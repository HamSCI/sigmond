"""The carry-over: ``[uploads]`` into the sink switch files, once, with the
marker last (spec §6; plan Rulings 8, 11, 12; V8; D12).

Before part 2 enforces anything, each station needs a switch file for the
site and for every governed client.  The carry-over writes them from what
the station already holds:

- the site switch from coordination.toml's ``[uploads]`` (§6.3):
  ``mode = "discard"`` gives ``off``; ``enabled = false`` with no mode (the
  legacy hold) gives ``fill``; ``enabled = true``, or no block, gives
  ``upload``;
- one file per governed client (one whose deploy.toml declares
  ``[[hs_uploader.pipeline]]`` blocks) that topology enables or that has
  rows in ``sink.db``: ``PSK_USE_HS_UPLOADER=0`` in any psk-recorder
  instance env gives psk-recorder ``fill``; the same for
  ``METEOR_SCATTER_USE_HS_UPLOADER=0``; a client topology does not enable
  gives ``fill``; anything else gives ``upload``.  ``WSPR_USE_HS_UPLOADER``
  is ignored: it marks a merge fleet's decode-only role, not an order.  A
  client with no outbound route gets no file; a PSWS client with no ids yet
  still gets ``upload``.

Each file's first history entry carries ``by = "carry-over"``, a fixed
reason, and for a client one identity table per instance (design §3.4),
passed to ``hs-uploader sink set --identity-json -`` on stdin (Ruling 11).
Every switch file and the marker are written by hs-uploader's own command
line (``sink set``, ``sink mark-carried``), never by this module.

Order (§6.4): the site, then each missing client file, then the lever step
for every client whose effective state reads below ``upload`` (computed
here from the states this run intends; the stricter of site and client),
then the stale-writer restarts when any state reads ``off``, then the
marker.  Any failure before the marker leaves none, and the result says
what was written; the next run writes only what is still missing.

Without the marker, ``site.toml`` is rewritten whenever it differs from
``[uploads]`` (Ruling 8: ``[uploads]`` held the truth while the marker
stood absent, as after a rollback), and every existing client file stays
exactly as it stands.  With the marker standing, the carry-over has run:
:func:`late_clients` writes a file only for a governed client that has none
(§6.5, Michael's answer 2), under the site switch as it stands.

``sink.db`` is opened read-only (``mode=ro``) and never created; producers
stored as ``''`` get D12's inference.  The carry-over writes nothing into
it.

The caller (the manifest step) holds the install lock; this module never
takes it and never calls back into the manifest step.  Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import psws, sink_levers
from . import uploader_manifest as um
from .coordination import load_coordination
from .discover import find_deploy_toml
from .harmonize import parse_env_text
from .hamsci_sink import _bundled
from .instance import InvalidReporterId, display_reporter_id, validate_reporter_id
from .safe_write import read_nofollow
from .sink_state import MARKER_PATH, SINKS_DIR, marker_stands
from .topology import load_topology

BY = "carry-over"
SITE = "site"
STATES = ("off", "fill", "upload")          # strictest first
SITE_FROM_MODE = {"discard": "off", "hold": "fill", "upload": "upload"}
# client -> the env flag whose "0" is an operator's order to stop (§6.3)
STOP_FLAGS = {
    "psk-recorder": "PSK_USE_HS_UPLOADER",
    "meteor-scatter": "METEOR_SCATTER_USE_HS_UPLOADER",
}
_OFF_WORDS = ("0", "false", "no", "off")
_CLIENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

REASON_UPLOADS = "carried over from [uploads] mode = {mode}"
REASON_FLAG = "carried over from {flag}=0"
REASON_DISABLED = "carried over: not enabled in topology.toml, with rows in sink.db"
REASON_LATE = ("carried over after the marker: a client new since the "
               "carry-over")


@dataclass(frozen=True)
class CarryRoots:
    """Where the carry-over reads and writes; a scratch tree can stand in.

    ``deploy_dir`` None means each client's deploy.toml is found as the
    manifest step finds it (topology's client_dir, then
    ``discover.find_deploy_toml``)."""
    etc: Path = Path("/etc")
    sinks: Path = SINKS_DIR
    marker: Path = MARKER_PATH
    sink_db: Path = Path(um.SINK_PATH)
    deploy_dir: Optional[Path] = None
    writer_py: Path = sink_levers.WRITER_PY
    python: Path = um.HS_UPLOADER_PYTHON

    @property
    def coordination(self) -> Path:
        return self.etc / "sigmond" / "coordination.toml"

    @property
    def topology(self) -> Path:
        return self.etc / "sigmond" / "topology.toml"


@dataclass(frozen=True)
class FileWrite:
    """One switch file the carry-over writes through ``sink set``."""
    name: str               # "site" or a client
    state: str
    reason: str
    before: str             # missing, a state, or error
    identity: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class CarryPlan:
    marker_stands: bool
    mode: str                         # [uploads] mode, normalised
    site: Optional[FileWrite]         # None: site.toml left as it stands
    site_state: str                   # the site state the plan works under
    clients: tuple = ()               # FileWrite, sorted by name
    kept: tuple = ()                  # (client, state) whose file stands
    below: tuple = ()                 # clients for the lever step
    to_off: bool = False              # a state reads off: stale writers
    notes: tuple = ()
    late: bool = False                # the marker-standing pass (§6.5)
    roots: CarryRoots = CarryRoots()
    topology: object = field(default=None, compare=False, repr=False)

    @property
    def nothing_to_do(self) -> bool:
        return self.marker_stands and not self.clients and not self.below

    def lines(self) -> list:
        """Each client's before and after (design §10.1)."""
        out = [f"carry-over: note: {n}" for n in self.notes]
        if self.late:
            out += [f"carry-over: {w.name} has no switch file; {w.before} -> "
                    f"{w.state} ({w.reason})" for w in self.clients]
        else:
            if self.site is not None:
                out.append(f"carry-over: site {self.site.before} -> "
                           f"{self.site.state} ({self.site.reason})")
            else:
                out.append(f"carry-over: site {self.site_state}, kept (matches "
                           f"[uploads] mode = {self.mode})")
            for w in self.clients:
                out.append(f"carry-over: {w.name} {w.before} -> {w.state} "
                           f"({w.reason})")
            for name, state in self.kept:
                out.append(f"carry-over: {name} {state}, kept (its switch file "
                           "stands; the carry-over appends nothing)")
        if self.below:
            out.append("carry-over: lever step for " + ", ".join(self.below)
                       + " (effective state below upload)")
        if self.to_off:
            out.append("carry-over: a state reads off; stale writers restart "
                       "after the lever step")
        return out


@dataclass
class CarryResult:
    written: list = field(default_factory=list)       # names, in order
    lever: Optional[sink_levers.LeverResult] = None
    verify: list = field(default_factory=list)
    stale: Optional[sink_levers.StaleResult] = None
    marker: bool = False
    failed: Optional[tuple] = None                     # (step, why)

    @property
    def ok(self) -> bool:
        return self.failed is None

    def lines(self) -> list:
        out = [f"carry-over: wrote {n}" for n in self.written]
        if self.lever is not None:
            out += self.lever.lines()
        out += [f"lever: still armed: {v}" for v in self.verify]
        if self.stale is not None:
            out += self.stale.lines()
        if self.marker:
            out.append("carry-over: marker written")
        if self.failed is not None:
            out.append(f"carry-over: FAILED at {self.failed[0]}: "
                       f"{self.failed[1]}; no marker written, nothing enforced "
                       "yet; the next manifest step tries again")
        return out


# ---- reading the station ---------------------------------------------------

def _stricter(a: str, b: str) -> str:
    return STATES[min(STATES.index(a), STATES.index(b))]


def switch_state(path: Path) -> str:
    """What one switch file says: its state, ``missing``, or ``error`` (a
    file that cannot be read or parsed, or names no known state)."""
    try:
        text = read_nofollow(path)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "error"
    if text is None:
        return "missing"
    try:
        state = tomllib.loads(text).get("state")
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return "error"
    return state if state in STATES else "error"


def _site_limit(state: str) -> str:
    """The limit a site file imposes (design §3.4): none when missing, off
    when broken."""
    return {"missing": "upload", "error": "off"}.get(state, state)


def _client_effective(state: str) -> str:
    return state if state in STATES else "off"


def sink_producers(path: Path) -> tuple:
    """(producers with rows in ``sink.db``, notes).

    ``SELECT DISTINCT producer`` on a read-only connection; a row stored
    with ``''`` (or a sink older than the column) gets D12's inference from
    its table and mode, the writer's own ``infer_producer``.  A missing
    file is never created.  A name that is no client name is left out and
    counted in a note (recorder accounts can write ``sink.db``)."""
    if not os.path.lexists(path):
        return set(), []
    uri = "file:" + urllib.parse.quote(str(path)) + "?mode=ro"
    found: set = set()
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        return set(), [f"cannot open {path} read-only ({exc}); its producers "
                       "were not read"]
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(pending_uploads)")}
        if not cols:
            return set(), []
        where = ""
        if "producer" in cols:
            found |= {p for (p,) in con.execute(
                "SELECT DISTINCT producer FROM pending_uploads "
                "WHERE producer != ''")}
            where = " WHERE producer = ''"
        for db, table, mode in con.execute(
                "SELECT DISTINCT target_db, target_table, CASE WHEN "
                "json_valid(payload_json) AND json_type(payload_json) = "
                "'object' THEN json_extract(payload_json, '$.mode') END "
                "FROM pending_uploads" + where):
            found.add(_bundled.infer_producer(
                db, table, {"mode": mode} if isinstance(mode, str) else {}))
    except sqlite3.Error as exc:
        return set(), [f"cannot read {path} ({exc}); its producers were not "
                       "read"]
    finally:
        con.close()
    found.discard("")
    bad = {p for p in found if not (isinstance(p, str) and _CLIENT_RE.match(p))}
    notes = ([f"{path}: {len(bad)} producer name(s) that are no client name "
              "ignored"] if bad else [])
    return found - bad, notes


def _deploy(client: str, roots: CarryRoots, topology) -> Optional[Path]:
    base = roots.deploy_dir or getattr(topology, "client_dir", None)
    if base is not None:
        p = Path(base) / client / "deploy.toml"
        if p.is_file():
            return p
    if roots.deploy_dir is None:
        return find_deploy_toml(client)
    return None


def _instances(client: str, roots: CarryRoots) -> list:
    """Reporter ids from the instance env-file names, sorted (storage
    form, e.g. ``AC0G=B4``)."""
    d = roots.etc / client / "env"
    try:
        names = sorted(p.stem for p in d.glob("*.env"))
    except OSError:
        return []
    out = []
    for n in names:
        try:
            validate_reporter_id(n)
        except InvalidReporterId:
            continue
        out.append(n)
    return out


def _stop_order(client: str, roots: CarryRoots, notes: list) -> bool:
    """True when any instance env of ``client`` sets its stop flag to 0."""
    flag = STOP_FLAGS.get(client)
    if flag is None:
        return False
    for rid in _instances(client, roots):
        f = roots.etc / client / "env" / f"{rid}.env"
        try:
            text = read_nofollow(f) or ""
        except OSError as exc:
            notes.append(f"cannot read {f} ({exc.strerror or exc}); its "
                         f"{flag} was not read")
            continue
        val = parse_env_text(text).get(flag)
        if val is not None and val.strip().lower() in _OFF_WORDS:
            return True
    return False


def _psws_ids(client: str, roots: CarryRoots) -> dict:
    spec = psws.RECORDERS.get(client)
    if spec is None:
        return {}
    cfg = roots.etc / spec["config"].relative_to("/etc")
    text = psws._read_text(cfg)
    if not text:
        return {}
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    out = {}
    for key, where in (("station", spec["station"]),
                       ("instrument", spec["instrument"])):
        v = psws._dig(data, where)
        if not psws.is_placeholder(v):
            out[key] = str(v)
    return out


def identity_tables(client: str, roots: CarryRoots, coord) -> dict:
    """One identity table per instance (design §3.4).  No per-instance
    lat/lon exists; the host's applies.  A client with no instance env
    files (mag-recorder, hamsci-physics) gets one table under its own
    name.  PSWS clients add ``psws`` when their ids resolve.  Empty values
    are left out, so the tables stay deterministic."""
    host = {}
    if coord.host.grid:
        host["grid"] = coord.host.grid
    if coord.host.lat:
        host["lat"] = coord.host.lat
    if coord.host.lon:
        host["lon"] = coord.host.lon
    ids = _psws_ids(client, roots)
    extra = {"psws": ids} if ids else {}
    rids = _instances(client, roots)
    if rids:
        return {rid: {"callsign": display_reporter_id(rid),
                      "reporter_id": display_reporter_id(rid),
                      **host, **extra} for rid in rids}
    table = {}
    if coord.host.call:
        table["callsign"] = coord.host.call
    if coord.station.reporter_id:
        table["reporter_id"] = display_reporter_id(coord.station.reporter_id)
    return {client: {**table, **host, **extra}}


@dataclass
class _Station:
    coord: object
    topology: object
    enabled: set
    governed: list
    notes: list


def _read_station(roots: CarryRoots) -> _Station:
    coord = load_coordination(roots.coordination)
    topology = load_topology(roots.topology)
    enabled = set(topology.enabled_components())
    producers, notes = sink_producers(roots.sink_db)
    governed = []
    for client in sorted(enabled | producers):
        if not _CLIENT_RE.match(client) or client == SITE:
            continue
        deploy = _deploy(client, roots, topology)
        if deploy is not None and um._pipeline_decls(deploy):
            governed.append(client)
    return _Station(coord, topology, enabled, governed, list(notes))


def _client_write(client: str, st: _Station, roots: CarryRoots, *,
                  default_reason: str) -> FileWrite:
    """§6.3's client table."""
    if _stop_order(client, roots, st.notes):
        state, reason = "fill", REASON_FLAG.format(flag=STOP_FLAGS[client])
    elif client not in st.enabled:
        state, reason = "fill", REASON_DISABLED
    else:
        state, reason = "upload", default_reason
    return FileWrite(client, state, reason, "missing",
                     identity_tables(client, roots, st.coord))


# ---- the plan ----------------------------------------------------------------

def plan(roots: CarryRoots = CarryRoots()) -> CarryPlan:
    """What the carry-over would write and run.  Reads only.

    With the marker standing it returns :func:`late_clients`: nothing,
    unless a governed client has no switch file."""
    if marker_stands(roots.marker):
        return late_clients(roots)
    st = _read_station(roots)
    mode = st.coord.uploads.mode
    want = SITE_FROM_MODE[mode]
    reason = REASON_UPLOADS.format(mode=mode)
    have = switch_state(roots.sinks / f"{SITE}.toml")
    site = None if have == want else FileWrite(SITE, want, reason, have)
    writes, kept, eff = [], [], {}
    for client in st.governed:
        cur = switch_state(roots.sinks / f"{client}.toml")
        if cur == "missing":
            w = _client_write(client, st, roots, default_reason=reason)
            writes.append(w)
            cur = w.state
        else:
            kept.append((client, cur))
        eff[client] = _stricter(want, _client_effective(cur))
    below = tuple(c for c in sorted(eff) if eff[c] != "upload")
    return CarryPlan(
        marker_stands=False, mode=mode, site=site, site_state=want,
        clients=tuple(writes), kept=tuple(kept), below=below,
        to_off=any(eff[c] == "off" for c in below), notes=tuple(st.notes),
        roots=roots, topology=st.topology)


def late_clients(roots: CarryRoots = CarryRoots()) -> CarryPlan:
    """The marker stands: a file, by §6.3's client table, for each governed
    client that has none (§6.5).  The site switch, as its file stands,
    still limits it; the lever step covers only these clients."""
    st = _read_station(roots)
    site = switch_state(roots.sinks / f"{SITE}.toml")
    limit = _site_limit(site)
    writes, eff = [], {}
    for client in st.governed:
        if switch_state(roots.sinks / f"{client}.toml") != "missing":
            continue
        w = _client_write(client, st, roots, default_reason=REASON_LATE)
        writes.append(w)
        eff[client] = _stricter(limit, w.state)
    below = tuple(c for c in sorted(eff) if eff[c] != "upload")
    return CarryPlan(
        marker_stands=True, mode=st.coord.uploads.mode, site=None,
        site_state=site, clients=tuple(writes), below=below,
        to_off=any(eff[c] == "off" for c in below), notes=tuple(st.notes),
        late=True, roots=roots, topology=st.topology)


# ---- applying it -------------------------------------------------------------

def _call(runner: Callable, args: list, stdin, res: CarryResult,
          step: str) -> bool:
    try:
        r = runner(args, stdin=stdin)
    except (OSError, subprocess.SubprocessError) as exc:
        res.failed = (step, f"{type(exc).__name__}: {exc}")
        return False
    if r.returncode != 0:
        err = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
        res.failed = (step, f"exit {r.returncode}: "
                            f"{err[-1].strip() if err else 'no message'}")
        return False
    return True


def _lever_step(plan: CarryPlan, res: CarryResult, systemctl) -> bool:
    roots = plan.roots
    lp = sink_levers.plan_levers(
        plan.below, plan.topology,
        roots=sink_levers.Roots(etc=roots.etc, writer_py=roots.writer_py),
        systemctl=systemctl)
    try:
        res.lever = sink_levers.apply_levers(lp, systemctl=systemctl)
    except sink_levers.RadiodMoved as exc:
        res.lever = exc.result
        res.failed = ("lever step", str(exc))
        return False
    if res.lever.failures:
        res.failed = ("lever step", "; ".join(f"{x}: {why}"
                                              for x, why in res.lever.failures))
        return False
    res.verify = sink_levers.verify_levers(lp, systemctl=systemctl)
    if res.verify:
        res.failed = ("lever step", "a sender still armed: "
                      + "; ".join(res.verify))
        return False
    return True


def _stale_step(plan: CarryPlan, res: CarryResult, systemctl) -> bool:
    skip = res.lever.restarted if res.lever is not None else ()
    try:
        res.stale = sink_levers.restart_stale_writers(
            plan.topology, to_off=True, writer_py=plan.roots.writer_py,
            systemctl=systemctl, skip=skip)
    except (sink_levers.RadiodMoved, sink_levers.RestartFailed,
            OSError, ValueError) as exc:
        res.failed = ("stale-writer restarts", str(exc))
        return False
    return True


def apply(plan: CarryPlan, *, runner: Optional[Callable] = None,
          systemctl=subprocess.run) -> CarryResult:
    """Carry the plan out, in §6.4's order; the marker last.

    ``runner(args, stdin=None)`` runs ``hs-uploader <args>`` and returns a
    CompletedProcess (default: :func:`uploader_manifest.run_hs_uploader`
    with the roots' interpreter).  Stops at the first failure: a failed
    ``sink set``, a lever that failed or left a sender armed, radiod moving,
    or a failed stale-writer restart.  Then no marker is written, and
    ``written`` names each file this run wrote."""
    roots = plan.roots
    if runner is None:
        def runner(args, stdin=None):
            return um.run_hs_uploader(args, stdin=stdin, python=roots.python)
    res = CarryResult()
    for w in ([plan.site] if plan.site is not None else []) + list(plan.clients):
        args = ["sink", "set", w.name, w.state, "--reason", w.reason,
                "--by", BY]
        stdin = None
        if w.name != SITE:
            args += ["--identity-json", "-"]
            stdin = json.dumps(w.identity, sort_keys=True)
        args += ["--sinks", str(roots.sinks)]
        if not _call(runner, args, stdin, res, f"hs-uploader sink set {w.name}"):
            return res
        res.written.append(w.name)
    if plan.below and not _lever_step(plan, res, systemctl):
        return res
    if plan.to_off and not _stale_step(plan, res, systemctl):
        return res
    if not plan.marker_stands:
        note = "carry-over wrote: " + (" ".join(res.written) or "nothing new")
        if not _call(runner, ["sink", "mark-carried", "--note", note,
                              "--marker", str(roots.marker)],
                     None, res, "hs-uploader sink mark-carried"):
            return res
        res.marker = True
    return res

"""Generate the single-host hs-uploader manifest from each client's deploy.toml.

The single-host uploader daemon (``hs-uploader.service``, user ``hsupload``,
one host key) drains every outbound path on a host by reading
``/etc/hs-uploader/pipelines.toml``.  Stage 6 stops hand-writing that file:
each client declares its outbound pipeline(s) in its own ``deploy.toml`` as
``[[hs_uploader.pipeline]]`` blocks — manifest-native pipeline shape (the same
``[[pipeline]]`` shape ``hs_uploader.pipeline_factory`` consumes) but with
``{placeholder}`` tokens for the per-site identity.  This module reads the
enabled clients' declarations, substitutes identity read from where it already
lives, and renders the host manifest.

Identity is read from the *current* authoritative configs — no new store:

* ``{call}`` / ``{call_pathsafe}`` — the wspr reporter id (e.g. ``AC0G/S`` ->
  ``AC0G_S``) from :func:`sigmond.instance.list_instances`, else
  coordination ``[host].call``.
* ``{grid}`` — coordination ``[host].grid``.
* ``{radiod_status}`` — first radiod status DNS in coordination.
* ``{sink_path}`` / ``{ssh_key_file}`` — fixed host paths.
* ``{station_id}`` / ``{instrument_id}`` — PSWS ids for the *declaring* client,
  via :data:`sigmond.psws.RECORDERS` (grape -> hf-timestd config, mag -> mag
  config).

A pipeline whose required identity is missing/placeholder is **skipped with a
warning** (mirrors ``upload_creds`` "upload info missing") rather than emitted
broken.  The rendered manifest reproduces the same derived watermark keys
(``source_id``/``dest_id``/``table``) as a correctly hand-written one, so the
daemon inherits its cursors with no backlog re-ship.

One pipeline is NOT client-declared: the fleet-heartbeat upload
(:func:`heartbeat_pipeline`) is a station-level product (sigmond#task-8/9/10),
so it is rendered straight from ``[heartbeat]`` in coordination.toml rather
than any client's ``deploy.toml``, and appended by :func:`generate` after
:func:`collect_pipelines`.
"""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path
from typing import Optional

from . import psws
from .coordination import Coordination, load_coordination
from .discover import find_deploy_toml
from .instance import display_reporter_id, list_instances
from .topology import Topology, load_topology

logger = logging.getLogger(__name__)

MANIFEST_PATH = Path("/etc/hs-uploader/pipelines.toml")
KEYS_DIR = Path("/etc/hs-uploader/keys")
SINK_PATH = "/var/lib/sigmond/sink.db"
# The interpreter hs-uploader.service runs (its unit's ExecStart venv).
HS_UPLOADER_PYTHON = Path("/opt/hs-uploader/venv/bin/python")
PUMP_INTERVAL_SEC = 30

# Reporter-keyed clients whose per-instance reporter id is the host's upstream
# identity (display form, e.g. AC0G/S).  Single-rx hosts have exactly one.
_REPORTER_CLIENT = "wspr-recorder"


# --------------------------------------------------------------------------
# identity resolution (read from current configs)
# --------------------------------------------------------------------------


def host_key_file() -> str:
    """The one host SSH key.  Prefer the ``_host``-suffixed name the
    single-host daemon uses; fall back to the legacy shared name."""
    preferred = KEYS_DIR / "id_ed25519_host"
    legacy = KEYS_DIR / "id_ed25519"
    if legacy.exists() and not preferred.exists():
        return str(legacy)
    return str(preferred)


def _first_radiod_status(coord: Coordination) -> Optional[str]:
    for r in coord.radiods.values():
        dns = r.effective_status_dns
        if dns:
            return dns
    return None


def reporter_call(coord: Coordination,
                  instances: Optional[list] = None) -> Optional[str]:
    """The host's upstream reporter id in display form (``AC0G/S``).

    The wspr-recorder instance's reporter id is canonical (one per
    radiod+antenna); a single-rx host has exactly one.  Falls back to the
    coordination ``[host].call`` when no wspr instance exists."""
    if instances is None:
        try:
            instances = list_instances()
        except Exception:  # pragma: no cover - defensive
            instances = []
    reporters = [display_reporter_id(i.reporter_id)
                 for i in instances if i.client == _REPORTER_CLIENT]
    if reporters:
        return sorted(reporters)[0]
    return coord.host.call or None


def _psws_station_for_host(coord: Coordination) -> Optional[str]:
    """The host PSWS station id for the base ``[identity]`` block.

    Coordination may carry it in ``[station].psws_id``; otherwise derive it
    from the first PSWS-capable client that has one configured (hf-timestd's
    GRAPE station on a typical host)."""
    sid = getattr(coord.station, "psws_id", "") or ""
    if sid:
        return sid
    for client in ("hamsci-physics", "hf-timestd", "mag-recorder"):
        try:
            st = psws.read_state(client)
        except Exception:
            continue
        if st.station:
            return st.station
    return None


def resolve_tokens(client: str, coord: Coordination,
                   call: Optional[str]) -> dict:
    """Substitution map for one client's pipeline declarations.

    Values are ``None`` when unresolved; a pipeline that *uses* an unresolved
    token is skipped by :func:`collect_pipelines`."""
    tokens: dict = {
        "call": call,
        "call_pathsafe": call.replace("/", "_") if call else None,
        "grid": coord.host.grid or None,
        "radiod_status": _first_radiod_status(coord),
        "sink_path": SINK_PATH,
        "ssh_key_file": host_key_file(),
    }
    if psws.is_psws_recorder(client):
        try:
            st = psws.read_state(client)
        except Exception:
            st = None
        tokens["station_id"] = (st.station or None) if st else None
        tokens["instrument_id"] = (st.instrument or None) if st else None
    return tokens


# --------------------------------------------------------------------------
# placeholder substitution
# --------------------------------------------------------------------------


# A placeholder is a lowercase identifier in braces: ``{station_id}``.  Any
# other brace content in a declaration (``{}``, ``{Not A Token}``) is literal
# data and must survive substitution untouched.
_PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def _subst(obj, tokens: dict, used: set, missing: set):
    if isinstance(obj, str):
        out = obj
        for tok, val in tokens.items():
            ph = "{" + tok + "}"
            if ph in out:
                used.add(tok)
                if val is None:
                    missing.add(tok)
                else:
                    out = out.replace(ph, val)
        # A token the map never OFFERED is just as unresolved as one it
        # offered as None -- and more dangerous, because nothing upstream
        # noticed it was needed.  (2026-08-24: the GRAPE pipeline moved to
        # hamsci-physics, which is not a psws.RECORDERS client, so
        # resolve_tokens() omitted station_id/instrument_id entirely; the
        # literal "{station_id}" reached the manifest and the daemon sftp'd
        # to a user by that name for two days.)  Treat both the same.
        for tok in _PLACEHOLDER_RE.findall(out):
            used.add(tok)
            missing.add(tok)
        return out
    if isinstance(obj, list):
        return [_subst(x, tokens, used, missing) for x in obj]
    if isinstance(obj, dict):
        return {k: _subst(v, tokens, used, missing) for k, v in obj.items()}
    return obj


def _pipeline_decls(deploy_path: Path) -> list:
    """Read the ``[[hs_uploader.pipeline]]`` array from a client deploy.toml."""
    try:
        with open(deploy_path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("uploader-manifest: cannot read %s: %s", deploy_path, exc)
        return []
    block = data.get("hs_uploader") or {}
    decls = block.get("pipeline") or []
    return decls if isinstance(decls, list) else []


# --------------------------------------------------------------------------
# the pskreporter producer split (tasks/spec-key-split-v373.md §5.1)
# --------------------------------------------------------------------------

# The store schema from which hs-uploader keys a sqlite source by producer.
SPLIT_STORE_VERSION = 2
# The pipeline psk-recorder and meteor-scatter both declare today.
SHARED_PSK_PIPELINE = "psk-pskreporter"
# Each producer's own pipeline and the modes it ships.  The clients' deploy.toml
# keep declaring the shared block (D17 answer 2); sigmond rewrites it.
PSK_PRODUCERS = {
    "psk-recorder": ("psk-recorder-pskreporter", ["ft8", "ft4"]),
    "meteor-scatter": ("meteor-scatter-pskreporter", ["msk144"]),
}


class ProbeFailed(Exception):
    """The capability probe of the installed hs-uploader failed or timed out,
    so sigmond cannot tell which manifest form the daemon reads."""


def _split_for_producer(rendered: dict, client: str) -> dict:
    """The shared ``psk-pskreporter`` block, rewritten as ``client``'s own
    pipeline: its name, ``source.producer``, and the block's ``extra_where``
    with the mode clause narrowed to the producer's modes.  Every other key
    and clause copies unchanged."""
    name, modes = PSK_PRODUCERS[client]
    out = dict(rendered, name=name)
    source = dict(out.get("source") or {})
    where = []
    replaced = False
    for clause in source.get("extra_where") or []:
        if (isinstance(clause, list) and len(clause) == 3
                and clause[0] == "mode" and str(clause[1]).upper() == "IN"):
            where.append(["mode", clause[1], list(modes)])
            replaced = True
        else:
            where.append(clause)
    if not replaced:
        where.append(["mode", "IN", list(modes)])
    source["extra_where"] = where
    source["producer"] = client
    out["source"] = source
    return out


def collect_pipelines(topology: Optional[Topology] = None,
                      coord: Optional[Coordination] = None,
                      *, store_version: int) -> list:
    """Every enabled client's outbound pipeline(s), placeholders substituted.

    Returns a list of pipeline dicts ready for :func:`render_manifest`.  A
    pipeline whose required identity is unresolved is skipped with a warning.
    Each dict carries ``client``: the client whose deploy.toml declared it.

    ``store_version`` is the store schema the installed hs-uploader reports
    (:func:`hs_uploader_store_version`).  From :data:`SPLIT_STORE_VERSION`
    on, a ``psk-pskreporter`` block becomes its declaring producer's own
    pipeline (spec §5.1).  Below it, the shared pipeline renders as before,
    with one warning that hs-uploader lags sigmond."""
    topology = topology or load_topology()
    coord = coord or load_coordination()
    call = reporter_call(coord)
    split = store_version >= SPLIT_STORE_VERSION
    warned_lag = False

    pipelines: list = []
    by_name: dict = {}
    for client in topology.enabled_components():
        deploy = find_deploy_toml(client)
        if not deploy:
            continue
        decls = _pipeline_decls(deploy)
        if not decls:
            continue
        tokens = resolve_tokens(client, coord, call)
        for decl in decls:
            used: set = set()
            missing: set = set()
            rendered = _subst(decl, tokens, used, missing)
            name = rendered.get("name") or f"{client}-pipeline"
            if missing:
                logger.warning(
                    "uploader-manifest: skipping pipeline %s (%s) — "
                    "unresolved identity: %s",
                    name, client, ", ".join(sorted(missing)))
                continue
            if name == SHARED_PSK_PIPELINE and client in PSK_PRODUCERS:
                if split:
                    rendered = _split_for_producer(rendered, client)
                    name = rendered["name"]
                elif not warned_lag:
                    logger.warning(
                        "uploader-manifest: hs-uploader lags sigmond (store "
                        "schema %d, the producer split needs %d) — rendering "
                        "the shared %s pipeline; update hs-uploader",
                        store_version, SPLIT_STORE_VERSION, SHARED_PSK_PIPELINE)
                    warned_lag = True
            rendered = dict(rendered, client=client)
            # Dedup by name: below the split, two clients declare the SAME
            # shared pipeline (psk-recorder + meteor-scatter both declare
            # psk-pskreporter because MSK144 rides the psk.spots stream).
            # Emit it once; warn if the resolved bodies actually differ
            # (keep the first).  ``client`` names the first declarer.
            if name in by_name:
                if ({k: v for k, v in rendered.items() if k != "client"}
                        != {k: v for k, v in by_name[name].items()
                            if k != "client"}):
                    logger.warning(
                        "uploader-manifest: pipeline %s declared twice with "
                        "differing bodies (%s); keeping the first", name, client)
                continue
            by_name[name] = rendered
            pipelines.append(rendered)
    return pipelines


def heartbeat_pipeline(coord: Coordination) -> Optional[dict]:
    """The station-level heartbeat pipeline (sigmond#task-8..10,
    PRODUCER-THREAT-MODEL.md), rendered from ``[heartbeat]`` in
    coordination.toml rather than a client's ``deploy.toml`` — the
    heartbeat is a station product, no client declares it.

    Returns ``None`` (skipped with the same warning discipline
    :func:`collect_pipelines` uses for unresolved identity) when
    ``[heartbeat]`` is absent, ``enabled`` is false, or ``station``/``host``
    is missing/empty.  Otherwise a pipeline dict shaped for
    :func:`render_manifest` / ``hs_uploader.pipeline_factory`` — the
    ``heartbeat_sftp`` transport keys (``host``/``port``/``sftp_user``/
    ``remote_path``) are a cross-repo contract with hs-uploader; do not
    rename."""
    hb = coord.heartbeat
    if not hb.enabled:
        return None
    missing = [k for k, v in (("station", hb.station), ("host", hb.host))
              if not v]
    if missing:
        logger.warning(
            "uploader-manifest: skipping pipeline %s (%s) — "
            "unresolved identity: %s",
            "heartbeat", "coordination.heartbeat", ", ".join(missing))
        return None
    return {
        "name": "heartbeat",
        # No client declares the heartbeat; sigmond renders it.
        "client": "sigmond",
        "source": {
            "type": "filetree",
            "root": "/var/lib/sigmond/heartbeat",
            "patterns": ["*.json"],
            "table": "station.heartbeat",
            "retention": "delete_on_ack",
        },
        "transport": {
            "type": "heartbeat_sftp",
            "host": hb.host,
            "port": hb.port,
            "sftp_user": hb.sftp_user,
            "remote_path": hb.remote_path,
        },
        # Matches the RetryPolicy() default hs_uploader.pipeline_factory
        # falls back to when a pipeline omits [retry] — and the explicit
        # values psk-recorder/meteor-scatter declare for their own
        # pipelines (wspr's 900.0 cap is a documented drain-mode special
        # case, not the norm).
        "retry": {"base": 2.0, "cap_sec": 300.0},
    }


def build_identity(coord: Coordination, call: Optional[str]) -> dict:
    ident: dict = {}
    if call:
        ident["call"] = call
    if coord.host.grid:
        ident["grid"] = coord.host.grid
    sid = _psws_station_for_host(coord)
    if sid:
        ident["station_id"] = sid
    ident["ssh_key_file"] = host_key_file()
    return ident


# --------------------------------------------------------------------------
# TOML serialization (stdlib has no writer; the manifest shape is bounded)
# --------------------------------------------------------------------------


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"cannot serialize {type(v).__name__} to TOML: {v!r}")


def _emit(header: Optional[str], d: dict, lines: list, *, array: bool = False) -> None:
    if header is not None:
        lines.append(f"[[{header}]]" if array else f"[{header}]")
    scalars = [(k, v) for k, v in d.items() if not isinstance(v, dict)]
    tables = [(k, v) for k, v in d.items() if isinstance(v, dict)]
    for k, v in scalars:
        lines.append(f"{k} = {_toml_value(v)}")
    for k, v in tables:
        lines.append("")
        sub = f"{header}.{k}" if header else k
        _emit(sub, v, lines)


def render_manifest(pipelines: list, identity: dict,
                    *, pump_interval_sec: int = PUMP_INTERVAL_SEC,
                    banner: Optional[list] = None) -> str:
    """Render the full ``pipelines.toml`` text from resolved pieces.
    ``banner`` (comment lines) is appended to the header — see
    :func:`policy_banner`."""
    lines: list = [
        "# HamSCI single-host uploader manifest — GENERATED by sigmond.",
        "# Source: each enabled client's deploy.toml [[hs_uploader.pipeline]]",
        "# blocks, with per-site identity substituted. Regenerate with:",
        "#   smd admin uploader manifest --write",
        "# Drained by `hs-uploader serve` (hs-uploader.service, user hsupload).",
        "# DO NOT hand-edit — changes are overwritten on the next apply.",
    ]
    lines.extend(banner or [])
    lines.append("")
    _emit("identity", identity, lines)
    lines.append("")
    _emit("daemon", {"pump_interval_sec": pump_interval_sec}, lines)
    for p in pipelines:
        lines.append("")
        _emit("pipeline", p, lines, array=True)
    return "\n".join(lines) + "\n"


def _run_daemon_python(argv: list, *, python: Path, stdin: Optional[str],
                       timeout: float):
    """Run the daemon's interpreter with `argv`, safe for root (ruling C23).

    ``-P`` keeps the working directory (or a script's directory) off
    ``sys.path``, and ``cwd="/"`` starts the child where no account can
    plant a package, so a ``hs_uploader/`` in the caller's directory never
    runs as root.  `stdin` goes to the child as text; without it the child
    reads an empty stdin, never the caller's.  Raises OSError or
    subprocess.SubprocessError (a timeout included); callers decide what
    that means."""
    import subprocess
    kw = (dict(input=stdin) if stdin is not None
          else dict(stdin=subprocess.DEVNULL))
    return subprocess.run([str(python), "-P", *argv], capture_output=True,
                          text=True, timeout=timeout, cwd="/", **kw)


def run_hs_uploader(args: list, *, stdin: Optional[str] = None,
                    timeout: float = 20,
                    python: Path = HS_UPLOADER_PYTHON):
    """Run ``hs-uploader <args>`` through the daemon's own interpreter.

    The one runner for every hs-uploader call sigmond makes (the probes,
    ``sink status``, ``sink set``, ``sink mark-carried``; spec §9.1): the
    interpreter the service runs, as ``python -P -m hs_uploader.cli`` from
    ``cwd="/"``, with a timeout and optional text on stdin (Ruling 11:
    data passes on stdin, never through a file root writes).  Returns the
    CompletedProcess, output captured as text.  Raises OSError or
    subprocess.SubprocessError, a timeout included."""
    return _run_daemon_python(["-m", "hs_uploader.cli", *args],
                              python=python, stdin=stdin, timeout=timeout)


def hs_uploader_store_version(python: Path = HS_UPLOADER_PYTHON) -> Optional[int]:
    """The store schema version the hs-uploader that the service runs
    supports, from ``hs-uploader capabilities`` (spec §5.1).

    Asks the service's own interpreter, through :func:`run_hs_uploader`.
    Returns the integer it prints; ``0`` when the subcommand does not
    exist (an older hs-uploader, whose argparse refuses it with exit 2);
    ``None`` when the probe failed, timed out or printed something else."""
    import subprocess
    try:
        r = run_hs_uploader(["capabilities"], python=python)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode == 2 and "invalid choice" in (r.stderr or ""):
        return 0
    if r.returncode != 0:
        return None
    try:
        return int((r.stdout or "").strip())
    except ValueError:
        return None


def hs_uploader_features(python: Path = HS_UPLOADER_PYTHON
                         ) -> Optional[frozenset]:
    """The feature words the hs-uploader that the service runs offers, from
    ``hs-uploader capabilities --features`` (spec §7.2).

    Asks the service's own interpreter, through :func:`run_hs_uploader`.
    Returns the set of non-empty lines, unknown words kept.  A v3.73-v3.76
    hs-uploader refuses the flag (or the subcommand) with exit 2, which
    reads as the empty set.  Any other failure, or a timeout, returns
    ``None``.  The manifest step asks once, first (spec §7.4)."""
    import subprocess
    try:
        r = run_hs_uploader(["capabilities", "--features"], python=python)
    except (OSError, subprocess.SubprocessError):
        return None
    err = r.stderr or ""
    if r.returncode == 2 and ("unrecognized arguments" in err
                              or "invalid choice" in err):
        return frozenset()
    if r.returncode != 0:
        return None
    return frozenset(w.strip() for w in (r.stdout or "").splitlines()
                     if w.strip())


def hs_uploader_supports_discard(python: Path = HS_UPLOADER_PYTHON) -> bool:
    """Whether the hs-uploader that the service runs understands a
    pipeline's ``discard = true``.

    It must, or discard turns dangerous: an older hs-uploader ignores the
    unknown key and SHIPS the pipeline.  So sigmond asks the service's own
    interpreter, not this checkout, and treats any doubt as "no".  The
    import runs with ``-P`` from ``/``, as every hs-uploader call does, so
    a planted ``hs_uploader/`` in the caller's directory cannot answer."""
    import subprocess
    try:
        r = _run_daemon_python(["-c", "import hs_uploader.transports.discard"],
                               python=python, stdin=None, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def effective_mode(coord: Coordination, *, supports_discard=None) -> str:
    """The mode the manifest actually renders: the policy's, except that
    ``discard`` falls back to ``hold`` when hs-uploader cannot honour it.
    Hold ships nothing too, so the fallback can only keep data, never send
    it."""
    mode = coord.uploads.mode
    if mode != "discard":
        return mode
    ok = (hs_uploader_supports_discard() if supports_discard is None
          else supports_discard)
    return "discard" if ok else "hold"


def policy_banner(coord: Coordination, mode: Optional[str] = None) -> list:
    """Header lines announcing the uploads policy (sigmond#53), or ``[]``
    when outbound uploads are enabled.  Rendered into the manifest so the
    file itself explains why it is heartbeat-only or discarding — the next
    person to open it must not mistake policy for a rendering bug."""
    up = coord.uploads
    mode = mode or up.mode
    if mode == "upload":
        return []
    why = f" ({up.reason})" if up.reason else ""
    if mode == "discard":
        return [
            "#",
            "# *** SITE SINK OFF: DATA PIPELINES ACK WITHOUT SENDING ***",
            f"# coordination.toml [uploads] mode = \"discard\" sets the site sink switch to off{why}",
            "# Nothing leaves this host except the heartbeat, and no backlog builds.",
            "# `smd sink upload` sends the GRAPE and magnetometer packages for today and",
            "# yesterday (UTC), even if built while off.  Older packages stay on the",
            "# station, and nothing else recorded while off ships.",
            "#   smd sink upload    (then the manifest regenerates)",
        ]
    lines = [
        "#",
        "# *** SITE SINK HOLD (LEGACY): DATA PIPELINES NOT RENDERED ***",
        f"# coordination.toml [uploads] enabled = false{why}",
        "# Only the station heartbeat leaves this host.  Clients keep FT8 spots",
        "# for one hour and WSPR spots for 24 hours.",
        "#   smd sink upload    (then the manifest regenerates; what is still stored ships)",
    ]
    if up.mode == "discard":
        lines[2:2] = ["# The site sink switch reads off ([uploads] mode = \"discard\"), but",
                      "# the hs-uploader this host runs cannot discard, so sigmond renders",
                      "# the legacy hold instead."]
    return lines


def suppressed_pipelines(topology: Optional[Topology] = None,
                         coord: Optional[Coordination] = None,
                         *, store_version: Optional[int] = None) -> list:
    """Names of the client pipelines the uploads policy is holding back or
    discarding — ``[]`` when uploads are enabled.  Lets the command layer
    print, by name, what the policy actually affects.

    ``store_version`` picks the split or shared names (see
    :func:`collect_pipelines`); ``None`` probes the installed hs-uploader.
    The list only informs, so a failed probe names the shared pipeline
    rather than raising."""
    topology = topology or load_topology()
    coord = coord or load_coordination()
    if coord.uploads.mode == "upload":
        return []
    if store_version is None:
        store_version = hs_uploader_store_version()
        if store_version is None:
            store_version = 0
    return [p["name"] for p in collect_pipelines(
        topology, coord, store_version=store_version)]


def drop_inprocess_pipelines(pipelines: list, senders: set,
                             *, store_version: int) -> list:
    """Leave out, per D13 (spec §5.3), each pskreporter daemon pipeline whose
    producer sends in-process.  Only a split manifest has a pipeline of the
    producer's own to remove.  In the shared form one pipeline serves both
    producers, so it stays (a double post beats a silent loss) and one
    warning says so."""
    drop = set()
    for producer, dest in sorted(senders):
        if dest != "pskreporter" or producer not in PSK_PRODUCERS:
            continue
        if store_version >= SPLIT_STORE_VERSION:
            drop.add(PSK_PRODUCERS[producer][0])
            logger.info("uploader-manifest: %s runs its own pskreporter "
                        "sender — leaving out pipeline %s",
                        producer, PSK_PRODUCERS[producer][0])
        else:
            logger.warning(
                "uploader-manifest: %s runs its own in-process pskreporter "
                "sender, but the shared %s pipeline serves both producers "
                "(store schema %d) — keeping it; spots may post twice until "
                "hs-uploader updates", producer, SHARED_PSK_PIPELINE,
                store_version)
    return [p for p in pipelines if p.get("name") not in drop]


def _inprocess_pairs(topology, pipelines: list) -> set:
    """The D13 pairs, asked only when a pskreporter pipeline is in play."""
    names = {SHARED_PSK_PIPELINE} | {v[0] for v in PSK_PRODUCERS.values()}
    if not any(p.get("name") in names for p in pipelines):
        return set()
    from .inprocess_senders import inprocess_senders
    return inprocess_senders(topology)


def generate(topology: Optional[Topology] = None,
             coord: Optional[Coordination] = None,
             *, supports_discard=None,
             store_version: Optional[int] = None) -> str:
    """Full pipeline: collect declarations + identity, render the manifest.

    ``store_version`` is the installed hs-uploader's store schema
    (:func:`collect_pipelines`).  ``None`` probes it once, and only when
    the mode renders client pipelines; a probe that fails or times out
    raises :class:`ProbeFailed`, so the caller keeps the manifest it has
    rather than render a form the daemon may not read.

    ``upload`` renders every client pipeline.  ``hold`` (``enabled =
    false``, sigmond#53) renders the heartbeat alone, with a loud banner.
    ``discard`` renders every client pipeline with ``discard = true``:
    hs-uploader acks each batch against the real destination's cursor and
    ships nothing.  The heartbeat never obeys the policy."""
    topology = topology or load_topology()
    coord = coord or load_coordination()
    call = reporter_call(coord)
    reason = f": {coord.uploads.reason}" if coord.uploads.reason else ""
    mode = effective_mode(coord, supports_discard=supports_discard)
    if mode in ("upload", "discard"):
        # Only a render of client pipelines needs the store version; a
        # hold renders the heartbeat alone, so a failed probe cannot
        # block it (ruling 9).
        if store_version is None:
            store_version = hs_uploader_store_version()
            if store_version is None:
                raise ProbeFailed(
                    f"cannot ask {HS_UPLOADER_PYTHON} for `hs-uploader "
                    "capabilities` (failed or timed out)")
        pipelines = collect_pipelines(topology, coord,
                                      store_version=store_version)
        pipelines = drop_inprocess_pipelines(
            pipelines, _inprocess_pairs(topology, pipelines),
            store_version=store_version)
    if mode == "upload":
        pass
    elif mode == "discard":
        pipelines = [dict(p, discard=True) for p in pipelines]
        logger.warning("uploader-manifest: SITE SINK OFF%s — data pipelines "
                       "ack without sending", reason)
    else:
        pipelines = []
        if coord.uploads.mode == "discard":
            logger.error(
                "uploader-manifest: the site sink switch reads off ([uploads] mode = "
                "discard), but %s cannot import hs_uploader.transports.discard — "
                "rendering the legacy hold (heartbeat only) instead; update "
                "hs-uploader", HS_UPLOADER_PYTHON)
        else:
            logger.warning(
                "uploader-manifest: SITE SINK HOLD (LEGACY) ([uploads] enabled = "
                "false%s) — rendering heartbeat only", reason)
    hb_pipeline = heartbeat_pipeline(coord)
    if hb_pipeline is not None:
        pipelines.append(hb_pipeline)
    identity = build_identity(coord, call)
    return render_manifest(pipelines, identity, banner=policy_banner(coord, mode))

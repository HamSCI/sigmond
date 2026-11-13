"""Move running psk-recorder and meteor-scatter instances onto the
wsprdaemon.org route.

The standard: every WSPR, FT4, FT8 and MSK144 spot from a sigmond station
goes to wsprdaemon.org alone.  The server forwards copies to wsprnet.org and
pskreporter.info under the spot's own reporter id.  psk-recorder takes
``PSK_DELIVERY_PIPELINES=server-merge`` and meteor-scatter takes
``METEOR_SCATTER_DELIVERY_MODE=deposit``; no instance posts from the station.

The carrier rule: psk and MSK rows reach wsprdaemon.org only inside the
wspr-wsprdaemon tar, which wspr-recorder builds.  On a station with no
enabled wspr-recorder, a server route would strand every row, so the
migration leaves each instance as it stands and reports an ERROR.  An
instance already on the server route gets an ERROR too: its spots stay on
the station.

hs-uploader keeps one forward flag per receiver, and one unflagged row
turns it off.  So when psk-recorder rides the server route and a
meteor-scatter instance does not, the migration names that instance in an
ERROR: FT8 and FT4 spots from its receiver stop reaching pskreporter.

A sink lever value stays.  Below ``upload`` the lever step writes
``server-raw`` / ``off`` and keeps no undo record, so nothing here can tell
a lever value from an operator's choice by the env file alone.  The sink
state decides: a client whose sink reads below ``upload`` keeps its value,
and a sink state that cannot be read changes nothing at all.

Restarts go only through ``sink_levers.restart_units``, which refuses
radiod and raises ``RadiodMoved`` if radiod's start time changes.

A restart that failed on an earlier run heals on the next one.  Given a
``unit_state`` reader, the migration also restarts an ACTIVE instance
already on the server route when it started before its env file last
changed: it still runs the old route.  An inactive or failed unit stays as
it stands: it reads the env file at its next start, an operator's stop
stands, and a failed unit raises its own alarm (ruling R10).

wspr-recorder posts nothing to wsprnet.org since its shim stopped building
a direct pipeline, but a process started before that shim landed still
runs the old code.  Given ``shim_py``, the migration restarts each ACTIVE
wspr-recorder unit that started before the shim file last changed.

A file whose mtime lies more than five seconds ahead of the host clock
restarts nothing: the migration names it and leaves the unit running.
"""

from __future__ import annotations

import math
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import inprocess_senders as ips
from . import sink_levers
from .catalog import SUITE_ROOT
from .upload import STANDARD_ROUTE, env_path_for, set_env_flag

ROUTED_CLIENTS = ("psk-recorder", "meteor-scatter")
CARRIER_CLIENT = "wspr-recorder"
# The shim the carrier's processes import.  wspr-recorder installs
# editable from its checkout, so the running code comes from this file.
WSPR_SHIM = (SUITE_ROOT / CARRIER_CLIENT / "wspr_recorder"
             / "hs_uploader_shim.py")
FUTURE_SLACK = 5.0                     # seconds of clock skew a file may show


def via_server(client: str, env: dict) -> bool:
    """True when ``env`` already sends ``client``'s spots via wsprdaemon.org."""
    if client == "psk-recorder":
        return ips._psk_delivery_pipelines(env) == ("server-merge",)
    if client == "meteor-scatter":
        return ips._ms_delivery_mode(env) == "deposit"
    raise KeyError(client)


def _routed_here(client: str, env: dict) -> bool:
    """``via_server`` for the migration, which also needs an explicit
    meteor-scatter mode.  meteor-scatter before 907c7b2 reads an unset or
    empty METEOR_SCATTER_DELIVERY_MODE as ``direct`` and flags its rows
    unforwarded, so the migration writes ``deposit`` for such an instance
    whatever version runs.  Status and the manifest keep F4's reading."""
    return not _unset_meteor(client, env) and via_server(client, env)


def _unset_meteor(client: str, env: dict) -> bool:
    return (client == "meteor-scatter"
            and not (env.get("METEOR_SCATTER_DELIVERY_MODE") or "").strip())


def _effective(client: str, env: dict) -> str:
    """``KEY=value`` as the client reads its route from ``env``."""
    key = STANDARD_ROUTE[client][0]
    if client == "psk-recorder":
        return f"{key}={','.join(ips._psk_delivery_pipelines(env))}"
    return f"{key}={ips._ms_delivery_mode(env)}"


def lowered_from_state(state) -> frozenset:
    """The clients whose sink reads below ``upload``: those ``below_upload``
    names, plus each routed client the state does not list while the site
    itself reads below ``upload``."""
    from . import sink_state
    out = set(sink_state.below_upload(state))
    if state.site.state != sink_state.UPLOAD:
        out.update(c for c in ROUTED_CLIENTS if c not in state.clients)
    return frozenset(out)


_ACTIVE = "active"


def unit_state(unit: str, *, systemctl=subprocess.run
               ) -> "tuple[str, Optional[float]]":
    """``(ActiveState, ActiveEnterTimestamp as unix seconds or None)`` for
    ``unit``, through ``sink_levers``' systemctl seam.  Raises OSError when
    systemctl fails."""
    p = sink_levers._unit_props(systemctl, unit,
                                ("ActiveState", "ActiveEnterTimestamp"))
    try:
        started = float(p.get("ActiveEnterTimestamp", "").lstrip("@"))
    except ValueError:
        started = None
    return p.get("ActiveState", ""), started


def _stale_check(unit: str, path: Path, unit_state, now: float,
                 what: str) -> "Optional[tuple[bool, str]]":
    """Whether ``unit`` still runs code or settings older than ``path``.

    ``(True, why)``: it runs, and it started before ``path`` last changed
    (whole seconds, as systemd prints them).  ``(False, note)``: ``path``
    shows an mtime more than ``FUTURE_SLACK`` seconds past ``now``, so no
    comparison means anything.  None when the unit does not read active,
    when it started at or after the change, or when either time cannot be
    read."""
    try:
        state, started = unit_state(unit)
    except OSError:
        return None
    if state != _ACTIVE:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if mtime > now + FUTURE_SLACK:
        return False, (f"{unit}: {path} mtime in the future (host clock?) "
                       "— not restarted")
    if started is not None and math.floor(started) < math.floor(mtime):
        return True, f"it started before {what} last changed"
    return None


@dataclass
class RoutingResult:
    applied: bool
    changed: list = field(default_factory=list)      # (unit, key, old, new)
    restarted: list = field(default_factory=list)
    stale: list = field(default_factory=list)        # (unit, why) restarted
    lowered: list = field(default_factory=list)      # (unit, "KEY=value")
    already: list = field(default_factory=list)
    no_carrier: list = field(default_factory=list)   # posts from the station
    stranded: list = field(default_factory=list)     # routed, no carrier
    unreadable: list = field(default_factory=list)   # (unit, why)
    failures: list = field(default_factory=list)     # (what, why)
    unforwarded: list = field(default_factory=list)  # (meteor unit, receiver)
    notes: list = field(default_factory=list)        # lines; ok unchanged
    stale_failed: list = field(default_factory=list) # stale restart failed
    undecided: str = ""

    @property
    def ok(self) -> bool:
        return not (self.failures or self.no_carrier or self.stranded
                    or self.unreadable or self.unforwarded or self.undecided)

    def lines(self) -> list:
        if self.undecided:
            return [f"routing: cannot read the sink switches "
                    f"({self.undecided}); no instance changed"]
        out = []
        for unit, key, old, new in self.changed:
            if self.applied:
                out.append(f"routing: {unit}: {key} {old} -> {new}")
            else:
                out.append(f"routing: would set {unit}: {key} {old} -> {new}")
        out += [f"routing: restarted {u}" for u in self.restarted]
        out += [f"routing: restarted (stale) {u}: {why}"
                for u, why in self.stale]
        out += [f"routing: left alone {u} ({kv}: its sink reads below upload; "
                "`smd sink upload` routes it)" for u, kv in self.lowered]
        out += [f"routing: ERROR {u} posts from this station: no "
                f"{CARRIER_CLIENT} is enabled to carry its spots to "
                "wsprdaemon.org; left as it stands "
                f"(smd enable {CARRIER_CLIENT})" for u in self.no_carrier]
        out += [f"routing: ERROR {u}: its spots stay on this station: no "
                f"{CARRIER_CLIENT} is enabled to carry them to "
                f"wsprdaemon.org (smd enable {CARRIER_CLIENT})"
                for u in self.stranded]
        out += [f"routing: cannot read {u}'s environment ({why}); "
                "left as it stands" for u, why in self.unreadable]
        out += [f"routing: FAILED {what}: {why}" for what, why in self.failures]
        out += [f"routing: ERROR meteor-scatter {u} still flags its rows "
                "unforwarded; hs-uploader marks the whole receiver "
                f"not-forwarded, so FT8/FT4 spots from {rx} stop reaching "
                f"pskreporter — fix {u} (smd admin uploader routing --apply)"
                for u, rx in self.unforwarded]
        out += [f"routing: {u}: restart failed; its running process may "
                "hold old settings" for u in self.stale_failed]
        out += [f"routing: {n}" for n in self.notes]
        if self.already:
            out.append(f"routing: {len(self.already)} instance(s) already "
                       "deliver via wsprdaemon.org")
        if not out:
            out.append("routing: no psk-recorder or meteor-scatter instance "
                       "on this host")
        return out


def _receiver(unit: str) -> str:
    """The reporter id a meteor-scatter unit runs under, for the F2 line;
    the whole host when no instance can be named."""
    inst = sink_levers._instance_of(unit)
    return inst if inst else "every receiver on this host"


def migrate_routing(topology, *, lowered: Optional[frozenset],
                    apply: bool = True, etc: Path = Path("/etc"),
                    instance_units=ips._instance_units,
                    show=ips._systemctl_show,
                    restart=sink_levers.restart_units,
                    undecided_why: str = "",
                    unit_state=None, shim_py: Optional[Path] = None,
                    now=time.time) -> RoutingResult:
    """Put each enabled psk-recorder / meteor-scatter instance on the
    wsprdaemon.org route, restarting only the instances it changed.

    ``lowered`` names the clients whose sink reads below ``upload``
    (``lowered_from_state``); None means the sink state could not be read,
    and then nothing changes.  ``apply=False`` reports what would change.
    ``unit_state`` (see :func:`unit_state`) turns on the stale heal; None
    leaves every instance already on the route alone.  With ``unit_state``
    and ``shim_py`` both given, the stale-shim restart of wspr-recorder
    runs too."""
    res = RoutingResult(applied=apply)
    if lowered is None:
        res.undecided = undecided_why or "no reason given"
        return res
    enabled = set(topology.enabled_components())
    carrier = CARRIER_CLIENT in enabled
    t_now = now()
    queued = []
    stale = []                                        # (unit, why)
    psk_routed = []                       # psk units on server-merge after
    ms_off = []                           # meteor units left off deposit
    for client in ROUTED_CLIENTS:
        if client not in enabled:
            continue
        key, value = STANDARD_ROUTE[client]
        try:
            units = instance_units(client, topology)
        except OSError as exc:
            res.unreadable.append(
                (client, f"cannot enumerate its instances: {exc}"))
            if client == "meteor-scatter":
                ms_off.append(client)
            continue
        for unit in units:
            try:
                env = show(unit)
            except OSError as exc:
                res.unreadable.append((unit, str(exc)))
                if client == "meteor-scatter":
                    ms_off.append(unit)
                continue
            if _routed_here(client, env):
                if not carrier:
                    res.stranded.append(unit)
                    continue
                res.already.append(unit)
                if client == "psk-recorder":
                    psk_routed.append(unit)
                if apply and unit_state is not None and client not in lowered:
                    try:
                        epath = env_path_for(
                            client, sink_levers._instance_of(unit),
                            base=str(etc))
                    except (OSError, ValueError):
                        epath = None
                    got = (None if epath is None else _stale_check(
                        unit, epath, unit_state, t_now, "its env file"))
                    if got is not None:
                        if got[0]:
                            stale.append((unit, got[1]))
                        else:
                            res.notes.append(got[1])
                continue
            if client in lowered:
                res.lowered.append((unit, _effective(client, env)))
                continue
            # Neither client reads its *_USE_HS_UPLOADER flag: at 0 an
            # instance still posts directly, so the flag never exempts one.
            if not carrier:
                # An unset meteor mode posts on an old meteor-scatter and
                # deposits on a new one; either way nothing carries it.
                (res.stranded if _unset_meteor(client, env)
                 else res.no_carrier).append(unit)
                continue
            old = env.get(key) or "unset"
            if not apply:
                res.changed.append((unit, key, old, value))
                if client == "psk-recorder":
                    psk_routed.append(unit)
                continue
            path = None
            try:
                path = env_path_for(client, sink_levers._instance_of(unit),
                                    base=str(etc))
                set_env_flag(path, key, value)
            except (OSError, ValueError) as exc:
                res.failures.append((str(path) if path else unit,
                                     f"not written: {exc}"))
                if client == "meteor-scatter":
                    ms_off.append(unit)
                continue
            try:
                after = show(unit)
            except OSError as exc:
                res.failures.append((str(path), f"written, but cannot re-read "
                                     f"{unit}'s environment ({exc}); "
                                     "not restarted"))
                if client == "meteor-scatter":
                    ms_off.append(unit)
                continue
            if not _routed_here(client, after):
                res.failures.append((str(path), f"written, but {unit}'s "
                                     f"environment still overrides {key}; "
                                     "not restarted"))
                if client == "meteor-scatter":
                    ms_off.append(unit)
                continue
            res.changed.append((unit, key, old, value))
            queued.append(unit)
    shim_stale = []                                   # (unit, why)
    if apply and carrier and unit_state is not None and shim_py is not None:
        shim_stale = _stale_shim_units(topology, instance_units, unit_state,
                                       shim_py, t_now, res)
    todo = queued + [u for u, _ in stale] + [u for u, _ in shim_stale]
    bad: set = set()
    if apply and todo:
        failed = []
        try:
            restart(todo)
        except sink_levers.RadiodMoved as exc:
            failed = list(exc.failures)
            res.failures.extend(exc.failures)
            res.failures.append(("restarts", str(exc)))
        except sink_levers.RestartFailed as exc:
            failed = list(exc.failures)
            res.failures.extend(exc.failures)
        bad = {u for u, _ in failed}
        res.restarted = [u for u in queued if u not in bad]
        res.stale = [(u, why) for u, why in stale + shim_stale
                     if u not in bad]
    psk_routed = [u for u in psk_routed if u not in bad]
    psk_routed += [u for u in res.restarted
                   if u.startswith("psk-recorder@")]
    stale_units = {u for u, _ in stale}
    ms_off += [u for u in bad if u.startswith("meteor-scatter@")
               and u not in stale_units]
    # A meteor unit already on deposit whose stale restart failed flags
    # nothing unforwarded on disk; only its running process may lag.
    res.stale_failed = [u for u in bad if u in stale_units]
    if psk_routed:
        for u in dict.fromkeys(ms_off):
            label = u if u != "meteor-scatter" else "(its instances)"
            res.unforwarded.append((label, _receiver(u)))
    return res


def _stale_shim_units(topology, instance_units, unit_state, shim_py: Path,
                      now: float, res: RoutingResult) -> list:
    """``(unit, why)`` for each ACTIVE wspr-recorder unit that started
    before ``shim_py`` last changed.  An unreadable shim restarts nothing
    and leaves one line in ``res.notes``; so does a shim dated in the
    future."""
    try:
        shim_py.stat()
    except OSError as exc:
        res.notes.append(f"cannot read {shim_py} ({exc.strerror or exc}); "
                         f"{CARRIER_CLIENT} needs `smd align --apply`; "
                         f"no {CARRIER_CLIENT} restarted")
        return []
    try:
        units = list(instance_units(CARRIER_CLIENT, topology))
    except OSError as exc:
        res.unreadable.append(
            (CARRIER_CLIENT, f"cannot enumerate its instances: {exc}"))
        return []
    units.append(sink_levers.WSPR_UPLOADER)
    out, future = [], False
    for unit in units:
        got = _stale_check(unit, shim_py, unit_state, now, shim_py.name)
        if got is None:
            continue
        if got[0]:
            out.append((unit, got[1]))
        elif not future:
            future = True
            res.notes.append(f"{shim_py} mtime in the future (host "
                             "clock?) — not restarted")
    return out

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
migration leaves each instance as it stands and reports an ERROR.

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
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import inprocess_senders as ips
from . import sink_levers
from .upload import (STANDARD_ROUTE, UPLOAD_ENABLE, env_path_for, is_truthy,
                     set_env_flag)

ROUTED_CLIENTS = ("psk-recorder", "meteor-scatter")
CARRIER_CLIENT = "wspr-recorder"


def via_server(client: str, env: dict) -> bool:
    """True when ``env`` already sends ``client``'s spots via wsprdaemon.org."""
    if client == "psk-recorder":
        return ips._psk_delivery_pipelines(env) == ("server-merge",)
    if client == "meteor-scatter":
        return ips._ms_delivery_mode(env) == "deposit"
    raise KeyError(client)


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


def _stale_why(unit: str, path: Path, unit_state) -> Optional[str]:
    """Why ``unit``, already on the server route, still runs the old one:
    it runs, and it started before ``path`` last changed (whole seconds,
    as systemd prints them).  A unit that does not read active never
    counts.  None when it runs
    the current file, or when either time cannot be read."""
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
    if started is not None and math.floor(started) < math.floor(mtime):
        return "it started before its env file last changed"
    return None


@dataclass
class RoutingResult:
    applied: bool
    changed: list = field(default_factory=list)      # (unit, key, old, new)
    restarted: list = field(default_factory=list)
    stale: list = field(default_factory=list)        # (unit, why) restarted
    lowered: list = field(default_factory=list)      # (unit, "KEY=value")
    upload_off: list = field(default_factory=list)
    already: list = field(default_factory=list)
    no_carrier: list = field(default_factory=list)
    unreadable: list = field(default_factory=list)   # (unit, why)
    failures: list = field(default_factory=list)     # (what, why)
    undecided: str = ""

    @property
    def ok(self) -> bool:
        return not (self.failures or self.no_carrier or self.unreadable
                    or self.undecided)

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
        out += [f"routing: left alone {u} (its upload is off)"
                for u in self.upload_off]
        out += [f"routing: ERROR {u} posts from this station: no "
                f"{CARRIER_CLIENT} is enabled to carry its spots to "
                "wsprdaemon.org; left as it stands "
                f"(smd enable {CARRIER_CLIENT})" for u in self.no_carrier]
        out += [f"routing: cannot read {u}'s environment ({why}); "
                "left as it stands" for u, why in self.unreadable]
        out += [f"routing: FAILED {what}: {why}" for what, why in self.failures]
        if self.already:
            out.append(f"routing: {len(self.already)} instance(s) already "
                       "deliver via wsprdaemon.org")
        if not out:
            out.append("routing: no psk-recorder or meteor-scatter instance "
                       "on this host")
        return out


def migrate_routing(topology, *, lowered: Optional[frozenset],
                    apply: bool = True, etc: Path = Path("/etc"),
                    instance_units=ips._instance_units,
                    show=ips._systemctl_show,
                    restart=sink_levers.restart_units,
                    undecided_why: str = "",
                    unit_state=None) -> RoutingResult:
    """Put each enabled psk-recorder / meteor-scatter instance on the
    wsprdaemon.org route, restarting only the instances it changed.

    ``lowered`` names the clients whose sink reads below ``upload``
    (``lowered_from_state``); None means the sink state could not be read,
    and then nothing changes.  ``apply=False`` reports what would change.
    ``unit_state`` (see :func:`unit_state`) turns on the stale heal; None
    leaves every instance already on the route alone."""
    res = RoutingResult(applied=apply)
    if lowered is None:
        res.undecided = undecided_why or "no reason given"
        return res
    enabled = set(topology.enabled_components())
    carrier = CARRIER_CLIENT in enabled
    queued = []
    stale = []                                        # (unit, why)
    for client in ROUTED_CLIENTS:
        if client not in enabled:
            continue
        key, value = STANDARD_ROUTE[client]
        flag = UPLOAD_ENABLE[client][0]
        try:
            units = instance_units(client, topology)
        except OSError as exc:
            res.unreadable.append(
                (client, f"cannot enumerate its instances: {exc}"))
            continue
        for unit in units:
            try:
                env = show(unit)
            except OSError as exc:
                res.unreadable.append((unit, str(exc)))
                continue
            if via_server(client, env):
                res.already.append(unit)
                if apply and unit_state is not None and client not in lowered:
                    try:
                        epath = env_path_for(
                            client, sink_levers._instance_of(unit),
                            base=str(etc))
                    except (OSError, ValueError):
                        epath = None
                    why = (None if epath is None
                           else _stale_why(unit, epath, unit_state))
                    if why is not None:
                        stale.append((unit, why))
                continue
            if client in lowered:
                res.lowered.append((unit, _effective(client, env)))
                continue
            # psk-recorder reads PSK_USE_HS_UPLOADER nowhere: at 0 it still
            # posts directly, so only meteor-scatter honours its flag here.
            if client != "psk-recorder" and not is_truthy(env.get(flag)):
                res.upload_off.append(unit)
                continue
            if not carrier:
                res.no_carrier.append(unit)
                continue
            old = env.get(key) or "unset"
            if not apply:
                res.changed.append((unit, key, old, value))
                continue
            path = None
            try:
                path = env_path_for(client, sink_levers._instance_of(unit),
                                    base=str(etc))
                set_env_flag(path, key, value)
            except (OSError, ValueError) as exc:
                res.failures.append((str(path) if path else unit,
                                     f"not written: {exc}"))
                continue
            try:
                after = show(unit)
            except OSError as exc:
                res.failures.append((str(path), f"written, but cannot re-read "
                                     f"{unit}'s environment ({exc}); "
                                     "not restarted"))
                continue
            if not via_server(client, after):
                res.failures.append((str(path), f"written, but {unit}'s "
                                     f"environment still overrides {key}; "
                                     "not restarted"))
                continue
            res.changed.append((unit, key, old, value))
            queued.append(unit)
    if apply and (queued or stale):
        failed = []
        try:
            restart(queued + [u for u, _ in stale])
        except sink_levers.RadiodMoved as exc:
            failed = list(exc.failures)
            res.failures.extend(exc.failures)
            res.failures.append(("restarts", str(exc)))
        except sink_levers.RestartFailed as exc:
            failed = list(exc.failures)
            res.failures.extend(exc.failures)
        bad = {u for u, _ in failed}
        res.restarted = [u for u in queued if u not in bad]
        res.stale = [(u, why) for u, why in stale if u not in bad]
    return res

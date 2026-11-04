"""The ladder: decide, from one measurement, what each store's pressure calls for.

Pure.  `decide` reads no clock, no files and no services; the caller hands in
the measured stores, the allotments, the holdings, the trim records and `now`,
and acts on the returned Decision.  Output order is deterministic (stores,
then clients, sorted by name) so tests and alarms stay stable.

Stages climb per store (spec "The ladder"):

- WATCH    warn, naming the biggest holders.  A store whose projected time
           to full (from its measured churn) falls under the horizon
           reaches WATCH early, whatever its percentage.
- TIGHTEN  lower targets toward floors; ask managed clients to trim; clean up.
- DEMAND   a managed client that has not answered in time gets a loud alarm.
- FINAL    clients apply their final-stage policy; sigmond stops the service
           of a client that failed to answer, unless the station is hands-off.
"""

import math
from dataclasses import dataclass
from enum import IntEnum
from typing import Mapping, Optional, Sequence

from .model import (Allotments, Demand, Holding, Levels, Registration, Stage, Store,
                    allot_days)

DEMAND_DEADLINE_S = 600
UNMANAGED_FINAL_GRACE_S = 900

_GiB = 1 << 30


class Severity(IntEnum):
    """Alarm severity; the values are syslog priorities."""
    WARNING = 4
    ERROR = 3
    CRITICAL = 2


@dataclass
class TrimRecord:
    """The state of one client's current trim request.

    Contract for whoever keeps these records (`decide` only reads them):

    - `requested_at` holds the FIRST request of the current unanswered streak.
      Repeating the request while the client stays unanswered keeps it, so
      the demand deadline counts from the streak's start.
    - An answer with `ok=True` ends the streak; a later request starts a new
      one with a new `requested_at`.  A refusal below the floor counts as
      an answer (`ok=True`), never as a failure.
    - `answered_at` set with `ok=False` means the client replied but the reply
      failed (unparseable, timed out, error); it counts as a failed answer.
    - `final_since` marks when an unmanaged client first sat over its target
      at FINAL; it starts the unmanaged grace period.
    - The keeper (storage/tick.py) clears a client's record entirely when
      every store it sits on drops below TIGHTEN or the client fits its
      target, so a failure in one episode never counts against the next.
    """
    requested_at: float
    answered_at: Optional[float] = None
    ok: bool = False
    final_since: Optional[float] = None


@dataclass(frozen=True)
class Action:
    kind: str  # "trim" | "final" | "stop" | "cleanup"
    store: str
    client: Optional[str] = None
    fit_bytes: Optional[int] = None
    policy: Optional[str] = None


@dataclass
class Decision:
    stages: dict  # store -> Stage
    actions: list  # [Action]
    alarms: dict  # key -> (Severity, message)
    targets: dict  # store -> {client: bytes}


def hours_to_full(store: Store, churn_bytes_per_h: Optional[float]) -> Optional[float]:
    """Hours until the store is full at its measured churn; None when it
    does not grow."""
    if churn_bytes_per_h is None or not churn_bytes_per_h > 0:
        return None
    return max(0, store.capacity - store.used) / churn_bytes_per_h


def store_stage(store: Store, levels: Levels, churn_bytes_per_h: Optional[float] = None,
                horizon_h: float = 24.0) -> Stage:
    """The stage a store's fill percentage puts it in, raised to WATCH when
    its projected time to full falls under `horizon_h`."""
    pct = store.used_pct
    if pct >= levels.final:
        return Stage.FINAL
    if pct >= levels.demand:
        return Stage.DEMAND
    if pct >= levels.tighten:
        return Stage.TIGHTEN
    if pct >= levels.watch:
        return Stage.WATCH
    h = hours_to_full(store, churn_bytes_per_h)
    if h is not None and h < horizon_h:
        return Stage.WATCH
    return Stage.OK


def tightened_targets(store: Store, levels: Levels, holdings: Mapping[str, int],
                      demands: Sequence[Demand],
                      allotments: Mapping[str, int]) -> dict:
    """Targets that bring the store back to the WATCH level, in equal days.

    The space at WATCH is the clients' holdings less the reduction R (used
    less the WATCH level's bytes, never negative).  `allot_days` divides
    that space, so every client drops to the same days of coverage and
    keeps its floor.  Each target is the smaller of that and the client's
    allotment, never below its floor.
    """
    floor: dict = {}
    for d in demands:
        floor[d.client] = floor.get(d.client, 0) + d.floor_bytes
    clients = sorted(set(holdings) | set(floor) | set(allotments))
    held = {c: holdings.get(c, 0) for c in clients}
    reduction = max(0, math.ceil(store.used - store.capacity * levels.watch / 100.0))
    if reduction == 0:
        return {c: max(floor.get(c, 0), allotments.get(c, held[c])) for c in clients}
    space = max(0, sum(held.values()) - reduction)
    known = {d.client for d in demands}
    at_watch = allot_days(list(demands) + [Demand(c, 0, 0.0) for c in clients
                                           if c not in known], space).per_client
    return {c: max(floor.get(c, 0), min(allotments.get(c, held[c]), at_watch.get(c, 0)))
            for c in clients}


_STORE_SEVERITY = {
    Stage.WATCH: Severity.WARNING,
    Stage.TIGHTEN: Severity.WARNING,
    Stage.DEMAND: Severity.ERROR,
    Stage.FINAL: Severity.CRITICAL,
}


def _fmt_bytes(n: int) -> str:
    return f"{n} B ({n / _GiB:.1f} GiB)"


def _failed_to_answer(rec: Optional[TrimRecord], now: float) -> bool:
    """A managed client's trim request stands unanswered, or answered badly,
    past the demand deadline."""
    return (rec is not None and not rec.ok
            and now - rec.requested_at > DEMAND_DEADLINE_S)


def decide(*, stores: Mapping[str, Store], allotments: Mapping[str, Allotments],
           holdings: Mapping[tuple, Holding], registrations: Sequence[Registration],
           levels: Levels, trims: Mapping[str, TrimRecord],
           directives: Mapping[str, str], hands_off: bool,
           host_fault: Optional[str], now: float,
           demands: Optional[Mapping[str, Sequence[Demand]]] = None,
           churn: Optional[Mapping[str, Optional[float]]] = None,
           horizon_h: float = 24.0) -> Decision:
    """`demands` gives each store's client rates for the days division under
    pressure (absent: every client at rate 0, so targets fall to floors);
    `churn` gives each store's growth in bytes per hour."""
    demands = demands or {}
    churn = churn or {}
    stages: dict = {}
    actions: list = []
    alarms: dict = {}
    targets: dict = {}
    client_watch: dict = {}  # client -> [message parts], one alarm per client
    stopped: set = set()

    for name in sorted(stores):
        store = stores[name]
        allot = allotments.get(name)
        growth = churn.get(name)
        stage = store_stage(store, levels, growth, horizon_h)
        early = stage == Stage.WATCH and store.used_pct < levels.watch
        if allot is not None and not allot.floors_fit:
            stage = Stage.FINAL
            alarms[f"floors:{name}"] = (
                Severity.CRITICAL,
                f"store {name}: the registered floors no longer fit "
                f"({_fmt_bytes(allot.allottable)} allottable); stage FINAL")
        stages[name] = stage

        regs = [r for r in registrations if r.store == name]
        floors: dict = {}
        managed: dict = {}
        for r in regs:
            floors[r.client] = floors.get(r.client, 0) + r.floor_bytes
            # The contract belongs to the client: any managed registration
            # means the client answers trim calls.
            managed[r.client] = managed.get(r.client, False) or r.managed
        held = {c: h.total for (c, s), h in holdings.items() if s == name}

        if stage >= Stage.WATCH:
            top = sorted(held.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            who = ", ".join(f"{c} {_fmt_bytes(b)}" for c, b in top) or "no registered holders"
            alarms[f"store:{name}"] = (
                _STORE_SEVERITY[stage],
                f"store {name} at {store.used_pct:.1f}% ({stage.name}); "
                f"largest holders: {who}"
                + (f"; full in {hours_to_full(store, growth):.1f} h at "
                   f"{growth / _GiB:.1f} GiB/h, under the {horizon_h:g} h horizon"
                   if early else ""))

        if allot is None:
            continue
        clients = sorted(set(floors) | set(allot.per_client))
        per_client = {c: allot.per_client.get(c, floors.get(c, 0)) for c in clients}
        hold = {c: held.get(c, 0) for c in clients}
        if stage >= Stage.TIGHTEN:
            dem = demands.get(name)
            if dem is None:
                dem = [Demand(c, floors.get(c, 0), 0.0) for c in clients]
            tgt = tightened_targets(store, levels, hold, dem, per_client)
        else:
            tgt = dict(per_client)
        targets[name] = tgt

        if store.kind == "pool":
            # The pool's clients are the guest's; the pool limit already
            # shrank their allotments.  Stages and alarms only.
            continue

        for c in clients:
            if hold[c] > per_client[c] * levels.client_watch / 100.0:
                client_watch.setdefault(c, []).append(
                    f"{c} holds {_fmt_bytes(hold[c])} of its "
                    f"{_fmt_bytes(per_client[c])} allotment on {name}")

        if stage < Stage.TIGHTEN:
            continue
        actions.append(Action("cleanup", name))
        for c in clients:
            is_managed = managed.get(c, False)
            over = hold[c] > tgt[c]
            rec = trims.get(c)
            if is_managed and over:
                actions.append(Action("trim", name, c, fit_bytes=tgt[c]))
            if stage >= Stage.DEMAND and is_managed and _failed_to_answer(rec, now):
                alarms[f"demand-overdue:{c}"] = (
                    Severity.ERROR,
                    f"{c} has not answered a trim request on {name} within "
                    f"{DEMAND_DEADLINE_S} s (asked {now - rec.requested_at:.0f} s ago)")
            if stage < Stage.FINAL:
                continue
            if is_managed and over:
                actions.append(Action("final", name, c, fit_bytes=tgt[c],
                                      policy=directives.get(c)))
            # A stop needs the client over its target now, whatever its
            # trim record says: a client within target is not the cause.
            if is_managed:
                fails = over and _failed_to_answer(rec, now)
                why = f"failed to answer a trim request within {DEMAND_DEADLINE_S} s"
            else:
                fails = (over and rec is not None and rec.final_since is not None
                         and now - rec.final_since > UNMANAGED_FINAL_GRACE_S)
                why = (f"unmanaged and over its target at FINAL for more than "
                       f"{UNMANAGED_FINAL_GRACE_S} s")
            if not fails or c in stopped:
                continue
            stopped.add(c)
            if hands_off:
                alarms[f"hands-off-held:{c}"] = (
                    Severity.CRITICAL,
                    f"{c} on {name} {why}; station is hands-off, so its "
                    f"service was NOT stopped")
            else:
                actions.append(Action("stop", name, c))

    for c in sorted(client_watch):
        alarms[f"client-watch:{c}"] = (Severity.WARNING, "; ".join(client_watch[c]))
    if host_fault:
        alarms[f"host-report:{host_fault}"] = (
            Severity.ERROR, f"host pool report: {host_fault}")
    return Decision(stages, actions, alarms, targets)

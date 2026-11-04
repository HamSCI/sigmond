"""Storage ledger model: stores, registrations, holdings, and allotment arithmetic.

Pure arithmetic on byte counts, flooring at 0.  Nothing here reads the
filesystem; callers hand in measured Store values and write rates.

Allotments divide a store in equal DAYS OF COVERAGE, not equal bytes
(Michael, 2026-11-04): each client gets its floor plus its write rate times
its operator weight times one number of days D, chosen so the allotments
fill the allottable space.  A client's retention cap stops it at its own
max_days, and the days it gives up go to the others.
"""

import math
from dataclasses import dataclass
from enum import IntEnum
from typing import Mapping, Optional, Sequence

CLASSES = ("pinned", "raw", "derived", "sent")


class Stage(IntEnum):
    OK = 0
    WATCH = 1
    TIGHTEN = 2
    DEMAND = 3
    FINAL = 4


@dataclass(frozen=True)
class Levels:
    """Fill percentages at which each stage begins."""
    watch: float = 80.0
    tighten: float = 85.0
    demand: float = 90.0
    final: float = 95.0
    client_watch: float = 90.0


@dataclass(frozen=True)
class Store:
    name: str
    mount: str
    kind: str  # "guest" | "archive" | "pool"
    capacity: int
    used: int

    @property
    def used_pct(self) -> float:
        return 100.0 * self.used / self.capacity if self.capacity > 0 else 100.0


@dataclass(frozen=True)
class Registration:
    client: str
    store: str
    paths: tuple
    floor_bytes: int
    policy: Optional[str]
    managed: bool
    mb_per_day: float = 0.0             # declared, summed over the client's sinks here
    max_days: Optional[float] = None    # smallest declared retention_days > 0, if any


@dataclass(frozen=True)
class Holding:
    client: str
    store: str
    bytes_by_class: Mapping[str, int]
    estimated: bool

    @property
    def total(self) -> int:
        return sum(self.bytes_by_class.values())


@dataclass(frozen=True)
class Allotments:
    per_client: Mapping[str, int]
    floors_fit: bool
    allottable: int
    days: Optional[float] = None        # D; None when no client has a write rate


@dataclass(frozen=True)
class Demand:
    """One client's claim on a store: its floor, its write rate in bytes per
    day, its operator weight, and its own retention cap in days."""
    client: str
    floor_bytes: int
    rate_bytes_per_day: float
    weight: float = 1.0
    max_days: Optional[float] = None


HEADROOM_FLOOR_BYTES = {"guest": 2 << 30, "archive": 2 << 30, "pool": 10 << 30}
STATE_RESERVE_BYTES = 1 << 30


def _below_final(store: Store, levels: Levels) -> int:
    """Bytes of capacity at or under the FINAL fill level."""
    return int(round(store.capacity * levels.final / 100.0))


def headroom_bytes(store: Store, levels: Levels) -> int:
    """Space kept above the FINAL level, never less than the kind's floor."""
    above_final = max(0, store.capacity - _below_final(store, levels))
    return max(above_final, HEADROOM_FLOOR_BYTES.get(store.kind, 0))


def allottable_bytes(store: Store, levels: Levels, registered_bytes: int,
                     state_reserve: int) -> int:
    """Space the registered clients may share.

    Capacity less the state reserve, the headroom, and whatever unregistered
    consumers already occupy (used minus the registered holdings).
    """
    unregistered = max(0, store.used - registered_bytes)
    return max(0, store.capacity - state_reserve
               - headroom_bytes(store, levels) - unregistered)


def pool_room_bytes(pool: Store, levels: Levels, share: float) -> int:
    """This VM's share of the pool space still below FINAL."""
    return max(0, int((_below_final(pool, levels) - pool.used) * share))


def apply_pool_limit(allottable: int, registered_bytes: int, pool_room: int) -> int:
    """A filling pool caps growth beyond current holdings, not the holdings."""
    return max(0, min(allottable, registered_bytes + pool_room))


def _merge(demands: Sequence[Demand]) -> dict:
    """One Demand per client: floors and rates add, the tightest cap wins."""
    out: dict = {}
    for d in demands:
        w = d.weight if d.weight > 0 else 1.0
        rate = max(0.0, float(d.rate_bytes_per_day))
        cap = d.max_days if d.max_days is not None and d.max_days > 0 else None
        prev = out.get(d.client)
        if prev is None:
            out[d.client] = Demand(d.client, int(d.floor_bytes), rate, w, cap)
            continue
        caps = [x for x in (prev.max_days, cap) if x is not None]
        out[d.client] = Demand(d.client, prev.floor_bytes + int(d.floor_bytes),
                               prev.rate_bytes_per_day + rate, prev.weight,
                               min(caps) if caps else None)
    return out


def _water_fill(slopes: Mapping[str, float], caps: Mapping[str, Optional[float]],
                room: float) -> float:
    """The D at which sum(min(slope*D, slope*cap)) reaches `room`; when every
    client caps out first, the largest cap.

    The open clients are a set, and their slope a fresh sum over it, so no
    float residue survives the last client capping."""
    open_clients = set(slopes)
    used = 0.0                          # bytes taken by clients already capped
    for cap, c in sorted((caps[c], c) for c in slopes if caps[c] is not None):
        d = (room - used) / sum(slopes[x] for x in open_clients)
        if d <= cap:
            return d
        used += slopes[c] * cap
        open_clients.discard(c)
        if not open_clients:
            return cap
    return (room - used) / sum(slopes[x] for x in open_clients)


def allot_days(demands: Sequence[Demand], allottable: int) -> Allotments:
    """Floors first; then one number of days D for every client with a rate.

    Each allotment is floor + rate * weight * min(D, max_days).  A client
    with no rate gets its floor only.  When every rated client reaches its
    cap, the space left stays unallotted.  Allotments are whole bytes; the
    rounding remainder goes to the first uncapped rated client by name.
    """
    merged = _merge(demands)
    clients = sorted(merged)
    floors = {c: merged[c].floor_bytes for c in clients}
    slopes = {c: merged[c].rate_bytes_per_day * merged[c].weight for c in clients
              if merged[c].rate_bytes_per_day > 0}
    room = allottable - sum(floors.values())
    if room < 0:
        return Allotments(dict(floors), False, allottable, 0.0 if slopes else None)
    if not slopes:
        return Allotments(dict(floors), True, allottable, None)
    caps = {c: merged[c].max_days for c in slopes}
    days = _water_fill(slopes, caps, float(room))
    extra = {c: 0 for c in clients}
    for c, s in slopes.items():
        d = days if caps[c] is None else min(days, caps[c])
        extra[c] = min(room, int(math.floor(s * d)))
    open_clients = [c for c in sorted(slopes) if caps[c] is None or days < caps[c]]
    if open_clients:
        extra[open_clients[0]] += room - sum(extra.values())
    return Allotments({c: floors[c] + extra[c] for c in clients}, True, allottable, days)

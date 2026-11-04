"""Storage ledger model: stores, registrations, holdings, and allotment arithmetic.

Pure integer arithmetic on byte counts, flooring at 0.  Nothing here reads
the filesystem; callers hand in measured Store values.
"""

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


def allot(registrations: Sequence[Registration], allottable: int,
          shares: Mapping[str, float]) -> Allotments:
    """Give each client its floors, then split the rest by weight."""
    floors: dict = {}
    for r in registrations:
        floors[r.client] = floors.get(r.client, 0) + r.floor_bytes
    clients = sorted(floors)
    remainder = allottable - sum(floors.values())
    if remainder < 0:
        return Allotments(dict(floors), False, allottable)
    weights = {}
    for c in clients:
        w = shares.get(c, 1.0)
        weights[c] = w if w > 0 else 1.0
    total_w = sum(weights.values())
    extra = {c: int(remainder * weights[c] / total_w) for c in clients}
    if clients:
        extra[clients[0]] += remainder - sum(extra.values())
    return Allotments({c: floors[c] + extra[c] for c in clients}, True, allottable)

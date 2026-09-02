# lib/sigmond/adoption.py
"""What could this station adopt, and does it look like a kit we know?

Install lays the groundwork and activates nothing; the operator adopts.  This
module is the decision half of that: pure functions over an inventory, so the
whole surface tests without a USB bus or a LAN, and a station's behaviour can
be predicted before anyone travels to it.

⛔ Nothing here starts anything.  Hardware appearing is not an instruction.
On 2026-09-01 a unit that nobody asked to run was started by `smd apply` and
took B4's timing chain down twice in one day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Tuple

from .sources import SourceKey

#: The hardware that defines a DASI2 kit.  Membership in the funded programme
#: is decided by the HOSTNAME (see `station_identity`), never by this — anyone
#: who assembles the kit gets the identical configuration offered.
DASI2_KIT: FrozenSet[str] = frozenset({"rx888", "gpsdo", "magnetometer"})


@dataclass(frozen=True)
class StationInventory:
    """What is visible: hardware on the local bus, and selectable sources.

    `hardware` holds coarse kind names ("rx888", "gpsdo", "magnetometer") as
    reported by `sigmond.hardware`.  `sources` holds the stable keys
    `sigmond.sources.inventory()` produced, which include LAN radiods.
    """
    hardware: FrozenSet[str] = frozenset()
    sources: Tuple[SourceKey, ...] = ()


@dataclass(frozen=True)
class Offer:
    """Something adoptable that has not been adopted."""
    name: str            # "dasi2", or str(SourceKey)
    kind: str            # "kit" | "source"
    sources: Tuple[SourceKey, ...] = ()


def recognise(inv: StationInventory) -> Optional[str]:
    """Name the kit this station's hardware matches, if any.

    A superset still matches: a station may exceed the kit and remain a
    recognisable one.
    """
    if DASI2_KIT <= inv.hardware:
        return "dasi2"
    return None


def offers(inv: StationInventory,
           adopted: FrozenSet[SourceKey]) -> List[Offer]:
    """What could be adopted here that has not been.

    A recognised kit is offered as ONE thing, so provisioning a fleet is one
    decision rather than three.  Otherwise each unadopted source stands alone,
    which is the Fargo case and the no-local-hardware case both.
    """
    remaining = tuple(s for s in inv.sources if s not in adopted)
    if not remaining:
        return []
    kit = recognise(inv)
    if kit is not None:
        return [Offer(name=kit, kind="kit", sources=remaining)]
    return [Offer(name=str(s), kind="source", sources=(s,)) for s in remaining]

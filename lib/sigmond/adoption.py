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
from .station_identity import StationIdentity

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


#: What each piece of hardware brings with it.  radiod needs ka9q-web and
#: igmp-querier because multicast will not work without the querier.
_HARDWARE_COMPONENTS = {
    "rx888": ("ka9q-radio", "ka9q-web", "igmp-querier"),
    "gpsdo": ("gpsdo-monitor",),
    "magnetometer": ("mag-recorder",),
}


#: Of the components a kind brings, the ones that actually CONSUME the source.
#: `_HARDWARE_COMPONENTS` above is the ENABLE list — everything adopting a kind
#: brings up.  This is the narrower question the selection layer asks: which of
#: them reads the device?  ka9q-web serves radiod's status page over HTTP and
#: igmp-querier keeps multicast alive; neither reads a source, and writing
#: `igmp-querier.sources.toml` would store a fact nobody ever reads.
#:
#: The two lists are deliberately kept side by side: adding a component to
#: `_HARDWARE_COMPONENTS` should force a decision about this one.
SOURCE_CONSUMERS: FrozenSet[str] = frozenset({
    "ka9q-radio",     # the SDR is radiod's frontend
    "gpsdo-monitor",  # reads the GPSDO's HID / CDC stream
    "mag-recorder",   # reads the RM3100 behind its USB-I2C adapter
})


@dataclass(frozen=True)
class AdoptionPlan:
    """What adopting an offer would do, and what it still needs to be told."""
    components: Tuple[str, ...] = ()
    prefills: dict = field(default_factory=dict)
    ask: Tuple[str, ...] = ()


def _hardware_for(offer: Offer, inv: StationInventory) -> FrozenSet[str]:
    if offer.kind == "kit":
        return inv.hardware
    # A single source: a local USB device implies its hardware kind; a remote
    # radiod implies none, because the SDR is not ours.
    kinds = set()
    for key in offer.sources:
        if key.type != "usb":
            continue
        kinds |= {k for k in inv.hardware if k in _HARDWARE_COMPONENTS}
    return frozenset(kinds)


def plan(offer: Offer, inv: StationInventory,
         identity: "StationIdentity") -> AdoptionPlan:
    """What adopting this offer entails.

    Prefills split the way identity does: anything derivable from the kit or
    the host is filled; grant identity comes from the roster on a site and is
    ASKED on a DASI2-alike, which is a first-class station with no roster
    entry rather than a degraded one.
    """
    hardware = _hardware_for(offer, inv)
    components: list = []
    for kind in sorted(hardware):
        for comp in _HARDWARE_COMPONENTS.get(kind, ()):
            if comp not in components:
                components.append(comp)

    prefills = {
        # Auto-composed from the hostname — zero operator input, the rule the
        # install redesign already locked for radiod.
        "radiod_status_name": f"{identity.hostname}-status.local",
    }
    ask: list = []
    if identity.dasi2_site:
        prefills["psws_station"] = identity.psws_station
        prefills["psws_instrument"] = identity.psws_instrument
    else:
        ask.extend(("psws_station", "psws_instrument"))

    return AdoptionPlan(components=tuple(components),
                        prefills=prefills, ask=tuple(ask))

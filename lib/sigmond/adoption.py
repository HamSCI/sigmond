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

from .sources import KNOWN_CLIENTS, SourceKey
from .station_identity import StationIdentity

#: The hardware that defines a DASI2 kit.  Membership in the funded programme
#: is decided by the HOSTNAME (see `station_identity`), never by this — anyone
#: who assembles the kit gets the identical configuration offered.
#:
#: This is the RECOGNITION set, not the start list: `recognise()` matches a
#: superset, and `_hardware_for`'s kit branch then returns everything on the
#: station.  See the comment there before adding a hardware kind.
DASI2_KIT: FrozenSet[str] = frozenset({"rx888", "gpsdo", "magnetometer"})


@dataclass(frozen=True)
class StationInventory:
    """What is visible: hardware on the local bus, and selectable sources.

    `hardware` holds coarse kind names ("rx888", "gpsdo", "magnetometer") as
    reported by `sigmond.hardware`.  `sources` holds the stable keys
    `sigmond.sources.inventory()` produced, which include LAN radiods.

    `source_kinds` says which hardware kind each LOCAL source stands for —
    `usb:1dd2:2211:mini01 -> "gpsdo"`.  The caller building the inventory
    knows this at the moment it builds each key and used to throw it away;
    without it `plan()` could only guess, and guessing meant "every kind on
    the station", which planned radiod for a station where the operator had
    named only the GPSDO.  A tuple of pairs rather than a dict so the whole
    dataclass stays immutable and hashable.
    """
    hardware: FrozenSet[str] = frozenset()
    sources: Tuple[SourceKey, ...] = ()
    source_kinds: Tuple[Tuple[SourceKey, str], ...] = ()

    def kind_of(self, key: SourceKey) -> Optional[str]:
        """The hardware kind this source stands for, or None when unknown.

        None is a real answer, not a gap to fill in: a LAN radiod stands for
        no local hardware, and an unrecognised device must plan nothing.
        """
        for candidate, kind in self.source_kinds:
            if candidate == key:
                return kind
        return None


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
    if kit is None:
        return [Offer(name=str(s), kind="source", sources=(s,))
                for s in remaining]

    # A kit claims the LOCAL devices it is made of, and nothing else.  It used
    # to claim every unadopted source, so a neighbour's radiod on the LAN got
    # swept into "dasi2" — recorded as adopted for a station that had never
    # configured it, and gone from `smd status` for good.  Anything with no
    # local hardware kind stands on its own, the way it would on a station
    # that matched no kit.
    local = tuple(s for s in remaining if inv.kind_of(s) is not None)
    rest = tuple(s for s in remaining if inv.kind_of(s) is None)
    out: List[Offer] = []
    if local:
        out.append(Offer(name=kit, kind="kit", sources=local))
    out.extend(Offer(name=str(s), kind="source", sources=(s,)) for s in rest)
    return out


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


def consumers_for(key: SourceKey, inv: StationInventory) -> Tuple[str, ...]:
    """The components that would actually READ this source.

    Narrower than the enable list, and per-key rather than per-offer: adopting
    a kit used to write every one of its sources into every consumer's
    selection, so mag-recorder was told the RX888 was one of its sources.

    A radiod on another machine is consumed by the decode clients —
    `sources.KNOWN_CLIENTS`, reused rather than restated so the two cannot
    drift.  A local device is consumed by the one component that reads it.
    Anything unrecognised is consumed by nothing, and gets no selection file.
    """
    if key.type == "radiod":
        return tuple(KNOWN_CLIENTS)
    kind = inv.kind_of(key)
    return tuple(c for c in _HARDWARE_COMPONENTS.get(kind, ())
                 if c in SOURCE_CONSUMERS)


@dataclass(frozen=True)
class AdoptionPlan:
    """What adopting an offer would do, and what it still needs to be told."""
    components: Tuple[str, ...] = ()
    prefills: dict = field(default_factory=dict)
    ask: Tuple[str, ...] = ()


def _hardware_for(offer: Offer, inv: StationInventory) -> FrozenSet[str]:
    """The hardware kinds adopting this offer speaks for.

    A kit legitimately means every kind on the station — that is what makes it
    one decision instead of three.  A single source means ITS OWN kind and
    nothing else.

    ⛔ It used to mean every kind on the station in BOTH cases: the loop below
    ignored `key` and unioned `inv.hardware` for any `usb:` source.  While
    `smd adopt` only printed, that was cosmetic.  Once it started services it
    became the 2026-09-01 incident in miniature — adopting a Fargo miniGPS
    would have started radiod, which nobody asked for.  An unknown source now
    yields NO kinds: under-planning says so out loud, over-planning starts
    services on its own.
    """
    if offer.kind == "kit":
        # ⛔ `inv.hardware`, UNFILTERED -- and `recognise()` matches a
        # SUPERSET, so this is every kind on a station that merely contains
        # the kit.  Add a fourth hardware kind to `_HARDWARE_COMPONENTS` and
        # it joins the kit's start list here, without anyone touching
        # `DASI2_KIT` or reading this file.  That was harmless while `smd
        # adopt` only printed; adopt now enables and starts what this
        # returns.  If a kind should NOT come up with the kit, filter it
        # here -- `DASI2_KIT` alone will not stop it.
        return inv.hardware
    kinds = set()
    for key in offer.sources:
        if key.type != "usb":
            # A remote radiod implies no local hardware — the SDR is not ours.
            continue
        kind = inv.kind_of(key)
        if kind is not None:
            kinds.add(kind)
    return frozenset(kinds)


def components_for(offer: Offer, inv: StationInventory) -> Tuple[str, ...]:
    """The components adopting this offer would bring up.

    Identity-free on purpose, so `smd status` can ask "would adopt accept
    this?" without deciding who the station is.  An EMPTY answer means no
    component here claims that source, which is exactly when `smd adopt`
    refuses — the two surfaces read this one function so they cannot drift
    into advertising an offer the verb would reject.
    """
    hardware = _hardware_for(offer, inv)
    components: List[str] = []
    for kind in sorted(hardware):
        for comp in _HARDWARE_COMPONENTS.get(kind, ()):
            if comp not in components:
                components.append(comp)

    # The no-local-hardware station (design §2): "a site runs no SDR; radiod
    # lives on another machine on the LAN. sigmond should find it and offer
    # the clients that can consume it."  Without this a discovered radiod was
    # offered by `smd status` and then refused by `smd adopt`, because no
    # hardware kind mapped to it.
    if any(k.type == "radiod" for k in offer.sources):
        for client in KNOWN_CLIENTS:
            if client not in components:
                components.append(client)
    return tuple(components)


def plan(offer: Offer, inv: StationInventory,
         identity: "StationIdentity") -> AdoptionPlan:
    """What adopting this offer entails.

    Prefills split the way identity does: anything derivable from the kit or
    the host is filled; grant identity comes from the roster on a site and is
    ASKED on a DASI2-alike, which is a first-class station with no roster
    entry rather than a degraded one.
    """
    components = components_for(offer, inv)

    prefills = {
        # Auto-composed from the hostname — zero operator input, the rule the
        # install redesign already locked for radiod.
        "radiod_status_name": f"{identity.hostname}-status.local",
    }
    ask: list = []
    if identity.dasi2_site:
        prefills["psws_station"] = identity.psws_station
        # A site reports a GRAPE/HF instrument AND a magnetometer instrument
        # under one station id, so this is the whole per-recorder map, not a
        # single value.
        #
        # The WHOLE map, deliberately, not the subset this offer brings.  An
        # earlier draft filtered by `components` and was wrong twice over: the
        # GRAPE instrument belongs to `hf-timestd`, which is a profile client
        # and not a component of any hardware kind, so filtering silently
        # dropped it; and these ids are facts about the STATION, true whether
        # or not this particular adoption touches the recorder that reports
        # them.  Adoption does not decide identity.
        if identity.psws_instruments:
            prefills["psws_instruments"] = dict(identity.psws_instruments)
    else:
        # An ordinary station is asked.  Which recorders it must supply ids for
        # follows from what it runs, which the config flow knows and this pure
        # function deliberately does not.
        ask.extend(("psws_station", "psws_instruments"))

    return AdoptionPlan(components=components,
                        prefills=prefills, ask=tuple(ask))

# tests/test_adoption.py
"""What can this station adopt, and does it look like a known kit?

Pure over an inventory, so the whole decision surface tests with no USB bus
and no LAN — which is what lets Fargo be dry-run before anyone travels.
"""

import pytest

from sigmond.adoption import DASI2_KIT, Offer, StationInventory, offers, recognise
from sigmond.sources import SourceKey


def _inv(hardware=(), sources=(), kinds=()):
    """`kinds` is the (key, hardware-kind) mapping a real station carries for
    its LOCAL sources.  Omitting it means "this source stands for no local
    hardware" -- which is what a LAN radiod is, and what plans nothing."""
    return StationInventory(hardware=frozenset(hardware),
                            sources=tuple(sources),
                            source_kinds=tuple(kinds))


DASI2 = ("rx888", "gpsdo", "magnetometer")
RX = SourceKey(type="usb", identifier="04b4:00f1:0009061C028B1629")
GPS = SourceKey(type="usb", identifier="1dd2:2211:mini01")
REMOTE = SourceKey(type="radiod", identifier="bee3-status.local")


class TestRecognise:

    def test_the_full_kit_is_recognised(self):
        assert recognise(_inv(DASI2)) == "dasi2"

    def test_fargo_is_not_a_kit(self):
        """rx888 + miniGPS, no magnetometer and no TS-1 for weeks."""
        assert recognise(_inv(("rx888", "gpsdo"))) is None

    def test_extra_hardware_still_recognises(self):
        """Someone may exceed the set; that is still a DASI2-alike kit."""
        assert recognise(_inv(DASI2 + ("kiwisdr",))) == "dasi2"

    def test_nothing_attached_is_not_a_kit(self):
        assert recognise(_inv()) is None

    def test_the_kit_is_stated_once(self):
        assert DASI2_KIT == frozenset({"rx888", "gpsdo", "magnetometer"})


class TestOffers:

    def test_a_matching_kit_is_offered_as_one_thing(self):
        got = offers(_inv(DASI2, [RX, GPS]), adopted=frozenset())
        assert [o.name for o in got] == ["dasi2"]
        assert got[0].kind == "kit"

    def test_without_a_kit_each_source_is_offered_separately(self):
        got = offers(_inv(("rx888", "gpsdo"), [RX, GPS]), adopted=frozenset())
        assert {o.name for o in got} == {str(RX), str(GPS)}
        assert all(o.kind == "source" for o in got)

    def test_an_adopted_source_stops_being_offered(self):
        got = offers(_inv(("rx888", "gpsdo"), [RX, GPS]), adopted=frozenset({RX}))
        assert [o.name for o in got] == [str(GPS)]

    def test_a_remote_radiod_is_offered_with_no_local_hardware(self):
        """The no-hardware station: radiod lives on another machine."""
        got = offers(_inv((), [REMOTE]), adopted=frozenset())
        assert [o.name for o in got] == [str(REMOTE)]
        assert got[0].kind == "source"

    def test_nothing_visible_offers_nothing(self):
        assert offers(_inv(), adopted=frozenset()) == []

    def test_a_fully_adopted_kit_offers_nothing(self):
        got = offers(_inv(DASI2, [RX, GPS]), adopted=frozenset({RX, GPS}))
        assert got == []


from sigmond.adoption import AdoptionPlan, plan
from sigmond.station_identity import StationIdentity

SITE = StationIdentity(hostname="DASI007", dasi2_site=True,
                       psws_station="S000207", psws_instrument="I000207")
ALIKE = StationIdentity(hostname="fargo-1", dasi2_site=False)


class TestPlan:

    def test_a_rostered_kit_asks_for_nothing(self):
        """Fleet provisioning is meant to be zero-input."""
        inv = _inv(DASI2, [RX, GPS])
        p = plan(offers(inv, frozenset())[0], inv, SITE)
        assert p.ask == ()
        assert p.prefills["psws_station"] == "S000207"
        assert p.prefills["psws_instrument"] == "I000207"

    def test_an_alike_kit_gets_the_same_components_without_identity(self):
        """Same hardware, same configuration — simply not a funded site."""
        inv = _inv(DASI2, [RX, GPS], [(RX, "rx888"), (GPS, "gpsdo")])
        site_plan = plan(offers(inv, frozenset())[0], inv, SITE)
        alike_plan = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert alike_plan.components == site_plan.components
        assert "psws_station" not in alike_plan.prefills
        assert "psws_station" in alike_plan.ask

    def test_the_radiod_status_name_comes_from_the_hostname(self):
        inv = _inv(("rx888",), [RX], [(RX, "rx888")])
        p = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert p.prefills["radiod_status_name"] == "fargo-1-status.local"

    def test_an_rx888_brings_radiod_and_its_infrastructure(self):
        """EXACTLY those three.  A superset assertion here is what let
        `_hardware_for` union every kind on the station unnoticed."""
        inv = _inv(("rx888",), [RX], [(RX, "rx888")])
        p = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert set(p.components) == {"ka9q-radio", "ka9q-web", "igmp-querier"}

    def test_a_gpsdo_brings_its_monitor(self):
        inv = _inv(("gpsdo",), [GPS], [(GPS, "gpsdo")])
        p = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert set(p.components) == {"gpsdo-monitor"}

    def test_adopting_one_device_does_not_plan_the_others(self):
        """The defect this guards: a Fargo box with an RX888 AND a miniGPS.
        Adopting the GPSDO alone must not plan radiod -- a service nobody
        named must never start (2026-09-01)."""
        inv = _inv(("rx888", "gpsdo"), [RX, GPS],
                   [(RX, "rx888"), (GPS, "gpsdo")])
        offer = next(o for o in offers(inv, frozenset()) if o.name == str(GPS))
        p = plan(offer, inv, ALIKE)
        assert set(p.components) == {"gpsdo-monitor"}
        assert "ka9q-radio" not in p.components

    def test_an_unrecognised_source_plans_nothing_rather_than_everything(self):
        """No mapping means no components.  Under-planning says so out loud;
        over-planning starts services on its own."""
        stray = SourceKey(type="usb", identifier="dead:beef")
        inv = _inv(("rx888", "gpsdo"), [stray])
        p = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert p.components == ()

    def test_a_remote_radiod_brings_no_local_radiod(self):
        """The no-hardware station consumes someone else's radiod."""
        inv = _inv((), [REMOTE])
        p = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert p.components == ()

    def test_a_kit_plan_covers_every_kit_component(self):
        """The kit branch is the one place breadth is correct: adopting the
        kit IS the decision to run all three devices."""
        inv = _inv(DASI2, [RX, GPS], [(RX, "rx888"), (GPS, "gpsdo")])
        p = plan(offers(inv, frozenset())[0], inv, SITE)
        assert set(p.components) == {
            "ka9q-radio", "ka9q-web", "igmp-querier",
            "gpsdo-monitor", "mag-recorder"}

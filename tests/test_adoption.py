# tests/test_adoption.py
"""What can this station adopt, and does it look like a known kit?

Pure over an inventory, so the whole decision surface tests with no USB bus
and no LAN — which is what lets Fargo be dry-run before anyone travels.
"""

import pytest

from sigmond.adoption import (DASI2_KIT, Offer, StationInventory,
                              consumers_for, offers, recognise)
from sigmond.sources import KNOWN_CLIENTS, SourceKey


def _inv(hardware=(), sources=(), kinds=()):
    """`kinds` is the (key, hardware-kind) mapping a real station carries for
    its LOCAL sources.  Omitting it means "this source stands for no local
    hardware" -- which is what a LAN radiod is, and what plans nothing.

    ⚠ So omitting it on a KIT fixture silently degrades the test: `offers()`
    returns per-source offers instead of one kit, and a `plan()` built from
    one of them has EMPTY components.  The test still passes -- on whatever
    assertions never touched the kit.  It is the same safe default that makes
    an unknown source kind plan nothing, and it reads as a passing test
    either way, so state the kinds and assert `offer.kind == "kit"`."""
    return StationInventory(hardware=frozenset(hardware),
                            sources=tuple(sources),
                            source_kinds=tuple(kinds))


DASI2 = ("rx888", "gpsdo", "magnetometer")
RX = SourceKey(type="usb", identifier="04b4:00f1:0009061C028B1629")
GPS = SourceKey(type="usb", identifier="1dd2:2211:mini01")
REMOTE = SourceKey(type="radiod", identifier="bee3-status.local")

#: What each local key stands for -- a real station always knows this.
KITKINDS = ((RX, "rx888"), (GPS, "gpsdo"))


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
        got = offers(_inv(DASI2, [RX, GPS], KITKINDS), adopted=frozenset())
        assert [o.name for o in got] == ["dasi2"]
        assert got[0].kind == "kit"

    def test_a_kit_does_not_swallow_a_radiod_on_the_lan(self):
        """A neighbour's radiod is not part of this station's kit.  Swept in,
        it was recorded as adopted for a station that had never configured it
        and vanished from `smd status` for good."""
        got = offers(_inv(DASI2, [RX, GPS, REMOTE], KITKINDS),
                     adopted=frozenset())
        assert [o.name for o in got] == ["dasi2", str(REMOTE)]
        assert got[0].sources == (RX, GPS)
        assert got[1].kind == "source"

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

# Shaped like Michael's real roster (2026-09-02): one station id, and one
# instrument per recorder -- a DASI2 site reports GRAPE/HF and magnetometer
# instruments under the same station.
SITE = StationIdentity(hostname="DASI007", dasi2_site=True,
                       psws_station="S000422",
                       psws_instruments=(("hf-timestd", "370"),
                                         ("mag-recorder", "371")))
ALIKE = StationIdentity(hostname="fargo-1", dasi2_site=False)


class TestPlan:

    def test_a_rostered_kit_asks_for_nothing(self):
        """Fleet provisioning is meant to be zero-input.

        The fixture used to omit `kinds`, so `offers()` returned two SOURCE
        offers rather than one kit and `[0]` planned no components at all --
        the test passed on its prefill assertions while testing nothing its
        name describes.  The kit assertions below are what make the name
        true.
        """
        inv = _inv(DASI2, [RX, GPS], KITKINDS)
        offer = offers(inv, frozenset())[0]
        assert offer.kind == "kit"
        assert offer.sources == (RX, GPS)
        p = plan(offer, inv, SITE)
        assert "mag-recorder" in p.components     # the whole kit, one decision
        assert p.ask == ()
        assert p.prefills["psws_station"] == "S000422"
        # The kit brings mag-recorder AND ka9q-radio, so both instruments are
        # prefilled -- one station id, two instruments, nothing typed.
        assert p.prefills["psws_instruments"] == {"hf-timestd": "370",
                                                  "mag-recorder": "371"}

    def test_the_instruments_do_not_depend_on_what_is_being_adopted(self):
        """A station's PSWS ids are facts about the STATION, not about the
        offer in hand.

        An earlier draft filtered the map by the offer's components and lost
        the GRAPE instrument entirely, because it belongs to `hf-timestd` --
        a profile client, not a component any hardware kind brings.  Adopting
        one device gets the same identity as adopting the kit.
        """
        inv = _inv(("gpsdo",), [GPS], ((GPS, "gpsdo"),))
        p = plan(offers(inv, frozenset())[0], inv, SITE)
        assert p.components == ("gpsdo-monitor",)
        assert p.prefills["psws_instruments"] == {"hf-timestd": "370",
                                                  "mag-recorder": "371"}

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

    def test_a_remote_radiod_brings_the_clients_that_can_decode_from_it(self):
        """The no-hardware station: design §2 says sigmond should find the
        radiod on the LAN and offer the clients that can consume it.  Before
        this, `smd status` offered it and `smd adopt` refused it."""
        inv = _inv((), [REMOTE])
        p = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert set(p.components) == set(KNOWN_CLIENTS)
        assert "ka9q-radio" not in p.components       # the SDR is not ours

    def test_the_radiod_client_list_is_not_restated(self):
        """One place answers "who consumes a radiod?"  Two would drift."""
        inv = _inv((), [REMOTE])
        p = plan(offers(inv, frozenset())[0], inv, ALIKE)
        assert tuple(p.components) == tuple(KNOWN_CLIENTS)

    def test_a_kit_plan_covers_every_kit_component(self):
        """The kit branch is the one place breadth is correct: adopting the
        kit IS the decision to run all three devices."""
        inv = _inv(DASI2, [RX, GPS], [(RX, "rx888"), (GPS, "gpsdo")])
        p = plan(offers(inv, frozenset())[0], inv, SITE)
        assert set(p.components) == {
            "ka9q-radio", "ka9q-web", "igmp-querier",
            "gpsdo-monitor", "mag-recorder"}


class TestConsumers:
    """Which components actually READ a source -- per key, not per offer."""

    def test_an_sdr_is_read_by_radiod_alone(self):
        inv = _inv(DASI2, [RX, GPS], KITKINDS)
        assert consumers_for(RX, inv) == ("ka9q-radio",)

    def test_a_gpsdo_is_read_by_its_monitor_alone(self):
        inv = _inv(DASI2, [RX, GPS], KITKINDS)
        assert consumers_for(GPS, inv) == ("gpsdo-monitor",)

    def test_ka9q_web_and_the_querier_read_nothing(self):
        """They are brought up by an SDR adoption but consume no source; a
        selection file for them stores a fact nobody reads."""
        inv = _inv(DASI2, [RX, GPS], KITKINDS)
        for key in (RX, GPS):
            assert "ka9q-web" not in consumers_for(key, inv)
            assert "igmp-querier" not in consumers_for(key, inv)

    def test_a_remote_radiod_is_read_by_the_decode_clients(self):
        assert consumers_for(REMOTE, _inv((), [REMOTE])) == tuple(KNOWN_CLIENTS)

    def test_an_unrecognised_source_is_read_by_nothing(self):
        stray = SourceKey(type="usb", identifier="dead:beef")
        assert consumers_for(stray, _inv(DASI2, [stray])) == ()

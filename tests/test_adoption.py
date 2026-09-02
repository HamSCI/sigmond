# tests/test_adoption.py
"""What can this station adopt, and does it look like a known kit?

Pure over an inventory, so the whole decision surface tests with no USB bus
and no LAN — which is what lets Fargo be dry-run before anyone travels.
"""

import pytest

from sigmond.adoption import DASI2_KIT, Offer, StationInventory, offers, recognise
from sigmond.sources import SourceKey


def _inv(hardware=(), sources=()):
    return StationInventory(hardware=frozenset(hardware), sources=tuple(sources))


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

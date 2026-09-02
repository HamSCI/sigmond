"""Predict what the Fargo Beelink will say, before anyone travels to it.

The sequence Michael described on 2026-09-02:
  1. install PM and VM with NOTHING attached
  2. reboot
  3. attach a uhubctl-capable hub, an RX888, and a Bodnar miniGPS
  4. no magnetometer and no TS-1 for weeks

A checklist says what to do.  This says what the machine will answer.
"""

from sigmond.adoption import StationInventory, offers, plan, recognise
from sigmond.sources import SourceKey
from sigmond.station_identity import StationIdentity

FARGO = StationIdentity(hostname="fargo-1", dasi2_site=False)

RX888 = SourceKey(type="usb", identifier="04b4:00f1:0009061C028B1629")
MINIGPS = SourceKey(type="usb", identifier="1dd2:2211:mini01")


def test_step_1_bare_install_offers_nothing():
    """Nothing attached: the install completes and offers nothing."""
    assert offers(StationInventory(), adopted=frozenset()) == []


def test_step_3_the_attached_kit_is_not_a_dasi2_match():
    """No magnetometer, so this is not the grant kit and must not claim it."""
    inv = StationInventory(hardware=frozenset({"rx888", "gpsdo"}),
                           sources=(RX888, MINIGPS),
                           source_kinds=((RX888, "rx888"),
                                         (MINIGPS, "gpsdo")))
    assert recognise(inv) is None


def test_step_3_offers_the_two_devices_separately():
    inv = StationInventory(hardware=frozenset({"rx888", "gpsdo"}),
                           sources=(RX888, MINIGPS),
                           source_kinds=((RX888, "rx888"),
                                         (MINIGPS, "gpsdo")))
    got = offers(inv, adopted=frozenset())
    assert {o.name for o in got} == {str(RX888), str(MINIGPS)}
    assert all(o.kind == "source" for o in got)


def test_adopting_the_rx888_brings_radiod_and_multicast():
    inv = StationInventory(hardware=frozenset({"rx888", "gpsdo"}),
                           sources=(RX888,),
                           source_kinds=((RX888, "rx888"),))
    p = plan(offers(inv, frozenset())[0], inv, FARGO)
    assert set(p.components) == {"ka9q-radio", "ka9q-web", "igmp-querier"}


def test_fargo_is_asked_for_psws_identity_and_prefilled_the_rest():
    """Not a funded site, so identity is asked; the hostname supplies radiod."""
    inv = StationInventory(hardware=frozenset({"rx888", "gpsdo"}),
                           sources=(RX888,),
                           source_kinds=((RX888, "rx888"),))
    p = plan(offers(inv, frozenset())[0], inv, FARGO)
    assert set(p.ask) == {"psws_station", "psws_instruments"}
    assert p.prefills["radiod_status_name"] == "fargo-1-status.local"


def test_after_adopting_both_nothing_remains_offered():
    inv = StationInventory(hardware=frozenset({"rx888", "gpsdo"}),
                           sources=(RX888, MINIGPS),
                           source_kinds=((RX888, "rx888"),
                                         (MINIGPS, "gpsdo")))
    assert offers(inv, adopted=frozenset({RX888, MINIGPS})) == []

"""Tests for lib/sigmond/usb_hubs.py and its use in sigmond-sdr-recover.

The inventory exists because "uhubctl says off" and "the RX-888 lost power"
are different claims, and a count of 04b4:00f3 sightings confused them on
2026-11-11: the RSHTECH hub was called a VBUS switcher on all seven ports
while the card's LED and an inline meter stayed lit.  The card had come back
as 00f1 first (firmware intact) and reset itself to 00f3 seconds later.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import usb_hubs  # noqa: E402
from tests.test_sdr_recover import _load  # noqa: E402

SR = _load()


def _hub(root: Path, devpath: str, ids: str) -> None:
    d = root / devpath
    d.mkdir(parents=True)
    vid, pid = ids.split(":")
    (d / "idVendor").write_text(vid + "\n")
    (d / "idProduct").write_text(pid + "\n")


class LookupTests(unittest.TestCase):
    def test_either_half_finds_the_hub(self):
        for ids in (["0bda:0411"], ["0bda:5411"], ["xxxx:yyyy", "0BDA:5411"]):
            self.assertEqual(usb_hubs.lookup(ids).name.split()[0], "RSHTECH", ids)

    def test_unknown_hub_is_none(self):
        self.assertIsNone(usb_hubs.lookup(["1234:5678"]))
        self.assertIsNone(usb_hubs.lookup([]))

    def test_only_the_lenovo_is_recorded_as_cutting_vbus(self):
        cutters = [h.name for h in usb_hubs.KNOWN_HUBS if h.vbus == usb_hubs.CUTS_VBUS]
        self.assertEqual(len(cutters), 1)
        self.assertIn("Lenovo", cutters[0])

    def test_same_via_chip_two_verdicts(self):
        # The board decides, not the chip: Lenovo's VL817 cuts, B4's does not.
        self.assertEqual(usb_hubs.lookup(["17ef:1039"]).vbus, usb_hubs.CUTS_VBUS)
        self.assertEqual(usb_hubs.lookup(["2109:2817"]).vbus, usb_hubs.DATA_ONLY)

    def test_every_entry_says_how_it_was_measured(self):
        for h in usb_hubs.KNOWN_HUBS:
            self.assertTrue(h.evidence, h.name)
            self.assertTrue(all(":" in i and len(i) == 9 for i in h.ids), h.name)


class DescribeTests(unittest.TestCase):
    def test_data_only_tells_the_operator_what_it_cannot_do(self):
        text = usb_hubs.describe(usb_hubs.lookup(["0bda:0411"]))
        self.assertIn("DATA LINK ONLY", text)
        self.assertIn("recover_port_data_only = true", text)
        self.assertIn("buttons DO cut power", text)
        # a generic chip match must not read as a product verdict
        self.assertIn("may behave differently", text)

    def test_cutter_points_at_the_verified_flag(self):
        text = usb_hubs.describe(usb_hubs.lookup(["17ef:103a"]))
        self.assertIn("REALLY CUTS", text)
        self.assertIn("recover_port_verified = true", text)
        self.assertNotIn("may behave differently", text)   # vendor ID names the product

    def test_ganged_is_said(self):
        self.assertIn("GANGED", usb_hubs.describe(usb_hubs.lookup(["05e3:0626"])))


class FirstReturnTests(unittest.TestCase):
    def test_firmware_kept_means_power_held(self):
        self.assertIn("did NOT cut VBUS", usb_hubs.first_return_verdict("00f1"))

    def test_bootloader_first_is_consistent_not_proof(self):
        v = usb_hubs.first_return_verdict("00f3")
        self.assertIn("only the LED or a meter proves", v)

    def test_unseen_says_nothing(self):
        self.assertIn("says nothing", usb_hubs.first_return_verdict(None))


class HubIdsTests(unittest.TestCase):
    def test_reads_both_halves_and_skips_root_hubs(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _hub(root, "5-1.1", "0bda:0411")
            _hub(root, "4-1.1", "0bda:5411")
            self.assertEqual(usb_hubs.hub_ids(["5-1.1", "4-1.1", "5", None], root),
                             ("0bda:0411", "0bda:5411"))


class SdrRecoverNoteTests(unittest.TestCase):
    def test_note_for_a_card_on_a_known_hub(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _hub(root, "5-1.1", "0bda:0411")
            loc = SR.Located("5-1.1.1", "5-1.1", "1", "SER", "5000", "4-1.1", "1", "00f1", "7")
            self.assertIn("RSHTECH", SR.known_hub_note(loc, root))

    def test_no_note_for_an_unknown_hub_or_no_card(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _hub(root, "2-1", "1234:5678")
            loc = SR.Located("2-1.3", "2-1", "3", "SER", "5000")
            self.assertIsNone(SR.known_hub_note(loc, root))
        self.assertIsNone(SR.known_hub_note(None))

    def test_wait_records_the_first_sighting(self):
        cards = iter([[SR.Located("5-1.4", "5-1", "4", "S", "5000", product="00f1")]])
        first = []
        orig = SR.all_rx888
        SR.all_rx888 = lambda *a, **k: next(cards)
        try:
            card, why = SR.wait_for_running_card([("5-1", "4", False)], "S",
                                                 timeout_s=5, first_seen=first)
        finally:
            SR.all_rx888 = orig
        self.assertEqual(why, "ready")
        self.assertEqual(first, ["00f1"])


if __name__ == "__main__":
    unittest.main()

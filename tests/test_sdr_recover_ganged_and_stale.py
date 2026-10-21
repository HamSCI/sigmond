"""sdr-recover: a GANGED hub must still be driveable, and a STALE learned
port must be named rather than silently cycled.

Both failures were measured on AI6VN, 2026-10-21, while rob's RX-888 sat off
the bus and the station reported "USB failing" without recovering.

1. STALE LOCATION — the real fault.  The state recorded the card on hub 4-2
   port 4.  Across a re-enumeration the hub became 4-1, so every cycle ran
   `uhubctl -l 4-2 -p 4 -a off -e`, got "No compatible devices detected at
   location 4-2", switched a port THAT DID NOT EXIST, saw no card return, and
   entered cooldown -- reporting a failed recovery on a hub that worked
   perfectly.  A replug by hand fixed it in seconds.

2. GANGED HUB — the latent one.  uhubctl refuses a hub whose descriptor says
   `ganged power switching`, with the SAME words it uses for a location that
   does not exist.  A UGREEN 05e3:0626 declares ganged, is refused by default,
   and under `-f` really drops VBUS (proved: a USB-Ethernet adapter vanished
   from the bus and returned).  Without --force such a hub reads as "not
   switchable" and the card is never recovered.

⚠ The guiding rule, learned by getting it wrong first: a probe that cannot
CONFIRM a port must never be the reason a vanished card goes unrecovered.
"uhubctl did not confirm" is not "the port does not exist" -- it is also what
an unreadable or stubbed probe looks like.  So staleness is REPORTED and the
cycle still runs.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

REFUSED = "No compatible devices detected at location 4-2!\n"
GANGED_OK = (
    "Current status for hub 4-1 [05e3:0626 GenesysLogic USB3.1 Hub, "
    "USB 3.20, 4 ports, ganged]\n"
    "  Port 4: 0203 power 5gbps U0 enable connect [04b4:00f1 RX888mk2]\n")


class _CP:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def _load():
    name = "sdr_recover_ganged_under_test"
    loader = importlib.machinery.SourceFileLoader(
        name, str(REPO / "bin" / "sigmond-sdr-recover"))
    spec = importlib.util.spec_from_file_location(
        name, str(REPO / "bin" / "sigmond-sdr-recover"), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


class GangedHubTests(unittest.TestCase):
    def setUp(self):
        self.sdr = _load()
        self.calls = []

    def _runner(self, plain_refuses: bool):
        def fake(cmd, *a, **k):
            self.calls.append(list(cmd))
            forced = "-f" in cmd
            if plain_refuses and not forced:
                return _CP("", REFUSED, 1)
            return _CP(GANGED_OK, "", 0)
        return fake

    def test_a_ganged_hub_is_driveable_under_force(self):
        self.sdr._run = self._runner(plain_refuses=True)
        self.assertTrue(
            self.sdr._uhubctl_sees("4-1", "4", True),
            "a hub refused by default but driveable with --force must be "
            "reported switchable, or its card is never recovered")
        self.assertTrue(any("-f" in c for c in self.calls),
                        "--force was never tried")

    def test_force_is_only_a_fallback_never_the_first_call(self):
        self.sdr._run = self._runner(plain_refuses=False)
        self.assertTrue(self.sdr._uhubctl_sees("4-1", "4", True))
        self.assertNotIn("-f", self.calls[0],
                         "a hub that answers normally must never be forced")

    def test_force_does_not_rescue_a_location_that_does_not_exist(self):
        # The AI6VN fault: 4-2 is gone. Force must not paper over it, because
        # the cure is the right address, not more force.
        self.sdr._run = lambda cmd, *a, **k: _CP("", REFUSED, 1)
        self.assertFalse(self.sdr._uhubctl_sees("4-2", "4", True))


class StaleTargetTests(unittest.TestCase):
    def setUp(self):
        self.sdr = _load()

    def test_a_stale_location_is_separated_from_a_live_one(self):
        def sees(hub, port, exact):
            return hub == "4-1"          # 4-2 has gone; 4-1 is the real hub
        self.sdr._uhubctl_sees = sees
        live, stale = self.sdr.live_targets([("4-2", "4", True),
                                             ("4-1", "4", True)])
        self.assertEqual(live, [("4-1", "4", True)])
        self.assertEqual(stale, [("4-2", "4", True)],
                         "the stale path must be named, which is what AI6VN "
                         "never said while it cycled a port that was not there")

    def test_an_unreadable_probe_never_marks_anything_stale(self):
        # ⛔ The rule that cost 13 broken tests when I got it backwards:
        # uhubctl missing or unreadable (None) must keep every target, so a
        # probe that cannot look is never the reason a card goes unrecovered.
        self.sdr._uhubctl_sees = lambda h, p, e: None
        targets = [("4-2", "4", True), ("4-1", "4", False)]
        live, stale = self.sdr.live_targets(targets)
        self.assertEqual(live, targets)
        self.assertEqual(stale, [])


if __name__ == "__main__":
    unittest.main()


class LinkPromotionTests(unittest.TestCase):
    """A card on the WRONG BUS is not a dead card.

    AI6VN, 2026-10-21, diagnosed by rob from the DFU sequence: the RX-888
    enumerates in its FX3 bootloader (04b4:00f3) at HIGH SPEED, takes its
    firmware, and must then RE-TRAIN the link to SuperSpeed as 04b4:00f1.
    Some hubs do not follow that transition.  The card then sits on the USB2
    bus at 480 Mb/s, radiod refuses it --

        rx888_usb_init() failed / device setup returned -1

    -- and the station reads as a hardware failure while the card is present
    and healthy.  Measured: a Lenovo/VIA 17ef:1039 follows it every time, a
    UGREEN 05e3:0626 never does.  A USB-Ethernet adapter never shows the
    fault, because it arrives as a USB3 device and links once, which is why
    the hub passes every other test.

    The fix is a SHORT blip, the opposite of a recovery cycle: the FX3 keeps
    its firmware across a brief drop, so the card returns as 00f1 with no DFU
    phase and negotiates afresh.  Proved on the station: 2-1.1 @ 480 -> blip
    -> 4-1.1 @ 5000, radiod active.
    """
    def setUp(self):
        self.sdr = _load()

    def _loc(self, speed, pid="00f1", peer=("4-1", "1")):
        return self.sdr.Located("2-1.1", "2-1", "1", "SER", speed,
                                peer[0], peer[1], pid, "99")

    def test_a_running_card_below_superspeed_is_underspeed(self):
        self.assertTrue(self.sdr.link_underspeed(self._loc("480")))

    def test_a_superspeed_card_is_left_alone(self):
        self.assertFalse(self.sdr.link_underspeed(self._loc("5000")))

    def test_the_bootloader_is_never_blipped(self):
        # ⛔ 00f3 at high speed is CORRECT and expected; blipping there would
        # interrupt the firmware load the card is in the middle of.
        self.assertFalse(self.sdr.link_underspeed(self._loc("480", pid="00f3")))

    def test_a_usb2_only_path_is_never_blipped(self):
        # No SuperSpeed peer means no faster link to re-train onto, so a blip
        # would drop a working card for nothing.  This is the `usb2-only`
        # case the duality tests already cover.
        self.assertFalse(
            self.sdr.link_underspeed(self._loc("480", peer=(None, None))))

    def test_absent_card_is_not_underspeed(self):
        self.assertFalse(self.sdr.link_underspeed(None))

    def test_the_blip_is_far_shorter_than_a_recovery_cut(self):
        # ⛔ The two cuts have OPPOSITE purposes: --off-seconds is long so the
        # FX3 DROPS its firmware and reloads; the blip is short so it KEEPS
        # it.  A blip as long as a recovery would send the card back through
        # the bootloader and land it at 480 again -- the very state it is
        # meant to leave.
        self.assertLess(self.sdr.LINK_BLIP_S, 10)
        self.assertEqual(self.sdr.SS_SPEED, "5000")

"""sdr-recover --off / --on: manual RX-888 power control, and its hold.

Why this exists at all.  The only proof that a hub really cuts VBUS is
watching the RX-888's LED go dark.  An automatic 5-25 s reset is not
watchable, so rob needs a pair of commands that take the radio down and
LEAVE it down -- the job WsprDaemon's `wd -U off` / `wd -U on` do.

Three things make it more than two uhubctl calls, and each is a test here:

  * "off" must be a MEASUREMENT, not a claim.  A USB3 jack has two halves
    sharing one VBUS rail; switching the half uhubctl happens to accept and
    declaring victory leaves the card powered.  So every half must be
    switchable before anything is stopped, and the card must be confirmed
    gone from the bus afterwards.

  * The automatic watchdogs must STAND DOWN.  Measured on AI6VN today: the
    recovery timer fires every 30 s and the radiod watchdog every minute.
    Either one would turn the port back on under the operator's hands --
    and worse, would make the hub look as if it had recovered the card by
    itself, which is exactly the question being asked.

  * A two-card station must never be guessed at.  N8UR, ON5KQ and KX4AZ-T
    each run two or three RX-888s; powering down the wrong radio is a
    silent outage on a station nobody is looking at.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import io
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, script: str):
    loader = importlib.machinery.SourceFileLoader(name, str(REPO / "bin" / script))
    spec = importlib.util.spec_from_file_location(
        name, str(REPO / "bin" / script), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


sdr = _load("sdr_manual_under_test", "sigmond-sdr-recover")
wd = _load("radiod_watchdog_under_test", "sigmond-radiod-watchdog")


def _card(serial="RX0001", devpath="4-1.4", hub="4-1", port="4",
          speed="5000", peer_hub="3-1", peer_port="4", product="00f1"):
    return sdr.Located(devpath, hub, port, serial, speed,
                       peer_hub, peer_port, product, "7")


class TestSelectCard(unittest.TestCase):
    """Which radio.  The answer is never "whichever sysfs listed first"."""

    def test_one_card_needs_no_serial(self):
        with mock.patch.object(sdr, "all_rx888", return_value=[_card()]):
            card, err = sdr._select_card(None)
        self.assertIsNone(err)
        self.assertEqual(card.serial, "RX0001")

    def test_two_cards_without_a_serial_is_refused_not_guessed(self):
        """⛔ The regression this guards: locate_rx888() returns the FIRST
        04b4:00f1 in sysfs.  Reusing it here would power down whichever card
        the kernel happened to enumerate first -- on a station where the
        other one is the production radio."""
        cards = [_card("RX0001", "4-1.4"), _card("RX0002", "4-1.5", port="5")]
        with mock.patch.object(sdr, "all_rx888", return_value=cards):
            card, err = sdr._select_card(None)
        self.assertIsNone(card)
        self.assertIn("--serial", err)
        self.assertIn("RX0001", err)
        self.assertIn("RX0002", err)

    def test_two_cards_with_a_serial_selects_exactly_that_one(self):
        cards = [_card("RX0001", "4-1.4"), _card("RX0002", "4-1.5", port="5")]
        with mock.patch.object(sdr, "all_rx888", return_value=cards):
            card, err = sdr._select_card("RX0002")
        self.assertIsNone(err)
        self.assertEqual(card.devpath, "4-1.5")

    def test_an_unknown_serial_names_what_is_actually_present(self):
        with mock.patch.object(sdr, "all_rx888", return_value=[_card("RX0001")]):
            card, err = sdr._select_card("RX9999")
        self.assertIsNone(card)
        self.assertIn("RX0001", err)


class TestOffRefusals(unittest.TestCase):
    """What --off must refuse to do, before it stops a single service."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hold = Path(self.tmp.name) / "sdr-hold.json"
        patch = mock.patch.object(sdr, "HOLD_PATH", self.hold)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.stopped = mock.patch.object(sdr, "_stop_station").start()
        self.addCleanup(mock.patch.stopall)

    def test_refuses_when_only_one_half_of_the_jack_can_be_switched(self):
        """⛔ Both halves share VBUS.  Switching the reachable half and
        reporting success leaves the card powered -- and the operator stares
        at a lit LED concluding the hub does not cut VBUS, when in fact we
        never switched the half that is wired to the switch."""
        with mock.patch.object(sdr, "all_rx888", return_value=[_card()]), \
             mock.patch.object(sdr, "hub_switchable",
                               return_value=(True, [("3-1", "4", False)])), \
             mock.patch.object(sdr, "port_halves",
                               return_value={"ss": False, "usb2": True}):
            rc = sdr.manual_off(None, {})
        self.assertEqual(rc, 1)
        self.stopped.assert_not_called()
        self.assertFalse(self.hold.exists(),
                         "a refused --off must leave no hold behind")

    def test_refuses_when_the_hub_has_no_per_port_power_switching(self):
        with mock.patch.object(sdr, "all_rx888", return_value=[_card()]), \
             mock.patch.object(sdr, "hub_switchable", return_value=(False, [])):
            rc = sdr.manual_off(None, {})
        self.assertEqual(rc, 1)
        self.stopped.assert_not_called()

    def test_refuses_when_anything_else_shares_the_jack(self):
        """The GPSDO, the TS-1 and the Pololu board live on neighbouring
        ports of the same hub.  A power cut takes down whatever shares the
        port, on either half."""
        with mock.patch.object(sdr, "all_rx888", return_value=[_card()]), \
             mock.patch.object(sdr, "hub_switchable",
                               return_value=(True, [("4-1", "4", True)])), \
             mock.patch.object(sdr, "port_halves",
                               return_value={"ss": True, "usb2": None}), \
             mock.patch.object(sdr, "partition_targets",
                               return_value=([], [(("4-1", "4", True),
                                                   [("4-1.4.1", "1dd2:2211")])])):
            rc = sdr.manual_off(None, {})
        self.assertEqual(rc, 1)
        self.stopped.assert_not_called()

    def test_refuses_a_second_off_while_one_is_already_held(self):
        self.hold.write_text(json.dumps({"RX0001": {"since": 1, "locations": []}}))
        with mock.patch.object(sdr, "all_rx888", return_value=[_card()]):
            rc = sdr.manual_off(None, {})
        self.assertEqual(rc, 1)
        self.stopped.assert_not_called()


class TestOffHappyPath(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hold = Path(self.tmp.name) / "sdr-hold.json"
        mock.patch.object(sdr, "HOLD_PATH", self.hold).start()
        self.stop = mock.patch.object(sdr, "_stop_station").start()
        self.set = mock.patch.object(sdr, "_uhubctl_set", return_value=True).start()
        mock.patch.object(sdr, "detect_radiod_unit",
                          return_value="radiod@AI6VN.service").start()
        mock.patch.object(sdr, "installed_units",
                          return_value=["wspr-recorder@AI6VN.service"]).start()
        mock.patch.object(sdr, "time").start().time.return_value = 1000.0
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(self.tmp.cleanup)

    def _run_off(self, after_off):
        with mock.patch.object(sdr, "all_rx888", return_value=[_card()]), \
             mock.patch.object(sdr, "hub_switchable",
                               return_value=(True, [("4-1", "4", True),
                                                    ("3-1", "4", False)])), \
             mock.patch.object(sdr, "port_halves",
                               return_value={"ss": True, "usb2": True}), \
             mock.patch.object(sdr, "partition_targets",
                               return_value=([("4-1", "4", True),
                                              ("3-1", "4", False)], [])), \
             mock.patch.object(sdr, "port_power",
                               return_value=("unverified", "switching unverified")), \
             mock.patch.object(sdr, "locate_rx888", return_value=after_off):
            return sdr.manual_off(None, {"consumers": ["wspr-recorder@*"]})

    def test_switches_both_halves_stops_the_station_and_writes_the_hold(self):
        rc = self._run_off(after_off=None)          # card left the bus
        self.assertEqual(rc, 0)
        self.stop.assert_called_once()
        switched = {(c.args[0], c.args[1], c.args[3]) for c in self.set.call_args_list}
        self.assertEqual(switched, {("4-1", "4", "off"), ("3-1", "4", "off")},
                         "both halves of the jack must be switched off")
        hold = json.loads(self.hold.read_text())["RX0001"]
        self.assertEqual(hold["radiod_unit"], "radiod@AI6VN.service")
        self.assertEqual(hold["consumers"], ["wspr-recorder@AI6VN.service"])
        self.assertEqual(sorted(hold["locations"]),
                         [["3-1", "4", False], ["4-1", "4", True]])

    def test_a_card_still_on_the_bus_after_the_cut_is_a_FAILURE(self):
        """⛔ "I ran uhubctl off" is not "the port is off".  On AI6VN's VIA
        dock the USB2 half reported off while the card streamed on
        regardless.  If the card is still enumerated, say so -- an operator
        about to judge a hub by its LED must not be told the port is dark
        when it is not."""
        rc = self._run_off(after_off=_card())       # still there
        self.assertEqual(rc, 1)
        self.assertIn("RX0001", json.loads(self.hold.read_text()),
                      "the hold must stand so --on can still restore the port")


class TestOn(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hold = Path(self.tmp.name) / "sdr-hold.json"
        mock.patch.object(sdr, "HOLD_PATH", self.hold).start()
        self.set = mock.patch.object(sdr, "_uhubctl_set", return_value=True).start()
        self.restart = mock.patch.object(sdr, "restart_station",
                                         return_value=True).start()
        self.wait = mock.patch.object(
            sdr, "wait_for_running_card",
            return_value=(_card(), "ready")).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(self.tmp.cleanup)
        self.hold.write_text(json.dumps({"RX0001": {
            "serial": "RX0001", "devpath": "4-1.4",
            "locations": [["3-1", "4", False], ["4-1", "4", True]],
            "radiod_unit": "radiod@AI6VN.service",
            "consumers": ["wspr-recorder@AI6VN.service"], "since": 1000.0}}))

    def test_restores_both_ports_restarts_the_station_and_clears_the_hold(self):
        rc = sdr.manual_on(None, {})
        self.assertEqual(rc, 0)
        self.restart.assert_called_once_with("radiod@AI6VN.service",
                                             ["wspr-recorder@AI6VN.service"])
        self.assertFalse(self.hold.exists())

    def test_switches_the_superspeed_half_on_first(self):
        """A card brought up on the USB2 half first can enumerate at 480 and
        be refused by radiod.  The SuperSpeed half leads."""
        with mock.patch.object(sdr, "hub_speed",
                               side_effect=lambda h: "5000" if h == "4-1" else "480"):
            sdr.manual_on(None, {})
        order = [c.args[0] for c in self.set.call_args_list]
        self.assertEqual(order[0], "4-1")

    def test_a_port_that_will_not_come_back_on_KEEPS_the_hold(self):
        """⛔ A failed "on" leaves the port dark, which is strictly worse than
        never having cut it.  Clearing the hold there would release the
        watchdogs onto a radio whose port a human still has to reach."""
        self.set.return_value = False
        rc = sdr.manual_on(None, {})
        self.assertEqual(rc, 1)
        self.restart.assert_not_called()
        self.assertIn("RX0001", json.loads(self.hold.read_text()))

    def test_a_card_that_never_returns_KEEPS_the_hold(self):
        self.wait.return_value = (None, "the card never returned to the bus")
        rc = sdr.manual_on(None, {})
        self.assertEqual(rc, 1)
        self.restart.assert_not_called()
        self.assertIn("RX0001", json.loads(self.hold.read_text()))

    def test_a_card_STUCK_IN_ITS_BOOTLOADER_does_not_start_radiod(self):
        """⛔ The 2026-10-26 defect.  After a real VBUS cut the card's FIRST
        appearance is 04b4:00f3 at 480 Mb/s; --on took that as arrival and
        started radiod, which exited status=66/NOINPUT.  It recovered only
        because systemd's Restart= tried again 11 s later.  A station without
        that, or one whose retry also lands early, is simply down."""
        self.wait.return_value = (None, "the card is stuck in its FX3 "
                                        "bootloader (04b4:00f3 at 480 Mb/s)")
        rc = sdr.manual_on(None, {})
        self.assertEqual(rc, 1)
        self.restart.assert_not_called()
        self.assertIn("RX0001", json.loads(self.hold.read_text()),
                      "the hold must stand while the card is not usable")

    def test_the_wait_is_told_which_ports_and_serial_to_watch(self):
        sdr.manual_on(None, {})
        locations, serial = self.wait.call_args.args[:2]
        self.assertEqual(sorted(locations),
                         [("3-1", "4", False), ("4-1", "4", True)])
        self.assertEqual(serial, "RX0001")

    def test_two_held_radios_without_a_serial_is_refused(self):
        self.hold.write_text(json.dumps({"RX0001": {"locations": [["4-1", "4", True]]},
                                         "RX0002": {"locations": [["4-1", "5", True]]}}))
        rc = sdr.manual_on(None, {})
        self.assertEqual(rc, 1)
        self.set.assert_not_called()

    def test_on_with_no_hold_changes_nothing(self):
        self.hold.unlink()
        rc = sdr.manual_on(None, {})
        self.assertEqual(rc, 1)
        self.set.assert_not_called()
        self.restart.assert_not_called()


class TestWaitForRunningCard(unittest.TestCase):
    """⛔ "ON THE BUS" IS NOT "READY".

    Measured on AI6VN 2026-10-26 21:42:49Z.  After a real VBUS cut the card
    comes back as its FX3 bootloader, 04b4:00f3 at 480 Mb/s on the USB2 half
    (3-1.4).  rx888_boot loads the firmware and it re-enumerates as 00f1 at
    5000 on the SuperSpeed half (4-1.4) seconds later.  --on took the first
    sighting as arrival, started radiod against the bootloader, and radiod
    exited status=66/NOINPUT.  It recovered only because systemd restarted it
    11 s later -- so the bug was invisible behind Restart=.
    """

    LOCS = [("4-1", "4", True), ("3-1", "4", False)]

    def setUp(self):
        mock.patch.object(sdr.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def _run(self, sightings, **kw):
        """Each call to all_rx888() returns the next sighting, then repeats
        the last one for as long as the wait keeps polling."""
        queue = list(sightings)

        def next_sighting():
            return queue.pop(0) if len(queue) > 1 else queue[0]

        with mock.patch.object(sdr, "all_rx888", side_effect=next_sighting):
            return sdr.wait_for_running_card(self.LOCS, "RX0001", **kw)

    def test_waits_THROUGH_the_bootloader_to_the_running_card(self):
        boot = _card(devpath="3-1.4", hub="3-1", port="4", speed="480",
                     peer_hub="4-1", product="00f3", serial="")
        ready = _card()          # 4-1.4, 00f1, 5000
        card, why = self._run([[boot], [boot], [ready]])
        self.assertIsNotNone(card)
        self.assertEqual((card.product, card.speed), ("00f1", "5000"))
        self.assertEqual(why, "ready")

    def test_a_card_that_stays_in_its_bootloader_is_NOT_ready(self):
        boot = _card(devpath="3-1.4", hub="3-1", port="4", speed="480",
                     peer_hub="4-1", product="00f3", serial="")
        card, why = self._run([[boot]], timeout_s=1)
        self.assertIsNone(card)
        self.assertIn("00f3", why)
        # Distinct from the "hub does not follow the transition" message,
        # which also says "bootloader": the operator's next move differs.
        # Stuck at 00f3 means the FIRMWARE LOADER did not run.
        self.assertIn("firmware loader did not finish", why)

    def test_a_BOOTLOADER_AT_SUPERSPEED_is_still_not_ready(self):
        """The readiness test is on the PRODUCT ID, not on speed alone.  An
        FX3 bootloader that happens to link at 5000 carries no firmware and
        radiod would refuse it exactly as it refuses one at 480."""
        boot = _card(product="00f3", speed="5000", serial="")
        card, why = self._run([[boot]], timeout_s=1)
        self.assertIsNone(card)
        self.assertIn("00f3", why)

    def test_a_card_at_480_WITH_ITS_FIRMWARE_gets_one_blip(self):
        """00f1 at 480 is the hub not following the bootloader-to-SuperSpeed
        transition.  One blip re-trains it; this is promote_link's case, and
        it must happen inside the wait rather than after radiod starts."""
        slow = _card(devpath="3-1.4", hub="3-1", port="4", speed="480",
                     peer_hub="4-1")
        with mock.patch.object(sdr, "all_rx888", return_value=[slow]), \
             mock.patch.object(sdr, "promote_link",
                               return_value=_card()) as blip:
            card, why = sdr.wait_for_running_card(self.LOCS, "RX0001",
                                                  timeout_s=2)
        self.assertIsNotNone(card)
        self.assertEqual(card.speed, "5000")
        self.assertIn("blip", why)
        blip.assert_called_once()

    def test_the_blip_is_tried_ONCE_not_every_poll(self):
        """A blip per poll would hammer a card that cannot be promoted, and
        each one drops the link again -- turning a slow link into a loop."""
        slow = _card(devpath="3-1.4", hub="3-1", port="4", speed="480",
                     peer_hub="4-1")
        with mock.patch.object(sdr, "all_rx888", return_value=[slow]), \
             mock.patch.object(sdr, "promote_link", return_value=slow) as blip:
            card, why = sdr.wait_for_running_card(self.LOCS, "RX0001",
                                                  timeout_s=2)
        self.assertIsNone(card)
        self.assertEqual(blip.call_count, 1)
        self.assertIn("does not follow", why)

    def test_a_card_that_never_appears_says_so_in_its_own_words(self):
        card, why = self._run([[]], timeout_s=1)
        self.assertIsNone(card)
        self.assertIn("never returned", why)

    def test_the_card_is_matched_by_JACK_when_the_bootloader_has_no_serial(self):
        """The FX3 bootloader need not present the serial the running
        firmware does, so a serial-only match would lose the card for the
        whole bootloader phase -- which is exactly the phase being waited
        through.  (hub, port) against the ports --off switched carries it."""
        boot = _card(devpath="3-1.4", hub="3-1", port="4", speed="480",
                     peer_hub="4-1", product="00f3", serial="")
        card, why = self._run([[boot]], timeout_s=1)
        self.assertIsNone(card)
        self.assertIn("bootloader", why,
                      "the card must have been RECOGNISED, not merely absent")

    def test_another_station_card_on_a_DIFFERENT_jack_is_ignored(self):
        """On a two-card station the other radio must not end the wait."""
        other = _card(serial="RX0002", devpath="4-1.7", port="7")
        card, why = self._run([[other]], timeout_s=1)
        self.assertIsNone(card)
        self.assertIn("never returned", why)


class TestHoldStandsDownTheAutomaticPaths(unittest.TestCase):
    """The hold is worthless unless BOTH watchdogs read it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hold = Path(self.tmp.name) / "sdr-hold.json"
        self.addCleanup(self.tmp.cleanup)

    def test_sdr_recover_main_stands_down_while_a_hold_stands(self):
        self.hold.write_text(json.dumps({"RX0001": {"since": 1}}))
        with mock.patch.object(sdr, "HOLD_PATH", self.hold), \
             mock.patch.object(sdr, "read_config", return_value={}), \
             mock.patch.object(sdr, "device_present") as present, \
             mock.patch.object(sdr, "detect_radiod_unit", return_value=None):
            rc = sdr.main([])
        self.assertEqual(rc, 0)
        present.assert_not_called()

    def test_sdr_recover_main_runs_normally_with_no_hold(self):
        """The mutation guard for the test above: without a hold the helper
        must still reach its own decision, or 'stands down' proves nothing."""
        with mock.patch.object(sdr, "HOLD_PATH", self.hold), \
             mock.patch.object(sdr, "read_config", return_value={}), \
             mock.patch.object(sdr, "device_present",
                               return_value=True) as present, \
             mock.patch.object(sdr, "detect_radiod_unit", return_value=None):
            sdr.main([])
        present.assert_called()

    def test_radiod_watchdog_skips_the_unit_a_hold_names(self):
        self.hold.write_text(json.dumps(
            {"RX0001": {"radiod_unit": "radiod@AI6VN.service"}}))
        with mock.patch.object(wd, "HOLD_PATH", self.hold):
            units, blanket = wd._held_units()
        self.assertEqual(units, ("radiod@AI6VN.service",))
        self.assertFalse(blanket)

    def test_a_hold_naming_no_unit_covers_every_instance(self):
        """Without a serial -> radiod instance map we cannot tell which radio
        lost its card, so an unattributed hold must stop the lot rather than
        restart the wrong one."""
        self.hold.write_text(json.dumps({"RX0001": {"since": 1}}))
        with mock.patch.object(wd, "HOLD_PATH", self.hold):
            units, blanket = wd._held_units()
        self.assertTrue(blanket)

    def test_the_watchdog_SAYS_SO_when_a_hold_stays_its_hand(self):
        """⛔ --off STOPS radiod, so the watchdog's unit list is EMPTY exactly
        when a hold is in force.  Returning 0 in silence left an operator on
        AI6VN unable to tell whether the hold had been seen at all.  A
        watchdog that cannot say why it stayed its hand is as hard to trust
        as one that cannot say why it acted."""
        self.hold.write_text(json.dumps({"RX0001": {"since": 1}}))
        buf = io.StringIO()
        with mock.patch.object(wd, "HOLD_PATH", self.hold), \
             mock.patch.object(wd, "_active_radiod_units", return_value=[]), \
             mock.patch.object(wd, "_honor_restart_request"), \
             contextlib.redirect_stdout(buf):
            rc = wd.main()
        self.assertEqual(rc, 0)
        self.assertIn("hold in force", buf.getvalue())
        self.assertIn("--on", buf.getvalue())

    def test_the_watchdog_says_NOTHING_about_holds_when_none_stands(self):
        """Mutation guard for the test above: the line must be conditional,
        not unconditional, or every ordinary minute logs a hold notice."""
        buf = io.StringIO()
        with mock.patch.object(wd, "HOLD_PATH", self.hold), \
             mock.patch.object(wd, "_active_radiod_units", return_value=[]), \
             mock.patch.object(wd, "_honor_restart_request"), \
             contextlib.redirect_stdout(buf):
            wd.main()
        self.assertNotIn("hold in force", buf.getvalue())

    def test_no_hold_file_means_the_watchdog_works_as_before(self):
        with mock.patch.object(wd, "HOLD_PATH", self.hold):
            units, blanket = wd._held_units()
        self.assertEqual(units, ())
        self.assertFalse(blanket)

    def test_a_corrupt_hold_file_does_not_wedge_the_watchdog(self):
        self.hold.write_text("{not json")
        with mock.patch.object(wd, "HOLD_PATH", self.hold):
            units, blanket = wd._held_units()
        self.assertEqual((units, blanket), ((), False))


if __name__ == "__main__":
    unittest.main()

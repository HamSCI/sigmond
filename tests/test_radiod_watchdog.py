"""Tests for sigmond-radiod-watchdog — the wedged-radiod restarter.

Written against a live failure on AC0G-B4, 2026-08-30 03:09Z.  The RX-888
stalled, radiod wedged, and the watchdog correctly decided to restart it.
It ran ``systemctl stop``, that call exceeded the 60 s subprocess timeout
because the wedged process was slow to die, and ``TimeoutExpired`` unwound
out of ``_restart`` before the matching ``start`` was ever issued.  systemd
completed the stop 31 s later on its own, so the station was left cleanly
stopped and nothing brought it back: ``Restart=always`` does not fire on a
deliberate stop.  B4 was dark for 59 minutes.

The invariant these tests protect: once ``_restart`` has stopped a unit, it
issues the start.  A stop that times out, fails, or raises must not be able
to consume the start.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import sdr_events as ev  # noqa: E402


def _load():
    name = "radiod_watchdog_under_test"
    path = str(REPO / "bin" / "sigmond-radiod-watchdog")
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


wd = _load()


class RestartAlwaysStarts(unittest.TestCase):
    """_restart must issue the start whatever the stop did."""

    def setUp(self):
        self.calls = []
        self._run = wd._run
        self._reset = wd._reset_usb_devices
        self._mark = wd._mark_restarted
        self._sleep = wd.time.sleep
        self._note = wd._note_event
        wd._reset_usb_devices = lambda: None
        wd._mark_restarted = lambda unit, now: None
        wd._note_event = lambda unit, reason, now: None
        wd.time.sleep = lambda s: None
        wd.DRY_RUN = False

    def tearDown(self):
        wd._run = self._run
        wd._reset_usb_devices = self._reset
        wd._mark_restarted = self._mark
        wd._note_event = self._note
        wd.time.sleep = self._sleep

    def _verbs(self):
        return [c[1] for c in self.calls if c[0] == "systemctl"]

    def test_start_follows_a_normal_stop(self):
        def fake(cmd, timeout=60):
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        wd._run = fake
        wd._restart("radiod@X.service", "wedged", 1000.0)
        self.assertEqual(self._verbs(), ["stop", "start"])

    def test_start_still_issued_when_stop_times_out(self):
        """The AC0G-B4 03:09Z failure: stop exceeded its timeout."""
        def fake(cmd, timeout=60):
            self.calls.append(cmd)
            if cmd[1] == "stop":
                raise subprocess.TimeoutExpired(cmd, 60)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        wd._run = fake
        wd._restart("radiod@X.service", "wedged", 1000.0)
        self.assertIn("start", self._verbs(),
                      "a timed-out stop must not consume the start")

    def test_start_still_issued_when_stop_returns_nonzero(self):
        def fake(cmd, timeout=60):
            self.calls.append(cmd)
            if cmd[1] == "stop":
                return subprocess.CompletedProcess(cmd, 1, "", "boom")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        wd._run = fake
        wd._restart("radiod@X.service", "wedged", 1000.0)
        self.assertIn("start", self._verbs())

    def test_start_still_issued_when_usb_reset_raises(self):
        def fake(cmd, timeout=60):
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        wd._run = fake

        def boom():
            raise OSError("no such device")
        wd._reset_usb_devices = boom
        wd._restart("radiod@X.service", "wedged", 1000.0)
        self.assertIn("start", self._verbs())

    def test_dry_run_issues_nothing(self):
        def fake(cmd, timeout=60):
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        wd._run = fake
        wd.DRY_RUN = True
        try:
            wd._restart("radiod@X.service", "wedged", 1000.0)
        finally:
            wd.DRY_RUN = False
        self.assertEqual(self._verbs(), [])


T0 = 1_760_000_000.0
UNIT = "radiod@ac0g.service"


class RestartNotesTheLoss(unittest.TestCase):
    """v3.71.  Each restart the watchdog makes leaves one event for `smd
    doctor`.  systemd reads the watchdog's stop as asked for, so radiod's own
    ExecStopPost= hook writes nothing; without this, a wedge that the
    watchdog cleared would leave no trace."""

    SAVED = ("_run", "_reset_usb_devices", "_mark_restarted", "_cooldown_ok",
             "_unit_prop", "_epoch", "_journal_since_invocation",
             "SDR_EVENTS_PATH", "DRY_RUN")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.events = self.tmp / "sdr-events.jsonl"
        self.calls = []
        self._saved = {n: getattr(wd, n) for n in self.SAVED}
        self._sleep = wd.time.sleep

        def fake(cmd, timeout=60):
            self.calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        wd._run = fake
        wd._reset_usb_devices = lambda: None
        wd._mark_restarted = lambda unit, now: None
        wd.time.sleep = lambda s: None
        wd.SDR_EVENTS_PATH = self.events
        wd.DRY_RUN = False

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(wd, n, v)
        wd.time.sleep = self._sleep

    def lines(self):
        return [json.loads(l) for l in self.events.read_text().splitlines()]

    def verbs(self):
        return [c[1] for c in self.calls if c and c[0] == "systemctl"]

    def a_unit_an_hour_old(self, journal):
        wd._unit_prop = lambda unit, prop: {"InvocationID": "abc",
                                            "ActiveEnterTimestamp": "x"}[prop]
        wd._epoch = lambda ts: T0 - 3600
        wd._journal_since_invocation = lambda unit, inv: journal

    def test_a_restart_leaves_one_event_naming_the_unit_and_the_reason(self):
        wd._restart(UNIT, "no journal output for 700s (heartbeat lost)", T0)
        (d,) = self.lines()
        self.assertEqual(d["kinds"], ["radiod-wedge"])
        self.assertIn(UNIT, d["detail"])
        self.assertIn("heartbeat lost", d["detail"])

    def test_the_quitting_detector_records_through_restart(self):
        self.a_unit_an_hour_old([
            f"{T0 - 300:.6f} host radiod[1]: No rx888 data for 5 seconds, quitting",
            f"{T0 - 10:.6f} host radiod[1]: measured sample rate 64800000"])
        wd._cooldown_ok = lambda unit, now: True
        wd.check_unit(UNIT, T0)
        (d,) = self.lines()
        self.assertIn("quitting", d["detail"])
        self.assertEqual(self.verbs(), ["stop", "start"])

    def test_a_unit_in_cooldown_records_nothing(self):
        self.a_unit_an_hour_old([f"{T0 - 900:.6f} host radiod[1]: last words"])
        wd._cooldown_ok = lambda unit, now: False
        with contextlib.redirect_stdout(io.StringIO()):
            wd.check_unit(UNIT, T0)
        self.assertFalse(self.events.exists())
        self.assertEqual(self.verbs(), [])

    def test_the_event_lands_before_the_stop(self):
        seen = []

        def fake(cmd, timeout=60):
            seen.append((cmd[1], self.events.exists()))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        wd._run = fake
        wd._restart(UNIT, "wedged", T0)
        self.assertEqual(seen[0], ("stop", True))

    def test_a_dry_run_records_nothing(self):
        wd.DRY_RUN = True
        with contextlib.redirect_stdout(io.StringIO()):
            wd._restart(UNIT, "wedged", T0)
        self.assertFalse(self.events.exists())

    def test_a_restart_and_radiods_failing_starts_make_one_episode(self):
        wd._restart(UNIT, 'declared "no rx888 data, quitting" 300s ago', T0)
        ev.record("radiod-exit", "ac0g core-dump dumped/ABRT", now=T0 + 20, path=self.events)
        (d,) = self.lines()
        self.assertEqual(d["kinds"], ["radiod-exit", "radiod-wedge"])
        self.assertEqual(d["count"], 2)

    def test_a_record_that_fails_still_restarts(self):
        (self.tmp / "file").write_text("")
        wd.SDR_EVENTS_PATH = self.tmp / "file" / "sdr-events.jsonl"
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            wd._restart(UNIT, "wedged", T0)
        self.assertEqual(self.verbs(), ["stop", "start"])
        self.assertIn("could not record", err.getvalue())

    def test_a_restart_adds_no_line_of_its_own(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            wd._restart(UNIT, "wedged", T0)
        self.assertEqual(len(out.getvalue().splitlines()), 1, out.getvalue())


class HoldScopedBySerial(unittest.TestCase):
    """A hold that names a serial covers only that card's radiod."""

    def _held(self, holds, serial_map=None):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sdr-hold.json"
            path.write_text(json.dumps(holds))
            old, wd.HOLD_PATH = wd.HOLD_PATH, path
            try:
                return wd._held_units(serial_map)
            finally:
                wd.HOLD_PATH = old

    def test_hold_with_serial_but_no_unit_resolves_through_map(self):
        got = self._held({"ABC": {"serial": "abc "}}, {"ABC": "kfs-se"})
        self.assertEqual(got, (("radiod@kfs-se.service",), False))

    def test_hold_with_unknown_serial_is_blanket(self):
        got = self._held({"ABC": {"serial": "ABC"}}, {"XYZ": "kfs-se"})
        self.assertEqual(got, ((), True))

    def test_legacy_hold_without_serial_or_unit_is_blanket(self):
        for holds in ({"ABC": {}}, {"ABC": "garbage"}, {"ABC": None}):
            self.assertEqual(self._held(holds, {"ABC": "kfs-se"}),
                             ((), True), holds)

    def test_hold_with_unit_wins_over_serial(self):
        hold = {"ABC": {"serial": "ABC", "radiod_unit": "radiod@other.service"}}
        got = self._held(hold, {"ABC": "kfs-se"})
        self.assertEqual(got, (("radiod@other.service",), False))

    def test_no_map_behaves_as_before(self):
        hold = {"ABC": {"serial": "ABC"}}
        self.assertEqual(self._held(hold), ((), True))
        self.assertEqual(self._held(hold, {}), ((), True))

    def test_detail_reports_which_units_came_from_the_map(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h.json"
            path.write_text(json.dumps({
                "A": {"serial": "A"},
                "B": {"radiod_unit": "radiod@b.service"}}))
            old, wd.HOLD_PATH = wd.HOLD_PATH, path
            try:
                units, blanket, by_serial = wd._held_units_detail({"A": "a"})
            finally:
                wd.HOLD_PATH = old
        self.assertEqual(units, ("radiod@a.service", "radiod@b.service"))
        self.assertFalse(blanket)
        self.assertEqual(by_serial, {"radiod@a.service"})


if __name__ == "__main__":
    unittest.main()

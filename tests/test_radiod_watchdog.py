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

import importlib.machinery
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


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
        wd._reset_usb_devices = lambda: None
        wd._mark_restarted = lambda unit, now: None
        wd.time.sleep = lambda s: None
        wd.DRY_RUN = False

    def tearDown(self):
        wd._run = self._run
        wd._reset_usb_devices = self._reset
        wd._mark_restarted = self._mark
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


if __name__ == "__main__":
    unittest.main()

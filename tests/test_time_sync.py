"""Tests for sigmond-time-sync — chrony enforcement ported from wsprdaemon's
wd-time-sync.sh (N8UR 2026-09-06: timesyncd polled a dead DHCP server, 7 s slow)."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    p = str(REPO / "bin" / "sigmond-time-sync")
    loader = importlib.machinery.SourceFileLoader("timesync_under_test", p)
    spec = importlib.util.spec_from_file_location("timesync_under_test", p, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


m = _load()
SYNCED = "17BAA87F,ntp.maxhost.io,2,1788652489.1,0.000791124,0.000281298,0.001309,-3.2,0.01,0.1,0.03,0.01,64.5,Normal"
UNSYNC = "00000000,,0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,Not synchronised"


class TestTracking(unittest.TestCase):
    def test_parses_synced_line(self):
        t = m.parse_tracking_csv(SYNCED)
        self.assertEqual((t.stratum, t.leap, t.ref_name), (2, "Normal", "ntp.maxhost.io"))
        self.assertTrue(m.is_synced(t, 1.0))

    def test_not_synchronised_is_unsynced(self):
        self.assertFalse(m.is_synced(m.parse_tracking_csv(UNSYNC), 1.0))

    def test_large_offset_is_unsynced_even_when_tracking(self):
        t = m.parse_tracking_csv(SYNCED.replace("0.000791124", "7.2"))
        self.assertFalse(m.is_synced(t, 1.0))

    def test_garbage_is_none(self):
        self.assertIsNone(m.parse_tracking_csv("506 Cannot talk to daemon"))
        self.assertFalse(m.is_synced(None, 1.0))


class TestConfig(unittest.TestCase):
    def test_sources_file_lists_pools(self):
        s = m.render_sources(["pool.ntp.org", "time.nist.gov"])
        self.assertIn("pool pool.ntp.org iburst\n", s)
        self.assertIn("pool time.nist.gov iburst\n", s)

    def test_managed_block_is_appended_once_and_replaced_in_place(self):
        conf = "pool 2.debian.pool.ntp.org iburst\nmakestep 1 3\nconfdir /etc/chrony/conf.d\n"
        once = m.with_managed_block(conf, ["makestep 1 -1"])
        self.assertTrue(once.endswith(f"{m.MARK_BEGIN}\nmakestep 1 -1\n{m.MARK_END}\n"))
        self.assertEqual(once.count(m.MARK_BEGIN), 1)
        twice = m.with_managed_block(once, ["makestep 1 -1", "pool x iburst"])
        self.assertEqual(twice.count(m.MARK_BEGIN), 1)
        self.assertIn("pool x iburst", twice)
        # distro lines untouched, and our makestep is LAST (chrony: last makestep wins)
        self.assertLess(twice.index("makestep 1 3"), twice.index("makestep 1 -1"))

    def test_sourcedir_detection_is_anchored(self):
        self.assertTrue(m.has_sourcedir("sourcedir /etc/chrony/sources.d\n"))
        self.assertFalse(m.has_sourcedir("# sourcedir /etc/chrony/sources.d\n"))

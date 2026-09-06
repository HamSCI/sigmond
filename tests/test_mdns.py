"""Tests for sigmond-mdns — the guard that keeps radiod's .local names
resolvable.  Ported from wsprdaemon's wd-mdns.sh after N8GA's two stations
sat silent for days (2026-09-06) behind an avahi stuck in REGISTERING."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    p = str(REPO / "bin" / "sigmond-mdns")
    loader = importlib.machinery.SourceFileLoader("mdns_under_test", p)
    spec = importlib.util.spec_from_file_location("mdns_under_test", p, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


m = _load()


class TestParsing(unittest.TestCase):
    def test_avahi_state_from_busctl(self):
        self.assertEqual(m.parse_avahi_state("i 2\n"), 2)
        self.assertEqual(m.parse_avahi_state("i 1"), 1)
        self.assertIsNone(m.parse_avahi_state(""))

    def test_status_names_from_coordination_toml(self):
        text = '[host]\ncall = "AI6VN"\n[radiod."AI6VN-status.local"]\nx = 1\n' \
               '[radiod."remote-status.local"]\n[radiod."notlocal"]\n'
        self.assertEqual(m.status_names_from_toml(text),
                         ["AI6VN-status.local", "remote-status.local"])

    def test_status_names_from_radiod_conf_ignores_commented_examples(self):
        """wsprdaemon 6d0101f: an unanchored match on a commented example
        line corrupted K6FOD's FT8 stream name."""
        text = "[global]\n# status = example-status.local\nstatus = AI6VN-status.local\n" \
               "  status   =  \"quoted-status.local\"\n"
        self.assertEqual(m.status_names_from_radiod_conf(text),
                         ["AI6VN-status.local", "quoted-status.local"])


class TestDecision(unittest.TestCase):
    def test_healthy(self):
        d = m.decide(True, 2, [], True, True, None)
        self.assertTrue(d.healthy); self.assertFalse(d.repair)

    def test_avahi_stuck_registering_repairs(self):
        d = m.decide(True, 1, ["a-status.local"], True, True, None)
        self.assertFalse(d.healthy); self.assertTrue(d.repair)
        self.assertIn("REGISTERING", d.reason)

    def test_records_lost_after_avahi_restart_repairs(self):
        """avahi says RUNNING, radiod runs, but its names are gone."""
        d = m.decide(True, 2, ["a-status.local"], True, True, None)
        self.assertTrue(d.repair); self.assertIn("lost", d.reason)

    def test_no_radiod_means_nothing_to_repair(self):
        d = m.decide(True, 2, ["a-status.local"], False, True, None)
        self.assertTrue(d.healthy); self.assertFalse(d.repair)

    def test_report_mode_never_repairs(self):
        d = m.decide(True, 1, ["a-status.local"], True, False, None)
        self.assertFalse(d.repair); self.assertFalse(d.healthy)

    def test_cooldown_holds_a_second_repair(self):
        d = m.decide(True, 1, ["a-status.local"], True, True, 120)
        self.assertFalse(d.repair)
        d = m.decide(True, 1, ["a-status.local"], True, True, 1000)
        self.assertTrue(d.repair)

    def test_avahi_down(self):
        d = m.decide(False, None, ["a-status.local"], True, True, None)
        self.assertTrue(d.repair); self.assertIn("not running", d.reason)

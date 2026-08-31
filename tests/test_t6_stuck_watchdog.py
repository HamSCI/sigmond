"""Tests for sigmond-t6-stuck-watchdog.

AC0G-B4, 2026-08-31: T6 sat UNLOCKED for 6 h 23 m (03:38-10:01Z) and
only a restart of timestd-core-recorder cleared it.  Everything the
authority depends on was healthy the whole time:

  * C/N0 57-58 dB-Hz -- HIGHER than when it had been working at 03Z
  * costas_locked True throughout; the carrier never let go
  * T5/LBE-1421 120/120 with a valid fix, NMEA age 0.3 s
  * an independent block-wise estimator on the SAME multicast located
    the edge sample-exactly, 98% of seconds, median SNR ~340

So the signal carried the information continuously and the software
could not use it.  Availability went 97.2% (08-30) to 41.5% (08-31).

The trap this must avoid is the one sigmond's own watchdogs document:
"a watchdog that fires on a downstream symptom restarts things forever
without fixing anything" -- timestd-hpps-watchdog restarted the recorder
four times overnight on 2026-08-29 against a stale refclock when the
real cause was low C/N0.  So this fires ONLY when the evidence says the
INPUT IS GOOD and the software is nonetheless not using it.  Weak
signal, an unlocked carrier, or a dead GPS are all reasons NOT to act.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    name = "t6_stuck_under_test"
    path = str(REPO / "bin" / "sigmond-t6-stuck-watchdog")
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


wd = _load()


def state(**kw):
    base = dict(authoritative=False, costas_locked=True, cn0_db_hz=57.5,
                t5_valid_fix=True, stuck_for_sec=1800.0)
    base.update(kw)
    return wd.T6State(**base)


class TestFiresOnlyOnTheStuckState(unittest.TestCase):
    def test_the_ac0g_b4_signature_fires(self):
        """Unlocked, carrier fine, signal strong, GPS good, sustained."""
        self.assertTrue(wd.should_restart(state()))

    def test_healthy_t6_never_fires(self):
        self.assertFalse(wd.should_restart(state(authoritative=True)))


class TestRefusesWhenTheInputIsBad(unittest.TestCase):
    """Each of these is a reason the software is RIGHT to be unlocked."""

    def test_weak_signal_is_not_a_stuck_state(self):
        self.assertFalse(wd.should_restart(state(cn0_db_hz=52.0)))

    def test_an_unlocked_carrier_is_not_a_stuck_state(self):
        self.assertFalse(wd.should_restart(state(costas_locked=False)))

    def test_no_gps_fix_is_not_a_stuck_state(self):
        self.assertFalse(wd.should_restart(state(t5_valid_fix=False)))

    def test_a_brief_unlock_is_not_a_stuck_state(self):
        """T6 re-acquires on its own in minutes; only a sustained
        unlock against a good signal means stuck."""
        self.assertFalse(wd.should_restart(state(stuck_for_sec=120.0)))

    def test_missing_telemetry_refuses_rather_than_guesses(self):
        self.assertFalse(wd.should_restart(state(cn0_db_hz=None)))
        self.assertFalse(wd.should_restart(state(costas_locked=None)))


class TestStormGuards(unittest.TestCase):
    def test_cooldown_blocks_a_second_restart(self):
        self.assertFalse(wd.cooldown_ok(now=1000.0, last_action=1000.0 - 10))

    def test_cooldown_expires(self):
        self.assertTrue(
            wd.cooldown_ok(now=1000.0, last_action=1000.0 - wd.COOLDOWN_SEC - 1))

    def test_no_prior_action_is_allowed(self):
        self.assertTrue(wd.cooldown_ok(now=1000.0, last_action=None))

    def test_daily_cap_is_enforced(self):
        self.assertFalse(wd.under_daily_cap(wd.MAX_PER_DAY))
        self.assertTrue(wd.under_daily_cap(wd.MAX_PER_DAY - 1))


if __name__ == "__main__":
    unittest.main()


class TestTelemetryComesFromWhereItActuallyLives(unittest.TestCase):
    """C/N0 is not in core-recorder-status.json.

    The first cut read t6_pps.baseband_power / t6_pps.n0 from the status
    file.  Those keys do not exist there -- the frontend probe writes
    them to the authority snapshot store -- so the gate would have
    returned None on every tick and the watchdog would have sat silent
    forever while looking installed and healthy.
    """

    def test_cn0_reads_the_authority_db(self):
        import sqlite3
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db") as fh:
            con = sqlite3.connect(fh.name)
            con.execute("CREATE TABLE authority_snapshot ("
                        "utc_published TEXT, t6_baseband_power REAL, t6_n0 REAL)")
            con.execute("INSERT INTO authority_snapshot VALUES "
                        "('2026-08-31T04:00:00Z', -95.0, -153.7)")
            con.commit(); con.close()
            self.assertAlmostEqual(wd.cn0_from_db(fh.name), 58.7, places=1)

    def test_a_missing_db_refuses_rather_than_acting(self):
        self.assertIsNone(wd.cn0_from_db("/nonexistent/nope.db"))

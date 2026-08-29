"""Tests for sigmond-sdr-recover — RX888 power-cycle recovery.

The RX-888 recurrently drops off the USB bus and only a power cycle of the
card recovers it.  This helper cuts and restores VBUS on the hub port, then
brings the station back in the one order that works: radiod first, then the
units that consume its RTP streams.

Two failure modes shape every test here, and both were observed on AC0G-B4:

  * A watchdog that fires on a downstream symptom restarts things forever
    without fixing anything.  timestd-hpps-watchdog restarted the recorder
    four times overnight on 2026-08-29 against a stale chrony refclock,
    while the actual cause was low C/N0.  So this helper fires ONLY on the
    device being absent from the bus, and never on a consequence of that.

  * A consumer left running against a restarted radiod fails SILENTLY.
    wspr-recorder ran two days against a stale RTP anchor, throwing a
    timing fault every two minutes that nothing acted on.  So the consumer
    set is declared, not discovered by an ad-hoc glob typed at the time.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    loader = importlib.machinery.SourceFileLoader(
        "sdr_recover_under_test", str(REPO / "bin" / "sigmond-sdr-recover"))
    spec = importlib.util.spec_from_file_location(
        "sdr_recover_under_test", str(REPO / "bin" / "sigmond-sdr-recover"),
        loader=loader)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: dataclasses resolves field types through
    # sys.modules, so a module-level @dataclass raises AttributeError if the
    # module is not yet there.
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


sdr = _load()


class TestShouldCycle(unittest.TestCase):
    """The trigger. Narrow on purpose."""

    def test_cycles_when_device_absent_long_enough(self):
        d = sdr.should_cycle(device_present=False, absent_for_s=120,
                             last_cycle_age_s=None)
        self.assertTrue(d.cycle)
        self.assertIn("absent", d.reason)

    def test_never_cycles_while_the_device_is_present(self):
        """Whatever else is wrong, a present device is not this tool's problem.
        Cycling a healthy card because something downstream looks unhappy is
        the hpps-watchdog failure repeated."""
        d = sdr.should_cycle(device_present=True, absent_for_s=0,
                             last_cycle_age_s=None)
        self.assertFalse(d.cycle)

    def test_waits_out_a_brief_absence(self):
        """A re-enumeration blips the device off the bus for a second or two.
        Cycling then would interrupt its own recovery."""
        d = sdr.should_cycle(device_present=False, absent_for_s=5,
                             last_cycle_age_s=None)
        self.assertFalse(d.cycle)
        self.assertIn("grace", d.reason)

    def test_rate_limits_repeat_cycles(self):
        """If a cycle did not fix it, cycling again immediately will not
        either — and a power-cycle loop is worse than a dead SDR."""
        d = sdr.should_cycle(device_present=False, absent_for_s=600,
                             last_cycle_age_s=30)
        self.assertFalse(d.cycle)
        self.assertIn("cooldown", d.reason)

    def test_cycles_again_once_the_cooldown_expires(self):
        d = sdr.should_cycle(device_present=False, absent_for_s=1800,
                             last_cycle_age_s=sdr.CYCLE_COOLDOWN_S + 1)
        self.assertTrue(d.cycle)


class TestConsumerExpansion(unittest.TestCase):
    """The declared set. A glob typed by hand is how wspr-recorder got missed."""

    INSTALLED = [
        "wspr-recorder@AC0G\\x3dB4.service",
        "psk-recorder@AC0G\\x3dB4.service",
        "timestd-core-recorder.service",
        "timestd-metrology@SHARED_5000.service",
        "timestd-metrology@WWV_20000.service",
        "mag-recorder.service",
        "timestd-web-api.service",
    ]

    def test_expands_instance_patterns(self):
        got = sdr.expand_consumers(["wspr-recorder@*.service"], self.INSTALLED)
        self.assertEqual(got, ["wspr-recorder@AC0G\\x3dB4.service"])

    def test_expands_a_template_to_every_instance(self):
        got = sdr.expand_consumers(["timestd-metrology@*.service"], self.INSTALLED)
        self.assertEqual(len(got), 2)

    def test_preserves_declared_order(self):
        """radiod's consumers are restarted in the declared order; a set or a
        dict comprehension would silently reorder them."""
        got = sdr.expand_consumers(
            ["timestd-core-recorder.service", "wspr-recorder@*.service"],
            self.INSTALLED)
        self.assertEqual(got[0], "timestd-core-recorder.service")

    def test_omits_units_that_do_not_consume_radiod(self):
        got = sdr.expand_consumers(
            ["wspr-recorder@*.service", "psk-recorder@*.service"], self.INSTALLED)
        self.assertNotIn("mag-recorder.service", got)
        self.assertNotIn("timestd-web-api.service", got)

    def test_a_pattern_matching_nothing_is_reported_not_swallowed(self):
        """A consumer that stops matching — renamed unit, changed instance —
        must surface. Silently restarting fewer things is the failure this
        whole module exists to prevent."""
        got, missing = sdr.expand_consumers_checked(
            ["wspr-recorder@*.service", "hfdl-recorder@*.service"], self.INSTALLED)
        self.assertEqual(missing, ["hfdl-recorder@*.service"])
        self.assertEqual(len(got), 1)


if __name__ == "__main__":
    unittest.main()

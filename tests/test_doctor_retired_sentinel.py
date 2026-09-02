"""`smd doctor` must name the retired SDR sentinel where it is still armed.

⛔ Why this check exists.  Until 2026-09-02 the appliance wizard installed
`sigmond-sdr-sentinel.timer`, which every two minutes ran
`smd bringup dasi2 --non-interactive` whenever it saw an RX888 and no radiod
conf.  That made hardware appearing an INSTRUCTION: a station brought its whole
stack up, unattended and unasked, within two minutes of a device being plugged
in.  `smd adopt` replaced it and asks first.

Retiring it in the wizard only changes NEW installs.  Every station already in
the field keeps the timer, so on those machines the constraint the adoption
design rests on — nothing starts until asked — is simply not true.  A station
cannot fix what nothing reports, so `doctor` is where the retired timer has to
surface.

The check is REPORT-ONLY.  Removing it stops a running station from bringing
itself up, which is a judgement call an operator makes, not one a diagnostic
makes for them.
"""
import importlib.machinery
import importlib.util
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    # bin/smd re-execs into the production venv unless told not to; without
    # this, importing the script would os.execv() and replace pytest itself.
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_retired_sentinel", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(
        "smd_under_test_retired_sentinel", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class RetiredUnitCheck(unittest.TestCase):
    """`_retired_unit_findings` reports an armed sentinel and stays quiet
    otherwise."""

    def test_an_armed_sentinel_is_reported(self):
        findings = smd._retired_unit_findings(
            is_enabled=lambda unit: "enabled")
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.kind, "retired-unit")
        self.assertIn("sigmond-sdr-sentinel.timer", f.detail)

    def test_the_finding_says_what_it_does_and_how_to_stop_it(self):
        """An operator reading this must learn the consequence AND the fix —
        a finding that only names a unit tells them nothing they can act on."""
        detail = smd._retired_unit_findings(
            is_enabled=lambda unit: "enabled")[0].detail.lower()
        self.assertIn("bringup", detail)      # what it does
        self.assertIn("adopt", detail)        # what replaced it
        self.assertIn("systemctl disable", detail)   # how to stop it

    def test_a_disabled_sentinel_is_not_reported(self):
        self.assertEqual(
            smd._retired_unit_findings(is_enabled=lambda unit: "disabled"), [])

    def test_an_absent_sentinel_is_not_reported(self):
        """The expected state on a fresh install and on every non-appliance
        host — it must not manufacture a finding out of a missing unit."""
        self.assertEqual(
            smd._retired_unit_findings(is_enabled=lambda unit: None), [])

    def test_it_is_report_only(self):
        """Never fixable: stopping a running station from bringing itself up
        is the operator's call, not a diagnostic's."""
        f = smd._retired_unit_findings(is_enabled=lambda unit: "enabled")[0]
        self.assertFalse(f.fixable)

    def test_an_unreadable_systemctl_reports_nothing_rather_than_crashing(self):
        """A diagnostic has to survive the permission problems it exists to
        report — doctor runs unprivileged on hosts where systemctl may fail."""
        def boom(unit):
            raise OSError("systemctl: not found")
        self.assertEqual(smd._retired_unit_findings(is_enabled=boom), [])


if __name__ == "__main__":
    unittest.main()

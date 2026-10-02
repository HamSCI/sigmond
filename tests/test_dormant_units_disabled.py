"""A dormant hardware-gated component must be systemd-DISABLED, not just unstarted.

`smd start` has refused to start a hardware-gated component with no device for
some time (harmonize.dormant_reason -> "don't start it, it would crash-loop").
But that gate guards the smd VERB.  systemd does not consult it: the client's
unit ships WantedBy=multi-user.target with UnitFilePreset=enabled, so every
boot starts it regardless, and with no device it crash-loops into
start-limit-hit.

Observed 2026-10-02 on WB6CXC-7, which has no Pololu USB-I2C adapter on the
bus at all (`mag-recorder inventory --json` -> "hardware_present": false):

    Active: failed (Result: start-limit-hit) since Fri 2026-10-02 16:28:35 UTC
    Duration: 1.030s

Boot was 16:27:21, so all 10 of StartLimitBurst were spent in 74 seconds.
W3USR-06 was in the identical state — a class defect, not one station's.

The fix must stay REVERSIBLE: topology still says enabled=true, because the
component does belong on a dasi2 station; it is the hardware that is absent,
not the intent.
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_dormant", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_dormant", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class _Unit:
    def __init__(self, unit, orphaned=False):
        self.unit = unit
        self.orphaned = orphaned


class DormantUnitsAreDisabled(unittest.TestCase):

    def _harness(self, units, is_enabled='enabled'):
        """Record every systemctl smd runs; report is-enabled as given."""
        calls = []

        def fake_run(cmd, *a, **kw):
            calls.append(list(cmd))
            out = is_enabled if cmd[:2] == ['systemctl', 'is-enabled'] else ''
            return subprocess.CompletedProcess(cmd, 0, out, '')

        self._orig_run = smd._run
        smd._run = fake_run
        self.addCleanup(lambda: setattr(smd, '_run', self._orig_run))

        import sigmond.lifecycle as lifecycle
        orig = lifecycle.resolve_units
        lifecycle.resolve_units = lambda *a, **kw: units
        self.addCleanup(lambda: setattr(lifecycle, 'resolve_units', orig))
        return calls

    def test_an_enabled_dormant_unit_is_disabled(self):
        calls = self._harness([_Unit('mag-recorder.service')])

        smd._dormant_disable_units('mag-recorder')

        self.assertIn(['systemctl', 'disable', '--now', 'mag-recorder.service'], calls)

    def test_the_start_limit_failure_is_cleared(self):
        """Otherwise the unit stays red forever for a condition sigmond has
        now deliberately resolved."""
        calls = self._harness([_Unit('mag-recorder.service')])

        smd._dormant_disable_units('mag-recorder')

        self.assertIn(['systemctl', 'reset-failed', 'mag-recorder.service'], calls)

    def test_an_already_disabled_unit_is_left_alone(self):
        """The null.  Without this, a test that only ever saw 'enabled' would
        pass against code that disables unconditionally — including units
        systemd would never have started."""
        calls = self._harness([_Unit('mag-recorder.service')], is_enabled='disabled')

        smd._dormant_disable_units('mag-recorder')

        self.assertNotIn(['systemctl', 'disable', '--now', 'mag-recorder.service'], calls)

    def test_a_static_unit_is_left_alone(self):
        calls = self._harness([_Unit('mag-recorder.service')], is_enabled='static')

        smd._dormant_disable_units('mag-recorder')

        self.assertFalse([c for c in calls if 'disable' in c])

    def test_orphaned_units_are_skipped(self):
        calls = self._harness([_Unit('mag-recorder@stale.service', orphaned=True)])

        smd._dormant_disable_units('mag-recorder')

        self.assertFalse([c for c in calls if 'disable' in c])

    def test_unresolvable_units_warn_and_do_not_raise(self):
        """Best-effort: a station must never fail to start because this
        bookkeeping did not work."""
        import sigmond.lifecycle as lifecycle
        orig = lifecycle.resolve_units

        def boom(*a, **kw):
            raise RuntimeError('no deploy.toml')

        lifecycle.resolve_units = boom
        self.addCleanup(lambda: setattr(lifecycle, 'resolve_units', orig))

        smd._dormant_disable_units('mag-recorder')   # must not raise


if __name__ == '__main__':
    unittest.main()

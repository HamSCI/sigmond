"""Tests for sigmond.hardware — the CONTRACT §3 / Phase D readiness probe.

Hermetic: the client `inventory --json` call and the lsusb fallbacks are
monkeypatched so nothing touches the host bus or PATH.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond import hardware


class TestHardwareReady(unittest.TestCase):

    def _patch_inventory(self, value):
        orig = hardware.inventory_hardware_present
        hardware.inventory_hardware_present = lambda client: value
        self.addCleanup(lambda: setattr(
            hardware, 'inventory_hardware_present', orig))

    def _patch_legacy(self, mapping):
        orig = hardware._LEGACY_PROBES
        hardware._LEGACY_PROBES = mapping
        self.addCleanup(lambda: setattr(hardware, '_LEGACY_PROBES', orig))

    def test_inventory_is_authoritative_over_fallback(self):
        # Self-describe says False even though the lsusb fallback says True.
        self._patch_inventory(False)
        self._patch_legacy({'mag-recorder': lambda: True})
        self.assertIs(hardware.hardware_ready('mag-recorder'), False)

    def test_inventory_true_short_circuits(self):
        self._patch_inventory(True)
        self._patch_legacy({'mag-recorder': lambda: False})
        self.assertIs(hardware.hardware_ready('mag-recorder'), True)

    def test_falls_back_when_inventory_silent(self):
        # Client doesn't report the field -> lsusb fallback decides.
        self._patch_inventory(None)
        self._patch_legacy({'mag-recorder': lambda: True})
        self.assertIs(hardware.hardware_ready('mag-recorder'), True)

    def test_none_when_no_inventory_and_no_fallback(self):
        self._patch_inventory(None)
        self._patch_legacy({})
        self.assertIsNone(hardware.hardware_ready('whatever'))


class TestInventoryHardwarePresent(unittest.TestCase):

    def _patch(self, *, exe, run_result):
        import shutil
        import subprocess
        orig_which, orig_run = shutil.which, subprocess.run
        shutil.which = lambda name: exe
        subprocess.run = lambda *a, **k: run_result
        self.addCleanup(lambda: setattr(shutil, 'which', orig_which))
        self.addCleanup(lambda: setattr(subprocess, 'run', orig_run))

    class _R:
        def __init__(self, returncode, stdout):
            self.returncode, self.stdout = returncode, stdout

    def test_none_when_client_not_on_path(self):
        self._patch(exe=None, run_result=self._R(0, '{}'))
        self.assertIsNone(hardware.inventory_hardware_present('x'))

    def test_reads_bool_field(self):
        self._patch(exe='/usr/bin/x',
                    run_result=self._R(0, '{"hardware_present": true}'))
        self.assertIs(hardware.inventory_hardware_present('x'), True)

    def test_none_when_field_absent(self):
        self._patch(exe='/usr/bin/x', run_result=self._R(0, '{"client": "x"}'))
        self.assertIsNone(hardware.inventory_hardware_present('x'))

    def test_none_on_nonzero_exit(self):
        self._patch(exe='/usr/bin/x', run_result=self._R(1, ''))
        self.assertIsNone(hardware.inventory_hardware_present('x'))

    def test_none_on_unparseable_json(self):
        self._patch(exe='/usr/bin/x',
                    run_result=self._R(0, 'Logging configured\n{bad'))
        self.assertIsNone(hardware.inventory_hardware_present('x'))


if __name__ == '__main__':
    unittest.main()


class TestRm3100HeldAdapter(unittest.TestCase):
    """#20: the probe must not touch an adapter mag-recorder already holds.
    DASI-011 2026-10-14: `mag-usb -A 0x23` froze a held device 1 s into bring-up."""

    def setUp(self):
        import json
        import tempfile
        import time
        from datetime import datetime, timezone
        self._json, self._time = json, time
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = Path(self._tmp.name)
        self.magusb = self.d / 'mag-usb'
        self.magusb.write_text('')
        self.dev = self.d / 'ttyMAG0'
        self.dev.write_text('')
        self.now = time.time()
        self.day = datetime.fromtimestamp(self.now, tz=timezone.utc).strftime('%Y-%m-%d')
        self.calls = []

    def _samples(self, n=60, age=2.0, frozen=False):
        from datetime import datetime, timezone
        lines = []
        for i in range(n):
            t = self.now - age - (n - 1 - i)
            ts = datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
            v = 1.0 if frozen else float(i)
            lines.append(self._json.dumps(
                {'ts': ts, 'x': v, 'y': v, 'z': v, 'rt': 21.5}))
        (self.d / f'samples-{self.day}.jsonl').write_text('\n'.join(lines) + '\n')

    def _run(self, unit_active, probe_out='{"x": 1}'):
        import types

        def run(argv, **kw):
            self.calls.append(list(argv))
            if argv[0] == 'systemctl':
                return types.SimpleNamespace(returncode=0 if unit_active else 3,
                                             stdout='', stderr='')
            if argv[0] == 'fuser':
                return types.SimpleNamespace(returncode=1, stdout='', stderr='')
            return types.SimpleNamespace(returncode=0, stdout=probe_out, stderr='')
        return run

    def _call(self, unit_active, **kw):
        return hardware.rm3100_responds(
            run=self._run(unit_active, **kw), magusb=self.magusb, dev=self.dev,
            mag_dir=self.d, now=self.now)

    def _probed(self):
        return any(str(self.magusb) in c for c in self.calls)

    def test_active_unit_never_runs_mag_usb_fresh_samples_true(self):
        self._samples()
        self.assertIs(self._call(True), True)
        self.assertFalse(self._probed())

    def test_active_unit_frozen_samples_false(self):
        self._samples(frozen=True)
        self.assertIs(self._call(True), False)
        self.assertFalse(self._probed())
        self.assertIn('frozen', hardware.mag_held_hint(
            run=self._run(True), mag_dir=self.d, now=self.now))

    def test_active_unit_stale_samples_none(self):
        self._samples(age=120.0)
        self.assertIsNone(self._call(True))
        self.assertFalse(self._probed())

    def test_active_unit_no_samples_none(self):
        self.assertIsNone(self._call(True))
        self.assertFalse(self._probed())

    def test_inactive_unit_probes_as_before(self):
        self.assertIs(self._call(False), True)
        self.assertTrue(self._probed())

    def test_fuser_holder_also_skips_probe(self):
        import types
        self._samples()

        def run(argv, **kw):
            self.calls.append(list(argv))
            rc = 0 if argv[0] == 'fuser' else 3
            return types.SimpleNamespace(returncode=rc, stdout='', stderr='')
        r = hardware.rm3100_responds(run=run, magusb=self.magusb, dev=self.dev,
                                     mag_dir=self.d, now=self.now)
        self.assertIs(r, True)
        self.assertFalse(self._probed())

"""`_ensure_radiod_dropins()` — radiod's template drop-ins + companion units.

⛔ AC0G-B4 (rebuilt 2026-09-23) had NO `/etc/systemd/system/radiod@.service.d/`
at all: install.sh's radiod block only fires `if` a `radiod@.service` unit
already exists, and on a fresh image sigmond installs before ka9q-radio.
`_ensure_radiod_dropins()` gives the radiod install path (and `smd doctor
--fix`) a way to lay these down AFTER the fact, idempotently, with no
restart — see docs/superpowers/plans/2026-09-27-radiod-dropins-lockwait-cleanups.md.

Loads bin/smd directly via SourceFileLoader (bin/smd has no .py extension),
following the pattern in tests/test_radiod_core_placement.py.
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_radiod_dropins", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_radiod_dropins", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

# Installed paths, relative to systemd_dir, in the exact order
# _RADIOD_DROPINS declares them.
_DROPIN_RELPATHS = [
    'radiod@.service.d/10-sigmond-restart.conf',
    'radiod@.service.d/40-sigmond-park.conf',
    'radiod@.service.d/45-sigmond-consumers.conf',
    'sigmond-radiod-park@.service',
    'sigmond-radiod-consumers@.service',
]


def _fake_run(calls, unit_installed=True):
    """Stand-in for `_run`: fakes `systemctl cat`/`daemon-reload` (no real
    systemd involved), and passes everything else (mkdir/mv/chmod) through
    to a real subprocess so files actually land under the tmp systemd_dir.
    Records every argv so tests can assert what was (and was not) run."""
    def run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ['systemctl', 'cat']:
            return types.SimpleNamespace(
                returncode=0 if unit_installed else 1, stdout='', stderr='')
        if cmd[:2] == ['systemctl', 'daemon-reload']:
            return types.SimpleNamespace(returncode=0, stdout='', stderr='')
        return subprocess.run(cmd, capture_output=True, text=True)
    return run


class RadiodDropinsWriterTests(unittest.TestCase):
    def setUp(self):
        self.tdir = Path(tempfile.mkdtemp())
        self.src_dir = self.tdir / 'systemd-src'
        self.systemd_dir = self.tdir / 'etc-systemd'
        self.src_dir.mkdir()
        self.systemd_dir.mkdir()
        self.src_names = [
            'radiod-restart-forever.conf',
            'radiod-park.conf',
            'radiod-consumers.conf',
            'sigmond-radiod-park@.service',
            'sigmond-radiod-consumers@.service',
        ]
        for i, name in enumerate(self.src_names):
            (self.src_dir / name).write_bytes(f'content-{i}\n'.encode())

    def test_no_template_writes_nothing_and_returns_empty(self):
        calls = []
        run = _fake_run(calls, unit_installed=False)
        written = smd._ensure_radiod_dropins(
            systemd_dir=self.systemd_dir, src_dir=self.src_dir, run=run)
        self.assertEqual(written, [])
        self.assertEqual(list(self.systemd_dir.rglob('*')), [])
        self.assertFalse(any(c[:2] == ['systemctl', 'daemon-reload'] for c in calls))

    def test_empty_tree_writes_all_five_with_source_bytes(self):
        calls = []
        run = _fake_run(calls, unit_installed=True)
        written = smd._ensure_radiod_dropins(
            systemd_dir=self.systemd_dir, src_dir=self.src_dir, run=run)
        self.assertEqual(len(written), 5)
        for src_name, rel in zip(self.src_names, _DROPIN_RELPATHS):
            dest = self.systemd_dir / rel
            self.assertTrue(dest.exists(), f'{dest} not written')
            self.assertEqual(dest.read_bytes(), (self.src_dir / src_name).read_bytes())
        reloads = [c for c in calls if c[:2] == ['systemctl', 'daemon-reload']]
        self.assertEqual(len(reloads), 1)

    def test_second_call_is_idempotent_no_reload(self):
        smd._ensure_radiod_dropins(systemd_dir=self.systemd_dir, src_dir=self.src_dir,
                                   run=_fake_run([], unit_installed=True))
        calls2 = []
        written2 = smd._ensure_radiod_dropins(
            systemd_dir=self.systemd_dir, src_dir=self.src_dir,
            run=_fake_run(calls2, unit_installed=True))
        self.assertEqual(written2, [])
        self.assertFalse(any(c[:2] == ['systemctl', 'daemon-reload'] for c in calls2))

    def test_one_stale_dropin_is_rewritten_alone(self):
        smd._ensure_radiod_dropins(systemd_dir=self.systemd_dir, src_dir=self.src_dir,
                                   run=_fake_run([], unit_installed=True))
        stale_rel = _DROPIN_RELPATHS[1]
        (self.systemd_dir / stale_rel).write_bytes(b'stale content\n')
        calls = []
        written = smd._ensure_radiod_dropins(
            systemd_dir=self.systemd_dir, src_dir=self.src_dir,
            run=_fake_run(calls, unit_installed=True))
        self.assertEqual(written, [str(self.systemd_dir / stale_rel)])
        self.assertEqual((self.systemd_dir / stale_rel).read_bytes(),
                         (self.src_dir / self.src_names[1]).read_bytes())
        reloads = [c for c in calls if c[:2] == ['systemctl', 'daemon-reload']]
        self.assertEqual(len(reloads), 1)

    def test_never_restarts_or_starts_a_unit(self):
        calls = []
        run = _fake_run(calls, unit_installed=True)
        smd._ensure_radiod_dropins(systemd_dir=self.systemd_dir, src_dir=self.src_dir, run=run)
        (self.systemd_dir / _DROPIN_RELPATHS[0]).write_bytes(b'mutated\n')
        smd._ensure_radiod_dropins(systemd_dir=self.systemd_dir, src_dir=self.src_dir, run=run)
        for c in calls:
            self.assertNotIn('restart', c)
            self.assertNotIn('start', c)


class RadiodDropinsMissingTests(unittest.TestCase):
    def setUp(self):
        self.tdir = Path(tempfile.mkdtemp())
        self.src_dir = self.tdir / 'systemd-src'
        self.systemd_dir = self.tdir / 'etc-systemd'
        self.src_dir.mkdir()
        self.systemd_dir.mkdir()
        self.src_names = [
            'radiod-restart-forever.conf',
            'radiod-park.conf',
            'radiod-consumers.conf',
            'sigmond-radiod-park@.service',
            'sigmond-radiod-consumers@.service',
        ]
        for i, name in enumerate(self.src_names):
            (self.src_dir / name).write_bytes(f'content-{i}\n'.encode())

    def test_no_template_is_empty(self):
        missing = smd._radiod_dropins_missing(
            systemd_dir=self.systemd_dir, src_dir=self.src_dir,
            run=_fake_run([], unit_installed=False))
        self.assertEqual(missing, [])

    def test_lists_exactly_the_missing_or_stale_paths(self):
        missing = smd._radiod_dropins_missing(
            systemd_dir=self.systemd_dir, src_dir=self.src_dir,
            run=_fake_run([], unit_installed=True))
        self.assertEqual(
            sorted(missing),
            sorted(str(self.systemd_dir / r) for r in _DROPIN_RELPATHS))

        smd._ensure_radiod_dropins(systemd_dir=self.systemd_dir, src_dir=self.src_dir,
                                   run=_fake_run([], unit_installed=True))
        stale_rel = _DROPIN_RELPATHS[2]
        (self.systemd_dir / stale_rel).write_bytes(b'oops\n')
        missing2 = smd._radiod_dropins_missing(
            systemd_dir=self.systemd_dir, src_dir=self.src_dir,
            run=_fake_run([], unit_installed=True))
        self.assertEqual(missing2, [str(self.systemd_dir / stale_rel)])

    def test_never_writes_or_calls_daemon_reload(self):
        calls = []
        smd._radiod_dropins_missing(
            systemd_dir=self.systemd_dir, src_dir=self.src_dir,
            run=_fake_run(calls, unit_installed=True))
        self.assertEqual(list(self.systemd_dir.rglob('*')), [])
        self.assertFalse(any(c[:2] == ['systemctl', 'daemon-reload'] for c in calls))


class RadiodDropinsDoctorTests(unittest.TestCase):
    """The `smd doctor` finding + `--fix` wiring (Task 4 greps for the
    finding's `kind`: 'radiod-dropins-missing')."""

    def setUp(self):
        self.tdir = Path(tempfile.mkdtemp())

    def test_finding_appears_when_something_is_missing(self):
        with mock.patch.object(
                smd, '_radiod_dropins_missing',
                return_value=['/etc/systemd/system/radiod@.service.d/10-sigmond-restart.conf']):
            findings = smd._radiod_dropins_findings()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].kind, 'radiod-dropins-missing')
        self.assertTrue(findings[0].fixable)

    def test_finding_absent_when_nothing_missing(self):
        with mock.patch.object(smd, '_radiod_dropins_missing', return_value=[]):
            findings = smd._radiod_dropins_findings()
        self.assertEqual(findings, [])

    def test_doctor_fix_calls_ensure_radiod_dropins(self):
        calls = []
        args = types.SimpleNamespace(base=str(self.tdir), fix=True)
        with mock.patch.object(
                smd, '_ensure_radiod_dropins',
                side_effect=lambda **kw: calls.append(True) or []), \
             mock.patch.object(smd, 'collect_findings', return_value=(True, [])):
            smd.cmd_doctor(args)
        self.assertEqual(calls, [True])


if __name__ == '__main__':
    unittest.main()

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


def _fake_install(cmd):
    """Stand-in for `install -m ... -o root -g root ...`: performs the
    equivalent file/dir operation WITHOUT the real chown, since the test
    process isn't root — the point of the test is the recorded argv (that
    -o/-g root were requested), not that this fake actually changes
    ownership. Handles both the `-d` (directory) and file-copy forms."""
    args = cmd[1:]
    is_dir = '-d' in args
    paths = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ('-m', '-o', '-g'):
            i += 2
            continue
        if a == '-d':
            i += 1
            continue
        paths.append(a)
        i += 1
    if is_dir:
        for p in paths:
            Path(p).mkdir(parents=True, exist_ok=True)
    else:
        src, dest = paths
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(Path(src).read_bytes())
    return types.SimpleNamespace(returncode=0, stdout='', stderr='')


def _fake_run(calls, unit_installed=True):
    """Stand-in for `_run`: fakes `systemctl cat`/`daemon-reload` (no real
    systemd involved) and `install` (see `_fake_install` — no real chown),
    and passes everything else through to a real subprocess so files
    actually land under the tmp systemd_dir. Records every argv so tests
    can assert what was (and was not) run."""
    def run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ['systemctl', 'cat']:
            return types.SimpleNamespace(
                returncode=0 if unit_installed else 1, stdout='', stderr='')
        if cmd[:2] == ['systemctl', 'daemon-reload']:
            return types.SimpleNamespace(returncode=0, stdout='', stderr='')
        if cmd and cmd[0] == 'install':
            return _fake_install(cmd)
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

    def test_writes_land_root_owned_not_the_invoking_user(self):
        """I1: `mv` keeps the temp file's owner, so a plain `sudo mv` +
        `chmod 644` leaves these root-run units owned by whichever user ran
        `smd doctor --fix` (its fix branch only elevates for foreign-owned
        checkout paths). The writer must use `install -o root -g root`
        (dirs via `install -d -m 0755 -o root -g root`) so root — not the
        invoking user — always owns them."""
        calls = []
        run = _fake_run(calls, unit_installed=True)
        smd._ensure_radiod_dropins(systemd_dir=self.systemd_dir, src_dir=self.src_dir, run=run)

        install_dir_calls = [c for c in calls if c[:2] == ['install', '-d']]
        self.assertTrue(install_dir_calls, 'expected an `install -d ...` dir-creation call')
        for c in install_dir_calls:
            self.assertIn('-o', c)
            self.assertEqual(c[c.index('-o') + 1], 'root')
            self.assertIn('-g', c)
            self.assertEqual(c[c.index('-g') + 1], 'root')
            self.assertIn('-m', c)
            self.assertEqual(c[c.index('-m') + 1], '0755')

        install_file_calls = [
            c for c in calls
            if c[:1] == ['install'] and '-d' not in c
        ]
        self.assertEqual(len(install_file_calls), 5)
        for c in install_file_calls:
            self.assertIn('-o', c)
            self.assertEqual(c[c.index('-o') + 1], 'root')
            self.assertIn('-g', c)
            self.assertEqual(c[c.index('-g') + 1], 'root')
            self.assertIn('-m', c)
            self.assertEqual(c[c.index('-m') + 1], '0644')

    def test_temp_file_removed_after_install(self):
        """The staged tempfile in /tmp must not be left behind once
        `install` has copied it to its destination."""
        before = set(Path(tempfile.gettempdir()).iterdir())
        smd._ensure_radiod_dropins(
            systemd_dir=self.systemd_dir, src_dir=self.src_dir,
            run=_fake_run([], unit_installed=True))
        after = set(Path(tempfile.gettempdir()).iterdir())
        self.assertEqual(before, after, f'leaked temp file(s): {after - before}')


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


class InstallRadiodNativeOrderTests(unittest.TestCase):
    """I2: on a fresh install, `_ensure_radiod_running()` used to run
    BEFORE `_ensure_radiod_dropins()`, so the first radiod run happened
    with none of 10-sigmond-restart.conf (patient retry), park, or
    consumers in place — with the RX888 absent it burns 5 restarts and
    lands failed. `_ensure_radiod_dropins()` must run first; `make
    install` has already written radiod@.service by the time either is
    called."""

    def test_dropins_written_before_radiod_started(self):
        calls = []

        with mock.patch.object(smd, 'get_entry', return_value=mock.Mock()), \
             mock.patch.object(smd, '_install_radiod_deps', return_value=True), \
             mock.patch.object(smd, '_ensure_radio_user', return_value=True), \
             mock.patch.object(smd, '_ensure_fftw_dir', return_value=True), \
             mock.patch.object(smd, '_ka9q_radio_pin', return_value=None), \
             mock.patch.object(smd, '_clone_repo', return_value=Path('/tmp/fake-ka9q-radio')), \
             mock.patch.object(smd, '_build_ka9q_radio', return_value=True), \
             mock.patch.object(smd, '_reload_udev_for_sdrs'), \
             mock.patch.object(smd, '_install_wisdom_service'), \
             mock.patch.object(
                 smd, '_ensure_radiod_running',
                 side_effect=lambda: calls.append('running')), \
             mock.patch.object(
                 smd, '_ensure_radiod_dropins',
                 side_effect=lambda: calls.append('dropins') or []):
            ok = smd._install_radiod_native(catalog={})

        self.assertTrue(ok)
        self.assertEqual(calls, ['dropins', 'running'])


class EnsureRadiodRunningBareTemplateTests(unittest.TestCase):
    """M4: the `radiod@*.service.d` glob also matches the bare template
    dir `radiod@.service.d` (`*` matches zero characters), which would try
    `systemctl enable --now radiod@.service` — not a real instance. Must
    be skipped, mirroring the `'@.' in unit_name` guard the CPU-affinity
    cleanup already uses."""

    def test_bare_template_instance_never_queried_or_started(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if cmd[:2] == ['systemctl', 'list-units']:
                return types.SimpleNamespace(returncode=0, stdout='', stderr='')
            if cmd[:2] == ['systemctl', 'is-active']:
                return types.SimpleNamespace(returncode=0, stdout='active\n', stderr='')
            return types.SimpleNamespace(returncode=0, stdout='', stderr='')

        with mock.patch.object(smd, '_run', side_effect=fake_run), \
             mock.patch('glob.glob', return_value=[
                 '/etc/systemd/system/radiod@.service.d',
                 '/etc/systemd/system/radiod@foo.service.d',
             ]):
            smd._ensure_radiod_running()

        for c in calls:
            self.assertNotIn('radiod@.service', c)
        self.assertTrue(any('radiod@foo.service' in c for c in calls),
                        'the real instance radiod@foo.service was never queried')


if __name__ == '__main__':
    unittest.main()

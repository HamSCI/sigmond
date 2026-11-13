"""apply_systemd_enables starts a timer it enables (KJ6MKI, 2026-11-12:
grape-daily.timer stayed linked and inactive), and a successful
`smd config init` runs it -- never install, which runs before the client's
config or service user exists (v3.91 nested test, 2026-11-13)."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond import installer
from sigmond.commands import client_config


def _fake(states):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        verb = argv[1]
        if verb == 'is-enabled':
            out = states[argv[2]]
        elif verb == 'cat':
            out = '[Install]\nWantedBy=timers.target\n'
        else:
            out = ''
        return subprocess.CompletedProcess(argv, 0, out, '')
    return run, calls


class TimersStart(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.repo = Path(self.td.name)
        (self.repo / 'deploy.toml').write_text(
            '[systemd]\nunits = ["x-fusion.service", "grape-daily.timer", '
            '"y@.service", "done.timer"]\n')

    def test_an_enabled_timer_is_started_and_a_service_is_not(self):
        run, calls = _fake({'x-fusion.service': 'linked',
                            'grape-daily.timer': 'linked',
                            'done.timer': 'enabled'})
        with mock.patch.object(installer.subprocess, 'run', run):
            msgs = installer.apply_systemd_enables(self.repo)
        self.assertIn(['systemctl', 'start', 'grape-daily.timer'], calls)
        self.assertNotIn(['systemctl', 'start', 'x-fusion.service'], calls)
        self.assertNotIn(['systemctl', 'start', 'done.timer'], calls)
        self.assertIn('  started grape-daily.timer', msgs)

    def test_start_timers_false_only_enables(self):
        run, calls = _fake({'x-fusion.service': 'enabled',
                            'grape-daily.timer': 'linked',
                            'done.timer': 'enabled'})
        with mock.patch.object(installer.subprocess, 'run', run):
            installer.apply_systemd_enables(self.repo, start_timers=False)
        self.assertIn(['systemctl', 'enable', 'grape-daily.timer'], calls)
        self.assertFalse(any(c[1] == 'start' for c in calls))

    def test_dry_run_starts_nothing(self):
        run, calls = _fake({'x-fusion.service': 'enabled',
                            'grape-daily.timer': 'linked',
                            'done.timer': 'enabled'})
        with mock.patch.object(installer.subprocess, 'run', run):
            installer.apply_systemd_enables(self.repo, dry_run=True)
        self.assertFalse(any(c[1] in ('enable', 'start') for c in calls))


    def test_elevate_wraps_only_the_mutating_calls(self):
        run, calls = _fake_any({'x-fusion.service': 'enabled',
                                'grape-daily.timer': 'linked',
                                'done.timer': 'enabled'})
        with mock.patch.object(installer.subprocess, 'run', run):
            installer.apply_systemd_enables(
                self.repo, elevate=lambda a: ['sudo', '--', *a])
        self.assertIn(['sudo', '--', 'systemctl', 'enable', 'grape-daily.timer'], calls)
        self.assertIn(['sudo', '--', 'systemctl', 'start', 'grape-daily.timer'], calls)
        self.assertIn(['systemctl', 'is-enabled', 'grape-daily.timer'], calls)


def _fake_any(states):
    """Like _fake, but finds the systemctl verb behind a sudo prefix."""
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        i = argv.index('systemctl')
        verb = argv[i + 1]
        if verb == 'is-enabled':
            out = states[argv[i + 2]]
        elif verb == 'cat':
            out = '[Install]\nWantedBy=timers.target\n'
        else:
            out = ''
        return subprocess.CompletedProcess(argv, 0, out, '')
    return run, calls


class EnableAtConfigInitNotInstall(unittest.TestCase):

    def test_install_client_enables_nothing(self):
        src = Path(installer.__file__).read_text()
        body = src.split('def install_client', 1)[1].split('\ndef ', 1)[0]
        self.assertNotIn('apply_systemd_enables(', body)

    def _init(self, rc):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        repo = Path(td.name)
        exe = repo / 'setup.sh'
        exe.write_text(f'#!/bin/sh\nexit {rc}\n')
        exe.chmod(0o755)
        deploy = repo / 'deploy.toml'
        deploy.write_text('[systemd]\nunits = ["grape-daily.timer"]\n')
        with mock.patch.object(client_config, '_build_env_bag', return_value={}), \
             mock.patch.object(client_config, '_maybe_elevate', side_effect=lambda a, e=None: a), \
             mock.patch.object(client_config, '_enable_configured_units') as en:
            got = client_config._run_client_entrypoint(
                'hamsci-physics', deploy, 'setup.sh', 'init')
        return got, en, repo

    def test_successful_init_enables_the_clients_units(self):
        got, en, repo = self._init(0)
        self.assertEqual(got, 0)
        en.assert_called_once_with(repo)

    def test_failed_init_enables_nothing(self):
        got, en, _ = self._init(3)
        self.assertEqual(got, 3)
        en.assert_not_called()

    def test_enable_goes_through_the_sudo_wrapper(self):
        with mock.patch('sigmond.installer.apply_systemd_enables',
                        return_value=['  started grape-daily.timer']) as ase:
            client_config._enable_configured_units(Path('/nonexistent'))
        self.assertIs(ase.call_args.kwargs['elevate'], client_config._maybe_elevate)


if __name__ == '__main__':
    unittest.main()

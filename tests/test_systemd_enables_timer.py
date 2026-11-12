"""apply_systemd_enables starts a timer it enables, and install_client runs
it (KJ6MKI, 2026-11-12: grape-daily.timer stayed linked and inactive)."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond import installer


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


if __name__ == '__main__':
    unittest.main()

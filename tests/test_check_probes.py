"""`smd check` probes: systemd state and age, files through a sudo
fallback, the journal (tasks/spec-smd-check-v372.md §4, §4.2)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from sigmond.commands import check


def _cp(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class FakeRun:
    """Answers each argv by its first words; records every call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        for prefix, result in self.answers:
            if list(argv[:len(prefix)]) == list(prefix):
                return result
        return _cp(returncode=1)


SHOW_ACTIVE = ('Id=psk-recorder@AC0G\\x3dB4.service\nActiveState=active\nLoadState=loaded\n'
               'UnitFileState=enabled\nActiveEnterTimestampMonotonic=100000000\n\n'
               'Id=psk-recorder@AC0G\\x3dB5.service\nActiveState=active\nLoadState=loaded\n'
               'UnitFileState=enabled\nActiveEnterTimestampMonotonic=400000000\n')


class ServiceStateTests(unittest.TestCase):
    def _state(self, listing, show, pattern='psk-recorder@*.service', mono=1000.0):
        run = FakeRun([(['systemctl', 'list-units'], _cp(listing)),
                       (['systemctl', 'show'], _cp(show))])
        return check.service_state(pattern, run=run, monotonic=lambda: mono), run

    def test_age_comes_from_the_youngest_active_instance(self):
        listing = ('psk-recorder@AC0G\\x3dB4.service loaded active running psk\n'
                   'psk-recorder@AC0G\\x3dB5.service loaded active running psk\n')
        svc, run = self._state(listing, SHOW_ACTIVE)
        self.assertEqual(svc, check.Service('active', 600.0, True))  # the younger instance
        self.assertIn('ActiveEnterTimestampMonotonic', run.calls[1])

    def test_one_failed_instance_fails_the_glob(self):
        show = (SHOW_ACTIVE.split('\n\n')[0] + '\n\n' +
                'Id=psk-recorder@AC0G\\x3dB5.service\nActiveState=failed\nLoadState=loaded\n'
                'UnitFileState=enabled\nActiveEnterTimestampMonotonic=0\n')
        svc, _ = self._state('psk-recorder@a.service loaded active running x\n', show)
        self.assertEqual(svc.state, 'failed')

    def test_auto_restart_reads_failed_and_restarting(self):
        show = ('Id=x.service\nActiveState=activating\nSubState=auto-restart\nLoadState=loaded\n'
                'UnitFileState=enabled\nInactiveExitTimestampMonotonic=990000000\n')
        svc, _ = self._state('x.service loaded activating auto-restart x\n', show, pattern='x.service')
        self.assertEqual((svc.state, svc.restarting), ('failed', True))
        line = check.service_problem('PSK', svc, 'x')
        self.assertIn('keeps restarting', line.text)

    def test_activating_age_comes_from_inactive_exit(self):
        show = ('Id=x.service\nActiveState=activating\nSubState=start\nLoadState=loaded\n'
                'UnitFileState=enabled\nInactiveExitTimestampMonotonic=400000000\n')
        svc, _ = self._state('x.service loaded activating start x\n', show, pattern='x.service')
        self.assertEqual((svc.state, svc.age_s), ('activating', 600.0))
        self.assertFalse(check.is_young(svc, 60))

    def test_no_unit_means_absent(self):
        svc, _ = self._state('', '')
        self.assertEqual(svc.state, 'absent')

    def test_a_failed_unit_with_no_active_one_reads_failed(self):
        show = ('Id=mag-recorder.service\nActiveState=failed\nLoadState=loaded\n'
                'UnitFileState=enabled\nActiveEnterTimestampMonotonic=0\n')
        svc, _ = self._state('mag-recorder.service loaded failed failed mag\n', show,
                             pattern='mag-recorder.service')
        self.assertEqual(svc, check.Service('failed', None, True))

    def test_a_single_unit_not_loaded_is_shown_by_name(self):
        # list-units --all omits an inactive unit systemd has not loaded; show still knows it.
        show = ('Id=mag-recorder.service\nActiveState=inactive\nLoadState=loaded\n'
                'UnitFileState=disabled\nActiveEnterTimestampMonotonic=0\n')
        svc, run = self._state('', show, pattern='mag-recorder.service')
        self.assertEqual(svc, check.Service('inactive', None, False))
        self.assertEqual(run.calls[1][-1], 'mag-recorder.service')

    def test_a_unit_systemd_does_not_know_reads_absent(self):
        show = 'Id=mag-recorder.service\nActiveState=inactive\nLoadState=not-found\n'
        svc, _ = self._state('', show, pattern='mag-recorder.service')
        self.assertEqual(svc.state, 'absent')


class YoungTests(unittest.TestCase):
    def test_young_inside_the_window_only(self):
        self.assertTrue(check.is_young(check.Service('active', 59.0), 60))
        self.assertFalse(check.is_young(check.Service('active', 60.0), 60))

    def test_unknown_age_is_never_young_and_a_recent_start_is(self):
        self.assertFalse(check.is_young(check.Service('activating', None), 60))
        self.assertTrue(check.is_young(check.Service('activating', 5.0), 60))
        self.assertFalse(check.is_young(check.Service('active', None), 60))

    def test_activating_past_its_window_is_not_young(self):
        self.assertFalse(check.is_young(check.Service('activating', 61.0), 60))
        self.assertTrue(check.is_young(check.Service('activating', 5.0), 60))

    def test_a_stopped_service_fails_with_a_fix(self):
        line = check.service_problem('PSK', check.Service('failed', None), 'psk-recorder')
        self.assertEqual(line, check.Line(check.FAIL, 'PSK',
                                          'psk-recorder is failed.  Fix: smd start psk-recorder'))
        self.assertIsNone(check.service_problem('PSK', check.Service('active', 5.0), 'psk-recorder'))


@unittest.skipIf(os.geteuid() == 0, 'root reads every file; the refusal cannot happen')
class ReaderTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)

    def test_tail_returns_the_last_bytes_and_none_for_a_missing_file(self):
        p = self.dir / 'a.log'
        p.write_text('one\ntwo\nthree\n')
        r = check.Reader()
        self.assertEqual(r.tail(p, 6), 'three\n')
        self.assertIsNone(r.tail(self.dir / 'missing.log', 6))

    def test_a_refused_file_goes_through_sudo_once(self):
        p = self.dir / 'secret.log'
        p.write_text('x\n')
        p.chmod(0)
        self.addCleanup(p.chmod, 0o600)
        run = FakeRun([(['sudo', '-n', 'tail'], _cp('via sudo\n'))])
        self.assertEqual(check.Reader(run=run).tail(p, 100), 'via sudo\n')
        self.assertEqual(run.calls, [['sudo', '-n', 'tail', '-c', '100', str(p)]])

    def test_a_refusal_sudo_also_refuses_raises_unreadable(self):
        p = self.dir / 'secret.log'
        p.write_text('x\n')
        p.chmod(0)
        self.addCleanup(p.chmod, 0o600)
        run = FakeRun([(['sudo'], _cp(returncode=1, stderr='a password is required'))])
        with self.assertRaisesRegex(check.Unreadable, r'cannot read .*secret\.log as '):
            check.Reader(run=run).tail(p, 100)

    def test_newest_mtime_sees_files_only(self):
        (self.dir / 'old.bin').write_text('x')
        (self.dir / 'new.bin').write_text('x')
        (self.dir / 'sub').mkdir()
        os.utime(self.dir / 'old.bin', (1000, 1000))
        os.utime(self.dir / 'new.bin', (2000, 2000))
        os.utime(self.dir / 'sub', (9000, 9000))
        r = check.Reader()
        self.assertEqual(r.newest_mtime(self.dir), 2000)
        self.assertIsNone(r.newest_mtime(self.dir / 'absent'))
        self.assertEqual(r.listdir(self.dir), ['new.bin', 'old.bin', 'sub'])


class JournalTests(unittest.TestCase):
    def test_a_journal_group_member_reads_directly(self):
        run = FakeRun([(['journalctl'], _cp('line one\nline two\n'))])
        out = check.read_journal(['wspr-recorder@*.service'], 10, run=run,
                                 euid=lambda: 1000, groups=lambda: {'adm'})
        self.assertEqual(out, ['line one', 'line two'])
        self.assertEqual(run.calls[0], ['journalctl', '--no-pager', '-q', '-o', 'cat',
                                        '--since', '10 min ago', '-u', 'wspr-recorder@*.service'])

    def test_anyone_else_asks_sudo_first(self):
        # journalctl shows such a user only their own messages, and says nothing.
        run = FakeRun([(['sudo', '-n', 'journalctl'], _cp('from sudo\n'))])
        out = check.read_journal(['x.service'], 5, run=run, euid=lambda: 1000, groups=set)
        self.assertEqual(out, ['from sudo'])

    def test_journalctl_runs_with_the_c_locale(self):
        seen = {}

        def run(argv, **kw):
            seen.update(kw)
            return _cp('')
        check.read_journal(['x.service'], 5, run=run, euid=lambda: 0)
        self.assertEqual(seen['env']['LC_ALL'], 'C')

    def test_primary_group_alone_counts(self):
        import grp
        from unittest import mock
        with mock.patch.object(os, 'getgroups', return_value=[]), \
             mock.patch.object(os, 'getgid', return_value=0), \
             mock.patch.object(grp, 'getgrgid', return_value=mock.Mock(gr_name='adm')):
            self.assertIn('adm', check._my_groups())

    def test_journal_refused_raises_unreadable(self):
        run = FakeRun([(['sudo'], _cp(returncode=1, stderr='sudo: a password is required'))])
        with self.assertRaisesRegex(check.Unreadable, r'^cannot read the journal as '):
            check.read_journal(['x.service'], 5, run=run, euid=lambda: 1000, groups=set)

    def test_a_glob_that_never_logged_is_an_empty_journal(self):
        run = FakeRun([(['journalctl'], _cp(returncode=1, stderr='Failed to add filter for units: No data available'))])
        self.assertEqual(check.read_journal(['x@*.service'], 5, run=run, euid=lambda: 0), [])


if __name__ == '__main__':
    unittest.main()

"""`smd check` while the first-run bring-up runs (final review I3): a first
line says bring-up still runs, and a client it has not started yet waits
instead of failing with a Fix."""
import tempfile
import unittest
from pathlib import Path

from sigmond.commands import check
from sigmond.commands.check import FAIL, OK, WAIT, Line

from tests.check_fakes import make_env, svc

LOG = '/var/log/sigmond/firstrun-bringup.log'


class BringupLineTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.marker = Path(d.name) / '.firstrun-bringup-done'

    def line(self, unit):
        return check.bringup_line(make_env(bringup=lambda: unit), marker=self.marker)

    def test_a_running_bringup_waits_and_says_since_when(self):
        self.assertEqual(self.line(svc(state='activating', age_s=7 * 60)),
                         Line(WAIT, 'bring-up', 'still running (started 7 min ago)'))

    def test_a_failed_unit_fails_and_names_its_log(self):
        self.assertEqual(self.line(svc(state='failed', age_s=None)),
                         Line(FAIL, 'bring-up', f'failed; its log is {LOG}.  '
                                                f'Tell the fleet admin.'))

    def test_a_marker_that_says_retry_or_gave_up_fails(self):
        # The unit exits 0 and lists 1 as success, so after a reboot only the
        # marker remembers that bring-up failed.
        self.marker.write_text('attempts=1\nresult=retry\n2026-10-14T12:00:00+00:00\n')
        out = self.line(svc(state='inactive', age_s=None))
        self.assertEqual(out, Line(FAIL, 'bring-up', f'failed (attempt 1 of 3; it tries again at '
                                                     f'the next boot); its log is {LOG}.  '
                                                     f'Tell the fleet admin.'))
        self.marker.write_text('attempts=3\nresult=gave-up\n2026-10-14T12:00:00+00:00\n')
        out = self.line(svc(state='inactive', age_s=None))
        self.assertEqual(out, Line(FAIL, 'bring-up', f'failed 3 times and stopped trying; its log '
                                                     f'is {LOG}.  Tell the fleet admin.'))

    def test_a_finished_bringup_prints_nothing(self):
        self.marker.write_text('attempts=1\nresult=ok\n2026-10-14T12:00:00+00:00\n')
        self.assertIsNone(self.line(svc(state='inactive', age_s=None)))
        self.marker.unlink()
        self.assertIsNone(self.line(svc(state='inactive', age_s=None)))

    def test_an_image_without_the_unit_prints_nothing(self):
        self.marker.write_text('attempts=3\nresult=gave-up\n')
        self.assertIsNone(self.line(svc(state='absent', age_s=None)))
        self.assertIsNone(check.bringup_line(make_env(), marker=self.marker))

    def test_bringup_is_the_first_line(self):
        self.assertEqual(check.CHECKS[0][0], 'bring-up')


class ClientsDuringBringupTests(unittest.TestCase):
    RUNNING = staticmethod(lambda: svc(state='activating', age_s=120))

    def test_a_client_not_started_yet_waits_without_a_fix(self):
        env = make_env(bringup=self.RUNNING, service=lambda p: svc(state='inactive', age_s=None))
        out = check.wspr_line(env)
        self.assertEqual(out, Line(WAIT, 'WSPR', 'wspr-recorder is inactive; bring-up has not '
                                                 'started it yet'))
        self.assertNotIn('Fix:', out.text)

    def test_every_service_line_waits_while_bringup_runs(self):
        with tempfile.TemporaryDirectory() as d:
            env = make_env(bringup=self.RUNNING, service=lambda p: svc(state='failed', age_s=None))
            for fn in (lambda e: check.psk_line(e, log_dir=Path(d)), check.wspr_line,
                       check.meteor_line):
                self.assertEqual(fn(env).mark, WAIT)

    def test_the_same_client_fails_with_a_fix_once_bringup_is_done(self):
        env = make_env(bringup=lambda: svc(state='inactive', age_s=None),
                       service=lambda p: svc(state='inactive', age_s=None))
        self.assertEqual(check.wspr_line(env),
                         Line(FAIL, 'WSPR', 'wspr-recorder is inactive.  Fix: smd start wspr-recorder'))

    def test_a_running_client_is_unaffected(self):
        env = make_env(bringup=self.RUNNING,
                       journal=lambda u, m: ['cycle UTC 12:00 rx=B4 B4 3 spots in wspr.spots (2 bands, x)'])
        self.assertEqual(check.wspr_line(env).mark, OK)


if __name__ == '__main__':
    unittest.main()

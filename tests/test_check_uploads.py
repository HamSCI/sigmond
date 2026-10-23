"""`smd check` uploads line: the site sink switch and hs-uploader's backlog
(tasks/spec-smd-check-v372.md §4)."""
import subprocess
import unittest
from datetime import datetime, timezone
from unittest import mock

from sigmond.commands import check
from sigmond.commands.check import FAIL, OK, WAIT
from sigmond.coordination import Coordination, Uploads

from tests.check_fakes import NOW, make_env

NEW = 'new station: the site sink switch stays off until someone runs smd sink upload'


def iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def coord(mode='upload', reason=''):
    return Coordination(uploads=Uploads(mode=mode, reason=reason))


def backlog(ack_age_s=60, dead=0, heartbeat_age_s=None):
    cursors = [{'source_id': 'psk', 'dest_id': 'pskreporter', 'table_name': 'psk.spots',
                'last_ack': iso(NOW - ack_age_s), 'cursor_len': 8}] if ack_age_s is not None else []
    if heartbeat_age_s is not None:
        cursors.append({'source_id': 'hb', 'dest_id': 'gw', 'table_name': 'station.heartbeat',
                        'last_ack': iso(NOW - heartbeat_age_s), 'cursor_len': 8})
    return {'readable': True, 'cursors': cursors,
            'pipelines': [{'name': 'psk-pskreporter', 'deliverable_count': 0, 'dead_letter_count': dead,
                           'dead_letter_recent_count': dead, 'dead_letter_newest': None}]}


def line(c=None, b=None, age=None):
    env = make_env(coordination=lambda: c or coord(), backlog=lambda: b or backlog(),
                   coordination_age_s=lambda: age)
    return check.uploads_line(env)


class UploadsTests(unittest.TestCase):
    def test_new_station_off_waits_and_names_the_command(self):
        self.assertEqual(line(coord('discard', NEW)),
                         check.Line(WAIT, 'uploads', 'off, new station: check the lines above, '
                                                     'then run: smd sink upload'))

    def test_off_for_any_other_reason_fails(self):
        out = line(coord('discard', 'bench provisioning for DASI-021'))
        self.assertEqual(out, check.Line(FAIL, 'uploads', 'off (bench provisioning for DASI-021).  '
                                                          'Ask the fleet admin.'))

    def test_hold_fails(self):
        self.assertEqual(line(coord('hold', 'antenna work')).text, 'hold (antenna work).  Ask the fleet admin.')

    def test_upload_with_a_recent_delivery_passes(self):
        self.assertEqual(line(), check.Line(OK, 'uploads', 'on; last delivery 1 min ago, no failure in 24 h'))

    def test_a_recent_dead_letter_fails(self):
        out = line(b=backlog(dead=3))
        self.assertEqual(out, check.Line(FAIL, 'uploads', '3 uploads failed for good in the last 24 h '
                                                          '(psk-pskreporter).  Tell the fleet admin.'))

    def test_no_delivery_in_30_minutes_fails(self):
        self.assertEqual(line(b=backlog(ack_age_s=31 * 60)).mark, FAIL)
        self.assertEqual(line(b=backlog(ack_age_s=30 * 60)).mark, OK)

    def test_the_heartbeat_alone_proves_nothing(self):
        out = line(b=backlog(ack_age_s=None, heartbeat_age_s=60))
        self.assertEqual(out, check.Line(FAIL, 'uploads', 'on, but nothing delivered in 30 min.  '
                                                          'Tell the fleet admin.'))

    def test_a_switch_turned_on_minutes_ago_waits(self):
        out = line(b=backlog(ack_age_s=None), age=5 * 60)
        self.assertEqual(out, check.Line(WAIT, 'uploads', 'on since 5 min ago; the first delivery '
                                                          'comes within 30 min'))

    def test_discard_era_acks_do_not_count_as_deliveries(self):
        # hs-uploader's DiscardTransport acks against the real cursor while the
        # switch is off, so an ack from before the switch turned on proves nothing.
        out = line(b=backlog(ack_age_s=2 * 60), age=60)
        self.assertEqual(out, check.Line(WAIT, 'uploads', 'on since 1 min ago; the first delivery '
                                                          'comes within 30 min'))

    def test_an_ack_after_the_switch_turned_on_passes(self):
        out = line(b=backlog(ack_age_s=60), age=2 * 60)
        self.assertEqual(out, check.Line(OK, 'uploads', 'on; last delivery 1 min ago, no failure in 24 h'))

    def test_discard_era_acks_alone_fail_once_the_wait_is_over(self):
        self.assertEqual(line(b=backlog(ack_age_s=40 * 60), age=31 * 60).mark, FAIL)

    def test_an_unreadable_backlog_fails_with_its_reason(self):
        out = line(b={'readable': False, 'reason': 'OperationalError: unable to open database file'})
        self.assertEqual(out.text, 'cannot read the upload backlog: OperationalError: unable to open '
                                   'database file')


class ReadBacklogTests(unittest.TestCase):
    def test_asks_sudo_once_when_the_store_refuses_this_user(self):
        refused = {'readable': False, 'reason': 'OperationalError: unable to open database file'}
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout='{"readable": true, "pipelines": [], "cursors": []}')
        with mock.patch('sigmond.heartbeat._read_backlog', return_value=refused), \
                mock.patch('os.path.exists', return_value=True), \
                mock.patch('os.access', return_value=False):
            out = check.read_backlog(run=run, euid=lambda: 1000)
        self.assertEqual(out, {'readable': True, 'pipelines': [], 'cursors': []})
        self.assertEqual(calls[0][:3], ['sudo', '-n', 'env'])
        self.assertIn('hs_uploader.backlog', calls[0])

    def test_a_reader_failure_becomes_unreadable_not_an_exception(self):
        from sigmond.heartbeat import ReaderUnavailable
        with mock.patch('sigmond.heartbeat._read_backlog', side_effect=ReaderUnavailable('timed out')):
            self.assertEqual(check.read_backlog(), {'readable': False, 'reason': 'timed out'})

    def _sudo_call(self):
        refused = {'readable': False, 'reason': 'OperationalError: unable to open database file'}
        seen = []

        def run(argv, **kw):
            seen.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, stdout='{"readable": true}')
        with mock.patch('sigmond.heartbeat._read_backlog', return_value=refused), \
                mock.patch('os.path.exists', return_value=True), \
                mock.patch('os.access', return_value=False):
            check.read_backlog(run=run, euid=lambda: 1000)
        return seen[0]

    def test_the_root_reader_ignores_the_current_directory(self):
        argv, kw = self._sudo_call()
        self.assertLess(argv.index('-P'), argv.index('-m'))
        self.assertEqual(kw.get('cwd'), '/')

    def test_a_decoy_module_in_the_current_directory_does_not_run(self):
        import os
        import sys
        import tempfile
        argv, _ = self._sudo_call()
        argv = argv[argv.index(sys.executable):]          # drop sudo -n env PYTHONPATH=...
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'hs_uploader'))
            for name, body in (('__init__.py', ''), ('backlog.py', 'print("DECOY RAN")\n')):
                with open(os.path.join(d, 'hs_uploader', name), 'w') as f:
                    f.write(body)
            r = subprocess.run(argv[:-1] + ['/nonexistent'], cwd='/', capture_output=True,
                               text=True, env={'PYTHONPATH': d + '/empty'})
            self.assertNotIn('DECOY RAN', r.stdout)
            r = subprocess.run(argv[:-1] + ['/nonexistent'], cwd=d, capture_output=True,
                               text=True, env={'PYTHONPATH': d + '/empty'})
            self.assertNotIn('DECOY RAN', r.stdout)


if __name__ == '__main__':
    unittest.main()


class SwitchAgeTests(unittest.TestCase):
    """Spec §7.5: once the carry-over marker stands, uploads_line dates the
    last change of the site sink switch by site.toml's ``set_at``, not by
    coordination.toml's mtime (a mirror repair or a hand edit moves that)."""

    def setUp(self):
        import os
        import tempfile
        from pathlib import Path
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        self.marker = root / 'carry-over-v1.done'
        self.site = root / 'site.toml'
        self.coord = root / 'coordination.toml'
        self.coord.write_text('[uploads]\nenabled = true\n')
        os.utime(self.coord, (NOW - 2 * 86400, NOW - 2 * 86400))

    def age(self):
        return check._switch_age_s(now=NOW, marker=self.marker, site=self.site,
                                   coordination=self.coord)

    def write_site(self, set_at):
        self.site.write_text(f'state = "upload"\nreason = "x"\nset_by = "smd sink (opal)"\n'
                             f'set_at = "{set_at}"\n')

    def test_with_the_marker_set_at_dates_the_change(self):
        self.marker.write_text('')
        self.write_site(datetime.fromtimestamp(NOW - 300, tz=timezone.utc)
                        .strftime('%Y-%m-%dT%H:%M:%SZ'))
        self.assertEqual(self.age(), 300)

    def test_without_the_marker_coordination_toml_dates_it(self):
        self.write_site(datetime.fromtimestamp(NOW - 300, tz=timezone.utc)
                        .strftime('%Y-%m-%dT%H:%M:%SZ'))
        self.assertEqual(self.age(), 2 * 86400)

    def test_with_the_marker_an_unreadable_set_at_dates_nothing(self):
        self.marker.write_text('')
        self.assertIsNone(self.age())                 # no site.toml
        self.site.write_text('state = "upload"\n')    # no set_at
        self.assertIsNone(self.age())
        self.write_site('yesterday')
        self.assertIsNone(self.age())

    def test_the_uploads_line_waits_on_a_switch_raised_minutes_ago(self):
        # The station's real chain: default_env wires _switch_age_s.
        self.assertIs(check.default_env().coordination_age_s, check._switch_age_s)
        self.marker.write_text('')
        self.write_site(datetime.fromtimestamp(NOW - 300, tz=timezone.utc)
                        .strftime('%Y-%m-%dT%H:%M:%SZ'))
        env = make_env(coordination=lambda: coord(), backlog=lambda: backlog(ack_age_s=None),
                       coordination_age_s=self.age)
        self.assertEqual(check.uploads_line(env),
                         check.Line(WAIT, 'uploads', 'on since 5 min ago; the first delivery '
                                                     'comes within 30 min'))

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


if __name__ == '__main__':
    unittest.main()

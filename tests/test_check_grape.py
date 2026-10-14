"""`smd check` GRAPE and storage lines (tasks/spec-smd-check-v372.md §4).

GRAPE reads the channel list from hf-timestd's config and the newest file
under raw_buffer/<channel>/<YYYYMMDD>/, in the RAM tier and on disk."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sigmond.commands import check
from sigmond.commands.check import ABSENT, FAIL, OK, WAIT

from tests.check_fakes import NOW, make_env, svc

SIX = ['SHARED_2500', 'SHARED_5000', 'SHARED_10000', 'SHARED_15000', 'WWV_20000', 'WWV_25000']


def config(root, tiered=True, channels=SIX, engine='radiod', archive=True):
    chans = [{'frequency_hz': int(c.split('_')[1]) * 1000, 'description': c} for c in channels]
    return {'ka9q': {'source': engine},
            'recorder': {'production_data_root': str(root / 'var'),
                         'tiered_storage': tiered, 'hot_buffer_root': str(root / 'shm'),
                         'channel_group': {'timestd': {'archive': archive, 'channels': chans}}}}


class GrapeChannelTests(unittest.TestCase):
    def test_the_shipped_template_gives_six_channels(self):
        self.assertEqual(check.grape_channels(config(Path('/x'))), SIX)

    def test_a_channel_with_archive_off_is_not_expected(self):
        cfg = config(Path('/x'))
        cfg['recorder']['channel_group']['timestd']['channels'][0]['archive'] = False
        self.assertEqual(check.grape_channels(cfg), SIX[1:])

    def test_phase_engine_splits_shared_channels(self):
        cfg = config(Path('/x'), engine='phase-engine', channels=['SHARED_10000', 'WWV_20000'])
        self.assertEqual(check.grape_channels(cfg),
                         ['WWV_10000', 'WWVH_10000', 'BPM_10000', 'WWV_20000'])

    def test_roots_follow_tiered_storage(self):
        root = Path('/r')
        self.assertEqual(check.grape_roots(config(root)),
                         [root / 'shm' / 'raw_buffer', root / 'var' / 'raw_buffer'])
        self.assertEqual(check.grape_roots(config(root, tiered=False)),
                         [root / 'var' / 'raw_buffer', root / 'var'])


class GrapeLineTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        self.cfg = config(self.root)

    def put(self, channel, age_s, tier='shm', now=NOW):
        t = now - age_s
        day = datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y%m%d')
        d = self.root / tier / 'raw_buffer' / channel / day
        d.mkdir(parents=True, exist_ok=True)
        f = d / f'{int(t)}.bin.zst'
        f.write_text('x')
        os.utime(f, (t, t))

    def line(self, now=NOW, **kw):
        return check.grape_line(make_env(now=now, timestd_config=lambda: self.cfg, **kw))

    def test_every_channel_fresh_passes(self):
        for c in SIX:
            self.put(c, 300)
        self.assertEqual(self.line(), check.Line(OK, 'GRAPE', 'all 6 channels wrote a file in the last 11 min'))

    def test_a_file_already_moved_to_disk_counts(self):
        for c in SIX:
            self.put(c, 300, tier='var')
        self.assertEqual(self.line().mark, OK)

    def test_a_stale_channel_fails_by_name(self):
        for c in SIX[:-1]:
            self.put(c, 300)
        self.put('WWV_25000', 11 * 60 + 1)
        self.assertEqual(self.line(), check.Line(FAIL, 'GRAPE', 'no file in 11 min from WWV_25000.  '
                                                                'Tell the fleet admin.'))

    def test_young_recorder_waits_with_the_minutes_left(self):
        line = self.line(service=lambda p: svc(age_s=4 * 60))
        self.assertEqual(line, check.Line(WAIT, 'GRAPE',
                                          'first files due in about 8 min (hf-timestd started 4 min ago)'))

    def test_files_from_before_midnight_still_count(self):
        # Review Focus 1: at 00:03Z the newest file sits in yesterday's directory.
        now = datetime(2025, 10, 15, 0, 3, tzinfo=timezone.utc).timestamp()
        for c in SIX:
            self.put(c, 300, now=now)
        self.assertEqual(self.line(now=now).mark, OK)

    def test_no_config_fails_and_no_archived_channel_is_absent(self):
        self.cfg = None
        self.assertEqual(self.line().mark, FAIL)
        self.cfg = config(self.root, archive=False)
        self.assertEqual(self.line().mark, ABSENT)

    def test_stopped_recorder_fails(self):
        line = self.line(service=lambda p: svc(state='failed', age_s=None))
        self.assertEqual(line.text, 'hf-timestd is failed.  Fix: smd start hf-timestd')

    def test_asks_for_the_core_recorder(self):
        asked = []
        self.line(service=lambda p: asked.append(p) or svc())
        self.assertEqual(asked, ['timestd-core-recorder.service'])


class StorageTests(unittest.TestCase):
    def test_ten_percent_free_passes_and_less_fails(self):
        ok = check.storage_line(make_env(disk=lambda p: (1000, 100)))
        bad = check.storage_line(make_env(disk=lambda p: (1000, 99)))
        self.assertEqual(ok, check.Line(OK, 'storage', '/var/lib has 10 % free'))
        self.assertEqual(bad, check.Line(FAIL, 'storage',
                                         '/var/lib has only 9 % free.  Tell the fleet admin.'))


if __name__ == '__main__':
    unittest.main()

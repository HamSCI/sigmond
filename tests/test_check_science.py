"""`smd check` science lines: PSK, WSPR, meteor scatter
(tasks/spec-smd-check-v372.md §4)."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sigmond.commands import check
from sigmond.commands.check import ABSENT, FAIL, OK, WAIT

from tests.check_fakes import NOW, make_env, svc


def ft8(t, freq='14,074,131.2'):
    """A decode_ft8 spot line stamped at unix time t (UTC)."""
    stamp = datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y/%m/%d %H:%M:%S')
    return f'{stamp} -15 +0.50 {freq} ~ K1ABC W1XYZ EM26\n'


def jt9(t):
    stamp = datetime.fromtimestamp(t, tz=timezone.utc).strftime('%y%m%d %H%M%S')
    return f'{stamp} 14074000  -12  0.3 1234 ~  K1ABC W1XYZ EM26\n'


class PskTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.logs = Path(d.name)

    def line(self, **kw):
        return check.psk_line(make_env(**kw), log_dir=self.logs)

    def test_counts_spots_inside_15_minutes_only(self):
        (self.logs / 'B4-100-rx888mk2-status.local-ft8.log').write_text(
            ft8(NOW - 16 * 60) + ft8(NOW - 14 * 60) + ft8(NOW - 60) + 'garbage line\n')
        (self.logs / 'B4-100-rx888mk2-status.local-ft4.log').write_text(ft8(NOW - 30))
        self.assertEqual(self.line(), check.Line(OK, 'PSK', '2 FT8 and 1 FT4 spots in the last 15 min'))

    def test_sums_every_receiver(self):
        # Review Focus 5: two radiod instances, one log pair each.
        (self.logs / 'bee1-status.local-ft8.log').write_text(ft8(NOW - 60))
        (self.logs / 'bee2-status.local-ft8.log').write_text(ft8(NOW - 60) + ft8(NOW - 120))
        self.assertEqual(self.line().text, '3 FT8 and 0 FT4 spots in the last 15 min')

    def test_reads_jt9_lines_too(self):
        (self.logs / 'rx-ft8.log').write_text(jt9(NOW - 60))
        self.assertEqual(self.line().text, '1 FT8 and 0 FT4 spots in the last 15 min')

    def test_window_reaches_back_across_midnight(self):
        # Review Focus 1: at 00:05Z the window still counts 23:55Z spots.
        just_after = datetime(2025, 10, 15, 0, 5, tzinfo=timezone.utc).timestamp()
        (self.logs / 'rx-ft8.log').write_text(ft8(just_after - 10 * 60))
        self.assertEqual(check.psk_line(make_env(now=just_after), log_dir=self.logs).mark, OK)

    def test_young_service_with_no_spots_waits(self):
        line = self.line(service=lambda p: svc(age_s=14 * 60))
        self.assertEqual(line.mark, WAIT)
        self.assertIn('started 14 min ago', line.text)

    def test_restart_recreated_the_log(self):
        # Review Focus 3: a restart recreates <rx>-ft8.log, so older spots are gone.
        (self.logs / 'rx-ft8.log').write_text('')
        self.assertEqual(self.line(service=lambda p: svc(age_s=120)).mark, WAIT)
        (self.logs / 'rx-ft8.log').write_text(ft8(NOW - 30))
        self.assertEqual(self.line(service=lambda p: svc(age_s=120)).mark, OK)

    def test_old_service_with_no_spots_fails_and_names_the_antenna_first(self):
        line = self.line(service=lambda p: svc(age_s=15 * 60))
        self.assertEqual(line.mark, FAIL)
        self.assertTrue(line.text.startswith('no FT8 or FT4 spot in 15 min.  Check the antenna'))

    def test_stopped_service_fails_even_with_recent_spots(self):
        (self.logs / 'rx-ft8.log').write_text(ft8(NOW - 30))
        line = self.line(service=lambda p: svc(state='failed', age_s=None))
        self.assertEqual(line, check.Line(FAIL, 'PSK', 'psk-recorder is failed.  Fix: smd start psk-recorder'))

    def test_not_installed_is_absent(self):
        self.assertEqual(self.line(installed=lambda n: False).mark, ABSENT)

    def test_missing_log_dir_counts_as_no_spots(self):
        line = check.psk_line(make_env(), log_dir=self.logs / 'absent')
        self.assertEqual(line.mark, FAIL)


CYCLE = ('2026-10-14 11:52:31,897 - wspr_recorder.spot_sink - INFO - cycle UTC 11:50 '
         'rx=B4 → {n} spots in wspr.spots, 15 noise rows in wspr.noise (4 bands, write 2 ms) '
         'dt=+0.84 bands=[{bands}]')


class WsprTests(unittest.TestCase):
    def test_sums_spots_and_counts_bands(self):
        journal = [CYCLE.format(n=30, bands='40:10 30:7 20:13'),
                   'some other line',
                   CYCLE.format(n=18, bands='40:9 20:5 17:4 15:0')]
        env = make_env(journal=lambda units, minutes: journal)
        self.assertEqual(check.wspr_line(env), check.Line(OK, 'WSPR', '48 spots in the last 10 min, on 4 bands'))

    def test_old_shape_without_bands_list_uses_the_band_count(self):
        journal = ['cycle UTC 02:40 → 62 spots in wspr.spots (9 bands, write 4 ms)']
        line = check.wspr_line(make_env(journal=lambda u, m: journal))
        self.assertEqual(line.text, '62 spots in the last 10 min, on 9 bands')

    def test_asks_the_wspr_journal_for_10_minutes(self):
        asked = []
        check.wspr_line(make_env(journal=lambda u, m: asked.append((tuple(u), m)) or []))
        self.assertEqual(asked, [(('wspr-recorder@*.service',), 10)])

    def test_zero_spot_cycles_wait_then_fail(self):
        journal = [CYCLE.format(n=0, bands='')]
        young = make_env(journal=lambda u, m: journal, service=lambda p: svc(age_s=11 * 60))
        old = make_env(journal=lambda u, m: journal, service=lambda p: svc(age_s=12 * 60))
        self.assertEqual(check.wspr_line(young).mark, WAIT)
        self.assertEqual(check.wspr_line(old).mark, FAIL)
        self.assertIn('Check the antenna', check.wspr_line(old).text)

    def test_journal_refused_fails_with_the_user(self):
        # Review Focus 4: sigmond outside the journal groups and no sudo.
        def refused(units, minutes):
            raise check.Unreadable('the journal')
        line = check.run_checks(make_env(journal=refused), [('WSPR', check.wspr_line)])[0]
        self.assertEqual(line.mark, FAIL)
        self.assertRegex(line.text, r'^cannot read the journal as \S+$')


STATS = ('INFO:meteor_scatter.core.recorder:stats MSK144 rx=B4-status.local: spots={s} '
         'decodes={ok}/{all} slots_empty=0 freqs=2 inflight=0 oldest=0s (60s window)')


class MeteorTests(unittest.TestCase):
    def test_a_stats_line_passes_even_with_no_spots(self):
        journal = [STATS.format(s=0, ok=6, all=6), STATS.format(s=1, ok=4, all=5)]
        line = check.meteor_line(make_env(journal=lambda u, m: journal))
        self.assertEqual(line, check.Line(OK, 'meteor scatter',
                                          'decoding: 10 MSK144 decodes and 1 spot in the last 5 min'))

    def test_asks_for_5_minutes(self):
        asked = []
        check.meteor_line(make_env(journal=lambda u, m: asked.append(m) or []))
        self.assertEqual(asked, [5])

    def test_no_stats_line_waits_then_fails(self):
        self.assertEqual(check.meteor_line(make_env(service=lambda p: svc(age_s=240))).mark, WAIT)
        self.assertEqual(check.meteor_line(make_env(service=lambda p: svc(age_s=300))).mark, FAIL)

    def test_not_installed_is_absent(self):
        line = check.meteor_line(make_env(installed=lambda n: n != 'meteor-scatter'))
        self.assertEqual(line, check.Line(ABSENT, 'meteor scatter', 'not on this station'))


if __name__ == '__main__':
    unittest.main()

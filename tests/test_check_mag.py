"""`smd check` magnetometer line, with the freeze rule of spec §4.1 and the
missing adapter of v3.72 #17 (tasks/spec-smd-check-v372.md §4)."""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sigmond.commands import check
from sigmond.commands.check import ABSENT, FAIL, OK, WAIT

from tests.check_fakes import NOW, make_env, svc

FIX = 'Fix: sudo systemctl restart mag-recorder'


def sample(t, rt=23.31, x=-50.181, y=-4.442, z=15.945):
    ts = datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    return json.dumps({'ts': ts, 'rt': rt, 'x': x, 'y': y, 'z': z, 'reporter_id': 'AC0G=B4'})


class MagTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.device = self.dir / 'ttyMAG0'
        self.device.write_text('')

    def write(self, lines, day_t=NOW, mode='w'):
        name = f"samples-{datetime.fromtimestamp(day_t, tz=timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        with open(self.dir / name, mode) as fh:
            fh.write(''.join(ln + '\n' for ln in lines))

    def live(self, n=60, end=NOW - 1, **kw):
        return [sample(end - (n - 1 - i), x=-50.0 + i * 0.01, rt=23.0 + i * 0.001, **kw)
                for i in range(n)]

    def line(self, now=NOW, **kw):
        return check.mag_line(make_env(now=now, **kw), mag_dir=self.dir, device=str(self.device))

    def test_live_samples_pass(self):
        self.write(self.live())
        line = self.line()
        self.assertEqual(line.mark, OK)
        self.assertEqual(line.text, 'samples arriving, 23.1 C')

    def test_sixty_samples_at_zero_degrees_are_frozen(self):
        self.write([sample(NOW - 60 + i, rt=0.0, x=-50.0 + i) for i in range(60)])
        self.assertEqual(self.line(), check.Line(FAIL, 'magnetometer',
                                                 f'frozen: 0.00 C, no change in 60 s.  {FIX}'))

    def test_sixty_samples_with_unchanging_axes_are_frozen(self):
        self.write([sample(NOW - 60 + i, rt=23.0 + i * 0.01) for i in range(60)])
        line = self.line()
        self.assertEqual(line.mark, FAIL)
        self.assertTrue(line.text.startswith('frozen: x, y and z did not change in 60 s.'))

    def test_fifty_nine_frozen_then_one_live_passes(self):
        frozen = [sample(NOW - 61 + i, rt=0.0) for i in range(59)]
        self.write(frozen + [sample(NOW - 1, rt=23.4, x=-49.0)])
        self.assertEqual(self.line().mark, OK)

    def test_fewer_than_sixty_samples_cannot_be_called_frozen(self):
        self.write([sample(NOW - 30 + i, rt=0.0) for i in range(30)])
        self.assertEqual(self.line().mark, OK)

    def test_freeze_window_reaches_into_yesterdays_file(self):
        # Review Focus 1: at 00:00:30Z today's file holds 30 samples.
        now = datetime(2025, 10, 15, 0, 0, 30, tzinfo=timezone.utc).timestamp()
        self.write([sample(now - 60 + i, rt=0.0) for i in range(30)], day_t=now - 3600)
        self.write([sample(now - 30 + i, rt=0.0) for i in range(30)], day_t=now)
        self.assertEqual(self.line(now=now).mark, FAIL)

    def test_a_half_written_last_line_is_skipped(self):
        # Review Focus 2: mag-recorder is mid-write when smd check reads.
        self.write(self.live())
        with open(self.dir / f"samples-{datetime.fromtimestamp(NOW, tz=timezone.utc):%Y-%m-%d}.jsonl", 'a') as fh:
            fh.write('{"ts":"2025-10-14T12:00:00.0')
        self.assertEqual(self.line().mark, OK)

    def test_no_sample_for_ten_seconds_fails(self):
        self.write(self.live(end=NOW - 11))
        line = self.line()
        self.assertEqual(line, check.Line(FAIL, 'magnetometer', f'no sample for 11 s.  {FIX}'))

    def test_no_file_today_fails_unless_just_started(self):
        self.assertEqual(self.line().text, f'no sample today.  {FIX}')
        self.assertEqual(self.line(service=lambda p: svc(age_s=30)).mark, WAIT)

    def test_enabled_service_without_its_adapter_fails(self):
        self.device.unlink()
        self.write(self.live())
        line = self.line()
        self.assertEqual(line.mark, FAIL)
        self.assertEqual(line.text, f'no magnetometer adapter at {self.device}.  '
                                    f'Check its USB cable.  {FIX}')

    def test_a_station_that_never_had_a_magnetometer_reads_absent(self):
        # Every dasi2 image installs and enables mag-recorder; with no device
        # it writes no samples file, and the site profile names no PSWS
        # magnetometer instrument.  (mag-recorder.service carries Restart=always
        # and no condition, so the unit itself may read failed or restarting.)
        self.device.unlink()
        for state in ('inactive', 'failed'):
            line = self.line(service=lambda p: svc(state=state, age_s=None, enabled=True),
                             mag_psws_id=lambda: '')
            self.assertEqual(line, check.Line(ABSENT, 'magnetometer', 'no magnetometer on this station'))

    def test_a_declared_magnetometer_without_its_adapter_fails_before_any_sample(self):
        # R2: a reflashed station starts with an empty /var/lib/mag-recorder.
        # Its site profile still names the PSWS magnetometer instrument.
        self.device.unlink()
        line = self.line(service=lambda p: svc(state='inactive', age_s=None, enabled=True),
                         mag_psws_id=lambda: '84')
        self.assertEqual(line.mark, FAIL)
        self.assertEqual(line.text, f'no magnetometer adapter at {self.device}.  '
                                    f'Check its USB cable.  {FIX}')

    def test_history_without_a_declared_id_still_fails(self):
        self.device.unlink()
        self.write(self.live(), day_t=NOW - 2 * 86400)
        line = self.line(service=lambda p: svc(state='inactive', age_s=None, enabled=True),
                         mag_psws_id=lambda: '')
        self.assertEqual(line.mark, FAIL)

    def test_an_adapter_gone_after_samples_were_written_still_fails(self):
        # The B4 hub swap (v3.72 #17): samples once came, the adapter is gone now.
        self.device.unlink()
        self.write(self.live(), day_t=NOW - 5 * 86400)
        line = self.line(service=lambda p: svc(state='inactive', age_s=None, enabled=True))
        self.assertEqual(line.mark, FAIL)
        self.assertTrue(line.text.startswith('no magnetometer adapter at'))

    def test_a_disabled_service_without_its_adapter_reads_as_stopped(self):
        self.device.unlink()
        line = self.line(service=lambda p: svc(state='inactive', age_s=None, enabled=False))
        self.assertEqual(line.text, 'mag-recorder is inactive.  Fix: smd start mag-recorder')

    def test_dasi011_axes_frozen_while_rt_jumps_among_junk_values(self):
        # Measured 2026-10-14 at DASI-011: x, y and z each held one value for
        # minutes while rt jumped among junk values and samples kept arriving.
        junk = [0.0, 2.56, 12.12, 39.06, 39.12, 144.0]
        self.write([sample(NOW - 60 + i, rt=junk[i % len(junk)],
                           x=-42951.914, y=-12874.324, z=14730.293) for i in range(60)])
        line = self.line()
        self.assertEqual(line.mark, FAIL)
        self.assertEqual(line.text, f'frozen: x, y and z did not change in 60 s.  {FIX}')

    def test_not_installed_is_absent(self):
        self.assertEqual(self.line(installed=lambda n: False).mark, ABSENT)


class MagPswsIdTests(unittest.TestCase):
    """read_mag_psws_id: the default Env hook, read-only and never fatal."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = Path(d.name) / 'site-profile.toml'

    def read(self, reader=None):
        return check.read_mag_psws_id(reader or check.Reader(euid=lambda: 0), self.path)

    def test_the_magnetometer_instrument_id_is_read(self):
        self.path.write_text('[psws]\nstation_id = "S000170"\ninstrument_id = "172"\n'
                             '[psws.instruments]\n"mag-recorder" = "84"\n')
        self.assertEqual(self.read(), '84')

    def test_the_legacy_single_id_is_the_grape_id_not_the_magnetometer(self):
        self.path.write_text('[psws]\nstation_id = "S000170"\ninstrument_id = "172"\n')
        self.assertEqual(self.read(), '')

    def test_a_placeholder_counts_as_no_id(self):
        self.path.write_text('[psws.instruments]\n"mag-recorder" = "<from the portal>"\n')
        self.assertEqual(self.read(), '')

    def test_a_missing_broken_or_unreadable_profile_means_no_id(self):
        self.assertEqual(self.read(), '')
        self.path.write_text('[psws\nnot toml')
        self.assertEqual(self.read(), '')
        self.path.write_text('[psws.instruments]\n"mag-recorder" = "84"\n')
        self.path.chmod(0)
        self.addCleanup(self.path.chmod, 0o644)
        if os.access(self.path, os.R_OK):
            self.skipTest('running as root: every file is readable')
        refuses = check.Reader(run=lambda *a, **k: subprocess.CompletedProcess(a, 1, '', ''),
                               euid=lambda: 1000)
        self.assertEqual(self.read(refuses), '')
        allows = check.Reader(run=lambda *a, **k: subprocess.CompletedProcess(
            a, 0, '[psws.instruments]\n"mag-recorder" = "84"\n', ''), euid=lambda: 1000)
        self.assertEqual(self.read(allows), '84')


if __name__ == '__main__':
    unittest.main()

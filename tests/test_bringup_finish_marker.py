"""A bring-up finished by hand settles a gave-up first-run marker (v3.72 #14, R1).

bin/sigmond-firstrun-bringup treats result=ok and result=gave-up as final, and
`smd check` reads result=retry or result=gave-up as a failed bring-up.  An
operator who finished a gave-up station with `smd bringup` left that marker
behind, so the station read `FAIL  bring-up` for ever.  A bring-up that ends
with rc 0 now runs the site wiring firstrun would have run, and when that
succeeds rewrites a gave-up marker to result=ok in firstrun's own format.
result=retry stays: firstrun's next boot finishes it, with site wiring and
CPU isolation, and a hand run that wrote ok would skip both.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sigmond.bringup import build_plan
from sigmond.catalog import Profile

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault('SIGMOND_NO_VENV_REEXEC', '1')
    loader = importlib.machinery.SourceFileLoader('smd_under_test_marker', str(REPO / 'bin' / 'smd'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _dasi2():
    return Profile(name='dasi2', description='', clients=('hf-timestd', 'psk-recorder'),
                   local_radiod_infra=('igmp-querier',), optional=())


def _unsudo(calls, raise_on=None):
    """A subprocess.run that records each call and runs it without its
    `sudo -n` prefix, so the root path runs for real as this user."""
    import subprocess as sp
    real_run = sp.run      # taken before the test patches subprocess.run

    def run(argv, **kw):
        argv = list(argv)
        calls.append((argv, kw.get('input')))
        if raise_on and argv[:1] == [raise_on]:
            raise FileNotFoundError(2, 'No such file or directory', raise_on)
        real = argv[2:] if argv[:2] == ['sudo', '-n'] else argv
        return real_run(real, **kw)
    return run


class FinishSettlesMarker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.smd = _load_smd()

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.marker = self.dir / '.firstrun-bringup-done'
        self.site_timing = self.dir / 'sigmond-site-timing'
        self.site_rc(0)
        env = {'SIGMOND_FIRSTRUN_MARKER': str(self.marker),
               'SIGMOND_FIRSTRUN_SITE_TIMING': str(self.site_timing)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('SIGMOND_FIRSTRUN', None)
        self.calls = []

    def site_rc(self, rc):
        self.site_timing.write_text(f'#!/bin/sh\necho ran >> "{self.dir}/site-timing.ran"\nexit {rc}\n')
        self.site_timing.chmod(0o755)

    def site_ran(self):
        return (self.dir / 'site-timing.ran').exists()

    def finish(self, failed=(), sdr_absent=False, run=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
             mock.patch.object(self.smd.subprocess, 'run', run or _unsudo(self.calls)):
            rc = self.smd._bringup_finish(build_plan(_dasi2(), local_radiod=True,
                                                     sdr_absent=sdr_absent), 'dasi2', failed)
        return rc, out.getvalue()

    def fields(self):
        return dict(ln.split('=', 1) for ln in self.marker.read_text().splitlines() if '=' in ln)

    GAVE_UP = 'attempts=3\nresult=gave-up\n2026-10-13T16:30:27+00:00\n'

    def test_a_retry_marker_is_left_for_the_next_boot(self):
        # firstrun's next boot does bring-up, site wiring and isolation, then
        # writes ok.  A hand finish that wrote ok would skip the other two.
        before = 'attempts=1\nresult=retry\n2026-10-14T20:25:15+00:00\n'
        self.marker.write_text(before)
        self.assertEqual(self.finish()[0], 0)
        self.assertEqual(self.marker.read_text(), before)
        self.assertFalse(self.site_ran())

    def test_a_gave_up_marker_reads_ok_once_site_wiring_succeeds(self):
        self.marker.write_text(self.GAVE_UP)
        rc, out = self.finish()
        self.assertEqual(rc, 0)
        self.assertTrue(self.site_ran())
        lines = self.marker.read_text().splitlines()
        # firstrun's format exactly: attempts, result, then a `date -Iseconds` stamp.
        self.assertEqual(lines[:2], ['attempts=3', 'result=ok'])
        self.assertEqual(len(lines), 3)
        self.assertRegex(lines[2], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$')
        self.assertEqual(self.marker.stat().st_mode & 0o777, 0o644)
        self.assertIn('CPU isolation', out)
        self.assertIn('next reboot', out)
        # Site wiring runs as root.
        site = [a for a, _ in self.calls if str(self.site_timing) in a]
        self.assertEqual(site, [self.smd._as_root([str(self.site_timing)])])

    def test_a_failed_site_wiring_leaves_gave_up_and_says_so(self):
        self.marker.write_text(self.GAVE_UP)
        self.site_rc(1)
        rc, out = self.finish()
        self.assertEqual(rc, 0)
        self.assertEqual(self.marker.read_text(), self.GAVE_UP)
        self.assertIn('sigmond-site-timing', out)
        self.assertIn('exited 1', out)

    def test_a_marker_on_one_line_is_read_too(self):
        # Fleet output 2026-10-14 showed markers as one space-separated line.
        self.marker.write_text('attempts=2 result=gave-up 2026-10-14T20:25:15+00:00\n')
        self.assertEqual(self.finish()[0], 0)
        self.assertEqual(self.fields(), {'attempts': '2', 'result': 'ok'})

    def test_firstrun_as_the_caller_settles_nothing(self):
        # firstrun runs smd bringup itself and does site wiring and isolation
        # AFTER it; it writes its own marker at the end.
        self.marker.write_text(self.GAVE_UP)
        with mock.patch.dict(os.environ, {'SIGMOND_FIRSTRUN': '1'}):
            self.assertEqual(self.finish()[0], 0)
        self.assertEqual(self.marker.read_text(), self.GAVE_UP)
        self.assertFalse(self.site_ran())

    def test_no_marker_is_created(self):
        self.assertEqual(self.finish()[0], 0)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.site_ran())

    def test_a_failed_finish_leaves_the_marker_alone(self):
        self.marker.write_text(self.GAVE_UP)
        rc, _ = self.finish(failed=['start psk-recorder@AC0G (exit 1)'])
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.marker.read_text(), self.GAVE_UP)
        rc, _ = self.finish(sdr_absent=True)
        self.assertEqual(rc, 3)
        self.assertEqual(self.marker.read_text(), self.GAVE_UP)
        self.assertFalse(self.site_ran())

    def test_final_and_other_markers_are_left_alone(self):
        for text in ('attempts=1\nresult=ok\n2026-10-13T16:30:27+00:00\n',
                     # K3LR: the format from before result= existed, a stamp alone.
                     '2026-09-30T01:00:32+00:00\n',
                     'attempts=0\nresult=awaiting-sdr\nsdr_reruns=0\n2026-10-14T01:00:00+00:00\n'):
            self.marker.write_text(text)
            self.assertEqual(self.finish()[0], 0)
            self.assertEqual(self.marker.read_text(), text)
        self.assertFalse(self.site_ran())

    def test_a_missing_sudo_while_reading_is_a_warning_not_a_crash(self):
        self.marker.write_text(self.GAVE_UP)
        self.marker.chmod(0)
        self.addCleanup(self.marker.chmod, 0o644)
        if os.access(self.marker, os.R_OK):
            self.skipTest('running as root: every file is readable')
        rc, out = self.finish(run=_unsudo(self.calls, raise_on='sudo'))
        self.assertEqual(rc, 0)
        self.assertIn('cannot read', out)
        self.assertFalse(self.site_ran())

    def test_a_marker_owned_by_another_user_is_rewritten_through_sudo(self):
        # The real marker belongs to root; an operator's smd must not replace
        # it with one of its own.
        self.marker.write_text(self.GAVE_UP)
        st = self.marker.stat()
        with mock.patch.object(self.smd.os, 'geteuid', lambda: st.st_uid + 1):
            self.assertEqual(self.finish()[0], 0)
        writes = [(a, data) for a, data in self.calls if a[:2] == ['sudo', '-n'] and 'sh' in a]
        self.assertEqual(len(writes), 1)
        argv, data = writes[0]
        self.assertIn(str(self.marker), argv)
        self.assertIn(f'{st.st_uid}:{st.st_gid}', argv)
        self.assertTrue(data.startswith('attempts=3\nresult=ok\n'))
        self.assertEqual(self.fields(), {'attempts': '3', 'result': 'ok'})
        self.assertEqual((self.marker.stat().st_uid, self.marker.stat().st_gid),
                         (st.st_uid, st.st_gid))


if __name__ == '__main__':
    unittest.main()

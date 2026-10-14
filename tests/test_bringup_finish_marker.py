"""A bring-up finished by hand settles the first-run marker (v3.72 #14, R1).

bin/sigmond-firstrun-bringup treats result=ok and result=gave-up as final, and
`smd check` reads result=retry or result=gave-up as a failed bring-up.  An
operator who then finishes the job with `smd bringup` left that marker behind,
so the station read `FAIL  bring-up` for ever.  A bring-up that ends with rc 0
now rewrites such a marker to result=ok, in firstrun's own format.
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


class FinishSettlesMarker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.smd = _load_smd()

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.marker = Path(d.name) / '.firstrun-bringup-done'
        patcher = mock.patch.dict(os.environ, {'SIGMOND_FIRSTRUN_MARKER': str(self.marker)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def finish(self, failed=(), sdr_absent=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = self.smd._bringup_finish(build_plan(_dasi2(), local_radiod=True,
                                                     sdr_absent=sdr_absent), 'dasi2', failed)
        return rc, out.getvalue()

    def fields(self):
        return dict(ln.split('=', 1) for ln in self.marker.read_text().splitlines() if '=' in ln)

    def test_a_retry_marker_reads_ok_after_a_finish_by_hand(self):
        self.marker.write_text('attempts=1\nresult=retry\n2026-10-14T20:25:15+00:00\n')
        rc, _ = self.finish()
        self.assertEqual(rc, 0)
        lines = self.marker.read_text().splitlines()
        self.assertEqual(lines[:2], ['attempts=1', 'result=ok'])
        # firstrun's format exactly: attempts, result, then a `date -Iseconds` stamp.
        self.assertEqual(len(lines), 3)
        self.assertRegex(lines[2], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$')
        self.assertEqual(self.marker.stat().st_mode & 0o777, 0o644)

    def test_a_gave_up_marker_reads_ok_and_keeps_its_attempts(self):
        self.marker.write_text('attempts=3\nresult=gave-up\n2026-10-13T16:30:27+00:00\n')
        self.assertEqual(self.finish()[0], 0)
        self.assertEqual(self.fields(), {'attempts': '3', 'result': 'ok'})

    def test_a_marker_on_one_line_is_read_too(self):
        # Fleet output 2026-10-14 showed markers as one space-separated line.
        self.marker.write_text('attempts=2 result=retry 2026-10-14T20:25:15+00:00\n')
        self.assertEqual(self.finish()[0], 0)
        self.assertEqual(self.fields(), {'attempts': '2', 'result': 'ok'})

    def test_no_marker_is_created(self):
        self.assertEqual(self.finish()[0], 0)
        self.assertFalse(self.marker.exists())

    def test_a_failed_finish_leaves_the_marker_alone(self):
        before = 'attempts=1\nresult=retry\n2026-10-14T20:25:15+00:00\n'
        self.marker.write_text(before)
        rc, _ = self.finish(failed=['start psk-recorder@AC0G (exit 1)'])
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.marker.read_text(), before)
        rc, _ = self.finish(sdr_absent=True)
        self.assertEqual(rc, 3)
        self.assertEqual(self.marker.read_text(), before)

    def test_final_and_other_markers_are_left_alone(self):
        for text in ('attempts=1\nresult=ok\n2026-10-13T16:30:27+00:00\n',
                     # K3LR: the format from before result= existed, a stamp alone.
                     '2026-09-30T01:00:32+00:00\n',
                     'attempts=0\nresult=awaiting-sdr\nsdr_reruns=0\n2026-10-14T01:00:00+00:00\n'):
            self.marker.write_text(text)
            self.assertEqual(self.finish()[0], 0)
            self.assertEqual(self.marker.read_text(), text)

    def test_a_marker_this_user_cannot_write_is_rewritten_through_sudo(self):
        self.marker.write_text('attempts=1\nresult=retry\n2026-10-14T20:25:15+00:00\n')
        calls = []

        def fake_run(argv, **kw):
            calls.append((list(argv), kw.get('input')))
            return mock.Mock(returncode=0, stdout='', stderr='')
        with mock.patch.object(self.smd.os, 'access', lambda p, m: False), \
             mock.patch.object(self.smd.os, 'geteuid', lambda: 1000), \
             mock.patch.object(self.smd.subprocess, 'run', fake_run):
            self.assertEqual(self.finish()[0], 0)
        self.assertEqual(len(calls), 1)
        argv, data = calls[0]
        self.assertEqual(argv[:2], ['sudo', '-n'])
        self.assertIn(str(self.marker), argv)
        self.assertTrue(data.startswith('attempts=1\nresult=ok\n'))


if __name__ == '__main__':
    unittest.main()

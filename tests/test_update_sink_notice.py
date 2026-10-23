"""`smd update` says when the sink switches start to count (spec §6.1).

The carry-over runs in the manifest step alone; `smd update` never reaches
it.  So `smd update` prints one line, "the sink switches take effect at the
next `smd align`", when the carry-over marker stands absent and the
installed hs-uploader offers the feature word `gate`; otherwise nothing.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sigmond import sink_state as ss
from sigmond import uploader_manifest as um

from test_update_executor import _Rig, smd, _git

NOTICE = "the sink switches take effect at the next `smd align`"
GATE = frozenset({"gate", "pipelines.d", "switch-files"})
NO_GATE = frozenset({"pipelines.d", "switch-files"})


class NoticeTests(unittest.TestCase):

    def notice(self, *, marker, features):
        probes = []

        def probe(python=None):
            probes.append(python)
            return features
        with mock.patch.object(ss, "marker_stands", return_value=marker), \
                mock.patch.object(um, "hs_uploader_features", side_effect=probe):
            return ss.update_notice(), probes

    def test_printed_when_the_marker_stands_absent_and_gate_is_offered(self):
        line, probes = self.notice(marker=False, features=GATE)
        self.assertEqual(line, NOTICE)
        self.assertEqual(len(probes), 1)

    def test_silent_once_the_marker_stands(self):
        line, probes = self.notice(marker=True, features=GATE)
        self.assertIsNone(line)
        self.assertEqual(probes, [])          # no probe needed

    def test_silent_without_gate(self):
        for features in (NO_GATE, frozenset(), None):
            with self.subTest(features=features):
                line, _ = self.notice(marker=False, features=features)
                self.assertIsNone(line)


class UpdateWiringTests(_Rig):
    """cmd_update prints the line at its end, on both of its exits."""

    def _run(self, *, marker, features, apply=False):
        args = types.SimpleNamespace(base=self.base, apply=apply, no_fetch=False)
        import contextlib
        import io
        out = io.StringIO()
        with mock.patch.object(ss, "marker_stands", return_value=marker), \
                mock.patch.object(um, "hs_uploader_features", return_value=features), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = smd.cmd_update(args)
        return rc, out.getvalue()

    def test_a_dry_run_with_work_to_do_says_it(self):
        rc, text = self._run(marker=False, features=GATE)
        self.assertIn("[pull]", text)
        self.assertIn(NOTICE, text)
        self.assertEqual(text.count(NOTICE), 1)

    def test_nothing_to_do_says_it_too(self):
        _git("pull", "--ff-only", cwd=self.host)
        rc, text = self._run(marker=False, features=GATE)
        self.assertNotIn("[pull]", text)
        self.assertIn(NOTICE, text)

    def test_silent_once_the_marker_stands_or_without_gate(self):
        for marker, features in ((True, GATE), (False, NO_GATE)):
            with self.subTest(marker=marker, features=features):
                rc, text = self._run(marker=marker, features=features)
                self.assertIn("[pull]", text)
                self.assertNotIn(NOTICE, text)

    def test_a_notice_that_raises_stays_silent_and_keeps_the_exit_code(self):
        rc_ok, _ = self._run(marker=True, features=GATE)
        import contextlib
        import io
        out = io.StringIO()
        args = types.SimpleNamespace(base=self.base, apply=False, no_fetch=False)
        with mock.patch.object(ss, "update_notice",
                               side_effect=RuntimeError("probe blew up")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = smd.cmd_update(args)
        self.assertEqual(rc, rc_ok)
        self.assertIn("[pull]", out.getvalue())
        self.assertNotIn("probe blew up", out.getvalue())
        self.assertNotIn(NOTICE, out.getvalue())

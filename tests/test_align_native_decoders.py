"""v3.94 Task 3: `smd align` runs the native rebuilds that are due.

v3.93's align moved the ubersdr_wsprd checkout to 5eeeb53 and never rebuilt
the decoders; /usr/local/libexec/sigmond/decoders/ubersdr/.provenance still
named 8719788 on every station.  These tests drive the real `cmd_align` (dry
run and --apply) and the real `_native_rebuilds_due` over a real checkout;
only the builder's compile step and the root-only marker files stay stubbed.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from tests.test_align_native_rebuild import _Rig, _git, smd

REPO = Path(__file__).resolve().parent.parent
PIN = "5eeeb53"


class _DecoderRig(_Rig):
    """_Rig plus base/ubersdr_wsprd (two commits, HEAD at the pin) and a
    decoder directory whose .provenance names the older commit."""

    def setUp(self):
        super().setUp()
        # ka9q-radio and ka9q-web already built at HEAD: only decoders are due.
        for n in self.NAMES:
            self._record(n, self.new[n])
        root = Path(self._tmp.name)
        src = self.base / "ubersdr_wsprd"
        src.mkdir()
        _git("init", "-q", "-b", "main", cwd=src)
        (src / "a").write_text("a\n")
        _git("add", "-A", cwd=src)
        _git("commit", "-qm", "one", cwd=src)
        self.dec_old = _git("rev-parse", "HEAD", cwd=src).stdout.strip()
        (src / "b").write_text("b\n")
        _git("add", "-A", cwd=src)
        _git("commit", "-qm", "two", cwd=src)
        self.dec_new = _git("rev-parse", "HEAD", cwd=src).stdout.strip()
        self.decoders = root / "decoders"
        prov = self.decoders / "ubersdr" / ".provenance"
        prov.parent.mkdir(parents=True)
        prov.write_text(f"ubersdr-decoders {self.dec_old} version=x parity=verified\n")
        self.builds = []
        self.build_rc = True
        self.marked, self.cleared, self.recorded = [], [], []

        def builder(name, base):
            self.builds.append(name)
            return self.build_rc
        for name, val in (
                ("_DECODER_DIR", self.decoders),
                ("_UBERSDR_COMMIT", self.dec_new),
                ("_run_native_builder", builder),
                ("_record_native_build",
                 lambda *a: self.recorded.append(a)),
                ("_mark_native_build_failed",
                 lambda b, why: self.marked.append((b, why))),
                ("_clear_native_build_failed",
                 lambda b: self.cleared.append(b))):
            p = mock.patch.object(smd, name, val)
            p.start()
            self.addCleanup(p.stop)


class AlignNativeDecodersTests(_DecoderRig):

    def test_align_rebuilds_due_decoders(self):
        rc, out, _ = self._apply()
        self.assertEqual(self.builds, ["ubersdr-decoders"], out)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.recorded), 1)

    def test_dry_run_lists_due_decoders_builds_nothing(self):
        rc, out = self._dry_run()
        self.assertIn(
            f"native rebuild due: ubersdr-decoders (built {self.dec_old[:7]}, "
            f"pin {self.dec_new[:7]})", out)
        self.assertEqual(self.builds, [])
        self.assertEqual(rc, 1, out)          # --apply has work to do

    def test_dry_run_with_decoders_at_pin_is_quiet(self):
        (self.decoders / "ubersdr" / ".provenance").write_text(
            f"ubersdr-decoders {self.dec_new} version=x parity=verified\n")
        rc, out = self._dry_run()
        self.assertNotIn("native rebuild due", out)

    def test_failed_decoder_build_is_reported_and_counted(self):
        self.build_rc = False
        rc, out, _ = self._apply()
        self.assertEqual(self.builds, ["ubersdr-decoders"], out)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("ubersdr-decoders", out)
        self.assertIn("FAILED", out)
        self.assertEqual([m[0] for m in self.marked], ["ubersdr-decoders"])
        self.assertEqual(self.recorded, [])

    def test_decoders_build_before_the_live_stages(self):
        order = []
        with mock.patch.object(smd, "_run_native_builder",
                               lambda n, b: (order.append("build"), True)[1]), \
             mock.patch.object(smd, "_align_make_live",
                               lambda *a, **k: (order.append("live"), 0)[1]):
            self._apply()
        self.assertEqual(order, ["build", "live"])


class NoRecorderFlagsTests(unittest.TestCase):

    FLAG = re.compile(r"(?:^|[\s\"'\[,])(--nn|--fine-freq|-F)(?=[\s\"'\],=]|$)")

    def test_no_recorder_template_passes_nn_or_F(self):
        hits = []
        for p in sorted((REPO / "etc").rglob("*")):
            if not p.is_file():
                continue
            try:
                text = p.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if self.FLAG.search(line):
                    hits.append(f"{p.relative_to(REPO)}:{n}: {line.strip()}")
        self.assertEqual(hits, [])

"""v3.94 Task 3: `smd align` runs the native rebuilds that are due.

v3.93's align moved the ubersdr_wsprd checkout to 5eeeb53 and never rebuilt
the decoders; /usr/local/libexec/sigmond/decoders/ubersdr/.provenance still
named 8719788 on every station.  These tests drive the real `cmd_align` (dry
run and --apply) and the real `_native_rebuilds_due` over a real checkout;
only the builder's compile step and the root-only marker files stay stubbed.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import re
import unittest
from pathlib import Path
from unittest import mock

from sigmond import align_apply
from tests.test_align_cli import (APPLY_REL, _no_real_record_live,
                                   _quiet_live_stages)
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


class OnionDoubleBuildTests(_DecoderRig):
    """Onion maps to the ka9q-web builder.  When ka9q-web is already rebuilt
    in this align (Task 2), the decoder stage neither builds nor counts it
    a second time."""

    def setUp(self):
        super().setUp()
        (self.decoders / "ubersdr" / ".provenance").write_text(
            f"ubersdr-decoders {self.dec_new} version=x parity=verified\n")
        onion = self.base / "onion"
        onion.mkdir()
        _git("init", "-q", "-b", "main", cwd=onion)
        (onion / "a").write_text("a\n")
        _git("add", "-A", cwd=onion)
        _git("commit", "-qm", "one", cwd=onion)
        self.onion_old = _git("rev-parse", "HEAD", cwd=onion).stdout.strip()
        (onion / "b").write_text("b\n")
        _git("add", "-A", cwd=onion)
        _git("commit", "-qm", "two", cwd=onion)
        self.onion_new = _git("rev-parse", "HEAD", cwd=onion).stdout.strip()
        _git("checkout", "-q", self.onion_old, cwd=onion)
        p = mock.patch.object(smd, "_ONION_COMMIT", self.onion_new)
        p.start()
        self.addCleanup(p.stop)
        # ka9q-web trails its record, so Task 2 rebuilds it.
        self._record("ka9q-web", self.old["ka9q-web"])

    def test_apply_does_not_build_ka9q_web_twice(self):
        rc, out, _ = self._apply()
        self.assertEqual(len([c for c in self.calls if c[0] == "ka9q-web"]), 1, out)
        self.assertNotIn("ka9q-web", self.builds, out)

    def test_dry_run_counts_ka9q_web_once(self):
        rc, out = self._dry_run()
        self.assertIn("1 to rebuild", out)
        self.assertNotIn("native rebuild due: ka9q-web", out)

    def test_onion_alone_still_builds_ka9q_web(self):
        self._record("ka9q-web", self.new["ka9q-web"])
        rc, out, _ = self._apply()
        self.assertEqual(self.builds, ["ka9q-web"], out)


class OldTargetTests(unittest.TestCase):
    """Sigmond moved to a target whose smd align predates this one: this
    process still holds the OLD pin constants, so it must not build decoders."""

    def _run(self, has_align):
        due = [("ubersdr_wsprd", "ubersdr-decoders", "a" * 40, "b" * 40)]
        builds = []
        boot = align_apply.Step("sigmond", "moved", "459bee6 -> daba1f6", 100)
        rest = [align_apply.Step("hf-timestd", "current"),
                align_apply.Step("ka9q-radio", "current")]
        out = io.StringIO()
        with contextlib.ExitStack() as st:
            st.enter_context(_quiet_live_stages())
            st.enter_context(_no_real_record_live())
            for p in [
                mock.patch("sigmond.align_apply.apply_plan",
                           mock.Mock(side_effect=[[boot], rest])),
                mock.patch.object(smd, "_need_root", return_value=False),
                mock.patch.object(smd, "lifecycle_lock",
                                  lambda reason=None: contextlib.nullcontext()),
                mock.patch.object(smd, "_align_live_state",
                                  return_value=({"sigmond": "459bee6", "hf-timestd": "5c8196d",
                                                 "ka9q-radio": "401992c"}, {}, {}, {})),
                mock.patch("sigmond.align.fetch_release", return_value=APPLY_REL),
                mock.patch("sigmond.align_apply.verify_release", return_value=None),
                mock.patch.object(smd, "_align_ancestry",
                                  return_value=lambda c, a, b: True),
                mock.patch.object(smd, "_align_target_has_align", return_value=has_align),
                mock.patch.object(smd, "_align_target_skips_missing", return_value=has_align),
                mock.patch.object(smd, "_align_target_supports", return_value=True),
                mock.patch("sigmond.align_apply.record"),
                mock.patch("sigmond.align_apply.record_history"),
                mock.patch("sigmond.catalog.load_catalog", return_value={}),
                mock.patch("os.execv"),
                mock.patch.object(smd, "_native_rebuilds_due", lambda base: due),
                mock.patch.object(smd, "_run_native_builder",
                                  lambda n, b: builds.append(n) or True),
                mock.patch.object(smd, "_record_native_build"),
                mock.patch.object(smd, "_clear_native_build_failed"),
            ]:
                st.enter_context(p)
            args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=True,
                                      apply=True, allow_rollback=False, max_bytes=None)
            with contextlib.redirect_stdout(out):
                smd.cmd_align(args)
        return builds, out.getvalue()

    def test_old_smd_continuing_skips_native_decoders(self):
        builds, out = self._run(has_align=False)
        self.assertEqual(builds, [], out)
        self.assertIn("predates this one", out)
        self.assertIn("smd admin native-build --apply", out)


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

    RENDERERS = ("instance", "coordination", "bringup", "harmonize",
                 "decoder_choice", "uniform_routing")

    def test_no_recorder_renderer_passes_nn_or_F(self):
        """The code that renders recorder env and args: its source, and the
        per-instance env stub it writes for each recorder."""
        hits = []
        for name in self.RENDERERS:
            p = REPO / "lib" / "sigmond" / f"{name}.py"
            for n, line in enumerate(p.read_text().splitlines(), 1):
                if self.FLAG.search(line):
                    hits.append(f"{p.relative_to(REPO)}:{n}: {line.strip()}")
        from sigmond import instance
        for client in ("wspr-recorder", "psk-recorder"):
            for n, line in enumerate(instance._env_stub(client, "AC0G").splitlines(), 1):
                if self.FLAG.search(line):
                    hits.append(f"_env_stub({client}):{n}: {line.strip()}")
        self.assertEqual(hits, [])

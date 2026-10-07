"""`smd sink off|upload|status` -- the site sink switch in the sink vocabulary
(tasks/plan-sink-control.md §2.1, §10.3 item 1)."""
import contextlib
import io
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import sigmond.commands.config as cfg
from sigmond.commands import sink


class SinkCliTests(unittest.TestCase):
    def setUp(self):
        d = TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.coord = Path(d.name) / "coordination.toml"
        self.coord.write_text('[host]\ncall = "DASI002"\ngrid = "FN21ok"\n')
        self.profile = Path(d.name) / "site-profile.toml"
        self.profile.write_text('[station]\ncallsign = "DASI002"\ngrid_square = "FN21ok"\n')
        for name, val in (("COORDINATION_PATH", self.coord), ("SITE_PROFILE_PATH", self.profile)):
            p = mock.patch.object(cfg, name, val); p.start(); self.addCleanup(p.stop)
        self.regen = mock.patch.object(cfg, "_regenerate_uploader_manifest", return_value=0).start()
        self.doors = mock.patch.object(cfg, "_close_doors", return_value=0).start()
        self.addCleanup(mock.patch.stopall)
        mock.patch("sigmond.uploader_manifest.hs_uploader_supports_discard", return_value=True).start()
        mock.patch("sigmond.uploader_manifest.suppressed_pipelines",
                   return_value=["wspr-wsprnet"]).start()

    def _run(self, **kw):
        args = types.SimpleNamespace(**{"sink_command": None, "reason": None, "yes": False, **kw})
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = sink.cmd_sink(args)
        return rc, out.getvalue()

    def _mode(self):
        from sigmond.coordination import load_coordination
        return load_coordination(self.coord).uploads.mode

    def test_off_writes_the_legacy_discard_and_speaks_sink(self):
        rc, out = self._run(sink_command="off", reason="bench", yes=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._mode(), "discard")
        self.assertIn("site sink: off", out)
        self.assertIn("site sink switch set to off", out)
        # No legacy words under `smd sink` (spec §2.1).
        self.assertNotIn("discard mode", out)
        self.assertNotIn("[uploads] mode", out)

    def test_off_needs_a_reason(self):
        rc, out = self._run(sink_command="off", reason="", yes=True)
        self.assertEqual(rc, 2)
        self.assertIn("smd sink off needs --reason", out)
        self.regen.assert_not_called()

    def test_off_unconfirmed_off_a_terminal_changes_nothing(self):
        with mock.patch("sys.stdin.isatty", return_value=False):
            rc, out = self._run(sink_command="off", reason="bench", yes=False)
        self.assertEqual(rc, 1)
        self.assertEqual(self._mode(), "upload")
        self.assertIn("smd sink off needs --yes", out)

    def test_off_refuses_an_hs_uploader_that_would_ship(self):
        # §10.3 item 1: smd sink off keeps smd upload discard's refusal.
        with mock.patch("sigmond.uploader_manifest.hs_uploader_supports_discard",
                        return_value=False):
            rc, out = self._run(sink_command="off", reason="bench", yes=True)
        self.assertEqual(rc, 1)
        self.assertEqual(self._mode(), "upload")
        self.regen.assert_not_called()
        self.assertIn("cannot discard", out)
        self.assertNotIn("policy unchanged", out)

    def test_upload_after_off_closes_the_doors(self):
        self._run(sink_command="off", reason="bench", yes=True)
        rc, out = self._run(sink_command="upload")
        self.assertEqual(rc, 0)
        self.doors.assert_called_once()
        self.assertEqual(self._mode(), "upload")
        self.assertIn("site sink: upload", out)
        self.assertIn("read off will ship", out)
        self.assertNotIn("during discard", out)

    def test_fill_is_refused_and_changes_nothing(self):
        rc, out = self._run(sink_command="fill")
        self.assertEqual(rc, 2)
        self.assertIn("not available yet", out)
        self.regen.assert_not_called()
        self.assertEqual(self._mode(), "upload")

    def test_status_reports_upload(self):
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("site sink: upload", out)

    def test_status_reports_off_and_what_it_holds_back(self):
        self._run(sink_command="off", reason="bench", yes=True)
        rc, out = self._run(sink_command="status")
        self.assertEqual(rc, 0)
        self.assertIn("site sink: off", out)
        self.assertIn("bench", out)
        self.assertIn("wspr-wsprnet", out)

    def test_status_warns_when_this_host_cannot_discard(self):
        self._run(sink_command="off", reason="bench", yes=True)
        with mock.patch("sigmond.uploader_manifest.hs_uploader_supports_discard",
                        return_value=False):
            rc, out = self._run(sink_command="status")
        self.assertEqual(rc, 0)
        self.assertIn("cannot discard", out)

    def test_status_names_a_legacy_hold(self):
        from sigmond.commands.config import _patch_uploads_block
        from sigmond.coordination import Uploads
        _patch_uploads_block(self.coord, Uploads(mode="hold", reason="station under test"))
        rc, out = self._run(sink_command="status")
        self.assertEqual(rc, 0)
        self.assertIn("hold (legacy)", out)
        self.assertIn("station under test", out)
        self.assertNotIn("filling", out)

    # -- the remaining sink strings of cmd_config_uploads, driven through `smd sink`

    def test_upload_from_a_legacy_hold_says_the_stored_backlog_ships(self):
        # Review Focus 5: a station on the legacy hold, such as K3LR.
        from sigmond.commands.config import _patch_uploads_block
        from sigmond.coordination import Uploads
        _patch_uploads_block(self.coord, Uploads(mode="hold", reason="station under test"))
        rc, out = self._run(sink_command="upload")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._mode(), "upload")
        self.assertIn("the backlog stored under the legacy hold ships now, oldest first", out)
        self.assertIn("site sink: upload", out)
        self.doors.assert_not_called()

    def test_upload_after_off_on_a_host_that_could_not_discard_says_so(self):
        # off was written, but this host's hs-uploader rendered the legacy hold:
        # data piled up, and it ships now.
        self._run(sink_command="off", reason="bench", yes=True)
        with mock.patch("sigmond.uploader_manifest.hs_uploader_supports_discard",
                        return_value=False):
            rc, out = self._run(sink_command="upload")
        self.assertEqual(rc, 0, out)
        self.assertIn("this host could not discard", out)
        self.assertIn("that backlog ships now, oldest first", out)
        self.assertNotIn("read off will ship", out)

    def test_a_permission_failure_names_the_site_sink_switch(self):
        with mock.patch.object(cfg, "_patch_uploads_block", side_effect=PermissionError):
            rc, out = self._run(sink_command="off", reason="bench", yes=True)
        self.assertEqual(rc, 1)
        self.assertIn("permission denied writing the site sink switch", out)
        self.assertNotIn("the policy", out)

    def test_every_sink_message_keeps_to_the_sink_words(self):
        for kw in (dict(sink_command="off", reason="bench", yes=True),
                   dict(sink_command="upload"),
                   dict(sink_command="status")):
            rc, out = self._run(**kw)
            self.assertEqual(rc, 0, out)
            for legacy in ("discard", "[uploads]", "uploads on", "hold —"):
                self.assertNotIn(legacy, out, f"{kw['sink_command']}: {out}")


class SinkCommandRegistrationTests(unittest.TestCase):
    """`bin/smd sink` reaches cmd_sink, and its help names the three verbs."""

    def _smd(self, *argv):
        repo = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(repo / "lib"), SIGMOND_NO_VENV_REEXEC="1")
        proc = subprocess.run([sys.executable, str(repo / "bin" / "smd"), *argv],
                              capture_output=True, text=True, env=env, timeout=60)
        return proc.returncode, " ".join((proc.stdout + proc.stderr).split())

    def test_sink_help_lists_the_verbs(self):
        rc, out = self._smd("sink", "--help")
        self.assertEqual(rc, 0, out)
        for verb in ("status", "off", "upload", "fill"):
            self.assertIn(verb, out)

    def test_sink_help_says_leaving_off_sets_aside_every_spooled_package(self):
        # close_doors moves EVERY GRAPE package and magnetometer zip still in
        # the spools, unsent ones built before off included (final review S4).
        rc, out = self._smd("sink", "--help")
        self.assertEqual(rc, 0, out)
        self.assertIn("every GRAPE package and magnetometer zip still in the spools", out)
        self.assertIn("unsent ones built before off", out)
        self.assertNotIn("stored while off", out)

    def test_the_contributor_table_says_the_same(self):
        page = Path(__file__).resolve().parents[1] / "docs" / "contributor" / "orchestration.md"
        rows = {line.split("|")[1].strip(): line for line in page.read_text().splitlines()
                if line.startswith("| `sink` |") or line.startswith("| `upload` |")}
        for verb in ("`sink`", "`upload`"):
            row = " ".join(rows[verb].split())
            self.assertIn("every GRAPE package and magnetometer zip still in the spools", row, verb)
        self.assertNotIn("stored while off", rows["`sink`"])

    def test_sink_fill_refuses_without_root(self):
        # `fill` is refused before any root check, with exit code 2.
        rc, out = self._smd("sink", "fill")
        self.assertEqual(rc, 2, out)
        self.assertIn("not available yet", out)

    def test_sink_off_needs_a_reason_at_the_parser(self):
        rc, out = self._smd("sink", "off")
        self.assertEqual(rc, 2, out)
        self.assertIn("--reason", out)


class LegacyUploadHelpTests(unittest.TestCase):
    """`smd upload` stays as the legacy alias.  Its help must not promise what
    the legacy modes do not do (tasks/plan-sink-control.md §10.3 item 1)."""

    def test_upload_help_points_at_smd_sink_and_says_what_hold_keeps(self):
        repo = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(repo / "lib"), SIGMOND_NO_VENV_REEXEC="1")
        out = subprocess.run([sys.executable, str(repo / "bin" / "smd"), "upload", "--help"],
                             capture_output=True, text=True, env=env, timeout=60).stdout
        out = " ".join(out.split())          # argparse wraps help text
        self.assertIn("smd sink upload", out)
        self.assertIn("smd sink off", out)
        self.assertIn("about 24 h", out)
        self.assertIn("except the GRAPE and magnetometer packages", out)
        self.assertNotIn("store everything", out)
        self.assertNotIn("keeps the data", out)

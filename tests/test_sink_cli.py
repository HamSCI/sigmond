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
        # Part 1's path: no `gate`, so no hs-uploader call and no site.toml
        # (the gate path has its own tests in test_sink_site_form).
        mock.patch("sigmond.uploader_manifest.hs_uploader_features",
                   return_value=frozenset()).start()
        # No carry-over marker: status reads [uploads] (spec §7.5), whatever
        # the host running the suite holds at the production path.
        mock.patch("sigmond.sink_state.marker_stands", return_value=False).start()

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
        self.assertIn("go out, even if built while the site sink switch read off", out)
        self.assertNotIn("during discard", out)

    def test_fill_without_gate_writes_the_legacy_hold(self):
        # V2: an hs-uploader without `gate` gets part 1's write, where fill
        # rides on the legacy hold.
        rc, out = self._run(sink_command="fill", reason="rig")
        self.assertEqual(rc, 0, out)
        self.regen.assert_called_once()
        self.assertEqual(self._mode(), "hold")
        self.assertIn("site sink: hold (legacy)", out)
        self.assertIn("waits for an hs-uploader that offers the gate", out)

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

    def test_sink_help_says_leaving_off_keeps_today_and_yesterday(self):
        # close_doors keeps today's and yesterday's (UTC) packages; they ship.
        # Older ones are set aside (v3.72 #5).
        rc, out = self._smd("sink", "--help")
        self.assertEqual(rc, 0, out)
        self.assertIn("Today's and yesterday's (UTC) packages stay and ship", out)
        self.assertIn("older ones are set aside", out)
        self.assertNotIn("none of them ships", out)
        self.assertNotIn("stored while off", out)

    def test_the_contributor_table_says_the_same(self):
        page = Path(__file__).resolve().parents[1] / "docs" / "contributor" / "orchestration.md"
        rows = {line.split("|")[1].strip(): line for line in page.read_text().splitlines()
                if line.startswith("| `sink` |") or line.startswith("| `upload` |")}
        for verb in ("`sink`", "`upload`"):
            row = " ".join(rows[verb].split())
            self.assertIn("keeps today's and yesterday's (UTC) packages", row, verb)
            self.assertNotIn("none of them ships", row, verb)
        self.assertNotIn("stored while off", rows["`sink`"])

    def test_sink_fill_takes_a_reason(self):
        # Never run a mutating verb here: it elevates through sudo and would
        # write this host's /etc.  --help exits at the parser.
        rc, out = self._smd("sink", "fill", "--help")
        self.assertEqual(rc, 0, out)
        self.assertIn("--reason", out)
        self.assertIn("FT8 spots for one hour and WSPR spots for 24 hours", out)
        self.assertNotIn("not available yet", out)

    def test_the_client_form_keeps_refusing(self):
        # D18: `smd sink <client> ...` stays out of part 2; the parser
        # refuses it before any root check.
        rc, out = self._smd("sink", "psk-recorder", "fill")
        self.assertEqual(rc, 2, out)
        self.assertIn("invalid choice", out)

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
        self.assertIn("today's and yesterday's (UTC) packages go out", out)
        self.assertNotIn("except the GRAPE and magnetometer packages", out)
        self.assertNotIn("store everything", out)
        self.assertNotIn("keeps the data", out)


# ---------------------------------------------------------------------------
# Spec §7.5, test 23: the readers follow the switch files once the marker
# stands, and [uploads] before.  Each station is a scratch copy of the
# carry-over `legacy` fixture (W1AW: psk-recorder direct, wspr-recorder with
# its shim on), carried over by one manifest step that runs the branch
# hs-uploader.  `sink status --json` runs that hs-uploader too, through a
# recording runner.
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sink_carryover import needs_real  # noqa: E402
from test_sink_site_form import SiteStation  # noqa: E402
from test_uploader_manifest_part2 import tree_sum  # noqa: E402

from sigmond import sink_mirror  # noqa: E402
from sigmond import uploader_manifest as um  # noqa: E402

FIXTURE_STATUS = Path(__file__).resolve().parent / "fixtures" / "sink_status"
PSK = "psk-recorder@W1AW.service"
WSPR = "wspr-recorder@W1AW.service"


class StatusStation(SiteStation):
    """A SiteStation whose status runs record every hs-uploader call."""

    def status(self, *, legacy=False, runner=None):
        calls = self.hs_calls = []
        real = um.run_hs_uploader

        def run(args, **kw):
            calls.append(list(args))
            if runner is not None:
                return runner(list(args), **kw)
            return real(args, **kw)

        if legacy:          # `smd upload status`
            args = types.SimpleNamespace(uploads_command="status", reason=None,
                                         yes=False)
            fn = lambda: cfg.cmd_config_uploads(args)          # noqa: E731
        else:
            args = types.SimpleNamespace(sink_command="status", reason=None,
                                         yes=False)
            fn = lambda: sink.cmd_sink(args)                    # noqa: E731
        return self.run(fn, extra=[
            mock.patch.object(cfg, "COORDINATION_PATH", self.coord_path),
            mock.patch.object(cfg, "SITE_PROFILE_PATH", self.profile),
            mock.patch.object(um, "run_hs_uploader", side_effect=run)])

    def status_calls(self):
        return [a for a in self.hs_calls if "status" in a and "sink" in a]

    def set_client(self, client, state):
        """`hs-uploader sink set <client>` direct, as bench step 6 runs it:
        no lever step follows (D6)."""
        r = um.run_hs_uploader(
            ["sink", "set", client, state, "--reason", "bench", "--by", "test",
             "--sinks", str(self.roots.sinks)], python=self.roots.python)
        assert r.returncode == 0, r.stderr

    def hand_edit_uploads(self, body="[uploads]\nenabled = false\n"):
        self.coord_path.write_text(self.coord_path.read_text() + "\n" + body)


def _fixture_runner(case, rc=None):
    text = (FIXTURE_STATUS / f"{case}.json").read_text()
    code = int((FIXTURE_STATUS / f"{case}.rc").read_text()) if rc is None else rc

    def run(args, **kw):
        return subprocess.CompletedProcess(args, code, text, "")
    return run


@needs_real
class SwitchStatusTests(unittest.TestCase):
    """Test 23 and the rest of spec §7.5's list."""

    def test_status_reads_the_json_once_the_marker_stands(self):
        st = StatusStation(self)
        r = st.status()
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(len(st.status_calls()), 1, st.hs_calls)
        call = st.status_calls()[0]
        self.assertIn("--json", call)
        self.assertEqual(call[call.index("--marker") + 1], str(st.roots.marker))
        self.assertEqual(call[call.index("--sinks") + 1], str(st.roots.sinks))
        self.assertIn("site sink: upload", r.both)
        self.assertIn("by carry-over", r.both)
        self.assertIn("psk-recorder: its file reads upload; effective upload",
                      r.both)
        self.assertIn("wspr-recorder: its file reads upload; effective upload",
                      r.both)

    def test_status_reads_uploads_before_the_marker(self):
        st = StatusStation(self, carried=False)
        self.assertFalse(st.roots.marker.exists())
        r = st.status()
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.status_calls(), [])
        self.assertIn("site sink: upload (store and send)", r.both)
        self.assertNotIn("effective", r.both)

    def test_upload_status_follows_the_same_rule(self):
        st = StatusStation(self)
        r = st.status(legacy=True)
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(len(st.status_calls()), 1, st.hs_calls)
        self.assertIn("psk-recorder: its file reads upload; effective upload",
                      r.both)
        st2 = StatusStation(self, carried=False)
        r = st2.status(legacy=True)
        self.assertEqual(st2.status_calls(), [])
        self.assertIn("uploads: on (store and ship)", r.both)

    def test_a_client_held_by_its_own_switch_names_the_switch_and_pipeline(self):
        st = StatusStation(self)
        st.set_client("psk-recorder", "fill")
        r = st.status()
        self.assertIn("psk-recorder: its file reads fill; effective fill, set "
                      "by its own switch", r.both)
        self.assertIn("pipelines held: psk-pskreporter (client)", r.both)

    def test_the_site_switch_names_what_holds_each_client(self):
        st = StatusStation(self)
        r = um.run_hs_uploader(
            ["sink", "set", "site", "fill", "--reason", "rig", "--by", "test",
             "--sinks", str(st.roots.sinks)], python=st.roots.python)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = st.status()
        self.assertIn("site sink: fill", r.both)
        self.assertIn("wspr-recorder: its file reads upload; effective fill, "
                      "set by the site switch", r.both)

    def test_an_armed_sender_below_upload_reads_unmanaged(self):
        st = StatusStation(self)
        r = st.status()
        self.assertNotIn("UNMANAGED", r.both)          # at upload: no lever due
        st.set_client("psk-recorder", "fill")           # D6: no lever ran
        r = st.status()
        self.assertEqual(r.rc, 1, r.both)
        self.assertRegex(r.both, r"UNMANAGED: psk-recorder@W1AW\.service")
        self.assertNotIn("UNMANAGED: wspr", r.both)

    def test_a_stale_writer_appears_by_name(self):
        st = StatusStation(self)
        r = st.status()
        self.assertNotIn("stale writer", r.both)
        st.make_stale()
        r = st.status()
        self.assertIn(f"stale writer: {PSK}", r.both)
        self.assertIn(f"stale writer: {WSPR}", r.both)

    def test_a_mirror_mismatch_appears_and_is_not_repaired(self):
        st = StatusStation(self)
        st.hand_edit_uploads()
        before = st.coord_path.read_bytes()
        r = st.status()
        self.assertEqual(r.rc, 1, r.both)
        self.assertIn("mirror mismatch", r.both)
        self.assertIn(f"{st.coord_path} reads [uploads] mode = hold", r.both)
        self.assertEqual(st.coord_path.read_bytes(), before)

    def test_status_writes_nothing_and_never_elevates(self):
        st = StatusStation(self)
        st.set_client("psk-recorder", "fill")     # an armed sender to report
        st.make_stale()                           # a stale writer to report
        st.hand_edit_uploads()                    # a mirror to report
        before = tree_sum(st.tmp)
        st.sc.calls.clear()
        for legacy in (False, True):
            r = st.status(legacy=legacy)
            self.assertIn("UNMANAGED", r.both)
            self.assertIn("stale writer", r.both)
            self.assertIn("mirror mismatch", r.both)
            self.assertEqual(tree_sum(st.tmp), before)
            self.assertEqual(st.sc.verbs(), [])   # systemctl show alone
            self.assertEqual(r.calls, [])         # the daemon's systemctl
            for a in st.hs_calls:
                self.assertEqual(a[a.index("sink"):a.index("sink") + 2],
                                 ["sink", "status"], a)
                self.assertNotIn("sudo", a)
        # Positive control: the same harness sees a write.  The repair that
        # status must not run changes the tree.
        site_path, site, uploads = sink_mirror.read_inputs(
            st.roots.sinks, st.coord_path, st.profile)
        plan = sink_mirror.plan_mirror(site_path, site, uploads)
        self.assertEqual(plan.action, "rewrite")
        self.assertEqual(sink_mirror.apply_mirror(plan, runner=None,
                                                  sinks=st.roots.sinks), [])
        self.assertNotEqual(tree_sum(st.tmp), before)

    def test_before_the_marker_nothing_unmanaged_stale_or_mirrored(self):
        # Scope ruling: before the carry-over these mean nothing.
        st = StatusStation(self, carried=False)
        st.make_stale()
        st.hand_edit_uploads()
        r = st.status()
        self.assertEqual(st.status_calls(), [])
        self.assertEqual(st.sc.calls, [])
        for word in ("UNMANAGED", "stale writer", "mirror"):
            self.assertNotIn(word, r.both)

    def test_an_unreadable_status_falls_back_to_uploads_and_says_so(self):
        st = StatusStation(self)
        r = st.status(runner=_fixture_runner("manifest-unreadable"))
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(len(st.status_calls()), 1)
        self.assertIn("falling back to [uploads]", r.both)
        self.assertIn("site sink: upload (store and send)", r.both)
        self.assertNotIn("effective", r.both)
        # every run says it again (Ruling 19)
        r = st.status(runner=_fixture_runner("manifest-unreadable"))
        self.assertIn("falling back to [uploads]", r.both)

    def test_a_partial_state_shows_its_errors_and_claims_no_empty_refusals(self):
        st = StatusStation(self)
        r = st.status(runner=_fixture_runner("store-unreadable"))
        self.assertEqual(r.rc, 1, r.both)
        self.assertIn("file is not a database", r.both)
        self.assertIn("refusals unknown", r.both)
        self.assertNotIn("refused switch files: none", r.both)
        r = st.status(runner=_fixture_runner("psk-fill"))
        self.assertIn("refused switch files: none", r.both)

    def test_refused_files_are_named(self):
        st = StatusStation(self)
        r = st.status(runner=_fixture_runner("refused"))
        self.assertEqual(r.rc, 1, r.both)
        self.assertIn("store schema too old", r.both)
        self.assertIn("meteor-scatter.toml", r.both)
        self.assertNotIn("refused switch files: none", r.both)

    def test_a_broken_site_file_reads_as_a_fault(self):
        st = StatusStation(self)
        r = st.status(runner=_fixture_runner("broken-site"))
        self.assertEqual(r.rc, 1, r.both)
        self.assertIn("site sink: cannot read", r.both)
        self.assertIn("parse error", r.both)
        self.assertIn("effective off, set by a fault", r.both)


@needs_real
class StatusReviewTests(unittest.TestCase):
    """Task 21 review: part 1's words, and the remaining reader paths."""

    def set_site(self, st, state):
        r = um.run_hs_uploader(
            ["sink", "set", "site", state, "--reason", "rig", "--by", "test",
             "--sinks", str(st.roots.sinks)], python=st.roots.python)
        self.assertEqual(r.returncode, 0, r.stderr)

    def lower_levers(self, st):
        st.env("psk-recorder", "W1AW", "PSK_DELIVERY_PIPELINES=server-raw\n")
        st.env("wspr-recorder", "W1AW", "WSPR_USE_HS_UPLOADER=0\n")

    def test_both_views_keep_part_1s_site_phrases(self):
        # The nested rig greps these (sigmond-appliance test-nested-v3.sh
        # :861 and :1316), whichever view the station shows.
        before, after = StatusStation(self, carried=False), StatusStation(self)
        for st in (before, after):
            self.assertIn("site sink: upload (store and send)", st.status().both)
        self.assertIn("effective", after.status().both)      # the switch view
        before.hand_edit_uploads('[uploads]\nenabled = false\nmode = "discard"\n'
                                 'reason = "rig"\n')
        self.set_site(after, "off")
        for st in (before, after):
            self.assertIn("site sink: off", st.status().both)
        self.set_site(after, "fill")
        self.assertIn("site sink: fill (store and send nothing)",
                      after.status().both)

    def test_not_enforced_is_a_fault(self):
        st = StatusStation(self)
        self.lower_levers(st)
        r = st.status(runner=_fixture_runner("psk-fill"))
        self.assertEqual(r.rc, 0, r.both)              # control: no fault
        r = st.status(runner=_fixture_runner("marker-absent"))
        self.assertIn("does not enforce the switches", r.both)
        self.assertNotIn("UNMANAGED", r.both)
        self.assertEqual(r.rc, 1, r.both)

    def test_upload_status_falls_back_with_exit_1(self):
        st = StatusStation(self)
        r = st.status(legacy=True, runner=_fixture_runner("manifest-unreadable"))
        self.assertIn("falling back to [uploads]", r.both)
        self.assertIn("uploads: on (store and ship)", r.both)
        self.assertEqual(r.rc, 1, r.both)

    def test_a_missing_site_file_reads_as_a_mirror_mismatch_left_alone(self):
        st = StatusStation(self)
        site = st.roots.sinks / "site.toml"
        site.unlink()
        r = st.status()
        self.assertEqual(r.rc, 1, r.both)
        self.assertIn(f"mirror mismatch: the site switch {site} is missing",
                      r.both)
        self.assertIn("recreates it at upload", r.both)
        self.assertFalse(site.exists())

    def test_a_broken_site_file_reads_as_a_mirror_mismatch_left_alone(self):
        st = StatusStation(self)
        site = st.roots.sinks / "site.toml"
        site.write_text("state = \n")
        before = st.coord_path.read_bytes()
        r = st.status()
        self.assertEqual(r.rc, 1, r.both)
        self.assertIn(f"mirror mismatch: the site sink switch {site} cannot be "
                      "read", r.both)
        self.assertEqual(site.read_text(), "state = \n")
        self.assertEqual(st.coord_path.read_bytes(), before)

    def test_an_unreadable_sender_reads_cannot_check_not_unmanaged(self):
        st = StatusStation(self)
        st.set_client("psk-recorder", "fill")
        real_show = st.sc._show

        def show(cmd, rest):
            if "Environment" in rest and any(a.startswith("psk-recorder@")
                                             for a in rest):
                return subprocess.CompletedProcess(cmd, 1, "", "no bus")
            return real_show(cmd, rest)
        st.sc._show = show
        r = st.status()
        self.assertIn("cannot check: psk-recorder@W1AW.service: cannot read "
                      "its environment", r.both)
        self.assertNotIn("UNMANAGED", r.both)
        self.assertEqual(r.rc, 1, r.both)    # unknown arming is still a fault

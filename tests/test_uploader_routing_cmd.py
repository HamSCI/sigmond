"""`smd sink upload`, `smd update --apply`, `smd align --apply` and
`smd admin uploader routing` move psk-recorder and meteor-scatter
instances onto the wsprdaemon.org route (uniform routing, Task 4).

`smd sink fill` and `smd sink off` never run the migration, and a sink
state that cannot be read changes nothing (plan Review Focus 2).
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sigmond import sink_mirror, sink_state
from sigmond import uniform_routing as ur
from sigmond import uploader_manifest as um
from sigmond.commands import config as cfg
from sigmond.commands import uploader as up_cmd

GATE = frozenset({um.GATE_FEATURE, "pipelines.d", "switch-files"})
NO_GATE = frozenset({"pipelines.d", "switch-files"})


def _client(file_state, effective=None):
    return types.SimpleNamespace(file_state=file_state,
                                 effective=effective or file_state, error="")


def _state(site="fill", **clients):
    return types.SimpleNamespace(
        site=types.SimpleNamespace(state=site),
        clients={name.replace("_", "-"): c for name, c in clients.items()})


class _Station(unittest.TestCase):
    """Scratch coordination.toml and site-profile.toml; every step that
    reaches a service, hs-uploader or the manifest is faked, and ``calls``
    records the routing and the manifest step in the order they ran."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        self.coord = root / "coordination.toml"
        self.coord.write_text('[uploads]\nmode = "hold"\n')
        self.profile = root / "site-profile.toml"
        self.profile.write_text('[station]\ncallsign = "W1AW"\n')
        self.roots = types.SimpleNamespace(
            sinks=root / "sinks", marker=root / "carry-over-v1.done",
            python=Path("/nonexistent/python"), manifest=root / "p.toml",
            store=None, etc=root / "etc")
        self.calls = []
        self.routed = []

        def route(lowered, why=""):
            self.calls.append(f"route:{lowered}")
            self.routed.append((lowered, why))
            return 0

        def manifest():
            self.calls.append("manifest")
            return 0

        for p in (
            mock.patch.object(cfg, "COORDINATION_PATH", self.coord),
            mock.patch.object(cfg, "SITE_PROFILE_PATH", self.profile),
            mock.patch.object(cfg, "_route_via_wsprdaemon", side_effect=route),
            mock.patch.object(cfg, "_regenerate_uploader_manifest",
                              side_effect=manifest),
            mock.patch.object(cfg, "_close_doors", return_value=0),
            mock.patch.object(cfg, "_euid", return_value=0),
            mock.patch.object(up_cmd, "_step_roots", return_value=self.roots),
            mock.patch.object(up_cmd, "_state_dir_override", return_value=None),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _start(self, patches, stack):
        for p in patches:
            if stack is not None:
                stack.enter_context(p)
            else:
                p.start()
                self.addCleanup(p.stop)

    def gate(self, *, marker=True, state=None, unreadable=False, stack=None):
        """Fake the gate path's own steps: hs-uploader offers `gate`, the
        marker stands (or not), and the status reads ``state``."""
        self._start((
            mock.patch.object(um, "hs_uploader_features", return_value=GATE),
            mock.patch.object(sink_state, "marker_stands", return_value=marker),
            mock.patch.object(sink_mirror, "read_site",
                              return_value=types.SimpleNamespace(state="fill")),
            mock.patch.object(cfg, "_site_status",
                              return_value=None if unreadable else state),
            mock.patch.object(cfg, "_site_levers", return_value=(0, [], False)),
            mock.patch.object(cfg, "_site_stale", return_value=0),
            mock.patch.object(cfg, "_set_site", return_value=None),
            mock.patch.object(cfg, "_raise_report"),
        ), stack)

    def no_gate(self, stack=None):
        self._start((
            mock.patch.object(um, "hs_uploader_features", return_value=NO_GATE),
            mock.patch.object(um, "hs_uploader_supports_discard",
                              return_value=True),
            mock.patch.object(sink_state, "marker_stands", return_value=False),
        ), stack)

    def verb(self, verb, reason=None):
        args = types.SimpleNamespace(uploads_command=verb, reason=reason,
                                     yes=True, sink_words=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = cfg.cmd_config_uploads(args)
        return rc, out.getvalue()


class SinkUploadTests(_Station):

    def test_sink_upload_routes_before_the_manifest_step(self):
        self.no_gate()
        rc, text = self.verb("on")
        self.assertEqual(rc, 0, text)
        self.assertEqual(self.calls, ["route:frozenset()", "manifest"])

    def test_a_failed_route_on_the_part_1_path_exits_1(self):
        self.no_gate()
        with mock.patch.object(cfg, "_route_via_wsprdaemon", return_value=1):
            rc, text = self.verb("on")
        self.assertEqual(rc, 1, text)

    def test_sink_upload_with_the_gate_routes_with_the_change_below(self):
        state = _state(site="fill", psk_recorder=_client("upload", "fill"),
                       meteor_scatter=_client("fill"))
        self.gate(state=state)
        rc, text = self.verb("on")
        self.assertEqual(rc, 0, text)
        self.assertEqual(self.routed, [(frozenset({"meteor-scatter"}), "")])
        self.assertEqual(self.calls[-1], "manifest")
        self.assertLess(self.calls.index("route:frozenset({'meteor-scatter'})"),
                        self.calls.index("manifest"))

    def test_sink_upload_with_an_unreadable_state_routes_nothing(self):
        self.gate(unreadable=True)
        rc, text = self.verb("on")
        self.assertEqual(rc, 1, text)
        self.assertEqual(self.routed,
                         [(None, "the sink status could not be read")])
        self.assertEqual(self.calls[-1], "manifest")

    def test_sink_upload_before_the_marker_routes_with_nothing_below(self):
        self.gate(marker=False)
        rc, text = self.verb("on")
        self.assertEqual(rc, 0, text)
        self.assertEqual(self.routed, [(frozenset(), "")])

    def test_a_site_switch_that_was_not_written_moves_nothing(self):
        state = _state(site="fill", psk_recorder=_client("upload", "fill"))
        self.gate(state=state)
        with mock.patch.object(cfg, "_set_site", return_value="exit 1: boom"):
            rc, text = self.verb("on")
        self.assertEqual(rc, 1, text)
        self.assertEqual(self.routed,
                         [(None, "the site sink switch was not written")])

    def test_a_coordination_write_that_failed_before_the_marker_moves_nothing(self):
        self.gate(marker=False)
        real = cfg._patch_uploads_block

        def patch(path, up):
            if Path(path) == self.coord:
                raise PermissionError(13, "Permission denied", str(path))
            return real(path, up)
        with mock.patch.object(cfg, "_patch_uploads_block", side_effect=patch):
            rc, text = self.verb("on")
        self.assertEqual(rc, 1, text)
        self.assertEqual(self.routed,
                         [(None, "the site sink switch was not written")])

    def test_sink_fill_never_routes(self):
        for path in ("gate", "no gate"):
            for verb in ("hold", "discard"):
                with self.subTest(path=path, verb=verb):
                    self.calls.clear()
                    self.routed.clear()
                    with contextlib.ExitStack() as stack:
                        if path == "gate":
                            self.gate(state=_state(
                                site="upload",
                                psk_recorder=_client("upload")), stack=stack)
                        else:
                            self.no_gate(stack=stack)
                        rc, text = self.verb(verb, reason="rig")
                    self.assertEqual(self.routed, [], text)
                    self.assertIn("manifest", self.calls)


class RoutingLoweredTests(unittest.TestCase):
    """routing_lowered: the sink state with the gate and the marker, the
    [uploads] mode otherwise."""

    def roots(self):
        return types.SimpleNamespace(
            python=Path("/x/python"), manifest=Path("/x/p.toml"),
            sinks=Path("/x/sinks"), marker=Path("/x/marker"), store=None)

    def lowered(self, *, features, marker, mode="upload", state=None):
        coord = types.SimpleNamespace(uploads=types.SimpleNamespace(mode=mode))
        with mock.patch.object(um, "hs_uploader_features",
                               return_value=features), \
                mock.patch.object(sink_state, "marker_stands",
                                  return_value=marker), \
                mock.patch.object(sink_state, "read_state",
                                  return_value=state) as rs:
            return up_cmd.routing_lowered(self.roots(), coord), rs

    def test_the_gate_and_the_marker_read_the_sink_state(self):
        state = _state(site="upload", psk_recorder=_client("upload"),
                       meteor_scatter=_client("fill"))
        with mock.patch.object(sink_state, "below_upload",
                               return_value=["meteor-scatter"]):
            (lowered, why), rs = self.lowered(features=GATE, marker=True,
                                              state=state)
        self.assertEqual(lowered, frozenset({"meteor-scatter"}))
        self.assertEqual(why, "")
        self.assertEqual(rs.call_count, 1)

    def test_an_unreadable_sink_state_decides_nothing(self):
        (lowered, why), _ = self.lowered(features=GATE, marker=True, state=None)
        self.assertIsNone(lowered)
        self.assertTrue(why)

    def test_a_failed_probe_under_the_marker_decides_nothing(self):
        # Final fix F6: [uploads] no longer rules once the marker stands.
        for mode in ("upload", "hold"):
            with self.subTest(mode=mode):
                (lowered, why), rs = self.lowered(features=None, marker=True,
                                                  mode=mode)
                self.assertIsNone(lowered)
                self.assertIn("features probe failed", why)
                self.assertIn("/x/marker", why)
                rs.assert_not_called()

    def test_without_the_gate_or_the_marker_the_uploads_mode_decides(self):
        for features, marker in ((GATE, False), (NO_GATE, True), (None, False)):
            with self.subTest(features=features, marker=marker):
                (lowered, _), rs = self.lowered(features=features,
                                                marker=marker, mode="upload")
                self.assertEqual(lowered, frozenset())
                rs.assert_not_called()
                (lowered, _), _ = self.lowered(features=features,
                                               marker=marker, mode="hold")
                self.assertEqual(lowered, frozenset(ur.ROUTED_CLIENTS))


class RouteViaWsprdaemonTests(unittest.TestCase):

    def test_prints_each_line_and_returns_the_result(self):
        for ok_, lines, rc in (
                (True, ["routing: 2 instance(s) already deliver via "
                        "wsprdaemon.org"], 0),
                (False, ["routing: ERROR psk-recorder@X posts from this "
                         "station"], 1)):
            with self.subTest(ok=ok_):
                res = types.SimpleNamespace(ok=ok_, lines=lambda l=lines: l)
                with mock.patch.object(up_cmd, "_migrate",
                                       return_value=res) as mig, \
                        mock.patch.object(cfg, "err") as e, \
                        mock.patch.object(cfg, "info") as i:
                    self.assertEqual(
                        cfg._route_via_wsprdaemon(frozenset(), "why"), rc)
                mig.assert_called_once_with(frozenset(), "why", apply=True)
                printed = [c.args[0] for c in (e if not ok_ else i).call_args_list]
                self.assertEqual(printed, lines)


    def test_a_migration_that_raises_is_a_failure_not_a_crash(self):
        with mock.patch.object(up_cmd, "_migrate",
                               side_effect=RuntimeError("boom")), \
                mock.patch.object(cfg, "err") as e:
            self.assertEqual(cfg._route_via_wsprdaemon(frozenset()), 1)
        self.assertIn("boom", e.call_args.args[0])


class MigrateWiringTests(unittest.TestCase):

    def test_migrate_reads_unit_state_through_the_lever_systemctl(self):
        import subprocess as sp
        seen, got = [], {}

        def systemctl(cmd, **kw):
            seen.append(cmd)
            return sp.CompletedProcess(
                cmd, 0, "ActiveState=failed\nActiveEnterTimestamp=\n", "")

        def migrate(topology, **kw):
            got.update(kw)
            return ur.RoutingResult(applied=True)
        roots = types.SimpleNamespace(topology=Path("/nonexistent/t.toml"),
                                      etc=Path("/nonexistent"))
        with mock.patch.object(up_cmd, "_step_roots", return_value=roots), \
                mock.patch.object(up_cmd, "_lever_systemctl", systemctl), \
                mock.patch.object(ur, "migrate_routing", side_effect=migrate):
            up_cmd._migrate(frozenset(), apply=True)
        self.assertEqual(got["unit_state"]("psk-recorder@X.service"),
                         ("failed", None))
        self.assertEqual(seen[0][:2], ["systemctl", "show"])
        self.assertEqual(got["shim_py"], ur.WSPR_SHIM)      # final fix F3


class RoutingVerbTests(unittest.TestCase):

    def setUp(self):
        self.calls = []
        self.applies = []
        self.result = ur.RoutingResult(applied=True)

        def migrate(topology, *, lowered, apply=True, **kw):
            self.calls.append("migrate")
            self.applies.append(apply)
            self.result.applied = apply
            return self.result

        def manifest(args):
            self.calls.append("manifest write" if args.write else "manifest")
            self.assertFalse(args.enable)
            return 0

        roots = types.SimpleNamespace(topology=Path("/nonexistent/t.toml"),
                                      coordination=Path("/nonexistent/c.toml"),
                                      etc=Path("/nonexistent"))
        for p in (
            mock.patch.object(ur, "migrate_routing", side_effect=migrate),
            mock.patch.object(up_cmd, "cmd_uploader_manifest",
                              side_effect=manifest),
            mock.patch.object(up_cmd, "routing_lowered",
                              return_value=(frozenset(), "")),
            mock.patch.object(up_cmd, "_step_roots", return_value=roots),
            mock.patch.object(sink_state, "update_notice", return_value=None),
        ):
            p.start()
            self.addCleanup(p.stop)

    def run_verb(self, *, apply, euid=0):
        out, errs = io.StringIO(), io.StringIO()
        with mock.patch.object(up_cmd, "_euid", return_value=euid), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(errs):
            rc = up_cmd.cmd_uploader_routing(types.SimpleNamespace(apply=apply))
        return rc, out.getvalue(), errs.getvalue()

    def test_routing_verb_dry_run_writes_nothing(self):
        rc, out, _ = self.run_verb(apply=False, euid=1000)
        self.assertEqual(rc, 0)
        self.assertEqual(self.applies, [False])
        self.assertEqual(self.calls, ["migrate"])
        self.assertIn("routing: no psk-recorder or meteor-scatter", out)

    def test_routing_verb_apply_renders_after_migrating(self):
        rc, out, err = self.run_verb(apply=True)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.calls, ["migrate", "manifest write"])
        self.assertEqual(self.applies, [True])

    def test_routing_verb_apply_needs_root(self):
        rc, out, err = self.run_verb(apply=True, euid=1000)
        self.assertEqual(rc, 1)
        self.assertEqual(self.calls, [])
        self.assertIn("root", err)
        self.assertIn("`smd admin uploader routing --apply`", err)
        self.assertNotIn("sudo", err)

    def test_routing_verb_defers_the_render_without_a_marker(self):
        with mock.patch.object(sink_state, "update_notice",
                               return_value=sink_state.UPDATE_NOTICE):
            rc, out, _ = self.run_verb(apply=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, ["migrate"])
        self.assertIn("routing: the manifest render waits for the next "
                      "`smd align` (no carry-over marker yet)", out)

    def test_a_failed_migration_or_render_exits_1(self):
        self.result.no_carrier.append("psk-recorder@W1AW.service")
        rc, out, err = self.run_verb(apply=True)
        self.assertEqual(rc, 1)
        self.assertIn("ERROR psk-recorder@W1AW.service", out + err)
        self.assertEqual(self.calls, ["migrate", "manifest write"])
        self.result.no_carrier.clear()
        with mock.patch.object(up_cmd, "cmd_uploader_manifest", return_value=1):
            rc, _, _ = self.run_verb(apply=True)
        self.assertEqual(rc, 1)

    def test_an_unreadable_sink_state_changes_nothing_and_exits_1(self):
        def migrate(topology, *, lowered, apply=True, undecided_why="", **kw):
            self.calls.append("migrate")
            self.assertIsNone(lowered)
            return ur.RoutingResult(applied=apply, undecided=undecided_why)
        with mock.patch.object(up_cmd, "routing_lowered",
                               return_value=(None, "status timed out")), \
                mock.patch.object(ur, "migrate_routing", side_effect=migrate):
            rc, out, err = self.run_verb(apply=True)
        self.assertEqual(rc, 1)
        self.assertIn("status timed out", out + err)


# ---------------------------------------------------------------------------
# `smd update --apply` and `smd align --apply` run the routing in a child
# ---------------------------------------------------------------------------

from test_update_executor import _Rig, smd  # noqa: E402

# _Rig stubs the routing step; these tests run the real one down to the
# child-smd call.
_REAL_UPDATE_ROUTING = smd._update_routing

ROUTING_ARGV = ["admin", "uploader", "routing", "--apply"]


class UpdateRoutingTests(_Rig):

    def setUp(self):
        super().setUp()
        self.recorded = []
        self.child_rc = 0

        def child(argv_tail, log_target, what, **kw):
            self.recorded.append(list(argv_tail))
            return self.child_rc

        for p in (mock.patch.object(smd, "_update_routing",
                                    _REAL_UPDATE_ROUTING),
                  mock.patch.object(smd, "_run_smd_child", side_effect=child)):
            p.start()
            self.addCleanup(p.stop)
        # One applied pull and no install: the fake component has no
        # installer worth running here.
        p = mock.patch.object(smd, "_align_run_install", return_value=0)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(sink_state, "update_notice", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def run_update(self, *, apply):
        args = types.SimpleNamespace(base=self.base, apply=apply,
                                     no_fetch=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = smd.cmd_update(args)
        return rc, out.getvalue()

    def test_update_apply_runs_the_routing_child(self):
        rc, text = self.run_update(apply=True)
        self.assertIn("[pull]", text)
        self.assertEqual(self.recorded, [ROUTING_ARGV], text)
        self.assertEqual(rc, 0, text)

    def test_a_failed_routing_child_makes_update_exit_1(self):
        self.child_rc = 1
        rc, text = self.run_update(apply=True)
        self.assertEqual(self.recorded, [ROUTING_ARGV])
        self.assertEqual(rc, 1, text)
        self.assertIn("routing: exit 1; re-run: smd admin uploader "
                      "routing --apply", text)
        self.assertNotIn("sudo smd", text)

    def test_update_with_nothing_runnable_still_routes(self):
        from test_update_executor import _git
        _git("pull", "--ff-only", cwd=self.host)
        rc, text = self.run_update(apply=True)
        self.assertNotIn("[pull]", text)
        self.assertEqual(self.recorded, [ROUTING_ARGV], text)
        self.assertEqual(rc, 0, text)
        self.child_rc = 1
        rc, text = self.run_update(apply=True)
        self.assertEqual(rc, 1, text)
        self.assertIn("re-run: smd admin uploader routing --apply", text)

    def test_update_dry_run_with_nothing_runnable_never_routes(self):
        from test_update_executor import _git
        _git("pull", "--ff-only", cwd=self.host)
        self.run_update(apply=False)
        self.assertEqual(self.recorded, [])

    def test_update_dry_run_never_routes(self):
        rc, text = self.run_update(apply=False)
        self.assertIn("[pull]", text)
        self.assertEqual(self.recorded, [])

    def test_a_failed_step_skips_the_routing(self):
        from test_update_executor import _git
        _git("fetch", cwd=self.host)
        _git("remote", "set-url", "origin", "/nonexistent/origin.git",
             cwd=self.host)
        rc, text = self.run_update(apply=True)
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.recorded, [])


class AlignRoutingStepTests(unittest.TestCase):
    """`_align_routing`: the step `smd align --apply` runs after its
    bring-up re-run, through the same child-smd route as `smd update`."""

    def run_step(self, rc):
        recorded = []

        def child(argv_tail, log_target, what, **kw):
            recorded.append(list(argv_tail))
            return rc
        out = io.StringIO()
        with mock.patch.object(smd, "_run_smd_child", side_effect=child), \
                contextlib.redirect_stdout(out):
            step = smd._align_routing()
        return step, recorded

    def test_runs_the_routing_child(self):
        step, recorded = self.run_step(0)
        self.assertEqual(recorded, [ROUTING_ARGV])
        self.assertEqual(step.outcome, "ran")

    def test_a_failed_child_fails_the_step_and_names_the_rerun(self):
        step, _ = self.run_step(2)
        self.assertEqual(step.outcome, "failed")
        self.assertIn("exit 2", step.detail)
        self.assertIn("re-run: smd admin uploader routing --apply", step.detail)
        self.assertNotIn("sudo", step.detail)
        self.assertIn(step.outcome, smd._ALIGN_BLOCKING)


class AlignApplyRoutesTests(unittest.TestCase):
    """`smd align --apply` runs the routing step after its bring-up re-run
    and the record; a failed routing step exits 1 (controller ruling R2)."""

    def align(self, outcome, *, no_restart=False, bringup_fails=False):
        import test_align_cli as tac
        from sigmond import align_apply
        order = []
        bringup = ([align_apply.Step("admin uploader manifest --write", "failed")]
                   if bringup_fails else [])
        m_bringup = mock.Mock(side_effect=lambda **kw:
                              order.append("bringup") or list(bringup))
        m_routing = mock.Mock(side_effect=lambda: order.append("routing") or
                              align_apply.Step("admin uploader routing --apply",
                                               outcome))
        moves = [align_apply.Step("sigmond", "current"),
                 align_apply.Step("hf-timestd", "moved", "a -> b", 10)]
        out = io.StringIO()
        with contextlib.ExitStack() as st:
            m_record, _ = tac._apply_patches(
                st, rel=tac.APPLY_REL, live=tac.LIVE_ALIGNED,
                _align_bringup=m_bringup, _align_routing=m_routing)
            m_record.side_effect = lambda *a, **k: order.append("record")
            st.enter_context(mock.patch("sigmond.align_apply.apply_plan",
                                        return_value=moves))
            st.enter_context(mock.patch("sigmond.provenance.record_update"))
            st.enter_context(contextlib.redirect_stdout(out))
            rc = tac.smd.cmd_align(tac._apply_args("/opt/git/sigmond",
                                                   no_restart=no_restart))
        return rc, order, out.getvalue()

    def test_align_apply_routes_after_the_bringup(self):
        for no_restart in (False, True):
            with self.subTest(no_restart=no_restart):
                rc, order, text = self.align("ran", no_restart=no_restart)
                self.assertEqual(rc, 0, text)
                self.assertEqual(order, ["bringup", "record", "routing"])
                self.assertIn("admin uploader routing --apply", text)

    def test_a_failed_routing_step_makes_align_exit_1(self):
        for no_restart in (False, True):
            with self.subTest(no_restart=no_restart):
                rc, order, text = self.align("failed", no_restart=no_restart)
                self.assertEqual(rc, 1, text)
                self.assertIn("routing", order)

    def align_noop(self, outcome):
        """A run with nothing to do: every component current, aligned.json
        naming this release with a live stage that passed, nothing stale."""
        import test_align_cli as tac
        from sigmond import align_apply
        order = []
        m_routing = mock.Mock(side_effect=lambda: order.append("routing") or
                              align_apply.Step("admin uploader routing --apply",
                                               outcome))
        m_bringup = mock.Mock(side_effect=lambda **kw:
                              order.append("bringup") or [])
        out = io.StringIO()
        with contextlib.ExitStack() as st:
            tac._apply_patches(
                st, rel=tac.APPLY_REL, live=tac.LIVE_ALIGNED,
                _align_routing=m_routing, _align_bringup=m_bringup,
                _align_aligned_release=lambda: tac.APPLY_REL.tag,
                _align_aligned_live=lambda: dict(tac._LIVE_PASSED))
            st.enter_context(mock.patch(
                "sigmond.align_apply.apply_plan",
                return_value=[align_apply.Step("sigmond", "current"),
                              align_apply.Step("hf-timestd", "current")]))
            st.enter_context(contextlib.redirect_stdout(out))
            rc = tac.smd.cmd_align(tac._apply_args("/opt/git/sigmond"))
        return rc, order, out.getvalue()

    def test_align_with_nothing_to_do_still_routes(self):
        rc, order, text = self.align_noop("ran")
        self.assertIn("aligned — nothing to do", text)
        self.assertEqual(order, ["routing"])
        self.assertEqual(rc, 0, text)
        rc, order, text = self.align_noop("failed")
        self.assertEqual(order, ["routing"])
        self.assertEqual(rc, 1, text)

    def test_a_failed_bringup_never_routes(self):
        rc, order, _ = self.align("ran", bringup_fails=True)
        self.assertEqual(rc, 1)
        self.assertNotIn("routing", order)


class ParserTests(unittest.TestCase):

    def test_admin_uploader_routing_parses_apply(self):
        parser = smd._build_parser()
        args = parser.parse_args(["admin", "uploader", "routing", "--apply"])
        self.assertEqual(args.uploader_command, "routing")
        self.assertTrue(args.apply)
        args = parser.parse_args(["admin", "uploader", "routing"])
        self.assertFalse(args.apply)


if __name__ == "__main__":
    unittest.main()

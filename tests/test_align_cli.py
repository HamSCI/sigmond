"""CLI tests for `smd align` — every probe patched; no network, no git."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from sigmond import align, align_apply

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_align", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_align", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()
REL = align.Release(tag="v3.53", manifest_text="", appliance_commit=None,
                    components={"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                "ka9q-radio": "401992c"})


def run(live, dirty=None, files=None, recorded="v3.36", release=REL, no_cost=True,
       origins=None, errors=None, **ns):
    args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=no_cost, **ns)
    patches = [
        mock.patch.object(smd, "_align_live_state",
                          return_value=(live, dirty or {}, origins or {}, errors or {})),
        mock.patch("sigmond.align.image_file_drift", return_value=files or [
            {"path": "/usr/local/sbin/sigmond-site-timing", "status": "current", "note": ""}]),
        mock.patch("sigmond.align.recorded_release", return_value=recorded),
    ]
    if isinstance(release, Exception):
        patches.append(mock.patch("sigmond.align.fetch_release", side_effect=release))
    else:
        patches.append(mock.patch("sigmond.align.fetch_release", return_value=release))
    out = io.StringIO()
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        st.enter_context(contextlib.redirect_stdout(out))
        rc = smd.cmd_align(args)
    return rc, out.getvalue()


class AlignCliTests(unittest.TestCase):
    def test_aligned_station_exits_0(self):
        rc, out = run({"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      recorded="v3.53")
        self.assertEqual(rc, 0)
        self.assertIn("against blessed v3.53", out)
        self.assertIn("nothing changes", out)

    def test_moves_exit_1_and_flag_radiod(self):
        rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "deb7bdd"})
        self.assertEqual(rc, 1)
        self.assertIn("459bee6 -> daba1f6", out)
        self.assertIn("RESTARTS radiod", out)
        self.assertIn("check it with pm-align (Plan 3)", out)

    def test_differing_image_file_exits_1(self):
        rc, out = run({"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      files=[{"path": "/usr/local/sbin/sigmond-site-timing",
                              "status": "differs", "note": "differs from v3.53"}],
                      recorded="v3.53")
        self.assertEqual(rc, 1)
        self.assertIn("1 file to refresh", out)

    def test_lookup_failure_exits_2(self):
        rc, out = run({}, release=align.LookupError_("could not reach api.github.com"))
        self.assertEqual(rc, 2)
        self.assertIn("could not reach", out)

    def test_dry_run_cannot_move_anything(self):
        # Structural guard: the dry run has no way to change a station. align.py
        # never imports subprocess, and cmd_align names no git mutation. Adding
        # either on purpose (Plan 2) must come with its own tests.
        import inspect
        src = inspect.getsource(align)
        self.assertNotIn("import subprocess", src)
        self.assertNotIn("subprocess.", src)
        body = inspect.getsource(smd.cmd_align)
        for word in ("checkout", "'fetch'", '"fetch"', "install.sh", "systemctl", "_run_git"):
            self.assertNotIn(word, body)

    # --- Fix round 1 -----------------------------------------------------

    def test_unverified_image_file_blocks_exit_0(self):
        """An 'unknown' image-file status (probe unreachable) must not let
        the run silently claim alignment — exit 1, and name the file(s)."""
        rc, out = run({"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      files=[{"path": "/usr/local/sbin/sigmond-site-timing",
                              "status": "unknown", "note": "could not reach raw.githubusercontent.com"},
                             {"path": "/usr/local/sbin/sigmond-location-check",
                              "status": "unknown", "note": "could not reach raw.githubusercontent.com"}],
                      recorded="v3.53")
        self.assertEqual(rc, 1)
        self.assertIn("could not be checked", out)
        self.assertIn("/usr/local/sbin/sigmond-site-timing", out)
        self.assertIn("/usr/local/sbin/sigmond-location-check", out)

    def test_no_cost_false_shows_commit_distance(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond")}
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance",
                        return_value={"ahead": 39, "behind": 0, "files": 51}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False)
        self.assertIn("behind by 39, 51 files", out)

    def test_no_cost_false_catalog_unreadable_no_crash(self):
        with mock.patch("sigmond.catalog.load_catalog", side_effect=ValueError("bad toml")):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False)
        self.assertEqual(rc, 1)
        self.assertIn("catalog unreadable", out)

    def test_no_cost_false_commit_distance_error_shows_unknown(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond")}
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance", return_value={"error": "no route"}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False)
        self.assertIn("distance unknown", out)

    def test_origin_fallback_when_catalog_has_no_repo(self):
        """sigmond has no catalog entry/repo; _align_live_state's origin-URL
        fallback should still let a commit distance be computed."""
        with mock.patch("sigmond.catalog.load_catalog", return_value={}), \
             mock.patch("sigmond.align.commit_distance",
                        return_value={"ahead": 39, "behind": 0, "files": 51}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False,
                          origins={"sigmond": "https://github.com/HamSCI/sigmond"})
        self.assertIn("behind by 39, 51 files", out)

    def test_commit_distance_singular_grammar(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond")}
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance",
                        return_value={"ahead": 1, "behind": 0, "files": 1}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False)
        self.assertIn("behind by 1, 1 file", out)
        self.assertNotIn("1 files", out)

    def test_refuse_row_shows_shas(self):
        rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      dirty={"sigmond": True})
        self.assertIn("459bee6 -> daba1f6   refuse", out)

    # --- Fix round 2 -----------------------------------------------------

    def test_move_direction_behind(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond")}
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance",
                        return_value={"ahead": 5, "behind": 0, "files": 3}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False)
        self.assertIn("behind by 5, 3 files", out)

    def test_move_direction_ahead_would_roll_back(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond")}
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance",
                        return_value={"ahead": 0, "behind": 4, "files": 2}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False)
        self.assertIn("AHEAD by 4 — would roll back", out)

    def test_move_direction_diverged(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond")}
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance",
                        return_value={"ahead": 3, "behind": 2, "files": 7}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False)
        self.assertIn("diverged (3/2)", out)

    def test_move_direction_files_unknown(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond")}
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance",
                        return_value={"ahead": 5, "behind": 0, "files": None}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False)
        self.assertIn("behind by 5, files ?", out)

    def test_install_time_release_label(self):
        rc, out = run({"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      recorded="v3.53")
        self.assertIn("install-time release: v3.53   (the image this VM was first installed "
                      "from)", out)
        self.assertNotIn("station recorded release", out)

    def test_recorded_differs_names_proxmox_host_not_measured(self):
        rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      recorded="v3.36")
        self.assertIn("Proxmox host: level not measured from here — check it with pm-align "
                      "(Plan 3)", out)
        self.assertNotIn("host: recorded", out)

    def test_error_entry_would_move_refuses(self):
        rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      dirty={"sigmond": True}, errors={"sigmond": "not a git repository"})
        self.assertIn("state unreadable: not a git repository", out)

    def test_rate_limit_stops_further_compares(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond"),
                  "hf-timestd": types.SimpleNamespace(repo="https://github.com/HamSCI/hf-timestd")}
        calls = mock.Mock(side_effect=[
            {"error": "GitHub API rate limit reached (resets 03:15Z)"},
            {"ahead": 1, "behind": 0, "files": 1}])
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance", calls):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "4595c00",
                          "ka9q-radio": "401992c"}, no_cost=False)
        self.assertEqual(calls.call_count, 1)
        self.assertIn("commit distances skipped: GitHub API rate limit reached "
                      "(resets 03:15Z)", out)


class AlignLiveStateTests(unittest.TestCase):
    """Exercises _align_live_state against REAL git checkouts (never
    /opt/git) — the one place this fix wave requires real git behaviour."""

    def _git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True,
                       capture_output=True, text=True)

    def test_unreadable_head_maps_to_none_and_readable_head_is_a_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)

            good = base / "good-comp"
            good.mkdir()
            self._git("init", "-q", cwd=good)
            self._git("config", "user.name", "Test", cwd=good)
            self._git("config", "user.email", "test@example.com", cwd=good)
            (good / "f.txt").write_text("x")
            self._git("add", "f.txt", cwd=good)
            self._git("commit", "-q", "-m", "init", cwd=good)

            broken = base / "broken-comp"
            broken.mkdir()
            (broken / ".git").write_text("gitdir: /nonexistent/nowhere\n")

            live, dirty, origins, errors = smd._align_live_state(str(base))

            self.assertIsNotNone(live["good-comp"])
            self.assertIsNone(live["broken-comp"])
            self.assertIn("broken-comp", errors)
            self.assertFalse(dirty["good-comp"])
            self.assertTrue(dirty["broken-comp"])


# ---------------------------------------------------------------------------
# --apply — Plan 2a: verify, classify, sigmond bootstrap, apply, record
# ---------------------------------------------------------------------------

APPLY_REL = align.Release(tag="v3.53", manifest_text="manifest text",
                          appliance_commit="a" * 40,
                          components={"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                      "ka9q-radio": "401992c"})


def run_apply(*, apply=True, allow_rollback=False, max_bytes=None, release=APPLY_REL,
             live=None, need_root=False, verify_error=None, ancestor=lambda c, a, b: True,
             apply_plan_result=None, target_has_align=True, base="/opt/git/sigmond",
             no_cost=True):
    """Drives `smd align --apply` (or the flag-without-apply guard) with
    every side-effecting collaborator patched. Returns (rc, out, mocks) —
    `mocks` is the dict of the patched callables, for call-order/arg
    assertions."""
    live = live if live is not None else {"sigmond": "459bee6", "hf-timestd": "5c8196d",
                                          "ka9q-radio": "401992c"}
    args = argparse.Namespace(release=None, base=base, no_cost=no_cost, apply=apply,
                              allow_rollback=allow_rollback, max_bytes=max_bytes)

    m_need_root = mock.Mock(return_value=need_root)
    m_lock = mock.Mock(side_effect=lambda reason=None: contextlib.nullcontext())
    m_live_state = mock.Mock(return_value=(live, {}, {}, {}))
    m_ancestry = mock.Mock(return_value=ancestor)
    m_apply_plan = mock.Mock(return_value=apply_plan_result if apply_plan_result is not None else [])
    m_record = mock.Mock()
    m_target_has_align = mock.Mock(return_value=target_has_align)
    m_execv = mock.Mock()
    m_verify = mock.Mock(side_effect=verify_error) if verify_error else mock.Mock(return_value=None)

    mocks = dict(need_root=m_need_root, lock=m_lock, live_state=m_live_state,
                ancestry=m_ancestry, apply_plan=m_apply_plan, record=m_record,
                target_has_align=m_target_has_align, execv=m_execv, verify=m_verify)

    patches = [
        mock.patch.object(smd, "_need_root", m_need_root),
        mock.patch.object(smd, "lifecycle_lock", m_lock),
        mock.patch.object(smd, "_align_live_state", m_live_state),
        mock.patch.object(smd, "_align_ancestry", m_ancestry),
        mock.patch.object(smd, "_align_target_has_align", m_target_has_align),
        mock.patch("sigmond.align_apply.verify_release", m_verify),
        mock.patch("sigmond.align_apply.apply_plan", m_apply_plan),
        mock.patch("sigmond.align_apply.record", m_record),
        mock.patch("sigmond.catalog.load_catalog", return_value={}),
        mock.patch("os.execv", m_execv),
    ]
    if isinstance(release, Exception):
        patches.append(mock.patch("sigmond.align.fetch_release", side_effect=release))
    else:
        patches.append(mock.patch("sigmond.align.fetch_release", return_value=release))

    out = io.StringIO()
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        st.enter_context(contextlib.redirect_stdout(out))
        rc = smd.cmd_align(args)
    return rc, out.getvalue(), mocks


class AlignApplyFlagTests(unittest.TestCase):
    def test_allow_rollback_without_apply_exits_2(self):
        rc, out, mocks = run_apply(apply=False, allow_rollback=True)
        self.assertEqual(rc, 2)
        self.assertIn("only apply with --apply", out)
        mocks["need_root"].assert_not_called()

    def test_max_bytes_without_apply_exits_2(self):
        rc, out, mocks = run_apply(apply=False, max_bytes=1024)
        self.assertEqual(rc, 2)
        self.assertIn("only apply with --apply", out)

    def test_dry_run_still_calls_none_of_the_apply_machinery(self):
        # Without --apply, cmd_align must not touch _need_root, lifecycle_lock,
        # apply_plan or record — the dry run stays a dry run.
        rc, out, mocks = run_apply(apply=False)
        mocks["need_root"].assert_not_called()
        mocks["lock"].assert_not_called()
        mocks["apply_plan"].assert_not_called()
        mocks["record"].assert_not_called()

    def test_max_bytes_parses_suffix(self):
        parsed = smd._parse_bytes("20M")
        self.assertEqual(parsed, 20971520)

    def test_max_bytes_parses_plain_int(self):
        self.assertEqual(smd._parse_bytes("500"), 500)

    def test_max_bytes_parses_k_and_g(self):
        self.assertEqual(smd._parse_bytes("500K"), 512000)
        self.assertEqual(smd._parse_bytes("1G"), 1073741824)

    def test_max_bytes_case_insensitive(self):
        self.assertEqual(smd._parse_bytes("20m"), 20971520)

    def test_max_bytes_rejects_garbage(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            smd._parse_bytes("banana")


class AlignApplyOrderTests(unittest.TestCase):
    def test_apply_calls_need_root_before_fetch_release(self):
        calls = []
        with mock.patch.object(smd, "_need_root",
                               mock.Mock(side_effect=lambda *a: calls.append("need_root") or False)), \
             mock.patch("sigmond.align.fetch_release",
                        mock.Mock(side_effect=lambda *a, **k: calls.append("fetch_release") or APPLY_REL)), \
             mock.patch.object(smd, "lifecycle_lock", lambda reason=None: contextlib.nullcontext()), \
             mock.patch.object(smd, "_align_live_state",
                               return_value=({"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                             "ka9q-radio": "401992c"}, {}, {}, {})), \
             mock.patch("sigmond.align_apply.verify_release", return_value=None), \
             mock.patch.object(smd, "_align_ancestry", return_value=lambda c, a, b: True), \
             mock.patch("sigmond.align_apply.apply_plan", return_value=[]), \
             mock.patch("sigmond.align_apply.record"), \
             mock.patch("sigmond.catalog.load_catalog", return_value={}):
            args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=True,
                                      apply=True, allow_rollback=False, max_bytes=None)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                smd.cmd_align(args)
        self.assertEqual(calls, ["need_root", "fetch_release"])

    def test_need_root_refusing_stops_before_fetch_release(self):
        rc, out, mocks = run_apply(need_root=True)
        self.assertEqual(rc, 1)
        mocks["verify"].assert_not_called()
        mocks["apply_plan"].assert_not_called()

    def test_verify_failure_exits_2_and_never_calls_apply_plan(self):
        rc, out, mocks = run_apply(verify_error=align_apply.ApplyError("tag not found"))
        self.assertEqual(rc, 2)
        mocks["apply_plan"].assert_not_called()
        self.assertIn("tag not found", out)


class AlignApplySigmondBootstrapTests(unittest.TestCase):
    def test_sigmond_forward_target_has_align_execs(self):
        boot_step = align_apply.Step("sigmond", "moved", "459bee6 -> daba1f6", 100)
        rc, out, mocks = run_apply(
            live={"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
            apply_plan_result=[boot_step], target_has_align=True)
        mocks["execv"].assert_called_once()
        exec_args = mocks["execv"].call_args[0][1]
        self.assertIn("--release", exec_args)
        self.assertIn("v3.53", exec_args)
        self.assertIn("align", exec_args)
        self.assertIn("--apply", exec_args)

    def test_sigmond_forward_target_lacks_align_continues_without_exec(self):
        boot_step = align_apply.Step("sigmond", "moved", "459bee6 -> daba1f6", 100)
        other_steps = [align_apply.Step("hf-timestd", "current"),
                      align_apply.Step("ka9q-radio", "current")]
        # First apply_plan call (sigmond alone) returns the boot step; the
        # second (the rest) returns other_steps.
        m_apply_plan = mock.Mock(side_effect=[[boot_step], other_steps])
        with mock.patch("sigmond.align_apply.apply_plan", m_apply_plan), \
             mock.patch.object(smd, "_need_root", return_value=False), \
             mock.patch.object(smd, "lifecycle_lock", lambda reason=None: contextlib.nullcontext()), \
             mock.patch.object(smd, "_align_live_state",
                               return_value=({"sigmond": "459bee6", "hf-timestd": "5c8196d",
                                             "ka9q-radio": "401992c"}, {}, {}, {})), \
             mock.patch("sigmond.align.fetch_release", return_value=APPLY_REL), \
             mock.patch("sigmond.align_apply.verify_release", return_value=None), \
             mock.patch.object(smd, "_align_ancestry", return_value=lambda c, a, b: True), \
             mock.patch.object(smd, "_align_target_has_align", return_value=False), \
             mock.patch("sigmond.align_apply.record"), \
             mock.patch("sigmond.catalog.load_catalog", return_value={}), \
             mock.patch("os.execv") as m_execv:
            args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=True,
                                      apply=True, allow_rollback=False, max_bytes=None)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = smd.cmd_align(args)
        m_execv.assert_not_called()
        self.assertIn("predates smd align", out.getvalue())
        self.assertEqual(m_apply_plan.call_count, 2)


class AlignApplyExitCodeTests(unittest.TestCase):
    def test_all_moved_exits_0(self):
        steps = [align_apply.Step("sigmond", "current"),
                align_apply.Step("hf-timestd", "moved", "a -> b", 10),
                align_apply.Step("ka9q-radio", "current")]
        rc, out, mocks = run_apply(
            live={"sigmond": "daba1f6", "hf-timestd": "459bee6", "ka9q-radio": "401992c"},
            apply_plan_result=steps)
        self.assertEqual(rc, 0)

    def test_a_failed_step_exits_1(self):
        steps = [align_apply.Step("sigmond", "current"),
                align_apply.Step("hf-timestd", "failed", "git fetch failed"),
                align_apply.Step("ka9q-radio", "skipped", "stopped after hf-timestd failed")]
        rc, out, mocks = run_apply(
            live={"sigmond": "daba1f6", "hf-timestd": "459bee6", "ka9q-radio": "401992c"},
            apply_plan_result=steps)
        self.assertEqual(rc, 1)

    def test_record_called_with_the_final_steps(self):
        steps = [align_apply.Step("sigmond", "current")]
        rc, out, mocks = run_apply(
            live={"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
            apply_plan_result=steps)
        mocks["record"].assert_called_once()
        _, kwargs = mocks["record"].call_args
        self.assertEqual(kwargs["manifest_path"], smd.MANIFEST_PATH)


class AlignAncestryFactoryTests(unittest.TestCase):
    """_align_ancestry: fetch-at-most-once, the prefetch budget (ruling 2),
    and a fetch failure never crashing the run (ruling 3)."""

    def test_fetches_each_component_at_most_once(self):
        prefetched = {}
        calls = []
        with mock.patch("sigmond.align_apply.fetch",
                        mock.Mock(side_effect=lambda repo, owner, **k: calls.append(str(repo)) or 10)), \
             mock.patch("sigmond.align_apply.is_ancestor", return_value=True), \
             mock.patch.object(Path, "owner", return_value="sigmond"):
            is_ancestor = smd._align_ancestry("/opt/git/sigmond", prefetched, None, print)
            is_ancestor("hf-timestd", "a", "b")   # forward direction
            is_ancestor("hf-timestd", "b", "a")   # backward direction
        self.assertEqual(len(calls), 1)
        self.assertEqual(prefetched["hf-timestd"], 10)

    def test_budget_stops_fetching_further_components(self):
        prefetched = {}
        say = mock.Mock()
        fetch_calls = []
        with mock.patch("sigmond.align_apply.fetch",
                        mock.Mock(side_effect=lambda repo, owner, **k:
                                  fetch_calls.append(str(repo)) or 1000)), \
             mock.patch("sigmond.align_apply.is_ancestor", return_value=True), \
             mock.patch.object(Path, "owner", return_value="sigmond"):
            is_ancestor = smd._align_ancestry("/opt/git/sigmond", prefetched, 500, say)
            is_ancestor("hf-timestd", "a", "b")
            is_ancestor("hf-timestd", "b", "a")
            is_ancestor("ka9q-radio", "a", "b")
            is_ancestor("ka9q-radio", "b", "a")
        # Only the first component's fetch actually ran; the second is over
        # budget by the time it's reached and must not be fetched at all.
        self.assertEqual(fetch_calls, ["/opt/git/sigmond/hf-timestd"])

    def test_fetch_failure_says_and_returns_none_without_crashing(self):
        prefetched = {}
        say = mock.Mock()
        with mock.patch("sigmond.align_apply.fetch",
                        side_effect=align_apply.ApplyError("network unreachable")), \
             mock.patch.object(Path, "owner", return_value="sigmond"):
            is_ancestor = smd._align_ancestry("/opt/git/sigmond", prefetched, None, say)
            result = is_ancestor("hf-timestd", "a", "b")
        self.assertIsNone(result)
        say.assert_called_once()
        self.assertIn("hf-timestd: fetch failed: network unreachable", say.call_args[0][0])


class AlignTargetHasAlignTests(unittest.TestCase):
    def test_true_when_cat_file_succeeds(self):
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as m:
            self.assertTrue(smd._align_target_has_align("/opt/git/sigmond", "daba1f6"))
        argv = m.call_args[0][0]
        self.assertIn("cat-file", argv)

    def test_false_when_cat_file_fails(self):
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertFalse(smd._align_target_has_align("/opt/git/sigmond", "daba1f6"))


if __name__ == "__main__":
    unittest.main()

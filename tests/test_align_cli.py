"""CLI tests for `smd align` — every probe patched; no network, no git."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from sigmond import align, align_apply, align_live

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
        self.assertIn("radiod rebuild is Plan 2b — --apply will not move it", out)
        self.assertNotIn("RESTARTS radiod", out)
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

    def test_dry_run_shows_uvlock_only_dirt_as_a_move_that_resets_it(self):
        rc, out = run({"sigmond": "daba1f6", "hf-timestd": "4595c00", "ka9q-radio": "401992c"},
                      dirty={"hf-timestd": ["uv.lock"]})
        line = next(l for l in out.splitlines() if "hf-timestd" in l)
        self.assertIn("move", line)
        self.assertIn("uv.lock will be reset", line)

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
        self.assertIn("left — ahead by 4; --apply leaves it (--allow-rollback moves it back)", out)

    # --- B4 finding: an ahead component is left, not counted toward the
    # exit-code "pending" tally; an unmeasurable distance stays a move. ---

    def test_dry_run_ahead_only_component_exits_0(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond")}
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance",
                        return_value={"ahead": 0, "behind": 4, "files": 2}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False, recorded="v3.53")
        self.assertEqual(rc, 0)
        self.assertIn("left — ahead by", out)
        self.assertIn("1 left ahead", out)

    def test_dry_run_ahead_with_unknown_distance_still_pending_exits_1(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond")}
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance", return_value={"error": "no route"}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False, recorded="v3.53")
        self.assertEqual(rc, 1)

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
# Plan 2b / Task 3 — reading the station: reflog, systemctl, editable
# siblings, units-per-component, the radiod binary's mtime. Every probe
# here is faked; no git, no systemctl, no real filesystem outside tmp.
# ---------------------------------------------------------------------------


class AlignHeadMovedAtTests(unittest.TestCase):
    def test_reflog_newest_entry_is_the_move_time(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="1790270000\n", stderr="")

        got = smd._align_head_moved_at("/opt/git/sigmond/psk-recorder", run=fake_run)
        self.assertEqual(got, 1790270000.0)
        self.assertIn("-g", calls[0])
        self.assertIn("--no-optional-locks", calls[0])

    def test_nonzero_returncode_is_none(self):
        def fake_run(argv, **kw):
            return subprocess.CompletedProcess(argv, 128, stdout="", stderr="fatal: bad reflog")

        self.assertIsNone(smd._align_head_moved_at("/opt/git/sigmond/x", run=fake_run))

    def test_empty_stdout_is_none(self):
        def fake_run(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        self.assertIsNone(smd._align_head_moved_at("/opt/git/sigmond/x", run=fake_run))


class AlignUnitsStartedAtTests(unittest.TestCase):
    def _run_for(self, states):
        """``states``: {unit: (ActiveState, 'unix-ts-or-None')} — mirrors
        `systemctl show --timestamp=unix -p ActiveState -p ActiveEnterTimestamp`."""

        def fake_run(argv, **kw):
            unit = argv[-1]
            active_state, ts = states[unit]
            lines = [f"ActiveState={active_state}"]
            lines.append(f"ActiveEnterTimestamp={ts if ts is not None else 'n/a'}")
            return subprocess.CompletedProcess(argv, 0, stdout="\n".join(lines) + "\n", stderr="")

        return fake_run

    def test_min_over_two_active_units(self):
        run = self._run_for({"a.service": ("active", "@100"), "b.service": ("active", "@50")})
        got = smd._align_units_started_at(["a.service", "b.service"], run=run)
        self.assertEqual(got, 50.0)

    def test_inactive_unit_excluded_active_unit_wins(self):
        run = self._run_for({"a.service": ("inactive", None), "b.service": ("active", "@70")})
        got = smd._align_units_started_at(["a.service", "b.service"], run=run)
        self.assertEqual(got, 70.0)

    def test_all_inactive_is_none(self):
        run = self._run_for({"a.service": ("inactive", None), "b.service": ("failed", None)})
        self.assertIsNone(smd._align_units_started_at(["a.service", "b.service"], run=run))

    def test_malformed_timestamp_line_ignored(self):
        def fake_run(argv, **kw):
            return subprocess.CompletedProcess(
                argv, 0, stdout="ActiveState=active\nActiveEnterTimestamp=garbage\n", stderr="")

        self.assertIsNone(smd._align_units_started_at(["a.service"], run=fake_run))


class AlignConsumesTests(unittest.TestCase):
    def test_editable_sibling_under_base_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "ka9q-python").mkdir()
            with mock.patch.object(smd, "_editable_siblings",
                                   return_value={"ka9q-python": base / "ka9q-python"}):
                got = smd._align_consumes(str(base), ["psk-recorder"])
            self.assertEqual(got, {"psk-recorder": {"ka9q-python"}})

    def test_path_outside_base_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with mock.patch.object(smd, "_editable_siblings",
                                   return_value={"elsewhere": Path("/etc/passwd-not-here")}):
                got = smd._align_consumes(str(base), ["psk-recorder"])
            self.assertEqual(got, {})


class AlignServicesTests(unittest.TestCase):
    def test_orphaned_unit_dropped(self):
        u1 = smd.UnitRef(component="psk-recorder", unit="psk-recorder@a.service",
                         template="psk-recorder@.service", instance="a",
                         kind="service", source="deploy.toml:x")
        u2 = smd.UnitRef(component="psk-recorder", unit="psk-recorder@b.service",
                         template="psk-recorder@.service", instance="b",
                         kind="service", source="deploy.toml:x", orphaned=True)
        topo = {"components": {"psk-recorder": {"enabled": True}}}
        with mock.patch.object(smd, "_load_topology", return_value=topo), \
            mock.patch.object(smd, "resolve_units", return_value=[u1, u2]):
            out = smd._align_services()
        self.assertEqual(out, {"psk-recorder": ["psk-recorder@a.service"]})

    def test_rejected_component_is_skipped_not_raised(self):
        good = smd.UnitRef(component="hf-timestd", unit="hf-timestd.service",
                           template=None, instance=None, kind="service", source="x")

        def fake_resolve(components, enabled):
            if components == ["broken-comp"]:
                raise ValueError("component 'broken-comp' not found in enabled components")
            return [good]

        topo = {"components": {"broken-comp": {"enabled": True},
                               "hf-timestd": {"enabled": True}}}
        with mock.patch.object(smd, "_load_topology", return_value=topo), \
            mock.patch.object(smd, "resolve_units", side_effect=fake_resolve):
            out = smd._align_services()
        self.assertEqual(out, {"hf-timestd": ["hf-timestd.service"]})


class AlignRadiodBuiltAtTests(unittest.TestCase):
    def test_mtime_of_installed_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "radiod"
            binary.write_text("x")
            with mock.patch.object(smd, "_ALIGN_RADIOD_BINARY", binary):
                got = smd._align_radiod_built_at()
            self.assertEqual(got, binary.stat().st_mtime)

    def test_absent_binary_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "radiod"
            with mock.patch.object(smd, "_ALIGN_RADIOD_BINARY", binary):
                got = smd._align_radiod_built_at()
            self.assertIsNone(got)


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
        # M8: tightened — the executable, the re-exec'd script path, and
        # the passthrough flags, not just "an execv happened somewhere".
        boot_step = align_apply.Step("sigmond", "moved", "459bee6 -> daba1f6", 100)
        rc, out, mocks = run_apply(
            live={"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
            apply_plan_result=[boot_step], target_has_align=True)
        mocks["execv"].assert_called_once()
        exe, exec_args = mocks["execv"].call_args[0]
        self.assertEqual(exe, sys.executable)
        self.assertEqual(exec_args[0], sys.executable)
        self.assertEqual(exec_args[1],
                         str(Path("/opt/git/sigmond") / "sigmond" / "bin" / "smd"))
        self.assertIn("align", exec_args)
        self.assertIn("--apply", exec_args)
        self.assertIn("--release", exec_args)
        self.assertIn("v3.53", exec_args)
        self.assertIn("--base", exec_args)
        self.assertIn("/opt/git/sigmond", exec_args)

    def test_execv_passthrough_carries_allow_rollback_and_max_bytes(self):
        boot_step = align_apply.Step("sigmond", "moved", "459bee6 -> daba1f6", 100)
        rc, out, mocks = run_apply(
            live={"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
            apply_plan_result=[boot_step], target_has_align=True,
            allow_rollback=True, max_bytes=500)
        exec_args = mocks["execv"].call_args[0][1]
        self.assertIn("--allow-rollback", exec_args)
        self.assertIn("--max-bytes", exec_args)
        self.assertIn("500", exec_args)

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

    def test_sigmond_bootstrap_not_moved_skips_rest_with_explanation(self):
        # M5: when sigmond itself doesn't move cleanly, the rest are
        # skipped ON PURPOSE — the printed detail must say why, not just
        # name sigmond's own outcome.
        boot_step = align_apply.Step("sigmond", "refused", "dirty working tree")
        rc, out, mocks = run_apply(
            live={"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
            apply_plan_result=[boot_step])
        self.assertEqual(rc, 1)
        self.assertIn("nothing else moves under an smd in an unknown state", out)

    def test_lifecycle_lock_called_with_reason_align(self):
        # M8. sigmond already current (not 'forward') so the plain
        # single apply_plan call path runs — the bootstrap path is
        # exercised by the other tests in this class.
        rc, out, mocks = run_apply(
            live={"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"})
        mocks["lock"].assert_called_once_with(reason="align")

    def test_history_written_for_boot_steps_before_execv_and_flush(self):
        # I2, as amended by final review I6: the sigmond self-move must be
        # in HISTORY, and stdout/stderr flushed, BEFORE the process
        # replaces itself — while the manifest and aligned.json wait for
        # the child's full run (record is never called here).
        boot_step = align_apply.Step("sigmond", "moved", "459bee6 -> daba1f6", 100)
        call_order = []
        m_record = mock.Mock(side_effect=lambda *a, **k: call_order.append("record"))
        m_history = mock.Mock(side_effect=lambda *a, **k: call_order.append("history"))
        m_execv = mock.Mock(side_effect=lambda *a, **k: call_order.append("execv"))
        with mock.patch.object(smd, "_need_root", return_value=False), \
             mock.patch.object(smd, "lifecycle_lock",
                               lambda reason=None: contextlib.nullcontext()), \
             mock.patch.object(smd, "_align_live_state",
                               return_value=({"sigmond": "459bee6", "hf-timestd": "5c8196d",
                                             "ka9q-radio": "401992c"}, {}, {}, {})), \
             mock.patch("sigmond.align.fetch_release", return_value=APPLY_REL), \
             mock.patch("sigmond.align_apply.verify_release", return_value=None), \
             mock.patch.object(smd, "_align_ancestry", return_value=lambda c, a, b: True), \
             mock.patch("sigmond.align_apply.apply_plan", return_value=[boot_step]), \
             mock.patch.object(smd, "_align_target_has_align", return_value=True), \
             mock.patch("sigmond.align_apply.record", m_record), \
             mock.patch("sigmond.align_apply.record_history", m_history), \
             mock.patch("sigmond.catalog.load_catalog", return_value={}), \
             mock.patch("os.execv", m_execv), \
             mock.patch.object(sys.stdout, "flush",
                               side_effect=lambda: call_order.append("flush_stdout")), \
             mock.patch.object(sys.stderr, "flush",
                               side_effect=lambda: call_order.append("flush_stderr")):
            args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=True,
                                      apply=True, allow_rollback=False, max_bytes=None)
            smd.cmd_align(args)
        self.assertEqual(call_order, ["history", "flush_stdout", "flush_stderr", "execv"])
        called_rel, called_steps = m_history.call_args[0][:2]
        self.assertEqual(called_steps, [boot_step])


class AlignApplyBudgetCarryoverTests(unittest.TestCase):
    """I1: --max-bytes actually holds, both across the prefetch phase and
    across the sigmond-bootstrap's two apply_plan calls."""

    def test_prefetch_budget_crossed_by_sigmond_excludes_second_item(self):
        # _align_ancestry runs for REAL here (not mocked) so its budget
        # logic executes; align_apply.fetch/is_ancestor are the only fakes.
        live = {"sigmond": "459bee6", "hf-timestd": "5c8196d", "psk-recorder": "222222"}
        rel = align.Release(tag="v3.53", manifest_text="m", appliance_commit="a" * 40,
                            components={"sigmond": "daba1f6", "hf-timestd": "111111",
                                       "psk-recorder": "333333"})
        fetch_calls = []

        def fake_fetch(repo, owner, **kw):
            fetch_calls.append(str(repo))
            return 1000   # each fetch alone exceeds a 500-byte budget

        with mock.patch.object(smd, "_need_root", return_value=False), \
             mock.patch.object(smd, "lifecycle_lock",
                               lambda reason=None: contextlib.nullcontext()), \
             mock.patch.object(smd, "_align_live_state", return_value=(live, {}, {}, {})), \
             mock.patch("sigmond.align.fetch_release", return_value=rel), \
             mock.patch("sigmond.align_apply.verify_release", return_value=None), \
             mock.patch("sigmond.align_apply.fetch", side_effect=fake_fetch), \
             mock.patch("sigmond.align_apply.is_ancestor", return_value=True), \
             mock.patch.object(Path, "owner", return_value="sigmond"), \
             mock.patch.object(smd, "_align_target_has_align", return_value=False), \
             mock.patch("sigmond.align_apply.apply_plan",
                        side_effect=lambda rel, items, ctx:
                        [align_apply.Step(it.component, "moved", "x -> y", 1000)
                         for it in items]), \
             mock.patch("sigmond.align_apply.record"), \
             mock.patch("sigmond.catalog.load_catalog", return_value={}), \
             mock.patch("os.execv") as m_execv:
            args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=True,
                                      apply=True, allow_rollback=False, max_bytes=500)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = smd.cmd_align(args)
        m_execv.assert_not_called()
        # sigmond is first in plan order and always gets fetched (the
        # running total starts at 0, and the budget check only fires
        # AFTER it's exceeded); once its 1000 bytes crosses the 500-byte
        # budget, neither hf-timestd's nor psk-recorder's prefetch fetch
        # may fire — no fetch argv for either.
        self.assertEqual(fetch_calls, ["/opt/git/sigmond/sigmond"])
        text = out.getvalue()
        self.assertIn("hf-timestd: skipped", text)
        self.assertIn("psk-recorder: skipped", text)
        self.assertEqual(text.count("byte budget reached"), 2)
        self.assertEqual(rc, 1)

    def test_bootstrap_remainder_at_or_below_zero_skips_all_remaining_forward(self):
        # _align_ancestry is mocked here (bypassing prefetch entirely, so
        # skipped-for-budget stays empty) to isolate the SECOND mechanism:
        # apply_plan's own total_bytes restarting at 0 on the second call
        # unless the boot move's cost is carried forward via ctx2.max_bytes.
        boot_step = align_apply.Step("sigmond", "moved", "459bee6 -> daba1f6", 500)
        rel = align.Release(tag="v3.53", manifest_text="m", appliance_commit="a" * 40,
                            components={"sigmond": "daba1f6", "hf-timestd": "111111",
                                       "ka9q-radio": "222222"})
        apply_plan_calls = []

        def fake_apply_plan(rel, items, ctx):
            apply_plan_calls.append(([it.component for it in items], ctx.max_bytes))
            if items and items[0].component == "sigmond":
                return [boot_step]
            return [align_apply.Step(it.component, "current") for it in items]

        m_apply_plan = mock.Mock(side_effect=fake_apply_plan)
        with mock.patch.object(smd, "_need_root", return_value=False), \
             mock.patch.object(smd, "lifecycle_lock",
                               lambda reason=None: contextlib.nullcontext()), \
             mock.patch.object(smd, "_align_live_state",
                               return_value=({"sigmond": "459bee6", "hf-timestd": "5c8196d",
                                             "ka9q-radio": "401992c"}, {}, {}, {})), \
             mock.patch("sigmond.align.fetch_release", return_value=rel), \
             mock.patch("sigmond.align_apply.verify_release", return_value=None), \
             mock.patch.object(smd, "_align_ancestry", return_value=lambda c, a, b: True), \
             mock.patch.object(smd, "_align_target_has_align", return_value=False), \
             mock.patch("sigmond.align_apply.apply_plan", m_apply_plan), \
             mock.patch("sigmond.align_apply.record"), \
             mock.patch("sigmond.catalog.load_catalog", return_value={}), \
             mock.patch("os.execv") as m_execv:
            args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=True,
                                      apply=True, allow_rollback=False, max_bytes=500)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = smd.cmd_align(args)
        m_execv.assert_not_called()
        # boot.bytes (500) == max_bytes (500) -> remaining == 0 -> the
        # second apply_plan call must never even see hf-timestd/ka9q-radio;
        # both are forced straight to a skipped step instead. Two calls
        # happen in total: the boot call for sigmond alone, then a second
        # call for whatever's left of other_items after the forced ones
        # are pulled out — here, nothing (an empty list).
        self.assertEqual(len(apply_plan_calls), 2)
        self.assertEqual(apply_plan_calls[0][0], ["sigmond"])
        self.assertEqual(apply_plan_calls[1], ([], 0))
        text = out.getvalue()
        self.assertIn("hf-timestd: skipped", text)
        self.assertIn("ka9q-radio: skipped", text)
        self.assertEqual(text.count("byte budget reached"), 2)
        self.assertEqual(rc, 1)


class AlignApplyRadiodMissingTests(unittest.TestCase):
    def test_missing_ka9q_radio_is_refused_not_installed(self):
        # M4: ka9q-radio has no install.sh of its own (smd builds it
        # in-tree) — a missing checkout must read as a refusal naming the
        # real forward path, not an install attempt that would fail.
        live = {"sigmond": "daba1f6", "hf-timestd": "5c8196d"}   # no ka9q-radio at all
        rel = align.Release(tag="v3.53", manifest_text="m", appliance_commit="a" * 40,
                            components={"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                       "ka9q-radio": "401992c"})
        with mock.patch.object(smd, "_need_root", return_value=False), \
             mock.patch.object(smd, "lifecycle_lock",
                               lambda reason=None: contextlib.nullcontext()), \
             mock.patch.object(smd, "_align_live_state", return_value=(live, {}, {}, {})), \
             mock.patch("sigmond.align.fetch_release", return_value=rel), \
             mock.patch("sigmond.align_apply.verify_release", return_value=None), \
             mock.patch.object(smd, "_align_ancestry", return_value=lambda c, a, b: True), \
             mock.patch("sigmond.align_apply.apply_plan", return_value=[]), \
             mock.patch("sigmond.align_apply.record"), \
             mock.patch("sigmond.catalog.load_catalog", return_value={}):
            args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=True,
                                      apply=True, allow_rollback=False, max_bytes=None)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = smd.cmd_align(args)
        self.assertEqual(rc, 1)
        text = out.getvalue()
        self.assertIn("ka9q-radio: refused", text)
        self.assertIn("smd install ka9q-radio", text)


class AlignRunInstallTests(unittest.TestCase):
    """M3: stdin=DEVNULL (never hang on a stray prompt), and the last few
    stderr lines print on a non-zero exit."""

    def test_passes_stdin_devnull(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "install.sh").write_text("#!/bin/sh\n")
            with mock.patch("subprocess.run",
                            return_value=subprocess.CompletedProcess([], 0, "", "")) as m:
                rc = smd._align_run_install(repo)
        self.assertEqual(rc, 0)
        self.assertEqual(m.call_args.kwargs.get("stdin"), subprocess.DEVNULL)

    def test_prints_last_stderr_lines_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "install.sh").write_text("#!/bin/sh\n")
            stderr = "\n".join(f"line{i}" for i in range(10))
            with mock.patch("subprocess.run",
                            return_value=subprocess.CompletedProcess([], 1, "", stderr)):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = smd._align_run_install(repo)
        self.assertEqual(rc, 1)
        printed = out.getvalue()
        for i in range(5, 10):
            self.assertIn(f"line{i}", printed)
        self.assertNotIn("line4", printed)

    def test_no_installer_on_disk_is_zero_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(smd._align_run_install(Path(tmp)), 0)


class AlignInstallMissingTests(unittest.TestCase):
    """I3: an installed-from-missing component gets a .pin (so the NEXT
    `smd align` sees it as current, not as missing-forever) and the same
    three chowns a move does."""

    def test_writes_pin_and_chowns_three_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "ka9q-radio"
            repo.mkdir()
            catalog = {"ka9q-radio": types.SimpleNamespace(name="ka9q-radio",
                                                           repo="https://example/ka9q-radio")}
            full_sha = "d" * 40
            chown_calls = []
            with mock.patch.object(smd, "_clone_repo", return_value=repo) as m_clone, \
                 mock.patch.object(smd, "_align_run_install", return_value=0), \
                 mock.patch("sigmond.align_apply.resolve", return_value=full_sha), \
                 mock.patch.object(smd, "_align_chown",
                                   side_effect=lambda p, o: chown_calls.append(Path(p))):
                smd._align_install_missing("ka9q-radio", "abc1234", base=base, catalog=catalog)
            m_clone.assert_called_once()
            self.assertEqual((repo / ".pin").read_text().strip(), full_sha)
            self.assertEqual(chown_calls,
                             [repo / ".pin", repo / ".git" / "info" / "exclude",
                              repo / ".git" / "info"])

    def test_install_failure_raises_before_writing_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "ka9q-radio"
            repo.mkdir()
            catalog = {"ka9q-radio": types.SimpleNamespace(name="ka9q-radio")}
            with mock.patch.object(smd, "_clone_repo", return_value=repo), \
                 mock.patch.object(smd, "_align_run_install", return_value=1), \
                 mock.patch("sigmond.align_apply.resolve") as m_resolve:
                with self.assertRaises(RuntimeError):
                    smd._align_install_missing("ka9q-radio", "abc1234", base=base, catalog=catalog)
            m_resolve.assert_not_called()
            self.assertFalse((repo / ".pin").exists())


class AlignChownTests(unittest.TestCase):
    def test_uses_checkout_dir_uid_and_gid_not_swapped(self):
        # M8: mutating the implementation to swap st_uid/st_gid must make
        # this fail — it asserts the exact (uid, gid) order os.chown gets.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "hf-timestd"
            (repo / ".git").mkdir(parents=True)
            pin = repo / ".pin"
            pin.write_text("x")
            fake_stat = types.SimpleNamespace(st_uid=4242, st_gid=4343)
            calls = []
            with mock.patch("os.stat", return_value=fake_stat), \
                 mock.patch("os.chown",
                            side_effect=lambda p, u, g: calls.append((p, u, g))):
                smd._align_chown(pin, "irrelevant")
            self.assertEqual(calls, [(pin, 4242, 4343)])

    def test_walks_up_to_the_checkout_root_for_a_nested_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "hf-timestd"
            (repo / ".git" / "info").mkdir(parents=True)
            exclude = repo / ".git" / "info" / "exclude"
            exclude.write_text("")
            # wraps=os.stat (a spy on the REAL implementation, not a stub) —
            # a plain return_value stub would make every Path.exists() call
            # along the walk-up (which itself calls os.stat) report "exists"
            # unconditionally, masking the very walk this test checks.
            with mock.patch("os.stat", wraps=os.stat) as m_stat, \
                 mock.patch("os.chown") as m_chown:
                smd._align_chown(exclude, "irrelevant")
            # The LAST os.stat call is _align_chown's own — after walking
            # past .git/info/.git and .git/.git (neither exists) it must
            # land on the checkout root, not an intermediate directory.
            self.assertEqual(m_stat.call_args, mock.call(repo))
            m_chown.assert_called_once()
            self.assertEqual(m_chown.call_args[0][0], exclude)


class _FakeRestartRun:
    """Records every argv it's called with; answers by subcommand — a
    `systemctl restart <unit>` for a unit in ``fail_units`` returns 1,
    the readiness probe returns ``ready_rc``, everything else 0."""
    def __init__(self, fail_units=None, ready_rc=0):
        self.calls = []
        self.fail_units = fail_units or set()
        self.ready_rc = ready_rc

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if argv[0] == "/usr/local/sbin/sigmond-radiod-ready":
            return subprocess.CompletedProcess(argv, self.ready_rc, "", "")
        if argv[:2] == ["systemctl", "restart"] and argv[2] in self.fail_units:
            return subprocess.CompletedProcess(argv, 1, "", f"failed: {argv[2]}\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def restart_argv(self, unit):
        return [c for c in self.calls if c[:2] == ["systemctl", "restart"] and c[2] == unit]

    def index_of(self, argv):
        return self.calls.index(argv)


class AlignRestartTests(unittest.TestCase):
    """Task 4: radiod first (with a readiness wait), then its consumers —
    all in-process, never a child `smd restart`."""

    def test_radiod_first_orders_before_ready_before_consumers(self):
        fake = _FakeRestartRun()
        services = {align_live.RADIOD: ["radiod@default.service"],
                   "psk-recorder": ["psk-recorder@default.service"]}
        smd._align_restart(True, ["psk-recorder"], services, run=fake,
                           say=lambda *_: None, sleep=lambda *_: None)
        radiod_restart = fake.restart_argv("radiod@default.service")[0]
        consumer_restart = fake.restart_argv("psk-recorder@default.service")[0]
        ready = next(c for c in fake.calls
                    if c[0] == "/usr/local/sbin/sigmond-radiod-ready")
        self.assertLess(fake.index_of(radiod_restart), fake.index_of(ready))
        self.assertLess(fake.index_of(ready), fake.index_of(consumer_restart))

    def test_readiness_failure_skips_consumers_without_restarting_them(self):
        fake = _FakeRestartRun(ready_rc=1)
        services = {align_live.RADIOD: ["radiod@default.service"],
                   "psk-recorder": ["psk-recorder@default.service"]}
        steps = smd._align_restart(True, ["psk-recorder"], services, run=fake,
                                   say=lambda *_: None, sleep=lambda *_: None)
        by_component = {s.component: s for s in steps}
        self.assertEqual(by_component[align_live.RADIOD].outcome, "failed")
        self.assertEqual(by_component["psk-recorder"].outcome, "skipped")
        self.assertEqual(by_component["psk-recorder"].detail, "radiod did not come back")
        self.assertEqual(fake.restart_argv("psk-recorder@default.service"), [])

    def test_one_failed_component_does_not_stop_the_rest(self):
        fake = _FakeRestartRun(fail_units={"b-recorder@default.service"})
        services = {"a": ["a-recorder@default.service"],
                   "b": ["b-recorder@default.service"],
                   "c": ["c-recorder@default.service"]}
        steps = smd._align_restart(False, ["a", "b", "c"], services, run=fake,
                                   say=lambda *_: None, sleep=lambda *_: None)
        by_component = {s.component: s for s in steps}
        self.assertEqual(by_component["a"].outcome, "restarted")
        self.assertEqual(by_component["b"].outcome, "failed")
        self.assertEqual(by_component["c"].outcome, "restarted")
        self.assertEqual(fake.restart_argv("c-recorder@default.service"),
                         [["systemctl", "restart", "c-recorder@default.service"]])

    def test_argv_never_contains_a_child_smd(self):
        fake = _FakeRestartRun()
        services = {align_live.RADIOD: ["radiod@default.service"],
                   "psk-recorder": ["psk-recorder@default.service"]}
        smd._align_restart(True, ["psk-recorder"], services, run=fake,
                           say=lambda *_: None, sleep=lambda *_: None)
        for argv in fake.calls:
            self.assertNotIn("smd", argv)


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
        args_, kwargs = mocks["record"].call_args
        self.assertEqual(kwargs["manifest_path"], smd.MANIFEST_PATH)
        self.assertEqual(args_[1], steps)


class AlignAncestryFactoryTests(unittest.TestCase):
    """_align_ancestry: fetch-at-most-once, the prefetch budget (ruling 2),
    a fetch failure never crashing the run (ruling 3), and — fix round 1 —
    the skipped-for-budget set it exposes (I1) and an unresolvable checkout
    owner never crashing it either (M7)."""

    def test_fetches_each_component_at_most_once(self):
        prefetched = {}
        skipped = set()
        calls = []
        with mock.patch("sigmond.align_apply.fetch",
                        mock.Mock(side_effect=lambda repo, owner, **k: calls.append(str(repo)) or 10)), \
             mock.patch("sigmond.align_apply.is_ancestor", return_value=True), \
             mock.patch.object(Path, "owner", return_value="sigmond"):
            is_ancestor = smd._align_ancestry("/opt/git/sigmond", prefetched, None, print, skipped)
            is_ancestor("hf-timestd", "a", "b")   # forward direction
            is_ancestor("hf-timestd", "b", "a")   # backward direction
        self.assertEqual(len(calls), 1)
        self.assertEqual(prefetched["hf-timestd"], 10)
        self.assertEqual(skipped, set())

    def test_budget_stops_fetching_further_components(self):
        prefetched = {}
        skipped = set()
        say = mock.Mock()
        fetch_calls = []
        with mock.patch("sigmond.align_apply.fetch",
                        mock.Mock(side_effect=lambda repo, owner, **k:
                                  fetch_calls.append(str(repo)) or 1000)), \
             mock.patch("sigmond.align_apply.is_ancestor", return_value=True), \
             mock.patch.object(Path, "owner", return_value="sigmond"):
            is_ancestor = smd._align_ancestry("/opt/git/sigmond", prefetched, 500, say, skipped)
            is_ancestor("hf-timestd", "a", "b")
            is_ancestor("hf-timestd", "b", "a")
            is_ancestor("ka9q-radio", "a", "b")
            is_ancestor("ka9q-radio", "b", "a")
        # Only the first component's fetch actually ran; the second is over
        # budget by the time it's reached and must not be fetched at all.
        self.assertEqual(fetch_calls, ["/opt/git/sigmond/hf-timestd"])
        # I1: the second component is exposed as skipped-for-budget so the
        # caller can keep it away from apply_plan's own move step entirely.
        self.assertEqual(skipped, {"ka9q-radio"})

    def test_fetch_failure_says_and_returns_none_without_crashing(self):
        prefetched = {}
        skipped = set()
        say = mock.Mock()
        with mock.patch("sigmond.align_apply.fetch",
                        side_effect=align_apply.ApplyError("network unreachable")), \
             mock.patch.object(Path, "owner", return_value="sigmond"):
            is_ancestor = smd._align_ancestry("/opt/git/sigmond", prefetched, None, say, skipped)
            result = is_ancestor("hf-timestd", "a", "b")
        self.assertIsNone(result)
        say.assert_called_once()
        self.assertIn("hf-timestd: fetch failed: network unreachable", say.call_args[0][0])
        self.assertEqual(skipped, set())

    def test_owner_keyerror_says_and_returns_none_without_crashing(self):
        # M7: an orphaned checkout owner (uid with no passwd entry) must
        # read as a refusal, never a traceback.
        prefetched = {}
        skipped = set()
        say = mock.Mock()
        with mock.patch.object(Path, "owner", side_effect=KeyError("getpwuid(): uid not found")):
            is_ancestor = smd._align_ancestry("/opt/git/sigmond", prefetched, None, say, skipped)
            result = is_ancestor("hf-timestd", "a", "b")
        self.assertIsNone(result)
        say.assert_called_once()
        self.assertIn("hf-timestd: cannot resolve checkout owner — skipped",
                      say.call_args[0][0])

    def test_owner_keyerror_only_reported_once_across_both_calls(self):
        prefetched = {}
        skipped = set()
        say = mock.Mock()
        with mock.patch.object(Path, "owner", side_effect=KeyError("no such user")):
            is_ancestor = smd._align_ancestry("/opt/git/sigmond", prefetched, None, say, skipped)
            is_ancestor("hf-timestd", "a", "b")
            is_ancestor("hf-timestd", "b", "a")
        say.assert_called_once()


class AlignTargetHasAlignTests(unittest.TestCase):
    def test_true_when_cat_file_succeeds(self):
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as m:
            self.assertTrue(smd._align_target_has_align("/opt/git/sigmond", "daba1f6"))
        argv = m.call_args[0][0]
        self.assertIn("cat-file", argv)

    def test_false_when_cat_file_fails(self):
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertFalse(smd._align_target_has_align("/opt/git/sigmond", "daba1f6"))


# ---------------------------------------------------------------------------
# Final whole-branch review of Plan 2a
# ---------------------------------------------------------------------------

def _apply_args(base, **over):
    ns = dict(release=None, base=str(base), no_cost=True, apply=True,
              allow_rollback=False, max_bytes=None)
    ns.update(over)
    return argparse.Namespace(**ns)


def _apply_patches(st, *, rel, live, dirty=None, ancestry=None, **extra):
    """The collaborators every --apply CLI test below stubs; returns the
    record / record_history mocks."""
    m_record = mock.Mock()
    m_history = mock.Mock()
    for p in [
        mock.patch.object(smd, "_need_root", return_value=False),
        mock.patch.object(smd, "lifecycle_lock", lambda reason=None: contextlib.nullcontext()),
        mock.patch.object(smd, "_align_live_state", return_value=(live, dirty or {}, {}, {})),
        mock.patch("sigmond.align.fetch_release", return_value=rel),
        mock.patch("sigmond.align_apply.verify_release", return_value=None),
        mock.patch.object(smd, "_align_ancestry",
                          ancestry or mock.Mock(return_value=lambda c, a, b: True)),
        mock.patch.object(smd, "_align_target_has_align", return_value=False),
        mock.patch("sigmond.align_apply.record", m_record),
        mock.patch("sigmond.align_apply.record_history", m_history),
        mock.patch("sigmond.catalog.load_catalog", return_value={}),
        mock.patch("os.execv"),
        *[mock.patch.object(smd, k, v) for k, v in extra.items()],
    ]:
        st.enter_context(p)
    return m_record, m_history


class AlignFinalReviewCliTests(unittest.TestCase):

    # --- I2: uv.lock-only dirt reaches the reset ---

    def test_uvlock_only_dirt_reaches_apply_and_resets_before_the_checkout(self):
        from tests.test_align_apply import _MultiFakeGit
        rel = align.Release(tag="v3.53", manifest_text="m", appliance_commit="a" * 40,
                            components={"sigmond": "daba1f6", "hf-timestd": "5c8196d"})
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for n in ("sigmond", "hf-timestd"):
                (base / n / ".git" / "info").mkdir(parents=True)
            (base / "sigmond" / ".pin").write_text("daba1f6\n")
            git = _MultiFakeGit()
            git.dirty["hf-timestd"] = ["uv.lock"]
            out = io.StringIO()
            with contextlib.ExitStack() as st:
                _apply_patches(st, rel=rel, live={"sigmond": "daba1f6", "hf-timestd": "4595c00"},
                               dirty={"hf-timestd": ["uv.lock"]},
                               _align_chown=mock.Mock())
                st.enter_context(mock.patch("subprocess.run", git))
                st.enter_context(mock.patch.object(Path, "owner", return_value="sigmond"))
                st.enter_context(contextlib.redirect_stdout(out))
                rc = smd.cmd_align(_apply_args(base))
            hf = [c for c in git.calls if git._name(c) == "hf-timestd"]
            reset = next(i for i, c in enumerate(hf)
                         if git._subcommand(c) == "checkout" and "uv.lock" in c)
            detach = next(i for i, c in enumerate(hf)
                          if git._subcommand(c) == "checkout" and "--detach" in c)
            self.assertLess(reset, detach)
            self.assertIn("hf-timestd: moved — reset uv.lock; ", out.getvalue())
            self.assertEqual(rc, 0)

    # --- I6: the bootstrap writes history only ---

    def test_bootstrap_writes_history_not_records_before_execv(self):
        boot_step = align_apply.Step("sigmond", "moved", "459bee6 -> daba1f6", 100)
        order = []
        with contextlib.ExitStack() as st:
            m_record, m_history = _apply_patches(
                st, rel=APPLY_REL,
                live={"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"})
            m_history.side_effect = lambda *a, **k: order.append("history")
            st.enter_context(mock.patch.object(smd, "_align_target_has_align", return_value=True))
            st.enter_context(mock.patch("sigmond.align_apply.apply_plan", return_value=[boot_step]))
            st.enter_context(mock.patch("os.execv", side_effect=lambda *a: order.append("execv")))
            st.enter_context(contextlib.redirect_stdout(io.StringIO()))
            smd.cmd_align(_apply_args("/opt/git/sigmond"))
        m_record.assert_not_called()
        self.assertEqual(order, ["history", "execv"])
        self.assertEqual(m_history.call_args[0][1], [boot_step])

    # --- minor: skipped-for-budget steps in plan order ---

    def test_budget_skips_are_reported_in_plan_order(self):
        names = ["ka9q-python", "hamsci-dsp", "alpha", "bravo", "charlie", "delta", "echo",
                 "foxtrot", "golf"]
        rel = align.Release(tag="v3.53", manifest_text="m", appliance_commit="a" * 40,
                            components={"sigmond": "daba1f6", **{n: "1111111" for n in names}})
        live = {"sigmond": "daba1f6", **{n: "2222222" for n in names}}

        def ancestry(base, prefetched, max_bytes, say, skipped):
            skipped.update(names)
            return lambda c, a, b: True
        out = io.StringIO()
        with contextlib.ExitStack() as st:
            _apply_patches(st, rel=rel, live=live, ancestry=ancestry)
            st.enter_context(mock.patch("sigmond.align_apply.apply_plan",
                                        return_value=[align_apply.Step("sigmond", "current")]))
            st.enter_context(contextlib.redirect_stdout(out))
            smd.cmd_align(_apply_args("/opt/git/sigmond"))
        reported = [l.split(":")[0].strip() for l in out.getvalue().splitlines()
                    if "byte budget reached" in l]
        self.assertEqual(reported, names)

    # --- minor: the Plan 2b notice follows the step list ---

    def test_plan_2b_notice_prints_after_the_step_list(self):
        steps = [align_apply.Step("sigmond", "current"),
                 align_apply.Step("hf-timestd", "moved", "a -> b", 10)]
        rc, out, mocks = run_apply(
            live={"sigmond": "daba1f6", "hf-timestd": "459bee6", "ka9q-radio": "401992c"},
            apply_plan_result=steps)
        notice = "services still run the old code until restarted — Plan 2b"
        self.assertIn(notice, out)
        self.assertEqual(out.count(notice), 1)
        self.assertGreater(out.index(notice), out.index("hf-timestd: moved"))

    def test_no_plan_2b_notice_when_nothing_moved(self):
        rc, out, mocks = run_apply(
            live={"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
            apply_plan_result=[align_apply.Step("sigmond", "current")])
        self.assertNotIn("Plan 2b", out.replace("radiod rebuild is Plan 2b", ""))

    # --- I4: install.sh runs as root; ownership repaired after it ---

    def test_ctx_run_install_repairs_ownership_even_after_a_failure(self):
        seen = {}

        def fake_apply_plan(rel, items, ctx):
            seen["ctx"] = ctx
            return []
        with contextlib.ExitStack() as st:
            _apply_patches(st, rel=APPLY_REL,
                           live={"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                 "ka9q-radio": "401992c"})
            st.enter_context(mock.patch("sigmond.align_apply.apply_plan", fake_apply_plan))
            st.enter_context(contextlib.redirect_stdout(io.StringIO()))
            smd.cmd_align(_apply_args("/srv/base"))
        with mock.patch.object(smd, "_align_run_install", return_value=1) as m_run, \
             mock.patch.object(smd, "_align_repair_ownership") as m_repair:
            rc = seen["ctx"].run_install(Path("/srv/base/hf-timestd"))
        self.assertEqual(rc, 1)
        m_run.assert_called_once_with(Path("/srv/base/hf-timestd"))
        m_repair.assert_called_once_with(Path("/srv/base"))


class AlignRepairOwnershipTests(unittest.TestCase):
    """I4: only paths inside *.egg-info directories, only where the owner
    differs from the checkout directory's — never venvs or build trees."""

    def test_chowns_only_egg_info_contents_that_differ(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "hf-timestd"
            (repo / ".git").mkdir(parents=True)
            egg = repo / "src" / "hf_timestd.egg-info"
            egg.mkdir(parents=True)
            (egg / "PKG-INFO").write_text("x")
            (egg / "SOURCES.txt").write_text("x")          # already right: left alone
            (repo / "README.md").write_text("x")            # root-owned but not egg-info
            venv_egg = repo / ".venv" / "lib" / "site-packages" / "dep.egg-info"
            venv_egg.mkdir(parents=True)
            (repo / ".venv" / "pyvenv.cfg").write_text("")
            build_egg = repo / "build" / "lib" / "x.egg-info"
            build_egg.mkdir(parents=True)
            other_venv_egg = repo / "env2" / "y.egg-info"
            other_venv_egg.mkdir(parents=True)
            (repo / "env2" / "pyvenv.cfg").write_text("")
            root_owned = {egg, egg / "PKG-INFO", repo / "README.md", venv_egg, build_egg,
                          other_venv_egg}
            real_lstat = os.lstat
            me = os.stat(repo)

            def fake_lstat(p, *a, **k):
                st = real_lstat(p, *a, **k)
                if Path(p) in root_owned:
                    return types.SimpleNamespace(st_uid=0, st_gid=0, st_mode=st.st_mode)
                return st
            calls = []
            with mock.patch("os.lstat", side_effect=fake_lstat), \
                 mock.patch("os.chown", side_effect=lambda p, u, g, **k: calls.append(
                     (Path(p), u, g))):
                smd._align_repair_ownership(base)
            self.assertEqual(sorted(calls),
                             sorted([(egg, me.st_uid, me.st_gid),
                                     (egg / "PKG-INFO", me.st_uid, me.st_gid)]))

    # --- B4 finding: install.sh (run as root) also leaves the in-checkout
    # venv root-owned; repair it too, but only the top-level `<repo>/venv`,
    # never a symlink and never a deeper venv. ---

    def test_venv_pyvenv_cfg_and_bin_python_chowned(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "hf-timestd"
            (repo / ".git").mkdir(parents=True)
            venv = repo / "venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "pyvenv.cfg").write_text("")
            (venv / "bin" / "python").write_text("x")
            root_owned = {venv / "pyvenv.cfg", venv / "bin" / "python"}
            real_lstat = os.lstat
            me = os.stat(repo)

            def fake_lstat(p, *a, **k):
                st = real_lstat(p, *a, **k)
                if Path(p) in root_owned:
                    return types.SimpleNamespace(st_uid=0, st_gid=0, st_mode=st.st_mode)
                return st
            calls = []
            with mock.patch("os.lstat", side_effect=fake_lstat), \
                 mock.patch("os.chown", side_effect=lambda p, u, g, **k: calls.append(
                     (Path(p), u, g))):
                smd._align_repair_ownership(base)
            self.assertEqual(sorted(calls),
                             sorted([(venv / "pyvenv.cfg", me.st_uid, me.st_gid),
                                     (venv / "bin" / "python", me.st_uid, me.st_gid)]))

    def test_venv_symlink_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "hf-timestd"
            (repo / ".git").mkdir(parents=True)
            real_venv = base / "real-venv"
            (real_venv / "bin").mkdir(parents=True)
            (real_venv / "pyvenv.cfg").write_text("")
            (real_venv / "bin" / "python").write_text("x")
            (repo / "venv").symlink_to(real_venv, target_is_directory=True)
            real_lstat = os.lstat

            def fake_lstat(p, *a, **k):
                st = real_lstat(p, *a, **k)
                return types.SimpleNamespace(st_uid=0, st_gid=0, st_mode=st.st_mode)
            calls = []
            with mock.patch("os.lstat", side_effect=fake_lstat), \
                 mock.patch("os.chown",
                            side_effect=lambda p, u, g, **k: calls.append(Path(p))):
                smd._align_repair_ownership(base)
            self.assertEqual(calls, [])

    def test_venv_nested_under_a_subdir_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "hf-timestd"
            (repo / ".git").mkdir(parents=True)
            nested = repo / "sub" / "venv"
            (nested / "bin").mkdir(parents=True)
            (nested / "pyvenv.cfg").write_text("")
            (nested / "bin" / "python").write_text("x")
            real_lstat = os.lstat

            def fake_lstat(p, *a, **k):
                st = real_lstat(p, *a, **k)
                return types.SimpleNamespace(st_uid=0, st_gid=0, st_mode=st.st_mode)
            calls = []
            with mock.patch("os.lstat", side_effect=fake_lstat), \
                 mock.patch("os.chown",
                            side_effect=lambda p, u, g, **k: calls.append(Path(p))):
                smd._align_repair_ownership(base)
            self.assertEqual(calls, [])

    def test_a_chown_error_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            egg = base / "c" / "c.egg-info"
            (base / "c" / ".git").mkdir(parents=True)
            egg.mkdir()
            real_lstat = os.lstat

            def fake_lstat(p, *a, **k):
                st = real_lstat(p, *a, **k)
                if Path(p) == egg:
                    return types.SimpleNamespace(st_uid=0, st_gid=0, st_mode=st.st_mode)
                return st
            out = io.StringIO()
            with mock.patch("os.lstat", side_effect=fake_lstat), \
                 mock.patch("os.chown", side_effect=PermissionError("nope")), \
                 contextlib.redirect_stdout(out):
                smd._align_repair_ownership(base)
            self.assertIn("nope", out.getvalue())


class AlignInstallMissingFinalReviewTests(unittest.TestCase):
    """C1 for a fresh clone: an install failure removes the clone so a
    re-run sees the component missing again."""

    def _install(self, base, rc, *, pre_existing=False, resolve=None):
        repo = base / "hf-timestd"
        if pre_existing:
            repo.mkdir()

        def fake_clone(entry, base, ref):
            (base / "hf-timestd" / ".git" / "info").mkdir(parents=True, exist_ok=True)
            (base / "hf-timestd" / "pyproject.toml").write_text("x")
            return base / "hf-timestd"
        catalog = {"hf-timestd": types.SimpleNamespace(name="hf-timestd")}
        events = []
        out = io.StringIO()
        with mock.patch.object(smd, "_clone_repo", side_effect=fake_clone), \
             mock.patch.object(smd, "_align_run_install",
                               side_effect=lambda r: events.append("INSTALL") or rc), \
             mock.patch.object(smd, "_align_repair_ownership",
                               side_effect=lambda b: events.append(("REPAIR", b))), \
             mock.patch("sigmond.align_apply.resolve", resolve or mock.Mock(return_value="d" * 40)), \
             mock.patch.object(smd, "_align_chown"), \
             contextlib.redirect_stdout(out):
            try:
                smd._align_install_missing("hf-timestd", "abc1234", base=base, catalog=catalog)
                err = None
            except RuntimeError as e:
                err = e
        return repo, err, events, out.getvalue()

    def test_install_failure_removes_the_fresh_clone(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, err, events, out = self._install(Path(tmp), 1)
            self.assertIsNotNone(err)
            self.assertIn("install.sh exited 1", str(err))
            self.assertFalse(repo.exists())

    def test_a_resolve_failure_after_install_also_removes_the_clone(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = mock.Mock(side_effect=align_apply.ApplyError("no such commit"))
            repo, err, events, out = self._install(Path(tmp), 0, resolve=bad)
            self.assertIsNotNone(err)
            self.assertFalse(repo.exists())

    def test_a_directory_that_existed_before_is_never_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, err, events, out = self._install(Path(tmp), 1, pre_existing=True)
            self.assertIsNotNone(err)
            self.assertTrue(repo.exists())

    def test_says_running_install_and_repairs_ownership_after_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, err, events, out = self._install(base, 0)
            self.assertIsNone(err)
            self.assertIn("hf-timestd: running install.sh …", out)
            self.assertEqual(events, ["INSTALL", ("REPAIR", base)])

    def test_repairs_ownership_even_when_install_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, err, events, out = self._install(base, 2)
            self.assertIn(("REPAIR", base), events)


class AlignLiveStateFileListTests(unittest.TestCase):
    """I2: _align_live_state reports each checkout's dirty FILES; untracked
    files are not dirt. Real git, in a tmp dir only."""

    def _git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)

    def _repo(self, base, name):
        d = base / name
        d.mkdir()
        self._git("init", "-q", cwd=d)
        self._git("config", "user.name", "Test", cwd=d)
        self._git("config", "user.email", "test@example.com", cwd=d)
        (d / "uv.lock").write_text("a")
        self._git("add", "uv.lock", cwd=d)
        self._git("commit", "-q", "-m", "init", cwd=d)
        return d

    def test_dirty_is_a_file_list_and_untracked_is_not_dirt(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (self._repo(base, "untracked-only") / "stray.txt").write_text("x")
            (self._repo(base, "uvlock-only") / "uv.lock").write_text("b")
            live, dirty, origins, errors = smd._align_live_state(str(base))
            self.assertEqual(dirty["untracked-only"], [])
            self.assertEqual(dirty["uvlock-only"], ["uv.lock"])
            rel = align.Release(tag="v3.53", manifest_text="", appliance_commit=None,
                                components={"untracked-only": "1111111",
                                            "uvlock-only": "2222222"})
            plan = {i.component: i for i in align.plan_align(rel, live, dirty, errors)}
            self.assertEqual(plan["untracked-only"].status, "move")
            self.assertEqual(plan["uvlock-only"].status, "move")
            self.assertIn("uv.lock will be reset", plan["uvlock-only"].note)


if __name__ == "__main__":
    unittest.main()

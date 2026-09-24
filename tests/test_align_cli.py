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
import time
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
        # Task 7: the dry run's restarts section reads the station's
        # services; none here, so nothing touches systemctl or git — and
        # never the real catalog (Fix round 1 / I-2).
        mock.patch.object(smd, "_align_services", return_value={}),
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
    # Never the real catalog (Fix round 1 / I-2) — unless the test has
    # already put its own in place.
    import sigmond.catalog
    if not isinstance(sigmond.catalog.load_catalog, mock.NonCallableMock):
        patches.append(mock.patch("sigmond.catalog.load_catalog", return_value={}))
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
        self.assertIn("deb7bdd -> 401992c", out)
        self.assertIn("REBUILDS and RESTARTS radiod", out)
        self.assertIn(f"recording gap of about {align_live.RADIOD_GAP_ESTIMATE_S} s", out)
        self.assertNotIn("--apply will not move it", out)
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
    """Fix round 1 / C-A: the time HEAD MOVED, from .git/logs/HEAD — never
    a commit date (`git log -g --format=%ct` prints the commit's)."""

    def _git(self, repo, *args, date=None):
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        if date:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        return subprocess.run(["git", "-C", str(repo), *args], env=env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def test_checkout_of_an_old_commit_reads_now_not_the_commit_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "r"
            repo.mkdir()
            self._git(repo, "init", "-q")
            (repo / "f").write_text("1")
            self._git(repo, "add", "f")
            self._git(repo, "commit", "-q", "-m", "old", date="2020-01-01T00:00:00Z")
            first = self._git(repo, "rev-parse", "HEAD")
            (repo / "f").write_text("2")
            self._git(repo, "commit", "-q", "-am", "new", date="2020-01-01T00:00:00Z")
            self._git(repo, "checkout", "-q", "--detach", first)
            got = smd._align_head_moved_at(repo)
        self.assertIsNotNone(got)
        self.assertNotEqual(got, 1577836800.0)
        self.assertLess(abs(got - time.time()), 60)

    def test_last_line_timestamp_after_the_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / ".git" / "logs"
            logs.mkdir(parents=True)
            (logs / "HEAD").write_text(
                f"{'0' * 40} {'1' * 40} A Name <a@b> 1600000000 +0000\tclone: from x\n"
                f"{'1' * 40} {'2' * 40} Name With > Odd <a@b> 1790270000 -0500\tcheckout: moving\n")
            self.assertEqual(smd._align_head_moved_at(tmp), 1790270000.0)

    def test_missing_logs_head_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            self.assertIsNone(smd._align_head_moved_at(tmp))

    def test_empty_or_unparsable_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / ".git" / "logs"
            logs.mkdir(parents=True)
            (logs / "HEAD").write_text("")
            self.assertIsNone(smd._align_head_moved_at(tmp))
            (logs / "HEAD").write_text("garbage without an email\n")
            self.assertIsNone(smd._align_head_moved_at(tmp))


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


class AlignBuildTests(unittest.TestCase):
    """Task 5: the Ctx.build hook — never restarts radiod itself (Task 7's
    restart stage does that, from staleness)."""

    def test_prints_before_building_and_returns_0_on_success(self):
        repo = Path("/opt/git/sigmond/ka9q-radio")
        with mock.patch.object(smd, "_build_ka9q_radio", return_value=True) as m_build:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = smd._align_build("ka9q-radio", repo)
        self.assertEqual(rc, 0)
        m_build.assert_called_once_with(repo, force=True)
        self.assertIn("building ka9q-radio (radiod) …", out.getvalue())

    def test_returns_1_on_build_failure(self):
        repo = Path("/opt/git/sigmond/ka9q-radio")
        with mock.patch.object(smd, "_build_ka9q_radio", return_value=False):
            rc = smd._align_build("ka9q-radio", repo)
        self.assertEqual(rc, 1)


# ---------------------------------------------------------------------------
# --apply — Plan 2a: verify, classify, sigmond bootstrap, apply, record
# ---------------------------------------------------------------------------

APPLY_REL = align.Release(tag="v3.53", manifest_text="manifest text",
                          appliance_commit="a" * 40,
                          components={"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                      "ka9q-radio": "401992c"})


_CHECKS_PASS = {"units_stable": True, "authority_fresh": None,
                "heartbeat_sent": True, "notes": []}

# aligned.json's live block after a finished, passing restart stage — the
# precondition (with the release) for a no-op run (final review / I2).
_LIVE_PASSED = {"at": "2026-09-24T12:00:00Z", "restarted": ["hf-timestd"], "failed": [],
                "units_stable": True, "authority_fresh": True, "heartbeat_sent": True,
                "notes": [], "complete": True}


def _quiet_live_stages():
    """Task 7 wired image refresh, bring-up, restarts and fast checks into
    --apply. Tests written for the moves alone stub every one of them, so
    no test reaches the network, a child smd, systemctl or /etc."""
    return mock.patch.multiple(
        smd,
        _align_refresh_image_files=lambda tag: [],
        _align_bringup=lambda **kw: [],
        _align_services=lambda *a, **k: {},
        _align_restart=lambda *a, **k: [],
        _align_fast_checks=lambda *a, **k: dict(_CHECKS_PASS),
        # Never the real /etc/sigmond-appliance/aligned.json (final review / M5).
        _align_aligned_release=lambda: None,
        _align_aligned_live=lambda: None,
        # Never the real systemctl or /opt/git reflogs: the staleness read
        # also asks after hs-uploader's own daemon (final review / I5).
        _align_staleness=lambda *a, **k: _staleness())


def _no_real_record_live():
    """record_live reads, then rewrites, /etc/sigmond-appliance/aligned.json,
    and every run that reaches the restart stage now writes a live block
    (final review / I2) — so every harness that stubs the live stages
    stubs this too. A test that watches record_live patches it again,
    inside."""
    return mock.patch("sigmond.align_apply.record_live", new=lambda *a, **k: None)


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
        _quiet_live_stages(),
        _no_real_record_live(),
        # Fix round 1 / I-1: with apply=False this drives the real dry run,
        # which must reach neither the network nor /etc.
        mock.patch("sigmond.align.image_file_drift", return_value=[]),
        mock.patch("sigmond.align.recorded_release", return_value="v3.53"),
        mock.patch("sigmond.align.commit_distance",
                   side_effect=AssertionError("commit_distance reached the network")),
        # The bootstrap path writes history before execv; never the real
        # /var/lib/sigmond/update-history.jsonl (Fix round 1 audit).
        mock.patch("sigmond.provenance.record_update"),
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


@_quiet_live_stages()
@_no_real_record_live()
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


@_quiet_live_stages()
@_no_real_record_live()
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


@_quiet_live_stages()
@_no_real_record_live()
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


@_quiet_live_stages()
@_no_real_record_live()
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
        # Fix round 1 / M6: the stale "radiod build is Plan 2b" wording is
        # gone now that Plan 2b actually builds it.
        self.assertIn("ka9q-radio is not installed — install it with smd install ka9q-radio",
                     text)
        self.assertNotIn("radiod build is Plan 2b", text)


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


class AlignRefreshImageFilesTests(unittest.TestCase):
    """_align_refresh_image_files — the `--apply` glue between
    align.image_file_drift (read-only compare) and align_apply.refresh_image_file
    (the write). Task 6 of Plan 2b."""

    def test_differs_is_refreshed_by_name_current_is_left_alone(self):
        files = [
            {"path": "/usr/local/sbin/sigmond-site-timing", "status": "differs",
             "note": "differs from v3.53"},
            {"path": "/usr/local/sbin/sigmond-location-check", "status": "current", "note": ""},
        ]
        with mock.patch("sigmond.align.image_file_drift", return_value=files), \
             mock.patch("sigmond.align_apply.refresh_image_file",
                        return_value=align_apply.Step("x", "refreshed")) as m_refresh:
            steps = smd._align_refresh_image_files("v3.53")
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].outcome, "refreshed")
        m_refresh.assert_called_once_with(
            "v3.53", "sigmond-site-timing", Path("/usr/local/sbin/sigmond-site-timing"))

    def test_absent_is_also_refreshed(self):
        files = [{"path": "/usr/local/sbin/sigmond-location-check", "status": "absent",
                  "note": "not installed"}]
        with mock.patch("sigmond.align.image_file_drift", return_value=files), \
             mock.patch("sigmond.align_apply.refresh_image_file",
                        return_value=align_apply.Step("x", "refreshed")) as m_refresh:
            steps = smd._align_refresh_image_files("v3.53")
        m_refresh.assert_called_once_with(
            "v3.53", "sigmond-location-check", Path("/usr/local/sbin/sigmond-location-check"))
        self.assertEqual(steps[0].outcome, "refreshed")

    def test_unknown_status_is_refused_not_refreshed(self):
        files = [{"path": "/usr/local/sbin/sigmond-site-timing", "status": "unknown",
                  "note": "could not reach raw.githubusercontent.com"}]
        with mock.patch("sigmond.align.image_file_drift", return_value=files), \
             mock.patch("sigmond.align_apply.refresh_image_file") as m_refresh:
            steps = smd._align_refresh_image_files("v3.53")
        m_refresh.assert_not_called()
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].component, "/usr/local/sbin/sigmond-site-timing")
        self.assertEqual(steps[0].outcome, "refused")
        self.assertEqual(steps[0].detail, "could not compare — not refreshed")


def _completed(rc, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class _FakeBringupRun:
    """Answers `_align_bringup`'s child-process calls in call order; each
    answer is a (returncode, stdout, stderr) triple. Records every argv it
    was called with, and every keyword `_align_run` passed through."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.kwargs = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        self.kwargs.append(kw)
        rc, out, err = self.answers.pop(0) if self.answers else (0, "", "")
        return _completed(rc, out, err)


class _RaisingOnNthCall:
    """A fake `run` that answers 0/ok for every call except the Nth
    (1-indexed), where it raises `exc` — for exercising `_align_run`'s own
    TimeoutExpired/OSError handling from inside `_align_bringup`, rather
    than a pre-built CompletedProcess."""
    def __init__(self, n, exc):
        self.n, self.exc = n, exc
        self.calls = 0

    def __call__(self, argv, **kw):
        self.calls += 1
        if self.calls == self.n:
            raise self.exc
        return _completed(0)


# Never the real /etc/hs-uploader/pipelines.toml (Fix round 1 audit); a
# test that needs the manifest patches its own path on top of this.
@mock.patch("sigmond.uploader_manifest.MANIFEST_PATH",
            Path(tempfile.gettempdir()) / "align-test-absent" / "pipelines.toml")
class AlignBringupTests(unittest.TestCase):
    """_align_bringup — re-runs bring-up's own steps in bring-up's own
    order. Task 6 of Plan 2b; not yet wired into `_align_apply` (Task 7)."""

    def _run(self, answers, say=None, refreshed=False):
        fake = _FakeBringupRun(answers)
        # Never read the real /etc/hs-uploader/pipelines.toml (Fix round 1 audit).
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sigmond.uploader_manifest.MANIFEST_PATH", Path(tmp) / "pipelines.toml"), \
             mock.patch("os.access", return_value=True):
            steps = smd._align_bringup(run=fake, say=say or (lambda *a, **k: None),
                                       site_timing_refreshed=refreshed)
        return steps, fake

    def test_order_is_site_timing_render_manifest_doctor(self):
        steps, fake = self._run([(0, "", "")] * 5, refreshed=True)
        self.assertEqual(
            [s.component for s in steps],
            ["sigmond-site-timing", "config render",
             "admin uploader manifest --write", "doctor --fix"])
        self.assertIn("sigmond-site-timing", fake.calls[0][-1])
        self.assertEqual(fake.calls[1][:3], ["journalctl", "-t", "sigmond-site-timing"])
        self.assertEqual(fake.calls[2][-3:], ["config", "render", "--if-present"])
        self.assertEqual(fake.calls[3][-4:], ["admin", "uploader", "manifest", "--write"])
        self.assertEqual(fake.calls[4][-2:], ["doctor", "--fix"])
        for kw in fake.kwargs:
            self.assertEqual(kw.get("stdin"), subprocess.DEVNULL)
        for step in steps:
            self.assertEqual(step.outcome, "ran")

    def test_site_timing_failure_stops_before_render(self):
        steps, fake = self._run([(1, "", "chrony restart failed")], refreshed=True)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].component, "sigmond-site-timing")
        self.assertEqual(steps[0].outcome, "failed")
        self.assertEqual(len(fake.calls), 2)          # the script, then its journal
        self.assertEqual(fake.calls[1][0], "journalctl")

    # --- Final review / P2 + C1: site-timing re-runs only when this run
    # refreshed it; otherwise its wiring (profile full, enable --now on
    # every metrology channel, chrony restart) is left to bring-up. ---

    def test_unrefreshed_site_timing_is_not_re_run(self):
        steps, fake = self._run([(0, "", "")] * 3)
        self.assertEqual(steps[0], align_apply.Step(
            "sigmond-site-timing", "ran", "not re-run — unchanged; its wiring is bring-up's"))
        for argv in fake.calls:
            self.assertFalse(any("sigmond-site-timing" in str(a) for a in argv), argv)
            self.assertNotEqual(argv[0], "journalctl")
        self.assertEqual(fake.calls[0][-3:], ["config", "render", "--if-present"])

    def test_refreshed_site_timing_runs_and_its_detail_carries_the_journal_lines(self):
        journal = "".join(f"[site-timing] line {i}\n" for i in range(1, 26))
        steps, fake = self._run([(0, "done\n", ""), (0, journal, "")] + [(0, "", "")] * 3,
                                refreshed=True)
        self.assertEqual(fake.calls[0], ["/usr/local/sbin/sigmond-site-timing"])
        j = fake.calls[1]
        self.assertEqual(j[:3], ["journalctl", "-t", "sigmond-site-timing"])
        since = j[j.index("--since") + 1]
        self.assertTrue(since.startswith("@") and since[1:].isdigit(), since)
        self.assertIn("-o", j)
        self.assertEqual(j[j.index("-o") + 1], "cat")
        self.assertIn("--no-pager", j)
        detail = steps[0].detail
        self.assertEqual(steps[0].outcome, "ran")
        self.assertIn("[site-timing] line 25", detail)
        self.assertIn("[site-timing] line 6", detail)
        self.assertNotIn("[site-timing] line 5\n", detail + "\n")
        self.assertEqual(sum(1 for l in detail.splitlines() if "[site-timing] line" in l), 20)

    def test_doctor_findings_still_counts_as_ran(self):
        steps, fake = self._run([(0, "", ""), (0, "", ""),
                                 (1, "3 findings", "")])
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(steps[-1].component, "doctor --fix")
        self.assertEqual(steps[-1].outcome, "ran")

    def test_doctor_crash_is_failed(self):
        steps, fake = self._run([(0, "", ""), (0, "", ""),
                                 (2, "", "traceback")])
        self.assertEqual(steps[-1].component, "doctor --fix")
        self.assertEqual(steps[-1].outcome, "failed")

    # --- Fix round 1 / C1: a timed-out or unrunnable doctor --fix must
    # not read as "ran" — `_align_run`'s synthetic returncode for
    # TimeoutExpired/OSError is -1, and every _align_bringup step
    # (doctor included) must treat -1 as failed. ---

    def test_doctor_timeout_is_failed(self):
        run = _RaisingOnNthCall(3, subprocess.TimeoutExpired(cmd="doctor", timeout=120))
        with mock.patch("os.access", return_value=True):
            steps = smd._align_bringup(run=run, say=lambda *a, **k: None)
        self.assertEqual(steps[-1].component, "doctor --fix")
        self.assertEqual(steps[-1].outcome, "failed")
        self.assertIn("timed out", steps[-1].detail)

    def test_doctor_oserror_is_failed(self):
        run = _RaisingOnNthCall(3, OSError("no such file or directory"))
        with mock.patch("os.access", return_value=True):
            steps = smd._align_bringup(run=run, say=lambda *a, **k: None)
        self.assertEqual(steps[-1].component, "doctor --fix")
        self.assertEqual(steps[-1].outcome, "failed")
        self.assertIn("could not run", steps[-1].detail)

    def test_manifest_bytes_changed_reports_manifest_changed(self):
        from sigmond import uploader_manifest as um
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pipelines.toml"
            path.write_text("before\n")

            def run(argv, **kw):
                if "manifest" in argv and "--write" in argv:
                    path.write_text("after\n")
                return _completed(0)

            with mock.patch("os.access", return_value=True), \
                 mock.patch.object(um, "MANIFEST_PATH", path):
                steps = smd._align_bringup(run=run, say=lambda *a, **k: None)
        manifest_step = next(s for s in steps if s.component == "admin uploader manifest --write")
        self.assertEqual(manifest_step.detail, "manifest changed")

    def test_manifest_bytes_unchanged_no_manifest_changed_detail(self):
        from sigmond import uploader_manifest as um
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pipelines.toml"
            path.write_text("same\n")

            def run(argv, **kw):
                return _completed(0)

            with mock.patch("os.access", return_value=True), \
                 mock.patch.object(um, "MANIFEST_PATH", path):
                steps = smd._align_bringup(run=run, say=lambda *a, **k: None)
        manifest_step = next(s for s in steps if s.component == "admin uploader manifest --write")
        self.assertNotEqual(manifest_step.detail, "manifest changed")

    def test_not_executable_site_timing_is_skipped_not_failed(self):
        with mock.patch("os.access", return_value=False):
            steps = smd._align_bringup(run=_FakeBringupRun([(0, "", "")] * 3),
                                       say=lambda *a, **k: None, site_timing_refreshed=True)
        self.assertEqual([s.component for s in steps],
                         ["config render", "admin uploader manifest --write", "doctor --fix"])


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


# order_units() reads the catalog for start priorities; never the real one
# (/opt/git deploy.toml files, /etc/sigmond/catalog.toml) — Fix round 1 audit.
_NO_REAL_CATALOG = mock.patch("sigmond.catalog.load_catalog", new=lambda *a, **k: {})


class _FakeRestartRun:
    """Records every argv it's called with; answers by subcommand:
    `systemctl is-active <unit>` reports "inactive" for any unit in
    ``inactive_units`` (else "active"); `systemctl restart <unit>` for a
    unit in ``fail_units`` returns 1; the readiness probe returns
    ``ready_rc``; everything else 0. ``raise_on`` maps an exact argv (as a
    tuple) to an exception instance to raise instead of answering — for
    exercising the TimeoutExpired/OSError paths."""
    def __init__(self, fail_units=None, ready_rc=0, inactive_units=None, raise_on=None,
                 show=None):
        self.calls = []
        self.kwargs = []
        self.fail_units = fail_units or set()
        self.ready_rc = ready_rc
        self.inactive_units = inactive_units or set()
        self.raise_on = raise_on or {}
        self.show = show      # callable(argv) -> stdout for `systemctl show`, or None

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        self.kwargs.append(kw)
        exc = self.raise_on.get(tuple(argv))
        if exc is not None:
            raise exc
        if argv[:2] == ["systemctl", "is-active"]:
            active = argv[2] not in self.inactive_units
            return subprocess.CompletedProcess(
                argv, 0 if active else 3, "active\n" if active else "inactive\n", "")
        if argv[0] == "/usr/local/sbin/sigmond-radiod-ready":
            return subprocess.CompletedProcess(argv, self.ready_rc, "", "")
        if argv[:2] == ["systemctl", "restart"]:
            bad = [u for u in argv[2:] if u in self.fail_units]
            if bad:
                return subprocess.CompletedProcess(argv, 1, "", f"failed: {bad[0]}\n")
        if argv[:2] == ["systemctl", "show"] and self.show is not None:
            return subprocess.CompletedProcess(argv, 0, self.show(argv), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def restart_argv(self, unit):
        return [c for c in self.calls if c[:2] == ["systemctl", "restart"] and unit in c[2:]]

    def restarts(self):
        return [c for c in self.calls if c[:2] == ["systemctl", "restart"]]

    def is_active_argv(self, unit):
        return [c for c in self.calls if c[:2] == ["systemctl", "is-active"] and c[2] == unit]

    def readiness_argv(self, unit=None):
        return [c for c in self.calls
               if c and c[0] == "/usr/local/sbin/sigmond-radiod-ready"
               and (unit is None or unit in c)]

    def index_of(self, argv):
        return self.calls.index(argv)


@_NO_REAL_CATALOG
class AlignRestartTests(unittest.TestCase):
    """Task 4 + Fix round 1 (I2, I3): radiod first (with a per-unit
    readiness wait), then its consumers — restarting only units that are
    already active, every subprocess call timed out and exception-safe,
    all in-process, never a child `smd restart`."""

    def test_radiod_first_orders_before_ready_before_consumers(self):
        fake = _FakeRestartRun()
        services = {align_live.RADIOD: ["radiod@default.service"],
                   "psk-recorder": ["psk-recorder@default.service"]}
        smd._align_restart(True, ["psk-recorder"], services, run=fake, say=lambda *_: None)
        radiod_restart = fake.restart_argv("radiod@default.service")[0]
        consumer_restart = fake.restart_argv("psk-recorder@default.service")[0]
        ready = fake.readiness_argv("radiod@default.service")[0]
        self.assertLess(fake.index_of(radiod_restart), fake.index_of(ready))
        self.assertLess(fake.index_of(ready), fake.index_of(consumer_restart))

    def test_readiness_failure_skips_consumers_without_restarting_them(self):
        fake = _FakeRestartRun(ready_rc=1)
        services = {align_live.RADIOD: ["radiod@default.service"],
                   "psk-recorder": ["psk-recorder@default.service"]}
        steps = smd._align_restart(True, ["psk-recorder"], services, run=fake,
                                   say=lambda *_: None)
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
                                   say=lambda *_: None)
        by_component = {s.component: s for s in steps}
        self.assertEqual(by_component["a"].outcome, "restarted")
        self.assertEqual(by_component["b"].outcome, "failed")
        self.assertEqual(by_component["c"].outcome, "restarted")
        self.assertEqual(fake.restart_argv("c-recorder@default.service"),
                         [["systemctl", "restart", "c-recorder@default.service"]])

    def test_argv_never_contains_a_child_smd(self):
        # Fix round 1 / M8: check every argv element, not just membership —
        # a full path like ".../bin/smd" would slip past `"smd" not in argv`.
        fake = _FakeRestartRun()
        services = {align_live.RADIOD: ["radiod@default.service"],
                   "psk-recorder": ["psk-recorder@default.service"]}
        smd._align_restart(True, ["psk-recorder"], services, run=fake, say=lambda *_: None)
        self.assertTrue(fake.calls)
        for argv in fake.calls:
            for a in argv:
                self.assertNotEqual(os.path.basename(str(a)), "smd")
                self.assertFalse(str(a).endswith("/bin/smd"))

    # --- Fix round 1 / I2: never start a stopped unit ---

    def test_inactive_radiod_unit_is_left_alone(self):
        fake = _FakeRestartRun(inactive_units={"radiod@b.service"})
        services = {align_live.RADIOD: ["radiod@a.service", "radiod@b.service"]}
        steps = smd._align_restart(True, [], services, run=fake, say=lambda *_: None)
        self.assertEqual(fake.restart_argv("radiod@a.service"),
                         [["systemctl", "restart", "radiod@a.service"]])
        self.assertEqual(fake.restart_argv("radiod@b.service"), [])
        self.assertEqual(fake.readiness_argv("radiod@a.service"),
                         [["/usr/local/sbin/sigmond-radiod-ready", "--unit",
                           "radiod@a.service", "90"]])
        self.assertEqual(fake.readiness_argv("radiod@b.service"), [])
        self.assertEqual(steps, [align_apply.Step(align_live.RADIOD, "restarted", "ready")])

    def test_inactive_consumer_unit_is_left_alone(self):
        fake = _FakeRestartRun(inactive_units={"c2.service"})
        services = {"c": ["c1.service", "c2.service"]}
        steps = smd._align_restart(False, ["c"], services, run=fake, say=lambda *_: None)
        self.assertEqual(fake.restart_argv("c1.service"),
                         [["systemctl", "restart", "c1.service"]])
        self.assertEqual(fake.restart_argv("c2.service"), [])
        self.assertEqual(steps, [align_apply.Step("c", "restarted")])

    def test_component_whose_only_unit_is_inactive_is_left_not_restarted(self):
        fake = _FakeRestartRun(inactive_units={"c1.service"})
        steps = smd._align_restart(False, ["c"], {"c": ["c1.service"]}, run=fake,
                                   say=lambda *_: None)
        self.assertEqual(fake.restart_argv("c1.service"), [])
        self.assertEqual(steps, [align_apply.Step("c", "left", "not running — left alone")])

    def test_no_active_radiod_unit_makes_radiod_not_restarted_consumers_proceed(self):
        fake = _FakeRestartRun(inactive_units={"radiod@default.service"})
        services = {align_live.RADIOD: ["radiod@default.service"],
                   "psk-recorder": ["psk-recorder@default.service"]}
        steps = smd._align_restart(True, ["psk-recorder"], services, run=fake,
                                   say=lambda *_: None)
        self.assertEqual(fake.restart_argv("radiod@default.service"), [])
        self.assertEqual(fake.readiness_argv(), [])
        self.assertEqual(fake.restart_argv("psk-recorder@default.service"),
                         [["systemctl", "restart", "psk-recorder@default.service"]])
        self.assertEqual(steps, [align_apply.Step("psk-recorder", "restarted")])

    # --- Fix round 1 / I3: timeouts, and no exception escapes mid-sequence ---

    def test_readiness_timeout_fails_radiod_and_skips_consumers_without_a_traceback(self):
        ready_argv = ["/usr/local/sbin/sigmond-radiod-ready", "--unit",
                     "radiod@default.service", "90"]
        exc = subprocess.TimeoutExpired(cmd=ready_argv, timeout=120)
        fake = _FakeRestartRun(raise_on={tuple(ready_argv): exc})
        services = {align_live.RADIOD: ["radiod@default.service"],
                   "psk-recorder": ["psk-recorder@default.service"]}
        steps = smd._align_restart(True, ["psk-recorder"], services, run=fake,
                                   say=lambda *_: None)
        by_component = {s.component: s for s in steps}
        self.assertEqual(by_component[align_live.RADIOD].outcome, "failed")
        self.assertEqual(by_component["psk-recorder"].outcome, "skipped")
        self.assertEqual(fake.restart_argv("psk-recorder@default.service"), [])

    def test_consumer_restart_oserror_fails_that_one_and_continues(self):
        b_restart = ["systemctl", "restart", "b.service"]
        fake = _FakeRestartRun(raise_on={tuple(b_restart): OSError("no systemctl")})
        services = {"a": ["a.service"], "b": ["b.service"], "c": ["c.service"]}
        steps = smd._align_restart(False, ["a", "b", "c"], services, run=fake,
                                   say=lambda *_: None)
        by_component = {s.component: s for s in steps}
        self.assertEqual(by_component["a"].outcome, "restarted")
        self.assertEqual(by_component["b"].outcome, "failed")
        self.assertEqual(by_component["c"].outcome, "restarted")
        self.assertEqual(fake.restart_argv("c.service"),
                         [["systemctl", "restart", "c.service"]])


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
        # A move, so this is not the no-op run (Fix round 1 / I-3).
        steps = [align_apply.Step("sigmond", "current"),
                 align_apply.Step("hf-timestd", "moved", "a -> b", 10)]
        rc, out, mocks = run_apply(
            live={"sigmond": "daba1f6", "hf-timestd": "459bee6", "ka9q-radio": "401992c"},
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
        _quiet_live_stages(),
        _no_real_record_live(),
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

    # --- Task 7: the Plan 2b notice is gone; restarts replace it ---

    def test_plan_2b_notice_is_gone_after_a_move(self):
        steps = [align_apply.Step("sigmond", "current"),
                 align_apply.Step("hf-timestd", "moved", "a -> b", 10)]
        rc, out, mocks = run_apply(
            live={"sigmond": "daba1f6", "hf-timestd": "459bee6", "ka9q-radio": "401992c"},
            apply_plan_result=steps)
        self.assertIn("hf-timestd: moved", out)
        self.assertNotIn("Plan 2b", out)
        self.assertNotIn("until restarted", out)

    def test_no_plan_2b_notice_when_nothing_moved(self):
        rc, out, mocks = run_apply(
            live={"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
            apply_plan_result=[align_apply.Step("sigmond", "current")])
        self.assertNotIn("Plan 2b", out)

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

    # --- Task 5: Ctx.build wired to _align_build ---

    def test_ctx_build_wires_to_align_build(self):
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
        with mock.patch.object(smd, "_build_ka9q_radio", return_value=True) as m_build:
            rc = seen["ctx"].build("ka9q-radio", Path("/srv/base/ka9q-radio"))
        self.assertEqual(rc, 0)
        m_build.assert_called_once_with(Path("/srv/base/ka9q-radio"), force=True)


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


# ---------------------------------------------------------------------------
# Task 7 — the make-live stages: image refresh, bring-up, record, restarts,
# fast checks, the live record; --no-restart; the dry run's restarts:
# section; smd align --verify
# ---------------------------------------------------------------------------

def _staleness(stale=None, running=None, radiod_consumers=None, consumes=None):
    return {"stale": dict(stale or {}), "running": set(running or ()),
            "radiod_consumers": set(radiod_consumers or ()),
            "consumes": dict(consumes or {}), "started_at": {}, "moved_at": {}}


LIVE_ALIGNED = {"sigmond": "daba1f6", "hf-timestd": "459bee6", "ka9q-radio": "401992c"}


class AlignMakeLiveApplyTests(unittest.TestCase):
    """The stage order and what each stage's outcome lets through."""

    def _run(self, *, moves=None, images=(), bringup=(), staleness=None,
             services=None, restart_steps=None, restarted_units=(), checks=None,
             no_restart=False, fast_checks=None, extra_patches=(),
             aligned_release=APPLY_REL.tag, aligned_live=_LIVE_PASSED):
        order = []
        moves = moves if moves is not None else [
            align_apply.Step("sigmond", "current"),
            align_apply.Step("hf-timestd", "moved", "459bee6 -> 5c8196d", 10)]

        def fake_restart(radiod_first, components, services_, *, units_out=None, **kw):
            order.append("restart")
            if units_out is not None:
                units_out.extend(restarted_units)
            return list(restart_steps if restart_steps is not None else [])

        m_restart = mock.Mock(side_effect=fake_restart)
        m_images = mock.Mock(side_effect=lambda tag: order.append("images") or list(images))
        m_bringup = mock.Mock(side_effect=lambda **kw: order.append("bringup") or list(bringup))
        m_checks = fast_checks or mock.Mock(
            side_effect=lambda *a, **k: order.append("checks") or dict(checks or _CHECKS_PASS))
        m_record_live = mock.Mock(side_effect=lambda path, live: order.append(
            f"live:{live.get('complete')}"))
        m_update = mock.Mock()
        out = io.StringIO()
        with contextlib.ExitStack() as st:
            m_record, m_history = _apply_patches(
                st, rel=APPLY_REL, live=LIVE_ALIGNED,
                _align_refresh_image_files=m_images, _align_bringup=m_bringup,
                _align_restart=m_restart, _align_fast_checks=m_checks,
                _align_services=mock.Mock(return_value=services or {"hf-timestd": ["hf-a.service"]}),
                _align_staleness=mock.Mock(return_value=staleness or _staleness(
                    stale={"hf-timestd": "its checkout moved after it started"},
                    running={"hf-timestd"})))
            st.enter_context(mock.patch("sigmond.provenance.record_update", m_update))
            m_record.side_effect = lambda *a, **k: order.append("record")
            st.enter_context(mock.patch("sigmond.align_apply.apply_plan",
                                        side_effect=lambda rel, items, ctx:
                                        order.append("moves") or list(moves)))
            st.enter_context(mock.patch("sigmond.align_apply.record_live", m_record_live))
            # Never the real /run/hf-timestd/authority.json (Fix round 1 audit).
            st.enter_context(mock.patch("sigmond.heartbeat._read_authority", return_value={}))
            st.enter_context(mock.patch.object(smd, "_align_aligned_release",
                                               return_value=aligned_release))
            st.enter_context(mock.patch.object(smd, "_align_aligned_live",
                                               return_value=aligned_live))
            for p in extra_patches:
                st.enter_context(p)
            st.enter_context(contextlib.redirect_stdout(out))
            rc = smd.cmd_align(_apply_args("/opt/git/sigmond", no_restart=no_restart))
        return rc, out.getvalue(), dict(order=order, record=m_record, history=m_history,
                                        restart=m_restart, images=m_images, bringup=m_bringup,
                                        checks=m_checks, record_live=m_record_live,
                                        update=m_update)

    def test_stage_order_moves_images_bringup_record_restart_checks(self):
        rc, out, m = self._run(
            bringup=[align_apply.Step("config render", "ran")],
            restart_steps=[align_apply.Step("hf-timestd", "restarted")],
            restarted_units=["hf-a.service"])
        self.assertEqual(m["order"], ["moves", "images", "bringup", "record", "restart",
                                      "live:False", "checks", "live:True"])
        self.assertEqual(rc, 0)
        self.assertIn("  restarts:", out)
        self.assertIn("  checks:", out)
        self.assertEqual(m["checks"].call_args[0][0], ["hf-a.service"])
        live = m["record_live"].call_args[0][1]
        self.assertEqual(m["record_live"].call_args[0][0], align_apply.ALIGNED_RECORD)
        self.assertEqual(live["restarted"], ["hf-timestd"])
        self.assertEqual(live["failed"], [])
        self.assertIs(live["complete"], True)
        self.assertTrue(live["units_stable"])
        self.assertIn("at", live)
        self.assertIn("baseline", m["checks"].call_args.kwargs)
        restart_lines = [c[0][1]["what"] for c in m["update"].call_args_list
                         if "restarted" in c[0][1]["what"]]
        self.assertEqual(restart_lines, ["smd align --apply v3.53: hf-timestd restarted"])

    def test_bringup_re_runs_site_timing_only_when_this_run_refreshed_it(self):
        rc, out, m = self._run(
            images=[align_apply.Step("/usr/local/sbin/sigmond-site-timing", "refreshed")])
        self.assertIs(m["bringup"].call_args.kwargs.get("site_timing_refreshed"), True)
        rc, out, m = self._run(
            images=[align_apply.Step("/usr/local/sbin/sigmond-location-check", "refreshed")])
        self.assertIs(m["bringup"].call_args.kwargs.get("site_timing_refreshed"), False)
        rc, out, m = self._run()
        self.assertIs(m["bringup"].call_args.kwargs.get("site_timing_refreshed"), False)

    def test_bringup_failure_means_no_record_and_no_restart(self):
        rc, out, m = self._run(
            bringup=[align_apply.Step("sigmond-site-timing", "ran"),
                     align_apply.Step("config render", "failed", "boom")])
        self.assertEqual(rc, 1)
        m["record"].assert_not_called()
        m["restart"].assert_not_called()
        m["checks"].assert_not_called()
        m["record_live"].assert_not_called()
        self.assertIn("config render: failed — boom", out)
        # The moves happened, so their history lines are still written.
        m["history"].assert_called_once()
        self.assertIn(align_apply.Step("hf-timestd", "moved", "459bee6 -> 5c8196d", 10),
                      m["history"].call_args[0][1])

    def test_refused_image_file_blocks_record_bringup_and_restart(self):
        rc, out, m = self._run(
            images=[align_apply.Step("/usr/local/sbin/sigmond-site-timing", "refused",
                                     "could not compare — not refreshed")])
        self.assertEqual(rc, 1)
        m["bringup"].assert_not_called()
        m["record"].assert_not_called()
        m["restart"].assert_not_called()

    def test_blocking_move_stops_before_images(self):
        rc, out, m = self._run(moves=[align_apply.Step("hf-timestd", "failed", "git fetch failed")])
        self.assertEqual(rc, 1)
        self.assertEqual(m["order"], ["moves", "record"])
        m["images"].assert_not_called()
        m["restart"].assert_not_called()

    def test_no_restart_never_restarts_and_names_what_still_runs_old_code(self):
        rc, out, m = self._run(
            no_restart=True, services={"hf-timestd": ["a"], "psk-recorder": ["b"]},
            staleness=_staleness(stale={"hf-timestd": "x", "psk-recorder": "y"},
                                 running={"hf-timestd", "psk-recorder"}))
        m["restart"].assert_not_called()
        m["checks"].assert_not_called()
        m["record"].assert_called_once()
        self.assertIn("2 services still run old code: hf-timestd, psk-recorder — "
                      "re-run smd align --apply to restart them", out)
        self.assertEqual(rc, 0)

    def test_nrestarts_rose_makes_units_unstable_and_exits_1(self):
        # The real _align_fast_checks, over a fake systemctl: the unit
        # restarted once more during the 120 s wait.
        shows = {"n": 0}

        def fake_run(argv, **kw):
            if argv[:2] == ["systemctl", "show"]:
                shows["n"] += 1
                n = 0 if shows["n"] == 1 else 1
                return subprocess.CompletedProcess(argv, 0, f"NRestarts={n}\nActiveState=active\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        m_sleep = mock.Mock()
        rc, out, m = self._run(
            restart_steps=[align_apply.Step("hf-timestd", "restarted")],
            restarted_units=["hf-a.service"], fast_checks=smd._align_fast_checks,
            extra_patches=[mock.patch("subprocess.run", fake_run),
                           mock.patch("time.sleep", m_sleep)])
        self.assertEqual(rc, 1)
        m_sleep.assert_called_once_with(120)
        live = m["record_live"].call_args[0][1]
        self.assertFalse(live["units_stable"])
        self.assertIn("hf-a.service", " ".join(live["notes"]))

    def test_nothing_restarted_writes_a_complete_live_record(self):
        # Final review / I2: the restart stage ran, so the live block is
        # written — without it the next run could never be a no-op.
        rc, out, m = self._run(staleness=_staleness())
        m["checks"].assert_not_called()
        m["record_live"].assert_called_once()
        live = m["record_live"].call_args[0][1]
        self.assertEqual((live["restarted"], live["failed"], live["complete"]), ([], [], True))
        self.assertIsNone(live["units_stable"])
        self.assertEqual(rc, 0)

    def test_failed_restart_exits_1(self):
        rc, out, m = self._run(restart_steps=[align_apply.Step("hf-timestd", "failed", "x")])
        self.assertEqual(rc, 1)

    def test_failed_fast_check_exits_1(self):
        rc, out, m = self._run(
            restart_steps=[align_apply.Step("hf-timestd", "restarted")],
            restarted_units=["hf-a.service"],
            checks=dict(_CHECKS_PASS, heartbeat_sent=False, notes=["heartbeat: exit 1"]))
        self.assertEqual(rc, 1)
        self.assertIn("heartbeat: exit 1", out)

    def test_manifest_changed_restarts_hs_uploader_after_the_ordered_components(self):
        from sigmond.commands import uploader
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            if argv[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(argv, 0, "active\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        rc, out, m = self._run(
            bringup=[align_apply.Step("admin uploader manifest --write", "ran", "manifest changed")],
            restart_steps=[align_apply.Step("hf-timestd", "restarted")],
            restarted_units=["hf-a.service"],
            extra_patches=[mock.patch("subprocess.run", fake_run)])
        self.assertEqual(rc, 0)
        self.assertIn(["systemctl", "restart", uploader.SERVICE], calls)
        self.assertEqual(m["order"][-4:], ["restart", "live:False", "checks", "live:True"])
        self.assertIn(uploader.SERVICE, m["checks"].call_args[0][0])
        self.assertEqual(m["record_live"].call_args[0][1]["restarted"],
                         ["hf-timestd", "hs-uploader"])

    # --- Fix round 1 / I-3: a run with nothing to do stays a no-op ---

    def test_nothing_to_do_is_a_no_op(self):
        rc, out, m = self._run(moves=[align_apply.Step("sigmond", "current"),
                                      align_apply.Step("hf-timestd", "left", align.AHEAD_NOTE)],
                               staleness=_staleness(running={"hf-timestd"}))
        self.assertEqual(rc, 0)
        self.assertIn("  aligned — nothing to do", out)
        m["bringup"].assert_not_called()
        m["record"].assert_not_called()
        m["restart"].assert_not_called()
        m["record_live"].assert_not_called()
        m["history"].assert_not_called()

    # --- Final review / I2: a failed or interrupted restart stage is
    # never a no-op ---

    def _quiet_run(self, aligned_live, **kw):
        """Nothing moved, nothing stale, aligned.json names this release."""
        return self._run(moves=[align_apply.Step("sigmond", "current")],
                         staleness=_staleness(running={"hf-timestd"}),
                         aligned_live=aligned_live, **kw)

    def test_complete_passing_live_block_is_a_no_op(self):
        rc, out, m = self._quiet_run(dict(_LIVE_PASSED))
        self.assertIn("aligned — nothing to do", out)
        m["bringup"].assert_not_called()
        self.assertEqual(rc, 0)

    def test_no_live_block_is_not_a_no_op(self):
        rc, out, m = self._quiet_run(None)
        self.assertNotIn("nothing to do", out)
        m["bringup"].assert_called_once()
        m["record"].assert_called_once()
        self.assertIs(m["record_live"].call_args[0][1]["complete"], True)

    def test_incomplete_live_block_is_not_a_no_op_and_rechecks_what_it_restarted(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            if argv[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(argv, 0, "active\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        rc, out, m = self._quiet_run(dict(_LIVE_PASSED, complete=False),
                                     services={"hf-timestd": ["hf-a.service", "hf-t.timer"],
                                               "psk-recorder": ["psk.service"]},
                                     extra_patches=[mock.patch("subprocess.run", fake_run)])
        self.assertNotIn("nothing to do", out)
        m["bringup"].assert_called_once()
        m["restart"].assert_not_called()
        self.assertEqual(m["checks"].call_args[0][0], ["hf-a.service"])
        self.assertFalse(any(a[:2] == ["systemctl", "restart"] for a in calls))
        self.assertEqual(m["order"][-3:], ["live:False", "checks", "live:True"])
        self.assertEqual(m["record_live"].call_args[0][1]["restarted"], [])
        self.assertEqual(rc, 0)

    def test_recheck_skips_a_unit_no_longer_running(self):
        def fake_run(argv, **kw):
            if argv[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(argv, 3, "inactive\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        rc, out, m = self._quiet_run(dict(_LIVE_PASSED, complete=False),
                                     extra_patches=[mock.patch("subprocess.run", fake_run)])
        m["checks"].assert_not_called()

    def test_live_block_with_a_failed_restart_is_not_a_no_op(self):
        rc, out, m = self._quiet_run(dict(_LIVE_PASSED, failed=["psk-recorder"]))
        self.assertNotIn("nothing to do", out)
        m["bringup"].assert_called_once()

    def test_live_block_with_a_failed_check_is_not_a_no_op(self):
        for key in ("units_stable", "authority_fresh", "heartbeat_sent"):
            rc, out, m = self._quiet_run(dict(_LIVE_PASSED, **{key: False}))
            self.assertNotIn("nothing to do", out, key)
            m["bringup"].assert_called_once()

    def test_live_block_from_older_code_without_complete_is_not_a_no_op(self):
        old = {k: v for k, v in _LIVE_PASSED.items() if k not in ("complete", "failed")}
        rc, out, m = self._quiet_run(old)
        self.assertNotIn("nothing to do", out)

    def test_every_restart_failed_still_writes_the_live_block(self):
        rc, out, m = self._run(restart_steps=[align_apply.Step("hf-timestd", "failed", "x")])
        self.assertEqual(rc, 1)
        live = m["record_live"].call_args[0][1]
        self.assertEqual(live["failed"], ["hf-timestd"])
        self.assertEqual(live["restarted"], [])
        self.assertIs(live["complete"], True)

    def test_ctrl_c_during_the_wait_leaves_complete_false(self):
        seen = []
        m_live = mock.Mock(side_effect=lambda path, live: seen.append(dict(live)))

        def interrupted(*a, **k):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self._run(restart_steps=[align_apply.Step("hf-timestd", "restarted")],
                      restarted_units=["hf-a.service"],
                      fast_checks=mock.Mock(side_effect=interrupted),
                      extra_patches=[mock.patch("sigmond.align_apply.record_live", m_live)])
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0]["complete"], False)
        self.assertEqual(seen[0]["restarted"], ["hf-timestd"])

    # --- Final review / I8: a stale authority fails the run ---

    def test_stale_authority_exits_1(self):
        rc, out, m = self._run(
            restart_steps=[align_apply.Step("hf-timestd", "restarted")],
            restarted_units=["hf-a.service"],
            checks=dict(_CHECKS_PASS, authority_fresh=False,
                        notes=["hf-timestd: authority.json stale (90s old)"]))
        self.assertEqual(rc, 1)
        self.assertIn("authority fresh: NO", out)
        self.assertIs(m["record_live"].call_args[0][1]["authority_fresh"], False)

    def test_aligned_live_reads_the_record(self):
        with tempfile.TemporaryDirectory() as d:
            rec = Path(d) / "aligned.json"
            with mock.patch.object(align_apply, "ALIGNED_RECORD", rec):
                self.assertIsNone(smd._align_aligned_live())
                rec.write_text('{"release": "v3.53"}')
                self.assertIsNone(smd._align_aligned_live())
                rec.write_text('{"release": "v3.53", "live": {"complete": true}}')
                self.assertEqual(smd._align_aligned_live(), {"complete": True})
                rec.write_text('{"release": "v3.53", "live": [1]}')
                self.assertIsNone(smd._align_aligned_live())
                rec.write_text("[1, 2]")
                self.assertIsNone(smd._align_aligned_live())

    def test_aligned_release_reads_the_record(self):
        with tempfile.TemporaryDirectory() as d:
            rec = Path(d) / "aligned.json"
            with mock.patch.object(align_apply, "ALIGNED_RECORD", rec):
                self.assertIsNone(smd._align_aligned_release())
                rec.write_text('{"release": "v3.53"}')
                self.assertEqual(smd._align_aligned_release(), "v3.53")
                rec.write_text("[1, 2]")
                self.assertIsNone(smd._align_aligned_release())

    def test_nothing_changed_but_not_yet_recorded_is_not_a_no_op(self):
        # A sigmond-only release: the re-exec'd child sees sigmond current and
        # nothing stale, but aligned.json does not name this release yet.
        for recorded in (None, "v3.52"):
            rc, out, m = self._run(moves=[align_apply.Step("sigmond", "current")],
                                   staleness=_staleness(running={"hf-timestd"}),
                                   aligned_release=recorded)
            self.assertNotIn("nothing to do", out)
            m["bringup"].assert_called_once()
            m["record"].assert_called_once()

    def test_only_stale_services_still_runs_bringup_record_and_restarts(self):
        rc, out, m = self._run(
            moves=[align_apply.Step("sigmond", "current")],
            restart_steps=[align_apply.Step("hf-timestd", "restarted")],
            restarted_units=["hf-a.service"])
        self.assertEqual(m["order"], ["moves", "images", "bringup", "record", "restart",
                                      "live:False", "checks", "live:True"])
        self.assertNotIn("nothing to do", out)
        self.assertEqual(rc, 0)

    def test_a_refreshed_image_file_is_not_a_no_op(self):
        rc, out, m = self._run(
            moves=[align_apply.Step("sigmond", "current")], staleness=_staleness(),
            images=[align_apply.Step("/usr/local/sbin/sigmond-site-timing", "refreshed")])
        m["bringup"].assert_called_once()
        m["record"].assert_called_once()

    def test_heartbeat_not_checked_passes_and_prints_n_a(self):
        rc, out, m = self._run(
            restart_steps=[align_apply.Step("hf-timestd", "restarted")],
            restarted_units=["hf-a.service"],
            checks=dict(_CHECKS_PASS, heartbeat_sent=None))
        self.assertEqual(rc, 0)
        self.assertIn("heartbeat sent:  n/a", out)

    def test_hs_uploader_restarts_at_most_once(self):
        # Minor: even if a component list somehow names hs-uploader too.
        from sigmond.commands import uploader
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            if argv[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(argv, 0, "active\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        rc, out, m = self._run(
            bringup=[align_apply.Step("admin uploader manifest --write", "ran", "manifest changed")],
            restart_steps=[align_apply.Step("hs-uploader", "restarted")],
            restarted_units=[uploader.SERVICE],
            extra_patches=[mock.patch("subprocess.run", fake_run)])
        self.assertEqual(calls.count(["systemctl", "restart", uploader.SERVICE]), 0)
        self.assertEqual(m["record_live"].call_args[0][1]["restarted"], ["hs-uploader"])
        self.assertEqual(m["checks"].call_args[0][0].count(uploader.SERVICE), 1)

    def test_stale_hs_uploader_restarts_once_with_the_manifest_unchanged(self):
        # Final review / I5: its checkout moved after the daemon started.
        from sigmond.commands import uploader
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            if argv[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(argv, 0, "active\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        rc, out, m = self._run(
            bringup=[align_apply.Step("admin uploader manifest --write", "ran", "")],
            staleness=_staleness(stale={"hs-uploader": "its checkout moved after it started"}),
            extra_patches=[mock.patch("subprocess.run", fake_run)])
        restarts = [a for a in calls if a[:2] == ["systemctl", "restart"]]
        self.assertEqual(restarts, [["systemctl", "restart", uploader.SERVICE]])
        self.assertEqual(rc, 0)

    def test_manifest_unchanged_never_touches_hs_uploader(self):
        from sigmond.commands import uploader
        calls = []
        rc, out, m = self._run(
            bringup=[align_apply.Step("admin uploader manifest --write", "ran", "")],
            extra_patches=[mock.patch("subprocess.run",
                                      lambda argv, **kw: calls.append(argv)
                                      or subprocess.CompletedProcess(argv, 0, "active\n", ""))])
        self.assertFalse(any(uploader.SERVICE in a for a in calls))


class AlignRestartUploaderTests(unittest.TestCase):
    def test_inactive_uploader_is_left_alone(self):
        from sigmond.commands import uploader
        fake = _FakeRestartRun(inactive_units={uploader.SERVICE})
        units = []
        step = smd._align_restart_uploader(run=fake, say=lambda m: None, units_out=units)
        self.assertEqual(step.outcome, "left")
        self.assertEqual(fake.restart_argv(uploader.SERVICE), [])
        self.assertEqual(units, [])

    def test_active_uploader_restarts_via_reset_failed_then_restart(self):
        from sigmond.commands import uploader
        fake = _FakeRestartRun()
        units = []
        step = smd._align_restart_uploader(run=fake, say=lambda m: None, units_out=units)
        self.assertEqual(step, align_apply.Step("hs-uploader", "restarted"))
        self.assertLess(fake.index_of(["systemctl", "reset-failed", uploader.SERVICE]),
                        fake.index_of(["systemctl", "restart", uploader.SERVICE]))
        self.assertEqual(units, [uploader.SERVICE])

    def test_failed_uploader_restart_is_failed(self):
        from sigmond.commands import uploader
        fake = _FakeRestartRun(fail_units={uploader.SERVICE})
        step = smd._align_restart_uploader(run=fake, say=lambda m: None, units_out=[])
        self.assertEqual(step.outcome, "failed")


@_NO_REAL_CATALOG
class AlignRestartUnitsOutTests(unittest.TestCase):
    def test_units_out_lists_only_units_whose_restart_succeeded(self):
        fake = _FakeRestartRun(fail_units={"psk.service"}, inactive_units={"wspr.service"})
        units = []
        smd._align_restart(True, ["hf-timestd", "psk-recorder", "wspr-recorder"],
                           {align_live.RADIOD: ["radiod@a.service"],
                            "hf-timestd": ["hf.service"], "psk-recorder": ["psk.service"],
                            "wspr-recorder": ["wspr.service"]},
                           run=fake, say=lambda m: None, units_out=units)
        self.assertEqual(sorted(units), ["hf.service", "radiod@a.service"])


class AlignPlanRestartsTests(unittest.TestCase):
    """Which restarts --apply asks for, from the staleness read and this
    run's own steps."""

    def _plan(self, steps, st, bringup=()):
        said = []
        plan = smd._align_plan_restarts(steps, list(bringup), st, say=said.append)
        return plan, said

    def test_stale_radiod_restarts_first_and_brings_every_running_consumer(self):
        st = _staleness(stale={align_live.RADIOD: "its checkout moved after it started"},
                        running={align_live.RADIOD, "psk-recorder", "wspr-recorder"},
                        radiod_consumers={"psk-recorder", "wspr-recorder"})
        (radiod_first, comps, uploader), said = self._plan(
            [align_apply.Step(align_live.RADIOD, "moved", "a -> b")], st)
        self.assertTrue(radiod_first)
        self.assertEqual(comps, ["psk-recorder", "wspr-recorder"])
        self.assertFalse(uploader)

    def test_failed_ka9q_radio_step_never_restarts_radiod(self):
        # Controller ruling 2: guards the ordering if a blocking outcome
        # ever stops failing to stop the run before this stage.
        st = _staleness(stale={align_live.RADIOD: "its checkout moved after it started",
                               "hf-timestd": "ka9q-python moved after it started"},
                        running={align_live.RADIOD, "psk-recorder", "hf-timestd"},
                        radiod_consumers={"psk-recorder", "hf-timestd"})
        (radiod_first, comps, _), said = self._plan(
            [align_apply.Step(align_live.RADIOD, "failed",
                              "build failed — do NOT restart radiod")], st)
        self.assertFalse(radiod_first)
        self.assertEqual(comps, ["hf-timestd"])   # stale on its own; psk only followed radiod
        self.assertIn("  radiod not restarted: this run's ka9q-radio step failed — see above", said)

    def test_manifest_changed_asks_for_the_uploader(self):
        (_, _, uploader), _ = self._plan(
            [], _staleness(),
            bringup=[align_apply.Step("admin uploader manifest --write", "ran", "manifest changed")])
        self.assertTrue(uploader)


class AlignStalenessTests(unittest.TestCase):
    """_align_staleness: the inputs align_live needs, read from the station."""

    def _read(self, services, catalog, moved, started, built_at=500.0, consumes=None):
        seen = []

        def head(repo):
            seen.append(Path(repo).name)
            return moved.get(Path(repo).name)
        with mock.patch.object(smd, "_align_head_moved_at", side_effect=head), \
             mock.patch.object(smd, "_align_units_started_at",
                               side_effect=lambda units, run=None: started.get(units[0])), \
             mock.patch.object(smd, "_align_consumes", return_value=consumes or {}), \
             mock.patch.object(smd, "_align_radiod_built_at", return_value=built_at):
            return smd._align_staleness("/opt/git/sigmond", services, catalog), seen

    def test_radiod_moved_at_is_the_binary_not_the_reflog(self):
        st, seen = self._read({align_live.RADIOD: ["radiod@a.service"]}, {},
                              moved={align_live.RADIOD: 9999.0},
                              started={"radiod@a.service": 100.0}, built_at=50.0)
        self.assertEqual(st["moved_at"][align_live.RADIOD], 50.0)
        self.assertNotIn(align_live.RADIOD, st["stale"])

    # --- Final review / P1: radiod is stale only when its binary AND its
    # checkout both moved after it started. A same-bytes rebuild by smd
    # update / smd install --force bumps the mtime alone. ---

    def test_radiod_binary_new_checkout_old_is_not_stale(self):
        st, seen = self._read({align_live.RADIOD: ["radiod@a.service"]}, {},
                              moved={align_live.RADIOD: 50.0},
                              started={"radiod@a.service": 100.0}, built_at=200.0)
        self.assertNotIn(align_live.RADIOD, st["stale"])
        self.assertIsNone(st["moved_at"][align_live.RADIOD])
        self.assertIn(align_live.RADIOD, seen)

    def test_radiod_binary_and_checkout_both_new_is_stale(self):
        st, _ = self._read({align_live.RADIOD: ["radiod@a.service"]}, {},
                           moved={align_live.RADIOD: 150.0},
                           started={"radiod@a.service": 100.0}, built_at=200.0)
        self.assertIn(align_live.RADIOD, st["stale"])
        self.assertEqual(st["moved_at"][align_live.RADIOD], 200.0)

    def test_radiod_checkout_new_binary_old_is_not_stale(self):
        st, _ = self._read({align_live.RADIOD: ["radiod@a.service"]}, {},
                           moved={align_live.RADIOD: 150.0},
                           started={"radiod@a.service": 100.0}, built_at=50.0)
        self.assertNotIn(align_live.RADIOD, st["stale"])

    # --- Final review / I3: a radiod consumer that started before radiod
    # did is stale — stateless, so a later run finds it. ---

    def test_consumer_started_before_radiod_is_stale(self):
        st, _ = self._read({align_live.RADIOD: ["radiod@a.service"], "psk-recorder": ["psk"]}, {},
                           moved={}, started={"radiod@a.service": 200.0, "psk": 100.0})
        self.assertEqual(st["stale"], {"psk-recorder": "radiod restarted after it started"})

    def test_consumer_started_after_radiod_is_not_stale(self):
        st, _ = self._read({align_live.RADIOD: ["radiod@a.service"], "psk-recorder": ["psk"]}, {},
                           moved={}, started={"radiod@a.service": 200.0, "psk": 300.0})
        self.assertEqual(st["stale"], {})

    def test_non_client_started_before_radiod_is_not_stale(self):
        cat = {"igmp-querier": types.SimpleNamespace(kind="infra")}
        st, _ = self._read({align_live.RADIOD: ["radiod@a.service"], "igmp-querier": ["igmp"]},
                           cat, moved={}, started={"radiod@a.service": 200.0, "igmp": 100.0})
        self.assertEqual(st["stale"], {})

    def test_stale_consumer_older_than_radiod_keeps_both_reasons(self):
        st, _ = self._read({align_live.RADIOD: ["radiod@a.service"], "psk-recorder": ["psk"]}, {},
                           moved={"psk-recorder": 150.0},
                           started={"radiod@a.service": 200.0, "psk": 100.0})
        self.assertEqual(st["stale"]["psk-recorder"],
                         "its checkout moved after it started; radiod restarted after it started")

    # --- Final review / I5: the hs-uploader daemon, which the services map
    # may never list, is stale when its checkout moved after it started. ---

    def test_hs_uploader_checkout_moved_after_its_service_started_is_stale(self):
        from sigmond.commands import uploader
        st, _ = self._read({"hf-timestd": ["hf"]}, {}, moved={"hs-uploader": 200.0},
                           started={"hf": 100.0, uploader.SERVICE: 100.0})
        self.assertEqual(st["stale"].get("hs-uploader"), "its checkout moved after it started")

    def test_hs_uploader_started_after_its_checkout_moved_is_not_stale(self):
        from sigmond.commands import uploader
        st, _ = self._read({"hf-timestd": ["hf"]}, {}, moved={"hs-uploader": 50.0},
                           started={"hf": 100.0, uploader.SERVICE: 100.0})
        self.assertNotIn("hs-uploader", st["stale"])

    def test_hs_uploader_not_running_is_not_stale(self):
        st, _ = self._read({"hf-timestd": ["hf"]}, {}, moved={"hs-uploader": 200.0},
                           started={"hf": 100.0})
        self.assertNotIn("hs-uploader", st["stale"])

    def test_running_is_components_with_an_active_unit(self):
        st, _ = self._read({"hf-timestd": ["hf"], "psk-recorder": ["psk"]}, {},
                           moved={}, started={"hf": 100.0, "psk": None})
        self.assertEqual(st["running"], {"hf-timestd"})

    def test_radiod_consumers_are_running_catalog_clients_only(self):
        cat = {"psk-recorder": types.SimpleNamespace(kind="client"),
               "igmp-querier": types.SimpleNamespace(kind="infra"),
               "ka9q-web": types.SimpleNamespace(kind="server"),
               align_live.RADIOD: types.SimpleNamespace(kind="server")}
        services = {n: [n] for n in ("psk-recorder", "igmp-querier", "ka9q-web",
                                     "mystery-recorder", "stopped-recorder", align_live.RADIOD)}
        started = {n: 100.0 for n in services}
        started["stopped-recorder"] = None
        st, _ = self._read(services, cat, moved={}, started=started)
        self.assertEqual(st["radiod_consumers"], {"psk-recorder", "mystery-recorder"})

    def test_empty_reflog_reads_as_not_stale(self):
        st, _ = self._read({"hf-timestd": ["hf"]}, {}, moved={"hf-timestd": None},
                           started={"hf": 100.0})
        self.assertEqual(st["stale"], {})

    def test_library_moved_after_start_makes_its_consumer_stale(self):
        st, seen = self._read({"hf-timestd": ["hf"]}, {},
                              moved={"hf-timestd": 50.0, "ka9q-python": 200.0},
                              started={"hf": 100.0}, consumes={"hf-timestd": {"ka9q-python"}})
        self.assertEqual(st["stale"], {"hf-timestd": "ka9q-python moved after it started"})
        self.assertIn("ka9q-python", seen)


class AlignFastChecksTests(unittest.TestCase):
    def _fake(self, before, after, heartbeat_rc=0, timer_state="enabled"):
        state = {"calls": []}
        seen = {}

        def run(argv, **kw):
            state["calls"].append(argv)
            if argv[:2] == ["systemctl", "show"]:
                unit = argv[-1]
                table = after if unit in seen else before
                seen[unit] = True
                n, active = table[unit]
                return subprocess.CompletedProcess(argv, 0, f"NRestarts={n}\nActiveState={active}\n", "")
            if argv[:2] == ["systemctl", "start"]:
                return subprocess.CompletedProcess(argv, heartbeat_rc, "", "failed\n" if heartbeat_rc else "")
            if argv[:2] == ["systemctl", "is-enabled"]:
                return subprocess.CompletedProcess(argv, 0 if timer_state == "enabled" else 1,
                                                   timer_state + "\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return run, state

    # --- Fix round 1 / I-4: heartbeat off on this station ---

    def test_timer_not_enabled_means_heartbeat_not_checked(self):
        for timer_state in ("disabled", "masked", ""):
            run, state = self._fake({"a.service": (0, "active")}, {"a.service": (0, "active")},
                                    timer_state=timer_state)
            r = smd._align_fast_checks(["a.service"], run=run, sleep=lambda s: None,
                                       now=lambda: 0.0, hf_timestd_running=False)
            self.assertIsNone(r["heartbeat_sent"], timer_state)
            self.assertIn(["systemctl", "is-enabled", "sigmond-heartbeat.timer"], state["calls"])
            self.assertNotIn(["systemctl", "start", "sigmond-heartbeat.service"], state["calls"])

    def test_authority_reader_surprise_is_not_fresh_with_the_exception_noted(self):
        # Minor: non-dict JSON makes _read_authority raise AttributeError.
        run, _ = self._fake({"a.service": (0, "active")}, {"a.service": (0, "active")})
        with mock.patch("sigmond.heartbeat._read_authority",
                        side_effect=AttributeError("'list' object has no attribute 'get'")):
            r = smd._align_fast_checks(["a.service"], run=run, sleep=lambda s: None,
                                       now=lambda: 0.0, hf_timestd_running=True)
        self.assertFalse(r["authority_fresh"])
        self.assertIn("AttributeError", " ".join(r["notes"]))

    def test_stable_units_pass_and_the_wait_is_120_s(self):
        run, state = self._fake({"a.service": (0, "active")}, {"a.service": (0, "active")})
        sleep = mock.Mock()
        r = smd._align_fast_checks(["a.service"], run=run, sleep=sleep, now=lambda: 1000.0,
                                   hf_timestd_running=False)
        sleep.assert_called_once_with(120)
        self.assertEqual(r["units_stable"], True)
        self.assertIsNone(r["authority_fresh"])
        self.assertTrue(r["heartbeat_sent"])
        self.assertIn(["systemctl", "start", "sigmond-heartbeat.service"], state["calls"])

    def test_nrestarts_rise_is_unstable(self):
        run, _ = self._fake({"a.service": (0, "active"), "b.service": (2, "active")},
                            {"a.service": (0, "active"), "b.service": (3, "active")})
        r = smd._align_fast_checks(["a.service", "b.service"], run=run, sleep=lambda s: None,
                                   now=lambda: 0.0, hf_timestd_running=False)
        self.assertFalse(r["units_stable"])
        self.assertTrue(any("b.service" in n and "NRestarts" in n for n in r["notes"]))

    def test_unit_no_longer_active_is_unstable(self):
        run, _ = self._fake({"a.service": (0, "active")}, {"a.service": (0, "failed")})
        r = smd._align_fast_checks(["a.service"], run=run, sleep=lambda s: None,
                                   now=lambda: 0.0, hf_timestd_running=False)
        self.assertFalse(r["units_stable"])

    def test_authority_fresh_when_hf_timestd_runs(self):
        run, _ = self._fake({"a.service": (0, "active")}, {"a.service": (0, "active")})
        with mock.patch("sigmond.heartbeat._read_authority", return_value={"snapshot_age_s": 3}) as m:
            r = smd._align_fast_checks(["a.service"], run=run, sleep=lambda s: None,
                                       now=lambda: 1234.0, hf_timestd_running=True)
        self.assertTrue(r["authority_fresh"])
        from sigmond import heartbeat
        self.assertEqual(m.call_args.kwargs.get("freshness_sec"), heartbeat.AUTHORITY_FRESHNESS_SEC)
        self.assertEqual(m.call_args.kwargs.get("now"), 1234.0)

    def test_stale_authority_fails_with_its_reason(self):
        from sigmond import heartbeat
        run, _ = self._fake({"a.service": (0, "active")}, {"a.service": (0, "active")})
        with mock.patch("sigmond.heartbeat._read_authority",
                        side_effect=heartbeat.ReaderUnavailable("authority.json stale (90s old)")):
            r = smd._align_fast_checks(["a.service"], run=run, sleep=lambda s: None,
                                       now=lambda: 0.0, hf_timestd_running=True)
        self.assertFalse(r["authority_fresh"])
        self.assertIn("authority.json stale (90s old)", " ".join(r["notes"]))

    def test_heartbeat_start_failure(self):
        run, _ = self._fake({"a.service": (0, "active")}, {"a.service": (0, "active")}, heartbeat_rc=1)
        r = smd._align_fast_checks(["a.service"], run=run, sleep=lambda s: None,
                                   now=lambda: 0.0, hf_timestd_running=False)
        self.assertFalse(r["heartbeat_sent"])


class AlignDryRunRestartsTests(unittest.TestCase):
    def test_already_moved_component_that_started_earlier_appears_under_restarts(self):
        live = {"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"}
        with mock.patch.object(smd, "_align_units_started_at",
                               side_effect=lambda units, run=None: 100.0), \
             mock.patch.object(smd, "_align_head_moved_at",
                               side_effect=lambda repo:
                               200.0 if Path(repo).name == "hf-timestd" else 50.0), \
             mock.patch.object(smd, "_align_consumes", return_value={}), \
             mock.patch.object(smd, "_align_radiod_built_at", return_value=None), \
             mock.patch.object(smd, "_need_root") as m_root, \
             mock.patch.object(smd, "lifecycle_lock") as m_lock:
            args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=True)
            out = io.StringIO()
            with mock.patch.object(smd, "_align_services",
                                   return_value={"hf-timestd": ["hf.service"],
                                                 "psk-recorder": ["psk.service"]}), \
                 mock.patch.object(smd, "_align_live_state", return_value=(live, {}, {}, {})), \
                 mock.patch("sigmond.align.image_file_drift", return_value=[]), \
                 mock.patch("sigmond.align.recorded_release", return_value="v3.53"), \
                 mock.patch("sigmond.align.fetch_release", return_value=REL), \
                 mock.patch("sigmond.catalog.load_catalog", return_value={}), \
                 contextlib.redirect_stdout(out):
                rc = smd.cmd_align(args)
        text = out.getvalue()
        self.assertIn("  restarts:", text)
        section = text[text.index("  restarts:"):]
        self.assertIn("hf-timestd", section)
        self.assertIn("its checkout moved after it started", section)
        self.assertNotIn("psk-recorder", section)
        self.assertIn("1 service to restart", text)
        self.assertEqual(rc, 1)
        m_root.assert_not_called()
        m_lock.assert_not_called()

    def test_moving_running_component_is_predicted_and_radiod_line_shown(self):
        live = {"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "deb7bdd"}
        cat = {"psk-recorder": types.SimpleNamespace(kind="client"),
               align_live.RADIOD: types.SimpleNamespace(kind="server")}
        with mock.patch.object(smd, "_align_units_started_at",
                               side_effect=lambda units, run=None: 100.0), \
             mock.patch.object(smd, "_align_head_moved_at", side_effect=lambda repo: 50.0), \
             mock.patch.object(smd, "_align_consumes", return_value={}), \
             mock.patch.object(smd, "_align_radiod_built_at", return_value=50.0):
            args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=True)
            out = io.StringIO()
            with mock.patch.object(smd, "_align_services",
                                   return_value={align_live.RADIOD: ["radiod@a.service"],
                                                 "psk-recorder": ["psk.service"]}), \
                 mock.patch.object(smd, "_align_live_state", return_value=(live, {}, {}, {})), \
                 mock.patch("sigmond.align.image_file_drift", return_value=[]), \
                 mock.patch("sigmond.align.recorded_release", return_value="v3.53"), \
                 mock.patch("sigmond.align.fetch_release", return_value=REL), \
                 mock.patch("sigmond.catalog.load_catalog", return_value=cat), \
                 contextlib.redirect_stdout(out):
                rc = smd.cmd_align(args)
        section = out.getvalue()[out.getvalue().index("  restarts:"):]
        self.assertIn("radiod", section)
        self.assertIn("restarts first", section)
        self.assertIn("psk-recorder", section)
        self.assertIn("follows radiod", section)

    def test_stale_hs_uploader_is_named_and_counted(self):
        said = []
        with mock.patch.object(smd, "_align_services", return_value={}), \
             mock.patch.object(smd, "_align_staleness", return_value=_staleness(
                 stale={"hs-uploader": "its checkout moved after it started"})):
            n = smd._align_print_restarts("/opt/git/sigmond", [], catalog={}, say=said.append)
        self.assertEqual(n, 1)
        self.assertTrue(any("hs-uploader" in l and "stale now" in l for l in said), said)

    def test_nothing_stale_or_moving_prints_none(self):
        rc, out = run({"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      recorded="v3.53")
        self.assertIn("  restarts:\n    none", out)
        self.assertEqual(rc, 0)


def _write_aligned(tmp, at="2026-09-24T12:00:00Z", live_at=None):
    import json
    p = Path(tmp) / "aligned.json"
    data = {"release": "v3.53", "at": at, "components": {}, "left": {}}
    if live_at:
        data["live"] = {"at": live_at, "restarted": ["hf-timestd"]}
    p.write_text(json.dumps(data))
    return p


class AlignVerifyTests(unittest.TestCase):
    def _verify(self, aligned_path, gap_raw, backlog_raw):
        from sigmond import heartbeat
        gap = (mock.patch("sigmond.heartbeat._read_gap_tsv", side_effect=gap_raw)
               if isinstance(gap_raw, Exception)
               else mock.patch("sigmond.heartbeat._read_gap_tsv", return_value=gap_raw))
        m_backlog = mock.Mock(return_value=backlog_raw)
        out = io.StringIO()
        # --verify must never fall through to the dry run: that would
        # reach the network and this box's own checkouts.
        with gap, mock.patch("sigmond.heartbeat._read_backlog", m_backlog), \
             mock.patch("sigmond.align.fetch_release",
                        side_effect=AssertionError("--verify reached the dry run")), \
             mock.patch.object(smd, "_align_live_state",
                               side_effect=AssertionError("--verify reached the dry run")), \
             mock.patch("sigmond.align_apply.ALIGNED_RECORD", aligned_path), \
             mock.patch.object(smd, "_need_root") as m_root, \
             mock.patch.object(smd, "lifecycle_lock") as m_lock, \
             contextlib.redirect_stdout(out):
            rc = smd.cmd_align(argparse.Namespace(release=None, base=None, no_cost=True,
                                                  apply=False, verify=True))
        m_root.assert_not_called()
        m_lock.assert_not_called()
        if m_backlog.called:
            self.assertIsInstance(m_backlog.call_args[0][0], heartbeat.HeartbeatPaths)
        return rc, out.getvalue()

    GOOD_BACKLOG = {"readable": True, "pipelines": [], "cursors": []}

    def _gap(self, row_utc, gaps=0):
        return {"row_utc": row_utc, "gaps": gaps, "channel_hours": 6.0, "rate": 0.0,
                "row_age_s": 60}

    def test_gap_row_older_than_the_live_alignment_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_aligned(tmp, at="2026-09-24T11:00:00Z", live_at="2026-09-24T12:05:00Z")
            rc, out = self._verify(p, self._gap("2026-09-24T12:00Z"), self.GOOD_BACKLOG)
        self.assertEqual(rc, 1)
        self.assertIn("gap sampler has not run since the alignment — "
                      "re-run --verify after the next hour", out)

    def test_both_valid_with_a_newer_row_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_aligned(tmp, at="2026-09-24T11:00:00Z", live_at="2026-09-24T12:05:00Z")
            rc, out = self._verify(p, self._gap("2026-09-24T13:05Z"), self.GOOD_BACKLOG)
        self.assertEqual(rc, 0)
        self.assertIn("VALID", out)
        self.assertIn("0 gap events over 6.00 channel-hours", out)
        self.assertIn("no backlog, no dead letters", out)
        self.assertNotIn("has not run since", out)

    def test_row_within_the_hour_after_the_alignment_is_not_enough(self):
        # Fix round 1 / C5: the row covers the trailing hour, so it must be
        # stamped a full hour after the alignment.
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_aligned(tmp, live_at="2026-09-24T12:05:00Z")
            rc, out = self._verify(p, self._gap("2026-09-24T13:04Z"), self.GOOD_BACKLOG)
        self.assertEqual(rc, 1)
        self.assertIn("gap sampler has not run since the alignment — "
                      "re-run --verify after the next hour", out)

    def test_top_level_at_used_when_there_is_no_live_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_aligned(tmp, at="2026-09-24T12:30:00Z")
            rc, out = self._verify(p, self._gap("2026-09-24T12:00Z"), self.GOOD_BACKLOG)
        self.assertEqual(rc, 1)
        self.assertIn("has not run since", out)

    def test_invalid_gap_verdict_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_aligned(tmp, live_at="2026-09-24T12:05:00Z")
            rc, out = self._verify(p, self._gap("2026-09-24T13:00Z", gaps=4), self.GOOD_BACKLOG)
        self.assertEqual(rc, 1)
        self.assertIn("INVALID", out)

    def test_unreadable_gap_tsv_is_indeterminate_not_a_traceback(self):
        from sigmond import heartbeat
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_aligned(tmp, live_at="2026-09-24T12:05:00Z")
            rc, out = self._verify(p, heartbeat.ReaderUnavailable("/var/log/gap-hourly.tsv missing"),
                                   self.GOOD_BACKLOG)
        self.assertEqual(rc, 1)
        self.assertIn("INDETERMINATE", out)
        self.assertIn("gap-hourly.tsv missing", out)

    def test_no_aligned_record_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._verify(Path(tmp) / "aligned.json", self._gap("x"), self.GOOD_BACKLOG)
        self.assertEqual(rc, 2)
        self.assertIn("no aligned record", out)

    def test_verify_alone_parses_and_reaches_cmd_align(self):
        seen = {}
        with mock.patch.object(sys, "argv", ["smd", "align", "--verify"]), \
             mock.patch.object(smd, "cmd_align",
                               side_effect=lambda a: seen.setdefault("args", a) and 0):
            smd.main()
        self.assertTrue(seen["args"].verify)
        self.assertFalse(seen["args"].apply)

    def test_no_restart_parses(self):
        seen = {}
        with mock.patch.object(sys, "argv", ["smd", "align", "--apply", "--no-restart"]), \
             mock.patch.object(smd, "cmd_align",
                               side_effect=lambda a: seen.setdefault("args", a) and 0):
            smd.main()
        self.assertTrue(seen["args"].no_restart)

    def test_verify_with_apply_is_an_argparse_error(self):
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["smd", "align", "--apply", "--verify"]), \
             mock.patch.object(smd, "cmd_align") as m_cmd, \
             contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                smd.main()
        self.assertEqual(cm.exception.code, 2)
        m_cmd.assert_not_called()
        self.assertIn("not allowed with argument", err.getvalue())


class AlignMakeLiveRobustnessTests(unittest.TestCase):
    def test_no_restart_without_apply_exits_2(self):
        args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=True,
                                  apply=False, no_restart=True)
        out = io.StringIO()
        with mock.patch("sigmond.align.fetch_release",
                        side_effect=AssertionError("reached the dry run")), \
             mock.patch.object(smd, "_align_live_state",
                               side_effect=AssertionError("reached the dry run")), \
             contextlib.redirect_stdout(out):
            rc = smd.cmd_align(args)
        self.assertEqual(rc, 2)
        self.assertIn("--no-restart", out.getvalue())

    def test_dry_run_survives_an_unreadable_service_map(self):
        with mock.patch.object(smd, "_align_live_state",
                               return_value=({"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                              "ka9q-radio": "401992c"}, {}, {}, {})), \
             mock.patch.object(smd, "_align_services", side_effect=RuntimeError("topology broke")), \
             mock.patch("sigmond.align.image_file_drift", return_value=[]), \
             mock.patch("sigmond.align.recorded_release", return_value="v3.53"), \
             mock.patch("sigmond.align.fetch_release", return_value=REL):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = smd.cmd_align(argparse.Namespace(release=None, base="/opt/git/sigmond",
                                                      no_cost=True))
        self.assertIn("could not read this station's services", out.getvalue())
        self.assertIn("topology broke", out.getvalue())
        self.assertEqual(rc, 1)   # unknown staleness is not "aligned"

    def test_apply_staleness_read_failure_exits_1_without_restarting(self):
        m_restart = mock.Mock()
        out = io.StringIO()
        with contextlib.ExitStack() as st:
            _apply_patches(st, rel=APPLY_REL, live=LIVE_ALIGNED,
                           _align_restart=m_restart,
                           _align_services=mock.Mock(side_effect=RuntimeError("topology broke")))
            st.enter_context(mock.patch("sigmond.align_apply.apply_plan",
                                        return_value=[align_apply.Step("sigmond", "current")]))
            st.enter_context(contextlib.redirect_stdout(out))
            rc = smd.cmd_align(_apply_args("/opt/git/sigmond"))
        self.assertEqual(rc, 1)
        m_restart.assert_not_called()
        self.assertIn("topology broke", out.getvalue())


# hf-timestd's real [systemd] units (hf-timestd/deploy.toml), with two
# metrology@ instances the templated unit expands to on a station.
HF_TIMESTD_UNITS = ["timestd-core-recorder.service", "timestd-metrology.target",
                    "timestd-fusion.service", "timestd-l2-calibration.service",
                    "timestd-radiod-monitor.service", "timestd-vtec.service",
                    "timestd-chrony-monitor.timer", "timestd-pipeline-watchdog.timer",
                    "timestd-hpps-watchdog.timer", "timestd-prune.timer",
                    "timestd-metrology@wwv5.service", "timestd-metrology@wwv10.service"]
HF_TIMESTD_SERVICES = ["timestd-core-recorder.service", "timestd-fusion.service",
                       "timestd-l2-calibration.service", "timestd-radiod-monitor.service",
                       "timestd-vtec.service", "timestd-metrology@wwv5.service",
                       "timestd-metrology@wwv10.service"]


@_NO_REAL_CATALOG
class AlignServiceUnitsOnlyTests(unittest.TestCase):
    """Fix round 1 / C-B: only .service units are restarted, timed and
    watched. A timer or target has no NRestarts, and restarting a target
    re-restarts its PartOf= members."""

    def test_restart_touches_only_service_units_all_active(self):
        fake = _FakeRestartRun()
        units = []
        said = []
        steps = smd._align_restart(False, ["hf-timestd"], {"hf-timestd": HF_TIMESTD_UNITS},
                                   run=fake, say=said.append, units_out=units)
        # Final review / I1: ONE restart argv for the whole component —
        # its units Require each other, so one at a time would restart
        # fusion and every metrology channel twice.
        restarts = fake.restarts()
        self.assertEqual(len(restarts), 1, restarts)
        self.assertEqual(sorted(restarts[0][2:]), sorted(HF_TIMESTD_SERVICES))
        resets = [c for c in fake.calls if c[:2] == ["systemctl", "reset-failed"]]
        self.assertEqual(len(resets), 1, resets)
        self.assertEqual(sorted(resets[0][2:]), sorted(HF_TIMESTD_SERVICES))
        self.assertLess(fake.index_of(resets[0]), fake.index_of(restarts[0]))
        self.assertEqual(sorted(units), sorted(HF_TIMESTD_SERVICES))
        for c in fake.calls:
            self.assertFalse(any(str(a).endswith((".timer", ".target")) for a in c), c)
        self.assertFalse(any(".timer" in m or ".target" in m for m in said))
        self.assertEqual(steps, [align_apply.Step("hf-timestd", "restarted")])

        # ...and the fast checks over exactly those units, NRestarts unchanged.
        def run(argv, **kw):
            if argv[:2] == ["systemctl", "show"]:
                if not argv[-1].endswith(".service"):
                    return subprocess.CompletedProcess(argv, 0, "NRestarts=\nActiveState=active\n", "")
                return subprocess.CompletedProcess(argv, 0, "NRestarts=0\nActiveState=active\n", "")
            if argv[:2] == ["systemctl", "is-enabled"]:
                return subprocess.CompletedProcess(argv, 0, "enabled\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        r = smd._align_fast_checks(units, run=run, sleep=lambda s: None, now=lambda: 0.0,
                                   hf_timestd_running=False)
        self.assertTrue(r["units_stable"], r["notes"])

    def test_component_with_only_timers_is_not_restarted(self):
        fake = _FakeRestartRun()
        steps = smd._align_restart(False, ["hamsci-physics"],
                                   {"hamsci-physics": ["physics-a.timer", "physics-b.timer"]},
                                   run=fake, say=lambda m: None)
        self.assertEqual([c for c in fake.calls if c[:2] == ["systemctl", "restart"]], [])
        self.assertNotEqual(steps[0].outcome, "restarted")

    def test_started_at_ignores_timers_and_targets(self):
        def run(argv, **kw):
            ts = {"a.timer": "@10", "m.target": "@20", "s.service": "@100"}[argv[-1]]
            return subprocess.CompletedProcess(
                argv, 0, f"ActiveState=active\nActiveEnterTimestamp={ts}\n", "")
        self.assertEqual(smd._align_units_started_at(["a.timer", "m.target", "s.service"], run=run),
                         100.0)
        self.assertIsNone(smd._align_units_started_at(["a.timer", "m.target"], run=run))


@_NO_REAL_CATALOG
class AlignRestartTimeoutTests(unittest.TestCase):
    """Final review / I1: the restart's client timeout comes from the units'
    own TimeoutStartUSec + TimeoutStopUSec — core-recorder alone has
    TimeoutStartSec=300, so a flat 300 s could fire while systemd still
    waits on it."""

    SHOW = {"a.service": "TimeoutStartUSec=5min\nTimeoutStopUSec=1min 30s\n",
            "b.service": "TimeoutStartUSec=1min 30s\nTimeoutStopUSec=1min 30s\n"}

    def _show(self, argv):
        units = [a for a in argv if a.endswith(".service")]
        return "\n".join(self.SHOW.get(u, "") for u in units)

    def _restart_timeout(self, fake):
        i = next(i for i, c in enumerate(fake.calls) if c[:2] == ["systemctl", "restart"])
        return fake.kwargs[i]["timeout"]

    def test_timeout_is_the_max_start_plus_stop_plus_60(self):
        fake = _FakeRestartRun(show=self._show)
        smd._align_restart(False, ["c"], {"c": ["a.service", "b.service"]}, run=fake,
                           say=lambda m: None)
        show = [c for c in fake.calls if c[:2] == ["systemctl", "show"]
                and "TimeoutStartUSec" in c][0]
        self.assertEqual(show, ["systemctl", "show", "-p", "TimeoutStartUSec",
                                "-p", "TimeoutStopUSec", "a.service", "b.service"])
        self.assertEqual(self._restart_timeout(fake), 300 + 90 + 60)

    def test_unreadable_timeouts_default_to_600(self):
        for out in ("", "TimeoutStartUSec=infinity\nTimeoutStopUSec=infinity\n", "garbage"):
            fake = _FakeRestartRun(show=lambda argv, out=out: out)
            smd._align_restart(False, ["c"], {"c": ["a.service"]}, run=fake, say=lambda m: None)
            self.assertEqual(self._restart_timeout(fake), 600, out)

    def test_parse_usec_forms(self):
        p = smd._align_parse_usec
        self.assertEqual(p("5min"), 300.0)
        self.assertEqual(p("1min 30s"), 90.0)
        self.assertEqual(p("1h 2min 3s"), 3723.0)
        self.assertEqual(p("500ms"), 0.5)
        self.assertEqual(p("45"), 45.0)
        self.assertIsNone(p("infinity"))
        self.assertIsNone(p(""))
        self.assertIsNone(p("5 parsecs"))

    def test_radiod_restarts_in_one_argv_then_readiness_per_unit(self):
        fake = _FakeRestartRun()
        steps = smd._align_restart(True, [], {align_live.RADIOD: ["radiod@a.service",
                                                                   "radiod@b.service"]},
                                   run=fake, say=lambda m: None)
        self.assertEqual(fake.restarts(),
                         [["systemctl", "restart", "radiod@a.service", "radiod@b.service"]])
        self.assertEqual(len(fake.readiness_argv("radiod@a.service")), 1)
        self.assertEqual(len(fake.readiness_argv("radiod@b.service")), 1)
        self.assertEqual(steps, [align_apply.Step(align_live.RADIOD, "restarted", "ready")])

    # --- M1: NRestarts snapshotted right after each component's restart ---

    def test_nrestarts_snapshot_right_after_the_restart(self):
        def show(argv):
            return "NRestarts=3\nActiveState=active\n" if "NRestarts" in argv else ""
        fake = _FakeRestartRun(show=show)
        snap = {}
        smd._align_restart(False, ["c"], {"c": ["a.service", "b.service"]}, run=fake,
                           say=lambda m: None, nrestarts_out=snap)
        self.assertEqual(snap, {"a.service": 3, "b.service": 3})
        restart_i = fake.index_of(fake.restarts()[0])
        shows = [i for i, c in enumerate(fake.calls)
                 if c[:2] == ["systemctl", "show"] and "NRestarts" in c]
        self.assertTrue(shows and all(i > restart_i for i in shows))

    def test_fast_checks_measure_from_the_snapshot(self):
        # A unit that restarted once more between its own restart and the
        # start of the 120 s window is unstable.
        def run(argv, **kw):
            if argv[:2] == ["systemctl", "show"]:
                return subprocess.CompletedProcess(argv, 0, "NRestarts=1\nActiveState=active\n", "")
            if argv[:2] == ["systemctl", "is-enabled"]:
                return subprocess.CompletedProcess(argv, 1, "disabled\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        r = smd._align_fast_checks(["a.service"], run=run, sleep=lambda s: None,
                                   now=lambda: 0.0, hf_timestd_running=False,
                                   baseline={"a.service": 0})
        self.assertFalse(r["units_stable"])
        r = smd._align_fast_checks(["a.service"], run=run, sleep=lambda s: None,
                                   now=lambda: 0.0, hf_timestd_running=False)
        self.assertTrue(r["units_stable"])


class AlignUnitsStartedAtRobustnessTests(unittest.TestCase):
    """M2: systemctl through _align_run, timeout 15; an error ignores that unit."""

    def test_oserror_or_timeout_ignores_that_unit(self):
        seen = []

        def run(argv, **kw):
            seen.append(kw.get("timeout"))
            if argv[-1] == "bad.service":
                raise OSError("no systemctl")
            if argv[-1] == "slow.service":
                raise subprocess.TimeoutExpired(argv, 15)
            return subprocess.CompletedProcess(
                argv, 0, "ActiveState=active\nActiveEnterTimestamp=@100\n", "")
        self.assertEqual(smd._align_units_started_at(
            ["bad.service", "slow.service", "ok.service"], run=run), 100.0)
        self.assertEqual(seen, [15, 15, 15])
        self.assertIsNone(smd._align_units_started_at(["bad.service"], run=run))

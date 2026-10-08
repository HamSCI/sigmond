"""sigmond#48 — `smd update --apply` must not run install after a failed
pull, must show WHY install.sh failed (and keep the full output), and must
bound its network phase.  Real git repos under a scratch `--base`, a fake
install.sh that records its invocation; `runuser` (root-only) is shimmed
away so the privilege drop does not make the executor untestable."""
from __future__ import annotations

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

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_update_exec", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_update_exec", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()
_REAL_RUN = subprocess.run


def _run_without_runuser(cmd, *a, **kw):
    """Tests are not root: `runuser -u X -- git ...` would be refused.
    Strip the privilege-drop prefix and run the git command as ourselves."""
    if isinstance(cmd, list) and cmd[:1] == ["runuser"] and "--" in cmd:
        cmd = cmd[cmd.index("--") + 1:]
    return _REAL_RUN(cmd, *a, **kw)


def _git(*args, cwd):
    return _REAL_RUN(["git", "-c", "user.email=t@example", "-c", "user.name=t", *args],
                     cwd=cwd, check=True, capture_output=True, text=True)


class _Rig(unittest.TestCase):
    """origin (bare) + base/<comp> checkout that is ONE commit behind, with a
    fake install.sh whose behaviour the test chooses."""

    COMP = "fakeclient"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.origin = root / "origin.git"
        _git("init", "--bare", "-b", "main", str(self.origin), cwd=root)
        seed = root / "seed"
        _git("clone", str(self.origin), str(seed), cwd=root)
        (seed / "scripts").mkdir()
        (seed / "scripts" / "install.sh").write_text("#!/bin/bash\necho install-ran > \"$(dirname \"$0\")/../.installed\"\nexit 0\n")
        (seed / "a.txt").write_text("a\n")
        _git("add", "-A", cwd=seed); _git("commit", "-m", "first", cwd=seed)
        _git("push", "-u", "origin", "main", cwd=seed)
        self.base = root / "base"; self.base.mkdir()
        self.host = self.base / self.COMP
        _git("clone", str(self.origin), str(self.host), cwd=root)
        (seed / "b.txt").write_text("b\n")
        _git("add", "-A", cwd=seed); _git("commit", "-m", "second", cwd=seed)
        _git("push", "origin", "main", cwd=seed)
        self.logdir = root / "logs"
        self._patches = [
            mock.patch.object(smd.subprocess, "run", side_effect=_run_without_runuser),
            mock.patch.object(smd, "UPDATE_LOG_DIR", self.logdir),
            mock.patch.object(smd, "_venv_sibling_skew", return_value=([], [])),
        ]
        for p in self._patches: p.start()

    def tearDown(self):
        for p in self._patches: p.stop()
        self._tmp.cleanup()

    def _set_install_sh(self, body: str):
        """Change the HOST checkout's install.sh (committed so the tree is
        clean for the pull) — the version that will run after the pull is
        the one upstream ships, so write it in seed and push."""
        # host is behind; the install.sh that runs is the pulled one, so we
        # must put it upstream.
        root = Path(self._tmp.name); seed = root / "seed"
        (seed / "scripts" / "install.sh").write_text(body)
        _git("add", "-A", cwd=seed); _git("commit", "-m", "install.sh", cwd=seed)
        _git("push", "origin", "main", cwd=seed)

    def _update(self):
        args = types.SimpleNamespace(base=self.base, apply=True, no_fetch=False)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = smd.cmd_update(args)
        return rc, out.getvalue() + err.getvalue()

    def _add_plain_component(self, name):
        """A second, ordinary checkout under base: not pinned, genuinely
        one commit behind, clean, and with no install script to
        complicate the plan — "no installer issue" per the fix request."""
        root = Path(self._tmp.name)
        origin = root / f"{name}-origin.git"
        _git("init", "--bare", "-b", "main", str(origin), cwd=root)
        seed = root / f"{name}-seed"
        _git("clone", str(origin), str(seed), cwd=root)
        (seed / "a.txt").write_text("a\n")
        _git("add", "-A", cwd=seed)
        _git("commit", "-m", "first", cwd=seed)
        _git("push", "-u", "origin", "main", cwd=seed)
        host = self.base / name
        _git("clone", str(origin), str(host), cwd=root)
        (seed / "b.txt").write_text("b\n")
        _git("add", "-A", cwd=seed)
        _git("commit", "-m", "second", cwd=seed)
        _git("push", "origin", "main", cwd=seed)
        expected_sha = _git("rev-parse", "HEAD", cwd=seed).stdout.strip()
        return host, expected_sha


class ExecutorTests(_Rig):
    def test_success_path_pulls_then_installs(self):
        rc, text = self._update()
        self.assertEqual(rc, 0, text)
        self.assertTrue((self.host / ".installed").exists(), text)

    def test_install_is_skipped_when_pull_failed(self):
        # The host KNOWS it is behind (fetched), then the remote goes away:
        # pull must fail, and install.sh must NOT run.
        _git("fetch", cwd=self.host)
        _git("remote", "set-url", "origin", "/nonexistent/origin.git", cwd=self.host)
        rc, text = self._update()
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.host / ".installed").exists(),
                         "install.sh ran after a failed pull")
        self.assertIn("install skipped", text.lower())

    def test_install_failure_shows_why_and_keeps_the_full_output(self):
        self._set_install_sh("#!/bin/bash\necho 'uv sync: ENOSPC no space left on device' >&2\nexit 1\n")
        rc, text = self._update()
        self.assertNotEqual(rc, 0)
        self.assertIn("no space left on device", text)          # the reason, on screen
        logs = list(self.logdir.glob(f"update-*{self.COMP}*.log"))
        self.assertEqual(len(logs), 1, f"expected one update log, got {logs}")
        self.assertIn("no space left on device", logs[0].read_text())
        self.assertIn(str(logs[0]), text)                          # and named


class NetworkBoundTests(unittest.TestCase):
    def test_fetch_and_pull_are_bounded(self):
        seen = []
        def fake_run(cmd, *a, **kw):
            seen.append(kw)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(smd.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(smd.os, "geteuid", return_value=1000):
            smd._git_fetch_as_owner(Path("/tmp/x"))
            smd._git_pull_as_owner(Path("/tmp/x"))
        for kw in seen:
            self.assertGreater(kw.get("timeout", 0), 0)
            env = kw.get("env") or {}
            self.assertIn("GIT_HTTP_LOW_SPEED_TIME", env)
            self.assertIn("GIT_HTTP_LOW_SPEED_LIMIT", env)
            self.assertEqual(env.get("GIT_TERMINAL_PROMPT"), "0")

    def test_timeout_becomes_a_failed_result_not_an_exception(self):
        def fake_run(cmd, *a, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))
        with mock.patch.object(smd.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(smd.os, "geteuid", return_value=1000):
            r = smd._git_fetch_as_owner(Path("/tmp/x"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("timed out", (r.stderr or "").lower())


class HistoryPerStepTests(_Rig):
    """sigmond#50: a killed/failed update must leave provenance for the steps
    it DID apply — record each step as it lands, not once at the end."""

    def _history(self, tmp):
        p = Path(tmp) / "update-history.jsonl"
        return p, (p.read_text().splitlines() if p.exists() else [])

    def test_pull_is_recorded_even_when_install_fails(self):
        import sigmond.provenance as prov
        hist = Path(self._tmp.name) / "update-history.jsonl"
        self._set_install_sh("#!/bin/bash\necho boom >&2\nexit 1\n")
        with mock.patch.object(prov, "HISTORY_FILE", str(hist)):
            rc, text = self._update()
        self.assertNotEqual(rc, 0)
        lines = hist.read_text().splitlines() if hist.exists() else []
        self.assertTrue(any("pull fakeclient" in l for l in lines), lines)
        self.assertFalse(any("install fakeclient" in l for l in lines), lines)

    def test_success_records_pull_then_install(self):
        import sigmond.provenance as prov
        hist = Path(self._tmp.name) / "update-history.jsonl"
        with mock.patch.object(prov, "HISTORY_FILE", str(hist)):
            rc, text = self._update()
        self.assertEqual(rc, 0, text)
        lines = hist.read_text().splitlines()
        self.assertTrue(any("pull fakeclient" in l for l in lines), lines)
        self.assertTrue(any("install fakeclient" in l for l in lines), lines)


class PinnedComponentTests(_Rig):
    """Task 7: bring-up's image build and `smd align` both detach a
    checkout onto `.pin`, and `smd update` must hold it there until
    `--unpin` is given.  A pinned checkout sits on a DETACHED HEAD
    (Controller ruling 5), so `--unpin` has to check out the tracking
    branch before it can `git pull --ff-only`."""

    def _pin_at_head(self):
        """Detach the host checkout onto its current HEAD and drop a
        `.pin` naming it — the shape bring-up / `smd align` leave behind."""
        head = _git("rev-parse", "HEAD", cwd=self.host).stdout.strip()
        _git("checkout", "--detach", head, cwd=self.host)
        (self.host / ".pin").write_text(head + "\n")
        return head

    def test_a_pinned_component_is_held_not_pulled(self):
        self._pin_at_head()
        rc, text = self._update()
        self.assertEqual(rc, 0, text)  # a pin hold alone must not fail the run
        self.assertIn("pinned by smd align", text)
        self.assertFalse((self.host / ".installed").exists(), text)
        self.assertTrue((self.host / ".pin").exists())

    def test_a_pin_hold_does_not_block_or_fail_the_rest_of_the_plan(self):
        """A held component sharing a plan with a real, runnable pull must
        neither block that pull nor push the run's exit code to
        UPDATE_EXIT_HELD — the pin refusal is informational (Controller
        ruling 4), and that must hold in the mixed-plan case, not just
        the all-held ("nothing to do") one."""
        self._pin_at_head()
        other, expected_sha = self._add_plain_component("otherclient")

        rc, text = self._update()

        self.assertEqual(rc, 0, text)
        self.assertIn("[pull] otherclient", text)
        self.assertNotIn("[pull] fakeclient", text)
        other_head = _git("rev-parse", "HEAD", cwd=other).stdout.strip()
        self.assertEqual(other_head, expected_sha, text)
        # The pinned component stayed untouched.
        self.assertFalse((self.host / ".installed").exists(), text)
        self.assertTrue((self.host / ".pin").exists())

    def test_unpin_checks_out_the_branch_then_pulls_and_removes_pin(self):
        self._pin_at_head()
        args = types.SimpleNamespace(base=self.base, apply=True,
                                     no_fetch=False, unpin=True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = smd.cmd_update(args)
        text = out.getvalue() + err.getvalue()

        self.assertEqual(rc, 0, text)
        self.assertFalse((self.host / ".pin").exists(), text)
        branch = _git("rev-parse", "--abbrev-ref", "HEAD",
                      cwd=self.host).stdout.strip()
        self.assertEqual(branch, "main")
        self.assertTrue((self.host / ".installed").exists(), text)

    def test_unpin_checkout_precedes_pull_and_pin_survives_a_failed_pull(self):
        pin = self._pin_at_head()
        # Cache origin/main at "second" locally while the remote still
        # works, then break it — same pattern as
        # test_install_is_skipped_when_pull_failed.
        _git("fetch", cwd=self.host)
        _git("remote", "set-url", "origin", "/nonexistent/origin.git", cwd=self.host)

        calls = []

        def recording_run(cmd, *a, **kw):
            calls.append(list(cmd))
            return _run_without_runuser(cmd, *a, **kw)

        with mock.patch.object(smd.subprocess, "run", side_effect=recording_run):
            args = types.SimpleNamespace(base=self.base, apply=True,
                                         no_fetch=True, unpin=True)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = smd.cmd_update(args)
        text = out.getvalue() + err.getvalue()

        self.assertNotEqual(rc, 0, text)
        self.assertTrue((self.host / ".pin").exists(),
                        "a failed pull must not remove .pin")

        checkout_idx = next(i for i, c in enumerate(calls)
                            if isinstance(c, list) and "checkout" in c and "main" in c)
        pull_idx = next(i for i, c in enumerate(calls)
                        if isinstance(c, list) and "pull" in c and "--ff-only" in c)
        self.assertLess(checkout_idx, pull_idx, calls)

        # A failed pull after a successful branch checkout leaves the
        # checkout ON the branch, OFF its .pin — say so, and how to return.
        self.assertIn(f"off its .pin ({pin[:8]})", text)
        self.assertIn(f"git checkout --detach {pin}", text)
        self.assertIn("install skipped — pull failed above", text)

    def test_unpin_install_skip_says_checkout_failed_when_checkout_failed(self):
        """The install-skip message must name the step that actually
        failed — a checkout failure is not a pull failure, and printing
        "pull failed above" when the pull never even ran is misleading."""
        self._pin_at_head()
        broken = subprocess.CompletedProcess(
            ["git", "checkout", "main"], 1, "", "error: local changes would be overwritten")
        with mock.patch.object(smd, "_git_checkout_branch_as_owner",
                               return_value=broken):
            args = types.SimpleNamespace(base=self.base, apply=True,
                                         no_fetch=False, unpin=True)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = smd.cmd_update(args)
        text = out.getvalue() + err.getvalue()

        self.assertNotEqual(rc, 0, text)
        self.assertIn("install skipped — checkout failed above", text)
        self.assertNotIn("install skipped — pull failed above", text)
        self.assertTrue((self.host / ".pin").exists())


class _NativePinRig(_Rig):
    """A checkout sigmond's own native build pins (onion, wsjtx).

    On 2026-10-07 20:02Z upstream onion gained its first commit in four
    years.  Every station holds onion on branch master AT the pinned
    commit with no `.pin` file, so `smd update` counted that commit as
    "behind" and planned `[pull] onion` plus a service restart.  The
    pull would move the source off the commit libonion was built from.
    """

    def _head(self, repo=None):
        return _git("rev-parse", "HEAD", cwd=repo or self.host).stdout.strip()

    def _origin_head(self):
        return _git("rev-parse", "main", cwd=self.origin).stdout.strip()

    def _pin_constant(self, name, sha):
        """Point one of smd's native pin constants at a scratch commit."""
        p = mock.patch.object(smd, name, sha)
        p.start()
        self.addCleanup(p.stop)

    def _add_pin_file_component(self, name):
        """An ordinary checkout `smd align` has pinned: detached at its HEAD
        with a `.pin` naming it, one commit behind upstream."""
        host, _sha = self._add_plain_component(name)
        head = self._head(host)
        _git("checkout", "--detach", head, cwd=host)
        (host / ".pin").write_text(head + "\n")
        return host

    def _run_update(self, apply=False, unpin=False):
        args = types.SimpleNamespace(base=self.base, apply=apply,
                                     no_fetch=False, unpin=unpin)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = smd.cmd_update(args)
        return rc, out.getvalue() + err.getvalue()


class NativeBuildPinOnionTests(_NativePinRig):
    COMP = "onion"
    CONST = "_ONION_COMMIT"

    def _at_pin(self):
        """HEAD is the pin; upstream sits one commit ahead (setUp)."""
        pin = self._head()
        self._pin_constant(self.CONST, pin)
        return pin

    def test_onion_at_its_pin_is_not_pulled(self):
        """The defect: the one commit upstream gained planned a pull."""
        pin = self._at_pin()
        rc, text = self._run_update()
        self.assertNotIn("[pull] onion", text)
        self.assertNotIn("commit(s) behind", text)
        self.assertNotIn("[restart]", text)
        self.assertEqual(rc, 0, text)
        self.assertEqual(self._head(), pin)

    def test_onion_at_its_pin_is_held_as_a_pin_refusal(self):
        pin = self._at_pin()
        rc, text = self._run_update()
        self.assertIn(f"onion: HELD — pinned by sigmond's native build to "
                      f"{pin[:8]} (docs/native-binaries.md)", text)
        # A pin hold is the steady state: exit 0, and the sentinel the
        # fleet post-check reads (Controller ruling 4).
        self.assertEqual(rc, 0, text)
        self.assertIn("nothing to do", text)

    def test_no_comparison_against_upstream_for_a_native_pin(self):
        self._at_pin()
        _rc, text = self._run_update()
        self.assertNotIn("cannot compare against upstream", text)

    def test_apply_leaves_a_native_pin_where_it_is(self):
        pin = self._at_pin()
        rc, text = self._run_update(apply=True)
        self.assertEqual(self._head(), pin, text)
        self.assertEqual(rc, 0, text)
        self.assertFalse((self.host / ".installed").exists(), text)

    def test_unpin_does_not_release_a_native_pin(self):
        pin = self._at_pin()
        rc, text = self._run_update(apply=True, unpin=True)
        self.assertEqual(self._head(), pin, text)
        self.assertNotIn("[pull] onion", text)
        self.assertEqual(rc, 0, text)
        self.assertEqual(
            sum("onion: stays held under --unpin" in l
                for l in text.splitlines()), 1, text)
        self.assertIn("sigmond's native build pins it", text)

    def test_without_unpin_the_unpin_line_stays_quiet(self):
        self._at_pin()
        _rc, text = self._run_update()
        self.assertNotIn("--unpin", text)

    def test_a_local_edit_does_not_turn_a_native_hold_into_a_failure(self):
        """Nothing is pulled, so nothing can collide with the edit."""
        self._at_pin()
        (self.host / "a.txt").write_text("edited\n")
        rc, text = self._run_update()
        self.assertEqual(rc, 0, text)
        self.assertIn("onion: HELD — pinned by sigmond's native build", text)
        self.assertNotIn("modified file", text)

    def test_a_shorter_pin_constant_matches_the_full_head(self):
        pin = self._head()
        self._pin_constant(self.CONST, pin[:12])
        rc, text = self._run_update()
        self.assertIn("onion: HELD — pinned by sigmond's native build to "
                      f"{pin[:8]}", text)
        self.assertNotIn("[pull] onion", text)

    def test_off_its_pin_onion_plans_nothing_and_warns_once(self):
        """HEAD differs from the pin: the next ka9q-web build returns it,
        so the planner must not pull it somewhere else."""
        head = self._head()
        pin = self._origin_head()            # a commit HEAD is not at
        self._pin_constant(self.CONST, pin)
        rc, text = self._run_update(apply=True)
        self.assertNotIn("[pull] onion", text)
        self.assertNotIn("[restart]", text)
        warning = (f"onion: at {head[:8]}, off sigmond's build pin "
                   f"{pin[:8]} — the next ka9q-web build returns it")
        self.assertEqual(sum(warning in l for l in text.splitlines()), 1, text)
        self.assertNotIn("cannot compare against upstream", text)
        self.assertEqual(self._head(), head, text)
        self.assertEqual(rc, 0, text)

    def test_a_pin_file_hold_and_a_behind_library_behave_as_before(self):
        """Native pins must not disturb the other two kinds of neighbour."""
        self._at_pin()
        self._add_pin_file_component("pinclient")
        self._add_plain_component("otherclient")

        rc, text = self._run_update()

        self.assertEqual(rc, 0, text)
        self.assertIn("pinclient: HELD — pinned by smd align to", text)
        self.assertIn("onion: HELD — pinned by sigmond's native build to", text)
        self.assertIn("[pull] otherclient", text)
        self.assertNotIn("[pull] onion", text)
        self.assertNotIn("[pull] pinclient", text)

    def test_unpin_still_releases_a_pin_file_beside_a_native_pin(self):
        pin = self._at_pin()
        pinned = self._add_pin_file_component("pinclient")

        rc, text = self._run_update(apply=True, unpin=True)

        self.assertEqual(rc, 0, text)
        self.assertFalse((pinned / ".pin").exists(), text)      # released
        self.assertIn("[pull] pinclient", text)
        self.assertEqual(self._head(), pin, text)               # onion held
        self.assertIn("onion: stays held under --unpin", text)


class NativeBuildPinWsjtxTests(_NativePinRig):
    """wsjtx sits on a DETACHED HEAD at its pin (the build checks the commit
    out by hash).  Detached, it had no upstream to count against, so the
    planner only said "cannot compare against upstream"."""

    COMP = "wsjtx"
    CONST = "_WSJTX_COMMIT"

    def test_detached_wsjtx_at_its_pin_is_held(self):
        pin = self._head()
        _git("checkout", "--detach", pin, cwd=self.host)
        self._pin_constant(self.CONST, pin)
        rc, text = self._run_update()
        self.assertIn(f"wsjtx: HELD — pinned by sigmond's native build to "
                      f"{pin[:8]} (docs/native-binaries.md)", text)
        self.assertNotIn("cannot compare against upstream", text)
        self.assertEqual(rc, 0, text)

    def test_detached_wsjtx_off_its_pin_warns_with_its_own_builder(self):
        head = self._head()
        _git("checkout", "--detach", head, cwd=self.host)
        pin = self._origin_head()
        self._pin_constant(self.CONST, pin)
        rc, text = self._run_update()
        self.assertIn(f"wsjtx: at {head[:8]}, off sigmond's build pin "
                      f"{pin[:8]} — the next wsjtx-decoders build returns it",
                      text)
        self.assertNotIn("cannot compare against upstream", text)
        self.assertEqual(rc, 0, text)


class NativeBuildPinTableTests(unittest.TestCase):
    """One table, derived from the build constants — never copied."""

    def test_the_table_covers_onion_and_wsjtx(self):
        self.assertEqual(smd._native_build_pins(),
                         {"onion": smd._ONION_COMMIT,
                          "wsjtx": smd._WSJTX_COMMIT})

    def test_the_table_follows_the_constants(self):
        with mock.patch.object(smd, "_ONION_COMMIT", "a" * 40), \
             mock.patch.object(smd, "_WSJTX_COMMIT", "b" * 40):
            self.assertEqual(smd._native_build_pins(),
                             {"onion": "a" * 40, "wsjtx": "b" * 40})

    def test_every_key_names_a_checkout_the_build_clones(self):
        for name in smd._native_build_pins():
            self.assertIn(Path("/opt/git/sigmond") / name,
                          (smd._ONION_SRC, smd._WSJTX_SRC))

"""2026-11-09: a native source off sigmond's pin gets its build, and the
build's git work never runs as root in a tree another user owns.

The v3.86 update test installed v3.85, where the image build had cloned
ubersdr_wsprd at 529ec5d, then rolled sigmond forward to a pin of 8719788.
`smd update --apply` left the checkout and the decoders at 529ec5d: the
planner said "the next build returns it", and nothing in the update ran
that build.  The same run refused to fetch four native checkouts (ft8_lib,
onion, ubersdr_wsprd, wsjtx) because they were root-owned: `smd install`
elevates itself, so each builder's `git clone` ran as root.

`smd admin native-build` is the step that returns a source to its pin: the
update runs it (as the smd on disk, so a freshly pulled pin applies), and
sigmond's install.sh runs it, which is how an OLDER smd that pulls a newer
sigmond still applies the newer pin in the same run.
"""
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
        "smd_under_test_native_pin", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_native_pin", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()
_REAL_RUN = subprocess.run


def _git(*args, cwd):
    return _REAL_RUN(["git", "-c", "user.email=t@example", "-c",
                      "user.name=t", *args],
                     cwd=cwd, check=True, capture_output=True, text=True)


class _SourceRig(unittest.TestCase):
    """base/ubersdr_wsprd: a real checkout with two commits; HEAD at the
    first, so the second can stand for a moved pin."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "base"
        self.src = self.base / "ubersdr_wsprd"
        self.src.mkdir(parents=True)
        _git("init", "-q", "-b", "main", cwd=self.src)
        (self.src / "a").write_text("a\n")
        _git("add", "-A", cwd=self.src)
        _git("commit", "-qm", "one", cwd=self.src)
        self.old = _git("rev-parse", "HEAD", cwd=self.src).stdout.strip()
        (self.src / "b").write_text("b\n")
        _git("add", "-A", cwd=self.src)
        _git("commit", "-qm", "two", cwd=self.src)
        self.new = _git("rev-parse", "HEAD", cwd=self.src).stdout.strip()
        _git("checkout", "-q", self.old, cwd=self.src)

    def _pin(self, sha):
        p = mock.patch.object(smd, "_UBERSDR_COMMIT", sha)
        p.start()
        self.addCleanup(p.stop)

    def _verb(self, *builders, apply=False):
        args = types.SimpleNamespace(base=str(self.base), apply=apply,
                                     builders=list(builders))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            rc = smd.cmd_admin_native_build(args)
        return rc, out.getvalue()


class NativeRebuildsDueTests(_SourceRig):

    def test_a_source_off_its_pin_is_due_for_its_builder(self):
        self._pin(self.new)
        due = smd._native_rebuilds_due(self.base)
        self.assertEqual(due, [("ubersdr_wsprd", "ubersdr-decoders",
                                self.old, self.new)])

    def test_a_source_at_its_pin_is_not_due(self):
        self._pin(self.old)
        self.assertEqual(smd._native_rebuilds_due(self.base), [])

    def test_a_source_never_cloned_here_is_not_due(self):
        """No checkout: nothing was ever built here, so an update has
        nothing to return to a pin (align builds a missing source)."""
        self._pin(self.new)
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        self.assertEqual(smd._native_rebuilds_due(empty), [])


class NativeBuildVerbTests(_SourceRig):

    def setUp(self):
        super().setUp()
        p = mock.patch.object(smd, "_build_ubersdr_decoders",
                              return_value=True)
        self.build = p.start()
        self.addCleanup(p.stop)
        r = mock.patch.object(smd, "_record_native_build")
        self.record = r.start()
        self.addCleanup(r.stop)

    def test_dry_run_names_the_rebuild_and_builds_nothing(self):
        self._pin(self.new)
        rc, text = self._verb()
        self.assertEqual(rc, 0, text)
        self.assertIn(f"rebuild ubersdr-decoders: pin {self.old[:7]} → "
                      f"{self.new[:7]}", text)
        self.build.assert_not_called()

    def test_apply_runs_the_builder(self):
        self._pin(self.new)
        rc, text = self._verb(apply=True)
        self.assertEqual(rc, 0, text)
        self.build.assert_called_once()
        self.record.assert_called_once()

    def test_at_the_pin_nothing_is_built(self):
        self._pin(self.old)
        rc, text = self._verb(apply=True)
        self.assertEqual(rc, 0, text)
        self.build.assert_not_called()
        self.assertIn("at sigmond's pin", text)

    def test_a_failed_build_exits_nonzero(self):
        self._pin(self.new)
        self.build.return_value = False
        rc, text = self._verb(apply=True)
        self.assertEqual(rc, 1, text)
        self.record.assert_not_called()

    def test_a_named_builder_limits_the_run(self):
        self._pin(self.new)
        rc, text = self._verb("wsjtx-decoders", apply=True)
        self.assertEqual(rc, 0, text)
        self.build.assert_not_called()

    def test_onion_rebuilds_ka9q_web_by_force(self):
        """ka9q-web's own freshness check compares the ka9q-web binary
        only; onion off its pin would pass it untouched."""
        with mock.patch.object(smd, "_build_ka9q_web_with_onion",
                               return_value=True) as kw:
            ok = smd._run_native_builder("ka9q-web", self.base)
        self.assertTrue(ok)
        kw.assert_called_once_with(self.base / "ka9q-web", force=True)


class NativeGitOwnershipTests(unittest.TestCase):
    """The root-owned case.  v3.85's image build ran `smd install`, which
    elevates itself, so each builder's `git clone` ran as root and left a
    root-owned tree under the sigmond-owned /opt/git/sigmond."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name) / "ubersdr_wsprd"
        (self.src / ".git").mkdir(parents=True)
        self.calls = []

        def fake_run(cmd, **kw):
            self.calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        for target, kw in ((smd, dict(new=fake_run, attr="_run")),):
            p = mock.patch.object(target, kw["attr"], side_effect=kw["new"])
            p.start()
            self.addCleanup(p.stop)

    def _as_root(self, owner_action, user=None, sigmond=True):
        for p in (mock.patch.object(smd.os, "geteuid", return_value=0),
                  mock.patch.object(smd, "_native_owner_resolve",
                                    return_value=(owner_action, user)),
                  mock.patch.object(smd, "_sigmond_user_exists",
                                    return_value=sigmond)):
            p.start()
            self.addCleanup(p.stop)

    def _git_calls(self):
        return [c for c in self.calls if "git" in c]

    def test_root_owned_tree_goes_to_sigmond_then_git_runs_as_sigmond(self):
        self._as_root("root-ok")
        smd._native_git(self.src, "fetch", "--quiet", "origin")
        self.assertEqual(self.calls[0],
                         ["chown", "-R", "sigmond:sigmond", str(self.src)])
        git = self._git_calls()
        self.assertEqual(len(git), 1, self.calls)
        self.assertEqual(git[0][:5], ["sudo", "-u", "sigmond", "-H", "--"])
        self.assertIn("fetch", git[0])

    def test_a_tree_owned_by_a_user_runs_git_as_that_user(self):
        self._as_root("drop", "sigmond")
        smd._native_git(self.src, "checkout", "--quiet", "abc")
        self.assertFalse([c for c in self.calls if c[0] == "chown"])
        self.assertEqual(self._git_calls()[0][:5],
                         ["sudo", "-u", "sigmond", "-H", "--"])

    def test_split_ownership_is_repaired_too(self):
        """worktree and .git owned apart: the source is sigmond's own, so
        it goes back to one owner rather than being skipped forever."""
        self._as_root("refuse")
        smd._native_git(self.src, "fetch")
        self.assertEqual(self.calls[0][0], "chown")

    def test_root_with_no_sigmond_user_runs_git_as_root_in_a_root_tree(self):
        """A dev box with no sigmond account: a consistently root-owned
        tree stays consistent (gitowner ROOT_OK)."""
        self._as_root("root-ok", sigmond=False)
        smd._native_git(self.src, "fetch")
        self.assertFalse([c for c in self.calls if c[0] == "chown"])
        self.assertEqual(self._git_calls()[0][0], "git")

    def test_root_with_no_sigmond_user_refuses_a_split_tree(self):
        self._as_root("refuse", sigmond=False)
        r = smd._native_git(self.src, "fetch")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self._git_calls(), [])

    def test_clone_as_root_clones_as_sigmond(self):
        """The builder's own rule: clone unprivileged."""
        self._as_root("root-ok")
        smd._native_clone("https://example/x.git", self.src)
        self.assertEqual(self.calls[-1][:5],
                         ["sudo", "-u", "sigmond", "-H", "--"])
        self.assertIn("clone", self.calls[-1])

    def test_clone_unprivileged_runs_as_ourselves(self):
        with mock.patch.object(smd.os, "geteuid", return_value=1000):
            smd._native_clone("https://example/x.git", self.src)
        self.assertEqual(self.calls[-1][:2], ["git", "clone"])


class BuildersUseTheOwnerAwareGitTests(unittest.TestCase):
    """Each builder's fetch and checkout go through _native_git, never a
    bare `git -C <src> fetch` that runs as whoever smd is."""

    def _drive(self, builder, src, pin_const, *extra_patches):
        git_calls = []

        def fake_git(path, *args, **kw):
            git_calls.append((Path(path), args))
            # Fail the checkout so the builder stops before any compile.
            rc = 1 if args and args[0] == "checkout" else 0
            return subprocess.CompletedProcess(args, rc, "", "stop here")

        run_calls = []

        def fake_run(cmd, **kw):
            run_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        patches = [
            mock.patch.object(smd, "_native_git", side_effect=fake_git),
            mock.patch.object(smd, "_run", side_effect=fake_run),
            mock.patch.object(smd, "_git_head", return_value="0" * 40),
            mock.patch.object(smd, "_ensure_native_build_deps",
                              return_value=True),
            mock.patch.object(smd, "_apt",
                              return_value=subprocess.CompletedProcess(
                                  [], 0, "", "")),
            mock.patch.object(smd, pin_const, "f" * 40),
            *extra_patches,
        ]
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(contextlib.redirect_stdout(io.StringIO()))
            st.enter_context(contextlib.redirect_stderr(io.StringIO()))
            ok = builder()
        self.assertFalse(ok)
        verbs = [a[0] for _p, a in git_calls]
        self.assertIn("fetch", verbs)
        self.assertIn("checkout", verbs)
        bare = [c for c in run_calls
                if c[:1] == ["git"] and ("fetch" in c or "checkout" in c)]
        self.assertEqual(bare, [])

    def _existing(self, name):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / name
        src.mkdir()
        return src

    def test_ubersdr(self):
        src = self._existing("ubersdr_wsprd")
        dest = Path(tempfile.mkdtemp())
        self._drive(smd._build_ubersdr_decoders, src, "_UBERSDR_COMMIT",
                    mock.patch.object(smd, "_UBERSDR_SRC", src),
                    mock.patch.object(smd, "_DECODER_DIR", dest))

    def test_wsjtx(self):
        src = self._existing("wsjtx")
        dest = Path(tempfile.mkdtemp())
        self._drive(smd._build_wsjtx_decoders, src, "_WSJTX_COMMIT",
                    mock.patch.object(smd, "_WSJTX_SRC", src),
                    mock.patch.object(smd, "_DECODER_DIR", dest),
                    mock.patch.object(smd, "_adopt_legacy_decoders"))

    def test_onion(self):
        src = self._existing("onion")
        web = self._existing("ka9q-web")
        self._drive(lambda: smd._build_ka9q_web_with_onion(web, force=True),
                    src, "_ONION_COMMIT",
                    mock.patch.object(smd, "_ONION_SRC", src),
                    mock.patch.object(smd, "_install_radiod_deps",
                                      return_value=True))


if __name__ == "__main__":
    unittest.main()

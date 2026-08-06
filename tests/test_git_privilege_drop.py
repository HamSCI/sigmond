"""Git in a component checkout must never fall through to running as root.

Running git as root inside `/opt/git/sigmond/<name>` leaves root-owned .git
internals (FETCH_HEAD, config, HEAD, index, objects, refs) that every later
non-root operation trips over.  The damage is silent when done and only
surfaces when the sigmond/service user next touches the repo — sigmond#43.
Split worktree/.git ownership defeats the owning-uid fallback the same way
— sigmond#44.

A skipped update is recoverable; a root-owned .git is not.  So when no
unprivileged identity can be established, `_git_target_user` returns None and
the callers decline instead of proceeding.  See bin/smd `_git_target_user`,
`_git_refused`, `_git_pull_as_owner`, `_git_fetch_as_owner`.
"""
import importlib.machinery
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class _FakeStat:
    """Real stat result with st_uid overridden.

    Everything else (notably st_mode, which Path.is_dir() consults) must keep
    coming from the real filesystem entry.
    """

    def __init__(self, uid, real=None):
        self.st_uid = uid
        self._real = real

    def __getattr__(self, name):
        if self.__dict__.get("_real") is not None:
            return getattr(self._real, name)
        raise AttributeError(name)


def _stat_map(mapping):
    """Patch os.stat so specific paths report crafted uids, others pass through.

    Ownership cases can't be built for real without root, and the point of the
    test is the decision, not the filesystem.
    """
    real = os.stat

    def fake(path, *a, **kw):
        key = str(path)
        if key in mapping:
            try:
                actual = real(path, *a, **kw)
            except OSError:
                actual = None
            return _FakeStat(mapping[key], actual)
        return real(path, *a, **kw)

    return mock.patch.object(smd.os, "stat", side_effect=fake)


class GitTargetUserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "component"
        (self.repo / ".git").mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)
        self._env = os.environ.pop("SUDO_USER", None)
        self.addCleanup(
            lambda: os.environ.__setitem__("SUDO_USER", self._env)
            if self._env is not None else None)

    def test_prefers_sudo_user(self):
        os.environ["SUDO_USER"] = "alice"
        self.assertEqual(smd._git_target_user(self.repo), "alice")

    def test_falls_back_to_consistent_owner(self):
        # A fixed non-root uid, not os.getuid(): the suite may run as root,
        # and a root-owned checkout is precisely what must be refused.
        with _stat_map({str(self.repo): 1000, str(self.repo / ".git"): 1000}), \
             mock.patch.object(smd.pwd, "getpwuid",
                               return_value=mock.Mock(pw_name="sigmond")):
            self.assertEqual(smd._git_target_user(self.repo), "sigmond")

    def test_root_owned_checkout_refuses(self):
        """uid 0 gives nobody to drop to — must not silently run as root."""
        with _stat_map({str(self.repo): 0, str(self.repo / ".git"): 0}):
            self.assertIsNone(smd._git_target_user(self.repo))

    def test_root_owned_git_dir_refuses(self):
        with _stat_map({str(self.repo): 1000,
                        str(self.repo / ".git"): 0}):
            self.assertIsNone(smd._git_target_user(self.repo))

    def test_split_ownership_refuses(self):
        """sigmond#44: worktree and .git owned by different users.

        Running as the worktree owner would write into a .git owned by someone
        else, so the identity must not be guessed at.
        """
        with _stat_map({str(self.repo): 1000, str(self.repo / ".git"): 1001}):
            self.assertIsNone(smd._git_target_user(self.repo))

    def test_unknown_uid_refuses(self):
        with _stat_map({str(self.repo): 4242, str(self.repo / ".git"): 4242}):
            with mock.patch.object(smd.pwd, "getpwuid", side_effect=KeyError):
                self.assertIsNone(smd._git_target_user(self.repo))

    def test_missing_repo_refuses(self):
        self.assertIsNone(smd._git_target_user(Path("/nonexistent/component")))

    def test_git_file_uses_worktree_uid(self):
        """A linked worktree/submodule has a .git FILE, not a dir — no separate
        ownership worth comparing, so the worktree's own uid governs."""
        other = Path(self.tmp.name) / "linked"
        other.mkdir()
        (other / ".git").write_text("gitdir: /elsewhere\n")
        with _stat_map({str(other): 1000}), \
             mock.patch.object(smd.pwd, "getpwuid",
                               return_value=mock.Mock(pw_name="sigmond")):
            self.assertEqual(smd._git_target_user(other), "sigmond")

    def test_sudo_user_root_is_not_a_target(self):
        os.environ["SUDO_USER"] = "root"
        with _stat_map({str(self.repo): 0, str(self.repo / ".git"): 0}):
            self.assertIsNone(smd._git_target_user(self.repo))


class GitRefusalTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path("/opt/git/sigmond/example")

    def test_refused_result_is_a_distinct_failure(self):
        r = smd._git_refused(self.repo, "fetch")
        self.assertEqual(r.returncode, smd._GIT_REFUSED_RC)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing", r.stderr)
        self.assertIn(str(self.repo), r.stderr)

    def _assert_declines_without_running(self, fn, verb):
        with mock.patch.object(smd.os, "geteuid", return_value=0), \
             mock.patch.object(smd, "_git_target_user", return_value=None), \
             mock.patch.object(smd.subprocess, "run") as run:
            result = fn(self.repo)
        run.assert_not_called()          # the whole point: git never ran
        self.assertEqual(result.returncode, smd._GIT_REFUSED_RC)
        self.assertIn(verb, result.stderr)

    def test_pull_declines_rather_than_running_as_root(self):
        self._assert_declines_without_running(smd._git_pull_as_owner, "pull")

    def test_fetch_declines_rather_than_running_as_root(self):
        self._assert_declines_without_running(smd._git_fetch_as_owner, "fetch")

    def test_runs_as_resolved_user_when_one_exists(self):
        with mock.patch.object(smd.os, "geteuid", return_value=0), \
             mock.patch.object(smd, "_git_target_user", return_value="sigmond"), \
             mock.patch.object(smd.subprocess, "run") as run:
            smd._git_fetch_as_owner(self.repo)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:4], ["sudo", "-u", "sigmond", "-H"])

    def test_non_root_invocation_runs_git_directly(self):
        with mock.patch.object(smd.os, "geteuid", return_value=1000), \
             mock.patch.object(smd.subprocess, "run") as run:
            smd._git_fetch_as_owner(self.repo)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "git")


if __name__ == "__main__":
    unittest.main()

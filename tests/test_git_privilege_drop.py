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
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import gitowner  # noqa: E402  (after the sys.path bootstrap)


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

    return mock.patch.object(gitowner.os, "stat", side_effect=fake)


class GitTargetUserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "component"
        (self.repo / ".git").mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)
        # Restore SUDO_USER exactly — including *unsetting* it when it was not
        # set to begin with.  Leaking it poisons every later test that shells
        # out to git (sudo then fails with "unknown user").
        self._env = os.environ.pop("SUDO_USER", None)

        def _restore():
            if self._env is None:
                os.environ.pop("SUDO_USER", None)
            else:
                os.environ["SUDO_USER"] = self._env

        self.addCleanup(_restore)

    def test_prefers_sudo_user(self):
        os.environ["SUDO_USER"] = "alice"
        self.assertEqual(gitowner.target_user(self.repo), "alice")

    def test_falls_back_to_consistent_owner(self):
        # A fixed non-root uid, not os.getuid(): the suite may run as root,
        # and a root-owned checkout is precisely what must be refused.
        with _stat_map({str(self.repo): 1000, str(self.repo / ".git"): 1000}), \
             mock.patch.object(gitowner.pwd, "getpwuid",
                               return_value=mock.Mock(pw_name="sigmond")):
            self.assertEqual(gitowner.target_user(self.repo), "sigmond")

    def test_consistently_root_owned_is_root_ok(self):
        """A wholly root-owned tree may proceed as root.

        Running as root there adds no inconsistency, and the clone path needs
        it: install clones as root and the tree stays root-owned until
        _apply_canonical_perms() chowns it at the end.  Refusing would break
        fresh installs — caught by tests/test_installer.py.
        """
        with _stat_map({str(self.repo): 0, str(self.repo / ".git"): 0}):
            self.assertEqual(gitowner.resolve(self.repo),
                             (gitowner.ROOT_OK, None))
            self.assertIsNone(gitowner.target_user(self.repo))

    def test_root_owned_git_dir_refuses(self):
        """Non-root worktree with a root-owned .git is split ownership."""
        with _stat_map({str(self.repo): 1000,
                        str(self.repo / ".git"): 0}):
            self.assertEqual(gitowner.resolve(self.repo),
                             (gitowner.REFUSE, None))

    def test_split_ownership_refuses(self):
        """sigmond#44: worktree and .git owned by different users.

        Running as the worktree owner would write into a .git owned by someone
        else, so the identity must not be guessed at.
        """
        with _stat_map({str(self.repo): 1000, str(self.repo / ".git"): 1001}):
            self.assertEqual(gitowner.resolve(self.repo),
                             (gitowner.REFUSE, None))

    def test_unknown_uid_refuses(self):
        with _stat_map({str(self.repo): 4242, str(self.repo / ".git"): 4242}):
            with mock.patch.object(gitowner.pwd, "getpwuid", side_effect=KeyError):
                self.assertEqual(gitowner.resolve(self.repo),
                                 (gitowner.REFUSE, None))

    def test_missing_repo_refuses(self):
        self.assertEqual(gitowner.resolve(Path("/nonexistent/component")),
                         (gitowner.REFUSE, None))

    def test_git_file_uses_worktree_uid(self):
        """A linked worktree/submodule has a .git FILE, not a dir — no separate
        ownership worth comparing, so the worktree's own uid governs."""
        other = Path(self.tmp.name) / "linked"
        other.mkdir()
        (other / ".git").write_text("gitdir: /elsewhere\n")
        with _stat_map({str(other): 1000}), \
             mock.patch.object(gitowner.pwd, "getpwuid",
                               return_value=mock.Mock(pw_name="sigmond")):
            self.assertEqual(gitowner.target_user(other), "sigmond")

    def test_sudo_user_root_is_not_a_target(self):
        os.environ["SUDO_USER"] = "root"
        with _stat_map({str(self.repo): 0, str(self.repo / ".git"): 0}):
            self.assertEqual(gitowner.resolve(self.repo),
                             (gitowner.ROOT_OK, None))


class GitRefusalTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path("/opt/git/sigmond/example")

    def test_refused_result_is_a_distinct_failure(self):
        r = gitowner.refused(self.repo, "fetch")
        self.assertEqual(r.returncode, gitowner.REFUSED_RC)
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


class ReadOnlyClassificationTests(unittest.TestCase):
    """Reads may run as root; anything else must not.

    The split has to be conservative in one specific direction: an unlisted
    command is treated as mutating, because a wrongly-skipped read is an
    inconvenience while a wrongly-permitted write corrupts the checkout.
    Reads must stay runnable as root, though — inspection has to keep working
    on exactly those hosts whose checkouts are already root-owned.
    """

    def test_plain_reads(self):
        for args in (['rev-parse', 'HEAD'], ['ls-remote', 'origin'],
                     ['log', '-1'], ['show-ref'], ['cat-file', '-p', 'HEAD']):
            self.assertTrue(gitowner.is_read_only(args), args)

    def test_mutations(self):
        for args in (['fetch', 'origin'], ['checkout', 'main'],
                     ['pull', '--ff-only'], ['fetch', '--unshallow', 'origin'],
                     ['checkout', '-B', 'main', 'origin/main']):
            self.assertFalse(gitowner.is_read_only(args), args)

    def test_status_is_not_read_only(self):
        """`git status` refreshes the index stat cache — it writes .git/index."""
        self.assertFalse(gitowner.is_read_only(['status']))

    def test_remote_split_by_subcommand(self):
        self.assertTrue(gitowner.is_read_only(['remote', 'get-url', 'origin']))
        self.assertFalse(gitowner.is_read_only(['remote', 'set-url', 'origin', 'x']))

    def test_config_split_by_flag(self):
        self.assertTrue(gitowner.is_read_only(['config', '--get', 'a.b']))
        self.assertTrue(gitowner.is_read_only(['config', '--get-all', 'a.b']))
        self.assertFalse(gitowner.is_read_only(['config', 'a.b', 'value']))

    def test_symbolic_ref_reads_but_does_not_write(self):
        self.assertTrue(gitowner.is_read_only(['symbolic-ref', 'HEAD']))
        self.assertFalse(gitowner.is_read_only(['symbolic-ref', 'HEAD', 'refs/heads/x']))
        self.assertFalse(gitowner.is_read_only(['symbolic-ref', '-d', 'HEAD']))

    def test_empty_is_not_read_only(self):
        self.assertFalse(gitowner.is_read_only([]))


class RunGitTests(unittest.TestCase):
    """`run_git` is what sigmond.installer._git() now delegates to.

    That helper used to run *everything* as root, which is what left
    root-owned config/FETCH_HEAD/HEAD/index across component checkouts.
    """

    def setUp(self):
        self.repo = Path('/opt/git/sigmond/example')

    def test_root_mutation_without_owner_is_declined(self):
        with mock.patch.object(gitowner.os, 'geteuid', return_value=0), \
             mock.patch.object(gitowner, 'resolve',
                               return_value=(gitowner.REFUSE, None)), \
             mock.patch.object(gitowner.subprocess, 'run') as run:
            r = gitowner.run_git(self.repo, 'fetch', 'origin')
        run.assert_not_called()
        self.assertEqual(r.returncode, gitowner.REFUSED_RC)

    def test_root_mutation_with_owner_drops_privileges(self):
        with mock.patch.object(gitowner.os, 'geteuid', return_value=0), \
             mock.patch.object(gitowner, 'resolve',
                               return_value=(gitowner.DROP, 'sigmond')), \
             mock.patch.object(gitowner.subprocess, 'run') as run:
            gitowner.run_git(self.repo, 'checkout', 'main')
        self.assertEqual(run.call_args[0][0][:4], ['sudo', '-u', 'sigmond', '-H'])

    def test_root_read_runs_directly(self):
        """Reads must not be refused on a root-owned checkout."""
        with mock.patch.object(gitowner.os, 'geteuid', return_value=0), \
             mock.patch.object(gitowner, 'resolve',
                               return_value=(gitowner.REFUSE, None)), \
             mock.patch.object(gitowner.subprocess, 'run') as run:
            gitowner.run_git(self.repo, 'rev-parse', 'HEAD')
        self.assertEqual(run.call_args[0][0][0], 'git')

    def test_non_root_runs_directly(self):
        with mock.patch.object(gitowner.os, 'geteuid', return_value=1000), \
             mock.patch.object(gitowner.subprocess, 'run') as run:
            gitowner.run_git(self.repo, 'fetch', 'origin')
        self.assertEqual(run.call_args[0][0][0], 'git')

    def test_safe_directory_is_always_set(self):
        with mock.patch.object(gitowner.os, 'geteuid', return_value=1000), \
             mock.patch.object(gitowner.subprocess, 'run') as run:
            gitowner.run_git(self.repo, 'rev-parse', 'HEAD')
        self.assertIn(f'safe.directory={self.repo}', run.call_args[0][0])


class RootOkTests(unittest.TestCase):
    """A consistently root-owned tree proceeds as root, without sudo."""

    def test_root_owned_tree_runs_as_root_without_sudo(self):
        repo = Path('/opt/git/sigmond/example')
        with mock.patch.object(gitowner.os, 'geteuid', return_value=0), \
             mock.patch.object(gitowner, 'resolve',
                               return_value=(gitowner.ROOT_OK, None)), \
             mock.patch.object(gitowner.subprocess, 'run') as run:
            gitowner.run_git(repo, 'fetch', 'origin')
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], 'git')
        self.assertNotIn('sudo', cmd)

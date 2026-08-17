"""`smd update` must refresh origin/* before deciding a host is current.

`plan_update` decides there is nothing to do from `HEAD..@{u}` — the
count against the LOCALLY CACHED remote ref. Nothing refreshes that ref,
so on a host whose last fetch is old, `@{u}` still names the commit that
was current then, the count is 0, and the host reports:

    host is current — nothing to do

while genuinely being behind. Observed on B4 2026-08-17: HEAD and @{u}
both 517995a, last fetch 2026-08-15 19:10Z, real distance 10 commits.

This is the SAME defect `smd component list` already carries a fix for —
see `_git_fetch_as_owner`'s docstring ("leaving origin/* stale so
`component list` falsely reports 'up to date' when origin/main has
actually advanced") and the "Fetch by default so divergence is LIVE"
comment in `cmd_component_list`. `smd update` never got the same
treatment.

These tests use real git repositories rather than mocks: the bug lives
in what git actually reports about a stale ref, and a mocked git would
report whatever we told it to.
"""

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def _git(*args, cwd):
    return subprocess.run(['git', *args], cwd=str(cwd),
                          capture_output=True, text=True, check=True)


def _behind(repo):
    """What `cmd_update` computes to decide 'nothing to do'."""
    out = subprocess.run(
        ['git', '--no-optional-locks', '-C', str(repo),
         'rev-list', '--count', 'HEAD..@{u}'],
        capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


class StaleUpstreamRefTest(unittest.TestCase):
    """Build B4's exact situation: a checkout whose origin/* is old."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)

        self.origin = root / 'origin.git'
        _git('init', '--bare', '-b', 'main', str(self.origin), cwd=root)

        author = ['-c', 'user.email=t@example', '-c', 'user.name=t']
        seed = root / 'seed'
        _git('clone', str(self.origin), str(seed), cwd=root)
        (seed / 'a.txt').write_text('a\n')
        _git('add', 'a.txt', cwd=seed)
        _git(*author, 'commit', '-m', 'first', cwd=seed)
        _git('push', '-u', 'origin', 'main', cwd=seed)

        # The host: cloned when origin was at 'first', never fetched since.
        self.host = root / 'host'
        _git('clone', str(self.origin), str(self.host), cwd=root)

        # Upstream moves on, exactly as sigmond's main did.
        (seed / 'b.txt').write_text('b\n')
        _git('add', 'b.txt', cwd=seed)
        _git(*author, 'commit', '-m', 'second', cwd=seed)
        _git('push', 'origin', 'main', cwd=seed)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_stale_ref_really_does_report_nothing_to_do(self):
        """Reproduce the defect before fixing it.

        If this ever fails, the premise is gone and the fix below is
        solving a problem that no longer exists.
        """
        self.assertEqual(_behind(self.host), 0,
                         'expected the stale ref to hide the new commit')

    def test_refreshing_upstreams_reveals_the_real_distance(self):
        smd._refresh_upstreams([self.host], fetch=True)
        self.assertEqual(_behind(self.host), 1)

    def test_no_fetch_leaves_the_ref_alone(self):
        """--no-fetch stays available for speed, as on `component list`."""
        smd._refresh_upstreams([self.host], fetch=False)
        self.assertEqual(_behind(self.host), 0)

    def test_one_unfetchable_repo_does_not_abort_the_rest(self):
        """A checkout with no remote, or an unreachable one, must not
        stop every other component from being refreshed."""
        broken = Path(self._tmp.name) / 'broken'
        broken.mkdir()
        _git('init', '-b', 'main', '.', cwd=broken)

        smd._refresh_upstreams([broken, self.host], fetch=True)
        self.assertEqual(_behind(self.host), 1)


class UpdateExposesTheOptOutTest(unittest.TestCase):
    """The parser is built inside `main()`, so exercise the real CLI.

    Extracting a parser factory purely to test one flag would be a large
    refactor of an unrelated function; `--help` is the same surface an
    operator sees.
    """

    def _help(self):
        env = dict(os.environ, PYTHONPATH=str(REPO / 'lib'),
                   SIGMOND_NO_VENV_REEXEC='1')
        return subprocess.run([sys.executable, str(REPO / 'bin' / 'smd'),
                               'update', '--help'],
                              capture_output=True, text=True, env=env).stdout

    def test_update_offers_no_fetch(self):
        self.assertIn('--no-fetch', self._help())

    def test_the_help_says_fetching_is_the_default(self):
        """An operator must be able to tell which way the default runs."""
        flat = ' '.join(self._help().lower().split())   # argparse wraps
        self.assertRegex(flat, r'fetch.*default|default.*fetch')


if __name__ == '__main__':
    unittest.main()

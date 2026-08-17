"""smd runs as root, so ANY git it spawns can leave root-owned damage.

`git status` opportunistically refreshes and REWRITES `.git/index`. Run
as root that leaves the index root-owned — recreating exactly the damage
`smd doctor --fix` exists to repair. The codebase already fought this in
three separate places, each hand-adding `--no-optional-locks` with the
same comment (doctor.py `git_state`, provenance.py `_git_head`).

It was still losing, because the offending git is not one smd writes.
`smd doctor --fix` repaired ownership and then, seconds later, left
`.git/index` root-owned again on 5 components (B4) and 2 (DASI002).
Instrumenting with a PATH shim caught three bare invocations — no `-C`,
no `--no-optional-locks`, running as root:

    root | status --porcelain
    root | rev-parse HEAD
    root | rev-parse --abbrev-ref HEAD

None of them are in sigmond's source. They come from INSIDE the venv
probe: it imports each component's package as root, and setuptools_scm
style version derivation on an editable install shells out to precisely
those three, with cwd in the project root.

Per-call flags can never cover that — smd does not author the call. The
environment does: GIT_OPTIONAL_LOCKS=0 is the env form of
`--no-optional-locks`, and every descendant inherits it. Verified A/B on
DASI002 2026-08-17: with it the index stayed `sigmond`, without it the
same seeded run left it `root`.

It suppresses only OPTIONAL locks, so real work is unaffected: `git
pull`'s index write is required, not opportunistic.
"""

import importlib.machinery
import importlib.util
import os
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


class HardenGitEnvTest(unittest.TestCase):

    def test_it_sets_the_flag_when_absent(self):
        env = {}
        smd._harden_git_env(env)
        self.assertEqual(env['GIT_OPTIONAL_LOCKS'], '0')

    def test_an_operator_setting_is_respected(self):
        """Someone who deliberately set this means it. Overriding an
        explicit choice is how a diagnostic earns distrust."""
        env = {'GIT_OPTIONAL_LOCKS': '1'}
        smd._harden_git_env(env)
        self.assertEqual(env['GIT_OPTIONAL_LOCKS'], '1')

    def test_it_leaves_the_rest_of_the_environment_alone(self):
        env = {'PATH': '/usr/bin', 'HOME': '/root'}
        smd._harden_git_env(env)
        self.assertEqual(env['PATH'], '/usr/bin')
        self.assertEqual(env['HOME'], '/root')


class AppliedAtStartupTest(unittest.TestCase):

    def test_importing_smd_hardens_the_real_environment(self):
        """The whole point is that CHILDREN inherit it — a subprocess
        smd never wrote the argv for is the one that caused this."""
        self.assertEqual(os.environ.get('GIT_OPTIONAL_LOCKS'), '0')


class ExplicitFlagStillCarriedTest(unittest.TestCase):
    """Belt and braces: the env var covers children smd does not author,
    but smd's OWN git calls keep their explicit flag, so a caller that
    scrubs the environment is still safe."""

    def test_repo_is_dirty_passes_the_flag(self):
        import inspect
        src = inspect.getsource(smd._repo_is_dirty)
        self.assertIn('--no-optional-locks', src)


if __name__ == '__main__':
    unittest.main()

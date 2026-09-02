"""A terminal convenience must never abort the install.

⛔ Why this check exists.  On 2026-09-02 the first golden-VM build since
2026-08-27 died in stage 1, and every image build would have died the same way.
`55d039c` extended the tmux/toprc seeding to the `sigmond` operator account as
well as the invoking user.  On an appliance `sigmond` names a real operator with
a real home.  On a build VM — and anywhere sigmond installs its own service
account — `sigmond` is a system account whose home IS the source checkout:

    sigmond:x:999:989::/opt/git/sigmond:/usr/sbin/nologin
    drwxrwsr-x 6 sigmond sigmond /opt/git/sigmond

The loop guarded that the home exists and is a directory, never that the
installer may write to it.  So `>> /opt/git/sigmond/.tmux.conf` returned EACCES,
the redirection failed, `set -euo pipefail` killed install.sh, and the driver
polled thirty minutes for a `BOOTSTRAP DONE` that could never arrive.  The
build reported a timeout, which says nothing about a tmux config.

Two things went wrong and this holds both.  Seeding a scroll-wheel setting is
the least important thing install.sh does, so it must not be able to stop the
install — a convenience that cannot be delivered gets skipped and said aloud.
And an account with no login shell has no terminal to make convenient, so it is
not an operator and should never have been a target.
"""
import os
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.sh"

START = "# ─── operator terminal conveniences"
END = "# ─── avahi-browse"

HARNESS = r"""
set -euo pipefail
ok()   { echo "OK: $*"; }
warn() { echo "WARN: $*"; }
say()  { echo "SAY: $*"; }
INVOKER="alice"
REPO_DIR="__REPO__"
# The sigmond account exists, the way it does on any host sigmond has installed.
id() { case "$*" in *sigmond*) return 0 ;; *) command id "$@" ;; esac; }
getent() {
    case "$2" in
        alice)   echo "alice:x:1000:1000::__ALICE__:/bin/bash" ;;
        sigmond) echo "sigmond:x:999:989::__SIGHOME__:__SIGSHELL__" ;;
    esac
}
"""


def _block():
    lines = INSTALL.read_text().splitlines(keepends=True)
    starts = [i for i, l in enumerate(lines) if l.startswith(START)]
    ends = [i for i, l in enumerate(lines) if l.startswith(END)]
    assert starts and ends, "section markers moved — re-anchor this test"
    return "".join(lines[starts[0]:ends[0]])


class Conveniences(unittest.TestCase):

    def _run(self, alice, sighome, sigshell="/bin/bash"):
        script = (HARNESS
                  .replace("__REPO__", str(REPO))
                  .replace("__ALICE__", str(alice))
                  .replace("__SIGHOME__", str(sighome))
                  .replace("__SIGSHELL__", sigshell)) + _block()
        return subprocess.run(["bash", "-c", script],
                              capture_output=True, text=True)

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.alice = root / "alice"
        self.alice.mkdir()
        self.sigmond = root / "opt-git-sigmond"
        self.sigmond.mkdir()
        self.addCleanup(self.tmp.cleanup)

    def test_an_unwritable_operator_home_does_not_kill_the_install(self):
        """The build-VM case: sigmond's home is the checkout, mode rwxrwsr-x,
        and the installer runs as someone else."""
        if os.geteuid() == 0:
            self.skipTest("root writes anywhere; the EACCES cannot be staged")
        self.sigmond.chmod(0o555)
        self.addCleanup(self.sigmond.chmod, 0o755)
        r = self._run(self.alice, self.sigmond)
        self.assertEqual(0, r.returncode,
                         f"install aborted:\n{r.stdout}\n{r.stderr}")

    def test_the_reachable_home_still_gets_seeded(self):
        """Skipping one account must not cost the other its conveniences."""
        if os.geteuid() == 0:
            self.skipTest("root writes anywhere; the EACCES cannot be staged")
        self.sigmond.chmod(0o555)
        self.addCleanup(self.sigmond.chmod, 0o755)
        self._run(self.alice, self.sigmond)
        self.assertIn("set -g mouse on",
                      (self.alice / ".tmux.conf").read_text())

    def test_a_skipped_account_says_so(self):
        """Silence would leave an operator wondering why their scroll wheel
        does nothing.  Name it."""
        if os.geteuid() == 0:
            self.skipTest("root writes anywhere; the EACCES cannot be staged")
        self.sigmond.chmod(0o555)
        self.addCleanup(self.sigmond.chmod, 0o755)
        r = self._run(self.alice, self.sigmond)
        self.assertIn("sigmond", r.stdout + r.stderr)

    def test_an_account_with_no_login_shell_is_not_an_operator(self):
        """A nologin service account has no terminal to make convenient, so
        the installer must not write into its home even when it can."""
        r = self._run(self.alice, self.sigmond, sigshell="/usr/sbin/nologin")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertFalse((self.sigmond / ".tmux.conf").exists(),
                         "seeded a config for an account that cannot log in")

    def test_a_real_operator_account_still_gets_both(self):
        """Guard the guard: the fix must not disable what 55d039c added."""
        r = self._run(self.alice, self.sigmond)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("set -g mouse on",
                      (self.sigmond / ".tmux.conf").read_text())


if __name__ == "__main__":
    unittest.main()

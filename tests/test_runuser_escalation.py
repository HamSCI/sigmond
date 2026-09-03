"""`smd update --apply` must run as the operator who is meant to run it.

⛔ AC0G-ND, 2026-09-03.  `smd update --apply` died outright:

    FileNotFoundError: [Errno 2] No such file or directory: 'runuser'

Two things the bare name got wrong, and both had to be true for it to work:

  * `runuser` lives in **/usr/sbin**, which is not on an unprivileged
    operator's PATH.  On ND, `which runuser` returns nothing for the `hamsci`
    user while `/usr/sbin/runuser` sits right there.
  * it needs root — and `smd` deliberately refuses to run under sudo, so the
    escalation has to happen per-command, the same `sudo=True` pattern `_run`
    already uses everywhere else in this file.

Three call sites carried it: the `cmd_update` pull, the pre-plan fetch, and the
restore checkout.  The documented fleet-update path was unusable as the
operator, on any host whose PATH omits /usr/sbin — which is the default.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_runuser_under_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_runuser_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

GIT = ["git", "-C", "/opt/git/sigmond/sigmond", "pull", "--ff-only"]


class RunuserEscalationTest(unittest.TestCase):

    def test_an_absolute_path_is_used_when_which_finds_nothing(self):
        """The exact ND condition: /usr/sbin absent from PATH."""
        with patch.object(smd.shutil, "which", return_value=None), \
             patch.object(smd.os, "geteuid", return_value=0):
            cmd = smd._runuser("sigmond", GIT)
        self.assertEqual(cmd[0], "/usr/sbin/runuser")
        self.assertNotIn("runuser", [cmd[0].rsplit("/", 1)[0]])

    def test_a_resolved_path_is_preferred_when_available(self):
        with patch.object(smd.shutil, "which", return_value="/sbin/runuser"), \
             patch.object(smd.os, "geteuid", return_value=0):
            cmd = smd._runuser("sigmond", GIT)
        self.assertEqual(cmd[0], "/sbin/runuser")

    def test_sudo_is_prepended_when_not_root(self):
        with patch.object(smd.shutil, "which", return_value="/usr/sbin/runuser"), \
             patch.object(smd.os, "geteuid", return_value=1000):
            cmd = smd._runuser("sigmond", GIT)
        self.assertEqual(cmd[:2], ["sudo", "-n"])
        self.assertEqual(cmd[2], "/usr/sbin/runuser")

    def test_sudo_is_NOT_prepended_when_already_root(self):
        with patch.object(smd.shutil, "which", return_value="/usr/sbin/runuser"), \
             patch.object(smd.os, "geteuid", return_value=0):
            cmd = smd._runuser("sigmond", GIT)
        self.assertNotIn("sudo", cmd)

    def test_the_target_user_and_argv_survive_intact(self):
        with patch.object(smd.shutil, "which", return_value="/usr/sbin/runuser"), \
             patch.object(smd.os, "geteuid", return_value=0):
            cmd = smd._runuser("wsprrec", GIT)
        self.assertEqual(cmd, ["/usr/sbin/runuser", "-u", "wsprrec", "--"] + GIT)

    def test_an_empty_user_falls_back_to_root(self):
        with patch.object(smd.shutil, "which", return_value="/usr/sbin/runuser"), \
             patch.object(smd.os, "geteuid", return_value=0):
            self.assertIn("root", smd._runuser("", GIT))

    def test_the_install_step_escalates_too(self):
        """Every consumer's install.sh refuses to run unprivileged.

        With the pulls fixed, B4's update then reported "Run as root (sudo)"
        for gpsdo-monitor, hf-timestd, hs-uploader and wspr-recorder — leaving
        checkouts updated and venvs stale, which is a worse state than not
        having run at all.
        """
        with patch.object(smd.os, "geteuid", return_value=1000):
            self.assertEqual(
                smd._as_root(["bash", "/opt/git/sigmond/x/scripts/install.sh"])[:2],
                ["sudo", "-n"])
        with patch.object(smd.os, "geteuid", return_value=0):
            self.assertEqual(smd._as_root(["bash", "x"]), ["bash", "x"])

    def test_no_bare_bash_install_sh_invocation_remains(self):
        text = (REPO / "bin" / "smd").read_text()
        self.assertNotIn("subprocess.run(['bash', str(sh)]", text,
                         "install.sh needs root — route it through _as_root()")

    def test_no_call_site_still_uses_the_bare_name(self):
        """The bug was three identical call sites, so guard the file itself.

        The one remaining `['runuser', ...]` goes through `_run(sudo=True)`,
        where sudo's secure_path resolves it — that one is fine and stays.
        """
        text = (REPO / "bin" / "smd").read_text()
        bare = text.count("['runuser',")
        self.assertEqual(bare, 1, (
            "a bare 'runuser' outside the _run(sudo=True) path will fail on any "
            "host whose PATH omits /usr/sbin — use _runuser()"))


if __name__ == "__main__":
    unittest.main()

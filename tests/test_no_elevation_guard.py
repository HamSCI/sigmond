"""The suite never elevates (tests/conftest.py; Task 20 incident).

Unless SIGMOND_TEST_ALLOW_SUDO=1, `sudo` on PATH refuses and exits 1 for
this process and its children, and an in-process exec of sudo raises."""
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


@unittest.skipIf(conftest.ALLOW_SUDO, "the guard is off by request")
class NoElevationTests(unittest.TestCase):

    def test_sudo_on_path_is_the_refusing_stand_in(self):
        found = shutil.which("sudo")
        self.assertIsNotNone(found)
        self.assertIn("sigmond-test-nosudo-", found)

    def test_a_child_that_runs_sudo_is_refused(self):
        r = subprocess.run(["sh", "-c", "sudo -n true"], capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("sudo refused under pytest", r.stderr)

    def test_an_in_process_exec_of_sudo_raises(self):
        with self.assertRaises(AssertionError) as cm:
            os.execvp("sudo", ["sudo", "-n", "true"])
        self.assertIn("never elevates", str(cm.exception))

    def test_need_root_raises_instead_of_reexecuting(self):
        ns = {"__name__": "smd_under_test", "__file__": str(REPO / "bin" / "smd")}
        exec(compile((REPO / "bin" / "smd").read_text(), str(REPO / "bin" / "smd"),
                     "exec"), ns)
        with mock.patch("os.geteuid", return_value=1000):
            with self.assertRaises(AssertionError):
                ns["_need_root"]("sink fill")

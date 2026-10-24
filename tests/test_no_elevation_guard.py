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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import conftest  # noqa: E402  (the module pytest loaded, not a copy)

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
            with self.assertRaises((AssertionError, SystemExit)):
                ns["_need_root"]("sink fill")

    # ---- Task 27 HIGH: hardening ----------------------------------------

    def test_conftest_loads_once(self):
        """Each load prepends its own stand-in: one entry means one load
        (the `from tests.conftest import` form, not `from conftest`)."""
        stand_ins = [p for p in os.environ["PATH"].split(os.pathsep)
                     if "sigmond-test-nosudo-" in p]
        self.assertEqual(len(stand_ins), 1, stand_ins)
        self.assertFalse("conftest" in sys.modules,
                         "tests/conftest.py loaded a second time as `conftest`")

    def test_the_session_exports_no_elevate(self):
        self.assertEqual(os.environ.get("SIGMOND_TEST_NO_ELEVATE"), "1")

    def test_need_root_refuses_under_no_elevate_before_any_exec(self):
        ns = {"__name__": "smd_under_test", "__file__": str(REPO / "bin" / "smd")}
        exec(compile((REPO / "bin" / "smd").read_text(), str(REPO / "bin" / "smd"),
                     "exec"), ns)
        execs = []
        with mock.patch("os.geteuid", return_value=1000), \
                mock.patch("os.execvp", side_effect=lambda *a: execs.append(a)), \
                mock.patch("shutil.which", return_value="/usr/bin/sudo"):
            with self.assertRaises(SystemExit) as cm:
                ns["_need_root"]("sink fill")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(execs, [])

    def test_an_absolute_sudo_through_subprocess_is_refused(self):
        for argv, kw in ((["/usr/bin/sudo", "-n", "true"], {}),
                         ("/usr/bin/sudo -n true", {"shell": True}),
                         (["true"], {"executable": "/usr/bin/sudo"}),
                         (["/nonexistent/doas", "true"], {}),
                         (["/nonexistent/pkexec", "true"], {}),
                         (["/nonexistent/run0", "true"], {})):
            with self.subTest(argv=argv, kw=kw):
                with self.assertRaises(AssertionError) as cm:
                    subprocess.run(argv, capture_output=True, timeout=30, **kw)
                self.assertIn("never elevates", str(cm.exception))
        with self.assertRaises(AssertionError):
            subprocess.Popen(["/usr/bin/sudo", "-n", "true"])

    def test_spawn_and_system_refuse_sudo(self):
        calls = (
            lambda: os.system("/usr/bin/sudo -n true"),
            lambda: os.posix_spawn("/usr/bin/sudo", ["sudo", "-n", "true"],
                                   dict(os.environ)),
            lambda: os.posix_spawnp("sudo", ["sudo", "-n", "true"],
                                    dict(os.environ)),
            lambda: os.spawnv(os.P_WAIT, "/usr/bin/sudo",
                              ["sudo", "-n", "true"]),
            lambda: os.spawnlp(os.P_WAIT, "sudo", "sudo", "-n", "true"),
        )
        for i, call in enumerate(calls):
            with self.subTest(call=i):
                with self.assertRaises(AssertionError):
                    call()

    def test_a_child_with_its_own_env_still_finds_the_stand_in(self):
        r = subprocess.run([sys.executable, "-c",
                            "import os; print(os.environ.get('PATH', '')); "
                            "print(os.environ.get('SIGMOND_TEST_NO_ELEVATE'))"],
                           env={}, capture_output=True, text=True, timeout=30)
        path, flag = r.stdout.splitlines()
        self.assertTrue(path.split(os.pathsep)[0].find("sigmond-test-nosudo-")
                        >= 0, path)
        self.assertEqual(flag, "1")
        # positive control: the caller's env carries the stand-in already
        self.assertIn("sigmond-test-nosudo-", os.environ["PATH"])

    def test_bin_smd_with_a_stripped_env_refuses_to_elevate(self):
        """A child loads bin/smd with env={} and asks _need_root.  Its
        os.execvp only records, so even a broken guard elevates nothing."""
        child = (
            "import os, sys\n"
            "def rec(f, a):\n"
            "    print('EXEC', f); sys.stdout.flush(); os._exit(3)\n"
            "os.execvp = rec\n"
            f"ns = {{'__name__': 'smd_child', '__file__': {str(REPO / 'bin' / 'smd')!r}}}\n"
            f"exec(compile(open({str(REPO / 'bin' / 'smd')!r}).read(), "
            f"{str(REPO / 'bin' / 'smd')!r}, 'exec'), ns)\n"
            "sys.exit(9 if ns['_need_root']('sink fill') else 0)\n")
        r = subprocess.run([sys.executable, "-c", child],
                           env={"SIGMOND_NO_VENV_REEXEC": "1"},
                           capture_output=True, text=True, timeout=60)
        self.assertNotIn("EXEC", r.stdout)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("SIGMOND_TEST_NO_ELEVATE", r.stderr)

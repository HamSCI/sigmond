"""``uploader_manifest.hs_uploader_features`` (spec §7.2, tests 21 and 23).

The probe asks the service's own interpreter for
``hs-uploader capabilities --features``.  A v3.73-v3.76 hs-uploader exits 2
on the flag, which reads as the empty set.  A failure or timeout reads as
``None``.
"""
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond import uploader_manifest as um


class FeaturesProbeTests(unittest.TestCase):

    def _fake_python(self, body: str) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "python"
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return p

    def test_the_three_words_give_the_set(self):
        py = self._fake_python(
            "printf 'pipelines.d\\nsend-log-v3\\nswitch-files\\n'")
        self.assertEqual(um.hs_uploader_features(py),
                         frozenset({"pipelines.d", "send-log-v3",
                                    "switch-files"}))

    def test_an_unknown_word_stays_in_the_set(self):
        py = self._fake_python("printf 'pipelines.d\\nfuture-word\\n\\n'")
        self.assertEqual(um.hs_uploader_features(py),
                         frozenset({"pipelines.d", "future-word"}))

    def test_the_probe_passes_the_flag(self):
        with mock.patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            um.hs_uploader_features(Path("/x/python"))
        self.assertEqual(run.call_args[0][0],
                         ["/x/python", "-P", "-m", "hs_uploader.cli",
                          "capabilities", "--features"])

    def test_old_uploader_unrecognized_arguments_is_empty(self):
        py = self._fake_python(
            "echo 'cli.py: error: unrecognized arguments: --features' >&2; "
            "exit 2")
        self.assertEqual(um.hs_uploader_features(py), frozenset())

    def test_old_uploader_invalid_choice_is_empty(self):
        py = self._fake_python(
            "echo \"cli.py: error: argument cmd: invalid choice: "
            "'capabilities' (choose from 'status', 'serve')\" >&2; exit 2")
        self.assertEqual(um.hs_uploader_features(py), frozenset())

    def test_exit_two_for_another_reason_is_a_failure(self):
        py = self._fake_python("echo 'something else' >&2; exit 2")
        self.assertIsNone(um.hs_uploader_features(py))

    def test_exit_one_is_a_failure(self):
        py = self._fake_python('echo "No module named hs_uploader" >&2; exit 1')
        self.assertIsNone(um.hs_uploader_features(py))

    def test_timeout_is_a_failure(self):
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired("python", 20)):
            self.assertIsNone(um.hs_uploader_features(Path("/x")))

    def test_missing_interpreter_is_a_failure(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        missing = Path(td.name) / "no-such-python"
        self.assertIsNone(um.hs_uploader_features(missing))


class PlantedModuleTests(unittest.TestCase):
    """Ruling C23: the probes run as root, so a hs_uploader package in the
    caller's CWD must not run."""

    def test_neither_probe_runs_a_module_planted_in_the_cwd(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        planted = Path(td.name) / "hs_uploader"
        planted.mkdir()
        mark = Path(td.name) / "RAN"
        (planted / "__init__.py").write_text("")
        (planted / "cli.py").write_text(
            f"open({str(mark)!r}, 'w').write('x')\nprint('PLANTED')\n")
        old = os.getcwd()
        os.chdir(td.name)
        self.addCleanup(os.chdir, old)
        # The real interpreter, with no hs_uploader of its own installed.
        um.hs_uploader_features(Path(sys.executable))
        um.hs_uploader_store_version(Path(sys.executable))
        um.run_hs_uploader(["sink", "status", "--json"],
                           python=Path(sys.executable))
        self.assertFalse(mark.exists(), "the planted cli ran")
        # Positive control: the same interpreter, run plainly from here,
        # does load the planted package.
        subprocess.run([sys.executable, "-m", "hs_uploader.cli"],
                       capture_output=True, timeout=20)
        self.assertTrue(mark.exists(), "the harness cannot see a planted run")

    def test_the_discard_probe_runs_no_module_planted_in_the_cwd(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        pkg = Path(td.name) / "hs_uploader" / "transports"
        pkg.mkdir(parents=True)
        mark = Path(td.name) / "RAN"
        (pkg.parent / "__init__.py").write_text("")
        (pkg / "__init__.py").write_text("")
        (pkg / "discard.py").write_text(
            f"open({str(mark)!r}, 'w').write('x')\n")
        old = os.getcwd()
        os.chdir(td.name)
        self.addCleanup(os.chdir, old)
        self.assertFalse(um.hs_uploader_supports_discard(Path(sys.executable)))
        self.assertFalse(mark.exists(), "the planted discard module ran")
        # Positive control: a plain `-c` import from here loads it.
        subprocess.run([sys.executable, "-c",
                        "import hs_uploader.transports.discard"],
                       capture_output=True, timeout=20)
        self.assertTrue(mark.exists(), "the harness cannot see a planted run")


class RealCheckoutTests(unittest.TestCase):
    def test_the_real_hs_uploader_prints_its_words(self):
        src = Path(__file__).resolve().parent.parent.parent / "hs-uploader" / "src"
        if not (src / "hs_uploader" / "cli.py").is_file():
            raise unittest.SkipTest(f"no hs-uploader at {src}")
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        py = Path(td.name) / "python"
        py.write_text(f'#!/bin/sh\nPYTHONPATH="{src}" exec "{sys.executable}" "$@"\n')
        py.chmod(py.stat().st_mode | stat.S_IXUSR)
        self.assertEqual(um.hs_uploader_features(py),
                         frozenset({"gate", "pipelines.d", "send-log-v3",
                                    "switch-files"}))

    def test_the_real_hs_uploader_through_the_runner(self):
        src = Path(__file__).resolve().parent.parent.parent / "hs-uploader" / "src"
        if not (src / "hs_uploader" / "cli.py").is_file():
            raise unittest.SkipTest(f"no hs-uploader at {src}")
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        py = Path(td.name) / "python"
        py.write_text(f'#!/bin/sh\nPYTHONPATH="{src}" exec "{sys.executable}" "$@"\n')
        py.chmod(py.stat().st_mode | stat.S_IXUSR)
        r = um.run_hs_uploader(["capabilities"], python=py)
        self.assertEqual((r.returncode, r.stdout), (0, "3\n"))


class RunnerTests(unittest.TestCase):
    """Ruling C23 and spec §9.1: one runner for every hs-uploader call.
    The daemon's interpreter, ``-P``, ``cwd="/"``, a timeout, and stdin
    only when given."""

    def _fake_python(self, body: str) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "python"
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return p

    def test_argv_and_cwd_as_the_child_sees_them(self):
        # A real child reports its own argv and working directory.
        py = self._fake_python('printf "%s\\n" "$@"; echo "cwd=$(pwd)"')
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        old = os.getcwd()
        os.chdir(td.name)
        self.addCleanup(os.chdir, old)
        r = um.run_hs_uploader(["sink", "status", "--json"], python=py)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.splitlines(),
                         ["-P", "-m", "hs_uploader.cli", "sink", "status",
                          "--json", "cwd=/"])

    def test_the_default_interpreter_is_the_daemons(self):
        with mock.patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            um.run_hs_uploader(["capabilities"])
        self.assertEqual(run.call_args[0][0][0], str(um.HS_UPLOADER_PYTHON))
        self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_stdin_reaches_the_child(self):
        py = self._fake_python("cat")
        r = um.run_hs_uploader(["sink", "set"], stdin='{"a": 1}\n',
                               python=py)
        self.assertEqual(r.stdout, '{"a": 1}\n')

    def test_no_stdin_means_an_empty_one_never_the_callers(self):
        py = self._fake_python("cat; echo end")
        r = um.run_hs_uploader(["x"], python=py, timeout=5)
        self.assertEqual(r.stdout, "end\n")
        with mock.patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            um.run_hs_uploader(["x"])
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_the_timeout_holds(self):
        py = self._fake_python("sleep 10")
        t0 = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            um.run_hs_uploader(["x"], python=py, timeout=0.5)
        self.assertLess(time.monotonic() - t0, 5)

    def test_both_probes_go_through_the_runner(self):
        with mock.patch.object(um, "run_hs_uploader") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "3\n", "")
            um.hs_uploader_store_version(Path("/x/py"))
            um.hs_uploader_features(Path("/x/py"))
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [["capabilities"], ["capabilities", "--features"]])
        self.assertEqual([c.kwargs["python"] for c in run.call_args_list],
                         [Path("/x/py")] * 2)

    def test_a_timeout_in_the_runner_reads_as_none(self):
        with mock.patch.object(um, "run_hs_uploader",
                               side_effect=subprocess.TimeoutExpired("p", 20)):
            self.assertIsNone(um.hs_uploader_features(Path("/x")))
            self.assertIsNone(um.hs_uploader_store_version(Path("/x")))


if __name__ == "__main__":
    unittest.main()

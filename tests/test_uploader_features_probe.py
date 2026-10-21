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
        self.assertFalse(mark.exists(), "the planted cli ran")


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
                         frozenset({"pipelines.d", "send-log-v3",
                                    "switch-files"}))


if __name__ == "__main__":
    unittest.main()

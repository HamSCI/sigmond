"""`smd admin instance canonical` (tasks/spec-reporter-id-v372.md §6): the
one way a shell script turns a reporter id into a file name or unit name."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _canonical(*argv):
    env = dict(os.environ, PYTHONPATH=str(REPO / "lib"), SIGMOND_NO_VENV_REEXEC="1")
    return subprocess.run(
        [sys.executable, str(REPO / "bin" / "smd"), "admin", "instance",
         "canonical", *argv],
        capture_output=True, text=True, env=env, timeout=60)


class CanonicalTests(unittest.TestCase):

    def test_storage_form_from_every_spelling(self):
        for text in ("AC0G/B4", "AC0G=B4", "AC0G\\x3dB4", "ac0g/b4"):
            with self.subTest(text=text):
                r = _canonical(text)
                self.assertEqual((r.returncode, r.stdout), (0, "AC0G=B4\n"), r.stderr)

    def test_systemd_form(self):
        r = _canonical("--systemd", "AC0G/B4")
        self.assertEqual((r.returncode, r.stdout), (0, "AC0G\\x3dB4\n"), r.stderr)

    def test_hyphen_stays_a_hyphen(self):
        # systemd-escape(1) would print W1ABC\x2d5.
        r = _canonical("--systemd", "W1ABC-5")
        self.assertEqual((r.returncode, r.stdout), (0, "W1ABC-5\n"), r.stderr)

    def test_invalid_id_exits_1_with_nothing_on_stdout(self):
        for text in ("AC0G/", "AC0G\\x5cx3dB4", ""):
            with self.subTest(text=text):
                r = _canonical(text)
                self.assertEqual((r.returncode, r.stdout), (1, ""))
                self.assertIn("reporter ID", r.stderr)


if __name__ == "__main__":
    unittest.main()

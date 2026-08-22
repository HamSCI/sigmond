"""sigmond#47 — scripts/venv-atomic.sh: build the venv BESIDE the live one,
verify it imports, then swap.  A failed build must leave the live venv
untouched (ENOSPC/offline used to leave an EMPTY venv)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HELPER = REPO / "scripts" / "venv-atomic.sh"
UV = shutil.which("uv")


def _run(venv_dir: Path, *, pip_args: list, verify: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, UV=UV or "", PYTHON3=sys.executable, SUDO="",
               UV_PYTHON_INSTALL_DIR=str(venv_dir.parent / "uvpy"))
    cmd = ["bash", "-c",
           f'set -euo pipefail; source "{HELPER}"; '
           f'venv_atomic_install "{venv_dir}" "{verify}" "$@"', "_", *pip_args]
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)


@unittest.skipUnless(UV, "uv not available — the helper's fast path needs it")
class VenvAtomicTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.venv = Path(self._tmp.name) / "venv"

    def tearDown(self):
        self._tmp.cleanup()

    def test_fresh_install_creates_a_working_venv(self):
        r = _run(self.venv, pip_args=["--no-deps", str(REPO)], verify="sigmond")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.venv / "bin" / "python").exists())
        chk = subprocess.run([str(self.venv / "bin" / "python"), "-c", "import sigmond"],
                             capture_output=True, text=True)
        self.assertEqual(chk.returncode, 0, chk.stderr)
        self.assertFalse((self.venv.parent / "venv.new").exists())

    def test_failed_build_leaves_the_live_venv_untouched(self):
        ok = _run(self.venv, pip_args=["--no-deps", str(REPO)], verify="sigmond")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        marker = self.venv / "LIVE-MARKER"; marker.write_text("live\n")
        bad = _run(self.venv, pip_args=["--no-deps", "this-package-does-not-exist-zzz==99"],
                   verify="sigmond")
        self.assertNotEqual(bad.returncode, 0)
        self.assertTrue(marker.exists(), "live venv was replaced/wiped by a FAILED build")
        chk = subprocess.run([str(self.venv / "bin" / "python"), "-c", "import sigmond"],
                             capture_output=True, text=True)
        self.assertEqual(chk.returncode, 0, "live venv no longer imports after a failed build")
        self.assertIn("live venv untouched", (bad.stdout + bad.stderr).lower())

    def test_successful_rebuild_swaps_and_keeps_prev(self):
        _run(self.venv, pip_args=["--no-deps", str(REPO)], verify="sigmond")
        marker = self.venv / "LIVE-MARKER"; marker.write_text("old\n")
        r = _run(self.venv, pip_args=["--no-deps", str(REPO)], verify="sigmond")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(marker.exists())                       # new venv is live
        self.assertTrue((self.venv.parent / "venv.prev" / "LIVE-MARKER").exists())  # old kept once

    def test_failed_import_check_refuses_the_swap(self):
        _run(self.venv, pip_args=["--no-deps", str(REPO)], verify="sigmond")
        marker = self.venv / "LIVE-MARKER"; marker.write_text("live\n")
        r = _run(self.venv, pip_args=["--no-deps", str(REPO)], verify="module_that_is_not_there_zzz")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(marker.exists())

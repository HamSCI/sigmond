"""Every archive verb that needs root raises smd itself (smd refuses to run
under sudo): eject, test and health-refresh too, not only add and remove."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.geteuid() == 0, reason="as root nothing elevates")
@pytest.mark.parametrize("argv", [["eject"], ["test", "--long"], ["health-refresh"]])
def test_root_verbs_elevate(argv):
    env = dict(os.environ, SIGMOND_TEST_NO_ELEVATE="1", SIGMOND_NO_VENV_REEXEC="1")
    r = subprocess.run([sys.executable, str(REPO / "bin" / "smd"), "archive", *argv],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 1
    assert "refusing to elevate" in r.stderr

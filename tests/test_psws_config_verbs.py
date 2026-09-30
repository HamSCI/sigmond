"""Every PSWS recorder the login banner names must be a real `smd config` verb.

The banner (psws.py) prints `smd config <recorder> edit` for each entry in
psws.RECORDERS.  bin/smd once registered only hf-timestd and mag-recorder, so
on AC0G-B4 (2026-09-30) the banner told the operator to run
`smd config hamsci-physics edit` and smd answered "invalid choice".
"""
import os
import subprocess
import sys

import pytest

from sigmond.psws import RECORDERS


@pytest.mark.parametrize("recorder", sorted(RECORDERS))
def test_banner_advice_parses(recorder):
    env = dict(os.environ, PYTHONPATH="lib", SIGMOND_NO_VENV_REEXEC="1")
    r = subprocess.run([sys.executable, "bin/smd", "config", recorder, "--help"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    assert "edit" in r.stdout

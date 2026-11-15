"""Run the bash tests of bin/sigmond-site-timing under pytest.

sigmond-site-timing moved here from sigmond-appliance (2026-11-15), and its
four tests came with it.  They stay bash: each one cuts a step out of the
real script and runs it against stubs.  This wrapper makes the normal
`.venv/bin/pytest` run them, so a change to the script cannot pass the
suite while breaking its own tests.

One by hand:  bash tests/test-site-timing-t6.sh
"""
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SCRIPTS = sorted(TESTS.glob('test-site-timing-*.sh'))


def test_all_four_are_here():
    assert [p.name for p in SCRIPTS] == [
        'test-site-timing-meteor.sh', 'test-site-timing-metrology.sh',
        'test-site-timing-t4.sh', 'test-site-timing-t6.sh']


@pytest.mark.skipif(shutil.which('bash') is None, reason='needs bash')
@pytest.mark.parametrize('script', SCRIPTS, ids=lambda p: p.stem)
def test_site_timing_script(script):
    r = subprocess.run(['bash', str(script)], capture_output=True, text=True,
                       timeout=300, stdin=subprocess.DEVNULL)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-4000:]
    assert 'SKIP' not in out, out[-2000:]

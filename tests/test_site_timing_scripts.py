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


def test_siblings_resolve_through_the_sbin_link(tmp_path):
    """Run through a symlink (as /usr/local/sbin/sigmond-site-timing is),
    the script finds lib/ and sigmond-ts1-advertise in its real checkout;
    as a plain copy elsewhere, it falls back to the fixed paths."""
    repo = TESTS.parent
    text = (repo / 'bin' / 'sigmond-site-timing').read_text()
    head = text[:text.index('\nsay(){')]
    probe = head + '\necho "$_ST_LIB|$_ST_TS1_ADVERTISE"\n'
    fake = tmp_path / 'checkout'
    (fake / 'bin').mkdir(parents=True)
    (fake / 'lib' / 'sigmond').mkdir(parents=True)
    (fake / 'lib' / 'sigmond' / 'sdr_map.py').write_text('')
    adv = fake / 'bin' / 'sigmond-ts1-advertise'
    adv.write_text('#!/bin/sh\n')
    adv.chmod(0o755)
    real = fake / 'bin' / 'sigmond-site-timing'
    real.write_text(probe)
    real.chmod(0o755)
    link = tmp_path / 'sbin' / 'sigmond-site-timing'
    link.parent.mkdir()
    link.symlink_to(real)
    out = subprocess.run([str(link)], capture_output=True, text=True).stdout.strip()
    assert out == f'{fake}/lib|{fake}/bin/sigmond-ts1-advertise'
    plain = tmp_path / 'plain'
    plain.write_text(probe)
    plain.chmod(0o755)
    out = subprocess.run([str(plain)], capture_output=True, text=True).stdout.strip()
    assert out == '/opt/git/sigmond/sigmond/lib|/usr/local/sbin/sigmond-ts1-advertise'

"""scripts/install/site-timing-link.sh: /usr/local/sbin/sigmond-site-timing
becomes a link to sigmond's checkout (2026-11-15).

The script moved here from sigmond-appliance, whose wizard copied it into
the VM as a plain file that nothing refreshed.  install.sh, `smd component
update sigmond` and `smd align --apply` run this helper.  A plain file is
kept once as .pre-sigmond-link.
"""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / 'scripts' / 'install' / 'site-timing-link.sh'


def run(tmp, src=None, dest=None):
    env = dict(os.environ,
               SITE_TIMING_SRC=str(src or tmp / 'checkout' / 'bin' / 'sigmond-site-timing'),
               SITE_TIMING_DEST=str(dest or tmp / 'sbin' / 'sigmond-site-timing'))
    return subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True, text=True)


def setup(tmp):
    src = tmp / 'checkout' / 'bin' / 'sigmond-site-timing'
    src.parent.mkdir(parents=True)
    src.write_text('#!/bin/bash\necho new\n')
    src.chmod(0o755)
    dest = tmp / 'sbin' / 'sigmond-site-timing'
    return src, dest


def test_absent_destination_is_linked(tmp_path):
    src, dest = setup(tmp_path)
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert dest.is_symlink() and os.readlink(dest) == str(src)
    assert 'linked' in r.stdout


def test_plain_file_is_backed_up_once_then_linked(tmp_path):
    src, dest = setup(tmp_path)
    dest.parent.mkdir()
    dest.write_text('#!/bin/bash\necho old\n')
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert os.readlink(dest) == str(src)
    backup = dest.with_name(dest.name + '.pre-sigmond-link')
    assert backup.read_text() == '#!/bin/bash\necho old\n'
    # An old wizard writes a plain copy again: replaced, first backup kept.
    dest.unlink()
    dest.write_text('#!/bin/bash\necho older\n')
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert os.readlink(dest) == str(src)
    assert backup.read_text() == '#!/bin/bash\necho old\n'


def test_current_link_is_left_alone(tmp_path):
    src, dest = setup(tmp_path)
    run(tmp_path)
    before = os.lstat(dest).st_ino
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert 'current' in r.stdout
    assert os.lstat(dest).st_ino == before
    assert not dest.with_name(dest.name + '.pre-sigmond-link').exists()


def test_link_elsewhere_is_repointed(tmp_path):
    src, dest = setup(tmp_path)
    dest.parent.mkdir()
    other = tmp_path / 'other'
    other.write_text('x')
    dest.symlink_to(other)
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert os.readlink(dest) == str(src)
    assert other.read_text() == 'x'


def test_missing_source_fails_and_touches_nothing(tmp_path):
    dest = tmp_path / 'sbin' / 'sigmond-site-timing'
    dest.parent.mkdir()
    dest.write_text('plain')
    r = run(tmp_path, src=tmp_path / 'nope')
    assert r.returncode != 0
    assert dest.read_text() == 'plain' and not dest.is_symlink()


def test_default_source_is_this_checkout(tmp_path):
    dest = tmp_path / 'sbin' / 'sigmond-site-timing'
    env = dict(os.environ, SITE_TIMING_DEST=str(dest))
    env.pop('SITE_TIMING_SRC', None)
    r = subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert os.readlink(dest) == str(REPO / 'bin' / 'sigmond-site-timing')


def test_install_sh_runs_it():
    text = (REPO / 'install.sh').read_text()
    assert 'scripts/install/site-timing-link.sh' in text

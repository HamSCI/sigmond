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


# --- align_apply.link_site_timing: what `smd align --apply` does with it ---

from sigmond import align_apply  # noqa: E402


def _repo(tmp, body='#!/bin/bash\necho new\n'):
    repo = tmp / 'sigmond'
    (repo / 'bin').mkdir(parents=True)
    (repo / 'scripts' / 'install').mkdir(parents=True)
    src = repo / 'bin' / 'sigmond-site-timing'
    src.write_text(body)
    src.chmod(0o755)
    (repo / 'scripts' / 'install' / 'site-timing-link.sh').write_text(SCRIPT.read_text())
    return repo, src


def _paths(tmp):
    dest = tmp / 'sbin' / 'sigmond-site-timing'
    dest.parent.mkdir()
    return dest, tmp / 'state' / 'site-timing.sha256'


def test_align_plain_copy_that_differs_is_linked_and_refreshed(tmp_path):
    repo, src = _repo(tmp_path)
    dest, stamp = _paths(tmp_path)
    dest.write_text('#!/bin/bash\necho old\n')
    st = align_apply.link_site_timing(repo, dest=dest, stamp=stamp)
    assert st.outcome == 'refreshed'
    assert os.readlink(dest) == str(src)
    # bring-up runs it, then the stamp records it: the next align is quiet
    align_apply.record_site_timing_run(dest, stamp)
    assert align_apply.link_site_timing(repo, dest=dest, stamp=stamp) is None


def test_align_plain_copy_with_same_bytes_is_linked_not_rerun(tmp_path):
    repo, src = _repo(tmp_path)
    dest, stamp = _paths(tmp_path)
    dest.write_text(src.read_text())
    st = align_apply.link_site_timing(repo, dest=dest, stamp=stamp)
    assert st.outcome == 'linked'
    assert stamp.exists()


def test_align_pull_that_changes_the_script_is_refreshed(tmp_path):
    repo, src = _repo(tmp_path)
    dest, stamp = _paths(tmp_path)
    align_apply.link_site_timing(repo, dest=dest, stamp=stamp)
    align_apply.record_site_timing_run(dest, stamp)
    src.write_text('#!/bin/bash\necho newer\n')
    st = align_apply.link_site_timing(repo, dest=dest, stamp=stamp)
    assert st is not None and st.outcome == 'refreshed'


def test_align_link_without_stamp_is_not_rerun_on_a_guess(tmp_path):
    repo, src = _repo(tmp_path)
    dest, stamp = _paths(tmp_path)
    dest.symlink_to(src)
    assert align_apply.link_site_timing(repo, dest=dest, stamp=stamp) is None
    assert stamp.exists()


def test_align_sigmond_before_the_move_reports_nothing(tmp_path):
    dest, stamp = _paths(tmp_path)
    dest.write_text('plain')
    assert align_apply.link_site_timing(tmp_path / 'old', dest=dest, stamp=stamp) is None
    assert dest.read_text() == 'plain'


def test_align_dry_run_states(tmp_path):
    repo, src = _repo(tmp_path)
    dest, _ = _paths(tmp_path)
    assert align_apply.site_timing_link_state(repo, dest)['status'] == 'absent'
    dest.write_text('plain')
    assert align_apply.site_timing_link_state(repo, dest)['status'] == 'differs'
    dest.unlink()
    dest.symlink_to(src)
    assert align_apply.site_timing_link_state(repo, dest)['status'] == 'current'

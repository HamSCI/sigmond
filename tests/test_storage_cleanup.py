"""Tests for sigmond.storage.cleanup and expiring log levels."""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sigmond.log_cmd import expire_log_levels, set_log_level
from sigmond.storage.cleanup import report_unclaimed, run_cleanup


def test_cleanup_runs_every_step_even_after_a_failure(tmp_path):
    calls = []

    def run(argv, **kw):
        calls.append(argv[0])
        return SimpleNamespace(returncode=1 if argv[0] == "journalctl" else 0,
                               stdout="", stderr="x")
    old = tmp_path / "old"; old.write_text("x"); os.utime(old, (0, 0))
    new = tmp_path / "new"; new.write_text("x")
    res = run_cleanup(run=run, now=10 * 86400, tmp_root=tmp_path, tmp_uid=os.getuid())
    assert calls == ["journalctl", "apt-get"] and not res[0].ok and res[1].ok
    assert not old.exists() and new.exists()


def test_cleanup_spares_symlinks_and_directories(tmp_path):
    run = lambda argv, **kw: SimpleNamespace(returncode=0, stdout="", stderr="")
    target = tmp_path / "keep"; target.write_text("x")
    d = tmp_path / "sub"; d.mkdir()
    link = d / "ln"; link.symlink_to(target)
    os.utime(link, (0, 0), follow_symlinks=False)
    os.utime(target, (0, 0))
    run_cleanup(run=run, now=10 * 86400, tmp_root=d, tmp_uid=os.getuid())
    assert link.is_symlink() and target.exists()


def test_unclaimed_skips_claimed_paths(tmp_path):
    (tmp_path / "srv" / "big").mkdir(parents=True)
    (tmp_path / "srv" / "client").mkdir()
    sizes = {str(tmp_path / "srv" / "big"): 2 << 30,
             str(tmp_path / "srv" / "client"): 5 << 30}
    got = report_unclaimed([str(tmp_path / "srv" / "client")],
                           roots=(str(tmp_path / "srv"),),
                           size_of=lambda ps: sizes.get(ps[0], 0),
                           proc_mounts=str(tmp_path / "none"))
    assert got == [(str(tmp_path / "srv" / "big"), 2 << 30)]


def test_raised_level_expires_back_to_routine(tmp_path):
    env = tmp_path / "coordination.env"
    env.write_text("HF_TIMESTD_LOG_LEVEL=INFO\n")
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    set_log_level("hf-timestd", "DEBUG", env_path=env, until=now + timedelta(hours=2))
    assert "HF_TIMESTD_LOG_LEVEL_UNTIL=2026-11-04T02:00:00Z" in env.read_text()
    assert expire_log_levels(env, now + timedelta(hours=1)) == []
    assert expire_log_levels(env, now + timedelta(hours=3)) == ["HF_TIMESTD_LOG_LEVEL"]
    assert env.read_text() == "HF_TIMESTD_LOG_LEVEL=INFO\n"


def test_expiry_removes_key_when_no_routine_level(tmp_path):
    env = tmp_path / "coordination.env"
    env.write_text("OTHER=1\n")
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    set_log_level("psk-recorder", "DEBUG", env_path=env, until=now + timedelta(hours=1))
    assert expire_log_levels(env, now + timedelta(hours=2)) == ["PSK_RECORDER_LOG_LEVEL"]
    assert env.read_text() == "OTHER=1\n"


def test_second_raise_keeps_first_routine(tmp_path):
    env = tmp_path / "coordination.env"
    env.write_text("HF_TIMESTD_LOG_LEVEL=WARNING\n")
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    set_log_level("hf-timestd", "DEBUG", env_path=env, until=now + timedelta(hours=1))
    set_log_level("hf-timestd", "DEBUG", env_path=env, until=now + timedelta(hours=5))
    expire_log_levels(env, now + timedelta(hours=6))
    assert env.read_text() == "HF_TIMESTD_LOG_LEVEL=WARNING\n"


def test_plain_write_cancels_pending_expiry(tmp_path):
    env = tmp_path / "coordination.env"
    env.write_text("HF_TIMESTD_LOG_LEVEL=INFO\n")
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    set_log_level("hf-timestd", "DEBUG", env_path=env, until=now + timedelta(hours=2))
    set_log_level("hf-timestd", "WARNING", env_path=env)
    assert expire_log_levels(env, now + timedelta(hours=3)) == []
    assert env.read_text() == "HF_TIMESTD_LOG_LEVEL=WARNING\n"


def test_cleanup_refuses_symlinked_tmp_root(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    f = real / "old"; f.write_text("x"); os.utime(f, (0, 0))
    link = tmp_path / "link"; link.symlink_to(real)
    run = lambda argv, **kw: SimpleNamespace(returncode=0, stdout="", stderr="")
    res = run_cleanup(run=run, now=10 * 86400, tmp_root=link, tmp_uid=os.getuid())
    assert not res[2].ok and "symlink" in res[2].detail and f.exists()


def test_env_file_keeps_its_mode(tmp_path):
    env = tmp_path / "coordination.env"
    env.write_text("HF_TIMESTD_LOG_LEVEL=INFO\n")
    os.chmod(env, 0o640)
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    set_log_level("hf-timestd", "DEBUG", env_path=env, until=now + timedelta(hours=1))
    assert (env.stat().st_mode & 0o7777) == 0o640
    expire_log_levels(env, now + timedelta(hours=2))
    assert (env.stat().st_mode & 0o7777) == 0o640


def test_new_env_file_is_world_readable(tmp_path):
    env = tmp_path / "new.env"
    set_log_level("hf-timestd", "INFO", env_path=env)
    assert (env.stat().st_mode & 0o7777) == 0o644


# ---- the root tmp trim trusts only a root-owned, closed directory ------------

def _ok_run(argv, **kw):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def _tmp_with_old_file(tmp_path, mode=0o755):
    d = tmp_path / "tmp"; d.mkdir(); os.chmod(d, mode)
    f = d / "old"; f.write_text("x"); os.utime(f, (0, 0))
    return d, f


def test_tmp_trim_refuses_a_root_owned_by_someone_else(tmp_path):
    d, f = _tmp_with_old_file(tmp_path)
    res = run_cleanup(run=_ok_run, now=10 * 86400, tmp_root=d, tmp_uid=os.getuid() + 1)
    assert not res[2].ok and "owned by" in res[2].detail and f.exists()


@pytest.mark.parametrize("mode", [0o775, 0o757, 0o777, 0o1777])
def test_tmp_trim_refuses_a_root_others_may_write(tmp_path, mode):
    d, f = _tmp_with_old_file(tmp_path, mode)
    res = run_cleanup(run=_ok_run, now=10 * 86400, tmp_root=d, tmp_uid=os.getuid())
    assert not res[2].ok and "writable" in res[2].detail and f.exists()


def test_tmp_trim_unlinks_relative_to_a_directory_descriptor(tmp_path, monkeypatch):
    d, f = _tmp_with_old_file(tmp_path)
    sub = d / "sub"; sub.mkdir()
    g = sub / "older"; g.write_text("y"); os.utime(g, (0, 0))
    outside = tmp_path / "outside"; outside.mkdir()
    h = outside / "victim"; h.write_text("z"); os.utime(h, (0, 0))
    (d / "link").symlink_to(outside)
    seen = []
    real = os.unlink

    def unlink(path, *, dir_fd=None):
        seen.append((path, dir_fd))
        return real(path, dir_fd=dir_fd)
    monkeypatch.setattr(os, "unlink", unlink)
    res = run_cleanup(run=_ok_run, now=10 * 86400, tmp_root=d, tmp_uid=os.getuid())
    assert res[2].ok and not f.exists() and not g.exists() and h.exists()
    assert sorted(p for p, _ in seen) == ["old", "older"]
    assert all(fd is not None for _, fd in seen)


def test_tmpfiles_creates_the_tmp_root_as_root_0755():
    from pathlib import Path
    conf = Path(__file__).resolve().parent.parent / "systemd" / "sigmond-storage.conf"
    lines = [l.split() for l in conf.read_text().splitlines()
             if l.strip() and not l.lstrip().startswith("#")]
    assert ["d", "/var/lib/sigmond/tmp", "0755", "root", "root", "-"] in lines

"""Write rates: the source precedence, recent bytes and churn."""
import os

import pytest

from sigmond.storage import rates

GiB = 1 << 30


def test_a_ring_at_steady_state_still_has_a_rate(tmp_path):
    # holdings flat, but files keep being rewritten: recent_bytes sees them
    f = tmp_path / "x"; f.write_bytes(b"x" * 8192)
    assert rates.recent_bytes([str(tmp_path)], now=os.stat(f).st_mtime + 10) >= 8192
    assert rates.rate_for("c", reported=None, recent_bytes=5, declared_mb_per_day=1.0) == (5.0, "measured")
    assert rates.rate_for("c", reported=7, recent_bytes=5, declared_mb_per_day=1.0) == (7.0, "reported")


def test_declared_is_megabytes_and_none_is_zero():
    assert rates.rate_for("c", reported=None, recent_bytes=None, declared_mb_per_day=2.5) == (2.5e6, "declared")
    assert rates.rate_for("c", reported=None, recent_bytes=None, declared_mb_per_day=0.0) == (0.0, "none")
    assert rates.rate_for("c", reported=None, recent_bytes=None, declared_mb_per_day=None) == (0.0, "none")


def test_a_measured_zero_yields_to_a_declaration():
    # A stopped or idle client writes nothing for a day; its declaration still stands.
    assert rates.rate_for("c", reported=None, recent_bytes=0, declared_mb_per_day=1.0) == (1e6, "declared")
    assert rates.rate_for("c", reported=None, recent_bytes=0, declared_mb_per_day=0.0) == (0.0, "measured")
    assert rates.rate_for("c", reported=0, recent_bytes=9, declared_mb_per_day=1.0) == (0.0, "reported")


def _aged(names, ctime):
    """os.lstat, with st_ctime set back for `names` (user space cannot)."""
    def stat(path):
        st = os.lstat(path)
        if str(path) not in names:
            return st
        fields = list(st)
        fields[9] = int(ctime)                 # st_ctime's tuple slot
        return os.stat_result(fields, {"st_ctime": float(ctime)})
    return stat


def test_recent_bytes_skips_old_files_symlinks_and_counts_each_inode_once(tmp_path):
    new = tmp_path / "new"; new.write_bytes(b"x" * 8192)
    old = tmp_path / "old"; old.write_bytes(b"y" * 8192)
    now = os.stat(new).st_ctime + 10
    os.utime(old, (now - 2 * 86400, now - 2 * 86400))
    os.link(new, tmp_path / "hard")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir(); (outside / "big").write_bytes(b"z" * 65536)
    (tmp_path / "ln").symlink_to(outside, target_is_directory=True)
    (tmp_path / "lnf").symlink_to(outside / "big")
    got = rates.recent_sums([str(tmp_path)], now=now, stat=_aged({str(old)}, now - 2 * 86400))
    assert got == (os.stat(new).st_blocks * 512,) * 2
    assert rates.recent_bytes([str(tmp_path)], now=now) == got[0]


def test_recent_bytes_of_a_missing_path_is_zero(tmp_path):
    assert rates.recent_bytes([str(tmp_path / "gone")], now=0.0) == 0


def test_churn_by_least_squares_needs_an_hour():
    assert rates.churn_bytes_per_h([(0, 0), (1800, 10)]) is None
    assert rates.churn_bytes_per_h([(0, 0), (3600, GiB), (7200, 2 * GiB)]) == pytest.approx(GiB)
    assert rates.churn_bytes_per_h([(0, GiB), (3600, 0)]) == 0.0


def test_backdated_mtimes_count_by_ctime_not_in_the_rate(tmp_path):
    # tar -x, rsync -t and os.utime set mtime back; ctime still moves, so the
    # idle check sees the write.  The rate counts by mtime, as declared.
    f = tmp_path / "restored"; f.write_bytes(b"x" * 8192)
    now = os.stat(f).st_ctime + 10
    os.utime(f, (now - 30 * 86400, now - 30 * 86400))
    by_mtime, by_ctime = rates.recent_sums([str(tmp_path)], now=now)
    assert by_mtime == 0 and by_ctime >= 8192


def test_a_renamed_old_file_raises_the_ctime_sum_but_not_the_rate(tmp_path):
    f = tmp_path / "old"; f.write_bytes(b"x" * 8192)
    then = os.stat(f).st_mtime - 10 * 86400
    os.utime(f, (then, then))
    f.rename(tmp_path / "renamed")                  # moves ctime, not mtime
    now = os.stat(tmp_path / "renamed").st_ctime + 10
    by_mtime, by_ctime = rates.recent_sums([str(tmp_path)], now=now)
    assert by_mtime == 0 and by_ctime >= 8192
    assert rates.recent_bytes([str(tmp_path)], now=now) == 0

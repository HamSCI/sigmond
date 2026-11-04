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


def test_recent_bytes_skips_old_files_symlinks_and_counts_each_inode_once(tmp_path):
    new = tmp_path / "new"; new.write_bytes(b"x" * 8192)
    old = tmp_path / "old"; old.write_bytes(b"y" * 8192)
    now = os.stat(new).st_mtime + 10
    os.utime(old, (now - 2 * 86400, now - 2 * 86400))
    os.link(new, tmp_path / "hard")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir(); (outside / "big").write_bytes(b"z" * 65536)
    (tmp_path / "ln").symlink_to(outside, target_is_directory=True)
    (tmp_path / "lnf").symlink_to(outside / "big")
    got = rates.recent_bytes([str(tmp_path)], now=now)
    assert got == os.stat(new).st_blocks * 512


def test_recent_bytes_of_a_missing_path_is_zero(tmp_path):
    assert rates.recent_bytes([str(tmp_path / "gone")], now=0.0) == 0


def test_churn_by_least_squares_needs_an_hour():
    assert rates.churn_bytes_per_h([(0, 0), (1800, 10)]) is None
    assert rates.churn_bytes_per_h([(0, 0), (3600, GiB), (7200, 2 * GiB)]) == pytest.approx(GiB)
    assert rates.churn_bytes_per_h([(0, GiB), (3600, 0)]) == 0.0

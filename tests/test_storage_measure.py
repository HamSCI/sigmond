import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sigmond.storage.measure import (
    discover_stores, host_report_expected, measure_store, path_bytes,
    read_host_report,
)

GiB = 1 << 30


def _report(now, data_pct=50.0, metadata_pct=10.0, size=100 * GiB, vms=(100,)):
    return {
        "schema": 1,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vmid": 100,
        "pool": {"name": "pve/data", "size_bytes": size,
                 "data_pct": data_pct, "metadata_pct": metadata_pct},
        "vms_on_pool": list(vms),
        "snapshots": [{"vmid": 100, "name": "pre-v385",
                       "taken": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "sigmond": False, "expires": None}],
        "host_alarm": "",
    }


def test_reserved_blocks_count_as_used():
    fake = lambda p: SimpleNamespace(f_blocks=1000, f_frsize=4096, f_bavail=100, f_bfree=150)
    s = measure_store("root", "/", "guest", statvfs=fake)
    assert s.capacity == 4096000 and s.used == 900 * 4096


def test_a_vanished_store_is_a_fault_not_a_crash(tmp_path):
    def sv(p):
        if p == "/mnt/gone":
            raise OSError(5, "EIO")
        return SimpleNamespace(f_blocks=10, f_frsize=1, f_bavail=5, f_bfree=5)
    mounts = tmp_path / "m"
    mounts.write_text("/dev/sda1 / ext4 rw 0 0\n/dev/sdb1 /mnt/gone ext4 rw 0 0\n")
    stores, faults = discover_stores(["/mnt/gone/x"], "/var/lib/sigmond/archive", str(mounts), sv)
    assert [s.name for s in stores] == ["root"] and "/mnt/gone" in faults[0]


def test_archive_and_other_mounts_are_named(tmp_path):
    sv = lambda p: SimpleNamespace(f_blocks=10, f_frsize=1, f_bavail=5, f_bfree=5)
    mounts = tmp_path / "m"
    mounts.write_text("/dev/sda1 / ext4 rw 0 0\n/dev/sdb1 /var/lib/sigmond/archive ext4 rw 0 0\n"
                      "/dev/sdc1 /mnt/data ext4 rw 0 0\n")
    stores, faults = discover_stores(["/mnt/data/x", "/home/y"], "/var/lib/sigmond/archive",
                                     str(mounts), sv)
    assert [(s.name, s.kind) for s in stores] == [
        ("root", "guest"), ("archive", "guest"), ("mnt:/mnt/data", "guest")]
    assert faults == []


def test_path_bytes_counts_hardlinks_once(tmp_path):
    f = tmp_path / "a"
    f.write_bytes(b"x" * 10000)
    os.link(f, tmp_path / "b")
    assert path_bytes([str(tmp_path)]) == (
        os.stat(f).st_blocks + os.stat(tmp_path).st_blocks) * 512


def test_host_report_faults(tmp_path):
    now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
    p = tmp_path / "h.json"
    assert read_host_report(str(p), now) == (None, "missing")
    p.write_text("{")
    assert read_host_report(str(p), now)[1] == "unreadable"
    p.write_text(json.dumps(_report(now - timedelta(seconds=901))))
    assert read_host_report(str(p), now)[1] == "stale"
    p.write_text(json.dumps(_report(now)))
    rep, fault = read_host_report(str(p), now)
    assert fault is None and rep.pool.kind == "pool"
    assert rep.vms_on_pool == (100,)


def test_metadata_fill_counts_as_pool_fill(tmp_path):
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    p = tmp_path / "h.json"
    p.write_text(json.dumps(_report(now, data_pct=40.0, metadata_pct=96.0)))
    assert read_host_report(str(p), now)[0].pool.used_pct == pytest.approx(96.0)


def test_bare_metal_expects_no_host_report():
    assert not host_report_expected(None, detect_virt=lambda: "none")
    assert host_report_expected(None, detect_virt=lambda: "kvm")
    assert not host_report_expected(False, detect_virt=lambda: "kvm")
    assert host_report_expected(True, detect_virt=lambda: "none")


def test_future_and_naive_timestamps_are_faults(tmp_path):
    now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
    p = tmp_path / "h.json"
    p.write_text(json.dumps(_report(now + timedelta(seconds=901))))
    assert read_host_report(str(p), now)[1] == "stale"
    p.write_text(json.dumps(_report(now + timedelta(seconds=60))))
    assert read_host_report(str(p), now)[1] is None
    rep = _report(now)
    rep["generated_at"] = "2026-11-04T12:00:00"
    p.write_text(json.dumps(rep))
    assert read_host_report(str(p), now) == (None, "unreadable")

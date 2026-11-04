"""Measure stores and read the host's pool report.

Reads the filesystem; the arithmetic lives in model.py.
"""

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from .model import Store

HOST_REPORT_PATH = "/run/sigmond/host-pool.json"
HOST_REPORT_MAX_AGE_S = 900


def measure_store(name: str, mount: str, kind: str, statvfs=os.statvfs) -> Store:
    """Capacity and use of one mount; ext4's reserved blocks count as used."""
    st = statvfs(mount)
    capacity = st.f_blocks * st.f_frsize
    return Store(name, mount, kind, capacity, capacity - st.f_bavail * st.f_frsize)


def _mountpoints(proc_mounts: str) -> list:
    points = []
    try:
        with open(proc_mounts) as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2:
                    points.append(parts[1].replace("\\040", " "))
    except OSError:
        pass
    return points


def _under(path: str, mount: str) -> bool:
    return mount == "/" or path == mount or path.startswith(mount.rstrip("/") + "/")


def discover_stores(paths: Sequence[str], archive_mount: str,
                    proc_mounts: str = "/proc/mounts", statvfs=os.statvfs):
    """Return (stores, faults) for root, the archive mount and any mount
    holding a registered path.  An unmeasurable store becomes a fault line."""
    points = _mountpoints(proc_mounts)
    wanted = [("root", "/", "guest")]
    if archive_mount in points:
        wanted.append(("archive", archive_mount, "guest"))
    for p in paths:
        best = max((m for m in points if _under(p, m)), key=len, default="/")
        if best in ("/", archive_mount) or any(w[1] == best for w in wanted):
            continue
        wanted.append(("mnt:" + best, best, "guest"))
    stores, faults = [], []
    for name, mount, kind in wanted:
        try:
            stores.append(measure_store(name, mount, kind, statvfs))
        except OSError as exc:
            faults.append(f"store {name} ({mount}) unreadable: {exc}")
    return stores, faults


def path_bytes(paths: Sequence[str]) -> int:
    """Bytes allocated under paths: st_blocks*512, symlinks not followed,
    each inode once, vanished files ignored."""
    seen = set()
    total = 0

    def count(st):
        nonlocal total
        key = (st.st_dev, st.st_ino)
        if key not in seen:
            seen.add(key)
            total += st.st_blocks * 512

    for root in paths:
        try:
            st = os.lstat(root)
        except OSError:
            continue
        count(st)
        if not os.path.isdir(root) or os.path.islink(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for n in dirnames + filenames:
                try:
                    count(os.lstat(os.path.join(dirpath, n)))
                except OSError:
                    pass
    return total


@dataclass(frozen=True)
class HostReport:
    generated_at: datetime
    pool: Store
    vms_on_pool: tuple
    snapshots: tuple
    host_alarm: str


def _parse_time(text: str) -> datetime:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp carries no offset")
    return parsed.astimezone(timezone.utc)


def read_host_report(path: str, now: datetime):
    """Return (report, fault); fault is "missing", "stale", "unreadable" or None."""
    from ..safe_write import read_nofollow
    try:
        text = read_nofollow(path)      # no symlink, regular file, at most 1 MiB
    except FileNotFoundError:
        return None, "missing"          # the directory itself is absent
    except (OSError, ValueError):
        return None, "unreadable"
    if text is None:
        return None, "missing"
    try:
        raw = json.loads(text)
    except ValueError:
        return None, "unreadable"
    try:
        if raw.get("schema") != 1:
            return None, "unreadable"
        generated = _parse_time(raw["generated_at"])
        pool = raw["pool"]
        size = int(pool["size_bytes"])
        fill = max(float(pool["data_pct"]), float(pool["metadata_pct"]))
        store = Store(pool["name"], pool["name"], "pool", size, int(size * fill / 100))
        report = HostReport(
            generated_at=generated,
            pool=store,
            vms_on_pool=tuple(int(v) for v in raw.get("vms_on_pool", [])),
            snapshots=tuple(dict(s) for s in raw.get("snapshots", [])),
            host_alarm=str(raw.get("host_alarm", "")),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None, "unreadable"
    if abs((now - generated).total_seconds()) > HOST_REPORT_MAX_AGE_S:
        return None, "stale"
    return report, None


def _detect_virt() -> str:
    try:
        r = subprocess.run(["systemd-detect-virt", "--vm"], capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "none"
    return r.stdout.strip() if r.returncode == 0 else "none"


def host_report_expected(cfg_expect: Optional[bool], detect_virt=_detect_virt) -> bool:
    """A VM expects the host's report; bare metal does not.  Config overrides."""
    if cfg_expect is not None:
        return cfg_expect
    return detect_virt() not in ("", "none")

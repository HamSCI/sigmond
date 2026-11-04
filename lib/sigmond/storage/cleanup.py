"""Clean-up sigmond does on its own, and a read-only report of the rest.

run_cleanup touches only sigmond's journal budget, the apt cache and old
regular files under sigmond's own tmp directory.  report_unclaimed only
reports sizes; it never deletes anything.
"""

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from ..paths import SIGMOND_STATE
from .measure import path_bytes

JOURNAL_TARGET = "500M"
SIGMOND_TMP = Path("/var/lib/sigmond/tmp")
TMP_MAX_AGE_S = 7 * 86400
UNCLAIMED_MIN_BYTES = 1 << 30
UNCLAIMED_ROOTS = ("/var/tmp", "/tmp", "/home", "/srv", "/var/lib", "/opt", "/mnt")


@dataclass(frozen=True)
class CleanupResult:
    step: str
    ok: bool
    detail: str


def _run_step(step: str, argv: list, run) -> CleanupResult:
    try:
        r = run(argv, capture_output=True, text=True, timeout=300)
    except Exception as exc:
        return CleanupResult(step, False, str(exc))
    out = (getattr(r, "stdout", "") or getattr(r, "stderr", "") or "").strip()
    return CleanupResult(step, r.returncode == 0, out.splitlines()[-1] if out else "")


def _trim_tmp(tmp_root: Path, now: float, uid: int = 0) -> CleanupResult:
    """Delete regular files under `tmp_root` older than TMP_MAX_AGE_S.

    The tick runs as root, so the walk trusts nothing it does not hold open:
    the root must be a real directory owned by `uid` (root) that no group or
    other may write, opened without following a link; the walk goes by
    directory descriptors (os.fwalk, never following links), and each
    unlink names the file relative to its directory's descriptor.  A
    directory swapped for a link mid-walk then cannot steer a delete
    outside the tree."""
    removed = freed = 0
    if os.path.islink(tmp_root):
        return CleanupResult("tmp", False, f"{tmp_root} is a symlink; not trimmed")
    try:
        rootfd = os.open(tmp_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return CleanupResult("tmp", True, f"{tmp_root} absent; nothing to trim")
    except OSError as exc:
        return CleanupResult("tmp", False, f"{tmp_root}: {exc.strerror or exc}; not trimmed")
    try:
        st = os.fstat(rootfd)
        if st.st_uid != uid:
            return CleanupResult("tmp", False, f"{tmp_root} is owned by uid {st.st_uid}, "
                                               f"not uid {uid}; not trimmed")
        if st.st_mode & 0o022:
            return CleanupResult("tmp", False, f"{tmp_root} is group- or other-writable "
                                               f"(mode {stat.S_IMODE(st.st_mode):04o}); "
                                               f"not trimmed")
        for _dirpath, _dirs, files, dirfd in os.fwalk(".", dir_fd=rootfd,
                                                      follow_symlinks=False):
            for name in files:
                try:
                    fst = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
                    if stat.S_ISREG(fst.st_mode) and now - fst.st_mtime > TMP_MAX_AGE_S:
                        os.unlink(name, dir_fd=dirfd)
                        removed += 1
                        freed += fst.st_size
                except OSError:
                    continue
    except OSError as exc:
        return CleanupResult("tmp", False, str(exc))
    finally:
        os.close(rootfd)
    return CleanupResult("tmp", True, f"removed {removed} files, {freed} bytes")


def run_cleanup(run=subprocess.run, now: Optional[float] = None,
                tmp_root: Path = SIGMOND_TMP, tmp_uid: int = 0) -> list:
    """Vacuum the journal, clean the apt cache, trim old tmp files.
    One step failing never stops the next.  `tmp_uid` names the owner the
    tmp root must have (root; tests pass their own uid)."""
    import time
    if now is None:
        now = time.time()
    return [
        _run_step("journal", ["journalctl", f"--vacuum-size={JOURNAL_TARGET}"], run),
        _run_step("apt", ["apt-get", "clean"], run),
        _trim_tmp(Path(tmp_root), now, tmp_uid),
    ]


def _inside(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip("/") + "/")


def _loop_mounts(proc_mounts: str) -> list:
    points = []
    try:
        with open(proc_mounts) as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].startswith("/dev/loop"):
                    points.append(parts[1].replace("\\040", " "))
    except OSError:
        pass
    return points


def report_unclaimed(claimed: Sequence[str], roots=UNCLAIMED_ROOTS,
                     size_of=path_bytes, proc_mounts: str = "/proc/mounts") -> list:
    """Directories (depth <= 2 under each root) outside every claimed path
    and sigmond's state dirs, with size >= 1 GiB, largest first.
    Loop-device mounts under the roots are listed too.  Reports only."""
    skip = [str(c).rstrip("/") or "/" for c in claimed] + [str(SIGMOND_STATE)]
    seen = {}

    def consider(path: str) -> None:
        if path in seen or any(_inside(path, s) for s in skip):
            return
        size = size_of([path])
        if size >= UNCLAIMED_MIN_BYTES:
            seen[path] = size

    def subdirs(path: str) -> list:
        try:
            with os.scandir(path) as it:
                return sorted(e.path for e in it
                              if e.is_dir(follow_symlinks=False))
        except OSError:
            return []

    for root in roots:
        for d1 in subdirs(root):
            holds_claim = any(_inside(s, d1) and s != d1 for s in skip)
            if not holds_claim:
                consider(d1)
                continue
            for d2 in subdirs(d1):
                consider(d2)
    for mp in _loop_mounts(proc_mounts):
        if any(_inside(mp, r) for r in roots):
            consider(mp)
    return sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))

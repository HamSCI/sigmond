"""Write rates and churn: the numbers that turn bytes into days.

A client's rate means the bytes it writes per day, not the growth of its
holdings: a ring buffer at steady state writes gigabytes an hour and grows
by nothing.  Sources, best first:

1. `reported`: `bytes_written_per_day` from the client's `holdings` answer.
2. `measured`: the allocated bytes of its files changed in the last 24 h
   (by st_ctime, which every write moves and no user-space call can set back)
   (`recent_bytes`; the tick caches it per client for 30 minutes).
3. `declared`: the `mb_per_day` its inventory declares (MB = 10**6 bytes).
4. `none`: rate 0.  The client then gets its floor only, and the status
   says "none" so an operator sees why.

A measured zero yields to a declaration: a client stopped or idle for a day
writes nothing, and its own declaration describes it better than silence.

A store's churn comes from its own used-bytes history, by least squares,
the way pm-storage measures a thin pool's.
"""

import os
import stat as stat_mod
from typing import Callable, Iterable, Optional, Sequence

DAY_S = 86400
MB = 10 ** 6


def rate_for(client: str, *, reported: Optional[int], recent_bytes: Optional[int],
             declared_mb_per_day: Optional[float]) -> tuple:
    """(bytes per day, source) for one client on one store.

    A measured zero falls back to a declared `mb_per_day` for the allotment
    (controller ruling, 2026-11-04): a client stopped or idle for a day
    writes nothing, and its declaration describes it better than silence.
    A reported zero stays authoritative.  (The stop lever reads the measured
    bytes themselves, not this rate: tick.py spares a client that wrote
    nothing in 24 h.)"""
    if reported is not None and reported >= 0:
        return float(reported), "reported"
    declared = (float(declared_mb_per_day) * MB
                if declared_mb_per_day is not None and declared_mb_per_day > 0 else None)
    if recent_bytes is not None and recent_bytes > 0:
        return float(recent_bytes), "measured"
    if declared is not None:
        return declared, "declared"
    if recent_bytes is not None:
        return 0.0, "measured"
    return 0.0, "none"


def recent_bytes(paths: Iterable[str], now: float, window_s: float = DAY_S,
                 stat: Callable = os.lstat) -> int:
    """Allocated bytes (st_blocks * 512) of regular files under `paths`
    changed within `window_s` before `now`.  It reads st_ctime, not
    st_mtime: a writer that preserves or backdates mtimes (tar -x,
    rsync -t, os.utime) still moves ctime, so it cannot pass for idle.
    Symlinks are never followed, each inode counts once, and files that
    vanish mid-walk are skipped."""
    seen = set()
    total = 0
    since = now - window_s

    def count(st) -> None:
        nonlocal total
        if not stat_mod.S_ISREG(st.st_mode) or st.st_ctime < since:
            return
        key = (st.st_dev, st.st_ino)
        if key not in seen:
            seen.add(key)
            total += st.st_blocks * 512

    for root in paths:
        try:
            st = stat(root)
        except OSError:
            continue
        if not stat_mod.S_ISDIR(st.st_mode):
            count(st)
            continue
        for dirpath, _dirnames, filenames in os.walk(root):   # followlinks=False
            for n in filenames:
                try:
                    count(stat(os.path.join(dirpath, n)))
                except OSError:
                    pass
    return total


CHURN_WINDOW_S = 6 * 3600


def churn_bytes_per_h(history: Sequence) -> Optional[float]:
    """Growth in bytes per hour by least squares over (epoch, used_bytes)
    samples; None until the samples span an hour; shrinking counts as 0."""
    pts = [(float(t), float(u)) for t, u in history]
    if len(pts) < 2 or max(t for t, _ in pts) - min(t for t, _ in pts) < 3600:
        return None
    tm = sum(t for t, _ in pts) / len(pts)
    um = sum(u for _, u in pts) / len(pts)
    den = sum((t - tm) ** 2 for t, _ in pts)
    slope = sum((t - tm) * (u - um) for t, u in pts) / den * 3600
    return max(0.0, slope)

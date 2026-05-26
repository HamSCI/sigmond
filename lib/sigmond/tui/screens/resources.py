"""Resources screen — system + sigmond storage summary.

One pane, four sections.  All read-only, no sudo:

  System          memory / load / uptime from /proc
  Filesystems     mountpoint capacity (statvfs) for the FS roots
                  sigmond writes into (/var, /tmp, /dev/shm), with a
                  free-space figure and a coarse "days remaining"
                  estimate when daily growth can be calculated.
  Sigmond data    per-client spool / log directory sizes (scandir,
                  permission errors silently filtered), plus the
                  sigmond sink.db + WAL sizes broken out.
  SQLite sink     per-table row counts in /var/lib/sigmond/sink.db's
                  pending_uploads queue, with oldest/newest queued
                  timestamps so the operator can spot a stalled
                  consumer (e.g. wspr verifier behind on its sweep).

Days-remaining estimate logic is deliberately conservative — only
fires when we have at least one "yesterday" sample to compare
against today's growth.  If the operator wants a more sophisticated
prediction, the underlying numbers are surfaced verbatim.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static
from textual.worker import Worker, WorkerState


SINK_DB = Path("/var/lib/sigmond/sink.db")

# Spool / log roots we report on.  None of these are required to
# exist — the screen renders "(absent)" for the missing ones.
_SPOOL_TARGETS = [
    ("sigmond data",      Path("/var/lib/sigmond")),
    ("psk-recorder",      Path("/var/lib/psk-recorder")),
    ("wspr-recorder",     Path("/var/lib/wspr-recorder")),
    ("hfdl-recorder",     Path("/var/lib/hfdl-recorder")),
    ("codar-sounder",     Path("/var/lib/codar-sounder")),
    ("mag-recorder",      Path("/var/lib/mag-recorder")),
    ("hf-timestd",        Path("/var/lib/timestd")),
    ("/var/log",          Path("/var/log")),
    ("/dev/shm",          Path("/dev/shm")),
]

# Mountpoints we surface in the Filesystems table.  Same defensive
# stance: missing → skipped.
_FILESYSTEM_TARGETS = ["/", "/var", "/tmp", "/dev/shm"]


def _fmt_bytes(n: int) -> str:
    if n is None:
        return "—"
    for unit, scale in (("TB", 1024**4), ("GB", 1024**3),
                        ("MB", 1024**2), ("KB", 1024)):
        if abs(n) >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{n} B"


def _fmt_pct(used: int, total: int) -> str:
    if not total:
        return "—"
    return f"{100.0 * used / total:.0f}%"


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} d"


def _fmt_days(d: float | None) -> str:
    if d is None:
        return "—"
    if d > 3650:
        return "> 10 y"
    if d > 365:
        return f"{d / 365:.1f} y"
    if d < 1:
        return f"{d * 24:.1f} h"
    return f"{d:.0f} d"


def _dir_size_safe(path: Path) -> tuple[int, int]:
    """Return (total_bytes, permission_skip_count) for path.

    Recursive scandir; subtrees that raise PermissionError are
    silently counted into the skip counter and excluded from the
    byte total.  Worst case (everything blocked) the size reads as
    zero with a non-zero skip count — surfaced in the UI so the
    operator sees the size is incomplete.
    """
    total = 0
    skipped = 0
    if not path.is_dir():
        return 0, 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            try:
                                total += entry.stat(
                                    follow_symlinks=False).st_size
                            except (OSError, PermissionError):
                                skipped += 1
                    except (OSError, PermissionError):
                        skipped += 1
        except (OSError, PermissionError):
            skipped += 1
    return total, skipped


def _read_meminfo() -> dict:
    """Parse /proc/meminfo into a dict of int kB values."""
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                rest = rest.strip()
                if not rest:
                    continue
                # Values are "<n> kB" or just "<n>" depending on key.
                tok = rest.split()
                try:
                    out[key.strip()] = int(tok[0])
                except (ValueError, IndexError):
                    continue
    except OSError:
        pass
    return out


def _read_loadavg() -> tuple[float, float, float]:
    try:
        parts = Path("/proc/loadavg").read_text().split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (OSError, ValueError, IndexError):
        return (0.0, 0.0, 0.0)


def _read_uptime_seconds() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        return 0.0


def _sqlite_summary() -> dict:
    """Open sink.db read-only and group pending_uploads by table."""
    if not SINK_DB.is_file():
        return {"error": "sink.db not present"}
    try:
        sizes = {
            "sink.db":     SINK_DB.stat().st_size,
            "sink.db-wal": (SINK_DB.parent / "sink.db-wal").stat().st_size
                           if (SINK_DB.parent / "sink.db-wal").is_file() else 0,
            "sink.db-shm": (SINK_DB.parent / "sink.db-shm").stat().st_size
                           if (SINK_DB.parent / "sink.db-shm").is_file() else 0,
        }
    except OSError as exc:
        return {"error": f"sink.db stat: {exc}"}
    try:
        # Read-only URI so we don't perturb the WAL.
        uri = f"file:{SINK_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        cur = conn.cursor()
        cur.execute(
            "SELECT target_db, target_table, COUNT(*), "
            "MIN(queued_at), MAX(queued_at) "
            "FROM pending_uploads GROUP BY target_db, target_table "
            "ORDER BY target_db, target_table"
        )
        rows = cur.fetchall()
        conn.close()
    except sqlite3.Error as exc:
        return {"sizes": sizes, "error": f"sqlite: {exc}"}
    return {"sizes": sizes, "queue": rows}


def _filesystems() -> list[dict]:
    """statvfs each of `_FILESYSTEM_TARGETS` that exists."""
    out: list[dict] = []
    for path in _FILESYSTEM_TARGETS:
        try:
            st = os.statvfs(path)
        except (OSError, FileNotFoundError):
            continue
        total = st.f_blocks * st.f_frsize
        free  = st.f_bavail * st.f_frsize
        used  = total - free
        out.append({
            "path": path, "total": total, "used": used, "free": free,
        })
    return out


def _spool_sizes() -> list[dict]:
    out: list[dict] = []
    for label, path in _SPOOL_TARGETS:
        if not path.exists():
            out.append({"label": label, "path": str(path),
                        "size": None, "skipped": 0,
                        "growth_per_day": None})
            continue
        size, skipped = _dir_size_safe(path)
        # Best-effort "average bytes / day" estimate: list date-tagged
        # daily files under this root, average their sizes.
        bpd = _avg_daily_bytes(path)
        out.append({"label": label, "path": str(path),
                    "size": size, "skipped": skipped,
                    "growth_per_day": bpd})
    return out


_DATE_RE = __import__("re").compile(r"(\d{4})-(\d{2})-(\d{2})")
_TODAY_ISO = _dt.date.today().isoformat()


def _avg_daily_bytes(path: Path) -> Optional[int]:
    """Average size in bytes of daily-rotated files under `path`.

    Heuristic: find files whose name contains a ``YYYY-MM-DD``
    sub-string and use them as per-day samples.  Skip the
    current-day file (partial).  None when we can't find at least
    two complete-day samples.

    Picks up mag-recorder's ``samples-<DATE>.jsonl`` directly.
    Codar's ``<station>/YYYY/MM/DD.jsonl`` doesn't match (no
    ``YYYY-MM-DD`` in any single filename), so codar reports
    "—" today — fine, a future revision can do a directory-path
    based match if it becomes valuable.
    """
    if not path.is_dir():
        return None
    candidates: list[int] = []
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for f in files:
            m = _DATE_RE.search(f)
            if not m:
                continue
            date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            if date_str == _TODAY_ISO:
                continue   # partial current-day file
            try:
                sz = (Path(root) / f).stat().st_size
            except OSError:
                continue
            candidates.append(sz)
    if len(candidates) < 2:
        return None
    return int(sum(candidates) / len(candidates))


def _gather() -> dict:
    """Snapshot everything in one worker call."""
    mem = _read_meminfo()
    load = _read_loadavg()
    return {
        "memory": {
            "total_kb":     mem.get("MemTotal", 0),
            "available_kb": mem.get("MemAvailable", 0),
            "free_kb":      mem.get("MemFree", 0),
            "buffers_kb":   mem.get("Buffers", 0),
            "cached_kb":    mem.get("Cached", 0),
            "swap_total_kb": mem.get("SwapTotal", 0),
            "swap_free_kb":  mem.get("SwapFree", 0),
        },
        "load":            load,
        "uptime_seconds":  _read_uptime_seconds(),
        "filesystems":     _filesystems(),
        "spool":           _spool_sizes(),
        "sqlite":          _sqlite_summary(),
        "now_iso":         _dt.datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------


class ResourcesScreen(Vertical):
    """System + sigmond storage summary."""

    DEFAULT_CSS = """
    ResourcesScreen {
        padding: 1;
    }
    ResourcesScreen .section-title {
        text-style: bold;
        margin-top: 1;
        margin-bottom: 0;
    }
    ResourcesScreen #r-system {
        color: $text-muted;
        margin-bottom: 1;
    }
    ResourcesScreen #r-status {
        margin-top: 1;
        color: $text-muted;
    }
    ResourcesScreen #r-buttons {
        height: 3;
        margin-top: 1;
    }
    ResourcesScreen #r-buttons Button {
        margin-right: 1;
    }
    """

    def compose(self):
        yield Static("System resources", classes="section-title")
        yield Static("[dim]loading…[/]", id="r-system", markup=True)

        yield Static("Filesystems", classes="section-title")
        fs = DataTable(id="r-fs", zebra_stripes=True)
        fs.add_columns("Mount", "Used", "Total", "%", "Free")
        yield fs

        yield Static("Sigmond data", classes="section-title")
        sp = DataTable(id="r-spool", zebra_stripes=True)
        sp.add_columns("Path", "Size", "Daily-avg",
                       "Days @ growth", "Notes")
        yield sp

        yield Static("SQLite sink (pending_uploads)",
                     classes="section-title")
        yield Static("", id="r-sqlite-files", markup=True)
        sq = DataTable(id="r-sqlite-tables", zebra_stripes=True)
        sq.add_columns("Database / table", "Rows",
                       "Oldest queued", "Newest queued", "Lag")
        yield sq

        with Horizontal(id="r-buttons"):
            yield Button("Refresh", id="r-refresh", variant="primary")
        yield Static("", id="r-status", markup=True)

    def on_mount(self) -> None:
        self._refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "r-refresh":
            self._refresh()

    def _refresh(self) -> None:
        self.query_one("#r-status", Static).update(
            "[dim]gathering…[/]")
        self.run_worker(_gather, thread=True, group="r-snapshot",
                        exclusive=True)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.state != WorkerState.SUCCESS:
            return
        if event.worker.group != "r-snapshot":
            return
        self._render_snapshot(event.worker.result or {})

    def _render_snapshot(self, snap: dict) -> None:
        if not snap:
            return

        # System summary line.
        mem = snap["memory"]
        total = mem["total_kb"] * 1024
        avail = mem["available_kb"] * 1024
        used = total - avail
        sw_total = mem["swap_total_kb"] * 1024
        sw_free  = mem["swap_free_kb"] * 1024
        sw_used  = sw_total - sw_free
        l1, l5, l15 = snap["load"]
        up_s = snap["uptime_seconds"]
        sys_line = (
            f"Memory: [bold]{_fmt_bytes(used)}[/] / "
            f"{_fmt_bytes(total)} used ({_fmt_pct(used, total)})  "
            f"available {_fmt_bytes(avail)}  •  "
            f"Swap: {_fmt_bytes(sw_used)} / {_fmt_bytes(sw_total)}\n"
            f"Load 1/5/15: [bold]{l1:.2f}[/] / {l5:.2f} / {l15:.2f}  "
            f"•  Uptime: {_fmt_age(up_s)}  •  "
            f"snapshot {snap.get('now_iso', '?')}"
        )
        self.query_one("#r-system", Static).update(sys_line)

        # Filesystems.
        fs_table = self.query_one("#r-fs", DataTable)
        fs_table.clear()
        for fs in snap["filesystems"]:
            fs_table.add_row(
                str(fs["path"]),
                _fmt_bytes(fs["used"]),
                _fmt_bytes(fs["total"]),
                _fmt_pct(fs["used"], fs["total"]),
                _fmt_bytes(fs["free"]),
            )

        # Spool dirs.
        sp_table = self.query_one("#r-spool", DataTable)
        sp_table.clear()
        for sp in snap["spool"]:
            if sp["size"] is None:
                sp_table.add_row(sp["path"], "[dim](absent)[/]",
                                 "—", "—", "")
                continue
            note = ""
            if sp["skipped"]:
                note = f"[yellow]{sp['skipped']} entries unreadable[/]"
            # Days estimate: based on the spool's daily-avg growth
            # against the FREE space on whichever mount the path
            # lives on.  None when growth is unknown.
            days = None
            if sp["growth_per_day"]:
                # Find the matching mountpoint from snap["filesystems"].
                p = Path(sp["path"])
                mount = None
                while True:
                    for fs in snap["filesystems"]:
                        if str(fs["path"]) == str(p):
                            mount = fs
                            break
                    if mount is not None:
                        break
                    if p.parent == p:
                        break
                    p = p.parent
                if mount and sp["growth_per_day"]:
                    days = mount["free"] / sp["growth_per_day"]
            sp_table.add_row(
                sp["path"],
                _fmt_bytes(sp["size"]),
                _fmt_bytes(sp["growth_per_day"]) + "/day"
                    if sp["growth_per_day"] else "—",
                _fmt_days(days),
                note,
            )

        # SQLite.
        sq_files = self.query_one("#r-sqlite-files", Static)
        sq_table = self.query_one("#r-sqlite-tables", DataTable)
        sq_table.clear()
        sql = snap["sqlite"]
        if "error" in sql and "sizes" not in sql:
            sq_files.update(f"[red]{sql['error']}[/]")
        else:
            sizes = sql.get("sizes", {})
            sq_files.update(
                f"db: [bold]{_fmt_bytes(sizes.get('sink.db', 0))}[/]  "
                f"•  WAL: {_fmt_bytes(sizes.get('sink.db-wal', 0))}  "
                f"•  SHM: {_fmt_bytes(sizes.get('sink.db-shm', 0))}  "
                f"•  ({SINK_DB})"
            )
            now = time.time()
            for db, table, n, oldest, newest in sql.get("queue", []):
                age_s = None
                try:
                    if newest:
                        age_s = now - _dt.datetime.fromisoformat(
                            newest.replace("Z", "+00:00")
                        ).timestamp()
                except (ValueError, TypeError):
                    age_s = None
                sq_table.add_row(
                    f"{db}.{table}",
                    f"{n:,}",
                    str(oldest or "—"),
                    str(newest or "—"),
                    _fmt_age(age_s),
                )

        self.query_one("#r-status", Static).update(
            f"[green]snapshot at {snap.get('now_iso', '?')}[/]"
        )

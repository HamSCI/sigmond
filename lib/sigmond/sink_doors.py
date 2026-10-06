"""Set aside data stored while the site sink switch read `off`.

In v3.69 the site sink switch's `off` rides on the legacy `[uploads] mode =
"discard"`, which acts only when hs-uploader ships.  GRAPE packages and
magnetometer zips built while it read `off` can therefore still ship once it
leaves `off` -- above all where their pipelines dropped out of the manifest
for missing PSWS ids, so nothing discarded them.  Before the written mode
leaves discard, sigmond moves them into a sibling directory OUTSIDE each
spool root (hs-uploader's file source searches the whole tree, hidden
directories included) and marks earlier GRAPE days packaged so the catch-up
sweep never rebuilds them.  The caller first asks `packaging_running()` and
refuses while a packaging unit runs, so sigmond never moves a package that
a unit still writes.  tasks/plan-sink-control.md §10.3 item 3.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Tuple

GRAPE_SPOOL = Path("/var/lib/timestd/upload")
MAG_SPOOL = Path("/var/lib/mag-recorder/upload")
PACKAGED_MARKER = ".upload_complete"     # hamsci-physics grape/spool.py, same name

# The units that write into those spools.  Both run Type=oneshot, so while
# one works systemd reports it `activating`, never `active`.
PACKAGING_UNITS = ("grape-daily.service", "mag-recorder-upload.service")
_RUNNING_STATES = ("active", "activating", "deactivating", "reloading")


class DoorError(RuntimeError):
    """A set-aside that refused, or stopped partway.  The caller leaves the
    site sink switch at `off`; anything moved before a stop stays in the
    held directory."""


@dataclass
class DoorReport:
    moved: List[Tuple[Path, Path]] = field(default_factory=list)
    marked: List[Path] = field(default_factory=list)


def packaging_running(units=PACKAGING_UNITS, *, run=subprocess.run) -> List[str]:
    """The packaging units systemd reports running right now.  A unit that
    systemd does not answer for counts as running; a host without systemctl
    runs none."""
    busy: List[str] = []
    for unit in units:
        try:
            r = run(["systemctl", "is-active", unit],
                    capture_output=True, text=True, timeout=10)
        except FileNotFoundError:
            return []
        except (OSError, subprocess.SubprocessError):
            busy.append(unit)
            continue
        if r.stdout.strip() in _RUNNING_STATES:
            busy.append(unit)
    return busy


def held_dir(spool: Path, stamp: str) -> Path:
    """`<spool>-held/<stamp>/`, a sibling of the spool root, outside it."""
    return spool.parent / f"{spool.name}-held" / stamp


def set_aside(spool: Path, pattern: str, *, dirs: bool, stamp: str) -> List[Tuple[Path, Path]]:
    """Move every outermost match of `pattern` under `spool` into
    `held_dir(spool, stamp)`, keeping its path relative to the spool.
    `dirs` picks directory matches (GRAPE OBS*) or file matches (*.zip).
    Refuses before moving anything when the spool and its parent sit on
    different filesystems, since the move must stay a rename."""
    if not spool.is_dir():
        return []
    if os.stat(spool).st_dev != os.stat(spool.parent).st_dev:
        raise DoorError(
            f"{spool} sits on a different filesystem from {spool.parent}, so "
            "sigmond cannot set its packages aside by rename.  Move "
            f"{spool} onto the same filesystem as {spool.parent}, or ask your "
            "fleet admin; the site sink switch stays off.")
    chosen: List[Path] = []
    for p in sorted(spool.rglob(pattern)):
        if p.is_dir() != dirs:
            continue
        if any(c in p.parents for c in chosen):
            continue
        chosen.append(p)
    dest_root = held_dir(spool, stamp)
    moved: List[Tuple[Path, Path]] = []
    for src in chosen:
        dst = dest_root / src.relative_to(spool)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(src, dst)
        except OSError as exc:
            raise DoorError(
                f"could not move {src} to {dst}: {exc}.  The site sink switch "
                f"stays off; whatever moved before this sits in {dest_root}.") from exc
        moved.append((src, dst))
    return moved


def mark_days_complete(spool: Path, today: date, *, days: int = 7,
                       chown=os.chown) -> List[Path]:
    """Write `<spool>/<YYYYMMDD>/.upload_complete` for each of the `days`
    days before `today`.  A day directory this creates, and every marker,
    takes the spool's owner: GRAPE's packager runs as that owner and must
    still write inside a day directory that root created."""
    if not spool.is_dir():
        return []
    st = spool.stat()
    marks: List[Path] = []
    for back in range(1, days + 1):
        day_dir = spool / (today - timedelta(days=back)).strftime("%Y%m%d")
        if not day_dir.exists():
            day_dir.mkdir()
            chown(day_dir, st.st_uid, st.st_gid)
        marker = day_dir / PACKAGED_MARKER
        if not marker.exists():
            marker.touch()
            chown(marker, st.st_uid, st.st_gid)
        marks.append(marker)
    return marks


def close_doors(now: datetime, *, grape: Path = GRAPE_SPOOL, mag: Path = MAG_SPOOL,
                chown=os.chown) -> DoorReport:
    """Everything §10.3 item 3 does before the written mode leaves discard.
    The caller checks `packaging_running()` first."""
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    report = DoorReport()
    report.moved += set_aside(grape, "OBS*", dirs=True, stamp=stamp)
    report.moved += set_aside(mag, "*.zip", dirs=False, stamp=stamp)
    report.marked = mark_days_complete(grape, now.date(), chown=chown)
    return report

"""Set aside data stored while the site sink switch read `off`.

In v3.69 the site sink switch's `off` rides on the legacy `[uploads] mode =
"discard"`, which acts only when hs-uploader ships.  GRAPE packages and
magnetometer zips built while it read `off` can therefore still ship once it
leaves `off` -- above all where their pipelines dropped out of the manifest
for missing PSWS ids, so nothing discarded them.  Before the written mode
leaves discard, sigmond moves every package older than yesterday (UTC) still
in each spool (those, and any unsent ones built before discard) into a sibling directory OUTSIDE each
spool root (hs-uploader's file source searches the whole tree, hidden
directories included) and marks GRAPE days from two days back packaged so the catch-up
sweep never rebuilds them.  The caller first asks `packaging_running()` and
refuses while a packaging unit runs or may run, so sigmond never moves a
package that a unit still writes.  A refusal moves nothing: `close_doors`
checks both spools before it renames anything.  A stop partway reports the
pairs it moved.  Today's packages, and yesterday's if not yet packed, stay
and ship, as INSTALL.md tells the operator.  tasks/plan-sink-control.md §10.3 item 3.
"""
from __future__ import annotations

import contextlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple

GRAPE_SPOOL = Path("/var/lib/timestd/upload")
MAG_SPOOL = Path("/var/lib/mag-recorder/upload")
PACKAGED_MARKER = ".upload_complete"     # hamsci-physics grape/spool.py, same name

# The units that write into those spools.  Both run Type=oneshot, so while
# one works systemd reports it `activating`, never `active`.  A unit counts
# idle only when systemd answers one of these; any other answer counts busy.
# A unit that does not exist answers `inactive`.
PACKAGING_UNITS = ("grape-daily.service", "mag-recorder-upload.service")
_IDLE_STATES = ("inactive", "failed")


_DAY_DIR = re.compile(r"^\d{8}$")                      # GRAPE spool/<YYYYMMDD>/
_NAME_DAY = re.compile(r"(?:^|\D)(\d{4})-?(\d{2})-?(\d{2})(?:\D|$)")


def package_day(path: Path, spool: Path):
    """The UTC day a spooled package belongs to, or None when its path does
    not say.  GRAPE packages sit under `<spool>/<YYYYMMDD>/` and are named
    `OBS<YYYY-MM-DD>T..`; magnetometer zips are named `mag_<YYYYMMDD>.zip`."""
    rel = path.relative_to(spool)
    for part in (*rel.parts[:-1], rel.parts[-1]):
        text = part if _DAY_DIR.match(part) else None
        m = _NAME_DAY.search(part) if text is None else None
        try:
            if text:
                return date(int(text[:4]), int(text[4:6]), int(text[6:]))
            if m:
                return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            continue
    return None


class DoorError(RuntimeError):
    """A set-aside that refused, or stopped partway.  The caller leaves the
    site sink switch at `off`.  `moved` lists the (source, destination)
    pairs that moved before the stop; they stay in the held directory."""

    def __init__(self, message: str, moved=()):
        super().__init__(message)
        self.moved: List[Tuple[Path, Path]] = list(moved)


@dataclass
class DoorReport:
    moved: List[Tuple[Path, Path]] = field(default_factory=list)
    marked: List[Path] = field(default_factory=list)


def packaging_running(units=PACKAGING_UNITS, *, run=subprocess.run) -> List[str]:
    """The packaging units that may be running right now.  A unit counts idle
    only when systemd answers `inactive` or `failed`.  Any other state, an
    empty answer, or no answer at all counts busy.  A host without systemctl
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
        if (r.stdout or "").strip() not in _IDLE_STATES:
            busy.append(unit)
    return busy


def held_dir(spool: Path, stamp: str) -> Path:
    """`<spool>-held/<stamp>/`, a sibling of the spool root, outside it."""
    return spool.parent / f"{spool.name}-held" / stamp


def _check_same_fs(spool: Path) -> None:
    """Refuse when the spool and its parent sit on different filesystems,
    since the move into `held_dir` must stay a rename."""
    if os.stat(spool).st_dev != os.stat(spool.parent).st_dev:
        raise DoorError(
            f"{spool} sits on a different filesystem from {spool.parent}, so "
            "sigmond cannot set its packages aside by rename.  Move "
            f"{spool} onto the same filesystem as {spool.parent}, or ask your "
            "fleet admin; the site sink switch stays off.")


def set_aside(spool: Path, pattern: str, *, match_dirs: bool,
              stamp: str, keep_from: date = None) -> List[Tuple[Path, Path]]:
    """Move every outermost match of `pattern` under `spool` into
    `held_dir(spool, stamp)`, keeping its path relative to the spool.
    Files always match.  Directories match too when `match_dirs` is true
    (GRAPE OBS*), the rule of hs-uploader's file source.  Refuses before
    moving anything when the spool and its parent sit on different
    filesystems.  A source that vanished after the scan, such as a
    magnetometer zip the daemon deleted on ack, needs no setting aside and
    is skipped.  A package whose UTC day is `keep_from` or later stays in
    the spool: today's packages, and yesterday's if not yet packed, go out
    when uploads turn on.  A package whose day the path does not show moves,
    so the discard guarantee fails closed.  Any other rename failure stops the move; the error lists
    the pairs moved so far."""
    if not spool.is_dir():
        return []
    _check_same_fs(spool)
    chosen: List[Path] = []
    for p in sorted(spool.rglob(pattern)):
        if not (p.is_file() or (match_dirs and p.is_dir())):
            continue
        if any(c in p.parents for c in chosen):
            continue
        if keep_from is not None:
            day = package_day(p, spool)
            if day is not None and day >= keep_from:
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
            if isinstance(exc, FileNotFoundError) and not os.path.lexists(src):
                continue
            raise DoorError(
                f"could not move {src} to {dst}: {exc}.  The site sink switch "
                f"stays off; whatever moved before this sits in {dest_root}.",
                moved) from exc
        moved.append((src, dst))
    return moved


def mark_days_complete(spool: Path, today: date, *, days: int = 7,
                       chown=os.chown, chmod=os.chmod) -> List[Path]:
    """Write `<spool>/<YYYYMMDD>/.upload_complete` for `days` days, from two
    days before `today` back.  Today and yesterday stay open: their packages
    still ship.  A day directory this creates takes the spool's
    owner and mode, and every marker takes the spool's owner: GRAPE's
    packager runs as that owner and must still write inside a day directory
    that root created.  A day directory this call created, and could not
    hand over, goes away again.  An existing day directory stays untouched.
    A failure raises DoorError."""
    if not spool.is_dir():
        return []
    st = spool.stat()
    mode = stat.S_IMODE(st.st_mode)
    marks: List[Path] = []
    for back in range(2, days + 2):
        day_dir = spool / (today - timedelta(days=back)).strftime("%Y%m%d")
        unfinished = False                  # made here, not yet handed over
        try:
            if not day_dir.exists():
                day_dir.mkdir()
                unfinished = True
                chown(day_dir, st.st_uid, st.st_gid)
                chmod(day_dir, mode)
                unfinished = False
            marker = day_dir / PACKAGED_MARKER
            if not marker.exists():
                marker.touch()
                try:
                    chown(marker, st.st_uid, st.st_gid)
                except OSError:
                    with contextlib.suppress(OSError):
                        marker.unlink()     # a rerun retries it
                    raise
        except OSError as exc:
            left = ""
            if unfinished:
                try:
                    day_dir.rmdir()
                except OSError:
                    left = (f"  {day_dir} still belongs to root; remove it "
                            "by hand, or timestd cannot write there.")
            raise DoorError(
                f"could not mark {day_dir} packaged: {exc}.{left}  The site "
                "sink switch stays off; whatever moved before this sits in "
                "the held directory.") from exc
        marks.append(marker)
    return marks


def close_doors(now: datetime, *, grape: Path = GRAPE_SPOOL, mag: Path = MAG_SPOOL,
                chown=os.chown, chmod=os.chmod) -> DoorReport:
    """Everything §10.3 item 3 does before the written mode leaves discard.
    The caller checks `packaging_running()` first.  `now` must carry a time
    zone; the stamp and the marked days come out in UTC.  A refusal moves
    nothing.  A DoorError after the first rename lists every pair moved."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("close_doors needs a time with a time zone; "
                         "it names the stamp and the days in UTC")
    now = now.astimezone(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    for spool in (grape, mag):              # refuse before any rename
        if spool.is_dir():
            _check_same_fs(spool)
    report = DoorReport()
    yesterday = now.date() - timedelta(days=1)
    for spool, pattern, match_dirs in ((grape, "OBS*", True), (mag, "*.zip", False)):
        try:
            report.moved += set_aside(spool, pattern, match_dirs=match_dirs, stamp=stamp,
                                      keep_from=yesterday)
        except DoorError as exc:
            raise DoorError(str(exc), report.moved + exc.moved) from exc
    try:
        report.marked = mark_days_complete(grape, now.date(), chown=chown, chmod=chmod)
    except DoorError as exc:
        raise DoorError(str(exc), report.moved) from exc
    return report

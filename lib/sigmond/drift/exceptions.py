"""Drift exceptions: intended differences, temporary tests, restore reminders.

An exception covers one drift item by key (``kind:subject``).  While it
lasts, the item moves from ``items`` to ``accepted`` and no longer alarms.

File: /etc/sigmond/drift-exceptions.toml, an array of tables::

    [[exception]]
    key = "unit:hf-timestd-fusion.service"
    reason = "testing the Rust fusion"
    by = "mjh"
    kind = "test"            # "test" or "standing"
    start = 2026-11-18       # TOML dates, UTC calendar days
    until = 2026-11-25       # last day the exception holds (inclusive)

A ``test`` defaults to 7 days, at most 30.  A standing exception defaults
to 30 days, at most 180.  A test raises a reminder from the day before
``until`` and becomes a fault after ``until``.  A standing exception that
has expired leaves the item at its own severity.  An exception whose key
matches no current item stays in the file and is reported nowhere.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .model import Report

log = logging.getLogger("sigmond.drift.exceptions")

DEFAULT_PATH = Path("/etc/sigmond/drift-exceptions.toml")
DEFAULT_DAYS = {"test": 7, "standing": 30}
MAX_DAYS = {"test": 30, "standing": 180}


@dataclass
class Exception_:
    key: str
    reason: str
    by: str
    kind: str  # "test" | "standing"
    start: date
    until: date


def _today(now: datetime) -> date:
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    return now.date()


def _as_date(v) -> date:
    if isinstance(v, datetime) or not isinstance(v, date):
        raise ValueError(f"not a date: {v!r}")
    return v


def load(path: Path, now: datetime) -> list:
    """Read the file; skip bad entries (one warning each); clamp to the cap."""
    path = Path(path)
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("drift exceptions: cannot read %s: %s", path, exc)
        return []
    raw = data.get("exception", [])
    if not isinstance(raw, list):
        log.warning("drift exceptions: 'exception' is not an array of tables")
        return []
    out: list = []
    for n, ent in enumerate(raw, 1):
        try:
            if not isinstance(ent, dict):
                raise ValueError("not a table")
            kind = ent.get("kind")
            if kind not in DEFAULT_DAYS:
                raise ValueError(f"unknown kind {kind!r}")
            fields = {}
            for f in ("key", "reason", "by"):
                v = ent.get(f)
                if not isinstance(v, str) or not v.strip():
                    raise ValueError(f"missing {f}")
                fields[f] = v
            start = _as_date(ent.get("start"))
            until = _as_date(ent.get("until"))
            if until < start:
                raise ValueError("until before start")
        except ValueError as exc:
            log.warning("drift exceptions: skipping entry %d: %s", n, exc)
            continue
        cap = start + timedelta(days=MAX_DAYS[kind])
        if until > cap:
            log.warning("drift exceptions: %s: until %s beyond the %d-day "
                        "cap; clamped to %s", fields["key"], until,
                        MAX_DAYS[kind], cap)
            until = cap
        out.append(Exception_(kind=kind, start=start, until=until, **fields))
    return out


def _q(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{s}"'


def _write(path: Path, excs: list) -> None:
    lines = ["# sigmond drift exceptions; edit with: smd drift --accept",
             ""]
    for e in excs:
        lines += ["[[exception]]",
                  f"key = {_q(e.key)}",
                  f"reason = {_q(e.reason)}",
                  f"by = {_q(e.by)}",
                  f"kind = {_q(e.kind)}",
                  f"start = {e.start.isoformat()}",
                  f"until = {e.until.isoformat()}",
                  ""]
    text = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".drift-exc-")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def accept(path: Path, key: str, *, reason: str, by: str, test: bool,
           until: Optional[date], now: datetime) -> Exception_:
    """Record an exception for ``key``, replacing any earlier one."""
    path = Path(path)
    kind = "test" if test else "standing"
    start = _today(now)
    cap = start + timedelta(days=MAX_DAYS[kind])
    if until is None:
        until = start + timedelta(days=DEFAULT_DAYS[kind])
    elif until < start:
        raise ValueError(f"until {until} is before today {start}")
    elif until > cap:
        log.warning("drift exceptions: %s: until %s beyond the %d-day cap; "
                    "clamped to %s", key, until, MAX_DAYS[kind], cap)
        until = cap
    exc = Exception_(key=key, reason=reason, by=by, kind=kind,
                     start=start, until=until)
    kept = [e for e in load(path, now) if e.key != key]
    kept.append(exc)
    _write(path, kept)
    return exc


def apply(report: Report, excs: list, now: datetime) -> Report:
    """Return a new Report with exceptions applied; the input is unchanged."""
    today = _today(now)
    by_key = {e.key: e for e in excs}  # a later entry wins
    items, accepted, reminders = [], list(report.accepted), \
        list(report.reminders)
    for it in report.items:
        e = by_key.get(it.key)
        if e is None:
            items.append(it)
            continue
        if today <= e.until:
            days_left = (e.until - today).days
            accepted.append({
                "key": it.key, "reason": e.reason, "by": e.by,
                "kind": e.kind, "until": e.until.isoformat(),
                "days_left": days_left, "severity": it.severity,
                "fix": it.fix})
            if e.kind == "test" and today >= e.until - timedelta(days=1):
                reminders.append({
                    "station": report.station, "key": it.key, "by": e.by,
                    "reason": e.reason, "until": e.until.isoformat(),
                    "fix": it.fix})
        elif e.kind == "test":
            items.append(dataclasses.replace(
                it, severity="fault",
                detail=f"temporary test ended {e.until.isoformat()}; "
                       f"restore baseline: {it.fix}"))
        else:
            items.append(it)
    return dataclasses.replace(report, items=items, accepted=accepted,
                               reminders=reminders)

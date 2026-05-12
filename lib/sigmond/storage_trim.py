"""SQLite sink janitor.

The sink (``/var/lib/sigmond/sink.db``) is a FIFO queue: producers
append rows; hs-uploader drains them and ``commit()`` deletes acked
rows.  If a producer has no consumer wired (currently hfdl.spots and
timestd.events on bee1 — no external upload destination is configured
for them), nothing ever deletes their rows and the queue grows
unbounded.

This module provides a TTL-based DELETE: rows older than a given
``max_age`` are removed regardless of which (target_db, target_table)
they belong to.  Acked rows would already be gone via the source's
``commit()``; unacked rows that have aged out are presumed
unreachable or unwanted.

Same shape as ``sigmond.storage_migrate``:

* ``plan_trim()`` is pure — it inspects the sink and returns a
  ``TrimPlan`` describing what would be deleted.  Safe to call as
  any user that can read sink.db.
* ``execute_trim()`` does the destructive DELETE, requires
  ``plan.confirmed = True`` (set only after ``--yes``).
* Pluggable ``opener`` callable so tests can substitute a fake
  connection without touching the filesystem.

The CLI verb (``smd storage trim``) is in ``bin/smd``; this module
stays library-only so the same logic is unit-testable + reusable.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Tuple


DEFAULT_DB_PATH = "/var/lib/sigmond/sink.db"


class NotConfirmed(Exception):
    """Raised when execute_trim is called without plan.confirmed=True."""


@dataclass
class TrimPlan:
    """Concrete description of what `execute_trim` would delete."""

    db_path: str
    cutoff_iso: str
    # [(target_db, target_table, row_count), ...] for rows older than cutoff.
    # Grouped + ordered by (target_db, target_table) for stable display.
    rows_per_target: List[Tuple[str, str, int]] = field(default_factory=list)
    confirmed: bool = False

    @property
    def total_rows(self) -> int:
        return sum(n for _, _, n in self.rows_per_target)

    @property
    def is_empty(self) -> bool:
        return self.total_rows == 0


@dataclass
class TrimReport:
    rows_deleted: int = 0
    errors: List[str] = field(default_factory=list)


Opener = Callable[[str], sqlite3.Connection]


def _default_opener(path: str) -> sqlite3.Connection:
    return sqlite3.connect(path, timeout=30.0)


def plan_trim(
    db_path: str,
    max_age_seconds: float,
    *,
    now_fn: Optional[Callable[[], datetime]] = None,
    opener: Optional[Opener] = None,
) -> TrimPlan:
    """Inspect `db_path` and report rows with `queued_at < now - max_age`.

    Empty plan when:
    - the sink db doesn't exist (no producer has flushed)
    - the pending_uploads table doesn't exist (same — first-flush race)
    - the sink db is unreadable to this user
    - no rows match the cutoff

    None of these are errors at the planning layer; a `TrimPlan` with
    no rows means "nothing to do" and `execute_trim` becomes a no-op.
    """
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    opener = opener or _default_opener
    cutoff_dt = now_fn() - timedelta(seconds=max_age_seconds)
    cutoff_iso = cutoff_dt.isoformat()
    plan = TrimPlan(db_path=db_path, cutoff_iso=cutoff_iso)
    try:
        conn = opener(db_path)
    except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError):
        return plan
    try:
        cur = conn.execute(
            "SELECT target_db, target_table, COUNT(*) "
            "FROM pending_uploads "
            "WHERE queued_at < ? "
            "GROUP BY target_db, target_table "
            "ORDER BY target_db, target_table",
            (cutoff_iso,),
        )
        plan.rows_per_target = [
            (str(db), str(tbl), int(n)) for db, tbl, n in cur.fetchall()
        ]
    except sqlite3.OperationalError:
        # pending_uploads doesn't exist yet — first-flush race; treat
        # as empty plan, not an error.
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return plan


def execute_trim(
    plan: TrimPlan,
    *,
    opener: Optional[Opener] = None,
) -> TrimReport:
    """Apply the plan: DELETE rows with `queued_at < plan.cutoff_iso`.

    Requires `plan.confirmed = True`; raise `NotConfirmed` otherwise so
    a forgotten `--yes` doesn't silently destroy data.  Idempotent: a
    second call with the same plan deletes 0 rows.

    Failures (sink unreadable, transient lock, etc.) are recorded on
    the report rather than raised — the caller can decide whether a
    partial trim is OK.
    """
    if not plan.confirmed:
        raise NotConfirmed(
            "execute_trim refused: plan.confirmed=False. Set "
            "plan.confirmed=True only after operator approval (smd "
            "storage trim requires --yes)."
        )
    report = TrimReport()
    if plan.is_empty:
        return report
    opener = opener or _default_opener
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = opener(plan.db_path)
        with conn:
            cur = conn.execute(
                "DELETE FROM pending_uploads WHERE queued_at < ?",
                (plan.cutoff_iso,),
            )
            report.rows_deleted = int(cur.rowcount)
    except Exception as exc:
        report.errors.append(f"DELETE on {plan.db_path}: {exc}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return report


def parse_duration(spec: str) -> float:
    """Parse `30s`, `5m`, `2h`, `7d` (or a bare number = seconds).

    Returns seconds as a float.  Raises ValueError on a malformed
    spec — caller (CLI verb) should surface that to the operator.
    """
    s = spec.strip().lower()
    if not s:
        raise ValueError("empty duration")
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if s[-1] in units:
        head, suffix = s[:-1], s[-1]
        try:
            value = float(head)
        except ValueError as e:
            raise ValueError(f"invalid number in duration {spec!r}: {e}")
        if value < 0:
            raise ValueError(f"negative duration not allowed: {spec!r}")
        return value * units[suffix]
    # Bare number = seconds.
    try:
        value = float(s)
    except ValueError as e:
        raise ValueError(f"invalid duration {spec!r}: {e}")
    if value < 0:
        raise ValueError(f"negative duration not allowed: {spec!r}")
    return value

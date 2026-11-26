"""Client kit for the contract v0.9 storage verbs (CLIENT-CONTRACT.md 17.7).

A client writes one function that lists its files with their classes.
This module does the rest: it sums holdings, picks what to trim, and
wires `storage holdings|trim|final` onto the client's argparse.

Clients load sigmond lazily with a fallback, so this module imports only
the standard library and `sigmond.storage.model.CLASSES`.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from sigmond.storage.model import CLASSES

DAY = 86400.0
# Trim order: sent first, then derived, then raw.  Pinned never goes.
_TRIM_ORDER = tuple(c for c in ("sent", "derived", "raw") if c in CLASSES)


@dataclass(frozen=True)
class Item:
    path: str
    bytes: int
    cls: str
    mtime: float


def holdings(items: Sequence[Item]) -> dict:
    out: dict = {}
    for it in items:
        out[it.cls] = out.get(it.cls, 0) + it.bytes
    return out


def written_per_day(items: Sequence[Item], now: float) -> int:
    """Bytes of items modified within the last 24 h.

    A ring buffer at steady state still writes, so this counts what the
    client wrote, not what it holds.
    """
    return sum(it.bytes for it in items if now - DAY < it.mtime <= now + DAY)


def choose_trim(items: Sequence[Item], fit_bytes: int, floor_bytes: int,
                *, final: bool = False, classes: Optional[Sequence[str]] = None
                ) -> list:
    remaining = sum(it.bytes for it in items)
    chosen: list = []
    order = [c for c in _TRIM_ORDER if classes is None or c in classes]
    for cls in order:
        for it in sorted((i for i in items if i.cls == cls), key=lambda i: i.mtime):
            if remaining <= fit_bytes:
                return chosen
            if not final and remaining - it.bytes < floor_bytes:
                return chosen
            chosen.append(it)
            remaining -= it.bytes
    return chosen


def _answer(items: Sequence[Item], refused: bool = False, reason: str = "") -> dict:
    return {"held_bytes": sum(i.bytes for i in items),
            "bytes_by_class": holdings(items),
            "refused": refused, "reason": reason}


def _delete_all(chosen, delete, items):
    """Delete chosen items; return (items still held, error notes)."""
    gone, errors = set(), []
    for it in chosen:
        try:
            delete(it)
            gone.add(it.path)
        except Exception as exc:  # a failed delete leaves the item held
            errors.append(f"{it.path}: {exc}")
    return [i for i in items if i.path not in gone], errors


def _emit(obj: dict) -> int:
    print(json.dumps(obj))
    return 0


def add_storage_verbs(subparsers, *, list_items: Callable[[], list],
                      delete: Callable[[Item], None], floor_bytes: int,
                      final_policy: str,
                      apply_policy: Optional[Callable[[str, int], dict]] = None,
                      expire: Optional[Callable[[str, int, int], dict]] = None
                      ) -> None:
    verbs = _storage_verbs(subparsers)

    def do_holdings(args) -> int:
        items = list_items()
        return _emit({"bytes_by_class": holdings(items), "estimated": False,
                      "bytes_written_per_day": written_per_day(items, time.time())})

    def do_trim(args) -> int:
        items = list_items()
        if args.fit_bytes < floor_bytes:
            note = (f"fit {args.fit_bytes} B lies below the floor "
                    f"{floor_bytes} B; keeping the floor")
            refused = True
        else:
            note, refused = "", False
        chosen = choose_trim(items, args.fit_bytes, floor_bytes)
        left, errors = _delete_all(chosen, delete, items)
        reason = "; ".join(filter(None, [note] + (["could not delete " + ", ".join(errors)] if errors else [])))
        return _emit(_answer(left, refused, reason))

    def do_final(args) -> int:
        policy = args.policy or final_policy
        if apply_policy is not None:
            return _emit(apply_policy(policy, args.fit_bytes))
        items = list_items()
        kind, _, cls = policy.partition(":")
        if kind != "give-up-oldest" or cls not in CLASSES or cls == "pinned":
            return _emit(_answer(items, True, f"policy {policy} is not handled by this client"))
        chosen = choose_trim(items, args.fit_bytes, floor_bytes, final=True, classes=[cls])
        left, errors = _delete_all(chosen, delete, items)
        reason = ("could not delete " + ", ".join(errors)) if errors else ""
        return _emit(_answer(left, False, reason))

    p = verbs.add_parser("holdings")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=do_holdings)
    p = verbs.add_parser("trim")
    p.add_argument("--fit-bytes", type=int, required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=do_trim)
    p = verbs.add_parser("final")
    p.add_argument("--fit-bytes", type=int, required=True)
    p.add_argument("--policy", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=do_final)
    if expire is not None:
        add_expire_verb(subparsers, expire=expire)


def _storage_verbs(subparsers):
    """The `storage` verb action, created on first use and reused after."""
    storage = subparsers.choices.get("storage") if subparsers.choices else None
    if storage is None:
        storage = subparsers.add_parser("storage", help="storage capacity verbs (contract 17.7)")
        storage._sigmond_verbs = storage.add_subparsers(dest="storage_cmd")
    return storage._sigmond_verbs


def add_expire_verb(subparsers, *, expire: Callable[[str, int, int], dict]) -> None:
    """Add `storage expire --target T --days N [--max-seconds S] --json`."""
    verbs = _storage_verbs(subparsers)

    def do_expire(args) -> int:
        return _emit(expire(args.target, args.days, args.max_seconds))

    p = verbs.add_parser("expire")
    p.add_argument("--target", required=True)
    p.add_argument("--days", type=int, required=True)
    p.add_argument("--max-seconds", type=int, default=600)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=do_expire)


# ---- row pruning ----------------------------------------------------------

_IDENT = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class PruneResult:
    deleted: int
    complete: bool
    reason: str


def cutoff_iso(now: float, days: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - days * 86400))


_DROPPED_UID: Optional[int] = None


def drop_to_owner(path: str):
    """When root, become the owner of `path` so no root-owned -wal/-shm appears.

    The drop outlasts the call: it covers the rest of the process.  A later call
    that names a different owner raises.  Refuses a symlink, and refuses a file
    that root owns (never stays root).  Returns (st_dev, st_ino) of the file it
    checked, or None when nothing was checked (not root, no earlier drop).
    """
    global _DROPPED_UID
    if os.geteuid() != 0 and _DROPPED_UID is None:
        return None
    if stat.S_ISLNK(os.lstat(path).st_mode):
        raise ValueError(f"refusing to drop privileges to a symlink: {path}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if _DROPPED_UID is not None and st.st_uid != _DROPPED_UID:
        raise ValueError(f"{path} belongs to uid {st.st_uid}, but this process "
                         f"already dropped to uid {_DROPPED_UID}")
    if os.geteuid() == 0:
        if st.st_uid == 0:
            raise ValueError(f"refusing to run as root on a root-owned file: {path}")
        os.setgroups([])
        os.setgid(st.st_gid)
        os.setuid(st.st_uid)
        _DROPPED_UID = st.st_uid
    return (st.st_dev, st.st_ino)


def _open_owned(path: str, timeout: float):
    """drop_to_owner, then connect only if the path still names the file checked."""
    ident = drop_to_owner(path)
    if ident is not None:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode) or (st.st_dev, st.st_ino) != ident:
            raise ValueError(f"{path} changed after the ownership check")
    return sqlite3.connect(path, timeout=timeout, isolation_level=None)


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def _is_locked(exc: sqlite3.OperationalError) -> bool:
    m = str(exc).lower()
    return "locked" in m or "busy" in m


def prune_rows(db_path: str, table: str, ts_column: str, cutoff: str, *,
               deadline: float, batch_rows: int = 2000, pause_s: float = 0.05,
               busy_s: float = 0.25, clock=time.monotonic,
               sleep=time.sleep) -> PruneResult:
    """Delete rows with ts < cutoff in short batches that each commit on their own.

    The cut comes from a binary search over rowid, so rows written out of time
    order are safe: a new row at a low rowid survives (the DELETE keeps the
    timestamp predicate), and an old row past the cut waits for a later pass.
    """
    t, c = _ident(table), _ident(ts_column)
    con = _open_owned(db_path, busy_s)
    try:
        lo, hi = con.execute(f"SELECT min(rowid), max(rowid) FROM {t}").fetchone()
        if lo is None:
            return PruneResult(0, True, "")
        nxt = (f"SELECT {c}, typeof({c}) FROM {t} "
               f"WHERE rowid >= ? AND {c} IS NOT NULL ORDER BY rowid LIMIT 1")

        def probe(r):
            row = con.execute(nxt, (r,)).fetchone()
            if row is not None and row[1] != "text":
                raise ValueError(f"{table}.{ts_column} holds {row[1]} values, not "
                                 f"ISO text; refusing to prune")
            return None if row is None else row[0]

        probe(lo)
        last = con.execute(f"SELECT typeof({c}) FROM {t} WHERE {c} IS NOT NULL "
                           f"ORDER BY rowid DESC LIMIT 1").fetchone()
        if last is not None and last[0] != "text":
            raise ValueError(f"{table}.{ts_column} holds {last[0]} values, not "
                             f"ISO text; refusing to prune")
        # smallest r in [lo, hi+1] whose first non-NULL ts at rowid >= r is >= cutoff
        a, b = lo, hi + 1
        while a < b:
            mid = (a + b) // 2
            ts = probe(mid)
            if ts is None or ts >= cutoff:
                b = mid
            else:
                a = mid + 1
        boundary = a
        deleted, start, batches = 0, lo, 0
        while start < boundary:
            if clock() >= deadline:
                return PruneResult(deleted, False, "deadline")
            end = min(start + batch_rows, boundary)
            for attempt in range(6):
                try:
                    cur = con.execute(
                        f"DELETE FROM {t} WHERE rowid >= ? AND rowid < ? AND {c} < ?",
                        (start, end, cutoff))
                    deleted += cur.rowcount
                    break
                except sqlite3.OperationalError as exc:
                    if not _is_locked(exc):
                        raise
                    if attempt == 5:
                        return PruneResult(deleted, False, "database locked")
                    sleep(pause_s)
            start = end
            batches += 1
            sleep(pause_s)
            if batches % 50 == 0:
                try:
                    con.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
                except sqlite3.OperationalError:
                    pass
        return PruneResult(deleted, True, "")
    finally:
        con.close()


def incremental_vacuum(db_path: str, *, deadline: float, pages_per_step: int = 2048,
                       clock=time.monotonic) -> int:
    """Return freed pages to the filesystem; 0 unless the file is in incremental mode."""
    con = _open_owned(db_path, 0.25)
    try:
        if con.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
            return 0
        before = con.execute("PRAGMA freelist_count").fetchone()[0]
        while clock() < deadline:
            if con.execute("PRAGMA freelist_count").fetchone()[0] == 0:
                break
            con.execute(f"PRAGMA incremental_vacuum({int(pages_per_step)})").fetchall()
        return before - con.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        con.close()

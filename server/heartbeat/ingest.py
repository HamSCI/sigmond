#!/usr/bin/env python3
"""Ingest station heartbeats from the chrooted SFTP drop into sqlite.

RUNS ON THE CENTRAL BOARD (wd30), NOT ON A STATION.  This file is rsynced
to /opt/hamsci-fleetboard/ next to a verbatim copy of
``heartbeat_schema.py``; it is NOT a sigmond install and must never grow
an import from ``sigmond``.  STDLIB ONLY — wd30 runs a plain
``/usr/bin/python3`` with no venv.

The one idea worth carrying out of this file
--------------------------------------------
``received_at`` is the FILE MTIME — the server's own clock at the moment
sftp renamed the completed upload into place.  The station's
self-reported ``emitted_at`` is stored for forensics and is NEVER used
for availability.  A station that has stopped cannot report that it
stopped, so every self-reported timestamp is, at the moment it matters
most, exactly as stale as the station.  Availability is derived
downstream (fleetboard.derive_status) purely from these arrival times.

Safe to run every minute from a systemd timer: it is idempotent, it takes
no lock, and a file it cannot make sense of is quarantined with a row
explaining why rather than deleted or silently skipped.
"""

import argparse
import errno
import json
import os
import shutil
import sqlite3
import sys

import heartbeat_schema

DEFAULT_DROP_DIR = "/srv/hamsci-hb/incoming"
DEFAULT_DB = "/var/lib/hamsci-fleetboard/heartbeats.db"

#: Rejected files are kept, never deleted: the quarantine IS the evidence
#: for "this station is shipping something we cannot read", which is a
#: fleet defect in its own right and looks identical to silence on the
#: board unless the file survives to be looked at.
QUARANTINE_SUBDIR = "quarantine"

SCHEMA = """
CREATE TABLE IF NOT EXISTS heartbeats (
    id             INTEGER PRIMARY KEY,
    station        TEXT    NOT NULL,
    received_at    REAL    NOT NULL,
    emitted_at     TEXT,
    schema_version INTEGER,
    rollup_verdict TEXT,
    payload        TEXT    NOT NULL,
    -- One arrival per (station, mtime).  Without this, a manual run
    -- racing the timer double-inserts the same file: both read the drop,
    -- both insert, the loser's unlink fails harmlessly, and the table
    -- quietly disagrees with reality about how often a station reported.
    -- CREATE TABLE IF NOT EXISTS will NOT retrofit this onto an existing
    -- database — which is free to ignore today because nothing is
    -- deployed yet. Once wd30 has a real heartbeats.db, changing this
    -- line means writing a migration.
    UNIQUE (station, received_at)
);
-- Every board query is "the newest arrival for this station"; this index
-- is what keeps that O(log n) as the table grows without bound.
CREATE INDEX IF NOT EXISTS heartbeats_station_received
    ON heartbeats (station, received_at);

CREATE TABLE IF NOT EXISTS rejects (
    id            INTEGER PRIMARY KEY,
    station_guess TEXT,
    received_at   REAL,
    reason        TEXT,
    filename      TEXT
);
CREATE INDEX IF NOT EXISTS rejects_received ON rejects (received_at);
"""


# ---------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------

def open_db(path):
    """Open (creating if needed) the heartbeat database at ``path``."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def record_heartbeat(conn, station, received_at, envelope):
    """Insert one accepted heartbeat.  Caller commits.

    Returns True if a row was written, False if this exact arrival was
    already stored (a concurrent second ingest pass).  OR IGNORE rather
    than OR REPLACE: the first writer's row is the one whose file it
    unlinked, and rewriting it would gain nothing.
    """
    rollup = envelope.get("rollup")
    verdict = rollup.get("verdict") if isinstance(rollup, dict) else None
    cur = conn.execute(
        "INSERT OR IGNORE INTO heartbeats (station, received_at, emitted_at,"
        " schema_version, rollup_verdict, payload) VALUES (?, ?, ?, ?, ?, ?)",
        (station, float(received_at), envelope.get("emitted_at"),
         envelope.get("schema_version"), verdict,
         json.dumps(envelope, sort_keys=True)))
    return cur.rowcount > 0


def record_reject(conn, station_guess, received_at, reason, filename):
    """Insert one rejection.  Caller commits."""
    conn.execute(
        "INSERT INTO rejects (station_guess, received_at, reason, filename)"
        " VALUES (?, ?, ?, ?)",
        (station_guess, received_at, reason, filename))


# ---------------------------------------------------------------------------
# filesystem helpers
# ---------------------------------------------------------------------------

def station_guess(filename):
    """Best-effort station name from a drop filename.

    Producer filenames are ``<station>_<YYYYmmddTHHMMSSZ>.json`` and a
    station name may itself contain ``-`` (``AC0G-B4``) — so the LAST
    underscore is the separator, not the first.  This is only ever used
    to label a reject row: a file we could not parse cannot be trusted
    to name itself from the inside.
    """
    base = os.path.basename(filename or "")
    if base.endswith(".json"):
        base = base[:-len(".json")]
    head = base.rsplit("_", 1)[0] if "_" in base else ""
    return head.strip() or "unknown"


def read_bytes(path):
    """Read a file whole.  Split out so tests can simulate a lost race."""
    with open(path, "rb") as handle:
        return handle.read()


def quarantine_path(quarantine_dir, filename):
    """A free path under ``quarantine_dir`` — never clobber older evidence."""
    candidate = os.path.join(quarantine_dir, filename)
    if not os.path.exists(candidate):
        return candidate
    for n in range(1, 1000):
        alt = f"{candidate}.{n}"
        if not os.path.exists(alt):
            return alt
    raise OSError(errno.EEXIST, "quarantine name space exhausted", candidate)


def _is_candidate(name):
    """True for files the drop is allowed to hand us.

    ``.part`` is the in-flight name used by the station transport
    (put to ``<name>.part``, then rename) — reading one would read a
    torn file, so the ``.json`` suffix test IS the completeness
    guarantee.  Dotfiles are never ours.
    """
    return name.endswith(".json") and not name.startswith(".")


# ---------------------------------------------------------------------------
# the pass
# ---------------------------------------------------------------------------

def ingest_once(drop_dir, db_path=None, quarantine_dir=None, conn=None):
    """One scan of ``drop_dir``.  Returns a counts dict.

    Counts: ``ingested``, ``quarantined``, ``skipped`` (files that
    vanished under us — a lost race with sftp, not a fault), ``errors``
    (anything unexpected: fs errors, a locked database).  A non-zero
    ``errors`` is what makes the process exit 1.
    """
    result = {"ingested": 0, "quarantined": 0, "skipped": 0,
              "duplicates": 0, "errors": 0}
    if quarantine_dir is None:
        quarantine_dir = os.path.join(os.path.dirname(
            os.path.abspath(drop_dir)), QUARANTINE_SUBDIR)

    owned = conn is None
    if owned:
        conn = open_db(db_path or DEFAULT_DB)
    try:
        try:
            names = sorted(os.listdir(drop_dir))
        except FileNotFoundError:
            # Not an error: the timer may well fire before the operator
            # has run setup, and a missing drop is loud in the units, not
            # here.
            return result
        for name in names:
            if not _is_candidate(name):
                continue
            path = os.path.join(drop_dir, name)
            try:
                _ingest_file(conn, path, name, quarantine_dir, result)
            except FileNotFoundError:
                result["skipped"] += 1
            except (OSError, sqlite3.Error) as exc:
                result["errors"] += 1
                print(f"ingest: {name}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
        conn.commit()
    finally:
        if owned:
            conn.close()
    return result


def _ingest_file(conn, path, name, quarantine_dir, result):
    """Ingest or quarantine one file.  Raises for unexpected conditions."""
    if not os.path.isfile(path):
        # A directory (or a symlink to one) in the drop is not ours.
        result["skipped"] += 1
        return
    received_at = os.stat(path).st_mtime
    raw = read_bytes(path)

    reason = None
    envelope = None
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        reason = f"not UTF-8: {exc}"
    except ValueError as exc:
        reason = f"invalid JSON: {exc}"
    if reason is None:
        errors = heartbeat_schema.validate(envelope)
        if errors:
            reason = "schema: " + "; ".join(errors)

    if reason is not None:
        dest = quarantine_path(_ensure_dir(quarantine_dir), name)
        shutil.move(path, dest)
        record_reject(conn, station_guess(name), received_at, reason, name)
        result["quarantined"] += 1
        return

    # validate() has already guaranteed a non-empty string station.
    inserted = record_heartbeat(conn, envelope["station"], received_at,
                                envelope)
    conn.commit()          # commit BEFORE unlink: never lose a heartbeat
    os.unlink(path)
    if inserted:
        result["ingested"] += 1
    else:
        # Another pass stored this exact arrival first. The file is still
        # ours to remove, and the count is reported rather than hidden:
        # a rising duplicate count means two ingests are racing.
        result["duplicates"] += 1


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ingest station heartbeats from the SFTP drop into "
                    "sqlite (availability is derived from ARRIVAL time).")
    parser.add_argument("--drop-dir", default=DEFAULT_DROP_DIR,
                        help=f"drop directory (default {DEFAULT_DROP_DIR})")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"sqlite database (default {DEFAULT_DB})")
    parser.add_argument("--quarantine", default=None,
                        help="quarantine directory "
                             "(default <drop-dir>/../quarantine)")
    args = parser.parse_args(argv)

    try:
        result = ingest_once(args.drop_dir, args.db, args.quarantine)
    except (OSError, sqlite3.Error) as exc:
        print(f"ingest: fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if (result["ingested"] or result["quarantined"] or result["errors"]
            or result["duplicates"]):
        print("ingest: {ingested} ingested, {quarantined} quarantined, "
              "{skipped} skipped, {duplicates} duplicate, {errors} errors"
              .format(**result))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())

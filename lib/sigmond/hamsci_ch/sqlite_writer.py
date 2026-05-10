"""SQLite-backed alternative to the ClickHouse Writer (CONTRACT §17.5 alt).

Why this exists:
    On a sigmond client the local sink is just a store-and-forward buffer
    for `hs-uploader` to ship rows upstream.  A local ClickHouse-as-buffer
    burns 1-2 GB of RAM and several cores of background-merge CPU on a
    host whose real job is running an SDR pipeline.  SQLite gives the
    same durability promise (rows survive a crash; the uploader reads at
    its own pace) at tens of MB of RAM and no daemon.

Selection:
    Set `SIGMOND_SQLITE_PATH=/var/lib/sigmond/sink.db` in coordination.env.
    When that env var is present, `hamsci_ch.Writer.from_env()` returns a
    `SqliteWriter` instead of the ClickHouse `Writer`.  Neither env var
    set → no-op (standalone-safe).

Storage shape:
    One queue table `pending_uploads` shared across modes:

        id              INTEGER PRIMARY KEY AUTOINCREMENT
        target_db       TEXT     -- e.g. "psk", "wspr", "timestd"
        target_table    TEXT     -- e.g. "spots", "noise", "events"
        schema_version  INTEGER
        payload_json    TEXT     -- the row, JSON-serialized
        queued_at       TEXT     -- ISO8601 UTC (writer wall-clock)

    The future `hs-uploader` reads rows in FIFO order, ships them to the
    upstream ClickHouse, and deletes on success.  JSON-on-disk means the
    uploader owns schema translation, not the producer — so producers
    stay decoupled from the upstream's column shape.

Interface parity with the ClickHouse Writer:
    `insert(rows)`, `flush()`, `close()`, `health`, `is_noop`, `buffered`
    are all duck-type compatible with `hamsci_ch.Writer`.  Callers that
    do `from sigmond.hamsci_ch import Writer; Writer.from_env(...)` work
    unchanged.

Not threadsafe: instantiate one per producer thread, or serialize calls.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

logger = logging.getLogger("sigmond.hamsci_ch.sqlite")


HEALTH_OK = "ok"
HEALTH_UNREACHABLE = "unreachable"
HEALTH_DEGRADED = "degraded"
HEALTH_NOOP = "noop"


# BufferFull is owned by writer.py so callers can catch one type across
# both backends; we import it lazily inside methods to avoid an import
# cycle with __init__.py's re-exports.
from .writer import BufferFull  # noqa: E402


def _json_default(obj: Any) -> Any:
    """JSON encoder for datetimes and bytes — common in producer rows."""
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.isoformat()
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    raise TypeError(f"{type(obj).__name__} not JSON-serializable")


@dataclass
class SqliteConfig:
    """SQLite sink config resolved from env."""

    path: str

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> Optional["SqliteConfig"]:
        e = env if env is not None else os.environ
        path = (e.get("SIGMOND_SQLITE_PATH") or "").strip()
        if not path:
            return None
        return cls(path=path)


_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS pending_uploads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_db       TEXT NOT NULL,
    target_table    TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 0,
    payload_json    TEXT NOT NULL,
    queued_at       TEXT NOT NULL
)
"""

_QUEUE_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_pending_uploads_target
    ON pending_uploads (target_db, target_table, id)
"""


class SqliteWriter:
    """Writer that buffers rows into a local SQLite queue.

    Use `SqliteWriter.from_env(...)` to construct from coordination.env.
    Pass `connect_factory` in tests to inject a fake connection.
    """

    def __init__(
        self,
        database: str,
        table: str,
        *,
        schema_version: int = 0,
        batch_rows: int = 50_000,
        config: Optional[SqliteConfig] = None,
        connect_factory: Optional[Any] = None,
    ) -> None:
        self.database = database
        self.table = table
        self.schema_version = schema_version
        self.batch_rows = batch_rows
        self._buffer_max = batch_rows * 2
        self._config = config
        self._connect_factory = connect_factory or _default_connect_factory
        self._buffer: list = []
        self._conn: Optional[sqlite3.Connection] = None
        self._schema_initialized = False
        self._health = HEALTH_NOOP if config is None else HEALTH_OK

    @classmethod
    def from_env(
        cls,
        table: str,
        *,
        mode: str,
        database: Optional[str] = None,
        schema_version: int = 0,
        batch_rows: int = 50_000,
        env: Optional[dict] = None,
        connect_factory: Optional[Any] = None,
    ) -> "SqliteWriter":
        """Build a SqliteWriter from coordination.env.

        `mode` is the per-mode key (`wspr`, `psk`, `hfdl`, `codar`,
        `timestd`).  `database` defaults to the mode name — operators
        can override per host via `SIGMOND_SQLITE_DB_<MODE>` for parity
        with `SIGMOND_CLICKHOUSE_DB_<MODE>`.
        """
        cfg = SqliteConfig.from_env(env)
        actual_db = database or _resolve_db_alias(mode, env)
        return cls(
            database=actual_db,
            table=table,
            schema_version=schema_version,
            batch_rows=batch_rows,
            config=cfg,
            connect_factory=connect_factory,
        )

    @property
    def health(self) -> str:
        return self._health

    @property
    def is_noop(self) -> bool:
        return self._config is None

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def insert(self, rows: Sequence) -> None:
        """Buffer rows; auto-flush when `batch_rows` is reached.

        Raises `BufferFull` if the buffer would exceed `2 * batch_rows`
        (SQLite has been unwritable for too long).
        """
        if self.is_noop or not rows:
            return
        self._buffer.extend(rows)
        if len(self._buffer) > self._buffer_max:
            self._health = HEALTH_DEGRADED
            buffered = len(self._buffer)
            self._buffer = self._buffer[: self._buffer_max]
            raise BufferFull(
                f"hamsci_ch.sqlite buffer overflow: {buffered} rows pending, "
                f"max {self._buffer_max} (SQLite unwritable at "
                f"{self._config.path if self._config else '?'})"
            )
        if len(self._buffer) >= self.batch_rows:
            self.flush()

    def flush(self) -> None:
        """Force a flush. Quiet on transient failures (buffer retained)."""
        if self.is_noop or not self._buffer:
            return
        try:
            conn = self._connect()
            if not self._schema_initialized:
                self._init_schema(conn)
            now_iso = datetime.now(timezone.utc).isoformat()
            params = [
                (
                    self.database,
                    self.table,
                    self.schema_version,
                    json.dumps(row, default=_json_default),
                    now_iso,
                )
                for row in self._buffer
            ]
            conn.executemany(
                "INSERT INTO pending_uploads "
                "(target_db, target_table, schema_version, payload_json, queued_at) "
                "VALUES (?, ?, ?, ?, ?)",
                params,
            )
            conn.commit()
            self._buffer = []
            self._health = HEALTH_OK
        except BufferFull:
            raise
        except Exception as e:
            # Drop the handle so a stale/locked DB gets reopened on retry.
            self._conn = None
            self._schema_initialized = False
            if self._health != HEALTH_DEGRADED:
                self._health = HEALTH_UNREACHABLE
            logger.warning(
                "hamsci_ch.sqlite: flush failed for %s.%s "
                "(%d rows buffered): %s",
                self.database, self.table, len(self._buffer), e,
            )

    def close(self) -> None:
        try:
            self.flush()
        finally:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
            self._conn = None

    def __enter__(self) -> "SqliteWriter":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None and self._config is not None:
            self._conn = self._connect_factory(self._config)
        assert self._conn is not None
        return self._conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        # WAL keeps the uploader's reader from blocking the writer.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(_QUEUE_DDL)
        conn.execute(_QUEUE_INDEX_DDL)
        conn.commit()
        self._schema_initialized = True


def _default_connect_factory(config: SqliteConfig) -> sqlite3.Connection:
    # Ensure parent directory exists so first-time install works without
    # operators pre-creating /var/lib/sigmond.  Done here (not in the
    # writer) so tests that inject a factory can bypass filesystem prep.
    parent = Path(config.path).parent
    if str(parent) and parent != Path("."):
        parent.mkdir(parents=True, exist_ok=True)
    # Default isolation_level keeps explicit transactions around each
    # flush so a crash mid-batch loses at most the in-memory buffer,
    # never a partial batch on disk.
    return sqlite3.connect(config.path, timeout=30.0)


def _resolve_db_alias(mode: str, env: Optional[dict] = None) -> str:
    """Per-mode SQLite db-name alias, parallel to the ClickHouse one."""
    e = env if env is not None else os.environ
    return e.get(f"SIGMOND_SQLITE_DB_{mode.upper()}", mode)

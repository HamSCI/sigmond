"""HamSCI sink writer primitives (CONTRACT §17.5).

Producer clients call `Writer.from_env(...)` and get back a backend
chosen at construction time from the environment:

- `SIGMOND_SQLITE_PATH` set     → `SqliteWriter` (local FIFO queue,
  recommended for client hosts; tens of MB RAM, no daemon).
- `SIGMOND_CLICKHOUSE_URL` set  → ClickHouse `Writer` (matches the
  upstream wsprdaemon-server shape; heavier, OLAP-grade).
- Neither set                   → no-op (standalone-safe).

Both writers expose the same `insert/flush/close/health/is_noop/
buffered` interface, so callers don't branch.  `BufferFull` is the
single exception type either backend raises on prolonged sink failure.

Sibling library to the future `hs-uploader` (reader/shipper side); the
two share schema knowledge through migration files (CH) or JSON
payloads in the queue table (SQLite) but no code today.

Not threadsafe: instantiate one writer per producer thread, or
serialize calls externally.
"""

from .writer import (
    BufferFull,
    ConnectionConfig,
    Writer,
)
from .sqlite_writer import (
    SqliteConfig,
    SqliteWriter,
)

__all__ = [
    "Writer",
    "SqliteWriter",
    "BufferFull",
    "ConnectionConfig",
    "SqliteConfig",
]

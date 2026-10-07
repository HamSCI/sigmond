"""sigmond.hamsci_sink through the import paths clients and tests use today.

Since v3.70 the writer's own tests live in hs-uploader's
tests/test_sink_writer.py, beside its code, and tests/test_hamsci_sink_compat.py
holds sigmond's bundled copy to that code.  The classes kept here reach the
writer only through sigmond.hamsci_sink and sigmond.hamsci_sink.writer.  They
set module globals through the old submodule name, import HEALTH_*,
_resolve_db_alias and DEFAULT_SQLITE_BATCH_ROWS from it, and catch the
BufferFull the package exports.  sigmond's dev venv has no hs-uploader, so they
run the bundled copy, as codar-sounder, hf-tec and superdarn-sounder do.
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond.hamsci_sink import BufferFull, SqliteConfig, Writer
from sigmond.hamsci_sink.writer import (
    HEALTH_DEGRADED, HEALTH_NOOP, HEALTH_OK, HEALTH_UNREACHABLE,
    _resolve_db_alias,
)


def _temp_db_path() -> str:
    """Caller-owned temp file path; we delete in tearDown."""
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    Path(f.name).unlink()  # let sqlite create the file fresh
    return f.name


class TestNoOpMode(unittest.TestCase):
    """No path configured and no writable default → noop. Standalone-safe."""

    def test_from_env_no_path_yields_noop(self):
        w = Writer.from_env(table="spots", mode="psk", env={})
        # env={} has no SIGMOND_SQLITE_PATH; from_env may still pick the
        # /var/lib/sigmond default if that dir is writable on the test
        # host.  Force the standalone case by disabling the probe.
        from sigmond.hamsci_sink import writer as writer_mod
        original = writer_mod._default_sqlite_writable
        writer_mod._default_sqlite_writable = lambda _p: False
        try:
            w = Writer.from_env(table="spots", mode="psk", env={})
            self.assertTrue(w.is_noop)
            self.assertEqual(w.health, HEALTH_NOOP)
        finally:
            writer_mod._default_sqlite_writable = original

    def test_noop_insert_does_nothing(self):
        from sigmond.hamsci_sink import writer as writer_mod
        original = writer_mod._default_sqlite_writable
        writer_mod._default_sqlite_writable = lambda _p: False
        try:
            w = Writer.from_env(table="spots", mode="psk", env={})
            w.insert([{"a": 1}, {"a": 2}])
            self.assertEqual(w.buffered, 0)
            w.flush()
            w.close()
        finally:
            writer_mod._default_sqlite_writable = original


class TestConfigAndAlias(unittest.TestCase):

    def test_config_from_env_strips_blank(self):
        self.assertIsNone(SqliteConfig.from_env({"SIGMOND_SQLITE_PATH": ""}))
        self.assertIsNone(SqliteConfig.from_env({"SIGMOND_SQLITE_PATH": "   "}))

    def test_config_from_env_returns_path(self):
        cfg = SqliteConfig.from_env({"SIGMOND_SQLITE_PATH": "/tmp/sink.db"})
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.path, "/tmp/sink.db")

    def test_resolve_db_alias_uses_env_then_falls_back(self):
        env = {"SIGMOND_SQLITE_DB_PSK": "psk_local"}
        self.assertEqual(_resolve_db_alias("psk", env), "psk_local")
        self.assertEqual(_resolve_db_alias("hfdl", env), "hfdl")


class TestUnreachableHandling(unittest.TestCase):
    """SQLite is local, so 'unreachable' means disk-full / readonly /
    locked-too-long.  We simulate with a connect_factory that fails."""

    def test_transient_failure_keeps_buffer_marks_unreachable(self):
        attempts = {"n": 0}

        def factory(cfg):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise sqlite3.OperationalError("simulated disk error")
            return sqlite3.connect(":memory:")

        w = Writer.from_env(
            table="spots", mode="psk",
            env={"SIGMOND_SQLITE_PATH": "/nonexistent/dir/sink.db"},
            batch_rows=2, connect_factory=factory,
        )
        w.insert([{"a": 1}, {"a": 2}])  # triggers flush; first attempt fails
        self.assertEqual(w.health, HEALTH_UNREACHABLE)
        self.assertEqual(w.buffered, 2)
        # Next flush succeeds against the in-memory connection.
        w.flush()
        self.assertEqual(w.health, HEALTH_OK)
        self.assertEqual(w.buffered, 0)

    def test_buffer_overflow_raises_buffer_full(self):
        def always_fail(cfg):
            raise sqlite3.OperationalError("simulated disk full")

        w = Writer.from_env(
            table="spots", mode="psk",
            env={"SIGMOND_SQLITE_PATH": "/nonexistent/dir/sink.db"},
            batch_rows=3, connect_factory=always_fail,
        )
        with self.assertRaises(BufferFull):
            for i in range(7):
                w.insert([{"i": i}])
        self.assertEqual(w.health, HEALTH_DEGRADED)


class TestWriterFromEnvDispatch(unittest.TestCase):
    """`Writer.from_env` selects a writable sink path: an explicit
    `SIGMOND_SQLITE_PATH`, else the sigmond default, else no-op."""

    def setUp(self):
        self.db_path = _temp_db_path()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(self.db_path + suffix)
            if p.exists():
                p.unlink()

    def test_explicit_path_yields_enabled_writer(self):
        w = Writer.from_env(
            table="spots", mode="psk",
            env={"SIGMOND_SQLITE_PATH": self.db_path},
        )
        self.assertIsInstance(w, Writer)
        self.assertFalse(w.is_noop)

    def test_neither_set_with_no_default_dir_yields_noop(self):
        # When /var/lib/sigmond doesn't exist and can't be created, the
        # fallback is no-op (preserves standalone-safety).  We force this
        # by monkeypatching the writability probe to return False.
        from sigmond.hamsci_sink import writer as writer_mod
        original = writer_mod._default_sqlite_writable
        writer_mod._default_sqlite_writable = lambda _p: False
        try:
            w = Writer.from_env(table="spots", mode="psk", env={})
            self.assertTrue(w.is_noop)
        finally:
            writer_mod._default_sqlite_writable = original

    def test_neither_set_with_writable_default_yields_enabled(self):
        # The default: SQLite at /var/lib/sigmond/sink.db when the
        # parent dir is writable.  Inject a temp dir so the test doesn't
        # need /var/lib/sigmond on the host.
        tmpdir = tempfile.mkdtemp()
        try:
            from sigmond.hamsci_sink import writer as writer_mod
            original_path = writer_mod._DEFAULT_SQLITE_PATH
            writer_mod._DEFAULT_SQLITE_PATH = str(Path(tmpdir) / "sink.db")
            try:
                w = Writer.from_env(table="spots", mode="psk", env={})
                self.assertFalse(w.is_noop)
            finally:
                writer_mod._DEFAULT_SQLITE_PATH = original_path
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestFromEnvBatchRows(unittest.TestCase):
    """`Writer.from_env` defaults `batch_rows` to the small SQLite
    write-buffer size, and honors an explicit override."""

    def setUp(self):
        self.db_path = _temp_db_path()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(self.db_path + suffix)
            if p.exists():
                p.unlink()

    def test_default_batch_rows_is_sqlite_default(self):
        from sigmond.hamsci_sink.writer import DEFAULT_SQLITE_BATCH_ROWS
        w = Writer.from_env(
            table="spots", mode="psk",
            env={"SIGMOND_SQLITE_PATH": self.db_path},
            # No batch_rows arg — caller uses Writer.from_env's default.
        )
        self.assertEqual(w.batch_rows, DEFAULT_SQLITE_BATCH_ROWS)

    def test_explicit_batch_rows_honored(self):
        w = Writer.from_env(
            table="spots", mode="psk",
            env={"SIGMOND_SQLITE_PATH": self.db_path},
            batch_rows=42,
        )
        self.assertEqual(w.batch_rows, 42)


if __name__ == "__main__":
    unittest.main()

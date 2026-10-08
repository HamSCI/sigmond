# v3.70 — hs-uploader foundation (sink control step 2a-i) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the foundation that v3.71's per-sender send-record keys need, while changing nothing the station sends.

**Architecture:** The sink writer moves from sigmond into hs-uploader as `hs_uploader.sink`, and `sink.db` rows gain
`producer` and `local`. sigmond keeps a compatibility import with a bundled fallback. The send-record store gains a
schema version and an `hs-uploader migrate` step that sigmond runs before it restarts the daemon. Every source learns
to order its own send records, and the store logs, without refusing, any write that would move one backward. Two
fixes complete the foundation. A queued retry keeps its key through repeated failures. A rollback re-renders
`pipelines.toml` with the restored sigmond.

**Tech Stack:** Python 3.11 standard library (sqlite3, argparse, dataclasses), pytest, bash rigs.

**Spec:** `sigmond/tasks/plan-sink-control.md` — §10.4 (this image), decisions D8–D15 (§2), §6.1, §6.2. Research
map behind every file and line cited here: `sigmond/.superpowers/research/v370-step2a-map.json`.

## Global Constraints

- **v3.70 changes nothing the station sends** (D8, §10.4). It changes no send-record key, no row selection, no
  pipeline name and no `pipelines.toml` content, and it adds no filter on `producer`. The one deliberate change in
  behaviour fixes a defect: a queued retry keeps its key, send record and commit token (D15).
- A key migration, when one comes in v3.71, copies and never deletes (D9). v3.70's only migration records version 1
  and changes no row and no table.
- The daemon never migrates while it starts. Its store's constructor leaves `user_version` alone (D10).
- `sink.db` gains columns by `ALTER TABLE ADD COLUMN` alone: no index, no VACUUM, no UPDATE of existing rows (D12).
- sigmond's core stays stdlib-only, and so does `hs_uploader.sink`. hs-uploader's runtime dependencies do not change.
- No hs-uploader version bump: six client `uv.lock` files pin `0.1.0` editable, and none gets relocked.
- No client repo changes. Clients keep importing `sigmond.hamsci_sink`. CLIENT-CONTRACT.md waits for step 7.
- Vocabulary from spec §2.1: *sink*, *sink switch*, *site sink switch*. Name the database file only as `sink.db`.
- Develop on `main` in each repo, with one commit per task. Never push without Michael's explicit go. Every
  commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN
  ```
- Test runners: hs-uploader `cd /home/mjh/hamsci/repos/hs-uploader && .venv/bin/pytest <file> -v` (baseline 270
  passed, 1 skipped at 3e97223); sigmond `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest <file> -v`
  (baseline 3428 passed at 64cf83f); sigmond-appliance `bash -n` plus its hermetic `test-*.sh`.
- Tasks 1–9 touch no station and no rig. Task 10 is external: each step waits for Michael's go, and every station
  step starts with a claude-bus read and post.
- Prose in messages and docs: short sentences, active voice, few uses of "to be", times in UTC.
- Line numbers in a task name the file as it stands at that task's base. Find each edit by its quoted anchor.

## Review Focus

1. **Two recorders open an old `sink.db` at the same moment.** Both must keep writing. The columns must exist once,
   and neither recorder may lose a flush. Pinned by Task 1 (`test_two_writers_racing_ensure_columns`).
2. **A `sink.db` that already holds hundreds of thousands of rows.** Opening it must add the columns at once,
   without rewriting the table, so no recorder's flush waits out its busy timeout. Pinned by Task 1
   (`test_large_old_file_gains_columns_without_rewrite`).
3. **A client venv without hs-uploader** (codar-sounder, hf-tec, superdarn-sounder). `sigmond.hamsci_sink` must
   still write, through the bundled copy. Pinned by Task 6 (`test_bundled_writer_used_without_hs_uploader`).
4. **A station rolled back to v3.69 after v3.70 ran.** The v3.69 daemon and writers must open a version-1
   `watermarks.db` and a `sink.db` with the extra columns, and carry on unchanged. Pinned by Task 2
   (`test_current_store_opens_a_version_1_file`) and by Task 9's PHASE G-bis on the rig.
5. **A retry that fails on replay, then succeeds.** Its acknowledgement must move the send record to its own
   `cursor_after` and hand its commit token to the source, exactly once. Pinned by Task 4
   (`test_retry_failing_twice_then_acked_advances_to_its_cursor`).

---

## Carried to v3.71 (found while planning v3.70; nothing in v3.70 depends on them)

- `hs-uploader migrate`, run by sigmond, reads the default state directory. A host that sets
  `HS_UPLOADER_STATE_DIR` in the daemon's environment files would migrate a different file. v3.71's key copy must
  pass `--db` or read those files.
- On a fresh host, bring-up's migrate finds no `watermarks.db`, because the daemon creates it later. The store then
  sits at version 0 until the next manifest write. Migrations run up from 0, so nothing breaks, but v3.71 must not
  assume a version.
- `smd update --apply` neither re-renders `pipelines.toml` nor restarts the daemon. v3.71 must name the exact path
  that runs its key migration and then restarts the daemon (D10).
- Readers that filter on `producer` (v3.71) must handle a `sink.db` that does not have the column yet.
- Four test files in hs-uploader and sigmond still copy the old `pending_uploads` DDL by hand. They can switch to
  `PENDING_UPLOADS_DDL` when v3.71's readers need the new columns.
- wspr-recorder's suite cannot run on the devbox (its venv lacks `hamsci_dsp`), and hfdl-recorder is not checked out.
  Neither has been run against this plan.
- A daemon restart between about 02:55 and 03:10 UTC lets `mag-recorder-upload.service` ship that day's zip itself,
  outside the site sink switch. Time v3.71's restarts around it.
- **An alarm for an RX888 that software cannot bring back** (Michael, 2026-10-08: v3.71 carries it). Where a hub
  switches power per port, `sigmond-sdr-recover` cycles it. Where none does, a vanished card gets one journal line
  (`bin/sigmond-sdr-recover`, "a manual replug is the only recovery") and no more: the heartbeat has no block for it
  and nothing reads `/var/lib/sigmond/sdr-recover.json`. The heartbeat must carry the card's state (present, absent
  since when, whether its port can switch power), and the fleetboard must show "needs a person to replug" as an
  alarm. A schema change on both the station and wd30.

### Task 1: `hs_uploader.sink` — the sink writer moves into hs-uploader, and each row names its producer

Clients store their rows in `sink.db` through a writer that lives in sigmond today
(`sigmond/lib/sigmond/hamsci_sink/writer.py`).  Spec §10.4 item 1 moves it into hs-uploader as
`hs_uploader.sink`, beside the sources that read what it writes.  This task copies the file
verbatim and then changes only what item 2 asks.  The copy keeps sigmond's surface, its
environment names, its logger name `sigmond.hamsci_sink` and its `hamsci_sink:` message prefix,
because operators grep the last two.

Item 2 gives each `pending_uploads` row two new columns, `producer` and `local`.  When the writer
opens a file that lacks them, it adds both with `ALTER TABLE ADD COLUMN`.  SQLite then changes only
the schema and rewrites no row, so even a file the size of B4's (14.9 GB when a 12.8-million-row
trim ran there on 2026-07-23, `sigmond/lib/sigmond/storage_trim.py:333-334`) gains them in
milliseconds.  Several recorders
share one `sink.db` and restart together under `smd align`.  Two of them may both see a column
missing, and the slower ALTER then fails with "duplicate column name".  `ensure_columns` counts
that error as success.  It adds no index, runs no VACUUM and updates no row (D12), so older rows
keep `producer = ''`.

A caller may name its producer, but no client does so before step 4.  Until then the writer infers
the producer for each row with `infer_producer`, the same rule that readers will later apply to
rows stored with `''`.  Both sides read only stored columns, so they reach the same answer.  Every
new row stores `local = 0`.

Nothing the station sends changes.  No reader selects or filters on the new columns in v3.70, and
every reader names its columns, so the two extra columns go unseen.  Clients keep importing
`sigmond.hamsci_sink`, which runs sigmond's own copy until Task 6 makes it prefer this module.  A
v3.69 writer still running in memory keeps inserting its five columns, and SQLite fills in the
defaults; a station rolled back to v3.69 reads and writes the file the same way.

The D12 rule, checked against every client's writer call on 2026-10-07:

| Client | Writer call | Stores in | Producer |
|---|---|---|---|
| psk-recorder | `psk-recorder/src/psk_recorder/core/ch_tailer.py:563-567` (`mode="psk"`, `table="spots"`); each row's `mode` reads `ft8` or `ft4` (`ch_tailer.py:132`, `:188-193`, `:202`; tailer modes at `core/receiver_manager.py:487`) | `psk.spots` | psk-recorder |
| meteor-scatter | `meteor-scatter/src/meteor_scatter/core/ch_tailer.py:481-485` (`mode="psk"`, `table="spots"`); each row's `mode` reads `msk144` (`ch_tailer.py:70`, `:113`; tailer modes at `core/receiver_manager.py:483`) | `psk.spots` | meteor-scatter |
| wspr-recorder | `wspr-recorder/wspr_recorder/spot_sink.py:169-171` (spots) and `:614-617` (noise), both `mode="wspr"` | `wspr.spots`, `wspr.noise` | wspr-recorder |
| codar-sounder | `codar-sounder/src/codar_sounder/core/daemon.py:494-497` (`mode="codar"`) | `codar.spots` | codar-sounder |
| superdarn-sounder | `superdarn-sounder/src/superdarn_sounder/core/output.py:49-51` (`mode="superdarn"`) | `superdarn.detections` | superdarn-sounder |
| hfdl-recorder | repo not checked out on the devbox; `sigmond/lib/sigmond/storage_trim.py:91` and `:182` and the writer's own docstring (`writer.py:224-225`) name `hfdl.spots` | `hfdl.spots` | hfdl-recorder (not verified against its code) |
| hf-timestd | none since the 2026-05 move to `timestd.db` (`sigmond/lib/sigmond/commands/verifier_report_timestd.py:4-8`; no sink import under `hf-timestd/src`) | `timestd.events`, older rows only | hf-timestd |
| hf-tec | `hf-tec/src/hf_tec/core/output.py:232` calls `Writer.from_env(table=table)` without `mode`, raises TypeError, and stores nothing | nothing | no entry: its step-4 fix names its producer |
| mag-recorder, hamsci-physics, station-web | no writer call | nothing | — |

`SIGMOND_SQLITE_DB_<MODE>` can rename a target_db.  Only `wspr-recorder/docs/CONFIG.md:157`
mentions it, and no install sets it.  A renamed target_db infers `''`, the column's default, on
both the writer's side and the readers'.

**Files:**
- Create: `hs-uploader/src/hs_uploader/sink/__init__.py`
- Create: `hs-uploader/src/hs_uploader/sink/writer.py` — a copy of
  `sigmond/lib/sigmond/hamsci_sink/writer.py` as it stands at sigmond 64cf83f (436 lines), then
  thirteen edits.  The edits' line numbers name sigmond's file.
- Create: `hs-uploader/tests/test_sink_writer.py`
- Modify: `hs-uploader/CLAUDE.md` (lines 7-16, 39-40, 58-59, 94, 111, 150-151)
- Modify: `hs-uploader/README.md` (lines 3-9, 15-16, and a new section before line 37,
  `## Architecture`)
- Modify: `hs-uploader/docs/REQUIREMENTS.md` (lines 73-77, the first two bullets of
  `## 3. Non-goals / out of scope`)
- Modify: `hs-uploader/src/hs_uploader/sources/sqlite.py`, docstrings only: the module docstring
  (lines 1-13), `_ConnectionConfig.from_env` (line 125) and `SqliteSource` (lines 166-167).  These
  edits add four lines and change no code.
- Left alone here: `sigmond/tests/test_hamsci_sink.py` and `sigmond/lib/sigmond/hamsci_sink/`.
  Task 6 trims the first to the compatibility import's own tests and turns the second into that
  import.

**Interfaces:**
- Consumes: sigmond's `lib/sigmond/hamsci_sink/writer.py` at 64cf83f, the source of the copy.
- Produces, in package `hs_uploader.sink`:
  - `Writer` — sigmond's surface unchanged: `Writer(database, table, *, schema_version=0,
    batch_rows=…, auto_flush_seconds=…, config=None, connect_factory=None)`,
    `Writer.from_env(table, *, mode, database=None, schema_version=0, batch_rows=…,
    auto_flush_seconds=…, env=None, connect_factory=None)`, `insert`, `flush`, `close`, the context
    manager, and `.health .is_noop .buffered .database .table .schema_version .batch_rows`.  It
    gains one optional keyword on both, `producer: Optional[str] = None`, and the attribute
    `.producer` (`""` when the caller named none).
  - `BufferFull`, `SqliteConfig` — unchanged.
  - `PENDING_UPLOADS_DDL: str` — the CREATE TABLE and both CREATE INDEX statements as one script
    for `executescript()`, with `producer TEXT NOT NULL DEFAULT ''` and
    `local INTEGER NOT NULL DEFAULT 0` as the last two columns.
  - `ensure_columns(conn: sqlite3.Connection) -> list[str]` — the columns it added, in order.
  - `infer_producer(target_db: str, target_table: str, payload: dict) -> str` — never raises.
  - Unchanged names in `hs_uploader.sink.writer` that tests and Task 6 reach for:
    `HEALTH_OK`, `HEALTH_UNREACHABLE`, `HEALTH_DEGRADED`, `HEALTH_NOOP`,
    `DEFAULT_SQLITE_BATCH_ROWS`, `DEFAULT_SQLITE_AUTO_FLUSH_SECONDS`, `_DEFAULT_SQLITE_PATH`,
    `_default_sqlite_writable`, `_resolve_db_alias`, and `logger` (named `sigmond.hamsci_sink`).
  - `writer.py` imports only the standard library and nothing from the rest of `hs_uploader`.
    Task 6 bundles it in sigmond as a plain copy and relies on that.

- [ ] **Step 1: Write the failing tests**

Create `hs-uploader/tests/test_sink_writer.py`.  The first nine classes come from
`sigmond/tests/test_hamsci_sink.py` with three changes: the imports name `hs_uploader.sink`,
`import threading` moves to the top, and the child process puts this repo's `src/` on its path.
The logger name in `assertNoLogs` stays `sigmond.hamsci_sink`, because the logger keeps that
name.  The old file's stray `if __name__ == "__main__"` block, which sat between two classes, does
not come along.

```python
"""Tests for hs_uploader.sink.Writer (CONTRACT §17).

The first half moved here from sigmond/tests/test_hamsci_sink.py when
the writer moved into hs-uploader (v3.70); only the imports changed.
The second half covers what v3.70 added: the `producer` and `local`
columns, `ensure_columns`, `infer_producer` and `PENDING_UPLOADS_DDL`
(tasks/plan-sink-control.md §10.4 item 2, D12).
"""

import json
import logging
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from hs_uploader.sink import (
    PENDING_UPLOADS_DDL,
    BufferFull,
    SqliteConfig,
    Writer,
    ensure_columns,
    infer_producer,
)
from hs_uploader.sink.writer import (
    HEALTH_DEGRADED, HEALTH_NOOP, HEALTH_OK, HEALTH_UNREACHABLE,
    _resolve_db_alias,
)

_SRC = Path(__file__).resolve().parent.parent / "src"


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
        from hs_uploader.sink import writer as writer_mod
        original = writer_mod._default_sqlite_writable
        writer_mod._default_sqlite_writable = lambda _p: False
        try:
            w = Writer.from_env(table="spots", mode="psk", env={})
            self.assertTrue(w.is_noop)
            self.assertEqual(w.health, HEALTH_NOOP)
        finally:
            writer_mod._default_sqlite_writable = original

    def test_noop_insert_does_nothing(self):
        from hs_uploader.sink import writer as writer_mod
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


class TestEnabledWriter(unittest.TestCase):

    def setUp(self):
        self.db_path = _temp_db_path()
        self.env = {"SIGMOND_SQLITE_PATH": self.db_path}

    def tearDown(self):
        p = Path(self.db_path)
        if p.exists():
            p.unlink()
        # WAL/SHM sidecars
        for suffix in ("-wal", "-shm"):
            sidecar = Path(self.db_path + suffix)
            if sidecar.exists():
                sidecar.unlink()

    def _writer(self, **kwargs) -> Writer:
        return Writer.from_env(
            table="spots", mode="psk", env=self.env, batch_rows=3, **kwargs,
        )

    def _queue_rows(self) -> list:
        # Table is created lazily on first flush; treat "not yet" as empty.
        if not Path(self.db_path).exists():
            return []
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='pending_uploads'"
            )
            if cur.fetchone() is None:
                return []
            cur = conn.execute(
                "SELECT target_db, target_table, schema_version, "
                "payload_json, queued_at FROM pending_uploads ORDER BY id"
            )
            return list(cur.fetchall())
        finally:
            conn.close()

    def test_buffers_until_batch_threshold(self):
        w = self._writer()
        w.insert([{"a": 1}, {"a": 2}])
        self.assertEqual(w.buffered, 2)
        self.assertEqual(self._queue_rows(), [])  # not flushed yet
        w.insert([{"a": 3}])  # crosses batch_rows=3
        rows = self._queue_rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(w.buffered, 0)
        self.assertEqual(w.health, HEALTH_OK)

    def test_explicit_flush_drains_buffer(self):
        w = self._writer()
        w.insert([{"a": 1}])
        w.flush()
        self.assertEqual(len(self._queue_rows()), 1)
        self.assertEqual(w.buffered, 0)

    def test_payload_is_json_with_target_metadata(self):
        w = self._writer()
        w.insert([{"frequency": 14074000, "mode": "ft8", "score": 17}])
        w.flush()
        rows = self._queue_rows()
        self.assertEqual(len(rows), 1)
        target_db, target_table, schema_version, payload_json, queued_at = rows[0]
        self.assertEqual(target_db, "psk")
        self.assertEqual(target_table, "spots")
        self.assertEqual(schema_version, 0)
        decoded = json.loads(payload_json)
        self.assertEqual(decoded["frequency"], 14074000)
        self.assertEqual(decoded["mode"], "ft8")
        # queued_at parses as ISO8601 UTC.
        parsed = datetime.fromisoformat(queued_at)
        self.assertIsNotNone(parsed.tzinfo)

    def test_datetime_serializes_to_iso(self):
        w = self._writer()
        t = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
        w.insert([{"time": t}])
        w.flush()
        payload = json.loads(self._queue_rows()[0][3])
        self.assertEqual(payload["time"], t.isoformat())

    def test_alias_overrides_database_from_env(self):
        env = {**self.env, "SIGMOND_SQLITE_DB_PSK": "psk_alt"}
        w = Writer.from_env(
            table="spots", mode="psk", env=env, batch_rows=1,
        )
        w.insert([{"x": 1}])
        rows = self._queue_rows()
        self.assertEqual(rows[0][0], "psk_alt")

    def test_close_flushes_and_closes_conn(self):
        w = self._writer()
        w.insert([{"a": 1}])
        w.close()
        self.assertEqual(len(self._queue_rows()), 1)

    def test_context_manager_flushes(self):
        with Writer.from_env(
            table="spots", mode="psk", env=self.env, batch_rows=10,
        ) as w:
            w.insert([{"x": 1}])
        self.assertEqual(len(self._queue_rows()), 1)

    def test_schema_version_persisted(self):
        w = Writer.from_env(
            table="spots", mode="psk", env=self.env, batch_rows=1,
            schema_version=7,
        )
        w.insert([{"x": 1}])
        self.assertEqual(self._queue_rows()[0][2], 7)

    def test_multiple_tables_coexist_in_one_db(self):
        spots = Writer.from_env(
            table="spots", mode="psk", env=self.env, batch_rows=1,
        )
        noise = Writer.from_env(
            table="noise", mode="wspr", env=self.env, batch_rows=1,
        )
        spots.insert([{"freq": 14074000}])
        noise.insert([{"floor": -120}])
        rows = self._queue_rows()
        targets = {(r[0], r[1]) for r in rows}
        self.assertEqual(targets, {("psk", "spots"), ("wspr", "noise")})


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
        from hs_uploader.sink import writer as writer_mod
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
            from hs_uploader.sink import writer as writer_mod
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
        from hs_uploader.sink.writer import DEFAULT_SQLITE_BATCH_ROWS
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


class TestTimeBasedAutoFlush(unittest.TestCase):
    """auto_flush_seconds bounds in-memory residency.  Without it a slow
    stream's buffer could sit for hours before the first write to disk —
    a data-loss-on-crash trap and the same bug that bit psk-recorder
    on its first SQLite run."""

    def setUp(self):
        self.db_path = _temp_db_path()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(self.db_path + suffix)
            if p.exists():
                p.unlink()

    def _row_count(self) -> int:
        if not Path(self.db_path).exists():
            return 0
        conn = sqlite3.connect(self.db_path)
        try:
            r = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='pending_uploads'"
            ).fetchone()
            if r is None:
                return 0
            return conn.execute(
                "SELECT count(*) FROM pending_uploads"
            ).fetchone()[0]
        finally:
            conn.close()

    def test_age_trigger_fires_when_seconds_elapsed(self):
        # batch_rows=1000 (high) so size never trips; auto_flush=0.05s.
        w = Writer.from_env(
            table="spots", mode="psk",
            env={"SIGMOND_SQLITE_PATH": self.db_path},
            batch_rows=1000, auto_flush_seconds=0.05,
        )
        w.insert([{"a": 1}])
        self.assertEqual(self._row_count(), 0)  # not flushed yet
        import time as _time
        _time.sleep(0.08)
        w.insert([{"a": 2}])
        # After the sleep, the next insert sees age >= threshold and
        # flushes the whole accumulated buffer.
        self.assertEqual(self._row_count(), 2)

    def test_age_trigger_disabled_when_zero(self):
        w = Writer.from_env(
            table="spots", mode="psk",
            env={"SIGMOND_SQLITE_PATH": self.db_path},
            batch_rows=10, auto_flush_seconds=0,
        )
        w.insert([{"a": 1}])
        import time as _time
        _time.sleep(0.05)
        w.insert([{"a": 2}])
        # With auto_flush_seconds=0, no age trigger; under batch_rows
        # threshold so still buffered.
        self.assertEqual(self._row_count(), 0)
        self.assertEqual(w.buffered, 2)


class TestGroupWritablePerms(unittest.TestCase):
    """Writer must make sink.db (+WAL/SHM) group-writable after schema
    init so OTHER producers in the same supplementary group can write
    to the same sink.  Without this, the first producer to flush owns
    the files at mode 0644 and the rest hit "attempt to write a
    readonly database" — observed on bee1 2026-05-12.
    """

    def setUp(self):
        self.db_path = _temp_db_path()
        self.env = {"SIGMOND_SQLITE_PATH": self.db_path}

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(self.db_path + suffix)
            if p.exists():
                p.unlink()

    def test_main_db_is_group_writable_after_first_flush(self):
        import stat
        w = Writer.from_env(
            table="spots", mode="psk", env=self.env, batch_rows=1,
        )
        w.insert([{"x": 1}])  # triggers flush
        mode = Path(self.db_path).stat().st_mode
        self.assertTrue(
            mode & stat.S_IWGRP,
            f"main db mode {oct(mode & 0o7777)} missing group-write bit",
        )

    def test_wal_and_shm_sidecars_are_group_writable_after_first_flush(self):
        import stat
        w = Writer.from_env(
            table="spots", mode="psk", env=self.env, batch_rows=1,
        )
        w.insert([{"x": 1}])
        # journal_mode=WAL creates the -wal file at first write commit;
        # -shm appears alongside.  Both inherit the producer's umask
        # at create time, which is what _chmod_group_writable
        # remediates.
        for suffix in ("-wal", "-shm"):
            p = Path(self.db_path + suffix)
            if not p.exists():
                continue  # SQLite version may not have created one yet
            mode = p.stat().st_mode
            self.assertTrue(
                mode & stat.S_IWGRP,
                f"{p.name} mode {oct(mode & 0o7777)} missing group-write bit",
            )

    def test_chmod_failure_is_silent_no_raise(self):
        """A non-owner caller that lacks chmod permission must not
        crash the flush — every sigmond-group writer would otherwise
        hit a hard error on every flush after the first producer
        creates the file."""
        with patch("os.chmod", side_effect=PermissionError("not owner")):
            w = Writer.from_env(
                table="spots", mode="psk", env=self.env, batch_rows=1,
            )
            # No raise — flush completes despite chmod's failure.
            w.insert([{"x": 1}])
        # And the row landed on disk.
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute("SELECT COUNT(*) FROM pending_uploads")
            self.assertEqual(cur.fetchone()[0], 1)
        finally:
            conn.close()


class TestCrossThreadUse(unittest.TestCase):
    """AC0G-ND, 2026-09-24/25: wspr-recorder's batcher thread opened the
    wspr.noise connection; at shutdown the main thread's close() flushed
    the last rows through it, sqlite3 refused ("SQLite objects created in
    a thread can only be used in that same thread"), and the rows were
    lost on every exit. SpotSink also calls insert() from several
    BandRecorder threads, relying on the Writer to serialize itself."""

    def setUp(self):
        self.path = _temp_db_path()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            Path(self.path + suffix).unlink(missing_ok=True)

    def _count(self):
        with sqlite3.connect(self.path) as c:
            return c.execute("SELECT COUNT(*) FROM pending_uploads").fetchone()[0]

    def test_close_on_another_thread_flushes_what_a_worker_buffered(self):
        w = Writer("wspr", "noise", batch_rows=2, auto_flush_seconds=0,
                   config=SqliteConfig(path=self.path))

        def worker():
            w.insert([{"n": 1}, {"n": 2}])   # size-flush opens the connection here
            w.insert([{"n": 3}])             # buffered, not yet flushed

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        with self.assertNoLogs("sigmond.hamsci_sink", level="WARNING"):
            w.close()
        self.assertEqual(self._count(), 3)

    def test_concurrent_inserts_lose_no_rows(self):
        # In a child process with a hard timeout: an unserialized Writer
        # sharing one connection across threads can deadlock inside sqlite
        # while holding the GIL, which no in-process join timeout survives.
        import subprocess
        import textwrap
        script = textwrap.dedent(f"""
            import sys, threading
            sys.path.insert(0, {str(_SRC)!r})
            from hs_uploader.sink import SqliteConfig, Writer
            w = Writer("wspr", "spots", batch_rows=7, auto_flush_seconds=0,
                       config=SqliteConfig(path={self.path!r}))
            w._buffer_max = 10_000
            def worker(k):
                for i in range(250):
                    w.insert([{{"k": k, "i": i}}])
            ts = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
            for t in ts: t.start()
            for t in ts: t.join()
            w.close()
        """)
        try:
            subprocess.run([sys.executable, "-c", script], timeout=60, check=True,
                           capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            self.fail("concurrent inserts hung")
        self.assertEqual(self._count(), 8 * 250)


# ---- v3.70: producer, local, ensure_columns, infer_producer ----------------

# pending_uploads exactly as v3.69's writer created it: no producer, no local.
_V369_DDL = """
CREATE TABLE IF NOT EXISTS pending_uploads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_db       TEXT NOT NULL,
    target_table    TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 0,
    payload_json    TEXT NOT NULL,
    queued_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_uploads_target
    ON pending_uploads (target_db, target_table, id);
CREATE INDEX IF NOT EXISTS idx_pending_uploads_cycle_time
    ON pending_uploads (target_db, target_table,
                        json_extract(payload_json, '$.time'));
"""

# The INSERT v3.69's writer runs; a client still running that code keeps
# running it until it restarts.
_V369_INSERT = (
    "INSERT INTO pending_uploads "
    "(target_db, target_table, schema_version, payload_json, queued_at) "
    "VALUES (?, ?, ?, ?, ?)"
)

_QUEUED_AT = "2026-10-07T00:00:00+00:00"


def _old_sink(path: Path, rows=()) -> None:
    """A sink.db as v3.69 left it, holding `rows` of (db, table, payload)."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_V369_DDL)
        conn.executemany(
            _V369_INSERT,
            [(db, table, 2, json.dumps(p), _QUEUED_AT) for db, table, p in rows],
        )
        conn.commit()
    finally:
        conn.close()


def _columns(path: Path) -> list:
    with sqlite3.connect(path) as c:
        return [r[1] for r in c.execute("PRAGMA table_info(pending_uploads)")]


def _stored(path: Path) -> list:
    with sqlite3.connect(path) as c:
        return c.execute(
            "SELECT target_db, json_extract(payload_json, '$.mode'), producer, local "
            "FROM pending_uploads ORDER BY id"
        ).fetchall()


def _writer(path: Path, mode: str = "psk", table: str = "spots", **kw) -> Writer:
    return Writer.from_env(
        table=table, mode=mode, env={"SIGMOND_SQLITE_PATH": str(path)},
        batch_rows=1000, auto_flush_seconds=0, **kw,
    )


def test_package_exports_the_writer_surface():
    import hs_uploader.sink as sink
    assert set(sink.__all__) == {
        "Writer", "BufferFull", "SqliteConfig",
        "PENDING_UPLOADS_DDL", "ensure_columns", "infer_producer",
    }


def test_flush_failure_keeps_the_logger_name_and_prefix_operators_grep(caplog):
    def always_fail(cfg):
        raise sqlite3.OperationalError("simulated disk full")

    w = Writer.from_env(
        table="spots", mode="psk",
        env={"SIGMOND_SQLITE_PATH": "/nonexistent/dir/sink.db"},
        batch_rows=1, connect_factory=always_fail,
    )
    with caplog.at_level(logging.WARNING, logger="sigmond.hamsci_sink"):
        w.insert([{"a": 1}])
    [record] = caplog.records
    assert record.name == "sigmond.hamsci_sink"
    assert record.getMessage().startswith("hamsci_sink: flush failed for psk.spots")


def test_a_fresh_sink_has_both_new_columns_last(tmp_path):
    db = tmp_path / "sink.db"
    w = _writer(db)
    w.insert([{"mode": "ft8"}])
    w.close()
    assert _columns(db) == [
        "id", "target_db", "target_table", "schema_version",
        "payload_json", "queued_at", "producer", "local",
    ]


def test_the_ddl_script_and_ensure_columns_build_the_same_table(tmp_path):
    fresh, old = tmp_path / "fresh.db", tmp_path / "old.db"
    with sqlite3.connect(fresh) as c:
        c.executescript(PENDING_UPLOADS_DDL)
        c.executescript(PENDING_UPLOADS_DDL)      # IF NOT EXISTS: runs twice
    _old_sink(old)
    with sqlite3.connect(old) as c:
        assert ensure_columns(c) == ["producer", "local"]
    assert _columns(fresh) == _columns(old)
    with sqlite3.connect(fresh) as c:
        names = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_pending_uploads_target", "idx_pending_uploads_cycle_time"} <= names


def test_a_writer_adds_the_columns_to_an_old_sink(tmp_path):
    db = tmp_path / "sink.db"
    _old_sink(db, [("psk", "spots", {"mode": "ft8"})])
    w = _writer(db)
    w.insert([{"mode": "msk144"}])
    w.close()
    assert _columns(db)[-2:] == ["producer", "local"]
    assert _stored(db) == [
        ("psk", "ft8", "", 0),                    # stored by v3.69: untouched
        ("psk", "msk144", "meteor-scatter", 0),   # stored now: inferred
    ]


def test_ensure_columns_twice_adds_nothing_the_second_time(tmp_path):
    db = tmp_path / "sink.db"
    _old_sink(db)
    with sqlite3.connect(db) as c:
        assert ensure_columns(c) == ["producer", "local"]
        assert ensure_columns(c) == []


def test_ensure_columns_without_the_table_creates_nothing(tmp_path):
    db = tmp_path / "sink.db"
    with sqlite3.connect(db) as c:
        assert ensure_columns(c) == []
        assert c.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0


class _StaleTableInfo:
    """A connection that read `PRAGMA table_info` before a rival client
    added the columns: it still sees the v3.69 column list."""

    def __init__(self, conn: sqlite3.Connection, stale_rows: list):
        self._conn = conn
        self._stale = stale_rows

    def execute(self, sql, *args):
        if sql.startswith("PRAGMA table_info"):
            return iter(self._stale)
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_losing_the_race_to_add_a_column_counts_as_success(tmp_path):
    db = tmp_path / "sink.db"
    _old_sink(db)
    a = sqlite3.connect(db, timeout=30)
    b = sqlite3.connect(db, timeout=30)
    try:
        stale = list(b.execute("PRAGMA table_info(pending_uploads)"))
        assert ensure_columns(a) == ["producer", "local"]   # a wins
        # b decided from its stale read; both ALTERs meet "duplicate column".
        assert ensure_columns(_StaleTableInfo(b, stale)) == []
    finally:
        a.close()
        b.close()
    assert _columns(db)[-2:] == ["producer", "local"]


def test_ensure_columns_raises_any_other_error(tmp_path):
    db = tmp_path / "sink.db"
    _old_sink(db)

    class _Locked(_StaleTableInfo):
        def execute(self, sql, *args):
            if sql.startswith("ALTER"):
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, *args)

    with sqlite3.connect(db) as c:
        stale = list(c.execute("PRAGMA table_info(pending_uploads)"))
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            ensure_columns(_Locked(c, stale))


def test_many_connections_racing_ensure_columns_add_each_column_once(tmp_path):
    db = tmp_path / "sink.db"
    _old_sink(db)
    n = 8
    barrier = threading.Barrier(n)
    results, errors = [], []

    def client():
        conn = sqlite3.connect(db, timeout=30)
        try:
            barrier.wait()
            results.append(ensure_columns(conn))
        except Exception as e:
            errors.append(e)
        finally:
            conn.close()

    threads = [threading.Thread(target=client) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []
    assert sorted(c for added in results for c in added) == ["local", "producer"]
    assert _columns(db)[-2:] == ["producer", "local"]


def test_two_writers_racing_ensure_columns(tmp_path, caplog):
    # Two recorders open one old sink.db together.  The slower one read
    # table_info before the faster one added the columns, so both of its
    # ALTERs meet "duplicate column name".  Both must still store.
    db = tmp_path / "sink.db"
    _old_sink(db, [("psk", "spots", {"mode": "ft8"})])
    with sqlite3.connect(db) as c:
        stale = list(c.execute("PRAGMA table_info(pending_uploads)"))

    def slow_factory(cfg):
        conn = sqlite3.connect(cfg.path, timeout=30.0, check_same_thread=False)
        return _StaleTableInfo(conn, stale)

    fast = _writer(db)
    slow = _writer(db, mode="wspr", connect_factory=slow_factory)
    fast.insert([{"mode": "msk144"}])
    slow.insert([{"mode": "W2"}])
    with caplog.at_level(logging.WARNING, logger="sigmond.hamsci_sink"):
        fast.close()
        slow.close()
    assert caplog.records == []                   # neither flush failed
    assert (fast.health, fast.buffered) == (HEALTH_OK, 0)
    assert (slow.health, slow.buffered) == (HEALTH_OK, 0)
    assert _columns(db)[-2:] == ["producer", "local"]
    assert _stored(db) == [
        ("psk", "ft8", "", 0),
        ("psk", "msk144", "meteor-scatter", 0),
        ("wspr", "W2", "wspr-recorder", 0),
    ]


def _traced_factory(statements: list):
    def factory(cfg):
        conn = sqlite3.connect(cfg.path, timeout=30.0, check_same_thread=False)
        conn.set_trace_callback(statements.append)
        return conn
    return factory


def test_the_writer_updates_no_existing_row(tmp_path):
    db = tmp_path / "sink.db"
    _old_sink(db, [("psk", "spots", {"mode": "ft8"}),
                   ("psk", "spots", {"mode": "msk144"})])
    statements: list = []
    w = Writer.from_env(
        table="spots", mode="psk", env={"SIGMOND_SQLITE_PATH": str(db)},
        batch_rows=1000, auto_flush_seconds=0,
        connect_factory=_traced_factory(statements),
    )
    w.insert([{"mode": "ft8"}])
    w.close()
    # D12: rows stored before the column existed keep producer ''.
    assert _stored(db) == [
        ("psk", "ft8", "", 0),
        ("psk", "msk144", "", 0),
        ("psk", "ft8", "psk-recorder", 0),
    ]
    run = [s.lstrip().upper() for s in statements]
    assert not [s for s in run if s.startswith(("UPDATE", "VACUUM", "DELETE"))]
    assert not [s for s in run if "INDEX" in s and "PRODUCER" in s]
    assert len([s for s in run if s.startswith("ALTER TABLE")]) == 2


def test_a_v369_insert_still_works_after_the_columns_arrive(tmp_path):
    # A client restarted later keeps running v3.69's five-column INSERT.
    db = tmp_path / "sink.db"
    _old_sink(db)
    w = _writer(db)
    w.insert([{"mode": "ft8"}])
    w.close()
    with sqlite3.connect(db) as c:
        c.execute(_V369_INSERT, ("psk", "spots", 2, '{"mode": "ft4"}', _QUEUED_AT))
    assert _stored(db)[-1] == ("psk", "ft4", "", 0)


@pytest.mark.parametrize("target_db, target_table, payload, producer", [
    ("psk", "spots", {"mode": "ft8"}, "psk-recorder"),
    ("psk", "spots", {"mode": "ft4"}, "psk-recorder"),
    ("psk", "spots", {"mode": ""}, "psk-recorder"),       # psk-recorder's fallback
    ("psk", "spots", {}, "psk-recorder"),
    ("psk", "spots", {"mode": "msk144"}, "meteor-scatter"),
    ("psk", "spots", {"mode": "MSK144"}, "meteor-scatter"),
    ("psk", "spots", {"mode": None}, "psk-recorder"),
    ("psk", "spots", "not a dict", "psk-recorder"),       # never raises
    ("wspr", "spots", {"mode": "W2"}, "wspr-recorder"),
    ("wspr", "noise", {}, "wspr-recorder"),
    ("codar", "spots", {}, "codar-sounder"),
    ("superdarn", "detections", {}, "superdarn-sounder"),
    ("hfdl", "spots", {}, "hfdl-recorder"),
    ("timestd", "events", {}, "hf-timestd"),
    ("psk_local", "spots", {"mode": "ft8"}, ""),          # renamed by an alias
    ("unknown", "spots", {}, ""),
])
def test_infer_producer(target_db, target_table, payload, producer):
    assert infer_producer(target_db, target_table, payload) == producer


@pytest.mark.parametrize("mode, table, rows, producer", [
    ("psk", "spots", [{"mode": "ft8"}], "psk-recorder"),
    ("psk", "spots", [{"mode": "msk144"}], "meteor-scatter"),
    ("wspr", "spots", [{"mode": "W2"}], "wspr-recorder"),
    ("wspr", "noise", [{"floor": -120}], "wspr-recorder"),
    ("codar", "spots", [{"freq": 4.5e6}], "codar-sounder"),
    ("superdarn", "detections", [{"radar": "fhw"}], "superdarn-sounder"),
])
def test_the_writer_stores_each_clients_producer(tmp_path, mode, table, rows, producer):
    # Each client's real from_env call (mode, table), read from its repo.
    db = tmp_path / "sink.db"
    w = _writer(db, mode=mode, table=table)
    w.insert(rows)
    w.close()
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT producer, local FROM pending_uploads").fetchall() == [
            (producer, 0)]


def test_one_psk_flush_names_each_rows_producer(tmp_path):
    db = tmp_path / "sink.db"
    w = _writer(db)
    w.insert([{"mode": "ft8"}, {"mode": "msk144"}, {"mode": "ft4"}])
    w.close()
    assert [r[2] for r in _stored(db)] == ["psk-recorder", "meteor-scatter", "psk-recorder"]


def test_a_caller_given_producer_wins_over_inference(tmp_path):
    db = tmp_path / "sink.db"
    w = _writer(db, producer="psk-recorder")
    w.insert([{"mode": "msk144"}])
    w.close()
    alias = _writer(db, database="psk_local", producer="meteor-scatter")
    alias.insert([{"mode": "msk144"}])
    alias.close()
    assert [r[2] for r in _stored(db)] == ["psk-recorder", "meteor-scatter"]
    assert w.producer == "psk-recorder"


def test_an_empty_producer_falls_back_to_inference(tmp_path):
    db = tmp_path / "sink.db"
    w = _writer(db, producer="")
    w.insert([{"mode": "msk144"}])
    w.close()
    assert _stored(db)[0][2] == "meteor-scatter"


def test_large_old_file_gains_columns_without_rewrite(tmp_path):
    db = tmp_path / "sink.db"
    _old_sink(db, [("psk", "spots", {"mode": "ft8", "i": i}) for i in range(200_000)])
    conn = sqlite3.connect(db, timeout=30)
    try:
        pages_before = conn.execute("PRAGMA page_count").fetchone()[0]
        started = time.monotonic()
        assert ensure_columns(conn) == ["producer", "local"]
        elapsed = time.monotonic() - started
        conn.commit()
        assert conn.execute("PRAGMA page_count").fetchone()[0] == pages_before
        assert conn.execute(
            "SELECT count(*) FROM pending_uploads WHERE producer = '' AND local = 0"
        ).fetchone()[0] == 200_000
    finally:
        conn.close()
    # A table rewrite of 200k rows takes seconds; a schema-only change, ms.
    assert elapsed < 1.0, f"ensure_columns took {elapsed:.3f} s"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /home/mjh/hamsci/repos/hs-uploader && .venv/bin/pytest tests/test_sink_writer.py -q`
Expected: collection stops with `ModuleNotFoundError: No module named 'hs_uploader.sink'` and
`1 error`.

- [ ] **Step 3: Copy the writer verbatim and create the package**

```bash
cd /home/mjh/hamsci/repos/hs-uploader
mkdir -p src/hs_uploader/sink
cp /home/mjh/hamsci/repos/sigmond/lib/sigmond/hamsci_sink/writer.py src/hs_uploader/sink/writer.py
```

Create `src/hs_uploader/sink/__init__.py`:

```python
"""HamSCI sink writer (CONTRACT §17), the write side of hs-uploader.

Producer clients call `Writer.from_env(...)` to get a local-sink
writer.  The backend is SQLite — a store-and-forward queue under
sigmond's state dir that hs-uploader's sources drain upstream:

- `SIGMOND_SQLITE_PATH` set → writer at that path (explicit override).
- unset                    → `/var/lib/sigmond/sink.db` if its
  directory is writable, else no-op (preserves standalone-safety for
  clients running outside a sigmond install).

SQLite suits a sigmond client host: the local sink is just a buffer
for `hs-uploader`, and a daemon-backed columnar store would burn
1-2 GB of RAM and several merge-CPU cores for no benefit there.

`BufferFull` is the exception the writer raises on prolonged sink
failure rather than silently losing rows.

`PENDING_UPLOADS_DDL`, `ensure_columns` and `infer_producer` give
readers, tools and tests the writer's own schema and producer rule.

This package moved here from `sigmond.hamsci_sink` in v3.70; clients
still import it through that name (tasks/plan-sink-control.md §10.4).
"""

from .writer import (
    PENDING_UPLOADS_DDL,
    BufferFull,
    SqliteConfig,
    Writer,
    ensure_columns,
    infer_producer,
)

__all__ = [
    "Writer",
    "BufferFull",
    "SqliteConfig",
    "PENDING_UPLOADS_DDL",
    "ensure_columns",
    "infer_producer",
]
```

- [ ] **Step 4: Run the tests to see what the bare copy lacks**

Run: `.venv/bin/pytest tests/test_sink_writer.py -q`
Expected: collection stops with `ImportError: cannot import name 'PENDING_UPLOADS_DDL' from
'hs_uploader.sink.writer'`.

- [ ] **Step 5: Edit the copy — thirteen edits, A to M**

Each edit names its lines in sigmond's file and quotes its anchor.  Apply them in order to
`src/hs_uploader/sink/writer.py`.

**Edit A** (lines 8-10): say where the module lives now.

Replace:
```python
    a host whose real job is running an SDR pipeline.

Selection (`Writer.from_env`):
```
with:
```python
    a host whose real job is running an SDR pipeline.

Where it lives:
    This module moved here from sigmond (`sigmond/lib/sigmond/
    hamsci_sink/writer.py`) in v3.70.  Clients still import it as
    `sigmond.hamsci_sink`, which prefers this module.  sigmond also
    carries a copy for venvs that hold no hs-uploader (D14), and a drift
    test holds the two together.  Keep this file stdlib-only and free of
    imports from the rest of `hs_uploader`, so that copy can stay a
    plain file copy.  The logger name and the `hamsci_sink:` message
    prefix stay as they were: operators grep them.

Selection (`Writer.from_env`):
```

**Edit B** (lines 26-33): the two columns in the docstring.  The old last line contradicted the
class, which has held a lock since 2026-09-25.

Replace:
```python
        queued_at       TEXT     -- ISO8601 UTC (writer wall-clock)

    `hs-uploader` reads rows in FIFO order, ships them upstream, and
    deletes on success.  JSON-on-disk means the uploader owns schema
    translation, not the producer — so producers stay decoupled from
    the upstream's column shape.

Not threadsafe: instantiate one per producer thread, or serialize calls.
```
with:
```python
        queued_at       TEXT     -- ISO8601 UTC (writer wall-clock)
        producer        TEXT     -- the client that stored the row
        local           INTEGER  -- 1 = a local archive row (no caller sets it yet)

    `hs-uploader` reads rows in FIFO order, ships them upstream, and
    deletes on success.  JSON-on-disk means the uploader owns schema
    translation, not the producer — so producers stay decoupled from
    the upstream's column shape.

    `producer` and `local` arrived in v3.70 (tasks/plan-sink-control.md
    §10.4 item 2).  A writer that opens an older file adds both with
    ALTER TABLE ADD COLUMN, which changes only the schema: SQLite
    rewrites no row.  Rows stored before v3.70 keep `producer = ''`, and
    readers name their producer with `infer_producer` (D12).  The writer
    stores `local = 0` on every row; a caller that keeps a local archive
    gains a way to say so in a later release.

Thread-safe: see `Writer`.
```

**Edit C** (line 63): say why the logger keeps sigmond's name.

Replace:
```python
logger = logging.getLogger("sigmond.hamsci_sink")
```
with:
```python
# The name operators grep for; it predates the move into hs-uploader.
logger = logging.getLogger("sigmond.hamsci_sink")
```

**Edit D** (lines 130-139): a fresh table carries both columns, last.

Replace:
```python
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
```
with:
```python
# `producer` and `local` sit LAST, so a fresh table and an older table
# that `ensure_columns` extended list their columns in the same order.
_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS pending_uploads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_db       TEXT NOT NULL,
    target_table    TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 0,
    payload_json    TEXT NOT NULL,
    queued_at       TEXT NOT NULL,
    producer        TEXT NOT NULL DEFAULT '',
    local           INTEGER NOT NULL DEFAULT 0
)
"""
```

**Edit E** (lines 155-159, between the cycle index and `class Writer:`): `PENDING_UPLOADS_DDL`,
`ensure_columns` and `infer_producer`.

Replace:
```python
                        json_extract(payload_json, '$.time'))
"""


class Writer:
```
with:
```python
                        json_extract(payload_json, '$.time'))
"""

# The whole queue schema as one script, for `conn.executescript()`:
# tests, fixtures and tools that build a sink.db the way the writer does.
PENDING_UPLOADS_DDL = ";\n".join(
    s.strip() for s in (_QUEUE_DDL, _QUEUE_INDEX_DDL, _QUEUE_CYCLE_INDEX_DDL)
) + ";\n"

# The columns v3.70 added, in the order `ensure_columns` adds them.
_ADDED_COLUMNS = (
    ("producer", "TEXT NOT NULL DEFAULT ''"),
    ("local", "INTEGER NOT NULL DEFAULT 0"),
)


def ensure_columns(conn: sqlite3.Connection) -> list[str]:
    """Add `producer` and `local` to an older `pending_uploads`.

    Returns the names of the columns this call added, in order; an
    empty list when both already exist or the table does not exist yet
    (the CREATE TABLE in `PENDING_UPLOADS_DDL` carries both).

    ALTER TABLE ADD COLUMN with a constant default changes only the
    schema, so it runs in milliseconds on a file of millions of rows
    and rewrites none of them.  This function adds no index, runs no
    VACUUM, and updates no row: a row stored before the column existed
    reads `producer = ''` and `local = 0` (D12).

    Several clients open the same sink.db and may restart together.  Two
    can both see a column missing; the slower one's ALTER then fails
    with "duplicate column name", which means the column now exists, so
    that error counts as success.  Any other error propagates.  Each
    ALTER commits on its own unless the caller holds a transaction open.
    """
    have = {row[1] for row in conn.execute("PRAGMA table_info(pending_uploads)")}
    if not have:
        return []
    added: list[str] = []
    for name, decl in _ADDED_COLUMNS:
        if name in have:
            continue
        try:
            conn.execute(f"ALTER TABLE pending_uploads ADD COLUMN {name} {decl}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
            continue
        added.append(name)
    return added


# D12 (tasks/plan-sink-control.md §2): the one client that writes each
# target_db, read from the clients' own writer calls on 2026-10-07.
#   wspr       wspr-recorder      (wspr.spots, wspr.noise)
#   codar      codar-sounder      (codar.spots)
#   superdarn  superdarn-sounder  (superdarn.detections)
#   hfdl       hfdl-recorder      (hfdl.spots)
#   timestd    hf-timestd         (timestd.events, none written since 2026-05)
# psk-recorder and meteor-scatter both write psk.spots; each row's own
# `mode` tells them apart (see `infer_producer`).  hf-tec writes no row
# today, and the fix that makes it write will name its producer, so it
# needs no entry.
_PRODUCER_BY_DB = {
    "wspr": "wspr-recorder",
    "codar": "codar-sounder",
    "superdarn": "superdarn-sounder",
    "hfdl": "hfdl-recorder",
    "timestd": "hf-timestd",
}
_PSK_DB = "psk"
_METEOR_SCATTER_MODE = "msk144"


def infer_producer(target_db: str, target_table: str, payload: dict) -> str:
    """Name the client that stored a row whose writer named none (D12).

    MSK144 rows in `psk` belong to meteor-scatter and every other `psk`
    row to psk-recorder; each other target_db maps to the one client
    that writes it.  An unknown target_db, including one renamed by
    `SIGMOND_SQLITE_DB_<MODE>`, gives `''`, the column's default.

    The writer applies this to each new row whose caller named no
    producer, and readers apply it to rows stored with `''`, so both
    reach the same answer from the stored columns alone.
    `target_table` rides along for that shared signature; no rule needs
    it today.  Never raises: a flush must not fail over a payload's
    shape.
    """
    if target_db == _PSK_DB:
        mode = payload.get("mode") if isinstance(payload, dict) else None
        if isinstance(mode, str) and mode.strip().lower() == _METEOR_SCATTER_MODE:
            return "meteor-scatter"
        return "psk-recorder"
    return _PRODUCER_BY_DB.get(target_db, "")


class Writer:
```

**Edit F** (lines 163-165): the class docstring names `producer`.

Replace:
```python
    Pass `connect_factory` in tests to inject a fake connection.

    Thread-safe: `insert`, `flush` and `close` hold one re-entrant lock,
```
with:
```python
    Pass `connect_factory` in tests to inject a fake connection.

    `producer` names the client that stores the rows.  When the caller
    leaves it out, each row's producer comes from `infer_producer`.

    Thread-safe: `insert`, `flush` and `close` hold one re-entrant lock,
```

**Edit G** (lines 179-182, `__init__`'s keywords; the `config:` line makes the anchor unique).

Replace:
```python
        schema_version: int = 0,
        batch_rows: int = DEFAULT_SQLITE_BATCH_ROWS,
        auto_flush_seconds: float = DEFAULT_SQLITE_AUTO_FLUSH_SECONDS,
        config: Optional[SqliteConfig] = None,
```
with:
```python
        schema_version: int = 0,
        producer: Optional[str] = None,
        batch_rows: int = DEFAULT_SQLITE_BATCH_ROWS,
        auto_flush_seconds: float = DEFAULT_SQLITE_AUTO_FLUSH_SECONDS,
        config: Optional[SqliteConfig] = None,
```

**Edit H** (lines 187-188): keep the caller's producer.

Replace:
```python
        self.schema_version = schema_version
        self.batch_rows = batch_rows
```
with:
```python
        self.schema_version = schema_version
        self.producer = producer or ""
        self.batch_rows = batch_rows
```

**Edit I** (lines 210-213, `from_env`'s keywords; the `env:` line makes the anchor unique).

Replace:
```python
        schema_version: int = 0,
        batch_rows: int = DEFAULT_SQLITE_BATCH_ROWS,
        auto_flush_seconds: float = DEFAULT_SQLITE_AUTO_FLUSH_SECONDS,
        env: Optional[dict] = None,
```
with:
```python
        schema_version: int = 0,
        producer: Optional[str] = None,
        batch_rows: int = DEFAULT_SQLITE_BATCH_ROWS,
        auto_flush_seconds: float = DEFAULT_SQLITE_AUTO_FLUSH_SECONDS,
        env: Optional[dict] = None,
```

**Edit J** (lines 227-228): `from_env`'s docstring names `producer`.

Replace:
```python
        `database=` to bypass the alias.
        """
```
with:
```python
        `database=` to bypass the alias.  `producer` names the client;
        leave it out and the writer infers it per row (`infer_producer`).
        """
```

**Edit K** (lines 244-245): pass it through.

Replace:
```python
            schema_version=schema_version,
            batch_rows=batch_rows,
```
with:
```python
            schema_version=schema_version,
            producer=producer,
            batch_rows=batch_rows,
```

**Edit L** (lines 314-323): each row stores its producer and `local = 0`.

Replace:
```python
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
```
with:
```python
                        now_iso,
                        self.producer
                        or infer_producer(self.database, self.table, row),
                        0,
                    )
                    for row in self._buffer
                ]
                conn.executemany(
                    "INSERT INTO pending_uploads "
                    "(target_db, target_table, schema_version, payload_json, "
                    "queued_at, producer, local) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    params,
                )
```

**Edit M** (lines 372-373, in `_init_schema`): an older file gains the columns before the first
INSERT.  `_init_schema` runs on the first flush and again after any failed one, so a writer that
lost a race simply finds the columns there next time.

Replace:
```python
        conn.execute(_QUEUE_CYCLE_INDEX_DDL)
        conn.commit()
```
with:
```python
        conn.execute(_QUEUE_CYCLE_INDEX_DDL)
        # An older sink.db gains producer and local here (v3.70).
        added = ensure_columns(conn)
        conn.commit()
        if added:
            logger.info(
                "hamsci_sink: added column(s) %s to pending_uploads in %s",
                ", ".join(added),
                self._config.path if self._config else "?",
            )
```

- [ ] **Step 6: Confirm the rest of the copy stayed verbatim**

Run:
```bash
diff /home/mjh/hamsci/repos/sigmond/lib/sigmond/hamsci_sink/writer.py src/hs_uploader/sink/writer.py | grep '^<'
grep -nE '^(import|from) ' src/hs_uploader/sink/writer.py
```
Expected: exactly these five removed lines, and nothing else from sigmond's file:
```
< Not threadsafe: instantiate one per producer thread, or serialize calls.
<     queued_at       TEXT NOT NULL
<         `database=` to bypass the alias.
<                     "(target_db, target_table, schema_version, payload_json, queued_at) "
<                     "VALUES (?, ?, ?, ?, ?)",
```
The second command lists only `__future__`, `json`, `logging`, `os`, `sqlite3`, `threading`,
`time`, `dataclasses`, `datetime`, `pathlib` and `typing`: the standard library alone.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sink_writer.py -q`
Expected: `67 passed` (28 moved from sigmond, 39 new).  The 200k-row test takes about one second,
almost all of it spent building the file.

Run: `.venv/bin/pytest -q`
Expected: `337 passed, 1 skipped` (the baseline's 270 passed, 1 skipped, plus 67).

- [ ] **Step 8: Watch two guards fail**

A test nobody watched fail proves nothing.  Break the writer in a throwaway copy, twice:

```bash
cd /home/mjh/hamsci/repos/hs-uploader
T=$(mktemp -d) && cp -a src tests pyproject.toml "$T"/ && cd "$T"
sed -i 's/        added = ensure_columns(conn)/        added = []/' src/hs_uploader/sink/writer.py
/home/mjh/hamsci/repos/hs-uploader/.venv/bin/pytest tests/test_sink_writer.py -q -p no:cacheprovider | tail -1
cp /home/mjh/hamsci/repos/hs-uploader/src/hs_uploader/sink/writer.py src/hs_uploader/sink/writer.py
python3 - <<'EOF'
p = "src/hs_uploader/sink/writer.py"
s = open(p).read()
old = ('            if "duplicate column name" not in str(e).lower():\n'
       '                raise\n            continue')
assert s.count(old) == 1
open(p, "w").write(s.replace(old, "            raise"))
EOF
/home/mjh/hamsci/repos/hs-uploader/.venv/bin/pytest tests/test_sink_writer.py -q -p no:cacheprovider | tail -1
cd /home/mjh/hamsci/repos/hs-uploader && rm -rf "$T"
```
Expected: `4 failed, 63 passed` with the column step removed (an old file refuses the
seven-column INSERT), then `3 failed, 64 passed` with the race tolerance removed (all three race
tests, `test_two_writers_racing_ensure_columns` among them).

- [ ] **Step 9: Say in the docs that hs-uploader now owns the writer and runs a daemon**

Four files still say that recorders write through `sigmond.hamsci_sink`, or that hs-uploader only
reads: `CLAUDE.md`, `README.md`, `docs/REQUIREMENTS.md` §3, and the docstrings in
`src/hs_uploader/sources/sqlite.py`.  Each now says that the writer lives in `hs_uploader.sink`
and that sigmond keeps a compatibility import.

In `CLAUDE.md`, lines 7-16, replace:
```markdown
**hs-uploader** is the read-side counterpart to `sigmond.hamsci_sink`'s
`Writer`. Sigmond clients (the recorders) stage observation records
into a local SQLite sink (`/var/lib/sigmond/sink.db`); this library
forwards them up to HamSCI / community ingest destinations —
wsprdaemon.org, wsprnet.org, PSKReporter, PSWS.

Part of the HamSCI sigmond suite — see `/opt/git/sigmond/sigmond/CLAUDE.md`
(orchestrator) and `/opt/git/sigmond/CLAUDE.md` (umbrella) for
cross-repo context. This is a library; it has no daemon of its own.
Its consumers run a per-pipeline pump worker in-process.
```
with:
```markdown
**hs-uploader** holds both halves of a station's sink.  Its sink
writer, `hs_uploader.sink.Writer`, stores the observation records of
sigmond clients (the recorders) in a local SQLite sink
(`/var/lib/sigmond/sink.db`).  Its sources and transports then forward
those records to HamSCI and community destinations: wsprdaemon.org,
wsprnet.org, PSKReporter, PSWS.  The writer moved here from sigmond in
v3.70; clients still import it as `sigmond.hamsci_sink`, which sigmond
keeps as a compatibility import.

Part of the HamSCI sigmond suite — see `/opt/git/sigmond/sigmond/CLAUDE.md`
(orchestrator) and `/opt/git/sigmond/CLAUDE.md` (umbrella) for
cross-repo context.  hs-uploader runs both as a library and as a
daemon.  The host daemon, `hs-uploader serve` (`daemon.py`,
`systemd/hs-uploader.service`), runs every pipeline in
`/etc/hs-uploader/pipelines.toml` in one process.  Some recorders still
run in-process senders built from the same library.
```

In `CLAUDE.md`, lines 39-40, replace:
```markdown
Consumers integrate via `from hs_uploader import …`, not the CLI.
The CLI is a thin diagnostic entry point.
```
with:
```markdown
Clients store rows through `hs_uploader.sink.Writer` and build
in-process senders from `hs_uploader`'s sources and transports.  The CLI
inspects state and runs the host daemon (`hs-uploader serve`).
```

In `CLAUDE.md`, lines 58-59, replace:
```markdown
  - `SqliteSource` (preferred) reads `sigmond.hamsci_sink.Writer`'s
    `pending_uploads` queue. Supports `extra_where` and `start_at`
```
with:
```markdown
  - `SqliteSource` (preferred) reads the `pending_uploads` queue that
    `hs_uploader.sink.Writer` fills. Supports `extra_where` and `start_at`
```

In `CLAUDE.md`, line 94, replace:
```markdown
  config.py               # config loading helpers
```
with:
```markdown
  config.py               # config loading helpers
  daemon.py               # host daemon (`hs-uploader serve`)
  sink/
    writer.py             # sink Writer: stores client rows in sink.db
                          # (moved from sigmond in v3.70)
```

In `CLAUDE.md`, line 111 (the count has gone stale), replace:
```markdown
tests/                    # 11 files
```
with:
```markdown
tests/
```

In `CLAUDE.md`, lines 150-151, replace:
```markdown
- Sigmond sink: `/var/lib/sigmond/sink.db` (read-side; sigmond owns
  writes via `hamsci_sink.Writer`).
```
with:
```markdown
- Sigmond sink: `/var/lib/sigmond/sink.db`.  Clients write it through
  `hs_uploader.sink.Writer` (imported as `sigmond.hamsci_sink`), and the
  sources here read it.  Since v3.70 each row carries `producer` and
  `local`; the writer adds both columns when it opens an older file and
  never rewrites an existing row.
```

In `README.md`, lines 3-9, replace:
```markdown
Library for shipping HamSCI sigmond observations to HF reporting destinations.

`hs-uploader` is the read-side counterpart to `sigmond.hamsci_sink`'s
`Writer`: clients import it to forward records they have staged in
sigmond's local SQLite sink (preferred) or in spool files (fallback) up to a
HamSCI / community ingest destination — wsprdaemon.org, wsprnet.org,
PSKReporter, PSWS, etc.
```
with:
```markdown
Library and host daemon for shipping HamSCI sigmond observations to HF
reporting destinations.

`hs-uploader` holds both halves of a station's sink.  Clients store records
in sigmond's local SQLite sink through its sink writer,
`hs_uploader.sink.Writer`.  Its sources and transports then forward those
records, or files from spool directories, to a HamSCI or community
destination: wsprdaemon.org, wsprnet.org, PSKReporter, PSWS.  The host
daemon, `hs-uploader serve`, runs every pipeline a station declares in one
process.
```

In `README.md`, lines 15-16, replace:
```markdown
- **Sources:** `SqliteSource` (preferred — reads
  `sigmond.hamsci_sink.Writer`'s `pending_uploads` queue, with
```
with:
```markdown
- **Sources:** `SqliteSource` (preferred — reads the
  `pending_uploads` queue that `hs_uploader.sink.Writer` fills, with
```

In `README.md`, line 37, replace:
````markdown
## Architecture
````
with:
````markdown
## Sink writer

`hs_uploader.sink` stores a client's records in `/var/lib/sigmond/sink.db`,
the queue that `SqliteSource` and `WsprCycleSource` read.  It moved here from
sigmond in v3.70, and clients still import it as `sigmond.hamsci_sink`.

```python
from hs_uploader.sink import Writer

with Writer.from_env(table="spots", mode="psk", schema_version=2) as w:
    w.insert([{"time": "2026-10-07T12:00:00+00:00", "mode": "ft8",
               "frequency": 14074000}])
```

- `SIGMOND_SQLITE_PATH` names the file.  Without it the writer uses
  `/var/lib/sigmond/sink.db` when that directory accepts writes, and
  otherwise stores nothing, so a client outside a sigmond install stays safe.
- Each row records its `producer`, the client that stored it.  A caller may
  pass `producer=`; otherwise `infer_producer` names it from the row's
  `target_db`, and within `psk` from its `mode` (MSK144 rows belong to
  meteor-scatter, the rest to psk-recorder).
- `local` marks a row kept only as a local archive.  In v3.70 the writer
  stores 0 on every row.
- On a `sink.db` from before v3.70, the writer adds both columns with
  `ALTER TABLE ADD COLUMN` (`ensure_columns`).  SQLite rewrites no row, and
  older rows keep `producer = ''`.
- `PENDING_UPLOADS_DDL` gives tools and tests the writer's own schema.

## Architecture
````

In `docs/REQUIREMENTS.md`, lines 73-77 (the first two bullets of `## 3. Non-goals / out of
scope`), replace:
```markdown
- **Producing records.** Writing `pending_uploads` is the recorders' job
  via `sigmond.hamsci_sink.Writer`; hs-uploader only reads. (Owner: each
  recorder + sigmond's hamsci_sink.)
- **Being a service.** It owns no systemd unit, no daemon loop, no
  scheduler — the consuming client drives `pump()` / `pump_until_idle()`.
```
with:
```markdown
- **Choosing what to record.** Each recorder decides which observations
  to store and stores them through the sink writer.  The writer moved
  here in v3.70, as `hs_uploader.sink.Writer`, and sigmond keeps
  `sigmond.hamsci_sink` as a compatibility import, the name clients still
  use.  hs-uploader owns the writer's code and the `pending_uploads`
  schema; each recorder owns its rows.  (Owner: each recorder.)
- **Scheduling a client's in-process sender.** A recorder that ships from
  its own process drives `pump()` / `pump_until_idle()` itself.  The host
  daemon, `hs-uploader serve` (`systemd/hs-uploader.service`), runs only
  the pipelines in `/etc/hs-uploader/pipelines.toml`.
```

In `src/hs_uploader/sources/sqlite.py`, edit three docstrings and no code.  The module docstring,
lines 1-13, replace:
```python
"""SQLite source — reads from `sigmond.hamsci_sink.Writer`'s
`pending_uploads` queue.

This is hs-uploader's database source: it yields `RecordBatch`es
starting strictly after the supplied opaque cursor, with strict
schema-version checking.  `sigmond.hamsci_sink.Writer.from_env()` stages
rows into this queue by default (`/var/lib/sigmond/sink.db`).

Pipeline shape::

    Producer.hamsci_sink.Writer.from_env()  → Writer.flush()
        → pending_uploads (target_db, target_table, schema_version,
                           payload_json, queued_at)
```
with:
```python
"""SQLite source — reads the `pending_uploads` queue that
`hs_uploader.sink.Writer` fills.

This is hs-uploader's database source: it yields `RecordBatch`es
starting strictly after the supplied opaque cursor, with strict
schema-version checking.  `hs_uploader.sink.Writer.from_env()` stages
rows into this queue by default (`/var/lib/sigmond/sink.db`).  The
writer moved into this package in v3.70.  sigmond keeps
`sigmond.hamsci_sink` as a compatibility import, the name clients still
use.

Pipeline shape::

    Producer: hs_uploader.sink.Writer.from_env()  → Writer.flush()
        → pending_uploads (target_db, target_table, schema_version,
                           payload_json, queued_at, producer, local)
```

In `_ConnectionConfig.from_env`'s docstring, line 125, replace:
```python
        that mirrors `sigmond.hamsci_sink.Writer.from_env()`.
```
with:
```python
        that mirrors `hs_uploader.sink.Writer.from_env()`.
```

In `class SqliteSource:`, lines 166-167, replace:
```python
    """Read-side of sigmond.hamsci_sink.Writer's `pending_uploads`
    queue.
```
with:
```python
    """Read side of the `pending_uploads` queue that
    `hs_uploader.sink.Writer` fills (clients import the writer as
    `sigmond.hamsci_sink`, the compatibility import sigmond keeps).
```

Run: `grep -n "no daemon\|sigmond owns\|read-side counterpart" CLAUDE.md README.md`
Expected: no output.

Run:
```bash
sed -n '/^## 3\./,/^## 4\./p' docs/REQUIREMENTS.md | grep "hamsci_sink\|only reads\|no systemd unit"
grep -n "hamsci_sink" src/hs_uploader/sources/sqlite.py
```
Expected: only the lines that name the compatibility import, one from §3 and two from `sqlite.py`:
```
  `sigmond.hamsci_sink` as a compatibility import, the name clients still
9:`sigmond.hamsci_sink` as a compatibility import, the name clients still
171:    `sigmond.hamsci_sink`, the compatibility import sigmond keeps).
```

Then prove that the `sqlite.py` edits touched docstrings alone.  The script compares the syntax
trees of the committed file and the edited one with every docstring blanked:
```bash
.venv/bin/python - <<'EOF'
import ast, subprocess

def code_only(text):
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                first.value.value = ""
    return ast.dump(tree)

path = "src/hs_uploader/sources/sqlite.py"
old = subprocess.run(["git", "show", f"HEAD:{path}"], check=True,
                     capture_output=True, text=True).stdout
assert code_only(old) == code_only(open(path).read()), "code changed"
print("sqlite.py: docstrings only")
EOF
```
Expected: `sqlite.py: docstrings only`.

- [ ] **Step 10: Commit**

```bash
cd /home/mjh/hamsci/repos/hs-uploader
git add src/hs_uploader/sink/__init__.py src/hs_uploader/sink/writer.py \
        tests/test_sink_writer.py CLAUDE.md README.md docs/REQUIREMENTS.md \
        src/hs_uploader/sources/sqlite.py
git commit -m "sink: the sink writer moves in from sigmond, and rows name their producer

hs_uploader.sink holds the sink writer that sigmond's hamsci_sink
carried, copied from sigmond 64cf83f with its surface, logger name and
message prefix unchanged.  When it opens an older sink.db, the writer
adds producer and local with ALTER TABLE ADD COLUMN, treats a rival
client's 'duplicate column name' as success, and updates no existing
row (D12).  Each new row stores the producer its caller names, or else
the one infer_producer gives from target_db and, within psk, the row's
mode; local stays 0.  PENDING_UPLOADS_DDL exports the schema.  The
writer tests move here from sigmond/tests/test_hamsci_sink.py.

CLAUDE.md, README.md, REQUIREMENTS.md §3 and SqliteSource's docstrings
now say that the writer lives here and that sigmond keeps
sigmond.hamsci_sink as a compatibility import.  No code in
sources/sqlite.py changes.

Nothing reads the new columns yet, and clients keep importing
sigmond.hamsci_sink until sigmond prefers this module
(tasks/plan-sink-control.md §10.4 items 1 and 2).

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---


### Task 2: `watermarks.db` gains a schema version and `hs-uploader migrate`

v3.71 copies each send record to its new keys (D9), and that copy must run once, in one process,
before the daemon restarts (D10).  It needs a number that says which migrations a store has
already seen.  This task adds the number, `PRAGMA user_version`, and the one command that raises
it.  v3.70's only migration records version 1 and changes no row and no table, so a station that
runs it sends exactly what it sent before.  Nothing else migrates.  The store's constructor and the
daemon leave the number alone, so `hs-uploader status`, `peek` and `serve --dry-run` never change
the store, and a rolled-back release keeps opening the file as it always did.

**Files:**
- Create: `hs-uploader/src/hs_uploader/watermark/schema.py`
- Modify: `hs-uploader/src/hs_uploader/cli.py`: the module docstring (the `kick` bullet, lines
  10-12); `_build_parser` (after `sp_kick.set_defaults(func=_cmd_kick)`, line 63); `main` (between
  `return daemon.run(...)`, line 93, and `state_path = args.state or default_path()`, line 94); a new
  `_cmd_migrate` above `if __name__ == "__main__":` (line 169)
- Modify: `hs-uploader/README.md` (after line 104, `Optional extras let consuming clients pull only the
  transports they use.`)
- Modify: `hs-uploader/CLAUDE.md`: the Commands block (lines 41-42), the project tree (line 119,
  `sqlite.py             # SqliteWatermarkStore`), and a new section above `## Library lockfile
  policy` (line 170)
- Test: `hs-uploader/tests/test_watermark_schema.py` (create)
- Left alone on purpose: `watermark/sqlite.py`, `watermark/__init__.py`, `daemon.py`.  The store and
  the daemon must not learn the version in v3.70 (D10).

Line numbers name each file at this task's base, Task 1's commit.  Task 1 leaves `cli.py` and
`watermark/` alone, so their numbers match 3e97223.  Task 1 does edit README.md and CLAUDE.md, and
the numbers given here for those two files already count its edits.  (Task 1's other edits, to
`docs/REQUIREMENTS.md` and `sources/sqlite.py`, touch no file this task names.)  Find each edit by
its quoted anchor.

**Interfaces:**
- Consumes: `SqliteWatermarkStore` and `default_path()` from `watermark/sqlite.py`, unchanged.
- Produces, in `hs_uploader.watermark.schema`: `SCHEMA_VERSION = 1`; `BUSY_TIMEOUT_S = 30.0`;
  `class MigrateError(RuntimeError)`; `class JournalRecoveryNeeded(RuntimeError)`;
  `@dataclass(frozen=True) Migration(version: int, summary: str,
  apply: Callable[[sqlite3.Connection], None])` with `.name` → `"N: summary"`;
  `MIGRATIONS: tuple[Migration, ...]`; `@dataclass MigrateReport(from_version: int,
  to_version: int, applied: list[str], pending: list[str])`; `migrate(path: str, *,
  check: bool = False) -> MigrateReport`.  `from_version` holds the file's version when migrate
  looked, and `to_version` the version when it returned (with `check=True`, the same number).
  `migrate` raises `FileNotFoundError` for a missing file, `MigrateError` for a file without a
  `watermarks` table, and `sqlite3.Error` when SQLite refuses (a write lock held past
  `BUSY_TIMEOUT_S`, a file that holds no database).  With `check=True` only, it raises
  `JournalRecoveryNeeded` when a crash left a hot journal that its read-only open cannot roll back.
  An exception from a migration propagates after the rollback.
- Produces, the CLI, `hs-uploader migrate [--db PATH] [--check]`.  `--db` falls back to the global
  `--state`, then to `default_path()`.  It prints, all ASCII:
  - `watermarks.db: version N`: the file's version when the command ends.  `--check` changes
    nothing, so there N names the version the file holds now.
  - `  applied N: <summary>` for each migration it ran, or `  pending N: <summary>` for each one
    `--check` would run.
  - `  nothing to do` when it ran none and none waits.
  - `  newer than this hs-uploader, which knows versions up to 1; left as it stands` for a store
    above `SCHEMA_VERSION`.
  - On a missing file, one line, `watermarks.db: not found at PATH; nothing to migrate`.
  - With `--check` on a file that a crash left with a hot journal, one line, `watermarks.db: a
    crash left a journal to recover; run hs-uploader migrate without --check (or start the
    daemon) to recover it`.
  - On a failure, `hs-uploader migrate: PATH: <ExceptionType>: <message>` on stderr.

  Exit codes: 0 when it finished or found nothing to do (a missing file, a newer store and a
  `--check` that met a hot journal included), 1 on failure, 2 on a usage error (argparse).  An
  hs-uploader that predates `migrate` also exits 2, and prints `argument cmd: invalid choice:
  'migrate'` on stderr.  Task 7 can tell the two apart by that text, and its subprocess timeout
  must exceed `BUSY_TIMEOUT_S`.  Task 9's rig looks for `watermarks.db: version 1`.  Task 10 Step 1
  expects `--check` on a station snapshot to print `watermarks.db: version 0` and `  pending 1: …`
  and to leave the file byte-identical.

**Decisions this task makes** (the contract left them open):
1. **A missing file exits 0 and stays missing.**  Bring-up runs `migrate` before the daemon has
   ever started, so the file may not exist yet.  The daemon creates the store on its first start, as
   `hsupload`.  If a root-run migrate created it first, the daemon could meet bee1's "attempt to
   write a readonly database" again.  For v3.71: a store the daemon creates after migrate ran sits
   at version 0 until the next migrate.  v3.71 must decide what its daemon does with a version-0
   store that holds no rows.
2. **A store newer than the code exits 0 and keeps its number.**  On a rollback from v3.71, Tasks 7
   and 8 run v3.70's migrate on a version-2 store before they restart the daemon.  A failure there
   would block the rollback that D9 promises.  Lowering the number would make v3.71 repeat its key
   copy when the station rolls forward again.
3. **All pending migrations share one `BEGIN IMMEDIATE` transaction.**  A failure part-way leaves
   the file as migrate found it.
4. **A file without a `watermarks` table exits 1 untouched.**  Otherwise `sink.db`, passed by
   mistake, would gain a version.
5. **A run with nothing to do takes no write lock and writes no byte**, unless a crash left a hot
   journal for it to recover (decision 7).  `--check` reads through a `mode=ro` URI, as
   `backlog.py` already does; a real run opens a `mode=rw` URI.  `Path.as_uri()` quotes both, so
   a space, `?` or `#` in the path survives, and neither mode creates a file.
6. **The CLI never constructs `SqliteWatermarkStore` for `migrate`.**  The constructor creates a
   missing file and changes the file's mode, and `--check` must write nothing.
7. **`migrate` opens the file read-write from its first read; `--check` stays read-only.**  Ruled by
   the plan's controller on 2026-10-07.  A process that dies in the middle of a write leaves a hot
   journal, `watermarks.db-journal`, beside the store.  SQLite rolls it back on the first read
   through a connection allowed to write.  So the real run recovers it exactly as the daemon's own
   open would, and then migrates.  A read-only connection cannot roll it back, and refuses to read
   at all: "attempt to write a readonly database" (`SQLITE_READONLY_ROLLBACK`).  A `--check` that
   meets one prints `watermarks.db: a crash left a journal to recover; run hs-uploader migrate
   without --check (or start the daemon) to recover it`, writes nothing, and exits 0.  It found the
   store's state, not a fault in migrate.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_watermark_schema.py`:

```python
"""watermarks.db schema version and `hs-uploader migrate`
(sigmond/tasks/plan-sink-control.md §10.4 item 3, D10).

Every fixture store gets built through SqliteWatermarkStore, the code that
writes the files stations hold today, so a "v0 file" here means exactly
what ND and B4 carry: user_version 0, rows in every table.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from hs_uploader.cli import main
from hs_uploader.watermark import SqliteWatermarkStore
from hs_uploader.watermark import schema

SRC = Path(__file__).resolve().parent.parent / "src"

PSK_KEY = ("sqlite:psk.spots", "pskreporter-tcp:report.pskreporter.info:4739",
           "psk.spots")
WSPR_KEY = ("sqlite:wspr.cycle",
            "wsprdaemon-tar-sftp:gw1.wsprdaemon.org,gw2.wsprdaemon.org",
            "wspr.spots")
GRAPE_KEY = ("files:/var/lib/timestd/upload", "psws-grape", "grape.obs")

# SQLite rewrites only these header bytes when a commit changes nothing but
# user_version: the change counter (24-27), user_version (60-63), and the
# version-valid-for number and library version (92-99).
_HEADER_BYTES_A_VERSION_BUMP_TOUCHES = (
    set(range(24, 28)) | set(range(60, 64)) | set(range(92, 100))
)


def _v0_store(directory: Path, name: str = "watermarks.db") -> Path:
    """A store as v3.69 leaves it: version 0, rows in all four tables."""
    directory.mkdir(parents=True, exist_ok=True)
    db = directory / name
    s = SqliteWatermarkStore(db)
    s.advance_cursor(*PSK_KEY, cursor=b"1204",
                     last_ack="2026-10-07T12:00:00+00:00")
    s.advance_cursor(*WSPR_KEY, cursor=b"2026-10-07T11:58:00Z",
                     last_ack="2026-10-07T12:00:30+00:00")
    s.advance_cursor(*GRAPE_KEY, cursor=b"\x00\xff\x10binary",
                     last_ack="2026-10-07T01:10:00+00:00")
    for i, outcome in enumerate(("acked", "retry-later", "acked")):
        s.record_attempt(
            ts=f"2026-10-07T12:0{i}:00+00:00",
            source_id=PSK_KEY[0], dest_id=PSK_KEY[1], table=PSK_KEY[2],
            outcome=outcome, records=None if outcome != "acked" else 40 + i,
            bytes_=None if outcome != "acked" else 900 + i,
            error="timed out" if outcome != "acked" else None,
        )
    s.enqueue_deliverable(
        pipeline="psk-pskreporter", payload_blob=b"\x00\x0aIPFIX-frame",
        enqueued_at="2026-10-07T12:01:00+00:00",
        next_attempt_at="2026-10-07T12:06:00+00:00",
        source_id=PSK_KEY[0], dest_id=PSK_KEY[1], table=PSK_KEY[2],
        cursor_after=b"1205", commit_token=b"commit-1205",
    )
    s.enqueue_deliverable(
        pipeline="grape-psws", payload_blob=b"OBS2026-10-06T00-00",
        enqueued_at="2026-10-07T01:10:00+00:00",
        next_attempt_at="2026-10-07T01:40:00+00:00",
    )
    s.send_to_dead_letter(ts="2026-10-06T23:00:00+00:00",
                          pipeline="mag-psws", payload_blob=b"PK\x03\x04zip",
                          final_error="550 permission denied")
    s.close()
    return db


def _version(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _set_version(db: Path, n: int) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA user_version = %d" % n)
        conn.commit()
    finally:
        conn.close()


def _rows(db: Path) -> dict:
    """Every row of every table, BLOBs as bytes, plus the schema itself."""
    conn = sqlite3.connect(db)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        out = {n: conn.execute(f'SELECT * FROM "{n}" ORDER BY rowid').fetchall()
               for n in names}
        out["sqlite_master"] = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name"
        ).fetchall()
        return out
    finally:
        conn.close()


def _cli(*argv: str) -> subprocess.CompletedProcess:
    """`hs-uploader` as a real process, for its true exit status."""
    env = dict(os.environ, PYTHONPATH=str(SRC))
    return subprocess.run([sys.executable, "-m", "hs_uploader.cli", *argv],
                          capture_output=True, text=True, env=env, timeout=120)


def _crash_copy(directory: Path) -> tuple[Path, dict]:
    """A v0 store that a writer died inside.  Its cache spilled pages into
    the file before it died, so watermarks.db-journal holds the pages it
    overwrote: a hot journal.  Returns the path and every row as it stood
    before the crash."""
    db = _v0_store(directory)
    rows_before = _rows(db)
    size_before = db.stat().st_size
    crash = textwrap.dedent(f"""
        import os, sqlite3
        conn = sqlite3.connect({str(db)!r}, isolation_level=None)
        conn.execute("PRAGMA cache_size = 10")    # spill into the file early
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE watermarks SET cursor = x'00'")
        conn.execute("DELETE FROM deliverables")
        conn.execute("CREATE TABLE spill (x BLOB)")
        conn.executemany("INSERT INTO spill VALUES (?)",
                         [(os.urandom(1000),) for _ in range(500)])
        os._exit(1)                               # die before COMMIT
    """)
    subprocess.run([sys.executable, "-c", crash], timeout=60)
    assert Path(f"{db}-journal").stat().st_size > 0
    assert db.stat().st_size > size_before      # the spill reached the file
    return db, rows_before


# ---- the migration list ----


def test_versions_run_from_1_to_schema_version_without_gaps():
    assert [m.version for m in schema.MIGRATIONS] == list(
        range(1, schema.SCHEMA_VERSION + 1))
    assert schema.SCHEMA_VERSION == 1


def test_busy_timeout_constant_is_at_least_30_s():
    assert schema.BUSY_TIMEOUT_S >= 30


# ---- migrate ----


def test_fresh_store_file_migrates_to_version_1(tmp_path, capsys):
    db = tmp_path / "watermarks.db"
    SqliteWatermarkStore(db).close()
    assert _version(db) == 0

    rc = main(["migrate", "--db", str(db)])

    assert rc == 0
    assert _version(db) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "watermarks.db: version 1"
    assert out[1:] == [
        "  applied 1: record the schema version; changes no row and no table"]


def test_migrate_keeps_every_row_of_a_v0_store(tmp_path):
    db = _v0_store(tmp_path)
    rows_before = _rows(db)
    bytes_before = db.read_bytes()

    report = schema.migrate(str(db))

    assert (report.from_version, report.to_version) == (0, 1)
    assert report.applied == [schema.MIGRATIONS[0].name]
    assert report.pending == []
    assert _rows(db) == rows_before
    bytes_after = db.read_bytes()
    assert len(bytes_after) == len(bytes_before)
    changed = {i for i, (a, b) in enumerate(zip(bytes_before, bytes_after))
               if a != b}
    # Every page past the 100-byte file header stays byte-identical.
    assert changed <= _HEADER_BYTES_A_VERSION_BUMP_TOUCHES
    assert int.from_bytes(bytes_after[60:64], "big") == 1


def test_second_run_changes_nothing(tmp_path, capsys):
    db = _v0_store(tmp_path)
    assert main(["migrate", "--db", str(db)]) == 0
    capsys.readouterr()
    bytes_before = db.read_bytes()
    mtime_before = db.stat().st_mtime_ns

    rc = main(["migrate", "--db", str(db)])

    assert rc == 0
    assert db.read_bytes() == bytes_before
    assert db.stat().st_mtime_ns == mtime_before
    assert capsys.readouterr().out.splitlines() == [
        "watermarks.db: version 1", "  nothing to do"]
    report = schema.migrate(str(db))
    assert (report.from_version, report.to_version) == (1, 1)
    assert report.applied == [] and report.pending == []


def test_check_writes_nothing(tmp_path, capsys):
    # A space and a '#' in the path prove the read-only URI quotes it.
    db = _v0_store(tmp_path / "state dir#1")
    bytes_before = db.read_bytes()
    mtime_before = db.stat().st_mtime_ns
    listing_before = sorted(p.name for p in db.parent.iterdir())

    rc = main(["migrate", "--db", str(db), "--check"])

    assert rc == 0
    assert capsys.readouterr().out.splitlines() == [
        "watermarks.db: version 0",
        "  pending 1: record the schema version; changes no row and no table",
    ]
    assert db.read_bytes() == bytes_before
    assert db.stat().st_mtime_ns == mtime_before
    assert sorted(p.name for p in db.parent.iterdir()) == listing_before
    report = schema.migrate(str(db), check=True)
    assert (report.from_version, report.to_version) == (0, 0)
    assert report.applied == []
    assert report.pending == [schema.MIGRATIONS[0].name]


def test_a_crash_copy_migrates_and_keeps_every_row(tmp_path, capsys):
    # migrate opens read-write from its first read, so it rolls the hot
    # journal back exactly as the daemon's own open would, then migrates.
    # A read-only first read would fail: "attempt to write a readonly
    # database".
    db, rows_before = _crash_copy(tmp_path)

    rc = main(["migrate", "--db", str(db)])

    assert rc == 0
    assert capsys.readouterr().out.splitlines() == [
        "watermarks.db: version 1",
        "  applied 1: record the schema version; changes no row and no table",
    ]
    assert not Path(f"{db}-journal").exists()
    assert _version(db) == 1
    assert _rows(db) == rows_before


def test_check_on_a_crash_copy_says_so_and_writes_nothing(tmp_path, capsys):
    db, _ = _crash_copy(tmp_path)
    journal = Path(f"{db}-journal")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (db, journal)}
    listing_before = sorted(p.name for p in db.parent.iterdir())

    rc = main(["migrate", "--db", str(db), "--check"])

    assert rc == 0
    assert capsys.readouterr().out.splitlines() == [
        "watermarks.db: a crash left a journal to recover; run hs-uploader "
        "migrate without --check (or start the daemon) to recover it"]
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in (db, journal)} == before
    assert sorted(p.name for p in db.parent.iterdir()) == listing_before
    with pytest.raises(schema.JournalRecoveryNeeded):
        schema.migrate(str(db), check=True)


def test_migrate_waits_for_a_concurrent_writer(tmp_path):
    db = _v0_store(tmp_path)
    holder = sqlite3.connect(db, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        "INSERT INTO watermarks VALUES('sqlite:hfdl.spots','x','hfdl.spots',"
        "x'01','2026-10-07T12:00:00+00:00')")

    def release():
        time.sleep(1.0)
        holder.execute("COMMIT")

    t = threading.Thread(target=release)
    t.start()
    started = time.monotonic()
    try:
        report = schema.migrate(str(db))
    finally:
        t.join()
        holder.close()
    waited = time.monotonic() - started

    assert waited >= 0.9           # it waited for the lock ...
    assert report.to_version == 1  # ... and then finished, rather than failing
    assert _version(db) == 1
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM watermarks WHERE source_id='sqlite:hfdl.spots'"
        ).fetchone()[0] == 1       # the other writer's commit survived
    finally:
        conn.close()


def test_every_connection_waits_at_least_30_s(tmp_path, monkeypatch):
    db = _v0_store(tmp_path)
    seen = []
    real_connect = sqlite3.connect

    def spy(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        mode = str(args[0]).rsplit("?mode=", 1)[-1]
        seen.append((mode, conn.execute("PRAGMA busy_timeout").fetchone()[0]))
        return conn

    monkeypatch.setattr(schema.sqlite3, "connect", spy)
    schema.migrate(str(db), check=True)
    schema.migrate(str(db))

    # --check reads read-only.  A real run opens one read-write connection
    # from its first read, so a hot journal rolls back before it looks.
    assert [mode for mode, _ in seen] == ["ro", "rw"]
    assert all(ms >= 30_000 for _, ms in seen)


def test_a_lock_held_past_the_timeout_exits_1_and_changes_nothing(
        tmp_path, monkeypatch, capsys):
    db = _v0_store(tmp_path)
    rows_before = _rows(db)
    monkeypatch.setattr(schema, "BUSY_TIMEOUT_S", 0.2)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        rc = main(["migrate", "--db", str(db)])
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert rc == 1
    assert "database is locked" in capsys.readouterr().err
    assert _version(db) == 0
    assert _rows(db) == rows_before


def test_a_failing_migration_rolls_back_every_step(tmp_path, monkeypatch):
    db = _v0_store(tmp_path)
    rows_before = _rows(db)

    def boom(conn):
        conn.execute("DELETE FROM watermarks")
        raise RuntimeError("boom")

    monkeypatch.setattr(schema, "MIGRATIONS", (
        *schema.MIGRATIONS, schema.Migration(2, "fails", boom)))

    with pytest.raises(RuntimeError, match="boom"):
        schema.migrate(str(db))

    assert _version(db) == 0       # step 1 rolled back with step 2
    assert _rows(db) == rows_before


def test_missing_file_exits_0_and_creates_nothing(tmp_path, capsys):
    db = tmp_path / "absent" / "watermarks.db"

    rc = main(["migrate", "--db", str(db)])

    assert rc == 0
    assert capsys.readouterr().out.splitlines() == [
        f"watermarks.db: not found at {db}; nothing to migrate"]
    assert not db.parent.exists()
    with pytest.raises(FileNotFoundError):
        schema.migrate(str(db))


def test_a_database_without_a_watermarks_table_exits_1_untouched(
        tmp_path, capsys):
    # sink.db passed by mistake must not gain a version.
    db = tmp_path / "sink.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE pending_uploads (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    bytes_before = db.read_bytes()

    rc = main(["migrate", "--db", str(db)])

    assert rc == 1
    assert "not an hs-uploader watermark store" in capsys.readouterr().err
    assert db.read_bytes() == bytes_before


def test_not_a_database_exits_1(tmp_path, capsys):
    db = tmp_path / "watermarks.db"
    db.write_bytes(b"this is not sqlite\n" * 10)

    rc = main(["migrate", "--db", str(db)])

    assert rc == 1
    assert "hs-uploader migrate:" in capsys.readouterr().err


def test_a_newer_store_is_left_alone_and_exits_0(tmp_path, capsys):
    # A station rolled back from a release whose migrate went further.
    db = _v0_store(tmp_path)
    _set_version(db, schema.SCHEMA_VERSION + 1)
    bytes_before = db.read_bytes()

    rc = main(["migrate", "--db", str(db)])

    assert rc == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"watermarks.db: version {schema.SCHEMA_VERSION + 1}"
    assert "newer than this hs-uploader" in out[1]
    assert db.read_bytes() == bytes_before


def test_db_defaults_to_the_state_option(tmp_path, capsys):
    db = _v0_store(tmp_path)

    rc = main(["--state", str(db), "migrate"])

    assert rc == 0
    assert _version(db) == 1


def test_usage_error_exits_2(tmp_path):
    with pytest.raises(SystemExit) as caught:
        main(["migrate", "--no-such-option"])
    assert caught.value.code == 2


def test_exit_codes_from_a_real_process(tmp_path):
    db = _v0_store(tmp_path)
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not sqlite\n" * 10)

    first = _cli("migrate", "--db", str(db))
    again = _cli("migrate", "--db", str(db))
    check = _cli("migrate", "--db", str(db), "--check")
    failed = _cli("migrate", "--db", str(junk))
    usage = _cli("migrate", "--bogus")

    assert first.returncode == 0, first.stderr
    assert first.stdout.splitlines()[0] == "watermarks.db: version 1"
    assert again.returncode == 0 and "nothing to do" in again.stdout
    assert check.returncode == 0 and check.stdout.splitlines() == [
        "watermarks.db: version 1", "  nothing to do"]
    assert failed.returncode == 1
    assert usage.returncode == 2 and "unrecognized arguments" in usage.stderr


# ---- the store never migrates; old code reads a migrated file ----


def test_store_constructor_leaves_user_version_alone(tmp_path):
    db = tmp_path / "watermarks.db"
    SqliteWatermarkStore(db).close()
    assert _version(db) == 0       # a new store does not migrate itself

    old = _v0_store(tmp_path / "old")
    s = SqliteWatermarkStore(old)
    s.advance_cursor(*PSK_KEY, cursor=b"1300",
                     last_ack="2026-10-07T13:00:00+00:00")
    s.close()
    assert _version(old) == 0      # nor does reopening a v0 store

    _set_version(old, 1)
    s = SqliteWatermarkStore(old)
    s.advance_cursor(*PSK_KEY, cursor=b"1301",
                     last_ack="2026-10-07T13:00:30+00:00")
    s.close()
    assert _version(old) == 1      # and reopening never resets the number


def test_current_store_opens_a_version_1_file(tmp_path):
    # Rollback safety.  This commit leaves watermark/sqlite.py as v3.69
    # shipped it, so here the store IS the v3.69 store code, and it must
    # carry on against a file that v3.70's migrate has stamped.
    db = _v0_store(tmp_path)
    schema.migrate(str(db))
    assert _version(db) == 1

    s = SqliteWatermarkStore(db)
    try:
        assert s.get_cursor(*PSK_KEY) == b"1204"
        assert s.get_cursor(*GRAPE_KEY) == b"\x00\xff\x10binary"
        d = s.pop_due_deliverable("psk-pskreporter",
                                  now="2026-10-07T13:00:00+00:00")
        assert d is not None
        assert (d.cursor_after, d.commit_token) == (b"1205", b"commit-1205")
        s.requeue_deliverable(d)
        s.advance_cursor(*PSK_KEY, cursor=b"1300",
                         last_ack="2026-10-07T13:00:05+00:00")
        s.record_attempt(ts="2026-10-07T13:00:05+00:00",
                         source_id=PSK_KEY[0], dest_id=PSK_KEY[1],
                         table=PSK_KEY[2], outcome="acked", records=3,
                         bytes_=120, error=None)
        assert s.get_cursor(*PSK_KEY) == b"1300"
        assert s.deliverable_count() == 2
        assert s.dead_letter_count() == 1
        assert len(s.all_cursors()) == 3
    finally:
        s.close()
    assert _version(db) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /home/mjh/hamsci/repos/hs-uploader && .venv/bin/pytest tests/test_watermark_schema.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'schema' from 'hs_uploader.watermark'`.

- [ ] **Step 3: Create `src/hs_uploader/watermark/schema.py`**

```python
"""Schema version of the watermark store, and the migrations that raise it.

``PRAGMA user_version`` in ``watermarks.db`` holds the store's schema
version.  A file that has never met ``migrate`` reads 0.  ``migrate()``
runs every migration above the file's version, in order, inside one
``BEGIN IMMEDIATE`` transaction, so a failure part-way leaves the file as
it found it.

``migrate()`` opens the file read-write from its first read, as the
daemon's own open does.  A process that dies in the middle of a write
leaves a hot journal (``watermarks.db-journal``) beside the file, and
SQLite rolls it back on the first read through a connection allowed to
write.  A read-only connection cannot roll it back, so it refuses to read
at all.  Only ``check=True`` reads read-only; on a hot journal it raises
``JournalRecoveryNeeded`` and leaves both files as it found them.

Only ``hs-uploader migrate`` calls ``migrate()``.  Neither
``SqliteWatermarkStore``'s constructor nor the daemon at start reads or
writes the version (D10 in sigmond/tasks/plan-sink-control.md): sigmond
runs the migration once every component's code sits in place and before
it restarts the daemon, and a store still on an older version keeps
working as it stands.

Version 1 (v3.70) changes no row and no table.  It only records that the
store now carries a version, so v3.71's key migration has a number to
start from.  Never edit a migration that has shipped; append a new one
and raise ``SCHEMA_VERSION`` to match.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

__all__ = [
    "BUSY_TIMEOUT_S",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "JournalRecoveryNeeded",
    "MigrateError",
    "MigrateReport",
    "Migration",
    "migrate",
]

SCHEMA_VERSION = 1

# How long migrate waits for another process's write lock.  The daemon and
# the in-process senders each hold it for milliseconds; Python's default of
# 5 s would turn an unlucky moment into a failed update.
BUSY_TIMEOUT_S = 30.0


class MigrateError(RuntimeError):
    """The file opened, but it holds no watermark store."""


class JournalRecoveryNeeded(RuntimeError):
    """A read-only check met a hot journal.  A crash left a write half
    done, and only a connection allowed to write can roll it back."""


@dataclass(frozen=True)
class Migration:
    version: int
    summary: str
    apply: Callable[[sqlite3.Connection], None]

    @property
    def name(self) -> str:
        return f"{self.version}: {self.summary}"


@dataclass
class MigrateReport:
    from_version: int          # the file's version when migrate looked
    to_version: int            # the file's version when migrate returned
    applied: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)


def _record_version_only(conn: sqlite3.Connection) -> None:
    """Version 1 changes nothing; the runner records the number."""


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "record the schema version; changes no row and no table",
              _record_version_only),
)


def _connect(db: Path, mode: str) -> sqlite3.Connection:
    """Open ``db`` with ``mode`` ``ro`` or ``rw``; neither creates a file.
    as_uri() percent-encodes a space, '?' or '#' in the path."""
    uri = f"{db.resolve().as_uri()}?mode={mode}"
    return sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_S,
                           isolation_level=None)


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _inspect(conn: sqlite3.Connection, db: Path) -> int:
    """The file's version.  Refuse a file that holds no ``watermarks``
    table, such as ``sink.db`` passed by mistake, before anything writes
    to it.  Through a read-write connection this first read also rolls
    back a hot journal."""
    found = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='watermarks'"
    ).fetchone()
    if found is None:
        raise MigrateError(
            f"{db} holds no watermarks table, so it is not an "
            "hs-uploader watermark store"
        )
    return _user_version(conn)


def _hot_journal(exc: sqlite3.OperationalError, db: Path) -> bool:
    """True when a read-only read failed because a hot journal waits to
    be rolled back."""
    name = getattr(exc, "sqlite_errorname", None)    # Python 3.11 and later
    if name is not None:
        return name == "SQLITE_READONLY_ROLLBACK"
    return "readonly" in str(exc) and Path(f"{db}-journal").exists()


def _check(db: Path) -> MigrateReport:
    conn = _connect(db, "ro")
    try:
        current = _inspect(conn, db)
    except sqlite3.OperationalError as exc:
        if _hot_journal(exc, db):
            raise JournalRecoveryNeeded(
                f"a crash left {db}-journal to recover") from exc
        raise
    finally:
        conn.close()
    return MigrateReport(from_version=current, to_version=current,
                         pending=[m.name for m in MIGRATIONS
                                  if m.version > current])


def migrate(path: str, *, check: bool = False) -> MigrateReport:
    """Bring the store at ``path`` to ``SCHEMA_VERSION``.

    ``check=True`` opens the file read-only and reports what would run.  A
    store already at, or above, this code's version needs nothing and gets
    no write: a station rolled back from a newer release keeps its newer
    number.  Raises FileNotFoundError for a missing file (migrate never
    creates the store), MigrateError for a file that holds no store, and
    sqlite3.Error when SQLite refuses, for example a write lock held past
    ``BUSY_TIMEOUT_S``.  With ``check=True`` it raises
    JournalRecoveryNeeded on a hot journal and changes nothing.
    """
    db = Path(path)
    if not db.is_file():
        raise FileNotFoundError(f"no store file at {db}")
    if check:
        return _check(db)

    # Read-write from the first read: a hot journal left by a crash rolls
    # back here, as it would when the daemon opens the store.
    conn = _connect(db, "rw")
    try:
        current = _inspect(conn, db)
        if not any(m.version > current for m in MIGRATIONS):
            # Nothing to do: no write lock taken, no byte written.
            return MigrateReport(from_version=current, to_version=current)
        # IMMEDIATE takes the write lock now, waiting out a concurrent
        # writer, so no other process slips a write between our read of
        # the version and our update of it.
        conn.execute("BEGIN IMMEDIATE")
        try:
            start = _user_version(conn)   # again, now under the lock
            version = start
            applied: list[str] = []
            for m in MIGRATIONS:
                if m.version <= version:
                    continue
                m.apply(conn)
                conn.execute("PRAGMA user_version = %d" % m.version)
                version = m.version
                applied.append(m.name)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return MigrateReport(from_version=start, to_version=version, applied=applied)
```

- [ ] **Step 4: Run the tests; the library passes, the CLI does not exist yet**

Run: `.venv/bin/pytest tests/test_watermark_schema.py -v`
Expected: 9 passed, 12 failed.  Eleven CLI tests, the two crash-copy tests among them, fail with
`SystemExit: 2` (`argument cmd: invalid choice: 'migrate'`).  `test_exit_codes_from_a_real_process`
fails with `assert 2 == 0`.  `test_usage_error_exits_2` already passes, because argparse rejects the unknown
subcommand with 2 as well; it pins the finished command's usage exit.

- [ ] **Step 5: Wire `migrate` into `src/hs_uploader/cli.py`**

(a) In the module docstring, find (lines 10-12):

```python
* ``hs-uploader kick``   — bump every deliverable's
  ``next_attempt_at`` to now, so the next ``pump`` retries
  immediately instead of waiting out the backoff.
```

and add directly below it:

```python
* ``hs-uploader migrate [--db PATH] [--check]`` — bring watermarks.db
  to this release's schema version (``PRAGMA user_version``).
  ``--check`` reports the version and what would run, and writes
  nothing.  Exit 0 done or nothing to do, 1 failure, 2 usage error.
```

(b) In `_build_parser`, find (line 63):

```python
    sp_kick.set_defaults(func=_cmd_kick)
```

and add directly below it, before `sp_serve = sub.add_parser(`:

```python

    sp_migrate = sub.add_parser(
        "migrate",
        help="Bring watermarks.db to this release's schema version.  Run it "
             "once the new code sits in place, before the daemon restarts.",
    )
    sp_migrate.add_argument(
        "--db",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to watermarks.db (default: --state, else "
             f"{default_path()}).",
    )
    sp_migrate.add_argument(
        "--check",
        action="store_true",
        help="Report the version and the pending migrations; write nothing.",
    )
    sp_migrate.set_defaults(func=None)
```

(c) In `main`, find (lines 93-94):

```python
        return daemon.run(args.manifest, dry_run=args.dry_run, once=args.once)
    state_path = args.state or default_path()
```

and replace it with:

```python
        return daemon.run(args.manifest, dry_run=args.dry_run, once=args.once)
    # `migrate` opens the file itself.  Constructing the store would create
    # a missing file and change its mode, and `--check` writes nothing.
    if args.cmd == "migrate":
        return _cmd_migrate(args)
    state_path = args.state or default_path()
```

(d) Find (line 169):

```python
if __name__ == "__main__":
    sys.exit(main())
```

and insert above it:

```python
def _cmd_migrate(args) -> int:
    from .watermark import schema

    path = args.db or args.state or default_path()
    if not path.exists():
        # The daemon creates the store on its first start, as hsupload.  A
        # migrate run as root must not create it first.
        print(f"watermarks.db: not found at {path}; nothing to migrate")
        return 0
    try:
        report = schema.migrate(str(path), check=args.check)
    except schema.JournalRecoveryNeeded:
        # Only --check meets this.  Its read-only open cannot roll back
        # what a crash left half written, and it leaves the file alone.
        print("watermarks.db: a crash left a journal to recover; run "
              "hs-uploader migrate without --check (or start the daemon) "
              "to recover it")
        return 0
    except Exception as exc:  # noqa: BLE001 -- any failure means exit 1
        print(f"hs-uploader migrate: {path}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    print(f"watermarks.db: version {report.to_version}")
    for name in report.applied:
        print(f"  applied {name}")
    for name in report.pending:
        print(f"  pending {name}")
    if report.to_version > schema.SCHEMA_VERSION:
        print(f"  newer than this hs-uploader, which knows versions up to "
              f"{schema.SCHEMA_VERSION}; left as it stands")
    elif not report.applied and not report.pending:
        print("  nothing to do")
    return 0


```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_watermark_schema.py -v`
Expected: 21 passed (the dry run took 1.6 s; the concurrent-writer test holds the lock for 1 s).

Run: `.venv/bin/pytest -q`
Expected: `358 passed, 1 skipped` (Task 1's 337 passed, 1 skipped, plus these 21).

Run: `.venv/bin/hs-uploader migrate --help`
Expected: `usage: hs-uploader migrate [-h] [--db PATH] [--check]`.

- [ ] **Step 7: Watch five guards fail, then restore**

Each mutation must turn exactly the named tests red.  Restore the file after each one.

```bash
cd /home/mjh/hamsci/repos/hs-uploader
F=src/hs_uploader/watermark/schema.py
cp $F $F.keep
sed -i 's/conn.execute("BEGIN IMMEDIATE")/conn.execute("BEGIN")/' $F
.venv/bin/pytest tests/test_watermark_schema.py -q   # 1 failed: test_migrate_waits_for_a_concurrent_writer
cp $F.keep $F
sed -i 's/uri=True, timeout=BUSY_TIMEOUT_S,$/uri=True,/' $F
.venv/bin/pytest tests/test_watermark_schema.py -q   # 1 failed: test_every_connection_waits_at_least_30_s (about 6 s)
cp $F.keep $F
sed -i 's/^    if check:$/    if False:/' $F
.venv/bin/pytest tests/test_watermark_schema.py -q   # 3 failed: test_check_writes_nothing,
                                                     # test_check_on_a_crash_copy_says_so_and_writes_nothing,
                                                     # test_every_connection_waits_at_least_30_s
cp $F.keep $F
sed -i 's/^    conn = _connect(db, "rw")$/    _inspect(_connect(db, "ro"), db)\n    conn = _connect(db, "rw")/' $F
.venv/bin/pytest tests/test_watermark_schema.py -q   # 2 failed: test_a_crash_copy_migrates_and_keeps_every_row,
                                                     # test_every_connection_waits_at_least_30_s
cp $F.keep $F
sed -i 's/^        if _hot_journal(exc, db):$/        if False:/' $F
.venv/bin/pytest tests/test_watermark_schema.py -q   # 1 failed: test_check_on_a_crash_copy_says_so_and_writes_nothing
mv $F.keep $F
.venv/bin/pytest tests/test_watermark_schema.py -q   # 21 passed
```

A deferred `BEGIN` fails the first, because it reads the version under a shared lock.  SQLite then
refuses the upgrade to a write lock at once, without waiting, to avoid a deadlock.  The second
mutation falls back to Python's 5 s default.  The third lets `--check` run the migrations.  The
fourth restores the draft's read-only first read on a real run, and the crash copy then fails with
"attempt to write a readonly database".  The fifth turns a `--check` on a hot journal into exit 1.
The dry run also killed four more mutations: a commit after each migration, an unquoted `file:`
URI, a constructor that migrates, and a run that lowers a newer store's number.

- [ ] **Step 8: Document `migrate` in README.md and CLAUDE.md**

In `README.md`, find (line 104):

```markdown
Optional extras let consuming clients pull only the transports they use.
```

and insert this section between it and `## License`, with one blank line on each side:

````markdown
## The store's schema version: `hs-uploader migrate`

The watermark store, `/var/lib/hs-uploader/watermarks.db`, records its
schema version in `PRAGMA user_version`.  `hs-uploader migrate` brings
the store up to the version this release knows:

```bash
hs-uploader migrate --check     # print the version and what would run; write nothing
hs-uploader migrate             # run each pending migration once, in order
hs-uploader migrate --db PATH   # act on another file (default: --state, else the path above)
```

Run it once the new code sits in place and before the daemon restarts.
Neither the daemon nor the store migrates on its own.  Version 1, which
v3.70 introduced, changes no row and no table.  On a missing file,
`migrate` says so, creates nothing and leaves the file for the daemon to
create.  A crash in the middle of a write can leave
`watermarks.db-journal` beside the store.  `migrate` rolls that journal
back first, as the daemon does when it opens the store.  `--check` cannot
roll it back read-only, so it says so, changes nothing and exits 0.  The
command exits 0 when it finished or found nothing to do, 1 when it
failed, and 2 on a usage error.
````

In `CLAUDE.md`, find (lines 41-42, inside the Commands block):

```bash
# CLI (operator inspector — not the consumer integration path)
hs-uploader --help
```

and add directly below `hs-uploader --help`:

```bash
hs-uploader migrate --check    # watermarks.db schema version; writes nothing
hs-uploader migrate            # run pending migrations (sigmond does this on update)
```

Find (line 119, in the project tree):

```text
    sqlite.py             # SqliteWatermarkStore
```

and add directly below it:

```text
    schema.py             # PRAGMA user_version + migrate() (`hs-uploader migrate`)
```

Find (line 170):

```markdown
## Library lockfile policy
```

and insert above it (the block ends with the blank line that separates the two sections):

```markdown
## watermarks.db schema version (`hs-uploader migrate`)

`PRAGMA user_version` in `watermarks.db` holds the store's schema
version.  `src/hs_uploader/watermark/schema.py` owns it: `SCHEMA_VERSION`,
the ordered `MIGRATIONS`, and `migrate(path, *, check=False) -> MigrateReport`.

- Only `hs-uploader migrate` migrates.  `SqliteWatermarkStore`'s
  constructor never reads or writes `user_version`, and the daemon never
  migrates while it starts (D10 in sigmond/tasks/plan-sink-control.md).
  From v3.70, sigmond runs `hs-uploader migrate` once every component's
  code sits in place and before it starts or restarts the daemon.
- `migrate` runs every pending migration, in order, inside one
  `BEGIN IMMEDIATE` transaction.  It waits up to `BUSY_TIMEOUT_S` (30 s)
  for another process's write lock.  A failure rolls back every step.
- `migrate` opens the file read-write from its first read.  A crash in
  the middle of a write leaves a hot journal (`watermarks.db-journal`),
  and that first read rolls it back, exactly as the daemon's own open
  would.  A read-only open cannot roll it back and refuses to read.
- `--check` opens the file read-only and writes nothing.  On a hot
  journal it prints `watermarks.db: a crash left a journal to recover;
  run hs-uploader migrate without --check (or start the daemon) to
  recover it` and exits 0 (`JournalRecoveryNeeded` in the library).
- A missing file: exit 0, and migrate creates nothing.  The daemon
  creates the store on its first start, as `hsupload`; a migrate run as
  root must not create it first.
- A file without a `watermarks` table, such as `sink.db` passed by
  mistake: exit 1, file untouched.
- A store newer than the code, after a rollback: exit 0, and migrate
  leaves the number as it stands.  Older store code opens a newer file
  unchanged.
- Version 1 (v3.70) changes no row and no table.  It records the number
  and nothing else.
- To add a migration, append a `Migration` to `MIGRATIONS` and raise
  `SCHEMA_VERSION` to match.  Never edit a migration that has shipped.
- Exit codes: 0 done or nothing to do, 1 failure, 2 usage error.  An
  hs-uploader that predates `migrate` also exits 2, and prints
  `invalid choice: 'migrate'` on stderr.

```

Run: `.venv/bin/pytest -q`
Expected: the same count as Step 6; the docs touch no test.

- [ ] **Step 9: Commit**

```bash
cd /home/mjh/hamsci/repos/hs-uploader
git add src/hs_uploader/watermark/schema.py src/hs_uploader/cli.py \
        tests/test_watermark_schema.py README.md CLAUDE.md
git commit -m "watermark: schema version in PRAGMA user_version, and hs-uploader migrate

watermarks.db records its schema version in PRAGMA user_version.
hs-uploader migrate [--db PATH] [--check] runs each pending migration
once, in order, inside one BEGIN IMMEDIATE transaction, and waits up to
30 s for another writer.  v3.70's only migration records version 1 and
changes no row and no table.  The store's constructor and the daemon
never migrate (D10); sigmond runs the command before it restarts the
daemon.

A missing file exits 0 and stays missing, because the daemon creates the
store as hsupload.  A store newer than the code keeps its number, so a
rolled-back release runs on it unchanged (D9).  migrate opens the file
read-write from its first read, so it rolls back a hot journal left by a
crash, as the daemon's own open would.  --check stays read-only; on a hot
journal it says so, writes nothing and exits 0.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---


### Task 3: each source orders its own send records, and the store warns on a backward write

hs-uploader keeps each send record as opaque bytes and overwrites it on every acknowledgement
(`src/hs_uploader/watermark/sqlite.py:164-180`).  Nothing checks the direction, and a byte compare could not:
it ranks `b"999"` above `b"1000"`.  So each source learns to compare two of its own records, in the order its
own query uses (spec §6.2): integer ids for SqliteSource, integer nanoseconds for a FileTree spool kept after
sending (legacy float-second records read through `_decode_keep_cursor`), text for WsprCycleSource, and always
true for a spool deleted on acknowledgement.  The store asks the source before each write.  In v3.70 a write
that would move a record backward, or leave it where it stands, gets logged with the key and both values, and
then goes ahead exactly as today (§10.4 item 4).  v3.71 enforces the rule once each sender owns its key.
Until then the warnings measure how often a shared key runs backward: the daemon and the in-process senders
write the same psk and wspr keys from separate processes today.

Three facts shape the code.  The concrete sources do not subclass the `Source` protocol, so the protocol's
default reaches none of them; core asks through `getattr` and falls back to "always after" for any source
without the method, the tests' `MemorySource` among them.  The check must never block a write: a skipped
write would re-send the batch on every pump, so a comparator that raises counts as forward.  And the warning
must not flood the journal.  On B4 a shared key can run backward on every acknowledgement, about once a
minute, and a flood of one persistent condition hides every other line.  So the store keeps one window per
key (source_id, dest_id, table), as the plan's controller ruled on 2026-10-07.  The first write on a key that
does not move forward logs a WARNING with the key and both values.  For the next hour each further one adds to
an in-memory count and logs at DEBUG.  The first one after the hour logs one WARNING with the key, the count
since the last WARNING and the latest pair of values, and a new window starts.  The store still writes every
time, so what the station sends stays the same.  Each process keeps its own windows, and a restart clears
them.

**Files:**
- Modify: `hs-uploader/src/hs_uploader/sources/base.py` — add `cursor_is_after` after `commit`, whose body
  ends at line 54 (`        return None`, the last line of the file).
- Modify: `hs-uploader/src/hs_uploader/sources/sqlite.py` — add `cursor_is_after` after `commit`, which ends
  at line 416, before `    # ---- internals ----` (line 418).
- Modify: `hs-uploader/src/hs_uploader/sources/files.py` — add `cursor_is_after` after `commit`, which ends at
  line 231, before `    # -- internals --` (line 233); append `_keep_cursor_decodes` after
  `_decode_keep_cursor` (lines 263-279, the end of the file).
- Modify: `hs-uploader/src/hs_uploader/sources/wspr_cycle.py` — add `cursor_is_after` after `commit`, whose
  docstring ends at line 193, before `    def close(self) -> None:` (line 195).
- Modify: `hs-uploader/src/hs_uploader/watermark/sqlite.py` — the imports (lines 25-27); a constant after
  `logger = logging.getLogger(__name__)` (line 87); the constructor's signature (line 99) and its state after
  `self._lock = threading.RLock()` (line 106); add `advance_cursor_checked` and `_log_not_forward` after
  `advance_cursor` (lines 164-180), before `    # -- attempts (audit log) --` (line 182).
- Modify: `hs-uploader/src/hs_uploader/watermark/base.py` — the `typing` import (line 24); add the protocol
  method after `advance_cursor` (lines 73-82), before `    # --- attempts (audit log) ---` (line 84).
- Modify: `hs-uploader/src/hs_uploader/core.py` — first-attempt ack (lines 464-468), partial ack (lines
  481-485), replay ack (lines 544-551); append two helpers after `_iso` (lines 645-648, the end of the file).
- Test: `hs-uploader/tests/test_cursor_is_after.py` (create), `hs-uploader/tests/test_advance_cursor_checked.py`
  (create).

The dry run measured every line number at Task 3's base, Task 2's commit.  Tasks 1 and 2 leave six of these
seven files alone, so their numbers match 3e97223.  Task 1 edits docstrings in `sources/sqlite.py`: it adds
three lines to the module docstring and one to `SqliteSource`'s.  So `_Cursor` there sits three lines lower
than at 3e97223, and `commit` four.  The quoted anchors find each edit regardless.

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces:
  - `Source.cursor_is_after(self, new: bytes, stored: bytes) -> bool` on the protocol (default True), with
    overrides on `SqliteSource`, `FileTreeSource` and `WsprCycleSource`.  Empty `stored` → True.  A value the
    source cannot decode → True, logged at DEBUG.
  - `SqliteWatermarkStore.advance_cursor_checked(source_id, dest_id, table, *, cursor: bytes, last_ack: str,
    is_after: Callable[[bytes, bytes], bool]) -> bool`, declared on the `WatermarkStore` protocol too.  It
    takes `last_ack`, keyword-only like `advance_cursor`'s, because it writes exactly what `advance_cursor`
    writes; the contract's shorthand signature left it out.
  - `SqliteWatermarkStore(path=":memory:", *, clock: Callable[[], float] = time.monotonic)`.  The new keyword
    feeds the warning windows, so tests can move time by hand.  Every existing caller passes only the path.
  - `BACKWARD_WARN_INTERVAL_S = 3600.0` in `hs_uploader.watermark.sqlite`, the window per key.
  - `hs_uploader.core._is_after_for(source) -> Callable[[bytes, bytes], bool]`.  Task 4's replay-ack path runs
    through it.
  - Two WARNING texts on logger `hs_uploader.watermark.sqlite`.  The first write on a key that does not move
    forward logs `send record (<source_id>, <dest_id>, <table>) does not move forward: stored <old>, new <new>;
    writing it anyway (v3.70 warns only; repeats on this key log at DEBUG for 3600 s, then one WARNING counts
    them)`.  The first one after the window logs `send record (<source_id>, <dest_id>, <table>) does not move
    forward: <N> times since the last warning, the latest stored <old>, new <new>; writing it anyway (v3.70
    warns only)`, with `1 time` when N is 1.  The DEBUG lines in between read `... again fails to move forward ...`, so a grep for `does
    not move forward` finds WARNINGs alone.  Task 10 adds up, per key, one for each first-form line and N for
    each counted one.  It reads hs-uploader's journal and the journal of every in-process sender that opens
    `watermarks.db` (psk-recorder@, meteor-scatter@, wspr-recorder@, wspr-uploader): those processes write
    the same keys and keep their own windows.  Writes counted at DEBUG since a key's last WARNING reach no
    journal at INFO until that key's next write after the hour.

- [ ] **Step 1: Write the failing source tests**

Create `hs-uploader/tests/test_cursor_is_after.py`:

```python
"""Each source orders its own send records (sigmond plan-sink-control §6.2).

The store keeps a cursor as opaque bytes, and a byte compare ranks b"999"
above b"1000".  Only the source knows its encoding, so each source answers
``cursor_is_after(new, stored)`` in the order its own query uses.  An empty
``stored`` (no send record yet) and a value the source cannot decode both
answer True: v3.70 only warns, and a warning about an unreadable value would
measure nothing.
"""
from __future__ import annotations

import logging
import sqlite3

import pytest

from hs_uploader.sources import FileSpec, FileTreeSource, SqliteSource
from hs_uploader.sources.base import Source
from hs_uploader.sources.wspr_cycle import WsprCycleSource


def _sqlite() -> SqliteSource:
    # No connection config: the comparison never touches the database.
    return SqliteSource("psk", "spots", accepted_schema_versions=[1])


def _keep(tmp_path) -> FileTreeSource:
    return FileTreeSource(tmp_path, specs=[FileSpec("*")],
                          retention=FileTreeSource.KEEP)


def _delete_on_ack(tmp_path) -> FileTreeSource:
    return FileTreeSource(tmp_path, specs=[FileSpec("*")])


def _wspr(tmp_path) -> WsprCycleSource:
    return WsprCycleSource(db_path=tmp_path / "sink.db")


# ---- the protocol default ------------------------------------------------


def test_the_protocol_default_answers_true():
    class _Plain(Source):
        def source_id(self): return "plain"
        def health(self): return "ok"
        def iter_batches(self, cursor, limit): return iter(())

    assert _Plain().cursor_is_after(b"1", b"2") is True


# ---- SqliteSource: integer ids, the order of ``id > ?`` -------------------


def test_sqlite_ranks_1000_after_999():
    src = _sqlite()
    assert src.cursor_is_after(b"1000", b"999") is True
    assert src.cursor_is_after(b"999", b"1000") is False


def test_sqlite_an_equal_id_does_not_move_forward():
    assert _sqlite().cursor_is_after(b"42", b"42") is False


def test_sqlite_empty_stored_answers_true():
    assert _sqlite().cursor_is_after(b"1", b"") is True


def test_sqlite_undecodable_answers_true_and_logs_at_debug(caplog):
    src = _sqlite()
    with caplog.at_level(logging.DEBUG, logger="hs_uploader.sources.sqlite"):
        assert src.cursor_is_after(b"7", b"not-an-id") is True
        assert src.cursor_is_after(b"\xff", b"7") is True
    recs = [r for r in caplog.records if r.name == "hs_uploader.sources.sqlite"]
    assert recs and all(r.levelno == logging.DEBUG for r in recs)


# ---- FileTreeSource KEEP: integer nanoseconds ----------------------------


def test_keep_ranks_1000_after_999(tmp_path):
    src = _keep(tmp_path)
    assert src.cursor_is_after(b"1000", b"999") is True
    assert src.cursor_is_after(b"999", b"1000") is False


def test_keep_an_equal_mtime_does_not_move_forward(tmp_path):
    ns = b"1781583014421470123"
    assert _keep(tmp_path).cursor_is_after(ns, ns) is False


def test_keep_reads_a_legacy_float_seconds_record(tmp_path):
    # Stations still hold KEEP send records written as float seconds.
    # iter_batches reads them through _decode_keep_cursor; so must the check.
    src = _keep(tmp_path)
    legacy = b"1781583014.421470"
    assert src.cursor_is_after(b"1781583014421471000", legacy) is True   # 1 us later
    assert src.cursor_is_after(b"1781583014421469000", legacy) is False  # 1 us earlier


def test_keep_empty_stored_answers_true(tmp_path):
    assert _keep(tmp_path).cursor_is_after(b"1", b"") is True


@pytest.mark.parametrize("new,stored", [
    (b"1781583014421470123", b"garbage"),
    (b"garbage", b"1781583014421470123"),
    (b"1781583014421470123", b"\xff\xfe"),
    (b"1781583014421470123", b"inf"),   # float("inf") overflows int()
])
def test_keep_undecodable_answers_true_and_logs_at_debug(tmp_path, caplog, new, stored):
    src = _keep(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="hs_uploader.sources.files"):
        assert src.cursor_is_after(new, stored) is True
    recs = [r for r in caplog.records if r.name == "hs_uploader.sources.files"]
    assert recs and all(r.levelno == logging.DEBUG for r in recs)


# ---- FileTreeSource delete_on_ack: always forward ------------------------


@pytest.mark.parametrize("new,stored", [
    (b"<delete-on-ack>", b"<delete-on-ack>"),
    (b"<delete-on-ack>", b""),
    (b"999", b"1000"),
])
def test_delete_on_ack_always_answers_true(tmp_path, new, stored):
    # Its cursor never changes, so every write repeats the same value.
    assert _delete_on_ack(tmp_path).cursor_is_after(new, stored) is True


# ---- WsprCycleSource: text order, the order of its SQL -------------------


def test_wspr_cycle_a_later_cycle_moves_forward(tmp_path):
    src = _wspr(tmp_path)
    assert src.cursor_is_after(b"2026-10-07T12:02:00Z",
                               b"2026-10-07T12:00:00Z") is True
    assert src.cursor_is_after(b"2026-10-07T12:00:00Z",
                               b"2026-10-07T12:02:00Z") is False
    assert src.cursor_is_after(b"2026-10-07T12:00:00Z",
                               b"2026-10-07T12:00:00Z") is False


@pytest.mark.parametrize("new,stored", [
    (b"2026-10-07T12:02:00Z", b"2026-10-07T12:00:00Z"),
    (b"2026-10-07T12:00:00Z", b"2026-10-07T12:02:00Z"),
    (b"2026-10-07T12:00:00Z", b"2026-10-07T12:00:00Z"),
    (b"2026-10-07T12:00:00+00:00", b"2026-10-07T12:00:00Z"),
    (b"2026-10-07T12:00:00.5Z", b"2026-10-07T12:00:00Z"),
    (b"1000", b"999"),
])
def test_wspr_cycle_order_matches_the_sql_text_compare(tmp_path, new, stored):
    # iter_batches selects json_extract(payload_json, '$.time') > cursor,
    # a text compare.  Parsing the times would disagree with that query.
    sql_says = sqlite3.connect(":memory:").execute(
        "SELECT ? > ?", (new.decode("ascii"), stored.decode("ascii")),
    ).fetchone()[0]
    assert _wspr(tmp_path).cursor_is_after(new, stored) is bool(sql_says)


def test_wspr_cycle_empty_stored_answers_true(tmp_path):
    assert _wspr(tmp_path).cursor_is_after(b"2026-10-07T12:00:00Z", b"") is True


def test_wspr_cycle_undecodable_answers_true_and_logs_at_debug(tmp_path, caplog):
    src = _wspr(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="hs_uploader.sources.wspr_cycle"):
        assert src.cursor_is_after(b"\xff", b"2026-10-07T12:00:00Z") is True
    recs = [r for r in caplog.records if r.name == "hs_uploader.sources.wspr_cycle"]
    assert recs and all(r.levelno == logging.DEBUG for r in recs)
```

- [ ] **Step 2: Write the failing store and core tests**

Create `hs-uploader/tests/test_advance_cursor_checked.py`:

```python
"""The warn-only forward check on send records (sigmond plan-sink-control §10.4 item 4).

advance_cursor_checked asks the source whether a write moves the send record
forward.  When it does not, the store logs the key and both values, then
writes exactly as advance_cursor does.  The first such write on a key logs a
WARNING; for the next hour further ones log at DEBUG and add to a count; the
first one after the hour logs one WARNING with that count.  v3.70 changes
nothing the station sends; the warnings measure how often a shared key runs
backward before v3.71 enforces the rule.
"""
from __future__ import annotations

import logging
import time

import pytest

from hs_uploader import Outcome, Pipeline, RetryPolicy, Uploader
from hs_uploader.watermark import SqliteWatermarkStore
from tests.conftest import MemorySource
from tests.test_core_orchestration import _ident, _records

_STORE_LOG = "hs_uploader.watermark.sqlite"
_KEY = ("memory:test", "memory", "test.spots")
_PSK = ("sqlite:psk.spots", "pskreporter", "psk.spots")


def _int_order(new: bytes, stored: bytes) -> bool:
    return not stored or int(new) > int(stored)


def _last_ack(store: SqliteWatermarkStore) -> str:
    (row,) = store.all_cursors()
    return row["last_ack"]


def _warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records
            if r.name == _STORE_LOG and r.levelno == logging.WARNING]


def _debugs(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records
            if r.name == _STORE_LOG and r.levelno == logging.DEBUG]


class _Clock:
    """A clock the test moves by hand, in seconds."""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _write(store: SqliteWatermarkStore, key, cursor: bytes, last_ack: str) -> bool:
    return store.advance_cursor_checked(*key, cursor=cursor, last_ack=last_ack,
                                        is_after=_int_order)


# ---- the store ---------------------------------------------------------------


def test_a_forward_write_returns_true_and_stays_quiet(tmp_path, caplog):
    store = SqliteWatermarkStore(tmp_path / "wm.db")
    store.advance_cursor("s", "d", "t", cursor=b"999", last_ack="t1")
    with caplog.at_level(logging.WARNING, logger=_STORE_LOG):
        assert store.advance_cursor_checked(
            "s", "d", "t", cursor=b"1000", last_ack="t2", is_after=_int_order,
        ) is True
    assert store.get_cursor("s", "d", "t") == b"1000"
    assert _last_ack(store) == "t2"
    assert _warnings(caplog) == []


def test_a_backward_write_warns_with_key_and_both_values_and_still_writes(tmp_path, caplog):
    store = SqliteWatermarkStore(tmp_path / "wm.db")
    store.advance_cursor("sqlite:psk.spots", "pskreporter", "psk.spots",
                         cursor=b"1000", last_ack="t1")
    with caplog.at_level(logging.WARNING, logger=_STORE_LOG):
        assert store.advance_cursor_checked(
            "sqlite:psk.spots", "pskreporter", "psk.spots",
            cursor=b"999", last_ack="t2", is_after=_int_order,
        ) is False
    # v3.70 warns only: the write happens exactly as advance_cursor makes it.
    assert store.get_cursor("sqlite:psk.spots", "pskreporter", "psk.spots") == b"999"
    assert _last_ack(store) == "t2"
    (rec,) = _warnings(caplog)
    msg = rec.getMessage()
    for part in ("sqlite:psk.spots", "pskreporter", "psk.spots", "b'1000'", "b'999'"):
        assert part in msg, (part, msg)


def test_a_write_that_stands_still_warns_too(tmp_path, caplog):
    store = SqliteWatermarkStore(tmp_path / "wm.db")
    store.advance_cursor("s", "d", "t", cursor=b"42", last_ack="t1")
    with caplog.at_level(logging.WARNING, logger=_STORE_LOG):
        assert store.advance_cursor_checked(
            "s", "d", "t", cursor=b"42", last_ack="t2", is_after=_int_order,
        ) is False
    assert len(_warnings(caplog)) == 1
    assert _last_ack(store) == "t2"


def test_the_first_write_hands_the_comparator_an_empty_stored_value(tmp_path):
    store = SqliteWatermarkStore(tmp_path / "wm.db")
    seen: list[tuple[bytes, bytes]] = []

    def spy(new: bytes, stored: bytes) -> bool:
        seen.append((new, stored))
        return True

    assert store.advance_cursor_checked(
        "s", "d", "t", cursor=b"7", last_ack="t1", is_after=spy,
    ) is True
    assert seen == [(b"7", b"")]
    assert store.get_cursor("s", "d", "t") == b"7"


def test_a_comparator_that_raises_never_blocks_the_write(tmp_path, caplog):
    store = SqliteWatermarkStore(tmp_path / "wm.db")
    store.advance_cursor("s", "d", "t", cursor=b"1", last_ack="t1")

    def broken(new: bytes, stored: bytes) -> bool:
        raise RuntimeError("comparator bug")

    with caplog.at_level(logging.WARNING, logger=_STORE_LOG):
        assert store.advance_cursor_checked(
            "s", "d", "t", cursor=b"2", last_ack="t2", is_after=broken,
        ) is True
    assert store.get_cursor("s", "d", "t") == b"2"
    assert any("comparator bug" in r.getMessage() for r in _warnings(caplog))


# ---- one WARNING per key per hour --------------------------------------------


def test_a_repeat_within_the_hour_logs_at_debug_and_still_writes(tmp_path, caplog):
    clock = _Clock()
    store = SqliteWatermarkStore(tmp_path / "wm.db", clock=clock)
    store.advance_cursor(*_KEY, cursor=b"1000", last_ack="t0")
    with caplog.at_level(logging.DEBUG, logger=_STORE_LOG):
        assert _write(store, _KEY, b"999", "t1") is False     # the first: warns
        clock.t += 3599.0
        assert _write(store, _KEY, b"998", "t2") is False     # inside the hour
    assert len(_warnings(caplog)) == 1
    (debug,) = _debugs(caplog)
    assert "stored b'999', new b'998'" in debug.getMessage()
    assert "(1 since the last warning)" in debug.getMessage()
    assert store.get_cursor(*_KEY) == b"998"
    assert _last_ack(store) == "t2"


def test_after_the_hour_one_warning_counts_the_writes_since_the_last(tmp_path, caplog):
    clock = _Clock()
    store = SqliteWatermarkStore(tmp_path / "wm.db", clock=clock)
    store.advance_cursor(*_KEY, cursor=b"1000", last_ack="t0")
    with caplog.at_level(logging.WARNING, logger=_STORE_LOG):
        _write(store, _KEY, b"999", "t1")          # the first: warns at t = 1000
        for cursor in (b"998", b"997", b"996"):    # three more, at DEBUG
            clock.t += 600.0
            _write(store, _KEY, cursor, "t2")
        clock.t = 1000.0 + 3600.0                  # the hour has passed
        _write(store, _KEY, b"990", "t3")          # one WARNING counts four
        clock.t += 1.0
        _write(store, _KEY, b"989", "t4")          # a new window: quiet again
    first, summary = _warnings(caplog)
    assert "stored b'1000', new b'999'" in first.getMessage()
    msg = summary.getMessage()
    assert msg.startswith("send record (%s, %s, %s) " % _KEY)
    assert "does not move forward: 4 times since the last warning" in msg
    assert "the latest stored b'996', new b'990'" in msg
    assert store.get_cursor(*_KEY) == b"989"
    assert _last_ack(store) == "t4"


@pytest.mark.parametrize("other", [
    ("sqlite:psk.spots@meteor-scatter", _PSK[1], _PSK[2]),   # another source_id
    (_PSK[0], "pskreporter-udp", _PSK[2]),                   # another dest_id
    (_PSK[0], _PSK[1], "psk.msk144"),                        # another table
])
def test_each_key_keeps_its_own_window(tmp_path, caplog, other):
    clock = _Clock()
    store = SqliteWatermarkStore(tmp_path / "wm.db", clock=clock)
    for key in (_PSK, other):
        store.advance_cursor(*key, cursor=b"100", last_ack="t0")
    with caplog.at_level(logging.WARNING, logger=_STORE_LOG):
        _write(store, _PSK, b"99", "t1")      # _PSK's first: warns
        clock.t += 1800.0
        _write(store, other, b"99", "t2")     # other's first: warns, inside _PSK's hour
        _write(store, _PSK, b"98", "t3")      # inside _PSK's hour: quiet
        clock.t += 1800.0                     # _PSK's hour has passed, other's has not
        _write(store, _PSK, b"97", "t4")      # _PSK: one WARNING counts two
        _write(store, other, b"98", "t5")     # inside other's hour: quiet
    warned = [r.getMessage() for r in _warnings(caplog)]
    assert len(warned) == 3
    assert warned[0].startswith("send record (%s, %s, %s) " % _PSK)
    assert warned[1].startswith("send record (%s, %s, %s) " % other)
    assert warned[2].startswith("send record (%s, %s, %s) " % _PSK)
    assert "2 times since the last warning" in warned[2]
    assert store.get_cursor(*_PSK) == b"97"
    assert store.get_cursor(*other) == b"98"


def test_every_write_goes_ahead_whatever_it_logs(tmp_path, caplog):
    clock = _Clock()
    store = SqliteWatermarkStore(tmp_path / "wm.db", clock=clock)
    store.advance_cursor(*_KEY, cursor=b"100", last_ack="t0")
    steps = [(0.0, b"99"), (10.0, b"98"), (3600.0, b"97"), (5.0, b"96"), (7200.0, b"95")]
    with caplog.at_level(logging.DEBUG, logger=_STORE_LOG):
        for i, (step, cursor) in enumerate(steps, start=1):
            clock.t += step
            assert _write(store, _KEY, cursor, f"t{i}") is False
            assert store.get_cursor(*_KEY) == cursor
            assert _last_ack(store) == f"t{i}"
    levels = [r.levelname for r in caplog.records if r.name == _STORE_LOG]
    assert levels == ["WARNING", "DEBUG", "WARNING", "DEBUG", "WARNING"]


# ---- core: all three advance sites use the checked call --------------------


class _SpyStore(SqliteWatermarkStore):
    """Counts checked advances, and bare advance_cursor calls made from outside one."""

    def __init__(self, path):
        super().__init__(path)
        self.checked: list[tuple] = []
        self.bare = 0
        self._in_checked = False

    def advance_cursor(self, *a, **kw):
        if not self._in_checked:
            self.bare += 1
        return super().advance_cursor(*a, **kw)

    def advance_cursor_checked(self, source_id, dest_id, table, *, cursor,
                               last_ack, is_after):
        self.checked.append((source_id, dest_id, table, cursor, is_after))
        self._in_checked = True
        try:
            return super().advance_cursor_checked(
                source_id, dest_id, table, cursor=cursor, last_ack=last_ack,
                is_after=is_after,
            )
        finally:
            self._in_checked = False


class _IntOrderSource(MemorySource):
    """MemorySource that ranks its cursors as integers, as SqliteSource does."""

    def cursor_is_after(self, new: bytes, stored: bytes) -> bool:
        return _int_order(new, stored)


def _pipe(tmp_path, transport, src, **kw):
    wm = _SpyStore(tmp_path / "wm.db")
    return Pipeline(name="test", source=src, transport=transport, watermark=wm,
                    identity=_ident(), **kw), wm


def test_a_first_attempt_ack_uses_the_checked_call(tmp_path, memory_transport):
    src = _IntOrderSource(records=_records(3))
    pipe, wm = _pipe(tmp_path, memory_transport, src)
    Uploader([pipe]).pump()
    assert wm.bare == 0
    assert [c[:4] for c in wm.checked] == [(*_KEY, b"3")]
    assert wm.checked[0][4] == src.cursor_is_after
    assert wm.get_cursor(*_KEY) == b"3"


def test_a_partial_ack_uses_the_checked_call(tmp_path, memory_transport):
    src = _IntOrderSource(records=_records(5))
    memory_transport.next_outcomes = [
        Outcome.partial_ack(accepted_cursor=b"3", rejected=(), reason="2 rejected"),
    ]
    pipe, wm = _pipe(tmp_path, memory_transport, src)
    Uploader([pipe]).pump()
    assert wm.bare == 0
    assert [c[:4] for c in wm.checked] == [(*_KEY, b"3")]
    assert wm.checked[0][4] == src.cursor_is_after


def test_a_replay_ack_uses_the_checked_call(tmp_path, memory_transport):
    src = _IntOrderSource(records=_records(2))
    pipe, wm = _pipe(tmp_path, memory_transport, src,
                     retry=RetryPolicy(base=1.0, cap_sec=1.0, max_attempts=5))
    memory_transport.next_outcomes = [Outcome.retry_later("blip")]
    Uploader([pipe]).pump()
    assert wm.checked == [] and wm.bare == 0

    far = time.time() + 86_400.0
    memory_transport.next_outcomes = [Outcome.acked()]
    Uploader([pipe], now_fn=lambda: far).pump()
    assert wm.bare == 0
    assert [c[:4] for c in wm.checked] == [(*_KEY, b"2")]
    assert wm.checked[0][4] == src.cursor_is_after


def test_a_source_without_the_method_gets_always_forward(tmp_path, memory_transport, caplog):
    # MemorySource, like any source written before v3.70, lacks cursor_is_after.
    src = MemorySource(records=_records(3))
    pipe, wm = _pipe(tmp_path, memory_transport, src)
    with caplog.at_level(logging.WARNING, logger=_STORE_LOG):
        Uploader([pipe]).pump()
    (call,) = wm.checked
    assert call[4](b"1", b"2") is True
    assert wm.get_cursor(*_KEY) == b"3"
    assert _warnings(caplog) == []


def test_core_warns_on_a_backward_ack_and_still_writes(tmp_path, memory_transport, caplog):
    # The stored record reads b"10".  MemorySource filters with a byte compare,
    # so it yields b"2" and b"3", and the ack moves the record from 10 to 3.
    src = _IntOrderSource(records=_records(3))
    pipe, wm = _pipe(tmp_path, memory_transport, src)
    wm.advance_cursor(*_KEY, cursor=b"10", last_ack="t0")
    wm.bare = 0
    with caplog.at_level(logging.WARNING, logger=_STORE_LOG):
        Uploader([pipe]).pump()
    assert wm.bare == 0
    assert wm.get_cursor(*_KEY) == b"3"
    (rec,) = _warnings(caplog)
    assert "b'10'" in rec.getMessage() and "b'3'" in rec.getMessage()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/hs-uploader && .venv/bin/pytest tests/test_cursor_is_after.py tests/test_advance_cursor_checked.py -v`
Expected: 41 failed.  The source tests fail with `AttributeError: 'SqliteSource' object has no attribute
'cursor_is_after'` (and the same for `FileTreeSource`, `WsprCycleSource` and `_Plain`).  Five store tests fail
with `AttributeError: 'SqliteWatermarkStore' object has no attribute 'advance_cursor_checked'`, and the six
window tests with `TypeError: SqliteWatermarkStore.__init__() got an unexpected keyword argument 'clock'`.  The
core tests fail with `assert 1 == 0` on `wm.bare`, because core still calls the bare `advance_cursor`; and
`test_a_source_without_the_method_gets_always_forward` fails with `ValueError: not enough values to unpack
(expected 1, got 0)`.

- [ ] **Step 4: Give the `Source` protocol its default**

In `src/hs_uploader/sources/base.py`, after the last lines of `commit` (lines 52-54):

```python
        previously-failed deliverable.
        """
        return None
```

append:

```python

    def cursor_is_after(self, new: bytes, stored: bytes) -> bool:
        """True when ``new`` lies after ``stored`` in this source's own
        order, so that writing ``new`` moves the send record forward.

        The watermark store keeps cursors as opaque bytes, and a byte
        compare ranks ``b"999"`` above ``b"1000"``.  Only the source
        knows its encoding, so the orchestrator asks the source before
        each cursor write.  An empty ``stored`` (no send record yet)
        answers True, and so does a value the source cannot decode.
        In v3.70 a False answer only gets logged (one WARNING per key
        per hour); the write still happens.

        This default answers True.  The concrete sources do not
        subclass this protocol, so each defines its own; core falls
        back to True for a source that lacks the method.
        """
        return True
```

- [ ] **Step 5: SqliteSource compares integer ids**

In `src/hs_uploader/sources/sqlite.py`, after the end of `commit` (lines 413-416):

```python
            logger.warning(
                "SqliteSource.commit: DELETE failed for %s.%s up to id=%d: %s",
                self.database, self.table, last_id, exc,
            )
```

and before `    # ---- internals ----`, insert:

```python

    def cursor_is_after(self, new: bytes, stored: bytes) -> bool:
        """Compare two send records as integer ids, the order of the
        ``id > ?`` filter.  A byte compare would rank ``b"999"`` above
        ``b"1000"``.  An empty ``stored`` answers True; so does a value
        that is not an ASCII integer (logged at DEBUG)."""
        if not stored:
            return True
        try:
            return _Cursor.from_bytes(new).last_id > _Cursor.from_bytes(stored).last_id
        except ValueError as exc:
            logger.debug(
                "SqliteSource %s.%s: cannot order send records %r and %r (%s); "
                "treating the new one as after",
                self.database, self.table, new, stored, exc,
            )
            return True
```

`_Cursor.from_bytes` already raises `ValueError` for a value that is not an ASCII integer (lines 101-107).

- [ ] **Step 6: FileTreeSource compares nanoseconds, or answers True on a deleted-on-ack spool**

In `src/hs_uploader/sources/files.py`, after the end of `commit` (lines 229-231):

```python
                # Successfully removed: check the parent next.
                if d.parent != self.root:
                    to_try.append(d.parent)
```

and before `    # -- internals --`, insert:

```python

    def cursor_is_after(self, new: bytes, stored: bytes) -> bool:
        """KEEP: compare integer nanoseconds, the order of the
        ``st_mtime_ns > after`` filter in ``iter_batches``, reading a
        legacy float-seconds record through ``_decode_keep_cursor`` just
        as ``iter_batches`` does.  delete_on_ack: always True, since its
        cursor never changes (``b"<delete-on-ack>"``).

        An empty ``stored`` answers True; so does a value
        ``_decode_keep_cursor`` would read as 0 for want of a number
        (logged at DEBUG)."""
        if self.retention == self.DELETE_ON_ACK:
            return True
        if not stored:
            return True
        if not (_keep_cursor_decodes(new) and _keep_cursor_decodes(stored)):
            logger.debug(
                "FileTreeSource %s: cannot order send records %r and %r; "
                "treating the new one as after",
                self._source_id, new, stored,
            )
            return True
        return _decode_keep_cursor(new) > _decode_keep_cursor(stored)
```

At the end of the file, after `_decode_keep_cursor`'s last lines (276-279):

```python
        try:
            return int(float(text) * 1_000_000_000)
        except ValueError:
            return 0
```

append:

```python


def _keep_cursor_decodes(cursor: bytes) -> bool:
    """True when ``_decode_keep_cursor`` reads a number from ``cursor``
    rather than falling back to 0.  ``b"inf"`` counts as undecodable:
    ``int(float("inf"))`` raises OverflowError."""
    try:
        text = cursor.decode("ascii")
    except UnicodeDecodeError:
        return False
    try:
        int(text)
        return True
    except ValueError:
        pass
    try:
        int(float(text) * 1_000_000_000)
        return True
    except (ValueError, OverflowError):
        return False
```

`_decode_keep_cursor` itself stays byte-for-byte as it is: `iter_batches` reads it, and v3.70 changes no row
selection.

- [ ] **Step 7: WsprCycleSource compares text, as its SQL does**

In `src/hs_uploader/sources/wspr_cycle.py`, after `commit`'s docstring (lines 188-193):

```python
    def commit(self, commit_token: bytes) -> None:
        """Per-cycle commit hook.

        No-op in this source — row cleanup is deferred to
        ``smd storage trim`` (24 h retention).  Doing it here would
        race the wsprnet pipeline, which also reads ``wspr.spots``."""
```

and before `    def close(self) -> None:`, insert:

```python

    def cursor_is_after(self, new: bytes, stored: bytes) -> bool:
        """Compare as text, the order of the SQL filter
        ``json_extract(payload_json, '$.time') > ?`` in ``iter_batches``.
        Parsing the times could disagree with that query when a producer
        writes ``+00:00`` or fractional seconds, so the compare stays
        lexical.  An empty ``stored`` answers True; so does a value that
        is not ASCII (logged at DEBUG)."""
        if not stored:
            return True
        try:
            return new.decode("ascii") > stored.decode("ascii")
        except UnicodeDecodeError as exc:
            logger.debug(
                "WsprCycleSource: cannot order send records %r and %r (%s); "
                "treating the new one as after", new, stored, exc,
            )
            return True
```

- [ ] **Step 8: Run the source tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cursor_is_after.py -v`
Expected: 25 passed.

- [ ] **Step 9: The store asks, warns at most once an hour per key, and writes anyway**

In `src/hs_uploader/watermark/sqlite.py`, replace lines 25-27:

```python
import threading
from pathlib import Path
from typing import Optional
```

with:

```python
import threading
import time
from pathlib import Path
from typing import Callable, Optional
```

Replace line 87:

```python
logger = logging.getLogger(__name__)
```

with:

```python
logger = logging.getLogger(__name__)

# advance_cursor_checked logs a send record that does not move forward
# with one WARNING per key per this many seconds, and at DEBUG between.
# A shared key can run backward on every acknowledgement, and a WARNING
# each time would bury every other line in the journal.
BACKWARD_WARN_INTERVAL_S = 3600.0
```

Replace the constructor's signature (line 99):

```python
    def __init__(self, path: Path | str = ":memory:"):
```

with:

```python
    def __init__(
        self,
        path: Path | str = ":memory:",
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
```

and, after `self._lock = threading.RLock()` (line 106):

```python
        self._lock = threading.RLock()
```

add:

```python
        # advance_cursor_checked's warning windows, one per key:
        # key -> [window start on `clock`, writes since the last WARNING].
        # In memory only, so each process keeps its own.
        self._clock = clock
        self._warn_windows: dict[tuple[str, str, str], list] = {}
```

After the end of `advance_cursor` (lines 173-180):

```python
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO watermarks(source_id, dest_id, table_name, "
                "cursor, last_ack) VALUES(?,?,?,?,?) "
                "ON CONFLICT(source_id, dest_id, table_name) DO UPDATE SET "
                "cursor=excluded.cursor, last_ack=excluded.last_ack",
                (source_id, dest_id, table, cursor, last_ack),
            )
```

and before `    # -- attempts (audit log) --`, insert:

```python

    def advance_cursor_checked(
        self,
        source_id: str,
        dest_id: str,
        table: str,
        *,
        cursor: bytes,
        last_ack: str,
        is_after: Callable[[bytes, bytes], bool],
    ) -> bool:
        """Write the send record exactly as ``advance_cursor`` does, after
        asking ``is_after(cursor, stored)`` whether the write moves it
        forward.  Returns that answer.

        v3.70 warns only.  A write that would move the record backward,
        or leave it where it stands, gets logged with the key and both
        values and then goes ahead, so nothing the station sends
        changes.  v3.71 enforces the rule once each sender owns its key.
        ``_log_not_forward`` keeps that log to one WARNING per key per
        ``BACKWARD_WARN_INTERVAL_S``.  A comparator that raises counts as
        True: it logs a WARNING and never blocks the write, because a
        skipped write would re-send the batch on every pump.

        ``self._lock`` covers the read and the write, which serializes
        callers in this process only.  Another process that opens the
        same file (an in-process sender) can still write between them;
        a check that only warns tolerates that.
        """
        with self._lock:
            stored = self.get_cursor(source_id, dest_id, table)
            try:
                forward = bool(is_after(cursor, stored))
            except Exception as exc:  # noqa: BLE001 — never block the write
                logger.warning(
                    "send record (%s, %s, %s): cursor comparator raised %s: %s; "
                    "writing %r anyway",
                    source_id, dest_id, table, type(exc).__name__, exc, cursor,
                )
                forward = True
            if not forward:
                self._log_not_forward((source_id, dest_id, table), stored, cursor)
            self.advance_cursor(
                source_id, dest_id, table, cursor=cursor, last_ack=last_ack,
            )
            return forward

    def _log_not_forward(
        self, key: tuple[str, str, str], stored: bytes, new: bytes,
    ) -> None:
        """Log a write that does not move ``key``'s send record forward.

        The first such write on a key logs a WARNING naming the key and
        both values, and opens a window of ``BACKWARD_WARN_INTERVAL_S``
        on ``self._clock``.  Inside the window each further one adds to
        the key's count and logs at DEBUG.  The first one after the
        window logs one WARNING with the key, the count since the last
        WARNING (itself included) and the latest pair of values, then
        opens a new window.  The caller holds ``self._lock``.
        """
        now = self._clock()
        window = self._warn_windows.get(key)
        if window is None:
            self._warn_windows[key] = [now, 0]
            logger.warning(
                "send record (%s, %s, %s) does not move forward: stored %r, "
                "new %r; writing it anyway (v3.70 warns only; repeats on this "
                "key log at DEBUG for %d s, then one WARNING counts them)",
                *key, stored, new, BACKWARD_WARN_INTERVAL_S,
            )
            return
        window[1] += 1
        if now - window[0] < BACKWARD_WARN_INTERVAL_S:
            logger.debug(
                "send record (%s, %s, %s) again fails to move forward: "
                "stored %r, new %r (%d since the last warning)",
                *key, stored, new, window[1],
            )
            return
        logger.warning(
            "send record (%s, %s, %s) does not move forward: %d %s since the "
            "last warning, the latest stored %r, new %r; writing it anyway "
            "(v3.70 warns only)",
            *key, window[1], "time" if window[1] == 1 else "times", stored, new,
        )
        self._warn_windows[key] = [now, 0]
```

`self._lock` is an `RLock` (line 106), so the nested `get_cursor` and `advance_cursor` calls take it again
safely.

In `src/hs_uploader/watermark/base.py`, replace line 24:

```python
from typing import Optional, Protocol, runtime_checkable
```

with:

```python
from typing import Callable, Optional, Protocol, runtime_checkable
```

After the protocol's `advance_cursor` stub (lines 73-82):

```python
    def advance_cursor(
        self,
        source_id: str,
        dest_id: str,
        table: str,
        *,
        cursor: bytes,
        last_ack: str,
    ) -> None:
        ...
```

and before `    # --- attempts (audit log) ---`, insert:

```python

    def advance_cursor_checked(
        self,
        source_id: str,
        dest_id: str,
        table: str,
        *,
        cursor: bytes,
        last_ack: str,
        is_after: Callable[[bytes, bytes], bool],
    ) -> bool:
        """Read the stored cursor, ask ``is_after(cursor, stored)``,
        log the key and both values when it answers False, then write
        exactly as ``advance_cursor`` does.  Returns the answer.  The
        log holds one WARNING per key per hour, with a count of the
        writes logged at DEBUG between.  v3.70 never refuses a write.
        """
        ...
```

Run: `.venv/bin/pytest tests/test_advance_cursor_checked.py -v`
Expected: the eleven store tests pass; the five core tests still fail (`assert 1 == 0` on `wm.bare`, and the
`ValueError` unpack in `test_a_source_without_the_method_gets_always_forward`).

- [ ] **Step 10: core's three advance sites use the checked call**

In `src/hs_uploader/core.py`, `_handle_first_attempt`, replace the first-attempt ack (lines 464-468):

```python
        if outcome.kind == "acked":
            pipe.watermark.advance_cursor(
                pipe.source_id(), pipe.dest_id(), table,
                cursor=batch.cursor_after, last_ack=ts,
            )
```

with:

```python
        if outcome.kind == "acked":
            pipe.watermark.advance_cursor_checked(
                pipe.source_id(), pipe.dest_id(), table,
                cursor=batch.cursor_after, last_ack=ts,
                is_after=_is_after_for(pipe.source),
            )
```

Replace the partial ack (lines 481-485):

```python
            pipe.watermark.advance_cursor(
                pipe.source_id(), pipe.dest_id(), table,
                cursor=outcome.accepted_cursor or batch.cursor_after,
                last_ack=ts,
            )
```

with:

```python
            pipe.watermark.advance_cursor_checked(
                pipe.source_id(), pipe.dest_id(), table,
                cursor=outcome.accepted_cursor or batch.cursor_after,
                last_ack=ts,
                is_after=_is_after_for(pipe.source),
            )
```

In `_handle_outcome`, replace the replay ack (lines 544-551):

```python
            if deliverable.cursor_after:
                pipe.watermark.advance_cursor(
                    deliverable.source_id or pipe.source_id(),
                    deliverable.dest_id or pipe.dest_id(),
                    deliverable.table or pipe.transport.primary_table(),
                    cursor=deliverable.cursor_after,
                    last_ack=ts,
                )
```

with:

```python
            if deliverable.cursor_after:
                pipe.watermark.advance_cursor_checked(
                    deliverable.source_id or pipe.source_id(),
                    deliverable.dest_id or pipe.dest_id(),
                    deliverable.table or pipe.transport.primary_table(),
                    cursor=deliverable.cursor_after,
                    last_ack=ts,
                    is_after=_is_after_for(pipe.source),
                )
```

At the end of the file, after `_iso` (lines 645-648):

```python
def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(
        timespec="seconds"
    )
```

append:

```python


def _always_after(new: bytes, stored: bytes) -> bool:
    return True


def _is_after_for(source: "Source") -> Callable[[bytes, bytes], bool]:
    """The source's own cursor order, for ``advance_cursor_checked``.

    A source written before v3.70 (or a test double) may lack
    ``cursor_is_after``.  The concrete sources do not subclass the
    ``Source`` protocol, so they inherit no default; such a source gets
    "always after", which never warns.
    """
    fn = getattr(source, "cursor_is_after", None)
    return fn if callable(fn) else _always_after
```

`Callable` comes from the `typing` import at lines 20-28, and `Source` from the `TYPE_CHECKING` block at
line 31.  After this step `grep -n "advance_cursor(" src/hs_uploader/core.py` prints nothing.

- [ ] **Step 11: Run the tests to verify they pass, then the whole suite**

Run: `.venv/bin/pytest tests/test_cursor_is_after.py tests/test_advance_cursor_checked.py -v`
Expected: 41 passed.
Run: `.venv/bin/pytest -q`
Expected: `399 passed, 1 skipped` (Task 2's 358 passed, 1 skipped, plus these 41).

The dry run also mutated the code to measure the tests' power.  Each of these mutations turned at least one
test red: a bare `advance_cursor` at the partial-ack site, the same at the replay-ack site, a store that skips
the backward write (9 failures), SqliteSource comparing bytes, WsprCycleSource parsing times, FileTreeSource
reading only integers, a store that lets a raising comparator escape, and core calling
`source.cursor_is_after` without its `getattr` fallback (13 failures across the suite: every `MemorySource`
pipeline).  Four more mutations broke the warning windows, and each failed its own tests: a store that warns
on every write (6 failures), a window that never restarts (2), one window shared by every key (the three
`test_each_key_keeps_its_own_window` cases), and a summary that leaves out the write that triggers it (4).

- [ ] **Step 12: Commit**

```bash
cd /home/mjh/hamsci/repos/hs-uploader
git add src/hs_uploader/sources/base.py src/hs_uploader/sources/sqlite.py \
        src/hs_uploader/sources/files.py src/hs_uploader/sources/wspr_cycle.py \
        src/hs_uploader/watermark/sqlite.py src/hs_uploader/watermark/base.py \
        src/hs_uploader/core.py \
        tests/test_cursor_is_after.py tests/test_advance_cursor_checked.py
git commit -m "cursor order: each source ranks its send records; the store warns on a backward write

Each source gains cursor_is_after(new, stored) in the order its own query
uses: integer ids for SqliteSource, integer nanoseconds for a kept FileTree
spool (legacy float seconds read through _decode_keep_cursor), text for
WsprCycleSource, and always true for a spool deleted on ack.  A byte compare
would rank b\"999\" above b\"1000\".

SqliteWatermarkStore.advance_cursor_checked asks the source under the
store's lock.  When a write would not move the record forward it logs the
key and both values, then writes exactly as advance_cursor does.  core's
three advance sites (first ack, partial ack, replay ack) call it.  v3.70
changes nothing the station sends; the warnings measure how often a shared
key runs backward before v3.71 enforces the rule (sigmond
tasks/plan-sink-control.md §6.2, §10.4 item 4).

A shared key can run backward on every acknowledgement, and a WARNING each
time would bury the journal.  So each key gets one WARNING, then DEBUG
lines for an hour, then one WARNING that counts them and names the latest
pair, and the window starts again.  The clock that times the windows comes
in through the store's constructor, so tests can move it by hand.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---


### Task 4: a queued retry keeps its key, send record and commit token (D15)

When a replayed retry fails again, `_handle_outcome` requeues it by building a fresh `Deliverable` from six
fields (`src/hs_uploader/core.py:587-597` at Task 3's head, `584-594` before Task 3).  The dataclass defaults
the other five (`source_id`, `dest_id`, `table`, `cursor_after`, `commit_token`) to empty
(`watermark/base.py:48-52`), and `requeue_deliverable` writes them back empty.  From then on the retry's
acknowledgement advances no send record, because `if deliverable.cursor_after:` reads false.  It hands the
source an empty commit token, so SqliteSource deletes no rows and a spool deleted on acknowledgement keeps
its files.  The next drain then sends the same rows again.  The store's duplicate check on
`(pipeline, cursor_after)` also stops seeing the row.  v3.71's key migration re-homes queued retries by the
key they stored, so the keys must survive (D15, §10.4 item 6).

The fix copies the deliverable with `dataclasses.replace` and changes only `attempts` and `next_attempt_at`.
A field added to `Deliverable` later rides along without another edit; the field-by-field rebuild caused
this defect.

This changes what the station sends in one way only: a retry that failed a replay no longer re-sends its rows
once it gets through.  Rows that an older release already requeued without their key keep the empty fields,
and their acknowledgement still advances nothing, as today.  A requeued retry's acknowledgement now writes its
own `cursor_after` through Task 3's checked call, just as a retry acknowledged on its first replay already
does.  On a key that another process also writes, that write can run backward, and Task 3 logs it.

**Files:**
- Modify: `hs-uploader/src/hs_uploader/core.py` — the `dataclasses` import (line 17); the requeue in
  `_handle_outcome`'s `retry_later` branch (lines 587-597 at Task 3's head).
- Test: `hs-uploader/tests/test_requeue_keeps_key.py` (create).

**Interfaces:**
- Consumes: Task 3's `SqliteWatermarkStore.advance_cursor_checked` and `core._is_after_for`.  The replay ack
  that the test drives runs through them; this task changes neither.
- Produces: no new names.  `Uploader._handle_outcome` requeues `replace(deliverable, attempts=...,
  next_attempt_at=...)`, so every requeued row keeps `id`, `pipeline`, `payload_blob`, `enqueued_at`,
  `source_id`, `dest_id`, `table`, `cursor_after` and `commit_token`.

- [ ] **Step 1: Write the failing test**

Create `hs-uploader/tests/test_requeue_keeps_key.py`:

```python
"""D15: a retry that fails its replay keeps its key, cursor_after and commit_token.

The requeue rebuilt the deliverable field by field and left five fields out
(core.py, the retry_later branch of _handle_outcome).  A retry that failed
one replay lost its key, its cursor_after and its commit token.  When it
finally acknowledged, it advanced no send record and cleaned nothing up, and
the next drain sent its rows a second time (sigmond plan-sink-control D15,
§10.4 item 6).
"""
from __future__ import annotations

import time

from hs_uploader import Outcome, Pipeline, RecordBatch, RetryPolicy, Uploader
from hs_uploader.watermark import SqliteWatermarkStore
from tests.conftest import MemorySource
from tests.test_core_orchestration import _ident, _records

_KEY = ("memory:test", "memory", "test.spots")


class _TokenSource(MemorySource):
    """MemorySource whose batches carry a commit token; records each commit()."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.commits: list[bytes] = []

    def iter_batches(self, cursor: bytes, limit: int):
        for batch in super().iter_batches(cursor, limit):
            yield RecordBatch(records=batch.records,
                              cursor_after=batch.cursor_after,
                              commit_token=b"tok:" + batch.cursor_after)

    def commit(self, commit_token: bytes) -> None:
        self.commits.append(commit_token)


def _queued(wm: SqliteWatermarkStore) -> list[tuple]:
    return [
        (r["source_id"], r["dest_id"], r["table_name"], bytes(r["cursor_after"]),
         bytes(r["commit_token"]), r["attempts"])
        for r in wm._conn.execute(
            "SELECT source_id, dest_id, table_name, cursor_after, commit_token, "
            "attempts FROM deliverables ORDER BY id")
    ]


def test_retry_failing_twice_then_acked_advances_to_its_cursor(tmp_path, memory_transport):
    src = _TokenSource(records=_records(2))
    wm = SqliteWatermarkStore(tmp_path / "wm.db")
    pipe = Pipeline(name="test", source=src, transport=memory_transport,
                    watermark=wm, identity=_ident(),
                    retry=RetryPolicy(base=1.0, cap_sec=1.0, max_attempts=5))
    clock = [time.time()]
    up = Uploader([pipe], now_fn=lambda: clock[0])

    # 1. The first attempt fails: the retry queues with its key.
    memory_transport.next_outcomes = [Outcome.retry_later("blip")]
    up.pump()
    assert _queued(wm) == [(*_KEY, b"2", b"tok:2", 0)]

    # 2. Its replay fails too.  The requeued row must still carry the key,
    #    cursor_after and commit token; only the attempt count moves.
    clock[0] += 3600.0
    memory_transport.next_outcomes = [Outcome.retry_later("still down")]
    up.pump()
    assert _queued(wm) == [(*_KEY, b"2", b"tok:2", 1)]

    # 3. The next replay acknowledges.  A second outcome waits behind it: if
    #    the drain sends the rows again, that send fails, so the send record
    #    can reach b"2" only through the retry's own cursor_after.
    clock[0] += 3600.0
    memory_transport.next_outcomes = [Outcome.acked(),
                                      Outcome.retry_later("a re-send")]
    up.pump()
    assert wm.get_cursor(*_KEY) == b"2"
    assert src.commits == [b"tok:2"]
    assert wm.deliverable_count("test") == 0
    assert len(memory_transport.shipped) == 1      # sent once, never re-sent
    assert len(memory_transport.replayed) == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/mjh/hamsci/repos/hs-uploader && .venv/bin/pytest tests/test_requeue_keeps_key.py -v`
Expected: 1 failed, at the check after the second pump:

```
E       AssertionError: assert [('', '', '', b'', b'', 1)] == [('memory:tes... b'tok:2', 1)]
E         At index 0 diff: ('', '', '', b'', b'', 1) != ('memory:test', 'memory', 'test.spots', b'2', b'tok:2', 1)
```

The dry run also removed that check and ran each final assertion alone against the old requeue.  Four of
them fail on their own: the old code leaves the send record at `b""`, commits `b""`, queues a fresh retry for
the re-send, and ships the batch twice.  The fifth, `len(memory_transport.replayed) == 2`, only confirms the
sequence.

- [ ] **Step 3: Requeue with `dataclasses.replace`**

In `src/hs_uploader/core.py`, replace line 17:

```python
from dataclasses import dataclass, field
```

with:

```python
from dataclasses import dataclass, field, replace
```

In `_handle_outcome`, replace the requeue (lines 587-597 at Task 3's head):

```python
            from .watermark.base import Deliverable
            pipe.watermark.requeue_deliverable(
                Deliverable(
                    id=deliverable.id,
                    pipeline=deliverable.pipeline,
                    payload_blob=deliverable.payload_blob,
                    enqueued_at=deliverable.enqueued_at,
                    attempts=attempts,
                    next_attempt_at=_iso(now + pipe.retry.delay_for(attempts)),
                )
            )
```

with:

```python
            # Change only the attempt count and the next attempt time; keep
            # every other field, the key, cursor_after and commit_token
            # among them.  Rebuilding the row field by field once dropped
            # those five (D15): the retry's eventual ack then advanced no
            # send record, cleaned nothing up, and the rows went out again.
            pipe.watermark.requeue_deliverable(
                replace(
                    deliverable,
                    attempts=attempts,
                    next_attempt_at=_iso(now + pipe.retry.delay_for(attempts)),
                )
            )
```

`deliverable` comes from `pop_due_deliverable`, a frozen `Deliverable` dataclass, so `replace` applies.  The
local `Deliverable` import goes away with the rebuild; nothing else in `core.py` names it.

- [ ] **Step 4: Run the test to verify it passes, then the whole suite**

Run: `.venv/bin/pytest tests/test_requeue_keeps_key.py -v`
Expected: 1 passed.
Run: `.venv/bin/pytest -q`
Expected: `400 passed, 1 skipped` (Task 3's 399 passed, 1 skipped, plus this one).  `tests/test_core_orchestration.py`
(`test_retry_exhaustion_dead_letters` among them) and `tests/test_link_loss_hot_loop.py` must stay green.

- [ ] **Step 5: Commit**

```bash
cd /home/mjh/hamsci/repos/hs-uploader
git add src/hs_uploader/core.py tests/test_requeue_keeps_key.py
git commit -m "core: a retry that fails its replay keeps its key, cursor_after and commit token

The requeue in _handle_outcome rebuilt the Deliverable field by field and
left out source_id, dest_id, table, cursor_after and commit_token.  After
one failed replay a retry lost all five, so its eventual ack advanced no
send record, committed nothing, and the next drain sent its rows again.
dataclasses.replace now changes only attempts and next_attempt_at
(sigmond tasks/plan-sink-control.md D15, §10.4 item 6).

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---


### Task 5: `tools/neutrality_check.py`, the old-vs-new proof

§10.4 promises that v3.70 changes nothing a station sends, and it asks for proof before release.
Task 10 runs that proof on read-only snapshots from ND and B4.  This task builds the tool it runs.
The tool builds every pipeline in a station's `pipelines.toml` twice, once with an OLD hs-uploader
tree (the station's SHA) and once with a NEW one (v3.70).  Each tree runs in its own process, on
its own copy of `sink.db` and `watermarks.db`.  For each pipeline the tool prints the send-record
key the tree's own core reads, the send record stored under it, the queued retries, and the next
batch the source would hand its transport.  Then it says whether the two trees agree.

A recorder takes each transport's place.  It borrows the real transport's name and table, so the
key comes out as it does in production.  It stops the pump at the first send, before any cursor
advance, retry or commit, so nothing leaves the host and no spool file gets deleted.  The key
comes from the tree itself: a subclass of the tree's own store records the arguments of
`get_cursor`, which `Uploader._drain_source` calls with `(source_id, dest_id, table)`
(`src/hs_uploader/core.py:407-410`).  SqliteSource puts no row id on its Records
(`sources/sqlite.py:483-509`), so the tool reads the ids where the source reads them, from each
query on the sink copy whose first column is `id`.  A frozen clock lets both trees judge at the
same instant which WSPR cycles have closed (`sources/wspr_cycle.py:158, 244`;
`sources/wspr_completion.py:129`).  These line numbers name each file at this task's base, Task 4's
commit: they count Task 1's docstring lines in `sources/sqlite.py` and Task 3's insertions.

The check leaves three things to Task 10.  The in-process senders (psk-recorder's, meteor-scatter's
and wspr-recorder's shims) build their pipelines in client code, outside `pipelines.toml`.  The
check exercises the same source classes, under the daemon's arguments, not the shims'.
`hs-uploader migrate --check` runs on its own copy.  Filetree spools (GRAPE, the magnetometer, the
heartbeat) do not exist on the dev box, so for those pipelines the check compares keys only.

The tool lives in `tools/`, outside the package (`pyproject.toml` finds packages under `src/`
only).  Only a person runs it, so it changes nothing a station sends.

**Files:**
- Create: `hs-uploader/tools/neutrality_check.py`
- Test: `hs-uploader/tests/test_neutrality_check.py` (create)
- Modify: `hs-uploader/CLAUDE.md:38-39` (the "## Commands" block; insert after the two lines
  `# Build distribution` / `uv build`; Task 1's edit to lines 7-16 moved them down from 32-33)

**Interfaces:**
- Consumes, from each tree it compares (any SHA that has `hs_uploader.daemon`, which every
  station running the daemon has): `daemon.load_manifest(path)`;
  `daemon.build_all_pipelines(manifest, *, watermark)`; `core.Uploader(pipelines, *, now_fn)` with
  `.pump()` and `.close()`; `watermark.sqlite.SqliteWatermarkStore(path)` with `get_cursor`,
  `pop_due_deliverable` and `deliverable_count`; each transport's `name`, `ACCEPTS`,
  `primary_table()` and `batch_policy()`.  Nothing from Tasks 1-4.
- Produces, for Task 10: `tools/neutrality_check.py --manifest M --sink S --watermarks W
  --old OLD/src --new NEW/src [--now YYYY-MM-DDTHH:MM:SSZ]`.  For each pipeline it prints
  `<name>: AGREE` or `<name>: DISAGREE`, then four lines: `  OLD key  (...)`, `  NEW key  (...)`,
  `  OLD next ...`, `  NEW next ...`.  `--pipelines` works as another name for `--manifest`, the
  name `hs-uploader serve` uses.  The last line reads `RESULT: AGREE` or `RESULT: DISAGREE`,
  with ` (N raised an error; see above)` appended when any pipeline raised.  Exit 0 when every
  pipeline agrees; 1 when any disagrees, a tree fails to build or a given file changed during
  the run; 2 on a usage error.  `--now` defaults to the current UTC time; Task 10 should pass
  each snapshot's time, the `date -u` line of its probe.

All commands run from `/home/mjh/hamsci/repos/hs-uploader`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_neutrality_check.py`:

```python
"""tools/neutrality_check.py: the v3.70 proof that two hs-uploader trees
send the same thing (sigmond tasks/plan-sink-control.md §10.4).

Every test runs the tool for real: two worker processes, each importing
the tree it names.  A synthetic station stands in for ND or B4.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from hs_uploader.watermark.sqlite import SqliteWatermarkStore

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
_spec = importlib.util.spec_from_file_location(
    "neutrality_check", ROOT / "tools" / "neutrality_check.py")
nc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nc)

NOW = "2026-10-07T12:07:30Z"
PSK_KEY = ("sqlite:psk.spots", "pskreporter-tcp:report.pskreporter.info:4739",
           "psk.spots")
WSPRNET_KEY = ("sqlite:wspr.spots",
               "wsprnet-async:https://wsprnet.org/api/upload/v1", "wspr.spots")
CYCLE_KEY = ("sqlite:wspr.cycle",
             "wsprdaemon-tar-sftp:gw1.wsprdaemon.org,gw2.wsprdaemon.org",
             "wspr.cycle")

# pending_uploads as a v3.69 station holds it: sigmond's writer at 64cf83f,
# _QUEUE_DDL and both indexes, without Task 1's producer and local columns.
SINK_DDL = """
CREATE TABLE pending_uploads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_db       TEXT NOT NULL,
    target_table    TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 0,
    payload_json    TEXT NOT NULL,
    queued_at       TEXT NOT NULL
);
CREATE INDEX idx_pending_uploads_target
    ON pending_uploads (target_db, target_table, id);
CREATE INDEX idx_pending_uploads_cycle_time
    ON pending_uploads (target_db, target_table,
                        json_extract(payload_json, '$.time'));
"""

# id: (target_db, target_table, payload)
ROWS = [
    ("psk", "spots", {"time": "2026-10-07T12:00:15Z", "mode": "ft8", "tx_call": "K1ABC", "forward_to_pskreporter": 0}),
    ("psk", "spots", {"time": "2026-10-07T12:00:30Z", "mode": "ft8", "tx_call": "K2BCD", "forward_to_pskreporter": 0}),
    ("psk", "spots", {"time": "2026-10-07T12:01:00Z", "mode": "msk144", "tx_call": "K3CDE", "forward_to_pskreporter": 0}),
    ("psk", "spots", {"time": "2026-10-07T12:02:15Z", "mode": "ft4", "tx_call": "K4DEF", "forward_to_pskreporter": 0}),
    ("psk", "spots", {"time": "2026-10-07T12:02:30Z", "mode": "ft8", "tx_call": "K5EFG", "forward_to_pskreporter": 1}),
    ("wspr", "spots", {"time": "2026-10-07T12:00:00Z", "callsign": "W1AW", "band": 20, "snr_db": -10}),
    ("wspr", "noise", {"time": "2026-10-07T12:00:00Z", "rms_level": -120.0}),
    ("wspr", "spots", {"time": "2026-10-07T12:02:00Z", "callsign": "W1AW", "band": 20, "snr_db": -12}),
    ("wspr", "spots", {"time": "2026-10-07T12:02:00Z", "callsign": "W1AW", "band": 20, "snr_db": -8}),
    ("wspr", "noise", {"time": "2026-10-07T12:02:00Z", "rms_level": -119.0}),
    ("wspr", "spots", {"time": "2026-10-07T12:06:00Z", "callsign": "W1AW", "band": 20, "snr_db": -9}),
]

MANIFEST = """\
[identity]
call = "AC0G/T"
grid = "EM38ww"

[[pipeline]]
name = "psk-pskreporter"
batch_limit = 500
[pipeline.source]
type = "sqlite"
database = "psk"
table = "spots"
accepted_schema_versions = [2]
start_at = "now"
delete_on_commit = false
extra_where = [["tx_call", "!=", ""], ["mode", "IN", ["ft8", "ft4", "msk144"]], ["forward_to_pskreporter", "=", 0]]
[pipeline.transport]
type = "pskreporter"
decoding_software = "psk-recorder/0.1"

[[pipeline]]
name = "wspr-wsprdaemon"
batch_limit = 10000
max_records_per_pump = 20000
[pipeline.source]
type = "wspr_cycle"
db_path = "/var/lib/sigmond/sink.db"
start_at = "now"
include_psk = true
[pipeline.transport]
type = "wsprdaemon_tar"
servers = ["gw1.wsprdaemon.org", "gw2.wsprdaemon.org"]
receiver = "AC0G_T"
primary_table_name = "wspr.cycle"

[[pipeline]]
name = "wspr-wsprnet"
batch_limit = 900
[pipeline.source]
type = "sqlite"
database = "wspr"
table = "spots"
accepted_schema_versions = [1, 2]
start_at = "now"
delete_on_commit = false
dedup_partition_by = ["time", "callsign", "band"]
dedup_order_by_desc = "snr_db"
[pipeline.transport]
type = "wsprnet"
api_base_url = "https://wsprnet.org/api/upload/v1"

[[pipeline]]
name = "heartbeat"
[pipeline.source]
type = "filetree"
root = "{heartbeat}"
patterns = ["*.json"]
table = "station.heartbeat"
retention = "delete_on_ack"
[pipeline.transport]
type = "heartbeat_sftp"
host = "hb.example.org"
"""


def _station(tmp_path: Path):
    """A synthetic station: pipelines.toml, sink.db, watermarks.db and one
    heartbeat file, each as the snapshot procedure leaves it (read-only)."""
    snap = tmp_path / "snap"
    snap.mkdir()
    heartbeat = tmp_path / "heartbeat"
    heartbeat.mkdir()
    beat = heartbeat / "beat-1.json"
    beat.write_text('{"station": "AC0G-T"}')

    sink = snap / "sink.db"
    conn = sqlite3.connect(sink)
    conn.executescript(SINK_DDL)
    for db, table, payload in ROWS:
        conn.execute(
            "INSERT INTO pending_uploads(target_db, target_table, "
            "schema_version, payload_json, queued_at) VALUES (?,?,?,?,?)",
            (db, table, 2, json.dumps(payload), payload["time"]))
    conn.commit()
    conn.close()

    watermarks = snap / "watermarks.db"
    store = SqliteWatermarkStore(watermarks)
    store.advance_cursor(*PSK_KEY, cursor=b"1", last_ack="2026-10-07T12:01:00+00:00")
    store.advance_cursor(*WSPRNET_KEY, cursor=b"6", last_ack="2026-10-07T12:01:00+00:00")
    store.advance_cursor(*CYCLE_KEY, cursor=b"2026-10-07T12:00:00Z",
                         last_ack="2026-10-07T12:02:10+00:00")
    store.enqueue_deliverable(
        pipeline="wspr-wsprdaemon", payload_blob=b"tar-bytes",
        enqueued_at="2026-10-07T12:02:10+00:00",
        next_attempt_at="2026-10-07T12:02:12+00:00",
        source_id=CYCLE_KEY[0], dest_id=CYCLE_KEY[1], table=CYCLE_KEY[2],
        cursor_after=b"2026-10-07T12:00:00Z")
    store.close()

    manifest = snap / "pipelines.toml"
    manifest.write_text(MANIFEST.format(heartbeat=heartbeat))
    for f in (sink, watermarks, manifest):
        f.chmod(0o444)
    return manifest, sink, watermarks, beat


def _args(manifest, sink, watermarks, old=SRC, new=SRC, now=NOW):
    return ["--manifest", str(manifest), "--sink", str(sink),
            "--watermarks", str(watermarks), "--old", str(old),
            "--new", str(new), "--now", now]


def _section(out: str, name: str) -> str:
    """The five lines the tool prints for one pipeline."""
    lines = out.splitlines()
    start = lines.index(next(l for l in lines if l.startswith(f"{name}: ")))
    return "\n".join(lines[start:start + 5])


def _changed_tree(tmp_path: Path, module: str, old: str, new: str) -> Path:
    """A copy of this tree with one line of one module changed."""
    tree = tmp_path / "new-src"
    shutil.copytree(SRC, tree, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    path = tree / "hs_uploader" / module
    text = path.read_text()
    assert text.count(old) == 1
    path.write_text(text.replace(old, new))
    return tree


def test_one_tree_against_itself_agrees(tmp_path, capsys):
    manifest, sink, watermarks, _ = _station(tmp_path)
    assert nc.main(_args(manifest, sink, watermarks)) == 0
    out = capsys.readouterr().out

    psk = _section(out, "psk-pskreporter")
    assert psk.startswith("psk-pskreporter: AGREE")
    assert "  NEW key  (" + ", ".join(PSK_KEY) + ")" in psk
    # Rows 2-4 pass the filter; row 5 carries forward_to_pskreporter = 1.
    assert "3 records, rows 2..4, cursor 1 -> 4" in psk

    wsprnet = _section(out, "wspr-wsprnet")
    assert wsprnet.startswith("wspr-wsprnet: AGREE")
    # Row 9 beats row 8 on SNR; row 6's partition shipped before the cursor.
    assert "2 records, rows 9..11, cursor 6 -> 11" in wsprnet

    cycle = _section(out, "wspr-wsprdaemon")
    assert cycle.startswith("wspr-wsprdaemon: AGREE")
    # The 12:02 cycle: two spots, one noise row and two psk rows.
    assert ("5 records, cursor 2026-10-07T12:00:00Z -> 2026-10-07T12:02:00Z"
            in cycle)
    assert "; 1 queued" in cycle

    beat = _section(out, "heartbeat")
    assert beat.startswith("heartbeat: AGREE")
    assert "1 record, cursor (empty) -> <delete-on-ack>" in beat

    assert "4 pipelines: 4 agree, 0 disagree" in out
    assert out.rstrip().endswith("RESULT: AGREE")


def test_it_writes_nothing_it_was_given(tmp_path, capsys):
    manifest, sink, watermarks, beat = _station(tmp_path)
    given = (manifest, sink, watermarks, beat)
    before = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
              for p in given}
    assert nc.main(_args(manifest, sink, watermarks)) == 0
    assert {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in given} == before
    # The heartbeat source deletes on ack.  The recorder stops the pump at
    # the send, so no commit runs and the file stays.
    assert beat.exists()
    # No sidecar appeared beside the snapshot either.
    assert sorted(p.name for p in sink.parent.iterdir()) == [
        "pipelines.toml", "sink.db", "watermarks.db"]


def test_a_changed_source_id_disagrees(tmp_path, capsys):
    manifest, sink, watermarks, _ = _station(tmp_path)
    # SqliteSource names a producer in its key: the change v3.71 makes on
    # purpose and v3.70 must not.
    new = _changed_tree(
        tmp_path, "sources/sqlite.py",
        'return f"sqlite:{self.database}.{self.table}"',
        'return f"sqlite:{self.database}.{self.table}@psk-recorder"')
    assert nc.main(_args(manifest, sink, watermarks, new=new)) == 1
    out = capsys.readouterr().out

    psk = _section(out, "psk-pskreporter")
    assert psk.startswith("psk-pskreporter: DISAGREE")
    assert "  OLD key  (sqlite:psk.spots, " in psk
    assert "  NEW key  (sqlite:psk.spots@psk-recorder, " in psk
    # Under its new key NEW finds no send record, and start_at = "now"
    # puts it past every stored row.
    assert "  NEW next no batch; stored cursor (empty); 0 queued" in psk
    assert _section(out, "wspr-wsprnet").startswith("wspr-wsprnet: DISAGREE")
    # WsprCycleSource and the heartbeat form their keys elsewhere.
    assert _section(out, "wspr-wsprdaemon").startswith("wspr-wsprdaemon: AGREE")
    assert _section(out, "heartbeat").startswith("heartbeat: AGREE")
    assert "4 pipelines: 2 agree, 2 disagree" in out
    assert out.rstrip().endswith("RESULT: DISAGREE")


def test_a_changed_row_selection_disagrees_under_the_same_key(tmp_path, capsys):
    manifest, sink, watermarks, _ = _station(tmp_path)
    new = _changed_tree(tmp_path, "sources/sqlite.py",
                        '"id > ?",', '"id >= ?",')
    assert nc.main(_args(manifest, sink, watermarks, new=new)) == 1
    psk = _section(capsys.readouterr().out, "psk-pskreporter")
    assert psk.startswith("psk-pskreporter: DISAGREE")
    key = "(" + ", ".join(PSK_KEY) + ")"
    assert f"  OLD key  {key}" in psk and f"  NEW key  {key}" in psk
    # NEW re-sends row 1, which the stored cursor already covers.
    assert "  NEW next 4 records, rows 1..4, cursor 1 -> 4" in psk


def test_a_changed_key_alone_disagrees(tmp_path, capsys):
    manifest, sink, watermarks, _ = _station(tmp_path)
    # The heartbeat stores no cursor, so only its key can tell the trees apart.
    new = _changed_tree(tmp_path, "sources/files.py",
                        'source_id or f"files:{self.root}"',
                        'source_id or f"file:{self.root}"')
    assert nc.main(_args(manifest, sink, watermarks, new=new)) == 1
    beat = _section(capsys.readouterr().out, "heartbeat").splitlines()
    assert beat[0] == "heartbeat: DISAGREE"
    assert beat[3].replace("OLD", "NEW") == beat[4]


def test_a_given_file_that_changes_fails_the_check(tmp_path, capsys, monkeypatch):
    manifest, sink, watermarks, _ = _station(tmp_path)
    real_run_tree = nc._run_tree

    def run_tree_then_write(label, *args, **kwargs):
        result = real_run_tree(label, *args, **kwargs)
        if label == "NEW":    # a live writer touches the file mid-check
            watermarks.chmod(0o644)
            with open(watermarks, "ab") as fh:
                fh.write(b"\0")
        return result

    monkeypatch.setattr(nc, "_run_tree", run_tree_then_write)
    assert nc.main(_args(manifest, sink, watermarks)) == 1
    assert "ERROR: a given file changed while the check ran" in capsys.readouterr().out


def test_a_text_cursor_shows_the_same_error_in_both_trees(tmp_path, capsys):
    manifest, sink, watermarks, _ = _station(tmp_path)
    watermarks.chmod(0o644)
    conn = sqlite3.connect(watermarks)
    conn.execute("UPDATE watermarks SET cursor = CAST(cursor AS TEXT) "
                 "WHERE source_id = ?", (PSK_KEY[0],))
    conn.commit()
    conn.close()
    # Both trees fail alike, so they agree, and the summary says so aloud.
    assert nc.main(_args(manifest, sink, watermarks)) == 0
    out = capsys.readouterr().out
    psk = _section(out, "psk-pskreporter")
    assert psk.startswith("psk-pskreporter: AGREE")
    assert "  OLD next error TypeError: " in psk
    assert ("An error stopped these pipelines, so the check shows nothing of "
            "what they send: psk-pskreporter") in out
    assert out.rstrip().endswith("RESULT: AGREE (1 raised an error; see above)")


def test_both_trees_read_the_frozen_clock(tmp_path, capsys):
    manifest, sink, watermarks, _ = _station(tmp_path)
    # At 12:03:30 the 12:02 cycle has not closed, so nothing ships.  A tree
    # that read the real clock would ship it.  (This run also spells the
    # manifest flag --pipelines, as Task 10 does; both spellings work.)
    args = _args(manifest, sink, watermarks, now="2026-10-07T12:03:30Z")
    args[args.index("--manifest")] = "--pipelines"
    assert nc.main(args) == 0
    cycle = _section(capsys.readouterr().out, "wspr-wsprdaemon")
    assert cycle.startswith("wspr-wsprdaemon: AGREE")
    for tree in ("OLD", "NEW"):
        assert (f"  {tree} next no batch; stored cursor 2026-10-07T12:00:00Z; "
                "1 queued") in cycle


def test_rows_still_in_the_wal_reach_the_copy(tmp_path, capsys):
    manifest, sink, watermarks, _ = _station(tmp_path)
    sink.chmod(0o644)
    conn = sqlite3.connect(sink)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute(
            "INSERT INTO pending_uploads(target_db, target_table, "
            "schema_version, payload_json, queued_at) VALUES (?,?,?,?,?)",
            ("psk", "spots", 2, json.dumps({
                "time": "2026-10-07T12:04:15Z", "mode": "ft8",
                "tx_call": "K6FGH", "forward_to_pskreporter": 0}),
             "2026-10-07T12:04:15Z"))
        conn.commit()
        assert Path(f"{sink}-wal").exists()
        assert nc.main(_args(manifest, sink, watermarks)) == 0
    finally:
        conn.close()
    psk = _section(capsys.readouterr().out, "psk-pskreporter")
    assert "4 records, rows 2..12, cursor 1 -> 12" in psk


def test_a_directory_without_hs_uploader_is_a_usage_error(tmp_path, capsys):
    manifest, sink, watermarks, _ = _station(tmp_path)
    with pytest.raises(SystemExit) as exc:
        nc.main(_args(manifest, sink, watermarks, old=tmp_path))
    assert exc.value.code == 2
    assert "holds no hs_uploader package" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_neutrality_check.py -v`
Expected: collection fails with
`FileNotFoundError: [Errno 2] No such file or directory: '/home/mjh/hamsci/repos/hs-uploader/tools/neutrality_check.py'`;
the summary reads `1 error`.

- [ ] **Step 3: Create `tools/neutrality_check.py`**

```python
#!/usr/bin/env python3
"""neutrality_check: show that two hs-uploader trees would send the same thing.

v3.70 promises to change nothing a station sends (sigmond
tasks/plan-sink-control.md §10.4).  This tool tests that promise on copies
of a station's state.  It builds every pipeline in a pipelines.toml twice,
once with an OLD hs-uploader source tree and once with a NEW one, each in
its own Python process.  For each pipeline it reports:

  * the send-record key the tree's core reads: (source_id, dest_id, table);
  * the send record (cursor) stored under that key;
  * how many queued retries wait under the pipeline's name;
  * the next batch the source hands its transport: the number of records,
    the first and last pending_uploads row id when the rows carry one, the
    cursor the batch would store, and a digest of the records.

It then says whether OLD and NEW agree.

Usage, from an hs-uploader checkout:

    .venv/bin/python tools/neutrality_check.py \\
        --manifest pipelines.toml --sink sink.db --watermarks watermarks.db \\
        --old OLD/src --new NEW/src [--now 2026-10-07T15:51:00Z]

--old and --new each name a directory that holds the hs_uploader package,
for example the src/ of `git archive <sha> | tar -x -C DIR`.

Nothing leaves the host, and nothing writes to the given files:

  * each tree reads its own copy of sink.db and watermarks.db, made in a
    temporary directory (set TMPDIR to choose where);
  * a recorder replaces every transport.  It borrows the real transport's
    name and table, so the tree forms its real key.  It captures the first
    batch and stops the pump before any send, cursor advance, retry or
    commit;
  * the check counts queued retries and never replays them;
  * the tool hashes the given files before and after the run, and fails
    if anything changed them.

Filetree pipelines (GRAPE, the magnetometer, the heartbeat) read their
spool directories on the machine that runs the check.  A dev box has none
of those directories, so there the check compares only their keys.

--now freezes the clock that the wspr_cycle and sqlite sources consult, so
both trees judge at the same instant which WSPR cycles have closed.  It
defaults to the current UTC time, read once and handed to both trees.
Pass the snapshot's time to judge cycles as the station saw them.

Exit status: 0 when every pipeline agrees; 1 when any pipeline disagrees,
a tree fails to build, or a given file changed; 2 on a usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# The fields of one pipeline's result that OLD and NEW must share.
_FIELDS = ("key", "stored", "queued", "batch", "error")

# Modules whose clock --now freezes.  Transports also read the clock, but
# the recorder stops every pump before a transport runs.
_CLOCK_MODULES = (
    "hs_uploader.sources.wspr_cycle",
    "hs_uploader.sources.wspr_completion",
    "hs_uploader.sources.sqlite",
)


# ---- worker: runs inside one tree's own process ------------------------------


class _Captured(BaseException):
    """The recorder raises this at the first send.  It stops the pump before
    the tree advances a cursor, queues a retry or commits a batch.  It
    derives from BaseException so that no `except Exception` in the tree
    swallows it."""


class _Recorder:
    """Stands in for a pipeline's transport.  It borrows the real transport's
    name, table, ACCEPTS and batch policy, so the tree forms the key it
    forms in production.  It keeps the first batch and stops the pump."""

    def __init__(self, inner):
        self.inner = inner
        self.name = inner.name
        self.ACCEPTS = getattr(inner, "ACCEPTS", {})
        self.batch = None

    def primary_table(self):
        return self.inner.primary_table()

    def batch_policy(self):
        return self.inner.batch_policy()

    def ship(self, batch, identity):
        self.batch = batch
        raise _Captured()

    def serialize_for_retry(self, batch, identity):
        raise _Captured()

    def replay(self, payload_blob, identity):
        raise _Captured()


def _text(cursor) -> str | None:
    if cursor is None:
        return None
    if isinstance(cursor, (bytes, bytearray, memoryview)):
        return bytes(cursor).decode("ascii", "backslashreplace")
    return str(cursor)


def _describe(batch, ids: list) -> dict:
    records = [
        [r.table, r.time.isoformat(), dict(r.columns),
         str(r.payload_path) if r.payload_path else None]
        for r in batch.records
    ]
    blob = json.dumps(records, sort_keys=True, default=str).encode("utf-8")
    return {
        "records": len(records),
        "first_id": ids[0] if ids else None,
        "last_id": ids[-1] if ids else None,
        "cursor_after": _text(batch.cursor_after),
        "digest": hashlib.sha256(blob).hexdigest(),
    }


def _tap_sqlite(sqlite3, sink: Path, ids: list) -> None:
    """Record the pending_uploads ids that each read of the sink copy returns.

    SqliteSource puts no row id on its Records, so the check reads the ids
    where the source reads them: from every query on the sink copy whose
    first result column is `id`.  Connections to any other file stay
    untouched."""
    real_connect = sqlite3.connect
    target = os.path.realpath(sink)

    class _Rows:
        def __init__(self, rows, description):
            self._rows = list(rows)
            self.description = description

        def fetchall(self):
            rows, self._rows = self._rows, []
            return rows

        def fetchone(self):
            return self._rows.pop(0) if self._rows else None

        def fetchmany(self, size=1):
            rows, self._rows = self._rows[:size], self._rows[size:]
            return rows

        def __iter__(self):
            return iter(self.fetchall())

    class _TapConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            cur = super().execute(sql, parameters)
            if cur.description and cur.description[0][0] == "id":
                rows = cur.fetchall()
                ids.extend(row[0] for row in rows)
                return _Rows(rows, cur.description)
            return cur

    def connect(database, *args, **kwargs):
        if "factory" not in kwargs and os.path.realpath(str(database)) == target:
            kwargs["factory"] = _TapConnection
        return real_connect(database, *args, **kwargs)

    sqlite3.connect = connect


def _freeze_clock(now: datetime) -> None:
    import datetime as dt
    import importlib

    class _FrozenDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return now.astimezone().replace(tzinfo=None)
            return now.astimezone(tz)

        @classmethod
        def utcnow(cls):
            return now.astimezone(dt.timezone.utc).replace(tzinfo=None)

    for name in _CLOCK_MODULES:
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        if getattr(mod, "datetime", None) is dt.datetime:
            mod.datetime = _FrozenDatetime


def _worker(tree: Path, manifest_path: Path, sink: Path, watermarks: Path,
            now_iso: str, out: Path) -> int:
    import logging
    import sqlite3

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s:%(name)s:%(message)s")
    sys.path.insert(0, str(tree))
    import hs_uploader

    pkg = Path(hs_uploader.__file__).resolve().parent
    if pkg.parent != tree.resolve():
        print(f"imported hs_uploader from {pkg}, not from {tree}", file=sys.stderr)
        return 3

    ids: list = []
    _tap_sqlite(sqlite3, sink, ids)
    now = _parse_now(now_iso)
    _freeze_clock(now)

    from hs_uploader.core import Uploader
    from hs_uploader.daemon import build_all_pipelines, load_manifest
    from hs_uploader.watermark.sqlite import SqliteWatermarkStore

    class _TapStore(SqliteWatermarkStore):
        """The tree's own store, opened on the copy.  It records each key the
        tree's core reads.  It hides queued retries from the pump, so the
        pump reaches the source; the check counts them instead."""

        def __init__(self, path):
            super().__init__(path)
            self.reads: list = []

        def get_cursor(self, source_id, dest_id, table):
            cursor = super().get_cursor(source_id, dest_id, table)
            self.reads.append(((source_id, dest_id, table), cursor))
            return cursor

        def pop_due_deliverable(self, pipeline, *, now):
            return None

        def deliverable_count(self, pipeline=None):
            return 0

        def queued(self, pipeline):
            return super().deliverable_count(pipeline)

    manifest = load_manifest(manifest_path)
    entries = manifest.get("pipeline", []) or []
    for entry in entries:
        source = entry.get("source") or {}
        if str(source.get("type", "")).strip().lower() == "wspr_cycle":
            source["db_path"] = str(sink)    # SqliteSource reads SIGMOND_SQLITE_PATH
    named = [str(e["name"]) for e in entries if e.get("name")]

    store = _TapStore(watermarks)
    result: dict = {"tree": str(pkg), "pipelines": {}, "unbuilt": []}
    try:
        pipelines = build_all_pipelines(manifest, watermark=store)
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        result["build_error"] = f"{type(exc).__name__}: {exc}"
        out.write_text(json.dumps(result))
        return 0

    built = [p.name for p in pipelines]
    result["unbuilt"] = [n for n in named if n not in built]
    for pipe in pipelines:
        label, n = pipe.name, 2
        while label in result["pipelines"]:
            label, n = f"{pipe.name}#{n}", n + 1
        recorder = _Recorder(pipe.transport)
        pipe.transport = recorder
        store.reads.clear()
        ids.clear()
        entry = {"key": None, "stored": None, "queued": None,
                 "batch": None, "error": None}
        try:
            entry["queued"] = store.queued(pipe.name)
            uploader = Uploader([pipe], now_fn=now.timestamp)
            try:
                uploader.pump()
            except _Captured:
                pass
            finally:
                uploader.close()
        except Exception as exc:  # noqa: BLE001 - reported per pipeline
            entry["error"] = f"{type(exc).__name__}: {exc}"
        if store.reads:
            key, cursor = store.reads[0]
            entry["key"] = list(key)
            entry["stored"] = _text(cursor)
        if recorder.batch is not None:
            entry["batch"] = _describe(recorder.batch, list(ids))
        result["pipelines"][label] = entry
    out.write_text(json.dumps(result))
    return 0


# ---- parent: copy, run both trees, compare ------------------------------------


def _parse_now(text: str) -> datetime:
    value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError(f"--now needs a UTC offset or a trailing Z: {text!r}")
    return value.astimezone(timezone.utc)


def _with_wal(path: Path) -> list:
    wal = Path(f"{path}-wal")
    return [path, wal] if wal.exists() else [path]


def _fingerprint(paths) -> dict:
    prints = {}
    for p in paths:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        prints[str(p)] = h.hexdigest()
    return prints


def _copy_db(src: Path, dst: Path) -> None:
    """Copy a database and any -wal beside it.  The copy carries pages not
    yet checkpointed; SQLite recovers them when the worker opens it."""
    shutil.copyfile(src, dst)
    wal = Path(f"{src}-wal")
    if wal.exists():
        shutil.copyfile(wal, Path(f"{dst}-wal"))


def _run_tree(label: str, tree: Path, args, now_iso: str, scratch: Path) -> dict:
    work = scratch / label.lower()
    work.mkdir()
    sink, watermarks = work / "sink.db", work / "watermarks.db"
    _copy_db(args.sink, sink)
    _copy_db(args.watermarks, watermarks)
    out = work / "result.json"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["HS_UPLOADER_STATE_DIR"] = str(work)
    env["SIGMOND_SQLITE_PATH"] = str(sink)
    cmd = [sys.executable, "-B", str(Path(__file__).resolve()),
           "--worker", str(tree), "--manifest", str(args.manifest),
           "--sink", str(sink), "--watermarks", str(watermarks),
           "--now", now_iso, "--out", str(out)]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                          timeout=1800)
    for line in (proc.stdout + proc.stderr).splitlines():
        print(f"[{label}] {line}", file=sys.stderr)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"the {label} tree's worker exited {proc.returncode}")
    return json.loads(out.read_text())


def _key_text(entry) -> str:
    if entry is None:
        return "(not built)"
    if entry["key"] is None:
        return "(no key read)"
    return "(" + ", ".join(entry["key"]) + ")"


def _next_text(entry) -> str:
    if entry is None:
        return "(not built)"
    if entry["error"]:
        return f"error {entry['error']}"
    stored = entry["stored"] or "(empty)"
    queued = f"{entry['queued']} queued"
    batch = entry["batch"]
    if batch is None:
        return f"no batch; stored cursor {stored}; {queued}"
    n = batch["records"]
    rows = ""
    if batch["first_id"] is not None:
        rows = f"rows {batch['first_id']}..{batch['last_id']}, "
    return (f"{n} record{'' if n == 1 else 's'}, {rows}"
            f"cursor {stored} -> {batch['cursor_after']}, "
            f"digest {batch['digest'][:12]}; {queued}")


def _agree(old, new) -> bool:
    if old is None or new is None:
        return False
    return all(old.get(f) == new.get(f) for f in _FIELDS)


def _report(old: dict, new: dict) -> int:
    names = list(old["pipelines"])
    names += [n for n in new["pipelines"] if n not in names]
    agree = 0
    for name in names:
        o, n = old["pipelines"].get(name), new["pipelines"].get(name)
        same = _agree(o, n)
        agree += same
        print(f"{name}: {'AGREE' if same else 'DISAGREE'}")
        print(f"  OLD key  {_key_text(o)}")
        print(f"  NEW key  {_key_text(n)}")
        print(f"  OLD next {_next_text(o)}")
        print(f"  NEW next {_next_text(n)}")
    for label, res in (("OLD", old), ("NEW", new)):
        if res["unbuilt"]:
            print(f"{label} built no pipeline for: {', '.join(res['unbuilt'])}")
    raised = [n for n in names
              if any((res["pipelines"].get(n) or {}).get("error") for res in (old, new))]
    if raised:
        print(f"An error stopped these pipelines, so the check shows nothing "
              f"of what they send: {', '.join(raised)}")
    print(f"{len(names)} pipelines: {agree} agree, {len(names) - agree} disagree")
    ok = agree == len(names) and old["unbuilt"] == new["unbuilt"]
    note = f" ({len(raised)} raised an error; see above)" if raised else ""
    print(f"RESULT: {'AGREE' if ok else 'DISAGREE'}{note}")
    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="neutrality_check",
        description="Show whether two hs-uploader trees form the same "
                    "send-record keys and select the same next batch, on "
                    "copies of a station's pipelines.toml, sink.db and "
                    "watermarks.db.")
    p.add_argument("--manifest", "--pipelines", dest="manifest", type=Path,
                   required=True,
                   help="the station's /etc/hs-uploader/pipelines.toml (a copy)")
    p.add_argument("--sink", type=Path, required=True,
                   help="a snapshot of /var/lib/sigmond/sink.db")
    p.add_argument("--watermarks", type=Path, required=True,
                   help="a snapshot of /var/lib/hs-uploader/watermarks.db")
    p.add_argument("--old", type=Path, help="directory that holds the OLD hs_uploader")
    p.add_argument("--new", type=Path, help="directory that holds the NEW hs_uploader")
    p.add_argument("--now", help="UTC time both trees read as now "
                                 "(default: the current time, read once)")
    p.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--out", type=Path, help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if args.worker is not None:
        return _worker(args.worker, args.manifest, args.sink, args.watermarks,
                       args.now, args.out)

    for flag in ("old", "new"):
        tree = getattr(args, flag)
        if tree is None:
            p.error(f"--{flag} is required")
        if not (tree / "hs_uploader" / "__init__.py").is_file():
            p.error(f"--{flag} {tree} holds no hs_uploader package")
    for flag in ("manifest", "sink", "watermarks"):
        if not getattr(args, flag).is_file():
            p.error(f"--{flag} {getattr(args, flag)} is not a file")
    try:
        now = _parse_now(args.now) if args.now else datetime.now(timezone.utc)
    except ValueError as exc:
        p.error(str(exc))
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    given = [args.manifest, *_with_wal(args.sink), *_with_wal(args.watermarks)]
    before = _fingerprint(given)
    print(f"neutrality check: OLD {args.old}  NEW {args.new}  now {now_iso}")
    try:
        with tempfile.TemporaryDirectory(prefix="neutrality-") as scratch:
            old = _run_tree("OLD", args.old, args, now_iso, Path(scratch))
            new = _run_tree("NEW", args.new, args, now_iso, Path(scratch))
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"ERROR: {exc}")
        return 1
    if _fingerprint(given) != before:
        print("ERROR: a given file changed while the check ran.  Run the "
              "check on a snapshot, never on a live database.")
        return 1
    failed = False
    for label, res in (("OLD", old), ("NEW", new)):
        if "build_error" in res:
            print(f"ERROR: the {label} tree could not build the pipelines: "
                  f"{res['build_error']}")
            failed = True
    if failed:
        return 1
    return _report(old, new)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_neutrality_check.py -v`
Expected: 10 passed.

- [ ] **Step 5: Watch the tests catch four broken tools**

A test that nobody has watched fail proves nothing.  Break the tool four ways, one at a time, and
restore it after each:

```bash
KEEP=$(mktemp)
cp tools/neutrality_check.py "$KEEP"
mutate() {   # mutate OLD1 NEW1 [OLD2 NEW2 ...], run the tests, restore the tool
  .venv/bin/python - "$@" <<'PY'
import sys, pathlib
p = pathlib.Path("tools/neutrality_check.py")
t = p.read_text()
for old, new in zip(sys.argv[1::2], sys.argv[2::2]):
    assert t.count(old) == 1, old
    t = t.replace(old, new)
p.write_text(t)
PY
  .venv/bin/pytest tests/test_neutrality_check.py -q 2>&1 | grep -E "^FAILED|passed|failed" | sed 's/ - .*//'
  cp "$KEEP" tools/neutrality_check.py
}
# 1. Each tree reads the real clock.
mutate '    _freeze_clock(now)' '    pass'
# 2. The comparison ignores the batch.
mutate '_FIELDS = ("key", "stored", "queued", "batch", "error")' '_FIELDS = ("key", "stored", "queued", "error")'
# 3. The recorder acknowledges instead of stopping the pump.
mutate '        self.batch = batch
        raise _Captured()' '        self.batch = batch
        from hs_uploader.core import Outcome
        return Outcome.acked()'
# 4. The worker imports whatever hs_uploader Python finds first.
mutate '    sys.path.insert(0, str(tree))
' '' '    if pkg.parent != tree.resolve():' '    if False:'
cmp "$KEEP" tools/neutrality_check.py && echo "tool restored"
```

Expected, in order (timings trimmed):
```
FAILED tests/test_neutrality_check.py::test_both_trees_read_the_frozen_clock
1 failed, 9 passed
FAILED tests/test_neutrality_check.py::test_a_changed_row_selection_disagrees_under_the_same_key
1 failed, 9 passed
```
then seven FAILED lines, among them `test_it_writes_nothing_it_was_given` (the heartbeat file got
deleted on the ack), and `7 failed, 3 passed`; then
```
FAILED tests/test_neutrality_check.py::test_a_changed_source_id_disagrees
FAILED tests/test_neutrality_check.py::test_a_changed_row_selection_disagrees_under_the_same_key
FAILED tests/test_neutrality_check.py::test_a_changed_key_alone_disagrees
3 failed, 7 passed
tool restored
```
If any mutation leaves all ten passing, stop: a test has lost its power.

- [ ] **Step 6: Name the tool in CLAUDE.md**

In `CLAUDE.md`, "## Commands" block, find:

```
# Build distribution
uv build
```

and replace it with:

```
# Build distribution
uv build

# Do two trees send the same thing?  Runs OLD and NEW on copies of a
# station's state; never writes the given files (v3.70 neutrality proof)
.venv/bin/python tools/neutrality_check.py --manifest pipelines.toml \
    --sink sink.db --watermarks watermarks.db --old OLD/src --new NEW/src
```

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: `410 passed, 1 skipped` (Task 4's 400 passed, 1 skipped, plus these 10).

- [ ] **Step 8: Run v3.69's hs-uploader against this tree on the synthetic station**

This turns the tool on Tasks 1-4 before any station snapshot exists.  OLD comes from
`git archive` of 3e97223, the base of this plan, the way Task 10 extracts a station's SHA:

```bash
SCR=$(mktemp -d)
mkdir "$SCR/old" "$SCR/station"
git archive 3e97223 | tar -x -C "$SCR/old"
.venv/bin/python - "$SCR" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "tests")
import test_neutrality_check as t
scr = Path(sys.argv[1])
manifest, sink, watermarks, _ = t._station(scr / "station")
sys.exit(t.nc.main(t._args(manifest, sink, watermarks,
                           old=scr / "old" / "src", new=t.SRC)))
PY
echo "exit $?"
rm -rf "$SCR"
```

Expected: four pipelines, each `AGREE`, with these `next` lines for both trees:
```
psk-pskreporter: AGREE
  OLD next 3 records, rows 2..4, cursor 1 -> 4, digest f943a02551d6; 0 queued
wspr-wsprdaemon: AGREE
  OLD next 5 records, cursor 2026-10-07T12:00:00Z -> 2026-10-07T12:02:00Z, digest f996d1e4a582; 1 queued
wspr-wsprnet: AGREE
  OLD next 2 records, rows 9..11, cursor 6 -> 11, digest 9280a4e4ee08; 0 queued
heartbeat: AGREE
  OLD next 1 record, cursor (empty) -> <delete-on-ack>, digest <varies with the temp path>; 0 queued
4 pipelines: 4 agree, 0 disagree
RESULT: AGREE
exit 0
```
A `DISAGREE` here means Tasks 1-4 changed a key or a row selection.  Stop and report it; do not
commit until the cause is understood.

- [ ] **Step 9: Commit**

```bash
git add tools/neutrality_check.py tests/test_neutrality_check.py CLAUDE.md
git commit -m "tools: neutrality_check, the old-vs-new proof for v3.70

Builds every pipeline in a station's pipelines.toml with two hs-uploader
trees, each in its own process on its own copy of sink.db and
watermarks.db.  A recorder takes each transport's place under the real
name and stops the pump at the first send, so nothing leaves the host
and nothing writes to the given files.  Prints, per pipeline, the key
each tree's core reads and the next batch its source selects, and exits
1 when the trees disagree.  sigmond tasks/plan-sink-control.md §10.4.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```

---


### Task 6: `sigmond.hamsci_sink` becomes a compatibility import, with a bundled copy and a drift test

Task 1 moved the sink writer into hs-uploader as `hs_uploader.sink`.  Clients still import
`sigmond.hamsci_sink`, and they keep doing so through v3.70.  sigmond's package now hands each client
hs-uploader's module when the client's venv can import it.  Three clients cannot: codar-sounder, hf-tec
and superdarn-sounder carry sigmond but no hs-uploader (spec §13.2).  A bare re-export would fail to
import there, and each client's `try/except` would turn that failure into a silent stop of its sink
writes.  `smd update` also pulls sigmond before hs-uploader (`bin/smd` self-updates first, then pulls
components in sorted order), so a recorder restarted between the two pulls meets an hs-uploader with no
`sink` package.  For these cases sigmond ships a bundled copy, `lib/sigmond/hamsci_sink/_bundled.py`:
Task 1's `src/hs_uploader/sink/writer.py`, verbatim, below a marked comment header (D14).  A drift test
compares the two files whenever an hs-uploader checkout sits beside sigmond's, as on the devbox.

**The copy comes from Task 1's file, never from this text.**  Step 4 copies Task 1's committed
`src/hs_uploader/sink/writer.py` by command, and the drift test holds the copy to it from then on.
The Interfaces below list what this task needs from that file; Task 1's own Interfaces promise each
item.

The old `writer.py` goes away.  `__init__.py` registers the chosen module under the old name
`sigmond.hamsci_sink.writer`, so every name importers use today still resolves.  A test that sets a
module global through that name (`tests/test_hamsci_sink.py` sets `_default_sqlite_writable` and
`_DEFAULT_SQLITE_PATH`) still changes what `Writer.from_env` reads.  A plain re-export would leave
hs-uploader's globals untouched.

Task 1 carried all 28 tests of `tests/test_hamsci_sink.py` into hs-uploader's
`tests/test_sink_writer.py` and left sigmond's file for this task to trim.  Step 6 first runs all 28
against the bundled copy, once, in sigmond's own venv, which has no hs-uploader.  Step 7 then keeps the
five classes that reach the writer through the old import paths: they set module globals through
`sigmond.hamsci_sink.writer`, import `HEALTH_*`, `_resolve_db_alias` and `DEFAULT_SQLITE_BATCH_ROWS`
from it, and catch the `BufferFull` the package exports.  The four classes that test only the
writer's behaviour (`TestEnabledWriter`, `TestTimeBasedAutoFlush`, `TestGroupWritablePerms`,
`TestCrossThreadUse`) leave sigmond; they keep running in hs-uploader against the same bytes.

This changes nothing the station sends.  Both copies hold the same code, which writes the same rows
plus Task 1's two columns.

**Importers, read across `/home/mjh/hamsci/repos` at the base** (every name below must keep resolving):

| Importer | What it imports or uses |
|---|---|
| `psk-recorder/src/psk_recorder/core/ch_tailer.py:563` | `from sigmond.hamsci_sink import Writer` |
| `meteor-scatter/src/meteor_scatter/core/ch_tailer.py:481` | `from sigmond.hamsci_sink import Writer` |
| `wspr-recorder/wspr_recorder/spot_sink.py:161, 612` | `from sigmond.hamsci_sink import Writer` |
| `codar-sounder/src/codar_sounder/core/daemon.py:493` | `from sigmond.hamsci_sink import Writer as _HamsciWriter`; logs `.database` |
| `superdarn-sounder/src/superdarn_sounder/core/output.py:48` | `from sigmond.hamsci_sink import Writer` |
| `hf-tec/src/hf_tec/core/output.py:227` | `from sigmond.hamsci_sink import Writer` (its call raises today; out of scope) |
| `sigmond/scripts/bench_sqlite_contention.py:33` | `from sigmond.hamsci_sink import SqliteConfig, Writer` |
| `sigmond/tests/test_hamsci_sink.py:14-18, 37, 48, 272, 287, 314, 500, 513` | `BufferFull`, `SqliteConfig`, `Writer`; from `.writer`: `HEALTH_DEGRADED`, `HEALTH_NOOP`, `HEALTH_OK`, `HEALTH_UNREACHABLE`, `_resolve_db_alias`, `DEFAULT_SQLITE_BATCH_ROWS`; sets `_default_sqlite_writable` and `_DEFAULT_SQLITE_PATH`; logger `sigmond.hamsci_sink` |

The clients also read `.health`, `.is_noop`, `.buffered`, `.database`, `.table` and `.schema_version`,
and call `insert`, `flush`, `close` and the context manager.  No other repo imports the writer:
mag-recorder, hamsci-physics, hf-timestd, station-web and hs-uploader mention it only in docstrings.
hfdl-recorder has no checkout here, so its call stays unverified.

**Files:**
- Create: `sigmond/lib/sigmond/hamsci_sink/_bundled.py` — a 14-line comment header, then Task 1's
  `hs-uploader/src/hs_uploader/sink/writer.py` byte for byte (Step 4 builds it by command).
- Delete: `sigmond/lib/sigmond/hamsci_sink/writer.py` (436 lines).  git records the pair as a rename,
  `writer.py → _bundled.py`.
- Modify: `sigmond/lib/sigmond/hamsci_sink/__init__.py` — replace all 33 lines.
- Create: `sigmond/tests/test_hamsci_sink_compat.py`.
- Modify: `sigmond/tests/test_hamsci_sink.py` (530 lines) — keep `_temp_db_path` and five classes
  (`TestNoOpMode` L29-58, `TestConfigAndAlias` L61-75, `TestUnreachableHandling` L206-244,
  `TestWriterFromEnvDispatch` L247-297, `TestFromEnvBatchRows` L300-328); drop `TestEnabledWriter`
  L78-203, `TestTimeBasedAutoFlush` L331-392, `TestGroupWritablePerms` L395-462, `TestCrossThreadUse`
  L469-530 and the stray `if __name__` block at L465-466; rewrite the docstring and imports.  Step 7
  gives the whole file.
- Modify: `sigmond/CLAUDE.md` — L179-189, from `## Sink backend selection` through
  ``ClickHouse install, use `smd admin storage migrate-to-sqlite` to clean it up.``
- Modify: `sigmond/etc/catalog.toml` — under `[client.hs-uploader]` (L144): the `description` at L146
  and the comment at L148-151.
- Modify: `sigmond/docs/scientist/becoming-a-client.md` (L5, L202, L221-222) and
  `sigmond/docs/scientist/data-and-timing.md` (L5, L227-230).  Both link to `writer.py`, and
  `tests/test_docs_links.py` fails once it goes.
- Modify: `sigmond/docs/contributor/orchestration.md` (L67-68), which names `hamsci_sink/writer.py`
  as home of the sink default.
- Unchanged, and must keep importing: `sigmond/scripts/bench_sqlite_contention.py`.

**Interfaces:**
- Consumes, from Task 1: `hs-uploader/src/hs_uploader/sink/writer.py`, committed in the sibling
  checkout `../hs-uploader`.  This task needs it to:
  - import only the standard library, with no relative import and no `hs_uploader` import, so a
    verbatim copy runs where hs-uploader does not;
  - define at module level every name listed in the table above, plus
    `DEFAULT_SQLITE_AUTO_FLUSH_SECONDS` and `logger = logging.getLogger("sigmond.hamsci_sink")`;
  - store `producer` and `local` on each row it writes, inferring `psk-recorder` for a `psk.spots` row
    with mode `ft8` and `meteor-scatter` for one with mode `msk144` when the caller names no producer,
    and `local` 0 (D12).
- Consumes, from Task 1: `hs-uploader/tests/test_sink_writer.py`, which carries the four classes
  Step 7 drops from sigmond.
- Produces:
  - `sigmond.hamsci_sink.SINK_IMPL: str` — `"hs_uploader"` or `"bundled"`.  Task 9's rig can print it
    in each client venv: expect `hs_uploader` for psk-recorder, meteor-scatter and wspr-recorder, and
    `bundled` for codar-sounder, hf-tec and superdarn-sounder.
  - `sigmond.hamsci_sink.writer` — the chosen module itself (`hs_uploader.sink.writer` or
    `sigmond.hamsci_sink._bundled`), registered in `sys.modules` under that name.
  - `sigmond.hamsci_sink.__all__ == ["Writer", "BufferFull", "SqliteConfig", "SINK_IMPL"]`.  The
    package adds no other name; Task 1's `PENDING_UPLOADS_DDL`, `ensure_columns` and `infer_producer`
    stay reachable through `sigmond.hamsci_sink.writer`.

**Decisions taken here** (none touches what the station sends):
- The fallback catches `ImportError` only.  That covers a venv with no hs-uploader and one whose
  hs-uploader predates `sink` (both raise `ModuleNotFoundError`).  Any other exception propagates, as
  any import error in the writer would today.
- The package logs nothing about its choice.  `SINK_IMPL` and `sigmond.hamsci_sink.writer.__file__`
  answer the question when someone asks it.
- sigmond keeps five of the nine classes in `tests/test_hamsci_sink.py`, the ones that go through
  the old import paths.  CI checks out sigmond alone, so there the drift test skips, and those twelve
  tests plus `test_bundled_writer_used_without_hs_uploader` exercise the bundled copy.
- The drift test skips only when `../hs-uploader/src` does not exist.  A sibling checkout without
  `sink/writer.py` fails with "pull hs-uploader", because a stale sibling has drifted too.

- [ ] **Step 1: Confirm the base**

Run:
```bash
cd /home/mjh/hamsci/repos/sigmond && git status --short && git log --oneline -1
git -C ../hs-uploader status --porcelain -- src/hs_uploader/sink/
git -C ../hs-uploader log --oneline -1 -- src/hs_uploader/sink/writer.py
```
Expected: no output from either status.  sigmond sits at `64cf83f` (or at a commit that adds only this
plan under `tasks/`).  The last log line names Task 1's commit.  If hs-uploader shows uncommitted
changes under `sink/`, stop: the copy must come from a committed file.

- [ ] **Step 2: Write the failing test**

Create `sigmond/tests/test_hamsci_sink_compat.py`:

```python
"""sigmond.hamsci_sink, the compatibility import (plan-sink-control D14, §10.4 item 1).

Since v3.70 the sink writer lives in hs-uploader as hs_uploader.sink.  Clients
still import sigmond.hamsci_sink.  It hands them hs-uploader's module when their
venv can import it, and otherwise a bundled copy that sigmond ships, because
codar-sounder, hf-tec and superdarn-sounder carry sigmond but no hs-uploader.
These tests pin that choice, the names importers use today, and the bundled
copy's sameness with hs-uploader's file.

Each choice runs in a child interpreter: this process's sys.modules already
holds whichever copy its first import picked.
"""

import ast
import difflib
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_LIB = _REPO / "lib"
_BUNDLED = _LIB / "sigmond" / "hamsci_sink" / "_bundled.py"
_HS_SRC = _REPO.parent / "hs-uploader" / "src"
_UPSTREAM = _HS_SRC / "hs_uploader" / "sink" / "writer.py"

_BEGIN = b"# ---- BEGIN sigmond bundled-copy header"
_END = b"# ---- END sigmond bundled-copy header"
_REFRESH = (
    "{ sed '/^# ---- END sigmond bundled-copy header/q' "
    "lib/sigmond/hamsci_sink/_bundled.py; "
    "cat ../hs-uploader/src/hs_uploader/sink/writer.py; } "
    "> lib/sigmond/hamsci_sink/_bundled.py.new && "
    "mv lib/sigmond/hamsci_sink/_bundled.py.new lib/sigmond/hamsci_sink/_bundled.py"
)


def _split_header(data: bytes) -> tuple:
    """Split the bundled copy into (its header lines, the bytes below them)."""
    lines = data.splitlines(keepends=True)
    if not lines or not lines[0].startswith(_BEGIN):
        raise AssertionError(f"{_BUNDLED} must start with a line {_BEGIN.decode()!r}")
    for i, line in enumerate(lines):
        if line.startswith(_END):
            return lines[: i + 1], b"".join(lines[i + 1:])
    raise AssertionError(f"{_BUNDLED} has no line starting {_END.decode()!r}")


def _child(script: str, extra_path=()) -> dict:
    """Run `script` in a fresh interpreter with lib/ (then extra_path) first on
    sys.path, and return the JSON object it prints last."""
    paths = [str(_LIB), *map(str, extra_path)]
    prelude = f"import json, sys\nsys.path[:0] = {paths!r}\n"
    proc = subprocess.run(
        [sys.executable, "-I", "-c", prelude + textwrap.dedent(script)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"child interpreter failed (rc={proc.returncode}):\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


class BundledCopyTests(unittest.TestCase):
    """The bundled copy holds a marked header, then hs-uploader's file unchanged."""

    def test_header_sits_at_the_top_once_and_holds_only_comments(self):
        data = _BUNDLED.read_bytes()
        header, _ = _split_header(data)
        self.assertTrue(all(line.startswith(b"#") for line in header), header)
        lines = data.splitlines()
        self.assertEqual(sum(line.startswith(_BEGIN) for line in lines), 1)
        self.assertEqual(sum(line.startswith(_END) for line in lines), 1)

    def test_imports_only_the_standard_library(self):
        tree = ast.parse(_BUNDLED.read_bytes())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, f"relative import at line {node.lineno}")
                found.add(node.module.split(".")[0])
        self.assertEqual(sorted(found - set(sys.stdlib_module_names)), [])

    def test_matches_hs_uploader_writer(self):
        if not _HS_SRC.is_dir():
            self.skipTest(f"no hs-uploader checkout beside sigmond at {_HS_SRC}")
        self.assertTrue(
            _UPSTREAM.is_file(),
            f"{_UPSTREAM} is missing: pull hs-uploader, which holds the sink writer since v3.70",
        )
        _, body = _split_header(_BUNDLED.read_bytes())
        upstream = _UPSTREAM.read_bytes()
        if body != upstream:
            diff = "".join(difflib.unified_diff(
                upstream.decode("utf-8", "replace").splitlines(keepends=True),
                body.decode("utf-8", "replace").splitlines(keepends=True),
                "hs-uploader/src/hs_uploader/sink/writer.py",
                "sigmond/lib/sigmond/hamsci_sink/_bundled.py, below its header",
                n=1,
            ))
            self.fail(
                "the bundled sink writer differs from hs-uploader's.  Refresh it "
                f"from the sigmond checkout:\n  {_REFRESH}\n\n{diff[:4000]}"
            )


class SelectionTests(unittest.TestCase):
    """Which copy sigmond.hamsci_sink hands a client, and SINK_IMPL saying so."""

    def test_bundled_writer_used_without_hs_uploader(self):
        """codar-sounder, hf-tec and superdarn-sounder: no hs-uploader in the venv."""
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "sink.db")
            got = _child(f"""
                sys.modules["hs_uploader"] = None   # import hs_uploader now fails
                import sqlite3
                import sigmond.hamsci_sink as hs
                from sigmond.hamsci_sink import _bundled, writer
                w = hs.Writer.from_env(table="spots", mode="psk",
                                       env={{"SIGMOND_SQLITE_PATH": {db!r}}})
                w.insert([{{"mode": "ft8"}}, {{"mode": "msk144"}}])
                w.close()
                rows = sqlite3.connect({db!r}).execute(
                    "SELECT target_db, target_table, producer, local "
                    "FROM pending_uploads ORDER BY id").fetchall()
                print(json.dumps({{
                    "impl": hs.SINK_IMPL,
                    "bundled": writer is _bundled and hs.Writer is _bundled.Writer,
                    "rows": rows,
                }}))
            """)
        self.assertEqual(got["impl"], "bundled")
        self.assertTrue(got["bundled"])
        self.assertEqual(got["rows"], [["psk", "spots", "psk-recorder", 0],
                                       ["psk", "spots", "meteor-scatter", 0]])

    def test_bundled_writer_used_when_hs_uploader_predates_the_sink(self):
        """smd update pulls sigmond before hs-uploader.  A recorder restarted
        between the two pulls meets an hs-uploader with no sink package."""
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "hs_uploader"
            old.mkdir()
            (old / "__init__.py").write_text('__version__ = "0.1.0"\n')
            got = _child("""
                import hs_uploader
                import sigmond.hamsci_sink as hs
                print(json.dumps({"impl": hs.SINK_IMPL, "found": hs_uploader.__file__}))
            """, extra_path=[tmp])
            self.assertEqual(Path(got["found"]).parent, old)
        self.assertEqual(got["impl"], "bundled")

    def test_hs_uploader_writer_preferred_when_importable(self):
        if not _UPSTREAM.is_file():
            self.skipTest(f"no hs-uploader sink writer at {_UPSTREAM}")
        got = _child("""
            import sigmond.hamsci_sink as hs
            from sigmond.hamsci_sink import writer
            import hs_uploader.sink.writer as up
            print(json.dumps({
                "impl": hs.SINK_IMPL,
                "module": writer is up and sys.modules["sigmond.hamsci_sink.writer"] is up,
                "names": [hs.Writer is up.Writer, hs.BufferFull is up.BufferFull,
                          hs.SqliteConfig is up.SqliteConfig],
                "file": up.__file__,
            }))
        """, extra_path=[_HS_SRC])
        self.assertEqual(got["impl"], "hs_uploader")
        self.assertTrue(got["module"])
        self.assertEqual(got["names"], [True, True, True])
        self.assertEqual(Path(got["file"]).resolve(), _UPSTREAM.resolve())


class SurfaceTests(unittest.TestCase):
    """Every name sigmond and the clients import from sigmond.hamsci_sink today."""

    def test_every_name_importers_use_today(self):
        import sigmond.hamsci_sink as hs
        from sigmond.hamsci_sink import SINK_IMPL, BufferFull, SqliteConfig, Writer  # noqa: F401
        from sigmond.hamsci_sink import writer
        from sigmond.hamsci_sink.writer import (  # noqa: F401
            DEFAULT_SQLITE_AUTO_FLUSH_SECONDS, DEFAULT_SQLITE_BATCH_ROWS,
            HEALTH_DEGRADED, HEALTH_NOOP, HEALTH_OK, HEALTH_UNREACHABLE,
            _DEFAULT_SQLITE_PATH, _default_sqlite_writable, _resolve_db_alias,
        )
        self.assertIn(SINK_IMPL, ("hs_uploader", "bundled"))
        self.assertEqual(sorted(hs.__all__),
                         ["BufferFull", "SINK_IMPL", "SqliteConfig", "Writer"])
        # sigmond.hamsci_sink.writer names the chosen module itself, so a
        # global set through it (tests/test_hamsci_sink.py sets two) reaches
        # Writer.from_env.
        self.assertIs(sys.modules["sigmond.hamsci_sink.writer"], writer)
        self.assertIs(Writer, writer.Writer)
        self.assertIs(Writer.from_env.__func__.__globals__, vars(writer))
        for name in ("from_env", "insert", "flush", "close", "__enter__", "__exit__"):
            self.assertTrue(callable(getattr(Writer, name)), name)
        w = Writer("psk", "spots", schema_version=2)   # no config: a no-op writer
        self.assertEqual((w.database, w.table, w.schema_version), ("psk", "spots", 2))
        self.assertEqual((w.health, w.is_noop, w.buffered), (HEALTH_NOOP, True, 0))
        self.assertEqual(writer.logger.name, "sigmond.hamsci_sink")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_hamsci_sink_compat.py -v`

Expected (RED), with Task 1 committed in `../hs-uploader`:
```
FAILED tests/test_hamsci_sink_compat.py::BundledCopyTests::test_header_sits_at_the_top_once_and_holds_only_comments
FAILED tests/test_hamsci_sink_compat.py::BundledCopyTests::test_imports_only_the_standard_library
FAILED tests/test_hamsci_sink_compat.py::BundledCopyTests::test_matches_hs_uploader_writer
FAILED tests/test_hamsci_sink_compat.py::SelectionTests::test_bundled_writer_used_when_hs_uploader_predates_the_sink
FAILED tests/test_hamsci_sink_compat.py::SelectionTests::test_bundled_writer_used_without_hs_uploader
FAILED tests/test_hamsci_sink_compat.py::SelectionTests::test_hs_uploader_writer_preferred_when_importable
FAILED tests/test_hamsci_sink_compat.py::SurfaceTests::test_every_name_importers_use_today
============================== 7 failed in 0.27s ===============================
```
The three `BundledCopyTests` fail with `FileNotFoundError` on `_bundled.py`; the child interpreters
fail on `ImportError: cannot import name '_bundled'` or `AttributeError: ... no attribute 'SINK_IMPL'`;
the surface test fails on `ImportError: cannot import name 'SINK_IMPL'`.

- [ ] **Step 4: Build the bundled copy from Task 1's file, and remove `writer.py`**

Run, from the sigmond checkout:
```bash
cd /home/mjh/hamsci/repos/sigmond
{ cat <<'EOF'
# ---- BEGIN sigmond bundled-copy header -------------------------------------
# Below this header sits a verbatim copy of hs-uploader's
# src/hs_uploader/sink/writer.py.  sigmond.hamsci_sink imports it only where
# a client's venv cannot import hs_uploader.sink: codar-sounder, hf-tec and
# superdarn-sounder carry no hs-uploader, and an hs-uploader older than v3.70
# has no sink package (tasks/plan-sink-control.md D14).
#
# Never edit below this header.  Change hs-uploader's file, then refresh this
# copy from the sigmond checkout:
#   { sed '/^# ---- END sigmond bundled-copy header/q' lib/sigmond/hamsci_sink/_bundled.py
#     cat ../hs-uploader/src/hs_uploader/sink/writer.py; } > lib/sigmond/hamsci_sink/_bundled.py.new
#   mv lib/sigmond/hamsci_sink/_bundled.py.new lib/sigmond/hamsci_sink/_bundled.py
# tests/test_hamsci_sink_compat.py fails while the two copies differ.
# ---- END sigmond bundled-copy header ---------------------------------------
EOF
  cat ../hs-uploader/src/hs_uploader/sink/writer.py
} > lib/sigmond/hamsci_sink/_bundled.py
git rm -q lib/sigmond/hamsci_sink/writer.py
head -15 lib/sigmond/hamsci_sink/_bundled.py
```
Expected: the 14 header lines, then the first line of Task 1's docstring.  Never edit the file by
hand; CLAUDE.md (Step 10) gives the command that refreshes it.

- [ ] **Step 5: Replace `__init__.py`**

Replace the whole of `sigmond/lib/sigmond/hamsci_sink/__init__.py` (33 lines, starting
`"""HamSCI sink writer primitives (CONTRACT §17).`) with:

```python
"""HamSCI sink writer: sigmond's compatibility import (CONTRACT §17).

Since v3.70 the sink writer lives in hs-uploader as ``hs_uploader.sink``
(tasks/plan-sink-control.md D14, §10.4 item 1).  Clients still import it from
here, and this package hands each one a copy of the same code:

- ``hs_uploader.sink.writer`` when the client's venv can import it.  Six
  clients carry hs-uploader as an editable sibling: psk-recorder,
  meteor-scatter, wspr-recorder, mag-recorder, hamsci-physics and hf-timestd.
- ``sigmond.hamsci_sink._bundled`` otherwise, a verbatim copy that sigmond
  ships.  codar-sounder, hf-tec and superdarn-sounder carry sigmond but no
  hs-uploader.  ``smd update`` also pulls sigmond before hs-uploader, so a
  recorder restarted between the two pulls meets an hs-uploader without
  ``sink``.

``SINK_IMPL`` names the copy in use: ``"hs_uploader"`` or ``"bundled"``.
tests/test_hamsci_sink_compat.py fails when the two copies differ.

``sigmond.hamsci_sink.writer`` names the chosen module itself, not a
re-export.  A global set through it, such as ``_DEFAULT_SQLITE_PATH`` in a
test, changes what ``Writer.from_env`` reads.

Either copy picks the sink the same way.  ``SIGMOND_SQLITE_PATH`` names it
when set.  Otherwise the writer uses ``/var/lib/sigmond/sink.db`` if that
directory is writable, and falls back to a no-op, so a client outside a
sigmond install stays safe.  ``BufferFull`` reports prolonged sink failure
instead of losing rows silently.
"""

import sys

try:
    from hs_uploader.sink import writer
except ImportError:
    # No hs-uploader in this venv, or one older than v3.70 with no sink package.
    from . import _bundled as writer
    SINK_IMPL = "bundled"
else:
    SINK_IMPL = "hs_uploader"

# Register the chosen module under its old name.  `from
# sigmond.hamsci_sink.writer import HEALTH_OK` then reads the module whose
# globals Writer.from_env uses; no writer.py remains on disk to shadow it.
sys.modules[__name__ + ".writer"] = writer

BufferFull = writer.BufferFull
SqliteConfig = writer.SqliteConfig
Writer = writer.Writer

__all__ = [
    "Writer",
    "BufferFull",
    "SqliteConfig",
    "SINK_IMPL",
]
```

- [ ] **Step 6: Run the tests and watch them pass**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_hamsci_sink_compat.py tests/test_hamsci_sink.py -v`
Expected (GREEN): `35 passed` — the 7 new tests, and all 28 old ones running the bundled copy through
the old import paths.  Without a `../hs-uploader` checkout: `33 passed, 2 skipped`.

Then confirm the choice inside a real client venv, whose editable hs-uploader points at
`/home/mjh/hamsci/repos/hs-uploader/src`:
```bash
/home/mjh/hamsci/repos/psk-recorder/.venv/bin/python -I -c "import sys; sys.path.insert(0, '/home/mjh/hamsci/repos/sigmond/lib'); import sigmond.hamsci_sink as s; print(s.SINK_IMPL, s.writer.__file__)"
```
Expected: `hs_uploader /home/mjh/hamsci/repos/hs-uploader/src/hs_uploader/sink/writer.py`.  (Before
Task 1 the same command printed `bundled`, the update-order window the fallback covers.)

- [ ] **Step 7: Trim `tests/test_hamsci_sink.py` to the old import paths**

Replace the whole of `sigmond/tests/test_hamsci_sink.py` with the file below.  Every kept line below
the imports comes over unchanged from the base file; only the docstring and the stdlib imports change, and the
stray `if __name__` block moves to the end.

```python
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
```

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_hamsci_sink.py tests/test_hamsci_sink_compat.py -q`
Expected: `19 passed` (12 kept, 7 new).

- [ ] **Step 8: Watch each new guard fail once (mutation)**

Stage first, so each mutation restores from the index:
```bash
cd /home/mjh/hamsci/repos/sigmond
git add lib/sigmond/hamsci_sink/ tests/test_hamsci_sink_compat.py tests/test_hamsci_sink.py
```

(a) Drift.  Run:
```bash
echo "# drift" >> lib/sigmond/hamsci_sink/_bundled.py
.venv/bin/pytest tests/test_hamsci_sink_compat.py -q
git checkout -- lib/sigmond/hamsci_sink/
```
Expected: `1 failed, 6 passed`.  `test_matches_hs_uploader_writer` prints "the bundled sink writer
differs from hs-uploader's", the refresh command, and a diff ending `+# drift`.

(b) The old submodule name.  Run:
```bash
sed -i 's/^sys.modules\[__name__ + ".writer"\] = writer$/# &/' lib/sigmond/hamsci_sink/__init__.py
.venv/bin/pytest tests/test_hamsci_sink_compat.py -q
git checkout -- lib/sigmond/hamsci_sink/
```
Expected: `2 failed, 5 passed` — `test_every_name_importers_use_today`
(`ModuleNotFoundError: No module named 'sigmond.hamsci_sink.writer'`) and
`test_hs_uploader_writer_preferred_when_importable` (`KeyError: 'sigmond.hamsci_sink.writer'`).
The trimmed `test_hamsci_sink.py` would stop at collection with the same `ModuleNotFoundError`.

(c) The preference.  Run:
```bash
sed -i 's/^    from hs_uploader.sink import writer$/    raise ImportError/' lib/sigmond/hamsci_sink/__init__.py
.venv/bin/pytest tests/test_hamsci_sink_compat.py -q
git checkout -- lib/sigmond/hamsci_sink/
```
Expected: `1 failed, 6 passed` — `test_hs_uploader_writer_preferred_when_importable`.

Then `git diff --stat` must print nothing: the working tree matches the index again.

- [ ] **Step 9: Prove the refresh command reproduces the copy**

Run:
```bash
cd /home/mjh/hamsci/repos/sigmond
{ sed '/^# ---- END sigmond bundled-copy header/q' lib/sigmond/hamsci_sink/_bundled.py
  cat ../hs-uploader/src/hs_uploader/sink/writer.py; } > lib/sigmond/hamsci_sink/_bundled.py.new
cmp lib/sigmond/hamsci_sink/_bundled.py.new lib/sigmond/hamsci_sink/_bundled.py && echo SAME
rm lib/sigmond/hamsci_sink/_bundled.py.new
```
Expected: `SAME`.  CLAUDE.md teaches this command, so it must work as written.

- [ ] **Step 10: `CLAUDE.md`, "Sink backend selection"**

Replace L179-189 (from `## Sink backend selection` through
``ClickHouse install, use `smd admin storage migrate-to-sqlite` to clean it up.``; the blank line and
`## Architecture layers` that follow stay) with:

~~~markdown
## Sink backend selection

Since v3.70 the sink writer lives in hs-uploader as `hs_uploader.sink`
(`tasks/plan-sink-control.md` D14).  Clients still import
`sigmond.hamsci_sink`, a compatibility import that hands each one a copy of
the same writer:

- `hs_uploader.sink.writer` when the client's venv can import it, as the
  venvs of the six clients that declare hs-uploader can (psk-recorder,
  meteor-scatter, wspr-recorder, mag-recorder, hamsci-physics, hf-timestd).
- `lib/sigmond/hamsci_sink/_bundled.py` otherwise.  codar-sounder, hf-tec and
  superdarn-sounder carry no hs-uploader, and an hs-uploader older than v3.70
  has no `sink` package.

`sigmond.hamsci_sink.SINK_IMPL` names the copy in use, `"hs_uploader"` or
`"bundled"`.  `sigmond.hamsci_sink.writer` names the chosen module itself, so
a global set through it reaches `Writer.from_env`.

**Never edit `_bundled.py` by hand.**  Change hs-uploader's
`src/hs_uploader/sink/writer.py`, commit it there, then refresh the copy below
its marked header from this checkout:

```bash
{ sed '/^# ---- END sigmond bundled-copy header/q' lib/sigmond/hamsci_sink/_bundled.py
  cat ../hs-uploader/src/hs_uploader/sink/writer.py; } > lib/sigmond/hamsci_sink/_bundled.py.new
mv lib/sigmond/hamsci_sink/_bundled.py.new lib/sigmond/hamsci_sink/_bundled.py
```

`tests/test_hamsci_sink_compat.py` fails while the two copies differ, and skips
that comparison when no `../hs-uploader` checkout sits beside this one.

Either copy picks the sink at construction time:

- `SIGMOND_SQLITE_PATH` set → `Writer` at that path (override).
- Unset                    → `Writer` at `/var/lib/sigmond/sink.db`
  if writable, else no-op (preserves standalone-safety).

Each row also carries `producer` and `local` (spec §6.1).  The writer adds
both columns, by `ALTER TABLE ADD COLUMN` alone, to an older `sink.db` it
opens.

SQLite is the sole local sink. On a host carrying a leftover legacy
ClickHouse install, use `smd admin storage migrate-to-sqlite` to clean it up.
~~~

- [ ] **Step 11: `etc/catalog.toml`, the hs-uploader entry**

Under `[client.hs-uploader]` (L144), replace L146-151 (from `description     = "HamSCI PSWS uploader`
through `# discovery (the cloned repo carries its own install.sh).`) with:

```toml
description     = "HamSCI uploader: the sink writer (hs_uploader.sink), the upload library and the hs-uploader daemon"
repo            = "https://github.com/HamSCI/hs-uploader"
# No install_script: six clients (psk-recorder, meteor-scatter, wspr-recorder,
# mag-recorder, hamsci-physics, hf-timestd) declare hs-uploader and point
# [tool.uv.sources] at ../hs-uploader as an editable install — only the source
# tree needs to exist.
# sigmond.hamsci_sink imports hs_uploader.sink where a venv carries it, and its
# bundled copy elsewhere.  `smd install hs-uploader` still works thanks to
# find_install_script's post-clone discovery (the cloned repo carries its own
# install.sh, which installs the daemon's venv and hs-uploader.service).
```

`kind = "library"` (L145) stays, and the block repeats `repo` unchanged.  A host whose `/etc/sigmond/catalog.toml` still carries the
old description keeps showing it until someone prunes it; nothing reads the text but `smd component
list`.

- [ ] **Step 12: The three docs pages that point at `writer.py`**

`docs/scientist/becoming-a-client.md`:
- L202 → `` [`sigmond.hamsci_sink.Writer`](../../lib/sigmond/hamsci_sink/__init__.py). ``
- L221-222 (now ``What the code above is really doing, from`` /
  ``[`writer.py`](../../lib/sigmond/hamsci_sink/writer.py)'s own docstring:``) →

  ```markdown
  What the code above is really doing, from the writer's own docstring
  ([`_bundled.py`](../../lib/sigmond/hamsci_sink/_bundled.py), sigmond's copy of
  hs-uploader's `sink/writer.py`):
  ```

`docs/scientist/data-and-timing.md` L227-230 (now `(source: ... on b4,` / `2026-08-23; the same DDL is
in` / the `writer.py` link / `` `_QUEUE_DDL` / `_QUEUE_INDEX_DDL`.) ``) →

```markdown
(source: `sqlite3 /var/lib/sigmond/sink.db ".schema pending_uploads"` on b4,
2026-08-23.)  Since v3.70 the writer's DDL lives in hs-uploader's
`src/hs_uploader/sink/writer.py`, and sigmond carries a copy in
[`lib/sigmond/hamsci_sink/_bundled.py`](../../lib/sigmond/hamsci_sink/_bundled.py).
The table there gains two columns, `producer` and `local`, and the writer adds
both to an older `sink.db` when it opens one.
```

In both scientist pages, set L5 to
`> **Verified against:** sigmond <sha> on <date> — sink writer links checked against lib/sigmond/hamsci_sink/`,
where `<sha>` comes from `git rev-parse --short HEAD` before you commit (docs-freshness accepts the
parent of the commit that edits a page) and `<date>` gives today's UTC date.  Both pages read fresh at
the base, and the bump keeps them fresh.

`docs/contributor/orchestration.md` L67-68 (now ``capture_prep.py`, the sink default in
`hamsci_sink/writer.py`, and`` / ``upload-wake.sock` in ka9q-python's wspr_recorder.``) →

```markdown
`capture_prep.py`, the sink default in hs-uploader's `sink/writer.py` (copied
into `hamsci_sink/_bundled.py`), and `upload-wake.sock` in ka9q-python's
wspr_recorder.
```

Leave this page's L5 alone.  docs-freshness already reports it stale at the base (verified at
`2cd11c4`, content last edited at `401bb12`); a bump here would certify that earlier, unchecked edit.

- [ ] **Step 13: Run the docs checks and the whole suite**

Run:
```bash
cd /home/mjh/hamsci/repos/sigmond
python3 scripts/docs-linkcheck.py docs README.md
.venv/bin/pytest tests/test_docs_links.py tests/test_docs_cli_table.py tests/test_docs_freshness.py tests/test_docs_conformance.py -q
python3 -c "import tomllib; print(tomllib.load(open('etc/catalog.toml','rb'))['client']['hs-uploader'])"
git grep -n "hamsci_sink/writer" -- docs CLAUDE.md lib bin scripts etc
python3 scripts/docs-freshness.py docs/scientist docs/contributor
.venv/bin/pytest -q
```
Expected: `docs-linkcheck: 0 broken link(s)`; `23 passed`; the dict shows the new description; the
`git grep` prints two lines and no more.  One names
`docs/superpowers/plans/2026-08-23-docs-phase2-scientist.md:152`, a dated plan that stays as written.
The other names `lib/sigmond/hamsci_sink/_bundled.py:26`, inside the copied docstring, where Task 1's
writer says it moved here from `sigmond/lib/sigmond/hamsci_sink/writer.py`; that line belongs to the
verbatim copy and stays.  (A plain `grep` also finds the ignored `lib/sigmond.egg-info/SOURCES.txt`.)
docs-freshness names only `docs/contributor/orchestration.md`, stale since the base, and after the
commit in Step 14 `python3 scripts/docs-freshness.py docs/scientist` reports `0 stale page(s)`; then
`3419 passed, 1 warning, 53 subtests passed` (the baseline 3428, less the 16 tests that moved to
hs-uploader, plus 7).
`tests/test_radiod_dropins.py::RadiodDropinsWriterTests::test_temp_file_removed_after_install`
compares the whole system temp directory before and after; another process writing `/tmp` at that
moment fails it.  If it fails, rerun that file alone before suspecting this task.

- [ ] **Step 14: Commit**

```bash
cd /home/mjh/hamsci/repos/sigmond
git add lib/sigmond/hamsci_sink/ tests/test_hamsci_sink_compat.py tests/test_hamsci_sink.py \
        CLAUDE.md etc/catalog.toml \
        docs/scientist/becoming-a-client.md docs/scientist/data-and-timing.md \
        docs/contributor/orchestration.md
git status --short
git commit -m "hamsci_sink: a compatibility import over hs_uploader.sink, with a bundled copy

The sink writer now lives in hs-uploader as hs_uploader.sink (v3.70,
plan-sink-control D14).  sigmond.hamsci_sink imports it where a client's
venv can, and otherwise falls back to lib/sigmond/hamsci_sink/_bundled.py,
a verbatim copy below a marked header.  codar-sounder, hf-tec and
superdarn-sounder carry no hs-uploader, and smd update pulls sigmond
before hs-uploader; both cases keep writing through the copy.

SINK_IMPL names the copy in use.  sigmond.hamsci_sink.writer names the
chosen module itself, so every name importers use today still resolves,
and a global set through it still reaches Writer.from_env.
tests/test_hamsci_sink_compat.py fails when the copy drifts from
hs-uploader's file, and skips that comparison with no sibling checkout.
tests/test_hamsci_sink.py keeps the five classes that go through the old
import paths; the other four moved to hs-uploader with the writer.

CLAUDE.md, the catalog's hs-uploader entry and three docs pages now say
where the writer lives.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```
Expected from `git status --short` before the commit: `M` for `CLAUDE.md`, the three docs pages,
`etc/catalog.toml`, `__init__.py` and `tests/test_hamsci_sink.py`;
`R  lib/sigmond/hamsci_sink/writer.py -> lib/sigmond/hamsci_sink/_bundled.py`;
`A  tests/test_hamsci_sink_compat.py`; nothing else.


### Task 7: `smd admin uploader manifest` runs `hs-uploader migrate` before the daemon starts (D10)

The daemon never migrates its own store while it starts (D10).  So something must run `hs-uploader migrate`
once the new code sits in place and before hs-uploader restarts.  Every path that starts or restarts the
daemon over a new manifest passes through `cmd_uploader_manifest`.  Bring-up Stage 4, `smd apply` and the
site sink commands call it with `--write --enable` (`lib/sigmond/bringup.py:428`, `bin/smd:10265-10269`,
`lib/sigmond/commands/config.py:1000-1005`).  `smd align` calls it with `--write` alone, then restarts
hs-uploader itself, and restarts nothing when that step exits non-zero (`bin/smd:6003-6016`, `6298-6302`).
So this task runs migrate right after the write, under both flags, and before any `systemctl` call.  A
failed migrate leaves the daemon as it stands and exits 1.

Migrate runs as `hsupload`, the daemon's own account, through `runuser`, so no file SQLite creates beside
`watermarks.db` ever belongs to root.  Two cases count as skipped, with a line that says so: a host without
the daemon's venv, and an installed hs-uploader that predates `migrate`.  The second exits 2 with
`invalid choice: 'migrate'` on stderr (Task 2).  That happens when v3.70's sigmond meets an older
hs-uploader, for example part-way through an update.  The 58fb909 order stays: `--enable` installs the daemon before it renders, and a failed
install still writes the manifest, returns 1 and never migrates.

v3.70's migrate only records version 1 and changes no row and no table (Task 2), so a station sends
exactly what it sent before.  This task builds the step v3.71's key copy will need.

**Files:**
- Modify: `sigmond/lib/sigmond/commands/uploader.py` — the module docstring (lines 11-15); below
  `_VENV = Path("/opt/hs-uploader/venv")` (line 32); a new `_run_migrate` above
  `def cmd_uploader_manifest(args) -> int:` (line 94); the tail of `cmd_uploader_manifest` (lines 182-191)
- Modify: `sigmond/bin/smd` — the `--write` and `--enable` help of `admin uploader manifest` (lines
  23975-23978)
- Test: `sigmond/tests/test_uploader_cmd_policy.py` — the imports (lines 3-5); `_call` (lines 63-97);
  `test_root_enable_installs_before_generate` (lines 99-101); `test_write_without_enable_never_installs`
  (lines 119-121); new tests appended after the last line (152); a new class `RunMigrateTests`

Line numbers name each file at 64cf83f.  Tasks 1-6 leave these three files alone, so the numbers hold at
this task's base.

**Interfaces:**
- Consumes: Task 2's CLI, `hs-uploader migrate` with no arguments.  It prints `watermarks.db: version N`
  and lines such as `  applied 1: <summary>` on stdout; exits 0 when it finished or found nothing to do
  (a missing store and a newer store included), 1 on failure, 2 on a usage error.  An hs-uploader that
  predates it exits 2 with `argument cmd: invalid choice: 'migrate'` on stderr.  Its lock wait,
  `BUSY_TIMEOUT_S`, lasts 30 s.
- Produces: `sigmond.commands.uploader._run_migrate() -> int` (0 ok or skipped, 1 failure);
  `_MIGRATE_TIMEOUT_S = 120`.  `cmd_uploader_manifest` calls `_run_migrate()` after the write, on every
  root `--write` or `--enable`, and before `systemctl enable --now` or `restart`.  On 1 it prints why,
  runs no `systemctl`, and returns 1.  Each stdout line of migrate appears prefixed `uploader: `, so
  Task 9's rig finds `uploader: watermarks.db: version 1`.

**Decisions this task makes** (the contract left them open):
1. **`--write` alone migrates too.**  `smd align` restarts hs-uploader after its own `--write` step, so a
   migrate tied to `--enable` would never run before align's restart.  The contract's "before
   enable/restart" holds either way.
2. **No daemon venv means skipped, not failed.**  `--write` runs on hosts where nothing installed the
   daemon, and returned 0 there before this task.  No daemon there reads the store either.
3. **Only `invalid choice: 'migrate'` counts as an old hs-uploader.**  Any other exit 2 (a usage error
   from migrate itself) fails.
4. **The subprocess gets 120 s**, past migrate's own 30 s lock wait, with room for the interpreter to start
   on a busy station.  A hang or a `runuser` that cannot start fails.
5. **`smd update` gets no migrate here.**  It never re-renders and never restarts hs-uploader
   (`bin/smd:3992`), so the daemon keeps the code it started with.  Align, apply, bring-up and the site
   sink commands all migrate before they restart it.  If v3.71 makes `smd update` restart the daemon,
   it must migrate there first.
6. **Migrate may run beside the old daemon.**  `--enable` migrates before it restarts an active daemon,
   and align migrates well before its restart step.  v3.70's migration holds the write lock for one
   `PRAGMA user_version`, so the old daemon waits a moment at most.  v3.71's key copy must not run under
   a live daemon (research map 5, finding 1); it will need stop, migrate, start.

- [ ] **Step 1: Write the failing tests**

In `tests/test_uploader_cmd_policy.py`, find (lines 3-5):

```python
import contextlib
import io
import tempfile
```

and replace it with:

```python
import contextlib
import io
import subprocess
import tempfile
```

Replace `_call` (lines 63-97), from `    def _call(self, *, enable=True, write=True, euid=0, install=True,`
through its `            out=out.getvalue(), err=err.getvalue())`, with:

```python
    def _call(self, *, enable=True, write=True, euid=0, install=True,
              render=None, calls=None, migrate=0, active=False):
        """Run the command with the daemon install, the render, the
        send-record migration and systemd faked.  ``install`` is what the
        install returns, or an exception it raises.  ``render`` replaces the
        rendered text.  ``migrate`` is what ``_run_migrate`` returns, and
        ``active`` whether the daemon already runs.  ``calls`` records
        "install", "generate", "migrate" and each systemctl verb, such as
        "systemctl enable", in the order they ran."""
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        path = Path(d.name) / "pipelines.toml"
        calls = [] if calls is None else calls
        seen = {}

        def fake_install():
            calls.append("install")
            if isinstance(install, BaseException):
                raise install
            return install

        def fake_generate():
            calls.append("generate")
            return render() if render else self.TEXT

        def fake_migrate():
            calls.append("migrate")
            seen["manifest"] = path.read_text() if path.exists() else None
            return migrate

        def fake_run(cmd):
            calls.append(" ".join(cmd[:2]))
            return 0

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(um, "generate", side_effect=fake_generate), \
             mock.patch.object(um, "suppressed_pipelines", return_value=[]), \
             mock.patch.object(um, "MANIFEST_PATH", path), \
             mock.patch.object(up_cmd.os, "geteuid", return_value=euid), \
             mock.patch.object(up_cmd, "_ensure_daemon_installed",
                               side_effect=fake_install), \
             mock.patch.object(up_cmd, "_run_migrate", side_effect=fake_migrate), \
             mock.patch.object(up_cmd, "_service_active", return_value=active), \
             mock.patch.object(up_cmd, "_run", side_effect=fake_run) as run, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = up_cmd.cmd_uploader_manifest(
                types.SimpleNamespace(write=write, enable=enable))
        return types.SimpleNamespace(
            rc=rc, calls=calls, path=path, run=run,
            manifest_at_migrate=seen.get("manifest", "never migrated"),
            out=out.getvalue(), err=err.getvalue())
```

The helper must patch `_run_migrate` in every test.  On a host that carries `/opt/hs-uploader/venv`, the
real one would run `runuser` from the test, with `geteuid` faked to 0.

Find (lines 99-101):

```python
    def test_root_enable_installs_before_generate(self):
        r = self._call()
        self.assertEqual(r.calls, ["install", "generate"])
```

and replace it with:

```python
    def test_root_enable_installs_before_generate(self):
        r = self._call()
        self.assertEqual(r.calls, ["install", "generate", "migrate",
                                   "systemctl enable"])
```

Find (lines 119-121):

```python
    def test_write_without_enable_never_installs(self):
        r = self._call(enable=False)
        self.assertEqual(r.calls, ["generate"])
```

and replace it with:

```python
    def test_write_without_enable_never_installs(self):
        r = self._call(enable=False)
        self.assertEqual(r.calls, ["generate", "migrate"])
```

Leave `test_failed_install_still_writes_and_returns_1` and `test_install_error_still_writes_and_returns_1`
as they stand.  Their `["install", "generate"]` now also pins that a failed install never migrates.

After the last line of the file (line 152, `        r.run.assert_not_called()` inside
`test_install_error_still_writes_and_returns_1`), append:

```python

    # -- D10: `hs-uploader migrate` runs after the write, before any start --

    def test_migrate_runs_on_the_written_manifest_before_systemctl(self):
        r = self._call(active=True)
        self.assertEqual(r.calls, ["install", "generate", "migrate",
                                   "systemctl enable", "systemctl restart"])
        self.assertEqual(r.manifest_at_migrate, self.TEXT)
        self.assertEqual(r.rc, 0)

    def test_a_failed_migrate_starts_and_restarts_nothing(self):
        r = self._call(active=True, migrate=1)
        self.assertEqual(r.rc, 1)
        self.assertEqual(r.calls, ["install", "generate", "migrate"])
        r.run.assert_not_called()
        self.assertEqual(r.path.read_text(), self.TEXT)
        self.assertIn("neither started nor restarted", r.err)

    def test_write_alone_migrates_and_fails_when_migrate_fails(self):
        # smd align runs `--write` alone and restarts hs-uploader itself,
        # only when this step exits 0 (bin/smd, _align_apply).
        r = self._call(enable=False, migrate=1)
        self.assertEqual(r.calls, ["generate", "migrate"])
        self.assertEqual(r.rc, 1)
        r.run.assert_not_called()


class RunMigrateTests(unittest.TestCase):
    """`_run_migrate` runs the daemon's own `hs-uploader migrate` as hsupload
    (tasks/plan-sink-control.md D10).  subprocess.run is faked; nothing runs."""

    INVALID = ("usage: hs-uploader [-h] [--state STATE]\n"
               "                   {status,peek,reset-cursor,kick,serve} ...\n"
               "hs-uploader: error: argument cmd: invalid choice: 'migrate' "
               "(choose from 'status', 'peek', 'reset-cursor', 'kick', 'serve')\n")

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.venv = Path(d.name) / "venv"
        self.exe = self.venv / "bin" / "hs-uploader"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_text("#!/bin/sh\n")

    def _migrate(self, result=(0, "", ""), *, exc=None):
        """Run _run_migrate against the fake venv.  ``result`` is the faked
        (returncode, stdout, stderr); ``exc`` an exception to raise instead."""
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            if exc is not None:
                raise exc
            rc, so, se = result
            return subprocess.CompletedProcess(cmd, rc, so, se)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(up_cmd, "_VENV", self.venv), \
             mock.patch.object(up_cmd.subprocess, "run",
                               side_effect=fake_run) as run, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = up_cmd._run_migrate()
        return types.SimpleNamespace(rc=rc, run=run, cmd=seen.get("cmd"),
                                     kw=seen.get("kw"), out=out.getvalue(),
                                     err=err.getvalue())

    def test_runs_the_daemons_hs_uploader_as_hsupload(self):
        r = self._migrate((0, "watermarks.db: version 1\n"
                              "  applied 1: record the schema version; "
                              "changes no row and no table\n", ""))
        self.assertEqual(r.rc, 0)
        # Intent, not the bare name: runuser lives in /usr/sbin, off an
        # operator's PATH (AC0G-ND 2026-09-03).
        self.assertTrue(r.cmd[0].endswith("runuser"), r.cmd)
        self.assertEqual(r.cmd[1:], ["-u", "hsupload", "--", str(self.exe),
                                     "migrate"])
        self.assertGreater(r.kw["timeout"], 30)   # past migrate's busy timeout
        self.assertEqual(r.kw["cwd"], str(self.venv.parent))
        self.assertIn("uploader: watermarks.db: version 1", r.out)
        self.assertIn("uploader:   applied 1: record the schema version", r.out)

    def test_an_hs_uploader_without_migrate_counts_as_skipped(self):
        r = self._migrate((2, "", self.INVALID))
        self.assertEqual(r.rc, 0)
        self.assertIn("predates `migrate`", r.out)
        self.assertEqual(r.err, "")

    def test_a_usage_error_from_migrate_itself_fails(self):
        r = self._migrate((2, "", "usage: hs-uploader migrate [-h] [--db PATH] "
                                  "[--check]\nhs-uploader migrate: error: "
                                  "unrecognized arguments: --bogus\n"))
        self.assertEqual(r.rc, 1)
        self.assertIn("unrecognized arguments", r.err)

    def test_a_failed_migrate_returns_1_and_says_why(self):
        r = self._migrate((1, "", "hs-uploader migrate: /var/lib/hs-uploader/"
                                  "watermarks.db: OperationalError: database "
                                  "is locked\n"))
        self.assertEqual(r.rc, 1)
        self.assertIn("exit 1", r.err)
        self.assertIn("database is locked", r.err)

    def test_a_hung_migrate_returns_1(self):
        r = self._migrate(exc=subprocess.TimeoutExpired(["runuser"], 120))
        self.assertEqual(r.rc, 1)
        self.assertIn("did not finish", r.err)

    def test_a_runuser_that_cannot_start_returns_1(self):
        r = self._migrate(exc=FileNotFoundError(2, "No such file or directory",
                                                "/usr/sbin/runuser"))
        self.assertEqual(r.rc, 1)
        self.assertIn("could not run", r.err)

    def test_no_daemon_venv_skips_without_running_anything(self):
        self.exe.unlink()
        r = self._migrate()
        self.assertEqual(r.rc, 0)
        r.run.assert_not_called()
        self.assertIn("migrate skipped", r.out)
```

`INVALID` carries the stderr that hs-uploader at 3e97223 prints for `hs-uploader migrate`, copied from a
real run under Python 3.11.  Python 3.13, the stations' interpreter, drops the quotes inside the
parentheses and keeps `invalid choice: 'migrate'` as it stands.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_uploader_cmd_policy.py -v`
Expected: 17 failed, 3 passed.  The three `SuppressedPipelinesLineTests` pass.  All ten
`EnableInstallsDaemonFirstTests` fail in `_call` with `AttributeError: <module
'sigmond.commands.uploader' from '…'> does not have the attribute '_run_migrate'`.  All seven
`RunMigrateTests` fail with `AttributeError: module 'sigmond.commands.uploader' has no attribute
'_run_migrate'`.

- [ ] **Step 3: Implement in `lib/sigmond/commands/uploader.py`**

(a) In the module docstring, find (lines 11-15):

```python
* ``--write`` — write the manifest (root); back up any existing file to ``.bak``.
* ``--enable`` — install the daemon first when it is missing (the render
  probes its venv), write, then ensure ``hs-uploader.service`` is enabled +
  running (restart it when the manifest actually changed).
"""
```

and replace it with:

```python
* ``--write`` — write the manifest (root); back up any existing file to
  ``.bak``; then run ``hs-uploader migrate`` as ``hsupload``.
* ``--enable`` — install the daemon first when it is missing (the render
  probes its venv), write, migrate, then ensure ``hs-uploader.service`` is
  enabled + running (restart it when the manifest actually changed).

``hs-uploader migrate`` brings ``watermarks.db`` to the schema the
installed daemon expects.  It runs after the write and before anything
starts or restarts the daemon, because the daemon never migrates while it
starts (tasks/plan-sink-control.md D10).  A failed migrate leaves the
daemon as it stands and exits 1.
"""
```

(b) Find (line 32):

```python
_VENV = Path("/opt/hs-uploader/venv")
```

and add directly below it:

```python
# Longer than hs-uploader migrate's own 30 s wait for another writer's lock,
# with room for the interpreter to start on a busy station.
_MIGRATE_TIMEOUT_S = 120
```

(c) Find (line 94):

```python
def cmd_uploader_manifest(args) -> int:
```

and insert above it, after the two blank lines that follow `_ensure_daemon_installed`:

```python
def _run_migrate() -> int:
    """Run the daemon's own ``hs-uploader migrate`` as ``hsupload``.

    The daemon never migrates while it starts (tasks/plan-sink-control.md
    D10), so this runs after the manifest write and before any start or
    restart.  It runs as ``hsupload``, the daemon's account, in the unit's
    working directory.  So no file SQLite creates beside ``watermarks.db``,
    such as its rollback journal, ever belongs to root (bee1, 2026-05-12:
    "attempt to write a readonly database").

    Returns 0 when migrate finished or found nothing to do.  Also returns 0,
    with a line saying so, when no daemon venv exists or the installed
    hs-uploader predates ``migrate`` (argparse exits 2 with ``invalid
    choice: 'migrate'``).  Returns 1 on any other failure, after saying
    why."""
    exe = _VENV / "bin" / "hs-uploader"
    if not exe.exists():
        print(f"uploader: no {exe}; send-record migrate skipped")
        return 0
    runuser = shutil.which("runuser") or "/usr/sbin/runuser"
    cmd = [runuser, "-u", "hsupload", "--", str(exe), "migrate"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, cwd=str(_VENV.parent),
                           timeout=_MIGRATE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        _err(f"hs-uploader migrate did not finish within "
             f"{_MIGRATE_TIMEOUT_S} s")
        return 1
    except OSError as exc:
        _err(f"hs-uploader migrate could not run: {exc}")
        return 1
    stderr = (r.stderr or "").strip()
    if r.returncode == 2 and "invalid choice: 'migrate'" in stderr:
        print("uploader: this hs-uploader predates `migrate`; send-record "
              "migrate skipped")
        return 0
    for line in (r.stdout or "").splitlines():
        if line.strip():
            print(f"uploader: {line}")
    if r.returncode != 0:
        _err(f"hs-uploader migrate failed (exit {r.returncode}): "
             f"{stderr or 'no message'}")
        return 1
    return 0


```

The unit runs the daemon in `WorkingDirectory=/opt/hs-uploader`, which `_VENV.parent` names.  Without a
`cwd`, a `sudo smd` started from a home directory would hand `hsupload` a working directory it cannot read.

(d) In `cmd_uploader_manifest`, find (lines 182-191):

```python
    if not enable:
        return 0

    # A failed install still leaves the manifest written above; the call
    # then reports failure here.
    if daemon_ready is None:
        daemon_ready = _ensure_daemon_installed()
    if not daemon_ready:
        return 1
    was_active = _service_active()
```

and replace it with:

```python
    # A failed install still leaves the manifest written above; the call
    # then reports failure here, and never migrates.
    if enable:
        if daemon_ready is None:
            daemon_ready = _ensure_daemon_installed()
        if not daemon_ready:
            return 1

    # D10: migrate the send-record store after the write and before anything
    # starts or restarts the daemon.  `--write` alone migrates too: smd align
    # runs it, then restarts hs-uploader itself only when this exits 0.
    if _run_migrate() != 0:
        _err(f"hs-uploader migrate failed; {SERVICE} left as it stands, "
             "neither started nor restarted.  Fix the cause above, then run "
             "this command again.")
        return 1

    if not enable:
        return 0

    was_active = _service_active()
```

The install check moves above `if not enable:`, inside `if enable:`, so it runs only where it ran before.
The non-root refusal at line 161 still returns before any of this, so only root ever migrates.

- [ ] **Step 4: Say so in `smd admin uploader manifest --help`**

In `bin/smd`, find (lines 23975-23978):

```python
    p_u_manifest.add_argument('--write', action='store_true',
        help='write /etc/hs-uploader/pipelines.toml (root; backs up to .bak)')
    p_u_manifest.add_argument('--enable', action='store_true',
        help='write, then enable + (re)start hs-uploader.service')
```

and replace it with:

```python
    p_u_manifest.add_argument('--write', action='store_true',
        help='write /etc/hs-uploader/pipelines.toml (root; backs up to .bak), '
             'then run hs-uploader migrate')
    p_u_manifest.add_argument('--enable', action='store_true',
        help='write and migrate, then enable + (re)start hs-uploader.service')
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_uploader_cmd_policy.py -v`
Expected: 20 passed.

Run: `SIGMOND_NO_VENV_REEXEC=1 .venv/bin/python bin/smd admin uploader manifest --help`
Expected: the `--write` line ends `then run hs-uploader migrate`; the `--enable` line reads `write and
migrate, then enable + (re)start hs-uploader.service`.

Run: `.venv/bin/pytest -q`
Expected: no failures, and 10 more passed than at this task's base: `3429 passed, 1 warning, 53 subtests
passed` after Task 6's 3419, with Tasks 1-5 committed in the sibling `../hs-uploader`.

- [ ] **Step 6: Watch four guards fail, then restore**

Each mutation must turn exactly the named tests red.  Restore the file after each one.

```bash
cd /home/mjh/hamsci/repos/sigmond
F=lib/sigmond/commands/uploader.py
T=tests/test_uploader_cmd_policy.py
cp $F $F.keep
sed -i 's/    if _run_migrate() != 0:/    if enable and _run_migrate() != 0:/' $F
.venv/bin/pytest $T -q   # 2 failed: test_write_alone_migrates_and_fails_when_migrate_fails, test_write_without_enable_never_installs
cp $F.keep $F
sed -i 's/    if _run_migrate() != 0:/    if _run_migrate() != 0 and False:/' $F
.venv/bin/pytest $T -q   # 2 failed: test_a_failed_migrate_starts_and_restarts_nothing, test_write_alone_migrates_and_fails_when_migrate_fails
cp $F.keep $F
sed -i "s/    if r.returncode == 2 and \"invalid choice: 'migrate'\" in stderr:/    if r.returncode == 2:/" $F
.venv/bin/pytest $T -q   # 1 failed: test_a_usage_error_from_migrate_itself_fails
cp $F.keep $F
sed -i 's/    cmd = \[runuser, "-u", "hsupload", "--", str(exe), "migrate"\]/    cmd = [str(exe), "migrate"]/' $F
.venv/bin/pytest $T -q   # 1 failed: test_runs_the_daemons_hs_uploader_as_hsupload
mv $F.keep $F
.venv/bin/pytest $T -q   # 20 passed
```

The first mutation ties migrate to `--enable`, so `smd align` would restart a daemon over an unmigrated
store.  The second ignores a failed migrate.  The third takes any usage error for an old hs-uploader.  The
fourth runs migrate as root.  The dry run also moved the migrate block below `systemctl enable`; five tests
failed, `test_migrate_runs_on_the_written_manifest_before_systemctl` among them.

- [ ] **Step 7: Commit**

```bash
git add lib/sigmond/commands/uploader.py bin/smd tests/test_uploader_cmd_policy.py
git commit -m "uploader: run hs-uploader migrate before the daemon starts (D10)

smd admin uploader manifest --write and --enable now run the daemon's own
hs-uploader migrate as hsupload, after the write and before any systemctl
start or restart.  A failed migrate leaves the daemon as it stands and
exits 1, so smd align restarts nothing.  A host without the daemon's venv,
and an hs-uploader that predates migrate, count as skipped.  v3.70's
migrate only records version 1 and changes no row.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```


### Task 8: `smd admin manifest restore --apply` re-renders `pipelines.toml` with the restored sigmond (D11)

A restore moves git checkouts and nothing else.  It leaves `/etc/hs-uploader/pipelines.toml` as the newer
sigmond rendered it, so a rolled-back hs-uploader reads pipelines a newer renderer wrote at its next start.
Once v3.71 renders split pipelines, a v3.69 daemon would build both on one shared key, and each would skip
the other's rows: the loss case of spec §1, running for as long as the rollback lasts.  D11 closes that
door one release early.  After a verified `--apply`, the restored checkout's own smd runs
`admin uploader manifest --write --enable` as a child process.  The restored code renders and writes the
manifest, and restarts hs-uploader when the manifest changed.  The run must use a child: the restoring
process already holds the newer sigmond's modules in memory.

In v3.70 nothing changes `pipelines.toml` content, so a rollback from v3.70 to v3.69 re-renders the same
manifest and restarts nothing.  The running daemon then keeps the v3.70 code it started with until its next
restart, as after any restore today.  The step earns its keep at the v3.71 rollback, and Task 9's rig
checks that the re-rendered manifest matches the restored release.

**Files:**
- Modify: `sigmond/bin/smd` — a new `_RESTORE_RERENDER_TIMEOUT_S` and `_restore_rerender_uploader` above
  `def cmd_manifest_restore(args) -> int:` (line 4307); the `cmd_manifest_restore` docstring (lines
  4371-4374 and 4392-4398); its success branch (lines 4602-4607); its failure tail (lines 4614-4616); the
  `--apply` help of `admin manifest restore` (lines 24033-24035)
- Test: `sigmond/tests/test_manifest_restore.py` — `_FakeGit` (docstring lines 226-228, `__init__` lines
  230-234, `__call__` lines 242-245); `ManifestRestoreCliDryRunTests` (after line 350);
  `ManifestRestoreApplyTests` (after line 635)

Line numbers name each file at Task 7's head.  Task 7 grows the `admin uploader manifest` help in
`bin/smd` (lines 23975-23978) by one line, so the `--apply` help sits one line lower than at 64cf83f.
Every other number here matches 64cf83f.

**Interfaces:**
- Consumes: `smd admin uploader manifest --write --enable` of whichever sigmond the restore put back (v3.69
  on the first rollback from v3.70).  Exit 0 on success, non-zero on failure.  From v3.70 on it runs Task
  7's migrate before it starts the daemon.  `_SCRIPT` (bin/smd line 106), `_ok`, `_err`, `_info`.
- Produces: `_restore_rerender_uploader(base) -> int` (0 ok, 1 failed) and
  `_RESTORE_RERENDER_TIMEOUT_S = 600` in `bin/smd`.  Output for Task 9's rig, each line through `_info`,
  `_ok` or `_err`:
  - `| <line>` for every non-empty line the child printed, stdout then stderr;
  - `✓  pipelines.toml re-rendered by the restored sigmond (<path to smd>)` on success;
  - `✗  pipelines.toml re-render by the restored sigmond FAILED (exit N). …` on failure, with exit `-1`
    for a hang or a child that could not start;
  - `pipelines.toml not re-rendered: a restore re-renders only once it verifies` after a failed
    verification.

  `_ok` and `_err` print their mark inside ANSI colour codes, and none of these lines starts with
  `admin manifest restore:`.  A rig greps the words after the mark, such as
  `pipelines.toml re-rendered by the restored sigmond`.

  The success line `admin manifest restore: restored to manifest — N component(s) moved, …` keeps its
  words; test-update-v3.sh PHASE G greps it.  It now prints before the re-render, so a failed re-render
  shows that line and still exits 1.

**Decisions this task makes** (the contract left them open):
1. **The restored smd sits at `<base>/sigmond/bin/smd`**, the checkout restore just moved.  The child runs
   under `sys.executable`, the sigmond venv's interpreter, as `smd align` runs its children
   (`bin/smd:6003`).  Without a sigmond checkout under `base`, the running smd (`_SCRIPT`) stands in.  On
   a station both name the same file: install.sh links `/usr/local/bin/smd` to the checkout's
   `bin/smd`, and `_SCRIPT` resolves the link.
2. **The re-render runs after every verified `--apply`, even when nothing moved.**  A re-run of restore
   then repairs a re-render that failed the first time.
3. **No re-render after a failed verification.**  The host holds no single release to render with.  The
   restore says so and still exits 1.
4. **A failed, hung or unstartable child exits 1** with the fix named.  The checkouts stay where restore
   put them; nothing rolls back the rollback.
5. **The child gets 600 s.**  `--enable` may run hs-uploader's install.sh when its venv is missing, Task 7's
   migrate may wait 120 s, and the daemon restart waits on `TimeoutStartSec=30`.  PHASE G gives the whole
   restore 1800 s.
6. **The child inherits the environment.**  When `_need_root` re-executed this smd under sudo, it set
   `SIGMOND_ALLOW_SUDO=1`.  The child inherits it and passes main()'s sudo guard as the restore did.
7. **A host without hs-uploader gets no special case.**  There the child's `--enable` installs the daemon
   when its checkout carries install.sh, as bring-up does, and otherwise fails, so the restore exits 1
   after moving the checkouts.  Every appliance station runs hs-uploader, because it carries the
   heartbeat, so neither branch arises on the fleet.

- [ ] **Step 1: Write the failing tests**

In `tests/test_manifest_restore.py`, in the `_FakeGit` docstring, find (lines 226-228):

```python
    component name -> list of paths `git diff --name-only <from> <to>`
    should report changed (default: none, so install.sh is skipped).
    """
```

and replace it with:

```python
    component name -> list of paths `git diff --name-only <from> <to>`
    should report changed (default: none, so install.sh is skipped).
    ``rerender`` answers the restored smd's `admin uploader manifest
    --write --enable`: a (returncode, stdout, stderr) tuple, or an
    exception to raise (default: success).  ``rerender_kwargs`` keeps the
    keyword arguments that call received.
    """
```

Find (lines 230-234):

```python
    def __init__(self, dirty=None, unresolvable=None, changed_files=None):
        self.calls = []
        self.dirty = dirty or {}
        self.unresolvable = unresolvable or set()
        self.changed_files = changed_files or {}
```

and replace it with:

```python
    def __init__(self, dirty=None, unresolvable=None, changed_files=None,
                 rerender=(0, 'uploader: /etc/hs-uploader/pipelines.toml '
                              'already current (3 pipeline(s))\n', '')):
        self.calls = []
        self.dirty = dirty or {}
        self.unresolvable = unresolvable or set()
        self.changed_files = changed_files or {}
        self.rerender = rerender
        self.rerender_kwargs = None
```

Find (lines 242-245):

```python
    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        out = types.SimpleNamespace(returncode=0, stdout='', stderr='')
        name = self._component_of(cmd)
```

and replace it with:

```python
    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        out = types.SimpleNamespace(returncode=0, stdout='', stderr='')
        if 'uploader' in cmd and 'manifest' in cmd:
            self.rerender_kwargs = kwargs
            if isinstance(self.rerender, BaseException):
                raise self.rerender
            out.returncode, out.stdout, out.stderr = self.rerender
            return out
        name = self._component_of(cmd)
```

`_RealStatusGit` inherits both methods, so the untracked-file tests need no change.

In `ManifestRestoreCliDryRunTests`, find (lines 346-350):

```python
    def test_matching_components_are_never_fetched(self):
        manifest = self._manifest()
        live = {'hf-timestd': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        rc, out, fake = self._run(manifest, live=live)
        self.assertFalse(any('fetch' in c for c in fake.calls))
```

and add directly below it, with one blank line between:

```python
    def test_dry_run_never_rerenders_the_uploader_manifest(self):
        # D11's re-render belongs to --apply alone, even when the plan
        # would move a checkout.
        manifest = self._manifest()
        live = {'hf-timestd': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        rc, out, fake = self._run(manifest, live=live)
        self.assertEqual(rc, 0)
        self.assertFalse(any('uploader' in c for c in fake.calls))
```

In `ManifestRestoreApplyTests`, find the end of its last test (lines 633-635):

```python
        self.assertIn('wspr-recorder', out)
        self.assertIn('not resolvable', out)
        self.assertNotIn('re-plan verifies all-keep', out)
```

and add directly below it, before the two blank lines above `if __name__ == '__main__':`:

```python

    # -- D11: re-render pipelines.toml with the restored sigmond --------

    LIVE = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
    AFTER = {'wspr-recorder': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}

    @staticmethod
    def _rerenders(fake):
        return [c for c in fake.calls if 'uploader' in c and 'manifest' in c]

    def test_apply_rerenders_with_the_restored_smd_after_the_checkouts(self):
        restored = self.base / 'sigmond' / 'bin' / 'smd'
        restored.parent.mkdir(parents=True)
        restored.write_text('#!/usr/bin/env python3\n')
        fake = _FakeGit(rerender=(0, 'uploader: manifest changed — restarting '
                                     'hs-uploader.service\n', ''))
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 0, out)
        rerenders = self._rerenders(fake)
        self.assertEqual(len(rerenders), 1)
        self.assertEqual(rerenders[0][1:], [str(restored), 'admin', 'uploader',
                                            'manifest', '--write', '--enable'])
        checkout = next(i for i, c in enumerate(fake.calls)
                        if 'checkout' in c and '--detach' in c)
        self.assertGreater(fake.calls.index(rerenders[0]), checkout)
        self.assertIn('timeout', fake.rerender_kwargs)
        self.assertIn('restored to manifest — 1 component(s) moved', out)
        self.assertIn('| uploader: manifest changed — restarting '
                      'hs-uploader.service', out)
        self.assertIn('pipelines.toml re-rendered by the restored sigmond', out)

    def test_without_a_sigmond_checkout_the_running_smd_rerenders(self):
        fake = _FakeGit()
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._rerenders(fake)[0][1], str(smd._SCRIPT))

    def test_apply_rerenders_even_when_nothing_moved(self):
        # A re-run after a failed re-render repairs the manifest.
        fake = _FakeGit()
        rc, out, fake = self._run(self._manifest(), self.AFTER, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 0, out)
        self.assertFalse(any('checkout' in c for c in fake.calls))
        self.assertEqual(len(self._rerenders(fake)), 1)

    def test_a_failed_rerender_exits_1_and_names_the_fix(self):
        fake = _FakeGit(rerender=(1, '', 'smd: hs-uploader migrate failed\n'))
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 1)
        self.assertIn('restored to manifest', out)   # the checkouts did move
        self.assertIn('| smd: hs-uploader migrate failed', out)
        self.assertIn('re-render by the restored sigmond FAILED (exit 1)', out)
        self.assertIn('smd admin uploader manifest --write --enable', out)

    def test_a_hung_rerender_exits_1(self):
        fake = _FakeGit(rerender=subprocess.TimeoutExpired(['smd'], 600))
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 1)
        self.assertIn('timed out after 600 s', out)
        self.assertIn('FAILED', out)

    def test_a_restore_that_fails_verification_never_rerenders(self):
        fake = _FakeGit()
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.LIVE)
        self.assertEqual(rc, 1)
        self.assertEqual(self._rerenders(fake), [])
        self.assertIn('pipelines.toml not re-rendered', out)
```

The file already imports `subprocess`.  Every test patches `subprocess.run` with the fake, so no child smd
ever runs.  The six existing `--apply` tests keep passing: the default `rerender` answers success.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_manifest_restore.py -v`
Expected: 6 failed, 27 passed.  The five new `--apply` tests that expect a re-render find none: four fail
an `assertEqual` (`0 != 1`), and `test_without_a_sigmond_checkout_the_running_smd_rerenders` fails with
`IndexError: list index out of range`.  `test_a_restore_that_fails_verification_never_rerenders` fails on
`'pipelines.toml not re-rendered' not found`.  `test_dry_run_never_rerenders_the_uploader_manifest`
already passes; it pins the dry run against Step 3.

- [ ] **Step 3: Implement in `bin/smd`**

(a) Find (line 4307):

```python
def cmd_manifest_restore(args) -> int:
```

and insert above it, after the two blank lines that follow `cmd_manifest_adopt`:

```python
# Room for the child's own work: an hs-uploader install when its venv is
# missing, a migrate that may wait 120 s, and a daemon restart.
_RESTORE_RERENDER_TIMEOUT_S = 600


def _restore_rerender_uploader(base) -> int:
    """Re-render pipelines.toml with the sigmond a restore just put back
    (tasks/plan-sink-control.md D11).

    A restore moves checkouts and nothing else.  Without this step the
    manifest a newer sigmond rendered stays on disk, and a rolled-back
    hs-uploader reads pipelines it cannot honour at its next start.  This
    runs the restored checkout's own smd, `admin uploader manifest --write
    --enable`, so the restored code renders, writes, and restarts hs-uploader
    when the manifest changed.  It runs as a child: this process already
    holds the newer sigmond's modules in memory.

    The restored smd lives at `<base>/sigmond/bin/smd`; without a sigmond
    checkout under `base`, the running smd stands in.  It relays every line
    the child prints.  Returns 0 on success, 1 when the child failed, hung
    or could not start.
    """
    smd_path = Path(base) / 'sigmond' / 'bin' / 'smd'
    if not smd_path.is_file():
        smd_path = _SCRIPT
    argv = [sys.executable, str(smd_path), 'admin', 'uploader', 'manifest',
            '--write', '--enable']
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL,
                           timeout=_RESTORE_RERENDER_TIMEOUT_S)
        rc, text = r.returncode, (r.stdout or '') + (r.stderr or '')
    except subprocess.TimeoutExpired:
        rc, text = -1, f'timed out after {_RESTORE_RERENDER_TIMEOUT_S} s'
    except OSError as exc:
        rc, text = -1, f'could not run {smd_path}: {exc}'
    for line in text.splitlines():
        if line.strip():
            _info(f'| {line}')
    if rc == 0:
        _ok(f'pipelines.toml re-rendered by the restored sigmond ({smd_path})')
        return 0
    _err(f'pipelines.toml re-render by the restored sigmond FAILED (exit {rc}). '
         f'hs-uploader may still read pipelines a newer sigmond wrote.  Fix the '
         f'cause, then run `smd admin uploader manifest --write --enable`.')
    return 1


```

`sys`, `Path` and `subprocess` come from the module's own imports (lines 50, 51 and 96), and `_SCRIPT`
from line 106.  The tests patch `subprocess.run` on the module object, which this function looks up at call time.

(b) In the `cmd_manifest_restore` docstring, find (lines 4371-4374):

```python
       uses for its own install step. Finally, the SAME restart
       guidance `smd update` prints (`cmd_update`'s "restart not
       automated here" line) — restarts stay manual and stated, never
       silently triggered.
```

and replace it with:

```python
       uses for its own install step. Finally, the SAME restart
       guidance `smd update` prints (`cmd_update`'s "restart not
       automated here" line) — radiod and the recorders restart by
       hand, never silently.  hs-uploader alone restarts in step 8,
       and only when its manifest changed.
```

(c) Find (lines 4392-4398):

```python
       listed by name) and exits 1 — a restore that still shows drift
       immediately afterward is not a success just because the
       mechanical steps ran.

    Exit codes: 0 plan/restore ok, 1 refusal or post-apply verification
    failure, 2 usage (argparse's own doing, not this function's).
    """
```

and replace it with:

```python
       listed by name) and exits 1 — a restore that still shows drift
       immediately afterward is not a success just because the
       mechanical steps ran.
    8. Re-render (D11, only after step 7 verifies): the restored smd, as a
       child process, runs `admin uploader manifest --write --enable`
       (`_restore_rerender_uploader`).  A rolled-back hs-uploader then
       never reads pipelines a newer sigmond rendered.  It runs even when
       nothing moved, so a re-run repairs a re-render that failed.  A
       failed re-render exits 1; a dry run or a refused or unverified
       restore never re-renders.

    Exit codes: 0 plan/restore ok, 1 refusal, post-apply verification
    failure or a failed re-render, 2 usage (argparse's own doing, not
    this function's).
    """
```

(d) Find (lines 4602-4607):

```python
    if reverify.ok and not non_keep:
        print(f'admin manifest restore: restored to manifest — '
              f'{len(moved)} component(s) moved, re-plan verifies '
              f'all-keep ({len(reverify.strays)} stray component(s) '
              f'untouched)')
        return 0
```

and replace it with:

```python
    if reverify.ok and not non_keep:
        print(f'admin manifest restore: restored to manifest — '
              f'{len(moved)} component(s) moved, re-plan verifies '
              f'all-keep ({len(reverify.strays)} stray component(s) '
              f'untouched)')
        return _restore_rerender_uploader(base)
```

`base` holds the `Path` that line 4426 built from `--base`, or `/opt/git/sigmond`.

(e) Find (lines 4614-4616):

```python
    for n, a, f, t in non_keep:
        _err(f'{n}: still {a} ({f} -> {t}) after --apply')
    return 1
```

and replace it with:

```python
    for n, a, f, t in non_keep:
        _err(f'{n}: still {a} ({f} -> {t}) after --apply')
    _info('pipelines.toml not re-rendered: a restore re-renders only once '
          'it verifies')
    return 1
```

(f) In `main()`'s parser, find (lines 24033-24035):

```python
    p_m_restore.add_argument('--apply', action='store_true',
        help='move checkouts onto the manifest SHAs (root; only if the plan '
             'is ok). Default is dry-run: plan only, never touches a checkout.')
```

and replace it with:

```python
    p_m_restore.add_argument('--apply', action='store_true',
        help='move checkouts onto the manifest SHAs (root; only if the plan '
             'is ok), then re-render the hs-uploader manifest with the '
             'restored sigmond. Default is dry-run: plan only, never touches '
             'a checkout.')
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/pytest tests/test_manifest_restore.py -v`
Expected: 33 passed.

Run: `SIGMOND_NO_VENV_REEXEC=1 .venv/bin/python bin/smd admin manifest restore --help`
Expected: the `--apply` text reads `… (root; only if the plan is ok), then re-render the hs-uploader
manifest with the restored sigmond. Default is dry-run: …`.

Run: `.venv/bin/pytest -q`
Expected: no failures, and 7 more passed than at this task's base: `3436 passed, 1 warning, 53 subtests
passed` after Task 7's 3429, with Tasks 1-5 committed in the sibling `../hs-uploader`.

- [ ] **Step 5: Watch four guards fail, then restore**

Each mutation must turn exactly the named tests red.  Restore the file after each one.

```bash
cd /home/mjh/hamsci/repos/sigmond
F=bin/smd
T=tests/test_manifest_restore.py
cp $F $F.keep
sed -i 's|    if not smd_path.is_file():|    if True:|' $F
.venv/bin/pytest $T -q   # 1 failed: test_apply_rerenders_with_the_restored_smd_after_the_checkouts
cp $F.keep $F
sed -i 's/        return _restore_rerender_uploader(base)/        _restore_rerender_uploader(base); return 0/' $F
.venv/bin/pytest $T -q   # 2 failed: test_a_failed_rerender_exits_1_and_names_the_fix, test_a_hung_rerender_exits_1
cp $F.keep $F
sed -i "s/    _info('pipelines.toml not re-rendered: a restore re-renders only once '/    _restore_rerender_uploader(base); _info('pipelines.toml not re-rendered: a restore re-renders only once '/" $F
.venv/bin/pytest $T -q   # 1 failed: test_a_restore_that_fails_verification_never_rerenders
cp $F.keep $F
sed -i 's/^    if not apply_:$/    if not apply_ and not _restore_rerender_uploader(base):/' $F
.venv/bin/pytest $T -q   # 1 failed: test_dry_run_never_rerenders_the_uploader_manifest
mv $F.keep $F
.venv/bin/pytest $T -q   # 33 passed
```

The first renders with the running smd even where a restored one exists.  The second reports a failed
re-render as success.  The third re-renders a host that failed verification.  The fourth re-renders on a
dry run.

- [ ] **Step 6: Commit**

```bash
git add bin/smd tests/test_manifest_restore.py
git commit -m "restore: re-render pipelines.toml with the restored sigmond (D11)

After a verified smd admin manifest restore --apply, the restored
checkout's own smd runs admin uploader manifest --write --enable as a
child process.  A rolled-back hs-uploader then never reads pipelines a
newer sigmond rendered, and restarts only when the manifest changed.  A
failed re-render exits 1; a dry run, a refusal or an unverified restore
re-renders nothing.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
```


### Task 9: the rigs prove the migrate, the restart and the rollback

PHASE E of `test-update-v3.sh` runs `smd update --apply`, and PHASE G runs `smd admin manifest restore --apply`.
Neither re-renders `pipelines.toml` or restarts hs-uploader (`bin/smd:3991-3993`, `4578-4580` at sigmond 64cf83f),
so as the rig stands the v3.70 daemon never starts in E, and the restored daemon never meets the migrated store
in G. Spec §10.4 asks the update rig to show `hs-uploader migrate` running before the daemon restarts, and a
rollback whose re-rendered manifest matches the restored release. Two new phases do that. PHASE E-bis runs what
`smd align` runs once the code has moved: bring-up's manifest step, which from v3.70 runs `hs-uploader migrate`
first (Task 7), then a restart of hs-uploader. PHASE G-bis checks restore's own re-render (Task 8, D11), and starts
the restored daemon on the store E-bis migrated. `test-nested-v3.sh` learns three facts about a fresh v3.70
install: bring-up ran `migrate`, psk-recorder's venv writes through `hs_uploader.sink`, and a `sink.db` that holds
`pending_uploads` carries `producer` and `local`.

Neither rig runs here. A scratch harness plays the nested PM and its guest. It decodes exactly what `gx` and the
nested probe ship, runs the real hs-uploader CLI (Task 2's `migrate`, and the v3.69 code at 3e97223) against real
SQLite files, and hands back `qm guest exec`'s JSON. Ten mutations then show that each new check carries weight.

Decisions this task makes (the brief left them open):
1. **The rig restarts hs-uploader when the manifest step leaves the old daemon up, and says so.** Task 7 restarts
   the daemon only when the manifest changed, and v3.70 changes no manifest. `smd align` restarts it anyway,
   because its checkout moved after it started (`_align_staleness`, `_align_restart_uploader`). E-bis does the same,
   then asserts that the daemon started after the manifest step began and stayed up 15 s. G-bis does the same after
   the restore: that start stands in for the next crash, reboot or re-render, which would run the restored code.
2. **The nested check runs after `smd sink upload`, and reads the bring-up log.** On a fresh install bring-up's
   migrate probably meets no `watermarks.db`. hs-uploader's `install.sh` installs the unit without enabling it,
   capture-prep deletes the store, and the daemon creates it on its first start, at version 0, after the migrate
   (Task 2, decision 1). `smd sink upload` writes the manifest again, and its migrate lifts the store to version 1.
   The bring-up log then says whether bring-up ran `migrate` at all. A log with no `watermarks.db:` line FATALs.
3. **"Version 1" reads as "the version `migrate --check` reports, with nothing pending and nothing newer".** On
   v3.70 that reads 1. In v3.71 it reads 2, so the rigs need no edit for it. G-bis compares the rolled-back store
   with the version E-bis read: a rollback never lowers it.
4. **`SINK_BASE_IMAGE=1` now also covers a base image without `migrate`**, the v3.69 base under the update rig.
   Anywhere else such an image FATALs, for the reason the `SINKWIZ` gate gives.
5. **Every probe runs hs-uploader as `hsupload` from `/opt/hs-uploader`**, the daemon's user and working
   directory, so no root-owned file appears beside the store. Python runs without writing bytecode.

**Files:**
- Modify: `sigmond-appliance/test-update-v3.sh`: the phase list in the header (after line 20 and after line 24);
  the CONTRACT STRINGS block (after line 73); the `SINK_BASE_IMAGE` comment (lines 494-495); after
  `say "PHASE E PASS — …"` (line 873); above `gx_ok 1800 g3-restore-apply …` (line 968); after
  `say "PHASE G PASS — …"` (line 1057).
- Modify: `sigmond-appliance/test-nested-v3.sh`: the bring-up timeout branch (after line 881); between the
  `smd sink upload` block's closing `    fi` (line 1012) and the bring-up branch's closing `fi` (line 1013).
- Scratch, never committed: the harness under `$SCR`, `task9-harness` in this session's scratchpad directory.

Line numbers name each file at 6d1b8ce. 3129314 changed only `docs/RELEASE.md`, so they hold there too.

**Interfaces:**
- Consumes, Task 2: `hs-uploader migrate --check --db PATH` prints `watermarks.db: version N`, then
  `  pending N: …` per waiting migration, `  newer than this hs-uploader…` for a newer store, or
  `watermarks.db: not found at PATH; nothing to migrate`; exit 0. An older hs-uploader exits 2 with
  `invalid choice: 'migrate'`. The version lives in `PRAGMA user_version`.
- Consumes, Task 7: `smd admin uploader manifest --write [--enable]` runs `hs-uploader migrate` after the write and
  before any start or restart, exits 1 when it fails, and prints each line migrate prints (the draft prefixes
  `uploader: `). The rigs grep the substrings `watermarks.db: version ` and `watermarks.db: not found at `, so
  the prefix may change. It restarts hs-uploader only when the manifest changed; the rigs handle either.
- Consumes, Task 8: after every verified `smd admin manifest restore --apply`, even one that moved nothing,
  restore runs the restored smd's manifest write as a child. It relays each child line as `     | <line>`, then
  prints `✓  pipelines.toml re-rendered by the restored sigmond (<path to smd>)` through `_ok`, which colours
  the tick. G-bis greps the fixed text `pipelines.toml re-rendered by the restored sigmond`. A failed re-render
  prints `re-render by the restored sigmond FAILED` and exits 1, which g3 already turns into a FATAL.
- Consumes, Task 6: `python3 -c "import sigmond.hamsci_sink as s; print(s.SINK_IMPL)"` prints `hs_uploader` or
  `bundled`. Task 1: `pending_uploads` gains `producer` and `local`.
- Consumes, unchanged: `smd admin uploader manifest` (check mode) prints
  `uploader: <path> is up to date (N pipeline(s))` and exits 0 (`lib/sigmond/commands/uploader.py:149` at
  sigmond 64cf83f).
- Produces: `PHASE E-bis PASS — …` and `PHASE G-bis PASS — …` in `test-update-v3.log` (Task 10 Step 5b). In
  `test-v3.log`: `watermarks.db at version N with nothing pending (hs-uploader migrate --check) ✓` and
  `psk-recorder's venv writes through hs_uploader.sink (SINK_IMPL = hs_uploader) ✓` (Task 10 Step 5). The nest
  runs no writer, so `sink.db`'s columns come out as a `NOT EVALUATED` phrase there. Every existing PASS string
  stays byte-identical.

- [ ] **Step 1: Build the scratch harness and watch it fail**

`SCR` names `task9-harness` under this session's scratchpad directory. The scripts find each other through their
own directory, so nothing below bakes in the path. They read the two rigs and never write to any repo.
`HSU_NEW` defaults to `/home/mjh/hamsci/repos/hs-uploader/src`, which carries Task 2's `migrate` at this task's
base, and `HSU_OLD` to the v3.69 code at 3e97223.

```bash
mkdir -p "$SCR/stubs" "$SCR/hsu-old"
git -C /home/mjh/hamsci/repos/hs-uploader archive 3e97223 src | tar -x -C "$SCR/hsu-old"
```

The guest's commands. They sit on `PATH` only inside the fake guest.

Create `$SCR/stubs/base64`:

```bash
#!/bin/bash
# Decode as the guest would, then point guest paths at the fixture tree.
if [ "${1:-}" = "-d" ]; then
    /usr/bin/base64 -d | sed -e "s#/opt/hs-uploader#$FX/opt/hs-uploader#g" \
        -e "s#/var/lib/hs-uploader#$FX/var/lib/hs-uploader#g" \
        -e "s#/var/lib/sigmond#$FX/var/lib/sigmond#g" \
        -e "s#/var/log/sigmond#$FX/var/log/sigmond#g" \
        -e "s#/opt/git/sigmond/psk-recorder#$FX/opt/git/sigmond/psk-recorder#g" \
        -e "s#/tmp/u2-gx#$RUN/u2-gx#g"
else
    exec /usr/bin/base64 "$@"
fi
```

Create `$SCR/stubs/runuser`:

```bash
#!/bin/bash
# runuser -u USER -- cmd...: run cmd as this user (the harness has no hsupload).
[ -f "$FX/state/no_hsupload" ] && { echo "runuser: user hsupload does not exist" >&2; exit 1; }
while [ $# -gt 0 ] && [ "$1" != -- ]; do shift; done; shift
exec "$@"
```

Create `$SCR/stubs/systemctl`:

```bash
#!/bin/bash
S=$FX/state
mono(){ python3 -c 'import time; print(time.monotonic_ns() // 1000)'; }
echo "systemctl $*" >> $S/calls
case "$1" in
  is-active) if [ "$(cat $S/active)" = 1 ]; then echo active; exit 0; else echo inactive; exit 3; fi ;;
  show) st=$(cat $S/start)
        if [ -f $S/crash_on_settle ] && [ -f $S/slept ]; then st=$((st + 5000000)); fi
        echo "$st" ;;
  restart) [ -f $S/restart_fails ] && { echo "Job for hs-uploader.service failed." >&2; exit 1; }
           [ -f $S/restart_noop ] && exit 0
           echo 1 > $S/active; mono > $S/start; rm -f $S/slept; exit 0 ;;
  enable) [ -f $S/enable_noop ] && exit 0
          if [ "$(cat $S/active)" != 1 ]; then echo 1 > $S/active; mono > $S/start; fi; exit 0 ;;
  *) echo "stub systemctl: $*" >&2; exit 99 ;;
esac
```

Create `$SCR/stubs/sleep`:

```bash
#!/bin/bash
touch "$FX/state/slept"
```

Create `$SCR/stubs/journalctl`:

```bash
#!/bin/bash
echo "daemon: 1 pipeline(s): heartbeat"
echo "daemon: active (pump interval 30s, wake disabled)"
```

Create `$SCR/stubs/smd`, which plays Task 7's manifest step and the check mode:

```bash
#!/bin/bash
# Plays sigmond's smd for the two verbs the update rig's new phases call.
S=$FX/state
case "$*" in
  "admin uploader manifest --write --enable")
     echo "uploader: /etc/hs-uploader/pipelines.toml already current (1 pipeline(s))"
     [ -f $S/smd_fail ] && { echo "smd: hs-uploader migrate failed (exit 1): database is locked" >&2; exit 1; }
     if [ ! -f $S/smd_nomigrate ]; then
        out=$(cd $FX/opt/hs-uploader && runuser -u hsupload -- env HS_UPLOADER_STATE_DIR=$FX/var/lib/hs-uploader $FX/opt/hs-uploader/venv/bin/hs-uploader migrate 2>&1); rc=$?
        if [ $rc = 2 ] && printf '%s\n' "$out" | grep -q "invalid choice: 'migrate'"; then
            echo "uploader: this hs-uploader predates \`migrate\`; send-record migrate skipped"
        else
            printf '%s\n' "$out" | sed 's/^/uploader: /'
            [ $rc = 0 ] || { echo "smd: hs-uploader migrate failed (exit $rc)" >&2; exit 1; }
        fi
     fi
     [ -f $S/smd_traceback ] && echo "Traceback (most recent call last):"
     if [ -f $S/smd_restarts ]; then systemctl restart hs-uploader.service; else systemctl enable --now hs-uploader.service; fi
     exit 0 ;;
  "admin uploader manifest")
     if [ -f $S/smd_drift ]; then echo "uploader: /etc/hs-uploader/pipelines.toml DRIFT — \`--write\` would change it:"; exit 1; fi
     echo "uploader: /etc/hs-uploader/pipelines.toml is up to date (1 pipeline(s))"; exit 0 ;;
  *) echo "stub smd: unexpected args: $*" >&2; exit 99 ;;
esac
```

The nested PM. Its shell parses the command line the rig sends; `qm` runs the command in the "guest" and answers
in `qm guest exec`'s JSON, indent 3 and `" : "` separators. Create `$SCR/fake-ssh`:

```bash
#!/bin/bash
cmd="${!#}"
n=$(( $(cat "$FX/state/sshcount" 2>/dev/null || echo 0) + 1 )); echo $n > "$FX/state/sshcount"
if [ -n "${DEAD_AT:-}" ] && [ "$n" -ge "$DEAD_AT" ]; then echo "QEMU guest agent is not running" >&2; exit 255; fi
cmd="${cmd//\/tmp\/sig-f70.sh/$RUN/sig-f70.sh}"
cmd="${cmd//\/tmp\/u2-gx/$RUN/u2-gx}"
qm(){
    while [ $# -gt 0 ] && [ "$1" != -- ]; do shift; done; shift
    local out rc
    if [ "$1" = bash ] && [ "$2" = -lc ]; then
        shift 2; out=$(PATH="$STUBS:/usr/bin:/bin" bash --noprofile --norc -c "$@" 2>&1); rc=$?
    else
        out=$(PATH="$STUBS:/usr/bin:/bin" "$@" 2>&1); rc=$?
    fi
    printf '%s\n' "$out" | python3 -c 'import json,sys; print(json.dumps({"exitcode":int(sys.argv[1]),"exited":1,"out-data":sys.stdin.read()}, indent=3, separators=(",", " : ")))' "$rc"
}
eval "$cmd"
```

The guest's hs-uploader: the real CLI, from whichever tree the "checkout" holds. Create `$SCR/hsu-fixture`:

```bash
#!/bin/bash
# The guest's /opt/hs-uploader/venv/bin/hs-uploader: the REAL hs-uploader CLI,
# run from whichever source tree the "checkout" holds ($FX/state/code).
export PYTHONDONTWRITEBYTECODE=1
[ -f "$FX/state/hsu_liar" ] && [ "$1" = migrate ] && { echo "watermarks.db: version 1"; echo "  nothing to do"; exit 0; }
if [ -f "$FX/state/hsu_pend2" ] && [ "$1 $2" = "migrate --check" ]; then
    echo "watermarks.db: version 1"; echo "  pending 2: split the shared send-record keys"; exit 0
fi
PYTHONPATH="$(cat "$FX/state/code")" exec "$HSU_PY" -c 'import sys; from hs_uploader.cli import main; sys.exit(main())' "$@"
```

Create `$SCR/extract.sh`:

```bash
#!/bin/bash
# extract.sh <repo> <outdir>: cut Task 9's blocks, as committed, out of the two
# rigs, so every case below runs the exact bytes.  A block that is not there
# stops the harness: that is the RED run.
SRC="$1"; OUT="$2"; mkdir -p "$OUT"
U="$SRC/test-update-v3.sh"; N="$SRC/test-nested-v3.sh"
{ sed -n '/^fatal(){/,/^}/p' "$U"; sed -n '/^excerpt(){/,/^}/p' "$U"
  sed -n '/^GX_CHAN=1; GX_QM=/,/^GX_TOKEN=/p' "$U"; sed -n '/^gx(){/,/^}/p' "$U"
  sed -n '/^gx_ok(){/,/^}/p' "$U"; } > "$OUT/helpers.sh"
awk '/^say "PHASE E PASS/{f=1; next} f{print} /^say "PHASE E-bis PASS/{exit}' "$U" > "$OUT/ebis.sh"
awk '/^# PHASE G-bis orders/{f=1} f{print} f && /^\[ -n "\$GB_T0" \]/{exit}' "$U" > "$OUT/gb0.sh"
awk '/^say "PHASE G PASS/{f=1; next} f{print} /^say "PHASE G-bis PASS/{exit}' "$U" > "$OUT/gbis.sh"
awk '/# ── the v3.70 sink foundation/{f=1} /^# What the site sink checks left unchecked/{exit} f{print}' "$N" \
    | sed '$d' | sed '$d' > "$OUT/f70.sh"
awk '/^# What the site sink checks left unchecked/{f=1} f{print} /^say "PHASE D PASS/{exit}' "$N" > "$OUT/closing.sh"
sed -n '/^SINK_UNCHECKED=""$/p; /^sink_unchecked(){/p' "$N" > "$OUT/sinkdefs.sh"
grep -A1 'THE v3.70 SINK FOUNDATION CHECKS WERE NOT EVALUATED' "$N" > "$OUT/timeout.sh"
rc=0
for b in ebis:'PHASE E-bis PASS' gb0:'gb0-clock' gbis:'PHASE G-bis PASS' f70:'v3.70 sink foundation' timeout:'sink_unchecked'; do
    grep -q "${b#*:}" "$OUT/${b%%:*}.sh" || { echo "extract: no ${b%%:*} block in $SRC"; rc=1; }
done
exit $rc
```

Create `$SCR/run-upd.sh`:

```bash
#!/bin/bash
# run-upd.sh <case> <blocks> : a guest after PHASE E (v3.70 code in the
# checkout, a store the v3.69 code made, the old daemon up for 1000 s), then
# E-bis, the gb0 clock, a stand-in for g3's restore, and G-bis.
CASE="$1"; X="$2"; H="$(cd "$(dirname "$0")" && pwd)"
: "${HSU_PY:=/home/mjh/hamsci/repos/hs-uploader/.venv/bin/python}"
: "${HSU_NEW:=/home/mjh/hamsci/repos/hs-uploader/src}"
: "${HSU_OLD:=$H/hsu-old/src}"
export HSU_PY FX="$H/runs/upd-$CASE" STUBS="$H/stubs"; export RUN="$FX/run"
S="$FX/state"; W="$FX/var/lib/hs-uploader/watermarks.db"
rm -rf "$FX"; mkdir -p "$S" "$RUN" "$FX/opt/hs-uploader/venv/bin" "$FX/var/lib/hs-uploader"
cp "$H/hsu-fixture" "$FX/opt/hs-uploader/venv/bin/hs-uploader"
echo "$HSU_NEW" > "$S/code"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$HSU_OLD" "$HSU_PY" -c \
    'import sys; from hs_uploader.watermark.sqlite import SqliteWatermarkStore; SqliteWatermarkStore(sys.argv[1])' "$W"
echo 1 > "$S/active"
python3 -c 'import time; print(time.monotonic_ns() // 1000 - 1000 * 10**6)' > "$S/start"
setu(){ python3 -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("PRAGMA user_version=%d" % int(sys.argv[2])); c.commit()' "$W" "$1"; }
case "$CASE" in
  restarts)     touch "$S/smd_restarts" ;;
  nomigrate)    touch "$S/smd_nomigrate" ;;
  smdfail)      touch "$S/smd_fail" ;;
  restartnoop)  touch "$S/restart_noop" ;;
  settlecrash)  touch "$S/crash_on_settle" ;;
  restartfail)  touch "$S/restart_fails" ;;
  oldtarget)    echo "$HSU_OLD" > "$S/code" ;;
  liar)         touch "$S/hsu_liar" ;;
  pend2)        touch "$S/hsu_pend2" ;;
  nostore)      rm -f "$W" ;;
  newer)        setu 5 ;;
  inactive)     echo 0 > "$S/active"; touch "$S/enable_noop" ;;
  traceback)    touch "$S/smd_traceback" ;;
  nohsupload)   touch "$S/no_hsupload" ;;
  dead_eb2)     export DEAD_AT=3 ;;
esac
cd "$FX"; WORK="$FX/work"; mkdir -p "$WORK"; VMID=100; SSHN="$H/fake-ssh"; TARGET=origin/main
say(){ echo "[upd stub] $*"; }
set -u
. "$X/helpers.sh"
. "$X/ebis.sh"
echo "REACHED: PHASE E-bis PASS"
. "$X/gb0.sh"
# ── a stand-in for g3: restore checks the old code out and prints its lines,
# then Task 8's re-render: the child's relayed lines and the _ok line ──
echo "$HSU_OLD" > "$S/code"; MOVED=3
case "$CASE" in calendar|calnorr) MOVED=0 ;; esac
{ echo "  hs-uploader: checked out 3e97223 (detached HEAD)"
  echo "admin manifest restore: restored to manifest — $MOVED component(s) moved, re-plan verifies all-keep (0 stray component(s) untouched)"
  echo "     | uploader: /etc/hs-uploader/pipelines.toml already current (1 pipeline(s))"
  case "$CASE" in
    norerender|calnorr) ;;
    *) printf '  \033[32m✓\033[0m  pipelines.toml re-rendered by the restored sigmond (/opt/git/sigmond/sigmond/bin/smd)\n' ;;
  esac; } > "$WORK/g3-restore-apply.out"
case "$CASE" in
  restarts)   PATH="$STUBS:$PATH" systemctl restart hs-uploader.service ;;
  gdrift)     touch "$S/smd_drift" ;;
  glower)     setu 0 ;;
  ginactive)  echo 0 > "$S/active" ;;
  dead_gb)    rm -f "$S/sshcount"; export DEAD_AT=2 ;;
esac
. "$X/gbis.sh"
echo "REACHED: PHASE G-bis PASS"
```

Create `$SCR/run-nst.sh`:

```bash
#!/bin/bash
# run-nst.sh <case> <blocks> : a guest after bring-up and smd sink upload, then
# the v3.70 block and the file's own closing lines.
CASE="$1"; X="$2"; H="$(cd "$(dirname "$0")" && pwd)"
: "${HSU_PY:=/home/mjh/hamsci/repos/hs-uploader/.venv/bin/python}"
: "${HSU_NEW:=/home/mjh/hamsci/repos/hs-uploader/src}"
: "${HSU_OLD:=$H/hsu-old/src}"
export HSU_PY FX="$H/runs/nst-$CASE" STUBS="$H/stubs"; export RUN="$FX/run"
S="$FX/state"; W="$FX/var/lib/hs-uploader/watermarks.db"; L="$FX/var/log/sigmond/firstrun-bringup.log"
SDB="$FX/var/lib/sigmond/sink.db"; PKG="$FX/pskpkg/sigmond/hamsci_sink"
rm -rf "$FX"; mkdir -p "$S" "$RUN" "$FX/opt/hs-uploader/venv/bin" "$FX/var/lib/hs-uploader" "$FX/var/log/sigmond" \
    "$FX/var/lib/sigmond" "$FX/opt/git/sigmond/psk-recorder/venv/bin" "$PKG"
cp "$H/hsu-fixture" "$FX/opt/hs-uploader/venv/bin/hs-uploader"
echo "$HSU_NEW" > "$S/code"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$HSU_OLD" "$HSU_PY" -c \
    'import sys; from hs_uploader.watermark.sqlite import SqliteWatermarkStore; SqliteWatermarkStore(sys.argv[1])' "$W"
setu(){ python3 -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("PRAGMA user_version=%d" % int(sys.argv[2])); c.commit()' "$W" "$1"; }
LOGV="uploader: watermarks.db: version 1"
LOGNF="uploader: watermarks.db: not found at /var/lib/hs-uploader/watermarks.db; nothing to migrate"
printf '%s\n' "2026-10-08T01:00:00+00:00 bring-up starting" "» generate hs-uploader manifest + enable daemon" "$LOGV" > "$L"
# psk-recorder's venv: a python that finds a stand-in sigmond.hamsci_sink
printf '#!/bin/bash\nPYTHONPATH="%s" exec /usr/bin/python3 "$@"\n' "$FX/pskpkg" > "$FX/opt/git/sigmond/psk-recorder/venv/bin/python3"
chmod +x "$FX/opt/git/sigmond/psk-recorder/venv/bin/python3"
: > "$FX/pskpkg/sigmond/__init__.py"; echo 'SINK_IMPL = "hs_uploader"' > "$PKG/__init__.py"
: > "$SDB"                                  # bring-up stage 1's empty file
OLDDDL="CREATE TABLE pending_uploads (id INTEGER PRIMARY KEY AUTOINCREMENT, target_db TEXT NOT NULL, target_table TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL, queued_at TEXT NOT NULL)"
sinkdb(){ rm -f "$SDB"; python3 -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); [c.execute(s) for s in sys.argv[2:]]; c.commit()' "$SDB" "$@"; }
notfound(){ sed -i '$d' "$L"; echo "$LOGNF" >> "$L"; }
REFUSED='{ "out-data" : "smd: refusing while packaging runs (grape-daily)" }'
SINKWIZ=1; UP='{ "out-data" : "site sink: upload (store and send)\nDISCLEFT:0" }'; SINK_BASE_IMAGE=0
setu 1
case "$CASE" in
  happy_fresh)  notfound ;;
  happy_full)   sinkdb "$OLDDDL" "ALTER TABLE pending_uploads ADD COLUMN producer TEXT NOT NULL DEFAULT ''" \
                       "ALTER TABLE pending_uploads ADD COLUMN local INTEGER NOT NULL DEFAULT 0" ;;
  v0_refused)   notfound; setu 0; UP="$REFUSED" ;;
  v0_later)     notfound; setu 0 ;;
  v0_logv1)     setu 0 ;;
  nolog_line)   sed -i '$d' "$L" ;;
  base_skip)    echo "$HSU_OLD" > "$S/code"; setu 0; SINK_BASE_IMAGE=1 ;;
  base_fatal)   echo "$HSU_OLD" > "$S/code"; setu 0 ;;
  bundled)      echo 'SINK_IMPL = "bundled"' > "$PKG/__init__.py" ;;
  noimpl)       echo 'Writer = object' > "$PKG/__init__.py" ;;
  nopsk)        rm -rf "$FX/opt/git/sigmond/psk-recorder" ;;
  oldsink)      sinkdb "$OLDDDL" ;;
  badsink)      printf 'not a database: %0100d' 0 > "$SDB" ;;
  nosink)       rm -f "$SDB" ;;
  nostore)      rm -f "$W" ;;
  newer)        setu 5 ;;
  dead)         export DEAD_AT=1 ;;
  noexe)        rm -f "$FX/opt/hs-uploader/venv/bin/hs-uploader" ;;
  nohsupload)   touch "$S/no_hsupload" ;;
  nobulog)      rm -f "$L" ;;
  nobulog_v0)   rm -f "$L"; setu 0; UP="$REFUSED" ;;
  sinkwiz0_v0)  notfound; setu 0; SINKWIZ=0; unset UP ;;
esac
cd "$FX"; VMID=100; SSHN="$H/fake-ssh"
say(){ echo "[test stub] $*"; }
set -u
. "$X/sinkdefs.sh"
. "$X/f70.sh"
. "$X/closing.sh"
```

Create `$SCR/run-all-upd.sh`:

```bash
#!/bin/bash
# Every case, and the verdict it must reach.  PASS = reaches G-bis PASS.
H="$(cd "$(dirname "$0")" && pwd)"; X="$1"; ok=0; bad=0
while read -r c want; do
    out="$("$H/run-upd.sh" "$c" "$X" 2>&1)"; printf '%s\n' "$out" > "$H/runs/upd-$c.log"
    if printf '%s\n' "$out" | grep -q '^REACHED: PHASE G-bis PASS'; then got=PASS
    else got="FATAL: $(printf '%s\n' "$out" | grep -m1 -oE 'FATAL: .{0,260}' | sed 's/^FATAL: //')"; fi
    case "$want" in PASS) [ "$got" = PASS ] ;; *) case "$got" in "FATAL: "*"$want"*) true ;; *) false ;; esac ;; esac \
        && { ok=$((ok+1)); r=ok; } || { bad=$((bad+1)); r=MISMATCH; }
    printf '%-12s %-9s %s\n' "$c" "$r" "$got"
done <<'CASES'
happy        PASS
restarts     PASS
calendar     PASS
nomigrate    still pending
smdfail      manifest --write --enable exited 1
restartnoop  never restarted after the migrate
settlecrash  restarted during the 15 s settle
restartfail  did not reach READY
oldtarget    has no migrate command
liar         PRAGMA user_version reads 0
nostore      no watermarks.db before the manifest step
newer        newer than the updated hs-uploader
inactive     not active after smd admin uploader manifest --enable
traceback    printed a Python traceback
nohsupload   exited 1
pend2        with 1 migration(s) still pending
dead_eb2     guest-exec channel failed
norerender   printed no re-render line
calnorr      printed no re-render line
gdrift       manifest check exited 1
glower       the rollback changed the store's version
ginactive    not active after the restore
dead_gb      guest-exec channel failed
CASES
echo "SUMMARY: $ok as expected, $bad mismatched"
[ "$bad" = 0 ]
```

Create `$SCR/run-all-nst.sh`:

```bash
#!/bin/bash
# Each case's verdict: PASS (no NOT EVALUATED line), PASS+<phrase> (PASS, and the
# closing line names <phrase>), or the FATAL text it must print.
H="$(cd "$(dirname "$0")" && pwd)"; X="$1"; ok=0; bad=0
while IFS='|' read -r c want; do
    out="$("$H/run-nst.sh" "$c" "$X" 2>&1)"; printf '%s\n' "$out" > "$H/runs/nst-$c.log"
    if printf '%s\n' "$out" | grep -q 'PHASE D PASS'; then
        ne="$(printf '%s\n' "$out" | sed -n 's/.*SITE SINK CHECKS NOT EVALUATED: //p')"
        got="PASS${ne:++$ne}"
    else
        got="FATAL: $(printf '%s\n' "$out" | grep -m1 -oE 'FATAL: .{0,200}' | sed 's/^FATAL: //')"
    fi
    case "$want" in
        PASS) [ "$got" = PASS ] ;;
        PASS+*) case "$got" in "PASS+"*"${want#PASS+}"*) true ;; *) false ;; esac ;;
        *) case "$got" in "FATAL: "*"$want"*) true ;; *) false ;; esac ;;
    esac && { ok=$((ok+1)); r=ok; } || { bad=$((bad+1)); r=MISMATCH; }
    printf '%-12s %-9s %s\n' "$c" "$r" "$got"
done <<'CASES'
happy_fresh|PASS+no writer has created pending_uploads
happy_full|PASS
v0_refused|PASS+bring-up's migrate ran before the daemon created watermarks.db
v0_later|neither bring-up
v0_logv1|neither bring-up
nolog_line|bring-up never ran hs-uploader migrate
base_skip|PASS+this image's hs-uploader has no migrate
base_fatal|has no hs-uploader migrate
bundled|fell back to sigmond's bundled writer
noimpl|importing sigmond.hamsci_sink in psk-recorder's venv exited 1
nopsk|PASS+no psk-recorder venv
oldsink|lacks producer (0) or local (0)
badsink|could not read the file
nosink|PASS+no /var/lib/sigmond/sink.db
nostore|PASS+the daemon has not created its store
newer|newer than this image's hs-uploader
dead|returned no answer
noexe|no /opt/hs-uploader/venv/bin/hs-uploader
nohsupload|migrate --check exited 1
nobulog|PASS+no firstrun-bringup.log
nobulog_v0|PASS+no bring-up log, and smd sink upload ran no migrate
sinkwiz0_v0|PASS+smd sink upload ran no migrate
CASES
echo "SUMMARY: $ok as expected, $bad mismatched"
[ "$bad" = 0 ]
```

Create `$SCR/mutate.py`:

```python
"""Each mutation removes one check from a copy of the patched rigs; the named
case must change its verdict, or the check carries no weight."""
import pathlib, shutil, subprocess, sys
H = pathlib.Path(__file__).resolve().parent
REPO = pathlib.Path(sys.argv[1])        # the edited sigmond-appliance
M = [
 ("MU1", "test-update-v3.sh", '[ "$EB_S1" -gt "$EB_T0" ] \\\n', 'true \\\n', "upd", "restartnoop"),
 ("MU2", "test-update-v3.sh", '[ "$EB_S1" = "$EB_S2" ] \\\n', 'true \\\n', "upd", "settlecrash"),
 ("MU3", "test-update-v3.sh", '[ -n "$GB_RR" ] \\\n', 'true \\\n', "upd", "norerender"),
 ("MU4", "test-update-v3.sh", '[ "$GB_UV" = "$EB_VER" ] \\\n', 'true \\\n', "upd", "glower"),
 ("MU5", "test-update-v3.sh", '[ "$EB_PEND" = 0 ] || fatal', 'true || fatal', "upd", "pend2"),
 ("MN1", "test-nested-v3.sh", 'elif [ "$_later" = 0 ] && [ "$_blv" = 0 ] && [ "$_blnf" -ge 1 ]; then',
                              'elif [ "$_blv" = 0 ] && [ "$_blnf" -ge 1 ]; then', "nst", "v0_later"),
 ("MN2", "test-nested-v3.sh", "grep -c 'watermarks\\.db: version [0-9]' \"$L\"", "grep -c 'watermarks\\.db: versoin [0-9]' \"$L\"", "nst", "happy_full"),
 ("MN3", "test-nested-v3.sh", '[ "$_sprod" = 1 ] && [ "$_sloc" = 1 ] \\\n', 'true \\\n', "nst", "oldsink"),
 ("MN4", "test-nested-v3.sh", 'if [ "${SINK_BASE_IMAGE:-0}" != 1 ]; then\n            say "FATAL: the image under test has no hs-uploader migrate',
                              'if false; then\n            say "FATAL: the image under test has no hs-uploader migrate', "nst", "base_fatal"),
 ("MN5", "test-nested-v3.sh", "grep -cx 'hs_uploader'", "grep -cx 'hs_uploaderX'", "nst", "happy_full"),
]
missed = 0
def verdict(kind, case, x):
    out = subprocess.run([str(H / f"run-{kind}.sh"), case, str(x)], capture_output=True, text=True).stdout
    if kind == "upd":
        return ("G-bis PASS" if "REACHED: PHASE G-bis PASS" in out else
                "E-bis PASS, G-bis FATAL" if "REACHED: PHASE E-bis PASS" in out else "E-bis FATAL")
    if "PHASE D PASS" not in out: return "FATAL"
    return "PASS+NE" if "SITE SINK CHECKS NOT EVALUATED" in out else "PASS"
for mid, f, old, new, kind, case in M:
    w = H / f"mut-{mid}"; shutil.rmtree(w, ignore_errors=True); w.mkdir()
    for g in ("test-update-v3.sh", "test-nested-v3.sh"): shutil.copy2(REPO / g, w / g)
    p = w / f; t = p.read_text(); assert t.count(old) == 1, (mid, t.count(old)); p.write_text(t.replace(old, new))
    subprocess.run([str(H / "extract.sh"), str(w), str(H / f"xm-{mid}")], capture_output=True)
    base = verdict(kind, case, H / "x"); mut = verdict(kind, case, H / f"xm-{mid}")
    shutil.rmtree(w); shutil.rmtree(H / f"xm-{mid}")
    missed += base == mut
    print(f"{mid} {case:12s} real={base:24s} mutant={mut:24s} {'CAUGHT' if base != mut else 'NOT CAUGHT'}")
print(f"MUTATIONS: {len(M) - missed} caught, {missed} not caught")
sys.exit(1 if missed else 0)
```

Create `$SCR/run.sh`:

```bash
#!/bin/bash
# run.sh <sigmond-appliance checkout>: extract the blocks, run every case, then
# the mutations.  Exit 0 only when all three agree.
H="$(cd "$(dirname "$0")" && pwd)"; REPO="${1:-/home/mjh/hamsci/repos/sigmond-appliance}"
rm -rf "$H/x" "$H/runs"; mkdir -p "$H/runs"
"$H/extract.sh" "$REPO" "$H/x" || exit 1
"$H/run-all-upd.sh" "$H/x"; u=$?
"$H/run-all-nst.sh" "$H/x"; n=$?
python3 "$H/mutate.py" "$REPO"; m=$?
[ $u = 0 ] && [ $n = 0 ] && [ $m = 0 ]
```

Run: `chmod +x "$SCR"/stubs/* "$SCR"/*.sh "$SCR/fake-ssh" "$SCR/hsu-fixture" && "$SCR/run.sh" /home/mjh/hamsci/repos/sigmond-appliance; echo "exit=$?"`

Expected (RED): extract finds none of the blocks this task adds.
```
extract: no ebis block in /home/mjh/hamsci/repos/sigmond-appliance
extract: no gb0 block in /home/mjh/hamsci/repos/sigmond-appliance
extract: no gbis block in /home/mjh/hamsci/repos/sigmond-appliance
extract: no f70 block in /home/mjh/hamsci/repos/sigmond-appliance
extract: no timeout block in /home/mjh/hamsci/repos/sigmond-appliance
exit=1
```

- [ ] **Step 2: `test-update-v3.sh`: the phase list, the contract strings, the base-image comment**

(a) Find (line 20):

```bash
#             and the awareness heartbeat contract survives the update.
```

and add directly below it:

```bash
#   PHASE E-bis — what `smd align` does once the code has moved: bring-up's
#             manifest step, which from v3.70 runs `hs-uploader migrate`
#             first, then a restart of hs-uploader.  watermarks.db must sit
#             at the release's schema version with nothing pending, and the
#             daemon must start after the migrate and stay up.
```

(b) Find (line 24):

```bash
#             STRICT adopt of the same manifest: the round-trip proof.
```

and add directly below it:

```bash
#   PHASE G-bis — the rollback's own re-render (D11), then the restored
#             release's daemon: the restored smd reads pipelines.toml as up
#             to date, and the restored daemon starts on the store E-bis
#             migrated, which keeps its version.
```

(c) Find (lines 73-74):

```bash
#                                       test-nested-v3.sh:36, :385
set -u
```

and insert between those two lines:

```bash
#   "watermarks.db: version <N>",       hs-uploader src/hs_uploader/cli.py
#   "  pending <N>: ...",               _cmd_migrate (v3.70).  An older
#   "newer than this hs-uploader"       hs-uploader exits 2 with
#                                       "invalid choice: 'migrate'"
#   "is up to date"                     lib/sigmond/commands/uploader.py
#                                       cmd_uploader_manifest, check mode
#   "pipelines.toml re-rendered by the restored sigmond"
#                                       bin/smd _restore_rerender_uploader,
#                                       after every verified restore
#                                       --apply (v3.70, D11)
```

(d) Find (lines 494-495):

```bash
    # SINK_BASE_IMAGE=1 tells the sibling that this image may predate the site
    # sink switch; without it the sibling fails any image whose wizard lacks it.
```

and replace them with:

```bash
    # SINK_BASE_IMAGE=1 tells the sibling that this image may predate the site
    # sink switch or v3.70's sink foundation (an hs-uploader with `migrate`);
    # without it the sibling fails any image that lacks either.
```

- [ ] **Step 3: `test-update-v3.sh`: PHASE E-bis**

Find (line 873):

```bash
say "PHASE E PASS — rolled forward to $TARGET_SHA, idempotent, level, no new findings"
```

and add directly below it. The block opens with a blank line; the blank line and the `# ═══…` banner of PHASE F
that follow it today keep their place.

```bash

# ════════════════════════════════════════════════════════════════════════
say "════ PHASE E-bis: migrate the send-record store, then restart hs-uploader on it ════"

# `smd update --apply` moves code and stops there.  It re-runs no bring-up
# step and restarts no daemon, so after PHASE E the hs-uploader daemon still
# runs the code it started with, on a watermarks.db at the base image's
# schema version.  `smd align` finishes the job in two steps: bring-up's
# manifest step, which from v3.70 runs `hs-uploader migrate` before anything
# starts or restarts the daemon (D10), and then a restart of hs-uploader,
# last, because its checkout moved after it started (bin/smd
# _align_staleness and _align_restart_uploader).  The rig runs both steps
# through the production channel.  It cannot run `smd align` itself: align
# reads GitHub Releases, and nobody has blessed the release under test yet.
#
# Every probe below prints one KEY:N line per fact.  gx hands back real
# lines, so gx_tok anchors the match at both ends and one key never reads
# another's value.  A key that does not come back means the probe did not
# answer.  The rig says so, and never reads the gap as 0.
gx_tok(){ sed -n "s/^$2:\([0-9][0-9]*\)\$/\1/p" "$1" | head -1; }

# The send-record store.  migrate --check runs as hsupload, the store's
# owner and the daemon's user, from the unit's working directory, so no
# root-owned file can appear beside the store.  PRAGMA user_version then
# reads the same number straight from the file, through code that shares
# nothing with migrate.
UPL_STORE=$(cat <<'UPLEOF'
W=/var/lib/hs-uploader/watermarks.db
EXE=/opt/hs-uploader/venv/bin/hs-uploader
if [ -e "$W" ]; then echo "WMFILE:1"; else echo "WMFILE:0"; fi
o=$(cd /opt/hs-uploader && runuser -u hsupload -- /usr/bin/env PYTHONDONTWRITEBYTECODE=1 "$EXE" migrate --check --db "$W" 2>&1)
echo "MIGRC:$?"
echo "MIGVER:$(printf '%s\n' "$o" | sed -n 's/^watermarks\.db: version \([0-9][0-9]*\)$/\1/p' | head -1)"
echo "MIGPEND:$(printf '%s\n' "$o" | grep -c '^  pending ')"
echo "MIGNEWER:$(printf '%s\n' "$o" | grep -c 'newer than this hs-uploader')"
printf '%s\n' "$o" | sed 's/^/  migrate --check | /'
if [ -e "$W" ]; then
    uv=$(cd / && runuser -u hsupload -- /usr/bin/env PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -c '
import pathlib, sqlite3, sys
p = pathlib.Path(sys.argv[1]).resolve()
c = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=30)
print(c.execute("PRAGMA user_version").fetchone()[0])' "$W" 2>&1)
    case "$uv" in
        ''|*[!0-9]*) printf '%s\n' "$uv" | tail -3 | sed 's/^/  user_version read | /' ;;
        *) echo "USERVER:$uv" ;;
    esac
fi
exit 0
UPLEOF
)

# The daemon: ACTIVE1 and START1 (ActiveEnterTimestampMonotonic, in µs).
# With SETTLE=<s> on a line ahead of it, a second read follows the settle:
# Restart=always brings back a daemon that died after READY, with a new
# start time, so an unchanged START2 shows it stayed up.
UPL_UNIT=$(cat <<'UPLEOF'
SVC=hs-uploader.service
unit(){
    if [ "$(systemctl is-active "$SVC" 2>/dev/null)" = active ]; then echo "ACTIVE$1:1"; else echo "ACTIVE$1:0"; fi
    echo "START$1:$(systemctl show -p ActiveEnterTimestampMonotonic --value "$SVC" 2>/dev/null)"
}
unit 1
if [ "${SETTLE:-0}" -gt 0 ]; then sleep "$SETTLE"; unit 2; fi
journalctl -u "$SVC" -n 5 --no-pager -o cat 2>/dev/null | sed 's/^/  journal | /'
exit 0
UPLEOF
)

# The guest's CLOCK_MONOTONIC in µs, the clock systemd stamps
# ActiveEnterTimestampMonotonic with.  The rig can then order a start
# against one of its own moments without trusting the wall clock, which
# chrony may step.
UPL_CLOCK=$(cat <<'UPLEOF'
echo "MONO:$(python3 -c 'import time; print(time.monotonic_ns() // 1000)')"
UPLEOF
)

# ── the store before the manifest step ─────────────────────────────────
gx_ok 120 eb0-store-pre "$UPL_STORE" "E-bis: the send-record store before the manifest step"
sed 's/^/      /' "$GX_OUT"
EB_PRE_FILE="$(gx_tok "$GX_OUT" WMFILE)"
EB_PRE_VER="$(gx_tok "$GX_OUT" MIGVER)"
[ -n "$EB_PRE_FILE" ] || fatal "$GX_OUT" "E-bis: the store probe returned no answer (the snippet died before its first line; not a verdict)"
[ "$EB_PRE_FILE" = 1 ] || fatal "$GX_OUT" "no watermarks.db before the manifest step: the base image's daemon never created its store, so this rig cannot exercise the migrate"
say "before the manifest step: watermarks.db at version ${EB_PRE_VER:-? (migrate --check gave none)}"

# ── bring-up's manifest step, as align runs it ─────────────────────────
say "── smd admin uploader manifest --write --enable (from v3.70 it runs hs-uploader migrate before it touches the daemon)"
EB_MANIFEST=$(cat <<'UPLEOF'
mono(){ python3 -c 'import time; print(time.monotonic_ns() // 1000)'; }
echo "T0:$(mono)"
smd admin uploader manifest --write --enable
rc=$?
echo "T1:$(mono)"
exit $rc
UPLEOF
)
gx_ok 900 eb1-manifest "$EB_MANIFEST" "E-bis: smd admin uploader manifest --write --enable"
EB_RC="$GX_RC"
grep -vE '^T[01]:[0-9]+$' "$WORK/eb1-manifest.out" | sed 's/^/      /'
if grep -q 'Traceback (most recent call last)' "$WORK/eb1-manifest.out"; then
    fatal "$WORK/eb1-manifest.out" "smd admin uploader manifest --write --enable printed a Python traceback"
fi
[ "$EB_RC" -eq 0 ] || fatal "$WORK/eb1-manifest.out" "smd admin uploader manifest --write --enable exited $EB_RC; a failed migrate leaves the daemon as it stood and exits 1 (D10)"
EB_T0="$(gx_tok "$WORK/eb1-manifest.out" T0)"
EB_T1="$(gx_tok "$WORK/eb1-manifest.out" T1)"
[ -n "$EB_T0" ] && [ -n "$EB_T1" ] \
    || fatal "$WORK/eb1-manifest.out" "E-bis: no guest clock reading around the manifest step (python3 failed in the guest; not a verdict)"
say "manifest step exit 0; its migrate report: $(grep -m1 'watermarks\.db: ' "$WORK/eb1-manifest.out" || echo 'none printed')"

# ── the store after the manifest step, before anything restarts ────────
# Read here, ahead of any restart by this rig, a migrated store shows that
# the manifest step migrated it, not a daemon at its start (D10).
gx_ok 120 eb2-store "$UPL_STORE" "E-bis: the send-record store after the manifest step"
sed 's/^/      /' "$GX_OUT"
EB_FILE="$(gx_tok "$GX_OUT" WMFILE)"
EB_MRC="$(gx_tok "$GX_OUT" MIGRC)"
EB_VER="$(gx_tok "$GX_OUT" MIGVER)"
EB_PEND="$(gx_tok "$GX_OUT" MIGPEND)"
EB_NEW="$(gx_tok "$GX_OUT" MIGNEWER)"
EB_UV="$(gx_tok "$GX_OUT" USERVER)"
[ -n "$EB_FILE" ] && [ -n "$EB_MRC" ] && [ -n "$EB_PEND" ] && [ -n "$EB_NEW" ] \
    || fatal "$GX_OUT" "E-bis: the store probe returned no answer (not a verdict)"
[ "$EB_FILE" = 1 ] || fatal "$GX_OUT" "watermarks.db disappeared during the manifest step"
if [ "$EB_MRC" -ne 0 ]; then
    if grep -q "invalid choice: 'migrate'" "$GX_OUT"; then
        fatal "$GX_OUT" "the updated hs-uploader has no migrate command: $TARGET predates v3.70's hs-uploader, or the update never reached it"
    fi
    fatal "$GX_OUT" "hs-uploader migrate --check exited $EB_MRC"
fi
[ -n "$EB_VER" ] || fatal "$GX_OUT" "hs-uploader migrate --check printed no 'watermarks.db: version N' line"
[ "$EB_NEW" = 0 ] || fatal "$GX_OUT" "watermarks.db reads version $EB_VER, newer than the updated hs-uploader knows"
[ "$EB_PEND" = 0 ] || fatal "$GX_OUT" "watermarks.db reads version $EB_VER with $EB_PEND migration(s) still pending: the manifest step did not migrate it (D10)"
[ "$EB_VER" -ge 1 ] || fatal "$GX_OUT" "watermarks.db reads version $EB_VER with nothing pending: the updated hs-uploader knows no migration at all"
[ -n "$EB_UV" ] || fatal "$GX_OUT" "E-bis: PRAGMA user_version could not be read (no answer; not a verdict)"
[ "$EB_UV" = "$EB_VER" ] || fatal "$GX_OUT" "migrate --check reports version $EB_VER, but the file's PRAGMA user_version reads $EB_UV"
if [ "${EB_PRE_VER:-}" = "$EB_VER" ]; then
    say "watermarks.db at version $EB_VER, nothing pending; it stood there before the manifest step ✓"
else
    say "watermarks.db at version $EB_VER, nothing pending; the manifest step's migrate raised it from ${EB_PRE_VER:-an unversioned store} ✓"
fi

# ── the daemon starts after the migrate ────────────────────────────────
gx_ok 60 eb3-unit "$UPL_UNIT" "E-bis: hs-uploader after the manifest step"
EB_ACT="$(gx_tok "$GX_OUT" ACTIVE1)"
EB_START="$(gx_tok "$GX_OUT" START1)"
[ -n "$EB_ACT" ] && [ -n "$EB_START" ] || fatal "$GX_OUT" "E-bis: the hs-uploader probe returned no answer (not a verdict)"
[ "$EB_ACT" = 1 ] || fatal "$GX_OUT" "hs-uploader is not active after smd admin uploader manifest --enable"
if [ "$EB_START" -gt "$EB_T0" ]; then
    say "the manifest step itself started or restarted hs-uploader, after its migrate"
else
    # The manifest did not change, so --enable left the running daemon alone.
    say "the manifest step left hs-uploader running since before the update (its manifest did not change);"
    say "  restarting it, as smd align does for a daemon whose checkout moved after it started"
    gx_ok 120 eb4-restart 'systemctl restart hs-uploader.service' "E-bis: systemctl restart hs-uploader"
    [ "$GX_RC" -eq 0 ] || fatal "$GX_OUT" "systemctl restart hs-uploader.service exited $GX_RC: the updated daemon did not reach READY within its start timeout"
fi
gx_ok 180 eb5-unit "SETTLE=15
$UPL_UNIT" "E-bis: hs-uploader after a 15 s settle"
sed 's/^/      /' "$GX_OUT"
EB_A1="$(gx_tok "$GX_OUT" ACTIVE1)"; EB_S1="$(gx_tok "$GX_OUT" START1)"
EB_A2="$(gx_tok "$GX_OUT" ACTIVE2)"; EB_S2="$(gx_tok "$GX_OUT" START2)"
[ -n "$EB_A1" ] && [ -n "$EB_S1" ] && [ -n "$EB_A2" ] && [ -n "$EB_S2" ] \
    || fatal "$GX_OUT" "E-bis: the hs-uploader probe returned no answer (not a verdict)"
[ "$EB_A1" = 1 ] && [ "$EB_A2" = 1 ] \
    || fatal "$GX_OUT" "hs-uploader is not active on the migrated store (first read $EB_A1, 15 s later $EB_A2)"
[ "$EB_S1" = "$EB_S2" ] \
    || fatal "$GX_OUT" "hs-uploader restarted during the 15 s settle (start $EB_S1, then $EB_S2): the updated daemon dies after READY"
[ "$EB_S1" -gt "$EB_T0" ] \
    || fatal "$GX_OUT" "hs-uploader last started at $EB_S1 µs, before the manifest step began at $EB_T0 µs: the daemon running now never restarted after the migrate"
say "hs-uploader active, started $(( (EB_S1 - EB_T0) / 1000000 )) s after the manifest step began, and still up 15 s later ✓"

say "PHASE E-bis PASS — watermarks.db at version $EB_VER with nothing pending; hs-uploader restarted after the migrate and stayed up"
```

`gx_tok`, `UPL_STORE`, `UPL_UNIT` and `UPL_CLOCK` serve G-bis too, which runs later in the same file. The two
new phases add about two minutes of guest work: the manifest step, two 15 s settles and a handful of probes.
`U2_BUDGET_SEC` (2400 s) and its 300 s margin cover that, so leave the budget as it stands.

- [ ] **Step 4: `test-update-v3.sh`: PHASE G-bis**

(a) Find (line 968):

```bash
gx_ok 1800 g3-restore-apply "smd admin manifest restore $GMANIFEST --apply $NOFETCH" "manifest restore --apply"
```

and insert directly above it:

```bash
# PHASE G-bis orders the daemon's next start against this moment.
gx_ok 60 gb0-clock "$UPL_CLOCK" "G-bis: the guest's monotonic clock before the restore"
GB_T0="$(gx_tok "$GX_OUT" MONO)"
[ -n "$GB_T0" ] || fatal "$GX_OUT" "G-bis: no guest clock reading before the restore (python3 failed in the guest; not a verdict)"
```

(b) Find (line 1057):

```bash
say "PHASE G PASS — rolled back to the blessed baseline and proved it strictly"
```

and add directly below it, ahead of `say "UPDATE RIG COMPLETE — …"`:

```bash

# ════════════════════════════════════════════════════════════════════════
say "════ PHASE G-bis: the restored release re-renders its manifest, and its daemon runs on the migrated store ════"

# Restore moves checkouts only.  From v3.70 it then runs the restored
# checkout's smd as `admin uploader manifest --write --enable`, so a
# rolled-back daemon never reads pipelines a newer renderer wrote (D11).
# That write restarts hs-uploader only when the manifest changed, and v3.70
# changes none, so the daemon goes on running the code it started with.  Its
# next start, after a crash, a reboot or a later re-render, runs the restored
# code on the store E-bis migrated.  This phase makes that start happen now,
# as smd align would for a daemon whose checkout moved after it started, and
# proves it.  g4 above already showed every checkout at its blessed sha.

# ── restore printed its re-render line ─────────────────────────────────
# Restore re-renders after every verified --apply, even one that moved
# nothing, so a restore that exits 0 always prints this line.  _ok colours
# the tick ahead of the text; grep -o keeps the text alone.
GB_RR="$(grep -m1 -o 'pipelines\.toml re-rendered by the restored sigmond.*' "$WORK/g3-restore-apply.out")"
[ -n "$GB_RR" ] \
    || fatal "$WORK/g3-restore-apply.out" "restore --apply moved $MOVED component(s) but printed no re-render line: it left pipelines.toml as the newer sigmond rendered it (D11)"
say "restore: $GB_RR ✓"

# ── the restored smd reads the manifest as its own ─────────────────────
gx_ok 300 gb1-manifest-check 'smd admin uploader manifest' "G-bis: smd admin uploader manifest (check)"
sed 's/^/      /' "$GX_OUT"
[ "$GX_RC" -eq 0 ] || fatal "$GX_OUT" "the restored smd's manifest check exited $GX_RC: pipelines.toml is not what the restored sigmond renders (D11)"
grep -q 'is up to date' "$GX_OUT" \
    || fatal "$GX_OUT" "the restored smd's manifest check exited 0 without 'is up to date'; the check's contract changed"
say "the restored smd reads pipelines.toml as up to date ✓"

# ── the restored daemon starts, after the restore ──────────────────────
gx_ok 60 gb2-unit "$UPL_UNIT" "G-bis: hs-uploader after the restore"
GB_ACT="$(gx_tok "$GX_OUT" ACTIVE1)"
GB_START="$(gx_tok "$GX_OUT" START1)"
[ -n "$GB_ACT" ] && [ -n "$GB_START" ] || fatal "$GX_OUT" "G-bis: the hs-uploader probe returned no answer (not a verdict)"
[ "$GB_ACT" = 1 ] || fatal "$GX_OUT" "hs-uploader is not active after the restore and its re-render"
if [ "$GB_START" -gt "$GB_T0" ]; then
    say "the restore's re-render restarted hs-uploader itself"
else
    say "hs-uploader has run since before the restore (its manifest did not change); restarting it,"
    say "  as smd align does for a daemon whose checkout moved after it started"
    gx_ok 120 gb3-restart 'systemctl restart hs-uploader.service' "G-bis: systemctl restart hs-uploader"
    [ "$GX_RC" -eq 0 ] || fatal "$GX_OUT" "systemctl restart hs-uploader.service exited $GX_RC: the restored daemon did not reach READY on the migrated store"
fi
gx_ok 180 gb4-unit "SETTLE=15
$UPL_UNIT" "G-bis: hs-uploader after a 15 s settle"
sed 's/^/      /' "$GX_OUT"
GB_A1="$(gx_tok "$GX_OUT" ACTIVE1)"; GB_S1="$(gx_tok "$GX_OUT" START1)"
GB_A2="$(gx_tok "$GX_OUT" ACTIVE2)"; GB_S2="$(gx_tok "$GX_OUT" START2)"
[ -n "$GB_A1" ] && [ -n "$GB_S1" ] && [ -n "$GB_A2" ] && [ -n "$GB_S2" ] \
    || fatal "$GX_OUT" "G-bis: the hs-uploader probe returned no answer (not a verdict)"
[ "$GB_A1" = 1 ] && [ "$GB_A2" = 1 ] \
    || fatal "$GX_OUT" "the restored hs-uploader is not active (first read $GB_A1, 15 s later $GB_A2)"
[ "$GB_S1" = "$GB_S2" ] \
    || fatal "$GX_OUT" "the restored hs-uploader restarted during the 15 s settle (start $GB_S1, then $GB_S2): it dies after READY on the migrated store"
[ "$GB_S1" -gt "$GB_T0" ] \
    || fatal "$GX_OUT" "hs-uploader last started at $GB_S1 µs, before the restore began at $GB_T0 µs: the daemon running now is not the restored code"
say "the restored hs-uploader active, started after the restore, and still up 15 s later ✓"

# ── the store keeps the version E-bis gave it ──────────────────────────
# The restored release's hs-uploader may predate migrate, so read the number
# from the file.  A rollback never lowers it: a later roll forward must not
# repeat a migration it already ran.
gx_ok 120 gb5-store "$UPL_STORE" "G-bis: the send-record store under the restored daemon"
sed 's/^/      /' "$GX_OUT"
GB_UV="$(gx_tok "$GX_OUT" USERVER)"
[ -n "$GB_UV" ] || fatal "$GX_OUT" "G-bis: PRAGMA user_version could not be read (no answer; not a verdict)"
[ "$GB_UV" = "$EB_VER" ] \
    || fatal "$GX_OUT" "watermarks.db reads version $GB_UV under the restored daemon, but E-bis left it at $EB_VER: the rollback changed the store's version"
say "watermarks.db still at version $GB_UV under the restored daemon ✓"

say "PHASE G-bis PASS — the restored release re-rendered its manifest, and its daemon runs on the version-$GB_UV store"
```

- [ ] **Step 5: `test-nested-v3.sh`: the v3.70 sink foundation**

(a) In the bring-up timeout branch, find (line 881):

```bash
    [ "${SINKWIZ:-0}" = 1 ] && sink_unchecked "the manifest banner, per-pipeline discard, wspr-recorder's in-process sender and smd sink upload (bring-up did not finish)"
```

and add directly below it:

```bash
    say "  ⚠ THE v3.70 SINK FOUNDATION CHECKS WERE NOT EVALUATED either."
    sink_unchecked "the v3.70 sink foundation: send-record version, psk-recorder's sink writer, sink.db columns (bring-up did not finish)"
```

(b) Find the end of the bring-up branch (lines 1010-1015):

```bash
            fi ;;
        esac
    fi
fi

# What the site sink checks left unchecked, said once more beside the verdict.
```

and insert the block below between its `    fi` (line 1012) and its `fi` (line 1013). It runs after `smd sink upload`,
inside the branch that only a completed bring-up reaches. The heredoc body and its `F70EOF` line sit at column 0,
as bash requires.

```bash

    # ── the v3.70 sink foundation ───────────────────────────────────────────
    # Three facts about the image's sink code (tasks/plan-sink-control.md §10.4):
    #   1. bring-up ran `hs-uploader migrate`, and watermarks.db now sits at
    #      this release's schema version with nothing pending (D10);
    #   2. psk-recorder's venv writes through hs-uploader's writer, not
    #      sigmond's bundled copy (D14);
    #   3. a sink.db that holds pending_uploads carries producer and local (D12).
    # This runs after `smd sink upload`, whose manifest write runs migrate too.
    # On a fresh install bring-up's migrate can meet no watermarks.db at all:
    # the daemon creates its store on its first start, after that migrate, and
    # migrate never creates one.  The bring-up log says which happened.
    #
    # test-update-v3.sh runs these phases on the PREVIOUS blessed image, whose
    # hs-uploader has no `migrate`.  Only that run, with SINK_BASE_IMAGE=1, may
    # skip these checks; anywhere else such an image FATALs, for the reason the
    # SINKWIZ gate gives.  One KEY:N token per fact, as above.
    say "── v3.70 sink foundation: the send-record version, psk-recorder's sink writer, sink.db's columns"
    F70SCRIPT=$(cat <<'F70EOF'
set -u
export PYTHONDONTWRITEBYTECODE=1
EXE=/opt/hs-uploader/venv/bin/hs-uploader
W=/var/lib/hs-uploader/watermarks.db
L=/var/log/sigmond/firstrun-bringup.log
PSKPY=/opt/git/sigmond/psk-recorder/venv/bin/python3
S=/var/lib/sigmond/sink.db
[ -x "$EXE" ] || { echo "HSUEXE:0"; exit 0; }
echo "HSUEXE:1"
# 1. The send-record version, read as hsupload (the store's owner and the
#    daemon's user) from the unit's working directory.  An hs-uploader that
#    predates v3.70 answers "invalid choice: 'migrate'".
o=$(cd /opt/hs-uploader && runuser -u hsupload -- /usr/bin/env PYTHONDONTWRITEBYTECODE=1 "$EXE" migrate --check --db "$W" 2>&1)
rc=$?
printf '%s\n' "$o" | head -6 | sed 's/^/migrate-check| /'
if printf '%s\n' "$o" | grep -q "invalid choice: 'migrate'"; then echo "MIGCLI:0"; exit 0; fi
echo "MIGCLI:1"
echo "MIGRC:$rc"
echo "MIGVER:$(printf '%s\n' "$o" | sed -n 's/^watermarks\.db: version \([0-9][0-9]*\)$/\1/p' | head -1)"
echo "MIGPEND:$(printf '%s\n' "$o" | grep -c '^  pending ')"
echo "MIGNEWER:$(printf '%s\n' "$o" | grep -c 'newer than this hs-uploader')"
echo "MIGNOSTORE:$(printf '%s\n' "$o" | grep -c '^watermarks\.db: not found at ')"
# What bring-up's own migrate reported.  Bring-up's manifest step prints
# migrate's lines, and firstrun-bringup.log takes every step's output.
if [ -r "$L" ]; then
    echo "BULOG:1"
    echo "BULOGV:$(grep -c 'watermarks\.db: version [0-9]' "$L")"
    echo "BULOGNF:$(grep -c 'watermarks\.db: not found at ' "$L")"
else
    echo "BULOG:0"
fi
# 2. The writer psk-recorder's venv imports.  -B: write no bytecode into the
#    checkouts as root.
if [ -x "$PSKPY" ]; then
    echo "PSKVENV:1"
    io=$(cd / && "$PSKPY" -B -c "import sigmond.hamsci_sink as s; print(s.SINK_IMPL)" 2>&1)
    echo "IMPLRC:$?"
    echo "IMPLHS:$(printf '%s\n' "$io" | grep -cx 'hs_uploader')"
    echo "IMPLBUN:$(printf '%s\n' "$io" | grep -cx 'bundled')"
    printf '%s\n' "$io" | tail -3 | sed 's/^/sink-impl| /'
else
    echo "PSKVENV:0"
fi
# 3. sink.db's columns, read-only.  Bring-up creates sink.db as an empty
#    file; pending_uploads appears only once a writer opens it.
if [ -e "$S" ]; then
    echo "SINKDB:1"
    python3 -B - "$S" 2>&1 <<'PY'
import pathlib, sqlite3, sys
try:
    p = pathlib.Path(sys.argv[1]).resolve()
    c = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=10)
    n = c.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table' "
                  "AND name = 'pending_uploads'").fetchone()[0]
    print("SINKTAB:%d" % (1 if n else 0))
    if n:
        cols = {r[1] for r in c.execute("PRAGMA table_info(pending_uploads)")}
        print("SINKPROD:%d" % ("producer" in cols))
        print("SINKLOCAL:%d" % ("local" in cols))
except Exception as exc:
    print("sink-read| %s: %s" % (type(exc).__name__, exc))
PY
else
    echo "SINKDB:0"
fi
exit 0
F70EOF
)
    F70=$($SSHN "qm guest exec $VMID --timeout 120 -- bash -lc \"echo $(printf '%s\n' "$F70SCRIPT" | base64 -w0) | base64 -d > /tmp/sig-f70.sh; bash /tmp/sig-f70.sh\"" 2>&1)
    echo "$F70" | tail -12
    _f70(){ echo "$F70" | grep -oE "$1:[0-9]+" | head -1 | cut -d: -f2; }
    _hx=$(_f70 HSUEXE); _mcli=$(_f70 MIGCLI)
    [ -n "$_hx" ] || { say "FATAL: the v3.70 sink foundation probe returned no answer (guest exec failed, not a verdict)"; echo "$F70" | head -6; exit 1; }
    [ "$_hx" = 1 ] || { say "FATAL: no /opt/hs-uploader/venv/bin/hs-uploader after a completed bring-up"; exit 1; }
    [ -n "$_mcli" ] || { say "FATAL: the hs-uploader migrate probe returned no answer (not a verdict)"; echo "$F70" | head -6; exit 1; }
    if [ "$_mcli" = 0 ]; then
        if [ "${SINK_BASE_IMAGE:-0}" != 1 ]; then
            say "FATAL: the image under test has no hs-uploader migrate (its hs-uploader predates v3.70)"
            say "  only the update rig's base image may lack it; test-update-v3.sh sets SINK_BASE_IMAGE=1 for that run"
            exit 1
        fi
        say "⚠ v3.70 SINK FOUNDATION ASSERTIONS NOT EVALUATED — this image's hs-uploader has no"
        say "  migrate (pre-v3.70); expected only on the update rig's base image"
        sink_unchecked "the v3.70 sink foundation (this image's hs-uploader has no migrate; it predates v3.70)"
    else
        # 1. the send-record version, and bring-up's own migrate
        _mrc=$(_f70 MIGRC); _mver=$(_f70 MIGVER); _mpend=$(_f70 MIGPEND)
        _mnew=$(_f70 MIGNEWER); _mnone=$(_f70 MIGNOSTORE)
        _bl=$(_f70 BULOG); _blv=$(_f70 BULOGV); _blnf=$(_f70 BULOGNF)
        [ -n "$_mrc" ] && [ -n "$_mpend" ] && [ -n "$_mnew" ] && [ -n "$_mnone" ] && [ -n "$_bl" ] \
            || { say "FATAL: the send-record version probe returned no answer (not a verdict)"; echo "$F70" | head -6; exit 1; }
        [ "$_mrc" = 0 ] || { say "FATAL: hs-uploader migrate --check exited $_mrc"; echo "$F70" | head -6; exit 1; }
        if [ "$_bl" = 1 ]; then
            [ -n "$_blv" ] && [ -n "$_blnf" ] \
                || { say "FATAL: the bring-up log probe returned no answer (not a verdict)"; exit 1; }
            if [ "$_blv" -ge 1 ]; then
                say "bring-up's manifest step ran hs-uploader migrate on the store ✓"
            elif [ "$_blnf" -ge 1 ]; then
                say "bring-up's manifest step ran hs-uploader migrate before the daemon had created its store ✓"
            else
                say "FATAL: firstrun-bringup.log carries no 'watermarks.db:' line: bring-up never ran hs-uploader migrate (D10)"; exit 1
            fi
        else
            say "WARN: no firstrun-bringup.log in the VM; bring-up's own migrate was NOT evaluated"
            sink_unchecked "bring-up's own hs-uploader migrate (no firstrun-bringup.log)"
        fi
        # Did a manifest write run migrate after bring-up?  smd sink upload's
        # did, unless it refused while packaging ran.
        _later=0
        if [ "${SINKWIZ:-0}" = 1 ]; then
            case "${UP:-}" in *"refusing while packaging runs"*) ;; *) _later=1 ;; esac
        fi
        if [ "$_mnone" -ge 1 ]; then
            say "WARN: no watermarks.db: the daemon has not created its store, so the send-record version was NOT evaluated"
            sink_unchecked "the send-record version (no watermarks.db: the daemon has not created its store)"
        elif [ -z "$_mver" ]; then
            say "FATAL: hs-uploader migrate --check printed no 'watermarks.db: version N' line"; echo "$F70" | head -6; exit 1
        elif [ "$_mnew" -ge 1 ]; then
            say "FATAL: watermarks.db reads version $_mver, newer than this image's hs-uploader knows"; exit 1
        elif [ "$_mpend" = 0 ] && [ "$_mver" -ge 1 ]; then
            say "watermarks.db at version $_mver with nothing pending (hs-uploader migrate --check) ✓"
        elif [ "$_later" = 0 ] && [ "$_bl" = 0 ]; then
            say "WARN: watermarks.db reads version $_mver with $_mpend migration(s) pending.  No bring-up log"
            say "  says why, and smd sink upload ran no migrate, so the version was NOT evaluated"
            sink_unchecked "the send-record version (no bring-up log, and smd sink upload ran no migrate)"
        elif [ "$_later" = 0 ] && [ "$_blv" = 0 ] && [ "$_blnf" -ge 1 ]; then
            say "WARN: watermarks.db reads version $_mver with $_mpend migration(s) pending.  Bring-up's migrate"
            say "  ran before the daemon created the store, and smd sink upload ran no migrate, so the version"
            say "  was NOT evaluated"
            sink_unchecked "the send-record version (bring-up's migrate ran before the daemon created watermarks.db, and smd sink upload ran no migrate)"
        else
            say "FATAL: watermarks.db reads version $_mver with $_mpend migration(s) pending; neither bring-up"
            say "  nor a later manifest write brought it to this release's version (D10)"; exit 1
        fi
        # 2. psk-recorder's sink writer
        _pv=$(_f70 PSKVENV)
        [ -n "$_pv" ] || { say "FATAL: the psk-recorder sink writer probe returned no answer (not a verdict)"; exit 1; }
        if [ "$_pv" = 0 ]; then
            say "WARN: no psk-recorder venv in this nest; its sink writer was NOT evaluated"
            sink_unchecked "psk-recorder's sink writer (no psk-recorder venv in this nest)"
        else
            _irc=$(_f70 IMPLRC); _ihs=$(_f70 IMPLHS); _ibn=$(_f70 IMPLBUN)
            [ -n "$_irc" ] && [ -n "$_ihs" ] && [ -n "$_ibn" ] \
                || { say "FATAL: the psk-recorder sink writer probe returned no answer (not a verdict)"; exit 1; }
            [ "$_irc" = 0 ] || { say "FATAL: importing sigmond.hamsci_sink in psk-recorder's venv exited $_irc"; echo "$F70" | head -6; exit 1; }
            if [ "$_ihs" = 1 ]; then
                say "psk-recorder's venv writes through hs_uploader.sink (SINK_IMPL = hs_uploader) ✓"
            elif [ "$_ibn" = 1 ]; then
                say "FATAL: psk-recorder's venv fell back to sigmond's bundled writer (SINK_IMPL = bundled):"
                say "  the hs-uploader in that venv has no hs_uploader.sink"; exit 1
            else
                say "FATAL: sigmond.hamsci_sink.SINK_IMPL printed neither hs_uploader nor bundled"; echo "$F70" | head -6; exit 1
            fi
        fi
        # 3. sink.db's columns
        _sdb=$(_f70 SINKDB)
        [ -n "$_sdb" ] || { say "FATAL: the sink.db probe returned no answer (not a verdict)"; exit 1; }
        if [ "$_sdb" = 0 ]; then
            say "WARN: no /var/lib/sigmond/sink.db; its producer and local columns were NOT evaluated"
            sink_unchecked "sink.db's producer and local columns (no /var/lib/sigmond/sink.db)"
        else
            _stab=$(_f70 SINKTAB)
            [ -n "$_stab" ] || { say "FATAL: the sink.db probe could not read the file (not a verdict)"; echo "$F70" | head -6; exit 1; }
            if [ "$_stab" = 0 ]; then
                say "WARN: no writer has opened sink.db in this nest (it has no RX888), so it holds no pending_uploads;"
                say "  its producer and local columns were NOT evaluated"
                sink_unchecked "sink.db's producer and local columns (no writer has created pending_uploads in this nest)"
            else
                _sprod=$(_f70 SINKPROD); _sloc=$(_f70 SINKLOCAL)
                [ -n "$_sprod" ] && [ -n "$_sloc" ] \
                    || { say "FATAL: the sink.db column probe returned no answer (not a verdict)"; exit 1; }
                [ "$_sprod" = 1 ] && [ "$_sloc" = 1 ] \
                    || { say "FATAL: sink.db's pending_uploads lacks producer ($_sprod) or local ($_sloc)"; exit 1; }
                say "sink.db's pending_uploads carries producer and local ✓"
            fi
        fi
    fi
```

- [ ] **Step 6: Syntax, the existing checks, and the harness (GREEN)**

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
bash -n test-update-v3.sh && bash -n test-nested-v3.sh && echo "bash -n OK"
bash test-phase-d-verdict.sh | tail -1
git diff -U0 | grep '^-[^-]'
for f in test-update-v3.sh test-nested-v3.sh; do
    echo "== $f"; diff <(git show HEAD:$f | grep -oE 'say "[^"]*PASS[^"]*"') <(grep -oE 'say "[^"]*PASS[^"]*"' $f)
done
```

Expected: `bash -n OK`, then `PASS: phase_d_verdict`, then the one line this task rewrites,
`-    # sink switch; without it the sibling fails any image whose wizard lacks it.`, then the PASS strings: two
lines added in the update rig and none changed.

```
== test-update-v3.sh
3a4
> say "PHASE E-bis PASS — watermarks.db at version $EB_VER with nothing pending; hs-uploader restarted after the migrate and stayed up"
5a7
> say "PHASE G-bis PASS — the restored release re-rendered its manifest, and its daemon runs on the version-$GB_UV store"
== test-nested-v3.sh
```

Run: `"$SCR/run.sh" /home/mjh/hamsci/repos/sigmond-appliance; echo "exit=$?"` (about 40 s).

Expected (GREEN). Lines past 132 characters end in `…` here, and the µs values differ from run to run:
```
happy        ok        PASS
restarts     ok        PASS
calendar     ok        PASS
nomigrate    ok        FATAL: watermarks.db reads version 0 with 1 migration(s) still pending: the manifest step did not migrate …
smdfail      ok        FATAL: smd admin uploader manifest --write --enable exited 1; a failed migrate leaves the daemon as it sto…
restartnoop  ok        FATAL: hs-uploader last started at 4557669592235 µs, before the manifest step began at 4558669761865 µs: t…
settlecrash  ok        FATAL: hs-uploader restarted during the 15 s settle (start 4558670911157, then 4558675911157): the updated…
restartfail  ok        FATAL: systemctl restart hs-uploader.service exited 1: the updated daemon did not reach READY within its s…
oldtarget    ok        FATAL: the updated hs-uploader has no migrate command: origin/main predates v3.70's hs-uploader, or the up…
liar         ok        FATAL: migrate --check reports version 1, but the file's PRAGMA user_version reads 0
nostore      ok        FATAL: no watermarks.db before the manifest step: the base image's daemon never created its store, so this…
newer        ok        FATAL: watermarks.db reads version 5, newer than the updated hs-uploader knows
inactive     ok        FATAL: hs-uploader is not active after smd admin uploader manifest --enable
traceback    ok        FATAL: smd admin uploader manifest --write --enable printed a Python traceback
nohsupload   ok        FATAL: smd admin uploader manifest --write --enable exited 1; a failed migrate leaves the daemon as it sto…
pend2        ok        FATAL: watermarks.db reads version 1 with 1 migration(s) still pending: the manifest step did not migrate …
dead_eb2     ok        FATAL: E-bis: the send-record store after the manifest step: the guest-exec channel failed (agent wedged? …
norerender   ok        FATAL: restore --apply moved 3 component(s) but printed no re-render line: it left pipelines.toml as the n…
calnorr      ok        FATAL: restore --apply moved 0 component(s) but printed no re-render line: it left pipelines.toml as the n…
gdrift       ok        FATAL: the restored smd's manifest check exited 1: pipelines.toml is not what the restored sigmond renders…
glower       ok        FATAL: watermarks.db reads version 0 under the restored daemon, but E-bis left it at 1: the rollback chang…
ginactive    ok        FATAL: hs-uploader is not active after the restore and its re-render
dead_gb      ok        FATAL: G-bis: hs-uploader after the restore: the guest-exec channel failed (agent wedged? try: qm agent 10…
SUMMARY: 23 as expected, 0 mismatched
happy_fresh  ok        PASS+sink.db's producer and local columns (no writer has created pending_uploads in this nest)
happy_full   ok        PASS
v0_refused   ok        PASS+the send-record version (bring-up's migrate ran before the daemon created watermarks.db, and smd sink…
v0_later     ok        FATAL: watermarks.db reads version 0 with 1 migration(s) pending; neither bring-up
v0_logv1     ok        FATAL: watermarks.db reads version 0 with 1 migration(s) pending; neither bring-up
nolog_line   ok        FATAL: firstrun-bringup.log carries no 'watermarks.db:' line: bring-up never ran hs-uploader migrate (D10)
base_skip    ok        PASS+the v3.70 sink foundation (this image's hs-uploader has no migrate; it predates v3.70)
base_fatal   ok        FATAL: the image under test has no hs-uploader migrate (its hs-uploader predates v3.70)
bundled      ok        FATAL: psk-recorder's venv fell back to sigmond's bundled writer (SINK_IMPL = bundled):
noimpl       ok        FATAL: importing sigmond.hamsci_sink in psk-recorder's venv exited 1
nopsk        ok        PASS+psk-recorder's sink writer (no psk-recorder venv in this nest); sink.db's producer and local columns …
oldsink      ok        FATAL: sink.db's pending_uploads lacks producer (0) or local (0)
badsink      ok        FATAL: the sink.db probe could not read the file (not a verdict)
nosink       ok        PASS+sink.db's producer and local columns (no /var/lib/sigmond/sink.db)
nostore      ok        PASS+the send-record version (no watermarks.db: the daemon has not created its store); sink.db's producer …
newer        ok        FATAL: watermarks.db reads version 5, newer than this image's hs-uploader knows
dead         ok        FATAL: the v3.70 sink foundation probe returned no answer (guest exec failed, not a verdict)
noexe        ok        FATAL: no /opt/hs-uploader/venv/bin/hs-uploader after a completed bring-up
nohsupload   ok        FATAL: hs-uploader migrate --check exited 1
nobulog      ok        PASS+bring-up's own hs-uploader migrate (no firstrun-bringup.log); sink.db's producer and local columns (n…
nobulog_v0   ok        PASS+bring-up's own hs-uploader migrate (no firstrun-bringup.log); the send-record version (no bring-up lo…
sinkwiz0_v0  ok        PASS+the send-record version (bring-up's migrate ran before the daemon created watermarks.db, and smd sink…
SUMMARY: 22 as expected, 0 mismatched
MU1 restartnoop  real=E-bis FATAL              mutant=E-bis PASS, G-bis FATAL  CAUGHT
MU2 settlecrash  real=E-bis FATAL              mutant=G-bis PASS               CAUGHT
MU3 norerender   real=E-bis PASS, G-bis FATAL  mutant=G-bis PASS               CAUGHT
MU4 glower       real=E-bis PASS, G-bis FATAL  mutant=G-bis PASS               CAUGHT
MU5 pend2        real=E-bis FATAL              mutant=G-bis PASS               CAUGHT
MN1 v0_later     real=FATAL                    mutant=PASS+NE                  CAUGHT
MN2 happy_full   real=PASS                     mutant=FATAL                    CAUGHT
MN3 oldsink      real=FATAL                    mutant=PASS                     CAUGHT
MN4 base_fatal   real=FATAL                    mutant=PASS+NE                  CAUGHT
MN5 happy_full   real=PASS                     mutant=FATAL                    CAUGHT
MUTATIONS: 10 caught, 0 not caught
exit=0
```

Every case reaches the verdict it names. `happy`, `restarts` and `calendar` reach `PHASE G-bis PASS`: the rig
restarts the daemon itself, the product restarts it, and a restore that moved nothing. A restore re-renders
even when it moved nothing (Task 8), so `calnorr`, which moved nothing and printed no re-render line, FATALs.
Each FATAL names its cause, and a dead guest agent never reads as a verdict. In the nested run, `happy_fresh` shows what a real nest
prints: bring-up met no store, `smd sink upload` migrated it, and `sink.db`'s columns stay NOT EVALUATED because
no writer runs without an RX888. Each mutation removes one check, and its case changes verdict.

- [ ] **Step 7: Commit**

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
git add test-update-v3.sh test-nested-v3.sh
git commit -m "test-update, test-nested: the rigs prove v3.70's migrate, restart and rollback

PHASE E ran smd update --apply, and PHASE G smd admin manifest restore
--apply.  Neither re-renders pipelines.toml or restarts hs-uploader, so
the v3.70 daemon never started in E, and the restored daemon never met
the migrated store in G.

PHASE E-bis runs what smd align runs once the code has moved: smd admin
uploader manifest --write --enable, which runs hs-uploader migrate first,
then a restart of hs-uploader when that step left the old daemon up.
watermarks.db must read its release's version with nothing pending, by
migrate --check and by PRAGMA user_version.  The daemon must start after
the manifest step began and stay up 15 s.

PHASE G-bis checks restore's re-render line and the restored smd's
manifest check.  The restored daemon must start on the migrated store
and leave its version alone.

test-nested-v3.sh checks, after smd sink upload, that bring-up ran
migrate, that watermarks.db carries this release's version, that
psk-recorder's venv imports hs_uploader.sink, and that a sink.db holding
pending_uploads carries producer and local.  A check the nest cannot
conclude adds a NOT EVALUATED phrase.  Every existing PASS string stays
as it was.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ggy9DMZNG1dDX23dJuxprN"
graphify update /home/mjh/hamsci/repos
```

---


### Task 10: prove, build, test and bless v3.70 (each external step waits for Michael's go)

Nothing here is code. Each step reaches outside the devbox, so each one waits for Michael's explicit go. Every
station step starts with a claude-bus read and post, and for B4 also a read of `/etc/sigmond/CLAUDE-COORDINATION.md`
on the VM (with the hyphen).

`smd align` reads GitHub Releases, so an unblessed v3.70 is not alignable. Before the bless, stations get the new
code by `smd update --apply`, which pulls and runs each `install.sh` but never re-runs bring-up. That matters on B4:
bring-up would move its several psk-recorder instances to `server-raw`, and the spec keeps B4 on its present
settings until step 4.

- [x] **Step 1: The neutrality proof on real data** (read-only; bus post first).
  Take read-only snapshots of `/var/lib/sigmond/sink.db`, `/var/lib/hs-uploader/watermarks.db` and
  `/etc/hs-uploader/pipelines.toml` from ND and B4. Size each file first, and copy nothing over 2 GB without
  Michael's say-so. On each station, as the operator account through its Proxmox host:
  ```bash
  ssh ac0gndpm "ssh hamsci@10.99.0.2 'ls -la /var/lib/sigmond/sink.db /var/lib/hs-uploader/watermarks.db'"
  ssh ac0gndpm "ssh hamsci@10.99.0.2 'cd /tmp && sudo -n nice -n 19 sqlite3 -readonly /var/lib/sigmond/sink.db \".backup /tmp/nd-sink.db\" && sudo -n nice -n 19 sqlite3 -readonly /var/lib/hs-uploader/watermarks.db \".backup /tmp/nd-wm.db\" && sudo -n cp /etc/hs-uploader/pipelines.toml /tmp/nd-pipelines.toml && sudo -n chown hamsci /tmp/nd-*'"
  ```
  Copy the three files to the devbox scratchpad, then delete them from the station's `/tmp`. B4 uses `b4pm` in
  place of `ac0gndpm` and the prefix `b4-`. Then extract OLD (`git -C hs-uploader archive 3e97223 src | tar -x
  -C $SCR/old`) and NEW (the v3.70 head, the same way), and run Task 5's tool against each station's copies:
  ```bash
  .venv/bin/python tools/neutrality_check.py --old $SCR/old/src --new $SCR/new/src \
      --manifest $SCR/nd-pipelines.toml --sink $SCR/nd-sink.db --watermarks $SCR/nd-wm.db
  ```
  Expected: every pipeline prints `agree`, and the tool exits 0. Then make a second copy of each station's
  `sink.db`, let NEW's writer add its columns to it (`.venv/bin/python -c "import sqlite3, hs_uploader.sink as
  h; c = sqlite3.connect('$SCR/nd-sink-ext.db'); print(h.ensure_columns(c)); c.commit()"`), and run the tool again
  with `--sink $SCR/nd-sink-ext.db`. It must agree again: OLD's readers must select the same rows from a sink that
  carries `producer` and `local`, as a station rolled back to v3.69 would. Also run `hs-uploader migrate --check --db
  $SCR/nd-wm.db` with NEW: it reports version 0 with migration 1 pending, and the file stays byte-identical
  (`sha256sum` before and after). Record both stations' results in spec §13 as "Task 10 Step 1".
- [x] **Step 2: Full suites green** in hs-uploader, sigmond and sigmond-appliance (the runners and baselines in
  Global Constraints, plus each task's additions).
- [ ] **Step 3: Push** hs-uploader, sigmond and sigmond-appliance `main` (Michael's go). The golden VM clones each
  repo's latest `main` and fails when one sits behind its remote.
- [ ] **Step 4: Before tagging, run the pre-tag adversarial hunt** that caught v3.69's blocker. Look for anything
  that breaks the image build, a first boot, the rigs, or an existing station on `smd update`. Then tag `v3.70`
  in sigmond-appliance (`git tag -a v3.70`, push the tag) and build on the rig (root@192.168.1.182): pull both
  checkouts, `./sync-rig.sh`, `./build-golden-vm.sh`, `./build-usb-v3.sh --release`.
- [ ] **Step 5: Nested test** — `./test-nested-v3.sh` on the rig. Phase D must print
  `PHASE D PASS — NESTED TEST COMPLETE` and Task 9's new lines: `hs-uploader migrate` at version 1, the psk-recorder
  venv's `SINK_IMPL` reading `hs_uploader`. The nest has no RX888, so no writer creates `pending_uploads`; expect
  the columns check inside the closing `⚠ SITE SINK CHECKS NOT EVALUATED` line, as
  `sink.db's producer and local columns (no writer has created pending_uploads in this nest)`.
- [ ] **Step 5b: Update test** — `./test-update-v3.sh --image sigmond-appliance-v3.69-20261007-release.img`. It rolls
  blessed v3.69 forward to `main` and back. PHASE E, E-bis, F, G and G-bis must each print their PASS line.
- [ ] **Step 6: B4 overnight** (bus first, coordination doc first). Run `smd update --apply` on B4. Then run
  `smd admin uploader manifest --write --enable`, which runs `hs-uploader migrate`. v3.70 changes no manifest, so
  that command does not restart the daemon: run `sudo systemctl restart hs-uploader` yourself. Then restart each
  recorder alone with `sudo systemctl restart '<unit>'` (never `smd restart`, which also restarts radiod). Record `hs-uploader migrate --check` and `systemctl show hs-uploader -p ActiveEnterTimestamp -p NRestarts`.
  Next morning, after 05:00 UTC, compare B4's overnight spot counts on wd30 and pskreporter.info with the night
  before. Count the new forward-check WARNINGs per key in EVERY journal that writes send records: hs-uploader,
  and on B4 each `psk-recorder@`, `meteor-scatter@` and `wspr-recorder@` unit that runs an in-process sender. Each
  key warns once, then once an hour with a count, so add up the counts. The total measures how often a shared key
  ran backward, and it feeds v3.71. Treat it as a lower bound: each process keeps its windows in memory, so
  a restart drops its uncounted repeats. Confirm GRAPE and the magnetometer shipped their day.
- [ ] **Step 7: ND** — the same as Step 6 on ND, after B4's night passes, including the explicit
  `sudo systemctl restart hs-uploader`. ND runs no in-process sender, so only hs-uploader's journal can warn there.
- [ ] **Step 8: Bless** — `./bless-release.sh v3.70`, read its gates, then Michael runs `--apply` at a terminal on
  the rig.
- [ ] **Step 9: Update the operator install pages** (standing policy, `sigmond-appliance/docs/RELEASE.md` §3).
  `INSTALL.md` already reads `Status: current` from Step 7's hardware run; check its "Verified against" line names
  the v3.70 image commit, sha256 and the B4/ND runs. Then rebuild the shared page "Installing a Sigmond Station"
  (https://claude.ai/artifact/WvLUettP4ViRFYu6sftctf) from that `INSTALL.md`: version, file names, and every step
  v3.70 changed. v3.70 changes no operator step, so expect only the version, file names and footer to move.



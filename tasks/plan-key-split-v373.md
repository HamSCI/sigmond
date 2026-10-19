# The send-record key split (v3.73 row 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split hs-uploader's shared `psk-pskreporter` pipeline into one pipeline and one send record per producer, migrate the old record safely, and enforce forward-only send records.

**Architecture:** hs-uploader learns producer- and instance-scoped SQLite sources, a pipeline's client, a store version stamped at birth, migration 2 with a repeatable retry pass, and checked cursor writes. sigmond renders the split from the shared client block, decides D13 at render time, and reorders the manifest step to stop, migrate, then land the manifest.

**Tech Stack:** Python 3.11; SQLite (`sqlite3`, `json_extract`, `PRAGMA user_version`); pytest; sigmond core stays stdlib-only.

**Spec:** `sigmond/tasks/spec-key-split-v373.md` (decisions: `plan-sink-control.md` D9, D10, D13, D17).

## Global Constraints

- Work on `main` in each repo; push fast-forward only.  Commit trailers, exactly:
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01H4HYgCZ3dRtK4XWA1Xyoc3`.
- Every new test must be watched failing before the change; the commit message names the mutation.
- Runners: hs-uploader `.venv/bin/python -m pytest -q`; sigmond `.venv/bin/pytest -q`.
- Names, verbatim: pipelines `psk-recorder-pskreporter` (modes `ft8`, `ft4`) and
  `meteor-scatter-pskreporter` (mode `msk144`); producers `psk-recorder`, `meteor-scatter`;
  source parts `sqlite:psk.spots@psk-recorder`, `sqlite:psk.spots@meteor-scatter`, instance form
  `sqlite:<db>.<table>@<producer>/<radiod_id>`; old pipeline `psk-pskreporter`, old source part
  `sqlite:psk.spots`.  A pskreporter destination is a `dest_id` that starts with `pskreporter`.
- Store version after this plan: `SCHEMA_VERSION = 2`.
- D12 in SQL: `producer = ''` rows belong to meteor-scatter when `lower(trim(mode)) = 'msk144'`,
  else to psk-recorder (missing mode included).
- Never touch a station.  No client repository changes.  Do not edit `sigmond/tasks/` except this
  plan's ledger.

## Review Focus

1. A store the daemon created after an operator deleted `watermarks.db` → reads version 2, both pskreporter pipelines run (Task 1).
2. A `sink.db` whose rows carry `producer = ''` with modes `' MSK144'` or `NULL` → each row goes to exactly one pipeline (Task 2).
3. `--write` without `--enable` while the daemon runs → daemon stopped for the migrate and running again after (Task 10).
4. Capability probe times out → manifest left untouched, exit non-zero (Task 8).
5. A meteor-scatter-only host with a queued `psk-pskreporter` retry and no sink → retry moves to `meteor-scatter-pskreporter` (Task 5).

---

### Task 1: The store's version, from birth

**Files:**
- Modify: `hs-uploader/src/hs_uploader/watermark/sqlite.py` (`__init__`, `_init_schema`)
- Modify: `hs-uploader/src/hs_uploader/watermark/schema.py` (`SCHEMA_VERSION`, `migrate` on a missing or zero-length file)
- Modify: `hs-uploader/src/hs_uploader/cli.py:286-310` (`_cmd_migrate`)
- Test: `hs-uploader/tests/test_watermark_schema.py`

**Interfaces:**
- Produces: `schema.SCHEMA_VERSION: int` (2 after Task 5; 1 until then); `SqliteWatermarkStore` stamps it on a file that held no tables when opened.

- [ ] **Step 1: Write the failing tests**

```python
def test_new_store_is_stamped(tmp_path):
    p = tmp_path / "w.db"; SqliteWatermarkStore(p).close()
    assert _user_version(p) == schema.SCHEMA_VERSION

def test_zero_length_store_is_stamped(tmp_path):
    p = tmp_path / "w.db"; p.write_bytes(b"")
    SqliteWatermarkStore(p).close()
    assert _user_version(p) == schema.SCHEMA_VERSION

def test_old_store_with_tables_is_not_stamped(tmp_path):
    p = _store_at_version(tmp_path, 0, with_rows=True)   # tables, rows, user_version 0
    SqliteWatermarkStore(p).close()
    assert _user_version(p) == 0                          # migrate must still run

def test_migrate_creates_missing_store_when_not_root(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    rep = schema.migrate(str(tmp_path / "w.db"))
    assert rep.to_version == schema.SCHEMA_VERSION

def test_migrate_as_root_still_refuses_missing_store(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(FileNotFoundError): schema.migrate(str(tmp_path / "w.db"))
```

- [ ] **Step 2: Run them and see four fail** — `.venv/bin/python -m pytest -q tests/test_watermark_schema.py`
- [ ] **Step 3: Implement.** In `__init__`, read `SELECT count(*) FROM sqlite_master` before `_init_schema`; when it reads 0, set `PRAGMA user_version = SCHEMA_VERSION` after the tables exist.  `migrate` on a missing or zero-length file, when `os.geteuid() != 0`, creates the store through `SqliteWatermarkStore` and returns `MigrateReport(from_version=0, to_version=SCHEMA_VERSION)`; as root it raises as today.
- [ ] **Step 4: Run the file, then the whole suite; all pass.**
- [ ] **Step 5: Commit** `store: stamp the schema version on a store it creates (spec §4.3)`.

### Task 2: A source that knows its producer and its instance

**Files:**
- Modify: `hs-uploader/src/hs_uploader/sources/sqlite.py` (`__init__`, `from_env`, `source_id`, every SQL builder, `_cursor_from_start_at`, stale-schema probe, `commit`)
- Modify: `hs-uploader/src/hs_uploader/pipeline_factory.py:97-111` (keys `producer`, `radiod_id`)
- Test: `hs-uploader/tests/test_sqlite_source_producer.py` (new)

**Interfaces:**
- Produces: `SqliteSource(..., producer: Optional[str] = None, radiod_id: Optional[str] = None)`; `source_id()` → `sqlite:<db>.<table>`, `…@<producer>`, or `…@<producer>/<radiod_id>`; `radiod_id` without `producer` raises `ValueError`.

- [ ] **Step 1: Write the failing tests.**  A fixture sink with rows: ft8 and ft4 with `producer='psk-recorder'`; msk144 with `producer='meteor-scatter'`; `producer=''` rows with modes `'MSK144'`, `' msk144 '`, `'FT8'` and no mode; rows on two `radiod_id` values.
  - `test_source_id_forms` — the three forms above.
  - `test_producer_rows_partition` — the `psk-recorder` and `meteor-scatter` sources select disjoint ids whose union equals the unfiltered source's ids; the `''` rows land per the D12 constraint.
  - `test_no_producer_column` — same partition on a `sink.db` built without the column; `pump()` never raises.
  - `test_instance_rows_partition` — two `radiod_id` sources of one producer: disjoint, union equals that producer's rows (spec test 7a).
  - `test_commit_deletes_only_own_producer` — `delete_on_commit=True` with producer deletes no other producer's row.
  - `test_start_at_now_anchors_within_producer` — the anchor is the producer's max id, not the table's.
  - `test_factory_passes_producer_and_radiod_id`.
- [ ] **Step 2: Run; all fail.**
- [ ] **Step 3: Implement.**  One private helper builds the producer clause, used by every statement that touches rows:

```sql
-- column present
(producer = :p OR (producer = '' AND <d12(:p)>))
-- column absent
<d12(:p)>
-- d12('meteor-scatter') = lower(trim(coalesce(json_extract(payload_json,'$.mode'),''))) = 'msk144'
-- d12('psk-recorder')   = lower(trim(coalesce(json_extract(payload_json,'$.mode'),''))) != 'msk144'
```

  Any other producer value: column clause only.  `radiod_id` adds `json_extract(payload_json,'$.radiod_id') = :r`.  Detect the column once per connection with `PRAGMA table_info(pending_uploads)`.
- [ ] **Step 4: Run the new file and the suite; all pass.**
- [ ] **Step 5: Commit** `sqlite source: producer and radiod_id scope rows and key (spec §4.1, §3.3)`.

### Task 3: Forward-only, enforced

**Files:**
- Modify: `hs-uploader/src/hs_uploader/watermark/sqlite.py:184-330`, `watermark/base.py:73-105`
- Modify: `hs-uploader/src/hs_uploader/core.py:464-490, 540-561` (callers)
- Test: `hs-uploader/tests/test_advance_cursor_checked.py` (rewrite to the new contract)

**Interfaces:**
- Produces: `class Advance(enum.Enum): WRITTEN, REFRESHED, REFUSED, WRITTEN_UNCHECKED`; `SqliteWatermarkStore.advance_cursor(source_id, dest_id, table, *, cursor: bytes, last_ack: str, is_after: Callable[[bytes, bytes], bool]) -> Advance`.  The unchecked writer becomes private `_write_cursor`; `advance_cursor_checked` is removed; `reset_cursor` keeps writing unchecked.

- [ ] **Step 1: Write the failing tests:** one per row of spec §4.6 (`after`→`WRITTEN`, record changed; `equal`→`REFRESHED`, cursor same, `last_ack` new; `before`→`REFUSED`, nothing changed, one WARNING; comparator raises→`WRITTEN_UNCHECKED`, WARNING); `test_two_connections_race` — two store objects on one file, interleaved writes, the record never moves back; `test_core_logs_acked_record_held` — a REFUSED advance logs `acked, record held`; `test_filetree_keep_spool_is_checked` — the kept spool's mtime cursor refuses a backward write (review N1).
- [ ] **Step 2: Run; all fail.**
- [ ] **Step 3: Implement.**  Run the read, the comparison and the write inside `BEGIN IMMEDIATE` on the store's connection.  Keep the existing per-key WARNING throttle (`BACKWARD_WARN_INTERVAL_S`).  Update `base.py`'s Protocol and every caller found by `grep -rn "advance_cursor" --include=*.py` across `/home/mjh/hamsci/repos` (skip venvs).
- [ ] **Step 4: Run the suite; all pass.**
- [ ] **Step 5: Commit** `store: forward-only send records, enforced (spec §4.6, D17 answer 6)`.

### Task 4: A pipeline that knows its client; the daemon checks the store

**Files:**
- Modify: `hs-uploader/src/hs_uploader/core.py:183-215` (`Pipeline.client`)
- Modify: `hs-uploader/src/hs_uploader/pipeline_factory.py` (generic pipelines read `client`)
- Modify: `hs-uploader/src/hs_uploader/daemon.py:104-160` (builder pipelines get `entry["client"]`; store check; `pipeline_state` write)
- Modify: `hs-uploader/src/hs_uploader/watermark/sqlite.py` (`pipeline_state` table, `record_pipeline_state`, `pipeline_states`)
- Modify: `hs-uploader/src/hs_uploader/cli.py` (`capabilities` subcommand)
- Test: `hs-uploader/tests/test_daemon.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `schema.SCHEMA_VERSION` (Task 1), `SqliteSource.producer` (Task 2).
- Produces: `Pipeline.client: str = ""`; table `pipeline_state(name TEXT PRIMARY KEY, client TEXT NOT NULL, refused_reason TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)`; `SqliteWatermarkStore.record_pipeline_state(name: str, client: str, refused_reason: str = "") -> None`; `pipeline_states() -> list[sqlite3.Row]`; `hs-uploader capabilities` prints `2\n` and exits 0.

- [ ] **Step 1: Write the failing tests:** `test_client_from_manifest_generic_and_builder`; `test_old_store_refuses_only_producer_pipelines` (store at version 1, manifest with a producer pipeline and a plain one: only the plain one pumps; `pipeline_state` holds `refused_reason` naming version 1 and needing 2); `test_capabilities_prints_store_version`.
- [ ] **Step 2: Run; all fail.**
- [ ] **Step 3: Implement.**  The daemon reads the store's `user_version` after building pipelines, drops each pipeline whose source has a `producer` when the version is below 2, and records every pipeline's state at start.  Refusals log one ERROR each.
- [ ] **Step 4: Run the suite; all pass.**
- [ ] **Step 5: Commit** `daemon: pipelines know their client; refuse producer pipelines on an old store (spec §4.2, §4.7, §4.8)`.

### Task 5: Migration 2 and the retry pass

**Files:**
- Modify: `hs-uploader/src/hs_uploader/watermark/schema.py` (migration context, migration 2, retry pass, `superseded_keys`)
- Modify: `hs-uploader/src/hs_uploader/cli.py:84-102, 286-310` (`--sink`, `--keep-retry-for`)
- Test: `hs-uploader/tests/test_migration_split.py` (new)

**Interfaces:**
- Consumes: `SqliteWatermarkStore` schema (Task 1).
- Produces: `@dataclass MigrateContext(sink_path: Optional[Path] = None, keep_retry_for: Optional[str] = None)`; `Migration.apply: Callable[[sqlite3.Connection, MigrateContext], None]`; `migrate(path, *, check=False, ctx: MigrateContext = MigrateContext()) -> MigrateReport`; `MigrateReport` gains `lines: list[str]` (what it copied, deleted, moved); table `superseded_keys(old_source_id, old_dest_id, old_table, new_source_id, new_dest_id, new_table, migrated_at, PRIMARY KEY(old_source_id, old_dest_id, old_table, new_source_id))`; `SCHEMA_VERSION = 2`.

- [ ] **Step 1: Write the failing tests** (spec tests 1-2):
  - `test_copies_old_record_to_both_new_keys` — old row stays, both new rows equal it, `superseded_keys` holds two full rows.
  - `test_existing_new_key_untouched`.
  - `test_other_keys_untouched` — wspr, GRAPE, heartbeat rows byte-identical.
  - `test_retry_deleted_when_sink_holds_rows` — sink holds the id after the old cursor → retry gone.
  - `test_retry_moved_when_rows_aged_out` and `test_retry_moved_when_no_sink` → renamed to `keep_retry_for`, key rewritten to that pipeline's new key.
  - `test_keyless_retry_handled`.
  - `test_retry_pass_repeats_at_version_2` — a retry queued under `psk-pskreporter` after migration 2 gets handled by the next `migrate`.
  - `test_dead_letters_untouched`.
  - `test_check_reports_without_writing`.
  - `test_end_to_end_no_resend_no_skip` — fixture sink (FT8, FT4, MSK144, `''` rows), store at version 1 with the shared record mid-table, migrate, both pipelines pump to idle against a recording stand-in transport: every row past the old record ships exactly once on the right pipeline.
- [ ] **Step 2: Run; all fail.**
- [ ] **Step 3: Implement.**  Migration 2 selects `watermarks` rows with `source_id = 'sqlite:psk.spots'` and `dest_id LIKE 'pskreporter%'`.  The retry pass runs after the versioned migrations on every non-check run, inside the same transaction, over `deliverables WHERE pipeline = 'psk-pskreporter'`.  "Sink holds the rows" means: `ctx.sink_path` given and `SELECT 1 FROM pending_uploads WHERE id = <old cursor last_id> + 1` finds a row (decode the cursor with `SqliteSource`'s cursor codec).  With no `keep_retry_for`, a retry that cannot be deleted stays and the report says so.
- [ ] **Step 4: Run the suite; all pass.**
- [ ] **Step 5: Commit** `migrate 2: split the pskreporter send record; dissolve or move mixed retries (spec §4.4)`.

### Task 6: Status, backlog and reset-cursor read the new tables

**Files:**
- Modify: `hs-uploader/src/hs_uploader/cli.py:153-200` (`status`, `reset-cursor`)
- Modify: `hs-uploader/src/hs_uploader/backlog.py:60-155` (`summarize`)
- Test: `hs-uploader/tests/test_backlog.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `superseded_keys` (Task 5), `pipeline_state` (Task 4).
- Produces: each `summarize()` cursor entry gains `superseded_by: list[str]` (new source ids, empty when live); `summarize()` gains top-level `pipelines: [{name, client, refused_reason}]` and `orphaned_dead_letters: int` (dead letters whose pipeline is not in `pipeline_state`).

- [ ] **Step 1: Write the failing tests:** superseded key marked in `status` text and `summarize()`; `reset-cursor` on a superseded key exits 2 and names the new keys; refused pipeline and client appear in `summarize()`; orphaned dead letters counted and labelled in `status`.  `summarize()` must still work read-only on a version-1 store without either table.
- [ ] **Step 2: Run; all fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the suite; all pass.**
- [ ] **Step 5: Commit** `status and backlog: superseded keys, pipeline clients and refusals (spec §4.5)`.

### Task 7: The proof on real data

**Files:**
- Create: `hs-uploader/scripts/prove_key_split.py`
- Test: `hs-uploader/tests/test_prove_key_split.py`

**Interfaces:**
- Consumes: Tasks 2 and 5.
- Produces: `prove_key_split.py --sink SINK --store STORE [--add-msk144 N]` exits 0 when the two new pipelines' row sets are disjoint and their union equals the old pipeline's row set (same `extra_where` as the shared block), else 1 with the differing ids.  It works on copies, never on the given files.

- [ ] **Step 1: Write the failing test:** passes on a fixture; with the producer clause removed from Task 2's helper (patched in the test), it exits 1.
- [ ] **Step 2: Run; fails (script absent).**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the test, then the script against the 10-08 snapshots in `/home/mjh/v370-t10/` with `--add-msk144 50`; record the output in the commit message.
- [ ] **Step 5: Commit** `scripts: prove the key split on station snapshots (spec test 10)`.

### Task 8: sigmond renders the split

**Files:**
- Modify: `sigmond/lib/sigmond/uploader_manifest.py:204-247` (`collect_pipelines`), `:370-384` (probe)
- Test: `sigmond/tests/test_uploader_manifest_split.py` (new)

**Interfaces:**
- Consumes: `hs-uploader capabilities` (Task 4).
- Produces: `hs_uploader_store_version(python: Path = HS_UPLOADER_PYTHON) -> Optional[int]` — the integer, `0` when the subcommand does not exist (argparse exit 2), `None` when the probe failed or timed out; `class ProbeFailed(Exception)`; `collect_pipelines(topology=None, *, store_version: int)` — splits only when `store_version >= 2`; every pipeline dict carries `client`.

- [ ] **Step 1: Write the failing tests:** both clients → two pipelines with `producer`, `client` and `extra_where` modes `["ft8","ft4"]` / `["msk144"]`, the rest copied from the block; psk-recorder alone → only its pipeline; meteor-scatter alone → only its; `store_version=1` → the shared `psk-pskreporter`, with a warning; probe `None` → `generate()` raises `ProbeFailed`; every other pipeline carries its declaring `client`.
- [ ] **Step 2: Run; all fail.**
- [ ] **Step 3: Implement** the rewrite between substitution and de-duplication: replace the block's `["mode", "IN", [...]]` clause with the producer's modes and add `source.producer`.
- [ ] **Step 4: Run the sigmond suite; all pass.**
- [ ] **Step 5: Commit** `uploader manifest: render the pskreporter split per producer (spec §5.1, D17 answer 2)`.

### Task 9: D13 at render time

**Files:**
- Create: `sigmond/lib/sigmond/inprocess_senders.py`
- Modify: `sigmond/lib/sigmond/uploader_manifest.py` (`generate` leaves out matching pipelines)
- Modify: `sigmond/lib/sigmond/harmonize.py` (new rule)
- Test: `sigmond/tests/test_inprocess_senders.py` (new)

**Interfaces:**
- Produces: `inprocess_senders(topology, *, show=_systemctl_show) -> set[tuple[str, str]]` — `(producer, destination)` with destination `"pskreporter"`; `show(unit) -> dict[str, str]` returns the unit's effective environment (`Environment` plus the files in `EnvironmentFiles`), raising `OSError` when unreadable; harmonize rule id `uploader-inprocess-drift`.

- [ ] **Step 1: Write the failing tests:** psk-recorder `PSK_DELIVERY_PIPELINES=direct` → pair; `server-raw` → none; legacy `PSK_DELIVERY_MODE=direct` → pair; unset → none (`server-merge`); meteor-scatter `METEOR_SCATTER_DELIVERY_MODE=direct` → pair; `off` set only in `coordination.env` → none; unset → pair; unreadable environment → none for that client; `generate()` omits only the matching pipeline; drift rule warns when the env answer differs from the manifest on disk.
- [ ] **Step 2: Run; all fail.**
- [ ] **Step 3: Implement**, using `systemctl show -p Environment -p EnvironmentFiles <unit>` for each enabled instance unit, reading each listed file with sigmond's existing env-file parser.
- [ ] **Step 4: Run the sigmond suite; all pass.**
- [ ] **Step 5: Commit** `uploader manifest: D13 decided at render time, per producer and destination (spec §5.3)`.

### Task 10: The manifest step, reordered

**Files:**
- Modify: `sigmond/lib/sigmond/commands/uploader.py:143-320` (`_run_migrate`, `cmd_uploader_manifest`)
- Modify: `sigmond/bin/smd` (`apply` and bring-up honour the step's exit), `sigmond/lib/sigmond/bringup.py:517-520`
- Test: `sigmond/tests/test_uploader_manifest_step.py` (new)

**Interfaces:**
- Consumes: `hs_uploader_store_version`, `ProbeFailed` (Task 8); `migrate --check`, `--db`, `--sink`, `--keep-retry-for` (Tasks 1, 5).
- Produces: `_run_migrate(*, db: Path, sink: Path, keep_retry_for: Optional[str], check: bool = False) -> int` (check mode: 0 nothing pending, 3 pending); `cmd_uploader_manifest` follows spec §5.2 steps 1-8 in both `--write` and `--enable` modes.

- [ ] **Step 1: Write the failing tests** (subprocess and systemctl stubbed, recording the call order):
  - `test_order_stop_migrate_move_start` — calls run in that order; the manifest lands only after migrate exits 0.
  - `test_failed_migrate_keeps_old_manifest_and_daemon_state` — in `--write` and `--enable` modes; daemon running before → running after; stopped before → stopped after.
  - `test_unchanged_manifest_with_pending_migration_still_migrates` (review B1).
  - `test_unchanged_and_nothing_pending_returns_without_stop`.
  - `test_probe_failure_leaves_manifest_and_exits_nonzero`.
  - `test_migrate_timeout_kills_process_group` — `start_new_session=True`, `os.killpg` on timeout.
  - `test_keep_retry_for_names_a_rendered_pipeline` — psk-recorder's when rendered, else meteor-scatter's.
  - `test_apply_reports_manifest_step_failure`.
- [ ] **Step 2: Run; all fail.**
- [ ] **Step 3: Implement.**  `--db` is the daemon's store (`/var/lib/hs-uploader/watermarks.db` unless the unit's environment sets `HS_UPLOADER_STATE_DIR`); `--sink` is `/var/lib/sigmond/sink.db`.
- [ ] **Step 4: Run the sigmond suite; all pass.**
- [ ] **Step 5: Commit** `uploader manifest: stop, migrate, then land the manifest (spec §5.2, D17 answer 4)`.

### Task 11: Docs and the update rig

**Files:**
- Modify: `hs-uploader/README.md` (keys, `capabilities`, `migrate` flags, forward-only), `sigmond/docs/` page that documents `smd admin uploader manifest` (find with `grep -rln "uploader manifest" sigmond/docs`)
- Modify: `sigmond-appliance/test-update-v3.sh` (assert stop → migrate → start order and the two new pipelines; rollback re-renders `psk-pskreporter`; a second update leaves both new keys in place)

- [ ] **Step 1:** Update the docs to describe the current behaviour only.
- [ ] **Step 2:** Extend the rig test; run `bash -n` and the static update tests.  It runs end to end only on the rig: record that in the ledger as owed before the build.
- [ ] **Step 3: Commit** in each repo `docs: the send-record key split (v3.73 row 1)`.

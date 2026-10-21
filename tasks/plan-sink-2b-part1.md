# Sink control step 2b part 1: plumbing that sends nothing new — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.  Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the plumbing for the sink switches without changing a single byte that any station sends.  The daemon learns to read `pipelines.d/` and to rebuild when it changes.  The store gains a send log that names each pipeline and its client.  The sink writer gains `local=`, and every source learns to skip local rows.  hs-uploader gains a switch-file library that reports and enforces nothing, plus `capabilities --features`.  sigmond learns to ask for the features and only prints the answer.

**Architecture:** All the weight sits in hs-uploader.  Migration 3 adds two columns and one table.  A new loader merges `pipelines.d/<client>.toml` with the legacy manifest by a precedence rule.  The daemon rebuilds between pumps from a stat signature of that directory and swaps pipelines through `Uploader.replace_pipelines`.  A new stdlib-only module, `sinkswitch.py`, reads and writes switch files.  sigmond gains one probe and one printed line, plus a refreshed `_bundled.py`.

**Tech Stack:** Python 3.11; SQLite (`PRAGMA user_version`, `PRAGMA table_info`, `EXPLAIN QUERY PLAN`); `tomllib`; `fcntl.flock`; pytest.  sigmond's core stays stdlib-only.

**Spec:** `sigmond/tasks/spec-sink-2b-v377.md`, binding, with Michael's rulings in §13.  Design: `plan-sink-control.md` D9, D10, D12, D13, D17, D18, D20, D21, §3.4, §3.5, §6.1, §6.2, §13.7.

**Name and number.**  This plan calls the work "step 2b part 1".  It promises no release number.  v3.77 went to an unrelated image on 10-21, and the spec's "v3.77" names this work's place in D18's order, not a tag.  Whoever builds the image takes the next free number.  Where this plan quotes the spec's "v3.76" it means the release that carries hs-uploader 334e71e; "v3.78" means the next part of step 2b.

## Global Constraints

- **Push nothing while the current image build runs.**  An image build pulls sigmond, and it must not see this work.  Commit locally on `main` in each repo.  Push, fast-forward only, after Michael confirms the build has finished and clears the push.  From then on, push each task after its review passes, never before (key-split Ruling 3).
- Commit trailers, exactly:
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01H4HYgCZ3dRtK4XWA1Xyoc3`.
- Every new test must fail before its change.  The implementer watches it fail, then applies the spec's mutation (§9) to the finished code and watches it fail again.  The commit message names that mutation.
- Runners, from each repo's root:
  - hs-uploader: `.venv/bin/python -m pytest -q` (hs-uploader's CLAUDE.md shows `uv run pytest`; that form may re-sync the venv, so use the venv directly, as the key split did).
  - sigmond: `.venv/bin/pytest -q`.
  - sigmond-appliance: `bash -n <script>` and each `test-*.sh` static test named in the task.
- Bases at start: hs-uploader 334e71e, sigmond 79dca3d (spec anchors read a3d90e8; only `tasks/` moved since), sigmond-appliance 5da3a0c.  The spec's line anchors hold at those bases.
- Names, verbatim: store version after this plan `SCHEMA_VERSION = 3`; table `last_send`; columns `attempts.pipeline`, `attempts.client`; index `idx_pending_uploads_producer`; directories `pipelines.d/` and `sinks/` beside the legacy manifest; feature words `pipelines.d`, `send-log-v3`, `switch-files`; CLI `hs-uploader sink status`, `hs-uploader capabilities --features`; sigmond function `uploader_manifest.hs_uploader_features`.
- The client-name rule, everywhere: `^[a-z0-9][a-z0-9-]*$`.
- Touch no station.  Touch no client repository.  sigmond creates no directory under `/etc/hs-uploader`.  No unit file changes.  Do not edit `sigmond/tasks/` except this plan's ledger.
- After a repo's push, refresh the code graph: `graphify update /home/mjh/hamsci/repos` (never from `/home/mjh/hamsci`).
- The controller keeps a ledger in the SDD workspace, in the shape of `ledger-key-split-v373.md`: a preflight scan, numbered rulings each with its cost if wrong, one line per dispatch, review and fix round, and the deferred minors.  At the end it copies the ledger to `sigmond/tasks/ledger-sink-2b-part1.md`.

## Rulings this plan makes on the spec

The spec leaves these open or self-contradictory.  The controller records each in the ledger as a numbered ruling.

1. **The feature words land last.**  §7.1 says a word appears only in the release that ships its code complete.  Stations can pull `main` once pushes resume.  So `--features` (Task 12) lands after every task its three words describe.  Cost if wrong: none; the flag only waits.
2. **Run 5 needs a stale-copy variant.**  §8.3 run 5 moves legacy entries out of the legacy manifest.  With the entries gone, a loader that skips the precedence rule builds the same set, so run 6's second mutation could never fail.  Run 5b therefore leaves each moved entry in the legacy manifest too, with `batch_limit` altered there.  Precedence must ignore the stale copy; without precedence, the collision refuses the file and the altered limit surfaces as DISAGREE.  The tool also reports every refused file and never ends AGREE when one exists.  Cost if wrong: one extra run per station.
3. **ND's manifest carries no `client`.**  ND runs sigmond from the v3.69 image, which renders no `client` key.  Run 5 on ND assigns clients by name, from B4's manifest: `grape-psws` → `hamsci-physics`, `mag-psws` → `mag-recorder`, `psk-pskreporter` → `psk-recorder`, `wspr-wsprdaemon` and `wspr-wsprnet` → `wspr-recorder`; `heartbeat` stays.  Keys carry no client, so the result must still AGREE.  Cost if wrong: none to sending; run 5 on ND proves a little less.
4. **ND's store stands at version 1.**  Run 4's "every row hashes the same" holds only for a step from 2 to 3.  On ND, `migrate` runs migrations 2 and 3 together and adds the split keys.  Task 17 checks ND by comparing two paths to version 3 (see there).  Cost if wrong: none; it adds a check.
5. **No `sinks/` directory, no per-client lines.**  §6.2 asks for a line per governed client and also one line "no switch files" when `sinks/` is missing.  With `sinks/` missing the daemon logs only that one line.  Legacy entries count as governed (default true), since §11 leaves the heartbeat's exemption to v3.78; the report changes nothing either way.  Cost if wrong: a few INFO lines.
6. **The store learns its version from the daemon.**  The store caches whether it may write the v3 send-log columns.  The daemon refreshes that cache at start and at every rebuild (§4.3).  A test or tool that never refreshes it writes the old column set.  Cost if wrong: a tool's attempt rows lack names.

## Review Focus

1. A version-2 store with migration 3 pending and a missing producer key: `migrate --check` previews the fill, and `migrate` runs it (Tasks 1, 2).
2. A v3.76 daemon on a version-3 store: every INSERT succeeds and the new columns read `''` (Task 2).
3. A row with `local = 1` in `sink.db`: no source selects it, and no `commit()` deletes it (Task 5).
4. `pipelines.d/psk-recorder.toml` present but broken at start: psk-recorder's legacy entries stay silent, every other pipeline runs, and the daemon keeps running (Task 9).
5. A file changes between pumps: unchanged pipelines keep their `Pipeline` object and executor; a queued retry survives; a hand edit of the legacy manifest changes nothing until restart (Task 10).
6. Bare `hs-uploader capabilities` prints exactly `3\n` (Tasks 2, 12).

## Task order

| # | Task | Repo | Spec tests |
|---|---|---|---|
| 1 | `migrate --check` previews the fill whenever migration 2 does not wait | hs-uploader | 11 |
| 2 | Migration 3 and `SCHEMA_VERSION = 3` | hs-uploader | 9, 10, 11, 13, 20 (bare) |
| 3 | The send log writes `pipeline`, `client` and `last_send` | hs-uploader | 12 |
| 4 | The backlog reports `last_send` | hs-uploader | 14 |
| 5 | Every source skips local rows | hs-uploader | 16 |
| 6 | `Writer(local=)`, the producer index, and the bundled copy | hs-uploader, sigmond | 15 |
| 7 | The switch library: reading | hs-uploader | 17 |
| 8 | The switch library: writing | hs-uploader | 18 |
| 9 | The `pipelines.d/` loader | hs-uploader | 1, 2, 3, 4, 5, 8 (start) |
| 10 | Rebuild on change, and the store check at rebuild | hs-uploader | 6, 7, 8 (rebuild) |
| 11 | The switch report and `hs-uploader sink status` | hs-uploader | 19 |
| 12 | `capabilities --features` | hs-uploader | 20 |
| 13 | The neutrality tool reads `--pipelines-d` | hs-uploader | 22 |
| 14 | sigmond's features probe and the step test | sigmond | 21, 23 |
| 15 | The update rig's assertions | sigmond-appliance | — |
| 16 | Docs | all three | — |
| 17 | The proof on B4's and ND's copies | devbox only | §8.3 runs 1-6 |
| 18 | Final cross-repo review | all three | — |
| 19 | Rig runs: nested install and update in place, then the bench | rig, then B4 | §8.5 |

Task 5 lands before Task 6 on purpose: a writer that can mark a row local must not exist before every reader honours the mark (§5.2).  Tasks 7 and 8 land before Task 9, because the loader imports the client-name rule from `sinkswitch`.

---

### Task 1: `migrate --check` previews the fill

**Files:**
- Modify: `hs-uploader/src/hs_uploader/watermark/schema.py:560-563` (`_check`)
- Test: `hs-uploader/tests/test_watermark_schema.py`

**Interfaces:**
- Produces: no new name.  `_check` skips the fill preview only when a migration with `version == 2` waits.

- [ ] **Step 1: Write the failing test.**  Migration 3 does not exist yet, so the test adds a stand-in.

```python
def test_check_previews_fill_when_a_later_migration_waits(tmp_path, monkeypatch):
    # version-2 store: shared pskreporter record, psk-recorder key present,
    # meteor-scatter key missing
    p = _store_v2_missing_meteor_key(tmp_path)
    noop3 = schema.Migration(version=3, apply=lambda conn, ctx: [], preview=None,
                             description="stand-in")
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS + (noop3,))
    rep = schema.migrate(str(p), check=True)
    assert any("meteor-scatter" in ln for ln in rep.lines)   # the fill line
    assert _user_version(p) == 2                             # nothing written
```

  Match the `Migration` constructor to its real fields; the sketch shows intent.  Also pin the unchanged case: with migration 2 pending (a version-1 store), `--check` still shows migration 2's own preview and no separate fill line.
- [ ] **Step 2: Run and watch it fail** — `.venv/bin/python -m pytest -q tests/test_watermark_schema.py -k check_previews`.
- [ ] **Step 3: Implement.**  Replace `and not pending` with `and not any(m.version == 2 for m in pending)`.  Rewrite the comment above it to say why migration 2, and only migration 2, makes the preview redundant.
- [ ] **Step 4: Mutation.**  Restore `not pending`; the new test must fail.  Revert.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `migrate --check: preview the fill unless migration 2 itself waits (spec §4.2, test 11)` — body names the mutation.

**Review:** spec reviewer and quality reviewer (the standard per-task pair).

### Task 2: Migration 3 and `SCHEMA_VERSION = 3`

**Files:**
- Modify: `hs-uploader/src/hs_uploader/watermark/schema.py:166-180` (frozen DDL beside migration 2's), `:268-273` (`MIGRATIONS`), `:504` (follows by itself)
- Modify: `hs-uploader/src/hs_uploader/watermark/sqlite.py:38-105` (`_SCHEMA_SQL`), `:169-179` (stamp at birth reads `SCHEMA_VERSION`)
- Test: `hs-uploader/tests/test_migration_v3.py` (new); update tests that hard-code version 2

**Interfaces:**
- Consumes: Task 1's `_check`.
- Produces: `Migration(version=3, ...)` with frozen DDL `_V3_LAST_SEND_DDL`; `attempts.pipeline TEXT NOT NULL DEFAULT ''`, `attempts.client TEXT NOT NULL DEFAULT ''`; `last_send(client TEXT NOT NULL, pipeline TEXT NOT NULL, outcome TEXT NOT NULL, ts TEXT NOT NULL, records INTEGER, PRIMARY KEY (client, pipeline))`; `SCHEMA_VERSION == 3`; bare `hs-uploader capabilities` prints `3\n`.

- [ ] **Step 1: Write the failing tests** (spec tests 9, 10, 11, 13, 20's bare half):
  - `test_v2_store_reaches_3` — columns and `last_send` exist; `user_version` reads 3; a digest over every row of `watermarks`, `deliverables`, `dead_letter`, `superseded_keys`, `pipeline_state` matches before and after; `attempts` keeps every old value and the new columns read `''`.
  - `test_v0_store_runs_1_2_3_in_order` — report lines in that order.
  - `test_migration_3_adds_only_missing_columns` — a store that already has `pipeline` (hand-made) gains only `client`.
  - `test_fill_runs_at_version_3` (test 10) — a version-3 store with the shared pskreporter record and no producer keys: `migrate` fills both.
  - `test_check_previews_fill_with_migration_3_pending` (test 11, now without the stand-in).
  - `test_check_previews_migration_3_lines` — `--check` names both columns and the table, writes nothing.
  - `test_old_writer_inserts_on_v3_store` (test 13) — an INSERT naming only v3.76's `attempts` columns succeeds; the new columns read `''`.
  - `test_new_store_stamped_3_with_new_schema`.
  - `test_capabilities_bare_prints_3` — stdout reads exactly `3\n`; `int(stdout.strip())` gives 3.
- [ ] **Step 2: Run; all fail** — `.venv/bin/python -m pytest -q tests/test_migration_v3.py`.
- [ ] **Step 3: Implement.**  Each ALTER runs only when `PRAGMA table_info(attempts)` lacks the column.  `CREATE TABLE IF NOT EXISTS last_send` from the frozen DDL.  Report one line per change; the preview returns the same lines.  The migration fills no old row.  `_fills` needs no edit: it already reads up to `max(MIGRATIONS)`.
- [ ] **Step 4: Mutations.**  Omit the ALTER for `client` → `test_v2_store_reaches_3` fails.  Bound `_fills` to version 2 → `test_fill_runs_at_version_3` fails.  Declare a new column `NOT NULL` without a default → `test_old_writer_inserts_on_v3_store` fails.  Revert each.
- [ ] **Step 5: Run the suite; all pass.**  Fix tests that pinned `SCHEMA_VERSION == 2` to read the constant.
- [ ] **Step 6: Commit** `migrate 3: name the pipeline and client in the send log; add last_send (spec §4.1, tests 9-11, 13)`.

**REVIEW GATE (store):** after the per-task pair, the controller checks by hand that a v3.76 sigmond still reads the bare probe (`uploader_manifest.py:463`) and that `migrate --check` exits 3 on a version-2 store.

### Task 3: The send log names its pipeline and client

**Files:**
- Modify: `hs-uploader/src/hs_uploader/watermark/base.py:114-126` (Protocol), `watermark/sqlite.py:459-484` (`record_attempt`)
- Modify: `hs-uploader/src/hs_uploader/core.py:483, 497, 517, 531, 626, 646, 669, 687, 707` (nine callers)
- Modify: `hs-uploader/src/hs_uploader/transports/discard.py:30-55` (`ships = False`)
- Modify: `hs-uploader/src/hs_uploader/daemon.py` (refresh the store's version at start)
- Test: `hs-uploader/tests/test_send_log.py` (new)

**Interfaces:**
- Consumes: migration 3 (Task 2).
- Produces: `record_attempt(..., pipeline: str = "", client: str = "")`; `SqliteWatermarkStore.refresh_schema_version() -> int`, which caches whether the store stands at 3 or above (Ruling 6); `DiscardTransport.ships = False`; the core reads `getattr(transport, "ships", True)`.

- [ ] **Step 1: Write the failing tests** (spec test 12):
  - `test_v2_store_writes_old_columns_and_no_last_send`.
  - `test_v3_store_writes_pipeline_and_client` — every one of the nine outcomes the core records carries `pipe.name` and `pipe.client`.  Drive them through the core with a stand-in transport, as `test_core_orchestration.py` does.
  - `test_last_send_on_acked_and_partial_ack_only` — `acked` and `partial_ack` upsert `(client, pipeline)` with `outcome`, `ts`, `records`; a failure leaves the row as it stood.
  - `test_last_send_same_transaction` — when the attempt INSERT fails, `last_send` stays unchanged.
  - `test_discard_writes_no_last_send`.
  - `test_existing_callers_unchanged` — a call without the new keywords still works on both versions.
- [ ] **Step 2: Run; all fail.**
- [ ] **Step 3: Implement.**  The store chooses the column set from its cached version.  The daemon calls `refresh_schema_version()` once at start, beside `split_refused`.  Task 10 adds the call at each rebuild.
- [ ] **Step 4: Mutations.**  Write the new columns whatever the version → the version-2 test fails.  Drop the `ships` check → the discard test fails.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `send log: name the pipeline and client, keep last_send on version 3 (spec §4.3, test 12)`.

**Review:** per-task pair.  The spec reviewer counts the nine call sites against `grep -n record_attempt src/hs_uploader/core.py`.

### Task 4: The backlog reports `last_send`

**Files:**
- Modify: `hs-uploader/src/hs_uploader/backlog.py:70-202` (`summarize`)
- Test: `hs-uploader/tests/test_backlog.py`

**Interfaces:**
- Consumes: `last_send` (Tasks 2, 3).
- Produces: `summarize()["last_send"]: list[{client, pipeline, outcome, ts, records}]`, read through `_optional_rows`.

- [ ] **Step 1: Write the failing tests** (spec test 14): `last_send` sits at the top level; a version-2 store reads `[]`; each per-pipeline dict keeps exactly v3.76's keys (pin the key set as a literal).
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mutation.**  Put `last_send` inside the per-pipeline dicts → the key-set test fails.
- [ ] **Step 5: Run the hs-uploader suite, then sigmond's `tests/test_heartbeat.py`; all pass.**  The heartbeat copies only `pipelines` and `cursors` (`heartbeat.py:414-423`), so its envelope must not change.
- [ ] **Step 6: Commit** `backlog: report last_send at the top level (spec §4.4, test 14)`.

**Review:** per-task pair.

### Task 5: Every source skips local rows

**Files:**
- Modify: `hs-uploader/src/hs_uploader/sources/sqlite.py:437-481` (`commit`), `:529-560` (beside `_scope_clauses`), `:610-672`, `:674-750`, `:816-843`
- Modify: `hs-uploader/src/hs_uploader/sources/wspr_cycle.py:171-178`, `:311-316`, `:361-365`
- Test: `hs-uploader/tests/test_local_rows.py` (new)

**Interfaces:**
- Produces: a private helper per source that returns `"local = 0"` when `PRAGMA table_info(pending_uploads)` lists `local`, else nothing.  It probes once per connection, as the `producer` probe does (`sqlite.py:543-545`).

- [ ] **Step 1: Write the failing tests** (spec test 16).  The fixture sink holds pairs of rows that differ only in `local`, written with plain SQL (no writer sets `local=1` yet):
  - `SqliteSource` without a producer, and with one: the batch holds no local row.
  - The de-duplication path (`dedup_partition_by`): a local row with the higher SNR never wins.
  - `start_at = "now"`: the anchor ignores a local row with the highest id.
  - `commit()` with `delete_on_commit=True`: the local row survives.
  - `WsprCycleSource`: cycle discovery ignores a cycle that holds only local rows; the per-cycle fetch and the psk rider skip local rows.
  - A sink without the `local` column still reads, and `pump()` never raises.
  - Today's data: every row `local = 0` → the batch matches the unfiltered batch row for row.
- [ ] **Step 2: Run; all fail** except the no-column and today's-data cases.
- [ ] **Step 3: Implement.**  The clause applies with or without a producer, so it sits beside `_scope_clauses`, not inside.  Leave the stale-schema probe (`sqlite.py:752-782`) and `wspr_completion.py` alone.
- [ ] **Step 4: Mutations.**  Drop the clause from each statement in turn; each drop must fail at least one test.  Record which test caught which statement in the commit body.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `sources: never select or delete a local row (spec §5.2, test 16)`.

**REVIEW GATE (selection):** the spec reviewer lists every SQL statement in both sources that selects or deletes rows and shows each one carries the clause.  A missed statement here would ship local rows in v3.78.

### Task 6: `Writer(local=)`, the producer index, and sigmond's bundled copy

**Files:**
- Modify: `hs-uploader/src/hs_uploader/sink/writer.py:185-187` (`PENDING_UPLOADS_DDL`), `:295-339` (`__init__`, `from_env`), `:434-453` (`flush`), `:496-503` (schema setup)
- Modify: `sigmond/lib/sigmond/hamsci_sink/_bundled.py` (refreshed by recipe only)
- Test: `hs-uploader/tests/test_sink_writer.py`; sigmond's existing `tests/test_hamsci_sink_compat.py`

**Interfaces:**
- Produces: `Writer(..., local: bool = False)`, `Writer.from_env(..., local: bool = False)`; index `idx_pending_uploads_producer ON pending_uploads (target_db, target_table, producer, id)`, created after `ensure_columns`.

- [ ] **Step 1: Write the failing tests** (spec test 15): `local=True` stores 1; the default stores 0; an old sink (no `producer`, no `local`) gains both columns and then the index; `PENDING_UPLOADS_DDL` on an empty file builds the index; the index build on a sink another connection holds open with a read waits and succeeds.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**  `CREATE INDEX IF NOT EXISTS`.  No switch check at `insert()`; no VACUUM.
- [ ] **Step 4: Mutations.**  Store the constant 0 → the `local=True` test fails.  Create the index before `ensure_columns` → the old-sink test fails.
- [ ] **Step 5: Run the hs-uploader suite; all pass.  Commit in hs-uploader** `sink writer: local= and the producer index (spec §5.1, §5.3, test 15)`.
- [ ] **Step 6: Refresh sigmond's bundled copy, by the recipe in sigmond's CLAUDE.md, never by hand.**  From `/home/mjh/hamsci/repos/sigmond`:

```bash
.venv/bin/pytest -q tests/test_hamsci_sink_compat.py   # must FAIL now: the copies differ
{ sed '/^# ---- END sigmond bundled-copy header/q' lib/sigmond/hamsci_sink/_bundled.py
  cat ../hs-uploader/src/hs_uploader/sink/writer.py; } > lib/sigmond/hamsci_sink/_bundled.py.new
mv lib/sigmond/hamsci_sink/_bundled.py.new lib/sigmond/hamsci_sink/_bundled.py
.venv/bin/pytest -q tests/test_hamsci_sink_compat.py   # must PASS
.venv/bin/pytest -q                                    # whole sigmond suite
git diff --stat lib/sigmond/hamsci_sink/_bundled.py    # only the body below the header moved
```

- [ ] **Step 7: Commit in sigmond** `hamsci_sink: refresh the bundled writer from hs-uploader <sha> (local=, producer index)` — name the hs-uploader commit.

**Review:** per-task pair, over both repos.  The quality reviewer confirms the header above the marker stayed byte-identical.

### Task 7: The switch library — reading

**Files:**
- Create: `hs-uploader/src/hs_uploader/sinkswitch.py`
- Test: `hs-uploader/tests/test_sinkswitch.py` (new)

**Interfaces:**
- Produces, stdlib only: `DEFAULT_SINKS_DIR = Path("/etc/hs-uploader/sinks")`; `STATES = ("off", "fill", "upload")` in strictness order; `CLIENT_NAME_RE`; `valid_client_name(name: str) -> bool`; `@dataclass SwitchRead(state: Optional[str], missing: bool, error: str)`; `read_switch(path: Path) -> SwitchRead`; `@dataclass Effective(state: str, client_state: Optional[str], site_state: Optional[str], errors: list[str])`; `effective_state(client: str, *, sinks_dir: Path = DEFAULT_SINKS_DIR) -> Effective`.  The file shape follows design §3.4.

- [ ] **Step 1: Write the failing tests** (spec test 17): missing client file reads `off`; missing site file sets no limit; a parse error reads `off` and reports the error; an unknown state reads `off`; the stricter of client and site wins in each of the nine pairs; a rewrite with a new mtime, size or inode shows at the next call; one error logs once per file signature, not once per call; `valid_client_name` refuses `../x`, `a@b`, `A`, `a.b`, `""`, `-a`.
- [ ] **Step 2: Run; all fail** (module absent).
- [ ] **Step 3: Implement.**  Cache each file on `(st_mtime_ns, st_size, st_ino)`.
- [ ] **Step 4: Mutations.**  Swap the order so `fill` ranks below `off` → the stricter-of test fails.  Read a parse error as a missing file → the parse-error test fails.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `sinkswitch: read switch files by design §3.4's rules (spec §6.1, test 17)`.

**Review:** per-task pair.  The spec reviewer checks the rules against design §3.4 itself, not only against the spec's summary.

### Task 8: The switch library — writing

**Files:**
- Modify: `hs-uploader/src/hs_uploader/sinkswitch.py`
- Test: `hs-uploader/tests/test_sinkswitch.py`

**Interfaces:**
- Produces: `dumps_toml(data: Mapping) -> str` (strings, booleans, integers, floats, arrays of tables, nested identity tables; anything else raises `TypeError`); `write_switch(path: Path, change: Mapping) -> None`.  `change` carries at least `state` plus the history fields design §3.4 names.

- [ ] **Step 1: Write the failing tests** (spec test 18): two processes (`multiprocessing`) race to write one file, and both history entries survive; a reader that loops during writes never parses half a file; `pinned = false` round-trips as `False`, never `"false"`; `path` naming `../site` or `A.toml` raises `ValueError`; a new file gets mode 0644; `dumps_toml` output parses back to the same data through `tomllib`.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**  `flock(LOCK_EX)` on `sinks/.lock`; re-read under the lock; append one `[[history]]`; write a temp file in the same directory; `fsync`; `os.replace`; `fsync` the directory.  Do not reuse sigmond's `_set_toml_field`.
- [ ] **Step 4: Mutations.**  Drop the re-read under the lock → the race test loses an entry.  Drop the name check → the `../site` test fails.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `sinkswitch: write switch files under a lock, atomically (spec §6.1, test 18)`.

**Review:** per-task pair.  The quality reviewer runs the race test ten times in a loop to check it holds.

### Task 9: The `pipelines.d/` loader

**Files:**
- Create: `hs-uploader/src/hs_uploader/pipelines_d.py`
- Modify: `hs-uploader/src/hs_uploader/core.py:188-215` (`Pipeline.governed`, `Pipeline.origin`)
- Modify: `hs-uploader/src/hs_uploader/pipeline_factory.py:124` (the `start_at` default by origin), `:236-287` (identity unchanged, reused)
- Modify: `hs-uploader/src/hs_uploader/daemon.py:80-142` (load and build through the loader; log one INFO line per pipeline with name, client and origin)
- Test: `hs-uploader/tests/test_pipelines_d.py` (new)

**Interfaces:**
- Consumes: `sinkswitch.valid_client_name` (Task 7).
- Produces: `pipelines_dir_for(manifest_path: Path) -> Path` (the manifest's parent / `pipelines.d`); `@dataclass Refusal(file: str, client: str, reason: str)`; `@dataclass Layout(manifest: Mapping, entries: list[tuple[Mapping, str]], refused: list[Refusal], signature: tuple)`, where each entry pairs with its origin, `"legacy"` or `"pipelines.d/<file>"`; `load_layout(manifest_path: Path) -> Layout`; `daemon.build_layout(layout, *, watermark) -> tuple[list[Pipeline], list[Refusal]]`; `Pipeline.governed: bool = True`; `Pipeline.origin: str = "legacy"`.

- [ ] **Step 1: Write the failing tests:**
  - Test 1 — no `pipelines.d`: the entries equal `load_manifest`'s, in order, `client` kept.
  - Test 2 — precedence: with `pipelines.d/psk-recorder.toml` present, none of psk-recorder's legacy entries build and all others do; a broken `psk-recorder.toml` still silences them; a legacy entry with no `client` (ND's shape) never matches a file.
  - Test 3 — `--manifest /tmp/x/pipelines.toml` reads `/tmp/x/pipelines.d`.
  - Test 4 — file rules: client from the file name; a mismatched `client` refuses the file; `../x`, `a@b`, `A` refused; `start_at = "now"` refused; a top-level `[daemon]` or `[identity]` with `ssh_key_file` refused; `[identity.<instance>]` loads with one INFO line; `wspr_cycle` defaults to `beginning` in a file and stays `now` in the legacy manifest; `.toml.new`, `.x.toml` and `x.toml~` never load; `governed = false` lands on the `Pipeline`.
  - Test 5 — collisions: a file that repeats a legacy key or a legacy name is refused, and the refusal names both pipelines; a collision inside the legacy manifest alone logs one ERROR naming both, and both run.
  - Test 8, start half — a file broken at start silences only its client and the daemon starts.
  - A file whose second entry fails to build sends nothing from its first entry.
- [ ] **Step 2: Run; all fail.**
- [ ] **Step 3: Implement.**  Base identity comes from the legacy manifest's `[identity]`, plus each entry's `[pipeline.identity]`, through the factory's existing path.  "Exists" means present on disk, parsed or not.
- [ ] **Step 4: Mutations.**  Drop `client` from legacy entries → test 1 fails.  Skip precedence → test 2 fails.  Fall back to legacy entries when the file fails → test 2's broken case fails.  Hard-code `/etc/hs-uploader/pipelines.d` → test 3 fails.  Drop the `"now"` refusal → test 4 fails.  Apply the `beginning` default to legacy entries → test 4 fails.  Remove the collision refusal → test 5 fails.  Make a legacy collision fatal → test 5 fails.  Let a parse error propagate → test 8 fails.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `daemon: load pipelines.d beside the legacy manifest, by precedence (spec §3.1-§3.3, §3.6, tests 1-5, 8)`.

**REVIEW GATE (loader):** spec reviewer and quality reviewer, then the controller confirms by hand that the daemon run against B4's copied `pipelines.toml` with `--dry-run` (if the daemon offers it; otherwise through `build_layout`) builds the same seven pipelines as 334e71e.

### Task 10: Rebuild on change, and the store check at each rebuild

**Files:**
- Modify: `hs-uploader/src/hs_uploader/core.py:232-339` (`Uploader.replace_pipelines`)
- Modify: `hs-uploader/src/hs_uploader/daemon.py:150-273` (signature check before each pump, SIGHUP, `split_refused` and `refresh_schema_version()` at each rebuild, `pipeline_state` rows for refused files, wake-socket bind when first needed)
- Test: `hs-uploader/tests/test_daemon_rebuild.py` (new)

**Interfaces:**
- Consumes: `load_layout`, `build_layout` (Task 9); `split_refused`; `record_pipeline_states(..., replace=True)`; `refresh_schema_version()` (Task 3).
- Produces: `Uploader.replace_pipelines(new: list[Pipeline]) -> None`, which closes each removed pipeline's source on that pipeline's own executor thread, shuts that executor down, and creates an executor for each added one.  `pipeline_state` gains one row named `pipelines.d/<file>` per refused file, with its client and reason.

- [ ] **Step 1: Write the failing tests** (spec tests 6, 7, 8 rebuild half):
  - A changed file rebuilds without a restart, within one pump.
  - An unchanged pipeline keeps the same `Pipeline` object (`is`) and the same executor.
  - A removed pipeline's source closes on its own thread, and its executor shuts down.
  - A queued retry for a pipeline that survives the rebuild still replays.
  - An identical rebuild changes nothing and logs one DEBUG line.
  - SIGHUP triggers a rebuild with no file change.
  - An edit to the legacy manifest alone, with or without SIGHUP, changes no running pipeline.
  - Test 7: a producer pipeline arriving by file on a version-1 store gets refused, and `pipeline_state` says why.
  - Test 8: a file broken on rebuild keeps its last good build, logs one ERROR per signature, and the daemon keeps pumping.
  - The send log follows the store version re-read at rebuild.
- [ ] **Step 2: Run; all fail.**
- [ ] **Step 3: Implement.**  The signature holds `(name, st_mtime_ns, st_size, st_ino)` for the directory and each file.  The rebuild runs between pumps only; `pump()` already joins every future.  Keep the legacy manifest's entries, `[identity]` and host-wide settings as read at start.  Bind the wake socket if the new set first needs it; never unbind while running.
- [ ] **Step 4: Mutations.**  Rebuild every pipeline each time → the `is` test fails.  Drop the stat check → the change test fails.  Re-read the legacy manifest at rebuild → the legacy-edit test fails.  Skip `split_refused` at rebuild → test 7 fails.
- [ ] **Step 5: Run the suite three times; all pass every time** (thread timing).
- [ ] **Step 6: Commit** `daemon: rebuild from pipelines.d between pumps; store check at each rebuild (spec §3.4, §3.5, tests 6-8)`.

**REVIEW GATE (rebuild):** the heaviest task.  Dispatch the quality reviewer on the most capable model, and ask it for thread-safety in `replace_pipelines`, file descriptors left open across many rebuilds, and the case where a rebuild arrives while a retry waits under a pipeline that the rebuild removes.

### Task 11: The switch report and `hs-uploader sink status`

**Files:**
- Modify: `hs-uploader/src/hs_uploader/daemon.py` (call `effective_state` at each build; log changes)
- Modify: `hs-uploader/src/hs_uploader/cli.py` (new verb `sink status [--json] [--sinks DIR]`)
- Test: `hs-uploader/tests/test_daemon_rebuild.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `effective_state` (Task 7); the built set (Tasks 9, 10).
- Produces: one INFO line per governed client when its answer changes, "sink switch for X would read Y (not enforced in this release)"; with no `sinks/` directory, one INFO line, "no switch files; the manifest's policy governs" (Ruling 5); `hs-uploader sink status` output as §6.2 states.

- [ ] **Step 1: Write the failing tests:** (test 19) a client file reading `off` leaves that client's pipeline built and shipping to a stand-in transport; the INFO line appears once, not at every pump; an unreadable switch file logs at ERROR once per signature; no `sinks/` gives the single INFO line; `sink status` and `sink status --json` print the site and each client that owns a pipeline or has a file, with file state, effective state, error and the "not enforced" line; with no `sinks/` it says so.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**  The pump never consults the switch.
- [ ] **Step 4: Mutation.**  Consult `effective_state` in `_pump_one` → test 19 fails.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `daemon: report what each sink switch would say, enforce nothing (spec §6.2, test 19)`.

**Review:** per-task pair.

### Task 12: `capabilities --features`

**Files:**
- Modify: `hs-uploader/src/hs_uploader/cli.py:125-129`, `:160-163`
- Test: `hs-uploader/tests/test_cli.py`

**Interfaces:**
- Produces: `FEATURES = ("pipelines.d", "send-log-v3", "switch-files")`; `hs-uploader capabilities --features` prints one word per line, sorted.  Bare `capabilities` keeps printing one integer.

- [ ] **Step 1: Write the failing tests** (test 20): `--features` prints exactly `pipelines.d\nsend-log-v3\nswitch-files\n`; bare output reads exactly `3\n`; feed the bare output to the parser v3.73 shipped (`int(stdout.strip())`) and get 3.
- [ ] **Step 2: Run; the `--features` test fails.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mutation.**  Print the features after the integer on the bare call → the bare test fails.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `capabilities: --features names what this hs-uploader can do (spec §7.1, test 20)`.

**Review:** per-task pair.  Lands only after Tasks 3, 9, 10 and 8 (Ruling 1).

### Task 13: The neutrality tool reads `--pipelines-d`

**Files:**
- Modify: `hs-uploader/tools/neutrality_check.py:279-392` (worker), `:598-610` (arguments)
- Test: `hs-uploader/tests/test_neutrality_check.py`

**Interfaces:**
- Consumes: `hs_uploader.pipelines_d.load_layout`, `daemon.build_layout` (Task 9).
- Produces: `--pipelines-d DIR` (optional, per tree side: `--new-pipelines-d` if the two sides need different layouts; decide in Step 3 and record it as a ruling).  The worker copies the manifest and the directory side by side into its temp dir, so the loader finds `pipelines.d` beside the manifest.  The worker calls `load_layout` when the tree has `hs_uploader.pipelines_d`, else `load_manifest`.  The report lists every refused file, and any refusal keeps the result from AGREE (Ruling 2).  `_TapStore` keeps its three overrides; their signatures stay.

- [ ] **Step 1: Write the failing tests** (test 22): a fixture tree with `pipelines.d` that matches the legacy layout ends AGREE; a planted difference (one moved entry with another `batch_limit`) ends DISAGREE; a stale legacy copy with an altered `batch_limit` ends AGREE when precedence holds; a refused file never ends AGREE.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Mutation.**  Ignore `--pipelines-d` → the planted-difference test ends AGREE, so it fails.
- [ ] **Step 5: Run the suite; all pass.**
- [ ] **Step 6: Commit** `neutrality_check: read pipelines.d, and refuse AGREE beside a refused file (spec §8.3, test 22)`.

**Review:** per-task pair.

### Task 14: sigmond's features probe

**Files:**
- Modify: `sigmond/lib/sigmond/uploader_manifest.py:443-465` (new function beside `hs_uploader_store_version`)
- Modify: `sigmond/lib/sigmond/commands/uploader.py:363` (print one line)
- Test: `sigmond/tests/test_uploader_features_probe.py` (new), `sigmond/tests/test_uploader_manifest_step.py`

**Interfaces:**
- Consumes: `hs-uploader capabilities --features` (Task 12).
- Produces: `hs_uploader_features(python: Path = HS_UPLOADER_PYTHON) -> Optional[frozenset[str]]` — exit 0 → the set of non-empty lines, unknown words kept; exit 2 with `unrecognized arguments` or `invalid choice` → `frozenset()`; any other failure or a timeout → `None`.  The manifest step prints `uploader: hs-uploader features: <words>` or `none`, and warns on `None`.  Nothing reads the answer.

- [ ] **Step 1: Write the failing tests** (spec tests 21, 23):
  - The three words give the set; an unknown fourth word stays in it.
  - Exit 2 with `unrecognized arguments: --features` gives `frozenset()`; exit 2 with `invalid choice` gives `frozenset()`.
  - A timeout gives `None`; exit 1 gives `None`.
  - A failed probe prints a warning, and the step's exit code and rendered manifest stay as without the probe.
  - Test 23: a fake hs-uploader whose `capabilities` prints 3 and whose `migrate --check` exits 3 gets stop, migrate, land, start in that order.
- [ ] **Step 2: Run; they fail** (test 23 may pass already; if it does, prove it by mutation in Step 4 and say so in the commit).
- [ ] **Step 3: Implement.**  Ask the daemon's own interpreter, as the store-version probe does.
- [ ] **Step 4: Mutations.**  Read exit 2 as `None` → the exit-2 tests fail.  Return 1 from the step on a failed probe → the warning test fails.  Land before migrate → test 23 fails.
- [ ] **Step 5: Run the sigmond suite; all pass.**
- [ ] **Step 6: Commit** `uploader manifest: ask hs-uploader for its features, print only (spec §7.2, tests 21, 23)`.

**REVIEW GATE (sigmond):** per-task pair, plus a check that the bare-probe path and its parser at `uploader_manifest.py:463` stayed byte-identical.

### Task 15: The update rig's assertions

**Files:**
- Modify: `sigmond-appliance/test-update-v3.sh` (PHASE E-bis, G-bis, G-ter)
- Test: `sigmond-appliance/test-update-upl-journal.sh`, `test-update-smd-lines.sh`, plus a new static test if a new parser appears

- [ ] **Step 1: Write the failing static test first** where the change adds parsing (for example, the probe that reads `PRAGMA user_version` and the step's `migrate` lines), fed lines in the real format, as `test-update-upl-journal.sh` does.
- [ ] **Step 2: Extend the rig.**
  - E-bis: after the manifest step, `migrate` reached version 3 before the daemon started; `PRAGMA user_version` reads 3; the step printed the features line.
  - G-bis: the rollback re-renders a manifest that matches the restored release; the restored daemon runs on the version-3 store, and the version stays 3.
  - G-ter: a second update finds nothing to fill; `migrate --check` exits 0.
- [ ] **Step 3: Run** `bash -n test-update-v3.sh` and each static test.  The rig runs end to end only on the rig (Task 19).  Record that in the ledger as owed.
- [ ] **Step 4: Commit** `test-update-v3: assert migration 3 on update and rollback (step 2b part 1)`.

**Review:** per-task pair.  The quality reviewer checks each exit code comes from a file or a variable, never from `cmd | tail` (the script's own header rule).

### Task 16: Docs

**Files:**
- Modify: `hs-uploader/README.md` and `hs-uploader/CLAUDE.md` (`pipelines.d/`, precedence, rebuild, migration 3, `last_send`, `local=`, `sink status`, `capabilities --features`)
- Modify: the sigmond docs page for `smd admin uploader manifest` (find it with `grep -rln "uploader manifest" sigmond/docs`), and sigmond's CLAUDE.md sink section if `local` now does something it did not
- Modify: `sigmond-appliance` docs only where they describe the manifest step's output

- [ ] **Step 1:** Describe the current behaviour only.  Short sentences, active voice.  Say plainly that no switch stops anything yet.
- [ ] **Step 2: Commit** in each repo `docs: sink control step 2b part 1`.

**Review:** one reviewer for prose accuracy against the code.

### Task 17: The proof on B4's and ND's copies (spec §8)

Devbox only.  No station.  The copies already exist; spec §8.2 and Michael's ruling Q3 are done.

**Inputs:** `/home/mjh/hamsci/proofs/sink-2b-copies-20261021/{b4,ac0g-nd}/` (`sink.db`, `watermarks.db`, `pipelines.toml`, `taken_at`, `store_version`, `SHA256SUMS`).

| | B4 | ND |
|---|---|---|
| taken | 2026-10-21T11:03:53Z | 2026-10-21T11:03:59Z |
| store version | 2, split keys | 1, shared `psk-pskreporter` key |
| hs-uploader running | 334e71e | 8376fd8 (v3.70) |
| manifest | seven pipelines, `client` on each | six pipelines, no `client` |

**Files:**
- Create: `/home/mjh/hamsci/proofs/sink-2b-part1-<date>/` (work dir, outside every repo); never write inside the copies directory
- Modify: nothing in a repo.  A defect found here goes back to its task as a fix round.

- [ ] **Step 0: Set up.**  `sha256sum -c SHA256SUMS` in each copy directory, before and after the whole task.  Extract trees: `git -C hs-uploader archive 334e71e | tar -x -C $W/old`, the same for the plan's head into `$W/new`, and for ND also `8376fd8` into `$W/nd-running`.  Confirm 334e71e against the last blessed build manifest's hs-uploader row and record it.  Each run passes `--now $(cat taken_at)`.
- [ ] **Run 1, as copied.**  Each station's manifest, no `pipelines.d`, OLD 334e71e against NEW.  Every pipeline AGREE.  For ND, also run `nd-running` against NEW and record it as informational: it covers the pairing ND would meet, an older hs-uploader beside a newer one (spec §10, row 2).
- [ ] **Run 2, send records moved back,** in copies only, by design §13.7's method: once by about half an hour, once to the oldest stored rows, for `psk.spots`, `wspr.spots` and `wspr.cycle`.  On B4 move both producer keys; on ND move the shared key.  Every pipeline with rows produces a batch, and every batch AGREE.  Record each batch's size and digest.
- [ ] **Run 3, the sink as this work leaves it.**  Open a fresh sink copy with the new writer's schema setup; time the index build.  Capture each source statement with `sqlite3.Connection.set_trace_callback` from a short script in `$W`.  Record `EXPLAIN QUERY PLAN` and run time for each, before and after the index.  Runs 1 and 2 must AGREE again on this copy.
- [ ] **Run 4, the store as this work leaves it.**
  - B4 (version 2): NEW `hs-uploader migrate --db <copy> --sink <sink copy>` reports migration 3.  A digest over every row of `watermarks`, `deliverables`, `dead_letter`, `superseded_keys` and `pipeline_state` matches before and after.  `attempts` keeps every old value; the new columns read `''`.  `migrate --check` before the run exits 3 and shows the same lines.
  - ND (version 1, Ruling 4): the migration adds the split keys, so the digest cannot match the start.  Compare two paths instead.  Path A: OLD 334e71e `migrate` (1 → 2), then NEW `migrate` (2 → 3); the five-table digest matches across the second step.  Path B: NEW `migrate` alone (1 → 3).  A and B must end with equal digests, leaving out `superseded_keys.migrated_at`, which records the clock.  Pass `--sink` and `--keep-retry-for psk-recorder-pskreporter` on both, as sigmond's step would.
  - Then OLD and NEW AGREE on each migrated store with the ND and B4 manifests.  OLD on a version-3 store stands for a rollback to the release before this work.
- [ ] **Run 5, the same pipelines from `pipelines.d`.**  In a scratch copy, move each client's legacy entries into `pipelines.d/<client>.toml` and drop `start_at` from each moved entry.  Leave the heartbeat in the legacy manifest (Q1).  B4 groups by each entry's `client`; ND groups by Ruling 3's name map.  NEW reading the split layout must AGREE with OLD reading the original manifest.  The tool must print a stored record for every moved pipeline, or the run says which key lacks one.  On ND, `psk-pskreporter` moves into `psk-recorder.toml` with no `producer`, which the v1 store accepts.
- [ ] **Run 5b, stale legacy copies (Ruling 2).**  As run 5, but each moved entry also stays in the legacy manifest with `batch_limit` doubled.  NEW must AGREE with OLD on the original manifest.
- [ ] **Run 6, the tool still catches a difference.**  A NEW tree with the `local = 0` clause inverted ends DISAGREE on run 2.  A NEW tree whose loader skips precedence fails run 5b.  A run that cannot fail proves nothing.
- [ ] **Step 7: Write the record.**  A `README.md` in `$W` lists every run, its command, its RESULT line and the batches compared, in the shape of design §13.7.  The final reviewer reads it.  The proof's summary goes into the ledger.  Michael decides where its text lands in the design doc.

**REVIEW GATE (proof):** a reviewer re-runs run 1 and run 6 from the README's commands alone and gets the same RESULT lines.

### Task 18: Final cross-repo review

- [ ] **Step 1: Dispatch one reviewer, on the most capable model,** over hs-uploader 334e71e..HEAD, sigmond 79dca3d..HEAD and sigmond-appliance 5da3a0c..HEAD, with the spec, this plan, the ledger and Task 17's README.  Ask for:
  - spec compliance, section by section, with §9's 23 tests mapped to test names and their recorded mutations;
  - every row of §10's version-pairing table walked against the code;
  - §11's inheritance list: each item v3.78 needs exists with the named interface;
  - anything that changes a byte a station sends today;
  - code quality across the seams: the loader to the rebuild, the store version to the send log, the writer to the bundled copy.
- [ ] **Step 2: Fix wave.**  Important findings get fixed, each by TDD with a mutation.  Minors get fixed or deferred in the ledger with a reason.  Re-run both suites and Task 17's runs 1 and 6 after the wave.
- [ ] **Step 3: Push**, once Michael clears it (see Global Constraints), and only fast-forward.

### Task 19: Rig runs, then the bench

These steps need a built image, so they come after the push.  Check first that no other build or test runs on the rig.

- [ ] **Step 1: Sync and build.**  On the rig (`root@192.168.1.182`, synced with `sync-rig.sh`, never a devbox), build the next image by `project_image_build_rig` practice.  Name it by the next free release number.
- [ ] **Step 2: Nested install test.**  `test-nested-v3.sh` on the new image through PHASE D.  Check by hand, and record, that the fresh store reads `PRAGMA user_version` 3 and that the manifest step printed `hs-uploader features: pipelines.d send-log-v3 switch-files`.
- [ ] **Step 3: Update-in-place test.**  `test-update-v3.sh` from the previous blessed image to the new head: E, E-bis, F, G, G-bis and G-ter must PASS with Task 15's assertions.  This run was owed since Task 15; record its log lines in the ledger.
- [ ] **Step 4: The bench on B4** (spec §8.5), only after an announcement on the claude-bus and Michael's go.  Record in `/etc/sigmond/CLAUDE-COORDINATION.md` on the VM if anything there changes.
  - First night on the new release, ND on its current release as the control: no loss against ND, no backward send record, a `last_send` row for every pipeline that sent, the same pskreporter, wsprnet and wd30 counts per hour as the night before.
  - The rebuild on a real unit: place `pipelines.d/bench.toml` with one `filetree` pipeline on an empty scratch directory and `discard = true`.  The daemon builds it within one pump with no restart and lists it in `pipeline_state`.  Break the file: the daemon keeps the old build and logs one ERROR.  Remove it: the daemon drops it.  The bench key never gets a record.
  - Remove the bench file and announce the end on the bus.

# v3.96 Plan C: who restarts the recorder, and who deletes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One sigmond watchdog and systemd restart `timestd-core-recorder`; every other agent reports.  sigmond's storage ledger sets every retention, including row limits for `timestd.db` and `authority_history.db`; hf-timestd's other cleaners retire or fall back to emergency use.

**Architecture:** Reporters (hf-timestd's pipeline and HPPS watchdogs, sigmond's SDR recovery and its radiod-consumer hook) write a small request file; `sigmond-recorder-watchdog` reads the requests, applies one fresh-start grace, one cooldown and one daily cap, acts, and writes every action to one restart ledger.  For retention, the ledger gains a fourth client verb, `storage expire`.  An hourly sigmond timer asks each client to delete what lies past the retention the ledger sets, and each client deletes its own rows or files.  A shared helper in sigmond's client kit deletes SQLite rows in short batches, so live writers never wait long.

**Tech Stack:** Python 3.11 stdlib (sigmond core, the client kit, `sqlite3`), bash (hf-timestd watchdog scripts), systemd timers, pytest.

**Spec:** `/home/mjh/hamsci/tasks/hf-timestd-audit/ASSESSMENT.md` (Michael's decisions 3, 6 and 7, 2026-11-23), with `part-A-recording.md` (cleaners A17, A22-A27), `part-B-metrology.md` (B28, the tables), `part-C-fusion-watchdogs.md` (C11, C25, C29, "Who restarts the recorder"), and `sigmond/docs/superpowers/specs/2026-11-04-storage-capacity-design.md` (the ledger's own rules: sigmond never deletes a client's files).

---

## What fits in v3.96, and what waits

Everything below fits one release: 16 tasks, 12 in sigmond and hf-timestd code that runs on stations, one in hamsci-physics, one rollout-and-measurement task.  Eleven tasks change something that runs unattended on a station (Tasks 2, 3, 4, 5, 7, 9, 10, 11, 12, 13, 14).  Tasks 6 and 8 ship a compaction tool that stops services only when an operator runs it.

These wait for a later release, with the reason:

- **The archive drive under the ledger.**  The archiver's `make_room` (archiver.py:356) stays as that drive's only cleaner, at its 90 % high-water mark.  Registering the drive needs a holdings listing that never touches a sleeping drive (storage_items.py `_archived_days` explains why); that design deserves its own task.
- **Small unbounded files in hf-timestd's `state/` and `wwvb/`** (the T6 anchor ledger, A34; the WWVB ledger, A38) and `t3_shadow/`'s own 14-day trim.  Each stays small; none blocks a station.
- **The pipeline watchdog's metrology and fusion rules.**  Decision 6 covers the recorder.  The metrology rule should read `status.json` (B25), and its "enabled but inactive" restarts erase the failed state that alarms need (C25), but dropping them before failures reach the heartbeat would leave a dead metrology unit dead.  See Open question 6.
- **Running the compaction on every station.**  v3.96 ships the tool and runs it on B4 only.

## Open questions for Michael

**Answered 2026-11-25: "As recommended"** (Michael, to V396-PLAN-REVIEW.md questions 11-18).  Each recommendation below now binds, including 30 days for `L3_dtec_timeseries` and `L3_dtec_diff` (question 3).  Task 10 is dropped: Plan B's Task 1 (hf-timestd a2d8b910) already stopped the archive writer deleting.

Each carries a recommendation.  The plan builds the recommendation as a named constant, so a different answer changes one line.

1. **How long do the timing tables in `timestd.db` keep rows?**  You set 30 days for the science tables (`L2_tick_phase`, `L1_all_arrivals`, `L2_test_signal`).  The timing tables (`L1_metrology_measurements`, `L2_timing_measurements`, `L2_tick_timing`, `L2_detection_attempts`, `L3_fusion_timing`, `DIAG_chrony_stats`) add about 50,000 rows a day together, against 2.2 million for the two big science tables.  I recommend 90 days: they hold the station's timing record, and they cost little.  Constant: `TIMING_DAYS` in Task 7.
2. **How long does `authority_history.db` keep rows?**  hfdl-recorder reads only rows from the last 5 minutes (`HISTORY_MAX_AGE_S = 300`, hfdl-recorder `timing/batch.py:134`); sigmond's T6 watchdog reads only the newest row; the TUI and station-web panels show history.  B4 holds 33 MB today.  I recommend 30 days.  Constant: `AUTHORITY_DAYS` in Task 7.
3. **hamsci-physics's L3 tables.**  hamsci-physics declares 365 days for its six L3 tables (contract.py:69-81), about 60 MB a day for the three dTEC tables, roughly 22 GB a year inside `timestd.db`.  The audit's question D-4 (which of these products anyone uses) stays open.  Task 14 honours the declared 365 days.  I recommend 30 days for `L3_dtec_timeseries` and `L3_dtec_diff` until you answer D-4.
4. **Does a stale HPPS refclock still restart the recorder?**  `hpps-watchdog.sh` restarts on chrony's LastRx for a `noselect` refclock, a downstream symptom; sigmond's own docstrings name it as the trap that cost B4 four restarts on 2026-08-29.  The T6-stuck rule covers the cause.  I recommend that the watchdog record HPPS requests and not act on them (`ACT_ON_HPPS = False`).  The cost: a wedged SHM push gate, as on bee1 2026-05-12, no longer heals itself.
5. **The shared restart budget.**  I recommend one cooldown of 30 minutes and a cap of 6 restarts a day across all causes, with two exemptions: a radiod restart underneath the recorder (it must follow radiod every time) and systemd's own restarts.  The T6 rule keeps its 20-minute stuck test and its 56 dB-Hz floor.
6. **The pipeline watchdog's non-recorder "enabled but inactive" restarts.**  I recommend keeping them in v3.96 and dropping them once `timestd-alert@` failures reach the heartbeat.
7. **Compaction across the fleet.**  Compaction stops every process that holds `timestd.db` (the recorder among them, so it costs one chunk) for the few minutes the swap takes.  I recommend B4 in v3.96, then one station at a time by an operator, announced, rather than automatically.
8. **Leftover tables.**  B4's database may hold tables from retired products (`L2_decoder_comparison`, `L1_bcd_timecode`, `L1_tone_detections`, `L1_broadcast_measurements`, `L1_channel_observables`, `L2_physics`, `L3b_absorption`, `L3b_iono_events`).  I recommend hf-timestd expire the first six at 30 days as its own retired products, and that the two `L3b_*` tables wait for hamsci-physics to claim them.  The B4 baseline in Task 16 lists what exists.

## Where the code differs from the audit

- **Six agents restart the recorder, not five.**  The audit missed `sigmond-radiod-consumers@.service`, which runs `smd admin radiod restart-stale-consumers` on every radiod start (bin/smd:31766).  That verb restarts every radiod consumer older than radiod's current start, hf-timestd included, and revives failed consumers (`_radiod_revive_failed_consumers`, bin/smd:31629).  Task 5 routes it through the watchdog with the others.
- **The ledger already silences two cleaners.**  `QuotaManager.enforce_quota` (quota_manager.py:462) and the guardian's headroom path (resource_guardian.py:417, 532) step aside whenever sigmond's status file says the ledger manages hf-timestd (`ledger_gate.py:24`).  On a ledger-managed station only four of the seven still delete: the guardian's 95 % path, the writer's own cleaner (BAW:1274), the legacy `archive_root` path, and the archiver.  The audit's table marks this ("Honors sigmond ledger?") but its summary reads as if all seven act at once.
- **The HPPS watchdog has an installer.**  Part C (C29) says no installer installs `timestd-hpps-watchdog`.  `scripts/install.sh` does not, but sigmond's `apply_deploy_toml_links` (installer.py:810-840) links every unit file in hf-timestd's `systemd/` directory, and deploy.toml lists the timer, so `smd` installs and enables it.  The same holds for `timestd-clock-monitor` (linked, not enabled).
- **The pipeline watchdog's recorder rule sits at lines 243-270**, as the audit says; its `do_restart` at 211 runs `reset-failed` before every restart.

---

## Global Constraints

- Every change ships in sigmond v3.96 through the normal release path (`smd align`); never a hand edit on a station.
- Before any change on AC0G-B4 (deploy, timers, services, a compaction), announce it on the claude-bus.  Canary order: B4 and sigma first, then the fleet.
- sigmond's core and its client kit stay stdlib-only (`sqlite3`, `os`, `json`, `subprocess`, `fcntl` only).
- sigmond never deletes a client's files or rows; a client deletes its own, when the ledger asks (storage design, "Who trims").
- No precise station positions anywhere in code, tests, logs or fixtures; use grid-square centres.
- Timing faults get exposed, never silently corrected.  Nothing in this plan touches a timing path.
- Science tables in `timestd.db` (`L2_tick_phase`, `L1_all_arrivals`, `L2_test_signal`): 30 days, enforced (Michael, decision 3).
- A freshly started recorder gets its first full chunk before any watchdog judges it: grace = 2 × 600 s + 120 s = 1320 s (worst case: a start just after a boundary waits up to 600 s for the next one, writes one full 600 s chunk, then flushes).
- Tests: sigmond runs `.venv/bin/pytest --basetemp=/var/tmp/mjh-pt/sigmond -o addopts=""`; hf-timestd and hamsci-physics run `.venv/bin/pytest --basetemp=/var/tmp/mjh-pt/<repo> -o addopts=""` from the repo root.
- Every new test must fail when the code it checks goes away.  Each test step names that mutation check.
- Commits stage named files only, and end with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z`
- A process that runs as root and opens a client's SQLite database first drops to the database file's owner, so it never creates a root-owned `-wal` or `-shm` file that the client's writers cannot open.

## Review Focus

1. **A root-owned `-wal`/`-shm`.**  sigmond calls client verbs as root.  If no writer holds `timestd.db` open at that moment, the first connection creates `timestd.db-wal` and `-shm`, and a root-owned pair locks the `timestd` writers out ("attempt to write a readonly database").  Expected: the verb drops to the file's owner first.  Pinned by `test_expire_drops_to_the_db_owner_before_opening` (Task 1) and `test_expire_target_runs_as_owner` (Task 7).
2. **A writer waiting on the pruner.**  The hamsci-dsp writers open with a 5 s busy timeout (sqlite_writer.py:162-166).  A prune transaction that holds the write lock longer drops a metrology batch.  Expected: each delete batch commits in well under a second and the pruner yields between batches.  Pinned by `test_prune_yields_to_a_writer_between_batches` (Task 1).
3. **Rows out of time order.**  The pruner finds its cut by binary search over rowid.  A row with a new timestamp at a low rowid must survive; a row with an old timestamp past the cut may wait a day.  Pinned by `test_prune_keeps_a_new_row_at_a_low_rowid` (Task 1).
4. **The watchdog against an operator's or the ledger's stop.**  An operator's `systemctl stop`, or the storage ledger's stop lever (`/var/lib/sigmond/storage/state.json` "stopped"), must hold.  Pinned by `test_operator_stop_is_never_undone` and `test_ledger_stop_is_never_undone` (Task 4).
5. **A radiod restart during the fresh-start grace.**  The grace protects a recorder that started after radiod settled.  A recorder that started before radiod's latest start holds a stale anchor whatever its age, and must follow radiod.  Pinned by `test_radiod_restart_overrides_the_grace` (Task 4).

---

## File Structure

sigmond (`/home/mjh/hamsci/repos/sigmond`):

- `lib/sigmond/storage/client_kit.py` (modify): the `expire` verb wiring, `prune_rows`, `incremental_vacuum`, `drop_to_owner`.
- `lib/sigmond/storage/clientcall.py` (modify): `call_expire`, and the three `compact` calls.
- `lib/sigmond/storage/retention.py` (create): the expire pass, its plan, state and status.
- `lib/sigmond/storage/config.py` (modify): `[retention]` overrides in `storage.toml`.
- `lib/sigmond/clients/base.py`, `lib/sigmond/clients/contract.py` (modify): the `expires` sink field.
- `lib/sigmond/commands/storage.py`, `bin/smd` (modify): `smd storage expire`, `smd storage compact`.
- `lib/sigmond/storage/compact.py` (create): orchestration of a client's compaction.
- `lib/sigmond/unit_retire.py` (create): disable and unlink retired units, the clients' and sigmond's own.
- `lib/sigmond/recorder_watchdog.py` (create): request files, the decision, the restart ledger.
- `bin/sigmond-recorder-watchdog` (create), `systemd/sigmond-recorder-watchdog.{service,timer}` (create).
- `bin/sigmond-t6-stuck-watchdog`, `systemd/sigmond-t6-stuck-watchdog.{service,timer}` (delete; logic moves).
- `bin/sigmond-sdr-recover`, `bin/smd` (`_radiod_restart_pass`) (modify): report, do not restart, the recorder.
- `systemd/sigmond-storage-expire.{service,timer}` (create).
- `install.sh`, `lib/sigmond/drift/units.py`, `lib/sigmond/update_build.py` (modify).
- `docs/CLIENT-CONTRACT.md` (modify): §17.7 additions in the v0.10 draft.

hf-timestd (`/home/mjh/hamsci/repos/hf-timestd`):

- `src/hf_timestd/storage_retention.py` (create): the table map and `expire_target`.
- `src/hf_timestd/storage_compact.py` (create): `holders`, `prepare`, `swap`.
- `src/hf_timestd/storage_items.py` (modify): SQLite files listed as pinned; new sinks; verbs wired.
- `src/hf_timestd/core/core_recorder_v2.py` (modify): the QuotaManager leaves the recorder.
- `src/hf_timestd/core/binary_archive_writer.py` (modify): the writer stops deleting.
- `src/hf_timestd/core/resource_guardian.py` (modify): emergency use only.
- `src/hf_timestd/cli.py` (modify): retired config keys.
- `scripts/pipeline-watchdog.sh`, `scripts/hpps-watchdog.sh` (modify): report, do not restart.
- `deploy.toml` (modify): `timestd-prune.timer` moves to `retired_units`.
- `systemd/timestd-prune.{service,timer}` (delete).

hamsci-physics (`/home/mjh/hamsci/repos/hamsci-physics`):

- `src/hamsci_physics/storage_expire.py` (create), `src/hamsci_physics/contract.py`, `src/hamsci_physics/cli.py` (modify).

---

## Task 1: The `expire` verb and the row pruner in sigmond's client kit

**Files:**
- Modify: `lib/sigmond/storage/client_kit.py`
- Modify: `docs/CLIENT-CONTRACT.md` (§17.7 and the v0.9 → v0.10 draft note, near lines 2203-2290 and 2750)
- Test: `tests/test_storage_client_kit.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class PruneResult: deleted: int; complete: bool; reason: str`
  - `prune_rows(db_path: str, table: str, ts_column: str, cutoff: str, *, deadline: float, batch_rows: int = 2000, pause_s: float = 0.05, busy_s: float = 0.25, clock=time.monotonic, sleep=time.sleep) -> PruneResult`
  - `incremental_vacuum(db_path: str, *, deadline: float, pages_per_step: int = 2048, clock=time.monotonic) -> int` (pages freed; 0 when `PRAGMA auto_vacuum` != 2)
  - `drop_to_owner(path: str) -> None` (no-op unless euid 0; then `os.setgroups([])`, `os.setgid(st_gid)`, `os.setuid(st_uid)` of `path`)
  - `add_expire_verb(subparsers, *, expire: Callable[[str, int, int], dict]) -> None`: adds `storage expire --target T --days N [--max-seconds S=600] --json`, creating the `storage` parser when absent.  `add_storage_verbs(..., expire=None)` calls it when given.
  - `cutoff_iso(now: float, days: float) -> str`: `"%Y-%m-%dT%H:%M:%S"` in UTC.
  - Answer shape of `expire`: `{"target": str, "days": int, "deleted": int, "unit": "rows"|"files", "complete": bool, "refused": bool, "reason": str}`.

- [ ] **Step 1: Write the failing tests**

```python
def _db(tmp_path, rows):  # rows: [(rowid, ts_or_None)]
    p = tmp_path / "t.db"
    con = sqlite3.connect(p)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE L2_tick_phase (channel TEXT, timestamp_utc TEXT)")
    con.execute("CREATE INDEX idx_L2_tick_phase_chan_ts ON L2_tick_phase (channel, timestamp_utc)")
    con.executemany("INSERT INTO L2_tick_phase(rowid, channel, timestamp_utc) VALUES (?, 'SHARED_10000', ?)", rows)
    con.commit(); con.close()
    return str(p)

def _day(n):  # n days before a fixed 'now'
    return (datetime(2026, 11, 25, tzinfo=timezone.utc) - timedelta(days=n)).isoformat()

NOW = datetime(2026, 11, 25, tzinfo=timezone.utc).timestamp()

def test_prune_deletes_rows_older_than_the_cutoff(tmp_path):
    db = _db(tmp_path, [(i, _day(40 - i)) for i in range(1, 41)])
    r = client_kit.prune_rows(db, "L2_tick_phase", "timestamp_utc",
                              client_kit.cutoff_iso(NOW, 30), deadline=time.monotonic() + 30)
    assert r.complete and r.deleted == 9          # days 39..31 old
    left = [t for (t,) in sqlite3.connect(db).execute("SELECT timestamp_utc FROM L2_tick_phase")]
    assert min(left) >= client_kit.cutoff_iso(NOW, 30)

def test_prune_keeps_a_new_row_at_a_low_rowid(tmp_path):
    db = _db(tmp_path, [(1, _day(1)), (2, _day(50)), (3, _day(45)), (4, _day(2))])
    client_kit.prune_rows(db, "L2_tick_phase", "timestamp_utc",
                          client_kit.cutoff_iso(NOW, 30), deadline=time.monotonic() + 30)
    left = {r for (r,) in sqlite3.connect(db).execute("SELECT rowid FROM L2_tick_phase")}
    assert 1 in left and 4 in left               # mutation check: drop the ts predicate

def test_prune_never_deletes_a_null_timestamp(tmp_path):
    db = _db(tmp_path, [(1, None), (2, _day(50))])
    r = client_kit.prune_rows(db, "L2_tick_phase", "timestamp_utc",
                              client_kit.cutoff_iso(NOW, 30), deadline=time.monotonic() + 30)
    assert r.deleted == 1

def test_prune_stops_at_the_deadline_and_says_incomplete(tmp_path):
    db = _db(tmp_path, [(i, _day(60)) for i in range(1, 5001)])
    r = client_kit.prune_rows(db, "L2_tick_phase", "timestamp_utc",
                              client_kit.cutoff_iso(NOW, 30), deadline=time.monotonic() - 1)
    assert r.deleted == 0 and r.complete is False

def test_prune_yields_to_a_writer_between_batches(tmp_path):
    """A writer with the hamsci-dsp 5 s timeout never waits more than one batch."""
    db = _db(tmp_path, [(i, _day(60)) for i in range(1, 20001)])
    waits = []
    def writer():
        con = sqlite3.connect(db, timeout=5.0, isolation_level=None)
        for _ in range(20):
            t0 = time.monotonic()
            con.execute("INSERT INTO L2_tick_phase(channel, timestamp_utc) VALUES ('X', ?)", (_day(0),))
            waits.append(time.monotonic() - t0)
            time.sleep(0.01)
    th = threading.Thread(target=writer); th.start()
    client_kit.prune_rows(db, "L2_tick_phase", "timestamp_utc", client_kit.cutoff_iso(NOW, 30),
                          deadline=time.monotonic() + 60, batch_rows=500)
    th.join()
    assert max(waits) < 1.0                       # mutation check: one DELETE for all rows

def test_a_held_write_lock_ends_the_pass_without_raising(tmp_path):
    db = _db(tmp_path, [(1, _day(60))])
    hold = sqlite3.connect(db, isolation_level=None); hold.execute("BEGIN IMMEDIATE")
    r = client_kit.prune_rows(db, "L2_tick_phase", "timestamp_utc",
                              client_kit.cutoff_iso(NOW, 30), deadline=time.monotonic() + 2)
    hold.execute("ROLLBACK")
    assert r.complete is False and "locked" in r.reason

def test_incremental_vacuum_frees_pages_only_in_incremental_mode(tmp_path): ...
    # auto_vacuum=0 → returns 0; a copy made with auto_vacuum=2 and rows deleted → > 0
    # mutation check: return 0 always

def test_expire_drops_to_the_db_owner_before_opening(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "setgroups", lambda g: calls.append(("groups", g)))
    monkeypatch.setattr(os, "setgid", lambda g: calls.append(("gid", g)))
    monkeypatch.setattr(os, "setuid", lambda u: calls.append(("uid", u)))
    p = tmp_path / "x.db"; p.write_bytes(b"")
    client_kit.drop_to_owner(str(p))
    st = p.stat()
    assert calls == [("groups", []), ("gid", st.st_gid), ("uid", st.st_uid)]

def test_expire_verb_parses_and_prints_the_answer(capsys): ...
    # parser from add_storage_verbs(..., expire=fake); argv storage expire --target a.b --days 30 --json
    # → fake called with ("a.b", 30, 600); stdout JSON equals fake's dict
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/pytest tests/test_storage_client_kit.py -k "prune or vacuum or expire" --basetemp=/var/tmp/mjh-pt/sigmond -o addopts=""`
Expected: FAIL with `AttributeError: module 'sigmond.storage.client_kit' has no attribute 'prune_rows'`.

- [ ] **Step 3: Implement**

`prune_rows` follows this algorithm, which the signature does not determine:

```
con = sqlite3.connect(db_path, timeout=busy_s, isolation_level=None)
lo, hi = SELECT min(rowid), max(rowid)            # empty table → PruneResult(0, True, "")
# boundary: the smallest rowid r with the first non-NULL ts at rowid >= r being >= cutoff,
# found by binary search over [lo, hi + 1) with
#   SELECT ts FROM t WHERE rowid >= ? AND ts IS NOT NULL ORDER BY rowid LIMIT 1
start = lo
while start < boundary and clock() < deadline:
    end = min(start + batch_rows, boundary)
    DELETE FROM t WHERE rowid >= ? AND rowid < ? AND ts < ?     # its own implicit transaction
    on sqlite3.OperationalError "locked"/"busy": sleep(pause_s), retry this window up to 5 times,
        then return PruneResult(deleted, False, "database locked")
    start = end; sleep(pause_s)
    every 50 batches: PRAGMA wal_checkpoint(PASSIVE)
return PruneResult(deleted, start >= boundary, "" or "deadline")
```

Quote table and column names with `"..."` after checking them against `^[A-Za-z0-9_]+$`; refuse anything else with `ValueError`.  `incremental_vacuum` loops `PRAGMA incremental_vacuum(pages_per_step)` and must call `.fetchall()` on each, or SQLite frees nothing.

Contract text, §17.7: a file or service sink MAY carry `"expires": true`, meaning the client answers `storage expire` for that target.  Document the verb, its answer shape, that sigmond sends `--days` from the sink's `retention_days` or from `storage.toml [retention]`, that the client deletes only its own data and stops at `--max-seconds`, that `complete: false` means "call again", and that a client running as root drops to the data's owner first.  Add the three optional `storage compact` verbs from Task 6 in the same section.  Note both under "v0.9 → v0.10 (draft)" as MAY.

- [ ] **Step 4: Run the tests and see them pass**

Same command.  Expected: PASS.  Then the whole storage suite: `.venv/bin/pytest tests/ -k storage --basetemp=/var/tmp/mjh-pt/sigmond -o addopts=""`, all PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/storage/client_kit.py tests/test_storage_client_kit.py docs/CLIENT-CONTRACT.md
git commit -m "storage kit: an expire verb, and a row pruner that never holds a writer up"
```

---

## Task 2: The ledger's retention pass (`smd storage expire`)

**Files:**
- Create: `lib/sigmond/storage/retention.py`
- Modify: `lib/sigmond/storage/clientcall.py`, `lib/sigmond/storage/config.py`, `lib/sigmond/clients/base.py:23-31`, `lib/sigmond/clients/contract.py:262-271`, `lib/sigmond/commands/storage.py`, `bin/smd` (storage parser near 35388-35419)
- Create: `systemd/sigmond-storage-expire.service`, `systemd/sigmond-storage-expire.timer`
- Modify: `install.sh` (beside the `sigmond-storage.timer` block), `lib/sigmond/drift/units.py` (`INSTALL_ENABLED_TIMERS`)
- Test: `tests/test_storage_retention.py`, `tests/test_storage_config.py`, `tests/test_storage_clientcall.py`

**Interfaces:**
- Consumes: Task 1's answer shape.
- Produces:
  - `DataSink.expires: bool = False`, parsed only from a JSON `true`.
  - `StorageConfig.retention: Mapping[str, int]`, keys `"<client>:<target>"`, values whole days ≥ 0 (0 means "never expire").  A bad key or value drops alone with one problem line.
  - `@dataclass(frozen=True) class ExpireAnswer: ok: bool; deleted: int = 0; complete: bool = False; refused: bool = False; reason: str = ""; unsupported: bool = False`
  - `call_expire(binary: str, target: str, days: int, max_seconds: int, run=subprocess.run) -> ExpireAnswer` (timeout `max_seconds + 60`; exit 2 → `unsupported`).
  - `@dataclass(frozen=True) class ExpireJob: client: str; target: str; days: int; source: str` (`"declared"` or `"storage.toml"`).
  - `plan_jobs(views: Mapping[str, ClientView], retention: Mapping[str, int]) -> list[ExpireJob]`: one job per sink with `expires` true and an effective `days > 0`, sorted by (client, target).
  - `run_expire_pass(now: datetime, r: ExpireRunners, *, config_path=CONFIG_PATH, state_path=EXPIRE_STATE, status_path=EXPIRE_STATUS, budget_s: int = 1500, per_job_s: int = 600) -> int` (0 ran; 75 another pass held the lock).
  - Paths: `EXPIRE_STATE = /var/lib/sigmond/storage/expire-state.json`, `EXPIRE_STATUS = /run/sigmond/storage-expire.json`, lock `/run/sigmond/storage-expire.lock` (its own lock, so the 2-minute ledger tick never waits on an expire).

- [ ] **Step 1: Write the failing tests**

```python
def test_plan_takes_declared_days_for_sinks_that_expire():
    views = _views({"hf-timestd": [_sink("service", "timestd.L2_tick_phase", 30, expires=True),
                                   _sink("file", "/var/lib/timestd/raw_buffer", 0, expires=False)]})
    assert retention.plan_jobs(views, {}) == [
        retention.ExpireJob("hf-timestd", "timestd.L2_tick_phase", 30, "declared")]

def test_storage_toml_overrides_and_zero_disables():
    views = _views({"hf-timestd": [_sink("service", "timestd.L2_tick_phase", 30, expires=True),
                                   _sink("service", "timestd.L1_all_arrivals", 30, expires=True)]})
    jobs = retention.plan_jobs(views, {"hf-timestd:timestd.L2_tick_phase": 14,
                                       "hf-timestd:timestd.L1_all_arrivals": 0})
    assert jobs == [retention.ExpireJob("hf-timestd", "timestd.L2_tick_phase", 14, "storage.toml")]

def test_a_sink_without_expires_is_never_called():   # mutation check: ignore the flag
    views = _views({"psk-recorder": [_sink("file", "/var/log/psk/spots.log", 14, expires=False)]})
    assert retention.plan_jobs(views, {}) == []

def test_pass_runs_jobs_in_turn_and_resumes_where_it_stopped(tmp_path):
    # budget allows one job per pass; three jobs; three passes call each once, in order,
    # and the fourth starts again at the first.  State carries "next_index".

def test_pass_journals_each_deletion_and_writes_status(tmp_path): ...
    # one journal line per job with deleted > 0 or a failure; status JSON lists every job
    # with days, source, deleted, complete, reason, last_run_utc

def test_a_job_incomplete_for_seven_days_raises_one_warning(tmp_path): ...
    # incomplete_since in state; one "<4>storage expire: ..." line when it passes 7 d,
    # none on later passes of the same episode; the episode clears on the first complete answer

def test_an_unsupported_client_is_skipped_for_a_day(tmp_path): ...

def test_config_retention_parses_and_drops_bad_entries():  # test_storage_config.py
    cfg = _load('[retention]\n"hf-timestd:timestd.L2_tick_phase" = 30\n"bad" = 3\n"x:y" = -1\n')
    assert cfg.retention == {"hf-timestd:timestd.L2_tick_phase": 30}
    assert len(cfg.problems) == 2

def test_call_expire_reads_the_answer_and_maps_exit_2():  # test_storage_clientcall.py
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/pytest tests/test_storage_retention.py tests/test_storage_config.py tests/test_storage_clientcall.py --basetemp=/var/tmp/mjh-pt/sigmond -o addopts=""`
Expected: FAIL, `ModuleNotFoundError: No module named 'sigmond.storage.retention'`.

- [ ] **Step 3: Implement**

`ExpireRunners` mirrors `tick.Runners`: `load_views`, `binary_for`, `call_expire`, `journal`, `clock`.  The pass takes the lock with `fcntl.flock(LOCK_EX | LOCK_NB)`, plans the jobs, and runs them from `next_index` while `budget_s` lasts, giving each `min(per_job_s, budget left)` seconds.  `smd storage expire` with `--dry-run` prints the plan, one line per job; without it, it needs root and runs one pass.  The status file carries `"schema": 1`.

Units:

```ini
# sigmond-storage-expire.service
[Service]
Type=oneshot
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=1800
ExecStart=/usr/local/bin/smd storage expire

# sigmond-storage-expire.timer
[Timer]
OnBootSec=900
OnCalendar=hourly
RandomizedDelaySec=300
[Install]
WantedBy=timers.target
```

The client process inherits the idle I/O class, so a long prune on a station gives way to the recorder.  Add the timer to `install.sh` and to `INSTALL_ENABLED_TIMERS`; `tests/test_align_enables_units.py` holds the two equal.

- [ ] **Step 4: Run the tests and see them pass**

Same command, then `.venv/bin/pytest tests/test_align_enables_units.py --basetemp=/var/tmp/mjh-pt/sigmond -o addopts=""`.  Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/storage/retention.py lib/sigmond/storage/clientcall.py lib/sigmond/storage/config.py \
  lib/sigmond/clients/base.py lib/sigmond/clients/contract.py lib/sigmond/commands/storage.py bin/smd \
  systemd/sigmond-storage-expire.service systemd/sigmond-storage-expire.timer install.sh \
  lib/sigmond/drift/units.py tests/test_storage_retention.py tests/test_storage_config.py tests/test_storage_clientcall.py
git commit -m "storage: the ledger sets each retention and asks clients, hourly, to expire their own data"
```

---

## Task 3: Retiring units on a station

**Files:**
- Create: `lib/sigmond/unit_retire.py`
- Modify: `lib/sigmond/update_build.py:58` (after `apply_deploy_toml_links`), `lib/sigmond/drift/units.py` (`install_own_units` and the drift items)
- Test: `tests/test_unit_retire.py`

**Interfaces:**
- Produces:
  - deploy.toml key `[systemd] retired_units = ["x.timer", "x.service"]`.
  - `RETIRED_OWN_UNITS: tuple` in `unit_retire.py`, starting with `("sigmond-t6-stuck-watchdog.timer", "sigmond-t6-stuck-watchdog.service")`.
  - `retire_units(units: Sequence[str], *, owned_dir: Optional[Path], unit_dir: Path = Path("/etc/systemd/system"), run=subprocess.run, dry_run: bool = False) -> list[str]` (messages).  For each unit: `systemctl disable --now <unit>` (a timer before its service), then remove `unit_dir/<unit>` only when it dangles or resolves inside `owned_dir` (a client repo) or, for sigmond's own, when it holds a plain copy sigmond installed; then one `systemctl daemon-reload`.  It never touches an operator's own file of that name.
  - `retired_from_deploy(repo_dir: Path) -> list[str]`.
  - Drift: a retired unit still enabled or present yields one drift item, fixed by `smd align --apply`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_retired_timer_is_disabled_then_its_link_removed(tmp_path):
    repo, unit_dir, calls = _setup(tmp_path, link={"timestd-prune.timer": "repo"})
    unit_retire.retire_units(["timestd-prune.timer"], owned_dir=repo, unit_dir=unit_dir, run=_rec(calls))
    assert ["systemctl", "disable", "--now", "timestd-prune.timer"] in calls
    assert not (unit_dir / "timestd-prune.timer").exists()

def test_a_file_outside_the_repo_is_left_alone(tmp_path):     # mutation check: drop the owner test
    repo, unit_dir, calls = _setup(tmp_path, link={"timestd-prune.timer": "elsewhere"})
    unit_retire.retire_units(["timestd-prune.timer"], owned_dir=repo, unit_dir=unit_dir, run=_rec(calls))
    assert (unit_dir / "timestd-prune.timer").is_symlink()

def test_a_dangling_link_goes(tmp_path): ...
def test_dry_run_changes_nothing(tmp_path): ...
def test_retired_from_deploy_reads_the_key(tmp_path): ...
def test_drift_names_a_retired_unit_still_enabled(): ...
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/pytest tests/test_unit_retire.py --basetemp=/var/tmp/mjh-pt/sigmond -o addopts=""`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement** as the Interfaces block says.  Call `retire_units(retired_from_deploy(repo_dir), owned_dir=repo_dir)` in `update_build.py` right after the links, and `retire_units(RETIRED_OWN_UNITS, owned_dir=None)` from `install_own_units`.

- [ ] **Step 4: Run the tests and see them pass**

Same command, plus `.venv/bin/pytest tests/ -k "drift or align" --basetemp=/var/tmp/mjh-pt/sigmond -o addopts=""`.  Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/unit_retire.py lib/sigmond/update_build.py lib/sigmond/drift/units.py tests/test_unit_retire.py
git commit -m "units: a release can retire a unit, and align disables and unlinks it"
```

---

## Task 4: `sigmond-recorder-watchdog`, the one restarter

**Files:**
- Create: `lib/sigmond/recorder_watchdog.py`, `bin/sigmond-recorder-watchdog`, `systemd/sigmond-recorder-watchdog.service`, `systemd/sigmond-recorder-watchdog.timer`
- Delete: `bin/sigmond-t6-stuck-watchdog`, `systemd/sigmond-t6-stuck-watchdog.service`, `systemd/sigmond-t6-stuck-watchdog.timer`
- Modify: `install.sh:983-992`, `lib/sigmond/drift/units.py` (`INSTALL_ENABLED_TIMERS`: replace the t6-stuck timer)
- Test: `tests/test_recorder_watchdog.py` (new); move every case of `tests/test_t6_stuck_watchdog.py` into it against `should_restart_t6`, then delete the old file.

**Interfaces:**
- Consumes: `RETIRED_OWN_UNITS` (Task 3) retires the t6-stuck units on stations.
- Produces (all in `lib/sigmond/recorder_watchdog.py`):
  - Constants: `UNIT = "timestd-core-recorder.service"`, `REQUEST_DIR = Path("/run/sigmond/recorder-restart")`, `LEDGER = Path("/var/lib/sigmond/recorder-restarts.jsonl")` (kept to its last 2000 lines), `STATE = Path("/run/sigmond/recorder-watchdog.json")`, `CHUNK_S = 600`, `GRACE_S = 2 * CHUNK_S + 120`, `COOLDOWN_S = 1800`, `MAX_PER_DAY = 6`, `REQUEST_MAX_AGE_S = 900`, `FAILED_REVIVE_S = 1800`, `ACT_ON_HPPS = False`, and the T6 values unchanged from the old script (`MIN_STUCK_SEC = 1200`, `MIN_CN0_DB_HZ = 56.0`).  Each reads an `RWD_*` environment override.
  - Causes: `"t6-stuck"` and `"failed"` (the watchdog's own rules); `"radiod-restarted"`, `"no-output"`, `"hpps-stale"` (from requests).
  - `file_request(reporter: str, cause: str, evidence: str, *, stopped_recorder: bool = False, request_dir: Path = REQUEST_DIR, now: Optional[float] = None) -> Path`: writes `<request_dir>/<reporter>.json` atomically (`.tmp` then `os.replace`), `{"schema": 1, "reporter", "cause", "requested_utc", "evidence", "stopped_recorder"}`.
  - `kick(run=subprocess.run) -> None`: `systemctl start --no-block sigmond-recorder-watchdog.service`.
  - `take_requests(request_dir: Path, now: float) -> list[Request]`: renames each file to `.taken`, reads it, unlinks it; drops one older than `REQUEST_MAX_AGE_S` with a reason.
  - `@dataclass(frozen=True) class T6State` (moved unchanged) and `should_restart_t6(s: T6State) -> bool` (moved unchanged).
  - `@dataclass(frozen=True) class UnitState: active_state: str; result: str; active_enter_mono: Optional[float]; inactive_enter_mono: Optional[float]; n_restarts: int`
  - `@dataclass(frozen=True) class Facts: now_wall: float; now_mono: float; recorder: UnitState; radiod_started_mono: Optional[float]; t6: Optional[T6State]; requests: tuple; ledger_stopped: bool; state: Mapping`
  - `@dataclass(frozen=True) class Decision: action: str; cause: Optional[str]; reason: str; refused: tuple`  (`action` in `"restart" | "start" | "none"`; `refused` holds `(Request, reason)` pairs).
  - `decide(f: Facts) -> Decision`, pure.

`decide` applies these rules in order:

1. The storage ledger stopped the recorder → `none`; refuse every request ("the storage ledger stopped it").
2. Recorder `inactive` with `result == "success"` and no `radiod-restarted` request with `stopped_recorder` → `none` (an operator's stop).
3. A `radiod-restarted` request, radiod active, and either the recorder started before `radiod_started_mono + READY_SETTLE_S` (`restart`) or the recorder sits inactive or failed after `stopped_recorder` (`start`).  Once per radiod start (`state["radiod_start_acted"]`); exempt from cooldown and cap.
4. Recorder `failed` for at least `FAILED_REVIVE_S` → `start` ("systemd gave up"), within the cap.
5. Recorder active and younger than `GRACE_S` → `none`; refuse what remains ("fresh start: first full chunk not yet written").
6. Inside the cooldown, or at the daily cap → `none`; refuse what remains.
7. `should_restart_t6(f.t6)` → `restart`, cause `t6-stuck`.
8. A `no-output` request → `restart`.
9. An `hpps-stale` request → `restart` only when `ACT_ON_HPPS`; else refuse ("report-only cause").

The bin script gathers the facts (`systemctl show -p ActiveState,Result,ActiveEnterTimestampMonotonic,InactiveEnterTimestampMonotonic,NRestarts`; radiod's unit from `/etc/sigmond/topology.toml [radiod] unit`; the recorder status and C/N0 exactly as the old script read them; `stopped` from `/var/lib/sigmond/storage/state.json`).  It takes `sigmond.lifecycle.lifecycle_lock(wait_s=60)` before acting and refuses the tick if it cannot.  For `start` it runs `systemctl reset-failed` then `systemctl start`; for `restart`, `systemctl restart`.  It appends one ledger row for every action, every refused request, and every rise in `NRestarts` it did not cause (`"agent": "systemd"`), so every restart of the recorder shows in one file.

- [ ] **Step 1: Write the failing tests**

```python
G = rw.GRACE_S
def _f(**kw):  # a healthy, old recorder; radiod older still; nothing requested
    base = dict(now_wall=1e9, now_mono=100_000.0,
                recorder=rw.UnitState("active", "success", 100_000.0 - 5 * G, None, 0),
                radiod_started_mono=1_000.0, t6=None, requests=(), ledger_stopped=False, state={})
    base.update(kw); return rw.Facts(**base)
def _req(cause, **kw): return rw.Request("pipeline-watchdog", cause, 1e9 - 10, "x", kw.get("stopped", False))

def test_fresh_recorder_is_not_judged():
    d = rw.decide(_f(recorder=rw.UnitState("active", "success", 100_000.0 - 400, None, 0),
                     requests=(_req("no-output"),)))
    assert d.action == "none" and "first full chunk" in d.refused[0][1]  # mutation check: drop rule 5

def test_grace_ends_after_two_chunks_and_a_flush():
    d = rw.decide(_f(recorder=rw.UnitState("active", "success", 100_000.0 - G - 1, None, 0),
                     requests=(_req("no-output"),)))
    assert d.action == "restart" and d.cause == "no-output"

def test_radiod_restart_overrides_the_grace():
    d = rw.decide(_f(recorder=rw.UnitState("active", "success", 99_900.0, None, 0),
                     radiod_started_mono=99_950.0, requests=(_req("radiod-restarted"),)))
    assert d.action == "restart" and d.cause == "radiod-restarted"

def test_radiod_restart_is_acted_on_once_per_radiod_start(): ...
def test_radiod_restart_starts_a_recorder_sdr_recover_stopped(): ...
def test_operator_stop_is_never_undone():
    d = rw.decide(_f(recorder=rw.UnitState("inactive", "success", None, 99_000.0, 0),
                     requests=(_req("no-output"),)))
    assert d.action == "none"
def test_ledger_stop_is_never_undone():
    d = rw.decide(_f(ledger_stopped=True, requests=(_req("radiod-restarted"),)))
    assert d.action == "none"
def test_failed_is_revived_only_after_the_wait(): ...      # 1799 s → none; 1800 s → start
def test_cooldown_and_cap_are_shared_across_causes(): ...  # a t6 restart 10 min ago holds a no-output request
def test_hpps_stale_is_recorded_not_acted_on(): ...        # mutation check: flip ACT_ON_HPPS
def test_take_requests_consumes_each_once_and_drops_stale(tmp_path): ...
def test_file_request_is_atomic_and_carries_schema_1(tmp_path): ...
def test_ledger_keeps_the_last_2000_lines(tmp_path): ...
# plus every case from tests/test_t6_stuck_watchdog.py, now against rw.should_restart_t6
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/pytest tests/test_recorder_watchdog.py --basetemp=/var/tmp/mjh-pt/sigmond -o addopts=""`
Expected: FAIL, `ModuleNotFoundError: No module named 'sigmond.recorder_watchdog'`.

- [ ] **Step 3: Implement** the module, the bin script (sys.path setup as `bin/sigmond-sdr-recover:134` does), and the units:

```ini
# sigmond-recorder-watchdog.service
[Unit]
Description=The one agent besides systemd that restarts timestd-core-recorder
After=timestd-core-recorder.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/sigmond-recorder-watchdog
Nice=10

# sigmond-recorder-watchdog.timer
[Timer]
OnBootSec=300
OnUnitActiveSec=60
AccuracySec=10s
[Install]
WantedBy=timers.target
```

In `install.sh`, replace the t6-stuck block (983-992) with the same steps for the new script and units.  Keep the old docstring's account of 2026-08-31 and its "why this stays narrow" section in the new module.

- [ ] **Step 4: Run the tests and see them pass**

Same command, then `.venv/bin/pytest tests/test_align_enables_units.py tests/ -k "watchdog or drift" --basetemp=/var/tmp/mjh-pt/sigmond -o addopts=""`.  Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/recorder_watchdog.py bin/sigmond-recorder-watchdog systemd/sigmond-recorder-watchdog.service \
  systemd/sigmond-recorder-watchdog.timer install.sh lib/sigmond/drift/units.py lib/sigmond/unit_retire.py \
  tests/test_recorder_watchdog.py
git rm bin/sigmond-t6-stuck-watchdog systemd/sigmond-t6-stuck-watchdog.service \
  systemd/sigmond-t6-stuck-watchdog.timer tests/test_t6_stuck_watchdog.py
git commit -m "recorder watchdog: one restarter beside systemd, one grace, one budget, one ledger"
```

---

## Task 5: sigmond's SDR recovery and radiod-consumer hook report instead of restarting the recorder

**Files:**
- Modify: `bin/sigmond-sdr-recover` (`restart_station`, line 1181)
- Modify: `bin/smd` (`_radiod_restart_pass`, 31659; `_radiod_revive_failed_consumers`, 31629)
- Test: `tests/test_sdr_recover.py`, `tests/test_radiod_consumer_hook.py`

**Interfaces:**
- Consumes: `file_request`, `kick`, `UNIT` (Task 4).
- Produces: `RECORDER_COMPONENT = "hf-timestd"` in the hook; neither path restarts or revives that component any more.

- [ ] **Step 1: Write the failing tests**

```python
def test_restart_station_files_a_request_for_the_recorder(monkeypatch, tmp_path):
    # consumers = ["timestd-core-recorder.service", "wspr-recorder@X.service"]
    # → systemctl restart issued for wspr-recorder only; a request file
    #   reporter "sdr-recover", cause "radiod-restarted", stopped_recorder True when
    #   _stop_station stopped it; one `systemctl start --no-block sigmond-recorder-watchdog.service`
    # mutation check: put the recorder back in the restart loop

def test_the_hook_files_a_request_and_restarts_hf_timestd_no_longer(): ...
    # a stale hf-timestd and a stale psk-recorder → _align_restart sees psk-recorder only;
    # request reporter "radiod-consumers", cause "radiod-restarted"; watchdog kicked

def test_the_hook_never_revives_hf_timestd(): ...
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/pytest tests/test_sdr_recover.py tests/test_radiod_consumer_hook.py --basetemp=/var/tmp/mjh-pt/sigmond -o addopts=""`
Expected: the three new tests FAIL; the rest pass.

- [ ] **Step 3: Implement.**  In `restart_station`, skip `UNIT`, file the request and kick the watchdog after radiod reports ready.  In the hook, drop `RECORDER_COMPONENT` from `stale` and from `declared` before restart and revival, and file one request when it was stale or failed.  Today the hook restarts every hf-timestd unit through `_align_restart`; afterwards the watchdog restarts the recorder alone.  The other hf-timestd units read the recorder's ring and files, not radiod.

- [ ] **Step 4: Run the tests and see them pass**

Same command.  Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/sigmond-sdr-recover bin/smd tests/test_sdr_recover.py tests/test_radiod_consumer_hook.py
git commit -m "sdr recovery and the radiod hook ask the recorder watchdog; they restart the recorder no longer"
```

---

## Task 6: `smd storage compact`, the orchestration

**Files:**
- Create: `lib/sigmond/storage/compact.py`
- Modify: `lib/sigmond/storage/clientcall.py`, `lib/sigmond/commands/storage.py`, `bin/smd` (storage parser)
- Test: `tests/test_storage_compact.py`

**Interfaces:**
- Consumes: Task 8's three verbs, `<client> storage compact --prepare|--holders|--swap --target T --json`, with answers `{"refused": bool, "reason": str, ...}`; `--holders` answers `{"holders": [{"pid": int, "unit": str|null}]}`.
- Produces: `compact_client(client: str, target: str, *, apply: bool, r: CompactRunners) -> int`, and the verb `smd storage compact <client> <target> [--apply]` (dry run by default).

Sequence: refuse under an active hands-off; `--prepare` (runs while every writer runs); `--holders`; take the lifecycle lock; stop the holder units in reverse `lifecycle.order_units` order, never `radiod*`, `ka9q-*` or `sigmond*`; refuse and restart them if a holder has no unit; `--swap`; start the same units in order whatever the swap answered; journal each step.  The dry run prints the prepare answer, the holder units and the sizes.

- [ ] **Step 1: Write the failing tests**

```python
def test_dry_run_stops_nothing(): ...
def test_apply_stops_holders_swaps_and_starts_them_in_order(): ...
def test_a_failed_swap_still_restarts_every_unit_it_stopped(): ...   # mutation check: return early
def test_a_holder_without_a_unit_refuses_before_stopping_anything(): ...
def test_hands_off_refuses(): ...
def test_radiod_is_never_stopped(): ...
```

- [ ] **Step 2: Run them and watch them fail.**  `.venv/bin/pytest tests/test_storage_compact.py --basetemp=/var/tmp/mjh-pt/sigmond -o addopts=""`; expect `ModuleNotFoundError`.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run them and see them pass.**
- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/storage/compact.py lib/sigmond/storage/clientcall.py lib/sigmond/commands/storage.py bin/smd tests/test_storage_compact.py
git commit -m "storage: smd storage compact stops a database's holders, swaps in the compact copy, starts them"
```

---

## Task 7: hf-timestd declares and enforces row retention

**Files:**
- Create: `src/hf_timestd/storage_retention.py`
- Modify: `src/hf_timestd/storage_items.py` (`SINK_CLASSES`, `list_items`, `sinks`, `add_verbs`)
- Test: `tests/unit/test_storage_retention.py`, `tests/unit/test_storage_items.py`

**Interfaces:**
- Consumes: kit `prune_rows`, `incremental_vacuum`, `drop_to_owner`, `cutoff_iso`, `add_storage_verbs(..., expire=)` (Task 1).
- Produces:
  - Constants: `SCIENCE_DAYS = 30` (decision 3), `TIMING_DAYS = 90` (Open question 1), `AUTHORITY_DAYS = 30` (Open question 2), `RETIRED_DAYS = 30` (Open question 8).
  - `TABLES: Dict[str, int]`, table → days:
    - science: `L2_tick_phase`, `L1_all_arrivals`, `L2_test_signal`, `L3_gnss_vtec` → `SCIENCE_DAYS`
    - timing: `L1_metrology_measurements`, `L2_timing_measurements`, `L2_tick_timing`, `L2_detection_attempts`, `L3_fusion_timing`, `DIAG_chrony_stats` → `TIMING_DAYS`
    - retired products: `L2_decoder_comparison`, `L1_bcd_timecode`, `L1_tone_detections`, `L1_broadcast_measurements`, `L1_channel_observables`, `L2_physics` → `RETIRED_DAYS`
  - `TARGETS: Dict[str, Tuple[str, str, str]]`, target → (db file relative to the data root, table, time column): `"timestd.<T>" → ("phase2/timestd.db", T, "timestamp_utc")` for each table above; `"authority_history.authority_snapshot" → ("authority_history.db", "authority_snapshot", "utc_published")`.
  - `retention_sinks() -> List[dict]`: one `{"kind": "service", "target", "schema_ref": "timestd:1", "retention_days", "mb_per_day": 0, "expires": True}` per target.  `sinks()` appends them.
  - `expire_target(data_root: Path, target: str, days: int, max_seconds: int, now: Optional[float] = None) -> dict`: refuses an unknown target (hamsci-physics's `L3_*` tables among them) and a missing database; else `drop_to_owner(db)`, `prune_rows`, and `incremental_vacuum` with the time left; answers in Task 1's shape with `"unit": "rows"`; writes one journal line through `storage_items._journal` when it deletes anything.
  - `list_items` lists each SQLite file (and its `-wal`, `-shm`) as one `pinned` item, so `smd storage status` counts `timestd.db` as hf-timestd's.  `delete_item` keeps refusing them.

The `L3_gnss_vtec` entry moves to hamsci-physics with VTEC (decision 10) in its own plan.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_science_table_keeps_30_days():
    for t in ("L2_tick_phase", "L1_all_arrivals", "L2_test_signal"):
        assert storage_retention.TABLES[t] == 30          # decision 3

def test_sinks_declare_every_target_with_expires_true(tmp_path):
    sinks = storage_items.sinks(tmp_path, 0)
    svc = {s["target"]: s for s in sinks if s["kind"] == "service"}
    assert svc["timestd.L2_tick_phase"]["retention_days"] == 30 and svc["timestd.L2_tick_phase"]["expires"]
    assert "authority_history.authority_snapshot" in svc

def test_expire_target_deletes_old_rows_of_its_own_table(tmp_path): ...   # 40 days of rows → 30 left
def test_expire_target_refuses_a_hamsci_physics_table(tmp_path):
    a = storage_retention.expire_target(tmp_path, "timestd.L3_dtec", 30, 60)
    assert a["refused"] and a["deleted"] == 0             # mutation check: accept any timestd.* name
def test_expire_target_prunes_authority_history_by_utc_published(tmp_path): ...
def test_expire_target_runs_as_owner(monkeypatch, tmp_path): ...  # drop_to_owner called with the db path first
def test_list_items_shows_timestd_db_as_pinned(tmp_path):
    (tmp_path / "phase2").mkdir(); (tmp_path / "phase2" / "timestd.db").write_bytes(b"x" * 10)
    items = storage_items.list_items(tmp_path)
    assert any(i.path.endswith("phase2/timestd.db") and i.cls == "pinned" for i in items)
def test_storage_expire_verb_is_wired(): ...   # cli `hf-timestd storage expire --target ... --json` prints the answer
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/pytest tests/unit/test_storage_retention.py tests/unit/test_storage_items.py --basetemp=/var/tmp/mjh-pt/hf-timestd -o addopts=""`
Expected: FAIL, `ModuleNotFoundError: No module named 'hf_timestd.storage_retention'`.  The sigmond kit loads from `SIGMOND_LIB`; point it at the local checkout with `monkeypatch.setattr(storage_items, "SIGMOND_LIB", "/home/mjh/hamsci/repos/sigmond/lib")` in a fixture.

- [ ] **Step 3: Implement** as the Interfaces block says.
- [ ] **Step 4: Run them and see them pass**, then the full hf-timestd suite: `.venv/bin/pytest tests --basetemp=/var/tmp/mjh-pt/hf-timestd -o addopts=""`.
- [ ] **Step 5: Commit**

```bash
git add src/hf_timestd/storage_retention.py src/hf_timestd/storage_items.py \
  tests/unit/test_storage_retention.py tests/unit/test_storage_items.py
git commit -m "storage: timestd.db and authority_history.db keep rows for set days; science tables 30"
```

---

## Task 8: hf-timestd compacts `timestd.db` (prepare, holders, swap)

**Files:**
- Create: `src/hf_timestd/storage_compact.py`
- Modify: `src/hf_timestd/storage_items.py` (`add_verbs`: the `compact` verb)
- Test: `tests/unit/test_storage_compact.py`

**Interfaces:**
- Consumes: kit `drop_to_owner` (Task 1); `TARGETS` (Task 7) to resolve `--target timestd` to the file.
- Produces:
  - `holders(db: Path, proc_root: Path = Path("/proc")) -> List[Tuple[int, Optional[str]]]`: every pid with an fd resolving to `db`, `db-wal` or `db-shm`, with its `.service` unit from `/proc/<pid>/cgroup`.
  - `prepare(db: Path, *, margin_bytes: int = 2 << 30, factor: float = 1.2, now: Optional[float] = None) -> dict`
  - `swap(db: Path, *, max_age_s: int = 86400, proc_root: Path = Path("/proc")) -> dict`

`prepare`, while every writer runs:
1. `live = (page_count − freelist_count) × page_size`; refuse unless the filesystem's free space ≥ `live × factor + margin_bytes`.
2. Remove a stale `<db>.compact` and its sidecar; `drop_to_owner(db)`.
3. On a fresh connection: `PRAGMA auto_vacuum=2`, then `VACUUM INTO '<db>.compact'`.  The copy comes out in incremental mode; the live file stays as it was (checked with SQLite 3.46.1 on the devbox).
4. Record each table's `max(rowid)` in the copy to `<db>.compact.json` with `prepared_utc`.
5. Answer `{"live_bytes", "file_bytes", "compact_bytes", "free_bytes", "seconds", "refused", "reason"}`.

`swap`, with every holder stopped:
1. Refuse while `holders(db)` lists anyone, or when the copy or its sidecar is missing or older than `max_age_s`.
2. `drop_to_owner(db)`.
3. Open the copy, `ATTACH` the live file, and for each live table copy the rows past the sidecar's `max(rowid)` (all rows for a table the copy lacks, created from `sqlite_master`).
4. Require `PRAGMA quick_check` = `ok` on the copy.
5. On the live file, `PRAGMA wal_checkpoint(TRUNCATE)`, close; unlink `db-wal` and `db-shm`; `os.replace(copy, db)`; restore the old file's mode, uid and gid; remove the sidecar.
6. Answer `{"before_bytes", "after_bytes", "rows_caught_up": {table: n}, "seconds", "refused", "reason"}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_prepare_refuses_without_room(tmp_path, monkeypatch): ...   # disk_usage free < live*1.2 + margin
def test_prepare_makes_an_incremental_copy_and_leaves_the_live_file(tmp_path):
    db = _big_db(tmp_path)
    storage_compact.prepare(db, margin_bytes=0)
    assert sqlite3.connect(f"{db}.compact").execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    assert sqlite3.connect(db).execute("PRAGMA auto_vacuum").fetchone()[0] == 0
def test_swap_refuses_while_a_holder_remains(tmp_path): ...          # fake proc_root with an fd link
def test_swap_catches_up_rows_written_after_prepare(tmp_path):
    db = _big_db(tmp_path); storage_compact.prepare(db, margin_bytes=0)
    _insert(db, "L2_tick_phase", 5)                                  # writers kept writing
    a = storage_compact.swap(db, proc_root=_empty_proc(tmp_path))
    assert a["rows_caught_up"]["L2_tick_phase"] == 5                 # mutation check: skip the catch-up
    assert sqlite3.connect(db).execute("PRAGMA auto_vacuum").fetchone()[0] == 2
def test_swap_keeps_owner_and_mode(tmp_path): ...
def test_holders_maps_pids_to_units(tmp_path): ...
```

- [ ] **Step 2: Run them and watch them fail.**  `.venv/bin/pytest tests/unit/test_storage_compact.py --basetemp=/var/tmp/mjh-pt/hf-timestd -o addopts=""`; expect `ModuleNotFoundError`.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run them and see them pass.**
- [ ] **Step 5: Commit**

```bash
git add src/hf_timestd/storage_compact.py src/hf_timestd/storage_items.py tests/unit/test_storage_compact.py
git commit -m "storage: compact timestd.db into an incremental-vacuum copy, then swap it in with writers stopped"
```

---

## Task 9: The QuotaManager leaves the recorder; `timestd-prune` retires

**Files:**
- Modify: `src/hf_timestd/core/core_recorder_v2.py:55, 1333-1358, 1370, 1408-1410, 7744-7754`
- Modify: `src/hf_timestd/cli.py` (`RETIRED_KEYS`, line 245)
- Modify: `deploy.toml:157-193` (move `"timestd-prune.timer"` from `units` to a new `retired_units = ["timestd-prune.timer", "timestd-prune.service"]`)
- Delete: `systemd/timestd-prune.service`, `systemd/timestd-prune.timer`
- Modify: `config/timestd-config.toml.template:45` (drop `storage_quota`), and the docs that name the prune timer (`grep -rln timestd-prune docs/`)
- Test: `tests/test_quota_not_in_recorder.py` (new), `tests/test_cli_validate_retired_keys.py`, `tests/test_deploy_unit_parsing.py`

**Interfaces:**
- Consumes: `retired_units` (Task 3).
- Produces: three retired keys, each saying "the sigmond storage ledger owns retention since v3.96": `('recorder', 'storage_quota')`, `('recorder', 'derived_max_days')`, `('recorder', 'archive_root')` (the last adds "use `smd archive add`").

- [ ] **Step 1: Write the failing tests**

```python
def test_the_recorder_builds_no_quota_manager():
    src = (REPO / "src/hf_timestd/core/core_recorder_v2.py").read_text()
    assert "QuotaManager" not in src and "_enforce_quota" not in src   # mutation check: restore either

def test_storage_quota_is_a_retired_key():
    issues = retired_key_issues({'recorder': {'storage_quota': '80%'}})
    assert len(issues) == 1 and 'storage ledger' in issues[0]['message']

def test_archive_root_is_a_retired_key_and_names_smd_archive(): ...
def test_deploy_retires_the_prune_timer():
    d = tomllib.loads((REPO / "deploy.toml").read_text())["systemd"]
    assert "timestd-prune.timer" in d["retired_units"] and "timestd-prune.timer" not in d["units"]
```

- [ ] **Step 2: Run them and watch them fail.**  `.venv/bin/pytest tests/test_quota_not_in_recorder.py tests/test_cli_validate_retired_keys.py tests/test_deploy_unit_parsing.py --basetemp=/var/tmp/mjh-pt/hf-timestd -o addopts=""`.
- [ ] **Step 3: Implement.**  Remove the import, the construction, the 300 s call and `_enforce_quota`.  The main loop then never blocks on a deletion, so the systemd watchdog pet no longer waits on one (A2).  Leave `quota_manager.py` for the manual `hf-timestd data clean-*` verbs; the dead-code sweep decides its fate.
- [ ] **Step 4: Run them and see them pass**, then the full suite.
- [ ] **Step 5: Commit**

```bash
git add src/hf_timestd/core/core_recorder_v2.py src/hf_timestd/cli.py deploy.toml config/timestd-config.toml.template \
  tests/test_quota_not_in_recorder.py tests/test_cli_validate_retired_keys.py tests/test_deploy_unit_parsing.py docs/
git rm systemd/timestd-prune.service systemd/timestd-prune.timer
git commit -m "retention: the recorder and the prune timer stop deleting; the ledger owns retention"
```

---

## Task 10: The archive writer stops deleting raw files

**Files:**
- Modify: `src/hf_timestd/core/binary_archive_writer.py:127, 1274-1360`
- Test: `tests/test_writer_never_deletes.py` (new)

**Interfaces:**
- Produces: `_check_disk_space(path: Path, required_bytes: int) -> bool` keeps its signature and its refusal: False when free space falls below `required_bytes + 100 MiB`, with one ERROR line per episode.  `_remove_oldest_files` and `storage_quota_percent` go.

- [ ] **Step 1: Write the failing tests**

```python
def test_writer_deletes_nothing_on_a_full_disk(tmp_path, monkeypatch):
    old = tmp_path / "20261101" / "x.bin.zst"; old.parent.mkdir(); old.write_bytes(b"x")
    os.utime(old, (0, 0))
    monkeypatch.setattr(shutil, "disk_usage", lambda p: _Usage(total=100, used=99, free=1))
    assert _writer(tmp_path)._check_disk_space(tmp_path / "20261125", 10) is False
    assert old.exists()                                   # mutation check: restore _remove_oldest_files

def test_writer_still_writes_with_room(tmp_path, monkeypatch): ...
```

- [ ] **Step 2: Run them and watch them fail.**  `.venv/bin/pytest tests/test_writer_never_deletes.py --basetemp=/var/tmp/mjh-pt/hf-timestd -o addopts=""`.
- [ ] **Step 3: Implement.**  A refused flush stays visible through the writer's existing `write_errors` counter.  Publishing that counter belongs to the plan for lost samples (Phase 1 item 4), not this one.
- [ ] **Step 4: Run them and see them pass**, then the archive-writer tests (`-k "archive or writer"`).
- [ ] **Step 5: Commit**

```bash
git add src/hf_timestd/core/binary_archive_writer.py tests/test_writer_never_deletes.py
git commit -m "archive writer: refuse a flush on a full disk; never delete raw files behind the ledger's back"
```

---

## Task 11: The resource guardian drops to emergency use

**Files:**
- Modify: `src/hf_timestd/core/resource_guardian.py` (constants near 85-92; `watchdog_check` 432-588; `_move_to_archive` 899, `_enforce_archive_limits` 920, `_evict_archive_date` 986, `_archive_is_mounted` 890; `from_config` 1103)
- Test: `tests/test_resource_guardian.py`

**Interfaces:**
- Produces:
  - `DISK_HARD_STOP_PERCENT = 95.0` stays for a station the ledger does not manage; `LEDGER_HARD_STOP_PERCENT = 98.0` applies while `ledger_manages_us()` reads true (the storage design: "that net moves to 98 %, above the ledger's final stage").  `hard_stop_percent(self) -> float` picks between them on each check.
  - `EVICT_LOCK = Path("/run/hf-timestd/guardian-evict.lock")`: an eviction takes it with `flock(LOCK_EX | LOCK_NB)`; a process that cannot take it skips eviction this pass and logs at DEBUG.  Six metrology processes then never evict at once.
  - The headroom path (`_ensure_headroom` and its branch) goes.  So do the day counts in the OK branch, which walk the data trees every 60 s.  `ResourceStatus.days_retained` and `days_headroom` read 0.
  - The `archive_root` parameter and every archive method go.  An evicted day gets deleted, never moved.

- [ ] **Step 1: Write the failing tests**

```python
def test_ledger_managed_station_holds_until_98(monkeypatch, tmp_path):
    g = _guardian(tmp_path, used_pct=96.0, ledger=True)
    assert g.watchdog_check(force=True).state == ResourceState.OK    # mutation check: one fixed 95

def test_unmanaged_station_still_stops_at_95(monkeypatch, tmp_path): ...
def test_only_one_process_evicts_at_a_time(tmp_path): ...             # lock held → bytes_cleaned == 0
def test_ok_branch_walks_no_tree(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(ResourceGuardian, "_collect_all_dates", lambda self: called.append(1) or set())
    _guardian(tmp_path, used_pct=50.0, ledger=False).watchdog_check(force=True)
    assert called == []
def test_there_is_no_headroom_eviction_any_more(): ...                 # hasattr(ResourceGuardian, "_ensure_headroom") is False
def test_an_evicted_day_is_deleted_never_moved(tmp_path): ...
```

Delete the tests that pin the archive move and the headroom eviction, naming each in the commit message.

- [ ] **Step 2: Run them and watch them fail.**  `.venv/bin/pytest tests/test_resource_guardian.py tests/test_archive_before_evict.py --basetemp=/var/tmp/mjh-pt/hf-timestd -o addopts=""`.
- [ ] **Step 3: Implement** as above.  Update the module docstring (lines 1-50) to say what the guardian now does and that the ledger owns retention.
- [ ] **Step 4: Run them and see them pass**, then the full suite.
- [ ] **Step 5: Commit**

```bash
git add src/hf_timestd/core/resource_guardian.py tests/test_resource_guardian.py tests/test_archive_before_evict.py
git commit -m "guardian: emergency use only — 98% under the ledger, one evictor, no headroom pass, no archive move"
```

---

## Task 12: The pipeline watchdog reports on the recorder

**Files:**
- Modify: `scripts/pipeline-watchdog.sh:211-270` (`do_restart`, `RECORDER_STALE`, `check_recorder`)
- Test: `tests/test_pipeline_watchdog_liveness.py`

**Interfaces:**
- Consumes: the request file format (Task 4): `{"schema": 1, "reporter", "cause", "requested_utc", "evidence", "stopped_recorder": false}`.
- Produces:
  - `REQUEST_DIR="${REQUEST_DIR:-/run/sigmond/recorder-restart}"`, `RECORDER_GRACE=1320`, and a shell function `request_recorder_restart <cause> <evidence>` that writes `$REQUEST_DIR/pipeline-watchdog.json` through a `.tmp` file and `mv`.
  - `check_recorder`: inactive → one warning line, no request (the watchdog's own "failed" rule covers systemd giving up).  Active and younger than `RECORDER_GRACE` (from `systemctl show -p ActiveEnterTimestampMonotonic --value` against the first field of `${UPTIME_FILE:-/proc/uptime}`) → no judgement.  Older with no output for `RECORDER_STALE` → request `no-output`.  It never restarts the recorder.  `--dry-run` prints `[DRY-RUN] Would request ...`.

- [ ] **Step 1: Write the failing tests** (extend the systemctl shim to answer `show -p ActiveEnterTimestampMonotonic --value` from `$RECORDER_ENTER_US`, and point `UPTIME_FILE` at a temp file)

```python
REC = "timestd-core-recorder.service"

def test_fresh_recorder_is_not_judged(env):
    _uptime(env, 10_000); out = _run(env, [REC], recorder_enter_s=10_000 - 400, newest_chunk_age=1100)
    assert "request" not in out.lower()                      # mutation check: drop the grace test

def test_stale_recorder_files_a_request_and_restarts_nothing(env):
    _uptime(env, 10_000); _run(env, [REC], recorder_enter_s=1_000, newest_chunk_age=1100, dry=False)
    req = json.loads((env["request_dir"] / "pipeline-watchdog.json").read_text())
    assert req["cause"] == "no-output" and req["schema"] == 1
    assert "restart timestd-core-recorder" not in env["systemctl_log"].read_text()

def test_inactive_recorder_is_reported_only(env): ...
```

- [ ] **Step 2: Run them and watch them fail.**  `.venv/bin/pytest tests/test_pipeline_watchdog_liveness.py --basetemp=/var/tmp/mjh-pt/hf-timestd -o addopts=""`.
- [ ] **Step 3: Implement.**  The metrology, fusion, physics and calibration checks stay as they are (Open question 6).
- [ ] **Step 4: Run them and see them pass.**
- [ ] **Step 5: Commit**

```bash
git add scripts/pipeline-watchdog.sh tests/test_pipeline_watchdog_liveness.py
git commit -m "pipeline watchdog: give a new recorder its first chunk, then report to sigmond; never restart it"
```

---

## Task 13: The HPPS watchdog reports instead of restarting

**Files:**
- Modify: `scripts/hpps-watchdog.sh:184-272` (`cooldown_active`, `record_restart`, the restart block in `main`)
- Modify: `systemd/timestd-hpps-watchdog.service` (the comments about restarts; `Environment=HPPS_REQUEST_DIR` default)
- Test: `tests/test_hpps_watchdog_refusal.py`

**Interfaces:**
- Consumes: the request file format (Task 4).
- Produces: in place of `systemctl restart`, the script writes `$HPPS_REQUEST_DIR/hpps-watchdog.json` (`cause` `hpps-stale`) and logs "requested a recorder restart from sigmond-recorder-watchdog".  Its own cooldown now limits requests, not restarts.  Every hold it has today (incurable refusal, producer withdrawal, T6 disabled) stays.

- [ ] **Step 1: Change the tests** (the `_Station` helper gains a `requests` property counting files in its request dir)

```python
def test_a_dark_hpps_with_no_refusal_files_a_request(self):
    self.st.publish(None); self.st.run()
    self.assertEqual(self.st.restarts, 0)                    # mutation check: restore systemctl restart
    self.assertEqual(self.st.requests, 1)
def test_an_incurable_refusal_holds_the_request(self): ...  # requests == 0
```

- [ ] **Step 2: Run them and watch them fail.**  `.venv/bin/pytest tests/test_hpps_watchdog_refusal.py --basetemp=/var/tmp/mjh-pt/hf-timestd -o addopts=""`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and see them pass.**
- [ ] **Step 5: Commit**

```bash
git add scripts/hpps-watchdog.sh systemd/timestd-hpps-watchdog.service tests/test_hpps_watchdog_refusal.py
git commit -m "hpps watchdog: report a stale HPPS to sigmond; restart the recorder no longer"
```

---

## Task 14: hamsci-physics expires its own L3 rows

**Files:**
- Create: `src/hamsci_physics/storage_expire.py`
- Modify: `src/hamsci_physics/contract.py:60-93`, `src/hamsci_physics/cli.py` (parser near 252)
- Test: `tests/test_storage_expire.py`

**Interfaces:**
- Consumes: kit `add_expire_verb`, `prune_rows`, `incremental_vacuum`, `drop_to_owner`, `cutoff_iso` (Task 1).  Load the kit as hf-timestd's `storage_items.load_kit` does: try the import, else append `/opt/git/sigmond/sigmond/lib` to `sys.path` and retry.
- Produces:
  - `OWN_TABLES = ("L3_physics", "L3_tec", "L3_dtec", "L3_dtec_timeseries", "L3_dtec_diff", "L3_tid", "L3C_propagation_stats")`, all with time column `timestamp_utc`.
  - `expire_target(data_root: Path, target: str, days: int, max_seconds: int) -> dict`, accepting only `timestd.<one of OWN_TABLES>`.
  - `contract._data_sinks` adds `"expires": True` to each table sink, and adds the missing `L3_physics` sink (365 days, `mb_per_day` 1).  The declared days stay 365 (Open question 3).
  - `hamsci-physics storage expire ...` exists even when the other storage verbs do not; with the kit unavailable it exits 2.

- [ ] **Step 1: Write the failing tests**

```python
def test_expires_its_own_table(tmp_path): ...               # 400 days of L3_dtec rows, days=365 → 35 deleted
def test_refuses_hf_timestds_table(tmp_path):
    a = storage_expire.expire_target(tmp_path, "timestd.L2_tick_phase", 30, 60)
    assert a["refused"]                                     # mutation check: accept any table
def test_every_table_sink_declares_expires():
    sinks = contract._data_sinks({}, Path("/var/lib/timestd"))
    assert all(s.get("expires") for s in sinks if s["kind"] == "service")
    assert any(s["target"] == "timestd.L3_physics" for s in sinks)
```

- [ ] **Step 2: Run them and watch them fail.**  `.venv/bin/pytest tests/test_storage_expire.py --basetemp=/var/tmp/mjh-pt/hamsci-physics -o addopts=""`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and see them pass**, then the full hamsci-physics suite.
- [ ] **Step 5: Commit**

```bash
git add src/hamsci_physics/storage_expire.py src/hamsci_physics/contract.py src/hamsci_physics/cli.py tests/test_storage_expire.py
git commit -m "storage: hamsci-physics expires its own L3 rows in timestd.db when sigmond's ledger asks"
```

---

## Task 15: Docs say who restarts and who deletes

**Files:**
- Modify: `sigmond/CLAUDE.md` (item 14, the storage ledger: one paragraph on `expire` and `compact`; one new short section, "Who restarts the recorder", naming systemd, the watchdog, the reporters, the request directory and the restart ledger)
- Modify: `hf-timestd/docs/ARCHITECTURE.md` and `hf-timestd/docs/DEBUGGING.md` (the cleaners and watchdogs as they now stand; `grep -n -i "prune\|quota\|guardian\|watchdog"` finds the passages)
- Modify: `sigmond/docs/superpowers/specs/2026-11-04-storage-capacity-design.md` ("Existing pieces to fold in": mark the hand-over done, and add retention by days through `expire`)

No tests; the docs check in each repo's CI runs links and anchors.  Write in short sentences, name each agent, and describe the current state only.

- [ ] **Step 1: Edit the files.**
- [ ] **Step 2: Run each repo's docs check** (hamsci-physics `c3f0240` added the shared one; run it as each repo's CI does) and fix what it flags.
- [ ] **Step 3: Commit** in each repo, staging the named files only.

---

## Task 16: Rollout and measurement on AC0G-B4

Every command here reads only, except the steps marked **change**, each announced on the claude-bus first.  Reach B4 only through `ops/bin/fleet-ssh b4 '<command>'`.  Keep each command light: no `dbstat`, no `COUNT(*)` over a large table, no full-tree walks beyond the audit's own `du`.

### Part 1: baseline, run before v3.96 reaches B4

- [ ] **Step 1: The audit's unit counters** (as in `tasks/hf-timestd-audit/b4/snapshot-20261123T1215Z.txt`), saved locally as `tasks/v396-plan-c/b4-before-<stamp>.txt`:

```bash
ops/bin/fleet-ssh b4 'date -u +%FT%TZ; cat /proc/uptime;
  for u in $(systemctl list-units --all --plain --no-legend "timestd-*" "hamsci-physics-*" "sigmond-*" "radiod@*" | awk "{print \$1}"); do
    echo "$u $(systemctl show -p NRestarts,MemoryCurrent,MemoryPeak,CPUUsageNSec,ActiveState,ActiveEnterTimestampMonotonic --value $u | tr "\n" " ")"; done;
  sudo du -sm /var/lib/timestd/* 2>/dev/null'
```

- [ ] **Step 2: Who restarted the recorder in the last 24 h**

```bash
ops/bin/fleet-ssh b4 'S="-24 hours";
  echo starts $(journalctl --since "$S" -u timestd-core-recorder.service | grep -c "Started ");
  echo pipeline $(journalctl --since "$S" -t timestd-watchdog | grep -c "Restarting timestd-core-recorder");
  echo hpps $(journalctl --since "$S" -t hpps-watchdog | grep -c "restarting timestd-core-recorder");
  echo t6stuck $(journalctl --since "$S" -u sigmond-t6-stuck-watchdog.service | grep -c "restarting timestd-core-recorder");
  echo sdr $(journalctl --since "$S" -u sigmond-sdr-recover.service | grep -c "restarting consumer timestd-core-recorder");
  echo hook $(journalctl --since "$S" -u "sigmond-radiod-consumers@*" | grep -c "hf-timestd: started before")'
```

and the restarts that came within 1320 s of the previous start (the "killed before its first chunk" pattern), from the start times:

```bash
ops/bin/fleet-ssh b4 'journalctl --since "-24 hours" -u timestd-core-recorder.service -o short-unix | awk "/Started /{t=int(\$1); if (p && t-p < 1320) n++; p=t} END{print \"early_restarts\", n+0}"'
```

- [ ] **Step 3: Who deleted what in the last 24 h**

```bash
ops/bin/fleet-ssh b4 'S="-24 hours";
  echo ledger_trims $(journalctl --since "$S" -t hf-timestd-storage | wc -l);
  echo recorder_quota $(journalctl --since "$S" -u timestd-core-recorder.service | grep -c "Quota: deleted");
  echo prune_timer $(journalctl --since "$S" -u timestd-prune.service | grep -c -i "deleted");
  echo guardian $(journalctl --since "$S" -u "timestd-metrology@*" | grep -c -E "evicted|Headroom low");
  echo writer $(journalctl --since "$S" -u timestd-core-recorder.service | grep -c "removed oldest files");
  smd storage status | head -40'
```

- [ ] **Step 4: The two databases, read-only and cheap**

```bash
ops/bin/fleet-ssh b4 'cd /var/lib/timestd; ls -l --time-style=+%FT%T phase2/timestd.db* authority_history.db*;
  sqlite3 -readonly phase2/timestd.db "PRAGMA page_size; PRAGMA page_count; PRAGMA freelist_count; PRAGMA auto_vacuum;";
  for t in $(sqlite3 -readonly phase2/timestd.db "SELECT name FROM sqlite_master WHERE type=\"table\""); do
    echo "$t $(sqlite3 -readonly phase2/timestd.db "SELECT max(rowid)-min(rowid)+1 FROM \"$t\"") $(sqlite3 -readonly phase2/timestd.db "SELECT timestamp_utc FROM \"$t\" ORDER BY rowid LIMIT 1" 2>/dev/null)"; done;
  sqlite3 -readonly authority_history.db "SELECT max(rowid)-min(rowid)+1, (SELECT utc_published FROM authority_snapshot ORDER BY rowid LIMIT 1) FROM authority_snapshot"'
```

The rowid span stands in for a row count (an upper bound); the first row by rowid gives the oldest date.  Record which tables exist, for Open question 8.

### Part 2: rollout

- [ ] **Step 5 (change): v3.96 to B4 and sigma** through the release path (`smd align --apply`), announced on the bus.  The align restarts hf-timestd's units, as every deploy does.  Confirm afterwards: `systemctl is-enabled sigmond-recorder-watchdog.timer sigmond-storage-expire.timer` reads `enabled` twice; `systemctl is-enabled sigmond-t6-stuck-watchdog.timer timestd-prune.timer` reads `not-found` or `disabled`; `smd storage expire --dry-run` lists every hf-timestd and hamsci-physics target with its days.
- [ ] **Step 6: Watch the first expire passes.**  Each hour: `ops/bin/fleet-ssh b4 'cat /run/sigmond/storage-expire.json; journalctl --since "-1 hour" -t hf-timestd-storage | tail -5'`.  The backlog on B4 (29 GB, most of it past 30 days) takes several hours of 10-minute passes; each job reports `complete: true` when it catches up.  If metrology logs "database is locked" while a pass runs, stop the timer (announced), and halve `batch_rows`.
- [ ] **Step 7 (change, optional, Open question 7): compact B4's database** once every job reports complete.  Run `smd storage compact hf-timestd timestd` first (dry run: sizes and the units it would stop), then with `--apply`, announced on the bus.  It stops each process holding the file, the recorder among them, for the swap.

### Part 3: after, 24 h past the rollout

- [ ] **Step 8: Repeat Steps 1-4**, saved as `b4-after-<stamp>.txt`, and add:

```bash
ops/bin/fleet-ssh b4 'tail -n 50 /var/lib/sigmond/recorder-restarts.jsonl; cat /run/sigmond/recorder-watchdog.json'
```

- [ ] **Step 9: Judge against these checks**, and record the result in the ops ledger item for this plan (it stays open until every check passes on B4, then on sigma):
  1. No agent but systemd and `sigmond-recorder-watchdog` restarted the recorder: the pipeline, hpps, t6stuck, sdr and hook counts all read 0.
  2. `early_restarts` reads 0, unless a `radiod-restarted` row in the restart ledger explains each one.
  3. Every recorder start in the journal has a matching row in `recorder-restarts.jsonl` (agent `systemd` or `sigmond-recorder-watchdog`).
  4. `recorder_quota`, `prune_timer`, `guardian` and `writer` read 0; every deletion appears under `hf-timestd-storage`.
  5. The oldest science row in `timestd.db` lies no more than 31 days back; the oldest timing row no more than `TIMING_DAYS` + 1; the oldest `authority_snapshot` row no more than `AUTHORITY_DAYS` + 1.
  6. `page_count − freelist_count` holds steady from day to day; the file grows no further.  After a compaction, the file stands near `live_bytes` from the prepare answer.
  7. The recorder's and each metrology unit's `CPUUsageNSec` over 24 h and their `MemoryPeak` stay within 10 % of the baseline; the expire service's own CPU over 24 h stays under 15 minutes.

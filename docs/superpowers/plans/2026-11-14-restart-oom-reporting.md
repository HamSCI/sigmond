# Restart and OOM Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A self-healing failure is never silent: `smd check`, `smd status` and the heartbeat report each service's restarts and memory (OOM) kills over the last 24 h, and how close each runs to its memory cap.

**Architecture:** A root timer (every 5 min) reads the system journal as JSON (PID 1 and kernel messages only), counts per-unit events over 24 h, reads each unit's cgroup memory peak and cap, and writes `/run/sigmond/service-events.json`. `smd check`, `smd status` and the heartbeat read that file; none of them read the journal (hamsci cannot read kernel or PID 1 lines).

**Tech Stack:** Python 3.11, journalctl `-o json`, cgroup v2 files, systemd timer, pytest.

**Spec:** the design Rob was shown on 2026-11-12 (this session; ledger item `restart-oom-reporting`): per-unit 24 h counts from the journal; memcg OOM kills, whole-machine OOM kills and automatic restarts counted, manual restarts shown apart; memory peak against MemoryMax with a warning above 85 %; whole-machine OOM loud. Motivating cases: AI6VN-1 11-12 (psk-recorder memcg kill at 23:06:47 that NRestarts and memory.events hid after manual restarts; the 195-kill global OOM storm the boot before) and N8UR 11-14 (timestd-core-recorder OOM-killed every 10 min).

## Global Constraints

- Window: 24 h.  Status file `/run/sigmond/service-events.json`, root-owned, mode 0644, with `generated_at` (UTC ISO 8601 `Z`).  Stale after 900 s.
- `smd check` line `services` (id `platform.services`, stage `platform`): **FAIL** on any whole-machine OOM kill in the window; **WAIT** on any memcg OOM kill, more than 3 automatic restarts of one unit, or a unit whose memory peak is above 85 % of its MemoryMax; **OK** otherwise, naming the count of manual restarts.  Text ASCII only (the check renders on the Proxmox console).
- Journal message ids (systemd, stable): restart scheduled `5eb03494b6584870a536b337290809b3`; unit stopping `de5b426a63be47a7b6ac3eaac82e2f6f` (field `JOB_TYPE`); process exited `98e322203f7a4ed290d09fe03c09fe15` (`EXIT_CODE`, `EXIT_STATUS`); unit failed `d9b373ed55a64feb8242e02dbe79a49c` (`UNIT_RESULT`).  Kernel lines: `_TRANSPORT=kernel`, MESSAGE beginning `oom-kill:constraint=CONSTRAINT_MEMCG` (memcg) or `oom-kill:constraint=CONSTRAINT_NONE` (whole machine), unit from the `task_memcg=/system.slice/<unit>` field.
- A manual restart is a `stopping` job (JOB_TYPE `restart` or `stop`) for a unit with no restart-scheduled or process-exited event for it in the 5 s before.  An automatic restart is a restart-scheduled event.
- smd raises itself to root; the new admin verb calls `_need_root`.  Never `sudo smd` in text.
- sigmond only (no client repo changes).  Commit to main; never push during a b3 build.

## Review Focus

1. A journal with no JSON field for a unit (kernel line whose task_memcg is `/` or a user slice): counted under whole-machine kills with unit `-`, never a crash.  → Task 1 `test_kill_outside_system_slice`.
2. journalctl missing, slow or failing (rc != 0, timeout 60 s): the collector writes a status with `error` set and empty counts; check says WAIT "service events could not be read: ...".  → Task 2 `test_collect_journal_error`.
3. A template instance (`psk-recorder@KPH1.service`) and an escaped one (`psk-recorder@KFS\x3dOMNI.service`): counted under the exact unit name given by the journal.  → Task 1 `test_template_units_kept_apart`.
4. MemoryMax `max` (no cap): headroom not computed, never reported as over 85 %.  → Task 2 `test_no_cap_no_headroom`.
5. A stale or missing status file (timer not yet run, fresh install): check WAITs with "not collected yet", heartbeat sends `services: null` (INDETERMINATE), never VALID.  → Tasks 3 and 4.

---

### Task 1: Parse journal events into per-unit counts

**Files:** Create `lib/sigmond/service_events.py`; Test `tests/test_service_events.py`, fixtures `tests/fixtures/journal/*.jsonl`

**Interfaces — Produces:**
- `@dataclass UnitEvents: auto_restarts: int = 0; manual_restarts: int = 0; memcg_oom: int = 0; unexpected_exits: int = 0; last_utc: Optional[str] = None`
- `parse_events(lines: Iterable[str], now: float, window_s: int = 86400) -> Tuple[Dict[str, UnitEvents], List[dict]]` — the second item lists whole-machine OOM kills as `{"utc": str, "unit": str, "task": str}`.

- [ ] **Step 1: Fixtures and failing tests.**  Fixtures (one JSON object per line, as `journalctl -o json` prints): `memcg_kill.jsonl` (kernel `oom-kill:constraint=CONSTRAINT_MEMCG,...,task_memcg=/system.slice/psk-recorder@AI6VN-1.service,task=python3,...` + PID 1 process-exited `EXIT_CODE=killed EXIT_STATUS=9` + unit-failed `UNIT_RESULT=oom-kill` + restart-scheduled), `global_oom.jsonl` (three `CONSTRAINT_NONE` kills: sshd.service, radiod@AI6VN-1.service, `task_memcg=/`), `manual.jsonl` (stopping JOB_TYPE=restart with no prior exit; then a crash: process-exited status 1 then restart-scheduled then stopping 1 s later).  Before writing them, capture one real sample with `sudo journalctl -o json -n 200 _PID=1 + _TRANSPORT=kernel` on N8UR or any station that logged an OOM, and match field names to it.  Tests: `test_memcg_kill_counted` (psk unit: memcg_oom 1, auto_restarts 1, unexpected_exits 1), `test_global_oom_listed` (3 entries; units `sshd.service`, `radiod@AI6VN-1.service`, `-`), `test_manual_vs_automatic` (manual 1, auto 1 — the stopping 1 s after the scheduled restart is not manual), `test_window` (an event 25 h old is not counted), `test_kill_outside_system_slice`, `test_template_units_kept_apart`, `test_bad_lines_ignored` (non-JSON line, missing fields).
- [ ] **Step 2:** run → FAIL (module missing).
- [ ] **Step 3:** implement; timestamps from `__REALTIME_TIMESTAMP` (µs); unit from `UNIT` (PID 1) or `task_memcg` (kernel).
- [ ] **Step 4:** PASS.  **Step 5:** commit `service events: count restarts and OOM kills per unit from the journal`.

### Task 2: Collector, memory headroom, status file, timer

**Files:** Modify `lib/sigmond/service_events.py`; Create `systemd/sigmond-service-events.service` (oneshot, root, `ExecStart=/usr/local/bin/smd admin service-events collect`, `Nice=10`), `systemd/sigmond-service-events.timer` (`OnBootSec=2min`, `OnUnitActiveSec=5min`); Modify `bin/smd` (admin verb `service-events collect`, `_need_root`), `install.sh` only if sigmond-*.timer enabling needs it (it installs them by pattern; enable this one); Test `tests/test_service_events.py`

**Interfaces — Produces:**
- `memory_headroom(units: Iterable[str], cgroup_root: Path = Path("/sys/fs/cgroup/system.slice")) -> Dict[str, Tuple[int, Optional[int]]]` — (memory.peak bytes, memory.max bytes or None for `max`).
- `collect(now: float, run=subprocess.run, cgroup_root=...) -> dict` — the status document: `{"generated_at", "window_h": 24, "units": {unit: {...UnitEvents, "peak_bytes", "max_bytes", "peak_pct"}}, "global_oom": [...], "error": Optional[str]}`; `units` lists every unit with an event plus every running sigmond-managed service with a cap.
- `write_status(doc: dict, path: Path = Path("/run/sigmond/service-events.json")) -> None` (atomic, 0644).
- [ ] Steps: tests `test_headroom_reads_peak_and_max`, `test_no_cap_no_headroom`, `test_collect_journal_error` (run raises TimeoutExpired → `error` set), `test_status_written_atomically_0644`; command `journalctl -o json --since -24h --no-pager _PID=1 + _TRANSPORT=kernel` with timeout 60 s → FAIL → implement → PASS → commit `service events: root timer writes /run/sigmond/service-events.json`.

### Task 3: `smd check` services line, `smd status` table, operator page

**Files:** Modify `lib/sigmond/commands/check.py` (`services_line(env) -> Line`, `CHECK_META['services'] = ('platform.services', 'platform', None)`, entry after `archive` in `CHECKS`), `bin/smd` (`cmd_status`: a "service events (24 h)" table after the existing sections, only rows with an event or peak above 85 %), `tests/test_check_pages.py` (order list gains 'services'); Create `docs/operator/checks/platform.services.md` + README row; Test `tests/test_check_services.py`

- [ ] Steps: tests `test_no_events_ok_names_manual_count` ("ok    services  no restarts or memory kills in 24 h (2 manual restarts)"), `test_global_oom_fails` (FAIL text names the victims and "the machine ran out of memory"), `test_memcg_kill_waits` (names unit and count), `test_restart_storm_waits` (4 automatic restarts), `test_peak_over_85_waits` (names unit, peak and cap in MB), `test_stale_or_missing_waits`, `test_text_ascii`; `smd status` test `test_status_lists_only_eventful_units` → FAIL → implement → PASS → commit `check/status: restarts and memory kills (platform.services)`.

### Task 4: Heartbeat carries it

**Files:** Modify `lib/sigmond/heartbeat.py` (`_read_service_events(path=None, now=None) -> Optional[dict]` like `_read_storage_status`; `_read_resources` adds `fields["services"]`; the resources block's verdict takes INVALID on any global OOM, INCONCLUSIVE on memcg kills / restart storm / peak > 85 %, INDETERMINATE when missing or stale); Test `tests/test_heartbeat_services.py`

- [ ] Steps: tests `test_fresh_status_in_block`, `test_global_oom_invalid`, `test_memcg_inconclusive`, `test_missing_indeterminate` → FAIL → implement → PASS → full sigmond suite (compare failures with origin/main; env-only failures known: hs_uploader.sink.writer.sink_path) → commit `heartbeat: services (restarts, OOM kills, memory headroom)`.

### Task 5: Prove it (operations, Rob's go)

- [ ] Push (b3 idle).  On AI6VN-1 `smd update --apply`; after 5 min `smd check` shows a services line; `smd status` shows the table.
- [ ] On N8UR (Michael's station; ask his session first): `smd check` must WAIT naming `timestd-core-recorder` memcg/global kills — the live case.
- [ ] Ledger `restart-oom-reporting` → on-stations, with the N8UR check as its proof.

# Drift Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every station reports, daily and on demand, each way it differs from its release (source, builds, units, overrides, strays, host side), and the fleetboard alarms on faults and on temporary tests left in place.

**Architecture:** A new stdlib-only package `lib/sigmond/drift/` builds one `Report` from six collectors with injectable readers; `smd align` (dry run) prints it, `--json` emits it, `--report` writes `/var/lib/sigmond/drift.json` and a sink row that hs-uploader ships to sigmondb as `station_drift`.  Exceptions live in `/etc/sigmond/drift-exceptions.toml`.  wsprdaemon-server ingests the row and the fleetboard gains a `drift` check.  Existing manifest-drift readers (`smd admin manifest status`, doctor, heartbeat) switch to the report.

**Tech Stack:** Python 3.11 stdlib (`tomllib`, `json`, `subprocess`, `dataclasses`), systemd timer, pytest via `.venv/bin/pytest` (sigmond) and the wsprdaemon-server test suite; ClickHouse DDL in `sigmondb/ddl.py`.

**Spec:** `docs/superpowers/specs/2026-11-18-drift-report-design.md` (approved by Michael, 2026-11-18).

## Global Constraints

- Kinds, exactly: `source`, `build`, `unit`, `override`, `stray`, `host`.  Severities, exactly: `drift`, `fault`.
- Every item carries a one-line `fix` naming the command or file change that restores the release.
- The report compares against the blessed manifest, or against the candidate a canary recorded (`align`'s existing record).
- Daily timer: `sigmond-drift.timer`, `OnCalendar=daily`, `RandomizedDelaySec=1h`, `Persistent=true`.
- Report file: `/var/lib/sigmond/drift.json`.  Exceptions file: `/etc/sigmond/drift-exceptions.toml`.
- Exceptions: `--test` defaults to 7 days, at most 30; a standing exception defaults to 30 days, at most 180.  A test shows a reminder from the day before it ends, and becomes a `fault` after it ends.  An expired standing exception becomes the item's own severity again.
- A fault never blocks `smd fleet update`; its summary names stations with open faults.
- Nothing repairs automatically; the report only names the fix.
- Site-specific configuration (callsign, grid, receivers, channel choices) never counts as an override.
- Core stays stdlib-only; every line smd prints stays ASCII.  No change to radiod or its config.
- Work on `main` in each repo; post each repo's file list to the claude-bus before pushing.

## Review Focus

1. **A station with no release record at all** (never aligned, bare-metal repo install): the report says "no baseline" once and still runs the five non-source kinds; never reads as clean.  → Task 2 `test_no_baseline_still_reports_other_kinds`.
2. **A collector that raises** (a missing `systemctl`, an unreadable config): that kind reports one `fault` item "could not check <kind>: <error>" and the other kinds still run.  → Task 1 `test_collector_error_becomes_one_fault`.
3. **Exceptions file malformed or hand-edited** (bad date, unknown kind, expiry beyond the cap): bad entries are ignored with one warning each and capped expiries clamp to the maximum; the report never crashes.  → Task 7 `test_bad_exception_entries_skipped_and_capped`.
4. **Timer runs while `smd align --apply` holds the lifecycle lock:** the report run never takes the lock and never blocks the apply.  → Task 9 `test_report_takes_no_lifecycle_lock`.
5. **A row the server cannot place** (an unknown reporter, a schema version it does not know): ingest refuses that row with the existing refused-reporter tally and stores the rest.  → Task 10 `test_ingest_station_drift_rejects_unknown_schema`.

---

### Task 1: Model, report and the collector runner

**Files:**
- Create: `lib/sigmond/drift/__init__.py`, `lib/sigmond/drift/model.py`
- Test: `tests/test_drift_model.py`

**Interfaces:**
- Produces:
  - `KINDS = ("source", "build", "unit", "override", "stray", "host")`; `SEVERITIES = ("drift", "fault")`
  - `@dataclass(frozen=True) class Item: kind: str; severity: str; subject: str; detail: str; fix: str` with `key -> str` = `f"{kind}:{subject}"` (the exception key)
  - `@dataclass class Report: station: str; release: Optional[str]; baseline: str  # "blessed" | "candidate" | "none"; generated_at: str; items: list[Item]; accepted: list[dict]; reminders: list[dict]` with `counts() -> dict[str, dict[str, int]]` (kind → severity → n), `to_json() -> dict`, `faults() -> list[Item]`
  - `run_collectors(collectors: Sequence[tuple[str, Callable[[], list[Item]]]]) -> list[Item]`

- [ ] **Step 1: Failing tests** — `test_item_key_is_kind_and_subject`, `test_counts_by_kind_and_severity`, `test_to_json_is_ascii_and_round_trips`, `test_collector_error_becomes_one_fault` (a collector raising `OSError("boom")` yields exactly one `Item(kind=<its kind>, severity="fault", subject="check", detail contains "boom")` and the next collector's items still appear), `test_unknown_kind_or_severity_rejected` (constructing `Item` with `kind="x"` raises `ValueError`).
- [ ] **Step 2:** `.venv/bin/pytest tests/test_drift_model.py` → FAIL (module missing).
- [ ] **Step 3:** Implement; `generated_at` is UTC `%Y-%m-%dT%H:%M:%SZ`.
- [ ] **Step 4:** PASS.  **Step 5:** commit `drift: model, report and collector runner`.

### Task 2: Source and host collectors (wrap what align already computes)

**Files:**
- Create: `lib/sigmond/drift/source.py`
- Test: `tests/test_drift_source.py`

**Interfaces:**
- Consumes: `sigmond.align.Item`/`plan_align`/`classify`/`image_file_drift` (unchanged), Task 1's `Item`.
- Produces: `source_items(plan: list[align.Item]) -> list[Item]`; `host_items(host_record: Optional[dict], file_drift: list[tuple[str, str]]) -> list[Item]`; `baseline_of(aligned_record: Optional[dict]) -> tuple[Optional[str], str]` returning `(release, "blessed"|"candidate"|"none")`.

Mapping (from align's statuses): `move` behind → `drift`, fix `smd align --apply`; `ahead` → `drift`, fix `smd align --apply --allow-rollback` (or accept); `refuse`/diverged → `fault`; `missing` for an enabled component → `fault`, fix `smd align --apply --install-missing`; `current` → no item.  Host: a host file differing → `drift` (fix `pm-align --apply`); host record older than the VM's release → `drift`.

- [ ] **Step 1: Failing tests** — `test_behind_is_drift_with_apply_fix`, `test_ahead_is_drift_with_rollback_fix`, `test_missing_enabled_is_fault`, `test_current_gives_no_item`, `test_host_file_drift_items`, `test_no_baseline_still_reports_other_kinds` (with no aligned record, `baseline_of` gives `(None, "none")` and `source_items([])` returns `[Item("source","drift","baseline","no blessed or candidate record", "smd align --apply")]`).
- [ ] **Step 2:** FAIL.  **Step 3:** Implement.  **Step 4:** PASS.  **Step 5:** commit `drift: source and host items from align's plan`.

### Task 3: Build collector

**Files:**
- Create: `lib/sigmond/drift/builds.py`
- Test: `tests/test_drift_builds.py`

**Interfaces:**
- Consumes: `sigmond.build_stamp.declares_build`, `is_stale`; `sigmond.decoder_choice.DECODER_DIR`, `live()`, `operator_override()`.
- Produces: `build_items(base: Path, components: Iterable[str], *, enabled: Mapping[str, bool], decoder_dir: Path = DECODER_DIR, run=subprocess.run) -> list[Item]`.

Rules: a component that declares a build and `is_stale` → `fault`, fix `smd align --apply` (rebuilds).  When `meteor-scatter` is enabled and `decoder_dir/ubersdr/jt9 --help` does not list `--msk144` → `fault` "UberSDR jt9 lacks MSK144; meteor-scatter falls back to the WSJT-X build", fix `smd align --apply` (Michael 2026-11-18: every station on the UberSDR jt9).  `live()` returns `wsjtx` while the ubersdr build exists and its parity passed → `drift`.  `operator_override()` true → no build item (Task 5 reports the override).

- [ ] **Step 1: Failing tests** — `test_stale_build_is_fault`, `test_ubersdr_without_msk144_is_fault_when_meteor_enabled` (fake `run` returning help text without `--msk144`), `test_ubersdr_without_msk144_ignored_when_meteor_disabled`, `test_wsjtx_live_while_ubersdr_good_is_drift`, `test_help_probe_timeout_is_fault_not_crash` (fake `run` raising `subprocess.TimeoutExpired`).
- [ ] **Step 2:** FAIL.  **Step 3:** Implement; `--help` probe with `timeout=10`.  **Step 4:** PASS.  **Step 5:** commit `drift: build items (stale stamps, UberSDR MSK144, live decoder)`.

### Task 4: Unit collector, and align links sigmond's own units

**Files:**
- Create: `lib/sigmond/drift/units.py`
- Modify: `bin/smd` (`cmd_align` apply path: after component moves, install sigmond's own units the way `install.sh:826-833` does, then `daemon-reload`), `lib/sigmond/installer.py` only if a helper there already installs units (reuse; do not duplicate)
- Test: `tests/test_drift_units.py`, `tests/test_align_installs_own_units.py`

**Interfaces:**
- Produces: `unit_items(base: Path, enabled: Mapping[str, bool], *, sigmond_repo: Path, unit_dir: Path = Path("/etc/systemd/system"), is_enabled: Callable[[str], bool]) -> list[Item]`; `install_own_units(sigmond_repo: Path, unit_dir: Path, *, run=subprocess.run) -> list[str]` (names installed or replaced).

Rules: for each enabled component, every unit its `deploy.toml` `[systemd].units` lists must exist in `unit_dir` (missing → `fault`, fix `smd align --apply`); every listed timer must be enabled (not → `fault`, fix `sudo systemctl enable --now <timer>` via `smd enable <component>`).  sigmond's own `systemd/sigmond-*.{service,timer,path}`: missing in `unit_dir` or content differs → `fault`, fix `smd align --apply`.  A unit enabled for a component topology disables → `drift`.  `install_own_units` copies with mode 0644, skips identical files, never enables or starts anything (the ledger item sigmond-own-units-never-land; AB4EJ lacked `sigmond-archive-health.timer`).

- [ ] **Step 1: Failing tests** — `test_missing_declared_unit_is_fault`, `test_declared_timer_not_enabled_is_fault`, `test_own_unit_missing_or_changed_is_fault`, `test_unit_for_disabled_component_is_drift`, `test_install_own_units_copies_changed_only` (tmp dirs; identical file untouched, changed file replaced, mode 0644), `test_align_apply_installs_own_units` (cmd_align with injected runner calls `install_own_units` once and `systemctl daemon-reload` after).
- [ ] **Step 2:** FAIL.  **Step 3:** Implement.  **Step 4:** PASS + `tests/test_align*.py`.  **Step 5:** commit `drift: unit items; align installs sigmond's own units`.

### Task 5: Override collector

**Files:**
- Create: `lib/sigmond/drift/overrides.py`
- Test: `tests/test_drift_overrides.py`

**Interfaces:**
- Produces: `override_items(*, client_configs: Mapping[str, list[Path]], catalog_override: Path = Path("/etc/sigmond/catalog.toml"), pins_file: Path = Path("/etc/sigmond/component-pins.toml"), coordination: Path = Path("/etc/sigmond/coordination.toml"), manifest: Mapping[str, str]) -> list[Item]`.

Software-choice keys only (the spec's rule): any key in a client config whose name ends in `_jt9`, `_wsprd`, `_binary` or equals `decoder_jt9`/`decoder_path`, with a value set → `fault` (names file, key, value; fix "remove <key> from <file>"); `/etc/sigmond/catalog.toml` entries setting `repo` or `branch` → `drift`; a pin in `component-pins.toml` differing from the manifest → `drift`; `[decoders] operator_override = true` → `drift` naming the chosen implementation.  Commented lines never count.

- [ ] **Step 1: Failing tests** — `test_decoder_jt9_override_is_fault` (a meteor-scatter TOML with `decoder_jt9 = "/usr/local/libexec/sigmond/decoders/ubersdr-msk144/jt9"` under `[paths]`), `test_commented_override_ignored`, `test_catalog_repo_override_is_drift`, `test_pin_differing_from_manifest_is_drift`, `test_site_keys_never_count` (callsign, grid, receivers, channel blocks → no items), `test_unreadable_config_is_one_fault`.
- [ ] **Step 2:** FAIL.  **Step 3:** Implement.  **Step 4:** PASS.  **Step 5:** commit `drift: override items`.

### Task 6: Stray collector

**Files:**
- Create: `lib/sigmond/drift/strays.py`
- Test: `tests/test_drift_strays.py`

**Interfaces:**
- Consumes: `sigmond.lifecycle` unit discovery with `orphaned` marking (reuse; do not reimplement), `sigmond.catalog.load_catalog`.
- Produces: `stray_items(*, decoder_dir: Path, base: Path, catalog_names: set[str], unit_refs: Iterable) -> list[Item]`.

Rules: a folder under `decoder_dir` other than `ubersdr` and `wsjtx` → `drift` (fix "remove <dir> once nothing points at it"); a unit instance lifecycle marks `orphaned`, or whose instance name holds `\x3d` → `fault` when its unit file still carries an Environment that overrides delivery (as `meteor-scatter@AC0G\x3dB4`), else `drift`; fix `smd admin instance remove <client> <instance>`; a checkout under `base` that no catalog entry names → `drift`.

- [ ] **Step 1: Failing tests** — `test_extra_decoder_folder_is_drift` (`ubersdr-msk144`), `test_escaped_instance_is_stray`, `test_orphaned_instance_is_stray`, `test_unknown_checkout_is_drift`, `test_known_things_give_no_items`.
- [ ] **Step 2:** FAIL.  **Step 3:** Implement.  **Step 4:** PASS.  **Step 5:** commit `drift: stray items`.

### Task 7: Exceptions, tests and reminders

**Files:**
- Create: `lib/sigmond/drift/exceptions.py`
- Test: `tests/test_drift_exceptions.py`

**Interfaces:**
- Produces: `load(path: Path, now: datetime) -> list[Exception_]`; `accept(path: Path, key: str, *, reason: str, by: str, test: bool, until: Optional[date], now: datetime) -> Exception_`; `apply(report: Report, excs: list[Exception_], now: datetime) -> Report`; `@dataclass class Exception_: key: str; reason: str; by: str; kind: str  # "test" | "standing"; start: date; until: date`.

Rules: defaults and caps from Global Constraints (`--until` beyond the cap clamps, with a warning).  `apply`: an item matching a live exception moves to `report.accepted` (with days left); a `test` whose `until` is tomorrow or today adds a reminder `{station, key, by, reason, until, fix}`; a `test` past `until` keeps the item with `severity="fault"` and detail "temporary test ended <date>; restore baseline: <fix>"; a standing exception past `until` restores the item's own severity.  The file is TOML, written atomically (tmp + rename), mode 0644.

- [ ] **Step 1: Failing tests** — `test_accept_writes_entry_with_defaults` (test → 7 days, standing → 30), `test_until_beyond_cap_clamps` (test 45 days → 30; standing 365 → 180), `test_live_exception_moves_item_to_accepted`, `test_test_reminder_day_before_end`, `test_ended_test_is_fault_with_restore_fix`, `test_expired_standing_restores_severity`, `test_bad_exception_entries_skipped_and_capped` (bad date, unknown kind, missing reason: skipped, one warning each).
- [ ] **Step 2:** FAIL.  **Step 3:** Implement.  **Step 4:** PASS.  **Step 5:** commit `drift: exceptions, temporary tests and restore reminders`.

### Task 8: The report in `smd align`, `smd check` and the version commands

**Files:**
- Create: `lib/sigmond/drift/build_report.py`
- Modify: `bin/smd` (`cmd_align` dry run prints the report after today's plan; argparse at the `align` parser (~31096): `--json`, `--report`, `--accept ITEM`, `--test`, `--until YYYY-MM-DD`, `--reason TEXT`; `cmd_version` gains the lifecycle-state and version-policy columns `smd component list` shows today; plain `smd component list` (no `--catalog`) prints `cmd_version`'s table preceded by the line `smd component list now shows what smd version shows; use smd version (this form retires in v3.95)`), `lib/sigmond/commands/check.py` (`drift_line` in `CHECKS` after `topology`; `CHECK_META['drift'] = ('platform.drift', 'platform', None)`), `lib/sigmond/manifest_status.py`, `lib/sigmond/heartbeat.py` (`_read_manifest` consumers read `drift.json`'s source items when present; their output keys unchanged)
- Create: `docs/operator/checks/platform.drift.md` (header block like its siblings)
- Test: `tests/test_drift_build_report.py`, `tests/test_check_drift.py`, `tests/test_version_absorbs_component_list.py`

**Interfaces:**
- Consumes: Tasks 1-7.
- Produces: `build(station: str, *, readers: Readers) -> Report` where `Readers` bundles the injectable inputs of Tasks 2-7; `drift_line(env) -> Optional[Line]` reading `/var/lib/sigmond/drift.json` (age > 36 h → `!` "drift report stale"; missing → `-` "no drift report yet"; faults → `x` with the first fault's detail and `Fix:`; drift only → `ok` with counts; accepted/test items noted).

- [ ] **Step 1: Failing tests** — `test_build_runs_every_kind_and_applies_exceptions`, `test_align_json_emits_report`, `test_align_accept_writes_exception` (with `--test --reason`), `test_check_line_stale_missing_fault_clean`, `test_component_list_prints_version_table_with_note`, `test_manifest_status_keys_unchanged`.
- [ ] **Step 2:** FAIL.  **Step 3:** Implement.  **Step 4:** PASS + full suite.  **Step 5:** commit `drift: smd align report, --json, --accept; check line; version absorbs component list`.

### Task 9: Daily timer, drift.json and the sink row

**Files:**
- Create: `systemd/sigmond-drift.service` (`ExecStart=/usr/local/bin/smd align --report`, `Nice=10`, `IOSchedulingClass=idle`), `systemd/sigmond-drift.timer` (values from Global Constraints)
- Modify: `bin/smd` (`--report`: build, write `drift.json` atomically, insert one row via `sigmond.hamsci_sink` writer: database `sigmondb`, table `station_drift`, producer `sigmond`, columns below), `lib/sigmond/uploader_manifest.py` (the `pipelines.d/sigmond.toml` render gains a `sigmond-sigmondb-drift` pipeline: source sqlite `sigmondb.station_drift`, `requires_features = ["sigmondb-tar"]`, transport `wsprdaemon_tar` with `primary_table_name = "sigmondb.station_drift"`, `tar_tag = "chirp-drift"`, same servers/retry as codar-sounder's sigmondb pipelines)
- Test: `tests/test_drift_report_cmd.py`, `tests/test_uploader_manifest_drift.py`

Row columns: `time` (generated_at), `reporter_id`, `release`, `baseline`, `n_fault`, `n_drift`, `n_accepted`, `counts_json` (Report.counts()), `faults_json` (each fault item, max 50, detail truncated to 200 chars), `reminders_json`, `schema_version` = 1.

- [ ] **Step 1: Failing tests** — `test_report_writes_json_atomically`, `test_report_inserts_one_sink_row` (writer stubbed; fields as above), `test_report_takes_no_lifecycle_lock`, `test_sigmond_pipeline_renders_drift_pipeline_when_feature_present`, `test_drift_pipeline_absent_without_sigmondb_tar`.
- [ ] **Step 2:** FAIL.  **Step 3:** Implement; the timer installs through Task 4's `install_own_units` and is enabled by bring-up and by `smd align --apply` (enable only; start leaves it to the calendar).  **Step 4:** PASS.  **Step 5:** commit `drift: daily timer, drift.json and the sigmondb row`.

### Task 10: Server: ingest `station_drift` (wsprdaemon-server)

**Files:**
- Modify: `sigmondb/ddl.py` (table `station_drift`: columns of Task 9 plus `received_at`, `ingested_at`; MergeTree, partition by month, order by `(reporter_id, time)`, TTL 400 days), `sigmondb/ingest.py` (`CHIRP_TABLES` gains `"station_drift"`; reporter rule unchanged), `wsprdaemon_server.py` only if the chirp tar tag list needs `chirp-drift`
- Test: `tests/test_sigmondb_ingest.py` additions

- [ ] **Step 1: Failing tests** — `test_ingest_station_drift_row`, `test_ingest_station_drift_rejects_unknown_schema` (schema_version 2 → bad line; others in the file stored), `test_station_drift_reporter_rule_applies` (a row naming another callsign refused).
- [ ] **Step 2:** FAIL.  **Step 3:** Implement.  **Step 4:** PASS + the sigmondb test files.  **Step 5:** commit `sigmondb: ingest station_drift`.

### Task 11: Fleetboard `drift` check (wsprdaemon-server)

**Files:**
- Modify: `fleetboard/sources.py` (query latest `station_drift` row per reporter), `fleetboard/checks.py` (`drift` findings), `fleetboard/alarms.py` (alarm kinds `drift_fault` per station+item key, `test_restore` for reminders and ended tests), `fleetboard/board.py` (drift counts column)
- Test: `tests/test_fleetboard_drift.py`

Rules: each fault item opens `drift_fault` (subject `<station> <item key>`), closes when a later row lacks it; reminders open `test_restore` (text: who, test, until, restore command); a station whose latest row is older than 48 h shows "drift report stale" (no alarm: off-air already covers a dark station); drift-only items show on the board, no alarm.  Persistent-condition rule as the existing checks.

- [ ] **Step 1: Failing tests** — `test_fault_opens_and_clears_alarm`, `test_reminder_opens_test_restore`, `test_drift_only_no_alarm`, `test_stale_row_shows_not_alarms`, `test_one_station_many_faults_one_alarm_each`.
- [ ] **Step 2:** FAIL.  **Step 3:** Implement.  **Step 4:** PASS + fleetboard suite.  **Step 5:** commit `fleetboard: drift check, fault and test-restore alarms`.

### Task 12: Docs and the fleet summary

**Files:**
- Modify: `bin/smd` (`smd fleet update` summary line `open faults: <stations>` from each station's `smd check --json` drift line; never stops the roll; `smd fleet status` gains `release`, `faults`, `drift` columns read from each station's `drift.json`), `docs/admin/README.md` (drift report, exceptions, tests), `docs/operator/day-2.md` (one paragraph: `smd check`'s drift line and `smd align`), `docs/contributor/orchestration.md` (verb rows: align `--json/--report/--accept/--test/--until`, version, component list), `docs/INDEX.md` (platform.drift check page)
- Test: `tests/test_fleet_update_names_faults.py`, docs tests (`tests/test_docs*.py`, `scripts/docs-linkcheck.py docs`)

- [ ] **Step 1: Failing tests** — `test_fleet_update_names_faults_and_continues`, `test_fleet_status_shows_release_and_drift_counts`.
- [ ] **Step 2:** FAIL.  **Step 3:** Implement code and docs; re-stamp touched pages.  **Step 4:** PASS + docs checks + full suite.  **Step 5:** commit `drift: fleet update names open faults; docs`.

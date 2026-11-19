# v3.94 align and drift-report fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `smd align --apply` leave every native binary built from its checkout and every declared unit of a running component enabled, and make the drift report stop raising the false faults found on 2026-11-19.

**Architecture:** align gains one post-move pass that rebuilds native components whose build record (`/var/lib/sigmond/build-manifest/<c>.toml`, or `.provenance` for decoders) names a different commit from the one they should be built from, and one bring-up step that links and enables a running component's declared units.  The ka9q-web builder stops rewriting its record when it builds nothing.  The drift collectors read the same truths (native pin, instance targets, running service) so the report and align agree.

**Tech Stack:** Python 3 stdlib (sigmond core), bash (sigmond-appliance), ClickHouse DDL (wsprdaemon-server).

**Spec:** the ledger items `ops/ledger/items/v394-version.md` (owed fixes 1-6), `align-builds-before-checkout.md`, `station-drift-report.md` (entries 2026-11-19 22:40Z and 22:55Z), `sigmond-own-units-never-land.md`, `board-from-stations.md` (18:25Z).  Code facts behind this plan: the research report summarised in the SDD workspace ledger at plan start.

## Global Constraints

- Core stays stdlib-only; every line smd prints stays ASCII.
- smd refuses to run under sudo; root work goes through `_need_root`.
- Nothing repairs outside an explicit `smd align --apply` (or `smd update`); a dry run only names what `--apply` would do.
- A ka9q-radio that the site profile marks `locally_managed` (Phil's hosts) is never built, moved or restarted by any of this.
- Every radiod rebuild names itself in the dry run as `REBUILDS and RESTARTS radiod` with the reason, as a moved ka9q-radio already does.
- Tests run the real align and drift paths: a test may stub `make`, `systemctl`, git network calls and the builder's compile step, never the function the task changes.
- UberSDR decoders: pin `5eeeb53` (full sha from the ubersdr_wsprd repo); `--nn` and `-F` stay off — nothing in sigmond or a recorder template passes them.
- Commits stay local on sigmond `main` (and the named repos); nobody pushes.

## Review Focus

1. A station whose radiod is `locally_managed`: align must not rebuild or restart it even when its binary trails its checkout. (Task 2 test.)
2. A component that topology enables but whose service is deliberately stopped (KFS-OMNI stops hf-timestd on purpose): align must not enable or start its units, and the drift report must not fault them. (Tasks 4 and 5 tests.)
3. A failed rebuild (ka9q-web `make` exits non-zero) during `--apply`: align reports it, keeps the old binary running, does not write a build record, and the apply exits non-zero. (Task 1 and 2 tests.)
4. A station with two radiods (KFS-MAIN serves several): the ka9q-web template unit really is needed there; the report must still fault a missing template when a second target exists. (Task 4 test.)
5. A dry run on a station whose binaries are all current prints no rebuild line and changes nothing. (Task 2 test.)

---

### Task 1: The ka9q-web builder records only what it built

**Files:**
- Modify: `bin/smd` — `_build_ka9q_web_with_onion` (~2460-2535)
- Test: `tests/test_ka9q_web_build_record.py`

**Interfaces:**
- Produces: `_build_ka9q_web_with_onion(src_dir: Path, force: bool = False) -> bool` keeps its signature; it now (a) writes the build record only on the branch that ran `make`; (b) treats `_manifest_source_sha('ka9q-web') != _git_head(src_dir)` as stale even when the installed and in-tree binaries match; (c) runs `make clean` before `make` whenever it rebuilds (the Makefile's `config_paths.h` depends only on the Makefile, so a header carrying the commit never regenerates otherwise).

- [ ] **Step 1: Failing tests** (real two-commit git repo in tmp as `_SourceRig` in `tests/test_native_pin_rebuild.py` does; `smd._run` replaced by a recorder; `_build_onion`, `_ensure_native_build_deps`, `_install_radiod_deps` stubbed; `_BUILD_MANIFEST_DIR` and `_KA9Q_WEB_BIN` in tmp):
  - `test_no_rebuild_writes_no_record` — record at HEAD, binaries identical: no `make` argv recorded and the record file's bytes unchanged.
  - `test_record_behind_head_rebuilds` — record names commit A, HEAD at B, installed == in-tree binary: `make clean` then `make -jN` recorded; afterwards the record's `[[upstream]] name="ka9q-web"` sha == B.
  - `test_failed_make_keeps_record` — `make` returns rc 2: function returns False, record still names A.
- [ ] **Step 2:** Run `pytest tests/test_ka9q_web_build_record.py -q` — FAIL.
- [ ] **Step 3:** Implement in `_build_ka9q_web_with_onion`.
- [ ] **Step 4:** PASS, plus `tests/test_apt_lock_contention.py` (existing ka9q-web build tests).
- [ ] **Step 5:** Commit `ka9q-web build: record only a real build; a record behind HEAD rebuilds clean`.

### Task 2: align rebuilds a native component whose binary trails its source

**Files:**
- Modify: `lib/sigmond/align_apply.py` (`Ctx`, `apply_plan`, `_build_current`), `lib/sigmond/align.py` (plan line text), `bin/smd` (`_align_apply` wiring ~9387, dry-run printer ~10232)
- Test: `tests/test_align_native_rebuild.py`

**Interfaces:**
- Consumes: Task 1's builder; `_build_ka9q_radio(src_dir, force=True)`; `_manifest_source_sha(component) -> Optional[str]`; `_git_head(path) -> str`; the existing radiod staleness/restart stage (driven by binary mtime vs service start).
- Produces: `Ctx.native_stale: Callable[[str, Path], Optional[str]]` — returns a reason string (`"binary built from 1a2b3c4, checkout at 5d6e7f8"`, or `"no build record"`) or None; `Ctx.native_build: Callable[[str, Path], int]` — rebuilds (0 = ok).  `apply_plan` calls them for `ka9q-radio` and `ka9q-web` after every move and for every `current` item (replacing `_build_current`'s early return for `BUILT_COMPONENTS`).  The dry run prints, for each stale one, `<name> <short> current  — binary built from X; --apply REBUILDS` (ka9q-radio: `— REBUILDS and RESTARTS radiod — recording gap of about 60 s`).
- Rules: a `locally_managed` ka9q-radio returns None from `native_stale`.  A missing record on ka9q-radio counts as stale only when the binary is older than the checkout's HEAD commit time (B4's case: binary 2026-10-25, HEAD 2026-11-12); a missing record on ka9q-web counts as stale.  A rebuild failure marks the item failed (apply exits non-zero) and leaves the running binary.

- [ ] **Step 1: Failing tests** — drive the real `cmd_align --apply` with `_apply_patches` (test_align_cli.py:2027) and a real tmp git checkout per component (`_SourceRig` style); stub only `_build_ka9q_radio`, `_build_ka9q_web_with_onion` (as recorders returning True/False), `_quiet_live_stages()` and systemctl:
  - `test_current_radiod_with_old_binary_rebuilds` (B4: record absent, binary mtime before HEAD time) → `_build_ka9q_radio(force=True)` called once; summary counts it.
  - `test_moved_ka9q_web_builds_after_checkout` → the builder is called after the checkout's HEAD equals the release pin (assert `_git_head` inside the stub).
  - `test_locally_managed_radiod_untouched` (Review Focus 1).
  - `test_rebuild_failure_fails_apply` (Review Focus 3) → rc != 0, item `failed`.
  - `test_dry_run_names_rebuild_and_changes_nothing` and `test_all_current_prints_no_rebuild` (Review Focus 5).
- [ ] **Step 2:** FAIL.  **Step 3:** Implement.  **Step 4:** PASS + `tests/test_align_cli.py tests/test_align_apply.py`.  **Step 5:** Commit `align: rebuild ka9q-radio and ka9q-web whose binary trails the checkout`.

### Task 3: Decoders: pin 5eeeb53, and align runs the native rebuilds that are due

**Files:**
- Modify: `bin/smd` (`_UBERSDR_COMMIT` 1454; `_align_apply` after the moves), `docs/native-binaries.md` (pin line ~159), the tests that name the old pin (`tests/test_native_pin_rebuild.py`, `tests/test_update_plan.py:466`), `sigmond-appliance/test-update-smd-lines.sh:328`, `sigmond-appliance/test-update-v3.sh:1279`
- Test: `tests/test_align_native_decoders.py`

**Interfaces:**
- Consumes: `_native_rebuilds_due() -> list` (bin/smd 1760), `_run_native_builder(name) -> int` (1999).
- Produces: `_align_native_decoders(apply: bool) -> int` in `bin/smd`, called by `_align_apply` after the moves and before `_align_make_live`; dry run prints `native rebuild due: ubersdr-decoders (built 8719788, pin 5eeeb53)`; `--apply` runs each due builder; a failure is reported and counted, the old decoders stay.
- Pin: `_UBERSDR_COMMIT = '<full sha of 5eeeb53>'` (resolve with `git -C <ubersdr_wsprd> rev-parse 5eeeb53`).

- [ ] **Step 1: Failing tests** — `test_align_rebuilds_due_decoders` (real `_native_rebuilds_due` over a `_SourceRig` tree whose `.provenance` names the old pin; builder stubbed as a recorder) → called with `ubersdr-decoders`; `test_dry_run_lists_due_decoders_builds_nothing`; `test_no_recorder_template_passes_nn_or_F` (scan the shipped client templates in sigmond's `etc/clients/` and the recorder deploy defaults sigmond renders for `--nn`, `-F`, `--fine-freq` → none).
- [ ] **Step 2:** FAIL.  **Step 3:** Implement + bump the pin and its references.  **Step 4:** PASS + `tests/test_native_pin_rebuild.py tests/test_update_plan.py`.  **Step 5:** Commit `decoders: pin 5eeeb53; align runs the native rebuilds that are due`.

### Task 4: Drift report: four false or wrong faults

**Files:**
- Modify: `lib/sigmond/drift/units.py` (`unit_items` 68-98), `lib/sigmond/drift/builds.py` (`_provenance_item` 62-79), `lib/sigmond/drift/build_report.py` (`Readers`, `station_readers`), `bin/smd` (`_align_drift_report` ~9915 fills the new readers)
- Test: `tests/test_drift_units.py`, `tests/test_drift_builds.py`, `tests/test_drift_final_fixes.py` (end to end through the real `cmd_align --report`)

**Interfaces:**
- Produces on `Readers`: `native_pins: Mapping[str, str]` (component -> full sha, from `smd._native_pin(...)`); `service_active: Callable[[str], bool]` (default `systemctl is-active --quiet`); `template_instances: Callable[[str], int]` (count of enabled or active instances of a template unit, default from `systemctl list-units --all '<prefix>@*'`).
- Rules:
  1. A declared templated unit (`x@.service`) that is missing is a fault only when `template_instances` > 0 for it; otherwise no item.
  2. A component counts as running when topology enables it AND its first declared non-template `.service` is active (or it declares no service).  Units of an enabled-but-not-running component produce no fault; they produce one `drift` item `"<name> is enabled but <service> is not running"` with fix `smd start <name>` — check that verb exists (`grep "add_parser('start'" bin/smd`), else name `sudo systemctl start <service>`.
  3. "Installed but not enabled" timer fix text: `smd align --apply` (Task 5 makes align enable it).
  4. Decoder provenance compares to `native_pins['ubersdr_wsprd']`, not the checkout HEAD; fix stays `smd admin native-build --apply ubersdr-decoders`.

- [ ] **Step 1: Failing tests** — `test_template_missing_without_instances_no_item`; `test_template_missing_with_second_target_faults` (Review Focus 4); `test_units_of_stopped_component_not_faults` (Review Focus 2: hf-timestd enabled, core recorder inactive, timestd-events.timer disabled → no fault, one drift item); `test_disabled_timer_fix_is_align_apply`; `test_decoders_at_native_pin_clean_when_checkout_ahead` (provenance 8719788, pin 8719788, HEAD 5eeeb53 → no item); end-to-end in `test_drift_final_fixes.py`: a tree reproducing WB6CXC (plain ka9q-web.service only, mag-recorder inactive, decoders at pin, checkout ahead) → `smd align --report` shows only the real timer fault.
- [ ] **Step 2:** FAIL.  **Step 3:** Implement.  **Step 4:** PASS + `tests/test_drift_*.py`.  **Step 5:** Commit `drift: template, stopped-component, fix-text and decoder-pin corrections`.

### Task 5: align links and enables the declared units of running components

**Files:**
- Modify: `bin/smd` (`_align_bringup` ~9126; `_align_start_timers` ~9539)
- Test: `tests/test_align_enables_units.py`

**Interfaces:**
- Consumes: `installer.apply_deploy_toml_links(repo_dir)`, `installer.apply_systemd_enables(repo_dir)` (enables and starts non-template `[systemd] units` with an `[Install]` section, skipping `unit_not_ready`), `_topology_holds_off(name)` (15141), the running rule of Task 4 (`service_active`).
- Produces: `_align_enable_declared_units(apply: bool) -> int` called from `_align_bringup` after `config render`; for each installed catalog checkout that topology enables and whose main service is active: links, then enables.  Dry run lists `would enable <unit> (<component>)`.  `_align_start_timers` also enables every sigmond-own `.timer` that `install_own_units` installed (closes sigmond-archive-health.timer).
- Ruling recorded in the plan: a component's `[systemd] units` list is its intended state, so align enables `timestd-events.timer` even though hf-timestd's own install.sh withholds it without `events.toml`; most of the fleet already runs it.

- [ ] **Step 1: Failing tests** (real `cmd_align --apply` via `_apply_patches`, systemctl via `fake_run` recording argv, a tmp catalog checkout with a `deploy.toml` listing `x.service` and `x-events.timer`): `test_enables_declared_timer_of_running_component`; `test_skips_component_with_stopped_service` (Review Focus 2); `test_skips_topology_disabled`; `test_dry_run_lists_enables_only`; `test_own_timers_all_enabled` (sigmond-archive-health.timer).
- [ ] **Step 2:** FAIL.  **Step 3:** Implement.  **Step 4:** PASS + `tests/test_align_installs_own_units.py tests/test_align_cli.py`.  **Step 5:** Commit `align: enable the declared units of running components and all of sigmond's own timers`.

### Task 6: The board trusts the sender wd30 saw, not only the row's own key

**Files:**
- Modify: wsprdaemon-server `sigmondb/ddl.py` (station_drift column `sender String` + `MIGRATIONS` ALTER), `schema/sigmondb.sql` (regenerate), `sigmondb/ingest.py` (`chirp_row` sets `row["sender"] = fallback_reporter` for station_drift); ops `bin/fleet-map-export` (`query_drift_rows`, `stations_from_rows`)
- Test: wsprdaemon-server `tests/test_sigmondb_ingest.py`; ops `tests/test_fleet_map_export.py`

**Interfaces:**
- Produces: `sigmondb.station_drift.sender` (path-derived `RX_SITE` of the tar, e.g. `AC0G/B2`).  fleet-map-export selects `sender` and refuses a receiver claim when the receiver's callsign (before `/` or `=`) differs from the sender's callsign — stderr line naming both — in addition to the existing pin rule.
- Note in the commit: the sender is still station-asserted inside the tar; it narrows a forged claim to the forger's own callsign.  The authenticated SFTP account is not threaded to ingest today (ledger follow-up).

- [ ] **Step 1: Failing tests** — `test_station_drift_row_carries_sender`; `test_claim_from_other_callsign_refused` (sender W7WKR/2 claiming AC0G/B4 → refused, board keeps AC0G-B4); `test_transporter_claims_own_callsign_receivers` (sender AC0G/B2 → AC0G/B1A, AC0G/B1B accepted).
- [ ] **Step 2:** FAIL.  **Step 3:** Implement.  **Step 4:** PASS + each repo's ingest / export suites.  **Step 5:** Commit in each repo (wsprdaemon-server local main stays unpushed; ops local).

### Task 7: Docs and changelog

**Files:**
- Modify: `docs/admin/README.md` (align rebuild and unit-enable behaviour; drift rules 1-4), `docs/native-binaries.md` (align now runs due rebuilds), sigmond-appliance `CHANGELOG.md` v3.94 section (Upgrading notes: align may rebuild radiod once on stations whose binary trails its checkout — names B4-style cases; enables declared timers)
- Test: `tests/test_docs*.py`, `scripts/docs-linkcheck.py docs`

- [ ] **Step 1:** Write the doc changes; plain, short sentences, active voice.  **Step 2:** Run the docs checks and the full suite (`.venv/bin/pytest -q -p no:cacheprovider --basetemp=$HOME/.cache/pytest-tmp/v394 tests`).  **Step 3:** Commit `docs: v3.94 align rebuilds and unit enables; drift corrections`.

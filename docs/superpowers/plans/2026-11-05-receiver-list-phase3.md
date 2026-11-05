# Receiver list (multi-receiver Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `[[receiver]]` list in site-profile.toml says which radiod each reporter listens to, and bring-up, render, `instance add` and `smd check` all work from it.

**Architecture:** A new module, `lib/sigmond/receivers.py`, reads, checks and writes the list.  `smd admin receiver apply` turns the list into what exists today (coordination radiod blocks, `[[clients.X]]` bindings, per-instance sources files, instance configs) by calling the existing `_sources_bind` once per receiver.  `sources bind` and `instance add --radiod` become writers of the list followed by an apply, so the list stays the one record.  `build_plan` keeps its present shape and gains the declared receivers as input; a host with no list keeps today's plan step for step.

**Tech Stack:** Python 3.11 stdlib only (`tomllib` reads; text splicing writes, as `ensure_radiod_block` already does), pytest via `.venv/bin/pytest`.

**Spec:** `docs/superpowers/specs/2026-10-30-multi-receiver-station-design.md` §4 and §7 (Rob approved 2026-10-30), as amended by `docs/superpowers/specs/2026-11-05-source-switchboard-design.md` §4 and §4.1 (Michael, 2026-11-05).  Read both before any task.

## Global Constraints

- `smd` core stays stdlib-only; no TOML writer library.  Write TOML by text and replace files atomically (the pattern in `coordination._write_coordination`).
- `smd` refuses `sudo`; commands that write `/etc` check root with `_need_root` as their neighbours do.
- A receiver has exactly one of `radiod` (an mDNS status name passing `sources.valid_mdns_hostname`) or `kiwisdr`.  `kiwisdr` parses but `validate_receivers` refuses it: "KiwiSDR receivers arrive with Phase 4c".
- Reporter IDs: accept every spelling (`AC0G/B1A`, `AC0G=B1A`, `AC0G-B1A` stays hyphenated), store the display form from `instance.display_reporter_id(instance.parse_user_reporter_id(x))`, and compare in storage form.
- Several receivers may share one radiod (switchboard §4.1).  Then every receiver after the first on that radiod must carry `same_antenna_as = "<reporter ID of a receiver on the same radiod>"`, and at most one receiver per radiod may carry `injector = true`.
- `injector = true` means hf-timestd binds to that receiver's radiod.  hf-timestd stays a host singleton until Phase 3b, so at most one receiver on the whole host carries it (spec §7: "bring-up accepts `injector = true` on one receiver only and says so").
- Upload default: a receiver's instances upload unless it carries `same_antenna_as`; an explicit `upload = true|false` on the receiver overrides the default either way.
- `merge_into` and `merge_grid` parse and are kept, but nothing acts on them in this plan (Phase 4b).  An unknown key in a `[[receiver]]` block is an error naming the key.
- `clients` defaults to every reporter-keyed client the profile carries (`bringup._REPORTER_KEYED` ∩ profile clients); naming a client outside `_REPORTER_KEYED` is an error ("`<name>` is not a per-reporter client").
- A host with no `[[receiver]]` list behaves exactly as before: `tests/fixtures/bringup_dasi2_local_plan.json` must still match, and `coordination.env` gains no lines.
- Nothing in this plan deletes an instance, a sources file or recorded data.  `receiver apply` only adds and updates; removing is the switchboard's job.
- Commit trailers: `Co-Authored-By: <the implementing model's own line>` and `Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z`.

## Review Focus

1. A site-profile holding `[[receiver]]` arrays passes through `site_profile.carry_uploads_block` (profile install) and `_split_block` without losing or mangling a receiver.  Test in Task 1.
2. One reporter written two ways in two receivers (`AC0G/B1A` and `ac0g=b1a`) counts as a duplicate.  Test in Task 1.
3. A receiver that names this host's own local radiod by its status name gets no second `[radiod."…"]` block in coordination.toml.  Test in Task 3.
4. `receiver apply` run twice changes nothing the second time, and a client dropped from `clients` keeps its instance.  Test in Task 3.
5. A profile with `[[receiver]]` but no `[station]` table (bring-up created it on a flags-only host) renders without the callsign error.  Test in Task 2.

---

### Task 1: The receiver list: read, check, write

**Files:**
- Create: `lib/sigmond/receivers.py`
- Modify: `lib/sigmond/site_profile.py` (`_split_block`, `carry_uploads_block` only if Review Focus 1 fails against them)
- Test: `tests/test_receivers.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Receiver: reporter_id: str; radiod: str = ''; kiwisdr: str = ''; antenna: str = ''; clients: tuple = (); injector: bool = False; same_antenna_as: str = ''; upload: Optional[bool] = None; merge_into: str = ''; merge_grid: str = ''` with property `uploads -> bool` (explicit `upload`, else `not same_antenna_as`).
  - `class ReceiverError(ValueError)`.
  - `load_receivers(path: Path = SITE_PROFILE_PATH) -> list[Receiver]`: `[]` when the file or the array is absent; raises `ReceiverError` on a malformed entry (unknown key, wrong type).
  - `validate_receivers(receivers: list[Receiver], *, profile_clients: frozenset) -> list[str]`: one message per problem, `[]` when clean.  Fills defaulted `clients` is NOT its job; see `effective_clients`.
  - `effective_clients(r: Receiver, profile_clients: frozenset) -> tuple`: `r.clients` or the sorted reporter-keyed clients of the profile.
  - `write_receivers(path: Path, receivers: list[Receiver]) -> None`: rewrites every `[[receiver]]` block in the file, leaves every other byte as it was, creates the file (receivers only) when absent, atomic replace.
  - `upsert_receiver(path: Path, r: Receiver) -> list[Receiver]`: replace the receiver with the same storage-form reporter ID, else append; returns the new list.

- [ ] **Step 1: Write the failing tests** in `tests/test_receivers.py`:
  - `test_no_file_or_no_array_gives_an_empty_list`
  - `test_a_receiver_round_trips` — write two receivers (one with `clients`, `injector`, `antenna` containing a `"` and a `#`), load, compare equal; the file's `[station]` table survives byte for byte.
  - `test_reporter_spellings_store_the_display_form` — `ac0g=b1a` loads as `AC0G/B1A`; `KFS-SE` stays `KFS-SE`.
  - `test_one_reporter_two_spellings_is_a_duplicate` — `AC0G/B1A` and `ac0g=b1a` → a message containing `AC0G/B1A`.
  - `test_shared_radiod_needs_same_antenna_as` — two receivers on `ac0g-b1-a-status.local`, the second without `same_antenna_as` → a message naming both reporters; with `same_antenna_as = "AC0G/B1A"` → clean; `same_antenna_as` naming a receiver on another radiod → message.
  - `test_injector_on_two_receivers_is_refused` — message contains `injector`.
  - `test_kiwisdr_is_refused_until_phase_4c` and `test_radiod_and_kiwisdr_together_is_refused`.
  - `test_an_unknown_key_names_itself` — `[[receiver]] reporter_id="A" radiod="x.local" colour="red"` → `ReceiverError` mentioning `colour`.
  - `test_a_bad_status_name_is_refused` — `radiod = "evil\nX=1"` → message.
  - `test_uploads_default_and_override` — plain → True; `same_antenna_as` → False; `same_antenna_as` + `upload = true` → True; `upload = false` → False.
  - `test_clients_default_to_the_profiles_reporter_keyed_clients` and `test_a_non_reporter_client_is_refused` (`clients = ["hf-timestd"]`).
  - `test_receivers_survive_carry_uploads_block` — a profile text with `[uploads]` then two `[[receiver]]` blocks through `carry_uploads_block(new, old)`: both receivers still load from the result.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_receivers.py -q`.  Expected: fails on the missing module.
- [ ] **Step 3:** Implement `receivers.py`.  Read with `tomllib`; write each block as `[[receiver]]` followed by `key = value` lines (strings escaped as TOML basic strings, booleans lower-case, `clients` as an inline array), omitting keys left at their defaults.  To rewrite, drop every line from a `[[receiver]]` header up to the next header of any kind, then append the new blocks at the end of the file.  Fix `_split_block` / `carry_uploads_block` only if the carry test fails.
- [ ] **Step 4:** Run the test file and `tests/test_site_profile*.py`.  Expected: PASS.
- [ ] **Step 5:** Commit: `receivers: the [[receiver]] list in site-profile — read, check, write`.

### Task 2: Render: receivers reach coordination.env

**Files:**
- Modify: `lib/sigmond/coordination.py` (`render_env`), `lib/sigmond/commands/config.py` (`cmd_config_render`)
- Test: `tests/test_receivers_render.py`

**Interfaces:**
- Consumes: `load_receivers` (Task 1).
- Produces: `render_env(coord, passthrough_lookup=None, receivers=None)`; `receivers=None` loads them from `SITE_PROFILE_PATH`, swallowing `ReceiverError` with one stderr warning.  Lines, after the `[[clients.X]]` lines, only when the list is non-empty: `RECEIVER_COUNT=<n>`, then per receiver `n` (1-based, list order) `RECEIVER_<n>_REPORTER=<display id>` and `RECEIVER_<n>_RADIOD=<status name>`.

- [ ] **Step 1: Failing tests:**
  - `test_no_receivers_adds_no_lines` — `render_env(coord, receivers=[])` equals the output before this task (compare against `render_env` of the same coord with the receivers block patched out, or a literal).
  - `test_two_receivers_publish_count_reporter_and_radiod` — exact lines `RECEIVER_COUNT=2`, `RECEIVER_1_REPORTER=AC0G/B1A`, `RECEIVER_1_RADIOD=ac0g-b1-a-status.local`, `RECEIVER_2_…`.
  - `test_every_coordination_env_writer_carries_the_receivers` — with a site-profile holding one receiver (paths patched to tmp), `_sources_bind` rewrites coordination.env and the `RECEIVER_1_` lines are present (they come from `render_env`, so this guards that no writer bypasses it).
  - `test_a_profile_with_receivers_and_no_station_renders` (Review Focus 5) — `cmd_config_render` on a profile holding only `[[receiver]]` exits 0, writes the env, and prints no callsign error.
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.  In `cmd_config_render`, skip identity validation and identity patching when the profile has no `[station]` table, and say so in one `_info` line: "site-profile.toml has no [station] table; identity left as coordination.toml has it".  Also run `validate_receivers` there and fail render (exit 1, messages listed) when it reports problems.
- [ ] **Step 4:** Run the new tests plus `tests/test_coordination*.py tests/test_config_render*.py tests/test_sources_bind.py`.  PASS.
- [ ] **Step 5:** Commit: `render: publish the receiver list in coordination.env`.

### Task 3: `smd admin receiver list` and `apply`; `sources bind` writes a receiver

**Files:**
- Modify: `bin/smd` (new `admin receiver` subparser beside `admin sources`; `_sources_bind`, `cmd_sources_bind`)
- Test: `tests/test_receiver_apply.py`

**Interfaces:**
- Consumes: Task 1, Task 2; `_sources_bind(status_dns, reporter, clients, *, host, timing, etc_root, coord_path, inventory_rows) -> int`; `upload.apply_enable(client, instance, on, delivery=None)`.
- Produces:
  - `_receiver_apply(receivers, *, profile_clients, etc_root=Path('/etc'), coord_path=None, profile_path=SITE_PROFILE_PATH, dry_run=False) -> int` — validates, then per receiver calls `_sources_bind(r.radiod, r.reporter_id, effective_clients(...), host=<'' when local>, timing=r.injector, …)` and sets each instance's upload with `upload.apply_enable(client, rid, r.uploads)`; returns 0, or 1 after printing every validation message and touching nothing.
  - `cmd_receiver_list(args)` (`--json` prints `{"receivers": [ {reporter_id, radiod, antenna, clients, injector, same_antenna_as, uploads} ]}`; text prints one row per receiver).
  - `cmd_receiver_apply(args)` (`--dry-run`).
  - `cmd_sources_bind` now builds a `Receiver(reporter_id=…, radiod=status_dns, clients=tuple(clients), injector=timing)`, `upsert_receiver`s it into site-profile.toml, then runs `_receiver_apply` for that one receiver.  `--clients` keeps its meaning.  The `host` argument still reaches `_sources_bind`.
  - The profile's client set (`profile_clients`) comes from the station's profile in the catalog; when it cannot be resolved, use `_REPORTER_KEYED`.

- [ ] **Step 1: Failing tests** (filesystem under `tmp_path`, as `tests/test_sources_bind.py` does):
  - `test_apply_binds_each_receiver_once` — two receivers → `_sources_bind` called twice with the receivers' radiods, reporters, clients and `timing` from `injector`.
  - `test_apply_twice_changes_nothing` (Review Focus 4) — the tree's bytes after the second apply equal those after the first.
  - `test_dropping_a_client_keeps_its_instance` (Review Focus 4).
  - `test_a_shared_radiod_receiver_does_not_upload` — `apply_enable` gets `False` for the `same_antenna_as` receiver's instances, `True` for the first.
  - `test_a_local_radiod_named_by_status_gets_no_second_block` (Review Focus 3) — coordination.toml already holds `[radiod.sigma]` with `status_dns = "sigma-status.local"`; a receiver on `sigma-status.local` leaves exactly one radiod block.
  - `test_an_invalid_list_touches_nothing` — a duplicate reporter → exit 1, no file changed.
  - `test_sources_bind_writes_a_receiver` — `cmd_sources_bind` for `ac0g-b1-a-status.local --reporter AC0G/B1A --timing` leaves a receiver with `injector = true` in site-profile.toml, and a second bind of the same reporter replaces it rather than appending.
  - `test_receiver_list_json` — exact keys above.
  - Keep every existing test in `tests/test_sources_bind.py` passing.
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.  Decide "local" for the `host` argument with `coordination.radiod_id_for_status` plus the found block's `is_local`.
- [ ] **Step 4:** `.venv/bin/pytest tests/test_receiver_apply.py tests/test_sources_bind.py tests/test_receivers*.py -q`.  PASS.
- [ ] **Step 5:** Commit: `admin receiver list/apply; sources bind records a receiver`.

### Task 4: `smd admin instance add --radiod`

**Files:**
- Modify: `bin/smd` (`cmd_instance_add`, its parser)
- Test: `tests/test_instance_add_radiod.py`

**Interfaces:**
- Consumes: `upsert_receiver`, `_receiver_apply` (Tasks 1, 3).
- Produces: `smd admin instance add <client> <reporter> --radiod <status>`: after `create_instance`, upsert the receiver for `<reporter>` (radiod = `<status>`; `clients` = its existing clients plus `<client>`; a new receiver gets `clients = (<client>,)`), then `_receiver_apply` for that receiver.  Refuses, touching nothing, when `<status>` fails `valid_mdns_hostname`, or when `<reporter>` already has a receiver on a different radiod ("`<reporter>` listens to `<old>`; move it with the switchboard, or pick another reporter ID").  Without `--radiod`, behaviour is unchanged.

- [ ] **Step 1: Failing tests:** `test_radiod_records_a_receiver_and_binds_it`, `test_a_second_client_joins_the_same_receiver`, `test_a_reporter_on_another_radiod_is_refused`, `test_without_radiod_nothing_changes` (no site-profile written).
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the new file and `tests/test_instance*.py`.  PASS.
- [ ] **Step 5:** Commit: `instance add --radiod: record and bind the instance's receiver`.

### Task 5: Bring-up works from the receiver list

**Files:**
- Modify: `lib/sigmond/bringup.py` (`build_plan`), `bin/smd` (`cmd_bringup`)
- Test: `tests/test_bringup_receivers.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `build_plan(..., local_receivers=(), local_only=frozenset())`:
  - `local_receivers`: tuple of `(status_dns, reporter_id, clients)` for receivers on this host's own radiods, used only when there are two or more of them; one local receiver keeps today's `reporter=` path untouched.
  - With `local_receivers`, Stage 1 adds, after the `radiod-configured` checkpoint, one `config` step `smd admin receiver apply` (label `bind receivers to local radiods`), and Stage 3a/4 loop over `(client, reporter)` pairs from the receivers instead of the single `reporter`.
  - `local_only`: reporter IDs whose instances get `smd config upload <client> <rid> --off` in Stage 3a instead of `--on` (shared-antenna receivers).
  - `cmd_bringup`: when site-profile declares receivers, take them as the operator's choice: remote ones become `remote_pairs` (each must answer on the LAN, the same check `--remote-radiod` gets, and no discovery selection runs), local ones become `local_receivers`, the `injector` one sets `timing_named`, and `same_antenna_as` ones fill `local_only`.  `--remote-radiod` together with a declared list is an error ("the receiver list in site-profile.toml already names the radiods; edit it or drop --remote-radiod").
- [ ] **Step 1: Failing tests:**
  - `test_no_receivers_leaves_the_local_plan_unchanged` — the fixture comparison already in `tests/test_sources_bind.py` keeps passing (run it; do not copy it).
  - `test_two_local_receivers_bind_then_make_one_instance_pair_each` — argv of the `receiver apply` step, and `instance add`/`instance enable` once per (client, reporter), enables `settle_s == CLIENT_STAGGER_S`.
  - `test_a_shared_antenna_receiver_uploads_off` — its `config upload` steps end `--off`.
  - `test_declared_remote_receivers_skip_discovery_selection` — `cmd_bringup` with a profile declaring two remote receivers and `non_interactive=True` plans them without `--remote-radiod`, and fails before planning when one does not answer.
  - `test_remote_radiod_flag_and_a_declared_list_conflict`.
  - `test_the_injector_receiver_sets_the_timing_radiod`.
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `.venv/bin/pytest tests/test_bringup*.py tests/test_sources_bind.py -q`.  PASS.
- [ ] **Step 5:** Commit: `bring-up: plan from the receiver list`.

### Task 6: Capacity gate

**Controller, before dispatch:** measure on AC0G-B2 (bare metal, 5700U, four recorders from the Task 10 rebuild): for each `wspr-recorder@*`, `psk-recorder@*`, `meteor-scatter@*` (if present) and the hf-timestd services, the average CPU over at least 30 minutes (`ps -o cputime,etimes` per main PID, plus children via the unit's cgroup `cpu.stat usage_usec` read twice 30 min apart).  Put the per-client figures, in cores, into the brief as `INSTANCE_COST_CORES`.  These are light reads, not a load test.

**Files:**
- Create: `lib/sigmond/capacity.py`
- Modify: `lib/sigmond/bringup.py` or `bin/smd` (`cmd_bringup`, before `build_plan`)
- Test: `tests/test_capacity.py`

**Interfaces:**
- Produces:
  - `INSTANCE_COST_CORES: dict[str, float]` (measured figures from the brief; a client absent from it costs 0.25 cores and the verdict says "estimated").
  - `@dataclass class CapacityVerdict: needed: float; available: float; estimated: tuple; ok: bool` and `text() -> str`.
  - `capacity_verdict(instances: list[tuple[str, str]], *, online_cpus: int, radiod_cpus: int) -> CapacityVerdict` — `available = online_cpus - radiod_cpus - 1` (one CPU left to the system), `needed = sum(cost)`, `ok = needed <= available`.
  - `cmd_bringup` computes the verdict over every (client, reporter) pair it will create (local and remote receivers alike, since all run here), prints `verdict.text()`, and when not ok refuses before planning (exit 1), naming the receivers' `clients = [...]` narrowing as the fix.  `--ignore-capacity` turns the refusal into a warning.  A single-receiver host never refuses (the gate guards several receivers, and one-card stations must not change).
- [ ] **Step 1: Failing tests:** `test_enough_cores_is_ok`, `test_too_few_cores_refuses_with_the_numbers`, `test_an_unknown_client_is_estimated`, `test_one_receiver_never_refuses` (through `cmd_bringup`), `test_ignore_capacity_warns_and_proceeds`.
- [ ] **Step 2-4:** Run, implement, run (`tests/test_capacity.py tests/test_bringup*.py`).
- [ ] **Step 5:** Commit: `bring-up: a capacity gate across receivers`.

### Task 7: Seeing the receivers: `instance list` radiod column, `smd check` per receiver

**Files:**
- Modify: `bin/smd` (`cmd_instance_list`), `lib/sigmond/instance_report.py` (`instance_rows`), `lib/sigmond/commands/check.py` (a receiver check plus its `CHECK_META` entry)
- Test: `tests/test_receiver_visibility.py`

**Interfaces:**
- Produces:
  - `instance_rows` rows gain `radiod` (the status name from the instance's sources file `selected`, else `""`); the text table gains a `RADIOD` column after `REPORTER ID`.
  - `receivers_line(env) -> Line` in check.py: `—`/skipped when no list is declared; otherwise one line per receiver folded into one `Line` whose text reads `AC0G/B1A ← ac0g-b1-a: 2/2 active; AC0G/B1B ← ac0g-b1-b: 1/2 active (psk-recorder down)`, mark FAIL when any receiver has no active instance, WAIT when some are down, PASS otherwise.  `CHECK_META` id `receivers.instances`, stage `run`.  Active means `systemctl is-active` of each instance unit, through the `env` the other lines use.
- Scope: spec §7 also asks, per receiver, "radiod … streaming, … spots flowing, upload pipeline healthy".  This task covers instances only; the existing host-wide `psk_line`, `wspr_line` and `uploads_line` keep covering spots and uploads.  Per-receiver streaming and spot counts belong with the switchboard's liveness work (switchboard §3, "not heard").
- [ ] **Step 1: Failing tests:** `test_instance_rows_carry_the_radiod`, `test_instance_list_prints_a_radiod_column`, `test_receivers_line_absent_list_is_skipped`, `test_receivers_line_marks` (all-up PASS, one-down WAIT, none-up FAIL), `test_check_json_carries_the_receivers_id`.
- [ ] **Step 2-4:** Run, implement, run (`tests/test_receiver_visibility.py tests/test_check*.py tests/test_instance*.py`).
- [ ] **Step 5:** Commit: `instance list shows the radiod; smd check reports each receiver`.

### Task 8: Existing stations and the docs

**Files:**
- Modify: `lib/sigmond/commands/config.py` (`cmd_config_render --init`), `README.md`, `docs/superpowers/specs/2026-10-30-multi-receiver-station-design.md` (status line and Phase 3 row only)
- Test: `tests/test_receivers_init.py`

**Interfaces:**
- Produces: `smd config render --init` on a host whose profile has no `[[receiver]]`: build receivers from existing instances (each `/etc/sigmond/clients/<client>@<rid>.sources.toml` whose `selected` holds one `radiod:` key; instances grouped by reporter), print them, and write them with `write_receivers` (creating the profile from `TEMPLATE` first when absent, as today).  An instance whose sources file is empty, or a reporter whose instances point at two radiods, is listed as "left out" with the reason, never guessed.  A profile that already declares receivers is left alone ("already declares N receivers").  A single-radiod station with no instances writes nothing new (its plan stays the flags/`reporter` path).
- [ ] **Step 1: Failing tests:** `test_init_builds_receivers_from_instances`, `test_init_leaves_out_what_it_cannot_map`, `test_init_leaves_a_declared_list_alone`, `test_init_on_a_single_radiod_station_adds_nothing`.
- [ ] **Step 2-4:** Run, implement, run.
- [ ] **Step 5: Docs.**  README: a "Receivers" subsection after "Decode-only host" showing a two-receiver `[[receiver]]` example, `smd admin receiver list|apply`, `instance add --radiod`, and the shared-radiod rule with `same_antenna_as`.  Multi-receiver spec: status line notes Phase 3 delivered with the switchboard's §4.1 amendment, and the §4 rule "A radiod may serve at most one receiver" gains a line pointing to that amendment (Rob to confirm).
- [ ] **Step 6:** Full suite: `.venv/bin/pytest -q --deselect tests/test_rac_switch.py::test_the_switch_installs_from_the_wizard_as_an_ascii_script`.  Expected: all pass.
- [ ] **Step 7:** Commit: `config render --init adopts existing instances as receivers; docs`.

## Proving it (after Task 8, controller)

- B2: write two `[[receiver]]` blocks (AC0G/B1A on ac0g-b1-a, AC0G/B1B on ac0g-b1-b, `injector` on B1A), `smd admin receiver apply`, `smd admin instance list` shows the radiods, `smd check` shows both receivers PASS.  Then add a third receiver `AC0G/B1A2` on ac0g-b1-a with `same_antenna_as = "AC0G/B1A"` and `clients = ["wspr-recorder"]`: it decodes, its upload reads off, and B1A's spots keep flowing.
- sigma (one local radiod): `smd bringup dasi2` with no list plans as before.

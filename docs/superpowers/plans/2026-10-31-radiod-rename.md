# Radiod rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `smd admin radiod rename <old> <new> [--description TEXT]` and `smd admin radiod describe <id> TEXT` rename or re-describe one local radiod instance in one step, with every client still bound to it afterwards.

**Architecture:** A pure planner (`lib/sigmond/radiod_rename.py`) reads the station's files under a root and returns a `RenamePlan` of text edits, moves, refusals and unit operations; a command layer (`lib/sigmond/commands/radiod_rename.py`) shows the plan, takes the lifecycle lock, stops, writes atomically, regenerates derived files through injected hooks, and restarts what was running; `bin/smd` wires the verbs and supplies the ka9q-web rewrite hook (the writer lives there).

**Tech Stack:** Python 3.11 stdlib (re, pathlib, dataclasses, tomllib), argparse in `bin/smd`, pytest/unittest as the repo does.

**Spec:** `docs/superpowers/specs/2026-10-31-radiod-rename-design.md` (all sections; §10 holds the builder-check calls).

## Global Constraints

- Stdlib only. Pure logic in `lib/sigmond/radiod_rename.py` takes `root: Path` and is tested on fake trees without root; nothing in it prints, sleeps or runs a process.
- Ids are case-sensitive and must match `^[A-Za-z0-9_.-]+$`.
- After a rename the conf's `[global] status` is `<new>-status.local`; every whole-token occurrence of the OLD status string and of `radiod@<old>` (as `radiod@<old>.service` or `radiod@<old>.conf`) in the scanned directories is rewritten. A token boundary is start/end of text or any character outside `[A-Za-z0-9_.-]`.
- Edits are text edits (never re-renders); each file is written atomically (temp file in the same dir, `os.replace`, mode and uid/gid preserved). Nothing is written until the plan is complete and every refusal has passed.
- Scanned directories: `/etc/radio`, `/etc/sigmond`, `/etc/hs-uploader`, and `/etc/<name>` for every component in the catalog that has a config dir present. Hits elsewhere are reported, not edited.
- Exit codes: 0 done; 1 refused (nothing written); 2 declined at the prompt; 4 failed mid-write (written files listed).
- Run state is restored, never upgraded: only units that were active are started, only units that were enabled are enabled.
- Commit messages: `area: one sentence in plain words`, ending with the session's two attribution lines. Only changed files are added by path. `docs/superpowers/**` stays uncommitted.
- Tests: `cd /home/rob/hamsci/repos/sigmond && .venv/bin/python -m pytest tests/<file> -q -p no:cacheprovider`. `tests/test_docs_cli_table.py::test_admin_subverbs_match` requires every `smd admin` subverb in `docs/contributor/orchestration.md`'s table.

## Review Focus

1. A status string that is a prefix of another (`se-status.local` inside `base-status.local` is not a match, but `AI6VN-status.local` inside `RADIOD_AI6VN_STATUS_LOCAL_STATUS=AI6VN-status.local` is). Whole-token rule; test in Task 1.
2. The coordination table keyed by the status name (`[radiod."AI6VN-status.local"]`, no `status_dns`) must be re-keyed to `[radiod."<new>-status.local"]` and stay status-keyed; an id-keyed table stays id-keyed with `status_dns` rewritten. Test in Task 1.
3. Renaming the inactive instance must not start it (AI6VN-PM: `omni` -> `se`). Test in Task 2.
4. A write failure on the third file after two were written exits 4 and prints exactly those two paths, and starts nothing. Test in Task 2.
5. The plain `ka9q-web.service` holder is chosen by sorted id; the plan output shows the before/after port map and a flipped 8081 holder. Test in Task 3.

---

### Task 1: the planner

**Files:**
- Create: `lib/sigmond/radiod_rename.py`
- Test: `tests/test_radiod_rename.py`

**Interfaces (produces):**
- `ID_RE = re.compile(r'^[A-Za-z0-9_.-]+$')`; `valid_id(s: str) -> bool`.
- `status_of(conf_text: str) -> str`: the `[global] status` value, `""` if absent.
- `set_status(conf_text: str, status: str) -> str`: rewrite the `status =` line in `[global]` keeping its trailing comment, or insert `status    = <status>` right after the `[global]` header when absent.
- `hardware_section(conf_text: str) -> str`: the `[global] hardware` value (`"rx888"`), `""` if absent.
- `set_description(conf_text: str, text: str) -> str`: rewrite `description =` in the hardware section (quoted, trailing comment kept) or insert it as that section's last key; `ValueError` when the section is absent.
- `rewrite_token(text: str, old: str, new: str) -> str`: whole-token replacement per the Global Constraints boundary rule.
- `rename_coord(text: str, old: str, new: str, old_status: str, new_status: str) -> str`: re-key `[radiod."<old>"]`/`[radiod.<old>]` to `[radiod."<new>"]` and set `status_dns = "<new_status>"` in that table (insert after the header if absent); re-key `[radiod."<old_status>"]` to `[radiod."<new_status>"]` (no `status_dns` added); rewrite `radio_conf` values naming `radiod@<old>.conf`; rewrite `radiod_id = "<oldkey>"` for both key forms; rewrite `[timing_authority.per_radiod.<oldkey>]` headers (bare or quoted).
- `@dataclass Edit(path: Path, before: str, after: str)`; `@dataclass Move(src: Path, dst: Path)`.
- `@dataclass RenamePlan(old: str, new: str, old_status: str, new_status: str, description: str | None, edits: list[Edit], moves: list[Move], refusals: list[str], not_edited: list[Path], history: list[str], affected_components: list[str])` with property `refused -> bool`.
- `scan_dirs(root: Path, components: list[str]) -> list[Path]`: `root/etc/radio`, `root/etc/sigmond`, `root/etc/hs-uploader`, `root/etc/<c>` for each component whose dir exists.
- `plan(old: str, new: str, *, description: str | None = None, root: Path = Path('/'), components: list[str], hold: dict | None = None) -> RenamePlan`. Refusals (all collected, in this order): `new` fails `ID_RE`; no `root/etc/radio/radiod@<old>.conf`; `radiod@<new>.conf` exists; a coordination table keyed `<new>` or `<new>-status.local` exists; `root/etc/systemd/system/radiod@<new>.service.d` exists; `hold` has any value whose `radiod_unit == f"radiod@{old}.service"`; the old conf's `[global] hardware` is `sig_gen` or `old` starts with `bootstrap-fw`. Moves: the conf, the conf.d dir if present, the drop-in dir if present. Edits: the conf (status, header comment lines, optional description), coordination via `rename_coord`, every other scanned file containing either token via `rewrite_token` (both tokens), `topology.toml`'s `unit = "radiod@<old>.service"` handled by the same token rule. `not_edited`: files under `root/var/lib/sigmond` and `root/etc` outside the scanned dirs that contain the old status (scan `sdr-labels.toml` into edits, the rest into `not_edited`). `history`: the four path patterns from spec §4's last row, rendered with the old status. `affected_components`: components whose `/etc/<c>` had a hit, plus `hf-timestd` when `/etc/hf-timestd` had one, plus every client type that coordination binds to the old key through `[[clients.<type>]] radiod_id` (spec §10.4: the union of both).
- `describe_plan(instance: str, text: str, *, root: Path = Path('/')) -> RenamePlan` with one edit and refusals: no conf, `set_description` raised.

- [ ] **Step 1: Write the failing tests.** Build a fixture function `ai6vn_pm(tmp_path) -> Path` that lays out AI6VN-PM's files as of 2026-10-31 under `tmp_path`: `etc/radio/radiod@AI6VN.conf` (the real header comment lines, `[global]` with `hardware = rx888`, `status    = AI6VN-status.local      # mDNS name...`, `[rx888]` with `description = "AI6VN RX-888 Mk2" # callsign + antenna; surfaces in status` and `serial      = "0009002807411037"`), `etc/radio/radiod@omni.conf` (status `omni-status.local`, serial 352B), empty `etc/radio/radiod@AI6VN.conf.d/`, `etc/sigmond/coordination.toml` with `[radiod."AI6VN-status.local"]` (host, radio_conf, sdr_serial, no status_dns) and `[radiod."omni"]` (host, status_dns, radio_conf, sdr_serial), `etc/sigmond/coordination.env` with `RADIOD_AI6VN_STATUS_LOCAL_STATUS=AI6VN-status.local`, `etc/systemd/system/ka9q-web.service` with `ExecStart=/usr/local/sbin/ka9q-web -m AI6VN-status.local -p 8081`, `etc/systemd/system/radiod@AI6VN.service.d/smd-cpu-affinity.conf`, `etc/hf-timestd/timestd-config.toml` (`status = "AI6VN-status.local"`), `etc/psk-recorder/AI6VN.toml` and `psk-recorder-config.toml`, `etc/wspr-recorder/AI6VN.toml` and `config.toml`, `etc/meteor-scatter/AI6VN.toml` and `meteor-scatter-config.toml` (each with the `status = "AI6VN-status.local"` line), `etc/hs-uploader/pipelines.toml` with two `decoding_software = "psk-recorder/0.1 (radiod=AI6VN-status.local)"` lines, `etc/sigmond/clients/psk-recorder@AI6VN.sources.toml` with `selections = []`.

```python
def test_valid_id(): assert valid_id("se") and valid_id("AI6VN") and not valid_id("se/1") and not valid_id("")
def test_rewrite_token_is_whole_token(): assert rewrite_token("x base-status.local y", "se-status.local", "z") == "x base-status.local y"; assert rewrite_token("A=AI6VN-status.local", "AI6VN-status.local", "omni-status.local") == "A=omni-status.local"   # Review Focus 1
def test_set_status_keeps_comment_and_inserts_when_absent(): ...
def test_set_description_rewrites_or_inserts_and_refuses_without_section(): ...
def test_rename_coord_status_keyed_table_stays_status_keyed(): ...   # Review Focus 2: AI6VN -> omni
def test_rename_coord_id_keyed_table_gets_new_status_dns(): ...       # omni -> se
def test_plan_omni_to_se_on_ai6vn_pm(tmp_path): p = plan("omni", "se", root=ai6vn_pm(tmp_path), components=[...]); assert not p.refused; moves == [conf]; edits touch exactly conf and coordination.toml (no client file names omni-status.local); affected_components == []
def test_plan_ai6vn_to_omni_after_first_rename(tmp_path): ...  # apply the first plan's edits to the fixture by hand in the test, then plan("AI6VN","omni", description="TCI-530 Omni"); assert the edit set is exactly: conf (status, 2 header comments, description), coordination.toml, coordination.env, ka9q-web.service, 7 client TOMLs, pipelines.toml; moves == [conf, conf.d, service.d]; affected_components == ["hf-timestd","meteor-scatter","psk-recorder","wspr-recorder"]; sources.toml NOT in edits
def test_plan_refusals(tmp_path): ...  # new exists; bad id; hold names old unit; sig_gen; missing old -> each refusal string present, edits empty
def test_plan_reports_history_and_not_edited(tmp_path): ...  # a file under etc/foo with the token lands in not_edited
def test_describe_plan(tmp_path): ...
```

- [ ] **Step 2: Run to verify they fail.** Run: `.venv/bin/python -m pytest tests/test_radiod_rename.py -q`. Expected: FAIL, `ModuleNotFoundError: sigmond.radiod_rename`.
- [ ] **Step 3: Implement** `lib/sigmond/radiod_rename.py` with the signatures above. `rename_coord` is a line scan that tracks the current table header (reuse the header rule from `sdr_map._HEADER`: column-0 `[...]` lines).
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `radiod rename: a pure planner that lists every file, move and refusal before anything is written`

---

### Task 2: the command layer

**Files:**
- Create: `lib/sigmond/commands/radiod_rename.py`
- Test: `tests/test_radiod_rename_cmd.py`

**Interfaces:**
- Consumes: Task 1's `plan`, `describe_plan`, `RenamePlan`; `sigmond.lifecycle.lifecycle_lock(reason=...)`, `resolve_units(components, enabled_components)`, `order_units(units, coordination=...)`; `sigmond.coordination.load_coordination`, `render_env`; `sigmond.topology.load_topology` for the enabled component list; `sigmond.catalog.load_catalog` for component names.
- Produces, as module-level hooks tests monkeypatch: `ROOT = Path('/')`; `systemctl(*args) -> int`; `is_active(unit) -> bool`; `is_enabled(unit) -> bool`; `ka9q_web_rewrite(old: str, new: str) -> list[str]` (default: no-op returning `[]`; `bin/smd` replaces it, Task 3); `write_atomic(path: Path, text: str) -> None` (temp + `os.replace`, preserving mode/uid/gid); `confirm(prompt) -> bool`.
- `show_plan(p: RenamePlan, port_map: tuple[list, list] | None) -> None`: prints moves, edits (path + count of changed lines), refusals, `not_edited`, `history`, the units to stop and start, and the ka9q-web port map.
- `cmd_radiod_rename(args) -> int` and `cmd_radiod_describe(args) -> int` with `args.old, args.new, args.description, args.dry_run, args.yes` / `args.instance, args.text, args.dry_run, args.yes`. Flow per spec §5: plan → refuse (1) → show → dry-run (0) → confirm unless `--yes` (2) → lock → snapshot active/enabled for `radiod@<old>`, the ka9q-web unit(s) and every service unit of each affected component → stop clients (reverse `order_units`), ka9q-web, radiod; disable `radiod@<old>` if it was enabled → write moves then edits (on exception: print `written:` list, return 4, start nothing) → `coordination.env` = `render_env(load_coordination(ROOT/etc/sigmond/coordination.toml))` → `ka9q_web_rewrite(old, new)` → `systemctl daemon-reload` → enable `radiod@<new>` if old was enabled; start `radiod@<new>` if old was active; start ka9q-web and the clients that were active, in `order_units` order → report → return 0. `describe`: one edit, restart `radiod@<id>` only if active.

- [ ] **Step 1: Write the failing tests** using the Task 1 fixture, `ROOT = tmp_path`, a recording fake `systemctl`/`is_active`/`is_enabled`, a fake `ka9q_web_rewrite` recorder, `confirm` returning False/True, and `resolve_units` monkeypatched to return `UnitRef`s for `psk-recorder@AI6VN.service`, `wspr-recorder@AI6VN.service`, `meteor-scatter@AI6VN.service`, `timestd-core-recorder.service`:

```python
def test_rename_inactive_omni_to_se_starts_nothing(...): rc == 0; calls contain no 'start'; conf moved; se stays disabled   # Review Focus 3
def test_rename_running_ai6vn_to_omni_restarts_clients_in_order(...): stop order = clients (reverse order_units), ka9q-web, radiod@AI6VN; disable old; enable new; start radiod@omni, ka9q-web, clients in order; every client file now says omni-status.local; coordination.env re-rendered; ka9q_web_rewrite called once with ("AI6VN","omni")
def test_dry_run_writes_nothing(...): rc 0, tree byte-identical, no systemctl calls
def test_declined_prompt_is_2(...)
def test_refusal_is_1_and_writes_nothing(...)
def test_write_failure_midway_exits_4_and_lists_written(...): write_atomic raises on the third edit; rc 4; stdout lists exactly the first two paths; no 'start' calls   # Review Focus 4
def test_describe_restarts_only_radiod(...)
```

- [ ] **Step 2: Run to verify they fail.** Expected: FAIL, `ModuleNotFoundError`.
- [ ] **Step 3: Implement** the module. Moves use `os.replace`/`shutil.move` on directories; edits through `write_atomic`.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `radiod rename: stop, write atomically, regenerate, and restart only what was running`

---

### Task 3: `bin/smd` wiring and the ka9q-web hook

**Files:**
- Modify: `bin/smd` (the `radiod` admin subparser near line 26530; the `args.command == 'radiod'` dispatch near 28538; a new `_ka9q_web_rewrite_for_rename(old, new)` next to `_write_ka9q_web_unit` ~11656)
- Test: `tests/test_ka9q_web_units.py` (the `smd` fixture), `tests/test_cli_reference.py`, `tests/test_tui_parity.py`, `tests/test_snapshot.py`

**Interfaces:**
- Produces: `smd admin radiod rename <old> <new> [--description TEXT] [--dry-run] [--yes]` and `smd admin radiod describe <instance> <text> [--dry-run] [--yes]`; dispatch calls `_need_root('admin radiod rename')` unless `--dry-run`, sets `commands.radiod_rename.ka9q_web_rewrite = _ka9q_web_rewrite_for_rename`, then the handler.
- `_ka9q_web_rewrite_for_rename(old: str, new: str) -> list[str]`: `before = _ka9q_web_targets()` is captured by the dispatch BEFORE the handler runs (pass it in via a closure or module attr); after the handler's writes, `after = _ka9q_web_targets()`; run `_write_ka9q_web_unit()`; for every `(unit, status, port)` in `before` not in `after`: `systemctl stop`, `systemctl disable`, remove `/etc/sigmond/ka9q-web/<inst>.env` for templated units; for every unit in both whose status or port changed: `systemctl restart`; return the human-readable lines (`ka9q-web: <unit> <status> :<port>` before and after, and each stop/restart) which the handler prints.
- `ka9q_web_port_map(targets) -> list[str]` helper used by `show_plan` for the before/after map (compute `after` for the dry-run by calling `_ka9q_web_targets` against a coordination object with the renamed id, i.e. a pure helper `ka9q_web_targets_for(radiods: list[Radiod]) -> list` split out of `_ka9q_web_targets`; the plan shows the flipped 8081 holder (Review Focus 5)).

- [ ] **Step 1: Write the failing tests** in `tests/test_ka9q_web_units.py`: `test_targets_for_is_pure_and_sorted` (two radiods `AI6VN`,`omni` → plain unit to `AI6VN`; after rename to `omni`,`se` → plain unit to `omni`), `test_rewrite_for_rename_retires_stale_templated_unit` (with `_write_ka9q_web_unit`, `_run` and `Path.unlink` patched; assert stop/disable/env-removal calls for a unit that vanished and a restart for one whose status changed). Parser: `tests/test_cli_reference.py`, `tests/test_tui_parity.py`, `tests/test_snapshot.py` stay green.
- [ ] **Step 2: Run to verify they fail.** Expected: FAIL.
- [ ] **Step 3: Implement** the parser, dispatch and hook; add the two verbs' rows to `docs/contributor/orchestration.md`'s admin subverb table (the docs test demands it).
- [ ] **Step 4: Run the tests** plus `tests/test_docs_cli_table.py` and `python3 -m py_compile bin/smd`. Expected: PASS.
- [ ] **Step 5: Commit.** `smd admin radiod rename|describe: the verbs, with ka9q-web units regenerated and stale ones retired`

---

### Task 4: docs and the whole suite

**Files:**
- Modify: `docs/operator/troubleshooting.md` (the "Two RX888s" symptom: a "Renaming an instance" step with the two AI6VN-PM commands, what restarts, the history note, exit codes), `docs/commands/smd.md` (no section: rename is not read-only; add one sentence under `smd admin sdr list` pointing at the troubleshooting step), spec §8 marked with the delivery range (file stays untracked).
- Test: `tests/test_docs_links.py` (one pre-existing external-anchor failure), full suite compared against `.superpowers/sdd/2026-10-30-multi-card-hardening/baseline-red.txt` (no new red).

- [ ] **Step 1: Write the docs.**
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_docs_links.py tests/test_docs_cli_table.py -q` then the full suite with the baseline comparison. Expected: no new red.
- [ ] **Step 3: Commit.** `docs: renaming a radiod instance`

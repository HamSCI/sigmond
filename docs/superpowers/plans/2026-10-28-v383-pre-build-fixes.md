# v3.83 Pre-Build Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land v3.83 rows 11, 3 and 4 before the image build: `smd update` runs deploy.toml builds, install probes the magnetometer's I2C addresses, and a reflash keeps the uploader keys.

**Architecture:** Three independent fixes across four repos. sigmond gains a `build` update action and two `smd psws` verbs; mag-recorder gains a `probe-i2c` verb, an install hook and an all-zero sample guard; sigmond-appliance's three install docs gain a reflash section.

**Tech Stack:** Python 3.11 (sigmond core stdlib-only; unittest + pytest), bash install scripts, Markdown/HTML docs.

**Spec:** `sigmond/docs/superpowers/specs/2026-10-28-v383-pre-build-fixes-design.md`

## Global Constraints

- Repos live under `/home/mjh/hamsci/repos/`. Develop on `main`; commit, never push. Never `git commit --amend`.
- Commit trailer, every commit: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01H4HYgCZ3dRtK4XWA1Xyoc3`.
- sigmond's runtime core stays stdlib-only. Its test suite never elevates: never set `SIGMOND_TEST_ALLOW_SUDO`.
- Run tests with `TMPDIR=/home/mjh/t2tmp`; never write to `/tmp`. sigmond: `.venv/bin/pytest`. mag-recorder: its own `.venv/bin/pytest` (or `uv run pytest` if no `.venv`).
- No byte above 0x7F in anything `sigmond-wizard.sh` sends to the guest agent.
- The mag probe never blocks an install: every probe path exits 0.
- Never log once per sample: the zero guard logs on transitions only.
- Match surrounding style, comment density and naming.

## Review Focus

1. A component with BOTH `install.sh` and `[build]` (hf-timestd, wspr-recorder, psk-recorder, meteor-scatter): the update must run `install.sh` only, never the build too. Test in Task 1.
2. A `[build]` component whose pull failed: no build runs, and the run reports it skipped. Test in Task 1.
3. `mag-usb -S` hangs on a held or wedged adapter: the 10 s timeout fires and install carries on. Test in Task 2.
4. A restore tarball carrying `../`, an absolute path, a symlink, or a file outside the two key trees: refused before anything lands. Test in Task 5.
5. A config whose `remote_temp_address` line is commented out: the probe writes a live line, and leaves the comment block intact. Test in Task 2.

---

### Task 1: `smd update` runs a component's deploy.toml build (row 11)

**Files:**
- Modify: `sigmond/lib/sigmond/update.py` (`_ORDER`, `_NO_INSTALLER_NOTE`, `_has_installer`, `plan_update`)
- Modify: `sigmond/lib/sigmond/installer.py` (`run_deploy_build_steps` gains `force`)
- Create: `sigmond/lib/sigmond/update_build.py`
- Modify: `sigmond/bin/smd` (`cmd_update` state at ~4538; executor branch beside `step.kind == 'install'` at ~4698)
- Test: `sigmond/tests/test_update_plan.py`, `sigmond/tests/test_deploy_build_steps.py`, `sigmond/tests/test_update_executor.py`

**Interfaces:**
- Produces: `run_deploy_build_steps(repo_dir, *, dry_run=False, runner=subprocess.run, force=False) -> tuple[bool, list[str]]`; `force=True` skips the `produces`-all-present shortcut (still checks `produces` after the steps).
- Produces: `update_build.run(repo_dir: Path, *, runner=subprocess.run) -> tuple[bool, list[str]]` — `run_deploy_build_steps(force=True)` then, on success, `apply_deploy_toml_links(repo_dir)` and `systemctl daemon-reload` when a link under `/etc/systemd` was written. `update_build.main(argv) -> int` runs it on `argv[0]`, prints the messages, returns 0/1. The file runs as a script: it puts its own `lib/` on `sys.path`.
- Produces: per-repo state key `build: bool` (deploy.toml has a non-empty `[build].steps`), set in `cmd_update` beside `installer`.
- Produces: `Action('build', name, run_as='root', detail='run deploy.toml [build] steps (no install.sh)')`.

- [ ] **Step 1: Write the failing planner tests** in `tests/test_update_plan.py` (use the file's `_state(**over)` helper):
  - `test_a_component_with_a_build_and_no_installer_is_built_after_the_pull`: repo `{'behind': 2, 'installer': False, 'build': True}` → kinds `['pull', 'build', 'restart']`; the `build` action's `run_as == 'root'`.
  - `test_a_component_with_an_installer_and_a_build_runs_only_the_installer`: `installer: True, build: True` → kinds `['pull', 'install', 'restart']`.
  - `test_neither_installer_nor_build_says_so_plainly`: `installer: False, build: False` → kinds `['pull', 'restart']`; pull detail contains `'no install.sh and no deploy.toml [build]'` and does not contain `'consumed editably'`.
  - `test_venv_skew_on_a_build_component_plans_the_build`: `behind: 0, installer: False, build: True`, `venv_skew=[name]` → a `build` action, no refusal.
  - Update the two existing tests that assert the old wording (`test_the_plan_says_why_it_skipped_the_install_rather_than_going_quiet` and its neighbour at ~:240) to the new wording.
- [ ] **Step 2:** `TMPDIR=/home/mjh/t2tmp .venv/bin/pytest tests/test_update_plan.py -q --override-ini addopts=` → the new tests FAIL.
- [ ] **Step 3: Implement the planner.** `_ORDER = ('pull', 'install', 'build', 'rebuild-radiod', 'wisdom', 'restart')`. New note text: `'no install.sh and no deploy.toml [build], so the pull is the whole update'`. Add `_has_build(info) -> bool` (`info.get('build') is True`; absent means no build). In the install loop: installer → `install`; else build → `build`; else the existing skip/refusal. The skew refusal text stays for the no-installer, no-build case. The pull's note appends only when neither exists.
- [ ] **Step 4:** planner tests PASS.
- [ ] **Step 5: Write the failing `force` test** in `tests/test_deploy_build_steps.py`: `test_force_runs_the_steps_even_when_every_product_exists` — `produces` present on disk, `force=True`, fake runner records calls → the step ran once and `ok is True`. Run → FAIL. Implement `force`. Run → PASS.
- [ ] **Step 6: Write the failing executor tests** in `tests/test_update_executor.py` on the `_Rig` harness (a component with a `deploy.toml` whose `[build] steps = ["touch built.marker"]` and no `install.sh`):
  - `test_update_runs_the_build_of_a_component_without_an_installer`: after `--apply`, `built.marker` exists in the checkout and the output says `build exit 0`.
  - `test_update_skips_the_build_when_the_pull_failed`: break the origin so the pull fails → no `built.marker`, output contains `build skipped`.
  - The executor reaches root through `_as_root`; the harness's existing sudo shim covers it.
- [ ] **Step 7: Implement.** In `cmd_update`, read `d/'deploy.toml'` with `tomllib` (a parse error means `build: False`) and set `build`. In the executor, a `step.kind == 'build'` branch mirroring `install`: skip with `_warn(f'{target}: build skipped — {which} failed above')` when in `pull_failed`; otherwise run `_as_root([sys.executable, str(<lib>/sigmond/update_build.py), str(base/target)])` with `stdin=DEVNULL`, `capture_output=True`, `text=True`; on rc 0 `_ok(f'{target}: build exit 0')`, add to `applied` and `_record_update_step(f'build {target}')`; else `failed += 1`, print the last 15 lines and `_err(f'{target}: build exit {rc}')`. Add `'build'` wherever the executor or dry-run printer switches on step kinds (grep for `'install'` in `cmd_update`).
- [ ] **Step 8:** `TMPDIR=/home/mjh/t2tmp .venv/bin/pytest -q -x` (full suite) → green.
- [ ] **Step 9: Mutation check.** Revert `force=True` in `update_build.run` to `force=False`; confirm a test fails; restore.
- [ ] **Step 10: Commit** `update: run deploy.toml [build] for a component with no install.sh (v3.83 row 11)`.

### Task 2: mag-recorder probes its I2C addresses at install (row 3, part 1)

**Files:**
- Create: `mag-recorder/src/mag_recorder/i2c_probe.py`
- Modify: `mag-recorder/src/mag_recorder/cli.py` (new `probe-i2c` subcommand)
- Modify: `mag-recorder/install.sh` (call the probe after `install_config` and the udev step)
- Modify: `mag-recorder/config/mag-recorder-config.toml.template` (comment beside `i2c_address`)
- Test: `mag-recorder/tests/test_i2c_probe.py`

**Interfaces:**
- Produces: `parse_scan(text: str) -> list[int]` — every `Address: 0xNN` in `mag-usb -S` output.
- Produces: `choose(addresses: list[int]) -> tuple[int | None, int | None, list[str]]` — (magnetometer in 0x20–0x23, temperature in 0x18–0x1F, warnings). Exactly one answer in a range picks it; none or several pick `None` with a warning naming what answered.
- Produces: `write_addresses(config_path: Path, mag: int | None, temp: int | None) -> list[str]` — rewrites the live `i2c_address = …` line under `[mag]` and sets a live `remote_temp_address = 0xNN` line in the same table, as text edits that keep every comment; `None` leaves that key untouched. Values written as `0xNN`, uppercase hex digits.
- Produces: `probe(config_path: Path, *, device=Path('/dev/ttyMAG0'), mag_usb='/usr/local/bin/mag-usb', runner=subprocess.run, timeout=10) -> int` — always returns 0; prints what it found and wrote.
- CLI: `mag-recorder probe-i2c [--config PATH]`, default `/etc/mag-recorder/mag-recorder-config.toml`.

- [ ] **Step 1: Write the failing tests** in `tests/test_i2c_probe.py`:
  - `test_parse_scan_reads_the_real_transcript`: `parse_scan(Path('../mag-usb/assets/mag-usb_output.txt').read_text())` returns the addresses that file lists (skip if the sibling checkout is absent); plus an inline B4 sample `"    Found 2 device(s):\n      Address: 0x19\n      Address: 0x20\n"` → `[0x19, 0x20]`.
  - `test_choose_picks_one_per_range`: `[0x19, 0x20]` → `(0x20, 0x19, [])`.
  - `test_choose_refuses_two_in_a_range`: `[0x20, 0x23]` → mag `None`, a warning containing `0x20` and `0x23`.
  - `test_choose_with_nothing_found`: `[]` → `(None, None, [<two warnings>])`.
  - `test_write_replaces_the_mag_address_and_adds_a_live_temp_line`: on a copy of the template, `write_addresses(p, 0x20, 0x19)` → `tomllib` reads `mag.i2c_address == 0x20` and `mag.remote_temp_address == 0x19`; the line `# remote_temp_address = 0x1F` still exists. (Read the template first: if `remote_temp_address` belongs in a different table, use the table `config.py:168` reads.)
  - `test_write_with_none_leaves_the_key_alone`.
  - `test_probe_without_the_device_exits_zero_and_writes_nothing`: missing `device` path → 0, config bytes unchanged, runner never called.
  - `test_probe_survives_a_hung_scan`: runner raises `subprocess.TimeoutExpired` → 0, config unchanged, output mentions the timeout.
  - `test_probe_survives_a_failed_scan`: runner returns rc 1 with `No I2C devices found.` on stderr → 0, config unchanged.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement** `i2c_probe.py` and the `probe-i2c` subcommand. The scan reads stdout and stderr together.
- [ ] **Step 4:** run → PASS; then the full mag-recorder suite → green.
- [ ] **Step 5: install.sh.** Add `probe_i2c()`, called on every run after `install_config` and after the udev rule is installed and triggered. It: returns at once when `/dev/ttyMAG0` is absent (info line); records whether `mag-recorder.service` is active; if active, stops it; runs `/usr/local/bin/mag-recorder probe-i2c || true`; starts the service again only if it was active. Template: beside `i2c_address = 0x23`, a comment that install's probe overwrites this with the address that answers, and that 0x23 is only the fallback when nothing answered. Check `bash -n install.sh`.
- [ ] **Step 6: Commit** `probe-i2c: install writes the magnetometer and temperature addresses that answer (v3.83 row 3)`.

### Task 3: mag-recorder refuses all-zero samples (row 3, part 2)

**Files:**
- Modify: `mag-recorder/src/mag_recorder/core/supervisor.py` (the loop in `run_supervisor`, ~292–306)
- Test: `mag-recorder/tests/test_zero_guard.py`

**Interfaces:**
- Produces: `class ZeroGuard` with `admit(sample: dict) -> bool` (False when `x`, `y` and `z` all equal 0; `rt` ignored) and a `dropped` count. It logs one WARNING when the first zero sample arrives after a good one (or at start) and one INFO when a good sample ends a zero run, naming how many it dropped. The supervisor loop skips `writer.write` when `admit` returns False.

- [ ] **Step 1: Write the failing tests:** `test_zero_sample_is_refused`, `test_nonzero_sample_passes` (`x=0, y=0, z=1` passes; `rt=0` with nonzero xyz passes), `test_logs_once_per_transition` (with `caplog`: 100 zeros then 1 good → exactly one WARNING and one INFO; the INFO contains `100`), and `test_supervisor_never_spools_a_zero_sample` (drive the loop through the simulator or a fake source the existing supervisor tests use, feeding one zero and one good sample → the spool file holds only the good one).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS, full suite green.
- [ ] **Step 5: Commit** `supervisor: never store an all-zero magnetometer sample (v3.83 row 3)`.

### Task 4: sigmond's bring-up check probes the configured address (row 3, part 3)

**Files:**
- Modify: `sigmond/lib/sigmond/hardware.py` (`rm3100_responds` ~289–327; the gate hint text that says 0x23)
- Test: `sigmond/tests/test_hardware.py`

**Interfaces:**
- Produces: `configured_mag_address(path=Path('/etc/mag-recorder/mag-recorder-config.toml')) -> int` — `[mag].i2c_address` from the file, 0x23 when the file or key is missing or unreadable. `rm3100_responds(..., config_path=…)` passes `-A 0x{addr:02X}`.

- [ ] **Step 1: Failing tests:** `test_rm3100_probe_uses_the_configured_address` (config with `i2c_address = 0x20`, fake `run` records argv → `'-A'` followed by `'0x20'`), `test_rm3100_probe_falls_back_to_0x23_without_a_config`. **Step 2:** FAIL. **Step 3:** implement; the hint names the configured address. **Step 4:** PASS + full suite.
- [ ] **Step 5: Commit** `hardware: the RM3100 check probes the address mag-recorder is configured for (v3.83 row 3)`.

### Task 5: `smd psws save-keys` and `restore-keys` (row 4, part 1)

**Files:**
- Modify: `sigmond/lib/sigmond/psws.py` (two new verbs)
- Modify: `sigmond/bin/smd` (parsers beside `verify` at ~27530; dispatch; root elevation as the other root-needing psws verbs do)
- Modify: `sigmond/docs/` CLI reference table if one lists `smd psws` verbs (the suite's docs-table test will say)
- Test: `sigmond/tests/test_psws_keys.py`

**Interfaces:**
- Produces: `KEY_TREES = ('etc/hs-uploader/keys', 'home/timestd/.ssh')`.
- Produces: `save_keys(output: Path, *, root=Path('/')) -> int` — gzip tar of whichever of `KEY_TREES` exist under `root`, member names relative to `/` exactly as `tar czf site-keys.tar.gz -C / etc/hs-uploader/keys home/timestd/.ssh` writes them; file mode 600; returns 0, or 1 when neither tree exists.
- Produces: `restore_keys(archive: Path, *, root=Path('/'), chown=os.chown, verify=cmd_site_verify) -> int` — refuses (returns 4, extracts nothing) when any member is a symlink, hard link, device, absolute, contains `..`, or lies outside `KEY_TREES`; otherwise extracts, then sets owner and mode as `sigmond-wizard.sh` ~3150–3172 does (keys dir `hsupload:sigmond`, falling back to `root:sigmond` when user `hsupload` does not exist; `id_ed25519*` 600; `*.pub` 644; `home/timestd/.ssh` `timestd:timestd`, dir 700); then returns `verify()`.
- CLI: `smd psws save-keys [--output FILE]` (default `~/site-keys.tar.gz` of the invoking user, owned by that user after writing), `smd psws restore-keys FILE`.

- [ ] **Step 1: Failing tests** (all under a `tmp_path` root, fake `chown` recording calls, fake `verify`):
  - `test_save_then_restore_round_trips_bytes_and_modes`.
  - `test_saved_archive_matches_the_wizard_member_layout`: member names start with `etc/hs-uploader/keys` or `home/timestd/.ssh`.
  - `test_restore_refuses_traversal`, `test_restore_refuses_a_symlink`, `test_restore_refuses_a_path_outside_the_key_trees`: each returns 4 and writes nothing under root.
  - `test_restore_sets_owners_like_the_wizard`.
  - `test_restore_returns_verifys_code`: fake verify returns 3 → restore returns 3.
  - `test_save_with_no_keys_returns_1`.
- [ ] **Step 2:** FAIL. **Step 3:** implement with `tarfile`; check every member before extracting any. **Step 4:** PASS + full sigmond suite green.
- [ ] **Step 5: Commit** `psws: save-keys and restore-keys carry a station's registered keys across a reflash (v3.83 row 4)`.

### Task 6: the reflash section in the three install docs (row 4, part 2)

**Files:**
- Modify: `sigmond-appliance/INSTALL.md` §4 (~line 130), `sigmond-appliance/docs/install-page.html` §7 (~line 414), `sigmond-appliance/QUICKSTART.txt` (~line 262)
- Create: `sigmond-appliance/test-reflash-docs.sh`

**Interfaces:**
- Consumes: Task 5's two verbs, exact spelling `smd psws save-keys` and `smd psws restore-keys`.

- [ ] **Step 1: Write the failing check** `test-reflash-docs.sh` (style of the other `test-*.sh` there: plain bash, prints PASS/FAIL per assertion, exits non-zero on any FAIL). For each of the three files it asserts the text contains `Reflashing or replacing a station`, `smd psws save-keys`, `smd psws restore-keys` and `smd psws verify`, and that the save step comes before the restore step, and the verify step after both. Run → FAIL.
- [ ] **Step 2: Rewrite the three sections.** Heading "Reflashing or replacing a station". Order: (1) on the station, before the flash: `smd psws save-keys`, then copy `site-keys.tar.gz` off the station; (2) after writing the stick, copy the file onto its small EFI volume — the installer restores it and checks PSWS; (3) if the file missed the stick: copy it to the new station and run `smd psws restore-keys site-keys.tar.gz`; (4) finish with `smd psws verify`, which must report the key verified. Keep the old `tar czf …` one-liner as the fallback for a station still on v3.82 or older, which has no `save-keys`. Follow the voice of the surrounding text: short sentences, active voice, name who acts. In the HTML, match the page's existing markup for steps and code.
- [ ] **Step 3:** run → PASS.
- [ ] **Step 4: Commit** `docs: reflashing or replacing a station keeps its registered keys (v3.83 row 4)`.

### Task 7: record the rows

**Files:**
- Modify: `sigmond/tasks/release-v383.md` (rows 3, 4, 11 status column)

- [ ] **Step 1:** Set each row's status to `built 2026-10-28` with the commit ranges per repo. **Step 2: Commit** `tasks: v3.83 rows 3, 4 and 11 built`.

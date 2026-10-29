# Station Layering, First Steps — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the clients' storage and upload path loose from sigmond, stop two host-to-VM leaks in the wizard, and measure how far each client stands from running alone.

**Architecture:** hs-uploader gains one function, `sink_path()`, that every writer, source and the wake socket use to find the sink; the order puts an explicit setting first and keeps every existing station on `/var/lib/sigmond/sink.db`. Clients import the writer from hs-uploader and mark sigmond's directories optional in their units. The wizard stops overwriting a newer pm-align and stops stamping the host's provenance over the VM's. A podman harness installs each client into a clean Debian 13 container with no sigmond and reports what works.

**Tech Stack:** Python 3.11 (stdlib only in `sink/writer.py`), bash, systemd units, uv, pytest, podman.

**Spec:** `sigmond/docs/superpowers/specs/2026-10-29-station-layering-design.md` — §11 steps 1–3, with §6.1, §6.4, §4.1, §7.1, §9.

## Global Constraints

- `hs_uploader/sink/writer.py` stays stdlib only: sigmond ships a verbatim copy (`lib/sigmond/hamsci_sink/_bundled.py`) into venvs without hs-uploader.  It may not import other `hs_uploader` modules except inside an `ImportError` guard.
- No live station moves its sink: a station that holds `/var/lib/sigmond/sink.db` keeps using it (spec §6.4).
- Clients keep accepting the old import name `sigmond.hamsci_sink` as a fallback for one release (spec §6.4).
- Clients keep sharing editable libraries; no per-client pins in this plan (spec §10, item 4).
- The sink path and the wake socket never disagree: the socket sits in the sink's directory (`upload-wake.sock`) unless an explicit socket override names it.
- Any commit touching `hs_uploader/sink/writer.py` needs sigmond's `_bundled.py` refreshed (recipe in sigmond's CLAUDE.md, "Sink backend selection"); sigmond's `tests/test_hamsci_sink_compat.py` fails until then.
- No deploy to any station in this plan.  Nothing here runs on B4, ND or the rig.
- Develop on main in each repo.  Tests write under `TMPDIR=/home/mjh/t2tmp`, never `/tmp`.  The sigmond suite never elevates (never set `SIGMOND_TEST_ALLOW_SUDO`).
- Commit trailers on every commit: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01H4HYgCZ3dRtK4XWA1Xyoc3`.
- These repos have no exclusive owner: tell Rob on the claude-bus what changed in each repo before pushing.

## Review Focus

1. A station running mixed versions (a client or sigmond's bundled copy still on the old writer, the daemon on the new): every party must resolve to `/var/lib/sigmond/sink.db`.  Pinned by Task 1's `test_legacy_sink_wins_without_config` and Task 3's legacy branch.
2. A fresh install where the config exists and a legacy `sink.db` appears later (sigmond's installer creates an empty one): the config must keep winning, so no writer switches files mid-life.  Pinned by Task 1's `test_config_beats_a_later_legacy_file`.
3. A malformed or unreadable `/etc/hs-uploader/sink.toml`: the writer must not raise, must log one ERROR, and must fall through to the legacy-then-default order rather than drop rows.  Pinned by Task 1's `test_malformed_config_falls_through_and_logs_once`.
4. `SIGMOND_SQLITE_PATH` set in coordination.env on existing stations: it must still win over everything.  Pinned by Task 1's `test_env_override_wins`.
5. A unit on a host without `/var/lib/sigmond`: it must start.  Pinned by Task 4 and Task 5's unit-file tests and measured by Task 7.

---

## Part A — hs-uploader finds the sink by one rule

### Task 1: `sink_path()` in the writer

**Files:**
- Modify: `hs-uploader/src/hs_uploader/sink/writer.py` (`_DEFAULT_SQLITE_PATH`, `_default_sqlite_writable`, `SqliteConfig.from_env`, `Writer.from_env`, module docstring)
- Modify: `hs-uploader/src/hs_uploader/sink/__init__.py` (export `sink_path`)
- Test: `hs-uploader/tests/test_sink_path.py` (create)

**Interfaces:**
- Produces: `hs_uploader.sink.writer.sink_path(env: Optional[Mapping[str, str]] = None, *, config_file: str = "/etc/hs-uploader/sink.toml", legacy_path: str = "/var/lib/sigmond/sink.db", state_dir: Optional[str] = None) -> Optional[str]`, re-exported as `hs_uploader.sink.sink_path`.
- Produces: constants `SINK_CONFIG = "/etc/hs-uploader/sink.toml"`, `LEGACY_SINK_PATH = "/var/lib/sigmond/sink.db"`.

Resolution order, first match wins:
1. `env["HAMSCI_SINK_PATH"]`, then `env["SIGMOND_SQLITE_PATH"]` (non-empty after strip).
2. `config_file` parsed with `tomllib`, key `path` (non-empty string).  A missing file skips this step silently.  A file that fails to read or parse, or lacks a string `path`, logs one ERROR naming the file and skips this step.
3. `legacy_path`, if that file exists.
4. `<state_dir>/sink.db`, where `state_dir` defaults to `env["HS_UPLOADER_STATE_DIR"]` or `/var/lib/hs-uploader` (the same directory `sinkswitch.marker_path()` uses), if `_default_sqlite_writable()` passes for it.
5. `None`.

`Writer.from_env` and `SqliteConfig.from_env` both call `sink_path(e)`.  When it returns `None`, `Writer.from_env` logs one ERROR, once per process, naming `SINK_CONFIG` as the place to set a path, and returns the no-op writer it returns today.  The "one ERROR per process" uses a module-level flag.

- [ ] **Step 1: Write the failing tests** in `tests/test_sink_path.py`, each with temp dirs under `tmp_path`:
  - `test_env_override_wins`: env `SIGMOND_SQLITE_PATH=/x/a.db`, a config naming `/y/b.db`, an existing legacy file → `"/x/a.db"`; and `HAMSCI_SINK_PATH` beats `SIGMOND_SQLITE_PATH`.
  - `test_config_beats_a_later_legacy_file`: config `path = "<tmp>/new/sink.db"`, legacy file exists → the config's path.
  - `test_legacy_sink_wins_without_config`: no config, legacy file exists, state dir writable → the legacy path.
  - `test_fresh_install_takes_the_state_dir`: no config, no legacy file, writable `state_dir` → `"<state_dir>/sink.db"`.
  - `test_nothing_usable_returns_none`: no config, no legacy, unwritable state dir parent → `None`.
  - `test_malformed_config_falls_through_and_logs_once`: config text `path = [` with a legacy file present → legacy path; calling twice yields exactly one ERROR record (`caplog`) mentioning the config file.
  - `test_writer_from_env_uses_sink_path`: `Writer.from_env(table="spots", mode="wspr", env={"SIGMOND_SQLITE_PATH": "<tmp>/s.db"})` → `is_noop()` is False.
  - `test_writer_from_env_noop_logs_once`: patch `sink_path` to return `None`; two `Writer.from_env` calls → both `is_noop()`, one ERROR naming `/etc/hs-uploader/sink.toml`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd ~/hamsci/repos/hs-uploader && TMPDIR=/home/mjh/t2tmp uv run pytest tests/test_sink_path.py -v`
Expected: FAIL, `sink_path` not defined.

- [ ] **Step 3: Implement `sink_path` and wire both `from_env` methods to it**, as specified above.  Update the module docstring's path rules to the new order.

- [ ] **Step 4: Run the new tests and the whole hs-uploader suite**

Run: `TMPDIR=/home/mjh/t2tmp uv run pytest tests/ -q`
Expected: all pass.  A pre-existing test that asserted the old `/var/lib/sigmond` default may need its expectation updated to the new order; change only the expectation, and name each such test in the commit message.

- [ ] **Step 5: Commit** (`hs-uploader`): `sink: one rule finds the sink (env, sink.toml, legacy, state dir)`.

### Task 2: sources, the pipeline factory and the wake socket follow `sink_path()`

**Files:**
- Modify: `hs-uploader/src/hs_uploader/sources/sqlite.py` (the `from_env` near line 160: replace the hard-coded default)
- Modify: `hs-uploader/src/hs_uploader/pipeline_factory.py:125` (`wspr_cycle` `db_path` default)
- Modify: `hs-uploader/src/hs_uploader/sources/wspr_cycle.py:89` (constructor default)
- Modify: `hs-uploader/src/hs_uploader/wake.py` (`wake_path`, `_DEFAULT_SOCK`, docstring)
- Test: `hs-uploader/tests/test_sink_path_consumers.py` (create)

**Interfaces:**
- Consumes: `sink_path()` from Task 1.
- Produces: `wake.wake_path(env: Optional[Mapping[str, str]] = None) -> str`.  Order: `HS_UPLOADER_WAKE_SOCK`, `WSPR_UPLOAD_WAKE_SOCK`, then `<dirname(sink_path(env))>/upload-wake.sock`, then `<state dir>/upload-wake.sock` when `sink_path` returns `None`.

The SQLite source keeps its existing guard: it adopts a resolved path only when that file exists, else returns `None` as today.  A pipeline spec's explicit `db_path` still wins over the default.

- [ ] **Step 1: Write the failing tests**:
  - `test_sqlite_source_follows_config`: config naming an existing `<tmp>/x/sink.db`, no env → the source's path equals it.
  - `test_wspr_cycle_default_follows_sink_path`: build a `wspr_cycle` spec with no `db_path` while `sink_path` is patched to `<tmp>/s.db` → the built source's db path equals it; with `db_path` in the spec, the spec wins.
  - `test_wake_socket_sits_beside_the_sink`: `sink_path` patched to `/var/lib/sigmond/sink.db` → `"/var/lib/sigmond/upload-wake.sock"`; patched to `<tmp>/hs/sink.db` → `"<tmp>/hs/upload-wake.sock"`; `HS_UPLOADER_WAKE_SOCK` set → that value.

- [ ] **Step 2: Run, see them fail.** Run: `TMPDIR=/home/mjh/t2tmp uv run pytest tests/test_sink_path_consumers.py -v`

- [ ] **Step 3: Implement** the four changes above.  No other file in `src/` may keep the literal `/var/lib/sigmond/sink.db` except `LEGACY_SINK_PATH` and docstrings; check with `grep -rn '/var/lib/sigmond/sink.db' src/ | grep -v '"""'`.

- [ ] **Step 4: Run the whole suite.** Expected: all pass.

- [ ] **Step 5: Commit** (`hs-uploader`): `sources, factory, wake: follow sink_path()`.

### Task 3: hs-uploader's install names the sink explicitly; sigmond refreshes its bundled copy

**Files:**
- Modify: `hs-uploader/install.sh` (new function `write_sink_config`, called from the main install path after `ensure_runtime_state`)
- Modify: `hs-uploader/CLAUDE.md` ("Production paths": the new rule)
- Test: `hs-uploader/tests/test_install_sink_config.py` (create; runs the bash function)
- Modify: `sigmond/lib/sigmond/hamsci_sink/_bundled.py` (refresh by the recipe), `sigmond/lib/sigmond/hamsci_sink/__init__.py` (docstring: path rule), `sigmond/CLAUDE.md` ("Sink backend selection": the new rule)

**Interfaces:**
- Produces: bash function `write_sink_config <config_file> <legacy_path> <state_dir>`.  When `<config_file>` exists it changes nothing.  Otherwise it writes, mode 0644, atomically (temp file then `mv`): `path = "<legacy_path>"` when that file exists, else `path = "<state_dir>/sink.db"`, preceded by one comment line saying hs-uploader's install wrote it and why.

- [ ] **Step 1: Write the failing tests**: source `install.sh`'s function in a subshell (guard the script's main body so sourcing does not run it, e.g. `[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"`, if it does not already have such a guard) and call it on temp paths:
  - `test_existing_config_is_left_alone`: a config with `path = "/keep"` → unchanged, byte for byte.
  - `test_legacy_station_names_the_legacy_sink`: legacy file exists → config reads `path = "<legacy>"`.
  - `test_fresh_install_names_the_state_dir`: no legacy file → `path = "<state_dir>/sink.db"`.
  - `test_written_config_parses_and_resolves`: the written file, fed to `sink_path(config_file=...)`, returns the same path.

- [ ] **Step 2: Run, see them fail.** Run: `TMPDIR=/home/mjh/t2tmp uv run pytest tests/test_install_sink_config.py -v`

- [ ] **Step 3: Implement** `write_sink_config` and call it with `/etc/hs-uploader/sink.toml /var/lib/sigmond/sink.db /var/lib/hs-uploader`.  Update hs-uploader's CLAUDE.md.

- [ ] **Step 4: Run hs-uploader's suite; commit hs-uploader** (`install: name the sink in /etc/hs-uploader/sink.toml`).  Do not push; sigmond's refresh recipe reads the local `../hs-uploader` checkout.

- [ ] **Step 5: In sigmond, refresh `_bundled.py`** with the recipe in sigmond's CLAUDE.md, update the two docs, and run `TMPDIR=/home/mjh/t2tmp .venv/bin/python -m pytest -q --override-ini addopts= tests/test_hamsci_sink_compat.py` then the full sigmond suite.  Expected: all pass.

- [ ] **Step 6: Commit** (`sigmond`): `hamsci_sink: refresh bundled writer (sink_path rule)`.

## Part B — clients import the writer from hs-uploader

### Task 4: wspr-recorder, psk-recorder, meteor-scatter

One brief, three repos, one commit per repo.  The same change in each.

**Files:**
- Modify: `wspr-recorder/wspr_recorder/spot_sink.py` (`_resolve_writer`, line ~150)
- Modify: `psk-recorder/src/psk_recorder/core/ch_tailer.py` (`_default_writer_factory`, line ~549)
- Modify: `meteor-scatter/src/meteor_scatter/core/ch_tailer.py` (`_default_writer_factory`, line ~510)
- Modify: units — `wspr-recorder/systemd/wspr-recorder@.service`, `wspr-recorder/systemd/wspr-uploader.service`, `psk-recorder/systemd/psk-recorder@.service`, `meteor-scatter/systemd/meteor-scatter@.service`
- Test: one new test file per repo, `tests/test_sink_import.py`

**Interfaces:**
- Consumes: `hs_uploader.sink.Writer` (exists today).
- Produces: in each repo, the writer import reads, in this order: `from hs_uploader.sink import Writer`; on `ImportError`, `from sigmond.hamsci_sink import Writer`; on a second `ImportError`, the existing "not importable" path.  The log line in that last path names `hs_uploader.sink`, not `sigmond.hamsci_sink`.

In each unit, every `ReadWritePaths=` entry for `/var/lib/sigmond` or `/var/lib/hs-uploader` gains systemd's `-` prefix (optional path).  Leave every other entry as it stands.

- [ ] **Step 1: Write the failing tests**, per repo:
  - `test_writer_comes_from_hs_uploader_without_sigmond`: with `sys.modules["sigmond"] = None` and `sys.modules["sigmond.hamsci_sink"] = None` (which makes their import raise), call the repo's resolver with `SIGMOND_SQLITE_PATH` pointing into `tmp_path` → a writer whose class's module is `hs_uploader.sink.writer` and `is_noop()` is False.
  - `test_unit_marks_sigmond_paths_optional`: parse each listed unit file; every `ReadWritePaths` token naming `/var/lib/sigmond` or `/var/lib/hs-uploader` starts with `-`.

- [ ] **Step 2: Run, see them fail**, in each repo: `TMPDIR=/home/mjh/t2tmp uv run pytest tests/test_sink_import.py -v`

- [ ] **Step 3: Implement** the import order and the unit edits.  Update docstrings and comments that say `sigmond.hamsci_sink` is the writer's home.

- [ ] **Step 4: Run each repo's whole suite.** Expected: all pass.

- [ ] **Step 5: Commit** in each repo: `sink: import the writer from hs_uploader; sigmond paths optional in units`.

### Task 5: codar-sounder, hf-tec, superdarn-sounder declare hs-uploader

**Files:**
- Modify: `pyproject.toml` in each (add `"hs-uploader>=0.1.0"` to dependencies; add `hs-uploader = { path = "../hs-uploader", editable = true }` under `[tool.uv.sources]`), then `uv lock` to regenerate `uv.lock`
- Modify: each `scripts/install.sh`: ensure the `../hs-uploader` sibling exists, the same way it already ensures `ka9q-python` and `hamsci-dsp` (clone from `https://github.com/HamSCI/hs-uploader` when absent)
- Modify: the writer import in `codar-sounder/src/codar_sounder/core/daemon.py` (~line 493), `hf-tec/src/hf_tec/core/output.py` (`_HamsciSinkWriter`, ~line 227), `superdarn-sounder/src/superdarn_sounder/core/output.py` (`SinkWriter`, ~line 48): same order as Task 4
- Modify: units `hf-tec/systemd/hf-tec@.service`, `superdarn-sounder/systemd/superdarn-sounder@.service`, and codar-sounder's unit if it names `/var/lib/sigmond`: `-` prefix as in Task 4
- Test: `tests/test_sink_import.py` in each repo, same two tests as Task 4 (the resolver is the class or `__init__` named above)

**Interfaces:**
- Consumes: as Task 4.

- [ ] **Step 1: Write the failing tests** (as Task 4).
- [ ] **Step 2: Run, see them fail.**
- [ ] **Step 3: Implement**; run `uv lock` in each repo and confirm `uv sync --frozen` succeeds with `../hs-uploader` present.
- [ ] **Step 4: Run each repo's whole suite.** Expected: all pass.
- [ ] **Step 5: Commit** in each repo: `sink: declare hs-uploader; import the writer from it`.

## Part C — the wizard stops leaking the host into the VM

### Task 6: no pm-align downgrade; no host provenance over the VM's

**Files:**
- Modify: `sigmond/scripts/proxmox/sigmond-wizard.sh` — the `install -m 755 /root/sigmond-appliance/sigmond/scripts/proxmox/pm-align.py /usr/local/sbin/pm-align` line (~4382), and the two `gexec` lines that write `/etc/sigmond-appliance/version` and `manifest.txt` into the VM (~3341 and ~3348)
- Test: `sigmond/tests/test_wizard_host_vm_leaks.py` (create; static checks on executable lines, in the style of `tests/test_wizard_no_wd30.py`)

**Interfaces:** none.

The changes:
- The wizard installs pm-align only when `/usr/local/sbin/pm-align` does not exist.  pm-align refreshes itself on every `--apply`, so the wizard's copy matters only on a host that never had one.
- The VM gets the host's `version` only when the VM has no non-empty `/etc/sigmond-appliance/version`; likewise `manifest.txt`.  The guard runs inside the `gexec` command (`[ -s FILE ] || ...`), so a first install still stamps both.

- [ ] **Step 1: Write the failing tests**:
  - `test_wizard_never_overwrites_an_installed_pm_align`: every executable line that installs to `/usr/local/sbin/pm-align` carries a test for that file's absence (`[ -e /usr/local/sbin/pm-align ] ||` or `[ ! -e /usr/local/sbin/pm-align ] &&`) on the same logical line.
  - `test_vm_version_stamp_only_when_absent`: the executable line writing `/etc/sigmond-appliance/version` inside a `gexec` carries `[ -s /etc/sigmond-appliance/version ] ||` before the write.
  - `test_vm_manifest_copy_only_when_absent`: likewise for `manifest.txt`.

- [ ] **Step 2: Run, see them fail.** Run: `cd ~/hamsci/repos/sigmond && TMPDIR=/home/mjh/t2tmp .venv/bin/python -m pytest -q --override-ini addopts= tests/test_wizard_host_vm_leaks.py`

- [ ] **Step 3: Implement** the three guards.  Keep each `|| say "WARN: ..."` fallback as it stands.  Add one comment line at each guard giving the reason (spec §4.1, §7.1).

- [ ] **Step 4: Run the full sigmond suite.** Expected: all pass, including `tests/test_wizard_guest_senders.py` (the guest-agent ASCII rule: the guards add only ASCII).

- [ ] **Step 5: Commit** (`sigmond`): `wizard: never downgrade pm-align; never stamp host provenance over the VM's`.  Add both findings to `~/hamsci/tasks/v384-list.md` as done.

## Part D — measure each client alone

### Task 7: the client autonomy check

**Files:**
- Create: `sigmond/scripts/dev/client-autonomy/Containerfile`
- Create: `sigmond/scripts/dev/client-autonomy/check.sh`
- Create: `sigmond/scripts/dev/client-autonomy/README.md` (what it measures, how to run it, what it does not test)
- Output: `~/hamsci/tasks/2026-10-29-client-autonomy-report.md` (not committed to any repo)

**Interfaces:**
- Produces: `check.sh [--keep] <client> [<client> ...]`, run on the devbox as mjh, using rootless podman.  Exit 0 when it ran to the end, whatever the clients' results; non-zero only when the harness itself failed.  Prints one markdown table row per client and appends them to the report path given by `REPORT=` (default the path above).

Prerequisite: podman on the devbox.  The devbox has none today.  Michael installs it: `! sudo apt-get install -y podman` (devbox, Claude prompt).  Do not install it any other way.

The Containerfile: `FROM docker.io/library/debian:trixie`, installs `systemd systemd-sysv git ca-certificates curl sudo python3 build-essential`, and sets `CMD ["/sbin/init"]`.  No sigmond, no `/var/lib/sigmond`, no `/opt/git/sigmond/sigmond`.

For each client, `check.sh`:
1. Builds the image once (tag `hamsci-autonomy:trixie`).
2. Starts a container with systemd as init (`podman run -d --systemd=always`).
3. Copies the client's checkout and each sibling its `pyproject.toml` names under `[tool.uv.sources]` from `~/hamsci/repos/` into `/opt/git/sigmond/<name>` in the container, which is where the install scripts expect siblings.  It copies no sigmond checkout.
4. Runs the client's installer non-interactively (`install.sh --yes` where it accepts `--yes`, else plain), capturing exit code and the last 30 lines.
5. Records, per client:
   - `install`: exit code.
   - `sigmond in venv`: whether `import sigmond` succeeds in the client's venv (expected: no).
   - `writer`: in the client's venv, `hs_uploader.sink.sink_path()` and `Writer.from_env(table="probe", mode="probe").is_noop()`; for codar-sounder, hf-tec and superdarn-sounder also whether `hs_uploader` imports at all.
   - `units`: each unit the client's `deploy.toml` names, enabled and started (one instance named `autonomy` for templates), then `systemctl is-active` after 30 s, and `Result=` plus the exit status from `systemctl show` when not active.  An `exit-code` of 226 means a missing path; the row says so.
6. Removes the container unless `--keep`.

The check does not feed a radio stream and does not run radiod; a client that exits for want of radiod shows as such, and the README says so.  Feeding a recorded stream comes later (spec §9).

Clients to run: wspr-recorder, psk-recorder, meteor-scatter, mag-recorder, hf-timestd, codar-sounder, hf-tec, superdarn-sounder, hamsci-physics, gpsdo-monitor, hs-uploader.

- [ ] **Step 1: Write `check.sh`, the Containerfile and the README.** Test the harness itself on one client first: `REPORT=/home/mjh/t2tmp/autonomy-trial.md ~/hamsci/repos/sigmond/scripts/dev/client-autonomy/check.sh gpsdo-monitor`.  Expected: a table row; the container gone afterwards (`podman ps -a` empty).
- [ ] **Step 2: Run all eleven clients** after Tasks 1–5 have landed.  Expected: the report file holds eleven rows.
- [ ] **Step 3: Add a short findings section** to the report: for each row that failed, the wrong-way dependency it names, in one line, with the file and line that causes it.
- [ ] **Step 4: Commit the harness** (`sigmond`): `dev: client autonomy check (podman, no sigmond)`.  Send the report to Michael.

## After the last task

- Run each touched repo's full suite once more.
- Post one bus message to Rob listing each repo's new commits and what they change, before pushing.
- Push each repo only with Michael's go.
- Note in the spec's status line which of §11 steps 1–3 landed.

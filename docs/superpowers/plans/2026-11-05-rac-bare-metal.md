# RAC on bare metal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A bare-metal sigmond machine registers with the RAC gateway by itself and appears on the RAC page under its machine name, using the same code a Proxmox host runs.

**Architecture:** The wizard's RAC functions and switch stay in `scripts/proxmox/sigmond-wizard.sh` for now and learn a second mode, `RAC_MODE=station`.  A new entry point, `scripts/rac/sigmond-rac`, sources the wizard as a library (it returns before its dispatch) and drives register, retry and activate on a bare-metal machine.  Bring-up runs `sigmond-rac activate` at the end of a complete run; `smd remote` runs the local switch when no Proxmox pairing exists.

**Tech Stack:** bash (wizard, switch, entry point), Python 3.11 stdlib (`bin/smd`, `lib/sigmond/commands/remote.py`), pytest via `.venv/bin/pytest`.

**Spec:** `docs/superpowers/specs/2026-11-05-one-rac-design.md` (§4, §4.1, §7 steps 1-4).  Code map: the research notes summarised under "Ruling 1" below; line numbers in this plan refer to sigmond main at 6c20127.

## Rulings made while planning

1. **The code stays in the wizard file for now (deviates from spec §4 "Where the code lives").**
   pm-align (`scripts/proxmox/pm-align.py:194`, `:234`, `:287-294`) delivers only
   `sigmond-wizard.sh` to a field Proxmox host, installs it as `sigmond-setup`, and extracts the
   switch and unit heredocs from its text.  Six tests extract functions from the wizard by line
   scan and assert positions across the one file.  Moving the code now would break every field
   host's next `pm-align --apply` and rewrite six tests.  Instead the wizard gains a library mode,
   and the station entry point sources it: one code base, as Michael asked, with the physical
   move deferred until pm-align learns to deliver `scripts/rac/` (Rob's area; spec §7 step 5
   timeframe).  Cost if wrong: a later move of already-shared code, no rework of behaviour.
2. **Station proxies keep the `vm-*` names** (`$SITE-vm-ssh`, `-vm-web`, `-vm-station`,
   `-vm-gmag`), so the gateway's dashboard shows a station's channels in the same columns as a
   host's.  Cost if wrong: a rename on the station side only.
3. **State stays in `/etc/sigmond-appliance/`** on a station too (the switch's default
   `MARK_DIR`), plus a new file `rac-mode` holding `host` or `station`.  Absent reads `host`, so
   every existing Proxmox host behaves as today.
4. **Host-mode naming of NEW installs stays the reporter ID in this plan.**  Spec §4.1 moves new
   installs to `<CALL>-<MACHINE>`; on a Proxmox host that changes the wizard's questions, which
   this plan does not touch.  The order flip (recorded `rac-site` first) lands for both modes.
5. **A Proxmox appliance VM never runs station mode.**  `/etc/sigmond-appliance/version` or a
   pairing key (`/etc/sigmond/rac-switch.key`) means a host carries this machine's RAC already.

## Global Constraints

- Every existing RAC test passes unchanged except where a task names the change.
- Absent `RAC_MODE` and absent `rac-mode` file: host behaviour, byte for byte, in the TOML, the switch's output and the units.
- Station channels (spec §4 table): local `127.0.0.1:22`, `:8081`, `:8000`, `:8082`; remote `35800+n`, `45800+n`, `48800+n`, `49800+n`; no host-ssh, no host-ui.
- Station site name (spec §4.1): recorded `rac-site` wins; else `SIGMOND_RAC_SITE`; else `<CALL>-<HOSTNAME>` upper-cased, where CALL comes from `[station] callsign` in `/etc/sigmond/site-profile.toml`, and a hostname that already starts with `<CALL>-` (case-insensitive) is used alone.  Sanitised as `rac_site_name` does today: `/`→`_`, keep `[A-Z0-9_-]`.
- A RAC failure never fails bring-up and never changes its exit code.
- No byte above 0x7F in anything the wizard sends through the guest agent (sigmond CLAUDE.md); station code sends nothing through it.
- `smd` refuses `sudo smd`; code paths reached after `_need_root` already run as root.
- Commit trailers per the repo's convention (Co-Authored-By and Claude-Session lines).

## Review Focus

1. A Proxmox appliance VM (has `/etc/sigmond-appliance/version`) finishing bring-up must not register itself as a station — the host already carries it.  Test in Task 6.
2. A host whose recorded `rac-site` differs from today's `$REPORTER` keeps the recorded name (sticky number).  Test in Task 1.
3. A hostname already prefixed by the callsign (`ac0g-b1`) yields `AC0G-B1`, not `AC0G-AC0G-B1`.  Test in Task 1.
4. A station with no callsign in its site profile and no `SIGMOND_RAC_SITE`: activate refuses with one clear line naming both, registers nothing, exits non-zero; bring-up still succeeds.  Test in Tasks 4 and 6.
5. A registrar reply in station mode that omits `host_ssh`/`host_ui` still registers.  Test in Task 2.

---

### Task 1: Library mode, RAC_MODE, and the site name

**Files:**
- Modify: `scripts/proxmox/sigmond-wizard.sh` (top-level globals near W:376; `rac_site_name` W:384-393; the line just before the dispatch `case "${1:-}" in` near W:2006)
- Test: `tests/test_rac_station_mode.py` (new)

**Interfaces:**
- Produces: env `SIGMOND_WIZARD_AS_LIB=1` → sourcing the wizard defines everything above the dispatch and returns 0 before it.  Global `RAC_MODE="${RAC_MODE:-host}"`.  `rac_site_name` (no args, prints the name).  `rac_station_site_default` (no args, prints `<CALL>-<HOST>` or nothing).  Env overrides for tests: `SIGMOND_SITE_PROFILE` (default `/etc/sigmond/site-profile.toml`), `SIGMOND_RAC_HOSTNAME` (default `$(hostname -s)`).

- [ ] **Step 1: Write the failing tests** in `tests/test_rac_station_mode.py`, each running bash with `SIGMOND_WIZARD_AS_LIB=1 source <wizard>` and `MARK_DIR` pointed at a tmp dir:
  - `test_lib_mode_returns_before_dispatch`: sourcing with `--rac-register` in `$1` defines `rac_site_name` and runs no dispatch branch (a stubbed `rac_register` writes a sentinel file that must not exist).
  - `test_recorded_site_wins_over_reporter`: `rac-site` holds `AC0G_B4`, `REPORTER=AC0G/B9` → prints `AC0G_B4`.
  - `test_host_mode_falls_back_to_reporter`: no `rac-site`, `REPORTER=ac0g/b4` → `AC0G_B4`.
  - `test_station_default_call_hostname`: `RAC_MODE=station`, profile `[station]\ncallsign = "AC0G"`, hostname `sigma` → `AC0G-SIGMA`.
  - `test_station_hostname_already_prefixed`: hostname `ac0g-b1` → `AC0G-B1`.
  - `test_station_env_site`: `SIGMOND_RAC_SITE=AC0G-X` → `AC0G-X`.
  - `test_station_no_call_prints_nothing`: no callsign, no env → empty output.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_rac_station_mode.py -q` — expect failures.
- [ ] **Step 3: Implement.**  Add the guard line `[ "${SIGMOND_WIZARD_AS_LIB:-0}" = 1 ] && return 0` immediately before the dispatch `case`.  Add `RAC_MODE`.  Reorder `rac_site_name`: `rac-site` file, then (host) `$REPORTER` then `$CONF_MARK`, or (station) `SIGMOND_RAC_SITE` then `rac_station_site_default`.  `rac_station_site_default` reads the callsign with a plain `sed`/`awk` match on `callsign = "..."` inside `[station]` (no Python dependency).  Keep column-0 `name(){` / `}` so the line-scan extractor still works.
- [ ] **Step 4: Run** the new tests plus `tests/test_wizard_remote_default.py tests/test_wizard_rac_register.py` — expect PASS.  If `test_wizard_remote_default.py` pinned the old order, update only that assertion and say so in the commit.
- [ ] **Step 5: Commit** `rac: wizard library mode, RAC_MODE, and a sticky machine name`.

### Task 2: Station mode in registration and the frpc config

**Files:**
- Modify: `scripts/proxmox/sigmond-wizard.sh` — `rac_payload_ready` (W:395-401), `rac_ladder` DIRECT probe proxies (W:477-483) and registrar validation (W:545-548), `rac_write_config` proxies (W:~640-687), `rac_register_locked` relay precondition (W:742-745)
- Test: `tests/test_rac_station_mode.py`

**Interfaces:**
- Consumes: `RAC_MODE` (Task 1).
- Produces: in station mode, `frpc-host-<gw>.toml` with exactly four proxies; `REG` with `P_HSSH=0 P_HUI=0` when the registrar omits them.  Payload lookup order: `$SIGMOND_RAC_PAYLOAD`, `/root/sigmond-appliance/sigmond-rac`, `/opt/git/sigmond/sigmond-rac` (each a directory holding `install-host.sh`).

- [ ] **Step 1: Write the failing tests** (reuse the registrar stub pattern from `tests/test_wizard_rac_register.py`):
  - `test_station_toml_four_proxies`: station mode, registrar replies `rac=17`, `vm_ssh=35817`, `vm_web=45817` and no host ports → one TOML per gateway with proxy names `SITE-vm-ssh`, `-vm-web`, `-vm-station`, `-vm-gmag`; localPorts 22, 8081, 8000, 8082; remotePorts 35817, 45817, 48817, 49817; no `host-ssh`, no `host-ui`.
  - `test_host_toml_unchanged`: host mode with the same stub plus host ports → six proxies, localPorts 12222, 12223, 12224, 12225, 22, 8006 (pins today's output).
  - `test_station_skips_relay_precondition`: station mode with no `sigmond-vm-ssh-relay.socket` → registration proceeds; host mode without it → `RAC_FAIL` set as today.
  - `test_station_direct_probe_four_proxies`: the DIRECT claim config in station mode carries four proxies and accepts when all four run.
  - `test_payload_found_in_opt_checkout`: frpc absent, `SIGMOND_RAC_PAYLOAD` unset, `/opt/git/sigmond/sigmond-rac/install-host.sh` stub present (via a test-only root prefix env, `SIGMOND_RAC_PAYLOAD_DIRS`, colon-separated) → the stub runs.
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement** the branches.  In `rac_write_config` keep the host heredoc untouched and add a separate station heredoc (`<<STNTOMLEOF`), since `test_wizard_rac_register.py` counts `<<TOMLEOF` occurrences; keep `rac_tls_block` nested and indented.
- [ ] **Step 4: Run** the new tests and every `tests/test_wizard_rac_*.py tests/test_rac_dual_gateway.py` — PASS.
- [ ] **Step 5: Commit** `rac: station mode registers four channels with no relays`.

### Task 3: Station mode in the switch and its units

**Files:**
- Modify: `scripts/proxmox/sigmond-wizard.sh` — `rac_install_host_files` (W:928-1484) and the switch heredoc (W:934-1416)
- Test: `tests/test_rac_switch.py` (add cases using its `Host` stub), `tests/test_rac_station_mode.py`

**Interfaces:**
- Consumes: `RAC_MODE`, `MARK_DIR`.
- Produces: `rac_install_host_files` writes `$MARK_DIR/rac-mode` (`host` or `station`).  The installed switch reads it (absent → `host`).  In station mode the switch: enables and disables no relay sockets; registers with `/usr/local/sbin/sigmond-rac register` instead of `$SETUP --rac-register`; prints "on this machine" where host mode prints "on the Proxmox host" / "in the decoder VM"; skips `repaint_panel` when `sigmond-issue.service` does not exist.  The retry unit's `ExecStart` reads `/usr/local/sbin/sigmond-rac retry` in station mode.  The three pair units are written only in host mode.  First output lines and exit codes stay exactly as today in both modes.

- [ ] **Step 1: Write the failing tests:**
  - `test_switch_station_on_enables_no_sockets` and `..._off_disables_no_sockets`: with `rac-mode` = `station`, the recorded `systemctl` calls name no `sigmond-vm-*-relay.socket`.
  - `test_switch_station_registers_via_sigmond_rac`: wish on, not registered → the stubbed `/usr/local/sbin/sigmond-rac register` runs, `sigmond-setup` does not.
  - `test_switch_station_status_first_line`: first line is one of the three `STATE_LINES` in `lib/sigmond/commands/remote.py`.
  - `test_switch_absent_mode_is_host`: no `rac-mode` file → today's socket calls and `$SETUP --rac-register`.
  - `test_install_station_units`: station mode → retry service `ExecStart=/usr/local/sbin/sigmond-rac retry`, no `sigmond-rac-pair.*` files, `rac-mode` holds `station`.
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement.**  Keep the switch's last line `exit 0` (`rac_switch_settle` checks it) and the `RACSWEOF` delimiter (pm-align extracts it).
- [ ] **Step 4: Run** `tests/test_rac_switch.py tests/test_pm_align.py tests/test_console_menu.py tests/test_rac_station_mode.py` — PASS.
- [ ] **Step 5: Commit** `rac: the switch and retry unit learn station mode`.

### Task 4: The station entry point, `sigmond-rac`

**Files:**
- Create: `scripts/rac/sigmond-rac` (bash, executable)
- Test: `tests/test_sigmond_rac_entry.py` (new)

**Interfaces:**
- Consumes: library mode and every function above.
- Produces: `sigmond-rac register|retry|activate [--site NAME]`, run as root.  It sets `RAC_MODE=station`, finds the wizard at `$SIGMOND_WIZARD` or `<dir of this script>/../proxmox/sigmond-wizard.sh` (resolving symlinks), defines its own `LOG=/var/log/sigmond-rac.log` and `say()` before sourcing, and sources with `SIGMOND_WIZARD_AS_LIB=1`.
  - `register` → `rac_register fresh`; exit 0 on success, 1 with `RAC_FAIL` printed otherwise.
  - `retry` → `rac_retry`.
  - `activate` → refuse and exit 5 when `/etc/sigmond-appliance/version` or `/etc/sigmond/rac-switch.key` exists ("a Proxmox host carries this machine's RAC"); refuse and exit 4 when `rac_site_name` prints nothing ("set [station] callsign in /etc/sigmond/site-profile.toml or pass --site"); otherwise write `rac-site` (from `--site` when given), link `/usr/local/sbin/sigmond-rac` to this script, run `rac_install_host_files`, record wish `on` with `rac-changed` `on <UTC> install` only when no wish exists (spec: carry the operator's wish), then run the switch's `on` (or `status` when the wish reads `off`) with `SIGMOND_RAC_BY=install`, print its output, and exit with its code.

- [ ] **Step 1: Write the failing tests** (bash with a tmp root, stubbed `systemctl`, `frpc`, registrar):
  - `test_activate_refuses_on_appliance_vm` (exit 5, nothing written).
  - `test_activate_refuses_without_site` (exit 4, message names both remedies).
  - `test_activate_records_site_and_wish` (`rac-site` = `AC0G-SIGMA`, `rac-desired` = `on`, `rac-mode` = `station`).
  - `test_activate_keeps_operator_off` (existing wish `off` → switch `status`, wish still `off`).
  - `test_register_station_end_to_end` (stub registrar → four-proxy TOML written).
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS, plus the whole RAC test set from Tasks 1-3.
- [ ] **Step 5: Commit** `rac: sigmond-rac, the bare-metal entry point`.

### Task 5: `smd remote` on a station

**Files:**
- Modify: `lib/sigmond/commands/remote.py` (`cmd_remote` :236, `_not_paired` :151-171, `cmd_motd` :~185)
- Test: `tests/test_remote_cli.py`

**Interfaces:**
- Produces: `local_switch() -> Optional[str]`: the switch path when no pairing key exists, `/usr/local/sbin/sigmond-rac-switch` is executable and `/etc/sigmond-appliance/rac-mode` reads `station`; else `None`.  (Paths overridable through the module constants tests already patch.)  When it returns a path, `cmd_remote` runs `[path, word]` directly (already root after `_need_root`), with the same timeouts, the same first-line parse, the same cache file and the switch's exit code.  `cmd_motd` reads the cache the same way.

- [ ] **Step 1: Write the failing tests:** `test_station_local_switch_on`, `..._status_parses_down`, `test_paired_host_still_uses_ssh` (pairing present wins), `test_not_paired_no_switch_unchanged` (today's `_not_paired` text and exit 5).
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement.**  Replace the old hint "smd admin rac status" in `_not_paired` with `sigmond-rac activate`.
- [ ] **Step 4: Run** `tests/test_remote_cli.py` — PASS.
- [ ] **Step 5: Commit** `remote: smd remote drives the local switch on a bare-metal station`.

### Task 6: Bring-up activates the RAC

**Files:**
- Modify: `bin/smd` — `_bringup_finish` (bin/smd:10843-10882), after `_ok(f"bringup '{name}' complete")`
- Test: `tests/test_bringup_rac.py` (new)

**Interfaces:**
- Consumes: `scripts/rac/sigmond-rac activate` (Task 4).
- Produces: `_bringup_activate_rac(smd_root: Path) -> None`: skips silently when `/etc/sigmond-appliance/version` or `/etc/sigmond/rac-switch.key` exists; otherwise runs `<repo>/scripts/rac/sigmond-rac activate` with stdin from `/dev/null`, a 900 s timeout, output passed through; logs one `_info` line on a non-zero exit ("remote access not up yet: <reason>; the retry timer keeps trying; smd remote status") and never raises.

- [ ] **Step 1: Write the failing tests:** `test_finish_runs_activate_on_bare_metal`, `test_finish_skips_on_appliance_vm`, `test_finish_rc_unchanged_when_activate_fails` (activate exits 4 → bring-up still returns 0), `test_finish_rc_unchanged_on_timeout`, `test_partial_bringup_does_not_activate` (rc 3 path never calls it).
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement**, following how `_bringup_finish` calls its neighbours.
- [ ] **Step 4: Run** the new tests and `tests/test_smd_bringup*.py` — PASS.  Then the full suite: `.venv/bin/pytest -q` — no new failures.
- [ ] **Step 5: Commit** `bringup: a complete bare-metal run brings up remote access`.

### Task 7: Docs

**Files:**
- Modify: `README.md` (the bare-metal install section: what bring-up now does for remote access, `smd remote on|off|status`, `sigmond-rac activate --site` for a machine that runs no bring-up, such as a radiod-only host)
- Modify: `docs/superpowers/specs/2026-11-05-one-rac-design.md` (Status line; §4 "Where the code lives": record Ruling 1 and that the move waits on pm-align)

- [ ] **Step 1:** Write both, in plain short sentences.  `tests/test_docs_remote_access.py` must still pass.
- [ ] **Step 2: Commit** `docs: remote access on a bare-metal station`.

### Task 8: Proof on sigma, B2 and B1 (controller, with Michael)

Not a subagent task: it runs as root on Michael's machines, which this session's auto mode blocks.  The controller stages one script per machine in the devbox scratchpad and gives Michael the `!` commands.

- [ ] The work lives on branch `rac-bare-metal`, never main, until this task passes (Michael,
      2026-11-05: test on bare metal before anything reaches a Proxmox host).  Push the branch
      only (Michael's say-so), then on each machine, as the checkout's owner, fetch it and check
      it out in `/opt/git/sigmond/sigmond`.  Field Proxmox hosts change only through `pm-align
      --apply` with a chosen commit, so a branch reaches none of them.
- [ ] sigma: `sigmond-rac activate` (callsign from its site profile) → expect `AC0G-SIGMA`.
- [ ] B2 (`AC0G-B2`): `sigmond-rac activate` → expect `AC0G-B2`.
- [ ] B1 (`ac0g-b1`, radiod only): `sigmond-rac activate --site AC0G-B1` if its site profile lacks a callsign.
- [ ] For each: `smd remote status` reads `Remote access: on`; the RAC page lists the machine; from the devbox, ssh to `gw2.wsprdaemon.org -p 35800+n` reaches the machine's login.
- [ ] After the three pass and Michael approves the merge to main: one Proxmox host (B4-PM, after announcing on the bus): `sigmond-rac-switch status` unchanged, RAC number unchanged.  Only after Rob's answer to spec §8 Q2 if the registrar refuses a station.
- [ ] Record the RAC numbers in the bus post and in `ops/fleet.toml` (add sigma).

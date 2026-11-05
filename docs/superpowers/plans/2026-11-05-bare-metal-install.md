# Bare-Metal Repo Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A ham who follows the README on a machine they already own ends with a working station, whether it hosts radiod (sigma) or only decodes for radiods elsewhere on the LAN (AC0G-B2).

**Architecture:** Fixes land where each decision already lives: core placement in `lib/sigmond/cpu.py`; the bring-up plan in `lib/sigmond/bringup.py` and its executor and probes in `bin/smd`; radiod discovery and binding in `lib/sigmond/sources.py`, `lib/sigmond/coordination.py` and one new `smd admin sources bind` verb; the outage behaviour in the wspr-recorder, psk-recorder and meteor-scatter repos. One signal separates the two install kinds: `/etc/sigmond-appliance/version` exists only on an image install.

**Tech Stack:** Python 3.11+, bash, systemd, avahi (`avahi-browse`), pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-11-04-bare-metal-install-design.md`

## Global Constraints

- The image path keeps its behaviour.  Anything that differs for a repo install keys on `Path('/etc/sigmond-appliance/version').exists()` (see `provenance.image_lineage()`, lib/sigmond/provenance.py:37), through one helper, `is_appliance_install() -> bool`, added to lib/sigmond/provenance.py in Task 2.
- Never run `smd` under sudo; it elevates itself.
- Tests: `cd /home/mjh/hamsci/repos/sigmond && .venv/bin/python -m pytest -q <files>`.  The full suite has one failure that predates this work: `tests/test_rac_switch.py::test_the_switch_installs_from_the_wizard_as_an_ascii_script`.  Leave it alone.
- Each new test fails without its fix: run it once against the unfixed code and watch it fail.
- Commit on `main` (no branch) with the trailers `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z`.  Push only when Michael says so; tell Rob on the claude-bus about every pushed change.
- Comments and docs follow the house manner: name the station, the date and the symptom that motivated a rule.
- Discovery keeps a radiod only when its advertised name begins with the station's callsign (case-insensitive, first whitespace-separated token).  `--remote-radiod` names radiods outright and overrides that rule.
- Reporter ID for a discovered radiod: the callsign, `/`, then the status name with `-status.local` and a leading `<callsign>-` removed, non-alphanumerics dropped, upper-cased.  `ac0g-b1-a-status.local` with callsign AC0G gives `AC0G/B1A`.
- A repo install's first bring-up holds uploads with the reason `"first install from the repos: held until checks pass"`.

## Review Focus

1. A radiod advertising another station's call, a call in lower case, or a name with no call at all: listed, never kept unless named.  Test in Task 6.
2. A `--remote-radiod` name that does not answer: bring-up stops before installing anything, naming it.  Test in Task 7.
3. Two kept radiods whose derived reporter IDs collide: bring-up stops and names both rather than merging them.  Test in Task 6.
4. Re-running bring-up on a host already bound: no second `[radiod."…"]` block, no second `[[clients.X]]` entry, no `InstanceExists` failure.  Test in Task 6.
5. An image install: GRUB still written and uploads still on, exactly as before.  Tests in Tasks 2 and 9.

---

### Task 1: Performance cores only (spec 4.1)

The code and tests already sit uncommitted in the working tree: `get_performance_cpus(root='/sys/devices') -> set` and a first filter in `eligible_radiod_cores(..., performance_cpus=None)`, threaded through `assign_radiod_cores` and `radiod_core_shortfall`, plus `HybridCpuPlacementTests` (6 tests) in tests/test_cpu.py.

**Files:** Modify `lib/sigmond/cpu.py`; Test `tests/test_cpu.py`.

**Interfaces:** Produces `get_performance_cpus(root: str = '/sys/devices') -> set` (empty set = no hybrid split).

- [ ] **Step 1:** Review the diff (`git diff lib/sigmond/cpu.py tests/test_cpu.py`) against spec 4.1.
- [ ] **Step 2:** Run `tests/test_cpu.py tests/test_radiod_core_placement.py`; expect all pass.
- [ ] **Step 3:** Commit: `cpu: radiod on performance cores only, on a hybrid part`.

### Task 2: Kernel isolation only where sigmond owns the machine (spec 4.1, F16)

**Files:** Modify `lib/sigmond/provenance.py`, `bin/smd` (`_ensure_guest_cpu_isolation`, bin/smd:~13243); Test `tests/test_guest_isolation_repo_install.py` (new).

**Interfaces:**
- Produces `is_appliance_install(root: str = '/') -> bool` in lib/sigmond/provenance.py.
- Topology opt-in: `[cpu_affinity] write_kernel_isolation = true` makes a repo install write the GRUB drop-in as the image does.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_repo_install_prints_isolation_and_leaves_grub_alone(tmp_path, capsys): ...
    # is_appliance_install() False, no opt-in: GUEST_ISOLATION_PATH not written,
    # update-grub not run, stdout names the isolcpus/nohz_full values and the opt-in key.
def test_an_image_install_still_writes_the_drop_in(tmp_path): ...
def test_the_topology_opt_in_writes_on_a_repo_install(tmp_path): ...
def test_is_appliance_install_reads_the_image_version_file(tmp_path): ...
```

Patch `GUEST_ISOLATION_PATH`, `is_hypervisor_host`, `recommended_isolcpus` and the `update-grub` runner; assert on writes and calls.

- [ ] **Step 2:** Run them; expect the first and third to fail.
- [ ] **Step 3:** Implement: `_ensure_guest_cpu_isolation` checks `is_appliance_install()` or the opt-in before writing; otherwise it prints the drop-in it would write and returns False.  If the drop-in already exists from an earlier run (sigma has `isolcpus=15`), a repo install without the opt-in removes it and runs `update-grub`, saying so.
- [ ] **Step 4:** Run; expect pass.
- [ ] **Step 5:** Commit: `apply: a repo install suggests kernel isolation, the image writes it`.

### Task 3: FFT wisdom, through the existing bundle-and-top-up mechanism (spec 4.2, F6)

The mechanism stays as it is:
- `identify_cpu()`/`find_bundle()` match `/proc/cpuinfo`'s model against `wisdom/wisdomf-<slug>.cpu`.
- `_seed_bundled_wisdom` seeds a matching bundle.
- `smd admin wisdom plan` runs `fftwf-wisdom` over `profiles_for_planning()`, the static list plus `fft.log` misses.  It checks and skips transforms already planned.

Only the two gates in front of it change.  Today the unit's `ConditionPathExists=!/etc/fftw/wisdomf` and bring-up's `wisdom_file.exists()` both ask "is there a file?".  sigma's June file, planned for other sizes, therefore kept the planner from ever running.

**Files:**
- Modify `lib/sigmond/wisdom.py`.
- Modify `bin/smd`: `cmd_wisdom_plan` :17947, `_seed_bundled_wisdom`, `_bringup_wait_wisdom` :9565, apply's `_WISDOM_FILE.exists()` :12335, the wisdom parser ~:28750.
- Modify `systemd/sigmond-wisdom.service`.
- Test: `tests/test_wisdom_current.py` (new).

**Interfaces:**
- Consumes Task 1's `assign_radiod_cores` and `get_performance_cpus`, and Task 2's isolation outcome.  So Task 3 runs after both, and after the per-controller IRQ layout (ef2998d).
- `WISDOM_SIDECAR = Path('/etc/fftw/wisdomf.cpu')`, in the bundles' v2 sidecar format (`model=` plus `planned_profiles=` with a comma list).
  - Written by `cmd_wisdom_plan` after `install_wisdom`, with `model=cpu_model()`.
  - Written by `_seed_bundled_wisdom`, copied from the bundle's sidecar.
- `wisdom_is_current(wisdom_file=WISDOM_FILE, sidecar=WISDOM_SIDECAR, miss_log=FFT_MISS_LOG, cpuinfo=CPUINFO) -> bool` in lib/sigmond/wisdom.py.  True only when all three hold:
  - the file exists;
  - `read_sidecar(sidecar)['model'] == cpu_model(cpuinfo)`;
  - every entry of `plans_from_fft_log(miss_log)` appears in the sidecar's `planned_profiles`.
- `smd admin wisdom needed`: exit 0 when `not wisdom_is_current()`, 1 otherwise.  It prints `identify_cpu()`'s verdict either way; a miss stays loud.
- `cmd_wisdom_plan` sets the conditions radiod will run under before timing anything, because FFTW chooses plans by timing them:
  1. Run `/usr/local/sbin/sigmond-rx888-irq-affinity`.  With radiod stopped, it reads the fence from radiod's drop-in, so the xhci interrupts leave radiod's cores (ef2998d places them per controller).
  2. Refuse to plan, with the reason, if any `xhci_hcd` IRQ's `smp_affinity_list` still overlaps radiod's cores.
  3. Pin itself with `os.sched_setaffinity(0, cpus)` to the union of `assign_radiod_cores(...)` for the local radiod units.
  4. Log the CPUs and the IRQ layout it timed under.
- The sidecar also records the conditions, and `wisdom_is_current()` requires them to match the present placement:
  - `radiod_core_type`: `performance` on a hybrid part (`get_performance_cpus()` non-empty), else `uniform`;
  - `radiod_l3_bytes`: the size of the L3 domain radiod's cores sit in.

  Moving radiod between two like cores keeps the wisdom.  Moving it to another core type or a smaller L3 slice forces a replan.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_file_with_no_sidecar_is_not_current(tmp_path): ...        # sigma's June wisdomf
def test_wisdom_planned_on_another_cpu_is_not_current(tmp_path): ...  # sidecar model differs
def test_a_logged_miss_not_yet_planned_means_replan(tmp_path): ...    # fft.log 'cob1234' not in planned_profiles
def test_matching_cpu_and_no_new_misses_is_current(tmp_path): ...
def test_seeding_a_bundle_carries_its_sidecar(tmp_path): ...          # then wisdom_is_current() True
def test_a_v2_sidecar_bundle_is_seeded(tmp_path, monkeypatch):
    # ⛔ _seed_bundled_wisdom compared the sidecar's RAW TEXT with the model, but
    # both shipped sidecars are key=value (v2), so no bundle was ever seeded on
    # a repo install.  cpuinfo 'AMD Ryzen 7 5825U with Radeon Graphics' + the
    # shipped wisdom/ dir -> /etc/fftw/wisdomf (tmp) equals wisdomf-amd-ryzen-7-5825u
def test_the_planner_runs_on_radiods_cores(monkeypatch):
    # assign_radiod_cores -> {'radiod@sigma.service': {6, 7}}; sched_setaffinity called with {6, 7}
def test_the_planner_lays_out_irqs_before_timing(monkeypatch):
    # the IRQ script runs before the planner subprocess; call order asserted
def test_the_planner_refuses_while_an_xhci_irq_sits_on_radiods_cores(tmp_path, monkeypatch):
    # fake /proc/interrupts + smp_affinity_list '6-7' with radiod on {6,7} -> no plan, non-zero exit, reason printed
def test_wisdom_from_another_core_type_or_l3_slice_is_not_current(tmp_path): ...
def test_moving_between_like_cores_keeps_the_wisdom(tmp_path): ...
```

- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.
  - The unit: `ExecCondition=/usr/local/bin/smd admin wisdom needed` replaces `ConditionPathExists=!/etc/fftw/wisdomf`; keep `ConditionFileIsExecutable`.
  - `_bringup_wait_wisdom` and apply's radiod start gate call `wisdom_is_current()` instead of `exists()`.
  - `_seed_bundled_wisdom` finds its bundle with `wisdom.find_bundle(_WISDOM_BUNDLE_DIRS, cpu_model())` instead of its own raw-text comparison, which no v2 sidecar can match.
  - Seeding still happens only when no wisdom file exists; an existing file gets topped up by the planner, never overwritten by a bundle.
  - The wait still only warns at its timeout.
- [ ] **Step 4:** Run; expect pass, then `tests/test_bringup.py`.
- [ ] **Step 5:** Commit: `wisdom: plan when the wisdom wasn't made on this CPU or misses a size radiod asked for`.

### Task 4: Host tuning on every host, uploader set up only once it exists (spec 4.3, F7, F12)

**Files:** Modify `bin/smd` (`_apply_uploader_manifest` :12157), `lib/sigmond/bringup.py` (remote branch of Stage 1, ~:333); Test `tests/test_bringup.py`, `tests/test_apply_uploader_absent.py` (new).

**Interfaces:** Consumes `uploader_manifest.HS_UPLOADER_PYTHON` (`/opt/hs-uploader/venv/bin/python`) as the "installed" test.

- [ ] **Step 1: Write the failing tests**

```python
def test_apply_skips_the_uploader_step_until_hs_uploader_exists(monkeypatch, capsys):
    # HS_UPLOADER_PYTHON absent: cmd_uploader_manifest never called, returns True,
    # prints that bring-up's stage 4 sets it up.
def test_a_client_plan_tunes_the_host_before_any_client_starts():
    p = build_plan(_client(), local_radiod=False, remote_radiods=[...])
    # an 'apply host tuning' step precedes the first install/config of a radiod-bound client
```

(`remote_radiods` arrives in Task 7; until then pass `remote_status_dns`.)
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.  The client plan's Stage 1 gets the same `smd apply` tune step the local branch has, marked `may_fail=False`.
- [ ] **Step 4:** Run `tests/test_bringup.py tests/test_apply_uploader_absent.py`; expect pass.
- [ ] **Step 5:** Commit: `bringup: host tuning on client hosts; apply waits for hs-uploader`.

### Task 5: "Configured" means the client says so (spec 4.3, F8)

**Files:** Modify `bin/smd` (`_bringup_probe` :9777, `_config_is_placeholder` :9766); Test `tests/test_bringup_configured_probe.py` (new).

**Interfaces:** Each client's `validate --json` returns `{"ok": bool, "config_path": str, "issues": [{"severity": "fail"|"warn", ...}]}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_mag_recorders_fresh_template_is_unconfigured(tmp_path, monkeypatch):
    # config present with <YOUR_CALL>; validate --json -> ok False, a 'fail' issue -> (False, ...)
def test_a_client_whose_validate_passes_is_configured(...): ...
def test_without_a_validate_cli_any_angle_bracket_token_counts_as_template(tmp_path):
    # '<YOUR_PSWS_STATION_ID>' and '<configure-via-config-init>' both -> placeholder
```

- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement: when the config exists, run `<exe> validate --json` (timeout 30 s); a reply with `"ok": false` and any `fail` issue means unconfigured.  No usable reply falls back to `_config_is_placeholder`, widened to any quoted value matching `^<[A-Z][A-Z0-9_-]*>$` as well as the sentinel.
- [ ] **Step 4:** Run; expect pass, then run `tests/test_config_placeholder_conventions.py tests/test_bringup.py`.
- [ ] **Step 5:** Commit: `bringup: ask each client whether it's configured`.

### Task 6: Discovery, selection and naming (spec 4.4)

**Files:** Modify `lib/sigmond/sources.py`, `lib/sigmond/instance.py` (`_sources_stub` :592); Test `tests/test_sources_discovery.py` (new).

**Interfaces — produces:**

```python
@dataclass(frozen=True)
class DiscoveredRadiod:
    status_dns: str      # "ac0g-b1-a-status.local" (from the InventoryRow key)
    label: str           # advertised name, "AC0G @EM38ww B1-a longwire"
    host: str            # source_host, else the mDNS hostname

def discover_radiods(timeout: float = 5.0) -> list[DiscoveredRadiod]
def advertised_call(label: str) -> str                    # first token, upper-cased; "" if none
def reporter_id_for(callsign: str, status_dns: str) -> str
def select_radiods(found: list[DiscoveredRadiod], callsign: str,
                   named: list[str]) -> list[DiscoveredRadiod]
    # named non-empty: exactly those; a name not in `found` raises SourceSelectionError(name)
    # named empty: those whose advertised_call == callsign.upper()
    # derived reporter IDs that collide raise SourceSelectionError naming both
class SourceSelectionError(ValueError)
```

`discover_radiods` wraps the existing `mdns.probe` + `sources.inventory` path that `_sources_inventory()` (bin/smd:18298) uses.

- [ ] **Step 1: Write the failing tests**

```python
def test_reporter_id_from_the_status_name():
    assert reporter_id_for('AC0G', 'ac0g-b1-a-status.local') == 'AC0G/B1A'
    assert reporter_id_for('AC0G', 'sigma-status.local') == 'AC0G/SIGMA'
def test_own_callsign_radiods_are_kept_others_listed_only():
    found = [R('ac0g-b1-a-status.local', 'AC0G @EM38ww B1-a longwire'),
             R('ac0g-b1-b-status.local', 'ac0g @EM38ww B1-b T3FD'),
             R('k3lr-status.local', 'K3LR @EN91 main'),
             R('x-status.local', 'with airspyhf')]
    assert [r.status_dns for r in select_radiods(found, 'AC0G', [])] == \
        ['ac0g-b1-a-status.local', 'ac0g-b1-b-status.local']
def test_a_named_radiod_overrides_the_callsign_rule(): ...
def test_a_named_radiod_that_did_not_answer_is_an_error(): ...
def test_colliding_reporter_ids_are_an_error():
    # 'ac0g-b1-a-status.local' and 'ac0g-b1a-status.local' -> both AC0G/B1A
def test_a_new_instances_source_file_uses_the_key_the_reader_reads(tmp_path):
    # create_instance(...) then ClientSources.load(...) round-trips; stub writes `selected = []`
```

- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement in sources.py; change the stub's key to `selected`.
- [ ] **Step 4:** Run; expect pass.
- [ ] **Step 5:** Commit: `sources: discover radiods, keep the station's own, name instances after them`.

### Task 7: Bind a radiod and its instances; bring-up uses discovery (spec 4.4, F10, F11)

**Files:** Modify `lib/sigmond/coordination.py`, `bin/smd` (sources parser and `cmd_sources_apply` :18737, `cmd_bringup` :9939-10160, the bringup parser :26824), `lib/sigmond/bringup.py` (`build_plan`); Test `tests/test_sources_bind.py` (new), `tests/test_bringup.py`.

**Interfaces:**
- Produces in coordination.py: `ensure_radiod_block(path: Path, status_dns: str, host: str) -> bool` and `ensure_client_binding(path: Path, client: str, instance: str, radiod_id: str) -> bool`.  Each returns True when it wrote and is a no-op on a second call.  `radiod_id` is the status name; `instance` is the reporter ID's storage form (`AC0G=B1A`), or `""` for hf-timestd.
- Produces `smd admin sources bind <status_dns> --reporter <ID> [--host H] [--timing]`.  It runs `ensure_radiod_block`, and for each client in `_REPORTER_KEYED` that the topology enables: `create_instance(..., force=True)`, `ClientSources(...).save()` with `radiod:<status_dns>`, `ensure_client_binding`, then the per-instance render that `cmd_sources_apply` already does.  `--timing` also writes `ensure_client_binding(path, 'hf-timestd', '', status_dns)`.  It ends with `smd config render` so coordination.env carries the result.
- `build_plan(..., remote_radiods: list[tuple[str, str]] = ())` replaces `remote_status_dns`: each pair is `(status_dns, reporter_id)`.  `timing_radiod: str = ''` is new.  The `remote_status_dns` parameter goes.
- `cmd_bringup`:
  - `--remote-radiod` becomes `action='append'`, and `--timing-radiod NAME` is new.
  - On a client profile it runs `discover_radiods()` then `select_radiods()`, prints the list (kept and not), and stops on `SourceSelectionError` before planning.
  - Stage 1 gets one `config` step per kept radiod, `smd admin sources bind <dns> --reporter <id> [--timing]`, after the tune step.
  - Stage 3a/4 instance steps use each pair's reporter ID instead of the single `--reporter`.
  - With one kept radiod, `--timing` goes to it; with several, only to `--timing-radiod`.  If none is named, hf-timestd's configure and start steps are replaced by a `note`: "hf-timestd installed but idle: name its radiod with --timing-radiod".

- [ ] **Step 1: Write the failing tests**

```python
def test_bind_writes_radiod_block_and_bindings_once(tmp_path): ...   # second call: no duplicates
def test_bind_renders_each_instance_config_to_its_radiod(tmp_path):
    # /etc/psk-recorder/AC0G=B1B.toml [[radiod]] status == 'ac0g-b1-b-status.local'
    # /etc/wspr-recorder/AC0G=B1B.toml has [[source]] status == 'ac0g-b1-b-status.local'
def test_one_kept_radiod_gets_hf_timestd(): ...
def test_two_kept_radiods_leave_hf_timestd_idle_unless_named(): ...
def test_client_plan_binds_every_radiod_before_any_client_is_configured(): ...
def test_client_plan_enables_one_instance_pair_per_radiod():
    # remote_radiods=[('ac0g-b1-a-status.local','AC0G/B1A'),('ac0g-b1-b-status.local','AC0G/B1B')]
    # -> enable steps for wspr-recorder and psk-recorder at both reporter IDs
def test_bringup_stops_before_installing_when_a_named_radiod_is_silent(monkeypatch): ...
def test_a_local_radiod_plan_is_unchanged():
    # dasi2 with local_radiod=True: no bind steps, no discovery, same step list as before
```

- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.  Fix the non-f-string `{client}` in `cmd_sources_apply`'s "not yet multi-source aware" message while there.
- [ ] **Step 4:** Run `tests/test_sources_bind.py tests/test_bringup.py tests/test_bringup_no_sdr.py tests/test_install_no_pull.py`; expect pass.
- [ ] **Step 5:** README: a "Decode-only host" subsection under step 2, covering discovery, `--remote-radiod`, `--timing-radiod`, renaming an instance, and the bind verb.
- [ ] **Step 6:** Commit: `bringup: a client host discovers its radiods and binds an instance pair to each`.

### Task 8: Clients wait out an outage (spec 4.5, F13) — wspr-recorder, psk-recorder, meteor-scatter repos

**Files:**
- wspr-recorder: `wspr_recorder/__main__.py` (startup at ~:2038-2060).
- psk-recorder: `src/psk_recorder/core/recorder.py` (:440-460).
- meteor-scatter: its receiver-manager startup (`core/receiver_manager.py:209`); include it only if it exits the same way.
- sigmond: `docs/CLIENT-CONTRACT.md`.
- Tests in each repo's suite.

**Interfaces:**
- On startup, a source whose radiod does not resolve or connect is retried with delays 5, 10, 20, 40, then 60 s, without end.
- Before waiting, the client sends `READY=1` and `STATUS=waiting for radiod <status>`, so systemd's `TimeoutStartSec=180` and `StartLimitBurst=10` never fire.  When the source connects it sends `STATUS=running`.
- It logs one WARNING when an episode starts and one INFO when it ends, and nothing per try.
- The placeholder case keeps its exit 78.

- [ ] **Step 1: Write the failing tests** (each repo): `test_startup_waits_for_an_unresolvable_radiod_instead_of_exiting` (a fake `RadiodControl` failing three times then succeeding; the run reaches recording; the delays are 5, 10, 20), `test_waiting_logs_once_per_episode`, `test_notify_ready_precedes_the_wait`.
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run each repo's suite.
- [ ] **Step 5:** CLIENT-CONTRACT: a rule that a radiod-bound client must not exit when its radiod is absent at start, with the B2 history.  Bump nothing else.
- [ ] **Step 6:** Commit in each repo: `startup: wait for radiod instead of exiting`.

### Task 9: Uploads held after a repo install (spec 6.3) and the small items (F15)

**Files:** Modify `lib/sigmond/bringup.py` (Stage 4, the manifest step ~:465), `bin/smd` (`cmd_bringup` end), `lib/sigmond/harmonize.py` (`rule_ka9q_python_compat` :708); Test `tests/test_bringup.py`, `tests/test_harmonize.py` (or the file holding ka9q_python_compat tests).

**Interfaces:**
- `build_plan(..., hold_uploads: bool = False)`.  When True, Stage 4 puts `smd config uploads disable --reason "first install from the repos: held until checks pass"` before the manifest step.
- `cmd_bringup` sets `hold_uploads = not is_appliance_install() and coordination has no [uploads] block`.
- At the end of a COMPLETE run with `hold_uploads`: on a TTY it asks "Send uploads now? [y/N]" and runs `smd sink upload` on yes.  Otherwise it prints that command.

- [ ] **Step 1: Write the failing tests**: `test_a_repo_install_holds_uploads_before_the_manifest`, `test_an_image_install_does_not_hold_uploads`, `test_an_existing_uploads_block_is_respected`, `test_ka9q_python_compat_is_silent_when_ka9q_radio_is_not_enabled`.
- [ ] **Step 2:** Run; expect failures.
- [ ] **Step 3:** Implement.  `rule_ka9q_python_compat` returns pass/"skipped (this host builds no ka9q-radio)" when the topology does not enable `ka9q-radio`.
- [ ] **Step 4:** Run; expect pass.
- [ ] **Step 5:** Commit: `bringup: a first repo install holds uploads until checks pass`.

### Task 10: Prove it on the machines (spec 5)

No code.  Needs pushed commits; ask Michael before pushing, then tell Rob on the bus.  Move each install aside first, using `scratchpad/sigma-teardown2.sh` with `/etc/fftw` added to its pattern.  In the run script, `git` must be `git --no-pager`.

- [ ] **Step 1: sigma**
  - README step 1, then `smd bringup dasi2 --callsign AC0G --grid EM38ww --reporter AC0G/S --psws-station-id S000418 --non-interactive`.
  - Set `[psws.instruments]` to hf-timestd 367 and mag-recorder 401, then `smd config render`.
  - Expect:
    - COMPLETE;
    - radiod's CPUAffinity {6,7} and IRQs on {0,1};
    - wisdom planned on {6,7};
    - no `STEPPED` lines in radiod's journal over one hour;
    - WSPR, FT8, meteor scatter, hf-timestd and mag-recorder recording;
    - uploads held, with the end-of-run prompt shown;
    - GRUB untouched, and the old `isolcpus=15` drop-in removed.
- [ ] **Step 2: B2**
  - README step 1, then `smd bringup client --callsign AC0G --grid EM38ww --timing-radiod ac0g-b1-a-status.local --non-interactive`.
  - Expect:
    - COMPLETE;
    - the list shows sigma's and B4's radiods as not kept;
    - four recorders decoding as AC0G/B1A and AC0G/B1B;
    - hf-timestd on B1-a;
    - `rmem_max` 64 MiB;
    - uploads held.
- [ ] **Step 3: outage on B2.**  Stop `radiod@ac0g-b1-b` on B1 for five minutes, then start it.  Expect B2's B1B recorders still `active` throughout, one WARNING and one INFO each, and decoding again within two cycles.
- [ ] **Step 4: B4.**  Announce on the claude-bus first.  Deploy with `smd update`, then watch a day: no change in radiod placement (B4 is AMD), no GRUB change (image install), no upload change.
- [ ] **Step 5:** Report to Phil the libusb assertion in `rx888_close` on shutdown (F9), with sigma's backtrace.  Not code; Michael sends it.

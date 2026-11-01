# Multi-card hardening (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Sigmond host with more than one RX888 cannot swap antennas: every radiod is pinned to a confirmed serial, the tools that act on "the card" act on the right one, and a second radiod's ka9q-web gets a port nobody else owns.

**Architecture:** Six small changes on existing code paths, plus one new library module (`sigmond.sdr_map`, the serial-to-radiod map) that `smd admin sdr list|identify` and the radiod watchdog share. Nothing here needs two cards to test: pure functions take fake sysfs trees and fake coordination; the wizard and the watchdog are driven through recorded inventories. First real two-card run is KFS (design §10).

**Tech Stack:** Python 3.11 stdlib (tomllib, dataclasses, pathlib), argparse in `bin/smd`, pytest + unittest as the existing tests do. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-30-multi-receiver-station-design.md` §5 (items 1 to 8), §9, §10 row 1, §11 builder's calls.

## Global Constraints

- One-card stations are unchanged: a sole SDR still renders `# serial = ...` commented out ("sole SDR: unbound by design"). Every task's tests keep a sole-SDR case passing.
- No ka9q-radio fork or patch. A serial that matches nothing still makes radiod exit; that is upstream behaviour and the tools below exist to keep it from being a surprise.
- Stdlib only. Tests run with `cd /home/rob/hamsci/repos/sigmond && .venv/bin/python -m pytest tests/<file> -q`.
- Root-only actions self-elevate through the existing `_need_root(...)` in `bin/smd`; pure logic lives in `lib/sigmond/` and is tested without root.
- Serials are compared upper-cased and stripped, the way `_format_serial` and wsprdaemon's `wd -X` already do.
- Commit messages follow the repo style: `area: one sentence in plain words` (see `git log`), ending with the attribution lines from the session's system reminder.
- `cli_reference.py` has a test that lists every `smd` verb; adding `smd admin sdr` means updating its table in the same task.

## Review Focus

1. A one-card host whose RX888 has no readable iSerial must still get an unpinned conf and a warning, never a refusal. Test in Task 1.
2. During `sdr identify`, two serials dropping at once (operator pulled a hub) must abort that step and re-prompt, never assign the first one. Test in Task 4.
3. A card in the FX3 bootloader (`04b4:00f3`) has no serial; `sdr list` must count it separately and never map it to an instance. Test in Task 3.
4. A legacy hold with neither `radiod_unit` nor `serial` must still cover every instance (blanket). Test in Task 5.
5. The first ka9q-web stays on 8081 whatever else is installed, because the RAC web relay depends on it. Test in Task 7.

---

### Task 1: `init radiod` refuses an unpinned conf on a multi-card host

**Files:**
- Modify: `lib/sigmond/commands/radiod_config.py:684-760` (`_collect_per_sdr_values`), `:435-590` (`cmd_radiod_init`)
- Test: `tests/test_radiod_config.py`

**Interfaces:**
- Consumes: `_collect_per_sdr_values(sdr, args, hostname_short, iface, *, known_ids, sole) -> Optional[dict]` (existing).
- Produces: `class UnpinnedMultiCard(Exception)` in `radiod_config.py`, carrying `.sdr_type`, `.bus`, `.device`. `_collect_per_sdr_values` raises it when `not sole and not serial`. `cmd_radiod_init` catches it and returns 1.

- [ ] **Step 1: Write the failing tests** in `tests/test_radiod_config.py`, class `MultiCardSerialTests`:

```python
def test_two_cards_one_without_serial_refuses(self):
    sdr = _sdr("RX-888", serial="")
    with self.assertRaises(radiod_config.UnpinnedMultiCard):
        radiod_config._collect_per_sdr_values(
            sdr, _ns(non_interactive=True), "host", "eth0",
            known_ids=set(), sole=False)

def test_sole_card_without_serial_still_renders_unpinned(self):
    plan = radiod_config._collect_per_sdr_values(
        _sdr("RX-888", serial=""), _ns(non_interactive=True), "host", "eth0",
        known_ids=set(), sole=True)
    self.assertTrue(plan["serial_line"].startswith("# serial"))

def test_init_exits_1_and_names_identify_when_refused(self):
    # mock _discover_sdrs -> two RX-888s, second with serial ""; mock
    # load_coordination -> empty; capture stdout
    rc = radiod_config.cmd_radiod_init(_ns(non_interactive=True, force=False))
    self.assertEqual(rc, 1)
    self.assertIn("smd admin sdr identify", captured_stdout)
    self.assertFalse(any(RADIOD_CONFIG_DIR.glob("radiod@*.conf")))  # nothing written
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_radiod_config.py -k MultiCardSerial -q`
Expected: FAIL, `AttributeError: ... UnpinnedMultiCard`

- [ ] **Step 3: Implement.** Add `UnpinnedMultiCard(Exception)` with `__init__(self, sdr_type, bus, device)`. In `_collect_per_sdr_values`, replace the `if not serial:` branch's body with: if `sole`, keep today's warning and commented line; else `raise UnpinnedMultiCard(...)`. In `cmd_radiod_init`, wrap the per-SDR loop body's `_collect_per_sdr_values` call in `try/except UnpinnedMultiCard as e`, print with `err(...)` the message "more than one RX888 on the bus and {type} on bus {bus}/{device} has no readable serial; a conf without `serial =` would open whichever card is free and a reboot could swap antennas. Fix udev access and re-run, or run `smd admin sdr identify` once that exists." and `return 1` before anything is written (the loop writes per SDR today; move the write of every plan after the loop so a refusal writes nothing).

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_radiod_config.py -q`
Expected: PASS, including the existing `SoleSdrSerialBindingTests`.

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/commands/radiod_config.py tests/test_radiod_config.py
git commit -m "radiod config: a second RX888 with no readable serial refuses to render rather than bind to whichever card is free"
```

---

### Task 2: DFU bootstrap waits for the card's real serial

**Files:**
- Modify: `lib/sigmond/commands/radiod_config.py:217-310` (`_bootstrap_dfu_sdrs`)
- Test: `tests/test_radiod_config.py`

**Interfaces:**
- Produces: `_bootstrap_dfu_sdrs(dfu_sdrs: list, iface: str) -> list[str]` now returns the serials of the cards that came up programmed, in bootstrap order (empty list on total failure). `cmd_radiod_init` treats a list shorter than `len(dfu_sdrs)` the way it treated `False`.
- New pure helper: `_new_programmed_serials(before: set[str], now: list) -> set[str]`: serials of non-DFU SDRs in `now` (upper-cased) that are not in `before`.

- [ ] **Step 1: Write the failing tests**, class `DfuBootstrapSerialTests`. Mock `subprocess.run` to succeed, `time.sleep` to no-op, and `_discover_sdrs` to return a scripted sequence:

```python
def test_waits_for_a_new_programmed_serial_not_a_count_drop(self):
    # sequence: [dfu A, programmed X(serial "0001")] -> same -> [programmed X, programmed Y("0002")]
    got = radiod_config._bootstrap_dfu_sdrs([dfu_a], "eth0")
    self.assertEqual(got, ["0002"])

def test_two_cards_bootstrap_in_order_and_report_both(self):
    # after first stub: Y appears; after second stub: Z("0003") appears
    self.assertEqual(radiod_config._bootstrap_dfu_sdrs([dfu_a, dfu_b], "eth0"), ["0002", "0003"])

def test_timeout_reports_the_cards_that_did_not_come_up(self):
    # discovery never changes -> [] and err() mentions "did not come up with a serial"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_radiod_config.py -k DfuBootstrapSerial -q`
Expected: FAIL (`_new_programmed_serials` missing / return type is bool)

- [ ] **Step 3: Implement.** Before each stub starts, `before = {serials of non-DFU SDRs}`. In the poll loop, `new = _new_programmed_serials(before, _discover_sdrs())`; success when `len(new) == 1`; append it. Keep `BOOTSTRAP_TIMEOUT_S = 30`. Update the two callers' truthiness checks in `cmd_radiod_init` (they already re-discover afterwards; keep that).

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_radiod_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/commands/radiod_config.py tests/test_radiod_config.py
git commit -m "radiod config: DFU bootstrap waits for the card's own serial, so two cards programmed back to back cannot be confused"
```

---

### Task 3: `sigmond.sdr_map` and `smd admin sdr list`

**Files:**
- Create: `lib/sigmond/sdr_map.py`
- Create: `lib/sigmond/commands/sdr.py`
- Modify: `bin/smd` (admin parser near line 26440; admin dispatch near line 28390), `lib/sigmond/cli_reference.py` (verb table)
- Test: `tests/test_sdr_map.py`, `tests/test_cli_reference.py` (existing, must stay green)

**Interfaces:**
- Produces, in `sdr_map.py`:
  - `@dataclass(frozen=True) class PresentCard: serial: str; devpath: str; speed: str; programmed: bool` (`programmed=False` for `04b4:00f3`, serial `""`).
  - `present_cards(root: Path = Path('/sys/bus/usb/devices')) -> list[PresentCard]`, sysfs order, serials upper-cased.
  - `conf_serial(text: str) -> str`: the uncommented `serial =` value in the `[rx888]` section, upper-cased, `""` if none or commented.
  - `instance_serials(confdir: Path = Path('/etc/radio'), coord=None) -> dict[str, str]`: `{instance_id: serial}` for every `radiod@*.conf` with an `[rx888]` section; the conf's `serial =` wins, else `coord.radiods[id].sdr_serial`, else `""`.
  - `serial_to_instance(instances: dict[str, str]) -> dict[str, str]` inverted, excluding `""`.
  - `@dataclass class SdrReport: cards: list[PresentCard]; instances: dict[str,str]; problems: list[str]` and `report(cards, instances) -> SdrReport` with these problem strings, each exact:
    - `radiod@{inst} has no 'serial =' line, so it opens whichever RX888 is free: a reboot can swap antennas` (only when `len(instances) > 1`)
    - `radiod@{inst} wants RX888 {serial}, which is not on the USB bus`
    - `RX888 {serial} is named by more than one radiod: {a},{b}`
    - `RX888 {serial} on {devpath} is not named by any radiod conf` (only when `len(instances) > 1`)
    - `RX888 {serial} on {devpath} is at only {speed} Mb/s, so radiod will not open it` (speed < 5000)
    - `{n} RX888(s) still in the FX3 bootloader (no firmware yet, so no serial)` (informational, listed last)
- Produces, in `commands/sdr.py`: `cmd_sdr_list(args) -> int` (0 when `problems` is empty, 1 otherwise; `--json` prints `{"cards": [...], "instances": {...}, "problems": [...]}`); `cmd_sdr_identify(args) -> int` (Task 4; this task adds the stub that prints "not implemented" and returns 2).
- `bin/smd`: `smd admin sdr {list|identify}`; bare `smd admin sdr` means `list`. Dispatch follows the `instance` block pattern at line 28390.

- [ ] **Step 1: Write the failing tests** in `tests/test_sdr_map.py`. Build fake sysfs trees under `tmp_path` the way `tests/test_usb_delivery.py:237-305` does (dirs `4-1.4/` with `idVendor`, `idProduct`, `serial`, `speed`), and fake confs:

```python
def test_present_cards_reads_programmed_and_bootloader(tmp_path): ...  # one 00f1 with serial, one 00f3 -> programmed False, serial ""
def test_conf_serial_ignores_commented_line(): assert conf_serial('[rx888]\n# serial = "ABC"\n') == ""
def test_conf_serial_reads_rx888_section_only(): ...  # serial under [airspy] is not returned
def test_instance_serials_prefers_conf_then_coordination(tmp_path): ...
def test_report_flags_swap_risk_only_with_two_instances(): ...  # one instance, no serial -> no problem
def test_report_flags_missing_duplicate_unclaimed_and_slow(): ...  # exact strings above
def test_report_counts_bootloader_cards_without_mapping_them(): ...  # Review Focus 3
def test_cmd_sdr_list_json_and_exit_code(monkeypatch, capsys): ...  # rc 1 with problems, 0 without
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sdr_map.py -q`
Expected: FAIL, `ModuleNotFoundError: sigmond.sdr_map`

- [ ] **Step 3: Implement `sdr_map.py`** with the signatures above. `present_cards` mirrors `bin/smd:_rx888_usb_devices` (line 23333) but also lists `00f3`; do not import from `bin/smd`. `conf_serial` is a section-aware line scan (the `[rx888]` header starts the section, any `[` line ends it).

- [ ] **Step 4: Implement `commands/sdr.py` and wire `bin/smd`.** Table output: the two tables `wd -X` prints (cards: SERIAL, USB, SPEED, OPENED BY; instances: INSTANCE, STATE, SERIAL, ON BUS, ANTENNA), then `Needs attention:` with one `- ` line per problem, or `Every local radiod names its own RX888 and every RX888 is on the bus.` STATE comes from `systemctl is-active radiod@<id>`; ANTENNA from the conf's `description =`. Add the verb to `cli_reference.py`.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_sdr_map.py tests/test_cli_reference.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add lib/sigmond/sdr_map.py lib/sigmond/commands/sdr.py bin/smd lib/sigmond/cli_reference.py tests/test_sdr_map.py
git commit -m "smd admin sdr list: which RX888 is which radiod, and what is missing, duplicated or unclaimed"
```

---

### Task 4: `smd admin sdr identify`, the unplug walk

**Files:**
- Modify: `lib/sigmond/sdr_map.py`, `lib/sigmond/commands/sdr.py`
- Test: `tests/test_sdr_map.py`

**Interfaces:**
- Consumes: `present_cards`, `instance_serials`, `conf_serial` (Task 3).
- Produces, in `sdr_map.py`:
  - `wait_for_drop(before: set[str], poll: Callable[[], set[str]], deadline: float, clock=time.monotonic, sleep=time.sleep) -> str | None`: the ONE serial present in `before` and absent from `poll()`; returns `None` on timeout; raises `AmbiguousDrop(serials)` if more than one went missing in the same poll (Review Focus 2).
  - `wait_for_return(serial: str, poll, deadline, ...) -> bool`.
  - `set_conf_serial(text: str, serial: str) -> str`: in the `[rx888]` section, replace an existing (commented or not) `serial =` line with `serial      = "{serial}"`, or insert it as the section's first key; returns the new text; raises `ValueError` if there is no `[rx888]` section.
- Produces, in `commands/sdr.py`: `cmd_sdr_identify(args) -> int`. Flow: `_need_root`; refuse if `len(instances) < 2` ("one radiod needs no identify; its conf is unbound by design"); stop `sigmond-radiod-watchdog.timer` and every `radiod@<id>.service`; for each instance in sorted order: print `Unplug the RX888 on: {description}  (radiod@{id})`, `wait_for_drop` with `WAIT_SECS = 180` (env `SIGMOND_SDR_IDENTIFY_WAIT_SECS`); on `AmbiguousDrop` print the serials and re-prompt the same instance; print `Plug it back in`, `wait_for_return` (20 s bootloader grace folded into the same deadline); at the end print the mapping table and, unless `--dry-run`, write `set_conf_serial` into each conf and `sdr_serial` into coordination.toml (reuse `_append_coordination`'s writer or a targeted TOML edit, builder's choice), then start the units and the timer again. Exit 0; exit 3 if the walk was abandoned (timeout) with nothing written.

- [ ] **Step 1: Write the failing tests**

```python
def test_wait_for_drop_returns_the_one_missing_serial(): ...
def test_wait_for_drop_two_missing_raises_ambiguous(): ...
def test_wait_for_drop_times_out_to_none(): ...
def test_set_conf_serial_replaces_commented_line_in_rx888_section(): ...
def test_set_conf_serial_inserts_when_absent_and_refuses_without_section(): ...
def test_identify_refuses_single_instance(monkeypatch, capsys): ...  # rc 1
def test_identify_dry_run_writes_nothing(monkeypatch, tmp_path): ...  # scripted polls for two cards
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sdr_map.py -k "drop or return or set_conf or identify" -q`
Expected: FAIL

- [ ] **Step 3: Implement** the three helpers and `cmd_sdr_identify`. Poll interval 0.25 s. All systemctl calls go through one `_systemctl(*args)` wrapper so tests can monkeypatch it.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_sdr_map.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/sdr_map.py lib/sigmond/commands/sdr.py tests/test_sdr_map.py
git commit -m "smd admin sdr identify: confirm which RX888 is on which antenna by unplugging them one at a time, then pin the serials"
```

---

### Task 5: the radiod watchdog scopes a hold by serial

**Files:**
- Modify: `bin/sigmond-radiod-watchdog:78-102` (`_held_units`)
- Test: `tests/test_radiod_watchdog.py`

**Interfaces:**
- Consumes: `sigmond.sdr_map.instance_serials`, `serial_to_instance` (Task 3). The watchdog already imports from `sigmond` (`sdr_events`), so the import path is available.
- Produces: `_held_units(serial_map: Optional[dict] = None) -> tuple[tuple[str, ...], bool]`. Resolution per hold: `radiod_unit` if present; else `serial` looked up in `serial_map` (`{serial: instance}`) giving `radiod@{instance}.service`; else blanket. `main()` builds the map once with `serial_to_instance(instance_serials())` inside a try that falls back to `{}`.

- [ ] **Step 1: Write the failing tests** (the file already loads the script by path; follow its pattern):

```python
def test_hold_with_serial_but_no_unit_resolves_through_map(self):
    # hold {"ABC": {"serial": "ABC"}} and map {"ABC": "kfs-se"} -> (("radiod@kfs-se.service",), False)
def test_hold_with_unknown_serial_is_blanket(self): ...
def test_legacy_hold_without_serial_or_unit_is_blanket(self): ...   # Review Focus 4
def test_hold_with_unit_wins_over_serial(self): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_radiod_watchdog.py -k hold -q`
Expected: FAIL (`TypeError: _held_units() takes 0 positional arguments`)

- [ ] **Step 3: Implement** as specified; keep the printed sentence in `main()` and add `(resolved by serial)` after a unit name that came from the map.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_radiod_watchdog.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/sigmond-radiod-watchdog tests/test_radiod_watchdog.py
git commit -m "radiod watchdog: a hold that names a serial covers that card's radiod only, not every instance"
```

---

### Task 6: CPU placement says so instead of sharing a core

**Files:**
- Modify: `lib/sigmond/cpu.py:268-307` (`assign_radiod_cores`), `bin/smd` (the radiod drop-in writer that calls `assign_radiod_cores`; find with `grep -n assign_radiod_cores bin/smd`)
- Test: `tests/test_cpu.py`

**Interfaces:**
- Produces: `radiod_core_shortfall(units: list[str], *, cores=None, isolated_cpus=None, topology_cpu_affinity=None) -> int` in `cpu.py`: `max(0, len(units) - len(eligible))`, `0` when an override mask is set. `assign_radiod_cores` is unchanged in behaviour (it still shares the last core) so existing callers and tests stand.
- `bin/smd` drop-in writer: when `radiod_core_shortfall(...) > 0` and `--allow-shared-radiod-cores` is not given, print `{n} radiod instance(s) but only {m} eligible core(s): {units} would share core {c}. Give the VM more cores (LOCAL_RADIOD_COUNT on the Proxmox host) or pass --allow-shared-radiod-cores.` and return 1 without writing.

- [ ] **Step 1: Write the failing tests**

```python
def test_shortfall_zero_when_cores_suffice(): assert radiod_core_shortfall(["a","b"], cores=[{0,1},{2,3},{4,5}], isolated_cpus=set()) == 0
def test_shortfall_counts_instances_beyond_eligible_cores(): ...  # 3 units, cores [{0,1},{2,3}] -> eligible off-boot is [{2,3}] -> 2
def test_shortfall_zero_with_override_mask(): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cpu.py -k shortfall -q`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Implement** the helper (reuse `eligible_radiod_cores`) and the refusal in the drop-in writer, with the new flag on that `smd` verb's parser.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_cpu.py tests/test_tui_cpu_affinity.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/cpu.py bin/smd tests/test_cpu.py
git commit -m "cpu: two radiods on one eligible core is refused with the count, not shared in silence"
```

---

### Task 7: the Proxmox wizard counts the cards

**Files:**
- Modify: `scripts/proxmox/sigmond-wizard.sh` (the step that sets up USB passthrough; `grep -n rx888 scripts/proxmox/sigmond-wizard.sh`), `scripts/proxmox/host-setup.sh:34` (already reads `LOCAL_RADIOD_COUNT`)
- Test: `tests/test-proxmox-wizard-radiod-count.sh` (new, same style as `tests/test-usb-passthrough-deny.sh`)

**Interfaces:**
- Produces: shell function `sigmond_wizard_radiod_count()` in the wizard: counts `lsusb -d 04b4:00f1` plus `lsusb -d 04b4:00f3` lines, prints the number, minimum 1. The wizard shows `Found {n} RX888(s); the VM will be laid out for {n} radiod(s)` and exports `LOCAL_RADIOD_COUNT={n}` before calling `host-setup.sh`; an operator may override with `--radiod-count N` which the wizard forwards.

- [ ] **Step 1: Write the failing test**: a bash test that puts a fake `lsusb` on `PATH` printing two `00f1` lines and one `00f3`, sources the function, and asserts it prints `3`; a second case with no output asserts `1`.

- [ ] **Step 2: Run to verify it fails**

Run: `bash tests/test-proxmox-wizard-radiod-count.sh`
Expected: `FAIL: sigmond_wizard_radiod_count: command not found`

- [ ] **Step 3: Implement** the function and the two call-site lines in the wizard.

- [ ] **Step 4: Run the test**

Run: `bash tests/test-proxmox-wizard-radiod-count.sh`
Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add scripts/proxmox/sigmond-wizard.sh tests/test-proxmox-wizard-radiod-count.sh
git commit -m "proxmox wizard: count the RX888s on the host bus and lay the VM out for that many radiods"
```

---

### Task 8: ka9q-web ports that skip every other viewer

**Files:**
- Modify: `bin/smd:11534-11562` (`_ka9q_web_targets`), `lib/sigmond/viewers.py:1-10` (docstring), `tasks/release-v383.md` (mark item 9 done)
- Test: `tests/test_ka9q_web_units.py`, `tests/test_viewers.py`

**Interfaces:**
- Produces: `_ka9q_web_port(index: int, reserved: frozenset[int]) -> int` in `bin/smd`: `8081` for index 0 (Review Focus 5); for index ≥ 1 the `index`-th port counting up from 8082 that is not in `reserved`. `_ka9q_web_targets()` passes `reserved = frozenset({gmag_webui.PORT, 8000})` (gmag-webui and station-web). With gmag on 8082, the second radiod gets 8083, the third 8084.

- [ ] **Step 1: Write the failing tests**

```python
def test_second_radiod_skips_gmag_webui_port(smd, monkeypatch):
    _with(monkeypatch, [Radiod(id="a", host="localhost", status_dns="a-status.local"),
                        Radiod(id="b", host="localhost", status_dns="b-status.local")])
    assert smd._ka9q_web_targets() == [("ka9q-web.service", "a-status.local", 8081),
                                      ("ka9q-web@b.service", "b-status.local", 8083)]
def test_first_radiod_always_8081(smd): assert smd._ka9q_web_port(0, frozenset({8081})) == 8081
def test_port_conflicts_empty_with_two_radiods_and_gmag(): ...  # viewers.port_conflicts([...]) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ka9q_web_units.py tests/test_viewers.py -q`
Expected: FAIL (second radiod on 8082)

- [ ] **Step 3: Implement** `_ka9q_web_port` and use it in `_ka9q_web_targets`; update the docstring lines that say "8082, 8083, ...", and the viewers.py docstring sentence about the clash.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_ka9q_web_units.py tests/test_viewers.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/smd lib/sigmond/viewers.py tasks/release-v383.md tests/test_ka9q_web_units.py tests/test_viewers.py
git commit -m "ka9q-web: a second radiod's web UI takes a port no other viewer owns"
```

---

### Task 9: documentation and the whole suite

**Files:**
- Modify: `docs/commands/smd.md` (add an `smd admin sdr` section beside `smd admin instance`), `docs/superpowers/specs/2026-10-30-multi-receiver-station-design.md` §10 row 1 (mark delivered; note that §5 item 7, Stage 1 looping over N local radiods, lands with Phase 3's receiver-driven `build_plan`)
- Test: the full suite

- [ ] **Step 1: Write the `smd admin sdr` section**: what `list` shows and what each problem line means; the `identify` walk step by step, including "two serials dropped, I asked again"; that one-card stations are left unpinned on purpose.

- [ ] **Step 2: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q -x`
Expected: PASS (the docs conformance test in `tests/` checks the index link)

- [ ] **Step 3: Commit**

```bash
git add docs/commands/smd.md docs/superpowers/specs/2026-10-30-multi-receiver-station-design.md
git commit -m "docs: smd admin sdr, and Phase 1 of the multi-receiver design marked delivered"
```

# Archive Drive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A USB drive added with `smd archive add` is health-checked, survives replugs, and fills with verified F16 copies of hf-timestd's raw IQ, with days held and days that fit shown by `smd archive status`; plus two safety pieces from the data-volume design; for v3.91.

**Architecture:** sigmond owns the drive (SMART health, fstab/automount, archive.toml, a root hourly health timer, status, `smd check`). hf-timestd owns the data (an idle-priority oneshot archiver every 10 min that copies, converts, verifies and makes room). They meet only at `/etc/sigmond/archive.toml`, `/var/lib/sigmond/archive-health.json` (sigmond writes) and `/var/lib/timestd/archive-status.json` (archiver writes).

**Tech Stack:** Python 3.11 (tomllib), smartctl 7 JSON, systemd units, numpy + zstandard (hf-timestd's), pytest.

**Spec:** sigmond `docs/superpowers/specs/2026-11-12-archive-drive-design.md` (Rob approved for v3.91 on 2026-11-13). Tasks 9–10 implement two pieces of `2026-11-08-data-volume-design.md` ("Snapshots still pin the data disk"; the two manual guards in "What it means for the stations Rob already moved"); Rob 2026-11-13: v3.91 takes these, the full data volume is v3.92. Folded in: the uncommitted KPH archive-quota draft, saved beside this plan as `2026-11-13-archive-drive-kph-draft.patch`.

## Global Constraints

- Repos: sigmond, hf-timestd, sigmond-appliance. Commit straight to main; `git fetch && git rebase origin/main`, test, push; never force-push; never push while `ssh b3 'pgrep -fa "ladder-v|build-golden|build-usb-v3|test-nested|test-update"'` returns 0.
- hf-timestd is Michael's area: post the file list to `/srv/hamsci/claude-bus/` before each hf-timestd push. Do not touch `core/binary_archive_writer.py`, `core/core_recorder_v2.py`, `core/stream_recorder_v2.py` (Michael's torn-read fix, 9acb01c).
- Paths: mount `/var/lib/sigmond/archive`; config `/etc/sigmond/archive.toml` (root, 0644); health `/var/lib/sigmond/archive-health.json` (root, 0644); status `/var/lib/timestd/archive-status.json`; lock `/run/timestd-archive.lock`; drive folder `<mount>/timestd/<short hostname>/`.
- fstab options: `nofail,noatime,x-systemd.automount,x-systemd.device-timeout=10s`, pass 2.
- archive.toml defaults written by `add`: `archive_dtype = "f16"`, `high_water_pct = 90`, `kept_ceiling_pct = 25`, `evict_first = []`.
- F16: `iq_codec.encode_f16`, scale 4096, sidecar `dtype = "complex-f16"`, `scale = 4096`, `converted_from = "complex64"`, no `f16_check`. A sidecar with no `dtype` is complex64.
- Closed file: sidecar exists and both files unmodified ≥ 120 s. Never delete today's or yesterday's UTC day.
- SMART thresholds: FAIL = verdict failed, attr 197 > 0, attr 198 > 0, NVMe media_errors > 0, short self-test failed. WARN = attr 5 > 0, attr 199 > 0, temperature > 55 °C, power-on hours > 43 800, NVMe percentage_used > 80.
- `-d` order: `sat`, auto (no `-d`), `usbjmicron`, `usbcypress`, `usbprolific`, `usbsunplus`.
- Nothing that only reports (`status`, `check`, heartbeat) may stat inside the mount point; mount state comes from `/proc/self/mountinfo` and `blkid`.
- The archiver never runs smartctl and never writes outside `<mount>/timestd/` and its status file.

## Review Focus

1. Drive unplugged between the archiver's gate and its copy (ENODEV/EIO mid-file): the run ends, logs once, leaves no `.part` counted as archived, recorder untouched → Task 6 test `test_copy_error_ends_run_cleanly`.
2. A source file still being written (mtime < 120 s, or sidecar missing): never copied → Task 6 `test_open_files_skipped`.
3. A sidecar that is not valid JSON, or a `.bin.zst` with no sidecar older than a day: skipped and counted, never crashes the run → Task 6 `test_bad_sidecar_skipped`.
4. Drive above high-water with only today/yesterday left: copying stops, status says `full`, check WARNs → Task 7 `test_full_with_only_protected_days`.
5. A bridge that returns no SMART at all (every `-d` fails): verdict `unknown`, `add` continues, never reported as failing → Task 1 `test_no_smart_is_unknown`.

---

### Task 1: SMART reading and verdict (sigmond)

**Files:**
- Create: `lib/sigmond/archive_health.py`
- Test: `tests/test_archive_health.py`, fixtures `tests/fixtures/smart/{seagate_ok.json,pending.json,nvme_ok.json,uas_blocked.txt,selftest_running.json}`

**Interfaces:**
- Produces:
  - `@dataclass Reading: passed: Optional[bool]; model: str; disk_serial: str; power_on_hours: Optional[int]; power_cycles: Optional[int]; reallocated: Optional[int]; pending: Optional[int]; offline_uncorrectable: Optional[int]; reported_uncorrect: Optional[int]; crc_errors: Optional[int]; temperature_c: Optional[int]; nvme_media_errors: Optional[int]; nvme_percentage_used: Optional[int]; selftest_status: Optional[str]` (`'passed'|'failed'|'running'|None`)`; selftest_remaining_pct: Optional[int]; dev_type: Optional[str]`
  - `parse_smartctl(doc: dict, dev_type: Optional[str]) -> Reading`
  - `probe(device: str, run=subprocess.run) -> Tuple[Optional[Reading], str]` — tries the `-d` order; returns the first reading with attributes, else `(None, reason)`; reason contains `"blocks SMART under UAS"` when the `sat` stderr/messages say "unsupported field in scsi command" and the device's driver is `uas`.
  - `verdict(r: Optional[Reading]) -> Tuple[str, List[str]]` — `'fail'|'warn'|'healthy'|'unknown'` plus reasons.
  - `start_short_test(device, dev_type, run=...) -> bool`; `wait_self_test(device, dev_type, run=..., sleep=time.sleep, say=print, timeout_s=600) -> Optional[str]` (final status).
  - `uas_quirk_line(vid: str, pid: str) -> str` → `"options usb-storage quirks=<vid>:<pid>:u"`.
  - `health_doc(baseline: Reading, latest: Reading, when_utc: str) -> dict`; `grown(baseline, latest) -> List[str]` (reallocated/pending/uncorrectable increases).

- [ ] **Step 1: Write failing tests** — `test_parse_ata` (seagate_ok.json → reallocated 0, pending 0, power_on_hours from attr 9, temperature from 194), `test_parse_nvme` (nvme_ok.json → media_errors, percentage_used), `test_verdict_fail_on_pending` (pending.json → `('fail', [... 'pending' ...])`), `test_verdict_warn_thresholds` (temp 56 → warn; poh 43801 → warn), `test_no_smart_is_unknown` (every `-d` returns no attributes → probe `(None, reason)`, verdict `('unknown', [reason])`), `test_probe_order` (fake run records `-d sat` first, then no `-d`, then `usbjmicron`…), `test_uas_blocked_reason`, `test_grown`.
- [ ] **Step 2:** `pytest tests/test_archive_health.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement. smartctl is called as `smartctl -j -a [-d T] /dev/X`; treat exit bits 0–1 (command/open failure) as "no reading", other bits as a reading. Self-test: `smartctl -t short`, poll `-j -c` every 10 s (`self_test.status.remaining_percent`).
- [ ] **Step 4:** tests PASS.
- [ ] **Step 5:** commit `archive: SMART reading, bridge probing and verdict`.

### Task 2: `add` gains health, automount and the new archive.toml (sigmond)

**Files:**
- Modify: `lib/sigmond/archive.py` (`MOUNT_OPTS`, `config_text`, new `fstab_needs_upgrade(text) -> bool`, `mount_state(uuid, mountinfo=..., blkid=...) -> str` `'mounted'|'present'|'absent'`)
- Modify: `lib/sigmond/commands/archive.py` (`_add`, parser: `--no-self-test`, `--accept-unhealthy DEVICE`)
- Test: `tests/test_archive.py`, `tests/test_archive_cmd.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: archive.toml keys `disk_serial`, `smart_dev_type`, `archive_dtype`, `evict_first`; `archive-health.json` = `health_doc(...)`; `mount_state()` used by Tasks 4 and 5.

- [ ] **Step 1: Failing tests** — `test_fstab_line_has_automount` (line ends `ext4 nofail,noatime,x-systemd.automount,x-systemd.device-timeout=10s 0 2`), `test_fstab_upgrade_detected` (old line without automount → True), `test_config_text_new_keys`, `test_mount_state_three_ways` (mountinfo ext4 at mount with matching UUID → mounted; UUID in blkid only → present; neither → absent; autofs entry alone → not mounted), `test_add_refuses_fail` (fake health 'fail' → rc 1, nothing run past the health step), `test_add_accept_unhealthy_needs_name` (`--accept-unhealthy sdc` for disk sdb → refuse), `test_add_dry_run_reads_only` (no self-test, no mkfs), `test_add_offers_uas_quirk` (answer y → writes `/etc/modprobe.d/sigmond-archive-usb.conf` with the quirk line and rebinds via injected callable), `test_add_masks_smartd`, `test_add_chattr_before_mount` (order: `chattr +i` on bare mount point precedes mount), `test_add_upgrades_old_fstab_line`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement in `_add`, after placement and before the erase prompt: ensure smartctl (apt-get install -y smartmontools when missing; `systemctl mask --now smartmontools.service`), probe, UAS offer, show the table (spec "What add shows"), short self-test unless `--no-self-test` or dry run, verdict gate, then the existing erase flow. A known SMR model (ST2000LM007, ST4000LM024, ST5000LM000, WD20SPZX, WD40NDZW) gets one info line. After mount: `chattr -i` is never needed (immutable is on the bare dir, the mount covers it); write health json; enable `sigmond-archive-health.timer` (Task 3). Re-running `add` on a drive already in archive.toml with only an old fstab line rewrites the line (no format) when `--upgrade` is passed *(writer's call: `smd archive add --upgrade` is the in-place path for AI6VN-1)*.
- [ ] **Step 4:** PASS. **Step 5:** commit `archive add: SMART health and self-test before the erase; automount; chattr +i; UAS quirk`.

### Task 3: Health timer, `test --long`, `eject` (sigmond)

**Files:**
- Create: `systemd/sigmond-archive-health.service` (oneshot, root, `ExecStart=/usr/local/bin/smd archive health-refresh`, `IOSchedulingClass=idle`), `systemd/sigmond-archive-health.timer` (`OnCalendar=hourly`, `Persistent=true`)
- Modify: `lib/sigmond/commands/archive.py` (verbs `health-refresh`, `test --long`, `eject`; `remove --forget` disables/removes the timer)
- Modify: `lib/sigmond/cpu.py` (map `sigmond-archive-health.service`, `timestd-archive.service` → `'other'`)
- Test: `tests/test_archive_cmd.py`

- [ ] **Step 1: Failing tests** — `test_health_refresh_updates_latest_only` (baseline unchanged, latest replaced, file mode 0644), `test_health_refresh_skips_when_absent` (mount_state absent → no smartctl call, latest marked `"absent"`), `test_test_long_starts_and_returns`, `test_eject_waits_for_archiver` (injected `is_active('timestd-archive.service')` True then False), `test_eject_unmounts_and_stops_automount` (calls `systemctl stop var-lib-sigmond-archive.automount var-lib-sigmond-archive.mount`, prints "safe to unplug"), `test_forget_removes_timer`.
- [ ] **Step 2:** FAIL. **Step 3:** implement; `health-refresh` uses `smart_dev_type` from archive.toml and `-n standby` so a spun-down drive is not woken (a standby skip keeps the previous latest and records `skipped_standby_utc`). **Step 4:** PASS. **Step 5:** commit `archive: hourly health refresh, test --long, eject`.

### Task 4: `status` and `smd check` platform.archive (sigmond)

**Files:**
- Modify: `lib/sigmond/commands/archive.py` (`_status`), `lib/sigmond/archive.py` (`days_held(status: dict) -> Dict[str, Tuple[str,str,int]]`, `measured_bytes_per_day(status: dict) -> Optional[float]`)
- Modify: `lib/sigmond/commands/check.py` (`archive_line(env) -> Optional[Line]`, `CHECK_META['archive'] = ('platform.archive', 'platform', None)`, entry in `CHECKS` after storage)
- Test: `tests/test_archive_cmd.py`, `tests/test_check_archive.py`

**Interfaces:**
- Consumes: archive-status.json from Task 7: `{"run_utc", "present", "state": "ok|absent|full|error", "channels": {CH: {"oldest": "YYYYMMDD", "newest": "YYYYMMDD", "days": int, "bytes": int, "dtype": "complex-f16|complex64"}}, "copied", "bytes_copied", "behind", "kept_f32", "failures": int, "last_error", "bytes_per_day": float, "evicted": [..]}`.

- [ ] **Step 1: Failing tests** — `test_status_never_stats_mount` (mount path patched to raise on any os.stat/listdir; status still prints), `test_status_days_held_per_channel`, `test_status_days_fit_from_measured_rate` (bytes_per_day from status, not 15 GB), `test_status_present_not_mounted_shows_fix`, check lines: no toml → None; mounted+healthy+run ≤ 2 h → OK; present-not-mounted → WARN with `run: sudo mount /var/lib/sigmond/archive`; grown reallocated → WARN; run > 2 h old → WARN; `state == "full"` → WARN; pending > 0 or failed → FAIL; prune drop-in containing `--archive-root` → WARN.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS, then the full sigmond suite. **Step 5:** commit `archive: status shows days held and days that fit; smd check platform.archive`.

### Task 5: Retire the old move (sigmond, hf-timestd, appliance)

**Files:**
- Modify: sigmond `bin/smd` (timestd-tune-storage drop-in text without `--archive-root`; rewriting an existing drop-in drops it)
- Modify: hf-timestd `systemd/timestd-prune.service`, `deploy/systemd/timestd-raw-cleanup.service` (no archive root), `config/help.toml` (`[recorder.archive_root]` says: deprecated, use `smd archive add`)
- Modify: sigmond-appliance `provision.sh` (add `smartmontools` beside sqlite3 lsof bc; then `systemctl mask smartmontools.service`)
- Test: sigmond test for the drop-in text; hf-timestd `tests/unit/test_prune_unit.py` (`--archive-root` absent from the unit)

- [ ] Steps: failing tests → FAIL → edit → PASS → commit per repo (`retire --archive-root: the archiver copies; cleanup only deletes from the SSD`; appliance: `provision: smartmontools (smartd masked) for the archive health check`).

### Task 6: Archiver copy, convert, verify (hf-timestd)

**Files:**
- Create: `src/hf_timestd/archiver.py`
- Test: `tests/test_archiver.py` (build sources in tmp with `iq_codec`, zstd level 3, sidecars like `tests/fixtures/f16_archive`)

**Interfaces:**
- Produces:
  - `closed_files(raw_root: Path, now: float, min_age_s=120) -> Iterator[Tuple[Path, Path]]` — (bin, sidecar), oldest day first.
  - `copy_one(src_bin: Path, src_json: Path, dst_dir: Path, archive_dtype: str) -> str` — returns `'copied'|'converted'|'kept_f32'|'skipped'`; raises `ArchiveIOError` on drive errors, `VerifyError` on a mismatch.
  - `f16_within_rounding(src: np.ndarray, dec: np.ndarray) -> bool` — finite: `|dec*4096 - src*4096| <= 2**-10 * |src*4096| + 2**-24`; NaN/inf positions equal.
- [ ] **Step 1: Failing tests** — `test_f32_source_converted` (dst sidecar dtype/scale/converted_from, no f16_check; decode within rounding), `test_f16_source_byte_copy`, `test_complex64_mode_byte_copy_sha`, `test_overflow_kept_f32` (a sample of 20.0 → `'kept_f32'`, sidecar dtype complex64), `test_open_files_skipped`, `test_bad_sidecar_skipped`, `test_existing_destination_skipped`, `test_part_file_never_left_on_verify_fail` (monkeypatch decode to corrupt → VerifyError, no `.part`, no final file), `test_copy_error_ends_run_cleanly` (write raises OSError(ENODEV) → ArchiveIOError, `.part` removed when possible).
- [ ] **Step 2:** FAIL. **Step 3:** implement: write `.<name>.part`, fsync, verify, `os.replace`, sidecar last, fsync dir. **Step 4:** PASS. **Step 5:** commit `archiver: copy closed raw IQ to the drive, F16 with per-sample verification`.

### Task 7: Archiver run, making room, status, units, CLI (hf-timestd)

**Files:**
- Modify: `src/hf_timestd/archiver.py` (`run(config_path=Path('/etc/sigmond/archive.toml'), data_root=Path('/var/lib/timestd'), status_path=..., now=time.time) -> dict`; `make_room(drive_root: Path, mount: Path, high_water_pct: float, evict_first: List[Path], today: date) -> dict`; `gate(cfg) -> Optional[str]` reason)
- Modify: `src/hf_timestd/cli.py` (`archive run`, `archive status --json`)
- Create: `systemd/timestd-archive.service` (oneshot, `User=timestd`, `IOSchedulingClass=idle`, `Nice=19`, `ExecStart=<venv>/bin/python -m hf_timestd archive run`), `systemd/timestd-archive.timer` (`OnBootSec=5min`, `OnUnitActiveSec=10min`, `Persistent=true`)
- Modify: `scripts/install.sh` (install + `systemctl enable --now timestd-archive.timer`)
- Test: `tests/test_archiver_run.py`

- [ ] **Step 1: Failing tests** — `test_gate_no_toml`, `test_gate_not_a_mount` (ismount False → no write anywhere), `test_gate_uuid_mismatch`, `test_station_json_written`, `test_make_room_oldest_day_across_channels`, `test_evict_first_before_own` (KPH: `evict_first=[<mount>/wav-archive]` YYYYMMDD dirs go first, oldest first; non-date names untouched), `test_never_today_or_yesterday`, `test_full_with_only_protected_days` (state `full`, copying stops), `test_flock_second_run_exits`, `test_status_json_shape` (keys of Task 4's Consumes), `test_three_failures_counted`.
- [ ] **Step 2:** FAIL. **Step 3:** implement; make room before and after copies; usage from `statvfs` as in the KPH draft's `get_archive_usage`; three verify failures per file tracked in `<drive>/.archive-failures.json`. **Step 4:** PASS, then `pytest tests -q -x`. **Step 5:** bus post with file list; commit `archiver: run, make room (evict_first first), status, timer`.

### Task 8: `hf-timestd archive export-f32` (hf-timestd)

**Files:** Modify `src/hf_timestd/archiver.py` (`export_f32(src_root, dst, channel, start_utc, end_utc) -> int` files), `cli.py`; Test `tests/test_archive_export.py`
- [ ] `test_export_roundtrip` (F16 archive → F32 `.bin.zst` + sidecar dtype complex64, no scale/converted_from; values within rounding of the original), `test_export_window_bounds`. FAIL → implement → PASS → commit `archive export-f32`. `smd archive status` prints the command (Task 4 adds one line).

### Task 9: No whole-VM snapshot while a data disk is attached (sigmond, Proxmox host)

**Files:** Modify `scripts/proxmox/pm-storage.py` (`data_disks(qm_config_text: str) -> List[str]`; `_snapshot` refuses when it is non-empty unless `--include-data-disks`); Test `tests/test_pm_storage.py` (or the existing pm-storage test file).

- [ ] **Step 1: Failing tests** — `test_data_disks_none` (`scsi0` only, plus `ide2: ...,media=cdrom`, `efidisk0`, `tpmstate0` → `[]`), `test_data_disks_scsi1` (`scsi1: local-lvm:vm-101-disk-1,backup=0,discard=on,size=240G` → `['scsi1']`), `test_boot_disk_from_boot_order` (`boot: order=virtio0` with `virtio0` and `scsi0` → `['scsi0']`), `test_snapshot_refused_with_data_disk` (fake host: no `qm snapshot` call, rc 1, message names `scsi1` and says `vzdump <vmid> --mode snapshot` keeps backup=0 disks out), `test_snapshot_include_data_disks_flag_allows`.
- [ ] **Step 2:** FAIL. **Step 3:** implement; boot disk = first entry of `boot: order=`, else `bootdisk:`, else `scsi0`. **Step 4:** PASS. **Step 5:** commit `pm-storage: never a whole-VM snapshot while a data disk is attached`.

### Task 10: Guard data mounts: writers wait, the bare directory refuses (sigmond)

**Files:** Create `lib/sigmond/storage/guard.py`; Modify `lib/sigmond/commands/storage.py` (verb `guard [--dry-run]`), `bin/smd` (call `guard` at the end of `apply` and `update`, best-effort, one summary line), `lib/sigmond/commands/check.py` (storage line WARNs on an unguarded data mount); Test `tests/test_storage_guard.py`.

**Interfaces:**
- `data_mounts(proc_mounts: str, client_paths: Dict[str, List[str]], archive_mount: str) -> Dict[str, List[str]]` — mount point → clients with a `data_sinks` path under it; excludes `/` and the archive mount.
- `units_for(client: str) -> List[str]` — `[systemd] units` from the client's deploy.toml; for `hf-timestd` also `timestd-events.service`, `timestd-archive.service`.
- `dropin_text(mount: str) -> str` → `"# sigmond: data volume guard (smd storage guard)\n[Unit]\nRequiresMountsFor=<mount>\n"`, written to `/etc/systemd/system/<unit>.d/sigmond-data-volume.conf` (a template unit gets `<name>@.service.d`).
- `seal_bare_dir(mount: str, run=subprocess.run) -> str` — bind-mounts `/` (non-recursive) on a private temp dir, and when `<tmp><mount>` is an empty directory sets it root:root 0000 and `chattr +i`; returns `'sealed'|'already'|'not-empty'|'error: …'`; always unmounts the temp bind.
- [ ] **Step 1: Failing tests** — `test_data_mounts_finds_timestd` (`/var/lib/timestd` ext4 mount, hf-timestd sink under it → `{'/var/lib/timestd': ['hf-timestd']}`), `test_root_and_archive_excluded`, `test_dropins_written_once` (second run writes nothing; existing hand drop-ins with the same `RequiresMountsFor` are left, not duplicated), `test_template_unit_dropin_dir`, `test_seal_bare_dir_sealed_and_unbound` (fake run records `mount --bind / T`, `chmod 000`, `chattr +i`, `umount T` in that order), `test_seal_not_empty_left_alone` (a non-empty bare dir is reported, never touched), `test_guard_dry_run_changes_nothing`, `test_check_warns_unguarded`.
- [ ] **Step 2:** FAIL. **Step 3:** implement; `daemon-reload` once after writing; never restart a unit. **Step 4:** PASS + full suite. **Step 5:** commit `storage guard: data mounts get RequiresMountsFor drop-ins and a sealed bare directory`.

### Task 11: Ship and prove (operations; Rob's go for each station step)

- [ ] Push sigmond, hf-timestd, appliance (b3 idle check each time); bus post.
- [ ] AI6VN-1: `smd update`; `smd archive add --upgrade`, run the way the ops session ran `add` on 11-12 (this session never runs `sudo smd`); confirm automount line, timer, first archiver run; proof steps 4, 6, 7 of the spec over the following days.
- [ ] KPH2 (Rob's go): hand-write `/etc/sigmond/archive.toml` for the existing 16 TB drive at `/mnt/archive` with `evict_first = ["/mnt/archive/wav-archive"]` and `high_water_pct = 95`; first run with `--dry-run`.
- [ ] Deferred to v3.92 *(writer's call)*: the station console's "eject before unplugging" line; `smd archive keep`.
- [ ] KFS-SE-NW, KFS-OMNI-SW, KPH1, KPH2 (Rob's go): `smd update` (guard runs; daemon-reload only, no restarts), then `smd storage guard --dry-run` shows nothing left; a sealed `/var/lib/timestd` underneath on each; a `.not-empty` result is reported to Rob, not fixed.
- [ ] Ladder v3.91 again once all three repos are on main; Rob and Michael bless.

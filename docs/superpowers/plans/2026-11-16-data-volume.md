# Data Volume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every station keeps all of its clients' data on one data volume, apart from the system disk:
install creates it, an operator can add one to a running station, the storage ledger divides it among
the clients by days, and the system backup copies the system disk and the small state files, never the
bulk data. Target: v3.93.

**Architecture:** Four parts, each shippable on its own, in this order. (A) hf-timestd joins the storage
ledger as its first managed client and stops running its own percentage quota. (B) sigmond gains
`smd storage add|remove --role data`: one volume mounted at `/var/lib/sigmond/data`, each client's
folder on it bound to the client's usual path, with the guard Rob built (v3.91) making every writer wait
for it. (C) The Proxmox layer makes the block device appear (a second virtual disk at install, a helper
for existing VMs) and backs up the system disk with vzdump. (D) Rollout.

**Tech Stack:** Python 3.11 stdlib (sigmond core), hf-timestd (numpy allowed), bash (firstboot,
appliance tests), systemd mount/bind units via fstab, Proxmox `qm`/`pvesm`/`vzdump`, pytest.

**Spec:** sigmond `docs/superpowers/specs/2026-11-08-data-volume-design.md` (Michael's decisions 1-8).
Sizing from ops ledger item `data-volume-v392` (Michael, 2026-11-15): "room on the PM host for one
system+config snapshot and whatever other storage the host needs; the rest goes to maximize data
storage for the station."

## What already exists (do not rebuild)

- `lib/sigmond/storage/guard.py` (Rob, v3.91): RequiresMountsFor drop-ins from `data_sinks`, and
  `seal_bare_dir()`. `smd apply|update|align --apply` run it.
- `lib/sigmond/archive.py` + `commands/archive.py`: disk listing, RX888-hub placement, format with a
  label, fstab by UUID with `nofail`, mount verification. The `archive` role stays exactly as built.
- `scripts/proxmox/pm-storage.py`: host pool report into the guest, `data_disks()`, and the refusal of
  a whole-VM `qm snapshot` while a data disk is attached.
- `lib/sigmond/storage/client_kit.py`: `Item`, `add_storage_verbs()` (holdings/trim/final for a client).
- hf-timestd `archiver.raw_day_may_leave()` (Rob, 8acc22ae): no raw day leaves before the archive drive
  holds it.

## Global Constraints

- Mount point `/var/lib/sigmond/data`; filesystem label `SIGMOND-DATA`; ext4, `-m 0`.
- fstab volume line: `UUID=<uuid> /var/lib/sigmond/data ext4 nofail,noatime,x-systemd.device-timeout=30s 0 2`.
- fstab bind line per client root: `/var/lib/sigmond/data/<name> /var/lib/<name> none bind,nofail,x-systemd.requires-mounts-for=/var/lib/sigmond/data 0 0`.
  Every data line carries the trailing comment `# sigmond data volume`; tools find their lines by it.
- A client root is the `/var/lib/<name>` directory holding a client's `data_sinks` paths. `/var/lib/sigmond`
  itself never moves (it holds the lock, the sink of older stations' and both mount points).
- Configuration under `/etc` and the software stay on the system disk (decision 1).
- Never stop or restart radiod. A client stop for a move lasts only for the final delta copy (two-pass).
- Never take a `qm snapshot` of a VM with a data disk; protect with `vzdump` of the system disk.
- smd refuses a human `sudo smd`; verbs that need root elevate themselves (`_need_root`).
- Every line smd prints stays ASCII. Nothing above 0x7F in a guest-agent request.
- hf-timestd: do not touch `core/binary_archive_writer.py`, `core/stream_recorder_v2.py`; post the file
  list to the claude-bus before each hf-timestd push. Commit to main in every repo; never force-push;
  never push while the build rig runs a ladder (`ssh b3 'pgrep -fa "ladder-v|build-golden|test-nested|test-update"'`).

## Review Focus

1. **The volume's disk is missing at boot.** No client may write onto the root disk: the binds stay
   unmounted, the sealed bare directories refuse writes, the guarded units do not start, and `smd check`
   FAILs naming the volume. → Task B1 `test_bind_line_requires_volume`, Task B6 `test_check_fails_volume_missing`.
2. **A move interrupted halfway** (power loss, ENOSPC, Ctrl-C between passes). Re-running `add` resumes;
   the old copy stays in `<root>.pre-data` until `--finish`; no client ever starts against a half copy.
   → Task B3 `test_rerun_after_interrupted_copy_resumes`, `test_client_not_started_on_failed_verify`.
3. **A client installed after the volume exists** creates its root on the system disk. `smd check` WARNs
   with the exact `smd storage add --role data --sync` command. → Task B6 `test_check_warns_unbound_root`.
4. **The ledger counts the volume once per bind** and so reports four times the space. Stores collapse by
   device. → Task B5 `test_binds_collapse_into_volume_store`.
5. **The wrong disk offered as data**: the archive drive, a USB drive on the RX888's hub, the disk that
   holds `/`. Refused, or warned for any other USB drive; `--auto` never formats on bare metal.
   → Task B2 `test_pick_refuses_archive_and_root`, `test_auto_never_on_bare_metal`.

---

## Part A — hf-timestd joins the pool (hf-timestd repo)

### Task A1: Declare classes and answer the storage verbs

**Files:**
- Modify: `src/hf_timestd/cli.py` (inventory `data_sinks`, the `storage` verb group)
- Create: `src/hf_timestd/storage_items.py`
- Modify: `deploy.toml` (`contract_version = "0.9"`)
- Test: `tests/unit/test_storage_items.py`

**Interfaces:**
- Produces: `SINK_CLASSES: Dict[str, str]` mapping each top-level entry of the data root to a class;
  `sinks(data_root: Path, rate_bytes_per_day: int) -> List[dict]`;
  `list_items(data_root: Path) -> List[client_kit.Item]`; `delete_item(item, now: float) -> bool`.

Classes, from the KPH1 tree of 2026-11-16: `pinned` = `state`, `authority_history.db*`, `status`;
`raw` = `raw_buffer`; `sent` = `upload`; `derived` = every other entry (`phase2`, `products`, `timing`,
`t3_shadow`, `wwvb`, caches, `archive-status.json`). An entry not in the map counts as `derived`.

- [ ] **Step 1: Failing tests** — `test_classes_from_tree` (tmp tree with the KPH1 names → each class as
  above; an unknown name → derived), `test_sinks_shape` (three file sinks: pinned paths with
  `final_policy` absent, raw `raw_buffer` with `final_policy = "give-up-oldest:raw"` and `floor_bytes =
  2 * rate`, derived+sent sink on the root; every sink has `classes`), `test_raw_items_are_day_dirs`
  (one Item per `raw_buffer/<channel>/<YYYYMMDD>`, bytes summed, mtime = newest file),
  `test_delete_never_today_or_yesterday`, `test_delete_respects_archive_gate` (monkeypatch
  `archiver.raw_day_may_leave` False → `delete_item` returns False, nothing removed),
  `test_pinned_never_listed_for_trim`.
- [ ] **Step 2:** `.venv/bin/python -m pytest --override-ini addopts= tests/unit/test_storage_items.py` → FAIL.
- [ ] **Step 3:** Implement; wire `add_storage_verbs()` from `sigmond.storage.client_kit` under a guarded
  import (no sigmond → the verbs answer `{"unsupported": true}`). The raw sink's rate comes from
  `client_kit.written_per_day(list_items(...), now)`.
- [ ] **Step 4:** PASS, then the full suite. **Step 5:** bus post with the file list; commit
  `storage: hf-timestd declares its classes and answers holdings/trim/final (contract 0.9)`.

### Task A2: The ledger's word replaces the percentage quota

**Files:**
- Create: `src/hf_timestd/ledger_gate.py`
- Modify: `src/hf_timestd/quota_manager.py` (`enforce_quota`), `src/hf_timestd/core/resource_guardian.py`
  (headroom eviction only)
- Test: `tests/unit/test_ledger_gate.py`

**Interfaces:**
- Produces: `ledger_manages_us(status_path=Path("/run/sigmond/storage-status.json"), now=time.time,
  max_age_s=900) -> bool` — True when the file parses, its `run_utc` lies within `max_age_s`, and a
  `clients` row has `client == "hf-timestd"` and `managed` true.

- [ ] **Step 1: Failing tests** — `test_fresh_managed_true`, `test_stale_false` (901 s), `test_unmanaged_row_false`,
  `test_missing_or_bad_json_false`, `test_quota_skips_when_managed` (QuotaManager over threshold, gate True →
  deletes nothing, logs one INFO naming the ledger), `test_quota_unchanged_when_not_managed`,
  `test_guardian_emergency_still_evicts` (gate True, disk ≥ `EVICT_EMERGENCY_PERCENT` (95) → evicts, WARNING).
- [ ] **Step 2:** FAIL. **Step 3:** Implement; the gate is read at each enforcement pass, never cached across
  passes. **Step 4:** PASS + full suite. **Step 5:** bus post; commit
  `storage: the sigmond ledger, when it manages hf-timestd, replaces the percentage quota (95 % emergency kept)`.

---

## Part B — sigmond: `smd storage add|remove --role data`

### Task B1: Pure pieces — roots, fstab lines

**Files:**
- Create: `lib/sigmond/storage/datavol.py`
- Test: `tests/test_datavol.py`

**Interfaces:**
- Produces: `MOUNT = Path("/var/lib/sigmond/data")`, `LABEL = "SIGMOND-DATA"`, `MARK = "# sigmond data volume"`;
  `client_roots(client_paths: Mapping[str, Iterable[str]]) -> Dict[str, List[str]]` (root → clients);
  `outside_roots(client_paths) -> List[str]` (paths not under `/var/lib/<name>`, reported, never moved);
  `volume_line(uuid: str) -> str`; `bind_line(root: str) -> str`;
  `fstab_with_data(text: str, uuid: Optional[str], roots: Iterable[str]) -> str` (`uuid=None` = directory
  volume, binds only); `fstab_without_data(text: str) -> str`; `data_lines(text: str) -> List[str]`.

- [ ] **Step 1: Failing tests** — `test_roots_collapse_to_var_lib_name` (`/var/lib/timestd/raw_buffer` and
  `/var/lib/timestd/state` → `{'/var/lib/timestd': ['hf-timestd']}`), `test_var_lib_sigmond_never_a_root`,
  `test_outside_paths_reported` (`/srv/x` → in `outside_roots`), `test_bind_line_requires_volume` (exact text
  from Global Constraints), `test_fstab_with_data_idempotent` (twice → same text), `test_fstab_keeps_foreign_lines`,
  `test_fstab_without_data_removes_only_marked`, `test_directory_volume_has_no_volume_line`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit `storage: data volume roots and fstab lines`.

### Task B2: Choosing the disk

**Files:** Modify `lib/sigmond/storage/datavol.py`; Modify `lib/sigmond/archive.py` (move the format
sequence `wipefs`/`sfdisk`/`mkfs.ext4` out of `commands/archive.py` into `archive.format_disk(disk, label,
run, dry) -> int`, used by both roles); Test `tests/test_datavol.py`, `tests/test_archive_cmd.py` (unchanged
behaviour).

**Interfaces:**
- Produces: `candidates(disks: Dict[str, Disk], root_disk: str, archive_uuid: Optional[str], rx888: List[str])
  -> List[Tuple[Disk, str]]` (disk, verdict `ok|warn|refuse` with reason);
  `pick_auto(cands, virt: str) -> Optional[Disk]` — exactly one `ok` whole disk with no partitions, and
  `virt != "none"` (a VM); else None.

- [ ] **Step 1: Failing tests** — `test_pick_refuses_archive_and_root`, `test_usb_on_rx888_hub_refused`,
  `test_other_usb_warns`, `test_partitioned_disk_not_auto`, `test_auto_never_on_bare_metal` (virt "none" → None),
  `test_auto_needs_exactly_one`, and `test_archive_add_format_sequence_unchanged` (the archive role still runs
  the same three commands with the same arguments).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS + `tests/test_archive*.py`. **Step 5:** commit
  `storage: pick the data disk; archive and data share one format step`.

### Task B3: `add --role data` — format, two-pass move, bind, guard, start

**Files:** Create `lib/sigmond/commands/storage_data.py`; Modify `bin/smd` (parser: `smd storage add --role
data|archive [--device D] [--auto] [--directory] [--sync] [--finish] [--yes] [--dry-run]`; `--role archive`
hands the arguments to the existing `archive add`); Test `tests/test_storage_data_cmd.py`.

**Interfaces:**
- Consumes: B1, B2, `guard.guard()`, `guard.client_paths()`, `lifecycle` stop/start of a client's units.
- Produces: `cmd_storage_data_add(args, ops: Ops) -> int`, where `Ops` (dataclass of callables: `run`,
  `stop_client`, `start_client`, `units_active`, `statvfs`, `now`) lets tests drive every step.

Sequence (spec "For the data role, add also"): elevate; pick/confirm the disk (typed device name unless
`--yes`); `format_disk`; mount at `MOUNT`; for each root: pass 1 `rsync -aHAX --numeric-ids --delete
<root>/ MOUNT/<name>/` with the clients running; stop the root's clients; pass 2 (same command); verify
(file count and byte total equal between the two trees, `du -sb --apparent-size` and `find | wc -l`);
`mv <root> <root>.pre-data`; `mkdir <root>` (root:root 0755); write fstab (backup `fstab.pre-data-<utc>`);
`daemon-reload`; mount the bind; `guard()` (drop-ins, seal); start the clients. `--directory` makes `MOUNT`
a plain directory on `/` and skips the format and volume line (decision 8). `--sync` skips disk work and
moves only roots not yet bound (a client installed later). Refuse when `MOUNT`'s free space is under the
sum of the roots' sizes plus 10 %.

- [ ] **Step 1: Failing tests** — `test_dry_run_changes_nothing`, `test_two_pass_order` (recorded calls: rsync,
  stop, rsync, verify, mv, mkdir, fstab, mount, guard, start), `test_client_not_started_on_failed_verify`
  (verify mismatch → no `mv`, no bind; the clients restart on their untouched old root; rc 1; the
  message names the mismatch), `test_rerun_after_interrupted_copy_resumes`
  (MOUNT/<name> half full, no `.pre-data` → passes run again, ends bound), `test_refuses_short_space`,
  `test_radiod_never_stopped`, `test_sync_moves_only_unbound`, `test_directory_volume`,
  `test_old_copy_kept`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS + full suite. **Step 5:** commit
  `storage add --role data: one volume, every client's folder bound to its usual path`.

### Task B4: `--finish`, `remove --role data`, `--adopt`

**Files:** Modify `lib/sigmond/commands/storage_data.py`, `bin/smd` (`remove --role data`, `add --adopt`);
Test `tests/test_storage_data_cmd.py`.

- `--finish`: for each bound root with a `<root>.pre-data`, confirm the bind is mounted, then remove the old tree.
- `remove --role data`: stop the clients, copy back to `<root>.from-data` if `/` has room (else refuse naming
  the shortfall in GB), unmount the bind, swap the directories, drop the marked fstab lines, start.
- `--adopt` (KPH1, KPH2, KFS-SE-NW, KFS-OMNI-SW): a disk mounted by its own fstab line directly at one client
  root. Stop that root's clients; unmount; mount the device at `MOUNT`; `mkdir MOUNT/<name>` and `rename()`
  each top-level entry except `lost+found` into it (same filesystem: instant); replace the old fstab line with
  the volume and bind lines; mount; guard; start. Then continue as `add` for the other clients' roots. Refuse
  unless exactly one such mount exists and its fstab line names a UUID.

- [ ] **Step 1: Failing tests** — `test_finish_removes_only_when_bound`, `test_remove_refuses_without_room`,
  `test_remove_round_trip`, `test_adopt_renames_not_copies` (no rsync for the adopted root),
  `test_adopt_refuses_two_mounts`, `test_adopt_keeps_lost_found_at_top`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit
  `storage: --finish, remove --role data, and --adopt for a disk mounted at a client path`.

### Task B5: The ledger sees one volume

**Files:** Modify `lib/sigmond/storage/measure.py` (`discover_stores`); Test `tests/test_storage_measure.py`.

- [ ] **Step 1: Failing tests** — `test_binds_collapse_into_volume_store` (mountinfo with `MOUNT` and two binds
  of the same device → one store named `mnt:/var/lib/sigmond/data`, and `store_for_path('/var/lib/timestd/x')`
  resolves to it), `test_directory_volume_is_root_store` (binds on `/`'s device → the root store).
- [ ] **Step 2:** FAIL. **Step 3:** collapse by mountinfo major:minor; the store keeps the shortest mount path
  of that device. **Step 4:** PASS + storage tests. **Step 5:** commit `storage: a data volume counts once, whatever binds it carries`.

### Task B6: `smd check` and bring-up

**Files:** Modify `lib/sigmond/commands/check.py` (storage line), `lib/sigmond/bringup.py` (`build_plan`);
Test `tests/test_check_storage.py`, `tests/test_bringup_plan.py`.

- check: fstab has data lines and `MOUNT` is not mounted → FAIL `data volume missing: <device or UUID>`;
  a client root with `data_sinks` not bound while a volume exists → WARN `run: smd storage add --role data --sync`;
  a `<root>.pre-data` older than 7 days → WARN `run: smd storage add --role data --finish`.
- bring-up: a first step `data-volume` (`smd storage add --role data --auto --yes`, soft: no candidate is not a
  failure) and, after the clients install and before Stage 4's start, `data-volume-sync` (`... --sync --yes`).
  On bare metal with no candidate and non-interactive: `--directory`.

- [ ] **Step 1: Failing tests** — `test_check_fails_volume_missing`, `test_check_warns_unbound_root`,
  `test_check_warns_old_pre_data`, `test_plan_has_data_volume_first`, `test_plan_sync_before_start`,
  `test_plan_bare_metal_noninteractive_directory`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS + full suite. **Step 5:** commit
  `storage: smd check names the data volume; bring-up creates and fills it`.

### Task B7: Backup of the state the volume carries

**Files:** Modify `lib/sigmond/storage/tick.py` (once per 24 h), Create `lib/sigmond/storage/statecopy.py`;
Test `tests/test_statecopy.py`.

- `state_paths(views) -> List[str]`: every managed client's file sink whose `classes == ["pinned"]`.
- `copy_state(paths, dest=Path("/var/backups/sigmond-state"), run) -> dict`: `rsync -a --delete` each path
  under `dest/<client>/`; SQLite files copied with `sqlite3 <db> ".backup <dest>"` so a live WAL never tears.
- [ ] Tests: `test_only_pinned_sinks`, `test_sqlite_uses_backup_api`, `test_once_per_day`, `test_dest_on_system_disk`
  (refuses a `dest` that resolves under `MOUNT`). FAIL → implement → PASS → commit
  `storage: copy the pinned state to the system disk daily, so the system backup carries it`.

---

## Part C — Proxmox and bare-metal platform

### Task C1: `pm-storage data-disk` for an existing VM

**Files:** Modify `scripts/proxmox/pm-storage.py`; Test `tests/test_pm_storage.py`.

**Interfaces:**
- Produces: `plan_data_disk(pool_bytes: int, others_used: int, scsi0_after: int, headroom: int = 20 * GiB,
  minimum: int = 50 * GiB) -> Optional[int]` (bytes, rounded down to a GiB; None below `minimum`);
  verb `data-disk <vmid> [--size G] [--scsi0-after G (default 30)] [--dry-run]`.

Rule (11-15 sizing): size = pool − the other VMs' real use − scsi0's expected use after the move − headroom.
The verb refuses when `scsi1` exists; prints the overcommit (virtual sizes against the pool) and says that
`fstrim` in the guest after the move returns scsi0's freed blocks; runs
`qm set <vmid> --scsi1 <pool>:<G>,backup=0,discard=on,ssd=1`; when `scsi0` lacks `discard=on` it sets it and
says one VM stop and start applies it (it never stops the VM).

- [ ] **Step 1: Failing tests** — `test_plan_512gb_host` (pool 420 GiB, others 0, scsi0_after 30 → 370 GiB),
  `test_plan_below_minimum_none`, `test_refuses_existing_scsi1`, `test_qm_set_line_exact`,
  `test_scsi0_discard_set_not_restarted`, `test_dry_run_runs_nothing`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit
  `pm-storage data-disk: give an existing decoder VM its data disk, sized from the pool`.

### Task C2: `pm-storage backup` — the system disk, nightly

**Files:** Modify `scripts/proxmox/pm-storage.py`; Create `scripts/proxmox/pm-storage-backup.service`,
`scripts/proxmox/pm-storage-backup.timer`; Modify `scripts/proxmox/pm-align.py` (installs the pair);
Test `tests/test_pm_storage.py`, `tests/test_pm_align.py`.

- `backup <vmid>`: `vzdump <vmid> --mode snapshot --compress zstd --storage local --prune-backups keep-last=1`;
  refuses when a data disk lacks `backup=0`; records `last_system_backup_utc` in the host report it already
  pushes into the guest.
- Timer: `OnCalendar=*-*-* 04:30:00`, `RandomizedDelaySec=1h`, `Persistent=true`.
- Guest: `smd align --apply` on a VM with a data disk prints one WARN when the host report shows no backup
  in 36 h (it does not stop).
- [ ] Tests: `test_backup_argv_exact`, `test_backup_refuses_data_disk_without_backup0`,
  `test_report_carries_last_backup`, `test_pm_align_installs_backup_timer`, `test_align_warns_stale_backup`.
  FAIL → implement → PASS → commit `pm-storage backup: a nightly vzdump of the system disk (data disks stay out)`.

### Task C3: New installs get two disks (sigmond-appliance)

**Files:** Modify `answer.toml.template` (`[disk-setup]` `lvm.maxroot = 40`, `lvm.swapsize = 8`,
`lvm.minfree = 8`), `firstboot-v3.sh` (scsi0 resized to 48 G instead of 75 %/256 G; after the `pristine`
snapshot, `qm set --scsi1` sized as C1's rule with `scsi0_after = 48`, headroom 20 G, minimum 50 G, else
none and one log line); Create `test-data-disk-firstboot.sh` (fake `qm`/`pvesm` on PATH, as the other
`test-*.sh` do).

The `pristine` snapshot must come before `scsi1` exists, so it pins only the system disk.

- [ ] Tests: `scsi0 48G`, `scsi1 after pristine` (order in the fake `qm` log), `512 GB host → scsi1 size`
  (same numbers as C1's `test_plan_512gb_host`), `small host → no scsi1`, `answer.toml carries the three keys`.
  FAIL → implement → PASS → commit `firstboot: a 48 G system disk and a data disk with the rest of the pool`.

---

## Part D — Ship and prove (operations; announce each station step on the bus)

- [ ] Push hf-timestd, sigmond, sigmond-appliance (rig idle check); bus post; tell Rob.
- [ ] Ladder v3.93: golden → image → nested install (check: a fresh install has `scsi1`, every client root
  bound under `/var/lib/sigmond/data`, `smd check` storage ok, `smd storage status` shows one data store
  with hf-timestd managed) → update from v3.92.
- [ ] Bless, then B4 first: `pm-storage data-disk 100` on the host, `smd storage add --role data` in the VM,
  watch a day (T6, uploads, `smd storage status` days held), `--finish`, `fstrim -av` in the guest.
- [ ] The four hand-moved VMs (Rob's go; Phil's sites): `smd storage add --role data --adopt`, one at a time.
- [ ] The rest of the VM fleet, one station at a time on its operator's go; ND (1 TB host) shows the gain first.
- [ ] Bare-metal stations (SIGMA, B1/B2): only on Michael's word; `--directory` changes nothing in capacity.

# Satellite Host Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Proxmox host carries sigmond-link, inert unless its station is a satellite site, and follows the VM's own `[link_schedule]` so turning the guard on in the VM covers the host too, with no hand step.

**Architecture:** `sigmond-link` on a host reads `/etc/sigmond/link.toml`; a new key `follow_vm = <vmid>` makes it read the VM's `/etc/sigmond/topology.toml` `[link_schedule]` through the guest agent, keep the last good copy, and overlay the host's own host-only keys.  First boot installs it on every new host (`install-host-link.sh`, with `follow_vm = 100`); `pm-align` brings existing hosts up to date.  Probe mode needs no download, so a host with no internet still pauses its remote-access tunnels when the gateways stop answering.

**Tech Stack:** Python 3.11 (`bin/sigmond-link`, `scripts/proxmox/pm-align.py`), bash (`install-host-link.sh`, sigmond-appliance `firstboot-v3.sh`), `qm guest exec`, pytest and the appliance's shell tests.

**Spec:** Rob, 2026-11-13: "the host has to be" in the image; the host side of the satellite guard on every install and on existing hosts (AI6VN-1-PM and AI6VN-2-PM have it only by hand since 11-12; ops session report 2026-11-13 04:0xZ).  Existing behaviour and keys: `bin/sigmond-link` docstring (`[link_schedule]`, fail-open rules), `scripts/proxmox/install-host-link.sh`.

## Global Constraints

- Fail open, as sigmond-link already does: no VM answer and no cached copy → the guard is disabled (tunnels run), never "suspended".
- Host-only keys stay the host's even when following the VM: `gated_units`, `rac_units`, `probe_targets`.  Every other `[link_schedule]` key comes from the VM.
- Guest-agent read: `qm guest exec <vmid> --timeout 15 -- cat /etc/sigmond/topology.toml`, at most once per `refresh_hours`, cache `/var/lib/sigmond/link-vm.toml` (root, 0644).  A missing `[link_schedule]` table in the VM means disabled.
- `install-host-link.sh` never overwrites an existing `/etc/sigmond/link.toml`; it adds `follow_vm` only when the file is new and `VMID` is set.
- Repos: sigmond (link, installer, pm-align) and sigmond-appliance (firstboot).  Commit to main; never push during a b3 build.

## Review Focus

1. The VM is stopped or its guest agent is down (every host reboot, every pinning restart): use the cached copy, log one line, never block.  → Task 1 `test_vm_down_uses_cache`.
2. The VM's topology.toml has a TOML error: keep the cache, warn once.  → Task 1 `test_vm_bad_toml_keeps_cache`.
3. A host with two VMs, or whose decoder VM is not 100: `follow_vm` names the vmid; pm-align uses the vmid it already aligns.  → Task 3 `test_pm_align_writes_follow_vm_with_its_vmid`.
4. AI6VN-1-PM / AI6VN-2-PM already hand-installed with their own `link.toml`: pm-align updates the files but leaves link.toml alone.  → Task 3 `test_existing_link_toml_untouched`.
5. First boot with no network: the installer's poppler-utils step fails; install still completes and probe mode works.  → Task 2 `test-host-link-firstboot.sh` offline case.

---

### Task 1: sigmond-link follows the VM

**Files:** Modify `bin/sigmond-link` (`read_settings`); Test `tests/test_link_schedule.py`

**Interfaces — Produces:**
- `vm_link_schedule(vmid: int, run=subprocess.run, cache: Path = Path("/var/lib/sigmond/link-vm.toml"), now: float = None, refresh_s: float = 21600) -> Optional[dict]` — the VM's `[link_schedule]` table (from the VM, else the cache, else None).
- `read_settings(path=None, run=...)`: when the host file holds `follow_vm`, the result is the VM's table overlaid with the host's `gated_units`, `rac_units`, `probe_targets` (when the host file sets them), and `cfg["followed_vm"] = vmid`.

- [ ] **Step 1: failing tests** — `test_follow_vm_takes_vm_schedule` (VM table `enabled=true, mode="probe"` → cfg enabled, probe), `test_host_only_keys_stay_host` (host `rac_units` kept), `test_vm_down_uses_cache`, `test_vm_bad_toml_keeps_cache`, `test_no_vm_no_cache_disabled`, `test_cache_refreshed_only_after_refresh_hours` (two calls inside the window → one `qm` call), `test_status_names_followed_vm` (`sigmond-link status` prints "following VM 100").
- [ ] **Step 2:** FAIL.  **Step 3:** implement (run injected; the `qm` argv exactly as in Global Constraints).  **Step 4:** PASS.  **Step 5:** commit `sigmond-link: a Proxmox host follows its VM's [link_schedule]`.

### Task 2: Installed on every new host at first boot

**Files:** Modify sigmond `scripts/proxmox/install-host-link.sh` (`VMID` env → `follow_vm = $VMID` in a new link.toml; poppler failure stays a warning); sigmond-appliance `firstboot-v3.sh` (finalize, right after the host-apply success branch and before `finalized`: `VMID="$VMID" bash "$SIG/scripts/proxmox/install-host-link.sh" >>"$LOG" 2>&1 || say "finalize: WARNING — satellite guard not installed (see $LOG)"`); Create appliance `test-host-link-firstboot.sh`; sigmond test for the installer (`tests/test_install_host_link.py`, running the script under a fake root via env overrides for `/etc` and `/usr/local/sbin`, add `SIGMOND_ROOT` prefix support to the script if needed).

- [ ] Steps: tests `test_new_link_toml_follows_vm` (VMID=100 → `follow_vm = 100`, `enabled = false` absent or false), `test_existing_link_toml_kept`, `test_poppler_failure_is_a_warning`; appliance `test-host-link-firstboot.sh` (static: the call sits after host-apply's success and before `finalized`, with VMID; an offline-run fixture where apt fails still exits 0) → FAIL → implement → PASS → commit both repos (`install-host-link: follow the VM`; appliance `firstboot: the satellite guard on every host, inert until the VM enables it`).

### Task 3: pm-align brings existing hosts up to date

**Files:** Modify `scripts/proxmox/pm-align.py` (fetch `bin/sigmond-link` and `systemd/sigmond-link{,-rac}.{service,timer}` from sigmond at the release's sigmond commit, as optional files; install `/usr/local/sbin/sigmond-link` 0755 and the units 0644; daemon-reload; enable both timers; write `/etc/sigmond/link.toml` with `follow_vm = <its vmid>` only when absent); Test `tests/test_pm_align.py`

- [ ] Steps: tests `test_pm_align_installs_link_files`, `test_pm_align_writes_follow_vm_with_its_vmid`, `test_existing_link_toml_untouched`, `test_release_without_link_files_is_fine` (404 → absent, like pm-storage) → FAIL → implement → PASS → full sigmond suite vs origin/main → commit `pm-align: carry sigmond-link to existing hosts`.

### Task 4: Prove it (operations, Rob's go)

- [ ] Push (b3 idle).  AI6VN-1-PM: `pm-align --apply` keeps its hand link.toml; add `follow_vm = 100` by hand (Rob's go), `sigmond-link status` shows "following VM 100" and the VM's mode.
- [ ] A non-satellite host (e.g. KFS-MAIN-PM): after pm-align, `sigmond-link status` → disabled, tunnels untouched.
- [ ] Next image: the nested test's host shows link.toml with `follow_vm = 100`; ledger `satellite-host-guard` → pushed / on-stations.

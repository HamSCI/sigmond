# Proxmox host + VM setup

The full DASI2 install is **two phases**:

1. **Host (standalone, before any VM)** — `host-setup.sh`, run as root ON
   a fresh Proxmox host from a sigmond checkout.  Installs the host RAC
   (frpc — the site is remotely supportable from this point on, even with
   no VM), applies the CPU/passthrough base tuning (grub IOMMU +
   isolcpus/nohz_full flags, vfio modules, initramfs), saves the computed
   CPU layout to `/etc/sigmond/host-layout.env`, and reboots once.

   ```bash
   git clone https://github.com/HamSCI/sigmond
   sudo bash sigmond/scripts/proxmox/host-setup.sh [--radiod-count N]
   ```

2. **VM (instantiate + tune)** — clone the golden VM
   (`golden-image.sh clone`), boot it, personalize + site-profile, then
   `bootstrap.sh` inside it binds THIS host's passthrough + CPU pinning
   to the VM (hookscript, `qm set`).  When phase 1 already rebooted the
   host with the base config active, bootstrap detects it
   (`REBOOT_REQUIRED=0`) and skips the disruptive host reboot — only a
   one-time VM power-cycle is needed to bind the passthrough.

`bootstrap.sh` also still works standalone on a legacy single-phase
install (fresh Debian VM, host never prepared): it applies the host base
itself and reboots the host mid-flow, resuming automatically.

## Entry points

Phase 1: `sudo bash scripts/proxmox/host-setup.sh` (on the host).
Phase 2: invoked automatically by the top-level `install.sh` when it
detects a KVM guest and the operator answers "yes" to the "Proxmox
passthrough setup?" prompt — or directly:

```bash
sudo bash scripts/proxmox/bootstrap.sh
```

Re-running on a partially-configured system is safe: bootstrap reads
`/etc/sigmond/install-state.env` and resumes from the last completed
state.

## What runs where

| Script                        | Where it runs            | Purpose                                                |
|-------------------------------|--------------------------|--------------------------------------------------------|
| `host-setup.sh`               | ON the Proxmox host (root) | Phase 1 orchestrator: host RAC + base tuning + reboot. |
| `golden-image.sh`             | ON the Proxmox host (root) | Capture the golden template / clone per-site VMs.     |
| `bootstrap.sh`                | Inside the VM (root)     | Phase 2 orchestrator — binds passthrough/pinning to the VM. |
| `lib.sh`                      | Sourced by bootstrap     | State, SSH, prompt, logging helpers.                   |
| `host-discover.sh`            | scp'd to host (or run locally by host-setup, `--no-vm`) | Detects VMID, USB controllers, IOMMU groups, CPU. |
| `host-apply.sh`               | scp'd to host (or run locally by host-setup) | Base: vfio/grub/initramfs. With VMID: hookscript + qm config. |
| `host-verify.sh`              | scp'd to Proxmox host    | Post-reboot check that vfio-pci took ownership.        |
| `cpu-pin-VMID.sh.template`    | Rendered into /var/lib/vz/snippets/ | Per-vCPU pin + per-pCPU freq pin (min==max) hookscript.|
| `sigmond-install-resume.service` | Installed in /etc/systemd/system/ | Oneshot that resumes bootstrap after reboot.   |

## State file

`/etc/sigmond/install-state.env` (mode 0600). One state value plus all
discovered/operator-supplied parameters. Survives reboots.

## Golden image (capture / clone)

`golden-image.sh` — run ON the Proxmox host as root — captures a prepared
reference VM as a Proxmox template and instantiates per-site VMs from it:

```bash
# inside the reference VM first (strips per-site state, then shut down):
sudo smd admin capture-prep --yes && sudo shutdown -h now

# on the Proxmox host:
./golden-image.sh capture <vmid> --name dasi2-golden   # full clone -> qm template
./golden-image.sh clone <template-id> <site-name>      # per new site
```

`clone` strips the inherited hookscript + hostpci passthrough entries —
those are host-specific and re-created by `bootstrap.sh` from inside the
new VM. First boot of a clone: `smd admin personalize --reset-identity
--yes`, fill `/etc/sigmond/site-profile.toml`, `smd config render`, then
run `bootstrap.sh`. Gate everything with `smd admin readiness`.

## Thin-pool watch (`pm-storage.py`)

On 2026-11-03 four stations' thin pools filled from about 67 % to 100 % in
about 29 hours. An operator's snapshot pinned the old blocks while the
guests kept rewriting their data, and the host journal's warnings at 80, 85,
90 and 95 % went unseen. `pm-storage.py` watches the pool from the host.
It runs as root, stdlib only, from `pm-storage.timer` every five minutes;
`pm-heartbeat-setup.sh` installs and enables it beside the heartbeat.

`pm-storage.py report`, each tick:

- measures each VM's lvmthin pool (`/etc/pve/storage.cfg`, `qm config`,
  `lvs`) and keeps six hours of its use in `/run/sigmond/pm-storage.json`;
- deletes the snapshots sigmond's tools took, once past expiry, and all of
  them at once when the pool reaches 90 %. Every delete runs before any
  guest write, and no timeout cuts one short: a killed `qm delsnapshot`
  leaves the VM locked until `qm unlock`;
- writes each running guest `/run/sigmond/host-pool.json` through the guest
  agent (ASCII only), which the guest's storage ledger reads. A failed write
  gets one agent parser reset and one retry. The writes share a 180 s
  deadline, and a guest past it gets skipped with an alarm;
- logs alarms to the journal (`journalctl -t pm-storage`), one line when an
  episode starts, a reminder every 6 h, one when it clears.

The reaper deletes a snapshot only when its description carries
`sigmond-snapshot expires=<ISO-8601 time with offset>`. It never deletes
`pristine`: the first-boot snapshot carries no label, and the reaper skips
it by name besides. It never deletes an operator's snapshot either. It
names one in an alarm once the snapshot outlives the lifetime a sigmond
snapshot would get on that pool now (see below), or at any age once the
pool reaches 80 %. It names `pristine` only while the pool sits at 80 % or
more. A label it cannot parse makes the snapshot an operator's: kept, and
named in an alarm at once.

To take a snapshot the reaper will clean up:

```bash
pm-storage.py snapshot <vmid> <name> [--hours N]
```

It refuses (exit 3) when the pool sits at 70 % or more, or when the pool's
measured growth would fill it within 2 h. Otherwise the snapshot lives half
the pool's projected time to full, never more than 48 h; `--hours` may
shorten that, never lengthen it.

`pm-heartbeat.py` carries the fullest pool's percent as `pool_pct` in its
`resources` block, which turns INVALID at 95 %.

### Rollout order: set up the PM before the guest's ledger

The guest's storage ledger (`sigmond-storage.timer`) expects a host report
whenever `systemd-detect-virt` names a VM. A missing report, or one older
than 900 s, raises the `host-report` alarm at ERROR on every tick, and the
ledger then counts nothing from the pool. So on a VM station:

1. On the PM, run `pm-heartbeat-setup.sh`, which installs and enables
   `pm-storage.timer`. Check that `/run/sigmond/host-pool.json` appears in
   the guest within five minutes.
2. Only then let the guest's ledger run.

If the PM cannot get `pm-storage` yet, write this in the guest's
`/etc/sigmond/storage.toml` meanwhile:

```toml
[pool]
expect_report = false
```

The ledger then expects no host report and raises no alarm for its
absence, but it also cannot see the pool fill. Remove the line once the
PM reports. A bare-metal station needs neither step: it expects no report.

## Prerequisites

**Step 0 (manual, before phase 1)** — these scripts assume a working
Proxmox VE host:

- BIOS configured per `docs/proxmox/wsprdaemon-proxmox-bios-checklist.md`
  (operator does this once, before any of these scripts run).
- **Proxmox VE installed bare-metal** from the standard ISO (disk,
  network, root password).  No automation for this today; Proxmox 8.2+'s
  automated-installer (`proxmox-auto-install-assistant` / answer.toml)
  is a candidate for zero-touch site kits later.
- `apt update && apt install -y git` on the host (Proxmox's Debian base
  doesn't ship git; the sigmond clone needs it).

**For phase 2:**

- VM present — cloned from the golden template (`golden-image.sh clone`),
  or created fresh in Proxmox with the desired memory and disk for the
  legacy single-phase path.
- Linux user account inside the VM with sudo access (baked into the
  golden image).

## Reference

- `docs/proxmox/wsprdaemon-proxmox-vm-setup.md` — manual procedure these
  scripts automate.
- `docs/proxmox/wsprdaemon-proxmox-cpu-clock-tuning.md` — CPU isolation
  and clock tuning details.
- `docs/proxmox/cpu-pin-VMID.sh.example` — original hand-written example
  that `cpu-pin-VMID.sh.template` is parameterized from.
- `tasks/plan-proxmox-vm-bootstrap.md` — design notes for this flow.

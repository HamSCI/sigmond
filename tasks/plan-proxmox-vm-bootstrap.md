# Plan — Proxmox VM Bootstrap for Sigmond

**Author:** Rob Robinett (AI6VN / W0DAS) + Claude
**Date:** 2026-05-05
**Goal:** A user can `git clone https://github.com/mijahauan/sigmond ~/sigmond && bash ~/sigmond/install.sh` on a fresh Debian 13 VM running under Proxmox, answer one prompt, and end up with a fully configured station — including all host-side PCIe passthrough, CPU isolation, vfio binding, hookscript, and Sigmond itself installed and ready to run `smd install <client>`.

## Non-goals

- BIOS configuration on the Proxmox host (operator does this once, per `docs/proxmox/wsprdaemon-proxmox-bios-checklist.md`).
- Creating the Debian 13 VM via Proxmox (assumed already created).
- Creating the Linux user account inside the VM (operator does this).
- Bare-metal install behavior — must remain **completely unchanged**.

## Detection logic (added to `install.sh`)

The new code is gated entirely behind a virtualization check. Bare-metal users see **zero new output**.

```
state file /etc/sigmond/install-state.env exists  →  --resume mode (covered below)
systemd-detect-virt = none                        →  bare metal; existing flow, no prompt
systemd-detect-virt = kvm  + first run            →  one prompt: "Proxmox passthrough setup? [y/N]"
                                                      default N → existing flow
                                                      y       → exec scripts/proxmox/bootstrap.sh
anything else                                     →  existing flow
```

## State machine

State file: `/etc/sigmond/install-state.env` (key=value, root-readable). Drives `--resume`.

```
PRE_HOST           — initial; haven't talked to host yet
HOST_CONFIGURED    — host config files written; reboot pending
HOST_REBOOTED      — VM came back; host vfio binding verified
SIGMOND_INSTALLED  — install.sh completed inside the VM
DONE               — chrony + NIST stratum-1 servers configured; resume unit removed
```

Each transition is one step. On any error, state remains at the previous value so re-running picks up cleanly.

## Files to create

```
scripts/proxmox/
├── bootstrap.sh                        ← orchestrator; entry point from install.sh
├── lib.sh                              ← shared helpers (state, ssh, logging)
├── host-discover.sh                    ← scp'd to host; emits key=value config
├── host-apply.sh                       ← scp'd to host; writes configs + qm set
├── host-verify.sh                      ← scp'd to host; checks vfio binding post-reboot
├── cpu-pin-VMID.sh.template            ← parameterized version of docs/proxmox/cpu-pin-VMID.sh.example
├── sigmond-install-resume.service      ← systemd oneshot for post-reboot resume
└── README.md                           ← brief operator notes
```

## Files to modify

- `install.sh` — small KVM-detection hook near the top (~10 lines added). No logic changes to existing code.
- `docs/installation-guide.md` — new "Proxmox VM" section at the top, before existing Debian 13 content. Existing content unchanged.
- `README.md` — one-line pointer to the new install path.

## Bootstrap.sh phases (state machine)

### Phase 1: PRE_HOST → HOST_CONFIGURED

Run on the VM as root (re-execs with sudo if not).

1. **Sanity-check** — Debian 13, KVM guest, `ssh` + `scp` present, sudo OK.
2. **Auto-clone `ka9q-python`** sibling if missing. Fixes installation-guide.md bug #2.
3. **Generate SSH keypair** at `/root/.ssh/id_ed25519` if missing.
4. **Prompt once**: Proxmox host (name or IP). Save to state.
5. **`ssh-copy-id root@<host>`** — *the one* time the user sees a host root password prompt.
6. **Verify**: `ssh -o BatchMode=yes root@<host> true`.
7. **scp `host-discover.sh`** → host, run via SSH, capture output (VMID, USB IDs, IOMMU groups, host CPU count, sibling pattern). If multiple VMs and we can't infer (e.g. by SMBIOS), prompt user to pick.
8. **scp `host-apply.sh`** → host, run via SSH:
   - Backup originals to `/root/proxmox-passthrough-backup/`.
   - Write `/etc/default/grub.d/sigmond.cfg` (additive `GRUB_CMDLINE_LINUX_DEFAULT` flags — never touch `/etc/default/grub` itself; Debian/Proxmox sources `grub.d/`).
   - Append `vfio*` to `/etc/modules` if missing.
   - Write `/etc/modprobe.d/vfio.conf` (sigmond-owned; rewrite each run).
   - `update-grub && update-initramfs -u -k all`.
   - Write `/var/lib/vz/snippets/cpu-pin-<VMID>.sh` from template with VMID/RADIOD_CPUS/WORKER_CPUS substituted; chmod +x.
   - `qm set <VMID>` for: machine q35, hostpci0/1, cpu host, boot order=scsi0, onboot 1, affinity range, cores/sockets, hookscript, args (-smp ...,threads=2 -cpu host,topoext=on).
   - Remove existing `usb*` passthrough lines if present.
9. **State → HOST_CONFIGURED**.
10. **Install systemd resume unit** (`scripts/proxmox/sigmond-install-resume.service`) on the VM:
    - oneshot, ExecStart calls `bash <repo>/scripts/proxmox/bootstrap.sh --resume`.
    - WantedBy=multi-user.target.
    - Enabled now (will fire on next boot).
11. **Print** explaining what's about to happen, set state → HOST_REBOOTED-PENDING (anchor name actually `HOST_CONFIGURED`).
12. **`ssh root@<host> 'systemctl reboot'`** — host goes down, VM dies with it.

### Phase 2: VM reboots (because `qm set --onboot 1`)

When the VM comes back up, `sigmond-install-resume.service` fires.

### Phase 3: HOST_CONFIGURED → HOST_REBOOTED → SIGMOND_INSTALLED → DONE

Resume runs `bootstrap.sh --resume`.

1. Read state, branch on it.
2. **HOST_CONFIGURED**:
   - scp `host-verify.sh` → host, run.
   - Verify: `lspci -nnk -s <addr>` shows `Kernel driver in use: vfio-pci`; check no AER/AMD-GPU errors in `journalctl -k`.
   - Verify inside VM: `lsusb -t` shows RX-888 at 5000M.
   - If verification fails: print exact troubleshooting block from `wsprdaemon-proxmox-vm-setup.md`, exit non-zero — leaves state as HOST_CONFIGURED so a re-run can retry verification once the operator fixes something.
   - State → HOST_REBOOTED.
3. **HOST_REBOOTED**:
   - Install/configure chrony with NIST stratum-1 servers per `docs/proxmox/wsprdaemon-proxmox-cpu-clock-tuning.md`.
   - Run the existing `bash <repo>/install.sh`. If it requests a reboot we don't currently know how to detect that (it doesn't today); future-proof by passing through whatever exit code it returns.
   - State → SIGMOND_INSTALLED.
4. **SIGMOND_INSTALLED**:
   - Final cleanup: disable + remove the resume unit, rm the service file.
   - State → DONE.

## Idempotency and error handling

- All host-side writes either own their file (`/etc/modprobe.d/vfio.conf`, `/etc/default/grub.d/sigmond.cfg`, hookscript) or use marker-tagged inserts (`/etc/modules` lines). Re-running rewrites or skips, never duplicates.
- `qm set` is naturally idempotent.
- `ssh-copy-id` skips if the key is already authorized.
- If verification fails, we don't advance state. Operator can re-run `--resume`.
- Hard fail (network gone, etc.) leaves state at the last-good value; re-run resumes cleanly.

## Open questions (will resolve during implementation)

1. **CPU sibling layout** — code optimizes for AMD Ryzen U-series sequential HT pairing (cores 0–1 are siblings, 2–3, etc.). For Intel split pairing we abort with a clear "unsupported, configure manually" message. Track as a follow-up.
2. **VMID detection** — primary path: parse `/sys/class/dmi/id/product_serial` on the VM (Proxmox sets it to a UUID we can match against `/etc/pve/qemu-server/*.conf`). Fallback: `qm list` and prompt if more than one VM.
3. **Memory and disk size** — left untouched; assumes operator already sized the VM for Sigmond's needs.

## Tests / verification

End-to-end test: this very VM (`B4-100`, Debian 13.4, KVM guest) is itself a candidate target. Once committed, we'll need:
- Rob's Proxmox host name/IP and root password (one-time for `ssh-copy-id`).
- Confirmation the host has BIOS configured per the checklist.
- Acceptance that this will reboot the host (everything on it goes down, including this VM).

If those aren't ready, dry-run mode (`--dry-run`) prints all the commands without executing. Useful for review.

## Out of scope / follow-ups

- Intel CPU split-HT support.
- Migration script for existing Sigmond installs already configured under different conventions.
- Web UI / TUI integration with `smd tui` (could surface install state).
- Automatic BIOS-checklist verification (would require IPMI or vendor-specific tooling).

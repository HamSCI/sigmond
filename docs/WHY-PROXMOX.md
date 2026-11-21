# Why Proxmox: a station on bare metal, and a station in a VM

> **Audience:** all
> **Status:** draft
> **Verified against:** sigmond 4006b0a on 2026-11-21 — code and docs (`scripts/proxmox/`, appliance-boundary.md, install-bare-metal.md, operator/do-not-touch.md, the 2026-11-08 data-volume design)
> **Canonical for:** why the appliance runs the station in a Proxmox VM: what a bare-metal station keeps, and what the host adds

Why Proxmox? As with [sigmond itself](WHY-SIGMOND.md), the answer lies in
a difference: a sigmond station installed straight onto a machine's own
Linux, and the same station running as a virtual machine on a Proxmox host.
Both run the same release and the same clients. The host adds a second
layer beneath the station, one that stays up, reachable and in charge
when the station itself does not.

sigmond installs either way ([appliance-boundary.md](contributor/appliance-boundary.md)).
The bare-metal road ([install-bare-metal.md](install-bare-metal.md)) suits an
operator who already runs a Linux machine; W0WWV, B1 and B2 run that way. The
appliance road ([INSTALL.md in sigmond-proxmox](https://github.com/HamSCI/sigmond-proxmox/blob/main/INSTALL.md))
turns a dedicated mini-PC into a Proxmox host with the station in VM 100.

## What a bare-metal station keeps

Everything that makes a station a station runs the same on bare metal:

| It keeps | How |
|---|---|
| The release and its alignment | `smd align` to the same blessed list; it reports `host: none` and moves on |
| Every client and radiod | installed by sigmond, with the same contract |
| CPU care for radiod | guest-side placement and isolation still apply: radiod's FFT on its own core, interrupts steered away |
| Remote access | the station's own tunnel, while its operating system runs |
| The heartbeat and the board | the station reports itself |
| Data on its own volume | on one-disk bare metal, a directory serves as the data volume (data-volume design, 2026-11-08) |

## What it goes without

Each row names what the Proxmox host provides and what a bare-metal station
does without.

**A way in when the station is down.**

| The host provides | Without it |
|---|---|
| A remote-access tunnel of its own, to the host's SSH and the Proxmox web interface, running before any VM exists and while the VM is broken (`host-setup.sh`: "the site is remotely supportable from this point on, even with no VM") | a station whose operating system hangs or will not boot needs someone on site |
| Power control of the station: stop, start, reset the VM from the host, even when the guest is wedged | a hard reset needs hands at the machine |
| Its own heartbeat (`pm-heartbeat`), so the fleet board hears from the site even with the VM down | a silent station looks the same whether its software or its machine failed |

**Undo, and a clean reinstall.**

| The host provides | Without it |
|---|---|
| A snapshot or backup of the system disk before a change, with a one-command undo | an upgrade that goes wrong is repaired forward, by hand |
| A fresh station from the golden image: an identity-clean, gate-checked VM imported on the running host, with the host and the data volume untouched | a reinstall means reinstalling the machine's own Linux |
| The station's data on a second virtual disk, which outlives a reimported VM (data-volume design) | the data shares the reinstall's risk unless a second disk was planned |

**A machine fitted to the radio.** On a Proxmox host the hypervisor shapes
the hardware around radiod before the station's own Linux starts.

| The host provides | Without it |
|---|---|
| Strict one-to-one pinning of each virtual CPU to a physical one, with per-core frequency caps (`cpu-pin-<VMID>.sh` hookscript) | placement lives inside the one operating system, shared with everything else it runs |
| An exclusive slice of the L3 cache for radiod's cores (`sigmond-host-resctrl.sh`) | radiod shares its cache with whatever else runs |
| The VM's own housekeeping threads fenced off radiod's cores, and memory deduplication held off (`radiod-vm-fence.sh`) | nothing to fence: no hypervisor runs beside radiod |
| The RX888's USB controller handed whole to the station, away from the host's own devices | the radio shares the machine's USB with everything else |
| Host interrupts steered away from the station's cores (`sigmond-host-irq-affinity.sh`) | guest-side steering only |

**Watching the machine as well as the station.**

| The host provides | Without it |
|---|---|
| A watch on the host's storage pool: reported into the station, expired sigmond snapshots reaped, an alarm before it fills (`pm-storage`) | the disk is watched only from inside |
| The station's memory sized against the host's, the graphics carve-out included (`pm-memory`) | one operating system decides alone |
| Its own alignment of the host scripts to the release (`pm-align`) | there is no host to align |

## What Proxmox costs

The second layer brings its own failures, and every one of these has stopped
a station:

- **Snapshots pin storage.** A whole-VM snapshot holds every block the
  recorders rewrite; in early November the pre-v3.85 snapshots filled thin
  pools and paused four stations. The data-volume design now protects the
  system disk alone, and `pm-storage` reaps and alarms.
- **The host can take the radio back.** A host kernel or Proxmox upgrade can
  leave the passed-through USB controllers bound to the host, and the VM
  boots with no radio ([do-not-touch.md](operator/do-not-touch.md)).
- **CPU layout is brittle.** Change the VM's core count in the Proxmox web
  interface and the computed pinning no longer fits; the symptom is sample
  loss, seen as gaps.
- **Memory is split.** Host and VM each need their margin; on 2026-11-20
  AB4EJ's VM gave back about 1 GB so its host had room to breathe.
- **Two systems to keep current.** The host has its own release scripts,
  heartbeat and storage watch.
- **More to install.** A Proxmox host needs a dedicated machine, a firmware
  checklist and a USB image; bare metal needs only an existing Linux.

## When to choose which

Choose the Proxmox appliance for a station that has to run unattended far
from anyone who can touch it: the host keeps a way in, a way back and a
watch on the machine when the station cannot help itself. Choose bare metal
for a machine someone already runs and can reach, where a second system adds
more to look after than it saves.

## Read next

- [WHY-SIGMOND.md](WHY-SIGMOND.md): the same question one layer up.
- [contributor/appliance-boundary.md](contributor/appliance-boundary.md) ★: what the image bakes, what first boot does.
- [install-bare-metal.md](install-bare-metal.md): the bare-metal road.
- [operator/do-not-touch.md](operator/do-not-touch.md): what an operator must leave alone on the host and in the VM.
- [proxmox/wsprdaemon-proxmox-vm-setup.md](proxmox/wsprdaemon-proxmox-vm-setup.md): passthrough and CPU setup in detail.

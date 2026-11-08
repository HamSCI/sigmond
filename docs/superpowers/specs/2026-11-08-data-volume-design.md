# One way to give a station more storage, on any platform

**Status:** proposal for discussion, 2026-11-08. Michael Hauan set the direction; Rob Robinett's
data-disk work at KPH and KFS prompted it. Calls the writer made appear marked *(writer's call)*.

## Where this starts

On 11-07 and 11-08 Rob gave KPH1, KFS-SE-NW and KFS-OMNI-SW each a second virtual disk and moved
`/var/lib/timestd` onto it. By hand, on each VM, he:

- added a 240 GB `scsi1` with `backup=0`, `discard=on` and `ssd=1`;
- mounted it at `/var/lib/timestd` through an fstab line marked `nofail`;
- gave the `timestd-*`, `grape-daily` and `hamsci-physics-*` units `RequiresMountsFor` drop-ins,
  so they wait for the disk and refuse to start without it;
- copied the data across and removed the old copy.

The root disks fell to 12–14 GB. The aim: keep the image and the system disk small, and give the
data a life of its own, apart from the software that writes it.

Michael agrees with the direction and asks for two things. First, it should fit the mechanism
we already have for adding storage (`smd archive add`, `tasks/plan-external-storage.md`).
Second, it should work the same on a Proxmox appliance and on a bare-metal install. The way to
get both: take the hardware out of the picture, so that an operator assigns storage the same
way whatever lies underneath.

## What already works

- **The capacity ledger sees the new disk without any change.** `discover_stores()`
  (`lib/sigmond/storage/measure.py`) treats any mount that holds a registered client path as a
  store of its own. `/var/lib/timestd` on `scsi1` appears as `mnt:/var/lib/timestd`, and the
  ledger divides it among the clients that write there.
- **`smd archive add` already does most of the guest-side work.** It finds a newly attached
  disk, checks where it sits on the USB tree, formats it with a label, and mounts it by UUID
  through `nofail` fstab.
- **`pm-storage` already reports the host's thin pool into the guest,** with snapshot lifetimes
  and a host alarm.

## What the hand-made version leaves open

### 1. Snapshots still pin the data disk

Tested 2026-11-08 on the build rig (Proxmox VE 9.1, `local-lvm` thin pool), with a throwaway
VM 990 holding `scsi0` plus `scsi1 ... backup=0,discard=on`. `qm snapshot 990 probe` printed:

    snapshotting 'drive-scsi0' (local-lvm:vm-990-disk-0)
    snapshotting 'drive-scsi1' (local-lvm:vm-990-disk-1)

`backup=0` keeps a disk out of vzdump and nothing else. Any `qm snapshot` taken after the data
disk exists pins the raw ring's blocks on that disk, just as the `pre-v385` snapshots did on
11-03. The separate disk makes backups small; it does nothing for the snapshot failure that
stopped four stations.

So the platform layer has to change how it protects a VM before an align. It should snapshot
the system disk alone, or take a vzdump of it (which honours `backup=0`), but never take a
whole-VM `qm snapshot` while a data disk is attached. *(Writer's call: vzdump of the system
disk, because Proxmox offers no per-disk snapshot, and vzdump gives a restorable copy that pins
nothing in the pool.)* A long-lived snapshot that predates the data disk, such as firstboot's
`pristine`, does not include the data disk and does no harm there. It still pins the system disk.

### 2. A hand-picked unit list decides who waits for the disk

Only the units Rob named carry `RequiresMountsFor`. Any other writer under `/var/lib/timestd`
starts without the disk and writes into the empty mount point on the root disk. That covers
`timestd-events` today, and the planned `timestd-archive` and the CODAR captures tomorrow. A
root disk filled that way gives no warning, because the ledger sees the data volume missing,
not the root filling.

Two guards close this:

- sigmond writes the drop-ins itself. For every client whose declared `data_sinks` lie on the
  data volume, it adds `RequiresMountsFor=<mount>` to that client's units. A new client gets the
  guard by declaring its sinks, as the contract already asks.
- The empty mount point refuses writes. `smd storage add` leaves the bare directory owned by
  root, mode 0000 and immutable (`chattr +i`). Without the disk, a stray writer fails loudly
  instead of quietly filling `/`.

### 3. Learned state now has no backup

`/var/lib/timestd` holds more than raw IQ. On W2NAF it also holds `state/` (the T6 chain-delay
record, the anchor ledger, broadcast calibrations, tier transitions), `authority_history.db`
and the events ssh key. On the KFS and KPH VMs all of that now sits on a disk that no backup
copies.

Michael's rule (below) puts all of a station's data on the data volume, learned state included,
so that it outlives any reflash. That leaves one gap to close: a data volume that dies takes the
station's calibrations with it. The capacity design already names this small, irreplaceable
class (the reserve for state). The system backup should carry a copy of it, a few megabytes, and
leave the bulk data alone.

### 4. Two meanings of "more storage"

`smd archive add` attaches a removable drive as a long-term **archive**. We kept that drive off
the live recording path on purpose: a USB drive gets bumped, unplugged, or powered down with a
hub, and the recorder must not stall when it does. Rob's disk carries the live path, which suits
an internal or virtual disk. Both choices hold for their own kind of drive, but nothing in the
code names the difference yet.

### 5. It lives only on three VMs

The fstab lines and drop-ins exist nowhere in a repo. A reflash loses them, and no other station
gets them.

## The proposal

### Michael's decisions (2026-11-08)

1. **The data volume holds all of the station's data, for every client,** not hf-timestd's
   alone. The system disk holds the software and its configuration.
2. **Align backs up the system and never the data.**
3. **Sizing follows the capacity ledger already built** (`2026-11-04-storage-capacity-design.md`).
   Whatever size the data volume has, the ledger divides it among the enabled clients by each
   one's daily write rate, so every client holds the same number of days. The station then
   reports that it holds D days of data, given its storage and the clients running. No separate
   sizing rule belongs here: a bigger volume simply raises D.
4. **Install creates the data volume.** The Proxmox model provides it as a second virtual disk.
   A bare-metal install uses whatever the machine offers.
5. **Adding an external drive works the same in both modalities,** as `smd archive add` already
   intends, and the ledger counts it as added capacity.

### The idea in one paragraph

Inside a VM, Rob's `scsi1` looks exactly like a SATA disk or a USB drive on a bare-metal
machine: a block device. Only the act of making that device appear depends on the platform.
Everything after that can run the same code everywhere. That splits along the line the
two-layer alignment design (`2026-11-08-two-layer-alignment-design.md`) already draws.

### Platform layer: produce a block device

- **Proxmox appliance, at install.** firstboot creates the VM with two disks: a small system
  disk (`scsi0`) and a data disk (`scsi1 ... backup=0,discard=on,ssd=1`) that takes the space
  the thin pool can give, less the host's headroom *(writer's call)*. Both carry `discard` from
  the start.
- **Proxmox appliance, existing VM.** A host-side helper (*writer's call:*
  `sigmond-setup --data-disk SIZE`) checks that the thin pool can hold the whole disk, then runs
  `qm set <vmid> --scsi1 ...`. It turns on `discard` for `scsi0` if that's missing, which needs
  one VM stop and start, as Rob found at KFS-OMNI.
- **Bare metal, at install.** `install.sh` looks for a disk or partition the operator set
  aside, offers it, and runs the guest step on it. A machine with one disk gets a directory on
  the root filesystem in the same place, so every station has a data volume at the same path
  and the software never asks which kind it has *(writer's call)*.

The platform layer also owns the backup rule: align protects the system disk with a vzdump of
`scsi0` alone (which honours `backup=0`), plus the small state files, and never takes a whole-VM
`qm snapshot` while a data disk is attached. On bare metal the same step copies the system
configuration and the state files.

### Shared layer: one verb, two roles

`smd storage add --role data|archive` grows out of `smd archive add` and keeps its steps (find
the new disk, check placement, confirm, format with a label, mount by UUID with `nofail`). The
role decides the rest.

| | `data` | `archive` |
|---|---|---|
| Holds | all of the station's data, every client, live | copies of finished files |
| Mount | mounted once; each client's folder bound to its own usual path (decision 6) | `/var/lib/sigmond/archive`, as now |
| Disk | internal or virtual; refuses a drive on the RX888's hub, warns on any USB drive | removable drive welcome |
| Writers wait for it | yes: drop-ins generated from `data_sinks` | no: only the archiver touches it |
| When it vanishes | its clients stop, loudly | the archiver waits; nothing else notices |
| Ledger | a store, divided among the clients on it | a store, owned by the archiver |

For the `data` role, `add` also:

1. stops the clients whose data paths move;
2. copies their data directories onto the volume, verifying sizes, and binds each
   client's data path to its place there (decision 6), so no client's configured
   paths change;
3. writes the drop-ins and the guarded empty mount point;
4. restarts the clients, and keeps the old copy until the operator runs
   `smd storage add --finish`, as Rob kept `/var/lib/timestd.old`.

Everything a client writes moves: bulk data and learned state alike. Configuration under
`/etc` and the software stay on the system disk. Each client already tells the ledger which of
its paths hold state (the reserve for state), and the system backup copies exactly those.

`smd storage remove --role data` reverses it: stop, copy back if room allows (or refuse and say
how much is missing), unbind, restart.

### With no data volume

Nothing changes. Bulk data stays on the root disk, as on every station today, and the ledger
treats root as the only guest store. A station never needs a data volume to run.

### The external drive

`smd storage add --role archive` stays as `smd archive add` designed it. On an appliance the
USB controllers pass through to the VM, so a plugged-in drive appears inside the VM just as it
does on bare metal, and the same code handles both. The ledger adds the drive to the station's
capacity. The live recording path never runs through it.

## What it means for the stations Rob already moved

KPH1, KPH2, KFS-SE-NW and KFS-OMNI-SW keep running as they are. When `smd storage add` exists,
an `--adopt` path should recognise a disk mounted by hand at a client path, record it as the
data volume, and write the generated drop-ins. Until then,
two small manual steps would cover the largest risk:

- add `RequiresMountsFor=/var/lib/timestd` to `timestd-events` and any other unit that writes
  there;
- make the bare `/var/lib/timestd` beneath the mount immutable.

The snapshot rule applies to them now. No `qm snapshot` should be taken of those VMs without a
plan to delete it within hours.

## Settled in the second round (Michael, 2026-11-08)

6. **Per-client paths.** Each client keeps its own path (`/var/lib/timestd`, and so on), as at
   KFS. The volume mounts once, and sigmond binds each client's folder on it to that client's
   usual path, so no client's configuration changes.
7. **hf-timestd joins the pool.** It drops its own 80 % quota and takes its allotment from the
   ledger, like every other client.
8. **One-disk bare metal:** a directory on the root filesystem serves as the data volume. No
   repartitioning.

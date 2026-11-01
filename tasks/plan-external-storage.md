# Plan — external storage for looking back at events

**Authors:** Michael Hauan (AC0G) + Claude
**Date:** 2026-11-01
**Status:** draft for Rob's review. No code yet. Michael settled the four open questions on 2026-11-01
("Decisions", at the end).
**Verified against:** sigmond c737638 (`bin/smd` `admin storage`, `docs/operator/day-2.md` §3), hf-timestd
3814c6f (`scripts/prune-old-data.sh`, `paths.py`, `scripts/install.sh`), hs-uploader 2026-10-31; disk and raw
buffer measurements on AC0G-B4 and AC0G-ND, 2026-10-31

## Goal

An operator plugs a USB drive into the station, runs one command, and from then on the station keeps weeks
or months of raw 24 kHz IQ on that drive. Recording never depends on the drive. Unplugging it, losing it, or
filling it costs the archive and nothing else.

## Why

On 2026-10-31 we searched the stations' data for the M6.8 flare of 10-08 and the M6.6 flare of 10-10. Both
showed in every product: FT8 and WSPR counts fell, WWV's 10 MHz SNR at ND dropped 24 dB, the 10 MHz carrier
surged by about 1 Hz, 5 MHz went dark for over an hour. But the full-bandwidth IQ had gone from both B4 and
ND within two days. Only the 10 Hz GRAPE copy on PSWS survived.

The stations cannot simply keep more. The prune timer (`timestd-prune.timer`, hf-timestd
`scripts/prune-old-data.sh`) leaves about yesterday and today. The internal disks, about 251 GB, already ran
62 to 73 % full, with 117 to 131 GB of that in the raw buffer. Each channel writes about 15 GB a day, six
channels about 90 GB (`docs/operator/day-2.md` §3, "Keeping more days"). A month of look-back needs a few
terabytes, and that means a second drive.

## The choice: an archive beside the buffer, not a bigger pool

The first idea added the drive to the storage pool, so the raw buffer would simply grow onto it. We rejected
that. A USB drive gets bumped, unplugged, powered down with a hub, or fails. If the live recording path runs
through it, any of those stalls the recorder or turns the filesystem read-only, and the station stops
measuring time.

So the design keeps two stores apart:

- The **raw buffer** stays on the internal disk, written by the recorder and pruned as now.
- The **archive** lives on the external drive. An archiver copies finished ten-minute files from the buffer
  to the archive, and the archive keeps its own retention.

The recorder never learns the drive exists. When the drive vanishes, the archiver logs it, raises a check,
and waits. The files it missed stay in the buffer until the prune takes them, so a drive that returns within
a day loses nothing.

This also gives the event mode its slack. The buffer holds a day or more, so the archiver can decide after
the fact which hours deserve keeping.

## The operator's side: `smd archive`

`smd admin storage` already exists, but it names a different thing. Its help reads "manage the local sink
backend", and two of its three verbs (`migrate-to-sqlite`, `trim`) work on the sink database, the queue of
spots and records waiting to upload. The third, `tune-timestd`, sizes the raw buffer's prune to the internal
disk. None of them touches a second drive or a long-term copy. Folding the new verbs in beside them would
put two meanings under one word, so the new feature takes its own noun, named for what it keeps
(decision 1):

- `smd archive add` walks the operator through attaching a drive.
- `smd archive status` reports what the archive holds: oldest and newest day, days per channel, the flare
  windows kept, free space, and how many more days fit at the current rate.
- `smd archive keep <start> <length>` marks a window by hand, for an eruption or an earthquake.
- `smd archive remove` stops the archiver and unmounts the drive, so the operator can unplug it safely.

### What `smd archive add` does

1. Lists the block devices present, then asks the operator to plug in the drive and press Enter.
2. Finds the new device by comparing the two lists. Reads its model, size and serial number, and which USB
   hub and controller it sits on (from its sysfs path).
3. Refuses a drive on the RX888's hub. Our recovery for a stalled RX888 power-cycles that hub's port
   (`sigmond-sdr-recover`), and a cut can reach the hub's other ports. Warns, without refusing, when the drive
   shares the RX888's controller: the archive writes about 1 MB/s against the RX888's 2 Gbit/s, so bandwidth
   costs little, but a reset or a power sag on that controller would reach the radio.
4. Shows what it found and asks "use this drive?"
5. Formats it as ext4 with the label `SIGMOND-ARCHIVE`, always (decision 3). Formatting erases the drive,
   so `add` says so plainly and the operator types the device name to confirm. Any other answer stops
   `add` with the drive untouched.
6. Mounts it by UUID at `/var/lib/sigmond/archive`, through an fstab line with
   `nofail,noatime,x-systemd.device-timeout=10s`. A missing drive must never hold up a boot; we have lost
   stations to boot-order waits before.
7. Writes the choice to `/etc/sigmond/archive.toml`: the UUID, the mode (below), and the archive's
   retention.
8. Starts the archiver, and prints how many days the drive will hold at 15 GB per channel per day.

### Where the drive appears

On an appliance station both USB controllers pass through to the decoder VM (`vfio-pci`), and `lsusb` on
the Proxmox host returns nothing. So a drive plugged into any port appears inside the VM, where sigmond
runs. The whole design lives in the VM. A station without passthrough, or a bare-metal one, works the same
way.

## The archiver

- One systemd unit pair in hf-timestd, `timestd-archive.service` and `.timer`, every ten minutes. It runs
  at idle I/O priority and goes into `AFFINITY_UNITS`, so it stays off radiod's cores.
- It copies each closed `.bin.zst` file, and whatever metadata the recorder writes beside it (to confirm
  in phase 2), to `<archive>/<station>/raw_buffer/<channel>/<YYYYMMDD>/`, the same layout as the buffer.
  It verifies each copy's size and checksum before counting it done.
- It writes one small manifest per day with the station's grid, the channel frequencies and the timing tier
  in force, so an archived day still explains itself after the station changes.
- It keeps the archive below a high-water mark, 90 % by default, by deleting the oldest whole day, except
  for the files inside a kept window (below).
- It reports to `smd check` (a new `platform.archive` line) and to the heartbeat: drive present, days held,
  newest day copied, copies behind.

### What to keep (decisions 2 and 4)

The archiver copies **everything**: every channel, every ten minutes. Six channels fill about a terabyte in
10 days, so on a 2 TB drive the rolling archive reaches back about three weeks. A channel list stays
possible in `archive.toml` for an operator with a small drive, but the default keeps all.

On top of the rolling copy, the archiver **queues a kept window for each flare** that matters. When the
rolling copy deletes its oldest day, it leaves the kept windows standing, so a flare outlives the days
around it by months. Kept windows have their own ceiling, a quarter of the drive by default (about 35
two-hour windows per terabyte); past that, the oldest window goes first.

Which flares (decision 4, Michael: "do not record flares that will not strike earth"):

- A flare's X-rays reach the Earth in about eight minutes and fall only on the sunlit half. A station whose
  paths lie in darkness sees nothing of it. So the archiver keeps a window only when, at the flare's peak,
  the station stood in daylight **and** its signals stood exposed: the Sun above the horizon at the
  midpoint of at least one monitored path (WWV, WWVH, BPM), where the signal crosses the D region. GOES
  sees only flares on the Earth-facing side of the Sun, so the list already leaves out the far side.
- The threshold stays at class M1 and above. The window runs from 30 minutes before the flare's start to
  90 minutes after its end: about 7 GB over six channels.
- A coronal mass ejection strikes the Earth one to three days later, if it strikes at all, as a geomagnetic
  storm, and a storm reaches the night side too. So the archiver also keeps the whole UTC day of every
  storm that actually arrived, judged by the observed planetary K index (Kp 5 and above, NOAA's G1). It
  goes by the storm observed, not the forecast, so a CME that misses the Earth costs nothing. A storm day
  costs about 90 GB, and counts against the same ceiling.

The archiver reads the flare list and the K index once an hour. The buffer holds a day or more, so lists
that arrive hours late (NCEI's GOES summaries) still catch every window. Volcanic eruptions and earthquakes
have no such automatic list yet; `smd archive keep` serves for those.

## Who owns what

- **sigmond** owns the drive: finding it, checking its placement, formatting, mounting, `archive.toml`, the
  check and the heartbeat fields.
- **hf-timestd** owns what goes into the archive: the archiver, the modes, the event list. It alone knows
  the raw buffer's layout and what a usable day looks like.
- Later, the client contract could let any client declare a tree worth archiving (the magnetometer's one
  sample a second, say), and a generic archiver in sigmond would serve them all. Not in this plan.

## Non-goals

- Growing the storage pool or the VM's disk onto the drive.
- RAID, or any redundancy across drives. An operator who wants it can build it under the mount point.
- Uploading the archive anywhere. PSWS takes the 10 Hz GRAPE product; the archive stays local, for the
  operator and for anyone who asks the operator for a window.
- Network shares in phase 1. The archiver writes to a mount point, so an NFS or SMB share mounted there
  would work later with no change to the archiver.

## Phases

1. **sigmond, the drive.** `smd archive add|status|keep|remove`, `archive.toml`, the fstab line, the placement
   check against the RX888's hub, `platform.archive` in `smd check` (reports "no archive" as OK), the
   operator doc (`day-2.md` §3 gains the command). Tests with fake sysfs and lsblk trees.
2. **hf-timestd, the archiver.** The rolling copy of all channels, retention, manifests, checksums, the
   heartbeat field.
3. **hf-timestd, kept windows.** The GOES flare list with the daylight test, the observed K index, the
   hand-marked windows, and the ceiling.
4. Later: the client contract's archivable trees; network shares.

### Proof on B4

B4 serves as the test bench. With a spare USB drive on a port away from the RX888's hub:

- Add the drive, run the rolling copy for two days, compare checksums between the buffer and the archive.
- Pull the drive mid-copy. The recorder must not notice; the archiver must report it and resume when the
  drive returns.
- Reboot without the drive. The station must come up as fast as without the feature.
- Fill the archive past its high-water mark and watch the oldest day go.
- Plug the drive into the RX888's hub and confirm `smd archive add` refuses.

Each of these needs Michael's hands at the station, and an announcement on the bus first.

## Related fixes, found while writing this

- `prune-old-data.sh` budgets "~6.7 GB/channel/day"; the measured rate stands near 15.
- `smd admin storage tune-timestd` assumes "~2 GB/channel/day" in its docstring and sizes its policy from
  that.
- hf-timestd's `install.sh` still creates an empty `raw_archive/`, a name the runtime retired in favour of
  `raw_buffer`. The new archive should not reuse that folder, to avoid confusion with the legacy layout.

## Decisions (Michael, 2026-11-01)

1. **Command name.** Share one word only where the concept matches. `smd admin storage` manages the sink
   database, a different thing, so the feature takes its own noun: `smd archive`.
2. **What to keep.** All channels, rolling, with a kept window queued per flare.
3. **Formatting.** Always format the drive to ext4.
4. **Which flares.** "Do not record flares that will not strike earth", then: "keep a flare's peak only
   when station in daylight and exposed." So a flare's window stays only when the station stood in daylight
   at the peak and at least one monitored path's midpoint lay sunlit. The archive also keeps the whole UTC day of every
   storm that arrived (observed Kp 5 and above); Michael confirmed this.

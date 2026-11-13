# The archive drive: health check, archiver, half-precision copies

**Status:** design, 2026-11-12. Rob Robinett took this feature over from Michael Hauan and set the
direction; calls the writer made are marked *(writer's call)*.
**Grows from:** `tasks/plan-external-storage.md` (Michael, 2026-11-01: phases, decisions 1–4) and
hf-timestd `docs/superpowers/specs/2026-11-11-f16-archive-design.md` (Michael, the F16 study and codec).
**Test station:** AI6VN-1.

## Why

An operator should be able to plug a large external drive, typically a 4 TB spinning disk, into the station
and have the station keep weeks of its recordings there. Today the SSD keeps about a day or two of raw
24 kHz WWV/WWVH IQ (about 15 GB per channel per day) before the cleanup removes it, and events worth
studying are gone before anyone asks for them.

Rob has a stock of old drives that saw little use. Their health is hard to judge by eye, so attaching one
should report its health before anything is written to it.

Michael suggested storing the archive in half precision (F16). His study shows F16 with a fixed 2¹² scale
loses nothing that post-analysis can see, and the files take about 40–46 % of the F32 size, so a drive holds
about twice the days.

## What exists (verified 2026-11-12 against sigmond and hf-timestd main)

- **sigmond `smd archive add|status|remove`** (`lib/sigmond/archive.py`, `lib/sigmond/commands/archive.py`).
  `add` finds a newly plugged drive, refuses one below an RX888's hub, refuses a non-USB disk or one with
  anything mounted, asks the operator to type the device name, partitions GPT, formats ext4 with label
  `SIGMOND-ARCHIVE`, mounts by UUID at `/var/lib/sigmond/archive` through fstab
  (`nofail,noatime,x-systemd.device-timeout=10s`), and writes `/etc/sigmond/archive.toml` (uuid, label,
  mount, model, serial, size, `mode = "all"`, `high_water_pct = 90`, `kept_ceiling_pct = 25`). Never yet
  run against a real drive.
- **hf-timestd F16 codec** (`core/iq_codec.py`: `encode_f16`, `decode_iq(raw, dtype, scale)`,
  `bytes_per_sample`). An F16 chunk's sidecar says `"dtype": "complex-f16"` and `"scale": 4096`; every
  hf-timestd reader returns complex64 divided by the scale whichever it finds. The recorder's own
  `[recorder] archive_dtype = "f16"` setting (switching the SSD buffer) exists, defaults off, and waits on
  Michael's B4/ND trial.
- **hf-timestd quota_manager `--archive-root /mnt/timestd-archive`.** The cleanup *moves* raw day folders
  to that path when the folder exists and is writable, and moves instead of deleting when evicting. It does
  not check for a mount, never deletes on the archive, and uses a different path. sigmond's
  `smd timestd-tune-storage` writes the same flag into its drop-in. No station is known to use it.
- **Not built:** the archiver, any drive-health reading, `platform.archive` in `smd check`,
  `smd archive keep`.

## Goals

1. `smd archive add` reports the drive's SMART health and runs its short self-test before it erases the
   drive, and refuses a drive that is failing.
2. `smd archive status` and `smd check` report the drive's health against the reading taken at `add`, and
   flag a drive whose bad-sector counts grow.
3. An archiver in hf-timestd copies every finished raw IQ file from the SSD to the drive, converted to F16,
   verified, and keeps the drive below its high-water mark by deleting the oldest whole day.
4. An archived window can be turned back into F32 files of the original format.
5. Recording never depends on the drive.

## Non-goals

- Changing what is on the SSD. The SSD buffer stays F32 and is pruned as now; switching it to F16 remains
  Michael's B4/ND trial.
- Changing anything that leaves the station. PSWS (GRAPE, from the SSD) and every export stay F32.
- Flare and storm kept windows (plan phase 3) and `smd archive keep`. They follow once the rolling copy
  works on AI6VN-1. `kept_ceiling_pct` stays in `archive.toml`, unread for now.
- Other clients' data (GRAPE products, meteor-scatter, others). Raw IQ first; a general archive later.
- The data-volume design (`2026-11-08-data-volume-design.md`).
- RAID, network shares, uploading the archive.

## Anti-goals

These would count as failure even if the code works:

- A recorder that stalls, slows or errors because the drive is slow, full, unplugged or dying.
- A station that boots slower, or not at all, without its drive.
- Archive files written onto the SSD because the drive was absent and the mount point was a plain folder.
- An F16 file on the drive without dtype and scale beside it.
- A file counted as archived that does not decode to the source's samples within rounding.
- A drive erased after its health check said it was failing, without the operator overriding by name.
- A station that boots an old drive instead of its own system. Found on AI6VN-1, 2026-11-12: at power-on
  the host's firmware sees every USB port (the VM gets the controller only after Proxmox starts), and a
  plugged-in drive that still held a live-USB system booted instead of Proxmox. `add`'s wipe ends that for
  good, but until `add` runs, or when it refuses a drive, the drive can take the boot. The operator doc
  says: plug the drive in while the station runs, and set the firmware to put new boot devices last.
  A drive `add` refuses because of its health should be unplugged.

## Part 1 — drive health (sigmond)

### Reading it

sigmond reads SMART with `smartctl` (smartmontools 7+, JSON output `-j`). smartmontools joins the appliance
image's package list, and `add` installs it with apt when missing *(writer's call)*.

USB-to-SATA bridges vary. `add` tries `-d sat` first, then smartctl's own detection, then the
bridge-specific types smartctl knows (`usbjmicron`, `usbcypress`, `usbprolific`, `usbsunplus`), and uses the
first that returns SMART attributes *(writer's call)*. A drive whose bridge passes nothing gets the verdict
**unknown**, said plainly, and `add` continues to its usual confirmation. The working `-d` type is saved in
`archive.toml` so later readings use it.

**Bridges that block SMART under UAS.** Found on AI6VN-1, 2026-11-12: the Seagate Portable's bridge
(0bc2:2344) answers every `-d` type with "unsupported field in scsi command" while the kernel drives it
with `uas`. In usb-storage (BOT) mode the same bridge passes `-d sat` and the short self-test. So when the
`sat` attempt fails that way on a drive bound to `uas`, `add` says the bridge blocks SMART under UAS and
offers to switch it: it writes `options usb-storage quirks=<vid>:<pid>:u` to
`/etc/modprobe.d/sigmond-archive-usb.conf`, sets the same quirk at runtime
(`/sys/module/usb_storage/parameters/quirks`), unbinds and rebinds the USB device (the drive is not yet
mounted at that point in `add`), waits for the disk to reappear, and reads again *(writer's call: modprobe.d
rather than the kernel command line, so the change stays inside the VM and needs no grub edit)*. BOT is
slower than UAS, which costs nothing at the archive's few MB/s. Declining keeps UAS and the verdict
**unknown**. A failure to read health is never reported as a failing drive.

**Two serial numbers.** A USB enclosure reports its bridge's serial (NACA9A86 on AI6VN-1), while SMART
reports the disk's own (ZDZJHMBB). `archive.toml` keeps both, as `serial` (bridge, as now) and
`disk_serial`; the health baseline keys on `disk_serial`, so a disk moved to another enclosure keeps its
history and a new disk in the same enclosure starts a new one.

**smartd.** Installing smartmontools on Debian enables `smartmontools.service` (smartd). sigmond masks it
*(writer's call)*: the hourly sigmond reading does the job, and smartd's own polling and scheduled tests
would spin the drive up on a schedule sigmond does not control.

**SMR drives.** Many 2.5" portable drives are shingled (SMR; AI6VN-1's is an ST2000LM007). The archive
writes whole files sequentially and deletes whole days, which suits SMR; nothing in the archiver rewrites
files in place. `add` names an SMR drive when the model is a known one, for information only.

Spinning drives report ATA attributes; an NVMe drive in a USB enclosure reports the NVMe health log. Both
are read; the fields below name the ATA attribute and the NVMe equivalent where one exists.

### What `add` shows

Model, serial, capacity, and:

| Reading | ATA attribute | NVMe |
|---|---|---|
| Overall verdict | `smart_status.passed` | `smart_status.passed` / critical warning |
| Power-on hours, power cycles | 9, 12 | power_on_hours, power_cycles |
| Remapped sectors | 5 Reallocated_Sector_Ct | — |
| Sectors waiting to be remapped | 197 Current_Pending_Sector | — |
| Unreadable sectors | 198 Offline_Uncorrectable | media_errors |
| Reported uncorrectable | 187 | — |
| Link (cable) errors | 199 UDMA_CRC_Error_Count | — |
| Temperature | 194 / 190 | temperature |
| Wear | — | percentage_used |
| Self-test log | last results | last results |

### The short self-test

After the reading, and before the erase prompt, `add` starts the drive's short self-test (about 2 minutes)
and waits for it, showing progress. A shelf drive can read clean until something reads from it. `--no-self-test`
skips it. A drive whose bridge cannot run self-tests says so and continues.

### Verdict

- **Fail** — the drive's overall verdict is failed, or pending (197) or unreadable (198 / media errors)
  sectors are above zero, or the short self-test failed. `add` refuses. The operator overrides only with
  `--accept-unhealthy <device>`, naming the device, and still types the device name at the erase prompt.
- **Warn** — remapped sectors above zero, link errors above zero, temperature above 55 °C, power-on hours
  above 43 800 (five years), or NVMe wear above 80 %. `add` lists the reasons and asks before the erase
  prompt *(thresholds are the writer's call)*.
- **Healthy** — `add` goes on to the erase prompt as now.
- **Unknown** — as healthy, with the reason shown.

`--dry-run` reads SMART and reports the verdict, runs no self-test, and changes nothing.

### The baseline and later readings

`add` writes the reading to `/var/lib/sigmond/archive-health.json` (on the system disk, not the drive):
baseline at `add`, plus the latest reading. `smd archive status` takes a fresh reading and shows it beside
the baseline. `smd check` gains `platform.archive`:

- OK: no archive configured; or drive mounted, healthy, archiver current.
- WARN: drive configured but not mounted; remapped sectors grew since baseline; temperature high;
  archiver more than 2 hours behind; drive above high-water with nothing left to delete.
- FAIL: pending or unreadable sectors above zero, or overall verdict failed.

`smd check` reads the cached latest reading, so a check does not wake a spun-down drive. smartctl needs
root, so the reading belongs to sigmond, not the archiver: a root `sigmond-archive-health.timer` (hourly,
installed and enabled by `add`, removed by `remove --forget`) refreshes `archive-health.json` (root-owned,
mode 0644) *(writer's call)*. The archiver never runs smartctl. `archive.toml` is root-owned, mode 0644.

`smd archive test --long` starts the drive's extended self-test (hours on a large drive) and returns;
`status` shows its progress and result.

## Part 2 — the archiver (hf-timestd)

### Unit

`timestd-archive.service` (oneshot) and `timestd-archive.timer` (every 10 minutes, `Persistent=true`), run as
user `timestd`, `IOSchedulingClass=idle`, `Nice=19`. sigmond adds both to its CPU map as `other` so they stay
off radiod's cores. Installed by hf-timestd's `install.sh` with the other units, enabled; it does nothing
until an archive exists.

### When it acts

Each run, in order, stopping quietly when any fails:

1. `/etc/sigmond/archive.toml` exists and parses.
2. Its `mount` path is a mount point (`os.path.ismount`) whose filesystem UUID equals the toml's `uuid`.
3. The drive's archive folder `<mount>/timestd/<station>/` exists, or is created when step 2 holds.

So an absent drive leaves an unmounted mount point that the archiver never writes into. `smd archive add`
already makes the mount point a root-owned directory (0755, `mount_point_problem`); it also makes the bare
directory immutable (`chattr +i`) before mounting, so nothing, root included, writes into it while the drive
is away *(writer's call)*. `add` creates `<mount>/timestd/` on the drive owned by `timestd` *(writer's call:
the drive's top level stays root's; each client gets its own folder, ready for later clients)*.

One archiver per host: hf-timestd keeps one data root per host (`/var/lib/timestd`, read from hf-timestd's
config). `<station>` is the host's short hostname, so a drive moved between stations keeps their days apart;
each `<station>` folder holds a `station.json` with reporter ID, grid and the channel list, written each
run *(writer's call)*. All days, months and "today/yesterday" are UTC, as the folder names are.

Only one run at a time: the oneshot cannot start again while active, and the archiver also holds an
`flock` on `/run/timestd-archive.lock`. A catch-up that outlasts 10 minutes simply runs on.

### What it copies

Every `raw_buffer/<CHANNEL>/<YYYYMMDD>/<start>.bin.zst` that is closed, with its `<start>.json`. A file is
closed when its sidecar exists and both have been unmodified for at least 2 minutes *(writer's call)*. Files
already on the drive (by destination name, with a sidecar) are skipped, so a run after a gap catches up from
whatever the SSD still holds, oldest first.

Destination: `<mount>/timestd/<station>/raw_buffer/<CHANNEL>/<YYYYMMDD>/<start>.bin.zst` plus `.json`, the
same layout as the SSD, so hf-timestd's readers work on the drive by pointing their root at it.

### Converting to F16

With `archive_dtype = "f16"` in `archive.toml` (the default `add` writes), each F32 file is decoded,
encoded with `iq_codec.encode_f16` at scale 4096, and zstd-compressed at the recorder's level. The sidecar
copy is the source's with `dtype = "complex-f16"`, `scale = 4096`, and `converted_from = "complex64"`
added. A source already in F16 is copied byte for byte. With `archive_dtype = "complex64"` every file is
copied byte for byte.

When `encode_f16` returns None (a scaled sample beyond F16's range, a sample above 16 before scaling, which
the study never saw), that file is copied byte for byte as complex64 with its own sidecar, as the recorder's
own fallback does, and counted in the status as `kept_f32`. The sidecar's other fields (`samples_written`,
timing, gaps) stay true after conversion; a converted sidecar drops any `f16_check` field, which belongs to
the recorder's trial.

### Verifying

Each file is written as `.<name>.part`, fsynced, then checked before it is renamed into place, sidecar last:

- **Byte copy:** size and SHA-256 equal the source's.
- **Converted:** the written file is read back from the drive and decoded; its sample count equals the
  source's, and every finite sample's difference from the source is within F16 rounding (relative error at most
  2⁻¹⁰ of the scaled value, plus F16's subnormal step) *(writer's call: per-sample bound, no statistics)*;
  NaN and infinity must sit at the same positions in both.

A file that fails is deleted from the drive and retried next run; three failures on one file log an error
and count toward `platform.archive`'s WARN.

### Making room

Before each run's copies, and again after, if the drive is above `high_water_pct` (90) the archiver deletes
the oldest whole day folder across all channels until it is below, never today's or yesterday's. A drive
still above the mark with nothing left to delete stops copying and reports it.

### When the drive goes away

A read or write error on the drive ends the run, logs once, and leaves the next run to start from step 1.
The recorder never touches the drive, so it cannot notice. Files that age off the SSD while the drive is
away are lost to the archive only.

### Coming back after a replug

Found in the field (AI6VN-1, 2026-11-13): the plain `nofail` fstab line mounts only at boot. A drive pulled
while mounted and plugged back in enumerates fine but stays unmounted, and its ext4 journal needs replaying.

- `add` writes the fstab line with `x-systemd.automount` added (keeping `nofail`,
  `x-systemd.device-timeout=10s`, and pass 2). systemd mounts the drive on the first access after a replug,
  and because the pass is 2, the generated mount runs `systemd-fsck@` (`e2fsck -p`) first, which replays the
  journal after an unclean removal. An `e2fsck -p` that wants a manual repair leaves the drive unmounted, and
  status says so. `add` upgrades an existing line from an earlier `add`.
- Nothing that only reports may touch the mount point, because an access to an absent drive's automount
  blocks for the device timeout. `smd archive status`, `smd check` and the heartbeat read
  `/proc/self/mountinfo` and `blkid` instead. The archiver accesses it (its access is what remounts).
- The anti-goal still holds: the mount point is an autofs mount when the drive is away, never a plain
  directory, and stays `chattr +i` underneath.
- `smd archive status` and `smd check` report three states: mounted; **present but not mounted** (the UUID
  is on a block device but no ext4 is at the mount, e.g. fsck refused) as a WARN with the command to run;
  absent.
- `smd archive eject` (writer's call: a new verb; `remove` forgets the drive) waits for an archiver run in
  progress, unmounts, and prints that the drive can be pulled. The station console shows the same line
  while the drive is mounted.

### Status

Each run writes `/var/lib/timestd/archive-status.json` (on the SSD): time of run, drive present, newest and
oldest day archived, files copied, bytes, files behind, last error, and a refreshed SMART reading at most
`smd archive status` and `smd check` read it, with sigmond's `archive-health.json`.

### Retiring the old move

quota_manager's `--archive-root` path and `/mnt/timestd-archive` default are removed from hf-timestd's units,
`resource_guardian`, `config/help.toml`, and sigmond's `smd timestd-tune-storage` drop-in, so the cleanup only
ever deletes from the SSD. `tune-storage` rewrites existing drop-ins without the flag the next time it runs,
and `smd check` warns on any drop-in that still carries it. A station that has data under
`/mnt/timestd-archive` keeps it untouched; `smd check` reports its size, and the operator decides.

## Part 3 — getting F32 back

`hf-timestd archive export-f32 --from <archive root> --to <dir> --channel <C> --start <UTC> --end <UTC>`
writes F32 `.bin.zst` files and sidecars in the original layout (dtype `complex64`, no scale) from the
archive's files. The existing Digital RF export (`events/window_drf.py`) already reads F16 and writes F32, and
works against the drive's root unchanged.

`smd archive status` shows the commands to run for a window.

## `smd archive status` (whole)

Drive model, serial, mount, UUID; size, used, free; days held (oldest–newest), per channel; F16 or F32;
days that fit at the current rate (measured from the drive's recent days, not the 15 GB constant); archiver
last run, files behind, last error; health now beside baseline; long self-test progress.

## Proof on AI6VN-1

Rob attaches a drive to a USB port on the VM's passed-through controller, away from the RX888's hub, and
announces on the bus first.

1. `add` on the drive: health table, short self-test, verdict, erase, mount, `archive.toml`.
2. Reboot with and without the drive: boot time unchanged, archiver silent without it.
3. The drive moved to the RX888's hub: `add` refuses.
4. Two days of copying: every file on the drive decodes within rounding of its SSD source; the drive holds
   about 40–46 % of the SSD bytes for the same days.
5. The same rounding-error-versus-noise-floor check Michael's trial uses (`hf-timestd archive f16-report`)
   on AI6VN-1's channels.
6. Pull the drive mid-copy: recorder unaffected; archiver reports and resumes on return.
7. Lower `high_water_pct` to force deletion: the oldest day goes, today and yesterday stay.
8. `export-f32` for one hour; GRAPE and Digital RF tools read it.

If Rob plugs in more than one old drive, each gets step 1, to try the health report on drives of different
ages and bridges.

## Field results (AI6VN-1, 2026-11-12 21:46Z)

The first real-drive `add` (Seagate Portable 2 TB on USB 2, controller passed through) worked in 37 s and
found three bugs, fixed in sigmond: `add` searches `/usr/sbin` and `/sbin` for its tools; it creates
`<mount>/timestd` owned by `timestd`; all of its output goes to one stream, in order.

2026-11-13 04:02Z: the drive, pulled while mounted at 23:49Z, came back as `sdb1 SIGMOND-ARCHIVE` but stayed
unmounted until a hand `e2fsck -p` ("recovering journal ... clean") and `mount`. Hence the replug section.

## Who builds what

All of it: Rob's side (sigmond and hf-timestd), committed to main per the repos' rule. hf-timestd is
Michael's active area; changes there are announced on the bus before pushing.

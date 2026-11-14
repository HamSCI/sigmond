# Check: archive

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 5554c6e on 2026-11-13 — whole page re-read; every message, threshold and check id checked against lib/sigmond/commands/check.py. Earlier: sigmond b242233 on 2026-11-13 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the archive check in `smd check` means

Check id: `platform.archive`

## What it looks at

The USB archive drive that `smd archive add` attached, if there is one. The
check reads three files and never the drive itself, so it cannot wake a
sleeping drive or wait on one that is unplugged:

- `/etc/sigmond/archive.toml`: which drive it is;
- `/var/lib/sigmond/archive-health.json`: its SMART health, read hourly;
- `/var/lib/timestd/archive-status.json`: the archiver's last run.

A station with no archive drive shows no archive line, unless a drive that
`smd archive add` prepared earlier is plugged in (see the first message below).

## What healthy looks like

```
ok    archive  drive mounted and healthy; 12 day(s) archived
```

## What each message means

**wait: `an archive drive (/dev/sdb1) is plugged in but not set up on this machine.  run: smd archive add --device sdb re-attaches it and keeps its data`**
Seen after a reinstall: the new system has no `/etc/sigmond/archive.toml`, but the
drive still carries its data. `add` sees the drive's label, erases nothing, and
brings back its settings and health baseline from the copy it keeps on the drive
(`.sigmond/`). To erase it and start over instead, add `--format`.

**wait: `the drive is plugged in but not mounted.  run: sudo mount /var/lib/sigmond/archive`**
Usually after it was pulled out while in use. If the mount fails, run
`sudo e2fsck -p` on its partition first.

**wait: `the drive is not plugged in`**
Archiving waits for it. Plugged back in, it mounts on its own.

**wait: `remapped sectors 0 -> N since it was added`**
The drive is wearing. Copy off anything you need and plan to replace it.

**wait: `the archiver last ran ...`**, **`the archiver hit a drive error`**,
**`N file(s) failed verification three times`**
The archiver is behind or the drive is misbehaving. `smd archive status` shows more.

**wait: `the drive is full with nothing left it may delete`**
Only today's and yesterday's recordings are left on it. Use a larger drive.

**wait: `... still moves raw days with --archive-root`**
An old hf-timestd cleanup drop-in. Run `smd admin storage tune-timestd --apply`.

**FAIL: pending or unreadable sectors, or a failed SMART verdict**
The drive is failing. Copy off what you need and replace it.

## Where to look closer

`smd archive status`: the drive, its health against the reading at `add`,
the days held per channel, and the days that still fit.

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.

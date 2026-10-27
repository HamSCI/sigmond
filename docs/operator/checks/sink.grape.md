# Check: GRAPE

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the GRAPE check in `smd check` means

Check id: `sink.grape`

## What it looks at

GRAPE is a science project that studies the ionosphere by recording
time-station signals. The `hf-timestd` program records raw radio samples
for it, one folder per channel. The check looks for a recently written file in
each channel's folder for today or yesterday (UTC).

## What healthy looks like

```
ok    GRAPE  all 9 channels wrote a file in the last 11 min
```

## What each message means

**`--` not on this station**
`hf-timestd` is not installed. Nothing to do.

**`--` hf-timestd archives no channel on this station**
The configuration archives no channel. Nothing to do.

**FAIL: `hf-timestd has no config at /etc/hf-timestd/timestd-config.toml.  Tell the fleet admin.`**
The settings file is missing. Tell the fleet admin.

**WAIT: `first files due in about N min (hf-timestd started M min ago)`**
The recorder started within the last 12 minutes. Wait.

**FAIL: `no file in 11 min from <channels>.  Tell the fleet admin.`**
The named channels wrote no file in that time. Tell the fleet admin, and name
the channels.

**FAIL: `hf-timestd is <state>.  Fix: smd start hf-timestd`**
The line names the state the service has: `inactive`, `failed` or another word. Run the command the line gives.

**FAIL: `hf-timestd keeps restarting.  Tell the fleet admin.`** and
**`hf-timestd is installed but has no service.  Tell the fleet admin.`**
The service crashes or does not exist. Tell the fleet admin.

**WAIT: `hf-timestd is ...; bring-up has not started it yet`** or
**`...; it starts once the SDR is plugged in`**
See the bring-up check page.

## What to do

Follow the line.

## Where to look closer

The Logs screen in the TUI follows the `hf-timestd` service journal. The
storage check covers the disk.

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.

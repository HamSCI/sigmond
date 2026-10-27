# Check: WSPR

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the WSPR check in `smd check` means

Check id: `decode.wspr`

## What it looks at

WSPR (pronounced "whisper") is a weak-signal mode. Stations send short
beacons, and other stations report hearing them. The `wspr-recorder` service
decodes WSPR from the radio every two minutes. The check adds up the spots
(reports of one heard beacon) its journal logs over the last 10 minutes.

## What healthy looks like

```
ok    WSPR  38 spots in the last 10 min, on 6 bands
```

## What each message means

**`--` not on this station**
`wspr-recorder` is not installed. Nothing to do.

**WAIT: `no spots yet; wspr-recorder started N min ago, wait 12 min`**
The decoder started recently. Wait.

**FAIL: `no WSPR spot in 10 min.  Check the antenna and its cable first; if they are fine, tell the fleet admin.`**
The decoder runs and hears nothing. Check the antenna and cable.

**FAIL: `no WSPR spot in 10 min.  The radio (radiod) is not running; tell the fleet admin.`**
The radio program `radiod` stopped. Tell the fleet admin.

**FAIL: `wspr-recorder is inactive.  Fix: smd start wspr-recorder`** (or another state)
The decoder stopped. Run the command the line gives.

**FAIL: `wspr-recorder keeps restarting.  Tell the fleet admin.`** and
**`wspr-recorder is installed but has no service.  Tell the fleet admin.`**
The service crashes or does not exist. Tell the fleet admin.

**WAIT: `wspr-recorder is ...; bring-up has not started it yet`** or
**`...; it starts once the SDR is plugged in`**
See [platform.bringup](platform.bringup.md). Wait, or plug the SDR in.

## What to do

Follow the line. WSPR hears fewer stations than FT8, so a thin window on a
poor band may pass with few spots, but a window of zero means trouble.

## Where to look closer

Run `smd watch wspr` in the VM. It shows each upload batch to WSPRnet.

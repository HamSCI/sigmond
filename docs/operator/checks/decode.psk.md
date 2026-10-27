# Check: PSK

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the PSK check in `smd check` means

Check id: `decode.psk`

## What it looks at

PSK Reporter collects reports of signals that amateur stations hear. The
`psk-recorder` service decodes FT8 and FT4 (two digital modes) from the radio.
The check counts decoded spots, meaning reports of one heard station, in the
last 15 minutes.

## What healthy looks like

```
ok    PSK  214 FT8 and 31 FT4 spots in the last 15 min
```

## What each message means

**`--` not on this station**
`psk-recorder` is not installed. Nothing to do.

**WAIT: `no spots yet; psk-recorder started N min ago, wait 15 min`**
The decoder started less than 15 minutes ago. Wait.

**FAIL: `no FT8 or FT4 spot in 15 min.  Check the antenna and its cable first; if they are fine, tell the fleet admin.`**
The decoder runs and hears nothing. The antenna or its cable most often cause
this. Check them first.

**FAIL: `no FT8 or FT4 spot in 15 min.  The radio (radiod) is not running; tell the fleet admin.`**
The radio program `radiod` stopped, so the decoder has nothing to decode. Tell
the fleet admin.

**FAIL: `psk-recorder is inactive.  Fix: smd start psk-recorder`** (or another state)
The decoder stopped. Run the command the line gives.

**FAIL: `psk-recorder keeps restarting.  Tell the fleet admin.`** and
**`psk-recorder is installed but has no service.  Tell the fleet admin.`**
The service crashes or does not exist. Tell the fleet admin.

**WAIT: `psk-recorder is ...; bring-up has not started it yet`** or
**`...; it starts once the SDR is plugged in`**
Bring-up has not started this service, or the radio receiver (SDR) was
missing. Wait, or plug the SDR in. See [platform.bringup](platform.bringup.md).

## What to do

Follow the line. A quiet band can empty a 15-minute window, but a working
station on a busy band rarely sees zero.

## Where to look closer

Run `smd watch psk` in the VM for live upload counts. The TUI Activity screen
shows the same tail.

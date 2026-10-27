# Check: meteor scatter

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the meteor scatter check in `smd check` means

Check id: `decode.meteor`

## What it looks at

A meteor leaves a short trail of ionised air that reflects radio for a moment.
The `meteor-scatter` service listens for those bursts and decodes MSK144, a
fast digital mode built for them. The check reads the service's own report
lines from the last 5 minutes.

## What healthy looks like

```
ok    meteor scatter  decoding: 0 MSK144 decodes and 0 spots in the last 5 min
```

Zero decodes still counts as healthy. Meteors come and go. The line passes
as long as the service reports in.

## What each message means

**`--` not on this station**
`meteor-scatter` is not installed. Nothing to do.

**WAIT: `meteor-scatter started N min ago; its first report comes within 5 min`**
The service started recently. Wait.

**FAIL: `meteor-scatter has reported nothing in 5 min.  Tell the fleet admin.`**
The service runs but sends no report. Tell the fleet admin.

**FAIL: `meteor-scatter has reported nothing in 5 min.  The radio (radiod) is not running; tell the fleet admin.`**
The radio program `radiod` is not running (stopped or failed). Tell the fleet admin.

**FAIL: `meteor-scatter is <state>.  Fix: smd start meteor-scatter`**
The line names the state the service has: `inactive`, `failed` or another word. Run the command the line gives.

**FAIL: `meteor-scatter keeps restarting.  Tell the fleet admin.`** and
**`meteor-scatter is installed but has no service.  Tell the fleet admin.`**
The service crashes or does not exist. Tell the fleet admin.

**WAIT: `meteor-scatter is ...; bring-up has not started it yet`** or
**`...; it starts once the SDR is plugged in`**
See the bring-up check page.

## What to do

Follow the line.

## Where to look closer

Run `smd watch meteor` in the VM for a live tail of MSK144 decodes.

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.

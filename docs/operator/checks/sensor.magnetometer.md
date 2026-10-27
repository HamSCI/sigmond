# Check: magnetometer

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the magnetometer check in `smd check` means

Check id: `sensor.magnetometer`

## What it looks at

A magnetometer measures the strength of the Earth's magnetic field. The
`mag-recorder` service reads one through a USB adapter at `/dev/ttyMAG0` and
writes a sample each second. The check looks at the adapter, the service, and
the newest samples.

## What healthy looks like

```
ok    magnetometer  samples arriving, 24.3 C
```

The number is the sensor's temperature. A fresh sample must be under 10
seconds old.

## What each message means

**`--` not on this station**
`mag-recorder` is not installed. Nothing to do.

**`--` no magnetometer on this station**
The `mag-recorder` service stands enabled, but finds no adapter, never
recorded a sample here, and the site profile names no magnetometer. The check
tests that the service is enabled, not that it runs. This station simply has none. Nothing to do.

**FAIL: `no magnetometer adapter at /dev/ttyMAG0.  Check its USB cable.  Fix: sudo systemctl restart mag-recorder`**
The adapter does not show at that path, yet this station once recorded
samples or its site profile names a magnetometer. Do what the line says: check
its USB cable, then run the Fix.

**WAIT: `mag-recorder just started; its first samples come within a minute`**
The service started less than a minute ago. Wait a minute.

**FAIL: `no sample today.  Fix: ...` or `no sample for N s.  Fix: ...`**
The service writes no sample, or stopped writing. Run the Fix.

**FAIL: `frozen: 0.00 C, no change in 60 s.  Fix: ...`** and
**`frozen: x, y and z did not change in 60 s.  Fix: ...`**
Samples arrive, but the values stand still: either the temperature reads
exactly 0.00 C, or x, y and z never change. Run the Fix.

**FAIL: `mag-recorder is <state>.  Fix: smd start mag-recorder`**
The line names the state the service has: `inactive`, `failed` or another word. Run the command the line gives.

**FAIL: `mag-recorder keeps restarting.  Tell the fleet admin.`** and
**`mag-recorder is installed but has no service.  Tell the fleet admin.`**
The service crashes or does not exist. Tell the fleet admin.

**WAIT: `mag-recorder is ...; bring-up has not started it yet`**
First-run bring-up has not reached this service. Wait. See
platform.bringup.

## What to do

Do what the line says: run its Fix, or tell the fleet admin.

## Where to look closer

Run `smd watch mag` in the VM for a live view of the samples. The Logs screen
in the TUI follows the service journal.

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.

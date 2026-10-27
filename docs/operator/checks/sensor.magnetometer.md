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
The service runs, finds no adapter, never recorded a sample here, and the site
profile names no magnetometer. This station simply has none. Nothing to do.

**FAIL: `no magnetometer adapter at /dev/ttyMAG0.  Check its USB cable.  Fix: sudo systemctl restart mag-recorder`**
The adapter does not show on the USB bus, yet this station once had one or
declares one. Check the cable, then run the Fix.

**WAIT: `mag-recorder just started; its first samples come within a minute`**
The service started less than a minute ago. Wait a minute.

**FAIL: `no sample today.  Fix: ...` or `no sample for N s.  Fix: ...`**
The service runs but writes nothing, or stopped writing. Run the Fix. If the
sample age keeps growing, check the USB cable and tell the fleet admin.

**FAIL: `frozen: 0.00 C, no change in 60 s.  Fix: ...`** and
**`frozen: x, y and z did not change in 60 s.  Fix: ...`**
Samples arrive, but the values stand still. The sensor stopped reading, even
though the files look fresh. Unplug and replug the adapter, then run the Fix.

**FAIL: `mag-recorder is inactive.  Fix: smd start mag-recorder`** (or another
state in place of `inactive`)
The service stopped. Run the command the line gives.

**FAIL: `mag-recorder keeps restarting.  Tell the fleet admin.`** and
**`mag-recorder is installed but has no service.  Tell the fleet admin.`**
The service crashes or does not exist. Tell the fleet admin.

**WAIT: `mag-recorder is ...; bring-up has not started it yet`**
First-run bring-up has not reached this service. Wait. See
[platform.bringup](platform.bringup.md).

## What to do

Check the USB cable first. Then run the Fix on the line. Tell the fleet admin
if the line stays red.

## Where to look closer

Run `smd watch mag` in the VM for a live view of the samples. The TUI Logs
screen tails the same service.

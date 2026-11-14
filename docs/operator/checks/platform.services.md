# Check: services

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond a00d977 on 2026-11-14 — code (lib/sigmond/commands/check.py, lib/sigmond/service_events.py)
> **Canonical for:** what the services check in `smd check` means

Check id: `platform.services`

## What it looks at

systemd restarts a service that crashes or is killed, so a station can heal itself many times a
day without anyone noticing.  Every 5 minutes, `sigmond-service-events.timer` reads the system
journal and counts, for each service over the last 24 hours: automatic restarts, restarts and
stops done by hand, kills when a service reached its own memory cap, and kills when the whole
machine ran out of memory.  It also reads each service's highest memory use against its cap.
The check reads that summary (`/run/sigmond/service-events.json`), never the journal itself.

## What healthy looks like

```
ok    services  no restarts or memory kills in 24 h (2 manual restarts)
```

Restarts by hand (`smd restart`, an update) are counted but never count against the station.

## What each message means

**FAIL: `the machine ran out of memory N time(s) in 24 h; killed: ...`**
The kernel killed programs to free memory.  Anything can be killed this way, including ssh and
radiod.  Tell the fleet admin: the station needs more memory or fewer services.

**wait: `<service>: N memory kill(s) at its cap`**
The service reached its memory limit and was restarted.  Its data has gaps at each kill.

**wait: `<service>: N automatic restarts`**
More than 3 crashes in 24 hours.  `journalctl -u <service>` (with sudo) shows why.

**wait: `<service>: memory peak X of Y MB (Z %) since it last started`**
Since the service last started, it has come within 15 % of its memory cap and is at risk of
being killed.  The peak includes file cache charged to the service, and it only resets when the
service restarts.

**wait: `restarts and memory kills not collected yet`** or **`could not be read`**
The timer has not run since boot, or the journal could not be read.  It runs every 5 minutes.

## Where to look closer

`smd status` lists each service with events: automatic and manual restarts, memory kills, the
last event, and memory peak against cap.  `smd admin service-events show` prints the whole summary.

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.

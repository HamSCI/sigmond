# Check: topology

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond eeaed74+drift on 2026-11-10 — code (lib/sigmond/commands/check.py, lib/sigmond/topology_drift.py)
> **Canonical for:** what the topology check in `smd check` means

Check id: `platform.topology`

## What it looks at

The file `/etc/sigmond/topology.toml` lists the clients you set up to run on
this station. The check compares that list with the clients that actually run.

## What healthy looks like

```
ok    topology  every running client is enabled, and every enabled one runs
```

## What each message means

**FAIL: `<client> runs but topology does not enable it, so its uploads do not render.  Fix: smd enable <client>`**
The client runs, but the list leaves it out. sigmond follows the list, so it
sends none of that client's data and a plain `smd restart` skips the client.

**FAIL: `<client> enabled in topology but not running.  Fix: smd start <client>`**
The list names the client, it is installed, and none of its services runs.
Clients that have their own line in `smd check` (PSK, WSPR and others) report
there instead.

**WAIT: `<client> enabled, not running; bring-up has not started it yet`**
The first-run bring-up still runs, or waits for the radio. Give it time.

## What to do

Run the command the line gives. If the client should not run here, stop it
with `smd disable <client>` instead.

## Where to look closer

`smd status` lists the same disagreements under "action needed".

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.

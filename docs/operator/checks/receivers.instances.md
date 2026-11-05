# Check: receivers

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 12ae65d on 2026-11-05 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the receivers check in `smd check` means

Check id: `receivers.instances`

## What it looks at

A station can list several receivers in `/etc/sigmond/site-profile.toml`.
Each `[[receiver]]` block names a reporter ID and the radiod it listens to.
Each receiver runs one instance of each recorder it names, or of each
reporter-keyed recorder installed here when it names none. The check counts, per
receiver, how many of those instances run.

## What healthy looks like

```
ok    receivers  AC0G/B1A <- ac0g-b1-a: 2/2 active; AC0G/B1B <- ac0g-b1-b: 2/2 active
```

## What each message means

**No line at all**
This station lists no receivers, so the line does not appear.

**`--` no recorder installed for the listed receivers**
None of the recorders a receiver would run is installed. Nothing to do.

**WAIT: `AC0G/B1B <- ac0g-b1-b: 1/2 active (psk-recorder down)`**
Some instances of a receiver do not run. The line names them.

**FAIL: `AC0G/B1B <- ac0g-b1-b: 0/2 active (...)`**
A receiver has no running instance. A missing unit counts as down.

**FAIL: `the [[receiver]] list does not load: ...`**
The profile has an error. Fix the block the message names.

## What to do

Start the instance the line names with `smd start`, or tell the fleet admin.

## Where to look closer

Run `smd admin instance list`. It shows the radiod each instance uses.

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.

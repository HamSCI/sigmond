# Check: drift

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 4049e6b+drift-report on 2026-11-18 — code (lib/sigmond/commands/check.py, lib/sigmond/drift/)
> **Canonical for:** what the drift check in `smd check` means

Check id: `platform.drift`

## What it looks at

A daily timer writes the drift report to `/var/lib/sigmond/drift.json`. The
report lists every way this station differs from its sigmond release, in six
kinds: source, build, unit, override, stray and host. Each item counts as a
fault or as drift, and carries a one-line fix. The check reads that file
only; `smd align` prints the full report.

An item someone accepted with `smd align --accept` moves out of the counts
and shows as accepted until its exception ends.

## What healthy looks like

```
ok    drift    nothing differs from blessed v3.90
```

## What each message means

**ok: `no faults; <n> drift (<counts by kind>).  run: smd align lists them`**
The station differs from its release in ways that need no action now. The
report names each difference and the command that removes it.

**FAIL: `<n> faults, <m> drift; <kind> <subject>: <detail>.  Fix: <command>`**
At least one difference breaks something or will. The line names the first
fault and its fix. `smd align` lists the rest.

**wait: `drift report stale (written <time>, <n> h ago); the daily timer has not run.  run: smd align`**
The newest report holds data older than 36 hours. `smd align` prints a
current one; the timer writes the file again on its next run.

**wait: `/var/lib/sigmond/drift.json does not parse.  run: smd align`**
Something damaged the file. The next timer run replaces it.

**--: `no drift report yet.  run: smd align (prints one now)`**
The timer has not written a report on this station yet.

Any line may add `<n> accepted (<t> tests)`, the count of differences someone
accepted, and `<n> tests to restore soon`, the temporary tests that end
within a day.

## What to do

Run the fix the line gives. If the difference should stay, accept it with a
reason: `smd align --accept <kind:subject> --reason "<why>"`. Add `--test`
for a trial you mean to undo; it lasts 7 days unless `--until` says
otherwise.

## Where to look closer

`smd align` prints every item, the accepted ones and their remaining days.
`smd align --json` prints the same report for a script.

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.

# Check: storage

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the storage check in `smd check` means

Check id: `platform.storage`

## What it looks at

The station keeps its recordings and logs under `/var/lib`. The check reads
how much of that disk stays free.

## What healthy looks like

```
ok    storage  /var/lib has 62 % free
```

Any figure of 10 % or more passes.

## What each message means

**FAIL: `/var/lib has only N % free.  Tell the fleet admin.`**
Less than 10 % of the disk remains. A full disk stops the recorders and loses
data, so act before it reaches zero.

## What to do

Tell the fleet admin, as the line says.

## Where to look closer

Open the Resources screen in the TUI (`smd tui`). It lists the filesystems
and the sigmond data folders.

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.

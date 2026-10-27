# Check: station pages

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the station pages check in `smd check` means

Check id: `platform.station-pages`

## What it looks at

The station pages (`station-web`) give the station its own web site: status,
this check list, and the documentation. The check asks
`http://127.0.0.1:8000/` inside the VM and waits up to 3 seconds for status 200.

## What healthy looks like

```
ok    station pages  http://127.0.0.1:8000 answers
```

## What each message means

**FAIL: `http://127.0.0.1:8000 does not answer: <reason>`**
The page server refused or timed out. The reason names the cause.

**FAIL: `http://127.0.0.1:8000 does not answer: status N`**
The server answered with an error status instead of 200.

## What to do

Tell the fleet admin. This explanation reaches you only through these pages,
so run `smd check` in the VM to see the line, and send it on.

## Where to look closer

The TUI Logs screen tails the `station-web` service.

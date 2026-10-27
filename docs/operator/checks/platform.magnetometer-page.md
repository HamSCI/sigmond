# Check: magnetometer page

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the magnetometer page check in `smd check` means

Check id: `platform.magnetometer-page`

## What it looks at

The magnetometer page (`gmag-webui`) draws the magnetometer's readings in a
browser. The check asks `http://127.0.0.1:8082/` inside the VM and waits up to
3 seconds for status 200.

The line appears only when the station has a magnetometer. A station without
one owes no answer, so `smd check` prints no line.

## What healthy looks like

```
ok    magnetometer page  http://127.0.0.1:8082 answers
```

## What each message means

**FAIL: `http://127.0.0.1:8082 does not answer: <reason>`**
The page server refused or timed out. The reason names the cause.

**FAIL: `http://127.0.0.1:8082 does not answer: status N`**
The server answered with any answer other than 200 OK.

## What to do

Tell the fleet admin. Read the magnetometer check page too; if that line
fails as well, deal with it first.

## Where to look closer

Run `smd watch mag` in the VM to see the recorder at work. `smd status` shows
the web service's state.

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.

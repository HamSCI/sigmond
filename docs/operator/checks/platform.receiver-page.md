# Check: receiver page

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the receiver page check in `smd check` means

Check id: `platform.receiver-page`

## What it looks at

The receiver page (`ka9q-web`) shows the radio's spectrum in a browser. The
check asks `http://127.0.0.1:8081/` inside the VM and waits up to 3 seconds
for status 200.

## What healthy looks like

```
ok    receiver page  http://127.0.0.1:8081 answers
```

## What each message means

**FAIL: `http://127.0.0.1:8081 does not answer: <reason>`**
The page server refused or timed out. The reason names the cause, for example
"Connection refused".

**FAIL: `http://127.0.0.1:8081 does not answer: status N`**
The server answered, but with any answer other than 200 OK.

## What to do

Tell the fleet admin.

## Where to look closer

Run `smd status` in the VM, or open the Overview screen in the TUI
(`smd tui`). Both show the state of the `ka9q-web` service.

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.

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
"Connection refused". The `ka9q-web` service stopped or stalled.

**FAIL: `http://127.0.0.1:8081 does not answer: status N`**
The server answered, but with an error status instead of 200.

## What to do

Tell the fleet admin. If the decoders pass, the data keeps flowing and only
the page has failed.

## Where to look closer

The TUI Logs screen tails `ka9q-web`. Run `smd watch ka9q` in the VM for the
live view.

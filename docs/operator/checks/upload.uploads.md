# Check: uploads

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** what the uploads check in `smd check` means

Check id: `upload.uploads`

## What it looks at

The `hs-uploader` service sends the station's data to the science networks.
A site-wide switch says whether it sends, holds, or discards. The check reads
that switch, the uploader's backlog, and the time of the last confirmed
delivery. A confirmed delivery, an "ack", means the far end accepted the data.

## What healthy looks like

```
ok    uploads  on; last delivery 4 min ago, no failure in 24 h
```

## What each message means

**WAIT: `off, new station: check the lines above, then run: smd sink upload`**
A new station starts with uploads off, so bad data cannot reach science. When
the other lines look right, run `smd sink upload` in the VM.

**FAIL: `off (<reason>).  Ask the fleet admin.`**
Someone turned uploads off, and the reason follows. Ask the fleet admin.

**FAIL: `hold (<reason>).  Ask the fleet admin.`**
Someone holds uploads back. Ask the fleet admin.

**FAIL: `cannot read the upload backlog: <reason>`**
The check cannot open the uploader's database. The reason names the cause. Tell
the fleet admin.

**FAIL: `N uploads failed for good in the last 24 h (<pipelines>).  Tell the fleet admin.`**
The far end refused these uploads, and the uploader gave up on them. Tell the
fleet admin, and name the pipelines.

**WAIT: `on since N min ago; the first delivery comes within 30 min`**
Uploads switched on recently. Wait.

**FAIL: `on, but nothing delivered in 30 min.  Tell the fleet admin.`**
Uploads run in principle, yet the far end confirmed nothing for half an hour.
Tell the fleet admin.

## What to do

Follow the line.

## Where to look closer

Run `smd watch uploads` in the VM for live upload events.

## Two further FAIL lines

Any check can print one of two generic FAIL lines. See "When a check cannot
run" in the checks README.

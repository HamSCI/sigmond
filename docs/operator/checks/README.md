# What each `smd check` line means

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond f3b0c6e on 2026-11-18 — whole page re-read; marks, check ids, page names and the two generic FAIL texts checked against lib/sigmond/commands/check.py. Earlier: sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** the explanation pages behind the lines of `smd check`

`smd check` prints one line per thing it looks at. Each line carries a mark:
`ok` (healthy), `wait` (give it time), `FAIL` (needs action) or `--` (not on
this station). The station pages link each line to the page below that
explains it. Each page has the file name `<check id>.md`. Each page says what the check looks at, what healthy looks like,
what every warning or failure means, and what to do.

Run `smd check` in the VM. Commands on these pages run in the VM.

| Check id | Line | Page |
|---|---|---|
| `platform.bringup` | bring-up | platform.bringup.md |
| `platform.storage` | storage | platform.storage.md |
| `platform.archive` | archive | platform.archive.md |
| `platform.services` | services | platform.services.md |
| `platform.topology` | topology | platform.topology.md |
| `sensor.magnetometer` | magnetometer | sensor.magnetometer.md |
| `decode.psk` | PSK | decode.psk.md |
| `decode.wspr` | WSPR | decode.wspr.md |
| `receivers.instances` | receivers | receivers.instances.md |
| `decode.meteor` | meteor scatter | decode.meteor.md |
| `sink.grape` | GRAPE | sink.grape.md |
| `upload.uploads` | uploads | upload.uploads.md |
| `platform.receiver-page` | receiver page | platform.receiver-page.md |
| `platform.station-pages` | station pages | platform.station-pages.md |
| `platform.magnetometer-page` | magnetometer page | platform.magnetometer-page.md |

## On a radiod-only host

A host with the radiod-only role prints a different list. It shows one line
per radiod, labelled `radiod <id>`, and no decode, upload or page lines. Each
line names the first part that fails: the card missing from the USB bus, the
unit not active, a status name that does not resolve, a radiod that publishes
with `ttl 0` or on `lo` (fix: `smd admin radiod publish`), or sample loss in
the journal. The operator guide's radiod-only page, docs/operator/radiod-only.md, explains the role.

## When a check cannot run

Any line can show one of two generic FAIL texts. They mean the check could
not do its work, not that the thing it watches has failed.

**FAIL: `cannot read <path> as <user>`**
The check needed a file or the journal, and the operating system refused the
account running `smd check`, named in the message. `smd check` then tried
`sudo -n`, and that refused too. Tell the fleet admin, and give the path and
the user from the line.

**FAIL: `the check itself failed: <Type>: <message>`**
The check hit an unexpected error and stopped. The line shows the error type
and its message. It may point to a fault in sigmond or in the data it read. Tell
the fleet admin, and give the whole line. The other lines still run.

Neither text comes with a Fix.

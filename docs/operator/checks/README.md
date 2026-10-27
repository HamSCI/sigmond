# What each `smd check` line means

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
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
| `sensor.magnetometer` | magnetometer | sensor.magnetometer.md |
| `decode.psk` | PSK | decode.psk.md |
| `decode.wspr` | WSPR | decode.wspr.md |
| `decode.meteor` | meteor scatter | decode.meteor.md |
| `sink.grape` | GRAPE | sink.grape.md |
| `upload.uploads` | uploads | upload.uploads.md |
| `platform.receiver-page` | receiver page | platform.receiver-page.md |
| `platform.station-pages` | station pages | platform.station-pages.md |
| `platform.magnetometer-page` | magnetometer page | platform.magnetometer-page.md |

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

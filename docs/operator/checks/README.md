# What each `smd check` line means

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 783f674 on 2026-10-27 — code (lib/sigmond/commands/check.py)
> **Canonical for:** the explanation pages behind the lines of `smd check`

`smd check` prints one line per thing it looks at. Each line carries a mark:
`ok` (healthy), `wait` (give it time), `FAIL` (needs action) or `--` (not on
this station). The station pages link each line to the page below that
explains it. Each page says what the check looks at, what healthy looks like,
what every warning or failure means, and what to do.

Run `smd check` in the VM. Commands on these pages run in the VM.

| Check id | Line | Page |
|---|---|---|
| `platform.bringup` | bring-up | [platform.bringup.md](platform.bringup.md) |
| `platform.storage` | storage | [platform.storage.md](platform.storage.md) |
| `sensor.magnetometer` | magnetometer | [sensor.magnetometer.md](sensor.magnetometer.md) |
| `decode.psk` | PSK | [decode.psk.md](decode.psk.md) |
| `decode.wspr` | WSPR | [decode.wspr.md](decode.wspr.md) |
| `decode.meteor` | meteor scatter | [decode.meteor.md](decode.meteor.md) |
| `sink.grape` | GRAPE | [sink.grape.md](sink.grape.md) |
| `upload.uploads` | uploads | [upload.uploads.md](upload.uploads.md) |
| `platform.receiver-page` | receiver page | [platform.receiver-page.md](platform.receiver-page.md) |
| `platform.station-pages` | station pages | [platform.station-pages.md](platform.station-pages.md) |
| `platform.magnetometer-page` | magnetometer page | [platform.magnetometer-page.md](platform.magnetometer-page.md) |

Unfamiliar words are in the [glossary](../glossary.md).

# The components of a sigmond station

> **Audience:** all
> **Status:** current (moved from the root README 2026-11-21, not re-verified)
> **Verified against:** sigmond 4006b0a on 2026-11-21 — docs (text moved verbatim from README.md)
> **Canonical for:** the list of suite components and where each one's documentation lives

The top-level view of the suite: every client, library and service a
station can run, each with its own repository and its own documentation.
This page began as the root README's "Available clients" (2026-11-21);
it is to grow into a table generated from the catalog and the release list
and checked in CI (ledger docs-organization, step 2).

## The clients

Clients fall into two groups. **Core clients** are the DASI2 grant
station's primary instruments — they install and run by default (the
`dasi2` profile: hf-timestd, wspr-recorder, psk-recorder, mag-recorder,
meteor-scatter, hamsci-physics, hfdl-recorder, codar-sounder, plus the
gmag-webui and station-web pages).
**Additional clients** extend the same station to other HF science and are
installed **at your discretion** — either all at once during guided
bring-up (`smd bringup dasi2 --with-optional`) or one at a time
(`smd install <name>`, which installs *and* enables it). See
[Adding a discretionary client later](COMMANDS.md#adding-a-discretionary-client-later).

| Core client | What it does | Repo |
|--------|-------------|------|
| **radiod** | ka9q-radio SDR daemon — receives RF and multicasts IQ channels | [ka9q/ka9q-radio](https://github.com/ka9q/ka9q-radio) |
| **hf-timestd** | HF time-standard analyzer (WWV/WWVH/BPM, and WWVB at 60 kHz) — also the suite's **timing authority** (publishes the RTP↔UTC offset + tier other clients label against) | [HamSCI/hf-timestd](https://github.com/HamSCI/hf-timestd) |
| **wspr-recorder** | WSPR/FST4W recorder + decoder — uploads to wsprdaemon.org, which forwards to wsprnet.org | [HamSCI/wspr-recorder](https://github.com/HamSCI/wspr-recorder) |
| **psk-recorder** | FT4/FT8 spot recorder — decodes; wsprdaemon.org forwards its spots to PSKReporter | [HamSCI/psk-recorder](https://github.com/HamSCI/psk-recorder) |
| **mag-recorder** | RM3100 magnetometer recorder (USB-I²C, non-radiod) — daily datasets to PSWS | [HamSCI/mag-recorder](https://github.com/HamSCI/mag-recorder) |
| **meteor-scatter** | Meteor-scatter ping decoder (`jt9 --msk144`) → local sink + hs-uploader | [HamSCI/meteor-scatter](https://github.com/HamSCI/meteor-scatter) |
| **hfdl-recorder** | HFDL (High Frequency Data Link) recorder — one `dumphfdl` subprocess per band. Sends ground-station squitter timing to wsprdaemon.org; the airframes.io feed stays off unless you turn it on | [HamSCI/hfdl-recorder](https://github.com/HamSCI/hfdl-recorder) |
| **codar-sounder** | Opportunistic ionospheric sounder using CODAR chirp transmissions | [HamSCI/codar-sounder](https://github.com/HamSCI/codar-sounder) |
| **hamsci-physics** | Ionospheric science products (TEC/dTEC, TID, GRAPE/PSWS) from hf-timestd observations | [HamSCI/hamsci-physics](https://github.com/HamSCI/hamsci-physics) |
| **station-web** | Station status, documentation and data reports; timing and science pages where hf-timestd runs | [mijahauan/station-web](https://github.com/mijahauan/station-web) |
| **gmag-webui** | Real-time magnetometer web dashboard | [HamSCI/gmag_webui](https://github.com/HamSCI/gmag_webui) |

| Additional client | What it does | Repo |
|--------|-------------|------|
| **hf-tec** | HF PRN-coded beacon recorder for ionospheric specification (TEC) | [HamSCI/hf-tec](https://github.com/HamSCI/hf-tec) |
| **superdarn-sounder** | Passive SuperDARN coherent-radar monitor (detection / identification) | [HamSCI/superdarn-sounder](https://github.com/HamSCI/superdarn-sounder) |

### Infrastructure components

| Component | What it does | Repo |
|-----------|-------------|------|
| **igmp-querier** | Robust IGMP querier so radiod multicast survives on home-network switches | [HamSCI/igmp-querier](https://github.com/HamSCI/igmp-querier) |
| **sigmond-rac** | Remote Access Channel — frpc reverse tunnel to gw2.wsprdaemon.org for admin SSH/web behind NAT | [HamSCI/sigmond-rac](https://github.com/HamSCI/sigmond-rac) |
| **gpsdo-monitor** | GPSDO health monitor + mDNS advertiser (Leo Bodnar and MVR GPSDOs) |
| **ka9q-web** | Web interface for ka9q-radio (radiod status UI) | [mijahauan/ka9q-web](https://github.com/mijahauan/ka9q-web) (a fork until its audio change merges upstream) | [HamSCI/gpsdo-monitor](https://github.com/HamSCI/gpsdo-monitor) |

The RF clients use [ka9q-python](https://github.com/HamSCI/ka9q-python)
to receive RTP streams from radiod; `mag-recorder` is the exception — it
reads its magnetometer directly over USB-I²C, not through radiod.  Each
client runs in its own Python venv and manages its own systemd services.
Decoded spots / products are written to the shared SQLite sink
(`/var/lib/hs-uploader/sink.db`, or `/var/lib/sigmond/sink.db` on older
stations) and shipped upstream by `hs-uploader`.

> **Why a station is worth more as part of a network:** each DASI2 node already
> carries several GPS-co-registered ionospheric instruments — `hf-timestd`
> (dTEC), `hf-tec` (coded-beacon absolute TEC), `codar-sounder` (oblique
> sounding / MUF), `hfdl-recorder` (dense opportunistic paths), and
> `mag-recorder` (the geomagnetic *driver*) — so a single node is already a
> multi-modal observatory. Replicated across the map, the mesh becomes a
> distributed-aperture instrument that can *image* travelling ionospheric
> disturbances, sense TEC gradients by differencing receivers that share a
> transmitter, and feed tomographic TEC — and it is resilient to the steady loss
> of the HF time standards it listens to (CHU went silent in 2026). See
> [**docs/STATION-NETWORK-CAPABILITIES.md**](STATION-NETWORK-CAPABILITIES.md).

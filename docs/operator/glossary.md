# Glossary — the words this guide uses

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 63e8cbb on 2026-08-23 — live b4 + dasi002 (smd --help, smd status/version) + code/docs
> **Canonical for:** plain-English definitions of station vocabulary

Alphabetical. If a word in any operator page is not obvious, it should be here;
if it isn't, that is a bug in the docs — tell your fleet admin.

| Term | What it means |
|---|---|
| **appliance** | The ready-made Sigmond USB image that installs a whole station — Proxmox host, decoder VM and all the software — onto a blank machine in one pass. Installing any other way makes you a contributor, not an operator. |
| **canary** | The one fleet station that takes every update first, so a bad release breaks one station instead of all of them. Today that is AC0G/B4. |
| **decoder VM** (**VM**) | The virtual machine running inside the station computer, where `radiod` and every recorder actually run. Named after the *designator* (e.g. `AC0G-B4`). Commands tagged `[VM]` run here. |
| **designator** | The station name you give the *wizard* (e.g. `AC0G-B4`, `DASI2-01`). The VM takes the designator; the Proxmox host takes `<designator>-PM`. |
| **fleet admin** | The person who gave you the image and watches the fleet board. Your first contact whenever something looks wrong. |
| **GPSDO** | GPS-disciplined oscillator — a reference clock steered by GPS instead of by the temperature of your shack. We run the Leo Bodnar LBE-1421; it is required. |
| **grid square** | Your Maidenhead locator, 6 characters (e.g. `EM38ww`) — where the station is, to a few km. The *wizard* asks for it once. |
| **heartbeat** | A small status record your station sends to the fleet board every 5 minutes. If it stops arriving the board turns your station red — that is exactly what it is for (`CONTRIBUTING.md` §10). |
| **host** (**PM**, **Proxmox**) | The bare machine itself, running the Proxmox virtualisation system; named `<designator>-PM`, web GUI on port 8006. Commands tagged `[host]` run here. |
| **ka9q-web** | The live receiver web page on the VM, port 8081: waterfall plus the channel list. The quickest "is the radio hearing anything?" check. |
| **multicast** | How `radiod` hands each channel to the recorders — one stream on your local network that many programs subscribe to at once. It is why the station needs wired Ethernet; Wi-Fi handles multicast badly. |
| **PPS** | "Pulse per second", the once-a-second tick a GPS receiver produces. The PPS the GPSDO reports over USB is a liveness indicator only, never a timing reference. |
| **pskreporter** | [pskreporter.info](https://pskreporter.info) — the public database your FT8 and FT4 *spots* are uploaded to. |
| **PSWS** | HamSCI's Personal Space Weather Station network and its [data portal](https://pswsnetwork.eng.ua.edu/), where the daily time-standard and magnetometer products land. Needs a station ID plus per-instrument IDs. |
| **RAC** | Remote Access Channel — an outbound tunnel that lets your fleet admin reach the station from outside your router with no ports opened and no firewall changes. Optional. |
| **radiod** | The ka9q-radio program that reads the RX888 and publishes every channel to the recorders. If `radiod` is not running, nothing else on the station works. |
| **reporter ID** | The callsign, with optional `/suffix`, that your spots are reported under — e.g. `AC0G/B4`. One per station; set by the *wizard*. |
| **RM3100** | The PNI magnetometer sensor, optional. It produces the station's daily geomagnetic product for PSWS. |
| **RTP** | The real-time streaming format `radiod` uses on the local network. You will meet the word in log messages about "RTP gaps", which mean lost samples. |
| **RX888** | The wideband SDR receiver (Mk II) that digitises 10 kHz–64 MHz all at once and streams it over USB 3. One per station. |
| **sink** | The shared database `/var/lib/sigmond/sink.db` that every recorder writes into and the uploader reads from. It is the station's source of truth for what was heard. |
| **smd** | The single command that runs the station ("SigMonD") — `smd status`, `smd doctor`, `smd version`. Run it as yourself inside the VM; it refuses to run under `sudo`. |
| **spot** | One report that you heard one station, at one time, on one frequency. The unit of WSPR and FT8/FT4 data. |
| **SSRC** | The 32-bit stream id `radiod` assigns to each channel; it shows up in status output and logs. It is assigned, never derived from the frequency. |
| **timing tier** (**T1–T6**) | How good the station's clock evidence is right now — T6 (TS-1 injector, nanosecond class) is best, T5 is the GPSDO's GPS/PPS, lower tiers come from HF broadcasts. `smd status` prints the current tier (b4 showed T4, dasi002 T3, 2026-08-23). |
| **TS-1** | The Turn Island Systems time injector, optional. It feeds a GPS-locked signal into the receive path so the station can measure its own clock at tier T6. |
| **wizard** (`sigmond-setup`) | The setup questions asked on first boot — reporter ID, grid square, station name, PSWS IDs. Rerun it from the host as `sigmond-setup --reconfigure` to fix an answer. |
| **wsprdaemon** | [wsprdaemon.org](https://wsprdaemon.org) — the aggregation service that receives a copy of the spots for fleet-wide analysis. |
| **wsprnet** | [wsprnet.org](https://wsprnet.org) — the global WSPR spot database; where your WSPR spots land, searchable by your *reporter ID*. |

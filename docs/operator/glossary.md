# Glossary — the words this guide uses

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 14a7ebf on 2026-08-23 — walk-through fixes (live dasi002 + b4)
> **Canonical for:** plain-English definitions of station vocabulary

Alphabetical. If a word in any operator page is not obvious, it should be here;
if it isn't, that is a bug in the docs — tell your fleet admin.

> **"Verified against" names the commit *this page* was checked against — not
> your station.** Every operator page carries one, and they differ from page to
> page because pages are re-verified in batches. `smd version` on your station
> will print different commits again. That is expected and is not a sign your
> station is wrong or the page is stale; the line is there so you can tell your
> fleet admin *what* the page was true of.

| Term | What it means |
|---|---|
| **appliance** | The ready-made Sigmond USB image that installs a whole station — Proxmox host, decoder VM and all the software — onto a blank machine in one pass. Installing any other way makes you a contributor, not an operator. |
| **canary** | The one fleet station that takes every update first, so a bad release breaks one station instead of all of them. Today that is AC0G/B4. |
| **client** | A *component* that records or ships a science product and owns systemd units — `wspr-recorder`, `psk-recorder`, `hf-timestd`, `mag-recorder`, `meteor-scatter`, `gpsdo-monitor`. Only clients get a block in `smd status`, and only while they are *enabled*. |
| **component** | Anything `smd` manages and versions: a *client*, a shared library (`ka9q-python`, `hs-uploader`, `hamsci-dsp`), the radio server itself, or `smd`. `smd version` lists **every** component; `smd status` shows only the enabled clients among them, which is why the two lists are different lengths. |
| **decoder VM** (**VM**) | The virtual machine running inside the station computer, where `radiod` and every recorder actually run. Named after the *designator* (e.g. `AC0G-B4`). Commands tagged `[VM]` run here. |
| **designator** | The station name you give the *wizard* (e.g. `AC0G-B4`, `DASI2-01`). The VM takes the designator; the Proxmox host takes `<designator>-PM`. |
| **docs-gap ledger** | [`../contributor/docs-gap-ledger.md`](../contributor/docs-gap-ledger.md) — the project's running list of places where the software does not yet let a page say what it wants to say. Several of the ⚠ and ✗ lines you see every week are explained by a row in it, and the operator pages link straight to those rows. You may read it freely; it is written for contributors, so it names source files rather than actions. |
| **enabled** | A client this station has been told to run, so its systemd units exist and are started at boot. **`smd status` lists only enabled clients** (`smd status --help`: *"component names (default: all enabled)"*), so a client that is *installed* but deliberately switched off prints no block at all — which is not a fault. To see both at once, run `smd component list` and read its **LIFECYCLE** column. |
| **fleet admin** | The person who gave you the image and watches the fleet board. Your first contact whenever something looks wrong. |
| **frpc** | The small program that holds the *RAC* tunnel open. It runs on the `[host]`, dials out to the gateway and keeps the four channels alive; you meet the name in `smd admin rac status` output and in `/etc/sigmond/frpc-host.toml`. |
| **GPSDO** | GPS-disciplined oscillator — a reference clock steered by GPS instead of by the temperature of your shack. We run the Leo Bodnar LBE-1421; it is required. |
| **GRAPE** | HamSCI's HF time-standard data product, and the reason the timing client records at all: one packaged dataset per UTC day, built by `hf-timestd` from the WWV/WWVH/CHU channels and uploaded to *PSWS* at 01:00 UTC by `grape-daily.timer`. When these pages say "no GRAPE", they mean that daily dataset is not being produced. |
| **grid square** | Your Maidenhead locator, 6 characters (e.g. `EM38ww`) — where the station is, to a few km. The *wizard* asks for it once. |
| **heartbeat** | A small status record your station sends to the fleet board every 5 minutes. If it stops arriving the board turns your station red — that is exactly what it is for (`CONTRIBUTING.md` §10). |
| **holdover** | What a *GPSDO* does when it loses its GPS fix: it keeps producing a clock from its own oscillator, coasting and drifting slowly away from GPS. `smd status` prints `gpsdo=holdover` and marks it ⚠. The station keeps recording and keeps producing spots the whole time — it only loses timing quality, and it is a thing to report, not a thing to fix. |
| **host** (**PM**, **Proxmox**) | The bare machine itself, running the Proxmox virtualisation system; named `<designator>-PM`, web GUI on port 8006. Commands tagged `[host]` run here. |
| **igmp-querier** | A tiny always-on service on the VM that keeps your LAN switch forwarding the station's *multicast* streams. Switches stop forwarding multicast a few minutes after the last query, and then the recorders go deaf, so this exists to ask the question nothing else on a home network asks. One of the four clients that is cheap to restart. |
| **installed** | Present on the station: the code is checked out and `smd version` prints its commit. Installed does **not** mean running and does not mean *enabled* — dasi002 listed 23 installed components while `smd status` showed 7 client blocks (live, 2026-08-23). |
| **ka9q-web** | The live receiver web page on the VM, port 8081: waterfall plus the channel list. The quickest "is the radio hearing anything?" check. |
| **multicast** | How `radiod` hands each channel to the recorders — one stream on your local network that many programs subscribe to at once. It is why the station needs wired Ethernet; Wi-Fi handles multicast badly. |
| **orphaned** | The tag `smd status` puts on a running unit that the station's current configuration no longer declares — usually left behind by a rename. Harmless, but worth naming to your fleet admin so it gets tidied. |
| **PPS** | "Pulse per second", the once-a-second tick a GPS receiver produces. The edge statistics `gpsdo-monitor` reports over USB are a liveness/health check only (OS-millisecond), not precision metrology — but hf-timestd does use the GPSDO's GPS/PPS as timing tier T5. |
| **pskreporter** | [pskreporter.info](https://pskreporter.info) — the public database your FT8 and FT4 *spots* are uploaded to. |
| **PSWS** | HamSCI's Personal Space Weather Station network and its [data portal](https://pswsnetwork.eng.ua.edu/), where the daily time-standard and magnetometer products land. Needs a station ID plus per-instrument IDs. |
| **RAC** | Remote Access Channel — an outbound tunnel that lets your fleet admin reach the station from outside your router with no ports opened and no firewall changes. Optional. |
| **radiod** | The ka9q-radio program that reads the RX888 and publishes every channel to the recorders. If `radiod` is not running, nothing else on the station works. |
| **reporter ID** | The identity your *spots* are reported under — normally your callsign with an optional `/suffix`, e.g. `AC0G/B4`. One per station; set by the *wizard*. On a **testbed** it may legitimately be a plain *designator* with no callsign in it at all — our own no-antenna station reports as `DASI002` — so a reporter ID that is not a callsign is not automatically a wizard answer to correct. Whatever `smd admin instance list` prints in its **REPORTER ID** column is the string to search on *wsprnet*. |
| **RM3100** | The PNI magnetometer sensor, optional. It produces the station's daily geomagnetic product for PSWS. |
| **RTP** | The real-time streaming format `radiod` uses on the local network. You will meet the word in log messages about "RTP gaps", which mean lost samples. |
| **RX888** | The wideband SDR receiver (Mk II) that digitises 10 kHz–64 MHz all at once and streams it over USB 3. One per station. |
| **sink** | The shared database `/var/lib/sigmond/sink.db` that every recorder writes into and the uploader reads from. It is the station's source of truth for what was heard. |
| **smd** | The single command that runs the station ("SigMonD") — `smd status`, `smd doctor`, `smd version`. Run it as yourself inside the VM; it refuses to run under `sudo`. |
| **spot** | One report that you heard one station, at one time, on one frequency. The unit of WSPR and FT8/FT4 data. |
| **SSRC** | The 32-bit stream id `radiod` assigns to each channel; it shows up in status output and logs. `radiod` assigns it, so don't try to work it out from the frequency. |
| **timing judge** (**OFFSET VIOLATION**) | The part of `hf-timestd` that compares each `radiod` channel's advertised epoch against the station's best clock evidence and prints the `judge T<n> σ=… gpsdo=…` summary line in `smd status`. A channel that disagrees by more than *k×σ* for over a minute gets its own `OFFSET VIOLATION` line. It is a **detector, not a fault** — hf-timestd's own data labels stay corrected regardless — and both fleet stations carry violations while producing good data ([day-2.md](day-2.md#1-smd-status--is-everything-running)). |
| **timing tier** (**T1–T6**) | How good the station's clock evidence is right now, best first: T6 TS-1 injector (ns class), T5 the GPSDO's GPS/PPS over USB, T4 a LAN GPS timeserver over NTP, T3 HF broadcast fusion (WWV/WWVH/CHU), T2 internet NTP, T1 GPSDO rate only (source: `hf-timestd/docs/METROLOGY.md` §"Axis T"). `smd status` prints the current tier — b4 T4, dasi002 T3 on 2026-08-23. |
| **TS-1** | The Turn Island Systems time injector, optional. It feeds a GPS-locked signal into the receive path so the station can measure its own clock at tier T6. |
| **vTEC** (`timestd-vtec`) | Vertical total electron content — a measure of how much ionosphere is overhead. `hf-timestd` can compute it when the station has a **dual-frequency GNSS receiver** (a u-blox ZED-F9P), which is an optional extra most stations do not have. Without one, `timestd-vtec.service` sits `inactive` and that is the correct state, not a fault. |
| **wizard** (`sigmond-setup`) | The setup questions asked on first boot — reporter ID, grid square, station name, PSWS IDs. Rerun it from the host as `sigmond-setup --reconfigure` to fix an answer. |
| **wsprdaemon** | [wsprdaemon.org](https://wsprdaemon.org) — the aggregation service that receives a copy of the spots for fleet-wide analysis. |
| **wsprnet** | [wsprnet.org](https://wsprnet.org) — the global WSPR spot database; where your WSPR spots land, searchable by your *reporter ID*. |

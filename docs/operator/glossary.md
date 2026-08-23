# Glossary — the words this guide uses

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 4aec0c2 on 2026-08-23 — walk-through fixes (live DASI002 + code/docs)
> **Canonical for:** plain-English definitions of station vocabulary

Alphabetical. If a word in any operator page is not obvious, it should be here;
if it isn't, that is a bug in the docs — tell your fleet admin. The
[scientist guide](../scientist/README.md) defines its own vocabulary inline,
where the mechanism is, and sends you here for station and operator words —
plus the handful below that its pages use without defining.

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
| **client** | A *component* that records or ships a science product — `wspr-recorder`, `psk-recorder`, `hf-timestd`, `mag-recorder`, `meteor-scatter`, `gpsdo-monitor`. Every component that owns systemd units gets a block in `smd status`; the recording clients also get a summary line — see [day-2.md §1](day-2.md#1-smd-status--is-everything-running). |
| **component** | Anything `smd` manages and versions: a *client*, a shared library (`ka9q-python`, `hs-uploader`, `hamsci-dsp`), the radio server itself, or `smd`. `smd version` lists **every** component; `smd status` shows only the enabled clients among them, which is why the two lists are different lengths. |
| **Costas array** | A pattern of tones whose time/frequency shifts never repeat, so a receiver can find *when* a burst started even in noise. The 2026-08-12 eclipse experiment transmitted one on 14.110 MHz; "the Costas client" is the recorder built for it. |
| **dBFS** | Decibels relative to full scale — signal level measured against the largest number the digitiser can represent, so it is always ≤ 0 and more negative means quieter. A receiver with nothing plugged in reads around −127 dBFS. |
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
| **igmp-querier** | A tiny always-on service on the VM that keeps your LAN switch forwarding the station's *multicast* streams. Switches stop forwarding multicast a few minutes after the last query, and then the recorders go deaf, so this exists to ask the question nothing else on a home network asks. One of the four components that are cheap to restart. |
| **installed** | Present on the station: the code is checked out and `smd version` prints its commit. Installed does **not** mean running and does not mean *enabled* — dasi002 listed 23 installed components while `smd status` showed 7 client blocks (live, 2026-08-23). |
| **ka9q-web** | The live receiver web page on the VM, port 8081: waterfall plus the channel list. The quickest "is the radio hearing anything?" check. |
| **multicast** | How `radiod` hands each channel to the recorders — one stream on your local network that many programs subscribe to at once. It is why the station needs wired Ethernet; Wi-Fi handles multicast badly. |
| **orphaned** | The tag `smd status` puts on a running unit that the station's current configuration no longer declares — usually left behind by a rename. Harmless, but worth naming to your fleet admin so it gets tidied. |
| **PPS** | "Pulse per second", the once-a-second tick a GPS receiver produces. The edge statistics `gpsdo-monitor` reports over USB are a liveness/health check only (OS-millisecond), not precision metrology — but hf-timestd does use the GPSDO's GPS/PPS as timing tier T5. |
| **preset** (radiod) | A named recipe in `radiod` for how to demodulate one channel — `iq`, `usb`, `lsb`, `am`, `fm`, `cw` and others the station's `presets.conf` defines. `iq` is raw complex baseband and is what every timing and archive channel uses. **Not** systemd's `preset` (below), which is a different word about whether a unit starts at boot. |
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
| **WWV / WWVH / CHU** | The shortwave time stations the timing client listens to: WWV (Colorado) on 2.5, 5, 10, 15, 20 and 25 MHz, WWVH (Hawaii) on 2.5, 5, 10 and 15 MHz only, CHU (Canada) on 3.33, 7.85 and 14.67 MHz. They are the HF evidence behind timing tier T3, and the standard signal to test a capture against. |

---

## Words you will see in command output

The table above covers the words these pages *write*. This one covers words the
station's own commands *print* at you, which is a different list. Nothing here
is something you act on; it is here so you can read your own output.

### systemd, and how the station is run

| Term | What it means |
|---|---|
| **systemd** | The Linux service manager. It is what actually starts, stops and restarts everything on the station at boot and keeps it running. `smd` mostly talks to systemd on your behalf. |
| **unit** | One thing systemd manages, named like `radiod@AC0G-B4.service` or `grape-daily.timer`. A `.service` is a program; a `.timer` is a schedule that starts one. The lines under each client in `smd status` are its units. |
| **`active` / `inactive` / `failed`** | A unit's state. `active` = running. `inactive` = not running, and nobody asked it to be. `failed` = it tried to run and gave up — the only one of the three that is always worth reporting. |
| **`enabled` / `disabled` / `linked` / `preset`** | Whether systemd starts a unit at boot. `enabled` yes, `disabled` no; `linked` means the unit file lives outside systemd's own directory and was linked in, which on this station still means "not enabled unless something enabled it". `preset: enabled` is just the packaged default. |
| **`status=78` / `EX_CONFIG`** | A program exited with code 78, which by long Unix convention (`sysexits.h`) means *"my configuration is wrong or missing"* — not "the hardware broke". It is what `mag-recorder` exits with on a station that has no magnetometer configured. |
| **chrony** | The clock daemon that keeps the machine's system time disciplined. The timing client feeds it; you never configure it. |

### Words from `smd status`

| Term | What it means |
|---|---|
| **`contract=0.8`** | Which version of sigmond's client contract that client implements — the agreed interface between `smd` and a recorder. Bookkeeping between components; nothing for you. |
| **`default: 6 ch, 6 freqs`, `default: [other]`** | A client's inventory: how many radio channels its instance named `default` uses, and on how many frequencies. `[other]` means "this client does not consume radio channels". |
| **`σ`, `age 0s`, `seg 3`, `rate +0.066 ppm`** | The timing judge's working numbers: σ is the spread of the clock evidence (smaller is better), `age` how long since it was updated, `seg` which measurement segment, and `rate` how fast a channel's error is changing in parts per million. Your admin reads these; the word you watch is `gpsdo=`. |
| **`other pool: 10 CPUs`** | The CPUs *not* reserved for `radiod` — the ones everything else is allowed to run on. |
| **`lan-capable`, `querier: v2 <address> on ens18`** | The network self-check: the station can carry *multicast* on its LAN, and something is issuing the periodic membership queries that keep the switch forwarding it. `ens18` is just the network interface's name. |
| **`IQ`** | In-phase and quadrature — the pair of numbers that make up one raw radio sample. "Raw IQ" is the unprocessed recording the timing client writes, and it is what fills the disk. |
| **`raw_buffer` / `phase2` / eviction** | Two directories under `/var/lib/timestd`: `raw_buffer` holds the raw IQ on a rolling window, `phase2` holds the derived analysis products and database. **Eviction** is the timing client deleting its own oldest data to stay under the disk limit — see [day-2.md §3](day-2.md#3-disk--df--h-). |
| **`uploader.ssh_key_file`, `Grape uploader`** | mag-recorder's own config keys and its name for the shared upload path to *PSWS*. "Grape uploader" is the same thing as *GRAPE* above. |

### Words from `smd component list` and `smd doctor`

| Term | What it means |
|---|---|
| **VERDICT** | The state of a component's *source checkout*, not of the running station: `up to date`, `behind main (run update)`, `N unpushed commit(s)`, `on branch <name>`, `pinned to …`, or `dirty: <reason>`. Colour follows the same axis. Not an alarm — see [day-2.md](day-2.md#installed-enabled-shown). |
| **INDEX** | How many commits deep the checkout is. A serial number, nothing more. |
| **BEHIND** | How many commits behind its upstream branch that checkout is. `-` means level. |
| **HEAD DATE / POLICY / `latest`** | The date of the checked-out commit, and the version policy the catalog sets for that component — `latest` means "track the branch", anything else names a pin. |
| **`detached@<hash>` / `origin/main` / "feature branch"** | Git bookkeeping: the checkout is parked on one specific commit rather than following a branch; `origin/main` is the upstream it is compared against. |
| **`dirty` / `detached` / `untracked` / `ownership` / `venv-skew`** | `smd doctor`'s finding categories — a tracked file changed locally, a deliberate commit pin, files git does not track, files with the wrong owner, and a virtual environment that has drifted from its checkout. All explained at [day-2.md §4](day-2.md#4-smd-doctor--only-when-something-looks-off). |
| **`.pin`, `egg-info`** | Small housekeeping files a build leaves behind. They show up as `untracked` and are harmless. |

### Words from the other verbs

| Term | What it means |
|---|---|
| **`CONFIG` / `ENV` / `SOURCES`** (`smd admin instance list`) | Three ✓/✗ checks on one recorder instance: does it have a config file, has its environment file been rendered, and are its radio sources resolved. Three ✓ means fully wired. Anything else goes to your fleet admin. |
| **`uploader-manifest`, pipeline, unresolved identity** (`smd config uploads status`) | See [registration.md §6](registration.md#6-confirming-everything-flows) — a *pipeline* is one product-to-destination route, and *unresolved identity* names the ids it has not been given. |
| **`PLL`, `A-level` / `A0`, `no_fix`, `ANT`, `OUT1/OUT2 MHz`** (`smd watch gpsdo`) | The GPSDO's own report. **PLL** is its phase-locked loop — `yes` means the oscillator is locked to its reference. `A0`/`A1` is a coarse health grade with the reason spelled out beneath. `no_fix` means zero satellites. `ANT` is whether the antenna reads as connected. OUT1/OUT2 are the frequencies on its two outputs. |
| **`FX3`** | The USB controller chip inside the *RX888*. It is the part that latches up and needs a full power-off rather than a reboot. |

### Other components you may see listed

`smd version` and `smd component list` name everything sigmond knows about, not
just what your station runs. **None of these is missing from your station by
mistake**; unless your fleet admin enabled one, it is simply not part of the
standard DASI2 kit.

| Name | What it is |
|---|---|
| **callhash** | A small shared library for hashing callsigns consistently across clients. |
| **codar-sounder** | An optional client that records CODAR ocean-radar signals. |
| **hf-tec** | An optional client for total-electron-content work from HF. |
| **hfdl-recorder** | An optional client that decodes HFDL aircraft data links. |
| **superdarn-sounder** | An optional client that records SuperDARN radar signals. |
| **sigmond-rac** | The packaging of the remote-access tunnel — see [remote-access.md](remote-access.md). |
| **ft8_lib** | The FT8/FT4 decoder library the spot recorders are built against. A build ingredient, not a service. |
| **wsjtx** | Upstream WSJT-X source, built only for its `wsprd` and `jt9` decoders. A build ingredient, not a service. |
| **onion** | The small C web-server library `ka9q-web` is built on. A build ingredient, not a service. |

The last three have no row in `smd component list` for exactly that reason —
they are things other components are *made of*, so they have no lifecycle of
their own.

# What to buy — station hardware

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 14a7ebf on 2026-08-23 — walk-through fixes (live dasi002 + b4)
> **Canonical for:** the station parts list

Words in *italics* the first time they appear are explained in the
operator [glossary](../operator/glossary.md).

## The one-paragraph version

A HamSCI/DASI2 station is one small computer, one wideband radio, one
antenna, a GPS-disciplined clock, and a network cable. The radio is an
**RX888 Mk II** — it digitises everything from 10 kHz to 64 MHz at once
and fire-hoses it over USB 3 at more than 2 Gbit/s (source:
`ka9q-radio/docs/SDR/rx888.md`), so the computer has to be a real x86-64
machine with 16 GB of RAM and an internal NVMe drive, not a Pi (source:
`sigmond-appliance/INSTALL.md` §1). A **Leo Bodnar LBE-1421**
GPS-disciplined oscillator (*GPSDO*) is required too: it gives the
receiver a clock steered by GPS instead of by the temperature of your
shack. Everything past that is optional, and each optional part buys you
exactly one more science product — a **TS-1** time injector for
nanosecond-class timing, an **RM3100** magnetometer for geomagnetic
data, a dual-frequency GNSS receiver for total-electron-content work.
The required set on its own already produces WSPR and FT8/FT4 spots.
Budget roughly **$860–1,630** for it, and **$1,195–2,200** for the full
build AC0G/B4 runs (see [Approximate cost](#approximate-cost) — all
figures approximate, as of 2026-08, check current prices).

## Required

| Part | Exact model we run | Why | Notes / alternatives |
|---|---|---|---|
| Station computer | x86-64 mini-PC, **16 GB RAM or more for the whole machine**, internal NVMe/SSD (source: `sigmond-appliance/INSTALL.md` §1). The Proxmox reference system is a KAMRUI mini-PC with an AMD Ryzen 5 5560U — 6 cores / 12 threads (source: `sigmond/docs/proxmox/CLAUDE.md` §Hardware context); Beelink-class boxes are also in service (source: `mag-recorder/docs/PROVENANCE.md`, `gpsdo-monitor/README.md` §Status). On AC0G/B4 the **decoder VM's share** is 14 vCPUs, ~9 GB RAM and a 252 GB disk 52 % used (source: `nproc; free -g; df -h` run inside the VM on b4, 2026-08-23) — the 16 GB floor is for the host, which also runs Proxmox. | The radio streams more than 2 Gbit/s in real time and the host has to keep up with it; `radiod` works to a ~20 ms internal latency budget (source: `ka9q-radio/docs/SDR/rx888.md`). Fall behind and you lose samples silently rather than loudly (source: `sigmond/docs/PACKET-LOSS-DIAGNOSTICS.md`). | ⚠ **The whole internal drive is erased** by the appliance installer (source: `sigmond-appliance/INSTALL.md` §1). ⚠ **Buy more disk than the old sizing suggests.** Measured on AC0G/B4, 2026-08-23: about **15 GB per timing channel per day** — one complete UTC day (2026-08-22, all 288 five-minute files) of one channel's compressed raw IQ came to 15,073,610,352 bytes in `/var/lib/timestd/raw_buffer/WWV_25000/20260822` — so that station's **six** channels write roughly **90 GB a day**. That is **raw IQ only**. On top of it the station keeps a cumulative analysis database — `/var/lib/timestd/phase2/timestd.db`, 9,233,326,080 of the 9,244,733,098 bytes in `phase2` on b4 (measured 2026-08-23) — which **grows continuously instead of rolling**, plus a few megabytes of per-channel products. Amortised over what b4 is currently holding that is roughly **1 GB per channel per day**, so budget **≈16 GB per channel per day, ≈96 GB a day** for a six-channel station, and treat the database as a slow permanent addition rather than part of the daily churn. The written sizing "3 channels want 60 GB minimum, 6 channels 120 GB, 9 channels 180 GB" (source: `hf-timestd/INSTALLATION.md` §Storage Requirements) is barely more than **one day** of recording at the measured rate; it is a floor for aggressive retention, not a comfortable disk. b4 runs a **252 GB** disk for six channels and holds about a day and a half of raw IQ before its own eviction reclaims the space. Buy 500 GB or more and you will not think about it again. (The two written sources disagree by ~2.7× — [docs-gap ledger row 20](../contributor/docs-gap-ledger.md); the measurement above is the tie-break and neither source has been corrected yet.) Not a laptop — see [Things that look right but aren't](#things-that-look-right-but-arent). |
| GPSDO (GPS-disciplined oscillator) | **Leo Bodnar LBE-1421**, USB ID `1dd2:2444`, serial `0C7BB80D10EF` on b4 — PLL locked, 3D GPS fix, OUT1 = 10 MHz, OUT2 = 27 MHz, PPS enabled (source: `lsusb` and `/run/gpsdo/0C7BB80D10EF.json` on b4, 2026-08-23). | A sampling clock disciplined to GPS instead of drifting with room temperature — the ruler every timing measurement is made against. `smd bringup dasi2` provisions `gpsdo-monitor` as part of the station's core infrastructure (source: `sigmond/etc/catalog.toml` `[profile.dasi2]`). | Other Leo Bodnar models work, with different feature sets — see the notes under this table. Without a GPSDO the appliance still installs and still records WSPR and FT8/FT4, but the sample clock drifts, the higher timing tiers are unavailable, and it is not a DASI2 station. (`sigmond-appliance/INSTALL.md` §1 used to list the GPSDO among "optional extras"; it was corrected to the required list on 2026-08-23, so the two pages now agree.) |
| SDR receiver | **RX888 Mk II** (enumerates as `04b4:00f1 Cypress Semiconductor Corp. RX888mk2`) (source: `lsusb` on b4, 2026-08-23) | It samples the whole HF spectrum at once, which is what lets one box run WSPR, FT8, time standards, HFDL and meteor scatter from a single antenna (source: `ka9q-radio/docs/SDR/rx888.md`). | Any SDR ka9q-radio supports will work for a first light (Airspy HF+, SDRplay) (source: `hf-timestd/INSTALLATION.md` §Hardware), but the RX888 Mk II is the one the appliance image, the timing chain and every recipe here assume. **One per host** — the upstream author recommends against two (source: `ka9q-radio/docs/SDR/rx888.md`). |
| USB 3 port and cable for it | On b4 the RX888 sits on `Bus 004 Device 003`, directly on a `1d6b:0003 Linux Foundation 3.0 root hub` — nothing between it and the machine (source: `lsusb` on b4, 2026-08-23). | USB 2 is nowhere near fast enough for 2 Gbit/s, and a hub in the path starves the transfer; the symptom is silent sample loss, not an error message (source: `ka9q-radio/docs/SDR/rx888.md`; `sigmond/docs/PACKET-LOSS-DIAGNOSTICS.md`). | Use the blue port, use the cable that came with the radio, plug it straight in. |
| HF antenna | AC0G/B4 records `description = "T3FD"` — a terminated tilted folded dipole (source: `/etc/sigmond/site-profile.toml` on b4, 2026-08-23); the DASI2 kit reference antenna is a DX Engineering (DXE) model (source: `sigmond/README.md` §Architecture at a glance). | The receiver hears everything at once, so a broadband antenna beats a monoband one — a resonant 20 m dipole gives you 20 m and little else. | Any broadband HF antenna that hears WSPR on 20 m will do for a first light. Coverage of 2.5–25 MHz is what the timing client wants (source: `hf-timestd/INSTALLATION.md` §Hardware). Add coax, plus a small GPS puck antenna with sky view for the GPSDO (and a second one for the TS-1, if you add it). |
| Wired Ethernet | b4 reports `lan-capable` with a working *multicast* querier on `ens18` (source: `smd status` on b4, 2026-08-23). | `radiod` publishes each channel as a *multicast* RTP stream and every client subscribes to it; Wi-Fi handles multicast badly and the appliance does not configure it at all (source: `sigmond-appliance/INSTALL.md` §1; `sigmond/docs/networking.md`). | An ordinary home router with DHCP and internet is enough — "if other devices just work when plugged in, you're fine" (source: `sigmond-appliance/INSTALL.md` §1). |
| USB stick for the install | 8 GB minimum; 16–32 GB ideal (source: `sigmond-appliance/INSTALL.md` §1). | The appliance image is written to a stick, booted once, and then removed. | ⚠ Everything on the stick is erased. You also need any second computer — Mac, Windows or Linux — to write it. |
| Monitor + USB keyboard | Needed once, for the six setup questions (source: `sigmond-appliance/INSTALL.md` §1). | First boot asks for your callsign, grid square and a few other facts before the network is usable. | You can unplug both afterwards; the station is run over the network from then on. Expect the keyboard to stop working after the install reboot — that is correct, see below. |

Two things about the GPSDO before you buy one:

- **Other GPSDO models work.** The LBE-1420, LBE-1423, LBE-1425 and
  LBE-Mini are supported with different feature sets — see the hardware
  support matrix in
  [`gpsdo-monitor/README.md`](https://github.com/HamSCI/gpsdo-monitor/blob/main/README.md#hardware-support-matrix).
  The 1421 is the only model live-validated by us; the 1425 driver is
  experimental and untested on hardware (source: `gpsdo-monitor/README.md`
  §Status).
- ⚠ **The 1 PPS the GPSDO reports over USB is a liveness indicator, not
  a timing source.** Those numbers are OS-millisecond bound (read via
  `TIOCMIWAIT` on the CDC serial line), and every published measurement
  carries the warning verbatim — the live file on b4 says
  "OS-millisecond bound; not a metrology reference" (source:
  `gpsdo-monitor/README.md` §"What it does *not* do";
  `/run/gpsdo/0C7BB80D10EF.json` on b4, 2026-08-23). hf-timestd *does*
  consume the GPSDO's USB-delivered GPS/PPS as tier T5, at
  microsecond-to-millisecond class; what is not a measurement you can
  lean on is `gpsdo-monitor`'s own published PPS edge statistics
  (source: `hf-timestd/docs/ARCHITECTURE-FIRST-PRINCIPLES.md`).

## Optional — and what you lose without it

| Part | Exact model we run | What it buys you | What you lose without it |
|---|---|---|---|
| TS-1 time injector | **Turn Island Systems TS-1** (WB6CXC design). It appears on USB as an Adafruit Trinket M0, `239a:801e` — that is exactly how sigmond and hf-timestd detect it (source: `hf-timestd/scripts/ts1-probe.sh`; `sigmond/scripts/proxmox/sigmond-wizard.sh`; confirmed present on b4 by `lsusb`, 2026-08-23) | Timing tier **T6**: the TS-1 BPSK-modulates its own GPS PPS onto a clean GPSDO-disciplined carrier and injects it into the receive path, where it is recovered sample-precise from the sample stream. The path is hard-wired rather than through the ionosphere, so it is ns-class once the chain delay is calibrated (source: `hf-timestd/docs/ARCHITECTURE-FIRST-PRINCIPLES.md` §T6). | No T6. Timing falls back to the GPSDO's USB-delivered GPS/PPS (tier T5, microsecond-to-millisecond class) and to HF broadcast fusion (source: `hf-timestd/docs/METROLOGY.md`). WSPR, FT8/FT4 and the magnetometer are unaffected. |
| Magnetometer | **PNI RM3100** 3-axis magnetometer with an on-board MCP9808 temperature sensor, read over a **Pololu Isolated USB-to-I²C Adapter with Isolated Power** (Pololu **5397**, USB ID `1ffb:2503`; the 5396, without isolated power, is `1ffb:2502`) (source: `mag-recorder/README.md` §Data flow; `lsusb` on b4, 2026-08-23) | One geomagnetic sample per second, packaged daily and uploaded to the *PSWS* network as its own instrument — b4 is PSWS instrument 372 (source: `mag-recorder/README.md`; `smd psws status` on b4, 2026-08-23). | No geomagnetic product. Nothing else changes. |
| Dual-frequency GNSS receiver | **u-blox ZED-F9P** (or WaveShare LG290P) (source: `hf-timestd/INSTALLATION.md` §Hardware) | Local total-electron-content (TEC) measurement, used to correct the ionospheric delay in the HF timing products (source: `hf-timestd/INSTALLATION.md` §"Optional: GNSS VTEC Monitoring"). | No local TEC product; the timing chain falls back to downloaded global ionosphere maps. |
| Local GPS time server | Any GPS-disciplined NTP server on your LAN (source: `sigmond-appliance/INSTALL.md` §1) | A LAN-local stratum-1 time source the station discovers by itself. | The host clock leans on internet NTP — fine for spots, weaker for timing work. |

One thing worth knowing before you buy the optional parts:

- **The magnetometer sensor is the HamSCI TangerineSDR/Grape board**;
  the isolated adapter is what lets it hang off a normal PC instead of a
  Raspberry Pi's GPIO header (source: `mag-recorder/docs/PROVENANCE.md`).

## Cabling

Read this as a wiring list, not a picture:

- **Antenna → RX888 HF/RF input** (coax).
- **RX888 → station computer**: USB 3 cable into a blue USB-3 port, **no hub in between**. On b4 the radio is the only thing on its USB 3 root hub (source: `lsusb`, 2026-08-23).
- **GPSDO → RX888 reference input**: the LBE-1421 has two outputs and b4 runs both — OUT1 at 10 MHz and OUT2 at 27 MHz, both enabled (source: `/run/gpsdo/0C7BB80D10EF.json` on b4, 2026-08-23). The RX888's own sampling clock is 27 MHz, and ka9q-radio's guidance for a precise sampling clock is to feed an external 27 MHz reference to the connector inside the unit (source: `ka9q-radio/docs/SDR/rx888.md` §calibrate). Sigmond's own docs describe the GPSDO generically as "10 MHz + PPS" (source: `sigmond/README.md` §What you need). ⚠ **Which jack each output is physically patched into is not recorded in any file on the station** — check your own radio's manual and label your cables. Tracked as a gap in [`../contributor/docs-gap-ledger.md`](../contributor/docs-gap-ledger.md).
- **GPSDO → station computer** over USB: **health monitoring, plus the fallback timing tier.** The link reports lock state, GPS fix, satellite count and output frequencies, and hf-timestd uses the GPSDO's USB-delivered GPS/PPS as tier T5 (microsecond-to-millisecond class) when the TS-1 path is unavailable (source: `hf-timestd/docs/ARCHITECTURE-FIRST-PRINCIPLES.md`; `hf-timestd/docs/METROLOGY.md`). What this link does **not** give you is metrology: the PPS edge statistics `gpsdo-monitor` publishes are OS-millisecond bound and are a liveness/health indicator only (source: `gpsdo-monitor/README.md` §"What it does *not* do").
- **GPS puck antenna → GPSDO.** The GPSDO needs sky view to hold lock; b4 shows a 3D fix on 7 satellites with `antenna_ok` (source: `/run/gpsdo/<serial>.json` on b4, 2026-08-23).
- **TS-1 → the receive path**: the TS-1's BPSK-modulated carrier is coupled into the RF path ahead of the RX888 through a filter/attenuator (source: `hf-timestd/docs/ARCHITECTURE-FIRST-PRINCIPLES.md` §T6). The TS-1 has **its own GPS antenna and onboard PPS** — it does not take PPS from the LBE-1421 (source: `hf-timestd/docs/ARCHITECTURE-FIRST-PRINCIPLES.md` §"TS-1 HF-PPS injector"; `hf-timestd/INSTALLATION.md` §Hardware, "requires GPS+PPS").
- **TS-1 → station computer** over USB: a serial console that reports the injector's transmit frequency, used to set up detection (source: `hf-timestd/scripts/setup-station.sh`).
- **RM3100 → Pololu adapter** over I²C (addresses 0x23 and 0x1F), then **Pololu 5397 → station computer** over USB, appearing as `/dev/ttyMAG0` (source: `mag-recorder/README.md` §Data flow).
- **Station computer → router**: ordinary wired Ethernet.

A powered USB **hub is fine** for the slow devices — `lsusb -t` on b4 shows the magnetometer adapter one VIA Labs hub deep and the GPSDO two hubs deep, both on the same 480 Mbit/s USB 2 bus, while the RX888 sits by itself on a 5 Gbit/s port of its own root hub (source: `lsusb -t` on b4, 2026-08-23). It is only the RX888 that must go straight to the machine.

## Approximate cost

Nothing in this table comes from a price list we maintain — these are
order-of-magnitude figures **as of 2026-08, check current prices**
before you buy anything.

| Item | Approximate | Required? |
|---|---|---|
| x86-64 mini-PC, 16 GB RAM, ~500 GB NVMe | $350–650 | yes |
| RX888 Mk II | $150–250 | yes |
| Broadband HF antenna + coax | $150–400 | yes |
| USB stick, 16–32 GB | $10 | yes |
| Leo Bodnar LBE-1421 GPSDO + GPS antenna | $200–320 | yes |
| **Required subtotal** | **~$860–1,630** | |
| TS-1 time injector (Turn Island Systems) + GPS antenna | $250–400 | optional |
| PNI RM3100 magnetometer board | $50–120 | optional |
| Pololu Isolated USB-to-I²C adapter (5397) | $35–50 | optional |
| u-blox ZED-F9P GNSS board + antenna | $200–350 | optional |
| **Full build (what B4 runs, minus the F9P)** | **~$1,195–2,200** | |

## What AC0G/B4 actually runs

This is the known-good build. Everything below was read from the live
station on 2026-08-23.

| | |
|---|---|
| Station name / callsign | `AC0G-B4`, reporter `AC0G/B4`, grid `EM38ww` (source: `/etc/sigmond/site-profile.toml`) |
| Machine | station runs as a VM on a Proxmox host; the VM sees **14 vCPUs**, ~9 GB RAM, a 252 GB root disk 52 % used (source: `nproc; free -g; df -h`) |
| Appliance image | v3.30, with 13 component updates applied since install (source: `smd version`) |
| SDR | RX888 Mk II, `04b4:00f1`, on its own USB 3.0 root hub (source: `lsusb`) |
| GPSDO | Leo Bodnar LBE-1421, `1dd2:2444`, serial `0C7BB80D10EF` — PLL locked, 3D fix on 7 satellites, OUT1 10 MHz, OUT2 27 MHz, PPS on (source: `lsusb`, `/run/gpsdo/0C7BB80D10EF.json`) |
| Time injector | TS-1, present as Adafruit Trinket M0 `239a:801e` (source: `lsusb`) |
| Magnetometer | RM3100 behind a Pololu Isolated USB-to-I²C adapter, `1ffb:2503` (source: `lsusb`) |
| Antenna | T3FD (terminated tilted folded dipole) (source: `/etc/sigmond/site-profile.toml`) |
| Network | wired, `lan-capable`, multicast querier live on `ens18` (source: `smd status`) |
| Running clients | `radiod`, `hf-timestd` (6 timing channels), `wspr-recorder` (17 channels), `psk-recorder` (19 channels, FT8/FT4), `meteor-scatter` (MSK144), `mag-recorder`, `gpsdo-monitor`, `hs-uploader`, `ka9q-web`, `gmag-webui` (source: `smd status`) |
| PSWS registration | station `S000170`; GRAPE instrument 171, magnetometer instrument 372 (source: `smd psws status`) |

Several entries in b4's `lsusb` are **not** station parts: a QEMU Tablet and
the VIA Labs hubs belong to the virtual machine and the host's own USB
tree, and an HP keyboard and an Intel AX200 Bluetooth radio are simply
what happens to be plugged into the box.

There is no single command today that reports "here is the hardware
attached to this station" — you assemble it from `smd admin environment`,
`smd watch gpsdo`, `lsusb`, and the magnetometer's log. Tracked as row 1
of [`../contributor/docs-gap-ledger.md`](../contributor/docs-gap-ledger.md).

## Things that look right but aren't

| Looks fine | What actually happens |
|---|---|
| Plugging the RX888 into a USB 2 port | USB 2 cannot carry 2 Gbit/s. You get sample loss, which shows up as gaps in recordings rather than as an error (source: `ka9q-radio/docs/SDR/rx888.md`; `sigmond/docs/PACKET-LOSS-DIAGNOSTICS.md`). |
| Putting a USB hub between the RX888 and the computer | Same outcome — starved transfers and silent loss. Hubs are fine for the GPSDO and the magnetometer, never for the radio. |
| Using Wi-Fi | The station's clients talk to `radiod` over *multicast*, which Wi-Fi handles badly; the appliance does not configure Wi-Fi at all (source: `sigmond-appliance/INSTALL.md` §1; `sigmond/docs/networking.md`). |
| Using a laptop as the station computer | The install hands the machine's USB controllers to the station VM, so the built-in keyboard and any monitor console stop working — **by design** (source: `sigmond-appliance/INSTALL.md` §8; `sigmond/docs/proxmox/wsprdaemon-proxmox-vm-setup.md`). The monitor still shows a login panel with the station's addresses — it is only the keyboard that dies (source: `sigmond-appliance/INSTALL.md` §8). On a mini-PC you shrug and use SSH. On a laptop you have bricked your keyboard. |
| Rebooting to make an unseen RX888 appear | A warm reboot never drops USB power, and the radio's FX3 chip stays latched as long as VBUS is held. **Power the whole box off**, then on — or physically unplug the radio (source: `sigmond/scripts/proxmox/sigmond-wizard.sh`). |
| Two RX888s on one host | The upstream author explicitly recommends against it for performance reasons (source: `ka9q-radio/docs/SDR/rx888.md` §serial). |
| 8 GB of RAM "because it boots fine" | The appliance asks for 16 GB or more, and the station VM plus the Proxmox host both need their share (source: `sigmond-appliance/INSTALL.md` §1). |
| A small disk with retention left at defaults | **Measured on AC0G/B4, 2026-08-23: about 15 GB of raw IQ per timing channel per day** — a full UTC day of one channel came to 15,073,610,352 bytes — so a six-channel station writes roughly **90 GB a day** of raw IQ; the cumulative `phase2` analysis database adds roughly **1 GB per channel per day** amortised, for **≈16 GB per channel per day, ≈96 GB a day** in total. `hf-timestd/INSTALLATION.md` §Storage Requirements says 6.7 GB and calls **9 channels on a 120 GB disk** the absolute margin; at the measured rate a 120 GB disk is barely a day even at six channels. Whichever number you believe, the failure mode is the same: it fills, and above 95 % the timing client starts deleting your oldest recordings ([day-2.md §3](../operator/day-2.md#3-disk--df--h-)). The disagreement is [docs-gap ledger row 20](../contributor/docs-gap-ledger.md). |

## Next

- Burning the image and the first boot: [sigmond-appliance/INSTALL.md](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md).
- Everything else an operator needs: [Operator guide](../operator/README.md).

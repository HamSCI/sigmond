# HamSCI / DASI2 station documentation

> **Audience:** all
> **Status:** current
> **Verified against:** sigmond f3b0c6e on 2026-11-18
> **Canonical for:** the front door

Pick the door that matches you. Each path stands on its own; the page tells
you when you need something from another path.

## I install a station
You have a computer and an RX888, and you want a working station. Sigmond
installs two ways, and each has its own guide.
→ **[Proxmox USB image](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md)** — for a dedicated mini-PC. The stick turns it into a Proxmox host with the station in a virtual machine. No Linux experience needed.
→ **[Bare metal](install-bare-metal.md)** — for a Debian machine you already run. `install.sh` and `smd bringup` add the station to it.

## I host a station (amateur radio operator)
You have, or want, a Sigmond station: a small PC running the HamSCI
receiver that sends WSPR, FT8, timing and magnetometer data on for science.
This door covers running one day to day. To install one, use the door above.
→ **[Operator guide](operator/README.md)** — the shopping list, the install, getting registered with the upload networks, the weekly check, remote access, troubleshooting, what not to touch, and a glossary of every term used.

## I want to record a signal (scientist / event responder)
You have a signal in mind (a beacon, an eclipse experiment, a time standard)
and want it captured on a station — possibly by Friday.
→ **[Scientist guide](scientist/README.md)** — the capability envelope, Tier-0 capture running in an afternoon, a worked example, where data lands and what its timestamps are worth, and graduating to a client, in reading order.

## I work on the code (contributor)
→ **[CONTRIBUTING.md](../CONTRIBUTING.md)** then **[Contributor guide](contributor/README.md)** — orchestration, the appliance ↔ sigmond boundary, dev setup, client authoring, and the conventions that keep these docs true.

## I administer stations (fleet administrator)
You look after many stations: you bless releases, align stations to them, watch the fleet and answer operators.
→ **[Administrator guide](admin/README.md)** — the fleet administrator's front page.

## Everything, by audience
[INDEX.md](INDEX.md) lists every page and marks the canonical one per topic.

## What this is
SigMonD orchestrates a ka9q-radio/RX888 station plus a family of independent
clients that share the DASI2 station hardware: WSPR, FT8/FT4 and MSK144 decoders,
HF time standards (hf-timestd), the magnetometer, CODAR, HFDL, beacon TEC and
meteor scatter. A client writes to a shared SQLite sink; `hs-uploader` ships
from it. Every WSPR, FT4/FT8 and MSK144 spot goes to wsprdaemon.org only, which
forwards it to wsprnet.org and pskreporter.info; a station posts to neither
directly. GRAPE and magnetometer packages go to PSWS. See the
[architecture diagram](architecture.png) for the whole-suite picture.

A release has the name "sigmond vN" (for example v3.93). It lists the blessed
version of every component. The Proxmox USB image and a bare-metal install both
deliver it, and `smd align` moves a running station to the latest one.

## State of the docs

The pages in this tree carry a "Verified against" line that names the sigmond
commit and date of the last check against the code. A page that shows an older
date has not yet had its accuracy pass. The
[`docs-gap`](https://github.com/HamSCI/sigmond/issues?q=label%3Adocs-gap) label
marks the known gaps between what these pages say and what the software does;
the [docs-gap ledger](contributor/docs-gap-ledger.md) lists every repo's issue
query.

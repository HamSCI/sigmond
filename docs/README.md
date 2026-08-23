# HamSCI / DASI2 station documentation

> **Audience:** all
> **Status:** current
> **Verified against:** sigmond dac759d on 2026-08-23 — docs
> **Canonical for:** the front door

Pick the door that matches you. Each path is self-contained; you will be
told when (and only when) you need something from another path.

## I host a station (amateur radio operator)
You have, or want, a Sigmond appliance: a small PC running the HamSCI
receiver that uploads WSPR / FT8 / timing / magnetometer data for science.
→ **[Operator guide](operator/README.md)** — the shopping list, the install, getting registered with the upload networks, the weekly check, remote access, troubleshooting, what not to touch, and a glossary of every term used.

## I want to record a signal (scientist / event responder)
You have a signal in mind (a beacon, an eclipse experiment, a time standard)
and want it captured on a station — possibly by Friday.
→ **[Scientist guide](scientist/README.md)** — the capability envelope, Tier-0 capture running in an afternoon, a worked example, where data lands and what its timestamps are worth, and graduating to a client, in reading order.

## I work on the code (contributor)
→ **[CONTRIBUTING.md](../CONTRIBUTING.md)** then **[Contributor guide](contributor/README.md)** — orchestration, the appliance ↔ sigmond boundary, dev setup, client authoring, and the conventions that keep these docs true.

## Everything, by audience
[INDEX.md](INDEX.md) lists every page and marks the canonical one per topic.

## What this is
SigMonD orchestrates a ka9q-radio/RX888 station plus a family of independent
clients (WSPR, FT8/FT4, HF time standards, magnetometer, CODAR, HFDL, beacon
TEC, meteor scatter) that share the DASI2 station hardware; data flows
client → shared SQLite sink → `hs-uploader` → wsprnet / pskreporter /
wsprdaemon / PSWS. See the [architecture diagram](architecture.png) for the
whole-suite picture.

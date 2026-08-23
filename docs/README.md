# HamSCI / DASI2 station documentation

> **Audience:** all
> **Status:** current
> **Verified against:** sigmond 9f42681 on 2026-08-23 — code
> **Canonical for:** the front door

Pick the door that matches you. Each path is self-contained; you will be
told when (and only when) you need something from another path.

## I host a station (amateur radio operator)
You have, or want, a Sigmond appliance: a small PC running the HamSCI
receiver that uploads WSPR / FT8 / timing / magnetometer data for science.
→ **[Operator guide](operator/README.md)** *(Phase 1 — until then use [sigmond-appliance/INSTALL.md](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md))* — shopping list, install, registration, the weekly check, what to do when it breaks, what not to touch.

## I want to record a signal (scientist / event responder)
You have a signal in mind (a beacon, an eclipse experiment, a time standard)
and want it captured on a station — possibly by Friday.
→ **[Scientist guide](scientist/README.md)** *(Phase 2 — until then start with [EVENT-CLIENT-PLAYBOOK.md](EVENT-CLIENT-PLAYBOOK.md) and [ka9q-python Getting Started](https://github.com/HamSCI/ka9q-python/blob/main/docs/GETTING_STARTED.md))*

## I work on the code (contributor)
→ **[CONTRIBUTING.md](../CONTRIBUTING.md)** then **[Contributor guide](contributor/README.md)** *(Phase 3 — until then: [docs-conventions.md](contributor/docs-conventions.md), [CLIENT-CONTRACT.md](CLIENT-CONTRACT.md), [ADD-A-CLIENT.md](ADD-A-CLIENT.md))*

## Everything, by audience
[INDEX.md](INDEX.md) lists every page and marks the canonical one per topic.

## What this is
SigMonD orchestrates a ka9q-radio/RX888 station plus a family of independent
clients (WSPR, FT8/FT4, HF time standards, magnetometer, CODAR, HFDL, beacon
TEC, meteor scatter) that share the DASI2 station hardware; data flows
client → shared SQLite sink → `hs-uploader` → wsprnet / pskreporter /
wsprdaemon / PSWS. See the [architecture diagram](architecture.png) for the
whole-suite picture.

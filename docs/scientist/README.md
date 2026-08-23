# Scientist guide

> **Audience:** scientist
> **Status:** current
> **Verified against:** sigmond 6a569b5 on 2026-08-23 — docs
> **Canonical for:** the scientist's table of contents

You have a signal in mind — a beacon, an eclipse experiment, a time
standard — and want it captured on a DASI2 station, possibly by Friday.
This page is the reading order; each page below links back here and to
its neighbours, so read them in this sequence once and you will not need
to hunt for the next one.

## The path

| # | Page | Time | What it settles |
|---|---|---|---|
| 1 | [EVENT-CLIENT-PLAYBOOK.md](../EVENT-CLIENT-PLAYBOOK.md) ★ | ~15 min | the design judgment — capture first, the channel envelope, what fails silently |
| 2 | [station-capabilities.md](station-capabilities.md) ★ | ~10 min | the envelope: frequency/rate/encoding menu, load budget, timing tiers, storage, what the station cannot do |
| 3 | [capture-quickstart.md](capture-quickstart.md) ★ | Tier 0 running in an afternoon | `event-recorder` or a proven ~330-line script, pre-flight, the WWV check |
| 4 | [data-and-timing.md](data-and-timing.md) ★ | ~10 min | where your bytes land and what the timestamp is actually worth |
| 5 | [becoming-a-client.md](becoming-a-client.md) ★ | when it recurs | graduating a one-shot capture into a sigmond client |

## Tier 0 vs Tier 1

| | **Tier 0 — capture only** | **Tier 1 — sigmond client** |
|---|---|---|
| What you get | one dynamic `radiod` channel with a `lifetime`, via `event-recorder` or your own script; your own files, your own upload | a repo sigmond installs, supervises with systemd, and can ship through `hs-uploader`; visible in `smd status` |
| What you owe | nothing to the fleet — no contract, no unit | the seven things: a repo, `deploy.toml`, a templated unit, the four contract subcommands, a config template, `[client_features]` blocks, a catalog entry ([becoming-a-client.md §The seven things](becoming-a-client.md#the-seven-things-you-must-ship)) |
| When to upgrade | the experiment is one-shot, or the analysis is still changing daily | it recurs or runs indefinitely, someone else needs to see it is healthy, you want the fleet update/heartbeat lifecycle, or output should leave the station ([becoming-a-client.md §When to graduate](becoming-a-client.md#when-to-graduate--and-when-not-to)) |

The [2026-08-12 eclipse listener](costas-14110-worked-example.md) stayed
Tier 0 and that was the right call — read why before assuming Tier 1 is
the more serious choice.

## What you need from the fleet admin

- **Station access.** You reach the decoder VM, not the Proxmox host — the
  operator route in [operator/README.md §Two machines in one box](../operator/README.md#two-machines-in-one-box).
- **Load approval before adding channels.** More than a couple of extra
  channels, anything ≥ 96 kHz, or any real-time processing needs a
  conversation first — [station-capabilities.md §the load budget](station-capabilities.md#how-many-channels-you-may-add--the-load-budget).
- **An agreed output directory.** Never `/var/lib` — settle where your
  bytes go before the event, not during it
  ([capture-quickstart.md](capture-quickstart.md)).

## What the station will not do for you

One RX888 per host, nothing leaves the host by default, no Wi-Fi, no
inbound ports, nanosecond timing needs hardware (TS-1) that may be
absent, and you are one client among several on a shared channel — the
full list with sources is
[station-capabilities.md §What the station cannot do](station-capabilities.md#what-the-station-cannot-do).

## The worked example

[costas-14110-worked-example.md](costas-14110-worked-example.md) ★ walks
the real eclipse capture end to end — what was decided, what was checked,
what the data said, and the eight things worth doing differently. Read it
alongside capture-quickstart.md as the "this is what it looks like when
it works" companion to the recipe.

## The hardware underneath

[../hardware/character.md](../hardware/character.md) ★ — how the RX888
and radiod actually behave, especially when they misbehave: every failure
mode there is silent, none raises an exception.

## Words you don't know

[../operator/glossary.md](../operator/glossary.md) has plain-English
definitions for station vocabulary this guide assumes.

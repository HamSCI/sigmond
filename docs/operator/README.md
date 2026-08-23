# Operator guide — hosting a HamSCI station

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 4aec0c2 on 2026-08-23 — walk-through fixes (live dasi002 + b4)
> **Canonical for:** the operator's table of contents

You are hosting a *station*: one small computer that listens to the whole
shortwave band, all the time, and sends what it hears to science. This page is
the contents page; unfamiliar words are in the [glossary](glossary.md).

> **About the "Verified against" line at the top of every page here:** it names
> the commit *the page* was checked against, not your station. `smd version` on
> your station will print different commits, and different pages here name
> different ones — that is expected, not a warning sign.

## What you are signing up for (10-minute version)

- **The kit.** A small always-on x86-64 PC, an RX888 Mk II receiver, a
  broadband HF antenna, a GPS-disciplined clock (*GPSDO*), and a **wired**
  Ethernet cable — no Wi-Fi. Models and cost → [hardware.md](hardware.md).
- **What it does.** It runs by itself, 24/7, uploading WSPR spots to
  [wsprnet.org](https://wsprnet.org), FT8/FT4 spots to
  [pskreporter.info](https://pskreporter.info), copies of both to
  [wsprdaemon.org](https://wsprdaemon.org), and daily HF time-standard —
  plus magnetometer, if you have one — products to HamSCI's
  [PSWS](https://pswsnetwork.eng.ua.edu/).
- **Your job.** Keep it powered, keep the cable in, glance at one page a week,
  and tell your fleet admin when something goes wrong. No Linux experience
  needed: one USB stick installs it all, and every command you need is here.
- **If you are *not* installing from the appliance USB image** — building from
  source, changing code — you are a contributor: see [CONTRIBUTING.md](../../CONTRIBUTING.md).

## The path

Do these in order. Only steps 1 and 2 need you physically at the machine.

| Step | Page | Time |
|---|---|---|
| 1. Buy the parts | [hardware.md](hardware.md) | an evening of shopping |
| 2. Install from the USB stick | [install.md](install.md) | ~45 min |
| 3. Register your station's uploads | [registration.md](registration.md) | 30 min + portal waits |
| 4. Learn what "healthy" looks like | [day-2.md](day-2.md) | 15 min |
| 5. (optional) Let the fleet admin reach it | [remote-access.md](remote-access.md) | 10 min |
| When it breaks | [troubleshooting.md](troubleshooting.md) | — |
| Before you touch anything | [do-not-touch.md](do-not-touch.md) | — |
| Words used in these pages | [glossary.md](glossary.md) | — |

## Two machines in one box

One physical computer runs **two** systems. This trips everybody up once:

| Tag | What it is | Its name | You reach it by |
|---|---|---|---|
| `[host]` | The **Proxmox host** — the bare machine, the thing with the power button and the USB ports | `<designator>-PM`, e.g. `AC0G-B4-PM` | `ssh root@<host address>`, or a browser at `https://<host address>:8006` |
| `[VM]` | The **decoder VM** — a virtual machine running inside the host; the radio and every recorder live here | `<designator>`, e.g. `AC0G-B4` | `ssh hamsci@<VM address>` (or `sigmond@`) |

The *designator* is the station name you give the setup wizard, which names the
VM after it and adds `-PM` for the host (source:
`sigmond/scripts/proxmox/sigmond-wizard.sh`, `ask_names()`). Both addresses
print on the station's monitor; one password unlocks both at first — change it
with `passwd` in each ([INSTALL.md §10](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#10-logins--and-change-the-password)).

⚠ **Write down BOTH addresses while the install is showing them — the host one
especially.** After the first reboot the station's keyboard stops working on
purpose (the USB ports go to the radio), so that screen is the last easy chance
to read the host address. Several later steps — the Proxmox window, every
remote-access check in [remote-access.md §4](remote-access.md#4-turning-it-on-off-and-checking-it--all-on-the-host) —
run on the `[host]` and are simply unreachable without it. If you have already
lost it, your fleet admin or your router's DHCP list can recover it — it is a
nuisance, not a dead end.

**Every command here is tagged `[host]` or `[VM]`** — the tag sits on the line
above the command, never inside it, so a whole block is safe to copy. Nearly
all are `[VM]`; the host matters only for the Proxmox GUI and a dead box.

## The weekly check

Five minutes a week; [day-2.md](day-2.md) defines it properly, in four
steps. The one command to know now — `[VM]`, on the decoder VM:

```bash
smd status
```

Most lines should be ticked; a few ✗ and ⚠ are normal, and
[day-2.md](day-2.md) says which.
If a client **you expect to be enabled** has no block at all, or `radiod` is not
active, ask. (`smd status` lists only *[enabled](glossary.md)* clients, so a
client that is *[installed](glossary.md)* and deliberately switched off shows
nothing and that is correct — `smd component list` is the command that shows
both, see [day-2.md §1](day-2.md#installed-enabled-shown).)
Then the web pages, from any computer on your network: `http://<VM address>:8081` (live
receiver), `http://<VM address>:8000` (timing dashboard) and
`http://<VM address>:8082` (magnetometer dashboard, if you have one) — ports
confirmed live on AC0G/B4, 2026-08-23.

## Getting help — what to send

Ask early. Send these four outputs plus your designator and grid square, and
most questions get answered in one round trip — `[VM]`, on the decoder VM:

```bash
smd doctor
smd status
smd version
cat /etc/sigmond-appliance/version    # the image, not what you run now
```

That last file is written once at install time and never changes, so it is the
image you installed from, not what you are running now — `smd version` is.

**Where to send it:** your fleet admin (the person who gave you the image) or
the HamSCI DASI2 operators group via <https://hamsci.org/>.

**One thing you will notice when you follow the links:** some explanations of a
⚠ or ✗ line end by pointing at the
[docs-gap ledger](../contributor/docs-gap-ledger.md) — a list of known gaps
between what the software reports and what these pages would like to tell you.
It is marked *Audience: contributor*, and that is deliberate: it names source
files rather than actions. You may read it, and it is the honest answer to "why
does my healthy station print this every week?" — it just is not written for
you, and there is never anything in it for you to do.

# Day 2 — what healthy looks like and the weekly check

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 09d44f8 on 2026-08-23 — live b4 + dasi002 (smd status/version/doctor, smd update dry run, df -h, /etc/sigmond-appliance/version, ss -ltnp) + code/docs
> **Canonical for:** day-2 operation — what healthy looks like, the weekly check, updates, power loss

The station is meant to be boring. It runs itself, it restarts itself after a
power cut, and it does not need you on a schedule. What it does need is five
minutes a week from somebody who knows what "fine" looks like — because the
failure that matters is the quiet one, where everything still says *active* and
no data has left the building in three days.

This page is that five minutes. Words you don't recognise are in the
[glossary](glossary.md).

---

## The four windows

Four web pages, from any computer on the same network as the station. None
need a login except Proxmox. You do not have to look at all four every week —
step 1 of [the weekly check](#the-weekly-five-minute-check) covers most of it —
but these are where "is it actually working?" is answered with your own eyes.

| Window | Address | What "good" looks like |
|---|---|---|
| **Live receiver** (ka9q-web) | `http://<VM address>:8081` | A waterfall with **signals in it** — bright horizontal traces, not a flat empty wash. This is the one page that proves the antenna, the [RX888](glossary.md) and [radiod](glossary.md) are all doing their jobs ([INSTALL.md §9](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#9-fifteen-minutes-later--check-its-alive)). |
| **Timing dashboard** (hf-timestd) | `http://<VM address>:8000` | The page loads and its health/metrology panels show **recent** timestamps and a current timing tier — the same tier `smd status` prints. (`hf-timestd/docs/REQUIREMENTS.md` `HFT-F-050`: a FastAPI dashboard on port 8000 serving station/health/metrology/stability/propagation routes.) |
| **Magnetometer dashboard** (gmag-webui) — only if you have an RM3100 | `http://<VM address>:8082` | A trace that **moves**. Geomagnetic field readings always wiggle; a dead-flat unchanging line is a stuck sensor, not a quiet day. Report it. (Port: `lib/sigmond/gmag_webui.py`, `PORT = 8082`.) |
| **Proxmox** (the `[host]`) | `https://<host address>:8006` | Your one [decoder VM](glossary.md) — the one named after your [designator](glossary.md) — shows **running**. That is all you ever need Proxmox for. |

Ports 8081, 8000 and 8082 were confirmed listening on AC0G/B4 on 2026-08-23
(`ss -ltnp`); on dasi002 the same check found 8081 and 8000 only — no
magnetometer dashboard was running there that morning.

If a page will not load at all, that is a real finding — take it to
[troubleshooting.md → *Web pages don't load but ssh works*](troubleshooting.md#web-pages-dont-load-but-ssh-works).

---

## The weekly five-minute check

Four steps. Steps 1–3 every week; step 4 only when one of the first three looks
wrong.

Run these on the **decoder VM** — `[VM]`, i.e. after `ssh hamsci@<VM address>`:

```bash
smd status
df -h /
```

### 1. `smd status` — is everything running?

This is the one command to know. It walks every installed client, prints each
systemd unit with a ✓ or ✗, then adds the CPU-affinity summary, the timing
judge, and the network check.

**A few ✗ and ⚠ are normal.** Here is real, lightly trimmed output from
**AC0G/B4** — a healthy production station, and the fleet
[canary](glossary.md) — captured 2026-08-23:

```text
sudo: a password is required
sudo: a password is required

━━━ status ━━━

  gmag-webui:
    ✓  gmag-webui.service: active

  gpsdo-monitor:
    ✓  gpsdo-monitor.service: active

  hf-timestd:
    ✓  timestd-core-recorder.service: active
    ✓  timestd-metrology.target: active
    ✓  timestd-fusion.service: active
    ✓  timestd-web-api.service: active
    ...  (15 more hf-timestd services and timers, all active)
    ✓  timestd-metrology@WWV_25000.service: active

  ka9q-radio:
    ✓  radiod@AC0G-B4.service: active
    ✗  radiod@AC0G-B4-patched.service: inactive

  ka9q-web:
    ✓  ka9q-web.service: active

  mag-recorder:
    ✓  mag-recorder.service: active

  meteor-scatter:
    ✓  meteor-scatter@AC0G=B4.service: active
    ✓  meteor-scatter@my-rx888.service: active [orphaned]

  psk-recorder:
    ✓  psk-recorder@AC0G=B4.service: active

  wspr-recorder:
    ✓  wspr-recorder@AC0G=B4.service: active

  ...  (4 more client blocks: hf-timestd, mag-recorder, meteor-scatter,
        psk-recorder — all ✓)
  ✓  wspr-recorder  v0.1.0  (d96a0a2)  contract=0.8
     AC0G-B4: 17 ch, modes=F15,F2,F30,F5,W2

  CPU affinity:
     radiod cores: [10, 11, 12, 13] (other pool: 10 CPUs)
  ⚠  22 pinned process(es) overlap radiod cores

  timing judge:
  ✓  judge T4  σ=666.9 µs  age 0s  gpsdo=locked
  ✓  619e5d2: offset +2.656 ms, rate -0.059 ppm, T4, seg 1
  ✗  1926159f: OFFSET VIOLATION — offset +9.833 ms, rate -0.058 ppm, T4, seg 1
  ✗  4ad803f2: OFFSET VIOLATION — offset +19.408 ms, rate -0.058 ppm, T4, seg 1
  ...  (3 more sources: 1 ✓, 2 more OFFSET VIOLATION)

  network:
  ✓  lan-capable (checked 786m ago)
     querier: v2 <a LAN address> on ens18

━━━ PSWS upload not finished ━━━
  ⚠  hf-timestd: SSH key missing: /home/timestd/.ssh/id_rsa_psws
            finish:  smd config hf-timestd edit   (records locally regardless)
  ...  (1 more: the same ⚠ for mag-recorder)
```

**Every ✗ and ⚠ in that output is normal today.** Here is why, line by line:

| Line | Normal? | Why |
|---|---|---|
| `sudo: a password is required` (twice, before the banner) | **Normal — ignore** | A read-only command reaching for `sudo` it does not have and carrying on regardless. It is noise on stderr, not a failure, and it appears on b4 but not dasi002. Known and tracked — [docs-gap ledger row 6](../contributor/docs-gap-ledger.md). |
| `✗ radiod@AC0G-B4-patched.service: inactive` | **Normal** | A *second, deliberately disabled* `radiod` unit. B4 keeps two `radiod` configs in `/etc/radio/` — the live one and a spare "patched" variant. `systemctl is-enabled` reads `enabled` for `radiod@AC0G-B4` and `disabled` for `-patched` (checked live, 2026-08-23). Only one radiod can own the RX888, so the other one being down is the correct state. Most stations have only one and never see this line. |
| `✓ meteor-scatter@my-rx888.service: active [orphaned]` | **Normal-ish — mention it** | `[orphaned]` means a unit is running that the current config no longer declares. Harmless, but worth naming to your fleet admin so it gets tidied. |
| `⚠ 22 pinned process(es) overlap radiod cores` | **Normal — every station shows it** | The station reserves CPU cores for `radiod` and pins the decoders elsewhere. This line counts userspace processes whose CPU mask still overlaps radiod's cores (`lib/sigmond/cpu.py`, `find_contending_processes`; kernel threads and ka9q-radio's own mDNS helpers are already excluded). Both fleet stations carry it — b4 22, dasi002 18 on 2026-08-23 — while producing good data, so today it is the fleet's normal state rather than a fault. Nothing here is yours to fix; mention the number if it changes a lot. Tracked — [docs-gap ledger row 12](../contributor/docs-gap-ledger.md). |
| `✗ … OFFSET VIOLATION — offset +9.833 ms …` | **Normal today — known and tracked** | The **timing judge** compares each `radiod` channel's advertised epoch against the station's best clock evidence and flags any channel that disagrees by more than *k×σ* for longer than 60 s (`hf-timestd/src/hf_timestd/core/offset_judge.py`, `_evaluate_violation_locked`). It is a **detector, not a fault**: hf-timestd's own data labels stay corrected regardless — the judge's own log line says so ("labels remain CORRECTED … radiod's advertised epoch is contradicted"). B4 was flagging four of its six timing channels at 5–19 ms on 2026-08-23 while producing good data all day; dasi002, on the weaker T3 evidence, was flagging all six at 200–650 ms. **What matters to you is the summary line above them**: `judge T4 σ=666.9 µs gpsdo=locked`. `gpsdo=locked` is the healthy word; the tier is information for your admin about how good the clock evidence is, not something the software grades. |
| `━━━ PSWS upload not finished ━━━` on a station that *is* enrolled | **Normal — a known contradiction** | B4 is fully enrolled (`smd psws status` reports `✓ key verified 2026-08-17`), yet `smd status` still prints this block, because it checks for the *older* per-recorder key files that a station-key host does not use. Two enrolment models coexist and disagree in your face. Tracked — [docs-gap ledger rows 7 and 10](../contributor/docs-gap-ledger.md). Confirm your real enrolment state with `smd psws status`, not with this block. |

**The three lines that actually matter**, in order:

1. **`radiod@<designator>.service: active`.** If `radiod` is down, nothing on
   the station works — no spots, no timing, no GRAPE. Everything else on the
   page is downstream of this one line.
2. **The judge summary** — `judge T4 σ=666.9 µs age 0s gpsdo=locked`. The
   [timing tier](glossary.md) tells your admin how good the station's clock
   evidence is right now; **the word to watch is `gpsdo=`**. That is the only
   part of this line the software itself grades: `locked` prints ✓, while
   `holdover` or `unlocked` prints ⚠ (`lib/sigmond/timing_judge.py`,
   `render_status_lines`) — it means the [GPSDO](glossary.md) has lost its GPS
   fix. dasi002 read `⚠ judge T3 σ=3107.9 µs gpsdo=holdover` on the same
   morning: that ⚠ is the software telling the truth about a real condition,
   and it is the shape of thing to report —
   [troubleshooting.md → *GPS not locked*](troubleshooting.md#gps-not-locked-or-the-timing-dashboard-is-red).
3. **A whole client missing, or a unit `failed`.** `inactive` on a spare unit is
   fine (see the table); `failed` is not. dasi002 shows
   `✗ mag-recorder.service: failed` — that is a genuine finding, not background
   noise, and it is chased in
   [troubleshooting.md → *After a power cut everything is back except one client*](troubleshooting.md#after-a-power-cut-everything-is-back-except-one-client).

If any of those three is wrong, go to step 4 and then to your fleet admin.

### 2. Are your spots arriving?

The station can be perfectly healthy and still be shouting into a void — a
broken upload path looks identical from inside the box. So check the outside
world: search your [reporter ID](glossary.md) on [wsprnet](glossary.md) and your callsign as receiver
on pskreporter.info.

Where to look, what counts as arrived, and how long each product normally
takes is the table in
[registration.md §6 — Confirming everything flows](registration.md#6-confirming-everything-flows).
Don't duplicate the judgement here; that table is canonical.

One caveat worth remembering weekly: **no spots is not automatically a fault.**
Propagation dies, bands go empty, and a quiet afternoon produces nothing from a
flawless station. A week with *zero* spots is a fault. An hour with none is
weather.

### 3. Disk — `df -h /`

The one number that can quietly destroy data. hf-timestd records raw IQ at
roughly 18 GB per channel per day and manages its own eviction, but it also has
percentage-based safety nets for when *something else* fills the disk
(`hf-timestd/src/hf_timestd/core/resource_guardian.py`):

| `Use%` on `/` | What happens |
|---|---|
| under 80% | Normal. Nothing to do. |
| **80%** | hf-timestd logs a warning (`DISK_WARN_PERCENT = 80.0`). Worth mentioning to your fleet admin. |
| **95%** | Hard stop: hf-timestd **pauses all writes** and alerts (`DISK_HARD_STOP_PERCENT = 95.0`). If it stays ≥95% for 10 minutes (`EVICT_GRACE_SEC = 600`), it begins **deleting your oldest recordings** — smallest regenerable unit first — until the disk is back under 90% (`EVICT_LOW_WATER_PERCENT = 90.0`). |

So 95% is the number that costs you data. Live on 2026-08-23: b4 was at **52%**
(126 G of 252 G), dasi002 at **86%** (201 G of 245 G) — dasi002 is the one to
watch. If you are over 80% and climbing, tell your fleet admin *before* it
reaches 95%; do not start deleting files yourself
(→ [do-not-touch.md](do-not-touch.md)).

### 4. `smd doctor` — only when something looks off

`smd doctor` reports damage to the station's software checkouts: files with the
wrong owner, uncommitted edits, a checkout pinned to a specific commit. It is
read-only. **Do not run `smd doctor --fix` unless your fleet admin tells you
to** — `--fix` repairs file ownership and nothing else, but it is still a
change to a working station, and this is not the step where you guess.

`[VM]`:

```bash
smd doctor
```

Healthy output is not empty. Real b4 output, 2026-08-23 (trimmed to two of
the six components it named):

```text
ft8_lib:
    untracked: 1 untracked path(s): .pin (harmless in place; `git add -A` would commit them)
    detached: HEAD is detached
hf-timestd:
    ownership: 20 path(s) not owned by sigmond (e.g. CLAUDE.md.bak-doctor-20260819) [--fix repairs this]
     3 finding(s) repairable with: smd doctor --fix
```

`detached: HEAD is detached` means that component is deliberately pinned to a
tested commit — expected, not damage. `untracked` and `ownership` findings are
housekeeping. The tool says so itself where it can. Send the whole output to
your fleet admin rather than acting on it.

---

## Updates — who decides, and what you run

There are exactly two ways a station gets updated, and they are never mixed
(`CONTRIBUTING.md` [§3](../../CONTRIBUTING.md#3-updating--two-orientations-never-mixed)).

**Station-inward (you pull).** You are on your own machine and it looks *out*
to the repositories. `smd update` on its own is a **dry run**: it changes
nothing and prints the plan it *would* execute (`--apply` is documented in the
CLI as "perform the mechanical steps (default: dry run)"). Read the plan first,
always — `[VM]`:

```bash
smd update
```

Real b4 output, 2026-08-23:

```text
(dry run — re-run with --apply)

  [wisdom] ka9q-radio
     2 plan(s) falling back to FFTW_ESTIMATE: cob100 cif100 — fft-gen --patient -T <fft-threads>
     verify: wc -l /var/lib/ka9q-radio/fft.log == 0

  [restart] services
     radiod first, then the recorders once it is stable — a recorder started against a restarting radiod picks up a bad anchor
     verify: systemctl is-active for each unit; journal free of errors
```

A station with nothing to do says so plainly — dasi002 printed
`✓ host is current — nothing to do` on the same morning. The plan is
idempotent and safe to re-run.

**"Current" is not always true.** The plan is only as fresh as the station's
last fetch from the repositories; planning against cached refs means "a stale
host will report itself current" (`bin/smd`, the `--no-fetch` help text), and
older sigmond builds could land in that state without being asked. So if your
fleet admin says a release is out and `smd update` answers
`host is current`, **tell them rather than assuming**. `smd version` prints the
actual commit of every component, and that is the number that settles it.

To actually perform it: `smd update --apply`. **Do this only when your fleet
admin tells you the release is blessed.** That is not bureaucracy — the fleet
runs every release through one [canary](glossary.md) station (today, AC0G/B4) before anyone
else takes it, so a bad build breaks one station instead of all of them.

**Fleet-outward (the admin pushes).** Your fleet admin may roll the update to
you instead, one host at a time, canary first. It is the same mechanism running
on your box; they will tell you when it happens. The fan-out they use can only
*ask* questions — `--apply` is structurally impossible through it — so nothing
mutates your station without a decision aimed at your station specifically.

Never prefix `smd` with `sudo`; it elevates itself and refuses to run under
`sudo` anyway.

### Knowing what you are actually running

`[VM]`:

```bash
smd version
```

It prints the image lineage, then every component's live commit, then the log
of updates applied since install. The line that matters is the last one:

```text
image:      v3.30   [image installed on this host — lineage only; components may have moved since (see below)]
...
updates since install (13):
    2026-08-22T16:26:24Z  smd update --apply: install mag-recorder, install sigmond, ...
    -> this host has moved since v3.30 was installed
```

**`cat /etc/sigmond-appliance/version` is not your version.** That file is
written once, at install time, and nothing ever updates it — so after any
update it states something false. dasi002 reads `v3.20` while running
v3.31-era components (`CONTRIBUTING.md` §3). Quote it when asked, because it
tells your admin which image you started from; never read it as "what I am
running now."

### Three things never to do by hand

- **Never `apt upgrade`.** The station's radio and several of its decoders are
  native binaries built against exactly the system packages that are on it
  now, and nothing on the station stops you moving them.
- **Never `pip install`.** Every component lives in a managed virtual
  environment; a hand-installed package silently breaks the next update.
- **Never `git pull`** in `/opt/git/sigmond/…`. That is exactly what
  `smd update` does, in the right order, with the right ownership.

Full list and reasoning → [do-not-touch.md](do-not-touch.md).

---

## Power loss, reboots, and moving the box

**It comes back by itself.** Power cut, breaker trip, someone unplugging the
wrong thing — the host boots, the decoder VM starts, `radiod` starts, the
recorders start, and spots resume. **Allow about 10 minutes** before you judge
it — a fresh install budgets fifteen before it expects signs of life
([INSTALL.md §9, "Fifteen minutes later — check it's alive"](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#9-fifteen-minutes-later--check-its-alive)),
and a reboot is the easier case. Do not power-cycle it again while you wait.

If spots have not resumed after **30 minutes**, that is a fault: check the
antenna is still connected, then run `smd status` and send the output to your
fleet admin ([troubleshooting.md](troubleshooting.md);
[INSTALL.md §11](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#11-if-something-goes-wrong)).

**A dead keyboard on the station itself is normal.** After the first reboot
following installation, the USB ports belong to the decoder VM — the radio
needs them. The physical keyboard stops working and the monitor shows a login
panel with both addresses. That is correct and deliberate; you drive the
station from another computer over the network from then on
([INSTALL.md §8](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#8-remove-the-stick-when-told--done)).
You can unplug the monitor and keyboard whenever you like.

**Moving the station to a new location** — a different [grid square](glossary.md) — is a
documented procedure, not a reinstall: log into the `[host]`, run
`sigmond-setup --reconfigure`, type the new grid square, and press Enter
through everything else. The new location flows everywhere automatically and
the recorders restart themselves. Full steps:
[INSTALL.md §12](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#12-moving-a-station-staged-in-one-place-deployed-in-another).

---

## The heartbeat — what your station tells the fleet

Every **5 minutes**, your station assembles a small status record and ships it
to the fleet board, where your admin watches it
(`CONTRIBUTING.md` [§10](../../CONTRIBUTING.md#10-fleet-situational-awareness--the-heartbeat-and-the-board);
`interval_sec = 300` in the live config on both b4 and dasi002).

Three things worth knowing:

- **Availability is judged by arrival, not by self-report.** A station that
  goes silent past three intervals turns red on the board no matter how healthy
  its last message claimed to be. Silence *is* the signal — that is the whole
  design.
- **It is never switched off by the upload policy.** A station whose outbound
  uploads are disabled (dasi002, deliberately) still heartbeats, so it still
  appears on the board.
- **Nothing pages anybody.** The board is the entire interface. No emails, no
  alerts. If your station goes dark on a Friday night, it is seen when someone
  looks.

**You cannot confirm from the station that the heartbeat is landing.** There is
a local verb, `smd admin heartbeat show`, but on a *healthy* station it reports
`no heartbeat ticks` — because the uploader has already shipped and removed
them (verified live on b4, 2026-08-23: timer enabled and firing every 5
minutes, spool directory empty). So the honest answer is: **your fleet admin
sees it on the board; ask them.** Tracked as a gap —
[docs-gap ledger row 11](../contributor/docs-gap-ledger.md).

---

## Passwords and logins

One password unlocks both machines at first, and the factory default is
published in the install guide — so **change it**. There are two places, and
changing one does not change the other:

`[host]` — on the Proxmox host, after `ssh root@<host address>`:

```bash
passwd
```

`[VM]` — on the decoder VM, after `ssh hamsci@<VM address>`:

```bash
passwd
```

Full detail, including the factory default and the Proxmox GUI login:
[INSTALL.md §10](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#10-logins--and-change-the-password).

If you typed a wrong answer to the setup wizard — reporter ID, grid square,
PSWS ids — you do not reinstall: run `sigmond-setup --reconfigure` from the
`[host]` and press Enter through everything you want to keep.

---

## When it goes wrong

Send your fleet admin these four outputs plus your designator and grid square
— the same set the [operator front page](README.md#getting-help--what-to-send)
asks for. `[VM]`:

```bash
smd doctor
smd status
smd version
cat /etc/sigmond-appliance/version    # the image, not what you run now
```

Ask early. A station that has been quietly broken for a week has lost a week of
science, and nobody minds a false alarm.

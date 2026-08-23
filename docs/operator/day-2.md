# Day 2 — what healthy looks like and the weekly check

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 4aec0c2 on 2026-08-23 — walk-through pass 2 fixes (live dasi002 + b4)
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

Only two of the four are commands. **Step 2 is a web search** — you look
yourself up on wsprnet and pskreporter, there is nothing to type on the station
— and **step 4 is `smd doctor`**, which you run only when one of the first three
looked wrong. So the block below is steps 1 and 3, which is the whole of a
normal week. Run it on the **decoder VM** — `[VM]`, i.e. after
`ssh hamsci@<VM address>`:

```bash
smd status
df -h /
```

If you would rather copy all four at once, this is the whole check — the middle
line is a reminder, not a command. Step 4 is listed as "only when something
looks off" because that is when it *tells* you anything — `smd doctor` is
read-only and harmless to run weekly, it just prints the same housekeeping
findings every time on a healthy station. `[VM]`:

```bash
smd status                      # step 1
#                                 step 2: look yourself up on wsprnet / pskreporter
df -h /                         # step 3
smd doctor                      # step 4 — read-only; never add --fix
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

  ...  (1 more client block: igmp-querier — ✓)

  ─── second half: one summary line per recording client, not per unit ───
  ...  (4 more summary lines: hf-timestd, mag-recorder, meteor-scatter,
        psk-recorder — all ✓, none with issues under them)
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

A station with fewer optional parts prints a few lines b4 does not. Here are
the ones dasi002 — a testbed with no magnetometer, no dual-frequency GNSS and no
PSWS enrolment — added on the same morning, all of them normal:

```text
  ✗  station.psws_station_id is unset (need PSWS-issued S0xxxxx)     ← above the banner

  gmag-webui:
    ✗  gmag-webui.service: inactive
  hf-timestd:
    ✗  timestd-vtec.service: inactive
  igmp-querier:
    ✓  igmp-querier.service: active
  mag-recorder:
    ✗  mag-recorder.service: failed

  mag-recorder  v0.1.0  (8551d27)  contract=0.8                       ← note: no ✓
  ⚠  station.callsign is unset
  ⚠  uploader.ssh_key_file does not exist: /etc/hs-uploader/keys/id_ed25519
     (shared with hf-timestd Grape uploader; ssh-keygen + register key on PSWS portal first)
```

**Two structural things to know about that output before the table.**

First, `smd status` has **two halves**. The top half is one block per client
listing its systemd **units**, each with ✓ or ✗. The bottom half is one
**summary line** per client — `✓ hf-timestd v7.0.0 (4dfaaf7) contract=0.8` — and
those are different things with different rules. Second, **not every client
produces a summary line.** Only clients that report an inventory do; on dasi002
just two of seven did (`hf-timestd` and `mag-recorder`), and `gmag-webui`,
`gpsdo-monitor`, `igmp-querier`, `ka9q-radio` and `ka9q-web` got a unit block and
nothing else. **A client with no summary line is not a client with a problem** — only
*recording* clients print one. `smd status` emits a summary line for a component
the catalog marks `kind = "client"` and skips the rest (`bin/smd`, the inventory
loop: `if entry.kind != 'client': continue`), so the radio, the two web servers
and the two infrastructure services never get one. Live 2026-08-23: b4 printed
10 unit blocks and **5** summary lines (hf-timestd, mag-recorder,
meteor-scatter, psk-recorder, wspr-recorder); dasi002 printed 7 blocks and
**2** (hf-timestd, mag-recorder). The glyph rules below apply only to clients
that print one.

**Every ✗ and ⚠ in either output is normal today** — except `mag-recorder`'s
`failed`, which is the one genuine finding on that station. Here is why, line by
line:

| Line | Normal? | Why |
|---|---|---|
| `✗ station.psws_station_id is unset (need PSWS-issued S0xxxxx)`, *before* the banner | **Normal on a station with no PSWS enrolment** | This is not a station-wide check and it is not about `/etc/sigmond/site-profile.toml`. It is **mag-recorder's own config check** — `mag_recorder/contract.py`, `_collect_issues()`, which raises `severity: fail` when `[station] psws_station_id` in `[VM]` `/etc/mag-recorder/mag-recorder-config.toml` is missing or still holds its `<YOUR_PSWS_STATION_ID>` template placeholder. It surfaces above the banner because the client is asked for its inventory before the banner prints. On a station that never enrolled in PSWS, or has no magnetometer, this is the expected state. To see your station's PSWS position in plain English run `smd psws status` — but note it answers about the **site** profile (`[psws] disabled in site-profile.toml — nothing to do` on dasi002), not about mag-recorder's file, so it confirms "this station has no PSWS enrolment, and that is deliberate" rather than confirming this particular line ([registration.md §1](registration.md#1-what-the-wizard-already-did)). |
| `✗ gmag-webui.service: inactive` | **Normal without a magnetometer dashboard** | `gmag-webui` is the port-8082 magnetometer web page ([the four windows](#the-four-windows)). It is installed on every station but only worth running where there is an RM3100 feeding it, so on a station without one it sits `inactive`. dasi002 printed this on 2026-08-23; b4, which has the sensor, printed `✓ active`. |
| `✗ timestd-vtec.service: inactive` | **Normal without a dual-frequency GNSS receiver** | [vTEC](glossary.md) is the ionospheric total-electron-content product, and computing it needs an optional dual-frequency GNSS receiver (a u-blox ZED-F9P) that most stations do not have. `timestd-vtec.service` is enabled only where a GNSS receiver is configured, and left disabled elsewhere: on 2026-08-23 b4 read `UnitFileState=enabled  ActiveState=active` against its networked GNSS, while dasi002 read `UnitFileState=disabled  ActiveState=inactive`. Either way, **`inactive` on a station without a dual-frequency GNSS receiver is normal, not a fault** — you would only report it if you know your station has one. |
| A client-summary line with **no glyph at all** — e.g. `mag-recorder  v0.1.0  (8551d27)  contract=0.8` where `hf-timestd` above it reads `✓  hf-timestd …` | **Not an error — it means "this client reported something", and the something is printed underneath** | The ✓ on those summary lines means *validated clean*: `bin/smd` sets it only when the client returned an empty issue list (`clean_tag = '✓ ' if not issues else ''`), specifically so an operator can tell "checked, clean" from "not checked". No glyph therefore means the client returned one or more issues — and every one of them is printed immediately below that line as its own ⚠ or ✗. So read the lines under a glyph-less client, not the missing glyph itself. |
| `⚠ station.callsign is unset` on a station whose `site-profile.toml` **does** have a callsign | **Normal — different file** | Same origin as the row above: it is one of **mag-recorder's** config issues (`mag_recorder/contract.py`, `_collect_issues()` — `[station] callsign` in `[VM]` `/etc/mag-recorder/mag-recorder-config.toml`), printed under mag-recorder's glyph-less summary line. It says nothing about your station identity. `/etc/sigmond/site-profile.toml` remains the one place your identity lives ([registration.md §1](registration.md#1-what-the-wizard-already-did)), and `smd admin instance list` is what proves what your recorders actually report under. That the two look like the same key is a genuine trap — tracked as [docs-gap ledger row 24](../contributor/docs-gap-ledger.md). |
| `⚠ uploader.ssh_key_file does not exist: /etc/hs-uploader/keys/id_ed25519 (shared with hf-timestd Grape uploader; ssh-keygen + register key on PSWS portal first)` | **Normal on a station that has not enrolled in PSWS — but the path it names is the wrong one** | Another of mag-recorder's own checks (`mag_recorder/contract.py`, `_collect_issues()`), printed under its glyph-less summary line. Two things to know. (1) On a station with no PSWS enrolment there is no upload key yet, so a missing key is the expected state and nothing is lost — the recorder still records locally. (2) **The file name is stale.** `id_ed25519` is the *legacy shared* hs-uploader key name; today's station key is `id_ed25519_host` in the same directory, which is what `smd psws enroll` creates and what the uploader actually uses ([registration.md §1](registration.md#1-what-the-wizard-already-did)). mag-recorder still defaults to the old name (`mag_recorder/config.py:169`), so it reports the old path missing even on an enrolled station. Live 2026-08-23: dasi002 has only `id_ed25519_host` and prints this ⚠; b4 has both files and does not. ⛔ **Do not run the `ssh-keygen` the message suggests** — key creation is `smd psws enroll`'s job ([registration.md §5b](registration.md#5b-register-the-key)). Tracked as [docs-gap ledger row 30](../contributor/docs-gap-ledger.md). |
| `sudo: a password is required` (twice, before the banner) | **Normal — ignore** | A read-only command reaching for `sudo` it does not have and carrying on regardless. It is noise on stderr, not a failure, and it appears on b4 but not dasi002. Known and tracked — [docs-gap ledger row 6](../contributor/docs-gap-ledger.md). |
| `✗ radiod@AC0G-B4-patched.service: inactive` | **Normal** | A *second, deliberately disabled* `radiod` unit. B4 keeps two `radiod` configs in `/etc/radio/` — the live one and a spare "patched" variant. `systemctl is-enabled` reads `enabled` for `radiod@AC0G-B4` and `disabled` for `-patched` (checked live, 2026-08-23). Only one radiod can own the RX888, so the other one being down is the correct state. Most stations have only one and never see this line. |
| `✓ meteor-scatter@my-rx888.service: active [orphaned]` | **Normal-ish — mention it** | `[orphaned]` means a unit is running that the current config no longer declares. Harmless, but worth naming to your fleet admin so it gets tidied. |
| `⚠ 22 pinned process(es) overlap radiod cores` | **Normal — every station shows it** | The station reserves CPU cores for `radiod` and pins the decoders elsewhere. This line counts userspace processes whose CPU mask still overlaps radiod's cores (`lib/sigmond/cpu.py`, `find_contending_processes`; kernel threads and ka9q-radio's own mDNS helpers are already excluded). Both fleet stations carry it while producing good data — b4 around 22–23, dasi002 around 17–20, as of 2026-08-23; it drifts by a few between runs as processes come and go, so treat anything in the teens or twenties as normal — today it is the fleet's normal state rather than a fault. Nothing here is yours to fix; mention the number if it jumps to a different order of magnitude. Tracked — [docs-gap ledger row 12](../contributor/docs-gap-ledger.md). |
| `✗ … OFFSET VIOLATION — offset +9.833 ms …` | **Normal today — known and tracked** | The **timing judge** compares each `radiod` channel's advertised epoch against the station's best clock evidence and flags any channel that disagrees by more than *k×σ* for longer than 60 s (`hf-timestd/src/hf_timestd/core/offset_judge.py`, `_evaluate_violation_locked`). It is a **detector, not a fault**: hf-timestd's own data labels stay corrected regardless — the judge's own log line says so ("labels remain CORRECTED … radiod's advertised epoch is contradicted"). B4 was flagging four of its six timing channels at 5–19 ms on 2026-08-23 while producing good data all day; dasi002, on the weaker T3 evidence, was flagging all six at 200–650 ms. **What matters to you is the summary line above them**: `judge T4 σ=666.9 µs gpsdo=locked`. `gpsdo=locked` is the healthy word; the tier is information for your admin about how good the clock evidence is, not something the software grades. |
| `━━━ PSWS upload not finished ━━━` on a station that *is* enrolled | **Normal — a known contradiction** | B4 is fully enrolled (`smd psws status` reports `✓ key verified 2026-08-17`), yet `smd status` still prints this block, because it checks for the *older* per-recorder key files that a station-key host does not use. Two enrolment models coexist and disagree in your face. Tracked — [docs-gap ledger rows 7 and 10](../contributor/docs-gap-ledger.md). Confirm your real enrolment state with `smd psws status`, not with this block. |

**The three lines that actually matter**, in order:

1. **`radiod@<designator>.service: active`.** If `radiod` is down, nothing on
   the station works — no spots, no timing, no [GRAPE](glossary.md). Everything else on the
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
3. **A client you expect to be enabled has no block, or a unit says `failed`.**
   `inactive` on a spare unit is fine (see the table); `failed` is not. A client
   with no block at all is only a finding if you expected it to be enabled —
   read the next heading before you report one.

   **Where to chase a `failed` unit: start with that client's own section.**
   `mag-recorder` → [troubleshooting.md → *Magnetometer flat line, or mag-recorder says failed*](troubleshooting.md#magnetometer-flat-line-or-mag-recorder-says-failed),
   which separates "this station has no sensor" (normal, nothing to do) from "the
   sensor died" in one command. dasi002's `✗ mag-recorder.service: failed` is the
   first of those. Any **other** client showing `failed` — and any client at all
   if the machine has just lost power — goes to
   [troubleshooting.md → *After a power cut everything is back except one client*](troubleshooting.md#after-a-power-cut-everything-is-back-except-one-client),
   which is the general "one unit is down" tree despite its name.

If any of those three is wrong, go to step 4 and then to your fleet admin.

#### Installed, enabled, shown

`smd status` is shorter than `smd version`, on every station, and the difference
is not damage. Three words are doing three different jobs:

- **[installed](glossary.md)** — the code is on the station and `smd version`
  prints its commit. dasi002 listed **23** components on 2026-08-23.
- **[enabled](glossary.md)** — this station has been told to *run* that client,
  so its systemd units exist and start at boot. **`smd status` shows only
  enabled clients** — its own help says so: `component names (default: all
  enabled)`. dasi002 showed **7** client blocks (verified live, 2026-08-23).
- **shown** — the intersection. A client that is installed and deliberately not
  enabled prints nothing at all, and that is correct output.

That is why dasi002's `smd status` has no `wspr-recorder`, `psk-recorder` or
`meteor-scatter` block while `smd version` lists all three: **that station has no
HF antenna**, so its spot recorders were switched off on purpose rather than
uninstalled, to keep a testbed out of the public databases.

**The command that shows both** — `[VM]`:

```bash
smd component list
```

⚠ **It lists the catalog, not every checkout.** It printed **20** rows on
dasi002 against `smd version`'s 23, because it walks sigmond's component catalog
(`etc/catalog.toml`) and three of the things on disk are not catalog entries:
**`ft8_lib`** (the FT8/FT4 decoder library other recorders link against),
**`wsjtx`** (upstream WSJT-X, built only for its `wsprd` and `jt9` decoders) and
**`onion`** (the small C web-server library `ka9q-web` is built on). All three
are **build ingredients of other components, not components you run** — which is
why they have no lifecycle to report, and why `smd doctor` can name `ft8_lib`
and `wsjtx` while `smd component list` cannot. If you want the complete list of
what is on the machine, that is `smd version`.

Read the **LIFECYCLE** column. Live on 2026-08-23, the same three clients read:

| | b4 (production) | dasi002 (testbed, no antenna) |
|---|---|---|
| `wspr-recorder` | `enabled, running` | `binary, on PATH` |
| `psk-recorder` | `enabled, running` | `binary, on PATH` |
| `meteor-scatter` | `enabled, running` | `configured` |
| `mag-recorder` | `enabled, running` | `enabled, stopped` |

`enabled, running` and `enabled, stopped` are the two states that get a block in
`smd status`. `configured` (set up, no units running), `binary, on PATH`
(installed, nothing enabled), `binary, missing` (never installed here) and
`library` (no units at all — `ka9q-python`, `hs-uploader`) do not. It fetches
from the repositories first, which makes it slow; add `--no-fetch` if you only
want the lifecycle column.

⚠ **`enabled, stopped` is also what a `failed` unit reads as.** The column is
computed from `systemctl is-active` and `is-enabled` and nothing else
(`bin/smd`, the lifecycle branch), so a unit that crashed is "not active but
still enabled" — which prints as the reassuring word `stopped`. dasi002's
`mag-recorder` is `failed` in `smd status` and `systemctl`, and
`enabled, stopped` here, on the same morning. **`smd status` is the page that
tells you `failed`; this column never will.** Do not read `stopped` here as
"fine".

**A coloured VERDICT is git bookkeeping, not a station fault.** That column is
about the software *checkout*, not about whether anything is working: `up to
date`, `behind main (run update)`, `N unpushed commit(s)`, `on branch <name>`,
`pinned to …`, or `dirty: <reason>` (`bin/smd`, where `verdict` is assigned).
dasi002 reads `wspr-recorder … dirty` (its `uv.lock`, the benign case explained
under [step 4](#4-smd-doctor--only-when-something-looks-off)) and
`ka9q-radio … behind 68`. **Neither is yours to act on, and neither is an
emergency** — the radio being 68 commits behind a moving upstream is the normal
state of a deliberately pinned checkout. Mention it if you like; it belongs in
the same message as your `smd doctor` output, not in a separate alarm.

⛔ **Do not run what the bottom of that output tells you to run.** It ends with
lines like:

```text
     1 component(s) have updates available: ka9q-radio
     run: smd component update  (preview: smd component update --dry-run)
```

`smd component update` is on the forbidden list — it pulls every repository and
then runs a full `smd apply` ([do-not-touch.md](do-not-touch.md#the-table)).
Updates are decided by your fleet admin and taken through the
[canary](glossary.md) station first
([below](#updates--who-decides-and-what-you-run)). The same output also carries
an **upload readiness** block, which is covered next.

**So: report a missing block only when you expected that client to be enabled.**
If `smd component list` says `binary, on PATH` and you thought the station was
uploading WSPR, that *is* worth a message to your fleet admin — but it is a
configuration question, not a crash.

⚠ The column says what state a client is *in*; it does not say who chose it or
why. Nothing on the station records "deliberately disabled". If you are not sure
whether a client is off on purpose, ask — that is a one-line question with a
one-line answer, and it is tracked as
[docs-gap ledger row 25](../contributor/docs-gap-ledger.md).

#### "Ready" and "disabled" in the same weekly check

`smd component list` finishes with a block like this — and it will look like it
contradicts the upload switch:

```text
  upload readiness — missing items block ONLY uploading; the recorder still records locally:
    ⚠  PSWS (hf-timestd) — missing station id, instrument id, SFTP key /home/timestd/.ssh/id_rsa_psws
    ⚠  PSWS (mag-recorder) — missing PSWS station id
    ✓  ready: wsprnet.org, wsprdaemon.org, PSKReporter
```

while `smd config uploads status` on the very same station says
`⚠ uploads: DISABLED BY POLICY`.

**Both are true, and the policy line wins.** They answer different questions:

| Line | The question it answers |
|---|---|
| `✓ ready: wsprnet.org, …` in `smd component list` | *"Does this path have the credentials and identity it would need?"* — `lib/sigmond/upload_creds.py`, whose `ready` field is documented as "are the required credentials/identity present?". wsprnet, wsprdaemon and PSKReporter need no registration at all ([registration.md §2–§4](registration.md#2-wsprnetorg--nothing-to-register)), so they are *always* "ready" on every station. |
| `⚠ uploads: DISABLED BY POLICY` in `smd config uploads status` | *"Is this station allowed to upload right now?"* — the site-wide switch. |

So read the readiness block as **"nothing is missing that would stop these paths
if they were switched on"**, and `smd config uploads status` as **whether they
are switched on**. On a policy-disabled station the honest summary is: fully
equipped, deliberately silent. The readiness block knows nothing about the
policy switch, which is
[docs-gap ledger row 29](../contributor/docs-gap-ledger.md).

⛔ The readiness block's `↳ fix:` lines also hand you commands — `smd config
edit hf-timestd`, and a `sudo bash /opt/git/sigmond/hf-timestd/scripts/setup-psws-keys.sh`.
**Neither is yours** ([do-not-touch.md](do-not-touch.md#the-table)); the
operator path for PSWS is [registration.md §5](registration.md#5-psws--the-one-portal-step).

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

**Except on a station whose uploads are switched off** — then a week with zero
spots is guaranteed and is not a fault at all. A testbed with no antenna, or a
station still being built, is deliberately kept out of the public databases.
Check once, and you never have to wonder again — `[VM]`:

```bash
smd config uploads status
```

If it answers `⚠ uploads: DISABLED BY POLICY` with a reason, **zero spots is the
expected result and there is nothing to report**; do not turn it back on
yourself. The full explanation is the closing blockquote of
[registration.md §6 — Confirming everything flows](registration.md#6-confirming-everything-flows).

### 3. Disk — `df -h /`

The one number that can quietly destroy data. hf-timestd records raw IQ
continuously and manages its own eviction, but it also has percentage-based
safety nets for when *something else* fills the disk
(`hf-timestd/src/hf_timestd/core/resource_guardian.py`):

| `Use%` on `/` | What happens |
|---|---|
| under 80% | Normal. Nothing to do. |
| **80%** | hf-timestd logs a warning (`DISK_WARN_PERCENT = 80.0`). Worth mentioning to your fleet admin. |
| **95%** | Hard stop: hf-timestd **pauses all writes and alerts immediately** (`DISK_HARD_STOP_PERCENT = 95.0`) — *no data is deleted at this point*. Only if the disk is **still** ≥95% ten minutes later (`EVICT_GRACE_SEC = 600.0`) does it begin **deleting your oldest recordings** — smallest regenerable unit first — until the disk is back under 90% (`EVICT_LOW_WATER_PERCENT = 90.0`). See the note below the table: the ten-minute grace is new as of 2026-08-22. |

**The ten-minute grace period is new.** Before 2026-08-22 the guardian evicted
**on the crossing**, immediately — which is why you may hear that DASI002 lost an
85.5 GB archive day *two seconds* after crossing 95 % in a drill on 2026-08-21.
hf-timestd `4dfaaf7` ("resource guardian: hysteresis, pause-first, granular
eviction, operator gate", issue #31, 2026-08-22) changed it to **always pause and
alert first, and delete only after the pressure has persisted past the grace
window**; its own comment names that drill as the case the window now protects.
Both fleet stations run `4dfaaf7` or later, so ten minutes is the number that
applies today. Ten minutes is not long — which is exactly why the rule below is
to call your admin at 80 %.

So 95% is the number that costs you data. Live on 2026-08-23: b4 was at **52%**
(126 G of 252 G), dasi002 at **86%** (201 G of 245 G) — dasi002 is the one to
watch. If you are over 80% and climbing, tell your fleet admin *before* it
reaches 95%; do not start deleting files yourself
(→ [do-not-touch.md](do-not-touch.md)).

**How fast does it actually fill? Measured on AC0G/B4, 2026-08-23: about
15 GB per timing channel per day** — one complete UTC day (2026-08-22, all 288
five-minute files) of one channel's compressed raw IQ came to 15,073,610,352
bytes in `/var/lib/timestd/raw_buffer/WWV_25000/20260822`. That station records
**six** timing channels (`smd status` prints `default: 6 ch, 6 freqs`), so it
writes roughly **90 GB a day** and keeps about a day and a half of it before its
own eviction reclaims the space. Read your own channel count off `smd status`
rather than assuming six. (Sizes here are decimal GB, from `du -sb`; `df -h`
prints GiB, which is why 15 GB of files reads as `14G` there.)

That is **raw IQ only**. On top of it the station keeps a cumulative analysis
database — `/var/lib/timestd/phase2/timestd.db`, 9,233,326,080 of the
9,244,733,098 bytes in `phase2` on b4 (measured 2026-08-23) — which **grows
continuously instead of rolling**, plus a few megabytes of per-channel
products. Amortised over what b4 is currently holding that is roughly **1 GB
per channel per day**, so budget **≈16 GB per channel per day, ≈96 GB a day**
for a six-channel station, and treat the database as a slow permanent addition
rather than part of the daily churn.

⚠ **The two written sources disagree with each other and with that
measurement**, so quote the measured figures and not either of them.
hf-timestd's own code comment budgets ~18 GB per channel per day — raw ~14 GB
plus phase2 ~4 GB of "HDF5 data products" — and its raw line matches the
measurement well, but b4 writes no per-channel HDF5 anywhere near that size
(its phase2 channel directories are 292 bytes), so the phase2 half of that
budget over-counts. `hf-timestd/INSTALLATION.md` — the source behind
[shopping-list.md](../hardware/shopping-list.md) — says 6.7 GB and sizes a
6-channel station at a 120 GB disk, which the measurement says is well under
two days of recording. The ~2.7× disagreement is
[docs-gap ledger row 20](../contributor/docs-gap-ledger.md); neither upstream
number has been corrected yet.

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

Two more shapes you are likely to meet:

- **`dirty: 1 modified file(s): uv.lock (diff against origin before
  discarding)`** — "dirty" just means a file that git tracks has been changed on
  this machine since it was checked out. `uv.lock` is the common and benign case:
  it is a Python dependency lock file that the client's own installer rewrites
  when it runs, so it drifts from the committed copy without anyone editing
  anything. **"Discarding" is a decision for whoever maintains the checkout, not
  an instruction to you** — the parenthesis is `smd doctor` telling a *developer*
  to look at the diff first. Report it; do not run any git command.
- **`N finding(s) repairable with: smd doctor --fix`, where N is smaller than the
  number of lines on screen.** That is not a miscount and you have not missed
  anything. `--fix` repairs exactly one class — file **ownership** — so N counts
  only the `ownership:` lines. Every other line (`untracked`, `detached`,
  `dirty`) is reported and left alone by design. It reads like a mismatch, and
  that it does not say so is [docs-gap ledger row 26](../contributor/docs-gap-ledger.md).
  Either way: send the output, do not run `--fix`.

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
of updates applied since install. **The count in `updates since install (N)` is
the real total, but only the most recent five are printed** (`lib/sigmond/provenance.py`,
`history[-5:]`) — so `(13):` above five lines is correct output, not truncation
you need to report. The line that matters is the last one:

```text
image:      v3.30   [image installed on this host — lineage only; components may have moved since (see below)]
...
updates since install (13):
    2026-08-18T09:02:11Z  smd update --apply: install hf-timestd, install sigmond, ...
    2026-08-19T22:47:03Z  smd update --apply: install wspr-recorder, ...
    2026-08-20T14:11:56Z  smd component update: pull mag-recorder, ...
    2026-08-21T23:06:41Z  smd update --apply: install hs-uploader, ...
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

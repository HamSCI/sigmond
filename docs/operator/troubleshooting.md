# When something is wrong

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 2d5ea11 on 2026-08-23 — live b4 + dasi002 (smd status/doctor/version, df -h, journal/unit names via systemctl list-units, smd watch --help, smd admin log --help) + code/docs
> **Canonical for:** symptom-first troubleshooting of an appliance station

Start with the symptom you can actually see, not with a theory. Every section
below follows the same four beats — **likely causes**, most common first;
**what to check**, with what good and bad look like; **what to do**; and **when
to stop and ask**. That last beat is the important one: this station belongs to
a fleet, somebody is paying attention to it, and a false alarm costs far less
than a week of lost science.

Words you don't recognise are in the [glossary](glossary.md). What *healthy*
looks like — and which ✗ and ⚠ marks are normal — is
[day-2.md](day-2.md); this page assumes you have read it and something still
looks wrong.

Every command is tagged `[VM]` or `[host]` on the line above it, so a whole
block is safe to copy. Nearly all are `[VM]` — the [decoder VM](glossary.md),
which you reach with `ssh hamsci@<VM address>`
([two machines in one box](README.md#two-machines-in-one-box)).

---

## First, the 2-minute triage

Do these four before anything else. They are all read-only — nothing here
changes your station.

`[VM]` — on the decoder VM:

```bash
smd status
smd doctor
df -h /
```

Then, from any computer on your network, open the live receiver page:
`http://<VM address>:8081`.

Read them in this order:

| What you saw | Go to |
|---|---|
| `radiod@<designator>.service` is **not** `active` | [RX888 not found, or the waterfall is blank](#rx888-not-found-or-the-waterfall-is-blank) |
| A unit says `failed` | [After a power cut everything is back except one client](#after-a-power-cut-everything-is-back-except-one-client) |
| `df -h /` is 80 % or more | [Disk filling up](#disk-filling-up) |
| The waterfall is blank or empty | [RX888 not found, or the waterfall is blank](#rx888-not-found-or-the-waterfall-is-blank) |
| No page loads at all | [Web pages don't load but ssh works](#web-pages-dont-load-but-ssh-works) |
| It all looks fine but no data is arriving upstream | [No spots on wsprnet](#no-spots-on-wsprnet) |

**Everything else on that output is probably normal.** A handful of ✗ and ⚠
marks appear on a perfectly healthy station, and
[day-2.md's annotated `smd status`](day-2.md#1-smd-status--is-everything-running)
explains each one line by line. Do not chase the `OFFSET VIOLATION` lines, the
`N pinned process(es) overlap radiod cores` warning, or the
`━━━ PSWS upload not finished ━━━` block on their own — all three are known,
tracked, and true on healthy stations.

### One thing to know before you type anything that changes the station

**Never write `sudo smd`.** `smd` refuses to run that way, on purpose, and tells
you so:

```text
don't run smd under sudo — it elevates itself when a verb needs root.
```

The verbs that change the station — `start`, `stop`, `restart`, `reload` — call
for root themselves and re-run the whole command under `sudo` for you, which
means **you may be asked for your password**; that prompt is normal, not a
failure (source: `bin/smd`, the top-of-`main` guard and `_need_root()`, which
re-execs `sudo -- env SIGMOND_ALLOW_SUDO=1 smd <same arguments>`).

So the shape is always:

`[VM]` — on the decoder VM:

```bash
smd restart mag-recorder
```

`smd restart` takes component names, or the word `all`, and defaults to
everything enabled (`smd restart --help`, read live on AC0G/B4 2026-08-23).
The names it accepts are the ones in the **NAME** column of
`smd component list` — on b4, live 2026-08-23: `ka9q-radio` (that is the radio,
whose unit is `radiod@<designator>`), `ka9q-web`, `wspr-recorder`,
`psk-recorder`, `mag-recorder`, `hf-timestd`, `gpsdo-monitor`,
`meteor-scatter`, `gmag-webui` and `igmp-querier`.

> **If it answers that your account may not use sudo**, log in as `sigmond@`
> instead of `hamsci@`. The [wizard](glossary.md) puts the `sigmond` account in
> the `sudo` group and not the login you may have been given (source:
> `scripts/proxmox/sigmond-wizard.sh`, `usermod -aG sudo sigmond`; live on b4,
> `getent group sudo` reads `sudo:x:27:sigmond`).

---

## Symptoms

### No spots on wsprnet

**Likely causes, most common first**

1. **Not enough time, or the wrong search.** Rows appear about fifteen minutes
   after the station is up, and you must search the **Reporter** field with your
   [reporter ID](glossary.md) (`AC0G/B4`), not your bare callsign
   ([registration.md §6](registration.md#6-confirming-everything-flows)).
2. **You are searching the wrong identity.** The reporter ID is whatever the
   wizard recorded, which may not be what you remember typing.
3. **Uploads are switched off by policy.** A station with no antenna, or one
   still being built, is deliberately kept out of the public databases.
4. **Nothing is being decoded at all** — which is a different problem, and a
   bigger one.
5. **Propagation.** An hour with no spots is weather. A week with zero is a
   fault ([day-2.md §2](day-2.md#2-are-your-spots-arriving)).

**What to check**

`[VM]`:

```bash
smd watch uploads
```

*Good* looks like one line per two-minute cycle, e.g.
`cycle=21:48 shipped wsprdaemon=199 wsprnet=posted:75/added:74 ft8=312 ft4=0`.
*Bad* is either `wsprnet=posted:0` (the station is shipping, but zero WSPR spots
were found) or **nothing printed at all for more than five minutes** (nothing is
reaching the uploader). Ctrl-C stops it; it changes nothing.

Then confirm which identity you should be searching — `[VM]`:

```bash
smd admin instance list
```

The **REPORTER ID** column is the answer (b4 prints `AC0G/B4` for
`wspr-recorder`, `psk-recorder` and `meteor-scatter`, live 2026-08-23). The same
value is in `/etc/sigmond/site-profile.toml` under `[reporters] reporter_id`.

Then check the upload switch — `[VM]`:

```bash
smd config uploads status
```

*Good:* `✓ uploads: enabled (outbound data pipelines render normally)` — b4's
answer. *Bad, but deliberate:*
`⚠ uploads: DISABLED BY POLICY — no HF antenna; no PSWS station/instrument ids`
— DASI002's answer, and correct for that station.

**What to do**

- `wsprnet=posted:0` with cycles printing → your station is healthy and shipping;
  either the bands are empty or you are searching the wrong reporter ID. Search
  the one `smd admin instance list` printed.
- Nothing printed at all → the problem is upstream of uploading. Go to
  [Spots stopped](#spots-stopped-were-fine-before).
- `DISABLED BY POLICY` → **do not turn it back on yourself.** Somebody set that
  for a reason ([registration.md §6](registration.md#6-confirming-everything-flows)).
  Ask your [fleet admin](glossary.md).

**When to stop and ask**

Uploads enabled, `radiod` active, signals visible on the waterfall, cycles
printing with real counts — and still nothing on wsprnet an hour later. That
combination is not something you can fix from the station.

---

### Nothing on pskreporter

**Likely causes, most common first**

1. **You searched yourself as a transmitter.** This is the answer far more often
   than anything else. A station working perfectly shows nothing at all when you
   look it up as a sender; you have to look yourself up as the **receiver**
   (sometimes labelled *monitor*)
   ([registration.md §3](registration.md#3-pskreporterinfo--nothing-to-register)).
   It cost one of our sites an afternoon.
2. **You searched with the suffix.** pskreporter wants the bare callsign —
   `AC0G`, not `AC0G/B4` (same section).
3. **`psk-recorder` is not running.**
4. **Nothing is being decoded** — see [Spots stopped](#spots-stopped-were-fine-before).

**What to check**

`[VM]`:

```bash
smd watch uploads
```

Lines carrying `ft8=` and `ft4=` counters are the PSK path. *Good* is a non-zero
`ft8=`. Note that WSPR cycle lines and PSK lines are two different shapes sharing
one screen, so not every line has them.

Then `[VM]`:

```bash
smd status
```

*Good:* `✓ psk-recorder@<designator>.service: active`.

**What to do**

Search pskreporter again as the **receiver**, using the bare callsign. If
`ft8=` counters are moving, your station is doing its job.

A quiet log is *also* the healthy case here: the PSK upload path only writes a
line when something goes wrong. On AC0G/B4 a full day produced six log lines,
all `Broken pipe — reconnecting`, each of which recovered by itself (observed
2026-08-18). So "nothing in the log" is not evidence of a problem.

**When to stop and ask**

`ft8=` counters at zero for an hour while the waterfall shows signals and
`psk-recorder` is active.

> **Don't poll pskreporter's query API** in a script — it is rate-limited and a
> loop will get you blocked ([registration.md §3](registration.md#3-pskreporterinfo--nothing-to-register)).

---

### Spots stopped (were fine before)

This is the most interesting failure on the station, because it has a specific
mechanism and the station usually repairs it without you.

**Likely causes, most common first**

1. **A stale time anchor.** `psk-recorder` works out the mapping between the
   radio's stream clock and wall-clock **once, when it starts**. If `radiod`
   restarts, or the [RX888](glossary.md) glitches its sample rate, that mapping
   silently becomes wrong: the decoder keeps running, the audio keeps arriving,
   and FT8/FT4 output collapses to zero without a single error message. WSPR
   usually rides through the same event, because it re-correlates continuously
   and its two-minute window tolerates seconds of error (source:
   `bin/sigmond-timing-watchdog`, module docstring; diagnosed on AC0G/B4
   2026-06-02).
2. **The band died.** Propagation, not you.
3. **`radiod` itself stopped** — then nothing works, not just spots.
4. **The antenna or its coax.**
5. **Uploads switched off** — see [No spots on wsprnet](#no-spots-on-wsprnet).

**What to check**

`[VM]`:

```bash
smd status
```

*Good:* `✓ radiod@<designator>.service: active`, and every recorder ✓. If
`radiod` is not active, everything else on this page is downstream of that —
go to [RX888 not found](#rx888-not-found-or-the-waterfall-is-blank).

Then look at the live receiver page (`http://<VM address>:8081`). A waterfall
with bright horizontal traces means the antenna and the radio are fine and the
problem is in a decoder ([day-2.md, the four windows](day-2.md#the-four-windows)).

**What to do**

**Wait five minutes first.** Two watchdogs run on their own and this is exactly
what they are for: `sigmond-timing-watchdog.timer` fires every 90 seconds and
restarts a recorder whose anchor has gone stale, and
`sigmond-radiod-watchdog.timer` every two minutes (both enabled on b4, live
2026-08-23). The timing watchdog will not act on a genuinely dead band — it
requires either a peer receiver that *is* decoding, or a real RX888 sample-rate
glitch in the log, before it restarts anything (source:
`bin/sigmond-timing-watchdog`).

If it is still dead after five minutes and `radiod` is active — `[VM]`:

```bash
smd restart psk-recorder
```

(or `wspr-recorder`, whichever stopped). Recovery takes one or two decode
cycles.

⚠ **If `radiod` needs restarting too, restart it first and let it settle before
you touch the recorders.** A recorder started against a restarting `radiod`
picks up a bad anchor and you will have manufactured this exact fault — that
warning is `smd update`'s own words, printed live on b4 2026-08-23:
*"radiod first, then the recorders once it is stable — a recorder started
against a restarting radiod picks up a bad anchor."*

**When to stop and ask**

You restarted once and it came back broken; or `radiod` will not stay active;
or spots stop again within the hour.

---

### Uploads pending and growing

**Read this before you act, because the usual answer is "nothing is wrong."**

The station keeps a queue of things to upload in its [sink](glossary.md), and
you can see its depth in the TUI's Resources screen (`smd tui`) or on the fleet
board your admin watches. A growing number there, with the **oldest** entry
frozen at a fixed age, is the *normal, healthy* shape for the wsprdaemon path —
not a stall. What ages those rows out is a **retention timer, not delivery**:
`sigmond-storage-trim-all.timer` fires every 15 minutes and trims each table to
its retention policy (its own unit comment, live on b4), and that is exactly
what pins the oldest entry at a fixed age. This has been mistaken for a dead
upload path on AC0G/B4 — 66,000 rows growing steadily with the oldest pinned at
precisely 24 hours, while the upstream collector had that station's spots
current to the minute (observed 2026-08-18).

**So: queue depth is not a delivery measure. Check the destination instead.**
(There is no one command that answers "are my uploads being accepted?" across
all four destinations — that gap is row 8 of the
[docs-gap ledger](../contributor/docs-gap-ledger.md).)

**Likely causes, most common first**

1. **Nothing** — the shape above.
2. **Uploads disabled by policy** (a testbed, a station with no antenna).
3. **A real network outage.** Then *every* counter stops, not one queue.

**What to check**

`[VM]`, in this order:

```bash
smd config uploads status
smd watch uploads
```

*Good:* `✓ uploads: enabled`, then counters moving each cycle. *Bad:*
`⚠ uploads: DISABLED BY POLICY — <reason>` (deliberate; ask before changing), or
counters flat while cycles keep printing.

Then check the outside world:
[registration.md §6](registration.md#6-confirming-everything-flows) says where
each product should appear and how long it normally takes.

**What to do**

If the counters move and the databases have your rows, you are done — say so and
stop looking at the queue. If the counters are flat, treat it as a network
problem and check the station's internet.

**When to stop and ask**

Counters flat for an hour with uploads enabled and the station otherwise
healthy.

---

### PSWS not verified

[PSWS](glossary.md) is the only one of the four upload destinations with a form
to fill in, so it is the only one that can be "not registered."

**Likely causes, most common first**

1. **The key was never registered at the portal.** The station generates a key
   at install and deliberately does not register it for you — that step needs
   copy-and-paste and the install console has none
   ([registration.md §1](registration.md#1-what-the-wizard-already-did)).
2. **The key was pasted incompletely** — a line break or a trimmed end.
3. **PSWS was skipped at the wizard**, so there is nothing to verify. Not a
   fault.
4. **Network or firewall**, not registration.

**What to check**

`[VM]`:

```bash
smd psws status
```

*Good:* `✓ key verified <timestamp>` — b4 reads `2026-08-17T22:33:29Z`.
*Not a fault:* `[psws] disabled in site-profile.toml — nothing to do` — DASI002's
answer, meaning the wizard's PSWS questions were skipped.

Then `[VM]`:

```bash
smd psws verify
```

This makes one real login. It distinguishes the two failures for you, and
[registration.md §5c](registration.md#5c-prove-it-works) has the exact table of
what each means.

**What to do**

- Key not registered → `smd psws enroll`, then paste the whole `ssh-ed25519 …`
  line into the SSH-key field for your **site** on the portal. Expect a password
  prompt: `enroll` and `verify` write root-owned files, so `smd` re-runs itself
  under `sudo` ([registration.md §5b](registration.md#5b-register-the-key)).
- PSWS disabled and you now have IDs → run `sigmond-setup --reconfigure` from
  the `[host]` and press Enter through everything else
  ([registration.md §1](registration.md#1-what-the-wizard-already-did)).

⚠ **`smd status` will keep printing `━━━ PSWS upload not finished ━━━` even
after you are fully enrolled.** It is checking for an older per-recorder key
scheme that your station does not use. `smd psws status` is the one that tells
the truth ([day-2.md's annotation table](day-2.md#1-smd-status--is-everything-running);
docs-gap rows 7 and 10).

**When to stop and ask**

`smd psws verify` still says `public key not registered` after two careful
pastes; or it says it cannot reach the server and your station's internet is
otherwise fine.

---

### RX888 not found, or the waterfall is blank

**Likely causes, most common first**

1. **First install, and the radio was handed across to the VM mid-state.** This
   happens exactly once and is expected. ⛔ **A reboot cannot fix it** — the
   radio's FX3 chip stays latched as long as USB power is held, so only a full
   power-off, or physically unplugging the radio, resets it (source:
   `scripts/proxmox/sigmond-wizard.sh`, which says so on the console and leaves
   a marker so the login panel repeats it).
2. **Wrong port or a hub in the path.** The RX888 must go straight into a blue
   USB-3 port with nothing in between; USB 2 cannot carry its data rate and a hub
   starves it ([shopping-list.md](../hardware/shopping-list.md#things-that-look-right-but-arent)).
3. **The radio is fine and the antenna is not** — a blank waterfall with the
   radio present and `radiod` active is an RF problem, not a software one.

**What to check**

`[VM]`:

```bash
lsusb | grep -i rx888
```

*Good* (live b4, 2026-08-23):
`Bus 004 Device 003: ID 04b4:00f1 Cypress Semiconductor Corp. RX888mk2`.
*Bad:* no output at all (the VM cannot see the radio), or an id ending `00f3` —
that is the FX3 boot ROM, meaning the device is present but its firmware never
loaded.

Then `[VM]`:

```bash
smd status
```

*Good:* `✓ radiod@<designator>.service: active`.

**What to do**

- Nothing in `lsusb` → **power the whole machine off**, wait, power it back on.
  Not a reboot. If it is still missing, unplug the RX888's USB cable and plug it
  back into a blue port with no hub.
- You do not need to start anything afterwards: `sigmond-sdr-sentinel.timer`
  runs every two minutes and *"mints radiod once an RX888 is attached"* (its own
  unit description, read live on b4). Give it about two minutes, then re-run
  `smd status`.
- Radio present, `radiod` active, waterfall still blank → check the coax, the
  connector and the antenna itself. Nothing on the computer will fix that.

**When to stop and ask**

The RX888 appears in `lsusb` but `radiod` will not stay active; or it is still
missing after a full power-cycle *and* a replug.

---

### GPS not locked, or the timing dashboard is red

**Likely causes, most common first**

1. **The GPS antenna cannot see sky.** Moved indoors, knocked off a windowsill,
   or a cable pulled — this is nearly always it.
2. **The [GPSDO](glossary.md) was plugged in before its software was
   installed.** Device permission rules only fire when a device is plugged in, so
   a GPSDO that was already attached keeps the wrong ownership on its USB node
   and the monitor can never open it. The symptom is a monitor that runs but
   reports nothing, or a serial of `unknown`. Replugging the GPSDO fixes it
   permanently (the rules file `99-gpsdo.rules` is installed by
   `gpsdo-monitor/install.sh`; behaviour observed on AC0G/B4).
3. **Nothing at all** — the `OFFSET VIOLATION` lines under the judge summary are
   a detector, not a fault, and are true on healthy stations today
   ([day-2.md's annotation table](day-2.md#1-smd-status--is-everything-running)).

**What to check**

`[VM]`:

```bash
smd watch gpsdo --once
```

*Good* (live b4, 2026-08-23):

```text
  SERIAL        MODEL       A   PLL   FIX   SATS  ANT   OUT1 MHz    OUT2 MHz    PPS   AGE
  0C7BB80D10EF  lbe-1421    A1  yes   3D    8     yes   10.000000   27.000000   yes   0s
      A-level A1: pll_locked && gps_fix=3D && antenna_ok && pps_present && fresh
```

*Bad* (live DASI002, same morning):

```text
  0C7BB80D5116  lbe-1421    A0  yes   no_fix  0   yes   27.000000   27.000000   no    —
      A-level A0: gps_fix=no_fix
```

Note the shape of that bad case: the oscillator's own loop is locked (`PLL yes`)
and the antenna reads OK, but there are **zero satellites**. That is an antenna
that is plugged in and cannot see the sky.

Then, in `smd status`, read only the judge **summary** line — the word the
software actually grades is `gpsdo=`:

```text
✓  judge T4  σ=666.9 µs  age 0s  gpsdo=locked        ← b4, healthy
⚠  judge T3  σ=3107.9 µs  age 0s  gpsdo=holdover     ← dasi002, a real condition
```

`locked` is the healthy word; `holdover` or `unlocked` means the GPSDO has lost
its GPS fix. The [timing tier](glossary.md) (T3, T4…) is information for your
admin about how good the clock evidence is, not a grade
([day-2.md](day-2.md#1-smd-status--is-everything-running)).

**What to do**

- Check the GPS puck: connected, outdoors or in a window, nothing metal over it.
  Then give it fifteen minutes to acquire.
- Monitor running but reporting nothing → unplug and replug the GPSDO's USB
  cable.
- `holdover` costs you a timing tier. **It does not stop the station**: it keeps
  recording and keeps producing spots the whole time.

**When to stop and ask**

Still `no_fix` with zero satellites after an hour with a clear sky view; or
`gpsdo=holdover` persists after the fix returns.

---

### Magnetometer flat line, or mag-recorder says failed

These look similar and are two completely different things.

**Likely causes, most common first**

1. **You do not have an RM3100.** The magnetometer is optional
   ([shopping-list.md](../hardware/shopping-list.md#optional--and-what-you-lose-without-it)),
   and on a station without one `mag-recorder` will sit `failed` because it was
   never given a configuration. That is DASI002 today, and nothing about it is
   broken.
2. **The sensor stopped answering.** The recorder then re-emits the last reading
   it got, once a second, forever — so the dashboard shows a *perfectly flat,
   perfectly constant* line that looks like data. This happened on AC0G/B4 from
   2026-08-18 to 2026-08-21 and three days of frozen values were packaged and
   uploaded before anyone noticed.
3. **A genuinely quiet day.** No — geomagnetic readings always wiggle. A
   dead-flat unchanging line is a stuck sensor
   ([day-2.md, the four windows](day-2.md#the-four-windows)).

**What to check**

`[VM]`:

```bash
systemctl status mag-recorder --no-pager
```

*Bad, but harmless* (live DASI002, 2026-08-23): `Active: failed (Result:
exit-code) … Main PID: 48713 (code=exited, status=78)`.

For the reason, read the client's own file log — `[VM]`:

```bash
smd admin log mag-recorder --files
```

DASI002 answers, over and over:

```text
ERROR:mag_recorder.daemon:config still at template placeholders
  (station.psws_station_id=<YOUR_PSWS_STATION_ID>) — run `mag-recorder config init` … exiting EX_CONFIG
```

That is "no magnetometer was ever set up here," not "the hardware died."

For a station that *does* have a sensor, read the last two samples it wrote —
`[VM]`:

```bash
tail -2 /var/lib/mag-recorder/samples-$(date -u +%F).jsonl
```

*Good* (live b4, 2026-08-23) — the numbers **move** between one second and the
next, and `rt` (the sensor's own temperature) is a real value:

```text
{"ts":"2026-08-23T04:39:38.796Z","rt":31.06,"x":-39855.631,"y":-555.743,"z":-25219.144,…}
{"ts":"2026-08-23T04:39:39.796Z","rt":31.0,"x":-39855.405,"y":-553.378,"z":-25218.806,…}
```

*Bad:* identical `x`, `y` and `z` line after line, or `rt` stuck at `0.0`. That
is the frozen-sensor shape.

⚠ **Do not judge this with `smd watch mag`.** On b4 — a station whose
magnetometer was demonstrably alive and writing a sample every second — it
reported `samples= 0 (0.0/s) — no samples; is /dev/ttyMAG0 present?` while
`/dev/ttyMAG0` was present and the file was growing (live, 2026-08-23). Its
rollup is not a reliable liveness check today; the file above is.

**What to do**

- No RM3100 → nothing to do. Mention it to your admin so the unit can be
  disabled rather than left failing.
- Flat line → unplug and replug the sensor's USB adapter, then **restart the
  recorder** — `[VM]`:

  ```bash
  smd restart mag-recorder
  ```

  ⚠ The restart is not optional after a replug: the reader does not reopen the
  device by itself, so a replug alone leaves it holding a device that is gone
  (observed on AC0G/B4, 2026-08-21).
- Data on PSWS is a separate question and has its own known failure shape —
  [registration.md §5d](registration.md#5d-if-you-have-a-magnetometer).

**When to stop and ask**

Values still constant after a replug and a restart; or the unit fails again
straight after a restart on a station that *does* have a magnetometer.

---

### Disk filling up

**This is the one number on the station that can quietly destroy data**, so it
gets a low bar for asking.

**Likely causes, most common first**

1. **Normal growth.** The timing client records a great deal of raw radio and
   manages its own housekeeping; the rates and the safety nets are in
   [day-2.md §3](day-2.md#3-disk--df--h-).
2. **Something else filled the disk** — an unexpected log, a leftover file, a
   client misbehaving.

**What to check**

`[VM]`:

```bash
df -h /
```

Live on 2026-08-23: b4 at **52 %** (126 G of 252 G), DASI002 at **86 %**
(201 G of 245 G).

**What the numbers mean** —
[day-2.md §3](day-2.md#3-disk--df--h-) owns this table and is the canonical
version; the short form is that **80 %** is a warning and **95 %** is the number
that costs you data: the timing client stops writing and, after ten minutes
above that line, begins deleting your oldest recordings until the disk is back
under 90 %.

That is not theoretical. In a deliberate drill on DASI002 on 2026-08-21, an
85.5 GB archive day was evicted **two seconds** after the disk crossed 95 %.

**What to do**

- Over 80 % and climbing → tell your fleet admin **now**, while there is still
  time to act.
- ⛔ **Do not delete files yourself** to make room. You cannot tell which files
  are the station's regenerable scratch and which are the day's science
  (→ `do-not-touch.md` *(being written)*).

**When to stop and ask**

At 80 %. Not at 94 %.

---

### The host console keyboard is dead

**Nothing is wrong.** After the first reboot following installation, the
machine's USB ports belong to the [decoder VM](glossary.md) — the radio needs
them — so the physical keyboard stops working and the monitor shows a login
panel with both addresses instead. That is correct and deliberate
([INSTALL.md §8](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#8-remove-the-stick-when-told--done);
[day-2.md](day-2.md#power-loss-reboots-and-moving-the-box)).

**What to do:** drive the station from another computer over the network. You
can unplug the monitor and keyboard whenever you like.

⚠ **On a laptop this is a real problem**, because the built-in keyboard is the
one that dies. That is why the parts list says not to use one
([shopping-list.md](../hardware/shopping-list.md#things-that-look-right-but-arent)).

**When to stop and ask:** never for this. If you need console access to the VM,
your fleet admin has it through remote access
([remote-access.md](remote-access.md#1-what-it-is)).

---

### The VM did not start after a reboot

**Likely causes, most common first**

1. **It is still booting.** Allow about ten minutes before judging a station
   after a power cut ([day-2.md](day-2.md#power-loss-reboots-and-moving-the-box)).
   Do not power-cycle it again while you wait.
2. **The [host](glossary.md) came back but the VM did not start.**
3. **The host did not come back at all** — then nothing answers, including the
   Proxmox page.

**What to check**

From any computer on your network, open `https://<host address>:8006` — the
Proxmox page — and look for the one VM named after your
[designator](glossary.md). *Good:* it says **running**. That is the only thing
you ever need Proxmox for
([day-2.md, the four windows](day-2.md#the-four-windows)).

Or, if you can ssh to the host — `[host]`:

```bash
qm list
```

*Good* — one row, your VM, `running` (live on AC0G-B4-PM, 2026-08-23):

```text
      VMID NAME                 STATUS     MEM(MB)    BOOTDISK(GB) PID
       100 AC0G-B4              running    9831             256.00 1298
```

**What to do**

- Still within ten minutes of power-on → wait.
- Proxmox says the VM is stopped → select it and press **Start**, then wait ten
  minutes and re-check `smd status` on the VM.
- Nothing answers at all, including `https://<host address>:8006` → this is a
  power or hardware problem at the box. Check that it is powered and that its
  network cable is in.

**When to stop and ask**

The VM starts and then stops again; or the Proxmox page will not load while the
machine is clearly powered.

> Your fleet admin can still reach the station **while the VM is down** — remote
> access runs on the host, not in the VM, precisely for this
> ([remote-access.md §1](remote-access.md#it-runs-on-the-host-not-on-the-vm)).

---

### Remote access says FAILED

**A failed tunnel costs you nothing but support access.** The radio, the
recorders and every upload path are completely unaffected — remote access
carries no science data at all
([remote-access.md §7](remote-access.md#7-if-it-says-failed)).

**What to do** — one command, from the `[host]`, whenever you like:

```bash
# [host] — on the Proxmox host, not the VM
sigmond-setup --reconfigure
```

Press Enter through everything; only the remote-access step needs to re-run.
Your reporter ID, grid square, RAC number and PSWS registration all stick.

**Two traps worth knowing before you chase this:**

- ⛔ **If your admin says "I can reach the port but cannot log in", that is
  almost certainly the dashboard's `root@` command**, which can never work
  against the decoder VM. Send them to
  [remote-access.md §5](remote-access.md#5-how-the-admin-actually-connects--and-the-one-instruction-that-is-wrong).
- **Inside the VM, `smd admin rac status` will say "not configured".** That is
  correct output on an appliance station, not a fault, and you must **not** run
  `smd admin rac install` to "fix" it
  ([remote-access.md §4](remote-access.md#ignore-smd-admin-rac-on-an-appliance-station)).

**When to stop and ask**

`sigmond-setup --reconfigure` still reports FAILED. On an older image this step
used to fail for everybody; the answer is a newer image, not more retries
([remote-access.md §7](remote-access.md#7-if-it-says-failed)).

---

### Web pages don't load but ssh works

**Likely causes, most common first**

1. **That page's service is not running.** Each of the three pages is a separate
   program and any one of them can be down while the rest of the station is
   perfect. DASI002 shows exactly this: `✗ gmag-webui.service: inactive`, and
   nothing listening on port 8082 (live, 2026-08-23).
2. **You are on a different part of your network** — another VLAN, a guest
   Wi-Fi that isolates clients, or through a VPN.
3. **The address changed.** The VM takes a DHCP lease and a router reboot can
   move it.
4. **Not multicast.** See the note below before you go network-hunting.

**What to check**

`[VM]`:

```bash
smd status
ss -ltn
```

*Good* (live b4): ports **8081** (live receiver), **8000** (timing dashboard)
and **8082** (magnetometer dashboard, only if you have an RM3100) all listening.
DASI002 showed 8081 and 8000 only — because its magnetometer dashboard was not
running, which `smd status` had already said.

If the port *is* listening, prove it from the station itself — `[VM]`:

```bash
curl -sI http://127.0.0.1:8081 | head -1
```

*Good:* `HTTP/1.1 200 OK` — exactly what b4 returned on 2026-08-23, for 8081
and 8000 alike. If that works and your laptop still cannot reach the page, the
problem is on your network between the two machines, not on the station.

**What to do**

- Service not running → `smd restart <name>` for that page's component
  (`ka9q-web`, `hf-timestd`, `gmag-webui`).
- Listening locally but not reachable → try from a computer plugged into the
  same switch by cable. Check that you are using the **VM** address and not the
  host's.

> **This is almost never a multicast problem, and you should not go looking for
> one.** On a standard appliance station `radiod` publishes with `ttl = 0` and
> site-local multicast is routed over the loopback interface, so the radio →
> recorder traffic never touches your switch at all (live on b4:
> `ttl = 0 # 0 = loopback-only` in the radiod config, and
> `sigmond-loopback-multicast.service`, *"Route site-local multicast
> (239.0.0.0/8) via loopback for on-host radiod delivery"*). IGMP snooping only
> bites when a **second machine** subscribes to this station's streams — that
> case, and its silent 4-to-5-minute failure, is
> [networking.md](../networking.md#the-symptom).

**When to stop and ask**

The port is listening, `curl` works on the station, and a wired computer on the
same switch still cannot load the page.

---

### After a power cut everything is back except one client

**Likely causes, most common first**

1. **It is still coming up.** Allow about ten minutes after power-on before you
   judge anything ([day-2.md](day-2.md#power-loss-reboots-and-moving-the-box)).
2. **A client is genuinely `failed`** — it started, hit a problem, and gave up.
   `inactive` on a spare unit is fine; `failed` is not
   ([day-2.md](day-2.md#1-smd-status--is-everything-running)).
3. **A client that was never configured on this station**, which will fail every
   boot — see [Magnetometer flat line](#magnetometer-flat-line-or-mag-recorder-says-failed)
   for the worked example.

**What to check**

`[VM]`:

```bash
systemctl --failed --no-pager
```

*Good:* `0 loaded units listed.` *Bad* (live DASI002, 2026-08-23): one row,
`● mag-recorder.service loaded failed failed`.

Then, for each one — `[VM]`:

```bash
systemctl status <unit> --no-pager
smd admin log <client> --files
```

⚠ **`journalctl -u <unit>` will not work for you, and its silence is
misleading.** Neither operator account is in the `adm` or `systemd-journal`
group — both groups are empty on b4 — so `journalctl` answers *"No journal files
were opened due to insufficient permissions"* and prints nothing, which looks
exactly like a service that logged nothing. `systemctl status` and
`smd admin log <client> --files` are the two that do work. (Verified live on
both fleet stations, 2026-08-23; row 16 of the
[docs-gap ledger](../contributor/docs-gap-ledger.md).)

**What to do**

`[VM]`, naming just the one component:

```bash
smd restart <name>
```

If `radiod` is among the failures, restart `radiod` **first** and give it a
minute to settle before the recorders — see
[Spots stopped](#spots-stopped-were-fine-before) for why that order matters.

**When to stop and ask**

It fails again after one restart. One restart is your whole budget here; a
second one just hides the evidence.

---

## Replug, restart, reboot, reinstall — which one, when

Work down this table, never up. Each row costs more than the one above it.

| Action | Use it when | What it costs | How |
|---|---|---|---|
| **Replug a USB device** | The device is missing from `lsusb`, or a sensor has frozen at a constant value | Seconds of that one device's data | Unplug, wait, plug back in. The RX888 goes **straight into a blue USB-3 port, no hub**. Then restart the client that owns it (`smd restart mag-recorder`); the RX888 needs nothing — `sigmond-sdr-sentinel.timer` picks it up within two minutes |
| **Restart one client** | One client is `failed`, or one program's spots stopped while the rest are fine | A decode cycle or two of that program | `[VM]`: `smd restart <name>` — names from `smd component list`. **Never `sudo smd`.** If `radiod` is involved, `radiod` first, then the recorders once it is stable |
| **Reboot — or power off** | Nothing above worked, and your fleet admin agrees | About ten minutes of everything | ⛔ **These are not the same thing.** A reboot holds USB power, so a wedged RX888 stays wedged; only a full **power-off** resets it (source: `sigmond-wizard.sh`). If the radio is the problem, power off. Otherwise a reboot is enough |
| **Reinstall** | Never on your own initiative | Everything, including your PSWS registration unless the keys were saved first | Only with your fleet admin, and save the old keys onto the stick first ([INSTALL.md §4](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md#4-returning-station-put-your-old-keys-on-the-stick-optional)) |

Two commands that are **not** on this table because they are decisions, not
repairs: `smd update --apply` (only when your admin says a release is
blessed — [day-2.md](day-2.md#updates--who-decides-and-what-you-run)) and
`sigmond-setup --reconfigure` on the `[host]` (for wrong answers, a new grid
square, or remote access).

---

## What to send when you ask for help

Send these four outputs, plus your [designator](glossary.md) and grid square.
It is the same set the
[operator front page](README.md#getting-help--what-to-send) asks for, and it
answers most questions in one round trip — `[VM]`:

```bash
smd doctor
smd status
smd version
cat /etc/sigmond-appliance/version    # the image, not what you run now
```

That last file is written once at install time and never changes, so it says
which image you *started from*, not what you are running now —
`smd version` is what you are running now
([day-2.md](day-2.md#knowing-what-you-are-actually-running)).

**Add whatever the symptom section told you to run.** A few worth naming:

| If your symptom was | Also send |
|---|---|
| Anything about spots or uploads | `smd config uploads status`, and a minute of `smd watch uploads` |
| PSWS | `smd psws status` |
| GPS or timing | `smd watch gpsdo --once` |
| A failed unit | `systemctl --failed --no-pager`, then `systemctl status <unit> --no-pager` and `smd admin log <client> --files` |
| Disk | `df -h /` |
| Radio not found | `lsusb` |

**Paste text, don't screenshot.** A screenshot of a terminal cannot be searched,
diffed, or grepped, and it usually crops the one line that mattered. Copy the
text out of your terminal and paste it.

**Where to send it:** your [fleet admin](glossary.md) — the person who gave you
the image — or the HamSCI DASI2 operators group via <https://hamsci.org/>.

**Ask early.** A station that has been quietly broken for a week has lost a week
of science, and nobody minds a false alarm.

---

## What NOT to do while troubleshooting

The full list with the reasoning behind each one is
`do-not-touch.md` *(being written)*. The five that matter most while you are
chasing a fault:

- ⛔ **Don't `sudo smd`.** It refuses, and it is telling you the truth: it
  elevates itself when a verb needs root.
- ⛔ **Don't delete files to free disk space.** You cannot tell the station's
  scratch from the day's science. Ask.
- ⛔ **Don't `apt upgrade`, `pip install`, or `git pull`** anywhere on the
  station. The packages and checkouts are pinned where they are on purpose, and
  a hand-installed one silently breaks the next update
  ([day-2.md](day-2.md#three-things-never-to-do-by-hand)).
- ⛔ **Don't run `smd doctor --fix` or `smd update --apply`** unless your fleet
  admin has told you to. The plain forms this page uses — `smd doctor` and
  `smd update` — are read-only; those two flags are exactly what turn them into
  changes to a working station.
- ⛔ **Don't restart things repeatedly.** One restart is diagnosis; three is
  destroying the evidence somebody needs to find the actual cause.

And one that is not a prohibition but a habit: **write down what you changed and
when.** "It started on Tuesday, and on Monday I moved the GPS antenna" is worth
more than any command on this page.

# HF source diagnostics and repair

> **Audience:** contributor
> **Status:** design approved by Michael 2026-11-05; for Rob's review; no plan yet
> **Written:** 2026-11-05, at Michael's direction, with sigma's RX888 as the first case
> **Builds on:** `bin/sigmond-sdr-recover`, `bin/sigmond-radiod-watchdog`, `lib/sigmond/sdr_events.py`,
> [2026-11-04-bare-metal-install-design.md](2026-11-04-bare-metal-install-design.md)

---

## 1. Why

An HF source — an SDR, the radiod that drives it, and the clients that read radiod — fails in
more ways than one, and the symptom rarely names the cause.  A station that loses samples could
have a tired card, a poor cable, a hub that misbehaves, a radiod that falls behind, or a host that
starves radiod of CPU.  Each calls for a different repair, and some of the repairs need a person
at the machine.

The point of a station lies in gathering data.  So the suite has three jobs, in this order:
find the fault and fix it from afar where it can; when it cannot, hand a person solid evidence and
the one physical step to take; and leave a guard behind so the same fault costs less next time.
Michael named it **HF source diagnostics and repair**, a suite meant to grow as we learn.

### 1.1 What sigma showed on 2026-11-05

sigma, a bare-metal station, runs one RX888 on a four-port hub.

- At 129.6 Msps radiod lost about half its samples for hours.  radiod logged no USB transfer
  failures and used little CPU.  Nothing raised an alarm.
- Stopping radiod aborts it inside libusb, called from ka9q-radio's `rx888_close`.  After such an
  abort the card came back delivering nothing: radiod said "rx888 running" and then logged a
  full second of missing samples every second.  radiod restarted 834 times that morning.
- At 64.8 Msps, after `sdr-recover` reset the hub port, the card returned at SuperSpeed, and radiod
  still lost every sample: about 64.8 million each second.
- `sdr-recover` did its job.  It saw the wedge, reset the port (under `--force`, because the hub
  reports ganged power), saw `error -110` on return, raised "the RX888 needs a hand replug", and
  latched.  The alarm went to the journal, to `sdr-events.jsonl` and to the fleetboard heartbeat,
  which we are retiring.  Nobody heard it.
- So the evidence never said which layer failed.  The bare-card test in §5 would have.

## 2. What already exists

sigmond already carries most of a repair ladder.  The suite extends it; it does not replace it.

| Piece | What it does |
|---|---|
| `sigmond-radiod-watchdog` (timer, 2 min) | restarts a radiod that said "quitting" and hung, or fell silent |
| `sigmond-sdr-recover` (timer, 30 s) | learns the card's hub port while the card is present; on a vanished card or a wedge (stall lines plus restarts) resets the port; classifies the return; raises one replug alarm per episode and latches; `--off`/`--on` for hand judging of a hub |
| `radiod@` drop-ins | patient restart, park, restart of stale consumers after radiod returns, an event on every unasked exit |
| `sdr-events.jsonl` | one line per episode of a lost card; `smd doctor` reads it |
| `[radiod] recover_port_verified` / `recover_port_data_only` | the station's own word on whether its hub really cuts power (B4 measured two that never did) |

The gaps:

1. **Partial or total sample loss with radiod running** triggers nothing unless stall lines and
   restarts come with it.  sigma lost half its samples for hours, quietly.
2. **Nothing separates the layers.**  No test reads the card without radiod, so "the card fails"
   and "radiod fails" look alike.
3. **The ladder ends at the port.**  No rung reaches the machine's power, and no record says
   whether a site has a networked power switch.
4. **Alarms do not leave the station** once the fleetboard retires.
5. **No single report** gathers an episode's evidence for the person who must act.

## 3. Goals, non-goals, anti-goals

**Goals**

- G1.  A watchdog notices a failing HF source by itself, including a source that delivers some or
  none of its samples while radiod looks alive.
- G2.  For each episode, the suite names the failing layer (§4) and keeps the evidence that
  named it.
- G3.  The suite climbs the repair ladder (§6) by itself, gentlest step first, up to and
  including a port reset.  It stops before a machine restart and asks.
- G4.  Every episode leaves one report a person can read: what failed, what the suite ruled out,
  what it tried, what came back, and the one step left, if any.
- G5.  The alarm reaches the operator off the station.
- G6.  Downtime stays as short as the evidence allows: data gathering comes first.
- G7.  Each new fault we understand adds a test, a repair or a guard (§9).

**Non-goals**

- Fixing ka9q-radio's abort in `rx888_close`.  That belongs to Phil; the suite gathers the evidence
  for the report.
- SDRs other than the RX888 for now.  The layer model fits any source radiod drives; the tests in
  §5 start with the RX888.
- New hardware.  The suite uses what a site has, and records what it lacks.

**Anti-goals**

- **Repair a consequence.**  A restart that cannot mend the cause churns the station and fixes
  nothing (`sdr-recover`'s first rule, learned on B4 2026-08-29).  The suite acts on evidence about
  the source, never on an unhappy downstream product alone.
- **Restart the machine without a person's say.**  A machine restart stops the magnetometer,
  timing, every client and the uploads.  An operator or administrator decides (Michael,
  2026-11-05).
- **A third watchdog** beside the two that exist.  The suite grows inside them.
- **Claim a power cut that did not happen.**  A hub's descriptor does not prove it cuts power; only
  a meter or the card's LED does.

## 4. The layers and what separates them

| Layer | Evidence for it | Evidence against it |
|---|---|---|
| **Card** (RX888 hardware or firmware) | bootloader only after a verified power cut; `-71`/`-110` on a fresh power-up; the bare-card test (§5) loses samples or reads stuck bits, runs of zeros, or a spectrum with no noise floor; "ADC clock not locked"; heat | the bare-card test streams clean |
| **USB path** (cable, hub, port, controller) | link at 480 instead of 5000; xhci errors, resets, transfer failures; bare-card loss that changes with port or hub | a clean bare-card test on the same port |
| **GPSDO** (the card's reference clock) | lock lost or re-acquired in `gpsdo-monitor`; "ADC clock not locked"; a sample-rate deficit that tracks lock events; the station's timing tiers stepping together | lock steady through the episode |
| **radiod and host** | bare-card test clean while radiod loses samples; radiod off its CPU pair, IRQ on its cores, a CPU starved by other work; the `rx888_close` abort | radiod's loss persists with the host idle and the clients stopped |
| **sigmond and clients** | radiod clean (no STEPPED lines, steady status) while clients show stale anchors, no output or flapping channels | radiod itself loses samples |

**Where to look first.**  radiod has proven stable and reliable in the field unless something
fails beneath or around it: the SDR, the GPSDO, the USB cable, or an overloaded machine (Michael,
2026-11-05).  So the suite weighs evidence against those first, and names radiod only when the
bare-card test streams clean, the GPSDO holds lock, and the host has room.  The `rx888_close`
abort (§1.1) fits that pattern: radiod dies on its way out, after the card has already failed.

The suite works down this table from the cheap, harmless evidence (logs, sysfs, the USB tree)
to the tests that interrupt the stream.

## 5. The tests

**Passive (always safe, every episode):**

- USB tree: the card's path, link speed, hub, port, and whether the card shows firmware (`00f1`)
  or only the bootloader (`00f3`).
- Kernel log since the episode opened: resets, disconnects, `-71`, `-110`, xhci errors.
- radiod: restart count, STEPPED lines and their size, USB transfer failures, abort stacks from
  core dumps, its CPU use and placement against its CPU pair, the xhci IRQ's placement.
- Clients: restart counts, last output, anchor age.
- Power kit (§6): port switching as learned and as verified, and the site's power-switch record.

**Active (interrupt the stream):**

- **Clients stopped.**  Stop the clients for a minute and watch radiod's loss.  If the loss stops,
  client load explains it.
- **Bare card.**  With radiod stopped, a short program opens the card, loads its firmware, streams
  for about 30 s at the station's sample rate and at half that rate, and reports samples lost,
  transfer failures, and simple sanity checks on the samples (stuck bits, zero runs, noise floor).
  It answers the question that matters most: does the card stream without radiod?
  *(How to build it — from ka9q-radio's `rx888.c` or as a separate small libusb program — stays
  with the builder.  It must not share radiod's close path, since that path aborts.)*

Stopping radiod can leave the card dead until power returns.  So the suite runs the bare-card test
by itself only when the source already delivers nothing, or when the port can power-cycle the card
afterwards.  Otherwise the test waits for a person's go-ahead.  *(My call:)* "can power-cycle"
means a port the station verified (`recover_port_verified = true`) or a hub outlet on a networked
power switch (§6).  A ganged hub driven under `--force`, or an unverified one, does not count: B4's
two such hubs never cut power.  So on sigma the suite runs the bare-card test unattended only when
the card already delivers nothing.

**A good card**, after any test or rung, means what `sdr-recover` already requires to clear its
latch (card back as `00f1` on a SuperSpeed path, radiod ready), plus the sample-loss trigger (§7)
staying quiet for 5 min.

## 6. The repair ladder

The **kit** comes first: before it acts, the suite knows which rungs this site has.

| Rung | Step | Who decides |
|---|---|---|
| 1 | restart the clients that read radiod | the suite |
| 2 | restart radiod | the suite |
| 3 | reset the card in software: unbind and rebind, reload its firmware | the suite |
| 4 | reset the hub port (a power cut where verified, a data-only reset otherwise) | the suite |
| 5 | restart the machine: a soft reboot, or a power cycle through a networked power switch | **an operator or administrator** |
| 6 | a person at the machine: replug, another port, cable, hub or card | **a person on site** |

- **The layer chooses the first rung.**  *(My call.)*  Rung 1 runs only when the evidence points
  at the clients (radiod clean, clients unhappy); restarting clients against a failing card would
  repair a consequence.  A fault in the card, the USB path or radiod starts at rung 2.
- The suite climbs one rung at a time.  Before each rung it writes the evidence that justified
  it, and after each it checks whether the source came back.  It stops climbing as soon as the
  source runs clean.
- Cooldowns and the existing latch keep it from churning: one climb per episode, and no rung
  repeated against a fault it already failed to clear.  *(My call:)* the suite judges each rung 2 min
  after taking it, or 5 min for a sample-loss episode (the loss window).  radiod's own restarts under
  its systemd drop-in do not count as rung 2; rung 2 means a restart the suite asked for.  A source
  still bad after rung 4 latches as `sdr-recover` latches today, and the latch clears the same way.
- At rung 5 the suite stops and asks.  *(My call: the ask stays passive, a report and an alarm,
  with no approval channel.  The operator acts with `smd diag hf-source --repair --rung 5` or the
  switch itself.)*  The request says what failed, what the suite tried, what a
  restart would interrupt (the magnetometer, timing, clients, uploads), and gives the exact command
  or outlet.  An operator runs it, or says no.
- At rung 6 the suite names the one physical step the evidence points to.

**The power switch.**  The suite can test rungs 3 and 4 for itself.  It cannot detect a networked
power switch, so install asks, and the site profile records it:

```toml
[power_switch]
kind   = "..."      # the switch's make or protocol
host   = "..."      # how to reach it
outlet = "..."      # which outlet feeds the computer
# outlet_hub = "..." # optional: an outlet that feeds only the RX888's hub
```

*(My call: the table's shape; the builder fills in what each switch kind needs.)*  An outlet that
feeds only the hub gives rung 4 a real power cut on a hub that cannot switch its ports.  The
suite uses it by itself (Michael, 2026-11-05), since it stops nothing but what hangs on that hub;
the report names everything the hub carries (B4's hub also carries the TS-1 and the magnetometer
adapter), and the suite restarts those consumers afterwards.

## 7. The watchdog

`sigmond-sdr-recover` already runs every 30 s and owns the port.  The suite grows there.

- **New trigger: sample loss.**  With `clock-step-logging = yes` (sigmond writes it into every
  radiod config it generates; stock radiod leaves it off), radiod logs a STEPPED line whenever
  the samples it received fall short of GPS time, each giving the step in seconds and the
  samples missing.  The loss fraction over a window equals the sum of positive steps divided by
  the window's length.  Sustained loss above a threshold opens an episode even with radiod running
  and no restarts.  *(More than 1 % over 5 min, or more than 90 % over 30 s; Michael approved these as a
  starting point.  Tune on real stations.)*  Where the option sits off, radiod's once-a-minute measured-rate line serves as a
  coarse fallback: it scatters about ±30 ppm on a healthy card, far below 1 %.  A radiod that logs
  nothing at all falls to `sigmond-radiod-watchdog`'s silence detector, as today.
- The existing triggers stay: a vanished card, a wedge, a radiod restart loop.
- **One episode per card**, keyed by the card's serial, as `sdr-recover` keys its state today.  A
  station with two RX888s runs two ladders.  *(My call; sigma and B4 carry one card each.)*
- `sigmond-radiod-watchdog` keeps its narrow job, restarting a hung radiod, and records its rung in
  the same episode.

## 8. The report and the alarm

- **One report per episode**, kept under `/var/lib/sigmond/hf-source/`.  *(My call:)*
  `sdr-events.jsonl` stays the index: a sample-loss episode adds a new kind, `sample-loss`, to the
  same episode line and follows the same gap and INVALID rules that `smd doctor` reads today.  The
  report file takes the episode's opening time and the card's serial as its name.  `sdr-recover`
  owns the episode and the report; `sigmond-radiod-watchdog` appends its restarts to them.  The
  report holds the passive evidence, each test and its result, each rung climbed and
  what came back, the verdict (the layer, or "undetermined"), and the step left for a person.
- **`smd diag hf-source`** shows the open episode and the last few closed ones, runs the passive
  checks on demand, and with `--repair` climbs the ladder by hand.  Rung 5 runs only when named
  explicitly.
- **The station's colour.**  The suite contributes an `hf_source` block to the station's status
  envelope (`lib/sigmond/heartbeat_schema.py`), using its four verdicts: VALID (green) when the
  source runs clean; INCONCLUSIVE (yellow) while the suite climbs the ladder, or for an hour after
  it recovered the source by itself; INVALID (red) when the source needs a person, at rung 5 or 6;
  INDETERMINATE when the suite could not look.
- **Where the colour goes.**  `smd check` and `smd doctor`, station-web (read-only, public), and
  the status envelope.  Rob's plan (`tasks/plan-status-via-uploader.md`) carries that envelope on
  the station's upload to wsprdaemon.org into ClickHouse; its station producer (Phase 3) has not
  started.  A board shows the colour to whoever looks, and the server must read silence as red,
  since a dead station sends nothing.
- **Until something pushes the alarm to a person, someone must look** (Michael, 2026-11-05).  So the
  suite makes looking cheap: one command run from the devbox (`ops/bin`, beside `fleet-ssh`) visits
  every station it can reach and prints one line each, green, yellow, red, or unreachable, with the
  open episode's verdict and the step left.  An administrator runs it by hand or on a schedule.
  *(My call.)*  Pushing an alert from the server, by email or text, waits on Rob's status work and a
  decision about who receives what.

## 9. Guards sigma has already earned

Each item becomes a task in the plan:

1. A radiod crash loop raises an alarm (`smd check`, bring-up's verify) — sigma restarted radiod 834
   times unremarked.
2. Sample loss opens an episode (§7).
3. The bare-card test exists (§5).
4. Install records the port's switching: verified, data-only, or ganged.  A ganged or unverified
   hub prints a warning at bring-up: "this card cannot be power-cycled from software."
5. Install asks about a networked power switch (§6).
6. The `rx888_close` abort goes to Phil with the core-dump stacks.

## 10. Order of work

1. The sample-loss trigger and the crash-loop alarm (§7, §9.1-2).
2. The episode report and `smd diag hf-source`, passive evidence only (§8).
3. The bare-card test (§5).
4. The ladder's rung 3, and the rung 5 request (§6).
5. The power-switch record and the install questions (§6, §9.4-5).
6. The fleet sweep from the devbox (§8), then the `hf_source` block in the status envelope once
   Rob's Phase 3 carries it.
7. Proof on sigma: a full episode from trigger to verdict, and the same on one Proxmox station.

## 11. Open questions

*Answered 2026-11-05 by Michael:* the sample-loss thresholds (§7) stand as a starting point; the
suite power-cycles a hub-only outlet without asking (§6).

3. Rob built `sdr-recover` and designed the status upload.  Does he want to build this, share it,
   or review it?  (Michael asks that Rob review it.)
4. For Rob: when a station turns red or falls silent, should the wsprdaemon server push an alert to
   its operator, and through what?

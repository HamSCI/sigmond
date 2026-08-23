# The 14.110 MHz eclipse listener — a worked example

> **Audience:** scientist
> **Status:** current
> **Verified against:** sigmond e8e5bff on 2026-08-23 — Costas-array 1e7d6f6 + eclipse-reception-report + live b4 archive listing
> **Canonical for:** the 2026-08-12 eclipse Costas listener as a worked example

On 2026-08-11 a station operator was asked to receive an experimental HamSCI
signal on 14.110 MHz during the following day's European eclipse. Twenty-two
hours of I/Q were on disk by the following evening. The first analysis of that
recording concluded there was nothing in it; the second analysis — of the same
bytes, a day later — found the transmission and settled an open question about
the transmitter.

This page is that job end to end: what was decided, what was checked before the
event, what happened during the night, what the data actually said, and what
would be done differently. It is a **worked example, not the canonical
recipe** — the recipe is
[capture-quickstart.md](capture-quickstart.md), the envelope is
[station-capabilities.md](station-capabilities.md), and the design rules are the
[event-client playbook](../EVENT-CLIENT-PLAYBOOK.md). Where this page and those
disagree, they win; this page shows what following them looked like once.

The code is the public repository
[mijahauan/Costas-array](https://github.com/mijahauan/Costas-array) (package
`event_recorder`, MIT) — the same tool
[capture-quickstart.md §Option A](capture-quickstart.md#option-a--use-event-recorder)
recommends. The analysis is its
`docs/eclipse-reception-report.html`, quoted throughout.

---

## The ask

**2026-08-11.** Build something that receives a 21-tone Costas frame on
14.110 MHz during the 2026-08-12 European eclipse, greatest eclipse
17:47:06 UTC. There was no existing receiver for the signal, the specification
arrived with it, and the event would happen once.

The repository's first commit is stamped **2026-08-11T20:13:42Z** ("Design:
event-recorder, ad-hoc scheduled RF capture"); the capture's first sample is
stamped **2026-08-11T23:11:20.136992Z** — the same evening, **2 h 58 min and 28
commits later**. By the time the analysis was finished on 08-13 the repository
held **45 commits**, twelve of them that last day
(`git log --date=format-local:%Y-%m-%dT%H:%M:%SZ` at `1e7d6f6`; first sidecar in
the B4 archive). The README puts it plainly: "Built on ~1 day's notice for the
2026-08-12 European eclipse."

What the transmitting group supplied, and what the recording later confirmed:

| Property | Value | Verified where |
|---|---|---|
| Tone map | 21 tones at `300 + 120·k` Hz audio, k = 0…20 → 300–2700 Hz | repo README §"What it expects" |
| Costas sequence | `3,0,8,2,18,6,15,14,9,7,20,16,19,11,17,1,13,4,5,10,12` | reception report §"Two independent confirmations" — recovered from the spectrum, then compared |
| Frame | 24 slots × 40 ms: slot 0 silence, slot 1 pilot, slots 2–22 the 21 tones, slot 23 silence, then 40 ms guard | README §"What it expects" |
| Repetition | one frame per GPS second, GPS-locked | README, same |
| Pilot | **ambiguous**: the published frequency array said 1000 Hz, its DDS phase word implied 700.005 Hz | reception report §"The pilot question is settled" |

That last row is the reason the experiment was worth running at all, and it
shaped the detector: the pilot frequency was never assumed, only measured —
"`--pilot-hz` supplies a value to *check*, and a disagreement is reported rather
than resolved in the hint's favour" (README §"It works").

Two things were **never confirmed**, before the event or since: the
transmitters' **on-air schedule** and their **locations**. Both are still listed
as open questions to the transmitting group at the end of the reception report.
Nothing on this page turns a detection into a propagation path, because nothing
in the archive can.

---

## The decisions

### Capture first

The event was in under 24 hours and the analysis code did not exist. So the
recording envelope was chosen to be **wider than any analysis that might be
written later**, and the tool was built to write bytes and timestamps and
nothing else — the playbook's
[Rule 1](../EVENT-CLIENT-PLAYBOOK.md#rule-1--capture-first-process-later-always).
The job file on the station says so in its own comment:

> Wide filter edges are deliberate (capture-first): if the dial or tone-mapping
> assumption is wrong, a wide capture still contains the signal.
> — `/etc/event-recorder/jobs/eclipse-costas-14110.toml`, live on B4, 2026-08-23

That judgement is the one that paid. The first analysis of this recording said
there was nothing in it. Because the I/Q was kept, the second analysis could
disagree.

### The envelope, and why

This is the job file as it stands on the station today (read-only, 2026-08-23).
The repo README publishes a tidied version that differs in four lines: it drops
`radiod_encoding`, `agc` and `gain_db`, and sets `start_utc` to
`2026-08-12T01:14:11Z` — when the final run actually began — rather than the
`23:20:38Z` the file has carried since the first night:

```toml
name            = "eclipse-costas-14110"
frequency_hz    = 14_110_000
preset          = "iq"
sample_rate     = 12000
encoding        = "f32"
radiod_encoding = "f32"
agc             = false
gain_db         = 0.0
low_edge        = -5000
high_edge       = 5000
lead_in_sec     = 60
segment_sec     = 3600
out_dir         = "/var/lib/event-recorder/eclipse-costas-14110"
start_utc       = "2026-08-11T23:20:38Z"
stop_utc        = "2026-08-12T22:00:00Z"
```

Each line, and the reason, from the reception report's §"How the capture was
made" table:

| Choice | Why |
|---|---|
| 14.110000 MHz centre | as published |
| `iq`, ±5 kHz edges | "complex baseband keeps both sidebands for reanalysis" — and 10 kHz of span around a 2.4 kHz signal is the capture-first margin |
| 12 000 Hz | "covers the 300–2700 Hz tone span with margin" |
| `f32` on disk (`cf32_le`) | "no AGC, no requantisation, full dynamic range" |
| AGC off, gain 0 dB | "amplitudes stay comparable across the whole run" |
| 01:14 – 22:00 UTC (as finally run) | "greatest eclipse 17:47:06 UTC, bracketed by hours" |
| 1 h segments, each with its own absolute UTC | a segment is a unit you can lose without losing the archive |

The AGC line deserves a caveat this page did not know at the time and
[hardware/character.md](../hardware/character.md#the-front-end-agc-is-real-it-is-on-and-it-moves)
now states: `agc = false` turns off the **channel** AGC, not the RX888's
front-end AGC, which is a separate thing and is on by default. Amplitudes are
comparable across the run in the sense that nothing in the channel is chasing
them; the front end may still have moved. The Costas result is a *rank* statistic
— which bin is strongest inside each 40 ms slot — so it is immune to that. An
absolute-amplitude product would not have been.

### Load and disk, checked before

The station is a production DASI2 host already running radiod plus its normal
client set, so one added 12 kHz I/Q channel had to be affordable — the playbook's
[budget-the-load](../EVENT-CLIENT-PLAYBOOK.md#budget-the-load-before-you-choose-your-architecture)
rule, and the numbers now live in
[station-capabilities.md §load budget](station-capabilities.md#how-many-channels-you-may-add--the-load-budget).
`event-recorder` refuses to start if the disk cannot hold the projected capture
plus 20 %: `preflight_disk()` raises `InsufficientDiskError` against
`spec.projected_bytes * DISK_MARGIN`, `DISK_MARGIN = 1.20`
(`src/event_recorder/scheduler.py`).

The measured cost, from the station's own 5-minute status log during the run:
**96 000 B/s** of file growth — 345.6 MB/h, exactly the
[storage arithmetic](station-capabilities.md#storage-per-channel-hour) for
12 kHz complex float32 — against 198 GB free at the start
(`/var/log/event-recorder-status.log`, live B4, 2026-08-23). Note the other
number in that log: free space fell from 198 G to 126 G across the run while the
capture itself wrote 7.7 GB. **The station's own clients are writing too.**
Budget for the host, not for your channel.

### SigMF, and the anchor

Output is SigMF: a raw sample blob plus a JSON sidecar, no library needed to emit
it (`src/event_recorder/sigmfmeta.py` writes the JSON directly and says why).
Each sidecar carries the absolute UTC of sample 0, the RTP timestamp it came
from, and an explicit `event:timing_state` of `anchored` or `unanchored` —
"an unanchored entry omits `core:datetime` entirely rather than write a time we
do not believe" (`sigmfmeta.py::capture_entry`). That is the playbook's
[record the timing anchor](../EVENT-CLIENT-PLAYBOOK.md#record-the-timing-anchor-not-just-the-samples)
rule implemented literally. The first segment's sidecar, live on B4 today:

```json
{
  "core:sample_start": 0,
  "core:frequency": 14110000.0,
  "event:timing_state": "anchored",
  "core:datetime": "2026-08-11T23:11:20.136992+00:00",
  "event:rtp_timestamp": 445894560
}
```

The UTC comes from `rtp_to_utc(rtp, channel)` — radiod's GPS-referenced pair, not
the host clock (`scheduler.py::_rtp_to_utc`). What that pair is and is not
guaranteed to be is
[character.md §The anchor pair is not atomic](../hardware/character.md#the-anchor-pair-is-not-atomic);
for a 40 ms symbol grid it was ample, and the detector searches frame phase
anyway.

### A scheduler, `systemd-run`, and deliberately no unit

The job declares `start_utc`, `stop_utc`, `lead_in_sec` and `segment_sec`, and
`run_capture()` does the waiting, the segment rolling and the stopping. The
capture was launched not as a packaged service but as a transient one, on the
station VM:

```bash
sudo systemd-run --unit=event-recorder-eclipse --collect \
  --property=Restart=on-failure --property=RestartSec=10 \
  --property=User=hamsci ... -m event_recorder -v capture --job .../eclipse-costas-14110.toml
```

(from the repo's own execution plan, `docs/superpowers/plans/2026-08-11-event-recorder.md`;
the unit name `event-recorder-eclipse` is the one the watchdog on B4 restarts.)

`deploy.toml` states both omissions and their reasons, and both were right for a
one-off:

> deliberately no `[[radiod.fragment]]`. §15 fragments declare STATIC channel
> contributions written into `radiod@<id>.conf.d/`. This client creates its
> channel dynamically for the duration of a capture window and lets radiod's
> auto-destruct lifetime reclaim it, so a permanent fragment would be wrong — it
> would hold a channel open forever for a one-off capture.
>
> no `[systemd]` units yet. Captures are launched per-job via `systemd-run`.
> A packaged `event-recorder@.service` is the promotion-to-full-component step,
> out of scope for the eclipse deadline.

The channel is created with `lifetime=LIFETIME_FRAMES` (6000 frames ≈ 120 s) and
kept alive by a keepalive thread, so an abandoned capture cannot leave a channel
streaming to nobody (`src/event_recorder/channel.py`). Nobody had to edit
`/etc/radio` on a production station, and nothing had to be cleaned up
afterwards — which is why
[capture-quickstart.md](capture-quickstart.md#option-a--use-event-recorder)
makes a mandatory `lifetime` the first rule of Tier 0.

---

## What was verified before the event

The playbook says
[prove it against a known signal](../EVENT-CLIENT-PLAYBOOK.md#prove-it-against-a-known-signal):
tune 1 kHz below WWV and confirm the carrier lands at exactly +1000 Hz. The
station still holds the evidence that this was done — 2026-08-11T23:06:47Z, four
short segments (three full 30 s, the fourth 8.8 s), and the sidecar says the
dial:

```
"core:frequency": 9999000.0,
"event:timing_state": "anchored",
"core:datetime": "2026-08-11T23:06:47.116490+00:00"
```

(`/var/lib/event-recorder/wwv-smoke2/`, read-only on B4, 2026-08-23.) 9.999 MHz
is WWV 10 MHz minus 1 kHz — the test as prescribed, four minutes before the
eclipse capture began. What the archive proves is that the chain produced valid,
anchored, correctly-dialled SigMF; **whether the +1000 Hz carrier check itself
was read off is not recorded** anywhere in the repo or on the station. Write
that number down when you run it.

Then the wire format, which is the trap that eats these captures. The job's own
comment block preserves the original finding:

> `radiod_encoding = "s16"`: VERIFIED 2026-08-11 that this radiod serves S16BE
> only and an F32 request yields a **1.96x-rate garbage stream that still
> reports 100% completeness**.

That is the playbook's
[first station trap](../EVENT-CLIENT-PLAYBOOK.md#station-traps-worth-knowing)
and now ledger row 39: `ensure_channel()` returns an encoding it has not
verified, and `RadiodStream` decodes with it, so reading 2 bytes where 4 were
sent yields a plausible, well-formed array at twice the sample count with clean
completeness and zero gaps. The mechanism —
`OUTPUT_ENCODING` is a *second command*, not part of the create packet — is
[character.md §The encoding you asked for is a second command](../hardware/character.md#the-encoding-you-asked-for-is-a-second-command).

The client's answer, committed on 2026-08-12 mid-capture ("feat(channel):
measure the wire instead of trusting request or radiod's report"), is to stop
believing anyone: wait `DEFAULT_SETTLE_SEC = 4.0` for the encoding command to
land, then measure payload bytes ÷ RTP ticks ÷ components over a 3 s window and
assert *that* onto the channel
(`channel.py::probe_wire_bytes_per_component`,
`_measure_and_assert_encoding`). The scheduler adds a second, independent
tripwire: a delivered-sample-rate deviation over `SAMPLE_RATE_FATAL_FRACTION =
0.25` **stops the capture in the first seconds** rather than recording hours of
garbage (`scheduler.py`).

That work was re-proven against WWV before it was trusted with the eclipse. At
2026-08-12T01:12:34Z, one more 9.999 MHz test capture — and its sidecar is the
first in the archive to carry a measurement rather than a claim:

```json
"event:radiod_encoding": "f32",
"event:asserted_encoding": 4,
"event:wire_bytes_per_component": 4.0
```

(`/var/lib/event-recorder/f32d/`, live on B4.) Two minutes later the eclipse job
was switched from `s16` to `f32` and restarted.

Finally, a watchdog — the playbook's
[unattended means watchdogged](../EVENT-CLIENT-PLAYBOOK.md#unattended-means-watchdogged)
rule. `/usr/local/bin/event-recorder-watchdog` on B4 states its own threat
model:

> RadiodStream recovers from socket ERRORS, but when packets simply stop
> arriving (radiod restart, RX888 USB wedge) it spins on `socket.timeout`
> forever — no samples, no exception, no exit. The capture process therefore
> stays "active" and systemd's `Restart=on-failure` never fires. Without this
> watchdog a mid-event stall would silently record nothing through the eclipse
> maximum and still report success at 22:00Z.
>
> Deliberately EXTERNAL: it never touches recorder code, only observes file
> growth and restarts the unit.

It polls every 30 s, restarts the unit after `STALL=180` seconds without file
growth, and stops at a hard-coded deadline of 2026-08-12T22:05:00Z. Alongside it
a 5-minute status line recorded `active` / `wd` / segment count / bytes /
growth / load / free disk to `/var/log/event-recorder-status.log` — the
playbook's
[signal-level heartbeat](../EVENT-CLIENT-PLAYBOOK.md#strongly-recommended--each-of-these-would-have-caught-a-real-problem)
in its cheapest possible form, and the reason the timeline below can be
reconstructed at all.

---

## The night of

All times UTC, all from the station's own files (read-only, 2026-08-23).

| Time | What happened | Evidence |
|---|---|---|
| 08-11 23:06:47 | WWV smoke test at 9.999 MHz, four 30 s segments, anchored | `wwv-smoke2/` sidecars |
| 08-11 23:11:20 | Eclipse capture run 1 begins, 465 s | first archive sidecar |
| 08-11 23:19:38 | Restart; the job's 60 s `lead_in_sec` ahead of its `start_utc` of 23:20:38 | segment name + job file |
| 08-11 23:31:25 | Watchdog started, `stall=180s` | `event-recorder-watchdog.log` |
| 08-11 23:31:41 | Capture process dies, leaving a **`.sigmf-data` with no sidecar** | orphan file mtime |
| 08-11 23:31:55 | Watchdog: `ALARM unit inactive before window end -- restarting` | watchdog log |
| 08-12 00:21 | 5-minute status log running: `growth=96000/s free=198G` | status log |
| 08-12 00:25 – 01:13 | Four separate F32 test jobs (`f32-smoke`, `f32b`, `f32c`, `f32d`) while the eclipse capture keeps recording (one ~1 s restart at 00:42:31) | directory mtimes; archive sidecars |
| 08-12 01:12:34 | `f32d` on WWV measures **4.0 bytes/component** on the wire | `f32d` sidecar |
| 08-12 01:13 | Job file edited: `radiod_encoding` `s16` → `f32` | job file mtime + content |
| 08-12 01:13:59 | Watchdog: second `ALARM unit inactive` → restart issued 01:14:02 | watchdog log |
| 08-12 01:14:11 | Final run begins. Twenty unbroken hourly segments follow, then a 724 s tail | the 21 sidecars 01:14:11 → 21:14:11 |
| 08-12 17:47:06 | Greatest eclipse. That hour is one of the cleanest nulls in the set | reception report |
| 08-12 21:26:16 | Last sample (byte-derived); at the next status tick, **21:26:30**, recorder **and** watchdog are both `inactive` — 34 min before the declared 22:00 stop | segment size ÷ 8 ÷ 12 000; status log `active=inactive wd=inactive` |

Two honest readings of that table:

- **The watchdog fired twice**, both times before 01:15Z, and both times on its
  *unit-inactive* branch (`ALARM unit inactive before window end`) — not the
  socket-timeout stall branch it was written for, which never triggered. Neither
  restart cost more than 33 seconds of coverage. Read the second firing
  cautiously: it came 30–60 s after the operator's edit to the job file (mtime
  01:13) and may have raced an intentional stop rather than caught a fault. The
  stall detector the watchdog exists for was never exercised in anger — so test
  yours by killing the live capture, as the playbook says; an untested watchdog
  buys false confidence.
- **"Zero gaps" means zero gaps *inside* segments.** Four restarts in the first
  two hours left roughly **58 seconds** of wall clock unrecorded, all before
  01:15Z, all in the quiet-hours baseline, none within sixteen hours of the
  event. The archive tells you this itself, because every segment carries its own
  absolute UTC — which is precisely why it is worth writing one per segment.
- The final stop, 34 minutes early with both processes going down inside the
  same 5-minute tick, was not the scheduler reaching `stop_utc`. **Nothing in the
  repo or on the station records why**, and the last segment's sidecar was
  written, so the writer closed cleanly. The window had already been covered.

---

## What was captured

Live listing of `/var/lib/event-recorder/eclipse-costas-14110/` on B4,
2026-08-23:

| | |
|---|---|
| Files | **51** — 26 `.sigmf-data` + 25 `.sigmf-meta` |
| Bytes | **7,683,666,653** (7.68 GB); 7,683,647,520 of that is I/Q |
| Duration | **22.23 h** (7,683,647,520 ÷ 8 B/sample ÷ 12 000 Hz = 80,038 s) |
| First sample | 2026-08-11T23:11:20.136992Z |
| Last segment starts | 2026-08-12T21:14:11.085379Z, running 724 s |
| Anchored | **25 of 25** sidecars `"event:timing_state": "anchored"` |
| `gap` annotations | **0**, across every sidecar |
| Wire format recorded | 21 sidecars `"event:radiod_encoding": "f32"` with `asserted_encoding: 4` and `wire_bytes_per_component: 4.0`; 4 (the pre-01:14 runs) `"s16"` with no measurement |

> The reception report's own §"How the capture was made" table says **23 × 1 h
> segments** and **7.9 GB**; the archive on B4 holds **26 segments** and
> **7,683,666,653 bytes**. This page cites the archive.

The 26th data file is the **orphan** from 23:31:41 — a segment whose process died
before its sidecar was written. That is designed for, not an accident:

> An orphaned `.sigmf-data` is expected in normal operation, not a rare accident:
> metadata is written only when SegmentWriter finalizes a segment … Giving up on
> the whole directory over this would be exactly the silent-failure mode this
> project exists to avoid.
> — `src/event_recorder/analyze/costas.py::_load_or_recover`

The orphan is recoverable because the **filename** carries a UTC stamp; the
analyzer falls back to it and marks the result as ~1 s accurate rather than the
sub-millisecond a real `core:datetime` gives. Name your segments so that
lexicographic order equals chronological order and the name alone can rescue a
file — the playbook says this too, under
[unattended means watchdogged](../EVENT-CLIENT-PLAYBOOK.md#unattended-means-watchdogged).

---

## The result — and the day it was wrong

### 2026-08-12: "no detection"

The first sweep of the archive surfaced **133 candidate seconds** in the
19:14–20:14Z hour and rejected them. From the reception report:

> They looked like textbook false positives — plausible scores, a stable frame
> offset, and a pilot pinned at 695 Hz, suspiciously close to a predicted
> 700.005 Hz. We concluded the detector was being fooled by a carrier near
> 695 Hz and set out to build a gate that would reject them.

The verdict of that day was: nothing on 14.110 MHz reached the station. The
supporting measurement was a hand-run per-tone check reporting that on the
strongest candidate the expected Costas tone led in **0 of 21** slots.

### 2026-08-13: the same bytes, the opposite answer

> They were receptions. The 695 Hz "interferer" **was** the pilot tone. The gate
> built to throw them away confirms them instead. The real error ran the other
> way the whole time: the detector was **discarding signal**, and every
> diagnostic we trusted agreed with it.

And on the measurement that produced the wrong verdict:

> It does not reproduce: the same frames measure 17–20 of 21. That check was run
> at the wrong frame alignment, and nothing tested it, because it confirmed what
> we already believed. **A verification needs its own negative control or it can
> only agree with you.**

Four changes turned the null into a detection, all in the reception report
§"The four changes that produced the detections":

1. **Test the permutation, not the energy.** `score` measures how concentrated a
   slot's energy is — "noise concentrates too, and a carrier concentrates
   beautifully." `rank` counts slots whose strongest bin is the expected tone.
   Chance agreement is 1 in 21.
2. **Subtract a per-bin median** so carriers stop hiding the signal. A carrier
   occupies its bin in all 21 slots and is almost entirely median; a Costas tone
   occupies its bin in one slot of 21 and survives. The same frame goes from
   rank 1/21 to 21/21. "On daytime HF this is the normal condition, not an edge
   case."
3. **Choose the frame alignment by rank, not by score.** Measured on this
   segment: a fixed-alignment rank scan finds 688 qualifying seconds; selecting
   the offset by score and then ranking the winner finds **zero**.
4. **Stop score-thresholding, and untangle it from phase acquisition.**
   `score >= 3` discarded roughly seven of every eight real detections — and the
   same number secretly governed how much the aligner trusted the recording's
   timestamp. "Two different questions had been sharing one number."

### What the data says

The detection hour against its negative control, from the reception report's
table:

| Measure | 19:14–20:14Z | 03:14–04:14Z (control) |
|---|---|---|
| Mean rank | **8.73** | **1.00** |
| Median rank | 9 | 1 |
| Maximum rank | **21 / 21** | 6 / 21 |
| Seconds at rank ≥ 15 | **688 (19.1 %)** | 0 |
| Seconds at rank ≥ 20 | 131 | 0 |
| Perfect 21 / 21 | **62** | 0 |
| Median measured pilot | **695 Hz** | — |

On the best frame the peak bin is the expected Costas tone in **20 of 21 slots**,
**6.9–18.2 dB** above the in-slot median (median excess **14.1 dB**).

Three independent things make that a reception rather than a scoring artefact:

- **The sequence comes out of the spectrum.** Taking the strongest bin in each
  slot, with no reference to the Costas array, yields
  `3,0,8,2,18,6,15,14,9,7,20,16,19,11,17,1,13,4,5,10,12` — the published
  sequence.
- **Random permutations do not fit it.** Scored against that same data-derived
  sequence, 2000 random permutations average **1.02** matches and peak at 5;
  ascending order scores 0; the true array scores 20.
- **Phase repeats across frames**, which magnitude never touches. Across the 62
  perfect frames the 2100 Hz tone clusters at R = 0.97 against the control
  hour's 0.24; all 21 tones clear a chance level of 0.13.

And the open question the experiment existed to answer is closed. **The pilot
measures 695 Hz on a 5 Hz search grid, in every hour it appears — not the
published 1000 Hz.** The DDS phase word (700.005 Hz) was right and the frequency
table was wrong. The frame-alignment search independently puts the peak at
−5 Hz, "two unrelated measurements agreeing the transmitter sits about 5 Hz low."

### The 24 nulls are the point

All 26 segments were swept with identical settings and no per-segment tuning
(`docs/figures/sweep-results.jsonl`, one JSON row per segment). Detections fall
in exactly **2 of 26** segments — the 18:14Z hour (76 seconds at rank ≥ 15) and
the 19:14Z hour (688) — for **764 qualifying seconds** across the capture. The
other **24 segments return a clean null**, with mean ranks between **0.93 and
1.56** against a noise expectation of exactly 1.00, and not one qualifying
second among them.

> The 24 null hours are the strongest control in this report. Each is an
> independent hour of real off-air data through the same pipeline … A method
> that manufactured structure would manufacture it here too.

One null is named rather than averaged away: the 16:14Z hour sits at mean rank
**1.564** and reaches rank 8 — below the gate, not a detection, but the only hour
outside the opening that departs from noise at all, about ninety minutes before
the first confirmed reception. "Either an early marginal path or a fluctuation —
with one hour and no control day, this data cannot say which."

### What this does **not** show

Stated as plainly in the report as it is here:

> Greatest eclipse was 17:47:06 UTC, and the hour containing it is one of the
> cleanest nulls in the set. Reception begins in the following hour and builds.
> On its face that is an ordinary evening opening on 20 m … and **nothing here
> establishes an eclipse effect**. Doing that needs a control day on the same
> path at the same hours, which this capture does not contain.

Add to that: the transmitters' schedule and locations were never confirmed, so a
null hour cannot be distinguished from an hour with no transmission, and a
detection cannot be turned into a path. What the capture establishes is that the
signal is receivable, that the detector finds it, and that the pilot is 695 Hz.

---

## What we would change

Grounded in the playbook and in what the archive itself shows. In rough order of
regret prevented:

1. **Confirm the transmit schedule and locations first.** It is the cheapest item
   on the list and the one that limits every conclusion. Twenty-two hours of disk
   bought two hours of signal and no propagation result, because there is no way
   to tell a quiet path from a silent transmitter.
2. **Write gap evidence positively, not as an absence.** No sidecar in the
   archive carries a `gap` annotation, and a clean capture is therefore
   indistinguishable from broken gap accounting. Emit a per-segment count —
   `0` included — and radiod's own `FILTER_DROPS` alongside it. This matters more
   than it sounds on this hardware, where
   [loss is zeros, not gaps](../hardware/character.md#loss-is-zeros-not-gaps)
   and a zero-filled block is silently well-formed
   ([station-capabilities.md §Loss semantics](station-capabilities.md#loss-semantics--what-a-gap-is)).
3. **Measure the wire from the first segment, not the fifth hour.** Four sidecars
   record `radiod_encoding: s16` with no measurement beside it, because the wire
   probe was written mid-capture. It is thirty lines of code
   ([capture-quickstart.md §Option B](capture-quickstart.md#what-the-script-does-that-matters)
   has it) and it is the difference between an archive that documents itself and
   one you have to reason about later.
4. **Record what the known-signal test returned**, not just that it ran. The WWV
   captures are on disk and correctly dialled at 9.999 MHz; the +1000 Hz reading
   is nowhere. One line in a log makes the pre-flight check auditable a year
   later.
5. **Give the running code a git identity.** `/opt/git/event-recorder` on B4 is
   **not a git checkout** (`fatal: not a git repository`, live 2026-08-23), and
   the sidecars record `core:recorder: event-recorder/0.1.0` — a version string
   that did not change while the code did. Nothing in the archive says which
   commit produced which segment. Stamp the commit into the sidecar; the
   playbook already warns that
   [reported version ≠ code actually running](../EVENT-CLIENT-PLAYBOOK.md#station-traps-worth-knowing).
6. **Keep the watchdog external and test it by killing the capture.** This one
   was already right, and it is the item most often skipped.
7. **Promote to a packaged component only if the signal recurs.** `deploy.toml`'s
   two NOTE blocks are the correct call for a one-off: no `[[radiod.fragment]]`
   (a static fragment would hold a channel open forever), no `@.service` (a
   transient `systemd-run` unit is enough for one night). If 14.110 MHz becomes a
   standing observation, that is the moment to add both and cross into Tier 1 —
   becoming-a-client.md *(being written)*, and the playbook's
   [Rule 2](../EVENT-CLIENT-PLAYBOOK.md#rule-2--start-from-the-client-contract-not-from-the-radio).
8. **Never let a hand-run verification stand without its own negative control.**
   The single most expensive lesson here cost a day and a wrong public answer,
   and it cost nothing to fix: run the same check on an hour you believe is
   empty, and see whether it agrees.

For calibration, the playbook's
[§What "good" cost](../EVENT-CLIENT-PLAYBOOK.md#what-good-cost-for-calibration)
measured this build at the end of the deadline: about **2,900 lines of Python**,
226 tests, **~3 % of one core** at runtime, 96 kB/s of data, **one day** from
idea to live capture, and **11 defects caught pre-deployment — none of which
raised an exception**. (The repository has moved since: 3,353 source lines and
216 test functions at `1e7d6f6`, after the rank-gate and WAV-reader work of
2026-08-13.) That defect figure is the shape of the whole problem: on this kind
of software,
[every failure will be silent](../EVENT-CLIENT-PLAYBOOK.md#assume-every-failure-will-be-silent).

---

## Where the data and the code are

- **The archive** — `/var/lib/event-recorder/eclipse-costas-14110/` on B4
  (AC0G): 51 files, 7.68 GB, 26 segments, full I/Q retained. The report's design
  principle: "Every claim here can be recomputed from the recording, and
  re-examined with methods nobody has written yet — which, given that our first
  analysis of this data reached the opposite conclusion, is the part of the
  design that mattered most."
- **The code** — <https://github.com/mijahauan/Costas-array>, package
  `event_recorder`, MIT. The detector needs numpy and nothing else; the capture
  side needs ka9q-python and a running radiod.
- **The reception report** — `docs/eclipse-reception-report.html` in that repo,
  with its figures generated from the recording by `docs/figures/` and the
  full 26-segment sweep in `docs/figures/sweep-results.jsonl`.
- **Analysing your own recording** — the detector reads `.wav` and SigMF and
  takes `--dial-hz` if your 0 Hz is not 14.110000 MHz. "Read rank, not score.
  That is the whole lesson above, compressed into one line of output."

A note on the address: the repository is under a personal GitHub account rather
than the `HamSCI` organisation, which is why this page links it as an external
URL while every other cross-repo link in this tree resolves under
`HamSCI/`. That is [ledger row 41](../contributor/docs-gap-ledger.md).

---

## Next

- The recipe this example follows: [capture-quickstart.md](capture-quickstart.md) ★
- The envelope it chose from: [station-capabilities.md](station-capabilities.md) ★
- The rules it was built to: [EVENT-CLIENT-PLAYBOOK.md](../EVENT-CLIENT-PLAYBOOK.md) ★
- How the hardware behaves underneath it: [hardware/character.md](../hardware/character.md) ★
- Turning a capture into a station product: becoming-a-client.md *(being written)*
- Where the data lands and how time is carried: data-and-timing.md *(being written)*

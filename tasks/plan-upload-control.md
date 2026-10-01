# plan: upload control — what a station keeps, and how it ships it

**Status — 2026-10-01: PROPOSED, nothing built.**  Michael asked for it, then
widened it the same day: *"make this intelligent and adaptable to network
conditions."*  A station must handle an ordinary office link, a link that
fails without warning, a link that never runs fast, and a link that opens
only on a timetable.  The South Pole, which reaches the outside world only
during satellite passes, stands as the hardest case, not the only one.
USAP publishes those passes weekly as the South Pole Weekly Satellite Service
Schedule (<https://www.usap.gov/technology/1935/>).

The plan separates two questions that today's switch runs together:

1. **What does the station keep, and does it ship at all?**  The operator
   chooses, with one of three modes.
2. **When it ships, how?**  The station works this out from what it measures,
   plus whatever limits the operator declares.  Local storage ties the two
   together.  The disk decides how long the station can wait for a link
   that cannot keep up.

## 1. Three modes, chosen by the operator

| Mode | Stores | Ships | Typical use |
|---|---|---|---|
| `discard` | only a short working buffer | nothing | bench setup of a machine bound for another site; diagnostics |
| `hold` | everything | nothing | diagnostics; a pause before a known outage; awaiting PSWS registration |
| `upload` | everything not yet acknowledged | yes, adapting to the link | normal operation |

```
smd upload status
smd upload on                       # = upload
smd upload hold  [--reason ..]
smd upload off   [--reason ..]      # = hold, the safe meaning of "off"
smd upload discard --reason ..      # asks for confirmation
```

`off` means `hold` because it keeps data, and losing data should take a
deliberate word.  `discard` asks for confirmation and requires a reason.

**Leaving `discard` never ships the past.**  When a bench machine moves to
its real site and switches to `upload`, sigmond advances every pipeline's
cursor to the source's current head first.  Nothing recorded on the bench,
under the bench's antenna and perhaps the wrong identity, ever reaches PSWS.
Leaving `hold` does the opposite: it ships the whole backlog, oldest first.

`discard` still lets the recorders run and write.  It stops the cache from
growing, by trimming each product to a short working window (an hour, say)
so that diagnostics have something to read.

The heartbeat obeys none of these modes.  A station in `discard` or `hold`
still reports itself, and the fleetboard shows the mode and its reason.

`smd config uploads enable|disable` stays as an alias: `enable` maps to
`upload`, `disable` to `hold`.

## 2. Under `upload`: one adaptive shipper for four link conditions

Michael named four conditions.  Each one comes from a constraint that is either
absent or present, so the shipper needs only two optional inputs and one
measurement:

| Condition | Declared rate cap | Declared windows | What the shipper does |
|---|---|---|---|
| (a) unforeseen outages | — | — | detects the outage, probes cheaply, resumes, loses nothing |
| (b) narrow link, any time | yes | — | paces itself under the cap, small products first |
| (c) narrow link, timetable | yes | yes | paces under the cap, inside windows only |
| (d) wide link, timetable | — | yes | ships at full rate, inside windows only |

Every station handles (a), because every link fails sometimes.  (b), (c) and
(d) add declarations on top.

```toml
# coordination.toml
[uploads]
mode = "upload"                    # discard | hold | upload
reason = ""

[uploads.link]
rate_limit = ""                    # e.g. "2 Mbit/s"; empty = adapt by measurement
volume_per_day = ""                # e.g. "2 GB"; a metered or capped plan
schedule = ""                      # path to a window file; empty = always open
```

### What the shipper measures

- **Reachability, per destination, not per pipeline.**  One cheap probe (a
  TCP connect to PSWS, wd30, wsprnet) tells every pipeline using that host
  whether the link works.  Five pipelines need not each discover an outage
  by failing a real transfer.
- **Throughput, per destination.**  The shipper times every transfer it
  makes and keeps a running estimate.  With no declared cap, that estimate
  serves as the link's rate.
- **Production, per pipeline.**  Bytes per day that each source adds.
- **Backlog, per pipeline.**  Bytes and age of the oldest unshipped item.
  hs-uploader's `backlog.summarize()` already reads the honest figures from
  the watermark store.

### How it decides

- **Failures sort into kinds.**  A refused or timed-out connection marks the
  destination *down*.  The pipeline waits, and the probe brings it back.  It
  uses no retry count and writes no dead letter.  A destination that rejects
  the payload (bad key, bad station id, malformed data) marks the payload
  *permanent*, sends it to dead letter, and alarms.  Only that second kind
  needs a person.
- **Order.**  Heartbeat first, then spots, then the magnetometer, then GRAPE.
  Small, time-sensitive data goes ahead of large, patient data.  Within a
  pipeline, oldest first.
- **Pacing.**  Under a rate cap or a daily volume cap, a token bucket per
  destination holds the shipper below the limit, leaving room for the
  station's other traffic.
- **Window edges.**  With a schedule, the gate opens a few minutes after a
  window starts and closes a few minutes before it ends.  Before starting a
  batch, the shipper estimates its transfer time from measured throughput.
  If the batch cannot finish in the window, the shipper picks a smaller one
  or waits.

### The capacity sum, and what the disk has to cover

From those measurements the station can compute, for each day:

- **P**, the bytes it produces;
- **C**, the bytes the link can carry: usable rate × open hours, after the
  margins, and capped by `volume_per_day`;
- **B**, the backlog it holds now;
- **S**, the cache it may use, taken from the disk budget.

Three regimes follow:

- **C well above P.**  The backlog drains after every gap.  The disk needs to
  cover only the longest gap, plus a day of margin.
- **C close to P.**  The backlog drains slowly.  A long outage takes days to
  recover from, and the station should say how many.
- **C below P.**  The backlog grows without bound.  The disk fills in
  (S − B) / (P − C) days.  The station must say so long before then.

`smd upload forecast` prints P, C, B, S and the days until the cache fills,
per pipeline and in total.  The fleetboard shows the same.  A station whose
forecast reaches the disk within a set horizon (a week, say) alarms.

When the cache fills anyway, the station must drop something.  It never does
so silently: it names the pipeline and the time span lost.  Which product
goes first needs a decision from Michael and rob.  The candidates:

- drop the oldest of the lowest-priority product (GRAPE before spots);
- drop the oldest of every product, keeping the newest days whole;
- stop shipping GRAPE altogether and keep the link for spots and magnetometer.

A further lever sits outside this plan: a producer could make a smaller
product when C runs short, such as GRAPE at lower resolution.  That changes
the science, so it belongs to the science owners.

## 3. What the code does today

Each of these comes from reading the code.  I have not yet observed any of
them on a station.

- `smd config uploads disable` drops the data pipelines from
  `/etc/hs-uploader/pipelines.toml`.  Their cursors stay in the watermark
  store, so I expect `enable` to ship the gap.  That behaviour fits `hold`.
  It does not fit a station disabled for having no antenna.  Step 1
  confirms it.
- **A long outage fills the dead-letter table.**  `RetryPolicy` backs off
  1, 2, 4 … seconds, capped at 300, for 12 attempts: about 19 minutes.  Then
  it dead-letters the payload.  The cursor holds, so the next pump re-reads
  the same batch and starts again.  A 4-hour gap writes about 12 copies per
  pipeline, and a monitor would read that as a fault.
- **The wspr store forgets after 24 hours.**  `sigmond-storage-trim-wspr`
  deletes spots older than 24 h whether or not anyone shipped them.  GRAPE
  survives, because its pipeline declares `retention = "keep"`.
- `[disk_budget]` already exists (`root_path`, `warn_percent`) and
  `harmonize.rule_disk_budget` checks it.  The cache size S should come from
  it rather than from a new setting.

## 4. The South Pole, from the current issue

USAP publishes `cel.txt`, a fixed-column listing of confirmed TDRS events
(`26/274/172400` means year 26, day 274, 17:24:00 UTC), and a PDF covering
every satellite.  Only the PDF lists DSCS and Skynet.

The issue dated 2026-09-20 shows four broadband passes a day:

| Satellite | UTC window | Length |
|---|---|---|
| DSCS-B13 | 23:57 → 05:49 next day | 5 h 53 m |
| DSCS-A03 | 02:46 → 07:19 | 4 h 33 m |
| DSCS-B11 | 08:50 → 13:57 | 5 h 07 m |
| TDRS-6 | 18:08 → 22:08 | 4 h 00 m |

Together they give about 16.5 open hours a day, with gaps of 1.5, 4.2 and 1.8
hours.  Each pass starts about 4 minutes earlier every day.  On 09-25 TDRS-6
made no pass, and one gap grew to 10 hours.  Iridium narrowband stays up
around the clock.

Three lessons for the design:

- **The schedule governs, not the probe.**  The network answers over
  Iridium at any hour, slowly and at high cost.  Under a declared schedule,
  a closed window stays closed even when the probe succeeds.
- **Schedules go stale.**  At 4 minutes a day, a week-old schedule misses its
  edges by half an hour, and NASA changes passes after publishing.  Each
  schedule carries an expiry date.  Past it, the station holds and alarms.  It
  never extrapolates.
- **Latency.**  DSCS and Skynet sit in geostationary orbit, so the round trip
  takes about 600 ms.  SFTP over such a path runs well below the link's rate
  unless the TCP buffers grow to match.

## 5. Open questions

1. **How does science data leave the Pole?**  USAP may let a project's host
   open SFTP to an outside server during a pass, or it may route science data
   through a station transfer service with a per-project allocation.  The
   answer decides whether the station ships to PSWS or hands files to a
   relay.  Someone must ask USAP IT.
2. **Who sends the full schedule each week?**  The station can fetch
   `cel.txt` itself, but that covers TDRS only.
3. **Do the destinations accept late data?**  PSWS takes daily datasets.
   wsprnet and PSKReporter may refuse spots hours late, and wd30 needs
   checking.  A destination that refuses late data shrinks what the cache can
   usefully hold for it.
4. **What drops first when the cache fills?**  §2 lists the candidates.

## 6. Steps

1. **Modes in sigmond.**  `smd upload status | on | hold | off | discard`;
   cursor advance on leaving `discard`; trimming to a working window under
   `discard`; the `smd config uploads` alias.  Confirm the cursor reading in
   §3 before building on it.  Tests: `discard` then `on` ships nothing from
   the gap; `hold` then `on` ships all of it.
2. **Outage handling in hs-uploader** — the (a) case, which every station
   needs.  A per-destination probe; failure kinds (down vs permanent); no
   dead letters for down; backlog age and size in `hs-uploader status`.
   Coordinate with hs-uploader's owner.
3. **Retention that respects the cursor.**  `storage trim` never deletes an
   unacknowledged row unless the disk budget forces it, and then alarms with
   the pipeline and span.  Measure P per pipeline on B4.
4. **Rate and volume caps** — case (b).  Token bucket per destination,
   priority order, throughput measurement.
5. **Windows** — cases (c) and (d).  `smd upload schedule import` for
   `cel.txt`, a plain UTC CSV, or a hand-written file; overlapping passes
   merged; expiry; margins; the window-end guard.
6. **Forecast and visibility.**  `smd upload forecast`; the heartbeat carries
   the mode, the next window and days-to-full; the fleetboard shows "dark
   until 17:24Z, scheduled" and alarms on a missed window or a short
   forecast.
7. **Rehearse before any station depends on it.**  On B4, a synthetic
   schedule of two one-hour windows a day for a week.  On a bench host,
   `tc netem` with 600 ms delay and a few Mbit/s.  Then a 10-hour gap and a
   cache-fill run on a small disk budget, to watch the alarm fire and the
   drop rule act.

## Non-goals

- Predicting passes from orbital elements.  The schedule comes from USAP.
- Shaping the station's other traffic.  The cap restrains the uploader only.
- Changing what producers make when the link runs short.  §2 notes it as a
  lever for the science owners.

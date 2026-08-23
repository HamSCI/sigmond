# Where your data lands, and what its timestamps mean

> **Audience:** scientist
> **Status:** current
> **Verified against:** sigmond 8aee2f1 on 2026-08-23 — walk-through fixes (live DASI002 + code/docs)
> **Canonical for:** where station data lands and what its timing labels mean

Two questions arrive the moment a capture works: *where did it go*, and *what
is the number in the timestamp field actually worth*. This page answers both
for a DASI2 station. It is the page to read before you promise anyone a
result with a time in it.

It does not repeat what it links. The capability envelope — rates, tiers,
storage, loss semantics — is
[station-capabilities.md](station-capabilities.md). How the hardware behaves
when it misbehaves is [character.md](../hardware/character.md). How to *get* a
capture is [capture-quickstart.md](capture-quickstart.md). Terms an operator
would need explained are in the operator
[glossary](../operator/glossary.md).

Every claim ends with `(source: …)`. A `live b4` citation is a dated
read-only reading taken while writing this page.

---

## Part 1 — Where data lands

### Three shapes, and how to tell which is yours

| Shape | Where | Who reads it | Declared as |
|---|---|---|---|
| **File tree** — I/Q, SigMF, HDF5, JSONL, PNG | `/var/lib/<client>/…` | you, and `filetree` uploader pipelines | `disk_writes`, promoted to `data_sinks` ([§17.4](../CLIENT-CONTRACT.md#174-backwards-compatibility-with-disk_writes-v05)) |
| **Rows** — spots, detections, per-cycle measurements | one table in `/var/lib/sigmond/sink.db` | `hs-uploader`, which drains and ships them | `data_sinks` with `kind = "service"` ([§17.3](../CLIENT-CONTRACT.md#173-self-disclosure-data_sinks-in-inventory)) |
| **Nothing durable** | — | — | a Tier-0 capture in your own home directory is a legitimate third answer |

The split is the contract's, not a convention: §17 defines exactly two sink
kinds, `file` and `service`, and unifies them under one `data_sinks` array so
sigmond can budget disk and surface backpressure (source:
[CLIENT-CONTRACT.md §17.1](../CLIENT-CONTRACT.md#17-output-sinks-v06)). **If
your product is files, the sink is not for you** — write to
`/var/lib/<your-client>/` and declare `disk_writes`
([becoming-a-client.md §Writing rows to the sink](becoming-a-client.md#writing-rows-to-the-sink)).

### What is actually on a station — live b4, 2026-08-23

```text
/var/lib/timestd/            hf-timestd: raw IQ archive, products, status
/var/lib/event-recorder/     Tier-0 SigMF captures, one directory per job
/var/lib/sigmond/sink.db     the shared row queue every client writes to
/var/lib/mag-recorder/       magnetometer JSONL, one file per UTC day
/var/lib/psk-recorder/       FT8/FT4 per-radiod state
/var/lib/wspr-recorder/      WSPR per-reporter state and slot files
/var/lib/hs-uploader/        the uploader's own watermarks.db (not your data)
/var/log/gap-hourly.tsv      the honest hourly loss sample
```

(source: `ls -d /var/lib/*/` on b4, 2026-08-23, filtered to the suite's own
directories.)

### The station's own archive — hf-timestd's raw buffer

This is the biggest thing on the disk and the model worth copying. The path
convention is one directory per channel, one per UTC day, and one *pair* of
files per five-minute chunk:

```text
/var/lib/timestd/raw_buffer/<CHANNEL>/<YYYYMMDD>/<epoch_seconds>.bin.zst
/var/lib/timestd/raw_buffer/<CHANNEL>/<YYYYMMDD>/<epoch_seconds>.json
```

Live b4, 2026-08-23: six channels — `SHARED_2500`, `SHARED_5000`,
`SHARED_10000`, `SHARED_15000`, `WWV_20000`, `WWV_25000` — and 406 files in
`WWV_25000/20260823` by 17:00Z, i.e. 203 chunk pairs. The filename is the Unix
epoch second of the chunk boundary and it matches the sidecar's
`minute_boundary` exactly (`1787503800.bin.zst` ↔ `"minute_boundary":
1787503800`). One complete day of one 24 kHz F32 channel is ~15.07 GB (see
[station-capabilities.md §Storage](station-capabilities.md#storage-per-channel-hour)).

Three properties of that layout are the ones to steal:

- **Sample 0 of every file is a wall-clock boundary, not "whenever the first
  packet arrived."** The writer computes the RTP timestamp *of* the chunk
  boundary from the GPS/RTP mapping "so sample position 0 = chunk boundary,
  regardless of when the first packet actually arrived" (source:
  `hf-timestd/src/hf_timestd/core/binary_archive_writer.py:568-584`,
  `_start_new_minute` docstring — the method is named for the older
  one-minute chunk size).
- **Every data file has a sidecar with the same stem.** No metadata lives only
  in a database.
- **The sidecar is self-describing**: raw radiod mapping *and* the correction
  that was applied to it (§"How hf-timestd's sidecars do it", below).

Derived products live beside it, not inside it:
`/var/lib/timestd/products/<CHANNEL>/{decimated,spectrograms}/` — live b4 the
spectrograms are `YYYYMMDD_spectrogram.png` (`20260822_spectrogram.png`,
2.1 MB). The daily GRAPE pipeline writes its own status file:
`/var/lib/timestd/upload/grape_status.json`, live b4 2026-08-23 reading
`"date": "20260822", "status": "completed", "channels_expected": 6,
"channels_decimated": 6, "channels_spectrogram": 6, "upload_status":
"completed"`.

### Event captures — SigMF

A Tier-0 event recording lands as one raw blob plus one JSON sidecar per
segment:

```text
/var/lib/event-recorder/<job>/<job>-<YYYYMMDDTHHMMSSZ>-NN.sigmf-data
/var/lib/event-recorder/<job>/<job>-<YYYYMMDDTHHMMSSZ>-NN.sigmf-meta
```

Live b4, 2026-08-23: `eclipse-costas-14110/` holds **51 files** — 26
`.sigmf-data` and 25 `.sigmf-meta`, 7.68 GB — from the 2026-08-12 eclipse
capture, whose story is
[costas-14110-worked-example.md](costas-14110-worked-example.md#what-was-captured)
(including why one data file has no sidecar, and why that is designed for
rather than an accident). The `NN` suffix is a within-second collision counter,
and the timestamp in the name is the segment's start — **name your segments so
lexicographic order equals chronological order**, because on that archive the
filename is what rescued the orphan.

The `.sigmf-meta` of the **last** segment, live b4 2026-08-23 (read-only
`cat` of `…-20260812T211411Z-00.sigmf-meta`) — the last one because the wire
probe was written mid-capture, so the late sidecars are the complete ones:

```json
{
  "global": {
    "core:datatype": "cf32_le",
    "core:sample_rate": 12000.0,
    "core:version": "1.0.0",
    "core:recorder": "event-recorder/0.1.0",
    "core:description": "eclipse-costas-14110",
    "event:preset": "iq",
    "event:encoding": "f32",
    "event:radiod_encoding": "f32",
    "event:agc": false,
    "event:gain_db": 0.0,
    "event:low_edge_hz": -5000.0,
    "event:high_edge_hz": 5000.0,
    "event:asserted_encoding": 4,
    "event:wire_bytes_per_component": 4.0
  },
  "captures": [
    {
      "core:sample_start": 0,
      "core:frequency": 14110000.0,
      "event:timing_state": "anchored",
      "core:datetime": "2026-08-12T21:14:11.085379+00:00",
      "event:rtp_timestamp": 1398346860
    }
  ],
  "annotations": []
}
```

Five things that file does right:

- `core:datatype` says how to decode the blob without any other document.
- `captures[0]` pins `core:sample_start: 0` to both an absolute
  `core:datetime` **and** the `event:rtp_timestamp` it came from — the UTC and
  the ruler reading that produced it, together.
- `event:timing_state: "anchored"` is an explicit state, so an *unanchored*
  segment is distinguishable from an anchored one instead of silently looking
  identical.
- **Three separate encoding facts, not one.** `event:radiod_encoding` is what
  was *requested* from radiod on the wire; `event:encoding` (with
  `core:datatype`) is the *on-disk* format; `event:asserted_encoding: 4` and
  `event:wire_bytes_per_component: 4.0` are what the recorder's own probe
  *measured*. Those are three independent questions and the sidecar answers
  each separately (source: `event_recorder/jobspec.py`'s module docstring, "Two
  independent encodings live on a `JobSpec`, and they must not be conflated";
  `event_recorder/contract.py`, `asserted_encoding` / `wire_bytes_per_component`
  are "what was actually confirmed on the wire … as opposed to
  `radiod_encoding` … which is always what was REQUESTED").
- Annotations carry *quality*, not just data: this segment has none, but the
  first segment marks its opening 720,000 samples (60 s at 12 kHz) as
  `"core:label": "settling", "event:reason": "analog chain / AGC settling"`
  (live b4 2026-08-23, `…-20260811T231120Z-00.sigmf-meta`).

⚠ **A requested wire format with no measurement beside it is the trap**, and
this archive contains four of them: the four sidecars written before the wire
probe existed record `event:radiod_encoding: "s16"` with no
`asserted_encoding` and no `wire_bytes_per_component` — the worked example's
own lesson 3, "measure the wire from the first segment, not the fifth hour"
([costas-14110-worked-example.md §What we would change](costas-14110-worked-example.md#what-we-would-change)).
Note what it is *not*: `radiod_encoding: "s16"` beside `encoding: "f32"` is a
legitimate, documented configuration — ka9q-python decodes whatever the wire
carried and hands the client `complex64` regardless, so "a capture can
legitimately request `radiod_encoding="s16"` from radiod while still writing
`encoding="f32"` (`cf32_le`) to disk" (source: `event_recorder/jobspec.py`
docstring). The failure is the *absent measurement*, not the mismatch. Record
all three readings — what `ensure_channel` returned, what a fresh poll says,
and what you measured.

⚠ What that meta does **not** carry is the timing tier, the σ or the judge
age that were live while it ran — so the archive cannot be re-analysed against
a better tier later, which is exactly what §18.5 and
[becoming-a-client.md](becoming-a-client.md#timing-authority-say-what-your-timestamps-are-worth)
ask you to preserve ([docs-gap ledger row
48](../contributor/docs-gap-ledger.md)). Add those three fields to your own
captures.

The design reasoning behind choosing SigMF at all is in
[the worked example §SigMF, and the anchor](costas-14110-worked-example.md#sigmf-and-the-anchor).

### Rows — the shared sink

One SQLite file, one table, every client:

```sql
CREATE TABLE pending_uploads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_db       TEXT NOT NULL,
    target_table    TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 0,
    payload_json    TEXT NOT NULL,
    queued_at       TEXT NOT NULL
);
CREATE INDEX idx_pending_uploads_target
    ON pending_uploads (target_db, target_table, id);
CREATE INDEX idx_pending_uploads_cycle_time
    ON pending_uploads (target_db, target_table,
                        json_extract(payload_json, '$.time'));
```

(source: `sqlite3 /var/lib/sigmond/sink.db ".schema pending_uploads"` on b4,
2026-08-23; the same DDL is in
[`lib/sigmond/hamsci_sink/writer.py`](../../lib/sigmond/hamsci_sink/writer.py),
`_QUEUE_DDL` / `_QUEUE_INDEX_DDL`.)

Live b4, 2026-08-23, the queue held:

| `target_db` | `target_table` | `schema_version` | rows |
|---|---|---|---|
| `wspr` | `spots` | 2 | 31,313 |
| `psk` | `spots` | 2 | 16,935 |
| `wspr` | `noise` | 1 | 12,274 |

**Your row is JSON, and the column shape upstream is not your problem.** Every
row is one `payload_json` blob tagged with `(target_db, target_table,
schema_version)`; the uploader owns schema translation
([becoming-a-client.md §Writing rows to the sink](becoming-a-client.md#writing-rows-to-the-sink)).
A live WSPR row, for shape:

```json
{"time": "2026-08-22T17:00:00Z", "band": "22", "mode": "W2",
 "radiod_id": "AC0G-B4-status.local", "reporter_id": "AC0G/B4",
 "frequency_hz": 13555470, "callsign": "1R6ONN", "grid": "EN58",
 "snr_db": -28, "dt": -0.66, "schema_version": 2, "uploaded_at": null}
```

(source: `select payload_json from pending_uploads limit 1` on b4,
2026-08-23.)

Two conventions visible in it that you should copy: an ISO-8601 UTC `time`
field — the second index above is an *expression* index on
`json_extract(payload_json, '$.time')`, so `time` is load-bearing, not
decorative — and a `reporter_id` on every row
([§19.3](../CLIENT-CONTRACT.md#193--reporter_id-row-tag-must-when-client-emits-spotsrows)).

⚠ Copy the *shape*, not that value. `AC0G/B4` is the slash-delimited WSPRnet
form, which [§19.4](../CLIENT-CONTRACT.md#194--wsprnet-upload-boundary-must-for-wsprnet-bound-paths)
says is to be rendered "ONLY at the WSPRnet upload boundary" while
sigmond-internal surfaces "keep the hyphen form (`AC0G-B1`) consistently" —
and the sink is a sigmond-internal surface. Live b4 2026-08-23, every `wspr`
and every `psk` row in the queue carries the slash form
([docs-gap ledger row 49](../contributor/docs-gap-ledger.md)). Use the
path-safe hyphen form your `[instance].reporter_id` gives you
([§19.1](../CLIENT-CONTRACT.md#191--reporter-id-format-must)).

⚠ **A growing `pending_uploads` is not by itself a fault.** The queue is
store-and-forward; some transports never delete. What "shipped" means, and
which transports exist, is
[becoming-a-client.md §Shipping it upstream](becoming-a-client.md#shipping-it-upstream)
— including the flat statement that **there is no `sqlite` → PSWS pairing**,
so rows in the sink cannot reach PSWS at all today.

### Where not to write

`/var/lib/<client>/` is for *installed clients*. A Tier-0 capture belongs in
your own home directory or `/tmp` unless the operator has agreed otherwise in
advance — and the reason is not tidiness: **you are sharing a disk with a
client that deletes.** At 95 % full hf-timestd pauses writes and alerts, and
if the disk is still ≥ 95 % ten minutes later it begins deleting the *oldest
recordings* until the disk is back under 90 %
([station-capabilities.md §Storage](station-capabilities.md#storage-per-channel-hour)).
Ask for headroom before the event, not during it.

---

## Part 2 — What the timestamps mean

### The clock story in five sentences

1. **radiod stamps every sample on an RTP counter that ticks at the
   GPSDO-disciplined sample rate** — that counter is the only thing in the
   station "intrinsically traceable to a frequency standard … set in hardware
   and cannot drift" (source:
   [`hf-timestd/docs/ARCHITECTURE-FIRST-PRINCIPLES.md`](https://github.com/HamSCI/hf-timestd/blob/main/docs/ARCHITECTURE-FIRST-PRINCIPLES.md)
   §1, "RTP is the ruler").
2. **radiod separately publishes `(GPS_TIME, RTP_TIMESNAP)` pairs** in its
   status stream, and those two numbers are what turn a counter reading into a
   UTC label (source:
   [`ka9q-python/docs/RTP_TIMING_SUPPORT.md`](https://github.com/HamSCI/ka9q-python/blob/main/docs/RTP_TIMING_SUPPORT.md)
   §"How radiod Solves This").
3. **`GPS_TIME` is the host system clock** — `clock_gettime(CLOCK_TAI)` offset
   to the GPS epoch, read at the moment the status packet is built — so
   radiod's advertised epoch is only ever as good as the host's discipline
   ([character.md §The anchor pair is not atomic](../hardware/character.md#the-anchor-pair-is-not-atomic)).
4. **On a station running hf-timestd, that host clock is itself disciplined by
   chrony from hf-timestd's own SHM feed**, which closes a loop: hf-timestd's
   six-clocks table says `system_time` "inherits whatever chrony's source
   provides — may include hf-timestd's own SHM output (feedback)", and
   `radiod_clock` "= `system_time`" (source:
   [`hf-timestd/docs/TIMING-PIPELINE-WIRING.md`](https://github.com/HamSCI/hf-timestd/blob/main/docs/TIMING-PIPELINE-WIRING.md)
   §2).
5. **hf-timestd publishes, independently of that loop, the tier its evidence
   supports and the RTP↔UTC offset that corrects radiod's epoch — so record
   which tier labelled your data**, whether or not you apply the correction,
   because the annotation is what has to travel with the sample. (§18.5's
   "Annotation propagation" MUST is scoped to *authority-corrected*
   timestamps — source:
   [CLIENT-CONTRACT.md §18.5](../CLIENT-CONTRACT.md#185-client-obligations).
   In RTP-default mode nothing compels you, but the playbook asks for it
   anyway:
   [record the timing anchor](../EVENT-CLIENT-PLAYBOOK.md#record-the-timing-anchor-not-just-the-samples),
   and so does
   [station-capabilities.md §Timing](station-capabilities.md#timing-you-can-rely-on--tiers).)

Sentence 4 deserves its own paragraph, because it is the thing most likely to
be misunderstood. On b4, live 2026-08-23 17:05Z, `chronyc sources` showed
`#* FUSE` — chrony's *selected* reference was hf-timestd's own fusion SHM feed
(SHM unit 1, `refid FUSE`), with `#? HPPS` (SHM unit 2, the T6 BPSK-PPS feed)
also present but unselected; `chronyc tracking` reported `Reference ID :
46555345 (FUSE)`. The SHM-unit mapping is hf-timestd's (source:
[`hf-timestd/docs/DUAL_CHRONY_FEED_ARCHITECTURE.md`](https://github.com/HamSCI/hf-timestd/blob/main/docs/DUAL_CHRONY_FEED_ARCHITECTURE.md),
current-feeds table: unit 1 `FUSE` = calibrated L2 fusion timing, unit 2
`HPPS` = T6 BPSK-PPS via the matched-filter calibrator; the refclock lines are
live in `/etc/chrony/conf.d/timestd-refclocks.conf` on b4). The station's
chrony chain and why nothing may restart it is
[timing-chain-architecture.md](../timing-chain-architecture.md) — read it as
the design note it says it is (`Status: Design (proposed 2026-06-06)`), not as
a description of what every station runs today.

This is not a bug and hf-timestd does not pretend otherwise — it states
plainly that **chrony is a downstream consumer**, that its selection "reflects
chrony's view of the source's fitness for *disciplining the host clock*, NOT
the underlying quality of the RTP annotation", and that the chrony feed is "a
convenience" (source: `ARCHITECTURE-FIRST-PRINCIPLES.md` §5). The consequence
for you is narrow and practical: **`GPS_TIME` is not an independent GPS
measurement of your samples.** It is a host-clock reading that may descend
from the very software whose accuracy you were hoping to cross-check. Treat
the pair as the *ruler-to-label* mapping it is, and take your accuracy claim
from the tier and σ, never from the fact that the field is called `GPS_TIME`.

### The tiers, in one table

The full table with uncertainties, availability caveats and where the tier is
recorded is
[station-capabilities.md §Timing you can rely on](station-capabilities.md#timing-you-can-rely-on--tiers).
The one-line version, for reference while you read the rest of this page:

| Tier | UTC comes from | Order of magnitude |
|---|---|---|
| **T6** | TS-1 BPSK-PPS recovered from the IQ stream (hard-wired coax) | ns-class after chain-delay calibration |
| **T5** | GPSDO's GPS+PPS over USB | µs to a few ms |
| **T4** | LAN GPS-backed NTP peer | ~100 µs – few ms |
| **T3** | WWV/WWVH/CHU tick fusion | ~0.5 – 2 ms |
| **T2** | public NTP over the WAN | ~1 – 50 ms |
| **T1** | GPSDO rate only, epoch frozen at the last snapshot | holdover, not a steady state |

(source: `hf-timestd/docs/METROLOGY.md` §4.5 Axis T;
`ARCHITECTURE-FIRST-PRINCIPLES.md` §2, which adds **T0** — no GPSDO, wall
clock only.)

⚠ **The tier names the *source* of UTC, not the achieved uncertainty.** Live
b4 2026-08-23, `smd status` reported `judge T6 σ=738.9 µs` *and* six channels
flagged `OFFSET VIOLATION` at +219…+235 ms on the same screen
([character.md §The tier label is not an accuracy claim](../hardware/character.md#the-tier-label-is-not-an-accuracy-claim)).
Record the tier **and** the σ **and** the judge's age, and analyse against the
σ you actually had. And under holdover the epoch is frozen while the rate
survives — a capture that outlives GPS lock must record the tier transition
and be analysed as unanchored
([character.md §Holdover](../hardware/character.md#holdover-rate-survives-epoch-does-not)).

### How to stamp your own capture

**Use `rtp_to_utc()`. Do not difference raw pairs yourself.**

```python
from ka9q import rtp_to_utc          # formerly rtp_to_wallclock (deprecated alias)

utc_sec = rtp_to_utc(header.timestamp, channel)
```

`RTPRecorder` already passes that value into your `on_packet` callback, so
most recorders never call it (source:
[`RTP_TIMING_SUPPORT.md`](https://github.com/HamSCI/ka9q-python/blob/main/docs/RTP_TIMING_SUPPORT.md)
§"Converting RTP Timestamp to Wall Clock"). What it computes is

```text
utc = gps_utc_at_snapshot + (rtp − rtp_at_snapshot) / sample_rate
```

and its own docstring is explicit that "the timing REFERENCE is radiod's
GPS_TIME/RTP_TIMESNAP anchor, not the host clock" (source:
`ka9q-python/ka9q/rtp_recorder.py:128`, `rtp_to_utc`).

**The whole calculation, in the Tier-0 sidecar's own field names.** You need
this the moment you re-open a file offline, where there is no `channel` object
to hand it to `rtp_to_utc()` — so it is written out here rather than left to be
assembled from three places:

```text
utc_unix(N) = gps_time_ns/1e9
            + 315964800                                   # GPS epoch 1980-01-06, in Unix seconds
            − leap                                        # GPS − UTC, currently 18 s
            + (rtp_timestamp + N − rtp_timesnap) / sample_rate
```

`gps_time_ns`, `rtp_timesnap` and `rtp_timestamp` are the `anchor` block of the
[Tier-0 sidecar](capture-quickstart.md#the-sidecar); `sample_rate` is the
**granted** one, not the requested one; `N` is the sample index in the file,
so `N = 0` is the first sample. One RTP tick is one sample — for an `iq`
channel one *complex* sample, which is two components on the wire, which is why
the sidecar records `components_per_rtp_tick` separately. Both constants are in
ka9q-python as module-level values: `GPS_UTC_OFFSET = 315964800` and
`GPS_LEAP_SECONDS = 18` (source: `ka9q-python/ka9q/rtp_recorder.py:31,34`).

Checked against that page's published sidecar — `gps_time_ns
1471533805105752210`, `rtp_timesnap 2147495888`, `rtp_timestamp 2147570768`,
`sample_rate 12000`:

```python
gps_utc = 1471533805105752210 / 1e9 + 315964800 - 18   # 1787498587.1057522
delta   = (2147570768 - 2147495888) / 12000            # 74880 ticks = 6.24 s
sample0 = gps_utc + delta                              # 1787498593.3457522
#                            the sidecar's own anchor.utc_unix_sec: 1787498593.3457522
```

Bit-identical, which is the check worth doing once on your own file: if your
arithmetic does not reproduce the `utc` the library already computed, it is
your arithmetic.

⚠ **The leap term is 18 and you must not treat it as a constant of nature.**
GPS time does not observe leap seconds and UTC does, so the two drift apart by
an integer number of seconds; the offset has been **18 s since 2017-01-01**,
the last leap second inserted. Getting it wrong does not produce noise — it
produces a result that is wrong by *exactly* 1.000000 s, or 18.000000 s if you
omit it altogether, and it will look entirely plausible. So:

- **The authority is IERS Bulletin C**
  (<https://datacenter.iers.org/products/eop/bulletinc/>), issued twice a year;
  it announces a leap second about six months ahead or says "no leap second".
- **`chronyc tracking`'s `Leap status` line** on the station tells you whether
  one is *pending* (`Normal` = nothing scheduled this month; `Insert second` /
  `Delete second` = a change at the end of the current month). It reports the
  warning, not the 18.
- **ka9q-python has no helper.** `GPS_LEAP_SECONDS = 18` is a hardcoded module
  constant carrying the comment "as of 2025"
  (`ka9q-python/ka9q/rtp_recorder.py:34`); nothing in the library resolves the
  value for a given epoch. hf-timestd has one internally
  (`gps_leap_seconds_at_gps_time`, used per buffer — see
  [§How hf-timestd's sidecars do it](#how-hf-timestds-sidecars-do-it)) but it is
  not a Tier-0 surface.

**Record the number you used** in your sidecar, next to the pair. A file that
says which leap value produced its labels can be repaired; one that does not
cannot.

**The one place the host clock still enters**, and it is worth knowing:
`rtp_to_utc` consults `time.time()` for exactly one thing — disambiguating the
32-bit RTP wrap epoch — and it needs only ±half-a-period accuracy for that
(2³² samples is 12.43 h at 96 kHz, 49.7 h at 24 kHz). The docstring says the
host clock "never contributes to the sub-period value", and offers
`wallclock_hint_sec` so a caller with an hf-timestd authority offset can "keep
the labeling path off the chrony-disciplined system clock" (source: same
docstring). If your capture will outlive one wrap period, pass the hint.

**What to write down, per segment.** The playbook's rule is
[record the timing anchor, not just the samples](../EVENT-CLIENT-PLAYBOOK.md#record-the-timing-anchor-not-just-the-samples);
concretely that means five fields:

| Field | Why |
|---|---|
| `rtp_timestamp` of your first sample | the ruler reading — the only quantity that cannot drift |
| `utc` from `rtp_to_utc()` | the label, computed against radiod's GPS reference |
| the raw `(gps_time_ns, rtp_timesnap)` pair | so a future reader can redo the arithmetic, or redo it with a better offset |
| `timing_state`: `anchored` / `unanchored` | so an unanchored segment cannot be mistaken for an anchored one |
| tier, σ, judge age | the accuracy claim; see the tier table above |

Plus a `host_clock_at_receipt` if you want to *see* the divergence — the
Tier-0 recipe records it and it sat 20 ms from the anchor-derived UTC on the
DASI002 run ([capture-quickstart.md §The sidecar](capture-quickstart.md#the-sidecar)).

The last row is the one every Tier-0 sidecar on this fleet misses, including
the recipe's own and `event-recorder`'s
([docs-gap ledger row 48](../contributor/docs-gap-ledger.md)) — because no
scientist-facing surface publishes the tier: `smd status` prints it as text,
hf-timestd publishes it as JSON at `/run/hf-timestd/offset_judge.json`, and
nothing documented for a client says so
([docs-gap ledger row 50](../contributor/docs-gap-ledger.md)). Both routes, and
what they showed on a station in holdover, are in
[capture-quickstart.md §Optional: record what the timestamp is worth](capture-quickstart.md#optional-record-what-the-timestamp-is-worth).

**Anchor once. Do not poll for divergence.** ka9q-python removed exactly that
machinery in 3.19.0 — `ChannelInfo.update_anchor` "now simply adopts the
latest anchor pair" and `SlotClock.divergence_sec` is gone — because a busy
radiod's status pair "jitters ~0.45 s and occasionally tears between ~450 ms
snapshots", which drove recorders into a re-anchor storm
([character.md §The anchor pair is not atomic](../hardware/character.md#the-anchor-pair-is-not-atomic)).
The stated principle is "anchor once off radiod's RTP timestamp and defer to
it"; a genuine radiod restart is handled by the stream's drop/restore path,
not by polling status.

⚠ **The pair `get_anchor()` gives you may be the one captured at channel
discovery, not at your first packet** — unless you run a `StatusListener`
(ka9q-python 3.16.0+), which refreshes the anchor in place at sub-second
cadence via `ChannelInfo.update_anchor()` (source:
`ka9q-python/ka9q/status_listener.py:468`; `CHANGELOG.md` §[3.16.1], "the
`StatusListener` introduced in 3.16.0 refreshes the anchor in place at
sub-second cadence (~450 ms on a busy host)"). Without one, the pair is
frozen at discovery. On the DASI002 Tier-0 run it was taken
6.24 s before the first packet arrived, and every sample after it is
extrapolated from that single reading at the nominal rate
([capture-quickstart.md §What the script does that matters](capture-quickstart.md#what-the-script-does-that-matters)).
Over a minute that is irrelevant; over an hour you are extrapolating an hour
from one block-grid-resolution reading. A recorder that cares should adopt a
fresher pair and **record which pair it used**.

### If you can do better: subscribe to the authority

Everything above is what [§18](../CLIENT-CONTRACT.md#18-timing-authority-and-the-rtp-default-fallback-v07)
calls **RTP-default mode** — you use radiod's published anchor and the nominal
rate, you subscribe to nothing, and you say so in `inventory --json`:

```json
"uses_timing_calibration":     false,
"provides_timing_calibration": false,
"timing_authority_applied":    null
```

`timing_authority_applied: null` *means* RTP-default; a non-null object names
the authority, its tier, its σ and the snapshot's age
([§18.5](../CLIENT-CONTRACT.md#185-client-obligations)). It is the safe
default and it is standalone-safe. **Set it only when you actually apply the
correction** — claiming an authority you do not apply advertises a precision
your data does not have, and nothing downstream can detect the lie
([becoming-a-client.md §Timing authority](becoming-a-client.md#timing-authority-say-what-your-timestamps-are-worth)).

In **authority-corrected mode** you subscribe to hf-timestd, get a periodic
snapshot carrying `utc_anchor_ns`, `tier`, `sigma_ns`, `snapshot_age_s`,
`rtp_anchor_sample`, `rate_samples_per_utc_sec` and `radiod_id`, and compute

```text
utc(rtp_n) = utc_anchor_ns + (rtp_n − rtp_anchor_sample) × 1e9 / rate_samples_per_utc_sec
```

using the *measured* rate rather than the nominal one (source:
[§18.4](../CLIENT-CONTRACT.md#184-what-the-authority-publishes),
[§18.5](../CLIENT-CONTRACT.md#185-client-obligations)). Two obligations come
with it and both are contract MUSTs: if you make hard start/stop decisions you
must gate them on tier, snapshot age and σ, and on a failed gate **refuse or
downgrade loudly — silent degradation is a contract violation**; and you MUST
NOT propagate corrected timestamps downstream without also recording the tier
and σ that produced them (source: same). The §8 chain-delay correction, if
any, composes *after* the §18 conversion and never replaces it
([§18.6](../CLIENT-CONTRACT.md#186-relationship-to-8)).

### How hf-timestd's sidecars do it

The station's own recorder is the reference implementation of everything
above. A live five-minute sidecar, b4 2026-08-23 (read-only `cat` of
`/var/lib/timestd/raw_buffer/WWV_25000/20260823/1787503800.json`, abridged —
`radiod_snr_db: null` and the station block's `description` are elided):

```json
{
  "minute_boundary": 1787503800,
  "channel_name": "WWV_25000",
  "frequency_hz": 25000000,
  "sample_rate": 24000,
  "samples_written": 7200000,
  "samples_expected": 7200000,
  "file_duration_sec": 300,
  "completeness_pct": 100.0,
  "gap_count": 0,
  "gap_samples": 0,
  "start_rtp_timestamp": 2006546195,
  "start_system_time": 1787503800.0,
  "gps_time_ns": 1471504195463675564,
  "rtp_timesnap": 1170805408,
  "dtype": "complex64",
  "byte_order": "little",
  "compression": "zstd",
  "written_at": "2026-08-23T16:55:00.752029+00:00",
  "station": {"callsign": "AC0G", "grid_square": "EM38ww", "id": "S000170",
              "instrument_id": "171", "latitude": 38.9187497, "longitude": -92.1277207},
  "pipeline_offset_samples": 0,
  "timing_snapshots": [],
  "bpsk_chain_delay_ns": 16621380,
  "bpsk_chain_delay_applied": false,
  "timing": {
    "radiod_gps_time_ns": 1471504195463675564,
    "radiod_rtp_timesnap": 1170805408,
    "offset_ns": 3514416.3694462753,
    "offset_sigma_ns": 1131664.252281189,
    "judge_tier": "T6",
    "judge_age_s": 4.684931655006949,
    "segment_id": 3,
    "rate_ppm": -0.00016830773120808738
  }
}
```

**The `timing` block is the whole idea: the raw mapping and the applied
correction, side by side.** "The raw radiod pair in the sidecar stays
uncorrected", and `offset_ns` is the offset-judge verdict the writer *did*
apply to this chunk's labels — boundary placement and `start_system_time` —
"so the correction is re-applied here to reconstruct the corrected UTC
downstream"; the writer's own comment calls the result "fully self-describing
(raw mapping + applied correction)" (source:
`hf-timestd/src/hf_timestd/core/binary_archive_writer.py`, the `judge_timing`
block; `core/buffer_timing.py::resolve_buffer_timing`).

You can check the arithmetic yourself, on your laptop, from the JSON above
(source: `buffer_timing.py::resolve_buffer_timing`; `GPS_EPOCH_UNIX =
315964800` at `buffer_timing.py:38`, leap seconds resolved per buffer by
`gps_leap_seconds_at_gps_time`). It is the same calculation as
[§How to stamp your own capture](#how-to-stamp-your-own-capture) above, in
**this** sidecar's field names — `start_rtp_timestamp` where a Tier-0 sidecar
says `rtp_timestamp`, and a `minute_boundary` to check against instead of a
first sample — plus the offset-judge correction, which a Tier-0 capture does
not have:

```python
GPS_EPOCH_UNIX, leap = 315964800, 18          # 18 s since 2017-01-01; see above
gps_utc = 1471504195463675564 / 1e9 + GPS_EPOCH_UNIX - leap
delta   = (2006546195 - 1170805408) / 24000   # (start_rtp - rtp_timesnap) / rate
sample0 = gps_utc + delta + 3514416.3694462753 / 1e9
# 1787503799.999982  -> 18 µs from the 1787503800 chunk boundary
# without the offset: 1787503799.996467 -> 3.53 ms away
```

Two readings from that: the judge's correction was worth **3.53 ms** on this
chunk (and its own σ was 1.13 ms, so treat the correction as real but not
sharp), and the residual 18 µs is sub-sample-count rounding at the boundary,
not physics.

⚠ **A sidecar with no `timing` block is not a sidecar with no correction
needed.** The resolver treats a missing block as offset 0 — "Legacy sidecars
(no block) get offset 0 — behavior identical to before" (source:
`buffer_timing.py::resolve_buffer_timing`) — and the sidecar carries no
schema-version field to tell the two generations apart
([docs-gap ledger row 47](../contributor/docs-gap-ledger.md)). Put a `schema`
field in your own sidecar; the Tier-0 recipe's says `"tier0-capture/2"`.

Four more fields worth understanding before you copy the pattern:

- **`start_system_time` is a diagnostic, not a timestamp.** The module's own
  docstring: "start_system_time is NEVER used for timing. It is logged for
  diagnostics only. The writer computes it from its own (possibly stale)
  GPS/RTP mapping, which can be wrong by seconds or more after a radiod
  restart" (source: `hf-timestd/src/hf_timestd/core/buffer_timing.py`
  docstring).
- **`rate_ppm` is recorded, never applied.** It is "RECORDED metadata only,
  never resampled, never folded into the labels" (source:
  `binary_archive_writer.py`, the `rate_ppm` comment in `judge_timing`).
- **`bpsk_chain_delay_applied: false`** — the measured chain delay
  (16.6 ms here) is a *reported diagnostic*, not a correction that was
  subtracted. Do not assume otherwise; the two hf-timestd documents on this
  contradict each other
  ([character.md §The only path that is hard-wired](../hardware/character.md#the-only-path-that-is-hard-wired),
  [docs-gap ledger row 36](../contributor/docs-gap-ledger.md)).
- **`gap_count` / `gap_samples` are the honest loss counters**, while
  `samples_written`, `samples_expected` and `completeness_pct` read a perfect
  100 % straight through a dropped block because radiod zero-fills
  ([station-capabilities.md §Loss semantics](station-capabilities.md#loss-semantics--what-a-gap-is);
  `docs/PRODUCER-THREAT-MODEL.md`
  [§Metrics that lie](../PRODUCER-THREAT-MODEL.md#metrics-that-lie)). Note
  this sidecar shows exactly that shape — 100 % complete, `gap_count: 0` — and
  the two agree here; the station samples the honest rate hourly into
  `/var/log/gap-hourly.tsv` (live b4 2026-08-23: `17:05Z 1 5.00 0.20 0`).

---

## Pitfalls

Six ways a well-formed archive turns out to be wrong. None of them raises an
exception.

**1. Stamping with the host clock.** `time.time()` / `datetime.now()` at
packet receipt is *not* your sample's time — it is when your process got
scheduled, on a clock that on b4 is disciplined by hf-timestd's own SHM feed
(§"The clock story", sentence 4). On the DASI002 Tier-0 run the host clock sat
20 ms from the anchor-derived UTC ([capture-quickstart.md §The sidecar](capture-quickstart.md#the-sidecar)),
and hf-timestd's own writer refuses to use its equivalent field for timing at
all (`buffer_timing.py` docstring). Use `rtp_to_utc()`; keep a host-clock field
only as a labelled diagnostic.

**2. Trusting the encoding you asked for.** radiod takes `OUTPUT_ENCODING` as
a *separate* command after the create, "which makes the grant easy to drop and
impossible to notice"
([character.md §The encoding you asked for is a second command](../hardware/character.md#the-encoding-you-asked-for-is-a-second-command)).
A status field saying `f32` is a *report*, and a sidecar field saying `f32` may
only be recording what you *asked for* — the eclipse archive's four earliest
sidecars carry `event:radiod_encoding` with no measurement beside it
(§"Event captures — SigMF", above). **Measure the wire format** (payload bytes
÷ RTP ticks ÷ components: ~4 is F32, ~2 is S16) and record all three readings
— what `ensure_channel` returned, what a fresh poll says, and what you
measured — under names that say which is which.

**3. A *new channel* is a new counter space — not just a radiod restart.**
A cached anchor pair from before a restart is in a *different* RTP counter
space from the packets after it; hf-timestd's own resolver says so and handles it by using "the most
recent snapshot — that's the counter space the buffer's `start_rtp_timestamp`
was computed in" (source: `buffer_timing.py::resolve_buffer_timing`
docstring). Its writer's stale-map
field "can be wrong by seconds or more after a radiod restart" (same file).
Let the stream layer's drop/restore callback tell you the producer went away,
start a new segment with a new anchor, and never silently splice across it.

The same is true, and far more often, of **channel creation**: every dynamic
channel starts its RTP counter near 2³¹ rather than continuing anything, so a
recorder that restarts — which re-creates its channel — begins a fresh counter
space each time, and two runs hours apart can carry *overlapping* RTP
timestamps that mean completely different UTC. Three ephemeral Tier-0 channels
created on DASI002 on 2026-08-23 (15:23Z, 17:37Z and 17:53Z) took anchors of
`rtp_timesnap` 2147495888, 2147495888 and 2147496128 — the same value twice and
the third one block (240 ticks, 20 ms) later, all ≈ 2³¹ + 1.02 s at 12 kHz —
while `gps_time_ns` advanced by the real 9013.16 s between the first and the
last. An RTP timestamp is meaningful **only within one channel incarnation**,
and neither ka9q-radio's nor ka9q-python's documentation says so
([docs-gap ledger row 51](../contributor/docs-gap-ledger.md)). Keep the anchor
pair *per file*, which the Tier-0 sidecar does, and never compare raw RTP
timestamps across two captures.

**4. Re-anchoring to chase status jitter.** The pair jitters ~0.45 s on a busy
radiod and occasionally tears between snapshots; "correcting" your timestamps
against it manufactures that jitter into your data. ka9q-python deleted its own
divergence check for this reason in 3.19.0
([character.md §The anchor pair is not atomic](../hardware/character.md#the-anchor-pair-is-not-atomic)).
Record the first pair; interpret offline.

**5. USB sample loss stepping the anchor, silently.** A USB transfer drop
"steps the published `(GPS_TIME, RTP_TIMESNAP)` offset by the lost duration",
and the step-logging that would tell you is **opt-in and off by default** in
radiod. Downstream, `psk-recorder` anchors once at startup and never
re-anchors, so a transient glitch has silently killed FT8/FT4 on this fleet
while WSPR rode through
([character.md §USB sample loss steps the timing anchor](../hardware/character.md#usb-sample-loss-steps-the-timing-anchor--and-ft8-dies-quietly)).
Log a level/rate heartbeat so you can see it happen.

**6. Quoting a tier as an accuracy.** `T6` on a sidecar does not mean
nanoseconds — live b4 carried `judge T6 σ=738.9 µs` alongside 200 ms offset
violations on the same screen, and this page's own sidecar reports T6 with
`offset_sigma_ns` of 1.13 ms
([character.md §The tier label is not an accuracy claim](../hardware/character.md#the-tier-label-is-not-an-accuracy-claim)).
Carry the σ and the judge age with the tier, always.

⚠ One documentation trap while you read the sources: ka9q-python's
[`RTP_TIMING_SUPPORT.md`](https://github.com/HamSCI/ka9q-python/blob/main/docs/RTP_TIMING_SUPPORT.md)
calls the conversion "a **sample-accurate** Unix wallclock time" and describes
`RTP_TIMESNAP` as the RTP timestamp "at that exact GPS time"; the pair is in
fact block-grid-quantised plus emission lateness
([docs-gap ledger row 37](../contributor/docs-gap-ledger.md)). Likewise
`hf-timestd/docs/METROLOGY.md` §4.3 states that `GPS_TIME` and `RTP_TIMESNAP`
"are both derived from `input_sample_index / decimation` — they are in the
same counter space", which the ka9q-radio source does not support for
`GPS_TIME` ([docs-gap ledger rows 35 and
46](../contributor/docs-gap-ledger.md) — the same sentence is repeated in
`buffer_timing.py`'s module docstring). Both
statements are more optimistic than the mechanism; the mechanism is in
[character.md §The anchor pair is not atomic](../hardware/character.md#the-anchor-pair-is-not-atomic).

---

## Next

- The envelope your channel must fit: [station-capabilities.md](station-capabilities.md)
- How the hardware behaves when it misbehaves: [character.md](../hardware/character.md)
- Getting a capture running: [capture-quickstart.md](capture-quickstart.md)
- A real archive, end to end: [costas-14110-worked-example.md](costas-14110-worked-example.md)
- Turning it into a station product: [becoming-a-client.md](becoming-a-client.md)
- The contract, in full: [CLIENT-CONTRACT.md §17](../CLIENT-CONTRACT.md#17-output-sinks-v06) and [§18](../CLIENT-CONTRACT.md#18-timing-authority-and-the-rtp-default-fallback-v07)

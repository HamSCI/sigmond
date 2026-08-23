# Where your data lands, and what its timestamps mean

> **Audience:** scientist
> **Status:** current
> **Verified against:** sigmond e1ab9d6 on 2026-08-23 — live b4 (raw_buffer sidecar, event-recorder SigMF meta, sink schema) + code/docs
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
  `hf-timestd/src/hf_timestd/core/binary_archive_writer.py`, `_start_new_chunk`
  docstring).
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

The `.sigmf-meta` of the first segment, live b4 2026-08-23 (read-only `cat`):

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
    "event:radiod_encoding": "s16",
    "event:agc": false,
    "event:gain_db": 0.0,
    "event:low_edge_hz": -5000.0,
    "event:high_edge_hz": 5000.0
  },
  "captures": [
    {
      "core:sample_start": 0,
      "core:frequency": 14110000.0,
      "event:timing_state": "anchored",
      "core:datetime": "2026-08-11T23:11:20.136992+00:00",
      "event:rtp_timestamp": 445894560
    }
  ],
  "annotations": [
    {
      "core:sample_start": 0,
      "core:sample_count": 720000,
      "core:label": "settling",
      "event:reason": "analog chain / AGC settling"
    }
  ]
}
```

Four things that file does right, and one it records rather than hides:

- `core:datatype` says how to decode the blob without any other document.
- `captures[0]` pins `core:sample_start: 0` to both an absolute
  `core:datetime` **and** the `event:rtp_timestamp` it came from — the UTC and
  the ruler reading that produced it, together.
- `event:timing_state: "anchored"` is an explicit state, so an *unanchored*
  segment is distinguishable from an anchored one instead of silently looking
  identical.
- The `settling` annotation marks the first 720,000 samples (60 s at 12 kHz)
  as analog-chain/AGC settling — metadata about *quality*, carried with the
  data.
- ⚠ `"event:encoding": "f32"` next to `"event:radiod_encoding": "s16"` is the
  [encoding race](../hardware/character.md#the-encoding-you-asked-for-is-a-second-command)
  caught in the act and **written down** rather than resolved silently. The
  wire carried F32 (the recorder measured it); radiod's status still said S16.
  Recording both is what lets a future reader tell which one to believe.

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
   because the annotation is what has to travel with the sample (source:
   [CLIENT-CONTRACT.md §18.5](../CLIENT-CONTRACT.md#185-client-obligations),
   "Annotation propagation").

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
[timing-chain-architecture.md](../timing-chain-architecture.md).

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

**Anchor once. Do not poll for divergence.** ka9q-python removed exactly that
machinery in 3.19.0 — `ChannelInfo.update_anchor` "now simply adopts the
latest anchor pair" and `SlotClock.divergence_sec` is gone — because a busy
radiod's status pair "jitters ~0.45 s and occasionally tears between ~450 ms
snapshots", which drove recorders into a re-anchor storm
([character.md §The anchor pair is not atomic](../hardware/character.md#the-anchor-pair-is-not-atomic)).
The stated principle is "anchor once off radiod's RTP timestamp and defer to
it"; a genuine radiod restart is handled by the stream's drop/restore path,
not by polling status.

⚠ **The pair `get_anchor()` gives you is the one captured at channel
discovery, not at your first packet.** On the DASI002 Tier-0 run it was taken
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
`/var/lib/timestd/raw_buffer/WWV_25000/20260823/1787503800.json`):

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
`gps_leap_seconds_at_gps_time`):

```python
GPS_EPOCH_UNIX, leap = 315964800, 18          # leap = 18 for this GPS time
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
The eclipse SigMF meta above has the race written into it —
`event:encoding: "f32"` beside `event:radiod_encoding: "s16"`. **Measure the
wire format** (payload bytes ÷ RTP ticks ÷ components: ~4 is F32, ~2 is S16)
and record all three readings — what `ensure_channel` returned, what a fresh
poll says, and what you measured.

**3. A radiod restart moves the counter space.** A cached anchor pair from
before the restart is in a *different* RTP counter space from the packets
after it; hf-timestd's own resolver says so and handles it by using "the most
recent snapshot — that's the counter space the buffer's `start_rtp_timestamp`
was computed in" (source: `buffer_timing.py::resolve_buffer_timing`
docstring). Its writer's stale-map
field "can be wrong by seconds or more after a radiod restart" (same file).
Let the stream layer's drop/restore callback tell you the producer went away,
start a new segment with a new anchor, and never silently splice across it.

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

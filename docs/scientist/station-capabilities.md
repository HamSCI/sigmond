# What this station can give you — the capability envelope

> **Audience:** scientist
> **Status:** current
> **Verified against:** sigmond 55b9e68 on 2026-08-23 — walk-through fixes (live DASI002 + code/docs)
> **Canonical for:** the DASI2 station capability envelope for a new client

Every number below is cited to code, to a doc, or to a dated live reading on
AC0G/B4. Terms an operator would need explained are in the operator
[glossary](../operator/glossary.md); the *design* decisions behind the numbers
live in [EVENT-CLIENT-PLAYBOOK.md](../EVENT-CLIENT-PLAYBOOK.md), which this page
links rather than repeats.

## The one-paragraph version

A DASI2 station is **one** RX888 Mk II digitising 10 kHz – 64 MHz into **one**
`radiod` process, which inverse-FFTs each channel you ask for into its own
multicast RTP stream (source: `ka9q-radio/docs/SDR/rx888.md`;
`sigmond/docs/PRODUCER-THREAT-MODEL.md` §"The asset"). You ask through a single
`RadiodControl.ensure_channel()` call, and you may have: any centre frequency in
the front end's span, any preset the station's `presets.conf` defines, an output
sample rate that is a multiple of 200 Hz, filter edges of your choosing, AGC off
with fixed gain, and your choice of wire encoding. What you may **not** have: a
channel without a `lifetime`; a stream that leaves the host as things are
configured today; a second receiver; or more load than radiod's **20 ms** block
deadline can absorb. Live on b4 on 2026-08-23, radiod was carrying **44
channels** for four clients **as `smd status` counts them** (6 + 19 + 17 + 2;
source: `smd status` on b4). radiod's true count is higher — **at least 46** —
because a client's inventory is not radiod's channel list: hf-timestd declares
6 and also runs two channels that count in neither, the 96 kHz TS-1 BPSK-PPS
channel and the 4 kHz WWVB channel, from the same config. Budget against the
higher number. One
12 kHz complex-float32 channel costs about **96 kB/s** of disk and **~3 % of one
core** (source: `docs/EVENT-CLIENT-PLAYBOOK.md`
[§What "good" cost](../EVENT-CLIENT-PLAYBOOK.md#what-good-cost-for-calibration));
one 24 kHz complex-float32 channel, continuously archived, costs **15.07 GB per
day** on disk as stored (zstd-compressed; **16.59 GB/day** raw — measured on
b4, see [Storage](#storage-per-channel-hour)).

## Frequency and bandwidth — what radiod will hand you

**Span.** The RX-888 streams the whole range **10 kHz to 64 MHz** to the host
(source: `ka9q-radio/docs/SDR/rx888.md` l.5). b4 runs the A/D at its **full
129,600,000 Hz** rather than the 64.8 MHz default (source:
`/etc/radio/radiod@AC0G-B4.conf:38` on b4, 2026-08-23; default per
`ka9q-radio/docs/SDR/rx888.md` §samprate), so Nyquist is 64.8 MHz. The *live*
front-end passband is narrower than the datasheet: radiod reports
`fe filt low 15000 Hz`, `fe filt high 6.0912e+07 Hz` — **15 kHz to 60.9 MHz**
(source: a STAT record from `metadump AC0G-B4-status.local`, obtained by an
SSRC-targeted read-only poll of one 12 kHz `usb` channel — a passive `metadump`
on b4 shows only client keepalive CMDs — front-end fields printed as tags
`[100]`/`[101]`, live b4 2026-08-23). Ask for anything in **that** band, not
the datasheet's: 10 kHz–64 MHz is what the receiver streams to the host, and a
request inside it but outside `fe filt low/high` is one the front end will not
serve. The skeleton client's `validate` deliberately checks the *wider* span
and tells you to tighten it
([skeleton/README.md](skeleton/README.md#run-the-contract-verbs-by-hand)).

**Presets.** The menu is not fixed by the library: `ensure_channel`'s validator
only checks that the preset is a non-empty string ≤ 32 chars (source:
`ka9q-python/ka9q/control.py::_validate_preset`), so the real menu is whatever
`/usr/local/share/ka9q-radio/presets.conf` defines on the station. b4 defines
twenty — `pm npm wpm fm nfm wfm am sam ame iq cwu cwl usb lsb dsb isb amsq wspr
spectrum nam` (source: live b4, 2026-08-23). A misspelled preset therefore
fails at radiod, not at the API. **`iq` gives complex baseband and preserves
absolute phase; everything else discards it** (source:
`docs/EVENT-CLIENT-PLAYBOOK.md`
[§The channel envelope](../EVENT-CLIENT-PLAYBOOK.md#the-channel-envelope)) —
which is why every timing and archive channel on the station is `iq`.

**Sample rate.** radiod supports any output rate that is a common multiple of
the FFT bin spacing and the frame rate. At the default `blocktime = 20` ms
(source: `ka9q-radio/docs/ka9q-radio.md` §blocktime; b4 does not override it —
`/etc/radio/radiod@AC0G-B4.conf`, live 2026-08-23) the frame rate is 50 Hz, and
at the default `overlap = 5` the bin spacing is 40 Hz, so **the rate must be a
multiple of 200 Hz** (source: `ka9q-radio/docs/ka9q-radio-3.md` §samprate). The
conventional values are **12000 for linear modes** (`iq`, SSB, CW, AM), 24000
for FM/PM and 48000 for wideband FM (same source). Rates actually in service on
b4 today: **12000** (radiod's station default, used by WSPR and FT8/FT4 —
`/etc/radio/radiod@AC0G-B4.conf:17`), **24000** (every hf-timestd IQ channel —
`/etc/hf-timestd/timestd-config.toml` `[recorder.channel_defaults]`), **96000**
(the TS-1 BPSK-PPS detection channel — same file, `[timing.t6_pps]`) and
**4000** (the WWVB channel on 60 kHz — same file, `[wwvb]`). All four are
multiples of 200 Hz, as they must be.

**Filter edges.** `low_edge` / `high_edge` are in Hz relative to channel centre.
Be generous for a one-shot capture — a wide recording can be filtered later, a
narrow one cannot be widened (source: `docs/EVENT-CLIENT-PLAYBOOK.md`
[§The channel envelope](../EVENT-CLIENT-PLAYBOOK.md#the-channel-envelope)). One
trap worth knowing before you design: **filter edges are not part of the channel
identity** — they do not participate in SSRC allocation, so a second caller
asking for the same channel with different edges reconfigures *your* filter,
last-writer-wins, the same model as gain and AGC (source:
`ka9q-python/ka9q/control.py::ensure_channel` docstring, `low_edge`).

**Encoding.** The wire formats radiod and ka9q-python agree on are S16LE (1),
S16BE (2), Opus (3), F32LE (4), AX25 (5, packet — not a sample format), F16LE
(6), Opus-VoIP (7), F32BE (8), F16BE (9), µ-law (10) and A-law (11) (source:
`ka9q-python/ka9q/types.py::Encoding`, which must match `ka9q-radio/src/rtp.h`).
F32LE is what the station's own archive
channels use (source: `/etc/hf-timestd/timestd-config.toml`, `encoding = "F32"`;
confirmed on the wire as `[107] encoding 4 (f32le)`, live b4 2026-08-23).

⚠ **The create path does not verify what you asked for.** On the *create* branch
`ensure_channel` accepts the channel on frequency match alone: a sample-rate or
preset divergence is written to the log as a warning and not raised, and
radiod "may grant a different encoding (e.g. F32→S16 for some IQ configs)" — the
returned `ChannelInfo` carries the **granted** value, not the requested one
(source: `ka9q-python/ka9q/control.py::ensure_channel`, the comment above the
final `poll_channel`; the reuse path still verifies strictly). So read the
granted values off the returned `ChannelInfo`, and measure the wire format
independently — bytes ÷ RTP ticks ÷ components — before you trust a byte
(source: `docs/EVENT-CLIENT-PLAYBOOK.md`
[§Station traps](../EVENT-CLIENT-PLAYBOOK.md#station-traps-worth-knowing)). More
on how the hardware behaves under this:
[character.md](../hardware/character.md).

**`lifetime` is mandatory, `destination` is forbidden.** radiod cannot tell that
your process died, so a channel created without a `lifetime` streams to nobody
forever; ~6000 frames ≈ 120 s, refreshed from a keepalive thread. And never pass
`destination=` — construct `RadiodControl(status_address, client_id="<your
client>")` and let the library derive a collision-free multicast group; with no
`client_id` and no `destination` the call raises `ValidationError` rather than
falling back to a shared default (source:
`ka9q-python/ka9q/control.py::ensure_channel`, destination resolution, audit
finding F5). Both disciplines are visible on the live wire: the keepalive CMDs
that a passive `metadump AC0G-B4-status.local` shows on b4 carry
`[117] lifetime 6000 frames` (live b4 2026-08-23T14:03Z).

The full knob table — every parameter with its range and the reason to care — is
the playbook's, and is not duplicated here:
[§The channel envelope](../EVENT-CLIENT-PLAYBOOK.md#the-channel-envelope).

## How many channels you may add — the load budget

**What the station is already carrying.** Live on b4, 2026-08-23 (source:
`smd status`):

| `smd status` line | Client | Channels |
|---|---|---|
| `default: 6 ch, 6 freqs` | hf-timestd (IQ, 24 kHz, F32) | 6 |
| `AC0G-B4-status.local: 19 ch, modes=ft8,ft4` | psk-recorder | 19 |
| `AC0G-B4: 17 ch, modes=F15,F2,F30,F5,W2` | wspr-recorder | 17 |
| `AC0G-B4-status.local: 2 ch, modes=msk144` | meteor-scatter | 2 |
| | **total** | **44** |

(Client attribution per [day-2.md §1](../operator/day-2.md#1-smd-status--is-everything-running)
and [shopping-list.md](../hardware/shopping-list.md) §"What AC0G/B4 actually runs";
hf-timestd's channel characteristics from `/etc/hf-timestd/timestd-config.toml`.)

**The case that settles the budget.** Starting hf-timestd added 9 metrology
channels to this same radiod (~36 → ~45) and shifted `wspr-recorder`'s RTP↔wall
clock anchor by **+2.0 s**, misaligning its 120 s integration windows: **zero
WSPR spots for hours**. FT8/PSK on the same radiod were unaffected, restarting
wspr-recorder did not fix it, and only shedding the load did (source:
`docs/EVENT-CLIENT-PLAYBOOK.md`
[§Budget the load](../EVENT-CLIENT-PLAYBOOK.md#budget-the-load-before-you-choose-your-architecture)).
Read that section before you design; the three durable lessons in it are the
design content, and this page only carries the numbers.

**radiod is cache-bound, not core-bound.** The first direct LLC measurement on
b4 (2026-08-18, via `resctrl`) showed radiod's occupancy collapsing from
~13 MiB to ~5 MiB while the decoders took up to ~11 MiB of the same 16 MiB L3 —
and the part has **one 16 MiB L3 shared by all 16 CPUs**, so moving radiod to a
private core buys it no private cache (source:
`docs/PRODUCER-THREAT-MODEL.md` §"Cache eviction by co-resident work"). More
cores will not save you; do not prescribe affinity widening.

**The deadline you are spending.** radiod's deadline is the filter block time —
**20 ms** on b4. Miss it and radiod emits a block of **zeros** (source:
`docs/PRODUCER-THREAT-MODEL.md` §"The asset"). That is the whole cost model:
your channel is cheap, your *processing* is what misses the deadline.

**How to measure the load you actually add.** Three read-only readings, before
and after:

- `smd status` prints the CPU-affinity block — which cores radiod owns and how
  many are left. Live b4 2026-08-23: `radiod cores: [10, 11, 12, 13] (other
  pool: 10 CPUs)`, with a standing warning that pinned processes overlap
  radiod's cores (the count moves with what is running; read it on the day).
- radiod's own status carries a block-drop counter: `[77] block drops` in
  `metadump <status>.local` (live b4 2026-08-23: `219` cumulative since start).
  Use `metadump` to *read* front-end and channel status; do not use it as a
  channel enumerator (source: `docs/EVENT-CLIENT-PLAYBOOK.md`
  [§Station traps](../EVENT-CLIENT-PLAYBOOK.md#station-traps-worth-knowing)).
- The station samples the honest loss rate hourly into
  `/var/log/gap-hourly.tsv` — `utc  gaps  channel_hours  gaps_per_ch_hr
  grape_running`, derived from the `gap_count` fields of hf-timestd's raw-buffer
  sidecars (source: `sigmond/lib/sigmond/gap_hourly.py`, unit
  `sigmond-gap-hourly.timer`). Live b4 2026-08-23: `12:05Z 0 5.00 0.00 0`,
  `13:05Z 8 5.00 1.60 0`. A run of clean hours before your channel and dirty
  hours after it is the measurement that matters.

**The rule of thumb.** Not a measured limit — the two calibrated points are that
**one** 12 kHz IQ channel recorded to disk costs ~3 % of one core (source:
`docs/EVENT-CLIENT-PLAYBOOK.md`
[§What "good" cost](../EVENT-CLIENT-PLAYBOOK.md#what-good-cost-for-calibration))
and that **nine** hf-timestd metrology channels demonstrably broke a neighbour. So: one or
two extra channels at ≤ 24 kHz, recorded and not processed, is the routine case
and needs no conversation. **Ask the station operator before you add more than
four**, before any channel at ≥ 96 kHz, and before any real-time processing at
all — and in every case record to disk during the event and process afterwards
(source: same §"The practical rule").

## Timing you can rely on — tiers

The station knows UTC through a ranked set of sources; the authority manager
picks the highest one whose health probe passes, and the losers become witnesses
that cross-check it (source: `hf-timestd/docs/METROLOGY.md` §4.5 "Selection,
Cross-Check, and Transition Rules"). Uncertainties below are the (A1, T) column
— A1 meaning the RX888's ADC is disciplined by the GPSDO, which is the DASI2
configuration:

| Tier | Source | Uncertainty (A1) | Needs |
|---|---|---|---|
| **T6** | TS-1 HF-injected BPSK-PPS recovered sample-precise from the IQ stream | **~ns** (after chain-delay calibration) | TS-1 injector + detection lock + calibrated chain delay |
| **T5** | GPS + PPS delivered over USB from the LBE-1421 (USB-NMEA) | **~µs to a few ms** (USB-bus-jitter floored) | GPSDO on USB |
| **T4** | host clock chronyed to a LAN GPS+PPS time server | ~100 µs – few ms | reachable GPS-backed NTP peer |
| **T3** | UTC recovered from WWV/WWVH/CHU tick fusion | ~0.5 – 2 ms | ≥ 2 stations detected + ionospheric model |
| **T2** | host clock chronyed to public NTP over the WAN | ~1 – 50 ms | internet, stratum ≤ 3 |
| **T1** | ADC rate locked, no UTC discipline past the last `RTP_TIMESNAP` | constant offset at snapshot | GPSDO only |

(source: `hf-timestd/docs/METROLOGY.md` §4.5, Axis T table.)

**Availability is not guaranteed.** T3 is gated on a fresh `fusion_status.json`
and "can be (and currently is, in the field) unavailable"; its ±0.5 ms figure
holds only when multi-station fusion is locked and converged (source:
`hf-timestd/docs/METROLOGY.md` §4.5 status table). T6 needs a TS-1, which is an
optional part (source: [`../hardware/shopping-list.md`](../hardware/shopping-list.md)
§Optional). Design for the tier you can prove you had, not the best one.

**Which tier labelled your data, and where to read it.** hf-timestd stamps every
raw-buffer sidecar with the tier that was active: live b4 2026-08-23, a
`WWV_25000` five-minute sidecar carried `"timing": {"judge_tier": "T6",
"offset_sigma_ns": 714381.8, "rate_ppm": 0.179}` alongside
`radiod_gps_time_ns` / `radiod_rtp_timesnap`. Your own recorder should store the
same pairing — the RTP timestamp of the first sample, the UTC it corresponds to
from radiod's GPS reference (**not** the host clock), and an explicit
`anchored`/`unanchored` state (source: `docs/EVENT-CLIENT-PLAYBOOK.md`
[§Record the timing anchor](../EVENT-CLIENT-PLAYBOOK.md#record-the-timing-anchor-not-just-the-samples)).
The conversion itself is one library call, `rtp_to_utc()` (formerly
`rtp_to_wallclock()`), built from radiod's `GPS_TIME` and `RTP_TIMESNAP` status
fields — see
[ka9q-python `RTP_TIMING_SUPPORT.md`](https://github.com/HamSCI/ka9q-python/blob/main/docs/RTP_TIMING_SUPPORT.md).
Where the data lands and how these tiers propagate into products:
[data-and-timing.md](data-and-timing.md).

**Holdover.** When everything above T1 fails but the GPSDO still holds, RTP
timestamps stay rate-accurate and their UTC origin is frozen at whatever
`RTP_TIMESNAP` was at the last good sync — "the coasting on the GPSDO state",
described as a **degraded holdover, not a steady operating point** (source:
`hf-timestd/docs/METROLOGY.md` §4.5, "T1 is a degraded holdover"). No published
figure puts a drift rate on that coast ([docs-gap ledger row
33](../contributor/docs-gap-ledger.md)), so a capture that outlives GPS lock
should record the tier transition and be analysed as unanchored, not assumed
good.

⚠ **The GPSDO's own PPS statistics are a liveness indicator, not a measurement.**
hf-timestd *does* consume the LBE-1421's USB-delivered GPS/PPS as **T5**
(µs-to-ms class); what you must not lean on is `gpsdo-monitor`'s published PPS
edge statistics, which are OS-millisecond bound and labelled "not a metrology
reference" (source: `gpsdo-monitor/README.md` §"What it does *not* do";
[`../hardware/shopping-list.md`](../hardware/shopping-list.md) §Required).

## Storage per channel-hour

**The formula.** An `iq` preset gives you complex samples, so

```
bytes/s = sample_rate × 8      # F32 complex (2 × float32)
bytes/s = sample_rate × 4      # S16 complex (2 × int16), or F32 real (audio presets)
```

| Rate (IQ, F32) | Per second | Per hour | Per day |
|---|---|---|---|
| 12 kHz | 96 kB/s | 345.6 MB | 8.29 GB |
| 24 kHz | 192 kB/s | 691.2 MB | 16.59 GB |
| 96 kHz | 768 kB/s | 2.76 GB | 66.36 GB |

The 12 kHz row is the one with independent confirmation: the eclipse recorder
measured **96 kB/s · 345 MB/h at 12 kHz complex float32** (source:
`docs/EVENT-CLIENT-PLAYBOOK.md`
[§What "good" cost](../EVENT-CLIENT-PLAYBOOK.md#what-good-cost-for-calibration)).

**And the measured tie-point.** hf-timestd's archive channels are `iq`, **24000
Hz**, `encoding = "F32"` (source: `/etc/hf-timestd/timestd-config.toml`
`[recorder.channel_defaults]` and `[recorder.channel_group.timestd]`, live b4
2026-08-23), which the table says is 16.59 GB/day raw. On disk, one complete UTC
day (2026-08-22, all 288 five-minute files) of one such channel came to
**15,073,610,352 bytes ≈ 15.07 GB** (source: `du -sb
/var/lib/timestd/raw_buffer/WWV_25000/20260822` on b4, 2026-08-23, also cited in
[day-2.md §3](../operator/day-2.md#3-disk--df--h-) and
[shopping-list.md](../hardware/shopping-list.md)). The files are zstd-compressed
(`"compression": "zstd"`, `"dtype": "complex64"` in the sidecar, live b4
2026-08-23) — so **zstd buys about 9 % on band-limited HF IQ**. Budget from the
raw formula and treat compression as rounding error.

Two consequences for your capture window:

- **Do the arithmetic before you book the disk.** A 96 kHz channel for a ±4 h
  eclipse window is ~22 GB; the same channel for a week is ~465 GB.
- **You are sharing a disk with a client that deletes.** At 95 % full hf-timestd
  pauses all writes and alerts; if the disk is *still* ≥ 95 % ten minutes later
  it begins deleting the oldest recordings until the disk is back under 90 %
  (source: `hf-timestd/src/hf_timestd/core/resource_guardian.py`, via
  [day-2.md §3](../operator/day-2.md#3-disk--df--h-)). Ask the operator for
  headroom before the event, not during it.

## AGC and gain — science posture

**Your channel: AGC off, fixed gain.** `ensure_channel` defaults to
`agc_enable=0, gain=0.0` (source:
`ka9q-python/ka9q/control.py::ensure_channel` signature), which is the right
posture for science — amplitude stays comparable across the run (source:
`docs/EVENT-CLIENT-PLAYBOOK.md`
[§The channel envelope](../EVENT-CLIENT-PLAYBOOK.md#the-channel-envelope)). The
station's own archive channels do exactly this: `agc = 0`, `gain = 0` (source:
`/etc/hf-timestd/timestd-config.toml`, live b4 2026-08-23), and radiod confirms
it per channel on the wire as `[62] channel agc disable`, `[68] gain 0.0 dB`
(source: `metadump AC0G-B4-status.local`, live b4 2026-08-23).

⚠ **But the RX888's *front end* has its own AGC, and it is ON.** `rx888.md`
says "there is no front end AGC in hardware or software (yet)" (source:
`ka9q-radio/docs/SDR/rx888.md` §gain) — that documentation is stale
([docs-gap ledger row 34](../contributor/docs-gap-ledger.md)). A thread
named `agc_rx888` wakes once a second (`AGC_INTERVAL = 1`, `rx888.c:52`) and,
whenever the measured input level leaves the `[low_threshold, high_threshold]`
band, rewrites the AD8370 VGA gain toward the midpoint (source:
`ka9q-radio/src/rx888.c::agc_rx888`, l.575 and l.660; `rx888_set_gain`). It is
**on by default unless `gain` or an attenuation is specified in the config** (source: `ka9q-radio/src/rx888.c` l.238
and l.298/l.317) — and b4's `[rx888]` section specifies neither (source:
`/etc/radio/radiod@AC0G-B4.conf`, live 2026-08-23). radiod confirms it live:
`[99] rf agc enabled`, `[98] rf gain +16.5 dB`, `[97] rf atten 0.0 dB` (source:
`metadump AC0G-B4-status.local`, live b4 2026-08-23).

**What that means for your data.** Your channel's amplitude is *not* an absolute
measure of antenna-port power over long windows: a strong signal anywhere in the
64 MHz span can move the front-end gain and scale your channel with it, with no
event in your stream. Relative measurements within a block are fine; absolute
ones need the front-end gain recorded alongside. To read it, poll the front-end
status — `rf_gain`, `rf_atten`, `rf_agc` are decoded fields on the status object
(source: `ka9q-python/ka9q/status.py`, `RF_GAIN`/`RF_ATTEN` at
`ka9q/types.py:111-112`) and are printed by `ka9q query <status> --ssrc <n>` as
`RFgain=… RFatten=… RFAGC=…` (source: `ka9q-python/ka9q/cli.py:69`). Log it
periodically; it is exactly the "record what was *granted*, not just what was
requested" rule (source: `docs/EVENT-CLIENT-PLAYBOOK.md`
[§Strongly recommended](../EVENT-CLIENT-PLAYBOOK.md#strongly-recommended--each-of-these-would-have-caught-a-real-problem)).

## Loss semantics — what "a gap" is

**radiod does not drop; it zero-fills.** When radiod misses its 20 ms block
deadline it emits a block of **zeros** (source: `docs/PRODUCER-THREAT-MODEL.md`
§"The asset"). Your recorder receives those zeros as valid samples, writes them,
and every byte-derived health metric reads perfect.

**So byte counts and completeness lie, and only `gap_count` is honest.**
`samples_written` and `completeness_pct` "read **100 %** over dropped blocks —
radiod zero-fills, the recorder faithfully writes the zeros"; the counter to use
instead is `gap_count` in the raw-buffer sidecars (source:
`docs/PRODUCER-THREAT-MODEL.md` §"Metrics that lie"). You can see both in one
live sidecar on b4, 2026-08-23: `"samples_written": 7200000, "samples_expected":
7200000, "completeness_pct": 100.0, "gap_count": 0, "gap_samples": 0`. The
general rule the threat model draws from this — *prefer the counter that can
only move when the bad thing actually happens* — is the one to design to.

**Event count matters more than event duration.** A ~40 ms dropped block
invalidates up to **±25.6 s** of GRAPE spectrogram (NFFT=512 full-window
validity masking). On 2026-08-16 `WWV_25000` recorded **80 gap events**;
per-event loss measured the *following* day averaged **~67 ms**, which puts
that day's actual loss on the order of seconds — and its spectrogram
nonetheless reported 1419/1440 min, 98.5 % complete: **21 minutes invalidated**,
roughly 1000× amplification (source: `docs/PRODUCER-THREAT-MODEL.md` §"The
asset"). If your product is a spectrogram, optimise for *fewer* stalls, not
shorter ones.

**Record your own gap evidence.** Nothing outside your process will do it for
you, and every failure of this class is silent: across the Costas build eleven
defects were found before deployment and **not one threw an exception** — every
one produced plausible, well-formed, wrong output (source:
`docs/EVENT-CLIENT-PLAYBOOK.md`
[§Assume every failure will be silent](../EVENT-CLIENT-PLAYBOOK.md#assume-every-failure-will-be-silent)).
Per segment, store the gap count and gap positions, the timing anchor and its
state, and a periodic signal-level line so a dead antenna is distinguishable
from a live one.

## What the station cannot do

- **No second receiver.** One RX888 per host — the upstream author explicitly
  recommends against two for performance reasons (source:
  `ka9q-radio/docs/SDR/rx888.md`), and b4 is a local-RX888-only station. A new
  observation is a new *channel* on the existing radiod plus a client, never a
  second front end.
- **Nothing leaves the host by default.** b4's radiod publishes with `ttl = 0`
  — "loopback-only (single-host default, safe on every network)"; setting it to
  1 is only safe behind an IGMP-aware switch (source:
  `/etc/radio/radiod@AC0G-B4.conf:21-27`, live 2026-08-23; confirmed on the wire
  as `[19] TTL 0`). **Your client runs on the station**, not on your laptop.
- **No Wi-Fi.** Clients subscribe to radiod over multicast, which Wi-Fi handles
  badly, and the appliance does not configure it at all (source:
  [`../hardware/shopping-list.md`](../hardware/shopping-list.md);
  `sigmond/docs/networking.md`).
- **No inbound ports.** Remote access is an outbound tunnel; nothing is opened
  on the operator's router — no port forwarding, no firewall rules (source:
  [`../operator/remote-access.md`](../operator/remote-access.md)). You cannot
  reach a station from outside without going through that tunnel.
- **Nanosecond timing needs hardware that may be absent.** T6 requires a TS-1
  injector, an optional part; without it the best tier is T5 at µs-to-ms class
  (source: [`../hardware/shopping-list.md`](../hardware/shopping-list.md)
  §Optional; `hf-timestd/docs/METROLOGY.md` §4.5). Check what the station has
  before you promise a result that depends on it.
- **You are one client among several.** Gain, AGC and filter edges are
  last-writer-wins on a shared channel (source:
  `ka9q-python/ka9q/control.py::ensure_channel`), and your load lands on a
  radiod already carrying 44 channels for four other clients as `smd status`
  counts them — at least 46 in radiod itself
  ([the one-paragraph version](#the-one-paragraph-version)). Coordinate.

## Next

- Getting a capture running: [capture-quickstart.md](capture-quickstart.md).
- Where the data lands and how it is timestamped:
  [data-and-timing.md](data-and-timing.md).
- The design decisions behind all of the above:
  [EVENT-CLIENT-PLAYBOOK.md](../EVENT-CLIENT-PLAYBOOK.md).

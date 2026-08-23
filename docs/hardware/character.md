# How the station hardware behaves

> **Audience:** scientist, contributor
> **Status:** current
> **Verified against:** sigmond 55b9e68 on 2026-08-23 — walk-through fixes (live DASI002 + code/docs)
> **Canonical for:** how the station hardware behaves (dynamic range, AGC, loss modes, timing roles, failure modes)

[shopping-list.md](shopping-list.md) says what the parts *are*.
[station-capabilities.md](../scientist/station-capabilities.md) says what the
station will *give you* — spans, rates, channel budget, storage, timing tiers.
This page is the third question: **how these parts behave**, especially when
they misbehave. Every failure mode below is silent — none of them raises an
error, and most of them produce plausible, well-formed, wrong data. Terms an
operator would need explained are in the operator
[glossary](../operator/glossary.md).

Every claim ends with `(source: …)`. A `live b4` or `live dasi002` citation is
a dated read-only reading taken while writing this page.

## The RX888 Mk II

### One 16-bit converter for the whole band

The RX-888 digitises **10 kHz – 64 MHz** directly with an LTC2208 16-bit ADC
and streams it over USB 3 (source: `ka9q-radio/docs/SDR/rx888.md`
§Description). radiod confirms the width on the wire: `[82] A/D bits/sample
16` (source: `metadump AC0G-B4-status.local`, live b4 2026-08-23).

The consequence is the one thing to internalise before you design a
measurement: **every signal in the span shares one converter's range with
yours.** A broadcast transmitter at 9 MHz and your 14.110 MHz weak signal are
the same 16 bits. There is no per-channel front end.

radiod publishes the accounting you need. Live b4, 2026-08-23:

| Field | Reading | What it is |
|---|---|---|
| `[45] IF pwr` | `-19.2 dB` (14:02), `-22.7 dB` (14:31) | wideband A/D level, dBFS |
| `[47] N0` | `-158.5 dB/Hz` | noise density |
| `[104] A/D overrange` | `981,485,440` (14:02) → `981,487,109` (14:28) | cumulative count of clipped **samples** |
| `[108] Samples since A/D overrange` | `78,060,191,744` (14:02), `36,809,998,336` (14:28) | samples since the last buffer that contained one |

(source: `metadump AC0G-B4-status.local`, live b4 2026-08-23; the counter's
semantics — a sample is "overrange" at magnitude > 32766, counted per sample,
with `samp_since_over` reset on any buffer containing one — are
`ka9q-radio/src/rx888.c` l.719-724 and l.807-811.) Read the pair together:
**1,669 more samples clipped** in those 26 minutes, and the second reading's
36.8 × 10⁹ samples-since at 129.6 MHz puts the most recent clipping buffer
about **284 s** before the reading. This station clips routinely.

Log `AD_OVER` alongside your capture. Overrange is a property of the
*converter*, upstream of every per-channel filter, so it is **invisible in any
one channel's samples** — the counter is the only place it appears. The
station's own recorders already sample it (source:
`docs/PRODUCER-THREAT-MODEL.md`
[§Who observes what](../PRODUCER-THREAT-MODEL.md#who-observes-what-and-where-it-lands),
row "RF / front end" → `wspr-recorder/__main__.py::_read_cycle_ad_over`).

### The front-end AGC is real, it is on, and it moves

⚠ `rx888.md` states "There is no front end AGC in hardware or software (yet)"
(source: `ka9q-radio/docs/SDR/rx888.md` §gain). **That sentence is wrong**
about the code this station runs
([docs-gap ledger row 34](../contributor/docs-gap-ledger.md)).

What actually runs: a thread named `agc_rx888` wakes once a second
(`AGC_INTERVAL = 1`, `rx888.c:52`); whenever the measured level leaves the band
`[agc-low-threshold, agc-high-threshold]` — defaulting to
**−26 dBFS … −15 dBFS** (`AGC_LOWER_LIMIT` / `AGC_UPPER_LIMIT`,
`rx888.c:50-51`) — it rewrites the AD8370 VGA gain toward the band's midpoint,
capped at 34 dB (source: `ka9q-radio/src/rx888.c::agc_rx888`, l.660-668). It is
**on by default**: `frontend->rf_agc = true` is set at open (`rx888.c:238`) and
cleared only if the config specifies an attenuation (`rx888.c:298`) or a gain
(`rx888.c:317`).

b4's `[rx888]` section specifies neither — it sets `queuedepth`,
`description`, `samprate = 129600000`, `gainmode = high` and the two
clock-logging keys, and, apart from `device = "rx888"` and a commented-out
`serial`, nothing else (source: `/etc/radio/radiod@AC0G-B4.conf`,
live b4 2026-08-23). `gainmode` is not `gain`; the driver prints "gainmode
parameter is obsolete, now set automatically" and does not disable AGC for it
(source: `ka9q-radio/src/rx888.c` l.303-305). So the AGC is live, and it moves:

| Time (UTC, 2026-08-23) | `[98] rf gain` | `[45] IF pwr` | `[99] rf agc` |
|---|---|---|---|
| 14:02 | **+16.5 dB** | −19.2 dB | enabled |
| 14:28 | **+18.8 dB** | −17.4 dB | enabled |
| 14:31 | **+13.5 dB** | −22.7 dB | enabled |

(source: `metadump AC0G-B4-status.local`, live b4 2026-08-23 — three readings,
no configuration change between them.) That is a **5.3 dB swing in half an
hour**. The AGC's input is `frontend->if_power`, the *wideband* A/D level
(source: `ka9q-radio/src/rx888.c::agc_rx888`, l.647-660), so what moves your
channel's scale is whatever is loudest anywhere in the 64 MHz span — and
*nothing in your channel's stream marks it*.

**What to do about it.** Your channel-level `agc=0, gain=0` (the
`ensure_channel` default — see
[station-capabilities.md §AGC and gain](../scientist/station-capabilities.md#agc-and-gain--science-posture))
does **not** protect you from this: it is a second, independent gain stage
upstream of everything radiod does. Relative measurements inside one block are
fine. For anything absolute, poll and log `rf_gain` / `rf_atten` / `rf_agc`
alongside your samples (source: `ka9q-python/ka9q/status.py`; `ka9q query
<status> --ssrc <n>` prints them as `RFgain=… RFatten=… RFAGC=…`, source:
`ka9q-python/ka9q/cli.py:69`), and reconstruct amplitude afterwards. Asking the
operator to pin the front-end gain is a station-wide change that affects every
other client; discuss it before an event, do not assume it.

### The A/D clock, and why 27 MHz matters

The RX-888's internal sampling clock is synthesised by an Si5351 from a
**27 MHz** reference (`DEFAULT_REFERENCE = 27e6`, source:
`ka9q-radio/src/rx888.c:58`). ka9q-radio's software correction for a wrong
clock (`calibrate`) is described by its own author as experimental, CPU-hungry
and audible as "chugging"; the recommended fix for a precise sampling clock is
to **feed an external 27 MHz reference** to the connector inside the unit
(source: `ka9q-radio/docs/SDR/rx888.md` §calibrate). That is the upstream
reason the GPSDO sits in the *required* column rather than the optional one
(source: [shopping-list.md §Required](shopping-list.md#required)); see
[The GPSDO](#the-gpsdo-lbe-1421) below.

Rate: the default is 64.8 MHz because "several users have had thermal problems"
at the LTC2208's rated 130 MHz; full rate is 129.6 MHz, which synthesises
cleanly from 27 MHz and improves Si5351 phase noise (source:
`ka9q-radio/docs/SDR/rx888.md` §samprate). b4 runs the full 129,600,000 Hz
(source: `/etc/radio/radiod@AC0G-B4.conf:38`, live 2026-08-23) — a deliberate
departure from the conservative default, and the reason that default exists is
thermal, per the sentence above. Worth knowing before you attribute an odd
day's data to propagation.

### Loss is zeros, not gaps

radiod's deadline is the filter block time — **20 ms** on b4. Miss it and
radiod emits a block of **zeros** (source: `docs/PRODUCER-THREAT-MODEL.md`
[§The asset](../PRODUCER-THREAT-MODEL.md#the-asset)). Nothing in the RTP
sequence breaks; your recorder writes the zeros; every byte-derived metric
reads perfect. The counter that cannot lie about it is `gap_count`, and the
reasoning behind that is in
[station-capabilities.md §Loss semantics](../scientist/station-capabilities.md#loss-semantics--what-a-gap-is)
and `docs/PRODUCER-THREAT-MODEL.md`
[§Metrics that lie](../PRODUCER-THREAT-MODEL.md#metrics-that-lie).

The USB side has its own tolerance, and it is configured, not innate. b4 raises
the URB ring from the driver default of 16 buffers to **64**, annotated in its
own config as "64 × ~2 ms = ~130 ms stall tolerance (v3.31 soak; default 16 =
32 ms cliff)" (source: `/etc/radio/radiod@AC0G-B4.conf` `[rx888]`, live
2026-08-23; the default and its bounds are `rx888.c:246-250`). A host stall
longer than that ring is sample loss at the USB layer, before radiod ever sees
the data.

### USB sample loss steps the timing anchor — and FT8 dies quietly

This is the failure mode most likely to ruin a capture you thought succeeded.

The ADC clock is GPSDO-disciplined and radiod's `GPS_TIME` is the host's
GPS-referenced clock, so over any interval the host time should advance by
exactly `received_samples / nominal_rate`. **A shortfall means samples were
lost** — a USB transfer drop or an xhci reset — "which steps the published
(GPS_TIME, RTP_TIMESNAP) offset by the lost duration" (source:
`ka9q-radio/src/rx888.c::agc_rx888`, the RTP↔GPS monitor comment, l.584-594).
The same monitor separates real loss from measurement artifact by pairing the
step with the USB failure count: `failures > 0` prints `SAMPLE LOSS (USB
transfer drop)`, `failures == 0` prints `SAMPLE LOSS (no USB failure flagged —
investigate)` (source: `rx888.c` l.612-613).

Two things a contributor should know about that monitor:

- It is **opt-in and off by default** — `clock-step-logging` (default `false`)
  and `clock-step-threshold` (default 0.05 s), "so stock radiod behaviour is
  unchanged for legacy users" (source: `ka9q-radio/src/rx888.c:273-277`). On a
  station that has not enabled it, the step happens silently. b4 *does* enable
  both `clock-step-logging` and `clock-rate-log` (source:
  `/etc/radio/radiod@AC0G-B4.conf` `[rx888]`, live 2026-08-23); a station you
  did not configure may not.
- Separately, once a minute the same thread checks the measured sample rate
  against nominal and logs when the error exceeds 1 % (source: `rx888.c`
  l.623-645). That line — `RX888 measured sample rate error: …` — is the
  glitch signature downstream tooling watches for.

Downstream, the damage is silent and asymmetric. `psk-recorder` anchors
RTP→wallclock **once at startup and never re-anchors**, so a transient
sample-rate glitch shifts the map and "silently kills FT8/FT4 (decoder keeps
running on a now-invalid time map), while WSPR's continuously-correlating
`RtpSyncStrategy` rides through" (source: `bin/sigmond-timing-watchdog`
docstring, B4 incident 2026-06-02). The station's defence is
`sigmond-timing-watchdog`, on a ~90 s timer, with two detectors — *broken*
(FT8 spots = 0 while peers decode) and *drifted* (spots > 0 but the FT8
dt-centre diverges from the peer median by more than 1.2 s and yield collapses
to under a quarter of it) — and it repairs by restarting the affected instance
to force a re-anchor (source: same docstring; operator view in
[troubleshooting.md](../operator/troubleshooting.md)).

**For your own recorder, the lesson generalises — but not the way it first
looks.** The tempting fix is to keep polling radiod's status pair and re-anchor
whenever it diverges. ka9q-python did exactly that and **removed it in 3.19.0**:
`ChannelInfo.update_anchor` "now simply adopts the latest anchor pair", and
`SlotClock.divergence_sec` is gone, because "a busy radiod's status pair
(`gps_time`/`rtp_timesnap`) jitters ~0.45 s and occasionally tears between
~450 ms snapshots, so the check reported large spurious divergences and drove
downstream recorders into a re-anchor storm" (source:
`ka9q-python/CHANGELOG.md` §[3.19.0], l.235-252). The `anchor_epoch`,
`last_offset_step_sec` and `anchor_step_threshold_sec` fields survive as
**vestigial** defaults in `ka9q/discovery.py:73,83` — do not build on them.

The principle the release states instead: **"anchor once off radiod's RTP
timestamp and defer to it."** A genuine radiod restart is handled by the
stream's drop/restore path (`MultiStream`'s callback), "not by polling the
status feed for divergence", and the sigmond recorders dropped their matching
re-anchor machinery in lockstep (source: same, l.254-258). So: anchor once,
record the anchor and its state per segment, and let the stream layer tell you
when the producer went away.

### The FX3 latch: a reboot is not a power cycle

If the RX888 has wedged — it is not enumerating, or the host's boot ROM caught
it mid-state — **rebooting will not fix it**. "A warm reboot does NOT clear it:
the FX3 stays latched as long as VBUS is maintained. Only removing power — or
physically replugging the RX888 — resets it" (source:
`scripts/proxmox/sigmond-wizard.sh` l.837-840). The appliance installer powers
the machine *off* at the end of setup for exactly this reason, and warns that
"some boards keep USB power even in soft-off" (source: same file, l.847-854).
Note the scope: the wizard is describing the **first-install USB handoff**,
where the host enumerates the FX3 in its boot ROM and then hands the
controllers to the VM mid-state — "that is why this is an install-only problem"
(source: same file, l.822-835). The latch itself is general, which is why the
no-RX888 branch also tells the operator "a wedged FX3 needs a physical replug"
(source: same file, l.867-869).
Operator procedure:
[troubleshooting.md](../operator/troubleshooting.md#rx888-not-found-or-the-waterfall-is-blank).

### One radio, straight into the machine

One RX888 per host — the processing requirements "are not exactly
insubstantial either so I recommend only one per host" (source:
`ka9q-radio/docs/SDR/rx888.md` l.33, the §Configuration preamble). And it goes
straight into a USB-3 port: on b4 the RX888 is alone on a 10 Gbit/s root hub,
negotiated at 5 Gbit/s, while the magnetometer adapter sits **one** hub deep
and the GPSDO **two**, both on 480 Mbit/s buses (source: `lsusb -t` on b4,
2026-08-23; the GPSDO's own `"hid_path": "3-3.4.4:1.2"` agrees). Hubs are fine for the slow devices and fatal for
the radio; the symptom is silent sample loss, not an error (source:
[shopping-list.md §Things that look right but aren't](shopping-list.md#things-that-look-right-but-arent);
`docs/PACKET-LOSS-DIAGNOSTICS.md`).

## The GPSDO (LBE-1421)

### What it provably does, and what is only inferred

The Leo Bodnar LBE-1421 publishes its own health on a timer
(`"probe_interval_sec": 60` on b4, 10 on dasi002). Live b4, 2026-08-23
(source: `/run/gpsdo/0C7BB80D10EF.json`):

```json
"health":  { "pll_locked": true, "gps_fix": "3D", "sats_used": 7,
             "antenna_ok": true, "gps_locked": true },
"outputs": { "out1_hz": 10000000, "out2_hz": 27000000, "pps_enabled": true },
"a_level_hint": "A1",
"a_level_reason": "pll_locked && gps_fix=3D && antenna_ok && pps_present && fresh"
```

**Read `a_level_reason` carefully.** `A1` means "the ADC timebase is
GPSDO-disciplined", and it is the assumption every microsecond-class timing
claim on the station rests on (source: `hf-timestd/docs/METROLOGY.md` §4.3
"Prerequisites for authoritative"). But the reason string shows it is derived
**entirely from the GPSDO's own side of the cable** — lock, fix, antenna,
freshness. Nothing measures the RX888's clock. The same file's `"governs": []`
is empty (live b4, 2026-08-23), and **which output is physically patched into
which jack is recorded nowhere on the station**
([docs-gap ledger row 5](../contributor/docs-gap-ledger.md)). The RX888's
sampling clock is 27 MHz and ka9q-radio's advice is an external 27 MHz
reference (source: `ka9q-radio/docs/SDR/rx888.md` §calibrate), which makes OUT2
the plausible feed — plausible, not recorded. If your result depends on A1,
ask the operator to confirm the cabling rather than reading it off a file.

The failure shape is visible on the fleet's other station. Live dasi002,
2026-08-23 (source: `/run/gpsdo/0C7BB80D5116.json`): the same model, PLL
locked, but `"gps_fix": "no_fix"`, `"sats_used": 0`, `"gps_locked": false`,
`"pps_enabled": false`, both outputs at 27 MHz, and therefore
`"a_level_hint": "A0"`, `"a_level_reason": "gps_fix=no_fix"`. **`pll_locked` is
not the field that tells you the timebase is disciplined; `gps_fix` and
`a_level_hint` are.** A0 is the free-running-TCXO case, under which "the RTP
tick rate drifts at ~±5 ppm" and RTP timestamps stay *sample*-accurate but stop
being UTC-authoritative at the µs scale (source:
`hf-timestd/docs/METROLOGY.md` §4.3, "Prerequisites for authoritative").

### Its PPS over USB is a tier, and its PPS statistics are not a measurement

hf-timestd *does* consume the LBE-1421's USB-delivered GPS+PPS: that is tier
**T5**, "µs-to-ms class … precision floored by USB bus scheduling", used as the
standalone UTC source when T6 is unavailable and as second-of-day context for
T6 even while T6 is active (source:
[`hf-timestd/docs/ARCHITECTURE-FIRST-PRINCIPLES.md`](https://github.com/HamSCI/hf-timestd/blob/main/docs/ARCHITECTURE-FIRST-PRINCIPLES.md)
tier table).

What is **not** a measurement is `gpsdo-monitor`'s own published PPS edge
statistics. Live b4 2026-08-23: `"edges": 61, "period_ms_p50": 1000.0857,
"period_ms_p95": 1000.1497, "note": "OS-millisecond bound; not a metrology
reference"` (source: `/run/gpsdo/0C7BB80D10EF.json`, `pps_study`). The warning
ships with every reading, and the same caveat is in
[shopping-list.md](shopping-list.md#required) and
[station-capabilities.md](../scientist/station-capabilities.md#timing-you-can-rely-on--tiers).
Treat those numbers as **liveness** — is the GPSDO still ticking — and nothing
more.

### Holdover: rate survives, epoch does not

When everything above T1 fails but the GPSDO still holds, RTP timestamps stay
rate-accurate while their UTC origin is frozen at whatever `RTP_TIMESNAP` was
at the last good sync — hf-timestd calls this "a degraded holdover, not a
steady operating point" (source: `hf-timestd/docs/METROLOGY.md` §4.5, T1). ⚠
**the only published figure is an idealised zero** — the tier table gives T1
`const offset at snapshot + 0 drift`, and the prose says "with no drift
(because A1 is perfect rate-wise)" (source: `hf-timestd/docs/METROLOGY.md`
l.262, l.305), which is an idealisation of a timebase that is ppb-stable, not
perfect ([docs-gap ledger row 33](../contributor/docs-gap-ledger.md)). So a capture
that outlives GPS lock must record the tier transition and be analysed as
unanchored. If someone quotes you a µs/hour coast rate, ask which document it
is in — as of 2026-08-23 there isn't one.

## The TS-1 time injector

### The only path that is hard-wired

The TS-1 has **its own GPS receiver** — "the TS-1's onboard GPS supplies the
PPS that gets BPSK-modulated into the RX path", so it does not take PPS from
the LBE-1421 (source: `hf-timestd/docs/ARCHITECTURE-FIRST-PRINCIPLES.md` l.147;
the tier table at l.47 says the same). It
BPSK-modulates that PPS onto a clean GPSDO-disciplined carrier (default
**84.225 MHz**), couples it into the receive path through a
filter/attenuator, and hf-timestd recovers the phase flips **sample-precise
from the IQ stream** (source: same tier table). Because the path is coax rather
than ionosphere, "the only latency is the static analog chain delay (TS-1
modulator → filter/attenuator → RX-888 front-end → ADC)", and once that is
calibrated the recovered edge is ns-class — tier **T6** (source: same,
§"T6 sits above T5").

Operationally: core-recorder creates a dedicated IQ channel for the injected
signal (nothing archived) and runs a BPSK phase-edge detector that locks after
**10 consecutive valid PPS edges** (`consecutive_required = 10`), reporting
`"locked": true` and a `chain_delay_ns` in
`/var/lib/timestd/status/core-recorder-status.json` (source:
[`hf-timestd/docs/STATION_SETUP_GUIDE.md`](https://github.com/HamSCI/hf-timestd/blob/main/docs/STATION_SETUP_GUIDE.md)
§BPSK-PPS). On b4 that channel runs at 96 kHz — the only channel on the station
above 24 kHz (source: `/etc/hf-timestd/timestd-config.toml`, live b4
2026-08-23).

⚠ **Do not assume that `chain_delay_ns` was subtracted from your timestamps.**
Under the T6 anchor inversion, `anchor_utc_ns = named_integer_second +
delay_budget_ns`, where `delay_budget_ns` is "a bounded (±1 ms hard validation
bound), configured constant — not a per-lock fitted quantity", and the measured
`chain_delay` survives "only as a reported diagnostic … never applied as a
correction". The reason is blunt: "every historical `chain_delay` fit
(32–106 ms) violated the microsecond-class analog-path definition above by
three to four orders of magnitude" (source:
`hf-timestd/docs/ARCHITECTURE-FIRST-PRINCIPLES.md` §"Under the T6 anchor
inversion"). The setup guide still describes the older behaviour — "the
measured chain delay is applied to all other channels' RTP-to-UTC mapping
automatically … typically a few milliseconds" — and the two hf-timestd
documents contradict each other on this
([docs-gap ledger row 36](../contributor/docs-gap-ledger.md)). Treat a sidecar
`chain_delay_ns` as a cross-check on the configured budget, not as a correction
you can assume was applied.

### The tier label is not an accuracy claim

⚠ Do not read `T6` off a sidecar and assume nanoseconds. Live b4, 2026-08-23,
`smd status` reported `judge T6  σ=738.9 µs  age 0s  gpsdo=locked` **and**, on
the same screen, six channels flagged `OFFSET VIOLATION — offset +219…+235 ms,
rate −0.095 ppm, T6, seg 2`. Both the tier and the offsets move through the
day: that is a ~14:30Z reading, while
[day-2.md](../operator/day-2.md#1-smd-status--is-everything-running)'s annotated
sample from the same station earlier on 2026-08-23 shows `judge T4 σ=666.9 µs`
with violations at 5–19 ms. Neither page is stale; the number is a reading, not
a property. Those violation lines are known and tracked and
are not an operator fault — the judge is "a detector, not a fault", it compares
each radiod channel's advertised epoch against the station's best clock
evidence, and hf-timestd's own labels stay corrected regardless (source:
[day-2.md §1](../operator/day-2.md#1-smd-status--is-everything-running), the
`OFFSET VIOLATION` row; `hf-timestd/src/hf_timestd/core/offset_judge.py`).

The scientist's takeaway is narrower and important: **the tier names the
*source* of UTC, not the achieved uncertainty.** Record the tier, the σ and the
judge state that were live during your capture — a five-minute hf-timestd
sidecar carries all three (`"timing": {"judge_tier": "T6", "offset_sigma_ns":
714381.8, "rate_ppm": 0.179}`, live b4 2026-08-23) — and analyse against the σ
you actually had.

**Without a TS-1** the best available tier is T5, µs-to-ms class (source:
`hf-timestd/docs/METROLOGY.md` §4.5). WSPR, FT8/FT4 and the magnetometer are
unaffected (source:
[shopping-list.md §Optional](shopping-list.md#optional--and-what-you-lose-without-it)).

## The magnetometer (RM3100)

The chain is: PNI RM3100 + MCP9808 on I²C (`0x23` / `0x1F`) → Pololu isolated
USB-to-I²C adapter → `/dev/ttyMAG0` (CDC-ACM) → Dave Witten's `mag-usb` C
utility emitting **1 Hz JSONL** on stdout → `mag_recorder.core.supervisor`,
which re-stamps ISO-8601 ms and spools one file per day to
`/var/lib/mag-recorder/samples-YYYY-MM-DD.jsonl` (source:
[`mag-recorder/README.md`](https://github.com/HamSCI/mag-recorder/blob/main/README.md)
§"Data flow"). The sensor board is the HamSCI TangerineSDR/Grape magnetometer;
the isolated adapter is what lets it hang off a normal PC instead of a
Raspberry Pi GPIO header (source: `mag-recorder/docs/PROVENANCE.md`).

### The failure mode: a NACKing sensor records a perfect straight line

⚠ **When the RM3100 stops answering on I²C, the recorder keeps emitting one
sample per second carrying the last good reading.** The unit stays `active`,
`smd status` stays ✓, and the plot draws a flat, perfectly constant line that
reads as data. This happened on AC0G/B4 from **2026-08-18 to 2026-08-21**, and
three days of frozen values were packaged and uploaded before anyone looked at
the trace (source:
[troubleshooting.md §Magnetometer flat line](../operator/troubleshooting.md#magnetometer-flat-line-or-mag-recorder-says-failed);
[docs-gap ledger row 18](../contributor/docs-gap-ledger.md)).

Three consequences for anyone using this data:

- **Detect it yourself.** Nothing on the station flags a constant-valued
  sensor. A geomagnetic trace always wiggles; a dead-flat `x`/`y`/`z`, or `rt`
  stuck at `0.0`, is a stuck sensor. The check is `tail` on today's JSONL
  (source: same troubleshooting section, with a live b4 good/bad pair).
- **Do not use `smd watch mag` as the liveness check.** On b4 — sensor
  demonstrably alive, `/dev/ttyMAG0` present, file growing — it reported
  `samples= 0 (0.0/s) — no samples; is /dev/ttyMAG0 present?` for its whole
  window (live b4, 2026-08-23;
  [docs-gap ledger row 19](../contributor/docs-gap-ledger.md)).
- **A replug is not enough.** The reader does not reopen the device, so
  recovery is unplug/replug **and then** restart `mag-recorder` (source:
  [docs-gap ledger row 18](../contributor/docs-gap-ledger.md);
  [troubleshooting.md](../operator/troubleshooting.md#magnetometer-flat-line-or-mag-recorder-says-failed)).

### The PSWS payload is a specific file, not a zip of whatever you have

Worth knowing if you build any PSWS-bound product: PSWS stored every one of the
station's magnetometer zips and ingested **none** of them until the payload
matched what its ingester expects — exactly one file named
`<site>-<YYYYMMDD>-runmag.log` in `mag-usb`/runMag native line form
(`{ "ts":"21 Aug 2026 00:00:01", "rt":23.31, "x":…, "y":…, "z":… }`), rather
than the `samples-<date>.jsonl` plus timing sidecar the station had been
shipping. The zip *name* was never the problem (source: `mag-recorder` commit
`d0a37b9`, 2026-08-22). Silent acceptance-without-ingestion is a real class of
failure on that path; verify on the portal, not from the uploader's exit code.

## The host

The station is a KVM guest on a Proxmox host (source: `sigmond/CLAUDE.md`
§"CPU pinning & the Proxmox host"; [shopping-list.md](shopping-list.md#what-ac0gb4-actually-runs)),
and several of its most important behaviours are invisible from inside the
guest.

**radiod owns specific cores, and more cores will not help it.** Live b4
2026-08-23, `smd status` reports `radiod cores: [10, 11, 12, 13] (other pool: 10
CPUs)` (source: live b4). The constraint is cache, not cores: the first direct
LLC measurement on b4 (2026-08-18, via `resctrl`) showed radiod's occupancy
collapsing from ~13 MiB to ~5 MiB while the decoders took up to ~11 MiB of the
same **16 MiB L3 shared by all 16 CPUs** — so a private core buys radiod no
private cache, and the defence is L3 CAT reserving 13 of 16 ways (source:
`docs/PRODUCER-THREAT-MODEL.md`
[§Cache eviction](../PRODUCER-THREAT-MODEL.md#cache-eviction-by-co-resident-work)).

**Tuning applied without measurement is itself a threat.** Guest-kernel
isolation (`isolcpus` / `nohz_full` / `rcu_nocbs`), applied in good faith to
protect radiod, made it **15–30× worse** in every variant tested — 0.00
gaps/channel-hour with no isolation and CAT, against 10.00–20.82 with isolation
— and was reverted only because the gap counter made it visible (source:
`docs/PRODUCER-THREAT-MODEL.md`
[§Well-intentioned tuning](../PRODUCER-THREAT-MODEL.md#well-intentioned-tuning)).

**Changing the VM's CPU count breaks the layout and costs you samples.** The
host pins vCPU→pCPU 1:1 from a boot hookscript and caps per-core frequency;
change the core count and the computed layout is wrong, and "the symptom is USB
sample loss, which appears as gaps in recordings, not as an error" — with
`-smp 14,sockets=1,cores=7,threads=2` recorded live on b4's host on 2026-08-23
(source: [do-not-touch.md](../operator/do-not-touch.md), the CPU-pinning row;
`sigmond/CLAUDE.md` §"How it's wired").

**USB passthrough is why the console keyboard is dead.** The installer binds
`vfio-pci` to the host's USB controllers *at boot*, before the host kernel
touches them, because on AMD Renoir/Cezanne parts the USB 3.1 controllers are
sibling functions of the integrated GPU and a live detach can reboot the host
(source: `docs/proxmox/wsprdaemon-proxmox-vm-setup.md`). The host keyboard
dying after install is by design, not a fault (source:
[shopping-list.md](shopping-list.md#things-that-look-right-but-arent)).

**Your read-only analysis is inside the threat model.** The single gap event
observed in an otherwise clean window (2026-08-18 00:45Z, all six channels
simultaneously) coincided with an engineer reading ~1700 sidecar JSON files off
`raw_buffer` (source: `docs/PRODUCER-THREAT-MODEL.md`
[§Batch load in general](../PRODUCER-THREAT-MODEL.md#batch-load-in-general)).
Bulk-reading the station's own archive during your event window is not free.
Record during the event; analyse afterwards.

When RTP gaps do show up at a consumer, the six-layer suspect list — kernel UDP
buffer, host NIC, switch port, IGMP, host CPU contention, USB starvation — and
the counters that separate them are in
[PACKET-LOSS-DIAGNOSTICS.md](../PACKET-LOSS-DIAGNOSTICS.md).

## Timing-chain caveats

Three traps that sit between the hardware and your timestamps. All three are
properties of the interface, not bugs you can wait out.

### The anchor pair is not atomic

`rtp_to_utc()` is built from radiod's `GPS_TIME` and `RTP_TIMESNAP` status
fields. **They are not sampled from the same clock, and not at the same
instant.** In `encode_radio_status()`:

```c
  int64_t now = gps_time_ns();
  encode_int64(&bp,GPS_TIME,now);                                   // radio_status.c:718-719
...
    encode_int32(&bp,RTP_TIMESNAP,chan->output.rtp.timestamp);      // radio_status.c:859
```

- `gps_time_ns()` is `clock_gettime(CLOCK_TAI)` offset to the GPS epoch — i.e.
  **the host system clock**, read at the moment the status packet is built
  (source: `ka9q-radio/src/misc.c:546-563`).
- `chan->output.rtp.timestamp` is "the next RTP timestamp to be sent" (source:
  `ka9q-radio/src/monitor-display.c:886`), advanced by the frame count each
  time a block is emitted (source: `ka9q-radio/src/audio.c:49-51`, l.177-179) —
  so it is **quantised to the 20 ms block grid**, and it reflects the last
  block that actually went out, including whatever lateness that emission had.

So a single `(GPS_TIME, RTP_TIMESNAP)` pair carries **block-grid resolution
plus emission lateness**, not sample-precise truth. ka9q-python measured the
consequence from the outside and reached the same place: on a busy radiod that
pair "jitters ~0.45 s and occasionally tears between ~450 ms snapshots"
(source: `ka9q-python/CHANGELOG.md` §[3.19.0], l.238-239) — which is why the
library stopped second-guessing its own grid against the status feed. Use
`rtp_to_utc()` (source: `ka9q-python/ka9q/rtp_recorder.py:128`) rather than
differencing raw pairs yourself, and do not attribute a sub-block offset to
physics.

⚠ ka9q-python's own
[`RTP_TIMING_SUPPORT.md`](https://github.com/HamSCI/ka9q-python/blob/main/docs/RTP_TIMING_SUPPORT.md)
overstates the pair in the other direction — "a **sample-accurate** Unix
wallclock time" (l.5), `RTP_TIMESNAP` as "the RTP timestamp value **at that
exact GPS time**" (l.24) — and still names the deprecated `rtp_to_wallclock()`.
Tracked as [docs-gap ledger row 37](../contributor/docs-gap-ledger.md).

Two corollaries worth stating plainly:

- **`GPS_TIME` is the host clock**, so radiod's advertised epoch is only as
  good as the host's discipline. That is precisely what hf-timestd's offset
  judge exists to contradict when it disagrees — "radiod's advertised epoch is
  contradicted", labels stay corrected (source:
  [day-2.md §1](../operator/day-2.md#1-smd-status--is-everything-running)).
- `hf-timestd/docs/METROLOGY.md` §4.3 states that `GPS_TIME` and
  `RTP_TIMESNAP` "are both derived from `input_sample_index / decimation` —
  they are in the same counter space". The ka9q-radio source above does not
  support that for `GPS_TIME`. Tracked as
  [docs-gap ledger row 35](../contributor/docs-gap-ledger.md).

### The encoding you asked for is a second command

radiod does not take `OUTPUT_ENCODING` in the create packet. ka9q-python sends
the create, then a **separate** command targeting the new SSRC — the
alternative is commented out in the source with the reason ("Radiod requires
OUTPUT_ENCODING to be sent in a separate command after creation", source:
`ka9q-python/ka9q/control.py:1568-1571`; the follow-up send is l.1603-1618).
The library's own note on why this matters: radiod "takes `OUTPUT_ENCODING`
only as a follow-up command …, which makes the grant easy to drop and
impossible to notice; HamSCI/ka9q-python#3" (source:
`ka9q-python/ka9q/control.py:946-948`).

That is the "requested F32, observed S16" shape. **The mitigation** already in
the library: the requested encoding is remembered per SSRC so keepalives
re-assert it, and `verify_channel(expected_encoding=…)` can tell a granted
encoding from a silently lost one (source: same, `_requested_encoding`;
`verify_channel` l.1666-1676).

**The gap, in the same place:** on the *create* path `ensure_channel` accepts
the channel on frequency match alone — "radiod may grant a different encoding
(e.g. F32→S16 for some IQ configs); the returned `ChannelInfo` carries the
granted value, which consumers use authoritatively" (source:
`ka9q-python/ka9q/control.py::ensure_channel`, l.2062-2066). That single
post-create poll can land *before* the library's own follow-up encoding command
is reflected in status, so the value it hands back is not a granted value at
all but a stale one — measured twice on DASI002 (`ensure_channel` → 2, a poll
4 s later → 4, the wire → 4). It is a defect to route around, not a defence
([docs-gap ledger row 39](../contributor/docs-gap-ledger.md)). What to do about
it — read the granted values, re-poll after the settle, and measure the wire
format independently — is already stated once, in
[station-capabilities.md §Encoding](../scientist/station-capabilities.md#frequency-and-bandwidth--what-radiod-will-hand-you);
this page only supplies the mechanism behind it.

### Two stations can share one frequency

If your product depends on identifying *which* time station you heard: on
**2.5, 5, 10 and 15 MHz, WWV and WWVH transmit simultaneously**, and
misidentification costs **3–8 ms of systematic error** (source:
`hf-timestd/docs/METROLOGY.md` §5.3). hf-timestd discriminates with four
independent methods — 100 Hz BCD subcarrier cross-correlation (primary), tone
power ratio (WWV 1000 Hz vs WWVH 1200 Hz), station ID tones, and
cross-frequency guidance — and validates the result by requiring inter-station
`D_clock` consistency under 1 ms and flagging jumps over 5 ms (source: same
§5.3). The implication for a client of your own: on a shared MHz, detecting
a tick is not the same as knowing whose it was, and getting it wrong costs the
3–8 ms above with no error and no warning.

## Next

- The envelope — spans, rates, channel budget, storage, tiers:
  [station-capabilities.md](../scientist/station-capabilities.md).
- What to buy and how it cables together:
  [shopping-list.md](shopping-list.md).
- The design decisions behind the client contract:
  [EVENT-CLIENT-PLAYBOOK.md](../EVENT-CLIENT-PLAYBOOK.md).
- The full producer threat model, for contributors:
  [PRODUCER-THREAT-MODEL.md](../PRODUCER-THREAT-MODEL.md).

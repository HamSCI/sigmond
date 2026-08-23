# Capture first — the Tier-0 recipe

> **Audience:** scientist
> **Status:** current
> **Verified against:** sigmond 04fc9b1 on 2026-08-23 — live DASI002 (Tier-0 capture run) + ka9q-python 3.22.0 + code/docs
> **Canonical for:** the Tier-0 capture recipe

**Tier 0 is capture only.** You create one `radiod` channel, you write its bytes
to a file with a timing anchor beside them, and you go home. No sigmond client
contract, no systemd unit, no shared sink, no uploader — those are Tier 1, and
they are a different page (becoming-a-client.md *(being written)*).

Tier 0 exists because the playbook's first rule is
[capture first, process later, always](../EVENT-CLIENT-PLAYBOOK.md#rule-1--capture-first-process-later-always):
the event happens once, and analysis code written under deadline is the part
most likely to be wrong. Everything below is in service of ending the event
with bytes on disk that you can still interpret in six months.

Two ways to do it:

| | Option A — `event-recorder` | Option B — write it yourself |
|---|---|---|
| Effort | a TOML job file | one Python file, below |
| Scheduling | built in (`start_utc` / `stop_utc`, lead-in, segments) | you supply it |
| Output | SigMF (`.sigmf-data` + `.sigmf-meta`) | raw payload + JSON sidecar |
| Provenance | the tool that recorded the 2026-08-12 eclipse | proven on DASI002, 2026-08-23 (this page) |

Take Option A unless you need something it cannot express. Read Option B
anyway — it is short, and it is the shape every correct Tier-0 recorder has.

---

## Before you start

The playbook's
[pre-flight checklist](../EVENT-CLIENT-PLAYBOOK.md#pre-flight-checklist) is the
list; it is not repeated here. Four things to settle **before** you touch a
station:

1. **Decide the envelope on paper.** Centre frequency, preset, sample rate,
   filter edges, encoding, and how many channel-hours of disk that costs.
   [station-capabilities.md](station-capabilities.md) has the menu radiod will
   actually serve, the
   [200 Hz rate rule](station-capabilities.md#frequency-and-bandwidth--what-radiod-will-hand-you),
   the [storage arithmetic](station-capabilities.md#storage-per-channel-hour)
   and the [load budget](station-capabilities.md#how-many-channels-you-may-add--the-load-budget).
   Be generous with filter edges: a wide recording can be narrowed later, a
   narrow one cannot be widened.
2. **Talk to the station operator about load.** One or two extra channels at
   ≤ 24 kHz, recorded and not processed, is routine. More than four channels,
   anything at ≥ 96 kHz, or any real-time processing needs a conversation —
   nine extra channels once cost a neighbouring client every WSPR spot it
   should have produced for hours
   (source: [§Budget the load](../EVENT-CLIENT-PLAYBOOK.md#budget-the-load-before-you-choose-your-architecture)).
   Also ask for disk headroom for the whole window, with margin: at 95 % full
   the station's timing client starts deleting the oldest recordings
   ([station-capabilities.md §Storage](station-capabilities.md#storage-per-channel-hour)).
3. **Know which station, and pick a testbed.** AC0G/B4 is a production station
   carrying four clients; DASI002 is a plumbing testbed with **no antenna** —
   its samples are noise, which makes it perfect for proving a pipeline and
   useless for proving a signal. Develop on the testbed, deploy on the real
   station.
4. **Your code runs on the station.** radiod publishes with `ttl = 0`,
   loopback-only, so the multicast stream never leaves the host and your laptop
   cannot subscribe to it
   ([station-capabilities.md §What the station cannot do](station-capabilities.md#what-the-station-cannot-do)).
   You need an account on the station VM and somewhere to write. **Not
   `/var/lib`** — that belongs to the station's own clients and its disk
   guardian. Use your home directory, or a directory the operator agrees to.

Finally, read the playbook's
[station traps](../EVENT-CLIENT-PLAYBOOK.md#station-traps-worth-knowing) once.
The first row of that table — a stale encoding from `ensure_channel` — is not
hypothetical: it happened again during the run recorded on this page, and the
script below is written to catch it.

---

## Option A — use `event-recorder`

`event-recorder` is the generic Tier-0 recorder built for the 2026-08-12
eclipse: it records a named frequency, with specified channel characteristics,
over a specified window, into timestamped SigMF files. A new event should be a
**new job file, not a new client**.

The repository is <https://github.com/mijahauan/Costas-array> (package
`event_recorder`, MIT).

On the station VM:

```bash
git clone https://github.com/mijahauan/Costas-array
cd Costas-array
python3 -m venv venv && ./venv/bin/pip install '.[capture]'
```

Write a job file — this is the eclipse job, unchanged:

```toml
name          = "eclipse-costas-14110"
frequency_hz  = 14_110_000
preset        = "iq"
sample_rate   = 12000
encoding      = "f32"
low_edge      = -5000
high_edge     = 5000
lead_in_sec   = 60
segment_sec   = 3600
start_utc     = "2026-08-12T01:14:11Z"
stop_utc      = "2026-08-12T22:00:00Z"
out_dir       = "/var/lib/event-recorder/eclipse-costas-14110"
```

Then, on the station VM:

```bash
./venv/bin/event-recorder run --job /etc/event-recorder/jobs/my-event.toml
```

What it produces: one `.sigmf-data` blob of raw samples per segment plus a
`.sigmf-meta` JSON sidecar carrying **the absolute UTC of sample 0** (source:
the repo's README §Capturing). SigMF is a raw blob plus a JSON sidecar, needs
no library to emit, and is directly shareable — which is why the playbook
recommends it for
[the timing anchor](../EVENT-CLIENT-PLAYBOOK.md#record-the-timing-anchor-not-just-the-samples).

Three properties worth knowing before you adopt it:

- **It creates its radiod channel dynamically, with a lifetime**, and refreshes
  that lifetime from a keepalive thread while it runs; when it exits, radiod
  reclaims the channel by itself (source:
  `src/event_recorder/channel.py`, `LIFETIME_FRAMES = 6000`, `ChannelManager._start_keepalive`).
  You never have to ask an operator to edit `radiod@*.conf`.
- **It measures the wire format instead of believing the status report**
  (source: same file, `probe_wire_bytes_per_component`) — see
  [Option B](#what-the-script-does-that-matters) for why that is not optional.
- **`out_dir` in the example points at `/var/lib`** because that station agreed
  to it in advance. Point yours at your own directory unless you have agreed
  otherwise.

The eclipse run itself — the job, the signal, what came out —
is `costas-14110-worked-example.md` *(being written)*.

---

## Option B — write it yourself

One file, no framework. This is the whole thing; it ran as printed.

Set up a venv on the station VM (the system `python3` on a station has no
`ka9q` module, and you must not install into the station's own venvs):

```bash
python3 -m venv ~/tier0
~/tier0/bin/pip install ka9q-python
~/tier0/bin/pip show ka9q-python | head -2
~/tier0/bin/ka9q --help | head -1
```

The `ka9q` CLI comes with the package and is worth having on `PATH` for
read-only pokes at the station (`ka9q list`, `ka9q query <status> --ssrc <n>`).
It has no `--version` flag; `pip show` is where the version lives. On DASI002,
2026-08-23, those two lines printed `Name: ka9q-python` / `Version: 3.22.0` and
`usage: ka9q [-h] [--interface INTERFACE] {list,query,set,tui} ...`.

⚠ **PyPI is behind the fleet.** On 2026-08-23 the newest release on PyPI was
**3.22.0** while the stations run **3.25.2** from the checkout at
`/opt/git/sigmond/ka9q-python`
([docs-gap ledger row 38](../contributor/docs-gap-ledger.md)). 3.22.0 is fine
for the presets this recipe uses (`iq`, `usb`, `lsb`, `cw`, `am`) but sends the
**FM** demodulator for every other preset, including `wfm` — the channel is
created, verified, and never emits a packet. If you need a preset outside that
list, install the checkout instead:

```bash
~/tier0/bin/pip install -e /opt/git/sigmond/ka9q-python
```

### The script

Save as `~/tier0_capture.py` on the station VM:

```python
#!/usr/bin/env python3
"""tier0_capture.py -- record one radiod channel to a raw file with a UTC anchor.

Tier 0: capture only. No sigmond contract, no systemd unit, no shared sink.
It creates ONE radiod channel with a mandatory lifetime, refreshes that
lifetime while it lives, writes every RTP payload verbatim to <name>.f32, and
writes <name>.json carrying (a) the FIRST packet's timing anchor, (b) every
value radiod actually GRANTED, and (c) an independent measurement of the wire
format. Nothing here trusts what it asked for.

Usage:
  tier0_capture.py --status DASI002-status.local --freq 10000000 \
      --preset iq --rate 12000 --low-edge -5000 --high-edge 5000 \
      --seconds 60 --out ~/capture
"""

import argparse
import json
import math
import pathlib
import signal
import threading
import time
from datetime import datetime, timezone

from ka9q import RadiodControl, Encoding
from ka9q.rtp_recorder import RTPRecorder

try:
    import numpy as np
except ImportError:                      # level heartbeat is optional
    np = None

# radiod counts lifetime in main-loop frames (~50/s at the default 20 ms
# blocktime), so 6000 frames is ~120 s. radiod cannot tell that a Python
# client died; without a lifetime the channel streams to nobody forever.
LIFETIME_FRAMES = 6000
KEEPALIVE_SEC = 30.0
# A tick delta this large means the packet pair was reordered or duplicated,
# not adjacent in time -- not a measurement.
MAX_PLAUSIBLE_TICK_DELTA = 1 << 20


def utc_stamp(unix_sec):
    if unix_sec is None:
        return None
    return datetime.fromtimestamp(unix_sec, timezone.utc).isoformat(timespec="microseconds")


class Capture:
    """Writes payloads, holds the anchor, and measures the wire as it goes."""

    def __init__(self, raw_path, channel, components_per_tick, level_every_sec):
        self.raw_path = raw_path
        self.fh = open(raw_path, "wb")
        self.channel = channel
        self.components_per_tick = components_per_tick
        self.level_every_sec = level_every_sec
        self.enabled = threading.Event()
        self.lock = threading.Lock()
        self.first = None
        self.last_rtp_timestamp = None
        self.packets = 0
        self.bytes_written = 0
        self.wire_bytes = 0
        self.wire_ticks = 0
        self._prev_ts = None
        self._prev_len = None
        self._next_level_at = 0.0

    def bytes_per_component(self):
        if self.wire_ticks <= 0:
            return None
        return self.wire_bytes / self.wire_ticks / self.components_per_tick

    def on_packet(self, header, payload, wallclock):
        if not self.enabled.is_set():
            return
        with self.lock:
            if self.first is None:
                # The anchor pair is read as ONE snapshot (get_anchor), never
                # as two attribute reads -- a background status listener can
                # tear them apart. wallclock is the library's rtp_to_utc() of
                # this packet's timestamp against that pair.
                anchor = self.channel.get_anchor()
                self.first = {
                    "rtp_timestamp": header.timestamp,
                    "rtp_sequence": header.sequence,
                    "utc_unix_sec": wallclock,
                    "utc": utc_stamp(wallclock),
                    "gps_time_ns": anchor[0] if anchor else None,
                    "rtp_timesnap": anchor[1] if anchor else None,
                    "state": "anchored" if (anchor and wallclock is not None) else "unanchored",
                    "host_clock_at_receipt": utc_stamp(time.time()),
                }
            if self._prev_ts is not None:
                delta = (header.timestamp - self._prev_ts) & 0xFFFFFFFF
                if 0 < delta <= MAX_PLAUSIBLE_TICK_DELTA:
                    self.wire_bytes += self._prev_len
                    self.wire_ticks += delta
            self._prev_ts = header.timestamp
            self._prev_len = len(payload)
            self.fh.write(payload)
            self.packets += 1
            self.bytes_written += len(payload)
            self.last_rtp_timestamp = header.timestamp
            bpc = self.bytes_per_component()
            due = time.monotonic() >= self._next_level_at
            if due:
                self._next_level_at = time.monotonic() + self.level_every_sec
        if due:
            lvl = level_dbfs(payload, bpc)
            print("  %6.1fs  packets=%-6d bytes=%-10d bytes/component=%s  level=%s"
                  % (time.monotonic() - START_MONO, self.packets, self.bytes_written,
                     "%.2f" % bpc if bpc else "?",
                     "%.1f dBFS" % lvl if lvl is not None else "?"), flush=True)

    def close(self):
        with self.lock:
            self.fh.flush()
            self.fh.close()


def level_dbfs(payload, bytes_per_component):
    """RMS of one packet, in dBFS -- the dead-antenna heartbeat.

    Decoded with the MEASURED width, not the requested one: 4 bytes/component
    is F32LE, 2 is S16BE (the width radiod's IQ channels actually use here).
    """
    if np is None or bytes_per_component is None or not payload:
        return None
    if abs(bytes_per_component - 4.0) <= 0.5:
        arr = np.frombuffer(payload, dtype="<f4")
    elif abs(bytes_per_component - 2.0) <= 0.5:
        arr = np.frombuffer(payload, dtype=">i2").astype("f8") / 32768.0
    else:
        return None
    if arr.size == 0:
        return None
    rms = float(np.sqrt(np.mean(arr.astype("f8") ** 2)))
    return 20.0 * math.log10(rms) if rms > 0 else float("-inf")


def encoding_from_wire(bytes_per_component):
    if bytes_per_component is None:
        return None
    if abs(bytes_per_component - 4.0) <= 0.5:
        return int(Encoding.F32LE)
    if abs(bytes_per_component - 2.0) <= 0.5:
        return int(Encoding.S16BE)
    return None


START_MONO = time.monotonic()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", required=True, help="radiod status/control mDNS name")
    ap.add_argument("--freq", type=float, required=True, help="centre frequency in Hz")
    ap.add_argument("--preset", default="iq")
    ap.add_argument("--rate", type=int, default=12000, help="output sample rate (multiple of 200)")
    ap.add_argument("--low-edge", type=float, default=None, help="filter low edge, Hz from centre")
    ap.add_argument("--high-edge", type=float, default=None, help="filter high edge, Hz from centre")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--out", default="~/capture")
    ap.add_argument("--client-id", default="tier0-capture")
    ap.add_argument("--settle", type=float, default=4.0,
                    help="seconds to let radiod's separate encoding command land")
    ap.add_argument("--level-every", type=float, default=10.0)
    args = ap.parse_args()

    out = pathlib.Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stem = "%s_%d_%s" % (args.preset, int(args.freq),
                         datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    raw_path = out / (stem + ".f32")
    meta_path = out / (stem + ".json")

    requested = {
        "status_address": args.status,
        "client_id": args.client_id,
        "frequency_hz": args.freq,
        "preset": args.preset,
        "sample_rate": args.rate,
        "low_edge": args.low_edge,
        "high_edge": args.high_edge,
        "encoding": int(Encoding.F32LE),
        "encoding_name": "F32LE",
        "agc_enable": 0,
        "gain_db": 0.0,
        "lifetime_frames": LIFETIME_FRAMES,
    }

    # client_id (never destination=) -- ka9q-python derives a collision-free
    # multicast group from (client_id, status_address).
    control = RadiodControl(args.status, client_id=args.client_id)
    kwargs = dict(frequency_hz=args.freq, preset=args.preset, sample_rate=args.rate,
                  agc_enable=0, gain=0.0, encoding=Encoding.F32LE,
                  lifetime=LIFETIME_FRAMES, timeout=20.0)
    if args.low_edge is not None:
        kwargs["low_edge"] = args.low_edge
    if args.high_edge is not None:
        kwargs["high_edge"] = args.high_edge
    channel = control.ensure_channel(**kwargs)

    granted = {
        "ssrc": channel.ssrc,
        "frequency_hz": channel.frequency,
        "preset": channel.preset,
        "sample_rate": channel.sample_rate,
        "encoding": channel.encoding,
        "multicast_address": channel.multicast_address,
        "port": channel.port,
    }
    print("channel granted: ssrc=%d %.6f MHz preset=%s rate=%d encoding=%s dest=%s:%d"
          % (channel.ssrc, channel.frequency / 1e6, channel.preset, channel.sample_rate,
             channel.encoding, channel.multicast_address, channel.port), flush=True)
    for key in ("frequency_hz", "preset", "sample_rate"):
        if requested[key] != granted[key]:
            print("WARNING: requested %s=%r but radiod granted %r -- the sidecar records "
                  "the granted value" % (key, requested[key], granted[key]), flush=True)

    stop_keepalive = threading.Event()

    def keepalive():
        # Bare polls do NOT extend the timer; only a command carrying a
        # LIFETIME tag does.
        while not stop_keepalive.wait(KEEPALIVE_SEC):
            try:
                control.set_channel_lifetime(channel.ssrc, LIFETIME_FRAMES)
            except Exception as exc:                       # noqa: BLE001
                print("WARNING: keepalive refresh failed: %s" % exc, flush=True)

    ka_thread = threading.Thread(target=keepalive, name="keepalive", daemon=True)
    ka_thread.start()

    components = 2 if args.preset == "iq" else 1
    cap = Capture(raw_path, channel, components, args.level_every)
    recorder = RTPRecorder(channel=channel, on_packet=cap.on_packet, pass_all_packets=True)

    stop_now = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_now.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_now.set())

    recorder.start()
    print("settling %.1fs (radiod takes OUTPUT_ENCODING as a SEPARATE command)"
          % args.settle, flush=True)
    time.sleep(args.settle)
    recorder.start_recording()
    cap.enabled.set()
    started = time.time()
    print("recording %.0fs to %s" % (args.seconds, raw_path), flush=True)

    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline and not stop_now.is_set():
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))

    cap.enabled.clear()
    stopped = time.time()
    recorder.stop_recording()
    recorder.stop()
    stop_keepalive.set()
    ka_thread.join(timeout=2.0)
    cap.close()

    bpc = cap.bytes_per_component()
    measured_encoding = encoding_from_wire(bpc)
    metrics = recorder.get_metrics()
    expected_samples = int(round(args.rate * (stopped - started)))
    written_samples = (cap.bytes_written / (components * bpc)) if bpc else None

    meta = {
        "tool": "tier0_capture.py",
        "schema": "tier0-capture/1",
        "ka9q_python_version": _lib_version(),
        "raw_file": raw_path.name,
        "requested": requested,
        "granted": granted,
        "anchor": cap.first,
        "wire": {
            "bytes_per_component_measured": bpc,
            "encoding_measured": measured_encoding,
            "encoding_reported_by_radiod": channel.encoding,
            "measurement_agrees_with_report": (
                None if measured_encoding is None else measured_encoding == channel.encoding),
            "components_per_rtp_tick": components,
            "note": "Trust the measurement, not the report. The file is payload bytes verbatim.",
        },
        "run": {
            "recording_started_host_utc": utc_stamp(started),
            "recording_stopped_host_utc": utc_stamp(stopped),
            "requested_seconds": args.seconds,
            "settle_sec": args.settle,
            "packets": cap.packets,
            "bytes_written": cap.bytes_written,
            "last_rtp_timestamp": cap.last_rtp_timestamp,
            "samples_written_estimate": written_samples,
            "samples_expected_from_host_clock": expected_samples,
        },
        "loss": {
            "packets_received": metrics["packets_received"],
            "packets_dropped": metrics["packets_dropped"],
            "sequence_errors": metrics["sequence_errors"],
            "timestamp_jumps": metrics["timestamp_jumps"],
            "note": "radiod zero-fills a missed block, so byte counts cannot show loss; "
                    "these RTP-level counters and the sequence gaps are the honest evidence.",
        },
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=False) + "\n")

    print("wrote %s (%d bytes, %d packets)" % (raw_path, cap.bytes_written, cap.packets), flush=True)
    print("wrote %s" % meta_path, flush=True)
    if bpc is None:
        print("ERROR: could not measure the wire format -- do not trust this file's layout",
              flush=True)
    elif measured_encoding != channel.encoding:
        print("WARNING: measured %.2f bytes/component (encoding %s) but radiod REPORTED "
              "encoding %s -- decode using the measurement" % (bpc, measured_encoding,
                                                               channel.encoding), flush=True)
    print("channel released; radiod auto-destructs it within ~%ds" % (LIFETIME_FRAMES // 50),
          flush=True)
    return 0


def _lib_version():
    try:
        from importlib.metadata import version
        return version("ka9q-python")
    except Exception:                                     # noqa: BLE001
        return None


if __name__ == "__main__":
    raise SystemExit(main())
```

### What the script does that matters

Seven decisions, each of which is there because the alternative fails quietly.

- **`client_id`, never `destination=`.** `RadiodControl(status, client_id=...)`
  makes ka9q-python derive a per-(client, radiod) multicast group, so you
  cannot land on a peer client's group. Passing neither raises `ValidationError`
  rather than falling back to a shared default (source:
  `ka9q-python/ka9q/control.py::RadiodControl.__init__`, audit finding F5).
- **`lifetime=6000`, plus a keepalive.** radiod cannot learn that a Python
  process died. 6000 frames ≈ 120 s at the default 20 ms blocktime; a *bare
  poll does not refresh it* — only a command carrying a `LIFETIME` tag does
  (source: `ka9q-python/ka9q/control.py::set_channel_lifetime`, audit finding
  F7), which is what the keepalive thread sends every 30 s. When the script
  exits, the channel expires on its own.
- **The sidecar records what was *granted*, not what was asked.** On the create
  path `ensure_channel` accepts the channel on frequency match alone; a
  sample-rate or preset divergence is logged, not raised
  ([station-capabilities.md §Encoding](station-capabilities.md#frequency-and-bandwidth--what-radiod-will-hand-you)).
  So the script prints a warning on divergence and writes both `requested` and
  `granted` blocks.
- **The wire format is measured, never believed.** radiod takes
  `OUTPUT_ENCODING` as a *separate* command after the create, which makes the
  grant easy to lose and impossible to notice
  ([character.md §The encoding you asked for is a second command](../hardware/character.md#the-encoding-you-asked-for-is-a-second-command)) —
  and, in the other direction, radiod's status **misreports** the encoding of
  IQ channels. The script accumulates payload bytes ÷ RTP ticks ÷ components as
  it records, which is a direct measurement of bytes-per-component: ~4 is F32,
  ~2 is S16. It reported a disagreement on the live run below.
  `--settle 4.0` exists so the follow-up encoding command has landed before the
  measurement starts.
- **The anchor is read as one snapshot.** `channel.get_anchor()` returns
  `(gps_time, rtp_timesnap)` as a single tuple; reading the two attributes
  separately can tear across a background status update. The `wallclock`
  argument the callback receives is already `rtp_to_utc()` of that packet's RTP
  timestamp against that pair, referenced to radiod's GPS epoch — not to the
  host clock. The script stores the RTP timestamp, the UTC, the raw pair, and an
  explicit `anchored`/`unanchored` state, exactly as the playbook's
  [§Record the timing anchor](../EVENT-CLIENT-PLAYBOOK.md#record-the-timing-anchor-not-just-the-samples)
  asks.
- **It anchors once, at the first packet, and does not poll for divergence.**
  A single `(GPS_TIME, RTP_TIMESNAP)` pair carries block-grid resolution plus
  emission lateness, and on a busy radiod it jitters ~0.45 s between snapshots
  ([character.md §The anchor pair is not atomic](../hardware/character.md#the-anchor-pair-is-not-atomic)).
  Re-reading it during the run and "correcting" your timestamps against it
  manufactures the jitter into your data. Record the first pair; interpret
  offline.
- **A level heartbeat, so a dead antenna is distinguishable from a live one.**
  Every 10 s the script prints packets, bytes, measured bytes-per-component and
  the RMS of the current packet in dBFS. Nothing outside your process will tell
  you the antenna fell off; across the Costas build eleven defects were found
  before deployment and
  [not one threw an exception](../EVENT-CLIENT-PLAYBOOK.md#assume-every-failure-will-be-silent).

### Running it — a real run on DASI002

This is the run that proves the recipe: 60 s on 10.000 MHz (WWV's frequency) on
the DASI002 testbed, 2026-08-23. DASI002 has **no antenna**, so what is being
proven here is the *pipeline* — channel created, packets received, file and
sidecar written with the anchor and the granted values — not a signal.

On the station VM, as the `sigmond` user:

```bash
~/tier0/bin/python ~/tier0_capture.py \
    --status DASI002-status.local --freq 10000000 \
    --preset iq --rate 12000 --low-edge -5000 --high-edge 5000 \
    --seconds 60 --out ~/capture
```

Real output, trimmed only of eight further `TTL=0` notices — one per other
channel radiod was already carrying (see below):

```text
Radiod reporting TTL=0 for SSRC 1038463489: Multicast data restricted to localhost loopback only!
channel granted: ssrc=1038463489 10.000000 MHz preset=iq rate=12000 encoding=2 dest=239.183.22.68:5004
settling 4.0s (radiod takes OUTPUT_ENCODING as a SEPARATE command)
recording 60s to /opt/git/sigmond/capture/iq_10000000_20260823T150049Z.f32
     5.1s  packets=1      bytes=1440       bytes/component=?  level=?
    15.1s  packets=1001   bytes=961440     bytes/component=4.00  level=-127.4 dBFS
    25.1s  packets=2003   bytes=1923360    bytes/component=4.00  level=-126.9 dBFS
    35.1s  packets=3003   bytes=2883360    bytes/component=4.00  level=-126.2 dBFS
    45.1s  packets=4003   bytes=3843360    bytes/component=4.00  level=-126.3 dBFS
    55.1s  packets=5003   bytes=4803360    bytes/component=4.00  level=-126.7 dBFS
wrote /opt/git/sigmond/capture/iq_10000000_20260823T150049Z.f32 (5760000 bytes, 6000 packets)
wrote /opt/git/sigmond/capture/iq_10000000_20260823T150049Z.json
WARNING: measured 4.00 bytes/component (encoding 4) but radiod REPORTED encoding 2 -- decode using the measurement
channel released; radiod auto-destructs it within ~120s
```

Four things to read out of that:

- **`encoding=2` in the grant line, `4.00 bytes/component` on the wire.**
  radiod *reported* S16BE (2 bytes) and *sent* F32LE (4 bytes) — the exact
  misreport `event-recorder` found on live hardware, reproduced here with a
  fresh client on a different station
  ([docs-gap ledger row 39](../contributor/docs-gap-ledger.md)). Had the script
  believed the report, it would have decoded 2 bytes where 4 were sent and
  produced a plausible, well-formed, wrong array — at twice the sample count,
  with clean completeness. **This is why you measure.**
- **The arithmetic closes.** 5,760,000 bytes ÷ 8 bytes per complex F32 sample =
  720,000 samples = exactly 60.0 s at 12 kHz. 6000 packets in 60 s is 100
  packets/s, i.e. 960 payload bytes = 120 complex samples = 10 ms per packet.
- **`level ≈ −127 dBFS`** is the noise floor of a receiver with nothing plugged
  into it. On a station with an antenna this line is where you would see WWV.
- **`TTL=0`** is not an error: it is radiod telling you the stream is
  loopback-only, which is the station's normal configuration
  ([station-capabilities.md](station-capabilities.md#what-the-station-cannot-do)).
  ka9q-python prints one notice per channel it sees, and DASI002's radiod was
  carrying nine, ours among them.

The files, and the whole sidecar:

```bash
ls -la ~/capture
```

```text
total 5640
drwxrwsr-x  2 sigmond sigmond    4096 Aug 23 15:01 .
drwxrwsr-x 29 sigmond sigmond    4096 Aug 23 15:00 ..
-rw-rw-r--  1 sigmond sigmond 5760000 Aug 23 15:01 iq_10000000_20260823T150049Z.f32
-rw-rw-r--  1 sigmond sigmond    1981 Aug 23 15:01 iq_10000000_20260823T150049Z.json
```

(`~` for the `sigmond` user on a station is `/opt/git/sigmond`, which is why
the absolute paths above look the way they do.)

### The sidecar

```json
{
  "tool": "tier0_capture.py",
  "schema": "tier0-capture/1",
  "ka9q_python_version": "3.22.0",
  "raw_file": "iq_10000000_20260823T150049Z.f32",
  "requested": {
    "status_address": "DASI002-status.local",
    "client_id": "tier0-capture",
    "frequency_hz": 10000000.0,
    "preset": "iq",
    "sample_rate": 12000,
    "low_edge": -5000.0,
    "high_edge": 5000.0,
    "encoding": 4,
    "encoding_name": "F32LE",
    "agc_enable": 0,
    "gain_db": 0.0,
    "lifetime_frames": 6000
  },
  "granted": {
    "ssrc": 1038463489,
    "frequency_hz": 10000000.0,
    "preset": "iq",
    "sample_rate": 12000,
    "encoding": 2,
    "multicast_address": "239.183.22.68",
    "port": 5004
  },
  "anchor": {
    "rtp_timestamp": 2147543888,
    "rtp_sequence": 400,
    "utc_unix_sec": 1787497254.3450809,
    "utc": "2026-08-23T15:00:54.345081+00:00",
    "gps_time_ns": 1471532468345080867,
    "rtp_timesnap": 2147495888,
    "state": "anchored",
    "host_clock_at_receipt": "2026-08-23T15:00:54.365189+00:00"
  },
  "wire": {
    "bytes_per_component_measured": 4.0,
    "encoding_measured": 4,
    "encoding_reported_by_radiod": 2,
    "measurement_agrees_with_report": false,
    "components_per_rtp_tick": 2,
    "note": "Trust the measurement, not the report. The file is payload bytes verbatim."
  },
  "run": {
    "recording_started_host_utc": "2026-08-23T15:00:54.346393+00:00",
    "recording_stopped_host_utc": "2026-08-23T15:01:54.346512+00:00",
    "requested_seconds": 60.0,
    "settle_sec": 4.0,
    "packets": 6000,
    "bytes_written": 5760000,
    "last_rtp_timestamp": 2148263828,
    "samples_written_estimate": 720000.0,
    "samples_expected_from_host_clock": 720001
  },
  "loss": {
    "packets_received": 70400,
    "packets_dropped": 0,
    "sequence_errors": 0,
    "timestamp_jumps": 0,
    "note": "radiod zero-fills a missed block, so byte counts cannot show loss; these RTP-level counters and the sequence gaps are the honest evidence."
  }
}
```

That sidecar is the whole point of Tier 0. It says which SSRC, on which
multicast group, at which granted rate and preset; it says what the wire really
carried; and it pins sample 0 of the file to an absolute UTC through radiod's
GPS reference. Six months later, that file is still interpretable.

Two readings that need care:

- **`gps_time_ns` is not the host clock, and is not sample-precise.** It is
  radiod's GPS-epoch nanosecond count paired with `rtp_timesnap`; the pair is
  quantised to the 20 ms block grid plus that emission's lateness
  ([character.md §The anchor pair is not atomic](../hardware/character.md#the-anchor-pair-is-not-atomic)).
  Do not attribute a sub-block offset to physics. The `utc` field, computed by
  the library's `rtp_to_utc()`, is the number to use; `host_clock_at_receipt`
  is there only so you can see how far the host clock sat from it (20 ms, on
  this run).
- **`packets_received: 70400` against `packets: 6000`.** `RTPRecorder`'s
  metric counts every datagram arriving on the bound port, before the SSRC
  filter, so on a station where several clients publish to port 5004 it is a
  measure of the host, not of your channel. `packets` (this script's own
  counter, SSRC-filtered) is yours; `packets_dropped` / `sequence_errors` /
  `timestamp_jumps` are also per-SSRC and are the honest ones
  ([docs-gap ledger row 40](../contributor/docs-gap-ledger.md)).

And the loss note is not a formality:
[radiod does not drop, it zero-fills](station-capabilities.md#loss-semantics--what-a-gap-is).
Your byte count will read 100 % complete over a missed block. Sequence gaps and
timestamp jumps are what can actually move when the bad thing happens.

### Cleaning up

Tier-0 captures are yours to delete. The channel needs no cleanup — it expires:

```bash
rm -rf ~/capture
ls -d ~/capture 2>/dev/null || echo "capture dir gone"
```

```text
capture dir gone
```

---

## Prove it against a known signal first

Never trust an unvalidated chain. Before the event, record a signal whose
answer you already know
([§Prove it against a known signal](../EVENT-CLIENT-PLAYBOOK.md#prove-it-against-a-known-signal)).

The cheapest good test on HF: tune **1 kHz below WWV** and confirm the carrier
lands at exactly **+1000 Hz** in the recording. That single measurement
validates frequency scale, sample rate, encoding, and that you are recording
real signal rather than noise or misinterpreted bytes. On a station with an
antenna:

```bash
~/tier0/bin/python ~/tier0_capture.py \
    --status AC0G-B4-status.local --freq 9999000 \
    --preset iq --rate 12000 --seconds 30 --out ~/capture
```

Then, on your laptop, over the file you copied off:

```python
import glob, json, numpy as np
meta_path = sorted(glob.glob("iq_9999000_*.json"))[-1]
meta = json.load(open(meta_path))
assert meta["wire"]["bytes_per_component_measured"] == 4.0     # F32; never assume
x = np.fromfile(meta_path[:-5] + ".f32", dtype="<c8")          # complex64 = 2 x float32 LE
rate = meta["granted"]["sample_rate"]
n = 1 << 18
spec = np.abs(np.fft.fft(x[:n]))
freqs = np.fft.fftfreq(n, 1.0 / rate)
spec[np.abs(freqs) < 100] = 0        # ignore DC/near-DC, which can dominate
print("peak at %.1f Hz (expect +1000)" % freqs[int(np.argmax(spec))])
```

That test caught a wire-format fault that had been reporting *"completeness
100.0 %, gaps 0"* while producing a 1.96×-rate stream of garbage (source: the
playbook, same section). It is worth thirty seconds.

Two cautions on the check itself, both from
[character.md](../hardware/character.md):

- **The RX888's front-end AGC is on**, so amplitude is not an absolute measure
  of antenna-port power over long windows — a strong signal anywhere in the
  64 MHz span can rescale your channel with no event in your stream
  ([station-capabilities.md §AGC and gain](station-capabilities.md#agc-and-gain--science-posture)).
  A carrier at the right *frequency* is the assertion; its *level* is not.
- **On 2.5, 5, 10 and 15 MHz, WWV and WWVH transmit simultaneously**, and
  misidentifying which one you heard costs 3–8 ms of systematic error
  ([character.md §Two stations can share one frequency](../hardware/character.md#two-stations-can-share-one-frequency)).
  Fine for validating a chain; not fine as a timing reference.

---

## Run it unattended

`Restart=on-failure` is **not enough**. The characteristic overnight failure is
a stream layer waiting on packets that never come: it never fails, never exits,
and reports healthy while recording nothing
([§Unattended means watchdogged](../EVENT-CLIENT-PLAYBOOK.md#unattended-means-watchdogged)).

For Tier 0, do not install a system unit — that is a Tier-1 concern and it puts
your process into the station's lifecycle. Use a user-level transient unit,
which dies with your session's scope and needs no operator involvement:

```bash
systemd-run --user --unit=tier0-capture \
    ~/tier0/bin/python ~/tier0_capture.py \
    --status DASI002-status.local --freq 10000000 --preset iq \
    --rate 12000 --seconds 3600 --out ~/capture
systemctl --user status tier0-capture
journalctl --user -u tier0-capture -f
```

⚠ **A `--user` unit dies with your last session unless lingering is on.**
Check before you rely on it — live on DASI002, 2026-08-23,
`loginctl show-user sigmond -p Linger` printed `Linger=no`, so the user manager
is torn down at logout and takes the transient unit with it. Enabling lingering
(`loginctl enable-linger <user>`) is a root action: ask the operator. `screen`
or `tmux` is the fallback that needs no privilege — bare processes survive a
logout on the stations' default `logind` configuration (`KillUserProcesses=no`,
DASI002 `/etc/systemd/logind.conf:22`, live 2026-08-23) — at the cost of the
journal.

Then the part that actually matters — **a file-growth watchdog outside the
capture process**, so that a bug in the capture cannot disable its own
supervisor:

```bash
while sleep 30; do
  newest=$(ls -t ~/capture/*.f32 2>/dev/null | head -1)
  size=$(stat -c %s "$newest" 2>/dev/null || echo 0)
  if [ "$size" = "${last:-}" ]; then
    echo "$(date -u +%FT%TZ) STALLED at $size bytes: $newest"
  fi
  last=$size
done
```

**Test it by killing the live capture.** An untested watchdog is worse than
none, because it buys false confidence. And make restart safe: the script's
UTC-stamped filenames never overwrite and sort chronologically, which is the
other half of that rule.

Write your own gap evidence while you are there. Nothing outside your process
records it for you: keep the sequence-error and timestamp-jump counters per
segment (the sidecar above does), and remember that
[event count matters more than event duration](station-capabilities.md#loss-semantics--what-a-gap-is) —
one ~40 ms dropped block can invalidate ±25.6 s of a GRAPE-style spectrogram.

---

## What you have now

- **A raw file** of payload bytes exactly as radiod sent them, whose true
  layout is recorded in the sidecar as a *measurement* rather than a request.
- **A sidecar** pinning sample 0 to absolute UTC through radiod's GPS
  reference, with the SSRC, multicast group, granted channel characteristics,
  and per-SSRC loss counters.
- **No footprint on the station.** The channel expired by itself; nothing was
  enabled, installed, or written outside your home directory.

Three rules about where to write, which matter more on a production station
than a testbed:

- **Never `/var/lib`.** `/var/lib/sigmond/`, `/var/lib/timestd/`,
  `/var/lib/event-recorder/` belong to the station's own clients, and the disk
  guardian deletes from `/var/lib/timestd/` on its own authority at 95 % full
  ([station-capabilities.md §Storage](station-capabilities.md#storage-per-channel-hour)).
  Your home directory, or a directory the operator agreed to.
- **Budget the disk for the whole window before you start**, and tell the
  operator the number. 12 kHz IQ F32 is 345.6 MB/h; 96 kHz is 2.76 GB/h.
- **Do not process during the event.** Record now, analyse later — the load you
  add to radiod is the load that breaks a neighbour's client, and the deadline
  you are spending is 20 ms.

When your capture stops being a one-shot — when it should survive reboots,
appear in `smd status`, and hand its products to the station's uploader — that
is Tier 1, and it is a different contract.

## Next

- Where the data lands, how RTP maps to UTC, and what each timing tier means
  for a product: data-and-timing.md *(being written)*.
- Turning this into a conformant sigmond client: becoming-a-client.md
  *(being written)*.
- The envelope this recipe assumed:
  [station-capabilities.md](station-capabilities.md).
- How the hardware behaves under it: [character.md](../hardware/character.md).
- The design judgment behind every rule above:
  [EVENT-CLIENT-PLAYBOOK.md](../EVENT-CLIENT-PLAYBOOK.md).

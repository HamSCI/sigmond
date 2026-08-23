# Capture first — the Tier-0 recipe

> **Audience:** scientist
> **Status:** current
> **Verified against:** sigmond 67a3a6d on 2026-08-23 — walk-through fixes (live DASI002 + code/docs)
> **Canonical for:** the Tier-0 capture recipe

**Tier 0 is capture only.** You create one `radiod` channel, you write its bytes
to a file with a timing anchor beside them, and you go home. No sigmond client
contract, no systemd unit, no shared sink, no uploader — those are Tier 1, and
they are a different page ([becoming-a-client.md](becoming-a-client.md)).

Tier 0 exists because the playbook's first rule is
[capture first, process later, always](../EVENT-CLIENT-PLAYBOOK.md#rule-1--capture-first-process-later-always):
the event happens once, and analysis code written under deadline is the part
most likely to be wrong. Everything below is in service of ending the event
with bytes on disk that you can still interpret in six months.

Two ways to do it:

| | Option A — `event-recorder` | Option B — write it yourself |
|---|---|---|
| Effort | a TOML job file | ~375 lines of Python, below |
| Scheduling | built in (`start_utc` / `stop_utc`, lead-in, segments) | you supply it |
| Output | SigMF (`.sigmf-data` + `.sigmf-meta`) | raw payload + JSON sidecar |
| Provenance | the tool that recorded the 2026-08-12 eclipse | proven on DASI002, 2026-08-23 (this page) |

Take Option A unless you need something it cannot express. Read Option B
anyway: most of its length is the checking, and that is the shape every correct
Tier-0 recorder has.

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
   the station's timing client pauses all writes and alerts, and if the disk is
   still ≥ 95 % ten minutes later it begins deleting the oldest recordings until
   the disk is back under 90 %
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

Write a job file — this is the eclipse job as published in the repo README with
`out_dir` repointed at a directory you can write (the published one is
`/var/lib/event-recorder/eclipse-costas-14110`; the file that actually ran
differs in four lines — see
[the worked example](costas-14110-worked-example.md#the-envelope-and-why)):

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
out_dir       = "/opt/git/sigmond/event-recorder/eclipse-costas-14110"  # absolute: "~" is not expanded
```

Save it somewhere you can write — **not** `/etc/event-recorder/jobs/`, which
the repo README's example uses and which is root-owned. Then, on the station
VM:

```bash
mkdir -p ~/event-recorder/jobs
$EDITOR ~/event-recorder/jobs/my-event.toml
./venv/bin/event-recorder run --job ~/event-recorder/jobs/my-event.toml
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
- **Both paths in the repo README's example are root-owned** — the job file at
  `/etc/event-recorder/jobs/` and an `out_dir` under `/var/lib/event-recorder/`
  — because that station agreed to them in advance. The job file above points
  `out_dir` at the `sigmond` user's home instead (`~` = `/opt/git/sigmond` on a
  station). Point both at your own directory unless you have agreed otherwise.

The eclipse run itself — the job, the signal, what came out —
is [costas-14110-worked-example.md](costas-14110-worked-example.md).

---

## Option B — write it yourself

One file, no framework — about 375 lines (328 of them non-blank), most of them
checking rather than recording. This is the whole thing; it ran as printed.

Set up a venv on the station VM (the system `python3` on a station has no
`ka9q` module, and you must not install into the station's own venvs):

```bash
python3 -m venv ~/tier0
~/tier0/bin/pip install ka9q-python
~/tier0/bin/pip show ka9q-python 2>/dev/null | grep -E '^(Name|Version)'
~/tier0/bin/ka9q --help | head -1
```

The `ka9q` CLI comes with the package and is worth having on `PATH` for
read-only pokes at the station (`ka9q list`, `ka9q query <status> --ssrc <n>`).
It has no `--version` flag; `pip show` is where the version lives. On DASI002,
2026-08-23, those two lines printed `Name: ka9q-python` / `Version: 3.22.0` and
`usage: ka9q [-h] [--interface INTERFACE] {list,query,set,tui} ...`. (`grep`,
not `head -2`: closing the pipe early makes `pip` print
`ERROR: Pipe to stdout was broken` on stderr, and an "ERROR" that means nothing
is exactly what you do not want in a recipe. `ka9q --help | head -1` can do the
same — that one is harmless.)

⚠ **PyPI is behind the fleet.** On 2026-08-23 the newest release on PyPI was
**3.22.0** while the stations run **3.25.2** from the checkout at
`/opt/git/sigmond/ka9q-python`
([docs-gap ledger row 38](../contributor/docs-gap-ledger.md)). 3.22.0 is fine
for the presets this recipe uses (`iq`, `usb`, `lsb`, `cw`, `am`), which it
labels with the right demodulator. Every *other* preset gets `DEMOD_TYPE = FM`
regardless of what `presets.conf` says it is — measured for `wfm`, where radiod
runs the narrowband FM demodulator behind a ±110 kHz filter and the channel
**never emits a packet** (source: `ka9q-python/CHANGELOG.md` §[3.25.1]); the
others (`sam`, `ame`, `dsb`, `cwu`, `cwl`, `wspr`, `nam`, `amsq`, `spectrum`)
are mislabelled the same way, with consequences that release note does not
quantify. If you need a preset outside the five, install a pinned 3.25.2
instead — on the station VM:

```bash
~/tier0/bin/pip install 'git+https://github.com/HamSCI/ka9q-python@v3.25.2'
```

⛔ Do **not** `pip install -e /opt/git/sigmond/ka9q-python`. That is the shared
checkout every client on the station imports; an editable install writes
`*.egg-info` into it and one shared library checkout means touching it changes
all of them at once (source:
[§Station traps](../EVENT-CLIENT-PLAYBOOK.md#station-traps-worth-knowing), "One
shared library checkout"). If the station cannot reach GitHub either, ask the
fleet admin — copying the checkout into your own home and installing from the
copy is the fallback, not an editable install over theirs.

### The script

Save as `~/tier0_capture.py` on the station VM:

```python
#!/usr/bin/env python3
"""tier0_capture.py -- record one radiod channel to a raw file with a UTC anchor.

Tier 0: capture only. No sigmond contract, no systemd unit, no shared sink.
It creates ONE radiod channel with a mandatory lifetime, refreshes that
lifetime while it lives, writes every RTP payload verbatim to <name>.f32, and
writes <name>.json carrying (a) the FIRST packet's timing anchor, (b) every
value radiod actually GRANTED -- both as ensure_channel reported it and as a
FRESH poll reports it after the separate encoding command has landed -- and
(c) an independent measurement of the wire format. Nothing here trusts what it
asked for.

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
                # tear them apart. This is the pair captured when the channel
                # was discovered, seconds before this packet; everything after
                # it is EXTRAPOLATION at the channel's nominal rate. wallclock
                # is the library's rtp_to_utc() of this packet's timestamp
                # against that pair.
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
    is F32LE, 2 is S16BE.
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


def parse_args():
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
    return ap.parse_args()


def run(args, control, raw_path, meta_path, requested):
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
        "encoding_from_ensure_channel": channel.encoding,
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

    # ensure_channel polls ONCE, immediately after the create -- possibly
    # before the separate OUTPUT_ENCODING command has been applied -- and it
    # matches on frequency only, so the encoding it hands back can be stale.
    # Re-poll now that the command has had time to land, and ask the library's
    # own verifier what it thinks.
    fresh = control.poll_channel(channel.ssrc, expected_freq=args.freq, timeout=2.0)
    fresh_encoding = fresh.encoding if fresh is not None else None
    verified = control.verify_channel(channel.ssrc, expected_freq=args.freq,
                                      expected_encoding=int(Encoding.F32LE))
    print("fresh poll: encoding=%s (ensure_channel said %s); "
          "verify_channel(expected=F32LE)=%s"
          % (fresh_encoding, channel.encoding, verified), flush=True)

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
        "schema": "tier0-capture/2",
        "ka9q_python_version": _lib_version(),
        "raw_file": raw_path.name,
        "requested": requested,
        "granted": granted,
        "anchor": cap.first,
        "wire": {
            "bytes_per_component_measured": bpc,
            "encoding_measured": measured_encoding,
            "encoding_from_ensure_channel": channel.encoding,
            "encoding_fresh_poll": fresh_encoding,
            "verify_channel_expected_f32le": verified,
            "measurement_agrees_with_ensure_channel": (
                None if measured_encoding is None else measured_encoding == channel.encoding),
            "measurement_agrees_with_fresh_poll": (
                None if (measured_encoding is None or fresh_encoding is None)
                else measured_encoding == fresh_encoding),
            "components_per_rtp_tick": components,
            "note": "Trust the measurement. The file is payload bytes verbatim.",
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
            "packets_received_on_port": metrics["packets_received"],
            "packets_dropped": metrics["packets_dropped"],
            "sequence_errors": metrics["sequence_errors"],
            "timestamp_jumps": metrics["timestamp_jumps"],
            "note": "radiod zero-fills a missed block, so byte counts cannot show loss; "
                    "these RTP-level counters and the sequence gaps are the honest "
                    "evidence. packets_received_on_port counts EVERY datagram on the "
                    "bound port, not just this SSRC.",
        },
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=False) + "\n")

    print("wrote %s (%d bytes, %d packets)" % (raw_path, cap.bytes_written, cap.packets), flush=True)
    print("wrote %s" % meta_path, flush=True)
    if bpc is None:
        print("ERROR: could not measure the wire format -- do not trust this file's layout",
              flush=True)
    else:
        if measured_encoding != channel.encoding:
            print("NOTE: measured %.2f bytes/component (encoding %s); ensure_channel had "
                  "reported encoding %s" % (bpc, measured_encoding, channel.encoding), flush=True)
        if fresh_encoding is not None and measured_encoding != fresh_encoding:
            print("WARNING: measured encoding %s but a FRESH poll still reports %s -- "
                  "radiod's status disagrees with its own wire; decode using the "
                  "measurement" % (measured_encoding, fresh_encoding), flush=True)
    print("stopping: no channel teardown is sent. The lifetime simply expires and radiod "
          "reclaims the channel within ~%ds -- a deliberate crash-safe trade, at the cost "
          "of up to that long streaming to nobody." % (LIFETIME_FRAMES // 50), flush=True)
    return 0


def main():
    args = parse_args()
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
    # multicast group from (client_id, status_address). The context manager
    # closes the control socket on the way out.
    with RadiodControl(args.status, client_id=args.client_id) as control:
        return run(args, control, raw_path, meta_path, requested)


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

Eight decisions, each of which is there because the alternative fails quietly.

- **`client_id`, never `destination=`.** `RadiodControl(status, client_id=...)`
  makes ka9q-python derive a per-(client, radiod) multicast group, so you
  cannot land on a peer client's group. Passing neither raises `ValidationError`
  rather than falling back to a shared default (source:
  `ka9q-python/ka9q/control.py::RadiodControl.__init__`, audit finding F5). It is
  used as a context manager so the control socket is closed on every exit path,
  including an exception.
- **`lifetime=6000`, plus a keepalive.** radiod cannot learn that a Python
  process died. 6000 frames ≈ 120 s at the default 20 ms blocktime; a *bare
  poll does not refresh it* — only a command carrying a `LIFETIME` tag does
  (source: `ka9q-python/ka9q/control.py::set_channel_lifetime`, audit finding
  F7), which is what the keepalive thread sends every 30 s. **The script sends
  no teardown when it exits** — it stops refreshing and lets the timer run out,
  so radiod reclaims the channel within ~120 s. That is a deliberate crash-safe
  trade: the same mechanism cleans up after a `kill -9` or a power cut, at the
  cost of up to two minutes of a channel streaming to nobody.
- **The sidecar records what was *granted*, not what was asked.** On the create
  path `ensure_channel` accepts the channel on frequency match alone; a
  sample-rate or preset divergence is logged, not raised
  ([station-capabilities.md §Encoding](station-capabilities.md#frequency-and-bandwidth--what-radiod-will-hand-you)).
  So the script prints a warning on divergence and writes both `requested` and
  `granted` blocks.
- **The encoding `ensure_channel` returns can be stale, so the script re-polls.**
  radiod takes `OUTPUT_ENCODING` as a *separate* command sent after the create
  (source: `ka9q-python/ka9q/control.py:1601-1618`;
  [character.md §The encoding you asked for is a second command](../hardware/character.md#the-encoding-you-asked-for-is-a-second-command)),
  while `ensure_channel` confirms the channel with **one** targeted poll that
  matches on *frequency only* and never re-checks encoding (source: same file,
  `ensure_channel`'s `poll_channel(ssrc, expected_freq=…)` and the comment above
  it). Those two can race. So after `--settle 4.0` the script calls
  `poll_channel()` again and `verify_channel(expected_encoding=…)`, and records
  **both** values — `encoding_from_ensure_channel` and `encoding_fresh_poll` —
  in the sidecar. On the live run below they disagreed, and the fresh one was
  right.
- **The wire format is measured, never believed.** Even a fresh poll is a
  *report*. The script accumulates payload bytes ÷ RTP ticks ÷ components as it
  records, which is a direct measurement of bytes-per-component: ~4 is F32, ~2
  is S16. That measurement is what the sidecar tells a future reader to decode
  with, and it is the check that catches a lost grant no status field can
  (source: [§Station traps](../EVENT-CLIENT-PLAYBOOK.md#station-traps-worth-knowing),
  "Stale encoding from `ensure_channel`").
- **The anchor is read as one snapshot.** `channel.get_anchor()` returns
  `(gps_time, rtp_timesnap)` as a single tuple; reading the two attributes
  separately can tear across a background status update. The `wallclock`
  argument the callback receives is already `rtp_to_utc()` of that packet's RTP
  timestamp against that pair, referenced to radiod's GPS epoch — not to the
  host clock. The script stores the RTP timestamp, the UTC, the raw pair, and an
  explicit `anchored`/`unanchored` state, exactly as the playbook's
  [§Record the timing anchor](../EVENT-CLIENT-PLAYBOOK.md#record-the-timing-anchor-not-just-the-samples)
  asks.

  ⚠ **That pair is the one captured at channel discovery, not at your first
  packet.** In the run below it was taken ~6 s before the first packet arrived
  (`rtp_timesnap 2147495888` against a first-packet `rtp_timestamp` of
  2147570768 — 74,880 ticks = 6.24 s at 12 kHz), and every sample after it is
  **extrapolated** from that one pair at the channel's nominal rate. Over 60 s
  that is fine; over an hour you are extrapolating an hour from a single
  block-grid-resolution reading. A recorder that cares should adopt a fresher
  pair — the `fresh` `ChannelInfo` this script already polls carries one — and
  record which pair it used.
- **It anchors once, at the first packet, and does not poll for divergence.**
  A single `(GPS_TIME, RTP_TIMESNAP)` pair carries block-grid resolution plus
  emission lateness, and on a busy radiod it jitters ~0.45 s between snapshots
  ([character.md §The anchor pair is not atomic](../hardware/character.md#the-anchor-pair-is-not-atomic)).
  Re-reading it during the run and "correcting" your timestamps against it
  manufactures the jitter into your data. Record the first pair; interpret
  offline. (The one re-poll the script does make is about *encoding*, before
  recording starts, and never touches the anchor.)
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

Real output. Two sets of repeated `TTL=0` notices are trimmed: eight after the
first (one per other channel radiod was already carrying), and seventeen more
after the settle, which is radiod's other channels answering the re-poll.

```text
Radiod reporting TTL=0 for SSRC 1038463489: Multicast data restricted to localhost loopback only!
channel granted: ssrc=1038463489 10.000000 MHz preset=iq rate=12000 encoding=2 dest=239.183.22.68:5004
settling 4.0s (radiod takes OUTPUT_ENCODING as a SEPARATE command)
fresh poll: encoding=4 (ensure_channel said 2); verify_channel(expected=F32LE)=True
recording 60s to /opt/git/sigmond/capture/iq_10000000_20260823T152306Z.f32
     7.3s  packets=1      bytes=1440       bytes/component=?  level=?
    17.3s  packets=1001   bytes=961440     bytes/component=4.00  level=-126.6 dBFS
    27.3s  packets=2003   bytes=1923360    bytes/component=4.00  level=-127.3 dBFS
    37.3s  packets=3003   bytes=2883360    bytes/component=4.00  level=-126.9 dBFS
    47.4s  packets=4005   bytes=3845280    bytes/component=4.00  level=-126.9 dBFS
    57.4s  packets=5005   bytes=4805280    bytes/component=4.00  level=-126.7 dBFS
wrote /opt/git/sigmond/capture/iq_10000000_20260823T152306Z.f32 (5760000 bytes, 6000 packets)
wrote /opt/git/sigmond/capture/iq_10000000_20260823T152306Z.json
NOTE: measured 4.00 bytes/component (encoding 4); ensure_channel had reported encoding 2
stopping: no channel teardown is sent. The lifetime simply expires and radiod reclaims the channel within ~120s -- a deliberate crash-safe trade, at the cost of up to that long streaming to nobody.
```

Five things to read out of that:

- **`encoding=2` from `ensure_channel`, `encoding=4` from a poll four seconds
  later, `4.00 bytes/component` on the wire.** The grant *was* honoured — radiod
  was sending F32LE and, once asked again, said so; `verify_channel(expected =
  F32LE)` returned `True`. What was wrong is the value `ensure_channel` handed
  back: it polls once, immediately after the create and before its own separate
  `OUTPUT_ENCODING` command can be reflected in status, and it matches that
  reply on frequency alone. **Stale, not misreported**
  ([docs-gap ledger row 39](../contributor/docs-gap-ledger.md)). Had the script
  believed that first value it would have decoded 2 bytes where 4 were sent and
  produced a plausible, well-formed, wrong array at twice the sample count, with
  clean completeness and zero gaps. Re-poll, or verify, or measure — this
  recipe does all three.
- **The arithmetic closes.** 5,760,000 bytes ÷ 8 bytes per complex F32 sample =
  720,000 samples = exactly 60.0 s at 12 kHz.
- **The packets are not all the same size, and the first one is not special.**
  6000 packets in 60 s averages 960 bytes, but no packet is 960 bytes: radiod
  caps a PCM packet at **1440 payload bytes** to fit the Ethernet MTU
  (`BYTES_PER_PKT` in `ka9q-radio/src/audio.c:27`), which for F32 complex is
  1440 ÷ (4 bytes × 2 components) = **180 complex samples**, and it flushes
  whatever is left over rather than waiting to fill the next one, because the
  default output buffering is none (`maxdelay = 0`, "No output buffering",
  `ka9q-radio/src/modes.c:225`; the send loop is `send_output` in `audio.c`).
  A 20 ms block at 12 kHz is 240 complex samples, so every block goes out as
  **1440 bytes + 480 bytes** — two packets per block, 100 packets/s, 96 kB/s.
  The counters above say so exactly: `packets=1001 bytes=961440` is 500 pairs
  plus one 1440-byte packet — 500 × 1920 + 1440 = **961,440**, to the byte, and
  the same identity holds at every heartbeat (`packets=5005` →
  2502 × 1920 + 1440 = 4,805,280). So `packets=1 bytes=1440` is simply the
  first full-size packet of the first block, not a runt or a header. Never
  divide a byte count by a packet count and
  call it a packet size — and note that the script never does: it divides
  payload bytes by *RTP ticks*, which is immune to how radiod chose to chop
  them up, and that is what `samples_written_estimate` is derived from.
- **`level ≈ −127 dBFS`** is the noise floor of a receiver with nothing plugged
  into it. On a station with an antenna this line is where you would see WWV.
- **`TTL=0`** is not an error: it is radiod telling you the stream is
  loopback-only, which is the station's normal configuration
  ([station-capabilities.md](station-capabilities.md#what-the-station-cannot-do)).
  ka9q-python prints one notice per channel status it parses.
- **Nothing was torn down.** The closing line is the honest description of the
  lifetime contract, not a euphemism: the script stops refreshing, and radiod
  reclaims the channel when the timer runs out.

The files it left, on the station VM:

```bash
ls -la ~/capture
```

```text
total 5640
drwxrwsr-x  2 sigmond sigmond    4096 Aug 23 15:24 .
drwxrwsr-x 29 sigmond sigmond    4096 Aug 23 15:23 ..
-rw-rw-r--  1 sigmond sigmond 5760000 Aug 23 15:24 iq_10000000_20260823T152306Z.f32
-rw-rw-r--  1 sigmond sigmond    2209 Aug 23 15:24 iq_10000000_20260823T152306Z.json
```

(`~` for the `sigmond` user on a station is `/opt/git/sigmond`, which is why
the absolute paths above look the way they do.)

### The sidecar

```json
{
  "tool": "tier0_capture.py",
  "schema": "tier0-capture/2",
  "ka9q_python_version": "3.22.0",
  "raw_file": "iq_10000000_20260823T152306Z.f32",
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
    "encoding_from_ensure_channel": 2,
    "multicast_address": "239.183.22.68",
    "port": 5004
  },
  "anchor": {
    "rtp_timestamp": 2147570768,
    "rtp_sequence": 624,
    "utc_unix_sec": 1787498593.3457522,
    "utc": "2026-08-23T15:23:13.345752+00:00",
    "gps_time_ns": 1471533805105752210,
    "rtp_timesnap": 2147495888,
    "state": "anchored",
    "host_clock_at_receipt": "2026-08-23T15:23:13.365408+00:00"
  },
  "wire": {
    "bytes_per_component_measured": 4.0,
    "encoding_measured": 4,
    "encoding_from_ensure_channel": 2,
    "encoding_fresh_poll": 4,
    "verify_channel_expected_f32le": true,
    "measurement_agrees_with_ensure_channel": false,
    "measurement_agrees_with_fresh_poll": true,
    "components_per_rtp_tick": 2,
    "note": "Trust the measurement. The file is payload bytes verbatim."
  },
  "run": {
    "recording_started_host_utc": "2026-08-23T15:23:13.362929+00:00",
    "recording_stopped_host_utc": "2026-08-23T15:24:13.363081+00:00",
    "requested_seconds": 60.0,
    "settle_sec": 4.0,
    "packets": 6000,
    "bytes_written": 5760000,
    "last_rtp_timestamp": 2148290708,
    "samples_written_estimate": 720000.0,
    "samples_expected_from_host_clock": 720002
  },
  "loss": {
    "packets_received_on_port": 72864,
    "packets_dropped": 0,
    "sequence_errors": 0,
    "timestamp_jumps": 0,
    "note": "radiod zero-fills a missed block, so byte counts cannot show loss; these RTP-level counters and the sequence gaps are the honest evidence. packets_received_on_port counts EVERY datagram on the bound port, not just this SSRC."
  }
}
```

That sidecar is the whole point of Tier 0. It says which SSRC, on which
multicast group, at which granted rate and preset; it says what the wire really
carried and how that compares with **both** status readings; and it pins sample
0 of the file to an absolute UTC through radiod's GPS reference. Six months
later, that file is still interpretable.

Three readings that need care:

- **`encoding_from_ensure_channel: 2` vs `encoding_fresh_poll: 4`.** Keep both.
  The first is what the library told you at create time; the second is what
  radiod said once the follow-up command had landed; `encoding_measured: 4` is
  what came off the wire. Recording only the first would have poisoned the file;
  recording only the last would have hidden the race from whoever debugs the
  next one.
- **`gps_time_ns` is not the host clock, is not sample-precise, and is not
  contemporaneous with your first packet.** It is radiod's GPS-epoch nanosecond
  count paired with `rtp_timesnap`, quantised to the 20 ms block grid plus that
  emission's lateness
  ([character.md §The anchor pair is not atomic](../hardware/character.md#the-anchor-pair-is-not-atomic)),
  and captured at channel discovery — here 74,880 RTP ticks (6.24 s) before the
  first packet. Everything after it is extrapolation at the nominal rate. Do not
  attribute a sub-block offset to physics. The `utc` field, computed by the
  library's `rtp_to_utc()`, is the number to use; `host_clock_at_receipt` is
  there only so you can see how far the host clock sat from it (20 ms, on this
  run).
- **`samples_written_estimate: 720000.0` against
  `samples_expected_from_host_clock: 720002`.** They measure different things.
  The first is the file itself — bytes written ÷ the *measured* wire width —
  and it is what you decode. The second is only `rate × (stopped − started)`
  with `started`/`stopped` read from `time.time()` around the sleep loop, so it
  inherits ~0.2 ms of scheduling slop on each end. Two samples is **167 µs**;
  the RTP counter is the ruler and the host clock is the estimate, so a small
  divergence here is the host clock being imprecise, not the recording being
  short. A *large* one (a whole second, say) would be worth chasing.
- **`packets_received_on_port: 72864` against `packets: 6000`.** `RTPRecorder`'s
  `packets_received` metric counts every datagram arriving on the bound port,
  before the SSRC filter, so on a station where several clients publish to port
  5004 it is a measure of the host, not of your channel — the sidecar renames it
  to say so. `packets` (this script's own counter, SSRC-filtered) is yours;
  `packets_dropped` / `sequence_errors` / `timestamp_jumps` are also per-SSRC and
  are the honest ones
  ([docs-gap ledger row 40](../contributor/docs-gap-ledger.md)).

And the loss note is not a formality:
[radiod does not drop, it zero-fills](station-capabilities.md#loss-semantics--what-a-gap-is).
Your byte count will read 100 % complete over a missed block. Sequence gaps and
timestamp jumps are what can actually move when the bad thing happens.

### Optional: record what the timestamp is *worth*

One thing that sidecar does **not** carry is the fifth field
[data-and-timing.md](data-and-timing.md#how-to-stamp-your-own-capture) asks
every segment to have: the **tier, σ and judge age**. `utc` says *when*; only
the tier and σ say what that number is worth, and without them you cannot
state an uncertainty six months later.

They do not come from radiod or ka9q-python — they come from hf-timestd, and
no scientist-facing page or API publishes them
([docs-gap ledger row 50](../contributor/docs-gap-ledger.md)). Two ways to get
them anyway, on a station that runs hf-timestd:

1. **`smd status`** — a read-only operator verb that works as an ordinary
   station user and prints one `judge` line. Always there; a text report.
2. **`/run/hf-timestd/offset_judge.json`** — the same numbers as data.
   World-readable on DASI002 (`-rw-r--r-- timestd:timestd`, live
   2026-08-23T18:02Z), carrying `"schema": "offset-judge-v1"` and a `judge`
   block: `"tier": "T3", "sigma_ns": 3083883.8, "age_s": 0.002` beside
   `"gpsdo_discipline": "holdover"`. hf-timestd's own spec says recorders "MAY
   consume" it (`hf-timestd/docs/OFFSET-JUDGE-SPEC-2026-08-05.md` §3, §4.4).
   **Prefer this when it is present** — parse it, keep the whole `judge` block,
   and fall back to (1) when the file is absent or its mtime is stale (`smd`
   itself treats a stale file as no reading: `lib/sigmond/timing_judge.py`,
   `load_offset_judge`). It is a `/run` file on a station you do not own, so
   code it as "may vanish", not as an API.

hf-timestd's own raw-buffer sidecars carry the tier as a field
(`timing.judge_tier`,
[data-and-timing.md §How hf-timestd's sidecars do it](data-and-timing.md#how-hf-timestds-sidecars-do-it)),
and a Tier-1 client should subscribe to the authority properly
([data-and-timing.md §If you can do better](data-and-timing.md#if-you-can-do-better-subscribe-to-the-authority)).

The snippet below takes route (1), because it needs nothing but `smd` on
`$PATH` and it is the one that was run live for this page.

⚠ **This is a text capture of a human-readable report, not an interface.** The
wording can change under you. Store the line verbatim, parse it offline, and
never let a failure here end a capture — which is why the helper returns a
string instead of raising. (Run where there is no `smd` at all, it returns
`["unavailable: [Errno 2] No such file or directory: 'smd'"]`.)

Paste this beside the script's other helpers:

```python
import re, subprocess

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def judge_lines():
    """Every `smd status` line naming the timing judge, colour stripped."""
    try:
        out = subprocess.run(["smd", "status"], capture_output=True,
                             text=True, timeout=90).stdout
    except Exception as exc:                              # noqa: BLE001
        return ["unavailable: %s" % exc]
    return [_ANSI.sub("", ln).strip() for ln in out.splitlines() if "judge" in ln]
```

then take a reading either side of the recording — `judge_at_start =
judge_lines()` immediately before `recorder.start_recording()`, `judge_at_end =
judge_lines()` immediately after `cap.close()` — and add both to the sidecar's
`run` block:

```python
            "timing_judge_at_start": judge_at_start,
            "timing_judge_at_end": judge_at_end,
```

Run live on DASI002 with the add-on applied (a second 60 s capture,
2026-08-23T17:53Z, otherwise the same command as above), the sidecar's `run`
block gained:

```json
    "timing_judge_at_start": [
      "timing judge:",
      "⚠  judge T3  σ=3174.9 µs  age 0s  gpsdo=holdover"
    ],
    "timing_judge_at_end": [
      "timing judge:",
      "⚠  judge T3  σ=3151.8 µs  age 0s  gpsdo=holdover"
    ]
```

Read that, and the point of the exercise is immediate: DASI002 was on **T3** —
WWV/WWVH/CHU tick fusion — with **σ ≈ 3.2 ms** and its GPSDO in **holdover**
([station-capabilities.md §Timing](station-capabilities.md#timing-you-can-rely-on--tiers)).
The `utc` field in the same sidecar prints microseconds. The capture is worth
milliseconds. Only the judge line tells you which.

### Cleaning up

Tier-0 captures are yours to delete. The channel needs no cleanup — it expires.
On the station VM:

```bash
rm -rf ~/capture
ls ~/capture
```

```text
ls: cannot access '/opt/git/sigmond/capture': No such file or directory
```

---

## Prove it against a known signal first

Never trust an unvalidated chain. Before the event, record a signal whose
answer you already know
([§Prove it against a known signal](../EVENT-CLIENT-PLAYBOOK.md#prove-it-against-a-known-signal)).

The cheapest good test on HF: tune **1 kHz below WWV** and confirm the carrier
lands at exactly **+1000 Hz** in the recording. That single measurement
validates frequency scale, sample rate, encoding, and that you are recording
real signal rather than noise or misinterpreted bytes. On the VM of a station
that has an antenna:

```bash
~/tier0/bin/python ~/tier0_capture.py \
    --status AC0G-B4-status.local --freq 9999000 \
    --preset iq --rate 12000 --low-edge -5000 --high-edge 5000 \
    --seconds 30 --out ~/capture
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

⚠ **Unlike everything above, this section has not been run here.** The Tier-0
proof on this page was done on DASI002, which has no antenna, so there was
nothing at +1000 Hz to find: the capture path is proven live, the known-signal
check is not. The two snippets are written from the playbook and from the
sidecar's own fields; execute them on a station with an antenna before you rely
on them.

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
which dies with your session's scope and needs no operator involvement. On the
station VM:

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
supervisor. In a second session on the station VM:

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

⚠ **This section has not been run here either.** A one-hour unattended capture
was outside the read-only budget for the testbed, so the `systemd-run` command
and the watchdog loop above are written, not executed — treat them as a
starting point you must test. What *was* measured live on DASI002, 2026-08-23,
is the pair of readings the lingering warning rests on
(`loginctl show-user sigmond -p Linger` → `Linger=no`;
`/etc/systemd/logind.conf:22` → `#KillUserProcesses=no`).

**Test the watchdog by killing the live capture.** An untested watchdog is worse
than none, because it buys false confidence. And make restart safe: the script's
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
  `/var/lib/event-recorder/` belong to the station's own clients, and at 95 %
  full hf-timestd's disk guardian pauses all writes and alerts — then, if the
  disk is still ≥ 95 % ten minutes later, begins deleting the oldest recordings
  from `/var/lib/timestd/` on its own authority until the disk is back under
  90 %
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
  for a product: [data-and-timing.md](data-and-timing.md).
- Turning this into a conformant sigmond client:
  [becoming-a-client.md](becoming-a-client.md).
- The envelope this recipe assumed:
  [station-capabilities.md](station-capabilities.md).
- How the hardware behaves under it: [character.md](../hardware/character.md).
- The design judgment behind every rule above:
  [EVENT-CLIENT-PLAYBOOK.md](../EVENT-CLIENT-PLAYBOOK.md).

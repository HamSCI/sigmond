#!/usr/bin/env python3
"""Sample-deficit meter for a radiod multicast group — Phase 1 instrument.

Measures samples radiod never produced, by comparing each SSRC's RTP tick
advance against wall time. Loss shows as a deficit accumulating in steps;
jitter alone leaves the deficit near zero.

WHY THIS IS NOT THE THROWAWAY VERSION
-------------------------------------
Two ways to be lied to, both observed on B4, both now self-reported:

1.  RTP sequence gaps cannot see USB-layer loss. Samples dropped before
    radiod forms a packet yield a CONTINUOUS sequence carrying fewer
    samples. Never conclude "no loss" from sequence counting; the deficit
    is the measurement, the sequence count is a cross-check.

2.  The meter starves on the very load it measures. On 2026-08-13 a run
    reported deficits 132% worse than baseline with 35,236 sequence
    losses -- per channel 5871/5877/5872/5871/5874/5871, six independent
    channels agreeing to 0.1%. That uniformity is a receiver-side socket
    overflow, not a source dropping samples: genuine radiod-side deficits
    differ across channels by ~80%. A 16 MB SO_RCVBUF was not enough
    under a host at load 7.4 with wspr-recorder at 107% CPU.

So this version raises the buffer, asks for SCHED_FIFO, and -- the part
that matters -- records the evidence needed to disqualify its own output:
its socket's drop counter, the system RcvbufErrors delta, the spread of
loss across channels, and the host load throughout. It emits a `validity`
verdict so a starved run declares itself instead of masquerading as
radiod loss.

Usage:
    drain_meter.py <mcast-addr> <port> <seconds> <out.json> [ssrc,ssrc,...]
"""
import collections
import json
import os
import socket
import struct
import sys
import time

CHECKPOINT_SEC = 60.0
WANT_RCVBUF = 256 * 1024 * 1024      # ask big; report what was granted
UNIFORMITY_FRAC = 0.02               # per-channel spread below this => receiver-side


def _snmp_udp():
    """(InErrors, RcvbufErrors) from /proc/net/snmp."""
    try:
        # /proc/net/snmp gives a header line and a values line, both "Udp:".
        lines = open("/proc/net/snmp").read().splitlines()
        for i in range(len(lines) - 1):
            if lines[i].startswith("Udp:") and lines[i + 1].startswith("Udp:"):
                d = dict(zip(lines[i].split()[1:], lines[i + 1].split()[1:]))
                return int(d.get("InErrors", 0)), int(d.get("RcvbufErrors", 0))
    except (OSError, IndexError, ValueError):
        pass
    return 0, 0


def _socket_drops(sock):
    """This socket's own kernel drop counter, via its inode in /proc/net/udp."""
    try:
        ino = os.stat(f"/proc/self/fd/{sock.fileno()}").st_ino
        for line in open("/proc/net/udp").read().splitlines()[1:]:
            f = line.split()
            if len(f) > 12 and int(f[9]) == ino:
                return int(f[-1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _host_sample():
    try:
        load = float(open("/proc/loadavg").read().split()[0])
    except OSError:
        load = -1.0
    return {"t": round(time.time(), 1), "load1": load}


def _realtime():
    """SCHED_FIFO so the drain loop is not preempted by decode bursts."""
    try:
        os.sched_setscheduler(0, os.SCHED_FIFO,
                              os.sched_param(max(1, os.sched_get_priority_min(os.SCHED_FIFO) + 10)))
        return True
    except (PermissionError, OSError, AttributeError):
        return False


def main(argv):
    dest, port, secs, out = argv[1], int(argv[2]), float(argv[3]), argv[4]
    watch = ({int(h, 16) for h in argv[5].split(",")} if len(argv) > 5 else None)

    rt = _realtime()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # SO_RCVBUFFORCE (CAP_NET_ADMIN) is the only way past net.core.rmem_max,
    # but CPython does not expose the constant on every build -- hence the
    # numeric fallback and the AttributeError catch. Missing that catch made
    # the privileged path, the whole point of this rewrite, crash on first
    # use while the unprivileged path kept working.
    _force = getattr(socket, "SO_RCVBUFFORCE", 33)
    try:
        s.setsockopt(socket.SOL_SOCKET, _force, WANT_RCVBUF)
    except (AttributeError, PermissionError, OSError):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, WANT_RCVBUF)
    granted = s.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)   # Linux reports 2x
    s.bind(("", port))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                 struct.pack("4s4s", socket.inet_aton(dest), socket.inet_aton("0.0.0.0")))
    s.settimeout(5.0)

    try:
        rmem_max = int(open("/proc/sys/net/core/rmem_max").read())
    except OSError:
        rmem_max = 0
    sys.stderr.write(
        f"drain_meter: rcvbuf granted {granted//(1024*1024)} MB "
        f"(requested {WANT_RCVBUF//(1024*1024)} MB, "
        f"net.core.rmem_max {rmem_max//(1024*1024)} MB), SCHED_FIFO={rt}\n")
    if granted < WANT_RCVBUF:
        sys.stderr.write(
            "  WARNING: kernel capped the buffer. Raise net.core.rmem_max, or run\n"
            "  with CAP_NET_ADMIN so SO_RCVBUFFORCE applies. A capped buffer is the\n"
            "  known cause of phantom loss on a loaded host.\n")
    if not rt:
        sys.stderr.write("  WARNING: no SCHED_FIFO (needs root/CAP_SYS_NICE) — "
                         "the meter can be preempted by the load it measures.\n")

    cps = collections.defaultdict(list)
    unw, lastraw, lastseq = {}, {}, {}
    seqlost = collections.Counter()
    host = []
    err0, rcv0 = _snmp_udp()
    drops0 = _socket_drops(s)

    t0 = time.monotonic(); tend = t0 + secs; nextcp = t0 + CHECKPOINT_SEC
    npkt = 0

    def snapshot(final=False):
        now = time.monotonic()
        for k in unw:
            cps[k].append((now - t0, unw[k]))
        err1, rcv1 = _snmp_udp()
        d1 = _socket_drops(s)
        rec = {
            "elapsed": now - t0, "packets": npkt,
            "cps": {str(k): v for k, v in cps.items()},
            "seqlost": {str(k): v for k, v in seqlost.items()},
            "instrument": {
                "rcvbuf_granted": granted, "rcvbuf_requested": WANT_RCVBUF,
                "sched_fifo": rt,
                "socket_drops": (None if d1 is None or drops0 is None else d1 - drops0),
                "udp_inerrors_delta": err1 - err0,
                "udp_rcvbuf_errors_delta": rcv1 - rcv0,
            },
            "host": host,
        }
        if final:
            rec["done"] = True
            rec["validity"] = verdict(rec)
        json.dump(rec, open(out, "w"))
        return rec

    while time.monotonic() < tend:
        try:
            d, _ = s.recvfrom(65536)
        except socket.timeout:
            if time.monotonic() >= nextcp:
                nextcp += CHECKPOINT_SEC
            continue
        if len(d) < 12:
            continue
        ssrc = struct.unpack("!I", d[8:12])[0]
        if watch and ssrc not in watch:
            continue
        npkt += 1
        ts = struct.unpack("!I", d[4:8])[0]
        seq = struct.unpack("!H", d[2:4])[0]
        if ssrc not in unw:
            unw[ssrc] = 0
        else:
            unw[ssrc] += (ts - lastraw[ssrc]) & 0xFFFFFFFF
        lastraw[ssrc] = ts
        if ssrc in lastseq:
            e = (lastseq[ssrc] + 1) & 0xFFFF
            if seq != e:
                g = (seq - e) & 0xFFFF
                if g < 30000:
                    seqlost[ssrc] += g
        lastseq[ssrc] = seq
        if time.monotonic() >= nextcp:
            host.append(_host_sample())
            snapshot()
            nextcp += CHECKPOINT_SEC

    rec = snapshot(final=True)
    s.close()
    v = rec["validity"]
    print("done: %d packets, %d ssrcs — %s" % (npkt, len(cps), v["verdict"]))
    for r in v["reasons"]:
        print("  " + r)
    return 0 if v["verdict"] == "VALID" else 2


def verdict(rec):
    """Disqualify the run when the evidence says the METER lost the packets.

    The discriminator is spread, not magnitude. A socket overflow discards
    whatever happens to arrive, so every channel loses nearly the same
    count; radiod-side loss is per-channel and differs a lot (~80% on B4).
    """
    inst = rec["instrument"]
    losses = sorted(rec["seqlost"].values())
    reasons, bad = [], False

    # No data is not a clean result. Without this, a wrong multicast group,
    # a dead radiod or an IGMP failure all report VALID with a deficit of
    # nothing -- the most dangerous possible output from an instrument whose
    # entire job is to notice absence.
    if not rec.get("packets") or not rec.get("cps"):
        return {"verdict": "INCONCLUSIVE — no packets received",
                "reasons": ["received %d packets on the group; check the "
                            "multicast address, radiod, and IGMP snooping "
                            "before reading anything into this run"
                            % (rec.get("packets") or 0)]}
    if min((len(v) for v in rec["cps"].values()), default=0) < 3:
        return {"verdict": "INCONCLUSIVE — too few checkpoints",
                "reasons": ["fewer than 3 minute-checkpoints per SSRC; the "
                            "rate fit needs a longer run"]}

    if inst["socket_drops"]:
        bad = True
        reasons.append(f"socket drop counter advanced by {inst['socket_drops']} "
                       "— the METER lost these, not radiod")
    if inst["udp_rcvbuf_errors_delta"]:
        bad = True
        reasons.append(f"system UDP RcvbufErrors +{inst['udp_rcvbuf_errors_delta']} "
                       "during the run — a receiver on this host overflowed")
    if len(losses) >= 3 and losses[-1] > 0:
        spread = (losses[-1] - losses[0]) / max(losses[-1], 1)
        if spread < UNIFORMITY_FRAC:
            bad = True
            reasons.append(
                f"per-channel loss is uniform to {spread*100:.1f}% "
                f"({losses[0]}..{losses[-1]} across {len(losses)} channels) — that is a "
                "receiver-side overflow signature, not source loss")
        else:
            reasons.append(f"per-channel loss spread {spread*100:.0f}% — consistent with "
                           "source-side loss, not a uniform receiver overflow")
    if inst["rcvbuf_granted"] < inst["rcvbuf_requested"]:
        reasons.append(f"rcvbuf capped at {inst['rcvbuf_granted']//(1024*1024)} MB "
                       "— raise net.core.rmem_max before trusting a loaded-host run")
    if not inst["sched_fifo"]:
        reasons.append("no SCHED_FIFO — meter was preemptible")
    loads = [h["load1"] for h in rec.get("host", []) if h["load1"] >= 0]
    if loads:
        reasons.append("host load1 mean %.2f, peak %.2f during the run"
                       % (sum(loads)/len(loads), max(loads)))
    if not bad:
        reasons.insert(0, "no receiver-side starvation detected; deficits are source-side")
    return {"verdict": "INVALID — instrument starved" if bad else "VALID",
            "reasons": reasons}


if __name__ == "__main__":
    sys.exit(main(sys.argv))

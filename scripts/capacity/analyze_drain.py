#!/usr/bin/env python3
"""Report a drain_meter run: per-SSRC deficit, and whether to believe it.

Reads the JSON drain_meter.py writes. Prints the validity verdict FIRST --
a run whose meter starved produces deficits that look like radiod loss and
are not, and that mistake has already cost one two-hour measurement.
"""
import json
import statistics
import sys

from drain_meter import verdict


def fit_deficit(cps):
    """Least-squares tick rate, and seconds of samples never delivered."""
    t = [c[0] for c in cps]
    y = [c[1] for c in cps]
    n = len(t)
    mt, my = sum(t) / n, sum(y) / n
    den = sum((a - mt) ** 2 for a in t)
    slope = sum((a - mt) * (b - my) for a, b in zip(t, y)) / den if den else 0.0
    nominal = round(slope / 1000) * 1000 or 12000
    span = t[-1] - t[0]
    return nominal, slope, span, (nominal * span - (y[-1] - y[0])) / nominal


def main(path):
    d = json.load(open(path))
    v = d.get("validity")
    if not v:
        # A run written before drain_meter graded itself still carries the
        # evidence needed to grade it: per-channel sequence loss. Recompute
        # rather than print "unknown" and let a starved run pass as data.
        d.setdefault("instrument", {"socket_drops": None, "udp_rcvbuf_errors_delta": 0,
                                    "rcvbuf_granted": 0, "rcvbuf_requested": 0,
                                    "sched_fifo": False})
        d.setdefault("host", [])
        v = verdict(d)
        v["reasons"].append("(graded retrospectively — this run predates the check)")
    print("VERDICT: %s" % v["verdict"])
    for r in v["reasons"]:
        print("   " + r)
    print()

    print("elapsed %.0f s   packets %d   complete=%s\n"
          % (d["elapsed"], d["packets"], d.get("done", False)))
    print("%-10s %8s %9s %12s %11s %9s"
          % ("ssrc", "nominal", "checkpts", "fit_rate", "deficit_s", "seq_lost"))
    rows = []
    for k, cps in d["cps"].items():
        if len(cps) < 3:
            continue
        nominal, slope, span, deficit = fit_deficit(cps)
        rows.append((int(k), nominal, len(cps), slope, deficit,
                     d["seqlost"].get(k, 0), span))
    for ssrc, nom, nc, slope, defi, sl, _ in sorted(rows):
        print("%-10s %8d %9d %12.3f %11.4f %9d"
              % ("%08x" % ssrc, nom, nc, slope, defi, sl))
    if not rows:
        return 1
    defs = [r[4] for r in rows]
    span = max(r[6] for r in rows)
    print("\ndeficit  min %+.4f  median %+.4f  max %+.4f s   over %.0f s "
          "=> worst %.0f ppm" % (min(defs), statistics.median(defs), max(defs),
                                 span, max(defs) / span * 1e6))
    # The receiver-vs-source discriminator is the spread of SEQUENCE LOSS,
    # not of deficit. A starved meter still yields uneven deficits (13% on
    # the 2026-08-13 baseline) because the deficit mixes real source loss
    # with what the socket dropped -- reading that spread as "source-side"
    # is exactly the wrong call, and the reason this is spelled out here.
    sl = sorted(r[5] for r in rows)
    if sl[-1] > 0:
        sspread = (sl[-1] - sl[0]) / max(sl[-1], 1)
        print("per-channel SEQUENCE LOSS spread %.1f%%  (%s)"
              % (sspread * 100,
                 "uneven — source-side" if sspread >= 0.02
                 else "UNIFORM — receiver overflow; the meter lost these"))
    else:
        print("per-channel sequence loss: none — nothing dropped in transit")
    print("\n  |deficit| < ~0.01 s     -> no sample loss; clock/measurement effect only")
    print("  deficit growing in steps -> real USB drain loss")
    print("  uniform across channels  -> the meter, not radiod")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/drain.json"))

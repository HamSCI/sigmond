# Capacity instrumentation

Phase 1 of [CAPACITY-MEASUREMENT-PLAN.md](../../docs/CAPACITY-MEASUREMENT-PLAN.md):
the tools that have to work before any capacity number means anything.

## drain_meter.py

Measures samples radiod never produced on a multicast group, by comparing
each SSRC's RTP tick advance against wall time.

```bash
sudo systemd-run --unit=drain --collect --property=User=hamsci \
  scripts/capacity/drain_meter.py 239.241.122.1 5004 7200 /tmp/drain.json
scripts/capacity/analyze_drain.py /tmp/drain.json
```

Run it as root (or with `CAP_NET_ADMIN` + `CAP_SYS_NICE`) so it can force a
256 MB receive buffer and take `SCHED_FIFO`. Without those it still runs and
still measures — it just tells you it was preemptible, and you should weight
the result accordingly.

**It grades its own output.** `validity.verdict` is `VALID` or
`INVALID — instrument starved`, from its socket's drop counter, the system
`RcvbufErrors` delta, and the spread of loss across channels.

## Read the spread, not the magnitude

The single most useful discriminator, learned the expensive way:

| Per-channel loss | Means |
|---|---|
| **Uniform** across channels | Receiver-side socket overflow — **the meter lost them** |
| **Differs** a lot (~80% on B4) | Source-side; radiod really did not produce them |

A socket overflow discards whatever happens to arrive, so every channel loses
nearly the same count. Real drain loss is per-channel and uneven.

## Two ways this measurement lies

1. **Sequence gaps cannot see USB-layer loss.** Samples dropped before radiod
   forms a packet leave a *continuous* RTP sequence carrying fewer samples.
   A 90 s gap count on bee1 and B4 returned 0.000000% while the deficit was
   real. Deficit is the measurement; sequence count is a cross-check.

2. **The meter starves on the load it measures.** See the baselines below.

## baselines/

Reference runs, including the invalid one — that is the point of keeping it.

| File | |
|---|---|
| `2026-08-12T2143Z-run1-fusion-info.json` | **VALID.** 2 h, deficits 0.95–1.72 s across 6 SSRCs (133–241 ppm), **zero** sequence loss, 96–99% arriving in ~30 discrete bursts. Deficits differ by 80% across channels — the source-side signature. |
| `2026-08-13T0008Z-run2-fusion-warning.json` | **INVALID.** Deficits 2.83–3.27 s looked 132% worse, with 35,236 sequence losses. Per channel: 5871, 5877, 5872, 5871, 5874, 5871 — uniform to 0.1%. The meter's 16 MB buffer overflowed; radiod was not at fault. |
| `2026-08-13T2023Z-run3-post-memory-fixes.json` | **VALID.** 2 h after the memory work (~1.7 GB reclaimed: VSCode session, hot-tier sizing, ring sizing). Deficit **0.08-0.32 s, 12-45 ppm** — down ~80% from run 1 — with a **median per-minute deficit of -0.00018 s**, i.e. zero. Zero radiod RTP↔GPS steps and zero OOM kills across the window. Shows 2001 uniform "sequence loss" per channel that costs no samples (2001 packets would be 13.4 s of audio; measured deficit is 0.3 s) — a sequence discontinuity, not loss. |
| `2026-08-13T0008Z-run2-host-context.log` | Load, context switches, IRQ rate and per-client CPU each minute of run 2: mean load 7.39, wspr-recorder 107% CPU. Why the buffer overflowed. |

Run 2 was intended as an A/B of an hf-timestd logging change (INFO → WARNING,
an ~8000× reduction in log rate). **It measured nothing of the kind.** The two
runs were four hours apart with decode load differing by an order of magnitude,
and the meter starved in the second. The logging change is live on B4 and
remains unevaluated.

The lesson is in the plan: interleave A and B conditions, or record load with
every measurement and discard runs that are not comparable.

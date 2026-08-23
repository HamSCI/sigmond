# Producer Threat Model

> **Audience:** contributor
> **Status:** shipped
> **Verified against:** sigmond affebbd on 2026-08-23 — not re-verified (header only)
> **Canonical for:** what threatens radiod data production

What threatens radiod's core data production, what currently defends it,
and what is still exposed.

`PACKET-LOSS-DIAGNOSTICS.md` is about *diagnosing* a symptom after the
fact. This document is about *defending the producer* so the symptom does
not occur. They are complements: that one tells you where to look, this
one tells you what to protect and why.

Every threat below cites the measurement that established it. Claims that
are still assumptions are marked **[UNVERIFIED]** — please do not promote
one to fact without a measurement, and please do not quietly delete one
either.

## The asset

A single radiod instance receiving roughly **2 Gbit/s** from an RX888 over
USB, FFT-ing the entire stream, and inverse-FFT-ing each requested channel
into its own RTP multicast stream.

Its deadline is the filter block time — **20 ms** on AC0G-B4. Miss it and
radiod emits a block of **zeros**, which is the loss event this whole
document exists to prevent.

The three properties worth defending, in order:

1. **Continuity.** No dropped filter output blocks.
2. **Timing integrity.** RTP timestamps remain the authoritative substrate.
3. **Availability.** The process keeps running.

Continuity comes first because of an amplification factor that is easy to
underestimate: **a ~40 ms dropped block invalidates up to ±25.6 s of GRAPE
spectrogram** (NFFT=512 full-window validity masking). On 2026-08-16
WWV_25000 recorded **80 gap events**; measured per-event loss the following
day averaged ~67 ms, putting that day's actual data loss on the order of
seconds. Its spectrogram nonetheless reported **1419/1440 min — 98.5%
complete**, i.e. **21 minutes invalidated**. Roughly a
1000× amplification, so **event count matters far more than event
duration**. Optimising for shorter stalls is close to worthless;
optimising for fewer is everything.

## Threat classes

Four classes, because the defences differ:

| class | examples | defence shape |
|---|---|---|
| **Contention** | cache, memory bandwidth, IRQ, CPU, disk I/O | reservation / partitioning |
| **Capacity** | RAM ceiling, disk space | limits + headroom |
| **Change** | deploys, reboots, config drift | making intent explicit and checked |
| **Observability** | metrics that lie | knowing you are degraded |

Observability is listed as a threat class deliberately. You cannot defend
what you cannot see, and several counters on this system report *healthy*
while data is being lost. See "Metrics that lie" below.

## Measured threats

### Placement — radiod on the boot CPU

**3.53 gaps/channel-hour.** The kernel refuses to tick-isolate CPU 0
(`nohz_full=0-13` on the cmdline comes back as `1-13` from sysfs), so a
radiod pinned to CPU 0's hyperthread pair keeps taking the timer tick on
half its pair regardless of what isolation was requested. Independently
reproduced by rob on a 2×RX888 host, where moving radiod off core 0 took
drops from thousands/hour to zero.

**Defended.** Fixed at source in sigmond `c75959b`: the placement rule
lived in *four* divergent copies, all handing radiod the lowest-numbered
pair. They now share one `assign_radiod_cores`, which never returns the
boot CPU's core and prefers cores inside the kernel's isolated set when
one exists.

### Cache eviction by co-resident work

**0.68 → 0.00 gaps/channel-hour.** The first direct LLC measurement on
AC0G-B4 (2026-08-18, via resctrl) showed radiod's occupancy repeatedly
collapsing from ~13 MiB to ~5 MiB while the decoders took up to ~11 MiB of
the same 16 MiB L3.

This is the threat that placement *cannot* fix: the part has **one 16 MiB
L3 shared by all 16 CPUs** (`shared_cpu_list = 0-15`). Moving radiod to a
private core buys it no private cache.

**Defended** by L3 CAT reserving 13 of 16 ways for radiod's cores
(`resctrl-cat.service`). Note the goal is *not* residency — radiod's FFT
working set is ~26 MB, larger than the entire L3, so it streams from DRAM
permanently at a constant ~4.5 GB/s. CAT protects the small hot fraction
(twiddle tables, filter coefficients, the block in flight) from being
flushed by a decode burst.

CAT is the single most valuable defence here because it is a **posture,
not a patch**: it protects radiod from every current and future batch job
at once, including ones nobody has written yet.

### Interrupts on radiod's cores

**7,133,689 interrupts delivered to CPU 12 in 1.5 h.** After a reboot the
RX888 xhci vector landed on radiod's own core.

Note the asymmetry that makes this subtle: the RX888's *own* interrupt
**belongs** on radiod's cores — that is `sigmond-rx888-irq-affinity`, and
it is deliberate (see hf-timestd `T6-BLOCK-SLIP-ROOT-CAUSE-2026-08-10.md`).
It is radiod's data arriving, not competition. The threat is *other*
interrupts landing there.

**Partly defended.** `irq_pin_drift_allowed = false` in the local_system
probe now catches drift, using deltas rather than since-boot counters.
Only handlers with an established intended placement should be declared —
encoding a guess turns an opinion into an alarm.

### Well-intentioned tuning

**15–30× worse.** Guest kernel isolation (`isolcpus` / `nohz_full` /
`rcu_nocbs`) was applied to protect radiod and made it dramatically worse
in every variant tested:

| configuration | gaps/channel-hour |
|---|---|
| no isolation, CAT | **0.00** |
| no isolation, no CAT | 0.68 |
| isolation + nohz_full, IRQ off radiod | 10.00 |
| isolation, no nohz_full, IRQ on radiod, + CAT | 15.60 |
| isolation + nohz_full, IRQ on radiod | 20.82 |

Two mechanisms, one confirmed and one not:

- `nohz_full` assumes the CPU is *not* interrupted — that is its whole
  premise — so pairing it with a deliberately co-located ~1300/s IRQ keeps
  all of its per-kernel-entry cost and delivers none of its benefit. These
  two designs are mutually exclusive.
- **[UNVERIFIED]** `isolcpus=domain` removes the load-balancing domain,
  and radiod runs **52 threads on 2 CPUs**. Without a shared domain the
  balancer cannot move a runnable thread from a busy CPU 12 to an idle
  CPU 13. This fits the persistent ~54%/~30% asymmetry across that pair,
  but has not been proven.

`isolcpus` suits a single-threaded RT task. For a many-threaded producer it
may remove the very mechanism keeping its output threads current — and
output threads falling behind is precisely what `FILTER_DROPS` counts.

**The generalisable lesson: tuning applied without measurement is itself a
threat.** This change was made in good faith, reasoned from first
principles, and cost a 15× regression for several hours. It was reverted
only because the gap counter made it visible.

### Redundant batch work

Every reboot triggered a complete ~3 h GRAPE run against a day already
processed and uploaded, because `grape-daily.timer` declared
`Requires=grape-daily.service` — an *activation* dependency, on a timer
that is `WantedBy=timers.target`. 20260817 was processed three times in one
day at ~1.5 CPU-hours each.

**Defended.** hf-timestd `aaab04a`. The same line also propagated *stop*,
so `systemctl stop grape-daily` silently disarmed the daily schedule until
the next reboot — a fault that failed quietly in the safe-looking
direction.

### Batch load in general

GRAPE decimation reads **14 GB per channel per day** (84 GB across six),
peaks at 3.9 GB RSS, and historically ran for ~3 h at 50% of a core.

It is worth being precise about what class this belongs to. It is not
special; it is the largest current member of a class that also includes the
catch-up sweep, `storage-trim` every 15 min, magnetometer packaging, the
uploader, `iono-reanalysis`, `vtec`, `l2-calibration` — **and operator
diagnostics**. The single gap event observed in an otherwise clean window
(2026-08-18 00:45Z, all six channels simultaneously) coincided with an
engineer reading ~1700 sidecar JSON files off `raw_buffer`. Read-only
analysis is inside the threat model.

**[UNVERIFIED] post-CAT status.** Every zero-gap measurement so far has
been taken with grape idle. Whether grape still threatens radiod now that
13 ways are reserved is unknown until a measurement window includes a
grape run.

Chasing individual batch jobs is whack-a-mole. Reserving cache ways is not.

## Exposed — no defence in place

### Disk I/O

`IOWeight` is a soft share, not a ceiling; no `io.max` is set on any batch
job. This is the residual suspect behind the one gap event seen in an
otherwise clean window, and it is untested.

### Memory capacity

`wspr-recorder` was OOM-killed twice on 2026-08-16 (cgroup limit, `jt9`
invoking the OOM killer). The ceiling is real and reachable, and reclaim
pressure is a plausible stall source for a neighbouring real-time process.

### Memory bandwidth

radiod pulls a constant ~4.5 GB/s and nothing guarantees it. MBA exists on
this part (`MB:0=2048` in the resctrl schemata) but rob measured it as too
coarse to be useful here. Peak observed total was ~9.1 GB/s against ~40
GB/s available, so this is not currently binding — but it is unguarded.

### Disk space

189 GB of 252 GB used (**79%**), of which `raw_buffer` is 160 GB. If it
fills, recording stops. Note the coupling: that buffer is large mainly so a
*daily batch* can read a whole day at once. Incremental decimation would
let retention collapse to hours — a capacity defence, not just tidiness.

### Redundancy

One RX888, one radiod, one host. No failover. Out of scope for tuning, but
it belongs in an honest threat model.

## Metrics that lie

You cannot defend what you cannot see, and several counters here report
*healthy* over genuine loss. Each of these produced a wrong conclusion
before being caught:

| counter | the lie | use instead |
|---|---|---|
| `samples_written`, `completeness_pct` | read **100%** over dropped blocks — radiod zero-fills, the recorder faithfully writes the zeros | `gap_count` in the raw_buffer sidecars |
| `completeness_pct` (historical) | was hardcoded to 100 | (fixed) |
| `journalctl` as the wrong user | returns **empty, with no error**, for other users' units | read as root |
| `ps -o psr` | shows *last-run* CPU; reads as a false escape from a cpuset | `/proc/<tid>/status Cpus_allowed_list` |
| `/proc/interrupts` cumulative counts | a boot transient reads as permanent drift | delta since previous probe |
| summed `run_delay` across threads | 45 s aggregate looked alarming; the two threads that matter were at **0 ms** | per-thread, critical path only |
| `pgrep -f <pattern>` | matches its own helper shell | `pgrep -f '[p]attern'` |

The general rule this suggests: **prefer the counter that can only move
when the bad thing actually happens.** Byte counts and completeness
percentages are derived and can be satisfied by zeros; `gap_count` cannot.

## Who observes what, and where it lands

The previous section says which fields to trust. This one says which module
produces each one and whether the value survives long enough to be useful
after an incident.

| stage | honest metric | produced by | sampled by | aggregates in |
|---|---|---|---|---|
| RF / front end | `AD_OVER`, AGC gain | radiod `rx888.c` / `agc_rx888()` → status multicast | `ka9q-python/ka9q/status.py` → `wspr-recorder/__main__.py::_read_cycle_ad_over` | recorder journal only |
| USB transport | xhci IRQ rate, per-CPU delivery | kernel `/proc/interrupts` | `discovery/local_resources.py::_parse_proc_interrupts` + `_summarise_irq` | environment observation cache (delta vs previous probe) |
| **radiod FFT / filter** | **`gap_count`, `gap_samples`** | **hf-timestd `core/binary_archive_writer.py`**, driven by `core_recorder_v2.py` | `local_resources.py::_summarise_gaps`; `gap-hourly.sh` | raw_buffer sidecars (~3 d) → **`/var/log/gap-hourly.tsv`** (indefinite) |
| — radiod's own view of the same event | `FILTER_DROPS` (tag 77) | radiod → `ka9q-python/ka9q/status.py` | `wspr-recorder::_read_cycle_filter_drops` | recorder journal, logged loud when > 0 |
| CPU scheduling | `run_delay` per thread | kernel `/proc/<tid>/schedstat` | **nothing** — ad hoc only | **nowhere** |
| cache / memory | LLC occupancy, MBM bandwidth | CPU RDT → kernel `resctrl` *(host only)* | `local_resources.py::_read_resctrl` | environment cache — **instantaneous, no history** |
| RTP emission | sample deficit vs wall time | radiod RTP timestamps | `scripts/capacity/drain_meter.py` | run output only, on demand |
| client reception | segments present, `samples_written` | same `binary_archive_writer.py` | — | raw_buffer sidecars |
| client processing | decode latency per cycle | `wspr-recorder/spot_sink.py` (`cycle UTC … dt=`) | — | journald → `sink.db` → wd30 `wsprdaemon.spots` |
| timing output | FUSE Std Dev, offset | hf-timestd fusion → chrony SHM refclock | `timestd-chrony-monitor`, `hf-timestd quality` | `/var/tmp/fuse_gate.tsv`, chrony's own stats |

Four things fall out of the last two columns.

**Three stages have no durable store.** `run_delay`, LLC/MBM occupancy and RTP
sample deficit are all instantaneous or on-demand. If nothing is sampling at
the moment of a fault, *the evidence is gone permanently* — you cannot go back
and ask what the cache looked like during last night's incident. This is why
the 2026-08-18 diagnosis required an engineer present running samplers by hand,
and why `gap-hourly.tsv` matters out of proportion to its size: it is the only
stage with an indefinite record.

**`run_delay` has no owner at all** — not produced by us, not sampled by
anything, not stored. It is the measurement that ruled out PREEMPT_RT, and it
survives only in a session transcript.

**The liar shares a module with the honest field.** `samples_written` and
`gap_count` are written by the *same* `binary_archive_writer.py`, into the same
sidecar, on the same line — but only one of them can see zero-filled loss. That
adjacency is exactly why the trap is easy to fall into.

**`gap_count` is measured twice, independently** — once by the recorder as it
writes the sidecar, once by radiod as `FILTER_DROPS` and read back over the
status socket. The two agree. That is a stronger position than any other stage
here and is worth preserving: an independent second witness is what lets you
trust a counter that reports zero.

### Gap list

If post-hoc forensics is wanted rather than live-only diagnosis, these three
need a sampler with somewhere to write:

1. **LLC / MBM** — highest value, since cache pressure is the mechanism behind
   the loss this document exists to prevent, and resctrl is now mounted anyway.
2. **RTP sample deficit** — `drain_meter.py` already computes it; it just has
   no scheduled run or store.
3. **`run_delay`** on the critical threads only — cheap, and it would let the
   PREEMPT_RT question be answered from history rather than re-measured.

## Standing defences

| defence | what it protects against | where |
|---|---|---|
| radiod off the boot CPU | timer tick on half its pair | `assign_radiod_cores`, sigmond `c75959b` |
| L3 CAT 13/3 | cache eviction by **all** co-resident work | `resctrl-cat.service` (host) |
| `AllowedCPUs` fencing | decoder threads on radiod's cores | smd cpu-affinity drop-ins |
| RX888 IRQ pinned to radiod's cores | capture path interrupted by other work | `sigmond-rx888-irq-affinity` |
| min==max frequency pin | amd-pstate parking an idle-looking core | `cpu-pin-VMID.sh` |
| hourly gap logging | not knowing you are degraded | `/var/log/gap-hourly.tsv` |
| `smd admin environment` | drift in any of the above | `local_resources` probe |

## How to verify the posture holds

```bash
cat /var/log/gap-hourly.tsv                                  # the honest metric, hourly
smd admin environment probe --source=local_resources --force
smd admin environment list --kind local_system               # drift in the defences
```

On the Proxmox host:

```bash
cat /sys/fs/resctrl/radiod/schemata     # expect L3:0=1fff  (13 of 16 ways)
cat /sys/fs/resctrl/schemata            # expect L3:0=e000  (3 ways for everything else)
```

Baselines to compare against, all measured on AC0G-B4 on 2026-08-18:
**3.53** gaps/channel-hour with radiod on the boot CPU, **0.68** once moved
off it, **0.00** with CAT. Reaching a visually clean 24 h spectrogram needs
roughly **0.05** — about 13× better than the pre-CAT figure — which is why
the amplification factor at the top of this document matters so much.

## Related

- `docs/PACKET-LOSS-DIAGNOSTICS.md` — diagnosing loss after the fact
- `docs/HOST-CAPACITY-PLANNING.md` — workload tiers and capacity levers
- `docs/CAPACITY-MEASUREMENT-PLAN.md` — the CAT analysis this finally acted on
- `etc/environment.example.toml` — thresholds that make drift visible
- hf-timestd `docs/T6-BLOCK-SLIP-ROOT-CAUSE-2026-08-10.md` — why the RX888
  IRQ belongs *on* radiod's cores

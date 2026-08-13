# Capacity measurement plan — how much headroom do we actually have?

**Status:** action plan, ready to execute. Companion to
[HOST-CAPACITY-PLANNING.md](HOST-CAPACITY-PLANNING.md), which frames the problem and
asks the open questions; this document says how to answer them with measurements
instead of debate.

**Why now:** the 2026-08-12 eclipse showed we can stand up an ad-hoc client in about a
day ([EVENT-CLIENT-PLAYBOOK.md](EVENT-CLIENT-PLAYBOOK.md)). That capability is only
safe if adding a client is a *calculation* — "this costs X, we have Y left" — rather
than a gamble that it won't tip radiod over. Today it is a gamble.

## The working hypothesis, stated plainly

**A Ryzen 5825U configured as DASI2 currently does is believed to be over capacity — it
cannot carry even the existing client set without packet loss.** That is the operator's
assessment from lived experience, and it is consistent with everything documented: the
A/B-proven hf-timestd-vs-WSPR failure, the chronic output jitter, the recorder-side
guards we have accumulated to *accommodate* loss rather than eliminate it.

So this is not a headroom survey with a comfortable answer expected. It is a **deficit
measurement**, and it carries a decision gate:

> **Acceptance criterion: zero packet loss with the full intended client set.**
> Not "low," not "tolerable," not "the guards catch it." Zero.

If Phase 5 cannot reach that on this hardware, the plan has still succeeded — it will
have produced the evidence that the all-in-one DASI2 assumption does not hold, and the
justification for splitting acquisition from processing. Either outcome is a result.
What we cannot keep doing is accommodating a loss whose cause we have not measured.

---

## The missing primitive

Everything downstream — cost per client, admission control, tier policy — depends on
one thing we cannot currently do: **measure how close radiod is to missing its
deadline.**

The constraint is hard and unforgiving. The RX888 runs at 129.6 Msps × 16 bit =
**259 MB/s, continuously, with no flow control**. If radiod does not drain the xHCI
ring in time, those samples are gone.

| In-flight ring budget | Time to overrun at 259 MB/s |
|---|---|
| 1 MB | 3.9 ms |
| 4 MB | 15.4 ms |
| 16 MB | 61.7 ms |

So the question "how much headroom is there?" is really "how much slack is there
between radiod's worst-case scheduling latency and that budget?" Nobody has measured
either number.

**Worse, our existing instrumentation is blind to the failure.** On 2026-08-12 a 90 s
passive RTP sequence-gap count on both bee1 and B4 returned **0.000000% loss** — while
B4 carried 46 SSRCs and 310,501 packets. That result is real but says nothing about
USB-layer loss, because samples dropped *before* radiod forms a packet produce a
**continuous RTP sequence containing fewer real samples**. The signature is an RTP↔GPS
*step*, not a gap. Any capacity work that measures sequence gaps will conclude
everything is fine right up until it isn't.

**And the instrument itself can lie in the other direction.** On 2026-08-13 a
second 2 h drain run reported deficits 132% worse than the first, with 35,236 RTP
sequence losses where the first run had zero. It was not radiod. The per-channel
losses were **5871, 5877, 5872, 5871, 5874, 5871** — six independent channels
agreeing to 0.1%, which is the fingerprint of a *receiver-side* socket overflow
discarding whatever arrives, not of a source dropping samples. (Genuine
radiod-side deficits differ across channels by ~80%.) `Udp RcvbufErrors` on the
host confirmed it, and the context sampler showed why: mean load 7.39 with
wspr-recorder at 107% CPU, against ~11% when the run started. A 16 MB
`SO_RCVBUF` was not enough under that load.

So the measuring tool starves on exactly the load it exists to measure, and
reports phantom loss when it does. **Both failure modes must be fixed before
Phase 1 produces a number anyone should act on:**

* a much larger `SO_RCVBUF`, and `SCHED_FIFO` on the listener
* per-run `RcvbufErrors` deltas recorded beside the deficit, so a starved run
  **declares itself** instead of masquerading as radiod loss
* per-channel loss spread reported as a first-class output — uniformity across
  channels is the tell that the reading is the instrument's, not the source's

**Known recurring load events, which any measurement window must dodge or
account for** (found 2026-08-13 while clearing the decks):

* `grape-daily.service` — fires **01:01 UTC daily**, runs ~3.5 h wall for
  **1 h 47 min CPU and a 2.6 GB memory peak**. It overlapped 72 of drain run
  2's 120 minutes. Note the honest limit of that observation: load1 averaged
  8.04 in the 53 minutes *before* it started and 7.01 after, so it is **not**
  the dominant driver and does not by itself explain that run's starvation.
  It is a confound to control, not a culprit to blame.
* `mag-recorder-upload.timer` — 03:07 UTC daily; small.
* B4's idle-ish baseline sits around **load 7–8** with radiod at ~112% and
  hf-timestd at ~91–141% CPU. Understanding that floor is arguably Phase 2's
  real question, ahead of measuring anything added on top.

A corollary for Phase 3, which stops clients one at a time: any A/B taken hours
apart is confounded by decode load that swings by an order of magnitude across
the day. Either randomise and interleave the A and B conditions, or record the
load alongside every measurement and discard runs that are not comparable. The
2026-08-13 pair had to be discarded on exactly this ground — the fusion logging
change it was meant to evaluate remains unmeasured.

---

## Phase 1 — Build the margin meter

*No configuration changes. Instrumentation only. Runs on any host.*

Three instruments, combining into one scalar.

**A. Scheduling latency** — the numerator of the margin.

    cyclictest -p 99 -a <radiod-core> -t 1 -m -D 24h -h 400

Gives max and p99.9 wake-up latency on radiod's isolated core. This is the closest
proxy we have for "could the drain thread have been late?"

**B. Drain health** — the endpoint that actually matters.

* radiod's own overrun/drop counters (`rx888.c` step logging already exists in the fork)
* **RTP↔GPS step events** per hour — the true USB-loss signature
* *Not* RTP sequence gaps. Keep counting them, but as a network check only.

**C. Cache pressure** — the mechanism rob identified (radiod is L1/L2-hit-rate bound,
not core-count bound).

* `perf stat -e LLC-loads,LLC-load-misses -p <radiod-pid>`
* or resctrl MBM counters once `/sys/fs/resctrl` is mounted (`cat_l3`, `mba`, `rdt_a`
  are present on both the 5700U and 5825U and currently unused)

**Deliverable:** a `margin` scalar, published like any other health metric:

    margin = (ring_budget_ms − p99.9_latency_ms) / ring_budget_ms

**Exit criterion:** the meter runs unattended for 24 h on one host and produces a
number.

---

## Phase 2 — Baseline the floor

*radiod only. Every client stopped.*

Run the meter for 24 h with nothing else on the box. This is the best the host can
ever do.

**Exit criterion:** zero steps, zero overruns, and a recorded p99.9 latency. If the
floor is not clean, stop — no capacity work is meaningful until it is.

Run on **bee1 (bare metal, radiod-only)** and **B4 (KVM guest, full suite)**. The
delta between them is the virtualization answer we currently only suspect. Note what
2026-08-12 already ruled out on B4: vCPU steal (0.02%), ballooning, deep C-states, and
USB emulation (it is proper VFIO passthrough of both xHCI controllers). Still open:
AVIC is **disabled** while the passed-through controller generates 1,206 irq/s and
2,537 irq_window_exits/s, and all of those interrupts land on host cpu3 — inside the
range pinned to guest vCPUs.

---

## Phase 3 — Cost per client, measured not declared

Start clients **one at a time**, 2 h each, recording Δmargin, ΔLLC-miss-rate and
Δstep-rate against the Phase 2 baseline.

This produces the cost table [HOST-CAPACITY-PLANNING.md](HOST-CAPACITY-PLANNING.md)
asks for, but observed rather than asserted — which also answers its open question 6
("what we can measure vs what we have to declare") for everything except cache
footprint.

Suggested order, cheapest first: `mag-recorder` (non-radiod), `meteor-scatter`,
`psk-recorder`, `wspr-recorder`, `hf-timestd` (expected worst — 9 channels, and the
A/B-proven cause of the +2.0 s WSPR anchor corruption).

**Exit criterion:** a table of `client → Δmargin, Δchannels, ΔLLC-misses`.

---

## Phase 4 — Find the knee

Add synthetic load until the first step or overrun appears. **That number is the
capacity.**

`event-recorder` is the natural load generator — it is a generic recorder that opens
N channels with specified characteristics and does nothing else with them, so it adds
radiod-side load without adding decode load. Ramp channels 1, 2, 4, 8, 16… and watch
the margin fall.

This separates two things that have always been conflated:

* **radiod-side cost** — more channels to filter and emit (the hf-timestd failure mode)
* **consumer-side cost** — decode bursts evicting cache (the jt9/WSPR failure mode)

**Exit criterion:** "this host sustains N channels at the current configuration, and
the first degradation appears at N+1." A number you can hand to anyone proposing a new
client.

---

## Phase 5 — Widen the margin, then re-measure

Only after Phases 1–4 give a before-number. Ordered by expected value:

1. **Increase URB depth.** The single highest-value change: it widens the deadline
   linearly and costs only RAM. There are no buffer/URB knobs in bee1's radiod config
   today, so it runs whatever `rx888.c` defaults to. Find that number; raise it. 4 ms
   → 62 ms of tolerance changes the problem's character.
2. **`irqaffinity=`** on the kernel cmdline — **absent on bee1 today.** `isolcpus`
   stops tasks, *not* interrupts; every xHCI IRQ is currently affine to `0-15`. Push
   general IRQs to the non-radiod CCX and pin each controller's IRQs to the CCX that
   consumes its samples.
3. **`libusb_event` thread priority.** Observed on bee1: `proc_rx888` is SCHED_FIFO
   prio 2 and `fft` is prio 1, but **`libusb_event` is SCHED_OTHER** — the thread
   closest to the hardware deadline is the only one not real-time. ⚠ This is inference
   from a thread listing, not from reading `rx888.c`; confirm with Phil/rob before
   changing it.
4. **Mount resctrl and reserve L3** for radiod via CAT; throttle decoder groups with
   MBA. The only *enforced* cache isolation available, and essential on a unified-L3
   host like the 5825U where pinning alone cannot isolate.
5. **`LimitMEMLOCK=infinity` + `mlockall()`** to remove major page faults from the
   drain path.
6. **On B4 specifically:** test `kvm_amd.avic=1`, and move the `vfio-msix` IRQs to host
   cpu14/15 (outside the vCPU range). Both reversible; measure, don't assume.

**Exit criterion:** Phase 4 re-run showing the new knee, and the improvement
attributable to each change.

---

## Phase 6 — Turn the number into admission control

The durable answer to "adding a client must not clog the drain" is not knowledge, it
is **enforcement**.

* Extend the client contract so each client declares its cost — the fields
  [HOST-CAPACITY-PLANNING.md](HOST-CAPACITY-PLANNING.md) proposes (`tier`,
  `cpu_sustained`, `cpu_burst`, `cache_footprint`, `instances_per_cycle`) plus the one
  the measurements show matters most: **`ka9q_channels`**, which `inventory --json`
  already reports.
* Add `smd admin diag capacity`: tally declared costs against the host's *measured*
  capacity from Phase 4, and warn — or refuse — when a new client would exceed it.
* Add the check to `smd apply` and to bring-up, so it is on the forward path rather
  than an audit someone remembers to run.

That closes HOST-CAPACITY-PLANNING.md's open question 5 (contract vs catalog) in favour
of the contract, because Phase 3 will have shown that costs are measurable per client
and change when clients change.

---

## Phase 5b — The decision gate

After Phase 5's fixes are measured, re-run Phase 3 with the **full intended client
set** — including headroom for one ad-hoc event client, since that capability is a
requirement, not a bonus.

**If margin holds and steps are zero:** the all-in-one DASI2 shape works, and Phase 6
turns the measured capacity into enforcement.

**If it does not:** we have quantitative evidence that a single 5825U cannot carry
acquisition plus the client suite at zero loss, and the answer is architectural:

* **Split acquisition from processing.** A radiod-only host does nothing that can
  burst — no decode spike, no packaging job, no upload — which removes the interference
  classes rather than scheduling around them. bee1 already runs this way (`ttl = 1`,
  "no local decode here", `isolcpus=0-7`), so the pattern is proven, only unformalised.
* The 5700U's **split L3 (2 × 4 MiB)** suits a two-radiod acquisition node — one per
  CCX, one RX888 per xHCI controller — while the 5825U's **unified 16 MiB L3** suits a
  client host, where a large shared cache helps decoders and there is no drain loop to
  protect.
* Cost of the trade, stated honestly: this **relocates** loss risk from the cache
  domain to the network domain — IGMP snooping/querier, switch buffers, client
  `rcvbuf`, and the timing anchor now travelling over the wire in the status stream.
  Good trade, because the network is measurable and has headroom, but not free.

This is exactly the "when do tactical fixes stop and architectural change start"
question that [HOST-CAPACITY-PLANNING.md](HOST-CAPACITY-PLANNING.md) leaves open —
answered with a measurement instead of a judgement call.

---

## What each phase buys

| Phase | Answers |
|---|---|
| 1 | Can we see the problem at all? |
| 2 | Is the floor clean, and does virtualization cost us? |
| 3 | What does each existing client cost? |
| 4 | **What is our capacity?** |
| 5 | How much can we buy back? |
| 5b | **Can this hardware reach zero loss — or must the architecture change?** |
| 6 | How do we stop anyone spending more than we have? |

Phases 1–2 are pure instrumentation and can run during normal operation. Phase 3
requires stopping clients one at a time. Phase 4 adds synthetic load and should run in
a quiet period. Phase 5 changes host configuration. Phase 6 is code.

**Do not start before the Perseid peak has passed** — Phases 3–5 all perturb a
production station.

---

*Written 2026-08-12 after the eclipse capture, from measurements taken that day on
bee1 and B4. See [`project_post_freeze_queue`] in operator memory for the tracked
work items.*

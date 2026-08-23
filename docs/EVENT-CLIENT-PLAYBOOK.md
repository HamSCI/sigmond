# Building an ad-hoc event client

> **Audience:** scientist, contributor
> **Status:** current
> **Verified against:** sigmond 4aec0c2 on 2026-08-23 — docs
> **Canonical for:** design judgment for an event/capture client

New here? Start at the [scientist guide](scientist/README.md) — this page is
required reading #1 there.

A playbook for standing up a receiving client on **short notice** — an eclipse, a
meteor shower, a volcanic eruption, an unannounced experiment.

This is the *design* companion to [ADD-A-CLIENT.md](ADD-A-CLIENT.md). That document
tells you which files to write for a conformant client repo; this one tells you which
decisions to make, in what order, when the event is tomorrow and there is no existing
receiver for the signal.

Distilled from the 14.110 MHz Costas client built in about a day for the 2026-08-12
eclipse, and from the operating history of `wspr-recorder`, `psk-recorder`,
`meteor-scatter`, `mag-recorder` and `hf-timestd`.

---

## When this applies

An event client is not a product. It exists because something is about to happen once,
at a known-ish time, and there is no existing receiver for it. It is judged on one
question: **did the data get captured?** Everything else — decoding, packaging,
elegance — can be fixed afterwards. That asymmetry drives every rule below.

If the thing you are building will still be running in six months, this is the wrong
document; build a proper component via [ADD-A-CLIENT.md](ADD-A-CLIENT.md). If it has to
work tomorrow, read on.

---

## Rule 1 — Capture first. Process later. Always.

Build the recording path, validate it against a known signal, and get it live **before**
writing a line of detector code. Store raw IQ, not decoded products.

**Why:** the event happens once. A detector bug found next week is recoverable from
archived samples; a capture that did not run is gone. On the Costas client the detector
was written last and *still* had a critical bug at first review — had it been on the
critical path, we would have shipped late or shipped broken.

## Rule 2 — Start from the client contract, not from the radio.

Read [CLIENT-CONTRACT.md](CLIENT-CONTRACT.md) before designing. Implement the mandatory
surface on day one: `inventory --json`, `validate --json`, `deploy.toml`, stdout
cleanliness, and the §7 multicast rule.

**Why:** the contract is not paperwork, it encodes fleet-safety invariants. Retrofitting
it is how you discover, late, that your client has been quietly degrading its
neighbours. Two concrete costs:

- Building `RadiodControl` **without** `client_id` puts your stream on radiod's *default*
  multicast group, so the kernel fans every packet to every peer client's socket.
- A `deploy.toml` whose discovery call finds no jobs makes `smd` report your running
  capture as absent.

Both were individually-correct code that was wrong at the integration seam.

## Rule 3 — Know your channel envelope before you design.

Everything you can ask radiod for is one call. Write the envelope down first, then choose
deliberately. Design conversations stall on "can we even get X?" — the answer is nearly
always yes, and knowing the knob set upfront turns a research question into a parameter
choice.

---

## The channel envelope

A client asks for a channel with exactly these characteristics, via one
`RadiodControl.ensure_channel()` call:

| Knob | Range / values | Notes |
|---|---|---|
| `frequency_hz` | anything the front end covers | Offset the dial deliberately if you want a known tone at a known audio frequency — invaluable for validation. |
| `preset` | `iq` `usb` `lsb` `am` `fm` `cw` | `iq` gives complex baseband and preserves absolute phase. Everything else discards it. |
| `sample_rate` | e.g. 12000, 24000, 96000 | Pick one where your symbol length is an integer number of samples. At 12 kHz a 40 ms symbol is exactly 480. |
| `encoding` | S16LE/BE, F32LE/BE, F16, µ-law, A-law, Opus | radiod honours the request. **Verify it on the wire** — see traps. |
| `low_edge` / `high_edge` | Hz, relative to centre | Be generous for a one-shot capture. A wide recording can be filtered later; a narrow one that excluded the signal cannot be widened. |
| `agc_enable` / `gain` | 0/1, dB | AGC **off** and fixed gain for science, so amplitude is comparable across the run. |
| `kaiser_beta` | filter window | Leave at default unless you know why. |
| `lifetime` | radiod frames | **Mandatory.** ~6000 ≈ 120 s. radiod cannot tell your process died; without it an orphaned channel streams to nobody forever. Refresh from a keepalive thread. |
| `destination` | — | **Never pass it.** Construct `RadiodControl(status_address, client_id="<your-client>")` and let the library derive a collision-free group (§7). |

---

## Budget the load before you choose your architecture

This is the decision most likely to hurt a neighbour, and it has to be made at design
time because it determines whether you process in real time or defer.

### The case that settles it

Starting `hf-timestd` added 9 metrology channels to B4's radiod (~36 → ~45). That was
enough to shift `wspr-recorder`'s RTP↔wall-clock anchor by **+2.0 s**, misaligning its
120 s integration windows: **zero WSPR spots for hours**. FT8/PSK on the same radiod were
unaffected. Restarting `wspr-recorder` did *not* fix it while the load persisted — only
shedding the load did. (A/B proven; see `reference_b4_wspr_vs_hftimestd_radiod_overload`.)

### Three durable lessons

1. **More cores will not save you.** radiod is limited by L1/L2 cache hit rate, not core
   count. The optimum is all of a radiod's threads on *one* hyperthreaded sibling pair;
   spreading them wider thrashes the cache and makes it worse. Do not prescribe affinity
   widening.
2. **One-shot anchors are fragile; per-slot re-anchoring is resilient.** That is precisely
   why WSPR died and FT8 rode it out. If your client must run alongside others, re-derive
   timing per slot or per segment rather than anchoring once at start.
3. **Post-processing bursts contend with acquisition.** Kicking off a decode the instant a
   file closes competes for USB/IRQ and CPU with the samples still arriving. Deferring —
   or rate-limiting — the processing is a load decision, not an optimisation.

### The practical rule

Record to disk, do nothing else while the event is live, process afterwards. The eclipse
capture costs ~3% of one core because it does exactly that. Choose real-time processing
only when the science genuinely requires acting *during* the event, and then budget it
explicitly against what is already running.

---

## Prove it against a known signal

Never trust an unvalidated chain. Before the event, record a signal whose answer you
already know and check that you get it.

The cheapest good test on HF: tune the dial **1 kHz below WWV** and confirm the carrier
lands at exactly **+1000 Hz** in the recording. That single check validates frequency
scale, sample rate, encoding, and that you are recording real signal rather than noise or
misinterpreted bytes — in one measurement.

That test caught a wire-format fault which had been reporting *"completeness 100.0%,
gaps 0"* while producing a 1.96×-rate stream of garbage.

---

## Assume every failure will be silent

Across the Costas build, eleven defects were found before deployment. **Not one threw an
exception.** Every one produced plausible, well-formed, wrong output. That is the dominant
failure mode of this kind of software, and it should shape how you write it.

- **Never let a `getattr(obj, "field", None)` default hide a missing attribute** on an
  object you do not own. That idiom silently returned `None` for a multicast address for
  hours.
- **Never silently coerce dtypes.** Casting complex samples to a real dtype drops the Q
  channel with only a warning. Raise instead.
- **Never let a status field substitute for a measurement** when you can measure.
  Bytes-per-RTP-tick tells you the true wire format; a reported field may be stale.
- **Never let a hint constrain a measurement.** An operator-supplied expected value must
  be a cross-check that warns on disagreement, never a search window — otherwise the tool
  confirms whatever was typed in.
- **Alarm loudly, then continue.** Capture-first means a fault should shout in the journal
  and keep recording, not exit — unless continuing would record garbage, in which case
  fail fast in the first seconds rather than the last hour.

---

## Unattended means watchdogged

A capture that runs overnight needs something outside it watching. `Restart=on-failure` is
**not enough**: the common stall is a stream layer waiting on packets that never come,
which never fails, never exits, and reports healthy while recording nothing.

Use an external file-growth watchdog — check the newest output file every 30 s and restart
the unit if it has not grown. Keep it out of the client's own code so a client bug cannot
disable it, and **test it by killing the live capture**. An untested watchdog is worse than
none, because it buys false confidence.

Make restart safe while you are at it: never overwrite an existing output file, and give
segment names a zero-padded sequence so lexicographic order always equals chronological
order.

---

## Record the timing anchor, not just the samples

Store, per segment:

- the RTP timestamp of the first sample,
- the UTC it corresponds to, from radiod's GPS reference — **not** the host clock,
- an explicit `anchored` / `unanchored` state.

Without that pairing, absolute alignment is unrecoverable and the archive is worth far
less. Prefer a standard container — SigMF is a raw blob plus a JSON sidecar, needs no
library to emit, and is directly shareable.

Make the offline analysis independent of where recording happened to begin: files do not
start on a second boundary, so acquire frame phase by **search** rather than assuming
alignment. Metadata may be used as a fast path but must never be able to constrain the
result to nothing.

---

## Beyond the essentials — features worth building in

The rules above are close to mandatory. What follows is graded advice: things a generic
recorder *should* offer, roughly in order of how much regret they prevent.

### Strongly recommended — each of these would have caught a real problem

**A signal-level heartbeat.** Log band power / RMS every minute or so.

This is the biggest gap in the client built for the eclipse, and it is worth being blunt
about: our health check is **byte growth**, and byte growth is *identical* whether the
antenna is connected, disconnected, or terminated into a dummy load. The recorder would
happily write 96 kB/s of pure noise all night and every monitoring line would read
healthy. A single periodic line —

```
level: rms=0.000056  peak/median=47.2 dB  band_power=-88.7 dBFS
```

— turns "is it running?" into "is it *receiving?*", and it costs almost nothing. Add a
loud warning when the level collapses or rails.

**Record what was *granted*, not just what was requested.** Metadata should carry the
channel parameters radiod actually delivered — measured where possible — alongside the
requested ones. When they differ, that difference *is* the interesting datum, and years
later it is the only way to know what the file really contains.

**A capture report at close.** Emit a small JSON/Markdown summary when the window ends:
duration, total samples, segments, gaps and their positions, timing state, disk used,
any alarms raised. It makes handoff to an analyst a link rather than a conversation, and
it is the natural artefact to attach to a write-up.

**Per-segment checksums.** A manifest with `sha256` per segment costs milliseconds and
detects truncated transfers, partial writes and bit rot. Without it, a corrupted archive
looks exactly like a good one until analysis fails confusingly.

**Idempotent everything.** Re-running any step must be safe. A packaging step that
*errors* when its output already exists — rather than skipping — turns a transient
backlog into a permanently failing daily unit. That is not hypothetical; it is a live
bug in `mag-recorder` today.

### Worth considering

**A dry-run / preflight mode.** Validate the job, resolve identity, compute projected
size, and log exactly what *would* be done — without touching radiod or the network.
`hs-uploader`'s PSWS transport has this and it is the right way to rehearse a path before
an event.

**Job files, not command lines.** A named TOML job in a jobs directory is a reproducible
artefact you can diff, review, commit and re-run. Shell history is none of those things.
It also gives the contract's discovery call something to enumerate.

**Human-friendly scheduling.** Accept `center_utc` + `window_sec` as an alternative to
`start_utc`/`stop_utc`. "±4 h around the maximum" is how people actually think about
events, and normalising it at parse time removes an arithmetic error class.

**Clock provenance.** Record *what disciplines the clock* you stamped with — GPS, chrony,
a timing authority, or nothing. `mag-recorder`'s timing sidecar does this well. It costs
one small file and makes the timestamps defensible rather than merely present.

**A cost estimator.** Print bytes/hour, projected total, and expected CPU before
committing to a window. Cheap to write, and it makes the load conversation concrete
instead of theoretical.

**Structured logs.** One machine-parseable line per periodic event (level, rate, gaps)
means post-hoc analysis of the *run* is a `grep` away, not an archaeology project.

**An explicit retention policy.** State what happens to data after upload or analysis —
kept, pruned, archived. Silence here is how disks fill during the next event.

### Nice to have

- **Multi-channel jobs** — one job describing several frequencies, if the science wants
  simultaneous bands. Weigh against the load budget above.
- **A post-event hook** — fire packaging or notification when the window closes.
- **Bounded run time** — a hard stop even if the stop time is misconfigured, so a
  fat-fingered job cannot fill a disk.
- **Live progress endpoint** — the runtime state file we added for contract §7 doubles as
  a cheap liveness probe for any external monitor.

### The meta-point

Notice how many of these are about **knowing whether the thing worked** rather than making
it work. That is the right instinct for one-shot capture. The failure you should design
against is not the crash — you will see the crash. It is the run that looks perfect and
contains nothing.

---

## Pre-flight checklist

- [ ] Channel characteristics chosen deliberately from the envelope table; filter edges generous
- [ ] `RadiodControl(..., client_id="<client>")`; no `destination=` passed
- [ ] `lifetime` set and refreshed by a keepalive thread
- [ ] Wire format measured, not assumed
- [ ] Known-signal validation passed (WWV at the predicted offset)
- [ ] Load measured against the running fleet; no neighbour degraded
- [ ] Multicast group verified unique among those joined on the host
- [ ] `inventory --json` and `validate --json` emit pure JSON and see the live job
- [ ] Disk pre-flight for the whole window, with margin
- [ ] Timing anchor present and reading `anchored` on a live segment
- [ ] Signal-level heartbeat logging, so a dead antenna is distinguishable from a live one
- [ ] External watchdog installed **and tested by killing the capture**
- [ ] Restart-safety confirmed: no overwrite, sortable segment names
- [ ] Window covers well before and after the event, plus a quiet baseline

---

## Station traps worth knowing

| Trap | Symptom | Do this |
|---|---|---|
| Stale encoding from `ensure_channel` | 2× sample rate, NaN or garbage, completeness still 100% | Measure bytes ÷ RTP ticks ÷ components; assert the measured format |
| Journal invisible to your user | `journalctl -u X` returns "No entries" for a busy service | Use `sudo journalctl`; non-`adm` users see nothing, with no error |
| Passive `metadump` as an enumerator | Different channel lists on successive runs | Do not conclude absence from it |
| Library version reporting | Reported version ≠ code actually running | Editable installs do not refresh dist-info on `git pull`; check `git describe` |
| One shared library checkout | Every client imports the same files | Touching it changes all of them at once — never during an event window |
| Timer never started | Unit "enabled" by preset but never fires | `enable --now`; `Persistent=true` does not back-fill a timer that was never enabled |
| Cross-user file access | Uploads fail with `rc=255` | Check the service user can actually read the key it is configured with |

---

## What "good" cost, for calibration

| | |
|---|---|
| Client source | ~2,900 lines Python |
| Tests | 226 |
| Runtime cost on the host | ~3% of one core |
| Data rate | 96 kB/s · 345 MB/h at 12 kHz complex float32 |
| Elapsed, idea to live capture | about one day |
| Defects caught pre-deployment | 11 — none of which raised an exception |

**The generic recorder is the reusable part.** It records a named frequency, with
specified channel characteristics, over a specified window, into timestamped files. A new
event should be a **new job file, not a new client**. Reach for a bespoke client only when
the channel envelope or the storage shape genuinely cannot express what you need.

---

*Derived from the `event-recorder` build of 2026-08-11/12 (AC0G/B4) and the operating
history of the clients above.*

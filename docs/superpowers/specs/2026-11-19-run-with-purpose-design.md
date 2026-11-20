# Run with purpose: each process runs because its input exists

19 November 2026.  Michael Hauan and Claude.  Status: design, awaiting Michael's approval of the whole document.

## Intent

A station should run a process only when that process has something to read, and should run it whenever it does.
Today a recipe decides: bring-up installs a profile's clients, and they stay on whatever the station holds.
Michael's two examples set the rule from both sides:

- **Off when nothing feeds it.**  AC1SN ran gmag-webui for days with no magnetometer on its bus.
- **On when something feeds it.**  SIGMA's RX888 hears the TS-1's BPSK-encoded PPS through a splitter and runs on
  the shared GPSDO, yet T6 stays off there, because the TS-1's USB plugs into B4.  T6 should arm whenever the HF
  input carries the pilot, whatever sits on the local USB.

## Goals

1. One sigmond step answers "does this input exist?" for every input a client depends on, and acts on the answer.
2. Two kinds of evidence count:
   - **a device on this machine's USB** (magnetometer, GPSDO): the client's own `inventory --json hardware_present`;
   - **a signal in the RF input** (the TS-1 pilot): a short probe that the owning client supplies, because the
     client owns the signal processing.
3. Present input switches its consumer on; absent input switches it off.  T6 arms itself when the pilot appears
   (Michael, 2026-11-19: "Arm itself.").
4. The cost of looking appears only while looking: no channel held open for a pilot that never comes.

## Non-goals

- Sharing evidence across the LAN (the TS-1 holder advertising it).  The sensed-bringup design proposes that; it
  can feed this step later as a second source.  Not built here.
- Choosing what a station installs.  Sensed bring-up (waiting on Rob) owns installation.  This design governs
  what runs among installed clients, on bring-up and afterwards.
- Granting timing authority.  Arming T6 starts measurement only.  T6 acceptance, unchanged, still decides
  whether T6 takes authority.

## Anti-goals (a failure even if it works)

- A probe that disturbs radiod or the running timing chain.  The probe never restarts radiod and never touches a
  running T6 channel.
- A station that switches a process on and off over and over because evidence flickers.
- A lost input that goes quiet.  A device or pilot that was present and has gone is a fault, raised loudly, not a
  silent switch-off.  (Memory rule: timing faults are exposed, never corrected.)

## Approaches considered

- **A. hf-timestd watches for the pilot itself**, holding a channel open at the alias frequency on every station.
  Simple ownership, but every station spends a channel and CPU on a pilot most never carry, which breaks the rule
  this design exists for.
- **B. Sigmond decides purpose for every input; each client supplies its detector.**  Chosen by Michael.  One rule,
  one place; Task 8 of v3.94 already builds its switch-off half for USB devices.
- **C. The TS-1's host advertises it on the LAN.**  No search, but SIGMA would depend on B4's service, and a TS-1
  with no USB link anywhere would never be found.  Kept as a later evidence source.

## The design

### Inputs, declared in the catalog

Each client entry in `etc/catalog.toml` may declare the inputs it depends on.  `hardware_gated` (the existing USB
label) stays and keeps its meaning.  A new field names an RF probe and what it switches:

```toml
[client.hf-timestd]
rf_inputs = [
  { name = "ts1-pilot", probe = "hf-timestd probe-pilot --json", switches = "t6" },
]
```

`switches` names a feature of the client, not the whole client: hf-timestd runs regardless; only T6 follows the
pilot.  The magnetometer and GPSDO gates switch whole clients, as Task 8 does.

### The probe contract

`<client> probe-<input> --json` runs for a bounded time (hf-timestd: at most 30 s), opens and closes its own radiod
channel through ka9q-python, and prints one JSON object:

```json
{"input": "ts1-pilot", "present": true, "frequency_hz": 45375000, "adc_hz": 129600000,
 "snr_db": 21.4, "seconds": 12}
```

`present` is tri-state: `true`, `false`, or `null` (could not look: radiod down, no channel slot).  Only a
confident answer acts.  hf-timestd's probe reuses its own pieces: `ts1_channel.resolve_t6_frequency_hz` for the
alias (transmit frequency from `FLEET_TS1_TX_HZ`, 84.225 MHz, on stations without the TS-1's USB), the BPSK
matched filter for detection.  *(Calls by Claude, from the builder check:)*

- **Fold length.**  The probe folds 60 s, as the recorder does; B4's pilot runs 48-57 dB-Hz after dark, and a
  shorter fold misses it.  It answers `false` only when a full 60 s fold finds nothing above the recorder's own
  lock threshold; anything weaker or cut short answers `null`.
- **Never a second channel.**  When `[timing.t6_pps] enabled` already reads true, the probe opens no channel at
  all.  It answers from the recorder's status file: `true` while locked, `null` otherwise.  This rules out an SSRC
  collision that could tear down the live T6 channel.
- **Which radiod.**  The probe runs where hf-timestd runs and uses the radiod hf-timestd's own config names.  On
  B1/B2 that means B2 probes the B1 radiod its recorder already consumes.  B1 is ours, not `locally_managed`.

### The purpose step

`lib/sigmond/purpose.py` evaluates every declared input of every installed, topology-enabled client and returns a
list of decisions: input, evidence (`present` / `absent` / `unknown`), current state, and the action that would
reconcile them.  Three callers:

- **bring-up**, after clients install, so a new station starts in the right state;
- **`smd align`**: the dry run lists the decisions; `--apply` carries them out (this absorbs Task 8);
- **`sigmond-purpose.timer`**, every 4 hours, which carries out only the arming direction automatically (below).

### What each decision does

| Evidence | Consumer now | Action |
|---|---|---|
| present | off | switch on: enable and start the client's units, or for T6 set `[timing.t6_pps] enabled = true` through sigmond's config render and restart `timestd-core-recorder` once |
| absent, never seen before | on | switch off (whole clients: stop and disable units; topology stays enabled) |
| absent, seen before | on | **fault**, raised in `smd check`, the drift report and the board; the consumer stays as it is for the operator to decide |
| absent (T6) | armed | never switched off automatically; reported (a fault if seen before, an info line if never).  B4 holds the fleet's only working T6 bench, and a recorder restart costs timing data. *(Claude's call.)* |
| unknown | either | no change; one info line |

"Seen before" means the client has produced output from that input at least once (Michael, 2026-11-20: "Agree").
Two sources, either one counts:

- the client's own past output: mag-recorder samples on disk, a gpsdo-monitor fix in its log or status, a T6
  lock in hf-timestd's anchor ledger;
- a small state file sigmond keeps per input (`/var/lib/sigmond/purpose/<client>.<input>`, written through
  `safe_write`), recording the last time the evidence read present.

The first source seeds stations that predate the state file, so the first run never mistakes a lost device for one
that was never there.  SIGMA's magnetometer, if absent, raises a fault; AC1SN's gmag-webui, which never saw one,
switches off.

### Flicker

The arming direction needs two consecutive `present` probes, at least 10 minutes apart (the timer re-probes 10
minutes after a first sighting rather than waiting 4 hours).  The switch-off direction applies only to inputs
never seen, so it cannot flap.

### The timer and Rob's rule

Since 2026-09-01 no hardware appearance starts anything by itself; `smd adopt` starts new things.  Michael chose
to arm T6 automatically.  The design keeps the departure narrow: the timer arms features of running clients only
(T6 today), never installs or starts a client, and the armed feature gains no authority until acceptance.
Switching whole clients on stays with explicit verbs (`smd align --apply`, bring-up).  Rob sees this on the bus
now; the timer is the plan's last task, so an objection from him removes it without touching the rest.
Arming writes both `enabled = true` and the alias `frequency_hz` (the template ships 0).

## Constraints

- Core stays stdlib-only; smd prints ASCII.  smd refuses sudo; root work goes through `_need_root`.
- The probe never restarts radiod.  `locally_managed` radiod hosts (Phil's) get no probe channel without the
  site profile's consent.
- Precise positions play no part.
- A recorder restart to arm T6 costs about a minute of timing data, once per change; the dry run says so.

## Left to the builder

- Where sigmond's config render writes `[timing.t6_pps] enabled` today, and how the sensed value overrides a
  profile default without hand-editing the station's config.
- The probe's channel parameters (bandwidth, preset) from hf-timestd's existing T6 channel.
- How the board shows a "seen before, now absent" fault (reuse the drift-report fault path).

## Delivery

Two repos: hf-timestd (the `probe-pilot` command) and sigmond (catalog field, `purpose.py`, align and bring-up
wiring, the timer).  It lands after v3.94, as v3.95, with B4, SIGMA and B1 as canaries: B4 holds the TS-1's USB,
SIGMA and B1 only hear the pilot.

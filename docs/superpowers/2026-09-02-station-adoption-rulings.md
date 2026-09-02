# Station adoption — decisions taken during implementation

Companion to `specs/2026-09-02-station-adoption-design.md` and
`plans/2026-09-02-station-adoption.md`. Those two say what we meant to build.
This one records where the build DIVERGED from them, and why — every judgment
call made while executing the plan, with what each costs if it turns out wrong.

Read it when the code and the plan disagree: the answer is almost always here.

Implementation ran 2026-09-02 as eight TDD tasks, each written by one agent and
graded by another, with a whole-branch review and a single fix wave at the end.
25 commits, `cb1b389..40641d8`. The suite went from 2085 to 2229 passing.

## The pattern worth remembering

Six of the findings below are the same defect wearing different clothes:
**something reported success it had not earned.** A propagation model
fabricating delays, a verb announcing it starts services and only printing the
command, a preview naming components it never started, a status section
advertising an offer the next command refuses, a roster whose placeholder IDs
are shaped exactly like real ones, and a dry run predicting something the box
could not say. The plan's own tests missed several because an assertion said
`>=` where it meant `==`.

If you change this code, the question to keep asking is not "does it work?" but
"if this silently did nothing, would anything tell me?"

---

## The rulings, in the order they were made

### Ruling: FINDING 1 — the plan's `bin/smd` line numbers (≈5419, ≈8507) are HINTS, not

### Ruling: FINDING 2 — `config/dasi2-roster.toml` ships with clearly-labelled PLACEHOLDER

### Ruling: the plan's own Task-4 test — a fixed 900-character source-text window scanned for the absence of `return 1` — is REPLACED with a behavioural test (monkeypatch `_detect_local_sdr` to False, call the bring-up path, assert it does not early-return and that the warning reaches stdout). — Why: the finding is plan-mandated, so I weigh it against the plan text; the SPEC is the binding authority and its §7 asks for tests that predict what the box will actually say. A window scan passes against an implementation that keeps the abort and merely moves it, and it already forced an inert padding comment — the test was steering the implementation instead of verifying it. — Cost if wrong: a slightly larger test than the plan drew, exercising a real CLI entry point; if the monkeypatch surface proves awkward the fix round will show it immediately.

### Ruling: the stale TUI hard-stop framing IS fixed inside Task 4, though the brief scoped only `bin/smd`. — Why: `greenfield.py` still tells the operator that a missing RX888 makes Begin "exit immediately, whatever flags are passed". That is now false, and the TUI is the primary guided path — it would tell an operator that the exact install this entire plan exists to unblock will fail outright. Leaving it is shipping a UI that lies about this plan's deliverable. It belongs with the change that made it stale. — Cost if wrong: Task 4's diff grows by two string literals and one renamed test class, in files no other task touches.

### Ruling: ACCEPT the implementer's decision to WIRE `_adoption_section` into `cmd_status`, though the brief's Step 3 only defined the function. — Why: a status section that is never rendered is dead code, and the task's whole purpose is that a station sitting on unadopted hardware says so out loud. A tested-but-unrendered function would satisfy the letter of the brief and none of its point. — Cost if wrong: `cmd_status` gains a read-only section one task earlier than the plan drew it.

### ⛔ Ruling (LOAD-BEARING — plan defect found in implementation, verified by controller): `sigmond.sources.inventory()` PROJECTS NO USB DEVICES. I read `lib/sigmond/sources.py:311-315`: the omission is deliberate and documented — "usb_sdr probes emit kind='sdr'; we'll wire those when the USB iSerial reading is reliable across hosts. Phase 2 ships without USB selection." Consequence, which the plan did not see: `_station_inventory` derives `sources` ONLY from discovery observations, so on the live Fargo box hardware={rx888,gpsdo} but sources=(), and `offers()` returns [] on an empty `remaining`. The station would render NOTHING adoptable while an RX888 and a miniGPS sit plugged into it — while Task 8's dry run asserts two offers and passes, because it hand-builds the `sources` tuple. The dry run would predict something the box cannot say. That is the exact failure this project has been chasing all session: silence presented as data. DECISION: `_station_inventory` in `bin/smd` SYNTHESISES `usb:` SourceKeys for locally detected hardware and unions them with the discovery-derived rows. Task 6 owns this, since it owns that helper and will consolidate Task 5's `_adoption_inventory` into it. — Why this and not the alternative: wiring USB projection into `sources.inventory()` would overturn a deliberate deferral in a SHARED module that other clients consume, and the stated blocker (iSerial reliability across hosts) is real and unsolved. Keeping the synthesis local to the adoption path changes nothing for existing consumers. — Cost if wrong: identifiers must be stable per host. Where iSerial is unreadable the fallback is `vid:pid`, which collides if a station ever has two identical devices; that limitation gets stated in the code and the report rather than papered over.

### Ruling: the "degrade quietly" Minor is pulled INTO the fix round rather than deferred. — Why: minors normally stay out of the loop, but this one guards a requirement I named as binding, whose failure mode is `smd status` crashing on an operator's machine. The reviewer confirmed it only by reading the code; a guarantee nothing tests is one a future edit removes silently by narrowing the `except`. — Cost if wrong: one extra regression test in a task that was otherwise done.

### Ruling: ACCEPT the implementer's extension of my USB ruling — synthesis reads `/sys/bus/usb/devices`, not `lsusb -v`. — Why: it improved on my ruling with a fact I did not have. `lsusb -v` hides iSerial from non-root, so an unprivileged `smd status` would print `usb:04b4:00f1` while the root `smd adopt` (post `_need_root` re-exec) would refuse the very name status had just printed. Sysfs is world-readable and yields the same key at both privilege levels. — Cost if wrong: sysfs layout is Linux-specific, which this appliance already is.

### Ruling: ACCEPT widening synthesis beyond the SDR to GPSDO and magnetometer. — Why: Task 8's dry run asserts Fargo offers TWO devices including `usb:1dd2:2211:mini01`; SDR-only synthesis would have left the dry run predicting something the box could not say — the same defect I ruled on, one device over. — Cost if wrong: none identified.

### Ruling: ACCEPT the `_adopted_sources()` glob fix. The implementer found a SECOND instance of the same class: `plan()` names ka9q-radio / ka9q-web / igmp-querier, none of which is in `sources.KNOWN_CLIENTS`, so `load_all_selections()` never read back what adopt wrote and an adopted device would be re-offered FOREVER — making the stable identifiers I insisted on pointless. Globbing `*.sources.toml` closes it. — Cost if wrong: reads selection files for names outside the known set, which is the intent.

### ⛔ Ruling (LOAD-BEARING — the plan contradicts its own spec): **`smd adopt` must actually perform the adoption.** The brief's `cmd_adopt` records the selection and then PRINTS "run `smd install ...` then `smd start ...`" — while its own docstring says "Adopt an offer: configure, enable and start what it brings." Spec §6 is explicit: "`smd adopt <kit-or-source>` composes the existing machinery: `sources.add`, then install → configure → enable → start", and §8 calls step 4 "the first that starts anything, and only when asked." A verb that announces it starts things and only prints a copy-paste line is the session's recurring failure in a new dress — and the TUI copy Task 4 rewrote now points operators at `smd adopt` as the remedy for a dormant install. DECISION: adopt COMPOSES the existing `smd` install/enable/start machinery — it invents no lifecycle of its own — and it acts only after showing the plan and getting an explicit confirmation; `--dry-run` keeps the print-only behaviour, and a non-interactive flag covers scripted fleet provisioning. — Why confirmation rather than straight execution: "adoption is explicit" is the constraint written in blood on 2026-09-01, when a unit nobody asked to run took the timing chain down twice in one day. Showing what will start and waiting for a yes satisfies the spec without repeating that. — Cost if wrong: adopt gains an execution path in a task that had none, and that path starts services; it is gated behind confirmation, composes reviewed machinery, and gets its own scoped re-review.

### Ruling: `plan()` must stop conflating "components to ENABLE" with "clients that RECORD a source selection". Writing `igmp-querier.sources.toml` records a selection for a component that consumes no source. Keep `_HARDWARE_COMPONENTS` as the enable list; record selections only for entries that actually consume a source. — Why: a selection file for a non-consumer is a fact nobody reads, and this project has spent a day on what happens when a stored fact nobody reads gets believed later. — Cost if wrong: one fewer file written.

### Ruling: ACCEPT leaving `install` out of `cmd_adopt`, against spec §6's literal "install → configure → enable → start". — Why: read whole, the spec's own §3 rule is that install ALREADY ran everywhere ("every component installs present-and-dormant on every station"), so adopt has nothing to install; and `cmd_install` for ka9q-radio is a multi-minute apt + native `make install`, which a `--yes` fleet path would trigger unattended across twenty machines. `cmd_start`'s existing gate warns per component where one is genuinely absent. — Cost if wrong: adopting on a station whose install was incomplete warns instead of repairing; adding the call is one line inside the same lock.

### Ruling: RATIFY the implementer's amendment of the plan document. It changed Task 3's and Task 8's `>=` component assertions to `==` and added `source_kinds` to five Task 8 fixtures, then flagged the edit and invited me to revert it. I read the diff myself (`git diff c3323ce..30b46bd -- docs/.../2026-09-02-station-adoption.md`): every change is a TIGHTENING or the addition of the now-required field. Nothing was loosened. — Why this is right and not an implementer rewriting the plan to suit its code: Task 8's brief written verbatim would now FAIL against correct code, and the obvious way to make a failing brief pass is to widen the code back to the Critical defect. It corrected the plan instead, and said so. That is the judgment I want. — Cost if wrong: the plan document diverges from what I wrote; the diff is small, in git, and I have read it.

### ⛔ Ruling (Important 1, load-bearing): the KIT path writes EVERY unadopted source into EVERY consumer's selection file — `offers()` sets a kit's `sources=remaining`, i.e. all of them, and `cmd_adopt` loops components × sources. The reviewer ran it: `mag-recorder` ends up told the RX888 is one of its sources, `ka9q-radio` told another station's LAN radiod is. Second-order harm is worse than the first: that swept-in LAN radiod is now "adopted", so it disappears from `smd status` forever without ever being configured for anything — silence presented as data, again. FIX: route each key by `inv.kind_of(key)` → `_HARDWARE_COMPONENTS` ∩ `SOURCE_CONSUMERS`. The machinery exists as of round 2. — Cost if wrong: three selection files nothing currently reads; but `SOURCE_CONSUMERS` was added on the premise they will.

### ⛔ Ruling (Important 2, load-bearing — SPEC GAP in my plan): `smd status` advertises every `radiod:` offer with "run 'smd adopt <name>'", and `cmd_adopt` then refuses it — no component claims a non-`usb` source. That is the NO-LOCAL-HARDWARE station, one of the two situations spec §1 names as forcing this whole design, and §2 is explicit: "A site runs no SDR; radiod lives on another machine on the LAN. sigmond should find it and offer the clients that can consume it." My `_HARDWARE_COMPONENTS` never mapped a remote radiod at all. FIX: a `radiod:` source maps to `sources.KNOWN_CLIENTS` (wspr-recorder, psk-recorder, hfdl-recorder, codar-sounder) — sigmond's OWN existing answer to "who consumes a radiod", reused rather than duplicated into a second list that would drift. The confirmation prompt shows exactly what will start, so the operator sees the set before consenting. — Cost if wrong: adopting a LAN radiod offers up to four recorder clients; `cmd_start`'s existing gate skips any that are not installed.

### Ruling (Important 3+4): move `_need_root` above the prompt — today an unprivileged operator is asked to confirm, says yes, and the sudo re-exec asks again. And booby-trap `_need_root` in the dry-run / decline / no-TTY tests instead of stubbing it False unconditionally, so nothing can silently move elevation onto a read-only path — the very property that keeps `adopt` out of `_MUTATING`. — Cost if wrong: none identified.

### Ruling: fold Minors 1 and 2 into the same round. Minor 1 — `cmd_adopt` lacks `cmd_status`'s degrade guard, so a hand-edited `*.sources.toml` or a missing roster gives the operator a TRACEBACK where status silently drops the offer; that is the same class as the refusal-not-a-traceback constraint already binding this task. Minor 2 — adopt prints "✓ adopted" when `cmd_start` started nothing because a component is not installed; reporting a success that did not happen is the failure this project keeps finding.

### Ruling: ACCEPT sudo-before-prompt on the declined path. Moving `_need_root` above the prompt necessarily means an operator who answers `n` has already been elevated. — Why: `cmd_bringup` already behaves exactly this way, so it is the house pattern rather than a new surprise; the cost is one wasted password entry, never a wrong action. The clean alternative — carrying consent across the sudo re-exec via an env var — would change `_need_root`, which EVERY verb shares, to fix a cosmetic wart in one. Not worth the blast radius. — Cost if wrong: an operator types a password and then declines.

### Ruling: ACCEPT the `offers()` change (beyond the letter of my instruction). A kit now claims only sources with a local hardware kind. — Why: routing alone would have left a swept-in LAN radiod matching no component — never recorded, never adopted, and re-offered as `dasi2` forever because `recognise()` still matched. It closes the same silent-loss hole my Important-1 ruling was aimed at, one layer up, and stays pure. — Cost if wrong: a kit offer no longer sweeps in non-local sources, which is the intent.

### ⛔ Ruling: the radiod-restart disclosure MUST also print on the `--yes` path. The implementer implemented it interactively only, reasoning that a fleet run "has nobody to warn". — Why: `--yes` means do not ASK me, not do not TELL me. An unattended fleet run that silently bounces a running radiod is precisely the 2026-09-01 failure, and the one artifact that survives an unattended run is its log. The disclosure costs one line and is the only trace the operator would have afterwards. — Cost if wrong: one extra line in fleet provisioning output. I also accept "disclose, don't predict": the restart fires only when the computed placement differs from both the drop-in on disk and the live affinity, and reproducing that decision would mean a second copy of `assign_radiod_cores` that drifts. A stale "no restart expected" would be worse than no claim — that is the right instinct.

### Ruling: RESUME the original implementer for round 4 rather than escalating to a fresh one as the process prescribes for rounds 4-5. — Why: that rule exists because a loop surviving three resumes usually means the implementer cannot see its own problem. That is not this. Each round addressed real defects newly surfaced by review — the implementer found the round-2 Critical ITSELF and has asked the right question every round. It is already on the most capable tier, so "one tier above" has nowhere to go, and fresh eyes would discard deep context on the plan's most intricate task. — Cost if wrong: a round spent by an implementer that has stopped seeing its own blind spot; the scoped re-review is the net.

### ⛔ Ruling (NEW Important, introduced by my own round-3 ruling): adopting a LAN radiod can start — and bounce — the LOCAL radiod, undisclosed. Round 3 makes a `radiod:` offer plan `KNOWN_CLIENTS`; all four of those clients declare `requires = ["ka9q-python","ka9q-radio"]` in `etc/catalog.toml`; `cmd_start` runs `_expand_requires_closure`, pulls in `ka9q-radio` (enabled on essentially every station, since install implies enable, and not hardware-gated so `dormant_reason` will not stop it), and reaches `_ensure_radiod_affinity_drop_ins` — the exact code the round-3/4 disclosure exists to warn about. And the disclosure does NOT fire, because it gates on `startable`, derived from `plan_.components`, which for this offer is the four clients and not radiod. Before round 3 the path was unreachable: a LAN radiod planned `()` and adopt refused. So my own fix opened a route to the 2026-09-01 failure — starting a unit nobody named — and silenced the warning built to catch it. FIX: compute BOTH the start preview and the radiod check over the REQUIRES CLOSURE, not over `plan_.components`. — Cost if wrong: the preview and the disclosure widen to what will actually start, which is what they always claimed to describe.

### Ruling: status must never advertise an offer adopt would refuse. `sources.inventory()` also emits `kiwisdr:` keys; those get no kind and no `radiod` branch, so finding 2's status-promises-what-adopt-refuses disagreement survives for that key type. Resolution: keep SHOWING every detected-but-unadopted device — silence is the enemy this section exists to fight — but print the "run `smd adopt`" instruction only for offers adopt would accept, and say plainly why the others cannot be adopted yet. — Cost if wrong: a KiwiSDR shows as detected with an explanation instead of an instruction that fails.

### Ruling: promote the previously-deferred `--dry-run` rc mismatch into this round. The empty-plan refusal sits AFTER the dry-run return, so `smd adopt <x> --dry-run` exits 0 for an offer a real adopt refuses with 1 — and that is now load-bearing, because it is what makes `test_every_offer_status_advertises_is_one_adopt_accepts` weaker than it reads (it proves only that the name resolves). A dry run must predict its own exit.

### Ruling: fold in `_radiod_already_running()`'s missing timeout and `FileNotFoundError` guard. It calls `_run(['systemctl', ...])` bare, so on a host without systemd it tracebacks out of `cmd_adopt` AFTER elevation. `harmonize._unit_active` already uses `timeout=10` for the same call — match it. Same blast radius as the disclosure it serves.

### Ruling (Important, CARRIED INTO TASK 8's dispatch rather than a 6th round): `startable` lacks `cmd_start`'s enabled-in-topology filter, and it feeds the consent prompt and the "started …" line as well as the warning. On the no-local-SDR station — the design's own §2 headline case — `ka9q-radio` is not installed, `_adopt_start_preview` calls it startable, `cmd_start` silently drops it at the topology filter, and adopt then reports "started ka9q-radio" for a component it neither enabled nor started. That is not a corner: it is the DEFAULT on that station class, and it directly contradicts the honest-reporting property round 3 established. Smallest change, named precisely by the reviewer: keep closure-ADDED names (those not in the offer's own components) only if they are in `_enabled_components(_load_topology(TOPOLOGY_PATH))` — two lines, leaving the loud direction intact for anything the operator actually named. — Cost if wrong: the preview under-reports a component that would have started; the radiod disclosure is unaffected, since it is separately gated on a live probe.

### Ruling (Minor, also carried into Task 8): the drift finding 2 closed on `smd status` survives a third time inside `cmd_adopt` itself — `bin/smd:8925` builds its "available" list from every pending offer, including ones the very next invocation refuses. One `components_for` call from consistency. Fixing it where the other two surfaces were fixed keeps all three reading the same function.

### Ruling (Minor, PARKED): `--dry-run` still cannot predict the "nothing here can start" and no-TTY rc=1 exits, and is the only path that never prints the skip list or the radiod disclosure — while the no-TTY message tells the operator to use `--dry-run` "to see the plan". Real, and the thinner plan is a genuine wart. Parked because moving the remaining refusals above the dry-run return reorders a consent path I have already had rebuilt twice this task, and the cap is the wrong moment to do that. Worth a follow-up.

### Ruling (Minor, PARKED): a declined prompt returns 0. Correct as it stands — declining is the successful completion of what the operator asked for, the output says "declined — nothing started" plainly, and a non-zero would make an interactive decline read as a failure inside a shell `&&` chain. Scripts use `--yes` and get a real outcome.

### ⛔ Ruling (Critical): `identity_from_manifest` takes `manifest["dasi2_site"]` at face value. Reviewer traced it: a manifest saying `dasi2_site = true` on host `fargo-1` returns an identity CLAIMING DASI2 membership, on a name that does not match `DASI\d{3}`. That is the silent promotion the global constraint forbids — "membership comes from the hostname, never from an operator answer", and a manifest is close kin to an operator answer. Latent today (nothing wires the two together yet) but it is the public contract the next caller inherits. FIX: when the manifest asserts `dasi2_site`, require `_DASI_NAME.match(hostname.strip())` and refuse loudly on mismatch. The PROMOTION half needs no roster — only the regex already in the module. The DEMOTION half (a rostered DASI host whose manifest says false) genuinely does need the roster and stays out of this signature; that is a fair line. — Cost if wrong: a hand-edited manifest that lies about membership is refused instead of believed.

### Ruling: fix the `.strip()` asymmetry in BOTH functions rather than one. The reviewer is right that normalising `StationIdentity.hostname` differently depending on which constructor made it is worse than being uniformly flawed. `identify()` already strips to build its lookup key, so storing the stripped value is the coherent choice. — Cost if wrong: a stored hostname loses padding nobody wanted.

### ⛔ Ruling: implementing the full `configure` step (spec §6's third verb) is OUT of this wave. — Why: `_apply_sources_to_wspr_recorder` exists but `bin/smd:12117-12124` states psk / hfdl / codar "are not yet multi-source aware", so genuinely configuring a LAN radiod's four recorders is new design, not wiring — and this is the final gate, not the place to grow the plan by a task. — What ships instead: adopt's copy stops claiming it configures, and it PRINTS the next step it cannot perform. Honest and small. — Cost if wrong: the no-local-hardware station still needs one operator command after adopting; it is told so plainly rather than left to discover it. Surfaced to Michael as the top follow-up.

### Ruling: PROMOTE three deferred items into the wave on the reviewer's triage — the conftest venv-re-exec guard (its failure mode looks like the suite ending early, not like a failure, and a 19th loader file is one commit away), the `_MUTATING` comment (forgetting it hangs the only verb that starts services), and the roster sentinel. Accepting all three; the reviewer's reasoning on each is better than my original deferral.

### Ruling: RE-OPEN the parked `recognise()`-superset item, at the level of one comment. — Why: my park reasoned "Task 1's superset behaviour was deliberate and reviewed", but that review happened when adopt only PRINTED. It now starts services, and the kit branch of `_hardware_for` returns `inv.hardware` unfiltered, so a fourth hardware kind would join the kit's start list without anyone touching `DASI2_KIT`. The file warns the next editor everywhere else; this is the one dangerous spot that does not.

### Ruling: CLOSE the item I had carried to Michael about `_ensure_radiod_affinity_drop_ins` bouncing a running radiod — the round-3/4 disclosure now covers both paths and its tests are real. Confirmed by the whole-branch reviewer.

### Ruling: move `config/dasi2-roster.toml` to `etc/`. This repo's shipped data convention is `etc/` (`etc/catalog.toml`, `etc/templates/`, `etc/*.example.toml`); one file in a new top-level sibling is what a future packaging step misses.

### Ruling: PARK the one-directional B3 sentinel. Landing real PSWS IDs while leaving `[_meta] placeholder = true` fails no test — it would emit a false placeholder warning forever. — Why park: the error direction is safe. A stale sentinel NAGS; a missing one is silent, and silence is the failure mode that motivated the guard. The file header instructs deleting the block in the same commit as the real IDs. Tying the sentinel to the ID values would mean teaching the repo what a real PSWS ID looks like, which is knowledge it should not hold. — Cost if wrong: a spurious warning on every load until someone deletes six lines.

### Ruling: PARK the one-closure-member over-report. The consent prompt, the radiod warning and the success line use `startable`, while `cmd_start` receives `to_start` and re-derives its own closure — so if every plan component pulling in a given closure member is skipped while an unrelated one remains startable, adopt names a component it did not start. — Why park: the error is in the SAFE direction (it starts less than promised, never more), the re-reviewer could construct no shipped offer shape that reaches it, and this is the final gate. — Cost if wrong: one over-named component in a message, on a path nobody has been able to produce.

### Ruling: PARK the dead `_smd_source()` helper in `tests/test_bringup_ungated.py:25`, orphaned when D3 removed its two callers. Cosmetic.
---

## Still open, deliberately

- **`smd adopt` does not configure.** Spec §6 says "install → configure → enable
  → start". `install` was ruled out (it already ran everywhere). `configure` is
  absent because `psk-recorder`, `hfdl-recorder` and `codar-sounder` are not yet
  multi-source aware — see `bin/smd:12117-12124`. Adopt records the sources,
  starts the clients, and PRINTS the `smd admin sources apply` step it cannot
  perform. A LAN-radiod station therefore runs four clients that are not yet
  pointed at that radiod until someone runs that command. **This is the top
  follow-up.**
- ~~**The roster ships placeholder PSWS IDs.**~~ **CLOSED same day.** Michael
  supplied the real ids for DASI002–DASI005; they and the `[_meta]` sentinel
  came out together, as the ruling required.

  Landing them exposed a modelling error worth recording: the roster carried a
  single `psws_instrument`, but a DASI2 site reports a **GRAPE/HF instrument
  AND a magnetometer instrument** under one station id. `sigmond.site_profile`
  had already settled that shape — a per-recorder `psws_instruments` map with
  `instrument_for(recorder)` — and the roster had invented a narrower one
  beside it. It now speaks the same vocabulary, so this codebase holds one
  model of PSWS identity rather than two that disagree.

  The roster is also a **closed list of four**, not twenty: only stations whose
  ids are actually defined appear, so `DASI001` and `DASI006+` are refused
  until someone adds them. That refusal is the feature.

  No grid in the roster, deliberately: PSWS requires one at registration, but
  the LBE-1421 overwrites it on deployment and `sigmond-location-check`
  re-asserts the GPSDO position from then on. A grid here would be a stale fact
  waiting to be believed.
- **Manifest push/pull, RAC registrar selection, and ssh access to PM and VM**
  belong to the separate PM-side spec. Nothing here implements them.
- **The demotion direction** — a rostered DASI host whose manifest claims
  `dasi2_site = false` — needs the roster inside `identity_from_manifest`, which
  does not take one. Left to whichever caller eventually holds both.

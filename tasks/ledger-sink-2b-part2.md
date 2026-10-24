# SDD ledger — plan: /home/mjh/hamsci/repos/sigmond/tasks/plan-sink-2b-part2.md

Spec: sigmond/tasks/spec-sink-2b-part2.md (binding; Michael's answers at its end).
Preflight written 2026-10-23, read-only on code.  The bus held nothing from Rob on these repos (newest message 20261023T122552Z, mjh's own heads-up).

## Bases
- hs-uploader sink-2b-part2 at 29a0806 (as planned).
- sigmond sink-2b-part2 at ebd6b19.  The plan expected be91e22.  `git diff be91e22 ebd6b19` shows only tasks/ files: the plan itself (d973e25) and a four-line spec edit (answer 5).  be91e22 stays an ancestor, so Task 16's "part 1 render at be91e22" and Task 27's range still work.
- sigmond-appliance sink-2b-part2 at 734afa3 (as planned).
- Clients, on main, no branch: wspr-recorder b523524, psk-recorder 84e81aa, meteor-scatter 6156e53, mag-recorder 55a450c.  Plan anchors checked: wspr shim `WSPR_USE_HS_UPLOADER` read at hs_uploader_shim.py:243-259 and `WsprCycleSource(` at :735-746 hold; psk `_resolve_delivery_pipelines` at recorder.py:206 holds.  All four client venvs import `hs_uploader` from /home/mjh/hamsci/repos/hs-uploader/src/hs_uploader (editable).
- The spec's own anchors (sigmond 10eca2f, psk 0bb62df, wspr d171d24) are older; the plan's are the right ones.

## Baseline suite counts (Task 1 step 3), at the bases, before any code change
| Suite | Command | Result |
|---|---|---|
| hs-uploader | `.venv/bin/python -m pytest -q` | 805 passed, 1 skipped (20 s) |
| sigmond | `.venv/bin/pytest -q` | 4938 passed, 1 warning, 200 subtests (3 min); no failure, no flaky test seen |
| wspr-recorder | `.venv/bin/pytest -q` | **BARE RUN FAILS: 20 collection errors, `ModuleNotFoundError: No module named 'hamsci_dsp'`** (the venv lacks hamsci-dsp; pyproject wants it from ../hamsci-dsp, editable). With `PYTHONPATH=../hamsci-dsp/src .venv/bin/pytest -q`: **510 passed, 26 subtests**.  The venv is untouched (no uv sync). |
| psk-recorder | `.venv/bin/pytest -q` | 316 passed, 30 subtests |
| meteor-scatter | `.venv/bin/pytest -q` | 559 passed, 1 skipped, 30 subtests |
| mag-recorder | `.venv/bin/pytest -q` | 165 passed |
Not run: sigmond-appliance static `test-*.sh` (plan runs them per task).
Use the wspr-recorder count 510 (with the PYTHONPATH) as its baseline; see ruling C2.

## Michael's answers, 2026-10-23 (spec end)
1. The psk rider checks the switch at ship time only: a client raised from fill to upload ships what it held.
2. A client installed after the carry-over gets `upload` under the site switch.
3. The B4 bench runs the short form: 2 h fill, 1 h off, 30 min rider test, until v3.79.
4. The one-time restarts of recorders on old writer code are accepted, one at a time, never radiod, announced to Rob on the bus beforehand.
5. Rollback without the gate moves the marker aside (replaces §10's "marker stays" row): when the probe lacks `gate`, the manifest step moves the marker aside, so a later return of the gate re-runs the carry-over from the current `[uploads]` and an operator's order given meanwhile survives.

## Plan rulings
Rulings 1-19 of the plan ("Rulings this plan makes on the spec", plan lines 90-112) are adopted as written, by reference, except where a controller ruling below amends one.  Ruling 10 is now confirmed by Michael's answer 5; its sentence "Michael should confirm" is stale.

## Controller rulings
- C1: Work on branch sink-2b-part2; merge to main only on Michael's go, after the rig.
- C2 (from the preflight): wspr-recorder's baseline and every later wspr-recorder run use `PYTHONPATH=../hamsci-dsp/src .venv/bin/pytest -q`, 510 passed.  No `uv sync`, no venv edit.  Task 11's "match its Task 1 count" means 510.  Cost if wrong: none.
- Further controller rulings, X1-X16, are proposed below; the controller adopts or amends each.

## Preflight scan
Scan of the plan against the spec, 2026-10-23, read-only.  Code anchors were checked at hs-uploader 29a0806 and sigmond ebd6b19 where noted.

### A. Pairs that share a file or an interface
| Pair | Producer / consumer | Agree? |
|---|---|---|
| global / C1 | Plan: push per task after review (GC, bus section); Task 27 step 3 pushes "as backup" once at the end. | Minor NO (X14) |
| T1 / base | Plan expects sigmond be91e22; branch sits at ebd6b19. | Yes, tasks/ only (see Bases) |
| T2 / T3 | `marker_stands` (T2) consumed by gate rule 3 (T3). `_atomic_write` extracted from `write_switch`. | Yes; but T2's symlink test vs `O_EXCL` (X1) |
| T2 / T5 / T7 / T8 / T9 | `marker_path()`, `marker_stands`, `write_marker` used by the daemon (T5/T6), writer (T7), rider (T8), CLI (T9). All read `HS_UPLOADER_STATE_DIR`; same default as `watermark.sqlite.default_path` (checked, sqlite.py:120). | Yes |
| T3 / T4 | `SinkGate.may_send` is the `gate` callable of `Uploader`. | Yes |
| T3 / T6 | T3 gives `decide` and no state; T6 adds a per-pipeline last decision and fault clocks to the same class.  The pump runs on threads (core.py:389-393). | Yes, but the state needs a lock (X8) |
| T3 / T5 / T6 | Ruling 4: gate holds no marker cache.  T5 wants "the gate first sees the marker appear". | NO (X3) |
| T4 / T6 | T4 holds without logging; T6 logs transitions in the gate.  Both edit nothing in common except `daemon.run` (T6). | Yes |
| T5 / T6 | T5 edits `Rebuilder`; `Rebuilder.due()` watches only `pipelines.d/` (daemon.py:572-590), and the gate is built in T6's `run`.  T5's marker-appearance test needs T6's wiring and a per-pump hook. | NO (X3) |
| T5 / T12 | Run 3 (T26) says the tool prints each key's starting record; T12 never says the tool calls `write_starting_records`. HELD says "wrote no record". | NO (X9) |
| T5 / T10 | Harness drives the daemon's `Uploader`; the starting record is not in the harness list. | Yes (T5 has its own walk test) |
| T7 / T7 sigmond | hs-uploader `sink/writer.py` is copied by recipe into `_bundled.py`; the compat test compares against `../hs-uploader` HEAD.  Any later hs-uploader commit that touches `writer.py` breaks sigmond's compat test. | Yes, with a rule (X11) |
| T7 test 9 | One bullet mixes "bundled copy matches" (a sigmond test) with the ImportError cases (writer tests). | Minor (X11) |
| T8 / T6 | T8 passes `sinks_dir` through `pipeline_factory`; the factory has no sinks dir today; only `Rebuilder` (daemon.py, `self.sdir`) has it.  T8's file list omits `daemon.py`. | NO (X7) |
| T8 / T12 | Tool's expected rider batch (T12) is computed from its own reader, so it does not depend on T8's code. | Yes |
| T9 / T15 | T15 captures `sink status --json --sinks <tree>`; `sink status` takes `--manifest` and `--sinks` (cli.py:143-146) but no `--marker`; T9's interface lists none. | NO (X5) |
| T9 / T3 | `held_pipelines` in the JSON must come from `SinkGate` (one implementation).  The JSON's `effective` and `held_by` must not depend on the marker, or sigmond's `below_upload` reads nothing before the marker stands. | Unstated (X5) |
| T9 / T2 | `write_switch` already takes `sinks/.lock` (sinkswitch.py:258-262).  T9's lock test is satisfiable. | Yes |
| T9 / T14-T20 | sigmond calls `sink set` and `sink mark-carried`; GC 4 says every parser of hs-uploader output takes real fixtures.  T15 captures only `status --json`. | Gap (X6) |
| T10 / T3 | Mutation 5 of T10 ("accept `governed = false` from any origin") needs a harness case with `governed = false` in a client file.  The assertion list has none. | NO (X10) |
| T11 / T2-T10 | Word `gate` last of the promised tasks.  `FEATURES` sorted gives `gate pipelines.d send-log-v3 switch-files`, which matches T23's expected line and the print code (uploader.py:429-430 joins sorted words). | Yes |
| T11 / clients | Client suites run against the branch hs-uploader; wspr-recorder cannot collect without `hamsci_dsp`. | NO (C2) |
| T12 / T26 | T26 runs 1-7 use T12's verdicts and `--sinks/--marker/--new-*`.  Run 7's mutated trees are built by the task, not the tool. | Yes |
| T13 / T16 / T17 / T19 / T22 | `write_owned` is the one writer.  T16's `land_sigmond_file` creates `pipelines.d/` itself (mkdir is outside `write_owned`); T22 renames the marker by `os.rename`. | Yes |
| T13 / T14 | Both new helpers refuse a file owned by "anyone but root"; tests run as mjh. | NO (X2) |
| T14 / T19 | T14 moves the probe first in `cmd_uploader_manifest`; T19 then wires carry-over and render in the same function. Sequential. | Yes |
| T14 / T20 | `smd sink` calls `sink set` (own flock) then the manifest step (install lock).  No nested install lock. | Yes |
| T15 / T17 | `below_upload(state)` feeds `plan_levers`.  During the carry-over the marker stands absent; T15's `read_state` and T9's JSON must report `effective` regardless. | Depends on X5 |
| T15 / capture | JSON carries `marker` and `sinks_dir` paths and file times; `capture.sh` re-run must leave `git diff` empty on another machine. | NO (X12) |
| T16 / callers | `generate()` changes return type to `RenderResult`; its other callers (`align_apply`, adopt, restore) are not listed. | NO (X13) |
| T16 / T19 | `sigmond_pipeline_file`, `land_sigmond_file` consumed by T19 (step 7 and the no-change branch, Ruling 14) and removed by T19/T22. | Yes |
| T16 / T19 | T16 imports part 1's loader and render "from a worktree" at 29a0806 / be91e22; `git worktree add` writes metadata into the shared repos. | Minor (X13) |
| T17 / T18 | `plan_levers`, `apply_levers`, `stale_writer_units`, `restart_units`, `verify_levers` consumed by the carry-over.  Carry-over step 3 needs the clients below `upload`: from the plan's own intended states (Ruling 12 says the carry-over computes what it writes) or from `read_state()` after the writes (Ruling 12 says sigmond learns states from the JSON). | Ambiguous (X4) |
| T17 / T20 | Stale-writer restart precedes a change to `off` (marker standing).  In the carry-over the marker is absent until step 4, so "before the marker" gives the same order. | Yes |
| T18 / T19 | Carry-over marker last (T18) then render (T19); a render failure after the marker leaves marker plus part 1 render.  Safe: the gate holds by switch files, and the part 1 text only adds `discard`/hold. | Yes |
| T18 / spec 6.3 | Table rows agree (mode discard -> off; enabled false, no mode -> fill; else upload).  `Coordination` normalises modes to upload/hold/discard (coordination.py:125-127), so a hold mode reads as `enabled=false`. | Yes |
| T19 / T22 | Ruling 10 (T19) moves the marker aside "exactly as restore does"; restore is T22, later.  No task owns one shared helper. | NO (X15) |
| T19 / T20 / T21 / T22 | All need to ask "does the marker stand" and name its path on the sigmond side.  Not a deliverable of any task. | NO (X15) |
| T19 / T23 | T19 captures `manifest_step_part2.txt`; T23 uses it. | Yes |
| T20 / T23 | T23 step 1 wants "a capture of `smd sink fill` from Task 20's tests"; T20 lists no capture. | NO (X6) |
| T22 / T24 | T24 step 1 wants "a captured restore run from Task 22's tests"; T22 lists no capture. | NO (X6) |
| T23 / T24 | Static tests share a parser style (`eval` between markers). | Yes |
| T26 / T12, T18 | Needs both (plan says so).  Run 2 needs the carry-over runner to accept redirected `--sinks`/marker args. | Yes (needs X5 `--marker`) |
| T27 / GC | See X14. | |
| T28 / T29 / T30 | T28 step 0 may stop the plan: the golden VM pulls `origin/main` (plan knows).  T29 follows T28. | Yes; known risk |
| spec §12 tests 1-23 / task table | Each test number maps to a task: 1 T10; 2 T3; 3-5 T4; 6 T5; 7 T6; 8-9 T7; 10 T8; 11 T3+T10; 12 T9(+T2); 13 T9+T11; 14-15 T16; 16 T18; 17-18 T20; 19-20 T17 (+T13 mag); 21+23 T21; 22 T22. | Yes, all 23 covered |
| V1-V15 matrix / spec §10 | V6 now agrees with answer 5.  V2-V4, V12-V15 are plan additions, consistent with §10. | Yes |

### B. Does each task agree with itself?
| Task | Self-consistent? | Note |
|---|---|---|
| 1 | Mostly | Step 2 comment "expect be91e22" is stale (ebd6b19). |
| 2 | NO | Symlink test "planted at a predictable temp name" vs `O_EXCL` (X1); `fchown` in GC3 but no owner parameter in the interface. |
| 3 | Yes | "judged by rule 5" for a non-sigmond `governed=false` should read "falls through rules 2-5"; same result on the test. |
| 4 | Yes | |
| 5 | NO | Marker-appearance trigger has no home (X3). |
| 6 | Yes | Needs a lock for gate state (X8). |
| 7 | Mostly | Test 9 bullet mixes two homes (X11). |
| 8 | NO | Passes `sinks_dir` through the factory without a source (X7). |
| 9 | NO | `sink status` has no `--marker` (X5); effective-state independence unstated. |
| 10 | NO | Mutation 5 has no catching assertion (X10). Step 2's worktree check needs the harness file copied in (minor). |
| 11 | Yes | Count rule per C2. |
| 12 | NO | HELD vs the starting-record pass (X9). |
| 13 | Yes | |
| 14 | NO | Lock owner check fails for non-root tests (X2). |
| 15 | NO | Reproducible fixtures (X12). |
| 16 | Mostly | Return-type change and worktree (X13). |
| 17 | Yes | |
| 18 | NO | Which states drive the lever step (X4). |
| 19 | Mostly | Marker helper (X15). |
| 20 | Mostly | Capture missing (X6). |
| 21 | Mostly | Marker helper (X15). |
| 22 | Mostly | Capture missing (X6); rolled-back name collision within one second (use a no-replace rename). |
| 23 | Yes | Depends on T20 capture (X6). |
| 24 | Yes | Depends on T22 capture (X6). |
| 25 | Yes | |
| 26 | Mostly | Step labels run "Step 1-3, Run 1-7, Step 8"; cosmetic. |
| 27 | Mostly | Push cadence (X14). |
| 28 | Yes | Step 0 may stop and ask Michael. |
| 29 | Yes | |
| 30 | Yes | |

### C. Conflicts, each with a proposed ruling (spec is the authority)
- X1 (T2). Test plants a symlink at a "predictable temporary name", but the new helper opens with `O_EXCL`, so a hit raises instead of writing.  Ruling: temp name = `.<name>.<pid>.<8 random hex>.tmp`, retry on `EEXIST`; the test plants a link at the old pid-only pattern and at the final path, and `write_marker` still lands a regular file.  `fchown` only when the helper is given an owner.  Cost if wrong: one flaky test.
- X2 (T13, T14). "Refuse a file owned by anyone but root" cannot pass in tests run as mjh.  Ruling: the expected owner is a parameter, `os.geteuid()` by default (root in production); tests set it; one test shows a foreign-owned file refused.
- X3 (T5, T6; Ruling 4). Ruling 4 says no marker cache in the gate; T5 detects "appears".  `Rebuilder.due()` watches only `pipelines.d/`.  Ruling: the main loop (or Rebuilder) keeps one `bool` of the last marker reading, calls `marker_stands` each pump, and runs `write_starting_records` on false -> true before the pumps.  The gate stays cacheless.  T5 owns the hook and tests it on `Rebuilder` with an explicit `marker` argument; T6 passes the same path from `run`.
- X4 (T17, T18; Ruling 12). Which states drive the carry-over's lever step.  Ruling: after the writes, the carry-over reads back with `read_state()` (the JSON, marker-independent) and levers the clients below `upload`; a `None` read aborts before the marker (fail safe, nothing enforces yet).  Ruling 12's "falls back to [uploads]" stays for the readers (T21), not for the carry-over.
- X5 (T9, T15, T26). Add `--marker PATH` to `sink status` (default `marker_path()`), and state that `effective`, `held_by` and `clients` do not depend on the marker (only `enforced` and `held_pipelines` do, since the gate sends without it).  `held_pipelines` comes from `SinkGate`.
- X6 (T15, T20, T22, T23, T24; GC 4). Missing capture steps.  Ruling: T15's `capture.sh` also captures `sink set` and `sink mark-carried` output, only if sigmond parses them (preferred: sigmond reads exit status and re-reads state, parses nothing); T20 captures `smd sink fill` output to `tests/fixtures/`; T22 captures the restore run; T23 and T24 read those files.
- X7 (T8). Add `daemon.py` (`Rebuilder` passes `self.sdir`) and the `build_pipelines`/`build_entry` signature to T8's file list; the default stays `DEFAULT_SINKS_DIR` for the shim.
- X8 (T3, T6). The gate's per-pipeline last-decision table and fault clocks take a lock; add a two-thread test.
- X9 (T5, T12, T26). Ruling: the tool calls `write_starting_records` for NEW when `hs_uploader.daemon` offers it, before the pumps, and prints each key's record.  HELD stays "no batch, no retry popped, no record written *by the pump*"; a starting record the pass writes is reported, and on the station copies none is written (T26 run 3 asserts the list is empty).
- X10 (T10). Add a harness case: a client `pipelines.d` file with `governed = false` and its client at `fill`; it must hold.  Without it, mutation 5 passes the harness.
- X11 (T7). Assign test 9: "bundled copy matches" to sigmond's compat test; the ImportError and missing-name cases to both.  Rule: any hs-uploader commit that touches `sink/writer.py` is followed in the same task by a bundled-copy refresh in sigmond.
- X12 (T15). `capture.sh` runs from a fixed directory with relative paths, and the contract test applies the same normalisation (placeholder for tree paths); no wall-clock values appear (switch files carry fixed `set_at`).  Re-run keeps `git diff` empty on any machine.
- X13 (T16). Keep `generate()` returning `str`; add `generate_forms()` returning `RenderResult`; every existing caller stays byte-identical.  Pin part 1 by `git archive` of be91e22 and 29a0806 into a temp dir (skip when git or the commit is absent), not `git worktree add`.
- X14 (global, T27). Push per task after review (the bus note names the pushed sha); T27 step 3 pushes the final state.
- X15 (T19-T22). One shared helper set on the sigmond side, created in T15 (`sink_state.py`) or T18: `MARKER_NAME`, `marker_path(root)`, `marker_stands(root)`, and `move_marker_aside(root, now)` using a no-replace rename.  T19 (Ruling 10), T21 and T22 import it.  T22 does not write its own.
- X16 (answer 5). Plan Ruling 10 matches Michael's answer 5; remove "Michael should confirm" when the ledger copies to tasks/.

### D. Things the plan mandates that a reviewer would call defects
- D1. The marker lives in a directory every recorder account can write (Ruling 5): a compromised account deletes the marker and the daemon passes everything (rule 3 fails open).  Plan accepts it and records v3.79: move the marker under `/etc/hs-uploader/sinks/`.  The security reviewer will raise it; the answer is already in Ruling 5.
- D2. Commit trailer `Co-Authored-By: Claude Opus 5.5 (1M context)` "verbatim whichever model writes the code" (part 1 ruling C5).  When another model writes the code, the trailer names the wrong author.  This session's harness reminder says to use the writing model's name.  Michael's ruling C5 stands unless he changes it; ask him once.
- D3. `smd sink` prints a persistent-fault line on every run (Ruling 19), while daemons log once.  Reasonable; not a defect, listed so a reviewer does not flag it.
- D4. Task 28 step 0 may find that the rig builds only from `origin/main`.  Merging first would break the branch rule; changing the build touches Rob's pipeline.  The plan already says stop and ask.
- D5. Writing the lock file in `/run/lock` (1777): a planted file by another user makes the manifest step fail until root removes it (denial of service, not a leak).  Acceptable; the failure message must name the file.
- D6. `hs-uploader sink set` direct (bench step 6) lowers a client with no lever run until the next manifest step; an in-process sender keeps sending meanwhile.  On B4 none runs (D17).  Say so in the bench post.

### Controller rulings on the preflight scan
- Ruling C3: X1-X15 adopted exactly as proposed in the scan above (random temp suffix with retry; expected-uid parameter defaulting to geteuid; the main loop owns the marker-appearance bool and runs the starting-record pass; the carry-over's lever step reads back via `sink status --json` and aborts on None before writing the marker; `sink status --marker` added and `effective` made marker-independent; capture steps added for `smd sink fill` and restore; sinks dir plumbed to the rider from Rebuilder; a lock plus a two-thread test on gate state; the neutrality tool calls write_starting_records and reports the keys; a harness case for governed=false from a client file; refresh the bundled copy in Task 7; fixed fixture dir and normalised paths; add generate_forms() and use git archive, not worktrees; push per task; one shared marker helper created early) — cost if wrong: small rework per item.
- Ruling C4: D2 — keep the session's trailer verbatim, as part 1's C5 did — cost if wrong: an attribution line.
- Ruling C5: D1 accepted per plan Ruling 5 (marker deletable by recorder accounts; revisit in v3.79); D4 handled when Task 28 starts — cost if wrong: as stated in the plan.
- Ruling C6: wspr-recorder suite runs with PYTHONPATH=../hamsci-dsp/src (baseline 510) — its venv lacks hamsci_dsp at base; not ours to fix here — cost if wrong: none.
- Michael's answer 5 (marker moved aside on rollback without the gate) confirms plan Ruling 10.
- Task 2: implementer DONE 54e96f0 (814 passed; 3 mutations failed); review dispatched
- Ruling C7: lesson 3's "refuse a symlink" is satisfied by REPLACE-not-follow: _atomic_write renames over a symlink target (rename(2) never dereferences it), never writes through it; refusing would let any recorder account block the marker forever. Task 13's sigmond helper follows the same rule — cost if wrong: none found (reviewed).
- Task 2: minor (deferred): test name says "refuses" but asserts replace; docstring should say missing fchown is deliberate; reader-test assertion hard to read; optional one-time WARNING when a planted symlink is replaced
- Task 2: note for Task 3/final: a recorder account can forge the marker (plain file or dangling link) — accepted per plan Ruling 5
- Task 2: complete (commits 29a0806..54e96f0, review clean)
- Task 3: implementer DONE_WITH_CONCERNS e93b3ef (845 passed x3; 31 new; 10 mutations failed). Concerns: stale-pyc trap in mutation runs (Task 2's may be affected) → added to global constraints; gate fault keys by exception text can grow/flood → Task 6 must key by type or cap; daemon must keep ONE SinkGate across rebuilds (Task 6); heartbeat by isinstance(HeartbeatSftp) after unwrapping DiscardTransport.
- Task 3: review Needs fixes — all 30 mutations (Task 2 + Task 3) re-verified bytecode-safe, none survived; Important: marker_stands (lexists) fails OPEN on EACCES/EIO (reproduced, mode 000 → sends during fill); fault keys use free exception text (breaks bounded-key constraint).
- Ruling C8: Minors 1 (reason from Effective, no second read), 3 (thread test asserts slow set used), 4 (assert exact fail-closed reasons) and 6 (bounded bad-name key in sinkswitch) join fix round 1 — cost if wrong: small test code.
- Task 3: minor (deferred): no gate-level test for unknown site state or switch file as directory (probed: both hold)
- Task 3: fix round 1/5 dispatched (resume implementer; base e93b3ef)
- Task 3: fix round 1/5 (5 addressed, 0 open; commits e93b3ef..d09352d)
- Task 3: minor (deferred): test_reason_words_the_deciding_read doesn't discriminate the original second read_switch read; log.warning inside locks; past the 1024 cap new faults go silent until restart (Task 6 reminder should reuse capped sets)
- Task 3: complete (commits 54e96f0..d09352d, review clean after round 1)
- Task 4: implementer DONE 1aade98 (863 passed x3; 11 new; 5 mutations failed bytecode-safe). V15: wspr/psk/meteor shims call Uploader without a gate → part 1 behaviour; their in-process sending is governed by Task 17's levers.
- Task 4: review Approved (every send path gated; 7 mutations caught incl. 2 placement mutations by reviewer).
- Ruling C9: Minor 1 (drain reads one batch ahead before a hold) and Minor 2 (a raising gate aborts pump()/floods) ruled into a hardening round — enforcement-adjacent, cheap — cost if wrong: small code.
- Task 4: minor (deferred): tests read wm._conn; gate asked twice back-to-back per pump (efficiency only)
- Task 4: fix round 1/5 (3 addressed; commits 1aade98..739d75e)
- Task 4: complete (commits d09352d..739d75e, review clean)
- Task 5: implementer DONE_WITH_CONCERNS def54ff (883 passed; 14 new; 7 mutations failed bytecode-safe).
- Ruling C10: WSPR starting record anchors at the cycle BEFORE the current floor (part 1's _effective_cursor), not "the current cycle floor" as spec wording says — the floor would skip the cycle in progress that an unheld pipeline ships; spec wording to be corrected in Task 25 docs — cost if wrong: one cycle re-sent once.
- Ruling C11: with the marker standing, every governed now-pipeline gets a starting record at build (spec rule), so an unheld pipeline's anchor moves up to one pump interval earlier — ships more rows, never fewer — accepted as spec'd.
- Task 5: review Needs fixes (tests only): all 5 gates hold; M8 and M13 survive; no 100-iteration log test for marker_check.
- Ruling C12: Minors 1 (insert-only starting record; last_ack untouched), 2 (cached WSPR anchor test), 3 (exempt_rule/pipeline_key inside try), 4 (wording) ruled into fix round 1 — cost if wrong: small code.
- Task 5: minor (deferred): byte-equality coverage omits radiod_id/extra_where/dedup (equal by construction)
- Task 5: fix round 1/5 dispatched (resume implementer; base def54ff)
- Task 5: fix round 1/5 (6 addressed; commits def54ff..bb93ce5). Ruling C13: starting record writes via new insert-only SqliteWatermarkStore.start_cursor (ON CONFLICT DO NOTHING) instead of the spec's advance_cursor — stricter than forward-only; spec wording to follow in Task 25 — cost if wrong: none.
- Task 5: minor (deferred): start_cursor not in the WatermarkStore Protocol (only SQLite store exists); an empty-cursor row silently blocks the starting record (nothing writes one)
- Task 5: complete (commits 739d75e..bb93ce5, review clean)
- Task 6: implementer DONE_WITH_CONCERNS ca97303 (BASE bb93ce5); 900 passed
- Task 6: review ca97303 — Spec ✅, Quality changes needed: I1 shared site/no-sinks keys cleared by invalid-client pipelines (flood/silent); M2 gate-exception key never clears; M3 _last unbounded-cap silent. M4-M6 concerns judged harmless. Fix round 1/5.
- Task 6: fix round 1 f8447a1 (I1, M2, M3 + gate.forget); 906 passed
- Task 6: re-review f8447a1 — I1, M3 fixed; NEW Important: M2 immediate clear floods on an alternating gate exception (50 ERROR/100); Minor: fault-as-transition INFO flap (predates). Ruling: a recurrence within REMIND_S of the last line stays silent; the transition line is suppressed for rule-0 decisions — global rule 2 outranks M2's first-sight wording — cost if wrong: a recurrence within 6 h shows only as held/sending state, not a new ERROR. Fix round 2/5.
- Task 6: fix round 2 fd8ba70; 907 passed. Implementer concern (first decision a gate fault → no held line): accepted, the ERROR line itself says 'pipeline p held: the sink gate failed'.
- Task 6: minor (parked → final review fix batch): recovery from a long gate fault logs nothing (gate.py _transition rule-0 return); fix = one rate-limited INFO "sending: the sink gate recovered" in _clear_exceptions for keys that logged.
- Task 6: complete (commits ca97303..fd8ba70, review clean after 2 fix rounds)
- Task 7: dispatched (BASE hs-uploader fd8ba70, sigmond ebd6b19)
- Task 7: implementer DONE_WITH_CONCERNS hs-uploader 7c3de5e, sigmond b2b9cd5. Ruling: spec §4.1 + design §3.4 govern a broken switch file (reads off → writer drops), overriding my dispatch default — spec is binding — cost if wrong: a site.toml typo loses repository rows while it lasts.
- Task 7: review — Spec ✅, Quality Approved. Fix round 1/5 (tests only): pin site_error drop + marker-vanish line (2 surviving mutations). Parked → final batch: exception path leaves _dropping True after _note_verdict (stale INFO 'storing nothing'); narrow catch unpinned (low value).
- Task 7: fix round 1 9db200c tests only (src byte-identical; 3 mutations watched failing); controller verified diff scope. Task 7: complete (hs-uploader 7c3de5e..9db200c, sigmond b2b9cd5, review clean)
- Task 8: dispatched (BASE hs-uploader 9db200c)
- Task 8: implementer DONE_WITH_CONCERNS e647173; 954 passed; wspr-recorder 510 baseline
- Task 8: Ruling: a raising rider check keeps the cycle's psk rows out, ships its WSPR rows, logs ERROR once per type then 6 h — fail closed like the gate (spec silent) — cost if wrong: those rows miss that one tar (they still reach pskreporter by their own pipeline). Ruling: missing client file drops a rider — spec §5 'only when reads upload' + effective_state fails closed on a missing client — cost: none beyond spec. Ruling: sinks_dir=None default resolved per cycle — same production paths, redirectable in tests.
- Task 8: review — Spec ✅, Quality Approved; fix round 1/5: M1 hermetic tests (3 fail on a marker-bearing host), M2 caps/reminder/count untested, M3 build_layout default. Parked → final batch: M4 producer-column probe cached per connection; M5 one raising row drops the cycle's riders.
- Task 8: fix round 1 c754072; 960 passed; host-marker proof green
- Task 8: complete (commits e647173..c754072, review clean)
- Task 9: dispatched (BASE hs-uploader c754072)
- Task 9: implementer DONE_WITH_CONCERNS 2da0bd7; 1003 passed. Ruling: the capture/contract command is '-P -m hs_uploader.cli' (no __main__.py; sigmond already uses it) — global constraint 4's example was loose — cost: none. Ruling: part-1 status JSON shape replaced by the brief's schema 1 (nothing in sigmond read the old one).
- Task 9: review — Spec ✅, Quality changes needed: I1 pipe_views parity untested (2 mutations survive), I2 status forms a path from a bad client name, I3 null identity → misleading 'move the file aside'. Fix round 1/5 also takes M1 (NullHandler on status), M2, M3 (buildable fixtures), M4 (stdin cap), and §7.5 gaps 1/3/4. Ruling: schema 1 gains held_pipelines[].held_by, top-level 'errors' (manifest unreadable, store unreadable), and store-schema refusals in refused_files with file=null — additive, before any reader exists — cost: none. Parked: M6 double read (small window).
- Task 9: fix round 1 5828153; 1022 passed; schema additive (+builder_pipelines, refused_files[].pipeline)
- Task 9: minor (parked → final batch): M1 stderr test inherits os.environ (PYTHONWARNINGS would break it) — pass a minimal env; M6 double read.
- Task 9: complete (commits 2da0bd7..5828153, review clean)
- Task 10: dispatched (BASE hs-uploader 5828153)
- Task 10: implementer DONE_WITH_CONCERNS 1ae85ce; 1024 passed; failed at fd8ba70 (OFF ROW SHIPPED); 5 mutations caught
- Task 10: review — Spec ✅, Quality Approved; gate: 5/5 mutations caught (M1 by HELD WORK, legitimate per §3.3). Fix round 1 (tests only): identity in blobs, sorted held, marker-vanish walk. Parked: rider not in harness (test 10 covers); outer drain check :557 unpinned (not a §3.2 place).
- Task 10: fix round 1 a9734b8 tests only (controller verified scope + harness green). Task 10: complete (commits 1ae85ce..a9734b8, review clean)
- Task 11: dispatched (BASE hs-uploader a9734b8)
- Task 11: 2f22fe2 (3-line diff). Ruling: controller reviewed inline (FEATURES tuple sorted, bare call 3, mutation seen failing; client suites at baselines 510/316/559+1s/165 with the editable path) — a reviewer seat on a one-word change buys nothing — cost if wrong: the final whole-branch review re-checks it. Task 11: complete (2f22fe2)
- Task 12: dispatched (BASE hs-uploader 2f22fe2)
- Task 12: implementer DONE_WITH_CONCERNS bdcb11a; 1083 passed; RESULT line now 'RESULT: PASS (a AGREE, h HELD, d DISAGREE)' — Tasks 25/26 must match
- Task 12: review — Spec ✅, Quality changes needed; gate NOT confirmed: I1 starting-pass record moves hidden (tool trusts NEW's return value), I2 duplicate names lend exemptions. Fix round 1/5: I1 diff watermarks around write_start; I2 per-name list matched by build order, duplicates with differing exemption → INCONCLUSIVE; M1 builder-entry expectation from NEW → INCONCLUSIVE once the marker stands; M2 bool(include_psk); M3 fingerprint the sink copy around held pumps (cheap).
- Task 12: fix round 1 77db5c8; 1093 passed; INCONCLUSIVE on pipeline lines (Task 26 parsers)
- Task 12: complete (commits bdcb11a..77db5c8, review clean, gate confirmed: 9 old + 5 new attacks all DISAGREE/INCONCLUSIVE)
- Task 13: dispatching (BASE sigmond b2b9cd5)
- Task 13: implementer DONE_WITH_CONCERNS sigmond 83cb8a1; 4979 passed, 1 failed (pre-existing: RealCheckoutTests expects no 'gate' — consequence of Task 11; Ruling: Task 14 owns test_uploader_features_probe.py and fixes it there — cost: one red test until Task 14).
- Task 13: review — Spec ✅, Quality Approved w/ minors, Security 4 LOW (hard-link disclosure confirmed by PoC, mitigated by protected_hardlinks=1; read-by-path swap; fchown before write; unbounded read; cleanup masks error). Fix round 1/5 takes all: read via dir fd + nlink/uid checks + 1 MiB cap; write→fsync→fchown→fchmod; cleanup catches OSError; write_toml_bool keeps uid/gid when uid is 0 or the dir owner; FIFO tests time out; rename the misnamed test. Ruling: refuse (not replace) a symlink at a lever file — the service can re-arm its own lever, replace would disclose or drop settings — cost: a loud lever failure the operator must clear. Parked: set_env_flag mkdir by path (harmless).
- Task 13: fix round 1 1755abf (rewrite_owned callback; one dir fd); 4991 passed + known Task 14 failure
- Task 13: re-review 1755abf — all round-1 security findings closed. Second automated finding confirmed (inherited from part 1's _chown_to_env_dir_owner): set_env_flag hands a root 0600 env file to the dir owner at 0644. Ruling: set_env_flag keeps an existing file's owner and mode (keep_owner=True, mode=None); a new file goes to the dir owner at 0644 — systemd reads EnvironmentFile as root, so services never need read access — cost if wrong: a recorder configurator run as its service user could not rewrite a root-owned env file (none does; smd runs them as root). Fix round 2/5.
- Task 13: fix round 2 bc0d3f4 (one-line keep_owner + tests; controller verified diff). Task 13: complete (commits 83cb8a1..bc0d3f4, review + security clean)
- Task 14: dispatched (BASE sigmond bc0d3f4)
- Task 14: implementer DONE_WITH_CONCERNS 9f203d0; 5024 passed, 0 failed; also closed a root-exec hole in hs_uploader_supports_discard (planted module in cwd)
- Task 14: review — Spec ✅, Quality Approved, Security findings: heartbeat._read_backlog -m without -P/cwd (pre-existing C23 class, MEDIUM); lock no nlink check (LOW on our hosts: tmpfs + protected_hardlinks); -P keeps PYTHON* env (LOW); planted lock file denies service (LOW, D5).
- Ruling C14-1: fix _read_backlog now (-P, cwd="/") — C23 named sibling -m probes — cost: none.
- Ruling C14-2: the runner uses -I (isolated) instead of -P — verified the venv's editable .pth still resolves under -I — cost: two RealCheckoutTests switch to ../hs-uploader/.venv/bin/python.
- Ruling C14-3: move the install lock to a root-only directory /run/hs-uploader-install/lock (mkdir 0700 root, lstat-check: dir, uid 0, no g/o write), plus nlink != 1 refusal. Departs from Ruling 7 / spec §6.1 / design §3.2's /run/lock path; nothing deployed takes the lock yet, so now costs least; it closes both the hard-link chmod and the planted-file denial of service. Task 25 updates spec/design text; the v3.79 installer must use the new path. Not /run/hs-uploader (a future RuntimeDirectory would chown/delete it). Report to Michael at merge time — cost if wrong: one constant and two doc lines.
- Forward notes: Tasks 16-20 must never re-enter cmd_uploader_manifest under the lock (flock self-deadlock), and an `smd sink off` order must never depend on taking the lock.
- Task 14: fix round 1 1dbf4f3; 5033 passed, 0 failed
- Task 14: complete (commits 9f203d0..1dbf4f3, review + security clean). Michael to confirm C14-3 (lock path) at merge time.
- Task 15: dispatched (BASE sigmond 1dbf4f3); brief corrected in dispatch: -I -m hs_uploader.cli, schema-1 additions from Task 9 fix round
- Task 15: implementer DONE_WITH_CONCERNS 28ebd37; 5061 passed. Exit 2 → None (no pipelines listed, would read 'nothing held'); errors kept as SinkState.errors/partial. Forward: Task 21 prints the fallback line and shows errors when partial.
- Task 15: review — Spec ✅, Quality Approved; capture.sh re-run, git diff empty. Parked → final batch: contract test skips on 'no venv' not 'no checkout' (fail when checkout present but venv absent); exit-2 test doesn't assert the quoted error. Forward note (Tasks 18, 21, harmonize): below_upload ignores the marker by design (X5) — callers must check enforced/partial deliberately.
- Task 15: complete (28ebd37, review clean)
- Task 16: dispatched (BASE sigmond 28ebd37)
- Task 16: implementer DONE_WITH_CONCERNS c44a81b; 5091 passed. Ruling: X13 stands over my dispatch — generate() keeps str; generate_forms() returns RenderResult; Task 19 switches cmd_uploader_manifest — cost: none. Refusing a symlink at pipelines.d/sigmond.toml is right (root-owned dir, C7 covers service-writable dirs).
- Task 16: review — Spec ✅, Quality Approved; pin isolation proven by mutation. Parked → final batch: warn on an existing pipelines.d sigmond can't read or others can write; no-change check ignores group; land_sigmond_file check and write use different dir fds (root-only swap); start_at strip untestable today.
- Forward note (Task 19): land order — gate form: sigmond.toml FIRST, then legacy manifest; old form: legacy manifest FIRST, then remove sigmond.toml; a land_sigmond_file exception in the gate form is a step failure; /etc/hs-uploader missing raises FileNotFoundError.
- Task 16: complete (c44a81b, review clean)
- Task 17: dispatched (BASE sigmond c44a81b)
- Task 17: implementer DONE_WITH_CONCERNS 8b98154; 5141 passed. A failed lever write is recorded with its file, the others go on, and that unit does not restart.
- Task 17: review — Spec ✅, RESTART GATE PASS, Quality changes needed: I-1 verify false failure (whole-second ActiveEnterTimestamp vs fractional mtime, 20/20 repro); I-2 a written-but-unrestarted lever is forgotten next run; I-3 RadiodMoved loses the partial result; I-4 unreadable writer → every off change restarts everything; I-5 forward: Task 20 order restarts twice.
- Ruling C17-1: compare at whole seconds (started < floor(mtime)) in verify and plan — cost: none.
- Ruling C17-2: plan_levers treats a lowered-on-disk instance running from before its env file's mtime as restart-only; verify applies the start-time check to every in-scope instance — cost: an extra restart where a lever was written by hand without a restart.
- Ruling C17-3: RadiodMoved carries the partial LeverResult and restart failures (exc.result).
- Ruling C17-4: unreadable writer file → nothing stale, reported as a line (no mass restart) — Michael's answer 4 accepted one-time restarts only — cost if wrong: a stale writer keeps storing rows for an off producer until the next restart.
- Ruling C17-5: apply_levers gains skip= and returns restarted; Task 20's order is lever step first, then restart_stale_writers(skip=res.restarted), then the site write.
- Minors taken now: M-1 positive controls; M-6 verify treats any UnitFileState other than disabled/masked/missing as armed. Parked: M-2 (Task 20 pins order), M-3, M-4, M-5.
- Task 17: fix round 1 c7aa34e; 5149 passed; restart_stale_writers now returns StaleResult(restarted, notes)
- Task 17: re-review c7aa34e — all findings addressed; RESTART GATE PASS. Parked → final batch: static wspr-uploader armed only while active (§8.3 second-run quiet); env_path_for legacy hint now prints for lowered instances each run; restart_stale_writers drops StaleResult on exception (failures still ride); M-3, M-4, M-5. Task 20 owns M-2 (pin real order).
- Task 17: complete (commits 8b98154..c7aa34e, review clean, gate PASS)
- Task 18: dispatched (BASE sigmond c7aa34e)
- Task 18: implementer DONE_WITH_CONCERNS 8395fb5; 5194 passed; B4 + K3LR predictions hold. Ruling C18-1: restore X4 — my dispatch's 'intended state' conflicted with it; after the switch writes, apply() reads back through sink status --json (Ruling 12, one implementation), lowers the union of read-back below_upload and the plan's own, and aborts before the marker when the read fails — cost: one subprocess.
- Task 18: review — Spec ❌ (X4 read-back missing; stop-flag fail-open; coordination.env unread), Quality changes needed. CARRY-OVER GATE PASS on fixtures; B4 + K3LR predictions confirmed. Automated scan confirmed by PoC: sink.db producer names reach shutil.which → root exec (Important).
- Ruling C18-2 (I1): a name seen only in sink.db becomes a candidate only if it is a catalog key (not deprecated); its deploy.toml resolves by fixed path only (topology client_dir/<name>/deploy.toml or GIT_BASE/<name>/deploy.toml) — never find_deploy_toml/inventory; any other name is a counted note. plan() truly reads only.
- Ruling C18-3 (I2): an unreadable or invalid-named stop-flag env file fails closed — the client is written at fill with a reason naming the file — cost: an operator clears it with smd sink.
- Ruling C18-4 (I3 / C18-1 condition c): read-back through sink status --json with roots.sinks and roots.marker; lower the union. If read_state returns None and the legacy manifest exists, abort before the marker. If the legacy manifest is absent (fresh install, carry-over before the first render), use the plan's own below_upload and print one line saying so — cost: on that one path the duplicate rule governs, and it fails toward lowering.
- Ruling C18-5: m1 read /etc/sigmond/coordination.env too (pure file read; drop-ins remain out, noted); m2 tests for the four surviving mutations; m3 keep the partial StaleResult; m7 restrict late_clients' scan to governed-but-not-enabled names; m8 a missing mag config reads not armed (sink_levers._mag_armed). m5: amend spec §6.4 (one note line) in Task 25 rather than change hs-uploader. m6: Task 25 amends §6.6 K3LR + fresh rows; Task 26 / §13.2 checks K3LR's real wspr flag and wspr-uploader state. m4 parked (over-lowering is the safe direction).
- Task 18: fix round 1 480c099; 5214 passed; 17 mutations bite
- Task 18: re-review 480c099 — I1/I2/I3 + C18-5 fixed; CARRY-OVER GATE PASS (B4, K3LR hold). No Critical/Important; no MEDIUM+ security.
- Correction to C18-3's cost line: no smd verb raises a client in part 2 (D18: smd sink is site-only); the remedy is `hs-uploader sink set <client> upload` as root. Task 25 docs say so.
- GATE CONDITION for §13.2 / Task 26 (Important): on each station, stat owner/nlink/type of /etc/sigmond/coordination.env and each /etc/{psk-recorder,meteor-scatter}/env/*.env with each directory's owner, and check env-file names against validate_reporter_id — a refused file now carries a client to fill permanently.
- Parked → final batch: R1 (read-back off feeding to_off untested), R3 (refused coordination.env untested), late_clients docstring, dangling manifest symlink test; hs-uploader sink status crashes on BLOB values in a replaced store (LOW; coerce/skip non-text in _store_rows/_sink_status).
- Task 18: complete (commits 8395fb5..480c099, review clean, gate PASS)
- Task 19: dispatched (BASE sigmond 480c099)
- Task 19: implementer DONE_WITH_CONCERNS ab8594d; 5251 passed. Ruling C19-1: with the marker standing and sink status unreadable, the step still renders and lands (the daemon's gate enforces the switches regardless), skips the lever step with one loud line naming the cause, and exits 1 — an unreadable status must never block an off order or a render — cost: a lowered client's in-process sender stays armed until status reads again (verify names it next run).
- Task 19: review — Spec ✅, CARRY-OVER GATE PASS (every caller traced; one carry-over; no re-entry under the lock), Quality changes needed: I-1 C19-1 untested, I-2 step-7 gate-form land failure untested, I-3 non-root write guard untested. Fix round 1/5 (tests + prefix): I-1..I-3, M-1 (prefix the sink_state line or name the cause in _lever_step), M-2 test, M-3 wording, M-4 lstat preflight of pipelines.d/sigmond.toml before step 5. M-5 → Task 24 (match line shapes).
- MERGE GATE (I-4): at ab8594d, `smd sink fill|off` still writes [uploads] alone; with the marker standing, mirror repair copies site.toml back → leak. Task 20 must land before any station sees this branch. Never deploy ab8594d alone.
- Task 19: fix round 1 9e832ec; 5257 passed; _err prints 'smd: uploader: <msg>'; unprefixed: logging output and DRIFT diff lines (Tasks 23/24)
- Task 19: re-review 9e832ec — all findings addressed. Parked → final batch: old form refuses a symlinked pipelines.d before landing the legacy manifest (check only when sigmond.toml present); _apply_relayed drops captured lines on exception; relay labels any stderr as read-back.
- Task 19: complete (commits ab8594d..9e832ec, review clean, gate PASS). MERGE GATE stands until Task 20.
- Ruling C20-1: the brief's order (stale restarts → site write → levers) conflicts with C17-5. With gate: lever step from the INTENDED state (clients below upload after the change) → restart_stale_writers(skip=res.restarted, to_off) only when lowering to off → `hs-uploader sink set site` → [uploads] in both files → the manifest step (its lever step then finds nothing). Lowering early is safe; no instance restarts twice. The site write must never wait on the install lock (forward note); only the manifest step takes it. A lever failure is reported, but the site write still happens (an off order must land) and the command exits 1 — cost: an armed sender may remain, named by verify.
- Task 20: dispatched (BASE sigmond 9e832ec)
- Task 20: implementer DONE_WITH_CONCERNS ffe491d; 5279 passed + 11 sudo-dependent tests failing identically at base under a fake sudo. INCIDENT (devbox only, 19:34Z): an old test ran real bin/smd sink fill; passwordless sudo elevated it; it created /etc/sigmond/coordination.toml ([uploads] enabled=false), /etc/hs-uploader/pipelines.toml, and 24 root-owned .pyc under lib/sigmond; tried to stop an unloaded hs-uploader.service. Controller verified contents. Cleanup needs Michael (classifier refused the agent). Test now runs --help only; reviewer to confirm no test can elevate.
- Task 20: review — Spec ✅; RESTART GATE FAIL on one path: gate without the marker, `smd sink off` restarts psk and wspr twice (steps 1-2 then the carry-over). MERGE GATE leak CLOSED (end-to-end). Ruling C20-2: before the marker, skip steps 1-2; the carry-over levers and restarts stale writers itself with skip — cost: none (rule 3 sends everything until the marker stands). Fix round 1/5 also: I-2 tests (discard door half; skip pinned), M-1 help text, M-2 success-line wording, M-3 radiod_moved test + wording, M-4 the two exceptions still reach the manifest step or exit cleanly, M-5 root guard in _site_change, and a conftest guard that makes _need_root/elevation raise under pytest (incident class).
- INCIDENT 2 (B4, 19:57Z): my ambiguous "run them here" sent the devbox cleanup into a B4 shell; `! sudo rm` deleted B4's /etc/sigmond/coordination.toml and /etc/hs-uploader/pipelines.toml (.bak of 10-22 20:22 remains). Services still up. Bus warning posted 19:59Z. Awaiting Michael's choice of restore path.
- INCIDENT 2 resolved 20:10Z: coordination.toml rebuilt (env byte-identical); manifest regenerated, identical to 10-21 copy; hs-uploader restarted once; radiod untouched. Lesson: tell Michael WHICH terminal — '!' only works in the Claude Code prompt; in bash it negates.
- Task 20: fix round 1 56baee8; 5291 passed, 11 skipped (needs_real_sudo); conftest exec guard (SIGMOND_TEST_ALLOW_SUDO=1 lifts it). Devbox cleanup already done by Michael 19:59Z.
- Task 20: re-review 56baee8 — all findings addressed; RESTART GATE PASS (incl. late client, radiod-moved). Parked → final batch (HIGH): harden the test guard — refuse sudo/doas/pkexec/run0 in Popen/posix_spawn/spawn*/system and in explicit env dicts; bin/smd _need_root refuses when SIGMOND_TEST_NO_ELEVATE=1 (conftest exports it). Minor: conftest double-load (`from tests.conftest import`).
- Task 20: complete (commits ffe491d..56baee8, review clean, gate PASS). MERGE GATE (I-4 leak) CLOSED.
- Task 21: dispatched (BASE sigmond 56baee8)
- Task 21: implementer DONE_WITH_CONCERNS cfd8948; 5321 passed, 11 skipped
- Task 21: review — Spec ✅, Quality Approved. Fix round 1/5: I-1 keep part 1 wording in the switch view's site line ('site sink: upload (store and send; set …, by …)') so the rig grep at test-nested-v3.sh:1316 holds; M2-M7 tests for surviving mutations; M8 label verify read failures 'cannot check', not UNMANAGED. Concern 1 premise wrong (pre-marker exits 0).
- Task 21: fix round 1 140346b (site line keeps part 1's phrase whole; 'cannot check' for unread senders, exit 1; 6 paths pinned); controller verified the code diff. Task 21: complete (commits cfd8948..140346b, review clean)
- Task 22: dispatched (BASE sigmond 140346b)
- Task 22: implementer DONE_WITH_CONCERNS ed9ec2c; 5344 passed; reuses move_marker_aside (X15) over the brief's os.rename
- Task 22: review — Spec ✅, CARRY-OVER GATE PASS, Quality changes needed: I-1 an unverified restore never moves the marker (Ruling 10 leak). Fix round 1/5: move the marker in the unverified branch too (idempotent; the render stays held back, D11); M-1 choose smd_path once and probe its own tree; M-2 name the rolled-back target in the failure message; M-3 change [uploads] between restore and the second carry-over.
- Task 22: re-review 31885c7 — all findings addressed; CARRY-OVER GATE PASS (V5→V8 in one test). Parked → final batch: M-4 pin the production fallbacks sink_state.MARKER_PATH / um.PIPELINES_D in _restore_below_part2 (a mutation to STATE_DIR survives).
- Task 22: complete (commits ed9ec2c..31885c7, review clean)
- Task 23: dispatched (BASE sigmond-appliance 734afa3)
- Task 23: implementer DONE_WITH_CONCERNS sigmond-appliance 9f7b02b; static test 93 checks; rig owed (Task 28); lever proof may fall to the bench (Task 29)
- Task 23: review — Spec ✅, Quality changes needed: I-1 lever verdict passes on a crash; I-2 the plant masks a carry-over that wrote no switch file. Fix round 1/5 takes I-1, I-2, Minor-1 (STEPRC/SMDERR), Minor-2 (run the guest state script hermetically with stub systemctl/journalctl), Minor-3, Minor-4 (judge every client instance by start time; tolerate two psk instances), Minor-5, Minor-6 (clean or announce plant residue).
- Ruling C23-1: in the nest, unevaluated lever cases are named on the bless line, not refused. MERGE GATE: Task 30 refuses while any named lever case — above all "radiod's start time unchanged" — stays unevaluated on the B4 bench (Task 29). Task 28 copies each UNCHECKED lever line into a bench-obligation list; Task 29 closes every item. Optional: the runtime sleep drop-in to make psk/meteor restarts evaluable in the nest.
- Task 23: fix round 1 eea8059; 162 static checks; 12 mutations bite
- Task 23: re-review eea8059 — I-1..M-6 fixed; open: N-1 (unwatched unsteady unit → false FATAL), N-2 (I-2 must use the carry-over's rule: enabled AND deploy.toml declares pipelines, read independently by fixed paths), U-1..U-3 undo gaps. Fix round 2/5.
- Task 23: fix round 2 a402171; 190 static checks. Parked: rig reads topology client_dir first but sigmond's load_topology never does (disagree only if a station sets it); ROWS skips blank-producer rows (fewer UNCHECKED, never a wrong FAIL).
- Task 23: re-review a402171 — all Important addressed. Fix round 3/5: M-A silent pre probe (PREDONE token), M-B drop client_dir.
- Task 23: fix round 3 0195e15 (M-A PREDONE; M-B /opt/git/sigmond only; 4 mutations bite; 197 checks). Task 23: complete (commits 9f7b02b..0195e15, review clean). Rig run owed at Task 28; unevaluated lever cases → bench obligations (C23-1).
- Task 24: dispatching (BASE sigmond-appliance 0195e15)
- Task 24: implementer DONE_WITH_CONCERNS sigmond ad53b95 (restore capture), appliance 9140423; 132 static checks; G-ter carry-over likely NOT EVALUATED on the rig (detached checkouts after restore) — Task 28 must decide
- Task 24: review — Spec ❌ (G-ter carry-over can never run on the rig: restore detaches checkouts, smd update compares HEAD..@{u} and pulls nothing), Quality changes needed, CARRY-OVER GATE PASS. Ruling C24-1: course B — a named rig step before gt1 re-attaches each checkout the restore moved to the branch recorded at E (runuser as the checkout owner), then smd update --apply; require [pull] sigmond and hs-uploader; the "left the restored sigmond in place" branch becomes FAIL — cost: the rig stands in for smd align. PRODUCT GAP (file for Rob / next build): restore's comment promises smd update gets components back onto main; a detached checkout has no upstream, so it never does.
- Fix round 1/5 also: M1 sentinel conditional on a real verdict line; M2 load sp2_tok at startup; M3 old-step input from a real v3.78 render; M4 name the likely cause for an added file; M5 tighten the sigmond moved-check and digest symlink targets.
- Task 24: fix round 1 36da796; 205 static checks; re-attach uses git checkout -B <branch> <restored sha> so the update really pulls
- Task 24: re-review 36da796 — all findings addressed; CARRY-OVER GATE PASS. Ruling C24-2: Task 28 accepts explicitly the UNCHECKED line for a pinned checkout other than sigmond/hs-uploader (no bearing on V8). Task 24: complete (sigmond ad53b95; appliance 9140423..36da796, review clean)
- Task 25: dispatched (BASEs hs-uploader 77db5c8, sigmond ad53b95, appliance 36da796)
- Task 25: implementer DONE_WITH_CONCERNS hsu c051a79, sigmond 53cf481, appliance 70fc8d3. Correction: smd align --apply pins detached at the blessed release (it does not re-attach); docs say so.
- Task 25: review — 25/29 claims match; 4 wrong. Ruling C25-1 (code, reopens Task 18 code): the carry-over keeps the operator's [uploads] reason — site.toml reason = "<[uploads].reason> (carried over from [uploads] mode = <mode>)" when a reason exists, else the current text — so "new station: …" survives (wizard, docs, check.py match on it) — cost: none. Fix round 1/5: C25-1 with a test; doc fixes 2-4 and the smaller points; troubleshooting/day-2/registration/QUICKSTART old sink words; style spots; the reviewer's restore-recovery steps go into the release notes verbatim in substance.
- Task 25: fix round 1: sigmond 913bfed (C25-1 code) + 5c75d75 docs, hsu e0fadf3, appliance c0e87b4; 5353 passed
- Task 25: re-review — Accuracy ✅; controller fixed spec §7.2 passive (ece13b8), glossary order and release-notes 'It' (cfbed4e, dcb409d). Task 25: complete (hsu c051a79..e0fadf3; sigmond 53cf481..dcb409d incl. 913bfed C25-1 code; appliance 70fc8d3..c0e87b4)
- Task 26: dispatched (hsu e0fadf3, sigmond dcb409d, appliance c0e87b4); includes GATE CONDITION stats on B4/ND and K3LR wspr/levers read (C18-5 m6)
- Task 26: implementer DONE_WITH_CONCERNS. Prediction = outcome on B4 and ND (6 sink set + mark-carried; nothing below upload; no levers). RESULT: run1 B4 7 AGREE / ND 6 AGREE; run3 ×3 PASS; run4 40 runs PASS (B4 exactly the held client HELD; ND v3.69 render → rule 4 all AGREE); run5 B4 (1 AGREE, 6 HELD); run6 B4 (1 AGREE, 6 HELD); run7 DISAGREE for gate-removed, rider-removed, marker-ignored (B4). GATE CONDITION met on B4/ND (root:root 644, nlink 1; names valid). K3LR unreachable (refused ×2) — §13.2 K3LR check OPEN.
- Final batch adds: (a) wspr-uploader "linked"/"static" count armed only while active — `disable --now` on a linked inactive unit would unlink it (both B4 and ND show linked+inactive); (b) one spec line: under rule 4 (v3.69 render) the rider still drops a held producer's psk rows from the wsprdaemon tar until the re-render; (c) pin core.py:557 outer drain check (Task 10 M4 again).
- Task 26: REVIEW GATE PASS (runs 1, 3, 7 reproduced from README; checksums OK before/after). README gaps sent back (nc.sh output dir, preconditions, digests for runs 4-6, digest check).
- Task 26: README gaps fixed (nc.sh -o, preconditions rebuild, digests.ref 72 runs/1550 lines with --check). Task 26: complete (proof /home/mjh/hamsci/proofs/sink-2b-part2-20261023; gate PASS). K3LR §13.2 read OPEN (unreachable).
- Task 27: final reviewer dispatched (hsu 29a0806..e0fadf3, sigmond be91e22..dcb409d, appliance 734afa3..c0e87b4)
- Task 27 Step 1: final review — not ready for the rig until I-1 fixed. All 23 §12 tests mapped with biting mutations; V1-V15 ✅; seams hold. I-1: `smd sink off|fill` with the marker standing and a failed features probe takes part 1's path, exits 0, and the mirror repair reverts the order (reproduced). M-1 sigmond SINKS_DIR unpinned; M-2 rider drops riders of an ungoverned producer while all switches read upload; M-3 marker path ignores HS_UPLOADER_STATE_DIR; M-4 = final batch (a), before the bench.
- Ruling C27-1 (fix wave scope). Wave A, sigmond: I-1 (marker standing + probe None → _site_change; never part 1's path); M-1 + Task 22 M-4 (pin SINKS_DIR, MARKER_PATH, PIPELINES_D against hs-uploader's values); M-3 (refuse a non-default HS_UPLOADER_STATE_DIR, loud, until v3.79 derives the marker); (a) linked/static wspr-uploader armed only while active; HIGH test-guard hardening (_need_root refuses under SIGMOND_TEST_NO_ELEVATE; Popen/spawn/system guard); Task 15 contract test fails when the checkout exists without a venv; R1/R3 tests; Task 19 old-form symlink check only when sigmond.toml present. Wave B, hs-uploader: Task 6 recovery INFO; Task 7 _dropping stale after exception; core.py:557 pin; sink status coerce non-text store values; Task 9 M1 minimal env; refresh the bundled writer if writer.py changes. Docs: (b) rule-4 rider line; M-2 in release notes (fails closed by §5; Michael may choose otherwise at merge). Deferred with reason (low value or no station impact): Task 8 M4/M5, Task 9 M6, Task 16 minors, Task 17 env hint + StaleResult, Task 19 relay labels, Task 23 ROWS, Task 7 narrow-catch pin.
- Task 27 wave A: sigmond be0c3ba..5a2d80f (8 items); 5387 passed; client suites at baseline. Note: the HIGH-guard red run executed '/usr/bin/sudo -n true' on the devbox (no-op). Ruling C27-2: for wspr-uploader whose UnitFileState is linked/static/indirect/generated and that is active, the lever runs 'systemctl stop' only (never disable, which would unlink it); enabled units keep 'disable --now'. The verb whitelist gains 'stop' for the wspr-uploader constant only — cost: a linked uploader started by hand again would send until the next lever pass.
- Task 27 wave A follow-up 7016eab (C27-2 stop not unlink); 5389 passed. Wave B dispatched (hsu e0fadf3, sigmond 7016eab).
- Task 27 wave B: hsu e0fadf3..5267103, sigmond c9a7d57 (bundled) + b2f677c (docs); hsu 1100, sigmond 5389; client suites at baseline; proof runs 1/3/7 + digests match against hsu 5267103.
- Task 27 Step 2 re-review: READY FOR THE RIG (no Critical/Important). Taking G-1 (scan shell strings; wrapper for PATH-resetting children), G-2 (/nonexistent/sudo in guard tests), M-3' (override → no gate, part 1 path, one loud line) now.
- Task 28 Step 0: the build has no branch knob. build-usb-v3.sh FATALs unless the wizard checkout is at origin/main (:153) and refuses a golden sigmond behind origin/main (:198-205); the golden VM and test-update-v3.sh Phase E pull upstream main through smd update. Per the brief: stop and ask Michael (merge first vs. a build knob in Rob's pipeline).
- Task 27: G-2 db6fb96, G-1 bb5b2da (path-named elevators refused in shell strings; bare sudo meets the PATH stand-in; no wrapper in front of /usr/bin/sudo without root — residual: a child that resets PATH and runs bare sudo), M-3' af1d564 (override → no gate; moves a standing marker aside per Ruling 10). 5390 passed. Task 27: complete. Heads: hs-uploader 5267103, sigmond af1d564, appliance c0e87b4.
- MERGED to main 2026-10-24 by Michael's word (option 1, before the rig): hs-uploader 5267103, sigmond af1d564, sigmond-appliance c0e87b4, all fast-forward. Departs from Ruling 18 / Task 30 order (rig and bench first); Michael chose it. Rig (Task 28) and bench (Task 29) now run from main.

# SDD ledger — plan: /home/mjh/hamsci/repos/sigmond/tasks/plan-sink-2b-part1.md

Spec: sigmond/tasks/spec-sink-2b-v377.md (binding; Michael's rulings in §13).
Bases: hs-uploader 334e71e, sigmond 79dca3d, sigmond-appliance 5da3a0c (tag v3.77 sits on it; unrelated image).

## Controller rulings
- Ruling C1: work on branch `sink-2b-part1` in hs-uploader, sigmond and sigmond-appliance, not on main — rob builds images from origin/main of every component while Michael travels, and a half-built part 1 must not ship in one; the branch may be pushed as a backup; merge to main (ff) only after the final review, with Michael's go — cost if wrong: a later rebase onto main if rob pushes there meanwhile.
- Ruling C2: the plan's rulings 1-6 adopted as written — see plan "Rulings this plan makes on the spec" — cost if wrong: as stated there.

## Preflight scan
Scan of plan-sink-2b-part1.md against spec-sink-2b-v377.md (binding), 2026-10-21, read-only.  I checked the plan's anchors against hs-uploader 334e71e on branch `sink-2b-part1` where noted.  Section A pairs tasks; section B checks each task against itself.  "Agree" means plan and spec both hold.

### A. Pairs that share a file or an interface

| Pair | Producer / consumer | Agree? |
|---|---|---|
| global / C1 | Plan: commit on `main`, push ff to main. Controller C1: branch `sink-2b-part1`. | NO (X1) |
| T1 / T2 | `schema.py` `_check` (T1) / migration 3 appended to `MIGRATIONS` (T2). T1's stand-in is `version=3`; T2 adds the real `version=3`. T2 re-specifies spec test 11. | NO (X2) |
| T2 / T3 | T2: columns, `last_send`, `SCHEMA_VERSION` 3. T3 writes them via a cached version. | Yes, but the unrefreshed default writes the old set (Ruling 6; see D6) |
| T2 / T4 | `last_send` table, read by `backlog.summarize` through `_optional_rows`. | Yes |
| T3 / T4 | `last_send` rows written by the store, read by the backlog. Column order `{client, pipeline, outcome, ts, records}` matches spec 4.4. | Yes |
| T2 / T12 | Bare `capabilities` prints `3\n`; T2 and T12 each test it. T12 owns the spec mutation. | Agree on behaviour; duplicate test (D1) |
| T2 / T14 | Review gate: `uploader_manifest.py:463` bare probe stays byte-identical. T14 repeats the check. | Yes |
| T3 / T10 | `refresh_schema_version()`: T3 calls it at start, T10 at each rebuild. | Yes |
| T3 / T9 / T10 / T11 | All four edit `daemon.py` (start refresh; load and build; run loop; switch report). Different regions, sequential. | Yes |
| T3 / T9 / T10 | All edit `core.py` (nine callers; `governed`/`origin`; `replace_pipelines`). Sequential. | Yes |
| T3 (store) / core | Core reads `ships`, but `record_attempt` gains only `pipeline` and `client`. The store cannot learn that the transport ships nothing. | NO (X3) |
| T5 / T6 | T5 filter lands before T6 writer. Same `pending_uploads` `local` column. Order matches spec 5.2. | Yes |
| T5 / T5 | A private "local" probe per source (SqliteSource, WsprCycleSource). | Agree with spec; duplicated logic (D2) |
| T6 / sigmond bundle | `writer.py` body is copied below the marker. Compat test compares `../hs-uploader` HEAD. | Yes, provided `../hs-uploader` is on the branch (see X1) |
| T7 / T8 | Both extend `sinkswitch.py` and `test_sinkswitch.py`. `read_switch` returns `SwitchRead(state, missing, error)`; `write_switch` must "re-read under the lock" and append to `[[history]]`. `SwitchRead` carries no parsed data and spec 6.1 says `read_switch` returns the parsed file. | NO (X4) |
| T7 / T9 | `valid_client_name` imported by the loader. Only T7 is needed; plan says "Tasks 7 and 8". | Yes (text over-states) |
| T7 / T11 | `effective_state` consumed by daemon and CLI. T7 logs each error once per file signature; T11 and spec 6.2 say the daemon logs it once per signature too. | Agree; double logging risk (X10) |
| T8 / T12 | T12 lands "only after Tasks 3, 9, 10 and 8". The three words need 2-4, 7-11. Numeric order makes it safe anyway. | Text incomplete (X11) |
| T9 / T10 | T9 `Layout.signature` set by `load_layout`; T10 stats the directory before each pump. No stat-only function exists, and a signature taken after the read can miss a change. | NO (X5) |
| T9 / T10 | `load_layout`, `build_layout`, `Refusal`, `Pipeline.origin`. Refusal rows into `pipeline_state` (T10) vs `Layout.refused` (T9). | Yes |
| T9 / T13 | `load_layout`, `build_layout` consumed by the neutrality worker. | Yes |
| T9 / T17 | Loader precedence needs `client` on legacy entries. ND's manifest has none (confirmed in the copy). | NO (X8) |
| T10 / T11 | Both write `tests/test_daemon_rebuild.py` and `daemon.py`. Sequential. | Yes |
| T11 / T12 | Both edit `cli.py` and `tests/test_cli.py`. T12's anchors (`:125-129`, `:160-163`) shift after T11. | Yes (anchors stale; re-find) |
| T11 / CLI | `sink status [--json] [--sinks DIR]` must list "each client that owns a pipeline", which needs the manifest. No `--manifest` flag. | NO (X10) |
| T12 / T14 | `--features` output sorted, one per line; T14 parses and prints. T14 prints `<words>` without saying sorted; T19 expects sorted. | Mostly (X11) |
| T12 / T4 | `send-log-v3` word needs T2-T4 done. | Yes |
| T13 / T17 | Per-side manifests: run 5 needs OLD on the original manifest and NEW on the split one. The tool has one `--manifest` (verified in `neutrality_check.py:598`), and T13 only adds `--pipelines-d`. | NO (X6) |
| T13 / T17 | T13's mutation ("ignore `--pipelines-d` makes the planted-difference test end AGREE") and run 6. | NO (X7) |
| T14 / T15 | T14 prints `uploader: hs-uploader features: ...`; T15 and T19 assert it. | Yes, once sorted (X11) |
| T14 / T19 | T19 expects `pipelines.d send-log-v3 switch-files`. | Yes, once sorted |
| T2 / T15 | Migration report lines parsed by the rig's static test. | Yes |
| T15 / T19 | Phases G-bis and G-ter already exist in `test-update-v3.sh` (verified). T19 Step 3 runs them after the push. | Agree; ordering conflicts with C1 (X1) |
| T16 / T1-T15 | Docs describe the code. | Yes |
| T17 / T2, T3, T9, T13 | Ruling 4 handles ND at version 1. `--keep-retry-for psk-recorder-pskreporter` is passed "on both"; ND's pipeline is `psk-pskreporter`. | NO (X9) |
| T17 / T18 | Task 18 re-runs runs 1 and 6 after fixes. | Yes |
| T18 / T19 | T19 needs a built image; plan says "after the push". | NO (X1) |

### B. Each task against itself

| Task | Self-consistent? | Note |
|---|---|---|
| 1 | No | Stand-in `Migration(version=3, ..., description=...)`: the real field is `summary` (verified, `schema.py:123-128`). After T2 lands, the stand-in duplicates version 3. Helper `_store_v2_missing_meteor_key` and `_user_version` undefined. |
| 2 | No | Step 2 says "all fail", but `test_check_previews_fill_with_migration_3_pending` passes at base (nothing pending, so the fill previews), and its spec mutation belongs to T1. Duplicates T1's test. |
| 3 | No | Interface gap on `ships` (X3). The "nine outcomes" test needs the core harness to call `refresh_schema_version()`; not stated. Protocol edit in `base.py` ignores any other store implementation or fake. |
| 4 | Yes | |
| 5 | Yes | One helper per source duplicates logic (D2). |
| 6 | Yes | The "reader holds the sink open" test has no stated way to hold the read; it can pass vacuously (D4). |
| 7 | Mostly | The parse-error mutation ("read as missing") changes no result for a client file, since missing also reads `off`. It bites only for the site file (parse error `off`, missing no limit) or if the test asserts the error list. |
| 8 | Mostly | Race test with mutation "drop the re-read": `multiprocessing` timing may pass without the mutation (D3). `path` naming `../site` vs "validates the client name": a path is not a client name; the check target is unstated. |
| 9 | Mostly | Test 4 lists `../x` as a refused client, but a file name cannot hold `/`. Collision refusal (needs built keys) belongs in `build_layout`, but `load_layout` also returns `refused`; the split is unstated. No pipeline_state row for a file refused at start. |
| 10 | No | Signature gap (X5). Only 4 of 10 tests carry a mutation; SIGHUP, executor shutdown, retry survival, identical rebuild, and "send log follows version" have none, against Global Constraints. Wake-socket bind has no test. |
| 11 | No | CLI lacks `--manifest` (X10); double error logging. Mutation covers only test 19. |
| 12 | Mostly | "Lands after 3, 9, 10 and 8" incomplete (X11). Bare-3 test duplicates T2's. |
| 13 | No | Per-side manifest missing (X6); mutation inverted (X7); "decide in Step 3" defers an interface that T17's commands need. |
| 14 | Mostly | Print order unspecified (X11). Existing step tests that assert stdout may break on the new line; not mentioned. Test 23 may pass at base; plan handles it by mutation. |
| 15 | Yes | Phases exist. |
| 16 | Yes | Remind the writer: no pointer to the private voice file in any pushed repo. |
| 17 | No | X8, X9; ND per-station names; `$W` vs `<date>` naming trivial. |
| 18 | Yes | Step 3 "push" reads against C1 (X1). |
| 19 | No | Runs after push, contradicting C1 (X1). |

### Conflicts and proposed rulings (spec is the authority)

- **X1. Branch vs main.**  Plan Global Constraints, Task 18 Step 3 and Task 19 header say commit on `main`, push ff, build "after the push".  Ruling: all work on `sink-2b-part1` in the three repos; `../hs-uploader` and `../sigmond` checkouts stay on the branch so the bundled-copy test compares branch HEAD.  Run Task 19 Steps 1-3 (rig sync from the branch) before merging; merge ff to main only after the rig passes and Michael clears it.  Spec 8.5 says "before release", so this fits.
- **X2. T1 vs T2.**  Ruling: T1's stand-in takes `version = max(m.version) + 1` and field `summary`.  T2 drops its duplicate test 11; T1's test carries spec test 11 and its mutation.  Define the two helpers in T1.
- **X3. `ships`.**  Ruling: `record_attempt(..., pipeline="", client="", last_send=True)`; the core passes `getattr(transport, "ships", True)`; the store upserts only when true and the outcome is `acked`/`partial_ack`.  Name it in T3's Interfaces.
- **X4. `SwitchRead`.**  Ruling: add `data: Mapping` (parsed file, `{}` when missing or broken).  `write_switch` re-reads through `read_switch` under the lock; a parse error there raises and writes nothing.
- **X5. Signature.**  Ruling: `pipelines_d.pipelines_signature(dir)` in T9 stats directory and files; `load_layout` calls it before it reads anything; T10 calls it before each pump and compares with `Layout.signature`.  One function, no second copy.
- **X6. Per-side manifests.**  Ruling: T13 adds `--old-manifest`/`--new-manifest` (default `--manifest`) and `--old-pipelines-d`/`--new-pipelines-d` (default none); record as a ruling now.  Run 5 passes the original manifest to OLD and the split one plus directory to NEW.
- **X7. T13 mutation.**  With the entries gone from the legacy manifest, ignoring `--pipelines-d` ends DISAGREE, so the planted test still passes.  Ruling: build the planted-difference fixture with the entries kept in the legacy manifest and the file altering one `batch_limit`; ignoring the directory then reads the stale copy and ends AGREE, and the test fails as spec test 22 intends.
- **X8. Run 5b on ND.**  ND's legacy entries carry no `client`, so precedence never matches and the stale copies collide with the file (refused).  Run 5b cannot AGREE on ND, and run 6's precedence mutation cannot tell the loaders apart there.  Ruling: on ND, run 5b adds `client` (Ruling 3's map) to the stale legacy copies, and the README says so; run 6 uses B4.  Record that a station on a pre-`client` sigmond gets collision refusal, not silence, when a client later ships a file (spec 3.3 assumes rendered `client`); hand to v3.78.
- **X9. ND retry name.**  Ruling: Run 4 passes `--keep-retry-for psk-recorder-pskreporter` for B4 and `--keep-retry-for psk-pskreporter` for ND (names verified in the copies' `pipelines.toml`).
- **X10. Task 11.**  Ruling: `sink status` takes `--manifest` (default `/etc/hs-uploader/pipelines.toml`) and derives `sinks/` from it unless `--sinks` is given; one helper `sinks_dir_for(manifest)` beside `pipelines_dir_for`, used by daemon and CLI.  `sinkswitch` alone logs each file error once per signature; the daemon logs only answer changes; the test counts exactly one ERROR line.
- **X11. Task 12 and print order.**  Ruling: the Task 12 note reads "after Tasks 2-4 and 7-11"; Task 14 prints `" ".join(sorted(words))`.

### Defects a code reviewer would flag in what the plan mandates

- D1. Duplicate tests: bare-`3\n` test in T2 and T12; test 11 in T1 and T2.  Keep the T12 one (it owns the spec mutation) and the T1 one.
- D2. Two private `local` probes (SqliteSource, WsprCycleSource) duplicate logic.  Use one shared helper module, probed once per connection.
- D3. T8 race test: no barrier or lock hook, so the "drop the re-read" mutation may not fail on every run.  Force the interleaving (hold the lock via a pipe, start the second writer, release).
- D4. T6 "index build waits" test can pass vacuously; assert the writer blocked (elapsed time) and that the reader released first.
- D5. T10 tests lack mutations (see B10); T10 wake-socket bind and T9 refused-at-start `pipeline_state` rows have no test.
- D6. Ruling 6: an unrefreshed store writes the old columns silently.  Also refresh in the store's own open path, in addition to the daemon (spec 4.3 allows; the version cannot rise between).
- D7. A client named `site` passes the name rule and aliases `sinks/site.toml`.  Reserve it in `valid_client_name`/loader (spec silent; low cost).
- D8. Commit trailer names Claude Opus 5.5 (1M context) regardless of the model doing the work; use the trailer the session supplies.


### Controller rulings on the scan
- Ruling C3: X1-X11 adopted exactly as proposed above. X1 extends C1: rig steps run from the branch; merge to main only after the rig passes and Michael clears it — cost if wrong: one rebase.
- Ruling C4: D1-D7 adopted as proposed (dedupe tests keeping T1's test-11 and T12's bare-3; one shared `local` probe helper; forced interleaving in T8's race test; T6 index test asserts blocking; add the missing T9/T10 tests and mutations; the store refreshes its version on open as well; reserve the client name `site`) — cost if wrong: small extra test code.
- Ruling C5: D8 rejected — the commit trailer is the one the session supplies (it names the session model, not the subagent's); keep it verbatim — cost if wrong: an attribution line.

## Tasks
- Task 1: dispatched (base 334e71e, sonnet)
- Task 1: implementer DONE 7c57c84 (616 passed; mutation failed); review dispatched. Minor: trailer names Sonnet 5.5 not the session line (C5)
- Task 1: minor (deferred): test pinning the v1 case has no watched mutation and asserts only a count
- Task 1: minor (deferred): `any("would copy" ...)` assertion loose
- Task 1: complete (commits 334e71e..7c57c84, review clean)
- Ruling C6: Task 1's commit names "Claude Sonnet 5.5" in its trailer — accepted as is (it names the model that wrote it; no history rewrite on a branch for an attribution line); later dispatches carry the session's exact lines — cost if wrong: one attribution line.
- Task 2: dispatched (base 7c57c84, sonnet)
- Task 2: implementer DONE_WITH_CONCERNS cfafd49 (624 passed; 3 mutations failed). Controller hand checks on B4 copy: `migrate --check` rc=3 at v2, `migrate` → v3 rc=0, bare capabilities = "3\n".
- Ruling C7: D6 (store refreshes cached version on open) moves to Task 3, which introduces the cache — cost if wrong: none, nothing caches before Task 3.
- Task 2: minor (deferred): test 10 asserts one fill, spec says both keys; fixture at v3 lacks v3 columns
- Task 2: minor (deferred): `_assert_kept_and_split` skips sqlite_master entirely; compare it minus attempts/last_send
- Task 2: minor (deferred): autouse pin in test_migration_split.py weakens test_a_fresh_store_has_the_superseded_table
- Task 2: minor (deferred): test 9 digest covers an empty pipeline_state; seed a row
- Ruling C8: `test_capabilities_bare_prints_3` (test_migration_v3.py) stays; Task 12 takes it over rather than adding a second copy (D1) — cost if wrong: none.
- Ruling C9: carried into Task 3 — gate the last_send upsert on store version ≥3, never on the table's existence (a v2 store opened by new code has the table); test 12's v2 fixture asserts zero rows; add a short test that opening a v2-shape store neither adds columns nor bumps user_version (D10) — cost if wrong: a v2 store gets last_send rows, harmless but against §4.3.
- Task 2: complete (commits 7c57c84..cfafd49, review clean)
- Task 3: dispatched (base cfafd49, sonnet)
- Task 3: implementer DONE c06933c (634 passed; 2 mutations failed); review dispatched
- Task 3: minor (deferred): test_last_send_same_transaction breaks the INSERT, not the upsert; doesn't pin atomicity
- Task 3: minor (deferred): replay assertions loose (>=2 rows); dead lines 508-509 in test_send_log.py
- Task 3: minor (deferred): `getattr(pipe.transport,"ships",True)` repeated 9x in core.py; a helper would centralise it
- Task 3: carry to Task 10: daemon calls refresh_schema_version() at every rebuild.
- Task 3: complete (commits cfafd49..c06933c, review clean)
- Task 4: dispatched (base c06933c, sonnet)
- Task 4: implementer DONE 33716e7 (637 passed; heartbeat 162 passed; mutation failed); review dispatched
- Task 4: minor (deferred): key-set test seeds one row; ORDER BY / multi-row unchecked; tests touch private _conn/_v3
- Task 4: heartbeat envelope ⚠ resolved by controller: heartbeat.py copies only pipelines and cursors (grep), and test_heartbeat 162 passed.
- Task 4: complete (commits c06933c..33716e7, review clean)
- Task 5: dispatched (base 33716e7, sonnet; extra gate: every selecting statement)
- Task 5: implementer DONE_WITH_CONCERNS 0105e87 (650 passed; 6 clause mutations each failed). Concern: probe-once-per-connection misses a `local` column added later — moot on stations, since the v3.70 writer already adds `local` (sigmond CLAUDE.md); carry a reprobe note to v3.78.
- Task 5: minor (deferred): schema.py:449 `_sink_holds` (migrate retry pass) reads pending_uploads without the local clause — out of §5.2 scope; revisit if psk rows ever become local
- Task 5: minor (deferred): wspr discovery clause tested by SQL trace; a behavioral include_psk case exists (all-local WSPR + non-local psk → no batch)
- Task 5: minor (deferred): no pump() call against a no-column sink
- Task 5: minor (deferred → v3.78): probe-once-per-connection can stay stale; re-probe a negative answer before any writer sets local=1; no test of re-probe on new connection
- Task 5: minor (deferred): no today's-data equality test for the dedup path
- Task 5: complete (commits 33716e7..0105e87, review clean)
- Task 6: dispatched (bases hs-uploader 0105e87, sigmond 79dca3d; sonnet)
- Task 6: implementer DONE_WITH_CONCERNS hs-uploader 10c1675, sigmond 9328958 (hs 655 passed; compat 9 passed; sigmond 4900 passed, 2 failed; both mutations failed).
- Task 6: sigmond failure 1 (test_docs_remote_access panel PWLINE) — controller traced to sigmond-appliance d3f27f9 (rob, on main, pre-2b); reported on bus; not ours.
- Ruling C10: sigmond failure 2 (test_uploader_manifest_split ProbeTests::test_asks_the_real_hs_uploader_checkout "3 != 2") is Task 2's intended bump of bare capabilities to 3; Task 14 (sigmond probe) updates that test to expect hs-uploader's SCHEMA_VERSION — cost if wrong: one red test until Task 14.
- Task 6: minor (deferred, triage at final — matters before step 4): test_from_env_and_init_both_take_local never calls Writer.from_env; a dropped pass-through would pass
- Task 6: minor (deferred): D4 test holds a write lock by design; add a comment saying why (a WAL read never blocks)
- Task 6: minor (deferred): PENDING_UPLOADS_DDL continuation indent; fix in hs-uploader, regenerate bundle
- Task 6: complete (commits hs-uploader 0105e87..10c1675, sigmond 79dca3d..9328958, review clean)
- Ruling C11: Tasks 7+8 batched into one dispatch (same new module, read then write), one commit each, one review — cost if wrong: a larger review surface. Dispatched (base 10c1675, sonnet).
- Tasks 7-8: implementer DONE_WITH_CONCERNS 750f352, a29a4ad (705 passed; 4 mutations failed; race test 3/3 under mutation, 10/10 clean)
- Ruling C12: `sinks_dir_for(manifest)` (X10) moves to Task 9, beside `pipelines_dir_for` — cost if wrong: none.
- Tasks 7-8: security review (automated) MEDIUM — sinkswitch .lock created 0644 and flock(LOCK_EX) unbounded: any local user can open it read-only and starve write_switch (the off switch). Ruling C13: enters the fix loop as Important — lock file 0600, LOCK_NB with a bounded retry (~10 s), then raise a clear error; test both — cost if wrong: a writer that times out under real contention (it then says so).
- Tasks 7-8: review Needs fixes — Important: empty/comment-only file refused by write_switch. Ruling C14: effective_state's ValueError on a bad name (reviewer Minor) and sinks/ mkdir mode (Minor 1) join fix round 1, since the spec says failures read off and the daemon will call it — cost if wrong: a little extra code.
- Tasks 7-8: minor (deferred): non-FileNotFound stat errors not logged once; _logged unbounded; emitter TypeError on hand-edited TOML datetime should name the file; redundant `or` in listing assertion; mid-file imports; race test sleep 0.7
- Tasks 7-8: fix round 1/5 dispatched (resume implementer; base a29a4ad): empty-file refusal, lock 0600 + bounded LOCK_NB, effective_state fails closed, sinks/ 0755
- Tasks 7-8: fix round 1/5 (4 addressed, 0 open; commits a29a4ad..732e70a)
- Tasks 7-8: minor (deferred): pre-existing 0700 sinks/ not widened; import order
- Task 7: complete (commits 10c1675..750f352, review clean after round 1)
- Task 8: complete (commits 750f352..732e70a, review clean after round 1)
- Task 9: dispatched (base 732e70a, opus — loader, extra gate)
- Task 9: implementer DONE_WITH_CONCERNS d9a1392 (749 passed; 38 new; all named mutations failed; B4 manifest builds same 7 pipelines)
- Task 9: review Needs fixes — gate (a) PASS on B4 (7) and ND (6) manifests vs 334e71e; Important: broken file silencing every pipeline → run() exits 1 before writing pipeline_state (restart loop).
- Ruling C15: write pipeline_state (incl. refusal rows) before any early return; empty build WITH refusals → ERROR + run idle for Task 10's rebuild; empty build without refusals keeps exit 1 — cost if wrong: an idle daemon instead of a crash loop (visible in pipeline_state).
- Ruling C16: reviewer Minors 1 (FIFO hang), 2 (unhashable legacy client), 3 ("now" refusal exact-match only) join fix round 1 — gate (b) and §3.2 depend on them — cost if wrong: small extra code.
- Task 9: minor (deferred): unreadable pipelines.d revives silenced legacy entries with only an ERROR line; "legacy" literal duplicates LEGACY; unnamed legacy entries renumber when one is silenced; INFO wording on [identity]
- Task 9: note for Task 10: one ERROR per broken file's own signature, not per directory signature; split_refused at rebuild.
- Task 9: fix round 1/5 dispatched (resume implementer; base d9a1392)
- Task 9: fix round 1/5 (4 addressed, 0 open; commits d9a1392..8e33377). Ruling C17: implementer's C15 extension (idle when store check refuses all AND a file was refused) accepted — same reasoning as C15 — cost if wrong: idle instead of exit.
- Task 9: minor (deferred): no test of the store-refused+file-refused idle branch; idle ERROR omits store-refusal reason
- Task 9: complete (commits 732e70a..8e33377, review clean after round 1)
- Task 10: dispatched (base 8e33377, opus — rebuild, strongest-model gate)
- Task 10: implementer DONE_WITH_CONCERNS f204a9b (779 passed x3; 20 new, 20/20 stable; 4 named + 14 D5 mutations failed; B4/ND manifests build identically)
- Ruling C18: a pipeline the store check refused is admitted by a later rebuild once the store version allows (e.g. SIGHUP after migrate) — §3.5 runs the store check at each rebuild, so admission follows the same check — cost if wrong: a pipeline starts without a restart, after a successful migrate only.
- Task 10: review Needs fixes — gates (a) races none, (b) inline_single=False sends nothing new, (c) no-pipelines.d cost = one failed stat per pump; Important I1: failed rebuild consumes the signature.
- Ruling C19: M1 (signal-handler logging; HUP handler before start) and M2 (real SqliteSource.close test + fd count) join fix round 1 — safety and the brief's fd gate — cost if wrong: small extra code.
- Ruling C20: §3.6 "one INFO line per pipeline at each build" read as: fresh pipelines log INFO, plus one summary line per rebuild; an identical rebuild logs DEBUG only — cost if wrong: fewer INFO lines than a literal reading.
- Task 10: minor (deferred): store-refused-then-readmitted path reopens lazily (reachable only if version falls); failing builder reruns each rebuild; `_always` duplicated; daemon.py +440 lines — rebuild machinery could move to its own module; Uploader._held_windows keeps removed entries
- Task 10: note for v3.78: a retry queued under a removed pipeline has no pipeline_state row (waits, replays on return).
- Task 10: fix round 1/5 dispatched (resume implementer; base f204a9b)
- Task 10: fix round 1/5 (3 addressed, 0 open; commits f204a9b..5c30a47)
- Task 10: minor (deferred): rebuild() commits signature before replace_pipelines (no raise path seen); _once may re-log a refused-file ERROR after a failed attempt; persistent failure = full _assemble each pump; Event.set() from signal handler (pre-existing _handle pattern) — tiny deadlock window
- Task 10: complete (commits 8e33377..5c30a47, review clean after round 1)
- Task 11: dispatched (base 5c30a47, sonnet)
- Task 11: implementer DONE 1d41e46 (790 passed; test 19 mutation failed)
- Task 11: review Needs fixes — Important: report_switches ignores Pipeline.governed. Both implementer concerns judged spec-correct (missing client file reads off; client-less legacy entries name no switch). Today's stations: one line, nothing else.
- Ruling C21: Minors 1 (count client-less entries in sink status — ND's case) and 2 (non-zero exit on a manifest read failure) join fix round 1 — operator visibility — cost if wrong: small CLI change.
- Task 11: minor (deferred): refused-file clients absent from sink status unless they have a switch file; stray blank lines in cli.py; test fixture clears sinkswitch privates
- Task 11: fix round 1/5 dispatched (resume implementer; base 1d41e46)
- Task 11: fix round 1/5 (3 addressed, 0 open; commits 1d41e46..efd4a3e)
- Task 11: minor (deferred): no-client count from manifest entries, not built set; sinks_dir_for outside the try in sink status
- Task 11: complete (commits 5c30a47..efd4a3e, review clean after round 1)
- Task 12: dispatched (base efd4a3e, sonnet)
- Task 12: implementer DONE fe42000 (794 passed; mutation failed); controller check: bare='3\n', --features ok
- Task 12: minor (deferred): unused imports (subprocess, sys, SRC) left in tests/test_migration_v3.py; _run_caps replaces whole env
- Task 12: complete (commits efd4a3e..fe42000, review clean)
- Task 13: dispatched (base fe42000, sonnet; X6 per-side options settle the brief's Step-3 decision)
- Task 13: implementer DONE 52ef156 (802 passed; mutation failed 3 tests; real 334e71e OLD vs NEW split → AGREE)
- Task 13: review Approved-with-Important — a side lacking the pipelines.d loader silently ignores a given directory and can end AGREE. Both implementer concerns judged correct (refusal → INCONCLUSIVE; _build_one re-hook keeps power).
- Ruling C22: DISAGREE-wins and OLD-only-refusal tests, plus getattr defaults, join fix round 1 — the tool is the release's proof instrument — cost if wrong: small test code.
- Task 13: minor (deferred): sinks/ not copied beside the manifest (loader doesn't read it yet); tests anchor on a docstring text
- Task 13: fix round 1/5 dispatched (resume implementer; base 52ef156)
- Task 13: fix round 1/5 (3 addressed, 0 open; commits 52ef156..fbe7c9e)
- Task 13: minor (deferred): no-loader test skips when 334e71e is absent (use a fixture tree); shell=True archive with unquoted paths
- Task 13: complete (commits fe42000..fbe7c9e, review clean after round 1)
- v3.77 nested test FAILED Phase D (panel read nothing over guest agent; parser-reset notes); re-run started 12:35Z; reported on bus. Not caused by 2b (branch-only).
- Task 14: dispatched (base sigmond 9328958, sonnet)
- Task 14: implementer DONE d27b09f (sigmond 4916 passed, 1 known appliance-docs failure; 3 mutations failed)
- Task 14: security review (automated) MEDIUM — probe runs `python -m` as root with the caller's CWD on sys.path. Ruling C23: Important, into the fix loop — cwd='/' and `-P` on the --features probe AND the existing bare capabilities probe (and any sibling `-m` probes in uploader_manifest.py); test that a planted hs_uploader/ in the CWD is not imported — cost if wrong: none (venv python is 3.11, -P exists).
- Task 14: review Approved (gate: 20 s timeout acceptable; hold mode now probes but cannot block; no writes). Fix loop opened for C23 (security).
- Ruling C24: review Minors 1 (in-process import of hs_uploader schema in a sigmond test) and 2 (temp-dir leaks) join fix round 1 — cost if wrong: none.
- Task 14: minor (deferred): test 23 sits in FeaturesPrintTests; annotation frozenset[str]; RealCheckoutTests fails if sibling hs-uploader is on a pre-features commit (merge order: hs-uploader before sigmond); hold-mode warning repeats every align (v3.78 log-spam note)
- Task 14: fix round 1/5 dispatched (resume implementer; base d27b09f)
- Task 14: fix round 1/5 (3 addressed, 0 open; commits d27b09f..7c69b51). Controller grep: only the two -m probes exist.
- Task 14: complete (commits 9328958..7c69b51, review clean after round 1)
- Task 15: dispatched (base appliance 5da3a0c, sonnet)
- Task 15: implementer DONE 5ff755f (static tests pass; mutation failed; rig run owed → Task 19)
- Task 15: review Needs fixes — Critical: mig3_facts anchors on unprefixed lines; smd prints `uploader: ` before each migrate line (controller's dispatch gave the unprefixed form — controller error).
- Ruling C25: Minors 1 (require attempts.client), 2 (no fill lines in second update's step output), 3 (G-bis guard on sigmond moved), 4 (name the probe-warning cause) join fix round 1 — first real rig run must not fail spuriously — cost if wrong: a little more rig code.
- Task 15: minor (deferred): empty EB_PRE_VER base → NOT EVALUATED (missed assertion)
- Task 15: fix round 1/5 dispatched (resume implementer; base 5ff755f)
- Task 15: fix round 1/5 (4 addressed, 1 justified-omitted; commits 5ff755f..525f03e). Ruling C26: G-ter no-fill-line assertion omitted — a correct second update prints "kept" lines — cost if wrong: one weaker rig check.
- Task 15: minor (deferred): probe-warning cause message depends on gx_ok merging stderr
- Task 15: complete (commits 5da3a0c..525f03e, review clean after round 1); rig run owed → Task 19
- Task 16: dispatched (bases hs-uploader fbe7c9e, sigmond 7c69b51, appliance 525f03e; sonnet)
- Task 16: implementer DONE_WITH_CONCERNS hs a8311d2, sigmond d544185, appliance 2eca3c1
- Task 16: review — Critical: orchestration.md "creates nothing under /etc/hs-uploader" false; Important: refused-file wording, exit rules, "every source", stale docstrings (writer.py, _bundled.py, core.py governed).
- Ruling C27: do NOT bump orchestration.md's "Verified against" — only one paragraph re-verified — cost if wrong: docs-freshness flags a stale line.
- Task 16: fix round 1/5 dispatched (resume implementer; bases hs a8311d2, sigmond d544185)
- Task 16: fix round 1/5 (9 addressed, 0 open; hs a8311d2..2d3567e, sigmond d544185..a669ed9)
- Task 16: minor (deferred): over-long wrapped lines (README:84,93; CLAUDE.md x3; core.py:217); leftover "stands at version 2" README:290; exit text omits manifest-load exit paths
- Task 16: complete (hs fbe7c9e..2d3567e, sigmond 7c69b51..a669ed9, appliance 525f03e..2eca3c1; review clean after round 1)
- Task 17: dispatched (proof; NEW = hs-uploader 2d3567e; opus)
- v3.77 nested re-run PASSED A-D 13:06:50Z; first failure was a one-off guest-agent read; rig freed on bus.
- Task 17: PROOF — run1 B4 7/7, ND 6/6 AGREE (+nd-running 8376fd8 6/6); run2 two rewinds ×2 stations AGREE; run3 index build 66/51 ms, AGREE on indexed sink; run4 B4 check rc3 = migrate lines, 5-table digest equal, attempts unchanged; ND path A = path B (4df01570ea46a203); OLD/NEW on migrated stores 8/8 AGREE; run5/5b AGREE; run6 mutations DISAGREE. README re-run block reproduced. Record: ~/hamsci/proofs/sink-2b-part1-20261021/README.md. Copies' SHA256SUMS OK before/after.
- Ruling C28 (corrects X9): `--keep-retry-for` names the INHERITING pipeline, so both stations take `psk-recorder-pskreporter`; X9's `psk-pskreporter` for ND was wrong (ValueError) — cost if wrong: none, ND held no retries.
- Task 17: note for v3.78: idx_pending_uploads_producer is never chosen by the planner (producer OR-clause keeps it on the target index) — costs insert time, buys no read; revisit with cleanup (v3.79) or drop.
- Task 17: coverage gap: no queued retry and no local=1 row in either copy — those paths rest on unit tests + mutations.
- Task 17: complete
- Task 18: final whole-branch review dispatched (opus); main unchanged upstream in all three repos since the branch base.
- Task 18: final review — ready to merge with fixes; no Critical. Central claim + all §10 rows hold; proof runs 1/6 reproduced (AGREE, AGREE, DISAGREE, DISAGREE).
- Ruling C29: one fix wave = I1 (rig: last_send may pre-exist), M1, M2 (sink status wording with no sinks/), M3 (empty array), M4 (SIGHUP doc), T2 test 10 both keys, T6 from_env test. M5 (requires-python) deferred — changing it disturbs uv.lock — cost if wrong: a 3.10 interpreter fails on -P/syntax at runtime rather than at install.
- Ruling C30: merge order hs-uploader → sigmond → sigmond-appliance (sigmond tests read ../hs-uploader).
- Must-fix-before-step-4 list (from final review): T5 _sink_holds local clause; T5 re-probe a missing local column; T6 from_env (if not done); T9 unreadable pipelines.d revives silenced legacy; X8 client-less sigmond → refusal not silence; v3.78 emitter errors name the file; producer index decision.
- Task 18: fix wave dispatched
- Task 18: controller re-ran proof runs 1 and 6 against hs-uploader 29a0806 (README block, NEW tree swapped): AGREE, AGREE, DISAGREE, DISAGREE; copies OK.
- Task 18: fix wave re-review — 7/7 addressed, no new Critical/Important (commits hs-uploader 2d3567e..29a0806, appliance 2eca3c1..9c691d8).
- Task 18: minor (deferred): rig probe's `type = "table"` relies on SQLite DQS fallback (fails closed); a failed second query reads "no last_send table"; requires-python >=3.10 vs real 3.11 floor
- Task 18: complete
- Task 19: NOT STARTED — needs an image built from the branch (golden VM pulls origin/main), then nested + update tests, then the B4 bench; the bench needs Michael's go and a bus announcement. Stopped here for Michael.

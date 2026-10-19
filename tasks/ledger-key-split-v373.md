# Ledger of the key-split build (v3.73 row 1), kept for its rulings and deferred minors

Copied from the git-ignored SDD workspace on 2026-10-19 when the plan finished. Agent ids are run bookkeeping.

# SDD ledger — plan: /home/mjh/hamsci/repos/sigmond/tasks/plan-key-split-v373.md
Spec: /home/mjh/hamsci/repos/sigmond/tasks/spec-key-split-v373.md (binding). Develop on main (Michael's standing rule; he chose subagent-driven 10-19).
Bases at start: hs-uploader 3090b91, sigmond 6ed87c2, sigmond-appliance 4197f3e.

## Preflight scan
| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1/T5 schema.py | T1 stamps SCHEMA_VERSION (1); T5 raises it to 2 | consistent |
| T1/T4 | T4 reads user_version, needs 2 for producer pipelines | consistent |
| T2/T5 | T5 decodes the old cursor with SqliteSource's `_Cursor` codec (sqlite.py:91) | plan says "cursor codec"; exists as _Cursor — fine |
| T5 "sink holds the rows" | id = last_id+1 test | ids interleave across targets and trim deletes; exact-id test is fragile → RULING 2 |
| T4/T8 capabilities | T4 prints literal `2` before T5 lands migration 2 | main is pullable by stations mid-plan → RULING 1 |
| T3/T5 | T3 changes advance_cursor API; T5 writes SQL directly | no conflict |
| T4/T6, T5/T6 | pipeline_state (T4), superseded_keys (T5) read by T6 read-only on stores lacking them | T6 must tolerate absence (plan says so) |
| T8/T9/T10 | uploader_manifest.py edited by T8, T9; commands/uploader.py by T10 | sequential; consistent names (hs_uploader_store_version, ProbeFailed, inprocess_senders) |
| T7 | snapshots at /home/mjh/v370-t10/ | present |
| T11 | rig test runs only on rig | owed before build |
| each task self-consistency | tests vs code vs files | T4's capabilities test asserts `2` → follows RULING 1; others consistent |

Ruling 1: `hs-uploader capabilities` prints schema.SCHEMA_VERSION (1 until Task 5 lands, then 2), not a literal 2 — a station pulling main between Task 4 and Task 5 must never get sigmond's split against an hs-uploader without migration 2 — costs nothing if wrong (value equals 2 from Task 5 on).
Ruling 2: in migration 2, "the sink still holds the retry's rows" means a psk.spots row with id <= the old cursor's last_id still exists in sink.db (trim deletes oldest first, so none after it was trimmed); otherwise move the retry — exact id+1 breaks on id gaps from other targets — if wrong, a retry gets moved instead of dissolved: one batch of one producer re-sent, which the spec already admits.
Ruling 3: work proceeds on main; each task's commits land on main and are pushed after its review passes, not before — a station pulling main sees only reviewed tasks — cost if wrong: none beyond later push.
Task 1: dispatched (impl a585cc75fee07e756, base hs-uploader 3090b91)
Task 1: implemented aa3361e (DONE); review dispatched
Task 1: ⚠ resolved by controller — missing parent dir: CLI guards it (path.parent.is_dir()); a direct schema.migrate() into a missing dir raises from sqlite, acceptable. SCHEMA_VERSION = max(MIGRATIONS) = 1 confirmed in diff (schema.py:142).
Task 1: minor (deferred): migrate docstring says root+empty file raises FileNotFoundError (it raises MigrateError); module docstring line 96 "raise SCHEMA_VERSION" stale; migrate --check on missing store untested; tests hard-code version 1 (Task 5 must update).
Task 1: complete (commits 3090b91..aa3361e, review clean) — pushed
Task 2: dispatched (impl aaef8de827308ab4a, opus, base hs-uploader aa3361e)
Task 2: implemented a9ab1f1 (DONE_WITH_CONCERNS: D12 mode clause not limited to db psk); review dispatched
Task 2: ⚠ resolved — rows without radiod_id would match no instance source; v3.73 renders no instance pipeline, so nothing strands now. Carried to v3.74 (per-instance rendering must place rows lacking radiod_id). psk-recorder writes radiod_id (ch_tailer.py:460).
Ruling: Task 8 keeps the per-producer mode clause in extra_where (ft8,ft4 / msk144) beside `producer` — the shared pipeline today ships exactly these exact-match modes, so '' rows with padded or missing modes stay unshipped as they are today (no regression); psk pipelines run delete_on_commit=false so commit() deletes nothing — cost if wrong: a few pre-v3.70 odd-mode rows, already aged out (60 min retention), never ship.
Task 2: minor (deferred): D12 clause not limited to db psk (guard before any non-psk pipeline gets a producer); SQL trim() strips only spaces vs Python strip(); malformed payload_json now raises in scoped query; column probe cached for source lifetime, not per connection; tautological last assertion in test_commit_deletes_only_own_producer; D12 constants duplicated from sink/writer.py.
Task 2: complete (commits aa3361e..a9ab1f1, review clean) — pushed
Task 3: dispatched (impl abb89ca532a3cb376, opus, base hs-uploader a9ab1f1)
Task 3: implemented 19c1d7e (DONE_WITH_CONCERNS: unthrottled 'acked, record held' INFO; tie detection asks comparator both ways); review dispatched
Task 3: review — spec ✅, 1 Important (unthrottled "acked, record held" INFO). ⚠ fourth advance site in core.py: reviewer's spy test (wm.bare == 0) covers it; controller accepts.
Task 3: minor (deferred): comparator called twice — a future comparator answering False for incomparable values would read as a tie (REFRESHED, silent); comparator+logging run under the db-wide write lock (keep comparators cheap; docstring note).
Task 3: fix round 1 sent (throttle INFO; also tighten weak assertion; keep original exception if rollback raises)
Task 3: fix round 1/5 (3 addressed, 0 open; commits 19c1d7e..ef0ff86)
Task 3: minor (deferred): per-key independence of core's held-window dict untested; import order in core.py.
Task 3: complete (commits a9ab1f1..ef0ff86, review clean) — pushed
Task 4: dispatched (impl a8d8b4d9b01a3ec97, sonnet, base hs-uploader ef0ff86; carries Ruling 1)
Task 4: implemented 1e5d6a3 (DONE; all-refused → exit 1 like 'no pipelines'); review dispatched
Task 4: ⚠ resolved — refusal can trigger only after Task 5 raises SCHEMA_VERSION; message pointing at migrate is then correct.
Ruling 4: pipeline_state must reflect the current manifest only — Task 6 changes the start-time write to replace the table (delete rows of pipelines no longer built), because Task 6's orphaned-dead-letter count depends on it (a stale psk-pskreporter row would hide its orphans) — cost if wrong: a removed pipeline's history row disappears from the report.
Ruling 5: Task 10's manifest step must clear a `failed` hs-uploader unit (systemctl reset-failed) before it starts the daemon, since an all-refused daemon exits 1 and hits the start limit — cost if wrong: one extra systemctl call.
Task 4: minor (deferred): all-refused exit-1 path untested; weak assertion `"2" in reason` (test_daemon.py:434); dry-run writes pipeline_state.
Task 4: complete (commits ef0ff86..1e5d6a3, review clean) — pushed
Task 5: dispatched (impl a6cb50b2fb1039908, opus, base hs-uploader 1e5d6a3; carries Ruling 2)
Task 5: implemented 2d5c6f7 (DONE_WITH_CONCERNS); review dispatched
Task 5: ⚠ resolved — sink trim deletes ORDER BY id oldest-first (sigmond storage_trim.py:344-347), so Ruling 2's premise holds; daemon-stopped and --sink/--keep-retry-for passing belong to Task 10 (carry).
Ruling 6: superseded_keys also records a row when the new key already existed — §4.5 wants status to name every successor of a frozen key, whether copied now or before — cost if wrong: a status reader may read "copied" where the record was kept; harmless to sending.
Task 5: minor (deferred, FLAG FOR FINAL REVIEW): no end-to-end test replays a moved retry (move → replay → ack advances keep key to cursor_after → other pipeline re-reads L..X); Migration.apply returns lines + preview field beyond brief; broken comment wrap test_watermark_schema.py:1829; stale CLAUDE.md line 36-37 (constructor never touches user_version); `now` computed on preview runs.
Task 5: complete (commits 1e5d6a3..2d5c6f7, review clean) — pushed
Task 6: dispatched (impl af290af2685af841e, sonnet, base hs-uploader 2d5c6f7; carries Ruling 4)
Task 6: implemented a714152 (DONE_WITH_CONCERNS: merged into existing summarize().pipelines; orphans 0 when pipeline_state empty; heartbeat drops orphaned_dead_letters); review dispatched
Ruling 7: summarize() carries client/refused_reason inside its existing per-pipeline `pipelines` list (no second list) — the brief's separate list would collide with an existing key; no later task expects a separate list — cost if wrong: one rename.
Ruling 8: sigmond heartbeat _map_uploads must copy summarize()'s top-level orphaned_dead_letters and fix its stale "idle pipeline is ABSENT" comment (~heartbeat.py:466) — folded into Task 10's dispatch as a small extra — cost if wrong: the fleetboard misses orphan counts.
Task 6: minor (deferred): status prints an aggregate orphan line, not per-pipeline names; second ro connection in _cmd_status uncommented; mid-file imports test_backlog.py ~336; empty-states replace untested.
Task 6: complete (commits 2d5c6f7..a714152, review clean) — pushed
Task 7: dispatched (impl a437417ba2d020620, sonnet, base hs-uploader a714152)
Task 7: implemented aa8a3f1 (B4 11836=11786+50, ND 8337=8287+50, 0 overlap/missing; added 'scope alone' check so the mutation can fail); review dispatched
Task 7: ⚠ resolved — no-column snapshots split by D12 mode rule (Task 2's deliberate column-absent branch, reviewed); commit body records B4 and ND output (checked). Hand-copied old/new specs vs sigmond's render: cross-check in final review after Task 8.
Task 7: minor (deferred): input-untouched assertion covers sink main file only; --store copied but unused; mutation patches _scope_clauses wholesale; old block hard-coded (add pointer); drain_ids lacks a repeat-cursor guard.
Task 7: complete (commits a714152..aa8a3f1, review clean) — pushed
Task 8: dispatched (impl aa22ce79826c6cd1d, opus, base sigmond 6ed87c2)
Task 8: implemented 082218b (DONE_WITH_CONCERNS: probe exit2→0 only on 'invalid choice'; shared pipeline now carries client; suppressed_pipelines probes, falls back on failure); review dispatched
Task 8: ⚠ resolved — ProbeFailed handling by name belongs to Task 10 (carried).
Ruling 9: Task 10's manifest step probes once and passes store_version to both generate() and suppressed_pipelines(); it probes only when the effective mode renders client pipelines (hold/heartbeat-only renders without a probe) — spec's "probe fails → nothing new" protects the key split, which hold mode does not render — cost if wrong: a hold-mode host renders a heartbeat-only manifest while hs-uploader is broken, which is what hold means anyway.
Task 8: minor (deferred): the probe trusts any integer (negative/absurd renders shared).
Task 8: complete (commits 6ed87c2..082218b, review clean) — pushed
Task 9: dispatched (impl a8460289703ab215a, sonnet, base sigmond 082218b)
Task 9: implemented 289db8a (DONE_WITH_CONCERNS: instances disagree → keep daemon pipeline; drift rule both directions; env parser refactor); review dispatched
Task 9: ⚠ resolved — real `systemctl show -p EnvironmentFiles` prints `EnvironmentFiles=/etc/default/ssh (ignore_errors=yes)` (checked on the devbox), the shape the parser assumes.
Task 9: minor (deferred): meteor-scatter starts no sender without callsign/grid even in direct mode, which an env-only reading cannot see (document); awkward `if mode == "upload": pass` flow (uploader_manifest.py ~609); tempdirs not cleaned in tests; drift "missing" direction heuristic for hold mode.
Task 9: complete (commits 082218b..289db8a, review clean) — pushed
Task 10: dispatched (impl afa23a05fe2c75cbf, opus, base sigmond 289db8a; carries Rulings 5, 8, 9)
Task 10: implemented a4661a9 (DONE_WITH_CONCERNS: pending detected from migrate --check wording since hs-uploader --check always exits 0; bring-up try-restart; apply exits 1 on step failure; align timeout 300 s); review dispatched
Task 10: review — spec ✅, 1 Important (B1 guard coupled to --check wording).
Ruling 10: reviewer Minors 1, 2, 6 (+3 as the means to 2) raised to Important — each can leave hs-uploader stopped, or a store unmigrated, with no signal, which is the failure class this whole plan exists to prevent — cost if wrong: one extra fix round.
Task 10: ⚠ resolved by ruling — bring-up Stage 1 apply on a host whose hs-uploader clone failed now reports INCOMPLETE, as the spec requires (a broken uploader must not pass silently).
Task 10: minor (deferred): apply test asserts on source text; side file can be left after an exception (harmless).
Task 10: fix round 1 sent (4 findings; hs-uploader --check exit 3 + contract test; restore daemon on exception; align budget; store-path fallback)
Task 10: fix round 1/5 (4 addressed, 0 open; commits sigmond a4661a9..6d839a2, hs-uploader aa8a3f1..72a7888)
Task 10: minor (deferred): align comment gives 90 s for stop, true worst case 180 s (600 s still covers; headroom ~8 instance units); contract test's wording subtest never reaches the parser on pending stores; contract test errors (not skips) on a sibling without `capabilities`; a guessed nonexistent store dir makes every apply stop/restart the daemon; D13 env reads 10 s per instance unit; a signal between stop and start still leaves the daemon stopped.
Task 10: complete (commits sigmond 289db8a..6d839a2 + hs-uploader 72a7888, review clean) — pushed
Task 11: dispatched (impl a75201aac4352711d, sonnet, bases hs-uploader 72a7888, sigmond 6d839a2, appliance 4197f3e)
Task 11: implemented hs-uploader c7385e6, sigmond 9c3a616, appliance 206dbbe (rig e2e owed); review dispatched
Task 11: review — 1 Important (rig journal awk cannot match the unit Description). ⚠ rig e2e owed: recorded here — OWED BEFORE THE v3.73 BUILD: run test-update-v3.sh end to end on the rig.
Task 11: minor (deferred): G-ter counts rows but not cursor stability; README does not say sigmond passes --sink/--keep-retry-for only at store version >= 2.
Task 11: fix round 1 sent (journal stop/start detection + static test; SQL params; orchestration.md step 4 + reflow)
Task 11: fix round 1/5 (3 addressed, 0 open; appliance 206dbbe..aac0e0f, sigmond 9c3a616..9599505; old awk verified to fail the new static test)
Task 11: complete (hs-uploader c7385e6, sigmond 9599505, appliance aac0e0f; review clean) — pushed. Rig e2e still OWED before build.
Final review: dispatched (ab7513b9936919fb1, opus) over hs-uploader 3090b91..c7385e6, sigmond 6ed87c2..9599505, appliance 4197f3e..aac0e0f
Final review: ready with fixes — I1 (retry pass can skip rows on a v2 store without producer keys), I2 (no in-repo moved-retry e2e test), M1-M8.
Ruling 11: I1 fixed by copying the old record to any MISSING producer key on every migrate (never touching an existing key; not the S1 catch-up), and allowing a retry delete only when both producer keys exist — D9 "re-send, never skip" — cost if wrong: re-send bounded by 60-min retention.
Ruling 12: fix wave covers I1, I2, M1, M2, M7, M8; M3 and M4 recorded in the spec (§9, §9a); M5, M6 deferred (loud and safe / heartbeat INCONCLUSIVE only) — cost if wrong: M6 shows INCONCLUSIVE on a hold-mode station with a stuck retry.
Final fix wave: dispatched (a059f7040525c4fe9, opus)
Ruling 13: the missing-key fill does not run on a store newer than the code (a rolled-back hs-uploader); the delete guard still blocks deletion there, so no row can skip — cost if wrong: on a newer store a retry moves instead of dissolving (one batch re-sent).
Final fix wave: 6 addressed, 0 open (hs-uploader c7385e6..334e71e, sigmond 86ab074..4adc3e1); Ruling 13 upheld; §9 note added.
Final fix wave: minor (deferred): _check assume_filled edge for a retry with a foreign source_id (no practical effect); unreadable meteor-scatter config WARNs on every render; fill creates a meteor-scatter key even when D13 drops its pipeline (a later return re-sends ≤ retention).
PLAN COMPLETE. Owed before build: rig e2e of test-update-v3.sh.

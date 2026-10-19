# Final review: the send-record key split (v3.73 row 1)

Reviewer: final whole-branch review, 2026-10-19.
Ranges: hs-uploader 3090b91..c7385e6, sigmond 6ed87c2..9599505,
sigmond-appliance 4197f3e..aac0e0f.  I read the three prepared diffs in passes
(hs-uploader source, then sigmond source, then the rig script and tests), and
the spec, the plan, D9/D10/D13/D17 and the ledger.

What I ran (read-only; no repository changed, `git status` clean in each):

- hs-uploader suite: 601 passed, 1 skipped.  sigmond suite: 4809 passed.
- Two scratch tests in the session scratchpad, run against hs-uploader's own
  fixtures with `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`:
  - `e2e/test_moved_retry_e2e.py`: replays a moved `psk-pskreporter` retry end
    to end, keep pipeline psk-recorder and meteor-scatter in turn.  **Passes**:
    the retry replays once on the keep pipeline, the keep key advances to the
    retry's `cursor_after`, the keep pipeline then ships only rows after it,
    and the other pipeline re-reads its own rows from the old record.  The
    behaviour matches spec §4.4 item 4.
  - `e2e/test_v2_born_shared.py`: a store born at version 2 that a shared
    pipeline then used.  **Confirms finding I1**: the retry pass deletes the
    retry, and rows 6-10 (the retry's rows) never ship on either pipeline.

## Strengths

- The names agree across all three repos.  sigmond's `PSK_PRODUCERS`, its
  rendered `source.producer` and top-level `client`, hs-uploader's
  `NEW_PSKREPORTER_PIPELINES`, `_D12_BY_PRODUCER`, the factory's
  `spec.get("producer")` and `entry.get("client")` (both paths), and the rig's
  greps all use the same strings.  The flags agree too: sigmond passes
  `--db/--sink/--keep-retry-for/--check`, migrate accepts each, and
  `--keep-retry-for` comes only from names in the rendered manifest.  The exit
  codes agree: `--check` exits 3 and `_CHECK_PENDING` is 3; `capabilities`
  prints a bare integer and sigmond parses it as one.
- An old hs-uploader factory ignores unknown keys (checked at 3090b91), so
  the new `client` key on the shared pipeline is safe when hs-uploader lags.
  The lag path (probe 0, shared render, only `--db` passed, the old
  `--check` read by its wording) holds together.
- Migration 2 and the retry pass run in one `BEGIN IMMEDIATE` and decide
  again under the lock.  A failed migrate rolls back whole.  The old row stays
  (D9).  The retry pass never deletes without the sink proving the rows exist,
  as long as the new keys exist (see I1).
- `advance_cursor` holds the read, the comparison and the write in one
  transaction.  The REFUSED, REFRESHED and WRITTEN_UNCHECKED cases follow
  spec §4.6.  Nothing outside hs-uploader calls the old method (grep across
  `repos/`).
- The manifest step restores the daemon on every failure branch, an
  exception included.  It gives the 600 s align budget with the waits added
  up, and it kills the migrate's process group.
- The instance slot stays open (requirement 4).  `SqliteSource` forms
  `@P/R`.  `superseded_keys` takes many new keys per old key.
  `_split_for_producer` keeps any `radiod_id` a future block declares.
  `split_refused` gates on `producer` alone.  Nothing renders per instance,
  and nothing forbids it.
- The prove script's real-snapshot run (B4 and ND, with a mutation that
  fails) gives the split an evidential footing that unit fixtures could not.

## Issues

### Critical (must fix)

None.

### Important (should fix)

**I1. The retry pass deletes a retry, and the split starts a pipeline at
"now", when a producer key is missing on a version-2 store.  Rows get skipped.**

- Where: `hs-uploader/src/hs_uploader/watermark/schema.py:324-347`
  (`_plan_retries`, the delete branch at :339).  The copy runs only once,
  inside migration 2 (`_split_pskreporter`, :187).  The store stamps version
  2 on a file it creates (`watermark/sqlite.py:173`).  Both deploy.toml
  blocks set `start_at = "now"` (`psk-recorder/deploy.toml:125`,
  `meteor-scatter/deploy.toml:151`).
- What goes wrong: the delete branch assumes each new pipeline will re-read
  from the old record or earlier.  That holds only when both producer keys
  exist.  When a key is missing, its pipeline anchors at the producer's
  current max id (`sources/sqlite.py:809`).  The deleted retry's rows never
  ship, and neither do the rows the shared pipeline had not sent before the
  stop.  The scratch test reproduces it: retry deleted; rows 6-10 lost on
  both pipelines.
- How a station gets there: a version-2 store with no producer keys, which a
  shared pipeline then uses.  Three ways:
  1. The daemon creates the store (fresh host, or an operator deletes
     `watermarks.db`) while a sigmond older than v3.73 renders
     `psk-pskreporter`.
  2. Migration 2 runs with no old pskreporter record yet, then a shared
     pipeline runs.  A v3.72 sigmond's manifest step runs migrate on a v3.73
     hs-uploader, so this happens whenever hs-uploader updates ahead of
     sigmond on a station whose pskreporter pipeline has not yet acked.
  3. A rollback lands before either split pipeline has acked once.
  A later v3.73 manifest step then renders the split.  Migration 2 never runs
  again, and nothing creates the missing keys.
- Why it matters: D9 says "re-send, never skip", and spec §4.4 item 4 says
  "repeating it cannot skip a row".  This path breaks both.  The window it
  can reach is small on today's fleet, but it is a skip, the one failure
  this release exists to rule out.
- Fix (small): make the copy idempotent and run it on every migrate, beside
  the retry pass.  For each old `sqlite:psk.spots` pskreporter record, copy it
  to each missing producer key and record the pair in `superseded_keys`.
  That only fills a missing key with a position the old pipeline has acked
  through.  It never moves an existing key, so it is not the catch-up that
  review S1 removed.  At the least, the retry pass should treat a missing new
  key as "cannot delete".  Add the scratch test as a regression test.  This
  is a gap in the spec, not only in the code: §4.4 item 4 takes the new keys'
  existence for granted.
- Before the build: yes.

**I2. No in-repo test replays a moved retry (the ledger's FOR FINAL REVIEW
item, Task 5).**

- Where: `hs-uploader/tests/test_migration_split.py`.  The move tests stop at
  the row rewrite, and `test_end_to_end_no_resend_no_skip` queues no retry.
- I verified the behaviour in a scratch test (above, both keep targets): it
  is correct.  The repo still has no test that would fail if, say, a later
  change dropped the `source_id` rewrite in `_apply_retries`.  That rewrite
  is load-bearing.  Without it the replay ack would advance the frozen old
  key (`core.py:617` uses `deliverable.source_id`).  Then the keep pipeline
  would re-read and re-send the whole batch.
- Fix: commit the scratch test (it uses only existing fixtures).  Watch it
  fail with `source_id = ?` removed from the UPDATE.
- Before the build: yes.  It costs minutes, and it guards spec test 2.

### Minor (nice to have)

- **M1.** D13 drops meteor-scatter's daemon pipeline whenever its delivery
  mode reads `direct`, unset included (`sigmond/lib/sigmond/inprocess_senders.py:120`).
  But meteor-scatter starts no sender without a callsign and a grid
  (`meteor-scatter/src/meteor_scatter/core/recorder.py:670`).  So an instance
  with an unset mode and no callsign posts nothing, and no daemon pipeline
  covers it either: a silent loss, the case spec §5.3 says to avoid.
  Today's fleet sets `off`, so the function returns nothing.  (Ledger T9.)
- **M2.** A keyless (pre-v3.70) retry that gets moved carries no
  `cursor_after`, so its ack advances nothing (`core.py:617`).  Both
  producers then re-send that batch, more than spec §4.4's "one batch of one
  producer".  Such retries would have dead-lettered long ago.  Fix the
  sentence in the spec or the docstring.
- **M3.** sigmond reads `capabilities` as `int(stdout.strip())`
  (`sigmond/lib/sigmond/uploader_manifest.py:463`).  If step 2b changes that
  output (JSON, or a second line), a v3.73 sigmond against a v3.74
  hs-uploader gets `ProbeFailed` and keeps its manifest forever.  Step 2b
  must keep a bare integer, or move to a new subcommand.  Add this to spec §9.
- **M4.** Ruling 2 takes "trim deletes oldest first" to mean id order.
  The trim actually deletes by `queued_at` (`sigmond/lib/sigmond/storage_trim.py:321`),
  and `queued_at` is the writer's wall clock.  A large backward clock step
  breaks the equivalence, and `_sink_holds` (`schema.py:299`) could then
  vouch for a retry whose later rows are gone.  This needs a step larger
  than the retention window's margin.  Note the assumption in the docstring.
- **M5.** When `systemctl show` fails, `_daemon_store_path`
  (`sigmond/lib/sigmond/commands/uploader.py:162-176`) guesses the default
  path.  Migrate may then create a stray store there and land the split,
  while the daemon's real store stays at version 1.  The daemon then refuses
  the producer pipelines, a loud and safe hold until the next run.  This
  joins the T10 deferred minor ("a guessed nonexistent store dir").
- **M6.** A retry that stays under `psk-pskreporter` keeps the heartbeat at
  INCONCLUSIVE ("deliverables retrying") until a migrate with a keep
  pipeline runs.  That happens in hold mode, or when D13 drops both producer
  pipelines.  The cause is visible, but it lingers.
- **M7.** `producer` and `radiod_id` go into the key unchecked
  (`sources/sqlite.py:239`).  A `/` or `@` inside one would make
  `@P/R` ambiguous.  Step 2b should validate them before it renders
  instances.
- **M8.** The `superseded_keys` primary key leaves out `new_dest_id` and
  `new_table` (`watermark/sqlite.py:95`, `schema.py:166`).  With
  `INSERT OR IGNORE`, a future migration that changes a destination under the
  same new source would lose its row without a word.  That does not matter
  under §3.1's "only the source part changes".  Record it for step 3.

## Seams checked end to end (requirement 1)

| Sequence | Result |
|---|---|
| Fresh install, v3.73 both | Store missing → `--check` says "not found" → sigmond counts it pending → stop, migrate as hsupload creates and stamps version 2, land the split, start.  New keys from `start_at = now`.  No skip. |
| Update from v3.72 (store v1, old record L) | The daemon may restart on new code with the old manifest first: no producer pipeline, nothing refused.  The step renders the split, `--check` exits 3, then stop, migrate 2 (copy L to both keys), land, start.  Test 1 and the prove script cover it. |
| Failed migrate | One transaction rolls back; side file dropped; daemon as found; exit 1; apply/bring-up report it.  Old manifest still matches the old keys. |
| Migrate succeeds but sigmond times out | Store at v2, old manifest kept, shared pipeline advances the old key; the next run lands the split from the copies.  A re-send, bounded by the 60 min retention.  Allowed by D9. |
| Rollback (sigmond, with or without hs-uploader) | The old renderer writes `psk-pskreporter`; the daemon resumes from the frozen L; a v3.72 migrate leaves the v2 store as it stands.  A re-send, bounded. |
| Second update after rollback | Keys exist: re-send from the pre-rollback records; a retry is deleted or moved safely (both cases traced).  **Keys missing: I1.** |
| hs-uploader lagging sigmond | Probe 0, shared render plus `client` (ignored), only `--db`; one bounce for the `client` text change.  Safe. |
| hs-uploader ahead of sigmond | A v3.72 step runs v3.73's migration 2.  Safe when an old record exists; **I1 when none does**. |
| hs-uploader moved back by hand | Both pipelines share the old key; MSK144 may be skipped, as spec §6 admits; the next probe re-renders shared. |

## Step 2b (requirement 3)

Step 2b needs to undo nothing beyond what spec §9 plans: retire sigmond's
§5.1 rewrite, and move D13 to the switch files if 2b so decides.  It must
also keep the `capabilities` output contract (M3), validate key parts (M7),
and run the store check at each rebuild (§9 already says so).  The fix for
I1 (an idempotent copy for missing keys) also gives step 2b's
producer-to-instance migration a pattern to reuse.

## Deferred minors from the ledger: triage

Fix before the v3.73 build:
- T5 FLAG: no test replays a moved retry → I2.  Behaviour verified correct;
  commit the test.
- T9: meteor-scatter in direct mode without callsign/grid → M1.  At least
  document it in `inprocess_senders` and in the drift rule's message.
  Better, count an instance as a sender only when its callsign and grid
  resolve.  (If the executor rules it a documentation item, it may wait.)

May wait (no effect on what gets sent):
- T1: the docstring on root plus an empty file names the wrong exception;
  `--check` on a missing store is untested; tests hard-code the version.
- T2: the D12 clause does not check `db == psk` (guard it before any
  non-psk producer); SQL `trim()` strips only spaces (the Ruling keeps the
  exact-mode filter, so it does not matter now); a malformed `payload_json`
  raises (extra_where already did that before); the column-probe docstring;
  a tautological assertion; the D12 constants appear twice.
- T3: the comparator runs twice (a tie read as "incomparable"); the lock
  covers the comparator; per-key window independence is untested; import
  order.
- T4: the all-refused exit-1 path is untested; a weak `"2" in reason`
  assertion; `--dry-run` writes `pipeline_state` (nothing in sigmond runs a
  dry run).
- T5 rest: `Migration.apply` returns lines (fine); a comment-wrap typo; a
  stale CLAUDE.md line; `now` computed on preview.
- T6: aggregate orphan line; second ro connection uncommented; mid-file
  imports; empty-states replace untested.
- T7: all five (test-hygiene items in the proof script).
- T8: the probe trusts any integer (a negative value renders the shared
  form, which is harmless).
- T9 rest: the awkward `if mode == "upload": pass` flow; tempdirs; the
  drift heuristic in hold mode.
- T10: the apply test asserts on source text; the side file can be left
  after an exception; the align comment's 90 s versus 180 s (600 s covers
  both); the contract test's wording subtest and its error-not-skip; a
  guessed store dir bounces the daemon on every apply (see M5); D13 reads take
  10 s per unit; a signal between stop and start leaves the daemon stopped.
- T11: G-ter counts rows but not cursor stability; the README omits that
  sigmond passes `--sink/--keep-retry-for` only at store version 2 or above.

Still owed before the build (ledger): `test-update-v3.sh` end to end on
the rig.  My review covered only `bash -n`-level reading of it, nothing run.

## Declined to judge

- The `queued_at`-versus-id ordering across a clock step (M4): I noted it,
  but judging trim semantics belongs to the sink-control plan, not this row.
- A client's in-process sender writing the old, superseded key (J16): spec
  N9 says J16 does not arise while no client sender runs, and §5.3 drops the
  daemon pipeline in that case.  Out of this row.
- Two pskreporter TCP sessions under one callsign after the split (spec §7
  says "on the wire nothing changes", but the connection count does change):
  pskreporter-side behaviour falls outside the plan; the payloads stay the same.
- The sink path the daemon's sources use versus `um.SINK_PATH`, which
  sigmond passes as `--sink`: I assumed the fixed default.  A mismatch can
  only move a retry instead of deleting it, which is safe.
- The bring-up Stage 1 apply turning INCOMPLETE when the hs-uploader
  install fails: the controller ruled on it (Task 10).
- Rig script logic beyond reading: it runs only on the rig, and the rig run
  is owed.

## Assessment

**Ready to merge?** With fixes.

**Reasoning:** The three repos agree on names, keys, flags and exit codes.
The ordered manifest step and forward-only records hold up on every
sequence I traced but one.  I1 lets the retry pass delete a retry, and the
split start a pipeline at "now", on a version-2 store with no producer keys.
That skips rows, which D9 forbids.  The fix is small and should land with
I2's regression test before the v3.73 build.  The rig run is still owed.

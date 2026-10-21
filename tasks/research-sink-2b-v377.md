# Sink control step 2b: research map

Written 2026-10-21 from code at origin/main.  Nothing here changed a repository or a station.  The
map proposes a split of step 2b into three images (§K) and ends with four questions for Michael.

## How to read this map

- Every path starts at `/home/mjh/hamsci/repos/`.  `hsu/` abbreviates `hs-uploader/src/hs_uploader/`.
- Commits read: hs-uploader 334e71e, sigmond 75a4da9, psk-recorder 0bb62df, meteor-scatter 6156e53,
  wspr-recorder d171d24, mag-recorder 55a450c, sigmond-appliance bdc0467 (origin/main; the local
  worktree lags nine commits).
- "Design" means `sigmond/tasks/plan-sink-control.md`, cited as `design §N`.  "Spec" means
  `sigmond/tasks/spec-key-split-v373.md`.  "Review" means `sigmond/tasks/review-final-key-split-v373.md`.
- The plan's status line still names v3.74 for step 2b (D16).  The release numbers moved again
  since (v3.74 and v3.75 went to field fixes, v3.76 collects now), so this map uses v3.77 and later.
- UNVERIFIED marks a claim that only a read on a station could settle.  No test ran for this map.
- Each element below gives: what exists today, what must change, the repo, the risks, and a size.
  Sizes count new or changed lines of code plus tests, roughly.

## The short version

Step 2a-i already moved the sink writer and added `producer` and `local` (element 1 stands
mostly done).  Everything else in 2b lacks any code: a grep of every repo for `sinks/`,
`pipelines.d`, `effective_state`, `write_switch`, `may_send`, `stored_intervals` and `carry_over`
finds design text only.

Six findings shape the plan more than the design text does.

1. Today the site switch works by rendering.  `hold` renders no client pipeline and `discard`
   wraps each in `discard = true` (`sigmond/lib/sigmond/uploader_manifest.py:626-646`).  Once the
   daemon gates with `may_send()`, sigmond would render every pipeline and trust the daemon.  A
   v3.73-v3.76 daemon has no gate, so it would ship everything.  sigmond must keep the rendered
   gate until a probe proves the daemon gates (§A2, §B4).
2. hs-uploader's own installer runs only at bootstrap.  sigmond calls it when the `hsupload` user
   or the venv is missing (`sigmond/lib/sigmond/commands/uploader.py:141-158`), and `smd update`
   treats hs-uploader as a source-only library (`sigmond/bin/smd`, `_rebuild_component`).  So an
   update in place never refreshes the unit file, never creates `/etc/hs-uploader/sinks` or
   `pipelines.d`, and never writes `hs-uploader.toml`.  2b must create these from sigmond, or
   from code the daemon runs, and must not depend on a changed unit file.
3. `smd restart <client>` restarts radiod because `cmd_restart` walks the catalog's `requires`
   closure (`sigmond/bin/smd:12716-12722`, `_expand_requires_closure` at `:532-589`).  hf-timestd
   requires ka9q-radio (`sigmond/etc/catalog.toml:73`).  Element 7 restarts recorders, so this fix
   comes first (§G3).
4. The in-process pskreporter senders of psk-recorder and meteor-scatter still write the old shared
   key, `sqlite:psk.spots` → pskreporter, the very key that migration 2 marked superseded (§D3).
   A cleanup that skips superseded keys would ignore a live reader.
5. Cleanup by producer breaks an assumption of the v3.73 retry pass: "the sink trims oldest first"
   (`hsu/watermark/schema.py:62-64`, `_sink_holds` at `:384-407`).  The guard needs a per-producer
   form before per-producer deletion ships (§D4).
6. While wspr-recorder's merged wsprdaemon tar still carries psk rows (`include_psk = true`,
   `wspr-recorder/deploy.toml`), any carry-over that gives psk-recorder `fill` and wspr-recorder
   `upload` leaks psk rows to wsprdaemon.org.  The design's step-3 gate on those rows
   (design §6.6, last paragraph) has to land in 2b, not step 3 (§B5).

---

## A. Element 1: the sink writer in hs-uploader; `producer` and `local` columns

### A1. What exists

- The writer lives at `hsu/sink/writer.py` (574 lines); `hsu/sink/__init__.py` exports `Writer`,
  `ensure_columns`, `infer_producer` and the DDL.  It moved in v3.70.
- sigmond's compatibility import: `sigmond/lib/sigmond/hamsci_sink/__init__.py:32-44` prefers
  `hs_uploader.sink.writer` and falls back to `_bundled.py`, a verbatim copy guarded by
  `sigmond/tests/test_hamsci_sink_compat.py`.
- `pending_uploads` carries `producer TEXT NOT NULL DEFAULT ''` and `local INTEGER NOT NULL DEFAULT 0`
  (`hsu/sink/writer.py:152-163`); `ensure_columns` adds both to an older file by ALTER alone
  (`:196-229`).
- `Writer.__init__` takes `producer=` (`:295-325`); `flush()` stores `producer or infer_producer(...)`
  and a constant `0` for `local` (`:434-453`).
- No caller passes `producer=` or `local=`.  psk-recorder (`src/psk_recorder/core/ch_tailer.py:563`),
  meteor-scatter (`src/meteor_scatter/core/ch_tailer.py:532`), wspr-recorder
  (`wspr_recorder/spot_sink.py:169`, `:614`) and codar-sounder all rely on D12's inference.
- Indexes: `(target_db, target_table, id)` and the `$.time` expression index (`:166-181`).  No
  `(target_db, target_table, producer, id)` index.  No `auto_vacuum`.

### A2. What 2b must change

| Change | Where | Note |
|---|---|---|
| `Writer(local=True)` stores `local = 1` and skips the switch check | `hsu/sink/writer.py` | design §6.1; nothing calls it until hfdl-recorder converts (step 4) |
| Every source ignores `local = 1` rows | `hsu/sources/sqlite.py` `_scope_clauses` (`:529-560`) and `hsu/sources/wspr_cycle.py` | no source filters on `local` today |
| The switch check at `insert()`: `effective_state(producer)`; in `off`, drop the rows and stay healthy | `hsu/sink/writer.py:389-422` | judge each row at its `insert()` time.  The payload's own time varies by table, and `insert()` runs at decode time, within the 30 s flush of the row's time.  This settles design §6.1's "each row's own time" without parsing payloads |
| Enforce only when the producer's switch file exists and the carry-over marker stands | same | design §3.4 compatibility rule and §10.1 marker |
| `sink unavailable`: log once and report, never a silent no-op | `hsu/sink/writer.py:100-114`, `HEALTH_NOOP` | design §3.2.  A writer on a host without `/var/lib/sigmond` must still not crash |
| The producer index | `_init_schema` (`:496-503`) | building it on a 10M-row file takes the write lock for seconds to minutes; see §D5 |
| Refresh `_bundled.py` | `sigmond/lib/sigmond/hamsci_sink/_bundled.py` | by the recipe in sigmond's CLAUDE.md |

### A3. Risks

- **The bundled copy cannot import a switch reader.**  `_bundled.py` serves venvs with no
  hs-uploader (codar-sounder, hf-tec, superdarn-sounder).  The check at `insert()` must therefore
  live inside `writer.py` itself, stdlib only, or the bundled scheme must grow a second file.
  Those three clients carry no switch file in 2b, so the bundled path only needs "no file, no
  enforcement".  Keep the reader small and inside `writer.py`.
- **A row stored with `producer = ''`** belongs to D12's producer.  The source scope applies D12
  only for psk (`hsu/sources/sqlite.py:111-114`); wspr, hfdl and codar rows with `''` match no
  scoped source.  Cleanup (§D) and any wspr producer filter must apply `infer_producer` to them.
- **Rollback.**  A v3.73 writer ignores the switch files and stores in every state.  That stores
  more, never sends more, since the daemon gates sending.
- **v3.73 sigmond with a v3.77 hs-uploader.**  No marker exists, so the writer never enforces.
  Neutral.

### A4. Size

Small.  About 120 lines in hs-uploader, 60 in tests, plus the bundled refresh.

---

## B. Element 2: switch files, `write_switch()`, `effective_state()`, and the `may_send` gate

### B1. What exists

Nothing.  `/etc/hs-uploader/sinks/` has no creator: hs-uploader's `install.sh` makes only
`/etc/hs-uploader/keys` (`hs-uploader/install.sh:190`).  The daemon reads switch files nowhere.
`Pipeline.client` exists since v3.73 (`hsu/core.py:214-216`), set by the factory
(`hsu/pipeline_factory.py:283`) and by the daemon for builder pipelines (`hsu/daemon.py:138`).

### B2. What 2b must add

A new module, say `hsu/sinkswitch.py`, stdlib only:

- `write_switch(path, change)`: an exclusive lock on `sinks/.lock`, re-read under the lock, append a
  `[[history]]` entry, write a temporary file, fsync, rename (design §3.4).  Needs a small TOML
  writer, since stdlib has `tomllib` only.  sigmond's `_set_toml_field`
  (`sigmond/lib/sigmond/psws.py:198-222`) writes every value as a quoted string, which would turn
  `pinned = false` into the truthy `"false"`; do not reuse it.
- `effective_state(client)`: the stricter of `sinks/<client>.toml` and `sinks/site.toml`, with the
  read rules of design §3.4.  A missing client file means `off`; a missing site file means no limit;
  any parse error or unknown state means `off`, logged once.  Cache on mtime, so a change takes
  effect at the next batch.
- `stored_intervals(client, start, end, at_least)`: merge both histories.  2b needs it only for the
  psk-rider gate (§B5); the packagers use it in step 4.
- `may_send(pipe)`: true for `governed = false`; true while the carry-over marker
  `/var/lib/hs-uploader/carry-over-v1.done` stays absent; otherwise `effective_state(pipe.client) ==
  "upload"`.

The gate sits in three places in `hsu/core.py`: the top of `_pump_one` (`:341-351`), before each
replay in `_drain_deliverables` (`:363-395`), and before each batch in `_drain_source`
(`:397-460`).  A refused pipeline returns without work and touches neither its send record nor its
queued retries (design §6.2).

`DiscardTransport` and `discard = true` (`hsu/transports/discard.py`, `hsu/pipeline_factory.py:29-31`)
retire only when sigmond stops rendering them (§B4).

### B3. Who writes the files in 2b

- The carry-over writes every client file and `site.toml` once (§F3).
- `smd sink off|fill|upload` and its aliases write `site.toml` (§F).
- No client writes its own file until step 4.  `smd sink <client>` waits for step 5.  So in 2b a
  client's file changes only at carry-over.  An operator who needs one client off before step 4 has
  no command; the site switch remains the only lever.

### B4. The rendered gate and the probe

Today the policy lives in the manifest (`sigmond/lib/sigmond/uploader_manifest.py:592-651`).  After
2b the daemon gates.  The two must not both drop away, nor both act on stale state.

| sigmond | hs-uploader | What must happen |
|---|---|---|
| 2b | 2b | sigmond renders every client pipeline, with `client`, and no `discard`; the daemon gates by the switch files.  The heartbeat carries `governed = false` |
| 2b | v3.73-v3.76 | the probe says "no gate": sigmond keeps rendering by the site state (`off` → `discard`, `fill` → heartbeat only), read from `site.toml`.  Otherwise the old daemon ships everything |
| v3.73-v3.76 | 2b | no marker exists (an old sigmond never runs the carry-over), so `may_send` passes everything and the old renderer's policy stands.  The legacy heartbeat carries no `governed = false`; the daemon must treat a `heartbeat_sftp` transport, or `client = "sigmond"`, as ungoverned, or the heartbeat stops wherever a marker exists |

The probe must keep M3's contract.  `hs-uploader capabilities` prints one bare integer, and
sigmond reads `int(stdout.strip())` (`sigmond/lib/sigmond/uploader_manifest.py:443-465`).  So 2b
adds `hs-uploader capabilities --features`, printing one word per line (`gate`, `pipelines.d`,
`cleanup`, `send-log-v3`).  A v3.73 hs-uploader's argparse refuses the flag with exit 2, which
sigmond reads as "no features".  The bare form keeps printing `SCHEMA_VERSION`; it moves to 3 if
2b adds migration 3 (§E2), still one integer.

### B5. The psk rider in the merged wsprdaemon tar

`WsprCycleSource` pulls every `psk.spots` row of a cycle when `include_psk` holds
(`hsu/sources/wspr_cycle.py:347-389`), with no producer or mode filter.  wspr-recorder's
`wspr-wsprdaemon` pipeline sets `include_psk = true`.  As soon as switch files can differ by
client, a psk row from a client in `fill` rides out under wspr-recorder's `upload`.  Design §6.6
gives the rule: a rider ships only if its producer read `upload` when it stored the row
(`stored_intervals(..., at_least="upload")`) and reads `upload` now.  That needs the producer per
row (D12 inference for `''`) and the two switch reads inside the source.  It must ship with the
gate, not in step 3.

### B6. Risks

- **Fail closed hurts on a parse error.**  A hand-edited `site.toml` with a typo turns every client
  `off`.  Design §3.4 wants exactly that, plus an ERROR and a red status.  The status views must
  show it on day one, or a station goes quiet with every light green.
- **Rollback after enforcement.**  A v3.73-v3.76 daemon ignores the files and follows the manifest.
  `smd admin manifest restore` re-renders with the restored sigmond (D11), which reads
  `[uploads]`.  If `[uploads]` went stale while `site.toml` ruled, the rollback ships by the stale
  value.  See §F4 and question 1.
- **Two writers.**  `write_switch` and sigmond's site command both take `sinks/.lock`.  The daemon
  only reads.  The daemon's unit holds `/etc/hs-uploader` read-only (`hs-uploader/systemd/hs-uploader.service:78`),
  which suits a reader.

### B7. Size

Medium.  hs-uploader: about 350 lines for the module and the gate, 150 for the rider gate, 400 in
tests.  sigmond: about 150 lines for the probe and the two-form renderer, 200 in tests.

---

## C. Element 3: `pipelines.d/` beside the legacy manifest; a store check at each rebuild

### C1. What exists

- The daemon reads one file, `/etc/hs-uploader/pipelines.toml` (`hsu/daemon.py:45`, `:172-200`).
  It builds every pipeline once and never rebuilds.  It handles SIGINT and SIGTERM only
  (`:243-249`).
- `Uploader` takes a fixed list and creates one single-thread executor per pipeline in `__init__`
  (`hsu/core.py:256-290`), because each `SqliteSource` pins its connection to its thread.
- The store check runs once, at start: `split_refused` (`hsu/daemon.py:150-166`) refuses producer
  pipelines on a store below version 2 and writes `pipeline_state`.
- The unit pins the manifest: `ConditionPathExists=/etc/hs-uploader/pipelines.toml` and
  `ExecStart=... serve --manifest /etc/hs-uploader/pipelines.toml`
  (`hs-uploader/systemd/hs-uploader.service:15`, `:30`).
- sigmond's §5.1 rewrite: `_split_for_producer` and the `PSK_PRODUCERS` table
  (`sigmond/lib/sigmond/uploader_manifest.py:205-248`), applied in `collect_pipelines` (`:291-293`).
  psk-recorder and meteor-scatter both still declare the shared `psk-pskreporter` block.

### C2. What 2b must change

- **Read `pipelines.d` without touching the unit.**  The daemon derives the directory from the
  manifest's parent (`/etc/hs-uploader/pipelines.d`), so the installed unit keeps working.  Finding 2
  of the short version explains why the unit cannot change on an update in place.
  `ConditionDirectoryNotEmpty=` waits for the release that re-runs hs-uploader's installer.
- **Precedence.**  A pipeline takes its client from its file's name.  Once `pipelines.d/<client>.toml`
  exists, the daemon ignores that client's legacy entries (design §3.5).  Legacy entries keep the
  `client` key sigmond renders.
- **Rebuild.**  At each pump, stat every file; on a changed mtime, or on SIGHUP, rebuild only the
  changed pipelines.  `Uploader` gains `replace_pipelines(new)`: shut down the executors of
  removed pipelines, create executors for added ones.  Queued retries survive, since they live in
  `watermarks.db`.
- **The store check at each rebuild** (spec §9): call `split_refused` and `record_pipeline_states`
  again.  A client-installed file bypasses the manifest step's stop-migrate-start order, so the
  daemon may meet a producer pipeline on an old store at any rebuild.
- **The key-collision refusal** (design §3.5): two built pipelines on one `(source, destination,
  table)` key stop the build and name both.  On a rebuild, refuse the new set and keep the old.
- **Refuse `start_at = "now"` in `pipelines.d`**, and make `"beginning"` the `wspr_cycle` default
  (`hsu/pipeline_factory.py:124`).  Leave the legacy manifest's `"now"` alone; every client
  declares it today (psk-recorder, meteor-scatter and wspr-recorder `deploy.toml`).
- **Validate key parts** before rendering instances (review M7): hs-uploader already refuses `/`
  and `@` (commit e7b1a7b); sigmond must check the same before it writes a file.
- **The §5.1 rewrite.**  Spec §9 says 2b retires it.  Nothing in 2b replaces it, though: no client
  writes a `pipelines.d` file until step 4, and D17 answer 2 kept client repos out of the split.
  Retiring it now would bring back the shared pipeline.  Two ways out: psk-recorder and
  meteor-scatter declare their split blocks in `deploy.toml` in 2b (a small client change), or the
  rewrite stays until step 4.  This map recommends that the rewrite stay until each client ships its
  own file.  It costs nothing and keeps an older client checkout working.

### C3. Risks

- **v3.73 sigmond with a 2b daemon.**  `pipelines.d` stays empty; the daemon reads the legacy
  manifest alone.  Neutral.
- **2b sigmond with an old daemon.**  An old daemon never reads `pipelines.d`.  So in 2b sigmond
  writes only the heartbeat's file there, and only after the features probe says `pipelines.d`;
  otherwise it keeps the heartbeat in `pipelines.toml`.  Writing it in both places would run two
  heartbeats on one key, which the collision refusal stops cold.
- **A rebuild mid-batch.**  Rebuild between pumps, never during one.

### C4. Size

Medium.  About 300 lines in `daemon.py` and `core.py`, 300 in tests.

---

## D. Element 4: cleanup moves into the daemon

### D1. What exists

- sigmond's age trim, `sigmond/lib/sigmond/storage_trim.py`.  Retention by table
  (`:94-99`): psk.spots 60 min, wspr.spots and wspr.noise 24 h, superdarn.detections 30 days,
  nothing else.  It selects by `queued_at < cutoff` and deletes in batches of 100,000 by id, with a
  `wal_checkpoint(TRUNCATE)` after each (`:295-365`; the 13.6 GB WAL on B4, 2026-07-23).  It knows
  nothing of producers, `local`, or send records.
- Units: `sigmond-storage-trim-all.timer` (every 15 min, the only one `install.sh:1189`
  enables), plus `-hfdl` (24 h), `-timestd` (7 d) and `-wspr` (24 h), which install copies
  (`install.sh:822-825`) but nobody enables.  So hfdl.spots, timestd.events, codar.spots and the
  hf_tec tables get no trim on a default host.  Design §6.3's "keep each retired unit's current
  retention" therefore means "none" for hfdl and timestd.
- `smd admin storage trim` (`sigmond/bin/smd:17338`, `:17398`), 39 tests in
  `sigmond/tests/test_storage_trim.py`.
- Every declared sqlite pipeline sets `delete_on_commit = false`, and so do all three shims.  The
  factory default stays `true` (`hsu/pipeline_factory.py:109`); `commit()` deletes across the whole
  table when no producer is set (`hsu/sources/sqlite.py:437-481`).
- One more deleter: wspr-recorder's `wsprnet_verifier.py:200-271`, under `WD_VERIFY_FLUSH=1` only,
  deletes verified rows and drops unverified rows after an hour, regardless of the wsprdaemon
  send record.
- No cleanup, `sink drop` or VACUUM exists in hs-uploader.  `/etc/hs-uploader/hs-uploader.toml`
  does not exist.
- The daemon can delete and vacuum `sink.db` today: `ReadWritePaths=/var/lib/sigmond`, `hsupload`
  in group `sigmond`, `sink.db` 0664 root:sigmond in a 2775 directory.

### D2. What 2b must add

- `sent_before(record)` on each source, by position, not by membership.  SqliteSource: `id <=
  cursor`.  WsprCycleSource: `$.time < cursor + 120 s`, so that a psk row in a cycle with no WSPR
  row counts as passed; a test by "was this cycle shipped" would keep it forever.
- A cleanup pass in the daemon, outside the pump, with the trim's batching and checkpoint.  It
  deletes a row only when every declared reader whose filter matches has sent it, oldest first,
  and only past the client's archive allowance.  The reader set comes from the declarations, not
  from what built; a declaration that failed to build blocks deletion and alarms.
- **Unknown readers.**  A client whose in-process sender still runs keeps today's age limit
  (design §6.3).  The daemon cannot see that sender.  sigmond knows (`sigmond/lib/sigmond/inprocess_senders.py:167`)
  and must hand the fact over, for example as `inprocess = true` on the client's legacy entries.
  wspr-recorder with `WD_VERIFY_FLUSH=1` counts as an unknown reader too.
- **Superseded keys.**  Skip a superseded key as a reader, except while that client counts as an
  unknown reader: the in-process psk and meteor-scatter senders still advance the old key
  `sqlite:psk.spots` → `pskreporter-tcp:report.pskreporter.info:4739`
  (`psk-recorder/src/psk_recorder/core/hs_uploader_shim.py:165-200`; meteor-scatter's shim the same).
- **The 5 GB backlog ceiling** and the 5 GB archive default, read from a new
  `/etc/hs-uploader/hs-uploader.toml`.  Usage per producer = `sum(length(payload_json))`.  At the
  ceiling the oldest unsent rows go, with a WARNING naming bytes and time span, and a counter.
- **Tables nobody trims**: codar.spots, hf_tec.spots, hf_tec_codeless.spots, timestd.events,
  hfdl.spots and superdarn.detections (the design's list omits the last).
- **`delete_on_commit` default false**, and producer-scoped when true.  Note that `commit()`
  ignores `extra_where`, so a true value on an ft8/ft4 pipeline would delete msk144 rows inside its
  producer scope.  Refuse `delete_on_commit = true` without a producer.
- **`hs-uploader sink drop <client>`** (design §6.2): each send record of the client to its
  producer's newest row, its queued retries deleted, only its rows deleted, while its pipelines
  stay idle.  It is a deliberate rewind and must bypass the forward-only rule on purpose.
- **sigmond retires the trim units**: stop shipping them, disable `-all` on upgrade, keep
  `smd admin storage trim` as a manual tool.  Only after the features probe says `cleanup`; an old
  daemon does no cleanup, and `sink.db` would grow without bound.

### D3. Risks

- **The retry guard (finding 5).**  `_sink_holds` proves "no row after the old record has gone" from
  one surviving row.  Per-producer deletion removes meteor-scatter's sent rows while
  psk-recorder's older unsent rows stay, or the reverse.  Make the guard per producer, scoped by
  D12, before cleanup ships.  Review M4's clock caveat stands too.
- **The one-time VACUUM** that turns on `auto_vacuum = INCREMENTAL`.  On B4 on 2026-07-23 a VACUUM
  took about 105 s with writers stopped, and shrank 14.9 GB to 764 MB.  It needs free space about
  the file's size and holds the write lock throughout.  Writers time out at 30 s and raise
  `BufferFull` past two batches (`hsu/sink/writer.py:405-413`).  It cannot run inside the live
  daemon unattended.  See question 2.
- **The producer index** takes the write lock while it builds.  Build it in the same window as the
  VACUUM, or accept a pause of seconds on a large file.
- **No pipeline, no cleanup.**  The daemon exits when nothing builds (`hsu/daemon.py:186-197`) and
  the unit waits for `pipelines.toml`.  A station with the site switch at `fill` renders client
  pipelines under 2b, so this holds; but a host with no hs-uploader manifest at all keeps
  sigmond's trim.  Keep `-all` enabled wherever the daemon will not run.
- **Rollback.**  A rolled-back sigmond re-ships its trim units and enables `-all`, which trims by
  age again.  Rows stored under `fill` older than 24 h (WSPR) or 60 min (psk) go.  That loses
  backlog but sends nothing.  State it in the release notes.

### D4. Size

Large.  About 600 lines in hs-uploader (cleanup, `sent_before` on three sources, config file,
`sink drop`, per-producer guard), 600 in tests; about 150 in sigmond (unit retirement, hand-over
of the in-process fact) and 100 in tests.  Plus the VACUUM procedure.

---

## E. Element 5: the send log's `pipeline` and `client` columns; `last_send`

### E1. What exists

- `attempts(rowid, ts, source_id, dest_id, table_name, outcome, records, bytes, error)`, a ring of
  10,000 rows (`hsu/watermark/sqlite.py:38`, `:50-61`, `:459-484`).
- `record_attempt` takes the key and the outcome, no pipeline name (`hsu/watermark/base.py:114-126`).
  It has nine callers in `hsu/core.py` (`_handle_first_attempt` `:462-538`, `_handle_outcome`
  `:603-717`); no other repo calls it or reads `attempts`.
- The heartbeat reads `python -m hs_uploader.backlog` (`sigmond/lib/sigmond/heartbeat.py:905`), which
  reports retries, dead letters, cursors, `superseded_by` and `pipeline_state`, but no send times.

### E2. What 2b must add

- Migration 3: `ALTER TABLE attempts ADD COLUMN pipeline TEXT NOT NULL DEFAULT ''` and `client`
  likewise; `CREATE TABLE last_send(client, pipeline, outcome, ts, records, PRIMARY KEY (client,
  pipeline))`, written only for `acked` and `partial_ack` on a transport that ships (not
  `DiscardTransport`).  Bump `SCHEMA_VERSION` to 3.  Keep the producer-key fill on every migrate
  (spec §9, last rows).
- `record_attempt(..., pipeline="", client="")`, keyword with defaults, so the in-process shims,
  which share the library, keep working.
- `backlog.summarize` adds `last_send` per client.

### E3. Risks

- **The daemon never migrates (D10).**  A 2b daemon on a version-2 store must write the old column
  set.  Check `schema_version()` once at start and at each rebuild, and write the new columns only
  at 3 or above.
- **Rollback.**  A v3.73 daemon on a version-3 store: `INSERT` names its own columns and the new
  ones default, so it works.  `migrate` on a newer store leaves the number alone (`hsu/watermark/schema.py:576-603`).
- **M3.**  The bare `capabilities` output moves from 2 to 3, still one integer.  A v3.73 sigmond
  reads 3 ≥ 2 and renders the split, as it should.

### E4. Size

Small.  About 120 lines and 150 in tests.

---

## F. Element 6: `smd sink` at site level; aliases; `[uploads]` read-only; carry-over

### F1. What exists

- One handler serves three commands: `cmd_config_uploads` (`sigmond/lib/sigmond/commands/config.py:1146-1253`)
  behind `smd upload`, `smd config uploads` and `smd sink` (`sigmond/lib/sigmond/commands/sink.py:23-35`
  maps `off` → `discard`, `upload` → `on`, and refuses `fill`).
- It writes `[uploads]` into both site-profile and coordination.toml
  (`_patch_uploads_block`, `config.py:950-995`; `hold` stores as `enabled = false` alone), closes the
  doors on leaving `discard` (`config.py:1016-1047`, `sigmond/lib/sigmond/sink_doors.py`), and
  regenerates the manifest.
- `smd config render` copies site-profile's `[uploads]` over coordination's on every `smd align`
  (`config.py:704-746`, `sigmond/bin/smd:6454-6460`).  site-profile wins.
- Readers of the mode: the manifest (`uploader_manifest.py:485-651`), `smd sink status`
  (`sink.py:38-63`), `smd upload status` (`config.py:1156-1189`), `smd check`'s `uploads_line`
  (`sigmond/lib/sigmond/commands/check.py:933-972`, which dates the last change by
  coordination.toml's mtime, `:982-990`), and the heartbeat (`sigmond/bin/smd:7128-7134`,
  `sigmond/lib/sigmond/heartbeat.py:414-486`).
- The wizard writes the `off` block on a first install only (`scripts/proxmox/sigmond-wizard.sh:1745-1748`).
- `smd update` never reaches the manifest step (D10); `smd align` does, without `--enable`
  (`sigmond/bin/smd:6474-6487`).

### F2. What 2b must change

- `smd sink off|fill|upload` and the aliases call `write_switch()` on `site.toml`.  `fill` arrives.
  The door-closing of v3.69 stays for the step out of `off`.
- Every reader in F1 moves to `site.toml` for the site state, with `[uploads]` as the fallback only
  while no marker exists.  `smd check` dates the change by `site.toml`'s `set_at`.
- `smd config render` stops copying `[uploads]` once the marker exists; otherwise `smd align`
  rewrites coordination from a stale profile on every run.
- Raising the site switch runs the identity check per client (D1).  No client has a `sink check`
  command in 2b, so sigmond has nothing to call; the check arrives with step 4.  2b should print
  each client's identity and refuse nothing, and say so.

### F3. Carry-over (design §10.1)

Inputs, all present today: enabled clients from `topology.toml`; producers with rows in
`sink.db`; send records and the `attempts` ring; the legacy list rendered as if `upload`;
`PSK_USE_HS_UPLOADER=0` and `METEOR_SCATTER_USE_HS_UPLOADER=0` in `/etc/<client>/env/*.env`
(`sigmond/lib/sigmond/upload.py:24-29`, `:47-75`); identity from coordination `[host]` and
`[station]`, reporter ids from env-file names (`sigmond/lib/sigmond/instance.py:116-140`), PSWS ids
from `psws.read_state` (`sigmond/lib/sigmond/psws.py:143`).  No per-instance lat/lon exists; the
host's applies.

Where it runs: `smd align` and `smd update`, under `/run/lock/hs-uploader-install.lock`.  `smd
update` has no hook today; one must be added.  It writes switch files only and restarts nothing,
so it stays clear of D10, which concerns the migration.

"Has an outbound route" means "declares `[[hs_uploader.pipeline]]` in its deploy.toml" today
(`uploader_manifest.py:191`).  hfdl-recorder declares none but sends; it gets no file in 2b and
stays UNMANAGED.

### F4. Risks

- **Rollback reads a stale `[uploads]`.**  Under 2b the operator sets the site to `off` in
  `site.toml`; `[uploads]` still reads `upload`.  A rollback to v3.73-v3.76 re-renders by
  `[uploads]` and ships.  Mirroring each site write into `[uploads]` closes this, but makes the
  legacy block writable again.  Question 1.
- **The nested test breaks by design.**  `sigmond-appliance/test-nested-v3.sh:813-847` asserts
  `mode = "discard"` in both files after a fresh install; `:1175-1210` and `:1276-1290` read the
  manifest's banner and discard counts.  All move to `site.toml` and to the daemon's
  `pipeline_state`.  The operator pages (`QUICKSTART.txt:235-238`, `docs/install-page.html`) teach
  `smd sink` already and keep working.
- **Fresh install.**  The wizard should write `site.toml` at `off` (through sigmond) and the
  installer should write the marker at once (design §10.1).  Until the wizard changes, a fresh 2b
  host gets its `site.toml` from the carry-over of `[uploads] mode = "discard"`.  Either path ends
  at `off`.

### F5. Size

Medium-large.  About 500 lines in sigmond (site writer, readers, render guard, carry-over, update
hook), 600 in tests; about 60 in the wizard and 150 in the appliance tests.

---

## G. Element 7: sigmond stops in-process senders when the site reads `off` or `fill`

### G1. The senders, read from code

| Sender | Lever | File | Unit to restart |
|---|---|---|---|
| psk-recorder direct | `PSK_DELIVERY_PIPELINES` without `direct` (`server-raw`); legacy `PSK_DELIVERY_MODE` as fallback; default `server-merge` sends nothing but marks rows for wd30 | `/etc/psk-recorder/env/<id>.env`; reader `psk-recorder/src/psk_recorder/core/recorder.py:206-262`, gate `:717-725`, row flag `:539-555` | `psk-recorder@<id>` |
| meteor-scatter direct | `METEOR_SCATTER_DELIVERY_MODE=off`; unset means `direct` | `/etc/meteor-scatter/env/<id>.env`; reader `meteor-scatter/src/meteor_scatter/core/recorder.py:627-643`; deploy seed `deploy.toml:22-23` | `meteor-scatter@<id>` |
| wspr shim | `WSPR_USE_HS_UPLOADER=0`; the shim also needs `WD_RECEIVER_GRID`, which sigmond never writes | `/etc/wspr-recorder/env/<id>.env`; `wspr-recorder/wspr_recorder/hs_uploader_shim.py:171-259` | `wspr-recorder@<id>` |
| `wspr-uploader.service` | stop and disable | `wspr-recorder/systemd/wspr-uploader.service`; env `/etc/wspr-recorder/uploader.env`; nobody enables it but an operator | itself |
| mag fallback | `[uploader] enabled = false` | `/etc/mag-recorder/mag-recorder-config.toml`; reader `mag-recorder` `cli.py:436-438`; runs only when hs-uploader is inactive (`systemd/mag-recorder-upload.service:38`) | none; the timer reads the file at 03:00 |

No code acts on the site state for any of these today.  `sigmond/lib/sigmond/upload.py` already
holds the env-file writer (`set_env_flag` `:78-94`, `env_path_for` `:47-75`, the chown at `:97`),
and `inprocess_senders()` already reads each instance's effective environment.

### G2. What 2b must add

- One function, run after every site change to `off` or `fill` and at the carry-over: write the
  levers above, through `set_env_flag`, and record what it changed.
- Restart each touched instance alone, by `systemctl restart <client>@<id>`, never through
  `cmd_restart`.  The working examples (`sigmond/lib/sigmond/commands/config.py:441-468`) already do
  it that way.
- Raising the site to `upload` restores nothing; step 4 retires the senders.  So the lowering
  stays one-way, and `smd sink status` lists any sender still armed as UNMANAGED.
- A bool-aware TOML setter for mag-recorder: `_set_toml_field` writes `"false"`, a truthy string
  that mag-recorder does not type-check.
- `harmonize.rule_upload_enabled` (`sigmond/lib/sigmond/harmonize.py:1215-1244`) warns "decoding
  but NOT uploading" on `WSPR_USE_HS_UPLOADER=0`.  It must learn the sink state, or it warns on
  every station that 2b quiets.

### G3. The radiod restart (v3.74 row 6, v3.76 row 4)

`cmd_restart` (`sigmond/bin/smd:12707`) expands the named components through
`_expand_requires_closure` (`:12716-12722`).  hf-timestd requires ka9q-radio
(`sigmond/etc/catalog.toml:73`), hamsci-physics requires hf-timestd (`:85`), and the closure also
emits ka9q-radio's alias `radiod`.  `order_units` then puts `radiod@<id>` first, and the loop
restarts every unit.  No unit file carries `Requires=`, `BindsTo=` or `PartOf=` on radiod.  So
`smd restart hamsci-physics` restarts radiod and hf-timestd too.

The fix: restart the named set only; for dependencies, `systemctl start` (a no-op on an active
unit) rather than `restart`.  `smd restart` with no argument keeps its meaning.  The test: `restart
hf-timestd` produces no ka9q-radio or radiod unit.  `_adopt_start_set` (`:13280`) carries the same
pattern on the start side.  This belongs to v3.76 and should land before any 2b sub-step.
UNVERIFIED on B4: the journal at 13:25:41Z and 13:30:02Z on 10-20 should show the closure's
`systemctl restart radiod@AC0G-B4`.

### G4. Risks

- **A psk station on `server-merge`** (K3LR's stop-gap) marks every row for wd30's forwarder.
  Moving it to `server-raw` at `fill` keeps the rows home; design §10.2 step 2 wants exactly that.
- **Losing pskreporter posts on purpose.**  At `fill`, a station that posts through a direct sender
  stops posting at once.  That matches the operator's order.
- **The live B4 values** are UNVERIFIED: `grep -H . /etc/{psk-recorder,meteor-scatter,wspr-recorder}/env/*.env`,
  `/etc/wspr-recorder/uploader.env`, and `systemctl is-enabled wspr-uploader` would settle them.

### G5. Size

Small-medium.  About 250 lines in sigmond, 300 in tests.  The restart fix: about 40 lines and
two tests.

---

## H. Element 8: tests

### H1. What exists

- hs-uploader: about 590 tests in 28 files.  The ones 2b touches: `test_daemon.py` (7),
  `test_core_orchestration.py` (11), `test_sink_writer.py` (47), `test_sqlite_source_producer.py`
  (18), `test_watermark_schema.py` (39), `test_migration_split.py` (30), `test_backlog.py` (23),
  `test_wspr_cycle_source.py` (4), `test_pipeline_factory.py` (16), `test_cli.py` (15); the tool
  `tools/neutrality_check.py`.
- sigmond: `test_sink_cli.py` (20), `test_site_profile.py` (the uploads cases at `:458-715`),
  `test_uploader_manifest.py`, `test_uploader_manifest_split.py` (22),
  `test_uploader_manifest_step.py` (34), `test_uploader_cmd_policy.py` (41), `test_check_uploads.py`
  (16), `test_heartbeat.py:1291-1402`, `test_wizard_uploads.py` (10), `test_upload.py` (20),
  `test_inprocess_senders.py` (41), `test_storage_trim.py` (39), `test_lifecycle.py`,
  `test_hamsci_sink_compat.py`.
- Appliance: `test-update-v3.sh` (PHASE E-bis runs the manifest step), `test-nested-v3.sh`
  (§F4).

### H2. New tests, each watched failing under a named mutation

| # | Test | Mutation that must make it fail |
|---|---|---|
| 1 | The leak harness of design §11: daemon plus stand-in destinations through every site and client state and transition | remove `may_send` from each of its three places in turn |
| 2 | The writer drops rows in `off`, stores in `fill`, ignores the switch for `local=True`, and enforces only with marker and file | remove the check; invert the marker test |
| 3 | No source ever selects a `local = 1` row | drop the `local` clause |
| 4 | The psk rider leaves out rows whose producer reads `fill`, and rows stored before `upload` | drop the rider gate |
| 5 | Each row of §B4's version table, including an old daemon with a 2b sigmond: nothing renders without `discard` | make the renderer trust the daemon unconditionally |
| 6 | Read rules: missing client file, missing site file, parse error, unknown state, stricter-of | swap the order `off` < `fill` < `upload` |
| 7 | `write_switch` under two racing writers keeps both history entries | drop the re-read under the lock |
| 8 | A rebuild after a file changes keeps queued retries and refuses a producer pipeline on an old store | skip the store check at rebuild |
| 9 | Two pipelines on one key: the build refuses and names both | remove the refusal |
| 10 | Cleanup never deletes an unsent row; spares rows of a client with an unknown reader; keeps a superseded key as a reader while its in-process sender runs | treat every superseded key as dead |
| 11 | The per-producer retry guard: a retry survives when the other producer's rows went | restore the table-wide guard |
| 12 | The 5 GB ceiling drops oldest unsent first and warns once | delete newest first |
| 13 | `sink drop` deletes only its producer's rows and retries | drop the producer scope |
| 14 | Migration 3 and the version gate on the new columns; a v3.73 store opened by 2b code | write the new columns unconditionally |
| 15 | Carry-over for each legacy mode (`upload`, `hold`, `discard`) and the K3LR case (`PSK_USE_HS_UPLOADER=0`); a second run changes nothing | ignore the env flag |
| 16 | Site `fill` writes each lever, restarts only the touched instances, never radiod | route through `cmd_restart` |
| 17 | `smd restart hf-timestd` names no radiod unit | restore the closure |
| 18 | The proof on real data, as for v3.70: B4 and ND snapshots through 2b code with no marker select the same rows as v3.76 | none needed; the neutrality tool already shows INCONCLUSIVE on an idle copy |

---

## J. Cross-version and rollout seams

| Case | Outcome under the proposed design |
|---|---|
| Fresh install, 2b both | wizard or carry-over writes `site.toml` at `off`; the marker stands at once; the daemon gates; cleanup runs; trim units stay off |
| Update in place from v3.76 | `smd update` pulls; hs-uploader's installer does not run; the daemon reads `pipelines.d` from the manifest's parent; carry-over at `smd update` and `smd align` writes files and marker; the manifest step migrates to version 3 |
| 2b sigmond, v3.73-v3.76 hs-uploader (hand-moved) | the features probe fails; sigmond renders by `site.toml` the old way; trim units stay |
| v3.73-v3.76 sigmond, 2b hs-uploader | no marker; the daemon gates nothing; the old renderer's policy stands; the heartbeat runs as ungoverned |
| Rollback of both after enforcement | the old daemon ignores the files; the restored renderer reads `[uploads]` (question 1); the old trim returns and may drop `fill` backlog older than its retention |
| M3 | the bare `capabilities` output stays one integer (3); features travel in `--features` |

---

## K. Size, and a proposed split

| Element | Size | Repos |
|---|---|---|
| 1 writer | small | hs-uploader, sigmond (bundled copy) |
| 2 switches and gate | medium | hs-uploader, sigmond |
| 3 `pipelines.d` and rebuild | medium | hs-uploader, sigmond |
| 4 cleanup | large | hs-uploader, sigmond |
| 5 send log | small | hs-uploader |
| 6 site command and carry-over | medium-large | sigmond, appliance |
| 7 in-process senders | small-medium | sigmond |
| 8 tests | spread across the above | all |

In all, about 2,500 lines of code and 3,000 of tests.  v3.73's key split, one element of this
size, took a spec, a review and an image.  2b in one image would carry three kinds of risk at
once: a new gate that can silence a station, deletion of data, and a carry-over that rewrites
every station's policy.  Three images separate them.

**2b-1, v3.77: plumbing that changes nothing a station sends or deletes.**  Elements 1 (the
`local` parameter, the source filter, the index; the switch check present but idle without a
marker), 3 (`pipelines.d` beside the legacy file, rebuild, store check at rebuild, collision
refusal; the heartbeat file only behind the probe), 5 (migration 3, the columns, `last_send`), the
switch-file library from element 2 with nothing calling it in the daemon, and
`capabilities --features`.  The restart fix lands before it, in v3.76.  Proof as for v3.70:
snapshots from B4 and ND select the same rows; the update rig migrates to 3; a rollback re-renders
cleanly.

**2b-2, v3.78: the switches go live.**  The rest of element 2 (the gate, the psk-rider gate, the
writer's check), element 6 (site command, readers, render guard, carry-over with its marker, the
`smd update` hook) and element 7 (levers and single-instance restarts).  This image changes what
stations send.  It ships after a day on B4 in `fill` and a change to `upload` (design §11).
Cleanup stays with sigmond's age trim, which keeps the plan's own rule: "except rows of clients
whose in-process senders still run".  Until 2b-3, `fill` keeps only 60 minutes of psk and 24 hours
of WSPR.  The release notes must say so, and `smd sink fill` should print it.

**2b-3, v3.79: cleanup moves.**  Element 4 whole: `sent_before`, the per-producer retry guard,
unknown readers, superseded keys, the 5 GB ceiling, `sink drop`, `hs-uploader.toml`, the
VACUUM window, retirement of the trim units.  Only this image makes `fill` keep what design §4.3
promises.

An alternative puts 2b-3 before 2b-2, so that `fill` keeps its promise the day it arrives.  Cleanup
without switches has a simpler reader set (everything counts as `upload`), so the order works
either way.  The order above ships the operator-visible lever first and the deletion change last.

---

## Questions only Michael can answer

Settled from the code or the binding decisions, so not asked: the probe form (M3 holds if the
bare output stays one integer, §B4); the superseded key (design §6.3's unknown-reader rule covers
it, §D2); the psk rider gate (design §6.6 already states it); the carry-over from `smd update`
(D10 governs the migration, not switch files, §F3); per-row time at `insert()` (§A2); hfdl and
timestd retention (none today, so the 5 GB archive default applies, design §6.3).

1. After 2b, should a site change also write `[uploads]`, so that a rollback to v3.73-v3.76 ships
   by the same setting (§F4)?  Design §10.2 calls the block read-only legacy; strictly read-only
   lets a rollback ship by a stale value.
2. When may the one-time VACUUM run (§D3)?  It needs the writers stopped for about two minutes on
   a large file and free space equal to the file.  Options: during the manifest step with recorders
   stopped, at a reboot, or never, accepting that `sink.db` never shrinks.
3. Do you accept the three-image split (§K), and its order: switches before cleanup, with `fill`
   keeping only 60 minutes of psk and 24 hours of WSPR until 2b-3?
4. Does meteor-scatter's Zenodo upload of MSQP recordings move under hs-uploader and the sink
   switch in step 2b, or later (spec §9, D17 answer 2)?  2b's pipeline files can carry several
   destinations per client either way; the question decides whether 2b builds a Zenodo transport.

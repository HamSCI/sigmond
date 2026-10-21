# spec: sink control step 2b, first image (v3.77): plumbing that sends nothing new

**Status:** reviewed by Michael 2026-10-21; his three rulings stand in §13.  Nobody has built any of it.

**Release number.**  "v3.77" names this image's place in D18's order, not a promised tag.  rob may
build further images for his own rows first, so this work takes whatever number comes next once it
is ready; v3.78 and v3.79 below move with it.  It implements the
v3.77 image of D18 in `plan-sink-control.md` §2, under D9, D10, D12, D13, D17, D18, D20 and D21.
D19, the dual write of `site.toml` and `[uploads]`, belongs to v3.78 and appears here only where
v3.77 must leave room for it.  Evidence: `research-sink-2b-v377.md` (the code map, 10-21, whose
three-image split Michael accepted), `spec-key-split-v373.md` (the format and the v3.73 contracts)
and `review-final-key-split-v373.md` (M3 and M7).  §11 lists what v3.78 inherits.

## How to read this spec

- Paths start at `/home/mjh/hamsci/repos/`.  `hsu/` abbreviates `hs-uploader/src/hs_uploader/`.
- Line anchors come from origin/main: hs-uploader 334e71e, sigmond a3d90e8.  Both local
  checkouts matched origin/main on 10-21.
- Words used in one sense throughout:
  - **the daemon**: `hs-uploader serve`, the one process that sends every pipeline on a host;
  - **the store**: the daemon's `watermarks.db`, which holds send records, queued retries, dead
    letters and the attempt log;
  - **a send record**: the position up to which one pipeline has sent, stored under a **key** of
    source, destination and table;
  - **the sink**: `sink.db`, the table `pending_uploads` that every recorder writes and every
    pipeline reads;
  - **the legacy manifest**: `/etc/hs-uploader/pipelines.toml`, which sigmond renders;
  - **the manifest step**: `smd admin uploader manifest`, which sigmond's align, apply, bring-up,
    `smd sink` and restore all run;
  - **a switch file**: `/etc/hs-uploader/sinks/<client>.toml` or `sinks/site.toml` (design §3.4).

## 1. What changes, in one paragraph

v3.77 lays the plumbing for the sink switches and changes nothing a station sends.  The daemon
learns to read pipeline files from `pipelines.d/` beside the legacy manifest, and to rebuild when
one changes, without a restart.  The store gains a send log that names each pipeline and its client,
plus a `last_send` table, through migration 3.  The sink writer gains `local=`, and the sink gains
an index on producer.  hs-uploader gains a library that reads and writes switch files; the daemon
loads it and reports what each client's switch would say, but no switch stops anything yet.
`hs-uploader capabilities --features` tells sigmond what the installed hs-uploader can do, while
bare `capabilities` keeps printing one integer.  On every station today no pipeline file and no
switch file exists, so every pipeline forms the same key, selects the same rows and ships the same
bytes as under v3.76.  §8 proves it on copies of B4's and ND's databases.

## 2. Where each change lives

| # | Change | Repo | Anchor today |
|---|---|---|---|
| 1 | Read `pipelines.d/` beside the legacy manifest; rebuild on change; store check at each rebuild | hs-uploader | `hsu/daemon.py:80-82` (load), `:103-142` (build), `:150-166` (store check), `:172-273` (run loop); `hsu/core.py:256-295` (executors) |
| 2 | Send-log columns `pipeline` and `client`; table `last_send`; migration 3 | hs-uploader | `hsu/watermark/sqlite.py:38-105` (DDL), `:459-484` (`record_attempt`); `hsu/watermark/schema.py:268-273` (migrations), `:279-328` (the fill); `hsu/core.py:462-717` (nine callers) |
| 3 | `Writer(local=True)`; the producer index; sources skip local rows | hs-uploader, plus sigmond's bundled copy | `hsu/sink/writer.py:153-190` (DDL), `:295-325`, `:424-453`, `:496-503`; `hsu/sources/sqlite.py:529-560`; `hsu/sources/wspr_cycle.py:171-178`, `:311-316`, `:361-365`; `sigmond/lib/sigmond/hamsci_sink/_bundled.py` |
| 4 | The switch-file library, loaded and reported, not enforcing | hs-uploader | new module `hsu/sinkswitch.py`; new CLI verb `hs-uploader sink status` |
| 5 | `hs-uploader capabilities --features` | hs-uploader | `hsu/cli.py:125-129`, `:160-163` |
| 6 | The features probe, informational only | sigmond | beside `sigmond/lib/sigmond/uploader_manifest.py:443-465`; called from `sigmond/lib/sigmond/commands/uploader.py:363` |
| 7 | The neutrality tool reads `pipelines.d` | hs-uploader | `hs-uploader/tools/neutrality_check.py:279-392` |

sigmond changes little in v3.77.  It renders the same manifest, keeps the §5.1 rewrite of the
v3.73 spec and the D13 rule, and creates no directory under `/etc/hs-uploader`.  No client
repository changes.  meteor-scatter gets nothing new (D21).

## 3. hs-uploader: `pipelines.d/` beside the legacy manifest

### 3.1 Where the daemon looks

The daemon derives the directory from the legacy manifest's own path: `--manifest
/etc/hs-uploader/pipelines.toml` gives `/etc/hs-uploader/pipelines.d/`.  The unit file stays as it
is (`hs-uploader/systemd/hs-uploader.service:15`, `:30`).  An update in place never re-runs
hs-uploader's installer: sigmond calls it only when the `hsupload` user or the venv is missing
(`sigmond/lib/sigmond/commands/uploader.py:141-158`).  So no release before the one that re-runs the
installer may depend on a changed unit file.  The unit already grants read access to
`/etc/hs-uploader` (`:78`), which covers both new directories.

A missing directory means no pipeline files.  The legacy manifest stays required: without it the
daemon exits, as today (`hsu/daemon.py:175-182`).

### 3.2 What a pipeline file holds

`pipelines.d/<client>.toml` holds `[[pipeline]]` entries in the legacy manifest's shape.  The daemon
reads only files whose names end in `.toml` and do not start with a dot, so an editor's backup or a
`.toml.new` side file never loads.

- The file's name gives the client.  The name must match `[a-z0-9][a-z0-9-]*`.  This refuses `/`,
  `@`, `.` and upper case, so a client name can never break a key or a path (review M7).
- An entry may carry `client`; it must equal the file's name.
- An entry may carry `governed` (true or false, default true).  The daemon stores it on the
  `Pipeline` and uses it nowhere in v3.77.
- An entry may not set `start_at = "now"` (design §6.2).  A `wspr_cycle` source in a pipeline file
  defaults to `start_at = "beginning"`.  The legacy manifest keeps its `"now"` default
  (`hsu/pipeline_factory.py:124`), since every client declares `"now"` today and a changed default
  would move where a new legacy pipeline starts.
- Host-wide settings stay in the legacy manifest: a top-level `[identity]` table naming
  `ssh_key_file`, and `[daemon]`, make the daemon refuse the file.
- Per-instance identity tables, `[identity.<instance>]` (design §3.5), parse and load, and nothing
  reads them yet.  The daemon logs one INFO line that it ignores them.  Step 4's clients may then
  write them without breaking a v3.77 daemon.
- Each pipeline takes the base identity from the legacy manifest's `[identity]`, plus its own
  `[pipeline.identity]` overrides, exactly as a legacy entry does (`hsu/pipeline_factory.py:236-287`).

The daemon accepts or refuses a file whole.  A file that fails to parse, names a bad client, breaks
a rule above, or fails to build any one entry sends nothing.  A client's declaration then either
stands complete or fails loudly; half a declaration never ships.

### 3.3 Precedence and collisions

- Once `pipelines.d/<client>.toml` exists, the daemon ignores every legacy entry whose `client` reads
  that name (design §3.5).  "Exists" means present on disk, parsed or not.  A broken file therefore
  silences its client rather than reviving that client's stale legacy entries.
- Legacy entries keep the `client` that sigmond renders (`sigmond/lib/sigmond/uploader_manifest.py:302`;
  the heartbeat carries `client = "sigmond"`, `:349`).
- Two built pipelines may not share a key or a name.  When a pipeline file brings a collision, the
  daemon refuses that file and names both pipelines.  A collision inside the legacy manifest alone
  gets an ERROR naming both, and the daemon runs it as v3.76 would.  v3.77 must not stop a
  station that runs today; v3.78 may harden this once the proof of §8 shows no station carries one.

### 3.4 Rebuild on change

Before each pump the daemon stats the directory and each file in it
(modification time in nanoseconds, size, inode).  When that signature differs from the one it last
built, or when SIGHUP arrives, the daemon rebuilds.  The main loop runs the rebuild between pumps,
never during one: `Uploader.pump()` joins every pipeline's future before it returns
(`hsu/core.py:297-339`).

A rebuild reads every file in `pipelines.d/` again and keeps the legacy manifest's entries as the daemon read them at start, then:

1. builds each entry whose content changed or appeared, and keeps every unchanged pipeline's
   `Pipeline` object and its executor, so its source keeps its open connection and its thread;
2. for a file that now fails, keeps the pipelines that file last built, logs one ERROR per
   signature, and records the refusal (item 4);
3. runs the store check of §3.5 on the new set;
4. writes `pipeline_state` with `replace=True`, one row per pipeline, and one row named
   `pipelines.d/<file>` for each refused file, with its client and reason;
5. hands the new list to a new method, `Uploader.replace_pipelines(new)`.  That method closes each
   removed pipeline's source on that pipeline's own executor thread, shuts the executor down, and
   creates an executor for each added pipeline.  Queued retries survive: they live in the store,
   keyed by pipeline name.
6. binds the wake socket if the new set first needs it (`hsu/daemon.py:222-241`).  It never unbinds
   one while running.

A rebuild whose set equals the running set changes nothing and logs one DEBUG line.

The daemon does not watch the legacy manifest (Michael, 10-21).  A change to it takes effect when
the daemon restarts, as today.  sigmond's manifest step already stops the daemon before it moves a
new manifest into place (`sigmond/lib/sigmond/commands/uploader.py:517-567`), so only a hand edit
waits, and a hand edit of the legacy manifest calls for a restart.  The rebuild still reads the
legacy manifest's `[identity]` and host-wide settings as they stood at start.

### 3.5 The store check at each rebuild

`split_refused` (`hsu/daemon.py:150-166`) runs at every rebuild, not only at start.  A file placed by
a client never passes through the manifest step's stop, migrate, start order, so the daemon may meet
a producer pipeline on an old store at any rebuild (v3.73 spec §9).  The same rebuild re-reads the
store's version for the send log (§4.3).

### 3.6 Reporting

At each build the daemon logs one INFO line per pipeline: its name, its client, and where it came
from (`legacy` or `pipelines.d/<file>`).  `pipeline_state` keeps the schema it has.  Recording the
origin there would need a column, and so a version-gated write; the journal serves until v3.78
needs more.

## 4. hs-uploader: the send log, `last_send`, migration 3

### 4.1 Migration 3

`hs-uploader migrate` gains migration 3, appended to `MIGRATIONS` (`hsu/watermark/schema.py:268-273`)
with its own frozen DDL, as migration 2 froze `superseded_keys` (`:166-180`).  Inside the one
`BEGIN IMMEDIATE` transaction that every migration shares, it:

1. adds `pipeline TEXT NOT NULL DEFAULT ''` and `client TEXT NOT NULL DEFAULT ''` to `attempts`,
   each only when `PRAGMA table_info(attempts)` lacks it;
2. creates `last_send(client TEXT NOT NULL, pipeline TEXT NOT NULL, outcome TEXT NOT NULL,
   ts TEXT NOT NULL, records INTEGER, PRIMARY KEY (client, pipeline))`;
3. reports one line per change, and `--check` previews the same lines.

It fills no old row.  The attempt log before migration 3 names no pipeline, and the migration cannot
learn names from the store alone.  `last_send` therefore starts empty and fills at each pipeline's
first send after the upgrade.  For GRAPE and the magnetometer that takes up to a day.  A reader must
say "no send since the upgrade", not "never sent".

`SCHEMA_VERSION` follows to 3 (`:504`).  A file the store creates carries the new columns and table
in `_SCHEMA_SQL` and gets stamped 3 (`hsu/watermark/sqlite.py:169-179`), as v3.73's spec §4.3 set.

### 4.2 The missing-key fill stays in every migration

`_fills` already reads "from version 2 to this code's highest" (`schema.py:323-328`), so migration 3
carries the fill with it.  A test pins that (test 10).

One defect needs fixing.  `_check` previews the fill only when no migration waits
(`schema.py:562-563`).  Its comment explains why: a pending migration 2 copies every missing key
itself.  With migration 3 pending on a version-2 store, `--check` then shows no fill line although
`migrate` runs the fill (`:641-642`).  The exit code still reads 3, so sigmond still migrates; only
the preview lies.  v3.77 skips the preview only when migration 2 itself waits.

### 4.3 Who writes the new columns

The daemon never migrates (D10).  On a store below version 3 it writes the old column set and no
`last_send`, exactly as v3.76 does.  It reads the version at start and at each rebuild (§3.5).
Between those reads the version cannot rise under it: the manifest step stops the daemon before it
migrates.

- `record_attempt` gains `pipeline: str = ""` and `client: str = ""`, keywords with defaults
  (`hsu/watermark/base.py:114-126`, `hsu/watermark/sqlite.py:459-484`).  Tests and tools that call it
  today keep working.
- Every caller in `hsu/core.py` (`:483`, `:497`, `:517`, `:531`, `:626`, `:646`, `:669`, `:687`,
  `:707`) passes `pipe.name` and `pipe.client`.
- On an `acked` or `partial_ack` outcome, the store upserts `last_send` for `(client, pipeline)` in the
  same transaction as the attempt row.  It skips a transport that ships nothing:
  `DiscardTransport` gains `ships = False` (`hsu/transports/discard.py:30-55`), and the core reads
  `getattr(transport, "ships", True)`.  So a station in `discard` shows no false "last sent".

### 4.4 The backlog report

`hs_uploader.backlog.summarize` (`hsu/backlog.py:70-202`) adds one top-level key, `last_send`: a list
of `{client, pipeline, outcome, ts, records}`, read through `_optional_rows` so a version-2 store
reads an empty list.  Each per-pipeline dict keeps exactly the keys it has.  The heartbeat copies
the `pipelines` and `cursors` lists into the envelope it sends (`sigmond/lib/sigmond/heartbeat.py:414-423`),
and ignores other keys.  So the heartbeat envelope gains no field in v3.77; v3.78 decides what it
shows.

## 5. hs-uploader: `local=` and the producer index

### 5.1 The writer

- `Writer.__init__` and `Writer.from_env` gain `local: bool = False`
  (`hsu/sink/writer.py:295-325`, `:327-339`).  `flush()` stores `1` for a local writer in place of the
  constant `0` (`:434-453`).
- v3.77 adds no switch check at `insert()`.  That check arrives with the gate in v3.78.  A local
  writer will then skip it, as design §6.1 says.
- No caller passes `local=True` until step 4 converts hfdl-recorder and hf-timestd.

### 5.2 Sources never select a local row

A writer that can mark a row local must never ship before every reader honours the mark.  v3.77
therefore ships the filter with the parameter, although D18's list names only the writer side.
Every statement that selects rows to send, or deletes rows it sent, adds `local = 0`:

- `SqliteSource`: the selection, the de-duplication, the `start_at` anchor and `commit()`'s delete
  (`hsu/sources/sqlite.py:610-672`, `:674-750`, `:816-843`, `:437-481`).  The clause applies with or
  without a producer, so it lives beside `_scope_clauses` (`:529-560`), not inside it.
- `WsprCycleSource`: cycle discovery, the per-cycle fetch and the psk rider
  (`hsu/sources/wspr_cycle.py:171-178`, `:311-316`, `:361-365`).

The stale-schema probe (`sqlite.py:752-782`) and the WSPR completion gate (`wspr_completion.py`)
stay as they are.  Neither selects rows to send, and no WSPR row carries `local = 1`.

A sink whose table lacks the `local` column, because no v3.70 writer has flushed into it, gets no
clause.  Each source probes the column once per connection, as it already probes `producer`
(`sqlite.py:543-545`).

Today every row reads `local = 0`, so the clause selects exactly what v3.76 selects.  §8 shows it on
real data.

### 5.3 The index

The writer's schema setup creates `idx_pending_uploads_producer ON pending_uploads (target_db,
target_table, producer, id)` after `ensure_columns`, since the index needs the column
(`writer.py:496-503`).  `PENDING_UPLOADS_DDL` (`:185-187`) carries it for fixtures and tools.

- Whichever writer next opens the sink with new code builds it.  The build holds the write lock.
  Other writers wait up to their 30 s busy timeout (`writer.py:568`).  A writer that times out keeps its buffer and retries at the next flush
  (`writer.py:424-470`).
- Retention keeps the sink small: on 10-08 B4 held 67,795 rows and ND 34,675 (design §13.7).  §8
  times the build on fresh copies before release.
- An index changes how SQLite finds rows, never which rows a statement returns.  Every source
  statement that feeds a batch orders by id or by cycle (`sqlite.py:668`, `:733`, `:744`;
  `wspr_cycle.py:177`, `:316`, `:365`), so batch content and order stay fixed.
- No VACUUM runs, in v3.77 or later (D20).

sigmond's bundled copy follows by the recipe in sigmond's CLAUDE.md
(`sigmond/lib/sigmond/hamsci_sink/_bundled.py`).  `sigmond/tests/test_hamsci_sink_compat.py`
fails until it does.

## 6. hs-uploader: the switch-file library

### 6.1 The module

A new module, `hsu/sinkswitch.py`, stdlib only, so that a client's venv and the daemon load the
same code.  Its directory defaults to `/etc/hs-uploader/sinks`.  The daemon passes the `sinks/`
directory beside its legacy manifest, as it does for `pipelines.d/`; tests pass their own.

- `read_switch(path)` returns the parsed file, a "missing" result, or an error with its reason.
- `effective_state(client, *, sinks_dir)` applies design §3.4's rules.  It takes the stricter of the
  client's file and `site.toml`, in the order `off` < `fill` < `upload`.  A missing client file reads
  `off`.  A missing site file sets no limit.  Any other failure to read or parse either file, and any
  state outside the three, reads `off`.  The function returns the state with its parts: the client
  file's state, the site file's state, and each error.  It caches each file on its modification time,
  size and inode, so a change counts at the next call.  It logs each error once per file signature.
- `write_switch(path, change)` follows design §3.4.  It takes an exclusive `flock` on `sinks/.lock`,
  re-reads the file under the lock, appends one `[[history]]` entry, writes a temporary file in the
  same directory, syncs it, renames it into place, and syncs the directory.  New files get mode 0644.
  It validates the client name by the rule of §3.2, so `../x` never names a path.
- A small TOML emitter writes these files.  Stdlib carries `tomllib` but no writer.  The emitter
  covers strings, booleans, integers, floats, arrays of tables and the nested identity tables, and
  nothing more.  sigmond's `_set_toml_field` stays out of it: that function writes every value as a
  quoted string, so `pinned = false` would turn into the true-reading `"false"`
  (`sigmond/lib/sigmond/psws.py:198-222`).

`stored_intervals()` and `may_send()` wait for v3.78, which first needs them.

### 6.2 Loaded, reported, not enforcing

- At each build the daemon calls `effective_state` once for each client that owns a governed
  pipeline.  It logs one INFO line per client when the answer changes: "sink switch for X would
  read Y (not enforced in this release)".  It logs errors at ERROR, once per file signature.
- It gates nothing.  The pump never consults the switch.  The legacy manifest's policy, rendered
  by sigmond as today, still decides what ships (`sigmond/lib/sigmond/uploader_manifest.py:592-651`).
- `hs-uploader sink status [--json] [--sinks DIR]` prints, for the site and for each client that owns a
  pipeline or has a file: the file's state, the effective state, any error, and the line "not
  enforced: the manifest's policy decides what ships".
- With no `sinks/` directory, which describes every station in v3.77, the daemon logs one INFO line,
  "no switch files; the manifest's policy governs", and `sink status` says the same.

No v3.77 code writes a switch file outside tests.  The carry-over and the site form of `smd sink`
write them in v3.78.

## 7. hs-uploader and sigmond: capabilities

### 7.1 hs-uploader

- Bare `hs-uploader capabilities` keeps printing exactly one line, one integer: `SCHEMA_VERSION`,
  now 3 (`hsu/cli.py:160-163`).  A v3.73-v3.76 sigmond reads it as `int(stdout.strip())`
  (`sigmond/lib/sigmond/uploader_manifest.py:463`), so it must stay so (review M3).
- `hs-uploader capabilities --features` prints one word per line, sorted: `pipelines.d`,
  `send-log-v3`, `switch-files`.  A word appears only in the release that ships its code complete.
  v3.78 adds `gate`; v3.79 adds `cleanup`.
- A v3.73-v3.76 hs-uploader refuses the flag: argparse prints `unrecognized arguments: --features`
  and exits 2.  v3.72 and older refuse the subcommand itself with `invalid choice`.

### 7.2 sigmond

`uploader_manifest.hs_uploader_features(python)` returns the set of words.  It asks the daemon's own
interpreter, as the store-version probe does (`:443-465`).

| The probe sees | It returns |
|---|---|
| exit 0 | the set of non-empty lines; unknown words kept, never an error |
| exit 2 with `unrecognized arguments` or `invalid choice` | the empty set |
| any other failure, or a timeout | `None` |

In v3.77 the manifest step prints one line, `uploader: hs-uploader features: <words>` or `none`, and
nothing reads the answer.  A failure prints a warning and changes nothing.  The probe thus meets
every station's real interpreter one release before v3.78 depends on it.  In v3.78, `None` must keep
the old rendering, exactly as the store-version probe's failure keeps the old manifest today.

## 8. The proof that nothing sends differently

### 8.1 How earlier releases proved it

- v3.70 used `hs-uploader/tools/neutrality_check.py`.  It builds every pipeline of a station's
  manifest twice, once with an OLD source tree and once with a NEW one, each in its own process, on
  private copies of `sink.db` and `watermarks.db`.  A recorder stands in for every transport and
  stops each pump at the first send.  The tool compares, per pipeline, the key, the stored record,
  the queued retries, the batch limits and a digest of the next batch.  It ends `RESULT: AGREE`,
  `DISAGREE` or `INCONCLUSIVE`.  Design §13.7 records the run on B4 and ND copies of 10-08, as
  copied and with send records moved back so that each pipeline had a batch to compare.
- v3.73 added `hs-uploader/scripts/prove_key_split.py`, which compared row sets across the split on
  the same copies (v3.73 spec §8 test 10).

v3.77 reuses the v3.70 tool.  It has no key split to prove, so `prove_key_split.py` plays no part.

### 8.2 Fresh copies

The 10-08 copies predate the split: their stores stand at version 0 with the shared pskreporter key.
v3.77 needs copies of what stations run now, at store version 2 with the split keys.  Before
release, someone takes read-only copies of `/var/lib/sigmond/sink.db`,
`/var/lib/hs-uploader/watermarks.db` and `/etc/hs-uploader/pipelines.toml` from B4 and ND.  They use
`sqlite3 -readonly <db> ".backup <copy>"` as the operator account, compare checksums on the devbox,
and delete the station copies.  B4 work starts with an announcement on the claude-bus.  A read
changes nothing on the station; the announcement still lets Rob's session know.

### 8.3 The runs

OLD names hs-uploader 334e71e, the tree v3.73 through v3.76 carry; the builder confirms that against
the v3.76 build manifest.  NEW names the v3.77 head.  Each run fixes `--now` at the copy's time.

1. **As copied.**  The station's manifest, no `pipelines.d`.  Every pipeline must AGREE.
2. **Send records moved back**, in copies only, as on 10-08: once by about half an hour, once to
   the oldest stored rows.  Every pipeline with rows must produce a batch, and every batch must
   AGREE.
3. **The sink as v3.77 leaves it.**  Open a sink copy with the v3.77 writer's schema setup, which
   adds the index, and time it.  Record `EXPLAIN QUERY PLAN` for each source statement before and
   after, and each statement's run time.  Runs 1 and 2 must AGREE again on this copy.
4. **The store as v3.77 leaves it.**  Run v3.77's `hs-uploader migrate` on a store copy.  It must
   report migration 3, and a digest over every row of `watermarks`, `deliverables`, `dead_letter`,
   `superseded_keys` and `pipeline_state` must match before and after.  `attempts` keeps every old
   column's values, and the two new columns read `''`.  OLD and NEW must then AGREE on the migrated
   store.  OLD on a version-3 store stands for a station rolled back to v3.76.
5. **The same pipelines from `pipelines.d`.**  Move each client's legacy entries into
   `pipelines.d/<client>.toml` in a scratch copy, by the entry's `client`, and drop `start_at` from
   each moved entry, since §3.2 refuses `"now"`.  Leave the heartbeat in the legacy manifest.
   NEW reading the split layout must AGREE with OLD reading the original manifest.  Dropping
   `start_at` matters only for a key with no stored record; the tool prints each stored record, and
   the run must show one for every moved pipeline, or say which key it lacks.
6. **The tool still catches a difference.**  A NEW tree with the `local = 0` clause inverted must
   end DISAGREE on run 2.  A NEW tree whose loader skips the precedence rule must fail run 5.  A run
   that cannot fail proves nothing.

The tool gains `--pipelines-d DIR`.  Its worker calls the new loader when the tree under test has
one, and the old `load_manifest` path otherwise (`tools/neutrality_check.py:304-334`).  `_TapStore`
overrides `get_cursor`, `pop_due_deliverable` and `deliverable_count`, so v3.77 keeps those three
signatures.

### 8.4 What this proof leaves to other tests

As in v3.70, the tool compares what each tree reads and would hand a transport.  The write side
rests on unit tests: the send log and `last_send` (tests 9-14), the rebuild (tests 6-8).  File-tree
pipelines (GRAPE, the magnetometer, the heartbeat) read spools that live on the station, so on the
devbox the tool compares only their keys and stored records.

### 8.5 The update rig and the bench

- **Update rig** (`sigmond-appliance/test-update-v3.sh`, PHASE E-bis): an update in place from
  v3.76 to v3.77, then the manifest step, must show `migrate` reaching version 3 before the daemon
  starts.  A rollback must re-render a manifest that matches the restored release, and the old
  daemon must run on the version-3 store.  A second update must then find nothing to fill.  The rig
  asserts these from the step's output and `PRAGMA user_version`.  That changes the appliance
  repository's tests only.
- **B4, the test bench**, after an announcement on the claude-bus: a first night on v3.77, with ND on
  v3.76 as the control, as v3.70 did.  Expect no loss against ND, no backward send record, a
  `last_send` row for every pipeline that sent, and the same pskreporter, wsprnet and wd30 counts per
  hour as the night before.
- **The rebuild on a real unit**, on B4 under the same announcement: place
  `pipelines.d/bench.toml` with one `filetree` pipeline on an empty scratch directory and
  `discard = true`.  The daemon must build it within one pump and without a restart, and must list
  it in `pipeline_state`.  Then break the file, and the daemon must keep the old build and log one
  ERROR.  Then remove it, and the daemon must drop it.  An empty spool forms no batch, so the bench
  key never gets a record.

## 9. Tests

Each test must fail before its change and pass after.  The commit message names the mutation that
proved it.  hs-uploader's tests live in `hs-uploader/tests/`; sigmond's in `sigmond/tests/`.

| # | Test | Mutation that must make it fail |
|---|---|---|
| 1 | With no `pipelines.d`, the loader yields the same entries, in the same order, as `load_manifest` | the loader drops `client` from legacy entries |
| 2 | Precedence: with `pipelines.d/psk-recorder.toml` present, the daemon builds none of psk-recorder's legacy entries and all others; a broken file still silences them | skip the precedence rule; or fall back to legacy entries when the file fails |
| 3 | The directory comes from the manifest's parent: `--manifest /tmp/x/pipelines.toml` reads `/tmp/x/pipelines.d` | hard-code `/etc/hs-uploader/pipelines.d` |
| 4 | File rules: client from the file name; a mismatched `client`; `../x`, `a@b`, `A` refused; `start_at = "now"` refused; `[daemon]` and `ssh_key_file` refused; `wspr_cycle` defaults to `beginning` in a file and stays `now` in the legacy manifest | drop the `"now"` refusal; apply the `beginning` default to legacy entries |
| 5 | Collisions: a file that repeats a legacy key or name is refused and both are named; a collision inside the legacy manifest alone logs an ERROR and runs | remove the refusal; make a legacy collision fatal |
| 6 | Rebuild: a changed file rebuilds without restart; an unchanged pipeline keeps its `Pipeline` object and executor; a removed one's executor shuts down; a queued retry survives; an identical rebuild changes nothing; SIGHUP triggers one; an edit to the legacy manifest alone, or with SIGHUP, changes no running pipeline until restart | rebuild every pipeline each time; drop the stat check; re-read the legacy manifest at rebuild |
| 7 | The store check at rebuild: a producer pipeline arriving by file on a version-1 store gets refused, and `pipeline_state` says why | skip `split_refused` at rebuild |
| 8 | A file broken on rebuild keeps its last good build; broken at start, it silences only its client; neither stops the daemon | let the parse error propagate |
| 9 | Migration 3: a version-2 store reaches 3; columns and `last_send` exist; every other row hashes the same; a version-0 store runs 1, 2 and 3 in order | omit the ALTER for `client` |
| 10 | The fill at version 3: a version-3 store with a shared pskreporter record and no producer keys gets both keys filled by `migrate` | bound `_fills` to version 2 |
| 11 | `--check` on a version-2 store missing a producer key, with migration 3 pending, previews the fill line | restore the `not pending` condition at `schema.py:562-563` |
| 12 | The send log: on a version-2 store the daemon writes the old columns and no `last_send`; on version 3 it writes `pipeline`, `client` and `last_send` for `acked` and `partial_ack` only; a discarding pipeline writes no `last_send` | write the new columns whatever the version; drop the `ships` check |
| 13 | An old writer on a new store: an INSERT naming only v3.76's columns succeeds on a version-3 store, the new columns reading `''` | declare a new column `NOT NULL` without a default |
| 14 | The backlog: `last_send` sits at the top level; each per-pipeline dict keeps exactly v3.76's keys | put `last_send` inside the per-pipeline dicts |
| 15 | The writer: `local=True` stores 1, the default stores 0; an old sink gains the index after `ensure_columns`; `PENDING_UPLOADS_DDL` builds it | store the constant 0; create the index before `ensure_columns` |
| 16 | No source selects a local row: `SqliteSource` with and without a producer, the de-duplication path, the anchor, `commit()`'s delete, and `WsprCycleSource`'s three statements; a sink without the `local` column still reads | drop the clause from each statement in turn |
| 17 | Switch read rules: missing client file, missing site file, parse error, unknown state, stricter-of, the cache seeing a rewrite | swap the order `off` < `fill` < `upload`; read a parse error as a missing file |
| 18 | `write_switch`: two processes racing keep both history entries; a reader never sees half a file; `pinned = false` survives a round trip as a boolean; `../site` refused | drop the re-read under the lock; drop the name check |
| 19 | Not enforcing: a client file reading `off` leaves that client's pipeline building and shipping to a stand-in transport | consult `effective_state` in `_pump_one` |
| 20 | Capabilities: bare output reads exactly `3\n`, which v3.73's parser accepts; `--features` prints the three words sorted | print the features after the integer on the bare call |
| 21 | sigmond's probe: v3.77 output gives the set; exit 2 with `unrecognized arguments` or `invalid choice` gives the empty set; a timeout gives `None`; a failure never fails the manifest step | read exit 2 as `None`; return 1 from the step on a failed probe |
| 22 | The neutrality tool reads `--pipelines-d` and ends DISAGREE on a planted difference (§8.3 run 6) | ignore `--pipelines-d` |
| 23 | sigmond's existing step tests gain one case: a fake hs-uploader whose `capabilities` prints 3 and whose `migrate --check` exits 3 gets stop, migrate, land, start in that order | land before migrate |

## 10. Version pairings and rollback

v3.77 updates stations in place.  `smd update` pulls code and runs no migration (D10).  The next
manifest step, from `smd align` or the image's own sequence, migrates the store.

| Case | What happens | Sends |
|---|---|---|
| **Both v3.77** | `smd update` pulls both.  A daemon restarted on new code meets a version-2 store, writes the old send-log columns and finds no pipeline file.  The next manifest step sees `migrate --check` exit 3, stops the daemon, runs migration 3, and starts it.  The rendered manifest stays byte-identical, so only the migration bounces the daemon, for a second or two. | unchanged |
| **v3.77 sigmond, v3.73-v3.76 hs-uploader** | The features probe gets exit 2 and prints `none`.  Bare `capabilities` reads 2, so sigmond renders the split as today.  `migrate --check` exits 0.  Nothing migrates. | unchanged |
| **v3.73-v3.76 sigmond, v3.77 hs-uploader** | Bare `capabilities` reads 3, and the old sigmond renders the split, since 3 ≥ 2.  It trusts the exit code, sees 3, and runs the stop, migrate, start order with `--db`, `--sink` and `--keep-retry-for`, which v3.77's `migrate` still takes.  The store reaches version 3.  The daemon finds no pipeline file and no switch file. | unchanged |
| **Rollback of both** after v3.77 ran | D11's restore re-renders with v3.76's sigmond.  The v3.76 daemon opens a version-3 store: its `CREATE TABLE IF NOT EXISTS` changes nothing, and its INSERTs name their own columns, so the new ones default to `''`.  `last_send` stands still.  v3.76's `migrate` leaves a newer store as it stands (`schema.py:576-603`): no pending migration, no fill, so no bounce.  The sink keeps the new index, which every writer maintains and no reader needs.  A `pipelines.d` file, if one existed, stops sending; none exists in production. | unchanged |
| **Rollback of hs-uploader alone** | The second row, on a version-3 store. | unchanged |
| **Rollback of sigmond alone** | The third row. | unchanged |
| **Update after a rollback** | v3.77's `migrate` finds version 3: nothing pending.  The fill still runs (§4.2), so a producer key missing since the rollback gets the old shared record.  The retry pass runs as in v3.73. | unchanged |

No row depends on a changed unit file, a new directory, or a client repository.

## 11. What v3.78 inherits

| v3.78 needs | v3.77 provides | v3.78 still does |
|---|---|---|
| `may_send(pipe)` in `_pump_one`, before each replay, before each batch | `Pipeline.client` on every path; `governed` parsed and stored; `effective_state` with design §3.4's rules, logged | the gate itself; treat the legacy heartbeat (`client = "sigmond"`, transport `heartbeat_sftp`) as ungoverned, since no legacy entry carries `governed = false` |
| a probe that proves the daemon gates | `capabilities --features`, sigmond's parser, a release of field exercise | add `gate`; render by the site state the old way when the word is absent or the probe returns `None` |
| switch files on every station | `write_switch`, the TOML emitter, the lock | the carry-over, and its marker; the site form of `smd sink` writing `site.toml` and `[uploads]` both (D19) |
| the psk rider gate in the wsprdaemon tar | the producer index; the D12 clause in SQL | `stored_intervals()`; the gate inside `WsprCycleSource` (design §6.6) |
| the writer's switch check at `insert()` | `local=` and the source filter | the check, idle without a switch file; the refreshed bundled copy |
| pipeline files from clients | the loader, precedence, rebuild, store check at rebuild, collision refusal, the `"now"` refusal | decide whether the heartbeat moves to `pipelines.d/sigmond.toml` with `governed = false`; retire the §5.1 rewrite only when psk-recorder and meteor-scatter ship their own files (step 4) |
| status that names the last send | `last_send` and its backlog key | show it in the heartbeat and in `smd sink status`, each saying "none since the upgrade" where the table holds no row |
| later migrations | migration machinery, the fill tied to `SCHEMA_VERSION`, the corrected `--check` preview | keep both in every new migration |
| a legacy collision made fatal | an ERROR on every station that carries one | harden it once §8's copies show no station carries one |
| the unit file's own change | nothing; the daemon finds both directories from its manifest | `ConditionDirectoryNotEmpty=` waits for the release that re-runs hs-uploader's installer (design §3.5) |

## 12. Out of scope

The gate, `may_send` and `stored_intervals`; the psk rider gate; the writer's switch check; the
carry-over and its marker; `smd sink fill` and the site form writing `site.toml` (D19); stopping
in-process senders; cleanup in the daemon, the 5 GB ceiling, `sink drop` and the retirement of
sigmond's trim units (v3.79); any VACUUM (D20); anything new for meteor-scatter, the Zenodo upload
included (D21); per-instance pipelines; a changed unit file; any client repository change; the
wd30 fleetboard.

## 13. Open questions

Settled from the code or D1-D21, so not asked:

- Whether `smd update` should migrate: no (D10).  The manifest step does.
- Whether to backfill `last_send`: no.  The old attempt log names no pipeline (§4.1).
- Whether a broken pipeline file at start should stop the daemon: no.  D17 (4) refuses only what
  cannot run, and §3.2 keeps a broken file from sending.
- Whether the bare probe may change shape: no (M3).  Features travel in `--features` (§7).
- Whether `local=` needs the source filter now: yes (§5.2), on the rule that a mark nobody honours
  must not exist.  It selects the same rows on today's data, and §8 checks that.
- Where the switch-file report lives in v3.77: hs-uploader's journal and `hs-uploader sink status`.
  No switch file exists on a station until v3.78, so sigmond shows nothing yet.

Ruled by Michael, 2026-10-21:

- **Q1 — wait.**  The heartbeat stays in the legacy manifest until v3.78.
- **Q2 — restart.**  The daemon does not watch the legacy manifest; an edit takes effect at restart
  (§3.4).
- **Q3 — mjh's session takes the copies,** after announcing on the claude-bus, as read-only
  `sqlite3 .backup` copies like those of 10-08.

The questions as asked:

1. Should v3.77 also move the heartbeat into `pipelines.d/sigmond.toml`?  Doing so would run the new
   loader on every station a release early, with a pipeline whose key and bytes stay the same.  This
   spec leaves it for v3.78, where `governed = false` gives the move a purpose, and lets the B4 bench
   file of §8.5 carry the field test instead.
2. Should a hand edit of the legacy manifest take effect within one pump, as §3.4 proposes, or keep
   waiting for a restart as today?  sigmond's own writes never reach a running daemon either way.
3. The fresh copies of §8.2 need a read on B4 and ND.  Does Michael take them, or does Rob's session,
   given that B4 runs Rob's experiments?

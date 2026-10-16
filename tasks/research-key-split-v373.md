> **Copied into tasks/ on 2026-10-16** from the git-excluded `.superpowers/research/` so Rob can read
> it.  The text below stands as written on 2026-10-10.  Since then the step moved from v3.72 to v3.73
> (`plan-sink-control.md` D16), and the six questions at the end are still open.  Code cited here
> has moved since the commits it names; re-read a line before you rely on it.

# v3.72 key split (sink control step 2a-ii): research map

Written 2026-10-10 from code and from the 2026-10-08 station snapshots.  Nothing here proposes tasks.
Section J lists the traps; read it first if time runs short.

## How to read this map

- Every path starts at `/home/mjh/hamsci/repos/`.  `hsu/` abbreviates `hs-uploader/src/hs_uploader/`.
- Commits read: hs-uploader 8376fd8, sigmond 5f39b05 (31 commits past the v3.70 pin 7fe58c7; of the
  files cited here only `bin/smd` changed, for remote access), psk-recorder 8dfb64f, meteor-scatter
  83213d0, wspr-recorder 65713b5, mag-recorder 81b6404 (B4 runs ec95cc0), hamsci-physics 04facaf,
  sigmond-appliance c348e21.
- "Design" means `sigmond/tasks/plan-sink-control.md`.  Its line numbers appear as `design:NNN`.
- MEASURED marks a number I read from a snapshot in `/home/mjh/v370-t10/`.
- TESTED marks a behaviour I ran: a throwaway script in the session scratch directory imported
  `hs-uploader/src` at 8376fd8 and drove real `Uploader`, `Pipeline`, `SqliteSource` and
  `SqliteWatermarkStore` objects against temporary files.  It wrote nothing into any repository.
- UNVERIFIED marks a statement that code and snapshots could not settle, with what would settle it.
- I ran no test suite.  No doubt needed one.

---

## A. The key today

### A1. Where the key gets formed

The store's primary key holds three strings (`hsu/watermark/sqlite.py:35-42`,
`PRIMARY KEY (source_id, dest_id, table_name)`).  `core.py` builds the triple at each call, from the
live objects, and caches nothing:

| Part | Formed by | Line |
|---|---|---|
| source_id | `Pipeline.source_id()` returns `self.source.source_id()` | `hsu/core.py:210-211` |
| dest_id | `Pipeline.dest_id()` returns `t.name`, the transport's name | `hsu/core.py:213-215` |
| table | `pipe.transport.primary_table()` | called at each use, below |

`Pipeline` carries `name`, `source`, `transport`, `watermark`, `identity`, `retry`,
`fallback_transport`, `batch_limit`, `max_records_per_pump` (`hsu/core.py:182-208`).  It carries no
client and no producer.  Nothing reads `fallback_transport`; `dest_id(transport)` never receives an
argument.

The pipeline's name never enters a send-record key.  The name keys three other things: queued
retries (`deliverables.pipeline`), dead letters (`dead_letter.pipeline`), and the pump thread's name
(`hsu/core.py:271`).

### A2. Every `source_id()`

| Source class | String | Line | Seen in the snapshots |
|---|---|---|---|
| `SqliteSource` | `f"sqlite:{self.database}.{self.table}"` | `hsu/sources/sqlite.py:335-336` | `sqlite:psk.spots`, `sqlite:wspr.spots` |
| `WsprCycleSource` | `f"sqlite:{_LOGICAL_TABLE}"`, with `_LOGICAL_TABLE = "wspr.cycle"` | `hsu/sources/wspr_cycle.py:57, 126-127` | `sqlite:wspr.cycle` |
| `FileTreeSource` | `source_id or f"files:{self.root}"` | `hsu/sources/files.py:88, 99-100` | `files:/var/lib/sigmond/heartbeat`, `grape-datasets:/var/lib/timestd/upload`, `mag-datasets:/var/lib/mag-recorder/upload` |

`SqliteSource.source_id()` ignores `extra_where`, `select_columns`, the de-duplication settings, the
ceiling and `start_at`.  Two sources that select different rows of one table form one id.  The
factory passes a manifest `source_id` only to file-tree sources (`hsu/pipeline_factory.py:95`); a
sqlite or wspr_cycle spec has no way to set one.

In-process fallbacks form two more ids when no `sink.db` exists: `psk-spool:<radiod_id>`
(`psk-recorder/src/psk_recorder/core/hs_uploader_shim.py:453`), `ms-spool:<radiod_id>`
(`meteor-scatter/src/meteor_scatter/core/hs_uploader_shim.py:463`) and `wsprnet-spool:<instance>`
(`wspr-recorder/wspr_recorder/hs_uploader_shim.py:1157`).  A sigmond station always has `sink.db`,
so none of them appears in a snapshot.

### A3. Every destination id

| Transport class | Default `name` | Line | What stations hold |
|---|---|---|---|
| `PskReporterTcp` | `f"pskreporter-tcp:{host}:{port}"` | `hsu/transports/pskreporter.py:127` | `pskreporter-tcp:report.pskreporter.info:4739` (no `name` rendered) |
| `WsprNet`, async API | `f"wsprnet-async:{self._api_base_url}"` | `hsu/transports/wsprnet.py:105-106` | `wsprnet-async:https://wsprnet.org/api/upload/v1` |
| `WsprNet`, legacy POST | `f"wsprnet:{url}"`, so `wsprnet:http://wsprnet.org/meptspots.php` | `hsu/transports/wsprnet.py:94` | none; an in-process shim with `WSPRNET_API_BASE=legacy` would form it |
| `WsprdaemonTarSftp` | `f"wsprdaemon-tar-sftp:{','.join(servers)}"` | `hsu/transports/wsprdaemon.py:836` | `wsprdaemon-tar-sftp:gw1.wsprdaemon.org,gw2.wsprdaemon.org` |
| `WsprdaemonTarFtp` | `f"wsprdaemon-tar-ftp:{','.join(servers)}"` | `hsu/transports/wsprdaemon.py:1128` | never a key: it runs only as `fallback_ftp` inside the SFTP transport |
| `PswsMagnetometerSftp` (alias `PswsDatasetSftp`, `:364`) | `f"psws-sftp:{host}:{instrument_id}"` | `hsu/transports/psws_magnetometer.py:156` | manifests override it: `psws-grape-sftp:pswsnetwork.eng.ua.edu:<station>`, `psws-mag-sftp:pswsnetwork.eng.ua.edu:<station>` |
| `HeartbeatSftp` | `f"heartbeat-sftp:{host}"` | `hsu/transports/heartbeat_sftp.py:100` | `heartbeat-sftp:wd30.wsprdaemon.org` |
| `DiscardTransport` | borrows `inner.name` and `inner.primary_table()` | `hsu/transports/discard.py:33, 37-38` | the same key as the real transport |

The server list order enters the wsprdaemon id.  A shim whose `WD_SFTP_SERVERS` lists gw2 before gw1
forms a different key from the daemon's.

The factory accepts a manifest `name` for every transport: psws (`hsu/pipeline_factory.py:149-150`),
pskreporter (`:165`), wsprnet (`:181-182`), wsprdaemon_tar (`:210`), heartbeat (`:235-236`).  Only
the two PSWS pipelines use it today.

### A4. Every `primary_table()`

| Transport | Value | Line |
|---|---|---|
| `PskReporterTcp` | constructor `primary_table`, default `"psk.spots"` | `hsu/transports/pskreporter.py:113, 138-139` |
| `WsprNet` | `"wspr.spots"`, fixed | `hsu/transports/wsprnet.py:120-121` |
| `WsprdaemonTarSftp` | constructor `primary_table_name`; class default `"wspr.spots"`, factory default `"wspr.cycle"` | `hsu/transports/wsprdaemon.py:819, 862-866`; `hsu/pipeline_factory.py:221` |
| `PswsMagnetometerSftp` | `self.table`, default `"mag.daily_zip"`; GRAPE passes `"grape.dataset"` | `hsu/transports/psws_magnetometer.py:70, 160-161` |
| `HeartbeatSftp` | `"station.heartbeat"` | `hsu/transports/heartbeat_sftp.py:57, 104-105` |

### A5. Who calls `get_cursor`, `advance_cursor`, `reset_cursor`

| Call | Where | Key it uses |
|---|---|---|
| `get_cursor` | `Uploader._drain_source`, once per drain loop pass, `hsu/core.py:407-410` | the pipeline's current triple |
| `advance_cursor_checked` | first-attempt ack, `hsu/core.py:465-469` | the pipeline's current triple |
| `advance_cursor_checked` | first-attempt partial ack, `hsu/core.py:482-487` | the pipeline's current triple |
| `advance_cursor_checked` | replay ack of a queued retry, `hsu/core.py:546-554` | the triple stored in the retry row, falling back field by field to the pipeline's: `deliverable.source_id or pipe.source_id()` |
| `get_cursor` then `advance_cursor` | inside `advance_cursor_checked`, `hsu/watermark/sqlite.py:243, 253` | as given |
| `reset_cursor` | `hs-uploader reset-cursor --source --dest --table`, `hsu/cli.py:52-60, 171-182` | typed by the operator |
| `all_cursors` | `hs-uploader status`, `hsu/cli.py:137-151` | lists every row |

Nothing else in the seven repositories calls them.  No client calls the store's cursor methods
itself; each in-process sender goes through `Uploader`.

Two readers open `watermarks.db` directly, read-only, without the store class:

- `hsu/backlog.py:89-95` selects every row of `watermarks` and counts `deliverables` and
  `dead_letter` per pipeline name (`:96-109`).  sigmond's heartbeat runs it as a child
  (`sigmond/lib/sigmond/heartbeat.py:856-884`) and ships the result (`:413-458`).
- `hamsci-physics/src/hamsci_physics/grape/spool.py:116-121` reads the GRAPE record by
  `source_id = "grape-datasets:/var/lib/timestd/upload"` and `table_name = "grape.dataset"`
  (`:56-57`), with no `dest_id`.  When several rows match, "the *lowest* cursor wins" (`:107-109`).

### A6. The store's tables

All four live in `hsu/watermark/sqlite.py:34-80`.

| Table | Columns | Carries the key | Carries a pipeline name |
|---|---|---|---|
| `watermarks` | `source_id, dest_id, table_name, cursor BLOB, last_ack TEXT` | yes, as primary key | no |
| `attempts` | `rowid, ts, source_id, dest_id, table_name, outcome, records, bytes, error` | yes, as history | no |
| `deliverables` | `id, pipeline, payload_blob, enqueued_at, attempts, next_attempt_at, source_id, dest_id, table_name, cursor_after, commit_token` | yes; the five trailing columns default to empty | yes |
| `dead_letter` | `rowid, ts, pipeline, payload_blob, final_error` | no | yes |

`attempts` keeps the last 10,000 rows (`:32, 349-354`).  One index covers
`deliverables(pipeline, next_attempt_at)` (`:70-71`).  `PRAGMA user_version` holds the schema
version; the store class never reads it.

### A7. The keys the two stations held on 2026-10-08 (MEASURED)

| source_id | dest_id | table_name | B4 cursor | ND cursor |
|---|---|---|---|---|
| `sqlite:psk.spots` | `pskreporter-tcp:report.pskreporter.info:4739` | `psk.spots` | `3094697` | `280067` |
| `sqlite:wspr.spots` | `wsprnet-async:https://wsprnet.org/api/upload/v1` | `wspr.spots` | `3094550` | `279936` |
| `sqlite:wspr.cycle` | `wsprdaemon-tar-sftp:gw1.wsprdaemon.org,gw2.wsprdaemon.org` | `wspr.cycle` | `2026-10-08T06:52:00Z` | `2026-10-08T06:52:00Z` |
| `files:/var/lib/sigmond/heartbeat` | `heartbeat-sftp:wd30.wsprdaemon.org` | `station.heartbeat` | `<delete-on-ack>` | `<delete-on-ack>` |
| `grape-datasets:/var/lib/timestd/upload` | `psws-grape-sftp:pswsnetwork.eng.ua.edu:S000170` (B4), `…:S000111` (ND) | `grape.dataset` | `1791434228175757852` | `1791430357212023800` |
| `mag-datasets:/var/lib/mag-recorder/upload` | `psws-mag-sftp:pswsnetwork.eng.ua.edu:S000111` | `mag.daily_zip` | no row | `<delete-on-ack>` |

Both stores read `user_version` 0.  `deliverables` holds 0 rows on both.  `dead_letter` holds 0 rows
on B4 and 1 on ND, under pipeline `heartbeat`.  `attempts` holds 10,000 rows on B4 and 3,178 on ND.
B4's manifest renders `mag-psws`, yet B4 holds no mag record: a key with no row forms a normal case
for any migration.

---

## B. Queued retries ("deliverables")

### B1. How a batch gets queued

A first attempt that returns `retry_later` serializes the batch through the transport and stores one
row (`hsu/core.py:496-508`):

```python
pipe.watermark.enqueue_deliverable(
    pipeline=pipe.name, payload_blob=payload, ...
    source_id=pipe.source_id(), dest_id=pipe.dest_id(), table=table,
    cursor_after=batch.cursor_after, commit_token=batch.commit_token)
```

The row therefore freezes six things at queue time: the pipeline's name, the three key parts, the
cursor the batch would store, and the source's commit token.  The payload freezes more.
`PskReporterTcp.serialize_for_retry` writes the receiver's callsign, locator and software string
ahead of the encoded spots (`hsu/transports/pskreporter.py:153-177`), so a queued pskreporter retry
also carries the identity of the moment it queued.

`enqueue_deliverable` refuses a second row for the same `(pipeline, cursor_after)` and returns the
first row's id (`hsu/watermark/sqlite.py:381-399`).  It never collapses an empty `cursor_after`.

A first attempt that returns `permanent` writes `dead_letter` with the pipeline's name and no key
(`hsu/core.py:516-522`).

### B2. How a replay works

- `_pump_one` drains queued retries first, then refuses to drain the source while any retry waits
  under the pipeline's name: `if pipe.watermark.deliverable_count(pipe.name) > 0: return did_work`
  (`hsu/core.py:326-336`).
- `_drain_deliverables` asks `pop_due_deliverable(pipe.name, now=…)` (`hsu/core.py:358-360`).  The
  store selects by `pipeline=?` and deletes the row as it hands it over
  (`hsu/watermark/sqlite.py:416-431`).
- The transport replays the stored payload: `pipe.transport.replay(deliverable.payload_blob,
  pipe.identity)` (`hsu/core.py:364`).
- On ack, the core advances the send record under the row's stored key and commits the row's token
  to the pipeline's current source (`hsu/core.py:546-555`).  It writes the `attempts` line under the
  pipeline's current key, not the row's (`:556-565`).
- A row whose `cursor_after` reads empty advances nothing (`if deliverable.cursor_after:`, `:546`).
- On another `retry_later` the core requeues, or dead-letters at `max_attempts` (12 by default,
  `hsu/core.py:164, 568-586`).  After one `retry_later` the loop stops for this pump (`:366-379`).

### B3. What D15 changed in v3.70

Commit eab40d3.  Before it, the requeue rebuilt the row field by field and left out `source_id`,
`dest_id`, `table`, `cursor_after` and `commit_token`.  Now `dataclasses.replace` changes only
`attempts` and `next_attempt_at` (`hsu/core.py:587-598`).  The design cites the old lines,
`core.py:585-594` (design:102).

A row that a pre-v3.70 daemon requeued still sits keyless: `source_id = ''`, `cursor_after = x''`.
`tests/test_requeue_keeps_key.py:128-163` pins that such a row survives and that its ack "advanced
nothing".  A station that updates straight from v3.69 or older can hold such rows.

### B4. What a key migration must rewrite here

Design §6.2 (design:609-615) asks the migration to re-home queued retries.  The table gives three
separate things to rewrite:

1. `deliverables.source_id` (and `dest_id`, `table_name` if those change), for rows whose key gets a
   new form.
2. `deliverables.pipeline`, for rows queued under a pipeline name that the new manifest retires.
3. Keyless rows carry no key to map.  Only their pipeline name can route them.

`dead_letter` rows carry only a pipeline name.  Nothing replays them, and no CLI clears them.  The
heartbeat reports them by name for as long as they exist (`sigmond/lib/sigmond/heartbeat.py:430-436`).

### B5. What happens to a retry left where it stands (TESTED)

- Left under a retired pipeline name: no pipeline ever pops it.  In the test the new pipeline
  replayed nothing, drained the same ten rows fresh from the source, and the row stayed queued.  The
  heartbeat then reports "deliverables retrying" for that name without end
  (`sigmond/lib/sigmond/heartbeat.py:438-443`).
- Left on the old key, under a pipeline name that survives but whose source now forms a new key: the
  replay acked and wrote the old key's row; the pipeline then drained the same ten rows from the
  source and sent them again.  The store ended with two rows, `sqlite:psk.spots` and
  `sqlite:psk.spots@psk-recorder`, both at `10`.  The design predicts the first half (design:612);
  the second send follows from `hsu/core.py:334-336` once the queue empties.

Neither snapshot holds a queued retry, so real data cannot exercise any of this.

---

## C. The schema-version machinery from v3.70

### C1. `hsu/watermark/schema.py`

| Piece | Line | Behaviour |
|---|---|---|
| `SCHEMA_VERSION = 1` | `:48` | the version this code knows |
| `BUSY_TIMEOUT_S = 30.0` | `:53` | wait for another writer's lock |
| `Migration(version, summary, apply)` | `:65-73` | `apply(conn)` receives the open connection; `name` reads `"<version>: <summary>"` |
| `MigrateReport(from_version, to_version, applied, pending)` | `:76-81` | two lists of migration names; no place for per-row detail |
| `MIGRATIONS` | `:88-91` | one entry: `Migration(1, "record the schema version; changes no row and no table", _record_version_only)` |
| `_connect(db, mode)` | `:94-99` | URI open with `mode=ro` or `mode=rw`; never creates a file; `isolation_level=None` |
| `_inspect` | `:106-119` | raises `MigrateError` when the file holds no `watermarks` table |
| `_check` | `:131-144` | read-only; returns the version and the names of pending migrations; raises `JournalRecoveryNeeded` on a hot journal |
| `migrate(path, *, check=False)` | `:147-195` | see below |

`migrate` without `check`: it opens read-write (`:167`), reads the version, and returns at once with
no write lock when nothing waits (`:170-172`).  Otherwise it runs `BEGIN IMMEDIATE` (`:176`), reads
the version again under the lock (`:178`), and for each migration above that version calls
`m.apply(conn)` and then `PRAGMA user_version = <n>` (`:181-187`).  One `COMMIT` ends the run
(`:188`).  Any exception rolls back every step (`:189-192`).

A store whose version exceeds the code's needs nothing and gets no write (`:151-153`).

### C2. The CLI

`hs-uploader migrate [--db PATH] [--check]` (`hsu/cli.py:69-87`).  The path resolves as
`args.db or args.state or default_path()` (`:200`).  `default_path()` reads `HS_UPLOADER_STATE_DIR`
from the caller's environment, else `/var/lib/hs-uploader` (`hsu/watermark/sqlite.py:83-85`).

| Case | Output | Exit | Line |
|---|---|---|---|
| file missing | `watermarks.db: not found at <path>; nothing to migrate` | 0 | `:202-206` |
| done or nothing to do | `watermarks.db: version <N>`, then `  applied <name>` / `  pending <name>` lines, or `  nothing to do` | 0 | `:219-229` |
| store newer than the code | adds `newer than this hs-uploader, which knows versions up to <N>; left as it stands` | 0 | `:224-226` |
| `--check` meets a hot journal | one line naming the journal | 0 | `:208-214` |
| any other failure | `hs-uploader migrate: <path>: <Type>: <message>` on stderr | 1 | `:215-218` |
| a binary without `migrate` | argparse prints `invalid choice: 'migrate'` | 2 | n/a |

`migrate` never constructs the store class, so it never creates a file or changes a mode (`:118-121`).

### C3. Which sigmond code runs it

`sigmond/lib/sigmond/commands/uploader.py:143-188`, `_run_migrate()`.  It runs
`runuser -u hsupload -- /opt/hs-uploader/venv/bin/hs-uploader migrate`, with working directory
`/opt/hs-uploader`, a 120-second limit (`:48, 158-168`), and no `--db`.  It returns 0 without running
anything in two cases: the venv holds no `hs-uploader` binary (`:159-161`), or the binary predates
`migrate` (`:177-180`).  It relays each stdout line with the prefix `uploader: ` (`:181-183`).

`cmd_uploader_manifest` (`:191-311`) calls it on `--write` and on `--enable`, never on the read-only
check.  The order inside one call:

1. render (`:212`);
2. write `pipelines.toml`, with a `.bak` of the old one (`:263-277`);
3. `_run_migrate()` (`:290`); on failure print "left as it stands" and return 1 (`:290-294`);
4. with `--enable` only: `systemctl enable --now`, then `systemctl restart` when the manifest changed
   or the daemon started before the manifest's last write (`:299-310`).

The daemon keeps running, on its old in-memory manifest, through steps 1 to 3.

Who reaches `cmd_uploader_manifest`:

| Command | Flags | Line | Note |
|---|---|---|---|
| bring-up | `--write --enable` | `sigmond/lib/sigmond/bringup.py:426-429` | bring-up then restarts hs-uploader unconditionally, `may_fail=True` (`:517-520`) |
| `smd apply` | write + enable | `sigmond/bin/smd:10418-10426` | discards the return code; an exception prints "skipped" |
| `smd align --apply` | `--write` only, as a child, 240 s limit | `sigmond/bin/smd:6147-6163` | align restarts the daemon later, itself, when the step reports "manifest changed" or the checkout moved after the daemon started (`:5311-5329, 5410-5416, 5442-5444`) |
| `smd sink off\|upload`, `smd upload …`, `smd config uploads …` | write + enable | `sigmond/lib/sigmond/commands/sink.py:32-36` → `config.py:1001-1005`, called at `:1227` | |
| `smd admin manifest restore --apply` | `--write --enable`, run by the restored checkout's own `smd` | `sigmond/bin/smd:4370-4431` | D11 |
| `smd admin uploader manifest --write\|--enable` | as typed | `sigmond/bin/smd:25046-25053` | |

### C4. What `smd update` does and does not run

`smd update --apply` pulls each planned checkout and runs each planned `install.sh`
(`sigmond/bin/smd:3987-4050`).  For the restart step it prints "restart not automated here"
(`:4051-4053`).  It never renders the manifest, never migrates, and never restarts hs-uploader.

The daemon's venv installs hs-uploader editable (`hs-uploader/install.sh:117`), and so do the client
venvs.  A pull therefore changes the code on disk for every process at once.  Each running process
keeps its old code until something restarts it.

On stations the client checkouts sit detached at a `.pin` that `smd align` wrote.  `smd update`
holds them until `--unpin`.  B4's v3.70 update log shows five clients "HELD — pinned by smd align"
while hs-uploader and sigmond moved (`/home/mjh/v370-t10/b4-v370-update-20261010T003202Z.log`).

### C5. Adding migration number 2

By the code and by `hs-uploader/CLAUDE.md` ("To add a migration, append a `Migration` to
`MIGRATIONS` and raise `SCHEMA_VERSION` to match.  Never edit a migration that has shipped."):

- append `Migration(2, "<summary>", <function>)` after the first entry in `MIGRATIONS`
  (`hsu/watermark/schema.py:88-91`);
- raise `SCHEMA_VERSION` to 2 (`:48`);
- the function receives a connection that already holds the `BEGIN IMMEDIATE` lock; it must not
  commit, and an exception it raises rolls back the whole run;
- the runner stamps `user_version = 2` after the function returns (`:185`).

Tests that pin the present numbers and must move with it:
`tests/test_watermark_schema.py:150-153` asserts `schema.SCHEMA_VERSION == 1` and gapless versions;
the rig asserts the version it finds (section I).

### C6. What D9 and D10 demand of it

D9 (design:96): copy each old send record to its new keys; never delete the old row; the old row
"stays frozen" so a rolled-back station resumes from it.  In SQL terms the function may `INSERT`
into `watermarks` and must not `DELETE` or `UPDATE` an old-key row there.

D10 (design:97): the migration runs only as `hs-uploader migrate`; only the manifest step calls it,
after the write and before the daemon starts or restarts; `smd update` alone does not; "The daemon
never migrates while it starts; a store still on an older version keeps its older keys".

Design §6.2 adds: "The migration prints the old key, the new key and the record" (design:613-614),
and it re-homes queued retries (design:610-612).

What the machinery lacks for those demands:

- `MigrateReport` holds only migration names, and `apply` returns nothing.  No channel carries
  per-row lines from a migration to `_cmd_migrate`'s output.
- `--check` lists the names of pending migrations.  It has no dry run of a migration's effect.
- Neither the daemon nor the store class reads `user_version` (`hsu/watermark/schema.py:17-19`;
  `hsu/daemon.py:159-160`), so nothing today can "keep older keys" on an older store.
- `migrate` receives only a database path.  It reads no manifest.  Any old-to-new mapping must
  follow from the stored rows alone.
- A migration runs once.  After a rollback and a second update, the version already reads 2 and the
  copy never runs again (`:181-183`).

---

## D. The forward-only check

### D1. Each source's `cursor_is_after(new, stored)`

| Source | Order | Line | Equal values | Undecodable |
|---|---|---|---|---|
| protocol default | always True | `hsu/sources/base.py:56-72` | True | True |
| `SqliteSource` | integer ids, `new > stored` | `hsu/sources/sqlite.py:418-433` | False | True, logged at DEBUG |
| `WsprCycleSource` | text compare of ASCII, `new.decode() > stored.decode()` | `hsu/sources/wspr_cycle.py:195-211` | False | True, logged at DEBUG |
| `FileTreeSource`, KEEP | integer nanoseconds through `_decode_keep_cursor`, which also reads a legacy float-seconds value | `hsu/sources/files.py:233-254, 286-302` | False | True, logged at DEBUG |
| `FileTreeSource`, delete_on_ack | always True | `hsu/sources/files.py:243-244` | True | True |

An empty `stored` answers True everywhere.  The concrete sources do not subclass the protocol, so
`core` looks the method up by name and falls back to "always after" (`hsu/core.py:655-668`).

### D2. Where v3.70 calls it and only warns

`SqliteWatermarkStore.advance_cursor_checked` (`hsu/watermark/sqlite.py:202-256`):

```python
with self._lock:
    forward = True
    try:
        stored = self.get_cursor(source_id, dest_id, table)
        try:
            forward = bool(is_after(cursor, stored))
        except Exception as exc:
            self._log_check_failed(key, "cursor comparator", exc, cursor); forward = True
        if not forward:
            self._log_not_forward(key, stored, cursor)
    except Exception as exc:
        self._log_check_failed(key, "forward check", exc, cursor)
    self.advance_cursor(source_id, dest_id, table, cursor=cursor, last_ack=last_ack)
    return forward
```

- `_log_not_forward` (`:287-325`) logs one WARNING per key per `BACKWARD_WARN_INTERVAL_S = 3600.0`
  (`:94`), DEBUG between, then one WARNING with a count.  Its text says "writing it anyway (v3.70
  warns only …)".
- `_log_check_failed` (`:258-285`) does the same for a read or comparator that raised.
- A write that leaves the record where it stands counts as not forward and warns too (docstring
  `:216-218`; `tests/test_advance_cursor_checked.py:104`).
- The lock is an in-process `RLock`.  The docstring says so: "Another process that opens the same
  file (an in-process sender) can still write between them; a check that only warns tolerates that"
  (`:234-237`).
- All three callers in `core` discard the return value, then commit to the source and record the
  attempt as acked (`hsu/core.py:465-475, 482-494, 546-565`).
- The unchecked `advance_cursor` stays public, in the class (`:184-200`) and in the protocol
  (`hsu/watermark/base.py:73-82`).  Only `advance_cursor_checked` and tests call it.

### D3. What "enforce" must change, read off the code

1. The write at `:253` runs whatever `forward` says.  Enforcement makes it conditional.
2. The guard exists so that "a skipped write would re-send the batch on every pump" (`:223-225`).
   Enforcement inverts that stance, so someone must decide what a failed read or a raising
   comparator does.  Today both write.
3. The read and the write share no SQLite transaction.  `get_cursor` runs outside one
   (`:175-182`); `advance_cursor` opens its own (`:193`).  Two processes on one key can interleave.
4. The callers ignore the answer.  After a refused write the core would still call
   `pipe.source.commit(...)` and log `acked`.
5. Equal values count as not forward.  A refused equal write also leaves `last_ack` unrefreshed.
6. `tests/test_advance_cursor_checked.py:459` pins the exact warning text, "v3.70 warns only"
   included; `:215` pins "every write goes ahead whatever it logs".
7. The design names `advance_cursor` as the call that "stores the new send record only when the
   source says it lies after the stored one" (design:588-589).  The code keeps that name for the
   unchecked write.

### D4. Keys that hold something other than one position

| Key | What the record holds | How the check treats it today |
|---|---|---|
| heartbeat, `files:/var/lib/sigmond/heartbeat` | the constant `b"<delete-on-ack>"` (`hsu/sources/files.py:179`) | always True; only `last_ack` changes |
| mag, `mag-datasets:/var/lib/mag-recorder/upload` | the same constant; the manifest sets `retention = "delete_on_ack"` | always True |
| GRAPE, `grape-datasets:/var/lib/timestd/upload` | one integer, `st_mtime_ns` of the newest dataset acked (`hsu/sources/files.py:181-185`) | integer compare; one position today |

The design says GRAPE's record "becomes a set of names (§6.4)" and that the rule "applies only to
send records that hold one position" (design:591-593).  That change belongs to step 4.  In v3.72 the
GRAPE record still holds one integer, and `hamsci-physics` reads that integer directly (A5).

### D5. What the v3.70 warnings have shown so far

B4's first v3.70 night, 00:34Z to 05:10Z on 2026-10-10, logged zero send-record lines in five units
(`/home/mjh/v370-t10/b4-baseline/b4-v370-night-followup-20261010T0511Z.txt`: `SEND psk-recorder…: 0`
through `SEND hs-uploader: 0`).  Section E3 shows that B4's pskreporter key had two writers on
2026-10-08.  Two writers that never overlap in time never write backward, so a silent journal does
not show that a key has one writer.

---

## E. Who shares a key today, on real stations

### E1. Each in-process sender, from its code

**psk-recorder** — builds one pipeline in its own process.

- Switch: the sender starts only when `direct` appears in the resolved delivery pipelines:
  `if "direct" not in pipelines: … return`
  (`psk-recorder/src/psk_recorder/core/recorder.py:717-725`).  `PSK_DELIVERY_PIPELINES` wins, then
  legacy `PSK_DELIVERY_MODE` (`direct` → direct, `both` → direct + server-raw), and with neither set
  the answer reads `("server-merge",)` (`:206-262`).  No runtime code reads `PSK_USE_HS_UPLOADER`;
  only the configurator and the wizard mention it.
- Store: `SqliteWatermarkStore(default_path())` (`…/core/hs_uploader_shim.py:186`), so
  `/var/lib/hs-uploader/watermarks.db` unless `HS_UPLOADER_STATE_DIR` says otherwise.  No unit, env
  template or script in these repositories sets that variable.
- Pipeline name `f"psk-recorder-{self._radiod_id}"` (`:193`).
- Source: `SqliteSource.from_env(database="psk", table="spots", accepted_schema_versions=[2], …)`
  with `extra_where=[("tx_call","!=",""), ("mode","IN",["ft8","ft4"])]`, `start_at="now"`,
  `delete_on_commit=False` (`:324-370, 415`).  It filters on no radiod and on no
  `forward_to_pskreporter`.  A comment records that the radiod filter left on purpose (`:346-352`).
  source_id: `sqlite:psk.spots`.
- Transport: `PskReporterTcp(decoding_software=…, antenna=…)`, default name (`:176-179`).  dest_id:
  `pskreporter-tcp:report.pskreporter.info:4739`.
- Rows it writes: `forward_to_pskreporter` reads True only for `server-merge` without `direct`
  (`recorder.py:553-555`).  Its writer names no producer: `Writer.from_env(table="spots",
  mode="psk", schema_version=2, …)` (`…/core/ch_tailer.py:564-567`).

**meteor-scatter** — builds one pipeline in its own process.

- Switch: `METEOR_SCATTER_DELIVERY_MODE`, default `"direct"` when unset
  (`meteor-scatter/src/meteor_scatter/core/recorder.py:586-588`).  `deposit`, `off`, `none` and
  `disabled` start no sender (`:603-611`).  `deploy.toml:22-23` seeds new instances with `"off"`
  since 83213d0; an instance created earlier keeps what it was seeded with.  No runtime code reads
  `METEOR_SCATTER_USE_HS_UPLOADER`.
- Store: `SqliteWatermarkStore(default_path())` (`…/core/hs_uploader_shim.py:194`).
- Pipeline name `f"meteor-scatter-{self._radiod_id}"` (`:201`).
- Source: `psk.spots` with `extra_where=[("tx_call","!=",""), ("mode","IN",["msk144"])]`,
  `start_at="now"`, `delete_on_commit=False` (`:355-379, 425`).  source_id: `sqlite:psk.spots`.
- Transport: `PskReporterTcp`, default name (`:184`).  The same dest_id as psk-recorder's.
- Rows it writes: `forward_flag = self._delivery_mode() == "deposit"` (`recorder.py:428`).  Writer
  call without a producer (`…/core/ch_tailer.py:482-485`).

**wspr-recorder** — builds two pipelines, in the recorder process or in `wspr-uploader.service`.

- Switch: `WSPR_USE_HS_UPLOADER` must read `1`, `true`, `yes` or `on`
  (`wspr-recorder/wspr_recorder/hs_uploader_shim.py:251-259`).  Before that, `from_env` raises unless
  a call (`WD_RECEIVER_CALL`, else `STATION_REPORTER_ID`) and `WD_RECEIVER_GRID` both exist
  (`:196-205`), and the recorder logs "env-validation skipped uploader" and carries on
  (`wspr_recorder/__main__.py:2145-2151`).  Bring-up runs `smd config upload wspr-recorder … --on`,
  which writes `WSPR_USE_HS_UPLOADER=1` (`sigmond/lib/sigmond/bringup.py:346-351`,
  `sigmond/lib/sigmond/upload.py:18-20`).  The flag alone therefore does not show a running sender;
  the missing grid keeps it off (design:1087-1092).
- `wspr-uploader.service` runs `python3 -m wspr_recorder.cli uploader` as `wsprrec`, reading
  `/etc/sigmond/coordination.env` and `/etc/wspr-recorder/uploader.env`
  (`wspr-recorder/systemd/wspr-uploader.service`).  `uploader_daemon.run()` applies the same flag and
  the same `from_env` (`wspr_recorder/uploader_daemon.py:83-97`).
- Store: `SqliteWatermarkStore(default_path())` (`hs_uploader_shim.py:290`).
- Pipeline `f"wsprdaemon-tar-{self._instance_name}"` (`:798-806`): `WsprCycleSource(db_path=…,
  expected_reporters=self._merge_reporters, backstop_sec=…, include_psk=True)` (`:739-747`), default
  `start_at="now"`; transport `WsprdaemonTarSftp(servers=[hosts of WD_SFTP_SERVERS], …,
  primary_table_name="wspr.cycle")` (`:782-797`).  Key: `sqlite:wspr.cycle`,
  `wsprdaemon-tar-sftp:<hosts in the order given>`, `wspr.cycle`.  The shim skips this pipeline when
  `WD_SFTP_SERVERS` reads empty (`:716-721`).
- Pipeline `f"wsprnet-{self._instance_name}"` (`:1034-1046`): `SqliteSource.from_env(database="wspr",
  table="spots", accepted_schema_versions=[1, 2], start_at="now", delete_on_commit=False,
  dedup_partition_by=('time','callsign','band'), dedup_order_by_desc='snr_db', ceiling…)`
  (`:1084-1134`); transport `WsprNet(...)` with the async API by default (`:980-1010`).  Key:
  `sqlite:wspr.spots`, `wsprnet-async:https://wsprnet.org/api/upload/v1`, `wspr.spots`.
- A third builder, `_build_psk_tar_pipeline` (`:813-937`), would form `sqlite:psk.spots` →
  `wsprdaemon-tar-sftp:…` on table `psk.spots`.  Nothing calls it (`:324-327`).
- The shim registers an outcome hook (`:345-348`); the daemon registers none (`hsu/daemon.py:168`).
- `WD_VERIFY_FLUSH=1` starts a thread that deletes confirmed rows from `pending_uploads` by id
  (`wspr_recorder/wsprnet_verifier.py:210, 267`).
- Writer calls without a producer (`wspr_recorder/spot_sink.py:169-171, 614-617`).

**mag-recorder** — builds no pipeline and opens no store.

- `mag-recorder-upload.service` runs at 03:00 UTC with up to 15 minutes of random delay
  (`mag-recorder/systemd/mag-recorder-upload.timer`).  Its second step reads
  `systemctl is-active --quiet hs-uploader && exit 0; exec … mag_recorder.cli upload`
  (`mag-recorder/systemd/mag-recorder-upload.service:38`).
- `upload` refuses when `[uploader] enabled` reads false (`mag-recorder/src/mag_recorder/cli.py:431-433`).
- `drain_queue` calls `PswsMagnetometerSftp.ship()` directly and unlinks the zip on ack
  (`mag-recorder/src/mag_recorder/core/uploader.py:104-160`).  It imports no `Pipeline`, no
  `Uploader` and no store.  The unit grants no write access to `/var/lib/hs-uploader`.
- It therefore writes no key.  It shares the spool directory with the daemon's `mag-psws`, and it
  ships only while the daemon sits inactive.

**hamsci-physics** — runs no sender.  Its spool trim reads the GRAPE record read-only (A5).

### E2. The daemon's pipelines on both stations (MEASURED)

`b4-pipelines.toml` and `nd-pipelines.toml` differ only in identity values.  Each renders six
pipelines, in this order: `grape-psws`, `mag-psws`, `psk-pskreporter`, `wspr-wsprdaemon`,
`wspr-wsprnet`, `heartbeat`.

| Pipeline | Source spec | Rows it selects |
|---|---|---|
| `psk-pskreporter` | sqlite `psk.spots`, versions `[2]`, `start_at = "now"`, `delete_on_commit = false`, `batch_limit = 500` | `extra_where = [["tx_call","!=",""], ["mode","IN",["ft8","ft4","msk144"]], ["forward_to_pskreporter","=",0]]` |
| `wspr-wsprdaemon` | wspr_cycle, `start_at = "now"`, `include_psk = true`, no `expected_reporters` | every `wspr.spots` and `wspr.noise` row of each closed cycle, plus every `psk.spots` row whose time falls in the cycle |
| `wspr-wsprnet` | sqlite `wspr.spots`, versions `[1, 2]`, `start_at = "now"`, `delete_on_commit = false`, dedup by `time, callsign, band` on `snr_db` | every `wspr.spots` row, best SNR per partition |

### E3. What the snapshots show about sharing (MEASURED)

Both snapshots show a single-radiod host.  B4's `coordination.env` reads `SIGMOND_RADIOD_COUNT=1`
(`/home/mjh/v370-t10/b4-pre-align/config-after/etc_sigmond_coordination.env`).  Every psk and wspr
row on B4 carries `radiod_id = "AC0G-B4-status.local"`.  B4 runs one unit per client:
`psk-recorder@AC0G=B4`, `wspr-recorder@AC0G=B4`, `meteor-scatter@AC0G=B4`.  The design describes B4
as "A host that runs several psk-recorder instances" (design:1080).  The snapshots do not show that.

The `attempts` log shows two writers on B4's pskreporter key and one on ND's:

| Measure, key `sqlite:psk.spots` → pskreporter | B4 | ND |
|---|---|---|
| attempts per minute, last 6 h | 3.26 | 1.97 |
| 30-second windows in the last 6 h that hold two or more attempts | 450 of 720 | 13 of 697 |
| attempts that carried exactly one record, last 24 h | 161 | 3 |

B4's last minutes read as two interleaved 30-second loops about five seconds apart:
`06:52:51 (79 records), 06:52:57 (43), 06:53:22 (44), 06:53:27 (41), 06:53:52 (46), 06:53:57 (37)`.
Only wspr-recorder pings the wake socket, once per two-minute cycle, so wakes cannot explain it.
Both loops ship dozens of rows, and B4's sink holds no MSK144 row, so both ship FT8 and FT4.  Only
two senders in the code select those rows under this key: the daemon's `psk-pskreporter` and
psk-recorder's `direct` shim.  I read the second loop as that shim.  The `attempts` table has no
pipeline column, so it cannot name the writer itself.

UNVERIFIED on the station: that the second loop belongs to psk-recorder, and that it still runs
after B4's v3.70 restarts on 2026-10-10.  `grep -h DELIVERY /etc/psk-recorder/env/*.env /etc/meteor-scatter/env/*.env`
on B4 and one `journalctl -u 'psk-recorder@*' | grep 'PSK pipelines active'` line would settle both.
No capture in `/home/mjh/v370-t10/` holds a per-instance env file.

ND shows one loop.  ND's store begins at 2026-10-07T14:23Z, after a fresh v3.69 install, whose
bring-up writes `--via server-raw` (`sigmond/lib/sigmond/bringup.py:339-351`).

The wspr keys show one acked attempt per two-minute cycle on both stations (B4 1,074 attempts over
1,073 cycles; ND 492 and 491 over 493).  That count cannot show a second wspr sender, because a
second sender on a shared key finds nothing left and logs no attempt.

### E4. Key → every sender that can write it

Daemon pipeline names appear in plain text; in-process senders appear with their switch.

| Key (source → dest, table) | Daemon pipeline | In-process senders that form the same key | Rows each selects |
|---|---|---|---|
| `sqlite:psk.spots` → `pskreporter-tcp:report.pskreporter.info:4739`, `psk.spots` | `psk-pskreporter` | `psk-recorder-<radiod>`, one per psk-recorder process whose pipelines include `direct`; `meteor-scatter-<radiod>`, one per meteor-scatter process whose mode reads `direct` or stands unset | daemon: ft8, ft4, msk144 with the forward flag 0; psk shim: ft8, ft4, any flag; meteor shim: msk144, any flag |
| `sqlite:wspr.spots` → `wsprnet-async:https://wsprnet.org/api/upload/v1`, `wspr.spots` | `wspr-wsprnet` | `wsprnet-<instance>`, in each wspr-recorder process with the flag, a call and `WD_RECEIVER_GRID`, and in `wspr-uploader.service` | the same rows; the shim adds a completion ceiling when `WD_MERGE_REPORTERS` is set |
| `sqlite:wspr.cycle` → `wsprdaemon-tar-sftp:gw1.wsprdaemon.org,gw2.wsprdaemon.org`, `wspr.cycle` | `wspr-wsprdaemon` | `wsprdaemon-tar-<instance>`, same conditions plus `WD_SFTP_SERVERS` listing gw1 then gw2 | the same cycles; both pull psk rows as riders |
| `grape-datasets:/var/lib/timestd/upload` → `psws-grape-sftp:…:<station>`, `grape.dataset` | `grape-psws` | none | |
| `mag-datasets:/var/lib/mag-recorder/upload` → `psws-mag-sftp:…:<station>`, `mag.daily_zip` | `mag-psws` | none; the mag fallback writes no key | |
| `files:/var/lib/sigmond/heartbeat` → `heartbeat-sftp:wd30.wsprdaemon.org`, `station.heartbeat` | `heartbeat` | none | |

Per station type:

| Station type | pskreporter key | wspr keys | Evidence |
|---|---|---|---|
| One receiver, installed at v3.69 or later (ND since 10-07) | daemon alone: bring-up writes `server-raw`, and new meteor instances seed `off` | daemon alone: no `WD_RECEIVER_GRID` | ND's single loop (E3); code |
| One receiver, installed before v3.69 (B4, image v3.66) | daemon plus psk-recorder `direct`; meteor-scatter depends on its seeded mode | daemon; a shim only if someone set `WD_RECEIVER_GRID` | B4's two loops (E3) for psk; the rest UNVERIFIED, same commands as above |
| Several receivers on one host | daemon, plus one psk shim per psk-recorder process in `direct`, each selecting every receiver's ft8 and ft4 rows | daemon, ungated, plus the merge instance's or `wspr-uploader.service`'s shim, gated on `WD_MERGE_REPORTERS` | code only; no snapshot shows such a host |

sigmond never renders `expected_reporters` (no match for that word under `sigmond/lib` or
`sigmond/bin`).  On a merge fleet the daemon's two wspr pipelines therefore run without the merge
gate that the shim applies.

---

## F. The producer column from v3.70

### F1. Where the writer stores it

- DDL: `producer TEXT NOT NULL DEFAULT ''` and `local INTEGER NOT NULL DEFAULT 0`, both last
  (`hsu/sink/writer.py:153-164`).
- An older file gains both by `ALTER TABLE … ADD COLUMN` in `ensure_columns` (`:196-229`), which the
  writer calls from `_init_schema` on its first flush (`:496-505`).  It "adds no index, runs no
  VACUUM, and updates no row" (`:205-207`).
- Each flush stores `self.producer or infer_producer(self.database, self.table, row)` and a constant
  `0` for `local` (`:441-443`).
- `Writer.from_env(..., producer: Optional[str] = None, ...)` (`:327-339`).  The argument stays
  optional.  No client passes it: psk-recorder, meteor-scatter and wspr-recorder call without it (E1).
- No caller can set `local`.  The module says "no caller sets it yet" (`:38`).
- sigmond's `hamsci_sink` hands clients this module, or a bundled copy where a venv holds no
  hs-uploader (`sigmond/lib/sigmond/hamsci_sink/__init__.py:33-41`).

### F2. The inference rule (D12)

`infer_producer(target_db, target_table, payload)` (`hsu/sink/writer.py:254-274`):

- `target_db == "psk"`: `"meteor-scatter"` when `payload["mode"]`, stripped and lower-cased, equals
  `"msk144"`; otherwise `"psk-recorder"`, a missing or non-string mode included.
- otherwise `_PRODUCER_BY_DB`: `wspr` → `wspr-recorder`, `codar` → `codar-sounder`, `superdarn` →
  `superdarn-sounder`, `hfdl` → `hfdl-recorder`, `timestd` → `hf-timestd` (`:243-249`).
- an unknown `target_db`, "including one renamed by `SIGMOND_SQLITE_DB_<MODE>`", gives `''`.

The rule exists in Python only.  No SQL form of it exists anywhere.

### F3. Does any reader filter on it yet?

No.  Outside `hsu/sink/` the word `producer` appears only in comments and docstrings of
`hsu/sources/sqlite.py`, `wspr_cycle.py`, `wake.py`, `daemon.py` and the transports.  No query names
the column.  No index covers it.  The two indexes that exist cover `(target_db, target_table, id)`
and `(target_db, target_table, json_extract(payload_json, '$.time'))` (`hsu/sink/writer.py:166-181`).

### F4. The two sink snapshots (MEASURED)

Neither snapshot has the `producer` or `local` column: both predate v3.70.  The widened copies
`b4-sink-ext.db` and `nd-sink-ext.db` have both columns, and every row reads `producer = ''`,
`local = 0`.

| Table | B4 rows | ND rows | Producer by the D12 rule |
|---|---|---|---|
| `psk.spots`, mode `ft8` | 11,826 | 8,343 | psk-recorder |
| `psk.spots`, mode `ft4` | 126 | 17 | psk-recorder |
| `psk.spots`, mode `msk144` | 0 | 0 | meteor-scatter |
| `wspr.spots` | 43,569 | 17,965 | wspr-recorder |
| `wspr.noise` | 12,274 | 8,350 | wspr-recorder |
| total | 67,795 | 34,675 | |

Other facts from the same files:

- Every psk row carries `forward_to_pskreporter` false.  B4 holds 166 psk rows with an empty
  `tx_call`, ND 73.
- `psk.spots` spans about one hour on both; the wspr tables span about 24 hours.  The trim defaults
  explain it: 60 minutes and 24 hours (`sigmond/lib/sigmond/storage_trim.py:94-99`).
- wspr rows write `time` as `2026-10-08T06:52:00Z`; psk rows write `2026-10-08T06:55:00+00:00`.
- Neither sink holds a single MSK144 row, though both stations run meteor-scatter.  Real data
  cannot exercise a meteor-scatter split.

### F5. What `SqliteSource` would need for a `producer=` filter

Design §6.1 lists six places (design:569-570).  Their lines today:

| Place | Line | What stands there |
|---|---|---|
| constructor and `from_env` | `hsu/sources/sqlite.py:183-199, 298-331` | no producer argument; the factory would pass one at `hsu/pipeline_factory.py:102-112` |
| query | `_build_query`, `:511-566` | clauses start `["id > ?", "target_db = ?", "target_table = ?"]` (`:514-518`); `extra_where` follows (`:538-553`) |
| de-duplication | `_build_dedup_query`, `:568-644` | assumes the cursor sits first: `cursor_param = params[0]`, `cte_clauses = clauses[1:]` (`:607-609`) |
| starting point | `_cursor_from_start_at`, `:705-725` | `MAX(id)` over `target_db` and `target_table` only; it ignores `extra_where` too |
| stale-schema probe | `_check_stale_schema`, `:646-671` | by `target_db` and `target_table`; it only sets `_health` and logs |
| commit | `commit`, `:374-416` | `DELETE … WHERE id <= ? AND target_db = ? AND target_table = ?` (`:403-407`), whole table; a no-op when `delete_on_commit` reads false |
| source_id | `:335-336` | fixed form |
| readiness | `_ensure_ready`, `:437-455` | checks only that `pending_uploads` exists, then caches the answer |

Facts that bear on such a filter:

- A query that names a column the file lacks raises `sqlite3.OperationalError: no such column:
  producer` out of `Uploader.pump()`; the source's health still reads `ok` (TESTED).  The daemon
  catches it once per pump and logs a traceback (`hsu/daemon.py:230-233`).  `iter_batches` guards
  only `_ensure_ready()` (`:347-355`); the query runs later, inside the generator.
- The column appears only after some v3.70 writer has flushed.  A recorder that nobody restarted
  since v3.69 still runs the old writer and adds nothing.  `smd update` restarts no recorder (C4).
- The source opens `sink.db` read-write (`:160`), as `hsupload`, with `/var/lib/sigmond` writable in
  the unit.  It could call `ensure_columns` itself; nothing does today.
- Rows from a not-yet-restarted recorder land with `producer = ''` even on a widened file.  A filter
  must apply the D12 rule to them in SQL.  `json_extract(...) != 'msk144'` yields NULL for a row
  with no mode and drops it, where the Python rule says psk-recorder.
- The Python rule strips and lower-cases the mode.  The existing `extra_where` compares
  case-sensitively (`mode IN ('ft8','ft4','msk144')`).
- All three sink pipelines render `delete_on_commit = false` today.  The factory default stays True
  (`hsu/pipeline_factory.py:109`).

### F6. What `WsprCycleSource` would need

Design §6.1: "WsprCycleSource gains `producer` and `tables`, and applies them to cycle discovery,
the per-cycle fetch and the ceiling" (design:571-572).

| Place | Line | What stands there |
|---|---|---|
| constructor | `hsu/sources/wspr_cycle.py:74-83` | `db_path, ship_buffer_sec, start_at, expected_reporters, backstop_sec, include_psk` |
| factory | `hsu/pipeline_factory.py:113-127` | passes those six |
| cycle discovery | `:170-179` | `target_db = 'wspr' AND target_table IN ('spots', 'noise')`, hard-coded |
| per-cycle fetch | `:310-318` | the same two tables, hard-coded |
| psk riders | `:352-367` | `target_db = 'psk' AND target_table = 'spots'`, every mode, both producers |
| ceiling, gated | `hsu/sources/wspr_completion.py:102-181` (query `:136-141`), `cycle_complete` `:77-100`, `_present_reporters` `:203-208` | hard-coded `wspr` tables |
| ceiling, ungated | `_cycle_floor_iso`, `:290-299` | clock only |
| starting point | `_effective_cursor`, `:223-256` | `"beginning"` gives `""`; `"now"` anchors one cycle before the current floor |
| source_id | `:57, 126-127` | fixed |
| commit | `:188-193` | no-op |
| readiness | `_ensure_ready`, `:263-288` | table exists; not cached |

---

## G. The daemon and D13

### G1. How `hs-uploader serve` builds pipelines

- `daemon.run` loads the manifest once (`hsu/daemon.py:150-157`), opens one store at
  `default_path()` (`:159`), builds every pipeline (`:160`), and logs their names (`:165-166`).
- It handles SIGINT and SIGTERM only (`:214-215`).  It never re-reads the manifest.
- `build_all_pipelines` (`:103-141`) first calls the factory, then resolves any
  `builder = "module:function"` entry.  No manifest uses a builder today.
- `build_pipelines` (`hsu/pipeline_factory.py:250-284`) reads, per entry: `name`, `source`,
  `transport`, `identity`, `retry`, `batch_limit`, `max_records_per_pump`, and `discard` through
  `wrap_if_discard` (`:269`).  It ignores any other key in an entry.
- `_build_source` and `_build_transport` read only the keys they name (`:80-128, 134-238`).  An
  unknown key in a source or transport table passes without a word.
- The daemon checks nothing across pipelines.  Design §3.5 wants it to refuse to start when two
  built pipelines share one key (design:347-348).  No such check exists.
- The pump loop runs every 30 seconds or on a wake (`hsu/daemon.py:225-233`).  `Uploader.pump` runs
  every pipeline at once, each on its own worker thread (`hsu/core.py:308-324`).

### G2. Where a "skip this producer's rows" rule could sit

| Place | Line | What sitting there means |
|---|---|---|
| sigmond's renderer leaves the pipeline out, or renders a filter | `sigmond/lib/sigmond/uploader_manifest.py:204-247` | decided when the manifest renders; the daemon learns of a change only at its next restart |
| a new key in the source spec | `hsu/pipeline_factory.py:97-127` | decided when the daemon starts |
| a clause in the source's query | `hsu/sources/sqlite.py:514-553`; `hsu/sources/wspr_cycle.py:170-179, 310-318, 360-367` | decided per pump if the source re-reads its condition |
| a gate at the top of `_pump_one` | `hsu/core.py:326-336` | the design's `may_send(pipe)` sits here in step 2b (design:596-599); a refused pipeline "touches neither its send record nor its queued deliveries" |

The rider query at `wspr_cycle.py:360-367` matters for any reading of "skip that producer's rows":
it pulls both producers' psk rows into wspr-recorder's tar.

### G3. How the daemon could know that a client's own sender still runs

Nothing in hs-uploader reads another unit's state, another client's env file, or any marker.  No
file or setting today says "this client's in-process sender runs".  The candidates:

| Candidate | Where | What it lacks |
|---|---|---|
| Per-instance env files: `PSK_DELIVERY_PIPELINES` / `PSK_DELIVERY_MODE` in `/etc/psk-recorder/env/<id>.env`; `METEOR_SCATTER_DELIVERY_MODE` in `/etc/meteor-scatter/env/<id>.env`; `WSPR_USE_HS_UPLOADER`, `WD_RECEIVER_CALL`, `WD_RECEIVER_GRID`, `WD_SFTP_SERVERS` in `/etc/wspr-recorder/env/<id>.env` and `/etc/wspr-recorder/uploader.env`; all merged over `/etc/sigmond/coordination.env` | units' `EnvironmentFile=` lines; sigmond writes them at mode 0644 (`sigmond/lib/sigmond/upload.py:86-94`) | They state intent, not the running process: an edited file takes effect only at the recorder's next restart.  A reader must repeat each client's rule: psk's precedence and default; meteor's default of `direct` when unset; wspr's four conditions.  The directories' modes on stations stay UNVERIFIED (`ls -ld /etc/psk-recorder/env`). |
| sigmond's own reading of those files | `sigmond/lib/sigmond/harmonize.py:1180-1194` (`_resolve_instance_env`), `:1197-…` (`_active_instances`), used by `rule_upload_enabled` (`:1229-1258`) | It reads only the `*_USE_HS_UPLOADER` flags of `upload.UPLOAD_ENABLE`.  psk-recorder and meteor-scatter ignore their flags at runtime.  sigmond reads `METEOR_SCATTER_DELIVERY_MODE` and `WD_RECEIVER_GRID` nowhere. |
| `systemctl is-active wspr-uploader.service` | precedent: the mag unit asks `is-active hs-uploader` (`mag-recorder/systemd/mag-recorder-upload.service:38`); `sigmond/bin/smd:17650` asks about `wspr-uploader.service` | It covers one of the four senders.  An active unit can still run no sender (flag off, grid missing). |
| `deliverables.pipeline` names in `watermarks.db` | in-process names differ from the daemon's: `psk-recorder-<radiod>`, `meteor-scatter-<radiod>`, `wsprnet-<instance>`, `wsprdaemon-tar-<instance>` | A name appears only while a retry waits. |
| `attempts` in `watermarks.db` | `hsu/watermark/sqlite.py:44-54` | No pipeline column; both senders write the same key.  Cadence shows a second writer (E3) and cannot name it. |
| The recorder's journal: `psk-uploader-hs started`, `ms-uploader-hs started`, `wspr-uploader-hs started`, `PSK pipelines active: …` | psk shim `:217-221`; meteor shim (same shape); wspr shim `:419-422`; `psk recorder.py:556-559` | A log line, readable by root or the journal group, not by `hsupload` without a grant. |
| The wake socket `/var/lib/sigmond/upload-wake.sock` | `hsu/wake.py`; the wspr shim binds the same path | `WakeListener.start` unlinks whatever stands there and binds fresh.  The path names no owner. |
| Client `inventory --json` | `psk-recorder/src/psk_recorder/contract.py:74-104` | It reports `data_sinks` and no sender state. |
| The per-minute state files recorders write (`timing-authority.json`) | `hamsci-dsp/src/hamsci_dsp/timing.py:500` (design:1288) | Timing only; no sender field. |

The design's own later mechanism, the switch files of §3.4, does not exist in code.

---

## H. sigmond's renderer

### H1. The code

`smd admin uploader manifest` → `cmd_uploader_manifest`
(`sigmond/lib/sigmond/commands/uploader.py:191-311`) → `um.generate()`
(`sigmond/lib/sigmond/uploader_manifest.py:448-486`).

- `collect_pipelines` (`:204-247`) walks `topology.enabled_components()`, which returns sorted
  names (`sigmond/lib/sigmond/topology.py:74-78`).  For each client it reads the
  `[[hs_uploader.pipeline]]` blocks of that client's `deploy.toml` (`:191-201`), substitutes
  `{placeholder}` tokens (`:162-188`), and emits the block as it stands.
- The renderer adds nothing to a block except, in discard mode, `discard = true` (`:466-467`).
- A block that uses an unresolved token gets skipped with a warning (`:229-234`).
- De-duplication by name: "two clients may declare the SAME shared pipeline … Emit it once; warn if
  the resolved bodies actually differ (keep the first)" (`:235-246`).
- The heartbeat comes from `[heartbeat]` in coordination.toml, not from a client (`:250-297`), and
  `generate` appends it last (`:482-484`).
- `render_manifest` writes `[identity]`, `[daemon] pump_interval_sec = 30`, then each
  `[[pipeline]]` (`:345-367`).

### H2. Which pipelines, under which conditions

| Pipeline | Declared in | Tokens it needs | Renders when |
|---|---|---|---|
| `grape-psws` | `hamsci-physics/deploy.toml:163-182` | `{station_id}`, `{instrument_id}` | hamsci-physics enabled and both PSWS ids resolve (`uploader_manifest.py:141-147`) |
| `mag-psws` | `mag-recorder/deploy.toml:159-186` | `{station_id}`, `{instrument_id}` | mag-recorder enabled and both ids resolve |
| `psk-pskreporter` | `psk-recorder/deploy.toml:117-139` and, word for word, `meteor-scatter/deploy.toml:131-153` | `{radiod_status}` | either client enabled and coordination names a radiod |
| `wspr-wsprdaemon` | `wspr-recorder/deploy.toml:154-181` | `{sink_path}`, `{call_pathsafe}` | wspr-recorder enabled and a reporter call resolves (`:89-105`) |
| `wspr-wsprnet` | `wspr-recorder/deploy.toml:184-199` | none | wspr-recorder enabled |
| `heartbeat` | `uploader_manifest.py:275-297` | `[heartbeat]` station and host | `[heartbeat] enabled`, whatever the uploads policy says |

Site policy, from `[uploads] mode` (`:462-481`): `upload` renders every client pipeline; `discard`
renders each with `discard = true`, provided the daemon's own interpreter can import
`hs_uploader.transports.discard` (`:370-397`); `hold` renders the heartbeat alone.

### H3. Names, client keys, `start_at`, `extra_where`

- Names come verbatim from each block's `name`; the fallback reads `f"{client}-pipeline"` (`:228`).
- No rendered entry carries a `client` key or anything like one.  `collect_pipelines` knows the
  declaring client only as its loop variable (`:216`).  Design §3.5 expects sigmond to render
  `client = "<name>"` into each legacy entry (design:342-343); nothing does.
- With both psk clients enabled, the sorted walk reaches meteor-scatter first, so meteor-scatter's
  copy of `psk-pskreporter` wins the de-duplication.  The two copies match, down to
  `decoding_software = "psk-recorder/0.1 (radiod={radiod_status})"` (`meteor-scatter/deploy.toml:150`).
- `start_at = "now"` comes verbatim from the blocks: `psk-recorder/deploy.toml:125`,
  `meteor-scatter/deploy.toml:139`, `wspr-recorder/deploy.toml:165, 192`.  File-tree blocks set none.
- `extra_where` comes verbatim: `psk-recorder/deploy.toml:133` and `meteor-scatter/deploy.toml:147`.
- The renderer never emits `expected_reporters`, `upload_id`, `sftp_user`, a pskreporter `name`, or
  a per-pipeline identity for the psk and wspr pipelines.  All of them fall to the base
  `[identity]`, whose `call` reads the first wspr reporter in sorted order (`:96-105`).

### H4. Where a split of `psk-pskreporter` would be rendered

The code offers two places, and the design names neither:

1. In the two clients' `deploy.toml` blocks.  Each client would declare its own name and filter, and
   the renderer would emit both unchanged.  This changes two client repositories.  Stations hold
   client checkouts at a pin (C4), so the two clients can move at different times.
2. In `collect_pipelines`, between substitution and de-duplication
   (`sigmond/lib/sigmond/uploader_manifest.py:224-246`).  sigmond would rewrite the shared block
   into two.  This changes sigmond alone.

`tests/test_uploader_manifest.py:222-235` pins today's de-duplication of the shared name.

sigmond already owns one capability probe of the daemon's venv: `hs_uploader_supports_discard`
(`:370-384`), which runs the service's interpreter and "treats any doubt as 'no'".

---

## I. Tests and proof tools that exist

### I1. hs-uploader tests (`hs-uploader/tests/`)

| File | Tests | What it pins |
|---|---|---|
| `test_watermark_sqlite.py` | 12 | cursor round trip, upsert, per-destination rows, `reset_cursor`, attempts ring, deliverable pop and requeue, dead letters, group-write mode |
| `test_watermark_schema.py` | 31 | migration list has no gaps and `SCHEMA_VERSION == 1` (`:150-153`); a v0 store keeps every row; second run changes nothing; `--check` writes nothing; crash copies; lock waits and timeouts; rollback of a failing migration; missing file; not-a-store; newer store left alone (`:410`); `--db` beats `--state`; the runner applies only unseen versions, using a stand-in migration 2 (`:527-542`); the store constructor leaves `user_version` alone (`:640`); the store at head opens a version-1 file (`:660`) |
| `test_cursor_is_after.py` | 17 | each source's order, equal values, empty stored, undecodable values, legacy float records, text order for cycles |
| `test_advance_cursor_checked.py` | 21 | forward quiet; backward and equal warn and still write; comparator and logging failures never block; one WARNING per key per hour; exact texts (`:459`); core's three call sites use the checked call (`:273-305`); "every write goes ahead whatever it logs" (`:215`) |
| `test_requeue_keeps_key.py` | 3 | a twice-failed retry advances to its own cursor; requeue changes two fields only; a legacy keyless row survives and advances nothing (`:128`) |
| `test_core_orchestration.py` | 11 | drain, retry, replay ack, exhaustion, partial ack, permanent failure, outcome callback |
| `test_link_loss_hot_loop.py` | 7 | one probe per pump while a destination fails; enqueue de-dup on `(pipeline, cursor_after)` (`:82`) |
| `test_sqlite_source.py` | 29 | id order, cursor skip, limit, table scoping, `extra_where`, schema versions, stale schema, commit delete and its switch, `start_at="now"`, dedup |
| `test_wspr_cycle_source.py` | 4 | empty and missing databases only |
| `test_wspr_completion.py` | 8 | reporter gate and ceiling |
| `test_file_source.py` | 13 | both retentions, KEEP cursor precision, legacy float cursor |
| `test_pipeline_factory.py` | 16 | shapes of each pipeline; one shared store; `test_wspr_slot_keys_match_in_process_for_cursor_inheritance` (`:230-252`) pins the daemon's wspr keys to the in-process literals |
| `test_daemon.py` | 4 | builder resolution, dry run, missing manifest |
| `test_cli.py` | 6 | status, peek, reset-cursor, kick |
| `test_discard_transport.py` | 5 | discard advances the real key |
| `test_sink_writer.py` | 47 | writer behaviour; `ensure_columns` races; `infer_producer` (`:867`); a caller's producer wins (`:898`) |
| `test_backlog.py` | 11 | read-only summary, never mutates |
| `test_neutrality_check.py` | 1,040 lines | the tool below |

Gaps that matter here: `WsprCycleSource` has no test of cycle discovery, riders or `start_at`; no
test builds two pipelines on one key; no test covers a store at one version under code at another
beyond "opens and carries on".

### I2. `hs-uploader/tools/neutrality_check.py`

- It takes one manifest, one `sink.db`, one `watermarks.db`, and two source trees, `--old` and
  `--new` (`:591-607`).  It copies both databases per tree into a temporary directory and fails if
  either given file changes (`:402-433`).
- In each tree it builds every pipeline with that tree's own `build_all_pipelines`
  (`:279-350`), replaces each transport with a recorder that borrows the real name and table
  (`:109-135`), hides queued retries from the pump while counting them (`:322-329`), pumps once, and
  captures the first batch before any send or cursor write (`:372-378`).
- Per pipeline it reports the key the core read, the stored cursor, the count of queued retries, the
  batch limits, and the first batch: record count, first and last row id, next cursor, and a digest
  (`:364-388`).
- It matches OLD and NEW by pipeline name and calls them equal only when `key`, `stored`, `queued`,
  `limits`, `batch` and `error` all match (`:88, 529-532`).
- `--now` freezes the clock in the cycle and sqlite sources (`:90-98, 244-262`).

How v3.70 used it (design §13.7, design:1312-1342): OLD 3e97223 against NEW 8376fd8 on the 06:55Z
copies; "six of six pipelines agree" as copied; again after two rewinds per station; again on sinks
widened by `ensure_columns`; again on a store migrated to version 1.  The outputs sit beside the
copies (`b4-run-r1.out` and its siblings).  The design states what the tool does not cover: the
write side, the three file-tree pipelines, and queued retries, since neither store held one.

For a key split the tool's premises change.  It compares one manifest under two trees, and it calls
a key difference a disagreement.  A split changes the manifest, the pipeline names and the keys on
purpose.

### I3. sigmond tests (`sigmond/tests/`)

| File | Tests | What it pins |
|---|---|---|
| `test_uploader_manifest.py` | 26 | serializer; substitution and unknown tokens; skips; `test_dedup_shared_pipeline_by_name` (`:222`); heartbeat shape; hold and discard rendering; `test_supports_discard_asks_the_service_interpreter` (`:494`); the rendered discard manifest builds in hs-uploader (`:479`) |
| `test_uploader_cmd_policy.py` | 41 | migrate runs after the write and before systemctl (`:190`); a failed migrate starts and restarts nothing (`:197`); `--write` alone migrates (`:205`); migrate runs on a current manifest (`:216`); a rerun restarts a stale daemon (`:241`); `_run_migrate`'s skip, failure and timeout cases (`:279-464`); start-time staleness (`:472-530`) |
| `test_manifest_restore.py` | 45 | apply re-renders with the restored smd after the checkouts (`:674`); failure and hang cases; a real-child test (`:838`) |
| `test_align_cli.py` | — | `test_the_manifest_step_outlasts_the_migrate_timeout` (`:1455`) |
| `test_upload.py` | 14 | `smd config upload` env writes, `--via` |
| `test_sink_cli.py` | 20 | `smd sink` verbs |
| `test_hamsci_sink_compat.py` | — | the bundled writer copy matches hs-uploader's |

### I4. The appliance's update rig (`sigmond-appliance/test-update-v3.sh`, grep only)

- Header: `:22-25` (the manifest step runs migrate first, then a restart), `:33-36` (PHASE G and
  G-bis), `:88-91` (contract strings `watermarks.db: version <N>`, `  pending <name>`,
  `invalid choice: 'migrate'`), `:105` (`pipelines.toml re-rendered by the restored sigmond`).
- PHASE E-bis, `:1017-1204`: reads the version before the step with `migrate --check --db` as
  `hsupload` (`:1042-1058`); requires a store to exist (`:1098`); runs
  `smd admin uploader manifest --write --enable` (`:1102`); fails on a non-zero exit (`:1118`);
  requires the version line, nothing pending, version at least 1, and `PRAGMA user_version` equal to
  the reported version (`:1145-1150`); fails when the store reads "newer than the updated
  hs-uploader knows" (`:1146`); orders the restart line after migrate's report (`:1166-1177`); and
  requires the daemon's start time to follow the step (`:1201`).
- PHASE G, `:1393-1542`: restore to the blessed baseline.
- PHASE G-bis, `:1545-1615`: the restore printed its re-render line (`:1563`); the restored smd
  reads the manifest as current (`:1569`); and "watermarks.db reads version … under the restored
  daemon, but E-bis left it at …: the rollback changed the store's version" fails the run (`:1612`).

The rig checks versions and ordering.  It reads no key, no row and no cursor.

---

## J. Traps

Each entry gives what the code does, what the design says, and what follows.

### J1. The copy goes stale between `migrate` and the daemon's restart

- Code: the manifest step migrates while the old daemon still pumps (C3 steps 2 to 4;
  `commands/uploader.py:290, 299-310`).  With `--write` alone, `smd align` restarts the daemon in a
  later stage (`sigmond/bin/smd:5442-5444`).  The old daemon keeps advancing the old keys throughout.
  In-process senders in other processes do the same until each recorder restarts.
- Design: D10 places the migration "after the write and before the daemon starts or restarts"
  (design:97); §6.2 says the copy happens "before the first pump" (design:609-610); D9 calls the old
  row "frozen" (design:96).  Neither says to stop the old daemon first.
- Consequence: the new daemon starts from a copy taken seconds or minutes earlier and sends again
  whatever the old daemon acked in between.  B4 acks about 90 psk rows per 30-second pump.

### J2. A failed migrate leaves the new manifest on disk

- Code: the step writes `pipelines.toml` (`commands/uploader.py:272`) before it migrates (`:290`).
  On failure it returns 1 and restarts nothing.  The unit sets `Restart=always`
  (`hs-uploader/systemd/hs-uploader.service`), bring-up restarts unconditionally
  (`bringup.py:517-520`), `smd apply` ignores the step's result (`bin/smd:10424`), and a reboot
  restarts everything.  Any of those starts the daemon on the new manifest and an unmigrated store.
- Code: with an empty key and `start_at = "now"`, `SqliteSource` jumps to `MAX(id)` of the whole
  table (`hsu/sources/sqlite.py:365-366, 705-725`) and `WsprCycleSource` anchors one cycle back
  (`hsu/sources/wspr_cycle.py:237-256`).
- Design: "A failed migrate leaves the daemon as it stands" and "a store still on an older version
  keeps its older keys" (design:97, and `commands/uploader.py:17-21`).
- Consequence: a new key that starts at "now" skips every row between the old record and that
  moment.  After an outage that span holds the whole backlog.  The v3.70 carry list already notes
  the unconditional restart as "Harmless while migration 1 changes nothing"
  (`plan-sink-control-v370.md:172-173`).

### J3. A new manifest under an old hs-uploader

- Code: the old factory ignores keys it does not know (G1).  It would build two split pipelines
  whose sources both form `sqlite:psk.spots`.
- TESTED: two pipelines in one `Uploader`, one key, the same rows: each shipped all ten rows.  Two
  pipelines on one key split by mode through `extra_where`: the ft8 pipeline shipped its eight rows
  and moved the record to 10; the msk144 pipeline then shipped nothing, and its two rows never went.
- Code: `_run_migrate` returns 0 when the binary predates `migrate` and when no binary exists
  (`commands/uploader.py:159-161, 177-180`).  An update that holds hs-uploader at its pin, or whose
  hs-uploader pull fails, while sigmond moves, leaves exactly this pairing.
- Design: D11 covers one path only: `smd admin manifest restore` re-renders with the restored
  sigmond (design:98).  A hand checkout that moves hs-uploader back alone re-renders nothing.  Where
  sigmond alone moved forward, the next manifest step renders the new manifest for the old daemon.
- Consequence: double posts or lost rows, depending on how the split filters.  `hs_uploader_supports_discard`
  (`uploader_manifest.py:370-384`) shows the existing way to ask the daemon's venv what it can do.

### J4. "Skip that producer's rows" reaches further than one destination

- Code: psk-recorder's `direct` sender covers pskreporter only.  The same rows still ride
  wspr-recorder's tar to wd30 (`hsu/sources/wspr_cycle.py:352-367`, `include_psk = true` in both
  manifests).  wspr-recorder's shim can run one of its two pipelines without the other
  (`hs_uploader_shim.py:716-721`).
- Design: D13 reads "the daemon's pipelines skip that producer's rows" (design:100).
- Consequence: read word for word, D13 would drop psk-recorder's rows from the wsprdaemon tar
  whenever its pskreporter sender runs.  The rule needs a destination beside the producer.

### J5. No signal says a sender runs, and a render-time answer ages

- Code: G3 lists every candidate and what each lacks.  The daemon reads its manifest once
  (`hsu/daemon.py:150-151`).  `smd config upload <client> <id> --on|--off|--via` edits the env file
  and prints "restart to apply"; it re-renders nothing and restarts nothing
  (`sigmond/lib/sigmond/commands/config.py:1315-1382`).
- Design: D13 states the rule and names no source for the fact (design:100).
- Consequence: an implementer must invent the signal.  If the renderer decides, the answer holds
  until someone edits an env file and restarts a recorder without re-rendering.

### J6. Nothing hands the position over when an in-process sender stops

- Code: while psk-recorder's `direct` sender runs, it advances whatever key its shim forms.  A
  daemon pipeline that sat idle for that producer holds the cursor the migration copied.
- Design: §6.2 mentions "no predecessor key" once (design:606) and defines no predecessor lookup.
- Consequence: when the operator moves the client to `server-raw`, the daemon's pipeline starts from
  the migration-day cursor and sends again everything the trim still holds: up to 60 minutes of psk
  rows, up to 24 hours of wspr rows.

### J7. In-process senders keep the old key unless four client repositories change

- Code: every shim calls `SqliteSource.from_env` or `WsprCycleSource` with no producer (E1).  If
  `source_id()` keeps its present form for such callers, the shims stay on the old keys, and the old
  rows keep moving.  If the default form changes, every shim jumps to an empty key at its recorder's
  next restart and meets `start_at = "now"`.
- Code: on a host with several psk-recorder processes, each `direct` shim selects every receiver's
  rows and forms the same key (E1).  A producer in the key does not separate them.
- Design: step 2a-ii gives "each sender … its own send-record key" (design:969-971); in-process
  senders "keep running until step 4" (design:985); v3.70 forbade client changes
  (`plan-sink-control-v370.md:30`).  The design does not say whether v3.72 lifts that.
- Consequence: the word "frozen" in D9 holds only where no sender still writes the old key.

### J8. Queued retries

- TESTED (B5): a retry under a retired name never ships and never leaves; a retry on an old key
  under a surviving name ships, rewrites the old row, and its rows go out a second time.
- Code: a `psk-pskreporter` retry holds FT8, FT4 and MSK144 spots in one payload, with the receiver
  identity in front (`hsu/transports/pskreporter.py:153-177`).  The store cannot split it by
  producer.  Re-homed whole to one of two new pipelines, it advances one new key; the other
  pipeline then sends that range's rows of its own again.
- Code: keyless rows from before v3.70 carry no key to map (B3).
- Design: §6.2 asks only that the migration "re-homes the queued retries" (design:610).
- Consequence: the design leaves the target of a mixed retry open.  Both snapshots hold zero queued
  retries, so only made-up rows can test whatever gets decided.

### J9. A rollback re-sends up to the retention, and a second update re-sends again

- Code: a rolled-back daemon forms the old keys and resumes from the old rows.  Those rows stopped
  at migration time unless something kept writing them (J7).  The trim bounds what remains: 60
  minutes of psk rows, 24 hours of wspr rows (`sigmond/lib/sigmond/storage_trim.py:94-99`).  A KEEP
  spool has no such bound short of its own 14-day trim
  (`hamsci-physics/src/hamsci_physics/grape/spool.py:50`).
- Code: the version stamp stops the copy from running twice (`hsu/watermark/schema.py:181-183`).
  After rollback and a second update, the new keys still hold what v3.72 last wrote before the
  rollback, behind the old keys.
- Design: D9 accepts "a rollback can re-send, never skip" (design:96) and gives no size.  It says
  nothing about the second update.
- Consequence: a key that does not need to change costs nothing if the migration leaves it alone.
  The GRAPE, mag and heartbeat keys hold no producer problem today.

### J10. Other readers match keys by string

- Code: `hamsci-physics` reads GRAPE's record by `source_id` and `table_name`, takes the lowest
  cursor among matches, and deletes spool packages older than it (A5).  A second row under the same
  source and table, frozen, would hold that minimum still and stop the trim.  A changed source id
  would return no row and stop it too.  Both fail safe; the spool grows about 40 MB a day
  (`spool.py:8`).
- Code: `hs_uploader.backlog` ships every `watermarks` row to the heartbeat (A5).  Old rows kept
  under D9 travel to the fleetboard with a `last_ack` that never moves again.
- UNVERIFIED: whether wd30's fleetboard alarms on an old `last_ack`.  Its code lives on wd30.
- Code: `hs-uploader status` lists every row (`hsu/cli.py:137-151`).  `hs-uploader reset-cursor`
  takes the three strings from the operator, who must now know the new forms.

### J11. A producer filter meets sinks that lack the column, rows that lack the value, and a start rule that ignores both

- TESTED: a missing column raises out of `pump()` on every pump (F5).
- Code: rows from a recorder nobody restarted read `''`; the D12 rule exists only in Python; its SQL
  twin must treat a missing mode and mixed case as the Python does (F2, F5).
- Code: `_cursor_from_start_at` takes `MAX(id)` over the whole table (`hsu/sources/sqlite.py:719-724`).
- Code: no index covers `producer` (F3).
- Design: §6.1 says the writer adds "an index on (target_db, target_table, producer, id)"
  (design:544); §10.4 says "v3.70 adds no index" (design:1134).  Nobody has said which release adds
  it, or which process pays for building it on a live file.
- Design: the carry list says "Readers that filter on `producer` (v3.71) must handle a `sink.db`
  that does not have the column yet" (`plan-sink-control-v370.md:74`).

### J12. Enforcement of forward-only

- Code and design disagree on the name: the design's `advance_cursor` enforces (design:588-589); the
  code's `advance_cursor` writes unchecked and stays public (D2).
- Code: the read and the write share no transaction, so a second process can slip between them (D3).
- Code: the callers ignore the answer and commit to the source regardless (D2).
- Code: equal counts as not forward; a refused equal write stops refreshing `last_ack` (D3).
- Code: a comparator that raises counts as forward today (D2).  Under enforcement someone must
  choose between writing blind and refusing.
- Design: "v3.71 enforces the rule once each sender owns its key; v3.70's warnings measure how
  often a shared key runs backward" (design:1144-1146).  MEASURED: B4 logged no warning in its first
  night although its pskreporter key showed two writers two days earlier (D5, E3).  The warnings
  count overlaps in time, not shared keys.
- Design: the rule "applies only to send records that hold one position" (design:592-593).  In
  v3.72 every key still holds one position or the delete-on-ack constant (D4).

### J13. Per-instance pipelines need more than a producer in the key

- Design: §3.5 wants wsprnet to run "as one pipeline per instance, each filtered to that instance's
  rows" and wants the daemon to refuse two pipelines on one key (design:330-332, 347-348).
- Code: two such pipelines would both form `sqlite:wspr.spots@wspr-recorder` with the same
  destination name.  No code builds per-instance pipelines, and no code refuses a shared key.
- Consequence: the design gives the key form for a producer (`sqlite:psk.spots@psk-recorder`,
  design:579) and none for an instance.  It does not say whether v3.72 must leave room for one.

### J14. Where the split lives decides how two clients can drift apart

- Code: H4 gives the two places.  If each client declares its own pipeline, a station whose
  psk-recorder checkout moved while meteor-scatter's stayed pinned renders one new pipeline for
  FT8 and FT4 beside the old shared one for all three modes.  They overlap on FT8 and FT4, on
  different keys, toward one destination.
- Design: "no two pipelines whose filters overlap may share a destination" (design:581-582); it
  does not say which repository renders the split.
- Code: a host with meteor-scatter alone, or psk-recorder alone, renders the shared pipeline today
  from the one client present.  Any split must keep both single-client hosts whole.

### J15. The design's line references predate v3.70

`core.py:494-506, 544-551` (design:611) now read `496-508, 546-554`.  `core.py:585-594` (design:102,
design:1151) now reads `587-598`.  `watermark/sqlite.py:34-40` (design:578) now reads `34-42`, and
`:164-180` (design:1245) now reads `184-200`.  `sigmond/lib/sigmond/hamsci_sink/writer.py`
(design:154, design:1258) no longer exists; the writer lives at `hsu/sink/writer.py`.

### J16. The two pskreporter senders post under different identities

- Code: `PskReporterTcp` posts `callsign=identity.call` (`hsu/transports/pskreporter.py:251`).  The
  daemon's identity comes from the manifest: `call = "AC0G/B4"` in `b4-pipelines.toml`, the wspr
  reporter id (`uploader_manifest.py:89-105`).  psk-recorder's shim builds its identity from its own
  `[station] callsign` (`hs_uploader_shim.py:187-191`; `recorder.py:726`).
- UNVERIFIED: the callsign in B4's psk-recorder config.  `coordination.env` there reads
  `STATION_CALL=AC0G` and `STATION_PSKREPORTER_CALL=AC0G`.  `psk-recorder config show` on B4 would
  settle it.
- Consequence: where two senders share the key, pskreporter may see one station's spots under two
  receiver callsigns.  Whichever sender D13 leaves in charge also decides the callsign.

### J17. Items the v3.70 plan already carried, still open in code

From `plan-sink-control-v370.md:64-184`, each confirmed against the code above:

- `_run_migrate` passes no `--db`, so it migrates the default path whatever the daemon's
  environment says (`commands/uploader.py:163`).
- On a fresh host the store does not exist when bring-up migrates; it then sits at version 0.
- `smd update --apply` renders nothing and restarts nothing (C4).
- A zero-length `watermarks.db` fails `_inspect` and stops every manifest step.
- A timed-out migrate can finish after sigmond reported failure (`commands/uploader.py:165-172`
  kills `runuser`, not the process group).
- A daemon restart between about 02:55 and 03:10 UTC lets `mag-recorder-upload.service` ship that
  day's zip itself (E1).

### J18. What the snapshots cannot show

- No host with several receivers (E3).
- No MSK144 row (F4).
- No queued retry and, on B4, no dead letter (A7).
- No `producer` value other than `''`, and that only in the widened copies (F4).
- No per-instance env file (E3).

### J19. Noticed in passing, outside this step

ND's psk rows carry `host_grid = "EM38ww"` while ND's manifest reads `grid = "EN16ov"`
(`nd-sink.db`; `nd-pipelines.toml`).  Design §6.6 builds the tar's `<RX_SITE>` from each row's
`host_grid` (design:756-758).

---

## Questions only a person can answer

1. Does B4 still count as a host with several receivers?  The snapshots show one radiod and one
   instance per client (E3), and the design's caution about B4 rests on several (design:1080).
2. May v3.72 change client repositories?  The split's home (J14) and the in-process senders' keys
   (J7) both turn on it.
3. What should carry D13's fact that a sender runs (J5), and does the skip apply per destination
   (J4)?
4. May the manifest step stop the daemon before it migrates (J1), and may the daemon read the
   store's version at start and refuse or fall back (J2)?
5. Where does a queued retry that mixes two producers go (J8)?
6. Does the forward-only rule refuse an equal write, and what does a failed comparison do (J12)?

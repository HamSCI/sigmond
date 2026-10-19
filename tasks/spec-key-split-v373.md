# spec: the send-record key split (sink control step 2a-ii), v3.73

**Status:** draft for Michael's review, 2026-10-19; revised the same day after an adversarial
review (1 blocker, 9 should-fix, 9 notes, all taken; §11 lists where each landed).  Implements
`release-v373.md` row 1 under `plan-sink-control.md` D9, D10, D13, D16 and D17.  Evidence:
`research-key-split-v373.md` (the map, 10-10) and `research-key-split-answers-v373.md` (the six
answers, 10-19).  Step 2b follows in v3.74; §9 lists what it inherits.

## 1. What changes, in one paragraph

Today psk-recorder's FT8 and FT4 spots and meteor-scatter's MSK144 spots leave the station through
one hs-uploader pipeline, `psk-pskreporter`, with one send record.  v3.73 splits it into
`psk-recorder-pskreporter` and `meteor-scatter-pskreporter`, each with its own send record.  A
one-time migration copies the old record to both new ones while the daemon stands stopped.  From
then on the store refuses to move any send record backward.  Every other pipeline, key and send
record stays as it is.

## 2. The fleet this ships to

Read on 10-19 at B4, ND, K3LR, UACNJ and W2NAF:

- one radiod and one instance of each client per station;
- psk-recorder on `server-raw` and meteor-scatter on `off`, so no client runs its own pskreporter
  sender, and the daemon sends every key alone;
- wspr-recorder's own sender off, for want of `WD_RECEIVER_GRID`.

AI6VN and DASI-002 refused the connection.  The spec keeps one guard (§5.3) for a station still
configured the old way, and builds nothing else that only such a station would need.

## 3. Keys

### 3.1 The rule

A send-record key changes only when the set of rows it covers changes.  A key has three parts:
source, destination and table (`watermark/sqlite.py:34-42`).  Only the source part takes a new form:

| Source | Source part of the key | When |
|---|---|---|
| SqliteSource, no producer | `sqlite:<db>.<table>` | today's form, unchanged |
| SqliteSource with `producer = P` | `sqlite:<db>.<table>@P` | from v3.73 |
| reserved, not built | `sqlite:<db>.<table>@P/<instance>` | per-instance pipelines, someday |

### 3.2 What changes in v3.73

| Pipeline today | Source part today | Pipelines from v3.73 | Source parts from v3.73 |
|---|---|---|---|
| `psk-pskreporter` | `sqlite:psk.spots` | `psk-recorder-pskreporter` (FT8, FT4) | `sqlite:psk.spots@psk-recorder` |
| | | `meteor-scatter-pskreporter` (MSK144) | `sqlite:psk.spots@meteor-scatter` |

Destination and table stay.  Nothing else moves: not wsprnet, not the wsprdaemon tar, not GRAPE,
not the magnetometer, not the heartbeat.  Their rows do not change owner in v3.73.  Leaving them
alone also spares the readers that match keys by text (map J10): hamsci-physics' GRAPE spool trim
and the heartbeat's backlog report.

The wsprdaemon tar's key changes in step 3, when `include_psk = false` removes psk rows from it.
That step owns its own migration; this spec fixes only the grammar it will use.

## 4. hs-uploader

### 4.1 A source that knows its producer

- `SqliteSource` gains `producer: Optional[str] = None`; the manifest key `producer` sets it.
- With a producer set, `source_id()` returns `sqlite:<db>.<table>@<producer>`, and every statement
  that touches rows adds the producer filter: the selection, the de-duplication, the `start_at`
  anchor, the stale-schema probe and `commit()`'s delete (design §6.1).  A source with a producer
  and `delete_on_commit = true` filters its delete too; it never deletes another producer's rows.
- The filter reads the `producer` column and applies D12's rule to rows that carry `''`: MSK144 to
  meteor-scatter, other psk modes to psk-recorder.  Mode compares trimmed and case-folded, and a
  missing mode counts as psk-recorder's, exactly as the writer infers it (`sink/writer.py:270`).
- A `sink.db` without the `producer` column gets the D12 rule alone, by mode.  The source checks
  for the column once per open; a missing column never raises out of `pump()` (map J11).
- With no producer, everything behaves as today, key included.  A client's own sender, built with
  no producer, therefore keeps the old key (D17 answer 2).
- No index: psk rows live 60 minutes, so a scan stays small.  Step 2b adds the index of §6.1 when
  cleanup moves into the daemon.

### 4.2 A pipeline that knows its client

`Pipeline` gains `client: str = ""`, set from the manifest key `client`, on the factory path and on
the builder path alike (`daemon.py:112-138`).  In v3.73 it changes no behaviour; §4.6 records it.
Step 2b's `may_send(pipe)` gates on it, so sigmond renders it now and 2b changes no manifest field.

### 4.3 The store's version, from birth

`SqliteWatermarkStore` stamps the current store version on a file it creates, and on a zero-length
file it initialises.  A store therefore never reads version 0 merely because the daemon, not
`migrate`, made it (review B1).  `hs-uploader migrate` on a missing or zero-length store creates
the tables and the stamp the same way.

### 4.4 Migration 2: the split

`hs-uploader migrate` gains migration 2.  It runs once, inside `BEGIN IMMEDIATE`, with the daemon
stopped (§5.2).  For each send record whose source part reads `sqlite:psk.spots` and whose
destination is a pskreporter destination:

1. It copies the record to the two new source parts, same destination and table, unless a row
   already exists under the new key.
2. It keeps the old row, frozen (D9).
3. It records each copy, full old key and full new key, in a new table `superseded_keys`
   (review S9).
4. It handles each queued retry under pipeline `psk-pskreporter` (D17 answer 5).  Because the
   daemon stands stopped and a queued retry blocks its pipeline's source (`core.py:334`), the old
   record sits before every row in the retry.
   - With `--sink <path>` given and that sink still holding the row right after the old record,
     every row of the retry still exists.  The migration deletes the retry, and each new pipeline
     re-reads its own rows and sends them once.
   - Otherwise it moves the retry whole to a pipeline the new manifest holds: psk-recorder's when
     rendered, else meteor-scatter's (`--keep-retry-for <pipeline>`, which sigmond passes).  That
     retry ships once, and the other pipeline re-sends its own rows of that batch: at most one batch
     of one producer goes out twice (review S2, S6).
   - A keyless retry from before v3.70 under that name gets the same treatment.
   - This item, unlike the rest, runs on every `migrate`, not once: a retry queued under
     `psk-pskreporter` by a rolled-back daemon gets the same treatment at the next update.  It
     only ever deletes a retry whose rows the sink still holds, or moves one, so repeating it
     cannot skip a row.
5. It leaves dead letters alone.  `hs-uploader status` labels a dead letter whose pipeline no
   longer exists, and `purge-dead-letters` clears it.
6. It prints every old key, new key and record, and every retry it deleted or moved.

`migrate --check` reports all of this without writing.  `migrate` takes `--db`, and sigmond passes
the daemon's own path (map J17).

### 4.5 The superseded and pipeline-state tables

- `hs-uploader status` and the heartbeat's backlog report mark a superseded key and name its
  successors, so the fleetboard can tell a frozen key from a stalled pipeline (map J10).
  `reset-cursor` on a superseded key refuses and names the new keys.
- A new table `pipeline_state(name, client, refused_reason, updated_at)` holds what the daemon
  knows at each start: each pipeline's client, and why it refused one (§4.7).  The backlog report,
  and so the heartbeat, reads it from the store (review S7).

### 4.6 Forward-only, enforced

`advance_cursor` becomes the checked write, and the unchecked write leaves the public interface
(map J12).  Under one `BEGIN IMMEDIATE` transaction it reads the stored record, asks the source's
`cursor_is_after(new, stored)`, and then (D17 answer 6):

| Comparison | Action | Log |
|---|---|---|
| new after stored | write | none |
| new equal to stored | refresh `last_ack` only | none |
| new before stored | refuse | WARNING, both values |
| comparison raises | write | WARNING, both values and the error |

The method returns what it did.  Callers log a refused write as "acked, record held", never plain
"acked".  The rule applies to every source that keeps one position, the GRAPE spool (kept files
compared by modification time, `sources/files.py:233-254`) included; only a delete-on-ack spool
answers "after" always (review N1).

### 4.7 The daemon checks the store before it pumps

At start the daemon reads the store's version.  A pipeline whose source names a producer needs
version 2.  On an older store the daemon refuses only those pipelines, runs the rest, and writes
each refusal to `pipeline_state`.  It never migrates itself (D10).

### 4.8 Capabilities

`hs-uploader capabilities` prints one integer: the highest store version this hs-uploader
understands, 2 for v3.73 (review N7).  sigmond asks the daemon's own interpreter, as
`hs_uploader_supports_discard` does today.

## 5. sigmond

### 5.1 The renderer splits the shared block

`collect_pipelines` rewrites a `psk-pskreporter` block into the two pipelines of §3.2 before it
de-duplicates by name.  Each gets its `producer`, its `client` and its mode filter (`ft8` and `ft4`,
or `msk144`); everything else copies from the block.  A host with only one of the two clients
renders only that client's pipeline (map J14).  No client repository changes (D17 answer 2).
Every other rendered pipeline carries `client`: the client that declared it.

The capability answer decides the form (review S4):

| Probe | Render |
|---|---|
| reports 2 or more | the split |
| reports less than 2, or `capabilities` does not exist | the shared pipeline, with a warning that hs-uploader lags sigmond |
| fails or times out | nothing new: the step keeps the manifest it has and exits non-zero |

### 5.2 The manifest step, reordered

`smd admin uploader manifest`, with `--write` or `--enable` alike (D17 answer 4; review S3):

1. asks the daemon's interpreter for its capabilities (§5.1);
2. renders the manifest to `pipelines.toml.new`;
3. asks `migrate --check` whether a migration is pending;
4. returns when the manifest has not changed and no migration is pending (review B1);
5. notes whether the daemon runs, then stops it;
6. runs `hs-uploader migrate --db <the daemon's store> --sink <its sink> --keep-retry-for <pipeline>`;
   on failure it removes the side file, returns the daemon to the state it found, and exits
   non-zero, so the old manifest and the old keys still match;
7. moves `pipelines.toml.new` into place, keeping a `.bak`;
8. starts the daemon if it ran before, or if `--enable` asked for it.

A migrate that times out kills its process group, not only `runuser` (map J17).  `smd apply` and
bring-up stop ignoring the step's result.  A manifest that changes now stops the daemon for a
second or two; when that pause lands between about 02:55 and 03:10 UTC, mag-recorder's fallback
unit ships that day's zip itself, which sends nothing twice.

### 5.3 D13: a client's own sender

One function, `inprocess_senders(topology)`, reads each client instance's effective environment,
through `systemctl show` of the unit's `Environment` and `EnvironmentFiles`, and applies that
client's own rule (D17 answer 3):

- psk-recorder: `direct` in `PSK_DELIVERY_PIPELINES`, else legacy `PSK_DELIVERY_MODE`, else the
  default `server-merge`, which runs no sender;
- meteor-scatter: `METEOR_SCATTER_DELIVERY_MODE`, which the recorder reads as `direct` when unset.

It returns `(producer, destination)` pairs.  The renderer leaves out only the daemon pipeline that
matches a pair.  When the function cannot read an instance's environment, it returns no pair for
that client, so the daemon pipeline stays: a double post costs less than a silent loss (review S8).

A harmonize rule calls the same function and warns when its answer differs from the rendered
manifest, which happens when someone edits an env file without re-rendering.

On today's fleet the function returns nothing.  Its tests use made-up stations.

## 6. Rollback, a second update, and a hand-moved hs-uploader

- **Rollback.**  `smd admin manifest restore` re-renders with the restored sigmond (D11), so the
  old daemon reads `psk-pskreporter` and resumes from the frozen old record.  It re-sends what the
  new pipelines sent since the migration, bounded by the sink's psk retention, 60 minutes.
- **A second update after a rollback.**  Migration 2 has already run, so the new keys still hold
  their pre-rollback records, and the daemon re-sends up to 60 minutes again.  D9 accepts it
  ("re-send, never skip").  A `psk-pskreporter` retry queued during the rollback gets §4.4 item 4's
  treatment at that update, which repeats on every `migrate` (review N2).
  No catch-up pass: it was the only new code that could skip rows (review S1).
- **hs-uploader moved back by hand** after sigmond rendered the split: the old factory ignores
  `producer` and both pipelines read every row on one key, so one of them ships nothing (map J3,
  TESTED).  The next manifest step's probe catches it and renders the shared pipeline; until then
  MSK144 spots may wait or age out.  Only a hand checkout reaches this state (review N3).

## 7. Operator-visible change

- `hs-uploader status` lists two pskreporter pipelines, each with its client, plus the old key
  marked superseded.  `smd status` and the heartbeat show the same.
- The journal says "acked, record held" whenever the forward-only rule refuses a write.
- The wd30 fleetboard should learn the superseded mark; until it does, it shows the old key's
  `last_ack` standing still.  That change lives on wd30, outside this release.
- On the wire nothing changes: the same spots go to pskreporter under the same callsign.

## 8. Tests

Each test must fail before its change and pass after; the commit message names the mutation that
proved it.

1. **The split neither re-sends nor skips.**  A made-up `sink.db` with FT8, FT4 and MSK144 rows, a
   store at version 1 with a shared record mid-table, and a stand-in pskreporter that records every
   spot.  Migrate, run both pipelines to idle: every row past the old record ships exactly once,
   under the right pipeline.  Repeat with rows carrying `''`, with padded and upper-case modes, and
   with no `producer` column.
2. **A mixed retry**: deleted when the sink holds its rows; moved whole when it does not or when no
   `--sink` came; moved to meteor-scatter's pipeline on a meteor-scatter-only host.
3. **A failed migrate leaves a consistent pair**, in `--write` and `--enable` modes, with the daemon
   left as found.
4. **A store the daemon created reads version 2**, and the manifest step migrates when a migration
   is pending though the manifest did not change.
5. **An old store refuses only producer pipelines**, and `pipeline_state` records why.
6. **Forward-only**: each row of §4.6's table, two processes racing one key, and the GRAPE spool.
7. **`commit()` with a producer deletes only its producer's rows.**
8. **The renderer**: both clients, each alone, each probe answer of §5.1, and every D13 case,
   including an unreadable environment and a variable set only in `coordination.env`.
9. **Rollback and second update**, on the update rig.
10. **The proof on real data**: read-only snapshots of B4's and ND's `watermarks.db` and `sink.db`
    (`sqlite3 -readonly .backup`, operator account), run through v3.72 and v3.73 code side by side.
    The two new pipelines' row sets must not overlap, and their union must equal the old pipeline's
    rows.  The 10-08 snapshots hold no MSK144 row, so the run adds synthetic MSK144 rows, and a
    mutation (drop the producer filter) must make it fail.

## 9. What step 2b (v3.74) inherits

| Step 2b needs | v3.73 provides | 2b still does |
|---|---|---|
| per-client pipeline files in `pipelines.d` | the same names, producers and keys, so moving a pipeline from the renderer to its client's file needs no migration | retire sigmond's rewrite of §5.1, by design |
| `may_send(pipe)` gating on the client | `Pipeline.client`, rendered on every path | the gate itself |
| forward-only send records | enforced | nothing |
| a new pipeline starting at its producer's first stored row | the producer filter in the `start_at` anchor | change the default |
| the store check | at daemon start | run it at each pipeline rebuild too, since client-installed files bypass §5.2's ordering |
| cleanup inside the daemon | `superseded_keys` | skip superseded keys, or a frozen key blocks psk cleanup for good |
| later key splits (step 3's wsprdaemon tar) | the key grammar, migration machinery and `superseded_keys` | its own migration |
| the index on `(target_db, target_table, producer, id)` | nothing | add it with cleanup |

## 10. Out of scope

Per-instance pipelines; any key other than the pskreporter pair; client repository changes; the
sink switch files, `effective_state()` and `may_send` (step 2b); the wsprdaemon tar split (step 3);
retiring in-process senders (step 4); the wd30 fleetboard's reading of the superseded mark.

## 11. Review findings and where they landed

| Finding | Where |
|---|---|
| B1 a daemon-created store reads version 0; the step returned early | §4.3, §5.2 steps 3-4, test 4 |
| S1 catch-up could skip rows | removed; §6 |
| S2 the retry's first row; no sink path | §4.4 item 4, `--sink` |
| S3 `--write` without `--enable` | §5.2 |
| S4 a probe timeout re-rendered the shared pipeline | §5.1 table |
| S5 `commit()` lacked the producer filter | §4.1, test 7 |
| S6 retry moved to a pipeline a host may lack; the double send unstated | §4.4 item 4 |
| S7 the heartbeat could not see refusals or clients | §4.5, `pipeline_state` |
| S8 meteor-scatter's environment read | §5.3 |
| S9 superseded keys held source strings only | §4.4 item 3 |
| N1 no GRAPE exemption exists | §4.6 |
| N2 a retry queued during rollback | §6 |
| N3 hs-uploader moved back by hand | §6 |
| N4 disjointness, MSK144 rows, a mutation | test 10 |
| N5 trim as well as case-fold | §4.1 |
| N6 builder pipelines get no client | §4.2 |
| N7 over-built capability JSON | §4.8 |
| N8 2b's store check and cleanup | §9 |
| N9 J16, J10 fleetboard, J17 mag window, zero-length store | §7, §5.2, §4.3; J16 does not arise while no client sender runs |

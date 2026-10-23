# spec: sink control step 2b, part 2: the switches take effect

**Status:** built on branch `sink-2b-part2` (hs-uploader 77db5c8, sigmond ad53b95, sigmond-appliance
36da796), not yet merged.  Michael answered §16's questions on 2026-10-23 (end of file).  Where the
build's rulings departed from this text, the text now says what the code does, marked "(amended)";
§17 lists each amendment and the ruling behind it.

**Release number.**  Part 1 shipped in v3.78, blessed 2026-10-22.  This work takes whatever number comes
next once it is ready.  "Part 1" below means the code v3.78 carries; "part 2" means this spec; "v3.79"
names the next part of D18, cleanup in the daemon, and moves with it.

This spec implements the second image of D18 in `plan-sink-control.md` §2, under D1, D2, D9, D10,
D12, D13, D17-D21.  Evidence: `research-sink-2b-v377.md` (the code map for all of 2b),
`spec-sink-2b-v377.md` (part 1's spec; its §11 lists what part 2 inherits), `plan-sink-2b-part1.md`
and `ledger-sink-2b-part1.md` (rulings C1-C31 and the deferred minors), and the code on origin/main.

## How to read this spec

- Paths start at `/home/mjh/hamsci/repos/`.  `hsu/` abbreviates `hs-uploader/src/hs_uploader/`.
- Line anchors come from origin/main: hs-uploader 29a0806, sigmond 10eca2f, sigmond-appliance 734afa3.
  Client anchors come from psk-recorder 0bb62df, meteor-scatter 6156e53, wspr-recorder d171d24 and
  mag-recorder 55a450c.
- Words used in one sense throughout.  Part 1's words keep their meaning: **the daemon**
  (`hs-uploader serve`), **the store** (`watermarks.db`), **a send record**, **the sink**
  (`sink.db`'s `pending_uploads`), **the legacy manifest** (`/etc/hs-uploader/pipelines.toml`),
  **the manifest step** (`smd admin uploader manifest --write`), **a switch file**
  (`/etc/hs-uploader/sinks/<client>.toml` or `sinks/site.toml`).  New here:
  - **the effective state** of a client: the stricter of its switch file and `site.toml`, by the read
    rules of design §3.4, as `hsu/sinkswitch.py:111-132` already computes it;
  - **the marker**: `/var/lib/hs-uploader/carry-over-v1.done`.  The carry-over writes it last.  No
    part 2 code enforces anything while it stays absent;
  - **the gate**: the daemon's question `may_send(pipe)`, asked before every send;
  - **held**: a pipeline the gate refuses;
  - **a governed client**: one whose `deploy.toml` declares `[[hs_uploader.pipeline]]` blocks.  Today
    that means wspr-recorder, psk-recorder, meteor-scatter, mag-recorder and hamsci-physics (GRAPE);
  - **an in-process sender**: a sender that runs inside a recorder or in its own unit, outside the
    daemon;
  - **a lever**: the setting that turns one in-process sender off;
  - **a recorder instance**: one systemd unit such as `psk-recorder@AC0G=B4.service`.

## 1. What changes, in one paragraph

Part 2 makes the switch files decide what leaves a station.  The daemon asks the gate before every
send, and a held pipeline waits with its send record and its queued retries untouched.  The sink
writer stores nothing for a repository while its producer's effective state reads `off`.
wspr-recorder's merged wsprdaemon upload drops each psk row whose producer does not read `upload`.
sigmond carries each station's current `[uploads]` setting into switch files once, then writes the
marker that turns enforcement on.  `smd sink off|fill|upload` and its aliases write `site.toml` and
`[uploads]` both (D19), and `smd sink fill` arrives.  When a client's effective state falls below
`upload`, sigmond turns off that client's in-process senders and restarts each touched recorder
instance alone, never radiod.  The heartbeat moves to `pipelines.d/sigmond.toml` with `governed =
false`.  `hs-uploader capabilities --features` gains `gate`, and sigmond renders the new way only when
it sees that word.  With every switch at `upload`, every pipeline forms the same key, selects the same
rows and ships the same bytes as under part 1; §11 proves it on station copies.

## 2. Where each change lives

| # | Change | Repo | Anchor today |
|---|---|---|---|
| 1 | The gate in the pump; a starting record for a pipeline held from its first pump | hs-uploader | `hsu/core.py:279-311` (`Uploader.__init__`), `:410-420` (`_pump_one`), `:432-464` (`_drain_deliverables`), `:466-529` (`_drain_source`); `hsu/daemon.py:561-698` (`Rebuilder`), `:721-865` (`run`) |
| 2 | Enforced switch report, transition logging | hs-uploader | `hsu/daemon.py:626-647` (`report_switches`) |
| 3 | The writer's switch check at `insert()`; the bundled copy | hs-uploader, sigmond | `hsu/sink/writer.py:263-283` (`infer_producer`), `:402-435` (`insert`); `sigmond/lib/sigmond/hamsci_sink/_bundled.py` |
| 4 | The psk rider gate | hs-uploader | `hsu/sources/wspr_cycle.py:75-125` (`__init__`), `:358-401` (the rider) |
| 5 | `hs-uploader sink set`, `sink status` says "enforced", feature word `gate` | hs-uploader | `hsu/cli.py:37` (`FEATURES`), `:138-147`, `:213-273` |
| 6 | The carry-over and its marker | sigmond | new module `sigmond/lib/sigmond/sink_carryover.py`; called from `sigmond/lib/sigmond/commands/uploader.py:363` |
| 7 | Two render forms; the heartbeat file | sigmond | `sigmond/lib/sigmond/uploader_manifest.py:321-370` (heartbeat), `:513-523` (`effective_mode`), `:620-679` (`generate`) |
| 8 | The site form of `smd sink`, `fill`, the aliases, D19's dual write | sigmond | `sigmond/lib/sigmond/commands/sink.py:1-64`; `sigmond/lib/sigmond/commands/config.py:950-994`, `:1016-1047`, `:1122-1126`, `:1146-1253` |
| 9 | Levers and single-instance restarts | sigmond | `sigmond/lib/sigmond/upload.py:47-105`; `sigmond/lib/sigmond/inprocess_senders.py:119-192`; new module `sigmond/lib/sigmond/sink_levers.py` |
| 10 | Readers of the site state | sigmond | `sigmond/lib/sigmond/commands/check.py:933` (`uploads_line`); `sigmond/lib/sigmond/harmonize.py:1215` (`rule_upload_enabled`) |
| 11 | Restore moves the marker aside and removes the heartbeat file | sigmond | `sigmond/bin/smd:4385-4444` (`_restore_rerender_uploader`) |
| 12 | Rig assertions | sigmond-appliance | `test-nested-v3.sh:813-860`, `:1175-1221`; `test-update-v3.sh` (E-bis, G-bis, G-ter) |

No client repository changes.  Part 2 changes how Rob's recorders behave all the same: the writer
check runs inside their processes, sigmond edits their env files, and sigmond restarts their
instances.  §13.1 lists what the claude-bus heads-up to Rob covers.  meteor-scatter gets nothing new
beyond its lever (D21).

## 3. The gate

### 3.1 The rule

`may_send(pipe)` answers in this order.  The first rule that applies decides.

1. `pipe.governed` reads false: **send**.  Only `pipelines.d/sigmond.toml` declares it (§3.6).
2. The legacy heartbeat: an entry from the legacy manifest whose `client` reads `sigmond`, or whose
   transport type reads `heartbeat_sftp`: **send**.  sigmond v3.73 through part 1 renders the heartbeat
   there with `client = "sigmond"` (`sigmond/lib/sigmond/uploader_manifest.py:349`), and no legacy entry
   carries `governed = false`.  Without this rule a rollback of sigmond alone would silence the
   heartbeat (part 1 §11).
3. The marker stands absent: **send**.  Part 1's behaviour holds until the carry-over finishes.
4. The pipeline names no client (`client = ""`): **send**.  sigmond v3.69 through v3.72 renders no
   `client`.  Its manifest still carries the old rendered policy (`discard`, or a hold), and D19
   keeps `[uploads]` current, so that policy stands.  The daemon logs one INFO line per such
   pipeline: "pipeline X names no client; its manifest's policy governs it".
5. Otherwise **send** when `effective_state(pipe.client).state` reads `upload`, and **hold** in every
   other case.  So `fill` and `off` hold alike, and so does a missing client file, an unreadable or
   broken file, an unknown state, or a missing `sinks/` directory.  These last four fail closed, as
   design §3.4 requires.

The stricter of the two switches wins inside `effective_state`, in the order `off` < `fill` <
`upload`.  The site switch acts as a ceiling (D1).  A missing site file sets no limit.

### 3.2 Where the daemon asks

`Uploader` gains `gate: Optional[Callable[[Pipeline], bool]] = None`.  `None` means "always send", so
the in-process shims of psk-recorder, meteor-scatter and wspr-recorder, which build their own
`Uploader`, and the neutrality tool all behave as before.  The daemon passes a `SinkGate` built from
`sinks_dir_for(manifest)` and the marker path beside its store (`default_path().parent`).

The pump asks in three places (design §6.2):

- at the top of `_pump_one` (`hsu/core.py:410`), before anything else;
- before each replay in `_drain_deliverables` (`:448`);
- before each batch in `_drain_source` (`:505`), so a drain-mode pipeline stops between batches.

A switch file can change in the middle of a pump.  The second and third checks make the change count
at the next send, not the next pump.

### 3.3 What a held pipeline does

A held pipeline returns from `_pump_one` without work.  It opens no batch, ships nothing and writes
no attempt row.  It leaves its send record and its queued retries as they stand.  A retry keeps its
attempt count and its `next_attempt_at`, and replays first once the pipeline sends again.  Nothing
dead-letters while held, since nothing attempts.

`DiscardTransport` stays in the code, so a part 2 daemon still reads a manifest that an older sigmond
renders with `discard = true` (§10).  sigmond stops rendering `discard` once the daemon gates (§7.4).

### 3.4 What happens to data, by state

| Effective state | Sink rows for a repository | Packages (GRAPE, magnetometer) | Sent | Retention in part 2 |
|---|---|---|---|---|
| `upload` | stored | built and spooled | yes | as today |
| `fill` | stored | built and spooled | no; the pipeline holds | sigmond's trim still runs: psk rows last 60 min, WSPR rows 24 h, sent or not.  v3.79 moves cleanup into the daemon and spares unsent rows |
| `off` | not stored: the writer drops them (§4) | built and spooled; the doors set them aside when the site leaves `off` (§7.3) | no; the pipeline holds | rows stored before `off` wait, under the same trim |

Raising a client from `fill` to `upload` ships what the trim left, oldest first, exactly once: the
send record never moved while held.  Raising from `off` ships rows stored before `off` that had not
yet gone, and nothing recorded during `off`.

Local rows, `local = 1`, never ship in any state (part 1 §5.2).  Clients with no switch file, such as
codar-sounder, hf-tec, hfdl-recorder and superdarn-sounder, keep storing in every state; no
governed pipeline reads their rows.

### 3.5 A pipeline held from its first pump

A legacy entry with `start_at = "now"` fixes its starting point at the first pump that reads its
source (`hsu/sources/sqlite.py:344-347`; `hsu/sources/wspr_cycle.py:121-123`), and keeps that point
in memory only.  A pipeline held from its first pump never reads its source.  On a fresh station that
goes `off`, then `fill`, then `upload`, the first real pump would anchor at the raise and skip the
whole `fill` backlog.

So at each build, while the marker stands, the daemon gives every governed pipeline whose key holds
no send record, and whose source starts at `now`, a starting record at the source's present
position.  Each such source gains `initial_cursor() -> Optional[bytes]`: the highest id within its
scope for `SqliteSource`; for `WsprCycleSource`, the cycle before the current cycle floor, where its
first pump anchors, so the cycle in progress still ships (amended, ruling C10).  The daemon writes it
through the store's `start_cursor`, an insert that does nothing when the key already holds a row
(amended, ruling C13).  This writes exactly what the first unheld pump would have written, only
earlier.  It never moves an existing record, nor its `last_ack`.  On every station today each key
holds a record, so this writes nothing there; §11 run 3 confirms it.

### 3.6 The heartbeat moves to `pipelines.d/sigmond.toml`

Michael deferred this move to part 2 (part 1 §13, Q1).  When the daemon gates (§7.4), sigmond writes
the heartbeat entry of `heartbeat_pipeline()` into `/etc/hs-uploader/pipelines.d/sigmond.toml`, with
`governed = false` and no `start_at`, and leaves it out of the legacy manifest.  The file holds one
`[[pipeline]]` entry and nothing else, so part 1's file rules accept it
(`hsu/pipelines_d.py:165-205`).  Its transport takes the SSH key from the legacy manifest's
`[identity]`, as every pipeline file does.

- sigmond creates `pipelines.d/` (root:sigmond, 0755) when missing, and writes the file 0644 through a
  temporary file and a rename.  An update in place never re-runs hs-uploader's installer, so nothing
  else creates the directory.
- sigmond lands the file together with the legacy manifest, in step 7 of the manifest step, before
  the daemon starts.  A part 1 daemon that sees the file first rebuilds within one pump; precedence
  then ignores the legacy heartbeat, whose `client` reads `sigmond`.  Either way one heartbeat runs.
- Without the word `gate`, sigmond renders the heartbeat in the legacy manifest as today and removes
  `pipelines.d/sigmond.toml` if present.

## 4. The writer stores nothing in `off`

### 4.1 The rule

`Writer.insert()` (`hsu/sink/writer.py:402`) judges each row as it arrives.  It takes the row's
producer as `flush()` does, `self.producer or infer_producer(...)` (D12).  It drops the row when all
of these hold:

- the writer is not local (`local=False`);
- the marker stands;
- `sinks/<producer>.toml` exists (design §3.4's compatibility rule: a producer with no switch file
  stores);
- `effective_state(producer)` reads `off`.

`fill` and `upload` store.  A batch that mixes producers, as psk.spots does with FT8 and MSK144,
splits row by row: meteor-scatter at `off` drops its MSK144 rows while psk-recorder's FT8 rows store.
The writer judges at `insert()` time, within the 30 s flush of the row's own time (research §A2).

The writer stays live and healthy in `off`.  It logs one INFO line per producer when its decision
changes, "sink switch reads off for X: storing nothing for repositories", and keeps a dropped-row
count that `health` does not turn red.  It never logs per row (feedback: persistent-condition log
spam).

### 4.2 Where the reader lives

The check imports `hs_uploader.sinkswitch` by its absolute name.  An `ImportError` means no
enforcement.  That covers sigmond's bundled copy (`sigmond/lib/sigmond/hamsci_sink/_bundled.py`): the
venvs that load it (codar-sounder, hf-tec, superdarn-sounder) carry no hs-uploader and own no switch
file, so they need "no file, no enforcement" and nothing more (research §A3).  The bundled copy
refreshes by the recipe in sigmond's CLAUDE.md.

### 4.3 Loading the check

Python loads the writer once, when a recorder starts.  `smd update` restarts nothing, so after an
update in place each recorder keeps the part 1 writer until it restarts.  That writer stores in every
state.  If a station went `off` with such a writer running, rows stored during `off` would wait
behind a held send record and ship at the next raise.

So sigmond restarts the stale writers before any effective state first reads `off`.  "Stale" means
the instance's `ActiveEnterTimestamp` falls before the modification time of the installed
`/opt/git/sigmond/hs-uploader/src/hs_uploader/sink/writer.py`.  The instances in scope run
psk-recorder, meteor-scatter or wspr-recorder, the clients that write governed sink rows.  §8.3 gives
the restart mechanics; the carry-over and every change toward `off` run it.  A station whose states
never read `off` needs no such restart: the old writer stores, and `fill` and `upload` store too.

## 5. The psk rider gate

wspr-recorder's `wspr-wsprdaemon` pipeline sets `include_psk = true`.  Its source pulls every
`psk.spots` row of a cycle into the WSPR tar, with no producer filter (`hsu/sources/wspr_cycle.py:358-401`).
Once switches can differ by client, a psk row from a client held at `fill` would ride out under
wspr-recorder's `upload`.

- `WsprCycleSource` gains `sinks_dir` and `marker`, defaulting to `/etc/hs-uploader/sinks` and the
  marker path.  The daemon passes the paths it uses.  The default stays on, so wspr-recorder's
  in-process shim, which builds its own `WsprCycleSource(include_psk=True)`
  (`wspr-recorder/wspr_recorder/hs_uploader_shim.py:739-746`), applies the gate too.
- While the marker stands, the rider keeps a psk row only when its producer's effective state reads
  `upload` at build time.  The producer comes from the row's `producer` column, or from
  `infer_producer` when the column reads `''` (D12).  The select adds `producer` beside
  `payload_json`; a sink without the column infers for every row.
- The WSPR spots and noise of the cycle stay exactly as they were.  The cycle's send record advances
  when the tar ships, so a dropped rider never rides later.  Rows held at `fill` still reach
  pskreporter.info through the producer's own pskreporter pipeline once it reads `upload`.
- The source logs one INFO line per producer when its rider decision changes, and the per-cycle line
  it already writes (`:419-422`) adds the count left out.

This rule checks the state when the tar ships.  Design §6.6 asks for more: the state must also have
read `upload` when the client stored the row, which needs `stored_intervals()`.  §10.2 step 3 of the
design, D18 and the task for this spec state the simpler rule.  §16 Q1 asks Michael to confirm it.

## 6. The carry-over

### 6.1 Where and when it runs

The carry-over runs inside the manifest step, after the features probe and before the render, and
only when the probe returns `gate`.  `smd align`, `smd apply`, bring-up, `smd sink`, restore and the
image's own update sequence all reach the manifest step.  It runs under hs-uploader's install lock,
`/run/hs-uploader-install/lock` (design §10.1; amended, ruling C14-3).  The lock lives in a directory
only root can write, mode 0700, not in the world-writable `/run/lock`, so no other account can plant a
file or a link at its name.  The v3.79 hs-uploader installer, when it learns to take the lock, must use
this path.  The read-only form of the step,
`smd admin uploader manifest` without `--write`, prints the carry-over it would make and writes
nothing.

Design §10.1 also names `smd update`.  The code settles against it.  The first update into part 2 runs
the old sigmond's update code, which has no hook.  `smd update` restarts nothing and never reaches the
manifest step (D10), and the carry-over must precede the render that drops `discard`.  So the
manifest step alone runs it.  `smd update` prints one line when the marker stands absent and the
installed hs-uploader offers `gate`: "the sink switches take effect at the next `smd align`".

### 6.2 Inputs

All exist on stations today:

- the enabled components in `topology.toml`, and the producers with rows in `sink.db`
  (`SELECT DISTINCT producer`, with D12's inference for `''`).  A name seen only in `sink.db`, which
  recorder accounts can write, counts only when it names a catalog entry, and its `deploy.toml`
  resolves by fixed path alone; any other name earns one counted note (amended, ruling C18-2);
- each client's `deploy.toml` declarations (`uploader_manifest._pipeline_decls`, `:191-201`);
- coordination.toml's `[uploads]` block (`enabled`, `mode`, `reason`);
- each instance's env file and effective environment (`sigmond/lib/sigmond/upload.py:47-75`,
  `sigmond/lib/sigmond/inprocess_senders.py:63-104`);
- identity: `[host]` and `[station]` in coordination.toml, reporter ids from env-file names
  (`sigmond/lib/sigmond/instance.py:116-140`), PSWS ids from `psws.read_state`.

### 6.3 The mapping

The site switch:

| `[uploads]` in coordination.toml | `site.toml` |
|---|---|
| `mode = "discard"` | `off` |
| `enabled = false`, no mode (the legacy hold; K3LR) | `fill` |
| `enabled = true`, or no block (a missing block means upload) | `upload` |

Each governed client, enabled or with rows in the sink, gets a switch file:

| Evidence | Client file |
|---|---|
| `PSK_USE_HS_UPLOADER=0` in any psk-recorder instance env (K3LR's `smd config upload psk-recorder K3LR --off`) | psk-recorder `fill` |
| `METEOR_SCATTER_USE_HS_UPLOADER=0` in any meteor-scatter instance env | meteor-scatter `fill` |
| an instance env file that cannot be read, or whose name fails the reporter-id check (amended, ruling C18-3) | that client `fill`, with a reason naming the file |
| the client is not enabled in `topology.toml` | `fill` |
| otherwise | `upload` |

The carry-over ignores `WSPR_USE_HS_UPLOADER`, which marks a merge fleet's decode-only role rather
than an order to stop (design §10.1).  A client with no outbound route gets no file.

Design §10.1 gives two sources of "shows shipping": the send records, and the legacy list rendered as
if `[uploads]` read `upload`.  The second covers every enabled governed client, and a disabled client
renders nothing; so the send records add no client today, and the table above states the result.  One
reading this spec settles: the rendered list counts a client's declarations before identity
substitution.  mag-recorder or GRAPE without PSWS ids renders nothing today and ships once the ids
arrive.  A missing id expresses no operator order, so such a client carries over at `upload` and
keeps that behaviour.  Read literally, the design would give it `fill`, and part 2 offers no
per-client command to lift it.

Each file's first history entry carries `by = "carry-over"`, the reason "carried over from [uploads]
mode = <mode>" or "… from PSK_USE_HS_UPLOADER=0", and, for a client file, one identity table per
instance (design §3.4).  No per-instance lat/lon exists; the host's applies.

D12 forbids a bulk update of `sink.db`.  The carry-over therefore writes no producer into old rows and
marks no `hfdl.spots` or `timestd.events` row local, although design §10.1 lists both.  Readers infer
the producer; no pipeline reads those two tables.  So part 2 adds no writer that sets `local = 1`, and
part 1's deferred re-probe of a missing `local` column waits for step 4, as the ledger says.

### 6.4 Order, the marker, idempotence

1. Take the install lock.
2. Write `site.toml` if absent, then each missing client file, each through `hs-uploader sink set`
   (§9.1), which calls `write_switch()`.  An existing file stays as it stands; the carry-over appends
   nothing to it.
3. Read the states back through `hs-uploader sink status --json` (amended, ruling C18-4).  Run the
   lever step (§8) for every client that the read-back or this run's own plan puts below `upload`,
   and the stale-writer restarts (§4.3) when any reads `off`.  A failure here stops the carry-over
   before the marker.  A read-back that fails also stops it, while a legacy manifest exists; on a
   fresh install, with no legacy manifest yet, the plan's own states decide, and one line says so.
4. Write the marker through `hs-uploader sink mark-carried`: a temporary file and a rename, mode
   0644.  It holds two lines: `carried over at <UTC time>`, then one note line that names every
   switch file this run wrote, `carry-over wrote: site psk-recorder …` (amended, ruling C18-5).
5. Print each client's before and after, as design §10.1 asks.

Any failure before step 4 leaves no marker, so nothing enforces and the next manifest step tries
again.  Files written by the failed run stay and count as existing.  A second run with the marker
standing writes nothing, except for a client that has none (§6.5).

### 6.5 A client that arrives after the carry-over

An operator may install or enable a governed client later, for example meteor-scatter.  It has no
switch file, so the gate holds it as `off`.  Design §3.6 wants exactly that, and has the client's
installer create the file at `off`.  But part 2 has no per-client command to raise it (D18), so the
client would never ship.

This spec proposes that the manifest step, with the marker standing, apply §6.3's client table to any
governed client that lacks a file, with `by = "carry-over"`, and print one line naming it.  The site
switch still limits it.  §16 Q2 asks Michael to choose between this and `off`.

### 6.6 The fleet

The carry-over reads only what each station holds, so stations with mixed versions need no special
case.  What each class of station should see:

| Station | Expect |
|---|---|
| AC0G-B4, on part 1 since 2026-10-21, `[uploads]` at upload | site `upload`; wspr-recorder, psk-recorder, mag-recorder and hamsci-physics `upload`, plus meteor-scatter if enabled.  No lever to touch (psk on `server-raw` since 10-15, D17).  The manifest step lands a changed manifest (the heartbeat moves), so the daemon restarts once. |
| A fresh install from the next image | the wizard writes `mode = "discard"` (the B4 field fact); the carry-over gives site `off` and every client `upload`.  Under a site at `off` every client reads below `upload`, so the lever step lowers every armed sender: psk-recorder and meteor-scatter, the wspr shim, `wspr-uploader` and the mag fallback (amended, ruling C18-5).  `smd sink upload` then lifts the site, and every pipeline ships, as `smd upload on` did on B4. |
| A station on sigmond v3.69-v3.72 (no `client` rendered) | the update brings part 1 and part 2 together.  Until the first manifest step, the daemon's rule 4 (§3.1) leaves the old render's policy in charge. |
| AC0G-ND, w2naf, uacnj (DASI-010) and others on v3.70 or later | as B4, by their own `[uploads]`.  Each env file decides its levers. |
| K3LR (legacy hold, `PSK_USE_HS_UPLOADER=0`, `server-merge`) | site `fill`, psk-recorder `fill`.  Under a site at `fill` every client reads below `upload`.  The lever step moves psk-recorder to `server-raw` and restarts each psk instance alone.  It also lowers the wspr shim, `wspr-uploader` and the mag fallback wherever K3LR arms them (amended, ruling C18-5). |

Before the image ships, mjh's session reads each reachable station's inputs read-only, after a bus
post, and predicts its carry-over by these tables (§13.2).

### 6.7 Rollback and update after a rollback

A rollback never deletes a switch file.  The restore that runs sigmond back below part 2 moves the
marker aside, to `carry-over-v1.done.rolled-back-<UTC time>`, and removes `pipelines.d/sigmond.toml`
(§10).  When that name is taken, it adds `.1`, `.2` … and never replaces a file.  It does both even
when the restore's re-verification fails (amended, Task 22 review).  An update after such a rollback finds no marker, so the carry-over runs again.  It rewrites
`site.toml` from `[uploads]` when the two differ, since `[uploads]` held the truth meanwhile, and keeps
every client file.

## 7. The site form of `smd sink`

### 7.1 Verbs

| Command | Site switch | `[uploads]` written |
|---|---|---|
| `smd sink upload`, `smd upload on`, `smd config uploads enable` | `upload` | `enabled = true` |
| `smd sink fill`, `smd upload hold`, `smd upload off`, `smd config uploads disable` | `fill` | `enabled = false` (the legacy hold) |
| `smd sink off`, `smd upload discard` | `off` | `enabled = false`, `mode = "discard"` |

The legacy `off` keeps its fixed meaning, `fill` (design §2.1).  The per-client form, `smd sink
<client>`, stays out of part 2 (D18; design §10.2 step 5), and `smd sink <client> …` keeps refusing.
`smd sink status` shows every client's effective state all the same (§7.5).

### 7.2 The write (D19)

With the word `gate`, the handler (`_site_change` in `sigmond/lib/sigmond/commands/config.py`) runs,
in order (amended, rulings C20-1 and C20-2):

1. the lever step (§8) for every client that reads below `upload` after the change;
2. before a change to `off`, the stale-writer restarts (§4.3), skipping each instance step 1 restarted;
3. `site.toml`, through `hs-uploader sink set site <state> --reason … --by "smd sink (<user>)"`;
4. `[uploads]` in site-profile.toml and coordination.toml, through `_patch_uploads_block`, as today;
5. the manifest step, which alone takes the install lock.

Before the marker stands, steps 1 and 2 wait: the carry-over inside step 5 lowers and restarts once.
A failure in steps 1-4 is named, and the later steps still run, so an `off` order always lands; the
command then exits 1.  If either write fails, the line names the file that now disagrees.  The manifest step
repairs the mirror at its next run: with the marker standing, `site.toml` holds the truth, and the
step rewrites `[uploads]` in both files from it when they differ, with one line saying so.  That also
undoes a hand edit of site-profile's `[uploads]` that `smd config render` copied into coordination.toml
(`config.py:704-746`): the line names the edit and points at `smd sink`.

Without `gate`, the handler writes `[uploads]` alone, exactly as part 1 does, and says that
`site.toml` waits for an hs-uploader with the gate.

### 7.3 What each change does

- **Leaving `off`**, for `fill` or `upload`: the doors close first, as today (`_close_doors`,
  `:1016-1047`).  sigmond refuses while GRAPE or the magnetometer packages, and sets aside every
  package older than yesterday.  The gap of design §10.3 item 3 stays: today's and yesterday's
  packages ship, as `smd upload on` showed on B4.  It closes in step 4, when the packagers use
  `stored_intervals()`.
- **Lowering**, to `fill` or `off`: after the write, the lever step (§8) runs for every client whose
  effective state now reads below `upload`.  Before a change to `off`, the stale-writer restarts run
  (§4.3).
- **`off`** keeps its safeguards: `--reason` required, and confirmation at a terminal or `--yes`.
  With `gate` it no longer needs `hs_uploader_supports_discard`.  Its words change: "site sink: off —
  nothing ships and nothing new is stored for upload; data stored earlier waits and ships when the
  switch rises".
- **`fill`** prints the part 2 limit plainly: "until hs-uploader cleans the sink itself, sigmond's
  trim keeps FT8 spots for one hour and WSPR spots for 24 hours, sent or not".
- **Raising** to `upload`: sigmond prints, for each client the change lifts to `upload`, its callsign,
  reporter ids, grid and PSWS ids, then "the identity check that can refuse this arrives with each
  client's `sink check` (step 4); nothing refused".  D1 asks sigmond to run that check, and no client
  offers it yet.  It lists each client that stays below `upload` and the switch that keeps it there.
- The manifest step runs after every change, as today (`_regenerate_uploader_manifest`, `:1001-1006`).
  With `gate` the render no longer depends on the site state (§7.4), so the manifest stays the same
  and the daemon does not restart.  The daemon reads the switch file within one pump.

### 7.4 Two render forms

`generate()` (`sigmond/lib/sigmond/uploader_manifest.py:620-679`) gains `features`.  The manifest step
probes once, first, and passes the answer.

| Probe | Marker | Render |
|---|---|---|
| contains `gate` | stands | every client pipeline, with `client`, no `discard`, no hold, no policy banner; the heartbeat in `pipelines.d/sigmond.toml` (§3.6).  The bytes do not depend on any switch. |
| contains `gate` | absent after a failed carry-over | as the row below; the step exits 1 |
| lacks `gate`, or returns the empty set | — | exactly part 1's render, by `[uploads]`; the heartbeat in the legacy manifest |
| `None` (failed or timed out) | — | part 1's render, and one warning, as part 1 §7.2 promised |

With the marker standing and `sink status` unreadable, the step still renders and lands the gate
form, skips the lever step with one line naming the cause, and exits 1 (amended, ruling C19-1).  An
unreadable status must never block an `off` order or a render; the daemon's gate enforces the
switches either way.

D13 stays: the renderer still leaves out a producer's daemon pskreporter pipeline while its
in-process sender runs (`:585-608`).  Once the lever step stops that sender, the next render includes
the daemon pipeline, and the gate decides.

`effective_mode()` (`:513-523`) and `policy_banner()` (`:526-559`) serve only the old form.

### 7.5 Readers of the site state

- `smd sink status` and `smd upload status` read `hs-uploader sink status --json` when the marker
  stands, and `[uploads]` otherwise.  They show the site switch, each governed client's file and
  effective state, which switch sets it, the pipelines held, each in-process sender still armed (as
  UNMANAGED), each stale writer, and any mirror mismatch.
- `smd check`'s `uploads_line` (`sigmond/lib/sigmond/commands/check.py:933`) dates the last change by
  `site.toml`'s `set_at` when the marker stands.
- The heartbeat keeps reading `[uploads]`, which D19 keeps current.  Per-client heartbeat lines belong
  to step 5.
- `harmonize.rule_upload_enabled` (`sigmond/lib/sigmond/harmonize.py:1215`) stops warning "decoding but
  NOT uploading" for an instance whose client's effective state reads below `upload`.  Otherwise it
  would warn on every station that part 2 lowers.

## 8. Stopping in-process senders

### 8.1 The rule

The client answers for every route its data takes (design §2).  So sigmond turns off a client's
in-process senders whenever the client's effective state reads below `upload`, whichever switch puts
it there.  Design §10.2 step 2 says "the site sink switch"; the stricter-of rule extends it to a
client file the carry-over set to `fill`, which covers K3LR.  In part 2 a client file changes only at
the carry-over, so the lever step runs at the carry-over, at each site change, and, idempotent, at
every manifest step.

### 8.2 The levers

| Sender | Lever | Written when | File |
|---|---|---|---|
| psk-recorder direct, or `server-merge` (rows marked for wd30's forwarder) | `PSK_DELIVERY_PIPELINES=server-raw` | the instance's effective delivery, by `inprocess_senders._psk_recorder_sends` and the legacy `PSK_DELIVERY_MODE`, reads anything but `server-raw` alone | `/etc/psk-recorder/env/<id>.env`; reader `psk-recorder/src/psk_recorder/core/recorder.py:206` |
| meteor-scatter direct or `deposit` | `METEOR_SCATTER_DELIVERY_MODE=off` | the mode reads `direct` (or unset) or `deposit` | `/etc/meteor-scatter/env/<id>.env`; reader `meteor-scatter/src/meteor_scatter/core/recorder.py:627-645` |
| wspr-recorder's in-process shim | `WSPR_USE_HS_UPLOADER=0` | the flag reads true | `/etc/wspr-recorder/env/<id>.env`; reader `wspr-recorder/wspr_recorder/hs_uploader_shim.py:243-259` |
| `wspr-uploader.service` | `systemctl disable --now wspr-uploader` | the unit runs or stands enabled | `wspr-recorder/systemd/wspr-uploader.service` |
| mag-recorder's fallback | `[uploader] enabled = false`, a TOML boolean | it reads true or stays absent | `/etc/mag-recorder/mag-recorder-config.toml`; reader `mag-recorder/src/mag_recorder/cli.py:436`; runs only when hs-uploader stands inactive (`mag-recorder/systemd/mag-recorder-upload.service:38`) |

The env writes go through `set_env_flag` (`sigmond/lib/sigmond/upload.py`), which now writes through
`safe_write.rewrite_owned`: one descriptor on the env directory for the read and the write (amended,
Task 13).  An existing env file keeps its owner and mode.  A new one goes to the directory's owner at
0644.  The write refuses a lever file that is a symlink, a hard link, or (as root) a file owned by
anyone but root or the directory's owner; the lever then fails loudly and names the file.
The mag write needs a boolean-aware setter.  `_set_toml_field` (`sigmond/lib/sigmond/psws.py:198`)
writes `"false"`, a string that mag-recorder reads as true.  The new setter writes `false` bare and
re-reads the file with `tomllib` to confirm a boolean.

The lowering runs one way.  Raising restores nothing; step 4 retires these senders.  `smd sink
status` names any sender still armed.

### 8.3 Restarts

- Each touched recorder instance restarts alone, by `systemctl restart <unit>`, one at a time.  The
  lever step never calls `cmd_restart`, never restarts a client by name, and never touches radiod.
  sigmond c478b40 already keeps `smd restart <client>` off radiod; the lever step still restarts single
  units, since only the touched instance needs it.
- `wspr-uploader.service` stops through its own `disable --now`.  The mag fallback runs from a timer
  and reads its config each run, so it needs no restart.
- The stale-writer restarts of §4.3 use the same path, and skip an instance the lever step already
  restarted.
- Before the first restart, sigmond records radiod's `ActiveEnterTimestamp`.  After the last, it reads
  it again and fails loudly if it moved.
- The step prints each file it changed, each unit it restarted, and each it left alone.  A second run
  with nothing to change touches nothing and restarts nothing.

A restart costs each WSPR instance one cycle and each psk instance one slot.

### 8.4 Checking that each sender stopped

After the restarts sigmond checks, and exits 1 with the instance named when a check fails:

- psk-recorder and meteor-scatter: `inprocess_senders()` (`:167-192`) reads each instance's effective
  environment again and finds no sender.
- wspr-recorder's shim: the restarted instance's effective environment reads `WSPR_USE_HS_UPLOADER`
  false.
- `wspr-uploader`: `systemctl is-active` reads inactive and `is-enabled` reads disabled.
- mag-recorder: `tomllib` reads `uploader.enabled` as the boolean `False`.

The bench (§13.3) also checks the store: no attempt row with an empty `pipeline` column, the mark of
a sender outside the daemon, after the restart time.

## 9. hs-uploader's command line, and the probe

### 9.1 New verbs

- `hs-uploader sink set <site|client> <off|fill|upload> --reason TEXT --by TEXT [--identity-json
  FILE] [--sinks DIR | --manifest PATH]` calls `write_switch()`.  It refuses an unknown state or a bad
  name, and exits 1 with the reason.  It needs root, since `sinks/` belongs to root:sigmond.
- `hs-uploader sink mark-carried [--note TEXT]` writes the marker (§6.4).
- `write_switch()` names the file in any emitter error, for example a hand-written TOML datetime it
  cannot re-emit (part 1 final review, "emitter errors name the file").

sigmond calls them through the daemon's own interpreter, as `python -I -m hs_uploader.cli` from
`cwd=/` (amended, ruling C14-2: `-I`, isolated mode, in place of `-P`).  The package has no
`__main__`.  So one implementation writes every switch file, as design §3.4 requires.
`sink status` also takes `--marker PATH` (amended, ruling X5).

### 9.2 `sink status` and the switch report

- `hs-uploader sink status` reads "enforced" when the marker stands, and lists each held pipeline with
  its client and the switch that holds it.  It lists a client whose pipeline file the daemon refused
  (part 1 deferred minor, Task 11).  The effective states and `held_by` do not depend on the marker;
  only `enforced` and the held list do (amended, ruling X5).
- `sink status --json` carries `"schema": 1`, with the keys `schema`, `enforced`, `marker`,
  `sinks_dir`, `site`, `clients[]` (with `held_by`), `held_pipelines[]` (with `held_by`),
  `refused_files[]` (with `pipeline`; `file` may be null, for a pipeline the daemon refused for the
  store's schema), `no_client_pipelines`, `builder_pipelines` and `errors` (amended, Task 9).
  sigmond refuses any other schema number.
- `report_switches` (`hsu/daemon.py:626-647`) drops "not enforced in this release".  The gate logs
  each pipeline's transition once, at INFO: "pipeline X held: sink filling (site)" and "pipeline X
  sending: sink uploading".  At start it logs one line per held pipeline.  A pipeline held for a week
  logs nothing after its first line (feedback: persistent-condition log spam).

### 9.3 The feature word

`FEATURES` (`hsu/cli.py:37`) gains `gate`, last of the release's tasks to land, as part 1 ruled for
its own words (ruling 1).  `gate` promises all of: the pump gate with rules 1-5, the starting record,
the writer check, the rider gate, `sink set` and `sink mark-carried`.  Bare `capabilities` keeps
printing `3`.

## 10. Version pairings and rollback

Part 2 updates stations in place.  `smd update` pulls code and restarts nothing.  The next manifest
step runs the carry-over and renders.

| Case | What happens | Sends |
|---|---|---|
| **Both part 2** | The probe sees `gate`.  The carry-over writes files, runs levers if any state reads below `upload`, writes the marker.  The render drops `discard` and moves the heartbeat, so the manifest changes and the daemon restarts on new code, which gates. | by the switch files; with every switch at `upload`, as part 1 |
| **Part 2 sigmond, part 1 hs-uploader** (v3.77 code, `gate` absent) | No carry-over, no marker.  sigmond renders by `[uploads]` and keeps the heartbeat in the legacy manifest.  `smd sink fill` writes the legacy hold. | as part 1 |
| **Part 2 sigmond, v3.73-v3.76 hs-uploader** | The probe returns the empty set.  As the row above. | as part 1 |
| **Part 1 or older sigmond, part 2 hs-uploader** | No sigmond writes the marker, so rule 3 passes every pipeline, the writer stores, and the rider keeps every row. | as part 1 |
| **Rollback of both** after part 2 ran, through `smd admin manifest restore` | The part 2 restore code sees that the restored sigmond carries no `lib/sigmond/sink_carryover.py`.  Before the restored smd renders, it moves the marker aside and removes `pipelines.d/sigmond.toml`.  The restored sigmond renders by `[uploads]`, which D19 kept current.  The old daemon ignores the switch files.  The lowered levers stay lowered.  A client file at `fill` no longer counts; K3LR's psk-recorder then behaves as before part 2, still on `server-raw`. | by the current site setting |
| **Rollback of hs-uploader alone** | The restore's manifest step probes: no `gate`.  sigmond renders the old way by the site setting, puts the heartbeat back in the legacy manifest, and removes `sigmond.toml`.  It moves the marker aside (amended, Michael's answer 5), so when the gate returns the carry-over runs again from the current `[uploads]`.  Client files at `fill` go unenforced until hs-uploader returns. | by the site setting |
| **Rollback of sigmond alone** | As "both" for the marker and `sigmond.toml`.  The part 2 daemon then passes everything by rule 3, and the restored render's policy governs. | by the site setting |
| **A hand rollback** (`git checkout`, no restore) | Unsupported.  A part 2 daemon keeps enforcing `site.toml`, which the old `smd upload` no longer writes.  The release notes say: roll back only with `smd admin manifest restore` (`tasks/release-notes-sink-2b-part2.md`). | — |
| **Update after a rollback** | No marker: the carry-over runs again, re-derives `site.toml` from `[uploads]`, keeps client files, writes the marker. | by the switch files |

No row depends on a changed unit file, or on hs-uploader's installer.

## 11. The proof on station copies

### 11.1 Copies

Part 1's copies of 2026-10-21 predate B4's migration to store version 3.  Before release, mjh's
session takes fresh read-only copies from B4 and ND, after a bus post, by part 1's method
(`sqlite3 -readonly <db> ".backup <copy>"` as the operator account, checksums compared on the
devbox, station copies deleted).  Beyond `sink.db`, `watermarks.db` and `pipelines.toml`, part 2
copies the carry-over's inputs: `topology.toml`, `coordination.toml`, `site-profile.toml`, every
`/etc/{psk-recorder,meteor-scatter,wspr-recorder}/env/*.env`, `/etc/wspr-recorder/uploader.env`, the
mag-recorder config, and `systemctl is-enabled wspr-uploader`.

### 11.2 The tool

`tools/neutrality_check.py` gains `--sinks DIR` and `--marker PATH` for the NEW side.  Its worker
builds each pipeline's `Uploader` with the daemon's own gate when the tree offers one
(`hsu/daemon.py`'s `SinkGate`), and the tap still stops each pump at the first send.  Each pipeline
then ends in one of three verdicts:

- AGREE: both sides produced the same key, record, retries, limits and batch digest;
- HELD: NEW produced no batch, popped no retry and wrote no record, where the switches say it holds;
- DISAGREE: anything else;
- INCONCLUSIVE (amended, Task 12): once the marker stands, a pipeline the tool cannot find in its own
  read of the manifest (a builder entry), or one of several entries that share a name whose build
  order the tool cannot confirm.

The last line counts the verdicts and reads `RESULT: PASS (a AGREE, h HELD, d DISAGREE)`, or
`RESULT: DISAGREE (…)`, or `RESULT: INCONCLUSIVE (…; reason)`.  The tool exits 0 only on PASS.  It
diffs the store copy's send records around NEW's starting-record pass itself and names each key the
pass added; a record that existed and changed ends the run DISAGREE.

For `wspr-wsprdaemon`, the tool computes the expected NEW batch on its own: OLD's batch minus the
psk rows whose producer, read from the column or by D12's rule copied into the tool, does not read
`upload`.  The tool keeps its own copy of the rule, so a defect in hs-uploader's inference cannot hide
itself (feedback: test power by mutation).

### 11.3 Runs

OLD names hs-uploader 29a0806 (part 1).  NEW names the part 2 head.  Each run fixes `--now` at the
copy's time.

1. **As copied**, no `sinks/`, no marker.  Every pipeline AGREE.  Part 2 code stays inert before the
   carry-over.
2. **The carry-over on the copies.**  Run sigmond's carry-over against the copied inputs with a root
   override into a scratch tree.  Record the files it writes and the levers it would touch, and check
   them against §6.6's prediction for that station.
3. **Marker, every switch at `upload`.**  NEW reads the scratch `sinks/` and marker.  Every pipeline
   AGREE, as copied and with send records moved back (part 1's two rewinds).  The starting-record
   pass (§3.5) writes nothing, since each key holds a record; the tool prints each key's record.
4. **One client held.**  For each governed client in turn, at `fill` and then at `off`: exactly that
   client's pipelines HELD, every other pipeline AGREE.  With psk-recorder or meteor-scatter held,
   `wspr-wsprdaemon` matches the tool's expected batch.  With wspr-recorder held, both WSPR pipelines
   HELD.
5. **The site held**, at `fill` and at `off`: every governed pipeline HELD.  With ND's manifest, which
   names no client if ND still runs a v3.69 render, every pipeline AGREE by rule 4.  The run records
   which case ND's copy shows.
6. **Fail closed**: marker standing with `sinks/` missing, then with a broken `site.toml`.  Every
   governed pipeline HELD.
7. **The tool still catches a difference.**  A NEW tree with the gate removed from `_pump_one` and
   the per-batch check ends DISAGREE on run 4.  A NEW tree with the rider gate removed ends DISAGREE on
   run 4 with psk-recorder held.  A NEW tree that ignores the marker ends DISAGREE on run 1.  A run
   that cannot fail proves nothing.

### 11.4 What this proof leaves to other tests

The tool compares what each side would read and hand to a transport.  The writer check, the levers,
the restarts and the render rest on unit tests (§12), the rig (§13.1) and the bench (§13.3).  File-tree
pipelines read spools that live on the station, so on the devbox the tool sees their keys, records and
HELD verdicts, not their batches.

## 12. Tests

Each test must fail before its change and pass after.  The implementer then applies the named
mutation to the finished code, watches the test fail, and names the mutation in the commit message.

| # | Test | Mutation that must make it fail |
|---|---|---|
| 1 | The leak harness (design §11): the daemon, stand-in transports that record every byte, and a writer, driven through every site and client state and each transition.  Nothing leaves in `off` or `fill`; `fill` → `upload` ships the backlog once, oldest first; nothing stored during `off` ever ships; the heartbeat ships throughout | remove the gate from each of its three places in turn; remove the writer check |
| 2 | `may_send` rules 1-5: `governed = false`; legacy heartbeat by client and by transport; no marker; `client = ""`; all nine switch pairs; missing client file, broken file, unknown state, missing `sinks/` | drop the heartbeat exemption; ignore the marker; hold a client-less pipeline; swap the order `off` < `fill` |
| 3 | A held pipeline writes no attempt row, pops no retry, moves no record; its retry keeps its attempt count and replays first on release | let the held path pop the retry |
| 4 | A switch that changes between two replays stops the second; one that changes between two drain-mode batches stops the second | drop the per-replay check; drop the per-batch check |
| 5 | `Uploader()` with no gate behaves as part 1 (the shims' path) | default the gate to the system's switch files |
| 6 | The starting record: a held `now` pipeline with no record gets one at build; `off` → `fill` → `upload` on a fresh store ships the `fill` rows; a key with a record stays untouched | anchor at the first unheld pump (part 1's behaviour) |
| 7 | One INFO line per pipeline per transition; a hundred held pumps add none | log inside the held branch of `_pump_one` |
| 8 | The writer: `off` drops, `fill` and `upload` store; a mixed psk batch splits by producer; no client file stores; no marker stores; `local=True` stores; a switch change counts at the next `insert()` with no restart | remove the check; invert the marker test; judge by `self.producer` alone; check local writers |
| 9 | The bundled copy matches hs-uploader's writer; with `hs_uploader` unimportable it stores and raises nothing | let the `ImportError` propagate |
| 10 | The rider: a cycle with FT8 from psk-recorder at `fill` and MSK144 from meteor-scatter at `upload` ships the MSK144 rows only; WSPR rows unchanged; `producer = ''` rows inferred; no marker keeps all; the source built without paths, as the shim builds it, still filters | drop the rider gate; skip inference for `''`; default the rider gate off |
| 11 | `pipelines.d/sigmond.toml` with `governed = false` ships with the marker standing, no `sigmond` switch file and the site at `off` | ignore `governed` |
| 12 | `sink set` writes through `write_switch` and refuses a bad state or name; `mark-carried` writes the marker atomically; an emitter error names the file | write the file directly, bypassing the lock |
| 13 | `sink status` says "enforced" and lists held pipelines and refused-file clients; `--features` adds `gate`; bare output stays `3\n` | print `gate` on the bare call |
| 14 | Render with `gate` and marker: every client pipeline, no `discard`, heartbeat only in `sigmond.toml`, legacy bytes identical for site `off`, `fill` and `upload` | render `discard` for site `off` under the gate |
| 15 | Render without `gate`, and with a probe of `None`: byte-identical to part 1's render for each `[uploads]` mode, heartbeat in the legacy manifest, `sigmond.toml` removed | trust the daemon whenever the probe fails |
| 16 | Carry-over, one case per row of §6.3's tables, K3LR's case included; `WSPR_USE_HS_UPLOADER=0` ignored; an unresolved PSWS id still `upload`; history entries carry `by = "carry-over"` and identity per instance; the marker lands last; a second run writes nothing; a failure before the marker leaves none | read the wspr flag; write the marker first; rewrite an existing file |
| 17 | The site form: each verb of §7.1 writes `site.toml` and both `[uploads]` blocks; the mirror repair rewrites `[uploads]` from `site.toml`; without `gate`, `[uploads]` alone | map the legacy `off` to `off`; repair in the wrong direction |
| 18 | Leaving `off` for `fill` or `upload` closes the doors; any other change does not | close the doors only on `upload` |
| 19 | The lever step: each lever of §8.2 written for an effective state below `upload`; site at `upload` with psk-recorder at `fill` lowers psk-recorder alone; only touched units restart, by `systemctl restart <unit>`; no radiod unit appears; mag's lever reads back as a boolean; a second run changes nothing | route through `cmd_restart`; write `"false"` as a string; restart untouched units |
| 20 | Stale writers restart before the first change to `off` and not for `fill`; fresh ones stay | skip the restart |
| 21 | `rule_upload_enabled` stays quiet for an instance whose client reads below `upload` | the part 1 rule |
| 22 | Restore below part 2 moves the marker aside and removes `sigmond.toml`; restore to part 2 leaves both | keep the marker |
| 23 | `smd sink status` reads `hs-uploader sink status --json` once the marker stands, and `[uploads]` before | read `[uploads]` only |

## 13. The rig, the bus and the bench

### 13.1 The rig

On the rig (`root@192.168.1.182`), after checking that no other build or test runs: build an image
from the branch.

- **Nested install** (`test-nested-v3.sh`).  The fresh guest shows site `off`, every client `upload`,
  the marker, `gate` in the features line, no `discard` in the manifest, and the heartbeat in
  `sigmond.toml`.  Phase D's assertions on `mode = "discard"` (`:853-860`) stay, since D19 still
  writes `[uploads]`; those on the banner and per-pipeline `discard` (`:1192-1221`) move to `site.toml`
  and to `hs-uploader sink status`.  Then plant a direct psk-recorder, an unset meteor-scatter mode with
  callsign and grid, an enabled `wspr-uploader` and an enabled mag fallback in the guest, run `smd sink
  fill`, and check: each lever written, each touched unit restarted once, radiod's start time unchanged.
- **Update in place** (`test-update-v3.sh`) from the v3.78 image.  E-bis: the carry-over ran before the
  render, the marker stands, `site.toml` matches `[uploads]`.  G-bis: restore to v3.78 moves the marker
  aside, removes `sigmond.toml`, renders the heartbeat in the legacy manifest, and `[uploads]` still
  matches.  G-ter: the next update runs the carry-over again, writes the marker, and leaves every
  client file's history as it stood.

### 13.2 The bus

Rob owns wspr-recorder, psk-recorder and the multi-receiver paths.  Before the plan starts, one bus
post tells him what part 2 does to his recorders: the writer check inside their processes, the env
levers, single-instance restarts at lowering and for stale writers, and the rider gate in the shim.
It asks him one thing: does any station run a WSPR merge fleet whose merge instance ships through
the in-process shim?  D17 found none.  Where one runs, lowering stops its shim, and the daemon's WSPR
pipelines must carry the merge reporters before the site rises again.

The station survey of §6.6 and §11.1, and every B4 step below, start with their own bus post.  Any
change to B4's services goes into `/etc/sigmond/CLAUDE-COORDINATION.md` on the VM.

### 13.3 The B4 bench

After the rig passes, with Michael's go:

1. **Deploy** by checkout and the manifest step, as part 1 did.  Expect: `gate` in the features line;
   the carry-over plan of §6.6; the marker; one daemon restart; no recorder restart, since no state
   reads below `upload`; radiod untouched; the heartbeat arriving at the fleetboard from `sigmond.toml`.
2. **A night at `upload`**, ND as the control: pskreporter, wsprnet and wd30 counts per hour within
   the night-to-night spread; no held line in the journal; a `last_send` row for every pipeline that
   sent.
3. **`smd sink fill`** for the window of §16 Q3.  On the wire (TCP 4739 to pskreporter, SFTP to gw1,
   gw2 and PSWS, HTTPS to wsprnet) nothing leaves but the heartbeat.  wd30 shows no B4 rows after the
   switch.  Then `smd sink upload`: the backlog ships once, oldest first; wd30 receives the held
   cycles; pskreporter receives at most the trim's last hour.
4. **`smd sink off`** for one hour.  Read-only counts by producer in `sink.db` show no new psk or WSPR
   rows; codar and hfdl rows keep growing.  The stale-writer restarts run first, if any writer predates
   the check.  Then `smd sink upload`: the doors run, and wd30 shows a gap of exactly that hour.
5. **The rider**, bench only: `hs-uploader sink set psk-recorder fill` for 30 minutes.  wd30 shows B4's
   WSPR cycles with no B4 FT8 rows; pskreporter shows no B4 FT8 reports; MSK144 carries on.  Then set
   it back to `upload`.
6. **Levers**: read B4's env files, `wspr-uploader` and mag config first.  The lowerings in steps 3
   and 4 must touch only what that read predicts (D17 says nothing).

Throughout: radiod's `ActiveEnterTimestamp` never moves, and each recorder restart appears in the
journal once, named.

## 14. What v3.79 inherits

| v3.79 needs | Part 2 provides | v3.79 still does |
|---|---|---|
| cleanup that spares unsent rows | the gate, so a held pipeline's record marks exactly what remains unsent; producers inferred by D12 at read time | `sent_before`, the per-producer retry guard (`_sink_holds` with the `local` clause), unknown readers, superseded keys, the 5 GB ceiling, `sink drop`, retiring sigmond's trim units (D18, D20) |
| `fill` that keeps what it promises | `smd sink fill`, which prints the trim's limits | the daemon's cleanup in place of the trim; `smd sink fill` drops the caveat |
| status that shows held data | held pipelines in `sink status` | backlog size and age per client |
| a producer index that earns its keep | nothing new; part 1's index stays unused by the planner (ledger, Task 17) | keep it for cleanup or drop it |
| step 4's clients | the gate, the writer check, `governed`, `sink set` | each client's own `sink` command and pipeline file; the re-probe of a missing `local` column before any writer sets `local = 1`; `stored_intervals()` for the packagers and, if Michael chooses it, the rider |

## 15. Out of scope

Cleanup in the daemon, the 5 GB ceiling, `sink drop` and the retirement of the trim units (v3.79);
any VACUUM (D20); the per-client form `smd sink <client>` and each client's `sink` command (steps 4
and 5); `stored_intervals()`; per-client wsprdaemon uploads and `include_psk = false` (step 3);
storage grants (step 6); the contract's §20 (step 7); anything new for meteor-scatter beyond its lever
(D21); any client repository change; any unit file change; per-client lines in the heartbeat and the
fleetboard; masking ka9q-radio's feeder units.

## 16. Open questions

Settled from D1-D21 or the code, so not asked:

- Whether part 2 includes the per-client form: no.  D18 lists only the site form (§7.1).
- Whether the writer check belongs in part 2: yes.  Without it `off` would store rows that ship at the
  next raise (§4); D2 defines `off` as storing nothing for a repository.
- Where the carry-over runs: in the manifest step only (§6.1).
- Whether the carry-over writes producers or `local` marks into old rows: no (D12; §6.3).
- Whether a fresh station starts its clients at `off`: not in part 2.  With no per-client command, a
  client at `off` could never ship; the site switch starts at `off` instead, as v3.69 set (§6.6).
- Whether a client without PSWS ids carries over at `fill`: no, at `upload` (§6.3).
- Whether part 2 needs the `local` column re-probe: no; no part 2 writer sets `local = 1` (§6.3).
- Whether sigmond keeps rendering `discard` under the gate: no.  The gate stops a held pipeline before
  its transport, so `discard` would add nothing, and a stale render could ack away data after a switch
  rises (§7.4).
- How a rollback ships: by the current site setting, through D19's mirror (§10).

Questions for Michael:

1. **The psk rider: when shipped, or also when stored?**  This spec drops a psk row from the WSPR tar
   when its producer does not read `upload` at ship time, as D18, design §10.2 step 3 and this task
   word it.  Design §6.6 also requires `upload` when the client stored the row.  That stricter rule
   needs `stored_intervals()`, and it would keep a site-wide `fill` backlog's FT8 rows off
   wsprdaemon.org forever, although `fill` promises to ship them.  Recommendation: the ship-time rule
   now; revisit with step 3.
2. **A client that arrives after the carry-over.**  (a) The manifest step writes its switch file by
   the carry-over's table, usually `upload`, under the site switch (§6.5).  (b) It starts at `off`, as
   the 10-06 decision says, and part 2 grows a minimal `smd sink <client>` to raise it.
   Recommendation: (a), which keeps part 2 inside D18; the decision then holds from step 4.
3. **B4's bench window.**  Design §11 asks for a day in `fill`.  Until v3.79 the trim keeps WSPR 24 h
   and psk one hour, so a day proves little beyond what two hours prove, and it holds a day of B4's
   data.  Recommendation: two hours in `fill`, one in `off`, 30 minutes of the rider test; the full day
   with v3.79, when `fill` keeps everything.
4. **The stale-writer restarts.**  Before any state first reads `off`, sigmond restarts each psk, meteor
   and WSPR instance whose writer code predates the check (§4.3).  On a bench station going `off` right
   after an update, that restarts every recorder instance once.  Recommendation: accept it, with Rob's
   agreement on the bus.  The alternative, a reader-side filter on `queued_at`, costs far more code.

## Size

hs-uploader: about 450 lines and 650 of tests (gate, starting record, writer check, rider, two verbs,
tool).  sigmond: about 750 lines and 950 of tests (carry-over, two render forms, heartbeat file, site
form, levers and restarts, readers, restore).  sigmond-appliance: about 150 lines in the two rig
scripts.  In all about 1,350 lines of code and 1,750 of tests, a little larger than part 1, with more
of the weight in sigmond.


## Michael's answers, 2026-10-23 (all as recommended)

1. The psk rider checks the switch at **ship time only**: a client raised from fill to upload ships what it held.
2. A client installed after the carry-over gets **upload** under the site switch.
3. The B4 bench runs the **short form**: 2 h fill, 1 h off, 30 min rider test, until v3.79.
4. The one-time restarts of recorders on old writer code are **accepted**, one at a time, never radiod,
   announced to Rob on the bus beforehand.
5. **Rollback without the gate moves the marker aside** (Michael, 2026-10-23), replacing §10's "marker
   stays" row: when the probe lacks `gate`, the manifest step moves the carry-over marker aside, so a
   later return of the gate re-runs the carry-over from the current `[uploads]` and an operator's
   order given meanwhile (e.g. `fill`) survives.

## 17. Amendments after the build, 2026-10-23

The build on branch `sink-2b-part2` departed from this text in the places below.  The build's
ledger holds each ruling in full; it joins `tasks/` as `ledger-sink-2b-part2.md` at merge, as part 1's
did.  The sections above
now say what the code does.

| § | What changed | Ruling |
|---|---|---|
| 3.5 | `WsprCycleSource` anchors its starting record at the cycle before the current floor, where its first pump anchors, not at the floor itself.  The floor would skip the cycle in progress, which an unheld pipeline ships. | C10 |
| 3.5 | The starting record goes through the store's insert-only `start_cursor`, not `advance_cursor`.  An existing record and its `last_ack` never move. | C13 |
| 6.1 | The install lock lives at `/run/hs-uploader-install/lock`, in a directory only root can write, not at `/run/lock/hs-uploader-install.lock`.  The v3.79 hs-uploader installer must use the new path.  Michael confirms this at merge time. | C14-3 |
| 6.2 | A name seen only in `sink.db` counts only when it names a catalog entry, and resolves by fixed path alone. | C18-2 |
| 6.3 | An unreadable or badly named stop-flag env file carries its client at `fill`.  In part 2 no `smd` verb raises a single client: `smd sink` is site-only (D18).  To raise one, run `hs-uploader sink set <client> upload --reason … --by …` as root. | C18-3 and its correction |
| 6.4 | The carry-over reads the states back through `sink status --json` before the lever step.  The marker carries one note line, which names every switch file written. | C18-4, C18-5 |
| 6.6 | Under a site at `fill` or `off`, every client reads below `upload`, so on K3LR and on a fresh install the lever step lowers the wspr shim, `wspr-uploader` and the mag fallback too, wherever they stand armed. | C18-5 |
| 6.7, 10 | Restore moves the marker aside, `.1`, `.2` … when the name is taken, and removes `pipelines.d/sigmond.toml`, even when re-verification fails. | Task 22 review |
| 7.2 | A site change runs the lever step first, then stale-writer restarts before `off`, then `site.toml`, then `[uploads]`, then the manifest step.  Before the marker, the carry-over alone levers. | C20-1, C20-2 |
| 7.4 | With the marker standing and `sink status` unreadable, the step renders and lands, skips the lever step, and exits 1. | C19-1 |
| 8.2 | Lever writes go through the safe writer.  An existing env file keeps its owner and mode.  A symlink, a hard link or a stranger's file at a lever path refuses. | Task 13 |
| 9.1 | sigmond runs hs-uploader as `python -I -m hs_uploader.cli`; `-I` replaces `-P`.  `sink status` takes `--marker`. | C14-2, X5 |
| 9.2 | `sink status --json` schema 1 gains `held_pipelines[].held_by`, `errors`, `builder_pipelines` and `refused_files[].pipeline`, with `file` null for a store-schema refusal. | Task 9 |
| 10 | Rollback of hs-uploader alone moves the marker aside. | Michael's answer 5 |
| 11.2 | A fourth verdict, INCONCLUSIVE, and the last line `RESULT: PASS (a AGREE, h HELD, d DISAGREE)`. | Task 12 |

One product gap stays open (Task 24).  A restore leaves each checkout it moved detached at the
restored commit, with no `.pin`.  `smd update` then pulls nothing into it: a detached checkout has no
upstream, and `--unpin` releases only a pinned one.  Restore's own comment promises that `smd update`
brings the components back onto main; it does not.  To move such a station forward, the operator runs
`smd align --apply`, which moves each checkout to the latest blessed release (detached, with a
`.pin`); `smd update --unpin` then releases them onto their branches.  The release notes say so; the
fix belongs to the next build.

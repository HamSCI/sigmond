# The key split's six open questions: what the code and the stations show

Written 2026-10-19 for Michael, read-only. Nothing changed on any repository or station.

Sources: the research map `sigmond/tasks/research-key-split-v373.md` (written 2026-10-10), the
design `sigmond/tasks/plan-sink-control.md`, current `main` in every repository named below, and
light read-only checks on AC0G-B4, AC0G-ND, K3LR, UACNJ and W2NAF made this morning (11:00Z).
AI6VN refused the reach (no key on its host) and DASI-002 refused the connection. Paths start at
`/home/mjh/hamsci/repos/`; `hsu/` stands for `hs-uploader/src/hs_uploader/`. The map says
"v3.72"; read v3.73.

## What changed since the map

The map read hs-uploader at 8376fd8 and sigmond at 5f39b05. Since then:

- **hs-uploader** gained one commit, 3090b91: the backlog report now says which dead letters are
  recent, and a new CLI command, `hs-uploader purge-dead-letters` (`hsu/cli.py:70-82`). No other
  file the map cites changed. The core, the store, the sources and the transports read as the map
  describes them; only line numbers moved a little.
- **sigmond** moved 40 commits, mostly v3.72 work. The cited files changed only in wording and in
  reporter-id spelling (`upload.py`, `harmonize.py`, `commands/config.py`). The manifest step still
  writes the manifest at `commands/uploader.py:272`, migrates at `:290`, and restarts at `:307-310`.
- **Client repositories** changed, but not in their senders. psk-recorder, wspr-recorder and
  meteor-scatter took reporter-id fixes; meteor-scatter also took the MSQP capture work. No commit
  touched a shim, a delivery switch or a `[[hs_uploader.pipeline]]` block.
- **The stations changed the most.** On 2026-10-10 the map read B4 with two pskreporter senders. B4
  got a fresh bring-up on 2026-10-15 (the v3.72 reflash; its per-instance env files carry that
  date). Today B4 sets `PSK_DELIVERY_PIPELINES=server-raw` and `METEOR_SCATTER_DELIVERY_MODE=off`,
  and its journal confirms both recorders started without a sender:
  `PSK pipelines active: ['server-raw'] (forward_to_pskreporter=False)` and
  `METEOR_SCATTER_DELIVERY_MODE=off — PSKReporter uploader disabled` (09:46Z today).
  wspr-recorder logs `env-validation skipped uploader (… WD_RECEIVER_GRID are required)`.
  `wspr-uploader.service` sits linked but inactive.
- B4's pskreporter key now shows one writer: 712 attempts in six hours (1.98 a minute, against
  3.26 on 10-08), each carrying 87 to 93 records at a 30-second rhythm. ND showed 1.97 a minute on
  10-08.
- ND, K3LR, UACNJ and W2NAF read the same way: one radiod (`SIGMOND_RADIOD_COUNT=1`), one instance
  per client, psk-recorder on `server-raw`, meteor-scatter `off`, no `WD_RECEIVER_GRID`.

**So no reachable station runs an in-process sender today.** Every one of them leaves the daemon
as the only sender on every key. Several of the map's traps (J6, J7, J16 and much of J4, J5 and J8)
describe stations that no longer exist in the reachable fleet. They still matter for an
unreachable or hand-configured station, so the recommendations below keep a guard for them.

Two more B4 facts bear on the questions:

- B4's store holds one queued retry right now, under `grape-psws`, at attempt 9. It belongs to one
  producer and to a key the split need not touch.
- B4's store holds 73 dead letters: 66 under `grape-psws` and 7 under `psk-pskreporter`. A dead
  letter carries only its pipeline's name. Once the split retires that name, those seven will name
  a pipeline that no longer exists.
- B4's store sits at schema version 1. Neither B4 nor ND logged a forward-only warning in the two
  days their journals still hold (since 10-17).

---

## Question 1. Does B4 still count as a host with several receivers?

### (a) The question in plain words

The design holds B4 back from one change because it describes B4 as "a host that runs several
psk-recorder instances" (design:1087). On such a host, one daemon pipeline posts every receiver's
spots under one callsign, and per-receiver pipelines would need a key form that names the instance.
Does B4 still need that special treatment, and does any station?

### (b) What the code and stations show

- B4 runs one radiod and one instance of each client: `radiod@AC0G-B4`, `psk-recorder@AC0G=B4`,
  `meteor-scatter@AC0G=B4`, `wspr-recorder@AC0G=B4`. Its `coordination.env` reads
  `SIGMOND_RADIOD_COUNT=1`.
- B4 already took the bring-up that the design said it should skip until step 4. Its psk-recorder
  now runs `server-raw`, which the design calls "the intended end state" (design:1087-1093).
- ND, K3LR, UACNJ and W2NAF each run one receiver too.
- The only host with two RX888s, B1 (`ops/fleet.toml:195`), runs radiod alone. Its consumers live on
  B2, your separate dual-RX888 build, which the image line does not cover.
- No code builds per-instance pipelines, and no key form names an instance (map J13). The design
  gives a key form for a producer (`sqlite:psk.spots@psk-recorder`, design:579) and none for an
  instance.

### (c) Options

1. **Treat B4, and the fleet, as single-receiver for v3.73.** Strike the B4 caution from design
   §10.3 item 4. Build nothing for several receivers. Cost: one doc edit. Risk: a multi-receiver
   station appears before step 4, and it behaves as it does today (one pipeline, one callsign).
2. **Keep the caution and reserve an instance slot in the key form now**, for example
   `sqlite:psk.spots@psk-recorder` today and `sqlite:psk.spots@psk-recorder/<instance>` later.
   Cost: a design note and a parser that tolerates the slot. Risk: little; the next migration has
   to rewrite keys anyway.
3. **Build per-instance pipelines in v3.73.** Cost: the renderer, the filter and the migration all
   grow; no station exists to test it. Risk: untested code on every station.

### (d) Recommendation

Option 1, with option 2's one sentence added to the design: B4 now runs one receiver per client and
took the bring-up the caution guarded against, so nothing in the fleet needs the special case.

### (e) If that proves wrong

A host with several receivers keeps today's behaviour: one daemon pipeline posts every receiver's
FT8 and FT4 spots under the station's one callsign. That loses nothing and doubles nothing. The cost
lands later, as one more key migration when per-instance pipelines arrive.

---

## Question 2. May the release change client repositories?

### (a) The question in plain words

Two parts of the key split could live in the client repositories. First, the shared
`psk-pskreporter` pipeline has to become two pipelines, one per producer. Today both psk-recorder and
meteor-scatter declare the shared pipeline word for word in their `deploy.toml`, and sigmond copies
it into the manifest. The split could live in those two files, or sigmond could rewrite the shared
block itself. Second, the clients' own in-process senders call hs-uploader with no producer. If
hs-uploader changes the key it forms for such callers, those senders jump to an empty key the next
time their recorder restarts.

The v3.70 plan forbade client changes (`sigmond/tasks/plan-sink-control-v370.md:30`). Does the key
split lift that rule?

### (b) What the code and stations show

- The shared block sits at `psk-recorder/deploy.toml:117-139` and `meteor-scatter/deploy.toml:131-153`
  (name at `:118` and `:144`). sigmond's `collect_pipelines`
  (`sigmond/lib/sigmond/uploader_manifest.py:204-247`) copies blocks as they stand and de-duplicates
  by name, keeping the first (`:235-246`).
- `SqliteSource.source_id()` returns `f"sqlite:{self.database}.{self.table}"`
  (`hsu/sources/sqlite.py:335-336`). It has no producer yet.
- v3.73 already changes client repositories for other reasons. Rows 3, 12 and 16 of
  `sigmond/tasks/release-v373.md` touch mag-recorder and meteor-scatter. So "no client changes" no
  longer holds for the release as a whole. The question narrows to the key split.
- No reachable station runs an in-process sender (see "What changed"). If hs-uploader keeps the old
  key form for callers that name no producer, the shims would keep the old key, and nothing on any
  reachable station would write it.
- Six client `uv.lock` files pin hs-uploader `0.1.0` editable (`plan-sink-control-v370.md:29`), so
  the shims run whatever hs-uploader the station checked out.

### (c) Options

1. **sigmond splits the block; no client changes.** `collect_pipelines` rewrites the shared
   `psk-pskreporter` into `psk-recorder-pskreporter` (FT8, FT4) and `meteor-scatter-pskreporter`
   (MSK144) between substitution and de-duplication (map H4, place 2). hs-uploader keeps the old
   key form for any source built without a producer, so the shims stay on the old key.
   Cost: sigmond carries a little client knowledge (two producer names, their mode lists) until
   step 2b moves declarations into `pipelines.d`. Risk: low. The split and the migration both live
   in sigmond and hs-uploader, which move together.
2. **Each client declares its own pipeline in `deploy.toml`.** Cost: two client commits, and the
   design's "the client answers for every route" fits better. Risk: map J14. If one client's
   checkout moves and the other's stays pinned, the renderer emits a new split pipeline beside the
   old shared one. They overlap on FT8 and FT4 (or on MSK144) toward one destination, and spots go
   out twice. A renderer check that refuses overlapping filters would contain it.
3. **Change the shims too**, so each in-process sender names its producer. Cost: changes in four
   client repositories for senders that run nowhere reachable and retire in step 4. Risk: each
   shim jumps to an empty key at its next restart and, with `start_at = "now"`, skips everything
   in between (map J7).

### (d) Recommendation

Option 1: it keeps the split and the migration in the two repositories that already move as one,
and it changes no sender that the fleet has stopped running.

### (e) If that proves wrong

If you later want the declarations in the clients, step 2b's `pipelines.d` already plans that move.
sigmond's rewrite then gets deleted. The cost amounts to one small throwaway function and its tests.
If a station somewhere still runs a shim, it keeps the old key, so D9's "frozen" old row keeps
moving there. That shim and the daemon then double-post; question 3's guard exists to catch it.

---

## Question 3. What carries D13's fact that a sender runs, and does the skip apply per destination?

### (a) The question in plain words

D13 says: while a client's own in-process sender still runs, the daemon's pipelines skip that
producer's rows, so exactly one sender covers each row (design:106). Two things stay undecided.
First, nothing tells the daemon that a client's sender runs; someone has to choose the signal.
Second, read word for word, D13 would also drop that producer's rows from destinations the
in-process sender never serves.

### (b) What the code and stations show

- The daemon reads its manifest once, at start (`hsu/daemon.py:150-160`). It reads no other unit's
  state, no client's env file and no marker (map G3).
- Each client decides in its own way whether its sender runs:
  - psk-recorder: `direct` in `PSK_DELIVERY_PIPELINES`, else legacy `PSK_DELIVERY_MODE`, else the
    default `server-merge` (`psk-recorder/src/psk_recorder/core/recorder.py:194-262`).
  - meteor-scatter: `METEOR_SCATTER_DELIVERY_MODE`, with `direct` as the default when unset
    (`meteor-scatter/src/meteor_scatter/core/recorder.py:627-660`).
  - wspr-recorder: `WSPR_USE_HS_UPLOADER` on, plus a call, plus `WD_RECEIVER_GRID`, plus
    `WD_SFTP_SERVERS` for the tar (map E1).
  sigmond reads none of `METEOR_SCATTER_DELIVERY_MODE` or `WD_RECEIVER_GRID` today (map G3).
- `smd config upload … --on|--off|--via` edits the env file and prints "restart to apply". It
  re-renders nothing (`sigmond/lib/sigmond/commands/config.py:1315-1393`).
- Each in-process sender serves specific destinations. psk-recorder's and meteor-scatter's serve
  pskreporter alone. The same psk rows also ride wspr-recorder's tar to wsprdaemon
  (`hsu/sources/wspr_cycle.py:347-367`, `include_psk = true`). wspr-recorder's shim can run its
  wsprnet pipeline without its tar pipeline.
- No reachable station runs any in-process sender today.

### (c) Options

1. **sigmond decides at render time, per producer and destination.** One sigmond function reads
   each client's per-instance env files and applies that client's own rule. When it finds a
   sender armed, the renderer leaves out only the daemon pipeline for that producer and that
   destination. A harmonize rule runs the same function and warns when the answer differs from
   what the manifest holds (an env edited since the last render). Cost: one function that repeats
   three clients' rules, one renderer branch, one harmonize rule. Risk: the answer ages between
   renders; the harmonize warning makes the drift visible.
2. **The daemon decides at run time.** It reads the env files or asks `systemctl is-active` on each
   pump. Cost: the daemon's account needs read access it lacks, and hs-uploader would carry client
   rules. Risk: an active unit can still run no sender (flag off, grid missing), so `is-active`
   alone answers wrongly.
3. **Retire D13's skip; enforce one sender instead.** The manifest step, or harmonize, refuses or
   alarms when it finds an armed in-process sender and tells the operator the one-line fix
   (`smd config upload psk-recorder <id> --via server-raw`, `… meteor-scatter <id> --via off`).
   Cost: the same detector as option 1, without the renderer branch. Risk: the station posts twice
   until someone acts on the alarm.

### (d) Recommendation

Option 1, scoped per producer and destination, with one rule for doubt: when the detector cannot
tell, render the daemon pipeline. The fleet already runs no in-process sender, so the skip will
almost never fire. A double post costs less than a silent loss (D9's own ordering: "re-send, never
skip").

### (e) If that proves wrong

If the detector misses a running sender, that producer's spots go to pskreporter twice until
someone re-renders. If it wrongly finds one, the daemon skips rows that nobody else sends, and they
age out of the sink (60 minutes for psk rows, `sigmond/lib/sigmond/storage_trim.py:94-99`). The
doubt rule above keeps the second case rare. Getting the destination scope wrong costs more: a
skip applied to every destination would drop psk-recorder's rows from the wsprdaemon tar.

---

## Question 4. May the manifest step stop the daemon before it migrates, and may the daemon check the store's version at start?

### (a) The question in plain words

The key split needs a one-time copy: each old send record gets copied to its new keys. Today the
manifest step (`smd admin uploader manifest`) writes the new manifest, runs `hs-uploader migrate`,
and only then restarts the daemon. Two traps follow.

- **J1.** The old daemon keeps sending, and keeps advancing the old keys, while the copy runs and
  until the restart. The new daemon starts from a copy taken seconds or minutes earlier and sends
  that span again.
- **J2.** If the migrate fails, the new manifest already sits on disk. Any later restart (the unit's
  `Restart=always`, bring-up, `smd apply`, a reboot) starts the daemon on the new manifest against an
  unmigrated store. A new key with `start_at = "now"` then jumps to the newest row and skips
  everything in between.

### (b) What the code shows

- The step writes the manifest (`sigmond/lib/sigmond/commands/uploader.py:272`), migrates
  (`:290-294`), then enables and restarts (`:307-310`). On a failed migrate it returns 1 and
  restarts nothing, but the new file stays.
- The unit sets `Restart=always` (`hs-uploader/systemd/hs-uploader.service:33`). A manual
  `systemctl stop` does not trigger that restart.
- An empty key with `start_at = "now"` starts at `MAX(id)` of the whole table
  (`hsu/sources/sqlite.py:364-372, 705-725`).
- `hs-uploader migrate` takes a `BEGIN IMMEDIATE` lock and runs each migration once
  (`hsu/watermark/schema.py:147-185`). On a store of B4's size it finishes in well under a second.
- `_run_migrate` returns 0 when the installed hs-uploader predates `migrate` (map J3,
  `commands/uploader.py:143-187`). sigmond already probes the daemon's venv for another capability,
  `hs_uploader_supports_discard` (`uploader_manifest.py:370-384`).
- D10 says the daemon never migrates while it starts (design:104).

### (c) Options

For J1:

1. **Stop, migrate, start.** The step stops the daemon, migrates, then starts it. Cost: the daemon
   pauses for a second or two; rows wait in the sink and nothing gets lost. Risk: if the stop lands
   between about 02:55 and 03:10 UTC, `mag-recorder-upload.service` sees the daemon inactive and
   ships the day's zip itself (`mag-recorder/systemd/mag-recorder-upload.service:38`). That sends
   nothing twice; the zip goes by the other road.
2. **Leave it running and accept the re-send.** Cost: none in code. Risk: about 90 psk rows per
   30-second pump go out twice on a busy station, and more if the restart comes minutes later.

For J2:

1. **Land the manifest only after the migrate succeeds.** The step renders to a side file, stops the
   daemon, migrates, renames the new manifest into place, and starts. A failed migrate leaves the
   old manifest, so every later restart starts a consistent pair: old manifest, old keys.
2. **The daemon reads the store's version at start and refuses** the pipelines whose key form needs
   a newer store. It keeps the rest running, the heartbeat included, and the heartbeat reports the
   refusal. Cost: a version number in the manifest or in the code, plus a check at start. Risk: the
   refused pipelines send nothing until someone migrates; psk rows older than 60 minutes age out.
3. **The daemon falls back to the old keys.** Cost: high. Once the manifest has split
   `psk-pskreporter` into two pipelines, both fall back to one old key, and the map's test (J3)
   showed one of them then ships nothing. Fall-back without the old pipeline set loses rows.

### (d) Recommendation

Yes to both, in this form: stop the daemon, migrate, then land the manifest and start (J1 option 1
with J2 option 1), and give the daemon the start-time check that refuses only the pipelines that
need a newer store (J2 option 2) as a backstop. Also add a capability probe so that sigmond renders
the split only when the installed hs-uploader can form the new keys. Reason: ordering the steps
removes both traps for the ordinary path, and the daemon's check catches the paths sigmond does not
control (a hand checkout, a reboot mid-update).

### (e) If that proves wrong

If stopping the daemon proves unwelcome, the cost amounts to a pause of a second or two, plus the
rare mag zip shipped by its fallback unit. If the start-time refusal fires in a case nobody
foresaw, the affected pipelines send nothing until an operator runs the manifest step again; the
heartbeat keeps running and reports it. Psk rows waiting longer than 60 minutes would age out.

---

## Question 5. Where does a queued retry that mixes two producers go?

### (a) The question in plain words

When a send fails with "try later", hs-uploader stores the whole batch as a queued retry, tagged
with its pipeline's name and its key. A retry queued under today's shared `psk-pskreporter` can hold
FT8, FT4 and MSK144 spots in one payload. After the split, those rows belong to two pipelines. The
store cannot split the payload. Which new pipeline gets it?

### (b) What the code and stations show

- A pskreporter retry stores an encoded payload with the receiver's identity in front
  (`hsu/transports/pskreporter.py:153-177`). The store sees bytes, not rows.
- A retry left under a retired pipeline name never ships and never leaves; the heartbeat then
  reports it as retrying forever (map B5, TESTED).
- While a retry waits, `_pump_one` refuses to read new rows for that pipeline
  (`hsu/core.py:326-336`). So the key's stored position sits at or before the start of the queued
  batch: the failed batch never advanced it.
- Each new batch reads its starting position from the store (`hsu/core.py:407`), not from memory.
- A retry queued by a daemon older than v3.70 carries no key at all (map B3).
- B4 holds one queued retry today, under `grape-psws`, which the split need not touch. Neither
  snapshot, and neither station today, holds a queued psk retry.
- B4 holds seven dead letters under `psk-pskreporter`. Dead letters never replay. hs-uploader now
  has `purge-dead-letters` (3090b91) to clear them.

### (c) Options

1. **Dissolve it.** The migration deletes the mixed retry and copies the old key's position, which
   sits before the retry's rows, to both new keys. Each new pipeline then reads its own rows of that
   range from the sink and sends them once. Cost: a few lines in the migration. Risk: rows the sink
   has already trimmed (psk rows older than 60 minutes) never go. That needs an outage longer than an
   hour, and pskreporter values hour-old spots little.
2. **Re-home it whole to `psk-recorder-pskreporter`**, the pipeline that holds most of its rows.
   Cost: a rewrite of the retry's name and key. Risk: when it acks, it moves psk-recorder's key past
   the range; meteor-scatter's pipeline still sends that range's MSK144 rows again. So MSK144 goes out
   twice, and only for one batch.
3. **Make the migration wait or refuse** while a `psk-pskreporter` retry sits queued. Cost: the
   update stalls for as long as pskreporter refuses. Risk: an update that cannot finish during a
   pskreporter outage.

### (d) Recommendation

Option 1, with option 2 as the fallback when the retry's first row no longer exists in the sink:
dissolving sends every row once, and it relies only on facts the code already guarantees (the key
stops before a queued batch; each batch re-reads the key). Apply the same treatment to a keyless
pre-v3.70 retry under that name. Leave retries on keys the split does not touch, such as B4's GRAPE
retry, exactly where they stand. Leave the dead letters, and let an operator clear them with
`purge-dead-letters`.

### (e) If that proves wrong

With dissolve, the worst case drops one batch (at most 500 spots, the pipeline's `batch_limit`) after
an outage of more than an hour. With the fallback, the worst case sends one batch of MSK144 spots
twice. Neither touches any other key. No station holds such a retry today, so a test with made-up
rows will settle the behaviour before any station meets it.

---

## Question 6. Does the forward-only rule refuse an equal write, and what does a failed comparison do?

### (a) The question in plain words

From v3.73 the store should refuse to move a send record backward. Two edge cases need a ruling.
First, a write that leaves the record exactly where it stands: refuse it or allow it? Second, when
the comparison itself fails (the stored value cannot be read or compared), should the store write
anyway or refuse?

### (b) What the code shows

- `advance_cursor_checked` (`hsu/watermark/sqlite.py:202-256`) compares, warns, and writes whatever
  the answer. Its text says "v3.70 warns only" (`:216, 306, 322`).
- Equal counts as "not forward" today, so an equal write warns. If enforcement refuses it, the
  write that refreshes `last_ack` also stops, so the record looks stale while the pipeline works
  (map D3).
- A comparator that raises counts as forward today (`:241-248`). The sources themselves already
  answer "forward" for a value they cannot decode, and log it at DEBUG (`hsu/sources/sqlite.py`,
  `hsu/sources/wspr_cycle.py:194-211`). So a raise means something unexpected, usually a bad stored
  value.
- Each batch reads its start from the store (`hsu/core.py:407`). So refusing a backward write
  causes no loop: the next batch starts from the further-ahead position.
- The read and the write share no SQLite transaction (map D3). The lock works only inside one
  process.
- The callers ignore the answer and commit to the source and log "acked" regardless
  (`hsu/core.py:465-494, 547`).
- B4 and ND logged no forward-only warning in the two days their journals hold. The design notes
  that warnings measure overlap in time, not a shared key (map D5).

### (c) Options

For an equal write:

1. **Allow it as a refresh.** Keep the record, refresh `last_ack`, log nothing. Cost: a branch.
   Risk: none known; an equal value moves nothing backward.
2. **Refuse it.** Cost: none. Risk: `last_ack` freezes on a working pipeline, and the heartbeat
   carries that stale time to the fleetboard.

For a failed comparison:

1. **Write and warn (today's choice).** Risk: a genuine backward write slips through once. The new
   value comes from a source that just produced it, so it decodes; the bad value sits in the store,
   and writing repairs it.
2. **Refuse and warn.** Risk: if the stored value stays unreadable, every later batch starts from a
   value that cannot be decoded, and the pipeline can stall or re-send without end.

### (d) Recommendation

Allow an equal write as a refresh of `last_ack`, refuse only a write that truly moves backward, and
on a failed comparison write and warn as today. Reason: the store's stored value, not the new one,
falls under suspicion when a comparison fails, and refusing an equal write would make healthy
pipelines look stale. Two smaller rulings travel with this: run the read and the write inside one
`BEGIN IMMEDIATE` transaction so a second process cannot slip between them, and let callers log a
refused write as acked-but-held rather than plain acked.

### (e) If that proves wrong

If an equal write should have counted as a warning sign, the cost amounts to one missed early hint
of two writers on one key; the separate keys of the split already remove that case. If writing on a
failed comparison lets one backward write through, the record moves back once and that range goes
out again, within the sink's retention. That matches D9's "re-send, never skip".

---

## The six rulings in one table

| # | Question | Recommendation |
|---|---|---|
| 1 | B4 several receivers? | No. B4 and every reachable station run one receiver per client; strike the B4 caution; leave room for an instance in the key form, build nothing for it. |
| 2 | Change client repos? | Not for the split. sigmond rewrites the shared pskreporter block; hs-uploader keeps the old key for callers that name no producer. |
| 3 | D13's signal and scope | sigmond decides at render time, per producer and destination, from the clients' env files; harmonize warns on drift; when unsure, render the daemon pipeline. |
| 4 | Stop before migrate; check at start? | Yes and yes. Stop, migrate, then land the manifest and start; the daemon refuses only pipelines that need a newer store; sigmond probes hs-uploader before rendering the split. |
| 5 | Mixed queued retry | Dissolve it: copy the old position to both keys and let each pipeline re-read its own rows; re-home whole to psk-recorder's pipeline if the rows have aged out. |
| 6 | Equal write; failed compare | Allow equal as a `last_ack` refresh; refuse only a true backward write; on a failed comparison write and warn; read and write in one transaction. |

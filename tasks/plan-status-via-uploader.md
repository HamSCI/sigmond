# Status reporting through the uploader, into ClickHouse

**Status:** DESIGN AGREED 2026-10-26.  **Phase 2 BUILT 2026-10-26**
(hs-uploader `905ef13`): the persistent ssh connection is in
`WsprdaemonTarSftp`, with its tmpfiles.d entry and 16 tests, all eight guards
mutation-checked.  It is NOT yet soaked — the 48 h single-station soak below
is the remaining Phase 2 item, and nothing else has started.  Supersedes the stcp/RAC
carrier agreed 2026-10-25 (see "Why not the RAC tunnel" — the record of that
decision is kept deliberately).  Carrier: the station's EXISTING wsprdaemon tar
upload to gw1/gw2, over a PERSISTENT ssh connection.  Receiver:
`wsprdaemon_reflector.py`, unchanged.

**Governing constraint (rob, 2026-10-26):** limit the cost of moving data
between a client and the server.  Stated first as "limit SFTP transactions per
minute", then sharpened: hold ONE ssh connection up and run every transfer —
spots and status alike — over it, rather than paying setup and teardown per
file.  Both forms are honoured below, and they attack different costs:

| lever | what it reduces | by how much |
|---|---|---|
| persistent connection (`ControlPersist`) | cost PER transfer | ~1,296 ssh handshakes/day/station -> ~10 |
| one envelope file carrying many envelopes | number of FILES at the gateway | 1 tar per delivery regardless of production rate |

The second matters because once the connection is persistent, the binding cost
is no longer ssh — it is the reflector's per-file work (a forked `tar tf` plus
three rsync fan-outs for every tar that lands).

## Motivation

A station's heartbeat goes to wd30 today by a private path: one shared chrooted
SFTP account, a sqlite database, and a board bound to a LAN address.  That path
is separate from everything else the fleet ships, and it terminates on one host.

Moving it onto the upload path puts status where spots and FTx data already
land — on all three WD servers, in ClickHouse, queryable beside everything else
a site produces — and does it with machinery that already runs.

## What exists today (measured, not assumed)

Two independent producers, both shipping to wd30:

| producer | assembles | cadence | ships |
|---|---|---|---|
| station VM | `smd admin heartbeat emit` -> `/var/lib/sigmond/heartbeat/*.json` | `sigmond-heartbeat.timer`, `OnCalendar=*:0/5` (300 s) | hs-uploader `FileTreeSource` + `HeartbeatSftp`, one file per record |
| Proxmox host | `scripts/proxmox/pm-heartbeat.py` (stdlib only, no hs-uploader) | `DEFAULT_INTERVAL_SEC = 300` | its own `ship_spool()`, the whole spool in ONE sftp batch |

Both land in `/srv/hamsci-hb/incoming` on wd30 as the shared account
`hamsci-hb`; `hamsci-hb-ingest.timer` runs `server/heartbeat/ingest.py` into
`/var/lib/hamsci-fleetboard/heartbeats.db`; `fleetboard.py` serves
`192.168.1.164:8090`.

The upload path already does everything this work needs:

- **The station already builds the right tar.**
  `hs-uploader/src/hs_uploader/transports/wsprdaemon.py` assembles the Phase 2
  layout — peer subdirs at the tar root (`wspr/`, `ft8/`, `ft4/`,
  `routing.json`) — and ships it by SFTP to gw1/gw2.
- **One session per shipment.**  `wsprdaemon.py:988` loops the servers and
  returns on the first success: gw1 normally, gw2 only on failure, never both.
  A second `sftp` invocation happens only on host-key recovery.
- **Riders are an established pattern.**  `WsprCycleSource.include_psk` sweeps
  the cycle's FT8/FT4 rows into the WSPR tar — "no separate psk pipeline needed
  when True" (`sources/wspr_cycle.py:139`).  Per-rider gating already exists in
  `_judge_riders`.
- **The reflector never opens a tar.**  `wsprdaemon_reflector.py` globs
  `/home/*/uploads/*.tbz`, validates with `tar tf`, hard-links into
  per-destination queues and `rsync -a --remove-source-files` to wd10/20/30.
- **The server parses peer subdirs.**  `ft8/`, `ft4/`, `msk144/` were added
  exactly this way in Phase 2 PR 1: a list in `config['psk_modes']`, a
  `process_mode_files()` call in `extract_and_parse_tbz()`, gated by
  `WSPRDAEMON_INGEST_PSK=1`.
- **92 upload accounts already exist on gw1**, including `AC0G_B4_PROXMOX`,
  `AC0G_ND`, `AC0G_B4` and `DASI002` — stations and Proxmox hosts alike.
  Nothing needs provisioning.

## Design

Status becomes a peer subdir in a tar, carried by the upload machinery the
station already runs, over one ssh connection the station holds up.

```
station VM --> sink.db --> wspr tar pipeline  --+
                       \                        |   ONE PERSISTENT ssh connection
                        -> status pipeline  ----+   (ControlMaster; one channel
                                                |    per transfer, no handshake)
Proxmox host ----------> own tar ---------------+
                                                v
                                        gw1  (gw2 on failure)
                                                |
                     reflector: UNCHANGED       |
                  (glob, tar tf, hard-link, rsync -a)
                  +-------------+-------------+
                  v             v             v
                wd10          wd20          wd30
                     wsprdaemon_server.py: new status/ parser
                             -> station.heartbeat (ClickHouse)
                fleetboard   fleetboard   fleetboard
                +---- rac-dashboard "Fleet" tab (gw2) ----+
```

### One persistent ssh connection, not one per transfer

Every `sftp` the station runs today is a full TCP connect + key exchange + auth
+ teardown.  `WsprdaemonTarSftp._run_sftp` already builds an `sftp -b - ... -o`
argv, so the change is three more options on it:

```
-o ControlMaster=auto
-o ControlPath=/run/hs-uploader/cm-%r@%h:%p
-o ControlPersist=10m
```

The first transfer opens the connection; every later transfer is a new CHANNEL
on it, with no handshake.  The process model is untouched — still one `sftp`
invocation per shipment, still one `Outcome` per batch, so retries, watermarks
and backlog figures all behave exactly as they do now.

Measured on GW1 (2026-10-26), the gateway allows this and benefits from it:

```
#MaxSessions 10          default 10 channels per connection — we need 1-2
#MaxStartups 10:30:100   throttles concurrent UNAUTHENTICATED connections
Match Group sftponly     ChrootDirectory /home/%u, ForceCommand internal-sftp
```

`MaxStartups` is the throttle that 92 stations' handshake churn presses
against; this removes nearly all of it.  `ForceCommand internal-sftp` is applied
per channel, so multiplexing is unaffected by the chroot.

| | today | after |
|---|---|---|
| ssh handshakes/day/station | ~1,296 | ~10 (master reconnects only) |
| fleet-wide handshakes/day | ~119,000 | ~900 |

**This is a cost win, not a latency win.**  It removes ~0.2-0.5 s of handshake
per transfer, while the reflector holds every arriving file for
`min_age_seconds: 10` on a `scan_interval: 5` loop before forwarding.  That
10-15 s floor dominates end-to-end latency and this does not touch it.

Four things to get right, none of them large:

- **`ControlPath` per (user, host, port)** via `%r@%h:%p`, or a gw1 master gets
  reused for gw2 and failover silently targets the wrong gateway.
- **Keep the path short.**  A unix socket path is capped near 104 bytes;
  `/run/hs-uploader/` rather than a deep state dir.  It needs a tmpfiles.d
  entry beside the existing one for `/var/lib/hs-uploader`.
- **A hung master blocks transfers.**  Bound it: `ServerAliveInterval=30`,
  `ServerAliveCountMax=3`, and the finite `ControlPersist` above.  A dead
  master makes `ControlMaster=auto` fall back to a fresh connection; a *hung*
  one is the case worth the keepalives.
- **A master that dies mid-transfer** fails that transfer, which returns
  `retry_later` — already handled, no new path.

### What that buys: status gets its own pipeline

The 2026-10-25 draft of this plan had status ride INSIDE the WSPR cycle tar, as
psk rows already do (`WsprCycleSource.include_psk`, `sources/wspr_cycle.py:139`).
That was justified entirely by session scarcity.  A persistent connection ends
the scarcity, and the rider's two real costs remain:

1. **A deadline change inside `WsprCycleSource`** — it would have to yield a
   batch when status is pending and the cycle carried no spots, because a
   station with a dead decoder is exactly the one whose heartbeat matters.
2. **Entanglement with the sink gate.**  `SinkGate` holds a whole pipeline when
   a producer's switch reads `fill` or `off`.  Status riding that pipeline
   would be held with it — so turning a station's data flow down would also
   silence its status, which is the opposite of what an operator wants.

A separate status pipeline has neither.  It needs no change to
`WsprCycleSource`, it names no client so `gate.exempt_rule` already exempts it,
its cadence is independent of WSPR cycles, and it behaves identically on a
station that runs no WSPR at all.  One source, one transport, one watermark
slot — the model hs-uploader is built around.

**The rider stays documented as the fallback.**  If Phase 4 shows the gateway's
per-file cost is binding, folding status back into the cycle tar reclaims every
status file at the cost of items 1 and 2 above.

### The real cap is the reflector, not the connection

Each tar that lands costs the gateway a forked `tar tf`, a hard-link per
destination and three rsync deliveries.  That cost is per FILE and is unmoved by
a persistent connection, so it — not ssh — sets how often status may ship:

| status delivery cadence | status tars/s fleet-wide | vs today's WSPR tar rate (~0.77/s) |
|---|---|---|
| 300 s | 0.31 | +40 % |
| 120 s | 0.77 | +100 % |
| 15 s | 6.1 | +790 % |

**Default: 120 s delivery.**  Detection within ~2.5 minutes, roughly doubling
the reflector's file rate — measured in Phase 5 before it is trusted.  15 s
delivery is off the table on this evidence; 15 s *production* is addressed
below.

### Production cadence is NOT delivery cadence

Envelopes accumulate in the sink at the producer's cadence and ship in one tar
at the pipeline's cadence.  Two envelopes in one `.jsonl` cost two lines, not
two files — and the reflector's per-file cost is the one that binds.
`emitted_at` on each line reconstructs the fine timeline from a coarse
delivery, so a coarse delivery loses resolution of nothing.

**Production cadence: 60 s, down from 300 s.  Do NOT go to 15 s.**  The envelope
is not a ping — it is a diagnostic sweep (`versions manifest timing gaps uploads
doctor resources`) that shells out to build.  Running it every 15 s on a station
VM runs against the project's standing rule that heavy diagnostics do not run on
station VMs, for a resolution nobody reads.  60 s gives a 5x finer record than
today at 1/4 the envelope cost of 15 s.

So the two cadences are set by two different limits, and neither is ssh:
**production at 60 s** by what it costs the station VM to measure itself,
**delivery at 120 s** by what it costs the gateway to handle a file.

Delivery latency floor, for honesty about what the cadence buys: the reflector
holds every arriving file for `min_age_seconds: 10` on a `scan_interval: 5`
loop, so there is a 10-15 s gateway floor no matter what the station does.
End to end at the defaults: up to 60 s in the sink + up to 120 s to delivery +
~15 s at the gateway, so a fault is visible fleet-wide inside ~3 minutes
against ~5 minutes today.

### Tar layout

```
<tar root>/
  status/<ROLE>/<HOST_ID>/<YYYYMMDDTHHMMSSZ>.jsonl    one envelope per line
                                                      ROLE is 'vm' or 'pm'
```

The envelope on each line is unchanged — `lib/sigmond/heartbeat_schema.py` stays
the wire contract, `kind = "station_heartbeat"`, `schema_version = 1`, blocks
`versions manifest timing gaps uploads doctor resources` plus `rac` for a host.
`validate()` already allows additive fields at every depth, so a station one
release ahead of a server is never rejected.

### `received_at` is the tar's mtime, and that is load-bearing

`ingest.py`'s one idea must survive this move verbatim: availability comes from
ARRIVAL, never from anything a station says about itself, because a station that
has stopped cannot report that it stopped.

The client writes the tar into `uploads/` on the gateway; the reflector
hard-links it (mtime preserved) and `rsync -a` preserves mtime to every WD
server.  **So the tar's mtime at wd10/20/30 is the instant the station finished
delivering at the gateway** — one server clock, the same number on all three
boards, with no new code and nothing stamped by the station.

`emitted_at` is stored for forensics and is never used for availability.

This also disposes of the objection that in-band status dies with the uploader.
It does — and that is correct.  Silence IS the signal; the board derives
liveness from arrival, so a wedged uploader reads as a down station, which is
what it is.

### Why not the RAC tunnel (the 2026-10-25 design, superseded)

The previous design gave each station an frp `stcp` visitor to a provider frpc
on the gateway, so status rode the RAC session to the gateway's own sshd.  It
was dropped on 2026-10-26 for three reasons, in order of weight:

1. **It built a second delivery channel beside one that already runs.**  A
   provider frpc per gateway, a `[[visitors]]` block in 92 station configs, a
   loopback bindPort per gateway, and a new transport — to reach the same sshd,
   with the same account, that the upload already reaches.
2. **It cost an extra ssh session per station.**  This was the decisive
   argument on 2026-10-25 and is now the weakest of the three: a persistent
   connection makes sessions cheap.  Recorded honestly so that nobody later
   revives the stcp design believing this objection still carries weight —
   reasons 1 and 3 are what stand.
3. **It left RAC 200-299 with no path at all.**  Those stations route RAC to
   vpn1/vpn2 only (`_rac_gateways_for()` in `bin/smd`), and probing on
   2026-10-25 showed neither accepts root for any key we hold — they are
   HamSCI-operated.  The upload path has no such gap: those stations upload to
   gw1/gw2 like everyone else.

**What this gives up, stated plainly:** status no longer proves the RAC channel
is alive, which was the original reason for tying them (rob, 2026-10-25:
monitoring and control both go through RAC).  The substitute is better than the
original: the envelope already carries a `rac` block, so RAC health arrives as a
*reported verdict* rather than as an inference from silence.  "RAC is down"
becomes a message, with a reason, instead of an absence.

### ClickHouse

`schema/station.heartbeat.sql` in wsprdaemon-server, house style
(ReplacingMergeTree, ZSTD(3), Delta codecs, provenance header), mirroring
`psk.spots.sql`:

```sql
CREATE TABLE IF NOT EXISTS station.heartbeat
(
    `received_at`    DateTime               CODEC(Delta(4), ZSTD(3)),  -- tar mtime = gateway arrival
    `ingested_at`    DateTime DEFAULT now() CODEC(Delta(4), ZSTD(3)),
    `station`        LowCardinality(String) CODEC(ZSTD(3)),
    `host_id`        LowCardinality(String) CODEC(ZSTD(3)),
    `role`           LowCardinality(String) CODEC(ZSTD(3)),  -- 'vm' | 'pm'
    `gateway`        LowCardinality(String) CODEC(ZSTD(3)),  -- from the from-gwN spool dir
    `callsign`       LowCardinality(String) CODEC(ZSTD(3)),
    `grid`           LowCardinality(String) CODEC(ZSTD(3)),
    `emitted_at`     DateTime               CODEC(Delta(4), ZSTD(3)),  -- FORENSICS ONLY
    `interval_sec`   UInt16                 CODEC(ZSTD(3)),
    `schema_version` UInt16                 CODEC(ZSTD(3)),
    `rollup_verdict` LowCardinality(String) CODEC(ZSTD(3)),
    `rollup_block`   LowCardinality(String) CODEC(ZSTD(3)),
    `rollup_reason`  String                 CODEC(ZSTD(3)),
    `verdict`        Map(LowCardinality(String), LowCardinality(String)),
    `reason`         Map(LowCardinality(String), String),
    `envelope_sha`   FixedString(64)        CODEC(ZSTD(3)),
    `payload`        String                 CODEC(ZSTD(3))   -- the envelope, verbatim
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(received_at)
ORDER BY (station, emitted_at, envelope_sha)
TTL received_at + INTERVAL 13 MONTH;
```

Choices worth their rationale:

- **`verdict` / `reason` as `Map`, not columns.**  Adding a block is a schema
  change in `heartbeat_schema.py`; it must not also be a ClickHouse migration,
  or the fleet loses visibility of exactly the hosts updated most recently.
- **`payload` verbatim.**  The rule that a block which vanished can never read
  as healthy only holds if the original is recoverable.  The derived columns are
  a convenience; the envelope is the evidence.
- **`ORDER BY (station, emitted_at, envelope_sha)` collapses duplicates.**  A
  retry that lands after a partial success yields two rows with the same key;
  ReplacingMergeTree keeps one.  Their `received_at` differ by the delivery gap,
  so the surviving value is whichever merged last — a difference of seconds,
  stated here so nobody later reads it as exact.
- **Volume at the defaults:** the VM produces every 60 s (1,440/day) and the PM
  every 300 s (288/day), so ~1,728/day/station, ~159 k rows/day across 92
  stations — about 3x today's ~53 k.  At 15 s VM production it would be ~556 k;
  another reason the cadence stays at 60.

## Non-goals

- **Signed envelopes.**  `ssh-keygen -Y sign` against the TOFU registry would
  turn "the server trusts whatever the envelope claims" into real attribution,
  and the key material already exists.  NOT in this work: delivery requires the
  station's own gateway account, so attribution is no worse than the spot path
  it joins.  Worth doing after.
- **A server-side pull collector** for hosts that emit no heartbeat (plain
  wsprdaemon stations, Proxmox hosts outside the sigmond fleet).
- **Multiple tars in one `sftp` invocation.**  `_sftp_batch_cmd()` puts one tar
  per invocation.  `ControlPersist` already removes the cost that batching
  would recover (the handshake), so batching buys only a process fork.  It
  stays available if ever wanted.
- **A shared connection between the VM and the PM.**  They are different hosts
  with different accounts; each holds its own master.
- **Retiring wd30's board before the dual-ship week passes.**
- **Changing the heartbeat envelope.**  Not one field.

## Traps

1. **`"delete_patterns": ["AI6VN_2*", "*.png"]`** in
   `/etc/wsprdaemon/reflector_destinations.json` on BOTH gateways — broader than
   the repo default `AI6VN_25*`.  Any upload whose name starts `AI6VN_2` is
   deleted before forwarding.  Status tars carry the ordinary station name, so
   this would silently delete AI6VN's status as well as its spots.  **Decide
   whether that pattern is still wanted before any test traffic.**
2. **The sink gate must never hold status.**  `SinkGate` holds a whole pipeline
   when a producer's switch reads `fill` or `off`.  A station must not stop
   reporting because an operator turned its data flow down — that is the moment
   status matters most.  A separate pipeline naming no client is exempt by
   `gate.exempt_rule` today, so this is satisfied BY CONSTRUCTION rather than
   by new code — but it is satisfied only as long as status stays its own
   pipeline.  **Verify it with a test, and re-read this trap before ever
   folding status back into the cycle tar.**
3. **`rac-dashboard` is not under version control.**
   `/home/wsprdaemon/rac-dashboard/server.py` on gw2, 38 KB, with ten hand-made
   `server.py.bak-*` copies, newest edited 2026-10-24 21:54Z.  Put it in a repo
   before adding the Fleet tab.
4. **The PM has no sink and no hs-uploader.**  `pm-heartbeat.py` is
   stdlib-only and runs on the Proxmox host, which carries neither `sink.db`
   nor hs-uploader.  It keeps its own spool, its own tar and its own master
   connection; none of the pipeline machinery above applies to it.
5. **`ControlPath` collision across gateways.**  Without `%h` in the path, the
   master opened to gw1 is reused when the transport fails over to gw2, and
   the failover silently delivers to the wrong gateway.  The `%r@%h:%p` form
   in the design is load-bearing, not decoration.

## Phases

### Phase 1 — the sink, proven without touching a station

- [ ] `schema/station.heartbeat.sql` in wsprdaemon-server.
- [ ] `process_status_files()` in `wsprdaemon_server.py`, beside
      `process_mode_files()`; `config['status_subdir']` (default `status`);
      gated `--ingest-status` / `WSPRDAEMON_INGEST_STATUS=1`, exactly as psk
      ingestion is gated.
- [ ] `gateway` derived from the incoming dir (`from-gw1` / `from-gw2`);
      `received_at` from the tar's mtime; `role` from the TAR PATH, never
      inferred from which blocks are present.  Inferring "pm" from the `rac`
      block would misfile every Proxmox host that carries no switch script —
      `pm-heartbeat.py:191` leaves `rac` out on exactly those hosts, so the
      inference fails for the hosts least likely to be noticed.
- [ ] `tests/test_status_ingest.py` against synthetic extracted trees, no
      ClickHouse required — the shape `tests/test_psk_ingest.py` already uses.
- [ ] Replay wd30's existing `heartbeats.db` through the parser.  Every envelope
      the sqlite board holds must land, with the same verdicts.

### Phase 2 — the persistent connection, on its own

Done first and separately, because it changes every existing upload and must be
provable in isolation: a regression here breaks spots, not just status.

- [x] `ControlMaster`/`ControlPath`/`ControlPersist` + keepalives on the argv
      in `WsprdaemonTarSftp._run_sftp`.  `tmpfiles.d` entry for
      `/run/hs-uploader`.  Opt-out env var (`HS_UPLOADER_SSH_MULTIPLEX=0`) for
      a host where it misbehaves, plus `HS_UPLOADER_SSH_CONTROL_DIR`.
- [x] Test that the `ControlPath` differs per server — mutate `%h` out of it
      and the test must fail (trap 5).  Done, and the mutation is run: the
      first version of that test asserted on the stored attribute rather than
      the emitted argv and did NOT fail when `%h` was dropped from the
      command.  Both are asserted now.
- [ ] Soak on ONE station for 48 h: spot delivery unchanged, handshake count
      down, no stuck master across a deliberate network drop.  **NOT DONE —
      this is the gate on Phase 2, and nothing downstream should assume it
      passed.**

Three decisions made while building it, none of them in the design above:

- **`ssh` refuses an over-long `ControlPath` rather than falling back.**  So
  the expansion is MEASURED against this transport's own `servers` and user
  and declined if it will not fit, rather than assumed to fit under a short
  directory.  Falling back to plain connections is always correct; emitting a
  path ssh will reject would break every upload on that host.
- **An unusable control directory is not an error.**  Opt-out, a directory
  that cannot be created, and a directory that exists but is not writable (the
  ownership drift the tmpfiles.d `z` line repairs) all fall back to today's
  behaviour.  A transport that refused to ship because it could not open a
  socket would trade a cost win for an outage.
- **A host-key change tears the master down and retries without it.**  Not in
  the design, and load-bearing: reusing a master authenticated against the key
  we have just decided is stale would carry the tar over the connection whose
  identity is in question, and would make clearing `known_hosts` mean nothing.

### Phase 3 — the VM producer, its own pipeline

- [ ] `smd admin heartbeat emit` writes the envelope to the sink as a
      `station.heartbeat` row (alongside, not instead of, the spool file, until
      Phase 7 retires the file).
- [ ] A status pipeline: `SqliteSource` on those rows + a tar transport writing
      `status/<ROLE>/<HOST_ID>/<ts>.jsonl`.  No change to `WsprCycleSource`.
- [ ] Confirm by test that the pipeline names no client and so is exempt from
      `SinkGate` — a station whose sink switch reads `fill` must still report
      (trap 2).
- [ ] `pipeline_factory` branch; `site_profile.py` + `uploader_manifest.py` keys.
- [ ] `sigmond-heartbeat.timer` to `OnCalendar=*:0/1` (60 s); delivery 120 s.
- [ ] Pilot on a station whose account cannot match trap 1.

### Phase 4 — the Proxmox producer

- [ ] `pm-heartbeat.py`: tar step, and the injected shipper points at gw1/gw2
      instead of wd30.  `ship_spool()`'s logic is unchanged — it already ships
      the whole spool in one batch, which is one tar.  Cadence stays 300 s.
- [ ] The same `ControlPersist` options on its own sftp call.

### Phase 5 — dual-ship, compare, and measure what we claimed

- [ ] Both paths live for a week: sftp to wd30 AND the tar to ClickHouse.
- [ ] A comparison that fails loudly: every station the sqlite board shows, the
      ClickHouse board shows, with arrival times within the delivery gap.
      Disagreement is the finding, not a nuisance.
- [ ] **Measure the three numbers this design was argued on**, at the gateway:
      ssh handshakes per station per day (claimed ~10, from ~1,296), tars
      arriving per second (claimed ~1.5/s, from ~0.77/s), and reflector queue
      depth.  Each is a falsifiable claim; if one is wrong the cadence table is
      wrong with it, and the rider fallback is the remedy.

### Phase 6 — the boards

- [ ] `fleetboard.py` reads ClickHouse instead of sqlite, one instance per WD
      server.  `derive_status()` keeps deriving from arrival alone.
- [ ] `rac-dashboard` into a repo (trap 3), then a `Fleet` tab: one `tabs`
      entry, fleet-wide active status, links to each site's status page.

### Phase 7 — retire the old path

- [ ] `HeartbeatSftp` transport, the heartbeat spool pipeline, `ingest.py`,
      `hamsci-hb-ingest.timer`, the `hamsci-hb` account on wd30.

## Open items for rob

1. **Is `AI6VN_2*` still wanted as a delete pattern?**  It now discards status
   as well as spots, and it constrains which station can pilot Phase 3.
2. **Is the Fleet tab admin-tier-only**, or visible to every station operator?
3. **60 s production / 120 s delivery** — confirm, or name the numbers you
   want.  15 s production argues against the standing
   no-heavy-diagnostics-on-station-VMs rule; 15 s delivery would put ~6 tars/s
   on the reflector, roughly 8x its present file rate.
4. **Does `ControlPersist` go in on its own, ahead of all status work?**  It
   touches every existing spot upload, which is an argument for landing and
   soaking it separately (as Phase 2 assumes) — and also an argument for not
   doing it at all until status needs it.  It is written as Phase 2 because it
   is the change most likely to pay for itself regardless of what status does.

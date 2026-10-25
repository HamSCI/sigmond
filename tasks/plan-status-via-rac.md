# Status reporting through the RAC, into ClickHouse

**Status:** DESIGN AGREED 2026-10-25, nothing built.  rob ruled that RAC and
status reporting are tied, because monitoring and control of a station both go
through the RAC channel.  Carrier: the station pushes.  Receiver: whatever
`wsprdaemon_reflector.py` already accepts.

## Motivation

A station's heartbeat goes to wd30 today by a private path: one shared chrooted
SFTP account, a sqlite database, and a board bound to a LAN address.  That path
is separate from every other thing the fleet ships, separate from the channel
used to reach a station, and it terminates on one host.

Tying it to the RAC gives one channel to keep alive and one channel to debug: a
station with a working RAC has working status reporting by construction, and the
data lands where spots and FTx data already land, on all three WD servers, in
ClickHouse, queryable beside everything else a site produces.

## What exists today (measured 2026-10-25, not assumed)

Two independent producers, both shipping to wd30:

| producer | assembles | ships |
|---|---|---|
| station VM | `smd admin heartbeat emit` -> `/var/lib/sigmond/heartbeat/*.json` | hs-uploader `FileTreeSource` + `HeartbeatSftp`, one file per record |
| Proxmox host | `scripts/proxmox/pm-heartbeat.py` (stdlib only, no hs-uploader) | its own `ship_spool()`, the whole spool in ONE sftp batch |

Both land in `/srv/hamsci-hb/incoming` on wd30 as the shared account
`hamsci-hb`; `hamsci-hb-ingest.timer` runs `server/heartbeat/ingest.py` into
`/var/lib/hamsci-fleetboard/heartbeats.db`; `fleetboard.py` serves
`192.168.1.164:8090`.

The WD side already has the pipeline this work needs:

- `wsprdaemon_reflector.py` on gw1/gw2 globs `/home/*/uploads/*.tbz`, validates
  with `tar tf`, hard-links into per-destination queues and
  `rsync -a --remove-source-files` to wd10, wd20 and wd30
  (`/var/spool/wsprdaemon/from-gw1`, `.../from-gw2`).  **It never looks inside
  a tar.**
- `wsprdaemon_server.py` on each WD server extracts the tar and parses peer
  subdirs at the tar root.  `ft8/`, `ft4/` and `msk144/` were added exactly this
  way in Phase 2 PR 1: a list in `config['psk_modes']`, a `process_mode_files()`
  call in `extract_and_parse_tbz()`, gated by `WSPRDAEMON_INGEST_PSK=1`.
- 92 upload accounts already exist on gw1, **including `AC0G_B4_PROXMOX`,
  `AC0G_ND`, `AC0G_B4` and `DASI002`** — stations and Proxmox hosts alike.
  Nothing needs provisioning.

## Design

Status becomes a new peer subdir in a tar, delivered to the station's existing
gateway account **through the RAC tunnel**, and parsed on each WD server into
one ClickHouse table.

```
station VM  --+
              |  existing frpc session (TLS, pinned CA, TOFU node key)
Proxmox host -+        |
                       v
         gw1 / gw2 / vpn1 / vpn2     reflector: UNCHANGED
                       |             (glob, tar tf, hard-link, rsync -a)
          +------------+------------+
          v            v            v
        wd10         wd20         wd30     wsprdaemon_server.py: new status/ parser
          |            |            |      -> station.heartbeat (ClickHouse)
      fleetboard   fleetboard   fleetboard
          +--------- rac-dashboard "Fleet" tab (gw2) ---------+
```

### Why the station pushes rather than the server pulling

A server-side collector over the RAC ssh ports would need no station change at
all and would enrol automatically from the registry.  It was rejected for the
first implementation because it builds a fan-out scheduler, concurrency control
and key handling on the server to replace machinery the station already has:
the spool, `FileTreeSource`, the watermark store, the retry policy, and the
backlog figures `smd check` reads.  Pull remains the right answer later for
hosts that emit no heartbeat at all; see Non-goals.

### Tar layout

```
<tar root>/
  status/<ROLE>/<HOST_ID>/<YYYYMMDDTHHMMSSZ>.jsonl     one envelope per line
                                                       ROLE is 'vm' or 'pm'
```

One tar per shipment, carrying only status.  It does **not** ride the WSPR cycle
tar: the 5-minute cadence must not depend on WSPR cycles, nor on the station
running WSPR at all.

The envelope on each line is unchanged — `lib/sigmond/heartbeat_schema.py`
stays the wire contract, `kind = "station_heartbeat"`, `schema_version = 1`,
blocks `versions manifest timing gaps uploads doctor resources` plus `rac` for a
host.  `validate()` already allows additive fields at every depth, so a station
one release ahead of a server is never rejected.

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

### Delivery through the tunnel

Each station's frpc gains one `[[visitors]]` block per gateway, binding a
loopback port; each gateway runs one provider frpc publishing an `stcp` proxy to
its own loopback sshd.  The status transport then sftp's to
`127.0.0.1:<visitor port>` instead of to a public gateway address.

- **Distinct `bindPort` per gateway.**  Two sessions binding one loopback port
  means the second frpc dies "address already in use" — the same defect class as
  the `[webServer] port = 7500` collision fixed in `e49e385`.
- **Ordered endpoints, not parallel pipelines.**  One pipeline tries the vpn1
  visitor, then vpn2.  Delivering through both every cycle would double every
  row; trying in order means a duplicate only during a flap, so the dedupe below
  is a safety net rather than load-bearing.
- **No new exposure.**  The stcp proxy reaches the gateway's own sshd, which
  already accepts these same accounts from the public internet for spot uploads.
  The station reuses its existing account and key.

### ClickHouse

`schema/station.heartbeat.sql` in wsprdaemon-server, house style (ReplacingMergeTree,
ZSTD(3), Delta codecs, provenance header), mirroring `psk.spots.sql`:

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

Three choices worth their rationale:

- **`verdict` / `reason` as `Map`, not columns.**  Adding a block is a schema
  change in `heartbeat_schema.py`; it must not also be a ClickHouse migration,
  or the fleet loses visibility of exactly the hosts updated most recently.
- **`payload` verbatim.**  The rule that a block which vanished can never read
  as healthy only holds if the original is recoverable.  The derived columns are
  a convenience; the envelope is the evidence.
- **`ORDER BY (station, emitted_at, envelope_sha)` collapses gateway
  duplicates.**  A tar delivered through both gateways yields two rows with the
  same key; ReplacingMergeTree keeps one.  Their `received_at` differ by the
  delivery gap, so the surviving value is whichever merged last — a difference
  of seconds, and stated here so nobody later reads it as exact.
  Volume is ~92 stations x 288/day ~= 26 k rows/day; no partition-pruning concern.

## Non-goals

- **Signed envelopes.**  `ssh-keygen -Y sign` against the TOFU registry would
  turn "the server trusts whatever the envelope claims" into real attribution,
  and the key material already exists.  It is NOT in this work: delivery already
  requires a live RAC session and the station's own gateway account, so
  attribution is no worse than the spot path it joins.  Worth doing after.
- **A server-side pull collector** for hosts that emit no heartbeat (plain
  wsprdaemon stations, Proxmox hosts outside the sigmond fleet).
- **Retiring wd30's board before the dual-ship week passes.**
- **Changing the heartbeat envelope.**  Not one field.

## Traps found while surveying (all verified on the running systems)

1. **`"delete_patterns": ["AI6VN_2*", "*.png"]`** in `/etc/wsprdaemon/reflector_destinations.json`
   on BOTH gateways — broader than the repo default `AI6VN_25*`.  Any upload
   whose name starts `AI6VN_2` is deleted before forwarding.  The status tar
   name must not collide, and the pilot station must not be one whose account
   name triggers it.  **Check this before any test traffic.**
2. **frp visitor `serverName` may need the provider's user prefix.**  frp
   prefixes a proxy name with its client's `user`.  Verify against frp 0.64
   whether the visitor must name `wd-status` or `<provider-user>.wd-status`
   before writing it into 92 station configs.
3. **vpn1/vpn2 are UNVERIFIED as reflectors.**  gw1 and gw2 were read directly
   and both run `wsprdaemon_reflector@reflector.service` with upload accounts.
   The HamSCI gateways were not — and a station with a RAC number in 200-299
   rides vpn1/vpn2 ONLY (`_rac_gateways_for()` in `bin/smd`), so its status has
   no path at all unless those two carry a reflector and the station has an
   account there.  PROBED 2026-10-25: neither vpn1 nor vpn2 accepts root for
   any key we hold — they are HamSCI-operated, not ours to install a reflector
   on.  So this is not a verification step but a REAL GAP: either HamSCI runs a
   reflector and issues upload accounts, or stations in that range need a gw2
   session for status alone, or that tier keeps a different route.  It needs a
   decision before any 200-299 station can report this way.
4. **`rac-dashboard` is not under version control.**  `/home/wsprdaemon/rac-dashboard/server.py`
   on gw2, 38 KB, with ten hand-made `server.py.bak-*` copies, newest edited
   2026-10-24 21:54Z.  Put it in a repo before adding the Fleet tab.

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
      `pm-heartbeat.py` leaves `rac` out on exactly those hosts
      (`scripts/proxmox/pm-heartbeat.py:191`), so the inference fails for the
      hosts least likely to be noticed.
- [ ] `tests/test_status_ingest.py` against synthetic extracted trees, no
      ClickHouse required — the shape `tests/test_psk_ingest.py` already uses.
- [ ] Replay wd30's existing `heartbeats.db` through the parser.  Every envelope
      the sqlite board holds must land, with the same verdicts.

### Phase 2 — one station end to end

- [ ] Provider frpc + stcp proxy on one gateway; resolve trap 2 first.
- [ ] `[[visitors]]` in `_render_frpc_config()`, distinct bindPort per gateway,
      with the test that the ports in a station's set are distinct (the
      `AdminPorts` class in `tests/test_rac_dual_gateway.py` is the model).
- [ ] `hs-uploader/transports/status_tar_sftp.py`, built on `WsprdaemonTarSftp`'s
      proven mechanics (`.part` + rename, host-key recovery); ordered endpoints;
      ANY failure is `retry_later`, never permanent — the file is the payload and
      a retry is free.
- [ ] `pipeline_factory` branch; `site_profile.py` + `uploader_manifest.py` keys.
- [ ] Pilot on a station whose account cannot match trap 1.

### Phase 3 — the Proxmox producer

- [ ] `pm-heartbeat.py`: tar step, and the injected shipper becomes the RAC
      endpoint.  `ship_spool()`'s logic is unchanged — it already ships the whole
      spool in one batch, which is one tar.

### Phase 4 — dual-ship and compare

- [ ] Both paths live for a week: sftp to wd30 AND RAC to ClickHouse.
- [ ] A comparison that fails loudly: every station the sqlite board shows, the
      ClickHouse board shows, with arrival times within the delivery gap.
      Disagreement is the finding, not a nuisance.

### Phase 5 — the boards

- [ ] `fleetboard.py` reads ClickHouse instead of sqlite, one instance per WD
      server.  `derive_status()` keeps deriving from arrival alone.
- [ ] `rac-dashboard` into a repo (trap 3), then a `Fleet` tab: one `tabs` entry,
      one route, one query.  It can join live frps state against last-heartbeat,
      which is strictly more than either source knows alone — a station whose
      tunnel is up but whose heartbeat stopped is a different fault from one
      that is simply gone, and only this join can tell them apart.
- [ ] Each row links to that site's own status page.

### Phase 6 — retire

- [ ] `HeartbeatSftp`, `server/heartbeat/ingest.py`, the `hamsci-hb` account and
      its authorize script.  Only after phase 4 passes.

## Open items

- Which station pilots phase 2 (constrained by trap 1).
- Whether the `AI6VN_2*` delete pattern is still wanted at all.
- Whether the Fleet tab is visible to every dashboard tier or admin tiers only.

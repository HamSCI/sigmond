# Operator commands through the uploader

Companion to [`plan-status-via-uploader.md`](plan-status-via-uploader.md).
That plan moves **observation** station→server on the upload path.  This one
adds **intent** on the same path: an operator stating a fact about their own
receivers that the servers must act on.

Merge selection is the worked example throughout, because it is the case that
forced the question and the only one with running code today.

**Status, 2026-10-28:** reviewed by Michael (bus 20261028T002659Z), amended
here.  The command now rides the **client's own upload**, written by
hs-uploader, never by `smd` — Michael's principle is that each client keeps
responsibility for its own uploads and stays usable without sigmond.  One
question is open for Michael and Rob; see "Open items".

## Why this path and not a web page

The obvious design is a page on a WD server where an operator picks which of
their receivers report as one.  It does not survive contact with the fleet:

- The WD servers are firewalled.  A page an operator could reach means an open
  port, or a page on a gateway with a hole punched through to the server.
- Most operators have no WireGuard tunnel to the WD servers and never will.
- A merge decision must reach **all three** servers, because the forwarders run
  under an elector and the server that POSTS is not the one the operator
  talked to.

The upload tar already solves all three.  It is outbound-only from the
station, it is authenticated by the station's own gateway account, it is
already replicated to wd10/wd20/wd30, and every uploading station has one.
Status is moving onto it for exactly these reasons.  Commands are the same
channel carrying intent instead of observation.

**No new port, no public surface, no tunnel requirement.**  That constraint is
the design driver, not an afterthought.

## What exists today (measured 2026-10-27, not assumed)

- **Status, server side: built.**  `station.heartbeat` ingests from the tar
  (`setup_status_tables`, `tests/test_status_ingest.py`).  Phase 1 of the
  status plan is landed.
- **Status, station side: not built.**  `sigmond-heartbeat.service` runs
  `smd admin heartbeat emit`, which writes one envelope to the spool.  Phase 3
  of the status plan — the producer that puts it in the tar — is open.
- **Commands: one narrow case exists.**  `routing.json` carries
  `psk_reporter` / `wsprnet_reporter`; the server records them in
  `psk.reporter` and `wsprdaemon.wsprnet_reporter`.  That is already an
  operator→server command on the upload path, with a fixed payload and no
  envelope.  Pushed as `eeb8ab5`; **not deployed to any server**.
- **Replication of those tables: fixed, not deployed** (`410261f`).  Before it,
  a *changed* declaration could not leave the server that ingested it.
- **Missing entirely:** a general envelope, a command vocabulary, ordering and
  idempotence across three servers, authority scoping, and any return leg.

## Design

### The envelope

One command per line, at the root of the **client's own** tar, beside the
`routing.json` it generalises:

```
<tar root>/
  routing.json                       (today: per-receiver forwarding flags)
  command.jsonl                      (new: one operator_command per line)
  wspr/spots/<RX_SITE>/<RECEIVER>/…
```

It is deliberately NOT under a `<HOST_ID>` directory the way status is: a
command belongs to the client that uploaded it, and that client is already
named by the tar it rides in.

```json
{"kind": "operator_command", "schema_version": 1,
 "command_id": "<uuid>",
 "issued_at": "2026-10-27T22:40:00Z",
 "issued_by": "AC0G",
 "command": "set_reporter_identity",
 "scope": {"path": "pskreporter", "key": "AC0G/ND"},
 "args":  {"call": "AC0G", "grid": "EN16ov"}}
```

### Five rules, each one earned

1. **Idempotent and ordered.**  `command_id` plus `issued_at`; newest wins per
   `(command, scope.key)`.  A command replayed by three servers converges
   instead of fighting.  This is what makes "the same tar reaches all three"
   safe rather than racy.

2. **Authority is the delivery account, never the envelope.**  The tar arrives
   through the station's own gateway account.  A command may only touch keys
   whose callsign base matches the delivering station's registered callsign —
   `AC0G` may set `AC0G/ND`, nobody else may.  Enforced server-side, so the
   UI (if one ever exists) cannot be talked out of it.  Attribution is exactly
   as good as the spot path it rides, and no better; signed envelopes remain a
   non-goal here for the same reason the status plan gives.

3. **Arrival, not self-report.**  `received_at` is the tar's mtime, as with
   status.  A station cannot report that it stopped.

4. **Never fail the spots.**  The passenger rule, already load-bearing in two
   places: a malformed or unauthorised command is logged and skipped, and the
   spots in the same tar still land.  A command path that can cause an outage
   will not be trusted with anything interesting.

5. **Replicable by construction.**  Command state lands in a table carried by
   `ch-sync-peers`, with a sorting key that lets a CHANGED value propagate.
   `410261f` is the precedent and the cautionary tale: ordered by key alone, a
   change hashes identically to what it replaces and never leaves the server
   it landed on.

### The command vocabulary is closed and declarative

A command states a fact about the operator's own receivers.  It is never
"run this".  There is no shell, no path, no argument that reaches an
interpreter.  `set_reporter_identity` is the whole vocabulary at the start.

This is a security property, not a style preference: an inbound channel that
can only write a callsign into a lookup table has a bounded blast radius even
if authority is somehow wrong.

### Worked example — merging B1, B2 and B4

Today the operator edits `site-profile.toml` on each of the three stations and
has no way to see the group as a group.  With commands:

1. Operator declares the group once, naming its members, in each member's
   own uploader config.
2. **hs-uploader** writes the resulting `set_reporter_identity` facts into
   that client's own tar, the way it already writes `routing.json`
   (`_render_routing_json`, `transports/wsprdaemon.py`).  `smd` may help the
   operator author the config, as it does for all config, but it never
   carries the command.
3. They ride that client's next upload.
4. Each WD server applies them; `ch-sync-peers` converges the rest.
5. The elected forwarder groups by resolved identity and posts one reporter.

The only change from what already works is that the declaration gains an
envelope — `command_id`, `issued_at`, `issued_by` — so three servers can
apply it idempotently and newest-wins.  The channel itself is unchanged and
needs no sigmond.

### ⛔ Two merges, and they must not both fire

wspr-recorder already merges on the station: its merge instance
(`WD_MERGE_REPORTERS`, `wspr_completion.cycle_complete`) waits for every
named receiver and keeps the best SNR per spot *before the tar leaves*.

So a receiver can be merged in two places, and must never be merged in both
— "best SNR per spot" applied twice against different populations is not the
same answer twice.  The rule:

```
a receiver that arrives ALREADY MERGED is not a candidate for server merging
```

**Code fact, and the two paths are NOT symmetric:** that station-side merge
is **WSPR-only**.  There is no FT8/FT4 equivalent — psk-recorder writes
per-receiver rows and the merge decision lives in the server forwarder.  For
FT the server is not a fallback for separate stations; today it is the only
place a merge can happen at all.  Do not assume symmetry between the two
upload paths.

### Migration — nothing to undo

The existing `routing.json` declarations stay, and are treated as an implicit
`set_reporter_identity` at the **lowest** precedence:

```
operator command  >  routing.json declaration  >  the row's own rx_sign
```

A station that never sends a command behaves exactly as it does today.

## The return leg is the open problem

"Where do I gain access" ends here, and honestly: the station has no inbound
path, by design.  Outbound-only is why this channel works at all.

Three candidates, none free:

- **Publish, do not return.**  The server writes `station.command_result`;
  the operator sees it on a board.  Cheapest, but a board is the thing
  operators cannot reach — it only helps wd admins.
- **Poll on the station's existing outbound connection.**  A station with RAC
  already holds an outbound ssh; the result could be fetched over it.  Works
  for sigmond stations, not for plain wsprdaemon ones.
- **External confirmation.**  The operator verifies by looking at PSKReporter:
  one reporter where there were three.  Zero code, and it is what they would
  check anyway — but it is slow and only works for commands with a visible
  external effect.

Recommendation: ship A and C, treat B as part of the status plan's Phase 6
boards rather than inventing a fourth transport.

## Phases

### Phase A — the server half, provable without a station
- [ ] `station.command` table + envelope parse + the passenger rule.
- [ ] Authority check against the delivering account.
- [ ] Precedence wired into both forwarders' identity resolution.
- [ ] Added to `ch-sync-peers` with a sorting key that carries changes.

### Phase B — the client half, in hs-uploader
- [ ] hs-uploader writes the command envelope into the client's own tar,
      beside `routing.json` or extending it.  No `smd`, no sink, no status
      pipeline — a standalone client must be able to do this.
- [ ] Group expansion: the operator names a group in the client's config;
      hs-uploader expands it into per-receiver facts.
- [ ] Enforce the already-merged rule above.

### Phase C — results
- [ ] `station.command_result`, surfaced on the fleetboard.

Phase A is testable with synthetic tars, exactly as the status ingest is, and
depends on nothing a station does.

## What a tar names today — and what it does not

Measured on `origin/main`, 2026-10-28, because Michael's point 5 asks the
fleet view to treat a standalone client as a first-class kind of site.

Present today:

- `<RX_SITE>/<RECEIVER>` in the path of every spot file.
- `uploads_config.txt` — `CLIENT_VERSION` plus three format-version lines
  (`_config_bytes`).  A version, and **no client name**: it cannot tell
  wspr-recorder from a clone of it.
- `routing.json`, only when ft* files are present.
- The delivering account, which identifies the uploader, not the software.

**⛔ RX_SITE IS NOT A STABLE SITE KEY.**  `_build_rx_site(call, grid)` returns
`<call, / as => _ <grid>`, so it changes when the grid changes.  It changed
under us on 2026-10-27: AC0G/ND moved from `AC0G=ND_EM38ww` to
`AC0G=ND_EN16ov` at 19:18Z when its stale client configs were corrected.  A
fleet view joined on RX_SITE would have shown ND as two sites, the first of
which "went silent" that afternoon.

So two changes are needed before a fleet view can list standalone clients:

1. A **client-name** field.  `uploads_config.txt` is the natural home: it
   already exists, is already metadata-shaped, and rides every tar.
2. A join key of **callsign + receiver, with the grid excluded.**

## Status in two layers (Michael's point 3)

- **Client layer, always present** — a small status block in the client's own
  tar: client name and version, instances, last cycle's output, decoder
  errors.  A standalone client reports itself with no sigmond.
- **Station layer, only under sigmond** — the heartbeat on its own pipeline,
  as `plan-status-via-uploader.md` designs it.
- The server joins the two by station identity (see the join key above).
- **Arrival stays the backstop.**  A client that stops uploading goes silent
  where it matters, whatever it last said about itself.

⚠ The unresolved part is what happens when a client is deliberately not
uploading — see "Open items".

## Non-goals

- **Signed envelopes.**  Same reasoning as the status plan: delivery already
  requires the station's gateway account.  Worth doing after, for both.
- **Remote execution of anything.**  The vocabulary is closed and declarative.
- **A web page on a WD server or a gateway.**  See the opening section.
- **Changing the heartbeat envelope.**  Not one field.

## Open items

### 1. FOR MICHAEL AND ROB — may a down data switch silence a client?

`plan-status-via-uploader.md` trap 2 says, of folding status into the data
tar: *"A station must not stop reporting because an operator turned its data
flow down — that is the moment status matters most... re-read this trap
before ever folding status back into the cycle tar."*

A client status block inside the client's data tar is that fold.  Under
sigmond it is survivable: the heartbeat rides its own pipeline and covers
the gap.  For a **standalone client** there is no heartbeat, so one whose
sink switch reads `fill` or `off` ships no tar, carries no status, and reads
as DEAD rather than healthy-and-not-sending — the trap-2 failure, in the
configuration Michael is designing for.

- *Michael / mjh:* the block rides the client's own data upload; simplest,
  and arrival is the backstop.
- *rob's instance:* same, **plus** a client whose data switch is down still
  ships a status-only tar, so status is never gated by the data switch.

This is a policy question about what silence is allowed to mean.  Neither
assistant should rule on it.

### 2. The return leg
A and C above, or something better.

### 3. Authority: delivering station, or an operator token?
The delivering account is simpler and needs no new secret.  A token would let
an operator manage receivers at a site they do not upload from.  Start with
the account.

### 4. Vocabulary beyond identity
Nothing else is proposed yet, deliberately.

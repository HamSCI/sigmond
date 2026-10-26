# Delivery over a constrained link, and a reflector that stops reading payloads

**Status:** DESIGN PROPOSED 2026-10-26, nothing built.  Driven by the next
installation being at the **South Pole**: limited bandwidth, scheduled daily
outages, and a cost per communication transaction.  The outage structure was
read off the USAP schedule rather than assumed — three gaps a day totalling
~7 h, the largest ~4h10m, and **~10 h on the 27 % of days when TDRS has no
pass at all**.  Two separable parts:

- **Part A — fleet-wide.**  The reflector stops validating tars; the client
  makes "upload finished" a fact rather than a guess.  Helps every station
  today, Pole or not, and removes the 10-15 s delivery floor.
- **Part B — constrained-link mode.**  Spool through the outage, ship once per
  link window, resume interrupted transfers, and (optionally) report by
  exception.  Opt-in per site; CONUS stations are untouched.

Related: `tasks/plan-status-via-uploader.md`.  Part A supersedes that plan's
assumption that `min_age_seconds` is immovable.

## Part A — the reflector does not need to open a tar

### What the validation actually buys

`wsprdaemon_reflector.py:246` runs `tar tf` with `stdout=DEVNULL`: it fully
decompresses every archive and discards the listing, to prove the file parses.
It catches exactly two conditions.

| condition | caught elsewhere? |
|---|---|
| corrupt archive | **yes** — wd10/20/30 must decompress to parse, so they detect it for free |
| partial upload (client still writing) | only here, and only by guessing |

So the gateway pays a decompression on 100% of files to pre-empt a case that
costs nothing downstream.  And because the reflector forwards with
`rsync --remove-source-files`, a bad tar is removed either way; the client
cannot tell the difference and would not retry differently.  The validation
changes where a corrupt file dies, not whether its data survives.

`min_age_seconds: 10` is the other half of the same guess, and it is the
expensive half: it is the dominant term in delivery latency, ahead of
compression, ssh and rsync combined.

### Replace the guess with a fact

The client knows exactly when an upload is complete.  Let it say so:

```
reput  <local>  <name>.tbz.part      # resumable; see Part B
rename <name>.tbz.part  <name>.tbz   # atomic within the filesystem
```

The reflector then globs `*.tbz` only.  A file matching that glob exists only
after a successful rename, so **completeness is structural**, not inferred.

Consequences:

- `min_age_seconds` -> 0.  Delivery floor falls from ~10-15 s to one
  `scan_interval` (5 s today, and tunable now that it is the only term).
- `tar tf` -> a magic-byte check, or nothing.  The gateway reads no payload
  bytes at all: stat, hard-link, rsync.  A router should not parse payloads.
- The validated-inode set and its bookkeeping go away with it.

### Compatibility

Old clients keep writing `name.tbz` directly.  The reflector must therefore
keep the age-based path for any file that appears without having been renamed —
there is no way to distinguish them at the gateway, so the two rules coexist:

- `*.tbz.part` — ignore, always, however old.  (Also fixes a real hazard
  today: an interrupted upload leaves a `.tbz` the reflector will happily
  forward once it is 10 s old.)
- `*.tbz` — forward.  Keep `min_age_seconds` configurable and drop it to 0
  only on a gateway whose stations have all moved to rename.

A `.part` suffix that the reflector ignores is therefore the FIRST change to
land, before any client uses it.  Gateways first, clients second.

## Part B — constrained-link mode

### What the Pole changes

Not CPU.  Round trips, interrupted transfers, and bits.

**The dominant defect is one most stations never notice.**
`sources/wspr_cycle.py:6` yields **one RecordBatch per cycle**.  After the
nominal 4h10m outage 125 cycles are pending — after a no-pass day's 10 h, 300 —
so the pump produces that many batches, tars and transfers — each with its own archive header, its own compression context
and its own protocol exchange, over exactly the link least able to afford them.
Status has the same shape at smaller scale.  Spot volume dominates, so this is
the first thing to fix for the Pole.

### Measured: what batching is worth

Method, so it can be redone against real data: 240 synthetic envelopes
(the nominal 4h10m gap at 60 s production) shaped on `heartbeat_schema.py`'s
eight blocks, with random float metrics.  Synthetic numbers — the ratios are
the point, not the absolute bytes, and random floats are pessimistic for
compression in a way real envelopes are not.  A no-pass day is 2.5x this
(~600 envelopes), where batching helps MORE, not less.

```
240 separate tars (today's model)        197,573 bytes
one archive, bzip2 -9                     47,004          4.2x
one archive, zstd -19                     53,994
change-triggered full envelopes + ticks    4,556           43x
```

Two findings worth carrying:

- **The 4.2x is free.**  It comes from nothing but putting the records in one
  compression stream.  Near-identical envelopes share almost everything; 240
  separate archives discard that redundancy by construction.
- **A trained zstd dictionary is NOT worth it once batched** — 53,994 -> 53,668.
  The batch already is the shared context.  Dictionaries pay only for small
  isolated payloads, which batching eliminates.  (Recorded because it looked
  attractive before it was measured.)

Codec choice is size-dependent, and the fleet default is wrong at both ends:
bzip2 loses to gzip on a single 2.7 KB envelope (828 vs 800 bytes, and 3.6x
slower to decompress) but wins clearly on the 47 KB batch.  So: small frequent
tars -> zstd; big batched archives -> bzip2.  One global codec cannot be right
for both.

### Report by exception (the 43x, opt-in)

Send a full envelope only when a verdict CHANGES, plus an hourly keepalive.
Every other interval emits a tick:

```json
{"t":"2026-10-26T12:34:00Z","r":"VALID","h":"<sha256[:16] of the envelope>"}
```

In the measured run that was 4 full envelopes and 236 ticks, and **every
transition was preserved** — what is lost is drift in the detail metrics on
intervals where no verdict moved.  The hash lets the server prove the station
computed a full envelope it chose not to send.

This is a per-site setting.  CONUS stations keep full envelopes; nothing about
the wire contract changes, since a tick is a different `kind`.

### Resumable transfers: the operational one

With a daily outage, **an interrupted transfer is the norm, not the
exception** — and today a cut transfer resends from zero, so the next window
can cut it at the same place forever.  OpenSSH `sftp` has `reput`, which
resumes from the remote file's current length.  With Part A's `.part` + rename
this needs no server-side change at all.

**The archive must be byte-stable across attempts**, or `reput` splices two
different files into one corrupt archive.  So a constrained-link archive is
named by content hash, not by time-of-send, and is written once and never
rebuilt.  This is a correctness requirement, and it is the reason the batch
must be formed BEFORE the first attempt rather than "whatever is pending now".

### The schedule is published — use it (USAP)

USAP publishes the South Pole pass schedule, so the station does not have to
discover its windows by failing.  Read 2026-10-26:

| source | covers | format | stability |
|---|---|---|---|
| `https://www.usap.gov/technology/documents/cel.txt` | TDRS only | fixed-column text, `YY/DDD/HHMMSS` | **stable URL**, ~2.6 KB, poll weekly |
| `https://www.usap.gov/technology/documents/SPSAT <YYYYMMDD>.pdf` | TDRS + DSCS + Skynet | PDF (also renders EDT) | filename carries the week; anchor must be scraped from `/technology/1935/` |
| `https://www.usap.gov/usapgov/technology/contentHandler.cfm?id=1974` | short-notice changes | HTML | "Special Service Announcements" — where mid-week changes land |

There is no iCal, JSON, CSV or API.  `cel.txt` is the machine-readable one.

**Times are UTC — verified, not assumed.**  `cel.txt` carries no timezone label
anywhere in the file.  It was confirmed by cross-reference: the weekly PDF is
headed "South Pole Satellite Schedule (UTC)" and lists `10/14/26 4:32 PM
9:02 PM` for TDRS-6, against `cel.txt`'s `26/287/163200 210200` — day 287 of
2026 is 14 Oct, and the times match exactly.  **Re-run that cross-check before
trusting any future parser**; getting this wrong by the NZ offset would invert
suppression and silence the station for exactly the hours it can talk.

### What the schedule actually looks like

Measured from the 2026-10-14 weekly PDF, one representative day (UTC):

```
COVERED  00:00-05:44   07:13-12:22   16:32-21:02   22:22-24:00
GAPS          05:44-07:13 (1h29m)   12:22-16:32 (4h10m)   21:02-22:22 (1h20m)
```

Three findings that the design must answer:

1. **The "4-hour gap" is the typical largest gap** (4h10m here), not the only
   one.  There are three gaps a day, totalling ~7 h.
2. **It regresses ~4 minutes per day.**  Consecutive TDRS passes step
   16:32 -> 16:28 -> 16:24, durations pinned — one sidereal day.  A cached
   schedule is ~28 minutes wrong after a week, so it MUST be refreshed; a
   hard-coded window is wrong by design.
3. **TDRS has "No Pass" days, and they are frequent.**  4 of the 15 published
   days (10/19, 10/26, 10/27, 10/28) carry no TDRS pass at all.  On those days
   12:22-22:22 is unbroken: **a 10-hour gap, not a 4-hour one.**  Spool sizing,
   archive caps and backlog tests must use 10 h, and nothing should assume a
   nominal day.

### Trust the schedule's "no", verify its "yes"

The schedule is reliable about when there is no satellite.  It is NOT a promise
that the link works when there is one: TDRS is shared with other users, the
station has its own outages, and the overview page itself warns that "NASA's
satellite schedules occasionally change after publishing."

So the asymmetry is load-bearing:

- **Outside a window** — do not attempt.  No connection attempt, no retry
  backoff, no log churn.  This is the suppression rob asked for.
- **Inside a window** — attempt, and let the transport's existing outcome
  handling decide.  The schedule never asserts the link is up.

**A schedule must never become a gag.**  If the cached schedule is older than
its refresh horizon, or cannot be parsed, the station reverts to
attempt-anyway.  A stale file silencing a station indefinitely is a worse
failure than a few wasted attempts, and it is the kind that goes unnoticed.

The refresh is self-bootstrapping: fetch `cel.txt` at the start of each window,
when the link is by definition up.  2.6 KB a week is free.

### Ship on link state and schedule, not on a timer

A 120 s delivery cadence is meaningless on a link that is down a sixth of the
day.  Constrained-link pipelines pump when the link comes up: coalesce
everything pending into one archive, send it, done.  One transaction per
window, which is also the answer to cost-per-transaction.

## The board must know the link schedule

Arrival-time liveness is load-bearing in `plan-status-via-uploader.md`, and at
the Pole it degrades honestly to "alive within the last window".  A station
that is legitimately dark four hours a day will show red four hours a day, and
people will learn to ignore the board — which is worse than having no board.

So a constrained-link site carries an expected-window schedule, and the board
distinguishes **dark-as-expected** from **missed its window**.  The tick stream
restores the fine-grained record retrospectively once the link returns, so
nothing is lost but promptness, which the link had already taken.

## TODO before any Pole deployment — measure the station's egress

USAP will want the list of everything a sigmond station talks to: a restricted
network needs an allowlist, and a bandwidth budget needs to know who is
spending it.  **Measure it on AI6VN before anything ships**, because the list
has to be complete before deployment, not discovered after.

### When: from the next image installation (rob, 2026-10-26)

Do NOT start on the running box.  Begin the capture at the **next image
install on AI6VN**, running from first boot, so the record covers bring-up and
install as well as steady state.  Those are different firewall cases and the
install-time set is the one a steady-state capture silently misses — package
and repo endpoints a station touches once and never again.

### Where to capture: the odroid, and it is now a COMPLETE point

rob, 2026-10-26: **keep the IPv6 path through the odroid, drop the IPv4 path
through the EdgeRouter.**  A major test on AI6VN is that it comes up in a
wired IPv6-only environment.

That makes the odroid (ODROID-XU4, `rob@10.22.23.3`) the right capture point
and a complete one.  It is AI6VN's default IPv6 router — ASIX dongle at
`fd4f:a955:ac3d:1::1`, v6 segment of exactly two nodes
(`ops/ssh/config.fleet:104-122`) — so with IPv4 gone, **every packet the
station sends crosses it.**  The split-capture hazard disappears: there is one
path, and the odroid is on it.

### The allowlist needs an AAAA column, because almost nothing has one

An IPv6-only station cannot reach an IPv4-only service without NAT64.
Measured 2026-10-26 (and see `tasks/plan-ipv6-support.md` §1, which found the
same a month earlier — nothing has changed since):

- **No AAAA:** gw1, gw2, remote/vpn.wsprdaemon.org, vpn1/vpn2.hamsci.org,
  wd30, `report.pskreporter.info`, `pswsnetwork.eng.ua.edu`, github.com,
  api.github.com, pool.ntp.org.
- **Has AAAA:** wsprnet.org, raw.githubusercontent.com, pypi.org,
  files.pythonhosted.org, astral.sh.

So **every wsprdaemon-operated host and every science upload destination
except wsprnet is IPv4-only.**  An IPv6-only station's entire function — both
uploading and being reachable — runs through NAT64/clatd.

### The odroid is the translator, so AI6VN tests Case A

rob, 2026-10-26: **the odroid operates as an IPv6-to-IPv4 translator.**

That makes AI6VN a standing test bed for exactly the architecture
`plan-ipv6-support.md` calls **Case A — the site provides NAT64 + DNS64**, and
names as the most likely McMurdo configuration.  That case was validated once
on a bench (2026-09-28); AI6VN turns it into a permanent rig where every image
is exercised against it on first boot.

**This corrects something written earlier in this document.**  The claim that
"the IPv6-only test is really a clatd test" is right for only one of two
architectures, and probably the wrong one:

| architecture | what translates | does the station need clatd? |
|---|---|---|
| NAT64 **+ DNS64** at the edge | the odroid, and the station sees synthetic AAAA | **no** — "the VM is unaware" |
| NAT64 without DNS64 | the station's own CLAT | **yes** — 464XLAT, prefix discovery |

Which one the odroid implements is **the single most valuable thing to
establish**, because it is the same question `plan-ipv6-support.md` asks about
McMurdo (§7 Q1: "Is there NAT64/DNS64?").  If USAP provides DNS64, the station
needs nothing.  If NAT64 only, clatd is load-bearing and its boot race is a
deployment blocker.  **Verify on the odroid** — which service (tayga, jool),
and whether a resolver there synthesizes AAAA.

### Capture on the TRANSLATED side, or the list is useless elsewhere

A trap specific to this setup.  Under DNS64 the station sees synthetic AAAA
addresses — the NAT64 prefix with the real IPv4 address embedded in the low
bits.  **A capture taken on the station, or on the v6 side of the odroid,
yields a list of NAT64-prefix addresses that mean nothing to any other
network's firewall**, including USAP's.

So capture on the odroid's **IPv4 side**, or decode the embedded v4 address
out of each synthetic one.  A translator is the ideal capture point precisely
because it sees both halves and can log the mapping — but only if the capture
is taken from the half that is portable.

The list must therefore record, per destination: the real IPv4 address, the
hostname, and whether the flow was native v6 or translated.  Only the last of
those tells USAP whether an IPv6-only firewall can carry it unaided.

### These three plans are one deployment program

`plan-ipv6-support.md` was written for **McMurdo, which uses only IPv6**.
This plan is for the **South Pole**, with its scheduled satellite outages.
Both are USAP sites on the same program, so the Pole station is likely to be
**IPv6-only AND satellite-constrained at once**, and neither plan is
sufficient alone.  AI6VN is where the combination gets tested before anything
ships.

### What the list has to contain

- **Hostnames, not just addresses.**  An IP allowlist breaks on every
  CDN-backed service (GitHub, PyPI/Astral, Google Drive, pskreporter), whose
  addresses rotate.  Log DNS queries, which is also cheaper than packet
  capture.
- **Bytes per destination per day.**  The Pole needs a budget, not just a
  permission list, and this is what decides which endpoints are affordable.
- **Which unit caused each flow**, so endpoints can be dropped selectively —
  above all, which are needed at RUNTIME versus only at install time.  A
  station that reaches GitHub only during `smd update` is a different firewall
  case from one that reaches it hourly.
- **At least 7 days**, to catch anything weekly or periodic: certificate
  renewal, scheduled updates, log shipping, and the schedule fetch this plan
  adds.

### Make the capture falsifiable

A capture nobody can check is not a measurement.  These are known to be in the
set, so the capture must find them or the capture is wrong: the wsprdaemon
gateways (gw1/gw2), the frp gateway for RAC, pskreporter, wsprnet, PSWS, NTP,
GitHub and the uv/PyPI endpoints.  Anything the capture finds that is NOT on
that list is the interesting part, and the reason to do this at all.

## Non-goals

- **A custom streaming protocol terminating on the gateway.**  It trades
  file-level durability — where a retry is free and needs no state — for gains
  Part A already captures.
- **Routing uploads through frp.**  It adds a relay hop and framing: strictly
  more bytes and more latency than direct sftp.
- **Changing the heartbeat envelope.**  A tick is a new `kind`, not a new
  envelope shape.
- **Making report-by-exception the fleet default.**  It is for metered links.
- **Retiring `min_age_seconds`.**  It stays configurable for gateways still
  serving pre-rename clients.

## Traps

1. **Dropping `tar tf` before `.part` is honoured loses the partial-upload
   guard entirely.**  Order matters: gateways learn to ignore `*.tbz.part`
   FIRST; only then do clients start using it; only then does
   `min_age_seconds` go to 0.  Doing these in any other order ships corrupt
   tars to three servers.
2. **`reput` against a rebuilt archive silently corrupts.**  Content-hash
   naming is the guard, and the test is: build, send half, rebuild from a
   changed spool, resume, and verify the result fails a checksum rather than
   landing.
3. **A coalesced archive can exceed what one window can carry.**  A cap on
   archive size is needed, with the remainder carried to the next window.  The
   sizing case is a **10-hour** backlog (a TDRS no-pass day), not the nominal
   4h10m gap, and nobody has measured what the Pole station produces in 10 h.
4. **Report-by-exception can mask a stuck producer.**  A station whose
   heartbeat process has frozen emits no ticks — which reads the same as a link
   gap until the window closes.  The hourly keepalive is what separates them;
   it is not optional.
5. **`.part` files accumulate** if a client dies mid-upload and never returns.
   The gateway needs an age-based sweep for them — the one place where an age
   heuristic is still the right tool.
6. **A timezone error in the schedule parser inverts suppression.**  `cel.txt`
   carries no timezone label; it was shown to be UTC only by cross-reference
   against the weekly PDF.  Read 13 hours off (NZ, which is what South Pole
   Station runs on locally) and the station transmits only when it cannot and
   stays silent when it can.  The parser carries that cross-check as a test,
   with a known-good row pinned.
7. **The schedule drifts 4 min/day.**  Any test using a fixed date must pin the
   schedule file too, or it starts failing a week after it is written.

## Phases

### Phase 1 — gateways learn `.part` (no client change)

- [ ] Reflector ignores `*.tbz.part` at any age; sweep them after N hours.
- [ ] Magic-byte check replaces `tar tf`; the validated-inode set goes.
- [ ] `min_age_seconds` stays, default unchanged.  Nothing has moved yet.
- [ ] Verify on gw1/gw2 that forwarding is unchanged for existing clients.

### Phase 2 — clients upload to `.part` and rename

- [ ] `WsprdaemonTarSftp._sftp_batch_cmd`: put to `.part`, then `rename`.
- [ ] Mutation test: remove the rename and the file must never be forwarded.
- [ ] Once a gateway's stations have all moved, `min_age_seconds` -> 0 there,
      and measure the delivery-latency change rather than asserting it.

### Phase 3 — coalescing and resume

- [ ] `WsprCycleSource`: a mode that coalesces every pending cycle into ONE
      batch instead of one per cycle.  Default off.
- [ ] Content-hash archive naming; build-once, never rebuild (trap 2).
- [ ] `reput` in place of `put` when resuming a known `.part`.
- [ ] Archive size cap with carry-over (trap 3).
- [ ] Measure the coalescing gain on REAL spot data; the 4.2x above is from
      synthetic envelopes and says nothing about spots.

### Phase 4 — schedule-driven suppression

- [ ] `cel.txt` parser: fixed columns, `YY/DDD/HHMMSS`, day-of-year to UTC.
      Pinned-row test including the PDF cross-check (traps 6, 7).
- [ ] Weekly refresh, fetched at the start of a window; cached locally with a
      fetched-at stamp.
- [ ] Suppression: outside every known window, the pipeline does not attempt.
      Inside, it attempts and the transport decides.
- [ ] **Fail-open test**: a cached schedule past its horizon, a corrupt file
      and a missing file must each produce attempt-anyway, never silence.
      Prove it by mutation — break the file, watch the station still transmit.
- [ ] Per-site config: constrained = true, schedule source, refresh horizon,
      archive cap.
- [ ] DSCS/Skynet windows come from the weekly PDF, whose URL changes weekly;
      decide whether to scrape that anchor or start with TDRS-only coverage.

### Phase 5 — report by exception

- [ ] Tick `kind` in `heartbeat_schema.py`; server-side parse and storage.
- [ ] Change detection against the last SENT envelope, hourly keepalive
      (trap 4).
- [ ] Opt-in per site; off everywhere by default.

### Phase 6 — the board

- [ ] Expected-window schedule per site; dark-as-expected distinguished from
      missed-its-window.
- [ ] Ticks render as liveness without pretending to be full envelopes.

## Open items for rob

1. **Which satellites will the station actually have?**  The schedule is
   answered, but not the provisioning: TDRS high-rate is reserved for "science
   and operational data files", DSCS and Skynet are separate services, and a
   HamSCI station's traffic has to be allowed on one of them.  Coverage of
   ~17 h/day assumes all of them; TDRS-only is far less.  RTT is also still
   unmeasured, and it decides how much sftp's chattiness costs.
2. **How much data does the Pole station produce in 10 hours?**  Sizes trap 3
   and decides whether the archive cap is needed on day one.
3. **Iridium for the tick stream during gaps?**  IMCS exists precisely to fill
   these gaps, and a 10-hour tick backlog is a few KB — comfortably inside the
   "emails less than 50 KB" the service is provisioned for.  That would keep
   liveness reporting alive across every gap while bulk data waits for a
   window.  It needs USAP permission, not just code; worth asking before
   designing around it.
4. **Is report-by-exception acceptable for a science station**, or must every
   interval's detail metrics survive even at 11x the bits?
5. **Does Part A go in regardless of the Pole?**  It stands on its own — it
   removes the delivery-latency floor fleet-wide and takes work off both
   gateways — and it is the prerequisite for everything in Part B.

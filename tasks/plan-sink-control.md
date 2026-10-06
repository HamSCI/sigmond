# plan: per-client sink control — who stores, who ships, and how much disk each may hold

**Status — 2026-10-06.**  Michael agreed this design in conversation.  An adversarial review on
2026-10-06 then checked it against the code.  This revision carries the review's corrections, three
decisions Michael made after it, and one question that Claude settled during the review (§2, rows
D1-D4).  The
spec awaits his review, and nobody has built any of it yet.  On 2026-10-06 Michael confirmed that
wd30, which he runs, takes separate per-client uploads as it stands (§12).  It supersedes §1 of
[plan-upload-control.md](plan-upload-control.md), the three site-wide modes, and leaves that plan's
§2, adaptive shipping, standing.  The evidence comes from read-only code traces across every client
repo on 2026-10-06 (§13).  Nobody touched a station.

## 1. Why

On 2026-10-01 an operator put K3LR on hold with `smd config uploads disable --reason "station under
test"`. `smd upload status` reported the hold for four days.  During those four days psk-recorder's
own uploader, running inside the psk-recorder process, posted roughly 35,000 spots a day to
pskreporter.info.  The site switch never reached it.  The per-client lever, `smd config upload
psk-recorder K3LR --off`, wrote a flag that psk-recorder does not read.

On 2026-10-06 a second operator, running sigmond with meteor-scatter alone, asked why none of his
decoded MSK144 spots reached pskreporter.info.  Sigmond most likely seeded his instance into
`deposit` mode, its default for a new instance, which hands delivery to a wsprdaemon server.  The
code shows why nothing arrived.  On a host
without wspr-recorder nothing carries deposit rows to that server, and the cleanup timer deletes
psk.spots rows after 60 minutes, sent or not.  No health view checks either path.  This account
comes from the code alone; nobody examined his host.

Both failures share a cause.  A client's data can leave a station by more than one route, and no
single party knows or controls all of them.  Three code traces, run on 2026-10-06, mapped the
routes, the switches and the storage behaviour (§13).  They found:

- 14 outbound data paths across 7 clients.  The site switch governs only the paths that run
  inside the hs-uploader daemon.
- Six senders that never read the site switch: psk-recorder's and meteor-scatter's direct
  uploaders, wspr-recorder's in-process uploader and `wspr-uploader.service`, mag-recorder's
  fallback uploader, and hfdl-recorder's live feed.
- Senders that share one record of what they have sent.  Each selects different rows, so whichever
  runs first moves the record past rows the others have not sent.  By the code, those rows never
  ship.  Nobody has yet observed the loss on a station.
- A hold that does not hold.  The cleanup timer deletes psk spots after 60 minutes and WSPR spots
  after 24 hours, sent or not.
- A discard that leaks.  GRAPE packages each day a day late and re-packages up to a week back; the
  magnetometer packages whole days at 03:00 UTC.  Bench days become uploadable after discard ends.
- Switches that do nothing, and status views that report the setting rather than the behaviour.
- No coordination of disk among clients.  The contract carries storage figures one way only,
  from client to sigmond, and sigmond reads none of them.

## 2. What the operator gets

An operator can turn every outbound data flow off for the whole station, or for one client, with
one command, and can trust what the status says.  A newly installed client that can send data
stores nothing for a repository and sends nothing until someone turns it on.  Turning a client off
stops only what could reach a repository.  The client keeps the local data it needs, such as
hf-timestd's timing and raw IQ.  The design aims to keep every byte bound for a repository at the
site until someone has confirmed that the station receives well and that its data carry the right
callsign and location.

| Decision | Made by | Date |
|---|---|---|
| Each client with a route to a repository owns two switches: store the data bound for a repository, and, if storing, upload it on its own schedule (scoped by D2) | Michael | 2026-10-06 |
| Three states follow: `off` (store nothing for a repository, send nothing), `fill` (store, send nothing), `upload` (store and send) | Michael | 2026-10-06 |
| The client answers for every route its data takes, whatever the path | Michael | 2026-10-06 |
| Sigmond directs one client, or all of them at once through one site switch — the site spigot | Michael | 2026-10-06 |
| The stricter of the site switch and the client switch wins | proposed by Claude, accepted by Michael | 2026-10-06 |
| One switch covers a whole client, all its instances; the file shape leaves room for per-instance control later | Michael | 2026-10-06 |
| A new client with a route to a repository starts `off` | Michael | 2026-10-06 |
| The heartbeat reports each client as `sink off`, `sink filling` or `sink uploading` | Michael | 2026-10-06 |
| One upload pathway: the hs-uploader daemon, packaged with every client; in-process senders retire | Michael | 2026-10-06 |
| Storage: declare, grant, report; backlog divided by equal days of holdover | Michael | 2026-10-06 |
| wsprdaemon.org: each client sends its own per-cycle upload; the merged WSPR+FT8 upload ends | Michael | 2026-10-06 |
| D1.  The site switch acts as a ceiling over every client.  Opening the site lifts the limit but turns on no client that someone set `off` by name.  Before the site opens, sigmond runs the identity check for every client the opening would raise to `upload`, and refuses if any check fails (§3.2) | Michael | 2026-10-06 |
| D2.  `off` stops only data bound for a repository, meaning sink rows and upload packages.  Local data a client needs or keeps for itself continues.  A client with no outbound route carries no switch; it offers `sink status` and `sink limit`, and the heartbeat shows it as `local-only` (§4) | Michael | 2026-10-06 |
| D3.  In a WSPR merge fleet every instance feeds one sink, and the client's one switch governs them all.  The decode-only role disappears.  The carry-over reads what actually shipped, and the switch file records identity per instance (§6.4, §10.1) | resolved by Claude during the review; open to Michael's correction | 2026-10-06 |
| D4.  If wd30 cannot take separate per-client FT8, FT4 and MSK144 uploads, one merged upload per cycle stays.  It carries a client's rows only while that client's effective state reads `upload` (§6.6) | Michael | 2026-10-06 |

## 3. Ownership and the single pathway

### 3.1 Library and daemon

hs-uploader comes in two forms, and the same code does the sending in both.  The library holds the
parts that move data.  A source reads a queue, either a table in `sink.db` or a directory of files.
A transport talks to one destination.  A store keeps a send record for each pairing of source and
destination, which marks how far that source has shipped to that destination.  The code calls a
send record a cursor and keeps it in `/var/lib/hs-uploader/watermarks.db`; this spec says send
record throughout.  The same file holds a send log, the `attempts` table, with one line per send
attempt.  The daemon, `hs-uploader.service`, runs as one process per host.  It imports the library
and runs a set of pipelines.

Today a client's data leaves either through the daemon or from inside the client, where the client
imports the library and runs its own pipeline in a thread.  Two routes for the same data produced
the K3LR leak and the shared send record.  The project already chose the daemon in writing.
`hs-uploader/src/hs_uploader/daemon.py:1-6` says it "replaces the per-recorder in-process
uploaders", and `hamsci-physics/src/hamsci_physics/cli.py:479-485` calls it "the SINGLE outbound
path for every HamSCI client".  hamsci-physics followed.  psk-recorder, meteor-scatter,
wspr-recorder and mag-recorder still carry senders of their own.

This design keeps one route, the daemon, and makes it travel with every client.  Three jobs need
one sender per host.  One process must own the send records, so that turning a client on never
ships data from its `off` period.  A machine needs one PSWS key.  The adaptive shipping of
`tasks/plan-upload-control.md` §2 needs one place that sees the whole link.

### 3.2 Who owns what

**hs-uploader, the courier.**  One copy per host.  Whichever client arrives first installs it.  Its
installer runs safely more than once.  It holds the lock `/run/lock/hs-uploader-install.lock` while
it works, tolerates an existing user or group, creates `/etc/hs-uploader/pipelines.d` and
`/etc/hs-uploader/sinks`, and enables the unit.

- The library, as now.
- The daemon reads one pipeline file per client from `/etc/hs-uploader/pipelines.d/<client>.toml`
  instead of one list that sigmond renders (§3.5).
- The sink writer moves here from `sigmond.hamsci_sink`.  It uses only the Python standard library
  (`sigmond/lib/sigmond/hamsci_sink/writer.py:37-46`).  The sink stays at
  `/var/lib/sigmond/sink.db`, so row ids continue and no send record needs resetting.  A fresh file
  would restart the ids, and every stored send record would then skip all new rows.
  hs-uploader's `tmpfiles.d` entry creates `/var/lib/sigmond` (02775, root:sigmond) on every host,
  with or without sigmond.
- When a client's effective state allows storing and the sink cannot open, the writer logs once,
  reports `sink unavailable` in `sink status`, and alarms.  It never falls silently to a no-op, as
  it does today.
- Six clients declare hs-uploader as a dependency today.  codar-sounder, hf-tec and
  superdarn-sounder import the writer but declare neither sigmond nor hs-uploader.  Each adds
  hs-uploader to its `pyproject.toml` and `[tool.uv.sources]` and refreshes `uv.lock` when it
  converts (§10.2).
- Cleanup of the sink moves here from sigmond's storage-trim units (§6.3).
- The switch files live here: `/etc/hs-uploader/sinks/<client>.toml` and
  `/etc/hs-uploader/sinks/site.toml` (§3.4).
- Three library functions serve every reader and writer.  `effective_state(client)` reads both
  switch files and returns the state that applies.
  `stored_intervals(client, start, end, at_least="fill")` returns the periods in which the
  effective state stood at `at_least` or above: storing by default (§4.1), or uploading when a
  caller passes `at_least="upload"` (§6.6).  `write_switch(path, change)`
  performs every write to a switch file (§3.4).  The sink writer, the daemon and every client call
  the same functions.

**Each client, the owner of its data.**

- A client with an outbound route writes its own pipeline file at install and at every
  configuration change (§3.5), taking callsign, grid, reporter id and PSWS ids from its own
  configuration.
- A client with an outbound route owns its switch through its own command:
  `<client> sink status | off | fill | upload | check | limit`.  A client with none offers
  `sink status` and `sink limit` only (§4).
- It writes repository-bound data into the sink, or packages it for upload, only when its effective
  state allows storing.  The daemon carries that data out only when the effective state allows
  uploading.

**Sigmond, the orchestrator.**

- `smd sink <client> off|fill|upload` directs one client by calling that client's own command.  It
  passes every option through: `--drop-backlog`, `--accept-identity-change`, `--accept-position`
  and `--yes`.  If the client has no `sink` command, sigmond refuses (§3.6).
- `smd sink off|fill|upload`, naming no client, sets the site switch, which acts as a ceiling over
  every client (D1).  Lowering it holds every client at or below the new state.  Raising it lifts
  the limit but turns on no client that someone set `off` by name.  That client stays off until
  someone turns it on by name.
- Before sigmond raises the site switch, it finds every client whose effective state would become
  `upload` and runs that client's identity check, `<client> sink check --yes` (§4.2).  If any check
  fails, sigmond refuses to open the site and names each client that failed.  The operator then
  fixes the configuration or passes an override.  `smd sink upload` and `smd upload on` accept
  `--accept-identity-change <client>`, `--accept-position <client>` and
  `--drop-backlog <client>`, and each option reaches only the client it names.  Once the site
  opens, sigmond prints every client whose effective state stays below `upload`, and the switch
  that holds it there.
- `smd sink status` shows each client's report beside what the daemon actually sent (§8).
- `smd upload on|hold|off|discard` stays as an alias for the site command.  `on` sets the site to
  `upload`; `hold` and `off` set it to `fill`; `discard` sets it to `off`.  Because the alias sets
  only the site switch, `smd upload on` cannot turn on a client that someone set `off` by name.  The
  old command could, since it re-rendered every pipeline.  The alias's help text says so plainly,
  and its output names each client that stays off.
- `smd config uploads enable|disable` aliases `smd upload` until step 5 of §10.2, and then retires.
- Sigmond stops rendering `/etc/hs-uploader/pipelines.toml`.  It drops in a pipeline file for its
  own heartbeat, declared ungoverned (§3.4).
- Sigmond computes and delivers storage grants (§5).

### 3.3 The flow

```
 <client> process ──writes──► sink (sink.db rows or a spool dir) ──read by──► hs-uploader daemon ──► destination
        │ effective_state(): may it store?                                         │ effective_state(): may it send?
        └──────────── /etc/hs-uploader/sinks/<client>.toml  +  sinks/site.toml ────────┘
                                 ▲ changed only through write_switch() (§3.4)
```

The client's own `sink` command writes the client file, and sigmond may call that command.
`smd sink`, naming no client, writes the site file.

### 3.4 The switch files

Only `write_switch()` changes `/etc/hs-uploader/sinks/<client>.toml`.  The installer of a client
with an outbound route calls it to create the file at `off` when none exists.  The client's own
`sink` command calls it for every later change, and the carry-over calls it once (§10.1).  Every
service account can read the file.

```toml
client  = "wspr-recorder"
state   = "fill"                     # off | fill | upload
reason  = "checkout at the permanent site"
set_by  = "smd sink (mjh)"
set_at  = "2026-10-06T21:30:00Z"

[grant]                              # written by `<client> sink limit …` (§5)
working_bytes = 2000000000           # an absent key means no grant; 0 means zero
backlog_bytes = 5000000000
archive_bytes = 10000000000
pinned        = false                # true: sigmond never rewrites this grant
granted_by    = "sigmond"
granted_at    = "2026-10-06T00:00:00Z"

[[history]]                          # appended at every change, never rewritten
at     = "2026-10-06T21:30:00Z"
from   = "off"
to     = "fill"
by     = "smd sink (mjh)"
reason = "checkout at the permanent site"

[history.identity."AC0G=S"]          # one table per instance, in every entry that starts storing
callsign    = "AC0G/S"
reporter_id = "AC0G/S"
grid        = "EM38uw"
lat         = 38.92
lon         = -92.33

[history.identity."AC0G=B5"]
callsign    = "AC0G/B5"
reporter_id = "AC0G/B5"
grid        = "EM38uw"
lat         = 38.92
lon         = -92.33
```

wspr-recorder ships nothing to PSWS, so its identity tables carry no PSWS ids.  hamsci-physics and
mag-recorder add `psws = { station = "…", instrument = "…" }` to theirs.

`/etc/hs-uploader/sinks/site.toml` carries `state`, `reason`, `set_by`, `set_at` and the same
`[[history]]` list, without identity tables.  A site change can start storing for a client.  When
it does, sigmond calls that client's command, which appends its own history entry with
`by = "site"`.  The client file then records the identity in force when storing began.

`/etc/hs-uploader/sinks/` belongs to root:sigmond with mode 0755, and each file carries mode 0644.
Every write goes through one library function, `write_switch(path, change)`.  The client's installer
and its command, sigmond's site command and the carry-over all call it.  It takes an exclusive lock
on `sinks/.lock`, re-reads the file under the lock, appends the history entry, writes a temporary
file, syncs it to disk and renames it into place.  A reader therefore never sees half a file, and
two writers never lose each other's change.  `<client> sink off|fill|upload|limit` elevates itself
to root as `smd` does; `sink status` needs no root.

Rules for reading them:

- The effective state equals the stricter of the client state and the site state, in the order
  `off` < `fill` < `upload`.  Site `fill` with client `upload` yields `fill`; a client set to `off`
  stays off when the site opens.
- `effective_state()` and `stored_intervals()` read both files.  No reader uses one file's history
  alone.
- A missing client file means `off`.  The check fails closed.  §10.1 explains how existing
  stations get their files before enforcement begins.  Only a writer of repository-bound data and
  a governed pipeline ever ask, so a client with no outbound route never meets this rule.  One
  exception runs until step 4 of §10.2 converts a caller: the compatibility writer of §6.1 skips
  the check for a producer that has no switch file.  The daemon still treats that client as `off`,
  so nothing it stores can ship.
- A missing site file imposes no limit.  A standalone host has none.
- Only a missing file counts as missing.  Any other failure to read or parse either file, or a
  state outside `off`, `fill` and `upload`, makes the effective state `off`.  The reader logs it
  once, and both status views show ERROR and name the file.
- Readers check the file's modification time before each write batch and before each send, so a
  change takes effect within one cycle, about 30 seconds, and nobody restarts a recorder.
- Per-instance control, if it ever arrives, adds an optional `[instances.<name>]` table.  Nothing
  in this design depends on it.  Identity already sits per instance, in the history (D3); the state
  stays per client.

Station telemetry stands outside the switches.  Sigmond's heartbeat pipeline file,
`pipelines.d/sigmond-heartbeat.toml`, declares `governed = false`.  The daemon never calls
`effective_state()` for an ungoverned pipeline, so neither the site switch nor a missing switch
file stops it.  The heartbeat carries station status, never received data.  Today's renderer
exempts it in the same way (`sigmond/lib/sigmond/uploader_manifest.py:453`).

### 3.5 Pipeline files

hs-uploader's installer writes `/etc/hs-uploader/hs-uploader.toml`, which holds the host-wide
settings: the SSH key path, the pump interval and the wake socket.  A client's
`pipelines.d/<client>.toml` holds its `[[pipeline]]` entries and one `[identity.<instance>]` table
per instance, matching the switch file.  Each table carries callsign, grid, reporter id and PSWS
ids.  These identities apply to the client's own pipelines only, builder pipelines included, and
never set the key path.  A transport that posts every batch under one identity, as wsprnet does
(`hs-uploader/src/hs_uploader/transports/wsprnet.py:199-210`), runs as one pipeline per instance,
each filtered to that instance's rows.  The identity check of §4.2 compares these tables too.

The daemon loads every `pipelines.d/*.toml`.  At each pump it checks the files for a changed
modification time, and it re-reads them on SIGHUP.  It then rebuilds only the pipelines that
changed.  Queued deliveries survive a rebuild, because they live in `watermarks.db`.  The unit
replaces `ConditionPathExists=/etc/hs-uploader/pipelines.toml` with
`ConditionDirectoryNotEmpty=/etc/hs-uploader/pipelines.d` and drops the pinned `--manifest`.
hs-uploader's installer enables it.

Each pipeline takes its client from its file's name.  During steps 2 to 5 of §10.2 the daemon also
reads the legacy `pipelines.toml`.  Sigmond renders `client = "<name>"` into each legacy entry, and
once `pipelines.d/<client>.toml` exists the daemon ignores that client's legacy entries.  A client
removes its `[[hs_uploader.pipeline]]` blocks from `deploy.toml` in the release that adds its
pipeline file.

Two pipelines must never share a send record.  When two built pipelines share one key of source,
destination and table, the daemon refuses to start and names both.

### 3.6 Clients without a sink command, and removal

Some clients will lack a `sink` command for a while: those not yet converted in step 4 of §10.2,
third-party clients, and older pinned versions.  For such a client `smd sink <client>` refuses and
names the client's version, and `smd sink status` shows it as UNMANAGED.

`smd remove <client>` first asks whether to ship or drop the client's unsent backlog.  It accepts
ship only while the client's effective state reads `upload`; otherwise it refuses and names the
switch that holds the client.  When the operator chooses to ship, the removal waits until the
daemon has sent the backlog.  The removal then deletes `pipelines.d/<client>.toml`, calls
`hs-uploader sink drop <client>`, and moves `sinks/<client>.toml` to
`sinks/removed/<client>.<UTC time>.toml`.  The history survives, and a reinstall starts `off`.

A client with an outbound route counts as new whenever `sinks/<client>.toml` does not exist, and
its installer then creates the file at `off`.  A client with no outbound route keeps only
`sinks/<client>.grant.toml` (§5.4).  Sigmond recomputes the storage grants after a removal (§5.3).

## 4. What each change does to the data

One rule lies underneath every transition.  Data recorded while a client's effective state reads
`off` never enters the sink and never enters a later package, so it can never reach a repository.
`off` stops only that repository-bound data.  The client keeps receiving and decoding throughout,
and it keeps the local data it needs or keeps for itself.  That covers hf-timestd's timing and raw
IQ, the local JSONL archives of codar-sounder, hf-tec and superdarn-sounder, decoder logs, and
hfdl-recorder's local archive rows (§6.4).  `sink status` shows reception live, as decodes or
samples per minute and the time of the last data, so an operator can judge reception without
storing anything for upload.

A client with no outbound route carries no switch.  hf-timestd, gpsdo-monitor, station-web and
phase-engine send nothing off the station, so nothing they write can reach a repository.  They
implement `sink status` and `sink limit` only, and the heartbeat shows them as `local-only`.
GRAPE's switch belongs to hamsci-physics, which packages and sends GRAPE.  It does not belong to
hf-timestd, which records the samples GRAPE draws on.

| Change | What happens |
|---|---|
| `off` → `fill` | The client starts storing from that moment.  The history of whichever switch changed records the moment.  The client's own history records the identity in force for each instance (§3.4). |
| `fill` → `upload` | The identity check runs (§4.2).  The daemon then ships everything stored during `fill`, oldest first, and keeps up with new data.  `--drop-backlog` discards the stored data and ships only new data. |
| `upload` → `fill` | Sending stops before the next batch.  Storing continues.  Nothing gets lost. |
| `fill` or `upload` → `off` | Storing for upload stops.  Data stored but not yet sent stays on disk, and `sink status` reports its size and age range.  Only `--drop-backlog`, given deliberately, deletes it. |
| `off` → `upload`, with an old backlog | `sink status` shows the backlog's age and the identities recorded with it.  It ships unless the operator passes `--drop-backlog`.  The identity check applies. |
| site raised | Sigmond runs the identity check for each client the change raises to `upload`, and refuses the change if any check fails (§3.2).  A client set `off` by name stays off. |

### 4.1 Products packaged after the fact

GRAPE (hamsci-physics) and the magnetometer (mag-recorder) package their data some hours after
recording it.  Their packagers call the library function `stored_intervals(client, start, end)`.
It merges the client's and the site's histories into the intervals when the effective state
allowed storing.  A packager builds a product only from samples inside those intervals.  GRAPE's
packager reads the switch of hamsci-physics, `sinks/hamsci-physics.toml`.

The GRAPE packager masks rather than skips.  It zeroes the minutes outside stored intervals and
marks them invalid in the day's metadata and in `gap_summary.json`, the way an outage appears
today.  The magnetometer packager drops JSONL lines whose timestamp falls outside the intervals;
the JSONL samples themselves stay on disk as local data.  History times come from the host clock in
UTC, and a packager treats the minute on either side of a transition as off.

### 4.2 The identity check

A switch to `upload` first prints what the data will carry for each instance: callsign, reporter
id, grid, lat/lon and PSWS ids.  Two findings stop the switch.

1. An instance's configured identity differs from any identity recorded across the periods that
   the unsent backlog spans.  Someone edited the configuration, or moved the station, after storing
   began, so part of the backlog carries an old identity.  The switch refuses to ship that backlog
   unless the operator chooses `--drop-backlog` or passes `--accept-identity-change`.
2. The host publishes a measured position, today through gpsdo-monitor's files under `/run/gpsdo/`,
   and that position falls outside the configured grid square.  The check compares against the
   grid at the precision the configuration gives it.  The switch refuses until the configuration
   matches or the operator passes `--accept-position`.

At a terminal the check asks for confirmation.  Called by sigmond or a script, it needs `--yes`,
and it logs exactly what it accepted.  `--yes` replaces only the prompt.  Each blocking finding
still needs its own `--accept-*` option.  The check runs at `<client> sink upload`, as
`<client> sink check` when sigmond raises the site switch, and at bring-up.

The check has a blind spot.  A bench station may carry its permanent identity and lack a GNSS
receiver, and the check then sees nothing wrong.  So a bench uses `off`, never `fill`, and the
default for a new client makes `off` the natural starting point.

### 4.3 Storage while filling

`fill` means keep.  Cleanup therefore deletes only data that every reader has already sent (§6.3).
Unsent data stays until it ships, up to the client's backlog grant (§5).  At the grant the oldest
unsent data goes first, with a journal warning that names the bytes and the time span lost, and a
counter in the heartbeat.  It never goes silently.  Until grants arrive in step 6 of §10.2, each
client's backlog ceiling stands at 5 GB, an initial default set in
`/etc/hs-uploader/hs-uploader.toml`.

## 5. Storage: declare, grant, report

### 5.1 Today

Sigmond does not divide the disk among clients, and the contract holds no allotment.  §17 of
CLIENT-CONTRACT.md asks each client to describe its sinks with `retention_days` and `mb_per_day`.
Sigmond never reads `data_sinks`.  It parses `mb_per_day` only from the legacy `disk_writes` key,
for display, and sums nothing (`sigmond/lib/sigmond/clients/contract.py:215-220`).  Nobody built
the per-filesystem sum that §17.6 promised.  The declared figures also mislead.  hf-timestd declares
0 MB/day, while its own guardian budgets about 16.6 GB of raw IQ and 4 GB of phase2 per channel per
day (`hf-timestd/src/hf_timestd/core/resource_guardian.py:91-97`).

All clients share one filesystem in the appliance.  Each polices itself by age or file count.  Only
hf-timestd watches free space, and it watches the whole filesystem, so a neighbour's growth makes
hf-timestd delete its own data at 75-80 % and stop every metrology channel at 95 %.  hf-timestd's
cleanup also deletes GRAPE's spectrograms after seven days, because they sit in its directory tree.
GRAPE's unsent packages, the magnetometer's files and codar-sounder's sink rows grow without any
limit.  hf-tec writes no sink rows today.  Its writer omits the required `mode` argument
(`hf-tec/src/hf_tec/core/output.py:232`), the construction fails, and its JSONL holds its only
copy.

### 5.2 Three kinds of storage

| Kind | Examples | Sized by | Order of loss when the disk tightens |
|---|---|---|---|
| working | hf-timestd's raw IQ ring; decoder WAV slots | the client declares a fixed size; sigmond reserves it first | never; a client that cannot fit its working set stops storing and alarms |
| backlog | stored data not yet sent, in `fill` or during an outage in `upload` | sigmond, by equal days of holdover | second, oldest first, with an alarm |
| archive | data kept after sending for local use, and local data that never ships: spectrograms, `timestd.db`, phase2, JSONL archives | sigmond, from what remains | first, oldest first |

The contract names these kinds in a new `storage_class` field (§7).

### 5.3 How sigmond divides the disk

Let B stand for the storage budget, the filesystem's size times a usable fraction, less what the
operating system and logs occupy.  The fraction defaults to 0.85 and lives in `[disk_budget]` in
coordination.toml.

1. Reserve each client's working size, W_i.
2. Measure each client's backlog growth rate, g_i.  It counts the bytes per day the client writes
   into backlog-kind storage, measured over its last seven days in `fill` or `upload`.  It measures
   what the client produces, not the net change in its backlog, so a client on a healthy link still
   earns a backlog grant.  The declared rate applies until three such days exist.
3. Give every client the same holdover, D days: D = min(D_max, (B − ΣW_i) × backlog_share / Σg_i),
   and grant each client a backlog ceiling of g_i × D.  When Σg_i equals zero, D equals D_max.
   `D_max` and `backlog_share` live in `[disk_budget]`.
4. Divide what remains among the clients' archives in proportion to what each declares it wants
   to keep (its archive growth times its archive days).  When the remainder cannot hold every
   wish, all archives shrink by the same fraction.

When ΣW_i exceeds B, sigmond grants no backlog or archive, alarms, and leaves the working sets as
declared.

Every number here serves as an initial default until measurements on B4 replace it.  That covers
the usable fraction of 0.85, `D_max` of 30 days and `backlog_share` of 0.5.  The fraction sits below
hf-timestd's 95 % hard stop and above the 80 % `warn_percent` already in `[disk_budget]`
(`sigmond/lib/sigmond/coordination.py:155-157`).  A station that fills its grants therefore warns
well before anything stops.

Sigmond recomputes the grants daily, and whenever a client arrives or leaves (§3.6) or the budget
changes.  An operator may pin any client's grant by hand, with `pinned = true` in its `[grant]`
table.  Sigmond never rewrites a pinned grant, and it subtracts the pinned bytes from B before it
divides the rest.  A 1 MB/day spot client and a 40 MB/day GRAPE client both receive D days of
holdover; as the disk tightens, everyone's holdover shrinks together.

### 5.4 Delivery and enforcement

Sigmond delivers each grant through the client's own command, `<client> sink limit`, which writes
the `[grant]` table of the switch file.  A client with no outbound route has no switch file, so its
`sink limit` writes the same table, through the same function, to `sinks/<client>.grant.toml`.  The
client enforces the grant, counting only its own directories and its own rows in the sink, never
the whole filesystem.  The daemon's cleanup enforces the backlog and archive grants for rows in
`sink.db` (§6.3).  In the `[grant]` table an absent key means no grant, and the client then uses a
limit from its own configuration, as on a standalone host.  A value of 0 means zero.

hs-uploader's library tells a client what has shipped, through `sent.is_sent(client, item)` and
`sent.oldest_unsent(client)`.  A client that trims its own spool asks these before it deletes.
hamsci-physics' spool trim (`grape/spool.py`) switches to them in the same release as the GRAPE
ledger (§6.4).  They replace its direct read of the send records table.

### 5.5 What must change first

- Four hf-timestd limiters watch the whole filesystem today.  They comprise quota_manager
  (`quota_manager.py:91-95`, which `timestd-prune.service` also runs), ResourceGuardian at startup
  (`resource_guardian.py:211-262`) and at runtime (`resource_guardian.py:445-490`), and
  BinaryArchiveWriter (`binary_archive_writer.py:1016-1020`).  Each must measure hf-timestd's own
  subtree against its working and archive grants.
- hf-timestd's cleanup must stop walking the subtrees that hamsci-physics owns:
  `products/<channel>/decimated`, `products/<channel>/spectrograms`, `upload/`, `phase2/science/`
  and `phase2/ionex/`.  `phase2/<CHANNEL>/` stays hf-timestd's.
- Each limiter keeps a whole-filesystem floor as a last resort, the 95 % pause and the 100 MB
  headroom.  At that floor it pauses hf-timestd's own writes and alarms, but it deletes nothing
  outside its subtree.
- The sink cleanup must spare unsent rows (§6.3).
- psk-recorder's and meteor-scatter's rows need separate accounting in the shared table (§6.1).
- hs-uploader must count bytes per producer in `sink.db` (§6.3).

## 6. Enforcement on each path

### 6.1 The sink

The sink writer, now part of hs-uploader, enforces the store switch.  `pending_uploads` gains a
real column, `producer TEXT NOT NULL DEFAULT ''`.  When the column does not exist yet, the writer's
schema setup adds it with ALTER TABLE, along with an index on (target_db, target_table, producer,
id).  `Writer.from_env` gains a required `producer=` argument naming the client.

The writer checks `effective_state(producer)` at `insert()`, against each row's own time.  It splits
a batch that straddles a switch change and judges each row on its own.  In `off` the writer drops
rows inside `insert()` and otherwise stays live and healthy.  No client disables its sink object or
skips decoding because the state reads `off`.

Until a caller passes `producer=` (step 4 of §10.2), the compatibility writer infers the producer
from each row's table and its `mode` field.  psk.spots rows with mode msk144 go to meteor-scatter,
and other psk.spots rows to psk-recorder.  The compatibility writer enforces only when that
producer's switch file exists.

Some clients keep a local archive inside the sink.  hfdl-recorder's rows in `hfdl.spots` and
hf-timestd's rows in `timestd.events` serve that purpose, and no pipeline reads either table
(`sigmond/lib/sigmond/storage_trim.py:5-7, 91-93`).  Such a client opens its writer with
`local=True`.  The writer then skips the switch check and marks each row local in a second new
column, `local INTEGER NOT NULL DEFAULT 0`.  No source ever selects a row marked local, so a local
row can never reach a repository, and the store switch has nothing to guard there.

psk-recorder and meteor-scatter keep sharing the `psk.spots` table, as the wsprdaemon server
expects, but each row names its writer, which gives each client its own pipelines, its own byte
count and its own grant.  During carry-over, sigmond infers a producer for each older row from its
table and mode (§10.1).

The sources follow the column.  SqliteSource gains `producer=`, and applies it in its query, its
de-duplication, its starting point, its stale-schema probe, its `commit()` and its `source_id()`.
WsprCycleSource gains `producer` and `tables`, and applies them to cycle discovery, the per-cycle
fetch and the ceiling.

### 6.2 Pipelines and send records

- One pipeline per client and destination, each with its own send record.  hs-uploader keys a send
  record on source, destination and table, not on the pipeline name
  (`hs-uploader/src/hs_uploader/watermark/sqlite.py:34-40`), so the fix changes the key.
  `SqliteSource.source_id()` names its producer, for example `sqlite:psk.spots@psk-recorder`.  The
  shared `psk-pskreporter` pipeline splits into `psk-recorder-pskreporter` (FT8, FT4) and
  `meteor-scatter-pskreporter` (MSK144).  Exactly one sender covers any row, so no two pipelines
  whose filters overlap may share a destination.
- Send records move forward only.  The source defines the order, not the store, because each
  source encodes its send record its own way.  A plain byte comparison would rank `999` above
  `1000` and freeze the record.  Each source therefore gains `cursor_is_after(new, stored)`.  It
  compares integer ids for SqliteSource, integer nanoseconds for a FileTree spool kept after
  sending, and ISO times for WsprCycleSource, and it always answers true for a spool deleted on
  acknowledgement.  `advance_cursor` reads and writes under one lock.  It stores the new send
  record only when the source says it lies after the stored one, and otherwise logs a WARNING.
  `reset_cursor` and `sink drop` stay the only deliberate rewinds.  The forward-only rule guards
  against regressions; the separate keys of the first bullet fix the shared record.  GRAPE's send record
  becomes a set of names (§6.4), and the forward-only rule applies only to send records that hold
  one position.
- Each pipeline knows its client.  `Pipeline` gains `client: str`.  The daemon sets it from the
  `pipelines.d` file name, and for legacy entries during steps 2 to 5 from the `client` key that
  sigmond renders.  The daemon asks `may_send(pipe)` at the top of `_pump_one`, before each queued
  delivery it replays (the code calls them deliverables), and before each batch in the
  `_drain_source` loop.  In `fill` or `off` a refused pipeline returns without work.  It touches
  neither its send record nor its queued deliveries; they wait, and nothing gets dropped.  The
  `discard` flag and DiscardTransport retire when the gate goes live, and site `off` replaces them.
  A pipeline declared `governed = false` skips the check (§3.4).
- A new pipeline starts from its client's first stored row, not from "now".  Because `off` stores
  nothing bound for a repository, the sink holds only data from `fill` or `upload` periods, so the
  whole backlog ships.  Pipeline files never set `start_at = "now"`.  The factory default for
  `wspr_cycle` becomes `"beginning"`, and the daemon refuses `"now"` in `pipelines.d`.  With no
  send record and no predecessor key, a source starts at its producer's first stored row.
  SqliteSource starts above id 0 within its producer filter.  WsprCycleSource starts from an empty
  record.  A FileTree spool kept after sending starts above modification time 0.
- When a new key replaces an older one, a one-time migration copies the old send record to each new
  key before the first pump.  It prints the old key, the new key and the record, and then deletes
  the old row.
- `hs-uploader sink drop <client>` discards a client's backlog.  It sets each of the client's send
  records to the producer's newest row, deletes the client's queued deliveries, and deletes only
  rows that match its producer.  It runs while the daemon holds that client's pipelines idle.  The
  client's `--drop-backlog` option calls it.

### 6.3 Cleanup

The daemon runs cleanup in place of sigmond's four storage-trim units: `sigmond-storage-trim-all`
and the per-target `trim-hfdl`, `trim-timestd` and `trim-wspr`.  Sigmond's installer copies all
four onto every host today and enables only the first (`sigmond/install.sh:822-826, 1171`).
Sigmond stops shipping them and disables any enabled one on upgrade.

Each source exposes its row filter and a test, `sent_before(record)`.  Cleanup deletes a row only
when every declared reader whose filter matches it has sent it, and only while the client's sent
rows exceed its `archive_bytes` grant, oldest first.  The reader set comes from the declarations in
`pipelines.d`, not from the pipelines that built.  A declared pipeline that failed to build blocks
deletion and raises an alarm.  Until a client retires its in-process sender (step 4 of §10.2),
cleanup treats that client's rows as having an unknown reader and keeps today's age limit for them.
Where step 2 has stopped a client's in-process senders, that client has no unknown reader, and
cleanup spares its unsent rows like any other.

Two kinds of row have no reader.  Local rows never ship.  Governed rows that no declared pipeline
reads, such as those of codar-sounder, hf-tec and superdarn-sounder today, count as sent.  Cleanup
trims both kinds by their client's archive grant alone.  Until grants arrive in step 6 of §10.2,
cleanup keeps each retired trim unit's current retention for sent and local rows, and applies the
5 GB archive default from `/etc/hs-uploader/hs-uploader.toml` where no retention exists.

`delete_on_commit` defaults to false.  When true, it deletes only the pipeline's own producer's
rows.  Today it defaults to true and deletes across the whole table
(`hs-uploader/src/hs_uploader/pipeline_factory.py:109`,
`hs-uploader/src/hs_uploader/sources/sqlite.py:398-403`), so a pipeline file that omitted the key
would delete the other producer's unsent rows.

Files in spools stay with the client (§5.4).  The daemon deletes only in spools that a client grants
it through a `ReadWritePaths` drop-in (§7 item 9).

Cleanup then enforces each client's backlog grant, oldest unsent first, with the alarm of §4.3.  It
also covers the tables nobody trims today: `codar.spots`, `hf_tec.spots`, `hf_tec_codeless.spots`,
`timestd.events` and `hfdl.spots`.

`sink.db` stays one shared file.  A producer's usage means the sum of `length(payload_json)` over
its rows.  Deleting rows alone never shrinks the file, so the writer sets
`PRAGMA auto_vacuum=INCREMENTAL`, with one VACUUM on existing files, and cleanup runs
`incremental_vacuum`.

### 6.4 Per client

| Client | The store switch acts on | The upload switch acts on | Retires |
|---|---|---|---|
| wspr-recorder | rows written to `wspr.spots` and `wspr.noise` | its wsprnet and wsprdaemon pipelines | its in-process uploader (`wspr_recorder/hs_uploader_shim.py`), `wspr-uploader.service`, `WSPR_USE_HS_UPLOADER` and the decode-only merge role, once the shim's other duties move (below) |
| psk-recorder | its rows in `psk.spots` | its pskreporter pipeline and its own wsprdaemon pipeline | the `direct` in-process uploader, the per-slot `.spots.txt` fallback, `PSK_DELIVERY_PIPELINES`, `PSK_USE_HS_UPLOADER` |
| meteor-scatter | its rows in `psk.spots` | its pskreporter pipeline and its own wsprdaemon pipeline | the in-process uploader, the per-slot `.spots.txt` fallback, `METEOR_SCATTER_DELIVERY_MODE`, the `deposit` seed in `deploy.toml` |
| GRAPE (hamsci-physics) | packaging, per §4.1 | its PSWS pipeline, whose send record becomes a ledger of dataset names (below) | the no-op `[grape] upload` key and `--no-upload` |
| mag-recorder | packaging, per §4.1; its JSONL samples stay local | its PSWS pipeline | the fallback uploader in `mag-recorder-upload.service` |
| hfdl-recorder | nothing; its `hfdl.spots` rows form a local archive, written with `local=True` (§6.1) | its live feed to airframes.io, which runs only while the effective state reads `upload` | `sigmond-storage-trim-hfdl` (§6.3) |
| codar-sounder, hf-tec, superdarn-sounder | their sink rows; their JSONL archives stay local | nothing today; no pipeline reads their rows | — |
| hf-timestd | no switch (§4); its raw IQ, phase2, `timestd.db` and local `timestd.events` rows count as working and archive storage | nothing; GRAPE belongs to hamsci-physics | — |
| gpsdo-monitor, station-web, phase-engine | no switch (§4) | nothing | — |

hfdl-recorder runs its decoder with the feed output only while the effective state reads
`upload`.  A change to either switch file makes the client restart its own subprocess, so `off` and
`fill` behave alike for hfdl-recorder.  The rows it writes to `hfdl.spots` continue in every state,
because nothing can ship them.

No pipeline reads the sink rows of codar-sounder, hf-tec or superdarn-sounder today, so `fill` and
`upload` behave alike for those three until one does.  Their daily JSONL files, which their code
calls the canonical record, continue in every state.

GRAPE needs a different kind of send record.  FileTreeSource keeps one modification-time mark per
key today, so a day that the sweep packages late, after later days have shipped, would never ship.
GRAPE's send record therefore becomes a ledger, kept in `watermarks.db` as the table
`sent_items(source_id, dest_id, item_key, acked_at)`.  FileTreeSource gains `key = "name"`.  It
yields only datasets absent from the ledger and records each name on acknowledgement.  Re-packaging
never re-sends, and a day packaged late still ships.

wspr-recorder's in-process uploader does more than send.  Its outcome callback writes the per-spot
wsprnet audit that `smd verifier report` reads.  It also updates the wsprnet negative call cache,
which the callsign database uses to suppress decodes.  The shim further hosts the `WD_VERIFY_FLUSH`
wsprnet verifier and the wsprdaemon verifier.  The daemon builds its uploader with no outcome hook
(`hs-uploader/src/hs_uploader/daemon.py:168`), so retiring the shim as it stands would drop all four
silently.  They move first.  Either the pipeline file names per-pipeline outcome hooks that the
daemon imports from wspr-recorder, or a wspr-recorder observer reads the daemon's send log.

A WSPR merge fleet runs several wspr-recorder instances on one host, each writing into one sink
(D3).  Today only the merge instance uploads, and the others carry
`WSPR_USE_HS_UPLOADER=0`.  With the daemon as the only sender, that decode-only role disappears.
Every instance feeds the one sink, and the daemon's wspr pipeline waits for every expected reporter
as the merge instance does today.  The client's one switch governs all the instances.  Identity
stays per instance, inside the switch file's history (§3.4).

### 6.5 Destinations as client configuration

Which destinations a client serves becomes part of its configuration, expressed in its pipeline
file.  psk-recorder and meteor-scatter post to pskreporter.info from the station, through the
daemon's pskreporter pipeline.  Each sets `forward_to_pskreporter` false on its rows, and
hs-uploader carries that choice to wd30 in the tar's `routing.json` (§6.6).  The delivery-mode
environment variables go away.

wd30's forwarder could post on a station's behalf instead.  As late as 2026-09-30 one K3LR msk144
spot still asked it to, the mark of meteor-scatter's `deposit` mode.  This design leaves that path
closed, because as it runs today it loses spots in two ways.  It reads 500 rows at a time past a
watermark kept in whole seconds.  The server stamps each insert, often more than 1,000 rows, with a
single second, so the forwarder never reads the rest of that second.  And the ftlib library beneath
it drops any spot older than 50 minutes, so nothing held in `fill` survives that path.  The path
reopens only after Michael fixes both (§12).

### 6.6 wsprdaemon.org

Each client sends its own per-cycle upload to gw1 and gw2.  wspr-recorder's pipeline stops pulling
`psk.spots` rows into its WSPR upload (`include_psk` goes false), and psk-recorder and
meteor-scatter each gain a wsprdaemon pipeline of their own.  A station then sends up to three tars
per cycle.

wd30 takes these as it runs today; Michael confirmed it on 2026-10-06 (§12).  The server picks a tar
apart by the directories inside it and reads no meaning into the tar's name.  It takes WSPR spots
from `wspr/spots/`, noise from `wspr/noise/`, and psk rows from
`<mode>/<RX_SITE>/<RECEIVER>/<BAND>/*_<mode>.jsonl` for ft8, ft4 and msk144.  A tar that carries psk
rows alone passes every step.  The spot and noise scans find nothing, the psk rows go into
`psk.spots`, and the server deletes the tar.  meteor-scatter's msk144 rows already reach `psk.spots`
inside today's merged upload.

No source today can build a psk-only upload per cycle.  WsprCycleSource finds its cycles in
`wspr.spots` and `wspr.noise` and pulls psk rows only as riders on a WSPR cycle, so on a host
without wspr-recorder it yields nothing.  hs-uploader therefore gains a cycle source for
`psk.spots`.  It takes the tables, the producer, a cycle of 120 seconds and a shipping delay no
shorter than the decode latency plus the writer's 30-second flush.  It carries the producer in its
source id.  It yields exactly one batch per closed cycle, however many rows the cycle holds, as
WsprCycleSource does, and the tar takes its name from the cycle's start.  It skips a cycle that
holds no ft8, ft4 or msk144 row, since the transport would otherwise ship a tar with no data in it.

The tar's name matters in one way only.  Each gateway's reflector queues every station's uploads in
one flat directory keyed by bare file name, and wd30 remembers the names it has processed for about
six hours.  At either point the software drops a second tar that reuses a name, and logs the drop as
routine housekeeping.  So each client uploads under its own upload id, made from the station's SFTP
account name and a client suffix: `AC0G_B4` for wspr-recorder, `AC0G_B4_psk` for psk-recorder,
`AC0G_B4_msk` for meteor-scatter.  The base call alone would not do, since `AC0G_psk` from B4 and
from ND would collide.  No pipeline reuses a name for different content.  Every pipeline keeps the
SFTP login at the station call and uses the shared station key, so the gateways need no new account,
key or registration.

wd30 takes a receiver's identity from the directory path inside the tar.  The transport builds
`<RX_SITE>/<RECEIVER>` from each row's `reporter_id`, `host_grid` and `radiod_id`, so a per-client
tar files a spot under the same receiver as the merged tar did.  Each new pipeline also passes
`receiver=` to the transport, which otherwise refuses to build the tar.

wd30 ignores each row's own `forward_to_pskreporter`.  It reads the flag from the tar's
`routing.json`, one value per `<RX_SITE>/<RECEIVER>`, and forwards every row of a tar that lacks the
file.  The transport writes `routing.json` whenever a tar carries psk rows, so each client's choice
now travels alone.  In the merged upload, one client's opt-out silenced the other client's rows for
the same receiver.

Until step 3 of §10.2 ships, one merged upload per cycle stays, built by wspr-recorder's pipeline as
today.  A client's row rides in it only if the client's effective state read `upload` both when the
client stored the row and when the tar ships.  The pipeline learns the first from
`stored_intervals(client, start, end, at_least="upload")` and the second from
`effective_state(client)`.  Rows stored while psk-recorder or meteor-scatter sat in `fill` still
reach pskreporter.info later, through the client's own pskreporter pipeline, but never reach
wsprdaemon.org.  The same rule stands as D4's fallback, should wd30 ever stop taking separate
uploads.

### 6.7 Units outside the design

ka9q-radio's `make install`, which `smd` runs, copies four feeder units onto every station, among
others: hfdl, acars (to feed.acars.io), aprsfeed and horusdemod.  None belongs to a HamSCI client,
and each sends data off-site under any setting.  Its ft8 and ft4 decode units write only a
local log.  A `pskreporter@` unit, which ka9q-radio's docs describe but its tree does not ship,
counts as UNMANAGED if found.  Sigmond masks the sending units at install, and `smd sink status`
raises UNMANAGED if one ever runs.

## 7. Contract changes

A new section, §20 "Data sink control", in CLIENT-CONTRACT.md raises the contract to v0.9.  A
conforming client with an outbound route meets all nine items below.  A client with none meets
items 1, 6, 7, 8 and 9, and offers only `sink status` and `sink limit`.

1. Implements `<client> sink status [--json] | off | fill | upload | check | limit`, writing only
   its own switch file, through hs-uploader's library.
2. Writes `/etc/hs-uploader/pipelines.d/<client>.toml` at install and at every configuration change
   (§3.5).
3. Calls `effective_state()` before storing anything bound for a repository, and stores none of it
   in `off`.  Local data continues (§4).
4. Packages after-the-fact products only from stored intervals (§4.1).
5. Sends nothing itself.  Every outbound data path goes through the daemon.  A live feed with no
   store-and-forward (hfdl) obeys the effective state directly.
6. Reports, in `sink status --json`, the fields of §8.1.
7. Declares its working size and its archive wish in §17's `data_sinks`, which gains a
   `storage_class` field (`working`, `backlog`, `archive`).  The existing `kind` field (`file`,
   `service`) keeps its meaning.
8. Honours its grant, counting only its own directories and rows.
9. Runs hs-uploader's installer from its own installer, passing `--reader-group <its data group>`
   so that `hsupload` joins that group.  Drops
   `/etc/systemd/system/hs-uploader.service.d/<client>.conf`, with `ReadWritePaths=-` for each spool
   deleted on acknowledgement and `ReadOnlyPaths=-` for each spool kept after sending, then reloads
   systemd.  Marks every sigmond-owned path in its own unit files optional (a leading `-`), so it
   installs and runs on a host without sigmond.

Section 5 of this design replaces §17.6, which nobody built.  `smd config upload` and the flag table
in `lib/sigmond/upload.py` retire.

## 8. Showing what actually happens

At K3LR the status reported the setting while data flowed.  Every view in this design reports
observed behaviour beside the setting.

### 8.1 What each client reports

`<client> sink status --json`:

- switches: the client state, the site state and the effective state, with who set each one, when
  and why, or `local-only` for a client with no outbound route;
- identity in force for each instance, and the identities recorded across the unsent backlog,
  where they differ;
- reception, live even in `off`: decodes or samples per minute, the time of the last data;
- storage by kind: bytes held, the grant, the age of the oldest unsent item, days of headroom at
  the current rate;
- sending: the last send per pipeline, and the count sent since the last switch change.

Reception comes from a file, not from the recorder's memory.  Each recorder adds a `reception`
block, holding decodes or samples in the last minute and the time of the last datum, to the
per-minute file it already writes through `hamsci_dsp.timing.write_applied_state`.  `sink status`
reads that file.  mag-recorder reports from its spool's last line.  hamsci-physics reports the
freshness of hf-timestd's raw ring for the GRAPE channels.

### 8.2 What `smd sink status` adds

It compares each client's report against the daemon's send log and flags three conditions.

- **LEAK** — a send recorded for a client after a switch change that forbade it.  Only real
  deliveries count, meaning an outcome of `acked` or `partial_ack` on a transport that ships.  For
  hfdl's live feed, LEAK compares the decoder's command line with the effective state.
- **UNMANAGED** — something able to send outside this design.  The check flags:
  - an enabled ka9q-radio feeder unit;
  - a leftover in-process sender setting in a client's env file;
  - a pipeline file whose client has no switch file;
  - a client that lacks a `sink` command (§3.6) or does not answer `sink status`;
  - a pipeline file other than the heartbeat's that declares `governed = false`;
  - an ungoverned pipeline whose source differs from the heartbeat spool.
- **STALE** — a client's reception file or status older than five minutes.

Today's send log cannot attribute a send to a client.  It has no pipeline or client column, keeps
only the last 10,000 rows, and records outcomes that delivered nothing, such as `retry_later`.
Step 2 of §10.2 adds `pipeline` and `client` columns, filled by `record_attempt`, and a per-client
`last_send` table outside the ring, which keeps the evidence past the trim.

### 8.3 Heartbeat and fleetboard

One line per client.  The line shows the effective state, as `sink off`, `sink filling` or
`sink uploading`, and names the switch that sets it, client or site.  A client with no outbound
route shows `local-only`.  Each line also carries backlog size, the age of the oldest unsent item,
days of headroom, the last send and the leak flag.  The fleetboard shows the per-client line, not
only the site state.

### 8.4 Logging and alarms

Each client and the daemon log a switch change once, at the change.  A pipeline skipped because of
its state stays quiet in the journal and shows in status; a condition repeated at every send attempt
would bury the alarms that matter.

| Event | Journal | Heartbeat and fleetboard | `smd sink status` |
|---|---|---|---|
| leak | ERROR | flag | red |
| switch file unreadable, or holding an unknown state | ERROR, once, naming the file | flag | red |
| declared pipeline failed to build | ERROR, once, naming the pipeline | flag | red |
| oldest unsent data deleted at the grant | WARNING, naming the bytes and time span lost | counter | amber |
| headroom below one day | WARNING, once | value | amber |
| identity check blocked a switch to `upload` or a site opening | INFO, naming what differed | — | shown |

The five-minute limit for STALE and the one-day headroom warning serve as initial defaults until
measurements on B4 replace them.

## 9. Standalone operation

A client cloned and installed on a host without sigmond brings ka9q-python, to reach radiod, and
hs-uploader's library, daemon and sink writer, to store and send.  Its installer runs hs-uploader's
installer.  That creates the `hsupload` user, the `sigmond` group, `/var/lib/hs-uploader`,
`/var/lib/sigmond` for the sink, `/etc/hs-uploader/keys`, `pipelines.d` and `sinks`, and it installs
and enables the unit.  A client with an outbound route drops in its own pipeline file, and its
installer creates its switch file at `off`.  Its operator runs `<client> sink fill`, checks
reception and identity with `<client> sink status`, and then runs `<client> sink upload`.  No site
file exists, so the client's own switch governs alone, and its own configuration supplies its
storage limits.

The 2026-10-06 trace found that this principle holds today for reaching radiod and fails for
storing and sending (§13.2).  Each client installer must therefore meet five conditions.

1. Clone every `[tool.uv.sources]` sibling it lacks.  wspr-recorder and hf-tec fetch none today,
   and psk-recorder and meteor-scatter never fetch `hamsci-dsp`.
2. Run hs-uploader's installer first, then join the `sigmond` group it creates.
3. Build or fetch its native decoders (jt9, wsprd) through a shared script moved out of `smd`,
   which alone builds them today.
4. Write its own `[contract.instance_env]` defaults.  Without `WD_DECODE_VIA_DB=1`, which sigmond
   seeds today, wspr-recorder records but never decodes.
5. Take reporter id, callsign, grid and PSWS ids from its own configuration, and refuse
   `sink fill` until the configuration holds all of them.

hamsci-physics gains an `install.sh`; it has none today.  The client work in §10.2 repairs the rest.

## 10. Carry-over and rollout

### 10.1 Carrying existing stations over

One rule governs the carry-over.  What the operator allowed keeps shipping, and what the operator
held stays held, even where code ignored the hold until now.

- Carry-over runs wherever `topology.toml` lists an enabled client with an outbound route, or
  `sink.db` or `watermarks.db` exists.  Only a host with none of these counts as fresh, gets no
  carry-over, and starts its clients `off`.  A station whose clients ship only through in-process
  senders, with no legacy `pipelines.toml`, therefore still carries over.
- Sigmond runs it, from `smd align` and `smd update`, under hs-uploader's install lock,
  `/run/lock/hs-uploader-install.lock`.  It takes the client list from the enabled components in
  `topology.toml`, plus any producer with rows in `sink.db`.  It
  never takes the list from `pipelines.toml`, which names pipelines rather than clients and, under
  a hold, lists only the heartbeat.
- A client gets `upload` when either source shows it shipping: its send records or the send log
  show its data leaving, or the legacy list, rendered as if the site stood open, includes its
  pipelines.  The site file then carries any site-wide hold (below).  A client whose data shows no
  sign of shipping gets `fill`, so whatever it holds stays held.  A client with no outbound route
  gets no switch file.
- The carry-over ignores `WSPR_USE_HS_UPLOADER`, which in a merge fleet marks a decode-only role
  rather than a hold (§6.4).  It reads `PSK_USE_HS_UPLOADER=0` and `METEOR_SCATTER_USE_HS_UPLOADER=0`
  as operator holds, even though the code ignored them, and gives that client `fill`.  K3LR's
  `smd config upload psk-recorder K3LR --off` (§1) carries over this way.
- The carry-over records each instance's configured identity in a history entry with
  `by = "carry-over"`.
- It writes the site file from sigmond's `[uploads]` mode, when one exists.  `upload` becomes
  `upload`, `hold` becomes `fill` (K3LR today), and `discard` becomes `off`.
- Rows already in `sink.db` gain a producer inferred from their table and mode, and the
  carry-over marks the rows in `hfdl.spots` and `timestd.events` local (§6.1).
- Completion writes the marker `/var/lib/hs-uploader/carry-over-v1.done`, and the daemon enforces
  only once that marker exists.  On a fresh host the installer writes the marker at once, since
  nothing needs carrying over.  `smd align` and `smd update` print each client's before and after,
  and a second run changes nothing.

### 10.2 Build order

Each step ships on its own and leaves stations working.

1. **The shared send record, as a bug fix now.**  Give each sender its own key.  Put the producer
   in the source id, or the mode until §6.1 lands.  Where neither fits, give the sender a distinct
   transport `name`.  In the same release, migrate each existing send record to every key that replaces it
   (§6.2).  Leave one sender per set of rows.  Where the daemon's `psk-pskreporter` runs,
   psk-recorder's and meteor-scatter's in-process pskreporter senders stop.  Make send records move
   forward only.  By the code, this ends the spot loss wherever psk-recorder, meteor-scatter and
   the daemon run together.
2. **hs-uploader foundation.**  The sink writer moves in, with a compatibility import left in
   sigmond, and rows gain `producer` and `local`.  The daemon reads `pipelines.d/` alongside the
   legacy list (§3.5).  The switch files, `effective_state()`, the carry-over and enforcement arrive
   together.  Cleanup moves into the daemon and spares unsent data, except rows of clients whose
   in-process senders still run (§6.3), and `sink drop` arrives.  The
   send log gains `pipeline` and `client` columns and the `last_send` table (§8.2).  Until grants
   arrive in step 6, each client's backlog ceiling stands at the 5 GB default of §4.3.  From this
   step `smd upload` and `smd config uploads` write `site.toml` through `write_switch()`, and
   coordination.toml's `[uploads]` becomes read-only legacy.  In-process senders keep running until
   step 4 reaches their client.  On a held station sigmond stops them now:
   - it sets `PSK_DELIVERY_PIPELINES=server-raw` and `METEOR_SCATTER_DELIVERY_MODE=off`, so their
     rows wait in the sink behind the site gate with `forward_to_pskreporter` false.
     `server-merge` and `deposit` would mark every held row for wd30's forwarder
     (`psk-recorder/src/psk_recorder/core/recorder.py:553-555`,
     `meteor-scatter/src/meteor_scatter/core/recorder.py:427`).  That forwarder drops spots older
     than 50 minutes, and it would post fresh ones a second time beside the daemon's pskreporter
     pipeline (§6.5);
   - it stops and disables `wspr-uploader.service`, and sets `WSPR_USE_HS_UPLOADER=0` where the
     in-process wspr shim runs;
   - it sets mag-recorder's `[uploader] enabled = false`, which governs only the fallback sender.

   Each env change takes effect when sigmond restarts that recorder alone, never radiod. `smd sink
   status` lists each remaining in-process sender as UNMANAGED. 3. **Per-client wsprdaemon
   uploads.**  One release ships the psk cycle source, psk-recorder's and meteor-scatter's own
   wsprdaemon pipelines, and `include_psk = false` together.  Michael confirmed on 2026-10-06 that
   wd30 takes separate uploads as it runs (§12).  This step waits only on the wd30 test of §11.
   Step 4 may not move psk-recorder or meteor-scatter to `fill` before this step ships.  Until then,
   the gate on wspr-recorder's wsprdaemon pipeline drops psk rows whose producer's effective state
   does not read `upload`. 4. **Clients, one at a time.**  Each gains its `sink` command, its
   pipeline file, the store check, an installer that meets §9, and optional sigmond paths in its
   units, and each retires its in-process sender.  They convert in this order: psk-recorder and
   meteor-scatter first; then wspr-recorder, mag-recorder and GRAPE; then hfdl-recorder; then
   codar-sounder, hf-tec and superdarn-sounder.  wspr-recorder first moves its wsprnet audit, its
   negative call cache and both verifiers (§6.4).  hf-tec fixes its writer call, passing
   `mode="hf_tec"` and `producer="hf-tec"` and handing `insert()` a list of records.  codar-sounder,
   hf-tec and superdarn-sounder add hs-uploader to their dependencies (§3.2).  The GRAPE ledger and
   hamsci-physics' spool trim ship together (§5.4). 5. **Sigmond.**  `smd sink` arrives, with the
   status check for LEAK, UNMANAGED and STALE, per-client heartbeat lines, masking of ka9q-radio's
   feeder units, and `off` with the identity check at bring-up.  Sigmond stops rendering the
   pipeline list.  coordination.toml's `[uploads]` and `smd config uploads` retire. 6. **Storage
   grants.**  §20's declare-grant-report fields, per-producer byte accounting, measured growth,
   grants by equal holdover, and hf-timestd confined to its own subtree.  Until this step lands,
   each client keeps the fixed 5 GB backlog ceiling of step 2, and cleanup already spares unsent
   data, except rows of clients whose in-process senders still run. 7. **The contract.**
   CLIENT-CONTRACT.md gains §20.  This step marks §1 of `tasks/plan-upload-control.md` superseded
   and leaves its unbuilt §2 standing.

### 10.3 The first image: v3.69

Michael chose on 2026-10-06 to ship a small v3.69 ahead of the rest.  It carries only what stops a
new station from sending unverified data, and it moves the earlier v3.69 list to v3.70.

1. **Every fresh install starts with the site in `discard`.**  That mode comes nearest, in today's
   code, to the `off` that row 64 asks of a new client: nothing ships and no backlog builds.  The
   wizard writes `[uploads] mode = "discard"` into site-profile, which already parses the mode
   (`sigmond/lib/sigmond/site_profile.py:137, 273`); until now the wizard wrote none.  It tells the
   operator that the station records but sends nothing until someone runs `smd upload on`.
2. **The first `smd upload on` closes the doors that `discard` leaves open.**  `discard` acts when
   the daemon ships, so data packaged after the fact can still leak once it ends (§1).  Before it
   opens the site, sigmond moves aside every GRAPE package and magnetometer zip built before that
   moment, and prints what it moved.  It writes `upload/<day>/.upload_complete` for each earlier day
   that still has raw data, so GRAPE's catch-up sweep never re-packages a bench day
   (`hamsci-physics/src/hamsci_physics/cli.py:576-591`).  That also covers a station whose PSWS
   ids arrive later, since nothing from before the switch remains to ship.  One gap stays open
   until §4.1 lands: the GRAPE and magnetometer packages of the day of the switch itself still
   cover the whole day.
3. **Bring-up stops arming in-process senders.**  It sets `PSK_DELIVERY_PIPELINES=server-raw` and
   `METEOR_SCATTER_DELIVERY_MODE=off`, so their rows carry `forward_to_pskreporter` false and the
   daemon's pskreporter pipeline posts them under the site switch.  `smd upload on` then needs to
   re-arm nothing.  The daemon posts every row under the station's single identity, so this fits a
   station with one receiver per client; a host that runs several psk-recorder instances, as B4
   does, keeps its present settings until step 4.
4. **Step 1 of §10.2**, the separate send-record keys with their migration, and the forward-only
   rule.
5. **INSTALL.md, verified against v3.69.**  A new step after "check it's alive" turns uploads on:
   check the waterfall and the decodes, confirm callsign and grid on the station pages, then run
   `smd upload on`.  It says plainly that nothing recorded before that command leaves the station,
   apart from the GRAPE and magnetometer packages of that same day.  §12, on moving a station, keeps uploads off through staging, reconfigures the grid at
   the destination, and only then turns uploads on.

Existing stations keep shipping as they do.  v3.69 changes only what a fresh install starts with,
and the send-record fix.

## 11. Testing

- **A leak harness.**  It runs the daemon and a real client against stand-in destinations that
  record every byte, and drives every state and every transition.  It asserts the following.
  - Nothing leaves in `off` or `fill`.
  - `fill` → `upload` ships the backlog exactly once, under the right identity.
  - Nothing from an `off` period ever ships, re-packaging included.
  - With the client in `upload` and the site in `off` for a day, then the site in `fill`, nothing
    from the site-off day ships.
  - `--drop-backlog` drops what it should.
  - An identity mismatch blocks the switch, and blocks the site from opening.
  - Decoding continues in `off`, and local data keeps growing.
  - The send-record migration of step 1 neither re-sends nor skips a row.
- **Proof that the harness catches leaks.**  Remove the state check from the daemon, from the sink
  writer and from each packager, one at a time; the harness must fail each time.  A test nobody
  has watched fail proves nothing.
- **Storage.**  Cleanup never deletes unsent data; a grant drops the oldest data first and raises
  its alarm; grants sum to the budget; hf-timestd's limiters ignore a neighbour's growth.
- **Carry-over.**  Upgrade a host in each site mode and confirm that what ships stays the same.
- **On B4, the test bench**, after announcing on the claude-bus: watch the wire for each
  destination (pskreporter TCP 4739; SFTP to gw1, gw2 and PSWS; HTTPS to wsprnet) through every
  state; then a day in `fill` and a switch to `upload`, checked at wd30 and on pskreporter.info.
- **wd30, before step 3 ships.**  After announcing on the claude-bus, ship one cycle from B4 under
  `AC0G_B4_psk` and one under `AC0G_B4_msk`.  At wd30, confirm three things.  The server log shows a
  flush of psk rows with no spots and no noise.  The rows land in `psk.spots` under `AC0G=B4_EM38ww`
  / `AC0G-B4` with `forward_to_pskreporter` 0.  The WSPR tar for the same cycle still arrives.  No
  psk-only tar has passed through wd30 yet, so this test, not the code reading, closes the question
  in §12.

## 12. Dependencies and open questions

- **wd30 (Michael), answered 2026-10-06.**  wd30 takes separate per-client FT8, FT4 and MSK144
  uploads as it runs today, with no change on wd30 or the gateways.  Read-only checks of the running
  server (v2.27.0, psk ingest on, ft8, ft4 and msk144 enabled) found nothing that requires WSPR
  content in a tar.  Three conditions fall on the station side: names that never repeat, a
  `routing.json` in every psk tar, and per-row receiver identity (§6.6).  Nobody has yet watched a
  psk-only tar pass through, so the B4 test in §11 closes the question.  Step 3 of §10.2 no longer
  waits on anyone else.
- **wd30's pskreporter forwarder (Michael), open, outside this design's path.**  The server applies
  `forward_to_pskreporter` per tar and per receiver, through `routing.json`, never per row.  The
  forwarder loses rows past its 500-row batch when they share an ingest second, and ftlib drops
  spots older than 50 minutes.  Delivery stays at most once, since the watermark advances when spots
  enter ftlib's memory, not when they arrive.  Stations post directly under this design, so none of
  this blocks it.  All three need fixing before any station leaves pskreporter delivery to wd30
  again (§6.5).
- **Gateways (gw1, gw2).**  The reflector deletes any upload whose name matches a `delete_patterns`
  entry.  wd30's copy of the code defaults to `AI6VN_25*`.  Whoever runs the gateways confirms that
  no live pattern matches `*_psk_*` or `*_msk_*`.  Nothing else on the gateways changes.
- **PSWS:** does PSWS de-duplicate a dataset uploaded twice?  The design stops re-sending, but the
  answer bounds the harm of past re-sends.
- **wsprnet:** does wsprnet accept spots more than 24 hours old after a long `fill`?
- **pskreporter.info:** does it accept spots hours or days old?  On wd30's own path ftlib drops
  spots older than 50 minutes.  The station's direct transport sets no such limit, so the question
  stands for pskreporter.info itself.  If it refuses old spots, `fill` → `upload` for psk-recorder
  and meteor-scatter must age out spots past its limit, and `sink status` says so.
- **hfdl-recorder:** nobody has cloned the repo here, so the decoder's flags remain unverified.
  This spec takes the feed flag from sigmond's own account, in its hfdl trim unit
  (`sigmond/systemd/sigmond-storage-trim-hfdl.service:15-16`): dumphfdl ships frames with
  `--output decoded:json:tcp` to feed.airframes.io:5556.  Confirm it against hfdl-recorder.
- **Adaptive shipping** (`tasks/plan-upload-control.md` §2) stays unbuilt and outside this design;
  it fits naturally on the single daemon.

## 13. Evidence

All paths start at the repos root.  Every item comes from reading code on 2026-10-06, during the
first traces and the review.  Nothing ran on a station.

### 13.1 Egress and switches

- psk-recorder's direct uploader never reads the site policy: `psk-recorder/src/psk_recorder/core/recorder.py:714-748`; `coordination.env` carries no uploads key: `sigmond/lib/sigmond/coordination.py:557` (`render_env`).
- Bring-up writes `PSK_DELIVERY_PIPELINES=direct` whatever the site mode: `sigmond/lib/sigmond/bringup.py:339-350`, `sigmond/lib/sigmond/upload.py:35-38`.
- `smd config upload psk-recorder … --off` writes `PSK_USE_HS_UPLOADER`, which psk-recorder ignores; `smd config upload meteor-scatter` writes `METEOR_SCATTER_USE_HS_UPLOADER`, which no runtime code reads: `sigmond/lib/sigmond/upload.py:18-23`.
- `smd config upload` refuses mag-recorder, hamsci-physics, hf-timestd and hfdl-recorder with "has no upstream upload path", though all but hf-timestd upload: `sigmond/lib/sigmond/commands/config.py:1139-1142`.  hf-timestd uploads nothing since the 2026-08-24 split: `hf-timestd/deploy.toml:219-227`, `hf-timestd/src/hf_timestd/cli.py:336-344`.
- Shared send records: hs-uploader keys a send record on source, destination and table (`hs-uploader/src/hs_uploader/watermark/sqlite.py:34-40`; `hs-uploader/src/hs_uploader/core.py:407-410, 465-467`), and the pipeline name keys only the queued deliveries (`hs-uploader/src/hs_uploader/core.py:334`).  `SqliteSource.source_id()` ignores `extra_where` (`hs-uploader/src/hs_uploader/sources/sqlite.py:331-332`); `PskReporterTcp` has a fixed default name (`hs-uploader/src/hs_uploader/transports/pskreporter.py:127`).  So psk-recorder's in-process sender, though named `psk-recorder-<rid>`, shares its key with the daemon's `psk-pskreporter` (`psk-recorder/src/psk_recorder/core/hs_uploader_shim.py:186, 193`).  `advance_cursor` overwrites blindly (`hs-uploader/src/hs_uploader/watermark/sqlite.py:164-180`).
- meteor-scatter's `deposit` seed and the daemon pipeline that refuses deposit rows: `meteor-scatter/deploy.toml:21-22, 146`.
- mag-recorder's fallback ships whenever hs-uploader is inactive at 03:00 UTC: `mag-recorder/systemd/mag-recorder-upload.service:38`.
- An unresolved PSWS id skips a pipeline in every mode, discard included: `sigmond/lib/sigmond/uploader_manifest.py:226-234`.
- GRAPE's sweep re-packages days 2-7 back and bumps modification times past the send record: `hamsci-physics/src/hamsci_physics/cli.py:576-591`, `hs-uploader/src/hs_uploader/sources/files.py:113-120`.
- hamsci-physics hands its spool to the daemon and keeps `--no-upload` only as a no-op: `hamsci-physics/src/hamsci_physics/cli.py:479-497`.
- The heartbeat never obeys the upload policy, and a hold renders it alone: `sigmond/lib/sigmond/uploader_manifest.py:453, 466-480`.
- ka9q-radio installs acars, aprsfeed, hfdl and horusdemod units (`ka9q-radio/service/Makefile:10-14`); acars posts to feed.acars.io (`ka9q-radio/service/acars.service.in:17`); the ft8 decode unit "only needs the file system, not the network" (`ka9q-radio/service/ft8-decode.service.in:3`); `pskreporter@` appears only in the docs (`ka9q-radio/docs/ft8.md:84-85`).

### 13.2 Standalone

- hs-uploader travels with six clients as a library only; no client installer runs `hs-uploader/install.sh`; only sigmond renders the pipeline list (`sigmond/lib/sigmond/uploader_manifest.py:54`).  The daemon reads exactly one file (`hs-uploader/src/hs_uploader/daemon.py:45, 80-82`; `hs-uploader/systemd/hs-uploader.service:15, 30`), loads it once, and handles only SIGINT and SIGTERM (`hs-uploader/src/hs_uploader/daemon.py:147-168, 214-215`).
- hs-uploader's installer installs the unit but never enables it, and adds `hsupload` only to the timestd, wsprrec and pskrec groups: `hs-uploader/install.sh:183-185, 192-200`.  Its `tmpfiles.d` entry creates only `/var/lib/hs-uploader`: `hs-uploader/tmpfiles.d/hs-uploader.conf`.
- The writer falls silently to a no-op when it cannot write to `/var/lib/sigmond` (`sigmond/lib/sigmond/hamsci_sink/writer.py:10-16, 234-235`), and SqliteSource does the same when the file does not exist (`hs-uploader/src/hs_uploader/sources/sqlite.py:120-138`).
- No client declares `sigmond`, yet six import `sigmond.hamsci_sink`: e.g. `psk-recorder/src/psk_recorder/core/ch_tailer.py:563`, `wspr-recorder/wspr_recorder/spot_sink.py:161`.  codar-sounder, hf-tec and superdarn-sounder declare neither sigmond nor hs-uploader in their `pyproject.toml`.
- wspr-recorder skips decoding when the sink is unavailable: `wspr-recorder/wspr_recorder/__main__.py:467-468`.  It decodes only with `WD_DECODE_VIA_DB=1`, which sigmond seeds: `wspr-recorder/deploy.toml:18-23`.
- psk-recorder's and meteor-scatter's installers never fetch `hamsci-dsp`: `psk-recorder/scripts/install.sh:144-146`, `meteor-scatter/scripts/install.sh:149-151`.  Only `smd` builds jt9 and wsprd: `sigmond/bin/smd:1445`.  hamsci-physics has no installer: `sigmond/etc/catalog.toml:79`.
- Units require sigmond-owned paths without the optional prefix: e.g. `meteor-scatter/systemd/meteor-scatter@.service:95`, `hs-uploader/systemd/hs-uploader.service:77-78`.

### 13.3 Storage

- Age-only trim: `sigmond/lib/sigmond/storage_trim.py:94-106, 263-277`.  No pipeline reads `hfdl.spots` or `timestd.events`; the sink holds them as an archive: `sigmond/lib/sigmond/storage_trim.py:5-7, 91-93`.  sigmond's installer copies the per-target trim units onto every host and enables only `-all`: `sigmond/install.sh:822-826, 1171`.
- Sigmond reads no `data_sinks`; it parses `mb_per_day` from the legacy `disk_writes` key for display: `sigmond/lib/sigmond/clients/contract.py:215-220`.  `data_sinks` already uses `kind` for `file` and `service`: `sigmond/docs/CLIENT-CONTRACT.md:2060, 2067, 2082, 2170-2172`.
- hf-timestd's budget per channel per day: `hf-timestd/src/hf_timestd/core/resource_guardian.py:91-97`.
- hf-timestd's whole-filesystem limiters: `hf-timestd/src/hf_timestd/quota_manager.py:91-95` (also run by `hf-timestd/systemd/timestd-prune.service:19`), `hf-timestd/src/hf_timestd/core/resource_guardian.py:211-262, 445-490`, `hf-timestd/src/hf_timestd/core/binary_archive_writer.py:1016-1020` and its 100 MB floor at `:1038`.
- hf-timestd deletes GRAPE spectrograms after seven days: `hf-timestd/src/hf_timestd/quota_manager.py:161-178` walks `products/<channel>/spectrograms`, and the seven days come from `quota_manager.py:72` and `hf-timestd/systemd/timestd-prune.service:23`.  hamsci-physics intends to keep them: `hamsci-physics/src/hamsci_physics/cli.py:543`.
- hf-timestd writes `phase2/<CHANNEL>/` itself (`hf-timestd/src/hf_timestd/paths.py:169-182`); hamsci-physics writes only `phase2/ionex/` and `phase2/science/` (`hamsci-physics/src/hamsci_physics/physics_fusion_service.py:141, 157`).
- hf-tec writes no sink rows: `Writer.from_env(table=table)` omits the required `mode` and the error gets caught, and `insert()` receives a dict where the writer expects a sequence: `hf-tec/src/hf_tec/core/output.py:232, 241`; `sigmond/lib/sigmond/hamsci_sink/writer.py:204-216, 263`.
- `[disk_budget]` holds only `root_path` and `warn_percent`: `sigmond/lib/sigmond/coordination.py:155-157`.
- One guest filesystem in the appliance: `sigmond-appliance/firstboot-v3.sh:2145-2154`.

### 13.4 Send records, sources and the daemon

- Each source encodes its send record its own way: ASCII integer ids (`hs-uploader/src/hs_uploader/sources/sqlite.py:106-107`), integer-nanosecond modification times or a constant for spools deleted on acknowledgement (`hs-uploader/src/hs_uploader/sources/files.py:113-120, 178-185`), and ISO cycle times (`hs-uploader/src/hs_uploader/sources/wspr_cycle.py:394-396`).
- Every current declaration starts at "now": `psk-recorder/deploy.toml:125`, `meteor-scatter/deploy.toml:138`, `wspr-recorder/deploy.toml:165, 192`; the factory defaults `wspr_cycle` to "now": `hs-uploader/src/hs_uploader/pipeline_factory.py:122`.
- `Pipeline` carries no client, and `_pump_one` replays queued deliveries before it drains the source: `hs-uploader/src/hs_uploader/core.py:182-215, 326-336`.
- `delete_on_commit` defaults to true and deletes across the whole table: `hs-uploader/src/hs_uploader/pipeline_factory.py:109`, `hs-uploader/src/hs_uploader/sources/sqlite.py:398-403`.
- The send log has no pipeline or client column and keeps 10,000 rows: `hs-uploader/src/hs_uploader/watermark/sqlite.py:31, 43-53, 184-209`.
- WsprCycleSource discovers cycles from WSPR rows only and carries a fixed source id; the tar name comes from the upload id and the cycle time: `hs-uploader/src/hs_uploader/sources/wspr_cycle.py:1-15, 126-127`, `hs-uploader/src/hs_uploader/transports/wsprdaemon.py:961-976`.
- hamsci-physics' spool trim reads GRAPE's send record straight from `watermarks.db`: `hamsci-physics/src/hamsci_physics/grape/spool.py:19-35`.
- The GRAPE day file keeps a per-minute `valid` flag, and the packager writes `gap_summary.json`: `hamsci-physics/src/hamsci_physics/grape/decimated_buffer.py:8-12, 55`, `hamsci-physics/src/hamsci_physics/grape/packager.py:209, 338`.
- Identity varies per instance: `wspr-recorder/deploy.toml:24-28` (reporter call per instance), `psk-recorder/src/psk_recorder/cli.py:365-381` (reporter id, with a hostname fallback), `hamsci-physics/src/hamsci_physics/grape/packager.py:223-229` (PSWS station and instrument in the dataset path).
- wspr-recorder's shim registers an outcome hook; the daemon registers none: `wspr-recorder/wspr_recorder/hs_uploader_shim.py:347, 552`, `hs-uploader/src/hs_uploader/daemon.py:168`.
- Recorders already write a per-minute state file: `hamsci-dsp/src/hamsci_dsp/timing.py:500`.

### 13.5 wd30 and the gateways

Paths marked `wd30:` lie on the server.  Claude read them over ssh on 2026-10-06 with read-only
commands, and took the ClickHouse figures from queries bounded by time.

- The server dispatches on directories inside the tar, never on its name: `wd30:~/wsprdaemon-server/wsprdaemon_server.py:1510-1523, 1022-1029, 1221-1228, 574-589`.  It ingests a psk-only tar and deletes it after the flush: `:1732-1821`.
- psk ingest runs live: the drop-in `wd30:/etc/systemd/system/wsprdaemon_server@.service.d/ingest-psk.conf` and the process environment carry `WSPRDAEMON_INGEST_PSK=1`.  No `--config` appears, so `psk_modes` keeps ft8, ft4 and msk144: `:46`.
- The name serves only as a silent dedup key: wd30's processed list, about six hours long (`:424-426, 1683-1692`), and the reflector's flat queue (`wd30:~/wsprdaemon-server/wsprdaemon_reflector.py:693-700, 732-744`).
- The tar name and the SFTP login come apart: `hs-uploader/src/hs_uploader/transports/wsprdaemon.py:956-976`.  routing.json: `:683-693, 729-734`.  The transport requires `receiver=`: `:901-930`.  WsprCycleSource ignores the batch limit: `hs-uploader/src/hs_uploader/sources/wspr_cycle.py:134`, `hs-uploader/src/hs_uploader/core.py:413-417`.
- The server overwrites the row flag from routing.json: `wsprdaemon_server.py:522-555, 627`.
- The forwarder's batch boundary: `wd30:~/wsprdaemon-server/pskreporter_forwarder.py:216-224, 434-437` and `wsprdaemon_server.py:489`.  In the hour to 18:22Z, 42 of 51 ingest seconds held more than 500 rows.
- ftlib's age cutoff: `pskreporter_forwarder.py:324`, `wd30:/opt/wsprdaemon-server/venv/lib/python3.13/site-packages/pskreporter.py:139-146`.
- The delivery modes that set the row flag: `psk-recorder/src/psk_recorder/core/recorder.py:553-555`, `meteor-scatter/src/meteor_scatter/core/recorder.py:427`.

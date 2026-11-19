# Drift report: how a station differs from its release — design

Date: 2026-11-18.  Status: approved by Michael, 2026-11-18.  Target: sigmond v3.94.
Ledger: `station-drift-report` (ops).

## Why

On 18 November two stations that `smd align` called current still differed from the release in ways
that mattered.  AC0G-ND decoded MSK144 with a hand-built jt9 that its config named directly; KPH2
decoded it with the WSJT-X build, a CPU hog, because its UberSDR build predated MSK144.  The same day
turned up archive and health timers that a release declared but stations lacked, and leftover
meteor-scatter instances under an old escaped name on both canaries.  Each was found by accident.

`smd align`'s dry run compares only git checkouts and image-carried host files against the blessed
manifest, and only when someone runs it by hand on one station.  Michael (18 November): a station's
difference from its release should show without anyone asking.

## What the report covers

For one station, against the release it claims (the blessed manifest, or the candidate a canary
recorded), the report lists each difference as one item, in these kinds:

1. **Source.**  A component's checkout behind, ahead of, or diverged from its pin; a component the
   release names that the station lacks.  (What `smd align` reports today.)
2. **Builds.**  A built program whose build stamp does not match its checkout: the decoders
   (`decoders/ubersdr`, `decoders/wsjtx`), radiod, ka9q-web.  A build missing a capability the release
   expects of it (the UberSDR jt9 without `--msk144`).
3. **Units.**  A systemd unit a component's `deploy.toml` declares (or sigmond's own install declares)
   that is not linked or not enabled on the station; a unit enabled for a component the topology
   disables.
4. **Overrides.**  A config key that points a component away from the release's own choice: a decoder
   path, a repo URL in `/etc/sigmond/catalog.toml`, a pin in `component-pins.toml`.  The report names
   the key, the file and the value.
5. **Strays.**  Anything under sigmond's managed paths that no component or release accounts for: a
   hand-built decoder folder, a unit instance whose name no reporter id produces (the escaped
   `AC0G\x3dB4`), a checkout no catalog entry names.
6. **Host side.**  For a Proxmox station, `pm-align`'s comparison of host files, carried into the
   VM's report so one place shows both.

Each item carries a severity: `drift` (differs, likely harmless), `fault` (changes behaviour: a
missing unit, a stale build, an override of a decoder), and a one-line fix (`smd align --apply`,
`smd archive add --upgrade`, remove a file, drop a key).

## How it runs and where it goes

- `smd align` (dry run) prints the full report; `smd align --json` emits it for machines.
- `smd check` gains one line: drift none, or the count by severity with the first fault.
- A daily timer (`sigmond-drift.timer`) writes the report to `/var/lib/sigmond/drift.json` and hands
  a summary row to hs-uploader for sigmondb (`station_drift`: reporter, release, counts by kind and
  severity, fault items), on the same route as every other sigmondb row.
- The fleetboard gains a `drift` check: a fault opens an alarm naming the station and the item; drift
  shows on the board without alarming.  `smd fleet status` shows each station's release and drift
  counts in one table.

## One command per question (Michael, 18 November: "reconcile or simplify")

Four commands answer close variants of "what does this station run, and is it right": `smd version`
(alias `versions`, Rob 11-11), `smd component list`, `smd align` (dry run) and `smd status`.  After this
work each question has one command:

| Question | Command | Change |
|---|---|---|
| What does this station run? | `smd version` (`versions` stays an alias) | absorbs `component list`'s lifecycle state and version-policy columns |
| How does it differ from its release? | `smd align` (dry run) | becomes the drift report above |
| What could I install? | `smd component list --catalog` | unchanged |
| Is it healthy? | `smd status`, `smd check` | unchanged scope; `smd check` gains the one drift line |

Plain `smd component list` prints `smd version`'s table with a note that points there, for one release,
then retires.  The CLI reference, the operator and administrator docs, and the TUI's "Software
versions" screen follow the same split.

## Intended differences, and the reminder to undo them

A difference someone means stays visible but does not alarm while it lasts:

- A canary records its candidate; the report compares against that candidate, not the blessed
  release.
- `/etc/sigmond/drift-exceptions.toml` lists accepted items, each with a reason, who accepted it, a
  kind and an expiry.  `smd align --accept <item> --reason "..." [--test] [--until DATE]` writes one.
- **A temporary test** (`--test`: a different implementation, a trial setting, a hand-built program)
  defaults to 7 days, at most 30.  Michael (18 November): we need reminding to restore a test to
  baseline.  So from the day before it ends the board shows a reminder naming the station, the test,
  who started it and the command that restores baseline (`smd align --apply`, the key to drop, the
  folder to remove); once it ends the item alarms as a fault until someone restores it or renews the
  test with a new reason.
- **A standing exception** (no `--test`: a site that genuinely needs a different setting) defaults to
  30 days, at most 180, and alarms again when it expires.
- The report lists accepted items apart, with their remaining time.

## Not in scope

- Repairing anything automatically.  The report names the fix; `smd align --apply` and the other
  verbs stay the way to act.
- Configuration that legitimately varies by site (callsign, grid, receivers, channel choices).
  Overrides cover only keys that replace a release's own choice of software or source.

## Decided (Michael, 18 November)

- **Daily** suffices.
- **A fault does not hold back a fleet roll.**  It should prompt a fix on that station; `smd fleet
  update` proceeds and names the stations with open faults in its summary.
- **Temporary tests remind their owner to restore baseline** (above).

## Amendment 2026-11-19: the board learns each station's identity from the station

Michael, 19 November: the fleetboard showed AB4EJ after the station became AB4EJ-RX888, and
KPH's map icon sat in the ocean.  Both came from names and grids typed into `ops/fleet.toml`.
He approved this approach: the board takes each station's name and position from what the
station reports about itself, and the fleet file keeps only how to reach each host.

- The daily `station_drift` row (Task 9) also carries the station's identity: a stable
  `host_key` (the first 12 hex digits of the SHA-256 of `/etc/machine-id`, which survives a
  rename), the hostname, and one entry per receiver on the host: reporter id, radiod id, grid6
  and position source (`gpsdo` or `operator`).  Only the grid6 leaves the station in this row;
  the precise position stays in the station's configuration snapshot.
- `ops/bin/fleet-map-export` builds `fleet.station_map` from the newest row per `host_key`.
  The station's current reporter ids name it; every other id seen for the same `host_key`
  becomes an alias, so a rename keeps its history on the board.
- `fleet.toml`'s `[board.*]` entries keep authority only for stations that send no row
  (KJ6MKI, W0WWV, hosts not yet on this release).  Where both exist, the station wins and the
  export prints the difference so the hand entry can go.

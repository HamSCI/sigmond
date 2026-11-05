# The source switchboard: patching clients to the radiods a host hears

> **Audience:** contributor
> **Status:** design; builder check done, Michael's two questions answered 2026-11-05; awaiting Rob on §9 items 3-5
> **Written:** 2026-11-05, from a conversation with Michael (AC0G)
> **Builds on:** [2026-10-30-multi-receiver-station-design.md](2026-10-30-multi-receiver-station-design.md)
> (Rob approved it 2026-10-30; this note supplies the operator interface its
> §2 left for later), [2026-10-31-radiod-rename-design.md](2026-10-31-radiod-rename-design.md),
> [2026-11-04-bare-metal-install-design.md](2026-11-04-bare-metal-install-design.md)

---

## 1. Why

Most stations run one radiod on the same machine as sigmond.  Bring-up there
needs no decision: one radiod, one set of clients, one reporter ID, and it
should stay automatic.

ka9q-radio does more than that.  radiod sends its streams by multicast, so a
client on any machine can listen to any radiod it hears.  Rob's radio network
at KFS (a shared bridge and VXLAN between three Proxmox hosts, live since
2026-11-04) makes radiods in separate VMs hear one another, so this stops being
a curiosity.  An operator then has real choices to make, and makes them more
than once:

- at install, which of the radiods on the network this host's clients should use;
- later, when adding a client to a radiod, stopping one, or pointing one at a
  different radiod;
- later still, when a new SDR arrives and needs a radiod of its own.

Today sigmond answers the first question by itself.  On a client-only host,
bring-up keeps every radiod whose advertised name starts with the station's
callsign.  On B2 this morning that rule would have bound firebat's airspyhf
beside the two B1 radiods, with uploads switched on, because all three
advertise "AC0G".  The later questions have no answer short of
`smd admin instance` and `smd admin sources` commands typed in the right order.

Michael's picture for the answer comes from the old telephone switchboard.  The
operator sees every line that could connect, and patches any two ends together.

## 2. Goals, non-goals, anti-goals

**Goals**

- G1.  One board shows the radiods this host hears, the client instances
  configured on this host, and the patches between them.
- G2.  From the board an operator patches a client to a radiod, unpatches it,
  stops or starts it, and moves it to another radiod.
- G3.  The board offers a local SDR that has no radiod yet, and leads the
  operator through giving it one.
- G4.  Bring-up proposes patches on the same board, and the operator confirms
  or edits them.  A host with one local radiod still comes up with no
  question asked.
- G5.  Reporter IDs follow one simple convention (§5), and nothing renames an
  ID behind the operator's back.
- G6.  Each patch carries its metadata (antenna first), which the operator
  keeps current, and the board reminds the operator when a change may have
  made it stale.

**Non-goals**

- Making radiods audible across machines.  Rob's radio network does that at
  KFS; on a plain LAN the switch and IGMP querier do it (`docs/networking.md`).
  The board lists what the host hears and nothing more.
- The merge itself.  Merging moves to the wsprdaemon servers (§5.3); the board
  never sets up a merge instance in wspr-recorder.
- New upload formats.  wspr, psk and meteor-scatter uploads keep the
  established spot fields.

**Anti-goals** (failures even if everything "works")

- A bring-up that binds radiods the operator never chose.  The airspyhf case
  above.
- An ID that changes because the station grew.  Adding a second source never
  renames the first.
- Two records of the same patch that can disagree.  The board edits one
  record (§4) and renders everything else from it.
- A board that lies about liveness: a patch to a radiod the host no longer
  hears must show as such, not as healthy.

## 3. What the operator sees

```
 RADIODS HEARD                         CLIENTS ON THIS HOST
 ─────────────────────────────         ──────────────────────────────────
 ● ac0g-b1-a   AC0G B1-a longwire  ──┬─ wspr-recorder  AC0G/B1A   ● run
   (ac0g-b1, LAN)                    ├─ psk-recorder   AC0G/B1A   ● run
                                     └─ hf-timestd                ● run
 ● ac0g-b1-b   AC0G B1-b T3FD      ──┬─ wspr-recorder  AC0G/B1B   ● run
                                     └─ psk-recorder   AC0G/B1B   ○ stopped
 ● airspyhf    AC0G with airspyhf      (no patch)
 ○ ac0g-s      sigma   (not heard)  ── meteor-scatter AC0G/S     ⚠ waiting
 ◌ RX888 0x8a3f…  (local, no radiod)   [n] give it a radiod
 ─────────────────────────────         ──────────────────────────────────
 [p] patch  [u] unpatch  [s] stop/start  [m] move  [e] metadata  [n] new radiod
```

- **Left column:** every radiod the host hears by mDNS, local or remote, with
  its advertised description and the machine it runs on.  A radiod that has
  gone silent stays listed, marked "not heard", for as long as anything stays
  patched to it.  Local SDRs without a radiod appear at the foot.
- **Right column:** every client instance on this host, its reporter ID, and
  its state.
- **A line between them:** a patch.  The instance listens to that radiod.

**Actions.**

- **Patch.**  Pick a radiod and a client type.  The board proposes a reporter
  ID (§5), shows the capacity verdict for the host (multi-receiver design §4),
  and on confirmation creates the instance, binds it, renders, and starts it.
- **Unpatch.**  Stops the instance and removes it.  Its recorded data and
  state stay on disk unless the operator asks for them gone.
- **Stop / start.**  Pauses an instance without removing it.  The pause
  survives render, a radiod's return and a reboot (§4).
- **Move.**  Points a receiver at another radiod: every client on it moves
  together, and the reporter ID stays.  This answers "I moved the antenna to
  another SDR".  To send one client elsewhere, the operator unpatches it here
  and patches it there, where it takes that receiver's ID.  The board asks
  whether the metadata still holds (§6).
- **Metadata.**  Opens the receiver's metadata for editing (§6).
- **New radiod.**  Runs the existing walk: `smd admin sdr identify` to tell
  which card hangs on which antenna, then `smd config init radiod`.  The new
  radiod then appears in the left column, ready to patch.  Renaming a radiod
  follows `smd admin radiod rename` (rename design).

**Where it runs.**  Michael's guidance: the browser displays, the TUI displays
and acts, and that split need not stay fixed.

- The TUI (`smd tui`, Textual) gets the board as a screen, with every action.
- Each action also exists as a plain `smd` command, so the board stays one face
  among several and scripts can do anything it does (sigmond runs headless
  first).
- station-web shows the same board read-only, from a root-written snapshot,
  the pattern it already uses.  It never patches (Michael, 2026-11-05):
  he means to open station-web to public view, so it stays read-only.  That
  also rules out the command-request spool the multi-receiver design §7c
  proposed for its merge tab; the merge gets set from the TUI or the CLI.

## 4. One record: the receiver list

The multi-receiver design already defines the record this board needs: the
`[[receiver]]` list in `/etc/sigmond/site-profile.toml`.  A receiver joins one
radiod, one reporter ID and one antenna description; an optional `clients`
key narrows which clients run on it.  `smd config render` turns the list into
per-instance configs and `coordination.env` facts.

The board edits that list and nothing else:

| Board action | What changes in the list |
|---|---|
| patch the first client to a radiod | a new `[[receiver]]` |
| patch another client to the same radiod | its name added to that receiver's `clients` |
| unpatch a client | its name removed from `clients`; the last one removes the receiver |
| move | that receiver's `radiod` changes |
| stop / start | the client's name added to or removed from that receiver's `paused` list; render and every start leave a paused instance down |
| metadata | that receiver's `antenna` (and later fields, §6) |

A receiver has one radiod, as in the multi-receiver design.  That design also
says a radiod serves at most one receiver; this note proposes changing that
(§4.1).

### 4.1 Several receivers on one radiod

radiod serves whoever asks, and Michael can picture one radiod feeding two
receivers to compare two decoders or two algorithms.  The multi-receiver design
gives no reason for its one-receiver rule, and the plumbing does not need it:

- ka9q-python names a channel by a hash of its settings and the radiod, not of
  who asks.  Two instances that want the same channel share one radiod channel
  and both read its multicast stream.  Instances that want different settings
  get separate channels, which is ordinary work for radiod.
- wspr-recorder, psk-recorder and meteor-scatter never remove a radiod channel
  when they stop, so one instance stopping cannot cut the other off.
- Several reporter IDs already write to one spot database on one host (B4 ran
  three wspr-recorder instances).

Two things do need a rule, and the rule goes there instead:

1. **One timing instance per radiod.**  hf-timestd removes its channels when it
   stops and publishes one chain-delay correction for the radiod it times.  Two
   timing instances on one radiod would tear down each other's channels and
   publish two corrections.  So at most one receiver on a radiod carries timing.
2. **A shared antenna says so.**  Two reporter IDs on one radiod mean one
   antenna reporting twice.  wsprnet and the server's merge would count them as
   two independent receivers.  A receiver that shares its radiod with another
   names it (`same_antenna_as = "<reporter ID>"`), and render keeps that
   receiver's spots local (the sink's `local = 1` rows, never uploaded) unless
   the operator turns its uploads on.

On the board, patching a second reporter ID onto a radiod that already serves
one asks whether this is a comparison receiver, records `same_antenna_as`, and
offers to keep its spots local, the default.  Michael agreed to this change on
2026-11-05; it changes Rob's Phase 3 rule, so it waits on him (§9).

**What this replaces.**  The bare-metal work of 2026-11-05 records a remote
radiod through `smd admin sources bind`, which writes a `[radiod."<dns>"]`
block and `[[clients.X]]` bindings into `coordination.toml`.  Two records of one
patch would drift apart, so `sources bind` becomes a writer of a `[[receiver]]`
entry, and render produces the coordination facts from the list.  The radiod
blocks stay as rendered output, not as a second place to edit.

**Dependency.**  The receiver list belongs to Phase 3 of the multi-receiver
design, which nobody has built yet (Phase 1 shipped 2026-10-30; Phase 2 sits on
the unmerged branch `radiod-only-profile`).  This board needs Phase 3's data
model and receiver-driven `build_plan`, but not its capacity gate or
hf-timestd's per-instance work (Phase 3b).  Who builds Phase 3, and in what
order with this board, needs a word with Rob (§9).

## 5. Reporter IDs

### 5.1 The convention (Michael's)

- A station's first source reports under the bare callsign: `AC0G`.
- Each further source takes a suffix: `AC0G/B1B`, `AC0G/S`.  The board offers
  one derived from the radiod's name, and the operator may type another.
- Adding a source never renames an existing one.  When a second source
  arrives, the board offers once to rename the first (`AC0G` to
  `AC0G/<suffix>`), with "keep `AC0G`" as the default.  The operator may take
  it; nothing obliges her to.

Reporter IDs keep the spelling rules the multi-receiver design §3 states, and
the board writes them through the existing canonical forms
(`smd admin instance canonical`), never a new one.

### 5.2 What sigmond stops trying to do

Hams change stations and antennas all the time.  sigmond cannot keep an ID's
meaning stable across those changes and should not try.  The metadata records
what an ID meant at a given time (§6).

### 5.3 The merge lives on the server

Michael, 2026-11-05: the merge moves out of wspr-recorder to the wsprdaemon
servers, where it can merge across every station one operator runs, not just
the receivers on one host.  That also settles a conflict the convention would
otherwise raise: AC0G runs several one-source stations, and each could report
as bare `AC0G`.

- The merge serves operators who contest for the most spots, not science.
- Each reporter's own uploads stay in the wsprdaemon databases under its own
  ID.  Science reads those.
- A merged upload to wsprnet under `AC0G` may mix spots from `AC0G` and
  `AC0G/B1B`.  That stays acceptable even though `AC0G` also names a real
  source.

The declaration stays the one the multi-receiver design §7c gives:
`merge_into` on each receiver, rendered into the uploader's routing facts.  A
merge never gets inferred from a shared callsign prefix.  The board shows each
receiver's `merge_into` and lets the operator set or clear it.

**What changes against the multi-receiver design:** that design keeps the
station-side merge (`WD_MERGE_REPORTERS`) supported beside the server merge,
"either/or, not both".  Michael's direction retires the station-side merge.
The board never offers it, and existing stations that use it (the AC0G-B4
fleet) move to `merge_into` on a schedule Rob and Michael set.  How the server
groups receivers from different stations under one merged call belongs to
Rob's server stream (§9).

## 6. Metadata

Michael's rule: the operator keeps the metadata locally and amends it when
something changes, such as a new antenna.

What travels today:

- wspr, psk and meteor-scatter spot uploads carry the established spot fields
  only.
- Antenna, machine, operating system, timing authority, gain switches and the
  like travel only with the digital_rf uploads to PSWS.
- Some of that will soon go to wsprdaemon.org as well, stored and handled
  apart from the spots.

What the board does:

- **One home per receiver.**  The receiver's `antenna` field holds the
  operator's description.  The radiod `description` in site-profile and the
  commented `antenna` line in each instance template already exist; the board
  reads the receiver's field and keeps those in step on render rather than
  inventing a third place.
- **A reminder, never a gate.**  After a patch, a move or a rename, the board
  shows the receiver's metadata and asks whether it still holds.  The
  operator may dismiss it.
- **A dated history.**  Each board action appends one line to a local log
  (`/var/lib/sigmond/receiver-history.jsonl`: time, action, receiver, reporter
  ID, radiod, antenna before and after).  Whatever later carries metadata to
  wsprdaemon.org can read what an ID meant on a given date.  (My call; cheap,
  and it answers the history question without policing IDs.)

## 7. Bring-up

- **One local radiod, no remote ones named:** unchanged and automatic.  One
  receiver, reporter ID from `--reporter` or the bare callsign, every default
  client patched to it.
- **Interactive, anything else:** bring-up draws the board with its proposed
  patches already in place: every local radiod, plus every remote radiod whose
  advertised name starts with exactly the station's callsign.  The operator
  confirms, removes or adds patches, then bring-up proceeds.
- **Unattended (`--non-interactive`) on a host with no local radiod:** binds
  only the radiods the operator names with `--remote-radiod`, and with none
  named it lists what it hears and stops (Michael, 2026-11-05).  Shipped
  ahead of the board, in sigmond after 95221c9.

Reporter IDs at bring-up follow §5.1.  Today `reporter_id_for` gives every
remote source a suffix, the first one included; under the convention the first
source gets the bare call.

## 8. Order of work

1. Phase 3 data model of the multi-receiver design: `[[receiver]]`, render, and
   `instance add --radiod`, with `sources bind` rewritten as a receiver
   writer (§4).
2. The board's actions as `smd` commands, each a thin edit of the receiver list
   plus render plus unit control, with the history log.
3. The TUI board screen over those commands.
4. Bring-up drawing the board (§7) and the reporter-ID convention (§5.1).
5. The station-web read-only board from a snapshot.
6. `merge_into` on the board, once Rob's server side can merge across stations.

Proving it: B2 against B1's two radiods and firebat's airspyhf (patch,
unpatch, move between B1-a and B1-b, a stop held across a B1 radiod outage);
sigma with one local radiod (bring-up asks nothing); a new-radiod walk on a
spare RX888.

## 9. Open questions

**Answered by Michael, 2026-11-05**

1. An unattended bring-up on a host with no local radiod binds only the
   radiods the operator names; it never binds automatically (§7).
2. station-web stays read-only: Michael means to open it to public view
   (§3).

**For Rob**

3. Phase 3 of the multi-receiver design: who builds it, and does this board
   ride on it or wait for it?
4. Server merge across stations: does the server merge by the declared
   `merge_into` call alone, whichever station's tar a receiver arrives in?
5. Retiring the station-side merge (§5.3): when, and in what order for the
   AC0G-B4 fleet?
6. Several receivers on one radiod (§4.1): does the replacement rule (one
   timing instance per radiod, a shared antenna declared and kept local by
   default) cover what the one-receiver rule protected?

## 10. Calls I made after the builder check

Michael can overrule any of these.

1. **Move works per receiver** (§3).  A receiver stands for one antenna's
   path, so its clients move together and keep the ID.  Moving a single
   client means unpatching it and patching it elsewhere.
2. **A paused instance lives in the receiver list** as `paused = [...]`
   (§4), so the one record also holds what an operator stopped, and render,
   a radiod's return or a reboot cannot quietly start it again.
3. **Clients that are not per-reporter.**  hf-timestd appears patched to its
   timing radiod with no reporter ID; until the multi-receiver Phase 3b makes
   it per-instance, the board allows one such patch per host, and offers move
   (rebind its radiod) and stop/start, nothing more.  Clients with no radiod
   (mag-recorder, station-web, hamsci-physics) appear in a strip below the
   board with stop/start only.
4. **Existing hosts.**  The first time the board opens on a host with no
   `[[receiver]]` list, it builds one with `smd config render --init`, as the
   multi-receiver design §4 already specifies, and shows the result before
   saving.  An instance it cannot map (a station-side merge instance, an
   orphan) appears greyed out and read-only, so the board never writes a
   second record of it.
5. **Renaming a reporter ID starts a fresh instance** under the new ID.  The
   old instance's state and recorded data stay where they are under the old
   ID, and the history log records the change.  This follows from §5.2:
   sigmond does not try to carry an ID's meaning across a change.
6. **Capacity verdict.**  The board shows the multi-receiver capacity gate's
   verdict once Phase 3 builds that gate; until then the patch dialog shows
   none.
7. **Descriptions flow one way.**  For a local radiod, the receiver's
   `antenna` wins and render writes it into the radiod `description` and the
   instance template.  A remote radiod's advertised description belongs to its
   own host; the board shows it read-only.
8. **KiwiSDR receivers** appear on the board if declared, but patching them
   waits for the multi-receiver Phase 4c.
9. **A patched radiod never heard** (named at bring-up, say, before it came
   up) shows "not heard" from the start.  Only a radiod heard in the last
   browse shows as live.

## 11. Left to the builder

- Key bindings, colours and layout of the TUI screen, within the picture in §3.
- Command names for the board's actions (they extend `smd admin receiver` or
  `smd admin instance`; either works).
- The history log's exact fields beyond those §6 names.
- How often the board re-browses mDNS, and how long a silent radiod shows
  "not heard" before it counts as gone when nothing patches to it.

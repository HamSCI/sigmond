# Multi-receiver stations: local and remote RX888s under one Sigmond host

> **Audience:** contributor
> **Status:** APPROVED by Rob 2026-10-30 ("go forward with implementation"); plans follow per phase in ../plans/
> **Written:** 2026-10-30 against sigmond `wizard-v384`
> **Target station:** KFS (first deployment); the design is general
> **Companions:** [MULTI-INSTANCE-ARCHITECTURE.md](../../MULTI-INSTANCE-ARCHITECTURE.md),
> [RADIOD-IDENTIFICATION.md](../../RADIOD-IDENTIFICATION.md),
> [plan-multi-rx888-sources.md](plan-multi-rx888-sources.md) (2026-05, partly superseded)

---

## 1. Intent

A Sigmond station today is one host, one RX888, one radiod, one reporter.
KFS is not that. The target is:

```
 KFS decoder host (Sigmond, full profile)          Beelink (Sigmond, radiod-only profile)
 ┌───────────────────────────────────────┐         ┌──────────────────────────────────┐
 │ RX888 "SE"   -> radiod@kfs-se         │         │ RX888 "NW" -> radiod@kfs-nw      │
 │ RX888 "OMNI" -> radiod@kfs-omni       │         │ RX888 "SW" -> radiod@kfs-sw      │
 │                                       │  LAN    │                                  │
 │ wspr-recorder@KFS-SE   psk-recorder@..│<=multicast===  (no decoders, no uploader)   │
 │ wspr-recorder@KFS-OMNI psk-recorder@..│  switch │                                  │
 │ wspr-recorder@KFS-NW   psk-recorder@..│         └──────────────────────────────────┘
 │ wspr-recorder@KFS-SW   psk-recorder@..│
 │ hf-timestd@<reporter>, one per radiod │
 │ hs-uploader: one tar, rows per rx     │
 └──────────────┬────────────────────────┘
                │ every receiver's spots, tagged KFS-SE / KFS-OMNI / KFS-NW / KFS-SW
                v
 wsprdaemon servers (wd10/20/30): declared merge -> "KFS"; dedup; forward to
 wsprnet.org (WSPR) and pskreporter.info (FT8, FT4, and now WSPR too)
```

Two further capabilities ride on the same model (added 2026-10-30 after
the first draft):

- **KiwiSDR receivers, WSPR only.** Some sites (ON5KQ; KFS itself has four)
  have one or more KiwiSDRs with up to eight channels. Not enough channels
  for FT8/FT4, but they contribute WSPR spots today under wsprdaemon, and
  they must be able to under Sigmond, merged on the server with the rest.
- **The operator declares merges from the station.** The merge command
  rides the uploader's status-and-command channel in the tar. The server
  half and the envelope are designed (tasks/plan-operator-commands-via-
  uploader.md) and the narrow `routing.json` form exists; what is missing
  is an operator interface in Sigmond: a status and command tab on the
  station web page.

Two things make this different from how wsprdaemon runs KFS today:

1. **The station never deduplicates or forwards.** Every receiver's spots
   go to the WsprDaemon servers under that receiver's own reporter ID.
   Merging into one "KFS" identity, dedup, and forwarding to wsprnet.org
   and pskreporter.info are server-side, declared by the KFS operator
   (Rob) in the server's routing configuration. The server-side proxy
   upload exists and is being debugged on WD3 as of this writing.
2. **One host decodes receivers it does not own.** The Beelink runs
   radiod for two RX888s and nothing else. The KFS host subscribes to
   those radiods over the LAN exactly as it subscribes to its own.

Rob's words on the upload side: "the spots from each of the receivers at
the site are going to be bundled and uploaded to the wsprdaemon servers
... configured to merge them and create one KFS ... that server would
then dedup and forward one deduped stream to wsprnet.org, and for FT8
and FT4 it would dedup and upload to pskreporter.info." And: "we haven't
been uploading WSPR spots to PSKReporter but I think it will accept
them ... so PSKReporter has a complete set."

## 2. Goals, non-goals, anti-goals

**Goals**

- G1. A Sigmond host runs N radiods for N locally attached RX888s, each
  pinned to its card by USB serial, with the serial-to-antenna mapping
  confirmed by the operator rather than assumed.
- G2. A Sigmond host can be installed in a **radiod-only** role: ka9q-radio,
  radiod instances, host tuning, SDR recovery and watchdogs, mDNS. No
  decoder clients, no sink, no uploader.
- G3. A decoder host binds per-reporter client instances to any radiod it
  can see on the LAN, local or remote, one reporter per receiver.
- G4. Every receiver's spots reach the WsprDaemon servers tagged with that
  receiver's reporter ID, and the station posts nothing directly to
  wsprnet.org or pskreporter.info.
- G5. The server merges declared receivers into one station identity,
  dedups, and forwards WSPR to wsprnet.org and WSPR + FT8 + FT4 to
  pskreporter.info.
- G6. `smd check`, the USB watch table, the radiod watchdog and
  `sigmond-sdr-recover` all understand "which card is which radiod" on a
  multi-card host.
- G7. A KiwiSDR can be declared as a WSPR-only receiver with its own
  reporter ID, decoded by wspr-recorder on the host, and merged on the
  server like any other receiver.
- G8. The operator declares, on the station, which receivers merge into
  which reported identity for wsprnet.org and pskreporter.info, and sees
  the state of that declaration, without reaching the WD servers.

**Non-goals**

- Airspy or SDRplay sources (KFS has them under wsprdaemon; out of scope).
- FT8/FT4, hf-timestd or any non-WSPR client on a KiwiSDR receiver.
- One client process decoding several radiods. The shipped
  one-process-per-reporter model stands; a reporter has exactly one
  radiod in this design. The multi-source-per-instance path in
  `smd admin sources apply` stays deferred.
- Multicast across routers or WANs. The Beelink sits on the same
  IGMP-capable switch as the KFS host (Rob, 2026-10-30).
- The TUI. CLI and bring-up first; TUI screens are additive later.

**Anti-goals** (would make this a failure even if it "works")

- Two antennas silently swapping identities after a reboot or a replug.
  That is exactly what unpinned serials do today on a two-card host.
- Station-side dedup on a server-merged group. The diversity tier on the
  server needs every receiver's rows, and a receiver merged in two places
  gets two different "best SNR" answers. (Stations that merge on the
  station today, the AC0G-B4 fleet, are addressed in §8 item 3a and §11.)
- Merging inferred from a shared callsign prefix. The server rule
  (wsprdaemon-server CLAUDE.md, 2026-10-27) is that merging is declared.
- A radiod-only host that quietly grows decoders, a sink or an uploader
  because a profile defaulted them on.
- A new radiod serial check that keeps a one-card station down when the
  configured serial does not match (see radiod_config.py header for the
  retired fork patch and why).

## 3. Constraints

- radiod identity is the mDNS control/status name and nothing else
  (RADIOD-IDENTIFICATION.md). Remote and local radiods are
  indistinguishable to a client.
- Reporter IDs are `[A-Z0-9][A-Z0-9-]*[A-Z0-9]`; the first hyphen becomes
  `/` at the WSPRnet boundary. `KFS-SE` uploads as `KFS/SE`, which is the
  name KFS uses today under wsprdaemon.
- Per-instance file layout is locked (MULTI-INSTANCE-ARCHITECTURE.md §4).
- The RX888 DFU bootloader presents no usable serial; radiod binds "first
  matching DFU device" during bootstrap. With N cards, bootstrap must be
  one card at a time.
- The multicast switch needs an IGMP querier or streams die after the
  snooping timeout (networking.md).
- Sigmond does not fork ka9q-radio. Anything radiod itself cannot do goes
  to Phil as a patch, not into this design.
- The server side lives in `wsprdaemon-server`, operated by Rob. This
  design states the contract; the server work is its own stream.

## 4. The station declaration (the one new input)

Everything below hangs off one addition to `/etc/sigmond/site-profile.toml`:
a list of receivers. A receiver is one antenna, one radiod, one reporter.

```toml
[[receiver]]
reporter_id = "KFS-SE"
radiod      = "kfs-se-status.local"      # mDNS status name; local or remote
antenna     = "TCI-537B LP, 3-30 MHz, 15 dBi at 135 deg"
# sdr_serial is NOT here: it belongs to the radiod block on the host
# that owns the card (coordination.toml [radiod.<id>].sdr_serial).

[[receiver]]
reporter_id = "KFS-OMNI"
radiod      = "kfs-omni-status.local"
antenna     = "TCI-530 omni, 2-30 MHz, 6 dBi"

[[receiver]]
reporter_id = "KFS-NW"
radiod      = "kfs-nw-status.local"      # lives on the Beelink
antenna     = "TCI-532 LP, 3-30 MHz, 12 dBi at 278 deg"

[[receiver]]
reporter_id = "KFS-SW"
radiod      = "kfs-sw-status.local"      # lives on the Beelink
antenna     = "TCI-532 LP, 3-30 MHz, 12 dBi at 222 deg"
```

A KiwiSDR receiver is the same block with a different source:

```toml
[[receiver]]
reporter_id = "KFS-KIWI-NW"
kiwisdr     = "10.2.80.74:8073"          # host:port, or a sources key
channels    = 8                          # what the Kiwi makes available to us
bands       = ["160","80","40","30","20","17","15","10"]   # at most `channels`
antenna     = "TCI-532 LP at 278 deg, via the GPS 14-channel Kiwi"
# clients is implied: wspr-recorder only.  Naming psk-recorder here is an error.
```

Rules:

- A receiver has exactly one of `radiod` or `kiwisdr`.
- `radiod` must resolve to a radiod Sigmond can see: a local
  `[radiod.<id>]` block in coordination.toml, or one discovered on the LAN
  (§6). Bring-up refuses a receiver whose radiod is neither.
- A radiod may serve at most one receiver. Two receivers on one radiod is
  a configuration error, not a feature.
- The per-reporter client set defaults to **every** radiod-bound client
  the profile enables (wspr-recorder, psk-recorder, meteor-scatter,
  hf-timestd where `injector = true`), on every receiver. Rob: "all, if
  the CPU can handle them." So bring-up gains a capacity gate: it sums the
  per-instance cost (HOST-CAPACITY-PLANNING.md tiers) across receivers,
  including the clients for remote receivers, which run on this host, and
  refuses or marks instances dormant with a message when the host is too
  small, rather than starting N instances that starve each other. An
  optional `clients = [...]` key narrows the set per receiver.
- Sample rate is a radiod property, so it lives where it does today, in
  the radiod conf and `[radiod.<id>].samprate_hz` on the host that owns
  the card. Rob: the Beelink runs the same rate as the local radiods, and
  it stays configurable per radiod. Nothing in the receiver block
  overrides it.
- `merge_into` (§7c) is the only upload-identity key a receiver carries.
  One identity serves wsprnet and pskreporter (Rob, 2026-10-30).
- A radiod-only host has no `[[receiver]]` entries. Its radiods are
  declared the way they are today, by `smd config init radiod`.
- The existing single-receiver station is the degenerate case: one
  `[[receiver]]`, generated automatically from today's inputs by
  `smd config render --init` so nothing changes for existing stations.

`smd config render` publishes the list into `coordination.env` as
`RECEIVER_COUNT`, `RECEIVER_<n>_REPORTER`, `RECEIVER_<n>_RADIOD` alongside
the existing `RADIOD_<id>_*` facts, so every client and check can read it
without parsing TOML.

## 5. Multiple local RX888s on one host

What exists: `smd config init radiod` already walks every SDR on the bus,
prompts per card, renders `radiod@<id>.conf` and registers
`[radiod.<id>]` with `sdr_serial`. The USB watch table joins cards to
instances by serial. `sigmond-sdr-recover` takes `--serial`.

What changes:

1. **Serial pinning on a multi-card host already happens**:
   `_collect_per_sdr_values` writes `serial =` uncommented whenever more
   than one SDR is on the bus and its iSerial is readable. The one gap is
   an unreadable iSerial (no udev access): today that renders an unpinned
   conf on a multi-card host. Change: refuse to render, say why, and point
   at `smd admin sdr identify`.
2. **`smd admin sdr identify`**, a port of wsprdaemon's `wd -X identify`.
   Stops the radiods and the watchdog, then walks the operator through the
   cards one at a time: "unplug the RX888 on the SE antenna"; the serial
   that drops off the bus is that antenna's; plug it back; next. At the
   end it writes the confirmed serials into the radiod confs and
   coordination.toml and prints the mapping. This is how G1's "confirmed,
   not assumed" is met. `smd admin sdr list` prints the current mapping
   and flags missing, duplicate and unclaimed serials.
3. **DFU bootstrap is already sequential** and `cmd_radiod_init` already
   refuses to register a DFU placeholder serial. The remaining gap: it
   waits for the DFU *count* to drop, not for a specific card's real
   serial to appear. Change: wait for the serial, so two cards
   bootstrapping back to back cannot be confused.
4. **The watchdog reads the serial map from coordination.toml.**
   `sigmond-radiod-watchdog` today says a hold that names no unit covers
   every instance "because without a serial -> instance map we cannot
   tell which radio lost its card". The map exists
   (`[radiod.<id>].sdr_serial`); the watchdog uses it, so a hold on one
   card leaves the other radiod alone.
   `sigmond-sdr-recover`'s **manual** `--off/--on --serial` path is
   already per-card and stays. Its **automatic** path watches exactly one
   card (one devpath in its state file, a documented gap). My call: Phase 1
   keeps automatic recovery on one designated card and does not make it
   per-card; per-card automatic recovery is a follow-on once KFS's hub is
   known (two cards on one hub whose ports switch together cannot be
   recovered independently by any software).
5. **CPU placement per radiod instance already exists**:
   `cpu.assign_radiod_cores` gives each radiod unit its own eligible
   physical core and shares the last one when instances outnumber cores.
   Changes: refuse (with a message) rather than share silently, and wire
   `LOCAL_RADIOD_COUNT` through on the Proxmox side so the VM is sized for
   N radiods.
6. **FFT wisdom is shared.** One wisdom file serves all instances; bring-up
   waits for it once.
7. **Bring-up Stage 1 loops over local radiods.** Each gets its conf, its
   unit, its hard checkpoint (streaming), in sequence.
8. **ka9q-web ports.** Known clash (v3.83 item 9): the second radiod's
   ka9q-web lands on 8082, which gmag-webui owns, and station-web refuses
   the shared port. Fix in Phase 1: keep 8081 for the first ka9q-web (the
   RAC relay depends on it) and allocate further radiods from a range that
   skips every port a catalog client owns.

The ka9q-radio behaviour that matters: a configured serial that matches
nothing makes radiod exit. Item 2 is what keeps that from being a
surprise; the retired fallback patch is not revived unless a two-card
station shows it is needed, and then it goes to Phil.

## 6. The radiod-only host (the Beelink)

A new catalog profile, `radiod-only`. `install.sh` and `build_plan` with
this profile:

- Install: infra, ka9q-radio, radiod confs via `smd config init radiod`
  (with §5 rules), host tuning, `sigmond-sdr-recover`,
  `sigmond-radiod-watchdog`, `sigmond-radiod-ready`, `sigmond-mdns`,
  IGMP querier if the LAN probe says none is present, and ka9q-web
  (optional, default on, so the operator can look at the spectrum).
- Not installed: every contract client, the sink, hs-uploader,
  hf-timestd. `smd component enable` for any of them on a radiod-only
  host is refused with a message, not silently allowed (anti-goal).
- Bring-up: Stage 1 only, then a checkpoint that each radiod is streaming
  and its status name resolves over mDNS from the host itself.
- `smd check` on a radiod-only host reports each radiod: card present by
  serial, unit active, streaming, mDNS name published. No upload lines.
- Identity: hostname, site callsign and grid are still collected (they
  go into the radiod conf `description` and the mDNS names) but nothing
  reports upstream from this box.

Discovery from the decoder host: the radiod control-plane discovery the
2026-05 plan called Phase 1. `lib/sigmond/discovery/mdns.py` and
`multicast.py` exist; the builder verifies whether a bare radiod's status
name is already browsable and, if not, adds the probe there. `smd admin
sources list` already renders discovered radiods; bring-up uses the same
inventory to validate `[[receiver]].radiod`.

Operational note for KFS: the Beelink's two radiods are consumed only by
the KFS host, so the multicast load on the switch is two radiods' worth
of PCM channels, not IQ. The builder sizes it from the channel lists; it
is far below the ~100 Mbps worst case in the old plan's open question 2.

## 6b. Phil's ka9q-radio development on a Sigmond host

Rob, 2026-10-30: Phil maintains radiod at KFS and will keep developing
ka9q-radio on these hosts. He will pull and commit in his own checkout,
build and install radiod, and stop and start radiod independently of the
rest of Sigmond. Sigmond must accommodate that, and a document must tell
Phil how to work on ka9q-radio in the Sigmond context.

What Sigmond does to radiod today that Phil will run into:

- `install.sh` builds ka9q-radio from a pinned upstream commit, and
  `smd admin diag` reports pin drift.
- Four drop-ins on `radiod@.service`: patient restart (10), the thread
  parker (40), restart of RTP consumers that predate this radiod start
  (45), and a note on any exit nobody asked for (50).
- `sigmond-radiod-watchdog` restarts a silent radiod after 600 s unless a
  hold in `/var/lib/sigmond/sdr-hold.json` names its unit.
- `timestd-radiod-affinity.path` re-applies the CPU pin drop-in after a
  reinstall (already handles "Phil reinstalled radiod").
- `smd config init radiod` renders `/etc/radio/radiod@<id>.conf`.

What this design adds:

1. **Conf ownership rule.** Sigmond renders a radiod conf once. After
   that it is the operator's and Phil's file: `smd` reads it (serial,
   status name, samprate) and never rewrites it without `--force`.
2. **`smd admin radiod dev-checkout on|off`.** On: `smd` stops enforcing
   the ka9q-radio pin, `smd admin diag` reports "radiod built from local
   checkout <path> at <sha>[, dirty]" as information, and `smd update` /
   `smd align` never rebuild or reinstall ka9q-radio over Phil's build.
   Off restores the pin and offers to reinstall it.
3. **`smd admin radiod maintenance on <instance> [--for 4h] [--who phil]
   | off`.** On: writes a hold the watchdog already honours, silences the
   exit-note (50) and consumer-churn alarms for that instance, and shows
   in `smd check` and the Status tab as "radiod kfs-se: maintenance (phil,
   since 14:02, expires 18:02)". Everything still *works* while on: the
   consumer-restart drop-in still re-anchors the clients when radiod
   comes back, which is what Phil wants. Off, or expiry, clears it.
   Running radiod under gdb from a terminal instead of systemd is the
   same flow: maintenance on, `systemctl stop radiod@kfs-se`, run it by
   hand with the same conf, clients reconnect via mDNS when it returns.
4. **The document**, `docs/contributor/ka9q-radio-development-on-a-
   sigmond-host.md`, written with Phil: where the checkout lives, build
   and install, the two verbs above, what each drop-in will do when he
   restarts radiod, how to read `smd check` for the radiod he is working
   on, and what *not* to touch (coordination.toml, the serial lines, the
   status names clients are bound to). The radiod-only Beelink is the
   simplest place for him to work; the same document covers both hosts.

## 7. The decoder host: receivers to instances

`build_plan` today takes `local_radiod: bool`, `remote_status_dns` and one
`reporter`. It becomes receiver-driven:

- Stage 1 runs for every **local** radiod (zero on a pure consumer host).
- Stage 2, timing. **Rob, 2026-10-30: one TS-1 at KFS, split four ways
  (splitter from Paul), feeds all four RX888s.** So all four receivers
  carry `injector = true` and each radiod needs its own chain-delay
  correction, because the correction is a property of that card, cable
  and radiod pipeline (CLIENT-CONTRACT §8), not of the TS-1. The
  non-Sigmond KFS servers need no time sync.
  **hf-timestd is a host singleton** today (one config, one `status =`,
  non-templated core units; only `timestd-metrology@` is templated, per
  channel). **Rob's decision: four instances.** hf-timestd becomes a
  per-reporter instance client like the others, `hf-timestd@<reporter>`,
  with the locked per-instance layout (MULTI-INSTANCE-ARCHITECTURE.md §4):
  its own config, state under `/var/lib/timestd/<reporter>/`, run and log
  dirs, and one `status =`. Each instance publishes
  `RADIOD_<id>_CHAIN_DELAY_*` for its radiod. This is Phase 3b, a
  workstream in the hf-timestd repo with three edges in other repos:
  sigmond's `timing` commands lose their "no --instance" assumption;
  station-web's timing and science pages read `/var/lib/timestd` and must
  learn N instances; hamsci-physics, which pulls hf-timestd, follows. Until
  it lands, bring-up accepts `injector = true` on one receiver only and
  says so; the other three run on the GPSDO tier. The Beelink runs no
  hf-timestd; the KFS host calibrates the remote radiods over the LAN like
  the local ones.
- Stage 3a loops over receivers × radiod-bound clients: `smd admin
  instance add --force <client> <reporter> --radiod <status>`; the new
  `--radiod` writes the radiod `status` into the per-instance config
  (`[radiod]` for wspr-recorder, the single `[[radiod]]` block for
  psk-recorder and meteor-scatter). Then `smd config upload <client>
  <reporter> --on`; for psk-recorder the mode is `--via server-merge` when
  the receiver is in a server-merged group (see §8 item 2) and the
  existing `server-raw` otherwise. Then enable, start, and the
  existing 20 s stagger between radiod-bound starts so N instances do not
  starve one radiod's control plane. With four receivers that stagger is
  per start, so bring-up takes a few minutes longer; acceptable.
- Stage 3b (independent clients) is unchanged.
- `smd admin instance list` gains a radiod column; `smd check` prints one
  block per receiver: radiod up, streaming, each instance active, spots
  flowing, upload pipeline healthy.

The per-instance `sources.toml` file keeps being written (one entry, the
receiver's radiod) so the `smd admin sources` namespace stays truthful,
but nothing in this design needs `sources apply`.

## 7b. KiwiSDR receivers (WSPR only)

What exists: Sigmond discovers Kiwis (`discovery/http_kiwisdr.py`, the
`/status` and `/gps` probes) and the sources model has the
`kiwisdr:<host>:<port>` key. wspr-recorder has no Kiwi code at all: its
only source is a radiod RTP channel. wsprdaemon reaches Kiwis through
`kiwirecorder.py` (WebSocket, one 12 kHz audio stream per channel).

What changes:

1. **A Kiwi source adapter in wspr-recorder.** The recorder's per-band
   ingest takes 12 kHz PCM for a WSPR cycle from a radiod channel today.
   The adapter opens one Kiwi WebSocket channel per band, in the same
   format (`kiwiclient` is the reference), and feeds the same decode path.
   Everything downstream, spot rows, noise, uploads, is untouched. This is
   the "new SourceAdapter" the 2026-05 plan deferred, scoped to WSPR.
2. **Bands are capped by channels.** `bands` may not exceed `channels`;
   bring-up refuses otherwise. Band rotation across cycles (wsprdaemon's
   schedule) is a later feature, not this design. My call.
3. **One reporter per Kiwi**, named for the Kiwi (Rob, 2026-10-30), never
   sharing an RX888's reporter even on the same antenna. KFS's four Kiwis
   would be `KFS-KIWI-NW` etc. and merge into `KFS` on the server with the
   RX888 receivers. The first test rig is the Kiwi on AI6VN's LAN,
   decoded by AI6VN-PM.
4. **Timing tier.** No hf-timestd, no chain delay. The Kiwi's own GPS
   timestamp is what wsprdaemon uses; the adapter labels rows with the tier
   the Kiwi reports (`/gps` says whether it has a fix), and never claims an
   injected tier.
5. **`smd check` and bring-up** treat a Kiwi receiver as radiod-independent
   (Stage 3b timing, no stagger), and report "Kiwi unreachable" by host.
6. **Channel contention.** A public Kiwi can have its channels taken by
   web listeners. The adapter reports "N of M channels obtained" per cycle
   and the check shows it; it does not fight for channels.

## 7c. The merge declaration and its operator interface

What exists, measured in tasks/plan-operator-commands-via-uploader.md
(2026-10-28, reviewed by Michael):

- `routing.json` at the tar root already carries `psk_reporter` and
  `wsprnet_reporter` per receiver; the server records them in
  `psk.reporter` and `wsprdaemon.wsprnet_reporter`. Pushed, not yet
  deployed to a server, replication fix pending.
- The general form is designed: a `command.jsonl` envelope in the client's
  own tar, `set_reporter_identity` with `scope.key` (the receiver) and
  `args {call, grid}`, newest-wins, authority = the delivering account,
  never fail the spots. **Written by hs-uploader, not by `smd`** (Michael's
  principle: a client stays usable without Sigmond).
- Rule: a receiver that arrives already merged on the station
  (wspr-recorder's `WD_MERGE_REPORTERS` instance) is not a candidate for
  server merging. The two merges must never both fire.
- Missing: any operator interface, and the return leg.

What this design adds in Sigmond:

1. **The declaration lives in the receiver list.** Each `[[receiver]]` may
   carry `merge_into = "KFS"` (call) and optionally `merge_grid` (defaults
   to the site grid). All receivers naming the same `merge_into` form the
   group. No `merge_into` means the receiver reports as itself. One
   identity serves both wsprnet and pskreporter (Rob, 2026-10-30); the
   render writes the same call and grid into both manifest keys. KFS:
   all four RX888 receivers and all four Kiwi receivers say `merge_into =
   "KFS"`. During testing the rig uses `AI6VN-*` receivers merging into
   `AI6VN`, on the real servers.
   **A station merges in one place, never both** (Rob: "either/or, not
   both"). `merge_into` on any receiver makes `WD_MERGE_REPORTERS`
   host-wide a render error, and a host with a station-side merge
   instance refuses `merge_into`. Both modes stay supported; the AC0G-B4
   fleet may keep merging on the station for as long as Rob and Michael
   want.
2. **`smd config render` turns it into the uploader manifest keys that
   already exist**: `psk_reporter_call/grid` and `wsprnet_reporter_call/
   grid` (`pipeline_factory.py`), from which hs-uploader writes
   `routing.json` into every tar; the server ingests those into
   `psk.reporter` and `wsprdaemon.wsprnet_reporter`, and both forwarders
   resolve identity from them. That is the whole channel; no new server
   merge code is needed for it. Today those keys are one value per
   pipeline; `_render_routing_json` already takes a per-receiver map, so
   groups with different targets on one host are a builder detail, not a
   design question. When the command plan's `command.jsonl` envelope
   lands, the same render feeds it. Sigmond authors the config; it never
   carries the command. The render also forces `WD_MERGE_REPORTERS` empty
   on any instance in a server-merged group, enforcing the two-merges rule
   at the only place it can be enforced before the tar leaves.
3. **CLI:** `smd admin merge show` (groups, members, what each member's
   last tar carried), `smd admin merge set <reporter> --into <call>
   [--grid G]`, `smd admin merge clear <reporter>`. These edit
   site-profile and render; nothing else.
4. **The station web page is station-web** (Rob, 2026-10-30): Michael's
   `station-web` client, port 8000, already installed on every station. It
   reads and never runs a shell or touches the host; it reads `smd`'s
   JSON through root-written snapshots in `/run/sigmond/snapshot/` and
   asks for a fresh one by dropping an empty file in
   `/run/sigmond/snapshot-request/`, which a sigmond path unit answers.
   The two tabs use exactly that pattern:
   - **Status tab:** a new `receivers` snapshot (`smd check --json` gains
     a per-receiver block: radiod or Kiwi reachable, streaming, instances
     active, spots this cycle, last tar shipped, maintenance holds) plus
     the existing `instances` and `manifest` snapshots. station-web renders
     one block per receiver and the site sink switch.
   - **Command tab:** the merge group. Pick receivers, name the call they
     report as (grid defaults to the site's), save. station-web writes one
     JSON request, `{"command": "set_merge", "reporters": [...], "call":
     "KFS", "grid": "CM87tj"}`, into `/run/sigmond/command-request/`; a
     new `sigmond-command-request.path` unit runs `smd admin merge
     apply-request`, which accepts only that closed vocabulary (identity,
     receivers it knows), edits site-profile, renders, and writes a
     `merge` snapshot with the result and state: *queued for the next
     tar*, *shipped at HH:MM in <instance>'s tar*, plus links to
     pskreporter.info and wsprnet.org for the call, because the command
     plan's return leg is "publish and external confirmation" for now.
     When `station.command_result` lands on the servers, the snapshot
     carries it and the tab shows it.
   - This is the first thing station-web writes beyond an empty request
     file. It stays inside station-web's stance (no shell, no network, one
     file in a sigmond-owned spool) but Michael should agree to it before
     Phase 4b starts.

## 8. Uploads: one tar, rows per receiver, server-side everything else

What exists: `[station] wsprnet_proxy` flags the wsprdaemon tar and drops
the direct wsprnet pipeline (`uploader_manifest.apply_proxying`); the
server's `wsprnet_forwarder.py` posts flagged rows. The wsprdaemon tar is
fed by `WsprCycleSource` (every wspr row of the cycle, plus psk rows when
`include_psk` is on), and the transport files each row under its **own**
`rx_call`/`rx_grid`, which each wspr-recorder instance sets from its
reporter ID. One tar from a host already carries N receivers and the
server attributes per receiver; that is how the AC0G-B4 fleet works.
psk-recorder has two server-bound modes: `server-raw` writes
`forward_to_pskreporter = false` and the station's own
`psk-recorder-pskreporter` daemon pipeline posts directly;
`server-merge` writes it `true` so the server's `pskreporter_forwarder.py`
posts instead.

What changes on the station:

1. **One tar per host, rows credited per receiver.** My call, since it
   is what runs today: no per-reporter pipelines. What `reporter_call`'s
   `sorted(reporters)[0]` actually names is the tar, its `rx_site`
   directory, `client_info.reporter_id`, the direct-wsprnet identity (gone
   under proxy) and the host's SSH key registration with the WD servers.
   Change: `reporter_call` prefers `[station].reporter_id` (the site-wide
   identity that already exists in coordination.py) over the first
   instance alphabetically, so a KFS tar is `KFS_CM87tj` and the key is
   registered as KFS. Rows inside carry `KFS/SE` etc. as today.
2. **Both proxies on, by the operator.** `wsprnet_proxy = true` already
   exists. A `pskreporter_proxy = true` is added beside it in `[station]`,
   opt-in per station, never a default (the "someone else's uploads"
   warning in coordination.py stands). `PROXIED_SERVICES` has only
   wsprnet; pskreporter is added with the same two-halves-move-together
   guard. For pskreporter the two halves are: flip every psk-recorder
   instance to `--via server-merge` (sets `forward_to_pskreporter`) and
   drop the `psk-recorder-pskreporter` daemon pipeline. That touches
   bring-up's hard-coded `--via server-raw`, `inprocess_senders`, and
   `sink_levers._psk_armed`; the builder moves them together or not at
   all.
3. **The direct wsprnet and pskreporter pipelines are absent on KFS**, by
   the proxy mechanism, not by deleting code paths. A station that opts
   out keeps posting for itself as today.
3a. **No station-side merge on a server-merged group.** See §7c item 2.
   A KFS wspr-recorder instance with `WD_MERGE_REPORTERS` set is a render
   error, not a warning. Stations that merge on the station today (the
   AC0G-B4 fleet: three wspr-recorder instances, a merge instance running
   the uploader, `expected_reporters` cycle gating) are untouched unless
   they set `merge_into`; `smd config render --init` synthesizes their
   `[[receiver]]` blocks from the existing instances with no `merge_into`.
   Whether that fleet migrates to server merge is Rob's and Michael's
   call (§11).

What changes on the server (`wsprdaemon-server`, Rob's stream):

4. **Declared merge for KFS, arriving from the station.** The eight KFS
   receivers (four RX888, four Kiwi) declare `psk_reporter` and
   `wsprnet_reporter` `{call: "KFS", grid: "CM87tj"}` in `routing.json`
   (§7c); the server records them in `psk.reporter` and
   `wsprdaemon.wsprnet_reporter`. The server work here is deployment:
   the pushed-but-undeployed `routing.json` ingest and its replication
   fix. The pskreporter forwarder has no server-side override file (only
   wsprnet has `wsprnet_reporters.json`), so the station declaration is
   the only way to merge for pskreporter, which is what Rob wants anyway.
   The merged wsprnet identity is plain `KFS` (Rob). The
   dedup key is the client's own: `(date, time, callsign, freq to 1e-4
   MHz)`, best SNR, ties keep first.
5. **WSPR to pskreporter is a new server data path, not a flag.**
   `wsprnet_forwarder` does not persist its deduped set, and
   `pskreporter_forwarder` reads only `psk.spots` (its mode map has no
   WSPR); each runs under its own leader election. The design: the wsprnet
   forwarder writes the deduped MEPT set it posts to a table
   (`wsprdaemon.wsprnet_posted` or similar); the pskreporter forwarder
   gains a second source that reads that table as mode WSPR under its own
   watermark, so pskreporter sees one spot per cycle per call. PSKReporter
   accepts WSPR; the builder confirms the ftlib mode string. This is the
   largest single item in the server stream and is sized as such in §10.

## 9. Observability on a multi-card host

- `smd admin usb watch`: already joins by serial; with serials pinned the
  `?` column disappears. No change beyond §5.
- `sigmond-sdr-recover --off` with no `--serial` on a two-card host is
  already refused; stays.
- Fleetboard/status-via-uploader rows carry `reporter_id` per instance;
  a receiver whose radiod is remote reports the radiod host so a dead
  Beelink shows up as "NW, SW: radiod unreachable (beelink)" rather than
  as four generic stalls.

## 10. Rollout

Phases, each a PR, each shippable on its own and harmless to one-card
stations:

| Phase | Delivers | Proves on |
|---|---|---|
| 1 | §5: refuse an unpinned conf on a multi-card host, `smd admin sdr identify/list`, DFU waits for the real serial, watchdog uses the serial map, CPU placement refuses to share, `LOCAL_RADIOD_COUNT` on the Proxmox side, ka9q-web port allocation that avoids gmag-webui — **delivered 2026-10-30** on branch wizard-v384 (commits 8bf4d10..f7c0c52); §5 item 7 (Stage 1 looping over N local radiods) lands with Phase 3's receiver-driven build_plan | AI6VN-PM: one RX888 radiod + one sig_gen radiod; two-card parts unit-tested, first run for real at KFS |
| 2 | §6: `radiod-only` profile, Stage-1-only bring-up, `smd check` for it, control-plane discovery verified | a radiod-only VM on AI6VN-PM with the RX888 passed through, consumed by the full-profile VM |
| 2b | §6b: conf ownership rule, `dev-checkout`, `maintenance`, and the ka9q-radio development document, reviewed by Phil | AI6VN-PM with Phil restarting radiod by hand |
| 3 | §4 + §7: `[[receiver]]`, receiver-driven `build_plan`, the capacity gate, `instance add --radiod`, `smd check` per receiver | AI6VN-PM: one local + one remote receiver |
| 3b | §7 timing: `hf-timestd@<reporter>` per-instance (hf-timestd repo), sigmond `timing` commands and station-web timing pages learn N instances, bring-up accepts `injector = true` on all of them | AI6VN-PM with the lab TS-1 split two ways |
| 4 | §8 station side: tar identity from `[station].reporter_id`, `pskreporter_proxy` with its two halves (server-merge + drop the daemon pipeline), `WD_MERGE_REPORTERS` render error on merged groups | same rig, tars inspected on wd30 |
| 4b | §7c: `merge_into` in the receiver list, the either/or rule, render into hs-uploader, `smd admin merge`, the `receivers` and `merge` snapshots, the command-request path unit, and the Status and Command tabs in station-web (with Michael) | same rig as AI6VN-* merging to AI6VN; command seen in the tar on wd30 |
| 4c | §7b: Kiwi source adapter in wspr-recorder, Kiwi receivers in bring-up and `smd check` | the Kiwi at 10.20.23.73:8073 on AI6VN's LAN, from AI6VN-PM; then ON5KQ |
| 5a | §8 server side, deployment only: routing.json ingest + replication fix live on wd10/20/30; KFS merge declared from the station and seen in `psk.reporter` / `wsprnet_reporter` | wd30 against the Phase 4 rig, then wd10/wd20 |
| 5b | §8 item 5: WSPR to pskreporter, the new server data path (posted-set table + second forwarder source). In this plan (Rob); server stream | wd30, then the fleet |
| 6 | KFS cutover runbook: build the KFS host and Beelink, run in parallel with the wsprdaemon KFS servers under `AI6VN-*` test reporter IDs, compare spot counts per receiver, then switch to `KFS-*` merging into `KFS` and retire the wsprdaemon path for those four antennas | KFS |

**The lab has one RX888** (Rob, 2026-10-30), so no phase may depend on
a two-card bench before KFS. Test rigs that fit:

- **Two radiods on one host without two cards:** `smd config init radiod
  --siggen` renders a synthetic sig_gen radiod. A real RX888 radiod plus a
  sig_gen radiod exercises multi-radiod bring-up, receivers, CPU
  placement, ka9q-web ports, and the per-receiver check. Only serial
  pinning, DFU ordering and `sdr identify` need two physical cards; those
  are unit-tested against recorded USB inventories and first exercised
  for real at KFS during Phase 6, with a rollback to one card.
- **Radiod-only plus a consumer with one card:** two VMs on AI6VN-PM. One
  VM runs the `radiod-only` profile with the RX888 passed through (the
  Beelink stand-in); the full-profile VM decodes it as a remote receiver
  and adds a local sig_gen radiod, so it is a mixed local+remote host.
  Multicast crosses the Proxmox bridge; the IGMP notes in networking.md
  apply to `vmbr0`.
- **Kiwi:** the Kiwi on AI6VN's LAN at `10.20.23.73:8073` (as Rob gave
  it; verify the address when Phase 4c starts).

Phase 1 is the one with hardware risk (the serial-mismatch exit); it
goes first so the rest of the work lands on a host that cannot swap its
antennas. Phases 1 through 4c are Sigmond, hs-uploader and wspr-recorder
work; 5a and 5b are wsprdaemon-server work and can run in parallel from
Phase 4 on.

## 11. Left to the builder, and open for Rob

**Builder's calls** (sensible defaults; change them without asking):

- Exact `coordination.env` key names for receivers.
- Whether `smd admin sdr identify` reuses `sigmond-sdr-recover`'s port
  power switching to make the "unplug" step automatic on hubs that
  support it (nice to have; the manual walk is the baseline).
- ka9q-web on the radiod-only host: default on.
- The bring-up stagger value with N receivers.
- The radiod-only profile name.
- Per-receiver `routing.json` values on one host (extend the existing
  per-receiver map in `_render_routing_json`).
- The posted-set table name and schema for §8 item 5.

**Calls I made in this revision** (after the builder check; say if any
is wrong):

- One tar per host with rows credited per receiver, not one tar per
  reporter (§8.1).
- (superseded by Rob: four hf-timestd instances, §7 and Phase 3b.)
- Automatic SDR recovery stays single-card in Phase 1; manual per-card
  recovery is already there (§5.4).
- The merge declaration uses the existing manifest keys and
  `routing.json`; the `command.jsonl` envelope is a later carrier for the
  same facts (§7c).
- WSPR to pskreporter is its own server phase, 5b (§10).

**Answered by Rob, 2026-10-30** (all now in the design):

- Time injection: one TS-1 split to all four RX888s; all four receivers
  calibrated; the non-Sigmond servers need none (§7).
- Phil: keeps developing ka9q-radio on these hosts, restarting radiod
  independently; Sigmond accommodates it and documents the workflow (§6b).
- Reporters `KFS-SE/OMNI/NW/SW` merge to plain `KFS`, grid `CM87tj`; the
  same identity for wsprnet and pskreporter; `AI6VN-*` -> `AI6VN` during
  testing (§7c, §8, Phase 6).
- All clients on all receivers if the CPU can handle them: capacity gate
  in bring-up (§4).
- Beelink radiods at the same sample rate, configurable per radiod (§4).
- The station web page is station-web (§7c item 4).
- Kiwis carry their own reporter ID; first test rig is the Kiwi on
  AI6VN's LAN (§7b).
- hf-timestd: four instances, `hf-timestd@<reporter>` (§7, Phase 3b).
- A station merges on the station or on the server, never both; both
  modes stay supported (§7c item 1).
- WSPR to pskreporter (Phase 5b) is in this plan.

**Still open**

- Phase 6 assumes a parallel run under `AI6VN-*` test identities before
  the switch to `KFS-*`. Say if the four RX888s move in one step instead.
- Michael's agreement to station-web writing the command request file
  (§7c item 4), and to `hf-timestd@<reporter>` touching station-web's
  timing pages (Phase 3b).

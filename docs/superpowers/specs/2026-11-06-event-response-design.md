# Event response: recording the fleet through events worth keeping

**Authors:** Michael Hauan (AC0G) + Claude
**Date:** 2026-11-06
**Status:** design, for a prototype.  Builds on `tasks/plan-external-storage.md` (phase 1 shipped
2026-11-02: `smd archive add|status|remove`).  That plan's phases 2 and 3, the archiver and its
kept windows, never got built; this design takes them over and widens them.

## Why

On 2026-10-31 we searched our stations' data for the M6.8 flare of 10-08 and the M6.6 flare of
10-10.  Every product showed them.  FT8 and WSPR counts fell, WWV's 10 MHz SNR at ND dropped 24 dB,
5 MHz went dark for over an hour.  But the 24 kHz IQ had already gone from B4 and ND, pruned within
two days, and only the 10 Hz GRAPE copy on PSWS survived.

Flares make the easy case, because GOES announces them.  Our stations stand spread across the
continent, and many other discrete events reward a network like that: a CME and the storm it
brings, a volcanic eruption, an eclipse, an earthquake, a satellite re-entry, a meteor shower.
Some arrive with warning and some without.  Some want the time channels we already record; others
want frequencies, bandwidths or modulations no station records today.  We want the fleet to answer
each of them the same way: record at full bandwidth, keep the recording, and send it home for
analysis later.

## What success looks like

The first test runs at one station.  Every M1 or stronger flare that strikes B4's sunlit paths
sends B4's six time-channel windows to wd30, in Michael's house, as soon as the window closes,
without anyone lifting a finger.  B4 keeps no copy of its own.  What builds up on wd30 measures,
rather than estimates, what a fleet response would cost a server.

The Geminids peak on 13 and 14 December 2026, during the Meteor Scatter QSO Party.  Through those
dates B4 and a few other meteor-scatter stations record exactly as the MSQP defines: a WAV of each
slot that decodes, on the 6 m and 10 m MSK144 frequencies, sent to Zenodo the standard MSQP way.
Their totals tell us whether Zenodo serves the fleet.  Recording as the MSQP does lets us compare
our client fairly with the standard MSQP stations; IQ stays in the plan for later.  That part must
run by 1 December 2026.

Later, when something unforeseen happens, Michael or Rob can tell the fleet to record a chosen
frequency through a chosen window, with one command.

## Goals

1. Turn recording on for an event across the fleet, at chosen stations, or at one station.
2. Keep a window from the time-channel buffer after the fact, automatically for flares and storms,
   by hand for anything else.
3. Give each client that receives IQ a record mode that writes what it already receives, without
   disturbing what it produces live.
4. Provide a generic receiver for what no client covers.
5. Send recordings home within a day, inside each station's bandwidth limits, and free the local
   disk once the server confirms receipt.
6. Let anyone we send a link reach an event's recordings without an account.

## Non-goals

- Real-time processing of event recordings.  The stations record; the analysis comes later.
- Any change to the live products: spots, GRAPE, timing, CODAR soundings.
- A permanent home beyond wsprdaemon.org and Zenodo.  PSWS at the University of Alabama, or Zenodo
  for everything, needs agreement with the wider community first.
- Settling who beyond Michael, Rob and a station's operator may order a recording (see Open
  questions).

## Anti-goals

The design fails, however well it records, if any of these happens:

- Recording starves radiod, the decoders or timing of CPU or disk bandwidth.  KFS-OMNI already ran
  its decoder cores at 99 % on 2026-11-06; a recorder must yield, not compete.
- A recording fills the station's disk.  A full disk stops the measurements we exist to make.
- An upload swamps a slow or metered link, or delays the live spots.
- A recording reaches permanent storage without saying what it holds: which station, which
  receiver chain, which timing tier, which event, who ordered it.

## How it works

### Two ways to capture

**Looking back.**  hf-timestd keeps 24 kHz IQ on the six WWV/WWVH channels for as many days as the
station's disk allows, just in case.  An event marks a window in that buffer as worth keeping.  The
archiver copies the window out before the prune reaches it, and the window goes home.

**Looking forward.**  For anything else, a client switches into record mode at the window's start
and out at its end.  A station can only look back at what it already records.  An unforeseen
earthquake leaves us the time channels and nothing more.  Predicted events (eclipses, re-entries,
showers, a storm forecast after a CME) get chosen frequencies from the start.

### The event order

Every recording starts with an order, and the order travels with the recording into permanent
storage.  An order holds:

- an event id and a plain name (`2026-12-14-geminids`, "Geminids 2026, MSQP")
- the kind of event (flare, storm, eruption, earthquake, eclipse, re-entry, meteor shower, other)
- who ordered it, and why, in a sentence
- the window: start and end in UTC, or start and "until stopped"
- which stations: named ones, or the whole fleet
- what to record: for the time channels, a keep window; for a client, which client and which of
  its channels; for the generic receiver, the frequencies and channel settings
- where it goes (wsprdaemon.org or Zenodo, by client; see Destinations)

**Who can order, for the prototype.**  Michael or Rob, for any station or the whole fleet.  A
station's operator, for that station alone.  Automatic triggers, under their own names: GOES flares
of class M1 and above, and storm days of observed Kp 5 and above.  Michael or Rob can review or
cancel any automatic order.

**How an order reaches a station.**  (Claude's call.)  The orders live in one list on wsprdaemon.org,
and every station reads that list every few minutes over HTTPS and acts on the orders that name it.
A station's own operator gives a local order with `smd event record …`, which the station adds to
its own copy and reports upstream.  Stations sit behind NAT, campus firewalls and the RAC; a station
that reaches out works everywhere, while one that waits to be reached does not.  One list also gives
one record of who ordered what.

**Automatic triggers.**  (Claude's call: the server raises them, not each station.)  A job on
wsprdaemon.org reads the GOES flare list and the observed K index once an hour, runs the daylight
test for each station, and adds the orders to the one list.  Stations then need no access to NOAA,
and Michael or Rob cancel an automatic order exactly as they cancel any other.  For the first test,
with no order list yet, the job runs on wd30 and serves B4 alone.  The rules stay as
`tasks/plan-external-storage.md` settled them on 2026-11-01:

- A flare of class M1 or above becomes a keep order for a station only when, at the flare's peak,
  the station stood in daylight and at least one monitored path's midpoint lay sunlit (WWV, WWVH or
  BPM).  The window runs from 30 minutes before the flare's start to 90 minutes after its end, about
  7 GB over six channels.
- A UTC day with observed Kp 5 or above becomes a keep order for the whole day at every station,
  about 90 GB each.  This trigger stays off until open question 1 settles where such days can go.
- The buffer holds a day or more, so lists that arrive hours late still catch every window.

### Record mode in the client contract

Recording lives in the client, as the client contract already places every client's data with the
client.  A new contract section, §20 "Record mode" (contract v0.10; v0.9 already carries the
storage ledger), adds:

- **Declaring it.**  `inventory --json` says whether the client offers record mode, and on which of
  its channels.
- **Switching it.**  (Claude's call.)  sigmond turns record mode on and off with CLI verbs,
  `<client> record start --order <file>` and `<client> record stop <event-id>`, as the storage
  ledger chose CLI verbs over the §13 socket.  Only hf-timestd serves a §13 socket today, and §13.7
  keeps configuration off it.  The client records until the window ends or sigmond stops it,
  whichever comes first, and writes its state where `/status` and `inventory` report it.
- **Writing it.**  The client writes Digital RF with full metadata: station identity and grid, the
  radiod and its receiver chain, the channel's frequency, bandwidth, preset and sample rate, the
  timing tier in force and the anchor behind each sample's time (§18: RTP time through the
  authority, never the host clock), and the event order itself.
- **Reporting it.**  `/status` reports what the client is recording now and what it holds,
  awaiting upload.
- **Yielding.**  Recording runs at idle I/O priority, stays off radiod's cores, and stops itself
  rather than let its tree grow past the share sigmond's storage ledger grants it.  It never
  restarts radiod or changes a channel the client's live product depends on.

### The clients

**hf-timestd.**  Its buffer already records.  (Claude's call.)  The archiver converts each kept
window from the buffer's `.bin.zst` files to Digital RF on the station, at idle priority, as part of
the daily batch, so every recording on the server reads the same way.  The conversion repackages
samples it already holds; it computes nothing.  Its part becomes the archiver from
`tasks/plan-external-storage.md`, phases 2 and 3, changed in one respect.  The kept windows no longer
wait on an external drive.  They go home once confirmed, so a station without an archive drive
still keeps every flare.  The rolling "just in case" archive on an external drive stays as that
plan describes, for stations that add one.  `smd archive keep <start> <length>` gives a local keep
order.

**codar-sounder.**  Record mode writes the raw wideband chirp IQ it already receives, from every
CODAR site it follows, while its soundings carry on.  Later analysis can then rerun the dechirp per
sweep, choose other sites, or follow phase through a travelling disturbance.  An event order may
also add CODAR sites the station doesn't normally follow.

**meteor-scatter.**  It decodes MSK144 on the 6 m and 10 m meteor frequencies around the clock and
keeps a 12 kHz WAV of every slot that decodes.  For now it stays with WAV, as the MSQP does.

- It keeps every decoded slot, all year, as now.  Our own spots from 10-13 to 11-06 give a typical
  6 m receiver 7 to 24 decoded slots a day and the busiest 168.  At 0.72 MB a slot that comes to a
  few to a hundred-odd MB a day.
- For the Geminids it records exactly as the MSQP defines, nothing more: no every-slot mode.
  (Michael, 2026-11-06.)  B4 and a few other stations measure the totals against Zenodo's 50 GB
  limit per record.
- Its recordings go to Zenodo through the MSQP packaging it already has, under each operator's own
  Zenodo account, as the standard MSQP method works.
- IQ in Digital RF stays in the plan.  Before it ships, we settle whether IQ comes from a second
  radiod channel beside the audio one, or from one IQ channel that meteor-scatter demodulates
  itself, and whether the WAV survives beside it.

**The generic receiver.**  A new client on every sigmond install, off by default, whose only work is
recording.  It covers frequencies, modulations and channel settings that no existing client covers.
An event order names frequencies and whatever channel settings radiod accepts (bandwidth, preset or
demodulation, sample rate, encoding); the receiver asks radiod for those channels at the window's
start, records them as Digital RF, and releases them at the end.  It processes nothing in real time.
Like every client it obeys the contract, so `smd` installs, inventories and validates it as it does
the others, and it joins `AFFINITY_UNITS` in `lib/sigmond/cpu.py` so it never runs on radiod's
cores.

### Getting the data home

- Spots go up at once, as now.
- A flare response goes up as soon as its window closes, and the station keeps no copy once the
  server confirms it.  (Michael, 2026-11-06.)  The buffer holds its own days as before, so a flare
  window never competes with it for disk.
- Other Digital RF recordings go up once a day, in a batch.
- Each station sets its own limits on that batch: a bandwidth ceiling, and, if it likes, a
  time-of-day window.  A big event simply takes as many days as the link needs.
- (Claude's call.)  The station deletes its local copy only after the server confirms each file
  arrived whole, by checksum.  Ordered event windows go before automatic ones, and within each, the
  oldest goes first.
- (Claude's call.)  A backlog shows in `smd archive status` and in the station's heartbeat, before
  it can fill the disk.

### Destinations

| Recording | Goes to |
|---|---|
| meteor-scatter IQ | Zenodo, through its MSQP packaging |
| codar-sounder IQ | wsprdaemon.org |
| generic receiver IQ | wsprdaemon.org |
| hf-timestd kept windows | wsprdaemon.org (for the first test, wd30, from B4 alone) |
| GRAPE (unchanged) | PSWS, through hamsci-physics |

wsprdaemon.org serves for a start because Michael and Rob manage it.  The longer aim, PSWS or Zenodo
for everything, needs a method agreed with a wider group.

### Reaching the recordings on wsprdaemon.org

(Claude's call; Michael has not yet confirmed it.)

- One tree, by event, then station, then client and channel:
  `events/2026-12-14-geminids/AC0G-B4/codar-sounder/<site>/`.  Each event directory also holds its
  order.
- Michael and Rob reach the tree over ssh or rsync.
- A plain read-only web listing of the same tree serves everyone else, with no login.  Each event
  gets an index page naming the event, who ordered it, the window, the stations and channels, and
  each recording's size, with download links.
- On the server, `events list` and `events show <event>` find an event without browsing.

## Sizes to plan with

| What | Size |
|---|---|
| One time channel, 24 kHz IQ | about 15 GB a day |
| Six time channels | about 90 GB a day |
| One flare window, six channels | about 7 GB |
| One storm day, six channels | about 90 GB |
| One MSK144 slot as IQ, 12 kHz, 16-bit complex | 1.4 MB |
| Decoded MSK144 slots, busy 6 m receiver | about 240 MB a day |
| One CODAR channel, 64 kHz, 16-bit complex | about 22 GB a day |
| One Kp 5 storm day across ~26 stations | about 2.3 TB |

## Open questions

1. **Where the server side lives, and how much it must hold.**  One storm day kept at every station
   could come to about 2.3 TB.  B4's flare windows on wd30 will measure what a response actually
   costs; the answer then goes to a wider discussion before any fleet-wide upload.  Until then the
   storm-day trigger stays off.
2. **Who else may order a fleet-wide recording**, how we name and check them, and whether some
   orders need a second person.  The prototype allows only Michael, Rob, each station's operator
   for that station alone, and the automatic triggers.  Nothing in the prototype should make a
   wider group harder to add.
3. **A permanent home beyond wsprdaemon.org**, PSWS or Zenodo, with the HamSCI community.
4. **Events with no automatic list.**  Volcanic eruptions, earthquakes and re-entries have no feed
   wired in yet.  USGS publishes earthquakes and space-track publishes re-entry predictions; whether
   either should raise orders on its own waits for experience with hand-made ones.

## Order of work

1. B4's flare response: the GOES flare list and daylight test, the keep window from hf-timestd's
   buffer, conversion to Digital RF, and the upload to wd30 the moment the window closes, with its
   checksum confirmation.
2. meteor-scatter for the Geminids at B4 and a few stations, by 1 December: confirm its MSQP
   recording and packaging, with the new jt9, and count the totals.
3. The order list, the event tree and its listing, on whichever server the wider discussion
   chooses.
4. Record mode in the contract (§20) and in codar-sounder.
5. The generic receiver.
6. meteor-scatter's IQ.

## Left to the builder

- The order list's format and its HTTPS endpoint on wsprdaemon.org.
- The Digital RF metadata schema, in detail, so long as it carries everything "Writing it" lists.
- How the generic receiver names its channels and SSRCs within contract §12.2.
- (Claude's call.)  A station refuses an order that would push radiod past a channel limit set in
  its site profile, and says so on the order list.  Recording yields CPU and disk; extra radiod
  channels cost radiod itself, so a limit, not yielding, protects it.
- The upload transport (rsync over ssh, or HTTPS), provided it checksums every file.
- The web listing's look.

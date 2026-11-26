# What's new in each Sigmond release

Each section below says what a release changes for the people who run a station,
newest first.  The release's own page on GitHub carries the same text, and each
download folder holds it beside the image as `<image name>.CHANGELOG.md`.  The bless
step refuses a release until its section stands here, finished.

Every section ends with **How it was proven**: what the image builder tested, and
what has run on real hardware.  A release that no station has yet installed says so.

---

## sigmond v3.96 (draft)

### Units that change owners

- **`smd align` moves a unit from one component to another.**  A component now
  declares a unit it took over, under `[[systemd.moves]]` in its `deploy.toml`
  (CLIENT-CONTRACT §5.6).  v3.96 carries three moves.  `timestd-vtec.service` becomes
  `hamsci-physics-vtec.service`.  `hamsci-physics-ionex-download.timer` becomes
  `timestd-ionex-download.timer`, and `hamsci-physics-iri-update.timer` becomes
  `timestd-iri-update.timer`.  Align runs the new owner's adoption command, stops
  and disables the old unit, then enables the new one and starts it if the old one
  ran.  Two copies never run.  When topology disables the new owner, align leaves
  the old unit running, and the drift report faults it with the fix
  `smd enable <owner>`.  A move that fails holds the restarts back.
- **`sigmond-site-timing` wires a trusted GNSS stream into hamsci-physics.**  It writes
  `/etc/hamsci-physics/gnss-vtec.toml` and restarts `hamsci-physics-vtec`.  A station
  counts as wired when that file enables VTEC with a real host or a real `device`, so a
  USB receiver (N8UR) is never scanned for or rewritten.  The three new units
  (`hamsci-physics-vtec`, `timestd-ionex-download`, `timestd-iri-update`) stay off
  radiod's cores.

### Rolling back to v3.95

An align back to v3.95 works, but it leaves two v3.96 timers enabled, and both then
fail.  `sigmond-recorder-watchdog.timer` runs a helper that v3.95's checkout lacks.
`sigmond-storage-expire.timer` runs `smd storage expire`, a verb v3.95's smd lacks.
Neither restarts or deletes anything.  After the align, switch both off on the station:

```
systemctl disable --now sigmond-recorder-watchdog.timer sigmond-storage-expire.timer
```

- v3.96 retired `timestd-prune` and `sigmond-t6-stuck-watchdog`.  The rollback does not
  undo that by itself.  `sigmond-t6-stuck-watchdog.timer` comes back only where v3.95's
  install.sh runs again and enables it.  `timestd-prune.timer` stays off until an
  operator enables it (`systemctl enable --now timestd-prune.timer`).  Under v3.95
  nothing else trims old rows from `timestd.db`, so enable it.
- The rollback loses one partial raw chunk per channel.  v3.95's recorder writes the
  open chunk to `/dev/shm` again.  About 21 minutes after it starts, its mover moves
  that chunk onto the disk, where it replaces the partial chunk v3.96 wrote.

---

## sigmond v3.95 — blessed 2026-11-25

A release of fixes.  Sensed bring-up and run-with-purpose wait for the package pilot.

### Audio in ka9q-web

- **ka9q-web plays audio again.**  radiod stopped giving a new channel a data
  destination when the station's `[global]` section names none.  ka9q-web now sends the
  destination itself, when it opens the channel and with every mode change.  An older
  radiod ignores the field, so the change costs nothing there.  sigmond installs it from
  a fork until the fix merges upstream (wa2n-code/ka9q-web#12).

### Recording and timing

- **The raw archive places each batch by its first sample.**  It used to label a batch
  by its tenth packet, which put samples 60 ms late, and a chunk's seam lost the tail of
  a batch.  hf-timestd now carries a straddling batch into the next chunk and holds a
  broken label line until radiod's next time pair.
- **A recorder restart no longer hides data.**  A chunk reopened after a restart gets
  segments of its own (`<chunk>.seg1`, `.seg2`, ...).  Every reader now joins them:
  hf-timestd's archive readers, its storage tiering and F32 export, and GRAPE's daily
  processing.
- **Storage housekeeping leaves GRAPE's upload spool alone.**  hf-timestd's storage pass
  used to delete the packaged GRAPE datasets hours after packaging, and the uploader then
  shipped empty folders.  It now skips hamsci-physics's `upload/` folder and writes each
  deletion to the journal (tag `hf-timestd-storage`).
- **T3 admits itself.**  Each station decides each minute whether its WWV/WWVH timing
  (T3) beats T2 against its reference, and every hf-timestd process follows that one
  published decision.  `[timing.t3] admission = auto` (the default), `off` or `on`.
- **ka9q-python 3.28.0.**  Its resequencer counts each sample once, fills a gap with the
  true number of samples, and re-syncs after radiod restarts its sequence.
- A zero position in a GNSS receiver's RTCM message no longer crashes `timestd-vtec`.

### WSPR

- **Spot frequencies keep their sub-hertz digits.**  wspr-recorder stores the frequency
  the decoder printed, and hs-uploader sends it to wsprdaemon.org with nine decimals.
  Before, every spot reached the server rounded to the hertz.  The FST4W spread now
  comes from the right column (w50, in Hz).
- **wspr-recorder can take audio from a KiwiSDR** (`[[source]] kiwisdr = "host:port"`,
  fixed bands).  It stays off unless a station's config names a Kiwi.  See the
  recorder's `docs/KIWISDR.md`.
- **An optional carrier archive** files each WSPR and FST4W spot's carrier as a short
  WAV, with a read-only browser on localhost.  It ships off (`[carrier_archive]
  enabled = false`).

### Align and bring-up

- **`smd align` waits for a busy station.**  It now waits up to five minutes for the
  lifecycle lock, from a terminal too.  On v3.94 it gave up at once when the storage pass
  or the location check held the lock, and left a half-run align (AC1SN, N8UR).
- **Meteor-scatter's site-timing file carries the right name.**  `sigmond-site-timing`
  wrote it as `AC0G=B4.toml`, while the live instance reads `AC0G-B4.toml`.  The stray
  name made align fail its uploader step.  The script now writes the instance key and
  moves its own stray file aside.
- align skips the uploader step on a host that runs no client, such as a radiod-only
  host.
- The UberSDR decoders build against the system FFTW, never a copy in `/usr/local`.
- Ubuntu 22.04: the helper scripts fall back to `tomli`, and the recorders require
  hamsci-dsp (PR #110).
- systemd 249: the journal reads fall back to `--timestamp=utc` (PR #113).
- The CPU-frequency step takes radiod's cores from the affinity plan, even before
  radiod's drop-ins exist (PR #112).
- New admin verbs for the move from codar-sounder to chirp-recorder:
  `smd admin chirp convert`, `check` and `retire`.

- **A rollback past a linked unit completes.**  wspr-recorder links
  `wspr-carrier-browser.service` into `/etc/systemd/system`, and `systemctl disable`
  removes such a link itself.  `smd admin manifest restore` then found the file gone and
  exited 1; now it counts the unit as retired.  The rig's update test found it rolling
  v3.95 back to v3.94.
- The release list names chirp-recorder, and a test requires a row for every catalog
  repo: the golden VM refuses a checkout the list does not name.

### Known issues

- **A fresh v3.95 install shows `sigmond-location-check` as drift.**  The setup
  wizard writes an older pair of location-check units over the pair sigmond
  installs, so the drift report flags both.  The units still run.  One
  `smd align --apply` clears it; v3.96 fixes the wizard.
- **grape-daily can run a 9.6 GB station out of memory.**  On B4 it did so on
  2026-11-24 and 11-25, and the kernel stopped the timing recorder first, for about
  seven minutes.  v3.94 did the same; v3.95 did not cause it.  hamsci-physics
  00301b5 decodes each raw chunk once and runs each day in its own process; it
  ships in v3.96.

### How it was proven

Candidate 3 (`sigmond-v3.95-20261124-release.img`, sigmond 1c5d5d0) passed the
whole rig ladder on 2026-11-24: the golden VM, the nested image test, the update
test from v3.94 forward and back on the align path, and the bare-metal install
test.  The rollback fix above came from the update test.

On 2026-11-24 B4 (Proxmox) and SIGMA (bare metal) aligned to candidate 3 as
canaries; both aligns exited 0.  A day later both passed `smd align --verify`.
SIGMA's first verify failed on recording gaps.  A live-view prototype running
outside the release caused them: its band-waterfall channels on radiod cost the
timing recorder samples.  With those channels stopped, SIGMA's next hour showed
no gaps and its verify passed.

---

## sigmond v3.94 — blessed 2026-11-21

### A release now lives in sigmond

- **sigmond holds the version.**  `release/components.toml` in sigmond lists the commit
  of every component a release ships.  The Proxmox image builds from that list, and a
  bare-metal install checks out the same release and installs the same commits.  The
  tag `v3.94` on HamSCI/sigmond marks it, and the bless step cuts that tag.
- **The image repo has a new name: sigmond-proxmox.**  It builds the Proxmox image,
  the host's first boot and networking, and the test rig.  GitHub forwards the old name,
  so old links and old clones keep working.  Image files now read
  `sigmond-<version>-<date>-release.img`.
- **`smd align` and `pm-align` look for releases on HamSCI/sigmond first.**  They turn
  to the image repo only when HamSCI/sigmond holds no such release, as for v3.93 and
  older.  A network fault stops them rather than send a station back to an older
  release.  Until every station runs v3.94, each release appears in both places.
- **Every release passes a bare-metal install test.**  A fresh Debian VM on the rig
  follows `docs/install-bare-metal.md` word for word.  With no SDR attached, the radio
  clients wait for one, and `smd check` says so.  The bless step refuses a release
  without that test's pass.

### Bring-up and checks

- A bring-up with no SDR leaves stopped and disabled whatever it installs but leaves
  off, igmp-querier included.  It never touches what the station already ran.  When
  the SDR arrives, first-run bring-up finishes the job.
- gpsdo-monitor stays off only where the probe finds no GPSDO and none ever reported
  here.  A GPSDO that comes loose still raises its fault.
- With radiod waiting for its SDR, `smd check` reads hf-timestd's missing config and a
  new station's held uploads as waiting, not failed.
- A bring-up whose steps failed now reports INCOMPLETE, even with no SDR attached.
- `docs/install-bare-metal.md` now runs `apt-get update` and installs git first, and
  checks out the release it names.

### Timing and science

- hf-timestd's HPPS watchdog now runs only where T6 runs.  Elsewhere it guarded
  nothing, and align would have started it on every station.
- GRAPE's daily processing (hamsci-physics `grape-daily`) runs at idle CPU and I/O
  priority, so it no longer crowds the recorders.


### Each station reports how it differs from its release

- **The drift report.**  `smd align` (without `--apply`) now lists every way a station
  differs from its blessed release: its source checkouts, its builds, its systemd units,
  the settings an operator changed, files nothing installed, and the host itself.  Each
  item says whether it changes behaviour (a fault) or not (drift), and names the one
  command that puts it right.  Nothing repairs itself; `smd align --apply` still does
  the repair when the operator asks.
- `smd check` adds one line: no drift, or the count and the first fault.
- A daily timer, `sigmond-drift.timer`, writes the report to
  `/var/lib/sigmond/drift.json` and sends one row to sigmondb.  The row names the
  station's receivers and their grid squares.  It carries no precise position.
- `smd align --accept KEY` records an exception, with `--test` and `--until` for a
  temporary one; the report reminds the operator a day before a test ends.
- `smd fleet update` names the stations with open faults, and those it cannot judge,
  without stopping the roll.  `smd fleet status` shows each station's release and fault
  count.
- The fleet board raises an alarm for each fault.  Its public view shows only the
  station, the kind of item and a fixed phrase; the detail stays private.
- `smd version` now shows what `smd component list` showed.  `smd component list`
  still works for this release and points to `smd version`.

### Location

- **Bare-metal stations now read their GPSDO.**  Until now only the Proxmox image ran
  `sigmond-location-check`.  Every install now runs it every 10 minutes wherever sigmond
  sees a GPSDO, and it waits while `smd` holds its lifecycle lock.  The station's
  location comes from the GPSDO wherever one exists, as it already did on the image.
- sigmond records where each location came from, `gpsdo` or `operator`, whether the
  location check, `smd config location --from-gps` or `smd config identity --lat --lon`
  wrote it.

### Upgrading from v3.93

- The release needs hs-uploader f9698fd or later; an older one sends no drift row.
- `smd align --apply` installs and starts the two new timers after its bring-up
  finishes.  It backs up any sigmond unit it replaces (`*.bak-drift-<time>`), and it
  leaves alone a unit an operator excepted with `--accept`.

Read these before the roll.  The first `smd align --apply` on each station does more
than it did before.

- **The v3.93 dry run does not show this.**  A station at v3.93 runs its own smd for
  `smd align`, and that smd knows none of v3.94's new steps, so its dry run lists none of
  them.  `smd align --apply` moves sigmond first, re-runs itself from v3.94's smd, and
  then does them.  To preview them, run v3.94's smd from a temporary checkout, dry run
  only, as the operator without sudo.  `smd align --release v3.94` names the sigmond
  commit on its `sigmond` line:

  ```bash
  git clone -q https://github.com/HamSCI/sigmond ~/smd-preview
  git -C ~/smd-preview checkout -q <sigmond commit>
  ~/smd-preview/bin/smd align --release v3.94
  rm -rf ~/smd-preview
  ```

  sigmond's docs/admin/README.md describes this.  `smd update --apply` runs none of
  these steps; only `smd align --apply` does.
- **Rebuilds.**  The first align rebuilds ka9q-web, and onion with it, on every station,
  and restarts ka9q-web.  A ka9q-web build that fails leaves the old one running; align
  finishes the rest and exits 1.  On B4, on 2026-11-19, the ka9q-radio checkout had
  moved and the radiod binary had not.  Where radiod's binary trails its checkout that
  way, align rebuilds radiod once.
  Each radiod restart leaves a recording gap of about 60 seconds.  The dry run says
  `REBUILDS and RESTARTS radiod`.  A radiod that the site profile marks
  `locally_managed` never rebuilds.
- **Pins.**  SIGMA, KFS-MAIN and AC1SN keep a station pin file that held the decoders
  back.  Align retires that file, and their decoders and onion rebuild to the release.
  Every pin now comes from the release.
- **Timers you disabled by hand.**  Align enables the declared timers of running
  components and the timers sigmond's install enables, and starts them after its
  restarts.  It enables no service, and no hf-timestd timer that hf-timestd's service
  profile leaves off.  Before a station with
  hand-disabled timers runs align, record `smd align --accept unit:<timer>` for each
  one.  Otherwise align enables them again.
- **Magnetometers.**  A station without a magnetometer sees its magnetometer clients
  switched off.  A magnetometer that is missing but recorded before raises a fault in
  `smd check`, and align leaves the client alone.
- **Deploy order.**  wd30 must apply the `station_drift` `sender` column before the
  fleet board export runs.  The export selects that column and fails without it.
- **hf-timestd hotfix builds.**  B4 and SIGMA ran a hotfix build of the judge fix
  (branches `hotfix/*-judge-age`).  The same fix now sits on main in this release.  A
  station on such a build shows hf-timestd as diverged, and align leaves it.  Move its
  checkout to the release commit as the checkout's owner, then restart the timestd units.
- **Known: align may exit 1 on one step.**  Where `sigmond-site-timing` wrote a
  meteor-scatter instance file named `<CALL>=<SUFFIX>.toml` beside the live
  `<CALL>-<SUFFIX>` instance, the uploader routing step fails on that phantom instance.
  Everything else completes, and no data goes missing.  v3.95 fixes the naming.  Until
  then, align leaves `sigmond-location-check.timer` stopped on such a station; start it
  with `systemctl start sigmond-location-check.timer`.

### How it was proven

The sigmond suite (8,926 tests on the merged main), hs-uploader's suite and the
image repo's rig tests pass.  Reviews ran after every task and over the whole branch;
they caught, among others, four ways root could run another account's git or follow
its links, and the image-repo name that would have broken every station before the
rename.  On 2026-11-19 wd30 took the server half; on 2026-11-20 it added the drift
row's `sender` column.

On the rig, candidate 2 (`sigmond-v3.94-20261121-release.img`) passed the nested
image test, the update test from v3.93 forward and back, and the first bare-metal
install test.  Candidate 1 had passed the image and update tests; the bare-metal test
then found the bring-up and check faults listed above, and candidate 2 carries their
fixes.  On 2026-11-21 B4 (Proxmox) and SIGMA (bare metal) ran candidate 2 as
canaries: both aligned, their units stayed up and their timing authority stayed
fresh.  Both aligns exited 1 on the known meteor-scatter routing step above.

---

## sigmond-appliance v3.93

### Timing

- **CODAR timing holds.**  codar-sounder dropped samples while it processed each
  60-second block, and it stamped each block by counting blocks rather than samples.
  Every drop pushed all later timestamps early, by about 9 minutes an hour.  On
  2026-11-18 four stations ran 6 to 8 hours behind.  codar-sounder now gathers each
  block as the samples arrive and stamps it by its own position in the stream.  It also
  checks every batch of samples against radiod's RTP clock.  A mismatch (a long outage,
  a late packet, a radiod restart) discards the partial block, logs an error and
  re-anchors.  A new anchor that disagrees with the computer's clock by more than
  2 seconds never reaches a published label.
- On the server, sigmondb sets aside the CODAR data recorded before the fix, so no
  transmitter offset and no ionospheric product draws on it.  `python -m sigmondb
  set-aside list` shows each window and its reason.

### Uploads and storage

- **An upload store that stalls recovers by itself.**  When the station's upload store
  stayed locked for a while, psk-recorder's buffer filled and never tried to write
  again, so its FT8 spots stopped until a restart.  The buffer now tries to write before
  it gives up.
- **The storage trim keeps CODAR's local spot copy for 7 days.**  codar-sounder writes
  a local copy of each detection that nothing uploads; it grew without limit (31,000
  rows in five days at AB4EJ).  `CODAR_RETENTION_MIN` changes the 7 days.
- wspr-recorder writes 16-bit WAV files when a station's decoders cannot read float
  ones (Rob).

### Install and the Proxmox host

- **Give the station its full memory.**  Most station computers reserve 3 to 4 GB of
  RAM for built-in graphics that a station never uses.  The install guide now asks you
  to set UMA Frame Buffer Size to 64M in BIOS setup, and first boot warns when the
  reservation stays large.
- **`pm-memory`** on the Proxmox host reports that reservation and the station VM's
  memory.  `pm-memory --apply` grows the VM to the host's memory less 2 GB; the change
  takes effect at the VM's next restart.  pm-align installs it and prints its finding.

### Station software (sigmond)

- A station role, `full` or `radiod-only`, for a computer that runs only radiod and
  sends its streams to decoders elsewhere (Rob).
- An old full copy of the catalog in `/etc/sigmond/catalog.toml` can no longer pin a
  repository the catalog has replaced; `smd align` fetches ka9q-web from the right place.
- Smaller fixes: the remote-access install guard, the Zen 3 CPU-frequency default, no
  service enabled before its user exists, and FFT plans kept for a locally managed
  radiod (Rob).

### How it was proven

The rig built the v3.93 image (tag 2763b24) and passed the whole ladder on
2026-11-18: the golden VM, the release image, the nested test (13:17Z) and the update
test from v3.92 (14:21Z).  Two stations then ran the unblessed candidate as canaries:
B4 (Proxmox; the VM from 14:33Z and the host from 14:44Z) and SIGMA (bare metal, from
14:39Z).  After 27 hours, B4's `smd check` reads clean and its uploads deliver.  SIGMA
uploads and records too.  Its magnetometer has failed since 2026-11-17, before the
canary began: the sensor stopped answering, so the fault lies with the sensor or its
USB link, not this release.  On both stations `smd align` reported one failure, an old
meteor-scatter instance left over from a reporter rename (ledger:
reporter-rename-duplicates); it ran nothing.

---

## sigmond-appliance v3.92 — blessed 2026-11-18

### Decoding and uploads

- **psk-recorder runs one FT8 decoder per group of bands** instead of one per band, and
  each decoder's shared memory holds about 2 MB instead of 46.  Across nine stations
  that gave back about 3.5 GB of memory, with no lost decodes.
- hs-uploader waits 30 seconds for a busy upload store instead of 5, and its FTP
  fallback names the right sender.

### Station software

- `smd align` now runs the storage guard.
- hf-timestd's `config apply` keeps empty settings tables and the file's owner.
- The time-server check moved into sigmond.  It waits for chrony to choose the local
  server before it judges it, and it arms T6 from a TS-1 the operator has declared.
- Every host gets the satellite guard (off until the station turns it on) and
  smartmontools.

### How it was proven

The rig built v3.92 from tag commit c7a71f1.  Its nested test installed v3.92 from
scratch, and its update test carried a v3.91 station forward and back.  The new FT8
decoding ran on nine stations from 2026-11-16 with no OOM kills and no FT8 drop traced
to it.  AC0G-B4 updated to v3.92 in place on 2026-11-18.  No station has yet installed
v3.92 from scratch on hardware.

---

## sigmond-appliance v3.91 — blessed 2026-11-13

- **An archive drive for the raw time-station recordings.**  `smd archive add` reads a
  USB drive's health and runs its short self-test before it erases the drive, refuses a
  failing one, and mounts it so it returns by itself after a replug.  hf-timestd's
  archiver copies the recordings in half precision, checks every sample, and drops the
  oldest day when the drive passes 90 %.  `smd archive status` shows the days held and
  the days that still fit.
- HFDL and CODAR run on every station.
- `smd storage guard` makes every program that writes to a data disk wait for it, and
  pm-storage refuses a whole-VM snapshot while a data disk is attached.
- `sigmond-flash-latest.sh` names its version on every run and gains `--about`.

### How it was proven

The rig built v3.91 from tag commit eb925c1.  Its nested test installed v3.91 from
scratch, and its update test carried a v3.90 station forward; a test-harness memory cap
stopped the update test's later phases, rollback included.  Rob installed v3.91 from
scratch on hardware on 2026-11-13.

---

## sigmond-appliance v3.90

- On a host with two RX888s, sigmond gives the full 129.6 Msps rate to the receiver
  hf-timestd uses, or to one the operator names with `smd config init radiod
  --full-rate`; the others run at 64.8 Msps, and sigmond says which card got which rate
  (Rob).
- The wizard offers a time server on the local network only when it finds a GPS
  (stratum-1) server; otherwise it keeps the public pool.
- Re-running the wizard's remote-access step refreshes each radiod's web page without
  changing the station's remote-access number or ports.
- `sigmond-flash-latest.sh` downloads the newest image and writes it only to a whole,
  removable USB disk, asks its questions before the download, and reads the stick back.
- radionet, which links one site's receivers to another's, stays off unless a station
  asks for it, and accepts traffic only from the peers its configuration lists.

v3.89 was built but never blessed: v3.90 replaced it the same morning.

### How it was proven

The rig built v3.90 from tag commit 62f1611; its nested test installed it from scratch,
and its update test carried a v3.88 station forward and back.  No station installed
v3.90 from scratch on hardware.

---

## sigmond-appliance v3.88

- sigmond lays out radiod's CPUs on a host that runs one thread per core, where it used
  to refuse; radiod's other threads go to the worker CPUs.
- First boot says why the station VM did not start when VT-x is off in the BIOS, warns
  when VT-d (the IOMMU) is off, and says plainly when a station has no CPU layout, so
  none of these looks like a hung install.

### How it was proven

The rig built v3.88 from tag commit 1d274b8; its nested test installed it from scratch,
and its update test carried a v3.87 station forward and back.

---

## sigmond-appliance v3.87

Three fixes found when AC0G-B4 updated to v3.86:

- The 15-minute storage trim runs again; v3.86's storage ledger had taken over its
  command name, so every v3.86 station stopped trimming its spot tables.
- `pm-align` fetches `pm-storage`, so each station's Proxmox host reports its storage
  pool to the ledger.
- The FFT plan maker keeps radiod stopped until it finishes, and restarts a radiod that
  started early, so radiod always runs on the new plans.

### How it was proven

The rig built v3.87 from tag commit 29e0739; its nested test installed it from scratch,
and its update test carried a v3.86 station forward and back.

---

## sigmond-appliance v3.86

- sigmond keeps a ledger of what each client stores and how much room it has left; its
  stop lever ships switched off.
- A station lists its receivers in the site profile, and bring-up and the upload
  manifest read that list.
- sigmond installs on a bare-metal machine with no Proxmox host, and remote access comes
  up there too.
- `smd update` rebuilds a native decoder whose source sits off sigmond's pin; the
  UberSDR decoders move to 8719788 (faster MSK144, F32 I/Q).
- The installer no longer loses the guest-agent drop-in to a race in its own checks.

### How it was proven

The rig built v3.86 from tag commit 1286757; its nested test installed it from scratch,
and its update test carried a v3.85 station forward and back.

---

## sigmond-appliance v3.85

- A station without a T5 reference can place T6 with a GPS time server on its LAN, and
  the timing judge checks internet time before it refuses T6 on internet time's word.
- Every new station decodes WWVB, without feeding WWVB into its timing.
- ka9q-python 3.26.2 waits until radiod confirms the sample format a client asked for.
- A time server named in the wizard becomes a T4 source.
- First boot waits for a slow USB stick instead of giving up, and its closing summary no
  longer prints an empty address.

### How it was proven

The rig's nested test installed v3.85 from scratch, and its update test carried a v3.84
station forward and back.

---

## sigmond-appliance v3.84

- `smd align` builds the UberSDR decoders when a station lacks their source.
- `smd admin cli-reference` records every station command, and the station pages show it
  under Commands.
- hs-uploader finds its store by one rule, and the clients take their sink writer from
  hs-uploader.
- Rob's wizard changes: no question about status heartbeats (both heartbeat timers
  stay off); a question about a local NTP server, with a public pool capped at four
  servers otherwise; the grid taken from the GPS clock's position.

### How it was proven

The rig's nested test installed v3.84 from scratch, and its update test carried a v3.83
station forward and back.

---

## Older releases (v3.76 to v3.83)

These notes moved here from INSTALL.md unchanged.

sigmond-appliance v3.83: v3.82 plus three fixes and Rob's boot fixes.  `smd update` and `smd align` now run a component's deploy.toml build when it has no `install.sh`, so station-web and hamsci-physics get their new dependencies; each v3.82 station rebuilds those two once on its first update.  Install probes the magnetometer's I2C addresses every time and writes what answers, and the recorder refuses all-zero samples.  `smd psws save-keys` and `smd psws restore-keys` carry a station's upload keys across a reflash (§4).  Turning remote access off now also stops the old single-gateway tunnel service on a two-gateway station, so off survives a reboot.  Rob's fixes cover DNS and dhclient at boot, the power key, the console panel, wspr-recorder and psk-recorder.  The rig's nested test installed v3.83 from scratch, and its update test carried a v3.82 station forward to this code and back; station-web rebuilt and answered after the update.  ⚠ **No station has yet installed v3.83 from scratch on hardware**, and no station has yet run `smd update` then `smd align` from v3.82 on hardware.  The v3.82 note follows.

sigmond-appliance v3.82: v3.81 plus a menu at the console, colour on the console panel, and five fixes.  A root login at the station's own screen and keyboard now opens a menu (§8).  The panel shows addresses in cyan, logins in green and the starting password in yellow.  Bring-up no longer pulls any repository, so a station installs the commits its image carries.  `smd update` now passes `--yes` to each installer, so hf-timestd's setup wizard waits instead of stopping the update.  A rollback puts back any `uv.lock` an installer rewrote.  psk-recorder gets a private, writable `/tmp`.  The wspr-uploader warning about a stale links record now names the one file to delete.  The rig's nested test installed v3.82 from scratch, and its update test carried a v3.81 station forward to this code and back with no exception.  ⚠ **No station has yet installed v3.82 from scratch on hardware**, and no one has yet seen the menu or the colours on a real monitor.  The v3.81 note follows.

sigmond-appliance v3.81: v3.78 plus sink-control step 2b part 2 (sigmond writes each client's upload switch into `pipelines.d/` and `site.toml` together, and `smd sink off` stops a linked wspr-uploader rather than disabling it), Rob's UberSDR builds of wsprd and jt9 as the default decoders, `smd watch usb`, and two install fixes: hf-timestd's chrony script now carries its execute bit, and superdarn-sounder's install no longer rewrites its own `uv.lock`.  The rig's nested test installed v3.81 from scratch, and its update test carried a v3.78 station forward to this code and back.  ⚠ **No station has yet installed v3.81 from scratch on hardware.**  ⚠ Rolling a station back to v3.78 leaves superdarn-sounder's `uv.lock` modified; v3.82 fixes this.  The v3.78 note follows.

sigmond-appliance v3.78: v3.77 plus sink-control step 2b part 1 (hs-uploader reads `pipelines.d/`, its store moves to version 3, and nothing a station sends changes), the pairing fix for sudo-rs hosts, and Rob's RX888 recovery changes.  v3.77 added the `smd restart <client>` fix to v3.76.  Michael installed v3.78 from scratch on AC0G-B4 on 2026-10-22: bring-up finished `ok` with every service running (one RX888 replug behind a new hub).  ⚠ **No IPv6-only site has yet installed v3.78 from scratch**; the IPv6 fix it carries was proven by hand on a live IPv6-only station.  The v3.76 note follows.

sigmond-appliance v3.76 (built from 4b7ce17): v3.75 was installed on hardware by AI6VN (2026-10-21) at a WIRED IPv6-ONLY site and came up **unreachable** — a forwarding Proxmox host drops every router advertisement unless `accept_ra` is 2, so the station lost its IPv6 default route and never learned the RA's DNS64 resolver, and remote access could not resolve its own server name.  v3.76 is v3.75 plus that fix, which was proven on the live station by hand (the default route and the RDNSS server returned within seconds).  ⚠ **v3.76 has not yet been installed from scratch**; the nested rig test does not exercise this path, because it runs dual-stack with working DNS.  ⛔ Do not install v3.74 or v3.75 at a site with no IPv4.

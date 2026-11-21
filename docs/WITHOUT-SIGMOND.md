# A client without sigmond

> **Audience:** all
> **Status:** draft
> **Verified against:** sigmond 4006b0a on 2026-11-21 — code and docs (the contract, orchestration.md, the systemd units in `systemd/`)
> **Canonical for:** what a client does on its own, and what sigmond adds when it runs a station

Every HamSCI client runs on its own. wspr-recorder, psk-recorder,
codar-sounder (soon chirp-recorder), hf-timestd and the rest each install
from their own repository, read their own configuration, find their radio,
do their work and write their results with no sigmond present. The
[client contract](CLIENT-CONTRACT.md) makes this its first promise: a client
"runs **standalone** with no sigmond present, using only its own config
file."

sigmond adds nothing to what a client knows how to do. It records, decodes
and uploads nothing ([orchestration.md](contributor/orchestration.md)). What
it adds lies between the clients and around them: the facts one client
cannot know about the station, the arbitration of what they share, the
care of the radio beneath them, and the record of what the station holds.
This page says what a client keeps without sigmond, and what it goes
without.

## What a client keeps

Each contract-conformant client brings, on its own:

| It keeps | How |
|---|---|
| Its configuration | its own file under `/etc/<client>/`, whose schema it owns (contract §1) |
| Its radio | a `radiod_status` name in its own config, the standalone fallback for the radiod id sigmond would resolve (§2) |
| Its installation | its own `install.sh`, driven by the same `deploy.toml` sigmond reads, giving the same production layout (§4) |
| A safe place on the network | `ka9q-python` gives each client its own data multicast group, so two clients on one host never collide, with no coordinator ("the hard rule", §7) |
| Its work | recording, decoding, measuring: the whole of its own job |
| Its output | its own files and tables; a client whose sink is not set up gets a no-op writer and keeps running (orchestration.md, *Layers*) |
| Its uploads, where it owns them | wspr-recorder, for example, ships a standalone `wspr-uploader.service` |
| A description of itself | `inventory --json` and `validate`, answered whether or not anyone asks (§3) |

A single client on a well-provisioned machine, beside a radiod someone else
looks after, can run this way indefinitely. Several clients on one host can
too: the contract forbids any client from needing sigmond to avoid another.

## What it goes without

Without sigmond, each client still does its job, but nobody looks after the
station as a whole. Each row names what sigmond provides and what a client
then has to do without.

**The station's own facts.** sigmond publishes them in
`/etc/sigmond/coordination.env`, which a client reads and never has to
write.

| sigmond provides | Without it |
|---|---|
| Identity: callsign, grid, latitude and longitude, reporter id, PSWS station and instrument ids, PSKReporter and WSPRnet calls (`STATION_*`) | each client's config repeats them by hand, and they drift apart |
| Each radiod's facts: status name, sample rate (`RADIOD_<ID>_*`) | each client names its radiod itself and must be edited when it moves |
| The station location, kept honest by its GPSDO (`sigmond-location-check`) | a moved station uploads its old grid on everything, silently |
| Runtime log levels, set without a restart (§11) | each client's level lives in its own config |

**Timing.**

| sigmond provides | Without it |
|---|---|
| Discovery of the timing authority, so every client labels its samples from the same reference (§18) | each client falls back to its own default: the RTP stream's mapping, or the host clock |
| The BPSK PPS chain delay for each radiod (§8) | no client corrects for it |
| One owner for recovering the timing chain: GPSDO, gpsd, chrony, hf-timestd (`sigmond-timing-reconcile`) | each part recovers alone, or not at all |
| Watchdogs: re-anchoring psk-recorder streams whose timing broke, restarting the core recorder when T6 sticks against a good signal | a broken stream stays broken until someone notices |
| Pre-created timing shared-memory segments for chrony, gpsd and hf-timestd | start order decides whether they meet |

**The radio beneath the clients.**

| sigmond provides | Without it |
|---|---|
| CPU placement: radiod's FFT on a core of its own, its other threads together, clients kept off radiod's cores, USB and other interrupts steered away (`sigmond-radiod-thread-pin`, `-park`, `-rx888-irq-affinity`, `-guest-irq-affinity`) | clients and interrupts land on radiod's cores; the symptom is sample loss, seen as gaps, not as an error |
| FFTW wisdom for radiod's transforms (`sigmond-wisdom`) | radiod plans its FFTs without it, slower |
| A watchdog for a wedged radiod, recovery for a vanished RX888, and a restart of the clients that predate radiod's latest start | each client reconnects as best it can, or waits |
| Name resolution for radiod's `.local` names, and on-host multicast routing | a client finds no radiod after an avahi hiccup |

**What the clients share.**

| sigmond provides | Without it |
|---|---|
| Cross-client checks before clients start: CPU isolation from radiod, frequency coverage, which radiod each client uses (harmonization, `lib/sigmond/harmonize.py`) | clients can crowd radiod or each other, and nothing says so until data goes missing |
| The storage ledger: every client's holdings on every store, trimmed in stages when a store fills (contract §17.7, `sigmond-storage`) | each client keeps its own retention; a full disk stops them all |
| The uploader manifest, rendered from each client's declared pipelines | each client's uploads are set up by hand, one at a time |
| Start order: radiod first, then clients, stop in reverse | units start in whatever order systemd picks |
| An archive drive, with health checks and an archiver | long-term copies are each client's own business |

**Knowing what the station is.**

| sigmond provides | Without it |
|---|---|
| Install, update and `smd align` to a blessed release, with a record of what changed | each client is installed and updated by hand, and nothing records the set |
| The drift report: how the station differs from its release | a station's actual contents are a matter of memory |
| Decoder selection, with the WSJT-X parity check | each client finds whatever `jt9` or `wsprd` is on the path |
| Run with purpose: a client runs where its input exists, and a missing device counts as a fault | a client without its device runs idle, or fails, unnoticed |
| The heartbeat and the fleet board, service-event and gap sampling, decode health, read-only snapshots for station-web | the station tells nobody how it is |
| Remote access through the gateway, the satellite-link schedule, the private-network guard | each is set up and watched by hand |

## Why the line falls here

Each client knows its own signal better than any coordinator could, so the
work stays in the client. What no client can know alone (the other clients
on the host, the radio's needs, the station's identity, how full the disk
is, what release the station should run) sigmond knows, because it sees the
whole station. Keeping the line there lets a client be developed, tested and
run anywhere, and lets sigmond run any client that meets the contract
without changing itself.

The same line tells a client author what not to build. A client should not
pin CPUs, trim other clients' data, choose the station's decoder, or carry
the station's identity in its own config beyond the standalone fallback.
Those belong to sigmond, and a client that does them fights it.

## Running one client without sigmond

Each client's own README says how: its `install.sh`, its configuration
file, and its `radiod_status` fallback. The contract sections above say
what every client promises. Expect the client to do its job; expect to do
by hand, or go without, everything in the second half of this page.

## Read next

- [CLIENT-CONTRACT.md](CLIENT-CONTRACT.md) ★: the seam, section by section.
- [contributor/orchestration.md](contributor/orchestration.md) ★: how sigmond works, layer by layer.
- [ADD-A-CLIENT.md](ADD-A-CLIENT.md): writing a client that does its half.
- [native-binaries.md](native-binaries.md): compiled helpers a client brings with it.

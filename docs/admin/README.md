# Administrator guide — running the HamSCI sigmond fleet

> **Audience:** fleet administrator
> **Status:** current
> **Verified against:** sigmond f3b0c6e on 2026-11-18 — code: `bin/smd` argparse for every verb named here, `scripts/proxmox/pm-align.py`, `lib/sigmond/instance_split.py`, `lib/sigmond/uniform_routing.py`, `systemd/sigmond-storage.timer`; sigmond-appliance `docs/RELEASE.md`, `bless-release.sh` and `test-update-v3.sh`
> **Canonical for:** what a fleet administrator does

An operator hosts one station.  An administrator looks after all of them
together.  This page describes that job: what an administrator does, why each
task exists, and which `smd` commands and documents each task involves.  It
does not teach the mechanics of reaching a station.  Those depend on access
that only a current administrator can grant.

## How to become one

Every task below needs privileges that an ordinary operator account lacks.
You need them on the stations themselves, and on the infrastructure behind
them: the upload path, the release process, and the remote-access channel.
A current HamSCI sigmond administrator grants that access.  Nobody gets it by
installing software or reading a page.

To ask, open an issue on the
[HamSCI sigmond repository](https://github.com/HamSCI/sigmond/issues), or
reach HamSCI through [hamsci.org](https://hamsci.org).  Say who you are, which
stations you help with, and what you want to do.  A current administrator
replies, decides what access fits, and walks you through the private details
this page leaves out.

Until then, this page and the [operator guide](../operator/README.md) show you
the shape of the work.  Most of the commands below also run read-only on any
station, so an operator can watch them work.

## The job in one table

| Task | Why it exists | Main commands | Read more |
|---|---|---|---|
| Cut and prove a release | A station should run only a combination of code that someone has tested together | `smd version`, `smd align --manifest`, the appliance's `bless-release.sh` | [RELEASE.md](https://github.com/HamSCI/sigmond-appliance/blob/main/docs/RELEASE.md) |
| Keep stations current | Fixes help no one until they reach the stations | `smd update`, `smd align`, `pm-align`, `smd version` | [day-2 §Updates](../operator/day-2.md#updates--who-decides-and-what-you-run) |
| Watch the fleet | A station can run, look healthy, and send nothing | `smd status`, `smd check`, `smd watch uploads`, `smd sink status` | [day-2](../operator/day-2.md), [registration §6](../operator/registration.md#6-confirming-everything-flows) |
| Manage storage | Raw IQ fills a disk in hours, and a full disk stops a station | `smd storage status`, `smd archive add\|remove\|status` | [storage design](../superpowers/specs/2026-11-04-storage-capacity-design.md) |
| Set uploads and identity | Data must leave under the right name, to the right place | `smd sink`, `smd config reporters`, `smd config psws`, `smd psws` | [registration](../operator/registration.md) |
| Reach a station remotely | Many problems need hands on a station the operator cannot fix | `smd remote status\|on\|off` | [remote access](../operator/remote-access.md) |
| Shape multi-receiver hosts | One site can carry several receivers, or a host with radiod alone | `smd admin instance split`, `smd admin sources` | [radiod-only](../operator/radiod-only.md), [multi-instance](../MULTI-INSTANCE-ARCHITECTURE.md) |
| Work with operators | The station belongs to the operator; the administrator only borrows it | — | [do-not-touch](../operator/do-not-touch.md) |

The sections below take each row in turn.

## 1. What a release means

A release carries the name "sigmond vN", for example sigmond v3.93.  It
consists of three things.

- **The blessed list.**  One commit for every component: sigmond itself,
  radiod, hf-timestd, the recorders, the uploader and the rest.  A release
  names a combination that someone has tested together, not just "the newest
  code".
- **Two install paths.**  A new Proxmox station installs from a USB image,
  which the sigmond-appliance repository builds.  A bare-metal station
  installs from the repositories directly.  Both land the same blessed list.
- **`smd align`** for stations already running.  A live station never gets
  reimaged.  `smd align` moves it in place to the release's list.

### How a release earns the word "blessed"

The sigmond-appliance repository's
[RELEASE.md](https://github.com/HamSCI/sigmond-appliance/blob/main/docs/RELEASE.md)
holds the full procedure.  Its shape runs like this.

1. **Build.**  A build machine first assembles a golden VM, a template station
   with every component installed.  It records the components' commits from
   inside that template, so the manifest comes from what really got
   installed, never from a typed list.  It then builds the USB image from the
   template.
2. **Test the install.**  A nested test boots the image inside a virtual
   Proxmox host and drives it through install, first boot, the setup wizard
   and the first-run bring-up.  It passes only when the station it installed
   runs.
3. **Test the update.**  A second nested test boots the previous release and
   rolls it forward to the new one with `smd update` and `smd align`, then
   rolls it back.  A release must install cleanly and update cleanly.
4. **Canary.**  Two real stations run the candidate for about a day: one
   Proxmox station and one bare-metal station.  Each aligns to the
   candidate's manifest with `smd align --manifest`, which records the station
   as on a candidate, never as on a blessed release.  The administrator
   watches for stable services, no out-of-memory kills, uploads arriving,
   healthy timing, and an align free of surprises.
5. **Document.**  The administrator updates the install instructions and
   writes the release's changelog section, saying what changed, what an
   operator should do differently, and what got fixed.
6. **Bless.**  `bless-release.sh <version>` checks nine gates.  The tag sits
   on the main branch, the tree stays clean, the checksum verifies, the
   manifest holds its components, the test log shows a pass for this exact
   image, the install pages and the changelog name this release, and no
   release for the tag exists yet.  It runs dry by default.  Only with
   `--apply`, and only when every gate passes, does it publish the release.
   Before publishing, it scans the notes for hostnames, addresses and paths,
   and refuses to leak them.

Every rung asks for evidence tied to the exact artifact.  The procedure grew
from releases that someone called "verified" against the wrong file.

## 2. Keeping stations current

Two directions of update exist, and they never mix (sigmond's
[CONTRIBUTING.md §3](../../CONTRIBUTING.md#3-updating--two-orientations-never-mixed)).
In the first, a station pulls: someone on the station runs `smd update` or
`smd align`.  In the second, the administrator pushes: the same mechanism
runs from outside, one host at a time.

| Command | Where | What it does |
|---|---|---|
| `smd version` | station | Prints what really runs: the image the station started from, then every component's live commit, then the recent updates.  The file `/etc/sigmond-appliance/version` records only the install and goes stale. |
| `smd update` | station | Plans an update and prints it.  `--apply` performs it.  A station fresh from an image holds each component at its blessed pin; `--unpin` releases the holds, and `--latest` moves everything to the newest code, for test stations only. |
| `smd align` | station | Shows how far the station sits from the latest blessed release.  `--apply` moves the components, refreshes image files, re-runs bring-up and restarts stale services, radiod first.  `--release` names an older release; `--manifest` names an unblessed candidate; `--verify` reports gaps and uploads since the last align. |
| `pm-align` | Proxmox host | The host's counterpart to `smd align`.  It brings the host's own files level with the release and pairs the host with its decoder VM.  It never touches the host's network, CPU tuning, Proxmox packages or VM configuration. |
| `smd admin manifest adopt` | station | Records a blessed manifest on a live station whose components already match it.  It refuses unless every commit matches. |
| `smd fleet status`, `smd fleet update` | administrator's machine | Ask every station in the administrator's inventory how it compares with its blessed pins.  `fleet update` plans canary first, verify, then the rest. |

Each command here that changes a station runs as a dry run unless given
`--apply`.  Read the plan before applying it.

The order matters more than the commands.  The canary stations take a release
first.  The rest of the fleet follows only after the canaries run clean.  A
bad build then breaks one or two stations instead of all of them.

A Proxmox station has two halves: the decoder VM, where sigmond runs, and the
host underneath.  A release can change both.  Run `smd align` in the VM and
`pm-align` on its host; one alone leaves the station half updated.

## 3. Watching the fleet

A station can run every service, report green, and still send nothing.  An
administrator therefore watches what arrives outside the station, not only
what the station says about itself.

| Signal | What to look for |
|---|---|
| Spots at wsprdaemon.org | Each station's WSPR, FT8/FT4 and MSK144 spots arrive under its reporter ID.  A station silent for a week has a fault, unless its sink switch holds uploads back on purpose.  A silent hour means only quiet bands. |
| PSWS products | Daily timing products (and magnetometer products, where fitted) appear under the station's PSWS station ID. |
| `smd check` | One line per item and a verdict: PASS, WAIT or FAIL.  It runs read-only and needs no root, so an operator can run it and paste the answer. |
| `smd status` | Which services run, which wait for hardware, and which hardware sits detected but not yet adopted. |
| `smd sink status` | Whether the site sink switch lets data out.  It explains most "why no spots" questions before anything else needs checking. |
| `smd watch uploads` | A live view of upload activity on one station. |
| `smd storage status` | Each store's fill and each client's share (section 4). |
| `smd version` | Whether a station's components match the release it claims. |

Stations used to send a heartbeat every five minutes to a central fleet board.
Since v3.84 the setup wizard leaves the heartbeat off, so newer stations send
none.  HamSCI plans to carry station status over the upload path instead; that
has not shipped.  Until it does, arrival of data stands as the best sign of a
working station.  The operator's view of the heartbeat lives in
[day-2 §The heartbeat](../operator/day-2.md#the-heartbeat--what-your-station-tells-the-fleet).

## 4. Storage

Raw IQ fills disks.  hf-timestd keeps a rolling ring of raw IQ recordings,
and other clients keep their own data.  On 2026-11-03 four station VMs
stopped when the disk pool under them filled.  No software on those stations
knew the pool existed.

sigmond now keeps a storage ledger.  `sigmond-storage.timer` runs it every two
minutes.  The ledger measures every store a station depends on, gives each
client a share, and tells each client to trim its own data to fit.  It never
deletes a client's files itself.  When space runs short it raises an alarm
early, tightens the shares in stages, and names who holds the space.

| Command | What it does |
|---|---|
| `smd storage status` | Each store's fill and stage, each client's holding and target, and any alarms. |
| `smd storage share <client> <weight>` | Gives one client a larger or smaller share. |
| `smd storage directive ...` | Overrides a client's last-resort policy, or marks the station hands-off for a while, so the ledger alarms but stops nothing. |
| `smd archive status` | The archive drive: how full, and how many days of raw IQ fit. |
| `smd archive add` | Attaches a USB drive as the archive.  It refuses a drive on the RX888's hub and erases the drive before use, unless the drive already holds a sigmond archive. |
| `smd archive remove` | Unmounts the drive so someone can unplug it.  `--forget` also drops its record. |

With an archive drive attached, hf-timestd's archive timer copies raw IQ to it,
and the station keeps many more days.  Since 2026-11-18 hf-timestd stores raw
IQ in 16-bit floats by default, which halves the bytes.

`smd archive add` erases a drive.  An administrator runs it only with the
operator present or with their agreement, and only on a drive the operator
supplied for the purpose.  The design note
[2026-11-04-storage-capacity-design.md](../superpowers/specs/2026-11-04-storage-capacity-design.md)
explains the stages, the shares and the order in which data gives way.

## 5. Uploads and identity

Every WSPR, FT4/FT8 and MSK144 spot goes to wsprdaemon.org alone.
wsprdaemon.org keeps it and forwards copies to wsprnet.org and
pskreporter.info under the spot's reporter ID.  No station posts to those
sites directly.  Timing and magnetometer products go to PSWS.  An
administrator keeps that routing uniform across the fleet.

| Command | What it sets |
|---|---|
| `smd sink status\|off\|fill\|upload` | The site sink switch.  `off` sends nothing and stores nothing new; `fill` stores but sends nothing; `upload` stores and sends.  A new station starts held back, and nothing leaves until someone runs `smd sink upload`.  `off` and `fill` take a reason, which explains the choice to anyone who looks later. |
| `smd config reporters --uploader-call <NAME>` | The name this machine uploads under.  Each receiver's spots keep their own reporter ID; this sets the name of the uploading machine. |
| `smd config psws --station <ID> --instrument <recorder>=<ID>` | The PSWS station ID and one instrument ID per product, written to the site profile and rendered into each recorder. |
| `smd psws status\|enroll\|verify` | Enrollment state, the key to register at the PSWS portal, and a live login test. |

The operator's side of this, including the one step at the PSWS portal, lives
in [registration.md](../operator/registration.md).

An administrator sets the sink switch below `upload` deliberately: a testbed
with no antenna, a station under construction, a bench checkout.  Data from
such a station would mislead the public databases.

## 6. Remote access

A station can carry a remote-access channel, from the sigmond-rac component,
that lets an administrator reach it from outside the operator's network.
On a Proxmox station the channel runs on the host, and `smd remote` in the VM
asks the host to switch it.

| Command | What it does |
|---|---|
| `smd remote status` | Shows whether remote access stands on, off, or wanted on but not up. |
| `smd remote off` | Turns it off.  It stays off across reboots. |
| `smd remote on` | Turns it on again. |

The operator owns that switch.  An administrator uses the channel only with
the operator's consent, and an operator who turns it off has made a decision,
not a mistake.  [remote-access.md](../operator/remote-access.md) tells the
operator what the channel exposes and to whom.  A current administrator
explains how to connect through it.

## 7. Multi-receiver and transporter hosts

Not every site has one receiver on one machine.

- **A receiver** means an antenna, an RX888 and a radiod.  Its data carries
  its own reporter ID.
- **A radiod-only host** runs the receiver and nothing else.  A separate
  decoder host reads its multicast streams.  [radiod-only.md](../operator/radiod-only.md)
  covers when to choose it and how to attach it with `smd admin sources`.
- **A transporter host** runs clients for receivers that live on another
  machine.  It only sends the upload; each row still names the receiver that
  produced it.

codar-sounder and hfdl-recorder run one instance per receiver.
`smd admin instance split codar-sounder` (or `hfdl-recorder`) converts an
older station, where each client ran one instance per radiod, to that shape.
It runs dry by default, writes every new configuration before it stops
anything, and never touches radiod.
[MULTI-INSTANCE-ARCHITECTURE.md](../MULTI-INSTANCE-ARCHITECTURE.md) gives the
design.

## 8. Working with operators

Every station belongs to its operator.  The operator supplies the power, the
network, the antenna and the place.  An administrator works on it as a guest.

Before changing a station, an administrator asks:

- Does the operator know, and agree?  Say what will change and when.
- Does the change restart radiod?  A radiod restart interrupts every client
  on the station, and the recorders must restart after it.  `smd align --apply`
  and some configuration changes restart it.  Plan for that, and never restart
  radiod casually.
- Has the release passed its canaries?  Apply only blessed releases to an
  ordinary station.
- Does the change touch something on the
  [do-not-touch list](../operator/do-not-touch.md)?  Those guard rails apply to
  administrators too.
- Will another administrator work on the same station at the same time?
  Announce the change to the other administrators before making it, and
  record what changed afterwards.

After the change, check from outside: data arriving, `smd check` passing,
`smd version` showing the intended commits.  A change stays unfinished until
those checks pass on the station it targeted.

## Where to read more

| Page | Covers |
|---|---|
| [operator/README.md](../operator/README.md) | The operator's view of a station; read it first |
| [operator/day-2.md](../operator/day-2.md) | Healthy operation, the weekly check, updates from the station's side |
| [operator/registration.md](../operator/registration.md) | Reporter IDs, PSWS enrollment, confirming data flows |
| [operator/remote-access.md](../operator/remote-access.md) | What the remote-access channel exposes, and the operator's switch |
| [operator/do-not-touch.md](../operator/do-not-touch.md) | The guard rails, and why each exists |
| [operator/radiod-only.md](../operator/radiod-only.md) | Radiod-only hosts and attaching them to a decoder host |
| [operator/glossary.md](../operator/glossary.md) | The vocabulary |
| [commands/smd.md](../commands/smd.md) | The `smd` command reference |
| [contributor/orchestration.md](../contributor/orchestration.md) | How sigmond installs, configures and starts its clients |
| [CLIENT-CONTRACT.md](../CLIENT-CONTRACT.md) | What every client promises sigmond |
| [MULTI-INSTANCE-ARCHITECTURE.md](../MULTI-INSTANCE-ARCHITECTURE.md) | Per-receiver client instances |
| [superpowers/specs/2026-11-04-storage-capacity-design.md](../superpowers/specs/2026-11-04-storage-capacity-design.md) | The storage ledger |
| [superpowers/specs/2026-09-24-smd-align-design.md](../superpowers/specs/2026-09-24-smd-align-design.md) | `smd align` and `pm-align` |
| [CONTRIBUTING.md](../../CONTRIBUTING.md) | Update orientations (§3), pins (§5), cutting a release (§6), the heartbeat and board (§10) |
| [sigmond-appliance RELEASE.md](https://github.com/HamSCI/sigmond-appliance/blob/main/docs/RELEASE.md) | The release ladder in full |

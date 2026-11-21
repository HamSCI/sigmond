# Installing sigmond step by step

> **Audience:** operator, contributor
> **Status:** current (moved from the root README 2026-11-21, not re-verified)
> **Verified against:** sigmond 4006b0a on 2026-11-21 — docs (text moved verbatim from README.md)
> **Canonical for:** installing sigmond by hand, one smd step at a time

This page held the root README's Quick start until 2026-11-21. The
canonical bare-metal path is [install-bare-metal.md](install-bare-metal.md);
the Proxmox image has its own guide,
[sigmond-proxmox INSTALL.md](https://github.com/HamSCI/sigmond-proxmox/blob/main/INSTALL.md).
Use this page when you want each step by hand.  Its overlap with
[install-quickstart.md](install-quickstart.md) is a known docs gap, to be
merged.

## The steps

These are the bare-metal steps with their details; [docs/install-bare-metal.md](install-bare-metal.md)
puts them in order with what each does to your machine.  For the USB image, see
[the sigmond README](../README.md#start-here).

### 1. Install sigmond

Sigmond installs at `/opt/git/sigmond/sigmond/`, peer to its managed
components. Clone and install from there:

```bash
sudo mkdir -p /opt/git/sigmond
sudo chown $USER /opt/git/sigmond
git clone https://github.com/HamSCI/sigmond /opt/git/sigmond/sigmond
cd /opt/git/sigmond/sigmond
./install.sh
```

`install.sh` creates a `sigmond` system user that owns
`/opt/git/sigmond/*`, adds you to the `sigmond` group so you can edit
sources as yourself, sets up `/opt/git/sigmond/sigmond/venv`, and symlinks `smd` to
`/usr/local/bin/smd`. Open a new shell after install (or
`newgrp sigmond`) to pick up the group membership.

> **Running under Proxmox?** `install.sh` detects KVM guests and offers
> to configure the Proxmox host (PCIe passthrough, CPU isolation, vfio,
> hookscript) before installing sigmond — one prompt, one host reboot,
> automatic resume. See [`docs/installation-guide.md`](installation-guide.md)
> "Running under Proxmox VE? Read this first."

### 2. Bring up the station

One command installs, configures and starts a whole station on this
machine.  It works the same on bare metal and inside a VM:

```bash
smd bringup dasi2 --callsign AC0G --grid EM38ww --reporter AC0G/S \
                  --psws-station-id S000418 \
                  --psws-instrument hf-timestd=367 \
                  --psws-instrument mag-recorder=401
```

Use your own callsign, grid square, reporter ID and PSWS ids.  The PSWS flags
matter only if you upload to PSWS: one station ID for the site, and one
instrument ID per product under it (the RX888 for hf-timestd, the
magnetometer for mag-recorder).  Bring-up writes them to
`/etc/sigmond/site-profile.toml` and into each recorder's config;
`smd config psws` sets them later.
`dasi2`, the default profile, gives you a local radiod, hf-timestd (GRAPE),
WSPR, FT4/FT8, meteor scatter, HFDL and CODAR timing, the magnetometer, the science
products (hamsci-physics) and the station's web pages (station-web, gmag-webui).
`base` gives radiod, hf-timestd and station-web; `client` decodes from radiods on
other machines (see "Decode-only host" below); `radiod-only` runs radiod (and gpsdo-monitor) for a
decoder host elsewhere on the LAN; `wd` replaces a WsprDaemon station (WSPR and FT8/FT4
spots from its own radiod, plus ka9q-web).
Bring-up installs a component whose hardware it cannot find on the USB bus (the
magnetometer, the GPSDO) **dormant**: installed and enabled in topology, but not started.
`smd adopt` starts it once the hardware appears.  A missing RX888 gives a warning, or
stops bring-up with `--require-hardware`.

A station installed from these repositories sends nothing upstream until you release
its uploads with `smd sink upload` (bring-up asks on a terminal).  Until then it keeps
FT8 spots 1 h and WSPR spots 24 h.

Bring-up also sets up remote access when it runs on bare metal.  It runs
`sigmond-rac activate`, which registers the machine with the gateway and
starts its tunnel.  A Proxmox appliance VM skips this step, because its host
carries the machine's remote access.  If registration fails, bring-up still
finishes.  A retry timer keeps trying after a failure that can pass (the gateway
unreachable); after one that cannot (no station name, an old tunnel still running),
bring-up prints the `sigmond-rac activate` command to run once it is fixed.

The machine's remote-access name puts the callsign and the hostname together
in upper case, as in `AC0G-SIGMA` or `AC0G-B2`.  A hostname that already starts
with `<CALL>-` stands alone.  Once `sigmond-rac activate` records the name,
the name sticks.  A hostname of `localhost`, or one equal to the callsign,
gives no default; pass `--site`.

`smd remote status` shows whether remote access is on, and `smd remote on` or
`smd remote off` switches it.  Type these as yourself, never with `sudo`.  The
switch changes system files, so `smd` asks for root through sudo on its own and
may ask for your password.

A machine that runs only radiod and ka9q-web never runs bring-up.  On such a
machine, run this once:

```bash
sudo /opt/git/sigmond/sigmond/scripts/rac/sigmond-rac activate --site AC0G-B1
```

The [design](superpowers/specs/2026-11-05-one-rac-design.md) explains the
channels, the ports and the exit codes.

Add `--dry-run` to see every step first.  Add `--non-interactive` to accept
radiod's defaults instead of answering its questions.

If the station uploads to PSWS, put each instrument's ID in
`/etc/sigmond/site-profile.toml` and render it into the client configs:

```toml
[psws.instruments]
"hf-timestd"   = "<GRAPE instrument id>"
"mag-recorder" = "<magnetometer instrument id>"
```

```bash
smd config render
```

#### Decode-only host

A decode-only host runs no radiod of its own.  It decodes from radiods on
other machines on the LAN.  Name each radiod it should use; the option may
repeat:

```bash
smd bringup client --callsign AC0G --grid EM38ww \
    --remote-radiod ac0g-b1-a-status.local --remote-radiod ac0g-b1-b-status.local
```

Bring-up browses the LAN for radiods and prints every one it finds, with
its host and advertised name.  An unattended install (`--non-interactive`,
or no terminal) binds only the radiods you name, and with none named it
stops after printing the list, so you can choose from it.  A named radiod
that does not answer stops bring-up before it installs anything.

On a terminal with no radiods named, bring-up proposes the ones whose
advertised name starts with your callsign, so a radiod that advertises
"AC0G @EM38ww B1-a longwire" belongs to AC0G.  The first word must equal
the callsign; a radiod that advertises a reporter ID such as "AC0G/S
RX-888 Mk2" never gets proposed.  Check the proposal before you accept it:
every radiod on your LAN that advertises your callsign gets proposed,
whichever machine it runs on.  Bring-up prints one line for each radiod
it would keep, with the radiod's host, its advertised name and the reason,
then asks before it binds them.

Each kept radiod gets one instance of each per-reporter recorder the
profile carries.  The `client` profile carries two: wspr-recorder (WSPR)
and psk-recorder (FT4/FT8).  It also installs hfdl-recorder and
codar-sounder, which run one instance per radiod named in their own config
files; `smd start` starts one for each.  Bring-up names the instances after the radiod:
`ac0g-b1-a-status.local` gives the reporter ID AC0G/B1A.  With a single
radiod, `--reporter` still sets the name.  With several, `--reporter`
counts as an error, because one name cannot cover two instances.

hf-timestd takes its timing from one radiod.  With one kept radiod it uses
that one.  With several, name it with `--timing-radiod <status name>`.
Without that, bring-up installs hf-timestd, leaves it idle and says so.

Bring-up records each radiod with `smd admin sources bind`, and you can run
it yourself:

```bash
smd admin sources bind ac0g-b1-a-status.local --reporter AC0G/B1A [--timing]
```

The verb writes the radiod into `/etc/sigmond/coordination.toml`.  For each
recorder enabled here (or each one named with `--clients`), it makes the
instance, selects the radiod as its source and points the instance's config
at it.  `--timing` binds hf-timestd to the same radiod.  Running it twice
changes nothing.

No verb renames an instance yet.  To give one a new name, bind its radiod
again under the new reporter ID, start the new instance, then retire the
old one:

```bash
smd admin sources bind ac0g-b1-a-status.local --reporter AC0G/LOOP \
    --clients psk-recorder
smd admin instance enable psk-recorder AC0G/LOOP
smd admin instance disable psk-recorder AC0G/B1A
smd admin instance remove psk-recorder AC0G/B1A
```

The old name's entry stays in coordination.toml until you delete it by hand.

#### Receivers

The `[[receiver]]` list in `/etc/sigmond/site-profile.toml` is the one record
of which radiod each reporter ID listens to.  Bring-up, `smd check` and
`smd admin instance list` all read it.

```toml
[[receiver]]
reporter_id = "AC0G/B1A"
radiod = "ac0g-b1-a-status.local"
injector = true
antenna = "20 m longwire"

[[receiver]]
reporter_id = "AC0G/B1B"
radiod = "ac0g-b1-b-status.local"
```

Read the list back, then apply it:

```bash
smd admin receiver list
smd admin receiver apply
```

`apply` binds every declared receiver.  It adds and updates.  It never
removes an instance, a sources file or recorded data.  It sets an instance's
upload flag only when the receiver decides it, through `upload =` or
`same_antenna_as`, and only once the client has a config.  Otherwise the flag
stays as `smd config upload` left it.

A receiver with no `clients` key runs one instance of each per-reporter
recorder (wspr-recorder, psk-recorder, meteor-scatter) that this host enables
or has installed.  `clients = ["psk-recorder"]` narrows it to the ones you
name.  `apply --clients a,b` sets that default set for one run; bring-up
passes its profile's recorders this way.

Two commands write the list for you.  `smd admin sources bind <status>
--reporter <ID>` records the receiver, then binds it.  To add one receiver
for one client, run:

```bash
smd admin instance add <client> <reporter> --radiod <status>
```

Several receivers may share one radiod.  Each receiver after the first on a
radiod must name `same_antenna_as`, the reporter ID of a receiver on that
radiod.  Such a receiver keeps its spots local unless you set
`upload = true`.  At most one receiver per radiod carries `injector = true`,
and at most one on the whole host, because hf-timestd binds to that
receiver's radiod.

With a list declared, bring-up plans from it.  It needs no `--remote-radiod`
or `--reporter`, because the list already names every radiod and reporter ID.
Bring-up accepts flags that match the list, so you can re-run the same
command after a failure.  It refuses flags that disagree with the list; edit
the list instead.

Several receivers put several instances of each recorder on one host.  Before
it installs anything, bring-up adds up their cost against the CPUs left over
after radiod.  When the host falls short, bring-up stops and says by how much.
Narrow a receiver's `clients = [...]`, or pass `--ignore-capacity`.  Under
`--dry-run` the shortfall prints as a warning and the plan still shows.

On a station that already runs instances, `smd config render --init` builds
the list from them.  It reads each instance's sources file and makes one
receiver per reporter ID.  It prints what it wrote, and it names every
instance it left out and why: an empty or unreadable sources file, a key that
is not a radiod, a client that takes no per-reporter instance, or a reporter
whose instances point at two radiods.  If the adopted
list fails validation, it writes nothing and says what to fix.  A profile
that already declares receivers stays as it is.  `--dry-run` prints the
result and writes nothing.

The steps below build the same station a piece at a time, for when you
want to choose each part yourself.

### 3. See what's available

```bash
smd component list --catalog
```

```
━━━ catalog — known clients ━━━

  Servers (3)
    ·  gmag-webui             HamSCI magnetometer real-time web dashboard (gmag_webui; browser reads mag-usb's WebSocket feed)
    ·  ka9q-radio             ka9q-radio SDR daemon — receives and multicasts RF channels
    ·  ka9q-web               Web interface for ka9q-radio (radiod status UI)

  Clients (11)
    ·  codar-sounder          Opportunistic ionospheric sounder using CODAR chirp transmissions  contract=0.8
    ·  hamsci-physics         Ionospheric science products (TEC/dTEC, TID, GRAPE/PSWS) from hf-timestd observations  contract=0.8
    ·  hf-tec                 HF PRN-coded beacon recorder for ionospheric specification  contract=0.8
    ·  hf-timestd             HF time-standard analyzer (WWV/WWVH/BPM/WWVB)  contract=0.8
    ·  hfdl-recorder          HFDL (High Frequency Data Link) recorder — one dumphfdl subprocess per band  contract=0.8
    ·  mag-recorder           RM3100 magnetometer recorder + PSWS uploader (non-radiod, §16 'other')  contract=0.8
    ·  meteor-scatter         Meteor-scatter ping recorder/decoder (jt9 --msk144)  contract=0.8
    ·  psk-recorder           FT4/FT8 spot recorder for PSKReporter  contract=0.8
    ·  station-web            Station status, documentation and data reports; timing and science pages where hf-timestd runs  contract=0.8
    ·  superdarn-sounder      Passive SuperDARN coherent-radar monitor (detection / identification)  contract=0.8
    ·  wspr-recorder          WSPR/FST4W audio recorder (period-aligned WAVs)  contract=0.8

  Infra (3)
    ·  gpsdo-monitor          Leo Bodnar GPSDO health monitor + mDNS advertiser
    ·  igmp-querier           Robust IGMP querier so radiod multicast survives on home-network switches
    ·  sigmond-rac            Remote Access Channel — frpc reverse tunnel to gw2.wsprdaemon.org for admin SSH/web behind NAT
```

A `✓` marks what this host has installed, `·` what it has not.

> **Setting up a new host end to end?** See the
> [greenfield install runbook](greenfield-runbook.md) for the
> full ordered walkthrough (identity → radiod → wisdom → tuning →
> clients → validate).

### 4. Install clients

Install whichever clients you need.  Each client's installer handles
everything: creates a service user, Python venv, config template, and
systemd units.

```bash
smd install hf-timestd
smd install psk-recorder
```

Run `smd` as yourself, never under `sudo`.  It asks for root itself when a
step needs it, and it refuses to run under `sudo`.

Use `--dry-run` to preview what would happen without making changes:

```bash
smd install wspr-recorder --dry-run
```

### 5. Configure

Each client owns its own config file.  After installation, edit the config
for your station:

| Client | Config file |
|--------|-------------|
| radiod | `/etc/radio/*.conf` |
| hf-timestd | `/etc/hf-timestd/timestd-config.toml` |
| psk-recorder | `/etc/psk-recorder/psk-recorder-config.toml` |
| wspr-recorder | `/etc/wspr-recorder/config.toml` |
| mag-recorder | `/etc/mag-recorder/mag-recorder-config.toml` |
| codar-sounder | `/etc/codar-sounder/codar-sounder-config.toml` |
| hfdl-recorder | `/etc/hfdl-recorder/hfdl-recorder-config.toml` |
| hf-tec | `/etc/hf-tec/hf-tec-config.toml` |
| meteor-scatter | `/etc/meteor-scatter/meteor-scatter-config.toml` |

Sigmond keeps three files of its own in `/etc/sigmond/`: `site-profile.toml`
(the site's identity: callsign, grid, receivers, PSWS ids), `coordination.toml`
(host identity, radiods, instances, which `smd config render` writes from the
site profile) and `topology.toml` (which components are enabled, and the CPU
policy).  Bring-up writes all three.  To edit topology by hand, start from the
example:

```bash
sudo cp /opt/git/sigmond/sigmond/etc/topology.example.toml /etc/sigmond/topology.toml
sudo vi /etc/sigmond/topology.toml
```

Enable or disable components by setting `enabled = true` or `false`.

#### Using the TUI (recommended)

Instead of editing config files manually, you can use the interactive TUI:

```bash
smd tui
```

The TUI provides:
- **Install screen** — browse and install components from the catalog
- **Components screen** — enable/disable components, with live validation
- **Overview screen** — system health dashboard
- **Logs screen** — view and filter service logs
- **CPU affinity screen** — configure CPU core isolation
- **CPU frequency screen** — monitor and control CPU frequencies
- **Environment screen** — discover and probe network peers (KIWISDRs, GPSDOs)
- **GPSDO screen** — monitor Leo Bodnar GPSDO health
- **Validate screen** — cross-client harmonization checks
- **Lifecycle screen** — start/stop/restart services
- **Apply screen** — reconcile services with current config
- **List (Software versions) screen** — per-component status (git ref,
  upstream divergence, version policy) with Update All / per-component
  update buttons; replaces the old separate Update screen
- **Backup/Restore screens** — backup and restore configuration

Keybindings: `o` = overview, `c` = CPU affinity, `r` = radiod, `a` = remote access,
`v` = validate, `b` = backup, `R` = restore, `K` = ka9q-watch, `q` = quit

### 6. Start services

```bash
smd start
```

This starts all enabled components in the topology.  To start a single
component (the component name is now a positional argument; an optional
second positional selects a single instance):

```bash
smd start psk-recorder
```

### 7. Check health

```bash
smd status
```

Shows systemd unit state for every managed service, plus inventory data
from each installed client: version, channel count, active frequencies,
and any issues.

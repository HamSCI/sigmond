# Installing a station on your own Linux machine

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond f3b0c6e on 2026-11-18
> **Canonical for:** installing sigmond on a machine you already run (no Proxmox)
> **The other way:** to turn a dedicated mini-PC into a station from the Proxmox USB image (Proxmox
> host plus a decoder VM), follow the appliance guide,
> [sigmond-proxmox INSTALL.md](https://github.com/HamSCI/sigmond-proxmox/blob/main/INSTALL.md).

This guide installs sigmond from its repositories onto a Debian machine you already run: no USB
image, no Proxmox.  You keep the machine and its operating system; sigmond adds a station to it.
The README's [Quick start](../README.md#quick-start) holds the same commands with more detail on
each; this page puts them in order and says what each one does to your machine.

## 1. Before you start

- **Debian 13** (Debian 12 and Ubuntu 22.04 or later also work), on x86-64, with internet access.
- **An account in the `sudo` group.**  You run every command as yourself; sigmond asks for root
  through sudo when it needs it.  Never type `sudo smd`: smd refuses it.
- **The hardware** on the machine's own USB: the RX888, the GPSDO, and the magnetometer if you
  have one.  You may install without them: bring-up warns about each missing device, installs its
  components dormant (installed, not started), and goes on.  `smd adopt` starts them once the
  device arrives (section 6).  Add `--require-hardware` to make bring-up stop instead.
  The [shopping list](hardware/shopping-list.md) names the models.
- **No public IPv4 address on the machine.**  Bring-up refuses a host it could expose to the
  internet.  If you mean it, `sudo touch /etc/sigmond/allow-public-ip` lifts the refusal.
- **A healthy package system.**  Bring-up stops if `/etc/default/grub` has a syntax error or dpkg
  has packages left half-configured, and prints the command that fixes each.

## 2. Install sigmond

```bash
sudo mkdir -p /opt/git/sigmond
sudo chown $USER /opt/git/sigmond
git clone https://github.com/HamSCI/sigmond /opt/git/sigmond/sigmond
cd /opt/git/sigmond/sigmond
./install.sh
```

`install.sh` creates a `sigmond` system user, adds you to its group, builds sigmond's venv and
puts `smd` on your PATH.  Open a new shell afterwards (or `newgrp sigmond`).

## 3. Bring up the station

```bash
smd bringup dasi2 --callsign AC0G --grid EM38ww --reporter AC0G/S \
                  --release-list /opt/git/sigmond/sigmond/release/components.toml \
                  --psws-station-id S000418 \
                  --psws-instrument hf-timestd=367 \
                  --psws-instrument mag-recorder=401
```

Use your own callsign, grid, reporter ID and PSWS ids (section 4 says where they come from).
`--release-list` makes bring-up install the commits of the release you checked out in section 2,
not the newest main of each component.
`--dry-run` shows every step first.
`--non-interactive` accepts radiod's defaults; fine-tune clients afterwards with
`smd config edit`.  The `dasi2` profile gives a local radiod and the core clients: hf-timestd,
wspr-recorder, psk-recorder, mag-recorder, gmag-webui, meteor-scatter, hamsci-physics,
hfdl-recorder, codar-sounder and station-web.  `--with-optional` adds hf-tec and superdarn-sounder.
The `client` profile decodes from radiods on other machines (section 7).

What bring-up does to your machine:

- **It asks for your sudo password** when it reaches the steps that need root.
- **It can take up to an hour the first time.**  radiod plans its FFTs for your CPU before it
  starts, and bring-up waits for that plan.
- **It places radiod on performance cores**, away from system interrupts, and on a hybrid Intel
  CPU it keeps radiod off the efficiency cores.  If no performance core is free it stops and says
  why.  To choose the cores yourself, set `radiod_cpus` under `[cpu_affinity]` in
  `/etc/sigmond/topology.toml`.
- **It leaves your kernel command line alone.**  It prints the `isolcpus` line that would keep
  other work off radiod's cores, and changes nothing.  To let sigmond write it, set
  `write_kernel_isolation = true` under `[cpu_affinity]` in `/etc/sigmond/topology.toml`, run
  bring-up again, and reboot.
- **It sets up remote access** (`sigmond-rac activate`), so the machine can be reached for
  support.  `smd remote status` shows it; `smd remote off` turns it off.  See the README's
  [remote access notes](../README.md#2-bring-up-the-station).

## 4. Tell PSWS who you are

The PSWS portal gives your site one **station ID** (`S000418`) and gives each product you send
its own **instrument ID** under that station: one for the RX888 (GRAPE, from hf-timestd) and one
for the magnetometer (mag-recorder).  Sigmond expects that arrangement first.

If you gave the ids to bring-up (section 3), you have nothing more to do here.  Bring-up wrote
them to `/etc/sigmond/site-profile.toml`, created that file if your machine had none, and copied
them into each recorder's own config.

To set them later, or to change them:

```bash
smd config psws --station S000418 \
                --instrument hf-timestd=367 --instrument mag-recorder=401
```

That command writes the `[psws]` block of `site-profile.toml` and runs `smd config render`, which
carries the ids into hf-timestd, hamsci-physics and mag-recorder.  It keeps any id you do not
name, so `--instrument mag-recorder=401` alone adds the magnetometer and leaves the rest.  Add
`--dry-run` to see the block before it lands.

If the portal enrolled an instrument under a station of its own, name that station for that
instrument alone:

```bash
smd config psws --instrument mag-recorder=84 --instrument-station mag-recorder=S000082
```

Then register the machine's upload key with the portal: `smd psws enroll` prints it, and
`smd psws verify` proves the portal accepted it
([registration guide, section 5](operator/registration.md#5-psws--the-one-portal-step)).

## 5. Release uploads

**A first install from the repositories holds every upload** until you release it, so nothing
leaves the machine before you have looked at it.  Only the station heartbeat goes out.  Clients
keep FT8 spots for one hour and WSPR spots for 24 hours, so release within that time or the older
spots never ship.  On a terminal, bring-up asks at the end; otherwise:

```bash
smd sink upload
```

Today's and yesterday's (UTC) GRAPE and magnetometer packages ship; older ones made while
uploads were held get set aside, outside the spools, rather than sent.  `smd sink upload` refuses
while GRAPE or the magnetometer is packaging a day's data, and says so; run it again a few minutes
later.

Every WSPR, FT4/FT8 and MSK144 spot goes to wsprdaemon.org alone.  It forwards them to
wsprnet.org and pskreporter.info; your station posts to neither.  GRAPE and magnetometer
packages go to PSWS.

## 6. Check the station

```bash
smd status        # every service, with each client's version, channels and issues
smd check         # the station's own checks
```

If you installed without some hardware, `smd status` lists the device under "adoptable:" once
you plug it in.  Then run `smd adopt <name>` with the name `smd status` printed; it shows its plan
and asks first.  `smd adopt` starts the device's components; nothing else does.

## 7. Use a radiod on another machine

A station can decode from any radiod on its LAN.  The radiod may run on another sigmond station,
or on a plain Linux box where someone installed ka9q-radio from apt and never installed sigmond.
If it behaves as radiod should, it serves as a data source like any other.  Bring up a
decode-only station with `smd bringup client --remote-radiod <status name>`, or bind a radiod to
an existing station:

```bash
smd admin sources bind ac0g-b1-a-status.local --reporter AC0G/B1A [--timing]
```

The README's [Decode-only host](../README.md#decode-only-host) section covers the choices.

## 8. Keep the station current

```bash
smd align            # what would change to reach the latest blessed release; changes nothing
smd align --apply    # move to it, rebuild what needs it, restart what is stale
```

`smd align` moves sigmond's components to the latest blessed release ("sigmond vN"), the same
release the Proxmox USB image stations run.  On your machine it reports `host: none — a repo install on this machine; no
Proxmox host to align`; that line is expected.  It may also offer to install
`sigmond-location-check`, a helper script the image carries; take it.  `sigmond-site-timing`
lives in sigmond itself: `/usr/local/sbin/sigmond-site-timing` links to
`/opt/git/sigmond/sigmond/bin/sigmond-site-timing`, so each update of sigmond carries it.  A plain
copy left there by hand is kept once as `sigmond-site-timing.pre-sigmond-link`.

`sigmond-site-timing` wires a LAN stratum-1 NTP server or a LAN GNSS stream (port 9000) only after
you accept it.  When it finds one you have not accepted, it writes nothing and logs an offer with
the command that accepts it (`journalctl -t sigmond-site-timing`):

```bash
sudo sigmond-site-timing --trust-ntp 192.168.8.144   # record the server, then wire it
sudo sigmond-site-timing --trust-gnss 192.168.8.50   # the same for the GNSS stream
sigmond-site-timing --show-trust                     # what is accepted (/etc/sigmond/lan-trust.toml)
```

Servers wired before this record existed stay as they are.  A component you run ahead of the release stays where it is unless you add
`--allow-rollback`.

Debian itself stays yours.  Update it as you would any machine you run; sigmond pins none of its
packages.  After a kernel update, reboot as usual; radiod and the clients start on their own.

## 9. Where next

- Disk space: `smd storage status` shows each store's fill and what each client holds.  Sigmond's
  storage ledger (`sigmond-storage.timer`) asks clients to trim when a store fills.  To keep raw IQ
  longer, attach a USB archive drive with `smd archive add` (it erases the drive); hf-timestd's
  archiver then copies raw IQ to it.  `smd archive status` shows the result.
- Day-to-day commands: the README's [Command reference](../README.md#command-reference).
- Something wrong: `smd admin diag`, and the README's [Debugging](../README.md#debugging) section.
- Adding a client later: the README's
  [Adding a discretionary client later](../README.md#adding-a-discretionary-client-later).

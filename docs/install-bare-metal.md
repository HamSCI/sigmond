# Installing a station on your own Linux machine

> **Audience:** operator
> **Status:** current, 2026-11-08 (sigmond fc1320d)
> **The other way:** to turn a dedicated mini-PC into a station from the USB image (Proxmox host
> plus a decoder VM), follow the appliance guide,
> [sigmond-appliance INSTALL.md](https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md).

This guide installs sigmond from its repositories onto a Debian machine you already run: no USB
image, no Proxmox.  You keep the machine and its operating system; sigmond adds a station to it.
The README's [Quick start](../README.md#quick-start) holds the same commands with more detail on
each; this page puts them in order and says what each one does to your machine.

## 1. Before you start

- **Debian 13** (Debian 12 and Ubuntu 22.04 or later also work), on x86-64, with internet access.
- **An account in the `sudo` group.**  You run every command as yourself; sigmond asks for root
  through sudo when it needs it.  Never type `sudo smd`: smd refuses it.
- **The hardware** on the machine's own USB: the RX888, the GPSDO, and the magnetometer if you
  have one.  Bring-up warns about, and asks before it goes on without, an RX888 it cannot find.
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
                  --psws-station-id <S0000NN>
```

Use your own callsign, grid, reporter ID and PSWS station ID.  `--dry-run` shows every step first.
`--non-interactive` accepts radiod's defaults; fine-tune clients afterwards with
`smd config edit`.  `dasi2` gives a local radiod, hf-timestd, WSPR, FT4/FT8, meteor scatter and
the magnetometer; `client` decodes from radiods on other machines (section 7).

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

If the station uploads to PSWS, put each instrument's ID in `/etc/sigmond/site-profile.toml` and
render the client configs:

```toml
[psws.instruments]
"hf-timestd"   = "<GRAPE instrument id>"
"mag-recorder" = "<magnetometer instrument id>"
```

```bash
smd config render
```

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

## 6. Check the station

```bash
smd status        # every service, with each client's version, channels and issues
smd check         # the station's own checks
```

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

`smd align` moves sigmond's components to the latest blessed release, the same release the USB
image stations run.  On your machine it reports `host: none — a repo install on this machine; no
Proxmox host to align`; that line is expected.  It may also offer to install
`sigmond-site-timing` and `sigmond-location-check`, two helper scripts the image carries; take
them.  A component you run ahead of the release stays where it is unless you add
`--allow-rollback`.

Debian itself stays yours.  Update it as you would any machine you run; sigmond pins none of its
packages.  After a kernel update, reboot as usual; radiod and the clients start on their own.

## 9. Where next

- Day-to-day commands: the README's [Command reference](../README.md#command-reference).
- Something wrong: `smd admin diag`, and the README's [Debugging](../README.md#debugging) section.
- Adding a client later: the README's
  [Adding a discretionary client later](../README.md#adding-a-discretionary-client-later).

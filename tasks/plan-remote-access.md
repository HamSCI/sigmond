# Plan — remote access that every station sets up, and its operator can switch

**Authors:** Michael Hauan (AC0G) + Claude
**Date:** 2026-10-09
**Status:** design, for review. No code yet. Target release: v3.71.
**Verified against:** sigmond 1057d73 on 2026-10-09 — `scripts/proxmox/sigmond-wizard.sh`, `docs/operator/remote-access.md`, `scripts/proxmox/pm-heartbeat.py`; sigmond-appliance a0d7268 `firstboot-v3.sh`; the host unit read on AC0G-B4's Proxmox host

## Goal

Every station registers for remote access when it installs, and its tunnel comes up and stays up by default.
The operator can turn the tunnel off, and on again, from the decoder VM, with one command. The station and the
fleet both show which state holds.

## Why

A tunnel gives the only effective means of supporting a site. Michael decided on 2026-10-08 and 2026-10-09:

- Registration belongs to every install. The wizard offers no way to skip it. It is the price of the software
  and of any support.
- The tunnel starts on, for every station class.
- The operator holds the switch. Some owners will not grant standing access, yet will open the door when
  trouble comes.
- The operator works the switch from the VM, and the basic instructions say how.

## What exists today (do not rebuild it)

- **The tunnel runs on the Proxmox host**, not in the VM: `sigmond-rac-host.service` runs `frpc` against
  `/etc/sigmond/frpc-host.toml`, and four socket units (`sigmond-vm-{ssh,web,station,gmag}-relay.socket`) carry
  the VM's channels. One tunnel, six channels: VM ssh, ka9q-web, station-web, gmag, host ssh, the Proxmox UI.
- **The switch exists, on the host:** `sigmond-setup --rac-off` stops and disables those units and keeps the
  configuration; `--rac-on` enables them again with no new registration. Nothing else turns the tunnel on.
- **The wizard asks** "Enable remote access? [Y/n]". On Yes it posts the station's name and the host's public
  key to the gateway's registrar, receives an account, a token, a RAC number and ports, writes
  `frpc-host.toml`, starts the tunnel and waits for every channel. On No it skips all of that; enabling later
  means `sigmond-setup --reconfigure`, which asks the whole wizard again.
- **`smd admin rac`** in the VM manages a different tunnel (`wd-rac.service`), for stations without a Proxmox
  host. On an appliance station it reports "not configured", correctly.
- **The VM cannot ask the host for anything.** The host reaches the VM (guest agent, and ssh as `sigmond` with
  a key the wizard installs). The other direction exists only as the split console: a person types the host's
  root password on the VM's keyboard.
- **The host has no keyboard after install.** An operator who wants the switch today needs the host's address
  and root password, at the moment of trouble.

## Non-goals

- A second tunnel, or moving the tunnel into the VM.
- Changing what the tunnel carries, or who the gateway admits.
- Letting any party outside the station turn the tunnel on. Not the gateway, not the heartbeat server.
- A web button. The station's web pages carry no login; a switch for remote access must not sit behind none.

## Design

### 1. One switch script on the host

`/usr/local/sbin/sigmond-rac-switch` becomes the only code that turns the tunnel on or off. It takes exactly
three words: `on`, `off`, `status`.

- `on`: record the wish, register first if the station never registered (section 3), enable and start the
  relays and the tunnel, wait for the channels, print the state.
- `off`: record the wish, stop and disable the tunnel and the relays. Keep the registration, the key and the
  ports, so a later `on` needs nothing from the gateway's registrar.
- `status`: print the wish, whether the tunnel runs, how many channels the gateway accepted of how many the
  file declares, the RAC number and gateway, and who changed the switch last and when.

The wish lives in one file, `/etc/sigmond-appliance/rac-desired` (`on` or `off`). The units' enabled state
cannot carry it alone: a station that has not registered yet has nothing to enable, and its wish still reads
`on`.

`sigmond-setup --rac-on` and `--rac-off` call this script. The console panel refreshes after every change.

### 2. The VM asks; the host alone acts

The wizard pairs the two machines at every install:

1. It creates a key pair inside the VM, through the guest agent: `/etc/sigmond/rac-switch.key`, root-only.
2. It creates a host account that can do one thing. The account's `authorized_keys` line carries
   `restrict,command="sudo /usr/local/sbin/sigmond-rac-switch"`, and one sudoers line allows that account
   exactly that script. The script reads the requested word from `SSH_ORIGINAL_COMMAND` and refuses anything
   but the three words.
3. It writes the host's public key into the VM as the only host that key may talk to.

In the VM the operator types:

    smd remote off        turn remote access off; it stays off across reboots
    smd remote on         turn it on again
    smd remote status     show the state

`smd remote` elevates the way every changing `smd` verb does, opens ssh to the host on the private link
(10.99.0.1) with that key, and prints the host's answer.

Why this shape:

- **It adds no listener.** The host already accepts ssh from the VM on that link, for the split console.
- **The key buys one script.** No shell, no forwarding, no other command.
- **The VM can never pair itself.** If it could grant itself a path to the host, the host's authority would
  mean nothing. A station installed before v3.71 pairs with one command at the host console,
  `sigmond-setup --pair-vm`, or when `pm-align` brings the host to v3.71.
- **The pairing can be re-asserted.** On AC0G-B4 an identity restore replaced the host's key eight minutes
  after the wizard had authorized the old one, and the fleet's login broke for a week. So the host re-checks
  both halves at every boot and after any identity restore, over the guest agent: its own key in the VM, the
  VM's key in its account.
- **It does not ride the guest agent.** The agent can stop answering (see the v3.71 item on non-ASCII
  requests), and a switch must work on the day something else has failed.

The name: `smd remote`, not `smd rac`. `smd admin rac` already names the VM-side tunnel, and two "rac"
commands that manage different tunnels would mislead. On a station with no Proxmox host, `smd remote` can
drive that VM-side tunnel, so the operator learns one command wherever the tunnel lives.

What a break-in gains: someone who holds root in the VM can read the key and work the switch. They already
hold the VM. Turning the tunnel on opens the station only to the gateway's users, who still need an ssh key
the station accepts.

### 3. Registration runs alone, and keeps trying

Today registration sits inside the wizard. It moves into a step the host can run by itself,
`sigmond-rac-switch` calls it, and a timer retries it:

- The wizard no longer asks. It says what registration sends (the station's name and the host's public key),
  registers, brings the tunnel up, and names the command that turns it off.
- An install with no internet, or with the gateway down, still finishes. The wish reads `on`; a timer retries
  registration until it succeeds and then starts the tunnel. An operator who has turned the switch off in the
  meantime stays off: the timer registers and stops there.
- The registrar answers over plain HTTP today, and its reply carries the station's token. Move the request to
  HTTPS against the certificate authority the image already pins (`/etc/sigmond/frps-ca.crt`). Check first
  what the token alone allows.

### 4. The station and the fleet show the state

- **Console panel.** Today it prints "OFFLINE — check: journalctl -u sigmond-rac-host" whenever the unit does
  not run, so a deliberate off reads as a fault. Three states instead: on, with its channels; off by the
  operator, since when, with the command that turns it on; and wanted but not up, which stays the fault.
- **Login banner in the VM.** One line with the state and the command.
- **Heartbeat.** The host's heartbeat gains a `rac` block: the wish, whether the tunnel runs, channels up of
  declared, since when. The fleet board then shows "off by the operator" as a decision, never as a station
  to chase. A schema change on the station and on wd30.

### 5. The instructions

- `docs/operator/remote-access.md`: sections 3 and 4 rewritten around `smd remote`; the "ignore `smd admin
  rac`" section shrinks to a sentence.
- sigmond-appliance `INSTALL.md` and `docs/install-page.html`: the wizard step loses its question and gains
  three sentences: remote access comes up by itself, what it sends, and how to switch it from the VM.
- The quick-start card that rides the stick: one line.

## Open questions for Michael

1. **A time limit on `on`.** `smd remote on 24h` would let an owner open the door for one consultation and
   have it close by itself. The default `on` would carry no limit. Worth building now, or later?
2. **The name.** `smd remote`, as argued above, or another word.
3. **Stations already in the field** whose owner said No at install: do they register at their next
   `pm-align`, like a new install, or only when the owner asks?

## Phases

Each phase ships alone and leaves the station working.

1. **The switch script and the wish file.** `--rac-on` and `--rac-off` delegate to it. The panel shows the
   three states. Host only. Test: the script's parser, hermetic; on the rig, off, reboot, still off, on.
2. **Registration alone, with its retry timer.** The wizard registers without asking. Test: the rig needs a
   registrar and a tunnel server to stand in for the gateway; without them the nest can prove only "wish on,
   not registered, timer armed".
3. **The pairing and `smd remote`.** The wizard pairs; `sigmond-setup --pair-vm` and the boot-time re-check.
   Test: on the rig, from the VM: status, off, the host's units stop; a second key, or a fourth word, gets
   refused.
4. **The heartbeat block and the fleet board.**
5. **The instructions**, checked against a real install.
6. **The time limit**, if question 1 says yes.

A reflash of AC0G-ND with the v3.71 image proves phases 1 to 3 and 5 on hardware. AC0G-B4 and AI6VN receive
the host's half through `pm-align` and pair with `sigmond-setup --pair-vm`.

## Found on the way, not part of this plan

- `ops/bin/site-restore.sh b4` carries the repair for the very key whose loss stopped it reaching B4. A repair
  that travels over the broken path needs a second way in.

# Plan — remote access that every station sets up, and its operator can switch

**Authors:** Michael Hauan (AC0G) + Claude
**Date:** 2026-10-09
**Status:** design agreed with Michael on 2026-10-09. No code yet. Target release: v3.71.
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

## Decided on review (Michael, 2026-10-09)

- **No time limit.** The tunnel starts on, and the operator turns it off and on as desired. `on` carries no
  timer for now.
- **The command reads `smd remote`.**
- **The wizard loses its remote-access question and option altogether.** The installation documentation tells
  the operator, plainly, how to turn remote access off and on.

## One question still open

Stations already in the field whose owner said No at install. This plan leaves them as they stand: such a
station registers and comes up the first time its owner types `smd remote on`, and not before. A new install
never reaches that state, because its wizard no longer asks.

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

No phase builds a time limit; add one later if owners ask for it.

A reflash of AC0G-ND with the v3.71 image proves phases 1 to 3 and 5 on hardware. AC0G-B4 and AI6VN receive
the host's half through `pm-align` and pair with `sigmond-setup --pair-vm`.

## v3.71 hardware proof

Michael reflashed AC0G-B4 with `sigmond-appliance-v3.71-20261011-release.img` (appliance d243d81, sigmond
d504c56, sha256 3000f44c…5947320) on 2026-10-13 at about 15:30Z and installed it by `INSTALL.md`. The run
followed plan Task 12 Step 8. All times UTC.

The install met one fault outside this plan. The RX888 came up with error -71 and no SuperSpeed link. A
power cycle of its hub port did not clear it; a replug at the card did, at 16:22Z. The bring-up then re-ran
by itself and finished at 16:30:27Z.

1. **The wizard asked nothing about remote access.** Michael confirmed that it asked no remote-access
   question. He did not read back the printed `smd remote` commands or the text about what registering
   sends; that part stays unconfirmed by eye.
2. **The closing summary read `RAC: on` with 6 of 6 channels.** On the host, `pm-align --status` printed the
   `pairing (/etc/sigmond-appliance/rac-pairing): paired <UTC>` line.
3. **After a power cycle the panel still read on, 6 of 6** (Michael).
4. **A login to the VM printed the banner's remote-access line.**
5. **`smd remote status` answered `Remote access: on`, 6 of 6 channels, RAC 502 on gw2.wsprdaemon.org.**
6. **Off from B4's own network, 13 October 17:38:46Z.** `smd remote off` in the VM answered off. The host's
   `sigmond-rac-host` stopped. gw2 listed all six of B4's channels offline, and a connection to the VM's port
   through gw2 met a refusal. The host's heartbeat carried a valid `rac` block, "off by the operator since
   2026-10-13T17:38:46Z". The fleet board's `b4-pm` row first stayed silent, for two reasons older than this
   plan: wd30 had never authorized the B4 host's heartbeat key, and `ops/fleet.toml` lacked
   `heartbeat_station = "AC0G/B4-pm"` for that host. With both fixed (ops a4071a7 and the board's roster
   deploy at 18:49:57Z), the row read `pm, remote access off by the operator`, VALID, with `off` in its `rac`
   column. Michael did not read the panel's exact words at this step; at check 7 it read `RAC OFF`.
7. **Power cycle while off, 14 October.** On again from the VM at 01:31:13Z (6 of 6, a login through gw2
   worked), then off through the tunnel for check 9, then Michael power-cycled the whole station. The host came
   up at 01:36:52Z and the VM at 01:37:23Z. Everything still read off: the panel (`RAC OFF`), `smd remote
   status` ("The operator holds it off", still dated 01:31:53Z), the host's `sigmond-rac-host` inactive with
   no journal line since boot, and a connection through gw2 refused.
8. **On at the station console, 01:41:01Z.** The panel read `RAC restored`. `smd remote status` answered on,
   6 of 6 channels; `sigmond-rac-host` ran; a login through gw2 reached `AC0G-B4`.
9. **Off through the tunnel itself, 01:31:48Z.** From the devbox, through wd30 and gw2 port 36302, as
   `hamsci` in the VM. The off took effect at 01:31:53Z and the tunnel closed the session that carried it
   (`Connection to 10.112.0.2 closed by remote host`). Read over the LAN five seconds later: off, "The
   operator holds it off", `sigmond-rac-host` inactive, and a new connection through gw2 refused.
10. **The pairing re-asserted itself after the identity restore.** The restore replaced the host's sshd key
   at 16:58:44Z. The pairing re-asserted itself at 16:58:51Z, seven seconds later, with nobody typing a
   command. Nobody polled `smd remote status` inside those seven seconds, so a failure that short would have
   gone unseen; it answered on right after. B4 needed neither `systemctl restart ssh` nor
   `sigmond-setup --pair-vm`.
11. **Journals with the uplink pulled, 14 October.** Michael unplugged the host's Ethernet at about
   01:54:45Z (link down 01:54:09Z by the kernel's clock) and plugged it back in at 02:12:10Z, an outage of
   eighteen minutes.
   - `sigmond-rac-register` wrote no line at all: the registration stayed valid, so it had nothing to retry.
   - The tunnel's own program, `frpc` under `sigmond-rac-host`, wrote two lines per attempt — "try to connect
     to server" and "connect to server error: dial tcp: lookup gw2.wsprdaemon.org: i/o timeout" — once every
     20 to 30 seconds: 114 lines in the eighteen minutes, about 380 an hour.
   - At 02:00:02Z `sigmond-netfix` judged 192.168.1.244 stale on a port with no carrier and removed it from
     `vmbr0`, then found no live port and stopped. When the cable came back at 02:12:10Z nothing restored the
     address. netwatch's next pass ran netfix at 02:16:03Z, which retried DHCP on `vmbr0` and got
     192.168.1.244 back. `frpc` logged in three seconds later, at 02:16:06Z, and all six channels started. The
     station stayed unreachable for four minutes after the cable returned.
   - The hour with the gateway reachable, 02:16:07Z to 03:16:07Z: `sigmond-rac-host` wrote 46 lines, every
     one a request to frpc's local status API and its answer, 23 polls about two and a half minutes apart.
     It logged no error and no reconnect, and the unit never restarted. `sigmond-rac-register` and
     `sigmond-rac-pair` wrote nothing. At the end `smd remote status` still read on, 6 of 6 channels.

Filed for v3.72 from this run: `frpc` logs every retry, so a long outage fills the journal; netfix removes
the address on a lost carrier but does not restore it when the carrier returns, which leaves the host
dark until netwatch's next pass; the hub power cycle cannot clear an RX888 that needs a replug.

## Found on the way, not part of this plan

- `ops/bin/site-restore.sh b4` carries the repair for the very key whose loss stopped it reaching B4. A repair
  that travels over the broken path needs a second way in.

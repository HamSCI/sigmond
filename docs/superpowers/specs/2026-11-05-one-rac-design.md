# One remote-access implementation, on a Proxmox host or on bare metal

> **Audience:** contributor
> **Status:** design; Michael's questions answered 2026-11-05; Rob's (§8, 1-3) open; no plan yet
> **Written:** 2026-11-05, at Michael's direction: "however the method works now should be shared
> and available for use when installing sigmond in a Proxmox setting or bare metal, so it's one
> code base."
> **Builds on:** [tasks/plan-remote-access.md](../../../tasks/plan-remote-access.md) (v3.71),
> [tasks/plan-rac-dual-gateway.md](../../../tasks/plan-rac-dual-gateway.md),
> [2026-11-04-bare-metal-install-design.md](2026-11-04-bare-metal-install-design.md)

---

## 1. Why

sigma, a station installed from the repos on bare metal, never appeared on the wsprdaemon RAC.
Nothing in a repo install brings the tunnel up.  A Proxmox install does it unasked: the wizard on
the host registers with a gateway, takes the RAC number the registrar assigns, and starts one frpc
that carries six channels.

Looking for why the two differ turned up a larger fact.  sigmond carries two remote-access
implementations that share no code:

| | Proxmox wizard (host, bash) | `smd admin rac` (station, Python) |
|---|---|---|
| Registration | HTTP `POST /register` to the gateway's registrar; DASI units claim 220+n directly | an FTP upload of a key bundle to gw2 |
| Credentials | a user and token per station, from the registrar | one fleet-wide token written into `bin/smd` |
| Ports | bands by RAC number: 35800, 45800, 48800, 49800, 50800, 55800 + n | hashed from the station name: 10000s ssh, 50000s web |
| Channels | six; the VM's four through relays | two, ssh and web |
| On/off | `sigmond-rac-switch`, a stored wish, a retry timer, `smd remote` | `systemctl` on `wd-rac@<gw>` |
| Files | `sigmond-rac-host@<gw>`, `frpc-host-<gw>.toml` | `wd-rac@<gw>`, `frpc-<gw>.toml` |

The same station gets different numbers and ports from each.  The gateway table, the CA and
serverName rules and the admin ports exist twice, in two languages
(`tests/test_rac_dual_gateway.py` pins both copies).  A bare-metal install can reach only the
second, older model, which needs an operator to bring a number from the admin.

## 2. Goals, non-goals, anti-goals

**Goals**

- G1.  One implementation of registration, the gateway ladder, the frpc configuration, the switch
  and the retry timer, kept in one place in the sigmond repo.
- G2.  A Proxmox host and a bare-metal station both run it.  Each registers by itself during
  install, asking nothing, and comes up on the same gateways with the same kind of RAC number and
  the same port bands.
- G3.  The operator's switch reads the same on both: `smd remote on|off|status`.
- G4.  Existing Proxmox stations keep their RAC numbers, ports and pairing through the change.

**Non-goals**

- Changing the gateways, the registrar's protocol or the port bands.  Those belong to Rob's
  wsprdaemon-server and gateway work.
- RAC on a radiod-only host as a separate case.  It is a bare-metal or Proxmox station like any
  other.

**Anti-goals**

- A rewrite that throws away the tested bash.  Seven test files exercise the wizard's RAC
  functions and switch; a straight move keeps them passing.
- Two implementations surviving "for a transition" with no end date.

## 3. The decision: the wizard's model is the one

The wizard's model is newer (v3.71 and the dual-gateway work), runs every Proxmox station in the
fleet, gives each station its own credentials instead of a token in the source, and carries the
operator's wish across reboots.  It wins.  `smd admin rac` and `wd-rac` retire (§6).

## 4. Shape

**Where the code lives.**  The wizard's RAC functions (`build_rac_tiers`, `rac_ladder`,
`rac_register`, `rac_write_config`, `rac_tls_block`, `rac_gateway_set` and their helpers) move out
of `scripts/proxmox/sigmond-wizard.sh` into `scripts/rac/rac-lib.sh`.  The switch, today a 480-line
heredoc inside the wizard, becomes the file `scripts/rac/sigmond-rac-switch`.  The register and
retry entry points become `scripts/rac/sigmond-rac` (verbs `register`, `retry`, `activate`).  The
wizard sources `rac-lib.sh` from its own checkout or payload and installs the switch and the entry
point to `/usr/local/sbin`, as it installs the heredocs today.  The tests change only their source
path.  *(My call: bash stays bash; the code runs on a Proxmox host with no Python venv, and a
straight move is the cheapest proof of no regression.)*

**One mode switch.**  `RAC_MODE=host|station`, set by whoever installs it:

| | host (Proxmox) | station (bare metal) |
|---|---|---|
| Channels | vm-ssh, vm-web, vm-station, vm-gmag, host-ssh, host-ui | ssh, web, station, gmag |
| Local targets | relays 12222-12225 to the VM; host :22, :8006 | :22, :8081, :8000, :8082 directly |
| Remote ports | 35800/45800/48800/49800/50800/55800 + n | 35800/45800/48800/49800 + n (the same bands; host-ssh and host-ui unused) |
| Preconditions | relays installed, guest agent | none beyond frpc and the CAs |
| Pairing | `--pair-vm` (ssh key, sudoers, guest agent) | none |
| Site name | the machine's name (§4.1) | the machine's name (§4.1) |

A bare-metal station registers exactly as a host does: the same tier ladder, the same registrar
call, the same reply.  The registrar already returns `ports.vm_ssh` and `ports.vm_web`; a station
uses those, and derives station and gmag from the bands, as the host does today.

### 4.1 A RAC names a machine

A RAC gives an operator a way into a networked machine: the place where a radio runs, or where
its signals get processed, or both.  A machine with three RX888s and a machine with none each get
one RAC.  On a Proxmox host the RAC reaches the host, and the host reaches its VM and everything
the VM does.  (Michael, 2026-11-05.)

So the RAC registers under the machine's name, not a reporter ID.  A reporter ID names a stream
of spots and can change as the operator adds sources (switchboard design §3); the machine stays
put.  The wizard today registers a Proxmox host under its reporter ID (`rac_site_name`); that
ends for new installs.

- **The name.**  The operator's callsign joined to the machine's own name with a hyphen, in
  upper case: `AC0G-SIGMA`, `AC0G-B2`, as `ops/fleet.toml` already names machines.  Install
  offers that default and the operator may type another.  *(Michael approved, 2026-11-05.)*
- **The name sticks.**  Once a registration succeeds, the recorded name (`rac-site`) wins over
  anything derived later, so renaming a reporter never moves the RAC to a new number.  Today's
  `rac_site_name` reads `$REPORTER` first; the order flips.
- **Existing hosts keep their names.**  A Proxmox host already registered under a reporter ID
  (say `AC0G_B4`) keeps that name and its number.  Renaming one stays a deliberate act by its
  operator, never a side effect of an update.

**Installing it.**  The frpc binary and the per-gateway CAs come from one place, the sigmond-rac
payload (`install-host.sh` today), on both kinds of machine; `smd`'s GitHub download of frpc
retires.  On bare metal, bring-up runs `sigmond-rac activate` at the end of a COMPLETE run, the
point where the wizard runs `rac_activate`.  It asks nothing.  A failure never fails bring-up; the
retry timer carries on, as on a host.

**The switch.**  On a host the VM reaches the switch over the paired ssh key, as today.  On bare
metal `smd remote` finds no pairing but a local `/usr/local/sbin/sigmond-rac-switch`, and runs it
through `sudo`.  The output and exit codes stay the switch's own, so `smd remote` parses one
format on both.

## 5. What stays the same

- The gateways, admin ports 7500/7501/7503, CAs and serverName rules — now in one file.
- Sticky numbers: the registrar keys on site and key.  A Proxmox host that re-runs the new code
  sends the same site and key and gets its own number back.
- The wish (`/etc/sigmond-appliance/rac-desired`), the state file and the switch's exit codes.

## 6. Retiring `smd admin rac` and `wd-rac`

`smd admin rac install|register|start|stop` and the `wd-rac@` units stop being installed by
anything once the station mode exists.  A station that runs `wd-rac` today keeps its tunnel until
someone moves it.  Moving one means: stop `wd-rac@*`, run `sigmond-rac activate`, and accept a new
RAC number and new ports, since the hash-derived ports have no equivalent in the bands.  Which
stations run `wd-rac` now, and whether anyone depends on a hash-derived port, is Rob's to say
(§8).  The FTP registration and the fleet token in `bin/smd` go with it.

`lib/sigmond/rac_config.py` and the TUI RAC screen assume a third model (an admin hands the
operator a user, token and ports) that neither live path uses.  They go too, or the screen learns
to show the switch's status.

## 7. Order of work

1. Move the wizard's RAC functions and switch into `scripts/rac/` unchanged; the wizard sources
   and installs them; every existing RAC test passes from the new paths.
2. Add `RAC_MODE=station`: the four-channel config, no relays, no pairing.  Tests for both modes
   from the same fixtures.
3. Bring-up on bare metal installs the payload and runs `sigmond-rac activate`; `smd remote` falls
   back to the local switch.
4. Prove it: sigma registers as `AC0G-SIGMA` and B2 as `AC0G-B2`, each takes a number, and both
   are reachable on their four channels (Michael, 2026-11-05: B2 needs a RAC too); one Proxmox
   station re-runs the new code and keeps its number and ports.
5. Retire `smd admin rac`, `wd-rac`, the GitHub frpc download, `rac_config.py`, after Rob's answer
   on §8.

## 8. Open questions

**For Rob**

1. Which stations run `wd-rac` (`smd admin rac`) today, and does anyone rely on their
   hash-derived ports?
2. Does the registrar accept a bare-metal station as it is, or does it need to know there is no
   host (it returns host-ssh and host-ui ports that would go unused)?
3. Is this work you would rather do yourself, given your RAC commits this week?

**For Michael**

4. *Answered 2026-11-05:* on bare metal the machine's own ssh sits in the vm-ssh band (35800+n),
   where operators already look for a station's login.
5. *Answered 2026-11-05:* a RAC names the machine, not a reporter ID, and the default name takes
   the form `<CALL>-<MACHINE>` (§4.1).

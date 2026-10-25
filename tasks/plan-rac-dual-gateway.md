# Plan — two gateways, two tunnels: redundant RAC for every HamSCI station

**Authors:** Rob Robinett (AI6VN) + Claude
**Date:** 2026-10-24
**Status:** approved by rob 2026-10-24 ("take on the whole dual-tunnel job"). Implementation in progress.
  Target release: v3.82.
**Verified against:** sigmond e999011 on 2026-10-24 — `bin/smd` (`_write_frpc_config`,
  `_fetch_frps_ca_cert`), `scripts/proxmox/sigmond-wizard.sh` (`build_rac_tiers`, `rac_write_config`);
  sigmond-rac f06aa63 (`install-host.sh`, `frps-ca.crt`); AI6VN-PM and its VM, read live.
**Supersedes for HamSCI stations:** the tier LADDER in `plan-remote-access.md` §"what exists today".
  The ladder stays for wsprdaemon-gateway stations.

## Goal

Every HamSCI-managed station holds **two independent secure frpc sessions at once** — one to vpn1, one to
vpn2 — publishing the same RAC-derived remote ports on each. No failover logic and no election: both are
live, and an admin who cannot reach a station through one gateway uses the other.

## Why, and what rob decided (2026-10-24)

- vpn1 and vpn2 are two live, independent gateways. **There is no failover logic** — a station that can only
  reach one of them is still reachable.
- **Secure only, for new installs.** frps-secure on 35736, TLS, the fleet token, TOFU login metadata.
  vpn1 keeps its open tier on 35735 for existing legacy stations; vpn2 never had one.
- One gateway being down, or unreachable from a given station's network, must not cost the other.

## ⛔ The defect this work must fix first: every station pins the WRONG CA

Measured 2026-10-24, on AI6VN's Proxmox host AND its VM, and at the source:

    /etc/sigmond/frps-ca.crt            subject CN=GW2.WSPRDAEMON.ORG   EC:71:A9:00:17:77:28:4E:...
    vpn1.hamsci.org:35736 presents      subject CN=vpn.hamsci.org       7A:F8:BF:F8:E4:3D:C1:F4:...
    vpn2.hamsci.org:35736 presents      the SAME cert as vpn1           7A:F8:BF:F8:E4:3D:C1:F4:...

So a HamSCI station that pins that file **cannot complete a TLS login to either HamSCI gateway**. It fails
verification, the ladder walks down, and the station lands on the legacy open tier — which is exactly where
AI6VN sits today (`serverPort = 35735`, `transport.tls enable = false`).

Two independent causes, and BOTH must be fixed or the bug returns:

1. **`sigmond-rac/install-host.sh:39` installs the payload's own `frps-ca.crt` unconditionally**, and that
   file in the repo is gw2's CA. Any later payload run re-clobbers a corrected cert.
2. **`bin/smd:_fetch_frps_ca_cert` always fetches from `_FRPS_URL` (gw2)**, even for a station whose
   `_write_frpc_config` then points `serverAddr` at `vpn.hamsci.org` (rac 200-299). The fetch and the use
   disagree by construction.

⚠ This is why a pinned CA must be **per gateway**, not one file per station. The two HamSCI gateways share
one self-signed cert today and will get their own Let's Encrypt certs later (redundancy-plan decision 4),
at which point those entries simply drop `trustedCaFile` and use the OS bundle.

## Design

### A gateway is data, not a code path

One table, consulted by both the host writer and the VM writer:

    name   host               port    ca                              serverName
    vpn1   vpn1.hamsci.org    35736   /etc/sigmond/frps-ca-hamsci.crt vpn.hamsci.org
    vpn2   vpn2.hamsci.org    35736   /etc/sigmond/frps-ca-hamsci.crt vpn.hamsci.org
    gw2    gw2.wsprdaemon.org 35736   /etc/sigmond/frps-ca-gw2.crt    (none)

`serverName` is required for vpn2 and harmless for vpn1: both present a cert whose CN and only SAN is
`vpn.hamsci.org`, so hostname verification against the name `vpn2.hamsci.org` fails without it. ⛔ Never
leave `trustedCaFile` empty to work around it — frpc then verifies nothing at all.

A HamSCI station's gateway set is `[vpn1, vpn2]`. A wsprdaemon station's is `[gw2]`. Nothing else changes
about how a station is assigned.

### One session per gateway, one unit per session

    /etc/sigmond/frpc-host-<gw>.toml        config, one per gateway
    sigmond-rac-host@<gw>.service           templated unit, one instance per gateway

**Decision: a templated unit, not a second hand-written one.** A third gateway must cost one table row, not
a new unit file and a new branch in every caller.

Migration, for the stations already deployed:

- `sigmond-rac-host.service` (legacy, reads `/etc/sigmond/frpc-host.toml`) keeps working untouched for any
  station nobody has migrated.
- A migrated station gets `frpc-host-vpn1.toml` + `frpc-host-vpn2.toml`, both instances enabled, and the
  legacy unit stopped and disabled — in that order, so the station is never without a tunnel.
- `/etc/sigmond/frpc-host.toml` becomes a **symlink to `frpc-host-vpn1.toml`**. Several things read that
  path (`pm-align.py`, `identity.py`, the wizard's own re-reads, `smd remote`); a symlink keeps every reader
  correct with one source of truth. ⛔ Do not leave two real files — a station whose two copies disagree is
  exactly the failure this design is meant to remove.

### The switch acts on the set

`sigmond-setup --rac-off` / `--rac-on` act on **every** instance, not one. An operator who turns remote
access off and leaves one gateway live has not turned it off. Same for `pm-align`'s restart.

### Same identity on both

The gateways file a station's first login by TOFU and merge their registries both ways every 5 minutes; a
DIFFERENT key for the same user is rejected as impersonation. So both sessions MUST carry the same `user`,
the same `metadatas.pubkey` and the same token. One key, read once, written into both configs.

## Non-goals

- No failover, health-checking or election between gateways. Both are up, always. Resisting this is the
  point: a station that "chooses" a gateway is a station an admin cannot predict.
- No change to how RAC numbers or ports are derived. The same remote ports are published on both gateways.
- Not fixing the legacy open tier. vpn1 keeps 35735 for stations already on it.

## Open, for rob and the gateway side

- **Keeping the station key across a reimage** (the RAC session's item 3). A field station reimaged with a
  NEW key is locked out by strict TOFU, and the rekey-allow list is a lab-box workaround, not a fix. The
  real fix is to store the key outside the wiped image and have the installer reuse it. That is its own
  piece of work, with its own failure modes (where it lives, what reads it, what happens when the host is
  replaced too). NOT in this plan.
- **Bandwidth at the polar stations.** A second permanently-open control connection costs something on a
  metered link. frpc's heartbeat interval is the lever; a decision is owed before these stations ship.

## Phases

1. **CA correctness.** Per-gateway CA files; `sigmond-rac` ships both certs and stops clobbering; smd fetches
   from the gateway it is about to use. Tests. ← this is the one that unblocks any secure login at all.
2. **Gateway table + host writer.** Templated unit, two configs, migration + symlink, switch acts on the set.
3. **VM writer** (`smd admin rac install`) on the same table.
4. **AI6VN**: migrate, confirm both sessions online and all six ports on each gateway, report back.
5. **Say which session is down.** The switch reports remote access as ON while ANY session is up —
   correct, because the station IS reachable — but nothing yet names a gateway that has been lost. A
   redundant pair whose health is reported as one bit degrades to a single point of failure in silence.
   `rac_status()` in smd already returns `sessions` and `sessions_down`; the host switch and the console
   panel do not surface them yet.

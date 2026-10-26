# Request to HamSCI: a reflector on vpn1/vpn2, and a path to wd10/20/30

Written 2026-10-26 to be forwarded.  Everything below is on HamSCI-operated
hosts; nothing in it can be done from our side.  We hold no account on vpn1 or
vpn2 (re-verified 2026-10-26: root, `wsprdaemon` and `hamsci` all refused on
both, publickey and password).

## What is already true

vpn1 and vpn2 **already run half of this software.**  They serve the frps
tiers from `wsprdaemon-server`'s `install-reflector.sh` — AC0G's AI6VN station
registers on `vpn1.hamsci.org:35736` and `vpn2.hamsci.org:35736` (the
`frps-secure` tier that script installs), with six proxies running on each.

What is missing is the other half of the same script: the **reflector**, which
takes station uploads and forwards them to the WsprDaemon servers.  Today
nothing from the HamSCI gateways reaches wd10/20/30 — those servers' spool
directories hold only `from-gw1` and `from-gw2`.

## What we are asking for

Either option works.  **Option B asks less of you and exposes less of us**, and
is what we would choose.

### Option A — you run the reflector (symmetrical with gw1/gw2)

1. Create station upload accounts (chrooted sftp, as on gw1/gw2 — the same
   `Match Group sftponly` / `ForceCommand internal-sftp` arrangement).
2. Install the reflector from `wsprdaemon-server`:
   `sudo bash install-reflector.sh`, then
   `/etc/wsprdaemon/reflector_destinations.json`:

```json
{
  "incoming_pattern": "/home/*/uploads/*.tbz",
  "spool_base_dir": "/var/spool/wsprdaemon/reflector",
  "destinations": [
    {"name": "WD10", "user": "wsprdaemon", "host": "wd10.wsprdaemon.org",
     "path": "/var/spool/wsprdaemon/from-vpn1"},
    {"name": "WD20", "user": "wsprdaemon", "host": "wd20.wsprdaemon.org",
     "path": "/var/spool/wsprdaemon/from-vpn1"},
    {"name": "WD30", "user": "wsprdaemon", "host": "wd30.wsprdaemon.org",
     "path": "/var/spool/wsprdaemon/from-vpn1"}
  ],
  "scan_interval": 2, "rsync_interval": 2,
  "rsync_bandwidth_limit": 20000, "rsync_timeout": 300,
  "min_age_seconds": 10,
  "log_file": "/var/log/wsprdaemon/reflector.log"
}
```

(`from-vpn2` on vpn2, so provenance stays separable per gateway.)

3. Send us the public key the reflector will use.  We authorise it on each WD
   server **confined to one directory and write-only** — it gets no shell and
   can reach nothing else:

```
restrict,command="/usr/bin/rrsync -wo /var/spool/wsprdaemon/from-vpn1",from="23.92.20.151" ssh-ed25519 AAAA... vpn1-reflector
```

### Option B — you create ONE read-only account, and we pull

No reflector to install, no key of ours on your host, no key of yours on ours.

1. Station upload accounts as in A1.
2. One account, say `wdpull`, whose `authorized_keys` holds three entries we
   supply — one per WD server — each:

```
restrict,command="/usr/bin/rrsync -ro /var/spool/wsprdaemon/outbound",from="<wdN address>" ssh-ed25519 AAAA... wd10-pull
```

3. An age-based sweep of `outbound/` on your side (a cron job), because our
   key is deliberately read-only and cannot delete.

wd10/20/30 already reach vpn1 and vpn2 on port 22 outbound, so this needs
nothing opened anywhere.

## Why we would rather pull

Not distrust — blast radius.  Under A you hold a credential into the
WsprDaemon servers; under B nobody holds a credential into anyone.  If either
side is ever compromised, B contains it.  The `restrict` + `rrsync` lines above
make A's credential nearly worthless on its own, so A is acceptable; B is
simply better and costs you less work.

## A smaller first step, if you would rather not commit yet

**Just one upload account on vpn1, nothing else.**  We point a test station at
it and confirm tars arrive.  They would sit there going nowhere — which is a
complete test of the delivery leg, proves the account and the chroot work, and
commits you to no service, no key exchange and no ongoing operation.  If it
works we come back for the forwarding half.

## Contact

Rob Robinett (AI6VN) / Michael Hauan (AC0G).  Repo:
https://github.com/HamSCI/sigmond — design note in
`tasks/plan-hamsci-reflector-ingest.md`.

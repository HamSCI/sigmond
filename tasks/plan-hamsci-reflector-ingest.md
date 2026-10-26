# Ingesting from the HamSCI reflectors without exposing the WD servers

**Status:** DESIGN PROPOSED 2026-10-26, nothing built.  rob: "we need a secure
and authenticated path from the vpn1/2 machines to wd10/20/30.  I want to
minimize exposure of the WD servers to the systems connected to the HamSCI
reflectors."

## Threat model, stated first because it decides the design

vpn1 and vpn2 are **operated by HamSCI**, with rob (AI6VN) and mjh (AC0G) as
co-administrators alongside W2NAF.  The stations behind them are not
necessarily ones we vet or can audit.

> **Correction, 2026-10-26.**  An earlier revision of this document said we
> held no account on vpn1 or vpn2.  That was wrong.  It came from probing
> `root`, `wsprdaemon` and `hamsci`, all of which are refused, and never
> trying rob's own username.  Administrative access exists and is documented.
> The threat model below is unchanged, because it never rested on who
> administers the gateway: layers 1-3 assume a vpn host may be compromised or
> misconfigured **by anyone**, ourselves included, and layer 3 is the only one
> that survives that.  Having the keys is a reason to build the confinement,
> not a reason to skip it.

So the design must hold under three assumptions:

1. A vpn host may be compromised, or simply misconfigured by someone acting in
   good faith whose priorities are not ours.
2. A station behind a vpn host may be hostile, or may be a legitimate station
   whose credentials have been taken.
3. Neither case may yield a foothold on wd10/20/30, and neither may put forged
   rows into ClickHouse.

"Minimize exposure" therefore has two halves that need different answers:
**network exposure** (who can reach the WD servers) and **data exposure**
(what the WD servers must believe).  Layers 1-2 below address the first;
layer 3 addresses the second, and only layer 3 survives a compromised vpn host.

## Measured starting point (2026-10-26, from wd30)

```
vpn1.hamsci.org   23.92.20.151      port 22 OPEN outbound from wd30, 873 closed
vpn2.hamsci.org   172.238.180.229   port 22 OPEN outbound from wd30, 873 closed
vpn.hamsci.org    23.92.20.151      (same host as vpn1)
/var/spool/wsprdaemon/  on wd30:  from-gw1  from-gw2      <-- no vpn path exists
```

Two consequences:

- **Nothing from the HamSCI side reaches the WD servers today.**  This is a
  greenfield path, not a live one to harden, so there is no migration and no
  compatibility constraint.
- **The low-exposure direction is already open.**  wd30 can reach both hosts
  outbound on 22.  A pull needs no inbound rule, no new listening service and
  no account for HamSCI on our side.

## Layer 1 — the WD servers PULL; they never accept a push

The gw1/gw2 model has the gateway hold a key into the WD servers and push:

```python
# wsprdaemon_reflector.py:818
'rsync', '-a', '-e', f'ssh -i {ssh_key} -o StrictHostKeyChecking=no',
'--remove-source-files', ...
```

That is acceptable for gateways rob operates.  **Extending it to hosts someone
else operates would be the exposure rob is asking to avoid** — it would put a
credential for wd10/20/30 on a machine we do not control, and
`StrictHostKeyChecking=no` would accept whatever host key answers.

Inverting the direction moves the credential to our side of the trust
boundary.  wd10/20/30 each hold a key into a restricted account on vpn1/2;
vpn1/2 hold nothing that opens anything of ours.  A full compromise of a vpn
host yields the attacker no path inward — only the ability to serve us bad
files, which layer 3 answers.

**Consistency note:** `plan-status-via-uploader.md` rejected a server-side pull
collector, on the grounds that pulling from 92 roaming stations would mean
building a fan-out scheduler, concurrency control and key handling on the
server.  That objection does not transfer here: this is two fixed,
DNS-stable hosts, and the scheduler is a cron-shaped loop.

## Layer 2 — confine what the pull account can do, on their side

The ask to HamSCI is small and exactly stateable — far smaller than "install
and run the reflector", which we already know we cannot do there.

One account, three keys (one per WD server), each entry:

```
restrict,command="/usr/bin/rrsync -ro /var/spool/wsprdaemon/outbound",from="<wdN address>" ssh-ed25519 AAAA... wd10-pull
```

- `restrict` (OpenSSH 7.4+) disables port forwarding, agent forwarding, X11,
  PTY allocation and user-rc.  It is a default-deny that stays correct as
  OpenSSH adds features, unlike enumerating `no-*-forwarding` by hand.
- `rrsync -ro` ships with rsync: read-only, confined to one directory.  The
  key cannot run a shell, cannot rsync anything else, cannot write.
- `from=` pins the source address.
- **Read-only is deliberate, including no delete.**  We should not hold write
  access on a host we do not control, so retention stays their business
  (see below).

On our side, `StrictHostKeyChecking=yes` with each host key pinned at
enrolment.  Never `=no` on this path — the whole point is that we do not
assume the far side is honest.

### Retention, since a read-only puller cannot clean up

With push + `--remove-source-files` the pusher tidies.  With three independent
read-only consumers, nothing does.  Options, in preference order:

1. **HamSCI runs an age-based sweep** on `outbound/`.  Costs them one cron job
   and costs us no write access.  Recommended.
2. Per-consumer subdirs that each WD may delete from — needs write, weakens
   layer 2, and gains little.

Each WD tracks what it has already fetched locally and pulls with
`--ignore-existing` into its own staging dir, so a file still awaiting their
sweep is not re-fetched.  `wsprdaemon_server.py` already keeps a processed-hash
cache, so a duplicate that slips through is absorbed rather than double-counted.

## Layer 3 — sign the payload, so transport trust is not load-bearing

Layers 1-2 stop a compromised vpn host from reaching into the WD servers.
Neither stops it from **serving forged tars**, and no amount of ssh hardening
can: the host is the legitimate holder of the connection we are pulling over.

`ssh-keygen -Y sign` / `-Y verify` against an allowed-signers file built from
the TOFU registry closes that, and OpenSSH 10 on the fleet already has it.  A
detached signature travels beside each tar; the WD server verifies before it
parses.  A compromised reflector may then delay or drop — a denial of service,
visible as silence — but cannot forge, inject or alter.

**This reverses a decision I made twice, and the reason it reverses is
specific.**  `plan-status-via-uploader.md` lists signed envelopes as a non-goal
because "delivery already requires the station's own gateway account, so
attribution is no worse than the spot path it joins."  That argument depends on
the account being issued by us.  On the HamSCI path it is issued by someone
else, so the premise fails and the conclusion with it.  **Signing is a
prerequisite for this path, not an enhancement** — without it, "authenticated"
in rob's request means only "the pipe was authenticated", which is not the
property that matters.

## Layer 4 — contain what arrives, assuming some of it is bad

- **Separate spools** `from-vpn1` / `from-vpn2`, on their own filesystem or
  under a quota, so a flood cannot fill the WD server's root.
- **Caps per pull cycle** on total bytes and file count; exceeding one stops
  the pull and alarms rather than ingesting.  A reflector that suddenly offers
  10x its usual volume is a finding, not a backlog.
- **Provenance in the row.**  `station.heartbeat` already carries `gateway`,
  derived from the spool directory; `psk.spots` should carry the same.  It is
  what makes "drop everything from vpn1 between X and Y" a surgical operation
  instead of a restore from backup.
- **Unsigned or badly-signed tars are quarantined, never parsed**, and the
  count is a metric someone watches.

## Non-goals

- **Running our reflector on vpn1/vpn2.**  We have no root there and should
  not ask for it; the point of this design is to need as little from them as
  possible.
- **Giving HamSCI accounts on wd10/20/30.**  That is the exposure being
  removed.
- **Trusting the vpn hosts with anything.**  They are a transport, and after
  layer 3 they are not even a trusted one.
- **Changing the gw1/gw2 path.**  It stays push; rob operates those hosts.
  (Layer 3 should extend to it eventually, but that is its own change.)

## Traps

1. **`StrictHostKeyChecking=no` must not be copied from the gw path.**  It is
   the existing idiom in this codebase (`wsprdaemon_reflector.py:818`), so it
   will be copied unless the review catches it.  Pin the host keys.
2. **A read-only key that is quietly given write access** to solve the
   retention problem removes layer 2's main guarantee.  If it is ever
   proposed, it needs this document reopened, not a config edit.
3. **Signature verification that fails open.**  A verifier that treats "no
   allowed-signers file" or "verification raised" as pass is worse than no
   verifier, because it looks like protection.  Test it by mutation: corrupt
   a signature, delete the signers file, and confirm the tar is quarantined
   in both cases.
4. **Pulling as root.**  The puller runs as an unprivileged service account
   whose only right is to write its own staging dir.
5. **`rrsync` path assumptions.**  Its location and flags differ across
   distributions (`/usr/bin/rrsync`, `/usr/share/rsync/scripts/rrsync`).
   Confirm on their host rather than specifying ours.

## Phases

### Phase 0 — settle whether this path is needed at all

**Measured on AI6VN, 2026-10-26.**  The two planes are already separate, and
only one of them exists on the HamSCI side:

| plane | AI6VN today | state |
|---|---|---|
| RAC | `sigmond-rac-host@vpn1` + `@vpn2`, user `DASI-009`, TLS tier 35736 | **both registered, 6 proxies running each** — dual-RAC confirmed on hardware |
| uploads | `servers = ["gw1.wsprdaemon.org", "gw2.wsprdaemon.org"]`, `hs-uploader` active | works; spots reach wd10/20/30 through the wsprdaemon reflectors |

So AI6VN already rides vpn1/vpn2 for remote access while its spots go by a
different path entirely.  RAC proxies are ssh/web/station/gmag TCP tunnels;
they are not an upload path and never carry a tar.

**This makes AI6VN the ready-made pilot the moment HamSCI acts**, because the
station side is a one-line change — point the `wsprdaemon_tar` pipeline's
`servers` list at the HamSCI gateways — and nothing else moves.  The blocker
is entirely on the far side: there is no upload account and no reflector
there, and `/var/spool/wsprdaemon` on wd30 still shows only `from-gw1` and
`from-gw2`.

The ask can also be staged, and the first stage proves something on its own:
an upload account alone lets us confirm the SFTP leg (tars land on vpn1) with
no reflector and no pull service built yet.  Files would sit there going
nowhere, which is a complete test of half the path and costs HamSCI one
account.

- [ ] **Does anything deposit uploads on vpn1/vpn2 today?**  wd30 shows no
      `from-vpn*` spool, and those hosts may be purely frp/RAC endpoints with
      no upload role.  If nothing deposits there, this is a pipe for traffic
      that does not exist and should wait.  Answerable directly: rob and mjh
      administer both hosts.
- [ ] If stations behind vpn1/2 upload to **gw1/gw2** instead (as every
      sigmond station currently does), the gap closes with no new path at all
      and this plan is unnecessary.  Check before building.

### Phase 1 — the accounts on vpn1/vpn2

- [ ] One `wdpull` account per vpn host; three `restrict` + `rrsync -ro`
      authorized_keys entries; an agreed `outbound/` directory and sweep age.
- [ ] Exchange host-key fingerprints out of band for pinning.

### Phase 2 — signing, which gates the rest

- [ ] Detached `ssh-keygen -Y sign` beside each tar, station-side.
- [ ] `-Y verify` in `wsprdaemon_server.py` against an allowed-signers file
      built from the TOFU registry; quarantine on any failure.
- [ ] Fail-closed tests by mutation (trap 3).

### Phase 3 — the puller

- [ ] A pull service on each WD server: pinned host key, restricted key,
      `--ignore-existing` into its own staging dir, local fetched-record.
- [ ] Caps, separate spool, alarm on exceeding (layer 4).
- [ ] `gateway` provenance recorded for every row ingested from this path.

### Phase 4 — extend signing to the gw1/gw2 path

- [ ] Same verifier, same registry, so the WD servers stop trusting any
      gateway rather than just these two.

## Open items for rob

1. **Phase 0 is the gating question** — do the HamSCI reflectors receive
   uploads at all?  Everything here is premature if they do not.
2. **Who at HamSCI agrees the Phase 1 ask?**  It is small, but it is not ours
   to implement, and the design is worthless without it.
3. **Is signing acceptable as a prerequisite?**  It is real work and it has
   been deferred twice.  The alternative is accepting that a compromised vpn
   host can inject rows that look like any station's.

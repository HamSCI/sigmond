# Request to HamSCI: a reflector on vpn1/vpn2 — WITHDRAWN 2026-10-26

This document was a forwardable ask, written on the premise that we held no
account on vpn1 or vpn2 and therefore had to request every step from HamSCI.

**The premise was wrong.**  rob (AI6VN) and mjh (AC0G) are documented
co-administrators of both gateways alongside W2NAF.  The earlier probe tested
`root`, `wsprdaemon` and `hamsci` — all correctly refused — and never tried
rob's own username.  Nothing needs to be requested; the work is ours to do,
and rob authorised it on 2026-10-26.

Two further corrections to what this file used to say:

- It recommended **closing port 35735 on vpn1** as unnecessary attack surface.
  Retracted.  The polar-PSWS stations use that port today, and the USAP
  Station Information Package names `vpn.hamsci.org:35735` by address and
  port.  A gateway's ports are a fleet-wide contract; closing one strands the
  stations that cannot be reached to reconfigure.  `plan-rac-dual-gateway.md`
  has it right: vpn1 keeps 35735.
- It asked **what enforces the closed ports** on vpn1/vpn2 and proposed a
  Linode Cloud Firewall.  Answered: host `nftables`.  rob's decision
  (2026-10-26) is to stay on `nftables`, which both administrators can read
  and change directly.  A cloud firewall attached to vpn1 on 2026-10-01
  blocked every station port until it was detached.

The design that survives — WD servers pull rather than accept a push,
`rrsync -ro` confinement, and payload signing — is unchanged and lives in
[`plan-hamsci-reflector-ingest.md`](plan-hamsci-reflector-ingest.md).

Gateway-specific operational detail (accounts, addresses, firewall rules,
per-host port state) belongs in the private `HamSCI/hamsci-remote-admin`
repository, not here.  This repository is public.

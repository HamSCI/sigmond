# Satellite link — stations whose internet comes and goes

> **Audience:** operator, fleet admin, and the site's IT department
> **Status:** current
> **Verified against:** sigmond f3b0c6e on 2026-11-18 — keys and defaults checked against bin/sigmond-link; probe traffic measured on the AI6VN lab stations behind the ODROID gateway (sat-sim) on 2026-11-14.
> **Canonical for:** the satellite-link guard (`[link_schedule]`), what its probe costs, and how the list of addresses a station talks to is collected

Some sites (the South Pole is the first) reach the internet only while a
satellite is overhead. Between passes the link is dead. A station there keeps
recording through the gaps; what it must not do is keep hammering a dead link,
or keep remote-access tunnels and package downloads retrying all night.
`sigmond-link` is the guard. It is installed on every station and on every
Proxmox host, and it does nothing until it is switched on.

## Switching it on

In the decoder VM, `/etc/sigmond/topology.toml`:

```toml
[link_schedule]
enabled = true
mode = "probe"        # or "schedule"
```

- **`mode = "probe"`** — no schedule. Every `probe_min` minutes (default 5)
  the station opens ONE TCP connection to a gateway it already uses (vpn1/vpn2
  at a HamSCI station, gw1/gw2 at a wsprdaemon one), trying them in turn until
  one answers. The link is "up" while they answer and "down" after
  `probe_fail_count` (default 2) rounds without an answer.
- **`mode = "schedule"`** — the published USAP satellite schedule (`source =
  "usap"`, the weekly PDF) decides; the probe can only *open* the link early
  (it never closes it), so a gateway outage cannot silence a station whose link
  is fine.
- **`probe_min = 0`** — never probe (a site whose IT objects); the schedule
  alone decides.

While the link is down the station pauses its remote-access tunnels (with a
6-hour ceiling, after which they try regardless), skips package and schedule
downloads, and its uploader holds spots on disk until the link returns.
Every guard fails open: with no schedule and no answer from the probe, it lets
everything run.

The Proxmox **host** follows the VM: its `/etc/sigmond/link.toml` says
`follow_vm = 100`, and it reads the VM's `[link_schedule]` through the guest
agent (hourly; `sigmond-link status` on the host shows when). The host keeps
its own tunnel list and probe targets (it probes its own gateway), and the VM
can shorten but never lengthen the host's 6-hour ceiling. To make the host
re-read at once: `rm /var/lib/sigmond/link-vm.toml`.

`sigmond-link status` (VM or host) shows the mode, whether the link is up, the
last probe, and when the next change is due.

## What the probe costs

One probe is one TCP handshake and close to a gateway port the station's
remote-access tunnel already uses (35736): about 6 packets and 450 bytes, plus
a DNS lookup of the gateway's name that the site's resolver usually answers
from cache.

Measured on 2026-11-14 at the ODROID lab gateway: two probes (one from the VM,
one from its host) plus the host's own tunnel traffic on the same port in the
same seconds came to 38 packets, 2.7 KB. Taking that as an upper bound:

| | per probe | per machine per day (every 5 min) | per station per day (VM + host) |
|---|---|---|---|
| bytes | 0.45 KB (at most 1.35 KB) | 130 KB (at most 390 KB) | 260 KB (at most 780 KB) |
| connections | 1 | 288 | 576 |

In a gap the probe tries every gateway in turn (up to 4 targets, each a few
unanswered connection attempts) — but those packets never leave the site,
because the satellite link is down; they cost nothing on the satellite.
`probe_min` trades how quickly the station notices a pass against this cost:
at 5 minutes it notices within 5 minutes and spends under 1 MB a day.

## The list of addresses a station talks to (for site IT)

A site's IT department will ask what the station connects to. The lab gateway
the AI6VN test stations sit behind (an ODROID running `sat-sim`, which also
cuts the lab's link on the USAP schedule) counts every new outbound connection
while the link is up, per source, destination, protocol and port:

```
sat-sim inventory          # table: destination, name, protocol, port, connections, which machine
sat-sim inventory --csv    # the same as CSV, for the IT department
```

The counts accumulate in `/var/lib/sat-sim/inventory/inventory.json` on the
ODROID (harvested hourly and at every link change). Collect at least a week:
some jobs run daily or weekly (package updates, the satellite schedule, the
ionosphere maps). The lab stations run the same software as a South Pole
station would, so their list is the list to give the site.

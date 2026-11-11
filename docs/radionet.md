# Site radio network

Several Sigmond stations at one site can share their radiod streams: each
decoder VM gets a second NIC on a private radio network, and the Proxmox hosts
join those networks with a VXLAN tunnel.  A stream crosses to another station
only while a recorder there has joined it (IGMP snooping), so an idle tunnel
carries nothing.  KPH and KFS run this over IPv4; on 2026-11-11 a 6500 and a
Beelink proved it on an IPv6-only VLAN (0 kb/s before a join, 11.95 Mb/s while
joined, 0 after the leave).

## Set it up

On **each** Proxmox host at the site, as root:

```
sigmond-radionet setup --index N --peer <other host> [--peer <another host> ...]
```

- `--index N` (1–99) must differ per station.  The host gets `10.98.0.N`, its
  decoder VM `10.98.0.(100+N)`.
- `--peer` is another host's address on the site LAN.  Use the same family on
  every station: all IPv4 or all IPv6 (an IPv6-only site uses the hosts' SLAAC
  addresses).  The radio network's MTU follows: 1450 over IPv4, 1430 over IPv6.

It creates vmbr9 (with `ip`, never `ifup`, which can take vmbr0's IPv6 address
down with it), writes `/etc/sigmond/radionet.conf`, adds the VM's second NIC,
starts the tunnel and its two-minute timer, and then runs
`smd radionet apply` in the VM through the guest agent.  That configures the
radio NIC, points every radiod at it with ttl 1, removes the 239/8→lo route for
good and restarts the station.

## Check it

- Host: `sigmond-radionet status` -- the tunnel, its peers, the VM's radio NIC.
- VM: `smd radionet status` -- the radio NIC, the 239/8 route, each radiod's
  ttl and iface.

## Ports

The tunnel is VXLAN on UDP 4789 between the hosts only; sigmond-radionet's
nftables table drops 4789 from anyone else.  See [ports.md](ports.md).

# Ports a Sigmond station uses

What listens on a Sigmond appliance station, and the range reserved for
services added beside it. Use it before giving a new service a port.

The inventory was taken with `ss -ltnup` on a v3.90 station (an i5-6500T on
an IPv6-only network) on 2026-11-11, and checked against the appliance's
port forwarding (`sigmond-vm4relay` in `firstboot-v3.sh`). A station with
more hardware may run more, for example the magnetometer's websocket.

A station has two machines: the **decoder VM**, where radiod and every
recorder run, and the **Proxmox host** that holds it, which is what the site
LAN sees.

## Decoder VM

| Port | Proto | Service |
|---|---|---|
| 22 | tcp | ssh |
| 53, 5355 | tcp, udp | systemd-resolved (local resolver, LLMNR) |
| 323 | udp | chrony |
| 1883 | tcp | mosquitto (MQTT) |
| 5004 | udp | ka9q-radio RTP data streams (multicast) |
| 5006 | udp | ka9q-radio status and control (multicast) |
| 5353 | udp | mDNS (avahi): radiod's `*.local` names |
| 8000 | tcp | station-web (hf-timestd timing dashboard) |
| 8081 | tcp | ka9q-web (live receiver page) |
| 8082 | tcp | gmag-webui (magnetometer page) |
| 8765 | tcp | mag-usb live sample websocket (when a magnetometer is fitted) |

High-numbered UDP ports in the listing are ephemeral client sockets.

## Proxmox host

| Port | Proto | Service |
|---|---|---|
| 22 | tcp | host ssh |
| 2022 | tcp | host ssh, when `sigmond-vm4relay ssh22 on` gives the LAN's port 22 to the VM |
| 2222 | tcp | forwarded to the VM's ssh |
| 8000, 8081, 8082, 8765 | tcp | forwarded to the same VM services, from the station's own network only |
| 8006 | tcp | Proxmox web UI |
| 85, 111, 3128, 25 | tcp | Proxmox internals (pvedaemon, rpcbind, spiceproxy, local mail) |
| 53 | tcp, udp | dnsmasq, the VM's resolver, on 10.99.0.1 only |
| 7503 | tcp | frpc admin (remote access), localhost only |
| 7790 | tcp | console painter, 10.99.0.1 only |
| 12222–12225 | tcp | IPv6-to-VM relay sockets, localhost only |
| 68, 546 | udp | DHCP and DHCPv6 clients |
| 323 | udp | chrony |

Only the forwarded ports (2222, 8000, 8081, 8082, 8765) reach the VM from the
LAN. A new service in the VM is not reachable from the LAN until its port is
added to `sigmond-vm4relay` in the appliance.

## Reserved: 8090–8099

**TCP and UDP 8090–8099 are reserved for ka9q-radio's own services**
(Phil Karn, KA9Q), starting with his web monitor; 8090 is the first choice.
Nothing in Sigmond or the appliance may use a port in this range.

Those services talk between decoder VMs over the site radio network, the
VXLAN link between the hosts (`sigmond-radionet`), so the host does **not**
forward them to the LAN. If one of them should be reachable from the station
LAN, add its port to `sigmond-vm4relay` and to the host table above.

## Ports to stay off

When choosing a port for something new, also avoid:

- 8080: unused by Sigmond, but WsprDaemon sites and many web tools use it.
- 8073: KiwiSDR, which a station may reach on its LAN.
- 9000: the example port for a GNSS VTEC receiver a station may poll.

# plan: IPv6 support for the appliance (McMurdo)

**Status — 2026-09-28: CASE A VALIDATED ON A BENCH.** The architecture in §3
works. An unmodified IPv4-only decoder VM reached every upstream in §1 across a
genuinely IPv6-only site. See "§6 Bench validation" at the end for the setup,
the results, and the six changes it proved are required.

**Status — 2026-09-23: STUDY ONLY. No code written, nothing decided.**
Prompted by Nathaniel: *McMurdo uses ONLY IPv6.* This note is the survey rob
asked for before any commits. It ends with three questions that must be
answered by someone with access to McMurdo's network, because the answers
change the design and nothing should be built before then.

---

## 1. The finding that inverts the problem

The obvious framing is "the appliance is IPv4-only, teach it IPv6." That
framing is wrong, and building to it would waste the effort.

**Our upstreams are IPv4-only.** Measured 2026-09-23 with `host -t AAAA`:

| host | A | AAAA | what breaks without it |
|---|---|---|---|
| `vpn.hamsci.org` | ✅ | ❌ | RAC — no remote access to the station, at all |
| `gw2.wsprdaemon.org` | ✅ | ❌ | RAC fallback tier |
| `pswsnetwork.eng.ua.edu` | ✅ | ❌ | PSWS uploads — the science product |
| `github.com` | ✅ | ❌ | every component clone, `smd component update`, bring-up |
| `pool.ntp.org` | ✅ (4) | ❌ | time bootstrap |
| `wsprnet.org` | ✅ | ✅ | the one upstream that would work |

So a station at McMurdo could speak flawless IPv6 and still reach almost
nothing it needs. **Dual-stacking our code is neither sufficient nor the
main problem.** The problem is reaching IPv4-only services from a network
that has no IPv4.

That is a translation problem, and translation belongs at a boundary — not
smeared across 25 components.

## 2. What is IPv4 today, and what must NOT change

Audited across `sigmond-appliance` and `sigmond`: **zero IPv6 handling
anywhere** — not in `firstboot-v3.sh`, not in `sigmond-wizard.sh`, not in the
RAC path. That sounds like a large surface. It is not, because of where the
IPv4 sits.

**Inside the decoder VM — IPv4, and should STAY IPv4:**
- radiod ↔ client RTP is IPv4 multicast; `ka9q-python` hard-codes `AF_INET`
  in `_multicast.py`, `discovery.py`, `rtp_recorder.py`, `multi_stream.py`,
  `spectrum_stream.py`, `control.py`.
- the whole sink/uploader/decoder stack rides on that.

This traffic **never leaves the host**. Since v3.44 the decoder VM lives on a
host-only `/30` (`10.99.0.1` PM ↔ `10.99.0.2` VM) and the PM routes and NATs
for it. The site LAN never sees it. So the site being IPv6-only has *no
bearing* on radiod, and converting ka9q multicast to IPv6 would be a large,
risky change that buys nothing.

⚡ **This is a design dividend we already paid for.** The VM-behind-the-host
topology, built for the Scranton VLAN split, is exactly what confines the
IPv6 problem to one machine.

**On the Proxmox host — IPv4, and this is the real work:**
- PVE auto-install answer file: `[network] source = "from-dhcp"` → IPv4 DHCP.
  No lease ⇒ silent fallback to static `192.168.100.2/24`, unroutable, and on
  an appliance whose USB controller is passed to the VM there may be no
  keyboard either. **That is a dead install** (`answer.toml.template:10-11`).
- `sigmond-netfix` probes with `dhclient` and reads `ip -4` throughout. On an
  IPv6-only LAN it finds carrier on every NIC, no DHCP answer on any, and
  prints *"NO DHCP SERVER ANSWERED"* — correctly, and uselessly. There is no
  cable to move (`firstboot-v3.sh:185-215`).
- `sigmond-setnet <addr>/<cidr> <gateway>` — IPv4 literals, IPv4 ping proof.
- `MASQUERADE -s 10.99.0.0/30 -o vmbr0` — **requires vmbr0 to have IPv4.**
  This is the load-bearing line. With no IPv4 on the site interface the VM's
  outbound has nowhere to go: no git, no PSWS, no NTP (`firstboot-v3.sh:551`).
- `DNAT :8000/:8081/:8082/:2222 → 10.99.0.2` — LAN-local access to the VM.
- `HOSTIP` discovery, the console panel, the wizard summary: all `ip -4`.
- RAC: `frpc` on the PM dials `vpn.hamsci.org` / `gw2.wsprdaemon.org`, neither
  of which has an AAAA (§1).

## 3. Architecture: the PM is the translation boundary

**The VM keeps speaking IPv4 and never learns IPv6. The PM translates.**

Everything above points one way: put the boundary where the NAT already is.
The VM already believes the PM is its router; making the PM *also* the
protocol translator changes nothing the VM can observe, and leaves radiod,
ka9q-python and all 25 components untouched.

What that means in practice depends entirely on what McMurdo provides, and
there are three cases:

### Case A — McMurdo provides NAT64 + DNS64 (most likely)
This is how IPv6-only networks reach the IPv4 internet at all, and if
McMurdo has working IPv6-only clients today it almost certainly has this.

Then the PM runs a **CLAT** (customer-side translator) and we get 464XLAT:
the VM's IPv4 packets are translated to IPv6 on the PM, carried to the site's
NAT64, and translated back to IPv4 at the far end. The VM is unaware.

All three translators are packaged in Debian 13 — verified 2026-09-23:
`clatd` 2.1.0, `tayga` 0.9.2, `jool-dkms` 4.1.13. `clatd` is purpose-built
for exactly this (it discovers the NAT64 prefix via RFC 7050 and configures
TAYGA), which makes it the first candidate.

Work: teach the installer and netfix to obtain an IPv6 address (SLAAC or
DHCPv6), detect the NAT64 prefix, install and start the CLAT, and point the
existing MASQUERADE at it. **RAC then works unmodified**, because frpc still
dials an IPv4 address and the CLAT carries it.

### Case B — McMurdo is IPv6-only with NO translation
Then no IPv4-only upstream is reachable by anybody there, not just us, and
the only route out is a tunnel to an endpoint that has IPv4.

We already own such an endpoint: **the RAC gateway**. But `vpn.hamsci.org`
has no AAAA and no IPv6 listener. So Case B's work is mostly *not* in this
repo — it is giving the HamSCI gateway an IPv6 address and an frps listener
on it, after which the station tunnels everything through RAC.

⚠ This case makes the gateway a hard dependency for *science data*, not just
for remote access. That is a significant change in what RAC is for, and it
should be a deliberate decision rather than a consequence.

### Case C — dual-stack, or IPv4 available on request
Then almost nothing is needed beyond not *assuming* IPv4 in netfix's probe.
Worth confirming before doing anything else, because it costs nothing to ask
and it eliminates the whole project.

## 4. Non-goals

- **Not** converting radiod / ka9q-python multicast to IPv6. It is confined
  to the host-only link and there is no benefit.
- **Not** dual-stacking the decoder VM. One protocol inside the VM is a
  feature; the translation boundary is the PM.
- **Not** rewriting `sigmond-setnet` / the panel for v6 literals before §5 is
  answered — the answers decide whether they need it.
- **Not** touching the v3.51 release. This is its own line of work.

## 5. ⛔ The three questions that block design

These are for Nathaniel, or whoever runs McMurdo's network. Nothing should be
built before they are answered, because each answer selects a different case
above:

1. **Is there NAT64/DNS64?** If yes, what is the NAT64 prefix, or is RFC 7050
   discovery (`ipv4only.arpa`) available? → selects Case A.
2. **How does a host get its address — SLAAC, DHCPv6, or static?** This
   decides what the PVE auto-installer must be told, and whether the install
   can be unattended at all.
3. **Is any IPv4 obtainable** — a dual-stack VLAN, an RFC1918 range with NAT,
   anything? → Case C, and the project shrinks to almost nothing.

Two supporting questions, lower priority:
4. Is outbound TCP to arbitrary ports permitted (RAC dials 35735/35736), or
   is egress restricted to a proxy?
5. Is there a local NTP source? An IPv6-only site with no NAT64 cannot reach
   `pool.ntp.org`, and the station's whole purpose is timing.

## 6. Phases (provisional — do not start before §5)

- **Phase 0 — answers.** §5. Cheap, and it decides everything.
- **Phase 1 — see the network we are on.** Make netfix *report* IPv6
  correctly even if it cannot yet use it: SLAAC/DHCPv6 address, default
  route, NAT64 prefix if discoverable. Today it says "no DHCP" and stops,
  which is true and unhelpful. Console panel shows what it found. **No
  behaviour change** — this is diagnosis, and it is useful on every site.
- **Phase 2 — survive the install.** The PVE answer file and the first-boot
  path must not fossilize an unroutable IPv4 when the site is IPv6-only.
  Failing loudly at the console with what it saw beats a dead box.
- **Phase 3 — the translator** (Case A): `clatd` on the PM, MASQUERADE
  repointed, verified by the VM reaching github and PSWS unchanged.
- **Phase 4 — RAC over IPv6** (Case B, or belt-and-braces for A): AAAA +
  frps listener on the gateway; the tier ladder gains v6 rungs.
- **Phase 5 — inbound.** The `:8000/:8081/:8082/:2222` forwards for
  LAN-local access. Note `ip6tables` cannot DNAT to an IPv4 address; this
  wants a small TCP proxy on the PM (socat or nginx stream) listening on v6
  and connecting to `10.99.0.2`. Lowest priority — RAC covers remote access,
  and this is only for someone standing on the McMurdo LAN.

## 7. Testing

The nested test (`test-nested-v3.sh`) runs against a Proxmox host we create,
so an **IPv6-only nested network is buildable** — a bridge with no IPv4, RA
from a `radvd`, and a NAT64 to fake the site. That is the only honest way to
test this; a code review will not catch a fossilized address.

Whatever lands, it must be provable on that harness before an image goes
anywhere near Antarctica, where nobody can move a cable.

---

Related: `sigmond-appliance/firstboot-v3.sh` (netfix, setnet, NAT/DNAT),
`sigmond/scripts/proxmox/sigmond-wizard.sh` (RAC tier ladder), and
`docs/networking.md` (IGMP-snooping, the other network-silent-failure note).


---

# §6 Bench validation — 2026-09-28

## The bench

A Raspberry Pi (`WD-DEV-PI`) emulating McMurdo: `radvd` + `unbound` DNS64 +
`tayga` NAT64 on an isolated segment, IPv4 upstream only. AI6VN-PM's site NIC
was moved onto it, so `vmbr0` had **no IPv4 address and no IPv4 route** —
verified, not assumed. A Wi-Fi management path (no default route) kept the host
reachable; the VM has no Wi-Fi path, so the VM's result is clean.

`jool` was the first choice for the NAT64 and does **not** build on kernel 6.18
(`struct flowi4` lost `flowi4_tos`); `tayga` is userspace and kernel-agnostic.
A **network-specific prefix** was used, not the well-known `64:ff9b::/96`,
because RFC 6052 §3.1 forbids the well-known prefix with private IPv4 and a
station must reach RFC1918 hosts too.

## Result: the VM, unmodified, IPv4-only

```
github:443                       OK
wsprnet:80                       OK
RAC frps vpn.hamsci.org:35735    OK      <- remote access survives
PSWS pswsnetwork.eng.ua.edu:22   OK
git ls-remote HamSCI/sigmond     OK
curl https://github.com          HTTP 200 in 0.38 s
```

`clatd` (Debian 2.1.0) discovered the site's prefix by RFC 7050 unaided and
built the CLAT. `apt-get install` ran on the host with zero IPv4 addresses.
**The VM never learned IPv6 and needed no change**, which was the bet in §3.

## The six changes this proved are required

1. **`MASQUERADE` must also target the CLAT.** `firstboot-v3.sh:751` writes
   `-s 10.99.0.0/30 -o vmbr0` only. With no IPv4 on `vmbr0` the VM is cut off —
   measured. Adding `-o clat` restored it immediately. **One line.**
2. **`clatd` must be installed and enabled on the PM**, and is packaged.
3. **The PM must serve DNS to the VM over IPv4.** Nothing does today. The guest
   is IPv4-only; the site resolver is IPv6-only and unreachable from it. dnsmasq
   on `vmbr1` forwarding to the site resolver over IPv6 works — plus
   **`filter-AAAA`**, because the site's DNS64 synthesises AAAA the guest has no
   route for and happy-eyeballs will wait on them.
4. ⛔ **Nothing consumes RDNSS.** Proxmox has no `rdnssd` and no
   `systemd-resolved`, so a v6-only station comes up with **no resolver at all**
   while `resolv.conf` still names a dead IPv4 server. This blocks everything
   else, including NAT64 discovery. Hard requirement.
5. ⛔ **`accept_ra=2` is required.** `vmbr0` has `forwarding=1` (it routes for
   the VM) and Linux **ignores RAs on a forwarding interface** at the default
   `accept_ra=1`. SLAAC silently did nothing until it was set. **Every sigmond
   PM forwards**, so this hits every station.
6. ⚠ **A stale DHCP lease survives the move.** `vmbr0` kept `10.22.23.31/24`
   and its default route on a LAN where neither existed. "No IPv4 address"
   cannot be used to detect a v6-only site; the lease must be released.

## Also found

- `sigmond-net-probe` called a working station dead — two bugs, fixed in
  sigmond-appliance `5275cbe`.
- The CLAT route carries **MTU 1260**. Nothing broke at this scale, but PMTUD
  blackholes are the classic 464XLAT failure and are not exercised by these
  tests.
- Both `pswsnetwork.caps.ua.edu` (NXDOMAIN) and `pswsnetwork.eng.ua.edu` (live)
  appear in the deploy tree. Unrelated to IPv6; worth chasing.

## NOT yet tested

- **The PVE installer** (`answer.toml:11 source = "from-dhcp"`, §2). It fails
  before any of the above can run, and needs a fresh install on a v6-only LAN —
  a scratch machine or a nested install, not this host.
- Sustained operation: radiod capture, decode, upload over hours.
- Case B/C from §3 (no NAT64 at the site).

#!/usr/bin/env python3
"""Public-IP guard — refuse to run a station that is directly on the internet.

A sigmond station ships with password logins, NOPASSWD sudo for the operator
account, and a fleet of network services.  It is designed for PRIVATE networks
(home LAN, CGNAT, lab bench) and must not sit on a publicly routable IPv4
address.  This guard:

  * detect: reports any interface holding a globally routable IPv4 address.
    RFC1918 (10/8, 172.16/12, 192.168/16), CGNAT (100.64/10 — Starlink et al),
    loopback and link-local are all fine.  IPv6 global addresses are only
    NOTED, never enforced: essentially every modern LAN hands out GUAs behind
    the router firewall, so they are not evidence of internet exposure.

  * --enforce (sigmond-netguard.service, timer every 5 min): on violation,
    log CRIT, wall the console, and run the bulk `smd stop` — which leaves
    operator-managed units (the wd-rac remote-access tunnel) up, so the
    operator can still reach the box to fix its networking.

Override: an operator who genuinely means to run on a public address creates
/etc/sigmond/allow-public-ip (contents ignored).  The guard then logs the
exposure once per run but takes no action.
"""

import ipaddress
import json
import subprocess
import sys
from pathlib import Path

OVERRIDE = Path("/etc/sigmond/allow-public-ip")


def public_addrs():
    """Return ([(ifname, ipv4), ...], [(ifname, ipv6), ...]) of globally
    routable addresses per interface.  Never raises; returns ([], []) when
    `ip` is unavailable or unparsable."""
    v4, v6 = [], []
    try:
        out = subprocess.run(["ip", "-j", "addr"], capture_output=True,
                             text=True, timeout=10)
        links = json.loads(out.stdout or "[]")
    except Exception:
        return v4, v6
    for link in links:
        name = link.get("ifname", "?")
        for a in link.get("addr_info", []):
            try:
                ip = ipaddress.ip_address(a.get("local", ""))
            except ValueError:
                continue
            if not ip.is_global:
                continue
            (v4 if ip.version == 4 else v6).append((name, str(ip)))
    return v4, v6


def main() -> int:
    enforce = "--enforce" in sys.argv[1:]
    v4, v6 = public_addrs()

    if v6 and not v4:
        # Informational only — see module docstring.
        print("netguard: note: global IPv6 address(es) present "
              f"({', '.join(f'{i}:{a}' for i, a in v6)}); "
              "not treated as internet exposure", file=sys.stderr)

    if not v4:
        return 0

    exposure = ", ".join(f"{i}: {a}" for i, a in v4)
    if OVERRIDE.exists():
        print(f"netguard: PUBLIC IPv4 present ({exposure}) but "
              f"{OVERRIDE} override is set — continuing", file=sys.stderr)
        return 0

    msg = (f"sigmond netguard: PUBLIC IPv4 address on this host ({exposure}). "
           "This station must not run directly on the internet — stopping "
           "station services (remote-access tunnel stays up). Move the host "
           "behind a router/NAT, or create /etc/sigmond/allow-public-ip if "
           "this exposure is deliberate.")
    print(msg, file=sys.stderr)

    if enforce:
        subprocess.run(["logger", "-p", "daemon.crit", "-t",
                        "sigmond-netguard", msg], timeout=10)
        subprocess.run(["wall", msg], timeout=10)
        smd = "/usr/local/bin/smd"
        if Path(smd).exists():
            subprocess.run([smd, "stop"], timeout=600)
    return 1


if __name__ == "__main__":
    sys.exit(main())

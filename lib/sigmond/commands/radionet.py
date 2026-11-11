"""`smd radionet apply|status` -- the decoder VM's half of the site radio network.

The site radio network joins the decoder VMs of several Sigmond stations at one
site into one multicast segment, so a recorder on one station can join another
station's radiod streams.  Only joined streams cross: the hosts are linked by a
VXLAN tunnel with IGMP snooping (sigmond-radionet on the Proxmox host).

The host half -- vmbr9, the tunnel, the VM's second NIC -- is set up on the
Proxmox host by `sigmond-radionet setup`, which then runs `smd radionet apply`
in this VM through the guest agent.  This command does the VM half:

  1. the radio NIC (found by the MAC the host gave it) gets its address, its
     MTU and the 239.0.0.0/8 route, in 05-sigmond-radio.network;
  2. the management network matches its own NIC by MAC.  Images up to v3.90
     matched `Driver=virtio_net`, which also caught the radio NIC: both VMs in
     the IPv6-only radionet test (2026-11-11) put 10.99.0.2/30 on ens18 AND
     ens19;
  3. the 239.0.0.0/8 -> lo route goes, and sigmond-loopback-multicast stops
     putting it back at boot.  With both routes present the station's own
     recorders join on lo while radiod sends on the radio NIC;
  4. every radiod sends on the radio NIC with ttl 1 (KPH and KFS run so);
  5. the station restarts, radiod first, so radiod and its recorders take it.
     The restart runs as its own systemd unit (sigmond-radionet-restart), so
     this command returns at once: a whole-station restart outlasted the guest
     agent on a small host.

Verified by hand on a 6500 and a Beelink on an IPv6-only VLAN, 2026-11-11: a
join from one VM drew 11.95 Mb/s across the tunnel, and 0 kb/s before the join
and after the leave.
"""
from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
from pathlib import Path

from ..ui import err, info, ok, warn

NETWORK_DIR = Path('/etc/systemd/network')
RADIO_NETWORK = NETWORK_DIR / '05-sigmond-radio.network'
MGMT_NETWORK = NETWORK_DIR / '10-sigmond-mgmt.network'
RADIO_DIR = Path('/etc/radio')
SYSFS_NET = Path('/sys/class/net')
LOOPBACK_UNIT = 'sigmond-loopback-multicast.service'
MULTICAST = '239.0.0.0/8'
MGMT_ADDRESS = '10.99.0.2'
# `apply` configured everything, but the station restart reported a problem.
EXIT_RESTART = 3


# ─── pure helpers (tested) ────────────────────────────────────────────────────

def normalize_mac(mac: str) -> str:
    m = (mac or '').strip().lower()
    if not re.fullmatch(r'([0-9a-f]{2}:){5}[0-9a-f]{2}', m):
        raise ValueError(f'not a MAC address: {mac!r}')
    return m


def render_radio_network(mac: str, address: str, mtu: int) -> str:
    """05-sigmond-radio.network for the radio NIC.  Sorts before the
    management file, so the radio NIC never falls through to it."""
    iface = ipaddress.ip_interface(address)
    if iface.version != 4:
        raise ValueError('the radio network is IPv4 inside the tunnel')
    return ('# Site radio network: vmbr9 on the Proxmox host, joined to the other\n'
            '# hosts at this site by sigmond-radionet.  Written by `smd radionet apply`.\n'
            '[Match]\n'
            f'MACAddress={normalize_mac(mac)}\n'
            '\n[Link]\n'
            f'MTUBytes={int(mtu)}\n'
            '\n[Network]\n'
            f'Address={iface.with_prefixlen}\n'
            'IPv6AcceptRA=no\n'
            '\n[Route]\n'
            f'Destination={MULTICAST}\n'
            'Scope=link\n')


def mgmt_match_by_mac(text: str, mac: str) -> str:
    """The management network's [Match], keyed to its own NIC's MAC.  A
    `Driver=`, `Name=` or older `MACAddress=` line in [Match] is replaced; the
    rest of the file is kept as it is."""
    out, in_match, done = [], False, False
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith('['):
            in_match = (s == '[Match]')
        elif in_match and re.match(r'(Driver|Name|MACAddress)\s*=', s):
            if not done:
                out.append(f'MACAddress={normalize_mac(mac)}\n')
                done = True
            continue
        out.append(line)
    if not done:
        raise ValueError('no [Match] key to replace in the management network')
    return ''.join(out)


def set_global_keys(conf: str, values: dict, note: str) -> str:
    """radiod.conf with `key = value` set in [global], comments kept.  A key
    that is missing is added right after the [global] header."""
    lines = conf.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.strip() == '[global]'), None)
    if start is None:
        raise ValueError('no [global] section')
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].lstrip().startswith('[')), len(lines))
    todo = dict(values)
    for i in range(start + 1, end):
        m = re.match(r'(\s*)([A-Za-z_-]+)(\s*)=', lines[i])
        if m and m.group(2) in todo:
            k = m.group(2)
            lines[i] = f'{m.group(1)}{k}{m.group(3)}= {todo.pop(k)}   # {note}\n'
    for k, v in todo.items():
        lines.insert(start + 1, f'{k} = {v}   # {note}\n')
    return ''.join(lines)


def global_value(conf: str, key: str) -> str | None:
    in_global = False
    for line in conf.splitlines():
        s = line.strip()
        if s.startswith('['):
            in_global = (s == '[global]')
        elif in_global:
            m = re.match(rf'{re.escape(key)}\s*=\s*([^#\s]+)', s)
            if m:
                return m.group(1)
    return None


def iface_for_mac(mac: str, sysfs: Path = SYSFS_NET) -> str | None:
    mac = normalize_mac(mac)
    for dev in sorted(sysfs.iterdir()) if sysfs.is_dir() else []:
        try:
            if (dev / 'address').read_text().strip().lower() == mac:
                return dev.name
        except OSError:
            continue
    return None


# ─── system-touching ──────────────────────────────────────────────────────────

def _run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _iface_with(address: str) -> str | None:
    r = _run(['ip', '-4', '-o', 'addr', 'show'])
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) > 3 and f[3].split('/')[0] == address:
            return f[1]
    return None


def _write(path: Path, text: str) -> bool:
    """Write text, keeping a .pre-radionet copy of what was there first."""
    if path.exists():
        if path.read_text() == text:
            return False
        bak = path.with_name(path.name + '.pre-radionet')
        if not bak.exists():
            shutil.copy2(path, bak)
    path.write_text(text)
    return True


def apply(address: str, mac: str, mtu: int, restart: bool = True) -> int:
    radio = iface_for_mac(mac)
    if not radio:
        err(f'no NIC in this VM has the MAC {mac} -- add the radio NIC on the '
            'host first (sigmond-radionet setup does)')
        return 1
    mgmt = _iface_with(MGMT_ADDRESS)
    if mgmt == radio:
        mgmt = None              # the radio NIC caught the mgmt config itself
    if mgmt is None:
        # Find it the other way: the one other NIC on the host-only /30.
        # Only a virtio NIC can be it -- never a USB adapter on the passed-through
        # controller.
        for dev in sorted(SYSFS_NET.iterdir()):
            drv = dev / 'device' / 'driver'
            if dev.name != radio and drv.exists() and drv.resolve().name == 'virtio_net':
                mgmt = dev.name
                break
    info(f'radio NIC: {radio} ({normalize_mac(mac)}); management NIC: {mgmt}')

    # 1 + 2: the two network files.
    changed = _write(RADIO_NETWORK, render_radio_network(mac, address, mtu))
    if MGMT_NETWORK.exists() and mgmt:
        mgmt_mac = (SYSFS_NET / mgmt / 'address').read_text().strip()
        changed |= _write(MGMT_NETWORK, mgmt_match_by_mac(MGMT_NETWORK.read_text(), mgmt_mac))
    if changed:
        _run(['ip', 'addr', 'del', f'{MGMT_ADDRESS}/30', 'dev', radio])
        _run(['networkctl', 'reload'])
        _run(['networkctl', 'reconfigure', radio] + ([mgmt] if mgmt else []))
    ok(f'{radio}: {address}, MTU {mtu}, {MULTICAST} on the radio NIC')

    # 3: no loopback route, now or at the next boot.
    _run(['systemctl', 'disable', LOOPBACK_UNIT])
    _run(['ip', 'route', 'del', MULTICAST, 'dev', 'lo'])
    ok(f'{MULTICAST} no longer routed to lo ({LOOPBACK_UNIT} disabled)')

    # 4: every radiod on the radio NIC, ttl 1.
    confs = sorted(RADIO_DIR.glob('radiod@*.conf'))
    if not confs:
        warn('no radiod config in /etc/radio -- nothing to point at the radio network')
    for c in confs:
        new = set_global_keys(c.read_text(), {'ttl': '1', 'iface': radio},
                              'site radio network (smd radionet apply)')
        if _write(c, new):
            ok(f'{c.name}: ttl = 1, iface = {radio}')
        else:
            info(f'{c.name}: already ttl = 1, iface = {radio}')

    # 5: radiod first, then everything that reads it -- in the background.
    # ⛔ NOT INLINE.  The host runs this through the guest agent, and a full
    # station restart outlasted it on an i5-6500T (2026-11-11: load 25, the
    # agent stopped answering pings, setup reported a failure while the
    # restart was still going).  systemd runs the restart as its own unit;
    # this command returns as soon as it has started.
    if restart and confs:
        r = _run(['systemd-run', '--unit=sigmond-radionet-restart', '--collect',
                  '--description=smd restart for the site radio network',
                  'smd', 'restart'])
        if r.returncode != 0:
            warn('the radio network is configured, but the station restart could '
                 f'not be started ({r.stderr.strip()}) -- run `smd restart`')
            return EXIT_RESTART
        ok('station restart started (radiod first, then its recorders); follow it '
           'with `journalctl -fu sigmond-radionet-restart` or `smd status`')
    return 0

def status() -> int:
    if not RADIO_NETWORK.exists():
        info('radio network: not set up in this VM (sigmond-radionet setup on the host)')
        return 0
    text = RADIO_NETWORK.read_text()
    mac = re.search(r'^MACAddress=(\S+)', text, re.M)
    addr = re.search(r'^Address=(\S+)', text, re.M)
    radio = iface_for_mac(mac.group(1)) if mac else None
    if not radio:
        warn(f'radio network configured for {mac.group(1) if mac else "?"}, '
             'but no NIC has that MAC')
        return 1
    have = _run(['ip', '-4', '-o', 'addr', 'show', 'dev', radio]).stdout
    up = addr and addr.group(1).split('/')[0] in have
    (ok if up else warn)(f'radio NIC {radio}: {addr.group(1) if addr else "?"} '
                         f'{"up" if up else "NOT configured"}')
    routes = _run(['ip', 'route', 'show', MULTICAST]).stdout
    devs = sorted(set(re.findall(r'dev (\S+)', routes)))
    (ok if devs == [radio] else warn)(f'{MULTICAST} routed via: {", ".join(devs) or "nothing"}')
    if _iface_with(MGMT_ADDRESS) == radio:
        warn(f'{radio} also holds the management address {MGMT_ADDRESS}')
    for c in sorted(RADIO_DIR.glob('radiod@*.conf')):
        t = c.read_text()
        ttl, ifc = global_value(t, 'ttl'), global_value(t, 'iface')
        good = ttl == '1' and ifc == radio
        (ok if good else warn)(f'{c.name}: ttl = {ttl}, iface = {ifc}')
    return 0


def cmd_radionet(args) -> int:
    verb = getattr(args, 'radionet_command', None) or 'status'
    if verb == 'status':
        return status()
    if verb == 'apply':
        try:
            return apply(args.address, args.mac, args.mtu,
                         restart=not getattr(args, 'no_restart', False))
        except ValueError as e:
            err(str(e))
            return 2
    err(f'unknown verb: {verb}')
    return 2

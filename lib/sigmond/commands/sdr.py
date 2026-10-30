"""`smd admin sdr {list|identify}`: which RX888 is which radiod.

Reading /sys and /etc/radio is unprivileged, so `list` needs no root.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

from sigmond import sdr_map
from sigmond.ui import heading

# Module attributes so tests can point them elsewhere.
SYSFS_ROOT = Path('/sys/bus/usb/devices')
CONFDIR = Path('/etc/radio')


def load_coord():
    try:
        from sigmond.coordination import load_coordination
        return load_coordination()
    except Exception:
        return None


def unit_state(inst: str) -> str:
    """`systemctl is-active radiod@<inst>`, `unknown` when it cannot run."""
    try:
        r = subprocess.run(['systemctl', 'is-active', f'radiod@{inst}'],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    return r.stdout.strip() or 'unknown'


def _antenna(inst: str) -> str:
    try:
        return sdr_map.conf_description(
            (CONFDIR / f'radiod@{inst}.conf').read_text())
    except OSError:
        return ''


def _table(headers, rows) -> list[str]:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    fmt = '  '.join(f'{{:<{w}}}' for w in widths)
    return [fmt.format(*headers).rstrip()] + \
           [fmt.format(*map(str, r)).rstrip() for r in rows]


def cmd_sdr_list(args) -> int:
    cards = sdr_map.present_cards(SYSFS_ROOT)
    instances = sdr_map.instance_serials(CONFDIR, load_coord())
    rep = sdr_map.report(cards, instances)
    opened = sdr_map.serial_to_instance(instances)
    rc = 1 if rep.problems else 0

    if getattr(args, 'json', False):
        print(json.dumps({
            'cards': [dict(dataclasses.asdict(c),
                           opened_by=opened.get(c.serial, ''))
                      for c in cards],
            'instances': instances,
            'problems': rep.problems,
        }, indent=2))
        return rc

    on_bus = {c.serial for c in cards if c.programmed}
    heading('RX888 cards on the USB bus')
    rows = [(c.serial if c.programmed else '(bootloader)', c.devpath,
             c.speed or '-', opened.get(c.serial, '') or '-') for c in cards]
    for line in (_table(['SERIAL', 'USB', 'SPEED', 'OPENED BY'], rows)
                 if rows else ['(none)']):
        print(line)
    print()
    heading('radiod instances')
    rows = [(inst, unit_state(inst), s or '-',
             ('yes' if s in on_bus else 'no') if s else '-', _antenna(inst))
            for inst, s in sorted(instances.items())]
    for line in (_table(['INSTANCE', 'STATE', 'SERIAL', 'ON BUS', 'ANTENNA'], rows)
                 if rows else ['(none)']):
        print(line)
    print()
    if rep.problems:
        print('Needs attention:')
        for p in rep.problems:
            print(f'- {p}')
    else:
        print('Every local radiod names its own RX888 and every RX888 is on the bus.')
    return rc


def cmd_sdr_identify(args) -> int:
    print('not implemented')
    return 2

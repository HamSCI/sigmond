"""Order radiod consumers behind a LAN that can actually carry multicast.

⛔ The failure this prevents.  A station boots, every unit reports active, and
it records nothing.  Our recorders order themselves after
``network-online.target``, which systemd can satisfy while loopback is still
the only multicast-capable interface — the recorder then joins a group that
reaches nobody, sees no packets, and reports perfect health.  Nothing in the
logs says "wrong interface".  Two of these boxes ship to McMurdo Station,
where that costs a season and nobody can reach the console.

ka9q-radio answers this upstream.  Since 2026.09 it ships ``wait-for-lan`` and
a ``lan.target`` that completes only once a real interface holds a lease, and
radiod's own unit already carries ``Wants=`` and ``After=lan.target``.  Our
consumers did not follow, and ``Wants=`` on radiod's side creates NO ordering
— it pulls the units in, it does not hold them back.  So a recorder could
still start ahead of the LAN.

This module writes one drop-in per consumer unit:

    /etc/systemd/system/<unit>.d/10-smd-lan-order.conf
        [Unit]
        After=lan.target

⚡ ``After=`` names a unit rather than requiring it, so on a host whose radiod
predates ``lan.target`` the line is inert — systemd orders against nothing and
starts the unit exactly as before.  That keeps a mixed fleet safe, which
matters while stations upgrade at different times.

⚠ Deliberately NOT ``Wants=lan.target`` here.  radiod already pulls the target
in, and a consumer that pulled it in on its own would start ``wait-for-lan``
on a decode-only host that has no radiod and no reason to wait.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

DROP_IN_NAME = '10-smd-lan-order.conf'
SYSTEMD_DIR = Path('/etc/systemd/system')

CONTENT = (
    '# Written by sigmond (lib/sigmond/lan_order.py).  Do not edit.\n'
    '#\n'
    '# Hold this consumer until a LAN that can carry multicast is up.\n'
    '# network-online.target can come true while loopback is the only\n'
    '# multicast-capable interface, which starts a recorder that joins a\n'
    '# group reaching nobody and then reports perfect health.\n'
    '#\n'
    '# After= only: radiod already Wants=lan.target, and a consumer that\n'
    '# pulled it in itself would run wait-for-lan on a decode-only host.\n'
    '# On a radiod older than lan.target this line is inert.\n'
    '[Unit]\n'
    'After=lan.target\n'
)


def drop_in_path(unit: str, root: Path = SYSTEMD_DIR) -> Path:
    """Where this unit's ordering drop-in lives.

    For a templated instance (``psk-recorder@AC0G-B4.service``) systemd reads
    both ``unit@inst.service.d/`` and ``unit@.service.d/``.  We write the
    INSTANCE path, so removing one station's instance takes its drop-in with
    it and leaves the others alone.
    """
    return root / f'{unit}.d' / DROP_IN_NAME


def ensure_lan_ordering(units: Iterable[str], *, dry_run: bool = False,
                        root: Path = SYSTEMD_DIR) -> List[str]:
    """Write the drop-in for each unit.  Returns a message per unit CHANGED.

    Idempotent: a unit whose drop-in already holds this exact content is
    skipped and reported nowhere, so a re-install is silent rather than noisy.
    The caller runs ``systemctl daemon-reload`` when the returned list is
    non-empty.
    """
    changed: List[str] = []
    for unit in units:
        if not unit or not unit.endswith(('.service', '.target', '.timer')):
            continue
        path = drop_in_path(unit, root)
        try:
            if path.exists() and path.read_text() == CONTENT:
                continue
        except OSError:
            pass
        if dry_run:
            changed.append(f'  would order {unit} after lan.target')
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(CONTENT)
        except OSError as exc:
            changed.append(f'  warning: {unit}: cannot write drop-in: {exc}')
            continue
        changed.append(f'  ordered {unit} after lan.target')
    return changed

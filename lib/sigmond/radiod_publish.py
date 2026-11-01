"""Is each radiod on this host published on the LAN?

A radiod-only host exists to be consumed by a decoder host elsewhere on the
LAN.  It is published when each configured radiod is running and its status
name resolves from the host itself (`getent hosts`, which goes through NSS and
so through mDNS where avahi is set up).  This is the check
`smd bringup radiod-only` ends on.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from sigmond.radiod_rename import status_of

RADIO_CONF_DIR = Path('/etc/radio')

# diagnose() reasons; '' means published.
NO_STATUS = 'no [global] status'
UNIT_DOWN = 'unit not active'
UNRESOLVED = 'status name does not resolve'


def _ok(run, argv) -> bool:
    try:
        r = run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(r, 'returncode', 1) == 0


def diagnose(conf_dir: Path = RADIO_CONF_DIR,
             run=subprocess.run) -> dict[str, str]:
    """{radiod id: why it is not published, '' when it is} for every
    radiod@<id>.conf in `conf_dir`.

    The firmware bootstrap instance (radiod@bootstrap-fw*) is never published
    and is skipped.
    """
    conf_dir = Path(conf_dir)
    if not conf_dir.is_dir():
        return {}
    result: dict[str, str] = {}
    for conf in sorted(conf_dir.glob('radiod@*.conf')):
        rid = conf.name[len('radiod@'):-len('.conf')]
        if rid.startswith('bootstrap-fw'):
            continue
        try:
            status = status_of(conf.read_text())
        except (OSError, UnicodeDecodeError):
            status = ''
        if not status:
            result[rid] = NO_STATUS
        elif not _ok(run, ['systemctl', 'is-active', '--quiet',
                           f'radiod@{rid}.service']):
            result[rid] = UNIT_DOWN
        elif not _ok(run, ['getent', 'hosts', status]):
            result[rid] = UNRESOLVED
        else:
            result[rid] = ''
    return result


def published(conf_dir: Path = RADIO_CONF_DIR,
              run=subprocess.run) -> dict[str, bool]:
    """{radiod id: published} for every radiod@<id>.conf in `conf_dir`."""
    return {rid: not why for rid, why in diagnose(conf_dir, run).items()}

"""`smd remote on|off|status` -- the operator's switch for remote access.

On an appliance station the tunnel runs on the Proxmox host, and the host
alone turns it on or off (tasks/plan-remote-access.md).  This verb asks.  It
opens ssh to the host over the private link between the two machines, with
the one key the host's wizard made in this VM, and prints the host's answer.
On the host that key can run exactly one script, sigmond-rac-switch, with one
of three words.

`smd remote motd` prints one line for the login banner.  It reads the last
answer the host gave and never asks the host, so a login stays fast.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..ui import err, info

KEY = Path('/etc/sigmond/rac-switch.key')
KNOWN_HOSTS = Path('/etc/sigmond/rac-switch.known_hosts')
CACHE = Path('/var/lib/sigmond/remote-access.json')
APPLIANCE_MARK = Path('/etc/sigmond-appliance/version')
HOST_ACCOUNT = 'sigmond-remote'

WORDS = ('on', 'off', 'status')
# The switch script prints exactly one of these as a line of its answer.
STATE_LINES = {
    'Remote access: on': 'on',
    'Remote access: off': 'off',
    'Remote access: wanted on, not up': 'down',
}
# `on` may register with a gateway first and then waits for the channels.
TIMEOUT_S = {'on': 600, 'off': 120, 'status': 60}

EXIT_NOT_PAIRED = 5
EXIT_UNREACHABLE = 6


def paired_host(known_hosts: Path | None = None) -> str | None:
    """The one host the pairing key may talk to.

    The host wrote a single line into the known-hosts file: its address on
    the private link, then its sshd key.  Returns the address, or None when
    the file is missing or holds anything but an address in that place.
    """
    try:
        text = (known_hosts or KNOWN_HOSTS).read_text()
    except OSError:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and re.fullmatch(r'[0-9A-Fa-f:.]{3,45}', parts[0]):
            return parts[0]
    return None


def ssh_argv(host: str, word: str) -> list:
    return ['ssh', '-T', '-i', str(KEY),
            '-o', 'BatchMode=yes',
            '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=yes',
            '-o', f'UserKnownHostsFile={KNOWN_HOSTS}',
            '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'ConnectTimeout=8',
            '-o', 'LogLevel=ERROR',
            f'{HOST_ACCOUNT}@{host}', word]


def state_of(answer: str) -> str | None:
    """'on', 'off' or 'down' from the host's answer; None when it names none."""
    for line in answer.splitlines():
        if line in STATE_LINES:
            return STATE_LINES[line]
    return None


def _remember(state: str, now: datetime | None = None) -> None:
    """Keep the answer for the login banner.  A failure here costs a banner
    line and nothing else."""
    now = now or datetime.now(timezone.utc)
    doc = {'state': state, 'checked_at': now.strftime('%Y-%m-%dT%H:%M:%SZ')}
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(CACHE.parent), prefix='.remote-access.')
        with os.fdopen(fd, 'w') as fh:
            fh.write(json.dumps(doc) + '\n')
        os.chmod(tmp, 0o644)
        os.replace(tmp, CACHE)
    except OSError:
        pass


def _not_paired() -> int:
    if APPLIANCE_MARK.exists():
        err('this VM holds no pairing with its Proxmox host, so it cannot ask '
            'the host to switch remote access')
        info('A station installed before v3.71 pairs once.  On the Proxmox host '
             '(ssh root@<host address>):')
        info('    sigmond-setup --pair-vm')
        info('Until then the host itself works the switch:  sigmond-setup --rac-off  '
             'and  sigmond-setup --rac-on')
    else:
        err('smd remote switches the tunnel of an appliance station through its '
            'Proxmox host, and this machine has none')
        info('A station without a Proxmox host runs its tunnel here:  smd admin rac status')
    return EXIT_NOT_PAIRED


def _unreachable(host: str, stderr: str) -> int:
    last = (stderr or '').strip().splitlines()[-1:] or ['ssh gave no reason']
    low = (stderr or '').lower()
    if 'host key verification failed' in low or 'identification has changed' in low:
        err(f'the Proxmox host at {host} shows an sshd key this VM was not paired with')
        info('The host pairs again by itself within minutes of a key change; try again shortly.')
        info('If it stays so, on the Proxmox host:  sigmond-setup --pair-vm')
    elif 'permission denied' in low:
        err(f'the Proxmox host at {host} did not accept this VM\'s pairing key')
        info('On the Proxmox host:  sigmond-setup --pair-vm')
    else:
        err(f'could not reach the Proxmox host at {host}: {last[0]}')
        info('The private link between this VM and its host carries this request; '
             'the station\'s uplink plays no part in it.')
    return EXIT_UNREACHABLE


def cmd_motd(now: datetime | None = None) -> int:
    """One line for the login banner; nothing on a VM that holds no pairing."""
    if not KEY.exists():
        return 0
    try:
        doc = json.loads(CACHE.read_text())
        state, when = doc['state'], str(doc['checked_at'])
    except (OSError, ValueError, KeyError, TypeError):
        print('Remote access: state not read yet.  Check it:  smd remote status')
        return 0
    as_of = f' (as the host reported {when[:16].replace("T", " ")} UTC)'
    if state == 'on':
        print(f'Remote access: on{as_of}.  Turn it off:  smd remote off')
    elif state == 'off':
        print(f'Remote access: OFF{as_of}.  Turn it on:  smd remote on')
    else:
        print(f'Remote access: wanted on, NOT UP{as_of}.  Details:  smd remote status')
    return 0


def cmd_remote(args, *, run=subprocess.run) -> int:
    word = getattr(args, 'remote_command', None) or 'status'
    if word == 'motd':
        return cmd_motd()
    if word not in WORDS:
        err('smd remote takes one word: on, off or status')
        return 2
    host = paired_host() if KEY.exists() else None
    if host is None:
        return _not_paired()
    if word == 'on':
        info('asking the Proxmox host; this can take a minute while the tunnel comes up')
    try:
        r = run(ssh_argv(host, word), capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=TIMEOUT_S[word])
    except subprocess.TimeoutExpired:
        err(f'the Proxmox host at {host} gave no answer within {TIMEOUT_S[word]} s')
        info('Ask again for the state:  smd remote status')
        return EXIT_UNREACHABLE
    except OSError as exc:
        err(f'could not run ssh: {exc}')
        return EXIT_UNREACHABLE
    answer = r.stdout or ''
    if answer:
        print(answer, end='' if answer.endswith('\n') else '\n')
    state = state_of(answer)
    if state is None:
        if r.returncode == 255:
            return _unreachable(host, r.stderr)
        tail = (r.stderr or '').strip().splitlines()[-1:]
        err(f'the host\'s switch gave no state (exit {r.returncode})'
            + (f': {tail[0]}' if tail else ''))
        return r.returncode or 1
    _remember(state)
    return r.returncode

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

import ipaddress
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..ui import err, info, warn

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
# With the wish off and the tunnel service running all the same, the switch
# adds a line that begins so (after two spaces).  The login banner carries it.
TUNNEL_RUNS_NOTE = 'NOTE: the tunnel service runs all the same.'
# `on` may register with a gateway first and then waits for the channels.
TIMEOUT_S = {'on': 600, 'off': 120, 'status': 60}

EXIT_NOT_PAIRED = 5
EXIT_UNREACHABLE = 6

# The banner trusts an answer on file for this long, then says how old it is.
FRESH_FOR = timedelta(hours=7)
STAMP_FORMAT = '%Y-%m-%dT%H:%M:%SZ'
# What the shell on the host prints when sudo fails: "sudo: ..." itself, or the
# same behind the shell's own prefix ("sh: 1: sudo: not found").
SUDO_FAILURE = re.compile(r'(?:.*: )?sudo: ')


def paired_host(known_hosts: Path | None = None) -> str | None:
    """The one host the pairing key may talk to.

    The host wrote a single line into the known-hosts file: its address on
    the private link, then its sshd key.  Returns the address, or None when
    the file cannot be read or its first line holds anything but an address
    and a key in that order.
    """
    try:
        text = (known_hosts or KNOWN_HOSTS).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.splitlines()
    parts = lines[0].split() if lines else []
    if len(parts) < 3 or not parts[1].startswith(('ssh-', 'ecdsa-')):
        return None
    try:
        ipaddress.ip_address(parts[0])
    except ValueError:
        return None
    return parts[0]


def ssh_argv(host: str, word: str) -> list:
    # -F /dev/null: no ssh config file, user or system, adds an option to this call.
    return ['ssh', '-T', '-F', '/dev/null', '-i', str(KEY),
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


def tunnel_still_runs(state: str, answer: str) -> bool:
    """True when the host answered off and added its NOTE that the tunnel
    service runs all the same.  The NOTE counts only as a line of its own."""
    return state == 'off' and any(
        line.startswith('  ' + TUNNEL_RUNS_NOTE) for line in answer.splitlines())


def _remember(state: str, answer: str = '', now: datetime | None = None) -> None:
    """Keep the answer for the login banner.  A failure here costs a banner
    line and one warning, and the verb's exit status stays the host's.

    The file holds the state and its time.  `tunnel_runs` joins them, as
    true, only when the wish reads off and the tunnel service still runs;
    a file without the key reads as false."""
    now = now or datetime.now(timezone.utc)
    doc = {'state': state, 'checked_at': now.strftime(STAMP_FORMAT)}
    if tunnel_still_runs(state, answer):
        doc['tunnel_runs'] = True
    tmp = None
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(CACHE.parent), prefix='.remote-access.')
        with os.fdopen(fd, 'w') as fh:
            fh.write(json.dumps(doc) + '\n')
        os.chmod(tmp, 0o644)
        os.replace(tmp, CACHE)
    except OSError as exc:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        warn(f'could not keep the host\'s answer for the login banner ({exc}); '
             'the banner keeps its older answer')


def _not_paired() -> int:
    if APPLIANCE_MARK.exists():
        err('this VM holds no pairing with its Proxmox host, so it cannot ask '
            'the host to switch remote access')
        info('The Proxmox host works the switch itself, whatever its version.  There, run:')
        info('    sigmond-setup --rac-off')
        info('    sigmond-setup --rac-on')
        info('To pair this VM, run on the Proxmox host:  sigmond-setup --pair-vm')
        info('That command needs the host at v3.71 or later.')
        info('An older host answers "already configured" and pairs nothing.')
        info('Such a host needs its alignment to v3.71 first.')
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
        info('A host at v3.71 pairs this VM again when its sshd key file changes.')
        info('If this message stays, on the Proxmox host:  sigmond-setup --pair-vm')
        # The pairing pins the key in the host's key file.  After that file
        # changed, the two differ until sshd restarts, and pairing again
        # cannot help: its own proof meets the earlier key and fails.
        info('If it stays after that, restart sshd on the Proxmox host, then pair again:')
        info('    systemctl restart ssh')
        info('    sigmond-setup --pair-vm')
        info('(The pairing reads the host\'s key file; a running sshd serves the key it '
             'loaded at its start.)')
    elif 'permission denied' in low:
        err(f'the Proxmox host at {host} did not accept this VM\'s pairing key')
        info('On the Proxmox host:  sigmond-setup --pair-vm')
    else:
        err(f'could not reach the Proxmox host at {host}: {last[0]}')
        info('The private link between this VM and its host carries this request; '
             'the station\'s uplink plays no part in it.')
        info('Check that the Proxmox host runs, then ask again:  smd remote status')
    return EXIT_UNREACHABLE


def _stamp(text) -> datetime | None:
    """The time a cache file gives, or None when it is no `%Y-%m-%dT%H:%M:%SZ`."""
    if not isinstance(text, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', text):
        return None
    try:
        return datetime.strptime(text, STAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def cmd_motd(now: datetime | None = None) -> int:
    """One line for the login banner; nothing on a VM that holds no pairing.

    The file it reads can be written by any member of group `sigmond`, so the
    line carries only a state and a time it can read, and one flag that must
    read as a JSON true: off, with the tunnel service still running.  An
    answer more than FRESH_FOR old, or dated ahead of the clock, says so.
    """
    if not KEY.exists():
        return 0
    try:
        doc = json.loads(CACHE.read_text(encoding='utf-8'))
        state, stamp = doc['state'], _stamp(doc['checked_at'])
        # Only a JSON true counts; a file without the key reads as false.
        runs = doc.get('tunnel_runs') is True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        state, stamp, runs = None, None, False
    if state not in ('on', 'off', 'down') or stamp is None:
        print('Remote access: state not read yet.  Check it:  smd remote status')
        return 0
    now = now or datetime.now(timezone.utc)
    age = now - stamp
    when = stamp.strftime('%Y-%m-%d %H:%M')
    word = {'on': 'on', 'off': 'OFF', 'down': 'wanted on, NOT UP'}[state]
    if age < timedelta(0):
        print(f'Remote access: the last answer ({word}) is dated {when} UTC, '
              f'in the future by this VM\'s clock.  Ask again:  smd remote status')
        return 0
    if age > FRESH_FOR:
        hours = int(age.total_seconds() // 3600)
        print(f'Remote access: the last answer is {hours} hours old ({word}, '
              f'as the host reported {when} UTC).  Ask again:  smd remote status')
        return 0
    as_of = f' (as the host reported {when} UTC)'
    if state == 'on':
        print(f'Remote access: on{as_of}.  Turn it off:  smd remote off')
    elif state == 'off' and runs:
        print(f'Remote access: OFF, but the tunnel service still runs{as_of}.  '
              f'Close it:  smd remote off')
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
        r = run(ssh_argv(host, word), capture_output=True, text=True, errors='replace',
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
        if tail and SUDO_FAILURE.match(tail[0]):
            err(f'the host\'s pairing account could not run the switch '
                f'(exit {r.returncode}): {tail[0]}')
            info('On the Proxmox host:  sigmond-setup --pair-vm')
        else:
            err(f'the host\'s switch gave no state (exit {r.returncode})'
                + (f': {tail[0]}' if tail else ''))
        return r.returncode or 1
    _remember(state, answer)
    if r.returncode == 255:
        err('the connection to the Proxmox host ended before its switch finished')
        return EXIT_UNREACHABLE
    return r.returncode

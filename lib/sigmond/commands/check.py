"""`smd check` -- one command that says whether this station works (v3.72 #14).

tasks/spec-smd-check-v372.md.  Each check reads data that already exists and
returns one Line: a mark, a label and a sentence.  The verdict takes the worst
mark.  Nothing here changes the station, and nothing here calls the timing
judge, `smd doctor` or a watcher.

Every probe of the outside world (files, systemd, the journal, HTTP, the
clock) comes in through an Env, so the tests build a station in a temporary
directory and inject the clock.
"""
from __future__ import annotations

import json
import os
import pwd
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Tuple

# ASCII on purpose: the Proxmox host's console font (Lat15, Fixed 8x16) has
# no glyph for a check mark or a dotted circle, and the installer reads this
# on exactly that console (Michael, 2026-10-14).  Everything smd check prints
# stays ASCII; render_line enforces it on the text.
OK, WAIT, FAIL, ABSENT = 'ok', 'wait', 'FAIL', '--'
MARKS = (OK, WAIT, FAIL, ABSENT)
EXIT_CODES = {'PASS': 0, 'WAIT': 2, 'FAIL': 1}
MARK_WIDTH = 4
LABEL_WIDTH = 15
TEXT_COLUMN = 1 + MARK_WIDTH + 2 + LABEL_WIDTH     # where a line's text starts
# Every command a line asks someone to type follows one of these two words.
# `sigmond-check` on the host writes `sigmond-vm ` after them, so the command
# reaches the VM from the host's console.
FIX = 'Fix: '
RUN = 'run: '


@dataclass(frozen=True)
class Line:
    mark: str
    label: str
    text: str


def verdict(lines: Sequence[Line]) -> str:
    marks = {ln.mark for ln in lines}
    if FAIL in marks:
        return 'FAIL'
    if WAIT in marks:
        return 'WAIT'
    return 'PASS'


def summary(lines: Sequence[Line]) -> str:
    failing = sum(1 for ln in lines if ln.mark == FAIL)
    waiting = sum(1 for ln in lines if ln.mark == WAIT)
    parts = []
    if failing:
        parts.append(f'{failing} failing')
    if waiting:
        parts.append(f'{waiting} waiting')
    word = verdict(lines)
    return f'{word}  ({", ".join(parts)})' if parts else word


def _ascii(text: str) -> str:
    """Text as the console can show it: any other character becomes '?'
    (an exception's message can carry anything)."""
    return text.encode('ascii', 'replace').decode('ascii')


def render_line(line: Line) -> str:
    """' ok    PSK            214 FT8 ...'.  A fix moves to a line of its
    own, under the text, so the command stands alone where a person reads it."""
    label = (line.label.ljust(LABEL_WIDTH) if len(line.label) < LABEL_WIDTH
             else line.label + '  ')
    head, sep, fix = _ascii(line.text).partition('  ' + FIX)
    out = f' {line.mark.ljust(MARK_WIDTH)}  {label}{head}'.rstrip()
    if sep:
        out += '\n' + ' ' * TEXT_COLUMN + FIX + fix
    return out


def _user() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return str(os.geteuid())


class Unreadable(Exception):
    """A file or the journal refused us, and `sudo -n` did too."""

    def __init__(self, what):
        super().__init__(f'cannot read {what} as {_user()}')


@dataclass
class Env:
    """What the checks may look at.  default_env() fills it with the real
    probes; the tests fill it with fakes.  The comment names the task of
    tasks/plan-smd-check-v372.md that first fills each field."""
    now: float = field(default_factory=time.time)                      # Task 1
    installed: Callable[[str], bool] = None                            # Task 1
    service: Callable[[str], Any] = None                               # Task 2
    reader: Any = None                                                 # Task 2
    journal: Callable[[Sequence[str], int], List[str]] = None          # Task 2
    exists: Callable[[str], bool] = os.path.exists                     # Task 4
    disk: Callable[[str], Tuple[int, int]] = None                      # Task 5
    timestd_config: Callable[[], Optional[dict]] = None                # Task 5
    coordination: Callable[[], Any] = None                             # Task 6
    coordination_age_s: Callable[[], Optional[float]] = None           # Task 6
    backlog: Callable[[], dict] = None                                 # Task 6
    http: Callable[[str], Tuple[Optional[int], str]] = None            # Task 7


def _installed(name: str) -> bool:
    from ..component_state import compute_state
    return compute_state(name).installed


def default_env() -> Env:
    return Env(installed=_installed, service=service_state, reader=Reader(),
               journal=read_journal)


def guarded(label: str, fn: Callable[[Env], Optional[Line]], env: Env) -> Optional[Line]:
    """Run one check.  An exception becomes a FAIL line with its text: never a
    crash, never a pass."""
    try:
        return fn(env)
    except Unreadable as exc:
        return Line(FAIL, label, str(exc))
    except Exception as exc:  # noqa: BLE001 -- the whole point
        return Line(FAIL, label, f'the check itself failed: {type(exc).__name__}: {exc}')


def run_checks(env: Env, checks=None) -> List[Line]:
    out = []
    for label, fn in (CHECKS if checks is None else checks):
        line = guarded(label, fn, env)
        if line is not None:
            out.append(line)
    return out


def cmd_check(args, env: Optional[Env] = None, checks=None) -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass
    lines = run_checks(env or default_env(), checks)
    word = verdict(lines)
    if getattr(args, 'json', False):
        print(json.dumps({'lines': [asdict(ln) for ln in lines], 'verdict': word,
                          'exit': EXIT_CODES[word]}))
    else:
        for ln in lines:
            print(render_line(ln))
        if not getattr(args, 'no_verdict', False):
            print(summary(lines))
    return EXIT_CODES[word]


# ---------------------------------------------------------------------------
# Probes: files with a sudo fallback, systemd, the journal
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Service:
    state: str                  # active | activating | failed | inactive | absent
    age_s: Optional[float]      # since the oldest active unit started; None if unknown
    enabled: bool = False
    restarting: bool = False    # state is failed because systemd keeps restarting it


def service_state(pattern: str, run=subprocess.run, monotonic=time.monotonic) -> Service:
    """The state of the units matching `pattern` (a glob such as
    `psk-recorder@*.service`, or one unit name).  The age comes from
    ActiveEnterTimestampMonotonic, so a host clock step cannot fake it."""
    r = run(['systemctl', 'list-units', '--all', '--plain', '--no-legend', '--no-pager',
             '--type=service', pattern],
            capture_output=True, text=True, errors='replace', timeout=15, check=False)
    units = {}
    for ln in (r.stdout or '').splitlines():
        parts = ln.split()
        if len(parts) >= 3 and parts[0].endswith('.service'):
            units[parts[0]] = parts[2]
    if not units and '*' not in pattern:
        units = {pattern: None}
    if not units:
        return Service('absent', None, False)
    show = run(['systemctl', 'show', '-p', 'Id', '-p', 'ActiveState', '-p', 'LoadState',
                '-p', 'UnitFileState', '-p', 'SubState', '-p', 'ActiveEnterTimestampMonotonic',
                '-p', 'InactiveExitTimestampMonotonic', *sorted(units)],
               capture_output=True, text=True, errors='replace', timeout=15, check=False)
    blocks, cur = [], {}
    for ln in (show.stdout or '').splitlines() + ['']:
        if not ln.strip():
            if cur:
                blocks.append(cur)
            cur = {}
            continue
        key, _, val = ln.partition('=')
        cur[key] = val.strip()
    blocks = [b for b in blocks if b.get('LoadState') not in ('not-found', None)]
    if not blocks:
        return Service('absent', None, False)
    enabled = any(b.get('UnitFileState') in ('enabled', 'enabled-runtime', 'static', 'indirect')
                  for b in blocks)

    def stamp(b, key):
        try:
            us = int(b.get(key) or 0)
        except ValueError:
            us = 0
        return us / 1e6 if us > 0 else None

    restarting = [b for b in blocks if b.get('SubState') == 'auto-restart']
    if restarting or any(b.get('ActiveState') == 'failed' for b in blocks):
        return Service('failed', None, enabled, bool(restarting))
    # The youngest instance sets the age, so a fresh restart still reads young.
    for state, key in (('active', 'ActiveEnterTimestampMonotonic'),
                       ('activating', 'InactiveExitTimestampMonotonic')):
        mine = [b for b in blocks if b.get('ActiveState') == state]
        if mine:
            starts = [t for t in (stamp(b, key) for b in mine) if t is not None]
            return Service(state, (monotonic() - max(starts)) if starts else None, enabled)
    return Service('inactive', None, enabled)


class Reader:
    """Reads files as the user who runs smd, and through `sudo -n` once when
    the file refuses that user (as `_file_size` in bin/smd does)."""

    def __init__(self, run=subprocess.run, euid=os.geteuid):
        self._run = run
        self._euid = euid

    def _sudo(self, argv) -> Optional[str]:
        if self._euid() == 0:
            return None
        try:
            r = self._run(['sudo', '-n', *argv], capture_output=True, text=True,
                          errors='replace', timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    def tail(self, path, nbytes: int) -> Optional[str]:
        """The last `nbytes` of `path` as text, or None when it does not exist."""
        try:
            with open(path, 'rb') as fh:
                size = os.fstat(fh.fileno()).st_size
                fh.seek(max(0, size - nbytes))
                return fh.read().decode('utf-8', 'replace')
        except FileNotFoundError:
            return None
        except PermissionError:
            out = self._sudo(['tail', '-c', str(nbytes), str(path)])
            if out is None:
                raise Unreadable(path)
            return out

    def listdir(self, path) -> Optional[List[str]]:
        try:
            return sorted(os.listdir(path))
        except FileNotFoundError:
            return None
        except PermissionError:
            out = self._sudo(['find', str(path), '-mindepth', '1', '-maxdepth', '1',
                              '-printf', '%f\\n'])
            if out is None:
                raise Unreadable(path)
            return sorted(x for x in out.splitlines() if x)

    def newest_mtime(self, path) -> Optional[float]:
        """The newest modification time of a plain file directly in `path`,
        or None when `path` is absent or holds no file."""
        try:
            best = None
            with os.scandir(path) as it:
                for entry in it:
                    if entry.is_file(follow_symlinks=False):
                        m = entry.stat(follow_symlinks=False).st_mtime
                        best = m if best is None else max(best, m)
            return best
        except FileNotFoundError:
            return None
        except PermissionError:
            out = self._sudo(['find', str(path), '-maxdepth', '1', '-type', 'f',
                              '-printf', '%T@\\n'])
            if out is None:
                raise Unreadable(path)
            times = [float(x) for x in out.split()]
            return max(times) if times else None


JOURNAL_GROUPS = ('systemd-journal', 'adm', 'wheel')


def _my_groups() -> set:
    import grp
    names = set()
    for gid in {os.getgid(), *os.getgroups()}:
        try:
            names.add(grp.getgrgid(gid).gr_name)
        except KeyError:
            pass
    return names


def read_journal(units: Sequence[str], minutes: int, *, run=subprocess.run,
                 euid=os.geteuid, groups=_my_groups) -> List[str]:
    """The last `minutes` of the journal of `units`, message text only.  A
    user outside the journal's groups sees only their own messages and no
    error, so such a user asks `sudo -n` from the start."""
    argv = ['journalctl', '--no-pager', '-q', '-o', 'cat', '--since', f'{minutes} min ago']
    for unit in units:
        argv += ['-u', unit]
    if euid() != 0 and not (set(JOURNAL_GROUPS) & groups()):
        argv = ['sudo', '-n', *argv]
    try:
        r = run(argv, capture_output=True, text=True, errors='replace', timeout=20,
                check=False, env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.SubprocessError):
        raise Unreadable('the journal')
    if r.returncode != 0:
        # journalctl refuses a -u glob that matches no unit that ever logged.
        if 'No data available' in (r.stderr or ''):
            return []
        raise Unreadable('the journal')
    return (r.stdout or '').splitlines()


def service_problem(label: str, svc: Service, unit: str) -> Optional[Line]:
    """A FAIL line when the service runs not at all, else None."""
    if svc.state in ('active', 'activating'):
        return None
    if svc.state == 'absent':
        return Line(FAIL, label, f'{unit} is installed but has no service.  Tell the fleet admin.')
    if svc.restarting:
        return Line(FAIL, label, f'{unit} is failed: it keeps restarting.  {FIX}smd start {unit}')
    return Line(FAIL, label, f'{unit} is {svc.state}.  {FIX}smd start {unit}')


def is_young(svc: Service, window_s: float) -> bool:
    """True while a service has run for less than `window_s`.  A start
    of unknown age counts as young; an unknown age otherwise does not, so it can never hide a fault."""
    if svc.state == 'activating' and svc.age_s is None:
        return True
    return svc.age_s is not None and svc.age_s < window_s


def minutes(seconds: float) -> int:
    return max(1, int(round(seconds / 60.0)))


# ---------------------------------------------------------------------------
# The lines, in the order the installer reads them (spec §2).  Each entry is
# (label, function); a function returns a Line, or None for a line that does
# not apply here at all.  Later tasks add their entries in place.
# ---------------------------------------------------------------------------

CHECKS: List[Tuple[str, Callable[[Env], Optional[Line]]]] = []

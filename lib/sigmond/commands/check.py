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
    return Env(installed=_installed)


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
# The lines, in the order the installer reads them (spec §2).  Each entry is
# (label, function); a function returns a Line, or None for a line that does
# not apply here at all.  Later tasks add their entries in place.
# ---------------------------------------------------------------------------

CHECKS: List[Tuple[str, Callable[[Env], Optional[Line]]]] = []

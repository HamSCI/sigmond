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
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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
    reader = Reader()
    return Env(installed=_installed, service=service_state, reader=reader,
               journal=read_journal, disk=_disk,
               timestd_config=lambda: read_timestd_config(reader),
               coordination=_coordination, coordination_age_s=_coordination_age_s,
               backlog=read_backlog)


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
    age_s: Optional[float]      # since the youngest active unit started; None if unknown
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
    """True while a service has run, or has been starting, for less than
    `window_s`.  An unknown age never counts as young, so it can never hide
    a fault."""
    return svc.age_s is not None and svc.age_s < window_s


def minutes(seconds: float) -> int:
    return max(1, int(round(seconds / 60.0)))


# ---------------------------------------------------------------------------
# Science: PSK, WSPR, meteor scatter
# ---------------------------------------------------------------------------


PSK_LOG_DIR = Path('/var/log/psk-recorder')
PSK_WINDOW_S = 15 * 60
PSK_TAIL_BYTES = 8 * 1024 * 1024
# decode_ft8:  "2026/05/07 12:34:56 -15 +0.50 14,074,131.2 ~ K1ABC W1XYZ EM26"
# jt9:         "260507 123456 14074000 ..."  (psk-recorder ch_tailer.py:58-59)
_FT8_SPOT = re.compile(r'^(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\s.*~')
_JT9_SPOT = re.compile(r'^(\d{2})(\d{2})(\d{2})\s+(\d{2})(\d{2})(\d{2})\s+\d+\s')

WSPR_UNITS = ('wspr-recorder@*.service',)
WSPR_WINDOW_MIN = 10
WSPR_YOUNG_S = 12 * 60
# spot_sink's cycle line, the one `smd watch wspr` parses (bin/smd cycle_re).
_WSPR_CYCLE = re.compile(
    r'cycle\s+UTC\s+\d{2}:\d{2}\s+(?:rx=\S+\s+)?\S+\s+(\d+)\s+spots\s+in\s+wspr\.spots'
    r'(?:,\s+\d+\s+noise\s+rows\s+in\s+wspr\.noise)?\s+\((\d+)\s+bands?,'
    r'[^)]*\)(?:.*?\bbands=\[([^\]]*)\])?')

METEOR_UNITS = ('meteor-scatter@*.service',)
METEOR_WINDOW_MIN = 5
# meteor-scatter core/recorder.py: "stats MSK144 rx=<id>: spots=N decodes=OK/ALL ..."
_METEOR_STATS = re.compile(r'\bstats MSK144 rx=\S+: spots=(\d+) decodes=(\d+)/(\d+)')


def plural(n: int, word: str) -> str:
    return f'{n} {word}' + ('' if n == 1 else 's')


def _spot_time(line: str) -> Optional[float]:
    m = _FT8_SPOT.match(line)
    if m:
        y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
    else:
        m = _JT9_SPOT.match(line)
        if not m:
            return None
        y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
        y += 2000
    try:
        return datetime(y, mo, d, hh, mm, ss, tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def count_psk_spots(reader, log_dir: Path, now: float,
                    window_s: float = PSK_WINDOW_S) -> dict:
    """{'ft8': n, 'ft4': n}: spot lines stamped (UTC) inside the window, summed
    over every receiver's `<radiod_id>-ft8.log` and `-ft4.log`."""
    counts = {'ft8': 0, 'ft4': 0}
    names = reader.listdir(log_dir)
    if names is None:
        return counts
    for name in names:
        for mode in counts:
            if not name.endswith(f'-{mode}.log'):
                continue
            text = reader.tail(log_dir / name, PSK_TAIL_BYTES) or ''
            for line in text.splitlines():
                t = _spot_time(line.strip())
                if t is not None and now - window_s <= t <= now + 60:
                    counts[mode] += 1
    return counts


def psk_line(env: Env, log_dir: Path = PSK_LOG_DIR) -> Line:
    label = 'PSK'
    if not env.installed('psk-recorder'):
        return Line(ABSENT, label, 'not on this station')
    svc = env.service('psk-recorder@*.service')
    bad = service_problem(label, svc, 'psk-recorder')
    if bad:
        return bad
    counts = count_psk_spots(env.reader, log_dir, env.now)
    if counts['ft8'] or counts['ft4']:
        return Line(OK, label, f"{counts['ft8']} FT8 and {counts['ft4']} FT4 spots "
                               f"in the last 15 min")
    if is_young(svc, PSK_WINDOW_S):
        return Line(WAIT, label, f'no spots yet; psk-recorder started '
                                 f'{minutes(svc.age_s or 0)} min ago, wait 15 min')
    return Line(FAIL, label, 'no FT8 or FT4 spot in 15 min.  Check the antenna and its '
                             'cable first; if they are fine, tell the fleet admin.')


def wspr_line(env: Env) -> Line:
    label = 'WSPR'
    if not env.installed('wspr-recorder'):
        return Line(ABSENT, label, 'not on this station')
    svc = env.service('wspr-recorder@*.service')
    bad = service_problem(label, svc, 'wspr-recorder')
    if bad:
        return bad
    spots, bands, band_count = 0, set(), 0
    for line in env.journal(WSPR_UNITS, WSPR_WINDOW_MIN):
        m = _WSPR_CYCLE.search(line)
        if not m:
            continue
        spots += int(m.group(1))
        band_count = max(band_count, int(m.group(2)))
        if m.group(3):
            bands.update(tok.split(':', 1)[0] for tok in m.group(3).split()
                         if tok.split(':', 1)[-1] not in ('0', ''))
    if spots:
        n_bands = len(bands) or band_count
        return Line(OK, label, f'{spots} spots in the last 10 min, on {n_bands} band'
                               f'{"" if n_bands == 1 else "s"}')
    if is_young(svc, WSPR_YOUNG_S):
        return Line(WAIT, label, f'no spots yet; wspr-recorder started '
                                 f'{minutes(svc.age_s or 0)} min ago, wait 12 min')
    return Line(FAIL, label, 'no WSPR spot in 10 min.  Check the antenna and its cable '
                             'first; if they are fine, tell the fleet admin.')


def meteor_line(env: Env) -> Line:
    label = 'meteor scatter'
    if not env.installed('meteor-scatter'):
        return Line(ABSENT, label, 'not on this station')
    svc = env.service('meteor-scatter@*.service')
    bad = service_problem(label, svc, 'meteor-scatter')
    if bad:
        return bad
    stats = [m for m in (_METEOR_STATS.search(x)
                         for x in env.journal(METEOR_UNITS, METEOR_WINDOW_MIN)) if m]
    if stats:
        spots = sum(int(m.group(1)) for m in stats)
        decodes = sum(int(m.group(2)) for m in stats)
        return Line(OK, label, f'decoding: {plural(decodes, "MSK144 decode")} and '
                               f'{plural(spots, "spot")} in the last 5 min')
    if is_young(svc, METEOR_WINDOW_MIN * 60):
        return Line(WAIT, label, f'meteor-scatter started {minutes(svc.age_s or 0)} min ago; '
                                 f'its first report comes within 5 min')
    return Line(FAIL, label, 'meteor-scatter has reported nothing in 5 min.  '
                             'Tell the fleet admin.')


# ---------------------------------------------------------------------------
# Magnetometer
# ---------------------------------------------------------------------------

MAG_DIR = Path('/var/lib/mag-recorder')
MAG_DEVICE = '/dev/ttyMAG0'
MAG_SAMPLES = 60
MAG_FRESH_S = 10
MAG_YOUNG_S = 60
MAG_TAIL_BYTES = 64 * 1024
MAG_FIX = f'{FIX}sudo systemctl restart mag-recorder'


def _utc_day(t: float, days_back: int = 0) -> str:
    return datetime.fromtimestamp(t - days_back * 86400, tz=timezone.utc).strftime('%Y-%m-%d')


def _sample_time(sample: dict) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(sample['ts']).replace('Z', '+00:00')).timestamp()
    except (KeyError, ValueError):
        return None


def last_mag_samples(reader, mag_dir: Path, now: float, n: int = MAG_SAMPLES) -> List[dict]:
    """The newest `n` samples, oldest first.  Reads yesterday's file too when
    today's holds fewer (just after 00:00 UTC).  A line that does not parse
    (the half-written last line) is skipped."""
    samples: List[dict] = []
    for back in (0, 1):
        text = reader.tail(mag_dir / f'samples-{_utc_day(now, back)}.jsonl', MAG_TAIL_BYTES)
        got = []
        for line in (text or '').splitlines():
            try:
                s = json.loads(line)
            except ValueError:
                continue
            if isinstance(s, dict) and _sample_time(s) is not None:
                got.append(s)
        samples = got + samples
        if len(samples) >= n:
            break
    return samples[-n:]


def mag_frozen(samples: Sequence[dict]) -> Optional[str]:
    """Why the last 60 samples look frozen (spec §4.1), or None."""
    if len(samples) < MAG_SAMPLES:
        return None
    try:
        rts = [float(s['rt']) for s in samples]
        axes = {k: {float(s[k]) for s in samples} for k in ('x', 'y', 'z')}
    except (KeyError, TypeError, ValueError):
        return None
    if all(rt == 0.0 for rt in rts):
        return f'frozen: 0.00 C, no change in {MAG_SAMPLES} s'
    if all(len(v) == 1 for v in axes.values()):
        return f'frozen: x, y and z did not change in {MAG_SAMPLES} s'
    return None


def mag_line(env: Env, mag_dir: Path = MAG_DIR, device: str = MAG_DEVICE) -> Line:
    label = 'magnetometer'
    if not env.installed('mag-recorder'):
        return Line(ABSENT, label, 'not on this station')
    svc = env.service('mag-recorder.service')
    if svc.enabled and not env.exists(device):
        return Line(FAIL, label, f'no magnetometer adapter at {device}.  Check its USB cable.  '
                                 f'{MAG_FIX}')
    bad = service_problem(label, svc, 'mag-recorder')
    if bad:
        return bad
    samples = last_mag_samples(env.reader, mag_dir, env.now)
    newest = _sample_time(samples[-1]) if samples else None
    if newest is None or env.now - newest > MAG_FRESH_S:
        if is_young(svc, MAG_YOUNG_S):
            return Line(WAIT, label, 'mag-recorder just started; its first samples come '
                                     'within a minute')
        age = 'no sample today' if newest is None else f'no sample for {int(env.now - newest)} s'
        return Line(FAIL, label, f'{age}.  {MAG_FIX}')
    why = mag_frozen(samples)
    if why:
        return Line(FAIL, label, f'{why}.  {MAG_FIX}')
    return Line(OK, label, f"samples arriving, {float(samples[-1]['rt']):.1f} C")


# ---------------------------------------------------------------------------
# GRAPE and storage
# ---------------------------------------------------------------------------


TIMESTD_CONFIG = Path('/etc/hf-timestd/timestd-config.toml')
GRAPE_FRESH_S = 11 * 60
GRAPE_YOUNG_S = 12 * 60
STORAGE_PATH = '/var/lib'
STORAGE_MIN_FREE_PCT = 10.0
_PHASE_ENGINE_SHARED_HZ = (2500000, 5000000, 10000000, 15000000)


def read_timestd_config(reader=None, path: Path = TIMESTD_CONFIG) -> Optional[dict]:
    """hf-timestd's config, or None when absent.  The file is 0640
    timestd:timestd (clients/hftimestd.py), so this reads it through the
    Reader's sudo fallback."""
    import tomllib
    text = (reader or Reader()).tail(path, 4 * 1024 * 1024)
    return None if text is None else tomllib.loads(text)


def _channel_dir(description: str) -> str:
    """hf_timestd.paths.channel_name_to_dir: canonical SHARED_/WWV_<kHz> names
    pass through; anything else has its spaces turned into underscores."""
    return description.replace(' ', '_')


def grape_channels(cfg: dict) -> List[str]:
    """The raw_buffer directory names hf-timestd archives, from its config:
    every [[recorder.channel_group.*.channels]] entry whose archive flag
    (channel, then group, then [recorder]) is true.  In phase-engine mode a
    SHARED channel on 2.5, 5, 10 or 15 MHz becomes WWV_, WWVH_ and BPM_
    (core_recorder_v2.py _expand_channel_groups and the expansion before it)."""
    rec = cfg.get('recorder', {}) or {}
    engine = (cfg.get('ka9q', {}) or {}).get('source') or rec.get('engine', 'radiod')
    out = []
    for group in (rec.get('channel_group', {}) or {}).values():
        defaults = {k: v for k, v in group.items() if k != 'channels'}
        for ch in group.get('channels', []) or []:
            spec = {**defaults, **ch}
            if not spec.get('archive', rec.get('archive', True)):
                continue
            freq = int(spec.get('frequency_hz') or 0)
            desc = spec.get('description') or f'{freq / 1e6:.3f} MHz'
            if (engine == 'phase-engine' and freq in _PHASE_ENGINE_SHARED_HZ
                    and desc.startswith('SHARED')):
                out += [f'{t}_{freq // 1000}' for t in ('WWV', 'WWVH', 'BPM')]
            else:
                out.append(_channel_dir(desc))
    return out


def grape_roots(cfg: dict) -> List[Path]:
    """Where hf-timestd writes raw IQ.  With tiered storage (the shipped
    default) a file lands first in <hot_buffer_root>/raw_buffer and moves to
    <data_root>/raw_buffer minutes later; without it the writer uses
    <data_root>/<channel> (binary_archive_writer.py archive_dir)."""
    rec = cfg.get('recorder', {}) or {}
    data_root = Path(rec.get('production_data_root', '/var/lib/timestd'))
    roots = [data_root / 'raw_buffer']
    if rec.get('tiered_storage', False):
        roots.insert(0, Path(rec.get('hot_buffer_root', '/dev/shm/timestd')) / 'raw_buffer')
    else:
        roots.append(data_root)
    return roots


def grape_line(env: Env) -> Line:
    label = 'GRAPE'
    if not env.installed('hf-timestd'):
        return Line(ABSENT, label, 'not on this station')
    cfg = env.timestd_config()
    if cfg is None:
        return Line(FAIL, label, f'hf-timestd has no config at {TIMESTD_CONFIG}.  '
                                 f'Tell the fleet admin.')
    channels = grape_channels(cfg)
    if not channels:
        return Line(ABSENT, label, 'hf-timestd archives no channel on this station')
    svc = env.service('timestd-core-recorder.service')
    bad = service_problem(label, svc, 'hf-timestd')
    if bad:
        return bad
    days = [datetime.fromtimestamp(env.now - back * 86400, tz=timezone.utc).strftime('%Y%m%d')
            for back in (0, 1)]
    stale = []
    for ch in channels:
        newest = None
        for root in grape_roots(cfg):
            for day in days:
                m = env.reader.newest_mtime(root / ch / day)
                if m is not None:
                    newest = m if newest is None else max(newest, m)
        if newest is None or env.now - newest > GRAPE_FRESH_S:
            stale.append(ch)
    if not stale:
        return Line(OK, label, f'all {len(channels)} channels wrote a file in the last 11 min')
    if is_young(svc, GRAPE_YOUNG_S):
        due = minutes(GRAPE_YOUNG_S - (svc.age_s or 0))
        return Line(WAIT, label, f'first files due in about {due} min (hf-timestd started '
                                 f'{minutes(svc.age_s or 0)} min ago)')
    return Line(FAIL, label, f'no file in 11 min from {", ".join(stale)}.  '
                             f'Tell the fleet admin.')


def storage_line(env: Env, path: str = STORAGE_PATH) -> Line:
    label = 'storage'
    total, free = env.disk(path)
    pct = 100.0 * free / total if total else 0.0
    if pct >= STORAGE_MIN_FREE_PCT:
        return Line(OK, label, f'{path} has {int(pct)} % free')
    return Line(FAIL, label, f'{path} has only {int(pct)} % free.  Tell the fleet admin.')


def _disk(path: str) -> Tuple[int, int]:
    usage = shutil.disk_usage(path)
    return usage.total, usage.free


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------

UPLOAD_ACK_S = 30 * 60
NEW_STATION = 'new station'
# The heartbeat ships through hs-uploader too, every five minutes, whatever
# the site sink switch says.  Its cursor proves nothing about spots or data.
HEARTBEAT_TABLE = 'station.heartbeat'


def read_backlog(run=subprocess.run, euid=os.geteuid) -> dict:
    """hs_uploader.backlog's summary of watermarks.db, read out of process by
    heartbeat._read_backlog.  When the store refuses this user, ask once
    more through `sudo -n`."""
    from ..heartbeat import HeartbeatPaths, ReaderUnavailable, _read_backlog
    paths = HeartbeatPaths()
    try:
        out = _read_backlog(paths)
    except ReaderUnavailable as exc:
        return {'readable': False, 'reason': str(exc)}
    db = paths.watermarks_db
    if out.get('readable') or euid() == 0 or not os.path.exists(db) or os.access(db, os.R_OK):
        return out
    try:
        r = run(['sudo', '-n', 'env', f'PYTHONPATH={paths.hs_uploader_src}', sys.executable,
                 '-m', 'hs_uploader.backlog', db],
                capture_output=True, text=True, errors='replace', timeout=20, check=False)
        return json.loads(r.stdout) if r.returncode == 0 else out
    except (OSError, subprocess.SubprocessError, ValueError):
        return out


def _parse_ts(ts) -> Optional[float]:
    try:
        t = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp()


def uploads_line(env: Env) -> Line:
    label = 'uploads'
    up = env.coordination().uploads
    reason = (up.reason or '').strip()
    if up.mode == 'discard':
        if reason.startswith(NEW_STATION):
            return Line(WAIT, label, f'off, new station: check the lines above, then '
                                     f'{RUN}smd sink upload')
        return Line(FAIL, label, f'off ({reason or "no reason given"}).  '
                                 f'Ask the fleet admin.')
    if up.mode == 'hold':
        return Line(FAIL, label, f'hold ({reason or "no reason given"}).  Ask the fleet admin.')
    backlog = env.backlog()
    if not backlog.get('readable'):
        return Line(FAIL, label, f'cannot read the upload backlog: '
                                 f'{backlog.get("reason", "no reason given")}')
    dead = [(p.get('name', '?'), int(p.get('dead_letter_recent_count') or 0))
            for p in backlog.get('pipelines', [])]
    dead = [(n, c) for n, c in dead if c]
    if dead:
        total = sum(c for _, c in dead)
        names = ', '.join(n for n, _ in dead)
        return Line(FAIL, label, f'{total} upload{"s" if total != 1 else ""} failed for good '
                                 f'in the last 24 h ({names}).  Tell the fleet admin.')
    acks = [_parse_ts(c.get('last_ack')) for c in backlog.get('cursors', [])
            if c.get('table_name') != HEARTBEAT_TABLE]
    acks = [a for a in acks if a is not None]
    newest = max(acks) if acks else None
    if newest is not None and env.now - newest <= UPLOAD_ACK_S:
        return Line(OK, label, f'on; last delivery {minutes(env.now - newest)} min ago, '
                               f'no failure in 24 h')
    switched = env.coordination_age_s()
    if switched is not None and switched < UPLOAD_ACK_S:
        return Line(WAIT, label, f'on since {minutes(switched)} min ago; the first delivery '
                                 f'comes within 30 min')
    return Line(FAIL, label, 'on, but nothing delivered in 30 min.  Tell the fleet admin.')


def _coordination():
    from ..coordination import load_coordination
    from ..paths import COORDINATION_PATH
    return load_coordination(COORDINATION_PATH)


def _coordination_age_s() -> Optional[float]:
    """Seconds since coordination.toml last changed: how long the site sink
    switch has stood where it stands."""
    from ..paths import COORDINATION_PATH
    try:
        return time.time() - COORDINATION_PATH.stat().st_mtime
    except OSError:
        return None


# ---------------------------------------------------------------------------
# The lines, in the order the installer reads them (spec §2).  Each entry is
# (label, function); a function returns a Line, or None for a line that does
# not apply here at all.  Later tasks add their entries in place.
# ---------------------------------------------------------------------------

CHECKS: List[Tuple[str, Callable[[Env], Optional[Line]]]] = [
    ('PSK', psk_line),
    ('WSPR', wspr_line),
    ('magnetometer', mag_line),
    ('GRAPE', grape_line),
    ('storage', storage_line),
    ('uploads', uploads_line),
    ('meteor scatter', meteor_line),
]

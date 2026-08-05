"""hf-timestd offset-judge artifacts — sigmond-side consumers.

hf-timestd's Offset Judge (offset-judge spec 2026-08-05) publishes two
/run artifacts that sigmond consumes but never owns:

  /run/hf-timestd/offset_judge.json
      Per-radiod-source timing verdicts, refreshed every judge tick
      (~10 s).  `smd status` renders a "timing judge" section from it
      when it is present and fresh (see render_status_lines()).

  /run/hf-timestd/radiod-restart-request.json
      The spec-§9-step-3 escalation artifact
      (schema "radiod-restart-request-v1").  hf-timestd only ever
      REQUESTS a radiod restart; acting on the request is site policy —
      topology.toml [timing] honor_radiod_restart_request (default
      false), executed by bin/sigmond-radiod-watchdog via
      process_restart_request().

Ownership rules enforced here:

  * hf-timestd's artifacts are never deleted or modified by sigmond
    (hf-timestd withdraws its own request when the fault clears).
  * A given request (identified by its `requested_utc`) is honored AT
    MOST ONCE, across watchdog runs and even if the restart itself
    fails: an acted stamp is written to
    /run/sigmond/radiod-restart-honored.json BEFORE the restart is
    attempted.
  * Only a LOCAL, ACTIVE radiod@<radiod_id>.service is ever restarted.
    A request naming an unknown/remote/inactive unit is logged loudly
    and left alone.

Stdlib-only (core-smd constraint); all I/O is parameterised for tests.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

OFFSET_JUDGE_PATH   = Path('/run/hf-timestd/offset_judge.json')
RESTART_REQUEST_PATH = Path('/run/hf-timestd/radiod-restart-request.json')
HONORED_STAMP_PATH  = Path('/run/sigmond/radiod-restart-honored.json')

# Freshness bounds.  The judge publishes every ~10 s tick, so a
# 120 s-old offset_judge.json means the judge (or all of hf-timestd) is
# down — the status section is omitted rather than rendering stale
# verdicts as current.  A restart request older than 15 min is likewise
# ignored: hf-timestd withdraws cleared requests itself, so a fresh
# fault re-arms within its own cooldown discipline.
JUDGE_FRESH_S   = 120.0
REQUEST_FRESH_S = 900.0

RESTART_REQUEST_SCHEMA = 'radiod-restart-request-v1'

# systemd instance names we are willing to build from an external
# artifact.  Deliberately conservative — anything outside this set
# (slashes, whitespace, escapes) is schema-invalid.
_RADIOD_ID_RE = re.compile(r'[A-Za-z0-9._-]+')


def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path, 'rb') as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _parse_iso_utc(value) -> Optional[float]:
    """ISO-8601 UTC ('...Z' or offset) → epoch seconds, else None."""
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + '+00:00' if value.endswith('Z') else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ---------------------------------------------------------------------------
# offset_judge.json — status rendering
# ---------------------------------------------------------------------------

def load_offset_judge(path: Path = OFFSET_JUDGE_PATH,
                      now: Optional[float] = None,
                      max_age_s: float = JUDGE_FRESH_S) -> Optional[dict]:
    """The judge snapshot, or None when absent / stale / unparseable.

    Freshness is judged by file mtime (the judge writes atomically via
    tmp+rename every tick, so mtime == publish time).
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if now is None:
        import time
        now = time.time()
    if not (0.0 <= (now - mtime) <= max_age_s):
        return None
    return _read_json(path)


def _short_ssrc(source_key: str) -> str:
    """'hf-status.local/0000a4b2' → 'a4b2' (short ssrc for one-liners)."""
    tail = source_key.rsplit('/', 1)[-1]
    short = tail.lstrip('0')
    return short if short else '0'


def _fmt_ms(offset_ns) -> str:
    try:
        return f'{float(offset_ns) / 1e6:+.3f} ms'
    except (TypeError, ValueError):
        return '?'


def render_status_lines(doc: dict,
                        request: Optional[dict] = None,
                        honored: Optional[dict] = None) -> List[Tuple[str, str]]:
    """Render the `smd status` "timing judge" section.

    Returns (level, text) rows — level in {'ok','warn','err','info'} —
    which cmd_status maps onto its ✓/⚠/✗ styling.  Pure function; the
    caller decides section presence (fresh artifact) and any_bad
    escalation (any 'err' row).
    """
    lines: List[Tuple[str, str]] = []

    judge = doc.get('judge') or {}
    tier = judge.get('tier')
    sigma_ns = judge.get('sigma_ns')
    age_s = judge.get('age_s')
    gpsdo = doc.get('gpsdo_discipline', 'absent')
    if tier:
        sigma_txt = (f'σ={float(sigma_ns) / 1e3:.1f} µs'
                     if isinstance(sigma_ns, (int, float)) else 'σ=?')
        age_txt = (f'age {float(age_s):.0f}s'
                   if isinstance(age_s, (int, float)) else '')
        level = 'warn' if gpsdo in ('holdover', 'unlocked') else 'ok'
        parts = [f'judge {tier}', sigma_txt]
        if age_txt:
            parts.append(age_txt)
        parts.append(f'gpsdo={gpsdo}')
        lines.append((level, '  '.join(parts)))
    else:
        lines.append(('warn', f'judge has no verdict yet  gpsdo={gpsdo}'))

    for key in sorted(doc.get('sources') or {}):
        src = doc['sources'][key]
        if not isinstance(src, dict):
            continue
        short = _short_ssrc(key)
        bits = [f'offset {_fmt_ms(src.get("offset_ns"))}']
        rate = src.get('rate_ppm')
        if isinstance(rate, (int, float)):
            bits.append(f'rate {rate:+.3f} ppm')
        s_tier = src.get('tier')
        if s_tier:
            bits.append(str(s_tier))
        seg = src.get('segment_id')
        if seg is not None:
            bits.append(f'seg {seg}')
        faults = []
        if src.get('in_violation'):
            faults.append('OFFSET VIOLATION')
        if src.get('rate_alarm'):
            faults.append('RATE ALARM')
        if faults:
            lines.append(('err', f'{short}: {" + ".join(faults)} — '
                                 f'{", ".join(bits)}'))
        else:
            lines.append(('ok', f'{short}: {", ".join(bits)}'))

    if request is not None:
        rid = request.get('radiod_id', '?')
        off = request.get('offset_ms')
        off_txt = (f'{float(off):+.3f} ms'
                   if isinstance(off, (int, float)) else '?')
        sust = request.get('sustained_s')
        sust_txt = (f'{float(sust):.0f}s'
                    if isinstance(sust, (int, float)) else '?')
        req_utc = request.get('requested_utc')
        state = ('honored (radiod restarted)'
                 if honored and honored.get('requested_utc') == req_utc
                 else 'awaiting site policy — see topology.toml [timing] '
                      'honor_radiod_restart_request')
        lines.append(('err',
                      f'restart requested for radiod {rid}: offset '
                      f'{off_txt} sustained {sust_txt} — {state}'))

    return lines


# ---------------------------------------------------------------------------
# radiod-restart-request.json — the opt-in honor path
# ---------------------------------------------------------------------------

def validate_restart_request(doc) -> List[str]:
    """Schema problems with a radiod-restart-request-v1 doc ([] = valid)."""
    problems: List[str] = []
    if not isinstance(doc, dict):
        return ['not a JSON object']
    if doc.get('schema') != RESTART_REQUEST_SCHEMA:
        problems.append(f'schema is {doc.get("schema")!r}, '
                        f'expected {RESTART_REQUEST_SCHEMA!r}')
    if _parse_iso_utc(doc.get('requested_utc')) is None:
        problems.append(f'requested_utc unparseable: '
                        f'{doc.get("requested_utc")!r}')
    rid = doc.get('radiod_id')
    if not (isinstance(rid, str) and _RADIOD_ID_RE.fullmatch(rid)):
        problems.append(f'radiod_id not a plain unit instance: {rid!r}')
    if not isinstance(doc.get('source_key'), str):
        problems.append('source_key missing')
    for field in ('offset_ms', 'sustained_s'):
        if not isinstance(doc.get(field), (int, float)):
            problems.append(f'{field} missing or non-numeric')
    if not isinstance(doc.get('evidence'), dict):
        problems.append('evidence missing')
    return problems


@dataclass
class Decision:
    """Outcome of one restart-request evaluation.

    action: 'none'    — nothing to do (no artifact); silent.
            'ignore'  — artifact present but not actionable; `loud` marks
                        the cases that must reach the operator.
            'restart' — unit was restarted (stamp written first).
    """
    action: str
    reason: str
    loud: bool = False
    unit: Optional[str] = None
    request: Optional[dict] = None


def _systemctl_unit_state(unit: str) -> str:
    r = subprocess.run(['systemctl', 'is-active', unit],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip() or 'unknown'


def _systemctl_restart(unit: str) -> None:
    subprocess.run(['systemctl', 'restart', unit],
                   capture_output=True, text=True, timeout=120)


def write_honored_stamp(request: dict, now: float,
                        unit: str,
                        stamp_path: Path = HONORED_STAMP_PATH) -> None:
    """Record that `request` was acted on (keyed by requested_utc)."""
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = {
        'schema': 'radiod-restart-honored-v1',
        'requested_utc': request.get('requested_utc'),
        'honored_utc': datetime.fromtimestamp(
            now, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'unit': unit,
        'source_key': request.get('source_key'),
        'offset_ms': request.get('offset_ms'),
    }
    tmp = stamp_path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(stamp, indent=1) + '\n')
    tmp.replace(stamp_path)


def load_honored_stamp(
        stamp_path: Path = HONORED_STAMP_PATH) -> Optional[dict]:
    return _read_json(stamp_path)


def process_restart_request(
        enabled: bool,
        *,
        request_path: Path = RESTART_REQUEST_PATH,
        stamp_path: Path = HONORED_STAMP_PATH,
        now: Optional[float] = None,
        max_age_s: float = REQUEST_FRESH_S,
        unit_state: Callable[[str], str] = _systemctl_unit_state,
        restart_unit: Callable[[str], None] = _systemctl_restart,
        act: bool = True,
) -> Decision:
    """Evaluate (and, when everything lines up, honor) the restart request.

    The gate, in order — every early-out leaves hf-timestd's artifact
    untouched on disk:

      1. artifact absent                    → none (silent no-op)
      2. site policy off (`enabled=False`)  → ignore (visible, not loud)
      3. unparseable / schema-invalid       → ignore LOUD
      4. stale (requested_utc > max_age_s)  → ignore
      5. requested_utc already stamped      → ignore (never honor twice)
      6. radiod@<id>.service not active     → ignore LOUD (unknown or
         remote radiod, or one that is already down — not ours to touch)
      7. stamp written, unit restarted      → restart

    The stamp is written BEFORE the restart is attempted so a crash
    mid-restart can never lead to honoring the same request twice.

    act=False (the watchdog's RWD_DRY_RUN) runs the full gate but skips
    both the stamp write and the restart.
    """
    if now is None:
        import time
        now = time.time()

    if not request_path.exists():
        return Decision('none', 'no restart-request artifact')

    if not enabled:
        return Decision(
            'ignore',
            f'{request_path} present but topology.toml [timing] '
            f'honor_radiod_restart_request is false (site policy) — '
            f'not acting')

    doc = _read_json(request_path)
    if doc is None:
        return Decision('ignore',
                        f'{request_path} is not valid JSON — ignoring',
                        loud=True)

    problems = validate_restart_request(doc)
    if problems:
        return Decision(
            'ignore',
            f'{request_path} failed schema validation '
            f'({"; ".join(problems)}) — ignoring',
            loud=True, request=doc)

    requested = _parse_iso_utc(doc['requested_utc'])
    age = now - requested
    if not (0.0 <= age <= max_age_s):
        return Decision(
            'ignore',
            f'request from {doc["requested_utc"]} is stale '
            f'({age:.0f}s old, limit {max_age_s:.0f}s) — ignoring',
            request=doc)

    stamp = _read_json(stamp_path)
    if stamp is not None and \
            stamp.get('requested_utc') == doc['requested_utc']:
        return Decision(
            'ignore',
            f'request {doc["requested_utc"]} already honored at '
            f'{stamp.get("honored_utc")} — never honoring twice',
            request=doc)

    unit = f'radiod@{doc["radiod_id"]}.service'
    state = unit_state(unit)
    if state != 'active':
        return Decision(
            'ignore',
            f'request names radiod_id={doc["radiod_id"]!r} but {unit} is '
            f'{state} on this host — unknown/remote/inactive radiod, '
            f'refusing to act',
            loud=True, unit=unit, request=doc)

    if not act:
        return Decision(
            'restart',
            f'DRY-RUN: would honor request {doc["requested_utc"]} and '
            f'restart {unit}',
            loud=True, unit=unit, request=doc)
    write_honored_stamp(doc, now, unit, stamp_path=stamp_path)
    restart_unit(unit)
    return Decision(
        'restart',
        f'honored hf-timestd restart request {doc["requested_utc"]}: '
        f'restarted {unit}',
        loud=True, unit=unit, request=doc)

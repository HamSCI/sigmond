"""Draw meteor-scatter's inventory `msqp` block for `smd status`.

meteor-scatter's daemon rewrites <spool>/<radiod_id>/msqp-state.json once a
minute, and `meteor-scatter inventory --json` copies it into each instance as
`msqp`.  A null block means the daemon has not refreshed the file in five
minutes (or never wrote it).  An instance with no `msqp` key at all comes from
an older meteor-scatter; the renderer says nothing for it.

Shape (meteor-scatter core/msqp.py write_state, core/archive.py usage):

    {"written_at": float, "archive_root": str,
     "archive": {"bytes": int, "oldest_day": "YYYYMMDD"|None, "kept_today": int,
                 "bands": {band: {"bytes", "oldest_day", "kept_today"}}},
     "frontend_last_packet_age_sec": float|None,
     "channels": [{"band", "scale_db", "archived", "archive_failures",
                   "archiving": bool, "noise_log": bool,
                   "noise": {"calibrated_dbfs", "last_dbfs", "alarmed", "since"}}]}

`msqp_lines` returns (level, text) pairs, level 'info' or 'warn'.
"""

FRONTEND_STALE_S = 60.0


def _day(d):
    s = str(d or '')
    return f'{s[:4]}-{s[4:6]}-{s[6:]}' if len(s) == 8 and s.isdigit() else (s or 'none')


def _size(n):
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


def _db(v):
    return 'n/a' if v is None else f'{v:.1f} dBFS'


def msqp_lines(inst: dict) -> list:
    """Lines for one inventory instance dict; [] when it has no `msqp` key."""
    if 'msqp' not in inst:
        return []
    m = inst['msqp']
    if m is None:
        return [('warn', 'msqp: no report; the daemon has not refreshed its '
                         'state in 5 minutes (is meteor-scatter running?)')]
    out = []
    arc = m.get('archive') or {}
    out.append(('info',
                f"msqp archive: {_size(arc.get('bytes'))}, oldest day "
                f"{_day(arc.get('oldest_day'))}, {arc.get('kept_today', 0)} "
                f"recordings kept today"))
    warns = []
    for ch in m.get('channels') or []:
        band = ch.get('band', '?')
        n = ch.get('noise') or {}
        cal, last = n.get('calibrated_dbfs'), n.get('last_dbfs')
        if cal is None:
            floor = f'noise floor {_db(last)}, not calibrated'
        elif last is None:
            floor = f'noise floor n/a, calibration {_db(cal)}'
        else:
            floor = f'noise floor {_db(last)} vs calibration {_db(cal)} ({last - cal:+.1f} dB)'
        out.append(('info', f'msqp {band}: {floor}'))
        if n.get('alarmed'):
            warns.append(f'msqp {band}: noise floor has stayed outside its calibration band')
        if ch.get('archiving') is False:
            warns.append(f"msqp {band}: archiving has failed ({ch.get('archive_failures', 0)} failures)")
        if ch.get('noise_log') is False:
            warns.append(f'msqp {band}: noise log is failing to write')
    age = m.get('frontend_last_packet_age_sec')
    if age is None:
        out.append(('warn', 'msqp front end: no status packet seen'))
    elif age > FRONTEND_STALE_S:
        out.append(('warn', f'msqp front end: last status packet {age:.0f} s ago'))
    else:
        out.append(('info', f'msqp front end: ok (last status packet {age:.0f} s ago)'))
    out.extend(('warn', w) for w in warns)
    return out

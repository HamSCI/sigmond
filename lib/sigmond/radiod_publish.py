"""Is each radiod on this host published on the LAN?

A radiod-only host exists to be consumed by a decoder host elsewhere on the
LAN.  It is published when each configured radiod is running and its status
name resolves from the host itself (`getent hosts`, which goes through NSS and
so through mDNS where avahi is set up).  This is the check
`smd bringup radiod-only` ends on.

A radiod whose streams never leave its host is not published, however well
it runs: `[global] ttl = 0` keeps every packet on the box, and `iface = lo`
sends them nowhere else.  `publish_text()` is the minimal line edit that
fixes both (`smd admin radiod publish`, run by the radiod-only bring-up).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from sigmond.radiod_rename import (_find_key, _replace_value,
                                   _section_bounds, _value_in, status_of)

RADIO_CONF_DIR = Path('/etc/radio')

# diagnose() reasons; '' means published.
NO_STATUS = 'no [global] status'
UNIT_DOWN = 'unit not active'
UNRESOLVED = 'status name does not resolve'
TTL_ZERO = 'ttl 0: streams never leave this host'
IFACE_LO = 'iface lo'


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
            text = conf.read_text()
        except (OSError, UnicodeDecodeError):
            text = ''
        status = status_of(text)
        if not status:
            result[rid] = NO_STATUS
        elif _value_in(text, 'global', 'ttl') == '0':
            result[rid] = TTL_ZERO
        elif _value_in(text, 'global', 'iface') == 'lo':
            result[rid] = IFACE_LO
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


def _set_global(lines: list, key: str, value: str) -> bool:
    """Set `key = value` in [global] of `lines` in place (its trailing comment
    kept), inserting `key = value` after the last [global] line when absent.
    True when a line changed."""
    b = _section_bounds(lines, 'global')
    if b is None:
        return False
    hit = _find_key(lines, b, key)
    if hit is not None:
        i, m = hit
        new = _replace_value(lines[i], m, value)
        if new == lines[i]:
            return False
        lines[i] = new
        return True
    # After the section's last non-blank line, so a blank separator before
    # the next section stays where it was.
    start, end = b
    at = end
    while at - 1 > start and not lines[at - 1].strip():
        at -= 1
    if not lines[at - 1].endswith(('\n', '\r')):
        lines[at - 1] += '\n'
    lines.insert(at, f'{key:<9} = {value}\n')
    return True


def publish_text(conf_text: str, iface: str) -> tuple[str, list[str]]:
    """The conf with `ttl = 1` in [global] and, when `iface` there is `lo`
    or absent, `iface = <iface>`; every other line untouched.  Returns
    (new text, the changes made as `key: old -> new` strings).  A conf with
    no [global] section is returned unchanged."""
    lines = conf_text.splitlines(keepends=True)
    if _section_bounds(lines, 'global') is None:
        return conf_text, []
    changes = []
    old_ttl = _value_in(conf_text, 'global', 'ttl')
    if old_ttl != '1' and _set_global(lines, 'ttl', '1'):
        changes.append(f"ttl: {old_ttl or '(absent)'} -> 1")
    old_iface = _value_in(conf_text, 'global', 'iface')
    if old_iface in ('', 'lo') and iface and iface != 'lo':
        if _set_global(lines, 'iface', iface):
            changes.append(f"iface: {old_iface or '(absent)'} -> {iface}")
    return ''.join(lines), changes


def publish_confs(conf_dir: Path, iface: str, *,
                  write: bool = True) -> dict[str, list[str]]:
    """Apply publish_text() to every radiod@<id>.conf in `conf_dir` (the
    firmware bootstrap instance excepted).  {radiod id: changes} for each
    conf that changed (written unless `write` is False)."""
    conf_dir = Path(conf_dir)
    if not conf_dir.is_dir():
        return {}
    out: dict[str, list[str]] = {}
    for conf in sorted(conf_dir.glob('radiod@*.conf')):
        rid = conf.name[len('radiod@'):-len('.conf')]
        if rid.startswith('bootstrap-fw'):
            continue
        try:
            text = conf.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        new, changes = publish_text(text, iface)
        if not changes:
            continue
        if write:
            conf.write_text(new)
        out[rid] = changes
    return out

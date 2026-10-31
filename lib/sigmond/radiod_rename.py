"""Plan a radiod instance rename (`smd admin radiod rename`) without writing.

A radiod instance id (`AI6VN` in /etc/radio/radiod@AI6VN.conf) also names
its multicast status (`AI6VN-status.local`), which every client config binds
to.  `plan()` reads a station laid out under `root` and returns every text
edit, directory move and refusal a rename needs; `describe_plan()` does the
same for a description-only change.  Nothing here writes, prints, sleeps or
runs a process: the command layer applies the plan.

Edits are text edits of the operator's files (comments, ordering and hand
edits survive).  Each `Edit.path` is where the file lives AFTER the moves,
so an applier performs the moves first and then the edits; `Edit.before` is
the current content.

Design: docs/superpowers/specs/2026-10-31-radiod-rename-design.md.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .sdr_map import _HEADER, _RADIOD_HEADER

ID_RE = re.compile(r'^[A-Za-z0-9_.-]+$')

# A whole token is bounded by start/end of text or any character outside
# this class.  `.` is inside it, so `se-status.local` is not found inside
# `base-status.local`, nor `radiod@X.conf` inside `radiod@X.conf.bak`.
_TOK = 'A-Za-z0-9_.-'
_BARE_KEY = re.compile(r'^[A-Za-z0-9_-]+$')
_PER_RADIOD_HEADER = re.compile(
    r"""^\[\s*timing_authority\s*\.\s*per_radiod\s*\.\s*"""
    r"""(?:"([^"]*)"|'([^']*)'|([A-Za-z0-9_-]+))\s*\]$""")
_CLIENTS_HEADER = re.compile(r'^\[\[\s*clients\s*\.\s*"?([A-Za-z0-9_.-]+)"?\s*\]\]$')
_RADIOD_ID_LINE = re.compile(r"""^(\s*radiod_id\s*=\s*)(["'])(.*?)\2(.*)$""")
_STATUS_DNS_LINE = re.compile(r'^(\s*status_dns\s*=\s*)(.*)$')
_RADIO_CONF_KEY = re.compile(r'^\s*radio_conf\s*=')
_KEY_LINE = re.compile(r'^\s*[A-Za-z0-9_-]+\s*=')

_MAX_SCAN_BYTES = 4 * 1024 * 1024
_HOLD_REMEDY = 'sigmond-sdr-recover --on'


def valid_id(s: str) -> bool:
    return bool(ID_RE.match(s or ''))


def default_status(instance: str) -> str:
    return f'{instance}-status.local'


# ---------------------------------------------------------------- conf text

def _is_section_header(line: str) -> bool:
    return line.strip().startswith('[')


def _section_name(line: str) -> str:
    h = line.strip().split('#', 1)[0].strip()
    return h[1:-1].strip() if h.startswith('[') and h.endswith(']') else ''


def _split_value(rest: str) -> tuple[str, str, str]:
    """`rest` is what follows `key = `: (value as written, gap, comment)."""
    body = rest.rstrip('\r\n')
    if body[:1] in ('"', "'"):
        end = body.find(body[0], 1)
        end = len(body) if end < 0 else end + 1
        value, tail = body[:end], body[end:]
    else:
        cut = body.find('#')
        cut = len(body) if cut < 0 else cut
        value = body[:cut].rstrip()
        tail = body[len(value):]
    comment = tail.lstrip()
    gap = tail[:len(tail) - len(comment)]
    return value, gap, comment


def _unquote(value: str) -> str:
    if value[:1] in ('"', "'") and value.endswith(value[0]) and len(value) > 1:
        return value[1:-1]
    return value


def _section_bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    """(header index, end index) of the first `[section]`, else None."""
    start = None
    for i, raw in enumerate(lines):
        if not _is_section_header(raw):
            continue
        if start is not None:
            return start, i
        if _section_name(raw) == section:
            start = i
    return (start, len(lines)) if start is not None else None


def _find_key(lines: list[str], bounds, key: str):
    pat = re.compile(rf'^(\s*{re.escape(key)}\s*=\s*)(.*)$', re.S)
    start, end = bounds
    for i in range(start + 1, end):
        m = pat.match(lines[i])
        if m:
            return i, m
    return None


def _value_in(conf_text: str, section: str, key: str) -> str:
    lines = conf_text.splitlines(keepends=True)
    b = _section_bounds(lines, section)
    if b is None:
        return ''
    hit = _find_key(lines, b, key)
    if hit is None:
        return ''
    return _unquote(_split_value(hit[1].group(2))[0]).strip()


def _eol(raw: str) -> str:
    return raw[len(raw.rstrip('\r\n')):] or '\n'


def _replace_value(raw: str, m: re.Match, new_value: str) -> str:
    _, gap, comment = _split_value(m.group(2))
    tail = (gap + comment) if comment else ''
    return m.group(1) + new_value + tail + _eol(raw)


def status_of(conf_text: str) -> str:
    """The `[global] status` value, "" if absent."""
    return _value_in(conf_text, 'global', 'status')


def hardware_section(conf_text: str) -> str:
    """The `[global] hardware` value (`rx888`), "" if absent."""
    return _value_in(conf_text, 'global', 'hardware')


def set_status(conf_text: str, status: str) -> str:
    """Rewrite `status =` in [global] (trailing comment kept), or insert
    `status    = <status>` right after the [global] header."""
    lines = conf_text.splitlines(keepends=True)
    b = _section_bounds(lines, 'global')
    if b is None:
        return f'[global]\nstatus    = {status}\n' + conf_text
    hit = _find_key(lines, b, 'status')
    if hit is not None:
        i, m = hit
        quote = m.group(2)[:1] if m.group(2)[:1] in ('"', "'") else ''
        lines[i] = _replace_value(lines[i], m, f'{quote}{status}{quote}')
    else:
        h = b[0]
        if not lines[h].endswith(('\n', '\r')):
            lines[h] += '\n'
        lines.insert(h + 1, f'status    = {status}\n')
    return ''.join(lines)


def set_description(conf_text: str, text: str) -> str:
    """Rewrite `description =` in the hardware section (quoted, trailing
    comment kept) or insert it as that section's last key.  ValueError when
    the conf names no hardware section or the text cannot be quoted."""
    if '"' in text or '\n' in text or '\r' in text:
        raise ValueError('a description cannot hold a double quote or a newline')
    hw = hardware_section(conf_text)
    lines = conf_text.splitlines(keepends=True)
    b = _section_bounds(lines, hw) if hw else None
    if b is None:
        raise ValueError(f'no hardware section [{hw or "?"}] in the conf')
    hit = _find_key(lines, b, 'description')
    if hit is not None:
        i, m = hit
        lines[i] = _replace_value(lines[i], m, f'"{text}"')
        return ''.join(lines)
    start, end = b
    last = max((i for i in range(start + 1, end) if _KEY_LINE.match(lines[i])),
               default=start)
    if not lines[last].endswith(('\n', '\r')):
        lines[last] += '\n'
    lines.insert(last + 1, f'description = "{text}"\n')
    return ''.join(lines)


# ---------------------------------------------------------------- tokens

def rewrite_token(text: str, old: str, new: str) -> str:
    """Replace every whole-token occurrence of `old` with `new`."""
    if not old:
        return text
    pat = re.compile(rf'(?<![{_TOK}]){re.escape(old)}(?![{_TOK}])')
    return pat.sub(lambda _m: new, text)


_UNIT_SUFFIXES = ('', '.conf', '.service', '.conf.d', '.service.d')


def _rewrite_unit(text: str, old: str, new: str) -> str:
    """`radiod@<old>` as a unit, conf, conf.d or drop-in dir token."""
    for sfx in _UNIT_SUFFIXES:
        text = rewrite_token(text, f'radiod@{old}{sfx}', f'radiod@{new}{sfx}')
    return text


def _has_token(text: str, old: str, old_status: str) -> bool:
    return (rewrite_token(text, old_status, '\0') != text
            or _rewrite_unit(text, old, '\0') != text)


def _rewrite_all(text: str, old: str, new: str,
                 old_status: str, new_status: str) -> str:
    return rewrite_token(_rewrite_unit(text, old, new), old_status, new_status)


# ---------------------------------------------------------------- coordination

def _header_key(regex: re.Pattern, header: str) -> tuple[str, bool] | None:
    m = regex.match(header)
    if not m:
        return None
    for g, quoted in zip(m.groups(), (True, True, False)):
        if g is not None:
            return g, quoted
    return None


def _split_header(raw: str) -> tuple[str, str, str]:
    """(header, trailing text after it, eol) for a column-0 header line."""
    body = raw.rstrip('\r\n')
    head = body.split('#', 1)[0].rstrip()
    return head, body[len(head):], raw[len(body):]


def rename_coord(text: str, old: str, new: str,
                 old_status: str, new_status: str) -> str:
    """Re-key coordination.toml for a rename; every other byte is kept.

    `[radiod."<old>"]` (id-keyed) becomes `[radiod."<new>"]` with
    `status_dns = "<new_status>"`; `[radiod."<old_status>"]` (status-keyed,
    as bring-up writes it) becomes `[radiod."<new_status>"]`.  `radio_conf`
    values naming radiod@<old>.conf, `radiod_id` values naming either key,
    and `[timing_authority.per_radiod.<key>]` headers follow."""
    keymap = {old: new, old_status: new_status}
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_id_table = False
    id_header_at: int | None = None
    has_status_dns = False

    def close_id_table():
        if id_header_at is not None and not has_status_dns:
            if not out[id_header_at].endswith(('\n', '\r')):
                out[id_header_at] += '\n'
            out.insert(id_header_at + 1, f'status_dns = "{new_status}"\n')

    for raw in lines:
        body = raw.rstrip('\r\n')
        if _HEADER.match(body):
            if in_id_table:
                close_id_table()
            in_id_table, id_header_at, has_status_dns = False, None, False
            head, tail, eol = _split_header(raw)
            rk = _header_key(_RADIOD_HEADER, head)
            pk = _header_key(_PER_RADIOD_HEADER, head)
            if rk and rk[0] == old:
                out.append(f'[radiod."{new}"]{tail}{eol}')
                in_id_table, id_header_at = True, len(out) - 1
                continue
            if rk and rk[0] == old_status:
                out.append(f'[radiod."{new_status}"]{tail}{eol}')
                continue
            if pk and pk[0] in keymap:
                nk = keymap[pk[0]]
                key = f'"{nk}"' if (pk[1] or not _BARE_KEY.match(nk)) else nk
                out.append(f'[timing_authority.per_radiod.{key}]{tail}{eol}')
                continue
            out.append(raw)
            continue
        if in_id_table:
            m = _STATUS_DNS_LINE.match(raw)
            if m:
                has_status_dns = True
                out.append(_replace_value(raw, m, f'"{new_status}"'))
                continue
        if _RADIO_CONF_KEY.match(raw):
            out.append(rewrite_token(raw, f'radiod@{old}.conf', f'radiod@{new}.conf'))
            continue
        m = _RADIOD_ID_LINE.match(raw)
        if m and m.group(3) in keymap:
            q, eol = m.group(2), raw[len(raw.rstrip('\r\n')):]
            out.append(f'{m.group(1)}{q}{keymap[m.group(3)]}{q}{m.group(4)}{eol}')
            continue
        out.append(raw)
    if in_id_table:
        close_id_table()
    return ''.join(out)


def _radiod_keys(text: str) -> list[str]:
    keys = []
    for raw in text.splitlines():
        if _HEADER.match(raw):
            k = _header_key(_RADIOD_HEADER, _split_header(raw)[0])
            if k:
                keys.append(k[0])
    return keys


def _clients_bound(text: str, keys: set[str]) -> set[str]:
    """Client types with a `[[clients.<type>]] radiod_id` naming a key."""
    found: set[str] = set()
    ctype = None
    for raw in text.splitlines():
        if _HEADER.match(raw):
            m = _CLIENTS_HEADER.match(_split_header(raw)[0])
            ctype = m.group(1) if m else None
            continue
        m = _RADIOD_ID_LINE.match(raw)
        if ctype and m and m.group(3) in keys:
            found.add(ctype)
    return found


# ---------------------------------------------------------------- the plan

@dataclass
class Edit:
    path: Path
    before: str
    after: str


@dataclass
class Move:
    src: Path
    dst: Path


@dataclass
class RenamePlan:
    old: str
    new: str
    old_status: str
    new_status: str
    description: str | None
    edits: list[Edit]
    moves: list[Move]
    refusals: list[str]
    not_edited: list[Path]
    history: list[str]
    affected_components: list[str]

    @property
    def refused(self) -> bool:
        return bool(self.refusals)


def scan_dirs(root: Path, components: list[str]) -> list[Path]:
    """The directories whose files a rename edits."""
    dirs = [root / 'etc/radio', root / 'etc/sigmond', root / 'etc/hs-uploader']
    for c in components:
        d = root / 'etc' / c
        if valid_id(c) and d.is_dir() and d not in dirs:
            dirs.append(d)
    return dirs


def _files(top: Path, prune: frozenset = frozenset()):
    """Regular files under `top`, never following a symlink."""
    if not top.is_dir() or top.is_symlink():
        return
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if here / d not in prune)
        for f in sorted(filenames):
            p = here / f
            if not p.is_symlink() and p.is_file():
                yield p


def _read(p: Path) -> str | None:
    try:
        if p.stat().st_size > _MAX_SCAN_BYTES:
            return None
        return p.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None


def _history(status: str) -> list[str]:
    slug = re.sub(r'[^A-Za-z0-9_.-]', '-', status)
    return [f'/var/lib/psk-recorder/{status}/',
            f'/var/log/psk-recorder/{status}-*.log',
            f'/var/log/meteor-scatter/{status}-*.log',
            f'/dev/shm/wspr-recorder/{slug}/']


def plan(old: str, new: str, *, description: str | None = None,
         root: Path = Path('/'), components: list[str],
         hold: dict | None = None) -> RenamePlan:
    """Every edit, move and refusal renaming radiod@<old> to radiod@<new>."""
    root = Path(root)
    radio = root / 'etc/radio'
    systemd = root / 'etc/systemd/system'
    coord_path = root / 'etc/sigmond/coordination.toml'
    old_conf = radio / f'radiod@{old}.conf'
    new_conf = radio / f'radiod@{new}.conf'
    old_ok = valid_id(old)
    conf_text = _read(old_conf) if old_ok and old_conf.is_file() else None
    old_status = (status_of(conf_text) if conf_text else '') or default_status(old)
    new_status = default_status(new)
    coord_text = _read(coord_path) if coord_path.is_file() else None

    refusals: list[str] = []
    new_ok = valid_id(new)
    if not new_ok:
        refusals.append(f"'{new}' is not a valid radiod id; use only letters, "
                        f"digits, '_', '.' and '-'.")
    if conf_text is None:
        refusals.append(f'no /etc/radio/radiod@{old}.conf on this host; check '
                        f'the id with: smd admin sdr list')
    if new_ok:
        if new_conf.exists():
            refusals.append(f'/etc/radio/radiod@{new}.conf already exists; pick '
                            f'another name or remove that radiod first.')
        for key in _radiod_keys(coord_text or ''):
            if key in (new, new_status):
                refusals.append(f'coordination table [radiod."{key}"] already '
                                f'exists; pick another name or remove that table '
                                f'from /etc/sigmond/coordination.toml.')
        if (systemd / f'radiod@{new}.service.d').exists():
            refusals.append(f'/etc/systemd/system/radiod@{new}.service.d already '
                            f'exists; pick another name or remove that directory.')
    unit = f'radiod@{old}.service'
    if any(isinstance(h, dict) and h.get('radiod_unit') == unit
           for h in (hold or {}).values()):
        refusals.append(f'a manual RX888 hold names {unit}; clear it first: '
                        f'{_HOLD_REMEDY}')
    if conf_text is not None and hardware_section(conf_text) == 'sig_gen':
        refusals.append(f'radiod@{old} is a signal generator (hardware = '
                        f'sig_gen), not a receiver; rename its files by hand '
                        f'if you must.')
    elif old.startswith('bootstrap-fw'):
        refusals.append(f'radiod@{old} is a bootstrap firmware stub, not a '
                        f'receiver; rename its files by hand if you must.')

    if not refusals:
        try:
            new_conf_text = _conf_after(conf_text, old, new, old_status,
                                        new_status, description)
        except ValueError as e:
            refusals.append(f'cannot set the description in '
                            f'/etc/radio/radiod@{old}.conf ({e}); add a '
                            f'hardware section or drop --description.')

    if refusals:
        return RenamePlan(old, new, old_status, new_status, description,
                          [], [], refusals, [], [], [])

    moves = [Move(old_conf, new_conf)]
    remap: list[tuple[Path, Path]] = []
    for src, dst in ((radio / f'radiod@{old}.conf.d', radio / f'radiod@{new}.conf.d'),
                     (systemd / f'radiod@{old}.service.d',
                      systemd / f'radiod@{new}.service.d')):
        if src.is_dir() and not src.is_symlink():
            moves.append(Move(src, dst))
            remap.append((src, dst))

    def dest(p: Path) -> Path:
        for src, dst in remap:
            if p.is_relative_to(src):
                return dst / p.relative_to(src)
        return p

    edits = [Edit(new_conf, conf_text, new_conf_text)]
    affected: set[str] = set()
    if coord_text is not None:
        after = _rewrite_all(rename_coord(coord_text, old, new, old_status,
                                          new_status),
                             old, new, old_status, new_status)
        if after != coord_text:
            edits.append(Edit(coord_path, coord_text, after))
        affected |= _clients_bound(coord_text, {old, old_status})

    comps = list(components)
    if 'hf-timestd' not in comps:
        comps.append('hf-timestd')
    comp_dirs = {root / 'etc' / c: c for c in comps if valid_id(c)}
    edit_dirs = scan_dirs(root, comps) + [systemd]
    skip = {old_conf, coord_path}
    others: list[Edit] = []
    seen: set[Path] = set()
    for d in edit_dirs:
        for p in _files(d):
            if p in skip or p in seen:
                continue
            seen.add(p)
            text = _read(p)
            if text is None or not _has_token(text, old, old_status):
                continue
            after = _rewrite_all(text, old, new, old_status, new_status)
            if after == text:
                continue
            others.append(Edit(dest(p), text, after))
            for cdir, c in comp_dirs.items():
                if p.is_relative_to(cdir):
                    affected.add(c)

    not_edited: list[Path] = []
    labels = root / 'var/lib/sigmond/sdr-labels.toml'
    prune = frozenset(edit_dirs)
    for top in (root / 'etc', root / 'var/lib/sigmond'):
        for p in _files(top, prune):
            if p in seen:
                continue
            text = _read(p)
            if text is None or not _has_token(text, old, old_status):
                continue
            if p == labels:
                others.append(Edit(p, text, _rewrite_all(
                    text, old, new, old_status, new_status)))
            else:
                not_edited.append(p)

    edits += sorted(others, key=lambda e: str(e.path))
    return RenamePlan(old, new, old_status, new_status, description,
                      edits, moves, [], sorted(not_edited),
                      _history(old_status), sorted(affected))


def _conf_after(conf_text: str, old: str, new: str, old_status: str,
                new_status: str, description: str | None) -> str:
    text = _rewrite_all(conf_text, old, new, old_status, new_status)
    text = set_status(text, new_status)
    if description is not None:
        text = set_description(text, description)
    return text


def describe_plan(instance: str, text: str, *,
                  root: Path = Path('/')) -> RenamePlan:
    """A one-edit plan: the `description =` line of radiod@<instance>.conf."""
    root = Path(root)
    conf = root / 'etc/radio' / f'radiod@{instance}.conf'
    conf_text = _read(conf) if valid_id(instance) and conf.is_file() else None
    status = (status_of(conf_text) if conf_text else '') or default_status(instance)
    refusals: list[str] = []
    edits: list[Edit] = []
    if conf_text is None:
        refusals.append(f'no /etc/radio/radiod@{instance}.conf on this host; '
                        f'check the id with: smd admin sdr list')
    else:
        try:
            edits.append(Edit(conf, conf_text, set_description(conf_text, text)))
        except ValueError as e:
            refusals.append(f'cannot set the description in '
                            f'/etc/radio/radiod@{instance}.conf ({e}); add the '
                            f'hardware section [global] hardware names first.')
    return RenamePlan(instance, instance, status, status, text, edits, [],
                      refusals, [], [], [])

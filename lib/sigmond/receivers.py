"""The ``[[receiver]]`` list in site-profile.toml.

Each entry says which radiod (named by its mDNS status name, such as
``ac0g-b1-a-status.local``) one reporter ID listens to.  Later stages render
the list, apply it and drive bring-up from it; this module reads it, checks
it and writes it back.

Reading uses tomllib.  Writing works on the text: it replaces every
``[[receiver]]`` block and leaves the rest of the file as it was.
"""

from __future__ import annotations

import dataclasses
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .site_profile import SITE_PROFILE_PATH


class ReceiverError(ValueError):
    """A [[receiver]] entry that cannot be read."""


@dataclass(frozen=True)
class Receiver:
    reporter_id: str
    radiod: str = ''
    kiwisdr: str = ''
    antenna: str = ''
    clients: tuple = ()
    injector: bool = False
    same_antenna_as: str = ''
    upload: Optional[bool] = None
    # Parsed and kept; nothing acts on these yet.
    merge_into: str = ''
    merge_grid: str = ''

    @property
    def uploads(self) -> bool:
        """An explicit ``upload`` wins; otherwise a receiver that shares
        another's antenna does not upload."""
        if self.upload is not None:
            return self.upload
        return not self.same_antenna_as


_STR_KEYS = ('radiod', 'kiwisdr', 'antenna', 'same_antenna_as',
             'merge_into', 'merge_grid')
_BOOL_KEYS = ('injector', 'upload')
_KNOWN = {'reporter_id', 'clients', *_STR_KEYS, *_BOOL_KEYS}


def _storage(rid: str) -> str:
    from .instance import parse_user_reporter_id
    return parse_user_reporter_id(rid)


def _display(rid: str) -> str:
    from .instance import display_reporter_id
    return display_reporter_id(_storage(rid))


def _from_table(i: int, t: dict) -> Receiver:
    where = f'[[receiver]] #{i + 1}'
    for key in t:
        if key not in _KNOWN:
            raise ReceiverError(f'{where}: unknown key {key!r}')
    rid = t.get('reporter_id')
    if not isinstance(rid, str) or not rid.strip():
        raise ReceiverError(f'{where}: reporter_id must be a non-empty string')
    try:
        rid = _display(rid)
    except ValueError as e:
        raise ReceiverError(f'{where}: reporter_id {rid!r}: {e}') from None
    where = f'[[receiver]] {rid}'
    kw: dict = {'reporter_id': rid}
    for key in _STR_KEYS:
        if key in t:
            if not isinstance(t[key], str):
                raise ReceiverError(f'{where}: {key} must be a string')
            kw[key] = t[key]
    for key in _BOOL_KEYS:
        if key in t:
            if not isinstance(t[key], bool):
                raise ReceiverError(f'{where}: {key} must be true or false')
            kw[key] = t[key]
    if 'clients' in t:
        c = t['clients']
        if not isinstance(c, list) or not all(isinstance(x, str) for x in c):
            raise ReceiverError(f'{where}: clients must be a list of strings')
        kw['clients'] = tuple(c)
    return Receiver(**kw)


def load_receivers(path: Path = SITE_PROFILE_PATH) -> list:
    """The receivers in ``path``; ``[]`` when the file or the array is absent."""
    path = Path(path)
    if not path.is_file():
        return []
    try:
        with open(path, 'rb') as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ReceiverError(f'{path}: {e}') from None
    arr = data.get('receiver', [])
    if (not isinstance(arr, list)
            or not all(isinstance(t, dict) for t in arr)):
        raise ReceiverError('receiver must be an array of tables: [[receiver]]')
    return [_from_table(i, t) for i, t in enumerate(arr)]


def effective_clients(r: Receiver, profile_clients: frozenset) -> tuple:
    """``r.clients``, or the profile's reporter-keyed clients, sorted."""
    if r.clients:
        return tuple(r.clients)
    from .bringup import _REPORTER_KEYED
    return tuple(sorted(_REPORTER_KEYED & frozenset(profile_clients)))


def validate_receivers(receivers: list, *, profile_clients: frozenset) -> list:
    """One message per problem; ``[]`` when the list is clean."""
    from .bringup import _REPORTER_KEYED
    from .sources import valid_mdns_hostname
    msgs: list = []
    keys: dict = {}          # storage form -> Receiver
    for r in receivers:
        try:
            k = _storage(r.reporter_id)
        except ValueError as e:
            msgs.append(f'reporter {r.reporter_id!r}: {e}')
            continue
        if k in keys:
            msgs.append(f'reporter {_display(r.reporter_id)} appears more '
                        f'than once')
        else:
            keys[k] = r
    for r in receivers:
        who = r.reporter_id
        if r.radiod and r.kiwisdr:
            msgs.append(f'{who}: names both radiod and kiwisdr; give one')
        elif r.kiwisdr:
            msgs.append(f'{who}: KiwiSDR receivers arrive with Phase 4c')
        elif not r.radiod:
            msgs.append(f'{who}: names no radiod')
        elif not valid_mdns_hostname(r.radiod):
            msgs.append(f'{who}: radiod {r.radiod!r} is not a valid '
                        f'status name')
        for c in r.clients:
            if c not in _REPORTER_KEYED:
                msgs.append(f'{who}: {c} is not a per-reporter client')

    def _key(rid):
        try:
            return _storage(rid)
        except ValueError:
            return None

    by_radiod: dict = {}
    for r in receivers:
        if r.radiod and not r.kiwisdr:
            by_radiod.setdefault(r.radiod, []).append(r)
    for radiod, group in by_radiod.items():
        for r in group[1:]:
            if not r.same_antenna_as:
                msgs.append(
                    f'{r.reporter_id} shares {radiod} with '
                    f'{group[0].reporter_id}: it needs same_antenna_as '
                    f'naming a receiver on that radiod')
        if sum(1 for r in group if r.injector) > 1:
            msgs.append(f'{radiod}: more than one receiver carries '
                        f'injector = true')
    for r in receivers:
        if not r.same_antenna_as:
            continue
        other = keys.get(_key(r.same_antenna_as))
        if other is None:
            msgs.append(f'{r.reporter_id}: same_antenna_as '
                        f'{r.same_antenna_as} names no receiver')
        elif other.radiod != r.radiod:
            msgs.append(f'{r.reporter_id}: same_antenna_as '
                        f'{r.same_antenna_as} is on another radiod '
                        f'({other.radiod or "none"})')
    if sum(1 for r in receivers if r.injector) > 1:
        msgs.append('injector = true on more than one receiver; hf-timestd '
                    'runs once per host, so one receiver may carry it')
    return msgs


_HEADER = '[[receiver]]'
_HEADER_RE = re.compile(r'^\[\[\s*receiver\s*\]\]\s*(#.*)?$')


def _q(s: str) -> str:
    out = []
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        elif ord(ch) < 0x20 or ord(ch) == 0x7f:
            out.append('\\u%04x' % ord(ch))
        else:
            out.append(ch)
    return '"' + ''.join(out) + '"'


def _block(r: Receiver) -> str:
    lines = [_HEADER, f'reporter_id = {_q(_display(r.reporter_id))}']
    for key in ('radiod', 'kiwisdr', 'antenna'):
        if getattr(r, key):
            lines.append(f'{key} = {_q(getattr(r, key))}')
    if r.clients:
        lines.append('clients = [' + ', '.join(_q(c) for c in r.clients) + ']')
    if r.injector:
        lines.append('injector = true')
    if r.same_antenna_as:
        lines.append(f'same_antenna_as = {_q(r.same_antenna_as)}')
    if r.upload is not None:
        lines.append(f'upload = {"true" if r.upload else "false"}')
    for key in ('merge_into', 'merge_grid'):
        if getattr(r, key):
            lines.append(f'{key} = {_q(getattr(r, key))}')
    return '\n'.join(lines) + '\n'


def _without_receivers(text: str) -> str:
    keep, inside = [], False
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith('['):
            inside = bool(_HEADER_RE.match(s))
        if not inside:
            keep.append(line)
    out = ''.join(keep)
    if inside and out.strip():
        # The blocks ran to the end of the file: drop the blank line
        # write_receivers put in front of them.
        out = out.rstrip('\n') + '\n'
    return out


def write_receivers(path: Path, receivers: list) -> None:
    """Replace every [[receiver]] block in ``path`` with ``receivers``.
    Everything else stays as it was; an absent file is created."""
    from .coordination import _write_coordination
    path = Path(path)
    text = path.read_text(encoding='utf-8') if path.is_file() else ''
    rest = _without_receivers(text)
    if rest and not rest.endswith('\n'):
        rest += '\n'
    if receivers and rest.strip() and not rest.endswith('\n\n'):
        rest += '\n'
    rest += '\n'.join(_block(r) for r in receivers)
    _write_coordination(path, rest)


def merged_receivers(current: list, r: Receiver, *,
                     keep_unset: bool = True) -> list:
    """The list ``current`` with ``r`` upserted; nothing is written.
    Replace the receiver with ``r``'s reporter ID (storage form), else
    append.  With ``keep_unset``, the fields ``r`` leaves at their defaults
    (antenna, same_antenna_as, upload, merge_into, merge_grid) carry over
    from the stored receiver."""
    key = _storage(r.reporter_id)
    out, found = [], False
    for old in current:
        if _storage(old.reporter_id) != key:
            out.append(old)
            continue
        found = True
        new = dataclasses.replace(r, reporter_id=_display(r.reporter_id))
        if keep_unset:
            carry = {}
            for f in dataclasses.fields(Receiver):
                if f.name in ('antenna', 'same_antenna_as', 'upload',
                              'merge_into', 'merge_grid') \
                        and getattr(r, f.name) == f.default:
                    carry[f.name] = getattr(old, f.name)
            new = dataclasses.replace(new, **carry)
        out.append(new)
    if not found:
        out.append(dataclasses.replace(r, reporter_id=_display(r.reporter_id)))
    return out


def upsert_receiver(path: Path, r: Receiver, *, keep_unset: bool = True) -> list:
    """`merged_receivers` over the stored list, written back to ``path``."""
    out = merged_receivers(load_receivers(path), r, keep_unset=keep_unset)
    write_receivers(path, out)
    return out

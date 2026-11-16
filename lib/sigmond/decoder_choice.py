"""Which decoder build the station runs, and whether its operator holds it.

sigmond keeps two builds of `wsprd`/`jt9` under DECODER_DIR (`ubersdr`,
`wsjtx`) and points /usr/local/bin/{wsprd,jt9} at one (bin/smd,
_select_decoders).  It refuses an ubersdr build whose WSJT-X parity check did
not pass, and an install falls back to wsjtx when ubersdr cannot be built.

WWV-2 (2026-11-16) chose its ubersdr build by hand although that build's
parity check had failed; the next `smd update` or `smd align` would have
pointed it back at wsjtx.  The operator override is the documented way to
make that choice stick:

    # /etc/sigmond/coordination.toml
    [decoders]
    implementation    = "ubersdr"   # optional; absent = keep the live build
    operator_override = true

With it set, sigmond selects the chosen build even when its parity check did
not pass, never falls back to the other build on its own, and says so
wherever it reports the decoders.  Absent or false: today's behaviour.

Read straight from the file, as bin/smd's _decoder_preference does: which
binary /usr/local/bin/wsprd points at is a property of the host's filesystem,
not of any service's environment.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Optional

COORDINATION_TOML = Path('/etc/sigmond/coordination.toml')
DECODER_DIR = Path('/usr/local/libexec/sigmond/decoders')
BIN_DIR = Path('/usr/local/bin')
IMPLS = ('ubersdr', 'wsjtx')
#: The key, under [decoders] in coordination.toml.
OVERRIDE_KEY = 'operator_override'


def decoders_table(path: Optional[Path] = None) -> dict:
    """coordination.toml's [decoders] table; {} when absent or unreadable."""
    try:
        data = tomllib.loads(Path(path or COORDINATION_TOML).read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    table = data.get('decoders')
    return table if isinstance(table, dict) else {}


def operator_override(path: Optional[Path] = None) -> bool:
    """True only for `operator_override = true`: a string or a number is not
    an operator's decision, and reads as the default."""
    return decoders_table(path).get(OVERRIDE_KEY) is True


def live(bindir: Optional[Path] = None) -> str:
    """The build /usr/local/bin/wsprd links into, or '' when it is not one
    of ours (a real file, a Debian binary, or absent)."""
    try:
        target = os.readlink(Path(bindir or BIN_DIR) / 'wsprd')
    except OSError:
        return ''
    for impl in IMPLS:
        if f'decoders/{impl}/' in target:
            return impl
    return ''


def parity(impl: str, decdir: Optional[Path] = None) -> str:
    """The `parity=` word in a build's .provenance (`verified`, `SKIPPED`,
    `FAILED`, ...), or '' when the marker or the word is missing."""
    try:
        text = (Path(decdir or DECODER_DIR) / impl / '.provenance').read_text()
    except OSError:
        return ''
    for tok in text.split():
        if tok.startswith('parity='):
            return tok.split('=', 1)[1]
    return ''


def override_note(*, path: Optional[Path] = None,
                  bindir: Optional[Path] = None,
                  decdir: Optional[Path] = None) -> Optional[str]:
    """One line saying the operator holds the decoder build, or None when no
    override is set.  The parity state is named whenever it is not
    `verified`, so nobody reads the hold as a passed check."""
    if not operator_override(path):
        return None
    impl = live(bindir)
    if not impl:
        return ('decoder operator override set, but /usr/local/bin/wsprd is '
                'not a sigmond decoder link')
    word = parity(impl, decdir) if impl == 'ubersdr' else ''
    tail = f', parity {word or "unknown"}' if impl == 'ubersdr' and word != 'verified' else ''
    return (f'decoders: {impl} held by operator override{tail}; '
            f'sigmond will not switch it')

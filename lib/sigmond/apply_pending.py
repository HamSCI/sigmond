"""Dry-run output parser for the TUI Apply screen.

The Apply screen runs ``smd apply --dry-run`` on mount and uses these
helpers to bucket each output line into one of:

  * ``pending``   — a change that would happen on the next live apply
                    (line contains "(dry-run)" or "would " phrasing,
                    which are the two patterns sigmond's reconciler
                    uses for would-do messages).
  * ``unchanged`` — explicit no-op summary ("unchanged: X").
  * ``warning``   — any line carrying "warning" or the ⚠ glyph.
  * ``info``      — headings, status banners, anything else.

Lives in its own module (not in tui/screens/apply.py) so the
classifier is importable without pulling in the Textual runtime
— makes it usable from unit tests that don't have textual
installed.  See tests/test_apply_pending.py.
"""

from __future__ import annotations

import re

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def strip_ansi(s: str) -> str:
    """Drop ANSI SGR escape sequences from a single line."""
    return _ANSI_RE.sub('', s)


def classify_line(line: str) -> str:
    """Bucket one line of ``smd apply --dry-run`` output.

    Returns one of: ``"pending"``, ``"unchanged"``, ``"warning"``,
    ``"info"``.  See module docstring for the rule each bucket
    matches.
    """
    bare = strip_ansi(line).strip()
    if not bare:
        return 'info'
    # Decorative section headings produced by bin/smd's _heading().
    if bare.startswith('━━━') or bare.startswith('==='):
        return 'info'
    low = bare.lower()
    if '(dry-run)' in low or 'would ' in low:
        return 'pending'
    if 'unchanged' in low:
        return 'unchanged'
    if 'warning' in low or '⚠' in bare:
        return 'warning'
    return 'info'


__all__ = ["strip_ansi", "classify_line"]

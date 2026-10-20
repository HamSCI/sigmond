"""What a component's install.sh accepts on its command line.

smd align, `smd admin manifest restore --apply` and bring-up run each
installer with stdin closed.  The catalog contract (etc/catalog.toml) asks
every installer to accept --yes.  A restore runs the OLDER release's
installers, though, and those may predate the contract: hf-tec 613408c
exited 2 on --yes (B3 rig, 2026-10-20).  So the caller passes --yes only
to an installer whose own argument parser names it.

This reads the script's text; it never runs it.
"""
from __future__ import annotations

import re

# An argument `case` over "$arg", "$1", "${1}" or "$opt", up to its esac.
_ARG_CASE = re.compile(r'case\s+"\$(?:arg|1|\{1\}|opt)"\s+in(.*?)\besac\b', re.S)
# A pattern list that names --yes: "--yes)", "--yes|-y)", "-y|--yes)".
_YES_PATTERN = re.compile(r'(?:^|[\s|(])--yes\s*[|)]', re.M)
_CATCH_ALL_EXITS = re.compile(r'^\s*\*\)[^\n]*\b(exit|die)\b', re.M)


def accepts_yes(text: str) -> bool:
    """True when an argument case in ``text`` has a --yes branch."""
    return any(_YES_PATTERN.search(m.group(1)) for m in _ARG_CASE.finditer(text))


def rejects_yes(text: str) -> bool:
    """True when an argument case exits on a catch-all branch and has no
    --yes branch: that installer fails when given --yes."""
    for m in _ARG_CASE.finditer(text):
        body = m.group(1)
        if _CATCH_ALL_EXITS.search(body) and not _YES_PATTERN.search(body):
            return True
    return False

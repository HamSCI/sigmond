"""Which units may `smd apply` act on, and which has the operator ruled out?

`smd apply` reconciles units toward "enabled and running". That is right for
drift and wrong for a decision. On B4 2026-09-01 it enabled and started a
leftover `radiod@AC0G-B4-patched.service` — twice in one day, the second time
after an operator had disabled it in between — during a component update
scoped to two LIBRARIES. It could not run, because the real radiod holds the
RX888, so it failed; and its failed state then blocked `timestd-core-recorder`
from starting. The station's whole timing chain went down.

`disable` did not survive, because apply reads "not enabled" as drift to
correct. `mask` is the one state that means "never run this", and apply must
read it as the instruction it is.

⚠ Deliberately narrow. Only an explicit mask exempts a unit; any unrecognised
state is still managed, because silently skipping units would be the opposite
failure — apply quietly doing less than it claims.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

#: `systemctl is-enabled` values that mean the operator has forbidden this
#: unit from running. Both forms of mask count.
_FORBIDDEN = frozenset({"masked", "masked-runtime"})


def should_manage(unit: str, enabled_state: str) -> bool:
    """May apply enable or start this unit?"""
    return (enabled_state or "").strip() not in _FORBIDDEN


def partition(states: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """Split units into (manage, skipped), preserving input order."""
    manage: List[str] = []
    skipped: List[str] = []
    for unit, state in states.items():
        (manage if should_manage(unit, state) else skipped).append(unit)
    return manage, skipped

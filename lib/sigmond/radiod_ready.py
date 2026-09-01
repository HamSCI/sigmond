"""Which radiod instance does a consumer wait for?

`sigmond-radiod-ready` discovers "the" radiod unit when no `--unit` is given.
It used to take whatever `systemctl list-units "radiod@*.service"` returned
and refuse to proceed unless there was exactly one. That listing includes
units in FAILED state and units that are MASKED — neither of which can ever
serve RTP.

⛔ Measured on B4 2026-09-01, and it stopped the station. `smd apply` enabled
a leftover `radiod@AC0G-B4-patched.service` from an abandoned upgrade branch.
It could not start, because the real radiod holds the RX888. So it failed, and
from then on:

    sigmond-radiod-ready: expected one radiod instance, found
      ['radiod@AC0G-B4-patched.service', 'radiod@AC0G-B4.service'] — pass --unit
    timestd-core-recorder.service: Control process exited, status=2

The whole timing chain refused to start. `systemctl mask` did NOT clear it —
a masked unit in failed state is still listed. Only `systemctl reset-failed`
worked, which is not something an operator would think to try at 22:00 with
the station down.

A unit that is masked, failed, or not loaded is not a candidate. Deciding that
here, rather than handing the operator an ambiguity, is the difference between
a self-clearing condition and an outage.
"""

from __future__ import annotations

from typing import List

#: `systemctl list-units --no-legend --plain` emits
#: ``UNIT LOAD ACTIVE SUB DESCRIPTION...``
_LOAD = 1
_ACTIVE = 2


def candidate_units(listing: str) -> List[str]:
    """Radiod units that could plausibly be serving, from a listing.

    Keeps units that are loaded and not failed — including ones still
    `activating`, since waiting for exactly that is why the gate exists.
    Genuine ambiguity (two live receivers) is preserved and returned, because
    that is a real question only the operator can answer.
    """
    out: List[str] = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) <= _ACTIVE:
            continue
        if fields[_LOAD] != "loaded":
            continue        # masked, not-found, error
        if fields[_ACTIVE] == "failed":
            continue        # cannot serve; often a stale sibling
        out.append(fields[0])
    return out


def excluded_units(listing: str) -> List[str]:
    """Units the listing offered that `candidate_units` rejected.

    Reported in the failure message so an operator sees WHY discovery landed
    where it did, instead of being told only that it could not decide.
    """
    keep = set(candidate_units(listing))
    out: List[str] = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) <= _ACTIVE:
            continue
        if fields[0] not in keep:
            out.append(f"{fields[0]} ({fields[_LOAD]}/{fields[_ACTIVE]})")
    return out

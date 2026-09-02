"""Who is this station?  Read from the hostname, never asked for.

DASI2 machines are named DASI001-DASI020 (later DASI plus three digits
generally), and each one's PSWS Station and Instrument IDs are pre-defined in
the roster shipped beside this file.  So provisioning a fleet is: name the
machine, and identity follows with nothing to type.

This extends a decision the install redesign already locked — "radiod status
name is auto-composed from the hostname, zero operator input" — to the
station's identity as a whole.

Membership has exactly two consequences: the PSWS IDs read here, and the RAC
registrar chosen on the PM (`vpn.hamsci.org` for a site, wsprdaemon gw2 for
everyone else).  The PM side is a separate spec; both agree on
`/etc/sigmond/station.toml`.

This module reads the roster file and nothing else: no network, no
subprocess, and `identify()` takes the hostname as a parameter rather than
detecting it, so the whole surface is testable without a real machine.

A later task adds `read_manifest()` / `identity_from_manifest()` to this same
module, for a fresh VM that gets its identity from the PM's manifest instead
of the roster. Not implemented here.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

#: Anchored: `mydasi001x` is not a fleet machine.
_DASI_NAME = re.compile(r"^DASI(\d{3})$", re.IGNORECASE)

DEFAULT_ROSTER = Path(__file__).resolve().parents[2] / "config" / "dasi2-roster.toml"


class UnrosteredDasiName(Exception):
    """A DASI-named host that the roster does not know.

    Refused rather than guessed.  Called a site it would take the DASI2
    registrar with no PSWS identity; called ordinary it would run forever
    under a fleet name it does not own.
    """


@dataclass(frozen=True)
class StationIdentity:
    hostname: str
    dasi2_site: bool
    psws_station: Optional[str] = None
    psws_instrument: Optional[str] = None


def load_roster(path: Path = DEFAULT_ROSTER) -> Dict[str, dict]:
    """The shipped fleet roster, keyed by upper-case host name."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return {k.upper(): v for k, v in data.items()}


def identify(hostname: str, roster: Optional[Dict[str, dict]] = None
             ) -> StationIdentity:
    """Decide membership from the hostname.

    Raises `UnrosteredDasiName` when the name looks like a fleet machine and
    the roster does not list it.
    """
    if roster is None:
        roster = load_roster()
    match = _DASI_NAME.match(hostname.strip())
    if match is None:
        return StationIdentity(hostname=hostname, dasi2_site=False)

    key = hostname.strip().upper()
    entry = roster.get(key)
    if entry is None:
        raise UnrosteredDasiName(
            f"hostname {hostname!r} matches the DASI fleet pattern but is not "
            f"in the roster, so this machine's identity and RAC registrar "
            f"cannot be decided. Either correct the hostname, or add a "
            f"{key} entry to the roster."
        )
    return StationIdentity(
        hostname=hostname,
        dasi2_site=True,
        psws_station=entry["psws_station"],
        psws_instrument=entry["psws_instrument"],
    )

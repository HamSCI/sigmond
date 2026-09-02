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

This module reads files and nothing else: no network, no subprocess, and
`identify()` takes the hostname as a parameter rather than detecting it, so
the whole surface is testable without a real machine.

`read_manifest()` / `identity_from_manifest()` cover the other source of
identity: a replaced VM that reads `/etc/sigmond/station.toml`, written by
the PM, instead of re-deciding from the roster. Writing and mirroring that
file is a separate, PM-side spec; this module only reads it.
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


class ManifestHostnameMismatch(Exception):
    """A manifest claims `dasi2_site` for a hostname that isn't DASI-shaped.

    Membership comes from the hostname, never from an operator answer — and
    a manifest is close kin to an operator answer: something written down
    elsewhere, not derived from the name in front of us.  Honoring it here
    would let a manifest promote an ordinary station to a site, which is
    exactly the silent-promotion the hostname rule forbids.  Refused rather
    than guessed: either the hostname is wrong, or the manifest is stale.
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

    The stripped hostname is what lands on the returned `StationIdentity`,
    not the raw input.  This is a deliberate behavior change (task 7, round
    2 review): previously the raw, possibly padded, hostname was stored even
    though the roster lookup key was always the stripped/upper-cased form —
    so the stored field could disagree with the key that was actually
    matched. Storing the stripped value keeps the two in agreement.
    """
    if roster is None:
        roster = load_roster()
    stripped = hostname.strip()
    match = _DASI_NAME.match(stripped)
    if match is None:
        return StationIdentity(hostname=stripped, dasi2_site=False)

    key = stripped.upper()
    entry = roster.get(key)
    if entry is None:
        raise UnrosteredDasiName(
            f"hostname {hostname!r} matches the DASI fleet pattern but is not "
            f"in the roster, so this machine's identity and RAC registrar "
            f"cannot be decided. Either correct the hostname, or add a "
            f"{key} entry to the roster."
        )
    return StationIdentity(
        hostname=stripped,
        dasi2_site=True,
        psws_station=entry["psws_station"],
        psws_instrument=entry["psws_instrument"],
    )


#: The PM writes this; the VM reads it.  The PM is the machine that survives a
#: VM replacement, so the facts that cannot be rediscovered live there.
DEFAULT_MANIFEST = Path("/etc/sigmond/station.toml")


def read_manifest(path: Path = DEFAULT_MANIFEST) -> Dict[str, object]:
    """The station manifest, or an empty dict when there is none.

    A station without a PM manifest is an ordinary case — a bare install, or a
    host with no PM — not a fault.
    """
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (FileNotFoundError, NotADirectoryError):
        return {}


def identity_from_manifest(manifest: Dict[str, object],
                            hostname: str) -> StationIdentity:
    """Identity as the PM recorded it.

    A fresh VM must not need the roster to know who it is: the PM already
    decided, and re-deciding risks a different answer.  But the hostname
    still governs membership, never the manifest alone: when the manifest
    asserts `dasi2_site`, the hostname must be DASI-shaped
    (`_DASI_NAME`) or this raises `ManifestHostnameMismatch` rather than
    returning a `StationIdentity` the hostname itself disagrees with.  That
    much needs no roster, just the shape of the name.

    The reverse — a DASI-shaped, rostered hostname whose manifest asserts
    `dasi2_site = false` (a demotion) — is deliberately NOT checked here:
    resolving it needs the roster, which this function does not take as a
    parameter, and belongs to whatever future caller holds both `identify()`
    and `identity_from_manifest()` together.  Not an oversight; the
    asymmetry is because the promotion half is decidable from the hostname
    string alone and the demotion half is not.

    `hostname` is stripped before it lands on the returned identity, the
    same as `identify()` (task 7 round 2: both now store the stripped form,
    so a `StationIdentity` built by either path is directly comparable).
    """
    hostname = hostname.strip()
    if not manifest.get("dasi2_site"):
        return StationIdentity(hostname=hostname, dasi2_site=False)
    if _DASI_NAME.match(hostname) is None:
        raise ManifestHostnameMismatch(
            f"manifest asserts dasi2_site for hostname {hostname!r}, which "
            f"does not match the DASI fleet pattern ({_DASI_NAME.pattern}). "
            f"Either the hostname is wrong, or the manifest is stale/wrong "
            f"— refusing rather than guessing which one to trust."
        )
    return StationIdentity(
        hostname=hostname,
        dasi2_site=True,
        psws_station=manifest.get("psws_station"),
        psws_instrument=manifest.get("psws_instrument"),
    )

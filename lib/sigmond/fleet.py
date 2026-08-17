"""Fleet inventory — where every sigmond host is, and how to reach it.

Answers "what hosts are there?"  It deliberately does **not** answer
"which credential reaches them" — that is the operator's ssh config.
The split is the same one ``ssh/config.fleet`` already makes: this file
records WHERE, ``~/.ssh/config`` supplies WHICH KEY.  Keeping them
separate is what lets two operators share one inventory without
stepping on each other's keys.

Layering, and why it is thinner than ``catalog.toml``'s
=======================================================

``catalog.py`` merges three layers by sparse overlay because a catalog
is a *description of software that could exist*, and new entries must
propagate to every host on ``git pull``.  An inventory is the opposite:
it is a *list of machines to act on*, and there is exactly one real one.

So there is **no repo-default layer here**.  ``etc/fleet.toml.example``
is documentation, not a layer — ``HamSCI/sigmond`` is public, and a
real inventory maps every field unit.  Resolution is first-match-wins,
highest precedence first:

    1. an explicit path (``--inventory``)
    2. ``$SIGMOND_FLEET``
    3. ``/etc/sigmond/fleet.toml``  (:data:`DEFAULT_FLEET_PATH`)

A *whole file* wins, rather than overlaying field-by-field.  Overlaying
inventories would mean an override file silently inherits hosts it did
not list — and a fan-out that touches a host the operator did not name
is precisely the failure this module exists to prevent.

Reach and hop
=============

``reach`` is an opaque ssh destination: anything ``ssh`` itself accepts,
including flags (``-J jump -p 2222 root@host``).  It is never parsed
into components — ssh is the parser.

``hop`` is the optional *inner* destination of a nested ssh, for hosts
whose credential does not live where the fan-out runs.  A sigmond guest
on a hypervisor is the live case: the hypervisor's own key is installed
in the guest, so the hypervisor must run the inner ssh.  This cannot be
expressed as ``-J``/ProxyJump, which would offer the *caller's* key on
the final hop.

Failure policy
==============

Loudly, always, naming what could not be parsed.  A host that is
silently skipped here is invisible in every later fan-out, which reads
exactly like a healthy fleet that happens not to include it.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


#: The documented default, mirroring ``catalog.toml``'s operator layer.
DEFAULT_FLEET_PATH = Path('/etc/sigmond/fleet.toml')

#: Escape hatch for hosts with no sigmond install — the devbox has no
#: ``/etc/sigmond`` at all, so the default path can never resolve there.
FLEET_PATH_ENV = 'SIGMOND_FLEET'


@dataclass(frozen=True)
class Host:
    """One fleet member.

    Attributes:
        name: Inventory key, e.g. ``b4``.
        reach: Opaque ssh destination. Never parsed.
        hop: Opaque inner ssh destination for a nested reach, or None.
        profile: Bring-up profile the host runs, e.g. ``dasi2``.
        role: Free-form grouping, e.g. ``field`` or ``server``.
        frozen: Why the host must not be changed, or None if it may be.
            A reason rather than a bool, so the skip can say *why* when
            it is reported.
    """

    name: str
    reach: str
    hop: Optional[str] = None
    profile: Optional[str] = None
    role: Optional[str] = None
    frozen: Optional[str] = None


def _resolve_path() -> Optional[Path]:
    """The inventory to read when no explicit path was given.

    Returns None when no inventory exists anywhere — a legitimate state
    on every host that is not the devbox.
    """
    env = os.environ.get(FLEET_PATH_ENV)
    if env:
        candidate = Path(env)
        if candidate.exists():
            return candidate
        # An operator who sets the variable means it; a typo there must
        # not degrade to "no fleet".
        raise FileNotFoundError(
            f"{FLEET_PATH_ENV} names a file that does not exist: {candidate}"
        )
    if DEFAULT_FLEET_PATH.exists():
        return DEFAULT_FLEET_PATH
    return None


def _host_from_block(name: str, block, source: Path) -> Host:
    if not isinstance(block, dict):
        raise ValueError(
            f"fleet inventory {source}: host '{name}' must be a table "
            f"([host.{name}]), got {type(block).__name__}"
        )
    reach = block.get('reach')
    if not reach or not isinstance(reach, str):
        raise ValueError(
            f"fleet inventory {source}: host '{name}' has no 'reach'. "
            f"Every host must record where it is, or it would be "
            f"invisible to every fan-out."
        )
    return Host(
        name=name,
        reach=reach,
        hop=block.get('hop'),
        profile=block.get('profile'),
        role=block.get('role'),
        frozen=block.get('frozen'),
    )


def load_fleet(path: Optional[str] = None) -> dict[str, Host]:
    """Load the inventory, keyed by host name.

    Args:
        path: Read this file specifically. When None, resolve per the
            precedence documented in the module docstring.

    Returns:
        Every host in the inventory. Empty when no inventory exists at
        all (no-path form only) — having no fleet is not an error.

    Raises:
        FileNotFoundError: An explicit ``path`` (or ``$SIGMOND_FLEET``)
            names a file that is not there. Returning an empty fleet for
            a typo'd path would report "no hosts" indistinguishably from
            a real empty inventory.
        ValueError: The file is not valid TOML, or a host block is
            malformed. Both name the file; host errors name the host.
    """
    if path is not None:
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(f"fleet inventory not found: {source}")
    else:
        resolved = _resolve_path()
        if resolved is None:
            return {}
        source = resolved

    try:
        with source.open('rb') as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"fleet inventory {source} is not valid TOML: {exc}") from exc

    hosts = raw.get('host') or {}
    if not isinstance(hosts, dict):
        raise ValueError(
            f"fleet inventory {source}: 'host' must be a table of hosts"
        )
    return {
        name: _host_from_block(name, block, source)
        for name, block in hosts.items()
    }

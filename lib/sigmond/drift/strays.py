"""Drift items for the stray kind: things on a station no release owns.

* a folder under the decoder directory other than ubersdr and wsjtx.
* a unit instance lifecycle marks orphaned, or whose name holds the
  escaped "=" (the literal \\x3d).  It is a fault when its environment
  still sets a *_DELIVERY_MODE or *_DELIVERY_PIPELINES key, which overrides
  the live instance's delivery; otherwise drift.
* a git checkout under base that the catalog does not name and that is not
  one of sigmond's own non-catalog checkouts.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Iterable, Optional

from sigmond.drift.model import Item

_DECODER_OK = {"ubersdr", "wsjtx"}
# Checkouts sigmond itself clones (bin/smd native sources) or is.
_KNOWN_CHECKOUTS = {"onion", "wsjtx", "ubersdr_wsprd", "ka9q-radio", "sigmond"}
_ESCAPED_EQ = "\\x3d"


def systemd_unit_env(unit: str) -> dict:
    """The unit's Environment as a dict (empty when systemctl fails)."""
    try:
        r = subprocess.run(
            ["systemctl", "show", "-p", "Environment", "--value", unit],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    env = {}
    for tok in r.stdout.split():
        key, eq, val = tok.partition("=")
        if eq:
            env[key] = val
    return env


def _delivery_keys(env: dict) -> list:
    return sorted(k for k in env
                  if k.endswith("_DELIVERY_MODE")
                  or k.endswith("_DELIVERY_PIPELINES"))


def _decoder_items(decoder_dir: Path) -> list:
    out = []
    try:
        entries = sorted(Path(decoder_dir).iterdir())
    except OSError:
        return out
    for p in entries:
        if p.name.startswith(".") or p.name in _DECODER_OK:
            continue
        out.append(Item(
            "stray", "drift", str(p),
            f"{p} is under the decoder directory and no release installs it",
            f"remove {p} once nothing points at it "
            "(smd align reports overrides that do)"))
    return out


def _unit_items(unit_refs: Iterable, unit_env: Callable) -> list:
    out = []
    for ref in unit_refs:
        inst = getattr(ref, "instance", None) or ""
        if not (getattr(ref, "orphaned", False) or _ESCAPED_EQ in inst):
            continue
        keys = _delivery_keys(unit_env(ref.unit) or {})
        why = ("is orphaned" if getattr(ref, "orphaned", False)
               else "has an escaped instance name")
        detail = f"{ref.unit} {why}"
        sev = "drift"
        if keys:
            sev = "fault"
            detail += f"; its environment still overrides {', '.join(keys)}"
        out.append(Item(
            "stray", sev, ref.unit, detail,
            f"smd admin instance remove {ref.component} {inst}"))
    return out


def _checkout_items(base: Path, catalog_names: set) -> list:
    out = []
    try:
        entries = sorted(Path(base).iterdir())
    except OSError:
        return out
    for p in entries:
        if (p.name.startswith(".") or p.name in catalog_names
                or p.name in _KNOWN_CHECKOUTS):
            continue
        if p.is_dir() and (p / ".git").exists():
            out.append(Item(
                "stray", "drift", str(p),
                f"{p} is a git checkout that no catalog entry names",
                f"remove {p} or add it to the catalog"))
    return out


def stray_items(*, decoder_dir: Path, base: Path, catalog_names: set,
                unit_refs: Iterable,
                unit_env: Optional[Callable] = None) -> list:
    unit_env = unit_env or systemd_unit_env
    return (_decoder_items(decoder_dir)
            + _unit_items(unit_refs, unit_env)
            + _checkout_items(base, set(catalog_names)))

"""Drift items for the override kind.

An override replaces the release's own choice of software or source.
Site configuration (callsign, grid, receivers, channels) never counts.

* client configs: a key ending in _jt9, _wsprd or _binary, or named
  decoder_jt9 or decoder_path, with a non-empty string value (any depth).
* /etc/sigmond/catalog.toml: an entry setting repo or branch.
* /etc/sigmond/component-pins.toml: a pin that differs from the manifest
  commit, or names a component the manifest lacks.
* coordination.toml [decoders] operator_override = true.

A file that cannot be read or parsed yields one fault; a missing file
yields nothing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Mapping

from sigmond import decoder_choice
from sigmond.drift.model import Item

_SUFFIXES = ("_jt9", "_wsprd", "_binary")
_NAMES = ("decoder_jt9", "decoder_path")


def _load(path: Path, out: list):
    """Parsed TOML, or None when missing or unreadable (fault appended)."""
    path = Path(path)
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        out.append(Item("override", "fault", str(path),
                        f"could not read {path}: {exc}",
                        f"repair or remove {path}"))
        return None


def _is_choice_key(key: str) -> bool:
    return key in _NAMES or key.endswith(_SUFFIXES)


def _walk(table: Mapping, prefix: str = ""):
    for key, val in table.items():
        dotted = f"{prefix}{key}"
        if isinstance(val, dict):
            yield from _walk(val, dotted + ".")
        elif (isinstance(val, str) and val.strip()
              and _is_choice_key(str(key))):
            yield dotted, val


def _client_items(client_configs, out: list) -> None:
    for comp in sorted(client_configs):
        for path in client_configs[comp]:
            data = _load(path, out)
            if data is None:
                continue
            name = Path(path).name
            for dotted, val in _walk(data):
                out.append(Item(
                    "override", "fault", f"{name}:{dotted}",
                    f"{name} sets {dotted} = {val!r}, replacing the "
                    "release's own software", f"remove {dotted} from {name}"))


def _catalog_items(path: Path, out: list) -> None:
    data = _load(path, out)
    if data is None:
        return
    for table in data.values():
        if not isinstance(table, dict):
            continue
        for name, entry in table.items():
            if not isinstance(entry, dict):
                continue
            keys = [k for k in ("repo", "branch") if k in entry]
            if keys:
                out.append(Item(
                    "override", "drift", f"catalog:{name}",
                    f"{path} sets {', '.join(keys)} for {name}",
                    f"remove the repo/branch override for {name} from "
                    f"{path} (smd config catalog-prune)"))


def _pin_items(path: Path, manifest: Mapping[str, str], out: list) -> None:
    data = _load(path, out)
    if data is None:
        return
    pins = data.get("pins")
    if not isinstance(pins, dict):
        return
    for comp in sorted(pins):
        pin = pins[comp]
        if not isinstance(pin, str) or not pin.strip():
            continue
        pin = pin.strip().lower()
        want = manifest.get(comp)
        if want is None:
            out.append(Item(
                "override", "drift", f"pin:{comp}",
                f"{comp} is pinned to {pin[:12]}; the release manifest "
                "does not list it",
                f"remove the {comp} pin from {path}"))
            continue
        want = str(want).strip().lower()
        if not (pin.startswith(want) or want.startswith(pin)):
            out.append(Item(
                "override", "drift", f"pin:{comp}",
                f"{comp} is pinned to {pin[:12]}; the release has "
                f"{want[:12]}",
                f"remove the {comp} pin from {path}"))


def _decoder_items(path: Path, out: list) -> None:
    data = _load(path, out)
    if data is None:
        return
    table = data.get("decoders")
    if isinstance(table, dict) and table.get(
            decoder_choice.OVERRIDE_KEY) is True:
        impl = table.get("implementation")
        if not isinstance(impl, str) or not impl:
            impl = decoder_choice.live() or "the live build"
        out.append(Item(
            "override", "drift", "decoders:operator_override",
            f"{path} holds the decoder build ({impl}) with "
            "operator_override = true",
            f"set operator_override = false in {path} when the hold is "
            "no longer needed"))


def override_items(*, client_configs: Mapping[str, list],
                   catalog_override: Path = Path("/etc/sigmond/catalog.toml"),
                   pins_file: Path = Path(
                       "/etc/sigmond/component-pins.toml"),
                   coordination: Path = Path(
                       "/etc/sigmond/coordination.toml"),
                   manifest: Mapping[str, str]) -> list:
    out: list = []
    _client_items(client_configs, out)
    _catalog_items(Path(catalog_override), out)
    _pin_items(Path(pins_file), manifest, out)
    _decoder_items(Path(coordination), out)
    return out

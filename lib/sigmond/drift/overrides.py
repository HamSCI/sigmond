"""Drift items for the override kind.

An override replaces the release's own choice of software or source.
Site configuration (callsign, grid, receivers, channels) never counts.

* client configs: a key ending in _jt9, _wsprd or _binary, or named
  decoder_jt9 or decoder_path, with a non-empty string value (any depth)
  that differs from the release's own value.  The release's value comes
  from the component's shipped template at its checkout
  (``<base>/<component>/config/*.toml.template``): the same dotted key
  when the template has it, else any template value under the same key
  name.  Without a readable template, ``RELEASE_DEFAULTS`` stands in.
* /etc/sigmond/catalog.toml: each field of a [client.*], [deprecated.*] or
  [profile.*] entry, one item per field (v3.94 Task 9), subject
  ``catalog:<name>.<key>`` for a client and ``catalog:<section>.<name>.<key>``
  for the others.  A missing or unreadable repo catalog is one fault.  A field that copies the repo
  catalog (``<sigmond>/etc/catalog.toml``) is fixed by
  ``smd config catalog-prune``, which ``smd align --apply`` runs.  A field
  that differs (a staging-fork repo, say) stays an item until the operator
  removes it: the prune keeps it, and nothing deletes it silently.  Other
  tables are not catalog entries and never count.
* /etc/sigmond/component-pins.toml: any such file, one item.  Every pin
  comes from the release (v3.94 Task 9); smd no longer reads the file, and
  ``smd align --apply`` retires it.
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

# The values the shipped templates write, for a component whose checkout
# holds no readable template.  Keyed by the key's own name.
RELEASE_DEFAULTS = {
    "decoder_jt9": {"/usr/local/bin/jt9_decode"},
    "mag_usb_binary": {"/usr/local/bin/mag-usb"},
}


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
        elif isinstance(val, list):
            for n, item in enumerate(val):
                if isinstance(item, dict):
                    yield from _walk(item, f"{dotted}[{n}].")
        elif (isinstance(val, str) and val.strip()
              and _is_choice_key(str(key))):
            yield dotted, val


def _leaf(dotted: str) -> str:
    return dotted.rsplit(".", 1)[-1]


def release_values(base, comp: str):
    """(by dotted key, by key name) of the choice keys the component's
    shipped templates set, or None when no template could be read."""
    if base is None:
        return None
    try:
        paths = sorted((Path(base) / comp / "config").glob("*.toml.template"))
    except OSError:
        return None
    by_dotted: dict = {}
    by_leaf: dict = {}
    found = False
    for p in paths:
        try:
            with open(p, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            continue
        found = True
        for dotted, val in _walk(data):
            by_dotted[dotted] = val.strip()
            by_leaf.setdefault(_leaf(dotted), set()).add(val.strip())
    return (by_dotted, by_leaf) if found else None


def _is_release_value(dotted: str, val: str, release) -> bool:
    val = val.strip()
    if release is None:
        return val in RELEASE_DEFAULTS.get(_leaf(dotted), ())
    by_dotted, by_leaf = release
    if dotted in by_dotted:
        return val == by_dotted[dotted]
    return val in by_leaf.get(_leaf(dotted), ())


def _client_items(client_configs, out: list, base=None) -> None:
    for comp in sorted(client_configs):
        release = release_values(base, comp)
        for path in client_configs[comp]:
            data = _load(path, out)
            if data is None:
                continue
            name = Path(path).name
            for dotted, val in _walk(data):
                if _is_release_value(dotted, val, release):
                    continue
                out.append(Item(
                    "override", "fault", f"{name}:{dotted}",
                    f"{name} sets {dotted} = {val!r}, replacing the "
                    "release's own software", f"remove {dotted} from {name}"))


_CATALOG_SECTIONS = ("client", "deprecated", "profile")


def _catalog_items(path: Path, repo_catalog: Path, out: list) -> None:
    data = _load(path, out)
    if data is None:
        return
    if not any(isinstance(data.get(s), dict) and data.get(s)
               for s in _CATALOG_SECTIONS):
        return
    try:
        with open(repo_catalog, "rb") as f:
            repo = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        out.append(Item("override", "fault", str(repo_catalog),
                        f"could not read {repo_catalog}: {exc}",
                        f"repair {repo_catalog} (smd align --apply)"))
        return
    for section in _CATALOG_SECTIONS:
        table = data.get(section)
        if not isinstance(table, dict):
            continue
        repo_table = repo.get(section)
        repo_table = repo_table if isinstance(repo_table, dict) else {}
        for name in sorted(table):
            entry = table[name]
            if not isinstance(entry, dict):
                continue
            want = repo_table.get(name)
            want = want if isinstance(want, dict) else {}
            where = name if section == "client" else f"{section}.{name}"
            for key in sorted(entry):
                subject = f"catalog:{where}.{key}"
                if key in want and want[key] == entry[key]:
                    out.append(Item(
                        "override", "drift", subject,
                        f"{path} sets {key} for {where}, which copies the "
                        "repo catalog and would hide a later change to it",
                        "smd config catalog-prune"))
                else:
                    out.append(Item(
                        "override", "drift", subject,
                        f"{path} sets {key} = {entry[key]!r} for {where}, "
                        "replacing the release's catalog",
                        f"remove {key} for {where} from {path} (smd config "
                        "catalog-prune keeps a field that differs)"))


def _pin_items(path: Path, out: list) -> None:
    data = _load(path, out)
    if data is None:
        return
    pins = data.get("pins") if isinstance(data, dict) else None
    n = len(pins) if isinstance(pins, dict) else 0
    out.append(Item(
        "override", "drift", "pins:station-file",
        f"{path} holds {n} station pin(s); every pin comes from the "
        "release, and smd no longer reads it",
        f"smd align --apply (retires {path})"))


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
                   repo_catalog: Path = Path(
                       "/opt/git/sigmond/sigmond/etc/catalog.toml"),
                   pins_file: Path = Path(
                       "/etc/sigmond/component-pins.toml"),
                   coordination: Path = Path(
                       "/etc/sigmond/coordination.toml"),
                   base=None) -> list:
    """``base`` holds the component checkouts whose shipped templates
    give the release's own values (None: RELEASE_DEFAULTS only).
    ``repo_catalog`` is sigmond's own etc/catalog.toml, which the
    operator catalog's fields are compared with."""
    out: list = []
    _client_items(client_configs, out, base)
    _catalog_items(Path(catalog_override), Path(repo_catalog), out)
    _pin_items(Path(pins_file), out)
    _decoder_items(Path(coordination), out)
    return out

"""Drift items for the unit kind.

* every unit an enabled component's deploy.toml declares must exist in the
  unit directory; a templated unit counts when its template file exists
  (instances are never required); a listed timer must be enabled.
* sigmond's own systemd/sigmond-*.{service,timer,path} must match the
  installed copies byte for byte.  Whether they are enabled stays out of
  scope: several install on purpose without enabling.
* a unit enabled for a component that topology disables is drift.

install_own_units copies sigmond's own units the way install.sh does, so
`smd align --apply` can land units added after a station's install.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Callable, Mapping

from sigmond import component_state
from sigmond.drift.model import Item

_APPLY = "smd align --apply"
_OWN_GLOBS = ("sigmond-*.service", "sigmond-*.timer", "sigmond-*.path")


def _declared(base: Path, name: str) -> list:
    """Unit names from deploy.toml [systemd] (units, then templated_units)."""
    deploy = None
    path = Path(base) / name / "deploy.toml"
    try:
        with open(path, "rb") as f:
            deploy = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        deploy = None
    if deploy is None:
        shim = component_state.fallback_shim_path(name)
        if shim is not None:
            try:
                with open(shim, "rb") as f:
                    deploy = tomllib.load(f)
            except (OSError, tomllib.TOMLDecodeError):
                deploy = None
    return component_state._systemd_unit_names(deploy or {})


def _exists(unit_dir: Path, unit: str) -> bool:
    p = Path(unit_dir) / unit
    return p.exists() or p.is_symlink()


def own_unit_files(sigmond_repo: Path) -> list:
    out = []
    for pat in _OWN_GLOBS:
        out.extend(sorted((Path(sigmond_repo) / "systemd").glob(pat)))
    return [p for p in out if p.is_file()]


def _differs(src: Path, dst: Path) -> bool:
    try:
        return dst.read_bytes() != src.read_bytes()
    except OSError:
        return True


def unit_items(base: Path, enabled: Mapping[str, bool], *,
               sigmond_repo: Path,
               unit_dir: Path = Path("/etc/systemd/system"),
               is_enabled: Callable[[str], bool]) -> list:
    out = []
    unit_dir = Path(unit_dir)
    for name in sorted(enabled):
        units = _declared(base, name)
        if enabled[name]:
            for u in units:
                if not _exists(unit_dir, u):
                    out.append(Item("unit", "fault", u,
                                    f"{name} declares {u}; not installed in "
                                    f"{unit_dir}", _APPLY))
                elif u.endswith(".timer") and not is_enabled(u):
                    out.append(Item("unit", "fault", u,
                                    f"{name} timer {u} is installed but not "
                                    "enabled", f"smd enable {name}"))
        else:
            for u in units:
                if _exists(unit_dir, u) and is_enabled(u):
                    out.append(Item(
                        "unit", "drift", u,
                        f"{u} is enabled; topology disables {name}",
                        f"smd disable {name} (stops and disables its units)"))
    for src in own_unit_files(sigmond_repo):
        dst = unit_dir / src.name
        if not _exists(unit_dir, src.name):
            out.append(Item("unit", "fault", src.name,
                            f"sigmond unit {src.name} is not installed",
                            _APPLY))
        elif _differs(src, dst):
            out.append(Item("unit", "fault", src.name,
                            f"installed {src.name} differs from sigmond's "
                            "copy", _APPLY))
    return out


def install_own_units(sigmond_repo: Path, unit_dir: Path, *,
                      run=subprocess.run) -> list:
    """Copy missing or changed sigmond units into unit_dir (0644, atomic).

    Enables and starts nothing.  Returns the names written.  ``run`` is
    kept for callers that inject a runner; the copy itself needs none.
    """
    unit_dir = Path(unit_dir)
    wrote = []
    for src in own_unit_files(sigmond_repo):
        dst = unit_dir / src.name
        if dst.is_file() and not _differs(src, dst):
            continue
        fd, tmp = tempfile.mkstemp(dir=unit_dir, prefix=f".{src.name}.")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(src.read_bytes())
            os.chmod(tmp, 0o644)
            os.replace(tmp, dst)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        wrote.append(src.name)
    return wrote

"""Which radiod instances are configured, in either of radiod's layouts.

radiod reads ``/etc/radio/radiod@<id>.conf`` or, in Phil's split layout, the
``*.conf`` files of ``/etc/radio/radiod@<id>.conf.d/`` (global.conf,
rx888.conf, ...).  Every place that asks "which radiods exist?" must see both:
code that globbed only ``radiod@*.conf`` marked a split-layout radiod orphaned
and stripped its CPU pinning (KFS-OMNI, 2026-11-13 to 11-28).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Union

CONF_SUFFIX = ".conf"
CONF_D_SUFFIX = ".conf.d"


def _conf_d_parts(d: Path) -> list:
    """The files radiod reads from a .conf.d: its regular *.conf, by name."""
    try:
        return sorted(p for p in d.glob("*" + CONF_SUFFIX)
                      if p.is_file() and not p.is_symlink())
    except OSError:
        return []


def radiod_conf_instances(conf_dir: Union[str, Path],
                          base: str = "radiod") -> Dict[str, Path]:
    """{instance: path} for every configured ``<base>@<instance>``.

    The path is the ``.conf`` file, or the ``.conf.d`` directory when there
    is no file.  A ``.conf.d`` counts only when it holds at least one
    ``*.conf`` (radiod reads nothing else from it).  Symlinks and editor
    backups (``.conf~``, ``.conf.bak-*``) are not instances.  Unreadable or
    missing ``conf_dir`` -> {}.
    """
    conf_dir = Path(conf_dir)
    prefix = base + "@"
    found: Dict[str, Path] = {}
    try:
        entries = list(conf_dir.iterdir())
    except OSError:
        return found
    for p in entries:
        name = p.name
        if not name.startswith(prefix) or p.is_symlink():
            continue
        if name.endswith(CONF_D_SUFFIX):
            inst = name[len(prefix):-len(CONF_D_SUFFIX)]
            if inst and p.is_dir() and _conf_d_parts(p):
                found.setdefault(inst, p)
        elif name.endswith(CONF_SUFFIX):
            inst = name[len(prefix):-len(CONF_SUFFIX)]
            if inst and p.is_file():
                found[inst] = p          # a .conf file wins over a .conf.d
    return found


def read_radiod_conf(path: Union[str, Path]) -> str:
    """The configuration text radiod reads at ``path``: the file itself, or a
    .conf.d's ``*.conf`` files joined in name order.  OSError propagates for
    an unreadable file, as Path.read_text would."""
    path = Path(path)
    if path.is_dir():
        return "\n".join(p.read_text() for p in _conf_d_parts(path))
    return path.read_text()

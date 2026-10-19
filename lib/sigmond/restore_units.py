"""Which installed unit files a rollback retires (release v3.73 row 4).

`smd admin manifest restore` moves a component back to an older commit and
then runs that commit's install.sh, as `smd update` runs install.sh after a
pull.  The older install.sh rewrites every unit file it knows, so a unit the
newer release changed goes back to the older bytes.  It cannot remove a unit
the older release never shipped: that file stays in /etc/systemd/system, and
its timer goes on firing against code that no longer exists.

plan_retire names those files.  It stays pure (no filesystem, no process
state): the caller lists the paths that the newer commit has and the older
lacks (`git diff --name-only --diff-filter=D <newer> <older>`), and passes
readers for the newer commit's bytes and for the installed bytes.

A file qualifies only when its installed bytes equal the newer commit's
bytes exactly.  Equal bytes at the path install.sh writes say that this
release installed the file.  A file somebody edited after install, or one
that came from elsewhere under the same name, stays put; plan_retire names
it with the reason, and the caller tells the operator.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

#: Files under systemd/ that install.sh copies to <systemd_dir>/<basename>.
#: A .conf drop-in has no fixed home and needs an entry in ``dest_map``.
UNIT_SUFFIXES = ('.service', '.timer', '.socket', '.path', '.target')


def _dest_for(path: str, dest_map: dict, systemd_dir: Path) -> Optional[Path]:
    if not path.startswith('systemd/') or path.count('/') != 1:
        return None
    name = path.split('/', 1)[1]
    if name in dest_map:
        return systemd_dir / dest_map[name]
    if name.endswith(UNIT_SUFFIXES):
        return systemd_dir / name
    return None


def plan_retire(removed: list, *,
                read_newer: Callable[[str], Optional[bytes]],
                read_installed: Callable[[Path], Optional[bytes]],
                dest_map: dict, systemd_dir: Path):
    """Return ``(retire, kept)``.

    ``retire`` lists the installed paths to disable and remove.  ``kept``
    lists ``(path, why)`` for each installed file left in place.
    ``removed`` holds repo-relative paths present in the newer commit and
    absent from the older.  ``read_newer(path)`` returns the newer commit's
    bytes, or None when git cannot show them; ``read_installed(dest)``
    returns the installed bytes, or None when nothing sits there.
    ``dest_map`` maps a systemd/ basename to its path below
    ``systemd_dir`` for a file install.sh renames (radiod's drop-ins).
    """
    retire, kept = [], []
    for path in removed:
        dest = _dest_for(path, dest_map, systemd_dir)
        if dest is None:
            continue
        have = read_installed(dest)
        if have is None:
            continue
        shipped = read_newer(path)
        if shipped is None:
            kept.append((dest, f'cannot read {path} at the newer commit'))
        elif have == shipped:
            retire.append(dest)
        else:
            kept.append((dest, f'differs from the {path} the newer release '
                               f'shipped (edited since install?)'))
    return retire, kept

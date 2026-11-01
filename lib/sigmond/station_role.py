"""Station role: one file says whether this host is `full` or `radiod-only`."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

ROLE_PATH = Path('/etc/sigmond/station-role')
RADIOD_ONLY = 'radiod-only'
FULL = 'full'
ALLOWED_ON_RADIOD_ONLY = frozenset({'ka9q-radio', 'igmp-querier', 'ka9q-web'})


def role(path: Path = ROLE_PATH) -> str:
    """Return the station role; anything missing or unrecognised is `full`."""
    try:
        text = Path(path).read_text().strip()
    except (OSError, ValueError):
        return FULL
    return RADIOD_ONLY if text == RADIOD_ONLY else FULL


def set_role(name: str, path: Path = ROLE_PATH) -> bool:
    """Persist the role atomically. True when the file changed."""
    if name not in (RADIOD_ONLY, FULL):
        raise ValueError(f"unknown station role {name!r}; "
                         f"expected {FULL!r} or {RADIOD_ONLY!r}")
    path = Path(path)
    try:
        if path.read_text().strip() == name:
            return False
    except (OSError, ValueError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.station-role.')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(name + '\n')
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def refusal(component: str, current: str) -> Optional[str]:
    """None if `component` may run under role `current`; else why not."""
    if current != RADIOD_ONLY or component in ALLOWED_ON_RADIOD_ONLY:
        return None
    return (f"{component} is not run on a radiod-only host "
            f"(this host's role is {current}); "
            f"to run it here, set {ROLE_PATH} to {FULL}.")

"""Per-instance report rows for `smd admin instance list --json`.

Each row pairs an instance's file facts with the state systemd reports for
its unit.  A failed systemctl call reads "unknown", never "active".
"""
import subprocess
from typing import Callable, Optional

from sigmond import instance as _instance

UNKNOWN = "unknown"
_TIMEOUT_S = 5


def _unit_word(verb: str, unit: str, run: Callable) -> str:
    """Return systemctl <verb>'s stdout word, or "unknown" on any failure.

    is-enabled and is-active exit non-zero for disabled, inactive and
    not-found units but still print the word, so the exit code is ignored.
    """
    try:
        proc = run(["systemctl", verb, unit],
                   stdin=subprocess.DEVNULL, capture_output=True,
                   text=True, timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    lines = (proc.stdout or "").strip().splitlines()
    return lines[0].strip() if lines else UNKNOWN


def instance_rows(instances: list, run: Optional[Callable] = None) -> list:
    # Resolve subprocess.run at call time so a patched one takes effect.
    run = run or subprocess.run
    rows = []
    for i in instances:
        unit = i.paths.unit_name
        rows.append({
            "client": i.client,
            "reporter_id": i.reporter_id,
            "display_id": _instance.display_reporter_id(i.reporter_id),
            "unit": unit,
            "has_config": i.has_config,
            "has_env": i.has_env,
            "has_sources": i.has_sources,
            "enabled": _unit_word("is-enabled", unit, run),
            "active": _unit_word("is-active", unit, run),
        })
    return rows

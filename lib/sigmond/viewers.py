"""The station's web viewers, for `smd admin viewers --json`.

Two kinds run here: one ka9q-web per local radiod (spectrum and waterfall),
and gmag-webui (magnetometer).  Each row carries the port and systemd unit so
a front page can link to it.  Two viewers on one port show up as a conflict.
smd skips the ports other viewers own when it numbers ka9q-web (the second
radiod gets 8083, since gmag-webui holds 8082), so none is expected.
"""
import subprocess
from typing import Callable

from sigmond import gmag_webui

UNKNOWN = "unknown"
_TIMEOUT_S = 5


def systemctl_is_active(unit: str) -> str:
    """`systemctl is-active <unit>`'s word, or "unknown" on failure.

    is-active exits non-zero for inactive and failed units but still prints
    the word, so the exit code is ignored.
    """
    try:
        # Resolve subprocess.run at call time so a patched one takes effect.
        proc = subprocess.run(["systemctl", "is-active", unit],
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    lines = (proc.stdout or "").strip().splitlines()
    return lines[0].strip() if lines else UNKNOWN


def list_viewers(ka9q_targets: list, gmag_installed: bool,
                 is_active: Callable[[str], str]) -> list:
    def state(unit: str) -> str:
        try:
            return is_active(unit) or UNKNOWN
        except Exception:
            return UNKNOWN

    rows = []
    for i, (unit, status_dns, port) in enumerate(ka9q_targets, start=1):
        rows.append({
            "name": "ka9q-web" if i == 1 else f"ka9q-web-{i}",
            "label": f"Spectrum and waterfall ({status_dns})",
            "port": port,
            "unit": unit,
            "active": state(unit),
            "description": f"ka9q-web spectrum and waterfall for the radiod "
                           f"at {status_dns}",
        })
    if gmag_installed:
        rows.append({
            "name": gmag_webui.COMPONENT,
            "label": "Magnetometer",
            "port": gmag_webui.PORT,
            "unit": gmag_webui.SERVICE,
            "active": state(gmag_webui.SERVICE),
            "description": "Real-time magnetometer dashboard (gmag-webui)",
        })
    return rows


def port_conflicts(rows: list) -> list:
    """[{port, names}] for each port that two or more viewers claim."""
    by_port: dict = {}
    for r in rows:
        by_port.setdefault(r["port"], []).append(r["name"])
    return [{"port": p, "names": n}
            for p, n in sorted(by_port.items()) if len(n) > 1]

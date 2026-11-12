"""The station's web viewers, for `smd admin viewers --json`.

Two kinds run here: one ka9q-web per local radiod (spectrum and waterfall),
and gmag-webui (magnetometer).  Each row carries the port and systemd unit so
a front page can link to it.  Two viewers on one port show up as a conflict.
smd skips the ports other viewers own when it numbers ka9q-web (the second
radiod gets 8083, since gmag-webui holds 8082), so none is expected.
"""
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

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


def installed_port(unit: str, port: int, root: Path = Path("/")) -> int:
    """The port this ka9q-web unit really listens on, as installed.

    `_ka9q_web_targets` recomputes each port from today's numbering rules,
    but a unit keeps the port it was written with: KFS-OMNI-SW (2026-11-11)
    ran ka9q-web@KFS-SW on 8084 from its 11-05 env file while this listing
    said 8083, where Phil's web-monitor listens, so the station page linked
    to the wrong service.  The templated unit reads KA9Q_WEB_PORT from
    /etc/sigmond/ka9q-web/<id>.env; the plain one carries `-p N` in its
    ExecStart.  Falls back to ``port`` when nothing is installed yet.
    """
    try:
        if unit.startswith("ka9q-web@") and unit.endswith(".service"):
            inst = unit[len("ka9q-web@"):-len(".service")]
            text = (root / "etc/sigmond/ka9q-web" / f"{inst}.env").read_text()
            m = re.search(r"^KA9Q_WEB_PORT=(\d+)\s*$", text, re.M)
        else:
            text = (root / "etc/systemd/system" / unit).read_text()
            m = re.search(r"^ExecStart=.*\s-p\s+(\d+)\b", text, re.M)
    except OSError:
        return port
    return int(m.group(1)) if m else port


def reporter_labels(coord) -> dict:
    """{status_dns: instance key} for each radiod whose client instances
    share one reporter ID.

    A ka9q-web link reads better as the reporter the radiod serves than as
    its multicast name: KFS shows KFS-OMNI and KFS-SW (reporter IDs KFS/OMNI
    and KFS/SW), while AC0G-B2 showed ac0g-b1-a-status.local where AC0G-B1A
    belongs (2026-11-11).  The key form (`/` -> `-`, instance.instance_key)
    matches the service names.  A radiod whose instances disagree, or that
    feeds none (a station-wide "" or "default" instance), gets no entry and
    keeps its radiod name.
    """
    from sigmond.instance import instance_key
    out = {}
    for rid, r in (getattr(coord, "radiods", None) or {}).items():
        keys = set()
        for c in coord.instances_bound_to(rid):
            if c.instance in ("", "default"):
                continue
            try:
                keys.add(instance_key(c.instance))
            except Exception:
                continue
        dns = getattr(r, "effective_status_dns", None)
        if len(keys) == 1 and dns:
            out[dns] = keys.pop()
    return out


def rac_lines(rows: list) -> str:
    """`<label> <port>` per ka9q-web row, primary first: what sigmond-rac
    tunnels and the dashboard shows as each link's name."""
    lines = []
    for r in rows:
        if r["name"] == "ka9q-web" or r["name"].startswith("ka9q-web-"):
            label = r.get("reporter") or re.sub(r"-status\.local$", "",
                                                r.get("radiod") or "-")
            lines.append(f"{label} {r['port']}")
    return "\n".join(lines) + ("\n" if lines else "")


def list_viewers(ka9q_targets: list, gmag_installed: bool,
                 is_active: Callable[[str], str],
                 reporters: Optional[dict] = None) -> list:
    def state(unit: str) -> str:
        try:
            return is_active(unit) or UNKNOWN
        except Exception:
            return UNKNOWN

    rows = []
    reporters = reporters or {}
    for i, (unit, status_dns, port) in enumerate(ka9q_targets, start=1):
        reporter = reporters.get(status_dns)
        rows.append({
            "name": "ka9q-web" if i == 1 else f"ka9q-web-{i}",
            "label": f"Spectrum and waterfall ({reporter or status_dns})",
            "radiod": status_dns,
            "reporter": reporter,
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

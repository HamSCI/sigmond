"""Adopted-manifest status, per live component.

`smd admin manifest status --json` prints `manifest_status()`.  It reuses
`heartbeat._read_manifest`, so this verb and `smd doctor` cannot disagree
about the same manifest file.  Read-only; stdlib only.
"""
from __future__ import annotations

from typing import Optional

from . import align, heartbeat
from .provenance import IMAGE_VERSION_FILE


def manifest_status(paths: Optional[heartbeat.HeartbeatPaths] = None,
                    version_file: str = IMAGE_VERSION_FILE) -> dict:
    """Return ``{present, release, source, components}``.

    ``components`` maps every live component to ``match``, ``moved``,
    ``superset`` or ``live_only``, plus each ``manifest_only`` entry.  A
    live component absent from the drift list reads ``match``.  With no
    usable manifest, ``present`` is False and ``components`` is empty:
    nothing was compared, so nothing may read as a match.
    """
    paths = paths or heartbeat.HeartbeatPaths()
    block = heartbeat._read_manifest(paths)
    out = {
        "present": bool(block.get("present")),
        "release": align.recorded_release(version_file),
        "source": block.get("blessed_source"),
        "components": {},
    }
    if not out["present"]:
        return out
    drift = {d["component"]: d["status"] for d in block.get("drift", [])}
    live = heartbeat._read_versions(paths)["components"]
    comps = {name: drift.get(name, "match") for name in sorted(live)}
    for name, status in drift.items():
        if status == "manifest_only":
            comps[name] = status
    out["components"] = comps
    return out

"""Device-reading clients and the device on this machine (v3.94 Task 8).

The catalog marks a client ``hardware_gated`` when it reads a device on
this machine's USB: mag-recorder and gmag-webui (the magnetometer),
gpsdo-monitor (a USB GPSDO).  hf-timestd never counts, whatever the
catalog says: it works from the RF input, and its GPSDO or TS-1 may sit
on another machine (B2, KFS-MAIN, SIGMA behind B4).

"Seen before" means the client has produced output from the device at
least once (docs/superpowers/specs/2026-11-19-run-with-purpose-design.md,
"What each decision does").  Two sources, either one counts:

* sigmond's own sighting record, ``/var/lib/sigmond/purpose/<client>.<input>``
  (``record_sighting``), which `smd align --apply` writes, through
  safe_write, each time it reads the device present.  It outlives the
  client's output: mag-recorder keeps its spool 7 days, so without it a
  magnetometer lost for a week read "never seen" and its clients
  switched off (final review M6).
* the client's past output:

* mag-recorder: a ``samples-*.jsonl`` file under its ``[paths] spool_dir``
  (/etc/mag-recorder/mag-recorder-config.toml; default
  /var/lib/mag-recorder), or a packaged ``OBS*.zip`` held in its
  ``[paths] upload_queue_dir`` (default /var/lib/mag-recorder/upload).  A
  config or directory that cannot be read counts as seen.
* gmag-webui: mag-recorder's evidence (the same magnetometer).
* gpsdo-monitor: the station's location came from a GPSDO read here.
  Its own record of a fix, /run/gpsdo/<serial>.json, lives on tmpfs and
  goes at each reboot; sigmond's location record persists.  The reader is
  drift.publish's ``pos_source``: ``location_source = "gpsdo"`` in the
  site profile's [station] or coordination's [host] (`smd config location
  --from-gps` writes it), else sigmond-location-check's GPSDO marker on
  the grid_square line.  Anything else (operator, a typed grid, nothing)
  reads never seen: a station behind a shared GPSDO switches it off.

``device_items`` turns "enabled, device absent, seen before" into one
drift fault per client, for the drift report and so `smd check`.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Callable, Mapping, Optional

from sigmond.drift.model import Item

MAG_CONFIG = Path("/etc/mag-recorder/mag-recorder-config.toml")
MAG_SPOOL_DEFAULT = Path("/var/lib/mag-recorder")
# sigmond's own record of each device read present (final review M6)
PURPOSE_DIR = Path("/var/lib/sigmond/purpose")

# Never gated on local USB, even if a catalog layer marks it.
NEVER_GATED = frozenset({"hf-timestd"})

# A client whose evidence is another client's: the same device.
EVIDENCE_OF = {"gmag-webui": "mag-recorder"}


def gated_components(catalog) -> dict:
    """{name: hardware label} for each catalog entry marked
    ``hardware_gated``, less NEVER_GATED."""
    out = {}
    for name, entry in dict(catalog).items():
        label = getattr(entry, "hardware_gated", None)
        if label and name not in NEVER_GATED:
            out[name] = label
    return out


# The input each client's sighting record names.
SIGHTING_INPUT = {"mag-recorder": "magnetometer", "gpsdo-monitor": "gpsdo"}


def _mag_path(key: str, default: Path):
    """A mag-recorder ``[paths]`` entry, or its default when the config is
    absent or names none.  None when the config exists but cannot be read
    or parsed: then the directory is unknown."""
    try:
        with open(MAG_CONFIG, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        return default
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = (cfg.get("paths") or {}).get(key)
    return Path(value) if isinstance(value, str) and value else default


def mag_spool_dir():
    """mag-recorder's ``[paths] spool_dir`` (``_mag_path``)."""
    return _mag_path("spool_dir", MAG_SPOOL_DEFAULT)


def mag_upload_queue_dir():
    """mag-recorder's ``[paths] upload_queue_dir`` (``_mag_path``)."""
    return _mag_path("upload_queue_dir", MAG_SPOOL_DEFAULT / "upload")


def sighting_path(name: str) -> Optional[Path]:
    """sigmond's sighting record for ``name``'s device, or None for a
    client that has none."""
    name = EVIDENCE_OF.get(name, name)
    what = SIGHTING_INPUT.get(name)
    return PURPOSE_DIR / f"{name}.{what}" if what else None


def record_sighting(name: str, now=None) -> bool:
    """Record that ``name``'s device read present now: one TOML line,
    0644, written through safe_write (a link at the record is replaced,
    never followed; a linked directory is refused).  Root only, under
    `smd align --apply`.  Returns False when it could not be written."""
    from datetime import datetime, timezone
    from sigmond.safe_write import write_owned
    path = sighting_path(name)
    if path is None:
        return False
    at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        if not os.path.lexists(path.parent):
            os.mkdir(path.parent, 0o755)
        write_owned(path, f'last_present = "{at}"\n', mode=0o644,
                    owner_from_dir=False)
    except (OSError, ValueError):
        return False
    return True


def _has(dirpath, match) -> Optional[bool]:
    """Whether ``dirpath`` holds a name ``match`` accepts; None when it
    cannot be read (cannot rule it out), False when it is absent."""
    if dirpath is None:
        return None
    try:
        names = os.listdir(dirpath)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return any(match(n) for n in names)


def seen_before(name: str) -> bool:
    """True when ``name`` has recorded from its device here before, or
    when that cannot be ruled out (the module docstring)."""
    name = EVIDENCE_OF.get(name, name)
    record = sighting_path(name)
    if record is not None and os.path.isfile(record):
        return True
    if name == "mag-recorder":
        samples = _has(mag_spool_dir(), lambda n: n.startswith("samples-")
                       and n.endswith(".jsonl"))
        zips = _has(mag_upload_queue_dir(), lambda n: n.startswith("OBS")
                    and n.endswith(".zip"))
        # Unreadable (None) cannot rule it out, so it counts as seen.
        return samples is not False or zips is not False
    if name == "gpsdo-monitor":
        return gpsdo_location_recorded()
    return True


def gpsdo_location_recorded() -> bool:
    """True when sigmond recorded the station's location from a GPSDO."""
    from sigmond import coordination, site_profile
    from sigmond.drift.publish import pos_source
    try:
        coord = coordination.load_coordination(coordination.COORDINATION_PATH)
    except Exception:                                  # noqa: BLE001
        coord = None   # the site profile and the marker still answer
    return pos_source(coord, site_profile.SITE_PROFILE_PATH) == "gpsdo"


def missing_detail(name: str, label: str) -> str:
    return f"{label} missing; {name} has recorded from it before"


def device_items(enabled: Mapping[str, bool], *, gated: Mapping[str, str],
                 ready: Callable[[str], Optional[bool]],
                 seen: Callable[[str], bool]) -> list:
    """One fault per topology-enabled gated client whose device reads
    absent (``ready`` False, never None) and that has recorded from it
    before."""
    out = []
    for name in sorted(gated):
        if name in NEVER_GATED or not enabled.get(name):
            continue
        if ready(name) is False and seen(name):
            label = gated[name]
            out.append(Item(
                "unit", "fault", name, missing_detail(name, label),
                f"reconnect the {label}; if it is gone for good, "
                f"smd disable {name}"))
    return out

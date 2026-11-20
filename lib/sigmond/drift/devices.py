"""Device-reading clients and the device on this machine (v3.94 Task 8).

The catalog marks a client ``hardware_gated`` when it reads a device on
this machine's USB: mag-recorder and gmag-webui (the magnetometer),
gpsdo-monitor (a USB GPSDO).  hf-timestd never counts, whatever the
catalog says: it works from the RF input, and its GPSDO or TS-1 may sit
on another machine (B2, KFS-MAIN, SIGMA behind B4).

"Seen before" means the client has produced output from the device at
least once (docs/superpowers/specs/2026-11-19-run-with-purpose-design.md,
"What each decision does").  v3.94 reads only the client's past output:

* mag-recorder: a ``samples-*.jsonl`` file under its ``[paths] spool_dir``
  (/etc/mag-recorder/mag-recorder-config.toml; default
  /var/lib/mag-recorder).  A spool that cannot be read counts as seen.
* gmag-webui: mag-recorder's evidence (the same magnetometer).
* gpsdo-monitor: always seen.  Its only record of a fix,
  /run/gpsdo/<serial>.json, lives on tmpfs and goes at each reboot, so an
  empty /run/gpsdo cannot show the device was never there.  A missing
  GPSDO faults; it never switches gpsdo-monitor off.

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


def mag_spool_dir() -> Path:
    """mag-recorder's ``[paths] spool_dir``, or its default."""
    try:
        with open(MAG_CONFIG, "rb") as f:
            cfg = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return MAG_SPOOL_DEFAULT
    spool = (cfg.get("paths") or {}).get("spool_dir")
    return Path(spool) if isinstance(spool, str) and spool else MAG_SPOOL_DEFAULT


def seen_before(name: str) -> bool:
    """True when ``name`` has recorded from its device here before, or
    when that cannot be ruled out (the module docstring)."""
    name = EVIDENCE_OF.get(name, name)
    if name == "mag-recorder":
        try:
            names = os.listdir(mag_spool_dir())
        except FileNotFoundError:
            return False
        except OSError:
            return True       # unreadable: cannot rule it out
        return any(n.startswith("samples-") and n.endswith(".jsonl")
                   for n in names)
    return True


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

"""smd timing — one-shot display of the station's timing authority + accuracy.

Read-only companion to the TUI's Timing & Authority screen and to
``smd admin timing`` (the chain reconciler).  Prints the adjudicated
authority (tier, RTP->UTC offset, sigma, governor, stations) from
hf-timestd's authority.json, and the system-clock side from chronyc —
the two numbers an operator or script actually wants:

    $ smd timing
    Timing authority (hf-timestd)
      tier       T3   (available: T3,T4 · witnesses: T4)
      offset     +92.012 us    sigma ±3.26 ms
      governor   AC0G-B4-status.local
      stations   WWV, WWVH
      published  4 s ago
    System clock (chrony)
      reference  FUSE   stratum 1   offset +1.8 us   rms 72 us

``--json`` emits a machine-readable merge of both.  Stdlib-only, per
smd core policy (sigmond.tui.format is plain stdlib).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sigmond.tui.format import (
    AUTHORITY_JSON_PATH,
    format_age_seconds,
    format_offset_ns,
    format_sigma_ns,
    read_authority_snapshot,
    snapshot_age_seconds,
)


def _chrony_tracking() -> dict:
    """Parse ``chronyc -c tracking`` into a small dict; {} when unavailable."""
    try:
        out = subprocess.run(
            ["chronyc", "-c", "tracking"],
            capture_output=True, text=True, timeout=3.0, check=True,
        ).stdout.strip().splitlines()
        f = out[0].split(",") if out else []
        if len(f) < 14:
            return {}
        return {
            "reference": f[1] or f[0],
            "stratum": int(f[2]),
            "system_offset_s": float(f[4]),
            "rms_offset_s": float(f[6]),
            "root_dispersion_s": float(f[11]),
            "leap_status": f[13],
        }
    except Exception:
        return {}


def _fmt_s(seconds: float) -> str:
    a = abs(seconds)
    if a < 1e-3:
        return f"{seconds * 1e6:+.1f} us"
    if a < 1.0:
        return f"{seconds * 1e3:+.3f} ms"
    return f"{seconds:+.3f} s"


def cmd_timing_show(args) -> int:
    path = Path(getattr(args, "path", None) or AUTHORITY_JSON_PATH)
    snap, snap_err = read_authority_snapshot(path=path)
    chrony = _chrony_tracking()

    if getattr(args, "json", False):
        d = {"authority": None, "chrony": chrony or None,
             "authority_path": str(path)}
        if snap is not None:
            d["authority"] = {
                "t_level_active": snap.t_level_active,
                "t_level_available": snap.t_level_available,
                "t_level_witnesses": snap.t_level_witnesses,
                "rtp_to_utc_offset_ns": snap.rtp_to_utc_offset_ns,
                "sigma_ns": snap.sigma_ns,
                "governor_radiod": snap.governor_radiod,
                "stations_contributing": snap.stations_contributing,
                "a_level": snap.a_level,
                "disagreement_flags": snap.disagreement_flags,
                "age_s": snapshot_age_seconds(snap),
            }
        print(json.dumps(d, indent=2))
        return 0

    print("Timing authority (hf-timestd)")
    if snap is None:
        print(f"  no authority snapshot at {path} ({snap_err or 'unknown'})")
        print("  (not an hf-timestd host, service down, or file stale-deleted)")
    else:
        avail = ",".join(snap.t_level_available or []) or "-"
        wit = ",".join(snap.t_level_witnesses or []) or "-"
        print(f"  tier       {snap.t_level_active or '(none)'}   "
              f"(available: {avail} · witnesses: {wit})")
        print(f"  offset     {format_offset_ns(snap.rtp_to_utc_offset_ns)}    "
              f"sigma {format_sigma_ns(snap.sigma_ns)}")
        if snap.governor_radiod:
            print(f"  governor   {snap.governor_radiod}")
        if snap.stations_contributing:
            print(f"  stations   {', '.join(snap.stations_contributing)}")
        if snap.disagreement_flags:
            print(f"  flags      {', '.join(snap.disagreement_flags)}")
        print(f"  published  {format_age_seconds(snapshot_age_seconds(snap))}")

    print("System clock (chrony)")
    if not chrony:
        print("  chronyc unavailable")
    else:
        print(f"  reference  {chrony['reference']}   stratum {chrony['stratum']}   "
              f"offset {_fmt_s(chrony['system_offset_s'])}   "
              f"rms {_fmt_s(chrony['rms_offset_s']).lstrip('+')}   "
              f"leap {chrony['leap_status']}")
    return 0

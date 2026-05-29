"""Magnetometer probe — local-filesystem health check for declared
USB magnetometer sensors.

Observes three things per sensor:

  device_present       udev-stable symlink (e.g. /dev/ttyMAG0) resolves.
  last_sample_age_sec  Age of the most recent samples-*.jsonl in the
                       consumer's /var/lib/<consumer>/ spool.  None if
                       no samples file exists.
  upload_queue_depth   Count of zip files queued in
                       /var/lib/<consumer>/upload/ — a depth that keeps
                       growing means the PSWS SFTP path is broken
                       (see /home/timestd/.ssh/id_rsa_psws for the
                       shared identity, or the network reachability of
                       pswsnetwork.eng.ua.edu:22).

Convention: the spool layout
  /var/lib/<consumer>/samples-YYYY-MM-DD.jsonl   (daily rotation)
  /var/lib/<consumer>/upload/<dataset>.zip       (packaged, awaiting SFTP)
is mag-recorder's only consumer-specific assumption today.  If a
second magnetometer-using client lands later, factor the spool layout
out into ``DeclaredMagnetometer`` fields or the catalog.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from ..environment import Environment, Observation


def probe(env: Environment, *,
          timeout: float = 1.0,
          limiter=None,
          ) -> list[Observation]:
    now = time.time()
    out: list[Observation] = []
    for m in env.magnetometers:
        if m.host not in ("localhost", "127.0.0.1", "::1", ""):
            # Remote sensors aren't in scope for v1 — same shape as
            # gpsdo.probe(), which only probes localhost authority files.
            continue

        endpoint = m.device or m.id
        fields: dict = {}
        ok = True
        err = ""

        device_present = _device_present(m.device)
        fields["device_present"] = device_present

        if m.consumer:
            spool = Path(f"/var/lib/{m.consumer}")
            age = _latest_sample_age_sec(spool, now)
            fields["last_sample_age_sec"] = age
            fields["upload_queue_depth"] = _upload_queue_depth(spool)
            fields["consumer"] = m.consumer
        else:
            # No declared consumer — only the device-present check is
            # meaningful.  Don't synthesize a "no samples" delta.
            fields["last_sample_age_sec"] = None
            fields["upload_queue_depth"] = 0

        if not device_present and m.device:
            ok = False
            err = f"device path missing: {m.device}"

        out.append(Observation(
            source="magnetometer", kind="magnetometer", id=m.id,
            endpoint=endpoint, fields=fields,
            observed_at=now, ok=ok, error=err,
        ))
    return out


def _device_present(path: str) -> bool:
    if not path:
        return False
    try:
        return Path(path).exists()
    except (OSError, PermissionError):
        return False


def _latest_sample_age_sec(spool: Path, now: float) -> Optional[float]:
    try:
        candidates = list(spool.glob("samples-*.jsonl"))
    except (OSError, PermissionError):
        return None
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: _safe_mtime(p))
    mtime = _safe_mtime(newest)
    if mtime <= 0:
        return None
    return max(0.0, now - mtime)


def _upload_queue_depth(spool: Path) -> int:
    try:
        return sum(1 for _ in (spool / "upload").glob("*.zip"))
    except (OSError, PermissionError):
        return 0


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except (OSError, PermissionError):
        return 0.0

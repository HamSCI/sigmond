"""Publish a drift report: /var/lib/sigmond/drift.json and one sigmondb row.

`smd align --report` (the daily timer) and the end of `smd align --apply`
call ``publish``.  The JSON file always lands first.  The sink row is best
effort: a sink that cannot take it costs one printed line, never the file.

The row also names the station: a stable ``host_key`` (it survives a
rename), the hostname, and one entry per receiver with its grid6.  Only the
grid6 leaves the station here; no latitude or longitude is in the row.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
from pathlib import Path
from typing import Optional

DRIFT_JSON = Path("/var/lib/sigmond/drift.json")
MACHINE_ID = Path("/etc/machine-id")
GPSDO_DIR = Path("/run/gpsdo")
SCHEMA_VERSION = 1
MAX_FAULTS = 50
DETAIL_MAX = 200


def host_key(machine_id: Optional[Path] = None) -> str:
    """First 12 hex digits of sha256 of the machine id; '' when unreadable."""
    path = Path(machine_id) if machine_id is not None else MACHINE_ID
    try:
        text = path.read_text().strip()
    except OSError:
        return ""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _ascii(s) -> str:
    return str(s).encode("ascii", "replace").decode("ascii")


def maidenhead(lat: float, lon: float) -> str:
    """Six-character grid square for a position."""
    lon = min(max(lon + 180.0, 0.0), 359.999999)
    lat = min(max(lat + 90.0, 0.0), 179.999999)
    f_lon, f_lat = int(lon // 20), int(lat // 10)
    s_lon, s_lat = int((lon % 20) // 2), int(lat % 10)
    ss_lon = int(((lon % 2) * 12) // 1)
    ss_lat = int(((lat % 1) * 24) // 1)
    return (chr(65 + f_lon) + chr(65 + f_lat) + str(s_lon) + str(s_lat)
            + chr(97 + ss_lon) + chr(97 + ss_lat))


def _gpsdo_has_fix(gpsdo_dir: Path) -> bool:
    try:
        files = sorted(Path(gpsdo_dir).glob("*.json"))
    except OSError:
        return False
    for f in files:
        if f.name.endswith("index.json"):
            continue
        try:
            health = json.loads(f.read_text()).get("health", {})
        except (OSError, ValueError, AttributeError):
            continue
        return health.get("gps_fix") in ("2D", "3D")
    return False


def position(coord, *, gpsdo_dir: Optional[Path] = None) -> tuple:
    """(grid6, pos_source) for this host.

    Nothing records where a location came from, so this infers it.
    Coordination holds lat/lon only from a GPS fix (`smd config location`)
    or an explicit `smd config identity --lat/--lon`; with a GPSDO that
    reports a fix right now, the position counts as ``gpsdo``.  Otherwise
    ``operator``.  grid6 comes from lat/lon when present, else from the
    typed grid (shorter than six characters stays as typed)."""
    host = getattr(coord, "host", None)
    lat = float(getattr(host, "lat", 0.0) or 0.0)
    lon = float(getattr(host, "lon", 0.0) or 0.0)
    grid = (getattr(host, "grid", "") or "").strip()
    gdir = Path(gpsdo_dir) if gpsdo_dir is not None else GPSDO_DIR
    if lat or lon:
        src = "gpsdo" if _gpsdo_has_fix(gdir) else "operator"
        return maidenhead(lat, lon), src
    return grid[:6], "operator"


def receiver_identity(station: str, *, coord, receivers,
                      gpsdo_dir: Optional[Path] = None) -> list:
    """One {reporter_id, radiod_id, grid6, pos_source} per receiver served.

    The receivers come from the site profile's [[receiver]] list; a host
    with none serves one, named by ``station``."""
    from sigmond.instance import display_reporter_id, parse_user_reporter_id
    grid6, src = position(coord, gpsdo_dir=gpsdo_dir)
    out = []
    for r in receivers or ():
        rid = (getattr(r, "reporter_id", "") or "").strip()
        if not rid:
            continue
        try:
            rid = display_reporter_id(parse_user_reporter_id(rid))
        except ValueError:
            pass
        out.append({"reporter_id": rid,
                    "radiod_id": getattr(r, "radiod", "") or "",
                    "grid6": grid6, "pos_source": src})
    if not out:
        radiods = list(getattr(coord, "radiods", {}) or {})
        out.append({"reporter_id": station,
                    "radiod_id": radiods[0] if radiods else "",
                    "grid6": grid6, "pos_source": src})
    return out


def build_row(report, *, machine_id: Optional[Path] = None, coord=None,
              receivers=None, hostname: Optional[str] = None,
              gpsdo_dir: Optional[Path] = None) -> dict:
    faults = []
    for it in report.faults()[:MAX_FAULTS]:
        faults.append({"kind": it.kind, "subject": it.subject,
                       "detail": _ascii(it.detail)[:DETAIL_MAX],
                       "fix": _ascii(it.fix), "key": it.key})
    n_fault = sum(1 for i in report.items if i.severity == "fault")
    n_drift = sum(1 for i in report.items if i.severity == "drift")
    return {
        "time": report.generated_at,
        "reporter_id": report.station,
        "host_key": host_key(machine_id),
        "hostname": hostname if hostname is not None else socket.gethostname(),
        "receivers_json": json.dumps(
            receiver_identity(report.station, coord=coord, receivers=receivers,
                              gpsdo_dir=gpsdo_dir), sort_keys=True),
        "release": report.release,
        "baseline": report.baseline,
        "n_fault": n_fault,
        "n_drift": n_drift,
        "n_accepted": len(report.accepted),
        "counts_json": json.dumps(report.counts(), sort_keys=True),
        "faults_json": json.dumps(faults),
        "reminders_json": json.dumps(list(report.reminders), default=str),
        "schema_version": SCHEMA_VERSION,
    }


def write_json_atomic(report, path: Optional[Path] = None) -> Path:
    """Write report.to_json() beside ``path`` then rename over it (0644)."""
    path = Path(path) if path is not None else DRIFT_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(report.to_json(), f, indent=2, ensure_ascii=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def make_writer():
    """The sink writer for sigmondb.station_drift (producer sigmond)."""
    from sigmond.hamsci_sink import Writer
    return Writer.from_env(table="station_drift", mode="sigmondb",
                           schema_version=SCHEMA_VERSION, producer="sigmond",
                           batch_rows=1)


def insert_row(row: dict, writer=None) -> bool:
    """Queue ``row`` and flush.  True when the sink holds it."""
    w = writer if writer is not None else make_writer()
    if getattr(w, "is_noop", False):
        return False
    w.insert([row])
    w.flush()
    ok = getattr(w, "buffered", 0) == 0
    close = getattr(w, "close", None)
    if close is not None:
        try:
            close()
        except Exception:  # noqa: BLE001
            pass
    return ok


def publish(report, *, path: Optional[Path] = None, writer=None,
            machine_id: Optional[Path] = None, coord=None, receivers=None,
            hostname: Optional[str] = None, gpsdo_dir: Optional[Path] = None,
            say=print) -> bool:
    """Write drift.json, then insert the row.  The file's failure raises;
    the row's failure says one line.  True when both landed."""
    write_json_atomic(report, path)
    try:
        if coord is None:
            from sigmond.coordination import load_coordination
            coord = load_coordination()
        if receivers is None:
            from sigmond.receivers import load_receivers
            receivers = load_receivers()
        row = build_row(report, machine_id=machine_id, coord=coord,
                        receivers=receivers, hostname=hostname,
                        gpsdo_dir=gpsdo_dir)
        if insert_row(row, writer):
            return True
        say("smd align --report: no sigmondb sink took the row; "
            "drift.json is written")
    except Exception as exc:  # noqa: BLE001
        say(f"smd align --report: sigmondb row failed: {_ascii(exc)}; "
            "drift.json is written")
    return False

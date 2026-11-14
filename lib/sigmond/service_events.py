"""Service restarts and memory (OOM) kills, per unit, over the last 24 h.

A self-healing failure must never be silent (Rob, 2026-11-12).  systemd's
NRestarts and the cgroup's memory.events reset on every manual restart, so
only the journal keeps the history.  A root timer
(sigmond-service-events.timer) reads it here and writes
/run/sigmond/service-events.json; smd check, smd status and the heartbeat
read that file (hamsci cannot read PID 1 or kernel journal lines).

Plan: docs/superpowers/plans/2026-11-14-restart-oom-reporting.md.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

STATUS_PATH = Path("/run/sigmond/service-events.json")
CGROUP_ROOT = Path("/sys/fs/cgroup/system.slice")
WINDOW_S = 86400
STALE_S = 900
PEAK_WARN_PCT = 85.0
RESTART_STORM = 3                  # more than this many automatic restarts warns

# systemd's catalogue message ids (stable across versions)
MSG_RESTART_SCHEDULED = "5eb03494b6584870a536b337290809b3"
MSG_STOPPING = "de5b426a63be47a7b6ac3eaac82e2f6f"
MSG_EXITED = "98e322203f7a4ed290d09fe03c09fe15"
MSG_FAILED = "d9b373ed55a64feb8242e02dbe79a49c"
_CAUSE_S = 5.0                     # a stop/exit this close before another event caused it


@dataclass
class UnitEvents:
    auto_restarts: int = 0
    manual_restarts: int = 0
    memcg_oom: int = 0
    unexpected_exits: int = 0
    last_utc: Optional[str] = None


def _utc(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unit_of_memcg(path: str) -> str:
    """The service a cgroup path belongs to.  Template instances sit one
    level down, in their own slice: /system.slice/system-wspr\\x2drecorder.slice/
    wspr-recorder@X.service, so take the last .service component."""
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] != "system.slice":
        return "-"
    for p in reversed(parts):
        if p.endswith(".service"):
            return p
    return "-"


def _kernel_oom(msg: str) -> Optional[Tuple[str, str, str]]:
    """(constraint, unit, task) from an `oom-kill:...` kernel line."""
    if not msg.startswith("oom-kill:"):
        return None
    fields = dict(kv.split("=", 1) for kv in msg[len("oom-kill:"):].split(",") if "=" in kv)
    return (fields.get("constraint", ""), _unit_of_memcg(fields.get("task_memcg", "")),
            fields.get("task", ""))


def parse_events(lines: Iterable[str], now: float, window_s: int = WINDOW_S
                 ) -> Tuple[Dict[str, UnitEvents], List[dict]]:
    events = []
    for ln in lines:
        try:
            d = json.loads(ln)
            t = int(d["__REALTIME_TIMESTAMP"]) / 1e6
        except (ValueError, KeyError, TypeError):
            continue
        if now - t > window_s or t > now + 60:
            continue
        events.append((t, d))
    events.sort(key=lambda e: e[0])

    units: Dict[str, UnitEvents] = {}
    glob: List[dict] = []
    last_cause: Dict[str, float] = {}     # unit -> time of its last exit or scheduled restart
    last_stop: Dict[str, float] = {}      # unit -> time of its last stopping job

    def u(name: str) -> UnitEvents:
        return units.setdefault(name, UnitEvents())

    for t, d in events:
        if d.get("_TRANSPORT") == "kernel":
            k = _kernel_oom(str(d.get("MESSAGE", "")))
            if not k:
                continue
            constraint, unit, task = k
            if constraint == "CONSTRAINT_MEMCG":
                u(unit).memcg_oom += 1
                u(unit).last_utc = _utc(t)
            else:
                glob.append({"utc": _utc(t), "unit": unit, "task": task})
            continue
        unit, mid = d.get("UNIT"), d.get("MESSAGE_ID")
        if not unit or not mid:
            continue
        if mid == MSG_RESTART_SCHEDULED:
            u(unit).auto_restarts += 1
            last_cause[unit] = t
        elif mid == MSG_STOPPING and d.get("JOB_TYPE") in ("restart", "stop"):
            if t - last_cause.get(unit, -1e18) > _CAUSE_S:
                u(unit).manual_restarts += 1
            last_stop[unit] = t
        elif mid == MSG_EXITED:
            clean = d.get("EXIT_CODE") == "exited" and str(d.get("EXIT_STATUS")) == "0"
            if not clean and t - last_stop.get(unit, -1e18) > _CAUSE_S:
                u(unit).unexpected_exits += 1
            last_cause[unit] = t
        else:
            continue
        u(unit).last_utc = _utc(t)
    return units, glob


def _service_dirs(cgroup_root: Path) -> Dict[str, Path]:
    """Every *.service cgroup under system.slice, template instances (one
    slice down) included."""
    out: Dict[str, Path] = {}
    try:
        for d in Path(cgroup_root).rglob("*.service"):
            if d.is_dir():
                out.setdefault(d.name, d)
    except OSError:
        pass
    return out


def memory_headroom(units: Iterable[str], cgroup_root: Path = CGROUP_ROOT
                    ) -> Dict[str, Tuple[int, Optional[int]]]:
    """(memory.peak, memory.max) per unit.  memory.peak is the high-water
    mark since the service last started, page cache included."""
    out = {}
    dirs = _service_dirs(cgroup_root)
    for unit in units:
        d = dirs.get(unit)
        if d is None:
            continue
        try:
            peak = int((d / "memory.peak").read_text().strip())
            mx = (d / "memory.max").read_text().strip()
        except (OSError, ValueError):
            continue
        out[unit] = (peak, None if mx == "max" else int(mx))
    return out


def _capped_units(cgroup_root: Path) -> List[str]:
    out = []
    for name, d in _service_dirs(cgroup_root).items():
        try:
            if (d / "memory.max").read_text().strip() != "max":
                out.append(name)
        except OSError:
            pass
    return sorted(out)


def collect(now: float, run=subprocess.run, cgroup_root: Path = CGROUP_ROOT,
            units: Optional[List[str]] = None) -> dict:
    doc = {"generated_at": _utc(now), "window_h": WINDOW_S // 3600, "units": {},
           "global_oom": [], "error": None}
    try:
        # Filtered at the source: PID 1 lines with one of the four message
        # ids (same-field matches OR, different fields AND), or kernel lines;
        # only the fields parse_events reads.  A busy station logs tens of
        # thousands of PID 1 lines a day.
        p = run(["journalctl", "-o", "json", "--no-pager", "--since", "-24h",
                 "--output-fields=MESSAGE,MESSAGE_ID,UNIT,JOB_TYPE,EXIT_CODE,EXIT_STATUS,_TRANSPORT",
                 "_PID=1", f"MESSAGE_ID={MSG_RESTART_SCHEDULED}", f"MESSAGE_ID={MSG_STOPPING}",
                 f"MESSAGE_ID={MSG_EXITED}", f"MESSAGE_ID={MSG_FAILED}",
                 "+", "_TRANSPORT=kernel"],
                capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise RuntimeError((p.stderr or "").strip()[:200] or f"journalctl exited {p.returncode}")
        per_unit, doc["global_oom"] = parse_events((p.stdout or "").splitlines(), now)
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        doc["error"] = f"{type(exc).__name__}: {exc}"
        return doc
    names = set(per_unit) | set(units if units is not None else _capped_units(cgroup_root))
    head = memory_headroom(sorted(names), cgroup_root)
    for name in sorted(names):
        ev = asdict(per_unit.get(name, UnitEvents()))
        peak, mx = head.get(name, (None, None))
        ev.update({"peak_bytes": peak, "max_bytes": mx,
                   "peak_pct": (100.0 * peak / mx) if peak is not None and mx else None})
        doc["units"][name] = ev
    return doc


def write_status(doc: dict, path: Path = STATUS_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read_status(path: Path = STATUS_PATH, now: Optional[float] = None) -> Optional[dict]:
    """The status document, or None when missing, unreadable or stale."""
    import time
    now = time.time() if now is None else now
    try:
        doc = json.loads(Path(path).read_text())
        t = datetime.strptime(doc["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return doc if abs(now - t) <= STALE_S else None


def status_lines(doc: Optional[dict]) -> List[str]:
    """`smd status`'s section: only units with an event or a peak above
    PEAK_WARN_PCT, plus any whole-machine OOM kills; [] when all is quiet."""
    if not doc:
        return []
    rows = []
    for name, u in sorted((doc.get("units") or {}).items()):
        pct = u.get("peak_pct")
        eventful = any(u.get(k) for k in ("auto_restarts", "manual_restarts", "memcg_oom",
                                           "unexpected_exits"))
        if not eventful and not (pct is not None and pct > PEAK_WARN_PCT):
            continue
        mem = (f"{round(u['peak_bytes'] / 2**20)}/{round(u['max_bytes'] / 2**20)} MB ({pct:.0f} %)"
               if pct is not None else "-")
        rows.append(f"  {name:<36} {u.get('auto_restarts', 0):>4} {u.get('manual_restarts', 0):>6}"
                    f" {u.get('memcg_oom', 0):>5}  {u.get('last_utc') or '-':<20} {mem}")
    glob = doc.get("global_oom") or []
    if not rows and not glob:
        return []
    out = ["", "service events (24 h)",
           f"  {'unit':<36} {'auto':>4} {'manual':>6} {'oom':>5}  {'last (UTC)':<20} memory peak/cap"]
    out += rows
    for g in glob:
        out.append(f"  whole-machine OOM {g.get('utc')}: killed {g.get('unit')} ({g.get('task')})")
    return out

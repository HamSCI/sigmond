"""SMART health of the archive drive (design 2026-11-12, part 1).

Reads `smartctl -j`, tries the USB bridge types smartctl knows, and turns a
reading into a verdict: fail, warn, healthy or unknown.  A failure to read is
never reported as a failing drive.  Pure functions; the subprocess runner is
injected so tests need no drive.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# smartctl's -d order: sat first, then its own detection, then the bridge
# types it knows.  (writer's call, design part 1)
DEV_TYPES: Tuple[Optional[str], ...] = ("sat", None, "usbjmicron", "usbcypress",
                                        "usbprolific", "usbsunplus")

WARN_TEMP_C = 55
WARN_POWER_ON_H = 43_800          # five years
WARN_NVME_USED_PCT = 80

UAS_BLOCK_TEXT = "unsupported field in scsi command"
KNOWN_SMR = ("ST2000LM007", "ST4000LM024", "ST5000LM000", "WD20SPZX", "WD40NDZW")


@dataclass
class Reading:
    passed: Optional[bool] = None
    model: str = ""
    disk_serial: str = ""
    power_on_hours: Optional[int] = None
    power_cycles: Optional[int] = None
    reallocated: Optional[int] = None
    pending: Optional[int] = None
    offline_uncorrectable: Optional[int] = None
    reported_uncorrect: Optional[int] = None
    crc_errors: Optional[int] = None
    temperature_c: Optional[int] = None
    nvme_media_errors: Optional[int] = None
    nvme_percentage_used: Optional[int] = None
    selftest_status: Optional[str] = None          # passed | failed | running | None
    selftest_remaining_pct: Optional[int] = None
    dev_type: Optional[str] = None


def _attr(doc: dict, ident: int) -> Optional[int]:
    for a in (doc.get("ata_smart_attributes") or {}).get("table") or []:
        if a.get("id") == ident:
            try:
                return int((a.get("raw") or {}).get("value"))
            except (TypeError, ValueError):
                return None
    return None


def has_attributes(doc: dict) -> bool:
    return bool((doc.get("ata_smart_attributes") or {}).get("table")
                or doc.get("nvme_smart_health_information_log"))


def parse_smartctl(doc: dict, dev_type: Optional[str]) -> Reading:
    nv = doc.get("nvme_smart_health_information_log") or {}
    r = Reading(dev_type=dev_type, model=str(doc.get("model_name") or ""),
                disk_serial=str(doc.get("serial_number") or ""))
    ss = doc.get("smart_status")
    if isinstance(ss, dict) and "passed" in ss:
        r.passed = bool(ss["passed"])
    if nv:
        r.power_on_hours = nv.get("power_on_hours")
        r.power_cycles = nv.get("power_cycles")
        r.temperature_c = nv.get("temperature")
        r.nvme_media_errors = nv.get("media_errors")
        r.nvme_percentage_used = nv.get("percentage_used")
        if nv.get("critical_warning"):
            r.passed = False
    else:
        r.power_on_hours = (doc.get("power_on_time") or {}).get("hours", _attr(doc, 9))
        r.power_cycles = doc.get("power_cycle_count", _attr(doc, 12))
        r.reallocated = _attr(doc, 5)
        r.pending = _attr(doc, 197)
        r.offline_uncorrectable = _attr(doc, 198)
        r.reported_uncorrect = _attr(doc, 187)
        r.crc_errors = _attr(doc, 199)
        temp = (doc.get("temperature") or {}).get("current")
        r.temperature_c = temp if temp is not None else (_attr(doc, 194) or _attr(doc, 190))
    st = ((doc.get("ata_smart_data") or {}).get("self_test") or {}).get("status") or {}
    if "remaining_percent" in st:
        r.selftest_status, r.selftest_remaining_pct = "running", int(st["remaining_percent"])
    else:
        log = (((doc.get("ata_smart_self_test_log") or {}).get("standard") or {}).get("table")
               or (doc.get("nvme_self_test_log") or {}).get("table") or [])
        if log:
            s = log[0].get("status") or log[0].get("self_test_result") or {}
            if "passed" in s:
                r.selftest_status = "passed" if s["passed"] else "failed"
    return r


def reading_from_dict(d: dict) -> Reading:
    names = {f.name for f in dataclasses.fields(Reading)}
    return Reading(**{k: v for k, v in (d or {}).items() if k in names})


def _smartctl(device: str, dev_type: Optional[str], args: List[str], run) -> dict:
    argv = ["smartctl", "-j", *args] + (["-d", dev_type] if dev_type else []) + [f"/dev/{device}"]
    try:
        p = run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"smartctl": {"exit_status": 2, "messages": [{"string": str(exc)}]}}
    try:
        doc = json.loads(p.stdout or "{}")
    except ValueError:
        doc = {}
    doc.setdefault("smartctl", {}).setdefault("exit_status", p.returncode)
    if p.stderr:
        doc["smartctl"].setdefault("messages", []).append({"string": p.stderr})
    return doc


def _messages(doc: dict) -> str:
    return " ".join(m.get("string", "") for m in (doc.get("smartctl") or {}).get("messages") or [])


def usb_driver(device: str) -> str:
    """The kernel driver of the disk's USB interface: 'uas' or 'usb-storage'."""
    try:
        p = Path(f"/sys/block/{device}/device").resolve()
        for anc in p.parents:
            drv = anc / "driver"
            if drv.exists() and anc.name.count(":") == 1:
                return drv.resolve().name
    except OSError:
        pass
    return ""


def probe(device: str, run=subprocess.run, driver: Callable[[str], str] = usb_driver
          ) -> Tuple[Optional[Reading], str]:
    """The first -d type that returns SMART attributes, as a Reading; else
    (None, why)."""
    uas_blocked = False
    for t in DEV_TYPES:
        doc = _smartctl(device, t, ["-a"], run)
        exit_status = int((doc.get("smartctl") or {}).get("exit_status", 2))
        if not (exit_status & 0b11) and has_attributes(doc):
            return parse_smartctl(doc, t), ""
        if t == "sat" and UAS_BLOCK_TEXT in _messages(doc).lower():
            uas_blocked = True
    if uas_blocked and driver(device) == "uas":
        return None, ("the drive's USB bridge blocks SMART under UAS; switching it to "
                      "usb-storage lets it through")
    return None, "the drive's USB bridge passes no SMART data"


def verdict(r: Optional[Reading], unknown_reason: str = "") -> Tuple[str, List[str]]:
    if r is None:
        return "unknown", [unknown_reason or "no SMART reading"]
    fail, warn = [], []
    if r.passed is False:
        fail.append("the drive's own SMART verdict is FAILED")
    if (r.pending or 0) > 0:
        fail.append(f"{r.pending} sectors pending remap (197)")
    if (r.offline_uncorrectable or 0) > 0:
        fail.append(f"{r.offline_uncorrectable} unreadable sectors (198)")
    if (r.nvme_media_errors or 0) > 0:
        fail.append(f"{r.nvme_media_errors} media errors")
    if r.selftest_status == "failed":
        fail.append("the last self-test failed")
    if fail:
        return "fail", fail
    if (r.reallocated or 0) > 0:
        warn.append(f"{r.reallocated} remapped sectors (5)")
    if (r.crc_errors or 0) > 0:
        warn.append(f"{r.crc_errors} link (cable) errors (199)")
    if r.temperature_c is not None and r.temperature_c > WARN_TEMP_C:
        warn.append(f"temperature {r.temperature_c} °C")
    if r.power_on_hours is not None and r.power_on_hours > WARN_POWER_ON_H:
        warn.append(f"{r.power_on_hours} power-on hours (over five years)")
    if r.nvme_percentage_used is not None and r.nvme_percentage_used > WARN_NVME_USED_PCT:
        warn.append(f"wear {r.nvme_percentage_used} %")
    return ("warn", warn) if warn else ("healthy", [])


def is_smr(model: str) -> bool:
    return any(model.upper().startswith(m) for m in KNOWN_SMR)


def table(r: Reading) -> List[Tuple[str, str]]:
    """(label, value) rows for `add` and `status`."""
    f = lambda v, unit="": "-" if v is None else f"{v}{unit}"
    return [
        ("overall", {True: "passed", False: "FAILED", None: "-"}[r.passed]),
        ("power-on", f"{f(r.power_on_hours, ' h')}, {f(r.power_cycles)} cycles"),
        ("remapped", f(r.reallocated)), ("pending", f(r.pending)),
        ("unreadable", f(r.offline_uncorrectable if r.nvme_media_errors is None
                         else r.nvme_media_errors)),
        ("reported uncorrectable", f(r.reported_uncorrect)),
        ("link errors", f(r.crc_errors)), ("temperature", f(r.temperature_c, " °C")),
        ("wear", f(r.nvme_percentage_used, " %")),
        ("self-test", f(r.selftest_status)),
    ]


def read_once(device: str, dev_type: Optional[str], run=subprocess.run
              ) -> Tuple[Optional[Reading], str]:
    """One reading with the known -d type, never waking a spun-down drive
    (-n standby).  (None, 'standby') when it was asleep."""
    doc = _smartctl(device, dev_type, ["-a", "-n", "standby"], run)
    if "standby" in _messages(doc).lower() or "STANDBY" in json.dumps(doc):
        return None, "standby"
    exit_status = int((doc.get("smartctl") or {}).get("exit_status", 2))
    if exit_status & 0b11 or not has_attributes(doc):
        return None, "no SMART reading"
    return parse_smartctl(doc, dev_type), ""


def start_short_test(device: str, dev_type: Optional[str], run=subprocess.run) -> bool:
    doc = _smartctl(device, dev_type, ["-t", "short"], run)
    return not (int((doc.get("smartctl") or {}).get("exit_status", 2)) & 0b11)


def start_long_test(device: str, dev_type: Optional[str], run=subprocess.run) -> bool:
    doc = _smartctl(device, dev_type, ["-t", "long"], run)
    return not (int((doc.get("smartctl") or {}).get("exit_status", 2)) & 0b11)


def wait_self_test(device: str, dev_type: Optional[str], run=subprocess.run,
                   sleep=time.sleep, say=print, timeout_s: float = 600) -> Optional[str]:
    """Poll every 10 s until the self-test ends; its result, or None on timeout."""
    waited = 0.0
    while waited <= timeout_s:
        r = parse_smartctl(_smartctl(device, dev_type, ["-a"], run), dev_type)
        if r.selftest_status != "running":
            return r.selftest_status
        say(f"    self-test running, {r.selftest_remaining_pct} % left")
        sleep(10)
        waited += 10
    return None


def uas_quirk_line(vid: str, pid: str) -> str:
    return f"options usb-storage quirks={vid}:{pid}:u"


def grown(baseline: Reading, latest: Reading) -> List[str]:
    out = []
    for label, field in (("remapped sectors", "reallocated"), ("pending sectors", "pending"),
                         ("unreadable sectors", "offline_uncorrectable"),
                         ("media errors", "nvme_media_errors")):
        a, b = getattr(baseline, field), getattr(latest, field)
        if a is not None and b is not None and b > a:
            out.append(f"{label} {a} -> {b}")
    return out


def health_doc(baseline: Reading, latest: Reading, when_utc: str,
               baseline_utc: Optional[str] = None) -> dict:
    return {"baseline": dataclasses.asdict(baseline), "baseline_utc": baseline_utc or when_utc,
            "latest": dataclasses.asdict(latest), "latest_utc": when_utc}

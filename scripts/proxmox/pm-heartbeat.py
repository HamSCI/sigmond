#!/usr/bin/env python3
"""Proxmox-HOST heartbeat emitter — the fulcrum's own honest envelope.

RUNS ON THE PM (the Proxmox host itself), NOT INSIDE A GUEST.  A PM runs
NO sigmond, no venv, no pip — plain PVE Debian with a system python3
(3.11+, ``tomllib`` in stdlib).  This file is therefore STDLIB ONLY, with
ZERO imports from ``sigmond`` (unlike a station's ``smd admin heartbeat
emit``, which lives inside the sigmond process).  It is deployed by
rsync/scp next to ``pm-heartbeat-setup.sh`` and the two systemd units.

Why this exists
----------------
The fleetboard watches VMs; the PM underneath them — the deployment
fulcrum and the project's named critical dependency — was invisible.
Worse, its ``qm guest exec`` channel to the VM can wedge SILENTLY: the
host-side QEMU guest agent connection resets while the guest agent
inside the VM looks perfectly healthy from the VM's own seat.  Only the
PM can see that from the outside, which is the entire reason this is a
separate emitter rather than one more block on the station's own
heartbeat.

Wire-contract compatibility (READ BEFORE EDITING)
--------------------------------------------------
``KIND``, ``SCHEMA_VERSION``, ``VERDICTS`` and ``PRECEDENCE`` below are
DUPLICATED FROM ``lib/sigmond/heartbeat_schema.py`` ON PURPOSE — a PM
cannot import sigmond, so there is no way to share the module instead of
its four constants.  ``tests/test_pm_heartbeat.py``'s
``test_contract_envelope_validates_against_schema`` (feeding a real
assembled envelope through the actual ``sigmond.heartbeat_schema.validate``)
and ``test_inlined_verdicts_and_precedence_match_the_real_schema`` are the
DRIFT GUARD for this duplication: if the real schema ever changes shape,
those tests go red here and force this file to catch up.

``BLOCK_NAMES`` here is NOT the schema's full 7-name set — it is the
THREE blocks a PM can honestly speak to (``versions``, ``doctor``,
``resources``).  ``heartbeat_schema.validate()`` deliberately accepts any
SUBSET of its block names (see its docstring: additive/partial producers
must not be rejected), which is what makes a heartbeat with only these
three blocks a valid ``station_heartbeat`` envelope.  Emitting
``timing``/``gaps``/``uploads``/``manifest`` from a PM would be a lie —
a Proxmox host has no view of any of those.

Architecture (mirrors lib/sigmond/heartbeat.py's shape on purpose)
-------------------------------------------------------------------
* ``assemble(config, checks)`` — pure.  ``checks`` is a bundle of five
  zero-arg callables keyed "now", "uptime_s", and one per ``BLOCK_NAMES``
  entry.  Every block callable is wrapped: an exception becomes
  ``INDETERMINATE`` with the exception attached, exactly like
  ``heartbeat.py``'s ``_assemble_block`` — "nothing measured" must never
  render as healthy.
* ``build_doctor_raw`` / ``build_versions_raw`` / ``build_resources_raw``
  — the "per-check builders taking injected runners": each calls the
  low-level probes (subprocess wrappers, file readers) it was handed and
  returns a raw dict, never raising for an EXPECTED failure shape (a
  missing file, a nonzero ``qm`` exit) so the difference between "no
  evidence" and "measured and fine" survives into the block mapper.
* ``_map_doctor`` / ``_map_versions`` / ``_map_resources`` — raw dict ->
  ``{"verdict", "reason", "data"}``, in the same style as
  ``heartbeat.py``'s ``_map_*`` functions.
* ``main()`` is the only impure code: it loads config, wires the real
  subprocess/fs runners, assembles one envelope, spools it, and attempts
  to ship the WHOLE spool in one sftp batch.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
import tomllib
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# wire contract (duplicated from lib/sigmond/heartbeat_schema.py — see the
# module docstring above for the drift guard that keeps this honest)
# ---------------------------------------------------------------------------

KIND = "station_heartbeat"
SCHEMA_VERSION = 1

VERDICTS = ("VALID", "INVALID", "INCONCLUSIVE", "INDETERMINATE")

PRECEDENCE = {
    "VALID": 0,
    "INCONCLUSIVE": 1,
    "INDETERMINATE": 2,
    "INVALID": 3,
}

# The subset of the schema's 7 block names a PM can honestly speak to, in
# report order.  See module docstring: validate() accepts any subset.
BLOCK_NAMES = ("versions", "doctor", "resources")

DEFAULT_INTERVAL_SEC = 300

CONFIG_PATH = "/etc/pm-heartbeat/config.toml"
SPOOL_DIR = "/var/lib/pm-heartbeat/spool"
KNOWN_HOSTS_PATH = "/var/lib/pm-heartbeat/known_hosts"
SPOOL_MAX_FILES = 48

_EMITTED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_FILENAME_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

RESCTRL_PATH = "/sys/fs/resctrl/radiod"

#: A subprocess result, real or fake — tests build these directly with
#: RunResult(rc, stdout, stderr) so no unit test ever spawns a real
#: process.
RunResult = namedtuple("RunResult", "rc stdout stderr")


class ConfigError(Exception):
    """config.toml is missing, unreadable, or missing a required key."""


# ---------------------------------------------------------------------------
# assemble — pure
# ---------------------------------------------------------------------------

def assemble(config: dict, checks: dict) -> dict:
    """Build one PM heartbeat envelope.  Pure, given its inputs.

    ``config``  ``{"station": str, "interval_sec": int}``.  ``station``
                is REQUIRED; a missing one raises ``ValueError`` — an
                unattributable heartbeat is worse than none (mirrors
                ``sigmond.heartbeat.assemble``).
    ``checks``  five zero-arg callables: "now" (-> datetime, required),
                "uptime_s" (-> float | None), and one per ``BLOCK_NAMES``
                entry (-> that block's RAW dict, or raises).  Bundled as
                callables — not read here — is what keeps this function
                pure and fully deterministic under test.
    """
    config = config or {}
    station = config.get("station")
    if not isinstance(station, str) or not station.strip():
        raise ValueError(
            "pm-heartbeat config requires a non-empty 'station' — an "
            "unattributable heartbeat is worse than none")

    checks = checks or {}
    now_fn = checks.get("now")
    if not callable(now_fn):
        raise ValueError("pm-heartbeat checks require a 'now' callable")
    emitted = _as_utc(now_fn())

    uptime_s = _safe_call(checks.get("uptime_s"))

    blocks = {name: _assemble_block(name, checks) for name in BLOCK_NAMES}

    interval_sec = config.get("interval_sec") or DEFAULT_INTERVAL_SEC

    return {
        "kind": KIND,
        "schema_version": SCHEMA_VERSION,
        "station": station,
        "role": "pm",           # additive field; validate() allows it
        "emitted_at": emitted.strftime(_EMITTED_AT_FORMAT),
        "interval_sec": interval_sec,
        "uptime_s": uptime_s,
        "rollup": rollup(blocks),
        "blocks": blocks,
    }


def rollup(blocks: dict) -> dict:
    """Worst verdict across ``blocks``, naming the block that earned it.

    Byte-for-byte the same shape as ``sigmond.heartbeat.rollup`` (worst
    wins, ties resolve to first-in-``BLOCK_NAMES``, unknown verdicts fail
    closed to INDETERMINATE) — duplicated rather than imported for the
    same stdlib-only reason as the constants above.
    """
    present = [n for n in BLOCK_NAMES if isinstance(blocks.get(n), dict)]
    if not present:
        return {"verdict": "INDETERMINATE", "reason": "no blocks assembled"}
    worst = max(_rank(blocks[n].get("verdict")) for n in present)
    for name in present:
        verdict = blocks[name].get("verdict")
        if _rank(verdict) != worst:
            continue
        if verdict not in PRECEDENCE:
            return {
                "verdict": "INDETERMINATE",
                "reason": f"{name}: unknown verdict {verdict!r} — "
                          f"{blocks[name].get('reason')}",
            }
        return {
            "verdict": verdict,
            "reason": f"{name}: {blocks[name].get('reason')}",
        }
    return {"verdict": "INDETERMINATE", "reason": "no blocks assembled"}


def _rank(verdict) -> int:
    return PRECEDENCE.get(verdict, PRECEDENCE["INDETERMINATE"])


def _assemble_block(name: str, checks: dict) -> dict:
    """Call the block's check, wrapped: exception -> INDETERMINATE.

    Mirrors ``sigmond.heartbeat._assemble_block`` — a check that raises
    or was never wired must never make its block silently disappear or
    read as healthy.
    """
    fn = checks.get(name)
    if not callable(fn):
        return _block("INDETERMINATE", f"no {name} check wired")
    try:
        raw = fn()
        return _MAPPERS[name](raw)
    except Exception as exc:                      # noqa: BLE001 - deliberate
        return _block(
            "INDETERMINATE",
            f"{name} check raised {exc.__class__.__name__}: {exc}")


def _block(verdict: str, reason: str, data=None) -> dict:
    block = {"verdict": verdict, "reason": reason}
    if data is not None:
        block["data"] = data
    return block


def _safe_call(fn):
    """Call ``fn()``, returning None on a missing callable OR a raise.

    Used only for "context, not evidence" fields (``uptime_s``) where a
    failure is honestly reported as null rather than crashing the whole
    envelope — the same discipline as ``sigmond.heartbeat.read_uptime``.
    """
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:                              # noqa: BLE001
        return None


def _as_utc(now) -> datetime:
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)
    return datetime.fromtimestamp(float(now), tz=timezone.utc)


def _unique(items) -> list:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _finding(component: str, kind: str, detail: str) -> dict:
    return {"component": component, "kind": kind, "detail": detail}


# ---------------------------------------------------------------------------
# versions block
# ---------------------------------------------------------------------------

def build_versions_raw(run_pveversion, run_uname) -> dict:
    """pveversion (first line) + kernel (uname -r).  Never raises: each
    sub-probe degrades independently to None so one failing does not
    hide the other."""
    pveversion = None
    try:
        res = run_pveversion()
        if res.rc == 0:
            lines = (res.stdout or "").splitlines()
            pveversion = lines[0].strip() if lines else None
    except Exception:                              # noqa: BLE001
        pveversion = None

    kernel = None
    try:
        res = run_uname()
        if res.rc == 0:
            kernel = (res.stdout or "").strip() or None
    except Exception:                              # noqa: BLE001
        kernel = None

    return {"pveversion": pveversion, "kernel": kernel}


def _map_versions(raw) -> dict:
    raw = raw or {}
    data = {"pveversion": raw.get("pveversion"), "kernel": raw.get("kernel")}
    if data["pveversion"] is None and data["kernel"] is None:
        return _block("INDETERMINATE",
                      "pveversion and kernel both unreadable", data)
    return _block("VALID", "host versions read", data)


# ---------------------------------------------------------------------------
# doctor block — the point of the whole feature
# ---------------------------------------------------------------------------

def build_doctor_raw(vmid, expect_cat, run_guest_exec, run_qm_status,
                     run_qm_config, probe_cat) -> dict:
    """Run the three doctor sub-checks, each independently wrapped.

    A sub-check that raises contributes a ``"<name>-unassessed"`` finding
    (verbatim exception attached) rather than aborting the other two —
    mirrors ``bin/smd``'s ``collect_findings(strict=False)._guarded``.
    ``checks_run`` is how the mapper distinguishes "nothing could be
    assessed" (all three raised -> INDETERMINATE) from "assessed, and
    here is what it found" (INVALID/VALID).
    """
    findings = []
    checks_run = 0

    try:
        f = _check_guest_exec(vmid, run_guest_exec, run_qm_status)
        checks_run += 1
        if f is not None:
            findings.append(f)
    except Exception as exc:                       # noqa: BLE001
        findings.append(_finding(
            "guest-exec", "guest-exec-unassessed",
            f"{exc.__class__.__name__}: {exc}"))

    try:
        f = _check_hookscript(vmid, run_qm_config)
        checks_run += 1
        if f is not None:
            findings.append(f)
    except Exception as exc:                       # noqa: BLE001
        findings.append(_finding(
            "hookscript", "hookscript-unassessed",
            f"{exc.__class__.__name__}: {exc}"))

    cat_data = None
    try:
        f, cat_data = _check_cat(expect_cat, probe_cat)
        checks_run += 1
        if f is not None:
            findings.append(f)
    except Exception as exc:                       # noqa: BLE001
        findings.append(_finding(
            "cat", "cat-unassessed", f"{exc.__class__.__name__}: {exc}"))

    return {
        "checks_run": checks_run,
        "checks_total": 3,
        "findings": findings,
        "cat": cat_data,
    }


def _check_guest_exec(vmid, run_guest_exec, run_qm_status):
    """``qm guest exec <vmid> --timeout 15 -- /usr/bin/true``.

    rc 0 -> no finding.  A failure distinguishes THE WEDGE DETECTOR
    ("QEMU guest agent is not running" while the VM IS running ->
    ``guest-agent-wedged``) from an unremarkable ``vm-stopped`` (checked
    via ``qm status``) from anything else (``guest-exec-error``, verbatim
    stderr).
    """
    res = run_guest_exec(vmid)
    if res.rc == 0:
        return None
    stderr = (res.stderr or "").strip()
    if "QEMU guest agent is not running" in stderr:
        status = run_qm_status(vmid)
        status_text = (status.stdout or "").strip().lower()
        if "stopped" in status_text:
            return _finding("guest-exec", "vm-stopped",
                            f"VM {vmid} is stopped")
        return _finding(
            "guest-exec", "guest-agent-wedged",
            f"VM {vmid} is running but the QEMU guest agent is not: "
            f"{stderr}")
    return _finding("guest-exec", "guest-exec-error",
                    stderr or f"qm guest exec exited {res.rc}")


def _check_hookscript(vmid, run_qm_config):
    """``qm config <vmid>`` must contain ``hookscript:`` — config drift
    otherwise (the cpu-pin hookscript silently not installed/registered)."""
    res = run_qm_config(vmid)
    if res.rc != 0:
        raise RuntimeError(
            f"qm config exited {res.rc}: {(res.stderr or '').strip()}")
    text = res.stdout or ""
    if "hookscript:" in text:
        return None
    return _finding("hookscript", "hookscript-missing",
                    f"VM {vmid} has no hookscript configured")


def _check_cat(expect_cat, probe_cat):
    """/sys/fs/resctrl/radiod, gated by ``expect_cat``.

    Returns ``(finding_or_None, data)`` — ``data`` (presence + cpus_list
    + schemata) rides along regardless of whether it produced a finding,
    so an operator can see CAT's real state even when it's not required.
    """
    info = probe_cat() or {}
    present = bool(info.get("present"))
    data = {
        "expect_cat": bool(expect_cat),
        "present": present,
        "cpus_list": info.get("cpus_list"),
        "schemata": info.get("schemata"),
    }
    if expect_cat and not present:
        return _finding(
            "cat", "cat-groups-missing",
            f"{RESCTRL_PATH} absent but expect_cat=true"), data
    return None, data


def _map_doctor(raw) -> dict:
    raw = raw or {}
    findings = list(raw.get("findings") or [])
    checks_run = raw.get("checks_run") or 0
    data = {
        "findings": findings,
        "cat": raw.get("cat"),
        "checks_run": checks_run,
        "checks_total": raw.get("checks_total"),
    }
    if checks_run == 0:
        kinds = _unique(f.get("kind") for f in findings)
        return _block(
            "INDETERMINATE",
            f"doctor could not assess any check ({', '.join(kinds)})",
            data)
    if findings:
        kinds = _unique(f.get("kind") for f in findings)
        return _block(
            "INVALID",
            f"{len(findings)} doctor finding(s): {', '.join(kinds)}",
            data)
    return _block("VALID", f"{checks_run} checks passed", data)


# ---------------------------------------------------------------------------
# resources block
# ---------------------------------------------------------------------------

def build_resources_raw(read_loadavg, read_disk_used_pct, read_uptime,
                        read_temperature) -> dict:
    """Loadavg, per-path disk usage, uptime, best-effort temperature.

    ``read_loadavg`` is the spine and is allowed to raise (propagates to
    ``_assemble_block``'s wrap -> the whole block goes INDETERMINATE,
    "Probe raised"): an unreadable /proc/loadavg means the probe itself
    is broken, not merely one field. Everything else degrades to null
    individually rather than take the block down with it.
    """
    loadavg = read_loadavg()
    disk = {}
    for path in ("/", "/var/lib/vz"):
        try:
            disk[path] = read_disk_used_pct(path)
        except Exception:                          # noqa: BLE001
            disk[path] = None
    uptime_s = None
    try:
        uptime_s = read_uptime()
    except Exception:                              # noqa: BLE001
        uptime_s = None
    temperature = None
    try:
        temperature = read_temperature()
    except Exception:                              # noqa: BLE001
        temperature = None
    return {"loadavg": loadavg, "disk": disk, "uptime_s": uptime_s,
            "temperature": temperature}


def _map_resources(raw) -> dict:
    raw = raw or {}
    data = {
        "loadavg": raw.get("loadavg"),
        "disk_used_pct": raw.get("disk"),
        "uptime_s": raw.get("uptime_s"),
        "temperature_c": raw.get("temperature"),
    }
    # No thresholds in v1: resources are context for the `doctor` verdict,
    # not a verdict in their own right. Reaching this point means the
    # probe did not raise, which is the only thing this block asserts.
    return _block("VALID", "host resource counters read", data)


_MAPPERS = {
    "versions": _map_versions,
    "doctor": _map_doctor,
    "resources": _map_resources,
}


# ---------------------------------------------------------------------------
# production check runners (impure; wired by main())
# ---------------------------------------------------------------------------

def read_uptime(path: str = "/proc/uptime"):
    """Host uptime in seconds, or None.  Never 0 on failure — a
    fabricated zero would read as "just rebooted" on the board.  Same
    contract as ``sigmond.heartbeat.read_uptime``."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _run_qm_guest_exec(vmid) -> RunResult:
    proc = subprocess.run(
        ["qm", "guest", "exec", str(vmid), "--timeout", "15", "--",
         "/usr/bin/true"],
        capture_output=True, text=True, timeout=25, check=False)
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


def _run_qm_status(vmid) -> RunResult:
    proc = subprocess.run(["qm", "status", str(vmid)], capture_output=True,
                          text=True, timeout=15, check=False)
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


def _run_qm_config(vmid) -> RunResult:
    proc = subprocess.run(["qm", "config", str(vmid)], capture_output=True,
                          text=True, timeout=15, check=False)
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


def _probe_cat(path: str = RESCTRL_PATH) -> dict:
    p = Path(path)
    if not p.is_dir():
        return {"present": False, "cpus_list": None, "schemata": None}

    def _read(name):
        try:
            return (p / name).read_text().strip()
        except OSError:
            return None

    return {"present": True, "cpus_list": _read("cpus_list"),
            "schemata": _read("schemata")}


def _run_pveversion() -> RunResult:
    proc = subprocess.run(["pveversion"], capture_output=True, text=True,
                          timeout=10, check=False)
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


def _run_uname() -> RunResult:
    proc = subprocess.run(["uname", "-r"], capture_output=True, text=True,
                          timeout=10, check=False)
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


def _read_loadavg() -> dict:
    with open("/proc/loadavg", "r", encoding="utf-8") as fh:
        parts = fh.read().split()
    return {"load1": float(parts[0]), "load5": float(parts[1]),
            "load15": float(parts[2])}


def _read_disk_used_pct(path: str):
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    if not st.f_blocks:
        return None
    used = st.f_blocks - st.f_bavail
    return round(100.0 * used / st.f_blocks, 1)


def _read_temperature():
    """Best-effort /sys/class/thermal reading.  None when absent — an
    honest null, never a fabricated 0."""
    for entry in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp")):
        try:
            with open(entry, "r", encoding="utf-8") as fh:
                milli = float(fh.read().strip())
            return milli / 1000.0
        except (OSError, ValueError):
            continue
    return None


def build_production_checks(config: dict) -> dict:
    vmid = config["vmid"]
    expect_cat = config["expect_cat"]
    return {
        "now": lambda: datetime.now(timezone.utc),
        "uptime_s": read_uptime,
        "versions": lambda: build_versions_raw(_run_pveversion, _run_uname),
        "doctor": lambda: build_doctor_raw(
            vmid, expect_cat, _run_qm_guest_exec, _run_qm_status,
            _run_qm_config, _probe_cat),
        "resources": lambda: build_resources_raw(
            _read_loadavg, _read_disk_used_pct, read_uptime,
            _read_temperature),
    }


# ---------------------------------------------------------------------------
# spool
# ---------------------------------------------------------------------------

def write_tick(envelope: dict, spool_dir) -> Path:
    """Atomically write one heartbeat to the spool; return its path.

    tmp+fsync+rename, same discipline as ``sigmond.heartbeat.write_tick``
    — a reader (the ship step, right below) must never see a half-written
    file. Also prunes to the newest ``SPOOL_MAX_FILES``.
    """
    spool = Path(spool_dir)
    spool.mkdir(parents=True, exist_ok=True)

    station = str(envelope.get("station") or "unknown")
    stamp = _stamp_for(envelope)
    name = f"{_safe_component(station)}_{stamp}.json"
    final = spool / name
    tmp = spool / f"{name}.tmp"

    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(envelope, fh, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    prune_spool_to_count(spool)
    return final


def prune_spool_to_count(spool_dir, keep: int = SPOOL_MAX_FILES) -> int:
    """Keep only the newest ``keep`` spooled ticks (by mtime); return the
    count deleted.  Count-based, not age-based — a PM offline for days
    must not silently accumulate an unbounded spool."""
    try:
        entries = list(Path(spool_dir).glob("*.json"))
    except OSError:
        return 0
    if len(entries) <= keep:
        return 0
    entries.sort(key=lambda p: _mtime_or_zero(p))
    pruned = 0
    for path in entries[: len(entries) - keep]:
        try:
            path.unlink()
            pruned += 1
        except OSError:
            continue
    return pruned


def _mtime_or_zero(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _stamp_for(envelope: dict) -> str:
    emitted = envelope.get("emitted_at")
    try:
        return datetime.strptime(
            str(emitted), _EMITTED_AT_FORMAT).strftime(_FILENAME_STAMP_FORMAT)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).strftime(_FILENAME_STAMP_FORMAT)


def _safe_component(value: str) -> str:
    """Filename-safe station name; a leading dot is replaced (not merely
    allowed through) so a station named ``.b4-pm`` cannot spool into an
    ingest-side dotfile skip and go silently invisible — same rule as
    ``sigmond.heartbeat._safe_component``."""
    safe = "".join(
        c if (c.isalnum() or c in "._-") else "-" for c in value) or "unknown"
    stripped = safe.lstrip(".")
    if stripped != safe:
        safe = "_" * (len(safe) - len(stripped)) + stripped
    return safe or "unknown"


# ---------------------------------------------------------------------------
# ship — one sftp batch for the whole spool
# ---------------------------------------------------------------------------

def ship_spool(spool_dir, remote_path: str, run_sftp) -> dict:
    """Ship every spooled ``*.json`` oldest-first in ONE sftp batch.

    ``run_sftp(batch_text) -> RunResult`` is injected so no test ever
    spawns a real sftp process. Batch rc 0 => every attempted file
    shipped and is deleted locally. Batch rc != 0 (or ``run_sftp``
    itself raising, e.g. the binary missing) => keep ALL files and
    report ``ok: False`` — the caller (``main``) still exits 0 for this:
    a missed tick must look missed AT THE BOARD (absence of arrival),
    never as a red systemd unit for what is often just a network blip.
    """
    spool = Path(spool_dir)
    files = sorted(spool.glob("*.json"), key=lambda p: p.name)
    if not files:
        return {"shipped": 0, "attempted": 0, "ok": True}

    lines = []
    for f in files:
        remote_tmp = f"{remote_path}/{f.name}.part"
        remote_final = f"{remote_path}/{f.name}"
        lines.append(f"put {f} {remote_tmp}")
        lines.append(f"rename {remote_tmp} {remote_final}")
    batch_text = "\n".join(lines) + "\n"

    try:
        result = run_sftp(batch_text)
    except Exception as exc:                       # noqa: BLE001
        print(f"pm-heartbeat: sftp batch could not run: "
              f"{exc.__class__.__name__}: {exc}", file=sys.stderr)
        return {"shipped": 0, "attempted": len(files), "ok": False}

    if result.rc != 0:
        tail = (result.stderr or "").strip().splitlines()
        detail = tail[-1] if tail else f"exit {result.rc}"
        print(f"pm-heartbeat: sftp batch failed ({len(files)} tick(s) "
              f"held): {detail}", file=sys.stderr)
        return {"shipped": 0, "attempted": len(files), "ok": False}

    for f in files:
        try:
            f.unlink()
        except OSError:
            pass
    return {"shipped": len(files), "attempted": len(files), "ok": True}


def _run_sftp_production(batch_text: str, config: dict) -> RunResult:
    cmd = [
        "sftp", "-b", "-", "-i", config["key_path"], "-P",
        str(config["dest_port"]),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={KNOWN_HOSTS_PATH}",
        f"{config['sftp_user']}@{config['dest_host']}",
    ]
    proc = subprocess.run(cmd, input=batch_text, capture_output=True,
                          text=True, timeout=60, check=False)
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = ("station", "vmid", "dest_host")


def load_config(path=CONFIG_PATH) -> dict:
    """Load + validate ``/etc/pm-heartbeat/config.toml``.

    Raises ``ConfigError`` for anything a human must fix (missing file,
    bad TOML, a missing required key) — ``main()`` turns that into a
    loud stderr line and exit 2, never a silent default.
    """
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"{path} does not exist — run pm-heartbeat-setup.sh")
    except OSError as exc:
        raise ConfigError(f"{path} unreadable: {exc}")
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}")

    missing = [k for k in _REQUIRED_KEYS
              if raw.get(k) in (None, "")]
    if missing:
        raise ConfigError(
            f"{path} is missing required key(s): {', '.join(missing)}")

    try:
        vmid = int(raw["vmid"])
    except (TypeError, ValueError):
        raise ConfigError(f"{path}: vmid must be an integer, got {raw['vmid']!r}")

    return {
        "station": str(raw["station"]),
        "vmid": vmid,
        "dest_host": str(raw["dest_host"]),
        "dest_port": int(raw.get("dest_port", 22)),
        "sftp_user": str(raw.get("sftp_user", "hamsci-hb")),
        "remote_path": str(raw.get("remote_path", "incoming")),
        "interval_sec": int(raw.get("interval_sec", DEFAULT_INTERVAL_SEC)),
        "key_path": str(raw.get("key_path", "/etc/pm-heartbeat/id_ed25519")),
        "expect_cat": bool(raw.get("expect_cat", False)),
    }


# ---------------------------------------------------------------------------
# main — the only impure entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"pm-heartbeat: {exc}", file=sys.stderr)
        return 2

    checks = build_production_checks(config)
    envelope = assemble(
        {"station": config["station"], "interval_sec": config["interval_sec"]},
        checks)

    try:
        write_tick(envelope, SPOOL_DIR)
    except OSError as exc:
        print(f"pm-heartbeat: could not write spool {SPOOL_DIR}: {exc}",
              file=sys.stderr)
        return 1

    run_sftp = lambda batch_text: _run_sftp_production(batch_text, config)
    ship_spool(SPOOL_DIR, config["remote_path"], run_sftp)
    # ship_spool already printed its own stderr line on failure and a
    # missed tick must look missed at the board (arrival-derived
    # availability) — never fail the unit red for a network error.
    return 0


if __name__ == "__main__":
    sys.exit(main())

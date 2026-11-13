#!/usr/bin/env python3
"""Proxmox-HOST storage watch: the thin pool's report, snapshot lifetimes,
and the host's own alarm.

RUNS ON THE PM (the Proxmox host itself), as root, from pm-storage.timer
every five minutes.  A PM runs no sigmond, no venv and no pip, so this file
is STDLIB ONLY and imports nothing from ``sigmond``.  pm-heartbeat-setup.sh
installs it next to pm-heartbeat.py.

Why this exists
---------------
On 2026-11-03 four stations' thin pools went from about 67 % to 100 % in
about 29 hours.  An operator's ``pre-v385`` snapshot pinned the old blocks
while hf-timestd rewrote its IQ ring at about 3.7 GB/h, and every rewrite
took fresh pool space.  The host journal carried dmeventd's warnings at 80,
85, 90 and 95 %, and nobody read them.  The guest could not see the pool at
all.  Design: docs/superpowers/specs/2026-11-04-storage-capacity-design.md,
"Prevention" and the host-pool paragraphs.

What it does
------------
``report`` (the timer):
  * finds each VM's thin pool (storage.cfg + ``qm config``);
  * measures the pool with ``lvs`` and keeps six hours of its use in
    ``LOCAL_STATE``, from which it estimates the pool's growth rate;
  * deletes the snapshots sigmond's own tools took, once past expiry, or
    all of them at once when the pool reaches the Demand stage (90 %);
  * writes each running guest the pool report its storage ledger reads
    (``/run/sigmond/host-pool.json``, schema 1, through the guest agent);
  * logs its alarms to the journal, one line per episode.

``snapshot <vmid> <name> [--hours N]`` takes a labelled snapshot whose
lifetime follows the pool's projected time to full, or refuses one the
pool cannot carry.

What it deletes (READ BEFORE EDITING)
-------------------------------------
A deleted snapshot cannot come back.  The reaper deletes a snapshot only
when ALL of these hold:
  * its description carries exactly one ``sigmond-snapshot expires=<ts>``
    tag, and <ts> parses as an ISO-8601 time WITH an offset;
  * its name is not in ``NEVER_REAP`` (``pristine``, the first-boot
    snapshot, carries no label and stays out by name besides);
  * the expiry has passed, or the pool sits at or above
    ``SNAP_EARLY_REAP_PCT``.
The tag must start a word.  A tag that fails to parse makes the snapshot
an operator's: kept, and named in an alarm at once.  An operator's
snapshot is never deleted; the host names it in an alarm once it outlives
the lifetime a sigmond snapshot would get on its pool now, or at any age
once the pool reaches watch.

Every delete on every pool runs before any guest write, with no timeout:
a killed ``qm delsnapshot`` leaves the VM locked.  The guest writes keep
their own deadline (``GUEST_WRITE_BUDGET_S``); a failed write gets one
agent parser reset and one retry before the guest counts as unreachable.
The state file gets written however the run ends.  A run that did not
measure something (host storage unreadable, a pool unreadable, a stop
request before the guest writes) carries that scope's alarm episodes
forward unchanged: it logs no "cleared" for them and opens none anew.

Guest agent: no byte above 0x7F in anything sent through it.  The agent
goes deaf on a short-written request (sigmond CLAUDE.md).  The report goes
out as ``json.dumps(..., ensure_ascii=True)``, and the file-write runner
refuses any argv that is not pure ASCII.

Architecture (pm-heartbeat.py's shape)
--------------------------------------
Pure builders (``parse_*``, ``read_pool``, ``expired``, ``host_alarms``,
``guest_report``, ...) plus a host bundle of injected runners.  ``main``
takes the bundle as its second argument; only ``RealHost`` touches qm,
pvesh, lvs and the filesystem.

Duplicated constants: ``LEVELS`` copies ``sigmond.storage.model.Levels``,
and pm-heartbeat.py copies ``LOCAL_STATE`` and the final level.
tests/test_pm_storage.py guards both copies against drift.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

#: Stage levels, percent of the pool; duplicated from sigmond.storage.model.Levels.
LEVELS = {"watch": 80.0, "tighten": 85.0, "demand": 90.0, "final": 95.0}

SNAP_TAG = "sigmond-snapshot expires="
SNAP_LIFE_H = 48                 # the cap on a labelled snapshot's lifetime
SNAP_EARLY_REAP_PCT = 90.0       # Demand: every labelled snapshot goes at once
SNAP_REFUSE_PCT = 70.0           # no new snapshot on a pool this full
SNAP_MIN_ROOM_H = 2.0            # nor on one projected to fill sooner
SNAP_FLOOR_H = 1.0
CHURN_WINDOW_S = 6 * 3600
NEVER_REAP = frozenset({"pristine"})
GUEST_REPORT_PATH = "/run/sigmond/host-pool.json"
LOCAL_STATE = "/run/sigmond/pm-storage.json"
REMIND_S = 6 * 3600              # an open episode's reminder interval
#: The guest-write phase's own deadline.  A deaf agent costs up to two 30 s
#: file-writes and a 4 s reset; past this budget the remaining guests are
#: skipped with an alarm rather than overrunning the timer's period.
GUEST_WRITE_BUDGET_S = 180

# Guest-agent parser reset, duplicated from pm-heartbeat.py (and pm-align.py)
# on purpose: a PM runs each script alone.  tests/test_qga_reset.py holds the
# copies to the same bytes and the same behaviour.
QGA_DIR = "/var/run/qemu-server"
QGA_RESET = b"\xff" + b'{"execute":"guest-sync-delimited","arguments":{"id":4242}}\n'
QGA_NOTE = "/run/sigmond/qga-reset"
DISK_KEYS = ("scsi0", "virtio0", "sata0")

_TS = "%Y-%m-%dT%H:%M:%SZ"
# The tag must start a word: "foosigmond-snapshot expires=..." is no label.
_TAG_RE = re.compile(r"(?<!\S)" + re.escape(SNAP_TAG) + r"(\S*)")
_STAGE_PRIO = {"watch": 4, "tighten": 4, "demand": 3, "final": 2}

RunResult = namedtuple("RunResult", "rc stdout stderr")


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS)


def _parse_aware(text) -> Optional[datetime]:
    """An ISO-8601 time that carries an offset, in UTC; None otherwise."""
    if not isinstance(text, str) or not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def _fill(pool: dict) -> float:
    """A full metadata area stops the pool as surely as full data."""
    return max(float(pool["data_pct"]), float(pool["metadata_pct"]))


def _stage(pct: float) -> Optional[str]:
    found = None
    for name in ("watch", "tighten", "demand", "final"):
        if pct >= LEVELS[name]:
            found = name
    return found


def parse_storage_cfg(text: str) -> dict:
    """Storage id -> (vgname, thinpool), for the ``lvmthin:`` blocks only."""
    out, kind, sid, keys = {}, None, None, {}

    def close():
        if kind == "lvmthin" and sid and keys.get("vgname") and keys.get("thinpool"):
            out[sid] = (keys["vgname"], keys["thinpool"])

    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            close()
            head, _, rest = line.partition(":")
            kind, sid, keys = head.strip(), rest.strip(), {}
        else:
            parts = line.split(None, 1)
            if len(parts) == 2:
                keys[parts[0]] = parts[1].strip()
    close()
    return out


def pool_for_vm(qm_config_text: str, storage_map: dict) -> Optional[tuple]:
    """The pool under the VM's first disk (scsi0, then virtio0, then sata0)."""
    disks = {}
    for line in qm_config_text.splitlines():
        if line.startswith("["):        # a snapshot section: not the live config
            break
        key, sep, value = line.partition(":")
        if sep:
            disks[key.strip()] = value.strip()
    for key in DISK_KEYS:
        volume = disks.get(key, "").split(",")[0]
        sid, sep, _ = volume.partition(":")
        if sep and sid in storage_map:
            return storage_map[sid]
    return None


def read_pool(lvs_json: str, vg: str, pool: str) -> dict:
    """The pool's size and fill from ``lvs --reportformat json``; raises if absent."""
    for report in json.loads(lvs_json).get("report", []):
        for lv in report.get("lv", []):
            if lv.get("lv_name") == pool and lv.get("vg_name") == vg:
                return {"name": f"{vg}/{pool}",
                        "size_bytes": int(str(lv["lv_size"]).rstrip("Bb")),
                        "data_pct": float(lv["data_percent"]),
                        "metadata_pct": float(lv["metadata_percent"])}
    raise LookupError(f"thin pool {vg}/{pool} not in lvs output")


def _parse_tag(description: str):
    """(sigmond, expires_iso, label_fault) for one snapshot description."""
    found = _TAG_RE.findall(description)
    if not found:
        return False, None, None
    if len(found) != 1:
        return False, None, f"{len(found)} sigmond labels"
    when = _parse_aware(found[0])
    if when is None:
        return False, None, f"expiry {found[0]!r} has no parseable time with an offset"
    return True, _iso(when), None


def parse_snapshots(pvesh_json: str, vmid: int) -> list:
    """The VM's snapshots, ``current`` dropped, the label read from each description."""
    out = []
    for raw in json.loads(pvesh_json):
        name = raw.get("name")
        if not isinstance(name, str) or not name or name == "current":
            continue
        snaptime = raw.get("snaptime")
        taken = None
        if isinstance(snaptime, (int, float)) and not isinstance(snaptime, bool):
            taken = _iso(datetime.fromtimestamp(snaptime, tz=timezone.utc))
        description = raw.get("description") or ""
        sigmond, expires, fault = _parse_tag(str(description))
        out.append({"vmid": int(vmid), "name": name, "taken": taken,
                    "sigmond": sigmond, "expires": expires, "label_fault": fault})
    return out


def expired(snaps: list, now: datetime, pool_pct: float = 0.0) -> list:
    """The labelled snapshots to delete now.  Never a name in NEVER_REAP,
    never one without ``sigmond is True`` and a parseable, offset-carrying
    expiry."""
    out = []
    for s in snaps:
        if s.get("sigmond") is not True:
            continue
        name = s.get("name")
        if not isinstance(name, str) or not name or name in NEVER_REAP:
            continue
        when = _parse_aware(s.get("expires"))
        if when is None:
            continue
        if pool_pct >= SNAP_EARLY_REAP_PCT or when < now:
            out.append(s)
    return out


def churn_bytes_per_h(history: list) -> Optional[float]:
    """The pool's growth rate by least squares over (epoch, used_bytes)
    samples; None until the samples span an hour; shrinking counts as 0."""
    pts = [(float(t), float(u)) for t, u in history]
    if len(pts) < 2 or max(t for t, _ in pts) - min(t for t, _ in pts) < 3600:
        return None
    tm = sum(t for t, _ in pts) / len(pts)
    um = sum(u for _, u in pts) / len(pts)
    den = sum((t - tm) ** 2 for t, _ in pts)
    slope = sum((t - tm) * (u - um) for t, u in pts) / den * 3600
    return max(0.0, slope)


def snapshot_life_h(free_bytes: int, churn: Optional[float]) -> float:
    """Half the projected time to full, capped at SNAP_LIFE_H, floored at 1 h."""
    if not churn or churn <= 0:
        return float(SNAP_LIFE_H)
    return max(SNAP_FLOOR_H, min(float(SNAP_LIFE_H), 0.5 * free_bytes / churn))


def _age_h(snap: dict, now: datetime) -> Optional[float]:
    taken = _parse_aware(snap.get("taken"))
    return None if taken is None else (now - taken).total_seconds() / 3600


def _alarm_items(pool: dict, snaps: list, now: datetime, churn: Optional[float] = None) -> list:
    """(key, signature, priority, text) per alarm; the signature changes
    when the alarm's kind changes, which opens a new episode.

    An operator's snapshot alarms once its age passes the lifetime a
    sigmond snapshot would get on this pool now (half the projected time to
    full, cap 48 h), at any age once the pool sits at or above watch, and
    at once when its age is unknown.  It is never deleted."""
    items = []
    fill = _fill(pool)
    life = snapshot_life_h(pool["size_bytes"] * (100 - fill) / 100, churn)
    stage = _stage(fill)
    name = pool["name"]
    if stage:
        items.append((f"pool:{name}", stage, _STAGE_PRIO[stage],
                      f"pool {name} at {fill:.1f} % ({stage}): data {pool['data_pct']:.1f} %, "
                      f"metadata {pool['metadata_pct']:.1f} %"))
    for s in snaps:
        key = f"snap:{s['vmid']}:{s['name']}"
        if s.get("sigmond") is True:
            continue
        if s.get("label_fault"):
            items.append((key, "label", 4,
                          f"snapshot {s['name']} of VM {s['vmid']} carries a malformed sigmond label "
                          f"({s['label_fault']}); kept, an operator decides"))
            continue
        age = _age_h(s, now)
        age_txt = "of unknown age" if age is None else f"{age:.0f} h old"
        if s["name"] in NEVER_REAP:
            if stage:
                items.append((key, "pristine", 4,
                              f"snapshot {s['name']} of VM {s['vmid']} ({age_txt}) pins space "
                              f"while pool {name} sits at {fill:.1f} %"))
            continue
        if stage:
            why = f"pool {name} sits at {fill:.1f} %"
        elif age is None:
            why = "its age cannot be read"
        elif age >= life:
            why = f"a sigmond snapshot would live {life:.1f} h on this pool"
        else:
            continue
        items.append((key, "old", 4,
                      f"snapshot {s['name']} of VM {s['vmid']} is {age_txt} and carries no "
                      f"sigmond label ({why}); an operator decides"))
    return items


def host_alarms(pool: dict, snaps: list, now: datetime, churn: Optional[float] = None) -> list:
    """The pool's stage at or above watch, and the snapshots an operator should see."""
    return [text for _, _, _, text in _alarm_items(pool, snaps, now, churn)]


def guest_report(vmid, pool, vms_on_pool, snaps, alarms, now) -> str:
    """The guest's host report, schema 1 (lib/sigmond/storage/measure.py), ASCII only."""
    return json.dumps({
        "schema": 1,
        "generated_at": _iso(now),
        "vmid": int(vmid),
        "pool": {"name": pool["name"], "size_bytes": int(pool["size_bytes"]),
                 "data_pct": float(pool["data_pct"]), "metadata_pct": float(pool["metadata_pct"])},
        "vms_on_pool": sorted(int(v) for v in vms_on_pool),
        "snapshots": [{"vmid": int(s["vmid"]), "name": s["name"], "taken": s.get("taken"),
                       "sigmond": bool(s.get("sigmond")), "expires": s.get("expires")}
                      for s in snaps],
        "host_alarm": "; ".join(alarms),
    }, ensure_ascii=True, sort_keys=True)


def _history(state: dict, name: str, now: datetime) -> list:
    pools = state.get("pools") if isinstance(state.get("pools"), dict) else {}
    raw = (pools.get(name) or {}).get("history") or []
    t_now = now.timestamp()
    out = []
    for item in raw:
        try:
            t, u = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if t_now - CHURN_WINDOW_S <= t <= t_now:
            out.append([t, u])
    return out


def _vm_keys(vmid) -> tuple:
    """(exact keys, key prefixes) of the alarms that belong to one VM."""
    return ({f"guest:{vmid}", f"snaplist:{vmid}", f"config:{vmid}"},
            (f"snap:{vmid}:", f"reap:{vmid}:"))


class Unmeasured:
    """The alarm keys a run did not measure.  Their prior episodes carry
    forward unchanged: no "cleared" line, no new episode.  A run that cannot
    see a thing says nothing about it."""

    def __init__(self):
        self.everything = False
        self.exact: set = set()
        self.prefixes: list = []

    def add_vm(self, vmid) -> None:
        exact, prefixes = _vm_keys(vmid)
        self.exact |= exact
        self.prefixes.extend(prefixes)

    def __contains__(self, key) -> bool:
        return (self.everything or key in self.exact
                or any(key.startswith(p) for p in self.prefixes))


def _episodes(prior: dict, items: list, now: datetime, log, unmeasured=()) -> dict:
    """Log each alarm once per episode, a reminder every REMIND_S, and a line
    on clearing.  A prior key in `unmeasured` that this run raised nothing
    for keeps its episode as it stood."""
    out = {}
    stamp = now.timestamp()
    prior = prior if isinstance(prior, dict) else {}
    for key, sig, prio, text in items:
        old = prior.get(key) if isinstance(prior.get(key), dict) else None
        if old is None or old.get("sig") != sig:
            log(f"<{prio}>{text}")
            out[key] = {"sig": sig, "since": stamp, "logged_at": stamp}
        elif stamp - float(old.get("logged_at", 0)) >= REMIND_S:
            log(f"<{prio}>still: {text}")
            out[key] = {**old, "logged_at": stamp}
        else:
            out[key] = old
    for key in prior:
        if key in out:
            continue
        if key in unmeasured and isinstance(prior.get(key), dict):
            out[key] = prior[key]
            continue
        log(f"<5>cleared: {key}")
    return out


# ---------------------------------------------------------------------------
# the subcommands
# ---------------------------------------------------------------------------

def _report(host) -> int:
    """Measure, reap, report into each guest, alarm.  The state file gets
    written however the run ends, cut short or not."""
    now = host.now()
    state = host.read_state() or {}
    items, pools_state = [], {}
    unmeasured = Unmeasured()
    try:
        return _report_body(host, now, state, items, pools_state, unmeasured)
    finally:
        alarms = _episodes(state.get("alarms"), items, now, host.log, unmeasured)
        fills = [p["pool_pct"] for p in pools_state.values() if "pool_pct" in p]
        host.write_state({"schema": 1, "generated_at": _iso(now),
                          "pool_pct": max(fills) if fills else None,
                          "pools": pools_state, "alarms": alarms})


def _write_guest(host, vmid, content) -> bool:
    """One file-write; on failure one parser reset and one retry."""
    def attempt():
        try:
            return host.file_write(vmid, GUEST_REPORT_PATH, content).rc == 0
        except Exception:                           # noqa: BLE001
            return False
    if attempt():
        return True
    try:
        host.reset_agent(vmid)
    except Exception:                               # noqa: BLE001
        pass
    return attempt()


def _report_body(host, now, state, items, pools_state, unmeasured) -> int:
    rc = 0
    try:
        smap = parse_storage_cfg(host.storage_cfg())
        lvs = host.lvs()
        vms = sorted(json.loads(host.list_vms()), key=lambda v: int(v["vmid"]))
    except Exception as exc:                        # noqa: BLE001
        items.append(("host-storage", "x", 3, f"pm-storage: cannot read the host's storage: {exc}"))
        unmeasured.everything = True                # nothing measured: every episode stands
        prior = state.get("pools") if isinstance(state.get("pools"), dict) else {}
        for name in prior:                          # keep the churn history; no fill claimed
            pools_state[name] = {"history": _history(state, name, now)}
        return 1

    by_pool, running = {}, set()
    for vm in vms:
        vmid = int(vm["vmid"])
        if vm.get("status") == "running":
            running.add(vmid)
        try:
            where = pool_for_vm(host.qm_config(vmid), smap)
        except Exception:                           # noqa: BLE001
            items.append((f"config:{vmid}", "x", 4, f"config of VM {vmid} unreadable"))
            unmeasured.add_vm(vmid)                 # its snapshots and guest went unseen
            continue
        if where:
            by_pool.setdefault(where, []).append(vmid)

    # Measure every pool and list its snapshots.
    measured = []      # [name, pool, members, snaps, churn, local_items]
    for (vg, lv), members in sorted(by_pool.items()):
        name = f"{vg}/{lv}"
        try:
            pool = read_pool(lvs, vg, lv)
        except Exception as exc:                    # noqa: BLE001
            items.append((f"pool-unreadable:{name}", "x", 3, f"pool {name} unreadable: {exc}"))
            rc = 1
            unmeasured.exact.add(f"pool:{name}")    # the pool and its VMs went unseen
            for vmid in members:
                unmeasured.add_vm(vmid)
            if name in (state.get("pools") or {}):  # keep the churn history; no fill claimed
                pools_state[name] = {"history": _history(state, name, now)}
            continue
        fill = _fill(pool)
        history = _history(state, name, now)
        history.append([now.timestamp(), pool["size_bytes"] * pool["data_pct"] / 100])
        churn = churn_bytes_per_h(history)
        pools_state[name] = {**pool, "pool_pct": fill, "vms": members, "history": history,
                             "churn_bytes_per_h": churn}
        snaps, local = [], []
        for vmid in members:
            try:
                snaps += parse_snapshots(host.list_snapshots(vmid), vmid)
            except Exception:                       # noqa: BLE001
                local.append((f"snaplist:{vmid}", "x", 4, f"snapshots of VM {vmid} unreadable"))
                unmeasured.prefixes.append(f"snap:{vmid}:")     # its snapshots went unseen
        measured.append([name, pool, members, snaps, churn, local])

    # Phase 1: every reap on every pool, before any guest write.  A delete
    # is never interrupted; a stop request ends the phase between deletes.
    stopped = False
    for name, pool, members, snaps, churn, local in measured:
        fill = _fill(pool)
        for s in expired(snaps, now, fill):
            if host.stop_requested():
                stopped = True
                break
            why = (f"pool at {fill:.1f} %" if fill >= SNAP_EARLY_REAP_PCT
                   else f"expired {s['expires']}")
            try:
                res = host.delsnapshot(s["vmid"], s["name"])
                ok = res.rc == 0
                detail = (res.stderr or res.stdout or "").strip()[:200]
            except Exception as exc:                # noqa: BLE001
                ok, detail = False, str(exc)
            if ok:
                host.log(f"<5>reaped snapshot {s['name']} of VM {s['vmid']} ({why})")
                snaps.remove(s)
            else:
                local.append((f"reap:{s['vmid']}:{s['name']}", "x", 3,
                              f"could not delete snapshot {s['name']} of VM {s['vmid']}: {detail}"))
        if stopped:
            break

    for m in measured:
        name, pool, members, snaps, churn, local = m
        m[5] = local + _alarm_items(pool, snaps, now, churn)
        items.extend(m[5])

    # A guest whose write this run never tried keeps its episode.
    unwritten = {f"guest:{v}" for _, _, members, _, _, _ in measured for v in members}
    unmeasured.exact |= unwritten
    if stopped or host.stop_requested():
        host.log("<4>pm-storage: stop requested; reaping ended between deletes, "
                 "guest reports skipped")
        return rc

    # Phase 2: the guest reports, under their own deadline.
    start = host.monotonic()
    for name, pool, members, snaps, churn, local in measured:
        texts = [t for _, _, _, t in local]
        for vmid in members:
            if vmid not in running:
                unmeasured.exact.discard(f"guest:{vmid}")
                continue
            if host.stop_requested():
                return rc
            unmeasured.exact.discard(f"guest:{vmid}")
            if host.monotonic() - start >= GUEST_WRITE_BUDGET_S:
                items.append((f"guest:{vmid}", "deadline", 4,
                              f"guest {vmid} report skipped: the guest-write phase passed its "
                              f"{GUEST_WRITE_BUDGET_S} s deadline"))
                continue
            content = guest_report(vmid, pool, members, snaps, texts, now)
            if not _write_guest(host, vmid, content):
                items.append((f"guest:{vmid}", "unreachable", 3, f"guest {vmid} unreachable"))
    return rc


_DISK_KEY = re.compile(r"^(scsi|virtio|sata|ide)(\d+)$")


def data_disks(qm_config_text: str) -> list:
    """The VM's disks other than its boot disk: data disks a whole-VM
    snapshot would pin (data-volume design 2026-11-08).  The boot disk is the
    first disk in `boot: order=`, else `bootdisk:`, else scsi0.  CD-ROMs,
    cloud-init, EFI and TPM state are not disks here."""
    disks, boot = [], None
    for ln in qm_config_text.splitlines():
        key, _, val = ln.partition(":")
        key, val = key.strip(), val.strip()
        if key == "boot" and "order=" in val:
            for item in val.split("order=", 1)[1].split(",")[0].split(";"):
                if _DISK_KEY.match(item.strip()):
                    boot = boot or item.strip()
        elif key == "bootdisk" and not boot:
            boot = val
        elif _DISK_KEY.match(key) and "media=cdrom" not in val and "cloudinit" not in val:
            disks.append(key)
    boot = boot or ("scsi0" if "scsi0" in disks else (disks[0] if disks else None))
    return [d for d in disks if d != boot]


def _refuse(msg: str) -> int:
    print(f"pm-storage: snapshot refused: {msg}", file=sys.stderr)
    return 3


def _vm_running(host, vmid: int) -> bool:
    try:
        return any(int(vm.get("vmid", -1)) == int(vmid) and vm.get("status") == "running"
                   for vm in json.loads(host.list_vms()))
    except Exception:                               # noqa: BLE001
        return True     # unknown: treat as running, so radiod is asked about


def _snapshot(host, vmid: int, name: str, hours: Optional[float],
              live: bool = False, include_data_disks: bool = False) -> int:
    if hours is not None and not hours > 0:
        print("pm-storage: --hours must be positive", file=sys.stderr)
        return 2
    now = host.now()
    try:
        cfg = host.qm_config(vmid)
        smap = parse_storage_cfg(host.storage_cfg())
    except Exception as exc:                        # noqa: BLE001
        print(f"pm-storage: cannot read VM {vmid}: {exc}", file=sys.stderr)
        return 2
    extra = data_disks(cfg)
    if extra and not include_data_disks:
        return _refuse(f"VM {vmid} has a data disk ({', '.join(extra)}); a qm snapshot pins "
                       f"its blocks in the pool until deleted.  Protect the system disk with "
                       f"`vzdump {vmid} --mode snapshot` (it leaves backup=0 disks out), or pass "
                       f"--include-data-disks for a snapshot you will delete within hours")
    where = pool_for_vm(cfg, smap)
    if where is None:
        return _refuse(f"VM {vmid} sits on no lvmthin pool this tool can measure")
    try:
        pool = read_pool(host.lvs(), *where)
    except Exception as exc:                        # noqa: BLE001
        return _refuse(f"pool {where[0]}/{where[1]} unreadable: {exc}")
    fill = _fill(pool)
    if fill >= SNAP_REFUSE_PCT:
        return _refuse(f"pool {pool['name']} at {fill:.1f} %; snapshots stop at "
                       f"{SNAP_REFUSE_PCT:.0f} %")
    free = pool["size_bytes"] * (100 - fill) / 100
    churn = churn_bytes_per_h(_history(host.read_state() or {}, pool["name"], now))
    if churn:
        room = free / churn
        if room < SNAP_MIN_ROOM_H:
            return _refuse(f"pool {pool['name']} projected to fill in {room:.1f} h at "
                           f"{churn / 1e9:.1f} GB/h; refused below {SNAP_MIN_ROOM_H:.0f} h")
        why = f"half the {room:.1f} h until the pool is full at {churn / 1e9:.1f} GB/h"
    else:
        why = "no growth measured on the pool yet"
    life = snapshot_life_h(free, churn)
    if life >= SNAP_LIFE_H:
        why += f", capped at {SNAP_LIFE_H} h"
    if hours is not None and hours < life:
        life, why = hours, f"--hours {hours:g} (the pool allows {life:.1f} h)"
    expires = _iso((now + timedelta(hours=life)).replace(microsecond=0))
    description = SNAP_TAG + expires
    # A snapshot of a running VM pauses it.  radiod's RX888 then overflows and
    # loses ~100-200 ms of samples, and the guest's clock pauses too, so radiod
    # cannot see the loss (B4 2026-11-10: 185 ms, seen only by the TS-1 pilot).
    # A stopped radiod is a clean, visible restart instead; its consumers
    # restart on their own when it starts (sigmond-radiod-consumers@).
    stopped = []
    if live:
        print(f"pm-storage: --live: radiod in VM {vmid} keeps running and may silently lose "
              f"~0.1-0.2 s of samples while the snapshot pauses the VM", file=sys.stderr)
    elif _vm_running(host, vmid):
        units = host.guest_radiods(vmid)
        if units is None:
            return _refuse(f"the guest agent in VM {vmid} cannot say which radiod units run; "
                           f"a live snapshot costs radiod samples it cannot see. "
                           f"Use --live to take it anyway")
        if units:
            r = host.guest_systemctl(vmid, "stop", units)
            if r.rc != 0:
                host.guest_systemctl(vmid, "start", units)
                return _refuse(f"could not stop {' '.join(units)} in VM {vmid}: "
                               f"{(r.stderr or r.stdout).strip()}")
            stopped = list(units)
            print(f"stopped {' '.join(stopped)} in VM {vmid} for the snapshot")
    res = host.snapshot(vmid, name, description)
    if stopped:
        r = host.guest_systemctl(vmid, "start", stopped)
        if r.rc != 0:
            print(f"pm-storage: could not start {' '.join(stopped)} in VM {vmid} again: "
                  f"{(r.stderr or r.stdout).strip()}", file=sys.stderr)
            return 1
        print(f"started {' '.join(stopped)} again; its consumers restart on their own")
    if res.rc != 0:
        print(f"pm-storage: qm snapshot failed: {(res.stderr or res.stdout).strip()}",
              file=sys.stderr)
        return 1
    print(f"snapshot {name} of VM {vmid}: lifetime {life:.1f} h, expires {expires}; {why}")
    return 0


# ---------------------------------------------------------------------------
# the real host (impure)
# ---------------------------------------------------------------------------

def _run(argv, timeout=60) -> RunResult:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return RunResult(124, "", f"timed out after {timeout} s")
    return RunResult(p.returncode, p.stdout, p.stderr)


def _out(argv, timeout=60) -> str:
    r = _run(argv, timeout)
    if r.rc != 0:
        raise RuntimeError(f"{argv[0]} exited {r.rc}: {r.stderr.strip()[:200]}")
    return r.stdout


def _qga_reset(vmid) -> bool:
    """One parser reset for the VM's guest agent; True when it answered.
    Copied from pm-heartbeat.py; tests/test_qga_reset.py guards the copy."""
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(4)
            s.connect(f"{QGA_DIR}/{vmid}.qga")
            s.sendall(QGA_RESET)
            end, got = time.monotonic() + 4, b""
            while b"4242" not in got:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                s.settimeout(left)
                data = s.recv(4096)
                if not data:
                    return False
                got += data
            return True
    except OSError:
        return False


def _note_reset(who: str) -> None:
    """Leave the one line a reset that worked leaves (pm-heartbeat reports
    it).  Never raises."""
    try:
        os.makedirs(os.path.dirname(QGA_NOTE), exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime(_TS)
        with open(QGA_NOTE, "w", encoding="ascii") as fh:
            fh.write(f"{stamp} {who}\n")
    except OSError:
        pass


_STOP = {"requested": False}


def _on_sigterm(signum, frame):
    """systemd's stop (KillMode=mixed reaches only this process): finish the
    delete under way, then end between steps.  Never kill a delsnapshot."""
    _STOP["requested"] = True


def _file_write_real(node, vmid, path, content) -> RunResult:
    """Write one file in the guest through the agent.  Pure ASCII or nothing."""
    argv = ["pvesh", "create", f"/nodes/{node}/qemu/{int(vmid)}/agent/file-write",
            "--file", path, "--content", content]
    if not all(a.isascii() for a in argv):
        raise ValueError("refusing a non-ASCII guest-agent request")
    return _run(argv, timeout=30)


class RealHost:
    def __init__(self):
        self._node = socket.gethostname().split(".")[0]

    def now(self):
        return datetime.now(timezone.utc)

    def node(self):
        return self._node

    def storage_cfg(self):
        with open("/etc/pve/storage.cfg", encoding="utf-8") as fh:
            return fh.read()

    def lvs(self):
        return _out(["lvs", "--reportformat", "json", "--units", "b", "--nosuffix", "-o",
                     "lv_name,vg_name,lv_size,data_percent,metadata_percent,lv_attr"])

    def list_vms(self):
        return _out(["pvesh", "get", f"/nodes/{self._node}/qemu", "--output-format", "json"])

    def qm_config(self, vmid):
        return _out(["qm", "config", str(int(vmid))])

    def list_snapshots(self, vmid):
        return _out(["pvesh", "get", f"/nodes/{self._node}/qemu/{int(vmid)}/snapshot",
                     "--output-format", "json"])

    # No timeout on either: a killed snapshot create or delete leaves the VM
    # locked ("snapshot-delete"), which blocks start, backup and snapshots
    # until someone runs `qm unlock`.
    def delsnapshot(self, vmid, name):
        return _run(["qm", "delsnapshot", str(int(vmid)), name], timeout=None)

    def snapshot(self, vmid, name, description):
        return _run(["qm", "snapshot", str(int(vmid)), name, "--description", description],
                    timeout=None)

    def _guest_exec(self, vmid, argv, timeout=90):
        args = ["qm", "guest", "exec", str(int(vmid)), "--timeout", str(timeout), "--"] + argv
        if not all(a.isascii() for a in args):
            raise ValueError("refusing a non-ASCII guest-agent request")
        r = _run(args, timeout=timeout + 15)
        if r.rc != 0:
            return r
        try:
            res = json.loads(r.stdout)
        except ValueError:
            return RunResult(1, r.stdout, "unreadable guest-exec reply")
        return RunResult(int(res.get("exitcode", 1)), res.get("out-data", ""),
                         res.get("err-data", ""))

    def guest_radiods(self, vmid):
        """Active radiod@ units in the guest, or None when the agent cannot say."""
        r = self._guest_exec(vmid, ["systemctl", "list-units", "--plain", "--no-legend",
                                    "--state=active", "radiod@*.service"], timeout=30)
        if r.rc != 0:
            return None
        return [line.split()[0] for line in r.stdout.splitlines() if line.strip()]

    def guest_systemctl(self, vmid, action, units):
        return self._guest_exec(vmid, ["systemctl", action] + list(units))

    def monotonic(self):
        return time.monotonic()

    def reset_agent(self, vmid):
        ok = _qga_reset(vmid)
        if ok:
            _note_reset("pm-storage")
        return ok

    def stop_requested(self):
        return _STOP["requested"]

    def file_write(self, vmid, path, content):
        return _file_write_real(self._node, vmid, path, content)

    def read_state(self):
        try:
            with open(LOCAL_STATE, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def write_state(self, state):
        os.makedirs(os.path.dirname(LOCAL_STATE), mode=0o755, exist_ok=True)
        tmp = LOCAL_STATE + ".tmp"
        with open(tmp, "w", encoding="ascii") as fh:
            json.dump(state, fh, ensure_ascii=True, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, LOCAL_STATE)

    def log(self, line):
        print(line, flush=True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None, host=None) -> int:
    ap = argparse.ArgumentParser(prog="pm-storage.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report", help="measure, reap, report into each guest, alarm")
    sp = sub.add_parser("snapshot", help="take a labelled snapshot the pool can carry")
    sp.add_argument("vmid", type=int)
    sp.add_argument("name")
    sp.add_argument("--hours", type=float, default=None)
    sp.add_argument("--live", action="store_true",
                    help="leave radiod running (it may silently lose ~0.1-0.2 s of samples)")
    sp.add_argument("--include-data-disks", action="store_true",
                    help="snapshot even with a data disk attached (it pins the data disk's blocks)")
    args = ap.parse_args(argv)
    if host is None:
        host = RealHost()
        signal.signal(signal.SIGTERM, _on_sigterm)
    if args.cmd == "report":
        return _report(host)
    return _snapshot(host, args.vmid, args.name, args.hours, live=args.live,
                     include_data_disks=args.include_data_disks)


if __name__ == "__main__":
    sys.exit(main())

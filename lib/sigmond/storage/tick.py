"""The storage tick: one pass of the capacity ledger, every 2 minutes.

The pass measures the stores and each client's holdings, lets the ladder
decide, acts on the decision (trim and final calls, the stop lever,
clean-up), updates the alarm book, and writes its state and a status file.
Every outside effect goes through a `Runners` value, so tests hand in fakes
and never touch /etc, /run, /var or systemctl.

Exit codes: 0 the pass ran; 75 (EX_TEMPFAIL) another pass held the lock;
1 a ledger fault, raised as the `ledger-fault` alarm.

TrimRecord upkeep (the ladder only reads the records; this module keeps
them, as ladder.TrimRecord's docstring asks):

- A trim or final call against a client with no open streak starts one
  (`requested_at` = now).  Calls while the streak stays unanswered keep its
  `requested_at`.  An ok answer ends the streak (`answered_at`, `ok=True`).
- `final_since` holds the first tick the client sat over its target on a
  store at FINAL; it clears on the first tick it does not.
- A client's record clears entirely on the first tick it sits over its
  target on no store at TIGHTEN or above.  So a failure in one episode never
  counts against the next.
- On load, a record that no tick has touched for STALE_TRIM_S drops.  The
  tick touches a record on every pass that keeps it, so a streak the tick
  keeps asking about never goes stale; a record left from a ledger that
  stopped running does.
"""
from __future__ import annotations

import fcntl
import json
import re
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .alarms import AlarmBook, journal_line
from .clientcall import ClientAnswer
from .config import CONFIG_PATH, hands_off_active, load_storage_config
from .ladder import Severity, TrimRecord, decide
from .model import (STATE_RESERVE_BYTES, Holding, Registration, Stage,
                    allot, allottable_bytes, apply_pool_limit, pool_room_bytes)
from .registry import registrations_from_views

STATE_PATH = Path("/var/lib/sigmond/storage/state.json")
STATUS_PATH = Path("/run/sigmond/storage-status.json")
LOCK_PATH = Path("/run/sigmond/storage.lock")
DU_CACHE_S = 1800
STALE_TRIM_S = 3600
UNCLAIMED_EVERY_S = 6 * 3600
CLEANUP_EVERY_S = 3600
EX_TEMPFAIL = 75
ARCHIVE_STORE = "archive"   # measure.discover_stores' name for the archive drive


@dataclass
class Runners:
    load_views: Callable            # () -> {name: ClientView}
    discover_stores: Callable       # (paths) -> (stores, faults)
    path_bytes: Callable            # (paths) -> int
    read_host_report: Callable      # (now) -> (HostReport | None, fault | None)
    host_report_expected: Callable  # (cfg_expect) -> bool
    call_holdings: Callable         # (binary) -> ClientAnswer
    call_trim: Callable             # (binary, fit_bytes) -> ClientAnswer
    call_final: Callable            # (binary, fit_bytes, policy) -> ClientAnswer
    stop_client: Callable           # (client) -> (ok, reason)
    run_cleanup: Callable           # () -> [CleanupResult]
    report_unclaimed: Callable      # (claimed paths) -> [(path, bytes)]
    expire_log_levels: Callable     # (now) -> [restored keys]
    sighup: Callable                # (client) -> [units that failed]
    journal: Callable               # (line) -> None
    binary_for: Callable            # (client) -> str | None
    client_active: Callable         # (client) -> bool: any of its services active


# ---------------------------------------------------------------- real runners

def _binary_for(client: str) -> Optional[str]:
    """The client's binary, found by the contract adapter's find_binary()."""
    from ..clients import load_adapter
    from ..clients.contract import ContractAdapter
    adapter = load_adapter(client)
    if isinstance(adapter, ContractAdapter):
        return adapter.find_binary()
    cands = getattr(adapter, "_candidate_binaries", None)   # hf-timestd's adapter
    if cands is None:
        return None
    for cand in cands():
        ca = ContractAdapter()
        ca.name, ca.binary = client, cand
        found = ca.find_binary()
        if found:
            return found
    return None


def _units_of(client: str) -> list:
    from .clientcall import _default_resolve
    try:
        return [u for u in _default_resolve(client) if not u.orphaned]
    except (ValueError, OSError):
        return []


def _client_active(client: str) -> bool:
    import subprocess
    for ref in _units_of(client):
        if ref.kind != "service":
            continue
        try:
            r = subprocess.run(["systemctl", "is-active", "--quiet", ref.unit],
                               capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            return True
    return False


def _sighup(client: str) -> list:
    from ..log_cmd import send_sighup
    units = [u.unit for u in _units_of(client)]
    return send_sighup(units) if units else []


def _journal(line: str) -> None:
    print(line, flush=True)


def default_runners() -> Runners:
    from .. import archive, log_cmd
    from ..paths import COORDINATION_ENV
    from ..sysview import build_system_view
    from . import cleanup, clientcall, measure
    return Runners(
        load_views=lambda: build_system_view().client_views,
        discover_stores=lambda paths: measure.discover_stores(paths, str(archive.MOUNT)),
        path_bytes=measure.path_bytes,
        read_host_report=lambda now: measure.read_host_report(measure.HOST_REPORT_PATH, now),
        host_report_expected=measure.host_report_expected,
        call_holdings=clientcall.call_holdings,
        call_trim=clientcall.call_trim,
        call_final=clientcall.call_final,
        stop_client=clientcall.stop_client,
        run_cleanup=lambda: cleanup.run_cleanup(),
        report_unclaimed=cleanup.report_unclaimed,
        expire_log_levels=lambda now: log_cmd.expire_log_levels(COORDINATION_ENV, now),
        sighup=_sighup,
        journal=_journal,
        binary_for=_binary_for,
        client_active=_client_active,
    )


# ---------------------------------------------------------------- state

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _load_state(path: Path, journal) -> dict:
    try:
        raw = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        journal(f"<4>storage: state file {path} unreadable ({exc}); starting fresh")
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_trims(raw, now: float) -> tuple:
    """(records, touched) from the state, dropping records gone stale."""
    trims, touched = {}, {}
    if not isinstance(raw, dict):
        return trims, touched
    for client, d in raw.items():
        if not isinstance(d, dict):
            continue
        req = _num(d.get("requested_at"))
        if req is None:
            continue
        ans = _num(d.get("answered_at"))
        fin = _num(d.get("final_since"))
        last = _num(d.get("touched"))
        last = max(x for x in (req, ans, last) if x is not None)
        if now - last > STALE_TRIM_S:
            continue
        trims[str(client)] = TrimRecord(req, ans, bool(d.get("ok", False)), fin)
        touched[str(client)] = last
    return trims, touched


def _write_json(path: Path, obj) -> None:
    from ..safe_write import write_owned
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_owned(path, json.dumps(obj, indent=1, sort_keys=True) + "\n", mode=0o644)


def _since(state: dict, active: dict, now: float) -> dict:
    """When each active alarm's episode began; a severity change starts anew."""
    old = state.get("since") if isinstance(state.get("since"), dict) else {}
    out = {}
    for key, (sev, _msg) in active.items():
        prev = old.get(key)
        if isinstance(prev, dict) and prev.get("sev") == int(sev) and _num(prev.get("at")) is not None:
            out[key] = prev
        else:
            out[key] = {"at": now, "sev": int(sev)}
    return out


def _alarm_rows(active: dict, since: dict) -> list:
    return [{"key": k, "severity": Severity(sev).name, "message": msg,
             "since": _iso(since[k]["at"]) if k in since else None}
            for k, (sev, msg) in sorted(active.items())]


# ---------------------------------------------------------------- the pass

def _fail(reason: str) -> ClientAnswer:
    return ClientAnswer(False, None, {}, False, reason, False)


def _view_paths(views) -> list:
    out = []
    for name in sorted(views):
        for iv in views[name].instances:
            for s in iv.data_sinks:
                if s.kind == "file" and s.path and s.path not in out:
                    out.append(s.path)
            for dw in iv.disk_writes:
                if dw.path and dw.path not in out:
                    out.append(dw.path)
    return out


def _measure(regs, path_bytes, du: dict, used: set, now: float, c, store) -> int:
    paths = sorted(p for r in regs if r.client == c and r.store == store for p in r.paths)
    key = f"{c}|{store}"
    used.add(key)
    hit = du.get(key)
    if (isinstance(hit, dict) and hit.get("paths") == paths
            and _num(hit.get("at")) is not None and now - hit["at"] < DU_CACHE_S
            and isinstance(hit.get("bytes"), int)):
        return hit["bytes"]
    n = int(path_bytes(paths))
    du[key] = {"at": now, "bytes": n, "paths": paths}
    return n


def _record_answer(trims: dict, touched: dict, c: str, ans: ClientAnswer, now: float) -> None:
    rec = trims.get(c)
    if rec is None or rec.ok:
        rec = TrimRecord(requested_at=now, final_since=rec.final_since if rec else None)
        trims[c] = rec
    rec.answered_at = now
    rec.ok = bool(ans.ok)
    touched[c] = now


def _pass(now: datetime, r: Runners, config_path, state: dict) -> tuple:
    """One pass.  Returns (state, status, active alarms)."""
    t = now.timestamp()
    journal = r.journal
    cfg = load_storage_config(config_path)
    levels = cfg.levels
    active: dict = {}
    for i, prob in enumerate(cfg.problems):
        active[f"config:{i}"] = (Severity.WARNING, f"storage.toml: {prob}")

    restored = r.expire_log_levels(now)
    views = r.load_views()
    _restore_levels(restored, views, r)

    stopped = state.get("stopped") if isinstance(state.get("stopped"), dict) else {}
    for c in sorted(stopped):
        if r.client_active(c):
            journal(f"<6>storage: {c} runs again; the ledger's stop no longer holds it")
            del stopped[c]

    stores_list, faults = r.discover_stores(_view_paths(views))
    for f in faults:
        m = re.search(r"\(([^)]*)\) unreadable", f)
        active[f"store-missing:{m.group(1) if m else f}"] = (Severity.ERROR, f)

    expected = bool(r.host_report_expected(cfg.expect_host_report))
    report, fault = r.read_host_report(now)
    host_fault = fault if expected else None
    pool = report.pool if report is not None else None
    age_s = int(round((now - report.generated_at).total_seconds())) if report else None

    regs = registrations_from_views(views, stores_list)

    # Holdings.  A managed client on one store reports its own bytes; a
    # client whose `holdings` verb is unknown is unmanaged (§17.7); every
    # other figure comes from the filesystem, cached for DU_CACHE_S.
    du = state.get("du") if isinstance(state.get("du"), dict) else {}
    du_used: set = set()
    holdings: dict = {}
    by_client: dict = {}
    for reg in regs:
        by_client.setdefault(reg.client, []).append(reg)
    final_regs: list = []
    for c in sorted(by_client):
        cregs = by_client[c]
        ans = None
        if any(x.managed for x in cregs):
            binary = r.binary_for(c)
            ans = r.call_holdings(binary) if binary else _fail("binary not found")
            if ans.unsupported:
                cregs = [Registration(x.client, x.store, x.paths, 0, None, False) for x in cregs]
                ans = None
        final_regs.extend(cregs)
        single = len(cregs) == 1
        for x in cregs:
            if ans is not None and ans.ok and single:
                holdings[(c, x.store)] = Holding(c, x.store, dict(ans.bytes_by_class), False)
            else:
                n = _measure(cregs, r.path_bytes, du, du_used, t, c, x.store)
                holdings[(c, x.store)] = Holding(c, x.store, {"unclassified": n}, True)
    regs = final_regs
    du = {k: v for k, v in du.items() if k in du_used}

    # Allotments, per guest store; a filling pool caps their growth.
    stores = {s.name: s for s in stores_list}
    allotments: dict = {}
    for s in stores_list:
        registered = sum(h.total for (c, sn), h in holdings.items() if sn == s.name)
        reserve = STATE_RESERVE_BYTES if s.mount == "/" else 0
        allottable = allottable_bytes(s, levels, registered, reserve)
        if pool is not None and s.name != ARCHIVE_STORE:
            if cfg.pool_share is not None:
                share = cfg.pool_share
            else:
                share = 1.0 / len(report.vms_on_pool) if report.vms_on_pool else 1.0
            allottable = apply_pool_limit(allottable, registered,
                                          pool_room_bytes(pool, levels, share))
        allotments[s.name] = allot([x for x in regs if x.store == s.name],
                                   allottable, cfg.shares)
    if pool is not None:
        stores[pool.name] = pool

    trims, touched = _load_trims(state.get("trims"), t)
    hands_off = hands_off_active(cfg, now)
    decision = decide(stores=stores, allotments=allotments, holdings=holdings,
                      registrations=regs, levels=levels, trims=trims,
                      directives=cfg.directives, hands_off=hands_off,
                      host_fault=host_fault, now=t)
    active.update(decision.alarms)

    # Who sits over its target on a store under pressure.
    pressed, over_final = set(), set()
    for sname, tgt in decision.targets.items():
        stage = decision.stages[sname]
        if stores[sname].kind == "pool" or stage < Stage.TIGHTEN:
            continue
        for c, target in tgt.items():
            h = holdings.get((c, sname))
            if h is not None and h.total > target:
                pressed.add(c)
                if stage >= Stage.FINAL:
                    over_final.add(c)

    _act_trims(decision.actions, decision.targets, holdings, cfg, trims, touched,
               stopped, r, t)
    managed = {x.client for x in regs if x.managed}
    for a in _unique(decision.actions, "stop"):
        c = a.client
        if c in stopped:
            continue
        ok, why = r.stop_client(c)
        if ok:
            reason = (f"it failed to answer a trim request" if c in managed
                      else "it is unmanaged and sat over its target at FINAL")
            stopped[c] = {"at": t, "store": a.store, "reason": reason, "detail": why}
        else:
            active[f"stop-failed:{c}"] = (
                Severity.CRITICAL,
                f"store {a.store}: the storage ledger tried to stop {c} and could "
                f"not: {why}; {c} still runs")
    for c in sorted(stopped):
        s = stopped[c]
        active[f"stopped:{c}"] = (
            Severity.CRITICAL,
            f"storage ledger stopped {c} at {_iso(s.get('at', t))} on store "
            f"{s.get('store')}: {s.get('reason')}.  It stays down until an operator "
            f"starts it (smd start {c})")

    # Record upkeep: final_since, then clear what no pressure holds.
    for c in over_final:
        rec = trims.get(c)
        if rec is None:
            rec = trims[c] = TrimRecord(requested_at=t)
        if rec.final_since is None:
            rec.final_since = t
    for c in list(trims):
        if c not in pressed:
            del trims[c]
            continue
        if c not in over_final:
            trims[c].final_since = None
        touched[c] = t

    # Clean-up, at most hourly.
    last_cleanup = _num(state.get("last_cleanup")) or 0.0
    if any(a.kind == "cleanup" for a in decision.actions) and t - last_cleanup >= CLEANUP_EVERY_S:
        last_cleanup = t
        for res in r.run_cleanup():
            journal(f"<{6 if res.ok else 4}>storage cleanup {res.step}: "
                    f"{'ok' if res.ok else 'FAILED'} {res.detail}".rstrip())

    last_unclaimed = _num(state.get("last_unclaimed")) or 0.0
    unclaimed = state.get("unclaimed") if isinstance(state.get("unclaimed"), list) else []
    if t - last_unclaimed >= UNCLAIMED_EVERY_S:
        last_unclaimed = t
        claimed = sorted({p for x in regs for p in x.paths})
        unclaimed = [[str(p), int(b)] for p, b in r.report_unclaimed(claimed)]
        if unclaimed:
            top = ", ".join(f"{p} {b / (1 << 30):.1f} GiB" for p, b in unclaimed[:3])
            journal(f"<5>storage unclaimed: {len(unclaimed)} large unclaimed "
                    f"path(s), for an operator to judge: {top}")

    new_state = {
        "schema": 1,
        "trims": {c: {"requested_at": x.requested_at, "answered_at": x.answered_at,
                      "ok": x.ok, "final_since": x.final_since,
                      "touched": touched.get(c, t)}
                  for c, x in sorted(trims.items())},
        "stopped": stopped,
        "du": du,
        "last_cleanup": last_cleanup,
        "last_unclaimed": last_unclaimed,
        "unclaimed": unclaimed,
    }

    worst = max(decision.stages.values(), default=Stage.OK)
    status = {
        "schema": 1,
        "worst_stage": Stage(worst).name,
        "stores": [
            {"name": s.name, "kind": s.kind, "capacity": s.capacity, "used": s.used,
             "used_pct": round(s.used_pct, 2), "stage": decision.stages[s.name].name,
             "allottable": allotments[s.name].allottable if s.name in allotments else None}
            for s in sorted(stores.values(), key=lambda s: s.name)],
        "clients": [
            {"client": x.client, "store": x.store,
             "held": holdings[(x.client, x.store)].total,
             "estimated": holdings[(x.client, x.store)].estimated,
             "allotment": allotments[x.store].per_client.get(x.client),
             "target": decision.targets.get(x.store, {}).get(x.client),
             "floor": x.floor_bytes, "managed": x.managed,
             "stopped_by_storage": x.client in stopped,
             "trim": _trim_row(trims.get(x.client))}
            for x in sorted(regs, key=lambda x: (x.client, x.store))],
        "host_report": {"expected": expected, "fault": fault if (expected or report) else None,
                        "age_s": age_s},
        "hands_off": ({"until": cfg.hands_off_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "reason": cfg.hands_off_reason} if hands_off else None),
        "unclaimed": unclaimed,
        "config_problems": list(cfg.problems),
    }
    return new_state, status, active


def _trim_row(rec: Optional[TrimRecord]):
    if rec is None:
        return None
    return {"requested_at": _iso(rec.requested_at),
            "answered_at": _iso(rec.answered_at) if rec.answered_at else None,
            "ok": rec.ok,
            "final_since": _iso(rec.final_since) if rec.final_since else None}


def _unique(actions, kind) -> list:
    seen, out = set(), []
    for a in actions:
        if a.kind == kind and a.client not in seen:
            seen.add(a.client)
            out.append(a)
    return out


def _act_trims(actions, targets, holdings, cfg, trims, touched, stopped, r, t) -> None:
    """One trim call per client, then a final call where FINAL asks for one
    and the trim left the client over.  A client on several stores is asked
    to fit the sum: its target on each store asked, its holding elsewhere."""
    asked: dict = {}
    finals: dict = {}
    for a in actions:
        if a.kind == "trim":
            asked.setdefault(a.client, set()).add(a.store)
        elif a.kind == "final":
            finals.setdefault(a.client, a.policy)
    for c in sorted(asked):
        if c in stopped:
            continue
        fit = 0
        for (hc, sname), h in holdings.items():
            if hc == c:
                fit += targets[sname][c] if sname in asked[c] else h.total
        binary = r.binary_for(c)
        ans = r.call_trim(binary, fit) if binary else _fail("binary not found")
        _record_answer(trims, touched, c, ans, t)
        if c not in finals:
            continue
        if ans.ok and ans.held_bytes is not None and ans.held_bytes <= fit:
            continue
        policy = finals[c] or cfg.directives.get(c)
        ans = r.call_final(binary, fit, policy) if binary else _fail("binary not found")
        _record_answer(trims, touched, c, ans, t)


def _restore_levels(restored, views, r: Runners) -> None:
    from ..log_cmd import client_env_key
    for key in restored:
        client = next((n for n in sorted(views) if client_env_key(n) == key), None)
        if client is None:
            r.journal(f"<6>storage log-level: {key} back to its routine level; "
                      f"no single client to SIGHUP, so clients take it at their next restart")
            continue
        failed = r.sighup(client)
        r.journal(f"<6>storage log-level: {key} back to its routine level; SIGHUP {client}"
                  + (f" (failed: {', '.join(failed)})" if failed else ""))


# ---------------------------------------------------------------- entry point

def run_tick(now: datetime, runners: Runners, *, config_path=CONFIG_PATH,
             state_path=STATE_PATH, status_path=STATUS_PATH,
             lock_path=LOCK_PATH) -> int:
    if now.tzinfo is None:
        raise ValueError("run_tick needs an aware UTC time")
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return EX_TEMPFAIL
        return _locked(now, runners, config_path, Path(state_path), Path(status_path))
    finally:
        fh.close()


def _locked(now, r, config_path, state_path, status_path) -> int:
    t = now.timestamp()
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state: dict = {}
    book = AlarmBook()
    try:
        state = _load_state(state_path, r.journal)
        book = AlarmBook.from_dict(state.get("alarms", {}) if isinstance(state.get("alarms"), dict) else {})
        new_state, status, active = _pass(now, r, config_path, json.loads(json.dumps(state)))
        for e in book.update(active, t):
            r.journal(journal_line(e))
        since = _since(state, active, t)
        new_state["alarms"] = book.to_dict()
        new_state["since"] = since
        status["generated_at"] = stamp
        status["alarms"] = _alarm_rows(active, since)
        _write_json(state_path, new_state)
        _write_json(status_path, status)
        return 0
    except Exception as exc:
        _fault(now, stamp, exc, r, state, book, state_path, status_path)
        return 1


def _fault(now, stamp, exc, r, state, book, state_path, status_path) -> None:
    """Raise `ledger-fault` and hold every other alarm as it stood."""
    t = now.timestamp()
    msg = f"storage ledger fault: {type(exc).__name__}: {exc}"
    held = {}
    for k, v in book.to_dict().get("active", {}).items():
        held[k] = (Severity(v["severity"]), v.get("message", ""))
    held["ledger-fault"] = (Severity.CRITICAL, msg)
    try:
        for e in book.update(held, t):
            r.journal(journal_line(e))
            if e.key == "ledger-fault" and e.kind == "raise":
                for line in traceback.format_exception(exc):
                    for part in line.rstrip().splitlines():
                        r.journal(f"<3>storage traceback: {part}")
    except Exception:
        pass
    since = _since(state, held, t)
    try:
        kept = dict(state)
        kept["alarms"] = book.to_dict()
        kept["since"] = since
        _write_json(state_path, kept)
    except Exception:
        pass
    try:
        _write_json(status_path, {
            "schema": 1, "generated_at": stamp, "worst_stage": "FAULT",
            "stores": [], "clients": [],
            "host_report": None, "hands_off": None,
            "alarms": _alarm_rows(held, since),
            "unclaimed": state.get("unclaimed", []) if isinstance(state.get("unclaimed"), list) else [],
            "config_problems": [], "fault": msg})
    except Exception:
        pass

"""The ledger's retention pass: `smd storage expire`, hourly.

sigmond sets how long each client keeps what it writes (the client's
declared `retention_days`, or a storage.toml `[retention]` entry) and asks
the client, through its `storage expire` verb, to delete what lies past
that.  sigmond deletes no client file and no client row itself.

A pass plans one job per sink that declares `expires`, then calls the
clients one at a time while its time budget lasts.  The state file keeps
`next_index`, so a pass that ran out of budget resumes at the next job.
Each call starts one client process (one target, one database), which the
client's permanent privilege drop relies on.

The pass has its own lock, so the 2-minute ledger tick never waits on it.
Exit codes: 0 the pass ran; 75 another pass held the lock; 1 a file or
directory the pass would not trust.

Episodes: a job that answers incomplete (or fails) for 7 days raises one
`<4>` journal line; the episode ends on the first complete answer.  A client
that answers "verb not supported" is skipped for a day.
"""
from __future__ import annotations

import fcntl
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional

from .config import CONFIG_PATH, load_storage_config
from .client_kit import NO_SUCH_TABLE

EXPIRE_STATE = Path("/var/lib/sigmond/storage/expire-state.json")
EXPIRE_STATUS = Path("/run/sigmond/storage-expire.json")
EXPIRE_LOCK = Path("/run/sigmond/storage-expire.lock")
EX_TEMPFAIL = 75
WARN_AFTER_S = 7 * 86400
UNSUPPORTED_SKIP_S = 86400


@dataclass(frozen=True)
class ExpireJob:
    client: str
    target: str
    days: int
    source: str          # "declared" or "storage.toml"

    @property
    def key(self) -> str:
        return f"{self.client}:{self.target}"


@dataclass
class ExpireRunners:
    load_views: Callable     # () -> {name: ClientView}
    binary_for: Callable     # (client) -> str | None
    call_expire: Callable    # (binary, target, days, max_seconds) -> ExpireAnswer
    journal: Callable        # (line) -> None
    clock: Callable          # () -> float seconds, monotonic; times the budget


def plan_jobs(views: Mapping, retention: Mapping[str, int]) -> list:
    jobs = {}
    for client in views:
        for iv in views[client].instances:
            for s in iv.data_sinks:
                if not s.expires or not s.path:
                    continue
                key = f"{client}:{s.path}"
                if key in retention:
                    days, source = retention[key], "storage.toml"
                else:
                    days, source = int(s.retention_days), "declared"
                if days > 0:
                    jobs[key] = ExpireJob(client, s.path, days, source)
    return sorted(jobs.values(), key=lambda j: (j.client, j.target))


def plan_lines(views: Mapping, retention: Mapping[str, int]) -> list:
    return [f"{j.client} {j.target}: {j.days} days ({j.source})"
            for j in plan_jobs(views, retention)]


def default_runners() -> ExpireRunners:
    import time
    from . import clientcall
    from .tick import _binary_for, _journal
    from ..sysview import build_system_view
    return ExpireRunners(
        load_views=lambda: build_system_view().client_views,
        binary_for=_binary_for,
        call_expire=clientcall.call_expire,
        journal=_journal,
        clock=time.monotonic,
    )


def _open_lock(lock_path: Path, uid: int) -> int:
    from .tick import _open_lock as open_lock
    return open_lock(lock_path, uid)


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_expire_pass(now: datetime, r: ExpireRunners, *, config_path=CONFIG_PATH,
                    state_path=EXPIRE_STATE, status_path=EXPIRE_STATUS,
                    lock_path=EXPIRE_LOCK, budget_s: int = 1500, per_job_s: int = 600,
                    expected_uid: Optional[int] = None) -> int:
    from .tick import Untrusted
    if now.tzinfo is None:
        raise ValueError("run_expire_pass needs an aware UTC time")
    uid = os.geteuid() if expected_uid is None else expected_uid
    try:
        fd = _open_lock(Path(lock_path), uid)
    except (Untrusted, OSError) as exc:
        r.journal(f"<2>storage expire: refusing the lock: {exc}; no pass ran")
        return 1
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return EX_TEMPFAIL
        return _locked(now, r, config_path, Path(state_path), Path(status_path),
                       budget_s, per_job_s, uid)
    finally:
        os.close(fd)


def _int(v, default=0) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def _locked(now, r, config_path, state_path, status_path, budget_s, per_job_s, uid) -> int:
    from .tick import Untrusted, _read_state, _trusted_dir, _write_at_dir
    sdfd = tdfd = None
    try:
        try:
            sdfd = _trusted_dir(state_path.parent, uid)
            tdfd = _trusted_dir(status_path.parent, uid)
        except (Untrusted, OSError) as exc:
            r.journal(f"<2>storage expire: refusing a directory: {exc}; no pass ran")
            return 1
        state, problem = _read_state(sdfd, state_path, uid)
        if problem:
            r.journal(f"<4>storage expire: state reset: {problem}")
            state = {}
        cfg = load_storage_config(config_path, now)
        jobs = plan_jobs(r.load_views(), cfg.retention)
        t = now.timestamp()

        last = state.get("last") if isinstance(state.get("last"), dict) else {}
        unsupported = state.get("unsupported") if isinstance(state.get("unsupported"), dict) else {}
        since = state.get("incomplete_since") if isinstance(state.get("incomplete_since"), dict) else {}
        warned = set(state.get("warned") or []) if isinstance(state.get("warned"), list) else set()
        keys = {j.key for j in jobs}
        last = {k: v for k, v in last.items() if k in keys and isinstance(v, dict)}
        unsupported = {k: v for k, v in unsupported.items() if k in keys}
        since = {k: v for k, v in since.items() if k in keys}
        warned &= keys

        start = _int(state.get("next_index")) % len(jobs) if jobs else 0
        t0 = r.clock()
        done = 0
        for step in range(len(jobs)):
            job = jobs[(start + step) % len(jobs)]
            left = budget_s - (r.clock() - t0)
            if left <= 0:
                break
            done += 1
            k = job.key
            until = unsupported.get(k)
            if isinstance(until, (int, float)) and t < until:
                continue
            unsupported.pop(k, None)
            prev_reason = (last.get(k) or {}).get("reason")
            entry = {"deleted": 0, "complete": False, "reason": "", "last_run_utc": _iso(now)}
            binary = r.binary_for(job.client)
            if not binary:
                entry["reason"] = "no binary found"
                r.journal(f"<4>storage expire: {job.client} {job.target}: no binary found")
                last[k] = entry
                continue
            ans = r.call_expire(binary, job.target, job.days, int(min(per_job_s, left)))
            entry["deleted"], entry["complete"] = ans.deleted, bool(ans.ok and ans.complete)
            entry["reason"] = ans.reason
            last[k] = entry
            if ans.unsupported:
                unsupported[k] = t + UNSUPPORTED_SKIP_S
                r.journal(f"<6>storage expire: {job.client} {job.target}: "
                          f"{ans.reason}; skipped for a day")
                continue
            if not ans.ok or ans.refused:
                r.journal(f"<4>storage expire: {job.client} {job.target}: "
                          f"{'refused' if ans.ok else 'failed'}: {ans.reason}")
            elif ans.deleted > 0:
                r.journal(f"<6>storage expire: {job.client} {job.target}: deleted "
                          f"{ans.deleted}, {'complete' if ans.complete else 'more remains'}")
            elif ans.reason == NO_SUCH_TABLE and prev_reason != NO_SUCH_TABLE:
                # An absent table reads complete; say so once, not every hour.
                r.journal(f"<6>storage expire: {job.client} {job.target}: the table "
                          "does not exist; nothing to expire")
            if ans.ok and ans.complete and not ans.refused:
                since.pop(k, None)
                warned.discard(k)
            else:
                first = since.setdefault(k, t)
                if t - first > WARN_AFTER_S and k not in warned:
                    warned.add(k)
                    r.journal(f"<4>storage expire: {job.client} {job.target}: incomplete for "
                              f"{int((t - first) // 86400)} days; the table outgrows the pass")
        new_state = {"next_index": (start + done) % len(jobs) if jobs else 0,
                     "last": last, "unsupported": unsupported,
                     "incomplete_since": since, "warned": sorted(warned)}
        status = {"schema": 1, "generated_at": _iso(now),
                  "jobs": [{"client": j.client, "target": j.target, "days": j.days,
                            "source": j.source,
                            "deleted": last.get(j.key, {}).get("deleted", 0),
                            "complete": last.get(j.key, {}).get("complete", False),
                            "reason": last.get(j.key, {}).get("reason", ""),
                            "last_run_utc": last.get(j.key, {}).get("last_run_utc")}
                           for j in jobs]}
        try:
            _write_at_dir(sdfd, state_path, new_state, 0o600, uid)
            _write_at_dir(tdfd, status_path, status, 0o644, uid)
        except OSError as exc:
            r.journal(f"<2>storage expire: cannot write state or status: {exc}")
            return 1
        return 0
    finally:
        for d in (sdfd, tdfd):
            if d is not None:
                os.close(d)

"""`smd storage compact <client> <target> [--apply]`: compact a client's database.

The client does the SQLite work through its `storage compact` verbs
(`--prepare`, `--holders`, `--swap`).  sigmond does the one thing the client
cannot: it stops the processes that hold the database, only when an operator
runs this, and starts them again.

Sequence (apply):
  1. refuse under an active hands-off;
  2. `--prepare`, while every writer still runs;
  3. stop sigmond-storage-expire.timer and take the expire pass's lock (a pass
     already running finishes first), so no pass touches the database mid-swap;
  4. `--holders`; refuse if a holder has no unit or is radiod, ka9q-* or sigmond*;
  5. take the lifecycle lock, stop the holder units in reverse start order;
  6. `--swap`;
  7. start the same units in start order, whatever the swap answered;
  8. release the expire lock and restart the timer, on every exit path.

The recorder watchdog leaves the stop alone: a `systemctl stop` reads as an
operator's stop, and the watchdog also waits on the lifecycle lock.
Every step goes to the journal.  The dry run asks the client to prepare, lists
the holders and the sizes, and stops nothing.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .clientcall import NEVER_STOP

EXPIRE_TIMER = "sigmond-storage-expire.timer"
EXPIRE_WAIT_S = 1800          # a pass runs at most ~1500 s


class CompactError(Exception):
    pass


@dataclass
class CompactRunners:
    hands_off: Callable           # () -> str ; "" when clear, else why
    binary_for: Callable          # (client) -> str | None
    prepare: Callable             # (binary, target) -> CompactAnswer
    holders: Callable             # (binary, target) -> CompactAnswer
    swap: Callable                # (binary, target) -> CompactAnswer
    start_order: Callable         # (client) -> [unit, ...] in start order
    systemctl: Callable           # (verb, unit) -> int exit code
    is_active: Callable           # (unit) -> bool
    expire_lock: Callable         # () -> context manager; raises CompactError
    lifecycle_lock: Callable      # (reason) -> context manager; raises CompactError
    journal: Callable             # (line) -> None
    say: Callable                 # (line) -> None


def never_stopped(unit: str) -> bool:
    base = unit.split("@", 1)[0].split(".", 1)[0]
    return (base in NEVER_STOP or base.startswith("radiod")
            or base.startswith("ka9q-") or base.startswith("sigmond"))


def make_expire_lock(lock_path: Path = None, wait_s: float = EXPIRE_WAIT_S,
                     poll_s: float = 5.0, sleep=time.sleep, clock=time.monotonic):
    """A factory for the expire pass's flock, taken blocking-with-a-bound."""
    from .retention import EXPIRE_LOCK, _open_lock

    @contextlib.contextmanager
    def lock():
        path = Path(lock_path or EXPIRE_LOCK)
        try:
            fd = _open_lock(path, os.geteuid())
        except Exception as exc:
            raise CompactError(f"cannot open the expire lock {path}: {exc}") from None
        try:
            end = clock() + wait_s
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if clock() >= end:
                        raise CompactError(
                            f"an expire pass still runs after {wait_s:.0f}s; no unit stopped"
                        ) from None
                    sleep(poll_s)
            yield
        finally:
            os.close(fd)
    return lock


def default_runners() -> CompactRunners:
    import subprocess
    from datetime import datetime, timezone
    from .. import lifecycle
    from . import clientcall
    from .config import CONFIG_PATH, hands_off_held, load_storage_config
    from .tick import _binary_for, _journal, _units_of

    def hands_off() -> str:
        now = datetime.now(timezone.utc)
        cfg = load_storage_config(CONFIG_PATH, now)
        if not hands_off_held(cfg, now):
            return ""
        return cfg.hands_off_unknown or f"until {cfg.hands_off_until} ({cfg.hands_off_reason})"

    def start_order(client: str) -> list:
        refs = [u for u in _units_of(client) if u.kind == "service"]
        return [u.unit for u in lifecycle.order_units(refs)]

    def systemctl(verb: str, unit: str) -> int:
        try:
            return subprocess.run(["systemctl", verb, unit], capture_output=True,
                                  timeout=120).returncode
        except (OSError, subprocess.SubprocessError):
            return 1

    def is_active(unit: str) -> bool:
        try:
            return subprocess.run(["systemctl", "is-active", "--quiet", unit],
                                  capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    @contextlib.contextmanager
    def lifecycle_lock(reason: str):
        try:
            with lifecycle.lifecycle_lock(reason):
                yield
        except SystemExit as exc:
            raise CompactError(f"lifecycle lock: {exc}") from None

    return CompactRunners(
        hands_off=hands_off, binary_for=_binary_for,
        prepare=clientcall.call_compact_prepare, holders=clientcall.call_compact_holders,
        swap=clientcall.call_compact_swap, start_order=start_order, systemctl=systemctl,
        is_active=is_active, expire_lock=make_expire_lock(), lifecycle_lock=lifecycle_lock,
        journal=_journal, say=print)


def _log(r: CompactRunners, client: str, target: str, msg: str, sev: str = "<6>") -> None:
    r.journal(f"{sev}storage compact {client}:{target}: {msg}")


def _sizes(data) -> str:
    keys = [k for k in sorted(data) if k not in ("refused", "reason")
            and isinstance(data[k], int) and not isinstance(data[k], bool)]
    return ", ".join(f"{k}={data[k]}" for k in keys) or "no sizes given"


def _order(units, start_order) -> list:
    rank = {u: i for i, u in enumerate(start_order)}
    return sorted(set(units), key=lambda u: (rank.get(u, len(rank)), u))


def compact_client(client: str, target: str, *, apply: bool, r: CompactRunners) -> int:
    def refuse(why: str) -> int:
        _log(r, client, target, f"refused: {why}", "<4>")
        r.say(f"refused: {why}")
        return 1

    held = r.hands_off()
    if held:
        return refuse(f"the storage ledger is hands-off ({held})")
    binary = r.binary_for(client)
    if not binary:
        return refuse(f"no binary found for {client}")
    prep = r.prepare(binary, target)
    _log(r, client, target, f"prepare: ok={prep.ok} refused={prep.refused} {prep.reason}")
    if not prep.ok or prep.refused:
        return refuse(f"prepare: {prep.reason or 'failed'}")
    r.say(f"prepare: {_sizes(prep.data)}")
    if not apply:
        return _dry_run(client, target, binary, r)
    return _apply(client, target, binary, r)


def _dry_run(client, target, binary, r) -> int:
    ans = r.holders(binary, target)
    if not ans.ok or ans.refused:
        r.say(f"holders: {ans.reason or 'failed'}")
        return 1
    units = _order([u for _, u in ans.holders if u], r.start_order(client))
    r.say("holder units, to stop in reverse order: "
          + (", ".join(reversed(units)) or "none"))
    for pid, unit in ans.holders:
        if unit is None:
            r.say(f"  pid {pid} has no unit; an apply would refuse")
        elif never_stopped(unit):
            r.say(f"  {unit} is never stopped; an apply would refuse")
    r.say("dry run: nothing stopped; add --apply to compact")
    return 0


def _apply(client, target, binary, r) -> int:
    timer_was_active = r.is_active(EXPIRE_TIMER)
    if timer_was_active:
        r.systemctl("stop", EXPIRE_TIMER)
        _log(r, client, target, f"stopped {EXPIRE_TIMER}")
    try:
        with r.expire_lock():
            return _holders_and_swap(client, target, binary, r)
    except CompactError as exc:
        _log(r, client, target, f"refused: {exc}", "<4>")
        r.say(f"refused: {exc}")
        return 1
    finally:
        if timer_was_active:
            rc = r.systemctl("start", EXPIRE_TIMER)
            _log(r, client, target, f"started {EXPIRE_TIMER} (exit {rc})",
                 "<6>" if rc == 0 else "<3>")


def _holders_and_swap(client, target, binary, r) -> int:
    ans = r.holders(binary, target)
    _log(r, client, target, f"holders: ok={ans.ok} refused={ans.refused} {ans.reason}")
    if not ans.ok or ans.refused:
        raise CompactError(f"holders: {ans.reason or 'failed'}")
    for pid, unit in ans.holders:
        if unit is None:
            raise CompactError(f"pid {pid} holds the database and has no unit; "
                               "nothing stopped")
        if never_stopped(unit):
            raise CompactError(f"{unit} holds the database and is never stopped; "
                               "nothing stopped")
    order = _order([u for _, u in ans.holders], r.start_order(client))
    with r.lifecycle_lock(f"storage compact {client} {target}"):
        stopped = []
        try:
            for unit in reversed(order):
                stopped.append(unit)        # a failed stop may still have half-stopped it
                rc = r.systemctl("stop", unit)
                _log(r, client, target, f"stop {unit}: exit {rc}",
                     "<6>" if rc == 0 else "<3>")
                if rc != 0:
                    raise CompactError(f"systemctl stop {unit} failed (exit {rc})")
            sw = r.swap(binary, target)
            _log(r, client, target, f"swap: ok={sw.ok} refused={sw.refused} {sw.reason}",
                 "<6>" if sw.ok and not sw.refused else "<3>")
            swap_ok = sw.ok and not sw.refused
            r.say(f"swap: {'done' if swap_ok else 'failed'}: {sw.reason or _sizes(sw.data)}")
        finally:
            failed = []
            for unit in sorted(stopped, key=order.index):
                rc = r.systemctl("start", unit)
                _log(r, client, target, f"start {unit}: exit {rc}",
                     "<6>" if rc == 0 else "<3>")
                if rc != 0:
                    failed.append(unit)
            if failed:
                r.say("could not start: " + ", ".join(failed))
    return 0 if swap_ok and not failed else 1

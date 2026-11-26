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
import json
import os
import signal
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
    estimate: Callable            # (binary, target) -> CompactAnswer; read-only
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
    marker_path: Path = None      # in-progress marker; None writes none


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
    from ..recorder_watchdog import COMPACT_MARKER
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
        cm = lifecycle.lifecycle_lock(reason)
        try:
            cm.__enter__()
        except SystemExit as exc:           # refused: only the entering
            raise CompactError(f"lifecycle lock: {exc}") from None
        try:
            yield
        except BaseException:
            if not cm.__exit__(*__import__("sys").exc_info()):
                raise
        else:
            cm.__exit__(None, None, None)

    return CompactRunners(
        hands_off=hands_off, binary_for=_binary_for,
        estimate=clientcall.call_compact_estimate,
        prepare=clientcall.call_compact_prepare, holders=clientcall.call_compact_holders,
        swap=clientcall.call_compact_swap, start_order=start_order, systemctl=systemctl,
        is_active=is_active, expire_lock=make_expire_lock(), lifecycle_lock=lifecycle_lock,
        journal=_journal, say=print, marker_path=COMPACT_MARKER)


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
    if not apply:
        return _dry_run(client, target, binary, r)
    prep = r.prepare(binary, target)
    _log(r, client, target, f"prepare: ok={prep.ok} refused={prep.refused} {prep.reason}")
    if not prep.ok or prep.refused:
        return refuse(f"prepare: {prep.reason or 'failed'}")
    r.say(f"prepare: {_sizes(prep.data)}")
    return _apply(client, target, binary, r)


def _dry_run(client, target, binary, r) -> int:
    """Builds no copy: asks the client's read-only `--estimate`, then holders."""
    est = r.estimate(binary, target)
    if est.unsupported:
        r.say("estimate: the client does not support --estimate (older client)")
    elif not est.ok:
        r.say(f"estimate: {est.reason or 'failed'}")
    else:
        r.say(f"estimate: {_sizes(est.data)}")
        if est.refused or est.data.get("would_refuse") is True:
            r.say(f"  prepare would refuse: {est.reason or est.data.get('reason', '')}")
    ans = r.holders(binary, target)
    if not ans.ok or ans.refused:
        r.say(f"holders: {ans.reason or 'failed'}")
        return 1
    units = _order([u for _, u in ans.holders
                    if u and not u.startswith("sigmond-storage-expire")], r.start_order(client))
    r.say("holder units, to stop in reverse order: "
          + (", ".join(reversed(units)) or "none"))
    for pid, unit in ans.holders:
        if unit is None:
            r.say(f"  pid {pid} has no unit; an apply would refuse")
        elif unit.startswith("sigmond-storage-expire"):
            r.say(f"  an expire pass is running ({unit}); an apply waits for it")
        elif never_stopped(unit):
            r.say(f"  {unit} is never stopped; an apply would refuse")
    _log(r, client, target, "dry run: built no copy, stopped nothing")
    r.say("dry run: nothing stopped, no copy built; add --apply to compact")
    return 0


@contextlib.contextmanager
def _signals_raise():
    """SIGTERM and SIGHUP raise SystemExit, once, so the cleanup `finally`
    blocks run; a second signal during cleanup is ignored."""
    saved = {}

    def handler(signum, frame):
        for other in (signal.SIGTERM, signal.SIGHUP):    # one exit; cleanup runs whole
            signal.signal(other, signal.SIG_IGN)
        raise SystemExit(128 + signum)
    try:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            saved[sig] = signal.signal(sig, handler)
    except ValueError:           # not the main thread
        saved = {}
    try:
        yield
    finally:
        for sig, old in saved.items():
            signal.signal(sig, old)


def _marker_text(timer_was_active, units, started) -> str:
    from ..recorder_watchdog import proc_start
    return json.dumps({"pid": os.getpid(), "pid_start": proc_start(os.getpid()),
                       "started": started, "units": list(units), "timer": EXPIRE_TIMER,
                       "timer_was_active": bool(timer_was_active)})


def _claim_marker(r, timer_was_active, started) -> None:
    """Create the marker exclusively; refuse while another one stands."""
    if r.marker_path is None:
        return
    from ..recorder_watchdog import _read_marker, marker_pid_alive
    path = Path(r.marker_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        _, d = _read_marker(path)
        if marker_pid_alive(d):
            raise CompactError("another compaction is running") from None
        raise CompactError(
            "an earlier compaction died and left its units stopped; the recorder "
            "watchdog restores them on its next tick; run this again after that") from None
    with os.fdopen(fd, "w") as fh:
        fh.write(_marker_text(timer_was_active, [], started))


def _write_marker(r, timer_was_active, units, started) -> None:
    if r.marker_path is None:
        return
    path = Path(r.marker_path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_marker_text(timer_was_active, units, started))
    os.replace(tmp, path)


def _clear_marker(r) -> None:
    if r.marker_path is not None:
        try:
            Path(r.marker_path).unlink()
        except OSError:
            pass


def _apply(client, target, binary, r) -> int:
    with _signals_raise():
        return _apply_guarded(client, target, binary, r)


def _apply_guarded(client, target, binary, r) -> int:
    timer_was_active = r.is_active(EXPIRE_TIMER)
    started = time.time()
    # The marker comes before the first stop; the recorder watchdog restores
    # the listed units if this process dies without cleaning up.  A standing
    # marker (another run, or a dead one not yet restored) refuses this run.
    try:
        _claim_marker(r, timer_was_active, started)
    except CompactError as exc:
        _log(r, client, target, f"refused: {exc}", "<4>")
        r.say(f"refused: {exc}")
        return 1
    state = {"incomplete": False}
    if timer_was_active:
        r.systemctl("stop", EXPIRE_TIMER)
        _log(r, client, target, f"stopped {EXPIRE_TIMER}")
    mark = lambda units: _write_marker(r, timer_was_active, units, started)
    try:
        with r.expire_lock():
            return _holders_and_swap(client, target, binary, r, mark, state)
    except CompactError as exc:
        _log(r, client, target, f"refused: {exc}", "<4>")
        r.say(f"refused: {exc}")
        return 1
    finally:
        if timer_was_active:
            rc = r.systemctl("start", EXPIRE_TIMER)
            _log(r, client, target, f"started {EXPIRE_TIMER} (exit {rc})",
                 "<6>" if rc == 0 else "<3>")
        # Only a cleanup that attempted every start removes the marker; if it
        # was cut short, the watchdog restores the listed units.
        if not state["incomplete"]:
            _clear_marker(r)


def _holders_and_swap(client, target, binary, r, mark=lambda units: None,
                      state=None) -> int:
    state = state if state is not None else {}
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
    mark(order)
    with r.lifecycle_lock(f"storage compact {client} {target}"):
        stopped = []
        state["incomplete"] = True      # until every stopped unit's start was tried
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
            state["incomplete"] = False
            if failed:
                r.say("could not start: " + ", ".join(failed))
    return 0 if swap_ok and not failed else 1

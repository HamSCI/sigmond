"""sigmond-recorder-watchdog: the one agent besides systemd that restarts
timestd-core-recorder.

Before v3.96 four agents restarted the recorder, each with its own clock and
its own budget: sigmond-t6-stuck-watchdog, sigmond-sdr-recover, the
radiod-consumer hook, and hf-timestd's pipeline and HPPS watchdogs.  Nobody
could say how often the recorder restarted or why, and one agent could
restart a recorder another had restarted a minute before, inside its first
chunk.  Now the others report; this one decides and acts.

How it works
------------
A reporter writes one small request file, ``<REQUEST_DIR>/<reporter>.json``
(``file_request``), and may ``kick`` the watchdog to run now rather than at
its next tick.  Reporters run as root and ``mkdir -p`` REQUEST_DIR before
writing (``file_request`` does so); the watchdog's unit also creates it
(``RuntimeDirectory=``), and ``/run`` empties at every boot.  A request
file reads::

    {"schema": 1, "reporter": "pipeline-watchdog", "cause": "no-output",
     "requested_utc": 1795000000.0, "evidence": "...", "stopped_recorder": false}

``requested_utc`` is epoch seconds (a float).  A newer request from the same
reporter replaces the older one.

Each tick (``tick``) takes every request (``take_requests``), gathers the
recorder's and radiod's systemd state and the T6 telemetry, and asks the
pure ``decide`` what to do.  ``decide`` applies one fresh-start grace
(GRACE_S), one cooldown shared by every cause (COOLDOWN_S) and one daily
cap (MAX_PER_DAY).  Two restarts stand outside the budget: one that follows
a radiod restart (the recorder holds a stale RTP anchor until it follows
radiod, every time) and systemd's own.  The watchdog writes every action,
every refused or dropped request and every restart systemd made to one
ledger (LEDGER), so every restart of the recorder shows in one file.

Causes: ``t6-stuck`` and ``failed`` come from the watchdog's own rules;
``radiod-restarted``, ``no-output`` and ``hpps-stale`` come from requests.
An ``hpps-stale`` request is recorded but not acted on (ACT_ON_HPPS): the
HPPS watchdog once restarted the recorder four times in a night against a
correct response to a weak signal (below).

The T6-stuck rule
-----------------
AC0G-B4, 2026-08-31: T6 sat UNLOCKED from 03:38 to 10:01Z -- six hours
and twenty-three minutes -- and recovered the instant the recorder was
restarted.  Nothing it depends on was unwell.  C/N0 ran 57-58 dB-Hz,
HIGHER than while it had been working an hour earlier.  costas_locked
stayed True; the carrier never let go.  T5/LBE-1421 reported a valid fix
120 times out of 120 with an NMEA age of 0.3 s.  And an independent
block-wise estimator, reading the SAME multicast, located the edge
sample-exactly in 98% of seconds at a median detection SNR of 340.

The information was there the whole time.  The software could not use it
and had no way back on its own.  Daily T6 availability fell from 97.2%
to 41.5%.

WHY THIS IS NARROW, AND MUST STAY NARROW
----------------------------------------
sigmond's own watchdogs record the trap: "a watchdog that fires on a
downstream symptom restarts things forever without fixing anything."
timestd-hpps-watchdog restarted the recorder four times overnight on
2026-08-29 against a stale chrony refclock when the actual cause was low
C/N0 -- it was treating a correct response to a weak signal as a fault.

So the T6 rule fires ONLY when every input says the signal is good and T6
is nonetheless not using it.  A weak signal, an unlocked carrier or a dead
GPS are all reasons the software is RIGHT to be unlocked, and each one
refuses.  Missing telemetry refuses too: an absent reading is not
evidence of health.

This treats a symptom.  The underlying defect -- why the fine stage
stops producing estimates and cannot resume -- is NOT diagnosed, and
this must not be mistaken for a fix.  It bounds the damage from six
hours to one cooldown.

Stdlib only; every process runs through an injected ``run`` as an argv
list.  It imports no tomllib at module level: the helper may run under a
system python3 older than 3.11.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from sigmond import radiod_ready


def _env(name: str, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    try:
        return type(default)(raw)
    except ValueError:
        return default


UNIT = _env("RWD_UNIT", "timestd-core-recorder.service")
SELF_SERVICE = "sigmond-recorder-watchdog.service"
AGENT = "sigmond-recorder-watchdog"
#: The client the storage ledger stops by name to stop the recorder.
CLIENT = "hf-timestd"

REQUEST_DIR = Path(_env("RWD_REQUEST_DIR", "/run/sigmond/recorder-restart"))
LEDGER = Path(_env("RWD_LEDGER", "/var/lib/sigmond/recorder-restarts.jsonl"))
LEDGER_KEEP = _env("RWD_LEDGER_KEEP", 2000)
STATE = Path(_env("RWD_STATE", "/run/sigmond/recorder-watchdog.json"))
STORAGE_STATE = Path(_env("RWD_STORAGE_STATE", "/var/lib/sigmond/storage/state.json"))
TOPOLOGY = Path(_env("RWD_TOPOLOGY", "/etc/sigmond/topology.toml"))

#: The recorder writes one chunk per CHUNK_S.  A start just after a chunk
#: boundary waits up to CHUNK_S for the next one, writes one full chunk,
#: then flushes: so nothing judges it for two chunks and two minutes.
CHUNK_S = _env("RWD_CHUNK_S", 600)
GRACE_S = 2 * CHUNK_S + 120
COOLDOWN_S = _env("RWD_COOLDOWN_S", 1800)
MAX_PER_DAY = _env("RWD_MAX_PER_DAY", 6)
REQUEST_MAX_AGE_S = _env("RWD_REQUEST_MAX_AGE_S", 900)
#: systemd's own Restart= gets this long before the watchdog revives a
#: recorder systemd gave up on.
FAILED_REVIVE_S = _env("RWD_FAILED_REVIVE_S", 1800)
#: How long radiod runs before a consumer that takes its anchor counts as
#: following it: the shared value sigmond-radiod-ready and the
#: radiod-consumer hook use (monotonic clock).
READY_SETTLE_S = _env("RWD_READY_SETTLE_S", float(radiod_ready.READY_SETTLE_S))
#: HPPS staleness is a downstream symptom (2026-08-29): record, do not act.
ACT_ON_HPPS = _env("RWD_ACT_ON_HPPS", False)
LOCK_WAIT_S = 60
#: A blocking `systemctl start|restart` waits for the recorder's own
#: TimeoutStartSec (300 s in timestd-core-recorder.service), plus a margin,
#: so a slow start that succeeds is not recorded as a failure.
ACT_TIMEOUT_S = _env("RWD_ACT_TIMEOUT_S", 300 + 60)
#: The recorder's RestartPreventExitStatus: it refused its config.  Neither
#: systemd nor this watchdog starts it again; an operator fixes the config.
NO_REVIVE_EXIT = (78,)

#: Sustained unlock before acting.  T6 re-acquires on its own within a
#: few minutes when it can; the 08-31 event ran 383 minutes.
MIN_STUCK_SEC = _env("RWD_MIN_STUCK_SEC", 1200)
#: Below this the software is right to be unlocked -- the measured
#: acquisition cliff sits at 58-59 dB-Hz, so this is deliberately under
#: it: we act only where acquisition demonstrably SHOULD succeed.
MIN_CN0_DB_HZ = _env("RWD_MIN_CN0", 56.0)

STATUS = Path(_env("RWD_STATUS", "/var/lib/timestd/status/core-recorder-status.json"))
#: C/N0 is NOT in core-recorder-status.json -- it is written to the
#: authority snapshot store by the frontend probe.  Reading it from the
#: status file returns None forever, and a watchdog whose gate is always
#: None never fires and never says why.  So it comes from the DB, and an
#: unreadable DB is reported rather than silently treated as unhealthy.
AUTH_DB = _env("RWD_AUTH_DB", "/var/lib/timestd/authority_history.db")

#: `smd storage compact --apply` writes this before it stops anything and
#: removes it after it has started everything again.  A marker whose pid is dead
#: means the run was killed mid-way; tick() starts the listed units again.
COMPACT_MARKER = Path(_env("RWD_COMPACT_MARKER", "/run/sigmond/compact-in-progress.json"))

T6_STUCK, FAILED = "t6-stuck", "failed"
RADIOD, NO_OUTPUT, HPPS = "radiod-restarted", "no-output", "hpps-stale"
REQUEST_CAUSES = (RADIOD, NO_OUTPUT, HPPS)
#: Causes outside the cooldown and the cap.
EXEMPT = (RADIOD,)

_REPORTER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}")


# --- types -------------------------------------------------------------------

@dataclass(frozen=True)
class Request:
    reporter: str
    cause: str
    requested_utc: float          # epoch seconds
    evidence: str
    stopped_recorder: bool = False


@dataclass(frozen=True)
class T6State:
    authoritative: Optional[bool]
    costas_locked: Optional[bool]
    cn0_db_hz: Optional[float]
    t5_valid_fix: Optional[bool]
    stuck_for_sec: float


@dataclass(frozen=True)
class UnitState:
    active_state: str
    result: str
    active_enter_mono: Optional[float]
    inactive_enter_mono: Optional[float]
    n_restarts: int
    #: ExecMainStatus: the exit code of the last main process, when known.
    exec_main_status: Optional[int] = None


@dataclass(frozen=True)
class Facts:
    now_wall: float
    now_mono: float
    recorder: UnitState
    radiod_started_mono: Optional[float]     # None unless radiod is active now
    t6: Optional[T6State]
    requests: tuple
    ledger_stopped: bool
    state: Mapping


@dataclass(frozen=True)
class Decision:
    action: str                   # "restart" | "start" | "none"
    cause: Optional[str]
    reason: str
    refused: tuple                # (Request, reason) pairs
    #: (Request, reason) pairs of radiod-restarted requests kept for the
    #: next tick: radiod is not running yet, or has not run READY_SETTLE_S.
    deferred: tuple = ()
    #: The request acted on, when a request caused the action.
    request: Optional[Request] = None


# --- the T6 rule (moved unchanged from sigmond-t6-stuck-watchdog) -----------

def should_restart_t6(s: Optional[T6State]) -> bool:
    """True only for: unlocked, sustained, against a demonstrably good signal."""
    if s is None:
        return False                      # no telemetry at all
    if s.authoritative is None or s.authoritative:
        return False                      # healthy, or we cannot tell
    if s.costas_locked is not True:
        return False                      # carrier gone: not a stuck state
    if s.t5_valid_fix is not True:
        return False                      # no GPS: naming is right to fail
    if s.cn0_db_hz is None or s.cn0_db_hz < MIN_CN0_DB_HZ:
        return False                      # weak signal: unlocking is correct
    return s.stuck_for_sec >= MIN_STUCK_SEC


def cn0_from_db(path: Optional[str] = None) -> Optional[float]:
    """Latest (baseband_power - n0) in dB-Hz, or None if unavailable."""
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{path or AUTH_DB}?mode=ro", uri=True,
                              timeout=5.0)
        try:
            row = con.execute(
                "SELECT t6_baseband_power, t6_n0 FROM authority_snapshot "
                "WHERE t6_baseband_power IS NOT NULL AND t6_n0 IS NOT NULL "
                "ORDER BY utc_published DESC LIMIT 1").fetchone()
        finally:
            con.close()
    except Exception as exc:
        print(f"  C/N0 unavailable ({exc}) — refusing to act")
        return None
    if not row:
        print("  C/N0 unavailable (no rows carry it) — refusing to act")
        return None
    try:
        return float(row[0]) - float(row[1])
    except (TypeError, ValueError):
        return None


def read_status(now: float, st: dict, *, status_path: Optional[Path] = None,
                cn0: Optional[Callable[[], Optional[float]]] = None) -> Optional[T6State]:
    """The T6 state from the recorder's status file, or None when unreadable.
    Tracks how long T6 has been unlocked in ``st["unlocked_since"]``."""
    try:
        with open(status_path or STATUS) as fh:
            d = json.load(fh)
    except Exception as exc:
        print(f"  status unreadable ({exc}) — refusing to act on T6")
        return None
    # `or {}`, not a .get default: a fresh station with no TS-1 writes these
    # sections as JSON NULL rather than omitting them, and a default applies
    # only when the KEY IS ABSENT.  A present null used to reach .get() as
    # None and crash the watchdog on every timer tick (AC0G-ND 2026-09-03) —
    # a watchdog reporting its own breakage as the station's.
    t6 = d.get("t6_pps") or {}
    auth = d.get("t6_authority") or {}
    state = auth.get("state")
    authoritative = None if state is None else (state == "AUTHORITATIVE")
    since = st.get("unlocked_since")
    if authoritative is False:
        since = since or now
    else:
        since = None
    st["unlocked_since"] = since
    return T6State(
        authoritative=authoritative,
        costas_locked=t6.get("costas_locked"),
        cn0_db_hz=(cn0 or cn0_from_db)(),
        t5_valid_fix=(d.get("t5_lbe1421") or {}).get("valid_fix"),
        stuck_for_sec=(now - since) if since else 0.0,
    )


# --- requests ----------------------------------------------------------------

def file_request(reporter: str, cause: str, evidence: str, *,
                 stopped_recorder: bool = False, request_dir: Path = REQUEST_DIR,
                 now: Optional[float] = None) -> Path:
    """Write ``<request_dir>/<reporter>.json`` atomically and return its path.
    Creates ``request_dir`` when it is missing (``/run`` empties at boot)."""
    if not _REPORTER.fullmatch(reporter):
        raise ValueError(f"reporter {reporter!r} is not a plain name")
    request_dir = Path(request_dir)
    request_dir.mkdir(parents=True, exist_ok=True)
    path = request_dir / f"{reporter}.json"
    tmp = Path(str(path) + ".tmp")
    body = {"schema": 1, "reporter": reporter, "cause": cause,
            "requested_utc": float(time.time() if now is None else now),
            "evidence": evidence, "stopped_recorder": bool(stopped_recorder)}
    with open(tmp, "w") as fh:
        json.dump(body, fh)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def kick(run: Callable = subprocess.run) -> None:
    """Ask the watchdog to run now; never raises, never waits."""
    try:
        run(["systemctl", "start", "--no-block", SELF_SERVICE],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def _parse(raw: str) -> Request:
    d = json.loads(raw)
    if not isinstance(d, dict) or d.get("schema") != 1:
        raise ValueError("not a schema-1 request")
    reporter, cause = d.get("reporter"), d.get("cause")
    if not isinstance(reporter, str) or not isinstance(cause, str):
        raise ValueError("reporter and cause must be strings")
    t = d.get("requested_utc")
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        raise ValueError("requested_utc must be epoch seconds")
    return Request(reporter, cause, float(t), str(d.get("evidence") or ""),
                   bool(d.get("stopped_recorder")))


def take_requests(request_dir: Path, now: float, *, dropped: Optional[list] = None,
                  consume: bool = True) -> list:
    """Every request in ``request_dir``, oldest first, each taken once: a
    file is renamed to ``.taken``, read, then unlinked (a ``.taken`` a crash
    left is read too).  A request older than REQUEST_MAX_AGE_S, or one that
    does not parse, is dropped; ``dropped`` collects ``(name, reason)``.
    ``consume=False`` reads without taking (the dry run)."""
    out = []
    request_dir = Path(request_dir)
    try:
        entries = sorted(request_dir.iterdir())
    except OSError:
        return out
    for p in entries:
        name = p.name
        if name.endswith(".json"):
            stem = name[:-5]
        elif name.endswith(".json.taken"):
            stem = name[:-11]
        else:
            continue
        src = p
        if consume and name.endswith(".json"):
            src = Path(str(p) + ".taken")
            try:
                os.replace(p, src)
            except FileNotFoundError:
                continue
        try:
            raw = src.read_text()
        except OSError as exc:
            if dropped is not None:
                dropped.append((stem, f"unreadable: {exc}"))
            continue
        finally:
            if consume:
                with contextlib.suppress(OSError):
                    src.unlink()
        try:
            req = _parse(raw)
        except (ValueError, TypeError) as exc:
            if dropped is not None:
                dropped.append((stem, f"malformed: {exc}"))
            continue
        age = now - req.requested_utc
        if age > REQUEST_MAX_AGE_S:
            if dropped is not None:
                dropped.append((req.reporter, f"filed {age:.0f} s ago, older than "
                                              f"{REQUEST_MAX_AGE_S} s"))
            continue
        out.append(req)
    out.sort(key=lambda r: (r.requested_utc, r.reporter))
    return out


def _refile(req: Request, request_dir: Path) -> None:
    """Put a taken request back, unless its reporter filed a newer one."""
    if (Path(request_dir) / f"{req.reporter}.json").exists():
        return
    with contextlib.suppress(OSError, ValueError):
        file_request(req.reporter, req.cause, req.evidence,
                     stopped_recorder=req.stopped_recorder, request_dir=request_dir,
                     now=req.requested_utc)


# --- decide ------------------------------------------------------------------

def _day(now_wall: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now_wall))


def actions_today(state: Mapping, now_wall: float) -> int:
    if state.get("day") != _day(now_wall):
        return 0
    try:
        return int(state.get("actions_today") or 0)
    except (TypeError, ValueError):
        return 0


def _covered(reqs, cause: str) -> list:
    return [(r, f"covered by this {cause} restart") for r in reqs]


def decide(f: Facts) -> Decision:
    """What to do this tick; pure.  The rules, in order, are those of the
    v3.96 plan (Task 4): the ledger's stop, an operator's stop, following
    radiod, reviving a failed recorder, the fresh-start grace, the shared
    cooldown and cap, then T6, no-output and hpps-stale."""
    reqs = list(f.requests)
    rec = f.recorder

    # 1. The storage ledger stopped the recorder: nothing overrides it.
    if f.ledger_stopped:
        return Decision("none", None, "the storage ledger stopped the recorder",
                        tuple((r, "the storage ledger stopped it") for r in reqs))

    radiod_reqs = [r for r in reqs if r.cause == RADIOD]
    others = [r for r in reqs if r.cause != RADIOD]
    sdr_stopped = [r for r in radiod_reqs if r.stopped_recorder]

    # 2. An operator's stop holds, unless sigmond's SDR recovery stopped it
    # for a radiod restart and asks it back.
    if rec.active_state == "inactive" and rec.result == "success" and not sdr_stopped:
        return Decision("none", None, "the recorder was stopped (an operator's stop holds)",
                        tuple((r, "the recorder was stopped on purpose; an operator's "
                                  "stop holds") for r in reqs))

    # 3. Follow radiod: outside the grace, the cooldown and the cap.
    refused, deferred = [], []
    if radiod_reqs:
        rmono = f.radiod_started_mono
        if rmono is None:
            deferred = [(r, "radiod is not running") for r in radiod_reqs]
        elif f.state.get("radiod_start_acted") == rmono:
            refused += [(r, "already followed this radiod start (once per radiod start)")
                        for r in radiod_reqs]
        elif f.now_mono < rmono + READY_SETTLE_S:
            # A recorder started now would take its anchor inside radiod's
            # settle window, and the once-per-start guard would then refuse
            # the correction.  Wait; the mark is set only when we act.
            deferred = [(r, f"radiod started {f.now_mono - rmono:.0f} s ago; it settles "
                            f"for {READY_SETTLE_S:.0f} s") for r in radiod_reqs]
        elif (rec.active_state == "active" and rec.active_enter_mono is not None
              and rec.active_enter_mono < rmono + READY_SETTLE_S):
            return Decision("restart", RADIOD,
                            f"the recorder started at {rec.active_enter_mono:.0f} s, before "
                            f"radiod's start at {rmono:.0f} s settled: its anchor is stale",
                            tuple(_covered(radiod_reqs[1:] + others, RADIOD)),
                            request=radiod_reqs[0])
        elif rec.active_state in ("inactive", "failed") and sdr_stopped:
            rest = [r for r in reqs if r is not sdr_stopped[0]]
            return Decision("start", RADIOD,
                            "SDR recovery stopped the recorder for radiod's restart; "
                            "radiod runs again", tuple(_covered(rest, RADIOD)),
                            request=sdr_stopped[0])
        elif rec.active_state == "active":
            refused += [(r, "the recorder already follows radiod's latest start")
                        for r in radiod_reqs]
        else:
            refused += [(r, f"the recorder is {rec.active_state}; nothing to follow with")
                        for r in radiod_reqs]
    deferred = tuple(deferred)
    used = actions_today(f.state, f.now_wall)

    def none(reason: str, why: str) -> Decision:
        return Decision("none", None, reason,
                        tuple(refused) + tuple((r, why) for r in others), deferred)

    # 4. systemd gave up: revive after its own restarts had their chance.
    if rec.active_state == "failed":
        if rec.result == "exit-code" and rec.exec_main_status in NO_REVIVE_EXIT:
            return none(f"the recorder exited {rec.exec_main_status} (it refused its "
                        "config; RestartPreventExitStatus): not revived",
                        f"the recorder exited {rec.exec_main_status}: its config needs fixing")
        if rec.result == "timeout":
            # A stop that timed out (SIGKILL after TimeoutStopSec) leaves the
            # unit failed, not inactive: it may be an operator's stop, which
            # must hold.  A start that timed out is systemd's to retry.
            return none("the recorder failed on a timeout (an operator's stop may have "
                        "ended this way): not revived",
                        "the recorder failed on a timeout; not revived")
        age = (None if rec.inactive_enter_mono is None
               else f.now_mono - rec.inactive_enter_mono)
        if age is None or age < FAILED_REVIVE_S:
            when = "at an unknown time" if age is None else f"{age:.0f} s ago"
            return none(f"the recorder failed {when}; revival waits {FAILED_REVIVE_S} s",
                        "the recorder has failed; only a revival can start it")
        if used >= MAX_PER_DAY:
            return none(f"the recorder failed {age:.0f} s ago, but the daily cap "
                        f"({MAX_PER_DAY}) is reached", "the daily cap is reached")
        return Decision("start", FAILED,
                        f"systemd gave up: the recorder failed {age:.0f} s ago",
                        tuple(refused) + tuple(_covered(others, FAILED)), deferred)
    if rec.active_state != "active":
        return none(f"the recorder is {rec.active_state}; nothing to judge",
                    f"the recorder is {rec.active_state}")

    # 5. The fresh-start grace.
    if rec.active_enter_mono is None or f.now_mono - rec.active_enter_mono < GRACE_S:
        return none("fresh start: first full chunk not yet written",
                    "fresh start: first full chunk not yet written")

    # 6. One cooldown and one cap, shared by every cause.
    last = f.state.get("last_action")
    if isinstance(last, (int, float)) and f.now_wall - last < COOLDOWN_S:
        why = (f"in cooldown: the last restart ({f.state.get('last_cause') or 'unknown'}) "
               f"ran {f.now_wall - last:.0f} s ago, cooldown {COOLDOWN_S} s")
        return none(why, why)
    if used >= MAX_PER_DAY:
        why = f"the daily cap ({MAX_PER_DAY}) is reached"
        return none(why, why)

    # 7. T6 stuck against a good signal.
    if should_restart_t6(f.t6):
        s = f.t6
        return Decision("restart", T6_STUCK,
                        f"T6 stuck {s.stuck_for_sec:.0f} s against a good signal "
                        f"(C/N0 {s.cn0_db_hz:.1f} dB-Hz, carrier locked)",
                        tuple(refused) + tuple(_covered(others, T6_STUCK)), deferred)

    # 8, 9. Requests.
    acted = None
    rest = []
    for r in others:
        if acted is None and r.cause == NO_OUTPUT:
            acted = r
        elif acted is None and r.cause == HPPS and ACT_ON_HPPS:
            acted = r
        else:
            rest.append(r)
    if acted is not None:
        tail = []
        for r in rest:
            if r.cause in REQUEST_CAUSES:
                tail.append((r, f"covered by this {acted.cause} restart"))
            else:
                tail.append((r, f"unknown cause {r.cause!r}"))
        return Decision("restart", acted.cause, f"{acted.reporter}: {acted.evidence}",
                        tuple(refused) + tuple(tail), deferred, request=acted)
    for r in rest:
        if r.cause == HPPS:
            refused.append((r, "report-only cause: HPPS staleness is recorded, not acted on"))
        else:
            refused.append((r, f"unknown cause {r.cause!r}"))
    return Decision("none", None, "nothing to do", tuple(refused), deferred)


# --- gathering facts ---------------------------------------------------------

def _systemctl(run: Callable, *args: str, timeout: float = 120):
    argv = ["systemctl", *args]
    try:
        return run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 1, "", str(exc))


def _int(raw) -> Optional[int]:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _mono(raw: str) -> Optional[float]:
    try:
        us = int(raw)
    except (TypeError, ValueError):
        return None
    return us / 1e6 if us > 0 else None


def unit_state(unit: str, run: Callable = subprocess.run) -> Optional[UnitState]:
    """The recorder's systemd state, or None when systemctl cannot say."""
    cp = _systemctl(run, "show", "-p", "ActiveState", "-p", "Result",
                    "-p", "ActiveEnterTimestampMonotonic",
                    "-p", "InactiveEnterTimestampMonotonic", "-p", "NRestarts", "-p", "ExecMainStatus", unit)
    if cp.returncode != 0:
        return None
    p = dict(l.split("=", 1) for l in (cp.stdout or "").splitlines() if "=" in l)
    if not p.get("ActiveState"):
        return None
    try:
        n = int(p.get("NRestarts") or 0)
    except ValueError:
        n = 0
    return UnitState(p["ActiveState"].strip(), (p.get("Result") or "").strip(),
                     _mono(p.get("ActiveEnterTimestampMonotonic")),
                     _mono(p.get("InactiveEnterTimestampMonotonic")), n,
                     _int(p.get("ExecMainStatus")))


def started_mono(unit: str, run: Callable = subprocess.run) -> Optional[float]:
    """When ``unit`` last entered active (monotonic s), None unless active now."""
    st = unit_state(unit, run)
    if st is None or st.active_state != "active":
        return None
    return st.active_enter_mono


def radiod_unit_from_topology(run: Callable = subprocess.run,
                              path: Path = TOPOLOGY) -> Optional[str]:
    """``[radiod] unit`` from topology.toml, else the one active radiod@."""
    try:
        try:
            import tomllib
        except ModuleNotFoundError:                     # python < 3.11
            import tomli as tomllib                     # type: ignore
        with open(path, "rb") as fh:
            unit = (tomllib.load(fh).get("radiod") or {}).get("unit")
        if isinstance(unit, str) and unit:
            return unit
    except Exception as exc:                            # noqa: BLE001
        print(f"  could not read {path}: {exc}")
    cp = _systemctl(run, "list-units", "--no-legend", "--plain", "--state=active",
                    "radiod@*.service")
    units = [l.split()[0] for l in (cp.stdout or "").splitlines() if l.strip()]
    return units[0] if len(units) == 1 else None


def storage_stopped(path: Path = STORAGE_STATE) -> bool:
    """True when the storage ledger's stop lever holds the recorder's client."""
    try:
        d = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as exc:
        print(f"  storage state unreadable ({exc}); an operator's stop rule still holds")
        return False
    stopped = d.get("stopped") if isinstance(d, dict) else None
    return isinstance(stopped, dict) and CLIENT in stopped


# --- state and ledger --------------------------------------------------------

def load_state(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, d: dict) -> None:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, path)
    except OSError as exc:
        print(f"  could not persist state: {exc}")


def append_ledger(rows, path: Path = LEDGER, keep: Optional[int] = None) -> None:
    """Append one JSON line per row; keep the last ``keep`` (LEDGER_KEEP) lines."""
    rows = list(rows)
    if not rows:
        return
    keep = LEDGER_KEEP if keep is None else keep
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    with open(path) as fh:
        lines = fh.readlines()
    if len(lines) > keep:
        tmp = Path(str(path) + ".tmp")
        tmp.write_text("".join(lines[-keep:]))
        os.replace(tmp, path)


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _request_row(now: float, action: str, r: Request, reason: str) -> dict:
    return {"utc": _iso(now), "agent": AGENT, "action": action, "cause": r.cause,
            "reporter": r.reporter, "requested_utc": r.requested_utc,
            "evidence": r.evidence, "reason": reason}


# --- the tick ----------------------------------------------------------------

@dataclass(frozen=True)
class Paths:
    request_dir: Path = REQUEST_DIR
    ledger: Path = LEDGER
    state: Path = STATE
    compact_marker: Path = COMPACT_MARKER


@contextlib.contextmanager
def _flock_fallback(reason: str = "", *, wait_s=LOCK_WAIT_S):
    """The lifecycle lock without sigmond.lifecycle (which needs tomllib)."""
    import fcntl
    from sigmond.paths import LIFECYCLE_LOCK
    LIFECYCLE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LIFECYCLE_LOCK), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        end = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise RuntimeError(f"lifecycle lock still held after {wait_s} s")
                time.sleep(5)
        yield
    finally:
        os.close(fd)


def _default_lock():
    try:
        from sigmond.lifecycle import lifecycle_lock
        return lifecycle_lock
    except ImportError:
        return _flock_fallback


def _act(d: Decision, run: Callable) -> tuple:
    if d.action == "start":
        _systemctl(run, "reset-failed", UNIT)
        cp = _systemctl(run, "start", UNIT, timeout=ACT_TIMEOUT_S)
    else:
        cp = _systemctl(run, "restart", UNIT, timeout=ACT_TIMEOUT_S)
    tail = ((cp.stderr or cp.stdout or "").strip().splitlines() or [""])[-1]
    return cp.returncode == 0, (f"systemctl {d.action} exit {cp.returncode}"
                                + (f" ({tail})" if tail else ""))


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True            # EPERM: it exists
    return True


def restore_interrupted_compaction(marker: Path, run: Callable, now: float, *,
                                   dry_run: bool = False,
                                   say: Callable[[str], None] = print) -> list:
    """If a compaction marker outlives its pid, start the units it stopped (in
    order) and its expire timer, remove the marker, and return ledger rows.
    Undoes a tool's own stop, so no cooldown or cap applies."""
    try:
        d = json.loads(Path(marker).read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        d = None
    if isinstance(d, dict) and _pid_alive(d.get("pid")):
        return []                # compaction is running; leave its units alone
    if not isinstance(d, dict):
        say(f"WARNING: {marker} is unreadable; removing it, restoring nothing")
        if not dry_run:
            try:
                Path(marker).unlink()
            except OSError:
                pass
        return [{"utc": _iso(now), "agent": AGENT, "action": "compaction-restore",
                 "ok": False, "reason": "marker unreadable"}]
    units = [u for u in (d.get("units") or []) if isinstance(u, str)]
    timer = d.get("timer") if d.get("timer_was_active") else None
    todo = units + ([timer] if isinstance(timer, str) else [])
    if dry_run:
        say(f"  DRY-RUN: would restore after an interrupted compaction: {', '.join(todo)}")
        return []
    rows, bad = [], []
    for u in todo:
        cp = _systemctl(run, "start", u, timeout=ACT_TIMEOUT_S)
        ok = cp.returncode == 0
        if not ok:
            bad.append(u)
        rows.append({"utc": _iso(now), "agent": AGENT, "action": "compaction-restore",
                     "unit": u, "ok": ok, "exempt": True,
                     "reason": f"compaction (pid {d.get('pid')}) died; started {u}"})
    try:
        Path(marker).unlink()
    except OSError:
        pass
    say(f"WARNING: compaction interrupted; restored {len(todo) - len(bad)} units"
        + (f"; COULD NOT START {', '.join(bad)}" if bad else ""))
    return rows


def tick(*, paths: Paths = Paths(), run: Callable = subprocess.run,
         wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
         radiod_unit: Optional[Callable] = None, t6: Optional[Callable] = None,
         ledger_stopped: Optional[Callable] = None, lock: Optional[Callable] = None,
         dry_run: bool = False, say: Callable[[str], None] = print) -> int:
    """One watchdog pass: take requests, gather facts, decide, act, record.
    Returns 1 when an action failed, else 0."""
    radiod_unit = radiod_unit or (lambda: radiod_unit_from_topology(run))
    t6 = t6 or (lambda st, now: read_status(now, st))
    ledger_stopped = ledger_stopped or storage_stopped
    lock = lock or _default_lock()

    now = wall()
    st = load_state(paths.state)
    restore_rows = restore_interrupted_compaction(paths.compact_marker, run, now,
                                                  dry_run=dry_run, say=say)
    dropped: list = []
    reqs = take_requests(paths.request_dir, now, dropped=dropped, consume=not dry_run)
    rows = restore_rows + [{"utc": _iso(now), "agent": AGENT, "action": "dropped",
                            "reporter": name, "reason": why} for name, why in dropped]

    rec = unit_state(UNIT, run)
    if rec is None:
        say(f"{UNIT}: systemctl show failed; requests kept for the next tick")
        if not dry_run:
            for r in reqs:
                _refile(r, paths.request_dir)
            append_ledger(rows, paths.ledger)
        return 0

    seen = st.get("n_restarts_seen")
    if isinstance(seen, int) and rec.n_restarts != seen:
        delta = rec.n_restarts - seen if rec.n_restarts > seen else rec.n_restarts
        if delta > 0:
            rows.append({"utc": _iso(now), "agent": "systemd", "action": "restart",
                         "cause": "Restart=", "restarts": delta,
                         "reason": f"NRestarts {seen} -> {rec.n_restarts}"})
    st["n_restarts_seen"] = rec.n_restarts

    runit = radiod_unit()
    facts = Facts(now, mono(), rec, started_mono(runit, run) if runit else None,
                  t6(st, now), tuple(reqs), bool(ledger_stopped()), dict(st))
    d = decide(facts)
    say(f"{UNIT}: {rec.active_state}/{rec.result}; {len(reqs)} request(s) -> "
        f"{d.action}{f' ({d.cause})' if d.cause else ''}: {d.reason}")
    for r, why in d.refused:
        say(f"  refused {r.reporter} {r.cause}: {why}")
    if dry_run:
        say("  DRY-RUN: nothing acted on, nothing recorded")
        return 0

    rc = 0
    if d.action != "none":
        try:
            held = lock(f"{AGENT}: {d.action} {UNIT} ({d.cause})", wait_s=LOCK_WAIT_S)
            held.__enter__()
        except (SystemExit, Exception) as exc:            # noqa: BLE001
            say(f"  lifecycle lock not taken: {exc}; requests kept for the next tick")
            rows.append({"utc": _iso(now), "agent": AGENT, "action": "tick-refused",
                         "cause": d.cause, "reason": f"lifecycle lock not taken in "
                                                         f"{LOCK_WAIT_S} s: {exc}"})
            for r in reqs:
                _refile(r, paths.request_dir)
            append_ledger(rows, paths.ledger)
            save_state(paths.state, st)
            return 0
        try:
            # The facts may have moved while we waited (an smd restart, or
            # the storage ledger's stop lever).
            rec2 = unit_state(UNIT, run) or rec
            facts = dataclasses.replace(
                facts, now_mono=mono(), recorder=rec2,
                radiod_started_mono=started_mono(runit, run) if runit else None,
                ledger_stopped=bool(ledger_stopped()))
            d = decide(facts)
            if d.action != "none":
                ok, detail = _act(d, run)
                say(f"  {d.action} {UNIT}: {'ok' if ok else detail}")
                row = {"utc": _iso(now), "agent": AGENT, "action": d.action,
                       "cause": d.cause, "reason": d.reason, "ok": ok, "detail": detail,
                       "exempt": d.cause in EXEMPT}
                if d.request is not None:
                    row.update(reporter=d.request.reporter, evidence=d.request.evidence,
                               requested_utc=d.request.requested_utc)
                rows.append(row)
                st["last_action"], st["last_cause"] = now, d.cause
                if d.cause not in EXEMPT:
                    used = actions_today(st, now)
                    st["day"], st["actions_today"] = _day(now), used + 1
                if d.cause == RADIOD:
                    st["radiod_start_acted"] = facts.radiod_started_mono
                st["unlocked_since"] = None
                after = unit_state(UNIT, run)
                if after is not None:
                    st["n_restarts_seen"] = after.n_restarts
                rc = 0 if ok else 1
        finally:
            held.__exit__(None, None, None)

    rows += [_request_row(now, "refused", r, why) for r, why in d.refused]
    # A deferral because no radiod unit is known never ends on its own:
    # say so once per request, loudly, and in the ledger.
    warned = set(st.get("warned_deferrals") or ())
    keep = set()
    for r, why in d.deferred:
        say(f"  kept {r.reporter} {r.cause}: {why}")
        if runit is None:
            key = f"{r.reporter}@{r.requested_utc}"
            keep.add(key)
            if key not in warned:
                msg = (f"WARNING: {r.reporter} {r.cause} request deferred: no radiod unit "
                       f"known (set [radiod] unit in topology.toml); it drops after "
                       f"{REQUEST_MAX_AGE_S} s")
                say(f"  {msg}")
                rows.append(_request_row(now, "deferred", r, msg))
        _refile(r, paths.request_dir)
    st["warned_deferrals"] = sorted(keep)
    append_ledger(rows, paths.ledger)
    save_state(paths.state, st)
    return rc


def main(argv=None) -> int:
    import sys
    argv = sys.argv[1:] if argv is None else argv
    return tick(dry_run="--dry-run" in argv)

"""Tests for sigmond-recorder-watchdog, the one restarter of
timestd-core-recorder besides systemd.

Reporters file requests; ``decide`` applies one fresh-start grace, one
shared cooldown and one daily cap; ``tick`` acts and writes every action,
every refused request and every systemd restart to one ledger.

The T6 cases below moved here from tests/test_t6_stuck_watchdog.py.

AC0G-B4, 2026-08-31: T6 sat UNLOCKED for 6 h 23 m (03:38-10:01Z) and
only a restart of timestd-core-recorder cleared it.  Everything the
authority depends on was healthy the whole time:

  * C/N0 57-58 dB-Hz -- HIGHER than when it had been working at 03Z
  * costas_locked True throughout; the carrier never let go
  * T5/LBE-1421 120/120 with a valid fix, NMEA age 0.3 s
  * an independent block-wise estimator on the SAME multicast located
    the edge sample-exactly, 98% of seconds, median SNR ~340

So the signal carried the information continuously and the software
could not use it.  Availability went 97.2% (08-30) to 41.5% (08-31).

The trap this must avoid is the one sigmond's own watchdogs document:
"a watchdog that fires on a downstream symptom restarts things forever
without fixing anything" -- timestd-hpps-watchdog restarted the recorder
four times overnight on 2026-08-29 against a stale refclock when the
real cause was low C/N0.  So the T6 rule fires ONLY when the evidence
says the INPUT IS GOOD and the software is nonetheless not using it.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
import unittest
from pathlib import Path

import pytest

from sigmond import recorder_watchdog as rw
from sigmond import unit_moves as um

REPO = Path(__file__).resolve().parent.parent
G = rw.GRACE_S


def _f(**kw):  # a healthy, old recorder; radiod older still; nothing requested
    base = dict(now_wall=1e9, now_mono=100_000.0,
                recorder=rw.UnitState("active", "success", 100_000.0 - 5 * G, None, 0),
                radiod_started_mono=1_000.0, t6=None, requests=(), ledger_stopped=False,
                state={})
    base.update(kw)
    return rw.Facts(**base)


def _req(cause, **kw):
    return rw.Request(kw.get("reporter", "pipeline-watchdog"), cause, 1e9 - 10, "x",
                      kw.get("stopped", False))


def _stuck(**kw):
    base = dict(authoritative=False, costas_locked=True, cn0_db_hz=57.5,
                t5_valid_fix=True, stuck_for_sec=1800.0)
    base.update(kw)
    return rw.T6State(**base)


# --- the values Michael answered -------------------------------------------

def test_the_budget_values():
    assert rw.GRACE_S == 2 * 600 + 120 == 1320
    assert rw.COOLDOWN_S == 1800 and rw.MAX_PER_DAY == 6
    assert rw.MIN_STUCK_SEC == 1200 and rw.MIN_CN0_DB_HZ == 56.0
    assert rw.ACT_ON_HPPS is False
    assert rw.UNIT == "timestd-core-recorder.service"


# --- decide: grace ----------------------------------------------------------

def test_fresh_recorder_is_not_judged():
    # Mutation check: drop rule 5 and the no-output request restarts it.
    d = rw.decide(_f(recorder=rw.UnitState("active", "success", 100_000.0 - 400, None, 0),
                     requests=(_req("no-output"),)))
    assert d.action == "none" and "first full chunk" in d.refused[0][1]


def test_fresh_recorder_is_not_judged_on_t6_either():
    d = rw.decide(_f(recorder=rw.UnitState("active", "success", 100_000.0 - 400, None, 0),
                     t6=_stuck()))
    assert d.action == "none"


def test_grace_ends_after_two_chunks_and_a_flush():
    d = rw.decide(_f(recorder=rw.UnitState("active", "success", 100_000.0 - G - 1, None, 0),
                     requests=(_req("no-output"),)))
    assert d.action == "restart" and d.cause == "no-output"


# --- decide: radiod ---------------------------------------------------------

def test_radiod_restart_overrides_the_grace():
    # Review Focus 5.  Mutation check: put rule 5 ahead of rule 3.
    d = rw.decide(_f(recorder=rw.UnitState("active", "success", 99_900.0, None, 0),
                     radiod_started_mono=99_950.0, requests=(_req("radiod-restarted"),)))
    assert d.action == "restart" and d.cause == "radiod-restarted"


def test_radiod_restart_overrides_cooldown_and_cap():
    d = rw.decide(_f(recorder=rw.UnitState("active", "success", 99_900.0, None, 0),
                     radiod_started_mono=99_950.0, requests=(_req("radiod-restarted"),),
                     state={"last_action": 1e9 - 60, "day": "2001-09-09",
                            "actions_today": rw.MAX_PER_DAY}))
    assert d.action == "restart" and d.cause == "radiod-restarted"


def test_a_recorder_started_inside_the_settle_window_still_follows():
    d = rw.decide(_f(recorder=rw.UnitState("active", "success",
                                           99_950.0 + rw.READY_SETTLE_S - 1, None, 0),
                     radiod_started_mono=99_950.0, requests=(_req("radiod-restarted"),)))
    assert d.action == "restart"


def test_a_recorder_already_following_radiod_is_left_alone():
    d = rw.decide(_f(recorder=rw.UnitState("active", "success",
                                           99_950.0 + rw.READY_SETTLE_S + 5, None, 0),
                     radiod_started_mono=99_950.0, requests=(_req("radiod-restarted"),)))
    assert d.action == "none" and "already follows" in d.refused[0][1]


def test_radiod_restart_is_acted_on_once_per_radiod_start():
    # Mutation check: drop the radiod_start_acted test and this restarts again.
    facts = dict(recorder=rw.UnitState("active", "success", 99_900.0, None, 0),
                 radiod_started_mono=99_950.0, requests=(_req("radiod-restarted"),))
    assert rw.decide(_f(**facts)).action == "restart"
    d = rw.decide(_f(**facts, state={"radiod_start_acted": 99_950.0}))
    assert d.action == "none" and "once per radiod start" in d.refused[0][1]
    # A newer radiod start is a new event.
    d = rw.decide(_f(**{**facts, "radiod_started_mono": 99_960.0},
                     state={"radiod_start_acted": 99_950.0}))
    assert d.action == "restart"


def test_radiod_restart_starts_a_recorder_sdr_recover_stopped():
    for word, result in (("inactive", "success"), ("failed", "exit-code")):
        d = rw.decide(_f(recorder=rw.UnitState(word, result, None, 99_000.0, 0),
                         radiod_started_mono=99_950.0,
                         requests=(_req("radiod-restarted", stopped=True),)))
        assert (d.action, d.cause) == ("start", "radiod-restarted"), word


def test_radiod_restart_without_stopped_recorder_starts_no_stopped_recorder():
    d = rw.decide(_f(recorder=rw.UnitState("inactive", "success", None, 99_000.0, 0),
                     radiod_started_mono=99_950.0, requests=(_req("radiod-restarted"),)))
    assert d.action == "none"


def test_radiod_restart_waits_for_radiod_to_run():
    d = rw.decide(_f(recorder=rw.UnitState("inactive", "success", None, 99_000.0, 0),
                     radiod_started_mono=None,
                     requests=(_req("radiod-restarted", stopped=True),)))
    assert d.action == "none" and len(d.deferred) == 1 and not d.refused


# --- decide: stops that must hold -------------------------------------------

def test_operator_stop_is_never_undone():
    # Review Focus 4.  Mutation check: drop rule 2.
    for req in (_req("no-output"), _req("radiod-restarted"), _req("hpps-stale")):
        d = rw.decide(_f(recorder=rw.UnitState("inactive", "success", None, 99_000.0, 0),
                         requests=(req,), t6=_stuck()))
        assert d.action == "none", req.cause
        assert "operator" in d.reason


def test_ledger_stop_is_never_undone():
    # Review Focus 4.  Mutation check: drop rule 1.
    for rec in (rw.UnitState("inactive", "success", None, 99_000.0, 0),
                rw.UnitState("failed", "exit-code", None, 100_000.0 - 2 * rw.FAILED_REVIVE_S, 0)):
        d = rw.decide(_f(ledger_stopped=True, recorder=rec, radiod_started_mono=99_950.0,
                         requests=(_req("radiod-restarted", stopped=True),)))
        assert d.action == "none"
        assert "storage ledger" in d.refused[0][1]


# --- decide: failed ---------------------------------------------------------

def test_failed_is_revived_only_after_the_wait():
    # Mutation check: drop the FAILED_REVIVE_S comparison.
    def at(age):
        return rw.decide(_f(recorder=rw.UnitState("failed", "exit-code", None,
                                                  100_000.0 - age, 3)))
    assert at(rw.FAILED_REVIVE_S - 1).action == "none"
    d = at(rw.FAILED_REVIVE_S)
    assert (d.action, d.cause) == ("start", "failed") and "systemd gave up" in d.reason


def test_failed_revival_respects_the_cap():
    d = rw.decide(_f(recorder=rw.UnitState("failed", "exit-code", None, 90_000.0, 3),
                     state={"day": "2001-09-09", "actions_today": rw.MAX_PER_DAY}))
    assert d.action == "none"


def test_a_failed_recorder_is_not_restarted_by_a_request():
    d = rw.decide(_f(recorder=rw.UnitState("failed", "exit-code", None, 99_900.0, 3),
                     requests=(_req("no-output"),), t6=_stuck()))
    assert d.action == "none" and d.refused


# --- decide: cooldown and cap -----------------------------------------------

def test_cooldown_and_cap_are_shared_across_causes():
    # A t6 restart ten minutes ago holds a no-output request.
    d = rw.decide(_f(requests=(_req("no-output"),),
                     state={"last_action": 1e9 - 600, "last_cause": "t6-stuck"}))
    assert d.action == "none" and "cooldown" in d.refused[0][1]
    d = rw.decide(_f(requests=(_req("no-output"),), state={"last_action": 1e9 - rw.COOLDOWN_S}))
    assert d.action == "restart"
    # At the cap, today: held.  The day rolled over: free.
    capped = {"day": "2001-09-09", "actions_today": rw.MAX_PER_DAY}
    d = rw.decide(_f(t6=_stuck(), state=capped))
    assert d.action == "none" and "daily cap" in d.reason
    d = rw.decide(_f(t6=_stuck(), state={**capped, "day": "2001-09-08"}))
    assert (d.action, d.cause) == ("restart", "t6-stuck")


# --- decide: causes ---------------------------------------------------------

def test_t6_stuck_restarts_and_covers_a_request():
    d = rw.decide(_f(t6=_stuck(), requests=(_req("no-output"),)))
    assert (d.action, d.cause) == ("restart", "t6-stuck")
    assert "covered" in d.refused[0][1]


def test_hpps_stale_is_recorded_not_acted_on(monkeypatch):
    # Mutation check: flip ACT_ON_HPPS.
    d = rw.decide(_f(requests=(_req("hpps-stale", reporter="hpps-watchdog"),)))
    assert d.action == "none" and "report-only" in d.refused[0][1]
    monkeypatch.setattr(rw, "ACT_ON_HPPS", True)
    d = rw.decide(_f(requests=(_req("hpps-stale", reporter="hpps-watchdog"),)))
    assert (d.action, d.cause) == ("restart", "hpps-stale")


def test_an_unknown_cause_is_refused():
    d = rw.decide(_f(requests=(_req("feels-off"),)))
    assert d.action == "none" and "unknown cause" in d.refused[0][1]


def test_nothing_asked_nothing_done():
    d = rw.decide(_f())
    assert d.action == "none" and not d.refused


# --- requests ---------------------------------------------------------------

def test_file_request_is_atomic_and_carries_schema_1(tmp_path, monkeypatch):
    replaced = []
    real = os.replace
    monkeypatch.setattr(rw.os, "replace",
                        lambda a, b: (replaced.append((str(a), str(b))), real(a, b))[1])
    p = rw.file_request("pipeline-watchdog", "no-output", "newest chunk 1500 s old",
                        request_dir=tmp_path / "rr", now=1e9)
    assert p == tmp_path / "rr" / "pipeline-watchdog.json"
    assert replaced == [(str(p) + ".tmp", str(p))]
    assert json.loads(p.read_text()) == {
        "schema": 1, "reporter": "pipeline-watchdog", "cause": "no-output",
        "requested_utc": 1e9, "evidence": "newest chunk 1500 s old",
        "stopped_recorder": False}
    assert list((tmp_path / "rr").iterdir()) == [p]


def test_file_request_refuses_a_path_as_reporter(tmp_path):
    with pytest.raises(ValueError):
        rw.file_request("../x", "no-output", "", request_dir=tmp_path)


def test_take_requests_consumes_each_once_and_drops_stale(tmp_path):
    # Mutation check: skip the unlink, and the second take returns them again.
    rw.file_request("a", "no-output", "e1", request_dir=tmp_path, now=1e9 - 10)
    rw.file_request("b", "radiod-restarted", "e2", stopped_recorder=True,
                    request_dir=tmp_path, now=1e9 - 5)
    rw.file_request("old", "no-output", "e3", request_dir=tmp_path,
                    now=1e9 - rw.REQUEST_MAX_AGE_S - 1)
    (tmp_path / "junk.json").write_text("{not json")
    dropped = []
    got = rw.take_requests(tmp_path, 1e9, dropped=dropped)
    assert got == [rw.Request("a", "no-output", 1e9 - 10, "e1", False),
                   rw.Request("b", "radiod-restarted", 1e9 - 5, "e2", True)]
    assert sorted(name for name, _ in dropped) == ["junk", "old"]
    assert any("older than" in why for _, why in dropped)
    assert list(tmp_path.iterdir()) == []
    assert rw.take_requests(tmp_path, 1e9) == []


def test_take_requests_picks_up_a_taken_file_a_crash_left(tmp_path):
    p = rw.file_request("a", "no-output", "e", request_dir=tmp_path, now=1e9)
    p.rename(str(p) + ".taken")
    assert [r.reporter for r in rw.take_requests(tmp_path, 1e9)] == ["a"]


def test_take_requests_peek_leaves_files(tmp_path):
    rw.file_request("a", "no-output", "e", request_dir=tmp_path, now=1e9)
    assert len(rw.take_requests(tmp_path, 1e9, consume=False)) == 1
    assert len(rw.take_requests(tmp_path, 1e9)) == 1


def test_take_requests_on_a_missing_dir(tmp_path):
    assert rw.take_requests(tmp_path / "nope", 1e9) == []


def test_kick_starts_the_watchdog_without_blocking():
    calls = []
    rw.kick(run=lambda argv, **kw: calls.append(argv))
    assert calls == [["systemctl", "start", "--no-block", "sigmond-recorder-watchdog.service"]]


def test_kick_never_raises():
    def boom(argv, **kw):
        raise OSError("no systemctl")
    rw.kick(run=boom)


# --- the ledger -------------------------------------------------------------

def test_ledger_keeps_the_last_2000_lines(tmp_path):
    # Mutation check: drop the trim and the file holds 2010 lines.
    led = tmp_path / "led.jsonl"
    led.write_text("".join(json.dumps({"n": i}) + "\n" for i in range(1990)))
    rw.append_ledger([{"n": 1990 + i} for i in range(20)], path=led)
    lines = led.read_text().splitlines()
    assert len(lines) == rw.LEDGER_KEEP == 2000
    assert json.loads(lines[0]) == {"n": 10} and json.loads(lines[-1]) == {"n": 2009}


# --- tick: the action and its ledger ---------------------------------------

class FakeSystemd:
    """systemctl show / restart / start, against a recorder and a radiod."""

    def __init__(self, recorder, radiod_mono=1_000.0, rc=0):
        self.recorder = dict(recorder)
        self.radiod_mono = radiod_mono
        self.rc = rc
        self.calls = []

    def __call__(self, argv, **kw):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:2] == ["systemctl", "show"]:
            unit = argv[-1]
            if unit == rw.UNIT:
                r = self.recorder
                out = (f"ActiveState={r['state']}\nResult={r['result']}\n"
                       f"ActiveEnterTimestampMonotonic={int(r['enter'] * 1e6)}\n"
                       f"InactiveEnterTimestampMonotonic={int(r.get('inactive', 0) * 1e6)}\n"
                       f"NRestarts={r.get('n', 0)}\n")
            else:
                active = self.radiod_mono is not None
                out = (f"ActiveState={'active' if active else 'inactive'}\nResult=success\n"
                       f"ActiveEnterTimestampMonotonic={int((self.radiod_mono or 0) * 1e6)}\n"
                       "InactiveEnterTimestampMonotonic=0\nNRestarts=0\n")
            return subprocess.CompletedProcess(argv, 0, out, "")
        if argv[:2] in (["systemctl", "restart"], ["systemctl", "start"]):
            self.recorder.update(state="active", enter=100_000.0, n=0)
            return subprocess.CompletedProcess(argv, self.rc, "", "boom" if self.rc else "")
        return subprocess.CompletedProcess(argv, 0, "", "")


def _tick(tmp_path, sysd, *, t6=None, stopped=False, lock_ok=True, dry=False):
    paths = rw.Paths(request_dir=tmp_path / "req", ledger=tmp_path / "led.jsonl",
                     state=tmp_path / "state.json")
    lock_calls = []

    @contextlib.contextmanager
    def lock(reason="", *, wait_s=None):
        lock_calls.append(wait_s)
        if not lock_ok:
            raise RuntimeError("lifecycle lock held by smd restart")
        yield

    rc = rw.tick(paths=paths, run=sysd, wall=lambda: 1e9, mono=lambda: 100_000.0,
                 radiod_unit=lambda: "radiod@x.service", t6=lambda st, now: t6,
                 ledger_stopped=lambda: stopped, lock=lock, dry_run=dry)
    rows = ([json.loads(l) for l in paths.ledger.read_text().splitlines()]
            if paths.ledger.exists() else [])
    state = json.loads(paths.state.read_text()) if paths.state.exists() else {}
    return rc, rows, state, lock_calls, paths


def test_tick_restarts_on_a_request_and_writes_the_ledger(tmp_path):
    sysd = FakeSystemd({"state": "active", "result": "success", "enter": 100_000.0 - 5 * G})
    rw.file_request("pipeline-watchdog", "no-output", "no chunk 1500 s",
                    request_dir=tmp_path / "req", now=1e9 - 5)
    rc, rows, state, lock_calls, _ = _tick(tmp_path, sysd)
    assert rc == 0
    assert ["systemctl", "restart", rw.UNIT] in sysd.calls
    assert lock_calls == [60]
    assert [(r["agent"], r["action"], r["cause"]) for r in rows] == [
        ("sigmond-recorder-watchdog", "restart", "no-output")]
    assert rows[0]["reporter"] == "pipeline-watchdog" and rows[0]["ok"] is True
    assert state["last_action"] == 1e9 and state["actions_today"] == 1


def test_tick_start_resets_failed_first(tmp_path):
    sysd = FakeSystemd({"state": "failed", "result": "exit-code", "enter": 0,
                        "inactive": 100_000.0 - rw.FAILED_REVIVE_S, "n": 5})
    _tick(tmp_path, sysd)
    acts = [c for c in sysd.calls if c[1] in ("reset-failed", "start", "restart")]
    assert acts == [["systemctl", "reset-failed", rw.UNIT], ["systemctl", "start", rw.UNIT]]


def test_tick_records_a_restart_systemd_made(tmp_path):
    sysd = FakeSystemd({"state": "active", "result": "success",
                        "enter": 100_000.0 - 5 * G, "n": 2})
    _, rows, state, _, _ = _tick(tmp_path, sysd)
    assert rows == []                       # first sight: a baseline, not an event
    sysd.recorder["n"] = 4
    _, rows, state, _, _ = _tick(tmp_path, sysd)
    assert [(r["agent"], r["action"], r["restarts"]) for r in rows] == [
        ("systemd", "restart", 2)]
    assert state["n_restarts_seen"] == 4


def test_tick_writes_refused_and_dropped_requests(tmp_path):
    sysd = FakeSystemd({"state": "active", "result": "success", "enter": 100_000.0 - 5 * G})
    rw.file_request("hpps-watchdog", "hpps-stale", "HPPS 900 s stale",
                    request_dir=tmp_path / "req", now=1e9 - 5)
    rw.file_request("old", "no-output", "", request_dir=tmp_path / "req", now=1e9 - 5000)
    _, rows, _, lock_calls, _ = _tick(tmp_path, sysd)
    assert sorted((r["action"], r["reporter"]) for r in rows) == [
        ("dropped", "old"), ("refused", "hpps-watchdog")]
    assert not any(c[1] in ("restart", "start") for c in sysd.calls)
    assert lock_calls == []                 # no action, no lock


def test_tick_refuses_when_the_lock_is_busy_and_keeps_the_requests(tmp_path):
    sysd = FakeSystemd({"state": "active", "result": "success", "enter": 100_000.0 - 5 * G})
    rw.file_request("pipeline-watchdog", "no-output", "x",
                    request_dir=tmp_path / "req", now=1e9 - 5)
    rc, rows, _, _, paths = _tick(tmp_path, sysd, lock_ok=False)
    assert not any(c[1] == "restart" for c in sysd.calls)
    assert rows[0]["action"] == "tick-refused" and "lifecycle lock" in rows[0]["reason"]
    assert [r.reporter for r in rw.take_requests(paths.request_dir, 1e9)] == [
        "pipeline-watchdog"]


def test_tick_records_a_failed_restart(tmp_path):
    sysd = FakeSystemd({"state": "active", "result": "success", "enter": 100_000.0 - 5 * G},
                       rc=1)
    rc, rows, _, _, _ = _tick(tmp_path, sysd, t6=_stuck())
    assert rc == 1 and rows[0]["ok"] is False and rows[0]["cause"] == "t6-stuck"


def test_tick_marks_the_radiod_start_it_followed(tmp_path):
    sysd = FakeSystemd({"state": "active", "result": "success", "enter": 99_900.0},
                       radiod_mono=99_950.0)
    rw.file_request("sdr-recover", "radiod-restarted", "", request_dir=tmp_path / "req",
                    now=1e9 - 5)
    _, rows, state, _, _ = _tick(tmp_path, sysd)
    assert state["radiod_start_acted"] == 99_950.0
    assert rows[0]["cause"] == "radiod-restarted" and rows[0]["exempt"] is True
    assert state.get("actions_today", 0) == 0           # exempt from the cap


def test_tick_keeps_a_deferred_request(tmp_path):
    sysd = FakeSystemd({"state": "inactive", "result": "success", "enter": 0,
                        "inactive": 99_000.0}, radiod_mono=None)
    rw.file_request("sdr-recover", "radiod-restarted", "", stopped_recorder=True,
                    request_dir=tmp_path / "req", now=1e9 - 5)
    _, _, _, _, paths = _tick(tmp_path, sysd)
    kept = rw.take_requests(paths.request_dir, 1e9)
    assert [(r.reporter, r.requested_utc, r.stopped_recorder) for r in kept] == [
        ("sdr-recover", 1e9 - 5, True)]


def test_tick_dry_run_acts_on_nothing(tmp_path):
    sysd = FakeSystemd({"state": "active", "result": "success", "enter": 100_000.0 - 5 * G})
    rw.file_request("pipeline-watchdog", "no-output", "x", request_dir=tmp_path / "req",
                    now=1e9 - 5)
    _, rows, _, _, paths = _tick(tmp_path, sysd, dry=True)
    assert not any(c[1] in ("restart", "start") for c in sysd.calls)
    assert rows == [] and len(rw.take_requests(paths.request_dir, 1e9)) == 1


def test_tick_reads_t6_and_restarts(tmp_path):
    sysd = FakeSystemd({"state": "active", "result": "success", "enter": 100_000.0 - 5 * G})
    _, rows, state, _, _ = _tick(tmp_path, sysd, t6=_stuck())
    assert rows[0]["cause"] == "t6-stuck" and state["unlocked_since"] is None


# --- the install and the retirement of sigmond-t6-stuck-watchdog -----------

def test_the_old_t6_watchdog_is_gone_and_retired():
    assert not (REPO / "bin" / "sigmond-t6-stuck-watchdog").exists()
    assert not list((REPO / "systemd").glob("sigmond-t6-stuck-watchdog.*"))
    own = {u: r for r in um.RETIRED_OWN for u in r.units}
    for u in ("sigmond-t6-stuck-watchdog.timer", "sigmond-t6-stuck-watchdog.service"):
        assert u in own
    assert "/usr/local/sbin/sigmond-t6-stuck-watchdog" in own[
        "sigmond-t6-stuck-watchdog.timer"].links
    text = (REPO / "install.sh").read_text()
    assert "sigmond-t6-stuck-watchdog" not in text
    assert "sigmond-recorder-watchdog" in text


def test_retire_clears_the_installed_copies_and_the_link(tmp_path):
    """install.sh copied the two units (install -m 0644) and linked the
    helper into /usr/local/sbin; the release deleted all three sources."""
    (retire,) = [r for r in um.RETIRED_OWN if "sigmond-t6-stuck-watchdog.timer" in r.units]
    unit_dir = tmp_path / "units"
    (unit_dir / "timers.target.wants").mkdir(parents=True)
    for u in retire.units:
        (unit_dir / u).write_text("[Unit]\n")
    (unit_dir / "timers.target.wants" / "sigmond-t6-stuck-watchdog.timer").symlink_to(
        unit_dir / "sigmond-t6-stuck-watchdog.timer")
    sbin = tmp_path / "sbin"
    sbin.mkdir()
    link = sbin / "sigmond-t6-stuck-watchdog"
    link.symlink_to(tmp_path / "checkout" / "bin" / "sigmond-t6-stuck-watchdog")  # dangles
    local = um.UnitRetire(retire.component, retire.units, (str(link),))
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        if argv[1] == "is-enabled":
            return subprocess.CompletedProcess(argv, 0, "enabled\n", "")
        if argv[1] == "is-active":
            word = "active" if argv[-1].endswith(".timer") else "inactive"
            return subprocess.CompletedProcess(argv, 0, word + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    from datetime import datetime, timezone
    step = um.apply_retire(local, apply=True, unit_dir=unit_dir, backup_dir=tmp_path / "bk",
                           run=run, now=datetime(2026, 11, 26, tzinfo=timezone.utc),
                           checkout_root=str(tmp_path / "checkout") + "/")
    assert step.outcome == "retired", step.detail
    assert ["systemctl", "stop", "sigmond-t6-stuck-watchdog.timer"] in calls
    assert not any((unit_dir / u).exists() for u in retire.units)
    assert not (unit_dir / "timers.target.wants" / "sigmond-t6-stuck-watchdog.timer").is_symlink()
    assert not link.is_symlink()
    assert len(list((tmp_path / "bk").iterdir())) == 2


def test_units_create_the_request_dir_and_run_the_new_script():
    svc = (REPO / "systemd" / "sigmond-recorder-watchdog.service").read_text()
    assert "ExecStart=/usr/local/sbin/sigmond-recorder-watchdog" in svc
    assert "RuntimeDirectory=sigmond/recorder-restart" in svc
    assert "RuntimeDirectoryPreserve=yes" in svc
    tmr = (REPO / "systemd" / "sigmond-recorder-watchdog.timer").read_text()
    for line in ("OnBootSec=300", "OnUnitActiveSec=60", "AccuracySec=10s",
                 "WantedBy=timers.target"):
        assert line in tmr
    assert str(rw.REQUEST_DIR) == "/run/sigmond/recorder-restart"


# --- T6: moved from tests/test_t6_stuck_watchdog.py, against should_restart_t6

def state(**kw):
    return _stuck(**kw)


class TestFiresOnlyOnTheStuckState(unittest.TestCase):
    def test_the_ac0g_b4_signature_fires(self):
        """Unlocked, carrier fine, signal strong, GPS good, sustained."""
        self.assertTrue(rw.should_restart_t6(state()))

    def test_healthy_t6_never_fires(self):
        self.assertFalse(rw.should_restart_t6(state(authoritative=True)))


class TestRefusesWhenTheInputIsBad(unittest.TestCase):
    """Each of these is a reason the software is RIGHT to be unlocked."""

    def test_weak_signal_is_not_a_stuck_state(self):
        self.assertFalse(rw.should_restart_t6(state(cn0_db_hz=52.0)))

    def test_an_unlocked_carrier_is_not_a_stuck_state(self):
        self.assertFalse(rw.should_restart_t6(state(costas_locked=False)))

    def test_no_gps_fix_is_not_a_stuck_state(self):
        self.assertFalse(rw.should_restart_t6(state(t5_valid_fix=False)))

    def test_a_brief_unlock_is_not_a_stuck_state(self):
        """T6 re-acquires on its own in minutes; only a sustained
        unlock against a good signal means stuck."""
        self.assertFalse(rw.should_restart_t6(state(stuck_for_sec=120.0)))

    def test_missing_telemetry_refuses_rather_than_guesses(self):
        self.assertFalse(rw.should_restart_t6(state(cn0_db_hz=None)))
        self.assertFalse(rw.should_restart_t6(state(costas_locked=None)))

    def test_none_is_no_t6_at_all(self):
        self.assertFalse(rw.should_restart_t6(None))


class TestStormGuards(unittest.TestCase):
    """The old script's own cooldown and cap became the shared ones."""

    def test_cooldown_blocks_a_second_restart(self):
        d = rw.decide(_f(t6=state(), state={"last_action": 1e9 - 10}))
        self.assertEqual(d.action, "none")

    def test_cooldown_expires(self):
        d = rw.decide(_f(t6=state(), state={"last_action": 1e9 - rw.COOLDOWN_S - 1}))
        self.assertEqual(d.action, "restart")

    def test_no_prior_action_is_allowed(self):
        self.assertEqual(rw.decide(_f(t6=state())).action, "restart")

    def test_daily_cap_is_enforced(self):
        day = "2001-09-09"   # 1e9 s, UTC
        self.assertEqual(rw.decide(_f(t6=state(), state={
            "day": day, "actions_today": rw.MAX_PER_DAY})).action, "none")
        self.assertEqual(rw.decide(_f(t6=state(), state={
            "day": day, "actions_today": rw.MAX_PER_DAY - 1})).action, "restart")


class TestTelemetryComesFromWhereItActuallyLives(unittest.TestCase):
    """C/N0 is not in core-recorder-status.json.

    The first cut read t6_pps.baseband_power / t6_pps.n0 from the status
    file.  Those keys do not exist there -- the frontend probe writes
    them to the authority snapshot store -- so the gate would have
    returned None on every tick and the watchdog would have sat silent
    forever while looking installed and healthy.
    """

    def test_cn0_reads_the_authority_db(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db") as fh:
            con = sqlite3.connect(fh.name)
            con.execute("CREATE TABLE authority_snapshot ("
                        "utc_published TEXT, t6_baseband_power REAL, t6_n0 REAL)")
            con.execute("INSERT INTO authority_snapshot VALUES "
                        "('2026-08-31T04:00:00Z', -95.0, -153.7)")
            con.commit(); con.close()
            self.assertAlmostEqual(rw.cn0_from_db(fh.name), 58.7, places=1)

    def test_a_missing_db_refuses_rather_than_acting(self):
        self.assertIsNone(rw.cn0_from_db("/nonexistent/nope.db"))


class NullTelemetrySectionTest(unittest.TestCase):
    """A section present-but-null crashed the watchdog every 5 minutes.

    AC0G-ND, 2026-09-03.  A fresh station has no TS-1, so core-recorder
    writes the authority section as JSON null rather than omitting it.
    ``d.get("t6_authority", {})`` supplies its default only when the KEY IS
    ABSENT, so a present null came back as None and ``auth.get("state")``
    raised AttributeError on every timer tick.  A watchdog that dies on a
    station with nothing yet to watch reports its own breakage as the
    station's.
    """

    def _read(self, payload):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'status.json'
            p.write_text(json.dumps(payload))
            return rw.read_status(1000.0, {}, status_path=p, cn0=lambda: None)

    def test_null_authority_section_does_not_crash(self):
        st = self._read({'t6_authority': None})
        self.assertIsNotNone(st, 'a readable status must still yield a state')
        self.assertIsNone(st.authoritative,
                          'no authority section means UNKNOWN, not False — '
                          'False would let the watchdog start restarting')

    def test_null_pps_section_does_not_crash(self):
        st = self._read({'t6_pps': None, 't6_authority': None})
        self.assertIsNone(st.costas_locked)

    def test_all_three_sections_null_at_once(self):
        st = self._read({'t6_authority': None, 't6_pps': None,
                         't5_lbe1421': None})
        self.assertIsNone(st.authoritative)
        self.assertIsNone(st.costas_locked)
        self.assertIsNone(st.t5_valid_fix)

    def test_a_populated_status_is_still_read_correctly(self):
        # Guards the guard: `or {}` must not swallow real telemetry.
        st = self._read({
            't6_authority': {'state': 'AUTHORITATIVE'},
            't6_pps': {'costas_locked': True},
            't5_lbe1421': {'valid_fix': True},
        })
        self.assertIs(st.authoritative, True)
        self.assertIs(st.costas_locked, True)
        self.assertIs(st.t5_valid_fix, True)

    def test_a_non_authoritative_state_still_reads_false(self):
        st = self._read({'t6_authority': {'state': 'ACQUIRING'}})
        self.assertIs(st.authoritative, False)

    def test_unlocked_since_tracks_across_ticks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'status.json'
            p.write_text(json.dumps({'t6_authority': {'state': 'ACQUIRING'}}))
            st = {}
            rw.read_status(1000.0, st, status_path=p, cn0=lambda: None)
            s = rw.read_status(1600.0, st, status_path=p, cn0=lambda: None)
            self.assertEqual(s.stuck_for_sec, 600.0)
            p.write_text(json.dumps({'t6_authority': {'state': 'AUTHORITATIVE'}}))
            rw.read_status(1700.0, st, status_path=p, cn0=lambda: None)
            self.assertIsNone(st['unlocked_since'])

    def test_an_unreadable_status_refuses(self):
        self.assertIsNone(rw.read_status(1.0, {}, status_path=Path('/nonexistent/s.json'),
                                         cn0=lambda: None))

"""smd storage compact: stops a database's holders, swaps, starts them."""
from __future__ import annotations

import contextlib
import fcntl
import json
import subprocess
from types import SimpleNamespace

import pytest

from sigmond import recorder_watchdog as rw
from sigmond.storage import compact as c
from sigmond.storage.clientcall import (CompactAnswer, call_compact_holders,
                                        call_compact_swap)

REC, MET = "timestd-core-recorder.service", "timestd-metrology.service"
ORDER = [REC, MET]          # start order


class Rig:
    def __init__(self, holders=((1, REC), (2, MET)), swap_ok=True, hands_off="",
                 stop_fail=(), timer_active=True, expire_busy=False, holders_refused=False):
        self.ev = []
        self.estimate_ans = CompactAnswer(True, data={"live_bytes": 100,
                                                      "expected_bytes": 40})
        self.marker = None
        self.holders_ans = holders
        self.swap_ok, self.stop_fail = swap_ok, set(stop_fail)
        self.timer_active, self.expire_busy = timer_active, expire_busy
        ok = lambda **k: CompactAnswer(True, data=k.get("data", {}))
        self.r = c.CompactRunners(
            hands_off=lambda: hands_off,
            binary_for=lambda cl: "/bin/hf",
            estimate=lambda b, t: self._rec("estimate", self.estimate_ans),
            prepare=lambda b, t: self._rec("prepare", CompactAnswer(
                True, data={"db_bytes": 100, "compact_bytes": 40})),
            holders=lambda b, t: self._rec("holders", CompactAnswer(
                True, refused=holders_refused, reason="no" if holders_refused else "",
                holders=tuple(self.holders_ans))),
            swap=lambda b, t: self._rec("swap", CompactAnswer(
                self.swap_ok, reason="" if self.swap_ok else "swap broke")),
            start_order=lambda cl: ORDER,
            systemctl=self._systemctl,
            is_active=lambda u: self.timer_active,
            expire_lock=self._expire_lock,
            lifecycle_lock=self._lifecycle,
            journal=lambda line: self.ev.append(("journal", line)),
            say=lambda line: self.ev.append(("say", line)),
            marker_path=None)

    def _rec(self, name, ans):
        self.ev.append((name,))
        return ans

    def _systemctl(self, verb, unit):
        self.ev.append((verb, unit))
        return 1 if (verb == "stop" and unit in self.stop_fail) else 0

    @contextlib.contextmanager
    def _expire_lock(self):
        if self.expire_busy:
            raise c.CompactError("an expire pass still runs")
        self.ev.append(("expire-lock",))
        try:
            yield
        finally:
            self.ev.append(("expire-unlock",))

    @contextlib.contextmanager
    def _lifecycle(self, why):
        self.ev.append(("lifecycle-lock",))
        try:
            yield
        finally:
            self.ev.append(("lifecycle-unlock",))

    def calls(self):
        return [e for e in self.ev if e[0] not in ("journal", "say")]

    def says(self):
        return " | ".join(e[1] for e in self.ev if e[0] == "say")


def _sysctl(rig):
    return [e for e in rig.calls() if e[0] in ("stop", "start")]


def test_dry_run_stops_nothing():
    rig = Rig()
    assert c.compact_client("hf-timestd", "timestd", apply=False, r=rig.r) == 0
    assert _sysctl(rig) == [] and ("swap",) not in rig.calls()
    assert ("prepare",) not in rig.calls()      # mutation: prepare in the dry run
    assert ("estimate",) in rig.calls() and ("holders",) in rig.calls()
    out = rig.says()
    assert "live_bytes=100" in out and REC in out and MET in out


def test_dry_run_says_so_when_estimate_is_unsupported_and_still_lists_holders():
    rig = Rig()
    rig.estimate_ans = CompactAnswer(False, reason="verb not supported (older client)",
                                     unsupported=True)
    assert c.compact_client("hf-timestd", "timestd", apply=False, r=rig.r) == 0
    assert "does not support --estimate" in rig.says() and REC in rig.says()
    assert ("prepare",) not in rig.calls()


def test_dry_run_reports_a_would_refuse_estimate():
    rig = Rig()
    rig.estimate_ans = CompactAnswer(True, data={"would_refuse": True, "reason": "no space",
                                                 "free_bytes": 5})
    c.compact_client("hf-timestd", "timestd", apply=False, r=rig.r)
    assert "would refuse: no space" in rig.says()


def test_dry_run_with_a_running_expire_says_apply_waits():
    rig = Rig(holders=((1, REC), (4, "sigmond-storage-expire.service")))
    c.compact_client("hf-timestd", "timestd", apply=False, r=rig.r)
    assert "apply waits" in rig.says() and "would refuse" not in rig.says()


def test_apply_still_prepares():
    rig = Rig()
    c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r)
    assert ("prepare",) in rig.calls() and ("estimate",) not in rig.calls()


def test_apply_stops_holders_swaps_and_starts_them_in_order():
    rig = Rig()
    assert c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r) == 0
    assert _sysctl(rig) == [("stop", "sigmond-storage-expire.timer"),
                            ("stop", MET), ("stop", REC),
                            ("start", REC), ("start", MET),
                            ("start", "sigmond-storage-expire.timer")]
    names = [e[0] for e in rig.calls()]
    assert names.index("swap") > names.index("stop") and \
        names.index("swap") < len(names) - names[::-1].index("start") - 1
    assert any(e[0] == "journal" and "stop " + MET in e[1] for e in rig.ev)


def test_a_failed_swap_still_restarts_every_unit_it_stopped():
    # Mutation check: return early after a failed swap.
    rig = Rig(swap_ok=False)
    assert c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r) == 1
    assert [e for e in _sysctl(rig) if e[0] == "start"] == [
        ("start", REC), ("start", MET), ("start", "sigmond-storage-expire.timer")]


def test_a_failed_stop_restarts_what_it_stopped():
    rig = Rig(stop_fail={REC})
    assert c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r) == 1
    assert ("swap",) not in rig.calls()
    starts = [e for e in _sysctl(rig) if e[0] == "start"]
    assert ("start", MET) in starts and ("start", REC) in starts


def test_a_holder_without_a_unit_refuses_before_stopping_anything():
    rig = Rig(holders=((1, REC), (7, None)))
    assert c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r) == 1
    assert [e for e in _sysctl(rig) if e[1] != c.EXPIRE_TIMER] == []
    assert ("swap",) not in rig.calls()
    assert "no unit" in rig.says()


def test_hands_off_refuses():
    rig = Rig(hands_off="until tomorrow")
    assert c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r) == 1
    assert rig.calls() == []


@pytest.mark.parametrize("unit", ["radiod@main.service", "ka9q-web.service",
                                  "sigmond-storage-tick.service", "radiod.service"])
def test_radiod_is_never_stopped(unit):
    rig = Rig(holders=((1, REC), (3, unit)))
    assert c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r) == 1
    assert [e for e in _sysctl(rig) if e[1] != c.EXPIRE_TIMER] == []


def test_the_expire_timer_is_stopped_and_locked_before_holders_are_asked():
    rig = Rig()
    c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r)
    names = [e for e in rig.calls()]
    assert names.index(("stop", c.EXPIRE_TIMER)) < names.index(("expire-lock",)) \
        < names.index(("holders",))


def test_a_running_expire_blocks_the_holders_check_and_the_timer_returns():
    rig = Rig(expire_busy=True)
    assert c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r) == 1
    assert ("holders",) not in rig.calls()
    assert _sysctl(rig) == [("stop", c.EXPIRE_TIMER), ("start", c.EXPIRE_TIMER)]


def test_the_timer_comes_back_after_a_failure():
    for kw in (dict(swap_ok=False), dict(stop_fail={MET}), dict(holders=((9, None),)),
               dict(holders_refused=True)):
        rig = Rig(**kw)
        assert c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r) == 1
        assert _sysctl(rig)[-1] == ("start", c.EXPIRE_TIMER), kw


def test_an_exception_still_restarts_units_and_timer():
    rig = Rig()
    rig.r.swap = lambda b, t: (_ for _ in ()).throw(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r)
    assert [e for e in _sysctl(rig) if e[0] == "start"] == [
        ("start", REC), ("start", MET), ("start", c.EXPIRE_TIMER)]


def test_a_stopped_timer_is_not_started_by_compaction():
    rig = Rig(timer_active=False)
    c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r)
    assert all(e[1] != c.EXPIRE_TIMER for e in _sysctl(rig))


# --- interruption -----------------------------------------------------------

def test_sigterm_mid_swap_still_starts_units_and_timer():
    import os, signal
    rig = Rig()
    def swap(b, t):
        rig.ev.append(("swap",))
        os.kill(os.getpid(), signal.SIGTERM)
        rig.ev.append(("after-signal",))      # not reached
        return CompactAnswer(True)
    rig.r.swap = swap
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(SystemExit) as e:
        c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r)
    assert e.value.code == 128 + signal.SIGTERM
    assert ("after-signal",) not in rig.calls()
    assert [x for x in _sysctl(rig) if x[0] == "start"] == [
        ("start", REC), ("start", MET), ("start", c.EXPIRE_TIMER)]
    assert signal.getsignal(signal.SIGTERM) is before      # handler restored


def test_sighup_is_handled_too():
    import os, signal
    rig = Rig()
    def swap(b, t):
        os.kill(os.getpid(), signal.SIGHUP)
        return CompactAnswer(True)
    rig.r.swap = swap
    with pytest.raises(SystemExit):
        c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r)
    assert ("start", REC) in _sysctl(rig)


def test_marker_is_written_before_the_first_stop_and_removed_after(tmp_path):
    rig = Rig()
    rig.r.marker_path = tmp_path / "run" / "compact.json"
    seen = {}
    orig = rig._systemctl
    def spy(verb, unit):
        if verb == "stop" and unit == MET:
            seen["m"] = json.loads(rig.r.marker_path.read_text())
        return orig(verb, unit)
    rig.r.systemctl = spy
    assert c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r) == 0
    import os
    assert seen["m"]["pid"] == os.getpid() and seen["m"]["units"] == [REC, MET]
    assert seen["m"]["timer_was_active"] is True
    assert not rig.r.marker_path.exists()


def test_marker_is_removed_after_a_refusal(tmp_path):
    rig = Rig(holders=((1, None),))
    rig.r.marker_path = tmp_path / "m.json"
    c.compact_client("hf-timestd", "timestd", apply=True, r=rig.r)
    assert not rig.r.marker_path.exists()


# --- the real expire flock -------------------------------------------------

def test_the_real_expire_lock_waits_for_a_running_pass(tmp_path):
    from sigmond.storage import retention
    path = tmp_path / "expire.lock"
    held = retention._open_lock(path, __import__("os").geteuid())
    fcntl.flock(held, fcntl.LOCK_EX)           # a pass is running
    try:
        ticks = iter(range(0, 100))
        lock = c.make_expire_lock(path, wait_s=3, poll_s=1, sleep=lambda s: None,
                                  clock=lambda: next(ticks))
        with pytest.raises(c.CompactError, match="expire pass"):
            with lock():
                pytest.fail("took the lock while a pass held it")
    finally:
        __import__("os").close(held)
    with c.make_expire_lock(path, wait_s=0)():   # free now
        # and a pass started meanwhile finds it held (returns 75)
        fd = retention._open_lock(path, __import__("os").geteuid())
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        __import__("os").close(fd)


# --- the watchdog leaves compaction's stop alone ---------------------------

def test_the_watchdog_does_not_undo_compactions_stop():
    # `systemctl stop` leaves inactive/success: an operator's stop, which holds.
    # The ledger lever is not used, so nothing else is written.
    f = rw.Facts(now_wall=1e9, now_mono=100_000.0,
                 recorder=rw.UnitState("inactive", "success", None, 99_900.0, 0),
                 radiod_started_mono=1_000.0, t6=None,
                 requests=(rw.Request("pipeline-watchdog", "no-output", 1e9 - 10, "x"),),
                 ledger_stopped=False, state={})
    d = rw.decide(f)
    assert d.action == "none" and "operator" in d.reason


# --- the client calls -------------------------------------------------------

def _run(out, rc=0):
    return lambda argv, **kw: SimpleNamespace(returncode=rc, stdout=json.dumps(out))


def test_holders_answer_is_parsed():
    ans = call_compact_holders("/b", "timestd", _run({"holders": [
        {"pid": 5, "unit": REC}, {"pid": 6, "unit": None}]}))
    assert ans.ok and ans.holders == ((5, REC), (6, None))


def test_a_malformed_holders_answer_fails():
    assert not call_compact_holders("/b", "t", _run({"holders": [{"pid": "x"}]})).ok
    assert not call_compact_holders("/b", "t", _run({})).ok


def test_swap_passes_the_target_and_flag():
    seen = []
    def run(argv, **kw):
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout='{"refused": false}')
    assert call_compact_swap("/b", "timestd", run).ok
    assert seen[0] == ["/b", "storage", "compact", "--swap", "--target", "timestd", "--json"]

"""The storage tick: one pass that measures, decides, acts, alarms and writes.

Every outside effect goes through a fake Runners; nothing here touches
/etc, /run, /var or systemctl.
"""
import fcntl
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from sigmond.clients.base import ClientView, DataSink, DiskWrite, InstanceView
from sigmond.storage.clientcall import ClientAnswer
from sigmond.storage.model import Store
from sigmond.storage.tick import Runners, run_tick

GiB = 1 << 30
T0 = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def _paths(tmp_path):
    return {
        "config_path": tmp_path / "storage.toml",
        "state_path": tmp_path / "state.json",
        "status_path": tmp_path / "status.json",
        "lock_path": tmp_path / "storage.lock",
    }


def _view(name, path, managed=True, floor=0):
    if managed:
        iv = InstanceView(name, data_sinks=[
            DataSink("file", path, floor_bytes=floor, classes=("raw",))])
        return ClientView(name, installed=True, contract_version="0.9", instances=[iv])
    iv = InstanceView(name, disk_writes=[DiskWrite(path)])
    return ClientView(name, installed=True, contract_version="0.5", instances=[iv])


def _fake_runners(used_pct, *, trim_ok=True, virt=False,
                  host_report=(None, "missing"), held=40 * GiB,
                  views=None, active=False, stop_result=(True, "stopped a.service"),
                  expired=(), capacity=100 * GiB, recent=10 * GiB, reported=None):
    calls = Counter()
    lines = []
    state = {"used": int(capacity * used_pct / 100), "held": held, "active": active,
             "recent": recent}

    def counted(name, fn):
        def wrapper(*a, **k):
            calls[name] += 1
            return fn(*a, **k)
        return wrapper

    def answer(ok, written=None):
        if ok:
            return ClientAnswer(True, state["held"], {"raw": state["held"]},
                                bytes_written_per_day=written)
        return ClientAnswer(False, None, {}, False, "exit 1")

    r = Runners(
        load_views=counted("load_views", lambda: dict(views) if views is not None
                           else {"a": _view("a", "/data/a")}),
        discover_stores=counted("discover_stores", lambda paths: (
            [Store("root", "/", "guest", capacity, state["used"])], [])),
        path_bytes=counted("path_bytes", lambda paths: state["held"]),
        read_host_report=counted("read_host_report", lambda now: host_report),
        host_report_expected=counted("host_report_expected", lambda cfg: virt),
        call_holdings=counted("call_holdings", lambda b: answer(True, reported)),
        call_trim=counted("call_trim", lambda b, n: answer(trim_ok)),
        call_final=counted("call_final", lambda b, n, p: answer(trim_ok)),
        stop_client=counted("stop_client", lambda c: stop_result),
        run_cleanup=counted("run_cleanup", lambda: []),
        report_unclaimed=counted("report_unclaimed", lambda claimed: []),
        expire_log_levels=counted("expire_log_levels", lambda now: list(expired)),
        sighup=counted("sighup", lambda c: []),
        journal=lines.append,
        binary_for=lambda c: f"/usr/bin/{c}",
        client_active=lambda c: state["active"],
        recent_writes=counted("recent_bytes", lambda paths, now: _pair(
            state["recent"](paths) if callable(state["recent"]) else state["recent"])),
    )
    r.calls = calls
    r.journal_lines = lines
    r.state = state
    return r


def _pair(v):
    """A fake walk's (bytes by mtime, bytes by ctime); a bare number is both."""
    return tuple(v) if isinstance(v, tuple) else (v, v)


def _status(p):
    return json.loads(p["status_path"].read_text())


def _trims(p):
    return json.loads(p["state_path"].read_text())["trims"]


# ---- the brief's tests ------------------------------------------------------

def test_tick_writes_status_and_raises_once(tmp_path):
    r = _fake_runners(used_pct=86)                          # one store, one managed client over target
    assert run_tick(T0, r, **_paths(tmp_path)) == 0
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["worst_stage"] == "TIGHTEN" and r.calls["call_trim"] == 1
    run_tick(T0 + timedelta(minutes=2), r, **_paths(tmp_path))
    assert sum("raise" in l for l in r.journal_lines) == 1


def test_held_lock_skips(tmp_path):
    p = _paths(tmp_path)
    with open(p["lock_path"], "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        assert run_tick(T0, _fake_runners(used_pct=10), **p) == 75


def test_stale_trim_records_never_cause_a_stop(tmp_path):
    p = _paths(tmp_path)
    p["state_path"].write_text(json.dumps({"trims": {"a": {"requested_at": (T0 - timedelta(hours=2)).timestamp(), "ok": False}}}))
    r = _fake_runners(used_pct=96)
    run_tick(T0, r, **p)
    assert r.calls["stop_client"] == 0


def test_failed_answer_then_stop_at_final(tmp_path):
    r = _fake_runners(used_pct=96, trim_ok=False); p = _paths(tmp_path)
    run_tick(T0, r, **p); run_tick(T0 + timedelta(seconds=601), r, **p)
    assert r.calls["stop_client"] == 1
    assert json.loads(p["status_path"].read_text())["clients"][0]["stopped_by_storage"]


def test_an_exception_is_a_loud_fault_not_silence(tmp_path):
    r = _fake_runners(used_pct=10); r.load_views = lambda: 1 / 0
    assert run_tick(T0, r, **_paths(tmp_path)) == 1
    assert json.loads((tmp_path / "status.json").read_text())["worst_stage"] == "FAULT"
    assert any(l.startswith("<2>") for l in r.journal_lines)


def test_bare_metal_has_no_host_alarm(tmp_path):
    r = _fake_runners(used_pct=10, virt=False, host_report=(None, "missing"))
    run_tick(T0, r, **_paths(tmp_path))
    assert not any("host-report" in a["key"] for a in json.loads((tmp_path / "status.json").read_text())["alarms"])


# ---- TrimRecord upkeep (controller ruling) -----------------------------------

def test_an_unanswered_streak_keeps_its_first_request(tmp_path):
    r = _fake_runners(used_pct=86, trim_ok=False); p = _paths(tmp_path)
    for k in range(4):
        run_tick(T0 + timedelta(minutes=2 * k), r, **p)
    rec = _trims(p)["a"]
    assert r.calls["call_trim"] == 4
    assert rec["requested_at"] == T0.timestamp() and rec["ok"] is False


def test_an_ok_answer_ends_the_streak_and_the_next_request_starts_anew(tmp_path):
    r = _fake_runners(used_pct=86, trim_ok=True); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    rec = _trims(p)["a"]
    assert rec["ok"] is True and rec["answered_at"] == T0.timestamp()
    t1 = T0 + timedelta(minutes=2)
    run_tick(t1, r, **p)                    # still over: a new request, a new streak
    assert _trims(p)["a"]["requested_at"] == t1.timestamp()


def test_records_clear_when_the_store_drops_below_tighten(tmp_path):
    r = _fake_runners(used_pct=86, trim_ok=False); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    assert "a" in _trims(p)
    r.state["used"] = 82 * GiB              # WATCH: below TIGHTEN
    run_tick(T0 + timedelta(minutes=2), r, **p)
    assert _trims(p) == {}


def test_records_clear_when_the_client_fits_its_target(tmp_path):
    views = {"a": _view("a", "/data/a", floor=2 * GiB)}
    r = _fake_runners(used_pct=86, trim_ok=False, views=views); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    assert "a" in _trims(p)
    r.state["held"] = 2 * GiB               # store still at TIGHTEN, client down to its floor
    run_tick(T0 + timedelta(minutes=2), r, **p)
    assert _trims(p) == {}


def test_a_new_episode_starts_a_fresh_deadline(tmp_path):
    """A failure in an old episode must not stop the client on the first
    FINAL tick of a new one."""
    r = _fake_runners(used_pct=96, trim_ok=False); p = _paths(tmp_path)
    run_tick(T0, r, **p)                    # fails; streak opens at T0
    r.state["used"] = 50 * GiB
    run_tick(T0 + timedelta(minutes=2), r, **p)     # pressure gone: record cleared
    r.state["used"] = 96 * GiB
    run_tick(T0 + timedelta(minutes=12), r, **p)    # new episode, > 600 s after T0
    assert r.calls["stop_client"] == 0


def test_a_continuous_streak_does_not_go_stale(tmp_path):
    """Records the tick keeps touching survive past STALE_TRIM_S."""
    r = _fake_runners(used_pct=91, trim_ok=False); p = _paths(tmp_path)
    for k in range(0, 40):                  # 80 minutes of DEMAND
        run_tick(T0 + timedelta(minutes=2 * k), r, **p)
    assert _trims(p)["a"]["requested_at"] == T0.timestamp()
    overdue = [l for l in r.journal_lines if "has not answered" in l]
    assert len(overdue) == 1, overdue       # one episode, not one per hour


def test_final_since_set_for_unmanaged_and_cleared(tmp_path):
    views = {"u": _view("u", "/data/u", managed=False)}
    r = _fake_runners(used_pct=96, views=views); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    assert _trims(p)["u"]["final_since"] == T0.timestamp()
    run_tick(T0 + timedelta(seconds=120), r, **p)
    assert _trims(p)["u"]["final_since"] == T0.timestamp()
    run_tick(T0 + timedelta(seconds=1000), r, **p)
    assert r.calls["stop_client"] == 1 and r.calls["call_trim"] == 0


# ---- the stop lever ---------------------------------------------------------

def test_a_stop_raises_stopped_held_until_the_client_runs_again(tmp_path):
    r = _fake_runners(used_pct=96, trim_ok=False); p = _paths(tmp_path)
    run_tick(T0, r, **p); run_tick(T0 + timedelta(seconds=601), r, **p)
    keys = {a["key"]: a for a in _status(p)["alarms"]}
    assert keys["stopped:a"]["severity"] == "CRITICAL"
    r.state["used"] = 50 * GiB               # pressure gone, client still down
    run_tick(T0 + timedelta(seconds=721), r, **p)
    assert "stopped:a" in {a["key"] for a in _status(p)["alarms"]}
    assert r.calls["stop_client"] == 1
    r.state["active"] = True                 # an operator started it
    run_tick(T0 + timedelta(seconds=841), r, **p)
    st = _status(p)
    assert "stopped:a" not in {a["key"] for a in st["alarms"]}
    assert not st["clients"][0]["stopped_by_storage"]
    assert any(l.startswith("<6>storage clear") for l in r.journal_lines)


def test_a_failed_stop_says_so_and_never_says_stopped(tmp_path):
    r = _fake_runners(used_pct=96, trim_ok=False,
                      stop_result=(False, "no units to stop")); p = _paths(tmp_path)
    run_tick(T0, r, **p); run_tick(T0 + timedelta(seconds=601), r, **p)
    st = _status(p)
    keys = {a["key"]: a for a in st["alarms"]}
    assert keys["stop-failed:a"]["severity"] == "CRITICAL"
    assert "no units to stop" in keys["stop-failed:a"]["message"]
    assert "stopped:a" not in keys
    assert not st["clients"][0]["stopped_by_storage"]
    assert not any("stopped:a" in l or "storage stopped" in l for l in r.journal_lines)


def test_hands_off_never_stops(tmp_path):
    p = _paths(tmp_path)
    until = (T0 + timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    p["config_path"].write_text(f'[hands_off]\nuntil = "{until}"\nreason = "test"\n')
    r = _fake_runners(used_pct=96, trim_ok=False)
    run_tick(T0, r, **p); run_tick(T0 + timedelta(seconds=601), r, **p)
    st = _status(p)
    assert r.calls["stop_client"] == 0
    assert st["hands_off"]["reason"] == "test"
    assert "hands-off-held:a" in {a["key"] for a in st["alarms"]}


# ---- the rest of the pass ---------------------------------------------------

def test_cleanup_at_most_once_an_hour(tmp_path):
    r = _fake_runners(used_pct=86); p = _paths(tmp_path)
    for k in range(10):
        run_tick(T0 + timedelta(minutes=2 * k), r, **p)
    assert r.calls["run_cleanup"] == 1
    run_tick(T0 + timedelta(minutes=61), r, **p)
    assert r.calls["run_cleanup"] == 2


def test_unclaimed_report_every_six_hours(tmp_path):
    r = _fake_runners(used_pct=10); p = _paths(tmp_path)
    run_tick(T0, r, **p); run_tick(T0 + timedelta(hours=1), r, **p)
    assert r.calls["report_unclaimed"] == 1
    run_tick(T0 + timedelta(hours=6, minutes=1), r, **p)
    assert r.calls["report_unclaimed"] == 2


def test_unmanaged_holdings_cached_for_half_an_hour(tmp_path):
    views = {"u": _view("u", "/data/u", managed=False)}
    r = _fake_runners(used_pct=10, views=views); p = _paths(tmp_path)
    run_tick(T0, r, **p); run_tick(T0 + timedelta(minutes=10), r, **p)
    assert r.calls["path_bytes"] == 1
    run_tick(T0 + timedelta(minutes=31), r, **p)
    assert r.calls["path_bytes"] == 2


def test_config_problems_become_warnings(tmp_path):
    p = _paths(tmp_path)
    p["config_path"].write_text('[shares]\na = -1\n')
    r = _fake_runners(used_pct=10)
    run_tick(T0, r, **p)
    st = _status(p)
    a = {x["key"]: x for x in st["alarms"]}
    assert a["config:0"]["severity"] == "WARNING" and st["config_problems"]


def test_store_faults_become_errors(tmp_path):
    r = _fake_runners(used_pct=10); p = _paths(tmp_path)
    r.discover_stores = lambda paths: (
        [Store("root", "/", "guest", 100 * GiB, 10 * GiB)],
        ["store mnt:/data (/data) unreadable: [Errno 5] I/O error"])
    run_tick(T0, r, **p)
    a = {x["key"]: x for x in _status(p)["alarms"]}
    assert a["store-missing:/data"]["severity"] == "ERROR"


def test_vm_missing_host_report_alarms(tmp_path):
    r = _fake_runners(used_pct=10, virt=True, host_report=(None, "stale")); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    st = _status(p)
    assert "host-report:stale" in {a["key"] for a in st["alarms"]}
    assert st["host_report"] == {"expected": True, "fault": "stale", "age_s": None}


def test_pool_store_joins_from_the_host_report(tmp_path):
    from sigmond.storage.measure import HostReport
    pool = Store("pve/data", "pve/data", "pool", 1000 * GiB, 960 * GiB)
    rep = HostReport(T0 - timedelta(seconds=30), pool, (100, 101), (), "")
    r = _fake_runners(used_pct=50, virt=True, host_report=(rep, None)); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    st = _status(p)
    stores = {s["name"]: s for s in st["stores"]}
    assert stores["pve/data"]["kind"] == "pool" and stores["pve/data"]["stage"] == "FINAL"
    assert st["host_report"] == {"expected": True, "fault": None, "age_s": 30}
    # the pool's headroom is gone: the guest's allottable shrinks to its holdings
    assert stores["root"]["allottable"] == 40 * GiB


def test_expired_log_levels_sighup_their_client(tmp_path):
    seen = []
    r = _fake_runners(used_pct=10, expired=["A_LOG_LEVEL", "CLIENT_LOG_LEVEL"])
    r.sighup = lambda c: seen.append(c) or []
    run_tick(T0, r, **_paths(tmp_path))
    assert seen == ["a"]


def test_status_shape(tmp_path):
    r = _fake_runners(used_pct=86); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    st = _status(p)
    assert st["schema"] == 1 and st["generated_at"].endswith("Z")
    assert set(st["stores"][0]) >= {"name", "kind", "capacity", "used", "used_pct", "stage", "allottable"}
    assert set(st["clients"][0]) >= {"client", "store", "held", "allotment", "target",
                                     "floor", "managed", "stopped_by_storage"}
    assert set(st["host_report"]) == {"expected", "fault", "age_s"}
    assert st["hands_off"] is None and st["unclaimed"] == [] and st["config_problems"] == []
    for a in st["alarms"]:
        assert set(a) == {"key", "severity", "message", "since"}


def test_unsupported_holdings_verb_leaves_client_unmanaged(tmp_path):
    r = _fake_runners(used_pct=86); p = _paths(tmp_path)
    r.call_holdings = lambda b: ClientAnswer(False, None, {}, False, "verb not supported", True)
    run_tick(T0, r, **p)
    assert _status(p)["clients"][0]["managed"] is False
    assert r.calls["call_trim"] == 0


def test_an_untouched_by_calls_record_stays_fresh_while_the_tick_keeps_it(tmp_path):
    """An unmanaged client gets no calls; its record must not go stale and
    restart its grace while the pressure lasts (hands-off: no stop)."""
    p = _paths(tmp_path)
    until = (T0 + timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    p["config_path"].write_text(f'[hands_off]\nuntil = "{until}"\n')
    r = _fake_runners(used_pct=96, views={"u": _view("u", "/data/u", managed=False)})
    for k in range(40):
        run_tick(T0 + timedelta(minutes=2 * k), r, **p)
    assert _trims(p)["u"]["final_since"] == T0.timestamp()
    assert sum("raise" in l and "hands-off" in l for l in r.journal_lines) == 1


# ---- the files the root tick trusts (security review of a29689b) ------------

import os


def test_a_symlinked_state_file_is_not_followed(tmp_path):
    p = _paths(tmp_path)
    secret = tmp_path / "elsewhere.json"
    forged = {"trims": {"a": {"requested_at": T0.timestamp() - 700, "ok": False,
                              "touched": T0.timestamp()}}}
    secret.write_text(json.dumps(forged))
    p["state_path"].symlink_to(secret)
    r = _fake_runners(used_pct=96, trim_ok=False)
    assert run_tick(T0, r, **p) == 0
    assert r.calls["stop_client"] == 0                  # the forged streak was not read
    a = {x["key"]: x for x in _status(p)["alarms"]}
    assert a["state-untrusted"]["severity"] == "ERROR"
    assert json.loads(secret.read_text()) == forged     # the link's target is untouched
    assert not p["state_path"].is_symlink()             # replaced, never written through


def test_a_state_file_owned_by_another_uid_is_not_trusted(tmp_path):
    p = _paths(tmp_path)
    p["state_path"].write_text(json.dumps({"trims": {"a": {
        "requested_at": T0.timestamp() - 700, "ok": False, "touched": T0.timestamp()}}}))
    from sigmond.storage import tick
    real = tick._state_owner_ok
    tick._state_owner_ok = lambda st, uid: real(st, uid + 1)    # as if another uid wrote it
    try:
        r = _fake_runners(used_pct=96, trim_ok=False)
        assert run_tick(T0, r, **p) == 0
    finally:
        tick._state_owner_ok = real
    assert r.calls["stop_client"] == 0
    assert "state-untrusted" in {x["key"] for x in _status(p)["alarms"]}


def test_the_same_state_owned_by_the_expected_uid_is_trusted(tmp_path):
    """The control for the test above: the forged streak does stop when trusted."""
    p = _paths(tmp_path)
    p["state_path"].write_text(json.dumps({"trims": {"a": {
        "requested_at": T0.timestamp() - 700, "ok": False, "touched": T0.timestamp()}}}))
    r = _fake_runners(used_pct=96, trim_ok=False)
    assert run_tick(T0, r, **p) == 0
    assert r.calls["stop_client"] == 1
    assert "state-untrusted" not in {x["key"] for x in _status(p)["alarms"]}


def test_an_oversized_state_file_is_not_trusted(tmp_path):
    p = _paths(tmp_path)
    p["state_path"].write_text(json.dumps({"pad": "x" * (2 << 20)}))
    r = _fake_runners(used_pct=10)
    run_tick(T0, r, **p)
    assert "state-untrusted" in {x["key"] for x in _status(p)["alarms"]}


def test_state_is_written_0600_and_status_0644(tmp_path):
    p = _paths(tmp_path)
    run_tick(T0, _fake_runners(used_pct=10), **p)
    assert (p["state_path"].stat().st_mode & 0o777) == 0o600
    assert (p["status_path"].stat().st_mode & 0o777) == 0o644


def test_a_symlinked_lock_is_refused_loudly(tmp_path):
    p = _paths(tmp_path)
    (tmp_path / "target").write_text("")
    p["lock_path"].symlink_to(tmp_path / "target")
    r = _fake_runners(used_pct=10)
    assert run_tick(T0, r, **p) == 1
    assert any(l.startswith("<2>") for l in r.journal_lines)
    assert r.calls["load_views"] == 0


def test_a_state_dir_owned_by_another_uid_is_refused(tmp_path):
    d = tmp_path / "storage"
    d.mkdir()
    p = _paths(tmp_path)
    p["state_path"] = d / "state.json"
    # the lock/status dir (tmp_path) and the state dir share our uid; pretend
    # only the state dir is foreign by checking it against another uid.
    from sigmond.storage import tick
    real = tick._trusted_dir
    calls = []

    def fake(path, uid):
        calls.append(path)
        return real(path, uid + 1 if Path(path) == d else uid)
    tick._trusted_dir = fake
    try:
        r = _fake_runners(used_pct=10)
        run_tick(T0, r, **p)
    finally:
        tick._trusted_dir = real
    assert "state-untrusted" in {x["key"] for x in _status(p)["alarms"]}
    assert not p["state_path"].exists()


from pathlib import Path  # noqa: E402


# ---- fix round 1 (review of 19915e6..7c0ce45) --------------------------------

def test_operator_restart_at_final_and_client_answers_ok(tmp_path):
    """The reviewer's probe: a restarted client that now answers ok, with the
    store still at FINAL, is not stopped again."""
    r = _fake_runners(used_pct=96, trim_ok=False); p = _paths(tmp_path)
    run_tick(T0, r, **p); run_tick(T0 + timedelta(seconds=601), r, **p)
    assert r.calls["stop_client"] == 1
    r.state["active"] = True
    r.call_trim = lambda b, n: ClientAnswer(True, 0, {"raw": 0})
    r.call_final = lambda b, n, pol: ClientAnswer(True, 0, {"raw": 0})
    run_tick(T0 + timedelta(seconds=721), r, **p)
    assert r.calls["stop_client"] == 1, "re-stopped a client that just answered ok"


def test_a_restarted_client_gets_a_fresh_deadline(tmp_path):
    """Its old failed streak does not survive the operator's start: still
    failing, it is not re-stopped until a new 600 s pass."""
    r = _fake_runners(used_pct=96, trim_ok=False); p = _paths(tmp_path)
    run_tick(T0, r, **p); run_tick(T0 + timedelta(seconds=601), r, **p)
    assert r.calls["stop_client"] == 1
    r.state["active"] = True
    run_tick(T0 + timedelta(seconds=721), r, **p)
    assert r.calls["stop_client"] == 1
    assert _trims(p)["a"]["requested_at"] == (T0 + timedelta(seconds=721)).timestamp()
    r.state["active"] = False
    run_tick(T0 + timedelta(seconds=721 + 601), r, **p)
    assert r.calls["stop_client"] == 2


def test_a_fault_after_a_stop_keeps_the_stop(tmp_path):
    r = _fake_runners(used_pct=96, trim_ok=False); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    boom = {"armed": False}
    real_stop, real_journal = r.stop_client, r.journal

    def stop(c):
        boom["armed"] = True
        return real_stop(c)

    def journal(line):
        if boom["armed"]:
            boom["armed"] = False
            raise RuntimeError("journal down")
        real_journal(line)
    r.stop_client, r.journal = stop, journal
    assert run_tick(T0 + timedelta(seconds=601), r, **p) == 1
    st = _status(p)
    assert st["worst_stage"] == "FAULT"
    assert "stopped:a" in {a["key"] for a in st["alarms"]}
    assert "a" in json.loads(p["state_path"].read_text())["stopped"]
    r.journal = real_journal
    run_tick(T0 + timedelta(seconds=721), r, **p)    # still down: held, not re-stopped
    assert r.calls["stop_client"] == 1
    assert _status(p)["clients"][0]["stopped_by_storage"]


def test_unmanaged_stop_waits_on_a_fresh_measurement(tmp_path):
    """A cached du may be 30 min old; a client that already freed space is
    re-measured before the stop and spared."""
    views = {"u": _view("u", "/data/u", managed=False)}
    r = _fake_runners(used_pct=96, views=views); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    r.state["held"] = 1 * GiB                           # freed, but the cache says 40 GiB
    run_tick(T0 + timedelta(seconds=1000), r, **p)
    assert r.calls["stop_client"] == 0
    assert r.calls["path_bytes"] == 2


def test_unmanaged_stop_proceeds_when_still_over(tmp_path):
    views = {"u": _view("u", "/data/u", managed=False)}
    r = _fake_runners(used_pct=96, views=views); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    run_tick(T0 + timedelta(seconds=1000), r, **p)
    assert r.calls["stop_client"] == 1


def test_client_active_counts_a_timer(monkeypatch):
    """A timer-driven oneshot reads inactive between runs; its active timer
    means an operator started it again."""
    from types import SimpleNamespace
    from sigmond.storage import tick
    units = [SimpleNamespace(unit="x.service", kind="service", orphaned=False),
             SimpleNamespace(unit="x.timer", kind="timer", orphaned=False),
             SimpleNamespace(unit="x.target", kind="target", orphaned=False)]
    asked = []

    def run(argv, **kw):
        asked.append(argv[-1])
        return SimpleNamespace(returncode=0 if argv[-1] == "x.timer" else 3)
    assert tick._client_active("x", units_of=lambda c: units, run=run) is True
    assert "x.target" not in asked

    def none(argv, **kw):
        return SimpleNamespace(returncode=3)
    assert tick._client_active("x", units_of=lambda c: units, run=none) is False


def test_an_ok_answer_this_pass_spares_the_stop(tmp_path):
    """decide judged the stop from the records at pass start; a client that
    answers ok in this very pass is not stopped on that older news."""
    r = _fake_runners(used_pct=96, trim_ok=False); p = _paths(tmp_path)
    run_tick(T0, r, **p)                                    # fails; streak opens
    r.call_trim = lambda b, n: ClientAnswer(True, 30 * GiB, {"raw": 30 * GiB})
    r.call_final = lambda b, n, pol: ClientAnswer(True, 20 * GiB, {"raw": 20 * GiB})
    run_tick(T0 + timedelta(seconds=601), r, **p)
    assert r.calls["stop_client"] == 0


# ---- days of coverage (Task 13) ---------------------------------------------

def test_status_reports_days_and_each_clients_days_held(tmp_path):
    p = _paths(tmp_path)
    run_tick(T0, _fake_runners(used_pct=50), **p)          # a holds 40 GiB, writes 10 GiB/day
    st = _status(p)
    (store,) = st["stores"]
    (c,) = st["clients"]
    # allottable 100 - 1 reserve - 5 headroom - 10 unregistered = 84 GiB, over 10 GiB/day
    assert store["days"] == 8.4
    assert c["rate_bytes_per_day"] == 10 * GiB and c["rate_source"] == "measured"
    assert c["days_held"] == 4.0


def test_a_reported_rate_beats_the_measured_one(tmp_path):
    p = _paths(tmp_path)
    run_tick(T0, _fake_runners(used_pct=50, reported=20 * GiB), **p)
    (c,) = _status(p)["clients"]
    assert c["rate_source"] == "reported" and c["days_held"] == 2.0


def test_no_rate_shows_none_and_floor_only(tmp_path):
    p = _paths(tmp_path)
    r = _fake_runners(used_pct=50, views={"a": _view("a", "/data/a", floor=2 * GiB)})
    r.recent_writes = None
    run_tick(T0, r, **p)
    st = _status(p)
    (c,) = st["clients"]
    assert c["rate_source"] == "none" and c["rate_bytes_per_day"] == 0
    assert c["days_held"] is None and c["allotment"] == 2 * GiB
    assert st["stores"][0]["days"] is None


def test_two_clients_split_in_days_not_bytes(tmp_path):
    p = _paths(tmp_path)
    views = {"a": _view("a", "/data/a"), "b": _view("b", "/data/b")}
    rates = {"/data/a": 20 * GiB, "/data/b": 4 * GiB}
    r = _fake_runners(used_pct=50, views=views, held=10 * GiB,
                      recent=lambda paths: rates[paths[0]])
    run_tick(T0, r, **p)
    st = _status(p)
    allot = {c["client"]: c["allotment"] for c in st["clients"]}
    # used 50, held 20 -> 30 unregistered; allottable 100 - 1 - 5 - 30 = 64 GiB over 24 GiB/day
    assert st["stores"][0]["days"] == round(64 / 24, 3)
    assert allot["a"] == 5 * allot["b"] or abs(allot["a"] - 5 * allot["b"]) <= 5


def test_recent_bytes_cached_for_half_an_hour(tmp_path):
    r = _fake_runners(used_pct=10); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    run_tick(T0 + timedelta(minutes=2), r, **p)
    assert r.calls["recent_bytes"] == 1
    run_tick(T0 + timedelta(minutes=31), r, **p)
    assert r.calls["recent_bytes"] == 2


def test_a_reported_rate_needs_no_walk(tmp_path):
    r = _fake_runners(used_pct=10, reported=GiB); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    assert r.calls["recent_bytes"] == 0


def test_fast_churn_raises_watch_early_and_the_horizon_is_configurable(tmp_path):
    p = _paths(tmp_path)
    r = _fake_runners(used_pct=40)
    for k in range(13):                         # 2 hours, 4 GiB/h: 54 GiB free -> ~13 h
        r.state["used"] = int(40 * GiB + k * GiB * 4 / 6)
        run_tick(T0 + timedelta(minutes=10 * k), r, **p)
    st = _status(p)
    assert st["stores"][0]["stage"] == "WATCH" and st["stores"][0]["used_pct"] < 80
    assert st["stores"][0]["churn_bytes_per_h"] == round(4 * GiB, -3) or \
        abs(st["stores"][0]["churn_bytes_per_h"] - 4 * GiB) < 1e6
    p["config_path"].write_text("[levels]\nhorizon_h = 6\n")
    run_tick(T0 + timedelta(minutes=130), r, **p)
    assert _status(p)["stores"][0]["stage"] == "OK"


def test_churn_waits_for_an_hour_of_history(tmp_path):
    p = _paths(tmp_path)
    r = _fake_runners(used_pct=40)
    for k in range(5):                          # 40 minutes only
        r.state["used"] = int(40 * GiB + k * 5 * GiB)
        run_tick(T0 + timedelta(minutes=10 * k), r, **p)
    assert _status(p)["stores"][0]["stage"] == "OK"


# ---- the stop lever spares an idle client (controller ruling on concern 1) --

def _alarm_keys(p):
    return {a["key"]: a for a in _status(p)["alarms"]}


def test_an_idle_unmanaged_client_over_target_at_final_is_not_stopped(tmp_path):
    views = {"u": _view("u", "/data/u", managed=False)}
    r = _fake_runners(used_pct=96, views=views, recent=0); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    before = r.calls["recent_bytes"]
    run_tick(T0 + timedelta(seconds=1000), r, **p)
    assert r.calls["stop_client"] == 0
    assert r.calls["recent_bytes"] > before          # measured fresh just before the stop
    al = _alarm_keys(p)["idle-over:u"]
    assert al["severity"] == "WARNING"
    assert "wrote nothing in 24 h" in al["message"] and str(40 * GiB) in al["message"]
    assert "stopped:u" not in _alarm_keys(p)


def test_an_idle_client_that_writes_again_is_stopped_as_before(tmp_path):
    views = {"u": _view("u", "/data/u", managed=False)}
    r = _fake_runners(used_pct=96, views=views, recent=0); p = _paths(tmp_path)
    run_tick(T0, r, **p)                              # cached recent = 0
    r.state["recent"] = GiB                           # it writes again; the fresh walk sees it
    run_tick(T0 + timedelta(seconds=1000), r, **p)
    assert r.calls["stop_client"] == 1 and "idle-over:u" not in _alarm_keys(p)


def test_a_writing_unmanaged_client_is_stopped_as_before(tmp_path):
    views = {"u": _view("u", "/data/u", managed=False)}
    r = _fake_runners(used_pct=96, views=views, recent=GiB); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    run_tick(T0 + timedelta(seconds=1000), r, **p)
    assert r.calls["stop_client"] == 1


def test_an_idle_managed_client_with_no_earlier_du_keeps_the_stop(tmp_path):
    # It reports its own holdings, so no du stands to show it has not grown:
    # the guard has nothing to judge by, and the stop proceeds as before.
    r = _fake_runners(used_pct=96, trim_ok=False, recent=0); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    run_tick(T0 + timedelta(seconds=601), r, **p)
    assert r.calls["call_trim"] == 2 and r.calls["call_final"] == 2
    assert r.calls["stop_client"] == 1 and "idle-over:a" not in _alarm_keys(p)


def test_an_idle_managed_client_whose_du_holds_still_is_spared(tmp_path):
    # Its holdings verb fails, so the tick measures it with du (cached).
    r = _fake_runners(used_pct=96, trim_ok=False, recent=0); p = _paths(tmp_path)
    r.call_holdings = lambda b: ClientAnswer(False, None, {}, False, "exit 1")
    run_tick(T0, r, **p)
    run_tick(T0 + timedelta(seconds=601), r, **p)
    assert r.calls["call_trim"] == 2 and r.calls["call_final"] == 2
    assert r.calls["stop_client"] == 0 and "idle-over:a" in _alarm_keys(p)


def test_no_recent_writes_but_growing_du_is_stopped(tmp_path):
    # Zero files changed by the walk's count, yet the du grew: not idle.
    views = {"u": _view("u", "/data/u", managed=False)}
    r = _fake_runners(used_pct=96, views=views, recent=0); p = _paths(tmp_path)
    run_tick(T0, r, **p)                              # du cached at 40 GiB
    r.state["held"] = 45 * GiB
    run_tick(T0 + timedelta(seconds=1000), r, **p)
    assert r.calls["stop_client"] == 1 and "idle-over:u" not in _alarm_keys(p)


def test_never_measured_recent_bytes_keeps_todays_stop(tmp_path):
    # A reported rate skips the walk: no measurement, so the stop proceeds.
    r = _fake_runners(used_pct=96, trim_ok=False, recent=0, reported=0); p = _paths(tmp_path)
    r.call_trim = lambda b, n: ClientAnswer(False, None, {}, False, "exit 1")
    r.call_final = lambda b, n, pol: ClientAnswer(False, None, {}, False, "exit 1")
    run_tick(T0, r, **p)
    run_tick(T0 + timedelta(seconds=601), r, **p)
    assert r.calls["recent_bytes"] == 0 and r.calls["stop_client"] == 1


# ---- fix round 1: mtime for the rate, ctime for the idle check ------------

def test_backdated_mtimes_with_fresh_ctimes_are_not_idle(tmp_path):
    views = {"u": _view("u", "/data/u", managed=False)}
    r = _fake_runners(used_pct=96, views=views, recent=(0, GiB)); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    run_tick(T0 + timedelta(seconds=1000), r, **p)
    assert r.calls["stop_client"] == 1 and "idle-over:u" not in _alarm_keys(p)


def test_ctime_only_changes_never_raise_the_allotment_rate(tmp_path):
    # A rename or chown moves ctime: the idle check sees it, the rate does not.
    p = _paths(tmp_path)
    run_tick(T0, _fake_runners(used_pct=50, recent=(0, 5 * GiB)), **p)
    (c,) = _status(p)["clients"]
    assert c["rate_bytes_per_day"] == 0 and c["rate_source"] == "measured"


def test_the_idle_check_compares_with_the_du_from_before_this_pass(tmp_path):
    # The du cache expires on the stop tick, so holdings re-measure 45 GiB this
    # pass; the earlier 40 GiB is the one to compare: it grew, so it is stopped.
    views = {"u": _view("u", "/data/u", managed=False)}
    r = _fake_runners(used_pct=96, views=views, recent=0); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    r.state["held"] = 45 * GiB
    run_tick(T0 + timedelta(seconds=1900), r, **p)
    assert r.calls["stop_client"] == 1 and "idle-over:u" not in _alarm_keys(p)


def test_a_clock_stepped_back_expires_both_caches(tmp_path):
    views = {"u": _view("u", "/data/u", managed=False)}
    r = _fake_runners(used_pct=10, views=views); p = _paths(tmp_path)
    run_tick(T0, r, **p)
    run_tick(T0 - timedelta(minutes=10), r, **p)
    assert r.calls["path_bytes"] == 2 and r.calls["recent_bytes"] == 2

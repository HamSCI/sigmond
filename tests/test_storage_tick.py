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
                  expired=(), capacity=100 * GiB):
    calls = Counter()
    lines = []
    state = {"used": int(capacity * used_pct / 100), "held": held, "active": active}

    def counted(name, fn):
        def wrapper(*a, **k):
            calls[name] += 1
            return fn(*a, **k)
        return wrapper

    def answer(ok):
        if ok:
            return ClientAnswer(True, state["held"], {"raw": state["held"]})
        return ClientAnswer(False, None, {}, False, "exit 1")

    r = Runners(
        load_views=counted("load_views", lambda: dict(views) if views is not None
                           else {"a": _view("a", "/data/a")}),
        discover_stores=counted("discover_stores", lambda paths: (
            [Store("root", "/", "guest", capacity, state["used"])], [])),
        path_bytes=counted("path_bytes", lambda paths: state["held"]),
        read_host_report=counted("read_host_report", lambda now: host_report),
        host_report_expected=counted("host_report_expected", lambda cfg: virt),
        call_holdings=counted("call_holdings", lambda b: answer(True)),
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
    )
    r.calls = calls
    r.journal_lines = lines
    r.state = state
    return r


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

"""The ledger's retention pass: plan the jobs, ask each client to expire its own
data, resume where the budget stopped.  No outside effect: fakes only."""
import json
from datetime import datetime, timedelta, timezone

from sigmond.clients.base import ClientView, DataSink, InstanceView
from sigmond.storage import retention
from sigmond.storage.clientcall import ExpireAnswer

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def _sink(kind, path, days, expires):
    return DataSink(kind, path, retention_days=days, expires=expires)


def _views(spec):
    return {n: ClientView(client_type=n, installed=True, contract_version="0.9",
                          instances=[InstanceView(instance="default", data_sinks=list(s))])
            for n, s in spec.items()}


def test_plan_takes_declared_days_for_sinks_that_expire():
    views = _views({"hf-timestd": [_sink("service", "timestd.L2_tick_phase", 30, True),
                                   _sink("file", "/var/lib/timestd/raw_buffer", 0, False)]})
    assert retention.plan_jobs(views, {}) == [
        retention.ExpireJob("hf-timestd", "timestd.L2_tick_phase", 30, "declared")]


def test_storage_toml_overrides_and_zero_disables():
    views = _views({"hf-timestd": [_sink("service", "timestd.L2_tick_phase", 30, True),
                                   _sink("service", "timestd.L1_all_arrivals", 30, True)]})
    jobs = retention.plan_jobs(views, {"hf-timestd:timestd.L2_tick_phase": 14,
                                       "hf-timestd:timestd.L1_all_arrivals": 0})
    assert jobs == [retention.ExpireJob("hf-timestd", "timestd.L2_tick_phase", 14, "storage.toml")]


def test_a_sink_without_expires_is_never_called():
    views = _views({"psk-recorder": [_sink("file", "/var/log/psk/spots.log", 14, False)]})
    assert retention.plan_jobs(views, {"psk-recorder:/var/log/psk/spots.log": 5}) == []


def test_jobs_sort_by_client_then_target():
    views = _views({"b": [_sink("service", "z", 1, True), _sink("service", "a", 1, True)],
                    "a": [_sink("service", "q", 1, True)]})
    assert [(j.client, j.target) for j in retention.plan_jobs(views, {})] == [
        ("a", "q"), ("b", "a"), ("b", "z")]


class Fake:
    def __init__(self, tmp_path, views, answers=None, binary="/bin/x"):
        self.t = 0.0
        self.calls, self.lines = [], []
        self.answers = answers or {}
        self.runners = retention.ExpireRunners(
            load_views=lambda: views,
            binary_for=lambda c: binary,
            call_expire=self._call,
            journal=self.lines.append,
            clock=lambda: self.t)
        self.kw = dict(config_path=tmp_path / "storage.toml",
                       state_path=tmp_path / "state.json",
                       status_path=tmp_path / "status.json",
                       lock_path=tmp_path / "expire.lock")
        self.tmp = tmp_path

    def _call(self, binary, target, days, max_seconds):
        self.calls.append((target, days, max_seconds))
        self.t += 1000.0
        a = self.answers.get(target, ExpireAnswer(True, 0, True))
        return a(self) if callable(a) else a

    def run(self, now=T0, **extra):
        return retention.run_expire_pass(now, self.runners, **{**self.kw, **extra})

    def status(self):
        return json.loads((self.tmp / "status.json").read_text())


def _three():
    return _views({"c": [_sink("service", n, 30, True) for n in ("t1", "t2", "t3")]})


def test_pass_runs_jobs_in_turn_and_resumes_where_it_stopped(tmp_path):
    f = Fake(tmp_path, _three())
    for _ in range(4):
        f.t = 0.0
        f.run(budget_s=900, per_job_s=600)
    assert [c[0] for c in f.calls] == ["t1", "t2", "t3", "t1"]


def test_each_job_gets_the_smaller_of_per_job_and_what_is_left(tmp_path):
    f = Fake(tmp_path, _three())
    f.run(budget_s=1500, per_job_s=600)
    assert [c[2] for c in f.calls] == [600, 500]


def test_pass_journals_each_deletion_and_writes_status(tmp_path):
    f = Fake(tmp_path, _three(), answers={
        "t1": ExpireAnswer(True, 42, True),
        "t2": ExpireAnswer(True, 0, True),
        "t3": ExpireAnswer(False, reason="exit 1")})
    f.run(budget_s=10**6)
    assert len(f.lines) == 2
    assert "t1" in f.lines[0] and "42" in f.lines[0]
    assert f.lines[1].startswith("<4>storage expire:") and "t3" in f.lines[1] and "exit 1" in f.lines[1]
    st = f.status()
    assert st["schema"] == 1
    jobs = {j["target"]: j for j in st["jobs"]}
    assert set(jobs) == {"t1", "t2", "t3"}
    assert jobs["t1"]["deleted"] == 42 and jobs["t1"]["complete"] is True
    assert jobs["t1"]["days"] == 30 and jobs["t1"]["source"] == "declared"
    assert jobs["t1"]["last_run_utc"] == "2026-11-26T12:00:00Z"
    assert jobs["t3"]["reason"] == "exit 1"


def test_status_keeps_earlier_results_for_jobs_not_reached_this_pass(tmp_path):
    f = Fake(tmp_path, _three(), answers={"t1": ExpireAnswer(True, 7, True)})
    f.t = 0.0
    f.run(budget_s=900)
    f.t = 0.0
    f.run(now=T0 + timedelta(hours=1), budget_s=900)
    jobs = {j["target"]: j for j in f.status()["jobs"]}
    assert jobs["t1"]["deleted"] == 7 and jobs["t1"]["last_run_utc"] == "2026-11-26T12:00:00Z"
    assert jobs["t2"]["last_run_utc"] == "2026-11-26T13:00:00Z"
    assert jobs["t3"]["last_run_utc"] is None


def test_a_job_incomplete_for_seven_days_raises_one_warning(tmp_path):
    views = _views({"c": [_sink("service", "t1", 30, True)]})
    f = Fake(tmp_path, views, answers={"t1": ExpireAnswer(True, 5, False, reason="deadline")})
    f.run(now=T0)
    f.run(now=T0 + timedelta(days=6, hours=23))
    assert not any("incomplete" in l for l in f.lines)
    f.run(now=T0 + timedelta(days=7, hours=1))
    warns = [l for l in f.lines if l.startswith("<4>storage expire:") and "incomplete" in l]
    assert len(warns) == 1
    f.run(now=T0 + timedelta(days=8))
    assert len([l for l in f.lines if "incomplete for" in l]) == 1
    f.answers["t1"] = ExpireAnswer(True, 1, True)
    f.run(now=T0 + timedelta(days=9))
    f.answers["t1"] = ExpireAnswer(True, 1, False)
    f.run(now=T0 + timedelta(days=10))
    f.run(now=T0 + timedelta(days=18))     # a fresh episode warns again after 7 d
    assert len([l for l in f.lines if "incomplete for" in l]) == 2


def test_an_unsupported_client_is_skipped_for_a_day(tmp_path):
    views = _views({"c": [_sink("service", "t1", 30, True)]})
    f = Fake(tmp_path, views, answers={"t1": ExpireAnswer(False, reason="verb not supported (older client)",
                                                          unsupported=True)})
    f.run(now=T0)
    f.run(now=T0 + timedelta(hours=23))
    assert len(f.calls) == 1
    f.run(now=T0 + timedelta(hours=25))
    assert len(f.calls) == 2


def test_a_client_with_no_binary_is_not_called(tmp_path):
    f = Fake(tmp_path, _views({"c": [_sink("service", "t1", 30, True)]}), binary=None)
    assert f.run() == 0
    assert f.calls == []
    assert "no binary" in f.status()["jobs"][0]["reason"]


def test_another_pass_holding_the_lock_returns_75(tmp_path):
    import fcntl
    f = Fake(tmp_path, _three())
    fd = retention._open_lock(f.kw["lock_path"], __import__("os").geteuid())
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert f.run() == 75
        assert f.calls == []
    finally:
        __import__("os").close(fd)


def test_the_expire_lock_is_not_the_ledger_lock():
    from sigmond.storage import tick
    assert retention.EXPIRE_LOCK != tick.LOCK_PATH
    assert str(retention.EXPIRE_LOCK) == "/run/sigmond/storage-expire.lock"
    assert str(retention.EXPIRE_STATE) == "/var/lib/sigmond/storage/expire-state.json"
    assert str(retention.EXPIRE_STATUS) == "/run/sigmond/storage-expire.json"


def test_dry_run_plan_lines(tmp_path):
    views = _views({"c": [_sink("service", "t1", 30, True)]})
    lines = retention.plan_lines(views, {"c:t1": 10})
    assert lines == ["c t1: 10 days (storage.toml)"]


def test_the_units_name_the_expire_pass():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "systemd"
    svc = (root / "sigmond-storage-expire.service").read_text()
    tmr = (root / "sigmond-storage-expire.timer").read_text()
    assert "ExecStart=/usr/local/bin/smd storage expire" in svc
    assert "IOSchedulingClass=idle" in svc and "Nice=10" in svc and "TimeoutStartSec=1800" in svc
    assert "OnBootSec=900" in tmr and "OnCalendar=hourly" in tmr and "RandomizedDelaySec=300" in tmr

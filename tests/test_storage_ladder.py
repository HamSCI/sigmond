import pytest

from sigmond.storage.ladder import (
    DEMAND_DEADLINE_S, UNMANAGED_FINAL_GRACE_S, Action, Severity, TrimRecord,
    decide, store_stage, tightened_targets,
)
from sigmond.storage.model import Allotments, Demand, Holding, Levels, Registration, Stage, Store

GiB = 1 << 30


def _decide(*, used_pct, managed, trims=None, directives=None, now=0.0,
            hands_off=False, floors_fit=True, host_fault=None,
            allotment=40 * GiB, floor=0, holding_gib=None):
    """One store `root` of 100 GiB; client `a` holds all but 1 GiB of the used space."""
    used = used_pct * GiB
    held = (used_pct - 1) * GiB if holding_gib is None else holding_gib * GiB
    return decide(
        stores={"root": Store("root", "/", "guest", 100 * GiB, used)},
        allotments={"root": Allotments({"a": allotment}, floors_fit, 90 * GiB)},
        holdings={("a", "root"): Holding("a", "root", {"raw": held}, False)},
        registrations=[Registration("a", "root", ("/var/a",), floor, None, managed)],
        levels=Levels(),
        trims=trims or {},
        directives=directives or {},
        hands_off=hands_off,
        host_fault=host_fault,
        now=now,
        demands={"root": [Demand("a", floor, 0.0)]},     # rate 0: targets fall to floors
    )


def _stops(d):
    return [a for a in d.actions if a.kind == "stop"]


# --- the brief's tests -------------------------------------------------------

def test_constants_and_severity_values():
    assert DEMAND_DEADLINE_S == 600 and UNMANAGED_FINAL_GRACE_S == 900
    assert (Severity.WARNING, Severity.ERROR, Severity.CRITICAL) == (4, 3, 2)


def test_stage_thresholds():
    lv = Levels()
    assert [store_stage(Store("r", "/", "guest", 100, u), lv) for u in (79, 80, 85, 90, 95)] == \
        [Stage.OK, Stage.WATCH, Stage.TIGHTEN, Stage.DEMAND, Stage.FINAL]


def test_tightened_targets_reach_the_watch_level_and_respect_floors():
    s = Store("root", "/", "guest", 100 * GiB, 86 * GiB)          # R = 6 GiB
    t = tightened_targets(s, Levels(), {"a": 40 * GiB, "b": 20 * GiB},
                          [Demand("a", 30 * GiB, 10 * GiB), Demand("b", 0, 10 * GiB)],
                          {"a": 50 * GiB, "b": 50 * GiB})
    # Task 13: the targets divide the space at WATCH (60 - 6 = 54 GiB) in equal
    # days: 24 GiB above floors over 20 GiB/day -> D = 1.2 -> a 42, b 12 GiB.
    # a already holds fewer days than D, so b alone trims, by 8 GiB (>= R).
    assert t["a"] >= 30 * GiB and t["a"] + t["b"] == 54 * GiB
    assert t == {"a": 42 * GiB, "b": 12 * GiB}
    after = min(40 * GiB, t["a"]) + min(20 * GiB, t["b"])
    assert (40 * GiB + 20 * GiB) - after >= 6 * GiB


def test_tighten_asks_managed_clients_to_trim_and_clears_clutter():
    d = _decide(used_pct=86, managed=True)
    assert {a.kind for a in d.actions} == {"trim", "cleanup"}


def test_final_with_a_directive_passes_it():
    d = _decide(used_pct=96, managed=True, directives={"a": "stop-writing"})
    assert any(a.kind == "final" and a.client == "a" and a.policy == "stop-writing" for a in d.actions)


def test_unanswered_client_gets_stopped_at_final_only():
    rec = {"a": TrimRecord(requested_at=0.0, ok=False)}
    assert not any(a.kind == "stop" for a in _decide(used_pct=91, managed=True, trims=rec, now=700).actions)
    assert any(a.kind == "stop" and a.client == "a" for a in _decide(used_pct=96, managed=True, trims=rec, now=700).actions)


def test_a_refusal_is_an_answer_not_a_failure():
    rec = {"a": TrimRecord(requested_at=0.0, answered_at=5.0, ok=True)}
    assert not any(a.kind == "stop" for a in _decide(used_pct=96, managed=True, trims=rec, now=700).actions)


def test_unmanaged_gets_grace_then_stop():
    early = {"a": TrimRecord(requested_at=0.0, final_since=0.0)}
    assert not any(a.kind == "stop" for a in _decide(used_pct=96, managed=False, trims=early, now=899).actions)
    assert any(a.kind == "stop" for a in _decide(used_pct=96, managed=False, trims=early, now=901).actions)


def test_hands_off_turns_a_stop_into_a_critical_alarm():
    rec = {"a": TrimRecord(requested_at=0.0, ok=False)}
    d = _decide(used_pct=96, managed=True, trims=rec, now=700, hands_off=True)
    assert not any(a.kind == "stop" for a in d.actions) and d.alarms["hands-off-held:a"][0] == Severity.CRITICAL


def test_floors_that_no_longer_fit_go_straight_to_final():
    d = _decide(used_pct=50, managed=True, floors_fit=False)
    assert d.stages["root"] == Stage.FINAL and "floors:root" in d.alarms


def test_host_fault_alarms():
    assert "host-report:stale" in _decide(used_pct=10, managed=True, host_fault="stale").alarms


def test_watch_alarm_names_the_biggest_holders():
    msg = _decide(used_pct=81, managed=True).alarms["store:root"][1]
    assert "a" in msg and "81" in msg


# --- controller rulings and edge cases ---------------------------------------

def test_ruling_a_an_unmanaged_unanswered_record_alone_never_stops():
    # A record with ok=False, far older than the demand deadline, but no final_since.
    rec = {"a": TrimRecord(requested_at=0.0, ok=False)}
    d = _decide(used_pct=96, managed=False, trims=rec, now=10_000)
    assert not _stops(d)
    assert "demand-overdue:a" not in d.alarms


def test_ruling_a_unmanaged_within_target_is_not_stopped_after_grace():
    # Holding 10 GiB at a 10 GiB floor: the target cannot drop below 10 GiB.
    rec = {"a": TrimRecord(requested_at=0.0, final_since=0.0)}
    d = _decide(used_pct=96, managed=False, trims=rec, now=10_000, holding_gib=10, floor=10 * GiB)
    assert d.targets["root"]["a"] == 10 * GiB
    assert not _stops(d)


def test_ruling_a_unmanaged_gets_no_trim_or_final_calls():
    d = _decide(used_pct=96, managed=False)
    assert {a.kind for a in d.actions} == {"cleanup"}


def test_ruling_b_the_deadline_counts_from_the_first_request_of_the_streak():
    # requested_at marks the streak's start; 601 s after it the client is overdue,
    # whatever happened in between.
    rec = {"a": TrimRecord(requested_at=100.0, ok=False)}
    assert "demand-overdue:a" not in _decide(used_pct=91, managed=True, trims=rec, now=700).alarms
    assert "demand-overdue:a" in _decide(used_pct=91, managed=True, trims=rec, now=701).alarms
    assert not _stops(_decide(used_pct=96, managed=True, trims=rec, now=700))
    assert _stops(_decide(used_pct=96, managed=True, trims=rec, now=701))


def test_decide_does_not_mutate_trim_records():
    rec = TrimRecord(requested_at=0.0, ok=False)
    _decide(used_pct=96, managed=True, trims={"a": rec}, now=700)
    assert rec == TrimRecord(requested_at=0.0, ok=False)


def test_answered_but_failed_counts_as_a_failure():
    rec = {"a": TrimRecord(requested_at=0.0, answered_at=30.0, ok=False)}
    assert any(a.client == "a" for a in _stops(_decide(used_pct=96, managed=True, trims=rec, now=700)))
    d = _decide(used_pct=91, managed=True, trims=rec, now=700)
    assert d.alarms["demand-overdue:a"][0] == Severity.ERROR


def test_a_refusal_never_raises_demand_overdue():
    rec = {"a": TrimRecord(requested_at=0.0, answered_at=5.0, ok=True)}
    assert "demand-overdue:a" not in _decide(used_pct=91, managed=True, trims=rec, now=10_000).alarms


def test_demand_overdue_only_from_demand_upward():
    rec = {"a": TrimRecord(requested_at=0.0, ok=False)}
    assert "demand-overdue:a" not in _decide(used_pct=86, managed=True, trims=rec, now=700).alarms
    assert "demand-overdue:a" in _decide(used_pct=96, managed=True, trims=rec, now=700).alarms


def test_a_fresh_request_is_not_yet_a_failure():
    rec = {"a": TrimRecord(requested_at=0.0, ok=False)}
    d = _decide(used_pct=96, managed=True, trims=rec, now=600)
    assert not _stops(d) and "demand-overdue:a" not in d.alarms


def test_no_record_means_no_stop():
    assert not _stops(_decide(used_pct=99, managed=True, now=10_000))


def test_below_tighten_there_are_no_actions():
    assert _decide(used_pct=81, managed=True).actions == []
    assert _decide(used_pct=10, managed=True).actions == []


def test_store_alarm_severity_by_stage():
    sev = {p: _decide(used_pct=p, managed=True).alarms["store:root"][0] for p in (81, 86, 91, 96)}
    assert sev == {81: Severity.WARNING, 86: Severity.WARNING, 91: Severity.ERROR, 96: Severity.CRITICAL}
    assert "store:root" not in _decide(used_pct=79, managed=True).alarms


def test_floors_alarm_is_critical():
    assert _decide(used_pct=50, managed=True, floors_fit=False).alarms["floors:root"][0] == Severity.CRITICAL


def test_client_watch_fires_at_every_stage():
    # 37 of 40 GiB = 92.5 % > 90 %, store at 10 %.
    d = _decide(used_pct=10, managed=True, holding_gib=37)
    assert d.alarms["client-watch:a"][0] == Severity.WARNING
    assert "client-watch:a" not in _decide(used_pct=10, managed=True, holding_gib=35).alarms


def test_trim_carries_the_target_as_fit_bytes():
    d = _decide(used_pct=86, managed=True)
    (trim,) = [a for a in d.actions if a.kind == "trim"]
    assert trim.store == "root" and trim.fit_bytes == d.targets["root"]["a"]
    assert d.targets["root"]["a"] <= 40 * GiB


def test_final_without_a_directive_has_no_policy():
    d = _decide(used_pct=96, managed=True)
    (final,) = [a for a in d.actions if a.kind == "final"]
    assert final.policy is None and final.client == "a"


def test_pool_store_gets_stages_and_alarms_but_no_actions():
    pool = Store("pool", "pve/data", "pool", 100 * GiB, 96 * GiB)
    rec = {"a": TrimRecord(requested_at=0.0, ok=False)}
    d = decide(
        stores={"pool": pool},
        allotments={"pool": Allotments({"a": 10 * GiB}, True, 50 * GiB)},
        holdings={("a", "pool"): Holding("a", "pool", {"raw": 90 * GiB}, False)},
        registrations=[Registration("a", "pool", ("/x",), 0, None, True)],
        levels=Levels(), trims=rec, directives={}, hands_off=False, host_fault=None, now=10_000,
        demands={"pool": [Demand("a", 0, 0.0)]},
    )
    assert d.stages["pool"] == Stage.FINAL
    assert d.alarms["store:pool"][0] == Severity.CRITICAL
    assert d.actions == []
    assert not any(k.startswith(("demand-overdue:", "hands-off-held:")) for k in d.alarms)


def test_actions_come_out_in_a_stable_order():
    stores = {n: Store(n, "/" + n, "guest", 100 * GiB, 96 * GiB) for n in ("zeta", "alpha")}
    allot = {n: Allotments({"b": 10 * GiB, "a": 10 * GiB}, True, 50 * GiB) for n in stores}
    hold = {(c, n): Holding(c, n, {"raw": 40 * GiB}, False) for n in stores for c in ("b", "a")}
    regs = [Registration(c, n, ("/x",), 0, None, True) for n in ("zeta", "alpha") for c in ("b", "a")]
    d = decide(stores=stores, allotments=allot, holdings=hold, registrations=regs, levels=Levels(),
               trims={}, directives={}, hands_off=False, host_fault=None, now=0.0,
               demands={n: [Demand(c, 0, 0.0) for c in "ab"] for n in stores})
    assert [(a.store, a.kind, a.client) for a in d.actions] == [
        ("alpha", "cleanup", None),
        ("alpha", "trim", "a"), ("alpha", "final", "a"),
        ("alpha", "trim", "b"), ("alpha", "final", "b"),
        ("zeta", "cleanup", None),
        ("zeta", "trim", "a"), ("zeta", "final", "a"),
        ("zeta", "trim", "b"), ("zeta", "final", "b"),
    ]
    assert list(d.stages) == ["alpha", "zeta"]


def test_a_client_on_two_final_stores_is_stopped_once():
    stores = {n: Store(n, "/" + n, "guest", 100 * GiB, 96 * GiB) for n in ("s1", "s2")}
    allot = {n: Allotments({"a": 10 * GiB}, True, 50 * GiB) for n in stores}
    hold = {("a", n): Holding("a", n, {"raw": 90 * GiB}, False) for n in stores}
    regs = [Registration("a", n, ("/x",), 0, None, True) for n in stores]
    d = decide(stores=stores, allotments=allot, holdings=hold, registrations=regs, levels=Levels(),
               trims={"a": TrimRecord(requested_at=0.0)}, directives={}, hands_off=False,
               host_fault=None, now=700, demands={n: [Demand("a", 0, 0.0)] for n in stores})
    assert [(a.store, a.client) for a in _stops(d)] == [("s1", "a")]


def test_tightened_targets_never_go_below_floor_when_reduction_exceeds_excess():
    s = Store("root", "/", "guest", 100 * GiB, 99 * GiB)          # R = 19 GiB
    t = tightened_targets(s, Levels(), {"a": 40 * GiB, "b": 20 * GiB},
                          [Demand("a", 35 * GiB, GiB), Demand("b", 15 * GiB, GiB)],
                          {"a": 50 * GiB, "b": 50 * GiB})
    assert t == {"a": 35 * GiB, "b": 15 * GiB}


def test_tightened_targets_are_capped_by_the_allotment():
    s = Store("root", "/", "guest", 100 * GiB, 81 * GiB)          # R = 1 GiB
    t = tightened_targets(s, Levels(), {"a": 40 * GiB}, [Demand("a", 0, GiB)], {"a": 20 * GiB})
    assert t == {"a": 20 * GiB}


def test_tightened_targets_reduction_sums_exactly():
    s = Store("root", "/", "guest", 100 * GiB, 87 * GiB)          # R = 7 GiB
    t = tightened_targets(s, Levels(), {"a": 30 * GiB, "b": 30 * GiB, "c": 30 * GiB},
                          [Demand(c, 0, GiB) for c in "abc"], {c: 50 * GiB for c in "abc"})
    assert 90 * GiB - sum(t.values()) == 7 * GiB


def test_a_managed_client_within_target_is_never_stopped_even_with_a_failed_streak():
    # Controller ruling: a stop at FINAL also needs the client over its target now.
    # Holding 10 GiB at a 10 GiB floor: the target cannot drop below 10 GiB.
    rec = {"a": TrimRecord(requested_at=0.0, answered_at=30.0, ok=False)}
    d = _decide(used_pct=96, managed=True, trims=rec, now=10_000, holding_gib=10, floor=10 * GiB)
    assert d.targets["root"]["a"] == 10 * GiB
    assert not _stops(d)
    held = _decide(used_pct=96, managed=True, trims=rec, now=10_000, holding_gib=10,
                   floor=10 * GiB, hands_off=True)
    assert "hands-off-held:a" not in held.alarms


# --- days of coverage and time to full (Task 13) -------------------------------

def test_fast_growth_reaches_watch_before_its_percentage():
    s = Store("pool", "p", "pool", 365 * 10**9, int(0.67 * 365 * 10**9))
    assert store_stage(s, Levels()) == Stage.OK
    assert store_stage(s, Levels(), churn_bytes_per_h=3.7e9, horizon_h=48) == Stage.WATCH


def test_slow_growth_and_shrinking_stay_ok():
    s = Store("pool", "p", "pool", 365 * 10**9, int(0.67 * 365 * 10**9))
    assert store_stage(s, Levels(), churn_bytes_per_h=1e9, horizon_h=48) == Stage.OK
    assert store_stage(s, Levels(), churn_bytes_per_h=0.0, horizon_h=48) == Stage.OK
    # time to full never lowers a stage
    hi = Store("r", "/", "guest", 100, 96)
    assert store_stage(hi, Levels(), churn_bytes_per_h=1.0, horizon_h=48) == Stage.FINAL


def test_tightened_targets_cut_equal_days_and_keep_floors():
    # 100 GiB, 86 used, all registered: R = 6 GiB, space at Watch = 80 GiB.
    s = Store("root", "/", "guest", 100 * GiB, 86 * GiB)
    dem = [Demand("iq", 2 * GiB, 10 * GiB), Demand("mag", 0, 0.1 * GiB)]
    t = tightened_targets(s, Levels(), {"iq": 80 * GiB, "mag": 6 * GiB}, dem,
                          {"iq": 90 * GiB, "mag": 90 * GiB})
    # D_watch = 78 / 10.1 days; each client keeps floor + rate * D_watch
    d = 78 / 10.1
    assert t["iq"] == pytest.approx(2 * GiB + 10 * GiB * d, abs=2)
    assert t["mag"] == pytest.approx(0.1 * GiB * d, abs=2)
    assert t["iq"] + t["mag"] == 80 * GiB


def test_decide_divides_watch_space_in_days_when_given_demands():
    stores = {"root": Store("root", "/", "guest", 100 * GiB, 90 * GiB)}
    allot = {"root": Allotments({"a": 60 * GiB, "b": 30 * GiB}, True, 90 * GiB, 3.0)}
    hold = {("a", "root"): Holding("a", "root", {"raw": 60 * GiB}, False),
            ("b", "root"): Holding("b", "root", {"raw": 30 * GiB}, False)}
    regs = [Registration(c, "root", ("/x",), 0, None, True) for c in "ab"]
    dem = {"root": [Demand("a", 0, 20 * GiB), Demand("b", 0, 10 * GiB)]}
    d = decide(stores=stores, allotments=allot, holdings=hold, registrations=regs, levels=Levels(),
               trims={}, directives={}, hands_off=False, host_fault=None, now=0.0, demands=dem)
    # Watch space 80 GiB over 30 GiB/day: D = 8/3 days
    assert d.targets["root"]["a"] == pytest.approx(160 / 3 * GiB, abs=2)
    assert d.targets["root"]["b"] == pytest.approx(80 / 3 * GiB, abs=2)


def test_decide_raises_watch_from_churn_and_says_why():
    stores = {"root": Store("root", "/", "guest", 100 * GiB, 50 * GiB)}
    d = decide(stores=stores, allotments={}, holdings={}, registrations=[], levels=Levels(),
               trims={}, directives={}, hands_off=False, host_fault=None, now=0.0, demands={},
               churn={"root": 5 * GiB}, horizon_h=24.0)
    assert d.stages["root"] == Stage.WATCH
    assert d.alarms["store:root"][0] == Severity.WARNING and "full in 10.0 h" in d.alarms["store:root"][1]


def test_decide_refuses_to_guess_missing_rates():
    stores = {"root": Store("root", "/", "guest", 100 * GiB, 90 * GiB)}
    kw = dict(stores=stores, allotments={"root": Allotments({"a": GiB}, True, 50 * GiB)},
              holdings={}, registrations=[Registration("a", "root", ("/x",), 0, None, True)],
              levels=Levels(), trims={}, directives={}, hands_off=False, host_fault=None, now=0.0)
    with pytest.raises(ValueError):
        decide(**kw)
    with pytest.raises(ValueError):
        decide(**kw, demands={})

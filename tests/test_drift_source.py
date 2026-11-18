from sigmond import align
from sigmond.drift.model import Item
from sigmond.drift import source


def _plan(status, note="", live="a" * 40, target="b" * 40, name="hf-timestd"):
    return [align.Item(name, status, live, target, note)]


def _one(status, **kw):
    out = source.source_items(_plan(status, **kw), baseline="blessed")
    assert len(out) == 1
    return out[0]


def test_behind_is_drift_with_apply_fix():
    it = _one("forward")
    assert (it.kind, it.severity, it.subject) == ("source", "drift", "hf-timestd")
    assert it.fix == "smd align --apply"
    assert "aaaaaaa" in it.detail and "bbbbbbb" in it.detail


def test_ahead_is_drift_with_rollback_fix():
    it = _one("ahead", note="ahead of pin")
    assert it.severity == "drift"
    assert "--allow-rollback" in it.fix
    assert "ahead of pin" in it.detail


def test_diverged_and_refuse_are_faults():
    assert _one("diverged").severity == "fault"
    r = _one("refuse", note="ancestry unknown")
    assert r.severity == "fault" and r.fix == "smd align --apply"


def test_missing_enabled_is_fault():
    it = _one("missing", live=None)
    assert it.severity == "fault"
    assert it.fix == "smd align --apply --install-missing"


def test_stray_and_local_are_drift():
    assert _one("stray").fix == "remove it or add it to the release"
    assert _one("local").fix == "none; managed locally"
    assert _one("local").severity == "drift"


def test_current_gives_no_item():
    assert source.source_items(_plan("current"), baseline="blessed") == []


def test_no_baseline_still_reports_other_kinds():
    assert source.baseline_of(None) == (None, "none")
    assert source.source_items([], baseline="none") == [Item(
        "source", "drift", "baseline", "no blessed or candidate record",
        "smd align --apply")]


def test_baseline_of_records():
    assert source.baseline_of({"release": "v3.93"}) == ("v3.93", "blessed")
    assert source.baseline_of({"release": "v3.93", "candidate": True}) == (
        "v3.93", "candidate")
    assert source.baseline_of({}) == (None, "none")


def test_host_file_drift_items():
    drift = [
        {"path": "/a", "status": "differs", "note": ""},
        {"path": "/b", "status": "absent", "note": ""},
        {"path": "/c", "status": "unknown", "note": "offline"},
        {"path": "/d", "status": "current", "note": ""},
    ]
    out = source.host_items({"release": "v3.93"}, drift, vm_release="v3.93")
    assert [i.subject for i in out] == ["host file /a differs", "host file /b absent"]
    assert all(i.kind == "host" and i.severity == "drift" and
               i.fix == "pm-align --apply" for i in out)


def test_host_release_mismatch():
    out = source.host_items({"release": "v3.92"}, [], vm_release="v3.93")
    assert len(out) == 1 and out[0].kind == "host"
    assert "v3.92" in out[0].detail and "v3.93" in out[0].detail


def test_host_none_gives_nothing():
    assert source.host_items(None, [], vm_release="v3.93") == []


def test_candidate_baseline_empty_plan_gives_no_items():
    assert source.source_items([], baseline="candidate") == []


def test_host_candidate_flag_mismatch_both_directions():
    out = source.host_items({"release": "v3.93", "candidate": True}, [],
                            vm_release="v3.93", vm_candidate=False)
    assert len(out) == 1
    assert "host on candidate v3.93, VM on blessed v3.93" in out[0].detail
    out = source.host_items({"release": "v3.93"}, [],
                            vm_release="v3.93", vm_candidate=True)
    assert "host on blessed v3.93, VM on candidate v3.93" in out[0].detail
    assert source.host_items({"release": "v3.93", "candidate": True}, [],
                             vm_release="v3.93", vm_candidate=True) == []


def test_host_record_without_release_says_unknown():
    out = source.host_items({}, [], vm_release="v3.93")
    assert "host on blessed unknown" in out[0].detail

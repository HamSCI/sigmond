import argparse
import json

from sigmond.storage.client_kit import (
    Item, add_storage_verbs, choose_trim, holdings, written_per_day)


def _i(n, cls, t, b=10):
    return Item(f"/d/{n}", b, cls, t)


def test_trim_takes_sent_then_derived_then_raw_oldest_first():
    items = [_i("r1", "raw", 1), _i("d1", "derived", 2), _i("s2", "sent", 3), _i("s1", "sent", 0)]
    assert [x.path for x in choose_trim(items, fit_bytes=10, floor_bytes=0)] == ["/d/s1", "/d/s2", "/d/d1"]


def test_trim_stops_at_the_floor_unless_final():
    items = [_i("r1", "raw", 1), _i("r2", "raw", 2)]
    assert choose_trim(items, 0, floor_bytes=20) == []
    assert len(choose_trim(items, 0, floor_bytes=20, final=True)) == 2


def test_pinned_never_goes():
    assert choose_trim([_i("p", "pinned", 0)], 0, 0, final=True) == []


def test_holdings_sums_by_class():
    assert holdings([_i("a", "raw", 0), _i("b", "raw", 0, 5), _i("c", "sent", 0)]) == {"raw": 15, "sent": 10}


def test_written_per_day_counts_only_the_last_24h():
    now = 1_000_000.0
    items = [_i("new", "raw", now - 100), _i("edge", "raw", now - 86400 + 1, 7), _i("old", "raw", now - 86400 - 1)]
    assert written_per_day(items, now) == 17


def _parser(items, deleted, **kw):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    add_storage_verbs(sub, list_items=lambda: items, delete=deleted.append, **kw)
    return ap


def test_verbs_print_the_contract_shape(capsys):
    deleted = []
    ap = _parser([_i("s", "sent", 0), _i("r", "raw", 1)], deleted, floor_bytes=0,
                 final_policy="give-up-oldest:raw")
    args = ap.parse_args(["storage", "trim", "--fit-bytes", "10", "--json"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"held_bytes": 10, "bytes_by_class": {"raw": 10}, "refused": False, "reason": ""}
    assert [x.path for x in deleted] == ["/d/s"]


def test_holdings_verb_reports_bytes_written_per_day(capsys):
    import time
    ap = _parser([_i("r", "raw", time.time())], [], floor_bytes=0, final_policy="stop-writing")
    args = ap.parse_args(["storage", "holdings", "--json"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["bytes_by_class"] == {"raw": 10}
    assert out["estimated"] is False
    assert out["bytes_written_per_day"] == 10


def test_trim_below_floor_refuses_and_keeps_floor(capsys):
    deleted = []
    ap = _parser([_i("r1", "raw", 1), _i("r2", "raw", 2)], deleted, floor_bytes=20,
                 final_policy="stop-writing")
    args = ap.parse_args(["storage", "trim", "--fit-bytes", "5", "--json"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["refused"] is True and out["reason"]
    assert out["held_bytes"] == 20 and deleted == []


def test_final_goes_below_floor_with_give_up_oldest(capsys):
    deleted = []
    ap = _parser([_i("r1", "raw", 1), _i("d1", "derived", 0)], deleted, floor_bytes=20,
                 final_policy="give-up-oldest:raw")
    args = ap.parse_args(["storage", "final", "--fit-bytes", "0", "--json"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert [x.path for x in deleted] == ["/d/r1"]  # restricted to raw
    assert out["bytes_by_class"] == {"derived": 10}


def test_final_refuses_other_policies_without_apply_policy(capsys):
    ap = _parser([_i("r1", "raw", 1)], [], floor_bytes=0, final_policy="stop-writing")
    args = ap.parse_args(["storage", "final", "--fit-bytes", "0", "--json"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["refused"] is True and "stop-writing" in out["reason"]
    assert out["held_bytes"] == 10


def test_final_uses_apply_policy_when_given(capsys):
    calls = []

    def apply(policy, fit):
        calls.append((policy, fit))
        return {"held_bytes": 0, "bytes_by_class": {}, "refused": False, "reason": ""}

    ap = _parser([_i("r1", "raw", 1)], [], floor_bytes=0, final_policy="stop-writing",
                 apply_policy=apply)
    args = ap.parse_args(["storage", "final", "--fit-bytes", "3", "--policy", "custom:x", "--json"])
    assert args.func(args) == 0
    assert calls == [("custom:x", 3)]
    assert json.loads(capsys.readouterr().out)["held_bytes"] == 0


def test_failed_delete_stays_held_and_is_reported(capsys):
    def boom(item):
        raise OSError("busy")

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    add_storage_verbs(sub, list_items=lambda: [_i("s", "sent", 0)], delete=boom,
                      floor_bytes=0, final_policy="stop-writing")
    args = ap.parse_args(["storage", "trim", "--fit-bytes", "0", "--json"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["held_bytes"] == 10 and "busy" in out["reason"]

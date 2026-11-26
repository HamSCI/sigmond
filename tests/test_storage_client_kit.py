import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import types

import pytest

from sigmond.storage import client_kit

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


def test_the_add_a_client_example_lists_regular_files_only(tmp_path):
    """Clients copy docs/ADD-A-CLIENT.md §9's list_items.  It must walk the
    tree and never hand the kit a directory or a link to delete."""
    import os
    import re
    from pathlib import Path
    doc = (Path(__file__).resolve().parent.parent / "docs" / "ADD-A-CLIENT.md").read_text()
    sec = doc[doc.index("## 9. Storage"):]
    code = re.search(r"```python\n(.*?)```", sec, re.S).group(1)
    code = code.split("def build_parser")[0]
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.uploaded").write_text("x")
    (tmp_path / "sub" / "b").write_text("yy")
    (tmp_path / "link").symlink_to(tmp_path / "sub" / "b")
    ns = {}
    exec(code, ns)
    ns["DATA"] = str(tmp_path)
    got = sorted((os.path.relpath(it.path, tmp_path), it.cls) for it in ns["list_items"]())
    assert got == [("a.uploaded", "sent"), ("sub/b", "raw")]


# ---- expire verb and row pruner -------------------------------------------

def _db(tmp_path, rows):  # rows: [(rowid, ts_or_None)]
    p = tmp_path / "t.db"
    con = sqlite3.connect(p)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE L2_tick_phase (channel TEXT, timestamp_utc TEXT)")
    con.execute("CREATE INDEX idx_L2_tick_phase_chan_ts ON L2_tick_phase (channel, timestamp_utc)")
    con.executemany("INSERT INTO L2_tick_phase(rowid, channel, timestamp_utc) VALUES (?, 'SHARED_10000', ?)", rows)
    con.commit()
    con.close()
    return str(p)


def _day(n):  # n days before a fixed 'now'
    return (datetime(2026, 11, 25, tzinfo=timezone.utc) - timedelta(days=n)).isoformat()


@pytest.fixture(autouse=True)
def _reset_dropped(monkeypatch):
    monkeypatch.setattr(client_kit, "_DROPPED_UID", None)


def _fake_root(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "setgroups", lambda g: calls.append("groups"))
    monkeypatch.setattr(os, "setgid", lambda g: calls.append("gid"))
    monkeypatch.setattr(os, "setuid", lambda u: calls.append("uid"))
    return calls


NOW = datetime(2026, 11, 25, tzinfo=timezone.utc).timestamp()


def _q(db, sql):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _prune(db, **kw):
    kw.setdefault("deadline", time.monotonic() + 30)
    return client_kit.prune_rows(db, "L2_tick_phase", "timestamp_utc",
                                 client_kit.cutoff_iso(NOW, 30), **kw)


def test_cutoff_iso_is_utc_seconds():
    assert client_kit.cutoff_iso(NOW, 30) == "2026-10-26T00:00:00"


def test_prune_deletes_rows_older_than_the_cutoff(tmp_path):
    db = _db(tmp_path, [(i, _day(40 - i)) for i in range(1, 41)])
    r = _prune(db)
    assert r.complete and r.deleted == 9          # days 39..31 old
    left = [t for (t,) in _q(db, "SELECT timestamp_utc FROM L2_tick_phase")]
    assert len(left) == 31
    assert min(left) >= client_kit.cutoff_iso(NOW, 30)


def test_prune_keeps_a_new_row_at_a_low_rowid(tmp_path):
    db = _db(tmp_path, [(1, _day(1)), (2, _day(50)), (3, _day(45)), (4, _day(2)),
                        (5, _day(3))])
    r = _prune(db)
    left = {x for (x,) in _q(db, "SELECT rowid FROM L2_tick_phase")}
    assert {1, 4, 5} <= left
    assert left.isdisjoint({2, 3})                 # the old rows were deleted
    assert r.deleted == 2


def test_prune_never_deletes_a_null_timestamp(tmp_path):
    db = _db(tmp_path, [(1, None), (2, _day(50))])
    r = _prune(db)
    assert r.deleted == 1
    assert [x for (x,) in _q(db, "SELECT rowid FROM L2_tick_phase")] == [1]


def test_prune_search_skips_null_timestamps(tmp_path):
    db = _db(tmp_path, [(1, _day(50)), (2, _day(50)), (3, None), (4, _day(1)), (5, _day(1))])
    r = _prune(db)
    assert r.complete and r.deleted == 2
    left = {x for (x,) in _q(db, "SELECT rowid FROM L2_tick_phase")}
    assert left == {3, 4, 5}


def test_prune_of_an_empty_table_is_complete(tmp_path):
    r = _prune(_db(tmp_path, []))
    assert (r.deleted, r.complete) == (0, True)


def test_prune_stops_at_the_deadline_and_says_incomplete(tmp_path):
    db = _db(tmp_path, [(i, _day(60)) for i in range(1, 5001)])
    r = _prune(db, deadline=time.monotonic() - 1)
    assert r.deleted == 0 and r.complete is False and r.reason == "deadline"


def test_prune_refuses_an_unsafe_identifier(tmp_path):
    db = _db(tmp_path, [(1, _day(60))])
    with pytest.raises(ValueError):
        client_kit.prune_rows(db, 'L2"; DROP TABLE x;--', "timestamp_utc", "2026-01-01T00:00:00",
                              deadline=time.monotonic() + 5)
    with pytest.raises(ValueError):
        client_kit.prune_rows(db, "L2_tick_phase", "ts col", "2026-01-01T00:00:00",
                              deadline=time.monotonic() + 5)


def test_prune_yields_to_a_writer_between_batches(tmp_path):
    """Structure, not timing: each batch commits, so a writer gets in between two batches.

    The injected sleep runs between batches; a second connection inserts there with a
    short busy timeout.  A pruner holding one transaction across batches locks it out.
    """
    db = _db(tmp_path, [(i, _day(60)) for i in range(1, 6)])
    inserted = []

    def sleep_and_write(_s):
        con = sqlite3.connect(db, timeout=0.1, isolation_level=None)
        try:
            con.execute("INSERT INTO L2_tick_phase(channel, timestamp_utc) VALUES ('X', ?)",
                        (_day(0),))
            inserted.append(1)
        finally:
            con.close()

    r = _prune(db, batch_rows=1, sleep=sleep_and_write)
    assert r.complete and r.deleted == 5
    assert len(inserted) >= 4                       # a writer got in between batches
    left = _q(db, "SELECT count(*) FROM L2_tick_phase")[0][0]
    assert left == len(inserted)                    # new rows all survive


def test_prune_retries_a_locked_window_then_ends_without_raising(tmp_path):
    db = _db(tmp_path, [(1, _day(60))])
    hold = sqlite3.connect(db, isolation_level=None)
    hold.execute("BEGIN IMMEDIATE")
    sleeps = []
    try:
        r = _prune(db, busy_s=0.01, sleep=sleeps.append)
    finally:
        hold.execute("ROLLBACK")
        hold.close()
    assert r.complete is False and "locked" in r.reason and r.deleted == 0
    assert len(sleeps) >= 5


def test_incremental_vacuum_frees_pages_only_in_incremental_mode(tmp_path):
    def build(name, mode):
        p = str(tmp_path / name)
        con = sqlite3.connect(p, isolation_level=None)
        con.execute(f"PRAGMA auto_vacuum={mode}")
        con.execute("CREATE TABLE t (a TEXT)")
        con.execute("BEGIN")
        con.executemany("INSERT INTO t VALUES (?)", [("x" * 500,)] * 2000)
        con.execute("COMMIT")
        con.execute("DELETE FROM t")
        con.close()
        return p
    plain = build("plain.db", 0)
    inc = build("inc.db", 2)
    assert client_kit.incremental_vacuum(plain, deadline=time.monotonic() + 30) == 0
    freed = client_kit.incremental_vacuum(inc, deadline=time.monotonic() + 30, pages_per_step=64)
    assert freed > 0
    assert _q(inc, "PRAGMA freelist_count")[0][0] == 0


def test_drop_to_owner_sets_groups_gid_uid_in_order(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "setgroups", lambda g: calls.append(("groups", g)))
    monkeypatch.setattr(os, "setgid", lambda g: calls.append(("gid", g)))
    monkeypatch.setattr(os, "setuid", lambda u: calls.append(("uid", u)))
    p = tmp_path / "x.db"
    p.write_bytes(b"")
    client_kit.drop_to_owner(str(p))
    st = p.stat()
    assert calls == [("groups", []), ("gid", st.st_gid), ("uid", st.st_uid)]


def test_drop_to_owner_is_a_noop_when_not_root(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(os, "setuid", lambda u: calls.append(u))
    p = tmp_path / "x.db"
    p.write_bytes(b"")
    client_kit.drop_to_owner(str(p))
    assert calls == []


def test_expire_drops_to_the_db_owner_before_opening(monkeypatch, tmp_path):
    db = _db(tmp_path, [(1, _day(60))])
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "setgroups", lambda g: calls.append("groups"))
    monkeypatch.setattr(os, "setgid", lambda g: calls.append("gid"))
    monkeypatch.setattr(os, "setuid", lambda u: calls.append("uid"))
    real = sqlite3.connect

    def spy(*a, **kw):
        calls.append("connect")
        return real(*a, **kw)
    monkeypatch.setattr(sqlite3, "connect", spy)
    _prune(db)
    assert "connect" in calls
    assert calls.index("connect") > calls.index("uid") > calls.index("gid") > calls.index("groups")
    calls.clear()
    client_kit.incremental_vacuum(db, deadline=time.monotonic() + 5)
    assert calls.index("connect") > calls.index("uid")


def test_expire_verb_parses_and_prints_the_answer(capsys):
    answer = {"target": "a.b", "days": 30, "deleted": 3, "unit": "rows",
              "complete": True, "refused": False, "reason": ""}
    seen = []

    def fake(target, days, max_seconds):
        seen.append((target, days, max_seconds))
        return answer
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    add_storage_verbs(sub, list_items=lambda: [], delete=lambda i: None, floor_bytes=0,
                      final_policy="stop-writing", expire=fake)
    args = ap.parse_args(["storage", "expire", "--target", "a.b", "--days", "30", "--json"])
    assert args.func(args) == 0
    assert seen == [("a.b", 30, 600)]
    assert json.loads(capsys.readouterr().out) == answer


def test_add_expire_verb_creates_the_storage_parser_when_absent(capsys):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    client_kit.add_expire_verb(sub, expire=lambda t, d, m: {"target": t, "max": m})
    args = ap.parse_args(["storage", "expire", "--target", "x", "--days", "7",
                          "--max-seconds", "5", "--json"])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out) == {"target": "x", "max": 5}


def test_drop_to_owner_refuses_a_symlink(monkeypatch, tmp_path):
    calls = _fake_root(monkeypatch)
    real = tmp_path / "real.db"
    real.write_bytes(b"")
    link = tmp_path / "link.db"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        client_kit.drop_to_owner(str(link))
    assert calls == []


def test_drop_to_owner_refuses_a_root_owned_file(monkeypatch, tmp_path):
    calls = _fake_root(monkeypatch)
    p = tmp_path / "x.db"
    p.write_bytes(b"")
    real_fstat = os.fstat

    def root_fstat(fd):
        st = real_fstat(fd)
        return types.SimpleNamespace(st_uid=0, st_gid=0, st_dev=st.st_dev, st_ino=st.st_ino,
                                     st_mode=st.st_mode)
    monkeypatch.setattr(os, "fstat", root_fstat)
    with pytest.raises(ValueError, match="root-owned"):
        client_kit.drop_to_owner(str(p))
    assert calls == []


def test_drop_to_owner_raises_for_a_second_owner(monkeypatch, tmp_path):
    _fake_root(monkeypatch)
    a = tmp_path / "a.db"
    b = tmp_path / "b.db"
    a.write_bytes(b"")
    b.write_bytes(b"")
    client_kit.drop_to_owner(str(a))
    real_fstat = os.fstat

    def other(fd):
        st = real_fstat(fd)
        return types.SimpleNamespace(st_uid=st.st_uid + 1, st_gid=st.st_gid, st_dev=st.st_dev,
                                     st_ino=st.st_ino, st_mode=st.st_mode)
    monkeypatch.setattr(os, "fstat", other)
    with pytest.raises(ValueError, match="already dropped"):
        client_kit.drop_to_owner(str(b))


def test_prune_refuses_an_epoch_column_and_deletes_nothing(tmp_path):
    p = tmp_path / "e.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE L2_tick_phase (channel TEXT, timestamp_utc REAL)")
    con.executemany("INSERT INTO L2_tick_phase VALUES ('c', ?)",
                    [(NOW - 86400.0 * n,) for n in (1, 2, 3, 60)])
    con.commit()
    con.close()
    with pytest.raises(ValueError, match="real"):
        _prune(str(p))
    con = sqlite3.connect(p)
    try:
        assert con.execute("SELECT count(*) FROM L2_tick_phase").fetchone()[0] == 4
    finally:
        con.close()

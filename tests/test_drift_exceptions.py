import logging
from datetime import date, datetime, timezone

import pytest

from sigmond.drift import exceptions as ex
from sigmond.drift.model import Item, Report

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def _report():
    return Report(station="B4", release="v3.9", baseline="blessed", items=[
        Item("unit", "drift", "a.service", "differs", "smd align a"),
        Item("source", "drift", "repo", "ahead", "git reset"),
    ])


def _later(days):
    return datetime(2026, 11, 18 + days, 12, tzinfo=timezone.utc)


def test_accept_writes_entry_with_defaults(tmp_path):
    p = tmp_path / "e.toml"
    t = ex.accept(p, "unit:a.service", reason='say "hi" \\', by="mjh",
                  test=True, until=None, now=NOW)
    s = ex.accept(p, "source:repo", reason="r", by="mjh", test=False,
                  until=None, now=NOW)
    assert (t.until - t.start).days == 7
    assert (s.until - s.start).days == 30
    assert oct(p.stat().st_mode & 0o777) == "0o644"
    got = {e.key: e for e in ex.load(p, NOW)}
    assert got["unit:a.service"].reason == 'say "hi" \\'
    ex.accept(p, "unit:a.service", reason="again", by="rob", test=True,
              until=None, now=NOW)
    got = ex.load(p, NOW)
    assert len(got) == 2
    assert [e for e in got if e.key == "unit:a.service"][0].by == "rob"


def test_until_beyond_cap_clamps(tmp_path, caplog):
    p = tmp_path / "e.toml"
    with caplog.at_level(logging.WARNING, "sigmond.drift.exceptions"):
        t = ex.accept(p, "k:a", reason="r", by="b", test=True,
                      until=date(2027, 1, 2), now=NOW)  # 45 days
        s = ex.accept(p, "k:b", reason="r", by="b", test=False,
                      until=date(2027, 11, 18), now=NOW)
    assert (t.until - t.start).days == 30
    assert (s.until - s.start).days == 180
    assert len(caplog.records) == 2


def _exc(kind, until, key="unit:a.service"):
    return ex.Exception_(key, "why", "mjh", kind, date(2026, 11, 10), until)


def test_live_exception_moves_item_to_accepted():
    r = _report()
    out = ex.apply(r, [_exc("standing", date(2026, 11, 28))], NOW)
    assert [i.key for i in out.items] == ["source:repo"]
    a = out.accepted[0]
    assert a["key"] == "unit:a.service" and a["days_left"] == 10
    assert a["severity"] == "drift" and a["fix"] == "smd align a"
    assert a["until"] == "2026-11-28" and out.reminders == []
    assert len(r.items) == 2 and r.accepted == []


def test_test_reminder_day_before_end():
    for until, remind in ((date(2026, 11, 20), False),
                          (date(2026, 11, 19), True),
                          (date(2026, 11, 18), True)):
        out = ex.apply(_report(), [_exc("test", until)], NOW)
        assert len(out.accepted) == 1
        assert bool(out.reminders) == remind
    assert out.reminders[0] == {
        "station": "B4", "key": "unit:a.service", "by": "mjh",
        "reason": "why", "until": "2026-11-18", "fix": "smd align a"}


def test_ended_test_is_fault_with_restore_fix():
    out = ex.apply(_report(), [_exc("test", date(2026, 11, 17))], NOW)
    it = [i for i in out.items if i.subject == "a.service"][0]
    assert it.severity == "fault"
    assert it.detail == ("temporary test ended 2026-11-17; "
                         "restore baseline: smd align a")
    assert out.accepted == []


def test_expired_standing_restores_severity():
    out = ex.apply(_report(), [_exc("standing", date(2026, 11, 17))], NOW)
    it = [i for i in out.items if i.subject == "a.service"][0]
    assert it.severity == "drift" and it.detail == "differs"


def test_unmatched_exception_kept(tmp_path):
    p = tmp_path / "e.toml"
    ex.accept(p, "unit:gone.service", reason="r", by="b", test=False,
              until=None, now=NOW)
    ex.accept(p, "unit:a.service", reason="r", by="b", test=False,
              until=None, now=NOW)
    out = ex.apply(_report(), ex.load(p, NOW), NOW)
    assert len(out.accepted) == 1 and len(ex.load(p, NOW)) == 2


def test_bad_exception_entries_skipped_and_capped(tmp_path, caplog):
    p = tmp_path / "e.toml"
    good = ('key = "k:ok"\nreason = "r"\nby = "b"\nkind = "test"\n'
            'start = 2026-11-01\nuntil = 2026-11-05\n')
    p.write_text(
        "[[exception]]\n" + good.replace("2026-11-05", "\"notadate\"") +
        "\n[[exception]]\n" + good.replace('"test"', '"forever"') +
        "\n[[exception]]\n" + good.replace('reason = "r"\n', "") +
        "\n[[exception]]\n" + good.replace("2026-11-05", "2026-12-25") +
        "\n[[exception]]\n" + good)
    with caplog.at_level(logging.WARNING, "sigmond.drift.exceptions"):
        got = ex.load(p, NOW)
    assert len(got) == 2
    assert got[0].until == date(2026, 12, 1)  # clamped to start + 30
    assert len(caplog.records) == 4

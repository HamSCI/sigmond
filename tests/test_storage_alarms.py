import json

from sigmond.storage.alarms import REMIND_S, AlarmBook, Emission, journal_line
from sigmond.storage.ladder import Severity


def test_one_line_per_episode_with_reminders():
    b = AlarmBook(); a = {"store:root": (Severity.WARNING, "root 81 %")}
    assert [e.kind for e in b.update(a, 0)] == ["raise"]
    assert b.update(a, 120) == [] and b.update(a, REMIND_S - 1) == []
    assert [e.kind for e in b.update(a, REMIND_S + 1)] == ["remind"]
    assert [e.kind for e in b.update({}, REMIND_S + 2)] == ["clear"]


def test_escalation_raises_again():
    b = AlarmBook(); b.update({"k": (Severity.WARNING, "m")}, 0)
    assert [e.kind for e in b.update({"k": (Severity.CRITICAL, "m")}, 1)] == ["raise"]


def test_round_trip_and_garbage():
    b = AlarmBook(); b.update({"k": (Severity.ERROR, "m")}, 0)
    assert AlarmBook.from_dict(json.loads(json.dumps(b.to_dict()))).update({"k": (Severity.ERROR, "m")}, 1) == []
    assert AlarmBook.from_dict({"nonsense": 1}).update({}, 0) == []


def test_from_dict_tolerates_bad_entries():
    for d in ({"active": "x"}, {"active": {"k": 5}}, {"active": {"k": {"severity": 99}}},
              {"active": {"k": {"severity": "a", "last_emit": "b"}}}, None, []):
        assert AlarmBook.from_dict(d).update({}, 0) == []


def test_clear_carries_last_message():
    b = AlarmBook(); b.update({"k": (Severity.WARNING, "first")}, 0)
    b.update({"k": (Severity.WARNING, "second")}, 5)
    (e,) = b.update({}, 10)
    assert e.kind == "clear" and "second" in e.message and e.key == "k"


def test_journal_priority_prefix():
    assert journal_line(Emission("k", "raise", Severity.CRITICAL, "m")).startswith("<2>storage raise: m")
    assert journal_line(Emission("k", "clear", Severity.CRITICAL, "m")).startswith("<6>")

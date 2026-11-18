"""The drift line of `smd check` reads /var/lib/sigmond/drift.json."""
import json
from datetime import datetime, timezone

from sigmond.commands import check
from sigmond.commands.check import ABSENT, FAIL, OK, WAIT, Env, render_line

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc).timestamp()


def _doc(items=(), accepted=(), reminders=(), at="2026-11-18T06:00:00Z"):
    return {"station": "S", "release": "v3.90", "baseline": "blessed",
            "generated_at": at, "counts": {}, "items": list(items),
            "accepted": list(accepted), "reminders": list(reminders)}


def _item(kind, sev, subject, detail="d", fix="smd align --apply"):
    return {"kind": kind, "severity": sev, "subject": subject, "detail": detail,
            "fix": fix, "key": f"{kind}:{subject}"}


def _line(tmp_path, doc):
    p = tmp_path / "drift.json"
    if doc is not None:
        p.write_text(json.dumps(doc) if isinstance(doc, dict) else doc)
    return check.drift_line(Env(now=NOW), path=p)


def test_check_line_stale_missing_fault_clean(tmp_path):
    ln = _line(tmp_path, None)
    assert (ln.mark, ln.label) == (ABSENT, "drift")
    assert "no drift report yet" in ln.text

    ln = _line(tmp_path, _doc(at="2026-11-16T23:00:00Z"))       # 37 h old
    assert ln.mark == WAIT and "drift report stale" in ln.text

    ln = _line(tmp_path, _doc(items=[
        _item("source", "drift", "hf-timestd"),
        _item("build", "fault", "ubersdr-jt9", "UberSDR jt9 lacks MSK144"),
        _item("unit", "fault", "x.timer")]))
    assert ln.mark == FAIL
    assert ln.text.startswith("2 faults, 1 drift; build ubersdr-jt9: UberSDR jt9 lacks MSK144")
    assert ln.text.endswith("  Fix: smd align --apply")
    assert check.json_line(ln)["fix"] == "smd align --apply"
    assert check.json_line(ln)["id"] == "platform.drift"

    ln = _line(tmp_path, _doc(items=[_item("source", "drift", "a"),
                                     _item("stray", "drift", "/b")]))
    assert ln.mark == OK
    assert ln.text.startswith("no faults; 2 drift (1 source, 1 stray)")

    ln = _line(tmp_path, _doc())
    assert ln == check.Line(OK, "drift", "nothing differs from blessed v3.90")


def test_accepted_tests_and_reminders_are_noted(tmp_path):
    acc = [{"key": "unit:a", "kind": "test"}, {"key": "unit:b", "kind": "standing"}]
    ln = _line(tmp_path, _doc(accepted=acc, reminders=[{"key": "unit:a"}]))
    assert ln.mark == OK
    assert "2 accepted (1 test)" in ln.text
    assert "1 test to restore soon" in ln.text
    ln = _line(tmp_path, _doc(items=[_item("unit", "fault", "c")], accepted=acc))
    head, _sep, fix = ln.text.partition("  Fix: ")
    assert "2 accepted (1 test)" in head and fix == "smd align --apply"


def test_unparseable_report_waits(tmp_path):
    ln = _line(tmp_path, "{not json")
    assert ln.mark == WAIT and "does not parse" in ln.text


def test_line_is_ascii_and_sits_after_topology():
    labels = [label for label, _fn in check.CHECKS]
    assert labels.index("drift") == labels.index("topology") + 1
    ln = check.Line(FAIL, "drift", "1 fault, 0 drift; unit x: d — e.  Fix: smd align --apply")
    assert render_line(ln).isascii()

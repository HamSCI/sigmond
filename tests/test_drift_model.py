"""Tests for sigmond.drift.model: Item, Report and run_collectors."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond.drift.model import Item, Report, run_collectors  # noqa: E402


def _item(kind="source", severity="drift", subject="hf-timestd"):
    return Item(kind=kind, severity=severity, subject=subject,
                detail="d", fix="f")


def _report(items):
    return Report(station="B4", release="v3.90", baseline="blessed",
                  generated_at="2026-11-18T00:00:00Z", items=items,
                  accepted=[], reminders=[])


def test_item_key_is_kind_and_subject():
    assert _item(kind="unit", subject="radiod.service").key == \
        "unit:radiod.service"


def test_counts_by_kind_and_severity():
    r = _report([_item(), _item(subject="b"),
                 _item(severity="fault", subject="c"), _item(kind="host")])
    counts = r.counts()
    assert counts["source"] == {"drift": 2, "fault": 1}
    assert counts["host"]["drift"] == 1
    assert [i.subject for i in r.faults()] == ["c"]


def test_to_json_is_ascii_and_round_trips():
    r = _report([Item("build", "fault", "x", "café → bad", "fix")])
    d = r.to_json()
    assert set(d) == {"station", "release", "baseline", "generated_at",
                      "counts", "items", "accepted", "reminders"}
    text = json.dumps(d, ensure_ascii=True)
    assert text.isascii()
    assert json.loads(text) == d
    assert d["items"][0]["key"] == "build:x"


def test_collector_error_becomes_one_fault():
    def bad():
        raise OSError("boom")

    out = run_collectors([("unit", bad), ("host", lambda: [_item(kind="host")])])
    assert len(out) == 2
    assert out[0].kind == "unit" and out[0].severity == "fault"
    assert out[0].subject == "check" and "boom" in out[0].detail
    assert out[1].kind == "host"


def test_unknown_kind_or_severity_rejected():
    with pytest.raises(ValueError):
        Item("x", "drift", "s", "d", "f")
    with pytest.raises(ValueError):
        Item("source", "bad", "s", "d", "f")

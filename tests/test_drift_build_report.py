"""drift.build_report.build, and the report in `smd align`
(dry run, --json, --accept)."""
import argparse
import contextlib
import io
import json
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from sigmond import align
from sigmond.drift import build_report as br
from sigmond.drift import exceptions as drift_exceptions
from sigmond.drift.model import Item, Report

from tests import test_align_cli as _tac

smd = _tac.smd
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
ALL = ("source", "build", "unit", "override", "stray", "host")


def _readers(tmp_path, **kw):
    base = tmp_path / "base"
    base.mkdir(exist_ok=True)
    defaults = dict(base=base, decoder_dir=tmp_path / "decoders",
                    unit_dir=tmp_path / "units",
                    sigmond_repo=tmp_path / "sigmond",
                    exceptions_path=tmp_path / "exc.toml", now=NOW,
                    is_enabled=lambda u: False, unit_env=lambda u: {},
                    catalog_override=tmp_path / "none-catalog.toml",
                    pins_file=tmp_path / "none-pins.toml",
                    coordination=tmp_path / "none-coord.toml")
    defaults.update(kw)
    return br.Readers(**defaults)


def test_build_runs_every_kind_and_applies_exceptions(tmp_path):
    calls = []

    def fake(kind):
        def collector(*a, **kw):
            calls.append(kind)
            return [Item(kind, "fault" if kind == "unit" else "drift",
                         f"{kind}-thing", f"{kind} detail", f"fix {kind}")]
        return collector

    drift_exceptions.accept(tmp_path / "exc.toml", "stray:stray-thing",
                            reason="trial", by="mjh", test=True, until=None,
                            now=NOW)
    with mock.patch.object(br, "source_items", fake("source")), \
            mock.patch.object(br, "build_items", fake("build")), \
            mock.patch.object(br, "unit_items", fake("unit")), \
            mock.patch.object(br, "override_items", fake("override")), \
            mock.patch.object(br, "stray_items", fake("stray")), \
            mock.patch.object(br, "host_items", fake("host")):
        rep = br.build("AC0G/B4", readers=_readers(tmp_path, baseline="blessed",
                                                  release="v3.90"))
    assert sorted(calls) == sorted(ALL)
    assert rep.station == "AC0G/B4" and rep.release == "v3.90"
    assert {i.kind for i in rep.items} == set(ALL) - {"stray"}
    assert [a["key"] for a in rep.accepted] == ["stray:stray-thing"]
    assert rep.accepted[0]["kind"] == "test"


def test_a_raising_reader_is_one_fault_and_the_rest_report(tmp_path):
    def broken():
        raise OSError("topology unreadable")
    (tmp_path / "decoders" / "ubersdr-msk144").mkdir(parents=True)
    plan = [align.Item("hf-timestd", "forward", "a" * 40, "b" * 40)]
    rep = br.build("S", readers=_readers(tmp_path, plan=plan, baseline="blessed",
                                         release="v3.90", enabled=broken))
    faults = {(i.kind, i.subject) for i in rep.faults()}
    assert ("build", "check") in faults and ("unit", "check") in faults
    assert any(i.kind == "source" and i.subject == "hf-timestd" for i in rep.items)
    assert any(i.kind == "stray" and i.subject.endswith("ubersdr-msk144")
               for i in rep.items)


def test_render_text_groups_faults_first_and_counts(tmp_path):
    rep = Report(station="S", release="v3.90", baseline="blessed", items=[
        Item("source", "drift", "hf-timestd", "aaaaaaa -> bbbbbbb", "smd align --apply"),
        Item("unit", "fault", "x.timer", "not enabled", "smd enable x")],
        accepted=[{"key": "stray:/d", "kind": "test", "reason": "trial", "by": "mjh",
                   "until": "2026-11-25", "days_left": 7}])
    lines = br.render_text(rep)
    assert lines[0] == "  drift report (blessed v3.90):"
    assert lines[1] == "    fault unit     x.timer: not enabled  fix: smd enable x"
    assert lines[2].startswith("    drift source   hf-timestd: aaaaaaa -> bbbbbbb")
    assert "  accepted:" in lines
    assert lines[-1] == "  drift: 1 fault, 1 drift, 1 accepted (1 test), 0 reminders"
    assert all(ln.isascii() for ln in lines)


def test_drift_plan_splits_moves_and_drops_skipped_missing():
    plan = [align.Item("a", "move", "1" * 7, "2" * 7),
            align.Item("b", "move", "1" * 7, "2" * 7),
            align.Item("c", "move", "1" * 7, "2" * 7),
            align.Item("d", "missing", None, "2" * 7),
            align.Item("e", "missing", None, "2" * 7),
            align.Item("f", "current", "2" * 7, "2" * 7)]
    out = smd._align_drift_plan(plan, {"b"}, {"c"}, {"e"})
    assert [(i.component, i.status) for i in out] == [
        ("a", "forward"), ("b", "ahead"), ("c", "diverged"),
        ("d", "missing"), ("f", "current")]


LIVE = {"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"}
REPORT = Report(station="S", release="v3.53", baseline="blessed", items=[
    Item("unit", "fault", "sigmond-drift.timer", "not installed", "smd align --apply")])


def _with_report(report):
    seen = {}

    def fake(rel, plan, files, base):
        seen["plan"] = plan
        return report
    return mock.patch.object(_tac, "_empty_drift", side_effect=fake), seen


def test_align_dry_run_prints_the_report_after_the_plan():
    patch, seen = _with_report(REPORT)
    with patch:
        rc, out = _tac.run(dict(LIVE))
    assert rc == 1                       # today's exit semantics
    assert out.index("  apply:") < out.index("  drift report (blessed v3.53):")
    assert ("    fault unit     sigmond-drift.timer: not installed  "
            "fix: smd align --apply") in out
    assert [(i.component, i.status) for i in seen["plan"] if i.status != "current"] \
        == [("sigmond", "forward")]


def test_align_json_emits_report():
    patch, _seen = _with_report(REPORT)
    with patch:
        rc, out = _tac.run(dict(LIVE), json=True)
    assert rc == 1
    doc = json.loads(out)               # nothing but the document
    assert doc["station"] == "S" and doc["baseline"] == "blessed"
    assert doc["counts"] == {"unit": {"fault": 1}}
    assert doc["items"][0]["key"] == "unit:sigmond-drift.timer"
    assert {p["component"]: p["status"] for p in doc["plan"]}["sigmond"] == "move"
    assert out.isascii()


def test_align_json_on_lookup_failure_is_json_too():
    rc, out = _tac.run({}, release=align.LookupError_("could not reach api.github.com"),
                       json=True)
    assert rc == 2
    assert "could not reach" in json.loads(out)["error"]


def _accept(tmp_path, monkeypatch, **ns):
    monkeypatch.setattr(drift_exceptions, "DEFAULT_PATH", tmp_path / "exc.toml")
    monkeypatch.setattr(smd, "_need_root", lambda name: False)
    monkeypatch.setenv("SUDO_USER", "mjh")
    args = argparse.Namespace(**{"accept": "unit:hf-timestd-fusion.service",
                                 "reason": "testing the Rust fusion",
                                 "test": False, "until": None, **ns})
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = smd.cmd_align(args)
    return rc, out.getvalue()


def test_align_accept_writes_exception(tmp_path, monkeypatch):
    rc, out = _accept(tmp_path, monkeypatch, test=True)
    assert rc == 0, out
    excs = drift_exceptions.load(tmp_path / "exc.toml", datetime.now(timezone.utc))
    assert len(excs) == 1
    e = excs[0]
    assert (e.key, e.kind, e.by, e.reason) == (
        "unit:hf-timestd-fusion.service", "test", "mjh", "testing the Rust fusion")
    assert (e.until - e.start).days == 7
    assert "unit:hf-timestd-fusion.service accepted as a temporary test by mjh" in out


def test_align_accept_needs_a_reason_and_a_key(tmp_path, monkeypatch):
    rc, out = _accept(tmp_path, monkeypatch, reason="  ")
    assert rc == 2 and "--reason is required" in out
    rc, out = _accept(tmp_path, monkeypatch, accept="nokey")
    assert rc == 2 and "kind:subject" in out
    rc, out = _accept(tmp_path, monkeypatch, until="25-11-2026")
    assert rc == 2 and "YYYY-MM-DD" in out
    assert not (tmp_path / "exc.toml").exists()


def test_align_accept_refuses_an_unparseable_file(tmp_path, monkeypatch):
    (tmp_path / "exc.toml").write_text("[[exception]\nbroken")
    rc, out = _accept(tmp_path, monkeypatch)
    assert rc == 1
    assert "cannot be parsed" in out and "nothing was written" in out
    assert (tmp_path / "exc.toml").read_text() == "[[exception]\nbroken"


def test_reason_without_accept_is_refused():
    args = argparse.Namespace(reason="x", release=None, base="/x", no_cost=True)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert smd.cmd_align(args) == 2
    assert "only apply with --accept" in out.getvalue()


def test_align_accept_says_when_until_is_clamped(tmp_path, monkeypatch):
    far = (datetime.now(timezone.utc) + timedelta(days=90)).date().isoformat()
    rc, out = _accept(tmp_path, monkeypatch, test=True, until=far)
    assert rc == 0, out
    assert "clamped to" in out and "30-day limit" in out

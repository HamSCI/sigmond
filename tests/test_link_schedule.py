"""Tests for sigmond-link — the South Pole satellite-pass schedule
(tasks/plan-constrained-link-delivery.md, Phase 4).

The fixtures are the USAP issue of 2026-11-05 (the PDF's UTC table as
pdftotext renders it) and cel.txt as generated 26/310.  They are pinned:
the schedule drifts ~4 min/day, so a live copy would fail these within a week
(plan trap 7).
"""
from __future__ import annotations

import datetime as dt
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "tests" / "fixtures" / "link"
UTC = dt.timezone.utc


def _load():
    p = str(REPO / "bin" / "sigmond-link")
    loader = importlib.machinery.SourceFileLoader("sigmond_link_under_test", p)
    spec = importlib.util.spec_from_file_location("sigmond_link_under_test", p, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


m = _load()
ROWS, PROBLEMS = m.parse_spsat((FIX / "spsat-20261105-utc.txt").read_text())
CEL = m.parse_cel((FIX / "cel-26310.txt").read_text())


def T(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


class TestParse(unittest.TestCase):
    def test_every_row_parses(self):
        self.assertEqual(PROBLEMS, [])
        self.assertEqual(len(ROWS), 15)
        self.assertEqual(min(ROWS), "2026-11-05")
        self.assertEqual(max(ROWS), "2026-11-19")

    def test_pinned_row_with_two_tdrs_passes(self):
        self.assertEqual(ROWS["2026-11-11"], [
            ["TDRS-6", "2026-11-11T14:40:00+00:00", "2026-11-11T19:10:00+00:00"],
            ["TDRS-6", "2026-11-11T19:12:00+00:00", "2026-11-11T20:42:00+00:00"],
            ["DSCS-B13", "2026-11-11T20:29:00+00:00", "2026-11-12T02:24:00+00:00"],
            ["DSCS-B11", "2026-11-11T05:21:00+00:00", "2026-11-11T10:33:00+00:00"],
            ["DSCS-A03", "2026-11-11T23:11:00+00:00", "2026-11-12T03:51:00+00:00"],
        ])

    def test_no_pass_days_have_no_tdrs(self):
        self.assertFalse(any(s == "TDRS-6" for s, *_ in ROWS["2026-11-05"]))
        self.assertEqual(sum(s == "TDRS-6" for s, *_ in ROWS["2026-11-12"]), 1)

    def test_cel_reads_utc_day_of_year(self):
        # 26/315/144000 191000 — day 315 of 2026 is 11 Nov
        self.assertIn(("2026-11-11T14:40:00+00:00", "2026-11-11T19:10:00+00:00"), CEL)

    def test_pdf_agrees_with_cel_so_both_are_utc(self):
        """The plan's trap 6: read the table in the wrong zone and the station
        talks only when it cannot.  The PDF's UTC table and cel.txt are
        independent sources; most TDRS passes must match exactly."""
        agree, differ = m.cross_check(ROWS, CEL)
        self.assertEqual((agree, differ), (18, 4))   # 4 = two real re-plans, 11-16 and 11-18

    def test_a_shifted_table_fails_the_cross_check(self):
        shifted = {d: [[s, (T(a[:19]) + dt.timedelta(hours=13)).isoformat(),
                        (T(b[:19]) + dt.timedelta(hours=13)).isoformat()] for s, a, b in p]
                   for d, p in ROWS.items()}
        self.assertEqual(m.cross_check(shifted, CEL)[0], 0)

    def test_only_the_utc_table_is_read(self):
        with self.assertRaises(ValueError):
            m.parse_spsat("South Pole Satellite Schedule (EST)\n11/05/26 No Pass\n")


class TestBuild(unittest.TestCase):
    def test_overlapping_passes_merge_and_gaps_are_the_published_ones(self):
        doc = m.build(ROWS, "all", 0)
        wins = [(T(w["start"][:19]), T(w["end"][:19])) for w in doc["windows"]]
        day = [w for w in wins if w[0].date() == dt.date(2026, 11, 12)]
        gaps = [(a[1].strftime("%H:%M"), b[0].strftime("%H:%M")) for a, b in zip(day, day[1:])]
        self.assertEqual(gaps, [("10:29", "14:36"), ("19:06", "20:25")])
        self.assertEqual(doc["horizon"], ["2026-11-05T00:00:00+00:00", "2026-11-20T00:00:00+00:00"])

    def test_satellite_filter_and_margin(self):
        doc = m.build(ROWS, ["TDRS-6"], 10)
        self.assertTrue(all(w["sats"] == ["TDRS-6"] for w in doc["windows"]))
        first = [w for w in doc["windows"] if w["start"].startswith("2026-11-11")][0]
        self.assertEqual(first["start"], "2026-11-11T14:50:00+00:00")
        self.assertEqual(first["end"], "2026-11-11T19:00:00+00:00")


def _doc(fetched="2026-11-12T00:00:00+00:00"):
    d = m.build(ROWS, "all", 0)
    d.update(version=1, fetched_at=fetched)
    return d


class TestEvaluate(unittest.TestCase):
    def test_in_a_pass(self):
        st = m.evaluate(_doc(), T("2026-11-12T16:00:00"))
        self.assertTrue(st["up"] and st["scheduled"])
        self.assertEqual(st["until"], "2026-11-12T19:06:00+00:00")

    def test_in_a_gap(self):
        st = m.evaluate(_doc(), T("2026-11-12T12:00:00"))
        self.assertFalse(st["up"])
        self.assertEqual(st["until"], "2026-11-12T14:36:00+00:00")

    def test_fail_open(self):
        gap = T("2026-11-12T12:00:00")
        for doc, t in [(None, gap),
                       (_doc(fetched="2026-11-01T00:00:00+00:00"), gap),   # stale
                       (_doc(), T("2026-11-25T12:00:00")),                  # past the horizon
                       ({"fetched_at": "x"}, gap),                          # garbage
                       (_doc(fetched="2026-11-12T00:00:00"), gap)]:         # no timezone
            self.assertTrue(m.evaluate(doc, t)["up"], doc and doc.get("fetched_at"))


class TestGateAndApply(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        m.SCHEDULE = self.tmp / "link-schedule.json"
        self.cfg = m.read_settings(self.tmp / "absent.toml")
        self.cfg["enabled"] = True

    def test_gate_exit_codes(self):
        m.SCHEDULE.write_text(json.dumps(_doc()))
        m.now = lambda: T("2026-11-12T12:00:00")
        self.assertEqual(m.cmd_gate(self.cfg), 1)
        m.now = lambda: T("2026-11-12T16:00:00")
        self.assertEqual(m.cmd_gate(self.cfg), 0)
        self.cfg["enabled"] = False
        m.now = lambda: T("2026-11-12T12:00:00")
        self.assertEqual(m.cmd_gate(self.cfg), 0)

    def test_gate_crash_lets_the_unit_run(self):
        m.SCHEDULE.write_text("{")      # corrupt file
        orig = m.read_settings
        m.read_settings = lambda *a: dict(self.cfg)
        try:
            self.assertEqual(m.main(["gate"]), 0)
        finally:
            m.read_settings = orig

    def test_fetch_never_tries_in_a_gap(self):
        m.SCHEDULE.write_text(json.dumps(_doc(fetched="2026-11-11T00:00:00+00:00")))
        m.now = lambda: T("2026-11-12T12:00:00")
        called = []
        m.fetch_usap = lambda rows: called.append(1) or (_ for _ in ()).throw(AssertionError)
        self.assertEqual(m.cmd_fetch(self.cfg, force=False), 0)
        self.assertEqual(called, [])

    def test_disabling_removes_the_schedule(self):
        m.SCHEDULE.write_text(json.dumps(_doc()))
        self.cfg["enabled"] = False
        m.cmd_fetch(self.cfg, force=False)
        self.assertFalse(m.SCHEDULE.exists())

    def test_apply_writes_and_removes_dropins(self):
        root = self.tmp / "systemd"
        self.cfg["gated_units"] = ["a.service", "b.service"]
        m.cmd_apply(self.cfg, root)
        p = root / "a.service.d" / m.DROPIN_NAME
        self.assertIn("ExecCondition=/usr/local/sbin/sigmond-link gate", p.read_text())
        self.cfg["gated_units"] = ["b.service"]
        m.cmd_apply(self.cfg, root)
        self.assertFalse(p.exists())
        self.cfg["enabled"] = False
        m.cmd_apply(self.cfg, root)
        self.assertEqual(list(root.glob("*.d/*")), [])


if __name__ == "__main__":
    unittest.main()

"""Tests for sigmond.gap_hourly — hourly radiod block-drop (gap_count) sampler.

Promotes B4's uncommitted /usr/local/sbin/gap-hourly.sh into the repo.
gap_count is the ONLY honest loss field (radiod zero-fills dropped blocks,
so completeness_pct reads 100% while data is genuinely missing) — see
sigmond.gap_hourly's module docstring for the full rationale + baselines.

The one behavior change vs. the .orig: an empty window (no sidecars seen
in the trailing 3600 s) must write literal "NA" fields, never a lying
0.00 rate.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond.gap_hourly import HEADER, append_row, collect_records, summarise_window


# ---- summarise_window ------------------------------------------------------


def test_summarise_window_records_in_window_compute_correct_rate():
    now = 100_000
    # 12 sidecars inside the trailing-3600s window, 1 gap each:
    # channel_hours = 12*300/3600 = 1.00, gaps = 12, rate = 12.00
    records = [
        {"minute_boundary": now - 3599 + i * 10, "gap_count": 1} for i in range(12)
    ]
    row = summarise_window(records, now)
    assert row["gaps"] == 12
    assert row["channel_hours"] == "1.00"
    assert row["gaps_per_ch_hr"] == "12.00"


def test_summarise_window_empty_window_is_honest_na():
    row = summarise_window([], 100_000)
    assert row["gaps"] == "NA"
    assert row["channel_hours"] == "0.00"
    assert row["gaps_per_ch_hr"] == "NA"


def test_summarise_window_excludes_records_outside_window():
    now = 100_000
    records = [
        {"minute_boundary": now - 3601, "gap_count": 5},  # before window start
        {"minute_boundary": now, "gap_count": 5},          # at 'now' -> excluded
        {"minute_boundary": now - 100, "gap_count": 2},    # inside
    ]
    row = summarise_window(records, now)
    # only the one inside-window record counts: n=1 -> h=300/3600=0.0833
    assert row["gaps"] == 2
    assert row["channel_hours"] == "0.08"
    assert row["gaps_per_ch_hr"] == "24.00"


def test_summarise_window_missing_gap_count_treated_as_zero():
    now = 100_000
    records = [{"minute_boundary": now - 100}]  # no gap_count key at all
    row = summarise_window(records, now)
    assert row["gaps"] == 0
    assert row["channel_hours"] == "0.08"
    assert row["gaps_per_ch_hr"] == "0.00"


def test_summarise_window_accepts_tuples_too():
    now = 100_000
    records = [(now - 100, 3), (now - 200, 1)]
    row = summarise_window(records, now)
    assert row["gaps"] == 4


# ---- append_row -------------------------------------------------------------


def test_append_row_writes_header_once_then_appends(tmp_path):
    path = tmp_path / "gap-hourly.tsv"
    append_row(path, ["2026-08-20T00:05Z", "3", "1.00", "3.00", "0"])
    append_row(path, ["2026-08-20T01:05Z", "5", "1.00", "5.00", "0"])

    lines = path.read_text().splitlines()
    assert lines[0] == HEADER
    assert lines.count(HEADER) == 1
    assert lines[1] == "2026-08-20T00:05Z\t3\t1.00\t3.00\t0"
    assert lines[2] == "2026-08-20T01:05Z\t5\t1.00\t5.00\t0"


# ---- collect_records ---------------------------------------------------------


def test_collect_records_skips_unparseable_json_without_raising(tmp_path):
    base = tmp_path / "raw_buffer"
    day_dir = base / "20m" / "2026-08-20"
    day_dir.mkdir(parents=True)

    good = day_dir / "00.json"
    good.write_text(json.dumps({"minute_boundary": 1000, "gap_count": 2}))

    bad = day_dir / "01.json"
    bad.write_text("{not valid json at all")

    records = list(collect_records(str(base), 2000))

    assert len(records) == 1
    assert records[0]["gap_count"] == 2
    assert records[0]["minute_boundary"] == 1000


def test_collect_records_skips_non_directory_day_entries(tmp_path):
    base = tmp_path / "raw_buffer"
    chan_dir = base / "20m"
    chan_dir.mkdir(parents=True)
    # A stray file sitting where a day-directory is expected must not blow
    # up the walk (mirrors the .orig's `if not os.path.isdir(d): continue`).
    (chan_dir / "not-a-day-dir.txt").write_text("stray")
    day_dir = chan_dir / "2026-08-20"
    day_dir.mkdir()
    (day_dir / "00.json").write_text(json.dumps({"minute_boundary": 1000, "gap_count": 1}))

    records = list(collect_records(str(base), 2000))
    assert len(records) == 1

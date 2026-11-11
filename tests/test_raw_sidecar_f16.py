"""Raw-buffer sidecars for F16 chunks (hf-timestd F16 archive).

One channel, one day directory, three chunks: an F16 chunk, a plain F32
chunk and an overflow-fallback chunk (F32 written because the F16 scale
overflowed).  Sigmond never decodes the I/Q.  gap_hourly and local_resources
read the JSON sidecars; check reads only file modification times.  None of
them may infer sample counts from file size, which differs per dtype."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond import gap_hourly
from sigmond.commands import check
from sigmond.discovery import local_resources
from tests.check_fakes import NOW, make_env

CHANNEL = "WWV_10000"
FS = 24000
SECONDS = 60
SAMPLES = FS * SECONDS


def _sidecar(start, dtype, **extra):
    rec = {
        "minute_boundary": start, "channel_name": CHANNEL,
        "frequency_hz": 10000000.0, "sample_rate": FS,
        "samples_written": SAMPLES, "samples_expected": SAMPLES,
        "file_duration_sec": SECONDS, "completeness_pct": 100.0,
        "gap_count": 0, "gap_samples": 0, "dtype": dtype,
        "byte_order": "little", "compression": "zstd",
    }
    rec.update(extra)
    return rec


F16_START = NOW - 300  # newest chunk, well inside check's 660 s window


@pytest.fixture
def raw_root(tmp_path):
    """tmp_path/raw_buffer/<chan>/<day>/ with F16, F32 and fallback chunks.
    The F16 chunk is the newest; the others are older than check's 660 s
    freshness window, so only the F16 chunk can make a channel look fresh.
    Both the .bin.zst and the .json of each chunk get the chunk's mtime.
    Payloads stay tiny: sigmond never decodes them, and sizes differ on purpose."""
    day = datetime.fromtimestamp(F16_START, tz=timezone.utc).strftime("%Y%m%d")
    d = tmp_path / "raw_buffer" / CHANNEL / day
    d.mkdir(parents=True)
    chunks = [
        (NOW - 1500, "complex64", {}, b"f32" * 40),
        (NOW - 1400, "complex64", {"f16_fallback": "overflow"}, b"ovf" * 40),
        (F16_START, "complex-f16", {"scale": 4096.0}, b"f16" * 10),
    ]
    for start, dtype, extra, payload in chunks:
        (d / f"{start}.bin.zst").write_bytes(payload)
        (d / f"{start}.json").write_text(json.dumps(_sidecar(start, dtype, **extra)))
        for ext in (".bin.zst", ".json"):
            os.utime(d / f"{start}{ext}", (start, start))
    return tmp_path / "raw_buffer"


def _f16_files(raw_root):
    return list(raw_root.glob(f"{CHANNEL}/*/{F16_START}.*"))


def test_gap_hourly_counts_every_chunk_including_f16(raw_root):
    records = list(gap_hourly.collect_records(str(raw_root), NOW))
    assert sorted(r["dtype"] for r in records) == ["complex-f16", "complex64", "complex64"]
    row = gap_hourly.summarise_window(records, NOW)
    assert row["gaps"] == 0
    assert row["channel_hours"] == "0.25"  # 3 sidecars x 300 s / 3600


def test_local_resources_reads_f16_sidecars_without_a_false_gap(raw_root):
    records = local_resources._default_gap_records(str(raw_root))
    assert len(records) == 3
    s = local_resources._summarise_gaps(records, NOW)
    assert s["gaps"] == 0
    assert s["channel_hours"] == pytest.approx(0.25)
    assert s["gap_rate_per_channel_hour"] == 0.0


def _grape(tmp_path):
    cfg = {"ka9q": {"source": "radiod"},
           "recorder": {"production_data_root": str(tmp_path), "tiered_storage": False,
                        "channel_group": {"g": {"channels": [
                            {"frequency_hz": 10000000, "description": CHANNEL}]}}}}
    return check.grape_line(make_env(now=NOW, timestd_config=lambda: cfg))


def test_check_grape_line_sees_f16_chunks_as_fresh(raw_root, tmp_path):
    assert len(_f16_files(raw_root)) == 2
    line = _grape(tmp_path)
    assert line.mark == check.OK, line


def test_check_grape_line_goes_stale_when_the_f16_chunk_ages(raw_root, tmp_path):
    for f in _f16_files(raw_root):
        os.utime(f, (NOW - 2000, NOW - 2000))
    assert _grape(tmp_path).mark != check.OK

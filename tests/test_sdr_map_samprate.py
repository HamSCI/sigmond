"""sdr_map.conf_samprate_hz — the [rx888] ADC rate of a radiod conf."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond import sdr_map  # noqa: E402


def _conf(line):
    return f"[global]\nsamprate = 12000\n\n[rx888]\ndevice = \"rx888\"\n{line}\n"


def test_plain_hz_and_global_rate_ignored():
    assert sdr_map.conf_samprate_hz(_conf("samprate = 129600000")) == 129_600_000


def test_ka9q_suffix_forms():
    assert sdr_map.conf_samprate_hz(_conf("samprate = 129m6")) == 129_600_000
    assert sdr_map.conf_samprate_hz(_conf("samprate = 64m800000")) == 64_800_000
    assert sdr_map.conf_samprate_hz(_conf("samprate = 30m0")) == 30_000_000


def test_trailing_comment_stripped():
    assert sdr_map.conf_samprate_hz(
        _conf("samprate=64800000  # 2026-09-05: (was 129600000)")) == 64_800_000


def test_absent_or_commented_is_none():
    assert sdr_map.conf_samprate_hz(_conf("#samprate = 64m8")) is None
    assert sdr_map.conf_samprate_hz("[global]\nsamprate = 12000\n") is None

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


# radiod reads samprate with ka9q's parse_frequency (src/parse.c): a
# decimal point or a k/M/G multiplier in either case, after iniparser has
# cut an inline `;` or `#` comment.  Every spelling radiod accepts must
# read back, or a --reconfig recomputes the rate and overwrites it.

def test_decimal_with_multiplier_either_case():
    assert sdr_map.conf_samprate_hz(_conf("samprate = 129.6M")) == 129_600_000
    assert sdr_map.conf_samprate_hz(_conf("samprate = 64.8m")) == 64_800_000
    assert sdr_map.conf_samprate_hz(_conf("samprate = 129600k")) == 129_600_000
    assert sdr_map.conf_samprate_hz(_conf("samprate = 0.1296G")) == 129_600_000
    assert sdr_map.conf_samprate_hz(_conf("samprate = 64M8")) == 64_800_000


def test_semicolon_comment_stripped():
    assert sdr_map.conf_samprate_hz(
        _conf("samprate = 129600000 ; full rate")) == 129_600_000
    assert sdr_map.conf_samprate_hz(
        _conf("samprate = 64.8M;hand-set")) == 64_800_000


def test_exponent_form():
    assert sdr_map.conf_samprate_hz(_conf("samprate = 129.6e6")) == 129_600_000
    assert sdr_map.conf_samprate_hz(_conf("samprate = 6.48E7")) == 64_800_000


def test_quoted_value():
    assert sdr_map.conf_samprate_hz(_conf('samprate = "129.6M"')) == 129_600_000


def test_raw_value_present_but_unparseable():
    # conf_samprate_hz cannot read it, but conf_samprate_raw shows a value
    # is there, so a caller keeps it rather than recomputing.
    text = _conf("samprate = $RATE   # set by the image")
    assert sdr_map.conf_samprate_hz(text) is None
    assert sdr_map.conf_samprate_raw(text) == "$RATE"
    assert sdr_map.conf_samprate_raw(_conf("#samprate = 64m8")) == ""
    assert sdr_map.conf_samprate_raw(
        _conf("samprate = 129.6M ; x")) == "129.6M"

"""TUI SDR inventory picks the RX888 ADC rate by the cpu_profiles rule too."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

pytest.importorskip("textual")

from sigmond import cpu_profiles, sdr_map  # noqa: E402
from sigmond.tui.screens import sdr_inventory as inv  # noqa: E402

CEZANNE = cpu_profiles.profile_for("AMD Ryzen 5 5560U with Radeon Graphics")


def test_first_conf_full_rate_second_reduced_on_5560u(tmp_path):
    first = tmp_path / "radiod@a.conf"
    assert inv._rx888_samprate_for(first, tmp_path, CEZANNE) == 129_600_000
    first.write_text(inv._build_rx888_conf("a", 0))
    second = tmp_path / "radiod@b.conf"
    hz = inv._rx888_samprate_for(second, tmp_path, CEZANNE)
    assert hz == 64_800_000
    text = inv._build_rx888_conf("b", 1, samprate_hz=hz)
    assert sdr_map.conf_samprate_hz(text) == 64_800_000


def test_rewriting_an_existing_conf_keeps_its_rate(tmp_path):
    (tmp_path / "radiod@a.conf").write_text(inv._build_rx888_conf("a", 0))
    b = tmp_path / "radiod@b.conf"
    b.write_text(inv._build_rx888_conf("b", 1, samprate_hz=129_600_000))
    # b already exists at 129.6: the profile would say 64.8, but it is kept.
    assert inv._rx888_samprate_for(b, tmp_path, CEZANNE) == 129_600_000


def test_full_rate_text_is_unchanged():
    text = inv._build_rx888_conf("a", 0)
    assert "samprate = 129m600000\n#samprate = 64m800000\n" in text

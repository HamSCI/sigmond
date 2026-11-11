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


@pytest.fixture(autouse=True)
def _no_host_files(tmp_path, monkeypatch):
    """Never this machine's coordination.toml or hf-timestd config."""
    monkeypatch.setattr(inv, "COORDINATION_PATH", tmp_path / "no-coord.toml")
    monkeypatch.setattr(inv, "HF_TIMESTD_CONF", tmp_path / "no-timestd.toml")


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


def test_rewriting_keeps_a_ka9q_spelled_rate(tmp_path):
    b = tmp_path / "radiod@b.conf"
    b.write_text("[global]\nstatus = b-status.local\n\n[rx888]\n"
                 "device = \"rx888\"\nsamprate = 64.8M ; hand-set\n")
    assert inv._rx888_samprate_for(b, tmp_path, CEZANNE) == 64_800_000


def test_rewriting_keeps_an_unparseable_rate_verbatim(tmp_path):
    b = tmp_path / "radiod@b.conf"
    b.write_text("[global]\nstatus = b-status.local\n\n[rx888]\n"
                 "device = \"rx888\"\nsamprate = $RATE\n")
    rate = inv._rx888_samprate_for(b, tmp_path, CEZANNE)
    assert rate == "$RATE"
    text = inv._build_rx888_conf("b", 0, samprate_hz=rate)
    assert sdr_map.conf_samprate_raw(text) == "$RATE"


def test_conf_hf_timestd_uses_gets_full_rate_though_another_exists(tmp_path):
    (tmp_path / "radiod@a.conf").write_text(inv._build_rx888_conf("a", 0))
    ts = tmp_path / "timestd-config.toml"
    ts.write_text('[ka9q]\nstatus_address = "b.local"\n')
    b = tmp_path / "radiod@b.conf"
    assert inv._rx888_samprate_for(b, tmp_path, CEZANNE,
                                   timestd_conf=ts) == 129_600_000
    # A further new conf, not the one hf-timestd uses, gets 64.8.
    c = tmp_path / "radiod@c.conf"
    assert inv._rx888_samprate_for(c, tmp_path, CEZANNE,
                                   timestd_conf=ts) == 64_800_000

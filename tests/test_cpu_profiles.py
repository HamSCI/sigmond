"""Per-CPU station defaults (lib/sigmond/cpu_profiles.py).

The 5560U entry records the two-radiod layout measured on KFS-OMNI,
2026-11-06: one RX888 radiod at 129.6 Msps, every other at 64.8 Msps, and
the decoder pCPUs capped at 8 GB/s of DRAM bandwidth (AMD MBA).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond import cpu_profiles  # noqa: E402

CEZANNE = "AMD Ryzen 5 5560U with Radeon Graphics"


def test_5560u_profile_is_keyed_by_the_wisdom_slug():
    p = cpu_profiles.profile_for(CEZANNE)
    assert p is not None
    assert "amd-ryzen-5-5560u" in cpu_profiles.CPU_PROFILES
    assert p.full_rate_radiods == 1
    assert p.reduced_samprate_hz == 64_800_000
    assert p.decoder_mba_gbps == 8


def test_unknown_cpu_has_no_profile():
    assert cpu_profiles.profile_for("Intel(R) Core(TM) i7-1165G7") is None
    assert cpu_profiles.profile_for("") is None


def test_first_rx888_radiod_keeps_full_rate_others_drop_to_64_8():
    p = cpu_profiles.profile_for(CEZANNE)
    assert cpu_profiles.rx888_samprate_hz(0, p) == 129_600_000
    assert cpu_profiles.rx888_samprate_hz(1, p) == 64_800_000
    assert cpu_profiles.rx888_samprate_hz(2, p) == 64_800_000


def test_unknown_cpu_keeps_129_6_for_every_radiod():
    for n in range(4):
        assert cpu_profiles.rx888_samprate_hz(n, None) == 129_600_000


def test_decoder_mba_cap():
    assert cpu_profiles.decoder_mba_gbps(CEZANNE) == 8
    assert cpu_profiles.decoder_mba_gbps("Some Other CPU") is None


def test_host_profile_reads_cpuinfo(tmp_path):
    ci = tmp_path / "cpuinfo"
    ci.write_text(f"processor\t: 0\nmodel name\t: {CEZANNE}\n")
    assert cpu_profiles.host_profile(ci) is cpu_profiles.profile_for(CEZANNE)
    ci.write_text("processor\t: 0\n")
    assert cpu_profiles.host_profile(ci) is None


def test_cli_prints_the_cap_or_nothing(capsys):
    assert cpu_profiles.main(["decoder-mba-gbps", CEZANNE]) == 0
    assert capsys.readouterr().out.strip() == "8"
    assert cpu_profiles.main(["decoder-mba-gbps", "Unknown"]) == 0
    assert capsys.readouterr().out.strip() == ""

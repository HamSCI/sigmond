"""Zen 3 mobile parts default to radiod 2000 / others 1600 MHz (Rob 2026-11-15,
VK5ARG 5850U: ~30 % less package power, FFT 70 % busy, no sample loss)."""
from pathlib import Path

from sigmond import topology as T


def _cpuinfo(tmp_path, name, family=25, model=80, vendor="AuthenticAMD"):
    p = tmp_path / "cpuinfo"
    block = (f"processor\t: 0\nvendor_id\t: {vendor}\ncpu family\t: {family}\nmodel\t\t: {model}\n"
             f"model name\t: {name}\n\n")
    p.write_text(block * 2)
    return p


def test_5825u_5850u_5560u_get_the_low_power_default(tmp_path):
    for name in ("AMD Ryzen 7 5825U with Radeon Graphics", "AMD Ryzen 7 5850U with Radeon Graphics",
                 "AMD Ryzen 5 5560U with Radeon Graphics", "AMD Ryzen 7 5800H with Radeon Graphics"):
        d = T.cpu_freq_defaults(_cpuinfo(tmp_path, name))
        assert (d["radiod_max_mhz"], d["other_max_mhz"]) == (2000, 1600), name


def test_everything_else_keeps_3200_1400(tmp_path):
    cases = [("AMD Ryzen 7 5700G with Radeon Graphics", 25, 80),          # desktop Cezanne
             ("AMD Ryzen 5 5500U with Radeon Graphics", 23, 104),         # Lucienne, Zen 2
             ("AMD Ryzen 7 6800U with Radeon Graphics", 25, 68),          # Rembrandt, Zen 3+
             ("Intel(R) Core(TM) i5-6500T CPU @ 2.50GHz", 6, 94)]
    for name, fam, mod in cases:
        vendor = "GenuineIntel" if "Intel" in name else "AuthenticAMD"
        d = T.cpu_freq_defaults(_cpuinfo(tmp_path, name, fam, mod, vendor))
        assert (d["radiod_max_mhz"], d["other_max_mhz"]) == (3200, 1400), name


def test_unreadable_cpuinfo_keeps_the_old_default(tmp_path):
    d = T.cpu_freq_defaults(tmp_path / "missing")
    assert (d["radiod_max_mhz"], d["other_max_mhz"]) == (3200, 1400)


def test_station_values_still_win(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "CPUINFO", _cpuinfo(tmp_path, "AMD Ryzen 7 5825U with Radeon Graphics"))
    topo = tmp_path / "topology.toml"
    topo.write_text("[cpu_freq]\nradiod_max_mhz = 2400\n")
    cf = T.load_topology(topo).cpu_freq
    assert (cf["radiod_max_mhz"], cf["other_max_mhz"]) == (2400, 1600)

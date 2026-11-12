"""`smd admin diag cpu-freq --other-max-mhz N --save` (2026-11-12).

The override flags applied once and were never saved, so the boot-time unit
(smd-cpu-freq.service, `--apply` without flags) put the old cap back at the
next reboot.  On AC0G-SIGMA, bare metal, the default 1400 MHz cap on every
non-radiod core raised CPU pressure from 1.5 % to 9 %; Michael chose 3200.
"""
from __future__ import annotations

from sigmond.topology import load_topology, set_cpu_freq


def test_save_writes_a_new_table_and_keeps_the_rest(tmp_path):
    p = tmp_path / "topology.toml"
    p.write_text("[component.wspr-recorder]\nenabled = true\n")
    assert set_cpu_freq({"other_max_mhz": 3200}, p) is True
    t = load_topology(p)
    assert t.cpu_freq["other_max_mhz"] == 3200
    assert t.cpu_freq["radiod_max_mhz"] == 3200          # default stands
    assert t.components["wspr-recorder"].enabled is True
    assert set_cpu_freq({"other_max_mhz": 3200}, p) is False   # idempotent


def test_save_replaces_a_key_in_place(tmp_path):
    p = tmp_path / "topology.toml"
    p.write_text("[cpu_freq]\n# operator note\nother_max_mhz = 1400\nradiod_max_mhz = 3000\n\n"
                 "[component.wspr-recorder]\nenabled = true\n")
    set_cpu_freq({"other_max_mhz": 3200}, p)
    text = p.read_text()
    assert "other_max_mhz = 3200" in text and "1400" not in text
    assert "radiod_max_mhz = 3000" in text and "# operator note" in text
    assert load_topology(p).cpu_freq["other_max_mhz"] == 3200


def test_save_adds_a_missing_key_to_an_existing_table(tmp_path):
    p = tmp_path / "topology.toml"
    p.write_text("[cpu_freq]\nradiod_max_mhz = 3000\n[component.wspr-recorder]\nenabled = true\n")
    set_cpu_freq({"other_max_mhz": 2800}, p)
    t = load_topology(p)
    assert t.cpu_freq["other_max_mhz"] == 2800 and t.cpu_freq["radiod_max_mhz"] == 3000
    assert t.components["wspr-recorder"].enabled is True

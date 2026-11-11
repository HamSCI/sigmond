"""build_system_view fills each local radiod's samprate_hz from its conf,
so the frequency_coverage rule checks the rate radiod actually runs."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond import sysview  # noqa: E402
from sigmond.coordination import Coordination, Radiod  # noqa: E402

RX888_64_8 = "[global]\nhardware = rx888\n[rx888]\nsamprate = 64.8M\n"


def _coord(**radiods):
    return Coordination(radiods={k: v for k, v in radiods.items()})


def test_rate_read_from_radio_conf(tmp_path):
    conf = tmp_path / "radiod@rx2.conf"
    conf.write_text(RX888_64_8)
    coord = _coord(rx2=Radiod(id="rx2", radio_conf=str(conf)))
    sysview.resolve_radiod_samprates(coord, tmp_path)
    assert coord.radiods["rx2"].samprate_hz == 64_800_000


def test_conf_found_by_id_without_radio_conf(tmp_path):
    (tmp_path / "radiod@rx2.conf").write_text(RX888_64_8)
    coord = _coord(rx2=Radiod(id="rx2"))
    sysview.resolve_radiod_samprates(coord, tmp_path)
    assert coord.radiods["rx2"].samprate_hz == 64_800_000


def test_conf_overrides_a_stale_declaration(tmp_path):
    conf = tmp_path / "radiod@rx2.conf"
    conf.write_text(RX888_64_8)
    coord = _coord(rx2=Radiod(id="rx2", radio_conf=str(conf),
                              samprate_hz=129_600_000))
    sysview.resolve_radiod_samprates(coord, tmp_path)
    assert coord.radiods["rx2"].samprate_hz == 64_800_000


def test_missing_or_unreadable_conf_keeps_declaration(tmp_path):
    bad = tmp_path / "radiod@rx3.conf"
    bad.write_text("[global]\nhardware = rx888\n[rx888]\nsamprate = lots\n")
    coord = _coord(
        rx1=Radiod(id="rx1", radio_conf=str(tmp_path / "nope.conf"),
                   samprate_hz=129_600_000),
        rx3=Radiod(id="rx3", radio_conf=str(bad)))
    sysview.resolve_radiod_samprates(coord, tmp_path)
    assert coord.radiods["rx1"].samprate_hz == 129_600_000
    assert coord.radiods["rx3"].samprate_hz == 0


def test_remote_radiod_untouched(tmp_path):
    (tmp_path / "radiod@far.conf").write_text(RX888_64_8)
    coord = _coord(far=Radiod(id="far", host="far.example"))
    sysview.resolve_radiod_samprates(coord, tmp_path)
    assert coord.radiods["far"].samprate_hz == 0

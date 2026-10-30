"""sigmond.sdr_map and `smd admin sdr list`: which RX888 is which radiod."""
import json
from types import SimpleNamespace

from sigmond import sdr_map
from sigmond.sdr_map import (PresentCard, conf_serial, instance_serials,
                             present_cards, report, serial_to_instance)


def _dev(root, name, pid, serial=None, speed="5000", busnum="4"):
    d = root / name
    d.mkdir()
    (d / "idVendor").write_text("04b4\n")
    (d / "idProduct").write_text(pid + "\n")
    if serial is not None:
        (d / "serial").write_text(serial + "\n")
    (d / "busnum").write_text(busnum + "\n")
    (d / "devnum").write_text("5\n")
    (d / "speed").write_text(speed + "\n")


def test_present_cards_reads_programmed_and_bootloader(tmp_path):
    _dev(tmp_path, "4-1.3", "00f1", serial="0009042d028f1016")
    _dev(tmp_path, "4-1.4", "00f3", speed="480")
    (tmp_path / "4-1.3:1.0").mkdir()
    other = tmp_path / "1-1"
    other.mkdir()
    (other / "idVendor").write_text("1d6b\n")
    (other / "idProduct").write_text("0002\n")
    cards = present_cards(tmp_path)
    assert cards == [
        PresentCard("0009042D028F1016", "4-1.3", "5000", True),
        PresentCard("", "4-1.4", "480", False),
    ]


def test_present_cards_missing_root(tmp_path):
    assert present_cards(tmp_path / "nope") == []


def test_conf_serial_ignores_commented_line():
    assert conf_serial('[rx888]\n# serial = "ABC"\n') == ""


def test_conf_serial_reads_rx888_section_only():
    text = '[airspy]\nserial = "AIR"\n[rx888]\ndevice = "rx888"\nserial = "abc123"\n[x]\nserial = "Z"\n'
    assert conf_serial(text) == "ABC123"
    assert conf_serial('[airspy]\nserial = "AIR"\n') == ""


def test_instance_serials_prefers_conf_then_coordination(tmp_path):
    (tmp_path / "radiod@a.conf").write_text('[rx888]\nserial = "aa"\n')
    (tmp_path / "radiod@b.conf").write_text('[rx888]\ndevice = "rx888"\n')
    (tmp_path / "radiod@c.conf").write_text('[rx888]\n')
    (tmp_path / "radiod@d.conf").write_text('[airspy]\nserial = "X"\n')
    coord = SimpleNamespace(radiods={
        "a": SimpleNamespace(sdr_serial="ignored"),
        "b": SimpleNamespace(sdr_serial=" bb "),
    })
    assert instance_serials(tmp_path, coord) == {"a": "AA", "b": "BB", "c": ""}
    assert instance_serials(tmp_path) == {"a": "AA", "b": "", "c": ""}


def test_serial_to_instance_excludes_blank():
    assert serial_to_instance({"a": "S1", "b": ""}) == {"S1": "a"}


def test_report_flags_swap_risk_only_with_two_instances():
    assert report([], {"a": ""}).problems == []
    r = report([], {"b": "", "a": ""})
    assert r.problems[:2] == [
        "radiod@a has no 'serial =' line, so it opens whichever RX888 is free: a reboot can swap antennas",
        "radiod@b has no 'serial =' line, so it opens whichever RX888 is free: a reboot can swap antennas",
    ]


def test_report_flags_missing_duplicate_unclaimed_and_slow():
    cards = [PresentCard("S1", "4-1", "5000", True),
             PresentCard("S2", "4-2", "480", True),
             PresentCard("S3", "4-3", "5000", True)]
    r = report(cards, {"x": "S1", "y": "S1", "z": "GONE", "w": "S2"})
    assert r.problems == [
        "radiod@z wants RX888 GONE, which is not on the USB bus",
        "RX888 S1 is named by more than one radiod: x,y",
        "RX888 S3 on 4-3 is not named by any radiod conf",
        "RX888 S2 on 4-2 is at only 480 Mb/s, so radiod will not open it",
    ]


def test_report_counts_bootloader_cards_without_mapping_them():
    cards = [PresentCard("S1", "4-1", "5000", True),
             PresentCard("", "4-2", "480", False)]
    r = report(cards, {"a": "S1", "b": "S1"})
    assert r.problems[-1] == \
        "1 RX888(s) still in the FX3 bootloader (no firmware yet, so no serial)"
    assert not any("4-2" in p for p in r.problems)


def _patch(monkeypatch, tmp_path, instances, cards, states="active"):
    from sigmond.commands import sdr
    sysfs = tmp_path / "sys"
    sysfs.mkdir()
    for i, (name, pid, serial) in enumerate(cards):
        _dev(sysfs, name, pid, serial=serial)
    conf = tmp_path / "radio"
    conf.mkdir()
    for inst, text in instances.items():
        (conf / f"radiod@{inst}.conf").write_text(text)
    monkeypatch.setattr(sdr, "SYSFS_ROOT", sysfs)
    monkeypatch.setattr(sdr, "CONFDIR", conf)
    monkeypatch.setattr(sdr, "load_coord", lambda: None)
    monkeypatch.setattr(sdr, "unit_state", lambda inst: states)
    return sdr


def test_cmd_sdr_list_json_and_exit_code(monkeypatch, capsys, tmp_path):
    sdr = _patch(monkeypatch, tmp_path,
                 {"a": '[rx888]\nserial = "S1"\n', "b": '[rx888]\nserial = "S2"\n'},
                 [("4-1", "00f1", "S1")])
    rc = sdr.cmd_sdr_list(SimpleNamespace(json=True))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["instances"] == {"a": "S1", "b": "S2"}
    assert out["cards"][0]["opened_by"] == "a"
    assert out["cards"][0]["serial"] == "S1"
    assert out["problems"] == ["radiod@b wants RX888 S2, which is not on the USB bus"]


def test_cmd_sdr_list_all_clear_table(monkeypatch, capsys, tmp_path):
    sdr = _patch(monkeypatch, tmp_path,
                 {"a": '[rx888]\nserial = "S1"\ndescription = "north"\n'},
                 [("4-1", "00f1", "S1"), ("4-2", "00f3", None)])
    rc = sdr.cmd_sdr_list(SimpleNamespace(json=False))
    out = capsys.readouterr().out
    assert rc == 1  # bootloader line is a problem
    assert "(bootloader)" in out and "north" in out and "Needs attention:" in out


def test_cmd_sdr_list_clean(monkeypatch, capsys, tmp_path):
    sdr = _patch(monkeypatch, tmp_path,
                 {"a": '[rx888]\nserial = "S1"\n'}, [("4-1", "00f1", "S1")])
    assert sdr.cmd_sdr_list(SimpleNamespace(json=False)) == 0
    assert ("Every local radiod names its own RX888 and every RX888 is on the bus."
            in capsys.readouterr().out)

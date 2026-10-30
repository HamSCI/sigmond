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


# ---- identify: pure helpers -------------------------------------------------

import pytest
from sigmond.sdr_map import (AmbiguousDrop, set_conf_serial, set_coord_serial,
                             wait_for_drop, wait_for_return)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _seq(*sets):
    """A poll that yields each scripted set in turn, then repeats the last."""
    items = [set(s) for s in sets]
    def poll():
        return set(items.pop(0)) if len(items) > 1 else set(items[0])
    return poll


def test_wait_for_drop_returns_the_one_missing_serial():
    c = _Clock()
    poll = _seq({"A", "B"}, {"A", "B"}, {"B"})
    assert wait_for_drop({"A", "B"}, poll, 10, c, c.sleep) == "A"
    assert c.t == 0.5  # two sleeps of 0.25 s


def test_wait_for_drop_two_missing_raises_ambiguous():
    c = _Clock()
    with pytest.raises(AmbiguousDrop) as e:
        wait_for_drop({"A", "B", "C"}, _seq({"C"}), 10, c, c.sleep)
    assert e.value.serials == ["A", "B"]


def test_wait_for_drop_times_out_to_none():
    c = _Clock()
    assert wait_for_drop({"A"}, _seq({"A"}), 2, c, c.sleep) is None
    assert c.t >= 2


def test_wait_for_return():
    c = _Clock()
    assert wait_for_return("A", _seq(set(), set(), {"A"}), 10, c, c.sleep)
    c = _Clock()
    assert not wait_for_return("A", _seq(set()), 1, c, c.sleep)


def test_set_conf_serial_replaces_commented_line_in_rx888_section():
    text = ('[airspy]\nserial = "AIR"\n[rx888]\ndevice = "rx888"\n'
            '# serial  = "OLD"   # sole SDR\nsample = 1\n')
    out = set_conf_serial(text, "NEW")
    assert out == ('[airspy]\nserial = "AIR"\n[rx888]\ndevice = "rx888"\n'
                   'serial      = "NEW"\nsample = 1\n')
    assert set_conf_serial('[rx888]\nserial = "X"\n', "Y") == \
        '[rx888]\nserial      = "Y"\n'


def test_set_conf_serial_inserts_when_absent_and_refuses_without_section():
    assert set_conf_serial('[rx888]\ndevice = "rx888"\n', "S") == \
        '[rx888]\nserial      = "S"\ndevice = "rx888"\n'
    with pytest.raises(ValueError):
        set_conf_serial('[airspy]\nserial = "A"\n', "S")


def test_set_coord_serial_replaces_inserts_and_refuses():
    text = ('[radiod.a]\nsdr_serial = "OLD"\nx = 1\n\n'
            '[radiod.b]\nx = 2\n')
    out = set_coord_serial(text, "a", "NEW")
    assert out.startswith('[radiod.a]\nsdr_serial  = "NEW"\nx = 1\n')
    assert '"OLD"' not in out
    out = set_coord_serial(text, "b", "S2")
    assert out.endswith('[radiod.b]\nsdr_serial  = "S2"\nx = 2\n')
    assert 'sdr_serial = "OLD"' in out
    with pytest.raises(ValueError):
        set_coord_serial(text, "c", "S")


# ---- identify: the CLI walk ---------------------------------------------------

CONF_A = '[rx888]\ndevice = "rx888"\ndescription = "north"\n'
CONF_B = '[rx888]\ndevice = "rx888"\ndescription = "south"\n'
COORD = '[radiod.a]\nx = 1\n\n[radiod.b]\nx = 2\n'


def _walk(monkeypatch, tmp_path, polls, confs=None):
    from sigmond.commands import sdr
    conf = tmp_path / "radio"
    conf.mkdir()
    for inst, text in (confs or {"a": CONF_A, "b": CONF_B}).items():
        (conf / f"radiod@{inst}.conf").write_text(text)
    coord = tmp_path / "coordination.toml"
    coord.write_text(COORD)
    calls = []
    clock = _Clock()
    monkeypatch.setattr(sdr, "CONFDIR", conf)
    monkeypatch.setattr(sdr, "COORD_PATH", coord)
    monkeypatch.setattr(sdr, "load_coord", lambda: None)
    monkeypatch.setattr(sdr, "_systemctl", lambda *a: calls.append(a) or 0)
    monkeypatch.setattr(sdr, "present_serials", _seq(*polls))
    monkeypatch.setattr(sdr, "_clock", clock)
    monkeypatch.setattr(sdr, "_sleep", clock.sleep)
    monkeypatch.setenv("SIGMOND_SDR_IDENTIFY_WAIT_SECS", "30")
    return sdr, conf, coord, calls


# initial check, step a drop, a returns, step b drop, b returns
_GOOD = [{"S1", "S2"}, {"S1", "S2"}, {"S2"}, {"S2"}, {"S1", "S2"},
         {"S1", "S2"}, {"S1"}, {"S1", "S2"}]


def test_identify_refuses_single_instance(monkeypatch, capsys, tmp_path):
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, _GOOD,
                                    confs={"a": CONF_A})
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=True)) == 1
    assert "one radiod needs no identify; its conf is unbound by design" \
        in capsys.readouterr().err
    assert calls == []


def test_identify_dry_run_writes_nothing(monkeypatch, capsys, tmp_path):
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, _GOOD)
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=True)) == 0
    out = capsys.readouterr().out
    assert "Unplug the RX888 on: north  (radiod@a)" in out
    assert "Plug it back in" in out
    assert "INSTANCE" in out and "S1" in out and "S2" in out
    assert (conf / "radiod@a.conf").read_text() == CONF_A
    assert (conf / "radiod@b.conf").read_text() == CONF_B
    assert coord.read_text() == COORD
    stops = [("stop", "sigmond-radiod-watchdog.timer"),
             ("stop", "radiod@a.service"), ("stop", "radiod@b.service")]
    starts = [("start", "radiod@a.service"), ("start", "radiod@b.service"),
              ("start", "sigmond-radiod-watchdog.timer")]
    assert calls == stops + starts


def test_identify_writes_conf_and_coordination(monkeypatch, tmp_path):
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, _GOOD)
    monkeypatch.setattr(sdr.os, "geteuid", lambda: 0)
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=False)) == 0
    assert 'serial      = "S1"' in (conf / "radiod@a.conf").read_text()
    assert 'serial      = "S2"' in (conf / "radiod@b.conf").read_text()
    t = coord.read_text()
    assert '[radiod.a]\nsdr_serial  = "S1"' in t
    assert '[radiod.b]\nsdr_serial  = "S2"' in t
    assert calls[-1] == ("start", "sigmond-radiod-watchdog.timer")


def test_identify_needs_root_to_write(monkeypatch, capsys, tmp_path):
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, _GOOD)
    monkeypatch.setattr(sdr.os, "geteuid", lambda: 1000)
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=False)) == 1
    assert "run it as root" in capsys.readouterr().err
    assert calls == []


def test_identify_two_dropped_at_once_reprompts_then_pins_the_one(
        monkeypatch, capsys, tmp_path):
    polls = [{"S1", "S2"},          # initial check
             {"S1", "S2"},          # step a baseline
             set(),                 # both vanish: ambiguous
             {"S1"}, {"S1", "S2"},  # operator restores them
             {"S1", "S2"},          # step a baseline again
             {"S2"},                # now only S1 drops
             {"S2"}, {"S1", "S2"},  # return
             {"S1", "S2"}, {"S1"}, {"S1", "S2"}]  # step b
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, polls)
    monkeypatch.setattr(sdr.os, "geteuid", lambda: 0)
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=False)) == 0
    out = capsys.readouterr().out
    assert "S1, S2" in out and "plug" in out.lower()
    assert out.count("Unplug the RX888 on: north") == 2
    assert 'serial      = "S1"' in (conf / "radiod@a.conf").read_text()
    assert 'serial      = "S2"' in (conf / "radiod@b.conf").read_text()


def test_identify_timeout_abandons_and_writes_nothing(monkeypatch, capsys, tmp_path):
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, [{"S1", "S2"}])
    monkeypatch.setattr(sdr.os, "geteuid", lambda: 0)
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=False)) == 3
    assert "abandoned" in capsys.readouterr().out
    assert (conf / "radiod@a.conf").read_text() == CONF_A
    assert coord.read_text() == COORD
    assert calls[-1] == ("start", "sigmond-radiod-watchdog.timer")


def test_identify_pin_failure_writes_nothing_and_exits_4(monkeypatch, capsys, tmp_path):
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, _GOOD)
    monkeypatch.setattr(sdr.os, "geteuid", lambda: 0)
    inner = sdr.present_serials
    n = []

    def poll():
        n.append(1)
        if len(n) == 7:  # mid-walk, radiod@b's conf loses its [rx888] section
            (conf / "radiod@b.conf").write_text('[airspy]\nserial = "X"\n')
        return inner()
    monkeypatch.setattr(sdr, "present_serials", poll)
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=False)) == 4
    assert "nothing written" in capsys.readouterr().err
    assert (conf / "radiod@a.conf").read_text() == CONF_A
    assert coord.read_text() == COORD
    assert calls[-1] == ("start", "sigmond-radiod-watchdog.timer")


def test_identify_refuses_when_fewer_cards_than_instances(monkeypatch, capsys, tmp_path):
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, [{"S1"}])
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=True)) == 1
    assert "fewer RX888 cards" in capsys.readouterr().err
    assert calls == []


def test_identify_reprompts_when_same_card_unplugged_twice(monkeypatch, capsys, tmp_path):
    two = {"S1", "S2"}
    polls = [two, two, {"S2"}, two,        # init, a baseline, S1 drops, returns
             two, {"S2"}, two,             # b baseline, S1 again, returns
             two, {"S1"}, two]             # b baseline, S2 drops, returns
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, polls)
    monkeypatch.setattr(sdr.os, "geteuid", lambda: 0)
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=False)) == 0
    assert "already matched" in capsys.readouterr().out
    assert 'serial      = "S1"' in (conf / "radiod@a.conf").read_text()
    assert 'serial      = "S2"' in (conf / "radiod@b.conf").read_text()

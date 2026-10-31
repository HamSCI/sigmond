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
    monkeypatch.setattr(sdr, "held_serials", lambda insts: {})
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


def _walk(monkeypatch, tmp_path, polls, confs=None, inactive=()):
    from sigmond.commands import sdr
    conf = tmp_path / "radio"
    conf.mkdir()
    for inst, text in (confs if confs is not None
                       else {"a": CONF_A, "b": CONF_B}).items():
        (conf / f"radiod@{inst}.conf").write_text(text)
    coord = tmp_path / "coordination.toml"
    coord.write_text(COORD)
    calls = []
    clock = _Clock()
    monkeypatch.setattr(sdr, "CONFDIR", conf)
    monkeypatch.setattr(sdr, "COORD_PATH", coord)
    monkeypatch.setattr(sdr, "load_coord", lambda: None)
    monkeypatch.setattr(sdr, "_systemctl", lambda *a: calls.append(a) or 0)
    monkeypatch.setattr(sdr, "_is_active", lambda unit: unit not in inactive)
    monkeypatch.setattr(sdr, "SYSFS_ROOT", tmp_path / "no-sysfs")
    monkeypatch.setattr(sdr, "present_serials", _seq(*polls))
    monkeypatch.setattr(sdr, "_clock", clock)
    monkeypatch.setattr(sdr, "_sleep", clock.sleep)
    monkeypatch.setenv("SIGMOND_SDR_IDENTIFY_WAIT_SECS", "30")
    return sdr, conf, coord, calls


# initial check, step a drop, a returns, step b drop, b returns
_GOOD = [{"S1", "S2"}, {"S1", "S2"}, {"S2"}, {"S2"}, {"S1", "S2"},
         {"S1", "S2"}, {"S1"}, {"S1", "S2"}]


def test_identify_refuses_single_instance(monkeypatch, capsys, tmp_path):
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, [{"S1"}],
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
    stops = [("stop", "sigmond-radiod-watchdog.service"),
             ("stop", "sigmond-radiod-watchdog.timer"),
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


# ---- fix wave: conf-only pinning, quoted coordination headers, sysfs serial --

from sigmond.sdr_map import conf_pinned_serials, sysfs_serial_for

HOST_UNPINNED = '[rx888]\ndevice = "rx888"\n# serial  = "AAA"   # sole SDR: unbound by design; uncomment to pin\n'
HOST_BBB = '[rx888]\ndevice = "rx888"\nserial      = "BBB"\n'


def _grown_host(tmp_path):
    """The reviewer's C1 reproduction: the first card was registered alone
    (commented serial), the second card got its own pinned conf."""
    (tmp_path / "radiod@host.conf").write_text(HOST_UNPINNED)
    (tmp_path / "radiod@host-rx888-bbb.conf").write_text(HOST_BBB)
    coord = SimpleNamespace(radiods={
        "host": SimpleNamespace(sdr_serial="AAA"),
        "host-rx888-bbb": SimpleNamespace(sdr_serial="BBB"),
    })
    cards = [PresentCard("AAA", "4-1", "5000", True),
             PresentCard("BBB", "4-2", "5000", True)]
    return coord, cards


def test_conf_pinned_serials_reads_conf_only(tmp_path):
    coord, _ = _grown_host(tmp_path)
    (tmp_path / "radiod@air.conf").write_text('[airspy]\nserial = "X"\n')
    assert conf_pinned_serials(tmp_path) == {"host": "", "host-rx888-bbb": "BBB"}
    # instance_serials keeps the coordination fallback for the watchdog.
    assert instance_serials(tmp_path, coord) == {"host": "AAA",
                                                 "host-rx888-bbb": "BBB"}


def test_report_flags_conf_unpinned_even_when_coordination_names_it(tmp_path):
    coord, cards = _grown_host(tmp_path)
    r = report(cards, instance_serials(tmp_path, coord),
               conf_pinned_serials(tmp_path))
    assert r.problems == [
        "radiod@host has no 'serial =' line, so it opens whichever RX888 "
        "is free: a reboot can swap antennas"]


def test_cmd_sdr_list_labels_coordination_only_serial(monkeypatch, capsys, tmp_path):
    from sigmond.commands import sdr
    coord, _ = _grown_host(tmp_path)
    sysfs = tmp_path / "sys"
    sysfs.mkdir()
    _dev(sysfs, "4-1", "00f1", serial="AAA")
    _dev(sysfs, "4-2", "00f1", serial="BBB")
    monkeypatch.setattr(sdr, "SYSFS_ROOT", sysfs)
    monkeypatch.setattr(sdr, "CONFDIR", tmp_path)
    monkeypatch.setattr(sdr, "load_coord", lambda: coord)
    monkeypatch.setattr(sdr, "unit_state", lambda inst: "active")
    assert sdr.cmd_sdr_list(SimpleNamespace(json=False)) == 1
    out = capsys.readouterr().out
    assert "AAA (coordination only, conf unpinned)" in out
    assert "radiod@host has no 'serial =' line" in out


def test_set_coord_serial_matches_quoted_header():
    text = ('[radiod."ac0g-b1-rx888a"]\nhost = "localhost"\n\n'
            "[radiod.'b']\nx = 2\n")
    out = set_coord_serial(text, "ac0g-b1-rx888a", "bbb")
    assert out.startswith('[radiod."ac0g-b1-rx888a"]\nsdr_serial  = "BBB"\n')
    out = set_coord_serial(text, "b", "S2")
    assert "[radiod.'b']\nsdr_serial  = \"S2\"\nx = 2\n" in out


def test_pin_in_table_ends_only_at_a_real_header():
    text = ('[radiod.a]\nlist = [\n  "x",\n[1, 2],\n]\nsdr_serial = "OLD"\n'
            '\n[[radiod.fragment]]\nsdr_serial = "KEEP"\n')
    out = set_coord_serial(text, "a", "NEW")
    assert 'sdr_serial  = "NEW"' in out and '"OLD"' not in out
    assert 'sdr_serial = "KEEP"' in out


def test_round_trip_through_the_init_renderers(tmp_path):
    from sigmond.commands import radiod_config
    plan = {"instance_id": "ac0g-b1-rx888a", "status_dns": "x-status.local",
            "description": "north", "frontend": "rx888",
            "frontend_defaults": radiod_config._profile_for("RX-888")["defaults"].rstrip(),
            "serial": "AAA",
            "serial_line": '# serial  = "AAA"   # sole SDR: unbound by design; uncomment to pin',
            "iface": "eth0", "target": tmp_path / "x.conf", "sdr_type": "RX-888"}
    coord = sdr_map.set_coord_serial(radiod_config._coord_block(plan),
                                     "ac0g-b1-rx888a", "bbb")
    assert 'sdr_serial  = "BBB"' in coord and '"AAA"' not in coord
    conf = sdr_map.set_conf_serial(radiod_config._render(plan), "bbb")
    assert conf_serial(conf) == "BBB"


def test_sysfs_serial_for_matches_bus_and_device(tmp_path):
    _dev(tmp_path, "4-1", "00f1", serial="abc1", busnum="4")
    (tmp_path / "4-1:1.0").mkdir()
    assert sysfs_serial_for(4, 5, tmp_path) == "ABC1"
    assert sysfs_serial_for("004", "005", tmp_path) == "ABC1"
    assert sysfs_serial_for(4, 6, tmp_path) == ""
    assert sysfs_serial_for(3, 5, tmp_path) == ""
    assert sysfs_serial_for(4, 5, tmp_path / "nope") == ""


def test_identify_zero_instances_says_run_init_first(monkeypatch, capsys, tmp_path):
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, _GOOD, confs={})
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=True)) == 1
    e = capsys.readouterr().err
    assert "no radiod conf" in e and "smd config init radiod" in e
    assert "one radiod needs no identify" not in e
    assert calls == []


def test_identify_restarts_only_what_was_running(monkeypatch, capsys, tmp_path):
    sdr, conf, coord, calls = _walk(
        monkeypatch, tmp_path, _GOOD,
        inactive={"radiod@b.service", "sigmond-radiod-watchdog.timer"})
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=True)) == 0
    assert [c for c in calls if c[0] == "start"] == [("start", "radiod@a.service")]


def test_identify_warns_about_a_slow_card_after_the_walk(monkeypatch, capsys, tmp_path):
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, _GOOD)
    sysfs = tmp_path / "sys"
    sysfs.mkdir()
    _dev(sysfs, "4-1", "00f1", serial="S1")
    _dev(sysfs, "4-2", "00f1", serial="S2", speed="480")
    monkeypatch.setattr(sdr, "SYSFS_ROOT", sysfs)
    monkeypatch.setattr(sdr.os, "geteuid", lambda: 0)
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=False)) == 0
    cap = capsys.readouterr()
    assert "RX888 S2 on 4-2 is at only 480 Mb/s" in cap.out + cap.err
    assert 'serial      = "S2"' in (conf / "radiod@b.conf").read_text()


def test_identify_wait_secs_flag_beats_env(monkeypatch):
    from sigmond.commands import sdr
    monkeypatch.setenv("SIGMOND_SDR_IDENTIFY_WAIT_SECS", "30")
    assert sdr._wait_secs(SimpleNamespace(wait_secs=7)) == 7
    assert sdr._wait_secs(SimpleNamespace(wait_secs=None)) == 30
    assert sdr._wait_secs(SimpleNamespace()) == 30
    monkeypatch.delenv("SIGMOND_SDR_IDENTIFY_WAIT_SECS")
    assert sdr._wait_secs(SimpleNamespace()) == 180


# ---- fix2: a second card on a formerly one-card station ----------------------

ROB_COORD = ('[radiod."AI6VN-status.local"]\nhost = "localhost"\n'
             'radio_conf = "/etc/radio/radiod@AI6VN.conf"\n\n'
             '[radiod.other]\nhost = "localhost"\n')


def test_coord_radiod_for_matches_radio_conf_then_id():
    rob = SimpleNamespace(id="AI6VN-status.local", sdr_serial="S1",
                          radio_conf="/etc/radio/radiod@AI6VN.conf")
    plain = SimpleNamespace(id="b", sdr_serial="S2", radio_conf="")
    coord = SimpleNamespace(radiods={"AI6VN-status.local": rob, "b": plain})
    assert sdr_map.coord_radiod_for(coord, "AI6VN") is rob
    assert sdr_map.coord_radiod_for(coord, "b") is plain
    assert sdr_map.coord_radiod_for(coord, "c") is None
    assert sdr_map.coord_radiod_for(None, "b") is None


def test_instance_serials_finds_coordination_keyed_by_status_name(tmp_path):
    (tmp_path / "radiod@AI6VN.conf").write_text('[rx888]\n# serial = "X"\n')
    rob = SimpleNamespace(id="AI6VN-status.local", sdr_serial=" s1 ",
                          radio_conf="/etc/radio/radiod@AI6VN.conf")
    coord = SimpleNamespace(radiods={"AI6VN-status.local": rob})
    assert instance_serials(tmp_path, coord) == {"AI6VN": "S1"}


def test_set_coord_serial_finds_table_by_radio_conf():
    out = set_coord_serial(ROB_COORD, "AI6VN", "0009002807411037")
    assert out.startswith('[radiod."AI6VN-status.local"]\n'
                          'sdr_serial  = "0009002807411037"\n')
    assert out.endswith('[radiod.other]\nhost = "localhost"\n')
    with pytest.raises(ValueError):
        set_coord_serial(ROB_COORD, "nope", "S")


def _usb_tree(tmp_path):
    sysfs = tmp_path / "sys"
    sysfs.mkdir()
    d = sysfs / "4-1.4"
    d.mkdir()
    (d / "busnum").write_text("4\n")
    (d / "devnum").write_text("16\n")
    (d / "serial").write_text("0009002807411037\n")
    fd = tmp_path / "fd"
    fd.mkdir()
    (fd / "0").symlink_to("/dev/null")
    (fd / "3").symlink_to("socket:[1234]")
    (fd / "9").symlink_to("/dev/bus/usb/004/016")
    return fd, sysfs


def test_held_serial_reads_the_usb_fd(tmp_path):
    fd, sysfs = _usb_tree(tmp_path)
    assert sdr_map.held_serial(fd, sysfs) == "0009002807411037"


def test_held_serial_blank_without_usb_fd_or_dir(tmp_path):
    fd, sysfs = _usb_tree(tmp_path)
    (fd / "9").unlink()
    assert sdr_map.held_serial(fd, sysfs) == ""
    assert sdr_map.held_serial(tmp_path / "nope", sysfs) == ""


def test_held_serial_blank_when_permission_denied(monkeypatch, tmp_path):
    from pathlib import Path
    fd, sysfs = _usb_tree(tmp_path)
    real = Path.iterdir

    def deny(self):
        if self == fd:
            raise PermissionError(13, "Permission denied")
        return real(self)
    monkeypatch.setattr(Path, "iterdir", deny)
    assert sdr_map.held_serial(fd, sysfs) == ""


ROB_CARDS = [PresentCard("0009002807411037", "4-1.4", "5000", True),
             PresentCard("0009061C028B352B", "4-2", "5000", True)]


def test_report_more_cards_than_confs_robs_station():
    r = report(ROB_CARDS, {"AI6VN": ""}, {"AI6VN": ""},
               held={"AI6VN": "0009002807411037"})
    assert r.problems == [
        "radiod@AI6VN has no 'serial =' line, so it opens whichever RX888 is free: a reboot can swap antennas",
        "RX888 0009061C028B352B on 4-2 is not named by any radiod conf",
        "2 RX888(s) on the bus but only 1 radiod conf(s): run `smd config init radiod` to register the rest",
    ]


def test_report_one_card_one_unpinned_conf_is_clear():
    assert report(ROB_CARDS[:1], {"AI6VN": ""}, {"AI6VN": ""}).problems == []


def test_cmd_sdr_list_robs_station(monkeypatch, capsys, tmp_path):
    sdr = _patch(monkeypatch, tmp_path,
                 {"AI6VN": '[rx888]\n# serial  = "0009002807411037"\n'},
                 [("4-1.4", "00f1", "0009002807411037"),
                  ("4-2", "00f1", "0009061C028B352B")])
    monkeypatch.setattr(sdr, "held_serials",
                        lambda insts: {"AI6VN": "0009002807411037"})
    rc = sdr.cmd_sdr_list(SimpleNamespace(json=False))
    out = capsys.readouterr().out
    assert rc == 1
    assert "Every local radiod" not in out
    assert ("2 RX888(s) on the bus but only 1 radiod conf(s): run "
            "`smd config init radiod` to register the rest") in out
    assert "RX888 0009061C028B352B on 4-2 is not named" in out
    assert "RX888 0009002807411037 on 4-1.4 is not named" not in out
    line = next(l for l in out.splitlines() if l.startswith("0009002807411037"))
    assert line.split()[-1] == "AI6VN"
    line = next(l for l in out.splitlines() if l.startswith("0009061C028B352B"))
    assert line.split()[-1] == "-"


def test_held_serials_uses_main_pid_and_proc(monkeypatch, tmp_path):
    from sigmond.commands import sdr
    fd, sysfs = _usb_tree(tmp_path)
    proc = tmp_path / "proc"
    (proc / "77").mkdir(parents=True)
    fd.rename(proc / "77" / "fd")
    monkeypatch.setattr(sdr, "PROC_ROOT", proc)
    monkeypatch.setattr(sdr, "SYSFS_ROOT", sysfs)
    monkeypatch.setattr(sdr, "main_pid",
                        lambda inst: 77 if inst == "AI6VN" else 0)
    assert sdr.held_serials(["AI6VN", "b"]) == {"AI6VN": "0009002807411037"}


def test_identify_refuses_more_cards_than_confs(monkeypatch, capsys, tmp_path):
    sdr, conf, coord, calls = _walk(monkeypatch, tmp_path, _GOOD,
                                    confs={"a": CONF_A})
    assert sdr.cmd_sdr_identify(SimpleNamespace(dry_run=True)) == 1
    assert ("2 RX888s on the bus but 1 radiod conf(s): run `smd config init "
            "radiod` to register the rest, then identify"
            in capsys.readouterr().err)
    assert calls == []

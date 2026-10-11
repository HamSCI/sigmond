"""The record of a station losing its RX888 (lib/sigmond/sdr_events.py).

One line per EPISODE, never per event: ND's loop of 2026-10-05 (1,708 radiod
restarts in 9.5 h) must make one line, and one vanish -- a udev removal,
radiod's failing restarts, sdr-recover's power cut -- must make one episode,
or a single loss would read as flapping.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond import sdr_events as ev  # noqa: E402

T0 = 1_760_000_000.0


def lines(p):
    return [json.loads(l) for l in p.read_text().splitlines()]


def good_line(opened=T0, last=T0, kinds=("radiod-exit",), count=1):
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    from datetime import datetime, timezone
    iso = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).strftime(fmt)
    return json.dumps({"opened": iso(opened), "last": iso(last), "kinds": list(kinds),
                       "count": count, "detail": "", "back": None})


# -- the write side --------------------------------------------------------------------

def test_a_first_loss_opens_one_episode(tmp_path):
    p = tmp_path / "e.jsonl"
    assert ev.record("radiod-exit", "ac0g core-dump dumped/ABRT", now=T0, path=p) == "opened"
    (d,) = lines(p)
    assert d["kinds"] == ["radiod-exit"] and d["count"] == 1 and d["back"] is None
    assert d["opened"].endswith("Z") and d["opened"] == d["last"]


def test_a_second_loss_inside_ten_minutes_grows_the_same_line(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-exit", now=T0, path=p)
    assert ev.record("radiod-exit", now=T0 + 599, path=p) == "grown"
    (d,) = lines(p)
    assert d["count"] == 2


def test_a_loss_after_ten_minutes_opens_another(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-exit", now=T0, path=p)
    assert ev.record("radiod-exit", now=T0 + 601, path=p) == "opened"
    assert len(lines(p)) == 2


def test_the_nd_restart_loop_makes_one_line(tmp_path):
    p = tmp_path / "e.jsonl"
    for i in range(1708):
        ev.record("radiod-exit", now=T0 + 20 * i, path=p)
    (d,) = lines(p)
    assert d["count"] == 1708


def test_one_vanish_is_one_episode_whatever_its_kinds(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("usb-remove", "8-1.2", now=T0, path=p)
    ev.record("radiod-exit", now=T0 + 1, path=p)
    ev.record("radiod-exit", now=T0 + 11, path=p)
    ev.record("power-cycle", "8-1p2", now=T0 + 30, path=p)
    assert ev.record("usb-add", "8-1.2", now=T0 + 60, path=p) == "back"
    (d,) = lines(p)
    assert d["kinds"] == ["power-cycle", "radiod-exit", "usb-remove"]
    assert d["count"] == 4
    assert d["back"] is not None


def test_a_watchdog_restart_joins_the_episode_like_any_loss(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-wedge", "radiod@ac0g.service no journal output for 700s", now=T0, path=p)
    assert ev.record("radiod-exit", now=T0 + 20, path=p) == "grown"
    (d,) = lines(p)
    assert d["kinds"] == ["radiod-exit", "radiod-wedge"] and d["count"] == 2
    assert ev.read_episodes(p, now=T0 + 30)[0].kinds == ["radiod-exit", "radiod-wedge"]


def test_a_card_that_drops_again_after_it_settled_opens_a_new_episode(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("usb-remove", now=T0, path=p)
    ev.record("usb-add", now=T0 + 5, path=p)
    assert ev.record("usb-remove", now=T0 + 300, path=p) == "opened"
    assert len(lines(p)) == 2


def test_a_radiod_exit_just_after_the_return_joins_and_keeps_the_return(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("usb-remove", now=T0, path=p)
    ev.record("usb-add", now=T0 + 40, path=p)
    assert ev.record("radiod-exit", now=T0 + 45, path=p) == "grown"
    assert lines(p)[0]["back"] is not None
    assert ev.record("usb-remove", now=T0 + 300, path=p) == "opened"


def test_an_arrival_with_no_loss_before_it_writes_nothing(tmp_path):
    p = tmp_path / "e.jsonl"
    assert ev.record("usb-add", "8-1.2", now=T0, path=p) is None
    assert not p.exists()


def test_episodes_older_than_seven_days_drop_on_the_next_write(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("usb-remove", now=T0, path=p)
    ev.record("usb-remove", now=T0 + ev.KEEP_S + 1, path=p)
    assert len(lines(p)) == 1


def test_the_file_never_passes_200_lines(tmp_path):
    p = tmp_path / "e.jsonl"
    for i in range(250):
        ev.record("usb-remove", now=T0 + 700 * i, path=p)
    got = lines(p)
    assert len(got) == ev.MAX_LINES
    assert got[0]["opened"] == ev._iso(T0 + 700 * 50)      # the oldest 50 went
    assert got[-1]["opened"] == ev._iso(T0 + 700 * 249)    # the newest stayed


def test_detail_is_printable_ascii_and_bounded(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-exit", "\x1b[31m" + "é" * 500, now=T0, path=p)
    d = lines(p)[0]["detail"]
    assert d.isascii() and d.isprintable() and len(d) <= ev.DETAIL_MAX


def test_detail_of_a_joining_event_is_cleaned_too(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-exit", now=T0, path=p)
    ev.record("radiod-exit", "\x1b[31m" + "é" * 500, now=T0 + 10, path=p)
    d = lines(p)[0]["detail"]
    assert d.isascii() and d.isprintable() and len(d) <= ev.DETAIL_MAX


def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(ValueError):
        ev.record("rm -rf", now=T0, path=tmp_path / "e.jsonl")


def test_the_file_stays_world_readable(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("usb-remove", now=T0, path=p)
    assert os.stat(p).st_mode & 0o777 == 0o644


# -- fix round 1: a return closes only an open episode; the clock may step back ---------

def test_a_stall_is_not_closed_by_a_boot_hours_later(tmp_path):
    p = tmp_path / "e.jsonl"
    for dt in (0, 10, 20):
        ev.record("radiod-exit", now=T0 + dt, path=p)
    assert ev.record("usb-add", "8-1.2", now=T0 + 8 * 3600, path=p) is None
    assert lines(p)[0]["back"] is None
    eps = ev.read_episodes(p, now=T0 + 8 * 3600)
    assert ev.assess(eps, True)[0] == "sdr-recovered"


def test_a_return_inside_the_gap_still_closes_the_episode(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-exit", now=T0, path=p)
    assert ev.record("usb-add", now=T0 + ev.EPISODE_GAP_S, path=p) == "back"
    assert lines(p)[0]["back"] is not None


def test_a_forty_minute_stall_then_a_return_reads_down_long(tmp_path):
    p = tmp_path / "e.jsonl"
    for i in range(241):
        ev.record("radiod-exit", now=T0 + 10 * i, path=p)
    assert ev.record("usb-add", now=T0 + 2400 + 5, path=p) == "back"
    eps = ev.read_episodes(p, now=T0 + 2500)
    assert ev.assess(eps, True)[0] == "sdr-down-long"


def test_an_event_stamped_a_day_ahead_then_the_true_time_opens_another(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-exit", now=T0 + 86400, path=p)
    assert ev.record("radiod-exit", now=T0, path=p) == "opened"
    eps = ev.read_episodes(p, now=T0 + 100)
    assert len(eps) == 2
    assert all(ev.episode_span(e) >= 0 for e in eps)


def test_a_small_step_back_joins_and_leaves_last_alone(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-exit", now=T0 + 100, path=p)
    assert ev.record("radiod-exit", now=T0 + 90, path=p) == "grown"
    (d,) = lines(p)
    assert d["count"] == 2 and d["last"] == ev._iso(T0 + 100)


def test_the_file_is_synced_before_it_replaces_the_old_one(tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    ev.record("usb-remove", now=T0, path=tmp_path / "e.jsonl")
    assert synced


# -- the read side ---------------------------------------------------------------------

def test_damaged_and_hostile_lines_are_skipped(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text("\n".join([
        "not json",
        json.dumps([1, 2]),
        good_line(kinds=("rm -rf",)),
        good_line(count=True),
        good_line(count=0),
        '{"kinds": ["usb-remove"], "count": 1}',
        good_line(),
    ]) + "\n")
    eps = ev.read_episodes(p, now=T0 + 10)
    assert len(eps) == 1 and eps[0].kinds == ["radiod-exit"]
    ev.record("usb-remove", now=T0 + 3600, path=p)
    assert len(lines(p)) == 2


def test_read_episodes_keeps_the_last_24_hours(tmp_path):
    p = tmp_path / "e.jsonl"
    ev.record("radiod-exit", now=T0, path=p)
    ev.record("radiod-exit", now=T0 + 90_000, path=p)
    eps = ev.read_episodes(p, now=T0 + 90_010)
    assert len(eps) == 1


def test_read_of_a_missing_file_is_empty(tmp_path):
    assert ev.read_episodes(tmp_path / "none.jsonl", now=T0) == []


# -- is the card on the bus ------------------------------------------------------------

def make_dev(root, name, vid, pid):
    d = root / name
    d.mkdir(parents=True)
    (d / "idVendor").write_text(vid + "\n")
    (d / "idProduct").write_text(pid + "\n")


def test_the_running_card_counts_as_present(tmp_path):
    make_dev(tmp_path, "8-1.2", "04b4", "00f1")
    assert ev.card_present(tmp_path)


def test_the_bootloader_counts_as_present(tmp_path):
    make_dev(tmp_path, "8-1.2", "04b4", "00f3")
    assert ev.card_present(tmp_path)


def test_other_devices_do_not_count(tmp_path):
    make_dev(tmp_path, "6-1.3", "239a", "80cb")
    (tmp_path / "usb6").mkdir()
    assert not ev.card_present(tmp_path)


def test_no_usb_tree_means_no_card(tmp_path):
    assert not ev.card_present(tmp_path / "none")


# -- the verdict -----------------------------------------------------------------------

def ep(opened, kinds=("radiod-exit",), count=1, detail="", span=0, back=None):
    return ev.Episode(opened=opened, last=opened + span, kinds=list(kinds), count=count,
                      detail=detail, back=back)


def test_nothing_to_assess_without_episodes():
    assert ev.assess([], True) is None
    assert ev.assess([], False) is None


def test_one_or_two_episodes_with_the_card_back_read_recovered():
    assert ev.assess([ep(T0)], True)[0] == "sdr-recovered"
    assert ev.assess([ep(T0), ep(T0 + 3600)], True)[0] == "sdr-recovered"


def test_three_episodes_read_flapping():
    assert ev.assess([ep(T0), ep(T0 + 3600), ep(T0 + 7200)], True)[0] == "sdr-flapping"


def test_the_card_off_the_bus_reads_lost_whatever_the_count():
    assert ev.assess([ep(T0, kinds=("usb-remove",))], False)[0] == "sdr-lost"


def test_the_detail_names_how_often_and_when_in_utc():
    kind, detail = ev.assess([ep(T0), ep(T0 + 3600, detail="ac0g core-dump dumped/ABRT")], True)
    assert "2 time(s) in 24 h" in detail
    assert "Z" in detail and "ABRT" in detail


# -- the 30-minute rule (Michael, 2026-10-11) -------------------------------------------

def test_an_episode_open_longer_than_30_minutes_reads_down_long():
    kind, detail = ev.assess([ep(T0, span=1801)], True)
    assert kind == "sdr-down-long"
    assert "30 min" in detail


def test_exactly_30_minutes_stays_recovered():
    assert ev.assess([ep(T0, span=1800)], True)[0] == "sdr-recovered"


def test_a_return_after_30_minutes_counts_as_long():
    # The card left, radiod failed a few starts, then nothing came until the
    # card returned 31 minutes after the loss.
    long = ep(T0, kinds=("usb-remove",), span=60, back=T0 + 1860)
    assert ev.episode_span(long) == 1860
    assert ev.assess([long], True)[0] == "sdr-down-long"


def test_a_long_episode_earlier_in_the_day_still_reads_down_long():
    assert ev.assess([ep(T0, span=3600), ep(T0 + 7200)], True)[0] == "sdr-down-long"


def test_a_long_episode_names_itself_before_flapping():
    eps = [ep(T0), ep(T0 + 3600, span=2400), ep(T0 + 7200)]
    assert ev.assess(eps, True)[0] == "sdr-down-long"


def test_the_card_off_the_bus_reads_lost_even_when_the_episode_ran_long():
    assert ev.assess([ep(T0, kinds=("usb-remove",), span=3600)], False)[0] == "sdr-lost"


def test_the_nd_restart_loop_reads_down_long(tmp_path):
    p = tmp_path / "e.jsonl"
    for i in range(1708):
        ev.record("radiod-exit", now=T0 + 20 * i, path=p)
    eps = ev.read_episodes(p, now=T0 + 20 * 1708)
    assert ev.assess(eps, True)[0] == "sdr-down-long"


@pytest.mark.parametrize("result,loss", [
    ("success", False), ("timeout", False), ("", False), ("start-limit-hit", False),
    ("exit-code", True), ("signal", True), ("core-dump", True), ("watchdog", True)])
def test_which_radiod_exits_count_as_a_loss(result, loss):
    assert ev.radiod_exit_is_loss(result) is loss

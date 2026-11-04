from datetime import datetime, timedelta, timezone

import pytest

from sigmond.storage.config import (
    hands_off_active, hands_off_held, load_storage_config, render_storage_toml,
    with_directive, with_hands_off, with_share,
)
from sigmond.storage.model import Levels


def test_missing_file_gives_defaults(tmp_path):
    c = load_storage_config(tmp_path / "none.toml")
    assert c.levels == Levels() and c.problems == () and c.shares == {}


def test_levels_out_of_order_fall_back_and_say_why(tmp_path):
    p = tmp_path / "s.toml"; p.write_text("[levels]\nwatch = 90\ntighten = 85\n")
    c = load_storage_config(p)
    assert c.levels == Levels() and any("levels" in m for m in c.problems)


def test_bad_keys_drop_singly(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('[shares]\na = -1\nb = 2\n[directives]\nx = "delete-everything"\ny = "stop-writing"\n'
                 '[hands_off]\nuntil = "tomorrow"\n')
    c = load_storage_config(p)
    assert c.shares == {"b": 2.0} and c.directives == {"y": "stop-writing"}
    assert c.hands_off_until is None and len(c.problems) == 3


def test_unparseable_toml_gives_defaults_and_a_problem(tmp_path):
    p = tmp_path / "s.toml"; p.write_text("[levels\n")
    assert load_storage_config(p).problems


def test_hands_off_expires(tmp_path):
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    c = with_hands_off(load_storage_config(tmp_path / "n"), now + timedelta(hours=24), "A/B run", now)
    assert hands_off_active(c, now) and not hands_off_active(c, now + timedelta(hours=25))
    with pytest.raises(ValueError):
        with_hands_off(c, now + timedelta(days=15), "", now)


def test_render_round_trips(tmp_path):
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    c = with_directive(with_share(load_storage_config(tmp_path / "n"), "hf-timestd", 2.0), "codar-sounder", "stop-writing")
    c = with_hands_off(c, now + timedelta(hours=3), "test", now)
    p = tmp_path / "s.toml"; p.write_text(render_storage_toml(c))
    assert load_storage_config(p) == c


def test_with_directive_rejects_bad_policy_and_none_clears(tmp_path):
    c = load_storage_config(tmp_path / "n")
    with pytest.raises(ValueError):
        with_directive(c, "x", "rm-rf")
    c = with_directive(c, "x", "give-up-oldest:raw")
    assert with_directive(c, "x", None).directives == {}
    assert with_share(with_share(c, "a", 1.0), "a", None).shares == {}


def test_horizon_defaults_parses_and_round_trips(tmp_path):
    assert load_storage_config(tmp_path / "none.toml").horizon_h == 24.0
    p = tmp_path / "s.toml"; p.write_text("[levels]\nwatch = 70\nhorizon_h = 48\n")
    c = load_storage_config(p)
    assert c.horizon_h == 48.0 and c.levels.watch == 70.0 and c.problems == ()
    p.write_text(render_storage_toml(c))
    assert load_storage_config(p) == c


def test_a_bad_horizon_drops_alone(tmp_path):
    for bad in ("0", "-5", '"soon"', "true"):
        p = tmp_path / "s.toml"; p.write_text(f"[levels]\nwatch = 70\nhorizon_h = {bad}\n")
        c = load_storage_config(p)
        assert c.horizon_h == 24.0 and c.levels.watch == 70.0
        assert any("horizon_h" in m for m in c.problems)


def test_the_stop_lever_is_off_by_default_and_parses(tmp_path):
    assert load_storage_config(tmp_path / "none.toml").stop_enabled is False
    p = tmp_path / "s.toml"; p.write_text("[stop]\nenabled = true\n")
    c = load_storage_config(p)
    assert c.stop_enabled is True and c.problems == ()
    p.write_text(render_storage_toml(c))
    assert load_storage_config(p) == c
    p.write_text('[stop]\nenabled = "yes"\n')
    c = load_storage_config(p)
    assert c.stop_enabled is False and any("stop.enabled" in m for m in c.problems)


# ---- hands-off fails closed (ruling) ----------------------------------------

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("body", [
    '[hands_off]\nuntil = "tomorrow"\n',
    '[hands_off]\nuntil = "2026-11-05T12:00:00"\n',        # naive
    '[hands_off]\nreason = "A/B run"\n',                     # no until
    'hands_off = "yes"\n',                                   # not a table
    '[hands_off]\nuntil = 7\n',
])
def test_a_hands_off_table_that_does_not_parse_is_unknown(tmp_path, body):
    p = tmp_path / "s.toml"; p.write_text(body)
    c = load_storage_config(p, now=NOW)
    assert c.hands_off_unknown and c.hands_off_until is None
    assert hands_off_held(c, NOW)


def test_an_unparseable_or_unreadable_file_leaves_hands_off_unknown(tmp_path):
    p = tmp_path / "s.toml"; p.write_text("[levels\n")
    assert load_storage_config(p, now=NOW).hands_off_unknown
    d = tmp_path / "dir.toml"; d.mkdir()                     # read raises IsADirectoryError
    assert load_storage_config(d, now=NOW).hands_off_unknown


def test_no_file_and_no_table_mean_no_hands_off(tmp_path):
    assert not load_storage_config(tmp_path / "none.toml", now=NOW).hands_off_unknown
    p = tmp_path / "s.toml"; p.write_text("[shares]\na = 2\n")
    c = load_storage_config(p, now=NOW)
    assert not c.hands_off_unknown and not hands_off_held(c, NOW)


def test_a_far_future_hands_off_clamps_to_14_days(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('[hands_off]\nuntil = "2027-06-01T00:00:00Z"\nreason = "forever"\n')
    c = load_storage_config(p, now=NOW)
    assert c.hands_off_until == NOW + timedelta(days=14)
    assert not c.hands_off_unknown
    assert any("14 days" in m for m in c.problems)


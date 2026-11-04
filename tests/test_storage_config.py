from datetime import datetime, timedelta, timezone

import pytest

from sigmond.storage.config import (
    hands_off_active, load_storage_config, render_storage_toml,
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

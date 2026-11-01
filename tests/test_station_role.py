"""Tests for sigmond.station_role."""

import pytest

from sigmond.catalog import load_profiles
from sigmond.station_role import refusal, role, set_role


def test_missing_empty_or_garbage_file_is_full(tmp_path):
    p = tmp_path / "station-role"
    assert role(p) == "full"
    p.write_text("")
    assert role(p) == "full"
    p.write_text("decoder-ish\n")
    assert role(p) == "full"


def test_round_trip_and_idempotent(tmp_path):
    p = tmp_path / "station-role"
    assert set_role("radiod-only", p) is True
    assert role(p) == "radiod-only"
    assert set_role("radiod-only", p) is False


def test_set_role_creates_parent_and_mode(tmp_path):
    p = tmp_path / "sub" / "station-role"
    assert set_role("full", p) is True
    assert p.stat().st_mode & 0o777 == 0o644


def test_set_role_rejects_unknown(tmp_path):
    with pytest.raises(ValueError):
        set_role("client", tmp_path / "station-role")


def test_refusal():
    assert refusal("ka9q-radio", "radiod-only") is None
    assert refusal("wspr-recorder", "full") is None
    msg = refusal("wspr-recorder", "radiod-only")
    assert "wspr-recorder" in msg and "radiod-only" in msg


def test_catalog_profile():
    prof = load_profiles()["radiod-only"]
    assert prof.clients == ()
    assert prof.local_radiod_infra == ()
    assert set(prof.optional) == {"igmp-querier", "ka9q-web"}

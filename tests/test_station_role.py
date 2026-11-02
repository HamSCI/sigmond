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
    from sigmond import station_role as sr
    assert str(sr.ROLE_PATH) in msg and "full" in msg
    assert refusal("igmp-querier", "radiod-only") is None
    assert refusal("ka9q-web", "radiod-only") is None


def test_non_utf8_file_is_full_and_replaceable(tmp_path):
    p = tmp_path / "station-role"
    p.write_bytes(b"\xff\xfe")
    assert role(p) == "full"
    assert set_role("radiod-only", p) is True
    assert role(p) == "radiod-only"


def test_catalog_profile():
    prof = load_profiles()["radiod-only"]
    assert prof.clients == ()
    assert prof.local_radiod_infra == ()
    assert set(prof.optional) == {"igmp-querier", "ka9q-web"}


# -- final fix round, item 9: no test reads the host's role file --------------

def test_every_test_sees_a_tmp_role_file(tmp_path):
    import os
    from sigmond import station_role as sr
    assert sr.ROLE_PATH == tmp_path / "sigmond-test-station-role"
    assert os.environ["SIGMOND_STATION_ROLE_PATH"] == str(sr.ROLE_PATH)
    assert not sr.ROLE_PATH.exists()
    # Read at call time: no argument means the pinned path.
    assert sr.role() == "full"
    sr.ROLE_PATH.write_text("radiod-only\n")
    assert sr.role() == "radiod-only"
    assert sr.set_role("full") is True
    assert sr.ROLE_PATH.read_text() == "full\n"


def test_production_role_path_is_etc_sigmond_station_role():
    import os
    import subprocess
    import sys
    from pathlib import Path
    lib = Path(__file__).resolve().parent.parent / "lib"
    env = {k: v for k, v in os.environ.items() if k != "SIGMOND_STATION_ROLE_PATH"}
    r = subprocess.run([sys.executable, "-c",
                        f"import sys; sys.path.insert(0, {str(lib)!r}); "
                        "from sigmond import station_role as s; print(s.ROLE_PATH)"],
                       env=env, capture_output=True, text=True, timeout=30)
    assert r.stdout.strip() == "/etc/sigmond/station-role", r.stderr

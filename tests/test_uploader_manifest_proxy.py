"""[station] wsprnet_proxy: hand wsprnet posting to the WD servers.

The spots already travel in the wsprdaemon tar, so the station's second
upload stops existing.  Measured on AC0G/B4: 24.8 -> 5.8 MB/day, and a
500-spot/day site currently spends ~13 kB of its link per spot delivered
because every cycle opens a fresh TLS connection.

The property that matters most here is NOT the saving — it is that the
two halves move together.  Dropping the direct pipeline without flagging
the tar leaves nobody posting at all, and nothing reports an error.

Run with:  .venv/bin/pytest tests/test_uploader_manifest_proxy.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sigmond.coordination import Coordination, Host, Station  # noqa: E402
from sigmond.uploader_manifest import apply_proxying          # noqa: E402


def _coord(*, proxy=False, call="", grid="EM38ww"):
    return Coordination(host=Host(call="AC0G/B4", grid=grid),
                        station=Station(wsprnet_proxy=proxy,
                                        reporter_id="AC0G/B4",
                                        wsprnet_call=call))


def _pipes():
    return [
        {"name": "wspr-wsprdaemon",
         "transport": {"type": "wsprdaemon_tar", "version": "4.0"}},
        {"name": "wspr-wsprnet",
         "transport": {"type": "wsprnet", "version": "4.0"}},
        {"name": "psk-recorder-pskreporter",
         "transport": {"type": "pskreporter"}},
    ]


def _names(ps):
    return [p["name"] for p in ps]


def test_off_by_default_changes_NOTHING():
    """⛔ Every station in the fleet renders this manifest.  If the default
    touched a byte, enabling proxying would be a flag day for 85 sites
    instead of a per-operator decision."""
    out = apply_proxying(_pipes(), _coord(proxy=False))
    assert out == _pipes()


def test_off_emits_NOTHING_not_false():
    """Absent means 'leave the per-spot field alone' to the server, and
    hs-uploader writes that field 0 when unset — so off still switches
    off, without every station asserting something it never said."""
    out = apply_proxying(_pipes(), _coord(proxy=False))
    tar = next(p for p in out if p["name"] == "wspr-wsprdaemon")
    assert "forward_to_wsprnet" not in tar["transport"]


def test_on_flags_the_tar_and_drops_the_direct_post():
    out = apply_proxying(_pipes(), _coord(proxy=True))
    assert "wspr-wsprnet" not in _names(out)
    assert "psk-recorder-pskreporter" in _names(out)   # untouched
    tar = next(p for p in out if p["name"] == "wspr-wsprdaemon")
    assert tar["transport"]["forward_to_wsprnet"] is True


def test_A_MISSING_CARRIER_KEEPS_THE_DIRECT_PIPELINE():
    """⛔⛔ THE ONE THAT LOSES DATA.  If the tar pipeline is not being
    rendered — client disabled, pipeline renamed, deploy.toml changed —
    dropping the direct post leaves NOBODY posting: the station stops, the
    server never starts, and spots just stop appearing on wsprnet with no
    error anywhere.  Keeping the direct post merely forfeits the saving."""
    pipes = [p for p in _pipes() if p["name"] != "wspr-wsprdaemon"]
    out = apply_proxying(pipes, _coord(proxy=True))
    assert "wspr-wsprnet" in _names(out), "must keep posting for itself"


def test_a_malformed_carrier_also_keeps_the_direct_pipeline():
    pipes = [{"name": "wspr-wsprdaemon", "transport": None},
             {"name": "wspr-wsprnet", "transport": {"type": "wsprnet"}}]
    out = apply_proxying(pipes, _coord(proxy=True))
    assert "wspr-wsprnet" in _names(out)


def test_SWITCHING_TO_THE_PROXY_CANNOT_MOVE_THE_CREDIT(caplog):
    """⛔⛔ FOUND ON A LIVE STATION.  AC0G/B4 has [station] wsprnet_call =
    "AC0G" while it actually posts to wsprnet as "AC0G/B4" — the direct
    pipeline never read that key, it takes [identity] call.  An earlier
    version of this renderer honoured it, which would have silently moved
    a live station's wsprnet credit the moment it opted in.

    No identity is sent at all: the server posts under the spot's own
    rx_sign, which IS the reporter id the direct pipeline used, so the
    switch cannot change who gets credited."""
    import logging
    out = apply_proxying(_pipes(), _coord(proxy=True, call="AC0G"))
    tar = next(p for p in out if p["name"] == "wspr-wsprdaemon")
    assert "wsprnet_reporter_call" not in tar["transport"]
    assert "wsprnet_reporter_grid" not in tar["transport"]


def test_a_contradictory_wsprnet_call_is_reported_not_obeyed(caplog):
    """It reads as a request nobody honours, so say so rather than leave
    the operator believing it does something."""
    import logging
    with caplog.at_level(logging.WARNING):
        apply_proxying(_pipes(), _coord(proxy=True, call="AC0G"))
    msgs = [r.getMessage() for r in caplog.records]
    assert any("wsprnet_call" in m and "AC0G/B4" in m for m in msgs), msgs


def test_a_matching_wsprnet_call_says_nothing(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        apply_proxying(_pipes(), _coord(proxy=True, call="AC0G/B4"))
    assert not [r for r in caplog.records
                if "wsprnet_call" in r.getMessage()]


def test_proxying_does_not_mutate_the_caller_s_pipelines():
    pipes = _pipes()
    apply_proxying(pipes, _coord(proxy=True))
    assert _names(pipes) == ["wspr-wsprdaemon", "wspr-wsprnet",
                             "psk-recorder-pskreporter"]
    assert "forward_to_wsprnet" not in pipes[0]["transport"]


def test_the_manifest_keys_match_what_hs_uploader_READS():
    """⛔ CROSS-REPO: these key names are a contract with hs-uploader's
    pipeline_factory.  A rename on either side is silent — the station's
    config says it opted in, the tar says nothing, and the spots reach
    wsprnet by neither route.  Skips without an hs-uploader checkout."""
    src = (Path(__file__).resolve().parents[2] / "hs-uploader" / "src" /
           "hs_uploader" / "pipeline_factory.py")
    if not src.exists():
        import pytest
        pytest.skip("no hs-uploader checkout to compare against")
    text = src.read_text()
    for key in ("forward_to_wsprnet", "wsprnet_reporter_call",
                "wsprnet_reporter_grid"):
        assert f'"{key}"' in text, f"hs-uploader does not read {key}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))


# ---------- the setting has to survive `smd config render` ----------

def test_the_setting_survives_a_render_round_trip(tmp_path):
    """⛔ THE WHOLE POINT OF PUTTING IT HERE.  B4's hand-edited
    pipelines.toml is reverted by the next `smd apply`; a setting that
    lived only there would silently return the station to posting for
    itself, and the only symptom would be bandwidth quietly climbing back.
    site-profile.toml -> coordination.toml -> manifest must carry it."""
    from sigmond.site_profile import load_site_profile
    from sigmond.commands.config import _patch_station_block
    from sigmond.coordination import load_coordination

    prof = tmp_path / "site-profile.toml"
    prof.write_text(
        '[station]\ncallsign = "AC0G/B4"\ngrid = "EM38ww"\n\n'
        '[reporters]\nreporter_id = "AC0G/B4"\nwsprnet_proxy = true\n')
    sp = load_site_profile(prof)
    assert sp.wsprnet_proxy is True

    coord_path = tmp_path / "coordination.toml"
    coord_path.write_text('[host]\ncall = "AC0G/B4"\ngrid = "EM38ww"\n\n')
    _patch_station_block(coord_path, Station(reporter_id="AC0G/B4",
                                             wsprnet_proxy=True))
    text = coord_path.read_text()
    assert "wsprnet_proxy = true" in text
    assert load_coordination(coord_path).station.wsprnet_proxy is True


def test_a_profile_that_says_nothing_writes_nothing(tmp_path):
    from sigmond.commands.config import _patch_station_block
    coord_path = tmp_path / "coordination.toml"
    coord_path.write_text('[host]\ncall = "AC0G/B4"\n\n')
    _patch_station_block(coord_path, Station(reporter_id="AC0G/B4"))
    assert "wsprnet_proxy" not in coord_path.read_text()

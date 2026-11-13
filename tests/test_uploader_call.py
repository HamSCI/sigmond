"""[reporters] uploader_call: the name a machine uploads under is the operator's.

KFS-MAIN (2026-11-12) decodes for four radiods (KFS/OMNI, KFS/SW, KFS/SE,
KFS/NW).  reporter_call() took the first reporter id in sort order, so the
manifest named the machine KFS/NW.  Michael: sigmond may suggest a name, but
the operator chooses; this one is KFS-MAIN.  Each report still carries its
own reporter id.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond.coordination import (Coordination, Host, Station,  # noqa: E402
                                  load_coordination, write_station_identity)
from sigmond.uploader_manifest import reporter_call  # noqa: E402


def _inst(rid):
    return SimpleNamespace(client="wspr-recorder", reporter_id=rid)


FOUR = [_inst("KFS=OMNI"), _inst("KFS=SW"), _inst("KFS=SE"), _inst("KFS=NW")]


def test_the_operator_s_name_wins():
    coord = Coordination(host=Host(call="KFS"),
                         station=Station(uploader_call="KFS-MAIN"))
    assert reporter_call(coord, FOUR) == "KFS-MAIN"


def test_unset_keeps_today_s_derivation():
    coord = Coordination(host=Host(call="KFS"), station=Station())
    assert reporter_call(coord, [_inst("AC0G=B4")]) == "AC0G/B4"
    assert reporter_call(coord, []) == "KFS"


def test_site_profile_to_coordination_round_trip(tmp_path):
    from sigmond.commands.config import _patch_station_block
    from sigmond.site_profile import load_site_profile

    prof = tmp_path / "site-profile.toml"
    prof.write_text('[station]\ncallsign = "KFS"\ngrid = "CM87tj"\n\n'
                    '[reporters]\nuploader_call = "kfs-main"\n')
    assert load_site_profile(prof).uploader_call == "KFS-MAIN"

    coord_path = tmp_path / "coordination.toml"
    coord_path.write_text('[host]\ncall = "KFS"\n\n')
    _patch_station_block(coord_path, Station(uploader_call="KFS-MAIN"))
    assert 'uploader_call = "KFS-MAIN"' in coord_path.read_text()
    assert load_coordination(coord_path).station.uploader_call == "KFS-MAIN"


def test_unset_writes_nothing(tmp_path):
    from sigmond.commands.config import _patch_station_block
    coord_path = tmp_path / "coordination.toml"
    coord_path.write_text('[host]\ncall = "KFS"\n\n')
    _patch_station_block(coord_path, Station(reporter_id="KFS/MAIN"))
    assert "uploader_call" not in coord_path.read_text()


def test_the_station_writer_keeps_operator_decisions(tmp_path):
    """write_station_identity rewrites [station]; it must not drop the
    machine's name.  An old wsprnet_proxy key is retired and dropped."""
    coord_path = tmp_path / "coordination.toml"
    coord_path.write_text('[host]\ncall = "KFS"\n\n[station]\n'
                          'reporter_id = "KFS/MAIN"\nuploader_call = "KFS-MAIN"\n'
                          'wsprnet_proxy = true\n')
    write_station_identity("KFS/OMNI", path=coord_path)
    st = load_coordination(coord_path).station
    assert st.reporter_id == "KFS/OMNI"
    assert st.uploader_call == "KFS-MAIN"
    assert st.wsprnet_proxy is False


def test_set_reporters_keys_edits_only_its_keys():
    from sigmond.site_profile import set_reporters_keys
    text = ('[station]\ncallsign = "KFS"\n\n[reporters]\nreporter_id = "KFS/MAIN"\n'
            'wsprnet_call = ""\n\n[host]\nhostname = "kfs-main"\n')
    out = set_reporters_keys(text, uploader_call="KFS-MAIN", wsprnet_proxy=True)
    import tomllib
    rep = tomllib.loads(out)["reporters"]
    assert rep == {"reporter_id": "KFS/MAIN", "wsprnet_call": "",
                   "uploader_call": "KFS-MAIN", "wsprnet_proxy": True}
    assert tomllib.loads(out)["host"] == {"hostname": "kfs-main"}
    # None removes; "off" writes nothing, never false.
    back = set_reporters_keys(out, wsprnet_proxy=None)
    assert "wsprnet_proxy" not in back


def test_set_reporters_keys_adds_a_missing_table():
    from sigmond.site_profile import set_reporters_keys
    import tomllib
    out = set_reporters_keys('[station]\ncallsign = "KFS"\n', uploader_call="KFS-MAIN")
    assert tomllib.loads(out)["reporters"] == {"uploader_call": "KFS-MAIN"}


def test_config_reporters_dry_run(tmp_path, monkeypatch, capsys):
    import types
    from sigmond.commands import config as cfg
    prof = tmp_path / "site-profile.toml"
    prof.write_text('[station]\ncallsign = "KFS"\ngrid_square = "CM87tj"\n')
    monkeypatch.setattr(cfg, "SITE_PROFILE_PATH", prof)
    rc = cfg.cmd_config_reporters(types.SimpleNamespace(
        uploader_call="kfs-main", wsprnet_proxy="on", dry_run=True, no_render=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert 'uploader_call = "KFS-MAIN"' in out and "wsprnet_proxy" not in out
    assert "uploader_call" not in prof.read_text()      # dry run wrote nothing


def test_config_reporters_refuses_a_bad_name(tmp_path, monkeypatch):
    import types
    from sigmond.commands import config as cfg
    prof = tmp_path / "site-profile.toml"
    prof.write_text('[station]\ncallsign = "KFS"\n')
    monkeypatch.setattr(cfg, "SITE_PROFILE_PATH", prof)
    rc = cfg.cmd_config_reporters(types.SimpleNamespace(
        uploader_call="KFS MAIN; rm", wsprnet_proxy=None, dry_run=True, no_render=True))
    assert rc == 1


# ---- wsprnet_proxy retired (2026-11-13) ------------------------------------

def _profile_at(tmp_path, monkeypatch, extra=""):
    from sigmond.commands import config as cfg
    prof = tmp_path / "site-profile.toml"
    prof.write_text('[station]\ncallsign = "KFS"\ngrid_square = "CM87tj"\n\n'
                    '[reporters]\nuploader_call = "X"\n' + extra)
    monkeypatch.setattr(cfg, "SITE_PROFILE_PATH", prof)
    return cfg, prof


def test_reporters_wsprnet_proxy_writes_nothing_and_says_why(tmp_path, monkeypatch, capsys):
    cfg, prof = _profile_at(tmp_path, monkeypatch)
    before = prof.read_bytes()
    rc = cfg.cmd_config_reporters(SimpleNamespace(
        uploader_call=None, wsprnet_proxy="on", dry_run=False, no_render=True))
    cap = capsys.readouterr()
    assert rc == 0
    assert prof.read_bytes() == before
    assert "wsprnet_proxy is retired" in cap.out + cap.err or \
        "--wsprnet-proxy is retired" in cap.out + cap.err
    assert "wsprdaemon.org" in cap.out + cap.err


def test_reporters_wsprnet_proxy_beside_uploader_call_writes_only_the_name(
        tmp_path, monkeypatch):
    cfg, prof = _profile_at(tmp_path, monkeypatch)
    rc = cfg.cmd_config_reporters(SimpleNamespace(
        uploader_call="kfs-main", wsprnet_proxy="on", dry_run=False, no_render=True))
    assert rc == 0
    text = prof.read_text()
    assert 'uploader_call = "KFS-MAIN"' in text
    assert "wsprnet_proxy" not in text


def test_render_never_writes_wsprnet_proxy(tmp_path):
    from sigmond.commands.config import _patch_station_block
    coord_path = tmp_path / "coordination.toml"
    coord_path.write_text('[host]\ncall = "KFS"\n\n')
    _patch_station_block(coord_path, Station(reporter_id="KFS/MAIN",
                                             wsprnet_proxy=True))
    assert "wsprnet_proxy" not in coord_path.read_text()


def test_write_station_identity_drops_an_old_wsprnet_proxy(tmp_path):
    coord_path = tmp_path / "coordination.toml"
    coord_path.write_text('[host]\ncall = "AC0G"\n\n[station]\n'
                          'reporter_id = "AC0G/B4"\nwsprnet_proxy = true\n')
    write_station_identity(reporter_id="AC0G/B4", path=coord_path)
    assert "wsprnet_proxy" not in coord_path.read_text()


def test_render_warns_once_about_a_profile_wsprnet_proxy(tmp_path, monkeypatch, capsys):
    from sigmond import site_profile as sp
    cfg, prof = _profile_at(tmp_path, monkeypatch, "wsprnet_proxy = true\n")
    monkeypatch.setattr(sp, "SITE_PROFILE_PATH", prof)
    monkeypatch.setattr(cfg, "COORDINATION_PATH", tmp_path / "coordination.toml")
    monkeypatch.setattr(cfg, "COORDINATION_ENV", tmp_path / "coordination.env")
    cfg.cmd_config_render(SimpleNamespace(init=False, if_present=False, dry_run=True))
    cap = capsys.readouterr()
    hits = [ln for ln in (cap.out + cap.err).splitlines() if "wsprnet_proxy" in ln]
    assert len(hits) == 1
    assert "retired" in hits[0] and "delete" in hits[0].lower()


def test_profile_template_has_no_wsprnet_proxy():
    from sigmond import site_profile
    assert "wsprnet_proxy" not in site_profile.TEMPLATE

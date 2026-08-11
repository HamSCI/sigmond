"""smd timing display command — formatting + json against a fixture snapshot."""
import json, sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from sigmond.commands.timing_show import cmd_timing_show


def _write_snapshot(tmp_path):
    p = tmp_path / "authority.json"
    p.write_text(json.dumps({
        "schema": "v1", "utc_published": "2026-08-11T13:00:00Z",
        "a_level": "A1", "t_level_active": "T3",
        "t_level_available": ["T3", "T4"], "t_level_witnesses": ["T4"],
        "rtp_to_utc_offset_ns": 92012, "sigma_ns": 3260000,
        "stations_contributing": ["WWV", "WWVH"],
        "last_transition_utc": None, "disagreement_flags": [],
        "governor_radiod": "AC0G-B4-status.local",
    }))
    return p


def test_text_output(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sigmond.commands.timing_show.chrony_tracking", lambda: {})
    args = types.SimpleNamespace(path=str(_write_snapshot(tmp_path)), json=False)
    assert cmd_timing_show(args) == 0
    out = capsys.readouterr().out
    assert "tier       T3" in out
    assert "WWV, WWVH" in out
    assert "governor   AC0G-B4-status.local" in out
    assert "System clock (chrony)" in out


def test_json_output(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sigmond.commands.timing_show.chrony_tracking",
                        lambda: {"reference": "FUSE", "stratum": 1,
                                 "system_offset_s": 1.8e-6, "rms_offset_s": 7.2e-5,
                                 "root_dispersion_s": 1e-4, "leap_status": "Normal"})
    args = types.SimpleNamespace(path=str(_write_snapshot(tmp_path)), json=True)
    assert cmd_timing_show(args) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["authority"]["t_level_active"] == "T3"
    assert d["authority"]["rtp_to_utc_offset_ns"] == 92012
    assert d["chrony"]["reference"] == "FUSE"


def test_missing_snapshot(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sigmond.commands.timing_show.chrony_tracking", lambda: {})
    args = types.SimpleNamespace(path=str(tmp_path / "nope.json"), json=False)
    assert cmd_timing_show(args) == 0
    assert "no authority snapshot" in capsys.readouterr().out

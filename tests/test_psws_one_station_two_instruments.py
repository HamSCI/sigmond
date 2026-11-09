"""One PSWS station id, one instrument id per product (Michael, 2026-11-09).

The PSWS portal now lets one station id carry a magnetometer instrument and
an RX888 (GRAPE) instrument.  sigmond's primary case: one station id
(S000418) with hf-timestd = 367 and mag-recorder = 401.  A separate station
id per instrument must keep working.

On sigma (bare metal, 2026-11-09) `smd bringup ... --psws-station-id
S000418` finished with every recorder's station id unset: bring-up only
exported the id as an env var, and with no site-profile.toml the render step
skipped itself.  These tests pin the path from the flags to each recorder's
own config file.
"""
from __future__ import annotations

import importlib.util
import os
import types
from pathlib import Path
from unittest import mock

import pytest

import sigmond.site_profile as sp
from sigmond.site_profile import load_site_profile


def _write(tmp_path, text):
    p = tmp_path / "site-profile.toml"
    p.write_text(text)
    return p


# --- site_profile.set_psws_identity -----------------------------------------

def test_new_profile_holds_one_station_and_two_instruments(tmp_path):
    text = sp.set_psws_identity(
        None, station_id="S000418",
        instruments={"hf-timestd": "367", "mag-recorder": "401"},
        seed={"call": "AC0G", "grid": "EM38ww", "reporter_id": "AC0G/S"})
    prof = load_site_profile(_write(tmp_path, text))
    assert prof.psws_enabled
    assert prof.psws_station_id == "S000418"
    for rec in ("hf-timestd", "hamsci-physics", "mag-recorder"):
        assert prof.station_for(rec) == "S000418"
    assert prof.instrument_for("hf-timestd") == "367"
    assert prof.instrument_for("hamsci-physics") == "367"   # same GRAPE instrument
    assert prof.instrument_for("mag-recorder") == "401"
    assert prof.call == "AC0G" and prof.grid == "EM38ww"
    assert prof.effective_reporter_id == "AC0G/S"


def test_new_profile_without_a_callsign_has_no_station_table(tmp_path):
    """render refuses a [station] table with no callsign; a non-ham station
    still gets its PSWS ids rendered without one."""
    text = sp.set_psws_identity(None, station_id="S000418",
                                instruments={}, seed={"grid": "EM38ww"})
    p = _write(tmp_path, text)
    import tomllib
    assert "station" not in tomllib.loads(p.read_text())
    assert load_site_profile(p).psws_station_id == "S000418"


def test_existing_profile_keeps_its_other_blocks_and_instruments(tmp_path):
    old = ('[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n\n'
           '[psws]\nenabled = false\nstation_id = ""\n\n'
           '[psws.instruments]\n"mag-recorder" = "84"\n\n'
           '[reporters]\nreporter_id = "AC0G/S"\n\n'
           '[uploads]\nmode = "hold"\n\n'
           '[[receiver]]\nreporter_id = "AC0G/S"\nradiod = "x-status.local"\n')
    text = sp.set_psws_identity(old, station_id="s000418",
                                instruments={"hf-timestd": "367"})
    prof = load_site_profile(_write(tmp_path, text))
    assert prof.psws_enabled and prof.psws_station_id == "S000418"
    assert prof.instrument_for("hf-timestd") == "367"
    assert prof.instrument_for("mag-recorder") == "84"      # kept
    assert prof.effective_reporter_id == "AC0G/S"
    assert prof.uploads_mode == "hold"
    from sigmond.receivers import load_receivers
    assert [r.reporter_id for r in load_receivers(tmp_path / "site-profile.toml")] \
        == ["AC0G/S"]


def test_instrument_flag_alone_keeps_the_station_id(tmp_path):
    old = ('[psws]\nenabled = true\nstation_id = "S000418"\n')
    text = sp.set_psws_identity(old, station_id="",
                                instruments={"mag-recorder": "401"})
    prof = load_site_profile(_write(tmp_path, text))
    assert prof.psws_station_id == "S000418"
    assert prof.instrument_for("mag-recorder") == "401"


# --- a separate station id per instrument still works -----------------------

def test_per_instrument_station_override_is_honoured(capsys, tmp_path):
    p = _write(tmp_path,
               '[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n'
               '[psws]\nenabled = true\nstation_id = "S000170"\n'
               '[psws.instruments]\n"mag-recorder" = "84"\n'
               '[psws.stations]\n"mag-recorder" = "S000082"\n')
    prof = load_site_profile(p)
    assert prof.station_for("mag-recorder") == "S000082"
    assert prof.station_for("hf-timestd") == "S000170"
    assert prof.station_for("hamsci-physics") == "S000170"
    assert "IGNORED" not in capsys.readouterr().err


def test_set_psws_identity_writes_a_per_instrument_station(tmp_path):
    text = sp.set_psws_identity(
        None, station_id="S000418",
        instruments={"hf-timestd": "367", "mag-recorder": "84"},
        stations={"mag-recorder": "S000082"}, seed={"call": "AC0G",
                                                    "grid": "EM38ww"})
    prof = load_site_profile(_write(tmp_path, text))
    assert prof.station_for("mag-recorder") == "S000082"
    assert prof.station_for("hf-timestd") == "S000418"


def test_plan_psws_updates_pushes_the_override():
    from sigmond.commands.config import plan_psws_updates
    prof = sp.SiteProfile(psws_enabled=True, psws_station_id="S000170",
                          psws_instruments={"mag-recorder": "84"},
                          psws_stations={"mag-recorder": "S000082"})
    st = types.SimpleNamespace(station="S000170", instrument="")
    assert plan_psws_updates(prof, "mag-recorder", st) == [
        ("station", "psws_station_id", "S000082"),
        ("station", "instrument_id", "84")]


# --- flag parsing ------------------------------------------------------------

def test_parse_psws_assignments():
    assert sp.parse_psws_assignments(
        ["hf-timestd=367", "mag-recorder=401"], kind="instrument") == \
        {"hf-timestd": "367", "mag-recorder": "401"}
    assert sp.parse_psws_assignments(["mag-recorder=s000082"],
                                     kind="station") == \
        {"mag-recorder": "S000082"}
    # the GRAPE instrument has two names; hamsci-physics files under hf-timestd
    assert sp.parse_psws_assignments(["hamsci-physics=367"],
                                     kind="instrument") == {"hf-timestd": "367"}
    for bad in (["wspr-recorder=5"], ["hf-timestd"], ["hf-timestd=abc"],
                ["hf-timestd=367", "hf-timestd=368"]):
        with pytest.raises(ValueError):
            sp.parse_psws_assignments(bad, kind="instrument")
    with pytest.raises(ValueError):
        sp.parse_psws_assignments(["mag-recorder=401"], kind="station")


def test_normalize_station_id():
    assert sp.normalize_psws_station_id(" s000418 ") == "S000418"
    for bad in ("418", "S", "S00041X", "<YOUR_ID>"):
        with pytest.raises(ValueError):
            sp.normalize_psws_station_id(bad)


# --- smd config psws: write the profile, then render into each recorder -----

def _fake_recorder_configs(tmp_path):
    import sigmond.psws as psws
    cfgs = {}
    recorders = {}
    for name, spec in psws.RECORDERS.items():
        f = tmp_path / f"{name}.toml"
        sec, key = spec["station"]
        f.write_text(f'[{sec}]\n{key} = "<YOUR_STATION_ID>"\n'
                     f'instrument_id = "<YOUR_INSTRUMENT_ID>"\n')
        cfgs[name] = f
        recorders[name] = dict(spec, config=f)
    return cfgs, recorders


def test_config_psws_creates_the_profile_and_reaches_each_recorder(tmp_path,
                                                                  capsys):
    import tomllib
    import sigmond.commands.config as cfg
    import sigmond.psws as psws
    prof = tmp_path / "site-profile.toml"
    coord = tmp_path / "coordination.toml"
    coord.write_text('[host]\ncall = "AC0G"\ngrid = "EM38ww"\n\n'
                     '[station]\nreporter_id = "AC0G/S"\n')
    env = tmp_path / "coordination.env"
    cfgs, recorders = _fake_recorder_configs(tmp_path)
    args = types.SimpleNamespace(
        station="S000418",
        instrument=["hf-timestd=367", "mag-recorder=401"],
        instrument_station=[], no_render=False, dry_run=False)
    with mock.patch.object(sp, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(cfg, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(cfg, "COORDINATION_PATH", coord), \
            mock.patch.object(cfg, "COORDINATION_ENV", env), \
            mock.patch.object(psws, "RECORDERS", recorders), \
            mock.patch.object(psws, "_sudo", lambda: []), \
            mock.patch.object(psws, "host_key", lambda: str(tmp_path / "k")), \
            mock.patch.object(cfg, "_report_station_key", lambda p: None):
        rc = cfg.cmd_config_psws(args)
    out = capsys.readouterr()
    assert rc == 0, out.out + out.err
    p = load_site_profile(prof)
    assert p.call == "AC0G" and p.effective_reporter_id == "AC0G/S"
    hf = tomllib.loads(cfgs["hf-timestd"].read_text())["station"]
    assert hf == {"id": "S000418", "instrument_id": "367"}
    phys = tomllib.loads(cfgs["hamsci-physics"].read_text())["station"]
    assert phys == {"psws_station_id": "S000418", "instrument_id": "367"}
    mag = tomllib.loads(cfgs["mag-recorder"].read_text())["station"]
    assert mag == {"psws_station_id": "S000418", "instrument_id": "401"}
    st = tomllib.loads(coord.read_text())["station"]
    assert st["psws_id"] == "S000418"
    assert st["reporter_id"] == "AC0G/S"


def test_config_psws_refuses_bad_input_and_writes_nothing(tmp_path, capsys):
    import sigmond.commands.config as cfg
    prof = tmp_path / "site-profile.toml"
    args = types.SimpleNamespace(station="S000418",
                                 instrument=["wspr-recorder=5"],
                                 instrument_station=[], no_render=False,
                                 dry_run=False)
    with mock.patch.object(sp, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(cfg, "SITE_PROFILE_PATH", prof):
        assert cfg.cmd_config_psws(args) == 1
    assert not prof.exists()


# --- smd bringup carries the flags through ------------------------------------

def _smd():
    spec = importlib.util.spec_from_loader("smd_mod_psws1s2i", loader=None)
    smd = importlib.util.module_from_spec(spec)
    smd.__dict__["__file__"] = "bin/smd"
    src = Path(__file__).resolve().parent.parent / "bin" / "smd"
    with mock.patch.dict(os.environ, {"SIGMOND_NO_VENV_REEXEC": "1"}):
        exec(compile(src.read_text(), str(src), "exec"), smd.__dict__)
    return smd


def test_bringup_parser_takes_instrument_flags():
    smd = _smd()
    a = smd._build_parser().parse_args(
        ["bringup", "dasi2", "--psws-station-id", "S000418",
         "--psws-instrument", "hf-timestd=367",
         "--psws-instrument", "mag-recorder=401",
         "--psws-instrument-station", "mag-recorder=S000082"])
    assert a.psws_station_id == "S000418"
    assert a.psws_instrument == ["hf-timestd=367", "mag-recorder=401"]
    assert a.psws_instrument_station == ["mag-recorder=S000082"]
    assert smd._bringup_psws_argv(a) == [
        "config", "psws", "--station", "S000418",
        "--instrument", "hf-timestd=367", "--instrument", "mag-recorder=401",
        "--instrument-station", "mag-recorder=S000082"]


def test_bringup_without_psws_flags_writes_no_profile():
    smd = _smd()
    a = smd._build_parser().parse_args(["bringup", "dasi2"])
    assert smd._bringup_psws_argv(a) is None


def test_config_psws_parser():
    smd = _smd()
    a = smd._build_parser().parse_args(
        ["config", "psws", "--station", "S000418",
         "--instrument", "hf-timestd=367", "--instrument", "mag-recorder=401"])
    assert a.station == "S000418"
    assert a.instrument == ["hf-timestd=367", "mag-recorder=401"]
    assert a.instrument_station == []

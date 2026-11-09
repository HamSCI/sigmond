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


# --- review fixes (2026-11-09) -------------------------------------------------

def test_setting_one_grape_alias_drops_the_other(tmp_path):
    """instrument_for() reads a recorder's own key first, so a stale
    "hamsci-physics" entry would keep GRAPE uploading under the old id."""
    old = ('[psws]\nenabled = true\nstation_id = "S000418"\n\n'
           '[psws.instruments]\n"hamsci-physics" = "172"\n\n'
           '[psws.stations]\n"hamsci-physics" = "S000082"\n')
    text = sp.set_psws_identity(old, instruments={"hf-timestd": "367"},
                                stations={"hf-timestd": "S000099"})
    prof = load_site_profile(_write(tmp_path, text))
    assert prof.instrument_for("hf-timestd") == "367"
    assert prof.instrument_for("hamsci-physics") == "367"
    assert prof.station_for("hamsci-physics") == "S000099"
    assert "hamsci-physics" not in text


def test_station_override_prints_a_notice_naming_it(capsys, tmp_path):
    p = _write(tmp_path,
               '[psws]\nenabled = true\nstation_id = "S000170"\n'
               '[psws.stations]\n"mag-recorder" = "S000082"\n'
               '"hf-timestd" = "S000170"\n')
    load_site_profile(p)
    err = capsys.readouterr().err
    assert "mag-recorder" in err and "S000082" in err and "S000170" in err
    assert "hf-timestd" not in err          # same as the site: no override


def test_station_ids_are_s_and_six_digits():
    assert sp.normalize_psws_station_id("s000418") == "S000418"
    for bad in ("S0418", "S0000418", "S00041"):
        with pytest.raises(ValueError):
            sp.normalize_psws_station_id(bad)


def _run_config_psws(tmp_path, coord_text, **kw):
    import sigmond.commands.config as cfg
    import sigmond.psws as psws
    prof = tmp_path / "site-profile.toml"
    coord = tmp_path / "coordination.toml"
    coord.write_text(coord_text)
    env = tmp_path / "coordination.env"
    _, recorders = _fake_recorder_configs(tmp_path)
    args = types.SimpleNamespace(
        station="S000418", instrument=["hf-timestd=367"],
        instrument_station=[], no_render=False, dry_run=False)
    for k, v in kw.items():
        setattr(args, k, v)
    with mock.patch.object(sp, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(cfg, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(cfg, "COORDINATION_PATH", coord), \
            mock.patch.object(cfg, "COORDINATION_ENV", env), \
            mock.patch.object(psws, "RECORDERS", recorders), \
            mock.patch.object(psws, "_sudo", lambda: []), \
            mock.patch.object(cfg, "_report_station_key", lambda p: None):
        rc = cfg.cmd_config_psws(args)
    return rc, prof, coord


def test_seed_with_gps_lat_lon_writes_no_grid(tmp_path, capsys):
    """GPSDO location where one exists, else the typed grid: coordination
    holding lat/lon seeds lat/lon, and no grid_square that could go stale."""
    import tomllib
    rc, prof, coord = _run_config_psws(
        tmp_path, '[host]\ncall = "AC0G"\nlat = 38.93\nlon = -92.33\n')
    assert rc == 0, capsys.readouterr()
    st = tomllib.loads(prof.read_text())["station"]
    assert st["latitude"] == 38.93 and st["longitude"] == -92.33
    assert "grid_square" not in st
    p = load_site_profile(prof)
    assert p.lat == 38.93 and p.lon == -92.33


def test_seed_with_grid_and_gps_prefers_gps(tmp_path, capsys):
    import tomllib
    rc, prof, _ = _run_config_psws(
        tmp_path,
        '[host]\ncall = "AC0G"\ngrid = "EM38ww"\nlat = 38.93\nlon = -92.33\n')
    assert rc == 0
    st = tomllib.loads(prof.read_text())["station"]
    assert "grid_square" not in st and st["latitude"] == 38.93


def test_seed_that_render_would_reject_leaves_no_file(tmp_path, capsys):
    """A callsign with no grid and no lat/lon: render refuses [station]
    without a location, so config psws must not leave that profile behind."""
    rc, prof, _ = _run_config_psws(tmp_path, '[host]\ncall = "AC0G"\n')
    out = capsys.readouterr()
    assert rc == 1
    assert not prof.exists()
    assert not list(tmp_path.glob("site-profile.toml*"))
    assert "grid" in (out.out + out.err)


def test_new_profile_is_0644(tmp_path, capsys):
    rc, prof, _ = _run_config_psws(
        tmp_path, '[host]\ncall = "AC0G"\ngrid = "EM38ww"\n')
    assert rc == 0
    assert (prof.stat().st_mode & 0o777) == 0o644


def test_update_keeps_the_profile_mode_and_leaves_no_tmp(tmp_path, capsys):
    prof = tmp_path / "site-profile.toml"
    prof.write_text('[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n')
    prof.chmod(0o640)
    rc, prof, _ = _run_config_psws(
        tmp_path, '[host]\ncall = "AC0G"\ngrid = "EM38ww"\n')
    assert rc == 0
    assert (prof.stat().st_mode & 0o777) == 0o640
    assert sorted(x.name for x in tmp_path.glob("site-profile.toml*")) == \
        ["site-profile.toml"]


def test_write_cleans_up_its_tmp_on_failure(tmp_path):
    import sigmond.commands.config as cfg
    prof = tmp_path / "site-profile.toml"
    with mock.patch.object(cfg, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(cfg.os, "replace",
                              side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            cfg._write_site_profile("schema_version = 1\n")
    assert list(tmp_path.iterdir()) == []


def test_config_location_updates_the_profile_location(tmp_path, capsys):
    """`smd config location --from-gps` must reach site-profile.toml too, or
    the next render puts the profile's stale grid back into coordination."""
    import tomllib
    import sigmond.commands.config as cfg
    prof = tmp_path / "site-profile.toml"
    prof.write_text('[station]\ncallsign = "AC0G"\n'
                    'grid_square = "EM38aa"   # typed\ndescription = "x"\n\n'
                    '[psws]\nenabled = true\nstation_id = "S000418"\n')
    coord = tmp_path / "coordination.toml"
    coord.write_text('[host]\ncall = "AC0G"\ngrid = "EM38aa"\n')
    env = tmp_path / "coordination.env"
    loc = {"fix": "3D", "grid": "EM38ww", "lat": 38.93, "lon": -92.33,
           "altitude_m": 200.0}
    with mock.patch.object(sp, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(cfg, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(cfg, "COORDINATION_PATH", coord), \
            mock.patch.object(cfg, "COORDINATION_ENV", env), \
            mock.patch("sigmond.coordination.COORDINATION_PATH", coord), \
            mock.patch.object(cfg, "_gpsdo_location", lambda: loc):
        rc = cfg.cmd_config_location(types.SimpleNamespace(
            dry_run=False, restart=False))
    assert rc == 0, capsys.readouterr()
    st = tomllib.loads(prof.read_text())["station"]
    assert "grid_square" not in st
    assert st["latitude"] == 38.93 and st["longitude"] == -92.33
    assert st["elevation_m"] == 200.0
    assert st["callsign"] == "AC0G" and st["description"] == "x"
    assert tomllib.loads(prof.read_text())["psws"]["station_id"] == "S000418"


def test_config_location_without_a_profile_creates_none(tmp_path, capsys):
    import sigmond.commands.config as cfg
    prof = tmp_path / "site-profile.toml"
    coord = tmp_path / "coordination.toml"
    coord.write_text('[host]\ncall = "AC0G"\n')
    loc = {"fix": "3D", "grid": "EM38ww", "lat": 38.93, "lon": -92.33,
           "altitude_m": None}
    with mock.patch.object(sp, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(cfg, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(cfg, "COORDINATION_PATH", coord), \
            mock.patch.object(cfg, "COORDINATION_ENV", tmp_path / "e"), \
            mock.patch("sigmond.coordination.COORDINATION_PATH", coord), \
            mock.patch.object(cfg, "_gpsdo_location", lambda: loc):
        assert cfg.cmd_config_location(types.SimpleNamespace(
            dry_run=False, restart=False)) == 0
    assert not prof.exists()


def test_bringup_refuses_instruments_without_a_station():
    smd = _smd()
    a = smd._build_parser().parse_args(
        ["bringup", "dasi2", "--psws-instrument", "hf-timestd=367"])
    assert smd._bringup_psws_check(a, None) is not None
    prof = sp.SiteProfile(psws_station_id="S000418")
    assert smd._bringup_psws_check(a, prof) is None
    a = smd._build_parser().parse_args(
        ["bringup", "dasi2", "--psws-station-id", "s000418"])
    assert smd._bringup_psws_check(a, None) is None
    a = smd._build_parser().parse_args(
        ["bringup", "dasi2", "--psws-station-id", "S0418"])
    assert "S0418" in smd._bringup_psws_check(a, None)


# --- review round 2 ---------------------------------------------------------

def test_bringup_refuses_a_profile_render_would_reject(tmp_path):
    """Callsign, no grid, no lat/lon in the profile: a grid in coordination
    must not paper over it, since the later render refuses the profile."""
    smd = _smd()
    p = _write(tmp_path, '[station]\ncallsign = "AC0G"\n')
    prof = load_site_profile(p)
    problem = smd._bringup_profile_location_problem(prof)
    assert problem and "location" in problem
    assert smd._bringup_grid_fallback(prof, "EM38ww") == ""


def test_bringup_takes_coordination_grid_only_beside_profile_lat_lon(tmp_path):
    smd = _smd()
    p = _write(tmp_path, '[station]\ncallsign = "AC0G"\n'
                         'latitude = 38.93\nlongitude = -92.33\n')
    prof = load_site_profile(p)
    assert smd._bringup_profile_location_problem(prof) is None
    assert smd._bringup_grid_fallback(prof, "EM38ww") == "EM38ww"
    # a receiver-only profile (no [station]) is one render accepts
    q = tmp_path / "q"
    q.mkdir()
    rx = load_site_profile(_write(q, '[psws]\nstation_id = "S000418"\n'))
    assert smd._bringup_profile_location_problem(rx) is None
    assert smd._bringup_profile_location_problem(None) is None


def test_station_notice_prints_once_per_process(capsys, tmp_path):
    p = _write(tmp_path,
               '[psws]\nenabled = true\nstation_id = "S000170"\n'
               '[psws.stations]\n"mag-recorder" = "S000083"\n')
    load_site_profile(p)
    load_site_profile(p)
    assert capsys.readouterr().err.count("S000083") == 1

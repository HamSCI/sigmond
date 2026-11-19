"""location_source: where the station's location came from (Task 9b).

Coordinates below are made up."""
import subprocess
import sys
import tomllib
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_align_cli import smd  # noqa: E402  (loads bin/smd once)

import sigmond.commands.config as cfg  # noqa: E402
import sigmond.site_profile as sp  # noqa: E402
from sigmond.coordination import Coordination, Host  # noqa: E402
from sigmond.drift import publish  # noqa: E402

PROFILE = ('[station]\ncallsign = "N0CALL"\n'
           'grid_square = "AA00aa"   # typed\n')


def _env(tmp_path, profile=PROFILE):
    prof = tmp_path / "site-profile.toml"
    prof.write_text(profile)
    coord = tmp_path / "coordination.toml"
    coord.write_text('[host]\ncall = "N0CALL"\ngrid = "AA00aa"\n')
    return prof, coord


def _patches(prof, coord, tmp_path):
    return [mock.patch.object(sp, "SITE_PROFILE_PATH", prof),
            mock.patch.object(cfg, "SITE_PROFILE_PATH", prof),
            mock.patch.object(cfg, "COORDINATION_PATH", coord),
            mock.patch.object(cfg, "COORDINATION_ENV", tmp_path / "c.env"),
            mock.patch("sigmond.coordination.COORDINATION_PATH", coord)]


def _run(patches, fn):
    import contextlib
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        return fn()


def test_config_location_writes_gpsdo_source(tmp_path):
    prof, coord = _env(tmp_path)
    loc = {"fix": "3D", "grid": "AA11bb", "lat": 10.5, "lon": 20.25,
           "altitude_m": 5.0}
    ps = _patches(prof, coord, tmp_path) + [
        mock.patch.object(cfg, "_gpsdo_location", lambda: loc)]
    rc = _run(ps, lambda: cfg.cmd_config_location(
        types.SimpleNamespace(dry_run=False, restart=False)))
    assert rc == 0
    assert tomllib.loads(coord.read_text())["host"]["location_source"] == "gpsdo"
    assert tomllib.loads(prof.read_text())["station"]["location_source"] == "gpsdo"


def test_identity_latlon_writes_operator_source(tmp_path):
    prof, coord = _env(tmp_path)
    args = types.SimpleNamespace(json=False, call=None, grid=None,
                                 lat=11.5, lon=21.25)
    rc = _run(_patches(prof, coord, tmp_path),
              lambda: cfg.cmd_config_identity(args))
    assert rc == 0
    assert tomllib.loads(coord.read_text())["host"]["location_source"] == "operator"
    assert tomllib.loads(prof.read_text())["station"]["location_source"] == "operator"


def _coord(src=""):
    return Coordination(host=Host(call="N0CALL", grid="AA00aa",
                                  lat=10.5, lon=20.25, location_source=src))


def test_publish_prefers_location_source_key(tmp_path):
    prof = tmp_path / "p.toml"
    prof.write_text('[station]\nlocation_source = "operator"\n'
                    'grid_square = "AA00aa"   # GPSDO location authority\n')
    assert publish.position(_coord(), profile=prof)[1] == "operator"
    prof.write_text('[station]\nlocation_source = "gpsdo"\n')
    assert publish.position(_coord(), profile=prof)[1] == "gpsdo"
    # the coordination [host] key counts when the profile lacks one
    prof.write_text('[station]\ncallsign = "N0CALL"\n')
    assert publish.position(_coord("gpsdo"), profile=prof)[1] == "gpsdo"


def test_publish_falls_back_to_marker(tmp_path):
    prof = tmp_path / "p.toml"
    prof.write_text('[station]\ngrid_square = "AA00aa"   '
                    '# GPSDO location authority\n')
    assert publish.position(_coord(), profile=prof)[1] == "gpsdo"
    prof.write_text('[station]\ngrid_square = "AA00aa"\n')
    assert publish.position(_coord(), profile=prof)[1] == "operator"


def _units_repo(tmp_path):
    repo = tmp_path / "sigmond"
    (repo / "systemd").mkdir(parents=True)
    for n in ("sigmond-location-check.service", "sigmond-location-check.timer"):
        (repo / "systemd" / n).write_text("x")
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    return repo, unit_dir


def _own_units(tmp_path, gpsdo_dir):
    repo, unit_dir = _units_repo(tmp_path)
    calls = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    smd._align_own_units(repo, unit_dir=unit_dir, run=fake_run,
                         gpsdo_dir=gpsdo_dir)
    return calls


def test_location_check_timer_installed_when_gpsdo_present(tmp_path):
    g = tmp_path / "gpsdo"
    g.mkdir()
    calls = _own_units(tmp_path, g)
    assert ["systemctl", "enable", "--now",
            "sigmond-location-check.timer"] in calls


def test_location_check_timer_absent_without_gpsdo(tmp_path):
    calls = _own_units(tmp_path, tmp_path / "no-gpsdo")
    assert not any("sigmond-location-check.timer" in c for c in calls)


def test_shipped_location_check_units():
    root = Path(__file__).resolve().parent.parent / "systemd"
    timer = (root / "sigmond-location-check.timer").read_text().splitlines()
    for line in ("OnBootSec=2min", "OnActiveSec=2min",
                 "OnUnitActiveSec=10min"):
        assert line in timer
    svc = (root / "sigmond-location-check.service").read_text().splitlines()
    assert "ExecStart=/usr/local/sbin/sigmond-location-check" in svc

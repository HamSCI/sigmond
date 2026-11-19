"""location_source: where the station's location came from (Task 9b).

Coordinates below are made up."""
import contextlib
import io
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


def _own_units(tmp_path):
    repo, unit_dir = _units_repo(tmp_path)
    calls = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    smd._align_own_units(repo, unit_dir=unit_dir, run=fake_run)
    return calls, unit_dir


def _start_timers(tmp_path, **kw):
    calls, unit_dir = _own_units(tmp_path)
    assert not [c for c in calls if c[:2] == ["systemctl", "enable"]]

    def fake_run(argv, **k):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        smd._align_start_timers(unit_dir=unit_dir, run=fake_run, **kw)
    return calls, out.getvalue()


def test_location_check_timer_enabled_on_every_host(tmp_path):
    calls, _ = _start_timers(tmp_path, rc=0, no_restart=False)
    assert ["systemctl", "enable", "--now",
            "sigmond-location-check.timer"] in calls


def test_location_check_timer_not_started_under_no_restart(tmp_path):
    calls, out = _start_timers(tmp_path, rc=0, no_restart=True)
    assert ["systemctl", "enable", "sigmond-location-check.timer"] in calls
    assert not any("--now" in c and "sigmond-location-check.timer" in c
                   for c in calls)
    assert "enabled but not started (--no-restart)" in out and out.isascii()


def test_location_check_timer_not_started_after_a_failed_align(tmp_path):
    calls, out = _start_timers(tmp_path, rc=1, no_restart=False)
    assert ["systemctl", "enable", "sigmond-location-check.timer"] in calls
    assert "align did not finish cleanly" in out


def test_location_check_service_inert_without_gpsdo():
    root = Path(__file__).resolve().parent.parent / "systemd"
    svc = (root / "sigmond-location-check.service").read_text().splitlines()
    assert "ConditionPathExists=/run/gpsdo" in svc


def test_identity_call_only_leaves_location_source(tmp_path):
    prof, coord = _env(tmp_path, PROFILE + 'location_source = "gpsdo"\n')
    coord.write_text('[host]\ncall = "N0CALL"\ngrid = "AA00aa"\n'
                     'lat = 10.5\nlon = 20.25\nlocation_source = "gpsdo"\n')
    args = types.SimpleNamespace(json=False, call="N1CALL", grid=None,
                                 lat=None, lon=None)
    rc = _run(_patches(prof, coord, tmp_path),
              lambda: cfg.cmd_config_identity(args))
    assert rc == 0
    assert tomllib.loads(coord.read_text())["host"]["location_source"] == "gpsdo"
    assert tomllib.loads(prof.read_text())["station"]["location_source"] == "gpsdo"


APPLIANCE = (Path(__file__).resolve().parents[2] / "sigmond-appliance"
             / "sigmond-location-check")


def _check(tmp_path, source, *, lat=10.5, hold_lock=False):
    import fcntl
    import json
    import os
    g = tmp_path / "gpsdo"
    g.mkdir()
    (g / "a.json").write_text(json.dumps({"health": {
        "latitude": 10.5, "longitude": 20.25, "gps_fix": "3D",
        "sats_used": 8, "fix_age_sec": 5}}))
    prof = tmp_path / "site-profile.toml"
    prof.write_text(f'[station]\ncallsign = "N0CALL"\nlatitude = {lat}\n'
                    'longitude = 20.25\n'
                    'grid_square = "KK00dm"   # typed\n'
                    + (f'location_source = "{source}"\n' if source else ""))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls"
    for n, body in (("smd", f'echo "smd $*" >> {log}\n'),
                    ("systemd-cat", "cat >/dev/null\n"),
                    ("systemctl", f'echo "systemctl $*" >> {log}\n')):
        p = bindir / n
        p.write_text("#!/bin/bash\n" + body)
        p.chmod(0o755)
    lock = tmp_path / "lifecycle.lock"
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
               SIGMOND_SITE_PROFILE=str(prof), SIGMOND_GPSDO_DIR=str(g),
               SIGMOND_LIFECYCLE_LOCK=str(lock))
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if hold_lock:      # as smd's lifecycle_lock does: flock, LOCK_EX
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        subprocess.run(["bash", str(APPLIANCE)], env=env, check=True)
    finally:
        os.close(fd)
    calls = log.read_text() if log.exists() else ""
    return tomllib.loads(prof.read_text())["station"], calls


import pytest  # noqa: E402


@pytest.mark.skipif(not APPLIANCE.exists(), reason="sigmond-appliance absent")
def test_location_check_relabels_a_current_position_as_gpsdo(tmp_path):
    st, calls = _check(tmp_path, "operator")
    assert st["location_source"] == "gpsdo"
    assert st["grid_square"] == "KK00dm"   # nothing moved
    assert "smd config render" in calls
    assert "systemctl" not in calls        # no recorder bounce


@pytest.mark.skipif(not APPLIANCE.exists(), reason="sigmond-appliance absent")
def test_location_check_current_and_gpsdo_does_nothing(tmp_path):
    st, calls = _check(tmp_path, "gpsdo")
    assert st["location_source"] == "gpsdo"
    assert calls == ""


@pytest.mark.skipif(not APPLIANCE.exists(), reason="sigmond-appliance absent")
def test_location_check_moves_and_restarts_without_the_lock(tmp_path):
    st, calls = _check(tmp_path, "operator", lat=11.0)
    assert st["latitude"] == 10.5 and st["location_source"] == "gpsdo"
    assert "systemctl try-restart" in calls


@pytest.mark.skipif(not APPLIANCE.exists(), reason="sigmond-appliance absent")
def test_location_check_skips_its_tick_while_smd_holds_the_lock(tmp_path):
    # Final review I1: never re-render or restart recorders while an smd
    # lifecycle operation (align --apply's bring-up) runs.
    st, calls = _check(tmp_path, "operator", lat=11.0, hold_lock=True)
    assert st["latitude"] == 11.0 and st["location_source"] == "operator"
    assert calls == ""
    assert not list(tmp_path.glob("site-profile.toml.bak-location-*"))


@pytest.mark.skipif(not APPLIANCE.exists(), reason="sigmond-appliance absent")
def test_location_check_relabel_also_waits_for_the_lock(tmp_path):
    st, calls = _check(tmp_path, "operator", hold_lock=True)
    assert st["location_source"] == "operator" and calls == ""


def test_location_check_lock_is_smds_lock():
    from sigmond import paths
    if not APPLIANCE.exists():
        pytest.skip("sigmond-appliance absent")
    assert f"/var/lib/sigmond/{paths.LIFECYCLE_LOCK.name}" in APPLIANCE.read_text()
    assert str(paths.LIFECYCLE_LOCK) == "/var/lib/sigmond/lifecycle.lock"


def test_shipped_location_check_units():
    root = Path(__file__).resolve().parent.parent / "systemd"
    timer = (root / "sigmond-location-check.timer").read_text().splitlines()
    for line in ("OnBootSec=2min", "OnActiveSec=2min",
                 "OnUnitActiveSec=10min"):
        assert line in timer
    svc = (root / "sigmond-location-check.service").read_text().splitlines()
    assert "ExecStart=/usr/local/sbin/sigmond-location-check" in svc

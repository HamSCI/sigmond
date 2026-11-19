"""`smd align --report`, drift.json, the sigmondb row (Task 9) and the
Task 8 carry-overs: --apply refreshes drift.json, --accept validates its key
and excludes --apply/--json/--verify."""
import argparse
import contextlib
import hashlib
import io
import json
import os
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_align_cli import smd  # noqa: E402  (loads bin/smd once)

from sigmond.drift import publish  # noqa: E402
from sigmond.drift.model import Item, Report  # noqa: E402
from sigmond.coordination import Coordination, Host, Radiod  # noqa: E402
from sigmond.receivers import Receiver  # noqa: E402


class FakeWriter:
    is_noop = False

    def __init__(self):
        self.rows = []
        self.buffered = 0

    def insert(self, rows):
        self.rows.extend(rows)

    def flush(self):
        pass

    def close(self):
        pass


def _report(station="AC0G-B4"):
    r = Report(station=station, release="v3.93", baseline="blessed",
               generated_at="2026-11-19T01:02:03Z")
    r.items = [
        Item("unit", "fault", "a.service", "x" * 300, "smd align --apply"),
        Item("source", "drift", "hf-timestd", "3 behind", "smd align --apply"),
    ]
    r.accepted = [{"key": "build:x"}]
    r.reminders = ["restore test"]
    return r


def _coord(lat=0.0, lon=0.0, grid="EM38ww"):
    return Coordination(host=Host(call="AC0G", grid=grid, lat=lat, lon=lon),
                        radiods={"sigma-status.local":
                                 Radiod(id="sigma-status.local")})


@pytest.fixture
def env(tmp_path, monkeypatch):
    mid = tmp_path / "machine-id"
    mid.write_text("0123456789abcdef0123456789abcdef\n")
    w = FakeWriter()
    path = tmp_path / "var" / "drift.json"
    monkeypatch.setattr(publish, "DRIFT_JSON", path)
    monkeypatch.setattr(publish, "MACHINE_ID", mid)
    monkeypatch.setattr("sigmond.site_profile.SITE_PROFILE_PATH",
                        tmp_path / "no-site-profile.toml")
    monkeypatch.setattr(publish, "make_writer", lambda: w)
    monkeypatch.setattr("sigmond.coordination.load_coordination",
                        lambda *a, **k: _coord())
    monkeypatch.setattr("sigmond.receivers.load_receivers", lambda *a, **k: [])
    return types_ns(path=path, writer=w, mid=mid)


def types_ns(**kw):
    return argparse.Namespace(**kw)


def _report_args(**kw):
    return argparse.Namespace(report=True, apply=False, verify=False,
                              json=False, accept=None, reason=None,
                              test=False, until=None, release=None,
                              base="/opt/git/sigmond", no_cost=True, **kw)


def _run(args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = smd.cmd_align(args)
    return rc, out.getvalue()


def test_report_writes_json_atomically(env):
    with mock.patch.object(smd, "_need_root", return_value=False), \
         mock.patch.object(smd, "_align_collect_report",
                           return_value=_report()), \
         mock.patch("os.replace", wraps=os.replace) as rep:
        rc, out = _run(_report_args())
    assert rc == 0, out
    doc = json.loads(env.path.read_text())
    assert doc["station"] == "AC0G-B4" and doc["counts"]["unit"] == {"fault": 1}
    assert stat.S_IMODE(env.path.stat().st_mode) == 0o644
    # written beside, then renamed over; no temp left behind
    assert any(Path(c.args[1]) == env.path for c in rep.call_args_list)
    assert [p.name for p in env.path.parent.iterdir()] == ["drift.json"]


def test_report_inserts_one_sink_row(env):
    with mock.patch.object(smd, "_need_root", return_value=False), \
         mock.patch.object(smd, "_align_collect_report",
                           return_value=_report()):
        rc, _ = _run(_report_args())
    assert rc == 0
    assert len(env.writer.rows) == 1
    row = env.writer.rows[0]
    assert row["time"] == "2026-11-19T01:02:03Z"
    assert row["reporter_id"] == "AC0G-B4"
    assert (row["release"], row["baseline"]) == ("v3.93", "blessed")
    assert (row["n_fault"], row["n_drift"], row["n_accepted"]) == (1, 1, 1)
    assert row["schema_version"] == 1
    assert json.loads(row["counts_json"]) == {
        "unit": {"fault": 1}, "source": {"drift": 1}}
    faults = json.loads(row["faults_json"])
    assert len(faults) == 1 and len(faults[0]["detail"]) == 200
    assert json.loads(row["reminders_json"]) == ["restore test"]


def test_report_caps_faults_at_50(env):
    r = _report()
    r.items = [Item("unit", "fault", f"u{i}.service", "d", "f")
               for i in range(60)]
    row = publish.build_row(r, machine_id=env.mid, coord=_coord(),
                            receivers=[], hostname="h")
    assert len(json.loads(row["faults_json"])) == 50
    assert row["n_fault"] == 60


def test_report_takes_no_lifecycle_lock(env):
    def boom(*a, **k):
        raise AssertionError("--report took the lifecycle lock")
    with mock.patch.object(smd, "_need_root", return_value=False), \
         mock.patch("sigmond.lifecycle.lifecycle_lock", boom), \
         mock.patch.object(smd, "lifecycle_lock", boom, create=True), \
         mock.patch.object(smd, "_align_collect_report",
                           return_value=_report()):
        rc, _ = _run(_report_args())
    assert rc == 0


def test_report_json_survives_a_sink_failure(env):
    env.writer.is_noop = True
    with mock.patch.object(smd, "_need_root", return_value=False), \
         mock.patch.object(smd, "_align_collect_report",
                           return_value=_report()):
        rc, out = _run(_report_args())
    assert rc == 0 and env.path.is_file()
    assert "no sigmondb sink" in out
    out.encode("ascii")


def test_row_carries_identity(env):
    recs = [Receiver(reporter_id="AC0G/B1A", radiod="b1a-status.local"),
            Receiver(reporter_id="AC0G/B1B", radiod="b1b-status.local")]
    row = publish.build_row(_report(), machine_id=env.mid,
                            coord=_coord(grid="EM38ww"), receivers=recs,
                            hostname="b4")
    want = hashlib.sha256(b"0123456789abcdef0123456789abcdef").hexdigest()[:12]
    assert row["host_key"] == want and row["hostname"] == "b4"
    rx = json.loads(row["receivers_json"])
    assert len(rx) == 2
    assert {r["reporter_id"] for r in rx} == {"AC0G/B1A", "AC0G/B1B"}
    assert {r["radiod_id"] for r in rx} == {"b1a-status.local",
                                            "b1b-status.local"}
    assert all(r["grid6"] == "EM38ww" and r["pos_source"] == "operator"
               for r in rx)
    # no position anywhere in the row
    flat = json.dumps(row).lower()
    for bad in ('"lat"', '"lon"', "latitude", "longitude"):
        assert bad not in flat
    assert not {"lat", "lon", "latitude", "longitude"} & set(row)


def _profile(tmp_path, grid_line):
    p = tmp_path / "site-profile.toml"
    p.write_text('[station]\ncallsign = "AC0G"\n' + grid_line + "\n")
    return p


def _pos_source(env, tmp_path, profile, coord=None):
    row = publish.build_row(_report(), machine_id=env.mid,
                            coord=coord or _coord(), receivers=[],
                            hostname="b4", profile=profile)
    return json.loads(row["receivers_json"])


def test_pos_source_gpsdo_when_location_check_marked_the_grid(env, tmp_path):
    prof = _profile(tmp_path,
                    'grid_square = "EM38uv"   # GPSDO location authority')
    rx = _pos_source(env, tmp_path, prof, _coord(lat=38.9, lon=-92.3))
    assert rx == [{"reporter_id": "AC0G-B4",
                   "radiod_id": "sigma-status.local",
                   "grid6": "EM38uv", "pos_source": "gpsdo"}]
    assert publish.maidenhead(38.9, -92.3) == "EM38uv"


def test_pos_source_operator_without_the_marker(env, tmp_path):
    prof = _profile(tmp_path, 'grid_square = "EM38ww"')
    assert _pos_source(env, tmp_path, prof)[0]["pos_source"] == "operator"
    assert _pos_source(env, tmp_path, tmp_path / "absent")[0][
        "pos_source"] == "operator"


def test_a_live_gps_fix_without_the_marker_stays_operator(env, tmp_path):
    prof = _profile(tmp_path, 'grid_square = "EM38ww"')
    g = tmp_path / "run-gpsdo"
    g.mkdir()
    (g / "lbe.json").write_text(json.dumps({"health": {"gps_fix": "3D"}}))
    assert not hasattr(publish, "_gpsdo_has_fix")
    rx = _pos_source(env, tmp_path, prof, _coord(lat=38.9, lon=-92.3))
    assert rx[0]["pos_source"] == "operator"


def test_host_key_stable_across_rename(env):
    a = publish.build_row(_report("AC0G-B4"), machine_id=env.mid,
                          coord=_coord(), receivers=[], hostname="old")
    b = publish.build_row(_report("AC0G-RX888"), machine_id=env.mid,
                          coord=_coord(), receivers=[], hostname="new")
    assert a["host_key"] == b["host_key"] != ""
    assert a["reporter_id"] != b["reporter_id"]


def test_report_lines_are_ascii(env):
    with mock.patch.object(smd, "_need_root", return_value=False), \
         mock.patch.object(smd, "_align_collect_report", return_value=None):
        rc, out = _run(_report_args())
    assert rc == 2
    out.encode("ascii")


# ---- Task 8 carry-overs -------------------------------------------------

def test_apply_refreshes_drift_json(env):
    args = argparse.Namespace(apply=True, verify=False, json=False,
                              report=False, accept=None, reason=None,
                              test=False, until=None, release=None,
                              base="/opt/git/sigmond", no_cost=True)
    with mock.patch.object(smd, "_align_apply", return_value=0), \
         mock.patch.object(smd, "_align_collect_report",
                           return_value=_report()):
        rc, _ = _run(args)
    assert rc == 0
    assert json.loads(env.path.read_text())["station"] == "AC0G-B4"
    assert len(env.writer.rows) == 1


def test_apply_exit_code_survives_a_refresh_error(env):
    args = argparse.Namespace(apply=True, verify=False, json=False,
                              report=False, accept=None, reason=None,
                              test=False, until=None, release=None,
                              base="/opt/git/sigmond", no_cost=True)
    with mock.patch.object(smd, "_align_apply", return_value=1), \
         mock.patch.object(smd, "_align_collect_report",
                           side_effect=RuntimeError("boom")):
        rc, out = _run(args)
    assert rc == 1
    assert "drift.json" in out and not env.path.exists()
    out.encode("ascii")


def _accept_args(key):
    return argparse.Namespace(accept=key, reason="because", test=False,
                              until=None, apply=False, verify=False,
                              json=False, report=False)


def test_accept_refuses_unknown_kind_and_writes_nothing():
    for key in ("bogus:thing", ":thing", "units:a.service"):
        with mock.patch("sigmond.drift.exceptions.accept") as acc, \
             mock.patch.object(smd, "_need_root") as nr:
            rc, out = _run(_accept_args(key))
        assert rc != 0
        acc.assert_not_called()
        nr.assert_not_called()
        assert "source, build, unit, override, stray, host" in out
        out.encode("ascii")


def test_accept_takes_each_known_kind():
    for kind in ("source", "build", "unit", "override", "stray", "host"):
        with mock.patch("sigmond.drift.exceptions.accept",
                        side_effect=ValueError("stop")) as acc, \
             mock.patch.object(smd, "_need_root", return_value=False):
            _run(_accept_args(f"{kind}:x"))
        acc.assert_called_once()


@pytest.mark.parametrize("other", ["--apply", "--json", "--verify"])
def test_accept_is_exclusive_with_apply_json_verify(other):
    with contextlib.redirect_stderr(io.StringIO()), \
         pytest.raises(SystemExit) as cm:
        smd._build_parser().parse_args(
            ["align", "--accept", "unit:a.service", "--reason", "r", other])
    assert cm.value.code == 2


def test_apply_enables_and_starts_the_drift_timer(tmp_path):
    import subprocess
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    repo = tmp_path / "sigmond"
    (repo / "systemd").mkdir(parents=True)
    for n in ("sigmond-drift.service", "sigmond-drift.timer"):
        (repo / "systemd" / n).write_text("x")
    calls = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    steps = smd._align_own_units(repo, unit_dir=unit_dir, run=fake_run)
    assert ["systemctl", "enable", "--now", "sigmond-drift.timer"] in calls
    assert [s.outcome for s in steps] == ["installed"]


def test_shipped_drift_units_carry_the_required_values():
    root = Path(__file__).resolve().parent.parent / "systemd"
    timer = (root / "sigmond-drift.timer").read_text()
    for line in ("OnCalendar=daily", "RandomizedDelaySec=1h",
                 "Persistent=true"):
        assert line in timer.splitlines()
    svc = (root / "sigmond-drift.service").read_text().splitlines()
    for line in ("ExecStart=/usr/local/bin/smd align --report", "Nice=10",
                 "IOSchedulingClass=idle"):
        assert line in svc


def test_report_is_exclusive_with_apply():
    with contextlib.redirect_stderr(io.StringIO()), \
         pytest.raises(SystemExit) as cm:
        smd._build_parser().parse_args(["align", "--report", "--apply"])
    assert cm.value.code == 2


def test_drift_service_has_a_start_timeout():
    root = Path(__file__).resolve().parent.parent / "systemd"
    assert "TimeoutStartSec=10min" in (
        root / "sigmond-drift.service").read_text().splitlines()

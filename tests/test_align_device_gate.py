"""Task 8 (v3.94): device-reading clients follow their local device.

AC1SN (installed 2026-10-15) kept gmag-webui enabled with no magnetometer:
`harmonize.dormant_reason` gates only the `smd start` verb, and nothing
re-sensed an existing station.  `smd align` now does, for the catalog's
`hardware_gated` clients (mag-recorder, gmag-webui, gpsdo-monitor):

* device absent, never seen: switch the client off (stop and disable its
  units; topology stays enabled);
* device absent, seen before: no switch-off; a fault in the drift report
  and `smd check`;
* device present, units disabled: enable and start them;
* readiness unknown: no change, one info line.

hf-timestd is never gated on local USB: its GPSDO or TS-1 may sit on
another machine.

The real `cmd_align` runs (dry run and --apply), with the real
`_align_bringup`, `_align_follow_devices` and `_dormant_disable_units`;
only systemctl (subprocess.run), hardware readiness, topology, the catalog
and the bring-up's child smd runs are stubbed."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import types
from pathlib import Path
from unittest import mock

from sigmond import align, align_apply
from tests.test_align_cli import _apply_args, _apply_patches, smd

REL = align.Release(tag="v3.53", manifest_text="m", appliance_commit="a" * 40,
                    components={"sigmond": "daba1f6"})

MAG = "magnetometer (RM3100 / Pololu USB-I2C)"
GPS = "GPSDO (Leo Bodnar, USB/HID)"

_DEPLOY = {
    "mag-recorder": ["mag-recorder.service", "mag-recorder-upload.timer"],
    "hf-timestd": ["timestd-core-recorder.service"],
    "gpsdo-monitor": ["gpsdo-monitor.service"],
}
# gmag-webui has no deploy.toml: lifecycle reads sigmond's shim
# (etc/clients/gmag-webui.deploy.toml), units = ["gmag-webui.service"].
_ALL_UNITS = ["mag-recorder.service", "mag-recorder-upload.timer",
              "gmag-webui.service", "timestd-core-recorder.service",
              "gpsdo-monitor.service"]


class FakeSystemd:
    """systemctl with state: `disable --now` disables and stops, `enable
    --now` enables and starts."""

    def __init__(self, enabled=(), active=(), masked=()):
        self.enabled = set(enabled)
        self.active = set(active)
        self.masked = set(masked)
        self.calls = []

    def __call__(self, argv, **kw):
        argv = list(argv)
        if argv and argv[0] == "sudo":
            argv = argv[1:]
        self.calls.append(argv)
        ok = subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:1] != ["systemctl"]:
            return ok
        verb, unit = (argv[1] if len(argv) > 1 else ""), argv[-1]
        if verb == "is-active":
            return subprocess.CompletedProcess(argv, 0 if unit in self.active else 3,
                                               "", "")
        if verb == "is-enabled":
            state = ("masked" if unit in self.masked
                     else "enabled" if unit in self.enabled else "disabled")
            return subprocess.CompletedProcess(argv, 0 if state == "enabled" else 1,
                                               state + "\n", "")
        if verb == "show":
            if "FragmentPath" in argv:
                return subprocess.CompletedProcess(argv, 0, f"/etc/systemd/system/{argv[2]}\n", "")
            if "LoadState" in argv:
                return subprocess.CompletedProcess(argv, 0, "loaded\n", "")
        if verb == "cat":
            return subprocess.CompletedProcess(argv, 1, "", "No files found")
        if verb == "disable":
            self.enabled.discard(unit)
            if "--now" in argv:
                self.active.discard(unit)
        if verb == "enable":
            self.enabled.add(unit)
            if "--now" in argv:
                self.active.add(unit)
        if verb == "stop":
            self.active.discard(unit)
        if verb == "start":
            self.active.add(unit)
        return ok


def _drive(*, ready, samples=False, dry=False, no_restart=False, quiet=False,
           enabled_units=None, active_units=None, topology=None,
           gated=None, exceptions=None, gmag_unit="current", gmag_device=True,
           gpsdo_location=False, result=None, masked=(), gmag_masked=False,
           runs=1, call=None):
    """Run cmd_align.  ``ready`` maps component -> True/False/None;
    ``samples`` writes a mag-recorder sample file (the magnetometer has
    delivered before).  Returns (FakeSystemd, output, topology dict,
    readiness queries).  ``gmag_unit`` is the installed gmag-webui unit
    text ("current": sigmond's render now; None: not installed);
    ``gmag_device`` whether /dev/ttyMAG0 exists; ``gpsdo_location`` whether
    the station's location came from a GPSDO.  ``result``, a dict, gets
    the gmag-webui unit text after the run."""
    topology = dict(topology or {"mag-recorder": True, "gmag-webui": True,
                                 "hf-timestd": True})
    sysd = FakeSystemd(
        enabled=_ALL_UNITS if enabled_units is None else enabled_units,
        active=_ALL_UNITS if active_units is None else active_units,
        masked=masked)
    gated = gated if gated is not None else {"mag-recorder": MAG, "gmag-webui": MAG,
                                             "gpsdo-monitor": GPS}
    asked = []

    def fake_ready(name):
        asked.append(name)
        return ready.get(name)

    def fake_align_run(argv, *, run, timeout, action, **kw):
        if argv[0] == "systemctl":
            return sysd(argv)
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    catalog = {n: types.SimpleNamespace(hardware_gated=gated.get(n))
               for n in set(topology) | set(gated)}
    out = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
        base = Path(tmp)
        for name, units in _DEPLOY.items():
            (base / name).mkdir()
            (base / name / "deploy.toml").write_text(
                "[systemd]\nunits = [" + ", ".join(f'"{u}"' for u in units) + "]\n")
        unit_dir = base / "etc-systemd"
        unit_dir.mkdir()
        for u in _ALL_UNITS:
            (unit_dir / u).write_text("[Service]\nExecStart=/bin/true\n")
        from sigmond import gmag_webui
        gmag_path = unit_dir / gmag_webui.SERVICE
        if gmag_masked:
            gmag_path.unlink()
            gmag_path.symlink_to("/dev/null")
        elif gmag_unit is None:
            gmag_path.unlink()
        elif gmag_unit == "current":
            gmag_path.write_text(gmag_webui.render_unit(checkout=base / "gmag-webui"))
        else:
            gmag_path.write_text(gmag_unit)
        dev = base / "ttyMAG0"
        if gmag_device:
            dev.write_text("")
        st.enter_context(mock.patch.object(gmag_webui, "UNIT_PATH", gmag_path))
        st.enter_context(mock.patch.object(gmag_webui, "DEVICE", str(dev)))
        st.enter_context(mock.patch("sigmond.drift.devices.gpsdo_location_recorded",
                                    return_value=gpsdo_location))
        spool = base / "spool"
        spool.mkdir()
        if samples:
            (spool / "samples-2026-11-01.jsonl").write_text('{"ts": "x"}\n')
        mag_cfg = base / "mag-recorder-config.toml"
        mag_cfg.write_text(f'[paths]\nspool_dir = "{spool}"\n')
        exc = base / "drift-exceptions.toml"
        if exceptions is not None:
            exc.write_text(exceptions)
        st.enter_context(mock.patch("sigmond.drift.exceptions.DEFAULT_PATH", exc))
        st.enter_context(mock.patch("sigmond.archive.CONFIG", base / "archive.toml"))
        real_bringup = smd._align_bringup
        _apply_patches(st, rel=REL, live={"sigmond": "daba1f6"})
        st.enter_context(mock.patch.object(smd, "_align_bringup", real_bringup))
        st.enter_context(mock.patch.object(smd, "_align_run", fake_align_run))
        st.enter_context(mock.patch.object(smd, "_align_own_units", lambda *a, **k: []))
        st.enter_context(mock.patch.object(smd, "_align_start_timers", lambda **k: None))
        st.enter_context(mock.patch.object(smd, "_align_drift_report",
                                           lambda *a, **k: None))
        st.enter_context(mock.patch.object(smd, "_align_print_drift", lambda r: None))
        if quiet:
            st.enter_context(mock.patch.object(smd, "_align_aligned_release",
                                               lambda: smd._align_record_key(REL)))
            st.enter_context(mock.patch.object(smd, "_align_live_passed",
                                               lambda live: True))
        st.enter_context(mock.patch.object(smd, "_ALIGN_UNIT_DIR", unit_dir))
        st.enter_context(mock.patch("sigmond.catalog.load_catalog", return_value=catalog))
        st.enter_context(mock.patch("sigmond.drift.build_report.topology_enabled",
                                    side_effect=lambda: dict(topology)))
        st.enter_context(mock.patch(
            "sigmond.lifecycle._find_deploy_toml",
            lambda c: (base / c / "deploy.toml") if (base / c / "deploy.toml").is_file()
            else None))
        st.enter_context(mock.patch("sigmond.hardware.hardware_ready", fake_ready))
        st.enter_context(mock.patch("sigmond.drift.devices.MAG_CONFIG", mag_cfg))
        st.enter_context(mock.patch("sigmond.uploader_manifest.MANIFEST_PATH",
                                    base / "pipelines.toml"))
        st.enter_context(mock.patch("subprocess.run", sysd))
        st.enter_context(mock.patch("sigmond.align_apply.apply_plan",
                                    return_value=[align_apply.Step("sigmond", "current")]))
        with contextlib.redirect_stdout(out):
            for _ in range(runs):
                if call is not None:
                    call()
                else:
                    smd.cmd_align(_apply_args(tmp, apply=not dry, no_restart=no_restart))
        if result is not None:
            result["link"] = (os.readlink(gmag_path) if gmag_path.is_symlink()
                              else None)
            result["unit"] = (gmag_path.read_text()
                              if gmag_path.exists() and not gmag_path.is_symlink()
                              else None)
            result["render"] = gmag_webui.render_unit(checkout=base / "gmag-webui")
    return sysd, out.getvalue(), topology, asked


def _mutating(calls):
    return [c for c in calls if len(c) > 1 and c[0] == "systemctl"
            and c[1] in ("enable", "disable", "start", "stop", "restart")]


def _touching(calls, unit):
    return [c for c in _mutating(calls) if c[-1] == unit]


def test_absent_magnetometer_switches_off_mag_clients():
    # AC1SN: both mag clients enabled and active, no magnetometer ever.
    sysd, out, topo, _ = _drive(ready={"mag-recorder": False, "gmag-webui": False})
    for u in ("mag-recorder.service", "mag-recorder-upload.timer", "gmag-webui.service"):
        assert ["systemctl", "disable", "--now", u] in sysd.calls, out
        assert u not in sysd.enabled and u not in sysd.active
    # Topology keeps the intent: the clients come back with the device.
    assert topo["mag-recorder"] is True and topo["gmag-webui"] is True
    # The declared-units step ran after and did not re-enable them.
    assert not [c for c in _mutating(sysd.calls) if c[1] == "enable"], out
    assert "timestd-core-recorder.service" in sysd.active


def test_declared_units_step_does_not_fight_the_switch_off():
    # mag-recorder's upload timer disabled: the declared-units step would
    # enable it on a running mag-recorder; after the switch-off it reads
    # stopped, so a full --apply leaves every mag unit off.
    on = [u for u in _ALL_UNITS if u != "mag-recorder-upload.timer"]
    sysd, out, _, _ = _drive(ready={"mag-recorder": False, "gmag-webui": False},
                             enabled_units=on)
    assert not [c for c in _mutating(sysd.calls) if c[1] == "enable"], out
    for u in ("mag-recorder.service", "mag-recorder-upload.timer", "gmag-webui.service"):
        assert u not in sysd.enabled and u not in sysd.active


def test_present_device_reenables():
    sysd, out, _, _ = _drive(ready={"mag-recorder": True, "gmag-webui": True},
                             enabled_units=["timestd-core-recorder.service"],
                             active_units=["timestd-core-recorder.service"])
    for u in ("mag-recorder.service", "gmag-webui.service"):
        assert ["systemctl", "enable", "--now", u] in sysd.calls, out
        assert u in sysd.active


def test_present_device_with_units_on_changes_nothing():
    sysd, out, _, _ = _drive(ready={"mag-recorder": True, "gmag-webui": True})
    assert _mutating(sysd.calls) == [], out


def test_unknown_readiness_changes_nothing():
    sysd, out, _, _ = _drive(ready={"mag-recorder": None, "gmag-webui": None})
    assert _mutating(sysd.calls) == [], out
    assert out.count("presence unknown") == 2, out


def test_absent_but_seen_before_faults_not_switches_off():
    # SIGMA: the magnetometer has delivered samples here before.
    sysd, out, _, _ = _drive(ready={"mag-recorder": False, "gmag-webui": False},
                             samples=True)
    assert not [c for c in _mutating(sysd.calls) if c[1] in ("disable", "stop")], out
    assert f"{MAG} missing; mag-recorder has recorded from it before" in out
    assert f"{MAG} missing; gmag-webui has recorded from it before" in out


def test_dry_run_only_names():
    sysd, out, _, _ = _drive(ready={"mag-recorder": False, "gmag-webui": False}, dry=True)
    assert _mutating(sysd.calls) == [], out
    assert f"would switch off mag-recorder: no {MAG} on this machine" in out
    assert f"would switch off gmag-webui: no {MAG} on this machine" in out


def test_dry_run_names_switch_on():
    sysd, out, _, _ = _drive(ready={"mag-recorder": True, "gmag-webui": True}, dry=True,
                             enabled_units=[], active_units=[])
    assert _mutating(sysd.calls) == [], out
    assert "would enable and start mag-recorder" in out


def test_quiet_apply_switches_off():
    sysd, out, _, _ = _drive(ready={"mag-recorder": False, "gmag-webui": False},
                             quiet=True)
    assert "nothing to do" in out
    assert ["systemctl", "disable", "--now", "gmag-webui.service"] in sysd.calls, out
    assert not [c for c in _mutating(sysd.calls) if c[1] == "enable"], out


def test_no_restart_holds_both_directions():
    sysd, out, _, _ = _drive(ready={"mag-recorder": False, "gmag-webui": False},
                             no_restart=True)
    assert not [c for c in _mutating(sysd.calls) if c[1] in ("disable", "stop")], out
    assert "--no-restart" in out


def test_drift_exception_keeps_component_as_is():
    from tests.test_align_enables_units import _exceptions_text
    sysd, out, _, _ = _drive(ready={"mag-recorder": False, "gmag-webui": False},
                             exceptions=_exceptions_text(["gmag-webui.service"]))
    assert _touching(sysd.calls, "gmag-webui.service") == [], out
    assert ["systemctl", "disable", "--now", "mag-recorder.service"] in sysd.calls


def test_unreadable_exceptions_change_nothing():
    sysd, out, _, _ = _drive(ready={"mag-recorder": False, "gmag-webui": False},
                             exceptions="this is [not toml\n")
    assert not [c for c in _mutating(sysd.calls) if c[1] in ("disable", "stop")], out


def test_hf_timestd_never_gated():
    # No TS-1 or GPSDO on this machine's USB; even a catalog that marked
    # hf-timestd hardware-gated must not switch it off.
    sysd, out, _, asked = _drive(
        ready={"mag-recorder": True, "gmag-webui": True, "hf-timestd": False,
               "gpsdo-monitor": False},
        gated={"mag-recorder": MAG, "gmag-webui": MAG, "gpsdo-monitor": GPS,
               "hf-timestd": GPS})
    assert "hf-timestd" not in asked
    assert _touching(sysd.calls, "timestd-core-recorder.service") == [], out
    assert "timestd-core-recorder.service" in sysd.active


def test_gpsdo_monitor_absent_seen_by_location_source_faults():
    # The station's location came from a GPSDO read here: seen before.
    sysd, out, _, _ = _drive(
        ready={"mag-recorder": True, "gmag-webui": True, "gpsdo-monitor": False},
        topology={"mag-recorder": True, "gmag-webui": True, "hf-timestd": True,
                  "gpsdo-monitor": True}, gpsdo_location=True)
    assert _touching(sysd.calls, "gpsdo-monitor.service") == [], out
    assert f"{GPS} missing; gpsdo-monitor has recorded from it before" in out


def test_gpsdo_monitor_absent_never_seen_switches_off():
    # A station behind a shared GPSDO: location typed or from a grid.
    sysd, out, _, _ = _drive(
        ready={"mag-recorder": True, "gmag-webui": True, "gpsdo-monitor": False},
        topology={"mag-recorder": True, "gmag-webui": True, "hf-timestd": True,
                  "gpsdo-monitor": True}, gpsdo_location=False)
    assert ["systemctl", "disable", "--now", "gpsdo-monitor.service"] in sysd.calls, out


def test_topology_disabled_client_untouched():
    sysd, out, _, asked = _drive(ready={"mag-recorder": False, "gmag-webui": False},
                                 topology={"mag-recorder": False, "gmag-webui": False,
                                           "hf-timestd": True})
    assert asked == []
    assert _mutating(sysd.calls) == [], out


# --- the rendered gmag-webui unit ---------------------------------------------

def test_gmag_webui_unit_has_device_condition():
    from sigmond import gmag_webui
    unit = gmag_webui.render_unit()
    head, _, _ = unit.partition("[Service]")
    assert "ConditionPathExists=/dev/ttyMAG0" in head
    assert head.index("[Unit]") < head.index("ConditionPathExists=/dev/ttyMAG0")


# --- readiness: gmag-webui follows mag-recorder -----------------------------

def test_gmag_webui_readiness_follows_mag_recorder():
    from sigmond import hardware
    with mock.patch.object(hardware, "inventory_hardware_present", return_value=None), \
            mock.patch.dict(hardware._LEGACY_PROBES, {"mag-recorder": lambda: False}):
        assert hardware.hardware_ready("gmag-webui") is False
    with mock.patch.object(hardware, "inventory_hardware_present", return_value=None), \
            mock.patch.dict(hardware._LEGACY_PROBES, {"mag-recorder": lambda: True}):
        assert hardware.hardware_ready("gmag-webui") is True


# --- the drift report and smd check -------------------------------------------

def _report(*, ready, seen, enabled=None):
    from sigmond.drift import build_report as br
    with tempfile.TemporaryDirectory() as tmp:
        readers = br.Readers(
            base=Path(tmp), sigmond_repo=Path(tmp) / "sigmond",
            unit_dir=Path(tmp), exceptions_path=Path(tmp) / "none.toml",
            enabled=enabled or {"mag-recorder": True},
            gated={"mag-recorder": MAG},
            device_ready=lambda n: ready, device_seen=lambda n: seen)
        return br.build("AC0G-T", readers=readers)


def test_drift_report_faults_seen_device_missing():
    rep = _report(ready=False, seen=True)
    faults = [i for i in rep.items if i.subject == "mag-recorder"]
    assert len(faults) == 1 and faults[0].severity == "fault"
    assert faults[0].kind == "unit"
    assert faults[0].detail == f"{MAG} missing; mag-recorder has recorded from it before"


def test_drift_report_quiet_otherwise():
    for ready, seen in ((False, False), (True, True), (None, True)):
        rep = _report(ready=ready, seen=seen)
        assert not [i for i in rep.items if i.subject == "mag-recorder"], (ready, seen)
    rep = _report(ready=False, seen=True, enabled={"mag-recorder": False})
    assert not [i for i in rep.items if i.subject == "mag-recorder"]


def test_smd_check_shows_the_fault():
    from sigmond.commands import check
    rep = _report(ready=False, seen=True)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "drift.json"
        path.write_text(json.dumps(rep.to_json()))
        env = types.SimpleNamespace(now=path.stat().st_mtime)
        line = check.drift_line(env, path)
    assert line.mark == check.FAIL
    assert f"{MAG} missing; mag-recorder has recorded from it before" in line.text


def test_seen_before_reads_mag_spool_from_config():
    from sigmond.drift import devices
    with tempfile.TemporaryDirectory() as tmp:
        spool = Path(tmp) / "sp"
        spool.mkdir()
        cfg = Path(tmp) / "c.toml"
        cfg.write_text(f'[paths]\nspool_dir = "{spool}"\n')
        with mock.patch.object(devices, "MAG_CONFIG", cfg):
            assert devices.seen_before("mag-recorder") is False
            assert devices.seen_before("gmag-webui") is False
            (spool / "samples-2026-10-01.jsonl").write_text("{}\n")
            assert devices.seen_before("mag-recorder") is True
            assert devices.seen_before("gmag-webui") is True


def test_gpsdo_seen_before_reads_location_source():
    from sigmond import coordination, site_profile
    from sigmond.drift import devices
    cases = [
        ('[station]\nlocation_source = "gpsdo"\n', "", True),
        ('[station]\nlocation_source = "operator"\n', "", False),
        ('[station]\ngrid_square = "EN34"\n', "", False),
        ("", '[host]\nlocation_source = "gpsdo"\n', True),
        ("", '[host]\ngrid = "EN34"\n', False),
        ("", "", False),
    ]
    for prof, coord, want in cases:
        with tempfile.TemporaryDirectory() as tmp:
            pp, cp = Path(tmp) / "site-profile.toml", Path(tmp) / "coordination.toml"
            if prof:
                pp.write_text(prof)
            if coord:
                cp.write_text(coord)
            with mock.patch.object(site_profile, "SITE_PROFILE_PATH", pp), \
                    mock.patch.object(coordination, "COORDINATION_PATH", cp):
                assert devices.seen_before("gpsdo-monitor") is want, (prof, coord)


# --- ruling 2: a dormant client is not "stopped" drift ------------------------

def _unit_report(*, ready, seen):
    from sigmond.drift import build_report as br
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "mag-recorder").mkdir()
        (base / "mag-recorder" / "deploy.toml").write_text(
            '[systemd]\nunits = ["mag-recorder.service"]\n')
        unit_dir = base / "units"
        unit_dir.mkdir()
        (unit_dir / "mag-recorder.service").write_text("[Service]\n")
        readers = br.Readers(
            base=base, sigmond_repo=base / "sigmond", unit_dir=unit_dir,
            exceptions_path=base / "none.toml",
            enabled={"mag-recorder": True}, gated={"mag-recorder": MAG},
            service_active=lambda u: False, is_enabled=lambda u: False,
            device_ready=lambda n: ready, device_seen=lambda n: seen)
        return [i for i in br.build("AC0G-T", readers=readers).items
                if "mag-recorder" in i.subject]


def test_switched_off_client_is_not_stopped_drift():
    # AC1SN after the switch-off: smd check reads "no magnetometer;
    # dormant" as ok, so the drift report says nothing either.
    assert _unit_report(ready=False, seen=False) == []


def test_seen_before_client_gets_the_fault_only():
    items = _unit_report(ready=False, seen=True)
    assert [(i.severity, i.subject) for i in items] == [("fault", "mag-recorder")]
    assert not any("smd start" in i.fix for i in items)


def test_stopped_client_with_device_stays_drift():
    for ready in (True, None):
        items = _unit_report(ready=ready, seen=False)
        assert [(i.severity, i.fix) for i in items] == [("drift", "smd start mag-recorder")]


# --- ruling 3: align re-renders sigmond's gmag-webui unit ---------------------

_OLD_UNIT = "[Unit]\nDescription=old\n[Service]\nExecStart=/bin/true\n"


def _reloads(calls):
    return [c for c in calls if c[:2] == ["systemctl", "daemon-reload"]]


def test_gmag_unit_rerendered_and_running_service_restarted_once():
    res = {}
    sysd, out, _, _ = _drive(ready={"mag-recorder": True, "gmag-webui": True},
                             gmag_unit=_OLD_UNIT, result=res)
    assert res["unit"] == res["render"]
    assert "ConditionPathExists=/dev/ttyMAG0" in res["unit"]
    assert len(_reloads(sysd.calls)) == 1, out
    assert _touching(sysd.calls, "gmag-webui.service") == [
        ["systemctl", "restart", "gmag-webui.service"]], out


def test_gmag_unit_current_untouched():
    sysd, out, _, _ = _drive(ready={"mag-recorder": True, "gmag-webui": True})
    assert _reloads(sysd.calls) == [] and _mutating(sysd.calls) == [], out


def test_gmag_unit_rerendered_inactive_not_started():
    res = {}
    on = [u for u in _ALL_UNITS if u != "gmag-webui.service"]
    sysd, out, _, _ = _drive(ready={"mag-recorder": None, "gmag-webui": None},
                             gmag_unit=_OLD_UNIT, active_units=on, result=res)
    assert res["unit"] == res["render"]
    assert len(_reloads(sysd.calls)) == 1
    assert _touching(sysd.calls, "gmag-webui.service") == [], out


def test_gmag_unit_dry_run_lists_only():
    res = {}
    sysd, out, _, _ = _drive(ready={"mag-recorder": True, "gmag-webui": True},
                             gmag_unit=_OLD_UNIT, dry=True, result=res)
    assert res["unit"] == _OLD_UNIT
    assert _reloads(sysd.calls) == [] and _mutating(sysd.calls) == [], out
    assert "would re-render gmag-webui.service" in out


def test_gmag_unit_no_restart_writes_without_restarting():
    res = {}
    sysd, out, _, _ = _drive(ready={"mag-recorder": True, "gmag-webui": True},
                             gmag_unit=_OLD_UNIT, no_restart=True, result=res)
    assert res["unit"] == res["render"]
    assert _touching(sysd.calls, "gmag-webui.service") == [], out
    assert "--no-restart" in out


def test_gmag_unit_not_installed_not_written():
    res = {}
    sysd, out, _, _ = _drive(ready={"mag-recorder": True, "gmag-webui": True},
                             gmag_unit=None, result=res)
    assert res["unit"] is None
    assert _reloads(sysd.calls) == []


def test_gmag_unit_running_without_device_not_restarted():
    # The new condition would keep a restarted unit down: the restart waits.
    res = {}
    sysd, out, _, _ = _drive(ready={"mag-recorder": None, "gmag-webui": None},
                             gmag_unit=_OLD_UNIT, gmag_device=False, result=res)
    assert res["unit"] == res["render"]
    assert _touching(sysd.calls, "gmag-webui.service") == [], out


def test_gmag_unit_drift_exception_keeps_it():
    from tests.test_align_enables_units import _exceptions_text
    res = {}
    sysd, out, _, _ = _drive(ready={"mag-recorder": True, "gmag-webui": True},
                             gmag_unit=_OLD_UNIT, result=res,
                             exceptions=_exceptions_text(["gmag-webui.service"]))
    assert res["unit"] == _OLD_UNIT
    assert _reloads(sysd.calls) == []


# --- review fix round ---------------------------------------------------------

def test_masked_gmag_unit_left_across_two_aligns():
    # An operator masked gmag-webui: the unit path is a symlink to /dev/null.
    res = {}
    sysd, out, _, _ = _drive(ready={"mag-recorder": True, "gmag-webui": True},
                             gmag_masked=True, masked=("gmag-webui.service",),
                             enabled_units=[u for u in _ALL_UNITS
                                            if u != "gmag-webui.service"],
                             active_units=[u for u in _ALL_UNITS
                                           if u != "gmag-webui.service"],
                             runs=2, result=res)
    assert res["link"] == "/dev/null", out
    assert _touching(sysd.calls, "gmag-webui.service") == [], out
    assert _reloads(sysd.calls) == [], out
    assert "gmag-webui.service" not in sysd.enabled | sysd.active


def test_masked_unit_makes_follow_devices_hands_off():
    sysd, out, _, _ = _drive(ready={"mag-recorder": False, "gmag-webui": False},
                             masked=("gmag-webui.service",))
    assert _touching(sysd.calls, "gmag-webui.service") == [], out
    assert "gmag-webui: left as it is: gmag-webui.service is masked" in out
    assert ["systemctl", "disable", "--now", "mag-recorder.service"] in sysd.calls


def test_unreadable_mag_config_counts_as_seen():
    from sigmond.drift import devices
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "c.toml"
        cfg.write_text("this is [not toml\n")
        empty = Path(tmp) / "empty"
        empty.mkdir()
        with mock.patch.object(devices, "MAG_CONFIG", cfg), \
                mock.patch.object(devices, "MAG_SPOOL_DEFAULT", empty):
            assert devices.seen_before("mag-recorder") is True
        cfg.write_text("")
        cfg.chmod(0)
        with mock.patch.object(devices, "MAG_CONFIG", cfg), \
                mock.patch.object(devices, "MAG_SPOOL_DEFAULT", empty), \
                mock.patch("builtins.open", side_effect=PermissionError("denied")):
            assert devices.seen_before("mag-recorder") is True
        missing = Path(tmp) / "absent.toml"
        with mock.patch.object(devices, "MAG_CONFIG", missing), \
                mock.patch.object(devices, "MAG_SPOOL_DEFAULT", empty):
            assert devices.seen_before("mag-recorder") is False


def test_dry_run_under_no_restart_names_only_what_apply_does():
    # cmd_align refuses a dry run with --no-restart today; the step itself
    # still names only what --apply would do when told not to start.
    sysd, out, _, _ = _drive(ready={"mag-recorder": False, "gmag-webui": False},
                             call=lambda: smd._align_follow_devices(False, start=False))
    assert _mutating(sysd.calls) == [], out
    assert "would switch off" not in out
    assert "mag-recorder: not switched off (--no-restart)" in out
    sysd, out, _, _ = _drive(ready={"mag-recorder": True, "gmag-webui": True},
                             enabled_units=[], active_units=[],
                             call=lambda: smd._align_follow_devices(False, start=False))
    assert "would enable and start" not in out
    assert "not switched on (--no-restart)" in out


def test_readiness_probe_that_raises_reads_unknown():
    from sigmond.drift import build_report as br

    def boom(name):
        raise RuntimeError("inventory hung")
    with tempfile.TemporaryDirectory() as tmp:
        readers = br.Readers(
            base=Path(tmp), sigmond_repo=Path(tmp) / "sigmond",
            unit_dir=Path(tmp), exceptions_path=Path(tmp) / "none.toml",
            enabled={"mag-recorder": True}, gated={"mag-recorder": MAG},
            device_ready=boom, device_seen=lambda n: True)
        items = br.build("AC0G-T", readers=readers).items
    assert not [i for i in items if i.subject == "check"], items
    assert not [i for i in items if i.subject == "mag-recorder"]

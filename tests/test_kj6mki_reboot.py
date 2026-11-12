"""KJ6MKI's first bare-metal reboot (2026-11-12): boot-time wisdom went fresh
with radiod stopped, resume left a unit down, `smd enable` did not persist,
a conf-less radiod counted as a radiod, and `diag net --listen` said sudo."""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent
MODEL = "AMD Ryzen 5 5560U with Radeon Graphics"


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_kj6mki_reboot", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_kj6mki_reboot", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def _ok(argv, out=""):
    return subprocess.CompletedProcess(argv, 0, out, "")


# ── 1. --auto installs this CPU's bundle instead of a fresh plan ────────────

@pytest.fixture
def wisdom_host(tmp_path, monkeypatch):
    from sigmond import wisdom as w
    bdir = tmp_path / "bundles"; bdir.mkdir()
    (bdir / "wisdomf-amd-ryzen-5-5560u").write_text("(bundle plans)\n")
    (bdir / "wisdomf-amd-ryzen-5-5560u.cpu").write_text(f"model={MODEL}\n")
    cpuinfo = tmp_path / "cpuinfo"; cpuinfo.write_text(f"model name\t: {MODEL}\n")
    etc = tmp_path / "fftw"; etc.mkdir()
    (etc / "wisdomf").write_text("(foreign plans)\n")
    monkeypatch.setattr(w, "WISDOM_FILE", etc / "wisdomf")
    monkeypatch.setattr(w, "WISDOM_SIDECAR", etc / "wisdomf.cpu")
    monkeypatch.setattr(w, "CPUINFO", cpuinfo)
    monkeypatch.setattr(smd, "_WISDOM_BUNDLE_DIRS", [bdir])
    for f in ("_info", "_warn", "_ok", "_err"):
        monkeypatch.setattr(smd, f, lambda *a, **k: None)
    return etc


def test_bundle_for_this_cpu_is_installed(wisdom_host):
    assert smd._wisdom_install_cpu_bundle()
    assert (wisdom_host / "wisdomf").read_text() == "(bundle plans)\n"
    assert (wisdom_host / "wisdomf.cpu").read_text() == f"model={MODEL}\n"


def test_no_bundle_for_this_cpu_leaves_wisdom_alone(wisdom_host, monkeypatch, tmp_path):
    monkeypatch.setattr(smd, "_WISDOM_BUNDLE_DIRS", [tmp_path / "none"])
    assert not smd._wisdom_install_cpu_bundle()
    assert (wisdom_host / "wisdomf").read_text() == "(foreign plans)\n"


def test_auto_takes_the_bundle_and_skips_the_fresh_plan(wisdom_host, monkeypatch):
    verdicts = iter(["fresh", "current"])
    monkeypatch.setattr(smd, "_wisdom_verdict", lambda *a, **k: next(verdicts))
    monkeypatch.setattr(smd.shutil, "which", lambda n: "/usr/bin/" + n)
    ran = []
    monkeypatch.setattr(smd.subprocess, "run", lambda *a, **k: ran.append(a) or _ok(a))
    rc = smd.cmd_wisdom_plan(NS(auto=True, fresh=False, no_install=False))
    assert rc == 0
    assert ran == []              # never reached the busy check or the planner
    assert (wisdom_host / "wisdomf").read_text() == "(bundle plans)\n"


# ── 2. resume restarts exactly what suspend stopped ─────────────────────────

def test_resume_restarts_every_unit_suspend_stopped(tmp_path, monkeypatch):
    marker = tmp_path / "wisdom-suspended"
    monkeypatch.setattr(smd, "_WISDOM_SUSPEND_MARKER", marker)
    active = {"radiod@X.service", "igmp-querier.service", "psk-recorder@X.service"}
    monkeypatch.setattr(smd, "_active_service_units", lambda: set(active))

    def stop(_a):
        active.clear()
        return 0
    monkeypatch.setattr(smd, "cmd_stop", stop)
    smd.cmd_wisdom_suspend(NS())
    assert sorted(smd._read_suspended_units()) == sorted(
        ["radiod@X.service", "igmp-querier.service", "psk-recorder@X.service"])

    def start(_a):                       # a bare start that forgets igmp-querier
        active.update({"radiod@X.service", "psk-recorder@X.service"})
        return 0
    monkeypatch.setattr(smd, "cmd_start", start)
    monkeypatch.setattr(smd, "_system_stopping", lambda: False)
    monkeypatch.setattr(smd, "_radiods_older_than_wisdom", lambda *a: [])
    calls = []
    monkeypatch.setattr(smd, "_run", lambda argv, **k: calls.append(argv) or _ok(argv))
    monkeypatch.setattr(smd, "_ok", lambda *a: None)
    assert smd.cmd_wisdom_resume(NS()) == 0
    assert ["systemctl", "start", "igmp-querier.service"] in calls
    assert not marker.exists()


# ── 3. smd enable persists the units at boot ────────────────────────────────

def test_enable_units_for_boot(monkeypatch):
    units = [NS(unit="gpsdo-monitor.service", orphaned=False),
             NS(unit="gpsdo-monitor@old.service", orphaned=True)]
    monkeypatch.setattr(smd, "resolve_units", lambda *a, **k: units)
    calls = []

    def run(argv, **k):
        calls.append(argv)
        return _ok(argv, "disabled\n" if argv[1] == "is-enabled" else "")
    monkeypatch.setattr(smd, "_run", run)
    monkeypatch.setattr(smd, "_ok", lambda *a: None)
    assert smd._enable_units_for_boot("gpsdo-monitor") == ["gpsdo-monitor.service"]
    assert ["systemctl", "enable", "gpsdo-monitor.service"] in calls
    assert not any("old" in " ".join(c) for c in calls if c[1] == "enable")


def test_cmd_enable_calls_it():
    src = (REPO / "bin" / "smd").read_text()
    i = src.index("def cmd_enable(args)")
    assert "_enable_units_for_boot(entry.name)" in src[i:src.index("\ndef ", i + 10)]


# ── 4. a conf-less radiod is orphaned and not counted as a radiod ───────────

def test_confless_radiod_instance_is_orphaned(tmp_path, monkeypatch):
    from sigmond import lifecycle
    conf = tmp_path / "radio"; conf.mkdir()
    (conf / "radiod@KJ6MKI-Ryzen.conf").write_text("x")
    lu = json.dumps([{"unit": "radiod@KJ6MKI-Ryzen.service"},
                     {"unit": "radiod@rx888-wsprdaemon.service"}])

    def run(argv, **k):
        return _ok(argv, lu if "list-units" in argv else "")
    monkeypatch.setattr(lifecycle.subprocess, "run", run)
    units = lifecycle._expand_template("ka9q-radio", "radiod@.service", "templated",
                                       "service", conf_dir=str(conf))
    orph = {u.instance: u.orphaned for u in units}
    assert orph == {"KJ6MKI-Ryzen": False, "rx888-wsprdaemon": True}


def test_crash_looping_radiod_is_not_a_radiod_instance(monkeypatch, tmp_path):
    from sigmond import cpu
    monkeypatch.setattr(cpu, "Path", lambda p: tmp_path / "noradio" if p == "/etc/radio" else Path(p))
    lu = json.dumps([
        {"unit": "radiod@KJ6MKI-Ryzen.service", "active": "active", "sub": "running"},
        {"unit": "radiod@rx888-wsprdaemon.service", "active": "activating", "sub": "auto-restart"}])
    monkeypatch.setattr(cpu, "_run_capture", lambda argv: _ok(argv, lu))
    assert cpu.get_radiod_instances() == ["radiod@KJ6MKI-Ryzen.service"]


# ── 5. diag net --listen elevates instead of advising sudo ──────────────────

def test_diag_net_listen_elevates():
    src = (REPO / "bin" / "smd").read_text()
    i = src.index("def cmd_diag_net(args)")
    body = src[i:src.index("\ndef ", i + 10)]
    assert "_need_root('admin diag net --listen')" in body
    assert "IGMP queriers: passive listen skipped (re-run with sudo" not in src
    assert "re-run with sudo" not in (REPO / "lib/sigmond/net_diag.py").read_text()


def test_confless_radiod_is_orphaned_even_with_no_conf_at_all(tmp_path, monkeypatch):
    from sigmond import lifecycle
    conf = tmp_path / "radio"; conf.mkdir()
    lu = json.dumps([{"unit": "radiod@rx888-wsprdaemon.service"}])
    monkeypatch.setattr(lifecycle.subprocess, "run",
                        lambda argv, **k: _ok(argv, lu if "list-units" in argv else ""))
    units = lifecycle._expand_template("ka9q-radio", "radiod@.service", "templated",
                                       "service", conf_dir=str(conf))
    assert [(u.instance, u.orphaned) for u in units] == [("rx888-wsprdaemon", True)]

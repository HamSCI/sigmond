"""A radiod configured as Phil's split layout, /etc/radio/radiod@<id>.conf.d/
(global.conf, rx888.conf, ...), with no radiod@<id>.conf beside it, is a real
radiod.  KFS-OMNI 2026-11-13 -> 11-28: sigmond saw no .conf, marked
radiod@KFS-OMNI orphaned, and the affinity writer deleted its
smd-cpu-affinity.conf as an orphan; the 129.6 Msps radiod then ran unpinned on
the decoders' cores (fft 92 %, proc_rx888 100 % of a core) for two weeks while
`smd admin diag cpu-affinity` showed it as OK.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_radiod_conf_d", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_radiod_conf_d", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _ok(argv, out=""):
    return subprocess.CompletedProcess(argv, 0, out, "")


@pytest.fixture
def radio(tmp_path):
    d = tmp_path / "radio"; d.mkdir()
    (d / "radiod@KFS-SW.conf").write_text("[global]\nsamprate = 64800000\nstatus = KFS-SW-status.local\n")
    split = d / "radiod@KFS-OMNI.conf.d"; split.mkdir()
    (split / "global.conf").write_text("[global]\nstatus = KFS-OMNI-status.local\n")
    (split / "rx888.conf").write_text("[rx888]\nsamprate = 129600000\n")
    empty = d / "radiod@EMPTY.conf.d"; empty.mkdir()
    (empty / "notes.txt").write_text("not read by radiod\n")
    (d / "radiod@KFS-OMNI.conf~").write_text("editor backup\n")
    return d


# ── discovery ───────────────────────────────────────────────────────────────

def test_shared_discovery_finds_conf_and_conf_d(radio):
    from sigmond.radiod_confs import radiod_conf_instances
    found = radiod_conf_instances(radio)
    assert set(found) == {"KFS-SW", "KFS-OMNI"}
    assert found["KFS-OMNI"].is_dir() and found["KFS-SW"].is_file()


def test_conf_d_text_is_its_conf_files_in_name_order(radio):
    from sigmond.radiod_confs import read_radiod_conf
    text = read_radiod_conf(radio / "radiod@KFS-OMNI.conf.d")
    assert text.index("[global]") < text.index("[rx888]")
    assert "notes" not in read_radiod_conf(radio / "radiod@EMPTY.conf.d")


def test_lifecycle_conf_d_radiod_is_not_orphaned(radio, monkeypatch):
    from sigmond import lifecycle
    lu = json.dumps([{"unit": "radiod@KFS-OMNI.service"}, {"unit": "radiod@KFS-SW.service"},
                     {"unit": "radiod@EMPTY.service"}])
    monkeypatch.setattr(lifecycle.subprocess, "run",
                        lambda argv, **k: _ok(argv, lu if "list-units" in argv else ""))
    units = lifecycle._expand_template("ka9q-radio", "radiod@.service", "templated",
                                       "service", conf_dir=str(radio))
    assert {u.instance: u.orphaned for u in units} == {
        "KFS-OMNI": False, "KFS-SW": False, "EMPTY": True}


def test_cpu_radiod_instances_include_conf_d(radio, monkeypatch):
    from sigmond import cpu
    monkeypatch.setattr(cpu, "Path", lambda p: radio if p == "/etc/radio" else Path(p))
    monkeypatch.setattr(cpu, "_run_capture", lambda argv: _ok(argv, "[]"))
    assert cpu.get_radiod_instances() == ["radiod@KFS-OMNI.service", "radiod@KFS-SW.service"]


def test_radiod_adapter_reads_samprate_from_conf_d(radio, monkeypatch):
    from sigmond.clients import radiod as adapter
    monkeypatch.setattr(adapter, "RADIO_CONF_DIR", radio)
    view = adapter.RadiodAdapter().read_view()
    by = {i.instance: i for i in view.instances}
    assert set(by) == {"KFS-OMNI", "KFS-SW"}


# ── the affinity writer never strips a running radiod ────────────────────────

def test_orphan_sweep_keeps_a_running_radiods_pinning(tmp_path, monkeypatch):
    smd = _load_smd()
    sysd = tmp_path / "systemd"; sysd.mkdir()
    for inst in ("KFS-OMNI", "GONE"):
        d = sysd / f"radiod@{inst}.service.d"; d.mkdir()
        (d / "smd-cpu-affinity.conf").write_text("[Service]\nCPUAffinity=6 7\n")
    removed = []

    def run(cmd, **k):
        if cmd[:2] == ["rm", "-rf"]:
            removed.append(Path(cmd[2]).name)
        if cmd[:2] == ["systemctl", "is-active"]:
            return _ok(cmd, "active\n" if "KFS-OMNI" in cmd[-1] else "inactive\n")
        return _ok(cmd)
    monkeypatch.setattr(smd, "_run", run)
    smd._sweep_orphan_radiod_drop_ins(valid_unit_names={"radiod@KFS-SW.service"}, sys_dir=sysd)
    assert removed == ["radiod@GONE.service.d"]


# ── the CPU check flags a running radiod with no pinning ─────────────────────

def test_affinity_report_warns_on_unpinned_running_radiod():
    from sigmond import cpu
    ua = cpu.UnitAffinity(unit="radiod@KFS-OMNI.service", role="radiod",
                          main_pid="123", observed_mask={0, 1, 2, 3, 4, 5, 6, 7, 8, 9})
    assert ua.unpinned_radiod
    assert not cpu.UnitAffinity(unit="radiod@KFS-SW.service", role="radiod", main_pid="9",
                                systemd_mask={8, 9}, observed_mask={8, 9}).unpinned_radiod
    assert not cpu.UnitAffinity(unit="radiod@X.service", role="radiod").unpinned_radiod
    w = cpu.unpinned_radiod_warnings([ua])
    assert len(w) == 1 and "radiod@KFS-OMNI.service" in w[0] and "--apply" in w[0]

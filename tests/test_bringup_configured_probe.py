"""Bring-up decides "configured" by asking whether config init would change
the file (spec 4.3, F8).

sigma, 2026-11-05: bring-up skipped mag-recorder as "already configured".
Its template held <YOUR_CALL>, <YOUR_GRID> and <YOUR_PSWS_STATION_ID>, and
the probe knew one sentinel.  A token counts when the env handed to
`smd config init` can fill it.  An unissued PSWS id never counts, and the
client's own `validate --json` only supplies the message: mag-recorder
raises "fail" for blocked delivery too, and an unenrolled station would
otherwise be reconfigured on every bring-up.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_cfgprobe", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_cfgprobe", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

SIGMA_TEMPLATE = ('[station]\npsws_station_id = "<YOUR_PSWS_STATION_ID>"\n'
                  'callsign = "<YOUR_CALL>"\ngrid_square = "<YOUR_GRID>"\n')
UNENROLLED = ('[station]\npsws_station_id = "<YOUR_PSWS_STATION_ID>"\n'
              'callsign = "AC0G"\ngrid_square = "EM38ww"\n')
FAIL_MSG = "station.psws_station_id unset: uploads blocked"


def _fake(monkeypatch, cfg, env, validate="ok"):
    monkeypatch.setattr(shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(smd, "_probe_config_env", lambda client: env)
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[1] == "inventory":
            return types.SimpleNamespace(
                returncode=0, stdout=json.dumps({"config_path": str(cfg)}))
        if validate == "timeout":
            raise subprocess.TimeoutExpired(cmd, 30)
        out = json.dumps({"ok": False, "issues": [
            {"severity": "fail", "instance": "all", "message": FAIL_MSG}]})
        return types.SimpleNamespace(returncode=1, stdout=out)
    monkeypatch.setattr(smd, "_run", run)
    return calls


def test_fillable_token_is_unconfigured_and_flags_reconfig(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text(SIGMA_TEMPLATE)
    _fake(monkeypatch, cfg, {"STATION_CALLSIGN": "AC0G"})
    ok, detail, reconf = smd._bringup_probe("configured:mag-recorder", "smd")
    assert (ok, reconf) == (False, True) and "UNCONFIGURED" in detail


def test_unfillable_psws_token_only_is_configured(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text(UNENROLLED)
    _fake(monkeypatch, cfg, {"STATION_CALLSIGN": "AC0G", "STATION_GRID": "EM38ww"})
    ok, _, reconf = smd._bringup_probe("configured:mag-recorder", "smd")
    assert (ok, reconf) == (True, False)


def test_unenrolled_station_stays_configured_and_detail_quotes_the_fail(
        tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text(UNENROLLED)
    calls = _fake(monkeypatch, cfg, {"STATION_CALLSIGN": "AC0G"})
    ok, detail, reconf = smd._bringup_probe("configured:mag-recorder", "smd")
    assert (ok, reconf) == (True, False) and FAIL_MSG in detail
    vcmd, kw = [c for c in calls if c[0][1] == "validate"][0]
    assert vcmd[2:] == ["--json", "--config", str(cfg)]
    assert kw.get("timeout") == 30


def test_validate_timing_out_changes_no_verdict(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text(UNENROLLED)
    _fake(monkeypatch, cfg, {}, validate="timeout")
    assert smd._bringup_probe("configured:mag-recorder", "smd") == (
        True, f"mag-recorder: {cfg} present", False)
    cfg.write_text(SIGMA_TEMPLATE)
    _fake(monkeypatch, cfg, {"STATION_CALLSIGN": "AC0G"}, validate="timeout")
    assert smd._bringup_probe("configured:mag-recorder", "smd")[::2] == (False, True)


def test_sentinel_always_unconfigured_with_no_env():
    assert smd._config_is_placeholder('a = "<configure-via-config-init>"\n')


HF_TEMPLATE_PSWS_LEFT = ('[station]\nid = "<YOUR_STATION_ID>"\n'
                         'callsign = "AI6VN"\ngrid_square = "CM87"\n')


def test_enrolled_mag_station_is_configured_no_reconfig(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text(UNENROLLED)
    _fake(monkeypatch, cfg, {"STATION_CALLSIGN": "AC0G", "STATION_GRID": "EM38ww",
                             "STATION_PSWS_STATION_ID": "S000082"})
    ok, _, reconf = smd._bringup_probe("configured:mag-recorder", "smd")
    assert (ok, reconf) == (True, False)


def test_hf_timestd_with_psws_token_left_and_id_in_env_is_configured(
        tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text(HF_TEMPLATE_PSWS_LEFT)
    _fake(monkeypatch, cfg, {"STATION_CALL": "AI6VN", "STATION_GRID": "CM87",
                             "STATION_PSWS_STATION_ID": "S000111"})
    ok, _, reconf = smd._bringup_probe("configured:hf-timestd", "smd")
    assert (ok, reconf) == (True, False)


def test_your_callsign_with_callsign_in_env_is_unconfigured(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[station]\ncallsign = "<YOUR_CALLSIGN>"\n')
    _fake(monkeypatch, cfg, {"STATION_CALLSIGN": "AI6VN"})
    ok, _, reconf = smd._bringup_probe("configured:hf-timestd", "smd")
    assert (ok, reconf) == (False, True)


def test_env_builder_raising_warns_and_judges_by_sentinel(monkeypatch, capsys):
    import sys
    mod = types.ModuleType("sigmond.commands.client_config")

    def boom(**kw):
        raise RuntimeError("coordination unreadable")
    mod._build_env_bag = boom
    monkeypatch.setitem(sys.modules, "sigmond.commands.client_config", mod)
    assert smd._probe_config_env("mag-recorder") == {}
    err = capsys.readouterr()
    assert "coordination unreadable" in err.out + err.err

"""Bring-up asks each client whether it is configured (spec 4.3, F8).

sigma, 2026-11-05: bring-up skipped mag-recorder as "already configured".
The client's install had just laid down its template, whose placeholders
read `<YOUR_PSWS_STATION_ID>`, `<YOUR_CALL>` and `<YOUR_GRID>`.  The probe
knew one sentinel, `<configure-via-config-init>`, so the magnetometer never
started.  The probe now runs the client's own `validate --json`.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
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

MAG_TEMPLATE = '[station]\npsws_station_id = "<YOUR_PSWS_STATION_ID>"\n' \
               'callsign = "<YOUR_CALL>"\ngrid_square = "<YOUR_GRID>"\n'


def _fake(monkeypatch, cfg, validate_out, validate_rc=1):
    """Pretend `mag-recorder` is on PATH; return the commands it was asked."""
    calls = []
    monkeypatch.setattr(shutil, "which", lambda n: f"/usr/bin/{n}")

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[1] == "inventory":
            out, rc = json.dumps({"config_path": str(cfg)}), 0
        else:
            out, rc = validate_out, validate_rc
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr="")
    monkeypatch.setattr(smd, "_run", run)
    return calls


def test_mag_recorders_fresh_template_is_unconfigured(tmp_path, monkeypatch):
    cfg = tmp_path / "mag.toml"
    cfg.write_text(MAG_TEMPLATE)
    payload = {"ok": False, "config_path": str(cfg), "issues": [
        {"severity": "fail", "instance": "all", "message": "template"}]}
    calls = _fake(monkeypatch, cfg, json.dumps(payload), validate_rc=1)
    ok, detail = smd._bringup_probe("configured:mag-recorder", "smd")
    assert ok is False and "UNCONFIGURED" in detail
    vcmd, kw = [c for c in calls if c[0][1] == "validate"][0]
    assert vcmd[2] == "--json" and kw.get("timeout") == 30


def test_a_client_whose_validate_passes_is_configured(tmp_path, monkeypatch):
    # Optional placeholders left alone (AI6VN hf-timestd, 2026-09-21) must
    # not count against a client that says it is fine.
    cfg = tmp_path / "c.toml"
    cfg.write_text(MAG_TEMPLATE)
    payload = {"ok": True, "config_path": str(cfg), "issues": [
        {"severity": "warn", "instance": "all", "message": "optional"}]}
    _fake(monkeypatch, cfg, json.dumps(payload), validate_rc=0)
    ok, _ = smd._bringup_probe("configured:mag-recorder", "smd")
    assert ok is True


def test_unusable_validate_reply_falls_back_to_placeholder_check(
        tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text(MAG_TEMPLATE)
    _fake(monkeypatch, cfg, "usage: error, no --json", validate_rc=2)
    assert smd._bringup_probe("configured:mag-recorder", "smd")[0] is False
    cfg.write_text('[station]\ncallsign = "AC0G"\n')
    assert smd._bringup_probe("configured:mag-recorder", "smd")[0] is True


def test_without_a_validate_cli_any_angle_bracket_token_counts_as_template():
    for text in ('a = "<YOUR_PSWS_STATION_ID>"\n',
                 'a = "<configure-via-config-init>"\n',
                 MAG_TEMPLATE):
        assert smd._config_is_placeholder(text), text
    assert not smd._config_is_placeholder('a = "see <docs>"\nb = "a < b > c"\n')

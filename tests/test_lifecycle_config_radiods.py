"""hfdl-recorder and codar-sounder name their instances after the radiods in
their own config's [[radiod]] blocks (2026-11-12).  None of the other instance
sources (env files, per-instance TOML, conf_dir, units systemd knows) reads
those, so on a fresh station `smd start` started nothing for codar-sounder
and hfdl-recorder@default on AC1SN.  `[systemd] instances_from =
"config-radiods"` (and a built-in default for the two clients) derives them.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

from sigmond.lifecycle import resolve_units

CONFIG = """[station]
station_id = "AC1SN-1"

[[radiod]]
status = "a-status.local"

[radiod.bands]
enabled = ["HFDL13"]

[[radiod]]
status = "b-status.local"

[[radiod]]
status = "<configure-via-config-init>"
"""


def _resolve(tmp_path, monkeypatch, component, deploy_text, config_text):
    deploy = tmp_path / "deploy.toml"
    deploy.write_text(deploy_text)
    etc_dir = tmp_path / "etc"
    etc_dir.mkdir()
    if config_text is not None:
        (etc_dir / f"{component}-config.toml").write_text(config_text)
    monkeypatch.setattr("sigmond.lifecycle._find_deploy_toml",
                        lambda c: deploy if c == component else None)

    def fake_path(p):
        s = str(p)
        if s == f"/etc/{component}":
            return etc_dir
        if s.startswith("/etc/"):
            m = mock.Mock(spec=Path)
            m.exists.return_value = False
            m.glob.return_value = []
            return m
        return Path(p)
    monkeypatch.setattr("sigmond.lifecycle.Path", fake_path)
    monkeypatch.setattr("sigmond.lifecycle.subprocess.run",
                        lambda *a, **k: mock.Mock(returncode=0, stdout="[]"))
    return {u.unit for u in resolve_units([component], [component])}


def test_instances_come_from_the_config_radiods(tmp_path, monkeypatch):
    units = _resolve(tmp_path, monkeypatch, "hfdl-recorder",
                     '[systemd]\ntemplated_units = ["hfdl-recorder@.service", '
                     '"hfdl-recorder-squitters@.timer"]\ninstances_from = "config-radiods"\n',
                     CONFIG)
    assert units == {"hfdl-recorder@a-status.local.service",
                     "hfdl-recorder@b-status.local.service",
                     "hfdl-recorder-squitters@a-status.local.timer",
                     "hfdl-recorder-squitters@b-status.local.timer"}


def test_codar_sounder_derives_them_without_the_key(tmp_path, monkeypatch):
    # A codar-sounder checkout older than the deploy.toml key still gets them.
    units = _resolve(tmp_path, monkeypatch, "codar-sounder",
                     '[systemd]\ntemplated_units = ["codar-sounder@.service"]\n',
                     CONFIG.replace("[radiod.bands]\nenabled = [\"HFDL13\"]\n", ""))
    assert units == {"codar-sounder@a-status.local.service",
                     "codar-sounder@b-status.local.service"}


def test_an_unconfigured_client_gets_no_instance(tmp_path, monkeypatch):
    units = _resolve(tmp_path, monkeypatch, "codar-sounder",
                     '[systemd]\ntemplated_units = ["codar-sounder@.service"]\n',
                     '[[radiod]]\nstatus = "<configure-via-config-init>"\n')
    assert units == set()


def test_other_clients_are_unaffected(tmp_path, monkeypatch):
    units = _resolve(tmp_path, monkeypatch, "psk-recorder",
                     '[systemd]\ntemplated_units = ["psk-recorder@.service"]\n',
                     CONFIG)
    assert units == set()

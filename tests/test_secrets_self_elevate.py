"""`smd admin secrets install|template` self-elevate instead of refusing a non-root operator.

They used to check `geteuid() != 0` and stop, while the top of main refuses `sudo smd`,
so neither verb could run (N8UR Earthdata install, 2026-11-14).
"""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

SMD = Path(__file__).resolve().parents[1] / "bin" / "smd"


def _load():
    loader = importlib.machinery.SourceFileLoader("smd_secrets_mod", str(SMD))
    spec = importlib.util.spec_from_loader("smd_secrets_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("verb", ["install", "template"])
def test_secrets_verbs_ask_need_root_not_geteuid(monkeypatch, tmp_path, verb):
    smd = _load()
    asked = []
    monkeypatch.setattr(smd, "_need_root", lambda name: asked.append(name) or True)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    fn = smd.cmd_secrets_install if verb == "install" else smd.cmd_secrets_template
    assert fn(SimpleNamespace(source=str(tmp_path), identity=None)) == 1
    assert asked == [f"admin secrets {verb}"]

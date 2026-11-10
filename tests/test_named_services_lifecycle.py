"""`smd restart hs-uploader` acts on the catalog's declared `services`.

hs-uploader is kind "library" for its clients yet installs a daemon.  Topology
rarely lists it, so a named restart warned "not enabled" and did nothing.
"""
import importlib.util
import os
import types

import pytest

import sigmond.catalog
from sigmond.catalog import _entry_from_toml_block


def _smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = "bin/smd"
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


def _entry(name, **extra):
    return _entry_from_toml_block(name, dict(kind="library", description="", repo="", **extra))


CATALOG = {
    "hs-uploader": _entry("hs-uploader", services=["hs-uploader.service"]),
    "callhash": _entry("callhash"),
}


def _run(monkeypatch, verb, names, enabled=()):
    mod = _smd()
    calls, out = [], []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sigmond.catalog, "load_catalog", lambda: CATALOG)
    mod.load_catalog = lambda: CATALOG
    mod._need_root = lambda v: False
    mod._load_topology = lambda path: {"components": {c: {"enabled": True} for c in enabled}}
    mod._run = fake_run
    mod._warn = lambda m: out.append(("warn", m))
    mod._info = lambda m: out.append(("info", m))
    mod._ok = lambda m: out.append(("ok", m))
    mod._autoenable_named_on_start = lambda *a, **k: False
    args = types.SimpleNamespace(topology=None, names=list(names), components=None)
    rc = getattr(mod, f"cmd_{verb}")(args)
    return rc, calls, out


def test_entry_parses_services():
    assert CATALOG["hs-uploader"].services == ("hs-uploader.service",)
    assert CATALOG["callhash"].services == ()


def test_repo_catalog_declares_hs_uploader_service():
    cat = sigmond.catalog.load_catalog()
    assert cat["hs-uploader"].services == ("hs-uploader.service",)


@pytest.mark.parametrize("verb", ["start", "stop", "restart"])
def test_named_library_with_services_acts_without_warning(monkeypatch, verb):
    rc, calls, out = _run(monkeypatch, verb, ["hs-uploader"])
    assert rc == 0
    assert ["systemctl", verb, "hs-uploader.service"] in [c[:3] for c in calls]
    assert not [m for k, m in out if "not enabled in topology — skipping" in m]
    assert any(k == "info" and "hs-uploader" in m for k, m in out)


def test_named_library_without_services_still_warns(monkeypatch):
    rc, calls, out = _run(monkeypatch, "restart", ["callhash"])
    assert rc == 0
    assert not [c for c in calls if c[1] in ("restart", "start", "stop")]
    assert any(k == "warn" and "callhash: not enabled in topology" in m for k, m in out)


def test_bare_restart_unchanged(monkeypatch):
    rc, calls, out = _run(monkeypatch, "restart", [])
    assert not [c for c in calls if "hs-uploader.service" in c]
    assert not [m for k, m in out if "hs-uploader" in m]


def test_reload_named_services_entry_still_warns(monkeypatch):
    # Only start/stop/restart act on declared services; reload must not go silent.
    mod = _smd()
    out = []
    monkeypatch.setattr(sigmond.catalog, "load_catalog", lambda: CATALOG)
    mod.load_catalog = lambda: CATALOG
    mod._warn = lambda m: out.append(m)
    args = types.SimpleNamespace(names=["hs-uploader"], components=None)
    assert mod._resolve_only_list(args, {"components": {}}) == []
    assert any("hs-uploader: not enabled in topology" in m for m in out)
    out.clear()
    assert mod._resolve_only_list(args, {"components": {}}, services_handled=True) == []
    assert out == []


def test_start_never_autoenables_a_services_entry(monkeypatch):
    import sigmond.component_state
    import sigmond.topology
    mod = _smd()
    monkeypatch.setattr(sigmond.catalog, "load_catalog", lambda: CATALOG)
    flipped = []
    monkeypatch.setattr(sigmond.topology, "set_component_enabled",
                        lambda *a, **k: flipped.append(a))
    monkeypatch.setattr(sigmond.component_state, "compute_state",
                        lambda *a, **k: types.SimpleNamespace(installed=True))
    mod._load_topology = lambda path: {"components": {}}
    args = types.SimpleNamespace(names=["hs-uploader"], components=None)
    assert mod._autoenable_named_on_start(args, "/nonexistent") is False
    assert flipped == []

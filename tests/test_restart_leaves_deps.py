"""`smd restart <client>` restarts that client and never its dependencies.

On B4, 2026-10-20, `smd restart hf-timestd` and `smd restart hamsci-physics`
each restarted `radiod@AC0G-B4`: cmd_restart walked the catalog's `requires`
closure and restarted every unit in it (v3.74 row 6).  A dependency must be
started, which leaves a running one alone, and only the named client restarted.
"""
import importlib.util
import os
import types

import sigmond.catalog
import sigmond.component_state
from sigmond.lifecycle import UnitRef


def _smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = "bin/smd"
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


def _unit(component, unit):
    return UnitRef(component=component, unit=unit, template=None, instance=None,
                   kind="service", source="test")


CATALOG = {
    "radiod": types.SimpleNamespace(topology_alias="ka9q-radio", kind="service"),
    "hf-timestd": types.SimpleNamespace(topology_alias=None, kind="service"),
}


def _run_restart(monkeypatch, named):
    mod = _smd()
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sigmond.catalog, "load_catalog", lambda: CATALOG)
    monkeypatch.setattr(sigmond.component_state, "compute_state",
                        lambda *a, **k: types.SimpleNamespace(
                            can_start=True, cloned=True, display_status="ok"))
    mod._need_root = lambda verb: False
    mod._load_topology = lambda path: {}
    mod._resolve_only_list = lambda args, topo: list(named) if named else None
    mod._enabled_components = lambda topo, only: list(only or ["ka9q-radio", "hf-timestd"])
    mod.resolve_units = lambda comps, enabled: [
        _unit(c, {"ka9q-radio": "radiod@AC0G-B4.service",
                  "radiod": "radiod@AC0G-B4.service",
                  "hf-timestd": "timestd-fusion.service"}[c])
        for c in comps if c in ("ka9q-radio", "hf-timestd")]
    # hf-timestd requires ka9q-radio, as its deploy.toml declares.
    # The real closure emits a catalog name beside its topology alias.
    def closure(names, cat):
        out = list(names)
        if "hf-timestd" in names or "radiod" in names:
            out = ["radiod", "ka9q-radio"] + [n for n in out if n != "radiod"]
        return out
    mod._expand_requires_closure = closure
    mod._HAS_SIGMOND_LIB = False
    mod._run = fake_run
    args = types.SimpleNamespace(topology=None)
    assert mod.cmd_restart(args) == 0
    return [c[1:] for c in calls if c[1] in ("restart", "start")]


def test_named_client_restarts_and_radiod_only_starts(monkeypatch):
    verbs = _run_restart(monkeypatch, ["hf-timestd"])
    assert ["restart", "timestd-fusion.service"] in verbs
    assert ["restart", "radiod@AC0G-B4.service"] not in verbs
    assert ["start", "radiod@AC0G-B4.service"] in verbs


def test_naming_radiod_by_alias_restarts_it(monkeypatch):
    verbs = _run_restart(monkeypatch, ["ka9q-radio"])
    assert ["restart", "radiod@AC0G-B4.service"] in verbs


def test_naming_radiod_by_catalog_name_restarts_it(monkeypatch):
    # The topology calls it ka9q-radio; the operator typed radiod.
    verbs = _run_restart(monkeypatch, ["radiod"])
    assert ["restart", "radiod@AC0G-B4.service"] in verbs


def test_bare_restart_restarts_everything(monkeypatch):
    verbs = _run_restart(monkeypatch, None)
    assert all(v[0] == "restart" for v in verbs) and len(verbs) == 2

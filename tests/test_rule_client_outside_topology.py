"""A client whose units run while topology says off (2026-11-12): sigmond
honours topology, so such a client gets no upload pipelines, no sink switch
above `fill`, no lifecycle.  It held HFDL squitter timing at K3LR, UACNJ,
W2NAF and WB6CXC for weeks with nothing saying why."""

from __future__ import annotations

from types import SimpleNamespace

from sigmond import harmonize
from sigmond.topology import Component, Topology


def _view(enabled):
    comps = {n: Component(name=n, enabled=True) for n in enabled}
    return SimpleNamespace(topology=Topology(client_dir="/nonexistent", smd_bin="smd", components=comps))


def test_warns_for_a_running_client_topology_leaves_off(monkeypatch):
    monkeypatch.setattr(harmonize, "_client_units_active",
                        lambda name: name in {"hfdl-recorder", "wspr-recorder"})
    r = harmonize.rule_client_outside_topology(_view(["wspr-recorder"]))
    assert r.severity == "warn"
    assert r.affected == ["hfdl-recorder"]
    assert "smd enable hfdl-recorder" in r.message


def test_passes_when_every_running_client_is_enabled(monkeypatch):
    monkeypatch.setattr(harmonize, "_client_units_active",
                        lambda name: name == "wspr-recorder")
    r = harmonize.rule_client_outside_topology(_view(["wspr-recorder"]))
    assert r.severity == "pass"


def test_is_a_runtime_rule():
    assert harmonize.rule_client_outside_topology in harmonize.ALL_RUNTIME_RULES

"""`smd admin wisdom suspend` / `resume` hand systemd one batch per group
instead of one serial `systemctl` call per unit (AI6VN-2, 2026-11-24: hf-timestd
units take up to a minute each to stop, so a serial suspend took ~4 minutes)."""
import importlib.machinery
import importlib.util
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_wisdom_batch", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_wisdom_batch", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

RADIOD = "radiod@X.service"
CLIENTS = ["psk-recorder@X.service", "hf-timestd-core.service", "hf-timestd-web.service"]


def _unit(name, component):
    return NS(unit=name, component=component, kind="service", orphaned=False)


class Fake:
    """A systemctl that records calls and tracks which units are active."""

    def __init__(self, active, stuck=(), fail_start=()):
        self.active = set(active)
        self.stuck = set(stuck)          # units that refuse to stop
        self.fail_start = set(fail_start)
        self.calls = []

    def run(self, argv, **k):
        self.calls.append(list(argv))
        verb, units = argv[1], argv[2:]
        rc, err = 0, ""
        if verb == "stop":
            for u in units:
                if u in self.stuck:
                    rc, err = 1, "Job timed out"
                else:
                    self.active.discard(u)
        elif verb == "start":
            for u in units:
                if u in self.fail_start:
                    rc, err = 1, "start failed"
                else:
                    self.active.add(u)
        elif verb == "is-active":
            rc = 0 if units[-1] in self.active else 3
        return subprocess.CompletedProcess(argv, rc, "", err)

    def of(self, verb):
        return [c for c in self.calls if c[1] == verb]


@pytest.fixture
def host(monkeypatch, tmp_path):
    monkeypatch.setattr(smd, "_WISDOM_SUSPEND_MARKER", tmp_path / "suspended")
    units = [_unit(RADIOD, "ka9q-radio")] + [_unit(c, "x") for c in CLIENTS]
    monkeypatch.setattr(smd, "_need_root", lambda *a: False)
    monkeypatch.setattr(smd, "_rekey_instances", lambda *a: None)
    monkeypatch.setattr(smd, "_netdiag_auto_refresh", lambda *a: None)
    monkeypatch.setattr(smd, "_heading", lambda *a: None)
    monkeypatch.setattr(smd, "_info", lambda *a: None)
    monkeypatch.setattr(smd, "_warn", lambda *a: None)
    monkeypatch.setattr(smd, "_enable_unit_for_start", lambda u: True)
    monkeypatch.setattr(smd, "_netdiag_auto_refresh", lambda *a: None)
    monkeypatch.setattr(smd, "_ensure_radiod_affinity_drop_ins", lambda *a, **k: None)
    monkeypatch.setattr(smd, "_load_topology", lambda *a, **k: {"components": {}})
    monkeypatch.setattr(smd, "_resolve_only_list", lambda *a, **k: None)
    monkeypatch.setattr(smd, "_enabled_components", lambda *a, **k: ["ka9q-radio", "x"])
    monkeypatch.setattr(smd, "_act_declared_services", lambda *a, **k: 0)
    monkeypatch.setattr(smd, "_autoenable_named_on_start", lambda *a, **k: False)
    monkeypatch.setattr(smd, "resolve_units", lambda *a, **k: list(units))
    # order_units: radiod first (start order); stop reverses it.
    monkeypatch.setattr(smd, "order_units", lambda u, **k: list(u))
    monkeypatch.setattr(smd, "_augment_stop_list_with_timer_services", lambda l: list(l))
    monkeypatch.setattr(smd, "_radiods_older_than_wisdom", lambda *a: [])
    monkeypatch.setattr(smd, "_system_stopping", lambda: False)
    return units


def _wire(monkeypatch, fake):
    monkeypatch.setattr(smd, "_run", fake.run)
    monkeypatch.setattr(smd, "_active_service_units", lambda: set(fake.active))
    msgs = {"ok": [], "err": []}
    monkeypatch.setattr(smd, "_ok", lambda m, *a: msgs["ok"].append(m))
    monkeypatch.setattr(smd, "_err", lambda m, *a: msgs["err"].append(m))
    return msgs


def test_suspend_is_one_stop_for_clients_then_one_for_radiod(host, monkeypatch):
    fake = Fake({RADIOD, *CLIENTS})
    _wire(monkeypatch, fake)
    assert smd.cmd_wisdom_suspend(NS()) == 0
    stops = fake.of("stop")
    assert len(stops) == 2
    assert sorted(stops[0][2:]) == sorted(CLIENTS)      # one call names every client
    assert stops[1] == ["systemctl", "stop", RADIOD]    # radiod stays last
    assert sorted(smd._read_suspended_units()) == sorted([RADIOD, *CLIENTS])


def test_suspend_reports_a_unit_that_would_not_stop_and_marks_the_rest(host, monkeypatch):
    stuck = "hf-timestd-core.service"
    fake = Fake({RADIOD, *CLIENTS}, stuck={stuck})
    msgs = _wire(monkeypatch, fake)
    assert smd.cmd_wisdom_suspend(NS()) == 1
    assert len(fake.of("stop")) == 2
    assert [m for m in msgs["err"] if m.startswith(smd._display_unit(stuck))]
    assert "stopped 3 units" in msgs["ok"]
    # the marker lists what is now down, not the stuck unit
    assert sorted(smd._read_suspended_units()) == sorted(
        [RADIOD, "psk-recorder@X.service", "hf-timestd-web.service"])


def test_resume_is_radiod_first_then_one_start_for_the_rest(host, monkeypatch):
    fake = Fake(set())
    smd._WISDOM_SUSPEND_MARKER.write_text(
        "suspended\n" + "".join(f"{u}\n" for u in [RADIOD, *CLIENTS]))
    msgs = _wire(monkeypatch, fake)
    assert smd.cmd_wisdom_resume(NS()) == 0
    starts = fake.of("start")
    assert starts[0] == ["systemctl", "start", RADIOD]
    assert sorted(starts[1][2:]) == sorted(CLIENTS)
    assert len(starts) == 2                             # nothing left for a third call
    assert not any("--no-block" in c for c in starts)   # blocking, as before
    assert len(msgs["ok"]) == 4 and not msgs["err"]
    assert not smd._WISDOM_SUSPEND_MARKER.exists()


def test_resume_reports_a_unit_that_failed_to_start(host, monkeypatch):
    bad = "hf-timestd-web.service"
    fake = Fake(set(), fail_start={bad})
    smd._WISDOM_SUSPEND_MARKER.write_text(
        "suspended\n" + "".join(f"{u}\n" for u in [RADIOD, *CLIENTS]))
    msgs = _wire(monkeypatch, fake)
    assert smd.cmd_wisdom_resume(NS()) == 1
    assert [m for m in msgs["err"] if m.startswith(smd._display_unit(bad))]
    assert len(msgs["ok"]) == 3

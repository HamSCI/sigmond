"""`smd bringup radiod-only`: Stage 1 only, ttl=1, ends on a published checkpoint.

A radiod-only host runs radiod for its SDRs and publishes it on the LAN for a
decoder host.  So its plan has no sink, no clients, no uploader and no
cpu-affinity step, and it is not done until every radiod it configured is
running and its status name resolves from the host itself.
"""
import importlib.machinery
import importlib.util
import io
import contextlib
import os
from argparse import Namespace
from pathlib import Path
from unittest import mock

import pytest

from sigmond import radiod_publish
from sigmond.bringup import STAGE3A, STAGE4, Plan, Step, build_plan
from sigmond.catalog import Profile
from sigmond.commands import radiod_config

REPO = Path(__file__).resolve().parent.parent

FORBIDDEN = ("uploader", "sink.db", "instance enable", "config render",
             "cpu-affinity")


@pytest.fixture
def prof_radiod_only():
    return Profile(name="radiod-only", clients=(), local_radiod_infra=(),
                   optional=("igmp-querier", "ka9q-web"))


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------

def test_radiod_only_plan_is_stage1_plus_start(prof_radiod_only):
    plan = build_plan(prof_radiod_only, local_radiod=True)
    labels = [s.label for s in plan.steps]
    assert any(l.startswith("configure radiod") for l in labels)
    assert any(s.check == "radiod-published" for s in plan.steps)
    for s in plan.steps:
        joined = " ".join(s.argv or ())
        assert not any(t in joined for t in FORBIDDEN), joined


def test_radiod_only_published_checkpoint_is_hard_and_after_streaming(
        prof_radiod_only):
    steps = build_plan(prof_radiod_only, local_radiod=True).steps
    kinds = [s.kind for s in steps]
    pub = next(i for i, s in enumerate(steps) if s.check == "radiod-published")
    assert steps[pub].hard and steps[pub].stage == STAGE4
    assert kinds.index("wait-wisdom") < pub
    assert kinds.index("wait-streaming") < pub
    assert steps[pub + 1].check == "validate"
    assert steps[-1].check == "validate"


def test_radiod_only_configure_passes_ttl_one(prof_radiod_only):
    step = next(s for s in build_plan(prof_radiod_only, local_radiod=True).steps
                if s.label.startswith("configure radiod"))
    assert step.argv[-2:] == ["--ttl", "1"]


def test_radiod_only_starts_radiod_and_chosen_optional_infra(prof_radiod_only):
    plan = build_plan(prof_radiod_only, local_radiod=True, with_optional=True)
    starts = [s.argv for s in plan.steps if s.kind == "start"]
    assert starts == [["smd", "start", "--components",
                       "ka9q-radio,igmp-querier,ka9q-web"]]
    # ka9q-web's unit needs the status name `configure radiod` just wrote.
    labels = [s.label for s in plan.steps]
    assert "install ka9q-web unit (post-radiod-config)" in labels
    plain = build_plan(prof_radiod_only, local_radiod=True)
    assert [s.argv for s in plain.steps if s.kind == "start"] == [
        ["smd", "start", "--components", "ka9q-radio"]]


def test_radiod_only_no_sdr_is_partial_with_no_client_steps(prof_radiod_only):
    plan = build_plan(prof_radiod_only, local_radiod=True, sdr_absent=True)
    assert plan.sdr_absent is True
    assert all(s.stage != STAGE3A for s in plan.steps)
    assert not any(s.check == "radiod-published" for s in plan.steps)
    for s in plan.steps:
        joined = " ".join(s.argv or ())
        assert not any(t in joined for t in FORBIDDEN), joined


def test_a_profile_with_clients_keeps_its_uploader_and_no_ttl():
    prof = Profile(name="dasi2", clients=("wspr-recorder",),
                   local_radiod_infra=("igmp-querier",), optional=())
    plan = build_plan(prof, local_radiod=True)
    joined = [" ".join(s.argv or ()) for s in plan.steps]
    assert any("uploader" in j for j in joined)
    assert not any("--ttl" in j for j in joined)
    assert not any(s.check == "radiod-published" for s in plan.steps)


# --------------------------------------------------------------------------
# radiod_publish.published
# --------------------------------------------------------------------------

class _R:
    def __init__(self, rc):
        self.returncode = rc


def _confs(tmp_path, **names):
    for rid, status in names.items():
        (tmp_path / f"radiod@{rid}.conf").write_text(
            f"[global]\nhardware = rx888\nstatus = {status}\n")
    return tmp_path


def _runner(active=(), resolves=()):
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        if argv[:2] == ["systemctl", "is-active"]:
            return _R(0 if argv[-1] in active else 3)
        if argv[:2] == ["getent", "hosts"]:
            return _R(0 if argv[-1] in resolves else 2)
        raise AssertionError(argv)
    run.calls = calls
    return run


def test_published_true_when_running_and_resolving(tmp_path):
    d = _confs(tmp_path, a="a-status.local", b="b-status.local")
    run = _runner(active={"radiod@a.service", "radiod@b.service"},
                  resolves={"a-status.local", "b-status.local"})
    assert radiod_publish.published(d, run=run) == {"a": True, "b": True}


def test_published_false_when_unit_down_or_name_unresolved(tmp_path):
    d = _confs(tmp_path, a="a-status.local", b="b-status.local")
    run = _runner(active={"radiod@a.service"}, resolves={"b-status.local"})
    assert radiod_publish.published(d, run=run) == {"a": False, "b": False}


def test_published_skips_bootstrap_and_handles_no_status(tmp_path):
    d = _confs(tmp_path, **{"bootstrap-fw": "x.local"})
    (d / "radiod@c.conf").write_text("[global]\nhardware = rx888\n")
    run = _runner(active={"radiod@c.service"})
    assert radiod_publish.published(d, run=run) == {"c": False}


def test_published_empty_without_confs(tmp_path):
    assert radiod_publish.published(tmp_path / "absent", run=_runner()) == {}


# --------------------------------------------------------------------------
# bin/smd: the probe and cmd_bringup
# --------------------------------------------------------------------------

def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_radiod_only", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def smd():
    return _load_smd()


@pytest.mark.parametrize("result,ok", [({"a": True, "b": True}, True),
                                       ({"a": True, "b": False}, False),
                                       ({}, False)])
def test_published_probe(smd, result, ok):
    with mock.patch("sigmond.radiod_publish.published", return_value=result):
        got, detail, _reconf = smd._bringup_probe("radiod-published", "smd")
    assert got is ok
    assert detail


@pytest.fixture
def tmp_role(tmp_path, monkeypatch):
    p = tmp_path / "station-role"
    monkeypatch.setattr("sigmond.station_role.ROLE_PATH", p)
    return p


def _topology(tmp_path, **enabled):
    p = tmp_path / "topology.toml"
    p.write_text("".join(f'[component.{n}]\nenabled = {str(v).lower()}\n\n'
                         for n, v in enabled.items()))
    return p


def _bringup(smd, prof, topo, *, dry_run, plan=None, probe=None):
    built = []

    def fake_build_plan(*a, **kw):
        built.append(kw)
        return plan or Plan(profile=prof.name, local_radiod=True, steps=[])

    args = Namespace(profile=prof.name, dry_run=dry_run, non_interactive=True)
    out = io.StringIO()
    with mock.patch.object(smd, "TOPOLOGY_PATH", topo), \
         mock.patch("sigmond.catalog.load_profiles", lambda: {prof.name: prof}), \
         mock.patch("sigmond.bringup.build_plan", fake_build_plan), \
         mock.patch("sigmond.site_profile.load_site_profile", lambda: None), \
         mock.patch.object(smd, "_need_root", lambda *_: False), \
         mock.patch.object(smd, "_detect_local_sdr", lambda: True), \
         mock.patch.object(smd, "_bringup_hardware_gate", lambda *a, **k: True), \
         mock.patch.object(smd, "_bringup_env_blockers", lambda *_: []), \
         mock.patch.object(smd, "_bringup_finish", lambda *a, **k: 0), \
         mock.patch.object(smd, "_bringup_probe",
                           probe or (lambda *a: (True, "ok", False))), \
         mock.patch("builtins.input", side_effect=AssertionError("prompted")), \
         contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = smd.cmd_bringup(args)
    return rc, built, out.getvalue()


@pytest.mark.parametrize("dry_run", [True, False])
def test_bringup_radiod_only_refuses_enabled_clients(
        smd, tmp_path, tmp_role, prof_radiod_only, dry_run):
    topo = _topology(tmp_path, **{"wspr-recorder": True, "ka9q-radio": True,
                                  "psk-recorder": False})
    rc, built, out = _bringup(smd, prof_radiod_only, topo, dry_run=dry_run)
    assert rc == 1
    assert built == []
    assert "wspr-recorder" in out and "smd disable wspr-recorder" in out
    assert "psk-recorder" not in out
    assert not tmp_role.exists()


def test_bringup_radiod_only_is_local_and_never_asks_for_a_remote(
        smd, tmp_path, tmp_role, prof_radiod_only):
    topo = _topology(tmp_path, **{"ka9q-radio": True, "ka9q-web": True})
    rc, built, _ = _bringup(smd, prof_radiod_only, topo, dry_run=True)
    assert rc == 0
    assert built[0]["local_radiod"] is True


def test_bringup_radiod_only_writes_role_before_the_plan_runs(
        smd, tmp_path, tmp_role, prof_radiod_only):
    topo = _topology(tmp_path, **{"ka9q-radio": True})
    seen = []
    plan = Plan(profile="radiod-only", local_radiod=True,
                steps=[Step(STAGE4, "checkpoint: x", "checkpoint", check="x")])

    def probe(check, _smd):
        seen.append(tmp_role.read_text().strip() if tmp_role.exists() else None)
        return True, "ok", False

    rc, _, _ = _bringup(smd, prof_radiod_only, topo, dry_run=False,
                        plan=plan, probe=probe)
    assert rc == 0
    assert seen == ["radiod-only"]


def test_bringup_radiod_only_dry_run_writes_no_role(
        smd, tmp_path, tmp_role, prof_radiod_only):
    topo = _topology(tmp_path, **{"ka9q-radio": True})
    rc, _, _ = _bringup(smd, prof_radiod_only, topo, dry_run=True)
    assert rc == 0
    assert not tmp_role.exists()


def test_bringup_with_clients_writes_no_role(smd, tmp_path, tmp_role):
    prof = Profile(name="t", clients=("codar-sounder",),
                   local_radiod_infra=("igmp-querier",), optional=())
    topo = _topology(tmp_path, **{"codar-sounder": True})
    rc, built, _ = _bringup(smd, prof, topo, dry_run=False)
    assert rc == 0 and built
    assert not tmp_role.exists()


# --------------------------------------------------------------------------
# _suggest_iface prefers the default-route interface
# --------------------------------------------------------------------------

_DEV = ("Inter-|   Receive\n face |bytes\n"
        "    lo: 0 0\n"
        "wlp2s0: 0 0\n"
        "enp1s0: 0 0\n")
_ROUTE_HDR = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"


def _iface(tmp_path, monkeypatch, route):
    dev = tmp_path / "dev"
    dev.write_text(_DEV)
    monkeypatch.setattr(radiod_config, "PROC_NET_DEV", str(dev))
    rt = tmp_path / "route"
    if route is not None:
        rt.write_text(_ROUTE_HDR + route)
    monkeypatch.setattr(radiod_config, "PROC_NET_ROUTE", str(rt))
    return radiod_config._suggest_iface()


def test_suggest_iface_prefers_default_route(tmp_path, monkeypatch):
    route = ("wlp2s0\t0002A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\n"
             "enp1s0\t00000000\t0102A8C0\t0003\t0\t0\t100\t00000000\n")
    assert _iface(tmp_path, monkeypatch, route) == "enp1s0"


def test_suggest_iface_falls_back_without_default_route(tmp_path, monkeypatch):
    route = "enp1s0\t0002A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\n"
    assert _iface(tmp_path, monkeypatch, route) == "wlp2s0"


def test_suggest_iface_falls_back_without_route_file(tmp_path, monkeypatch):
    assert _iface(tmp_path, monkeypatch, None) == "wlp2s0"

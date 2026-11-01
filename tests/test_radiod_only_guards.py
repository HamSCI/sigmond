"""A radiod-only host refuses clients, uploads and the loopback multicast route;
a decoder host with a remote radiod drops that route."""
import importlib.util
import os
from argparse import Namespace
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    spec = importlib.util.spec_from_loader("smd_mod_guards", loader=None)
    smd = importlib.util.module_from_spec(spec)
    smd.__dict__["__file__"] = "bin/smd"
    src = REPO / "bin" / "smd"
    with mock.patch.dict(os.environ, {"SIGMOND_NO_VENV_REEXEC": "1"}):
        exec(compile(src.read_text(), str(src), "exec"), smd.__dict__)
    return smd


@pytest.fixture
def smd(tmp_path, monkeypatch):
    mod = _load_smd()
    monkeypatch.setattr(mod, "TOPOLOGY_PATH", tmp_path / "topology.toml")
    monkeypatch.setattr(mod, "_need_root", lambda *_a, **_k: False)
    # cmd_enable also enables the component's units for boot (systemctl).
    monkeypatch.setattr(mod, "_enable_units_for_boot", lambda *_a, **_k: None)
    return mod


@pytest.fixture
def radiod_only(tmp_path, monkeypatch):
    p = tmp_path / "station-role"
    p.write_text("radiod-only\n")
    monkeypatch.setattr("sigmond.station_role.ROLE_PATH", p)


@pytest.fixture
def full_role(tmp_path, monkeypatch):
    monkeypatch.setattr("sigmond.station_role.ROLE_PATH", tmp_path / "absent")


class _R:
    returncode = 0
    stdout = stderr = ""


@pytest.fixture
def runs(smd, monkeypatch):
    calls = []

    def fake_run(argv, sudo=False, **kw):
        calls.append(list(argv))
        return _R()

    monkeypatch.setattr(smd, "_run", fake_run)
    return type("Runs", (), {"calls": calls})


def _enabled(smd, name):
    p = smd.TOPOLOGY_PATH
    if not p.exists():
        return False
    import tomllib
    comp = tomllib.loads(p.read_text()).get("component", {})
    return bool(comp.get(name, {}).get("enabled"))


def test_enable_refuses_a_client_on_radiod_only(smd, radiod_only):
    assert smd.cmd_enable(Namespace(names=["wspr-recorder"])) == 1
    assert not _enabled(smd, "wspr-recorder")


def test_enable_allows_radiod_infra_on_radiod_only(smd, radiod_only):
    assert smd.cmd_enable(Namespace(names=["igmp-querier"])) == 0
    assert _enabled(smd, "igmp-querier")


def test_enable_refuses_by_canonical_name_when_given_alias(smd, radiod_only):
    # `radiod` -> ka9q-radio is allowed; the check runs after resolution.
    assert smd.cmd_enable(Namespace(names=["radiod"])) == 0
    assert _enabled(smd, "ka9q-radio")


def test_enable_mixed_names_fail_overall_but_allowed_still_enable(smd, radiod_only):
    assert smd.cmd_enable(Namespace(names=["wspr-recorder", "igmp-querier"])) == 1
    assert _enabled(smd, "igmp-querier")
    assert not _enabled(smd, "wspr-recorder")


def test_enable_unrestricted_on_full_host(smd, full_role):
    assert smd.cmd_enable(Namespace(names=["wspr-recorder"])) == 0
    assert _enabled(smd, "wspr-recorder")


def test_enable_after_install_refuses_on_radiod_only(smd, radiod_only):
    entry = mock.Mock(description="d")
    entry.name = "wspr-recorder"
    smd._enable_after_install(entry, Namespace(no_enable=False))
    assert not _enabled(smd, "wspr-recorder")


def test_apply_skips_uploader_manifest_on_radiod_only(smd, radiod_only):
    with mock.patch.object(smd, "_apply_uploader_manifest") as m:
        assert smd._apply_uploads_step(dry_run=False) is True
    m.assert_not_called()


def test_apply_runs_uploader_manifest_on_full_host(smd, full_role):
    with mock.patch.object(smd, "_apply_uploader_manifest", return_value=False) as m:
        assert smd._apply_uploads_step(dry_run=False) is False
    m.assert_called_once()
    with mock.patch.object(smd, "_apply_uploader_manifest") as m:
        assert smd._apply_uploads_step(dry_run=True) is True
    m.assert_not_called()


def _service_dir(tmp_path):
    (tmp_path / "systemd").mkdir()
    (tmp_path / "systemd" / "sigmond-loopback-multicast.service").write_text("")
    return tmp_path


def test_loopback_route_never_installed_on_radiod_only(smd, radiod_only, runs, tmp_path):
    assert smd._install_loopback_multicast_route(_service_dir(tmp_path), local_radiod=True)
    assert runs.calls == []


def _coord_with(smd, monkeypatch, tmp_path, text):
    coord = tmp_path / "coordination.toml"
    coord.write_text(text)
    real = smd.Path

    class P(type(real())):
        def __new__(cls, *a, **k):
            if a and str(a[0]) == "/etc/sigmond/coordination.toml":
                a = (str(coord),) + a[1:]
            return super().__new__(cls, *a, **k)

    monkeypatch.setattr(smd, "Path", P)


def test_remote_radiod_in_coordination_removes_existing_route(
        smd, full_role, runs, tmp_path, monkeypatch):
    _coord_with(smd, monkeypatch, tmp_path,
                '[radiod.r1]\nhost = "radiod-box.example"\n')
    assert smd._install_loopback_multicast_route(_service_dir(tmp_path), local_radiod=True)
    assert ["systemctl", "disable", "--now", "sigmond-loopback-multicast.service"] in runs.calls
    assert ["ip", "route", "del", "239.0.0.0/8", "dev", "lo"] in runs.calls
    assert not any("enable" in c for c in runs.calls)


def test_remote_radiod_removal_is_nonfatal(smd, full_role, tmp_path, monkeypatch):
    _coord_with(smd, monkeypatch, tmp_path,
                '[radiod.r1]\nhost = "radiod-box.example"\n')

    calls = []

    class Bad(_R):
        returncode = 1
        stderr = "RTNETLINK answers: No such process"

    def fake_run(argv, sudo=False, **kw):
        calls.append(list(argv))
        return Bad()

    monkeypatch.setattr(smd, "_run", fake_run)
    assert smd._install_loopback_multicast_route(_service_dir(tmp_path), local_radiod=True) is True
    assert calls == [
        ["systemctl", "disable", "--now", "sigmond-loopback-multicast.service"],
        ["ip", "route", "del", "239.0.0.0/8", "dev", "lo"],
    ]


def test_remote_radiod_removal_oserror_never_installs_route(
        smd, full_role, tmp_path, monkeypatch):
    _coord_with(smd, monkeypatch, tmp_path,
                '[radiod.r1]\nhost = "radiod-box.example"\n')
    calls = []

    def fake_run(argv, sudo=False, **kw):
        calls.append(list(argv))
        if len(calls) == 1:
            raise FileNotFoundError("systemctl")
        return _R()

    monkeypatch.setattr(smd, "_run", fake_run)
    assert smd._install_loopback_multicast_route(_service_dir(tmp_path), local_radiod=True) is True
    assert not any(c[:2] == ["ln", "-sf"] or "enable" in c for c in calls)
    assert ["ip", "route", "del", "239.0.0.0/8", "dev", "lo"] in calls


def test_profile_install_on_radiod_only_drops_refused_components(
        smd, radiod_only, monkeypatch):
    seen = {}
    monkeypatch.setattr(smd, "_install_full_suite",
                        lambda a: seen.setdefault("components", a.components) and 0)
    args = Namespace(profile="dasi2", dry_run=False)
    smd._install_profile(args)
    comps = seen["components"].split(",")
    assert "ka9q-radio" in comps
    assert "wspr-recorder" not in comps
    assert set(comps) <= {"ka9q-radio", "igmp-querier", "ka9q-web"}
    assert not _enabled(smd, "wspr-recorder")

"""`smd apply` drops the 239/8->lo route once coordination declares a radiod
on another host, and never installs it (AI6VN-PM VM 100, 2026-11-01: the
route was removed only by a bare `smd install`)."""
import importlib.util
from argparse import Namespace
from pathlib import Path

import pytest

# The guards tests' smd loader and fixtures, by path (tests/ is not a package).
_spec = importlib.util.spec_from_file_location(
    "radiod_only_guards_fixtures", Path(__file__).with_name("test_radiod_only_guards.py"))
_g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_g)
smd, full_role, runs, _coord_with = _g.smd, _g.full_role, _g.runs, _g._coord_with

DISABLE = ["systemctl", "disable", "--now", "sigmond-loopback-multicast.service"]
ROUTE_DEL = ["ip", "route", "del", "239.0.0.0/8", "dev", "lo"]


class _Stop(Exception):
    pass


def _apply_until_the_route_step(smd, monkeypatch, dry_run=False):
    """Run cmd_apply for real up to and including the route step, then stop:
    the step after it (the Proxmox onboot check) raises."""
    monkeypatch.setattr(smd, "_load_topology", lambda *_a, **_k: {})
    monkeypatch.setattr(smd, "_netdiag_auto_refresh", lambda *_a, **_k: None)
    monkeypatch.setattr(smd, "_ensure_multicast_rcvbuf", lambda **_k: False)
    monkeypatch.setattr(smd, "_ensure_usbfs_memory", lambda **_k: False)

    def stop(**_k):
        raise _Stop

    monkeypatch.setattr(smd, "_ensure_proxmox_onboot", stop)
    with pytest.raises(_Stop):
        smd.cmd_apply(Namespace(topology=None, dry_run=dry_run))


def test_apply_removes_the_route_for_a_remote_radiod(smd, full_role, runs, tmp_path, monkeypatch):
    _coord_with(smd, monkeypatch, tmp_path,
                '[radiod."se-status.local"]\nhost = "AI6VN-R1"\n')
    _apply_until_the_route_step(smd, monkeypatch)
    assert DISABLE in runs.calls
    assert ROUTE_DEL in runs.calls
    assert not any("enable" in c or "replace" in c for c in runs.calls)


def test_apply_on_a_host_with_only_local_radiods_does_neither(
        smd, full_role, runs, tmp_path, monkeypatch):
    _coord_with(smd, monkeypatch, tmp_path,
                '[radiod.a]\nhost = "localhost"\n\n[radiod.b]\n')
    _apply_until_the_route_step(smd, monkeypatch)
    assert runs.calls == []


def test_apply_without_coordination_does_neither(smd, full_role, runs, tmp_path, monkeypatch):
    _coord_with(smd, monkeypatch, tmp_path, "")
    (tmp_path / "coordination.toml").unlink()
    _apply_until_the_route_step(smd, monkeypatch)
    assert runs.calls == []


def test_apply_dry_run_touches_nothing(smd, full_role, runs, tmp_path, monkeypatch):
    _coord_with(smd, monkeypatch, tmp_path, '[radiod.r1]\nhost = "radiod-box"\n')
    _apply_until_the_route_step(smd, monkeypatch, dry_run=True)
    assert runs.calls == []

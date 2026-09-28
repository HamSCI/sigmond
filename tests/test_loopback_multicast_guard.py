"""The 239/8→lo route belongs only on a host with its own radiod.

On a decode-only client host the route sends every IGMP join for a LAN
radiod's groups out ``lo``, so the host hears nothing.  AC0G-B2 (2026-09-28)
got the route because its coordination.toml named no radiod at all, and the
old guard skipped the route only when a *remote* radiod was declared.  Clients
find LAN radiods by mDNS; coordination need not name them.
"""
import importlib.util
import os
from pathlib import Path

import pytest


def _smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = "bin/smd"
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


@pytest.fixture
def smd():
    return _smd()


def _record_runs(smd, monkeypatch):
    calls = []

    class _R:
        returncode = 0
        stdout = stderr = ''

    def fake_run(argv, sudo=False, **kw):
        calls.append(list(argv))
        return _R()

    monkeypatch.setattr(smd, '_run', fake_run)
    return calls


def test_no_local_radiod_installs_no_route(smd, monkeypatch, tmp_path):
    calls = _record_runs(smd, monkeypatch)
    (tmp_path / 'systemd').mkdir()
    (tmp_path / 'systemd' / 'sigmond-loopback-multicast.service').write_text('')
    assert smd._install_loopback_multicast_route(tmp_path, local_radiod=False)
    assert not any('enable' in c for c in calls), calls


def test_local_radiod_installs_route(smd, monkeypatch, tmp_path):
    calls = _record_runs(smd, monkeypatch)
    monkeypatch.setattr(smd, 'Path', _path_without_coordination(smd.Path))
    (tmp_path / 'systemd').mkdir()
    (tmp_path / 'systemd' / 'sigmond-loopback-multicast.service').write_text('')
    assert smd._install_loopback_multicast_route(tmp_path, local_radiod=True)
    assert any('enable' in c for c in calls), calls


def _path_without_coordination(real_path):
    """Path() whose /etc/sigmond/coordination.toml reads as absent."""
    class P(type(real_path())):
        def exists(self):
            if str(self) == '/etc/sigmond/coordination.toml':
                return False
            return super().exists()
    return P


def test_host_has_local_radiod_when_radiod_is_being_installed(smd):
    # A greenfield local-radiod station runs this before radiod is built or
    # configured; it must still get the route.
    assert smd._host_has_local_radiod(radiod_needed=True)


def test_host_without_radiod_binary_or_config_has_none(smd, monkeypatch):
    real = smd.Path

    class P(type(real())):
        def exists(self):
            if str(self) == '/usr/local/sbin/radiod':
                return False
            return super().exists()

        def glob(self, pattern):
            if str(self) == '/etc/radio':
                return iter([real('/etc/radio/radiod@bootstrap-fw-1.conf')])
            return super().glob(pattern)

    monkeypatch.setattr(smd, 'Path', P)
    # Only a transient firmware-bootstrap config: not a local radiod.
    assert not smd._host_has_local_radiod(radiod_needed=False)

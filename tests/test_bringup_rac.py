"""A complete bare-metal bring-up activates the RAC; nothing it does moves the verdict."""
import contextlib
import importlib.machinery
import importlib.util
import io
import subprocess
from pathlib import Path
from unittest import mock

from sigmond.bringup import build_plan
from sigmond.catalog import Profile

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    loader = importlib.machinery.SourceFileLoader('smd_rac', str(REPO / 'bin' / 'smd'))
    spec = importlib.util.spec_from_loader('smd_rac', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _plan(**kw):
    prof = Profile(name='dasi2', clients=('hf-timestd',),
                   local_radiod_infra=(), optional=())
    return build_plan(prof, local_radiod=True, **kw)


import pytest


@pytest.fixture(autouse=True)
def _rac_on(monkeypatch):
    monkeypatch.delenv('SIGMOND_NO_RAC', raising=False)


def _finish(smd, tmp_path, run_result=None, run_exc=None, appliance=False,
            failed=(), plan=None):
    ver = tmp_path / 'version'
    key = tmp_path / 'rac-switch.key'
    if appliance:
        ver.write_text('1')
    out = io.StringIO()
    run = mock.Mock(side_effect=run_exc) if run_exc else mock.Mock(
        return_value=run_result or mock.Mock(returncode=0))
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
            mock.patch.object(smd, '_RAC_APPLIANCE_VERSION', ver), \
            mock.patch.object(smd, '_RAC_SWITCH_KEY', key), \
            mock.patch.object(smd.subprocess, 'run', run), \
            mock.patch.object(smd, '_settle_firstrun_marker'):
        rc = smd._bringup_finish(plan or _plan(), 'dasi2', list(failed), smd='/x/smd')
    return rc, run, out.getvalue()


def test_finish_runs_activate_on_bare_metal(tmp_path):
    smd = _load_smd()
    rc, run, _ = _finish(smd, tmp_path)
    assert rc == 0
    cmd = run.call_args.args[0]
    assert cmd == [str(REPO / 'scripts/rac/sigmond-rac'), 'activate']
    assert run.call_args.kwargs['timeout'] == 900
    assert run.call_args.kwargs['stdin'] == subprocess.DEVNULL


def test_finish_skips_on_appliance_vm(tmp_path):
    smd = _load_smd()
    rc, run, out = _finish(smd, tmp_path, appliance=True)
    assert rc == 0
    run.assert_not_called()
    assert 'remote access' not in out


def test_finish_rc_unchanged_when_activate_fails(tmp_path):
    smd = _load_smd()
    rc, run, out = _finish(smd, tmp_path, run_result=mock.Mock(returncode=4))
    assert rc == 0
    assert 'remote access not up yet: no site name' in out
    assert 'smd remote status' in out


def test_finish_rc_unchanged_on_timeout(tmp_path):
    smd = _load_smd()
    rc, run, out = _finish(smd, tmp_path,
                           run_exc=subprocess.TimeoutExpired('x', 900))
    assert rc == 0
    assert 'remote access not up yet' in out


def test_finish_rc_unchanged_on_oserror(tmp_path):
    smd = _load_smd()
    rc, _, out = _finish(smd, tmp_path, run_exc=OSError('gone'))
    assert rc == 0
    assert 'remote access not up yet' in out


def test_unknown_exit_code_is_named(tmp_path):
    smd = _load_smd()
    _, _, out = _finish(smd, tmp_path, run_result=mock.Mock(returncode=9))
    assert 'exit 9' in out


def test_partial_bringup_does_not_activate(tmp_path):
    smd = _load_smd()
    rc, run, _ = _finish(smd, tmp_path, plan=_plan(sdr_absent=True))
    assert rc == 3
    run.assert_not_called()


def test_incomplete_bringup_does_not_activate(tmp_path):
    smd = _load_smd()
    rc, run, _ = _finish(smd, tmp_path, failed=['x (exit 1)'])
    assert rc == 1
    run.assert_not_called()


def test_env_switch_skips_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv('SIGMOND_NO_RAC', '1')
    smd = _load_smd()
    rc, run, out = _finish(smd, tmp_path)
    assert rc == 0
    run.assert_not_called()
    assert 'remote access' not in out


def test_exit_5_names_the_proxmox_host_without_retry_clause(tmp_path):
    smd = _load_smd()
    _, _, out = _finish(smd, tmp_path, run_result=mock.Mock(returncode=5))
    assert 'carried by the Proxmox host' in out
    assert 'retry timer' not in out


def test_announces_the_wait(tmp_path):
    smd = _load_smd()
    _, _, out = _finish(smd, tmp_path)
    assert 'bringing up remote access (up to 15 min)' in out

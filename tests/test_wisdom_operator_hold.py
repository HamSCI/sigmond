"""FFT wisdom on a station whose radiod someone else looks after (WWV-2,
2026-11-16).

WWV-2 lists ka9q-radio in site-profile `[components] locally_managed`
(Phil runs its radiod).  An install still re-enabled and started
sigmond-wisdom.service after the operator had disabled it, and
`systemctl mask` failed ("File already exists") because the unit is a real
file in /etc/systemd/system.  So:

* ka9q-radio locally managed: sigmond keeps the existing wisdom and never
  starts planning on its own -- not at boot (the unit's ExecCondition, `smd
  admin wisdom needed`), not from `plan --auto`, not from bring-up.  An
  explicit `smd admin wisdom plan` still plans.
* a disabled (or masked) wisdom unit stays as the operator left it.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import types
from pathlib import Path

import pytest

from sigmond.site_profile import LocallyManaged, NOT_LOCALLY_MANAGED

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_wisdom_hold", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_wisdom_hold", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

PHIL = LocallyManaged(frozenset({'ka9q-radio'}), 'Phil')


@pytest.fixture
def systemctl(monkeypatch, tmp_path):
    """A fake _run: `systemctl is-enabled` answers from `state`, every call
    is recorded, and the unit's destination is a temp path."""
    calls, state = [], {'is-enabled': 'enabled'}
    dest = tmp_path / 'sigmond-wisdom.service'

    def run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        if cmd[:2] == ['systemctl', 'is-enabled']:
            return subprocess.CompletedProcess(cmd, 0, state['is-enabled'] + '\n', '')
        return subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(smd, '_run', run)
    monkeypatch.setattr(smd, '_WISDOM_UNIT_DEST', dest)
    for name in ('_ok', '_info', '_warn', '_err'):
        monkeypatch.setattr(smd, name, lambda *a, **k: None)
    return types.SimpleNamespace(calls=calls, state=state, dest=dest)


def _enabled(calls):
    return [c for c in calls if c[:2] == ['systemctl', 'enable']]


def _installed(calls):
    return [c for c in calls if c and c[0] == 'install']


# ── the unit's install ───────────────────────────────────────────────────

def test_fresh_host_installs_and_enables_it(systemctl, monkeypatch):
    monkeypatch.setattr(smd, '_locally_managed', lambda: NOT_LOCALLY_MANAGED)
    smd._install_wisdom_service()
    assert _installed(systemctl.calls)
    assert _enabled(systemctl.calls)


def test_a_disabled_unit_is_not_re_enabled(systemctl, monkeypatch):
    monkeypatch.setattr(smd, '_locally_managed', lambda: NOT_LOCALLY_MANAGED)
    systemctl.dest.write_text('[Unit]\n')
    systemctl.state['is-enabled'] = 'disabled'
    smd._install_wisdom_service()
    assert _enabled(systemctl.calls) == []


def test_a_masked_unit_is_neither_overwritten_nor_enabled(systemctl, monkeypatch):
    # `install` onto the mask's /dev/null link would unmask it.
    monkeypatch.setattr(smd, '_locally_managed', lambda: NOT_LOCALLY_MANAGED)
    systemctl.dest.symlink_to('/dev/null')
    systemctl.state['is-enabled'] = 'masked'
    smd._install_wisdom_service()
    assert _installed(systemctl.calls) == []
    assert _enabled(systemctl.calls) == []


def test_locally_managed_radiod_never_enables_it(systemctl, monkeypatch):
    monkeypatch.setattr(smd, '_locally_managed', lambda: PHIL)
    said = []
    monkeypatch.setattr(smd, '_info', lambda m, *a, **k: said.append(m))
    smd._install_wisdom_service()
    assert _enabled(systemctl.calls) == []
    assert any('locally managed' in m for m in said)


# ── nothing plans by itself ──────────────────────────────────────────────

def test_needed_says_no_when_radiod_is_locally_managed(monkeypatch):
    monkeypatch.setattr(smd, '_locally_managed', lambda: PHIL)
    monkeypatch.setattr(smd, '_wisdom_current',
                        lambda *a: pytest.fail('must not judge the wisdom'))
    monkeypatch.setattr(smd, '_info', lambda *a, **k: None)
    assert smd.cmd_wisdom_needed(types.SimpleNamespace()) == 1


def test_auto_plan_does_nothing_when_radiod_is_locally_managed(monkeypatch):
    monkeypatch.setattr(smd, '_locally_managed', lambda: PHIL)
    monkeypatch.setattr(smd, '_wisdom_install_cpu_bundle',
                        lambda: pytest.fail('must not replace the wisdom'))
    monkeypatch.setattr(smd, '_wisdom_verdict', lambda *a: 'fresh')
    monkeypatch.setattr(smd.shutil, 'which', lambda n: '/usr/bin/fftwf-wisdom')
    monkeypatch.setattr(smd.subprocess, 'run',
                        lambda *a, **k: pytest.fail('must not plan'))
    monkeypatch.setattr(smd, '_info', lambda *a, **k: None)
    args = types.SimpleNamespace(auto=True, fresh=False, no_install=False)
    assert smd.cmd_wisdom_plan(args) == 0


def test_bringup_does_not_wait_on_or_seed_locally_managed_wisdom(monkeypatch):
    monkeypatch.setattr(smd, '_locally_managed', lambda: PHIL)
    monkeypatch.setattr(smd, '_wisdom_verdict', lambda *a: 'fresh')
    monkeypatch.setattr(smd, '_seed_bundled_wisdom',
                        lambda: pytest.fail('must not replace the wisdom'))
    monkeypatch.setattr(smd, '_run', lambda *a, **k: pytest.fail('must not start the planner'))
    monkeypatch.setattr(smd, '_info', lambda *a, **k: None)
    assert smd._bringup_wait_wisdom(Path('/nonexistent/wisdomf')) is True

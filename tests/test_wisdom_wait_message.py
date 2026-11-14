"""_bringup_wait_wisdom tells the operator the truth about the wait.

A 'top-up' (an image station whose fft.log names a few small sizes its
wisdom lacks) measures only those: minutes, and radiod keeps running.  Only
a 'fresh' verdict means planning from scratch, which takes hours.  The
no-bundle warning used to print "Planning from scratch takes HOURS ... the
station is down" for both (wisdom re-review, 2026-11-14)."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from sigmond import wisdom

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_wisdom_wait", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_wisdom_wait", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


@pytest.fixture
def no_bundle(monkeypatch):
    import time as _time
    monkeypatch.setattr(wisdom, 'identify_cpu', lambda *a, **k: {
        'model': 'AMD Ryzen 5 5560U with Radeon Graphics', 'slug': 'amd-ryzen-5-5560u',
        'bundle': None, 'have_wisdom': False, 'known_bundles': []})
    monkeypatch.setattr(smd, '_wisdom_topup_list',
                        lambda: ['rof1620', 'cob800', 'rob3240'])
    monkeypatch.setattr(_time, 'sleep', lambda s: None)
    monkeypatch.setattr(smd, '_run', lambda cmd, **k: subprocess.CompletedProcess(
        cmd, 0, 'active\n', ''))


def _wait(monkeypatch, capsys, verdict):
    monkeypatch.setattr(smd, '_wisdom_verdict', lambda wf=None: verdict)
    smd._bringup_wait_wisdom(Path('/nonexistent/wisdomf'), timeout_s=1, poll_s=1)
    cap = capsys.readouterr()
    return cap.out + cap.err


def test_top_up_without_a_bundle_says_minutes_not_hours(no_bundle, monkeypatch, capsys):
    out = _wait(monkeypatch, capsys, 'top-up')
    assert 'HOURS' not in out
    assert 'station is down' not in out
    assert '3 missing small transforms' in out
    assert 'minutes' in out
    assert 'resumes' in out


def test_fresh_without_a_bundle_still_warns_of_hours(no_bundle, monkeypatch, capsys):
    out = _wait(monkeypatch, capsys, 'fresh')
    assert 'NO bundled wisdom' in out
    assert 'HOURS' in out
    assert 'station is down' in out


def test_current_returns_at_once(no_bundle, monkeypatch, capsys):
    monkeypatch.setattr(smd, '_wisdom_verdict', lambda wf=None: 'current')
    assert smd._bringup_wait_wisdom(Path('/x'), timeout_s=1, poll_s=1) is True

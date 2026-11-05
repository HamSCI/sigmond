"""Bring-up's capacity gate (Phase 3, task 6)."""
from __future__ import annotations

import pytest

from sigmond.capacity import INSTANCE_COST_CORES, capacity_verdict

from .test_bringup_receivers import (A, B, TWO_REMOTE, _found, _no_coordination,
                                     _profile_with)
from .test_sources_bind import _bringup_args, _bringup_env, _load_smd

PAIRS = [('wspr-recorder', 'AC0G/B1A'), ('psk-recorder', 'AC0G/B1A'),
         ('wspr-recorder', 'AC0G/B1B'), ('psk-recorder', 'AC0G/B1B')]


def test_enough_cores_is_ok():
    v = capacity_verdict(PAIRS, online_cpus=16, radiod_cpus=2)
    assert v.ok
    assert v.available == 13
    assert v.needed == pytest.approx(2 * (0.21 + 0.18))
    assert v.estimated == ()
    assert 'capacity: 4 instance(s) need 0.78 cores of 13 available' in v.text()
    assert '(16 CPUs, 2 for radiod, 1 for the system)' in v.text()
    assert '\n' not in v.text()


def test_too_few_cores_refuses_with_the_numbers():
    v = capacity_verdict(PAIRS, online_cpus=3, radiod_cpus=2)
    assert not v.ok
    assert v.available == 0
    t = v.text()
    assert 'need 0.78 cores of 0 available' in t
    assert 'short by 0.78' in t
    assert 'clients = [...]' in t and 'site-profile.toml' in t


def test_an_unknown_client_is_estimated():
    v = capacity_verdict([('meteor-scatter', 'AC0G/B1A')], online_cpus=16,
                         radiod_cpus=0)
    assert v.needed == 0.25
    assert v.estimated == ('meteor-scatter',)
    assert 'estimated at 0.25 cores for: meteor-scatter' in v.text()
    assert 'wspr-recorder' in INSTANCE_COST_CORES


def _declared(tmp_path, monkeypatch, body):
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, _found(A, B))
    _profile_with(tmp_path, monkeypatch, body)
    _no_coordination(monkeypatch)
    return smd, calls


def test_one_receiver_never_refuses(tmp_path, monkeypatch, capsys):
    one = f'[[receiver]]\nreporter_id = "AC0G/B1A"\nradiod = "{A}"\n'
    smd, calls = _declared(tmp_path, monkeypatch, one)
    monkeypatch.setattr(smd.os, 'cpu_count', lambda: 1)
    assert smd.cmd_bringup(_bringup_args()) == 0
    assert calls
    assert 'capacity:' not in capsys.readouterr().out


def test_too_small_host_refuses_before_planning(tmp_path, monkeypatch, capsys):
    smd, calls = _declared(tmp_path, monkeypatch, TWO_REMOTE)
    monkeypatch.setattr(smd.os, 'cpu_count', lambda: 1)
    assert smd.cmd_bringup(_bringup_args()) == 1
    assert calls == []
    cap = capsys.readouterr()
    assert 'capacity:' in cap.out + cap.err
    assert 'clients = [...]' in cap.out + cap.err


def test_big_host_prints_the_verdict_and_proceeds(tmp_path, monkeypatch, capsys):
    smd, calls = _declared(tmp_path, monkeypatch, TWO_REMOTE)
    monkeypatch.setattr(smd.os, 'cpu_count', lambda: 16)
    assert smd.cmd_bringup(_bringup_args()) == 0
    assert 'capacity:' in capsys.readouterr().out


def test_ignore_capacity_warns_and_proceeds(tmp_path, monkeypatch, capsys):
    smd, calls = _declared(tmp_path, monkeypatch, TWO_REMOTE)
    monkeypatch.setattr(smd.os, 'cpu_count', lambda: 1)
    assert smd.cmd_bringup(_bringup_args(ignore_capacity=True)) == 0
    assert calls
    cap = capsys.readouterr()
    assert 'capacity:' in cap.out + cap.err

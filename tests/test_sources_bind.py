"""`smd admin sources bind` and the client bring-up that uses it (spec 4.4).

AC0G-B2, 2026-11-04: `smd bringup client --remote-radiod <dns>` put the
radiod's name only into a note step.  Nothing recorded it, so hf-timestd
kept `<YOUR_RADIOD_STATUS>`, the recorders kept a template radiod, and
every client failed at its first start.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import types
from pathlib import Path
from unittest import mock

import pytest

from sigmond.catalog import Profile
from sigmond.bringup import build_plan
import sigmond.coordination as coordmod
from sigmond.coordination import load_coordination


def ensure_radiod_block(*a):
    return coordmod.ensure_radiod_block(*a)


def ensure_client_binding(*a):
    return coordmod.ensure_client_binding(*a)

A = 'ac0g-b1-a-status.local'
B = 'ac0g-b1-b-status.local'
TWO = [(A, 'AC0G/B1A'), (B, 'AC0G/B1B')]
FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def _load_smd():
    spec = importlib.util.spec_from_loader("smd_mod_sources_bind", loader=None)
    smd = importlib.util.module_from_spec(spec)
    smd.__dict__["__file__"] = "bin/smd"
    src = Path(__file__).resolve().parent.parent / "bin" / "smd"
    with mock.patch.dict(os.environ, {"SIGMOND_NO_VENV_REEXEC": "1"}):
        exec(compile(src.read_text(), str(src), "exec"), smd.__dict__)
    return smd


def _client():
    # etc/catalog.toml [profile.client]: decode-only, no local radiod infra.
    return Profile(
        name='client',
        clients=('hf-timestd', 'wspr-recorder', 'psk-recorder', 'station-web'),
        local_radiod_infra=(),
        optional=('mag-recorder',),
    )


def _dasi2():
    return Profile(
        name='dasi2',
        clients=('hf-timestd', 'wspr-recorder', 'psk-recorder', 'mag-recorder'),
        local_radiod_infra=('igmp-querier', 'gpsdo-monitor'),
        optional=('ka9q-web',),
    )


def _binds(plan):
    return [(i, s) for i, s in enumerate(plan.steps)
            if s.argv[1:4] == ['admin', 'sources', 'bind']]


# --- coordination writers -----------------------------------------------------

def test_bind_writes_radiod_block_and_bindings_once(tmp_path):
    coord = tmp_path / 'coordination.toml'
    coord.write_text('[host]\ncall = "AC0G"\n')
    coord.chmod(0o640)

    assert ensure_radiod_block(coord, A, 'ac0g-b1') is True
    assert ensure_radiod_block(coord, B, 'ac0g-b1') is True
    assert ensure_client_binding(coord, 'psk-recorder', 'AC0G=B1A', A) is True
    assert ensure_client_binding(coord, 'psk-recorder', 'AC0G=B1B', B) is True
    assert ensure_client_binding(coord, 'hf-timestd', '', B) is True
    first = coord.read_text()

    # A second pass writes nothing.
    assert ensure_radiod_block(coord, A, 'ac0g-b1') is False
    assert ensure_client_binding(coord, 'psk-recorder', 'AC0G=B1A', A) is False
    assert ensure_client_binding(coord, 'hf-timestd', '', B) is False
    assert coord.read_text() == first
    assert first.count(f'[radiod."{A}"]') == 1

    c = load_coordination(coord)
    assert c.host.call == 'AC0G'                       # earlier text kept
    assert c.radiods[A].host == 'ac0g-b1'
    assert c.radiods[A].effective_status_dns == A
    pairs = {(x.client_type, x.instance): x.radiod_id for x in c.clients}
    assert pairs == {('psk-recorder', 'AC0G=B1A'): A,
                     ('psk-recorder', 'AC0G=B1B'): B,
                     ('hf-timestd', ''): B}
    assert stat.S_IMODE(coord.stat().st_mode) == 0o640  # mode kept

    # Rebinding an instance moves it; it does not add a second entry.
    assert ensure_client_binding(coord, 'psk-recorder', 'AC0G=B1A', B) is True
    c = load_coordination(coord)
    assert [x.radiod_id for x in c.clients
            if x.instance == 'AC0G=B1A'] == [B]


def test_hf_timestd_config_init_learns_its_bound_radiod(tmp_path):
    # `smd config init hf-timestd` passes no instance.  With two radiods
    # declared, only the instance-less binding can name its radiod.
    from sigmond.commands.client_config import _resolve_radiod_status
    coord = tmp_path / 'coordination.toml'
    ensure_radiod_block(coord, A, 'ac0g-b1')
    ensure_radiod_block(coord, B, 'ac0g-b1')
    c = load_coordination(coord)
    assert _resolve_radiod_status(c, 'hf-timestd', None) == ''
    ensure_client_binding(coord, 'hf-timestd', '', B)
    c = load_coordination(coord)
    assert _resolve_radiod_status(c, 'hf-timestd', None) == B
    # The recorders' base config, still unbound, learns nothing.
    assert _resolve_radiod_status(c, 'psk-recorder', None) == ''


# --- the bind verb ------------------------------------------------------------

PSK_SHARED = """[[radiod]]
# REQUIRED: the radiod to consume from.
status = "<YOUR_RADIOD_STATUS>"

[radiod.ft8]
sample_rate = 12000
"""

WSPR_SHARED = """[radiod]
status = "template-status.local"

[recorder]
bands = ["20m"]
"""


def test_bind_renders_each_instance_config_to_its_radiod(tmp_path, monkeypatch):
    smd = _load_smd()
    etc = tmp_path / 'etc'
    (etc / 'psk-recorder').mkdir(parents=True)
    (etc / 'psk-recorder' / 'psk-recorder-config.toml').write_text(PSK_SHARED)
    (etc / 'wspr-recorder').mkdir(parents=True)
    (etc / 'wspr-recorder' / 'config.toml').write_text(WSPR_SHARED)
    coord = etc / 'sigmond' / 'coordination.toml'

    for dns, rid in TWO:
        rc = smd._sources_bind(dns, rid, ['wspr-recorder', 'psk-recorder'],
                               host='ac0g-b1', timing=(dns == B),
                               etc_root=etc, coord_path=coord,
                               inventory_rows=[])
        assert rc == 0

    psk = (etc / 'psk-recorder' / 'AC0G=B1B.toml').read_text()
    import tomllib
    assert tomllib.loads(psk)['radiod'][0]['status'] == B
    assert tomllib.loads((etc / 'psk-recorder' / 'AC0G=B1A.toml').read_text()
                         )['radiod'][0]['status'] == A
    wspr = tomllib.loads((etc / 'wspr-recorder' / 'AC0G=B1B.toml').read_text())
    assert [s['status'] for s in wspr['source']] == [B]

    sel = (etc / 'sigmond' / 'clients' / 'psk-recorder@AC0G=B1B.sources.toml')
    assert tomllib.loads(sel.read_text())['selected'] == [f'radiod:{B}']

    c = load_coordination(coord)
    assert set(c.radiods) == {A, B}
    pairs = {(x.client_type, x.instance): x.radiod_id for x in c.clients}
    assert pairs[('wspr-recorder', 'AC0G=B1A')] == A
    assert pairs[('psk-recorder', 'AC0G=B1B')] == B
    assert pairs[('hf-timestd', '')] == B
    # bind ends with coordination.env rendered from the result.
    env = (etc / 'sigmond' / 'coordination.env').read_text()
    assert 'HF_TIMESTD_RADIOD=' + B in env

    # A second bind changes nothing on disk.
    before = {p: p.read_text() for p in etc.rglob('*') if p.is_file()}
    smd._sources_bind(B, 'AC0G/B1B', ['wspr-recorder', 'psk-recorder'],
                      host='ac0g-b1', timing=True, etc_root=etc,
                      coord_path=coord, inventory_rows=[])
    after = {p: p.read_text() for p in etc.rglob('*') if p.is_file()}
    assert after == before


def test_bind_waits_for_a_client_that_is_not_installed(tmp_path):
    # Bring-up binds in Stage 1, before Stage 3a installs the recorders.  A
    # stub instance config written then would survive `instance add --force`
    # and never carry the client's tables, so bind records the selection and
    # leaves the instance to Stage 3a.
    smd = _load_smd()
    etc = tmp_path / 'etc'
    coord = etc / 'sigmond' / 'coordination.toml'
    rc = smd._sources_bind(A, 'AC0G/B1A', ['psk-recorder'], host='ac0g-b1',
                           etc_root=etc, coord_path=coord, inventory_rows=[])
    assert rc == 0
    assert not (etc / 'psk-recorder' / 'AC0G=B1A.toml').exists()
    assert (etc / 'sigmond' / 'clients' /
            'psk-recorder@AC0G=B1A.sources.toml').exists()
    c = load_coordination(coord)
    assert [x.radiod_id for x in c.clients] == [A]


# --- the client plan ----------------------------------------------------------

def test_one_kept_radiod_gets_hf_timestd():
    p = build_plan(_client(), local_radiod=False,
                   remote_radiods=[(A, 'AC0G/B1A')])
    binds = _binds(p)
    assert len(binds) == 1
    assert '--timing' in binds[0][1].argv
    assert 'configure hf-timestd' in [s.label for s in p.steps]


def test_two_kept_radiods_leave_hf_timestd_idle_unless_named():
    p = build_plan(_client(), local_radiod=False, remote_radiods=TWO)
    assert not any('--timing' in s.argv for _, s in _binds(p))
    labels = [s.label for s in p.steps]
    assert not any(l.startswith('configure hf-timestd') for l in labels)
    assert not any(s.kind == 'start' and 'hf-timestd' in s.argv
                   for s in p.steps)
    assert ('hf-timestd installed but idle: name its radiod with '
            '--timing-radiod') in labels
    # Installed, but not enabled, so the catch-all start leaves it alone.
    assert not any(s.kind == 'enable' and s.argv[-1] == 'hf-timestd'
                   for s in p.steps)
    assert any(s.kind == 'install' and 'hf-timestd' in s.argv
               for s in p.steps)

    p = build_plan(_client(), local_radiod=False, remote_radiods=TWO,
                   timing_radiod=B)
    timing = [s.argv[4] for _, s in _binds(p) if '--timing' in s.argv]
    assert timing == [B]
    assert 'configure hf-timestd' in [s.label for s in p.steps]


def test_client_plan_binds_every_radiod_before_any_client_is_configured():
    p = build_plan(_client(), local_radiod=False, remote_radiods=TWO)
    binds = _binds(p)
    assert [s.argv[4] for _, s in binds] == [A, B]
    for _, s in binds:
        assert s.kind == 'config'
        assert s.argv[s.argv.index('--clients') + 1] == 'wspr-recorder,psk-recorder'
    assert binds[0][1].argv[5:7] == ['--reporter', 'AC0G/B1A']
    tune = next(i for i, s in enumerate(p.steps) if s.argv == ['smd', 'apply'])
    first_config = next(i for i, s in enumerate(p.steps)
                        if s.argv[1:3] == ['config', 'init'])
    assert tune < binds[0][0]
    assert binds[-1][0] < first_config


def test_client_plan_enables_one_instance_pair_per_radiod():
    p = build_plan(_client(), local_radiod=False, remote_radiods=TWO)
    adds = [s.argv[-2:] for s in p.steps
            if s.argv[1:4] == ['admin', 'instance', 'add']]
    enables = [s.argv[-2:] for s in p.steps
               if s.argv[1:4] == ['admin', 'instance', 'enable']]
    want = [[c, r] for c in ('wspr-recorder', 'psk-recorder')
            for r in ('AC0G/B1A', 'AC0G/B1B')]
    assert sorted(adds) == sorted(want)
    assert sorted(enables) == sorted(want)
    # The instances made in Stage 3a get their sources rendered before any
    # of them starts.
    apply_i = next(i for i, s in enumerate(p.steps)
                   if s.argv[1:4] == ['admin', 'sources', 'apply'])
    last_add = max(i for i, s in enumerate(p.steps)
                   if s.argv[1:4] == ['admin', 'instance', 'add'])
    first_enable = min(i for i, s in enumerate(p.steps)
                       if s.argv[1:4] == ['admin', 'instance', 'enable'])
    assert last_add < apply_i < first_enable


def _bringup_args(**kw):
    base = dict(profile='client', profile_flag=None, remote_radiod=None,
                timing_radiod=None, dry_run=True, non_interactive=True,
                reporter=None, grid='EM38ww', callsign='AC0G',
                psws_station_id=None, with_optional=False,
                require_hardware=False, reconfigure=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _bringup_env(monkeypatch, smd, found):
    import sigmond.sources as sources
    import sigmond.site_profile as site_profile
    import sigmond.bringup as bringup
    monkeypatch.setattr(sources, 'discover_radiods', lambda timeout=5.0: found)
    monkeypatch.setattr(site_profile, 'load_site_profile', lambda *a, **k: None)
    monkeypatch.setattr(smd, '_bringup_hardware_gate', lambda *a, **k: True)
    monkeypatch.setattr(smd, '_bringup_env_blockers', lambda local: [])
    calls = []
    real = bringup.build_plan

    def spy(*a, **k):
        calls.append(k)
        return real(*a, **k)
    monkeypatch.setattr(bringup, 'build_plan', spy)
    return calls


def test_bringup_stops_before_installing_when_a_named_radiod_is_silent(
        monkeypatch, capsys):
    from sigmond.sources import DiscoveredRadiod
    smd = _load_smd()
    found = [DiscoveredRadiod(A, 'AC0G @EM38ww B1-a longwire', 'ac0g-b1')]
    calls = _bringup_env(monkeypatch, smd, found)
    rc = smd.cmd_bringup(_bringup_args(remote_radiod=[A, B]))
    assert rc == 1
    assert calls == []                              # never planned
    cap = capsys.readouterr()
    assert f'radiod {B} did not answer' in cap.out + cap.err


def test_bringup_keeps_the_stations_own_radiods(monkeypatch, capsys):
    from sigmond.sources import DiscoveredRadiod
    smd = _load_smd()
    found = [DiscoveredRadiod(A, 'AC0G @EM38ww B1-a longwire', 'ac0g-b1'),
             DiscoveredRadiod(B, 'AC0G @EM38ww B1-b loop', 'ac0g-b1'),
             DiscoveredRadiod('sigma-status.local', 'W1XYZ sigma', 'sigma')]
    calls = _bringup_env(monkeypatch, smd, found)
    rc = smd.cmd_bringup(_bringup_args())
    assert rc == 0
    assert calls[0]['remote_radiods'] == TWO
    out = capsys.readouterr().out
    assert 'sigma-status.local' in out              # listed, not kept


def test_bringup_on_a_client_profile_needs_a_callsign(monkeypatch, capsys):
    import sigmond.coordination as coordination
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, [])
    # No callsign in coordination either (the host running the suite may
    # carry one).
    monkeypatch.setattr(coordination, 'load_coordination',
                        lambda *a, **k: coordination.Coordination())
    rc = smd.cmd_bringup(_bringup_args(callsign=None))
    assert rc == 1
    assert calls == []
    cap = capsys.readouterr()
    assert '--callsign' in cap.out + cap.err


def test_a_local_radiod_plan_is_unchanged(monkeypatch):
    # The dasi2 plan as it stood before discovery existed (fixture captured
    # from 697ad90).  A local-radiod profile never discovers and never binds.
    want = json.loads((FIXTURES / 'bringup_dasi2_local_plan.json').read_text())
    p = build_plan(_dasi2(), local_radiod=True, reporter='AC0G/S')
    got = [[s.stage, s.kind, s.label, s.argv] for s in p.steps]
    assert got == want

    import sigmond.sources as sources

    def boom(*a, **k):
        raise AssertionError('a local-radiod bring-up must not discover')
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, [])
    monkeypatch.setattr(sources, 'discover_radiods', boom)
    monkeypatch.setattr(smd, '_detect_local_sdr', lambda: True)
    monkeypatch.setattr(smd, '_detect_magnetometer', lambda: True)
    monkeypatch.setattr(smd, '_detect_gpsdo', lambda: True)
    rc = smd.cmd_bringup(_bringup_args(profile='dasi2', reporter='AC0G/S'))
    assert rc == 0
    assert calls[0]['remote_radiods'] == []

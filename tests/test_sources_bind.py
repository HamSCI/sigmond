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

    psk = (etc / 'psk-recorder' / 'AC0G-B1B.toml').read_text()
    import tomllib
    assert tomllib.loads(psk)['radiod'][0]['status'] == B
    assert tomllib.loads((etc / 'psk-recorder' / 'AC0G-B1A.toml').read_text()
                         )['radiod'][0]['status'] == A
    wspr = tomllib.loads((etc / 'wspr-recorder' / 'AC0G-B1B.toml').read_text())
    assert [s['status'] for s in wspr['source']] == [B]

    sel = (etc / 'sigmond' / 'clients' / 'psk-recorder@AC0G-B1B.sources.toml')
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
    assert not (etc / 'psk-recorder' / 'AC0G-B1A.toml').exists()
    assert (etc / 'sigmond' / 'clients' /
            'psk-recorder@AC0G-B1A.sources.toml').exists()
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
    # On a terminal the callsign rule proposes; the operator confirms.
    monkeypatch.setattr(smd.sys, 'stdin', _Tty())
    monkeypatch.setattr('builtins.input', lambda prompt='': '')
    rc = smd.cmd_bringup(_bringup_args(non_interactive=False))
    assert rc == 0
    assert calls[0]['remote_radiods'] == TWO
    out = capsys.readouterr().out
    assert 'sigma-status.local' in out              # listed, not kept


def test_an_unattended_bringup_binds_only_the_radiods_it_names(monkeypatch, capsys):
    # AC0G-B2, 2026-11-05: the callsign rule also kept firebat's airspyhf,
    # which advertises "AC0G" too.  Michael: an unattended install binds only
    # the radiods the operator names.
    from sigmond.sources import DiscoveredRadiod
    smd = _load_smd()
    found = [DiscoveredRadiod(A, 'AC0G @EM38ww B1-a longwire', 'ac0g-b1'),
             DiscoveredRadiod('airspyhf-status.local', 'AC0G @EM38ww with airspyhf',
                              'firebat')]
    calls = _bringup_env(monkeypatch, smd, found)
    monkeypatch.setattr('builtins.input',
                        lambda prompt='': pytest.fail('asked while unattended'))
    rc = smd.cmd_bringup(_bringup_args())
    assert rc == 1
    assert calls == []                              # nothing planned
    cap = capsys.readouterr()
    text = cap.out + cap.err
    assert 'airspyhf-status.local' in text and A in text   # the list, to choose from
    assert '--remote-radiod' in text
    assert smd.cmd_bringup(_bringup_args(remote_radiod=[A])) == 0
    assert calls[0]['remote_radiods'] == [(A, 'AC0G/B1A')]


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


# --- fix round 1: mDNS data is untrusted --------------------------------------
# A LAN device chooses its own advertised host and status name.  Unescaped,
# a `"` or a newline in either would write TOML keys into coordination.toml
# and, through render_env, variables into the EnvironmentFile of root
# services.

EVIL_HOST = 'evil"\nX=1'
EVIL_DNS = 'ac0g-b1-a\n-status.local'


def test_mdns_hostname_validator():
    from sigmond.sources import valid_mdns_hostname
    assert valid_mdns_hostname(A)
    assert valid_mdns_hostname('ac0g-b1')
    for bad in (EVIL_HOST, EVIL_DNS, '', '-x.local', 'a..b', 'a b',
                'a"b', 'a\\b', 'x' * 64 + '.local', ('a.' * 127) + 'ab'):
        assert not valid_mdns_hostname(bad), repr(bad)


def test_discovery_drops_a_radiod_with_a_crafted_name(monkeypatch, caplog):
    import sigmond.sources as sources
    import sigmond.discovery.mdns as mdns
    from sigmond.sources import InventoryRow, SourceKey

    def row(ident, host):
        # SourceKey refuses whitespace and quotes, so build the crafted
        # identifier the way a parser that trusted the wire might.
        key = object.__new__(SourceKey)
        object.__setattr__(key, 'type', 'radiod')
        object.__setattr__(key, 'identifier', ident)
        return InventoryRow(key=key, label='AC0G x', reachability='ok',
                            observed_at=None, fields={'source_host': host})
    rows = [row(A, 'ac0g-b1'), row(B, EVIL_HOST), row(EVIL_DNS, 'ac0g-b1')]
    monkeypatch.setattr(mdns, 'probe', lambda env, timeout=5.0: [])
    monkeypatch.setattr(sources, 'inventory', lambda obs: rows)
    with caplog.at_level('WARNING'):
        found = sources.discover_radiods()
    assert [r.status_dns for r in found] == [A]
    assert repr(EVIL_HOST) in caplog.text
    assert repr(EVIL_DNS) in caplog.text


def test_coordination_writers_refuse_crafted_values(tmp_path):
    coord = tmp_path / 'coordination.toml'
    with pytest.raises(ValueError):
        ensure_radiod_block(coord, A, EVIL_HOST)
    with pytest.raises(ValueError):
        ensure_radiod_block(coord, EVIL_DNS, 'ac0g-b1')
    with pytest.raises(ValueError):
        ensure_client_binding(coord, 'psk-recorder', 'AC0G=B1A', EVIL_DNS)
    with pytest.raises(ValueError):
        ensure_client_binding(coord, 'psk-recorder', 'AC0G"=B1A', A)
    assert not coord.exists()
    assert ensure_radiod_block(coord, A, 'ac0g-b1') is True


def test_bind_refuses_crafted_values(tmp_path, capsys):
    smd = _load_smd()
    etc = tmp_path / 'etc'
    coord = etc / 'sigmond' / 'coordination.toml'
    for dns, host in ((A, EVIL_HOST), (EVIL_DNS, 'ac0g-b1')):
        rc = smd._sources_bind(dns, 'AC0G/B1A', ['psk-recorder'], host=host,
                               etc_root=etc, coord_path=coord,
                               inventory_rows=[])
        assert rc == 1
    assert not coord.exists()
    assert not (etc / 'sigmond' / 'coordination.env').exists()
    cap = capsys.readouterr()
    assert 'not a valid' in cap.out + cap.err


def test_render_env_skips_a_value_with_a_control_character(capsys):
    from sigmond.coordination import Coordination, Radiod, render_env
    c = Coordination()
    c.radiods = {A: Radiod(id=A, host='ac0g-b1', status_dns=A),
                 'evil': Radiod(id='evil', host=EVIL_HOST, status_dns=B)}
    env = render_env(c, passthrough_lookup=lambda t: [])
    assert 'X=1' not in env.splitlines()
    assert all('\r' not in ln for ln in env.splitlines())
    assert f'RADIOD_{A.upper().replace("-", "_").replace(".", "_")}_STATUS={A}' in env
    assert 'RADIOD_EVIL_STATUS=' + B in env           # the rest of it stays
    assert 'RADIOD_EVIL_HOST' in capsys.readouterr().err


# --- fix round 1: say what will be bound, and ask on a terminal ---------------

class _Tty:
    def isatty(self):
        return True


def _two_found():
    from sigmond.sources import DiscoveredRadiod
    return [DiscoveredRadiod(A, 'AC0G @EM38ww B1-a longwire', 'ac0g-b1'),
            DiscoveredRadiod(B, 'AC0G @EM38ww B1-b loop', 'ac0g-b1')]


def test_bringup_asks_before_binding_on_a_terminal(monkeypatch, capsys):
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, _two_found())
    monkeypatch.setattr(smd.sys, 'stdin', _Tty())
    asked = []

    def answer(prompt=''):
        asked.append(prompt)
        return 'n'
    monkeypatch.setattr('builtins.input', answer)
    rc = smd.cmd_bringup(_bringup_args(non_interactive=False))
    assert rc == 1
    assert calls == []                              # nothing planned
    assert any('Bind these radiods? [Y/n]' in p for p in asked)

    monkeypatch.setattr('builtins.input', lambda prompt='': '')
    rc = smd.cmd_bringup(_bringup_args(non_interactive=False))
    assert rc == 0
    assert calls[0]['remote_radiods'] == TWO


def test_bringup_says_why_each_radiod_was_kept(monkeypatch, capsys):
    smd = _load_smd()
    _bringup_env(monkeypatch, smd, _two_found())
    monkeypatch.setattr(smd.sys, 'stdin', _Tty())
    monkeypatch.setattr('builtins.input', lambda prompt='': '')
    assert smd.cmd_bringup(_bringup_args(non_interactive=False)) == 0
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if 'bind ' + A in l)
    assert 'ac0g-b1' in line and 'B1-a longwire' in line
    assert 'advertises AC0G' in line

    monkeypatch.setattr('builtins.input',
                        lambda prompt='': pytest.fail('asked while unattended'))
    assert smd.cmd_bringup(_bringup_args(remote_radiod=[B])) == 0
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if 'bind ' + B in l)
    assert 'named with --remote-radiod' in line


# --- fix round 1: a hard checkpoint that each instance names its radiod ------

def test_client_plan_checks_every_instance_names_its_radiod():
    p = build_plan(_client(), local_radiod=False, remote_radiods=TWO)
    apply_i = next(i for i, s in enumerate(p.steps)
                   if s.argv[1:4] == ['admin', 'sources', 'apply'])
    chk = p.steps[apply_i + 1]
    assert chk.kind == 'checkpoint' and chk.hard
    name, _, pairs = chk.check.partition(':')
    assert name == 'instances-bound'
    assert sorted(pairs.split(',')) == sorted(
        f'{c}@{r}' for c in ('wspr-recorder', 'psk-recorder')
        for r in ('AC0G/B1A', 'AC0G/B1B'))


def _bound_etc(tmp_path, psk_status, wspr_sources):
    etc = tmp_path / 'etc'
    clients = etc / 'sigmond' / 'clients'
    clients.mkdir(parents=True)
    (etc / 'psk-recorder').mkdir()
    (etc / 'wspr-recorder').mkdir()
    for c in ('psk-recorder', 'wspr-recorder'):
        (clients / f'{c}@AC0G-B1B.sources.toml').write_text(
            f'selected = ["radiod:{B}"]\n')
    (etc / 'psk-recorder' / 'AC0G-B1B.toml').write_text(
        f'[[radiod]]\nstatus = "{psk_status}"\n')
    body = '[radiod]\nstatus = "my-rx888-status.local"\n'
    for st in wspr_sources:
        body += f'\n[[source]]\nkey = "radiod:{st}"\nstatus = "{st}"\n'
    (etc / 'wspr-recorder' / 'AC0G-B1B.toml').write_text(body)
    return etc


def test_instances_bound_probe_passes_and_fails(tmp_path, monkeypatch):
    smd = _load_smd()
    pairs = 'psk-recorder@AC0G/B1B,wspr-recorder@AC0G/B1B'
    etc = _bound_etc(tmp_path, B, [B])
    ok, detail = smd._instances_bound(pairs, etc_root=etc)
    assert ok, detail

    # The fabricated name the clients' config init writes when several
    # radiods answer, left in place because `sources apply` never ran.
    etc = _bound_etc(tmp_path / 'f1', 'rx888-status.local', [B])
    ok, detail = smd._instances_bound(pairs, etc_root=etc)
    assert not ok and 'psk-recorder@AC0G/B1B' in detail
    etc = _bound_etc(tmp_path / 'f2', B, [])
    ok, detail = smd._instances_bound(pairs, etc_root=etc)
    assert not ok and 'wspr-recorder@AC0G/B1B' in detail
    etc = _bound_etc(tmp_path / 'f3', B, [B, A])
    assert not smd._instances_bound(pairs, etc_root=etc)[0]

    # _bringup_probe keeps its 3-tuple contract for the new check.
    monkeypatch.setattr(smd, '_instances_bound',
                        lambda p, etc_root=None: (False, 'x'))
    assert smd._bringup_probe('instances-bound:' + pairs, 'smd') == (
        False, 'x', False)


# --- fix round 2: the advertised label is untrusted too -----------------------
# mdns._decode_avahi_escapes turns `\010` into a newline and `\027` into ESC,
# so a crafted label could forge "bind …" lines, or hide one, in the listing
# the operator reads before "Bind these radiods? [Y/n]".

def _crafted_rows(labels):
    from sigmond.sources import InventoryRow, SourceKey
    return [InventoryRow(key=SourceKey('radiod', dns), label=label,
                         reachability='ok', observed_at=None,
                         fields={'source_host': 'ac0g-b1'})
            for dns, label in labels]


def test_discovery_drops_a_label_with_control_characters(monkeypatch, caplog):
    import sigmond.sources as sources
    import sigmond.discovery.mdns as mdns
    forged = 'AC0G x\n  bind evil-status.local  host evil'
    hidden = 'AC0G \x1b[2Kx'
    c1 = 'AC0G \x85x'
    rows = _crafted_rows([(A, 'AC0G @EM38ww B1-a longwire'),
                          (B, forged), ('c-status.local', hidden),
                          ('d-status.local', c1)])
    monkeypatch.setattr(mdns, 'probe', lambda env, timeout=5.0: [])
    monkeypatch.setattr(sources, 'inventory', lambda obs: rows)
    with caplog.at_level('WARNING'):
        found = sources.discover_radiods()
    assert [(r.status_dns, r.label) for r in found] == [
        (A, 'AC0G @EM38ww B1-a longwire')]
    for bad in (forged, hidden, c1):
        assert repr(bad) in caplog.text


def test_the_bind_line_prints_the_label_quoted(monkeypatch, capsys):
    smd = _load_smd()
    _bringup_env(monkeypatch, smd, _two_found())
    assert smd.cmd_bringup(_bringup_args(remote_radiod=[A])) == 0
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if 'bind ' + A in l)
    assert repr('AC0G @EM38ww B1-a longwire') in line


def test_a_source_label_cannot_inject_toml(tmp_path):
    import tomllib
    smd = _load_smd()
    from sigmond.sources import SourceKey
    cfg = tmp_path / 'AC0G=B1A.toml'
    cfg.write_text(WSPR_SHARED)
    key = SourceKey('radiod', A)
    evil = 'AC0G x\n[evil]\nk = "v"'
    row = _crafted_rows([(A, evil)])[0]
    smd._apply_sources_to_wspr_recorder([key], [row], cfg, dry_run=False)
    data = tomllib.loads(cfg.read_text())
    assert 'evil' not in data
    assert len(data['source']) == 1
    src = data['source'][0]
    assert src['key'] == f'radiod:{A}' and src['status'] == A
    assert set(src) <= {'key', 'status', 'label'}
    assert src.get('label', evil) == evil          # refused, or kept whole

    # A label with a quote and a backslash still round-trips.
    row = _crafted_rows([(A, 'AC0G "q" \\ loop')])[0]
    smd._apply_sources_to_wspr_recorder([key], [row], cfg, dry_run=False)
    assert tomllib.loads(cfg.read_text())['source'][0]['label'] == \
        'AC0G "q" \\ loop'


def test_render_env_skips_quotes_and_backslashes(tmp_path, capsys):
    from sigmond.coordination import render_env
    coord = tmp_path / 'coordination.toml'
    coord.write_text(
        '[radiod."x.local"]\n'
        'host = "trail\\\\"\n'
        'status_dns = "x.local"\n'
        '\n'
        '[radiod."y.local"]\n'
        'host = "open\\"quote"\n'
        'status_dns = "y.local"\n'
        '\n'
        '[radiod."z.local"]\n'
        'host = "it\'s"\n'
        'status_dns = "z.local"\n')
    env = render_env(load_coordination(coord), passthrough_lookup=lambda t: [])
    keys = dict(ln.split('=', 1) for ln in env.splitlines()
                if ln and not ln.startswith('#'))
    for r in ('X', 'Y', 'Z'):
        assert f'RADIOD_{r}_LOCAL_HOST' not in keys
        assert keys[f'RADIOD_{r}_LOCAL_STATUS'] == f'{r.lower()}.local'
    err = capsys.readouterr().err
    for r in ('X', 'Y', 'Z'):
        assert f'RADIOD_{r}_LOCAL_HOST' in err


# --- final review M2, 2026-11-05 ----------------------------------------------
# A Unicode format character (category Cf, e.g. U+202E RIGHT-TO-LEFT OVERRIDE)
# reorders what the terminal shows without being a control character, and
# `sources list` / `sources add` printed inventory labels raw.

def test_discovery_drops_a_label_with_a_format_character(monkeypatch, caplog):
    import sigmond.sources as sources
    import sigmond.discovery.mdns as mdns
    rlo = 'AC0G ‮x-1b'
    rows = _crafted_rows([(A, 'AC0G @EM38ww B1-a longwire'), (B, rlo)])
    monkeypatch.setattr(mdns, 'probe', lambda env, timeout=5.0: [])
    monkeypatch.setattr(sources, 'inventory', lambda obs: rows)
    with caplog.at_level('WARNING'):
        found = sources.discover_radiods()
    assert [r.status_dns for r in found] == [A]
    assert repr(rlo) in caplog.text


def test_sources_list_prints_labels_quoted(monkeypatch, capsys):
    smd = _load_smd()
    evil = 'AC0G x\x1b[2K‮'
    monkeypatch.setattr(smd, '_sources_inventory',
                        lambda: (_crafted_rows([(A, evil)]), None))
    args = types.SimpleNamespace(keys=False, inventory=True, clients_only=False)
    assert smd.cmd_sources_list(args) == 0
    out = capsys.readouterr().out
    assert evil not in out and repr(evil) in out


def test_sources_add_prints_the_resolved_key_quoted(monkeypatch, capsys):
    import sigmond.sources as sources
    smd = _load_smd()
    evil_dns = 'b1\u202e-status.local'
    monkeypatch.setattr(smd, '_need_root', lambda verb: False)
    monkeypatch.setattr(smd, '_sources_inventory',
                        lambda: (_crafted_rows([(evil_dns, 'AC0G only')]), None))

    class _CS:
        selected = []
        @classmethod
        def load(cls, client): return cls()
        def add(self, key): self.selected.append(key); return True
        def save(self): pass
    monkeypatch.setattr(sources, 'ClientSources', _CS)
    args = types.SimpleNamespace(client='psk-recorder', source='only')
    assert smd.cmd_sources_add(args) == 0
    out = capsys.readouterr().out
    assert evil_dns not in out and repr(f'radiod:{evil_dns}') in out


def test_bind_writes_coordination_env_atomically(tmp_path, monkeypatch):
    # Final review M4, 2026-11-05: coordination.env is the EnvironmentFile of
    # root services; a plain write_text left a window with a torn file.  It
    # now goes through the writer coordination.toml uses.
    import sigmond.coordination as coordination
    smd = _load_smd()
    etc = tmp_path / 'etc'
    coord = etc / 'sigmond' / 'coordination.toml'
    real = coordination._write_coordination
    wrote = []

    def spy(path, text):
        wrote.append(Path(path).name)
        real(path, text)
    monkeypatch.setattr(coordination, '_write_coordination', spy)
    assert smd._sources_bind(B, 'AC0G/B1B', ['psk-recorder'], host='ac0g-b1',
                             timing=True, etc_root=etc, coord_path=coord,
                             inventory_rows=[]) == 0
    assert 'coordination.env' in wrote
    assert 'HF_TIMESTD_RADIOD=' + B in (etc / 'sigmond' / 'coordination.env').read_text()

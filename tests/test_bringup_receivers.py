"""Bring-up from the [[receiver]] list in site-profile.toml (Phase 3, task 5).

The list names, for each reporter ID, the radiod it listens to.  Bring-up
takes it as the operator's choice: no discovery selection, no prompt, and
one `smd admin receiver apply` binds every receiver it declares.
"""
from __future__ import annotations

import types

import pytest

from sigmond.bringup import CLIENT_STAGGER_S, build_plan
from sigmond.catalog import Profile

from .test_sources_bind import (_bringup_args, _bringup_env, _client, _load_smd)

A = 'ac0g-b1-a-status.local'
B = 'ac0g-b1-b-status.local'
WP = ('wspr-recorder', 'psk-recorder')


def _local():
    # A local-radiod profile with two reporter-keyed clients (dasi2-shaped).
    return Profile(
        name='dasi2',
        clients=('hf-timestd', 'wspr-recorder', 'psk-recorder', 'mag-recorder'),
        local_radiod_infra=('igmp-querier', 'gpsdo-monitor'),
        optional=('ka9q-web',),
    )


def _argv_steps(plan, *words):
    n = len(words)
    return [s for s in plan.steps if s.argv[1:1 + n] == list(words)]


# --- build_plan -----------------------------------------------------------------

def test_two_local_receivers_bind_then_make_one_instance_pair_each():
    lr = ((A, 'AC0G/B1A', WP), (B, 'AC0G/B1B', WP))
    p = build_plan(_local(), local_radiod=True, reporter='AC0G/B1A',
                   receivers_declared=True, local_receivers=lr)
    applies = _argv_steps(p, 'admin', 'receiver', 'apply')
    assert len(applies) == 1
    assert applies[0].argv == ['smd', 'admin', 'receiver', 'apply',
                               '--clients', 'psk-recorder,wspr-recorder']
    assert applies[0].kind == 'config'
    assert applies[0].label == 'bind receivers to local radiods'
    # After the hard radiod-configured checkpoint, before any client config.
    i_apply = p.steps.index(applies[0])
    i_ckpt = next(i for i, s in enumerate(p.steps)
                  if s.check == 'radiod-configured')
    first_client_cfg = next(i for i, s in enumerate(p.steps)
                            if s.argv[1:3] == ['config', 'init']
                            and s.argv[3] != 'radiod')
    assert i_ckpt < i_apply < first_client_cfg
    # No per-pair sources bind beside it.
    assert not _argv_steps(p, 'admin', 'sources', 'bind')

    want = sorted([c, r] for c in WP for r in ('AC0G/B1A', 'AC0G/B1B'))
    adds = [s.argv[-2:] for s in _argv_steps(p, 'admin', 'instance', 'add')]
    enables = _argv_steps(p, 'admin', 'instance', 'enable')
    assert sorted(adds) == want
    assert sorted(s.argv[-2:] for s in enables) == want
    assert all(s.settle_s == CLIENT_STAGGER_S for s in enables)
    # Every declared pair must name its own radiod before anything starts.
    ckpt = next(s for s in p.steps if (s.check or '').startswith('instances-bound:'))
    assert ckpt.hard
    assert sorted(ckpt.check.split(':', 1)[1].split(',')) == sorted(
        f'{c}@{r}' for c, r in want)
    sa = p.steps.index(_argv_steps(p, 'admin', 'sources', 'apply')[0])
    assert sa < p.steps.index(ckpt) < p.steps.index(enables[0])


def test_one_local_receiver_keeps_the_reporter_path():
    lr = ((A, 'AC0G/B1A', WP),)
    p = build_plan(_local(), local_radiod=True, reporter='AC0G/B1A',
                   receivers_declared=True, local_receivers=lr)
    assert len(_argv_steps(p, 'admin', 'receiver', 'apply')) == 1
    adds = [s.argv[-2:] for s in _argv_steps(p, 'admin', 'instance', 'add')]
    assert sorted(adds) == sorted([c, 'AC0G/B1A'] for c in WP)
    # R7: the hard checkpoint covers the declared pair, after sources apply.
    ckpt = next(s for s in p.steps
                if (s.check or '').startswith('instances-bound:'))
    assert ckpt.hard
    assert sorted(ckpt.check.split(':', 1)[1].split(',')) == sorted(
        f'{c}@AC0G/B1A' for c in WP)
    sa = _argv_steps(p, 'admin', 'sources', 'apply')
    assert len(sa) == 1 and p.steps.index(sa[0]) < p.steps.index(ckpt)


def test_a_receiver_that_narrows_its_clients_gets_only_those_instances():
    lr = ((A, 'AC0G/B1A', WP), (B, 'AC0G/B1B', ('wspr-recorder',)))
    p = build_plan(_local(), local_radiod=True, receivers_declared=True,
                   local_receivers=lr)
    adds = sorted(s.argv[-2:] for s in _argv_steps(p, 'admin', 'instance', 'add'))
    assert adds == sorted([['wspr-recorder', 'AC0G/B1A'],
                           ['psk-recorder', 'AC0G/B1A'],
                           ['wspr-recorder', 'AC0G/B1B']])


def test_a_shared_antenna_receiver_uploads_off():
    lr = ((A, 'AC0G/B1A', WP), (A, 'AC0G/B1C', WP))
    p = build_plan(_local(), local_radiod=True, receivers_declared=True,
                   local_receivers=lr, local_only=frozenset({'AC0G/B1C'}))
    ups = _argv_steps(p, 'config', 'upload')
    off = [s.argv for s in ups if s.argv[4] == 'AC0G/B1C']
    on = [s.argv for s in ups if s.argv[4] == 'AC0G/B1A']
    assert len(off) == 2 and all(a[-1] == '--off' for a in off)
    assert not any('--via' in a for a in off)
    assert any(a[-2:] == ['--via', 'server-raw'] for a in on)
    assert all('--on' in a for a in on)


def test_declared_remote_receivers_replace_the_per_pair_binds():
    remote = ((A, 'AC0G/B1A', WP), (B, 'AC0G/B1B', WP))
    p = build_plan(_client(), local_radiod=False, remote_radiods=remote,
                   receivers_declared=True, timing_radiod=B)
    assert not _argv_steps(p, 'admin', 'sources', 'bind')
    applies = _argv_steps(p, 'admin', 'receiver', 'apply')
    assert [s.label for s in applies] == ['bind declared receivers']
    tune = next(i for i, s in enumerate(p.steps) if s.argv == ['smd', 'apply'])
    assert tune < p.steps.index(applies[0])
    assert 'configure hf-timestd' in [s.label for s in p.steps]


def test_no_receivers_leaves_the_remote_plan_unchanged():
    two = [(A, 'AC0G/B1A'), (B, 'AC0G/B1B')]
    old = build_plan(_client(), local_radiod=False, remote_radiods=two)
    new = build_plan(_client(), local_radiod=False, remote_radiods=two,
                     receivers_declared=False, local_receivers=(),
                     local_only=frozenset())
    assert ([(s.stage, s.kind, s.label, s.argv) for s in old.steps]
            == [(s.stage, s.kind, s.label, s.argv) for s in new.steps])


# --- cmd_bringup ----------------------------------------------------------------

def _profile_with(tmp_path, monkeypatch, body):
    import sigmond.site_profile as site_profile
    path = tmp_path / 'site-profile.toml'
    path.write_text(body)
    sp = types.SimpleNamespace(source_path=path,
                               effective_reporter_id='AC0G/B2',
                               grid='EM38ww', call='AC0G', psws_station_id='')
    monkeypatch.setattr(site_profile, 'load_site_profile', lambda *a, **k: sp)
    monkeypatch.setattr(site_profile, 'SITE_PROFILE_PATH', path)
    return path


def _no_coordination(monkeypatch, radiods=None):
    import sigmond.coordination as coordination
    c = coordination.Coordination()
    for rid, host in (radiods or {}).items():
        c.radiods[rid] = coordination.Radiod(id=rid, host=host)
    monkeypatch.setattr(coordination, 'load_coordination', lambda *a, **k: c)


TWO_REMOTE = f'''
[[receiver]]
reporter_id = "AC0G/B1A"
radiod = "{A}"

[[receiver]]
reporter_id = "AC0G/B1B"
radiod = "{B}"
'''


def _found(*names):
    from sigmond.sources import DiscoveredRadiod
    return [DiscoveredRadiod(n, f'AC0G @EM38ww {n}', 'ac0g-b1') for n in names]


def test_declared_remote_receivers_skip_discovery_selection(
        tmp_path, monkeypatch, capsys):
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, _found(A, B))
    _profile_with(tmp_path, monkeypatch, TWO_REMOTE)
    _no_coordination(monkeypatch)
    import sigmond.sources as sources

    def no_select(*a, **k):
        raise AssertionError('a declared list must not run select_radiods')
    monkeypatch.setattr(sources, 'select_radiods', no_select)
    monkeypatch.setattr('builtins.input',
                        lambda prompt='': pytest.fail('asked while declared'))
    assert smd.cmd_bringup(_bringup_args()) == 0
    k = calls[0]
    assert k['receivers_declared'] is True
    assert [tuple(x[:2]) for x in k['remote_radiods']] == [
        (A, 'AC0G/B1A'), (B, 'AC0G/B1B')]
    assert k['local_receivers'] == ()

    # One does not answer: stop before planning.
    calls.clear()
    monkeypatch.setattr(sources, 'discover_radiods',
                        lambda timeout=5.0: _found(A))
    assert smd.cmd_bringup(_bringup_args()) == 1
    assert calls == []
    cap = capsys.readouterr()
    assert f'radiod {B} did not answer' in cap.out + cap.err


def test_remote_radiod_flag_and_a_declared_list_conflict(
        tmp_path, monkeypatch, capsys):
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, _found(A, B))
    _profile_with(tmp_path, monkeypatch, TWO_REMOTE)
    _no_coordination(monkeypatch)
    assert smd.cmd_bringup(_bringup_args(remote_radiod=[A])) == 1
    assert calls == []
    cap = capsys.readouterr()
    assert ('the receiver list in site-profile.toml already names the '
            'radiods; edit it or drop --remote-radiod') in cap.out + cap.err


def test_the_injector_receiver_sets_the_timing_radiod(tmp_path, monkeypatch):
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, _found(A, B))
    _profile_with(tmp_path, monkeypatch,
                  TWO_REMOTE.replace(f'radiod = "{B}"',
                                     f'radiod = "{B}"\ninjector = true'))
    _no_coordination(monkeypatch)
    assert smd.cmd_bringup(_bringup_args()) == 0
    assert calls[0]['timing_radiod'] == B


def test_an_invalid_declared_list_stops_before_planning(
        tmp_path, monkeypatch, capsys):
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, _found(A, B))
    _profile_with(tmp_path, monkeypatch, f'''
[[receiver]]
reporter_id = "AC0G/B1A"
radiod = "{A}"
injector = true

[[receiver]]
reporter_id = "AC0G/B1B"
radiod = "{A}"
injector = true
''')
    _no_coordination(monkeypatch)
    assert smd.cmd_bringup(_bringup_args()) == 1
    assert calls == []
    text = ''.join(capsys.readouterr())
    assert 'same_antenna_as' in text            # every message, not the first
    assert 'more than one receiver carries injector' in text


def test_local_receivers_come_from_this_hosts_own_radiods(
        tmp_path, monkeypatch):
    smd = _load_smd()
    monkeypatch.setattr(smd.os, 'cpu_count', lambda: 16)  # the capacity gate reads it
    calls = _bringup_env(monkeypatch, smd, [])
    _profile_with(tmp_path, monkeypatch, TWO_REMOTE + f'''
[[receiver]]
reporter_id = "AC0G/B1C"
radiod = "{A}"
same_antenna_as = "AC0G/B1A"
''')
    _no_coordination(monkeypatch, {A: 'localhost', B: ''})
    import sigmond.sources as sources

    def boom(*a, **k):
        raise AssertionError('all receivers are local: nothing to discover')
    monkeypatch.setattr(sources, 'discover_radiods', boom)
    monkeypatch.setattr(smd, '_detect_local_sdr', lambda: True)
    monkeypatch.setattr(smd, '_detect_magnetometer', lambda: True)
    monkeypatch.setattr(smd, '_detect_gpsdo', lambda: True)
    assert smd.cmd_bringup(_bringup_args(profile='dasi2')) == 0
    k = calls[0]
    assert k['receivers_declared'] is True
    assert [x[1] for x in k['local_receivers']] == [
        'AC0G/B1A', 'AC0G/B1B', 'AC0G/B1C']
    assert tuple(k['remote_radiods']) == ()
    assert k['local_only'] == frozenset({'AC0G/B1C'})


def test_a_declared_list_adds_no_instance_for_the_bare_reporter():
    # A local host whose declared receivers all live elsewhere.
    remote = ((A, 'AC0G/B1A', WP),)
    p = build_plan(_local(), local_radiod=True, reporter='AC0G/S',
                   receivers_declared=True, remote_radiods=remote)
    adds = sorted(s.argv[-2:] for s in _argv_steps(p, 'admin', 'instance', 'add'))
    assert adds == sorted([c, 'AC0G/B1A'] for c in WP)


def test_two_receivers_on_one_remote_radiod_still_get_hf_timestd():
    remote = ((A, 'AC0G/B1A', WP), (A, 'AC0G/B1C', WP))
    p = build_plan(_client(), local_radiod=False, remote_radiods=remote,
                   receivers_declared=True)
    labels = [s.label for s in p.steps]
    assert 'configure hf-timestd' in labels
    assert not any('idle' in l for l in labels)


def _radiod_host(monkeypatch, smd):
    monkeypatch.setattr(smd, '_detect_local_sdr', lambda: True)
    monkeypatch.setattr(smd, '_detect_magnetometer', lambda: True)
    monkeypatch.setattr(smd, '_detect_gpsdo', lambda: True)


def test_a_greenfield_radiod_host_plans_its_silent_receivers_as_local(
        tmp_path, monkeypatch):
    # R8: no coordination block yet, and the radiods are not on the LAN:
    # this bring-up's Stage 1 creates them.
    smd = _load_smd()
    monkeypatch.setattr(smd.os, 'cpu_count', lambda: 16)  # the capacity gate reads it
    calls = _bringup_env(monkeypatch, smd, [])
    _profile_with(tmp_path, monkeypatch, TWO_REMOTE)
    _no_coordination(monkeypatch)
    _radiod_host(monkeypatch, smd)
    assert smd.cmd_bringup(_bringup_args(profile='dasi2')) == 0
    k = calls[0]
    assert [x[:2] for x in k['local_receivers']] == [
        (A, 'AC0G/B1A'), (B, 'AC0G/B1B')]
    assert tuple(k['remote_radiods']) == ()


def test_a_decode_only_host_ignores_a_stale_local_block(
        tmp_path, monkeypatch, capsys):
    # R8: on a host without radiod every receiver must answer, even one a
    # stale coordination block calls local.
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, _found(B))
    _profile_with(tmp_path, monkeypatch, TWO_REMOTE)
    _no_coordination(monkeypatch, {A: 'localhost'})
    assert smd.cmd_bringup(_bringup_args()) == 1
    assert calls == []
    cap = capsys.readouterr()
    assert f'radiod {A} did not answer' in cap.out + cap.err


def test_reporter_flag_and_a_declared_list_conflict(
        tmp_path, monkeypatch, capsys):
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, _found(A, B))
    _profile_with(tmp_path, monkeypatch, TWO_REMOTE)
    _no_coordination(monkeypatch)
    assert smd.cmd_bringup(_bringup_args(reporter='AC0G/X')) == 1
    assert calls == []
    cap = capsys.readouterr()
    assert ('the receiver list in site-profile.toml already names the '
            'reporter IDs; edit it or drop --reporter') in cap.out + cap.err


def test_a_radiod_host_with_one_local_and_one_remote_receiver(
        tmp_path, monkeypatch):
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, _found(B))
    _profile_with(tmp_path, monkeypatch, TWO_REMOTE)
    _no_coordination(monkeypatch, {A: 'localhost'})
    _radiod_host(monkeypatch, smd)
    assert smd.cmd_bringup(_bringup_args(profile='dasi2')) == 0
    k = calls[0]
    assert [x[:2] for x in k['local_receivers']] == [(A, 'AC0G/B1A')]
    assert [tuple(x[:2]) for x in k['remote_radiods']] == [(B, 'AC0G/B1B')]
    assert k['reporter'] == 'AC0G/B1A'
    # The plan makes and checks an instance pair for each receiver.
    p = build_plan(_local(), local_radiod=True, reporter=k['reporter'],
                   receivers_declared=True,
                   local_receivers=k['local_receivers'],
                   remote_radiods=k['remote_radiods'])
    want = sorted([c, r] for c in WP for r in ('AC0G/B1A', 'AC0G/B1B'))
    adds = sorted(s.argv[-2:] for s in _argv_steps(p, 'admin', 'instance', 'add'))
    assert adds == want
    ckpt = next(s for s in p.steps
                if (s.check or '').startswith('instances-bound:'))
    assert sorted(ckpt.check.split(':', 1)[1].split(',')) == sorted(
        f'{c}@{r}' for c, r in want)
    assert len(_argv_steps(p, 'admin', 'receiver', 'apply')) == 1


ONE_REMOTE = f'''
[[receiver]]
reporter_id = "AC0G/B1A"
radiod = "{A}"
clients = ["psk-recorder", "wspr-recorder"]
'''


def test_flags_that_match_the_declared_list_plan(tmp_path, monkeypatch, capsys):
    """R15: Stage 1's `sources bind` wrote the list; the TUI retries with the
    same --remote-radiod/--reporter, which must plan, not refuse."""
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, _found(A, B))
    _profile_with(tmp_path, monkeypatch, ONE_REMOTE)
    _no_coordination(monkeypatch)
    assert smd.cmd_bringup(_bringup_args(remote_radiod=[A.upper()],
                                         reporter='ac0g/b1a')) == 0
    assert [tuple(x[:2]) for x in calls[0]['remote_radiods']] == [
        (A, 'AC0G/B1A')]
    cap = capsys.readouterr()
    assert ('--remote-radiod/--reporter match the receiver list; using the '
            'list') in cap.out + cap.err


def test_flags_that_disagree_with_the_declared_list_refuse(
        tmp_path, monkeypatch, capsys):
    smd = _load_smd()
    calls = _bringup_env(monkeypatch, smd, _found(A, B))
    _profile_with(tmp_path, monkeypatch, ONE_REMOTE)
    _no_coordination(monkeypatch)
    assert smd.cmd_bringup(_bringup_args(remote_radiod=[B],
                                         reporter='AC0G/B1A')) == 1
    assert smd.cmd_bringup(_bringup_args(remote_radiod=[A],
                                         reporter='AC0G/B1B')) == 1
    assert calls == []
    cap = capsys.readouterr()
    assert 'drop --remote-radiod' in cap.out + cap.err
    assert 'drop --reporter' in cap.out + cap.err


def test_the_apply_step_names_the_profiles_clients_minus_skip():
    """R14: a profile without meteor-scatter must not have Stage 1 bind it."""
    lr = ((A, 'AC0G/B1A', WP),)
    p = build_plan(_local(), local_radiod=True, receivers_declared=True,
                   local_receivers=lr)
    (a,) = _argv_steps(p, 'admin', 'receiver', 'apply')
    assert a.argv[-2:] == ['--clients', 'psk-recorder,wspr-recorder']
    p = build_plan(_local(), local_radiod=True, receivers_declared=True,
                   local_receivers=lr, skip=frozenset({'psk-recorder'}))
    (a,) = _argv_steps(p, 'admin', 'receiver', 'apply')
    assert a.argv[-2:] == ['--clients', 'wspr-recorder']
    remote = ((A, 'AC0G/B1A', WP),)
    p = build_plan(_client(), local_radiod=False, remote_radiods=remote,
                   receivers_declared=True)
    (a,) = _argv_steps(p, 'admin', 'receiver', 'apply')
    assert a.argv[-2] == '--clients'
    assert 'meteor-scatter' not in a.argv[-1]

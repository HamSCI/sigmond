"""`smd admin receiver list/apply`, and `sources bind` writing a receiver."""
from __future__ import annotations

import importlib.util
import json
import os
import types
from pathlib import Path
from unittest import mock

import pytest

import sigmond.site_profile as sp
from sigmond.coordination import load_coordination
from sigmond.receivers import Receiver, load_receivers

A = 'ac0g-b1-a-status.local'
B = 'ac0g-b1-b-status.local'
CLIENTS = frozenset({'wspr-recorder', 'psk-recorder'})

PSK_SHARED = """\
[station]
callsign = "AC0G"
grid = "EM38ww"

[[radiod]]
id = "x"
status = "tmp-status.local"
"""


def _load_smd():
    spec = importlib.util.spec_from_loader('smd_mod_receiver_apply', loader=None)
    smd = importlib.util.module_from_spec(spec)
    smd.__dict__['__file__'] = 'bin/smd'
    src = Path(__file__).resolve().parent.parent / 'bin' / 'smd'
    with mock.patch.dict(os.environ, {'SIGMOND_NO_VENV_REEXEC': '1'}):
        exec(compile(src.read_text(), str(src), 'exec'), smd.__dict__)
    return smd


@pytest.fixture
def box(tmp_path):
    etc = tmp_path / 'etc'
    (etc / 'sigmond').mkdir(parents=True)
    return types.SimpleNamespace(
        etc=etc, coord=etc / 'sigmond' / 'coordination.toml',
        profile=etc / 'sigmond' / 'site-profile.toml')


def _apply(smd, box, receivers, **kw):
    with mock.patch.object(sp, 'SITE_PROFILE_PATH', box.profile):
        return smd._receiver_apply(
            receivers, profile_clients=CLIENTS, etc_root=box.etc,
            coord_path=box.coord, profile_path=box.profile,
            inventory_rows=[], **kw)


def _tree(root):
    return {p: p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}


TWO = [Receiver(reporter_id='AC0G/B1A', radiod=A,
                clients=('wspr-recorder', 'psk-recorder')),
       Receiver(reporter_id='AC0G/B1B', radiod=B, injector=True,
                clients=('psk-recorder',))]


def test_apply_binds_each_receiver_once(box):
    smd = _load_smd()
    with mock.patch.object(smd, '_sources_bind', return_value=0) as sb, \
            mock.patch('sigmond.upload.apply_enable'):
        assert _apply(smd, box, TWO) == 0
    assert sb.call_count == 2
    (a1, k1), (a2, k2) = sb.call_args_list
    assert a1[:2] == (A, 'AC0G/B1A') and list(a1[2]) == ['wspr-recorder', 'psk-recorder']
    assert k1['timing'] is False
    assert a2[:2] == (B, 'AC0G/B1B') and list(a2[2]) == ['psk-recorder']
    assert k2['timing'] is True


def test_apply_twice_changes_nothing(box):
    smd = _load_smd()
    _apply(smd, box, TWO)
    first = _tree(box.etc)
    assert first
    _apply(smd, box, TWO)
    assert _tree(box.etc) == first


def test_dropping_a_client_keeps_its_instance(box):
    smd = _load_smd()
    (box.etc / 'psk-recorder').mkdir()
    (box.etc / 'psk-recorder' / 'psk-recorder-config.toml').write_text(PSK_SHARED)
    both = [Receiver(reporter_id='AC0G/B1A', radiod=A,
                     clients=('wspr-recorder', 'psk-recorder'))]
    _apply(smd, box, both)
    inst = box.etc / 'psk-recorder' / 'AC0G=B1A.toml'
    sel = box.etc / 'sigmond' / 'clients' / 'psk-recorder@AC0G=B1A.sources.toml'
    assert inst.exists() and sel.exists()
    before = (inst.read_bytes(), sel.read_bytes())
    _apply(smd, box, [Receiver(reporter_id='AC0G/B1A', radiod=A,
                               clients=('wspr-recorder',))])
    assert (inst.read_bytes(), sel.read_bytes()) == before


def test_a_shared_radiod_receiver_does_not_upload(box):
    smd = _load_smd()
    rs = [Receiver(reporter_id='AC0G/B1A', radiod=A, clients=('psk-recorder',)),
          Receiver(reporter_id='AC0G/B1S', radiod=A, clients=('psk-recorder',),
                   same_antenna_as='AC0G/B1A')]
    # R13: both instances have configs; only the same_antenna_as receiver
    # decides its flag, the other leaves it to the operator.
    (box.etc / 'psk-recorder').mkdir()
    for rid in ('AC0G=B1A', 'AC0G=B1S'):
        (box.etc / 'psk-recorder' / f'{rid}.toml').write_text(PSK_SHARED)
    with mock.patch.object(smd, '_sources_bind', return_value=0), \
            mock.patch('sigmond.upload.apply_enable') as ae:
        assert _apply(smd, box, rs) == 0
    got = {c.args[1]: c.args[2] for c in ae.call_args_list}
    assert got == {'AC0G=B1S': False}


def test_a_local_radiod_named_by_status_gets_no_second_block(box):
    smd = _load_smd()
    box.coord.write_text('[radiod.sigma]\nhost = "localhost"\n'
                         'status_dns = "sigma-status.local"\n')
    r = [Receiver(reporter_id='AC0G/S', radiod='sigma-status.local',
                  clients=('psk-recorder',))]
    assert _apply(smd, box, r) == 0
    c = load_coordination(box.coord)
    assert list(c.radiods) == ['sigma']
    assert {x.radiod_id for x in c.clients} == {'sigma'}


def test_an_invalid_list_touches_nothing(box, capsys):
    smd = _load_smd()
    dup = [Receiver(reporter_id='AC0G/B1A', radiod=A),
           Receiver(reporter_id='AC0G=B1A', radiod=B)]
    with mock.patch.object(smd, '_sources_bind') as sb:
        assert _apply(smd, box, dup) == 1
    sb.assert_not_called()
    assert _tree(box.etc) == {}
    assert 'more than once' in capsys.readouterr().out


def test_dry_run_touches_nothing(box):
    smd = _load_smd()
    assert _apply(smd, box, TWO, dry_run=True) == 0
    assert _tree(box.etc) == {}


def _bind_args(**kw):
    base = dict(status_dns=A, reporter='AC0G/B1A', host='', timing=True,
                clients='psk-recorder')
    base.update(kw)
    return types.SimpleNamespace(**base)


def _run_bind(smd, box, args):
    real = smd._receiver_apply

    def wrapped(*a, **k):
        k.update(etc_root=box.etc, coord_path=box.coord,
                 profile_path=box.profile, inventory_rows=[])
        return real(*a, **k)
    with mock.patch.object(sp, 'SITE_PROFILE_PATH', box.profile), \
            mock.patch.object(smd, '_need_root', return_value=False), \
            mock.patch.object(smd, '_sources_inventory', return_value=([], '')), \
            mock.patch.object(smd, '_receiver_apply', wrapped):
        return smd.cmd_sources_bind(args)


def test_sources_bind_writes_a_receiver(box):
    smd = _load_smd()
    assert _run_bind(smd, box, _bind_args()) == 0
    (r,) = load_receivers(box.profile)
    assert (r.reporter_id, r.radiod, r.injector) == ('AC0G/B1A', A, True)
    assert r.clients == ('psk-recorder',)
    # a second bind of the same reporter replaces, never appends
    assert _run_bind(smd, box, _bind_args(timing=False)) == 0
    (r,) = load_receivers(box.profile)
    assert r.injector is False


def test_receiver_list_json(box, capsys):
    smd = _load_smd()
    box.profile.write_text(
        f'[[receiver]]\nreporter_id = "AC0G/B1A"\nradiod = "{A}"\n'
        f'antenna = "loop"\nclients = ["psk-recorder"]\ninjector = true\n'
        f'[[receiver]]\nreporter_id = "AC0G/B1S"\nradiod = "{A}"\n'
        f'same_antenna_as = "AC0G/B1A"\n')
    with mock.patch.object(sp, 'SITE_PROFILE_PATH', box.profile):
        rc = smd.cmd_receiver_list(types.SimpleNamespace(
            json=True, profile_path=box.profile))
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert list(doc) == ['receivers']
    assert list(doc['receivers'][0]) == [
        'reporter_id', 'radiod', 'antenna', 'clients', 'injector',
        'same_antenna_as', 'uploads']
    assert doc['receivers'][0]['uploads'] is True
    assert doc['receivers'][1]['uploads'] is False
    assert doc['receivers'][0]['clients'] == ['psk-recorder']


def test_a_refused_bind_leaves_the_profile_untouched(box):
    smd = _load_smd()
    assert _run_bind(smd, box, _bind_args(timing=False)) == 0
    before = box.profile.read_bytes()
    # a second reporter on a radiod already in use, with no same_antenna_as
    assert _run_bind(smd, box, _bind_args(reporter='AC0G/B1S')) == 1
    assert box.profile.read_bytes() == before
    # a crafted status name
    assert _run_bind(smd, box, _bind_args(
        status_dns='x"\nevil.local', reporter='AC0G/B1C')) == 1
    assert box.profile.read_bytes() == before


# --- R13: the upload flag belongs to the receiver only when it decides it ---

def _psk_installed(box):
    (box.etc / 'psk-recorder').mkdir(exist_ok=True)
    (box.etc / 'psk-recorder' / 'psk-recorder-config.toml').write_text(PSK_SHARED)


def _env(box, rid='AC0G=B1A', client='psk-recorder'):
    return box.etc / client / 'env' / f'{rid}.env'


def test_apply_before_install_leaves_the_env_stub_to_instance_add(box):
    """C1: Stage 1 applies before clients install; Stage 3a's
    `instance add --force` must still seed the env from instance_env."""
    from sigmond import instance as _inst
    smd = _load_smd()
    rs = [Receiver(reporter_id='AC0G/B1A', radiod=A, clients=('psk-recorder',),
                   upload=True),
          Receiver(reporter_id='AC0G/B1S', radiod=A, clients=('psk-recorder',),
                   same_antenna_as='AC0G/B1A')]
    assert _apply(smd, box, rs) == 0
    assert not _env(box).exists() and not _env(box, 'AC0G=B1S').exists()
    _psk_installed(box)
    with mock.patch.object(_inst, '_instance_env_defaults',
                           return_value={'STUB_KEY': '1'}):
        _inst.create_instance('psk-recorder', 'AC0G=B1A', force=True,
                              etc_root=box.etc)
    assert 'STUB_KEY=1' in _env(box).read_text()


def test_a_rebind_without_upload_leaves_the_operators_flag(box):
    """I4: no explicit `upload` -> the operator's `config upload --off` stands."""
    from sigmond import upload as _upload
    smd = _load_smd()
    _psk_installed(box)
    r = [Receiver(reporter_id='AC0G/B1A', radiod=A, clients=('psk-recorder',))]
    assert _apply(smd, box, r) == 0
    _upload.set_env_flag(_env(box), 'PSK_USE_HS_UPLOADER', '0')
    assert _apply(smd, box, r) == 0
    assert _upload.read_flag(_env(box), 'PSK_USE_HS_UPLOADER') == '0'


def test_a_same_antenna_receiver_with_a_config_gets_upload_off(box):
    from sigmond import upload as _upload
    smd = _load_smd()
    _psk_installed(box)
    rs = [Receiver(reporter_id='AC0G/B1A', radiod=A, clients=('psk-recorder',)),
          Receiver(reporter_id='AC0G/B1S', radiod=A, clients=('psk-recorder',),
                   same_antenna_as='AC0G/B1A')]
    assert _apply(smd, box, rs) == 0
    assert _upload.read_flag(_env(box, 'AC0G=B1S'), 'PSK_USE_HS_UPLOADER') == '0'
    assert _upload.read_flag(_env(box), 'PSK_USE_HS_UPLOADER') is None


def test_an_explicit_upload_with_a_config_sets_the_flag(box):
    from sigmond import upload as _upload
    smd = _load_smd()
    _psk_installed(box)
    r = [Receiver(reporter_id='AC0G/B1A', radiod=A, clients=('psk-recorder',),
                  upload=False)]
    assert _apply(smd, box, r) == 0
    assert _upload.read_flag(_env(box), 'PSK_USE_HS_UPLOADER') == '0'


def test_a_failed_bind_writes_no_upload_flag(box):
    smd = _load_smd()
    _psk_installed(box)
    (box.etc / 'psk-recorder' / 'AC0G=B1A.toml').write_text(PSK_SHARED)
    r = [Receiver(reporter_id='AC0G/B1A', radiod=A, clients=('psk-recorder',),
                  upload=True)]
    with mock.patch.object(smd, '_sources_bind', return_value=1), \
            mock.patch('sigmond.upload.apply_enable') as ae:
        assert _apply(smd, box, r) == 1
    ae.assert_not_called()


def test_apply_twice_with_a_client_config_changes_nothing(box):
    smd = _load_smd()
    _psk_installed(box)
    rs = [Receiver(reporter_id='AC0G/B1A', radiod=A, clients=('psk-recorder',),
                   upload=True),
          Receiver(reporter_id='AC0G/B1S', radiod=A, clients=('psk-recorder',),
                   same_antenna_as='AC0G/B1A')]
    _apply(smd, box, rs)
    first = _tree(box.etc)
    assert _env(box).exists() and _env(box, 'AC0G=B1S').exists()
    _apply(smd, box, rs)
    assert _tree(box.etc) == first


# --- R14: one default client set; `receiver apply --clients` narrows it ---

def _run_apply_cmd(smd, box, **kw):
    seen = {}

    def fake(receivers, **k):
        seen.update(k, receivers=list(receivers))
        return 0
    args = types.SimpleNamespace(dry_run=False, **kw)
    with mock.patch.object(sp, 'SITE_PROFILE_PATH', box.profile), \
            mock.patch.object(smd, '_need_root', return_value=False), \
            mock.patch.object(smd, '_sources_inventory', return_value=([], '')), \
            mock.patch.object(smd, '_receiver_apply', fake):
        rc = smd.cmd_receiver_apply(args)
    return rc, seen


def test_receiver_apply_clients_flag_narrows_the_default_set(box):
    smd = _load_smd()
    box.profile.write_text(f'[[receiver]]\nreporter_id = "AC0G/B1A"\n'
                           f'radiod = "{A}"\n')
    rc, seen = _run_apply_cmd(smd, box, clients='wspr-recorder,psk-recorder')
    assert rc == 0
    assert seen['profile_clients'] == frozenset({'wspr-recorder', 'psk-recorder'})
    rc, seen = _run_apply_cmd(smd, box, clients='mag-recorder')
    assert rc == 1 and not seen


def test_receiver_apply_without_clients_uses_the_shared_default(box):
    smd = _load_smd()
    box.profile.write_text(f'[[receiver]]\nreporter_id = "AC0G/B1A"\n'
                           f'radiod = "{A}"\n')
    with mock.patch.object(smd, '_receiver_profile_clients',
                           return_value=frozenset({'psk-recorder'})):
        rc, seen = _run_apply_cmd(smd, box, clients=None)
    assert rc == 0 and seen['profile_clients'] == frozenset({'psk-recorder'})


def test_the_default_set_is_enabled_or_installed_reporter_keyed(box):
    smd = _load_smd()
    topo = types.SimpleNamespace(
        enabled_components=lambda: ['wspr-recorder', 'mag-recorder'])
    installed = {'psk-recorder', 'hf-timestd'}

    def state(name, *a, **k):
        return types.SimpleNamespace(installed=name in installed)
    with mock.patch('sigmond.topology.load_topology', return_value=topo), \
            mock.patch('sigmond.component_state.compute_state', state):
        got = smd._receiver_profile_clients()
    assert got == frozenset({'wspr-recorder', 'psk-recorder'})


def test_apply_with_clients_binds_only_those(box):
    smd = _load_smd()
    r = [Receiver(reporter_id='AC0G/B1A', radiod=A)]
    with mock.patch.object(smd, '_sources_bind', return_value=0) as sb:
        with mock.patch.object(sp, 'SITE_PROFILE_PATH', box.profile):
            assert smd._receiver_apply(
                r, profile_clients=frozenset({'psk-recorder'}),
                etc_root=box.etc, coord_path=box.coord,
                profile_path=box.profile, inventory_rows=[]) == 0
    assert list(sb.call_args.args[2]) == ['psk-recorder']

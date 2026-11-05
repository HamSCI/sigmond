"""`smd admin instance add <client> <reporter> --radiod <status>`."""
from __future__ import annotations

import importlib.util
import os
import types
from pathlib import Path
from unittest import mock

import pytest

import sigmond.instance as inst
import sigmond.site_profile as sp
from sigmond.receivers import load_receivers

A = 'ac0g-b1-a-status.local'
B = 'ac0g-b1-b-status.local'


def _load_smd():
    spec = importlib.util.spec_from_loader('smd_mod_instance_add_radiod', loader=None)
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


def _add(smd, box, client='psk-recorder', reporter='AC0G/B1A', radiod=A,
         dry_run=False):
    args = types.SimpleNamespace(client=client, reporter_id=reporter,
                                 force=False, dry_run=dry_run, radiod=radiod)
    real = smd._receiver_apply

    def wrapped(*a, **k):
        box.apply_calls.append(a)
        k.update(etc_root=box.etc, coord_path=box.coord,
                 profile_path=box.profile, inventory_rows=[])
        return real(*a, **k)
    box.bind = mock.Mock(return_value=0)
    box.apply_calls = []
    created = mock.Mock(return_value=types.SimpleNamespace(
        config='c', env='e', sources='s'))
    with mock.patch.object(sp, 'SITE_PROFILE_PATH', box.profile), \
            mock.patch.object(smd, '_need_root', return_value=False), \
            mock.patch.object(smd, '_check_catalog_client', return_value=True), \
            mock.patch.object(smd, '_check_systemd_template', return_value=True), \
            mock.patch.object(inst, 'create_instance', created), \
            mock.patch.object(smd, '_sources_inventory', return_value=([], '')), \
            mock.patch.object(smd, '_sources_bind', box.bind), \
            mock.patch('sigmond.upload.apply_enable'), \
            mock.patch.object(smd, '_receiver_apply', wrapped):
        rc = smd.cmd_instance_add(args)
    return rc, created


def test_radiod_records_a_receiver_and_binds_it(box):
    smd = _load_smd()
    rc, created = _add(smd, box)
    assert rc == 0
    created.assert_called_once()
    (r,) = load_receivers(box.profile)
    assert (r.reporter_id, r.radiod, r.clients) == (
        'AC0G/B1A', A, ('psk-recorder',))
    box.bind.assert_called_once()
    assert box.bind.call_args.args[0] == A
    assert box.bind.call_args.args[1] == 'AC0G/B1A'
    assert box.bind.call_args.args[2] == ['psk-recorder']


def test_a_second_client_joins_the_same_receiver(box):
    smd = _load_smd()
    assert _add(smd, box)[0] == 0
    assert _add(smd, box, client='wspr-recorder', reporter='AC0G=B1A')[0] == 0
    (r,) = load_receivers(box.profile)
    assert set(r.clients) == {'psk-recorder', 'wspr-recorder'}


def test_a_reporter_on_another_radiod_is_refused(box, capsys):
    smd = _load_smd()
    assert _add(smd, box)[0] == 0
    before = box.profile.read_bytes()
    rc, created = _add(smd, box, client='wspr-recorder', radiod=B)
    assert rc == 1
    created.assert_not_called()
    assert box.profile.read_bytes() == before
    out = capsys.readouterr().out
    assert 'listens to' in out and A in out
    assert 'move it with the switchboard' in out


def test_a_bad_status_name_is_refused_before_anything(box):
    smd = _load_smd()
    rc, created = _add(smd, box, radiod='not a host!')
    assert rc == 1
    created.assert_not_called()
    assert not box.profile.exists()


def test_without_radiod_nothing_changes(box):
    smd = _load_smd()
    rc, created = _add(smd, box, radiod=None)
    assert rc == 0
    created.assert_called_once()
    assert not box.profile.exists()


def test_dry_run_writes_nothing(box):
    smd = _load_smd()
    rc, created = _add(smd, box, dry_run=True)
    assert rc == 0
    assert created.call_args.kwargs['dry_run'] is True
    assert not box.profile.exists() and not box.coord.exists()
    assert box.apply_calls == []
    box.bind.assert_not_called()


def test_an_invalid_candidate_list_is_refused_before_create(box, capsys):
    smd = _load_smd()
    assert _add(smd, box)[0] == 0
    before = box.profile.read_bytes()
    # a second reporter on the same radiod needs same_antenna_as
    rc, created = _add(smd, box, reporter='AC0G/B1S')
    assert rc == 1
    created.assert_not_called()
    assert box.profile.read_bytes() == before
    assert 'same_antenna_as' in capsys.readouterr().out


# --- I2: a stored receiver with no `clients` means the default set ---

def _default_receiver(box):
    box.profile.write_text(f'[[receiver]]\nreporter_id = "AC0G/B1A"\n'
                           f'radiod = "{A}"\n')


def test_a_default_set_receiver_stays_default_for_a_default_client(box):
    smd = _load_smd()
    _default_receiver(box)
    with mock.patch.object(smd, '_receiver_profile_clients',
                           return_value=frozenset({'psk-recorder',
                                                   'wspr-recorder'})):
        rc, _ = _add(smd, box, client='psk-recorder')
    assert rc == 0
    (r,) = load_receivers(box.profile)
    assert r.clients == ()


def test_a_default_set_receiver_widens_for_a_client_outside_it(box):
    smd = _load_smd()
    _default_receiver(box)
    with mock.patch.object(smd, '_receiver_profile_clients',
                           return_value=frozenset({'psk-recorder',
                                                   'wspr-recorder'})):
        rc, _ = _add(smd, box, client='meteor-scatter')
    assert rc == 0
    (r,) = load_receivers(box.profile)
    assert r.clients == ('meteor-scatter', 'psk-recorder', 'wspr-recorder')

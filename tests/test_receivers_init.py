"""`smd config render --init` adopts existing instances as receivers."""
from __future__ import annotations

import types
from unittest import mock

import sigmond.commands.config as cfg
import sigmond.site_profile as sp
from sigmond.receivers import load_receivers

B1A = 'ac0g-b1-a-status.local'
B1B = 'ac0g-b1-b-status.local'


def _src(root, name, *keys):
    items = ', '.join(f'"{k}"' for k in keys)
    (root / f'{name}.sources.toml').write_text(f'selected = [{items}]\n')


def _init(tmp_path, capsys, profile=None, dry_run=False):
    prof = tmp_path / 'site-profile.toml'
    if profile is not None:
        prof.write_text(profile)
    clients = tmp_path / 'clients'
    clients.mkdir(exist_ok=True)
    with mock.patch.object(sp, 'SITE_PROFILE_PATH', prof), \
            mock.patch.object(cfg, 'SITE_PROFILE_PATH', prof), \
            mock.patch.object(cfg, 'CLIENTS_ROOT', clients):
        rc = cfg.cmd_config_render(
            types.SimpleNamespace(init=True, dry_run=dry_run))
    cap = capsys.readouterr()
    return rc, prof, clients, cap.out + cap.err


def _seed(tmp_path):
    clients = tmp_path / 'clients'
    clients.mkdir(exist_ok=True)
    return clients


def test_init_builds_receivers_from_instances(tmp_path, capsys):
    c = _seed(tmp_path)
    _src(c, 'wspr-recorder@AC0G=B1A', f'radiod:{B1A}')
    _src(c, 'psk-recorder@AC0G=B1A', f'radiod:{B1A}')
    _src(c, 'wspr-recorder@AC0G=B1B', f'radiod:{B1B}')
    rc, prof, _, out = _init(tmp_path, capsys)
    assert rc == 0
    rs = load_receivers(prof)
    assert [(r.reporter_id, r.radiod, r.clients) for r in rs] == [
        ('AC0G/B1A', B1A, ('psk-recorder', 'wspr-recorder')),
        ('AC0G/B1B', B1B, ('wspr-recorder',))]
    assert 'AC0G/B1A' in out


def test_init_leaves_out_what_it_cannot_map(tmp_path, capsys):
    c = _seed(tmp_path)
    _src(c, 'wspr-recorder@AC0G=B1A', f'radiod:{B1A}')
    _src(c, 'psk-recorder@AC0G=B1A', f'radiod:{B1B}')      # two radiods
    _src(c, 'wspr-recorder@AC0G=B1B', f'radiod:{B1B}')
    _src(c, 'psk-recorder@AC0G=B1C')                       # empty
    _src(c, 'wspr-recorder@AC0G=B1D', 'kiwisdr:foo')       # non-radiod
    rc, prof, _, out = _init(tmp_path, capsys)
    assert rc == 0
    assert [r.reporter_id for r in load_receivers(prof)] == ['AC0G/B1B']
    assert out.count('left out') >= 3
    assert 'AC0G/B1A' in out and 'AC0G/B1C' in out and 'AC0G/B1D' in out


def test_init_leaves_a_declared_list_alone(tmp_path, capsys):
    c = _seed(tmp_path)
    _src(c, 'wspr-recorder@AC0G=B1B', f'radiod:{B1B}')
    body = f'[[receiver]]\nreporter_id = "AC0G/B1A"\nradiod = "{B1A}"\n'
    rc, prof, _, out = _init(tmp_path, capsys, profile=body)
    assert rc == 0
    assert prof.read_text() == body
    assert 'already declares 1 receiver' in out


def test_init_on_a_single_radiod_station_adds_nothing(tmp_path, capsys):
    rc, prof, _, out = _init(tmp_path, capsys)
    assert rc == 0
    assert prof.read_text() == sp.TEMPLATE
    assert load_receivers(prof) == []


def test_init_refuses_a_list_that_fails_validation(tmp_path, capsys):
    c = _seed(tmp_path)
    _src(c, 'wspr-recorder@AC0G=B1A', f'radiod:{B1A}')
    _src(c, 'wspr-recorder@AC0G=B1B', f'radiod:{B1A}')
    rc, prof, _, out = _init(tmp_path, capsys)
    assert rc == 1
    assert not prof.exists()
    assert 'same_antenna_as' in out


def test_init_dry_run_writes_nothing(tmp_path, capsys):
    c = _seed(tmp_path)
    _src(c, 'wspr-recorder@AC0G=B1A', f'radiod:{B1A}')
    rc, prof, _, out = _init(tmp_path, capsys, dry_run=True)
    assert rc == 0
    assert not prof.exists()
    assert 'AC0G/B1A' in out


def test_init_skips_a_reporter_with_an_unreadable_sources_file(tmp_path, capsys):
    c = _seed(tmp_path)
    _src(c, 'wspr-recorder@AC0G=B1A', f'radiod:{B1A}')
    (c / 'psk-recorder@AC0G=B1A.sources.toml').write_text('selected = [\n')
    _src(c, 'wspr-recorder@AC0G=B1B', f'radiod:{B1B}')
    rc, prof, _, out = _init(tmp_path, capsys)
    assert rc == 0
    assert [r.reporter_id for r in load_receivers(prof)] == ['AC0G/B1B']
    assert 'left out: AC0G/B1A' in out
    assert 'unreadable' in out


def test_init_leaves_out_a_client_that_is_not_reporter_keyed(tmp_path, capsys):
    c = _seed(tmp_path)
    _src(c, 'wspr-recorder@AC0G=B1A', f'radiod:{B1A}')
    _src(c, 'hfdl-recorder@AC0G=B1A', f'radiod:{B1A}')
    _src(c, 'wspr-recorder@AC0G=B1B', f'radiod:{B1B}')
    rc, prof, _, out = _init(tmp_path, capsys)
    assert rc == 0
    rs = load_receivers(prof)
    assert [(r.reporter_id, r.clients) for r in rs] == [
        ('AC0G/B1A', ('wspr-recorder',)), ('AC0G/B1B', ('wspr-recorder',))]
    assert 'left out: hfdl-recorder@AC0G/B1A' in out

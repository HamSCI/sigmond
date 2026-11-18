"""`smd admin instance split`: codar-sounder and hfdl-recorder run one
instance per receiver (tasks/transporter-reporters/2026-11-18-design.md,
part 3).

The shapes: AC0G-B2, a transporter with no radiod of its own, carries
AC0G/B1A and AC0G/B1B from B1's two radiods; KFS-MAIN carries four KFS
receivers.  The order matters most: every per-receiver config first, and a
refused config init leaves the station exactly as it was.
"""
from __future__ import annotations

import importlib.util
import os
import types
from pathlib import Path
from unittest import mock

import pytest

from sigmond import instance_split as S
from sigmond.receivers import Receiver

A = 'ac0g-b1-a-status.local'
B = 'ac0g-b1-b-status.local'
B2 = [Receiver('AC0G/B1A', radiod=A), Receiver('AC0G/B1B', radiod=B)]
KFS = [Receiver('KFS/OMNI', radiod='kfs-omni-status.local'),
       Receiver('KFS/SW', radiod='kfs-sw-status.local'),
       Receiver('KFS/SE', radiod='kfs-se-status.local'),
       Receiver('KFS/NW', radiod='kfs-nw-status.local')]


def _shared(etc, client, radiods):
    d = etc / client
    d.mkdir(parents=True, exist_ok=True)
    body = '[station]\ncallsign = "AC0G"\n\n' + ''.join(
        f'[[radiod]]\nstatus = "{r}"\n\n' for r in radiods)
    (d / f'{client}-config.toml').write_text(body)
    return d / f'{client}-config.toml'


class FakeSystem:
    """systemctl and the client's config init, recorded in order."""

    def __init__(self, etc, *, fail_init=None, units=None):
        self.etc = etc
        self.calls = []
        self.fail_init = fail_init or set()
        self.units = dict(units or {})       # unit -> [enabled, active]

    def __call__(self, argv, env=None):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == 'systemctl':
            verb, unit = argv[1], argv[-1]
            st = self.units.setdefault(unit, [False, False])
            if verb == 'is-enabled':
                return 0, 'enabled' if st[0] else 'disabled'
            if verb == 'is-active':
                return 0, 'active' if st[1] else 'inactive'
            if verb == 'enable':
                st[:] = [True, True]
            if verb == 'disable':
                st[:] = [False, False]
            if verb in ('list-units', 'list-unit-files'):
                out = []
                for u, (en, ac) in self.units.items():
                    if not u.startswith(argv[2].split('*')[0]):
                        continue
                    if verb == 'list-units' and ac:
                        out.append(f'{u} loaded active running x')
                    if verb == 'list-unit-files' and en:
                        out.append(f'{u} enabled enabled')
                return 0, '\n'.join(out)
            return 0, ''
        # <client> config init --non-interactive --instance K --reporter-id R --radiod-status S
        client = Path(argv[0]).name
        key = argv[argv.index('--instance') + 1]
        if key in self.fail_init:
            return 1, f'{client}: config init refused: no location'
        rid = argv[argv.index('--reporter-id') + 1]
        st = argv[argv.index('--radiod-status') + 1]
        (self.etc / client / f'{key}.toml').write_text(
            f'[instance]\nreporter_id = "{rid}"\n\n[[radiod]]\nstatus = "{st}"\n')
        return 0, f'wrote {key}.toml'

    def ops(self):
        """The changing calls, without the state queries."""
        return [c for c in self.calls
                if not (c[0] == 'systemctl' and c[1] in
                        ('is-enabled', 'is-active', 'list-units',
                         'list-unit-files'))]


def _plan(etc, client, receivers, sysm, **kw):
    return S.build_plan(client, receivers, etc_root=etc,
                        known_instances=S.known_instances(client, sysm),
                        today='20261118', **kw)


def _steps(plan, sysm, **kw):
    return S.steps(plan, state=lambda u: S.unit_state(u, sysm),
                   init_base=[f'/usr/local/bin/{plan.client}', 'config', 'init'],
                   **kw)


def _legacy(client, radiods, timer=False):
    u = {f'{client}@{r}.service': [True, True] for r in radiods}
    if timer:
        u.update({f'hfdl-recorder-squitters@{r}.timer': [True, True]
                  for r in radiods})
    return u


# ---------- the plan ----------

def test_b2_plan_names_each_receiver_and_the_legacy_side(tmp_path):
    _shared(tmp_path, 'hfdl-recorder', [A, B])
    sysm = FakeSystem(tmp_path, units=_legacy('hfdl-recorder', [A, B], True))
    plan = _plan(tmp_path, 'hfdl-recorder', B2, sysm, station_id='AC0G/B2')
    assert not plan.problems
    assert [(t.reporter_id, t.key, t.radiod, t.config.name, t.unit, t.timer)
            for t in plan.targets] == [
        ('AC0G/B1A', 'AC0G-B1A', A, 'AC0G-B1A.toml',
         'hfdl-recorder@AC0G-B1A.service',
         'hfdl-recorder-squitters@AC0G-B1A.timer'),
        ('AC0G/B1B', 'AC0G-B1B', B, 'AC0G-B1B.toml',
         'hfdl-recorder@AC0G-B1B.service',
         'hfdl-recorder-squitters@AC0G-B1B.timer')]
    assert plan.legacy_units == [f'hfdl-recorder@{A}.service',
                                 f'hfdl-recorder@{B}.service']
    assert plan.legacy_timers == [f'hfdl-recorder-squitters@{A}.timer',
                                  f'hfdl-recorder-squitters@{B}.timer']
    assert plan.shared_aside.name == \
        'hfdl-recorder-config.toml.pre-instances-20261118'
    # The aside name must not end in .toml: lifecycle would read it as an
    # instance.
    assert not plan.shared_aside.name.endswith('.toml')


def test_kfs_main_plan_gets_four_codar_instances(tmp_path):
    radiods = [r.radiod for r in KFS]
    _shared(tmp_path, 'codar-sounder', radiods)
    sysm = FakeSystem(tmp_path, units=_legacy('codar-sounder', radiods))
    plan = _plan(tmp_path, 'codar-sounder', KFS, sysm, station_id='KFS-MAIN')
    assert not plan.problems
    assert [t.unit for t in plan.targets] == [
        'codar-sounder@KFS-OMNI.service', 'codar-sounder@KFS-SW.service',
        'codar-sounder@KFS-SE.service', 'codar-sounder@KFS-NW.service']
    assert all(t.timer == '' for t in plan.targets)
    assert len(plan.legacy_units) == 4 and plan.legacy_timers == []
    todo = _steps(plan, sysm, max_centres=3)
    inits = [s.argv for s in todo if s.phase == 'config']
    assert list(inits[0]) == [
        '/usr/local/bin/codar-sounder', 'config', 'init', '--non-interactive',
        '--instance', 'KFS-OMNI', '--reporter-id', 'KFS/OMNI',
        '--radiod-status', 'kfs-omni-status.local', '--max-centres', '3']


def test_a_stray_default_instance_is_retired_too(tmp_path):
    _shared(tmp_path, 'hfdl-recorder', [A, B])
    units = _legacy('hfdl-recorder', [A, B])
    units['hfdl-recorder@default.service'] = [True, False]
    sysm = FakeSystem(tmp_path, units=units)
    plan = _plan(tmp_path, 'hfdl-recorder', B2, sysm, station_id='AC0G/B2')
    assert 'hfdl-recorder@default.service' in plan.legacy_units


def test_one_receiver_with_the_station_s_own_id_refuses_unless_forced(tmp_path):
    one = [Receiver('AC0G/ND', radiod='ac0g-nd-status.local')]
    _shared(tmp_path, 'codar-sounder', ['ac0g-nd-status.local'])
    sysm = FakeSystem(tmp_path)
    plan = _plan(tmp_path, 'codar-sounder', one, sysm, station_id='AC0G/ND')
    assert plan.refused and 'nothing to split' in plan.problems[0]
    plan = _plan(tmp_path, 'codar-sounder', one, sysm, station_id='AC0G/ND',
                 force=True)
    assert not plan.refused


def test_a_radiod_no_receiver_names_refuses(tmp_path):
    _shared(tmp_path, 'codar-sounder', [A, B, 'other-status.local'])
    sysm = FakeSystem(tmp_path)
    plan = _plan(tmp_path, 'codar-sounder', B2, sysm, station_id='AC0G/B2')
    assert plan.refused and 'other-status.local' in plan.problems[0]


def test_a_receiver_sharing_an_antenna_gets_no_instance(tmp_path):
    rx = B2 + [Receiver('AC0G/B1C', radiod=A, same_antenna_as='AC0G/B1A')]
    _shared(tmp_path, 'codar-sounder', [A, B])
    plan = _plan(tmp_path, 'codar-sounder', rx, FakeSystem(tmp_path),
                 station_id='AC0G/B2')
    assert [t.key for t in plan.targets] == ['AC0G-B1A', 'AC0G-B1B']
    assert plan.skipped[0][0] == 'AC0G/B1C'


def test_a_conflicting_per_receiver_file_refuses(tmp_path):
    _shared(tmp_path, 'codar-sounder', [A, B])
    (tmp_path / 'codar-sounder' / 'AC0G-B1A.toml').write_text(
        '[instance]\nreporter_id = "AC0G/B1A"\n\n[[radiod]]\nstatus = "x-status.local"\n')
    plan = _plan(tmp_path, 'codar-sounder', B2, FakeSystem(tmp_path),
                 station_id='AC0G/B2')
    assert plan.refused and 'x-status.local' in plan.problems[0]


def test_other_clients_refuse():
    plan = S.build_plan('psk-recorder', B2, known_instances=())
    assert plan.refused


# ---------- apply ----------

def test_apply_writes_every_config_before_touching_a_unit(tmp_path):
    shared = _shared(tmp_path, 'hfdl-recorder', [A, B])
    sysm = FakeSystem(tmp_path, units=_legacy('hfdl-recorder', [A, B], True))
    plan = _plan(tmp_path, 'hfdl-recorder', B2, sysm, station_id='AC0G/B2')
    todo = _steps(plan, sysm)
    sysm.calls.clear()
    wrote = []
    rc = S.apply(plan, todo, run=sysm, say=lambda m: None,
                 manifest=lambda: wrote.append('manifest') or 0)
    assert rc == 0
    ops = sysm.ops()
    kinds = [('init' if c[0] != 'systemctl' else f'{c[1]} {c[-1]}') for c in ops]
    assert kinds[:2] == ['init', 'init']
    assert 'init' not in kinds[2:]
    first_new = kinds.index('enable hfdl-recorder@AC0G-B1A.service')
    last_old = max(i for i, k in enumerate(kinds) if k.startswith('disable'))
    assert last_old < first_new
    # Timers follow the services.
    assert kinds.index('enable hfdl-recorder-squitters@AC0G-B1A.timer') > \
        kinds.index('enable hfdl-recorder@AC0G-B1B.service')
    assert not shared.exists()
    assert plan.shared_aside.exists()
    assert wrote == ['manifest']
    assert not any('radiod@' in ' '.join(c) for c in sysm.calls)


def test_a_refused_config_init_touches_nothing(tmp_path):
    shared = _shared(tmp_path, 'codar-sounder', [A, B])
    before = shared.read_text()
    sysm = FakeSystem(tmp_path, fail_init={'AC0G-B1B'},
                      units=_legacy('codar-sounder', [A, B]))
    plan = _plan(tmp_path, 'codar-sounder', B2, sysm, station_id='AC0G/B2')
    todo = _steps(plan, sysm)
    sysm.calls.clear()
    manifest = mock.Mock(return_value=0)
    rc = S.apply(plan, todo, run=sysm, say=lambda m: None, manifest=manifest)
    assert rc == 1
    assert [c for c in sysm.ops() if c[0] == 'systemctl'] == []
    assert shared.read_text() == before
    assert not (tmp_path / 'codar-sounder' / 'AC0G-B1A.toml').exists()
    assert sorted(p.name for p in (tmp_path / 'codar-sounder').iterdir()) == \
        ['codar-sounder-config.toml']
    manifest.assert_not_called()
    assert sysm.units[f'codar-sounder@{A}.service'] == [True, True]


def test_a_second_run_changes_nothing(tmp_path):
    _shared(tmp_path, 'hfdl-recorder', [A, B])
    sysm = FakeSystem(tmp_path, units=_legacy('hfdl-recorder', [A, B], True))
    plan = _plan(tmp_path, 'hfdl-recorder', B2, sysm, station_id='AC0G/B2')
    assert S.apply(plan, _steps(plan, sysm), run=sysm, say=lambda m: None) == 0
    snapshot = sorted(p.name for p in (tmp_path / 'hfdl-recorder').iterdir())
    again = _plan(tmp_path, 'hfdl-recorder', B2, sysm, station_id='AC0G/B2')
    assert not again.refused
    assert again.legacy_units == [] and again.shared is None
    assert all(t.config_ok for t in again.targets)
    assert _steps(again, sysm) == []
    assert sorted(p.name for p in (tmp_path / 'hfdl-recorder').iterdir()) == snapshot


def test_an_install_re_rendered_placeholder_is_left_alone(tmp_path):
    """After the split an `smd update` may re-render the template (if_absent);
    its radiod is the placeholder, so there is nothing to retire."""
    _shared(tmp_path, 'codar-sounder', [A, B])
    sysm = FakeSystem(tmp_path, units=_legacy('codar-sounder', [A, B]))
    plan = _plan(tmp_path, 'codar-sounder', B2, sysm, station_id='AC0G/B2')
    S.apply(plan, _steps(plan, sysm), run=sysm, say=lambda m: None)
    _shared(tmp_path, 'codar-sounder', ['<configure-via-config-init>'])
    again = _plan(tmp_path, 'codar-sounder', B2, sysm, station_id='AC0G/B2')
    assert _steps(again, sysm) == []


# ---------- the verb ----------

def _load_smd():
    spec = importlib.util.spec_from_loader('smd_mod_instance_split', loader=None)
    smd = importlib.util.module_from_spec(spec)
    smd.__dict__['__file__'] = 'bin/smd'
    src = Path(__file__).resolve().parent.parent / 'bin' / 'smd'
    with mock.patch.dict(os.environ, {'SIGMOND_NO_VENV_REEXEC': '1'}):
        exec(compile(src.read_text(), str(src), 'exec'), smd.__dict__)
    return smd


def _profile(tmp_path, receivers):
    from sigmond.receivers import write_receivers
    p = tmp_path / 'site-profile.toml'
    p.write_text('[station]\ncallsign = "AC0G"\n')
    write_receivers(p, receivers)
    return p


def _coord(call):
    from sigmond.coordination import Coordination, Host, Station
    return Coordination(host=Host(call=call), station=Station(uploader_call=call))


def test_the_verb_s_dry_run_changes_nothing(tmp_path, capsys):
    smd = _load_smd()
    _shared(tmp_path, 'codar-sounder', [A, B])
    sysm = FakeSystem(tmp_path, units=_legacy('codar-sounder', [A, B]))
    args = types.SimpleNamespace(
        client='codar-sounder', apply=False, max_centres=None, force=False,
        profile_path=_profile(tmp_path, B2), coord=_coord('AC0G/B2'))
    assert smd.cmd_instance_split(args, run=sysm, etc_root=tmp_path) == 0
    out = capsys.readouterr().out
    assert 'codar-sounder@AC0G-B1A.service' in out
    assert 'receivers = AC0G/B1A, AC0G/B1B' in out
    assert sysm.ops() == []
    assert (tmp_path / 'codar-sounder' / 'codar-sounder-config.toml').exists()


def test_the_verb_applies_and_then_finds_nothing_to_do(tmp_path, capsys):
    smd = _load_smd()
    _shared(tmp_path, 'codar-sounder', [A, B])
    sysm = FakeSystem(tmp_path, units=_legacy('codar-sounder', [A, B]))
    args = types.SimpleNamespace(
        client='codar-sounder', apply=True, max_centres=2, force=False,
        profile_path=_profile(tmp_path, B2), coord=_coord('AC0G/B2'))
    assert smd.cmd_instance_split(args, run=sysm, etc_root=tmp_path,
                                  manifest=lambda: 0) == 0
    assert sysm.units['codar-sounder@AC0G-B1B.service'] == [True, True]
    assert sysm.units[f'codar-sounder@{A}.service'] == [False, False]
    sysm.calls.clear()
    assert smd.cmd_instance_split(args, run=sysm, etc_root=tmp_path,
                                  manifest=lambda: 0) == 0
    assert 'nothing to do' in capsys.readouterr().out
    assert sysm.ops() == []


def test_the_verb_refuses_max_centres_for_hfdl(tmp_path):
    smd = _load_smd()
    args = types.SimpleNamespace(client='hfdl-recorder', apply=False,
                                 max_centres=2, force=False)
    assert smd.cmd_instance_split(args, run=FakeSystem(tmp_path)) == 2

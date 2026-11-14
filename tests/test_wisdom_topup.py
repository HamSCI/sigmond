"""FFT wisdom covers the fleet's channel sets, and image wisdom tops up.

2026-11-14: a read-only sweep of 18 stations found fft.log misses on 14, 48
sizes outside FFT_WISDOM_PROFILES, and `wisdom_verdict` calling image-adopted
wisdom 'current' without reading fft.log (N8UR: 27 misses, "current").
A large forward transform (rof >= 1000000) takes hours: it is reported, never
planned by an automatic run.
"""
from pathlib import Path

from sigmond import wisdom
from sigmond.wisdom import wisdom_verdict
from tests.test_wisdom_current import (  # noqa: F401  (plan_env is a fixture)
    B4, CXC, PLACED, _Args, _cpuinfo, _host, plan_env, smd)

FIXTURE = Path(__file__).parent / 'fixtures' / 'fleet-misses-20261114.txt'
ADOPTED = f'model={B4}\nplanned_profiles=\nadopted=legacy-image\n'


class _Auto(_Args):
    auto = True


def test_every_fleet_miss_is_in_the_planning_list():
    sizes = [l.strip() for l in FIXTURE.read_text().splitlines() if l.strip()]
    assert len(sizes) == 96
    lacking = [s for s in sizes if s not in wisdom.FFT_WISDOM_PROFILES]
    assert lacking == []


def test_the_list_has_no_duplicates():
    p = wisdom.FFT_WISDOM_PROFILES
    assert len(p) == len(set(p))


# ── verdict ─────────────────────────────────────────────────────────────

def test_adopted_wisdom_with_small_misses_tops_up(tmp_path):
    kw = _host(tmp_path, sidecar=ADOPTED, miss='cdb300\ncdb600\ncif2000\n')
    assert wisdom_verdict(**kw) == 'top-up'


def test_n8ur_adopted_with_cdb300_cdb600_cif2000_tops_up(tmp_path, monkeypatch):
    kw = _host(tmp_path, sidecar=ADOPTED,
               miss='cdb300 cdb600 cif2000 cof2000 cob2500 rof3240000\n')
    for name, attr in (('WISDOM_FILE', 'wisdom_file'), ('WISDOM_SIDECAR', 'sidecar'),
                       ('FFT_MISS_LOG', 'miss_log'), ('CPUINFO', 'cpuinfo')):
        monkeypatch.setattr(wisdom, name, kw[attr])
    monkeypatch.setattr(smd, '_wisdom_placement', lambda: PLACED)
    assert smd._wisdom_verdict() == 'top-up'


def test_adopted_wisdom_missing_only_the_big_forward_is_current(tmp_path):
    kw = _host(tmp_path, sidecar=ADOPTED, miss='rof3240000\nrof1620000\n')
    assert wisdom_verdict(**kw) == 'current'
    assert wisdom.large_forward_misses(kw['sidecar'], kw['miss_log']) == [
        'rof3240000', 'rof1620000']


def test_adopted_on_another_cpu_stays_fresh(tmp_path):
    kw = _host(tmp_path, sidecar=f'model={CXC}\nplanned_profiles=\nadopted=legacy-image\n',
               miss='cdb300\n')
    assert wisdom_verdict(**kw) == 'fresh'


def test_adopted_never_goes_fresh_on_placement(tmp_path):
    kw = _host(tmp_path, sidecar=ADOPTED + 'radiod_core_type=uniform\nradiod_l3_bytes=1\n',
               miss='cdb300\n')
    assert wisdom_verdict(**kw, placement=PLACED) == 'top-up'


def test_non_adopted_big_forward_does_not_trigger_a_top_up(tmp_path):
    kw = _host(tmp_path, sidecar=f'model={B4}\nplanned_profiles=cob15\n',
               miss='rof3240000\n')
    assert wisdom_verdict(**kw) == 'current'
    assert wisdom.large_forward_misses(kw['sidecar'], kw['miss_log']) == ['rof3240000']


def test_a_planned_big_forward_is_no_longer_reported(tmp_path):
    kw = _host(tmp_path, sidecar=f'model={B4}\nplanned_profiles=cob15,rof3240000\n',
               miss='rof3240000\n')
    assert wisdom.large_forward_misses(kw['sidecar'], kw['miss_log']) == []


def test_a_smaller_rof_still_triggers(tmp_path):
    kw = _host(tmp_path, sidecar=ADOPTED, miss='rof999999\n')
    assert wisdom_verdict(**kw) == 'top-up'


# ── needed ──────────────────────────────────────────────────────────────

def _live(kw, monkeypatch):
    for name, attr in (('WISDOM_FILE', 'wisdom_file'), ('WISDOM_SIDECAR', 'sidecar'),
                       ('FFT_MISS_LOG', 'miss_log'), ('CPUINFO', 'cpuinfo')):
        monkeypatch.setattr(wisdom, name, kw[attr])
    monkeypatch.setattr(smd, '_wisdom_placement', lambda: None)


def test_needed_exits_0_for_adopted_small_misses(tmp_path, monkeypatch):
    kw = _host(tmp_path, sidecar=ADOPTED, miss='cdb300\n')
    _live(kw, monkeypatch)
    assert smd.cmd_wisdom_needed(_Args()) == 0


def test_needed_exits_1_and_says_so_for_a_big_forward_miss(tmp_path, monkeypatch, capsys):
    kw = _host(tmp_path, sidecar=ADOPTED, miss='rof3240000\n')
    _live(kw, monkeypatch)
    assert smd.cmd_wisdom_needed(_Args()) == 1
    out = capsys.readouterr()
    text = out.out + out.err
    assert 'FFTW_ESTIMATE' in text and 'smd admin wisdom plan' in text
    assert 'rof3240000' in text


def test_status_reports_the_big_forward_miss(tmp_path, monkeypatch, capsys):
    kw = _host(tmp_path, sidecar=ADOPTED, miss='rof3240000\n')
    _live(kw, monkeypatch)
    smd.cmd_wisdom_status(_Args())
    out = capsys.readouterr()
    assert 'FFTW_ESTIMATE' in out.out + out.err
    assert 'smd admin wisdom plan' in out.out + out.err


# ── the top-up itself ───────────────────────────────────────────────────

def _topup_env(plan_env, monkeypatch, sidecar, miss):
    tp = plan_env['tmp_path']
    (tp / 'wisdomf').write_text('(image plans)')
    (tp / 'wisdomf.cpu').write_text(sidecar)
    (tp / 'fft.log').write_text(miss)
    monkeypatch.setattr(smd, '_wisdom_placement', lambda: None)
    return tp


def _profiles_planned(plan_env):
    (cmd,) = [c[1] for c in plan_env['calls'] if c[0] == 'planner']
    return cmd, cmd[cmd.index('-o') + 2:]


def test_adopted_top_up_keeps_plans_and_plans_only_the_small_misses(plan_env, monkeypatch):
    _topup_env(plan_env, monkeypatch, ADOPTED, 'cdb300\ncif2000\nrof3240000\n')
    assert smd.cmd_wisdom_plan(_Auto()) == 0
    cmd, profiles = _profiles_planned(plan_env)
    assert '-n' not in cmd
    assert profiles == ['cdb300', 'cif2000']


def test_after_a_top_up_the_verdict_is_current_and_adoption_is_kept(plan_env, monkeypatch):
    tp = _topup_env(plan_env, monkeypatch, ADOPTED, 'cdb300\ncif2000\nrof3240000\n')
    assert smd._wisdom_verdict() == 'top-up'
    assert smd.cmd_wisdom_plan(_Auto()) == 0
    side = wisdom.read_sidecar(tp / 'wisdomf.cpu')
    assert side['adopted'] == 'legacy-image'
    assert set(side['planned_profiles'].split(',')) == {'cdb300', 'cif2000'}
    assert smd._wisdom_verdict() == 'current'


def test_non_adopted_top_up_excludes_the_big_forward(plan_env, monkeypatch):
    tp = _topup_env(plan_env, monkeypatch,
                    f'model={B4}\nplanned_profiles=cob15\n', 'cdb300\nrof1620000\n')
    assert smd.cmd_wisdom_plan(_Auto()) == 0
    cmd, profiles = _profiles_planned(plan_env)
    assert '-n' not in cmd and 'rof1620000' not in profiles and 'cdb300' in profiles
    assert 'cob15' in wisdom.read_sidecar(tp / 'wisdomf.cpu')['planned_profiles'].split(',')
    assert smd._wisdom_verdict() == 'current'


def test_update_reports_a_big_forward_and_names_the_explicit_fix():
    from tests.test_update_plan import _state
    plan = __import__('sigmond.update', fromlist=['x']).plan_update(
        _state(wisdom_large_misses=['rof3240000']))
    (w,) = [a for a in plan if a.kind == 'wisdom']
    assert 'FFTW_ESTIMATE' in w.detail and 'smd admin wisdom plan' in w.detail
    assert [a for a in plan if a.kind == 'restart'] == []


def test_a_top_up_verdict_does_not_block_applys_radiod_start(tmp_path, monkeypatch):
    kw = _host(tmp_path, sidecar=ADOPTED, miss='cdb300\n')
    _live(kw, monkeypatch)
    assert smd._wisdom_verdict() == 'top-up'
    assert smd._wisdom_blocks_radiod() is False
    # apply's start and firmware-restart branch both hang on this one gate
    src = (Path(__file__).parent.parent / 'bin' / 'smd').read_text()
    assert 'wisdom_ready = not _wisdom_blocks_radiod(_WISDOM_FILE)' in src


def test_fresh_or_missing_wisdom_still_blocks_radiod(tmp_path, monkeypatch):
    _live(_host(tmp_path, sidecar=f'model={CXC}\nplanned_profiles=\n'), monkeypatch)
    assert smd._wisdom_blocks_radiod() is True
    _live(_host(tmp_path / 'x', sidecar=ADOPTED, wisdom_text=None)
          if (tmp_path / 'x').mkdir() is None else None, monkeypatch)
    assert smd._wisdom_blocks_radiod() is True


def test_update_state_ignores_a_large_miss_the_sidecar_already_planned(tmp_path):
    kw = _host(tmp_path, sidecar=f'model={B4}\nplanned_profiles=rof3240000\n',
               miss='rof3240000\ncdb300\n')
    assert wisdom.large_forward_misses(kw['sidecar'], kw['miss_log']) == []
    assert wisdom.topup_misses(kw['sidecar'], kw['miss_log']) == ['cdb300']
    src = (Path(__file__).parent.parent / 'bin' / 'smd').read_text()
    assert "'wisdom_large_misses': large_forward_misses(" in src


def test_the_big_forward_verify_string_can_pass():
    from tests.test_update_plan import _state
    from sigmond.update import plan_update
    (w,) = [a for a in plan_update(_state(wisdom_large_misses=['rof3240000']))
            if a.kind == 'wisdom']
    assert 'fft.log lists no' not in w.verify and 'wisdom status' in w.verify


def test_a_top_up_run_does_not_promise_hours(plan_env, monkeypatch, capsys):
    _topup_env(plan_env, monkeypatch, ADOPTED, 'cdb300\n')
    assert smd.cmd_wisdom_plan(_Auto()) == 0
    assert 'can run for hours' not in capsys.readouterr().out

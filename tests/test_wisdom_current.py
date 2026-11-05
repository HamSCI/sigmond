"""Wisdom is current only when it was made for THIS cpu, THESE sizes, THIS placement.

sigma, 2026-11-05: a June /etc/fftw/wisdomf, planned for other sizes, kept
sigmond-wisdom.service (ConditionPathExists=!wisdomf) and bring-up
(wisdom_file.exists()) from ever running the planner.  Both gates asked
"is there a file?".  They now ask wisdom_is_current().
"""
import importlib.machinery
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from sigmond import wisdom
from sigmond.wisdom import wisdom_is_current

REPO = Path(__file__).resolve().parent.parent
B4 = 'AMD Ryzen 7 5825U with Radeon Graphics'
CXC = 'AMD Ryzen 5 5560U with Radeon Graphics'


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_wisdom_current", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_wisdom_current", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def _cpuinfo(tmp_path, model=B4):
    p = tmp_path / 'cpuinfo'
    p.write_text(f'processor\t: 0\nmodel name\t: {model}\n')
    return p


def _host(tmp_path, *, model=B4, sidecar=None, miss='', wisdom_text='(w)'):
    """A fake host: returns kwargs for wisdom_is_current()."""
    w = tmp_path / 'wisdomf'
    if wisdom_text is not None:
        w.write_text(wisdom_text)
    sc = tmp_path / 'wisdomf.cpu'
    if sidecar is not None:
        sc.write_text(sidecar)
    log = tmp_path / 'fft.log'
    if miss:
        log.write_text(miss)
    return dict(wisdom_file=w, sidecar=sc, miss_log=log,
                cpuinfo=_cpuinfo(tmp_path, model))


def test_a_file_with_no_sidecar_is_not_current(tmp_path):
    # sigma's June wisdomf: a file, no record of what it was planned for.
    assert not wisdom_is_current(**_host(tmp_path))


def test_no_file_is_not_current(tmp_path):
    kw = _host(tmp_path, sidecar=f'model={B4}\n', wisdom_text=None)
    assert not wisdom_is_current(**kw)


def test_wisdom_planned_on_another_cpu_is_not_current(tmp_path):
    kw = _host(tmp_path, sidecar=f'model={CXC}\nplanned_profiles=cob15\n')
    assert not wisdom_is_current(**kw)


def test_a_logged_miss_not_yet_planned_means_replan(tmp_path):
    kw = _host(tmp_path, miss='cob1234\n',
               sidecar=f'model={B4}\nplanned_profiles=cob15,cob45\n')
    assert not wisdom_is_current(**kw)


def test_matching_cpu_and_no_new_misses_is_current(tmp_path):
    kw = _host(tmp_path, miss='cob45\n',
               sidecar=f'model={B4}\nplanned_profiles=cob15,cob45\n')
    assert wisdom_is_current(**kw)


def test_seeding_a_bundle_carries_its_sidecar(tmp_path, monkeypatch):
    kw = _host(tmp_path, model=B4, wisdom_text=None)
    monkeypatch.setattr(smd, '_WISDOM_FILE', kw['wisdom_file'])
    monkeypatch.setattr(wisdom, 'WISDOM_SIDECAR', kw['sidecar'])
    monkeypatch.setattr(wisdom, 'CPUINFO', kw['cpuinfo'])
    monkeypatch.setattr(smd, '_run', lambda cmd, **k: (
        subprocess.run([c for c in cmd if c != 'sudo'], capture_output=True)))
    smd._seed_bundled_wisdom()
    assert kw['wisdom_file'].exists()
    assert kw['sidecar'].exists()
    assert wisdom_is_current(**kw)


def test_a_v2_sidecar_bundle_is_seeded(tmp_path, monkeypatch):
    # ⛔ _seed_bundled_wisdom compared the sidecar's RAW TEXT with the model, but
    # both shipped sidecars are key=value (v2), so no bundle was ever seeded on
    # a repo install (sigma, 2026-11-05).
    kw = _host(tmp_path, model=B4, wisdom_text=None)
    monkeypatch.setattr(smd, '_WISDOM_FILE', kw['wisdom_file'])
    monkeypatch.setattr(wisdom, 'WISDOM_SIDECAR', kw['sidecar'])
    monkeypatch.setattr(wisdom, 'CPUINFO', kw['cpuinfo'])
    monkeypatch.setattr(smd, '_run', lambda cmd, **k: (
        subprocess.run([c for c in cmd if c != 'sudo'], capture_output=True)))
    smd._seed_bundled_wisdom()
    bundle = REPO / 'wisdom' / 'wisdomf-amd-ryzen-7-5825u'
    assert kw['wisdom_file'].read_bytes() == bundle.read_bytes()


def test_seeding_never_overwrites_an_existing_file(tmp_path, monkeypatch):
    kw = _host(tmp_path, model=B4, wisdom_text='(mine)')
    monkeypatch.setattr(smd, '_WISDOM_FILE', kw['wisdom_file'])
    monkeypatch.setattr(wisdom, 'WISDOM_SIDECAR', kw['sidecar'])
    monkeypatch.setattr(wisdom, 'CPUINFO', kw['cpuinfo'])
    smd._seed_bundled_wisdom()
    assert kw['wisdom_file'].read_text() == '(mine)'


def test_wisdom_from_another_core_type_or_l3_slice_is_not_current(tmp_path):
    side = (f'model={B4}\nplanned_profiles=cob15\n'
            'radiod_core_type=performance\nradiod_l3_bytes=10485760\n')
    kw = _host(tmp_path, sidecar=side)
    assert wisdom_is_current(**kw, placement={
        'radiod_core_type': 'performance', 'radiod_l3_bytes': 10485760})
    assert not wisdom_is_current(**kw, placement={
        'radiod_core_type': 'uniform', 'radiod_l3_bytes': 10485760})
    assert not wisdom_is_current(**kw, placement={
        'radiod_core_type': 'performance', 'radiod_l3_bytes': 5242880})


def test_moving_between_like_cores_keeps_the_wisdom(tmp_path):
    side = (f'model={B4}\nplanned_profiles=cob15\n'
            'radiod_core_type=uniform\nradiod_l3_bytes=16777216\n')
    kw = _host(tmp_path, sidecar=side)
    # radiod moved from cores 6-7 to 8-9: same type, same L3 slice.
    assert wisdom_is_current(**kw, placement=wisdom.radiod_placement(
        {8, 9}, set(), cache_root=_l3_tree(tmp_path, 16384, [{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}])))


def _l3_tree(tmp_path, kib, islands):
    root = tmp_path / 'cpusys'
    for n, cpus in enumerate(islands):
        for c in cpus:
            d = root / f'cpu{c}' / 'cache' / 'index3'
            d.mkdir(parents=True)
            (d / 'level').write_text('3\n')
            (d / 'size').write_text(f'{kib}K\n')
            (d / 'shared_cpu_list').write_text(','.join(map(str, sorted(cpus))) + '\n')
    return root


def test_placement_reads_the_l3_domain_of_radiods_cores(tmp_path):
    root = _l3_tree(tmp_path, 8192, [{0, 1, 2, 3}, {4, 5, 6, 7}])
    got = wisdom.radiod_placement({5, 6}, {4, 5, 6, 7}, cache_root=root)
    assert got == {'radiod_core_type': 'performance',
                   'radiod_l3_bytes': 8192 * 1024}
    assert wisdom.radiod_placement({5}, set(), cache_root=root)[
        'radiod_core_type'] == 'uniform'


# ── cmd_wisdom_plan: the conditions radiod will run under ──────────────

class _Args:
    no_install = False
    fresh = False


@pytest.fixture
def plan_env(tmp_path, monkeypatch):
    calls = []
    tmp = tmp_path / 'wisdomf.new'
    monkeypatch.setattr(wisdom, 'WISDOM_TMP', tmp)
    monkeypatch.setattr(wisdom, 'WISDOM_FILE', tmp_path / 'wisdomf')
    monkeypatch.setattr(wisdom, 'WISDOM_SIDECAR', tmp_path / 'wisdomf.cpu')
    monkeypatch.setattr(wisdom, 'WISDOM_LOG', tmp_path / 'wisdom.log')
    monkeypatch.setattr(wisdom, 'FFT_MISS_LOG', tmp_path / 'fft.log')
    monkeypatch.setattr(wisdom, 'CPUINFO', _cpuinfo(tmp_path))
    monkeypatch.setattr(smd.shutil, 'which', lambda n: f'/usr/bin/{n}')
    monkeypatch.setattr(smd, '_radiod_plan_cpus', lambda: '')
    monkeypatch.setattr(smd, '_load_topology', lambda *a, **k: {})
    import sigmond.cpu as cpu
    monkeypatch.setattr(cpu, 'get_radiod_instances', lambda: ['radiod@sigma.service'])
    monkeypatch.setattr(cpu, 'assign_radiod_cores',
                        lambda units, *a, **k: {'radiod@sigma.service': {6, 7}})
    monkeypatch.setattr(cpu, 'get_performance_cpus', lambda *a, **k: set())
    monkeypatch.setattr(smd.os, 'sched_setaffinity',
                        lambda pid, cpus: calls.append(('affinity', set(cpus))))
    proc_int = tmp_path / 'interrupts'
    proc_int.write_text(' 45:  1 2 3 PCI-MSI 0-edge xhci_hcd\n 46: 1 PCI-MSI nvme0q1\n')
    irq_root = tmp_path / 'irq'
    (irq_root / '45').mkdir(parents=True)
    (irq_root / '45' / 'smp_affinity_list').write_text('0-3\n')
    monkeypatch.setenv('SIGMOND_PROC_INTERRUPTS', str(proc_int))
    monkeypatch.setenv('SIGMOND_IRQ_ROOT', str(irq_root))

    class _P:
        stdout = iter(['planned\n'])
        def wait(self): return 0

    def fake_run(cmd, *a, **k):
        if cmd and 'sigmond-rx888-irq-affinity' in str(cmd[0]):
            calls.append(('irq-script',))
        return subprocess.CompletedProcess(cmd, 0, '', '')

    def fake_popen(cmd, *a, **k):
        calls.append(('planner', list(cmd)))
        tmp.write_text('(plan)')
        return _P()

    monkeypatch.setattr(smd.subprocess, 'run', fake_run)
    monkeypatch.setattr(smd.subprocess, 'Popen', fake_popen)
    return dict(calls=calls, irq=irq_root / '45' / 'smp_affinity_list',
                tmp_path=tmp_path)


def test_the_planner_runs_on_radiods_cores(plan_env):
    assert smd.cmd_wisdom_plan(_Args()) == 0
    assert ('affinity', {6, 7}) in plan_env['calls']


def test_the_planner_lays_out_irqs_before_timing(plan_env):
    assert smd.cmd_wisdom_plan(_Args()) == 0
    kinds = [c[0] for c in plan_env['calls']]
    assert kinds.index('irq-script') < kinds.index('planner')
    assert kinds.index('irq-script') < kinds.index('affinity')


def test_the_planner_refuses_while_an_xhci_irq_sits_on_radiods_cores(plan_env, capsys):
    plan_env['irq'].write_text('6-7\n')
    assert smd.cmd_wisdom_plan(_Args()) != 0
    assert not any(c[0] == 'planner' for c in plan_env['calls'])
    out = capsys.readouterr()
    assert 'xhci' in (out.out + out.err)


def test_a_finished_plan_records_the_sidecar(plan_env):
    assert smd.cmd_wisdom_plan(_Args()) == 0
    side = wisdom.read_sidecar(plan_env['tmp_path'] / 'wisdomf.cpu')
    assert side['model'] == B4
    assert side['radiod_core_type'] == 'uniform'
    assert 'cob15' in side['planned_profiles'].split(',')


def test_no_local_radiod_plans_unpinned(plan_env, monkeypatch):
    import sigmond.cpu as cpu
    monkeypatch.setattr(cpu, 'get_radiod_instances', lambda: [])
    assert smd.cmd_wisdom_plan(_Args()) == 0
    kinds = [c[0] for c in plan_env['calls']]
    assert 'irq-script' not in kinds and 'affinity' not in kinds


def test_needed_exits_zero_only_when_not_current(tmp_path, monkeypatch, capsys):
    kw = _host(tmp_path, sidecar=f'model={B4}\nplanned_profiles=cob15\n')
    monkeypatch.setattr(wisdom, 'WISDOM_FILE', kw['wisdom_file'])
    monkeypatch.setattr(wisdom, 'WISDOM_SIDECAR', kw['sidecar'])
    monkeypatch.setattr(wisdom, 'FFT_MISS_LOG', kw['miss_log'])
    monkeypatch.setattr(wisdom, 'CPUINFO', kw['cpuinfo'])
    monkeypatch.setattr(smd, '_wisdom_placement', lambda: None)
    assert smd.cmd_wisdom_needed(_Args()) == 1
    assert B4 in capsys.readouterr().out
    kw['sidecar'].write_text(f'model={CXC}\n')
    assert smd.cmd_wisdom_needed(_Args()) == 0

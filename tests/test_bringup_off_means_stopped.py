"""What bring-up leaves off in topology, it leaves stopped and disabled.

Bare-metal v3.94 run 2 (2026-11-21, no RX888, no GPSDO): gpsdo-monitor and
igmp-querier installed `--no-enable`, but each one's own install.sh enables
and starts its unit, so `smd check` read "gpsdo-monitor, igmp-querier run
but topology does not enable them".  Bring-up already stops and disables a
DORMANT sensor client (`smd disable`); the radio half it defers for want of
an SDR gets the same, and gpsdo-monitor with no GPSDO is now DORMANT the
way mag-recorder is with no magnetometer: off in topology, units off.

Either end state is consistent: topology off and units stopped + disabled,
or topology on and running.  When the device or the SDR appears, a later
bring-up (firstrun on the SDR's arrival) or `smd adopt` enables and starts
them; the tests below pin that path too.
"""
from sigmond.catalog import Profile
from sigmond.bringup import STAGE4, build_plan


def _dasi2(**kw):
    return Profile(
        name='dasi2',
        clients=('hf-timestd', 'wspr-recorder', 'psk-recorder', 'mag-recorder',
                 'gmag-webui', 'station-web'),
        local_radiod_infra=('igmp-querier', 'gpsdo-monitor', 'ka9q-web'),
        optional=kw.get('optional', ()),
    )


def _argvs(plan):
    return [' '.join(s.argv or []) for s in plan.steps]


def _index(plan, pred):
    return next(i for i, s in enumerate(plan.steps) if pred(s))


def _disables(plan):
    """{component: step index} for every `smd disable` step."""
    out = {}
    for i, s in enumerate(plan.steps):
        if s.argv[1:2] == ['disable']:
            for c in s.argv[2:]:
                out[c] = i
    return out


# --------------------------------------------------------------------------
# No SDR: the deferred radio half is stopped and disabled after it installs
# --------------------------------------------------------------------------

DEFERRED = ('igmp-querier', 'gpsdo-monitor', 'ka9q-web', 'ka9q-radio',
            'hf-timestd', 'wspr-recorder', 'psk-recorder')


def test_no_sdr_disables_every_deferred_component_after_its_install():
    p = build_plan(_dasi2(), local_radiod=True, sdr_absent=True)
    off = _disables(p)
    for c in DEFERRED:
        assert c in off, f'{c} is deferred and must be stopped + disabled'
        inst = _index(p, lambda s, c=c: s.argv[1:2] == ['install']
                      and s.argv[3:4] == [c])
        assert inst < off[c], f'{c}: disable must follow its install'


def test_no_sdr_disable_comes_before_the_manifest_and_the_start():
    p = build_plan(_dasi2(), local_radiod=True, sdr_absent=True)
    off = max(_disables(p).values())
    manifest = _index(p, lambda s: s.argv[1:4] == ['admin', 'uploader', 'manifest'])
    start = _index(p, lambda s: s.label.startswith('start the components this plan'))
    assert off < manifest < start


def test_no_sdr_leaves_the_independent_track_running():
    p = build_plan(_dasi2(), local_radiod=True, sdr_absent=True)
    off = _disables(p)
    for c in ('mag-recorder', 'station-web'):
        assert c not in off, f'{c} needs no radio; it must not be disabled'
    assert 'smd start --components station-web' in _argvs(p)


def test_no_sdr_disables_the_deferred_optional_too():
    p = build_plan(_dasi2(optional=('hf-tec',)), local_radiod=True,
                   sdr_absent=True, with_optional=True)
    assert 'hf-tec' in _disables(p)


def test_a_full_plan_disables_nothing_it_enables():
    p = build_plan(_dasi2(), local_radiod=True)
    assert _disables(p) == {}


def test_the_sdr_arriving_enables_and_starts_the_deferred_infra():
    # firstrun re-runs bring-up when the SDR arrives; that plan must enable
    # and start igmp-querier, or a deferred one stays off for good.
    p = build_plan(_dasi2(), local_radiod=True, sdr_absent=False)
    argv = _argvs(p)
    assert 'smd enable igmp-querier' in argv
    stack = next(s for s in p.steps if s.label == 'start radiod + local-radiod infra')
    assert 'igmp-querier' in stack.argv[-1].split(',')
    assert 'gpsdo-monitor' in stack.argv[-1].split(',')


# --------------------------------------------------------------------------
# No GPSDO: gpsdo-monitor is DORMANT the way mag-recorder is
# --------------------------------------------------------------------------

def test_no_gpsdo_installs_gpsdo_monitor_dormant_with_the_sdr():
    p = build_plan(_dasi2(), local_radiod=True,
                   dormant=frozenset({'gpsdo-monitor'}))
    argv = _argvs(p)
    assert 'smd enable gpsdo-monitor' not in argv
    assert 'smd install --components gpsdo-monitor --yes --no-enable --no-pull' in argv
    assert 'gpsdo-monitor' in _disables(p)
    # `smd start --components X` enables X in topology; naming a dormant
    # gpsdo-monitor there would undo the disable.
    for s in p.steps:
        if s.argv[1:3] == ['start', '--components']:
            assert 'gpsdo-monitor' not in s.argv[3].split(','), s.label
    stack = next(s for s in p.steps if s.label == 'start radiod + local-radiod infra')
    assert stack.argv[-1].split(',') == ['ka9q-radio', 'igmp-querier', 'ka9q-web']


def test_no_gpsdo_and_no_sdr_reads_dormant_not_deferred():
    p = build_plan(_dasi2(), local_radiod=True, sdr_absent=True,
                   dormant=frozenset({'gpsdo-monitor'}))
    labels = [s.label for s in p.steps]
    assert 'disable gpsdo-monitor (DORMANT — no GPSDO)' in labels
    assert 'install gpsdo-monitor (not enabled — radio deferred)' not in labels
    assert 'igmp-querier' in _disables(p)


def test_a_present_gpsdo_keeps_gpsdo_monitor_on():
    p = build_plan(_dasi2(), local_radiod=True)
    assert 'smd enable gpsdo-monitor' in _argvs(p)
    assert 'gpsdo-monitor' not in _disables(p)


def test_radiod_only_no_gpsdo_never_starts_gpsdo_monitor():
    prof = Profile(name='radiod-only', clients=(),
                   local_radiod_infra=('gpsdo-monitor',),
                   optional=('igmp-querier', 'ka9q-web'))
    p = build_plan(prof, local_radiod=True, with_optional=True,
                   dormant=frozenset({'gpsdo-monitor'}))
    assert 'gpsdo-monitor' in _disables(p)
    stack = next(s for s in p.steps if s.label == 'start radiod + local-radiod infra')
    assert 'gpsdo-monitor' not in stack.argv[-1].split(',')


def test_radiod_only_no_sdr_disables_the_deferred_stack():
    prof = Profile(name='radiod-only', clients=(),
                   local_radiod_infra=('gpsdo-monitor',),
                   optional=('igmp-querier', 'ka9q-web'))
    p = build_plan(prof, local_radiod=True, with_optional=True, sdr_absent=True)
    off = _disables(p)
    for c in ('gpsdo-monitor', 'ka9q-radio', 'igmp-querier', 'ka9q-web'):
        assert c in off, c
    assert p.steps[off['igmp-querier']].stage == STAGE4

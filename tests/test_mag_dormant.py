"""The sensor decides: no magnetometer => mag-recorder and gmag-webui install
DORMANT (installed, enabled=false, never started); with one, as before.

N8UR (John Ackermann), 2026-11-12/14: answered "no" to the wizard's DASI
question, bring-up fell back to profile dasi2, enabled mag-recorder on a
station with no magnetometer, and `smd check` FAILed "mag-recorder is
installed but has no service"."""
import importlib.util
import os
import types
from pathlib import Path
from unittest import mock

from sigmond.catalog import Profile
from sigmond.bringup import build_plan

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    spec = importlib.util.spec_from_loader("smd_mod_mag_dormant", loader=None)
    smd = importlib.util.module_from_spec(spec)
    smd.__dict__["__file__"] = "bin/smd"
    src = REPO / "bin" / "smd"
    with mock.patch.dict(os.environ, {"SIGMOND_NO_VENV_REEXEC": "1"}):
        exec(compile(src.read_text(), str(src), "exec"), smd.__dict__)
    return smd


def _prof():
    return Profile(name='dasi2',
                   clients=('hf-timestd', 'mag-recorder', 'gmag-webui'),
                   local_radiod_infra=('gpsdo-monitor',), optional=())


def _argv(plan, kind, comp):
    return [s.argv for s in plan.steps
            if s.kind == kind and s.argv and comp in ' '.join(s.argv)]


# -- the gate's decision -----------------------------------------------------

def test_no_magnetometer_makes_both_dormant_and_says_so(monkeypatch, capsys):
    smd = _load_smd()
    monkeypatch.setattr(smd, '_detect_magnetometer', lambda: False)
    monkeypatch.setattr(smd, '_detect_gpsdo', lambda: True)
    dormant = smd._bringup_dormant(_prof())
    assert {'mag-recorder', 'gmag-webui'} <= dormant
    cap = capsys.readouterr()
    out = cap.out + cap.err
    assert 'no magnetometer on the bus' in out
    assert 'mag-recorder installs DORMANT' in out
    assert 'smd enable mag-recorder' in out


def test_magnetometer_found_is_unchanged(monkeypatch, capsys):
    smd = _load_smd()
    monkeypatch.setattr(smd, '_detect_magnetometer', lambda: True)
    monkeypatch.setattr(smd, '_detect_gpsdo', lambda: True)
    assert smd._bringup_dormant(_prof()) == frozenset()
    cap = capsys.readouterr()
    assert 'DORMANT' not in cap.out + cap.err


# -- the plan ------------------------------------------------------------------

def test_dormant_sensor_clients_install_not_enabled_and_never_start():
    p = build_plan(_prof(), local_radiod=True,
                   dormant=frozenset({'mag-recorder', 'gmag-webui'}))
    for comp in ('mag-recorder', 'gmag-webui'):
        assert not [s for s in p.steps if s.kind == 'enable'
                    and s.argv == ['smd', 'enable', comp]], comp
        inst = _argv(p, 'install', comp)
        assert inst and all('--no-enable' in a for a in inst), comp
        assert not [s for s in p.steps if s.kind == 'start' and s.argv
                    and s.argv[-1] == comp], comp


def test_dormant_sensor_install_also_leaves_topology_disabled():
    # An image's topology may already say enabled=true; the plan must say no.
    p = build_plan(_prof(), local_radiod=True,
                   dormant=frozenset({'mag-recorder'}))
    assert ['smd', 'disable', 'mag-recorder'] in [s.argv for s in p.steps]


def test_present_magnetometer_enables_and_starts_as_before():
    p = build_plan(_prof(), local_radiod=True)
    for comp in ('mag-recorder', 'gmag-webui'):
        assert ['smd', 'enable', comp] in [s.argv for s in p.steps]
        assert ['smd', 'start', '--components', comp] in [s.argv for s in p.steps]
        assert ['smd', 'disable', comp] not in [s.argv for s in p.steps]


def test_dormant_gpsdo_monitor_keeps_its_existing_treatment():
    p = build_plan(_prof(), local_radiod=True,
                   dormant=frozenset({'gpsdo-monitor'}))
    assert ['smd', 'enable', 'gpsdo-monitor'] in [s.argv for s in p.steps]

"""smd check's topology line (sigmond.topology_drift)."""
from types import SimpleNamespace as NS

from sigmond.commands import check
from sigmond.topology_drift import Drift


def _env(drift, bringup='inactive'):
    return check.Env(drift=lambda: drift,
                     bringup=lambda: NS(state=bringup),
                     reader=NS(tail=lambda *a: ''))


def test_clean_is_ok():
    ln = check.topology_line(_env(Drift()))
    assert ln.mark == check.OK


def test_running_not_enabled_fails_with_enable_fix():
    ln = check.topology_line(_env(Drift(['hfdl-recorder'], [])))
    assert ln.mark == check.FAIL
    assert 'hfdl-recorder runs but topology does not enable it' in ln.text
    assert ln.text.endswith('Fix: smd enable hfdl-recorder')


def test_two_running_not_enabled_read_plural():
    ln = check.topology_line(_env(Drift(['codar-sounder', 'hfdl-recorder'], [])))
    assert 'run but topology does not enable them, so their uploads' in ln.text
    assert ln.text.endswith('smd enable codar-sounder hfdl-recorder')


def test_enabled_stopped_fails_with_start_fix():
    ln = check.topology_line(_env(Drift([], ['hfdl-recorder'])))
    assert ln.mark == check.FAIL
    assert ln.text.endswith('Fix: smd start hfdl-recorder')


def test_stopped_client_another_line_watches_is_left_to_it():
    ln = check.topology_line(_env(Drift([], ['psk-recorder'])))
    assert ln.mark == check.OK


def test_stopped_during_bringup_waits():
    ln = check.topology_line(_env(Drift([], ['hfdl-recorder']), bringup='active'))
    assert ln.mark == check.WAIT


def test_no_probe_no_line():
    assert check.topology_line(check.Env()) is None


def test_registered_with_metadata():
    assert ('topology', check.topology_line) in check.CHECKS
    assert check.CHECK_META['topology'][0] == 'platform.topology'

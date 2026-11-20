"""`smd check` reads a consumer of a dormant radiod as waiting, not failing.

Michael, 2026-11-20, "Waiting is a pass": the bare-metal install test has no
RX888, so radiod sits dormant and its consumers cannot run.  Dormant means
radiod installed, not running, not failed, and no SDR on the bus.  A failed
radiod, a present SDR or an unknown SDR keeps the consumers' FAIL."""
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from sigmond import receivers
from sigmond.commands import check
from tests.check_fakes import make_env, svc
from tests.test_check_grape import config

DORMANT = dict(service=lambda p: svc('inactive', None, enabled=False), sdr_present=lambda: False)


def _grape_cfg():
    return config(Path('/x'))


def _env(**kw):
    kw.setdefault('timestd_config', _grape_cfg)
    return make_env(**kw)


LINES = [(check.psk_line, 'PSK'), (check.wspr_line, 'WSPR'),
         (check.meteor_line, 'meteor scatter'), (check.grape_line, 'GRAPE')]


@pytest.mark.parametrize("fn,label", LINES)
def test_consumer_waits_on_dormant_radiod(fn, label):
    line = fn(_env(**DORMANT))
    assert line.label == label
    assert line.mark == check.WAIT and 'waits on dormant radiod' in line.text


def test_receiver_page_waits():
    env = make_env(http=lambda url: (None, 'refused'), **DORMANT)
    line = check.page_check('receiver page', 8081, None, radio=True)(env)
    assert line.mark == check.WAIT
    assert line.text.endswith(check.RADIOD_DORMANT)
    assert '127.0.0.1:8081 does not answer' in line.text


def test_receiver_page_in_the_table_passes_radio():
    env = make_env(http=lambda url: (None, 'refused'), **DORMANT)
    fn = dict(check.CHECKS)['receiver page']
    assert fn(env).mark == check.WAIT
    # the station pages are no radio consumer: they still fail
    assert dict(check.CHECKS)['station pages'](env).mark == check.FAIL


def test_receiver_page_that_answers_is_ok():
    env = make_env(http=lambda url: (200, ''), **DORMANT)
    assert check.page_check('receiver page', 8081, None, radio=True)(env).mark == check.OK


def _failed_radiod(pattern):
    if pattern == check.RADIOD_UNITS:
        return svc('failed', None, enabled=True)
    return svc('inactive', None, enabled=False)


@pytest.mark.parametrize("fn,label", LINES)
def test_failed_radiod_still_fails(fn, label):             # Review Focus 4
    line = fn(_env(service=_failed_radiod, sdr_present=lambda: False))
    assert line.mark == check.FAIL and 'dormant' not in line.text


@pytest.mark.parametrize("fn,label", LINES)
def test_sdr_present_is_not_dormant(fn, label):            # Review Focus 4
    line = fn(_env(service=lambda p: svc('inactive', None, enabled=False),
                   sdr_present=lambda: True))
    assert line.mark == check.FAIL and 'dormant' not in line.text


def test_unknown_sdr_is_not_dormant():
    line = check.psk_line(make_env(service=lambda p: svc('inactive', None, enabled=False),
                                   sdr_present=lambda: None))
    assert line.mark == check.FAIL


def test_missing_probe_is_not_dormant():
    line = check.psk_line(make_env(service=lambda p: svc('inactive', None, enabled=False),
                                   sdr_present=None))
    assert line.mark == check.FAIL


def test_radiod_absent_unit_counts_as_dormant():
    def service(p):
        return svc('absent', None, enabled=False) if p == check.RADIOD_UNITS \
            else svc('inactive', None, enabled=False)
    assert check.radiod_dormant(make_env(service=service, sdr_present=lambda: False))


def test_radiod_not_installed_is_not_dormant():
    env = make_env(installed=lambda n: n != 'ka9q-radio', **DORMANT)
    assert not check.radiod_dormant(env)
    assert check.psk_line(env).mark == check.FAIL


def test_running_radiod_is_not_dormant():
    env = make_env(sdr_present=lambda: False,
                   service=lambda p: svc('active') if p == check.RADIOD_UNITS
                   else svc('inactive', None, enabled=False))
    assert not check.radiod_dormant(env)


def test_mag_line_ignores_radiod():
    env = make_env(**DORMANT)
    assert check.bringup_hold(env, radio=False) is None
    assert 'dormant radiod' not in check.mag_line(env).text


def test_receivers_line_waits_on_dormant_radiod():
    recs = [receivers.Receiver(reporter_id='AC0G/B1A', radiod='ac0g-b1-a-status.local',
                               clients=('psk-recorder', 'wspr-recorder'))]
    with mock.patch('sigmond.receivers.load_receivers', return_value=recs):
        line = check.receivers_line(make_env(**DORMANT))
        failing = check.receivers_line(make_env(service=_failed_radiod,
                                                sdr_present=lambda: False))
    assert line.mark == check.WAIT and line.text.endswith('; waits on dormant radiod')
    assert failing.mark == check.FAIL


def test_verdict_is_wait_exit_2():
    env = _env(**DORMANT)
    lines = check.run_checks(env, [('PSK', check.psk_line), ('WSPR', check.wspr_line),
                                   ('meteor scatter', check.meteor_line),
                                   ('GRAPE', check.grape_line)])
    assert len(lines) == 4
    assert check.verdict(lines) == 'WAIT'
    assert check.EXIT_CODES[check.verdict(lines)] == 2

"""`smd fleet update` names stations with open drift faults and keeps
rolling; `smd fleet status` shows release, faults and drift per station."""

import datetime
import json

from sigmond.fleet import (CURRENT_SENTINEL, Host, RunResult, fleet_status,
                           fleet_update, format_status, format_update)

PLAN = 'smd update'
VERSION = ('components (live):\n  sigmond   abc1234\n')
CHECK = 'smd check --json'
DRIFT = 'cat /var/lib/sigmond/drift.json'


def _ok(out=''):
    return RunResult(rc=0, out=out, err='')


def _check_doc(fault):
    mark = 'x' if fault else 'ok'
    text = ('1 fault, 0 drift; unit foo: differs.  fix: smd align --apply'
            if fault else 'nothing differs from its release')
    return json.dumps({'lines': [
        {'label': 'topology', 'mark': 'ok', 'text': 'fine'},
        {'label': 'drift', 'mark': 'FAIL' if fault else 'ok', 'text': text}],
        'verdict': 'FAIL' if fault else 'PASS', 'exit': 1 if fault else 0})


def _runner(script):
    def run(host, command):
        return script.get((host.name, command)) or RunResult(1, '', 'no')
    return run


def _fleet(*names):
    return {n: Host(name=n, reach=f'root@{n}', canary=(n == names[0]))
            for n in names}


def test_fleet_update_names_faults_and_continues():
    script = {}
    for n in ('b4', 'nd', 'sigma'):
        script[(n, 'true')] = _ok()
        script[(n, 'smd update')] = _ok(CURRENT_SENTINEL)
        script[(n, 'smd version')] = _ok(VERSION)
    script[('b4', CHECK)] = RunResult(1, _check_doc(True), '')
    script[('nd', CHECK)] = _ok(_check_doc(False))
    script[('sigma', CHECK)] = RunResult(1, _check_doc(True), '')
    results = fleet_update(_fleet('b4', 'nd', 'sigma'), _runner(script))
    text = format_update(results)
    assert 'open faults: b4, sigma' in text
    # never stops the roll: every host was still planned
    assert all(r.reachable and r.error is None for r in results)
    assert not any(r.halted for r in results)


def test_fleet_update_prints_no_fault_line_when_clean():
    script = {('a', 'true'): _ok(), ('a', 'smd update'): _ok(CURRENT_SENTINEL),
              ('a', 'smd version'): _ok(VERSION),
              ('a', CHECK): _ok(_check_doc(False))}
    text = format_update(fleet_update(_fleet('a'), _runner(script)))
    assert 'open faults' not in text


def test_fleet_status_shows_release_and_drift_counts():
    doc = {'release': '3.94', 'baseline': 'blessed',
           'items': [{'severity': 'fault'}, {'severity': 'drift'},
                     {'severity': 'drift'}]}
    script = {('b4', 'true'): _ok(), ('b4', 'smd version'): _ok(VERSION),
              ('b4', DRIFT): _ok(json.dumps(doc))}
    [st] = fleet_status(_fleet('b4'), _runner(script))
    assert (st.release, st.faults, st.drift_count) == ('3.94', 1, 2)
    out = format_status([st])
    assert 'release 3.94' in out and 'faults 1' in out and 'drift 2' in out


def test_fleet_status_without_a_report_says_so():
    script = {('b4', 'true'): _ok(), ('b4', 'smd version'): _ok(VERSION)}
    [st] = fleet_status(_fleet('b4'), _runner(script))
    assert st.faults is None
    assert 'no drift report' in format_status([st])


def _stamp(hours_ago):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        hours=hours_ago)
    return t.strftime('%Y-%m-%dT%H:%M:%SZ')


def _status_with(doc):
    script = {('b4', 'true'): _ok(), ('b4', 'smd version'): _ok(VERSION),
              ('b4', DRIFT): _ok(json.dumps(doc))}
    [st] = fleet_status(_fleet('b4'), _runner(script))
    return st


def test_a_stale_report_shows_its_age_not_counts():
    st = _status_with({'release': '3.94', 'generated_at': _stamp(120),
                       'items': []})
    assert st.faults is None and st.drift_stale_h >= 119
    out = format_status([st])
    assert 'stale' in out and 'faults 0' not in out


def test_a_fresh_report_shows_counts():
    st = _status_with({'release': '3.94', 'generated_at': _stamp(2),
                       'items': []})
    assert st.faults == 0 and st.drift_stale_h is None


def test_an_empty_object_is_no_report():
    st = _status_with({})
    assert st.faults is None
    assert 'no drift report' in format_status([st])
    st = _status_with({'release': '3.94'})
    assert st.faults is None


def test_fleet_update_lists_stations_whose_check_cannot_say():
    script = {}
    for n in ('a', 'b', 'c', 'd'):
        script[(n, 'true')] = _ok()
        script[(n, 'smd update')] = _ok(CURRENT_SENTINEL)
        script[(n, 'smd version')] = _ok(VERSION)
    script[('a', CHECK)] = RunResult(1, _check_doc(True), '')
    # b: older smd, no drift line
    script[('b', CHECK)] = _ok(json.dumps({'lines': [], 'verdict': 'PASS'}))
    # c: stale report, line is WAIT
    script[('c', CHECK)] = RunResult(2, json.dumps({'lines': [
        {'label': 'drift', 'mark': 'wait', 'text': 'stale'}]}), '')
    # d: check prints nothing
    text = format_update(fleet_update(_fleet('a', 'b', 'c', 'd'),
                                      _runner(script)))
    assert 'open faults: a' in text
    assert 'drift unknown: b, c, d' in text

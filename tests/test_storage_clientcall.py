import json
import subprocess
from types import SimpleNamespace

from sigmond.storage.clientcall import call_final, call_trim, call_holdings, stop_client


def _run(stdout="", rc=0, exc=None):
    def run(argv, **kw):
        if exc:
            raise exc
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr="")
    return run


def test_trim_answer_parses():
    a = call_trim("x", 5, run=_run(json.dumps({"held_bytes": 4, "bytes_by_class": {"raw": 4}, "refused": False, "reason": ""})))
    assert a.ok and a.held_bytes == 4


def test_holdings_parses():
    a = call_holdings("x", run=_run(json.dumps({"bytes_by_class": {"raw": 3, "derived": 4}, "estimated": False})))
    assert a.ok and a.held_bytes == 7 and a.bytes_by_class == {"raw": 3, "derived": 4}


def test_hang_garbage_and_old_clients_are_failed_answers():
    assert not call_trim("x", 5, run=_run(exc=subprocess.TimeoutExpired("x", 120))).ok
    assert not call_trim("x", 5, run=_run("not json")).ok
    assert not call_trim("x", 5, run=_run("[1]")).ok
    assert not call_trim("x", 5, run=_run(rc=1)).ok
    assert not call_trim("x", 5, run=_run(exc=FileNotFoundError("x"))).ok
    old = call_trim("x", 5, run=_run(rc=2))
    assert old.unsupported and not old.ok


def test_final_passes_the_directive():
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout='{"held_bytes":1,"bytes_by_class":{},"refused":false,"reason":""}', stderr="")
    call_final("x", 5, "stop-writing", run=run)
    assert seen[0][1:] == ["storage", "final", "--fit-bytes", "5", "--policy", "stop-writing", "--json"]
    call_final("x", 5, None, run=run)
    assert "--policy" not in seen[1]


def test_stop_never_touches_radiod():
    ok, why = stop_client("radiod", resolve=lambda c: [], run=_run())
    assert not ok and "radiod" in why


def test_stop_stops_service_and_timer_units_and_skips_targets():
    calls = []
    units = [SimpleNamespace(unit="a@x.service", kind="service"),
             SimpleNamespace(unit="a.target", kind="target"),
             SimpleNamespace(unit="a.timer", kind="timer")]

    def run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    assert stop_client("a", resolve=lambda c: units, run=run)[0]
    assert calls == [["systemctl", "stop", "a@x.service"], ["systemctl", "stop", "a.timer"]]


def test_stop_reports_failure():
    units = [SimpleNamespace(unit="a.service", kind="service")]
    ok, why = stop_client("a", resolve=lambda c: units, run=_run(rc=1))
    assert not ok and "a.service" in why


def test_stop_timeout_is_a_failed_answer_naming_units():
    units = [SimpleNamespace(unit="a1.service", kind="service"),
             SimpleNamespace(unit="a2.service", kind="service")]
    n = []

    def run(argv, **kw):
        n.append(argv)
        if len(n) == 2:
            raise subprocess.TimeoutExpired("systemctl", 60)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    ok, why = stop_client("a", resolve=lambda c: units, run=run)
    assert not ok and "a2.service" in why and "a1.service" in why


def test_stop_oserror_is_a_failed_answer():
    units = [SimpleNamespace(unit="a.service", kind="service")]
    ok, why = stop_client("a", resolve=lambda c: units, run=_run(exc=FileNotFoundError("systemctl")))
    assert not ok and "a.service" in why


def test_stop_unknown_component_is_a_failed_answer():
    def resolve(c):
        raise ValueError("unknown component")
    ok, why = stop_client("nope", resolve=resolve, run=_run())
    assert not ok and "nope" in why


def test_stop_with_no_units_succeeds_and_says_so():
    assert stop_client("a", resolve=lambda c: [], run=_run()) == (True, "no units to stop")

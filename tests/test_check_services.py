"""smd check: platform.services — restarts and memory kills (plan 2026-11-14, Task 3)."""
import json
import time
from datetime import datetime, timezone

import pytest

from sigmond.commands import check as C


def _doc(units=None, glob=None, age_s=0, error=None):
    t = datetime.fromtimestamp(time.time() - age_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"generated_at": t, "window_h": 24, "units": units or {}, "global_oom": glob or [],
            "error": error}


def _u(**kw):
    base = dict(auto_restarts=0, manual_restarts=0, memcg_oom=0, unexpected_exits=0,
                last_utc="2026-11-14T10:00:00Z", peak_bytes=None, max_bytes=None, peak_pct=None)
    base.update(kw)
    return base


@pytest.fixture
def status(tmp_path, monkeypatch):
    p = tmp_path / "service-events.json"
    monkeypatch.setattr(C, "SERVICE_EVENTS", p)
    return lambda doc: p.write_text(json.dumps(doc))


def test_no_events_ok_names_manual_count(status):
    status(_doc({"a.service": _u(manual_restarts=2)}))
    ln = C.services_line(None)
    assert ln.mark == C.OK and ln.label == "services"
    assert "no restarts or memory kills in 24 h" in ln.text and "2 manual restarts" in ln.text


def test_global_oom_fails(status):
    status(_doc(glob=[{"utc": "2026-11-14T10:00:00Z", "unit": "sshd.service", "task": "sshd"},
                      {"utc": "2026-11-14T10:00:01Z", "unit": "radiod@X.service", "task": "radiod"}]))
    ln = C.services_line(None)
    assert ln.mark == C.FAIL and "the machine ran out of memory" in ln.text
    assert "sshd.service" in ln.text and "radiod@X.service" in ln.text


def test_memcg_kill_waits(status):
    status(_doc({"psk-recorder@A.service": _u(memcg_oom=2, auto_restarts=2)}))
    ln = C.services_line(None)
    assert ln.mark == C.WAIT and "psk-recorder@A.service" in ln.text and "2 memory kill" in ln.text


def test_restart_storm_waits(status):
    status(_doc({"wspr-recorder@A.service": _u(auto_restarts=4)}))
    ln = C.services_line(None)
    assert ln.mark == C.WAIT and "4 automatic restarts" in ln.text


def test_three_restarts_ok(status):
    status(_doc({"wspr-recorder@A.service": _u(auto_restarts=3)}))
    assert C.services_line(None).mark == C.OK


def test_peak_over_85_waits(status):
    status(_doc({"psk-recorder@A.service": _u(peak_bytes=2_900_000_000,
                                              max_bytes=3_221_225_472, peak_pct=90.0)}))
    ln = C.services_line(None)
    assert ln.mark == C.WAIT and "2766 of 3072 MB" in ln.text


def test_stale_or_missing_waits(status, tmp_path):
    assert C.services_line(None).mark == C.WAIT          # missing
    status(_doc(age_s=3600))
    ln = C.services_line(None)
    assert ln.mark == C.WAIT and "not collected" in ln.text


def test_collect_error_waits(status):
    status(_doc(error="TimeoutExpired: journalctl"))
    ln = C.services_line(None)
    assert ln.mark == C.WAIT and "could not be read" in ln.text


def test_text_ascii(status):
    status(_doc({"xé.service": _u(memcg_oom=1)}))
    assert C.services_line(None).text.isascii()


def test_registered():
    assert ("services", C.services_line) in C.CHECKS
    assert C.CHECK_META["services"][0] == "platform.services"

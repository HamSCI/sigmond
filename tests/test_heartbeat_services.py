"""heartbeat: the resources block carries services (plan 2026-11-14, Task 4)."""
import json
import time
from datetime import datetime, timezone

from sigmond import heartbeat as HB


def _raw(**kw):
    return {"llc": {"available": True}, "irqs": {}, **kw}


def _svc(units=None, glob=None):
    return {"generated_at": "2026-11-14T10:00:00Z", "units": units or {},
            "global_oom": glob or [], "error": None}


def _u(**kw):
    base = dict(auto_restarts=0, manual_restarts=0, memcg_oom=0, unexpected_exits=0,
                last_utc=None, peak_bytes=None, max_bytes=None, peak_pct=None)
    base.update(kw)
    return base


def test_fresh_status_in_block():
    block = HB._map_resources(_raw(services=_svc({"a.service": _u(manual_restarts=1)})))
    assert block["verdict"] == "VALID"
    assert block["data"]["services"]["units"]["a.service"]["manual_restarts"] == 1


def test_global_oom_invalid():
    b = HB._map_resources(_raw(services=_svc(glob=[{"utc": "x", "unit": "sshd.service", "task": "sshd"}])))
    assert b["verdict"] == "INVALID" and "out of memory" in b["reason"]


def test_memcg_inconclusive():
    b = HB._map_resources(_raw(services=_svc({"p.service": _u(memcg_oom=1)})))
    assert b["verdict"] == "INCONCLUSIVE" and "p.service" in b["reason"]


def test_missing_indeterminate():
    b = HB._map_resources(_raw(services=None))
    assert b["verdict"] == "INDETERMINATE"


def test_absent_key_adds_nothing():
    assert HB._map_resources(_raw())["verdict"] == "VALID"


def test_reader_reads_fresh_file(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    now = time.time()
    t = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    p.write_text(json.dumps({**_svc(), "generated_at": t}))
    assert HB._read_service_events(path=p, now=now)["units"] == {}
    assert HB._read_service_events(path=p, now=now + 3600) is None


def test_collect_error_indeterminate():
    doc = {**_svc(), "error": "TimeoutExpired: journalctl"}
    b = HB._map_resources(_raw(services=doc))
    assert b["verdict"] == "INDETERMINATE" and "could not be read" in b["reason"]

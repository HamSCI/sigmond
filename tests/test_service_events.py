"""service_events: restarts and OOM kills per unit from the journal (plan 2026-11-14)."""
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from sigmond import service_events as S

NOW = 1794528000.0          # 2026-11-13T00:00:00Z
PSK = "psk-recorder@AI6VN-1.service"

RESTART_SCHEDULED = "5eb03494b6584870a536b337290809b3"
STOPPING = "de5b426a63be47a7b6ac3eaac82e2f6f"
EXITED = "98e322203f7a4ed290d09fe03c09fe15"
FAILED = "d9b373ed55a64feb8242e02dbe79a49c"


def _ts(ago_s):
    return str(int((NOW - ago_s) * 1e6))


def pid1(ago_s, msg_id, unit, **kw):
    d = {"__REALTIME_TIMESTAMP": _ts(ago_s), "_PID": "1", "_TRANSPORT": "journal",
         "MESSAGE_ID": msg_id, "UNIT": unit, "MESSAGE": f"{unit}: event"}
    d.update(kw)
    return json.dumps(d)


def kernel_oom(ago_s, memcg, task="python3", constraint="CONSTRAINT_MEMCG"):
    return json.dumps({
        "__REALTIME_TIMESTAMP": _ts(ago_s), "_TRANSPORT": "kernel",
        "MESSAGE": (f"oom-kill:constraint={constraint},nodemask=(null),cpuset=/,mems_allowed=0,"
                    f"oom_memcg={memcg},task_memcg={memcg},task={task},pid=1234,uid=999")})


def memcg_kill(ago=3600):
    return [kernel_oom(ago, f"/system.slice/{PSK}"),
            pid1(ago - 1, EXITED, PSK, EXIT_CODE="killed", EXIT_STATUS="9"),
            pid1(ago - 1, FAILED, PSK, UNIT_RESULT="oom-kill"),
            pid1(ago - 2, RESTART_SCHEDULED, PSK, N_RESTARTS="1")]


def test_memcg_kill_counted():
    units, glob = S.parse_events(memcg_kill(), NOW)
    u = units[PSK]
    assert (u.memcg_oom, u.auto_restarts, u.unexpected_exits) == (1, 1, 1)
    assert glob == []
    assert u.last_utc == "2026-11-12T23:00:02Z"   # the restart, 2 s after the kill


def test_global_oom_listed():
    lines = [kernel_oom(7200, "/system.slice/sshd.service", "sshd", "CONSTRAINT_NONE"),
             kernel_oom(7100, "/system.slice/radiod@AI6VN-1.service", "radiod", "CONSTRAINT_NONE"),
             kernel_oom(7000, "/", "kworker", "CONSTRAINT_NONE")]
    units, glob = S.parse_events(lines, NOW)
    assert [g["unit"] for g in glob] == ["sshd.service", "radiod@AI6VN-1.service", "-"]
    assert all(u.memcg_oom == 0 for u in units.values())


def test_manual_vs_automatic():
    lines = [pid1(5000, STOPPING, PSK, JOB_TYPE="restart"),                 # by hand
             pid1(4000, EXITED, PSK, EXIT_CODE="exited", EXIT_STATUS="1"),    # crash
             pid1(3999, RESTART_SCHEDULED, PSK, N_RESTARTS="1"),
             pid1(3998, STOPPING, PSK, JOB_TYPE="restart")]                  # the auto restart's job
    units, _ = S.parse_events(lines, NOW)
    u = units[PSK]
    assert (u.manual_restarts, u.auto_restarts, u.unexpected_exits) == (1, 1, 1)


def test_clean_stop_is_not_unexpected():
    lines = [pid1(5000, STOPPING, PSK, JOB_TYPE="stop"),
             pid1(4999, EXITED, PSK, EXIT_CODE="killed", EXIT_STATUS="15")]
    units, _ = S.parse_events(lines, NOW)
    assert units[PSK].unexpected_exits == 0 and units[PSK].manual_restarts == 1


def test_window():
    units, glob = S.parse_events(memcg_kill(ago=25 * 3600), NOW)
    assert PSK not in units or units[PSK].memcg_oom == 0
    assert glob == []


def test_kill_outside_system_slice():
    _, glob = S.parse_events([kernel_oom(60, "/user.slice/user-1000.slice", "bash",
                                         "CONSTRAINT_NONE")], NOW)
    assert glob[0]["unit"] == "-"
    units, _ = S.parse_events([kernel_oom(60, "/user.slice/user-1000.slice")], NOW)
    assert "-" in units and units["-"].memcg_oom == 1


def test_template_units_kept_apart():
    a, b = "psk-recorder@KFS\\x3dOMNI.service", "psk-recorder@KFS-SW.service"
    lines = [pid1(60, RESTART_SCHEDULED, a), pid1(50, RESTART_SCHEDULED, b),
             pid1(40, RESTART_SCHEDULED, b)]
    units, _ = S.parse_events(lines, NOW)
    assert (units[a].auto_restarts, units[b].auto_restarts) == (1, 2)


def test_bad_lines_ignored():
    lines = ["not json", "{}", json.dumps({"_TRANSPORT": "kernel"}),
             json.dumps({"__REALTIME_TIMESTAMP": "x", "MESSAGE_ID": RESTART_SCHEDULED})]
    units, glob = S.parse_events(lines + memcg_kill(), NOW)
    assert units[PSK].memcg_oom == 1


# ---------------------------------------------------------------- Task 2

def _cg(tmp_path, unit, peak, mx):
    d = tmp_path / unit
    d.mkdir(parents=True)
    (d / "memory.peak").write_text(f"{peak}\n")
    (d / "memory.max").write_text(f"{mx}\n")


def test_headroom_reads_peak_and_max(tmp_path):
    _cg(tmp_path, PSK, 2_900_000_000, 3_221_225_472)
    h = S.memory_headroom([PSK], cgroup_root=tmp_path)
    assert h[PSK] == (2_900_000_000, 3_221_225_472)


def test_no_cap_no_headroom(tmp_path):
    _cg(tmp_path, PSK, 10**9, "max")
    assert S.memory_headroom([PSK], cgroup_root=tmp_path)[PSK] == (10**9, None)
    doc = S.collect(NOW, run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""),
                    cgroup_root=tmp_path, units=[PSK])
    assert doc["units"][PSK]["peak_pct"] is None


def test_collect_counts_and_pct(tmp_path):
    _cg(tmp_path, PSK, 2_900_000_000, 3_221_225_472)
    out = "\n".join(memcg_kill())
    doc = S.collect(NOW, run=lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""),
                    cgroup_root=tmp_path, units=[PSK])
    u = doc["units"][PSK]
    assert u["memcg_oom"] == 1 and round(u["peak_pct"]) == 90
    assert doc["generated_at"] == "2026-11-13T00:00:00Z" and doc["error"] is None


def test_collect_journal_error(tmp_path):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("journalctl", 60)
    doc = S.collect(NOW, run=boom, cgroup_root=tmp_path, units=[])
    assert doc["error"] and doc["units"] == {} and doc["global_oom"] == []


def test_status_written_atomically_0644(tmp_path):
    p = tmp_path / "run" / "service-events.json"
    S.write_status({"generated_at": "x", "units": {}}, path=p)
    assert json.loads(p.read_text())["generated_at"] == "x"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o644
    assert not list(p.parent.glob(".*tmp*"))


# ---------------------------------------------------------------- Task 3 (smd status)

def test_status_lists_only_eventful_units():
    doc = {"generated_at": "2026-11-14T10:00:00Z", "error": None, "global_oom": [
               {"utc": "2026-11-14T09:00:00Z", "unit": "sshd.service", "task": "sshd"}],
           "units": {
               "quiet.service": {"auto_restarts": 0, "manual_restarts": 0, "memcg_oom": 0,
                                 "unexpected_exits": 0, "last_utc": None, "peak_bytes": 10**8,
                                 "max_bytes": 10**9, "peak_pct": 10.0},
               "psk-recorder@A.service": {"auto_restarts": 2, "manual_restarts": 1, "memcg_oom": 1,
                                          "unexpected_exits": 1, "last_utc": "2026-11-14T09:30:00Z",
                                          "peak_bytes": 2_900_000_000, "max_bytes": 3_221_225_472,
                                          "peak_pct": 90.0}}}
    out = "\n".join(S.status_lines(doc))
    assert "psk-recorder@A.service" in out and "quiet.service" not in out
    assert "whole-machine" in out and "sshd.service" in out
    assert S.status_lines({"generated_at": "x", "units": {}, "global_oom": [], "error": None}) == []


# ---------------------------------------------------------------- final-review fixes

WSPR = "wspr-recorder@AI6VN-1.service"
WSPR_CG = "/system.slice/system-wspr\\x2drecorder.slice/" + WSPR


def test_template_memcg_maps_to_instance():
    units, glob = S.parse_events([kernel_oom(60, WSPR_CG)], NOW)
    assert units[WSPR].memcg_oom == 1 and "system-wspr\\x2drecorder.slice" not in units


def test_headroom_finds_template_instances(tmp_path):
    d = tmp_path / "system-wspr\\x2drecorder.slice" / WSPR
    d.mkdir(parents=True)
    (d / "memory.peak").write_text("3500000000\n")
    (d / "memory.max").write_text("4294967296\n")
    assert S.memory_headroom([WSPR], cgroup_root=tmp_path)[WSPR] == (3_500_000_000, 4_294_967_296)
    doc = S.collect(NOW, run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""),
                    cgroup_root=tmp_path)
    assert WSPR in doc["units"] and round(doc["units"][WSPR]["peak_pct"]) == 81


def test_journal_filtered_at_source(tmp_path):
    seen = {}

    def run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "", "")
    S.collect(NOW, run=run, cgroup_root=tmp_path, units=[])
    a = seen["argv"]
    for mid in (S.MSG_RESTART_SCHEDULED, S.MSG_STOPPING, S.MSG_EXITED):
        assert f"MESSAGE_ID={mid}" in a
    assert "_TRANSPORT=kernel" in a and any(x.startswith("--output-fields=") for x in a)

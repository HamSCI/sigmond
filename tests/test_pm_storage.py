"""Tests for scripts/proxmox/pm-storage.py: the host's pool report, snapshot
lifetimes and host alarm.

pm-storage.py runs as root on a Proxmox host and DELETES snapshots, which
cannot be undone.  So most of these tests pin down which snapshots it may
delete: only those whose description carries a well-formed
``sigmond-snapshot expires=<ISO Z>`` tag, never ``pristine``, never an
operator's.  The module has a dash in its name and imports nothing from
sigmond, so it loads by path, as tests/test_pm_heartbeat.py loads its sibling.
No test runs qm, pvesh or lvs: every host call goes through a fake bundle.
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROXMOX = REPO / "scripts" / "proxmox"

if str(REPO / "lib") not in sys.path:
    sys.path.insert(0, str(REPO / "lib"))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PROXMOX / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pm = _load("pm_storage", "pm-storage.py")
hb = _load("pm_heartbeat_for_storage", "pm-heartbeat.py")

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
GB = 10**9


def _lvs(pct, size=1000 * GB, meta=10.0, vg="pve", pool="data"):
    return json.dumps({"report": [{"lv": [
        {"lv_name": "root", "vg_name": vg, "lv_size": str(100 * GB), "data_percent": "",
         "metadata_percent": "", "lv_attr": "-wi-ao----"},
        {"lv_name": pool, "vg_name": vg, "lv_size": str(size), "data_percent": f"{pct:.2f}",
         "metadata_percent": f"{meta:.2f}", "lv_attr": "twi-aotz--"}]}]})


def _pool(pct, size=1000 * GB):
    return pm.read_pool(_lvs(pct, size), "pve", "data")


# ---------------------------------------------------------------------------
# the brief's tests
# ---------------------------------------------------------------------------

def test_levels_match_the_ledger():
    from sigmond.storage.model import Levels
    L = Levels()
    assert pm.LEVELS == {"watch": L.watch, "tighten": L.tighten, "demand": L.demand, "final": L.final}


def test_storage_cfg_maps_local_lvm():
    m = pm.parse_storage_cfg("lvmthin: local-lvm\n\tthinpool data\n\tvgname pve\n\tcontent rootdir,images\n")
    assert m == {"local-lvm": ("pve", "data")}
    assert pm.pool_for_vm("scsi0: local-lvm:vm-100-disk-0,size=255G\n", m) == ("pve", "data")


def test_metadata_counts():
    lvs = json.dumps({"report": [{"lv": [{"lv_name": "data", "vg_name": "pve", "lv_size": "100", "data_percent": "40.0",
                                           "metadata_percent": "96.0", "lv_attr": "twi-aotz--"}]}]})
    assert any("96" in a for a in pm.host_alarms(pm.read_pool(lvs, "pve", "data"), [], NOW))


def test_only_labelled_expired_snapshots_reap():
    snaps = pm.parse_snapshots(json.dumps([
        {"name": "current"},
        {"name": "pristine", "snaptime": 1, "description": ""},
        {"name": "pre-v385", "snaptime": 1, "description": "operator"},
        {"name": "align-1", "snaptime": 1, "description": "sigmond-snapshot expires=2026-11-01T00:00:00Z"},
        {"name": "align-2", "snaptime": 1, "description": "sigmond-snapshot expires=2026-12-01T00:00:00Z"}]), 100)
    assert [s["name"] for s in pm.expired(snaps, NOW)] == ["align-1"]


def test_old_operator_snapshot_alarms_but_never_reaps():
    # 500 GB free at 10 GB/h: a sigmond snapshot would live 25 h; this one is 47.6 h old.
    snaps = [{"vmid": 100, "name": "pre-v385", "taken": "2026-11-02T12:25:00Z", "sigmond": False, "expires": None}]
    assert pm.expired(snaps, NOW) == []
    assert pm.expired(snaps, NOW, pool_pct=99.0) == []
    assert any("pre-v385" in a for a in pm.host_alarms(_pool(50), snaps, NOW, churn=10 * GB))


def test_guest_report_is_ascii_and_matches_the_guest_reader(tmp_path):
    from sigmond.storage.measure import read_host_report
    text = pm.guest_report(100, _pool(50), [100, 101], [], ["pool 50 % — fine"], NOW)
    assert all(ord(c) < 128 for c in text)
    p = tmp_path / "h.json"
    p.write_text(text)
    rep, fault = read_host_report(str(p), NOW)
    assert fault is None and rep.vms_on_pool == (100, 101)


def test_lifetime_follows_the_pools_time_to_full():
    # 11-03: ~120 GB free, ~3.7 GB/h churn -> ~32 h to full -> ~16 h life
    assert pm.snapshot_life_h(120 * GB, 3.7 * GB) == pytest.approx(16.2, abs=0.2)
    assert pm.snapshot_life_h(500 * GB, 1 * GB) == 48          # the cap
    assert pm.snapshot_life_h(120 * GB, None) == 48             # no churn measured yet


def test_churn_from_pool_history():
    h = [(0, 100e9), (3600, 103.7e9), (7200, 107.4e9)]
    assert pm.churn_bytes_per_h(h) == pytest.approx(3.7e9, rel=0.01)
    assert pm.churn_bytes_per_h([(0, 1e9)]) is None
    assert pm.churn_bytes_per_h([(0, 5e9), (3600, 4e9)]) == 0


def test_pressure_reaps_labelled_snapshots_early_but_never_operators():
    snaps = [{"vmid": 100, "name": "align-2", "sigmond": True, "expires": "2026-12-01T00:00:00Z"},
             {"vmid": 100, "name": "pre-v385", "sigmond": False, "expires": None},
             {"vmid": 100, "name": "pristine", "sigmond": False, "expires": None}]
    assert pm.expired(snaps, NOW, pool_pct=89.9) == []
    assert [s["name"] for s in pm.expired(snaps, NOW, pool_pct=90.0)] == ["align-2"]


def test_snapshot_refused_on_a_full_pool(capsys):
    host = _fake_host(pool_pct=72)
    assert pm.main(["snapshot", "100", "x"], host) == 3
    assert host.calls_of("snapshot") == []
    assert "70" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# the tag: which snapshots count as sigmond's
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("desc", [
    "sigmond-snapshot expires=2026-11-01T00:00:00",      # naive: no offset
    "sigmond-snapshot expires=soon",                      # unparseable
    "sigmond-snapshot expires=",                          # empty
    "sigmond-snapshot expires=2026-11-01T00:00:00Z sigmond-snapshot expires=2026-11-02T00:00:00Z",
])
def test_a_malformed_tag_is_an_operator_snapshot_never_reaped(desc):
    snaps = pm.parse_snapshots(json.dumps([
        {"name": "align-x", "snaptime": int((NOW - timedelta(hours=1)).timestamp()), "description": desc}]), 100)
    assert snaps[0]["sigmond"] is False and snaps[0]["expires"] is None
    assert pm.expired(snaps, NOW) == []
    assert pm.expired(snaps, NOW, pool_pct=99.0) == []


def test_a_malformed_tag_alarms_at_once_even_while_young():
    snaps = pm.parse_snapshots(json.dumps([
        {"name": "align-x", "snaptime": int((NOW - timedelta(hours=1)).timestamp()),
         "description": "sigmond-snapshot expires=2026-11-01T00:00:00"}]), 100)
    alarms = pm.host_alarms(_pool(50), snaps, NOW)
    assert any("align-x" in a for a in alarms)


@pytest.mark.parametrize("desc", [
    "foosigmond-snapshot expires=2026-11-01T00:00:00Z",
    "x-sigmond-snapshot expires=2026-11-01T00:00:00Z",
])
def test_a_tag_glued_to_another_word_is_no_label(desc):
    (snap,) = pm.parse_snapshots(json.dumps([{"name": "a", "snaptime": 1, "description": desc}]), 100)
    assert snap["sigmond"] is False and snap["expires"] is None and snap["label_fault"] is None
    assert pm.expired([snap], NOW, pool_pct=99.0) == []


def test_a_well_formed_tag_parses_with_offset():
    snaps = pm.parse_snapshots(json.dumps([
        {"name": "a", "snaptime": 1, "description": "sigmond-snapshot expires=2026-11-05T00:00:00+00:00\n"},
        {"name": "b", "snaptime": 1, "description": "note\nsigmond-snapshot expires=2026-11-05T00:00:00Z"}]), 7)
    assert [(s["vmid"], s["sigmond"], s["expires"]) for s in snaps] == [
        (7, True, "2026-11-05T00:00:00Z"), (7, True, "2026-11-05T00:00:00Z")]


def test_pristine_is_never_reaped_even_when_labelled():
    snaps = [{"vmid": 100, "name": "pristine", "sigmond": True, "expires": "2026-11-01T00:00:00Z"}]
    assert pm.expired(snaps, NOW) == []
    assert pm.expired(snaps, NOW, pool_pct=99.0) == []


@pytest.mark.parametrize("expires", [None, "", "garbage", "2026-11-01T00:00:00"])
def test_expired_refuses_a_labelled_entry_without_a_valid_expiry(expires):
    snaps = [{"vmid": 100, "name": "align-1", "sigmond": True, "expires": expires}]
    assert pm.expired(snaps, NOW) == []
    assert pm.expired(snaps, NOW, pool_pct=99.0) == []


def test_expiry_at_now_exactly_is_not_yet_past():
    snaps = [{"vmid": 100, "name": "a", "sigmond": True, "expires": "2026-11-04T12:00:00Z"}]
    assert pm.expired(snaps, NOW) == []
    assert len(pm.expired(snaps, NOW + timedelta(seconds=1))) == 1


# ---------------------------------------------------------------------------
# host alarms
# ---------------------------------------------------------------------------

def test_pool_below_watch_raises_nothing():
    assert pm.host_alarms(_pool(79.9), [], NOW) == []


@pytest.mark.parametrize("pct,stage", [(80, "watch"), (85, "tighten"), (90, "demand"), (95, "final")])
def test_pool_stage_named(pct, stage):
    alarms = pm.host_alarms(_pool(pct), [], NOW)
    assert len(alarms) == 1 and stage in alarms[0] and "pve/data" in alarms[0]


def test_young_operator_snapshot_is_quiet():
    snaps = [{"vmid": 100, "name": "pre-v385", "taken": "2026-11-03T13:00:00Z", "sigmond": False, "expires": None}]
    assert pm.host_alarms(_pool(50), snaps, NOW) == []


def test_operator_snapshot_alarm_names_its_age():
    snaps = [{"vmid": 100, "name": "pre-v385", "taken": "2026-11-02T12:00:00Z", "sigmond": False, "expires": None}]
    (alarm,) = pm.host_alarms(_pool(50), snaps, NOW)
    assert "48" in alarm and "100" in alarm


def test_young_operator_snapshot_below_watch_stays_quiet_with_churn():
    # life 25 h (500 GB free at 10 GB/h); 24 h old, pool at 50 %
    snaps = [{"vmid": 100, "name": "pre-v385", "taken": "2026-11-03T12:00:00Z", "sigmond": False, "expires": None}]
    assert pm.host_alarms(_pool(50), snaps, NOW, churn=10 * GB) == []
    snaps[0]["taken"] = "2026-11-03T10:30:00Z"                      # 25.5 h: past the life
    assert any("pre-v385" in a for a in pm.host_alarms(_pool(50), snaps, NOW, churn=10 * GB))


def test_any_operator_snapshot_alarms_once_the_pool_reaches_watch():
    snaps = [{"vmid": 100, "name": "pre-v385", "taken": "2026-11-04T11:00:00Z", "sigmond": False, "expires": None}]
    assert pm.host_alarms(_pool(79.9), snaps, NOW) == []
    alarms = pm.host_alarms(_pool(80), snaps, NOW)
    assert any("pre-v385" in a for a in alarms)


def test_operator_snapshot_of_unknown_age_alarms():
    snaps = [{"vmid": 100, "name": "pre-v385", "taken": None, "sigmond": False, "expires": None}]
    assert any("unknown age" in a for a in pm.host_alarms(_pool(50), snaps, NOW))


def test_pristine_alarms_only_at_watch_or_above():
    snaps = [{"vmid": 100, "name": "pristine", "taken": "2026-01-01T00:00:00Z", "sigmond": False, "expires": None}]
    assert pm.host_alarms(_pool(79), snaps, NOW) == []
    assert any("pristine" in a for a in pm.host_alarms(_pool(80), snaps, NOW))


def test_labelled_snapshot_does_not_alarm_as_operator():
    snaps = [{"vmid": 100, "name": "align-1", "taken": "2026-10-01T00:00:00Z", "sigmond": True,
              "expires": "2026-12-01T00:00:00Z"}]
    assert pm.host_alarms(_pool(50), snaps, NOW) == []


# ---------------------------------------------------------------------------
# lifetime
# ---------------------------------------------------------------------------

def test_lifetime_floor_is_one_hour():
    assert pm.snapshot_life_h(2 * GB, 1 * GB) == 1.0
    assert pm.snapshot_life_h(120 * GB, 0) == 48


def test_churn_ignores_a_short_history():
    assert pm.churn_bytes_per_h([(0, 1e9), (1800, 2e9)]) is None


# ---------------------------------------------------------------------------
# the fake host
# ---------------------------------------------------------------------------

STORAGE_CFG = ("dir: local\n\tpath /var/lib/vz\n\tcontent iso\n\n"
               "lvmthin: local-lvm\n\tthinpool data\n\tvgname pve\n\tcontent rootdir,images\n")


class FakeHost:
    def __init__(self, pool_pct=50.0, size=1000 * GB, vms=None, snaps=None, state=None,
                 fail_write=(), now=NOW, write_cost=0.0, recover_on_reset=(), fail_del=()):
        self.calls = []
        self.pool_pct = pool_pct
        self.size = size
        self.vms = vms if vms is not None else [{"vmid": 100, "status": "running"},
                                                 {"vmid": 101, "status": "running"}]
        self.snaps = snaps or {}
        self.state = state or {}
        self.fail_write = set(fail_write)
        self._now = now
        self.logged = []
        self.mono = 0.0
        self.write_cost = write_cost
        self.recover_on_reset = set(recover_on_reset)
        self.fail_del = set(fail_del)
        self.stop_after_reaps = None
        self.storage_text = STORAGE_CFG
        self.pool_of = {}            # vmid -> storage id (default local-lvm)
        self.extra_lvs = []          # extra lv rows

    def calls_of(self, kind):
        return [c for c in self.calls if c[0] == kind]

    # the bundle
    def now(self):
        return self._now

    def node(self):
        return "pve"

    def storage_cfg(self):
        if isinstance(self.storage_text, Exception):
            raise self.storage_text
        return self.storage_text

    def lvs(self):
        rep = json.loads(_lvs(self.pool_pct, self.size))
        rep["report"][0]["lv"] += self.extra_lvs
        return json.dumps(rep)

    def monotonic(self):
        return self.mono

    def reset_agent(self, vmid):
        self.calls.append(("reset", vmid))
        if vmid in self.recover_on_reset:
            self.fail_write.discard(vmid)
            return True
        return False

    def stop_requested(self):
        return (self.stop_after_reaps is not None
                and len(self.calls_of("delsnapshot")) >= self.stop_after_reaps)

    def list_vms(self):
        return json.dumps(self.vms)

    def qm_config(self, vmid):
        sid = self.pool_of.get(vmid, "local-lvm")
        return f"boot: order=scsi0\nscsi0: {sid}:vm-{vmid}-disk-0,size=255G\n"

    def list_snapshots(self, vmid):
        return json.dumps([{"name": "current"}] + self.snaps.get(vmid, []))

    def delsnapshot(self, vmid, name):
        self.calls.append(("delsnapshot", vmid, name))
        if (vmid, name) in self.fail_del:
            return pm.RunResult(2, "", "snapshot is locked")
        return pm.RunResult(0, "", "")

    def snapshot(self, vmid, name, description):
        self.calls.append(("snapshot", vmid, name, description))
        return pm.RunResult(0, "", "")

    def file_write(self, vmid, path, content):
        self.calls.append(("file_write", vmid, path, content))
        self.mono += self.write_cost
        return pm.RunResult(1 if vmid in self.fail_write else 0, "", "agent not running" if vmid in self.fail_write else "")

    def read_state(self):
        return json.loads(json.dumps(self.state))

    def write_state(self, state):
        self.calls.append(("write_state",))
        self.state = json.loads(json.dumps(state))

    def log(self, line):
        self.logged.append(line)


def _fake_host(**kw):
    return FakeHost(**kw)


def _history(rate_gb_h, pct, size=1000 * GB, hours=3):
    """State with a pool history ending at the current fill, growing at rate."""
    used_now = size * pct / 100
    end = NOW.timestamp() - 300
    pts = [[end - h * 3600, used_now - rate_gb_h * GB * h] for h in range(hours, -1, -1)]
    return {"pools": {"pve/data": {"history": pts}}}


# ---------------------------------------------------------------------------
# snapshot subcommand
# ---------------------------------------------------------------------------

def test_snapshot_labels_with_the_measured_lifetime(capsys):
    # 880 GB used of 1000 would refuse; use 60 %: 400 GB free at 10 GB/h -> 40 h to full -> 20 h life
    host = _fake_host(pool_pct=60, state=_history(10, 60))
    assert pm.main(["snapshot", "100", "align-1"], host) == 0
    ((_, vmid, name, desc),) = host.calls_of("snapshot")
    assert (vmid, name) == (100, "align-1")
    snaps = pm.parse_snapshots(json.dumps([{"name": "align-1", "snaptime": 1, "description": desc}]), 100)
    expires = datetime.fromisoformat(snaps[0]["expires"].replace("Z", "+00:00"))
    assert snaps[0]["sigmond"] is True
    assert (expires - NOW).total_seconds() / 3600 == pytest.approx(20, abs=0.3)
    out = capsys.readouterr().out
    assert "20" in out and "full" in out


def test_snapshot_without_history_gets_the_cap():
    host = _fake_host(pool_pct=50)
    assert pm.main(["snapshot", "100", "a"], host) == 0
    desc = host.calls_of("snapshot")[0][3]
    assert desc == "sigmond-snapshot expires=2026-11-06T12:00:00Z"


def test_snapshot_hours_shortens_but_never_lengthens():
    host = _fake_host(pool_pct=50)
    assert pm.main(["snapshot", "100", "a", "--hours", "6"], host) == 0
    assert host.calls_of("snapshot")[0][3].endswith("2026-11-04T18:00:00Z")
    host = _fake_host(pool_pct=50)
    assert pm.main(["snapshot", "100", "a", "--hours", "100"], host) == 0
    assert host.calls_of("snapshot")[0][3].endswith("2026-11-06T12:00:00Z")


def test_snapshot_refused_when_the_pool_fills_within_two_hours(capsys):
    # 40 % free of 100 GB = 40 GB at 25 GB/h -> 1.6 h to full
    host = _fake_host(pool_pct=60, size=100 * GB, state=_history(25, 60, size=100 * GB))
    assert pm.main(["snapshot", "100", "a"], host) == 3
    assert host.calls_of("snapshot") == []
    assert "2 h" in capsys.readouterr().err


def test_snapshot_refused_when_metadata_is_full():
    host = _fake_host(pool_pct=10)
    host.lvs = lambda: _lvs(10, meta=75.0)
    assert pm.main(["snapshot", "100", "a"], host) == 3


def test_snapshot_refused_when_the_pool_is_unknown():
    host = _fake_host()
    host.qm_config = lambda vmid: "scsi0: other:vm-100-disk-0,size=1G\n"
    assert pm.main(["snapshot", "100", "a"], host) == 3
    assert host.calls_of("snapshot") == []


# ---------------------------------------------------------------------------
# report subcommand
# ---------------------------------------------------------------------------

def _snap(name, desc, hours_ago=1):
    return {"name": name, "snaptime": int((NOW - timedelta(hours=hours_ago)).timestamp()), "description": desc}


def test_report_reaps_only_expired_labelled_snapshots():
    host = _fake_host(snaps={100: [
        _snap("pristine", "", 500),
        _snap("pre-v385", "operator", 50),
        _snap("align-1", "sigmond-snapshot expires=2026-11-04T11:00:00Z"),
        _snap("align-2", "sigmond-snapshot expires=2026-11-04T13:00:00Z"),
        _snap("align-3", "sigmond-snapshot expires=2026-11-04T11:00:00")]})
    assert pm.main(["report"], host) == 0
    assert host.calls_of("delsnapshot") == [("delsnapshot", 100, "align-1")]


def test_report_reaps_every_labelled_snapshot_at_demand():
    host = _fake_host(pool_pct=91, snaps={101: [
        _snap("pristine", "sigmond-snapshot expires=2026-11-01T00:00:00Z", 500),
        _snap("pre-v385", "operator", 50),
        _snap("align-2", "sigmond-snapshot expires=2026-11-05T13:00:00Z")]})
    pm.main(["report"], host)
    assert host.calls_of("delsnapshot") == [("delsnapshot", 101, "align-2")]


def test_report_writes_each_running_guest_an_ascii_report(tmp_path):
    from sigmond.storage.measure import read_host_report
    host = _fake_host(vms=[{"vmid": 100, "status": "running"}, {"vmid": 101, "status": "stopped"}],
                      snaps={100: [_snap("pre-v385", "operator — v3.85", 50)]})
    assert pm.main(["report"], host) == 0
    writes = host.calls_of("file_write")
    assert [w[1] for w in writes] == [100]
    _, _, path, content = writes[0]
    assert path == "/run/sigmond/host-pool.json"
    assert all(ord(c) < 128 for c in content)
    p = tmp_path / "h.json"
    p.write_text(content)
    rep, fault = read_host_report(str(p), NOW)
    assert fault is None
    assert rep.vms_on_pool == (100, 101)
    assert "pre-v385" in rep.host_alarm
    assert [s["name"] for s in rep.snapshots] == ["pre-v385"]


def test_report_argv_is_ascii_through_the_real_runner(monkeypatch):
    seen = []

    class P:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kw):
        seen.append(argv)
        return P()

    monkeypatch.setattr(pm.subprocess, "run", fake_run)
    pm._file_write_real("pve", 100, "/run/sigmond/host-pool.json", '{"a": "\\u2014"}')
    assert seen and all(ord(c) < 128 for a in seen[0] for c in a)
    assert seen[0][:3] == ["pvesh", "create", "/nodes/pve/qemu/100/agent/file-write"]
    with pytest.raises(ValueError):
        pm._file_write_real("pve", 100, "/run/sigmond/host-pool.json", "—")
    assert len(seen) == 1


def test_an_unreachable_guest_alarms_and_the_run_goes_on():
    host = _fake_host(vms=[{"vmid": 100, "status": "running"}, {"vmid": 101, "status": "running"}],
                      fail_write={100})
    assert pm.main(["report"], host) == 0
    assert [w[1] for w in host.calls_of("file_write")] == [100, 100, 101]
    assert host.calls_of("reset") == [("reset", 100)]
    assert any("guest 100 unreachable" in line for line in host.logged)


def test_a_deaf_agent_gets_one_reset_then_one_retry_that_works():
    host = _fake_host(fail_write={100}, recover_on_reset={100})
    assert pm.main(["report"], host) == 0
    assert [c[:2] for c in host.calls if c[0] in ("file_write", "reset")] == [
        ("file_write", 100), ("reset", 100), ("file_write", 100), ("file_write", 101)]
    assert not any("unreachable" in line for line in host.logged)


def test_a_guest_that_writes_first_time_gets_no_reset():
    host = _fake_host()
    pm.main(["report"], host)
    assert host.calls_of("reset") == []


def test_report_keeps_pool_history_and_pool_pct_in_local_state():
    host = _fake_host(pool_pct=60, state=_history(10, 60))
    pm.main(["report"], host)
    pool = host.state["pools"]["pve/data"]
    assert host.state["pool_pct"] == 60.0
    assert len(pool["history"]) == 5
    assert pool["churn_bytes_per_h"] == pytest.approx(10 * GB, rel=0.05)


def test_report_drops_history_older_than_the_window():
    host = _fake_host(pool_pct=60, state=_history(1, 60, hours=10))
    pm.main(["report"], host)
    ts = [t for t, _ in host.state["pools"]["pve/data"]["history"]]
    assert min(ts) >= NOW.timestamp() - pm.CHURN_WINDOW_S


def test_alarms_log_once_per_episode_then_clear():
    host = _fake_host(pool_pct=86)
    pm.main(["report"], host)
    first = [l for l in host.logged if "pve/data" in l]
    assert len(first) == 1 and first[0].startswith("<4>") and "tighten" in first[0]
    host._now = NOW + timedelta(minutes=5)
    pm.main(["report"], host)
    assert [l for l in host.logged if "pve/data" in l] == first
    host._now = NOW + timedelta(hours=6, minutes=5)
    pm.main(["report"], host)
    assert len([l for l in host.logged if "pve/data" in l]) == 2      # the 6 h reminder
    host.pool_pct = 50
    host._now = NOW + timedelta(hours=6, minutes=10)
    pm.main(["report"], host)
    assert "cleared" in host.logged[-1] and "pve/data" in host.logged[-1]


def test_a_stage_change_logs_a_new_line_at_its_priority():
    host = _fake_host(pool_pct=86)
    pm.main(["report"], host)
    host.pool_pct = 96
    host._now = NOW + timedelta(minutes=5)
    pm.main(["report"], host)
    assert host.logged[-1].startswith("<2>") and "final" in host.logged[-1]


# ---------------------------------------------------------------------------
# heartbeat: pool_pct in the resources block
# ---------------------------------------------------------------------------

def _resources(pool_pct):
    return hb.build_resources_raw(
        read_loadavg=lambda: {"load1": 0.1, "load5": 0.1, "load15": 0.1},
        read_disk_used_pct=lambda path: 50.0, read_uptime=lambda: 1.0,
        read_temperature=lambda: None, read_pool_pct=lambda: pool_pct)


def test_heartbeat_pool_at_final_is_invalid():
    block = hb._map_resources(_resources(95.0))
    assert block["verdict"] == "INVALID" and "95" in block["reason"]
    assert block["data"]["pool_pct"] == 95.0


def test_heartbeat_pool_below_final_stays_valid():
    assert hb._map_resources(_resources(94.9))["verdict"] == "VALID"
    block = hb._map_resources(_resources(None))
    assert block["verdict"] == "VALID" and block["data"]["pool_pct"] is None


def test_heartbeat_reads_pool_pct_from_the_local_state(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"generated_at": "2026-11-04T11:58:00Z", "pool_pct": 91.5}))
    assert hb._read_pool_pct(str(p), now=NOW) == 91.5
    assert hb._read_pool_pct(str(p), now=NOW + timedelta(hours=1)) is None     # stale
    assert hb._read_pool_pct(str(tmp_path / "missing.json"), now=NOW) is None


def test_heartbeat_and_storage_agree_on_the_state_path_and_final_level():
    assert hb.POOL_STATE_PATH == pm.LOCAL_STATE
    assert hb.POOL_INVALID_PCT == pm.LEVELS["final"]


# ---------------------------------------------------------------------------
# units and setup
# ---------------------------------------------------------------------------

def test_timer_and_service():
    timer = (PROXMOX / "pm-storage.timer").read_text()
    assert "OnCalendar=*:0/5" in timer and "RandomizedDelaySec=30" in timer
    svc = (PROXMOX / "pm-storage.service").read_text()
    assert "/usr/local/sbin/pm-storage.py report" in svc and "Type=oneshot" in svc


def test_setup_installs_and_enables_the_storage_pair():
    text = (PROXMOX / "pm-heartbeat-setup.sh").read_text()
    assert "pm-storage.py" in text
    assert "pm-storage.service" in text and "pm-storage.timer" in text
    assert "enable --now pm-storage.timer" in text


def test_pm_storage_imports_nothing_from_sigmond():
    import re
    text = (PROXMOX / "pm-storage.py").read_text()
    assert not re.search(r"^\s*(import|from)\s+sigmond", text, re.M)


@pytest.mark.parametrize("flag", [False, None, "true", 1])
def test_expired_requires_the_sigmond_flag_itself(flag):
    snaps = [{"vmid": 100, "name": "pre-v385", "sigmond": flag, "expires": "2026-11-01T00:00:00Z"}]
    assert pm.expired(snaps, NOW) == []
    assert pm.expired(snaps, NOW, pool_pct=99.0) == []


# ---------------------------------------------------------------------------
# fix round 1: ordering, deadlines, storage faults, reap failures to the guest
# ---------------------------------------------------------------------------

STORAGE_2 = STORAGE_CFG + "\nlvmthin: fast\n\tthinpool fastdata\n\tvgname ssd\n\tcontent images\n"


def test_every_reap_on_every_pool_comes_before_any_guest_write():
    host = _fake_host(snaps={
        100: [_snap("align-1", "sigmond-snapshot expires=2026-11-04T11:00:00Z")],
        101: [_snap("align-2", "sigmond-snapshot expires=2026-11-04T11:00:00Z")]})
    host.storage_text = STORAGE_2
    host.pool_of = {101: "fast"}
    host.extra_lvs = [{"lv_name": "fastdata", "vg_name": "ssd", "lv_size": str(500 * GB),
                       "data_percent": "40.00", "metadata_percent": "5.00", "lv_attr": "twi-aotz--"}]
    assert pm.main(["report"], host) == 0
    kinds = [c[0] for c in host.calls if c[0] in ("delsnapshot", "file_write")]
    assert kinds == ["delsnapshot", "delsnapshot", "file_write", "file_write"]
    assert set(host.state["pools"]) == {"pve/data", "ssd/fastdata"}


def test_the_guest_write_phase_skips_guests_past_its_deadline():
    vms = [{"vmid": v, "status": "running"} for v in (100, 101, 102)]
    host = _fake_host(vms=vms, write_cost=pm.GUEST_WRITE_BUDGET_S * 0.6)
    pm.main(["report"], host)
    assert [w[1] for w in host.calls_of("file_write")] == [100, 101]
    assert any("guest 102" in line and "deadline" in line for line in host.logged)
    assert host.calls_of("write_state")


def test_a_stop_request_ends_the_reaping_between_deletes_and_still_writes_state():
    host = _fake_host(snaps={100: [
        _snap("align-1", "sigmond-snapshot expires=2026-11-04T11:00:00Z"),
        _snap("align-2", "sigmond-snapshot expires=2026-11-04T11:00:00Z")]})
    host.stop_after_reaps = 1
    pm.main(["report"], host)
    assert host.calls_of("delsnapshot") == [("delsnapshot", 100, "align-1")]
    assert host.calls_of("file_write") == []
    assert host.calls_of("write_state")
    assert host.state["pool_pct"] == 50.0


def test_delsnapshot_runs_with_no_timeout(monkeypatch):
    seen = []

    class P:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kw):
        seen.append((argv, kw))
        return P()

    monkeypatch.setattr(pm.subprocess, "run", fake_run)
    host = pm.RealHost.__new__(pm.RealHost)
    host._node = "pve"
    host.delsnapshot(100, "align-1")
    (argv, kw), = seen
    assert argv == ["qm", "delsnapshot", "100", "align-1"]
    assert kw.get("timeout") is None


def test_the_unit_outlasts_a_slow_delete():
    import re
    svc = (PROXMOX / "pm-storage.service").read_text()
    start = int(re.search(r"^TimeoutStartSec=(\d+)", svc, re.M).group(1))
    stop = int(re.search(r"^TimeoutStopSec=(\d+)", svc, re.M).group(1))
    assert start >= 1800 and stop >= 1800
    assert re.search(r"^KillMode=mixed", svc, re.M)
    assert pm.GUEST_WRITE_BUDGET_S < start


def test_unreadable_host_storage_alarms_once_per_episode_and_writes_state():
    host = _fake_host()
    host.storage_text = OSError("storage.cfg: permission denied")
    assert pm.main(["report"], host) == 1
    lines = [l for l in host.logged if "host's storage" in l]
    assert len(lines) == 1 and lines[0].startswith("<3>")
    assert host.calls_of("write_state") and host.state["pool_pct"] is None
    host._now = NOW + timedelta(minutes=5)
    pm.main(["report"], host)
    assert len([l for l in host.logged if "host's storage" in l]) == 1
    host.storage_text = STORAGE_CFG
    host._now = NOW + timedelta(minutes=10)
    assert pm.main(["report"], host) == 0
    assert any(l.startswith("<5>cleared: host-storage") for l in host.logged)


def test_unreadable_host_storage_keeps_the_pool_history():
    host = _fake_host(pool_pct=60, state=_history(10, 60))
    host.storage_text = OSError("gone")
    pm.main(["report"], host)
    assert len(host.state["pools"]["pve/data"]["history"]) == 4


def test_reap_failures_and_unreadable_lists_reach_the_guest(tmp_path):
    from sigmond.storage.measure import read_host_report
    host = _fake_host(snaps={100: [_snap("align-1", "sigmond-snapshot expires=2026-11-04T11:00:00Z")]},
                      fail_del={(100, "align-1")})
    real = host.list_snapshots
    host.list_snapshots = lambda vmid: (_ for _ in ()).throw(RuntimeError("x")) if vmid == 101 else real(vmid)
    pm.main(["report"], host)
    content = [w[3] for w in host.calls_of("file_write") if w[1] == 100][0]
    p = tmp_path / "h.json"
    p.write_text(content)
    rep, fault = read_host_report(str(p), NOW)
    assert fault is None
    assert "could not delete snapshot align-1" in rep.host_alarm
    assert "snapshots of VM 101 unreadable" in rep.host_alarm


# ---------------------------------------------------------------------------
# a run that does not measure a scope carries its episodes forward
# ---------------------------------------------------------------------------

def _op_snap():
    return {100: [_snap("before-test", "an operator's", hours_ago=60)]}


def _raised(host, needle):
    return [l for l in host.logged if needle in l and not l.startswith("<5>cleared")]


def _cleared(host):
    return [l for l in host.logged if l.startswith("<5>cleared")]


def test_unreadable_host_storage_carries_every_episode_forward():
    host = _fake_host(pool_pct=86, snaps=_op_snap())
    pm.main(["report"], host)
    before = dict(host.state["alarms"])
    assert {"pool:pve/data", "snap:100:before-test"} <= set(before)
    host.storage_text = OSError("gone")
    host._now = NOW + timedelta(minutes=5)
    pm.main(["report"], host)
    assert _cleared(host) == []
    for k, v in before.items():
        assert host.state["alarms"][k] == v
    host.storage_text = STORAGE_CFG
    host._now = NOW + timedelta(minutes=10)
    pm.main(["report"], host)
    assert len(_raised(host, "pool pve/data at")) == 1         # no reopen
    assert len(_raised(host, "snapshot before-test")) == 1
    assert _cleared(host) == ["<5>cleared: host-storage"]


def test_an_unreadable_pool_carries_its_episodes_forward():
    host = _fake_host(pool_pct=86, snaps=_op_snap(), fail_write={100})
    pm.main(["report"], host)
    before = dict(host.state["alarms"])
    assert {"pool:pve/data", "snap:100:before-test", "guest:100"} <= set(before)
    real = host.lvs
    host.lvs = lambda: _lvs(86, pool="other")
    host._now = NOW + timedelta(minutes=5)
    assert pm.main(["report"], host) == 1
    assert _cleared(host) == []
    for k, v in before.items():
        assert host.state["alarms"][k] == v
    host.lvs = real
    host._now = NOW + timedelta(minutes=10)
    pm.main(["report"], host)
    assert len(_raised(host, "pool pve/data at")) == 1
    assert len(_raised(host, "guest 100 unreachable")) == 1
    assert _cleared(host) == ["<5>cleared: pool-unreadable:pve/data"]


def test_a_stop_before_the_guest_writes_carries_the_guest_episodes_forward():
    host = _fake_host(fail_write={100})
    pm.main(["report"], host)
    assert "guest:100" in host.state["alarms"]
    before = host.state["alarms"]["guest:100"]
    host.stop_after_reaps = 0                   # a stop request stands from the start
    host._now = NOW + timedelta(minutes=5)
    pm.main(["report"], host)
    assert _cleared(host) == []
    assert host.state["alarms"]["guest:100"] == before
    host.stop_after_reaps = None
    host._now = NOW + timedelta(minutes=10)
    pm.main(["report"], host)
    assert len(_raised(host, "guest 100 unreachable")) == 1

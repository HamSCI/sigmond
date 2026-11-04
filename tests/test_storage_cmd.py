"""`smd storage status|share|directive|tick`."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sigmond.commands.storage import cmd_storage
from sigmond.storage.config import load_storage_config

REPO = Path(__file__).resolve().parent.parent

STATUS = {
    "schema": 1, "generated_at": "2026-11-04T12:00:00Z", "worst_stage": "TIGHTEN",
    "stores": [{"name": "root", "kind": "guest", "capacity": 100 << 30, "used": 86 << 30,
                "used_pct": 86.0, "stage": "TIGHTEN", "allottable": 48 << 30},
               {"name": "pve/data", "kind": "pool", "capacity": 1000 << 30,
                "used": 500 << 30, "used_pct": 50.0, "stage": "OK", "allottable": None}],
    "clients": [{"client": "hf-timestd", "store": "root", "held": 40 << 30,
                 "allotment": 48 << 30, "target": 34 << 30, "floor": 1 << 30,
                 "managed": True, "stopped_by_storage": False}],
    "host_report": {"expected": True, "fault": None, "age_s": 30},
    "hands_off": None,
    "alarms": [{"key": "store:root", "severity": "WARNING",
                "message": "store root at 86.0% (TIGHTEN); largest holders: hf-timestd — 40 GiB",
                "since": "2026-11-04T11:00:00Z"}],
    "unclaimed": [["/var/tmp/big", 2 << 30]],
    "config_problems": [],
}


def _args(tmp_path, **kw):
    base = dict(storage_command=None, json=False, target=None, value=None, clear=False,
                for_duration=None, reason=None,
                config_path=tmp_path / "storage.toml", status_path=tmp_path / "status.json")
    base.update(kw)
    return SimpleNamespace(**base)


def test_status_json_prints_the_file_unchanged(tmp_path, capsys):
    text = json.dumps(STATUS, indent=1)
    (tmp_path / "status.json").write_text(text)
    assert cmd_storage(_args(tmp_path, storage_command="status", json=True)) == 0
    assert capsys.readouterr().out == text


def test_status_plain_is_one_line_per_store_and_client_and_ascii(tmp_path, capsys):
    (tmp_path / "status.json").write_text(json.dumps(STATUS))
    assert cmd_storage(_args(tmp_path, storage_command="status")) == 0
    out = capsys.readouterr().out
    assert all(ord(ch) <= 0x7F for ch in out)
    lines = out.splitlines()
    assert sum(l.lstrip().startswith("store ") for l in lines) == 2
    assert sum(l.lstrip().startswith("client ") for l in lines) == 1
    assert "TIGHTEN" in out and "hf-timestd" in out


def test_status_with_no_file_says_no_tick_yet(tmp_path, capsys):
    assert cmd_storage(_args(tmp_path, storage_command="status")) == 0
    assert "no ledger tick yet" in capsys.readouterr().out


def test_share_writes_and_clears(tmp_path):
    a = _args(tmp_path, storage_command="share", target="hf-timestd", value="2")
    assert cmd_storage(a) == 0
    assert load_storage_config(a.config_path).shares == {"hf-timestd": 2.0}
    assert cmd_storage(_args(tmp_path, storage_command="share", target="hf-timestd",
                             clear=True)) == 0
    assert load_storage_config(a.config_path).shares == {}


def test_share_refuses_a_bad_weight(tmp_path):
    assert cmd_storage(_args(tmp_path, storage_command="share", target="x", value="-1")) == 2
    assert cmd_storage(_args(tmp_path, storage_command="share", target="x", value="lots")) == 2


def test_directive_writes_and_clears(tmp_path):
    a = _args(tmp_path, storage_command="directive", target="hf-timestd",
              value="give-up-oldest:raw")
    assert cmd_storage(a) == 0
    assert load_storage_config(a.config_path).directives == {"hf-timestd": "give-up-oldest:raw"}
    assert cmd_storage(_args(tmp_path, storage_command="directive", target="hf-timestd",
                             clear=True)) == 0
    assert load_storage_config(a.config_path).directives == {}


def test_bad_policy_exits_2_naming_the_allowed_set(tmp_path, capsys):
    a = _args(tmp_path, storage_command="directive", target="x", value="delete-everything")
    assert cmd_storage(a) == 2
    err = capsys.readouterr().err
    for word in ("give-up-oldest:", "stop-writing", "refuse-new-rows", "custom:"):
        assert word in err
    assert not a.config_path.exists()


def test_hands_off_writes_with_a_default_day(tmp_path):
    a = _args(tmp_path, storage_command="directive", target="hands-off", reason="A/B test")
    before = datetime.now(timezone.utc)
    assert cmd_storage(a) == 0
    c = load_storage_config(a.config_path)
    assert c.hands_off_reason == "A/B test"
    assert timedelta(hours=23) < c.hands_off_until - before <= timedelta(hours=24, seconds=5)
    assert cmd_storage(_args(tmp_path, storage_command="directive", target="hands-off",
                             clear=True)) == 0
    assert load_storage_config(a.config_path).hands_off_until is None


def test_hands_off_for_15_days_exits_2(tmp_path):
    a = _args(tmp_path, storage_command="directive", target="hands-off", for_duration="15d")
    assert cmd_storage(a) == 2
    assert not a.config_path.exists()


def test_an_unparseable_config_is_not_overwritten(tmp_path):
    a = _args(tmp_path, storage_command="share", target="x", value="2")
    a.config_path.write_text("this is [ not toml")
    assert cmd_storage(a) == 1
    assert a.config_path.read_text() == "this is [ not toml"


def test_smd_storage_status_from_the_dev_tree(tmp_path):
    """Smoke: the verb is wired, and with no status file says so, exit 0."""
    env = {"PYTHONPATH": str(REPO / "lib"), "PATH": "/usr/bin:/bin",
           "SIGMOND_TEST_NO_ELEVATE": "1", "SIGMOND_NO_VENV_REEXEC": "1",
           "SIGMOND_STORAGE_STATUS": str(tmp_path / "none.json")}
    r = subprocess.run([sys.executable, str(REPO / "bin" / "smd"), "storage", "status"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "no ledger tick yet" in r.stdout

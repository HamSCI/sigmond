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
                "used_pct": 86.0, "stage": "TIGHTEN", "allottable": 48 << 30, "days": 2.4},
               {"name": "pve/data", "kind": "pool", "capacity": 1000 << 30,
                "used": 500 << 30, "used_pct": 50.0, "stage": "OK", "allottable": None,
                "days": None}],
    "clients": [{"client": "hf-timestd", "store": "root", "held": 40 << 30,
                 "allotment": 48 << 30, "target": 34 << 30, "floor": 1 << 30,
                 "managed": True, "stopped_by_storage": False,
                 "rate_bytes_per_day": 20 << 30, "rate_source": "reported", "days_held": 2.0}],
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


def test_status_does_not_follow_a_symlink(tmp_path, capsys):
    real = tmp_path / "real.json"
    real.write_text(json.dumps(STATUS))
    (tmp_path / "status.json").symlink_to(real)
    assert cmd_storage(_args(tmp_path, storage_command="status", json=True)) == 1
    assert capsys.readouterr().out == ""


# ---- the timer, its service, the tmpfiles line, install.sh -------------------

import configparser
import re


def _unit(name):
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string((REPO / "systemd" / name).read_text())
    return cp


def test_storage_service_runs_the_tick_as_root():
    s = _unit("sigmond-storage.service")["Service"]
    assert s["Type"] == "oneshot" and s["User"] == "root"
    assert s["ExecStart"] == "/usr/local/bin/smd storage tick"
    assert "75" in s["SuccessExitStatus"].split()       # a held lock is not a failure
    assert s["SyslogIdentifier"] == "sigmond-storage"


def test_storage_timer_fires_every_two_minutes():
    cp = _unit("sigmond-storage.timer")
    assert cp["Timer"]["OnCalendar"] == "*:0/2"
    assert cp["Timer"]["RandomizedDelaySec"] == "20"
    assert cp["Install"]["WantedBy"] == "timers.target"


def test_tmpfiles_creates_run_sigmond():
    lines = [l.split() for l in (REPO / "systemd" / "sigmond-storage.conf").read_text().splitlines()
             if l.strip() and not l.lstrip().startswith("#")]
    assert ["d", "/run/sigmond", "0755", "root", "root", "-"] in lines


def test_install_sh_ships_and_enables_the_timer():
    text = (REPO / "install.sh").read_text()
    assert '"$REPO_DIR/systemd/sigmond-storage.conf" /etc/tmpfiles.d/' in text
    assert "systemd-tmpfiles --create /etc/tmpfiles.d/sigmond-storage.conf" in text
    assert re.search(r"systemctl enable --now [^\n]*\bsigmond-storage\.timer", text)


def test_install_sh_each_ok_follows_its_own_command():
    text = (REPO / "install.sh").read_text()
    i = text.index("$SUDO systemctl enable --now sigmond-storage-trim-all.timer")
    j = text.index('ok "sigmond-storage-trim-all.timer enabled')
    assert "sigmond-storage.timer" not in text[i:j]


def test_status_plain_leads_each_store_with_its_days(tmp_path, capsys):
    (tmp_path / "status.json").write_text(json.dumps(STATUS))
    assert cmd_storage(_args(tmp_path, storage_command="status")) == 0
    stores = [l.strip() for l in capsys.readouterr().out.splitlines()
              if l.lstrip().startswith("store ")]
    assert stores[0].startswith("store  root (guest): holds 2.4 days")
    assert stores[1].startswith("store  pve/data (pool): holds - days")


def test_status_plain_shows_each_clients_rate_and_days(tmp_path, capsys):
    (tmp_path / "status.json").write_text(json.dumps(STATUS))
    cmd_storage(_args(tmp_path, storage_command="status"))
    (line,) = [l for l in capsys.readouterr().out.splitlines() if "client hf-timestd" in l]
    assert "holds 2.0 days" in line and "20.0 GiB/day (reported)" in line


# ---- the stop lever's switch -------------------------------------------------

def test_stop_lever_on_and_off(tmp_path, capsys):
    a = _args(tmp_path, storage_command="directive", target="stop-lever", value="on")
    assert cmd_storage(a) == 0
    assert load_storage_config(a.config_path).stop_enabled is True
    assert cmd_storage(_args(tmp_path, storage_command="directive", target="stop-lever",
                             value="off")) == 0
    assert load_storage_config(a.config_path).stop_enabled is False
    assert "stop lever off" in capsys.readouterr().out


def test_stop_lever_needs_on_or_off(tmp_path):
    for bad in (None, "maybe"):
        a = _args(tmp_path, storage_command="directive", target="stop-lever", value=bad)
        assert cmd_storage(a) == 2
        assert not a.config_path.exists()


def test_status_shows_the_stop_lever(tmp_path, capsys):
    (tmp_path / "status.json").write_text(json.dumps(dict(STATUS, stop_lever="off")))
    cmd_storage(_args(tmp_path, storage_command="status"))
    out = capsys.readouterr().out
    assert "stop lever off" in out


def test_share_and_directive_refuse_while_hands_off_does_not_parse(tmp_path, capsys):
    bad = '[hands_off]\nuntil = "next tuesday"\n'
    for kw in (dict(storage_command="share", target="x", value="2"),
               dict(storage_command="directive", target="x", value="stop-writing"),
               dict(storage_command="directive", target="stop-lever", value="on"),
               dict(storage_command="directive", target="hands-off", clear=True)):
        a = _args(tmp_path, **kw)
        a.config_path.write_text(bad)
        assert cmd_storage(a) == 2, kw
        assert a.config_path.read_text() == bad
        assert "hands_off" in capsys.readouterr().err

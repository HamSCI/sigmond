"""The writer and the hooks that feed it (v3.71).

udev notes the RX888 leaving and rejoining the bus, and radiod's drop-in
notes an exit nobody asked for.  sigmond-sdr-recover notes its power cuts
(tests/test_sdr_recover.py), and sigmond-radiod-watchdog its restarts
(tests/test_radiod_watchdog.py), both in-process.  The writer must never
fail its caller.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "sigmond-sdr-event"
RULE = REPO / "udev" / "91-sigmond-sdr-event.rules"
DROPIN = REPO / "systemd" / "radiod-sdr-event.conf"


def run_cli(tmp_path, *args, events=None, **env):
    e = {k: v for k, v in os.environ.items()
         if k not in ("SERVICE_RESULT", "EXIT_CODE", "EXIT_STATUS")}
    e["SIGMOND_SDR_EVENTS"] = str(events or tmp_path / "sdr-events.jsonl")
    e.update(env)
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True,
                          text=True, env=e, timeout=30)


def lines(p):
    return [json.loads(l) for l in p.read_text().splitlines()]


# -- the writer, as radiod's drop-in runs it --------------------------------------------

def test_an_exit_someone_asked_for_writes_nothing(tmp_path):
    r = run_cli(tmp_path, "radiod-exit", "ac0g", SERVICE_RESULT="success",
                EXIT_CODE="exited", EXIT_STATUS="0")
    assert r.returncode == 0
    assert not (tmp_path / "sdr-events.jsonl").exists()


def test_a_slow_stop_writes_nothing(tmp_path):
    r = run_cli(tmp_path, "radiod-exit", "ac0g", SERVICE_RESULT="timeout",
                EXIT_CODE="killed", EXIT_STATUS="KILL")
    assert r.returncode == 0
    assert not (tmp_path / "sdr-events.jsonl").exists()


def test_a_run_by_hand_outside_systemd_writes_nothing(tmp_path):
    assert run_cli(tmp_path, "radiod-exit", "ac0g").returncode == 0
    assert not (tmp_path / "sdr-events.jsonl").exists()


def test_a_stall_writes_one_episode_naming_how_radiod_died(tmp_path):
    r = run_cli(tmp_path, "radiod-exit", "ac0g", SERVICE_RESULT="core-dump",
                EXIT_CODE="dumped", EXIT_STATUS="ABRT")
    assert r.returncode == 0
    (d,) = lines(tmp_path / "sdr-events.jsonl")
    assert d["kinds"] == ["radiod-exit"]
    assert d["detail"] == "ac0g core-dump dumped/ABRT"


def test_the_opening_line_speaks_once_per_episode(tmp_path):
    env = dict(SERVICE_RESULT="signal", EXIT_CODE="killed", EXIT_STATUS="ABRT")
    first = run_cli(tmp_path, "radiod-exit", "ac0g", **env)
    second = run_cli(tmp_path, "radiod-exit", "ac0g", **env)
    assert "RX888 lost" in first.stderr
    assert second.stderr == ""


# -- the writer, as udev runs it --------------------------------------------------------

def test_a_removal_then_a_return_make_one_episode(tmp_path):
    assert run_cli(tmp_path, "usb-remove", "8-1.2").returncode == 0
    assert run_cli(tmp_path, "usb-add", "8-1.2").returncode == 0
    (d,) = lines(tmp_path / "sdr-events.jsonl")
    assert d["kinds"] == ["usb-remove"] and d["back"] is not None


def test_an_unknown_kind_exits_2_and_writes_nothing(tmp_path):
    r = run_cli(tmp_path, "unplugged")
    assert r.returncode == 2
    assert not (tmp_path / "sdr-events.jsonl").exists()


def test_a_record_it_cannot_write_still_exits_0(tmp_path):
    (tmp_path / "file").write_text("")
    r = run_cli(tmp_path, "usb-remove", "8-1.2", events=tmp_path / "file" / "sdr-events.jsonl")
    assert r.returncode == 0
    assert "could not record" in r.stderr


# -- the udev rule ----------------------------------------------------------------------

def rule_lines():
    return [l for l in RULE.read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def test_the_rule_file_holds_ascii_only():
    RULE.read_bytes().decode("ascii")


def test_exactly_two_rules():
    assert len(rule_lines()) == 2


def test_a_removal_matches_the_running_card_by_the_kernels_product():
    (rm,) = [l for l in rule_lines() if 'ACTION=="remove"' in l]
    assert 'ENV{DEVTYPE}=="usb_device"' in rm
    assert 'ENV{PRODUCT}=="4b4/f1/*"' in rm
    assert "ATTR{" not in rm
    assert 'RUN+="/usr/bin/python3 /usr/local/sbin/sigmond-sdr-event usb-remove %k"' in rm


def test_an_arrival_matches_the_same_card():
    (add,) = [l for l in rule_lines() if 'ACTION=="add"' in l]
    assert 'ENV{PRODUCT}=="4b4/f1/*"' in add
    assert 'RUN+="/usr/bin/python3 /usr/local/sbin/sigmond-sdr-event usb-add %k"' in add


def test_the_bootloader_never_counts():
    for l in rule_lines():
        assert "f3" not in l, l


# -- radiod's drop-in -------------------------------------------------------------------

def test_the_dropin_runs_the_writer_as_root_and_never_fails_radiod():
    text = DROPIN.read_text()
    text.encode("ascii")
    body = [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert body == ["[Service]",
                    "ExecStopPost=-+/usr/local/sbin/sigmond-sdr-event radiod-exit %i"]


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_sdr_hooks", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_sdr_hooks", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_doctor_fix_and_the_native_install_deliver_the_dropin():
    smd = _load_smd()
    assert ("radiod-sdr-event.conf", "radiod@.service.d/50-sigmond-sdr-event.conf") in smd._RADIOD_DROPINS


# -- install.sh -------------------------------------------------------------------------

def test_install_sh_lays_down_the_writer_the_rule_and_the_dropin():
    sh = (REPO / "install.sh").read_text()
    assert 'ln -sf "$REPO_DIR/bin/sigmond-sdr-event"' in sh
    assert "/usr/local/sbin/sigmond-sdr-event" in sh
    assert "udev/91-sigmond-sdr-event.rules" in sh
    assert "/etc/udev/rules.d/91-sigmond-sdr-event.rules" in sh
    assert "systemd/radiod-sdr-event.conf" in sh
    assert "radiod@.service.d/50-sigmond-sdr-event.conf" in sh

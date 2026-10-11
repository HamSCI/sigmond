"""One parser reset for the guest agent, in four programs
(tasks/plan-guest-agent-requests.md section 4).

A guest agent can stand deaf: it keeps its process, reads every request and
answers none.  One 0xFF byte from the host resets its parser.  The console
panel, the wizard, pm-align and pm-heartbeat each carry the few lines that
send it, because each must run with nothing beside it.  These tests hold the
four copies to the same bytes, and they pin what the heartbeat and pm-align
do around a reset.
"""
import importlib.util
import os
import re
import socket
import sys
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROXMOX = Path(os.environ.get("SIGMOND_TEST_PROXMOX") or REPO / "scripts" / "proxmox")
WIZARD = Path(os.environ.get("SIGMOND_TEST_WIZARD") or REPO / "scripts" / "proxmox" / "sigmond-wizard.sh")
FIRSTBOOT = Path(os.environ.get("SIGMOND_TEST_FIRSTBOOT")
                 or REPO.parent / "sigmond-appliance" / "firstboot-v3.sh")

if str(REPO / "lib") not in sys.path:
    sys.path.insert(0, str(REPO / "lib"))

from sigmond import heartbeat_schema  # noqa: E402


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PROXMOX / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # pm-align's dataclasses look their module up by name
    spec.loader.exec_module(mod)
    return mod


hb = load("pm_heartbeat_qga", "pm-heartbeat.py")
pa = load("pm_align_qga", "pm-align.py")

#: The bytes every reset sends: the byte that resets the agent's parser, then
#: one sync request that the agent must answer.
RESET = b"\xff" + b'{"execute":"guest-sync-delimited","arguments":{"id":4242}}\n'
STAMP = r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ"


def rr(rc, stdout="", stderr=""):
    return hb.RunResult(rc, stdout, stderr)


DEAF = rr(255, "", "QEMU guest agent is not running")
RUNNING = rr(0, "status: running")


# -- the four copies send the same bytes ---------------------------------------------------

def test_the_heartbeat_and_pm_align_hold_the_reset_bytes():
    assert hb.QGA_RESET == RESET
    assert pa.QGA_RESET == RESET
    assert hb.QGA_NOTE == pa.QGA_NOTE == "/run/sigmond/qga-reset"


def bash_reset_snippet(text: str) -> str:
    """The python3 one-liner inside a bash `qga_reset`, as the script holds it."""
    m = re.search(r"^qga_reset\(\)\{\n(.*?)^\}\n", text, re.S | re.M)
    assert m, "no qga_reset function"
    return m.group(1)


def test_the_wizards_reset_holds_the_same_bytes():
    body = bash_reset_snippet(WIZARD.read_text())
    assert r's.sendall(b"\xff"+b"{\"execute\":\"guest-sync-delimited\",\"arguments\":{\"id\":4242}}\n")' in body


def test_the_panels_reset_is_the_wizards_line_for_line():
    """The panel lives in another repository.  With that checkout beside this
    one, its function must equal the wizard's; without it, the panel's own
    test (test-panel-agent.sh) still holds its bytes."""
    if not FIRSTBOOT.exists():
        pytest.skip(f"no sigmond-appliance checkout at {FIRSTBOOT.parent}")
    assert bash_reset_snippet(FIRSTBOOT.read_text()) == bash_reset_snippet(WIZARD.read_text())


# -- the Python form, against a stand-in socket ---------------------------------------------

class Agent:
    """The agent's side of the socket.  mode: answer | wrong | hangup | silent
    (it reads the request and holds the connection open without a word, as a
    socket that another request occupies would)."""

    def __init__(self, tmp_path: Path, mode="answer"):
        self.got, self.mode, self.release = b"", mode, threading.Event()
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(str(tmp_path / "100.qga"))
        self.srv.listen(1)
        self.srv.settimeout(10)
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self):
        try:
            c, _ = self.srv.accept()
        except OSError:
            return
        c.settimeout(5)
        try:
            while not self.got.endswith(b"\n"):
                d = c.recv(4096)
                if not d:
                    break
                self.got += d
            if self.mode == "answer":
                c.sendall(b"\xff" + b'{"return": 4242}\n')
            elif self.mode == "wrong":
                c.sendall(b"\xff" + b'{"return": 17}\n')
            elif self.mode == "silent":
                self.release.wait(20)
            elif self.mode == "pieces":
                # The protocol lets the agent answer in pieces: an error object
                # for the stray 0xFF, then the 0xFF, then the sync's answer.
                for piece in (b'{"error": {"class": "JSONParsingError"}}\n',
                              b"\xff", b'{"return": 4242}\n'):
                    c.sendall(piece)
                    time.sleep(0.3)
            elif self.mode == "error_only":
                c.sendall(b'{"error": {"class": "JSONParsingError"}}\n')
                self.release.wait(20)
        except OSError:
            pass
        c.close()


@pytest.mark.parametrize("mod", [hb, pa], ids=["pm-heartbeat", "pm-align"])
def test_a_reset_that_the_agent_answers_reads_true(tmp_path, monkeypatch, mod):
    monkeypatch.setattr(mod, "QGA_DIR", str(tmp_path))
    agent = Agent(tmp_path)
    assert mod._qga_reset(100) is True
    agent.t.join(5)
    assert agent.got == RESET


@pytest.mark.parametrize("mod", [hb, pa], ids=["pm-heartbeat", "pm-align"])
@pytest.mark.parametrize("mode", ["wrong", "hangup"])
def test_a_reset_without_its_own_answer_reads_false(tmp_path, monkeypatch, mod, mode):
    monkeypatch.setattr(mod, "QGA_DIR", str(tmp_path))
    Agent(tmp_path, mode)
    assert mod._qga_reset(100) is False


def test_a_reset_that_gets_no_word_gives_up_within_seconds(tmp_path, monkeypatch):
    """A caller must never hang on the socket: the panel runs under a lock
    that the next refresh waits behind."""
    import time
    monkeypatch.setattr(hb, "QGA_DIR", str(tmp_path))
    agent = Agent(tmp_path, "silent")
    t0 = time.monotonic()
    assert hb._qga_reset(100) is False
    took = time.monotonic() - t0
    agent.release.set()
    assert 3.0 < took < 8.0, took


@pytest.mark.parametrize("mod", [hb, pa], ids=["pm-heartbeat", "pm-align"])
def test_an_answer_that_comes_in_pieces_reads_true(tmp_path, monkeypatch, mod):
    monkeypatch.setattr(mod, "QGA_DIR", str(tmp_path))
    Agent(tmp_path, "pieces")
    assert mod._qga_reset(100) is True


@pytest.mark.parametrize("mod", [hb, pa], ids=["pm-heartbeat", "pm-align"])
def test_an_error_object_alone_fails_within_the_one_deadline(tmp_path, monkeypatch, mod):
    monkeypatch.setattr(mod, "QGA_DIR", str(tmp_path))
    agent = Agent(tmp_path, "error_only")
    t0 = time.monotonic()
    assert mod._qga_reset(100) is False
    took = time.monotonic() - t0
    agent.release.set()
    assert took < 6.0, took


def bash_reset(tmp_path, text, note=False):
    """Run a script's own `qga_reset` (and, with note, `qga_note_reset`)."""
    body = ""
    for name in ("qga_reset", "qga_note_reset"):
        m = re.search(rf"^{name}\(\)\{{\n.*?^\}}\n", text, re.S | re.M)
        assert m, name
        body += m.group(0)
    script = (f'QGA_DIR={tmp_path}\nQGA_NOTE={tmp_path}/run/qga-reset\n{body}\n'
              f'qga_reset 100 && {"qga_note_reset test" if note else "true"}\n')
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("src", [WIZARD, FIRSTBOOT], ids=["wizard", "panel"])
def test_the_bash_reset_reads_an_answer_in_pieces_and_leaves_the_record(tmp_path, src):
    if not src.exists():
        pytest.skip(f"no {src}")
    Agent(tmp_path, "pieces")
    r = bash_reset(tmp_path, src.read_text(), note=True)
    assert r.returncode == 0, r.stderr
    assert re.fullmatch(rf"{STAMP} test\n", (tmp_path / "run" / "qga-reset").read_text())


@pytest.mark.parametrize("src", [WIZARD, FIRSTBOOT], ids=["wizard", "panel"])
def test_the_bash_reset_fails_within_the_deadline_on_an_error_alone(tmp_path, src):
    if not src.exists():
        pytest.skip(f"no {src}")
    agent = Agent(tmp_path, "error_only")
    t0 = time.monotonic()
    r = bash_reset(tmp_path, src.read_text(), note=True)
    took = time.monotonic() - t0
    agent.release.set()
    assert r.returncode != 0
    assert not (tmp_path / "run" / "qga-reset").exists()
    assert took < 6.5, took


@pytest.mark.parametrize("mod", [hb, pa], ids=["pm-heartbeat", "pm-align"])
def test_a_reset_with_no_socket_reads_false(tmp_path, monkeypatch, mod):
    monkeypatch.setattr(mod, "QGA_DIR", str(tmp_path / "absent"))
    assert mod._qga_reset(100) is False


# -- the record one reset leaves ----------------------------------------------------------

@pytest.mark.parametrize("mod,who", [(hb, "heartbeat"), (pa, "pm-align")])
def test_a_reset_that_worked_leaves_one_line(tmp_path, monkeypatch, mod, who):
    note = tmp_path / "run" / "qga-reset"
    monkeypatch.setattr(mod, "QGA_NOTE", str(note))
    mod._note_reset("first")
    mod._note_reset(who)
    assert re.fullmatch(rf"{STAMP} {re.escape(who)}\n", note.read_text())


@pytest.mark.parametrize("mod", [hb, pa], ids=["pm-heartbeat", "pm-align"])
def test_a_record_that_cannot_be_written_raises_nothing(tmp_path, monkeypatch, mod):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    monkeypatch.setattr(mod, "QGA_NOTE", str(blocker / "qga-reset"))
    mod._note_reset("heartbeat")


def test_the_heartbeat_takes_the_record_once(tmp_path, monkeypatch):
    note = tmp_path / "qga-reset"
    monkeypatch.setattr(hb, "QGA_NOTE", str(note))
    note.write_text("2026-10-10T02:03:04Z panel\n")
    assert hb._take_reset_note() == "2026-10-10T02:03:04Z panel"
    assert not note.exists()
    assert (tmp_path / "qga-reset.seen").read_text() == "2026-10-10T02:03:04Z panel\n"
    assert hb._take_reset_note() is None


@pytest.mark.parametrize("raw,want", [
    ("2026-10-10T02:03:04Z panel\x1b[31m\x00\n", "2026-10-10T02:03:04Z panel[31m"),
    ("x" * 300 + "\n", "x" * 80),
    ("\n", None),
    ("", None),
])
def test_the_record_travels_as_printable_text_of_bounded_length(tmp_path, monkeypatch, raw, want):
    note = tmp_path / "qga-reset"
    monkeypatch.setattr(hb, "QGA_NOTE", str(note))
    note.write_text(raw)
    assert hb._take_reset_note() == want


# -- the heartbeat: one reset before it calls the agent wedged ----------------------------

def check(answers, status=RUNNING, reset=True, note=None):
    """Run _check_guest_exec with a scripted agent.  Returns (finding, log)."""
    log = []
    answers = list(answers)

    def run_guest_exec(vmid):
        log.append("exec")
        return answers.pop(0)

    def reset_agent(vmid):
        log.append("reset")
        return reset

    def take():
        log.append("take")
        return note

    f = hb._check_guest_exec(100, run_guest_exec, lambda vmid: status, reset_agent,
                             lambda who: log.append(f"note {who}"), take)
    return f, log


def test_a_deaf_agent_that_a_reset_brings_back_reads_reset_not_wedged():
    f, log = check([DEAF, rr(0)], note="2026-10-10T02:03:04Z heartbeat")
    assert f["kind"] == "guest-agent-reset"
    assert "2026-10-10T02:03:04Z heartbeat" in f["detail"]
    assert log == ["exec", "reset", "note heartbeat", "exec", "take"]


def test_a_deaf_agent_that_no_reset_brings_back_reads_wedged():
    f, log = check([DEAF], reset=False)
    assert f["kind"] == "guest-agent-wedged"
    assert "one parser reset brought no answer" in f["detail"]
    assert log == ["exec", "reset"]


def test_an_agent_that_answers_the_reset_and_fails_again_reads_wedged():
    f, log = check([DEAF, DEAF])
    assert f["kind"] == "guest-agent-wedged"
    assert "answered a parser reset and failed the next request" in f["detail"]
    assert log == ["exec", "reset", "note heartbeat", "exec"]


def test_a_stopped_vm_gets_no_reset():
    f, log = check([DEAF], status=rr(0, "status: stopped"))
    assert f["kind"] == "vm-stopped"
    assert log == ["exec"]


def test_another_programs_reset_since_the_last_beat_is_reported():
    f, log = check([rr(0)], note="2026-10-10T02:03:04Z panel")
    assert f["kind"] == "guest-agent-reset"
    assert "2026-10-10T02:03:04Z panel" in f["detail"]
    assert log == ["exec", "take"]


def test_an_agent_that_answers_with_no_record_brings_no_finding():
    f, log = check([rr(0)])
    assert f is None
    assert log == ["exec", "take"]


def test_another_failure_gets_no_reset_and_leaves_the_record_for_a_later_beat():
    f, log = check([rr(1, "", "connection refused")], note="2026-10-10T02:03:04Z panel")
    assert f["kind"] == "guest-exec-error"
    assert log == ["exec"]


def test_the_old_call_shape_still_reads_wedged_at_once():
    f = hb._check_guest_exec(100, lambda vmid: DEAF, lambda vmid: RUNNING)
    assert f["kind"] == "guest-agent-wedged"
    assert "reset" not in f["detail"]


def doctor(findings, checks_run=3):
    return hb._map_doctor({"findings": findings, "checks_run": checks_run, "checks_total": 3, "cat": None})


def test_a_reset_alone_reads_inconclusive():
    block = doctor([hb._finding("guest-exec", "guest-agent-reset", "x")])
    assert block["verdict"] == "INCONCLUSIVE"
    assert "guest-agent-reset" in block["reason"]


def test_a_reset_beside_a_real_fault_reads_invalid():
    block = doctor([hb._finding("guest-exec", "guest-agent-reset", "x"),
                    hb._finding("hookscript", "hookscript-missing", "y")])
    assert block["verdict"] == "INVALID"


def test_a_wedged_agent_still_reads_invalid():
    assert doctor([hb._finding("guest-exec", "guest-agent-wedged", "x")])["verdict"] == "INVALID"


def test_the_production_wiring_hands_the_doctor_all_three_helpers(monkeypatch):
    seen = {}

    def fake(vmid, expect_cat, *runners, **kw):
        seen.update(kw)
        return {"checks_run": 3, "checks_total": 3, "findings": [], "cat": None}

    monkeypatch.setattr(hb, "build_doctor_raw", fake)
    hb.build_production_checks({"vmid": 100, "expect_cat": False})["doctor"]()
    assert seen == {"reset_agent": hb._qga_reset, "note_reset": hb._note_reset,
                    "take_reset_note": hb._take_reset_note}


def test_an_envelope_with_a_reset_finding_validates_against_the_real_schema():
    now = datetime(2026, 10, 10, 2, 5, 0, tzinfo=timezone.utc)
    checks = {
        "now": lambda: now,
        "uptime_s": lambda: 1000.0,
        "versions": lambda: {"pveversion": "pve-manager/9.1.1", "kernel": "6.14.0"},
        "doctor": lambda: hb.build_doctor_raw(
            100, False, lambda vmid: rr(0), lambda vmid: RUNNING,
            lambda vmid: rr(0, "hookscript: local:snippets/cpu-pin-100.sh\n"),
            lambda: {"present": False, "cpus_list": None, "schemata": None},
            take_reset_note=lambda: "2026-10-10T02:03:04Z panel"),
        "resources": lambda: hb.build_resources_raw(
            lambda: (0.1, 0.1, 0.1), lambda path: 10.0, lambda: 1000.0, lambda: None),
    }
    env = hb.assemble({"station": "b4-pm", "interval_sec": 300}, checks)
    assert heartbeat_schema.validate(env) == []
    assert env["blocks"]["doctor"]["verdict"] == "INCONCLUSIVE"


# -- pm-align: one reset before it gives up on the VM's copy ------------------------------

class Completed:
    def __init__(self, rc, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def push(tmp_path, monkeypatch, answers, reset):
    rec = tmp_path / "host-aligned.json"
    rec.write_text("{}")
    log, answers = [], list(answers)
    monkeypatch.setattr(pa, "_qga_reset", lambda vmid: log.append("reset") or reset)
    monkeypatch.setattr(pa, "_note_reset", lambda who: log.append(f"note {who}"))

    def run(argv, **kw):
        log.append("exec")
        return answers.pop(0)

    return pa.push_record(rec, 100, run=run), log


AGENT_DOWN = Completed(255, "", "QEMU guest agent is not running\n")
PUSHED = Completed(0, '{"exitcode": 0}')


def test_pm_align_resets_a_deaf_agent_and_copies_the_record(tmp_path, monkeypatch):
    ok, log = push(tmp_path, monkeypatch, [AGENT_DOWN, PUSHED], reset=True)
    assert ok is True
    assert log == ["exec", "reset", "note pm-align", "exec"]


def test_pm_align_gives_up_when_the_reset_brings_no_answer(tmp_path, monkeypatch):
    ok, log = push(tmp_path, monkeypatch, [AGENT_DOWN], reset=False)
    assert ok is False
    assert log == ["exec", "reset"]


def test_pm_align_sends_no_reset_for_another_failure(tmp_path, monkeypatch):
    ok, log = push(tmp_path, monkeypatch, [Completed(0, '{"exitcode": 1}')], reset=True)
    assert ok is False
    assert log == ["exec"]


def test_pm_align_sends_no_reset_when_the_copy_works(tmp_path, monkeypatch):
    ok, log = push(tmp_path, monkeypatch, [PUSHED], reset=True)
    assert ok is True
    assert log == ["exec"]

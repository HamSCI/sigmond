"""What the wizard sends to the guest agent (tasks/plan-guest-agent-requests.md).

Proxmox writes a guest-agent request that holds non-ASCII characters short,
and about one time in six the agent then stands deaf for good.  So `gexec`
and `guest_out` send every command base64-encoded, and a wizard that finds
the agent silent resets its parser once before it waits.

These tests run the wizard's own functions, cut out of the shipped script by
name.  A stub `qm` plays Proxmox and the guest: it records every argument and
runs the request in a local shell, so a command's effect is real.
"""
import os
import re
import socket
import subprocess
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WIZARD = Path(os.environ.get("SIGMOND_TEST_WIZARD") or REPO / "scripts" / "proxmox" / "sigmond-wizard.sh")
TEXT = WIZARD.read_text()

#: The bytes every reset sends.  tests/test_qga_reset.py holds the four
#: programs' copies equal to this literal.
RESET = b"\xff" + b'{"execute":"guest-sync-delimited","arguments":{"id":4242}}\n'
SENDERS = ("qga_b64", "qga_reset", "qga_note_reset", "gexec", "guest_out")


def shell_func(name: str) -> str:
    out, keep = [], False
    for ln in TEXT.splitlines(True):
        if ln.startswith(f"{name}(){{") or ln.startswith(f"{name}() {{"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}" or (len(out) == 1 and ln.rstrip().endswith("}")):
                break
    assert out, f"{name} not found in the wizard"
    return "".join(out)


def shell_var(name: str) -> str:
    m = re.search(rf"^{name}=.*$", TEXT, re.M)
    assert m, f"the wizard sets no {name}"
    return m.group(0) + "\n"


def harness(tmp_path: Path) -> str:
    """The senders, with a stub qm that runs each request in a local shell.

    Scene files under tmp_path: `deaf` (the agent answers nothing), `busy`
    (a request times out while the agent still answers a ping), `calls/` (one
    file per qm call, arguments NUL-separated)."""
    (tmp_path / "calls").mkdir(exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    return f"""
set -u
VMID=100
LOG={tmp_path}/wizard.log
export SIGMOND_QGA_DIR={tmp_path}/qga SIGMOND_QGA_NOTE={tmp_path}/run/qga-reset
say(){{ echo "say: $*"; }}
sleep(){{ echo "$1" >> {tmp_path}/sleeps; }}
timeout(){{ shift; "$@"; }}
qm(){{
    printf '%s\\0' "$@" > "{tmp_path}/calls/$(date +%s%N)"
    case "$1" in
        agent) [ -e {tmp_path}/deaf ] && return 255; return 0 ;;
        guest)
            [ -e {tmp_path}/deaf ] && {{ echo "QEMU guest agent is not running" >&2; return 255; }}
            [ -e {tmp_path}/busy ] && {{ echo "VM 100 qmp command 'guest-exec' failed - got timeout" >&2; return 255; }}
            shift 3; [ "$1" = --timeout ] && shift 2; [ "$1" = -- ] && shift
            local o rc
            o=$(HOME={tmp_path}/home "$@" 2>/dev/null </dev/null); rc=$?
            python3 -c 'import json,sys; print(json.dumps({{"exitcode":int(sys.argv[1]),"exited":1,"out-data":sys.argv[2]}}))' "$rc" "$o"
            ;;
    esac
}}
{shell_var("QGA_RUN")}{shell_var("QGA_DIR")}{shell_var("QGA_NOTE")}
{"".join(shell_func(n) for n in SENDERS)}
"""


def bash(script, env=None):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, **(env or {})))


def calls(tmp_path: Path):
    return [p.read_bytes() for p in sorted((tmp_path / "calls").iterdir())]


def guest_execs(tmp_path: Path):
    return [c for c in calls(tmp_path) if c.startswith(b"guest\0exec\0")]


# -- the command arrives whole, and the request stays ASCII ------------------------------

COMMANDS = [
    "echo hello",
    "exit 3",
    "echo \"it's quoted\" && echo $HOME",
    "printf '%s\\n' \"na\u00efve \u2014 \u2713 \u00bb \u2500\u2500\u2500\"",
    "for l in /sys/class/net/lo*; do echo $(basename $l); done",
    "false",
    "nosuchcommand_xyz",
    "cat <<EOT\nheredoc $((1+2))\nEOT",
    "f(){ return 7; }; f; echo rc=$?",
    'echo "$0"',
    "set -e; true; echo after",
    "read x; echo \"[$x]\"",
    "mkdir -p /tmp && { echo '[Match]'; echo 'Name=en*'; } | wc -l && for l in a b; do echo \\$(basename $l); done",
]


@pytest.mark.parametrize("cmd", COMMANDS, ids=range(len(COMMANDS)))
def test_guest_out_gives_what_the_direct_form_gave(tmp_path, cmd):
    """The encoded request runs the same command in the same kind of shell."""
    direct = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=30,
                            stdin=subprocess.DEVNULL, env=dict(os.environ, HOME=str(tmp_path / "home")))
    r = bash(harness(tmp_path) + 'guest_out 15 "$CMD"\n', env={"CMD": cmd})
    assert r.stdout == (direct.stdout.rstrip("\n") + "\n")


@pytest.mark.parametrize("cmd", COMMANDS, ids=range(len(COMMANDS)))
def test_gexec_answers_with_the_commands_own_exit(tmp_path, cmd):
    direct = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=30,
                            stdin=subprocess.DEVNULL, env=dict(os.environ, HOME=str(tmp_path / "home")))
    r = bash(harness(tmp_path) + 'gexec 15 "$CMD"; echo "rc=$?"\n', env={"CMD": cmd})
    assert r.stdout.splitlines()[-1] == ("rc=0" if direct.returncode == 0 else "rc=1")


@pytest.mark.parametrize("sender", ["gexec", "guest_out"])
@pytest.mark.parametrize("cmd", COMMANDS, ids=range(len(COMMANDS)))
def test_no_request_holds_a_byte_above_0x7f(tmp_path, sender, cmd):
    bash(harness(tmp_path) + f'{sender} 15 "$CMD"\n', env={"CMD": cmd})
    sent = guest_execs(tmp_path)
    assert sent, "the sender made no request"
    for c in sent:
        assert all(b < 0x80 for b in c), c


def test_an_operators_answer_with_an_accent_reaches_the_guest_whole(tmp_path):
    """The case the encoding exists for: typed text inside a command."""
    name = "Jos\u00e9 \u00c5ngstr\u00f6m \u2014 KD\u00d8XYZ"
    out = tmp_path / "answer"
    r = bash(harness(tmp_path) + f'gexec 15 "printf %s \\"$NAME\\" > {out}"; echo "rc=$?"\n', env={"NAME": name})
    assert r.stdout.splitlines()[-1] == "rc=0"
    assert out.read_text() == name
    assert all(b < 0x80 for c in guest_execs(tmp_path) for b in c)


def test_the_command_arrives_as_an_argument_not_on_stdin(tmp_path):
    (req,) = (bash(harness(tmp_path) + 'gexec 15 "echo one"\n'), )
    (c,) = guest_execs(tmp_path)
    args = c.rstrip(b"\0").split(b"\0")
    assert args[:6] == [b"guest", b"exec", b"100", b"--timeout", b"15", b"--"]
    assert args[6:9] == [b"bash", b"-lc", b'eval "$(printf %s "$1" | base64 -d)"']
    assert args[9] == b"bash" and len(args) == 11
    import base64
    assert base64.b64decode(args[10]) == b"echo one"


def test_a_command_that_exits_nonzero_is_not_sent_twice(tmp_path):
    r = bash(harness(tmp_path) + 'gexec 15 "exit 4"; echo "rc=$?"\n')
    assert r.stdout.splitlines()[-1] == "rc=1"
    assert len(guest_execs(tmp_path)) == 1


# -- a silent agent: one reset, then the wait ---------------------------------------------

class Agent:
    """The agent's side of the socket: records what arrives, answers the sync,
    and from then on the scene's agent hears again."""

    def __init__(self, tmp_path: Path):
        self.tmp, self.got = tmp_path, b""
        (tmp_path / "qga").mkdir()
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(str(tmp_path / "qga" / "100.qga"))
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
        except OSError:
            pass
        (self.tmp / "deaf").unlink(missing_ok=True)
        c.sendall(b"\xff" + b'{"return": 4242}\n')
        c.close()


def test_a_deaf_agent_gets_one_reset_and_the_step_then_runs(tmp_path):
    (tmp_path / "deaf").touch()
    agent = Agent(tmp_path)
    out = tmp_path / "ran"
    r = bash(harness(tmp_path) + f'gexec 15 "echo done > {out}"; echo "rc=$?"\n')
    agent.t.join(5)
    assert agent.got == RESET
    assert r.stdout.splitlines()[-1] == "rc=0"
    assert out.read_text() == "done\n"
    assert "guest agent stopped answering" in r.stdout
    note = (tmp_path / "run" / "qga-reset").read_text()
    assert re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ wizard\n", note)
    assert not (tmp_path / "sleeps").exists(), "an agent that hears again costs no wait"


def test_an_agent_that_stays_silent_fails_the_step_and_leaves_no_record(tmp_path):
    (tmp_path / "deaf").touch()
    r = bash(harness(tmp_path) + 'gexec 15 "echo never"; echo "rc=$?"\n')
    assert r.stdout.splitlines()[-1] == "rc=1"
    assert not (tmp_path / "run" / "qga-reset").exists()
    assert (tmp_path / "sleeps").read_text().count("\n") == 24
    assert len(guest_execs(tmp_path)) == 2, "the step is tried once more after the wait, and no further"


def test_a_request_that_times_out_under_a_hearing_agent_brings_no_reset(tmp_path):
    """The record must mean: silent, then answered after a reset.  An agent
    that answers its ping was never silent."""
    (tmp_path / "busy").touch()
    agent = Agent(tmp_path)
    r = bash(harness(tmp_path) + 'gexec 15 "echo x"; echo "rc=$?"\n')
    agent.srv.close()
    assert r.stdout.splitlines()[-1] == "rc=1"
    assert agent.got == b""
    assert not (tmp_path / "run" / "qga-reset").exists()


def test_a_hearing_agent_gets_no_reset(tmp_path):
    agent = Agent(tmp_path)
    bash(harness(tmp_path) + 'gexec 15 "true"; guest_out 15 "echo x"\n')
    agent.srv.close()
    assert agent.got == b""
    assert not (tmp_path / "run" / "qga-reset").exists()


# -- the wizard's own wait for the agent: the reset first, the ssh restart second ---------

def agent_wait(tmp_path: Path) -> str:
    """The wizard's wait for the agent, cut out by its markers, in a subshell
    (it exits on failure).  Scene files: `deaf`, `ssh-heals` (a restart over
    ssh brings the agent back)."""
    m = re.search(r"^# >>> agent_wait[^\n]*\n(.*?)^# <<< agent_wait", TEXT, re.S | re.M)
    assert m, "no agent_wait block between its markers in the wizard"
    return f"""
set -u
VMID=100
export SIGMOND_QGA_DIR={tmp_path}/qga SIGMOND_QGA_NOTE={tmp_path}/run/qga-reset
C={tmp_path}/calls.txt
say(){{ echo "say: $*"; }}
sleep(){{ echo "sleep $1" >> $C; }}
timeout(){{ shift; "$@"; }}
mgmt_vm_ip(){{ echo 10.99.0.2; }}
ssh(){{ echo "ssh" >> $C; [ -e {tmp_path}/ssh-heals ] && rm -f {tmp_path}/deaf; return 0; }}
qm(){{
    echo "qm $1 ${{3:-}}" >> $C
    case "$1" in agent) [ -e {tmp_path}/deaf ] && return 255;; esac
    return 0
}}
{shell_var("QGA_DIR")}{shell_var("QGA_NOTE")}{shell_func("qga_reset")}{shell_func("qga_note_reset")}
( {m.group(1)}
  echo "went on" )
echo "rc=$?"
"""


def wait_calls(tmp_path):
    return (tmp_path / "calls.txt").read_text().splitlines()


def test_an_agent_that_answers_costs_the_wizard_no_wait(tmp_path):
    r = bash(agent_wait(tmp_path))
    assert r.stdout.splitlines()[-2:] == ["went on", "rc=0"]
    assert wait_calls(tmp_path) == ["qm agent ping", "qm agent ping"]


def test_the_wizard_resets_a_deaf_agent_before_it_restarts_anything(tmp_path):
    (tmp_path / "deaf").touch()
    agent = Agent(tmp_path)
    r = bash(agent_wait(tmp_path))
    agent.t.join(5)
    assert agent.got == RESET
    assert "answered after one parser reset" in r.stdout
    assert r.stdout.splitlines()[-2:] == ["went on", "rc=0"]
    calls = wait_calls(tmp_path)
    assert "ssh" not in calls
    assert calls.count("sleep 5") == 1, "one wait before the reset, none after it"
    assert re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ wizard\n",
                        (tmp_path / "run" / "qga-reset").read_text())


def test_the_restart_over_ssh_stays_as_the_second_resort(tmp_path):
    (tmp_path / "deaf").touch()
    (tmp_path / "ssh-heals").touch()
    r = bash(agent_wait(tmp_path))
    assert r.stdout.splitlines()[-2:] == ["went on", "rc=0"]
    calls = wait_calls(tmp_path)
    assert calls.count("ssh") == 1
    assert calls.index("ssh") > 6, "the restart comes after 30 s of silence, as before"
    assert not (tmp_path / "run" / "qga-reset").exists()


def test_an_agent_that_never_answers_ends_the_wizard(tmp_path):
    (tmp_path / "deaf").touch()
    r = bash(agent_wait(tmp_path))
    assert "not answering after 300s" in r.stdout
    assert r.stdout.splitlines()[-1] == "rc=1"
    assert wait_calls(tmp_path).count("sleep 5") == 60


# -- no other request in the wizard may carry a variable's text ---------------------------

CALL = re.compile(r"(?:^[ \t]*|\$\(|\bif\s+(?:!\s+)?|&&\s*|\|\|\s*|timeout\s+\d+\s+)qm guest exec\b", re.M)


def statement(text, start):
    """From `start` to the end of that one command: the first newline, `;`,
    `|` or `&` that stands outside every quote and every $( ), and after no
    backslash.  What a `&&` joins on is the host's own next command."""
    stack, i = [], start
    while i < len(text):
        ch = text[i]
        top = stack[-1] if stack else None
        if top == "'":
            if ch == "'":
                stack.pop()
        elif ch == "\\":
            i += 1
        elif text.startswith("$(", i):
            stack.append("$(")
            i += 1
        elif top == '"':
            if ch == '"':
                stack.pop()
        elif ch in "'\"":
            stack.append(ch)
        elif ch == ")" and top == "$(":
            stack.pop()
        elif ch in "\n;|" and not stack:
            break
        elif ch == "&" and not stack and text[i - 1] != ">" and text[i + 1:i + 2] != ">":
            break
        i += 1
    return text[start:i]


def direct_requests():
    """Every `qm guest exec` that stands as a command outside the two senders."""
    spans = []
    for name in ("gexec", "guest_out"):
        at = TEXT.index(shell_func(name))
        spans.append((at, at + len(shell_func(name))))
    return [statement(TEXT, m.start()) for m in CALL.finditer(TEXT)
            if not any(a <= m.start() < b for a, b in spans)]


def test_every_direct_request_in_the_wizard_is_ascii_and_counted():
    """Four requests stand outside the two senders, each with fixed text: the
    USB list, the VM's host key, its address, and sshd's settings after a
    refused login.  A fifth must go through a sender, or be added here with
    its reason.  The total counts prose too (two lines of advice), so that a
    request written in a form this test does not know still stops a reader."""
    direct = direct_requests()
    for s in direct:
        assert s.isascii(), s
    assert len(direct) == 4, "\n---\n".join(direct)
    assert TEXT.count("qm guest exec") == 8, "a new mention of `qm guest exec`: is it a request?"

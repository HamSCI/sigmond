"""Every smd the wizard sends runs in a transient unit of its own (v3.71).

sigmond caps qemu-guest-agent.service at MemoryMax=128M, and the cap covers
every process a request starts.  One smd needs about 55 MB; two pass the cap.
So the wizard's five smd requests go through gexec_unit, which wraps each in
`systemd-run --wait --pipe`: the command runs outside the agent's cgroup, its
output still comes back through the request, and its exit still decides.

The stub qm from tests/test_wizard_guest_senders.py runs each request in a
local shell; a stand-in systemd-run records its arguments and runs what it
was handed.
"""
import re
from pathlib import Path

import pytest

from tests.test_wizard_guest_senders import (
    TEXT, bash, guest_execs, harness, shell_func, shell_var)

FIVE = [
    ("30", "profile-probe", "smd config profile-install --help >/dev/null 2>&1"),
    ("30", "profile-install", "smd config profile-install /etc/sigmond/site-profile.toml.new"),
    ("600", "personalize", "smd admin personalize --reset-identity --yes"),
    ("600", "render", "smd config render"),
    ("90", "psws-verify", "smd psws verify"),
]


def unit_harness(tmp_path: Path) -> str:
    sdrun = tmp_path / "systemd-run"
    sdrun.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\0' \"$@\" > \"{tmp_path}/sdrun.$(date +%s%N)\"\n"
        "while [ $# -gt 0 ]; do case \"$1\" in --*) shift ;; *) break ;; esac; done\n"
        "exec \"$@\"\n")
    sdrun.chmod(0o755)
    return (harness(tmp_path)
            + f"export SIGMOND_QGA_SDRUN={sdrun}\n"
            + shell_var("QGA_SDRUN")
            + shell_func("gexec_unit"))


def sdrun_calls(tmp_path: Path):
    return [p.read_bytes().split(b"\0")[:-1] for p in sorted(tmp_path.glob("sdrun.*"))]


@pytest.mark.parametrize("cmd,want", [("exit 0", "rc=0"), ("true", "rc=0"),
                                      ("exit 3", "rc=1"), ("false", "rc=1")])
def test_gexec_unit_answers_with_the_commands_own_exit(tmp_path, cmd, want):
    r = bash(unit_harness(tmp_path) + 'gexec_unit 15 render "$CMD"; echo "rc=$?"\n',
             env={"CMD": cmd})
    assert r.stdout.splitlines()[-1] == want


def test_the_command_runs_in_a_transient_unit_that_waits_and_pipes(tmp_path):
    bash(unit_harness(tmp_path) + 'gexec_unit 15 render "true"\n')
    (call,) = sdrun_calls(tmp_path)
    for flag in (b"--quiet", b"--wait", b"--pipe", b"--collect",
                 b"--unit=sigmond-wizard-render"):
        assert flag in call, call
    assert call[call.index(b"--unit=sigmond-wizard-render") + 1] == b"/bin/bash"


def test_the_command_arrives_whole(tmp_path):
    out = tmp_path / "out"
    cmd = f"printf '%s\\n' \"it's naïve — $((1+2))\" > {out}"
    r = bash(unit_harness(tmp_path) + 'gexec_unit 15 render "$CMD"; echo "rc=$?"\n',
             env={"CMD": cmd})
    assert r.stdout.splitlines()[-1] == "rc=0"
    assert out.read_text() == "it's naïve — 3\n"


def test_the_request_stays_ascii(tmp_path):
    bash(unit_harness(tmp_path) + 'gexec_unit 15 render "$CMD"\n',
         env={"CMD": "echo ─»"})
    sent = guest_execs(tmp_path)
    assert sent, "gexec_unit made no request"
    for c in sent:
        assert all(b < 0x80 for b in c), c


def test_each_of_the_five_smd_requests_goes_through_gexec_unit():
    for t, name, cmd in FIVE:
        assert f'gexec_unit {t} {name} "{cmd}"' in TEXT, name
    assert len(re.findall(r"\bgexec_unit\s+\d+\s", TEXT)) == 5


SENT = re.compile(r'\b(?:gexec|guest_out)\s+\d+\s+(?:"((?:[^"\\]|\\[\s\S])*)"|\'([^\']*)\')')


def test_no_smd_crosses_the_agent_outside_systemd_run():
    bad = []
    for m in SENT.finditer(TEXT):
        body = m.group(1) if m.group(1) is not None else m.group(2)
        at = re.search(r"\bsmd\b", body)
        if at and "systemd-run" not in body[:at.start()]:
            bad.append(body)
    assert not bad, "\n---\n".join(bad)

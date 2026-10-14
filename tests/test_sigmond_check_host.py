"""`sigmond-check`, the host half of the on-site check
(tasks/spec-smd-check-v372.md §3, §5, §8).

The script under test is the heredoc the wizard writes to
/usr/local/bin/sigmond-check, cut out of the shipped wizard with pm-align's
own reader.  Stubs on PATH play the VM (`sigmond-vm`), the network (`ip`,
`iptables`, `sysctl`, `systemctl`) and the browser (`curl`); a scene
directory tells each stub what to answer."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROXMOX = REPO / "scripts" / "proxmox"
_spec = importlib.util.spec_from_file_location("pm_align_for_check", PROXMOX / "pm-align.py")
pm_align = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pm_align
_spec.loader.exec_module(pm_align)

SCRIPT = pm_align.extract_heredocs((PROXMOX / "sigmond-wizard.sh").read_text())["/usr/local/bin/sigmond-check"]

VM_LINES = """\
 ok    PSK            214 FT8 and 31 FT4 spots in the last 15 min
 FAIL  magnetometer   frozen: 0.00 C, no change in 60 s.
                      Fix: sudo systemctl restart mag-recorder
 wait  uploads        off, new station: check the lines above, then run: smd sink upload
 ok    receiver page  http://127.0.0.1:8081 answers
 FAIL  station pages  http://127.0.0.1:8000 does not answer: [Errno 111] Connection refused
                      Fix: nothing here; the host line below says more
 ok    magnetometer page  http://127.0.0.1:8082 answers
"""
PSK_OK = " ok    PSK            fine\n"
V4 = "2: vmbr0    inet 192.168.1.50/24 brd 192.168.1.255 scope global vmbr0\\       valid_lft forever"
V6 = ("2: vmbr0    inet6 2601:db8::abcd/64 scope global temporary dynamic \\       valid_lft 1sec\n"
      "2: vmbr0    inet6 2601:db8::50/64 scope global dynamic mngtmpaddr \\       valid_lft 1sec")
NAT = "\n".join(["-P PREROUTING ACCEPT"] + [
    f"-A PREROUTING -p tcp -m tcp --dport {p} -j DNAT --to-destination 10.99.0.2:{p}"
    for p in (8000, 8081, 8082, 8765)])

STUBS = {
    "sigmond-vm": ('printf %s "${SIGMOND_VM_BATCH:-unset}" > "$SCENE/vm.batch"\n'
                   'readlink /proc/self/fd/0 > "$SCENE/vm.stdin"\n'
                   'cat "$SCENE/vm.out"; exit "$(cat "$SCENE/vm.rc")"'),
    "ip": 'case "$1" in -4) cat "$SCENE/ip4" ;; -6) cat "$SCENE/ip6" ;; esac',
    "iptables": 'cat "$SCENE/nat"; exit "$(cat "$SCENE/nat.rc")"',
    "sysctl": 'cat "$SCENE/fwd"',
    "curl": ('for url; do :; done\n'
             'code=$(awk -v u="$url" \'$1 == u {print $2}\' "$SCENE/curl")\n'
             'printf %s "${code:-000}"'),
    "systemctl": '[ -e "$SCENE/relay" ]',
}


def scene(tmp_path, vm=VM_LINES, vm_rc=1, ip4=V4, ip6="", nat=NAT, nat_rc=0, fwd="1",
          curl=(), relay=False):
    s = tmp_path / "scene"
    s.mkdir()
    for name, text in (("vm.out", vm), ("vm.rc", str(vm_rc)), ("ip4", ip4), ("ip6", ip6),
                       ("nat", nat), ("nat.rc", str(nat_rc)), ("fwd", fwd),
                       ("curl", "".join(f"{u} {c}\n" for u, c in curl))):
        (s / name).write_text(text)
    if relay:
        (s / "relay").write_text("")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in STUBS.items():
        (bin_ / name).write_text("#!/bin/bash\n" + body + "\n")
        (bin_ / name).chmod(0o755)
    script = tmp_path / "sigmond-check"
    script.write_text(SCRIPT)
    return s, bin_, script


def run(tmp_path, **kw):
    s, bin_, script = scene(tmp_path, **kw)
    env = dict(os.environ, PATH=f"{bin_}:/usr/bin:/bin", SCENE=str(s), LC_ALL="C.UTF-8")
    # stdin a pipe that stays open, as the installer's console would: the VM
    # call must not be able to read it.
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=60,
                       input="")
    return r.returncode, r.stdout.splitlines()


VM_200 = [(f"http://10.99.0.2:{p}/", "200") for p in (8081, 8000, 8082)]
V6_200 = [(f"http://[2601:db8::50]:{p}/", "200") for p in (8081, 8000, 8082)]


def test_the_wizard_writes_a_script_that_parses():
    assert SCRIPT.startswith("#!/bin/bash\n")
    assert subprocess.run(["bash", "-n"], input=SCRIPT, text=True).returncode == 0


def test_ipv4_only_site(tmp_path):
    rc, out = run(tmp_path, curl=VM_200)
    assert out == [
        " ok    PSK            214 FT8 and 31 FT4 spots in the last 15 min",
        " FAIL  magnetometer   frozen: 0.00 C, no change in 60 s.",
        "                      Fix: sigmond-vm sudo systemctl restart mag-recorder",
        " wait  uploads        off, new station: check the lines above, then run: sigmond-vm smd sink upload",
        " ok    receiver page  IPv4 http://192.168.1.50:8081   no IPv6 here",
        " ok    station pages  IPv4 http://192.168.1.50:8000   no IPv6 here",
        " ok    magnetometer page  IPv4 http://192.168.1.50:8082   no IPv6 here",
        "FAIL  (1 failing, 1 waiting)",
    ]
    assert rc == 1


def test_dual_stack_with_the_relay(tmp_path):
    vm = PSK_OK + " ok    magnetometer page  http://127.0.0.1:8082 answers\n"
    rc, out = run(tmp_path, vm=vm, vm_rc=0, ip6=V6, curl=VM_200 + V6_200, relay=True)
    assert out == [
        " ok    PSK            fine",
        " ok    receiver page  IPv4 http://192.168.1.50:8081   IPv6 http://[2601:db8::50]:8081",
        " ok    station pages  IPv4 http://192.168.1.50:8000   IPv6 http://[2601:db8::50]:8000",
        " ok    magnetometer page  IPv4 http://192.168.1.50:8082   IPv6 http://[2601:db8::50]:8082",
        "PASS",
    ]
    assert rc == 0


def test_dual_stack_without_the_relay(tmp_path):
    rc, out = run(tmp_path, vm=PSK_OK, vm_rc=0, ip6=V6, curl=VM_200, relay=False)
    assert out[1] == (" FAIL  receiver page  IPv4 http://192.168.1.50:8081   IPv6 http://[2601:db8::50]:8081 "
                      "does not answer: the IPv6 relay is not installed")
    assert out[-1] == "FAIL  (2 failing)"
    assert rc == 1
    # No magnetometer page line: the VM reported none.
    assert not any("magnetometer page" in ln for ln in out)


def test_vm_silent(tmp_path):
    rc, out = run(tmp_path, vm="sigmond-vm: VM 100 is not running — try: qm start 100\n",
                  vm_rc=1, curl=())
    assert out[0] == " FAIL  decoder VM     does not answer: sigmond-vm: VM 100 is not running ??? try: qm start 100"
    assert out[1] == (" FAIL  receiver page  IPv4 http://192.168.1.50:8081 does not answer: "
                      "the VM at 10.99.0.2:8081 gave no answer   no IPv6 here")
    assert out[-1] == "FAIL  (3 failing)"
    assert rc == 1


def test_a_get_on_the_hosts_own_ipv4_is_never_the_proof(tmp_path):
    # Spec §5: the host's own address answers nothing (PREROUTING), so only
    # the rule, the forwarding switch and the VM's address count.
    rc, out = run(tmp_path, vm=PSK_OK, vm_rc=0,
                  curl=[(f"http://192.168.1.50:{p}/", "200") for p in (8081, 8000)])
    assert out[1].startswith(" FAIL  receiver page  IPv4 http://192.168.1.50:8081 does not answer: the VM at")


def test_ipv4_rule_missing_or_forwarding_off(tmp_path):
    nat = NAT.replace("--dport 8081 ", "--dport 9999 ")
    rc, out = run(tmp_path, vm=PSK_OK, vm_rc=0, nat=nat, curl=VM_200)
    assert "does not answer: no relay rule for port 8081" in out[1]
    assert out[2].startswith(" ok    station pages")


def test_ipv4_forwarding_off(tmp_path):
    rc, out = run(tmp_path, vm=PSK_OK, vm_rc=0, fwd="0", curl=VM_200)
    assert "does not answer: IPv4 forwarding is off" in out[1]
    assert rc == 1


def test_relay_rules_unreadable_says_run_as_root(tmp_path):
    rc, out = run(tmp_path, vm=PSK_OK, vm_rc=0, nat="", nat_rc=4, curl=VM_200)
    assert "cannot read the relay rules (run sigmond-check as root)" in out[1]


def test_wait_only_gives_exit_2(tmp_path):
    rc, out = run(tmp_path, vm=" wait  GRAPE          first files due in about 7 min\n", vm_rc=2, curl=VM_200)
    assert out[-1] == "WAIT  (1 waiting)"
    assert rc == 2


def test_everything_it_prints_is_ascii(tmp_path):
    # The host console's font (Lat15) shows nothing past ASCII (Michael,
    # 2026-10-14).  The VM's complaint below carries an em dash on purpose.
    rc, out = run(tmp_path, vm_rc=1, ip6=V6, curl=VM_200,
                  vm="sigmond-vm: VM 100 is not running \u2014 try: qm start 100\n")
    bad = sorted({c for line in out for c in line if ord(c) >= 0x80})
    assert bad == [], out
    # Every kind of line: a VM line from an smd that still printed a degree
    # sign, its fix, a wait, and the host's own passing page lines.
    (tmp_path / "again").mkdir()
    rc, out = run(tmp_path / "again", vm=VM_LINES.replace("0.00 C", "0.00 \u00b0C"),
                  curl=VM_200 + V6_200, ip6=V6, relay=True)
    assert out[1] == " FAIL  magnetometer   frozen: 0.00 ??C, no change in 60 s."
    assert any(line.startswith(" ok    receiver page") for line in out), out
    assert sorted({c for line in out for c in line if ord(c) >= 0x80}) == [], out


# -- fix round 1 -------------------------------------------------------------
TWO_LINES = (" ok    PSK            fine\n"
             " ok    GRAPE          fine\n")


@pytest.mark.parametrize("rc, why", [(255, "ssh to the VM failed"), (124, "timed out")])
def test_partial_vm_output_cannot_pass(tmp_path, rc, why):
    code, out = run(tmp_path, vm=TWO_LINES, vm_rc=rc, curl=VM_200)
    assert out[:2] == [" ok    PSK            fine", " ok    GRAPE          fine"]
    assert out[2].startswith(" FAIL  decoder VM     did not finish (")
    assert why in out[2]
    assert out[-1].startswith("FAIL")
    assert code == 1


def test_old_vm_without_smd_check(tmp_path):
    err = "smd: error: argument command: invalid choice: 'check'\n"
    code, out = run(tmp_path, vm=err, vm_rc=2, curl=VM_200)
    assert out[0] == " FAIL  decoder VM     answered, but has no smd check (update the VM)"
    assert code == 1


@pytest.mark.parametrize("rc", [127])
def test_iptables_missing(tmp_path, rc):
    code, out = run(tmp_path, vm=PSK_OK, vm_rc=0, nat="", nat_rc=rc, curl=VM_200)
    assert "does not answer: iptables not found" in out[1]
    assert "run sigmond-check as root" not in out[1]
    assert code == 1


def _v6(*addrs):
    return "\n".join(f"2: vmbr0    inet6 {a}/64 scope global {flags} \\       valid_lft 1sec"
                     for a, flags in addrs)


def test_ipv6_prefers_gua_over_ula(tmp_path):
    ip6 = _v6(("fd00::7", "dynamic mngtmpaddr"), ("2601:db8::50", "dynamic mngtmpaddr"))
    code, out = run(tmp_path, vm=PSK_OK, vm_rc=0, ip6=ip6, curl=VM_200 + V6_200, relay=True)
    assert "IPv6 http://[2601:db8::50]:8081" in out[1]


def test_ipv6_takes_a_ula_when_it_is_all_there_is(tmp_path):
    ip6 = _v6(("fd00::7", "dynamic mngtmpaddr"))
    code, out = run(tmp_path, vm=PSK_OK, vm_rc=0, ip6=ip6,
                    curl=VM_200 + [("http://[fd00::7]:8081/", "200")], relay=True)
    assert "IPv6 http://[fd00::7]:8081" in out[1] and "no IPv6 here" not in out[1]


def test_ipv6_falls_back_to_a_temporary_address(tmp_path):
    ip6 = _v6(("2601:db8::abcd", "temporary dynamic"))
    code, out = run(tmp_path, vm=PSK_OK, vm_rc=0, ip6=ip6,
                    curl=VM_200 + [("http://[2601:db8::abcd]:8081/", "200")], relay=True)
    assert "IPv6 http://[2601:db8::abcd]:8081" in out[1] and "no IPv6 here" not in out[1]


# -- final review I4: never wait at an ssh password prompt -------------------
SIGMOND_VM = pm_align.extract_heredocs((PROXMOX / "sigmond-wizard.sh").read_text())["/usr/local/bin/sigmond-vm"]


def test_sigmond_check_asks_sigmond_vm_for_batch_mode(tmp_path):
    run(tmp_path, vm=PSK_OK, vm_rc=0, curl=VM_200)
    assert (tmp_path / "scene" / "vm.batch").read_text() == "1"


def _sigmond_vm(tmp_path, batch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    stubs = {
        "qm": ('case "$1" in status) echo "status: running" ;;\n'
               '  agent) echo \'[{"ip-address" : "10.99.0.2"}]\' ;; esac'),
        "ssh": 'printf "%s\\n" "$@" > "$ARGS"',
    }
    for name, body in stubs.items():
        (bin_ / name).write_text("#!/bin/bash\n" + body + "\n")
        (bin_ / name).chmod(0o755)
    script = tmp_path / "sigmond-vm"
    script.write_text(SIGMOND_VM)
    args = tmp_path / "ssh.args"
    env = dict(os.environ, PATH=f"{bin_}:/usr/bin:/bin", SIGMOND_VMID="100", ARGS=str(args))
    env.pop("SIGMOND_VM_BATCH", None)
    if batch:
        env["SIGMOND_VM_BATCH"] = "1"
    r = subprocess.run(["bash", str(script), "smd", "check"], capture_output=True, text=True,
                       env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    return args.read_text().splitlines()


def test_sigmond_vm_batch_mode_never_prompts(tmp_path):
    argv = _sigmond_vm(tmp_path, batch=True)
    host = argv.index("sigmond@10.99.0.2")
    opts = argv[:host]
    assert "BatchMode=yes" in opts and "ConnectTimeout=5" in opts
    assert argv[host + 1:] == ["smd", "check"]


def test_sigmond_vm_stays_interactive_by_default(tmp_path):
    argv = _sigmond_vm(tmp_path, batch=False)
    assert "BatchMode=yes" not in argv
    assert argv[-3:] == ["sigmond@10.99.0.2", "smd", "check"]


def test_sigmond_check_gives_the_vm_no_console_to_read(tmp_path):
    run(tmp_path, vm=PSK_OK, vm_rc=0, curl=VM_200)
    assert (tmp_path / "scene" / "vm.stdin").read_text().strip() == "/dev/null"


# -- final review m4: the two halves agree on the line format ----------------
def test_lines_smd_check_renders_parse_on_the_host(tmp_path):
    """smd check renders, sigmond-check parses: a format change in one half
    fails here, not at a console."""
    from sigmond.commands import check
    from sigmond.commands.check import ABSENT, FAIL, OK, WAIT, Line
    vm_lines = [
        Line(WAIT, 'bring-up', 'still running (started 7 min ago)'),
        Line(FAIL, 'PSK', 'psk-recorder is failed.  Fix: smd start psk-recorder'),
        Line(FAIL, 'a label of 15+ chars', 'it broke.  Fix: sudo systemctl restart x'),
        Line(WAIT, 'uploads', 'off, new station: check the lines above, then run: smd sink upload'),
        Line(OK, 'magnetometer', 'samples arriving, 23.1 °C'),
        Line(ABSENT, 'meteor scatter', 'not on this station'),
        Line(OK, 'receiver page', 'http://127.0.0.1:8081 answers'),
    ]
    vm = "".join(check.render_line(ln) + "\n" for ln in vm_lines)
    rc, out = run(tmp_path, vm=vm, vm_rc=1, curl=VM_200)
    assert out == [
        " wait  bring-up       still running (started 7 min ago)",
        " FAIL  PSK            psk-recorder is failed.",
        "                      Fix: sigmond-vm smd start psk-recorder",
        " FAIL  a label of 15+ chars  it broke.",
        "                      Fix: sigmond-vm sudo systemctl restart x",
        " wait  uploads        off, new station: check the lines above, then run: sigmond-vm smd sink upload",
        " ok    magnetometer   samples arriving, 23.1 ?C",
        " --    meteor scatter not on this station",
        " ok    receiver page  IPv4 http://192.168.1.50:8081   no IPv6 here",
        " ok    station pages  IPv4 http://192.168.1.50:8000   no IPv6 here",
        "FAIL  (2 failing, 2 waiting)",
    ]
    assert rc == 1


def test_the_sigmond_check_heredoc_is_pure_ascii():
    # sigmond CLAUDE.md: no byte above 0x7F where a guest-agent request may carry it.
    assert all(ord(c) < 0x80 for c in SCRIPT)

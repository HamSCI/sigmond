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
    "sigmond-vm": 'cat "$SCENE/vm.out"; exit "$(cat "$SCENE/vm.rc")"',
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
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=60)
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

"""The vm-port-relay's _mgmt_peer, cut out of the wizard's RLEOF heredoc.

vmbr1 is a /29 since 2026-11 (host .1, decoder VM .2, a radiod-only VM .3)
and a /30 on hosts installed before.  With a /30-only match, AI6VN-PM
(2026-11-01) got None on its hand-made /29 and every RAC web connection fell
back to `qm agent` / `qm config`, holding the host's CPUs 10-11 at 100 %.
The CLAT's 192.0.0.1/29 must never be taken for the link.
"""
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"
TEXT = WIZARD.read_text()


def relay_source() -> str:
    m = re.search(r"<<'RLEOF'\n(.*?)\nRLEOF\n", TEXT, re.S)
    assert m, "vm-port-relay.py heredoc not found in the wizard"
    return m.group(1)


def mgmt_peer(ip_out: str):
    src = relay_source()
    start = src.index("def _mgmt_peer():")
    end = src.index("_peer = _mgmt_peer()")
    import socket, struct

    class FakeSubprocess:
        @staticmethod
        def run(argv, **kw):
            assert argv[:3] == ["ip", "-4", "-o"]
            return subprocess.CompletedProcess(argv, 0, stdout=ip_out, stderr="")

    ns = {"re": re, "socket": socket, "struct": struct, "subprocess": FakeSubprocess}
    exec(src[start:end], ns)
    return ns["_mgmt_peer"]()


def line(n, dev, cidr):
    return f"{n}: {dev}    inet {cidr} brd 0.0.0.0 scope global {dev}\\       valid_lft forever preferred_lft forever\n"


LO = line(1, "lo", "127.0.0.1/8")
VMBR0 = line(2, "vmbr0", "192.168.8.20/24")
CLAT = line(9, "clat", "192.0.0.1/29")


@pytest.mark.parametrize("cidr", ["10.99.0.1/29", "10.99.0.1/30"])
def test_the_decoder_vm_is_dot_two_on_either_size(cidr):
    assert mgmt_peer(LO + VMBR0 + line(3, "vmbr1", cidr)) == "10.99.0.2"


def test_the_clat_29_listed_first_is_not_the_link():
    assert mgmt_peer(LO + CLAT + VMBR0 + line(3, "vmbr1", "10.99.0.1/29")) == "10.99.0.2"


def test_a_29_off_vmbr1_is_never_taken():
    assert mgmt_peer(LO + CLAT + VMBR0) is None


def test_a_29_whose_first_address_is_not_ours_is_not_guessed():
    assert mgmt_peer(LO + line(3, "vmbr1", "10.99.0.3/29")) is None


def test_a_30_elsewhere_still_counts_when_vmbr1_has_none():
    # The pre-existing fallback: any /30, flipped.
    assert mgmt_peer(LO + line(4, "wg0", "172.16.5.2/30")) == "172.16.5.1"


def test_vmbr1_wins_over_another_30_listed_first():
    assert mgmt_peer(LO + line(2, "wg0", "172.16.5.1/30")
                     + line(3, "vmbr1", "10.99.0.1/29")) == "10.99.0.2"


def test_no_link_gives_none():
    assert mgmt_peer(LO + VMBR0) is None


# -- the wizard's own mgmt_vm_ip, which the --pair-vm repair uses ---------------

def shell_func(name: str) -> str:
    out, keep = [], False
    for ln in TEXT.splitlines(True):
        if ln.startswith(f"{name}() {{") or ln.startswith(f"{name}(){{"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, f"{name} not found"
    return "".join(out)


def mgmt_vm_ip(vmbr1: str, all_lines: str) -> str:
    stub = textwrap.dedent(f"""
        ip() {{
            if [ "$*" = "-4 -o addr show dev vmbr1" ]; then printf '%s' '{vmbr1}'
            else printf '%s' '{all_lines}'; fi
        }}
    """)
    script = "set -u\nunset SIGMOND_MGMT_VM\n" + stub + shell_func("mgmt_vm_ip") + "\nmgmt_vm_ip\n"
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    return p.stdout.strip()


def test_mgmt_vm_ip_reads_a_29_on_vmbr1():
    # Not 10.99.0.x: that is also the fallback, and would pass by accident.
    v = line(3, "vmbr1", "10.77.0.1/29")
    assert mgmt_vm_ip(v, CLAT + v) == "10.77.0.2"


def test_mgmt_vm_ip_never_reads_the_clat_29():
    # No vmbr1 address at all: the CLAT's /29 must not become "the VM".
    assert mgmt_vm_ip("", line(2, "clat", "192.0.0.5/29")) == "10.99.0.2"

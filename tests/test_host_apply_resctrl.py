"""host-apply.sh: decoder pCPUs + MBA cap reach sigmond-host-resctrl.sh, and
an active resctrl unit is never restarted.

host-apply itself needs a Proxmox host; the pieces are tested in isolation:
vcpus_to_pcpus is extracted and run, the unit/guard text is checked.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HA = REPO / "scripts" / "proxmox" / "host-apply.sh"
TEXT = HA.read_text()


def _fn():
    m = re.search(r"^vcpus_to_pcpus\(\) \{\n.*?^\}\n", TEXT, re.S | re.M)
    assert m, "vcpus_to_pcpus not found in host-apply.sh"
    return m.group(0)


def _map(vcpus, v2p):
    script = _fn() + f'vcpus_to_pcpus "{vcpus}" "{v2p}"\n'
    r = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_identity_layout_5560u_worker_pcpus():
    # KFS-OMNI-SW-PM shape: 12 threads, radiods on 6,7 and 8,9, workers
    # 0-5, the host's fence pair 10,11 outside the VM.
    v2p = "0 1 2 3 4 5 6 7 8 9"
    assert _map("0 1 2 3 4 5", v2p) == "0,1,2,3,4,5"
    assert _map("6 7 8 9", v2p) == "6,7,8,9"


def test_split_layout_maps_through_the_table():
    # split pairs {0,8},{1,9},...: guest vCPUs 0,1 sit on pCPUs 0,8.
    v2p = "0 8 1 9 2 10 3 11"
    assert _map("4 5 6 7", v2p) == "2,3,10,11"


def test_unit_passes_resctrl_args_with_decoders_only_when_capped():
    assert "ExecStart=/usr/local/sbin/sigmond-host-resctrl.sh ${RESCTRL_ARGS}" in TEXT
    assert ('RESCTRL_ARGS+=" ${DECODER_PCPUS} ${DECODER_MB_GBPS}"') in TEXT
    assert 'DECODER_PCPUS="$(vcpus_to_pcpus "${WORKER_CPUS}" "${VCPU_TO_PCPU}")"' in TEXT
    assert ': "${DECODER_MB_GBPS:=}"' in TEXT


def test_active_resctrl_unit_is_not_restarted():
    block = TEXT.split("systemctl is-active --quiet sigmond-host-resctrl.service", 1)
    assert len(block) == 2, "no is-active guard before the resctrl unit"
    active_branch = block[1].split("else", 1)[0]
    assert "--now" not in active_branch
    assert "restart" not in active_branch.replace("a live restart", "")
    assert "systemctl restart sigmond-host-resctrl" not in TEXT


def test_bootstrap_passes_the_cap():
    bs = (REPO / "scripts" / "proxmox" / "bootstrap.sh").read_text()
    assert "DECODER_MB_GBPS=" in bs
    assert "sigmond.cpu_profiles" in bs


def test_host_discover_emits_cpu_model():
    hd = (REPO / "scripts" / "proxmox" / "host-discover.sh").read_text()
    assert "emit CPU_MODEL" in hd

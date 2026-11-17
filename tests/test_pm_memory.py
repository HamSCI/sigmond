"""Tests for scripts/proxmox/pm-memory.py: the host's graphics carve-out and
the decoder VM's memory against the install rule.

Every Beelink PM shipped with 3-4 GB of its 16 GB reserved for the
integrated GPU (BIOS UMA frame buffer).  N8UR, 2026-11-17: the operator set it
to 64 MB, the host then saw 15.5 GB, and VM 100 still held 9832 MB until a
`qm set`.  pm-memory reports both and, with --apply, queues the resize; it
never restarts the VM and never lowers its memory.  The module has a dash in
its name and imports nothing from sigmond, so it loads by path.  No test runs
qm: every host call goes through a fake.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "pm_memory", REPO / "scripts" / "proxmox" / "pm-memory.py")
pmm = importlib.util.module_from_spec(_spec)
sys.modules["pm_memory"] = pmm        # @dataclass looks its module up
_spec.loader.exec_module(pmm)

MIB = 1024 * 1024


def _host(tmp_path, *, memtotal_mb, vram_mb=None, vmid=None):
    (tmp_path / "proc").mkdir()
    (tmp_path / "proc" / "meminfo").write_text(
        f"MemTotal:       {memtotal_mb * 1024} kB\nMemFree:        1000 kB\n")
    if vram_mb is not None:
        dev = tmp_path / "sys" / "class" / "drm" / "card0" / "device"
        dev.mkdir(parents=True)
        (dev / "mem_info_vram_total").write_text(f"{vram_mb * MIB}\n")
    if vmid is not None:
        unit = tmp_path / "etc" / "systemd" / "system"
        unit.mkdir(parents=True)
        (unit / "sigmond-vm-ssh-relay@.service").write_text(
            f"[Service]\nEnvironment=SIGMOND_VMID={vmid}\n")
    return tmp_path


class FakeQm:
    def __init__(self, memory_mb):
        self.memory_mb = memory_mb
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[:2] == ["qm", "config"]:
            out = "cores: 7\n" + (f"memory: {self.memory_mb}\n" if self.memory_mb else "")
            return subprocess.CompletedProcess(argv, 0, out, "")
        if argv[:2] == ["qm", "set"]:
            self.memory_mb = int(argv[argv.index("--memory") + 1])
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected call {argv}")

    def sets(self):
        return [c for c in self.calls if c[:2] == ["qm", "set"]]


# ---- reading the host ------------------------------------------------------

def test_vram_reads_the_largest_card(tmp_path):
    _host(tmp_path, memtotal_mb=11879, vram_mb=4096)
    other = tmp_path / "sys" / "class" / "drm" / "card1" / "device"
    other.mkdir(parents=True)
    (other / "mem_info_vram_total").write_text(f"{64 * MIB}\n")
    assert pmm.vram_mb(tmp_path) == 4096


def test_vram_is_none_without_an_amdgpu(tmp_path):
    _host(tmp_path, memtotal_mb=7800)
    assert pmm.vram_mb(tmp_path) is None


def test_memtotal_in_mb(tmp_path):
    _host(tmp_path, memtotal_mb=15848)
    assert pmm.memtotal_mb(tmp_path) == 15848


def test_vm_memory_from_qm_config():
    assert pmm.vm_memory_mb(100, run=FakeQm(9832)) == 9832
    assert pmm.vm_memory_mb(100, run=FakeQm(None)) is None


# ---- the assessment ---------------------------------------------------------

def test_carveout_flagged_above_512_mb():
    a = pmm.assess(memtotal=11879, vram=4096, vm_mem=9831)
    assert a.carveout
    assert any("UMA" in line and "64 MB" in line for line in a.lines)
    assert not a.resize                      # 11879 - 2048 = 9831: nothing to gain yet


def test_small_carveout_not_flagged():
    a = pmm.assess(memtotal=15848, vram=64, vm_mem=9832)
    assert not a.carveout


def test_resize_target_is_memtotal_less_the_install_reserve():
    # N8UR after its BIOS change, before the qm set
    a = pmm.assess(memtotal=15848, vram=64, vm_mem=9832)
    assert a.target_mb == 15848 - pmm.HOST_RESERVE_MB == 13800
    assert a.resize and a.gain_mb == 13800 - 9832


def test_no_resize_for_a_small_gain():
    a = pmm.assess(memtotal=12400, vram=None, vm_mem=10000)
    assert not a.resize


def test_never_lowers_the_vm():
    a = pmm.assess(memtotal=11879, vram=4096, vm_mem=10841)   # AB4EJ-like
    assert not a.resize and a.gain_mb < 0


def test_unknown_vm_memory_gives_no_resize():
    a = pmm.assess(memtotal=15848, vram=64, vm_mem=None)
    assert not a.resize


# ---- main --------------------------------------------------------------------

def test_dry_run_reports_and_changes_nothing(tmp_path, capsys):
    _host(tmp_path, memtotal_mb=15848, vram_mb=64, vmid=100)
    qm = FakeQm(9832)
    rc = pmm.main([], root=tmp_path, run=qm, euid=0)
    out = capsys.readouterr().out
    assert rc == 1
    assert "13800" in out and "--apply" in out
    assert qm.sets() == []


def test_apply_queues_the_resize_and_never_restarts(tmp_path, capsys):
    _host(tmp_path, memtotal_mb=15848, vram_mb=64, vmid=101)
    qm = FakeQm(9832)
    rc = pmm.main(["--apply"], root=tmp_path, run=qm, euid=0)
    out = capsys.readouterr().out
    assert rc == 0
    assert qm.sets() == [["qm", "set", "101", "--memory", "13800"]]
    assert not any(c[:2] in (["qm", "reboot"], ["qm", "stop"], ["qm", "shutdown"])
                   for c in qm.calls)
    assert "next restart" in out


def test_apply_with_nothing_to_gain_sets_nothing(tmp_path):
    _host(tmp_path, memtotal_mb=11879, vram_mb=4096, vmid=100)
    qm = FakeQm(9831)
    rc = pmm.main(["--apply"], root=tmp_path, run=qm, euid=0)
    assert qm.sets() == []
    assert rc == 1                           # the carve-out still wants a person


def test_apply_refuses_without_root(tmp_path):
    _host(tmp_path, memtotal_mb=15848, vram_mb=64, vmid=100)
    qm = FakeQm(9832)
    assert pmm.main(["--apply"], root=tmp_path, run=qm, euid=1000) == 2
    assert qm.sets() == []


def test_clean_host_exits_zero(tmp_path):
    _host(tmp_path, memtotal_mb=15848, vram_mb=64, vmid=100)
    assert pmm.main([], root=tmp_path, run=FakeQm(13800), euid=0) == 0


def test_brief_prints_one_line(tmp_path, capsys):
    _host(tmp_path, memtotal_mb=11879, vram_mb=4096, vmid=100)
    pmm.main(["--brief"], root=tmp_path, run=FakeQm(9831), euid=0)
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("memory:")

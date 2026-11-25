"""Tests for scripts/proxmox/pm-firmware.py: what Linux can see of the
host's BIOS settings (docs/station-firmware.md).  The module has a dash in
its name and imports nothing from sigmond, so it loads by path.  Every test
builds a fake host under tmp_path."""

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "pm_firmware", REPO / "scripts" / "proxmox" / "pm-firmware.py")
pmf = importlib.util.module_from_spec(_spec)
sys.modules["pm_firmware"] = pmf        # @dataclass looks its module up
_spec.loader.exec_module(pmf)


def _host(root, *, kvm=True, groups=2, smt="1", clock="tsc", avail="tsc hpet acpi_pm"):
    if kvm:
        (root / "dev").mkdir()
        (root / "dev" / "kvm").write_text("")
    g = root / "sys" / "kernel" / "iommu_groups"
    g.mkdir(parents=True)
    for i in range(groups):
        (g / str(i)).mkdir()
    cpu = root / "sys" / "devices" / "system" / "cpu" / "smt"
    cpu.mkdir(parents=True)
    (cpu / "active").write_text(smt + "\n")
    cs = root / "sys" / "devices" / "system" / "clocksource" / "clocksource0"
    cs.mkdir(parents=True)
    (cs / "current_clocksource").write_text(clock + "\n")
    (cs / "available_clocksource").write_text(avail + " \n")
    dmi = root / "sys" / "class" / "dmi" / "id"
    dmi.mkdir(parents=True)
    for f, v in (("sys_vendor", "AZW"), ("product_name", "SER"), ("board_name", "SER"),
                 ("bios_vendor", "American Megatrends International, LLC."),
                 ("bios_version", "SERAL301"), ("bios_date", "04/08/2025")):
        (dmi / f).write_text(v + "\n")
    (root / "proc").mkdir()
    (root / "proc" / "cpuinfo").write_text("processor\t: 0\nmodel name\t: AMD Ryzen 7 5825U\n")
    (root / "proc" / "meminfo").write_text("MemTotal:       12164592 kB\n")
    return root


def test_a_surveyed_beelink_needs_nothing(tmp_path, capsys):
    # Every Beelink on 2026-11-25: KVM, IOMMU groups, SMT, TSC, HPET offered.
    assert pmf.main(["--brief"], root=_host(tmp_path)) == 0
    assert capsys.readouterr().out.strip() == "firmware: ok"


def test_each_must_failure_names_itself(tmp_path, capsys):
    root = _host(tmp_path, kvm=False, groups=0, smt="0", clock="hpet")
    assert pmf.main(["--brief"], root=root) == 1
    assert capsys.readouterr().out.strip() == "firmware: to do: virtualization, iommu, smt, clock"


def test_missing_hpet_is_advice_not_a_fault(tmp_path, capsys):
    # SIGMA's LattePanda offers no HPET and keeps time on the TSC.
    assert pmf.main(["--brief"], root=_host(tmp_path, avail="tsc acpi_pm")) == 0
    assert capsys.readouterr().out.strip() == "firmware: ok (advice: hpet)"


def test_report_names_the_platform(tmp_path, capsys):
    assert pmf.main(["--report"], root=_host(tmp_path)) == 0
    out = capsys.readouterr().out
    for want in ("maker: AZW", "bios version: SERAL301", "cpu: AMD Ryzen 7 5825U",
                 "memory seen: 11879 MB", "restore on AC power loss"):
        assert want in out
    assert "serial" not in out.split("\n", 1)[1]


def test_an_unreadable_host_reports_rather_than_crashes(tmp_path, capsys):
    assert pmf.main(["--report"], root=tmp_path) == 1
    assert "maker: ?" in capsys.readouterr().out

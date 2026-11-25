#!/usr/bin/env python3
"""pm-firmware — what Linux can see of the host's BIOS settings.

RUNS ON THE PM (the Proxmox host itself).  A PM runs no sigmond, no venv and
no pip, so this file is STDLIB ONLY.  pm-align installs it as
/usr/local/sbin/pm-firmware.

Why this exists
---------------
A few settings live only in the BIOS, and only a person at the console can
change them.  docs/station-firmware.md lists them by platform.  This checks
the ones whose effect Linux can see, so a station that needs a visit says so
on every alignment instead of in a crash (AC0G-B4, 2026-11-25: the graphics
reservation left the station 4 GB short, and the kernel killed the timing
recorder).

The checks
----------
- virtualization: /dev/kvm exists (SVM or VT-x on).  Must.
- iommu: the kernel formed IOMMU groups (AMD-Vi or VT-d on).  Must: the
  station VM gets the USB controllers, and so the receiver, through it.
- smt: hyper-threading active.  Must: the CPU plan pins radiod to a pair.
- clock: the kernel keeps time on the TSC.  Must.  The host's kernel command
  line (tsc=reliable processor.max_cstate=1) makes the BIOS power-saving
  settings unnecessary; a host off the TSC needs a look.
- hpet: offered as a fallback clock.  Advice only.
The graphics reservation belongs to pm-memory, which also sizes the VM.
"Restore on AC power loss" leaves no trace Linux can read; only pulling the
power cord tests it.

``--report`` prints the platform's facts (maker, model, BIOS release, CPU,
memory) with the checks, ready to paste into a report, so the platform table
in docs/station-firmware.md grows with each site.

Dry run only: it reads, prints, and never changes anything.
Exit 0 nothing to do, 1 something needs a BIOS visit.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

CLOCKSOURCE = "/sys/devices/system/clocksource/clocksource0"
DMI = "/sys/class/dmi/id"
DMI_FIELDS = (("maker", "sys_vendor"), ("model", "product_name"), ("board", "board_name"),
              ("bios vendor", "bios_vendor"), ("bios version", "bios_version"),
              ("bios date", "bios_date"))
DOC = "docs/station-firmware.md"


def _under(root: Path, path: str) -> Path:
    return root / path.lstrip("/")


def _read(root: Path, path: str) -> Optional[str]:
    try:
        return _under(root, path).read_text().strip()
    except OSError:
        return None


@dataclass
class Check:
    name: str
    ok: bool
    must: bool
    say: str          # what a person should do, when not ok


def checks(root: Path) -> List[Check]:
    out = []
    out.append(Check("virtualization", _under(root, "/dev/kvm").exists(), True,
                     "no /dev/kvm: turn on SVM Mode (AMD) or VT-x (Intel) in the BIOS"))
    groups = _under(root, "/sys/kernel/iommu_groups")
    has_groups = groups.is_dir() and any(groups.iterdir())
    out.append(Check("iommu", has_groups, True,
                     "no IOMMU groups: turn on IOMMU (AMD-Vi or VT-d) in the BIOS"))
    smt = _read(root, "/sys/devices/system/cpu/smt/active")
    out.append(Check("smt", smt == "1", True,
                     "hyper-threading off: turn SMT on in the BIOS (the CPU plan pins radiod to a thread pair)"))
    clock = _read(root, f"{CLOCKSOURCE}/current_clocksource")
    out.append(Check("clock", clock == "tsc", True,
                     f"the kernel keeps time on {clock or 'an unknown clock'}, not the TSC: "
                     f"see the Fallback section of docs/proxmox/wsprdaemon-proxmox-cpu-clock-tuning.md"))
    avail = (_read(root, f"{CLOCKSOURCE}/available_clocksource") or "").split()
    out.append(Check("hpet", "hpet" in avail, False,
                     "no HPET offered: turn it on in the BIOS if the menu has it (a fallback clock only)"))
    return out


def platform(root: Path) -> List[tuple]:
    """(label, value) pairs naming the platform; no serial numbers."""
    out = [(label, _read(root, f"{DMI}/{f}") or "?") for label, f in DMI_FIELDS]
    cpu = "?"
    try:
        for line in _under(root, "/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    out.append(("cpu", cpu))
    try:
        for line in _under(root, "/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                out.append(("memory seen", f"{int(line.split()[1]) // 1024} MB"))
                break
    except (OSError, ValueError, IndexError):
        pass
    return out


def brief(cs: List[Check]) -> str:
    todo = [c.name for c in cs if not c.ok and c.must]
    note = [c.name for c in cs if not c.ok and not c.must]
    line = "firmware: " + ("to do: " + ", ".join(todo) if todo else "ok")
    if note:
        line += " (advice: " + ", ".join(note) + ")"
    return line


def main(argv=None, *, root: Path = Path("/")) -> int:
    ap = argparse.ArgumentParser(prog="pm-firmware", description=__doc__.splitlines()[0])
    ap.add_argument("--brief", action="store_true", help="one line, for pm-align")
    ap.add_argument("--report", action="store_true",
                    help="the platform's facts and the checks, to paste into a report")
    args = ap.parse_args(argv)
    cs = checks(root)
    rc = 1 if any(not c.ok and c.must for c in cs) else 0
    if args.brief:
        print(brief(cs))
        return rc
    if args.report:
        print("pm-firmware report (paste it into your note; it holds no serial numbers)")
        for label, value in platform(root):
            print(f"  {label}: {value}")
    for c in cs:
        mark = "ok  " if c.ok else ("FIX " if c.must else "note")
        print(f"  {mark} {c.name}" + ("" if c.ok else f": {c.say}"))
    print("  ?    restore on AC power loss: Linux cannot read it; pull the power cord "
          "for ten seconds and the host must start by itself")
    print("  graphics memory: run pm-memory")
    print(f"  settings by platform: {DOC} in the sigmond repo")
    return rc


if __name__ == "__main__":
    sys.exit(main())

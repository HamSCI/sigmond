#!/usr/bin/env python3
"""pm-memory — the host's graphics carve-out and the decoder VM's memory.

RUNS ON THE PM (the Proxmox host itself).  A PM runs no sigmond, no venv and
no pip, so this file is STDLIB ONLY.  pm-align installs it as
/usr/local/sbin/pm-memory.

Why this exists
---------------
Every Beelink PM in the fleet shipped with 3-4 GB of its RAM reserved for the
integrated GPU (the BIOS "UMA frame buffer"): a 5825U host showed 11.6 of
16 GB, a 5560U or 5800H host 12.6.  A headless host with a text console needs
about 8 MB of it.  John Ackermann set N8UR-PM's to 64 MB on 2026-11-17 and the
host then saw 15.5 GB.  The decoder VM still held 9832 MB: firstboot sizes the
VM once, at install, as MemTotal - 2048 MB, and nothing grows it afterwards.

What it does
------------
Dry run (the default): reads the carve-out (the amdgpu driver's
mem_info_vram_total), the host's MemTotal and the VM's memory, and says what
a person should do.  Exit 0 nothing to do, 1 something to do.

``--apply`` (root): when the VM sits at least 512 MB below MemTotal - 2048,
``qm set <vmid> --memory <target>``.  Proxmox holds the change as pending
until the VM next starts.  pm-memory never restarts the VM (that restarts
radiod), never lowers its memory, and never touches the BIOS, which only a
person at the console can change.

The VM's USB controllers are passed through, so Proxmox pins all of the VM's
memory; QEMU takes about 0.4 GB more on top.  The 2048 MB reserve matches
firstboot's install rule, which the fleet has run on since v3.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

HOST_RESERVE_MB = 2048       # firstboot-v3.sh: VMMEM = MemTotal - 2048
CARVEOUT_WARN_MB = 512       # above this, the BIOS reservation is worth a visit
RESIZE_MIN_GAIN_MB = 512     # smaller gains are not worth a pending change
VM_FLOOR_MB = 4096           # firstboot's floor
RELAY_UNIT = "/etc/systemd/system/sigmond-vm-ssh-relay@.service"
DRM = "/sys/class/drm"


def _under(root: Path, path: str) -> Path:
    return root / path.lstrip("/")


def vram_mb(root: Path) -> Optional[int]:
    """The largest amdgpu VRAM carve-out on this host, in MB; None when no
    card reports one (an Intel host, or no amdgpu driver)."""
    sizes = []
    for f in _under(root, DRM).glob("card*/device/mem_info_vram_total"):
        try:
            sizes.append(int(f.read_text().strip()) // (1024 * 1024))
        except (OSError, ValueError):
            continue
    return max(sizes) if sizes else None


def memtotal_mb(root: Path) -> int:
    text = _under(root, "/proc/meminfo").read_text()
    m = re.search(r"^MemTotal:\s+(\d+)\s+kB", text, re.M)
    if not m:
        raise ValueError("no MemTotal in /proc/meminfo")
    return int(m.group(1)) // 1024


def host_vmid(root: Path) -> int:
    """The decoder VM's id, from the ssh relay unit; 100 when it cannot be
    read (pm-align reads it the same way)."""
    try:
        m = re.search(r"^Environment=SIGMOND_VMID=(\d+)\s*$",
                      _under(root, RELAY_UNIT).read_text(), re.M)
        if m:
            return int(m.group(1))
    except OSError:
        pass
    return 100


def vm_memory_mb(vmid: int, *, run: Callable) -> Optional[int]:
    """The VM's memory as `qm config` shows it, pending changes included, so
    a queued resize counts as done; None when the config names none."""
    r = run(["qm", "config", str(vmid)], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    m = re.search(r"^memory:\s*(\d+)\s*$", r.stdout or "", re.M)
    return int(m.group(1)) if m else None


@dataclass
class Assessment:
    carveout: bool
    target_mb: int
    gain_mb: Optional[int]
    resize: bool
    lines: list = field(default_factory=list)


def assess(*, memtotal: int, vram: Optional[int], vm_mem: Optional[int]) -> Assessment:
    target = max(memtotal - HOST_RESERVE_MB, VM_FLOOR_MB)
    gain = None if vm_mem is None else target - vm_mem
    carveout = vram is not None and vram > CARVEOUT_WARN_MB
    resize = gain is not None and gain >= RESIZE_MIN_GAIN_MB
    lines = []
    if carveout:
        lines.append(f"the BIOS reserves {vram} MB for the integrated GPU; a headless host "
                     f"needs 64 MB.  At the console: BIOS > Advanced > AMD CBS > NBIO > GFX "
                     f"Configuration > UMA Frame buffer Size = 64 MB (menu names vary).  "
                     f"Then run pm-memory --apply.")
    if vm_mem is None:
        lines.append("the VM's memory could not be read (qm config)")
    elif resize:
        lines.append(f"the VM holds {vm_mem} MB; this host can give it {target} MB "
                     f"(MemTotal {memtotal} - {HOST_RESERVE_MB} reserve), {gain} MB more.  "
                     f"pm-memory --apply queues it for the VM's next restart.")
    return Assessment(carveout, target, gain, resize, lines)


def _brief(a: Assessment, vram: Optional[int], memtotal: int, vm_mem: Optional[int]) -> str:
    parts = [f"host {memtotal} MB", f"VM {vm_mem if vm_mem is not None else '?'} MB"]
    if vram is not None:
        parts.append(f"iGPU {vram} MB")
    todo = []
    if a.carveout:
        todo.append("BIOS UMA frame buffer to 64 MB")
    if a.resize:
        todo.append(f"VM to {a.target_mb} MB (pm-memory --apply)")
    return "memory: " + ", ".join(parts) + ("; to do: " + "; ".join(todo) if todo else "; ok")


def main(argv=None, *, root: Path = Path("/"), run: Callable = subprocess.run,
         euid: Optional[int] = None) -> int:
    ap = argparse.ArgumentParser(prog="pm-memory", description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="queue the VM's resize for its next restart (root; never restarts it)")
    ap.add_argument("--brief", action="store_true", help="one line, for pm-align")
    ap.add_argument("--vmid", type=int, default=None, help="the decoder VM (default: this host's)")
    args = ap.parse_args(argv)
    euid = os.geteuid() if euid is None else euid

    vmid = args.vmid if args.vmid is not None else host_vmid(root)
    try:
        memtotal = memtotal_mb(root)
    except (OSError, ValueError) as exc:
        print(f"pm-memory: cannot read this host's memory: {exc}")
        return 2
    vram = vram_mb(root)
    vm_mem = vm_memory_mb(vmid, run=run)
    a = assess(memtotal=memtotal, vram=vram, vm_mem=vm_mem)

    if args.brief:
        print(_brief(a, vram, memtotal, vm_mem))
        return 1 if (a.carveout or a.resize) else 0

    print(f"pm-memory: host {memtotal} MB, VM {vmid} {vm_mem if vm_mem is not None else '?'} MB, "
          f"iGPU {vram if vram is not None else 'none'} MB")
    for line in a.lines:
        print(f"  {line}")
    if not args.apply:
        if not a.lines:
            print("  nothing to do")
        return 1 if (a.carveout or a.resize) else 0

    if euid != 0:
        print("pm-memory --apply: run as root")
        return 2
    if a.resize:
        r = run(["qm", "set", str(vmid), "--memory", str(a.target_mb)],
                capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  qm set failed: {(r.stderr or r.stdout or '').strip()}")
            return 2
        print(f"  queued: VM {vmid} memory {vm_mem} -> {a.target_mb} MB.  It takes effect at the "
              f"VM's next restart; `qm reboot {vmid}` does it now and restarts radiod.")
    return 1 if a.carveout else 0


if __name__ == "__main__":
    sys.exit(main())

"""The archive drive: find it, judge where it sits, and record it.

tasks/plan-external-storage.md, phase 1.  sigmond owns the drive (finding
it, checking its placement, formatting, mounting, archive.toml); hf-timestd
will own what goes onto it (the archiver, phase 2).  Everything here reads
sysfs, /proc/mounts and /etc/fstab through paths a test can replace, and
runs nothing; `sigmond.commands.archive` does the formatting and mounting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

LABEL = "SIGMOND-ARCHIVE"
MOUNT = Path("/var/lib/sigmond/archive")
CONFIG = Path("/etc/sigmond/archive.toml")
FSTAB = Path("/etc/fstab")
# A missing drive must never hold up a boot (plan, step 6).
MOUNT_OPTS = "nofail,noatime,x-systemd.device-timeout=10s"
SYS_BLOCK = Path("/sys/block")
SYS_USB = Path("/sys/bus/usb/devices")
PROC_MOUNTS = Path("/proc/mounts")
RX888_IDS = ("04b4:00f1", "04b4:00f3")    # running, and the FX3 bootloader
# Raw 24 kHz IQ, measured on B4 and ND (docs/operator/day-2.md §3).
BYTES_PER_CHANNEL_DAY = 15 * 10**9
DEFAULT_CHANNELS = 6

_USB_DEVPATH = re.compile(r"^\d+-\d+(\.\d+)*$")


@dataclass(frozen=True)
class Disk:
    name: str                  # "sdb"
    size_bytes: int
    model: str
    serial: str
    usb_devpath: Optional[str]  # "2-1.3", or None when not on USB
    controller: Optional[str]   # the PCI device above the USB bus


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def _usb_parts(resolved: Path):
    """The USB device path and the controller above it, from a resolved
    sysfs device path such as
    /sys/devices/pci0000:00/0000:00:14.0/usb2/2-1/2-1.3/2-1.3:1.0/host2/..."""
    parts = resolved.parts
    devpath = controller = None
    for i, part in enumerate(parts):
        if re.fullmatch(r"usb\d+", part) and i > 0:
            controller = parts[i - 1]
        if _USB_DEVPATH.match(part):
            devpath = part       # the deepest one is the drive itself
    return devpath, controller


def list_disks(sys_block: Path = SYS_BLOCK) -> Dict[str, Disk]:
    """Whole disks the kernel lists, by name.  Loop, ram and device-mapper
    nodes are left out."""
    disks: Dict[str, Disk] = {}
    try:
        entries = sorted(sys_block.iterdir())
    except OSError:
        return disks
    for dev in entries:
        if dev.name.startswith(("loop", "ram", "dm-", "zram", "sr")):
            continue
        sectors = _read(dev / "size")
        size = int(sectors) * 512 if sectors.isdigit() else 0
        try:
            resolved = (dev / "device").resolve(strict=True)
        except OSError:
            resolved = None
        devpath, controller = _usb_parts(resolved) if resolved else (None, None)
        model = " ".join(x for x in (_read(dev / "device" / "vendor"),
                                     _read(dev / "device" / "model")) if x)
        serial = _read(dev / "device" / "serial")
        if not serial and devpath and resolved:
            # USB mass storage keeps the serial on the USB device, not the SCSI one.
            parts = resolved.parts
            serial = _read(Path(*parts[:len(parts) - parts[::-1].index(devpath)]) / "serial")
        disks[dev.name] = Disk(dev.name, size, model, serial, devpath, controller)
    return disks


def rx888_devpaths(sys_usb: Path = SYS_USB) -> List[str]:
    """Every RX-888 on the bus, as sysfs USB device paths ("4-1.4")."""
    found = []
    try:
        entries = sorted(sys_usb.iterdir())
    except OSError:
        return found
    for dev in entries:
        vid_pid = f"{_read(dev / 'idVendor')}:{_read(dev / 'idProduct')}"
        if vid_pid in RX888_IDS and _USB_DEVPATH.match(dev.name):
            found.append(dev.name)
    return found


def _hub_of(devpath: str) -> Optional[str]:
    """The hub a device hangs off: "4-1.4" -> "4-1"; "6-2" (a root port) -> None."""
    return devpath.rsplit(".", 1)[0] if "." in devpath else None


def _controller_of_usb(devpath: str, sys_usb: Path) -> Optional[str]:
    try:
        resolved = (sys_usb / devpath).resolve(strict=True)
    except OSError:
        return None
    return _usb_parts(resolved)[1]


@dataclass(frozen=True)
class Placement:
    verdict: str        # "ok", "warn" or "refuse"
    reason: str


def judge_placement(disk: Disk, rx888: List[str],
                    sys_usb: Path = SYS_USB) -> Placement:
    """Plan, step 3.  Refuse a drive anywhere below an RX-888's hub: our
    recovery for a stalled RX-888 resets that hub's port, and a cut can
    reach the hub's other ports, nested hubs inside it included.  Warn,
    without refusing, when the drive shares an RX-888's controller."""
    if not disk.usb_devpath:
        return Placement("refuse", f"/dev/{disk.name} is not on USB; the archive "
                                   "takes a USB drive")
    shared_controller = None
    for rx in rx888:
        hub = _hub_of(rx)
        if hub and (disk.usb_devpath == hub
                    or disk.usb_devpath.startswith(hub + ".")):
            return Placement("refuse", f"/dev/{disk.name} ({disk.usb_devpath}) sits on the "
                             f"RX-888's hub ({hub}); sigmond-sdr-recover resets that hub's "
                             "ports.  Move the drive to a port on another hub")
        if disk.controller and disk.controller == _controller_of_usb(rx, sys_usb):
            shared_controller = rx
    if shared_controller:
        return Placement("warn", f"/dev/{disk.name} shares the RX-888's USB controller "
                         f"({disk.controller}).  The archive writes about 1 MB/s, so "
                         "bandwidth costs little, but a reset or a power sag on that "
                         "controller would reach the radio")
    return Placement("ok", f"/dev/{disk.name} is on {disk.usb_devpath}, away from the RX-888")


def mounted_sources(proc_mounts: Path = PROC_MOUNTS) -> Dict[str, str]:
    """Mounted block device -> its mount point."""
    out: Dict[str, str] = {}
    for line in _read(proc_mounts).splitlines():
        f = line.split()
        if len(f) >= 2 and f[0].startswith("/dev/"):
            out[f[0]] = f[1].replace("\\040", " ")
    return out


def mounts_on(disk: str, proc_mounts: Path = PROC_MOUNTS) -> Dict[str, str]:
    """What of this disk (it or any partition) is mounted now."""
    pat = re.compile(rf"^/dev/{re.escape(disk)}(p?\d+)?$")
    return {s: m for s, m in mounted_sources(proc_mounts).items() if pat.match(s)}


def partition_name(disk: str) -> str:
    """The first partition's node: sdb -> sdb1, nvme0n1 -> nvme0n1p1."""
    return f"{disk}p1" if disk[-1].isdigit() else f"{disk}1"


def fstab_line(uuid: str, mount: Optional[Path] = None) -> str:
    mount = MOUNT if mount is None else mount
    return f"UUID={uuid} {mount} ext4 {MOUNT_OPTS} 0 2"


def fstab_with_archive(text: str, uuid: str, mount: Optional[Path] = None) -> str:
    """The fstab with exactly one line for the archive mount point: any
    earlier line for that mount point gives way to the new one."""
    mount = MOUNT if mount is None else mount
    kept = [ln for ln in text.splitlines()
            if not (ln.split() and not ln.lstrip().startswith("#")
                    and len(ln.split()) >= 2 and ln.split()[1] == str(mount))]
    kept.append(fstab_line(uuid, mount))
    return "\n".join(kept) + "\n"


def fstab_without_archive(text: str, mount: Optional[Path] = None) -> str:
    mount = MOUNT if mount is None else mount
    kept = [ln for ln in text.splitlines()
            if not (ln.split() and not ln.lstrip().startswith("#")
                    and len(ln.split()) >= 2 and ln.split()[1] == str(mount))]
    return "\n".join(kept) + ("\n" if kept else "")


def config_text(uuid: str, disk: Disk, when_utc: str) -> str:
    """/etc/sigmond/archive.toml.  `mode` and the retention keys are read by
    the archiver (hf-timestd, phase 2); their values here are the plan's
    defaults (decisions 2 and 4)."""
    return (
        "# The archive drive (smd archive add).  tasks/plan-external-storage.md\n"
        f'uuid = "{uuid}"\n'
        f'label = "{LABEL}"\n'
        f'mount = "{MOUNT}"\n'
        f'added_utc = "{when_utc}"\n'
        f'model = "{disk.model}"\n'
        f'serial = "{disk.serial}"\n'
        f'size_bytes = {disk.size_bytes}\n'
        "\n"
        "# Read by the archiver (hf-timestd, phase 2): every channel, rolling, with a\n"
        "# kept window per flare that reached the station.\n"
        'mode = "all"\n'
        "high_water_pct = 90\n"
        "kept_ceiling_pct = 25\n"
    )


def days_fit(free_bytes: int, channels: int = DEFAULT_CHANNELS) -> float:
    if channels <= 0:
        return 0.0
    return free_bytes / (channels * BYTES_PER_CHANNEL_DAY)


def human_bytes(n: int) -> str:
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"

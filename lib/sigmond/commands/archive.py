"""`smd archive add|status|remove` -- the archive drive.

tasks/plan-external-storage.md, phase 1.  `add` finds a USB drive the
operator plugs in, refuses one on the RX-888's hub, formats it ext4 with the
label SIGMOND-ARCHIVE (decision 3: always), mounts it by UUID at
/var/lib/sigmond/archive through an fstab line that never holds up a boot,
and writes /etc/sigmond/archive.toml.  `remove` unmounts it so it can be
unplugged.  The archiver that fills the drive is hf-timestd's (phase 2);
until it ships, the drive stands ready and empty.  `keep` waits for it too.
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess
import time
from typing import Dict, List, Optional

from .. import archive as ar
from ..ui import err, info, ok, warn

# Who must own the mount point.  Root, always; the tests point it at themselves.
_MOUNT_OWNER_UID = 0


def cmd_archive(args) -> int:
    verb = getattr(args, 'archive_command', None) or 'status'
    if verb == 'add':
        return _add(args)
    if verb == 'remove':
        return _remove(args)
    return _status()


# ---------------------------------------------------------------- helpers

def _run(cmd: List[str], dry: bool, stdin: Optional[str] = None) -> int:
    print(f"    $ {' '.join(cmd)}" + (f"   <<< {stdin.strip()!r}" if stdin else ""))
    if dry:
        return 0
    r = subprocess.run(cmd, input=stdin, text=True, capture_output=True)
    if r.returncode != 0:
        err(f"{cmd[0]} exited {r.returncode}: {(r.stderr or r.stdout).strip()[:400]}")
    return r.returncode


def _describe(d: ar.Disk) -> str:
    return (f"/dev/{d.name}  {ar.human_bytes(d.size_bytes)}  {d.model or '(no model)'}"
            f"  serial {d.serial or '?'}  USB {d.usb_devpath or '-'}"
            f"  controller {d.controller or '-'}")


def _wait_for_new_disk(before: Dict[str, ar.Disk], timeout_s: float = 30.0) -> List[ar.Disk]:
    end = time.monotonic() + timeout_s
    while True:
        now = ar.list_disks()
        new = [now[n] for n in sorted(now) if n not in before]
        if new or time.monotonic() >= end:
            return new
        time.sleep(1.0)


def _mount_verified(part: str) -> int:
    """Mount ``part`` on the mount point through a handle checked first.

    /var/lib/sigmond is writable by the sigmond group, so the path could be
    swapped for a symlink between a check and a mount by name.  Open it
    without following a symlink, check what the handle holds, and call
    mount(2) on /proc/self/fd/N: the kernel resolves that to the opened
    directory itself.  util-linux's mount cannot take that target (its
    move_mount() path refuses it, seen on the build rig 2026-11-02), so the
    call goes through libc."""
    try:
        fd = os.open(ar.MOUNT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as e:
        err(f"refusing to mount: cannot open {ar.MOUNT} without following a link ({e})")
        return 1
    try:
        st = os.fstat(fd)
        if st.st_uid != _MOUNT_OWNER_UID:
            err(f"refusing to mount: {ar.MOUNT} is not owned by root")
            return 1
        target = f"/proc/self/fd/{fd}"
        print(f"    $ mount(2) {part} on {ar.MOUNT} (via {target}), ext4, noatime")
        rc = _libc_mount(part, target)
        if rc != 0:
            err(f"mount {part} on {ar.MOUNT}: {os.strerror(rc)}")
        return rc
    finally:
        os.close(fd)


MS_NOATIME = 1024


def _libc_mount(source: str, target: str) -> int:
    """mount(2), ext4, noatime.  Returns 0 or the errno."""
    import ctypes
    import ctypes.util
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    if libc.mount(source.encode(), target.encode(), b"ext4",
                  ctypes.c_ulong(MS_NOATIME), None) != 0:
        return ctypes.get_errno() or 1
    return 0


def _blkid_uuid(node: str) -> str:
    r = subprocess.run(["blkid", "-s", "UUID", "-o", "value", node],
                       text=True, capture_output=True)
    return r.stdout.strip()


def _channels_in_buffer() -> int:
    """The raw buffer's channel count, for the days-fit estimate."""
    root = "/var/lib/timestd/raw_buffer"
    try:
        n = sum(1 for e in os.scandir(root) if e.is_dir())
    except OSError:
        n = 0
    return n or ar.DEFAULT_CHANNELS


# ---------------------------------------------------------------- add

def _add(args) -> int:
    dry = bool(getattr(args, 'dry_run', False))
    name = getattr(args, 'device', None)
    if name:
        name = name.removeprefix('/dev/')
        disks = ar.list_disks()
        if name not in disks:
            err(f"no disk /dev/{name}; `lsblk -d` lists what the kernel sees")
            return 1
        disk = disks[name]
    else:
        before = ar.list_disks()
        info("Disks present now:")
        for d in before.values():
            print(f"    {_describe(d)}")
        try:
            input("\nPlug in the archive drive now, then press Enter. ")
        except EOFError:
            err("no terminal to answer on; name the drive with --device sdX")
            return 1
        new = _wait_for_new_disk(before)
        if not new:
            err("no new disk appeared within 30 s.  Check the cable and the port, "
                "or name the drive with --device sdX")
            return 1
        if len(new) > 1:
            err("more than one new disk appeared: "
                + ", ".join(f"/dev/{d.name}" for d in new)
                + ".  Plug in one drive, or name it with --device")
            return 1
        disk = new[0]

    print(f"\n  found  {_describe(disk)}")
    placement = ar.judge_placement(disk, ar.rx888_devpaths())
    if placement.verdict == 'refuse':
        err(placement.reason)
        return 1
    if placement.verdict == 'warn':
        warn(placement.reason)
    else:
        ok(placement.reason)

    mounted = ar.mounts_on(disk.name)
    if mounted:
        err("part of the drive is mounted now ("
            + ", ".join(f"{s} on {m}" for s, m in mounted.items())
            + ").  Unmount it first; `add` formats the whole drive")
        return 1

    channels = _channels_in_buffer()
    print(f"\n  At about 15 GB per channel per day, {channels} channels fill this drive "
          f"in about {ar.days_fit(disk.size_bytes, channels):.0f} days.")
    warn(f"FORMATTING ERASES EVERYTHING ON /dev/{disk.name}.")
    confirm = getattr(args, 'confirm', None)
    if confirm is None and not dry:
        try:
            confirm = input(f"  Type {disk.name} to format it, anything else to stop: ").strip()
        except EOFError:
            err("no terminal to answer on; pass --confirm "
                f"{disk.name} to format it from a script")
            return 1
    if not dry and (confirm or '').removeprefix('/dev/') != disk.name:
        info(f"stopped; /dev/{disk.name} untouched")
        return 1

    for tool in ("wipefs", "sfdisk", "mkfs.ext4", "blkid", "udevadm", "mount"):
        if not shutil.which(tool):
            err(f"{tool} not found; it comes with util-linux / e2fsprogs")
            return 1

    dev = f"/dev/{disk.name}"
    part = f"/dev/{ar.partition_name(disk.name)}"
    info(("Would run" if dry else "Formatting") + f" {dev}:")
    steps = [
        (["wipefs", "-a", dev], None),
        (["sfdisk", "--quiet", dev], "label: gpt\n,,L\n"),
        (["udevadm", "settle"], None),
        (["mkfs.ext4", "-F", "-q", "-L", ar.LABEL, "-m", "0", "-T", "largefile", part], None),
    ]
    for cmd, stdin in steps:
        if _run(cmd, dry, stdin) != 0:
            err(f"{dev} is partly formatted; run `smd archive add --device {disk.name}` again")
            return 1

    uuid = "<uuid>" if dry else _blkid_uuid(part)
    if not uuid:
        err(f"blkid found no UUID on {part}")
        return 1
    fstab_old = ar.FSTAB.read_text() if ar.FSTAB.exists() else ""
    fstab_new = ar.fstab_with_archive(fstab_old, uuid)
    now = _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if dry:
        print(f"    would mkdir {ar.MOUNT}")
        print(f"    would set /etc/fstab's archive line to: {ar.fstab_line(uuid)}")
        print(f"    would write {ar.CONFIG}")
        print(f"    would run: systemctl daemon-reload; mount(2) {part} on {ar.MOUNT}, "
              "ext4, noatime, through a handle opened without following links")
        info("dry run: nothing changed")
        return 0

    if not os.path.lexists(ar.MOUNT):
        ar.MOUNT.parent.mkdir(parents=True, exist_ok=True)
        os.mkdir(ar.MOUNT, 0o755)
    problem = ar.mount_point_problem()
    if problem:
        err(f"refusing to mount: {problem}.  The drive is formatted; run add again "
            "once the mount point is a plain root-owned directory")
        return 1
    backup = ar.FSTAB.with_name(f"fstab.pre-archive-{now.replace(':', '')}")
    if fstab_old:
        backup.write_text(fstab_old)
    tmp = ar.FSTAB.with_name("fstab.archive-new")
    tmp.write_text(fstab_new)
    os.replace(tmp, ar.FSTAB)
    ar.CONFIG.parent.mkdir(parents=True, exist_ok=True)
    ar.CONFIG.write_text(ar.config_text(uuid, disk, now))
    if _run(["systemctl", "daemon-reload"], False) != 0:
        return 1
    if _mount_verified(part) != 0:
        err(f"the fstab line stands (backup {backup}); mount {ar.MOUNT} by hand to see why")
        return 1
    st = shutil.disk_usage(ar.MOUNT)
    ok(f"archive drive mounted at {ar.MOUNT}: {ar.human_bytes(st.free)} free, "
       f"about {ar.days_fit(st.free, channels):.0f} days at {channels} channels")
    info(f"UUID {uuid}; /etc/fstab (backup {backup.name}) and {ar.CONFIG} written.  "
         "A missing drive never holds up a boot.")
    info("The archiver that fills the drive comes with hf-timestd (phase 2); until then "
         "the drive stands ready and empty.  Unplug safely with `smd archive remove`.")
    return 0


# ---------------------------------------------------------------- status / remove

def _config() -> Dict[str, object]:
    try:
        import tomllib
        return tomllib.loads(ar.CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def _status() -> int:
    cfg = _config()
    if not cfg:
        info("no archive drive (`smd archive add` attaches one)")
        return 0
    print(f"  drive    {cfg.get('model') or '?'}  serial {cfg.get('serial') or '?'}  "
          f"UUID {cfg.get('uuid')}  added {cfg.get('added_utc')}")
    mounted = {m: s for s, m in ar.mounted_sources().items()}
    if str(ar.MOUNT) not in mounted:
        warn(f"not mounted at {ar.MOUNT}: the drive is unplugged, or was removed "
             "(`mount " + str(ar.MOUNT) + "` brings it back)")
        return 0
    st = shutil.disk_usage(ar.MOUNT)
    channels = _channels_in_buffer()
    ok(f"mounted at {ar.MOUNT} from {mounted[str(ar.MOUNT)]}: "
       f"{ar.human_bytes(st.used)} used of {ar.human_bytes(st.total)}, "
       f"{ar.human_bytes(st.free)} free (about {ar.days_fit(st.free, channels):.0f} days "
       f"at {channels} channels)")
    days = sorted({p.name for p in ar.MOUNT.glob('*/raw_buffer/*/[0-9]*')
                   if p.is_dir() and p.name.isdigit()})
    if days:
        print(f"  holds    {len(days)} day(s), {days[0]} to {days[-1]}")
    else:
        print("  holds    nothing yet (the archiver comes with hf-timestd, phase 2)")
    return 0


def _remove(args) -> int:
    forget = bool(getattr(args, 'forget', False))
    mounted = {m for m in ar.mounted_sources().values()}
    # Unmount by name only what /proc/mounts lists as mounted there: a mount
    # point in use cannot be renamed or replaced (EBUSY), so the name cannot
    # be swapped under us between this check and the umount.
    if str(ar.MOUNT) in mounted:
        problem = ar.mount_point_problem()
        if problem:
            err(f"refusing to unmount: {problem}")
            return 1
        if _run(["umount", str(ar.MOUNT)], False) != 0:
            err("the drive is busy; `fuser -vm " + str(ar.MOUNT) + "` names who holds it")
            return 1
        ok(f"unmounted {ar.MOUNT}: safe to unplug the drive")
    else:
        info(f"{ar.MOUNT} was not mounted")
    if forget:
        if ar.FSTAB.exists():
            ar.FSTAB.write_text(ar.fstab_without_archive(ar.FSTAB.read_text()))
        try:
            ar.CONFIG.unlink()
        except FileNotFoundError:
            pass
        _run(["systemctl", "daemon-reload"], False)
        ok("forgot the drive: its fstab line and archive.toml are gone")
    else:
        info("the fstab line stays (nofail), so the drive mounts again at the next boot "
             "when it is plugged in; `smd archive remove --forget` drops it")
    return 0

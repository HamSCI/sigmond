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
import pwd
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import json

from .. import archive as ar
from .. import archive_health as H
from ..ui import err, info, ok, warn

# Who must own the mount point.  Root, always; the tests point it at themselves.
_MOUNT_OWNER_UID = 0

# The drive's top level stays root's; each client that writes to the archive
# gets its own folder, owned by its service user (design 2026-11-12, part 2).
CLIENT_DIRS = {"timestd": "timestd"}

# wipefs, sfdisk and mkfs.ext4 live in /usr/sbin, which an operator's PATH
# often lacks (AI6VN-1, 2026-11-12: `--dry-run` as hamsci stopped on
# "wipefs not found").
_SBIN_DIRS = ("/usr/sbin", "/sbin")

HEALTH = ar.MOUNT.parent / "archive-health.json"            # /var/lib/sigmond/archive-health.json
MODPROBE_CONF = Path("/etc/modprobe.d/sigmond-archive-usb.conf")
HEALTH_TIMER = "sigmond-archive-health.timer"


def _say(msg: str = "") -> None:
    """One stream for everything `smd archive` tells the operator: the ui
    helpers write to stderr, so plain lines go there too and keep their order
    when stdout is a pipe."""
    print(msg, file=sys.stderr, flush=True)


def _with_sbin_on_path() -> None:
    parts = os.environ.get("PATH", "").split(os.pathsep)
    missing = [d for d in _SBIN_DIRS if d not in parts]
    if missing:
        os.environ["PATH"] = os.pathsep.join([p for p in parts if p] + missing)


def _make_client_dirs(root) -> None:
    """Create each client's folder on the mounted drive, owned by its user.
    A client whose user does not exist on this station gets no folder."""
    for name, user in CLIENT_DIRS.items():
        try:
            pw = pwd.getpwnam(user)
        except KeyError:
            continue
        d = root / name
        if not d.exists():
            os.mkdir(d, 0o755)
        os.chown(d, pw.pw_uid, pw.pw_gid)
        ok(f"{d} owned by {user}")


def cmd_archive(args) -> int:
    verb = getattr(args, 'archive_command', None) or 'status'
    if verb == 'add':
        return _add(args)
    if verb == 'remove':
        return _remove(args)
    return _status()


# ---------------------------------------------------------------- helpers

def _run(cmd: List[str], dry: bool, stdin: Optional[str] = None) -> int:
    _say(f"    $ {' '.join(cmd)}" + (f"   <<< {stdin.strip()!r}" if stdin else ""))
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
        _say(f"    $ mount(2) {part} on {ar.MOUNT} (via {target}), ext4, noatime")
        rc = _libc_mount(part, target)
        if rc != 0:
            err(f"mount {part} on {ar.MOUNT}: {os.strerror(rc)}")
            return rc
        # The directory could still have been renamed between the check and
        # the mount.  Confirm the drive landed at exactly the mount point;
        # if not, detach it wherever it went and fail.
        landed = _mount_points_of(part)
        if str(ar.MOUNT) not in landed:
            for where in landed:
                _libc_umount_detach(where)
            err(f"refusing: {part} mounted at {', '.join(landed) or 'nowhere'}, not "
                f"{ar.MOUNT} (the directory moved during the mount); detached it")
            return 1
        return 0
    finally:
        os.close(fd)


def _mount_points_of(part: str) -> List[str]:
    """Where /proc/self/mountinfo shows ``part`` mounted.  Field 5 is the
    mount point; the source follows the " - " separator."""
    out = []
    try:
        lines = open("/proc/self/mountinfo").read().splitlines()
    except OSError:
        return out
    for line in lines:
        head, _, tail = line.partition(" - ")
        f, t = head.split(), tail.split()
        if len(f) >= 5 and len(t) >= 2 and t[1] == part:
            out.append(f[4].replace("\\040", " "))
    return out


def _libc_umount_detach(where: str) -> None:
    import ctypes
    import ctypes.util
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    MNT_DETACH, UMOUNT_NOFOLLOW = 2, 8
    libc.umount2(where.encode(), MNT_DETACH | UMOUNT_NOFOLLOW)


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

# ---------------------------------------------------------------- health (part 1)

def _smart_run(argv, **kw):
    kw.pop("timeout", None)
    return subprocess.run(argv, capture_output=True, text=True)


def _probe(name: str):
    return H.probe(name, run=_smart_run)


def _short_self_test(name: str, dev_type) -> Optional[str]:
    if not H.start_short_test(name, dev_type, run=_smart_run):
        _say("    the drive's bridge cannot start a self-test; going on without one")
        return None
    _say("    short self-test started (about 2 minutes)")
    return H.wait_self_test(name, dev_type, run=_smart_run, say=_say)


def _usb_ids(devpath: str) -> Tuple[str, str]:
    d = ar.SYS_USB / devpath
    return ((d / "idVendor").read_text().strip(), (d / "idProduct").read_text().strip())


def _set_runtime_quirk(line: str) -> None:
    quirk = line.split("quirks=", 1)[1]
    p = Path("/sys/module/usb_storage/parameters/quirks")
    cur = p.read_text().strip() if p.exists() else ""
    if quirk not in cur.split(","):
        p.write_text(",".join(x for x in (cur, quirk) if x))


def _rebind_usb(devpath: str) -> bool:
    drv = Path("/sys/bus/usb/drivers/usb")
    try:
        (drv / "unbind").write_text(devpath)
        time.sleep(2)
        (drv / "bind").write_text(devpath)
        return True
    except OSError as exc:
        err(f"could not rebind USB {devpath}: {exc}")
        return False


def _switch_uas(disk: "ar.Disk") -> Optional["ar.Disk"]:
    """Put the drive's bridge on usb-storage (BOT) so SMART passes, here and
    at every boot; the disk that comes back, or None."""
    if not disk.usb_devpath:
        return None
    vid, pid = _usb_ids(disk.usb_devpath)
    line = H.uas_quirk_line(vid, pid)
    MODPROBE_CONF.parent.mkdir(parents=True, exist_ok=True)
    MODPROBE_CONF.write_text(line + "\n")
    ok(f"wrote {MODPROBE_CONF}: {line}")
    _set_runtime_quirk(line)
    if not _rebind_usb(disk.usb_devpath):
        return None
    end = time.monotonic() + 30
    while True:
        for d in ar.list_disks().values():
            if d.usb_devpath == disk.usb_devpath:
                return d
        if time.monotonic() >= end:
            err("the drive did not come back after the switch; unplug it and plug it in again")
            return None
        time.sleep(1)


def _ask(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _health_step(disk: "ar.Disk", args, dry: bool):
    """Read SMART, run the short self-test, judge.  Returns (rc, disk,
    reading); rc 1 stops add before the erase."""
    if not shutil.which("smartctl"):
        if dry:
            _say("    would install smartmontools (smartctl) to read the drive's health")
            return 0, disk, None
        if _run(["apt-get", "install", "-y", "-q", "smartmontools"], False) != 0:
            warn("smartctl could not be installed; the drive's health is unknown")
            return 0, disk, None
    reading, why = _probe(disk.name)
    if reading is None and "under UAS" in why and not dry:
        warn(why)
        if getattr(args, "switch_uas", False) or _ask(
                "  Switch the bridge to usb-storage so its health can be read? [y/N] "):
            back = _switch_uas(disk)
            if back is not None:
                disk = back
                reading, why = _probe(disk.name)
    if reading is not None:
        _say(f"\n  health  {reading.model}  disk serial {reading.disk_serial or '?'}  "
             f"(smartctl -d {reading.dev_type or 'auto'})")
        for label, value in H.table(reading):
            _say(f"    {label:<24}{value}")
        if H.is_smr(reading.model):
            info("this model is shingled (SMR): fine for the archive's whole-file writes")
        if not dry and not getattr(args, "no_self_test", False):
            result = _short_self_test(disk.name, reading.dev_type)
            if result:
                reading.selftest_status = result
                _say(f"    self-test: {result}")
    v, reasons = H.verdict(reading, why)
    if v == "fail":
        for r in reasons:
            err(r)
        if (getattr(args, "accept_unhealthy", None) or "").removeprefix("/dev/") != disk.name:
            err(f"refusing a failing drive; unplug it.  To use it anyway: "
                f"--accept-unhealthy {disk.name}")
            return 1, disk, reading
        warn(f"--accept-unhealthy {disk.name}: going on with a failing drive")
    elif v == "warn":
        for r in reasons:
            warn(r)
        if not dry and getattr(args, "confirm", None) is None and not _ask(
                "  Use this drive anyway? [y/N] "):
            info(f"stopped; /dev/{disk.name} untouched")
            return 1, disk, reading
    elif v == "unknown":
        info(f"health unknown: {reasons[0]}")
    else:
        ok("drive health: no warnings")
    return 0, disk, reading


def _write_health(reading) -> None:
    now = _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    HEALTH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEALTH.with_name(HEALTH.name + ".new")
    tmp.write_text(json.dumps(H.health_doc(reading, reading, now), indent=2) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, HEALTH)


def _after_mount(reading, dry: bool) -> None:
    if reading is not None:
        _write_health(reading)
    _run(["systemctl", "mask", "--now", "smartmontools.service"], dry)
    _run(["systemctl", "enable", "--now", HEALTH_TIMER], dry)


def _upgrade(args) -> int:
    """Bring a drive added before 2026-11-13 up to date without formatting:
    the automount fstab line, the new archive.toml keys, a health baseline."""
    cfg = _config()
    if not cfg.get("uuid"):
        err("no archive drive to upgrade (`smd archive add` attaches one)")
        return 1
    uuid = str(cfg["uuid"])
    fstab = ar.FSTAB.read_text() if ar.FSTAB.exists() else ""
    if ar.fstab_needs_upgrade(fstab):
        ar.FSTAB.write_text(ar.fstab_with_archive(fstab, uuid))
        ok("fstab: the archive line now automounts after a replug")
    disk = next((d for d in ar.list_disks().values() if d.serial and d.serial == cfg.get("serial")),
                None)
    reading = None
    if disk is not None:
        rc, disk, reading = _health_step(disk, args, False)
    disk_serial = reading.disk_serial if reading else str(cfg.get("disk_serial", ""))
    dev_type = reading.dev_type if reading else cfg.get("smart_dev_type")
    d = disk or ar.Disk("", int(cfg.get("size_bytes", 0)), str(cfg.get("model", "")),
                        str(cfg.get("serial", "")), None, None)
    ar.CONFIG.write_text(ar.config_text(uuid, d, str(cfg.get("added_utc", "")),
                                        disk_serial=disk_serial, smart_dev_type=dev_type))
    ok(f"{ar.CONFIG} has the archiver's keys (archive_dtype f16, evict_first)")
    _run(["systemctl", "daemon-reload"], False)
    _after_mount(reading, False)
    return 0


def _add(args) -> int:
    _with_sbin_on_path()
    if getattr(args, 'upgrade', False):
        return _upgrade(args)
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
            _say(f"    {_describe(d)}")
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

    _say(f"\n  found  {_describe(disk)}")
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

    rc, disk, reading = _health_step(disk, args, dry)
    if rc:
        return rc

    channels = _channels_in_buffer()
    _say(f"\n  At about 15 GB per channel per day, {channels} channels fill this drive "
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
    if not dry:
        info("mkfs writes the ext4 journal (about 1 GB): seconds on a hard drive, "
             "minutes on a slow USB stick.  Let it finish.")
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
        _say(f"    would mkdir {ar.MOUNT}")
        _say(f"    would set /etc/fstab's archive line to: {ar.fstab_line(uuid)}")
        _say(f"    would write {ar.CONFIG}")
        _say(f"    would run: systemctl daemon-reload; mount(2) {part} on {ar.MOUNT}, "
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
    ar.CONFIG.write_text(ar.config_text(
        uuid, disk, now, disk_serial=reading.disk_serial if reading else "",
        smart_dev_type=reading.dev_type if reading else None))
    if _run(["systemctl", "daemon-reload"], False) != 0:
        return 1
    # Nothing, root included, may write into the bare mount point while the
    # drive is away (design part 2: never archive onto the SSD).
    _run(["chattr", "+i", str(ar.MOUNT)], False)
    if _mount_verified(part) != 0:
        err(f"the fstab line stands (backup {backup}); mount {ar.MOUNT} by hand to see why")
        return 1
    _make_client_dirs(ar.MOUNT)
    _after_mount(reading, False)
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
    _say(f"  drive    {cfg.get('model') or '?'}  serial {cfg.get('serial') or '?'}  "
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
        _say(f"  holds    {len(days)} day(s), {days[0]} to {days[-1]}")
    else:
        _say("  holds    nothing yet (the archiver comes with hf-timestd, phase 2)")
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

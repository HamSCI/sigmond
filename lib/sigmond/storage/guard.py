"""smd storage guard: protect a station's data mounts.

Data-volume design 2026-11-08 ("A hand-picked unit list decides who waits for
the disk"; "What it means for the stations Rob already moved").  For every
mount (other than / and the archive drive) that holds a client's data path:

1. each of that client's services gets a drop-in
   ``RequiresMountsFor=<mount>``, so it waits for the disk and does not start
   without it;
2. the bare directory beneath the mount is made root-owned, mode 0000 and
   immutable, so a stray writer fails loudly instead of filling ``/``.

Idempotent; never restarts a unit.  The full data volume (`smd storage add
--role data`) is v3.92.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping

DROPIN_NAME = "sigmond-data-volume.conf"
ETC_SYSTEMD = Path("/etc/systemd/system")
PROC_MOUNTINFO = Path("/proc/self/mountinfo")

# Units that write under a client's data path but are not in its deploy.toml
# unit list (the design names timestd-events and the archiver).
EXTRA_UNITS = {"hf-timestd": ("timestd-events.service", "timestd-archive.service")}


def _mounts(mountinfo: Path) -> List[str]:
    out = []
    try:
        for ln in mountinfo.read_text().splitlines():
            f = ln.partition(" - ")[0].split()
            if len(f) >= 5:
                out.append(f[4].replace("\\040", " "))
    except OSError:
        pass
    return out


def _under(path: str, mount: str) -> bool:
    return path == mount or path.startswith(mount.rstrip("/") + "/")


def data_mounts(mountinfo: Path, client_paths: Mapping[str, Iterable[str]],
                archive_mount: str) -> Dict[str, List[str]]:
    """mount point -> clients with a data path on it; / and the archive
    drive excluded (the archive never carries a live path)."""
    points = _mounts(mountinfo)
    out: Dict[str, List[str]] = {}
    for client in sorted(client_paths):
        for p in client_paths[client]:
            best = max((m for m in points if _under(p, m)), key=len, default="/")
            if best == "/" or _under(best, archive_mount) or best.startswith(("/run", "/proc", "/sys", "/dev")):
                continue
            if client not in out.setdefault(best, []):
                out[best].append(client)
    return out


def _default_resolve(client: str):
    from .clientcall import _default_resolve as r
    return r(client)


def units_for(client: str, resolve: Callable = _default_resolve) -> List[str]:
    """The client's service units, a template instance as its template."""
    names = set()
    try:
        refs = list(resolve(client))
    except (ValueError, OSError):
        refs = []
    for ref in refs:
        if getattr(ref, "orphaned", False) or getattr(ref, "kind", "service") != "service":
            continue
        u = ref.unit
        if "@" in u:
            u = u.split("@", 1)[0] + "@.service"
        names.add(u)
    names.update(EXTRA_UNITS.get(client, ()))
    return sorted(names)


def plan(mounts: Mapping[str, List[str]], units: Callable[[str], List[str]] = units_for
         ) -> Dict[str, List[str]]:
    """unit -> the data mounts it must wait for."""
    out: Dict[str, List[str]] = {}
    for mount, clients in sorted(mounts.items()):
        for c in clients:
            for u in units(c):
                if mount not in out.setdefault(u, []):
                    out[u].append(mount)
    return out


def dropin_text(mounts: List[str]) -> str:
    return ("# sigmond: data volume guard (smd storage guard)\n"
            "[Unit]\nRequiresMountsFor=" + " ".join(mounts) + "\n")


def _already(unit: str, mounts: List[str], etc_dir: Path) -> bool:
    """Every mount is already required by some drop-in of this unit (ours or
    one written by hand)."""
    d = etc_dir / f"{unit}.d"
    have = set()
    try:
        for f in d.glob("*.conf"):
            for ln in f.read_text().splitlines():
                if ln.strip().startswith("RequiresMountsFor="):
                    have.update(ln.split("=", 1)[1].split())
    except OSError:
        pass
    return all(m in have for m in mounts)


def unguarded(units_plan: Mapping[str, List[str]], etc_dir: Path = ETC_SYSTEMD) -> List[str]:
    return sorted(u for u, ms in units_plan.items() if not _already(u, ms, etc_dir))


def write_dropins(units_plan: Mapping[str, List[str]], etc_dir: Path = ETC_SYSTEMD,
                  dry_run: bool = False) -> List[str]:
    """Write the missing drop-ins; the units changed (or that would be)."""
    changed = []
    for unit in unguarded(units_plan, etc_dir):
        changed.append(unit)
        if dry_run:
            continue
        d = etc_dir / f"{unit}.d"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{DROPIN_NAME}.new"
        tmp.write_text(dropin_text(units_plan[unit]))
        os.chmod(tmp, 0o644)
        os.replace(tmp, d / DROPIN_NAME)
    return changed


def _bind(src: str, dst: str) -> None:
    subprocess.run(["mount", "--bind", "--make-private", src, dst], check=True,
                   capture_output=True, text=True)


def _unbind(dst: str) -> None:
    subprocess.run(["umount", dst], capture_output=True, text=True)


def _mkdtemp() -> str:
    return tempfile.mkdtemp(prefix="sigmond-seal-", dir="/run")


def seal_bare_dir(mount: str, mountinfo: Path = PROC_MOUNTINFO, bind=_bind, unbind=_unbind,
                  run=subprocess.run, mkdtemp=_mkdtemp, mode: str = "000") -> str:
    """Reach the directory hidden beneath ``mount`` through a non-recursive
    bind of the filesystem it lives on, and when it is empty make it root's,
    mode 0000 and immutable.  'sealed', 'already', 'not-empty: ...' or
    'error: ...'.  The temporary bind is always undone."""
    points = _mounts(mountinfo)
    parent = max((m for m in points if m != mount and _under(mount, m)), key=len, default="/")
    rel = os.path.relpath(mount, parent)
    tmp = mkdtemp()
    try:
        bind(parent, tmp)
    except (subprocess.CalledProcessError, OSError) as exc:
        os.rmdir(tmp)
        return f"error: cannot bind {parent}: {exc}"
    try:
        under = os.path.join(tmp, rel)
        if not os.path.isdir(under):
            return f"error: {mount} has no directory beneath it"
        attrs = run(["lsattr", "-d", under], capture_output=True, text=True)
        if attrs.returncode == 0 and "i" in attrs.stdout.split()[0]:
            return "already"
        if os.listdir(under):
            return f"not-empty: {len(os.listdir(under))} entries beneath {mount}; left alone"
        steps = [["chown", "root:root", under]] + ([["chmod", mode, under]] if mode else [])
        for argv in steps + [["chattr", "+i", under]]:
            r = run(argv, capture_output=True, text=True)
            if r.returncode != 0:
                return f"error: {' '.join(argv[:2])}: {(r.stderr or '').strip()}"
        return "sealed"
    finally:
        unbind(tmp)
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def client_paths(views) -> Dict[str, List[str]]:
    """client -> its file data paths (data_sinks and disk_writes)."""
    out: Dict[str, List[str]] = {}
    for name, view in views.items():
        for iv in view.instances:
            for s in iv.data_sinks:
                if s.kind == "file" and s.path:
                    out.setdefault(name, []).append(s.path)
            for dw in iv.disk_writes:
                if dw.path:
                    out.setdefault(name, []).append(dw.path)
    return out


def guard(dry_run: bool = False, say=print) -> int:
    """`smd storage guard`: write the drop-ins, seal each bare directory."""
    from .. import archive
    from ..sysview import build_system_view
    mounts = data_mounts(PROC_MOUNTINFO, client_paths(build_system_view().client_views),
                         str(archive.MOUNT))
    if not mounts:
        say("storage guard: no data mount besides / (nothing to guard)")
        return 0
    units_plan = plan(mounts)
    changed = write_dropins(units_plan, dry_run=dry_run)
    for u in changed:
        say(f"storage guard: {'would add' if dry_run else 'added'} RequiresMountsFor="
            f"{' '.join(units_plan[u])} to {u}")
    if changed and not dry_run:
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    for m in sorted(mounts):
        if dry_run:
            say(f"storage guard: would seal the bare directory beneath {m}")
            continue
        say(f"storage guard: beneath {m}: {seal_bare_dir(m)}")
    if not changed:
        say("storage guard: every writer already waits for its data mount")
    return 0

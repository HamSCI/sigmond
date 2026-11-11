"""Move reporter-keyed instances from '=' names to the instance key.

rob, 2026-11-11: unit and per-instance file names read like radiod's --
psk-recorder@AI6VN-1, not psk-recorder@AI6VN\\x3d1 -- "and fix it wherever it
encounters it in any of the legacy installs".  instance.py now names
everything by the instance key (instance_key(): '/' and '=' become '-'); this
module moves a station that still has the old names:

    /etc/<client>/AC0G=B4.toml                  -> AC0G-B4.toml
    /etc/<client>/env/AC0G=B4.env (or \\x3d)     -> env/AC0G-B4.env
    /etc/sigmond/clients/<client>@AC0G=B4.sources.toml -> @AC0G-B4.sources.toml
    /var/lib|log/<client>/AC0G\\x3dB4 (or =)     -> AC0G-B4
    /etc/systemd/system/<client>@AC0G\\x3dB4.service.d -> @AC0G-B4.service.d
    <client>@AC0G\\x3dB4.service                 -> <client>@AC0G-B4.service

The key is lossy (AC0G/B4 and a real AC0G-B4 share it), so the moved config
must name its id: a config without ``[instance] reporter_id`` gets one.

Safe to run again and again: with nothing left to move it does nothing.
Never deletes data -- every file is copied to BACKUP_DIR before it moves, a
directory moves by rename, and a move onto an existing target is refused
(and reported) unless the two files are identical.
"""
from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .instance import (
    InvalidReporterId,
    display_reporter_id,
    instance_key,
    parse_user_reporter_id,
    systemd_escape,
    unescape_systemd_instance,
)

# Every client whose units are keyed by reporter id.
CLIENTS = ("psk-recorder", "wspr-recorder", "meteor-scatter",
           "hfdl-recorder", "codar-sounder", "superdarn-sounder")

BACKUP_DIR = "var/lib/sigmond/instance-rekey-backup"


@dataclass
class Rekey:
    client: str
    storage: str                  # AC0G=B4
    key: str                      # AC0G-B4
    moves: list = field(default_factory=list)     # [(src, dst)]

    @property
    def old_unit(self) -> str:
        return f"{self.client}@{systemd_escape(self.storage)}.service"

    @property
    def new_unit(self) -> str:
        return f"{self.client}@{self.key}.service"


def _old_storage(name: str) -> Optional[str]:
    """The storage id behind an old-style name (AC0G=B4, AC0G\\x3dB4), or None
    for a name that needs no move (a key, a legacy radiod name, junk)."""
    raw = unescape_systemd_instance(name)
    if "=" not in raw:
        return None
    try:
        return parse_user_reporter_id(raw)
    except InvalidReporterId:
        return None


def _names_in(d: Path, suffix: str, prefix: str = "") -> list:
    try:
        return [p.name[len(prefix):len(p.name) - len(suffix)]
                for p in d.iterdir()
                if p.name.startswith(prefix) and p.name.endswith(suffix)]
    except OSError:
        return []


def plan(root: Path = Path("/"), units: Optional[dict] = None) -> list:
    """Every instance that still carries an old name, with the moves that
    rename it.  ``units`` maps client -> old unit instance names systemd
    knows (from systemctl); the filesystem is scanned under ``root``."""
    root = Path(root)
    units = units or {}
    out = []
    for client in CLIENTS:
        etc = root / "etc" / client
        clients_dir = root / "etc/sigmond/clients"
        sysd = root / "etc/systemd/system"
        lib = root / "var/lib" / client
        log = root / "var/log" / client
        found = set()
        for name in (_names_in(etc, ".toml") + _names_in(etc / "env", ".env")
                     + _names_in(clients_dir, ".sources.toml", f"{client}@")
                     + _names_in(sysd, ".service.d", f"{client}@")
                     + _names_in(lib, "") + _names_in(log, "")
                     + list(units.get(client, ()))):
            s = _old_storage(name)
            if s:
                found.add(s)
        for s in sorted(found):
            key = instance_key(s)
            esc = systemd_escape(s)
            r = Rekey(client, s, key)
            for old in dict.fromkeys((s, esc)):
                for src, dst in (
                        (etc / f"{old}.toml", etc / f"{key}.toml"),
                        (etc / "env" / f"{old}.env", etc / "env" / f"{key}.env"),
                        (clients_dir / f"{client}@{old}.sources.toml",
                         clients_dir / f"{client}@{key}.sources.toml"),
                        (sysd / f"{client}@{old}.service.d",
                         sysd / f"{client}@{key}.service.d"),
                        (lib / old, lib / key),
                        (log / old, log / key)):
                    if src.exists() or src.is_symlink():
                        r.moves.append((src, dst))
            out.append(r)
    return out


def _systemd_units(run: Callable) -> dict:
    """client -> old-style unit instance names systemd lists (loaded or
    enabled)."""
    found: dict = {}
    for args in (["list-units", "--all", "--no-legend", "--plain", "--no-pager"],
                 ["list-unit-files", "--no-legend", "--no-pager"]):
        try:
            r = run(["systemctl", *args, "*@*.service"],
                    capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        for line in (getattr(r, "stdout", "") or "").splitlines():
            unit = line.split()[0] if line.split() else ""
            client, at, rest = unit.partition("@")
            if at and client in CLIENTS and rest.endswith(".service"):
                inst = rest[:-len(".service")]
                if _old_storage(inst):
                    found.setdefault(client, set()).add(inst)
    return found


def _ensure_reporter_id(cfg: Path, storage: str) -> bool:
    """Give a moved config ``[instance] reporter_id`` when it has none.
    Returns True when the file was changed."""
    import tomllib
    try:
        text = cfg.read_text()
        data = tomllib.loads(text)
    except (OSError, ValueError):
        return False
    inst = data.get("instance")
    if isinstance(inst, dict) and isinstance(inst.get("reporter_id"), str) \
            and inst["reporter_id"].strip():
        return False
    line = f'reporter_id = "{display_reporter_id(storage)}"'
    if isinstance(inst, dict):
        # An [instance] table without the id: add it under the header.
        lines = text.splitlines(keepends=True)
        for i, ln in enumerate(lines):
            if ln.strip() == "[instance]":
                lines.insert(i + 1, line + "\n")
                break
        new = "".join(lines)
    else:
        new = f"[instance]\n{line}\n\n" + text
    tomllib.loads(new)                        # never write a broken file
    st = cfg.stat()
    cfg.write_text(new)
    if os.geteuid() == 0:
        os.chown(cfg, st.st_uid, st.st_gid)
    os.chmod(cfg, st.st_mode & 0o7777)
    return True


def apply(root: Path = Path("/"), *, dry_run: bool = False,
          run: Callable = subprocess.run, units: Optional[dict] = None) -> list:
    """Move every old-named instance to its key.  Returns step lines for the
    operator ("WARN: ..." for anything refused).  ``units`` overrides the
    systemctl scan (tests); ``run`` is the systemctl runner."""
    root = Path(root)
    live = units if units is not None else _systemd_units(run)
    items = plan(root, live)
    steps: list = []
    if not items:
        return steps
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = root / BACKUP_DIR / stamp
    pre = "would " if dry_run else ""

    def sysctl(*args) -> str:
        steps.append(f"{pre}systemctl {' '.join(args)}")
        if dry_run:
            return ""
        r = run(["systemctl", *args], capture_output=True, text=True)
        return (getattr(r, "stdout", "") or "").strip()

    reload_needed = False
    for r in items:
        was_enabled = was_active = False
        old_known = r.storage in {_old_storage(n)
                                  for n in live.get(r.client, ())}
        if old_known:
            if not dry_run:
                q = run(["systemctl", "is-enabled", r.old_unit],
                        capture_output=True, text=True)
                was_enabled = (getattr(q, "stdout", "") or "").strip() == "enabled"
                q = run(["systemctl", "is-active", r.old_unit],
                        capture_output=True, text=True)
                was_active = (getattr(q, "stdout", "") or "").strip() == "active"
            else:
                was_enabled = was_active = True
            if was_active:
                sysctl("stop", r.old_unit)
            if was_enabled:
                sysctl("disable", r.old_unit)
        refused = False
        for src, dst in r.moves:
            if dst.exists():
                if src.is_file() and dst.is_file() and filecmp.cmp(src, dst, shallow=False):
                    steps.append(f"{pre}rm {src} (same as {dst})")
                    if not dry_run:
                        _backup(src, root, backup)
                        src.unlink()
                    continue
                steps.append(f"WARN: {dst} already exists; not moving {src} "
                             "onto it — resolve by hand")
                refused = True
                continue
            steps.append(f"{pre}mv {src} → {dst}")
            if not dry_run:
                if src.is_file():
                    _backup(src, root, backup)
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.rename(src, dst)
            if "/systemd/system/" in str(src):
                reload_needed = True
        cfg = root / "etc" / r.client / f"{r.key}.toml"
        if not dry_run and cfg.exists() and _ensure_reporter_id(cfg, r.storage):
            steps.append(f"{cfg}: added [instance] reporter_id = "
                         f"\"{display_reporter_id(r.storage)}\"")
        if refused:
            steps.append(f"WARN: {r.client}@{display_reporter_id(r.storage)} "
                         f"left half-moved; its old unit {r.old_unit} was "
                         + ("stopped" if was_active else "not running"))
            continue
        if was_enabled or was_active or reload_needed:
            sysctl("daemon-reload")
            reload_needed = False
        if was_enabled:
            sysctl("enable", r.new_unit)
        if was_active:
            sysctl("start", r.new_unit)
    if not dry_run and backup.exists():
        steps.append(f"backup of every moved file: {backup}")
    return steps


def _backup(src: Path, root: Path, backup: Path) -> None:
    rel = src.relative_to(root) if str(src).startswith(str(root)) else Path(src.name)
    dst = backup / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

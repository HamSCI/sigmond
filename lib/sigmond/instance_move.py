"""A reporter change moves an instance; it never adds a second one.

Ledger item reporter-rename-duplicates.  When a receiver's reporter id
changes (N8UR -> N8UR-SIGMOND), `smd admin receiver apply` only added the
new instance, so a client ended with two instances on one radiod.  The
invariant here: one instance per (client, radiod).

plan_moves() decides.  apply_moves() copies the old instance's files to the
new key (a backup of each file first), rewrites ``[instance] reporter_id``,
stops and disables the old unit, enables and starts the new one.  Nothing is
deleted: the old config, env and sources file take a ``.moved`` suffix (so
no ``*.toml`` glob finds them), and state directories and drop-ins are
copied while the old ones stay.  Stdlib only; systemctl runs only through
the injectable ``run``.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .instance import (InvalidReporterId, display_reporter_id, instance_key,
                       parse_user_reporter_id, systemd_escape)
from .instance_rekey import BACKUP_DIR, _ensure_reporter_id

MOVED_SUFFIX = ".moved"


def default_run(args: list) -> tuple:
    """Run ``systemctl <args>``; returns (returncode, output)."""
    try:
        p = subprocess.run(["systemctl", *args], capture_output=True,
                           text=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


@dataclass(frozen=True)
class Existing:
    client: str
    key: str
    radiod: str


@dataclass(frozen=True)
class Move:
    client: str
    old_key: str
    new_key: str
    new_reporter_id: str
    radiod: str


def _clients_dir(etc_root: Path) -> Path:
    return Path(etc_root) / "sigmond" / "clients"


def _radiod_of(client: str, key: str, etc_root: Path) -> str:
    """The status name the instance's sources file selects, or ''."""
    p = _clients_dir(etc_root) / f"{client}@{key}.sources.toml"
    try:
        sel = tomllib.loads(p.read_text()).get("selected", [])
    except (OSError, ValueError):
        return ""
    for s in sel if isinstance(sel, list) else []:
        if isinstance(s, str) and s.startswith("radiod:"):
            return s[len("radiod:"):]
    return ""


def existing_instances(client: str, etc_root: Path,
                       run: Callable = default_run) -> list:
    """Every instance of ``client`` with a config under ``etc_root``, with
    the radiod its sources file names.  Reads files only; ``run`` is kept
    so every caller passes one runner."""
    d = Path(etc_root) / client
    out = []
    try:
        names = sorted(p.stem for p in d.glob("*.toml"))
    except OSError:
        return out
    for key in names:
        out.append(Existing(client, key, _radiod_of(client, key, etc_root)))
    return out


def _key_of(reporter_id: str):
    try:
        return instance_key(reporter_id)
    except (InvalidReporterId, ValueError):
        return None


def plan_moves(declared: list, existing: list) -> tuple:
    """(moves, refusals).  ``declared`` holds Receiver records for one
    client; ``existing`` that client's instances."""
    declared_keys = {k for k in (_key_of(r.reporter_id) for r in declared) if k}
    moves, refused = [], []
    groups: dict = {}
    for e in existing:
        if e.radiod:
            groups.setdefault((e.client, e.radiod), []).append(e)
    for (client, radiod), group in sorted(groups.items()):
        stray = [e for e in group if e.key not in declared_keys]
        if not stray:
            continue          # every instance here is still declared
        if len(group) > 1:
            names = " and ".join(f"{client}@{e.key}" for e in group)
            refused.append(
                f"{names} both sit on {radiod}; one instance per client "
                f"per radiod -- remove one by hand, then re-run")
            continue
        here = [r for r in declared if r.radiod == radiod
                and _key_of(r.reporter_id)]
        if len(here) != 1:
            continue          # nothing declared there, or ambiguous
        r = here[0]
        try:
            rid = display_reporter_id(parse_user_reporter_id(r.reporter_id))
        except (InvalidReporterId, ValueError):
            continue
        moves.append(Move(client, stray[0].key, instance_key(r.reporter_id),
                          rid, radiod))
    return moves, refused


def _backup_file(src: Path, backup: Path, label: str) -> None:
    dst = backup / label
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def _label(src: Path, etc_root: Path) -> str:
    try:
        return str(src.relative_to(etc_root.parent))
    except ValueError:
        return src.name


def _set_reporter_id(cfg: Path, new_id: str) -> None:
    text = cfg.read_text()
    inst = tomllib.loads(text).get("instance")
    if isinstance(inst, dict) and isinstance(inst.get("reporter_id"), str):
        lines = text.splitlines(keepends=True)
        in_inst = False
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("["):
                in_inst = s == "[instance]"
            elif in_inst and re.match(r"reporter_id\s*=", s):
                lines[i] = f'reporter_id = "{new_id}"\n'
                break
        new = "".join(lines)
        tomllib.loads(new)
        cfg.write_text(new)
    else:
        cfg.write_text(text)
        _ensure_reporter_id(cfg, parse_user_reporter_id(new_id))


def apply_moves(moves: list, *, etc_root: Path, var_root: Path,
                run: Callable = default_run) -> list:
    """Carry out the moves.  Returns step lines ("WARN: ..." for anything
    skipped).  A target that already exists is never overwritten."""
    etc_root, var_root = Path(etc_root), Path(var_root)
    steps: list = []
    if not moves:
        return steps
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = var_root / BACKUP_DIR[len("var/"):] / stamp
    clients = _clients_dir(etc_root)
    sysd = etc_root / "systemd" / "system"

    def sysctl(*args):
        steps.append("systemctl " + " ".join(args))
        r = run(list(args))
        if isinstance(r, tuple) and r and isinstance(r[0], int) and r[0] != 0:
            steps.append(f"WARN: systemctl {' '.join(args)} failed: "
                         f"{str(r[1]).strip()[:200]}")

    for m in moves:
        old, new = m.old_key, m.new_key
        c = m.client
        cfg_old = etc_root / c / f"{old}.toml"
        cfg_new = etc_root / c / f"{new}.toml"
        if cfg_new.exists():
            steps.append(f"WARN: {cfg_new} already exists; "
                         f"{c}@{old} not moved")
            continue
        files = [(cfg_old, cfg_new),
                 (etc_root / c / "env" / f"{old}.env",
                  etc_root / c / "env" / f"{new}.env"),
                 (clients / f"{c}@{old}.sources.toml",
                  clients / f"{c}@{new}.sources.toml")]
        copies = [(sysd / f"{c}@{systemd_escape(old)}.service.d",
                   sysd / f"{c}@{systemd_escape(new)}.service.d"),
                  (var_root / "lib" / c / old, var_root / "lib" / c / new)]
        # Backup first: everything we will read, before anything is written.
        for src, _dst in files + copies:
            if src.exists():
                _backup_file(src, backup, _label(src, etc_root))
        for src, dst in files:
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            src.rename(src.with_name(src.name + MOVED_SUFFIX))
            steps.append(f"{src} -> {dst} (old kept as {src.name}{MOVED_SUFFIX})")
        for src, dst in copies:
            if src.is_dir() and not dst.exists():
                shutil.copytree(src, dst)
                steps.append(f"copied {src} -> {dst} (old kept)")
        if cfg_new.exists():
            _set_reporter_id(cfg_new, m.new_reporter_id)
            steps.append(f"{cfg_new}: reporter_id = \"{m.new_reporter_id}\"")
        sysctl("daemon-reload")
        sysctl("disable", "--now", f"{c}@{old}.service")
        sysctl("enable", "--now", f"{c}@{new}.service")
    steps.append(f"backup of every moved file: {backup}")
    return steps

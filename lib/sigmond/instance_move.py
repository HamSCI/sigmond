"""A reporter change moves an instance; it never adds a second one.

Ledger item reporter-rename-duplicates.  When a receiver's reporter id
changes (N8UR -> N8UR-SIGMOND), `smd admin receiver apply` only added the
new instance, so a client ended with two instances on one radiod.  The
invariant here: one instance per (client, radiod).

The existing instance is the evidence that a station runs a client, so the
plan looks at every reporter-keyed client that has an instance on a radiod
a declared receiver names.  A move never creates an instance of a client
that has none there.

plan_changes() decides: a Move (OLD -> NEW), a Retire (an undeclared
duplicate beside the declared instance), a refusal (ambiguous: two strays
and one place to go), or a warning (duplicates on a radiod no receiver
names).  apply_moves() and apply_retires() carry them out, one at a time,
each transactional: stop the old unit, back up, copy and rewrite, rename the
old files, then enable and start the new unit as the old one stood.  On any
failure the renamed files return, the new copies go, and the old unit
returns to its earlier state.  A journal under the sigmond var tree marks a
move in progress, so a re-run after a crash finishes it (or rolls it back).

Nothing is deleted except the copies this module made itself: old config,
env and sources files take a ``.moved`` (or ``.retired``) suffix; state
directories and drop-ins are copied, and the old ones stay.  Stdlib only;
systemctl runs only through the injectable ``run``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .instance import (InvalidReporterId, display_reporter_id, instance_key,
                       parse_user_reporter_id, systemd_escape)
from .instance_rekey import BACKUP_DIR, CLIENTS

MOVED_SUFFIX = ".moved"
RETIRED_SUFFIX = ".retired"
JOURNAL_DIR = "lib/sigmond/instance-move"

# Seams for tests: a copy keeps its source's owner (the daemon runs as its
# own user), and the journal must belong to the user smd runs as.
_chown = os.chown
_geteuid = os.geteuid


def _trusted_uid() -> int:
    return 0


class JournalError(Exception):
    """A journal or its directory that cannot be trusted."""


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


@dataclass(frozen=True)
class Retire:
    client: str
    key: str
    radiod: str


@dataclass
class Plan:
    moves: list = field(default_factory=list)
    retires: list = field(default_factory=list)
    refusals: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


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
    try:
        names = sorted(p.stem for p in d.glob("*.toml"))
    except OSError:
        return []
    return [Existing(client, k, _radiod_of(client, k, etc_root))
            for k in names]


def _key_of(reporter_id: str):
    try:
        return instance_key(reporter_id)
    except (InvalidReporterId, ValueError):
        return None


def plan_changes(declared: list, existing: list) -> Plan:
    """Decide moves, retires, refusals and warnings.  ``declared`` holds
    every Receiver; ``existing`` the instances of one or more clients.
    Radiod names compare without regard to case."""
    plan = Plan()
    declared_keys = {k for k in (_key_of(r.reporter_id) for r in declared) if k}
    groups: dict = {}
    for e in existing:
        if e.radiod:
            groups.setdefault((e.client, e.radiod.lower()), []).append(e)
    for (client, radiod), group in sorted(groups.items()):
        radiod_name = group[0].radiod
        stray = [e for e in group if e.key not in declared_keys]
        here = {}
        for r in declared:
            k = _key_of(r.reporter_id)
            if k and r.radiod and r.radiod.lower() == radiod:
                here.setdefault(k, r)
        if not here:
            if len(group) > 1:
                names = " and ".join(f"{client}@{e.key}" for e in group)
                plan.warnings.append(
                    f"{names} both sit on {radiod_name}, which no receiver "
                    f"declares; left as they are")
            continue
        if not stray:
            continue
        present = {e.key for e in group}
        missing = [k for k in here if k not in present]
        if not missing:
            plan.retires += [Retire(client, e.key, radiod_name) for e in stray]
        elif len(stray) == 1 and len(missing) == 1:
            r = here[missing[0]]
            try:
                rid = display_reporter_id(parse_user_reporter_id(r.reporter_id))
            except (InvalidReporterId, ValueError):
                continue
            plan.moves.append(Move(client, stray[0].key, missing[0], rid,
                                   radiod_name))
        else:
            names = " and ".join(f"{client}@{e.key}" for e in stray)
            plan.refusals.append(
                f"{names} sit on {radiod_name} with no way to tell which "
                f"becomes {', '.join(client + '@' + k for k in missing)}; "
                f"one instance per client per radiod -- remove one by hand, "
                f"then re-run")
    return plan


def plan_moves(declared: list, existing: list) -> tuple:
    """(moves, refusals) of plan_changes."""
    p = plan_changes(declared, existing)
    return p.moves, p.refusals


# ---------------------------------------------------------------------------
# Identity values inside the copied files
# ---------------------------------------------------------------------------

_ID_VAR = re.compile(r"^(?:[A-Z0-9_]*_REPORTER_ID|[A-Z0-9_]*_RECEIVER_CALL)$")
_ID_VARS = ("WD_RECEIVER_CALL", "STATION_REPORTER_ID")


def _old_spellings(old_key: str, old_reporter: str) -> set:
    out = {old_key}
    for rid in (old_reporter, old_key):
        try:
            st = parse_user_reporter_id(rid)
        except (InvalidReporterId, ValueError):
            continue
        out |= {rid, st, display_reporter_id(st), instance_key(st)}
    return {s for s in out if s}


def _new_value(var: str, old_value: str, new_display: str, new_key: str,
               new_storage: str) -> str:
    if "/" in old_value:
        return new_display
    if "=" in old_value:
        return new_storage
    if var.endswith("_CALL"):
        return new_display
    return new_key


def rewrite_env(text: str, old_key: str, old_reporter: str, new_reporter: str):
    """Env text with every reporter-identity variable that names the old
    id switched to the new one.  Returns (text, changed variable names)."""
    olds = _old_spellings(old_key, old_reporter)
    st = parse_user_reporter_id(new_reporter)
    new_display, new_key = display_reporter_id(st), instance_key(st)
    changed, out = [], []
    for ln in text.splitlines(keepends=True):
        m = re.match(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)=(.*?)(\r?\n?)$", ln)
        if m and (m.group(2) in _ID_VARS or _ID_VAR.match(m.group(2))):
            raw = m.group(3).strip()
            val = raw.strip("\"'")
            if val in olds:
                nv = _new_value(m.group(2), val, new_display, new_key, st)
                q = raw[0] if raw[:1] in "\"'" else ""
                ln = f"{m.group(1)}{m.group(2)}={q}{nv}{q}{m.group(4)}"
                changed.append(m.group(2))
        out.append(ln)
    return "".join(out), changed


def _leftovers(path: Path, olds: set, label: str) -> list:
    """WARN lines for a literal old id still in ``path`` (comments skipped)."""
    warns = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return warns
    pats = [re.compile(r"(?<![A-Za-z0-9/-])" + re.escape(o)
                       + r"(?![A-Za-z0-9/=-])") for o in olds]
    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith("#"):
            continue
        if any(p.search(ln) for p in pats):
            warns.append(f"WARN: {label}:{i} still names the old reporter id; "
                         f"check it by hand")
    return warns


def _set_reporter_id(cfg: Path, new_id: str) -> None:
    text = cfg.read_text()
    data = tomllib.loads(text)
    inst = data.get("instance")
    line = f'reporter_id = "{new_id}"\n'
    lines = text.splitlines(keepends=True)
    if isinstance(inst, dict) and "reporter_id" in inst:
        in_inst = False
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("["):
                in_inst = s == "[instance]"
            elif in_inst and re.match(r"reporter_id\s*=", s):
                lines[i] = line
                break
        new = "".join(lines)
    elif isinstance(inst, dict):
        for i, ln in enumerate(lines):
            if ln.strip() == "[instance]":
                lines.insert(i + 1, line)
                break
        new = "".join(lines)
    else:
        new = "[instance]\n" + line + "\n" + text
    tomllib.loads(new)                       # never write a broken file
    cfg.write_text(new)


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

def _paths(etc_root: Path, var_root: Path, client: str, key: str) -> tuple:
    """(files, dirs) of one instance: files rename, dirs copy."""
    clients = _clients_dir(etc_root)
    sysd = etc_root / "systemd" / "system"
    files = [etc_root / client / f"{key}.toml",
             etc_root / client / "env" / f"{key}.env",
             clients / f"{client}@{key}.sources.toml"]
    dirs = [sysd / f"{client}@{systemd_escape(key)}.service.d",
            var_root / "lib" / client / key]
    return files, dirs


def _label(src: Path, etc_root: Path) -> str:
    try:
        return str(src.relative_to(etc_root.parent))
    except ValueError:
        return src.name


def _own(src: Path, dst: Path) -> None:
    if _geteuid() == 0:
        st = src.stat()
        _chown(dst, st.st_uid, st.st_gid)


def _copy_file(src: Path, dst: Path) -> None:
    shutil.copy2(src, dst)
    _own(src, dst)


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy a directory with each file's and directory's mode and owner."""
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copystat(src, dst)
    _own(src, dst)
    for child in sorted(src.iterdir()):
        target = dst / child.name
        if child.is_dir() and not child.is_symlink():
            _copy_tree(child, target)
        elif child.is_symlink():
            if not target.exists() and not target.is_symlink():
                os.symlink(os.readlink(child), target)
        else:
            _copy_file(child, target)


def _backup(srcs: list, backup: Path, etc_root: Path) -> None:
    for src in srcs:
        if not src.exists():
            continue
        dst = backup / _label(src, etc_root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)


def _rm(p: Path, roots: tuple, allowed: list) -> None:
    """Remove a copy this module made: only a path recomputed for the move
    (``allowed``) that resolves inside the etc or var tree."""
    if p not in allowed:
        raise JournalError(f"refusing to remove {p}: not one of the move's paths")
    real = p.parent.resolve() / p.name
    if not any(real.is_relative_to(Path(r).resolve()) for r in roots):
        raise JournalError(f"refusing to remove {p}: outside the etc/var trees")
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif p.exists() or p.is_symlink():
        p.unlink()


class _Sys:
    """systemctl through the injectable runner, with a step log."""

    def __init__(self, run: Callable, steps: list):
        self.run, self.steps = run, steps

    def call(self, *args) -> tuple:
        self.steps.append("systemctl " + " ".join(args))
        r = self.run(list(args))
        if isinstance(r, tuple) and len(r) == 2:
            return r
        return 0, ""

    def must(self, *args) -> None:
        rc, out = self.call(*args)
        if rc != 0:
            raise RuntimeError(f"systemctl {' '.join(args)} failed: "
                               f"{str(out).strip()[:200]}")

    def state(self, unit: str) -> tuple:
        _, en = self.call("is-enabled", unit)
        _, ac = self.call("is-active", unit)
        return (((en or "").strip().splitlines() or [""])[0] == "enabled",
                ((ac or "").strip().splitlines() or [""])[0]
                in ("active", "activating"))


_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _valid_key(k) -> bool:
    return isinstance(k, str) and bool(_KEY_RE.match(k)) and ".." not in k


def _valid_move(d) -> Move:
    """A Move from untrusted JSON, or JournalError."""
    try:
        m = Move(**{f: d[f] for f in
                    ("client", "old_key", "new_key", "new_reporter_id",
                     "radiod")})
    except (KeyError, TypeError) as exc:
        raise JournalError(f"bad move record: {exc}")
    if m.client not in CLIENTS:
        raise JournalError(f"unknown client {m.client!r}")
    if not (_valid_key(m.old_key) and _valid_key(m.new_key)):
        raise JournalError("a key is not a valid instance key")
    for v in (m.new_reporter_id, m.radiod):
        if not isinstance(v, str) or "\0" in v:
            raise JournalError("a field holds a bad value")
    try:
        parse_user_reporter_id(m.new_reporter_id)
    except (InvalidReporterId, ValueError):
        raise JournalError("new_reporter_id is not a reporter id")
    return m


def _check_owner(p: Path, what: str) -> None:
    st = p.lstat()
    if st.st_uid != _trusted_uid():
        raise JournalError(f"{what} {p} is not owned by the trusted user")
    if st.st_mode & 0o022:
        raise JournalError(f"{what} {p} is writable by group or other")


def _journal_dir(var_root: Path, create: bool = False) -> Path:
    d = Path(var_root) / JOURNAL_DIR
    if create and not d.exists():
        d.mkdir(parents=True, mode=0o700)
        os.chmod(d, 0o700)
        if _geteuid() == 0:
            _chown(d, 0, 0)
    if d.exists():
        if d.is_symlink() or not d.is_dir():
            raise JournalError(f"{d} is not a plain directory")
        _check_owner(d, "journal directory")
    return d


def _journal(var_root: Path, client: str, old: str) -> Path:
    return Path(var_root) / JOURNAL_DIR / f"{client}@{old}.json"


def _read_journal(path: Path) -> dict:
    """The journal's content, after the owner/mode checks; JournalError
    when anything is off.  Holds no paths: the move recomputes them."""
    _check_owner(path, "journal")
    try:
        j = json.loads(path.read_text())
        _valid_move(j["move"])
        idx = [int(i) for i in j["fresh"]]
        return {"move": j["move"], "was_enabled": bool(j["was_enabled"]),
                "was_active": bool(j["was_active"]),
                "stamp": re.fullmatch(r"[0-9]{8}T[0-9]{6}Z",
                                      str(j["stamp"])).group(0),
                "fresh": [i for i in idx if 0 <= i < 5]}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise JournalError(f"unreadable journal {path}: {exc}")


def pending_moves(var_root: Path, errors: list = None) -> list:
    """Moves a crash left unfinished (their journals), to resume.  A
    journal that cannot be trusted adds a line to ``errors`` and is
    skipped."""
    out = []
    try:
        d = _journal_dir(var_root)
    except JournalError as exc:
        if errors is not None:
            errors.append(f"ERROR: journal: {exc}")
        return out
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(_valid_move(_read_journal(p)["move"]))
        except JournalError as exc:
            if errors is not None:
                errors.append(f"ERROR: journal {p.name}: {exc}")
    return out


def apply_moves(moves: list, *, etc_root: Path, var_root: Path,
                run: Callable = default_run, failed: list = None) -> list:
    """Carry out the moves.  Returns step lines; a line starting "ERROR:"
    marks a move that failed and was rolled back.  A move whose journal
    exists resumes.  Each Move that failed is appended to ``failed``."""
    etc_root, var_root = Path(etc_root), Path(var_root)
    steps: list = []
    sysd = _Sys(run, steps)
    for m in moves:
        if not _move_one(m, etc_root, var_root, sysd, steps) \
                and failed is not None:
            failed.append(m)
    return steps


def _move_one(m: Move, etc_root: Path, var_root: Path, sysd: _Sys,
              steps: list) -> bool:
    """One move; True when it completed."""
    try:
        m = _valid_move(m.__dict__)
        _journal_dir(var_root)
    except JournalError as exc:
        steps.append(f"ERROR: move refused: {exc}")
        return False
    c, old, new = m.client, m.old_key, m.new_key
    old_unit, new_unit = f"{c}@{old}.service", f"{c}@{new}.service"
    jpath = _journal(var_root, c, old)
    ofiles, odirs = _paths(etc_root, var_root, c, old)
    nfiles, ndirs = _paths(etc_root, var_root, c, new)
    resume = jpath.exists()
    allowed = nfiles + ndirs
    if resume:
        try:
            j = _read_journal(jpath)
        except JournalError as exc:
            steps.append(f"ERROR: {c}@{old} not moved: {exc}")
            return False
        was_enabled, was_active = j["was_enabled"], j["was_active"]
        stamp, fresh = j["stamp"], [allowed[i] for i in j["fresh"]]
        steps.append(f"resuming the move of {c}@{old} to {c}@{new}")
    else:
        if nfiles[0].exists():
            steps.append(f"ERROR: {nfiles[0]} already exists; "
                         f"{c}@{old} not moved")
            return False
        was_enabled, was_active = sysd.state(old_unit)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fresh = [p for p in allowed if not p.exists()]
        try:
            _journal_dir(var_root, create=True)
        except JournalError as exc:
            steps.append(f"ERROR: {c}@{old} not moved: {exc}")
            return False
        fd = os.open(jpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({
                "move": m.__dict__, "was_enabled": was_enabled,
                "was_active": was_active, "stamp": stamp,
                "fresh": [allowed.index(p) for p in fresh]}))
    backup = var_root / BACKUP_DIR[len("var/"):] / stamp
    olds = _old_spellings(old, "")
    try:
        old_reporter = old
        src_cfg = ofiles[0] if ofiles[0].exists() else \
            ofiles[0].with_name(ofiles[0].name + MOVED_SUFFIX)
        try:
            old_reporter = tomllib.loads(src_cfg.read_text()) \
                .get("instance", {}).get("reporter_id") or old
        except (OSError, ValueError):
            pass
        olds = _old_spellings(old, old_reporter)
        if was_active:
            sysd.must("stop", old_unit)
        _backup(ofiles + odirs, backup, etc_root)
        for src, dst in zip(ofiles, nfiles):
            moved = src.with_name(src.name + MOVED_SUFFIX)
            use = src if src.exists() else moved
            if not use.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(use, dst)
            if src.exists():
                src.rename(moved)
                steps.append(f"{src} -> {dst} (old kept as {moved.name})")
        for src, dst in zip(odirs, ndirs):
            if src.is_dir() and (resume or not dst.exists()):
                _copy_tree(src, dst)
                steps.append(f"copied {src} -> {dst} (old kept)")
        if nfiles[0].exists():
            _set_reporter_id(nfiles[0], m.new_reporter_id)
            steps.append(f"{nfiles[0]}: reporter_id = \"{m.new_reporter_id}\"")
        if nfiles[1].exists():
            text, changed = rewrite_env(nfiles[1].read_text(), old,
                                        old_reporter, m.new_reporter_id)
            if changed:
                nfiles[1].write_text(text)
                steps.append(f"{nfiles[1]}: {', '.join(changed)} now name "
                             f"{m.new_reporter_id}")
        for f in nfiles[:2]:
            steps += _leftovers(f, olds, str(f))
        sysd.must("daemon-reload")
        if was_enabled:
            sysd.must("disable", old_unit)
            sysd.must("enable", new_unit)
        if was_active:
            sysd.must("start", new_unit)
    except Exception as exc:                                # noqa: BLE001
        steps.append(f"ERROR: moving {c}@{old} to {c}@{new} failed: {exc}")
        try:
            for src in ofiles:
                moved = src.with_name(src.name + MOVED_SUFFIX)
                if moved.exists() and not src.exists():
                    moved.rename(src)
            for p in fresh:
                _rm(p, (etc_root, var_root), allowed)
            sysd.call("daemon-reload")
            if was_enabled:
                sysd.call("disable", new_unit)
                sysd.call("enable", old_unit)
            if was_active:
                sysd.call("start", old_unit)
            jpath.unlink(missing_ok=True)
            steps.append(f"rolled back: {c}@{old} is as it was")
        except Exception as exc2:                           # noqa: BLE001
            steps.append(f"ERROR: rollback of {c}@{old} also failed: {exc2}; "
                         f"re-run receiver apply to resume")
        return False
    jpath.unlink(missing_ok=True)
    steps.append(f"backup of every moved file: {backup}")
    return True


def apply_retires(retires: list, *, etc_root: Path, var_root: Path,
                  run: Callable = default_run) -> list:
    """Retire undeclared duplicates: stop and disable the unit, back up,
    rename config, env and sources to ``.retired``.  State stays."""
    etc_root, var_root = Path(etc_root), Path(var_root)
    steps: list = []
    sysd = _Sys(run, steps)
    for r in retires:
        unit = f"{r.client}@{r.key}.service"
        files, dirs = _paths(etc_root, var_root, r.client, r.key)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = var_root / BACKUP_DIR[len("var/"):] / stamp
        was_enabled = was_active = False
        renamed = []
        try:
            was_enabled, was_active = sysd.state(unit)
            if was_active:
                sysd.must("stop", unit)
            _backup(files + dirs, backup, etc_root)
            for f in files:
                if f.exists():
                    g = f.with_name(f.name + RETIRED_SUFFIX)
                    f.rename(g)
                    renamed.append((f, g))
                    steps.append(f"{f} -> {g.name}")
            sysd.must("daemon-reload")
            if was_enabled:
                sysd.must("disable", unit)
            if renamed:
                steps.append(f"retired {r.client}@{r.key}; state kept; "
                             f"backup {backup}")
        except Exception as exc:                            # noqa: BLE001
            steps.append(f"ERROR: retiring {r.client}@{r.key} failed: {exc}")
            for f, g in renamed:
                if g.exists() and not f.exists():
                    g.rename(f)
            if was_enabled:
                sysd.call("enable", unit)
            if was_active:
                sysd.call("start", unit)
            steps.append(f"rolled back: {r.client}@{r.key} is as it was")
    return steps

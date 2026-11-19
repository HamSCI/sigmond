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
systemctl runs only through the injectable ``run``.  Root never acts by path
under a directory a service account can write: see "Root and the
directories service users can write" below.
"""
from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .instance import (InvalidReporterId, display_reporter_id, instance_key,
                       parse_user_reporter_id, systemd_escape)
from . import safe_write
from .instance_rekey import BACKUP_DIR, CLIENTS

MOVED_SUFFIX = ".moved"
RETIRED_SUFFIX = ".retired"
JOURNAL_DIR = "lib/sigmond/instance-move"

# Seams for tests: the effective uid (the privilege-drop runner and the
# rollback act on it), and the user the journal must belong to.
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


def _leftovers(text: str, olds: set, label: str) -> list:
    """WARN lines for a literal old id still in ``text`` (comments skipped)."""
    warns = []
    pats = [re.compile(r"(?<![A-Za-z0-9/-])" + re.escape(o)
                       + r"(?![A-Za-z0-9/=-])") for o in olds]
    for i, ln in enumerate(text.splitlines(), 1):
        if ln.lstrip().startswith("#"):
            continue
        if any(p.search(ln) for p in pats):
            warns.append(f"WARN: {label}:{i} still names the old reporter id; "
                         f"check it by hand")
    return warns


def _with_reporter_id(text: str, new_id: str) -> str:
    """Config text with ``[instance] reporter_id`` set to ``new_id``."""
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
    return new


# ---------------------------------------------------------------------------
# Root and the directories service users can write
#
# /etc/<client>/ (wspr-recorder's install chowns it), /etc/<client>/env/,
# /var/lib/<client>/ (chirp-recorder@.service chowns it) and /var/lib/sigmond
# (root:sigmond 2775) can all be written by a service account, which can
# swap any entry in them for a symlink at any moment.  So:
# - state trees are copied, and removed on rollback, by a child running as
#   the tree's owner (``_run_as``); root makes no mkdir, chmod, chown, utime
#   or write under those directories, and the swap races stop mattering;
# - config, env and sources files are read with O_NOFOLLOW and written whole
#   through ``safe_write.write_owned`` (one directory descriptor, a fresh
#   O_EXCL temporary file, rename over the target), already rewritten;
# - the journal and the backups live in directories only root can write,
#   reached by descriptor from /var/lib/sigmond, never by path.
# ---------------------------------------------------------------------------

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_CHILD_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


def _run_as(uid: int, gid: int, argv: list) -> tuple:
    """Run ``argv`` as uid:gid; returns (returncode, output).  As root it
    drops privileges with setpriv first (the user's own supplementary
    groups when it has a passwd entry, none otherwise), so the command can
    do only what that user could already do.  Not root, it cannot change
    user and runs as itself, which escalates nothing."""
    if _geteuid() == 0 and uid != 0:
        try:
            pwd.getpwuid(uid)
            groups = "--init-groups"
        except KeyError:
            groups = "--clear-groups"
        argv = ["setpriv", f"--reuid={uid}", f"--regid={gid}", groups,
                "--", *argv]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, check=False,
                           cwd="/", env=_CHILD_ENV, timeout=6 * 3600)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _root_only(st: os.stat_result) -> bool:
    """A directory only root can change."""
    return st.st_uid == 0 and not st.st_mode & 0o022


def _lstat_at(path: Path):
    """(lstat of ``path`` or None, fstat of its directory), looked up
    through the directory opened with O_NOFOLLOW."""
    pfd = os.open(path.parent, _DIR_FLAGS)
    try:
        parent = os.fstat(pfd)
        try:
            st = os.stat(path.name, dir_fd=pfd, follow_symlinks=False)
        except FileNotFoundError:
            st = None
    finally:
        os.close(pfd)
    return st, parent


def _copy_as_owner(src: Path, dst: Path) -> None:
    """Copy a state tree (or drop-in directory) with ``cp -a`` running as
    the source's owner: links stay links, mode, group and times are kept,
    and the owner is the source's because the copy runs as it.  A
    destination that exists aborts.  A root-owned source is copied as root
    only when both directories are root's alone."""
    st, sparent = _lstat_at(src)
    if st is None:
        raise FileNotFoundError(f"{src} vanished")
    there, dparent = _lstat_at(dst)
    if there is not None:
        raise FileExistsError(f"{dst} already exists")
    if st.st_uid == 0 and not (_root_only(sparent) and _root_only(dparent)):
        raise OSError(f"{src} belongs to root but sits where another user "
                      f"can write; refusing to copy it as root")
    rc, out = _run_as(st.st_uid, st.st_gid,
                      ["cp", "-a", "--no-dereference", "-T", "--",
                       str(src), str(dst)])
    if rc != 0:
        raise OSError(f"copying {src} as uid {st.st_uid} failed: "
                      f"{str(out).strip()[:200]}")


def _copy_text(src: Path, dst: Path, transform: Callable = None) -> str:
    """Read ``src`` (no link, no hard link, no stranger's file), apply
    ``transform``, and write the result to ``dst`` in one step with the
    source's mode and owner.  A link standing at ``dst`` is replaced,
    never followed.  Returns the text written."""
    got = safe_write.read_nofollow_stat(src)
    if got is None:
        raise FileNotFoundError(f"{src} vanished")
    text, st = got
    if transform is not None:
        text = transform(text)
    safe_write.write_owned(dst, text, mode=stat.S_IMODE(st.st_mode),
                           owner=(st.st_uid, st.st_gid),
                           owner_from_dir=False)
    return text


def _rename_at(src: Path, new_name: str) -> None:
    """Rename ``src`` within its directory, held by descriptor."""
    pfd = os.open(src.parent, _DIR_FLAGS)
    try:
        os.rename(src.name, new_name, src_dir_fd=pfd, dst_dir_fd=pfd)
    finally:
        os.close(pfd)


# ---- root-only directories under /var/lib/sigmond, by descriptor ----------

def _open_private(parent: Path, name: str, what: str, create: bool = False,
                  tighten: bool = False):
    """A descriptor on ``parent/name``, a directory the trusted user owns
    and only it can write, or None when it is absent and ``create`` is
    off.  Opened with O_NOFOLLOW through a descriptor on ``parent``, so a
    link planted at either name is refused.  ``tighten`` drops group and
    other write from a trusted directory (by descriptor) instead of
    refusing it."""
    try:
        pfd = os.open(parent, _DIR_FLAGS)
    except FileNotFoundError:
        if not create:
            return None
        os.makedirs(parent, exist_ok=True)
        pfd = os.open(parent, _DIR_FLAGS)
    except OSError as exc:
        raise JournalError(f"{parent}: {exc.strerror}; refusing to use it")
    try:
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=pfd)
            except FileExistsError:
                pass
        try:
            fd = os.open(name, _DIR_FLAGS, dir_fd=pfd)
        except FileNotFoundError:
            return None
        except OSError:
            raise JournalError(f"{what} {parent / name} is not a plain "
                               f"directory")
    finally:
        os.close(pfd)
    try:
        st = os.fstat(fd)
        if st.st_uid != _trusted_uid():
            raise JournalError(f"{what} {parent / name} is not owned by the "
                               f"trusted user")
        if st.st_mode & 0o022:
            if not tighten:
                raise JournalError(f"{what} {parent / name} is writable by "
                                   f"group or other")
            os.fchmod(fd, stat.S_IMODE(st.st_mode) & ~0o022)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _sigmond_var(var_root: Path) -> Path:
    return Path(var_root) / "lib" / "sigmond"


def _journal_fd(var_root: Path, create: bool = False):
    return _open_private(_sigmond_var(var_root), Path(JOURNAL_DIR).name,
                         "journal directory", create=create)


def _journal_dir(var_root: Path, create: bool = False) -> Path:
    """The journal directory's path, after the descriptor checks."""
    fd = _journal_fd(var_root, create=create)
    if fd is not None:
        os.close(fd)
    return Path(var_root) / JOURNAL_DIR


def _journal_name(client: str, old: str) -> str:
    return f"{client}@{old}.json"


def _read_journal_at(dfd: int, name: str) -> dict:
    """The journal's content, after the owner/mode checks; JournalError
    when anything is off.  Holds no paths: the move recomputes them."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_CLOEXEC, dir_fd=dfd)
    except OSError as exc:
        raise JournalError(f"unreadable journal {name}: {exc}")
    try:
        with os.fdopen(fd, "r") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise JournalError(f"journal {name} is not a regular file")
            if st.st_uid != _trusted_uid():
                raise JournalError(f"journal {name} is not owned by the "
                                   f"trusted user")
            if st.st_mode & 0o022:
                raise JournalError(f"journal {name} is writable by group "
                                   f"or other")
            raw = fh.read(1 << 16)
        j = json.loads(raw)
        _valid_move(j["move"])
        idx = [int(i) for i in j["fresh"]]
        return {"move": j["move"], "was_enabled": bool(j["was_enabled"]),
                "was_active": bool(j["was_active"]),
                "stamp": re.fullmatch(r"[0-9]{8}T[0-9]{6}Z",
                                      str(j["stamp"])).group(0),
                "fresh": [i for i in idx if 0 <= i < 5]}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise JournalError(f"unreadable journal {name}: {exc}")


def _journal_op(var_root: Path, fn):
    """Run ``fn(dfd)`` on the journal directory's descriptor, or return
    None when there is no journal directory."""
    fd = _journal_fd(var_root)
    if fd is None:
        return None
    try:
        return fn(fd)
    finally:
        os.close(fd)


def _journal_exists(var_root: Path, name: str) -> bool:
    def look(dfd):
        try:
            os.stat(name, dir_fd=dfd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
    return bool(_journal_op(var_root, look))


def _write_journal(var_root: Path, name: str, body: dict) -> None:
    fd = _journal_fd(var_root, create=True)
    try:
        jfd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                      | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
        with os.fdopen(jfd, "w") as fh:
            fh.write(json.dumps(body))
    finally:
        os.close(fd)


def _drop_journal(var_root: Path, name: str) -> None:
    def drop(dfd):
        try:
            os.unlink(name, dir_fd=dfd)
        except FileNotFoundError:
            pass
    _journal_op(var_root, drop)


def pending_moves(var_root: Path, errors: list = None) -> list:
    """Moves a crash left unfinished (their journals), to resume.  A
    journal that cannot be trusted adds a line to ``errors`` and is
    skipped."""
    out = []

    def scan(dfd):
        for name in sorted(n for n in os.listdir(dfd) if n.endswith(".json")):
            try:
                out.append(_valid_move(_read_journal_at(dfd, name)["move"]))
            except JournalError as exc:
                if errors is not None:
                    errors.append(f"ERROR: journal {name}: {exc}")
    try:
        _journal_op(var_root, scan)
    except (JournalError, OSError) as exc:
        # A non-root dry run cannot read root's 0700 journal; as before,
        # it sees no pending moves (the real run, as root, resumes them).
        if isinstance(exc, PermissionError) and _geteuid() != 0:
            return out
        if errors is not None:
            errors.append(f"ERROR: journal: {exc}")
    return out


# ---- backups: read without following, written only inside root's dir ------

def _copy_file_at(sfd: int, name: str, dfd: int) -> None:
    fi = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                 | os.O_CLOEXEC, dir_fd=sfd)
    try:
        st = os.fstat(fi)
        if not stat.S_ISREG(st.st_mode):
            return                       # a FIFO or device: not backed up
        fo = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
        try:
            while True:
                b = os.read(fi, 1 << 20)
                if not b:
                    break
                view = memoryview(b)
                while view:
                    view = view[os.write(fo, view):]
            os.fchmod(fo, stat.S_IMODE(st.st_mode))
            os.utime(fo, ns=(st.st_atime_ns, st.st_mtime_ns))
        finally:
            os.close(fo)
    finally:
        os.close(fi)


def _copy_entry_at(sfd: int, name: str, dfd: int) -> None:
    """Copy entry ``name`` of directory ``sfd`` into directory ``dfd``
    (root's own), never following a link on either side."""
    st = os.stat(name, dir_fd=sfd, follow_symlinks=False)
    if stat.S_ISLNK(st.st_mode):
        os.symlink(os.readlink(name, dir_fd=sfd), name, dir_fd=dfd)
    elif stat.S_ISREG(st.st_mode):
        _copy_file_at(sfd, name, dfd)
    elif stat.S_ISDIR(st.st_mode):
        os.mkdir(name, 0o700, dir_fd=dfd)
        s2 = os.open(name, _DIR_FLAGS, dir_fd=sfd)
        try:
            d2 = os.open(name, _DIR_FLAGS, dir_fd=dfd)
            try:
                for n in sorted(os.listdir(s2)):
                    _copy_entry_at(s2, n, d2)
                os.fchmod(d2, stat.S_IMODE(st.st_mode))
            finally:
                os.close(d2)
        finally:
            os.close(s2)


def _subdir_at(dfd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=dfd)
    except FileExistsError:
        pass
    return os.open(name, _DIR_FLAGS, dir_fd=dfd)


def _backup(srcs: list, var_root: Path, stamp: str, etc_root: Path) -> Path:
    """Copy each existing source into BACKUP_DIR/<stamp>/<label>.  The
    backup root is opened by descriptor and must be root's alone; a link
    planted there aborts.  An entry already backed up under this stamp
    (a resumed move) keeps the first copy."""
    bpath = Path(BACKUP_DIR[len("var/"):])
    root = _open_private(Path(var_root) / bpath.parent, bpath.name,
                         "backup directory", create=True, tighten=True)
    try:
        sfd = _subdir_at(root, stamp)
    finally:
        os.close(root)
    try:
        for src in srcs:
            parts = Path(_label(src, etc_root)).parts
            try:
                spfd = os.open(src.parent, _DIR_FLAGS)
            except FileNotFoundError:
                continue
            try:
                try:
                    os.stat(src.name, dir_fd=spfd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                fds = [sfd]
                try:
                    for part in parts[:-1]:
                        fds.append(_subdir_at(fds[-1], part))
                    try:
                        os.stat(parts[-1], dir_fd=fds[-1],
                                follow_symlinks=False)
                        continue                     # backed up already
                    except FileNotFoundError:
                        pass
                    if parts[-1] != src.name:
                        raise OSError(f"bad backup label for {src}")
                    _copy_entry_at(spfd, src.name, fds[-1])
                finally:
                    for fd in fds[1:]:
                        os.close(fd)
            finally:
                os.close(spfd)
    finally:
        os.close(sfd)
    return Path(var_root) / bpath / stamp


def _rm(p: Path, roots: tuple, allowed: list) -> None:
    """Remove a copy this module made: only a path recomputed for the move
    (``allowed``) that resolves inside the etc or var tree.  A file or link
    goes by unlink through its directory's descriptor; a directory under a
    directory others can write goes by ``rm -rf`` running as that
    directory's owner, never by root."""
    if p not in allowed:
        raise JournalError(f"refusing to remove {p}: not one of the move's paths")
    real = p.parent.resolve() / p.name
    if not any(real.is_relative_to(Path(r).resolve()) for r in roots):
        raise JournalError(f"refusing to remove {p}: outside the etc/var trees")
    try:
        pfd = os.open(p.parent, _DIR_FLAGS)
    except FileNotFoundError:
        return
    try:
        parent = os.fstat(pfd)
        try:
            st = os.stat(p.name, dir_fd=pfd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(st.st_mode):
            os.unlink(p.name, dir_fd=pfd)
            return
    finally:
        os.close(pfd)
    if _root_only(parent) and _geteuid() == 0:
        shutil.rmtree(p)                 # the fd-based walk; parent is root's
    else:
        rc, out = _run_as(parent.st_uid, parent.st_gid,
                          ["rm", "-rf", "--one-file-system", "--", str(p)])
        if rc != 0 or os.path.lexists(p):
            raise OSError(f"removing {p} as uid {parent.st_uid} failed: "
                          f"{str(out).strip()[:200]}")


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


# The journal's grammar: the plan's, minus the backslash.  "=" is no path
# trick, and a crash while moving a legacy AC0G=B4 instance must resume.
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]*$")


def _valid_key(k) -> bool:
    return isinstance(k, str) and bool(_KEY_RE.match(k)) and ".." not in k


_PLAN_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=\\-]*$")


def _valid_plan_key(k) -> bool:
    """A key as instance.py makes or finds it, legacy ``=`` and ``\\x3d``
    spellings included.  Never a path component trick."""
    return isinstance(k, str) and bool(_PLAN_KEY_RE.match(k)) and ".." not in k


def _valid_move(d, strict: bool = True) -> Move:
    """A Move from JSON (the journal's grammar) or from the planner
    (``strict=False``: a backslash spelling passes too), or JournalError."""
    try:
        m = Move(**{f: d[f] for f in
                    ("client", "old_key", "new_key", "new_reporter_id",
                     "radiod")})
    except (KeyError, TypeError) as exc:
        raise JournalError(f"bad move record: {exc}")
    if m.client not in CLIENTS:
        raise JournalError(f"unknown client {m.client!r}")
    ok = _valid_key if strict else _valid_plan_key
    if not (ok(m.old_key) and ok(m.new_key)):
        raise JournalError(
            "a key is not a valid instance key; rename legacy instances "
            "with `smd admin instance migrate` first")
    for v in (m.new_reporter_id, m.radiod):
        if not isinstance(v, str) or "\0" in v:
            raise JournalError("a field holds a bad value")
    try:
        parse_user_reporter_id(m.new_reporter_id)
    except (InvalidReporterId, ValueError):
        raise JournalError("new_reporter_id is not a reporter id")
    return m


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
        m = _valid_move(m.__dict__, strict=False)
        _journal_dir(var_root)
    except JournalError as exc:
        steps.append(f"ERROR: move refused: {exc}")
        return False
    c, old, new = m.client, m.old_key, m.new_key
    old_unit = f"{c}@{systemd_escape(old)}.service"
    new_unit = f"{c}@{systemd_escape(new)}.service"
    jname = _journal_name(c, old)
    ofiles, odirs = _paths(etc_root, var_root, c, old)
    nfiles, ndirs = _paths(etc_root, var_root, c, new)
    allowed = nfiles + ndirs
    try:
        resume = _journal_exists(var_root, jname)
    except (JournalError, OSError) as exc:
        steps.append(f"ERROR: {c}@{old} not moved: {exc}")
        return False
    if resume:
        try:
            j = _journal_op(var_root,
                            lambda dfd: _read_journal_at(dfd, jname))
        except JournalError as exc:
            steps.append(f"ERROR: {c}@{old} not moved: {exc}")
            return False
        was_enabled, was_active = j["was_enabled"], j["was_active"]
        stamp, fresh = j["stamp"], [allowed[i] for i in j["fresh"]]
        steps.append(f"resuming the move of {c}@{old} to {c}@{new}")
    else:
        if os.path.lexists(nfiles[0]):
            steps.append(f"ERROR: {nfiles[0]} already exists; "
                         f"{c}@{old} not moved")
            return False
        was_enabled, was_active = sysd.state(old_unit)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fresh = [p for p in allowed if not os.path.lexists(p)]
        try:
            _write_journal(var_root, jname, {
                "move": m.__dict__, "was_enabled": was_enabled,
                "was_active": was_active, "stamp": stamp,
                "fresh": [allowed.index(p) for p in fresh]})
        except (JournalError, OSError) as exc:
            steps.append(f"ERROR: {c}@{old} not moved: {exc}")
            return False
    olds = _old_spellings(old, "")
    try:
        old_reporter = old
        src_cfg = ofiles[0] if os.path.lexists(ofiles[0]) else \
            ofiles[0].with_name(ofiles[0].name + MOVED_SUFFIX)
        try:
            old_reporter = tomllib.loads(
                safe_write.read_nofollow(src_cfg) or "") \
                .get("instance", {}).get("reporter_id") or old
        except (OSError, ValueError):
            pass
        olds = _old_spellings(old, old_reporter)
        if was_active:
            sysd.must("stop", old_unit)
        backup = _backup(ofiles + odirs
                         + [p for p in nfiles if os.path.lexists(p)],
                         var_root, stamp, etc_root)
        changed: list = []

        def env_edit(text):
            out, ch = rewrite_env(text, old, old_reporter, m.new_reporter_id)
            changed[:] = ch
            return out
        transforms = [lambda t: _with_reporter_id(t, m.new_reporter_id),
                      env_edit, None]
        for src, dst, tf in zip(ofiles, nfiles, transforms):
            moved = src.with_name(src.name + MOVED_SUFFIX)
            use = src if os.path.lexists(src) else moved
            if not os.path.lexists(use):
                continue
            text = _copy_text(use, dst, tf)
            if os.path.lexists(src):
                _rename_at(src, moved.name)
                steps.append(f"{src} -> {dst} (old kept as {moved.name})")
            if dst == nfiles[0]:
                steps.append(f"{dst}: reporter_id = \"{m.new_reporter_id}\"")
            elif dst == nfiles[1] and changed:
                steps.append(f"{dst}: {', '.join(changed)} now name "
                             f"{m.new_reporter_id}")
            if dst in nfiles[:2]:
                steps += _leftovers(text, olds, str(dst))
        for src, dst in zip(odirs, ndirs):
            if not os.path.lexists(src):
                continue
            if os.path.lexists(dst):
                if resume and dst in fresh:        # ours, from a crashed run
                    _rm(dst, (etc_root, var_root), allowed)
                else:
                    steps.append(
                        f"WARN: {dst} already exists; {c}@{old} not moved "
                        f"-- move it aside by hand (it may hold stale state), "
                        f"then re-run")
                    raise FileExistsError(f"{dst} already exists")
            _copy_as_owner(src, dst)
            steps.append(f"copied {src} -> {dst} (old kept)")
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
                if os.path.lexists(moved) and not os.path.lexists(src):
                    _rename_at(moved, src.name)
            for p in fresh:
                _rm(p, (etc_root, var_root), allowed)
            sysd.call("daemon-reload")
            if was_enabled:
                sysd.call("disable", new_unit)
                sysd.call("enable", old_unit)
            if was_active:
                sysd.call("start", old_unit)
            _drop_journal(var_root, jname)
            steps.append(f"rolled back: {c}@{old} is as it was")
        except Exception as exc2:                           # noqa: BLE001
            steps.append(f"ERROR: rollback of {c}@{old} also failed: {exc2}; "
                         f"re-run receiver apply to resume")
        return False
    _drop_journal(var_root, jname)
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
        unit = f"{r.client}@{systemd_escape(r.key)}.service"
        files, dirs = _paths(etc_root, var_root, r.client, r.key)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        was_enabled = was_active = False
        renamed = []
        try:
            was_enabled, was_active = sysd.state(unit)
            if was_active:
                sysd.must("stop", unit)
            backup = _backup(files + dirs, var_root, stamp, etc_root)
            for f in files:
                if os.path.lexists(f):
                    g = f.with_name(f.name + RETIRED_SUFFIX)
                    _rename_at(f, g.name)
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
                if os.path.lexists(g) and not os.path.lexists(f):
                    try:
                        _rename_at(g, f.name)
                    except OSError:
                        pass
            if was_enabled:
                sysd.call("enable", unit)
            if was_active:
                sysd.call("start", unit)
            steps.append(f"rolled back: {r.client}@{r.key} is as it was")
    return steps

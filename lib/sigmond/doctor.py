"""Deploy-tree health checks — find the damage before it blocks an update.

A component checkout on a deployed host is three things at once: a git
working tree, an install target, and the runtime source — touched by
root and by each service user.  That conflation produces a small,
recurring family of faults, and every one of them is discovered the same
way today: a command fails halfway through an update, and the operator
goes digging.

This module finds them all in one pass instead.  Each check corresponds
to something that actually blocked the DASI002 update on 2026-08-15:

* ``foreign_owned`` — 2996 root-owned paths in hf-timestd, 938 in
  wspr-recorder, left by an older sigmond whose ``_git()`` ran as root.
  The current ``_git()`` delegates to ``gitowner.run_git()`` and no
  longer causes this, so the bug is fixed — but the wreckage persists on
  every host installed before the fix, and nothing detects or repairs it.
* ``git_state`` — a real uncommitted fix sat in one checkout and blocked
  the pull.  REPORTED, never repaired: discarding it blindly would have
  destroyed work, and it had to be diffed against the incoming version
  first.
* ``venv_skew`` — one venv held a private copy of ka9q-python while its
  siblings were editable off the shared checkout, so updating the
  checkout silently missed it.

Ownership is the only class safe to auto-repair, because the correct
owner is unambiguous (the checkout's own owner) and the fix is
idempotent.  Everything else is a judgement call and is reported.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional


@dataclass
class Finding:
    """One problem, attributed to a component."""

    component: str
    kind: str
    detail: str
    fixable: bool = False


# A venv legitimately belongs to its SERVICE user rather than the
# checkout owner, and editor/build caches belong to whoever last built.
# Neither blocks a git operation or an install, and both bury the real
# findings: B4's first run reported 18460 paths for `.vscode`, 3080 for
# `dist`, 2340 for `.venv`, 1523 for `.ruff_cache`, and 1330 on DASI002
# for mag-recorder's venv.
# `.git` and `*.egg-info` are deliberately IN scope: a root-owned
# `.git/index` is the signature of the bug this tool exists to find, and
# an unwritable egg-info is what blocked pip on DASI002 ("Cannot update
# time stamp of directory 'ka9q_python.egg-info').
OWNERSHIP_SKIP_DIRS = (
    'venv', '.venv',                      # belong to the service user
    '.vscode', '.ruff_cache', '.pytest_cache', '.mypy_cache',
    '__pycache__', 'dist', 'build', 'node_modules',   # tool/build detritus
)


def component_checkouts(base) -> list:
    """Component checkouts under ``base``, skipping anything unreadable.

    ``/opt/git/sigmond`` contains entries the invoking user cannot stat
    (``.ssh``), and on Python 3.13 ``Path.exists()`` propagates
    PermissionError rather than returning False — so a naive scan died
    with a traceback on a 3.13 host while working on 3.11.  A diagnostic
    has to survive the very permission problems it exists to report.
    """
    out = []
    try:
        entries = sorted(Path(base).iterdir())
    except OSError:
        return out
    for d in entries:
        try:
            if (d / '.git').exists():
                out.append(d)
        except OSError:
            continue
    return out


def foreign_owned(root, expected_uid: int, limit: int = 0,
                  skip_dirs: tuple = OWNERSHIP_SKIP_DIRS) -> list:
    """Paths under ``root`` not owned by ``expected_uid``.

    A missing tree is not an error — a component may simply not be
    installed on this host.
    """
    root = Path(root)
    out: list = []
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for name in ('.', *dirnames, *filenames):
            p = Path(dirpath) if name == '.' else Path(dirpath) / name
            try:
                if p.lstat().st_uid != expected_uid:
                    out.append(p)
            except OSError:
                continue
            if limit and len(out) >= limit:
                return out
    return out


def git_state(repo_dir, run: Optional[Callable] = None) -> dict:
    """Working-tree state: modified files, unpushed commits, detached HEAD.

    Never mutates anything.  A local modification may be a real fix that
    has not been committed yet (it was, on DASI002), so this reports and
    leaves the judgement to a human.
    """
    repo_dir = Path(repo_dir)
    # --no-optional-locks: `git status` normally refreshes the stat cache
    # and REWRITES .git/index.  Run as root that leaves a root-owned index
    # behind — recreating the very damage this tool exists to find.
    # Observed on DASI002: .git/index reappeared immediately after
    # `smd doctor --fix` had just repaired it.
    runner = run or (lambda *a: subprocess.run(
        ['git', '--no-optional-locks', '-c', f'safe.directory={repo_dir}',
         '-C', str(repo_dir), *a],
        capture_output=True, text=True))

    probe = runner('rev-parse', '--is-inside-work-tree')
    if probe.returncode != 0:
        return {'error': probe.stderr.strip() or 'not a git repository',
                'dirty': [], 'untracked': [], 'ahead': 0, 'detached': False}

    # `?? path` is UNTRACKED: it does not block a pull, and calling it a
    # modification sends the operator hunting for a local edit that isn't
    # there.  It gets its own class because it is the `.awkshim` hazard —
    # harmless in place, swept into a commit by `git add -A`.
    dirty, untracked = [], []
    for line in runner('status', '--porcelain').stdout.splitlines():
        if not line.strip():
            continue
        (untracked if line.startswith('??') else dirty).append(line[3:].strip())
    branch = runner('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    ahead_out = runner('rev-list', '--count', '@{u}..HEAD').stdout.strip()
    try:
        ahead = int(ahead_out)
    except ValueError:
        ahead = 0          # no upstream configured
    return {'error': None, 'dirty': dirty, 'untracked': untracked,
            'ahead': ahead, 'detached': branch == 'HEAD'}


def venv_skew(venvs: Iterable[str], shared: str, probe: Callable) -> list:
    """Venvs whose ka9q-python does NOT come from the shared checkout.

    A private copy cannot be updated by pulling the checkout, so the new
    code is silently absent — which is exactly how hf-timestd sat on
    3.22.0 while four sibling venvs had already moved on.  ``probe``
    returns {'location', 'version'} for a venv, or None when the package
    is absent (that venv simply does not use it).
    """
    out = []
    for v in venvs:
        info = probe(v)
        if not info:
            continue
        if not str(info.get('location', '')).startswith(str(shared)):
            out.append({'venv': v, 'location': info.get('location'),
                        'version': info.get('version')})
    return out


def exec_mismatch(services: Iterable[dict], resolve: Callable) -> list:
    """Running services whose ``/proc/<pid>/exe`` is not the binary their
    deploy tree provides.

    The radiod-swap incident this exists for: a systemd drop-in pointed
    ``ExecStart`` at a different binary than the one that had been
    installed, and "verification" checked the installed file — never the
    running process. ``resolve(pid)`` is injected (mirrors ``probe`` in
    ``venv_skew``) so this never touches real ``/proc``; on a real host
    it resolves ``/proc/<pid>/exe``.

    Each ``service`` needs at least ``name``, ``pid`` and ``expected``
    (the executable path the deploy tree provides). A missing/``None``
    ``pid`` means the service isn't running — that's not a wrong binary,
    so it is skipped, not flagged. Paths are compared after
    ``os.path.realpath`` on both sides: the deploy tree path and the
    running path may legitimately differ by symlink (e.g. a ``current``
    symlink into a versioned install dir), and flagging that would be
    noise a real defect would drown in.

    Returns one entry per problem service, each a dict with ``name``,
    ``status`` (``'mismatch'`` — running a different binary; ``'unknown'``
    — ``/proc/<pid>/exe`` could not be read, because the process exited
    mid-check or permission was denied), ``expected``, and ``running``
    (``None`` for ``'unknown'`` — guessing there would be worse than
    admitting the check couldn't see what ran). This returns data only;
    ``summarise()`` owns presentation.
    """
    out = []
    for svc in services:
        pid = svc.get('pid')
        if not pid:
            continue          # not running — not a wrong binary
        try:
            running = resolve(pid)
        except OSError:
            out.append({'name': svc['name'], 'status': 'unknown',
                        'expected': svc['expected'], 'running': None})
            continue
        expected = svc['expected']
        if os.path.realpath(running) != os.path.realpath(expected):
            out.append({'name': svc['name'], 'status': 'mismatch',
                        'expected': expected, 'running': running})
    return out


def _parse_manifest_components(text: str) -> dict:
    """Extract the ``components (live):`` block emitted by ``smd version``
    (see ``provenance.format_report``): a header line, then one line per
    component, four-space indent, name then a short SHA. The block ends
    at the next blank line or unindented line.
    """
    lines = text.splitlines()
    out: dict = {}
    in_block = False
    for line in lines:
        if line.strip() == 'components (live):':
            in_block = True
            continue
        if not in_block:
            continue
        if not line.startswith('    ') or not line.strip():
            break
        parts = line.split()
        if len(parts) != 2:
            continue          # e.g. "(no component checkouts found)"
        name, sha = parts
        out[name] = sha
    return out


def manifest_drift(live: dict, manifest_path: str) -> list:
    """Components whose live SHA has drifted from the image manifest.

    ``manifest_path`` is an image manifest in the shape ``smd version``
    emits (see ``provenance.format_report``): a ``components (live):``
    block, four-space indent, name then short SHA. Both the host copy
    (``/etc/sigmond-appliance/manifest.txt``, no ``image_sha256``) and the
    Release-attached copy (which has one) share this block shape, and
    only the block is read — the surrounding fields are ignored.

    A host installed from an image that predates this manifest, or any
    host with no manifest at all, cannot be assessed against one. That
    is not drift — it's simply unknown — so a missing or unreadable
    manifest returns ``[]`` rather than raising.

    Returns one entry per component that differs, each a dict with
    ``component``, ``status`` (``'moved'`` — the SHA changed;
    ``'live_only'`` — installed live but not present in the manifest,
    e.g. added after install; ``'manifest_only'`` — listed in the
    manifest but absent live, e.g. removed or never installed), and the
    ``manifest``/``live`` SHAs (``None`` for whichever side doesn't have
    an entry). Matching components are omitted. This returns data only;
    ``summarise()`` owns presentation.
    """
    try:
        text = Path(manifest_path).read_text()
    except OSError:
        return []

    manifest = _parse_manifest_components(text)

    out = []
    for name, manifest_sha in manifest.items():
        live_sha = live.get(name)
        if live_sha is None:
            out.append({'component': name, 'status': 'manifest_only',
                        'manifest': manifest_sha, 'live': None})
        elif live_sha != manifest_sha:
            out.append({'component': name, 'status': 'moved',
                        'manifest': manifest_sha, 'live': live_sha})
    for name in sorted(set(live) - set(manifest)):
        out.append({'component': name, 'status': 'live_only',
                    'manifest': None, 'live': live[name]})
    return out


def summarise(findings: list) -> tuple:
    """(ok, human-readable report).  ok is False if anything was found."""
    if not findings:
        return True, 'deploy trees clean — no ownership, git or venv findings'
    lines = []
    by_component: dict = {}
    for f in findings:
        by_component.setdefault(f.component, []).append(f)
    for comp in sorted(by_component):
        lines.append(f'{comp}:')
        for f in by_component[comp]:
            tag = ' [--fix repairs this]' if f.fixable else ''
            lines.append(f'    {f.kind}: {f.detail}{tag}')
    return False, '\n'.join(lines)

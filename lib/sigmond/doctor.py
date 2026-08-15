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
# checkout owner, so scanning it is pure noise — mag-recorder's venv
# alone produced 1330 false findings on the first live run.  Build
# metadata is deliberately NOT excluded: an unwritable egg-info is what
# blocked pip on DASI002 ("Cannot update time stamp of directory
# 'ka9q_python.egg-info'").
OWNERSHIP_SKIP_DIRS = ('venv',)


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

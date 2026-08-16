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


# The literal suffix Linux appends to /proc/<pid>/exe's readlink target
# when the backing file has been unlinked (e.g. replaced during an
# update) while the process kept running. No exception is raised — the
# path just carries this marker.
_DELETED_SUFFIX = ' (deleted)'


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
    noise a real defect would drown in. Paths are expected to be
    absolute, as ``/proc/<pid>/exe`` and this codebase's deploy-tree
    paths always are; a relative ``resolve()`` result is resolved
    against this process's cwd, which is the caller's responsibility to
    avoid.

    ``resolve(pid)`` contract: return the resolved executable path as a
    non-empty string, or raise ``OSError``. Either convention signals
    failure — a falsy return (``None``, ``''``) is ALSO treated as
    failure, not just a raise, because `venv_skew`'s sibling ``probe``
    signals absence by returning a falsy value rather than raising, and
    a ``resolve`` written to match that established pattern must not
    crash the whole pass (nor silently compare a falsy value as if it
    were a real path, which would produce a spurious mismatch).

    Returns one entry per problem service, each a dict with ``name``,
    ``status``, ``expected``, and ``running``. ``status`` is one of:

    * ``'mismatch'`` — running a different, still-present binary.
    * ``'unknown'`` — ``/proc/<pid>/exe`` could not be read, because the
      process exited mid-check, permission was denied, or ``resolve``
      returned a falsy value. ``running`` is ``None`` — guessing here
      would be worse than admitting the check couldn't see what ran.
    * ``'deleted'`` — the backing file was unlinked while the process
      kept running (``/proc/<pid>/exe`` resolves to the path plus the
      literal `` (deleted)`` suffix, raising nothing — an in-place
      binary replacement produces exactly this). Kept distinct from
      ``'mismatch'``: the operator response differs (restart to pick up
      the new file, vs investigate a genuine wrong-binary swap), and
      collapsing it into ``'mismatch'`` would be technically true but
      indistinguishable from the incident this check exists to catch.
      Reported as ``'deleted'`` regardless of whether the path
      underneath the suffix also differs from ``expected`` — the
      deletion itself is the anomaly worth surfacing, and guessing
      whether it's *also* a wrong-binary swap would be worse than
      simply flagging it for a human to look at.

    This returns data only; ``summarise()`` owns presentation.
    """
    out = []
    for svc in services:
        pid = svc.get('pid')
        if not pid:
            continue          # not running — not a wrong binary
        try:
            running = resolve(pid)
        except OSError:
            running = None
        if not running:
            out.append({'name': svc['name'], 'status': 'unknown',
                        'expected': svc['expected'], 'running': None})
            continue
        expected = svc['expected']
        if running.endswith(_DELETED_SUFFIX):
            out.append({'name': svc['name'], 'status': 'deleted',
                        'expected': expected, 'running': running})
            continue
        if os.path.realpath(running) != os.path.realpath(expected):
            out.append({'name': svc['name'], 'status': 'mismatch',
                        'expected': expected, 'running': running})
    return out


def _parse_manifest_components(text: str) -> Optional[dict]:
    """Extract the ``components (live):`` block emitted by ``smd version``
    (see ``provenance.format_report``): a header line, then one line per
    component, four-space indent, name then a short SHA. The block ends
    at the next blank line or unindented line.

    Returns ``None`` — not ``{}`` — when the block cannot be trusted:
    the ``components (live):`` header is missing entirely (matches the
    ``grep -q 'components (live):'`` test ``firstboot-v3.sh`` uses to
    decide whether a manifest is installable at all), or the header is
    present but yields zero parseable rows. A real manifest never ships
    with zero components — the build-side capture gate refuses to ship
    one with fewer than 10 — so a header with nothing under it is exactly
    what a truncated or corrupted capture looks like, not a real "zero
    components" image. Conflating that with an empty dict would make
    ``manifest_drift`` report every live component as newly-added, which
    is a worse failure than admitting the file can't be read.
    """
    lines = text.splitlines()
    out: dict = {}
    in_block = False
    seen_header = False
    for line in lines:
        if line.strip() == 'components (live):':
            in_block = True
            seen_header = True
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
    if not seen_header or not out:
        return None
    return out


# `git rev-parse --short` picks abbreviation length by repository size at
# call time, not a fixed 7 — the real v3.32 manifest already carries 7,
# 8 and 9-character SHAs side by side (most components at 7, ka9q-radio
# at 8, wsjtx at 9). Two independent reads of the SAME commit can
# therefore legitimately come back different lengths, so exact string
# equality is the wrong comparison: it reports drift that never
# happened on whichever components happen to straddle an abbreviation
# boundary between manifest time and live read time.
#
# A shared prefix below this many hex characters is not trustworthy
# evidence of identity — a git SHA is hex, so a 3-character prefix has
# roughly a 1-in-4096 chance of coinciding between two unrelated commits
# even in a modest-sized repo, and this tool must not silently equate
# two possibly-different commits on that kind of coincidence. 4 is
# git's own historical floor for an unambiguous abbreviation
# (`git rev-parse --short=4`); below it we refuse to call two SHAs
# equal, which means the failure mode for a too-short pair is a
# reported (and easily dismissed) false "moved" rather than a silently
# swallowed real difference — the safer direction for a drift detector.
MIN_SHA_PREFIX = 4


def _sha_equal(a: str, b: str) -> bool:
    """True if two short SHAs plausibly name the same commit.

    Compares on the common prefix, using the SHORTER of the two lengths
    — extending either string would mean guessing digits that were never
    recorded. See ``MIN_SHA_PREFIX`` for why a too-short shared prefix is
    treated as NOT matching rather than as a match.
    """
    n = min(len(a), len(b))
    if n < MIN_SHA_PREFIX:
        return False
    return a[:n] == b[:n]


def manifest_drift(live: dict, manifest_path: str) -> list:
    """Components whose live SHA has drifted from the image manifest.

    ``manifest_path`` is an image manifest in the shape ``smd version``
    emits (see ``provenance.format_report``): a ``components (live):``
    block, four-space indent, name then short SHA. Both the host copy
    (``/etc/sigmond-appliance/manifest.txt``, no ``image_sha256``) and the
    Release-attached copy (which has one) share this block shape, and
    only the block is read — the surrounding fields are ignored.

    A host installed from an image that predates this manifest, any host
    with no manifest at all, or a manifest that is present but malformed
    (its ``components (live):`` header missing, or present with zero
    parseable rows — the truncated/corrupt case) cannot be assessed
    against. None of that is drift — it's simply unknown, on the same
    footing as no manifest at all — so all three return ``[]`` rather
    than raising, and rather than reporting every live component as
    freshly added.

    SHAs are compared on their shared prefix (see ``_sha_equal``), not by
    exact string equality — ``git rev-parse --short`` abbreviation length
    varies by repo size, so the manifest and a live read of the identical
    commit will not always be the same length.

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
    if manifest is None:
        return []

    out = []
    for name, manifest_sha in manifest.items():
        live_sha = live.get(name)
        if live_sha is None:
            out.append({'component': name, 'status': 'manifest_only',
                        'manifest': manifest_sha, 'live': None})
        elif not _sha_equal(live_sha, manifest_sha):
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

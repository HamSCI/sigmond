"""smd align --apply — the steps that change a station.

Design: docs/superpowers/specs/2026-09-24-smd-align-design.md.  The planner
(align.py) never runs a process; this module runs git, always through an
injected ``run`` (a subprocess.run stand-in), so tests fake git and never touch
a real checkout.  Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from sigmond.align import AHEAD_NOTE, Item, Release

APPLIANCE_REPO = "https://github.com/HamSCI/sigmond-appliance"


class ApplyError(RuntimeError):
    """A step refused or failed; the station stays as it was at that step."""


def verify_release(rel: Release, run: Callable = subprocess.run) -> None:
    """The release tag must point at the manifest's appliance_commit."""
    if not rel.appliance_commit:
        raise ApplyError(f"release {rel.tag}: manifest records no appliance_commit — cannot verify it")
    r = run(["git", "ls-remote", APPLIANCE_REPO, f"refs/tags/{rel.tag}", f"refs/tags/{rel.tag}^{{}}"],
            capture_output=True, text=True, timeout=60)
    plain = peeled = None
    for line in (r.stdout or "").splitlines():
        sha, _, ref = line.partition("\t")
        if ref.endswith("^{}"):
            peeled = sha.strip()
        elif ref.strip() == f"refs/tags/{rel.tag}":
            plain = sha.strip()
    tag_commit = peeled or plain
    if r.returncode != 0 or not tag_commit:
        raise ApplyError(f"release tag {rel.tag} not found in {APPLIANCE_REPO}")
    if tag_commit != rel.appliance_commit:
        raise ApplyError(f"release tag {rel.tag} does not point at the manifest's appliance_commit "
                         f"({tag_commit[:12]} vs {rel.appliance_commit[:12]}) — refusing")


def _default_as_owner(owner: Optional[str], argv: list) -> list:
    return ["runuser", "-u", owner or "root", "--", *argv]


def git(repo, *args) -> list:
    """Build a git argv scoped to ``repo`` — safe.directory set, -C repo."""
    return ["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args]


_FETCH_BYTES_RE = re.compile(r"Receiving objects:.*?,\s*([\d.]+)\s*(B|KiB|MiB|GiB)")
_FETCH_BYTES_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3}


def parse_fetch_bytes(stderr: str) -> Optional[int]:
    """Bytes transferred, from git's own progress line. None if git printed
    nothing (already up to date) — never a false zero."""
    matches = _FETCH_BYTES_RE.findall(stderr or "")
    if not matches:
        return None
    value, unit = matches[-1]
    return int(round(float(value) * _FETCH_BYTES_UNITS[unit]))


def fetch(repo, owner, *, run: Callable = subprocess.run,
          as_owner: Callable = _default_as_owner) -> Optional[int]:
    """git fetch --tags by ref (never a SHA) as the checkout's owner."""
    argv = as_owner(owner, git(repo, "fetch", "--tags", "--progress", "origin"))
    r = run(argv, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise ApplyError(f"{repo}: git fetch failed: {(r.stderr or '').strip()}")
    return parse_fetch_bytes(r.stderr)


def resolve(repo, sha, *, run: Callable = subprocess.run) -> str:
    """The full 40-char SHA a ref resolves to, or ApplyError naming it."""
    argv = git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{sha}^{{commit}}")
    r = run(argv, capture_output=True, text=True, timeout=30)
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        raise ApplyError(f"{repo}: {sha} does not resolve to exactly one commit")
    return out


def in_origin_history(repo, full, *, run: Callable = subprocess.run) -> bool:
    """Whether ``full`` is reachable from any origin remote-tracking branch.

    refs/tags is deliberately excluded: a tag created locally (or left over
    from another remote) must never make an unpublished commit look
    published."""
    argv = git(repo, "for-each-ref", "--contains", full, "--format=%(refname)",
               "refs/remotes/origin")
    r = run(argv, capture_output=True, text=True, timeout=30)
    return bool((r.stdout or "").strip())


def is_ancestor(repo, a, b, *, run: Callable = subprocess.run) -> Optional[bool]:
    """True/False from merge-base --is-ancestor; None when git couldn't tell."""
    argv = git(repo, "merge-base", "--is-ancestor", a, b)
    r = run(argv, capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        return True
    if r.returncode == 1:
        return False
    return None


def dirty_files(repo, *, run: Callable = subprocess.run) -> list:
    """Paths named by `git status --porcelain`."""
    argv = git(repo, "status", "--porcelain")
    r = run(argv, capture_output=True, text=True, timeout=30)
    return [line[3:] for line in (r.stdout or "").splitlines() if line]


def reset_uvlock(repo, owner, *, run: Callable = subprocess.run,
                 as_owner: Callable = _default_as_owner) -> None:
    """Discard a uv.lock-only change — the one thing this plan may discard."""
    argv = as_owner(owner, git(repo, "checkout", "--", "uv.lock"))
    r = run(argv, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise ApplyError(f"{repo}: git checkout -- uv.lock failed: {(r.stderr or '').strip()}")


def checkout(repo, owner, full, *, run: Callable = subprocess.run,
            as_owner: Callable = _default_as_owner) -> None:
    """Detach onto ``full`` as the checkout's owner."""
    argv = as_owner(owner, git(repo, "checkout", "--detach", full))
    r = run(argv, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise ApplyError(f"{repo}: git checkout --detach {full} failed: {(r.stderr or '').strip()}")


def changed_files(repo, a, b, *, run: Callable = subprocess.run) -> set:
    """Paths that differ between two commits — drives the install-trigger check."""
    argv = git(repo, "diff", "--name-only", a, b)
    r = run(argv, capture_output=True, text=True, timeout=30)
    return {line for line in (r.stdout or "").splitlines() if line}


INSTALL_TRIGGERS = {"pyproject.toml", "uv.lock", "install.sh", "scripts/install.sh"}


def write_pin(repo, full) -> None:
    """Record the pinned SHA at <repo>/.pin, and keep git from seeing it as
    untracked cruft by adding it to .git/info/exclude (once). Plain file I/O;
    the caller chowns."""
    repo_path = Path(repo)
    (repo_path / ".pin").write_text(full + "\n")
    exclude_path = repo_path / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    lines = exclude_path.read_text().splitlines() if exclude_path.exists() else []
    if ".pin" not in lines:
        lines.append(".pin")
        exclude_path.write_text("\n".join(lines) + "\n")


def normalize_repo(url: str) -> str:
    """A comparable form of a repo URL — case, SSH-vs-HTTPS, and trailing
    slash/.git differences shouldn't count as a different repo."""
    u = (url or "").strip().lower()
    if u.startswith("git@github.com:"):
        u = "https://github.com/" + u[len("git@github.com:"):]
    u = u.rstrip("/")
    if u.endswith(".git"):
        u = u[:-len(".git")]
    return u


_DIRTY_WORKTREE_NOTE = (
    '{name}: dirty working tree ({files}) — never stashed or discarded '
    '(CONTRIBUTING.md §8); commit or revert by hand, then retry{hint}')
_UVLOCK_HINT = (' — uv.lock is regenerated by install.sh; '
                '`git checkout -- uv.lock` then retry')


@dataclass
class Ctx:
    base: Path
    run: Callable
    as_owner: Callable
    owner_of: Callable[[Path], str]
    catalog: dict
    origins: dict
    allow_rollback: bool = False
    max_bytes: Optional[int] = None
    install_missing: Optional[Callable[[str, str], None]] = None
    run_install: Optional[Callable[[Path], int]] = None
    say: Callable[[str], None] = print
    prefetched: dict = field(default_factory=dict)
    chown: Optional[Callable[[Path, str], None]] = None


@dataclass
class Step:
    component: str
    outcome: str
    detail: str = ""
    bytes: Optional[int] = None


def _move(it: Item, ctx: Ctx) -> Step:
    """Steps 1-7 of a forward move (or an allowed rollback) for one
    component. Any ApplyError from a Task-3 helper becomes Step("failed", …)
    — the caller (apply_plan) stops the rest of the plan on that outcome."""
    name = it.component
    repo = ctx.base / name
    owner = ctx.owner_of(repo)

    # 1. origin check
    catalog_entry = ctx.catalog.get(name)
    catalog_repo = getattr(catalog_entry, "repo", None) if catalog_entry is not None else None
    origin = ctx.origins.get(name)
    if origin is not None and catalog_repo is not None:
        if normalize_repo(origin) != normalize_repo(catalog_repo):
            return Step(name, "refused", f"origin points at {origin}; catalog says {catalog_repo}")

    # 2. fetch — a prefetched component (Task 6 fills this in) counts its
    # bytes toward the running total without fetching again.
    if name in ctx.prefetched:
        fetched = ctx.prefetched[name]
    else:
        try:
            fetched = fetch(repo, owner, run=ctx.run, as_owner=ctx.as_owner)
        except ApplyError as e:
            return Step(name, "failed", str(e))

    # 3. resolve the target and confirm origin vouches for it
    try:
        full = resolve(repo, it.target, run=ctx.run)
    except ApplyError as e:
        return Step(name, "failed", str(e), fetched)
    if not in_origin_history(repo, full, run=ctx.run):
        return Step(name, "refused", f"pin {full} is not in origin's history", fetched)

    # 4. dirt — a lingering .pin from before the exclude entry existed is not
    # dirt; a uv.lock-only change is the one thing this plan may discard.
    files = [f for f in dirty_files(repo, run=ctx.run) if f != ".pin"]
    detail_prefix = ""
    if files:
        if files == ["uv.lock"]:
            try:
                reset_uvlock(repo, owner, run=ctx.run, as_owner=ctx.as_owner)
            except ApplyError as e:
                return Step(name, "failed", str(e), fetched)
            detail_prefix = "reset uv.lock; "
        else:
            hint = _UVLOCK_HINT if "uv.lock" in files else ""
            note = _DIRTY_WORKTREE_NOTE.format(name=name, files=", ".join(files), hint=hint)
            return Step(name, "refused", note, fetched)

    # 5. checkout + pin
    try:
        from_full = resolve(repo, it.live, run=ctx.run)
        checkout(repo, owner, full, run=ctx.run, as_owner=ctx.as_owner)
        write_pin(repo, full)
    except ApplyError as e:
        return Step(name, "failed", str(e), fetched)
    if ctx.chown is not None:
        ctx.chown(repo / ".pin", owner)
        ctx.chown(repo / ".git" / "info" / "exclude", owner)

    # 6. install.sh when a dependency-shaped file changed
    changed = changed_files(repo, from_full, full, run=ctx.run)
    if changed & INSTALL_TRIGGERS and ctx.run_install is not None:
        rc = ctx.run_install(repo)
        if rc:
            return Step(name, "failed", f"install.sh exited {rc}", fetched)

    # 7. moved
    return Step(name, "moved", f"{detail_prefix}{it.live[:8]} -> {full[:8]}", fetched)


def apply_plan(rel: Release, items: list, ctx: Ctx) -> list:
    """Move each item toward ``rel``, in plan order (sigmond first), stopping
    at the first failed component — everything after it stays untouched."""
    steps = []
    stopped = None
    total_bytes = 0
    for it in items:
        if stopped is not None:
            steps.append(Step(it.component, "skipped", stopped))
            continue
        if it.status == "current":
            steps.append(Step(it.component, "current"))
        elif it.status == "stray":
            steps.append(Step(it.component, "left"))
        elif it.status == "missing":
            if ctx.install_missing is None:
                steps.append(Step(it.component, "refused", "no installer wired"))
            else:
                try:
                    ctx.install_missing(it.component, it.target)
                except Exception as e:  # noqa: BLE001 — an installer is foreign code
                    steps.append(Step(it.component, "failed", f"install failed: {e}"))
                    stopped = f"stopped after {it.component} failed"
                else:
                    steps.append(Step(it.component, "installed"))
        elif it.status in ("refuse", "diverged"):
            steps.append(Step(it.component, "refused", it.note))
        elif it.status == "ahead" and not ctx.allow_rollback:
            steps.append(Step(it.component, "left", AHEAD_NOTE))
        else:  # forward, or an ahead item with allow_rollback set
            step = _move(it, ctx)
            steps.append(step)
            if step.bytes:
                total_bytes += step.bytes
            if step.outcome == "failed":
                stopped = f"stopped after {it.component} failed"
            elif ctx.max_bytes is not None and total_bytes > ctx.max_bytes:
                stopped = "byte budget reached"
    if any(s.outcome == "moved" for s in steps):
        ctx.say("services still run the old code until restarted — Plan 2b")
    return steps


ALIGNED_RECORD = Path("/etc/sigmond-appliance/aligned.json")

_LOGGED_OUTCOMES = ("moved", "installed", "failed")


def _atomic_write_keeping_prev(path: Path, text: str) -> None:
    """tmp + os.replace, keeping whatever the path held before as <path>.prev."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    if path.exists():
        os.replace(path, path.with_name(path.name + ".prev"))
    os.replace(tmp, path)


def record(rel: Release, steps: list, *, manifest_path: Path,
          aligned_path: Path = ALIGNED_RECORD,
          history: Callable[[dict], None], now: Callable[[], str]) -> None:
    """Persist a completed alignment. The manifest and the aligned-release
    marker are written only when every step succeeded (none failed or was
    skipped); a history line is appended for each component that moved,
    installed, or failed, whichever way the run as a whole came out."""
    manifest_path = Path(manifest_path)
    aligned_path = Path(aligned_path)
    ok = not any(s.outcome in ("failed", "skipped") for s in steps)
    if ok:
        _atomic_write_keeping_prev(manifest_path, rel.manifest_text)
        aligned_path.parent.mkdir(parents=True, exist_ok=True)
        aligned_path.write_text(json.dumps({
            "release": rel.tag,
            "appliance_commit": rel.appliance_commit,
            "at": now(),
            "components": dict(rel.components),
            "left": {s.component: s.detail for s in steps if s.outcome == "left"},
        }, indent=2) + "\n")

    for step in steps:
        if step.outcome in _LOGGED_OUTCOMES:
            history({"at": now(),
                    "what": f"smd align --apply {rel.tag}: {step.component} {step.outcome} {step.detail}"})

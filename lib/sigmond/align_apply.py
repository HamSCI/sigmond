"""smd align --apply — the steps that change a station.

Design: docs/superpowers/specs/2026-09-24-smd-align-design.md.  The planner
(align.py) never runs a process; this module runs git, always through an
injected ``run`` (a subprocess.run stand-in), so tests fake git and never touch
a real checkout.  Stdlib only.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from sigmond.align import Release

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

"""Index each installed component's ``docs/`` folder for station-web's browser.

station-web never runs git and never walks a checkout for facts beyond the
files it serves.  This module gathers those facts once, in sigmond, and
``smd admin docs index --json`` writes them as the ``docs`` snapshot:

* each page's title and header block (Audience, Status, Verified against);
* whether the root's ``docs/INDEX.md`` links the page, and whether that row
  marks it ★ canonical (docs/contributor/docs-conventions.md §2-3);
* the page's last git commit, and its age in days.

Git runs read-only as ``git -c safe.directory=<repo> -C <repo> ...`` with a
timeout.  Whenever git cannot answer (no git, not a repository, a timeout, a
non-zero exit) the functions return None, and the index gives the page no
date.  A shallow clone dates no page from its boundary commit, because that
commit shows every file as new.

The page walk matches station-web's bounds (``routers/docs.py``): ``*.md``
names, at most ``MAX_DEPTH`` directory components, ``MAX_LISTED`` pages per
root, and no symlink that resolves outside the root.

Standard library only.
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

STALE_DAYS = 90
SKIP_DIRS = ("archive", "superpowers")

#: station-web's bounds (routers/docs.py MAX_DEPTH, MAX_LISTED, _NAME, _DIR).
MAX_DEPTH = 3
MAX_LISTED = 500
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}\.md$")
_DIR = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$")

#: The git binary; tests point it elsewhere to stand in for "git missing".
GIT = "git"
#: The docs root named "sigmond" also yields this unlisted root.
CHECKS_ROOT = ("checks", "operator/checks")

_HEADER_LINES = 40
_HEADER_KEYS = {"audience": "audience", "status": "status", "verified against": "verified"}
_HEADER = re.compile(r"^>\s*\*\*([A-Za-z ]+):\*\*\s*(.*?)\s*$")
_LINK = re.compile(r"\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_STAR = "★"
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_CEILING_DIRECTORIES", "GIT_NAMESPACE")


# --- page headers and INDEX.md ------------------------------------------------

def parse_header(text: str) -> dict:
    """Title (first ``# `` line) and header-block values from the first 40 lines."""
    out = {"title": None, "audience": None, "status": None, "verified": None}
    for line in text.splitlines()[:_HEADER_LINES]:
        if out["title"] is None and line.startswith("# "):
            out["title"] = line[2:].strip() or None
            continue
        m = _HEADER.match(line.strip())
        if m:
            key = _HEADER_KEYS.get(m.group(1).strip().lower())
            if key and out[key] is None:
                out[key] = m.group(2) or None
    return out


def _link_target(raw: str) -> str | None:
    """A link target as a path relative to ``docs/``, or None if it names no in-repo page."""
    if "://" in raw or raw.startswith(("mailto:", "/", "#")) or ":" in raw.split("/")[0]:
        return None
    path = unquote(raw.split("#", 1)[0].split("?", 1)[0])
    if not path.endswith(".md"):
        return None
    norm = posixpath.normpath(path)
    if norm == ".." or norm.startswith("../") or norm.startswith("/"):
        return None
    return norm


def index_links(index_text: str) -> dict[str, bool]:
    """Pages an INDEX.md links explicitly, each mapped to True when ★ follows its link.

    The ★ belongs to the link it follows: it counts only between that link's
    end and the next link or table-cell boundary on the same line.
    """
    out: dict[str, bool] = {}
    for line in index_text.splitlines():
        matches = list(_LINK.finditer(line))
        for i, m in enumerate(matches):
            page = _link_target(m.group(1))
            if page is None:
                continue
            stop = matches[i + 1].start() if i + 1 < len(matches) else len(line)
            tail = line[m.end():stop].split("|", 1)[0]
            out[page] = out.get(page, False) or _STAR in tail
    return out


# --- git ----------------------------------------------------------------------

def _run_git(repo: Path, args: list[str], timeout: float) -> str | None:
    """stdout of a read-only git command in ``repo``, or None on any failure."""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        r = subprocess.run(
            [GIT, "-c", f"safe.directory={repo}", "-c", "core.quotePath=false",
             "-C", str(repo), *args],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            errors="replace", timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def head_sha(repo: Path, timeout: float = 10.0) -> str | None:
    out = _run_git(Path(repo), ["rev-parse", "--verify", "--quiet", "HEAD"], timeout)
    sha = (out or "").strip()
    return sha or None


def _shallow_boundary(repo: Path, timeout: float) -> set[str] | None:
    """Boundary commits of a shallow clone (empty for a full one); None if git fails."""
    out = _run_git(repo, ["rev-parse", "--is-shallow-repository"], timeout)
    if out is None:
        return None
    if out.strip() != "true":
        return set()
    where = _run_git(repo, ["rev-parse", "--git-path", "shallow"], timeout)
    if where is None:
        return None
    path = Path(where.strip())
    if not path.is_absolute():
        path = repo / path
    try:
        return {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}
    except OSError:
        return None


def git_last_commits(repo: Path, sub: str = "docs",
                     timeout: float = 20.0) -> dict[str, int] | None:
    """Path relative to ``sub`` -> unix time of the newest commit touching it.

    One ``git log`` pass, newest first; the first time seen per path wins.
    Renames count as a delete plus an add (``--no-renames``), so a renamed
    page dates from its rename.  In a shallow clone, a page whose newest
    commit is the boundary commit gets no entry.  None when git cannot answer.
    """
    repo = Path(repo)
    boundary = _shallow_boundary(repo, timeout)
    if boundary is None:
        return None
    out = _run_git(repo, ["log", "--format=%x00%ct %H", "--name-only", "--no-renames",
                          "--", sub], timeout)
    if out is None:
        return None
    prefix = sub.strip("/") + "/"
    seen: set[str] = set()
    result: dict[str, int] = {}
    for chunk in out.split("\x00"):
        lines = chunk.splitlines()
        if not lines:
            continue
        head = lines[0].split()
        try:
            ts, sha = int(head[0]), head[1]
        except (IndexError, ValueError):
            continue
        for name in lines[1:]:
            name = name.strip()
            if not name.startswith(prefix) or name in seen:
                continue
            seen.add(name)
            if sha not in boundary:
                result[name[len(prefix):]] = ts
    return result


# --- the page walk -------------------------------------------------------------

def _pages(base: Path) -> list[str]:
    """``*.md`` under ``base`` within station-web's bounds, INDEX.md first."""
    try:
        resolved_base = base.resolve()
    except OSError:
        return []
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        rel_dir = Path(dirpath).relative_to(base)
        parts = [] if str(rel_dir) == "." else list(rel_dir.parts)
        if len(parts) >= MAX_DEPTH:
            dirnames[:] = []
        else:
            dirnames[:] = [d for d in dirnames if _DIR.fullmatch(d)]
        for f in filenames:
            if not _NAME.fullmatch(f) or ".." in f:
                continue
            try:
                p = (Path(dirpath) / f).resolve()
                if resolved_base not in p.parents or not p.is_file():
                    continue
            except OSError:
                continue
            found.append("/".join([*parts, f]))
    found.sort(key=lambda n: (n != "INDEX.md", n))
    return found[:MAX_LISTED]


def _own_docs_dir(checkout: Path, docs: Path) -> bool:
    """True when ``docs`` is a real directory inside ``checkout``.  smd runs
    this as root, so a docs/ symlink would hand root's reads of whatever it
    names to the snapshot; refuse every symlink rather than judge where it
    points."""
    try:
        return (not docs.is_symlink() and docs.is_dir()
                and checkout.resolve() in docs.resolve().parents)
    except OSError:
        return False


def _read_head(path: Path) -> str:
    try:
        with path.open(errors="replace") as fh:
            return "".join(fh.readline() for _ in range(_HEADER_LINES))
    except OSError:
        return ""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _section(name: str) -> str | None:
    for part in name.split("/")[:-1]:
        if part in SKIP_DIRS:
            return part
    return None


def _root(name: str, base: Path, listed: bool, has_index: bool,
          links: dict[str, bool], commits: dict[str, int] | None,
          prefix: str, now: float) -> dict:
    """One root's entry.  ``prefix`` locates ``base`` inside ``docs/``."""
    docs = []
    for page in _pages(base):
        key = prefix + page
        head = parse_header(_read_head(base / page))
        ts = commits.get(key) if commits is not None else None
        docs.append({
            "name": page,
            "title": head["title"],
            "audience": head["audience"],
            "status": head["status"],
            "last_commit": _iso(ts) if ts is not None else None,
            "age_days": int((now - ts) // 86400) if ts is not None else None,
            "indexed": key == "INDEX.md" or key in links,
            "canonical": links.get(key, False),
            "section": _section(page),
        })
    return {"name": name, "path": str(base), "listed": listed,
            "has_index": has_index, "documents": docs}


def _cache_entry_ok(entry) -> bool:
    """True for ``{"head": str, "commits": {str: int}}``; anything else is a miss."""
    if not isinstance(entry, dict) or not isinstance(entry.get("head"), str):
        return False
    commits = entry.get("commits")
    return isinstance(commits, dict) and all(
        isinstance(k, str) and type(v) is int for k, v in commits.items())


def build_index(components: list[tuple[str, Path]], now: float,
                cache: dict) -> tuple[dict, dict]:
    """The docs index for ``components`` ((name, checkout) pairs), and the new cache.

    Each component with a ``docs/`` folder becomes one root of the same name.
    The ``sigmond`` component also yields the unlisted ``checks`` root
    (``docs/operator/checks``).  ``cache`` maps a checkout path to
    ``{"head": sha, "commits": {...}}``; a full (not shallow) checkout whose
    HEAD still equals the cached head reuses those commits and runs no
    ``git log``.  A shallow checkout neither reads nor writes the cache.
    """
    roots: list[dict] = []
    extra: list[dict] = []
    new_cache: dict = {}
    for name, checkout in components:
        checkout = Path(checkout)
        docs = checkout / "docs"
        if not _own_docs_dir(checkout, docs):
            continue
        key = str(checkout)
        head = head_sha(checkout)
        # A shallow checkout never feeds or reads the cache: `git fetch
        # --unshallow` (or --deepen) adds history without moving HEAD, so a
        # result computed while shallow would outlive the deepening.  Its
        # git log stays cheap, because the clone holds few commits.
        full = head is not None and _shallow_boundary(checkout, 10.0) == set()
        cached = cache.get(key) if isinstance(cache, dict) else None
        if full and _cache_entry_ok(cached) and cached["head"] == head:
            commits = cached["commits"]
        else:
            commits = git_last_commits(checkout)
        if full and commits is not None:
            new_cache[key] = {"head": head, "commits": commits}

        index_path = docs / "INDEX.md"
        has_index = index_path.is_file()
        try:
            links = index_links(index_path.read_text(errors="replace")) if has_index else {}
        except OSError:
            links = {}
        roots.append(_root(name, docs, True, has_index, links, commits, "", now))

        if name == "sigmond":
            cname, csub = CHECKS_ROOT
            cbase = docs / csub
            if cbase.is_dir():
                extra.append(_root(cname, cbase, False, has_index, links, commits,
                                   csub + "/", now))
    index = {"generated_at": _iso(now), "stale_days": STALE_DAYS, "roots": roots + extra}
    return index, new_cache

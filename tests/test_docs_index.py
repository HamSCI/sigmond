"""Tests for sigmond.docs_index: the per-component docs index station-web reads.

Every git fact comes from a real temporary repository built here, with fixed
author and committer dates, so the ages under test come from git itself.
"""

import os
import subprocess
from pathlib import Path

import pytest

from sigmond import docs_index
from sigmond.docs_index import (
    SKIP_DIRS,
    STALE_DAYS,
    build_index,
    git_last_commits,
    head_sha,
    index_links,
    parse_header,
)

T0 = 1_780_000_000  # 2026-05-28 UTC, an arbitrary fixed commit time
DAY = 86_400


def _git(repo: Path, *args: str, when: int | None = None) -> str:
    env = dict(os.environ)
    env.pop("GIT_DIR", None)
    if when is not None:
        stamp = f"@{when} +0000"
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    r = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false",
         "-C", str(repo), *args],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
    return r.stdout


def _write(base: Path, rel: str, text: str = "# T\n") -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _repo(tmp: Path, files: dict[str, str], when: int = T0) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    _git(tmp, "init", "-q")
    for rel, text in files.items():
        _write(tmp, rel, text)
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", "c", when=when)
    return tmp


def _root(index: dict, name: str) -> dict:
    return next(r for r in index["roots"] if r["name"] == name)


def _doc(root: dict, name: str) -> dict:
    return next(d for d in root["documents"] if d["name"] == name)


def test_constants():
    assert STALE_DAYS == 90
    assert SKIP_DIRS == ("archive", "superpowers")


# --- parse_header ------------------------------------------------------------

CONVENTIONS_EXAMPLE = """# Documentation conventions

> **Audience:** operator
> **Status:** current
> **Verified against:** sigmond 2cd11c4 on 2026-08-24 — docs
> **Canonical for:** how docs are organised

## 1. Where things live
"""


def test_parse_header_reads_the_block():
    h = parse_header(CONVENTIONS_EXAMPLE)
    assert h == {
        "title": "Documentation conventions",
        "audience": "operator",
        "status": "current",
        "verified": "sigmond 2cd11c4 on 2026-08-24 — docs",
    }
    bare = parse_header("Some preamble\n# Just a title\n\nBody **Status:** nope\n")
    assert bare == {"title": "Just a title", "audience": None, "status": None, "verified": None}


def test_parse_header_reads_only_the_first_40_lines():
    late = "\n" * 45 + "# Late title\n> **Status:** current\n"
    assert parse_header(late) == {"title": None, "audience": None, "status": None,
                                  "verified": None}


def test_parse_header_keeps_a_qualified_status_raw():
    h = parse_header("# X\n> **Status:** shipped (stages 0–3)\n")
    assert h["status"] == "shipped (stages 0–3)"


# --- index_links -------------------------------------------------------------

EVERY_SHAPE = """# Index

| Doc | What |
|---|---|
| [OVERVIEW.md](OVERVIEW.md) | the system in one page |
| [ARCHITECTURE.md](./ARCHITECTURE.md#pipeline) ★ | the pipeline |
| [design/X.md](design/X.md) | a design note |
| [design/](design/) | design rationale |
| [sigmond docs](../../sigmond/docs/INDEX.md) ★ | the suite's front door |
"""


def test_index_links_every_shape():
    assert index_links(EVERY_SHAPE) == {
        "OVERVIEW.md": False,
        "ARCHITECTURE.md": True,
        "design/X.md": False,
    }


def test_index_links_star_belongs_to_its_own_link_only():
    text = ("| [A.md](A.md) | see also [B.md](B.md) ★ |\n"
            "| [C.md](C.md) ★ | built on [D.md](D.md) |\n"
            "- [E.md](E.md) ★ — a list item\n"
            "[F](https://example.org/F.md) ★ and [G](mailto:x@y.md)\n"
            "[`H.md`](H.md \"a title\") ★\n"
            "[archive/](archive/README.md)\n"
            "| [K.md](K.md) | a later cell says ★ marks the canonical page |\n")
    assert index_links(text) == {
        "A.md": False, "B.md": True, "C.md": True, "D.md": False,
        "E.md": True, "H.md": True, "archive/README.md": False, "K.md": False,
    }


def test_index_links_against_the_real_indexes():
    """Parser invariants on the sibling repos' live INDEX.md files.

    No ★ flag is pinned here: an honest docs edit in a sibling repo must
    not fail sigmond's suite.
    """
    repos = Path(__file__).resolve().parents[2]
    checked = 0
    for name in ("hf-timestd", "sigmond", "wspr-recorder"):
        docs = repos / name / "docs"
        p = docs / "INDEX.md"
        if not p.is_file():
            continue
        checked += 1
        links = index_links(p.read_text())
        assert isinstance(links, dict) and links, name
        for page, star in links.items():
            assert isinstance(page, str) and type(star) is bool, (name, page)
            assert not page.startswith("../") and not page.endswith("/"), (name, page)
            assert (docs / page).is_file(), (name, page)
    if not checked:
        pytest.skip("no sibling checkouts with docs/INDEX.md beside this one")


# --- git ---------------------------------------------------------------------

def test_git_last_commits_newest_per_path(tmp_path):
    repo = _repo(tmp_path / "r", {"docs/a.md": "1", "docs/b.md": "1", "other.md": "x"})
    _write(repo, "docs/a.md", "2")
    _git(repo, "commit", "-qam", "a again", when=T0 + 10 * DAY)
    _git(repo, "mv", "docs/b.md", "docs/c.md")
    _git(repo, "commit", "-qm", "rename b to c", when=T0 + 20 * DAY)

    got = git_last_commits(repo)
    assert got["a.md"] == T0 + 10 * DAY
    assert got["c.md"] == T0 + 20 * DAY
    assert "other.md" not in got and "../other.md" not in got


def test_git_unavailable_gives_none(tmp_path):
    plain = tmp_path / "plain"
    _write(plain, "docs/INDEX.md", "# Index\n[a.md](a.md)\n")
    _write(plain, "docs/a.md", "# A\n")
    assert git_last_commits(plain) is None
    assert head_sha(plain) is None

    index, new_cache = build_index([("plain", plain)], now=T0, cache={})
    root = _root(index, "plain")
    assert [d["name"] for d in root["documents"]] == ["INDEX.md", "a.md"]
    for d in root["documents"]:
        assert d["last_commit"] is None and d["age_days"] is None
    assert new_cache == {}


def test_git_missing_or_slow_gives_none(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "r", {"docs/a.md": "1"})
    monkeypatch.setattr(docs_index, "GIT", str(tmp_path / "no-such-git"))
    assert git_last_commits(repo) is None
    assert head_sha(repo) is None
    monkeypatch.undo()

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=k.get("timeout", 0))
    monkeypatch.setattr(docs_index.subprocess, "run", slow)
    assert git_last_commits(repo) is None
    assert head_sha(repo) is None


def test_git_reads_run_hardened(tmp_path, monkeypatch):
    """Root runs these reads in checkouts other users own.  No signature
    check, no fsmonitor hook, and no lazy fetch of a missing blob."""
    seen = []

    def spy(argv, **kw):
        seen.append((argv, kw.get("env") or {}))
        return subprocess.CompletedProcess(argv, 0, "abc\n", "")
    monkeypatch.setattr(docs_index.subprocess, "run", spy)
    head_sha(tmp_path)
    assert seen
    argv, env = seen[0]
    sub = argv.index("rev-parse")
    for opt in ("log.showSignature=false", "core.fsmonitor=false"):
        i = argv.index(opt)
        assert argv[i - 1] == "-c" and i < sub, argv
    assert env.get("GIT_NO_LAZY_FETCH") == "1"


def test_shallow_clone_never_dates_a_page_from_the_boundary(tmp_path):
    origin = _repo(tmp_path / "origin", {"docs/old.md": "1"}, when=T0)
    _write(origin, "docs/old.md", "2")
    _git(origin, "commit", "-qam", "old again", when=T0 + DAY)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(clone)],
                   stdin=subprocess.DEVNULL, capture_output=True, check=True)
    _write(clone, "docs/new.md", "n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "new", when=T0 + 5 * DAY)

    got = git_last_commits(clone)
    assert got == {"new.md": T0 + 5 * DAY}


def test_cache_not_trusted_across_unshallow(tmp_path):
    origin = _repo(tmp_path / "origin", {"docs/a.md": "# A\n"}, when=T0)
    _write(origin, "docs/b.md", "# B\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "b", when=T0 + DAY)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(clone)],
                   stdin=subprocess.DEVNULL, capture_output=True, check=True)

    shallow, cache = build_index([("c", clone)], now=T0 + 2 * DAY, cache={})
    assert _doc(_root(shallow, "c"), "a.md")["last_commit"] is None

    _git(clone, "fetch", "-q", "--unshallow")
    deep, cache = build_index([("c", clone)], now=T0 + 2 * DAY, cache=cache)
    root = _root(deep, "c")
    assert _doc(root, "a.md")["age_days"] == 2
    assert _doc(root, "b.md")["age_days"] == 1
    assert cache[str(clone)]["commits"] == {"a.md": T0, "b.md": T0 + DAY}


def test_cache_with_a_bad_commit_value_is_a_miss(tmp_path):
    repo = _repo(tmp_path / "r", {"docs/a.md": "# A\n"})
    head = head_sha(repo)
    for bad in ({"a.md": "yesterday"}, {"a.md": None}, {"a.md": True}, {1: T0}):
        index, cache = build_index([("r", repo)], now=T0 + DAY,
                                   cache={str(repo): {"head": head, "commits": bad}})
        assert _doc(_root(index, "r"), "a.md")["age_days"] == 1, bad
        assert cache[str(repo)]["commits"] == {"a.md": T0}


# --- build_index ---------------------------------------------------------------

def test_build_index_marks_indexed_archive_superpowers(tmp_path):
    repo = _repo(tmp_path / "hf-timestd", {
        "docs/INDEX.md": "# Index\n| [ARCH.md](ARCH.md) ★ | a |\n| [B.md](B.md) | b |\n",
        "docs/ARCH.md": CONVENTIONS_EXAMPLE,
        "docs/B.md": "# B\n",
        "docs/loose.md": "# Loose\n",
        "docs/archive/old.md": "# Old\n",
        "docs/superpowers/plans/p.md": "# Plan\n",
        "docs/notes.txt": "not markdown",
    })
    index, _ = build_index([("hf-timestd", repo)], now=T0 + DAY, cache={})
    assert index["stale_days"] == 90
    assert index["generated_at"] == "2026-05-29T20:26:40Z"
    root = _root(index, "hf-timestd")
    assert root["path"] == str(repo / "docs")
    assert root["has_index"] is True
    assert root["listed"] is True
    names = [d["name"] for d in root["documents"]]
    assert names[0] == "INDEX.md"
    assert sorted(names) == sorted(["INDEX.md", "ARCH.md", "B.md", "loose.md",
                                    "archive/old.md", "superpowers/plans/p.md"])

    def flags(n):
        d = _doc(root, n)
        return d["indexed"], d["canonical"], d["section"]
    assert flags("INDEX.md") == (True, False, None)
    assert flags("ARCH.md") == (True, True, None)
    assert flags("B.md") == (True, False, None)
    assert flags("loose.md") == (False, False, None)
    assert flags("archive/old.md") == (False, False, "archive")
    assert flags("superpowers/plans/p.md") == (False, False, "superpowers")

    arch = _doc(root, "ARCH.md")
    assert set(arch) == {"name", "title", "audience", "status", "last_commit", "age_days",
                         "indexed", "canonical", "section"}
    assert (arch["title"], arch["audience"], arch["status"]) == (
        "Documentation conventions", "operator", "current")


def test_build_index_without_index_md(tmp_path):
    repo = _repo(tmp_path / "wspr", {"docs/A.md": "# A\n"})
    index, _ = build_index([("wspr", repo)], now=T0, cache={})
    root = _root(index, "wspr")
    assert root["has_index"] is False
    assert _doc(root, "A.md")["indexed"] is False


def test_build_index_bounds_depth_names_and_count(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "c", {
        "docs/a/b/c/ok.md": "# ok\n",
        "docs/a/b/c/d/too-deep.md": "# no\n",
        "docs/.hidden/h.md": "# no\n",
        "docs/bad name.md": "# no\n",
    })
    outside = tmp_path / "outside.md"
    outside.write_text("# secret\n")
    (repo / "docs" / "escape.md").symlink_to(outside)
    index, _ = build_index([("c", repo)], now=T0, cache={})
    assert [d["name"] for d in _root(index, "c")["documents"]] == ["a/b/c/ok.md"]

    for i in range(12):
        _write(repo, f"docs/p{i:02d}.md")
    _write(repo, "docs/INDEX.md", "# I\n")
    monkeypatch.setattr(docs_index, "MAX_LISTED", 5)
    index, _ = build_index([("c", repo)], now=T0, cache={})
    names = [d["name"] for d in _root(index, "c")["documents"]]
    assert len(names) == 5 and names[0] == "INDEX.md"


def test_build_index_skips_a_component_without_docs(tmp_path):
    repo = _repo(tmp_path / "nodocs", {"README.md": "# r\n"})
    index, _ = build_index([("nodocs", repo)], now=T0, cache={})
    assert index["roots"] == []


def test_build_index_refuses_docs_that_leave_the_checkout(tmp_path):
    """smd runs this as root.  A docs/ that is a symlink, here to a folder
    outside the checkout, must not hand root's reads of that folder to the
    snapshot."""
    secret = tmp_path / "elsewhere"
    secret.mkdir()
    (secret / "notes.md").write_text("# private heading\n")
    linked = _repo(tmp_path / "linked", {"README.md": "# r\n"})
    (linked / "docs").symlink_to(secret)
    inside = _repo(tmp_path / "inside", {"real/a.md": "# a\n"})
    (inside / "docs").symlink_to(inside / "real")
    index, _ = build_index([("linked", linked), ("inside", inside)], now=T0, cache={})
    assert index["roots"] == []
    assert "private heading" not in repr(index)


def test_cache_skips_git_when_head_unchanged(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "r", {"docs/INDEX.md": "# I\n", "docs/a.md": "# A\n"})
    calls = []
    real = docs_index.git_last_commits

    def counting(*a, **k):
        calls.append(a)
        return real(*a, **k)
    monkeypatch.setattr(docs_index, "git_last_commits", counting)

    first, cache = build_index([("r", repo)], now=T0 + DAY, cache={})
    assert len(calls) == 1
    assert cache == {str(repo): {"head": head_sha(repo),
                                 "commits": {"INDEX.md": T0, "a.md": T0}}}

    second, cache2 = build_index([("r", repo)], now=T0 + 2 * DAY, cache=cache)
    assert len(calls) == 1
    assert cache2 == cache
    assert _doc(_root(second, "r"), "a.md")["last_commit"] == _doc(
        _root(first, "r"), "a.md")["last_commit"]

    _write(repo, "docs/a.md", "# A2\n")
    _git(repo, "commit", "-qam", "move head", when=T0 + 3 * DAY)
    third, cache3 = build_index([("r", repo)], now=T0 + 4 * DAY, cache=cache2)
    assert len(calls) == 2
    assert cache3[str(repo)]["commits"]["a.md"] == T0 + 3 * DAY
    assert _doc(_root(third, "r"), "a.md")["age_days"] == 1


def test_sigmond_root_is_whole_docs_and_checks_unlisted(tmp_path):
    repo = _repo(tmp_path / "sigmond", {
        "docs/INDEX.md": ("# I\n| [operator/README.md](operator/README.md) ★ | x |\n"
                          "| [operator/checks/README.md](operator/checks/README.md) ★ | y |\n"),
        "docs/operator/README.md": "# Operator\n",
        "docs/scientist/README.md": "# Scientist\n",
        "docs/operator/checks/README.md": "# Checks\n",
        "docs/operator/checks/disk.md": "# Disk\n",
    })
    other = _repo(tmp_path / "hf-timestd", {"docs/A.md": "# A\n"})
    index, _ = build_index([("sigmond", repo), ("hf-timestd", other)], now=T0, cache={})
    assert [r["name"] for r in index["roots"]] == ["sigmond", "hf-timestd", "checks"]

    sig = _root(index, "sigmond")
    assert sig["path"] == str(repo / "docs") and sig["listed"] is True
    names = {d["name"] for d in sig["documents"]}
    assert {"operator/README.md", "scientist/README.md", "operator/checks/README.md",
            "operator/checks/disk.md"} <= names

    checks = _root(index, "checks")
    assert checks["path"] == str(repo / "docs" / "operator" / "checks")
    assert checks["listed"] is False
    readme = _doc(checks, "README.md")
    assert readme["indexed"] is True and readme["canonical"] is True
    assert readme["last_commit"] == "2026-05-28T20:26:40Z"
    assert _doc(checks, "disk.md")["indexed"] is False


def test_age_days_and_stale(tmp_path):
    repo = _repo(tmp_path / "r", {"docs/a.md": "# A\n"})
    index, _ = build_index([("r", repo)], now=T0 + 100 * DAY + 3600, cache={})
    a = _doc(_root(index, "r"), "a.md")
    assert a["last_commit"] == "2026-05-28T20:26:40Z"
    assert a["age_days"] == 100
    assert a["age_days"] > index["stale_days"]

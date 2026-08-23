#!/usr/bin/env python3
"""docs-freshness — flag doc pages whose `Verified against:` sha predates their
last CONTENT edit.

A page's header carries a line like:

    > **Verified against:** sigmond <sha> on <date> — code

`stale_pages(repo_root, paths)` walks every `*.md` under the given paths
(skipping `.venv venv node_modules graphify-out .git superpowers archive`
-- `archive/` pages are frozen history and are not treated as sources of
truth to check freshness against), reads that header, and finds the last
commit that touched the file's *content* -- i.e. the newest commit in
`git log` for the file whose diff has at least one changed line that is
not itself a `Verified against:` line. (Bumping the sha alone is not a
content edit, so a commit that only touches that one line is skipped
when walking history for "last content edit".)

Freshness allows naming the last content edit's *first parent* as well as
the edit itself: an author writing a content change cannot know their own
commit's sha before committing, so the sha they bump the header to in the
same commit is necessarily the parent they started from, not the commit
they're creating. A page is therefore fresh iff its named sha equals the
last content-edit commit, or equals that commit's first parent; it is
stale iff the named sha is a **proper ancestor of the last content-edit
commit's first parent** (i.e. older than what an author editing today
could possibly have named) -- checked with `git merge-base --is-ancestor`.
A named sha that does not resolve to a commit in the repo at all is
reported separately, with reason "unknown sha". Pages with no header, or
`n/a`, are skipped entirely (only the *first* `Verified against:` line in
the file is consulted for either check, so a later mention of the phrase
in prose -- e.g. this docstring's own examples -- can't be mistaken for
the page's real header).

Both the named sha and the last-content-edit sha are normalized with
`git rev-parse --verify <x>^{commit}` before comparison, so a full
40-character sha in the header compares equal to the short form `git log`
reports. Tags are not supported in the header's sha slot -- the value
must match `[0-9a-f]{7,40}`, so a tag name there is silently invisible to
this checker (not flagged, not resolved), even though it may be a fine
human-readable qualifier for a reader.

This check is warn-only by design (spec §7: "staleness is visible, not
enforced"): the CLI always exits 0 unless `--strict` is passed and at
least one stale page was found, in which case it exits 1. If `git` isn't
on PATH (or otherwise unusable), that's reported as a warning to stderr
and the check is skipped (empty result) rather than raising -- a warn-only
tool should never hand a caller a traceback. The CLI always prints a
summary line (`docs-freshness: N stale page(s)`), even on a clean run --
silence otherwise reads as "did nothing", not "ran and found nothing".

Usage: docs-freshness.py [--strict] PATH [PATH...]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

HEADER_RE = re.compile(r"\*\*Verified against:\*\*\s+(?P<repo>[\w\-]+)\s+(?P<sha>[0-9a-f]{7,40})")
VERIFIED_LINE_RE = re.compile(r"^.*\*\*Verified against:\*\*.*$", re.M)
SKIP_DIRS = {".venv", "venv", "node_modules", "graphify-out", ".git", "superpowers", "archive"}

Stale = namedtuple("Stale", "path named_sha last_content_sha")


def _git(repo_root, *args):
    try:
        return subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True
        )
    except (FileNotFoundError, OSError) as e:
        return subprocess.CompletedProcess(args, 127, "", str(e))


def _git_available(repo_root) -> bool:
    r = _git(repo_root, "--version")
    return r.returncode == 0


def iter_md(repo_root: Path, paths: list[Path]):
    for p in paths:
        p = Path(p).resolve()
        if p.is_file() and p.suffix.lower() == ".md":
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*.md")):
                rel = f.relative_to(p)
                if not (SKIP_DIRS & set(rel.parts)):
                    yield f.resolve()


def _first_verified_line(md: Path) -> str | None:
    """The first `**Verified against:**` line in the file, or None. Only this
    line is consulted by `_named_sha`/`_has_na_header` -- a later mention of
    the phrase elsewhere in the page's prose must not be mistaken for the
    real header."""
    text = md.read_text(errors="replace")
    m = VERIFIED_LINE_RE.search(text)
    return m.group(0) if m else None


def _named_sha(md: Path) -> str | None:
    line = _first_verified_line(md)
    if line is None:
        return None
    m = HEADER_RE.search(line)
    return m.group("sha") if m else None


def _has_na_header(md: Path) -> bool:
    line = _first_verified_line(md)
    if line is None:
        return False
    return bool(re.search(r"\*\*Verified against:\*\*\s+n/a", line))


def _resolve_commit(repo_root: Path, ref: str) -> str | None:
    """Normalize any commit-ish (short sha, full sha, `<sha>^`) to its full
    sha, peeling through to a commit object. None if `ref` doesn't resolve."""
    r = _git(repo_root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def _last_content_commit(repo_root: Path, md: Path) -> str | None:
    """Newest commit touching `md` whose diff has a non-Verified-against changed line."""
    rel = md.relative_to(repo_root)
    log = _git(repo_root, "log", "--format=%h", "--", str(rel))
    if log.returncode != 0:
        return None
    commits = [c for c in log.stdout.strip().splitlines() if c]
    for c in commits:
        show = _git(repo_root, "show", c, "--", str(rel))
        if show.returncode != 0:
            continue
        for line in show.stdout.splitlines():
            if line.startswith(("+++", "---")):
                continue
            if not line.startswith(("+", "-")):
                continue
            if "Verified against:" in line:
                continue
            # a real content change line
            return c
    return commits[0] if commits else None


def stale_pages(repo_root, paths) -> list[Stale]:
    repo_root = Path(repo_root).resolve()
    if not _git_available(repo_root):
        print("docs-freshness: git not available; skipping freshness check (warn-only)",
              file=sys.stderr)
        return []
    out: list[Stale] = []
    for md in iter_md(repo_root, [Path(p) for p in paths]):
        if _has_na_header(md):
            continue
        named_raw = _named_sha(md)
        if named_raw is None:
            continue
        last_content_raw = _last_content_commit(repo_root, md)
        if last_content_raw is None:
            continue

        named = _resolve_commit(repo_root, named_raw)
        if named is None:
            out.append(Stale(md, named_raw, "unknown sha"))
            continue

        last_content = _resolve_commit(repo_root, last_content_raw)
        if last_content is None:
            # shouldn't happen -- last_content_raw came from git log itself
            continue

        if named == last_content:
            continue  # exact match: fresh

        # first parent of the last content-edit commit: the newest sha an
        # author making that edit could have named in the same commit
        parent = _resolve_commit(repo_root, f"{last_content}^1")
        allowed = parent if parent is not None else last_content
        if named == allowed:
            continue  # fresh: named the parent (or, at the root commit, itself)

        anc = _git(repo_root, "merge-base", "--is-ancestor", named, allowed)
        if anc.returncode == 0:
            out.append(Stale(md, named_raw, last_content_raw))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--strict", action="store_true", help="exit 1 if any stale page is found")
    a = ap.parse_args(argv)
    repo_root = Path(a.paths[0]).resolve()
    # find the repo root by walking up to a .git dir
    for parent in [repo_root, *repo_root.parents]:
        if (parent / ".git").exists():
            repo_root = parent
            break
    stale = stale_pages(repo_root, a.paths)
    for s in stale:
        print(f"{s.path}: Verified against {s.named_sha} but last content edit {s.last_content_sha}")
    # Always print a summary count, symmetric with docs-linkcheck.py's
    # "N broken link(s)" line -- silence otherwise reads as "did nothing"
    # rather than "ran clean" (a real point of confusion on a first run).
    print(f"docs-freshness: {len(stale)} stale page(s)")
    if not stale:
        return 0
    return 1 if a.strict else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""docs-freshness — flag doc pages whose `Verified against:` sha predates their
last CONTENT edit.

A page's header carries a line like:

    > **Verified against:** sigmond <sha> on <date> — code

`stale_pages(repo_root, paths)` walks every `*.md` under the given paths
(skipping `.venv venv node_modules graphify-out .git superpowers archive`),
reads that header, and finds the last commit that touched the file's
*content* — i.e. the newest commit in `git log` for the file whose diff has
at least one changed line that is not itself a `Verified against:` line.
(Bumping the sha alone is not a content edit, so a commit that only touches
that one line is skipped when walking history for "last content edit".)

A page is stale when its named sha is a *proper ancestor* of that last
content-edit commit (`git merge-base --is-ancestor named last_content` exits
0 and named != last_content), or when the named sha does not exist in the
repo at all (reported with reason "unknown sha"). Pages with no header, or
`n/a`, are skipped entirely.

This check is warn-only by design (spec §7: "staleness is visible, not
enforced"): the CLI always exits 0 unless `--strict` is passed and at least
one stale page was found, in which case it exits 1.

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
SKIP_DIRS = {".venv", "venv", "node_modules", "graphify-out", ".git", "superpowers", "archive"}

Stale = namedtuple("Stale", "path named_sha last_content_sha")


def _git(repo_root, *args):
    return subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True
    )


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


def _named_sha(md: Path) -> str | None:
    text = md.read_text(errors="replace")
    m = HEADER_RE.search(text)
    if m:
        return m.group("sha")
    # explicit n/a header (no sha) -> treated as "no header" (skip)
    return None


def _has_na_header(md: Path) -> bool:
    text = md.read_text(errors="replace")
    return bool(re.search(r"\*\*Verified against:\*\*\s+n/a", text))


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
    out: list[Stale] = []
    for md in iter_md(repo_root, [Path(p) for p in paths]):
        if _has_na_header(md):
            continue
        named = _named_sha(md)
        if named is None:
            continue
        last_content = _last_content_commit(repo_root, md)
        if last_content is None:
            continue
        # does the named sha even exist in this repo?
        exists = _git(repo_root, "cat-file", "-e", named)
        if exists.returncode != 0:
            out.append(Stale(md, named, "unknown sha"))
            continue
        if named == last_content:
            continue
        anc = _git(repo_root, "merge-base", "--is-ancestor", named, last_content)
        if anc.returncode == 0:
            out.append(Stale(md, named, last_content))
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
    if not stale:
        return 0
    return 1 if a.strict else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""docs-linkcheck — verify relative Markdown links (and #anchors) resolve.

Usage:  docs-linkcheck.py [--workspace DIR] PATH [PATH...]
  PATH       a .md file or a directory (searched recursively for *.md)
  --workspace DIR   parent dir holding sibling repo checkouts; links of the form
             https://github.com/HamSCI/<repo>/blob/<ref>/<path> are checked
             against DIR/<repo>/<path> when that checkout exists (else skipped)

Exit 0 when every link resolves; exit 1 and print `file:line: broken -> target`
per failure otherwise.  Stdlib only.  Skips: other http(s)/mailto links, links
inside fenced code blocks, and directories named .venv/venv/node_modules/
graphify-out/.git.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\((<[^>]+>|[^)\s]+)(?:\s+\"[^\"]*\")?\)")
GH_RE = re.compile(r"^https?://github\.com/HamSCI/([^/]+)/(?:blob|tree)/[^/]+/(.*)$")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
SKIP_DIRS = {".venv", "venv", "node_modules", "graphify-out", ".git", "__pycache__", ".pytest_cache"}


def slugify(heading: str) -> str:
    """GitHub-style anchor: lowercase, strip punctuation except - and space, spaces -> -."""
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors_of(md: Path) -> set[str]:
    seen: dict[str, int] = {}
    out: set[str] = set()
    in_fence = False
    for line in md.read_text(errors="replace").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if not m:
            continue
        slug = slugify(m.group(1))
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f"{slug}-{n}")
    # explicit <a name="..."> / id="..." anchors
    text = md.read_text(errors="replace")
    out.update(re.findall(r'(?:name|id)="([^"]+)"', text))
    return out


def iter_md(paths: list[Path]):
    for p in paths:
        if p.is_file() and p.suffix.lower() == ".md":
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*.md")):
                if not (SKIP_DIRS & set(f.relative_to(p).parts)):
                    yield f


def resolve_target(md: Path, target: str, workspace: Path):
    """Return (Path|None, anchor|None, skip: bool)."""
    if target.startswith(("mailto:", "tel:")):
        return None, None, True
    gh = GH_RE.match(target)
    if gh:
        repo, sub = gh.groups()
        sub, _, anchor = sub.partition("#")
        local = workspace / repo / sub
        if not (workspace / repo).is_dir():
            return None, None, True
        return local, anchor or None, False
    if target.startswith(("http://", "https://")):
        return None, None, True
    path_part, _, anchor = target.partition("#")
    if path_part == "":
        return md, anchor or None, False
    path_part = re.sub(r"[?].*$", "", path_part)
    return (md.parent / path_part).resolve(), anchor or None, False


def check_file(md: Path, workspace: Path) -> list[str]:
    failures: list[str] = []
    in_fence = False
    for lineno, line in enumerate(md.read_text(errors="replace").splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in LINK_RE.finditer(line):
            target = m.group(1)
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            path, anchor, skip = resolve_target(md, target, workspace)
            if skip:
                continue
            if not path.exists():
                failures.append(f"{md}:{lineno}: broken -> {target}")
                continue
            if anchor and path.is_file() and path.suffix.lower() == ".md":
                if anchor.lower() not in anchors_of(path):
                    failures.append(f"{md}:{lineno}: broken -> {target} (missing anchor)")
    return failures


def check_paths(paths: list[Path], workspace: Path) -> list[str]:
    failures: list[str] = []
    for md in iter_md([Path(p) for p in paths]):
        failures.extend(check_file(md, Path(workspace)))
    return failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--workspace", type=Path, default=None,
                    help="dir holding sibling HamSCI repo checkouts (default: parent of the first path's repo)")
    a = ap.parse_args(argv)
    ws = a.workspace or Path(a.paths[0]).resolve().parents[1]
    failures = check_paths(a.paths, ws)
    for f in failures:
        print(f)
    print(f"docs-linkcheck: {len(failures)} broken link(s)", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

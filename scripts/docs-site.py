#!/usr/bin/env python3
"""Assemble the suite's public documentation site from every repository's docs.

    python3 scripts/docs-site.py OUT_DIR [--workspace DIR]

Writes OUT_DIR/mkdocs.yml and OUT_DIR/docs/, ready for `mkdocs build`.
The repositories are the components of ours that docs/COMPONENTS.md lists
(scripts/docs-components.py), plus sigmond itself.  Each is cloned shallow
from main into the workspace (a temporary directory unless given), so the
site shows exactly what main says, never a local checkout's state.

Layout: each repository keeps its own shape under /<repo>/ (its README and
other top-level pages, and its docs/ tree), so every relative link it
already carries still resolves.  Links to github.com/HamSCI/<repo>/blob/main
for a repository in the site are rewritten to stay inside the site.
Design records (docs/superpowers, docs/audit) and archives stay out: the
site carries reference documentation only (docs-conventions §1a).
Upstream components are linked, never copied.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"superpowers", "audit", "archive", ".github", ".claude", "venv", ".venv", "node_modules"}
SKIP_FILES = {"CLAUDE.md"}
SITE_NAME = "HamSCI sigmond station documentation"


def _components():
    spec = importlib.util.spec_from_file_location("docs_components", REPO / "scripts" / "docs-components.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = {"sigmond": "https://github.com/HamSCI/sigmond"}
    for r in mod.rows():
        if r["url"] and r["name"] not in mod.UPSTREAM and r["name"] not in mod.NO_INDEX:
            out[r["name"]] = r["url"]
    return out


def _clone(url: str, dest: Path) -> bool:
    r = subprocess.run(["git", "clone", "--quiet", "--depth", "1", url, str(dest)],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print(f"docs-site: could not clone {url}: {r.stderr.strip()[:200]}", file=sys.stderr)
    return r.returncode == 0


def _pages(root: Path):
    """The Markdown pages of one repository the site carries."""
    for p in sorted(root.glob("*.md")):
        if p.name not in SKIP_FILES:
            yield p.relative_to(root)
    docs = root / "docs"
    if docs.is_dir():
        for dirpath, dirnames, filenames in os.walk(docs):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in filenames:
                if f.endswith(".md") and f not in SKIP_FILES:
                    yield (Path(dirpath) / f).relative_to(root)
            for f in filenames:
                if f.endswith((".png", ".svg", ".jpg", ".jpeg", ".gif")):
                    yield (Path(dirpath) / f).relative_to(root)


def _rewrite(text: str, here: Path, repos: dict) -> str:
    """github.com/<owner>/<repo>/blob|tree/main/<path> -> a link inside the site."""
    by_slug = {url.split("github.com/")[-1].lower(): name for name, url in repos.items()}

    def sub(m):
        slug, path = f"{m.group(1)}/{m.group(2)}".lower(), m.group(3)
        name = by_slug.get(slug)
        if not name:
            return m.group(0)
        target = Path(name) / path
        return os.path.relpath(target, here.parent).replace(os.sep, "/")
    return re.sub(r"https://github\.com/([^/\s)]+)/([^/\s)]+)/(?:blob|tree)/main/([^)\s#]+\.md)", sub, text)


_LINK = re.compile(r"(\]\()(?!https?://|mailto:|#|/)([^)\s]+?)((?:#[^)\s]*)?)(\))")


def _outlinks(docs_out: Path, repos: dict) -> None:
    """A relative link whose target the site does not carry (source, config,
    design records) points at that file on GitHub, in whichever repository
    it resolves into, so a reader still reaches it."""
    carried = {p.relative_to(docs_out).as_posix() for p in docs_out.rglob("*") if p.is_file()}
    for page in docs_out.rglob("*.md"):
        rel_page = page.relative_to(docs_out)

        def sub(m):
            target = m.group(2)
            joined = os.path.normpath((rel_page.parent / target).as_posix()).replace(os.sep, "/")
            if joined in carried or joined.startswith(".."):
                return m.group(0)
            repo, _, inside = joined.partition("/")
            if repo not in repos:
                return m.group(0)
            kind = "tree" if target.endswith("/") or not inside or "." not in inside.rsplit("/", 1)[-1] else "blob"
            return f"{m.group(1)}{repos[repo]}/{kind}/main/{inside}{m.group(3)}{m.group(4)}"
        text = page.read_text()
        new = _LINK.sub(sub, text)
        if new != text:
            page.write_text(new)


def build(out: Path, workspace: Path) -> int:
    repos = _components()
    docs_out = out / "docs"
    if docs_out.exists():
        shutil.rmtree(docs_out)
    docs_out.mkdir(parents=True)
    carried = {}
    for name, url in repos.items():
        src = workspace / name
        if not src.exists() and not _clone(url, src):
            continue
        n = 0
        for rel in _pages(src):
            dest = docs_out / name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if rel.suffix == ".md":
                text = (src / rel).read_text(errors="replace")
                dest.write_text(_rewrite(text, Path(name) / rel, repos))
            else:
                shutil.copy2(src / rel, dest)
            n += 1
        carried[name] = n
    _outlinks(docs_out, repos)
    # The site's home: sigmond's landing page, its links re-rooted under sigmond/.
    landing = (docs_out / "sigmond" / "README.md").read_text()
    landing = re.sub(r"\]\((?!https?://|#|/)([^)]+)\)", r"](sigmond/\1)", landing)
    (docs_out / "index.md").write_text(landing)

    nav = [
        {"Home": "index.md"},
        {"Start here": "sigmond/docs/README.md"},
        {"Why sigmond": "sigmond/docs/WHY-SIGMOND.md"},
        {"Why Proxmox": "sigmond/docs/WHY-PROXMOX.md"},
        {"Components": "sigmond/docs/COMPONENTS.md"},
        {"Operators": "sigmond/docs/operator/README.md"},
        {"Scientists": "sigmond/docs/scientist/README.md"},
        {"Contributors": "sigmond/docs/contributor/README.md"},
        {"Administrators": "sigmond/docs/admin/README.md"},
        {"Every sigmond page": "sigmond/docs/INDEX.md"},
        {"Each component's docs": [{name: f"{name}/docs/INDEX.md"} for name in sorted(carried)
                                   if name != "sigmond" and (docs_out / name / "docs" / "INDEX.md").exists()]},
    ]
    (out / "mkdocs.yml").write_text(_yaml(nav))
    total = sum(carried.values())
    print(f"docs-site: {len(carried)} repositories, {total} files -> {docs_out}")
    return 0


def _yaml(nav) -> str:
    lines = [f"site_name: {SITE_NAME}",
             "site_url: https://hamsci.github.io/sigmond/",
             "repo_url: https://github.com/HamSCI/sigmond",
             "docs_dir: docs",
             "theme:",
             "  name: material",
             "  features: [navigation.sections, navigation.top, search.suggest, search.highlight, content.code.copy]",
             "plugins:",
             "  - search",
             "markdown_extensions:",
             "  - tables",
             "  - admonition",
             "  - toc: {permalink: true}",
             "  - pymdownx.superfences",
             "validation:",
             "  nav: {omitted_files: ignore}",
             "  links: {not_found: warn, anchors: ignore, unrecognized_links: ignore}",
             "nav:"]

    def emit(items, depth):
        for item in items:
            (k, v), = item.items()
            if isinstance(v, list):
                lines.append(f"{'  ' * depth}- {k!r}:")
                emit(v, depth + 1)
            else:
                lines.append(f"{'  ' * depth}- {k!r}: {v}")
    emit(nav, 1)
    return "\n".join(lines) + "\n"


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    out = Path(argv[0]).resolve()
    if "--workspace" in argv:
        ws = Path(argv[argv.index("--workspace") + 1]).resolve()
        ws.mkdir(parents=True, exist_ok=True)
        return build(out, ws)
    with tempfile.TemporaryDirectory() as ws:
        return build(out, Path(ws))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

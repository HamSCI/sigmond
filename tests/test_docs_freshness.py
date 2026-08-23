"""scripts/docs-freshness.py: a page whose `Verified against: sigmond <sha>` predates
its last CONTENT edit is reported. The Verified line itself is ignored when deciding
what counts as a content edit (bumping the sha is not a content edit)."""
import importlib.util, subprocess, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "docs-freshness.py"

def _load():
    spec = importlib.util.spec_from_file_location("docs_freshness", SCRIPT)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

def _repo_with(tmp_path, steps):
    _git(tmp_path, "init", "-q"); _git(tmp_path, "config", "user.email", "t@t"); _git(tmp_path, "config", "user.name", "t")
    shas = []
    for name, text in steps:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text); _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-q", "-m", "c")
        shas.append(_git(tmp_path, "rev-parse", "--short", "HEAD"))
    return shas

def test_fresh_when_sha_is_last_content_edit(tmp_path):
    mod = _load()
    s = _repo_with(tmp_path, [("docs/a.md", "# A\n\n> **Verified against:** sigmond 0000000 on d — code\n\nbody\n")])
    # bump the sha to the commit that wrote the body → fresh
    (tmp_path/"docs/a.md").write_text(f"# A\n\n> **Verified against:** sigmond {s[0]} on d — code\n\nbody\n")
    _git(tmp_path, "commit", "-qam", "bump")
    assert mod.stale_pages(tmp_path, [tmp_path/"docs"]) == []

def test_stale_when_content_edited_after_sha(tmp_path):
    mod = _load()
    s = _repo_with(tmp_path, [("docs/a.md", "# A\n\n> **Verified against:** sigmond 0000000 on d — code\n\nbody\n")])
    (tmp_path/"docs/a.md").write_text(f"# A\n\n> **Verified against:** sigmond {s[0]} on d — code\n\nbody\n")
    _git(tmp_path, "commit", "-qam", "bump")
    (tmp_path/"docs/a.md").write_text(f"# A\n\n> **Verified against:** sigmond {s[0]} on d — code\n\nbody changed\n")
    _git(tmp_path, "commit", "-qam", "edit")
    stale = mod.stale_pages(tmp_path, [tmp_path/"docs"])
    assert len(stale) == 1 and stale[0].path.name == "a.md"

def test_pages_without_header_or_na_are_skipped(tmp_path):
    mod = _load()
    _repo_with(tmp_path, [("docs/b.md", "# B\n\n> **Verified against:** n/a\n\nbody\n"), ("docs/c.md", "# C\nno header\n")])
    assert mod.stale_pages(tmp_path, [tmp_path/"docs"]) == []

def test_real_docs_tree_runs():
    mod = _load()
    # must run without error on the real tree; staleness here is a warning, not a failure
    result = mod.stale_pages(REPO, [REPO/"docs", REPO/"README.md", REPO/"CONTRIBUTING.md"])
    assert isinstance(result, list)

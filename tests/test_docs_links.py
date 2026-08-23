# sigmond/tests/test_docs_links.py
"""Every relative Markdown link in the docs must resolve.

Runs scripts/docs-linkcheck.py over the doc surface.  External http(s) links
are NOT checked here (network-free); GitHub links into HamSCI/<repo> are mapped
to the local workspace when that sibling checkout exists, otherwise skipped.
"""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "docs-linkcheck.py"


def _load():
    spec = importlib.util.spec_from_file_location("docs_linkcheck", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["docs_linkcheck"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_doc_links_resolve():
    mod = _load()
    roots = [REPO / "docs", REPO / "README.md", REPO / "CONTRIBUTING.md", REPO / "CLAUDE.md"]
    failures = mod.check_paths(roots, workspace=REPO.parent)
    assert not failures, "\n" + "\n".join(failures)


def test_checker_detects_broken_link(tmp_path):
    mod = _load()
    (tmp_path / "a.md").write_text("see [b](b.md) and [c](c.md#sec) and [ok](#here)\n\n## here\n")
    (tmp_path / "b.md").write_text("# B\n")
    failures = mod.check_paths([tmp_path], workspace=tmp_path)
    assert any("c.md" in f for f in failures)
    assert not any("b.md" in f for f in failures)
    assert not any("#here" in f for f in failures)


def test_checker_checks_anchor_in_target(tmp_path):
    mod = _load()
    (tmp_path / "a.md").write_text("[x](b.md#real-heading) [y](b.md#nope)\n")
    (tmp_path / "b.md").write_text("# Title\n\n## Real heading\n")
    failures = mod.check_paths([tmp_path], workspace=tmp_path)
    assert any("#nope" in f for f in failures)
    assert not any("#real-heading" in f for f in failures)


def test_checker_handles_angle_bracket_target_with_space(tmp_path):
    mod = _load()
    (tmp_path / "sub dir").mkdir()
    (tmp_path / "sub dir" / "x.md").write_text("# X\n")
    (tmp_path / "a.md").write_text(
        "ok: [x](<sub dir/x.md>) broken: [y](<sub dir/missing.md>)\n"
    )
    failures = mod.check_paths([tmp_path], workspace=tmp_path)
    assert any("missing.md" in f for f in failures)
    assert not any("sub dir/x.md" in f for f in failures)

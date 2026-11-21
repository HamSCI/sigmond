"""The public site's smd reference renders from bin/smd's parser at build
time (scripts/docs-cli.py), the walk station-web's Commands page uses.  No
copy is committed, so this checks only that the render works and covers
every top-level command.
"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_reference_lists_every_top_level_command():
    spec = importlib.util.spec_from_file_location("docs_cli", REPO / "scripts" / "docs-cli.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    page = cli.render()
    tops = [r["path"] for r in cli._walk() if len(r["path"].split()) == 2]
    assert len(tops) > 20
    missing = [t for t in tops if f"\n## {t}\n" not in page]
    assert not missing, missing


def test_no_committed_copy():
    assert not (REPO / "docs" / "commands" / "smd-reference.md").exists()

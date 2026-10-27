"""Every `smd check` line has an explanation page the station pages link to
(station-web's Check page links doc=<id>.md under the `checks` docs root)."""
from pathlib import Path

import pytest

from sigmond.commands.check import CHECK_META

DOCS = Path(__file__).resolve().parent.parent / "docs" / "operator" / "checks"
IDS = sorted(meta[0] for meta in CHECK_META.values())


@pytest.mark.parametrize("check_id", IDS)
def test_every_check_has_an_operator_page(check_id):
    page = DOCS / f"{check_id}.md"
    assert page.is_file(), f"missing {page}"
    text = page.read_text()
    assert "> **Audience:** operator" in text
    assert f"Check id: `{check_id}`" in text


def test_no_page_without_a_check():
    pages = {p.stem for p in DOCS.glob("*.md")} - {"README"}
    assert pages == set(IDS)


def test_readme_links_every_page():
    readme = (DOCS / "README.md").read_text()
    for check_id in IDS:
        assert f"({check_id}.md)" in readme

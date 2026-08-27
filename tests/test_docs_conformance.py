"""Contract §20 conformance rules — see docs/CLIENT-CONTRACT.md §20."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))

from sigmond.docs_conformance import check_repo  # noqa: E402


HEADER_LINE = "> **Verified against:** demo abc1234 on 2026-08-27 — docs\n"

WORKFLOW_CALLER = """name: docs-check
on: { push: { branches: [main] }, pull_request: { branches: [main] } }
jobs:
  docs:
    uses: HamSCI/sigmond/.github/workflows/docs-check.yml@main
    with: { paths: "docs README.md" }
"""


def _repo(tmp_path, *, index=True, header=True, requirements=True,
          workflow=WORKFLOW_CALLER):
    (tmp_path / "docs").mkdir()
    if index:
        body = "# demo documentation index\n\n"
        if header:
            body += HEADER_LINE
        (tmp_path / "docs" / "INDEX.md").write_text(body)
    if requirements:
        (tmp_path / "docs" / "REQUIREMENTS.md").write_text("# demo — Requirements\n")
    if workflow is not None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "docs-check.yml").write_text(workflow)
    return tmp_path


def _rules(findings):
    return sorted(f.rule for f in findings)


def test_conformant_repo_has_no_findings(tmp_path):
    assert check_repo(_repo(tmp_path)) == []


def test_missing_index_is_20_1(tmp_path):
    assert _rules(check_repo(_repo(tmp_path, index=False))) == ["20.1"]


def test_index_without_header_is_20_1(tmp_path):
    findings = check_repo(_repo(tmp_path, header=False))
    assert _rules(findings) == ["20.1"]
    assert "header" in findings[0].reason


def test_missing_requirements_is_20_2(tmp_path):
    assert _rules(check_repo(_repo(tmp_path, requirements=False))) == ["20.2"]


def test_missing_workflow_is_20_3(tmp_path):
    assert _rules(check_repo(_repo(tmp_path, workflow=None))) == ["20.3"]


def test_workflow_not_calling_the_reusable_ref_is_20_3(tmp_path):
    findings = check_repo(_repo(tmp_path, workflow="name: docs-check\njobs: {}\n"))
    assert _rules(findings) == ["20.3"]


def test_sigmond_itself_defines_the_workflow_and_conforms(tmp_path):
    """sigmond hosts the reusable workflow rather than calling it: a file
    with `workflow_call:` is the definer and is conformant."""
    definer = ("name: docs-check\non:\n  workflow_call:\n"
               "    inputs:\n      paths: { type: string }\n")
    assert check_repo(_repo(tmp_path, workflow=definer)) == []


def test_findings_carry_repo_relative_paths(tmp_path):
    findings = check_repo(_repo(tmp_path, index=False))
    assert findings[0].path == "docs/INDEX.md"


def test_all_three_rules_can_fire_together(tmp_path):
    (tmp_path / "docs").mkdir()
    assert _rules(check_repo(tmp_path)) == ["20.1", "20.2", "20.3"]

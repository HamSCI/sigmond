"""Contract §20 conformance — does a repo carry its documentation surface?

Three rules, all repo-local, all decidable from the checkout alone:

  20.1  docs/INDEX.md exists AND carries a `Verified against:` header
  20.2  docs/REQUIREMENTS.md exists
  20.3  .github/workflows/docs-check.yml exists AND calls the shared
        reusable workflow (or IS it — sigmond hosts the definition)

20.2's other half — a row in sigmond's REQUIREMENTS-INDEX.md — is NOT
checkable from inside the component's own repo, so it lives in
tests/test_docs_requirements_index.py instead.

This module is the single implementation of §20.  `scripts/docs-conformance.py`
and `smd doctor --docs` are both thin callers; that is deliberate, so a repo
cannot pass one and fail the other.

Nothing here consults topology.toml, enabled_components(), dormant_reason() or
hardware_gated: §20 binds at the catalog's *existence* layer, so a client that
is switched off or dormant behind absent hardware still conforms exactly as it
did when running.  See CLIENT-CONTRACT.md §20 and the design spec §2a.

Stdlib only, per sigmond's no-runtime-dependency rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Identical to docs-linkcheck.py's set, deliberately: a page the link checker
# will not read is not a page §20 can demand a header from.
SKIP_DIRS = {".venv", "venv", "node_modules", "graphify-out", ".git",
             "__pycache__", ".pytest_cache", "superpowers", "archive"}

INDEX_PATH = "docs/INDEX.md"
REQUIREMENTS_PATH = "docs/REQUIREMENTS.md"
WORKFLOW_PATH = ".github/workflows/docs-check.yml"
REUSABLE_REF = "HamSCI/sigmond/.github/workflows/docs-check.yml@main"
HEADER = "Verified against:"


@dataclass(frozen=True)
class Finding:
    """One §20 violation.  `path` is repo-relative so findings from
    different checkouts can be printed side by side."""
    rule: str      # "20.1" | "20.2" | "20.3"
    path: str
    reason: str


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def check_repo(root: Path) -> list[Finding]:
    """Every §20 violation in the checkout at `root`, in rule order."""
    root = Path(root)
    findings: list[Finding] = []

    index = root / INDEX_PATH
    if not index.is_file():
        findings.append(Finding("20.1", INDEX_PATH, "missing"))
    elif HEADER not in _read(index):
        findings.append(
            Finding("20.1", INDEX_PATH,
                    f"no `{HEADER}` header (see docs-conventions.md §2)"))

    if not (root / REQUIREMENTS_PATH).is_file():
        findings.append(Finding("20.2", REQUIREMENTS_PATH, "missing"))

    workflow = root / WORKFLOW_PATH
    if not workflow.is_file():
        findings.append(Finding("20.3", WORKFLOW_PATH, "missing"))
    else:
        text = _read(workflow)
        # sigmond hosts the reusable workflow instead of calling it; a file
        # declaring `workflow_call:` is the definition and conforms.
        if REUSABLE_REF not in text and "workflow_call:" not in text:
            findings.append(
                Finding("20.3", WORKFLOW_PATH,
                        f"does not call {REUSABLE_REF}"))

    return findings

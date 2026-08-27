# Docs Conformance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the documentation surface a contract obligation (§20) enforced by four checks, and bring all bound components into conformance.

**Architecture:** One rule module in `lib/sigmond/docs_conformance.py` is the single implementation; a CLI script, a CI workflow step, a pytest pair, and an `smd doctor` flag are thin callers, so no two checks can drift. Binding is decided by `etc/catalog.toml` (`lifecycle = "supported"`), never by host enablement or runtime dormancy. Repo work lands before the failing CI step is wired, because the shared workflow is consumed on `@main`.

**Tech Stack:** Python 3.11 stdlib only (sigmond's core is stdlib-by-design, see CLAUDE.md), pytest, GitHub Actions, `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-08-26-docs-conformance-design.md`

## Global Constraints

- **Stdlib only** in `lib/sigmond/` and `scripts/` — sigmond's core takes no runtime dependencies (`pyproject.toml`: `dependencies = []`).
- **Python floor 3.11** (`requires-python = ">=3.11"`).
- **Tests run as** `PYTHONPATH=lib python -m pytest tests/... -q` from the sigmond repo root.
- **Third-party GitHub Actions are pinned to full commit SHAs**, never tags, with a `# vN` comment recording the tag at pin time (existing convention in `.github/workflows/docs-check.yml`).
- **Skip set**, identical to `docs-linkcheck.py`: `.venv venv node_modules graphify-out .git __pycache__ .pytest_cache superpowers archive`.
- **Header string** is exactly `Verified against:`; only the *first* occurrence in a file is the page's header.
- **Reusable workflow ref** is exactly `HamSCI/sigmond/.github/workflows/docs-check.yml@main`.
- **Commit to `main`**; no feature branches (repo convention). Use SSH remotes for github.com.
- **No enforcement point may read `topology.toml`, `enabled_components()`, `dormant_reason()`, or `hardware_gated`.** Spec §2a.

---

### Task 1: The rule module

**Files:**
- Create: `lib/sigmond/docs_conformance.py`
- Test: `tests/test_docs_conformance.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Finding(rule: str, path: str, reason: str)` frozen dataclass; `check_repo(root: Path) -> list[Finding]`; module constants `SKIP_DIRS: set[str]`, `WORKFLOW_PATH: str`, `REUSABLE_REF: str`, `HEADER: str`.

Rule scope note for the implementer: **20.2 is checked here only as far as a repo can see it.** A repo-local check cannot verify its own row in sigmond's `REQUIREMENTS-INDEX.md`, so `check_repo` asserts only that `docs/REQUIREMENTS.md` exists. The index-row half of 20.2 belongs to Task 13 (B-lite), which reads both files from inside sigmond.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=lib python -m pytest tests/test_docs_conformance.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sigmond.docs_conformance'`

- [ ] **Step 3: Write the implementation**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=lib python -m pytest tests/test_docs_conformance.py -q`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/docs_conformance.py tests/test_docs_conformance.py
git commit -m "docs-conformance: the §20 rule module, single implementation for all four checks"
```

---

### Task 2: The CLI wrapper

**Files:**
- Create: `scripts/docs-conformance.py`
- Test: `tests/test_docs_conformance_cli.py`

**Interfaces:**
- Consumes: `sigmond.docs_conformance.check_repo`, `Finding` (Task 1).
- Produces: `main(argv=None) -> int`. Exit 0 clean / 1 on findings. One `path: [rule] reason` line per finding on stdout, a `docs-conformance: N finding(s)` summary on stderr — matching `docs-linkcheck.py`'s contract exactly, because CI logs read the two side by side.

- [ ] **Step 1: Write the failing test**

```python
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "docs-conformance.py"


def _run(root):
    return subprocess.run([sys.executable, str(SCRIPT), str(root)],
                          capture_output=True, text=True)


def _conformant(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "INDEX.md").write_text(
        "# x\n> **Verified against:** x abc1234 on 2026-08-27 — docs\n")
    (tmp_path / "docs" / "REQUIREMENTS.md").write_text("# x\n")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "docs-check.yml").write_text(
        "uses: HamSCI/sigmond/.github/workflows/docs-check.yml@main\n")
    return tmp_path


def test_clean_repo_exits_zero(tmp_path):
    r = _run(_conformant(tmp_path))
    assert r.returncode == 0
    assert "0 finding(s)" in r.stderr


def test_findings_exit_one_and_print_rule_and_path(tmp_path):
    (tmp_path / "docs").mkdir()
    r = _run(tmp_path)
    assert r.returncode == 1
    assert "docs/INDEX.md: [20.1]" in r.stdout
    assert "3 finding(s)" in r.stderr


def test_script_runs_without_sigmond_on_syspath(tmp_path):
    """CI checks out sigmond to .sigmond-tools and runs the script by path;
    it must bootstrap its own lib/ rather than needing PYTHONPATH."""
    r = subprocess.run([sys.executable, str(SCRIPT), str(_conformant(tmp_path))],
                       capture_output=True, text=True, cwd="/tmp")
    assert r.returncode == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=lib python -m pytest tests/test_docs_conformance_cli.py -q`
Expected: FAIL — `can't open file .../scripts/docs-conformance.py`

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""docs-conformance — verify a repo carries its contract §20 documentation surface.

Usage:  docs-conformance.py [REPO_ROOT ...]     (default: cwd)

Exit 0 when every bound rule passes; exit 1 and print
`path: [rule] reason` per violation otherwise.  Stdlib only.

Rules live in `lib/sigmond/docs_conformance.py`; this is only a CLI over them,
so `smd doctor --docs` and CI cannot disagree about what §20 means.

CI runs this from a sigmond checkout at `.sigmond-tools`, by path and with no
PYTHONPATH set, so the script bootstraps its own `lib/` onto sys.path.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sigmond.docs_conformance import check_repo  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="*", type=Path, default=[Path(".")],
                    help="repo checkout roots (default: cwd)")
    a = ap.parse_args(argv)

    findings = []
    for root in (a.roots or [Path(".")]):
        findings.extend(check_repo(root))

    for f in findings:
        print(f"{f.path}: [{f.rule}] {f.reason}")
    print(f"docs-conformance: {len(findings)} finding(s)", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=lib python -m pytest tests/test_docs_conformance_cli.py -q`
Expected: PASS, 3 passed

- [ ] **Step 5: Verify the checker against the real fleet — this is the baseline for Task 11**

```bash
chmod +x scripts/docs-conformance.py
for r in /home/mjh/hamsci/repos/*/; do
  printf '%-22s ' "$(basename "$r")"
  python3 scripts/docs-conformance.py "$r" 2>&1 | tail -1
done
```

Expected: sigmond and the eight other conformant repos report `0 finding(s)`; hf-timestd reports 1 (20.1 header); ka9q-python 2; hamsci-physics 3; sigmond-appliance 1 (20.2); graphify-out and ka9q-radio are not bound components — ignore them.

- [ ] **Step 6: Commit**

```bash
git add scripts/docs-conformance.py tests/test_docs_conformance_cli.py
git commit -m "docs-conformance: CLI over the rule module, matching docs-linkcheck's output contract"
```

---

### Task 3: The catalog `lifecycle` field

**Files:**
- Modify: `lib/sigmond/catalog.py` (`CatalogEntry` dataclass, `_entry_from_toml_block`, `_entry_to_block`)
- Modify: `etc/catalog.toml` (header comment documenting the field)
- Test: `tests/test_catalog_lifecycle.py`

**Interfaces:**
- Consumes: `CatalogEntry`, `load_catalog(path)` from `lib/sigmond/catalog.py`.
- Produces: `CatalogEntry.lifecycle: str`, one of `"supported"` (default) / `"experimental"` / `"retired"`; module constants `LIFECYCLE_VALUES: frozenset[str]`, `LIFECYCLE_DEFAULT: str`.

- [ ] **Step 1: Write the failing test**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))

from sigmond.catalog import (  # noqa: E402
    LIFECYCLE_DEFAULT, LIFECYCLE_VALUES, load_catalog,
)

CATALOG = """
[client.alpha]
kind = "client"
description = "no lifecycle declared"
repo = "https://github.com/HamSCI/alpha"

[client.beta]
kind = "client"
description = "being brought up"
repo = "https://github.com/HamSCI/beta"
lifecycle = "experimental"

[client.gamma]
kind = "client"
description = "gone"
repo = "https://github.com/HamSCI/gamma"
lifecycle = "retired"
"""


def _catalog(tmp_path):
    p = tmp_path / "catalog.toml"
    p.write_text(CATALOG)
    return load_catalog(p)


def test_absent_lifecycle_defaults_to_supported(tmp_path):
    """Silence must not buy an exemption: an entry added without thought
    about lifecycle is BOUND."""
    assert _catalog(tmp_path)["alpha"].lifecycle == "supported"
    assert LIFECYCLE_DEFAULT == "supported"


def test_declared_lifecycle_is_read(tmp_path):
    cat = _catalog(tmp_path)
    assert cat["beta"].lifecycle == "experimental"
    assert cat["gamma"].lifecycle == "retired"


def test_lifecycle_values_are_the_three_states():
    assert LIFECYCLE_VALUES == frozenset(
        {"supported", "experimental", "retired"})


def test_unknown_lifecycle_falls_back_to_supported(tmp_path):
    """A typo must fail safe (bound), never silently exempt."""
    p = tmp_path / "c.toml"
    p.write_text('[client.d]\nkind="client"\nrepo="r"\nlifecycle="suported"\n')
    assert load_catalog(p)["d"].lifecycle == "supported"


def test_lifecycle_survives_the_entry_to_block_round_trip(tmp_path):
    from sigmond.catalog import _entry_from_toml_block, _entry_to_block
    entry = _entry_from_toml_block("x", {"kind": "client",
                                         "lifecycle": "experimental"})
    assert _entry_to_block(entry)["lifecycle"] == "experimental"


def test_shipped_catalog_entries_are_all_loadable():
    """The real catalog must survive the new field."""
    repo_root = Path(__file__).resolve().parents[1]
    cat = load_catalog(repo_root / "etc" / "catalog.toml")
    assert all(e.lifecycle in LIFECYCLE_VALUES for e in cat.values())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=lib python -m pytest tests/test_catalog_lifecycle.py -q`
Expected: FAIL — `ImportError: cannot import name 'LIFECYCLE_DEFAULT'`

- [ ] **Step 3: Add the constants and the field**

In `lib/sigmond/catalog.py`, above the `CatalogEntry` class:

```python
# Contract §20 binds at the catalog's *existence* layer.  `lifecycle` says
# whether the suite still claims a component, independently of whether any
# host has it enabled (topology.toml) or whether it can run right now
# (harmonize.dormant_reason).  Those two layers move constantly — clients are
# stood up for an event and torn down after — and §20 must be blind to both.
#
#   supported     a real component of the suite; §20's MUSTs apply
#   experimental  defined, being brought up, docs not yet owed; checks warn
#   retired       no longer part of the suite; repo and docs stay readable,
#                 REQUIREMENTS-INDEX.md keeps its row, MUSTs drop
#
# The default is `supported` so that silence cannot buy an exemption: an entry
# added without a thought about lifecycle is bound.  An unrecognised value
# fails the same way, for the same reason.
LIFECYCLE_DEFAULT = 'supported'
LIFECYCLE_VALUES = frozenset({'supported', 'experimental', 'retired'})
```

Add to the `CatalogEntry` dataclass, after `hardware_gated`:

```python
    lifecycle: str = LIFECYCLE_DEFAULT
    """Whether the suite still claims this component — see LIFECYCLE_VALUES.

    Binds contract §20.  Never consulted for enablement or dormancy."""
```

In `_entry_from_toml_block`, add to the `CatalogEntry(...)` call:

```python
        lifecycle=(cfg.get('lifecycle')
                   if cfg.get('lifecycle') in LIFECYCLE_VALUES
                   else LIFECYCLE_DEFAULT),
```

In `_entry_to_block`, before `return block`:

```python
    if entry.lifecycle != LIFECYCLE_DEFAULT:
        block['lifecycle'] = entry.lifecycle
```

- [ ] **Step 4: Document the field in `etc/catalog.toml`**

Append to the header comment block, after the "Operator-specific pins" paragraph:

```toml
# Each entry may declare ``lifecycle``: "supported" (default), "experimental"
# or "retired".  It governs contract §20 (documentation surface) and nothing
# else -- it does NOT affect install, enablement or start order.  Omit it for
# any real component: the default is "supported", so a new entry is bound to
# §20 unless it deliberately says otherwise.  Use "experimental" for a client
# being brought up that does not yet owe docs, and "retired" to drop a
# component's obligations while keeping its REQUIREMENTS-INDEX.md row and its
# history readable.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=lib python -m pytest tests/test_catalog_lifecycle.py tests/test_catalog.py tests/test_catalog_pin.py tests/test_catalog_prune.py -q`
Expected: PASS — new tests plus the existing catalog suite still green

- [ ] **Step 6: Commit**

```bash
git add lib/sigmond/catalog.py etc/catalog.toml tests/test_catalog_lifecycle.py
git commit -m "catalog: lifecycle field (supported/experimental/retired) binding contract §20"
```

---

### Task 4: `bound_components()` — the one place binding is decided

**Files:**
- Modify: `lib/sigmond/docs_conformance.py`
- Test: `tests/test_docs_conformance.py` (append)

**Interfaces:**
- Consumes: `sigmond.catalog.load_catalog`, `LIFECYCLE_VALUES` (Task 3); `Finding`, `check_repo` (Task 1).
- Produces: `bound_components(catalog: dict[str, CatalogEntry]) -> dict[str, CatalogEntry]` — entries with `lifecycle == "supported"`, excluding those with a falsy `repo`.

- [ ] **Step 1: Write the failing test (append to `tests/test_docs_conformance.py`)**

```python
from sigmond.catalog import CatalogEntry  # noqa: E402
from sigmond.docs_conformance import bound_components  # noqa: E402


def _entry(name, **kw):
    kw.setdefault('kind', 'client')
    kw.setdefault('description', '')
    kw.setdefault('repo', f'https://github.com/HamSCI/{name}')
    return CatalogEntry(name=name, **kw)


def test_bound_components_keeps_supported_only():
    cat = {
        'a': _entry('a'),
        'b': _entry('b', lifecycle='experimental'),
        'c': _entry('c', lifecycle='retired'),
    }
    assert set(bound_components(cat)) == {'a'}


def test_bound_components_drops_entries_with_no_repo():
    """A component with no repo URL has no checkout to check."""
    cat = {'a': _entry('a'), 'n': _entry('n', repo='')}
    assert set(bound_components(cat)) == {'a'}


def test_bound_components_does_not_consult_enablement_or_dormancy():
    """§20 binds at the existence layer. A CatalogEntry carries
    hardware_gated; a gated component is still bound."""
    cat = {'m': _entry('m', hardware_gated='magnetometer (RM3100)')}
    assert set(bound_components(cat)) == {'m'}


def test_module_never_imports_the_operational_layers():
    """Structural guard on spec §2a's prohibition. Data-level tests can
    only prove the layers are not consulted for the inputs they happen to
    try; this proves the module cannot reach them at all. If this fails,
    someone made a docs check depend on whether a client is switched on --
    which would fail a repo for being deactivated between events."""
    import sigmond.docs_conformance as mod
    src = Path(mod.__file__).read_text()
    for forbidden in ('topology', 'enabled_components',
                      'dormant_reason', 'harmonize'):
        assert forbidden not in src, (
            f'docs_conformance must not reference {forbidden!r}: '
            f'§20 binds at the catalog existence layer only')
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=lib python -m pytest tests/test_docs_conformance.py -q`
Expected: FAIL — `ImportError: cannot import name 'bound_components'`

- [ ] **Step 3: Implement (append to `lib/sigmond/docs_conformance.py`)**

```python
def bound_components(catalog: dict) -> dict:
    """The subset of `catalog` that contract §20 binds.

    Binding is `lifecycle == "supported"` and nothing else.  In particular
    this function must never grow a check on topology enablement or runtime
    dormancy: a client switched off between events, or gated behind hardware
    that is not plugged in, owes exactly the same docs it owed while running.
    Making the check depend on operational state would punish the ordinary
    activate/deactivate cycle this suite is built around.

    Entries with no `repo` URL are dropped: there is no checkout to check.
    """
    from .catalog import LIFECYCLE_DEFAULT
    return {
        name: entry
        for name, entry in catalog.items()
        if entry.lifecycle == LIFECYCLE_DEFAULT and entry.repo
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=lib python -m pytest tests/test_docs_conformance.py -q`
Expected: PASS, 13 passed

- [ ] **Step 5: Commit**

```bash
git add lib/sigmond/docs_conformance.py tests/test_docs_conformance.py
git commit -m "docs-conformance: bound_components() — binding is lifecycle, never enablement"
```

---

### Task 5: Contract §20 and the index Lifecycle column

**Files:**
- Modify: `docs/CLIENT-CONTRACT.md` (version header, v0.9 changelog entry, new §20 before "What sigmond promises in return", Migration entry)
- Modify: `docs/REQUIREMENTS-INDEX.md` (Lifecycle column, two new rows)

**Interfaces:**
- Consumes: nothing (documentation).
- Produces: §20's rule numbering `20.0`–`20.4`, referenced by `Finding.rule` values from Task 1.

- [ ] **Step 1: Bump the contract version header**

In `docs/CLIENT-CONTRACT.md`, change `**Version:** 0.8` to `**Version:** 0.9`, and add above the `v0.8 adds:` block:

```markdown
v0.9 adds:

- **§20 (new) — documentation surface.**  A component's docs are part of
  what it owes the suite, not a courtesy.  Requires `docs/INDEX.md` with a
  `Verified against:` header, `docs/REQUIREMENTS.md` plus a row in
  [REQUIREMENTS-INDEX.md](REQUIREMENTS-INDEX.md), and a
  `.github/workflows/docs-check.yml` calling the shared reusable workflow.
  §20 is the one section that binds every component in the requirements
  index — libraries and infra included — because a docs surface is a
  property of the repo, not of the running sigmond↔client seam.  It binds
  at the catalog's *existence* layer and is blind to host enablement and
  runtime dormancy, so activating, deactivating or reconfiguring a client
  can never trip it.  §20 imposes no runtime obligation and therefore
  requires **no `contract_version` bump** in any client's `deploy.toml`.
```

- [ ] **Step 2: Write §20**

Insert immediately before `## What sigmond promises in return`:

```markdown
### 20. Documentation surface (v0.9)

**20.0 Scope.**  §20 binds every component listed in
[REQUIREMENTS-INDEX.md](REQUIREMENTS-INDEX.md) whose `etc/catalog.toml` entry
has `lifecycle = "supported"` (the default).  Clients, libraries and infra
alike: a docs surface is a property of the repo, not of the sigmond↔client
seam, so this is the one section reaching beyond contract-conformant clients.
Vendored upstreams (`ka9q-radio`, `ka9q-web`, `onion`, `ft8_lib`) are out of
scope, as the requirements index already states.

`lifecycle = "experimental"` exempts a component that is being brought up and
does not yet owe docs; checks warn rather than fail.  `lifecycle = "retired"`
drops the obligations while keeping the repo, its docs and its index row
readable.  Promotion from `experimental` to `supported` is the moment the
documentation debt comes due.

**§20 does not restate the standard.**  `docs/contributor/docs-conventions.md`
is normative for header blocks, ★-canonical marking and archive policy; §20
names what is *required* and points there for what it must look like — the
same division this contract already uses with
[REQUIREMENTS-TEMPLATE.md](REQUIREMENTS-TEMPLATE.md).

**20.1 (MUST) `docs/INDEX.md`.**  Every bound repo keeps a documentation index
carrying a `Verified against:` header, per docs-conventions.md §2.

A repo whose only doc page is `REQUIREMENTS.md` still owes an `INDEX.md`, and
it will be a one-row table pointing at that single sibling.  **That stub is
correct by design, not an oversight awaiting cleanup.**  The alternative — an
exemption for repos judged too small to bother — is the exact mechanism that
left `ka9q-python`, `hamsci-physics` and seven other components undocumented
while the index listed them as documented.

**20.2 (MUST) `docs/REQUIREMENTS.md`.**  Filled from
[REQUIREMENTS-TEMPLATE.md](REQUIREMENTS-TEMPLATE.md), with a row in
[REQUIREMENTS-INDEX.md](REQUIREMENTS-INDEX.md) naming the component's prefix,
kind, maturity and lifecycle.

**20.3 (MUST) `.github/workflows/docs-check.yml`.**  Calling
`HamSCI/sigmond/.github/workflows/docs-check.yml@main`.  Sigmond itself hosts
the reusable definition rather than calling it, and conforms by doing so.

**20.4 (SHOULD) `Verified against:` on every other live page.**  Enforced
warn-only by `docs-freshness.py`, unchanged.  The MUST/SHOULD split is the
severity the subject matter carries: an *absent* canonical page is a fact, a
*stale* header is a judgement call.

A configuration change made for a special event will stale a page's header.
That is correctly a freshness **warning** and never a §20 failure — §20 must
not become friction on ad-hoc operational work.

**20.5 Conformance.**  Four checks, each catching a failure the others
structurally cannot, all sharing one implementation in
`lib/sigmond/docs_conformance.py`:

| Check | Where | Catches |
|---|---|---|
| `docs-conformance.py` in the shared workflow | the component's own CI | 20.1–20.3, at merge time |
| `tests/test_docs_requirements_index.py` | sigmond CI | catalog↔index disagreement, both directions |
| workflow-presence check | sigmond CI | 20.3 in a repo that never added the workflow |
| `smd doctor --docs` | a live host | 20.1–20.3 across installed checkouts, no network |

A self-check cannot detect its own absence: a repo with no `docs-check.yml`
passes every checker it does not run.  That is why binding is decided from the
catalog roster rather than from the repo.
```

- [ ] **Step 3: Add the Migration entry**

Under `## Migration and versioning`, after the v0.2→v0.3 bullet:

```markdown
- **v0.8 → v0.9** adds §20 (documentation surface).  §20 is a repo-level
  obligation with no runtime component: no client changes code, no
  `deploy.toml` declares `contract_version = "0.9"` on its account, and
  sigmond's version-skew warning ignores §20 entirely.  `contract_version`
  tracks the runtime seam only.  A v0.8 client is fully v0.9-conformant the
  moment its repo carries the docs surface.
```

- [ ] **Step 4: Add the Lifecycle column to the requirements index**

In `docs/REQUIREMENTS-INDEX.md`, change the Components table header to:

```markdown
| Component | Prefix | Kind | Maturity | Lifecycle | Requirements doc |
|---|---|---|---|---|---|
```

Add `| Supported ` to every existing row before its requirements-doc cell, and add these two rows:

```markdown
| **hamsci-physics** | `PHY` | client | Active | Supported | [hamsci-physics/docs/REQUIREMENTS.md](https://github.com/HamSCI/hamsci-physics/blob/main/docs/REQUIREMENTS.md) ✅ |
| **sigmond-appliance** | `APP` | appliance/image | Active | Supported | [sigmond-appliance/docs/REQUIREMENTS.md](https://github.com/HamSCI/sigmond-appliance/blob/main/docs/REQUIREMENTS.md) ✅ |
```

Add below the table:

```markdown
> **Maturity vs Lifecycle.** Maturity says how complete the docs are;
> lifecycle says whether the suite still claims the component. They move
> independently — a `Mature`/`Retired` row is a well-documented component we no
> longer ship. Lifecycle mirrors `etc/catalog.toml`, which is authoritative;
> this column is a rendered view of it. Contract §20 binds the `Supported`
> rows.
```

- [ ] **Step 5: Verify links and freshness still pass**

Run: `python3 scripts/docs-linkcheck.py docs README.md`
Expected: `docs-linkcheck: 0 broken link(s)`

Run: `PYTHONPATH=lib python -m pytest tests/test_docs_links.py tests/test_docs_cli_table.py tests/test_docs_freshness.py -q`
Expected: PASS

- [ ] **Step 6: Bump the `Verified against:` headers you touched**

Both edited files carry a header. Set each to the current `git rev-parse --short HEAD` and today's date, `— docs`.

- [ ] **Step 7: Commit**

```bash
git add docs/CLIENT-CONTRACT.md docs/REQUIREMENTS-INDEX.md
git commit -m "contract: §20 documentation surface (v0.9), bound by catalog lifecycle"
```

---

### Task 6: hf-timestd — the missing INDEX header

**Files:**
- Modify: `/home/mjh/hamsci/repos/hf-timestd/docs/INDEX.md`

**Interfaces:**
- Consumes: `scripts/docs-conformance.py` (Task 2).
- Produces: nothing consumed downstream. This is the smallest repo fix; do it first to prove the edit-verify-push loop before the eight larger ones.

- [ ] **Step 1: Confirm the finding**

```bash
cd /home/mjh/hamsci/repos/sigmond
python3 scripts/docs-conformance.py /home/mjh/hamsci/repos/hf-timestd
```
Expected: `docs/INDEX.md: [20.1] no \`Verified against:\` header ...` and `1 finding(s)`

- [ ] **Step 2: Add the header**

Insert after the H1 of `/home/mjh/hamsci/repos/hf-timestd/docs/INDEX.md`, matching `hamsci-dsp/docs/INDEX.md`'s block exactly:

```markdown
> **Audience:** all
> **Status:** current
> **Verified against:** hf-timestd <short-sha> on 2026-08-27 — docs
> **Canonical for:** the map of this repo's docs
```

Get `<short-sha>` with `git -C /home/mjh/hamsci/repos/hf-timestd rev-parse --short HEAD`.

- [ ] **Step 3: Verify**

```bash
python3 scripts/docs-conformance.py /home/mjh/hamsci/repos/hf-timestd
```
Expected: `docs-conformance: 0 finding(s)`

- [ ] **Step 4: Commit and push**

```bash
cd /home/mjh/hamsci/repos/hf-timestd
git add docs/INDEX.md
git commit -m "docs: INDEX.md gains its Verified-against header (contract §20.1)"
git push origin main
```

---

### Task 7: ka9q-python — INDEX.md and the workflow

**Files:**
- Create: `/home/mjh/hamsci/repos/ka9q-python/docs/INDEX.md`
- Create: `/home/mjh/hamsci/repos/ka9q-python/.github/workflows/docs-check.yml`

**Interfaces:**
- Consumes: `scripts/docs-conformance.py` (Task 2).
- Produces: the INDEX.md shape reused verbatim by Tasks 8 and 9.

ka9q-python has 12 doc pages plus a README — the only substantial mapping job among the nine. `docs/superpowers/` and `docs/audit/` exist; superpowers is checker-exempt, audit is not, but §20 asks nothing of individual pages.

- [ ] **Step 1: Write `docs/INDEX.md`**

```markdown
# ka9q-python documentation index

> **Audience:** all
> **Status:** current
> **Verified against:** ka9q-python <short-sha> on 2026-08-27 — docs
> **Canonical for:** the map of this repo's docs

★ = canonical; when two docs disagree the ★ one wins. Suite-wide front door:
[HamSCI/sigmond docs](https://github.com/HamSCI/sigmond/blob/main/docs/README.md).

| Doc | Audience | What it gives you |
|-----|----------|-------------------|
| [../README.md](../README.md) ★ | all | what this library is: the clients↔radiod interface |
| [GETTING_STARTED.md](GETTING_STARTED.md) ★ | user | first channel, first stream, in order |
| [INSTALLATION.md](INSTALLATION.md) ★ | user | install and verify against a running radiod |
| [API_REFERENCE.md](API_REFERENCE.md) ★ | contributor | every public call, parameter and return type |
| [ARCHITECTURE.md](ARCHITECTURE.md) ★ | contributor | how control, status and data planes fit together |
| [RECIPES.md](RECIPES.md) | user | worked examples for common tasks |
| [MULTI_STREAM.md](MULTI_STREAM.md) ★ | user | running many channels at once |
| [RTP_TIMING_SUPPORT.md](RTP_TIMING_SUPPORT.md) ★ | contributor | RTP timestamps and what they can and cannot anchor |
| [CLI_GUIDE.md](CLI_GUIDE.md) ★ | user | the command-line tools |
| [TUI_GUIDE.md](TUI_GUIDE.md) ★ | user | the terminal UI |
| [TESTING_GUIDE.md](TESTING_GUIDE.md) ★ | contributor | how to run and extend the suite |
| [SECURITY.md](SECURITY.md) ★ | all | reporting and scope |
| [REQUIREMENTS.md](REQUIREMENTS.md) ★ | contributor | formal requirements, reconciled to code |
```

Before writing it, read each page's H1 and opening paragraph and correct any "what it gives you" cell that misdescribes it — a wrong index is worse than none.

- [ ] **Step 2: Create the workflow**

```yaml
name: docs-check
on: { push: { branches: [main] }, pull_request: { branches: [main] } }
jobs:
  docs:
    uses: HamSCI/sigmond/.github/workflows/docs-check.yml@main
    with: { paths: "docs README.md" }
```

- [ ] **Step 3: Verify**

```bash
cd /home/mjh/hamsci/repos/sigmond
python3 scripts/docs-conformance.py /home/mjh/hamsci/repos/ka9q-python
python3 scripts/docs-linkcheck.py --workspace /home/mjh/hamsci/repos \
  /home/mjh/hamsci/repos/ka9q-python/docs /home/mjh/hamsci/repos/ka9q-python/README.md
```
Expected: `0 finding(s)` and `0 broken link(s)`

- [ ] **Step 4: Commit and push**

```bash
cd /home/mjh/hamsci/repos/ka9q-python
git add docs/INDEX.md .github/workflows/docs-check.yml
git commit -m "docs: INDEX.md + docs-check CI (contract §20.1, §20.3)"
git push origin main
```

---

### Task 8: The seven surveyed repos — INDEX.md and workflow each

**Files:**
- Create, in each of `superdarn-sounder`, `codar-sounder`, `hfdl-recorder`, `hf-tec`, `callhash`, `igmp-querier`, `sigmond-rac`: `docs/INDEX.md` and `.github/workflows/docs-check.yml`

**Interfaces:**
- Consumes: the INDEX.md shape from Task 7; `scripts/docs-conformance.py` (Task 2).
- Produces: nothing consumed downstream.

None of these has a checkout in `repos/`. Clone to the scratchpad — `CLAUDE.md` warns against re-rooting the graphify extraction on a changed `repos/`, and these are one-touch doc edits.

Page inventories, already surveyed — do not re-derive:

| Repo | Doc pages besides REQUIREMENTS.md |
|---|---|
| superdarn-sounder | `OBSERVING.md`, `RADAR-EXPANSION.md` |
| hf-tec | `OVERVIEW.md`, `RECEIVER.md` |
| codar-sounder | `METHODOLOGY.md` |
| hfdl-recorder | `HFDL.md` |
| callhash | *(none — REQUIREMENTS.md only)* |
| igmp-querier | *(none — REQUIREMENTS.md only)* |
| sigmond-rac | *(none — REQUIREMENTS.md only)* |

- [ ] **Step 1: Clone all seven**

```bash
WORK=/tmp/claude-1000/-home-mjh-hamsci/29dca670-4cdf-4b60-8861-a04d1165686e/scratchpad/repos
mkdir -p "$WORK"
for r in superdarn-sounder codar-sounder hfdl-recorder hf-tec callhash igmp-querier sigmond-rac; do
  git clone git@github.com:HamSCI/$r.git "$WORK/$r"
done
```

- [ ] **Step 2: Confirm every one reports exactly the two expected findings**

```bash
cd /home/mjh/hamsci/repos/sigmond
for r in "$WORK"/*/; do
  printf '%-22s ' "$(basename "$r")"
  python3 scripts/docs-conformance.py "$r" 2>&1 | tail -1
done
```
Expected: `2 finding(s)` for each — 20.1 and 20.3. Any repo reporting 20.2 has lost its `REQUIREMENTS.md` since the survey; stop and report rather than writing one.

- [ ] **Step 3: Write each `docs/INDEX.md`**

For the four with extra pages, follow Task 7's shape. For the three single-page repos, the whole file is:

```markdown
# callhash documentation index

> **Audience:** all
> **Status:** current
> **Verified against:** callhash <short-sha> on 2026-08-27 — docs
> **Canonical for:** the map of this repo's docs

★ = canonical; when two docs disagree the ★ one wins. Suite-wide front door:
[HamSCI/sigmond docs](https://github.com/HamSCI/sigmond/blob/main/docs/README.md).

This repo has a single documentation page. That is expected, not an omission:
contract §20.1 asks every component for an index so the suite has one shape,
and a one-page repo's index is one row.

| Doc | Audience | What it gives you |
|-----|----------|-------------------|
| [../README.md](../README.md) ★ | all | what this component is and how to use it |
| [REQUIREMENTS.md](REQUIREMENTS.md) ★ | contributor | formal requirements, reconciled to code |
```

Substitute the repo name in the H1 and the header. Verify each repo actually has a root `README.md` before linking to it; drop that row if it does not.

- [ ] **Step 4: Write each workflow** — the identical 5 lines from Task 7 Step 2.

- [ ] **Step 5: Verify all seven are clean**

```bash
cd /home/mjh/hamsci/repos/sigmond
for r in "$WORK"/*/; do
  printf '%-22s ' "$(basename "$r")"
  python3 scripts/docs-conformance.py "$r" 2>&1 | tail -1
  python3 scripts/docs-linkcheck.py "$r/docs" 2>&1 | tail -1
done
```
Expected: `0 finding(s)` and `0 broken link(s)` for all seven.

- [ ] **Step 6: Commit and push each**

```bash
for r in "$WORK"/*/; do
  git -C "$r" add docs/INDEX.md .github/workflows/docs-check.yml
  git -C "$r" commit -m "docs: INDEX.md + docs-check CI (contract §20.1, §20.3)"
  git -C "$r" push origin main
done
```

---

### Task 9: hamsci-physics — the full surface

**Files:**
- Create: `/home/mjh/hamsci/repos/hamsci-physics/docs/INDEX.md`
- Create: `/home/mjh/hamsci/repos/hamsci-physics/docs/REQUIREMENTS.md`
- Create: `/home/mjh/hamsci/repos/hamsci-physics/.github/workflows/docs-check.yml`
- Modify: `/home/mjh/hamsci/repos/hamsci-physics/docs/DRF_UPLOAD_SYSTEM.md`

**Interfaces:**
- Consumes: `docs/REQUIREMENTS-TEMPLATE.md` (sigmond); the INDEX.md shape from Task 7.
- Produces: requirement IDs prefixed `PHY-`, referenced by the index row added in Task 5.

Remote is `git@github.com:mijahauan/hamsci-physics.git` and a transfer to `HamSCI/` is in hand. Push to whichever remote resolves; GitHub redirects after the move. Do not change the remote URL, and do not touch `catalog.toml`'s "staging: transfers to HamSCI/" comment — that is the transfer's own cleanup.

- [ ] **Step 1: Write `docs/REQUIREMENTS.md`**

Copy the template body from `sigmond/docs/REQUIREMENTS-TEMPLATE.md` (everything below "copy everything below into the new doc") and fill all 13 sections. Prefix `PHY`, kind `client`, maturity `Active`.

Derive §8 (external interfaces) from facts already in the repo rather than inventing them — `deploy.toml` is unusually explicit and its comments record *why*:

- **Inputs:** reads hf-timestd's data products under `/var/lib/timestd`. The data root not moving is a frozen contract of the 2026-08-24 split; the two must be co-installed.
- **Outputs:** `/var/lib/timestd/phase2/fusion` and `/var/lib/timestd/phase2/science`; GRAPE/PSWS daily datasets under `/var/lib/timestd/upload` matching `OBS*`.
- **Units:** `hamsci-physics-fusion.service`, `grape-daily.timer`, `hamsci-physics-reanalysis.timer`, `hamsci-physics-ionex-download.timer`. `grape-daily.timer` keeps its pre-split name deliberately.
- **Upload path:** the `[[hs_uploader.pipeline]]` block `grape-psws`, which must exist in exactly one repo's `deploy.toml` and lives here. `name` and `source_id` are pinned watermark keys — changing them re-ships every delivered dataset.
- **Config:** `/etc/hamsci-physics/config.toml`; §14 entry points `scripts/setup-station.sh` and `scripts/config-review.sh`.
- **Runs as:** user and group `timestd`.
- **Contract:** declares `contract_version = "0.8"`; §20 does not change that (Task 5, Migration).

Record in §12 (risks) that `grape upload` was removed in favour of hs-uploader as the sole outbound path on 2026-08-26, and that the retired `grape-upload-retry.timer` units cannot resurrect a second uploader.

- [ ] **Step 2: Write `docs/INDEX.md`** — Task 7's shape, five rows: `../README.md`, `GRAPE_DAILY_PROCESSING.md`, `PSWS_SETUP_GUIDE.md`, `NASA_EARTHDATA_SETUP.md`, `DRF_UPLOAD_SYSTEM.md`, plus `REQUIREMENTS.md`.

- [ ] **Step 3: Resolve the `DRF_UPLOAD_SYSTEM.md` stub**

It is 5 lines. Per docs-conventions.md §2, either fill it or make it an explicit pointer file. Read it first: if it describes the upload path that moved to hs-uploader, a pointer to `GRAPE_DAILY_PROCESSING.md` and hs-uploader's docs is the honest fix. Leave it un-starred in the index either way — a pointer owns no topic.

- [ ] **Step 4: Create the workflow** — the identical 5 lines from Task 7 Step 2.

- [ ] **Step 5: Verify**

```bash
cd /home/mjh/hamsci/repos/sigmond
python3 scripts/docs-conformance.py /home/mjh/hamsci/repos/hamsci-physics
python3 scripts/docs-linkcheck.py --workspace /home/mjh/hamsci/repos \
  /home/mjh/hamsci/repos/hamsci-physics/docs /home/mjh/hamsci/repos/hamsci-physics/README.md
```
Expected: `0 finding(s)` and `0 broken link(s)`

- [ ] **Step 6: Commit and push**

```bash
cd /home/mjh/hamsci/repos/hamsci-physics
git add docs/INDEX.md docs/REQUIREMENTS.md docs/DRF_UPLOAD_SYSTEM.md .github/workflows/docs-check.yml
git commit -m "docs: full §20 surface — INDEX.md, REQUIREMENTS.md (PHY), docs-check CI"
git push origin main
```

---

### Task 10: sigmond-appliance — REQUIREMENTS.md

**Files:**
- Create: `/home/mjh/hamsci/repos/sigmond-appliance/docs/REQUIREMENTS.md`

**Interfaces:**
- Consumes: `docs/REQUIREMENTS-TEMPLATE.md` (sigmond).
- Produces: requirement IDs prefixed `APP-`, referenced by the index row added in Task 5.

This repo already has `docs-check.yml` and a headered `docs/INDEX.md`; only 20.2 is open. It is the one genuinely novel authoring task in the plan: the template's §8 assumes a client with a `deploy.toml` and an `inventory --json`, and an appliance/image repo has neither.

- [ ] **Step 1: Read what the repo actually is**

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
cat README.md docs/INDEX.md
ls
```

Do not start writing until you can state in one sentence what the repo produces and what consumes it.

- [ ] **Step 2: Write `docs/REQUIREMENTS.md`**

Fill all 13 template sections, prefix `APP`. Adapt §8 rather than deriving it: for an image-building repo the external interfaces are the **produced artifact** (image name, format, size, target hardware), the **inputs** it builds from (base OS, package sources, the sigmond checkout and its pins), and the **install-time contract** with the operator (USB burn procedure, first-boot wizard, what is configured before first boot versus after). Anchor these to what the repo's own docs say; where a requirement is inferred from the build scripts rather than documented, tag it `[CODE]` per the template's provenance convention.

Relevant facts already established, with their sources — cite, do not re-derive:
- Images ship RAW `.img` only from v3.24 onward; `.xz` produced unbootable Pi-Imager sticks and saved 9%.
- Images are built small (~8G) and `growpart` re-expands on first boot.
- `/etc/sigmond-appliance/version` is install-time only and does not track later updates.

- [ ] **Step 3: Add the header block and verify**

```bash
cd /home/mjh/hamsci/repos/sigmond
python3 scripts/docs-conformance.py /home/mjh/hamsci/repos/sigmond-appliance
python3 scripts/docs-linkcheck.py --workspace /home/mjh/hamsci/repos \
  /home/mjh/hamsci/repos/sigmond-appliance/docs /home/mjh/hamsci/repos/sigmond-appliance/README.md
```
Expected: `0 finding(s)` and `0 broken link(s)`

- [ ] **Step 4: Add the new page to the repo's INDEX.md** — a `REQUIREMENTS.md` row, and bump that file's `Verified against:` header.

- [ ] **Step 5: Commit and push**

```bash
cd /home/mjh/hamsci/repos/sigmond-appliance
git add docs/REQUIREMENTS.md docs/INDEX.md
git commit -m "docs: REQUIREMENTS.md (APP) — contract §20.2"
git push origin main
```

---

### Task 11: The green gate

**Files:** none — this task changes nothing. It is the gate Task 12 depends on.

**Interfaces:**
- Consumes: `scripts/docs-conformance.py` (Task 2), `bound_components()` (Task 4), every repo fix (Tasks 6–10).
- Produces: the confirmed-green precondition for wiring the failing CI step.

- [ ] **Step 1: Check every bound component**

```bash
cd /home/mjh/hamsci/repos/sigmond
WORK=/tmp/claude-1000/-home-mjh-hamsci/29dca670-4cdf-4b60-8861-a04d1165686e/scratchpad/repos
for r in /home/mjh/hamsci/repos/*/ "$WORK"/*/; do
  n=$(basename "$r")
  case "$n" in graphify-out|ka9q-radio) continue;; esac
  printf '%-22s ' "$n"
  python3 scripts/docs-conformance.py "$r" 2>&1 | tail -1
done
```

Expected: `0 finding(s)` for all 19. **Do not proceed to Task 12 on any other result** — a single non-zero line means merging Task 12 reds that repo's CI immediately, with no staging and no per-repo pin to hide behind.

- [ ] **Step 2: Confirm every bound component was actually covered**

```bash
PYTHONPATH=lib python3 -c "
from sigmond.catalog import load_catalog
from sigmond.docs_conformance import bound_components
from pathlib import Path
cat = load_catalog(Path('etc/catalog.toml'))
for n in sorted(bound_components(cat)):
    print(n)
"
```

Cross-check against Step 1's list. A bound component that Step 1 never visited is an unchecked repo, not a pass — clone and check it before continuing.

---

### Task 12: Wire the failing step into the shared workflow

**Files:**
- Modify: `.github/workflows/docs-check.yml`

**Interfaces:**
- Consumes: `scripts/docs-conformance.py` (Task 2), the green gate (Task 11).
- Produces: live §20 enforcement in all 19 repos.

**This is the irreversible step.** Everything before it was additive; this one runs in every consuming repo on the next push. Task 11 must be green first.

- [ ] **Step 1: Add the step**

In `.github/workflows/docs-check.yml`, after the link-check step and before the freshness step:

```yaml
      - name: Docs conformance (contract §20 — INDEX, REQUIREMENTS, CI)
        run: python3 .sigmond-tools/scripts/docs-conformance.py .
```

It takes no `paths` input: §20 is about a fixed set of repo-root files, not about which pages get link-checked. Placing it before the warn-only freshness step keeps the two hard failures adjacent in the log.

- [ ] **Step 2: Add the sigmond-only pytest**

Extend the existing `sigmond-only doc tests` step's pytest invocation with `tests/test_docs_conformance.py tests/test_docs_conformance_cli.py`.

- [ ] **Step 3: Verify the workflow parses**

```bash
python3 -c "import sys,tomllib" 2>/dev/null; \
python3 - <<'EOF'
import re, pathlib
t = pathlib.Path('.github/workflows/docs-check.yml').read_text()
assert 'docs-conformance.py' in t
assert 'workflow_call:' in t
print('ok')
EOF
```

Then confirm sigmond passes its own new step:

```bash
python3 scripts/docs-conformance.py .
```
Expected: `0 finding(s)` — sigmond conforms by *defining* the reusable workflow (Task 1's `workflow_call:` branch).

- [ ] **Step 4: Commit and push**

```bash
git add .github/workflows/docs-check.yml
git commit -m "docs-check: enforce contract §20 — conformance is now a merge gate"
git push origin main
```

- [ ] **Step 5: Watch the fan-out**

```bash
sleep 90
for r in ka9q-python hamsci-physics superdarn-sounder codar-sounder hfdl-recorder \
         hf-tec callhash igmp-querier sigmond-rac sigmond hf-timestd; do
  printf '%-22s ' "$r"
  gh run list --repo HamSCI/$r --workflow docs-check.yml --limit 1 \
    --json conclusion,status --jq '.[0] | "\(.status) \(.conclusion // "-")"' 2>&1
done
```

Any failure here is a real §20 violation the local checker missed — investigate before doing anything else. `hamsci-physics` may need `--repo mijahauan/hamsci-physics` until the transfer lands.

---

### Task 13: B-lite — catalog ↔ requirements index, both directions

**Files:**
- Create: `tests/test_docs_requirements_index.py`

**Interfaces:**
- Consumes: `sigmond.catalog.load_catalog` (Task 3); `bound_components` (Task 4).
- Produces: nothing consumed downstream.

- [ ] **Step 1: Write the failing test**

```python
"""Contract §20.2's other half: the catalog and the requirements index must
agree, in BOTH directions.

A bound component with no index row is the gap that let hamsci-physics ship
undocumented. An index row with no catalog entry is the same lie told the
other way round -- the index claiming a component the suite no longer has.
`lifecycle = "retired"` is the graceful exit: the row stays, the MUSTs drop.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))

from sigmond.catalog import load_catalog  # noqa: E402
from sigmond.docs_conformance import bound_components  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / 'docs' / 'REQUIREMENTS-INDEX.md'
CATALOG = REPO / 'etc' / 'catalog.toml'

# Rows look like: | **name** | `PFX` | kind | Maturity | Lifecycle | [link] |
ROW = re.compile(r'^\|\s*\*\*([a-z0-9-]+)\*\*\s*\|')


def index_rows() -> set[str]:
    return {m.group(1)
            for line in INDEX.read_text().splitlines()
            if (m := ROW.match(line))}


def test_every_bound_component_has_an_index_row():
    catalog = load_catalog(CATALOG)
    missing = sorted(set(bound_components(catalog)) - index_rows())
    assert not missing, (
        f"bound components with no REQUIREMENTS-INDEX.md row: {missing}. "
        f"Add a row, or set lifecycle = 'experimental' in etc/catalog.toml "
        f"if the component does not yet owe docs.")


def test_every_index_row_maps_to_a_catalog_entry():
    catalog = load_catalog(CATALOG)
    orphans = sorted(index_rows() - set(catalog))
    assert not orphans, (
        f"REQUIREMENTS-INDEX.md rows with no catalog entry: {orphans}. "
        f"Set lifecycle = 'retired' rather than deleting the entry, so the "
        f"row and its history stay readable.")


def test_index_rows_are_parseable():
    """Guard the regex: if the table format changes, the two tests above
    would silently pass on an empty set."""
    assert len(index_rows()) >= 17
```

- [ ] **Step 2: Run the test to verify it fails, then passes**

Run: `PYTHONPATH=lib python -m pytest tests/test_docs_requirements_index.py -q`

Expected before Task 5's index edits: FAIL on `hamsci-physics` and `sigmond-appliance`. After Task 5: PASS. If it fails on any *other* name, that is a real gap this check exists to find — fix the index rather than the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_docs_requirements_index.py
git commit -m "docs-conformance: catalog↔requirements-index agreement, both directions"
```

---

### Task 14: B-full — workflow presence via the GitHub API

**Files:**
- Create: `tests/test_docs_check_workflows.py`

**Interfaces:**
- Consumes: `sigmond.catalog.load_catalog` (Task 3), `bound_components` (Task 4), `WORKFLOW_PATH` (Task 1).
- Produces: nothing consumed downstream.

This is the only check that catches a repo which never added the workflow — the failure mode a self-check structurally cannot see. It must **skip, never fail**, on any network or rate-limit problem: a GitHub outage reddening an unrelated build would get the check disabled within a week.

- [ ] **Step 1: Write the test**

```python
"""Contract §20.3 across the fleet: does each bound repo actually have
.github/workflows/docs-check.yml?

This is the one check that sees a repo which never added the workflow. A
self-check cannot detect its own absence -- ka9q-python and hamsci-physics
passed every checker they ran for months by running none.

Skips rather than fails on any network or rate-limit problem. A check that
reds the build during a GitHub outage gets deleted, and then nothing watches
this at all.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))

from sigmond.catalog import load_catalog  # noqa: E402
from sigmond.docs_conformance import WORKFLOW_PATH, bound_components  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / 'etc' / 'catalog.toml'
API = 'https://api.github.com/repos/{slug}/contents/{path}'

# Upstreams we vendor but do not own; §20 is explicitly out of scope for them.
UPSTREAM_OWNERS = {'ka9q'}


def _slug(repo_url: str):
    """owner/name from a github.com URL, or None if not on github.com."""
    if 'github.com/' not in repo_url:
        return None
    tail = repo_url.split('github.com/', 1)[1].removesuffix('.git').strip('/')
    parts = tail.split('/')
    if len(parts) != 2 or parts[0] in UPSTREAM_OWNERS:
        return None
    return f'{parts[0]}/{parts[1]}'


def _has_path(slug: str, path: str):
    """True/False, or None when the answer is unavailable (skip, don't fail)."""
    req = urllib.request.Request(API.format(slug=slug, path=path))
    req.add_header('Accept', 'application/vnd.github+json')
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            json.load(r)
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        return None          # 403 rate-limit, 401, 5xx -> unavailable
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _bound_slugs():
    catalog = load_catalog(CATALOG)
    out = {}
    for name, entry in bound_components(catalog).items():
        slug = _slug(entry.repo)
        if slug:
            out[name] = slug
    return out


@pytest.mark.network
def test_every_bound_repo_has_the_docs_check_workflow():
    slugs = _bound_slugs()
    if not slugs:
        pytest.skip('no bound components with github.com repos')

    missing, unavailable = [], []
    for name, slug in sorted(slugs.items()):
        got = _has_path(slug, WORKFLOW_PATH)
        if got is None:
            unavailable.append(name)
        elif not got:
            missing.append(f'{name} ({slug})')

    if unavailable and not missing:
        pytest.skip(f'GitHub API unavailable for: {", ".join(unavailable)}')

    assert not missing, (
        f'bound repos with no {WORKFLOW_PATH}: {missing}. '
        f'Add the 5-line reusable-workflow call, or set '
        f'lifecycle = "experimental" in etc/catalog.toml.')
```

- [ ] **Step 2: Register the marker**

Add to `pyproject.toml` under `[tool.pytest.ini_options]` (create the table if absent):

```toml
[tool.pytest.ini_options]
markers = [
    "network: needs outbound HTTPS; skips cleanly when unavailable",
]
```

- [ ] **Step 3: Run it**

Run: `PYTHONPATH=lib python -m pytest tests/test_docs_check_workflows.py -q`
Expected: PASS after Tasks 7–9 have pushed. Before them, it names the repos still missing the workflow — that is the check working.

Verify the skip path too, by confirming it does not fail without network:

Run: `PYTHONPATH=lib http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 python -m pytest tests/test_docs_check_workflows.py -q`
Expected: SKIPPED, not FAILED.

- [ ] **Step 4: Commit**

```bash
git add tests/test_docs_check_workflows.py pyproject.toml
git commit -m "docs-conformance: fleet-wide §20.3 check via the GitHub API, skip-on-outage"
```

---

### Task 15: `smd doctor --docs`

**Files:**
- Modify: `bin/smd` (`cmd_doctor`, and the `doctor` subparser at ~line 18321)
- Test: `tests/test_doctor_docs.py`

**Interfaces:**
- Consumes: `sigmond.docs_conformance.check_repo` (Task 1); `sigmond.doctor.component_checkouts`.
- Produces: `smd doctor --docs`, exit 0 always (warn-level, matching doctor's non-repairing checks).

- [ ] **Step 1: Write the failing test**

```python
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SMD = REPO / 'bin' / 'smd'


def _checkout(base, name, *, conformant):
    d = base / name
    (d / '.git').mkdir(parents=True)
    (d / 'docs').mkdir()
    if conformant:
        (d / 'docs' / 'INDEX.md').write_text(
            '# x\n> **Verified against:** x abc1234 on 2026-08-27 — docs\n')
        (d / 'docs' / 'REQUIREMENTS.md').write_text('# x\n')
        wf = d / '.github' / 'workflows'
        wf.mkdir(parents=True)
        (wf / 'docs-check.yml').write_text(
            'uses: HamSCI/sigmond/.github/workflows/docs-check.yml@main\n')
    return d


def _run(base):
    return subprocess.run([sys.executable, str(SMD), 'doctor', '--docs',
                           '--base', str(base)],
                          capture_output=True, text=True)


def test_reports_the_nonconformant_checkout(tmp_path):
    _checkout(tmp_path, 'good', conformant=True)
    _checkout(tmp_path, 'bad', conformant=False)
    r = _run(tmp_path)
    assert 'bad' in r.stdout
    assert '20.1' in r.stdout


def test_is_warn_level_and_exits_zero(tmp_path):
    """doctor reports; it does not gate. A docs gap must not make an
    operator's diagnostic exit non-zero mid-incident."""
    _checkout(tmp_path, 'bad', conformant=False)
    assert _run(tmp_path).returncode == 0


def test_clean_tree_says_so(tmp_path):
    _checkout(tmp_path, 'good', conformant=True)
    r = _run(tmp_path)
    assert 'good' not in r.stdout or '0' in r.stdout
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=lib python -m pytest tests/test_doctor_docs.py -q`
Expected: FAIL — `unrecognized arguments: --docs`

- [ ] **Step 3: Add the flag**

At the `doctor` subparser (~`bin/smd:18321`), after the `--base` argument:

```python
    p.add_argument('--docs', action='store_true',
                   help='also report contract §20 docs-surface gaps (warn-only)')
```

- [ ] **Step 4: Implement the check in `cmd_doctor`**

After the existing `base` validation and before the `--fix` branch:

```python
    if getattr(args, 'docs', False):
        from sigmond.docs_conformance import check_repo
        _heading('docs surface (contract §20)')
        total = 0
        for checkout in component_checkouts(base):
            findings = check_repo(checkout)
            total += len(findings)
            for f in findings:
                _warn(f'{checkout.name}: {f.path} [{f.rule}] {f.reason}')
        if total == 0:
            _ok('every installed component carries its docs surface')
        else:
            _info(f'{total} gap(s) — see CLIENT-CONTRACT.md §20')
        # Warn-level by design: a docs gap must never make an operator's
        # diagnostic exit non-zero during an incident.  It is also why this
        # walks checkouts rather than the catalog: a component not installed
        # here is simply absent, never "missing".  A client deactivated
        # between events must not read as a fault.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=lib python -m pytest tests/test_doctor_docs.py -q`
Expected: PASS, 3 passed

- [ ] **Step 6: Run it for real**

```bash
python3 bin/smd doctor --docs --base /home/mjh/hamsci/repos
```
Expected: `every installed component carries its docs surface`, or named gaps for `graphify-out`/`ka9q-radio`, which are not bound components.

- [ ] **Step 7: Update the CLI table**

`tests/test_docs_cli_table.py` checks `smd --help` against a documented table. Run the full suite and update whichever doc it names:

Run: `PYTHONPATH=lib python -m pytest tests/ -q`
Expected: PASS — the whole suite, zero failures (the gate per the 08-25 baseline).

- [ ] **Step 8: Commit**

```bash
git add bin/smd tests/test_doctor_docs.py docs/
git commit -m "smd doctor --docs: fleet-side §20 check, warn-level, blind to enablement"
```

---

## Verification

- [ ] `PYTHONPATH=lib python -m pytest tests/ -q` — zero failures.
- [ ] `python3 scripts/docs-conformance.py .` in sigmond — `0 finding(s)`.
- [ ] Task 11's loop — `0 finding(s)` for all 19 bound components.
- [ ] `gh run list --workflow docs-check.yml` green in all 19 repos.
- [ ] `graphify update /home/mjh/hamsci/repos` — keep the graph current (never `graphify update .` from `/home/mjh/hamsci`).

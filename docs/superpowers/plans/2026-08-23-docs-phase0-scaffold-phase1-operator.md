# Documentation Program — Phase 0 (scaffold) + Phase 1 (operator guide) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the HamSCI/DASI2 suite a single front door (`sigmond/docs/README.md`) with a complete, *verified* operator guide, a per-repo docs index everywhere, dated design notes moved out of user-facing doc roots, and a link checker that keeps it honest.

**Architecture:** Docs live in `sigmond/docs/{operator,scientist,contributor,hardware,archive}/` plus per-repo `docs/INDEX.md`; cross-repo material is linked, never moved; one ★-canonical page per topic; every new/touched page carries a header block (`Audience / Status / Verified against / Canonical for`). A stdlib-only link checker (`sigmond/scripts/docs-linkcheck.py`) runs as a pytest so the existing `tests.yml` CI enforces it from day one.

**Tech Stack:** Markdown (GitHub-rendered), Python 3.11 stdlib (link checker), pytest, git. No code changes to any product; software gaps go to `docs/contributor/docs-gap-ledger.md`.

**Spec:** `sigmond/docs/superpowers/specs/2026-08-23-documentation-program-design.md` — read §1–§3, §6–§10 before starting.

## Global Constraints

- **No product code changes.** Only `*.md`, the link-checker script + its test, and `QUICKSTART.txt`. A software gap ⇒ one line in `sigmond/docs/contributor/docs-gap-ledger.md` (created in Task 2), never a code edit.
- **Workspace:** repos are at `/home/mjh/hamsci/repos/<repo>/`; private ops notes at `/home/mjh/hamsci/ops/` and `/home/mjh/.claude/projects/-home-mjh-hamsci/memory/` (read-only sources of facts; **every fact taken from a memory note must be re-verified** against code or a live host before it lands in a page).
- **Commit to `main` in each repo; never create branches; never push** (the owner pushes). Commit message trailer, verbatim:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012XQRNXmBj87SxR5H5UxZqt
  ```
- **Header block** (verbatim shape) at the top of every page created or substantively edited, immediately under the H1:
  ```
  > **Audience:** operator | scientist | contributor | all
  > **Status:** draft | current | shipped | historical | pointer
  > **Verified against:** <repo> <commit-or-tag> on <YYYY-MM-DD> — <how: live dasi002 / live b4 / code / not re-verified>
  > **Canonical for:** <topic>        (or  **See instead:** [<path>](<path>)  for pointer pages)
  ```
- **Naming:** new narrative pages are `lowercase-kebab.md`; specs/contracts keep `SCREAMING-KEBAB.md`.
- **Moves** use `git mv` (history preserved). A moved file that has inbound references from another repo, from code comments, or from ops memory gets a **pointer file** left at the old path (header block with `Status: pointer` + `See instead:` + one line of context).
- **Link check must pass** (`pytest tests/test_docs_links.py` in sigmond, and `python3 scripts/docs-linkcheck.py <repo>` for any other repo touched) before every commit.
- **Operator pages speak to a human**, not a developer: no jargon without the glossary, every command shows where to run it (`[host]` vs `[VM]`), every "why" stated in one sentence.
- **Live verification** uses the read-only fleet fan-out only. Fleet ssh aliases come from `/home/mjh/hamsci/ops/ssh/config.fleet` (read `/home/mjh/hamsci/ops/docs/fleet-ssh-access.md` first). Hosts: `b4` (production, AC0G/B4) and `dasi002` (plumbing testbed, no antenna). **Never restart, stop, `--apply`, `--fix`, or edit anything on a host.** `smd` must run unprivileged (it refuses sudo).
- Ports known today (verify live): ka9q-web `:8081`, hf-timestd timing dashboard `:8000`, gmag-webui `:8082`, Proxmox GUI `:8006`.

---

## File map

**Create (sigmond):**
- `scripts/docs-linkcheck.py` — stdlib link checker (relative links, `#anchors`, `https://github.com/HamSCI/<repo>/blob/main/<path>` mapped to the local workspace)
- `tests/test_docs_links.py` — runs the checker over `docs/`, `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`
- `docs/README.md` — front door (three audience paths)
- `docs/INDEX.md` — every page in `sigmond/docs` by audience, ★ = canonical
- `docs/contributor/docs-conventions.md` — the rules above, for the next author
- `docs/contributor/docs-gap-ledger.md` — running list of software gaps (becomes the issue batch in Phase 4)
- `docs/archive/README.md` — "historical; canonical pages win"
- `docs/hardware/shopping-list.md`
- `docs/operator/{README,hardware,install,registration,day-2,remote-access,troubleshooting,do-not-touch,glossary}.md`
- pointer files left at old paths (listed per task)

**Modify (sigmond):** `README.md` (Who-are-you block; §What you need → pointer), `docs/install-quickstart.md` + `docs/installation-guide.md` (hardware section → pointer), `docs/T6-BLOCK-SLIP-ROOT-CAUSE-2026-08-10.md` (→ pointer), Status headers on 7 kept design notes, `docs/PROVISIONING-INPUTS.md` (link to registration.md).

**Modify (other repos):** `sigmond-appliance/INSTALL.md` §2/§3/§11 + `QUICKSTART.txt` (hub URL) + new `docs/INDEX.md`; `wspr-recorder` (archive move, `CLAUDE.md:177` v0.7→v0.8, `README.md:91`, `CLAUDE.md:316`, new `docs/INDEX.md`); `psk-recorder` (archive decision, new `docs/INDEX.md`); `mag-recorder` (archive moves, `docs/mag-usb-upstream.md:6`, new `docs/INDEX.md`); `meteor-scatter`, `gpsdo-monitor`, `hs-uploader`, `hamsci-dsp` (new `docs/INDEX.md`); `hs-uploader/docs/PER-SITE-SETUP.md` + `hf-timestd/docs/PSWS_SETUP_GUIDE.md` (one "operator narrative lives at…" line each).

---

# PHASE 0 — scaffold

### Task 1: Link checker + test

**Files:**
- Create: `sigmond/scripts/docs-linkcheck.py`
- Create: `sigmond/tests/test_docs_links.py`

**Interfaces:**
- Produces: CLI `python3 scripts/docs-linkcheck.py [--workspace DIR] PATH...` → exit 0 if all links resolve, exit 1 and one `file:line: broken -> target` line per failure. Function `check_paths(paths: list[Path], workspace: Path) -> list[str]` (list of failure strings) importable from tests.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/mjh/hamsci/repos/sigmond && source .venv/bin/activate 2>/dev/null || source venv/bin/activate; pytest tests/test_docs_links.py -v`
Expected: FAIL — `FileNotFoundError` / spec loader error because `scripts/docs-linkcheck.py` does not exist.

- [ ] **Step 3: Write the checker**

```python
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

LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
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
                    failures.append(f"{md}:{lineno}: missing anchor -> {target}")
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
```

`chmod +x scripts/docs-linkcheck.py`.

- [ ] **Step 4: Run the synthetic tests; expect them to pass and the real-docs test to report the current breakage**

Run: `pytest tests/test_docs_links.py -v`
Expected: `test_checker_detects_broken_link` PASS, `test_checker_checks_anchor_in_target` PASS. `test_doc_links_resolve` may FAIL listing pre-existing broken links in `sigmond/docs` — **record that list** (paste into the Task 3 notes); do not fix content in this task unless a failure is a checker bug (e.g. a valid GitHub anchor form the slugifier mishandles — fix the slugifier, not the doc).

If the real-docs failures are pre-existing doc breakage, temporarily mark `test_doc_links_resolve` with `@pytest.mark.xfail(strict=True, reason="pre-existing broken links; removed in Task 4")` so the suite stays green, and remove the mark in Task 4 once the sweep is clean.

- [ ] **Step 5: Run the full sigmond suite to make sure nothing else broke**

Run: `pytest -q 2>&1 | tail -3`
Expected: same pass count as before plus 3 (or 2 + 1 xfail).

- [ ] **Step 6: Commit**

```bash
git add scripts/docs-linkcheck.py tests/test_docs_links.py
git commit -m "docs: stdlib Markdown link checker + test (relative links, anchors, HamSCI cross-repo links)"
```
(append the trailer from Global Constraints)

---

### Task 2: Conventions page, gap ledger, archive README

**Files:**
- Create: `sigmond/docs/contributor/docs-conventions.md`
- Create: `sigmond/docs/contributor/docs-gap-ledger.md`
- Create: `sigmond/docs/archive/README.md`

- [ ] **Step 1: Write `docs/contributor/docs-conventions.md`** with exactly these sections (fill each from the Global Constraints and spec §2; no other content):

```markdown
# Documentation conventions

> **Audience:** contributor
> **Status:** current
> **Verified against:** sigmond <commit> on 2026-08-23 — code
> **Canonical for:** how docs are organised and kept true across the HamSCI/DASI2 repos

## 1. Where things live
(front door `docs/README.md`; `docs/INDEX.md`; `operator/ scientist/ contributor/ hardware/ archive/`; per-repo docs/INDEX.md; cross-repo = link never move)

## 2. One canonical page per topic
(★ marker in every INDEX; "when two pages disagree the canonical one wins"; duplicates become pointer files; nothing deleted)

## 3. The header block
(the verbatim block; what each field means; `Verified against` records HOW)

## 4. Pointer files
(shape: H1, header block with Status: pointer + See instead, one sentence of context)

## 5. Archive policy
(what moves: dated investigation reports, session logs, plans marked complete/superseded, design notes whose design shipped and which are no longer the best explanation; what stays: shipped-architecture notes still the best explanation, with `Status: shipped`; `git mv` only; pointer left when referenced from another repo / code / ops memory; each archive/ has a README)

## 6. Naming
(lowercase-kebab narrative; SCREAMING-KEBAB specs/contracts; `<TOPIC>-YYYY-MM-DD.md` dated artifacts; per-client six-file skeleton ARCHITECTURE/CONFIG/INSTALL/OPERATIONS/REQUIREMENTS/SIGMOND-CONTRACT)

## 7. Writing for each audience
(operator: human words, [host]/[VM] on every command, why in one sentence, glossary link; scientist: assumes Python, links ka9q-python docs rather than restating; contributor: links code paths)

## 8. Docs travel with behavior
(one paragraph: a PR that changes a CLI surface, config key, unit, path, wizard prompt or observable behavior touches the canonical page or says "no doc impact"; CONTRIBUTING §14 will make this a rule — Phase 3)

## 9. Checking links
(`python3 scripts/docs-linkcheck.py docs README.md CONTRIBUTING.md CLAUDE.md` from the sigmond root; `pytest tests/test_docs_links.py`; from any other repo: `python3 ../sigmond/scripts/docs-linkcheck.py docs README.md`)

## 10. Software gaps found while writing
(append to `docs-gap-ledger.md`; never fix code in a docs change; docs say "today: X — tracked in <repo>#N" once filed)
```

- [ ] **Step 2: Write `docs/contributor/docs-gap-ledger.md`**

```markdown
# Docs-gap ledger

> **Audience:** contributor
> **Status:** current (working file)
> **Verified against:** n/a
> **Canonical for:** software gaps discovered while documenting; each row becomes a `docs-gap` issue in the owning repo at the end of each phase

| # | Repo | Gap (what the doc wanted to say) | What is true today | Page that needs it | Issue |
|---|------|----------------------------------|--------------------|--------------------|-------|
| 1 | sigmond | one command that reports attached hardware (RX888 serial, GPSDO model, mag sensor) | scattered: `smd admin environment`, `smd watch gpsdo`, mag logs | operator/day-2.md, hardware/shopping-list.md | — |
| 2 | sigmond-appliance | the RAC page's root@ ssh command is wrong (PermitRootLogin no) | operators must ssh as hamsci@ via the PM | operator/remote-access.md | — |
| 3 | hs-uploader | PSWS transport for a new client's products | only hf-timestd/GRAPE + mag ship to PSWS; GRAPE uploader bypasses [uploads] policy | scientist/becoming-a-client.md (Phase 2) | — |
| 4 | sigmond | client scaffold command (`smd component add` takes a repo, not a template) | ADD-A-CLIENT says "copy psk-recorder" | scientist/becoming-a-client.md (Phase 2) | — |
```

- [ ] **Step 3: Write `docs/archive/README.md`**

```markdown
# Archive

> **Audience:** contributor
> **Status:** historical
> **Verified against:** n/a
> **Canonical for:** nothing — these pages are history

Dated investigation reports, session logs, plans that completed or were
superseded, and design notes whose design has shipped. They are kept for
provenance and **may contradict current documentation; when they do, the
★-canonical page listed in [`../INDEX.md`](../INDEX.md) wins.** Nothing here
is maintained. Moved with `git mv`, so `git log --follow` shows each file's
history.
```

- [ ] **Step 4: Link check and commit**

Run: `python3 scripts/docs-linkcheck.py docs/contributor docs/archive` → `0 broken link(s)`.
```bash
git add docs/contributor/docs-conventions.md docs/contributor/docs-gap-ledger.md docs/archive/README.md
git commit -m "docs: conventions, gap ledger, archive README (documentation program phase 0)"
```

---

### Task 3: sigmond archive moves, pointers, Status headers

**Files:**
- Move (git mv → `docs/archive/`): `docs/SESSION_2026-06-12_CLIENT_RESILIENCE_SWEEP.md`, `docs/METEOR-SCATTER-DESIGN.md`, `docs/PHASE-D-SERVER-MERGE-ENDPOINT.md`, `docs/TUI-RECONCILIATION-DESIGN.md`, `docs/TUI-FUNCTION-INVENTORY.md`, `tui-configurator.md` (repo root → `docs/archive/tui-configurator.md`)
- Create pointer files at: `docs/PHASE-D-SERVER-MERGE-ENDPOINT.md` (linked by URL from `psk-recorder/docs/REQUIREMENTS.md:79,324` and `docs/PSWS-INTERFACE-BOUNDARY.md:113`), `docs/TUI-FUNCTION-INVENTORY.md` (named in `lib/sigmond/tui/widgets/component_tree.py:13` and `tests/test_tui_navigation.py:145`), `docs/METEOR-SCATTER-DESIGN.md` (named in `meteor-scatter/docs/REQUIREMENTS.md:235,352,395`)
- Rewrite as pointer: `docs/T6-BLOCK-SLIP-ROOT-CAUSE-2026-08-10.md` → `https://github.com/HamSCI/hf-timestd/blob/main/docs/T6-BLOCK-SLIP-ROOT-CAUSE-2026-08-10.md` (keep its 27-line B4 mitigation list *below* the pointer block — it is the only record of the xhci IRQ pin + decode-spread drop-in; linked from `docs/PRODUCER-THREAT-MODEL.md`)
- Add Status header (no other edit) to kept design notes: `docs/install-redesign.md` (`Status: shipped (stages 0–3); still the best explanation of install-implies-enable`), `docs/install-orchestration-design.md` (`Status: shipped (phases A–C)`), `docs/CAPACITY-MEASUREMENT-PLAN.md` (`Status: plan, not executed`), `docs/MULTI-INSTANCE-ARCHITECTURE.md`, `docs/RADIOD-IDENTIFICATION.md`, `docs/PRODUCER-THREAT-MODEL.md`, `docs/PACKET-LOSS-DIAGNOSTICS.md` (`Status: shipped`/`current`)
- Fix any inbound links inside `sigmond/docs/**` that the moves break (the checker will list them).

- [ ] **Step 1: Move**

```bash
cd /home/mjh/hamsci/repos/sigmond
git mv docs/SESSION_2026-06-12_CLIENT_RESILIENCE_SWEEP.md docs/archive/
git mv docs/METEOR-SCATTER-DESIGN.md docs/archive/
git mv docs/PHASE-D-SERVER-MERGE-ENDPOINT.md docs/archive/
git mv docs/TUI-RECONCILIATION-DESIGN.md docs/archive/
git mv docs/TUI-FUNCTION-INVENTORY.md docs/archive/
git mv tui-configurator.md docs/archive/tui-configurator.md
```

- [ ] **Step 2: Pointer files** — each exactly this shape (adjust title/target/context):

```markdown
# Phase D — wsprdaemon-server PSK merge endpoint (moved)

> **Audience:** contributor
> **Status:** pointer
> **Verified against:** n/a
> **See instead:** [archive/PHASE-D-SERVER-MERGE-ENDPOINT.md](archive/PHASE-D-SERVER-MERGE-ENDPOINT.md)

Plan, never implemented; archived 2026-08-23. Kept at this path because
`psk-recorder/docs/REQUIREMENTS.md` links here by URL.
```
Contexts: TUI-FUNCTION-INVENTORY — "self-declared generated artifact, stale since the 2026-08-11 TUI reconciliation; named in `lib/sigmond/tui/widgets/component_tree.py` and `tests/test_tui_navigation.py`". METEOR-SCATTER-DESIGN — "Phase 1 shipped 2026-06-12; `meteor-scatter/docs/REQUIREMENTS.md` cites its §3/§7".

- [ ] **Step 3: T6 pointer rewrite** — prepend to `docs/T6-BLOCK-SLIP-ROOT-CAUSE-2026-08-10.md`:

```markdown
# T6 block-slip root cause — B4 mitigations (pointer + local record)

> **Audience:** contributor
> **Status:** pointer
> **Verified against:** n/a
> **See instead:** [hf-timestd/docs/T6-BLOCK-SLIP-ROOT-CAUSE-2026-08-10.md](https://github.com/HamSCI/hf-timestd/blob/main/docs/T6-BLOCK-SLIP-ROOT-CAUSE-2026-08-10.md) — the 108-line analysis is canonical.

What follows is only the record of the layer-1 mitigations deployed on B4
on 2026-08-10 (xhci IRQ pin, decode-wave spreading); it is historical.
```
…then the existing content unchanged.

- [ ] **Step 4: Status headers** — insert the header block directly under the H1 of each of the 7 kept notes; `Verified against:` = `sigmond <HEAD short sha> on 2026-08-23 — not re-verified (header only)`. `Canonical for:` values: install-redesign → "install-implies-enable vocabulary and station patterns"; install-orchestration-design → "bring-up phase order (A–C)"; CAPACITY-MEASUREMENT-PLAN → "how to measure headroom (unexecuted)"; MULTI-INSTANCE-ARCHITECTURE → "per-reporter instance shape"; RADIOD-IDENTIFICATION → "radiod multicast naming"; PRODUCER-THREAT-MODEL → "what threatens radiod data production"; PACKET-LOSS-DIAGNOSTICS → "diagnosing RTP loss".

- [ ] **Step 5: Run the checker, fix what the moves broke**

Run: `python3 scripts/docs-linkcheck.py docs README.md CONTRIBUTING.md CLAUDE.md`
Expected: only pre-existing failures from Task 1 Step 4 plus any link inside `docs/` that pointed at a moved file — fix the latter to the new `archive/` path (e.g. `docs/install-redesign.md` links `TUI-FUNCTION-INVENTORY`; `docs/PSWS-INTERFACE-BOUNDARY.md:113` → `archive/PHASE-D-SERVER-MERGE-ENDPOINT.md`).

- [ ] **Step 6: Run the TUI tests that mention the inventory** (they only *name* the file in a docstring, but prove it):

Run: `pytest tests/test_tui_navigation.py -q 2>&1 | tail -2` → same result as before the move.

- [ ] **Step 7: Commit**

```bash
git add -A docs tui-configurator.md
git commit -m "docs: archive dated design notes/session logs; pointers where referenced; Status headers on kept design notes"
```

---

### Task 4: sigmond `docs/INDEX.md` + `docs/README.md` front door + README "Who are you?"

**Files:**
- Create: `sigmond/docs/INDEX.md`, `sigmond/docs/README.md`
- Modify: `sigmond/README.md` (insert after line 4, before `## Architecture at a glance`)
- Modify: `sigmond/tests/test_docs_links.py` (remove the xfail if it was added)

- [ ] **Step 1: Build the INDEX** — run `ls docs/*.md docs/*/*.md` and classify **every** file into these sections, as a table `| Doc | What it gives you |` per section, ★ after the canonical page of each topic:

```
# sigmond documentation index
(header block; Canonical for: the map of sigmond/docs)

Reading-order map. ★ = canonical — when two docs disagree, the ★ one wins.
Front door for each audience: [docs/README.md](README.md).

## 1. Operator (host a station)            ← operator/*.md (Phase 1 will fill; list what exists now, mark "(coming)" only for pages this plan creates)
## 2. Scientist (record a signal)          ← EVENT-CLIENT-PLAYBOOK ★, ADD-A-CLIENT, STATION-NETWORK-CAPABILITIES, SCINTILLATION-MONITORING, scientist/* (coming — Phase 2)
## 3. Contributor (work on the code)        ← ../CONTRIBUTING.md ★, contributor/docs-conventions ★, CLIENT-CONTRACT ★, REQUIREMENTS*, CLI-V2-SPEC, MULTI-INSTANCE ★, RADIOD-IDENTIFICATION ★, networking ★, native-binaries, greenfield-runbook ★ (bare-host bring-up), install-quickstart, installation-guide, install-redesign, install-orchestration-design, HOST-CAPACITY-PLANNING, CAPACITY-MEASUREMENT-PLAN, PRODUCER-THREAT-MODEL ★, PACKET-LOSS-DIAGNOSTICS ★, timing-chain-architecture, PSWS-MAPPING, PSWS-INTERFACE-BOUNDARY, PSWS-HEARTBEAT-SPEC, PROVISIONING-INPUTS, proxmox/*, superpowers/specs/*, superpowers/plans/*
## 4. Hardware                              ← hardware/* (coming — Task 8)
## 5. Archive                               ← archive/README.md (one line; do not list each file)
```
Every `.md` under `docs/` must appear exactly once (archive collapsed to its README). Pointer files are listed with "(pointer)".

- [ ] **Step 2: Write `docs/README.md`** (the front door, ≤ 60 lines):

```markdown
# HamSCI / DASI2 station documentation

> **Audience:** all
> **Status:** current
> **Verified against:** sigmond <sha> on <date> — code
> **Canonical for:** the front door

Pick the door that matches you. Each path is self-contained; you will be
told when (and only when) you need something from another path.

## I host a station (amateur radio operator)
You have, or want, a Sigmond appliance: a small PC running the HamSCI
receiver that uploads WSPR / FT8 / timing / magnetometer data for science.
→ **[Operator guide](operator/README.md)** — shopping list, install, registration, the weekly check, what to do when it breaks, what not to touch.

## I want to record a signal (scientist / event responder)
You have a signal in mind (a beacon, an eclipse experiment, a time standard)
and want it captured on a station — possibly by Friday.
→ **[Scientist guide](scientist/README.md)** *(Phase 2 — until then start with [EVENT-CLIENT-PLAYBOOK.md](EVENT-CLIENT-PLAYBOOK.md) and [ka9q-python Getting Started](https://github.com/HamSCI/ka9q-python/blob/main/docs/GETTING_STARTED.md))*

## I work on the code (contributor)
→ **[CONTRIBUTING.md](../CONTRIBUTING.md)** then **[Contributor guide](contributor/README.md)** *(Phase 3 — until then: [docs-conventions.md](contributor/docs-conventions.md), [CLIENT-CONTRACT.md](CLIENT-CONTRACT.md), [ADD-A-CLIENT.md](ADD-A-CLIENT.md))*

## Everything, by audience
[INDEX.md](INDEX.md) lists every page and marks the canonical one per topic.

## What this is
One paragraph: SigMonD orchestrates a ka9q-radio/RX888 station plus clients; data → sink → hs-uploader → wsprnet / pskreporter / wsprdaemon / PSWS. Link the architecture diagram (`architecture.png`).
```
Until Phase 2/3 create `scientist/README.md` and `contributor/README.md`, link them as plain text with "(coming)" **or** create two 8-line stubs with `Status: draft` and the interim links — create the stubs (link checker stays green, readers get the interim path).

- [ ] **Step 3: Insert into `sigmond/README.md`** after the opening paragraph (line 4):

```markdown
> **Who are you?** Station host → [Operator guide](docs/operator/README.md) ·
> Scientist wanting a signal recorded → [Scientist guide](docs/scientist/README.md) ·
> Developer → [CONTRIBUTING.md](CONTRIBUTING.md). Full map: [docs/README.md](docs/README.md).
```

- [ ] **Step 4: Clean the pre-existing broken links** recorded in Task 1 Step 4 (fix the link or, if the target is legitimately gone, turn the link into plain text with "(removed)"), then remove the `xfail` mark.

Run: `pytest tests/test_docs_links.py -v` → 3 passed.

- [ ] **Step 5: Commit**

```bash
git add docs/INDEX.md docs/README.md docs/scientist/README.md docs/contributor/README.md README.md tests/test_docs_links.py docs
git commit -m "docs: front door, full docs index with canonical markers, README who-are-you block"
```

---

### Task 5: Fix the known contradictions (appliance burn text, hub URL, wspr contract version)

**Files:**
- Modify: `sigmond-appliance/INSTALL.md` §2 (lines 48–61), §3 (63–79), §11 row 1 (~200); add header block under the H1
- Modify: `sigmond-appliance/QUICKSTART.txt` (append 2 lines)
- Modify: `wspr-recorder/CLAUDE.md:177`

Facts: images are published as **raw `.img` only since v3.24** (xz produced unbootable Pi-Imager sticks and saved ~9%); the canonical burn note is `wd30:README-BURNING.txt`; the current image line is v3.34 (2026-08-22). Verify the version by reading `/home/mjh/hamsci/repos/sigmond-appliance/docs/RELEASE.md` and `git -C sigmond-appliance log --oneline -5`.

- [ ] **Step 1: INSTALL.md §2** — replace the `.img.xz` paragraph with:

```markdown
The image is published **uncompressed** (`.img`) — there is nothing to
decompress. (Images before v3.24 shipped as `.img.xz`; if you were handed
one of those, ask for a current image instead.) Check the checksum if you
like: `sha256sum -c sigmond-appliance-<version>.sha256`.
```
Update the example filenames to the current release naming (look at `build-usb-v3.sh` for the output pattern, e.g. `sigmond-appliance-v3.34-YYYYMMDD-HHMM.img`).

- [ ] **Step 2: INSTALL.md §3** — keep Etcher as the GUI option, keep the Mac `dd`, add the Linux `dd` line from QUICKSTART (`bs=4M oflag=direct conv=fsync status=progress`), and add the mandatory verification line from QUICKSTART: "Before you boot from it, check the stick really took the image: `sudo file -s /dev/sdX` must say `DOS/MBR boot sector`." Also mention Raspberry Pi Imager as acceptable ("choose Use custom").

- [ ] **Step 3: INSTALL.md §11 row 1** — change "Re-burn using the **decompressed** `.img` (not `.xz`)" to "Re-burn and verify with `file -s` (step 3)".

- [ ] **Step 4: INSTALL.md header block** under the H1: `Audience: operator · Status: current · Verified against: sigmond-appliance <sha> on <date> — wording only; install flow last exercised by the v3.34 build (2026-08-22) and the nested rig · Canonical for: burning, booting and first-boot wizard of the appliance image`. Add one line after the header: "Day-2 operation, troubleshooting beyond §11, remote access and what-not-to-touch live in the [Operator guide](https://github.com/HamSCI/sigmond/blob/main/docs/operator/README.md)."

- [ ] **Step 5: QUICKSTART.txt** — append before the "RETURNING STATION?" block:

```
AFTER INSTALL — the operator guide (day-2 checks, troubleshooting, what not
to touch): https://github.com/HamSCI/sigmond/blob/main/docs/operator/README.md
```
Confirm `build-usb-v3.sh` only substitutes `@@VERSION@@`/`@@VTAG@@` (grep it) so the new lines ride through untouched.

- [ ] **Step 6: wspr-recorder/CLAUDE.md:177** `## Client contract (v0.7)` → `## Client contract (v0.8)`; read the section and fix any v0.7-specific statement that contradicts `sigmond/docs/CLIENT-CONTRACT.md`'s version header (open both; if the section body only lists the four subcommands it needs no other change).

- [ ] **Step 7: Link check both repos, commit each**

```bash
python3 /home/mjh/hamsci/repos/sigmond/scripts/docs-linkcheck.py /home/mjh/hamsci/repos/sigmond-appliance/INSTALL.md /home/mjh/hamsci/repos/sigmond-appliance/README.md
cd /home/mjh/hamsci/repos/sigmond-appliance && git add INSTALL.md QUICKSTART.txt && git commit -m "docs: burn instructions match reality (raw .img, verify with file -s); point operators at the sigmond operator guide"
cd /home/mjh/hamsci/repos/wspr-recorder && git add CLAUDE.md && git commit -m "docs: client contract version v0.7 -> v0.8 to match SIGMOND-CONTRACT.md"
```

---

### Task 6: Client-repo archive moves + INDEX (wspr-recorder, psk-recorder, mag-recorder)

**Files:**
- wspr-recorder: `git mv docs/PHASE-2-COORDINATION.md docs/archive/`; create `docs/archive/README.md` (copy Task 2 Step 3 text); update `README.md:91` and `CLAUDE.md:316` links to `docs/archive/PHASE-2-COORDINATION.md`; create `docs/INDEX.md`
- psk-recorder: decide `docs/jt9-decoder.md` and `docs/decoder-findings.md` (Step 2); create `docs/INDEX.md`
- mag-recorder: `git mv` `docs/epoch-2026-08-10-v009-adoption.md`, `docs/plan-2026-08-10-v009-mqtt-provenance.md`, `docs/upstream-report-2026-08.md` → `docs/archive/`; pointer at `docs/upstream-report-2026-08.md` (named in `src/mag_recorder/core/driver_config.py:116`); fix `docs/mag-usb-upstream.md:6` link; `docs/archive/README.md`; create `docs/INDEX.md`

- [ ] **Step 1: wspr-recorder** moves + link fixes as listed. INDEX shape (same for every client repo):

```markdown
# <repo> documentation index

> header block (Audience: all; Canonical for: the map of this repo's docs)

★ = canonical; when two docs disagree the ★ one wins. Suite-wide front door:
[HamSCI/sigmond docs](https://github.com/HamSCI/sigmond/blob/main/docs/README.md).

| Doc | Audience | What it gives you |
|-----|----------|-------------------|
| [../README.md](../README.md) | all | what this client is, quick start |
| [ARCHITECTURE.md](ARCHITECTURE.md) ★ | contributor | internals |
| [CONFIG.md](CONFIG.md) ★ | operator/contributor | every config key |
| [INSTALL.md](INSTALL.md) | contributor | standalone install (operators get this via the appliance; see the operator guide) |
| [OPERATIONS.md](OPERATIONS.md) ★ | operator/contributor | running it: units, logs, watch |
| [REQUIREMENTS.md](REQUIREMENTS.md) | contributor | formal requirements |
| [SIGMOND-CONTRACT.md](SIGMOND-CONTRACT.md) ★ | contributor | conformance map to sigmond's CLIENT-CONTRACT |
| [archive/](archive/README.md) | — | history |
```
List every `.md` actually present (ls docs/); add rows for extras.

- [ ] **Step 2: psk-recorder decision** — run `git -C psk-recorder log --oneline -3 -- vendor/jt9-decode src/psk_recorder/core/slot.py` and `grep -n "jt9" psk-recorder/src/psk_recorder/config.py | head`. If jt9 is a selectable decoder in current config (it is referenced from 4 source files), **keep** `docs/jt9-decoder.md` live: change its Status line to `Status: shipped — jt9 is a selectable decoder; this page is the design record` and add the header block. Archive `docs/decoder-findings.md` (investigation notes) with a pointer (it is linked from jt9-decoder.md — update that link instead of leaving a pointer). If jt9 is NOT selectable today, archive both and leave a pointer at `docs/jt9-decoder.md` (code comments name it). Record which branch you took in the commit message.

- [ ] **Step 3: mag-recorder** moves, pointer, link fix, INDEX (its docs are README-heavy: rows for `PROVENANCE.md` ★, `mag-usb-upstream.md` ★, `REQUIREMENTS.md`, `archive/`).

- [ ] **Step 4: Link-check each repo and commit each**

```bash
for r in wspr-recorder psk-recorder mag-recorder; do python3 /home/mjh/hamsci/repos/sigmond/scripts/docs-linkcheck.py /home/mjh/hamsci/repos/$r/docs /home/mjh/hamsci/repos/$r/README.md /home/mjh/hamsci/repos/$r/CLAUDE.md; done
```
Expected: 0 broken in each (fix any pre-existing ones you can; list the rest in the commit body). Commit per repo: `docs: docs/INDEX.md with canonical markers; archive dated notes (pointers where referenced)`.

---

### Task 7: INDEX stubs for the remaining repos (sigmond-appliance, meteor-scatter, gpsdo-monitor, hs-uploader, hamsci-dsp)

**Files:** create `docs/INDEX.md` in each (`mkdir -p docs` where missing: hamsci-dsp has `docs/REQUIREMENTS.md` only; sigmond-appliance has `docs/RELEASE.md`).

- [ ] **Step 1:** Same shape as Task 6 Step 1. Specifics:
  - sigmond-appliance rows: `../INSTALL.md` ★ operator (burn/boot/wizard), `../QUICKSTART.txt` operator (on-stick), `../README.md` contributor (pipeline + packaging rules), `RELEASE.md` ★ contributor (build/test/bless/roll).
  - meteor-scatter: list the six files **and add a warning row**: "⚠ ARCHITECTURE/CONFIG/INSTALL/OPERATIONS/SIGMOND-CONTRACT are a stale copy of psk-recorder's text (they say FT8/FT4; this client decodes MSK144 via `jt9 --msk144`). `REQUIREMENTS.md` is the one accurate document. Truthing is scheduled (docs program Phase 3)." Add the same one-line warning under the H1 of each of the five stale files. Do **not** rewrite them now.
  - gpsdo-monitor rows: `../README.md` ★ (hardware support matrix — operators land here from the shopping list), `PROTOCOL.md`, `SCHEMA-v1.md`, `TOPOLOGY.md`, `REQUIREMENTS.md`, `superpowers/` (plans/specs — contributor).
  - hs-uploader rows: `PER-SITE-SETUP.md` ★ contributor/operator ("per-transport mechanics; the operator narrative is the sigmond operator guide → registration.md"), `REQUIREMENTS.md`, `../README.md`.
  - hamsci-dsp rows: `../README.md` ★, `REQUIREMENTS.md`.

- [ ] **Step 2:** link-check each; commit each: `docs: docs/INDEX.md with canonical markers`.

- [ ] **Step 3: Phase 0 checkpoint** — run from the sigmond root:
```bash
pytest tests/test_docs_links.py -q && for r in sigmond-appliance wspr-recorder psk-recorder meteor-scatter mag-recorder gpsdo-monitor hs-uploader hamsci-dsp; do echo "== $r"; ls /home/mjh/hamsci/repos/$r/docs/INDEX.md; done
```
All present, checker green. Update `docs/INDEX.md` in sigmond if Task 5–7 added pages it should list (none expected).

---

# PHASE 1 — operator guide

Read before starting Phase 1: `sigmond-appliance/INSTALL.md` (whole), `sigmond/README.md` §Monitoring (l.270–314) + §Debugging (315–350), `sigmond/CONTRIBUTING.md` §3 (l.63–132) + §13 (413–422), `sigmond/docs/networking.md`, `/home/mjh/hamsci/ops/docs/fleet-ssh-access.md`, and these memory notes (facts to re-verify, not copy): `reference_b4_reboot_pitfalls.md`, `reference_b4_upload_paths.md`, `reference_smd_update_honesty_bugs.md`, `reference_smd_version_provenance.md`, `reference_smd_fleet.md`, `reference_gw2_rac.md`, `reference_smd_rac.md`, `reference_ka9q_web_rac.md`, `reference_b4_rac_registrar_bug.md`, `reference_fleet_reach_topology.md`, `reference_b4_v3_access.md`, `reference_lbe1421_pps_not_on_usb.md`, `reference_gpsdo_monitor.md`, `reference_b4_mag_sensor_dead_20260818.md`, `reference_grape_spectrogram_gaps.md`, `reference_b4_rx888_firmware_serial.md`, `reference_hf_timestd_deploy_mechanics.md`, `feedback_smd_no_sudo.md`, `feedback_update_orientations.md`, `reference_b4_local_only_dasi2.md`.

**Live verification recipe (used by Tasks 10–14):** `ssh dasi002 'smd status; smd version; smd doctor; smd psws status; smd admin rac status; getent passwd hamsci sigmond; ls /etc/sigmond-appliance/version && cat /etc/sigmond-appliance/version'` and the same on `b4`. If the alias fails, read `ops/docs/fleet-ssh-access.md` — reach is nested via the PM host; `-J` does not work. Paste the outputs into `/tmp/claude-1000/-home-mjh-hamsci/c34744cd-f838-4226-981e-840c532862e2/scratchpad/live-<host>-<date>.txt` for reuse.

### Task 8: `hardware/shopping-list.md` + de-triplicate the hardware lists

**Files:**
- Create: `sigmond/docs/hardware/shopping-list.md`
- Modify: `sigmond/README.md` §"What you need" (l.46–58) → keep the 4-bullet summary, add "Full list with models, what is optional, and what B4 runs: [docs/hardware/shopping-list.md](docs/hardware/shopping-list.md)"; `sigmond/docs/install-quickstart.md` + `sigmond/docs/installation-guide.md` hardware sections → same pointer sentence replacing the duplicated bullets.
- Modify: `sigmond/docs/INDEX.md` §4 Hardware row.

Sources to read: `sigmond-appliance/INSTALL.md` §1 (the most complete list today), `gpsdo-monitor/README.md` (LBE model matrix), `mag-recorder/README.md` + `docs/PROVENANCE.md` (RM3100 + Pololu), `hf-timestd/INSTALLATION.md` §Hardware (TS-1, ZED-F9P), `ka9q-radio/docs/SDR/rx888.md`, `sigmond/docs/proxmox/*` (host floor), `sigmond/docs/HOST-CAPACITY-PLANNING.md`. For "what B4 runs": `ssh b4 'smd admin environment; lsusb; nproc; free -g; df -h /'` (read-only).

- [ ] **Step 1: Write the page** with these sections:

```
# What to buy — station hardware
(header block; Audience: operator; Canonical for: the station parts list)

## The one-paragraph version
## Required
| Part | Exact model we run | Why | Notes / alternatives |
  - Mini-PC (floor: N cores / RAM / NVMe size — take from HOST-CAPACITY-PLANNING + proxmox docs; state what B4 has)
  - RX888 Mk II SDR (USB 3 — must be a blue/USB-3 port; 10 MHz ext-ref input)
  - GPSDO: Leo Bodnar LBE-1421 (10 MHz → RX888; PPS → TS-1); matrix link to gpsdo-monitor README; note: "the PPS over USB is liveness only — timing comes from the 10 MHz + TS-1 path"
  - HF antenna (guidance + the DXE reference used on B4; "any broadband HF antenna that hears WSPR on 20 m will do for a first light")
  - USB stick ≥ 16 GB for the install
  - Ethernet (wired; Wi-Fi is not supported by the appliance)
## Optional — and what you lose without it
  - TS-1 time injector (no TS-1 ⇒ no T6 ns-class timing; WSPR/FT8 still fine)
  - RM3100 magnetometer + Pololu USB-I²C adapter (no mag ⇒ no geomagnetic product)
  - ZED-F9P GNSS (TEC product only)
## Cabling (one diagram-as-list: antenna → RX888 RF; GPSDO 10 MHz → RX888 CLK; GPSDO PPS → TS-1 → RX888 RF path; RX888 USB3 → PC; GPSDO USB → PC (monitoring only); mag USB → PC)
## Approximate cost (ranges, dated "as of 2026-08")
## What AC0G/B4 actually runs (the known-good build; from the live `smd admin environment` output)
## Things that look right but aren't
  - USB 2 port for the RX888 (sample loss)
  - a USB hub between RX888 and PC
  - Wi-Fi
  - a laptop (host keyboard/USB get passed to the VM; the console goes dead by design)
```
Every "we run" claim cites where it was read (`lsusb` on b4 / INSTALL.md §1 / gpsdo-monitor README).

- [ ] **Step 2: Replace the three duplicated lists** with the pointer sentence (leave the 4-bullet summary only in `sigmond/README.md`).

- [ ] **Step 3: Check + commit**
Run: `pytest tests/test_docs_links.py -q`.
```bash
git add docs/hardware/shopping-list.md README.md docs/install-quickstart.md docs/installation-guide.md docs/INDEX.md
git commit -m "docs(hardware): canonical shopping list; hardware bullets elsewhere now point here"
```

---

### Task 9: `operator/README.md`, `glossary.md`, `hardware.md`, `install.md`

**Files:** create the four under `sigmond/docs/operator/`; update `docs/INDEX.md` §1.

- [ ] **Step 1: `operator/README.md`** (≤ 90 lines):

```
# Operator guide — hosting a HamSCI station
(header block; Audience: operator; Canonical for: the operator's table of contents)

## What you are signing up for (10-minute version)
 - a small always-on PC + an HF antenna + (ideally) a GPS-disciplined clock, on wired internet
 - it runs by itself; it uploads WSPR and FT8/FT4 spots, HF time-standard timing, and magnetometer data to wsprnet.org, pskreporter.info, wsprdaemon.org and HamSCI's PSWS
 - your job: keep it powered and connected, glance at one page a week, and tell the fleet admin when something on this list happens
 - approximate cost → [hardware.md](hardware.md)
 - if you are NOT installing from the appliance USB image, you are a contributor: see ../../CONTRIBUTING.md

## The path
| Step | Page | Time |
| 1 Buy the parts | hardware.md | — |
| 2 Install from the USB stick | install.md | 45 min |
| 3 Register your station's uploads | registration.md | 30 min + portal waits |
| 4 Learn what "healthy" looks like | day-2.md | 15 min |
| 5 (optional) Let the fleet admin reach it | remote-access.md | 10 min |
| When it breaks | troubleshooting.md |
| Before you touch anything | do-not-touch.md |
| Words | glossary.md |

## Two machines in one box (VM vs PM) — explain once: the Proxmox host ("PM", `<designator>-PM`) and the decoder VM ("VM", `<designator>`), two addresses, one password at install; which one you ssh into for what. Every command in this guide is tagged [host] or [VM].

## Getting help — what to send the fleet admin (the `smd doctor` + `smd status` + `cat /etc/sigmond-appliance/version` + `smd version` recipe), and where: (ask the owner for the channel — placeholder is NOT allowed: use "the HamSCI DASI2 operators group / your fleet admin" and link hamsci.org)
```

- [ ] **Step 2: `operator/glossary.md`** — one table, alphabetical, plain-English definitions ≤ 2 lines each: appliance, canary, decoder VM / VM, designator, fleet admin, GPSDO, grid square, heartbeat, host / PM (Proxmox), ka9q-web, PPS, PSWS, RAC, radiod, reporter ID, RTP, RX888, sink, smd, spot, SSRC, TS-1, wizard (`sigmond-setup`), wsprdaemon, wsprnet, pskreporter.

- [ ] **Step 3: `operator/hardware.md`** and **`operator/install.md`** — pointer pages (Status: pointer) to `../hardware/shopping-list.md` and `https://github.com/HamSCI/sigmond-appliance/blob/main/INSTALL.md` respectively; install.md additionally says in two lines what to expect (~45 min, the machine powers itself off once on purpose, keep the stick until told) and "when INSTALL.md §9 says 'check it's alive', come back here → day-2.md".

- [ ] **Step 4:** INDEX §1 rows; link check; commit `docs(operator): guide front page, glossary, hardware/install pointers`.

---

### Task 10: `operator/registration.md`

**Files:** create `sigmond/docs/operator/registration.md`; add one line near the top of `hs-uploader/docs/PER-SITE-SETUP.md` and `hf-timestd/docs/PSWS_SETUP_GUIDE.md` ("Operators: the step-by-step narrative is [sigmond operator/registration.md](https://github.com/HamSCI/sigmond/blob/main/docs/operator/registration.md); this page is the per-transport reference"); `sigmond/docs/PROVISIONING-INPUTS.md` gets the same line under its H1.

Sources: `hs-uploader/docs/PER-SITE-SETUP.md` §1–§5, `hf-timestd/docs/PSWS_SETUP_GUIDE.md` steps 1–6, `sigmond/docs/PROVISIONING-INPUTS.md` §2–§4 + §7, `INSTALL.md` §7 + §10, `smd psws --help`, live `smd psws status` on dasi002 and b4, `reference_smd_config_show_and_psws.md`, `reference_b4_upload_paths.md`, `reference_psws_timestd.md`, `reference_b4_mag_sensor_dead_20260818.md` (the PSWS mag zip requirement).

- [ ] **Step 1: Write** with sections:

```
# Getting your data accepted — registration
## What the wizard already did (reporter ID, grid, site profile; PSWS key generated; RAC if chosen)
## wsprnet.org — nothing to register; your reporter ID is your identity; confirm: Database → search reporter
## pskreporter.info — nothing to register; confirm: lookup your callsign as receiver; note "don't poll"
## wsprdaemon.org — self-provisioning key exchange happens on first upload (PER-SITE-SETUP §4); what to check
## PSWS (HamSCI) — the only portal step: account → site → instrument; paste the key the VM's login banner shows (or `smd psws enroll`); then `[VM] smd psws verify`; `[VM] smd psws status` meanings; mag data needs the runmag.log (what that means for you: nothing, it's automatic, but if the PSWS page shows no magnetometer data, tell the admin)
## Confirming everything flows (a table: product → where to look → how long to wait: WSPR 15 min, FT8 minutes, PSWS daily, mag daily)
## Returning station / moving a station (link INSTALL.md §4 and §12; key restore)
## What can go wrong here (points into troubleshooting.md anchors)
```

- [ ] **Step 2: Verify live** — `ssh dasi002 'smd psws status'` and `ssh b4 'smd psws status; smd watch uploads --help'`; compare each claimed state string with the page. Record in the header `Verified against: sigmond <sha> on <date> — live b4 + dasi002 (smd psws status)`.

- [ ] **Step 3:** cross-repo one-liners; link check (sigmond + hs-uploader + hf-timestd files touched); commit in each repo (`docs(operator): registration narrative` / `docs: point operators at the sigmond registration narrative`).

---

### Task 11: `operator/day-2.md`

**Files:** create `sigmond/docs/operator/day-2.md`.

Sources: `sigmond/README.md` §Monitoring/§Debugging, `CONTRIBUTING.md` §3 (two update orientations) + §10 (heartbeat/board) + §13, `smd status/doctor/update/version --help` (run them), live outputs from both hosts (the recipe above), `INSTALL.md` §9/§10, memory notes `reference_smd_update_honesty_bugs.md` (hosts older than f389f3e can report current when not — state the observable: `smd version` shows the SHA), `reference_smd_version_provenance.md` (`/etc/sigmond-appliance/version` is install-time only), `feedback_update_orientations.md`, `reference_b4_reboot_pitfalls.md`, `reference_grape_spectrogram_gaps.md` (gap_count is the honest field), `feedback_smd_no_sudo.md`.

- [ ] **Step 1: Write** with sections:

```
# Day 2 — what healthy looks like and the weekly check
## The four windows (ka9q-web :8081 waterfall; timing dashboard :8000; gmag dashboard :8082 (if mag); Proxmox GUI :8006) — what "good" looks like in each, one screenshot-free sentence each
## The weekly 5-minute check  [VM]
   1. `smd status` — annotated sample output (paste a REAL trimmed output from b4, replace identifiers with <designator>); what each column means; the 3 lines that matter
   2. your spots on wsprnet / pskreporter (link registration.md §confirming)
   3. disk: `df -h /` (what number is bad: the guardian starts evicting at 95% — link troubleshooting)
   4. `smd doctor` — only when something looks off; never `--fix` unless told
## Updates — who decides and what you run
   - station-inward: `[VM] smd update` is a DRY RUN by default and prints a plan; `smd update --apply` does it; **do this only when the fleet admin says the release is blessed** — the admin can also push (fleet-outward) and will tell you
   - how to know what you run: `smd version` (per-component SHAs); `cat /etc/sigmond-appliance/version` is the IMAGE you were installed from — it never changes after install, so it is not "your version"
   - never `apt upgrade`, never `pip`, never `git pull` by hand (→ do-not-touch.md)
## Power loss, reboots, moving the box
   - it comes back by itself; allow 10 minutes; if spots don't resume in 30 → troubleshooting
   - host keyboard dead after the first reboot is normal (USB belongs to the VM)
   - moving = INSTALL.md §12
## The heartbeat (what your station tells the fleet, how often, how to see that it's landing — `smd admin environment`? verify which verb shows it; if none, say "the fleet admin sees it on the board" and add to the gap ledger)
## Passwords and logins (INSTALL.md §10 recap; change them; `passwd` in both places)
```

- [ ] **Step 2: Verify every command on dasi002** (read-only set: `smd status`, `smd version`, `smd doctor`, `smd update` (dry run), `df -h /`, `cat /etc/sigmond-appliance/version`) — paste real outputs into the page where the plan says "REAL"; redact nothing but hostnames/designators.

- [ ] **Step 3:** INDEX row; link check; commit `docs(operator): day-2 — healthy, the weekly check, updates, power loss`.

---

### Task 12: `operator/remote-access.md`

**Files:** create `sigmond/docs/operator/remote-access.md`; ledger row #2 gets the page path confirmed.

Sources: `smd admin rac --help` + subverb helps; `sigmond/scripts/proxmox/README.md` (RAC mentions); `INSTALL.md` §7 (wizard RAC prompt) + §11; memory `reference_gw2_rac.md` (gw2 gateway, WireGuard tiers, frps API), `reference_smd_rac.md`, `reference_ka9q_web_rac.md`, `reference_b4_rac_registrar_bug.md` (wizard used to default to dead vpn.hamsci.org:35737; gw2.wsprdaemon.org works; v3.25 ladder), `reference_fleet_reach_topology.md` (sigmond runs in the VM; `-J` can't work; the RAC page's `root@` command is wrong because `PermitRootLogin no`), `reference_vpn_frps_secure_tofu.md`, `project_wizard_console_queue.md` (the 4-tier RAC ladder). Live: `ssh b4 'smd admin rac status'`, `ssh dasi002 'smd admin rac status'`.

- [ ] **Step 1: Write** with sections:

```
# Remote access (RAC) — letting the fleet admin reach your station
## What it is (an outbound tunnel from the HOST to HamSCI's gateway; nothing inbound opens on your router; you can turn it off)
## What it exposes and to whom (host ssh + Proxmox GUI, via the gateway, to fleet admins with keys; not to the public)
## Turning it on / off / checking  [host]
   - wizard asked; later: `sigmond-setup --reconfigure` (host) — verify whether the VM-side `smd admin rac configure/status/start/stop` is the operator surface or the host-side; document the one that is TRUE on v3.34 (check `which sigmond-setup`/`smd admin rac status` on dasi002 PM vs VM)
   - `smd admin rac status` annotated sample
## How the admin actually connects (the truthful path; explicitly: the RAC page's `root@` command does not work — tracked in the gap ledger; the right form is `ssh hamsci@…` via the PM)
## Privacy and switching it off for good
## If it says FAILED (INSTALL.md §11 row; the registrar default trap is fixed since v3.25 — so "ask for a current image" if older)
```

- [ ] **Step 2:** verify live; INDEX row; link check; commit `docs(operator): remote access (RAC) explained for hosts`.

---

### Task 13: `operator/troubleshooting.md`

**Files:** create `sigmond/docs/operator/troubleshooting.md`; add one line at the end of `sigmond-appliance/INSTALL.md` §11: "More symptoms and the decision tree: [operator troubleshooting](https://github.com/HamSCI/sigmond/blob/main/docs/operator/troubleshooting.md)."

Sources: INSTALL.md §11, `sigmond/README.md` §Debugging, `docs/networking.md` (IGMP snooping silent failure), `docs/PACKET-LOSS-DIAGNOSTICS.md` (for the "what to send" only), memory `reference_b4_reboot_pitfalls.md` (radiod not enabled, stale ka9q-web -m, corrupt +10 min anchor → restart recorders after radiod), `reference_b4_mag_sensor_dead_20260818.md` (RM3100 NACK ⇒ frozen constant; replug ⇒ restart mag-recorder), `reference_lbe1421_pps_not_on_usb.md`, `reference_gpsdo_monitor.md` (udev group-gpsdo; attached-before-install ⇒ udev re-trigger), `reference_b4_upload_paths.md` (pending_uploads growing with frozen oldest is NORMAL), `reference_chaos_drills_20260821.md` (disk ≥95% guardian evicts; venv wipe on failed install — fixed #47; board blind to disk-full — fixed #49), `reference_timing_watchdog.md`, `reference_wspr_recorder_env_escaping.md` if still relevant (check fixed), `reference_b4_rx888_firmware_serial.md`.

- [ ] **Step 1: Write** — symptom-first, each symptom a `###` heading (so anchors work from registration/day-2), in this order:

```
# When something is wrong
## First, the 2-minute triage  [VM]: `smd status` → `smd doctor` → `df -h /` → look at ka9q-web. Then find your symptom below.
### No spots on wsprnet / pskreporter after 30 min
### Spots stopped (were fine before)
### "Uploads pending" keeps growing (when that is normal, when not)
### RX888 not found / waterfall blank
### GPS not locked / timing dashboard red
### Magnetometer flat line
### Disk filling up
### The host console keyboard is dead (normal — why)
### The VM did not start after a reboot
### Remote access says FAILED
### Web pages don't load but ssh works (IGMP snooping / switch — networking.md)
### After a power cut everything is back except one client
## Replug, restart, reboot, reinstall — which one, when (a 4-row table; restart/stop are `sudo smd restart <name>` on the VM — confirm the exact verb and that it's the ONLY sudo you should ever type)
## What to send when you ask for help (exact paste recipe + where the logs are: `smd admin log`? verify)
## What NOT to do while troubleshooting (→ do-not-touch.md)
```
Each symptom: *likely causes (most common first)* → *what to check (command + what good/bad looks like)* → *what to do* → *when to stop and ask*.

- [ ] **Step 2:** verify every command on dasi002; INDEX row; link check (both repos); commit sigmond `docs(operator): symptom-first troubleshooting` and appliance `docs: INSTALL §11 points at the operator troubleshooting tree`.

---

### Task 14: `operator/do-not-touch.md`

**Files:** create `sigmond/docs/operator/do-not-touch.md`.

Sources: memory `reference_b4_rx888_firmware_serial.md` (DON'T `smd apply` / `component install ka9q-radio` — reverts the fork pin), `feedback_smd_no_sudo.md` (smd refuses sudo), `reference_hf_timestd_deploy_mechanics.md` (NEVER install.sh hf-timestd — ipcrm's the chrony SHM), `reference_radiod_isolation_state.md` + `reference_radiod_cpu_affinity.md` (CPU pinning, `--apply` restarts radiod), `reference_radiod_fft_threads.md` (fft-threads=1), `reference_component_update_no_rebuild.md`, `CONTRIBUTING.md` §8 (deploy trees are not workspaces) + §9, `INSTALL.md` §12 (reconfigure), `reference_b4_reboot_pitfalls.md`.

- [ ] **Step 1: Write** — one table + one paragraph per row:

```
# Do not touch — the guard rails
(Audience: operator; Canonical for: what an operator must not do on a station)

| Don't | Why | If you already did |
| `apt upgrade` / `apt install` in the VM | pinned toolchain; radiod/clients built against it | tell the admin; `smd doctor` |
| `smd apply`, `smd component install ka9q-radio`, `smd install ka9q-radio` | reinstalls radiod from upstream and REVERTS the RX888 firmware/serial fork pin; `--apply` also restarts radiod | tell the admin before the next reboot |
| `sudo smd …` | smd refuses to run under sudo (it would silently do the wrong thing as root); only `smd start/stop/restart/disable` elevate themselves | nothing to undo |
| edit `/etc/radio/radiod@*.conf` by hand | rendered from the site profile; overwritten; fft-threads and affinity are load-bearing | `sigmond-setup --reconfigure` re-renders — ask first |
| `git pull` / `pip install` inside `/opt/git/sigmond/*` | deploy trees are not workspaces; `smd update` is the only path | `smd doctor` |
| run `install.sh` in `/opt/git/sigmond/hf-timestd` | it `ipcrm`s the chrony SHM segment and breaks timing until reboot | reboot the VM, tell the admin |
| change CPU pinning, grub, or the VM's CPU count in Proxmox | radiod lives on an isolated core; changing it causes sample loss | revert in the Proxmox GUI, reboot |
| move the station without `sigmond-setup --reconfigure` | wrong grid on every upload | INSTALL.md §12 |
| plug the RX888 into USB 2 or through a hub | sample loss | move it, restart radiod (`sudo smd restart radiod` — verify verb) |
| power-cycle the RX888 alone while the VM runs | VBUS reset needs the whole box powered off | power the box off/on |
```
Then: "What you MAY do: `smd status`, `smd doctor` (no `--fix`), `smd watch <x>`, `smd update` (dry run), `sudo smd restart <client>` when troubleshooting says so, `passwd`, look at the web pages, reboot/power-cycle the whole box."

- [ ] **Step 2:** verify the sudo/elevation claims against `smd --help` text and `bin/smd` (grep for the sudo refusal); INDEX row; link check; commit `docs(operator): do-not-touch guard rails with the why for each`.

---

### Task 15: Walk-through verification (fresh eyes) and fixes

**Files:** modify any `docs/operator/*.md` / `docs/hardware/shopping-list.md` the walk-through faults; append rows to `docs/contributor/docs-gap-ledger.md`.

- [ ] **Step 1: Dispatch a fresh-context subagent** (general-purpose) with ONLY this brief — do not give it the plan or the spec:

> You are an amateur radio operator who has just installed a HamSCI "Sigmond" station from the appliance USB stick. You are comfortable with ssh and copy-pasting commands, nothing more. Your only documentation is the folder `/home/mjh/hamsci/repos/sigmond/docs/operator/` (start at README.md) and whatever it links to. You can ssh read-only to a real station with `ssh dasi002` (it has no antenna, so "no spots" is expected there). **Do not run anything that changes state** (no sudo, no restart, no --apply, no --fix, no edits on the host). Perform: (1) the weekly check from day-2.md; (2) the registration confirmation steps for PSWS from registration.md; (3) the troubleshooting tree for the symptom "no spots after 30 minutes"; (4) find out whether remote access is enabled and how the admin would connect. For EACH step report: what the docs told you to do, what actually happened, and every point where you had to guess, a command failed, a word was undefined, or a claim looked wrong. Be ruthless; list at least 10 findings or state explicitly that you could not find more. Write the report to `/tmp/claude-1000/-home-mjh-hamsci/c34744cd-f838-4226-981e-840c532862e2/scratchpad/operator-walkthrough.md`.

- [ ] **Step 2: Triage the report** — every finding becomes either a doc fix (do it now) or a ledger row (software gap) or "by design" (add the sentence that would have prevented the guess). Re-run the link checker.

- [ ] **Step 3: Second pass** — dispatch the same brief again against the fixed docs; stop when the report has no finding that is a doc defect.

- [ ] **Step 4: Commit** `docs(operator): walk-through fixes (round N)` with the report path in the body.

---

### Task 16: Phase 1 close-out

- [ ] **Step 1:** `docs/INDEX.md` §1 lists all nine operator pages with ★ on registration, day-2, remote-access, troubleshooting, do-not-touch; `docs/README.md` operator line no longer says "(coming)" anywhere.
- [ ] **Step 2:** `pytest -q` in sigmond (full suite) green; `python3 scripts/docs-linkcheck.py` over every repo touched in Phases 0–1 green.
- [ ] **Step 3:** `cd /home/mjh/hamsci && graphify update /home/mjh/hamsci/repos` (never `graphify update .`).
- [ ] **Step 4:** Write a short handoff comment into `docs/contributor/docs-gap-ledger.md` (top): "Phase 0+1 done <date>; rows above are ready to file as issues (Phase 4); Phase 2 plan next."
- [ ] **Step 5:** Commit `docs: phase 0+1 close-out (index, ledger note)`; list every repo with unpushed commits (`for r in …; do git -C $r status -sb | head -1; done`) in the final report to the owner — **do not push**.

---

## Self-review notes (done at plan-writing time)

- Spec §2 structure → Tasks 2, 4, 7; §3 operator pages → Tasks 9–14; §6 shopping-list → Task 8 (`character.md` is Phase 2); §7 upkeep → link checker in Task 1 (the CI action, PR template, CONTRIBUTING §14 are Phase 3 by spec §10); §8 ledger → Task 2 + per-task rows; §9 verification → live recipe + Task 15; §10 Phase 0/1 "done when" → Task 7 Step 3 and Task 16.
- Archive list narrowed from the audit: hf-timestd is out of scope (spec §11); `sigmond/tasks/` is not a user-facing docs root and is left alone; `install-redesign`/`install-orchestration-design`/`CAPACITY-MEASUREMENT-PLAN` are kept live with Status headers because code and the runbook cite them.
- No placeholders: the only "verify which verb" notes are explicit live checks with the fallback spelled out (ledger row).

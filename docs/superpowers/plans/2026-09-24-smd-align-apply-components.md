# smd align — Plan 2a: `--apply` moves the components

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `smd align --apply` moves each component checkout to the latest blessed release's pin — forward only by default, verified and recorded — and `smd update` stops undoing it.

**Architecture:** `lib/sigmond/align.py` stays the pure planner (it must never import `subprocess`). A new `lib/sigmond/align_apply.py` holds every git and file step, each taking an injected `run` callable (a `subprocess.run` stand-in) so tests fake git exactly as `tests/test_manifest_restore.py`'s `_FakeGit` does. `bin/smd` `cmd_align` gains `--apply`, elevates (`_need_root`), takes the lifecycle lock, and hands the plan to `align_apply.apply_plan`. `plan_update` learns to hold a component sitting at its `.pin`.

**Tech Stack:** Python 3.11+ standard library, git, the existing `installer.clone_repo`, `lifecycle.lifecycle_lock`, `provenance.record_update`.

**Spec:** `docs/superpowers/specs/2026-09-24-smd-align-design.md` — §3 and its "Decisions for `--apply`" section.

## How the tasks specify tests

Tasks 1 and 2 give complete test code.  Tasks 3–7 state each test as exact assertions — inputs, calls and expected results — for the implementer to write out in full, each shown failing before its code exists.  Those tasks need a mid-tier implementer, not a transcriber.

## Scope

Plan 2a covers the component moves: verify the release, resolve and check each pin, move forward / leave ahead / refuse diverged / install missing / reset uv.lock-only dirt, run `install.sh` when dependencies changed, write `.pin`, measure bytes, record, and hold pinned components in `smd update`. **Plan 2b** makes moved code live (radiod rebuild and consumer restarts, service restarts, image-carried files, bring-up re-runs, production verification). Until 2b lands, `--apply` prints `services still run the old code until restarted — Plan 2b` after moving anything.

## The sigmond bootstrap (read before Task 6)

The blessed v3.53 release pins sigmond at `daba1f6`, which predates `smd align`. Two rules keep the command available:

1. **sigmond ahead of its pin stays** (the spec's decision). On a pre-align station an operator first brings sigmond to `main` (which carries `align`); `align --apply` then finds sigmond *ahead* and leaves it.
2. **Re-exec only into a sigmond that has align.** When sigmond moves *forward*, `align` re-executes under the new code only if the target commit contains `lib/sigmond/align_apply.py` (`git cat-file -e <sha>:lib/sigmond/align_apply.py`). Otherwise it continues in the code already loaded, and says so.

## Global Constraints

- `lib/sigmond/align.py` never imports `subprocess` (the existing guard test must keep passing). Every git call lives in `align_apply.py` or `bin/smd` helpers — never in `cmd_align`'s own body (the other guard test).
- Core smd stays stdlib-only.
- Every git step runs as the checkout's owner (`_runuser(owner, argv)` pattern, `Path.owner()`), with `-c safe.directory=<dir>`. Only `install.sh` runs as root.
- Fetch from `origin` by ref (`git fetch --quiet --tags origin`), never by SHA.
- Never stash or discard anything except a `uv.lock`-only change, and log exactly that.
- Stop at the first failed component; everything after it stays untouched; re-running resumes (every step checks before it acts).
- `--apply` elevates via `_need_root` BEFORE any work and takes `lifecycle_lock(reason='align')`; the dry run does neither.
- Commits: develop on main; end each message with the session's attribution lines. Nothing gets pushed by this plan.

## File map

| File | Responsibility |
|---|---|
| `lib/sigmond/align.py` (modify) | `Release` gains `draft`/`prerelease`; `fetch_release` refuses them; `classify` turns `move` into `forward`/`ahead`/`diverged` from an injected ancestry test |
| `lib/sigmond/align_apply.py` (create) | release-tag verification, per-component git steps, `.pin`, install, byte parsing, `apply_plan` orchestration, records |
| `lib/sigmond/update.py` (modify) | `plan_update` holds a component at its `.pin` |
| `bin/smd` (modify) | `cmd_align --apply` (+ `--allow-rollback`, `--max-bytes`), `cmd_update --unpin` |
| `tests/test_align.py`, `tests/test_align_apply.py` (create), `tests/test_align_cli.py`, `tests/test_update_plan.py` (modify) | tests |

---

### Task 1: refuse drafts/prereleases; classify a move's direction (align.py, pure)

**Interfaces — produces:**
- `Release` gains two fields with defaults: `draft: bool = False`, `prerelease: bool = False` (set from the release JSON).
- `fetch_release` raises `LookupError_("release <tag> is a draft or prerelease — not blessed")` when either is true.
- `classify(items: list[Item], is_ancestor: Callable[[str, str, str], bool | None]) -> list[Item]` — for each `move` item: `is_ancestor(name, live, target)` True → status `forward`; `is_ancestor(name, target, live)` True → `ahead` (note `ahead of the pin — left; --allow-rollback moves it back`); both False → `diverged` (note `diverged from the pin — resolve by hand`); either returns None (unknown) → `refuse` (note `ancestry unknown — run a fetch first`). Other statuses pass through unchanged. `RADIOD`'s restart note stays on `forward`.

- [ ] **Step 1: failing tests** — append to `tests/test_align.py`:

```python
class DraftTests(unittest.TestCase):
    def test_draft_or_prerelease_refuses(self):
        for flag in ("draft", "prerelease"):
            meta = json.loads(release_json())
            meta[flag] = True
            fake = FakeUrlopen({align.RELEASES_API + "/latest": json.dumps(meta).encode(),
                                "https://example/manifest": MANIFEST.encode()})
            with self.assertRaises(align.LookupError_) as cm:
                align.fetch_release(urlopen=fake)
            self.assertIn("not blessed", str(cm.exception))


def mv(name, live, target, note=""):
    return align.Item(name, "move", live, target, note)


class ClassifyTests(unittest.TestCase):
    def anc(self, table):
        return lambda name, a, b: table.get((name, a, b), False)

    def test_forward(self):
        out = align.classify([mv("hf-tec", "a1", "b2")], self.anc({("hf-tec", "a1", "b2"): True}))
        self.assertEqual(out[0].status, "forward")

    def test_ahead_is_left(self):
        out = align.classify([mv("sigmond", "c3", "b2")], self.anc({("sigmond", "b2", "c3"): True}))
        self.assertEqual(out[0].status, "ahead")
        self.assertIn("--allow-rollback", out[0].note)

    def test_diverged(self):
        out = align.classify([mv("x", "a1", "b2")], self.anc({}))
        self.assertEqual(out[0].status, "diverged")

    def test_unknown_ancestry_refuses(self):
        out = align.classify([mv("x", "a1", "b2")], lambda *a: None)
        self.assertEqual(out[0].status, "refuse")

    def test_radiod_forward_keeps_restart_note(self):
        out = align.classify([mv(align.RADIOD, "a1", "b2", "RESTARTS radiod on --apply")],
                             self.anc({(align.RADIOD, "a1", "b2"): True}))
        self.assertIn("RESTARTS radiod", out[0].note)

    def test_non_move_items_pass_through(self):
        cur = align.Item("hf-timestd", "current", "5c8196d", "5c8196d")
        self.assertEqual(align.classify([cur], lambda *a: True), [cur])
```

- [ ] **Step 2: run, expect FAIL** — `cd ~/hamsci/repos/sigmond && .venv/bin/python -m pytest tests/test_align.py -v --override-ini addopts=` → the new tests fail (`classify` missing; drafts not refused).

- [ ] **Step 3: implement** in `align.py`: add the two `Release` fields (defaults `False`, placed LAST so existing positional constructions keep working); in `fetch_release`, after parsing `meta`, `if meta.get("draft") or meta.get("prerelease"): raise LookupError_(f"release {meta.get('tag_name')} is a draft or prerelease — not blessed")`, and pass `draft=bool(meta.get("draft")), prerelease=bool(meta.get("prerelease"))` into `Release`. Then:

```python
AHEAD_NOTE = "ahead of the pin — left; --allow-rollback moves it back"
DIVERGED_NOTE = "diverged from the pin — resolve by hand"


def classify(items: list, is_ancestor: Callable) -> list:
    """Turn each 'move' into forward / ahead / diverged. Pure; ancestry is injected."""
    out = []
    for it in items:
        if it.status != "move":
            out.append(it)
            continue
        fwd = is_ancestor(it.component, it.live, it.target)
        back = is_ancestor(it.component, it.target, it.live)
        if fwd is None or back is None:
            out.append(Item(it.component, "refuse", it.live, it.target, "ancestry unknown — run a fetch first"))
        elif fwd:
            out.append(Item(it.component, "forward", it.live, it.target, it.note))
        elif back:
            out.append(Item(it.component, "ahead", it.live, it.target, AHEAD_NOTE))
        else:
            out.append(Item(it.component, "diverged", it.live, it.target, DIVERGED_NOTE))
    return out
```

- [ ] **Step 4: run, expect PASS**; **Step 5: mutation** — swap the `elif fwd` / `elif back` order: `test_forward` or `test_ahead_is_left` must FAIL; restore. **Step 6: commit** — `align: refuse drafts/prereleases; classify a move as forward / ahead / diverged`.

---

### Task 2: verify the release tag against the manifest's commit (align_apply.py)

**Interfaces — produces** (new module `lib/sigmond/align_apply.py`):
- `APPLIANCE_REPO = "https://github.com/HamSCI/sigmond-appliance"`
- `class ApplyError(RuntimeError)`
- `verify_release(rel: Release, run) -> None` — runs `git ls-remote <APPLIANCE_REPO> refs/tags/<tag> refs/tags/<tag>^{}` via `run(argv, capture_output=True, text=True, timeout=60)`; the peeled line (`^{}`) wins over the plain one; the commit must equal `rel.appliance_commit` (full 40-char compare); raises `ApplyError` with a specific message when the tag is absent, the manifest has no `appliance_commit`, or they differ.

- [ ] **Step 1: failing tests** — create `tests/test_align_apply.py`:

```python
"""Tests for sigmond.align_apply — git is faked; nothing touches a real checkout."""
import types
import unittest

from sigmond import align, align_apply

C = "4a92fbdbcce497035994e76636689d84f7ffe967"


def rel(commit=C, tag="v3.53"):
    return align.Release(tag=tag, manifest_text="", appliance_commit=commit,
                         components={"sigmond": "daba1f6"})


def ls_remote(lines):
    def run(argv, **kw):
        return types.SimpleNamespace(returncode=0, stdout="".join(lines), stderr="")
    return run


class VerifyReleaseTests(unittest.TestCase):
    def test_peeled_tag_matches(self):
        align_apply.verify_release(rel(), ls_remote([
            f"1111111111111111111111111111111111111111\trefs/tags/v3.53\n",
            f"{C}\trefs/tags/v3.53^{{}}\n"]))

    def test_mismatch_refuses(self):
        with self.assertRaises(align_apply.ApplyError) as cm:
            align_apply.verify_release(rel(), ls_remote(["2" * 40 + "\trefs/tags/v3.53^{}\n"]))
        self.assertIn("does not point at", str(cm.exception))

    def test_missing_tag_refuses(self):
        with self.assertRaises(align_apply.ApplyError):
            align_apply.verify_release(rel(), ls_remote([]))

    def test_manifest_without_commit_refuses(self):
        with self.assertRaises(align_apply.ApplyError):
            align_apply.verify_release(rel(commit=None), ls_remote([f"{C}\trefs/tags/v3.53\n"]))
```

- [ ] **Step 2: run, expect FAIL** (`cannot import name 'align_apply'`).

- [ ] **Step 3: implement** — create `lib/sigmond/align_apply.py`:

```python
"""smd align --apply — the steps that change a station.

Design: docs/superpowers/specs/2026-09-24-smd-align-design.md.  The planner
(align.py) never runs a process; this module runs git, always through an
injected ``run`` (a subprocess.run stand-in), so tests fake git and never touch
a real checkout.  Stdlib only.
"""
from __future__ import annotations

import subprocess
from typing import Callable, Optional

from sigmond.align import Release

APPLIANCE_REPO = "https://github.com/HamSCI/sigmond-appliance"


class ApplyError(RuntimeError):
    """A step refused or failed; the station stays as it was at that step."""


def verify_release(rel: Release, run: Callable = subprocess.run) -> None:
    """The release tag must point at the manifest's appliance_commit."""
    if not rel.appliance_commit:
        raise ApplyError(f"release {rel.tag}: manifest records no appliance_commit — cannot verify it")
    r = run(["git", "ls-remote", APPLIANCE_REPO, f"refs/tags/{rel.tag}", f"refs/tags/{rel.tag}^{{}}"],
            capture_output=True, text=True, timeout=60)
    plain = peeled = None
    for line in (r.stdout or "").splitlines():
        sha, _, ref = line.partition("\t")
        if ref.endswith("^{}"):
            peeled = sha.strip()
        elif ref.strip() == f"refs/tags/{rel.tag}":
            plain = sha.strip()
    tag_commit = peeled or plain
    if r.returncode != 0 or not tag_commit:
        raise ApplyError(f"release tag {rel.tag} not found in {APPLIANCE_REPO}")
    if tag_commit != rel.appliance_commit:
        raise ApplyError(f"release tag {rel.tag} does not point at the manifest's appliance_commit "
                         f"({tag_commit[:12]} vs {rel.appliance_commit[:12]}) — refusing")
```

- [ ] **Step 4: PASS**; **Step 5: mutation** — compare `plain` before `peeled`: `test_peeled_tag_matches` must FAIL; restore. **Step 6: commit** — `align_apply: verify the release tag points at the manifest's commit`.

---

### Task 3: per-component git steps (align_apply.py)

**Interfaces — produces** (each takes `run` as its last keyword arg, default `subprocess.run`; `as_owner(owner, argv) -> argv` is injected too, default `lambda o, a: ["runuser", "-u", o or "root", "--", *a]`):
- `git(repo, *args) -> list` — `["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args]`
- `fetch(repo, owner, *, run, as_owner) -> int | None` — runs `git -c safe.directory=<repo> -C <repo> fetch --tags --progress origin` as owner (no `--quiet`: git writes the byte counter to stderr only with `--progress`); returns `parse_fetch_bytes(stderr)`; raises `ApplyError` on a non-zero exit.
- `parse_fetch_bytes(stderr: str) -> int | None` — last `Receiving objects: … , <n> <unit>` → bytes (`B`, `KiB`, `MiB`, `GiB`); None when git printed nothing (up to date).
- `resolve(repo, sha, *, run) -> str` — `git rev-parse --verify --quiet --end-of-options <sha>^{commit}`; returns the 40-char SHA; raises `ApplyError("…: <sha> does not resolve to exactly one commit")` on failure.
- `in_origin_history(repo, full, *, run) -> bool` — `git for-each-ref --contains <full> --format=%(refname) refs/remotes/origin refs/tags` → True when any line.
- `is_ancestor(repo, a, b, *, run) -> bool | None` — `git merge-base --is-ancestor a b` → rc 0 True, rc 1 False, else None.
- `dirty_files(repo, *, run) -> list[str]` — `git status --porcelain` paths.
- `reset_uvlock(repo, owner, *, run, as_owner)` — `git checkout -- uv.lock` as owner.
- `checkout(repo, owner, full, *, run, as_owner)` — `git checkout --detach <full>` as owner; ApplyError on failure.
- `changed_files(repo, a, b, *, run) -> set[str]` — `git diff --name-only a b`.
- `INSTALL_TRIGGERS = {"pyproject.toml", "uv.lock", "install.sh", "scripts/install.sh"}`
- `write_pin(repo, full)` — writes `<repo>/.pin` (`full + "\n"`), and appends `.pin` to `<repo>/.git/info/exclude` if not already listed (create the file/dir if missing). Plain file I/O; the caller chowns (Task 5).
- `normalize_repo(url) -> str` — lowercase, `git@github.com:` → `https://github.com/`, strip trailing `/` and `.git`.

- [ ] **Step 1: failing tests** — append to `tests/test_align_apply.py`. Build a fake in the `_FakeGit` style: a callable recording argv lists, answering by subcommand. Tests (write each out in full):
  - `parse_fetch_bytes`: `"Receiving objects: 100% (412/412), 1.23 MiB | 2.00 MiB/s, done.\n"` → `1289748`; `"...,  56.50 KiB | ..."` → `57856`; `""` → `None`.
  - `resolve`: fake returns rc 0 + 40-char stdout → that SHA; rc 128 → `ApplyError` naming the SHA.
  - `is_ancestor`: rc 0 → True, rc 1 → False, rc 128 → None.
  - `in_origin_history`: stdout `refs/remotes/origin/main\n` → True; empty → False.
  - `dirty_files`: porcelain `" M uv.lock\n?? build/\n"` → `["uv.lock", "build/"]`.
  - `checkout` runs `git … checkout --detach <full>` wrapped by `as_owner("sigmond", …)` — assert the recorded argv; rc 1 → `ApplyError`.
  - `fetch` never passes a SHA to `git fetch` (assert argv ends with `origin`).
  - `write_pin` in a tmp dir with a `.git/info/` → `.pin` holds the SHA; `exclude` gains exactly one `.pin` line even when called twice.
  - `normalize_repo("git@github.com:HamSCI/sigmond.git") == normalize_repo("https://github.com/hamsci/sigmond/")`.

- [ ] **Step 2: FAIL**; **Step 3: implement** each function exactly as its interface line states (straightforward; keep each under ~15 lines; `parse_fetch_bytes` uses `re.findall(r"Receiving objects:.*?,\s*([\d.]+)\s*(B|KiB|MiB|GiB)", stderr)` and the last match, multipliers 1/1024/1024²/1024³, `int(round(...))`). **Step 4: PASS.** **Step 5: mutation** — make `is_ancestor` map rc 1 to None: its test must FAIL; restore. **Step 6: commit** — `align_apply: per-component git steps (fetch by ref, resolve, ancestry, uv.lock reset, .pin)`.

---

### Task 4: `apply_plan` — the orchestration (align_apply.py)

**Interfaces — consumes:** Tasks 1–3. **Produces:**
- `@dataclass class Ctx: base: Path; run: Callable; as_owner: Callable; owner_of: Callable[[Path], str]; catalog: dict; origins: dict[str, str | None]; allow_rollback: bool = False; max_bytes: int | None = None; install_missing: Callable[[str, str], None] | None = None; run_install: Callable[[Path], int] | None = None; say: Callable[[str], None] = print`
- `@dataclass class Step: component: str; outcome: str; detail: str = "", bytes: int | None = None` — `outcome` ∈ `moved`, `left`, `installed`, `refused`, `failed`, `skipped` (after a failure or budget stop), `current`.
- `apply_plan(rel: Release, items: list[Item], ctx: Ctx) -> list[Step]`

Behaviour, per item in plan order (sigmond first):
- `current`/`stray` → `Step(current|left)`, nothing run.
- `missing` → `ctx.install_missing(name, target)` then `Step("installed")`; if `install_missing` is None → `Step("refused", "no installer wired")`.
- `refuse`/`diverged` → `Step("refused", note)`.
- `ahead` → if `ctx.allow_rollback`, treat as a move; else `Step("left", AHEAD_NOTE)`.
- `forward` (or allowed rollback), in this order, each failure → `Step("failed", reason)` and every later item `Step("skipped", "stopped after <name> failed")`:
  1. origin check: `normalize_repo(ctx.origins[name]) == normalize_repo(catalog[name].repo)` when both known; mismatch → refused `origin points at <x>; catalog says <y>`.
  2. `fetch` (bytes added to a running total; if `max_bytes` set and total exceeds it → this step completes, every later item `skipped` "byte budget reached").
  3. `full = resolve(target)`; `in_origin_history(full)` else refused `pin <sha> is not in origin's history`.
  4. dirt: `files = dirty_files`; only `uv.lock` → `reset_uvlock`, detail notes `reset uv.lock`; anything else → refused (reuse manifest-restore's wording, with its `uv.lock` hint).
  5. `from_full = resolve(live)`; `checkout(full)`; `write_pin(full)`.
  6. `changed_files(from_full, full) & INSTALL_TRIGGERS` → `ctx.run_install(repo)`; non-zero → failed.
  7. `Step("moved", f"{live[:8]} -> {full[:8]}", bytes)`.
- After the loop, if any `moved`: `ctx.say("services still run the old code until restarted — Plan 2b")`.

- [ ] **Step 1: failing tests** — in `tests/test_align_apply.py`, a `FakeGit` class (dirty/unresolvable/changed_files/ancestry/origin tables, records calls) and a `ctx()` helper with `as_owner=lambda o, a: ["as", o, *a]`, `owner_of=lambda p: "sigmond"`, a `SimpleNamespace` catalog entry per component with `.repo`, `run_install` recording calls. Write these tests in full:
  - forward move: checkout argv present with the full SHA; `.pin` written (patch `write_pin` or use a tmp base with `.git/info`); Step `moved`.
  - ahead without flag → `left`, no checkout argv recorded; with `allow_rollback=True` → checkout recorded.
  - uv.lock-only dirt → `git checkout -- uv.lock` recorded BEFORE the detach checkout; other dirt → `refused`, no checkout.
  - origin mismatch → `refused`, no fetch recorded.
  - resolve failure on the second of three components → first `moved`, second `failed`, third `skipped`.
  - `pyproject.toml` in changed files → `run_install` called; changed files only `README.md` → not called.
  - byte budget: fetch stderr reports 2 MiB, `max_bytes=1_000_000` → first `moved`, second `skipped` "byte budget".
  - missing with `install_missing` → called with `(name, target)`; `installed`.
  - fetch argv never contains a SHA.

- [ ] **Step 2: FAIL**; **Step 3: implement** `Ctx`, `Step`, `apply_plan` per the behaviour list (one helper `_move(it, ctx) -> Step` holding steps 1–7 keeps `apply_plan` short). **Step 4: PASS.** **Step 5: mutation** — drop the `skipped` marking after a failure: the resolve-failure test must FAIL; restore. **Step 6: commit** — `align_apply: apply_plan — forward moves, ahead left, dirt rules, budget, stop at first failure`.

---

### Task 5: records — manifest, aligned-release, history (align_apply.py)

**Interfaces — produces:**
- `ALIGNED_RECORD = Path("/etc/sigmond-appliance/aligned.json")`
- `record(rel: Release, steps: list[Step], *, manifest_path: Path, aligned_path: Path = ALIGNED_RECORD, history: Callable[[dict], None], now: Callable[[], str]) -> None` — only when no step `failed`/`skipped`: write the release's `manifest_text` to `manifest_path` atomically (tmp + `os.replace`; keep the previous as `<path>.prev`), and write `aligned_path` as JSON `{"release": tag, "appliance_commit": …, "at": now(), "components": {name: target}}`. Always append one history entry per `moved`/`installed`/`failed` step: `{"at": now(), "what": f"smd align --apply {tag}: {component} {outcome} {detail}"}`. Never touches `/etc/sigmond-appliance/version`.

- [ ] **Step 1: failing tests** — tmp paths: a clean run writes both files and `.prev`; a run with a `failed` step writes neither but still logs the failure; the version file (a tmp file passed nowhere) stays untouched by construction — assert `record` has no parameter or code path naming `version` (`inspect.getsource`). **Steps 2–6** as before; mutation: write the manifest even when a step failed → its test must FAIL. Commit — `align_apply: record the alignment (atomic manifest, aligned.json, history)`.

---

### Task 6: `smd align --apply` in the CLI (bin/smd)

**Interfaces — consumes:** everything above; `_need_root`, `lifecycle_lock`, `_runuser`, `installer.clone_repo`, `load_catalog`, `MANIFEST_PATH`, `provenance.record_update`, `HISTORY_FILE`.
**Produces:** `smd align` gains `--apply`, `--allow-rollback`, `--max-bytes N` (accepts `500K`, `20M`, `1G`). Exit: `0` every item current/moved/installed/left-ahead; `1` anything refused, failed or skipped; `2` target could not be established or verified.

Flow in `cmd_align` when `--apply` (keep all git out of `cmd_align`'s own body — call helpers):
1. `if _need_root('align --apply'): return 1` before any work; then `with lifecycle_lock(reason='align'):`.
2. `rel = align.fetch_release(...)`; `align_apply.verify_release(rel)` (ApplyError → print, exit 2).
3. `live, dirty, origins, errors = _align_live_state(base)`; `items = align.plan_align(...)`; `items = align.classify(items, _align_is_ancestor_after_fetch)` where the helper (in bin/smd, outside `cmd_align`) first runs `align_apply.fetch` for each `move` component as its owner, then `align_apply.is_ancestor`. (Fetch happens once per component; the budget counter carries into `apply_plan`.)
4. **sigmond bootstrap:** if sigmond's item is `forward`, move it first via `apply_plan(rel, [that_item], ctx)`; then, if `git cat-file -e <full>:lib/sigmond/align_apply.py` succeeds (helper `_align_target_has_align`), `os.execv(sys.argv[0], [sys.argv[0], 'align', '--apply', '--release', rel.tag, *passthrough_flags])` — re-running sees sigmond current. Otherwise print `sigmond moved to <sha>, which predates smd align — continuing with the code already loaded`.
5. `steps = align_apply.apply_plan(rel, remaining_items, ctx)` with `install_missing = lambda name, sha: _align_install_missing(name, sha)` (clone via `installer.clone_repo(entry, ref=sha)` then run its install.sh as root; do NOT enable in topology), `run_install = _align_run_install` (the manifest-restore `install.sh` invocation), `owner_of = Path.owner`, `as_owner = _runuser`.
6. `align_apply.record(rel, steps, manifest_path=MANIFEST_PATH, history=lambda e: record_update(HISTORY_FILE, e), now=<utc iso Z>)`.
7. Print one line per step and a summary (`moved N, installed N, left N, refused N, failed N, skipped N, fetched X MiB`).

- [ ] **Step 1: failing tests** — in `tests/test_align_cli.py`, patch `_need_root` → False, `lifecycle_lock` → `contextlib.nullcontext`, `align.fetch_release`, `align_apply.verify_release`, `_align_live_state`, `_align_is_ancestor_after_fetch`, `align_apply.apply_plan` (returning canned `Step`s), `align_apply.record`, `_align_target_has_align`, and `os.execv`. Tests:
  - dry run unchanged: without `--apply`, none of `_need_root`, `lifecycle_lock`, `apply_plan`, `record` is called.
  - `--apply` calls `_need_root` before `fetch_release`.
  - verify failure → rc 2, `apply_plan` not called.
  - sigmond forward + target has align → `os.execv` called with `--release v3.53`; target lacks align → no execv, the "predates smd align" line printed, `apply_plan` called for the rest.
  - a `failed` step → rc 1; all `moved` → rc 0.
  - `--max-bytes 20M` parses to 20971520.
  - The existing guard test (`test_dry_run_cannot_move_anything`) still passes unchanged.

- [ ] **Steps 2–6** as before (mutation: skip the `_need_root` call → its test FAILS). Full suite once. Commit — `align: smd align --apply (verify, classify, sigmond bootstrap, apply, record)`.

---

### Task 7: `smd update` holds a pinned component

**Interfaces — produces:** `plan_update` (lib/sigmond/update.py) — when `state['repos'][name]` carries `pinned: str` (the `.pin` SHA) and `at_pin: True`, emit `Refusal(name, f"pinned by smd align to {pin[:8]} — `smd update --unpin` releases it", kind="pin")` instead of a pull. `cmd_update` fills `pinned`/`at_pin` from `doctor.git_state` (it already reads `.pin`) and gains `--unpin`: ignore pins, and after a successful pull of a formerly pinned component, delete its `.pin`.

- [ ] **Step 1: failing tests** — `tests/test_update_plan.py`: a behind repo with `pinned`/`at_pin` → a `Refusal` of kind `pin`, no `pull` Action; the same with `at_pin: False` (HEAD moved off the pin by hand) → normal pull (a pin the operator already left does not hold); CLI test (follow `tests/test_update_executor.py`'s fixture): `--unpin` pulls it and removes `.pin`.
- [ ] **Steps 2–6** as before (mutation: drop the `at_pin` check → the second test FAILS). Commit — `update: hold a component at its .pin (smd align) unless --unpin`.

---

### Task 8: first live alignment — B4 (test bench)

Controller task, with Michael (station root needs his `!`).

- [ ] **Step 1:** bus note: sigmond on B4 to `main` (bootstrap), then `smd align --apply` — expected moves: codar-sounder, hf-tec, ka9q-python, meteor-scatter, superdarn-sounder (forward, 1 commit each + ka9q-python 3.26), sigmond ahead (left). No radiod move. Services keep old code until restarted (Plan 2b).
- [ ] **Step 2:** capture B4's identity bundle first (`ops/bin/site-identity check b4` must match).
- [ ] **Step 3:** Michael runs, on B4: bring sigmond to main (`sudo -u sigmond git -C /opt/git/sigmond/sigmond pull --ff-only`), then `smd align` (dry run), then `smd align --apply`.
- [ ] **Step 4:** verify: `smd align` afterwards exits 0 except sigmond `ahead`; `smd update` (dry run) shows the moved components HELD by their pins; `cat /etc/sigmond-appliance/aligned.json`; history lines present; `smd doctor` shows no new findings (the `.pin` files now exist for the moved components and are excluded from git).
- [ ] **Step 5:** report, and note for Plan 2b what restarts the moved components need.

## Self-review notes

- Spec "Decisions for --apply": ahead stays → Task 1 + 4 (`left`, `--allow-rollback`); diverged refuses → Task 1/4; missing installs → Task 4/6; uv.lock reset → Task 4; tag verification, fetch by ref, full-SHA resolution, origin history, origin-vs-catalog → Tasks 2–4; `smd update` hold → Task 7; separate records → Task 5. Bytes measured and `--max-bytes` → Tasks 3, 4, 6. Plan 2b owns radiod, restarts, image files, bring-up re-runs, production verification.
- Type names used consistently: `Release`(+draft/prerelease), `Item`, `classify`, `ApplyError`, `verify_release`, `fetch`, `parse_fetch_bytes`, `resolve`, `in_origin_history`, `is_ancestor`, `dirty_files`, `reset_uvlock`, `checkout`, `changed_files`, `INSTALL_TRIGGERS`, `write_pin`, `normalize_repo`, `Ctx`, `Step`, `apply_plan`, `record`, `ALIGNED_RECORD`.

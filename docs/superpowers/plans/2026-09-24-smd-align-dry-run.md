# smd align — Plan 1: the dry run

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `smd align` on a station's VM says, without changing anything, how far the station sits from the latest blessed appliance release and what an alignment would move.

**Architecture:** A new stdlib-only module `lib/sigmond/align.py` holds a release lookup (GitHub Releases API over `urllib`, the fetcher injected so tests never touch the network), a pure planner, and three small probes (per-component commit distance via the GitHub compare API, image-carried file drift via the release tag's raw files, and the station's recorded release). `bin/smd` gains `cmd_align`, which gathers live state the way `cmd_manifest_restore` does and prints the plan.

**Tech Stack:** Python 3.11+ standard library (`urllib.request`, `json`, `hashlib`), existing sigmond modules (`doctor`, `provenance`, `catalog`), unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-smd-align-design.md` (this repo).

## Scope, and two departures from the spec

This plan builds the spec's §3 steps 1 and 3 only — find the target, plan and report — plus the "host behind" line of §4. It changes nothing on a station. Plan 2 builds `--apply` (§3 steps 2 and 4–8); Plan 3 `pm-align`; Plan 4 the nested-test alignment and the board's level column.

1. **Cost is commits and files, not bytes.** The spec's dry run states a byte cost. No cheap measurement exists before a fetch: GitHub's compare API reports commits and changed files, not sizes, and `git fetch --dry-run` still downloads the pack. So the dry run reports commits and files changed per component (about 1 KB of JSON each), labelled as such. Exact bytes and `--max-bytes` move to Plan 2, where git's transfer counter measures them.
2. **Step 2 (align sigmond first, re-run as the target) belongs to `--apply`.** A dry run needs no re-exec; it plans from the target manifest whatever sigmond the station runs.

## Global Constraints

- Core smd stays stdlib-only (`pyproject.toml` `dependencies = []`). No `requests`.
- Every network fetch goes through an injected `urlopen` callable, following `lib/sigmond/discovery/http_*.py` (`_default_urlopen(url, timeout)`). No test reaches the network.
- The dry run changes nothing: no git fetch, no checkout, no file write. It only reads local state and makes small HTTPS GETs.
- A lookup that fails says so and exits 2; it never guesses a target.
- SHA comparisons use `doctor._sha_equal` (shared prefix, min 4), never string equality — abbreviations vary 7–9 chars.
- The manifest parser is `doctor._parse_manifest_components`; reuse it, never a second parser.
- Commits: develop on main; end each message with the session's attribution lines. Nothing gets pushed by this plan.

## File map

| File | Responsibility |
|---|---|
| `lib/sigmond/align.py` (create) | release lookup, planner, cost / image-file / release-level probes, report formatting |
| `tests/test_align.py` (create) | unit tests for everything in `align.py`, fake `urlopen` |
| `bin/smd` (modify) | `cmd_align`, argparse `align` verb |
| `tests/test_align_cli.py` (create) | CLI wrapper tests via `SourceFileLoader`, all probes patched |

---

### Task 1: release lookup

**Files:** Create `lib/sigmond/align.py`; create `tests/test_align.py`.

**Interfaces — produces:**
- `RELEASES_API = "https://api.github.com/repos/HamSCI/sigmond-appliance/releases"`
- `@dataclass(frozen=True) class Release: tag: str; manifest_text: str; appliance_commit: str | None; components: dict[str, str]`
- `class LookupError_(RuntimeError)` (named with the underscore to avoid shadowing the builtin)
- `fetch_release(tag: str | None = None, urlopen=None, timeout: float = 20.0) -> Release` — `tag=None` means latest.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for sigmond.align — no test touches the network."""
import io
import json
import unittest

from sigmond import align

MANIFEST = """image_version: v3.53
appliance_commit: 4a92fbdbcce497035994e76636689d84f7ffe967
appliance_tag: v3.53

components (live):
    sigmond          daba1f6
    hf-timestd       5c8196d
    ka9q-radio       401992c
    ka9q-python      72ce2a2
    hs-uploader      1111111
    psk-recorder     2222222
    wspr-recorder    3333333
    mag-recorder     4444444
    hamsci-dsp       5555555
    station-web      6666666
    gpsdo-monitor    7777777
"""


def release_json(tag="v3.53", with_manifest=True):
    assets = [{"name": f"sigmond-appliance-{tag}-20260924.sha256",
               "browser_download_url": "https://example/sha"}]
    if with_manifest:
        assets.append({"name": f"sigmond-appliance-{tag}-20260924.manifest.txt",
                       "browser_download_url": "https://example/manifest"})
    return json.dumps({"tag_name": tag, "assets": assets}).encode()


class FakeUrlopen:
    """Answers by URL; records every URL asked for."""
    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def __call__(self, url, timeout=None):
        self.seen.append(url)
        body = self.routes.get(url)
        if body is None:
            raise OSError(f"no route for {url}")
        return io.BytesIO(body)


class FetchReleaseTests(unittest.TestCase):
    def test_latest_release_parses_manifest(self):
        fake = FakeUrlopen({align.RELEASES_API + "/latest": release_json(),
                            "https://example/manifest": MANIFEST.encode()})
        rel = align.fetch_release(urlopen=fake)
        self.assertEqual(rel.tag, "v3.53")
        self.assertEqual(rel.appliance_commit, "4a92fbdbcce497035994e76636689d84f7ffe967")
        self.assertEqual(rel.components["hf-timestd"], "5c8196d")
        self.assertEqual(len(rel.components), 11)

    def test_named_release_uses_the_tags_endpoint(self):
        fake = FakeUrlopen({align.RELEASES_API + "/tags/v3.51": release_json("v3.51"),
                            "https://example/manifest": MANIFEST.encode()})
        align.fetch_release("v3.51", urlopen=fake)
        self.assertIn(align.RELEASES_API + "/tags/v3.51", fake.seen)

    def test_release_without_manifest_asset_refuses(self):
        fake = FakeUrlopen({align.RELEASES_API + "/latest": release_json(with_manifest=False)})
        with self.assertRaises(align.LookupError_) as cm:
            align.fetch_release(urlopen=fake)
        self.assertIn("manifest", str(cm.exception))

    def test_untrustworthy_manifest_refuses(self):
        thin = "components (live):\n    sigmond daba1f6\n"
        fake = FakeUrlopen({align.RELEASES_API + "/latest": release_json(),
                            "https://example/manifest": thin.encode()})
        with self.assertRaises(align.LookupError_):
            align.fetch_release(urlopen=fake)

    def test_network_failure_refuses(self):
        with self.assertRaises(align.LookupError_) as cm:
            align.fetch_release(urlopen=FakeUrlopen({}))
        self.assertIn("could not reach", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd ~/hamsci/repos/sigmond && .venv/bin/python -m pytest tests/test_align.py -v --override-ini addopts=`
Expected: collection ERROR — `cannot import name 'align' from 'sigmond'`.

- [ ] **Step 3: Implement**

Create `lib/sigmond/align.py`:

```python
"""smd align — how far a station sits from the latest blessed appliance release.

Design: docs/superpowers/specs/2026-09-24-smd-align-design.md.  This module
plans; it never changes a station.  Every network read goes through an
injected ``urlopen`` (the lib/sigmond/discovery/http_* idiom), so tests
never reach the network.  Standard library only, like the rest of core smd.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from sigmond.doctor import _parse_manifest_components

RELEASES_API = "https://api.github.com/repos/HamSCI/sigmond-appliance/releases"
_MAX_BODY = 1 << 20          # no reply we read is anywhere near 1 MiB


class LookupError_(RuntimeError):
    """The target could not be established; align must not guess one."""


def _default_urlopen(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": "sigmond-align",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _get(url: str, urlopen, timeout: float) -> bytes:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.read(_MAX_BODY)
    except Exception as exc:  # noqa: BLE001 — every failure means "no answer"
        raise LookupError_(f"could not reach {url}: {exc}") from exc


@dataclass(frozen=True)
class Release:
    tag: str
    manifest_text: str
    appliance_commit: Optional[str]
    components: dict


def _preamble(text: str, key: str) -> Optional[str]:
    for line in text.splitlines():
        if line.startswith(key + ":"):
            return line.split(":", 1)[1].strip() or None
    return None


def fetch_release(tag: Optional[str] = None, urlopen: Optional[Callable] = None,
                  timeout: float = 20.0) -> Release:
    """The blessed release ``tag`` (default: the latest) and its manifest."""
    urlopen = urlopen or _default_urlopen
    url = f"{RELEASES_API}/tags/{tag}" if tag else f"{RELEASES_API}/latest"
    try:
        meta = json.loads(_get(url, urlopen, timeout))
    except ValueError as exc:
        raise LookupError_(f"{url} did not return JSON: {exc}") from exc
    asset = next((a for a in meta.get("assets", [])
                  if a.get("name", "").endswith(".manifest.txt")), None)
    if asset is None:
        raise LookupError_(f"release {meta.get('tag_name')} publishes no manifest asset")
    text = _get(asset["browser_download_url"], urlopen, timeout).decode("utf-8", "replace")
    components = _parse_manifest_components(text)
    if components is None:
        raise LookupError_(f"release {meta.get('tag_name')}: manifest is missing or truncated")
    return Release(tag=meta.get("tag_name") or (tag or "?"), manifest_text=text,
                   appliance_commit=_preamble(text, "appliance_commit"),
                   components=components)
```

- [ ] **Step 4: Run to verify they pass**

Run the Step 2 command. Expected: 5 passed.

- [ ] **Step 5: Mutation check**

Temporarily make `fetch_release` skip the `components is None` check. Re-run. Expected: `test_untrustworthy_manifest_refuses` FAILS. Restore; 5 pass.

- [ ] **Step 6: Commit**

```bash
git add lib/sigmond/align.py tests/test_align.py
git commit -m "align: look up the blessed release and its manifest (no network in tests)"
```

---

### Task 2: the planner

**Files:** Modify `lib/sigmond/align.py`; modify `tests/test_align.py`.

**Interfaces — consumes:** `Release`. **Produces:**
- `@dataclass(frozen=True) class Item: component: str; status: str; live: str | None; target: str | None; note: str = ""` — `status` ∈ `"current"`, `"move"`, `"refuse"`, `"missing"` (in the manifest, no checkout on the station), `"stray"` (checkout the manifest does not name).
- `plan_align(release: Release, live: dict[str, str | None], dirty: dict[str, bool]) -> list[Item]` — pure. `sigmond` sorts first; the rest alphabetical; strays last. A dirty checkout that would move becomes `refuse` with note `"uncommitted changes — commit, stash or discard first"`. A `move` of `ka9q-radio` carries note `"RESTARTS radiod on --apply"`.
- `RADIOD = "ka9q-radio"`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_align.py` above the `if __name__` block:

```python
def rel(**over):
    comps = {"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"}
    comps.update(over)
    return align.Release(tag="v3.53", manifest_text="", appliance_commit=None, components=comps)


class PlanTests(unittest.TestCase):
    def test_all_current(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6aa", "hf-timestd": "5c8196d",
                                        "ka9q-radio": "401992cdecaa"}, {})
        self.assertEqual({i.status for i in plan}, {"current"})

    def test_move_and_order_sigmond_first(self):
        plan = align.plan_align(rel(), {"sigmond": "459bee6", "hf-timestd": "4595c00",
                                        "ka9q-radio": "401992c"}, {})
        self.assertEqual(plan[0].component, "sigmond")
        self.assertEqual([i.status for i in plan[:2]], ["move", "move"])

    def test_dirty_checkout_that_would_move_refuses(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "hf-timestd": "4595c00",
                                        "ka9q-radio": "401992c"}, {"hf-timestd": True})
        item = next(i for i in plan if i.component == "hf-timestd")
        self.assertEqual(item.status, "refuse")
        self.assertIn("uncommitted", item.note)

    def test_dirty_but_current_is_not_refused(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                        "ka9q-radio": "401992c"}, {"hf-timestd": True})
        self.assertEqual(next(i for i in plan if i.component == "hf-timestd").status, "current")

    def test_radiod_move_is_flagged(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                        "ka9q-radio": "deb7bdd"}, {})
        item = next(i for i in plan if i.component == "ka9q-radio")
        self.assertEqual(item.status, "move")
        self.assertIn("RESTARTS radiod", item.note)

    def test_missing_and_stray(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "ka9q-radio": "401992c",
                                        "codar-sounder": "eeeeeee"}, {})
        by = {i.component: i.status for i in plan}
        self.assertEqual(by["hf-timestd"], "missing")
        self.assertEqual(by["codar-sounder"], "stray")
        self.assertEqual(plan[-1].component, "codar-sounder")

    def test_unreadable_head_is_a_refusal_not_a_move(self):
        plan = align.plan_align(rel(), {"sigmond": "daba1f6", "hf-timestd": None,
                                        "ka9q-radio": "401992c"}, {})
        self.assertEqual(next(i for i in plan if i.component == "hf-timestd").status, "refuse")
```

- [ ] **Step 2: Run to verify they fail**

Expected: the 7 PlanTests FAIL with `AttributeError: module 'sigmond.align' has no attribute 'plan_align'`.

- [ ] **Step 3: Implement** — append to `lib/sigmond/align.py`:

```python
from sigmond.doctor import _sha_equal  # noqa: E402  (grouped with the plan code)

RADIOD = "ka9q-radio"
_DIRTY_NOTE = "uncommitted changes — commit, stash or discard first"


@dataclass(frozen=True)
class Item:
    component: str
    status: str            # current | move | refuse | missing | stray
    live: Optional[str]
    target: Optional[str]
    note: str = ""


def plan_align(release: Release, live: dict, dirty: dict) -> list:
    """What aligning to ``release`` would do, component by component. Pure."""
    items = []
    for name, target in release.components.items():
        if name not in live:
            items.append(Item(name, "missing", None, target, "no checkout on this station"))
            continue
        head = live[name]
        if head is None:
            items.append(Item(name, "refuse", None, target, "HEAD unreadable"))
        elif _sha_equal(head, target):
            items.append(Item(name, "current", head, target))
        elif dirty.get(name):
            items.append(Item(name, "refuse", head, target, _DIRTY_NOTE))
        else:
            note = "RESTARTS radiod on --apply" if name == RADIOD else ""
            items.append(Item(name, "move", head, target, note))
    strays = [Item(n, "stray", h, None, "not in the release manifest; left alone")
              for n, h in live.items() if n not in release.components]
    items.sort(key=lambda i: (i.component != "sigmond", i.component))
    return items + sorted(strays, key=lambda i: i.component)
```

Move the `from sigmond.doctor import _sha_equal` into the module's import block at the top (beside `_parse_manifest_components`) and drop the `noqa` comment.

- [ ] **Step 4: Run to verify they pass** — Expected: 12 passed.

- [ ] **Step 5: Mutation check** — temporarily drop the `elif dirty.get(name)` branch. Expected: `test_dirty_checkout_that_would_move_refuses` FAILS. Restore; 12 pass.

- [ ] **Step 6: Commit**

```bash
git add lib/sigmond/align.py tests/test_align.py
git commit -m "align: plan component moves to the release pins (pure; sigmond first, radiod flagged)"
```

---

### Task 3: the three probes

**Files:** Modify `lib/sigmond/align.py`; modify `tests/test_align.py`.

**Interfaces — produces:**
- `github_slug(repo_url: str) -> str | None` — `"https://github.com/ka9q/ka9q-radio"` → `"ka9q/ka9q-radio"`; `.git` suffix and trailing slash tolerated; non-GitHub → `None`.
- `commit_distance(slug: str, base: str, head: str, urlopen=None, timeout=20.0) -> dict` — GET `https://api.github.com/repos/{slug}/compare/{base}...{head}`; returns `{"ahead": int, "behind": int, "files": int}` or `{"error": str}` (never raises).
- `IMAGE_FILES = (("/usr/local/sbin/sigmond-site-timing", "sigmond-site-timing"), ("/usr/local/sbin/sigmond-location-check", "sigmond-location-check"))` — VM path, path in sigmond-appliance at the tag.
- `RAW_BASE = "https://raw.githubusercontent.com/HamSCI/sigmond-appliance"`
- `image_file_drift(tag: str, read_local=None, urlopen=None, timeout=20.0) -> list[dict]` — per file `{"path": str, "status": "current"|"differs"|"absent"|"unknown", "note": str}`; `read_local(path) -> bytes | None` defaults to reading the file (None if missing).
- `recorded_release(path: str = "/etc/sigmond-appliance/version") -> str | None` — first token of the file, or None.

- [ ] **Step 1: Write the failing tests** — append:

```python
import hashlib


class ProbeTests(unittest.TestCase):
    def test_github_slug(self):
        self.assertEqual(align.github_slug("https://github.com/ka9q/ka9q-radio"), "ka9q/ka9q-radio")
        self.assertEqual(align.github_slug("https://github.com/HamSCI/sigmond.git/"), "HamSCI/sigmond")
        self.assertIsNone(align.github_slug("https://gitlab.com/x/y"))

    def test_commit_distance(self):
        url = "https://api.github.com/repos/HamSCI/hf-timestd/compare/4595c00...5c8196d"
        body = json.dumps({"ahead_by": 20, "behind_by": 0,
                           "files": [{}] * 22}).encode()
        got = align.commit_distance("HamSCI/hf-timestd", "4595c00", "5c8196d",
                                    urlopen=FakeUrlopen({url: body}))
        self.assertEqual(got, {"ahead": 20, "behind": 0, "files": 22})

    def test_commit_distance_failure_is_reported_not_raised(self):
        got = align.commit_distance("HamSCI/x", "a", "b", urlopen=FakeUrlopen({}))
        self.assertIn("error", got)

    def test_image_file_drift(self):
        good, stale = b"#!/bin/bash\nnew\n", b"#!/bin/bash\nold\n"
        routes = {f"{align.RAW_BASE}/v3.53/sigmond-site-timing": good,
                  f"{align.RAW_BASE}/v3.53/sigmond-location-check": good}
        local = {"/usr/local/sbin/sigmond-site-timing": stale,
                 "/usr/local/sbin/sigmond-location-check": None}
        got = {d["path"]: d["status"] for d in align.image_file_drift(
            "v3.53", read_local=local.get, urlopen=FakeUrlopen(routes))}
        self.assertEqual(got["/usr/local/sbin/sigmond-site-timing"], "differs")
        self.assertEqual(got["/usr/local/sbin/sigmond-location-check"], "absent")

    def test_image_file_unreachable_is_unknown(self):
        got = align.image_file_drift("v3.53", read_local=lambda p: b"x", urlopen=FakeUrlopen({}))
        self.assertEqual({d["status"] for d in got}, {"unknown"})

    def test_recorded_release(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("v3.36\n")
        try:
            self.assertEqual(align.recorded_release(f.name), "v3.36")
        finally:
            os.unlink(f.name)
        self.assertIsNone(align.recorded_release("/nonexistent/version"))
```

- [ ] **Step 2: Run to verify they fail** — Expected: 6 new tests FAIL with `AttributeError`.

- [ ] **Step 3: Implement** — append:

```python
import hashlib  # noqa: E402  (move to the top import block)
import re        # noqa: E402

RAW_BASE = "https://raw.githubusercontent.com/HamSCI/sigmond-appliance"
IMAGE_FILES = (
    ("/usr/local/sbin/sigmond-site-timing", "sigmond-site-timing"),
    ("/usr/local/sbin/sigmond-location-check", "sigmond-location-check"),
)


def github_slug(repo_url: str) -> Optional[str]:
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", repo_url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else None


def commit_distance(slug: str, base: str, head: str, urlopen: Optional[Callable] = None,
                    timeout: float = 20.0) -> dict:
    url = f"https://api.github.com/repos/{slug}/compare/{base}...{head}"
    try:
        doc = json.loads(_get(url, urlopen or _default_urlopen, timeout))
    except (LookupError_, ValueError) as exc:
        return {"error": str(exc)}
    return {"ahead": int(doc.get("ahead_by", 0)), "behind": int(doc.get("behind_by", 0)),
            "files": len(doc.get("files") or [])}


def _read_local(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def image_file_drift(tag: str, read_local: Optional[Callable] = None,
                     urlopen: Optional[Callable] = None, timeout: float = 20.0) -> list:
    read_local = read_local or _read_local
    out = []
    for vm_path, repo_path in IMAGE_FILES:
        try:
            want = _get(f"{RAW_BASE}/{tag}/{repo_path}", urlopen or _default_urlopen, timeout)
        except LookupError_ as exc:
            out.append({"path": vm_path, "status": "unknown", "note": str(exc)})
            continue
        have = read_local(vm_path)
        if have is None:
            out.append({"path": vm_path, "status": "absent", "note": "not installed"})
        elif hashlib.sha256(have).digest() == hashlib.sha256(want).digest():
            out.append({"path": vm_path, "status": "current", "note": ""})
        else:
            out.append({"path": vm_path, "status": "differs", "note": f"differs from {tag}"})
    return out


def recorded_release(path: str = "/etc/sigmond-appliance/version") -> Optional[str]:
    text = _read_local(path)
    if not text:
        return None
    parts = text.decode("utf-8", "replace").split()
    return parts[0] if parts else None
```

Move `hashlib` and `re` into the top import block and drop the `noqa` comments.

- [ ] **Step 4: Run to verify they pass** — Expected: 18 passed.

- [ ] **Step 5: Mutation check** — temporarily make `image_file_drift` compare lengths instead of digests. Expected: `test_image_file_drift` FAILS (the two fixtures have equal length). Restore; 18 pass.

- [ ] **Step 6: Commit**

```bash
git add lib/sigmond/align.py tests/test_align.py
git commit -m "align: probes — commit distance, image-carried file drift, recorded release"
```

---

### Task 4: `smd align` in the CLI

**Files:** Modify `bin/smd`; create `tests/test_align_cli.py`.

**Interfaces — consumes:** everything above; `doctor.component_checkouts(base)`, `doctor.git_state(path)` (`dirty` key: non-empty list means dirty), `provenance.component_versions(checkouts)`, `catalog.load_catalog()` (`.repo` per entry).
**Produces:** `cmd_align(args) -> int`; argparse verb `align` with `--release TAG`, `--base DIR`, `--no-cost` (skip the per-component compare calls). Exit codes: `0` aligned (every item current or stray, every image file current); `1` alignment available; `2` target could not be established.

Report shape (the tests pin these lines):

```
smd align — AC0G-ND against blessed v3.53 (dry run: nothing changes)
  station recorded release: v3.36
  components:
    sigmond          459bee6 -> daba1f6   move   39 commits, 51 files
    hf-timestd       5c8196d              current
    ka9q-radio       deb7bdd -> 401992c   move   243 commits, 180 files   RESTARTS radiod on --apply
    ...
  image-carried files:
    /usr/local/sbin/sigmond-site-timing      differs from v3.53
  host: recorded v3.36, blessed v3.53 — run pm-align on the Proxmox host (Plan 3)
  summary: 7 to move, 0 refused, 1 missing, 1 stray, 1 file to refresh
  apply: not built yet (Plan 2)
```

- [ ] **Step 1: Write the failing tests** — create `tests/test_align_cli.py`:

```python
"""CLI tests for `smd align` — every probe patched; no network, no git."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import unittest
from pathlib import Path
from unittest import mock

from sigmond import align

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_align", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_align", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()
REL = align.Release(tag="v3.53", manifest_text="", appliance_commit=None,
                    components={"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                "ka9q-radio": "401992c"})


def run(live, dirty=None, files=None, recorded="v3.36", release=REL, **ns):
    args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=True, **ns)
    patches = [
        mock.patch.object(smd, "_align_live_state", return_value=(live, dirty or {})),
        mock.patch("sigmond.align.image_file_drift", return_value=files or [
            {"path": "/usr/local/sbin/sigmond-site-timing", "status": "current", "note": ""}]),
        mock.patch("sigmond.align.recorded_release", return_value=recorded),
    ]
    if isinstance(release, Exception):
        patches.append(mock.patch("sigmond.align.fetch_release", side_effect=release))
    else:
        patches.append(mock.patch("sigmond.align.fetch_release", return_value=release))
    out = io.StringIO()
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        st.enter_context(contextlib.redirect_stdout(out))
        rc = smd.cmd_align(args)
    return rc, out.getvalue()


class AlignCliTests(unittest.TestCase):
    def test_aligned_station_exits_0(self):
        rc, out = run({"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      recorded="v3.53")
        self.assertEqual(rc, 0)
        self.assertIn("against blessed v3.53", out)
        self.assertIn("nothing changes", out)

    def test_moves_exit_1_and_flag_radiod(self):
        rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "deb7bdd"})
        self.assertEqual(rc, 1)
        self.assertIn("459bee6 -> daba1f6", out)
        self.assertIn("RESTARTS radiod", out)
        self.assertIn("run pm-align", out)

    def test_differing_image_file_exits_1(self):
        rc, out = run({"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      files=[{"path": "/usr/local/sbin/sigmond-site-timing",
                              "status": "differs", "note": "differs from v3.53"}],
                      recorded="v3.53")
        self.assertEqual(rc, 1)
        self.assertIn("1 file to refresh", out)

    def test_lookup_failure_exits_2(self):
        rc, out = run({}, release=align.LookupError_("could not reach api.github.com"))
        self.assertEqual(rc, 2)
        self.assertIn("could not reach", out)

    def test_dry_run_cannot_move_anything(self):
        # Structural guard: the dry run has no way to change a station. align.py
        # never imports subprocess, and cmd_align names no git mutation. Adding
        # either on purpose (Plan 2) must come with its own tests.
        import inspect
        src = inspect.getsource(align)
        self.assertNotIn("import subprocess", src)
        self.assertNotIn("subprocess.", src)
        body = inspect.getsource(smd.cmd_align)
        for word in ("checkout", "fetch", "install.sh", "systemctl"):
            self.assertNotIn(word, body)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd ~/hamsci/repos/sigmond && .venv/bin/python -m pytest tests/test_align_cli.py -v --override-ini addopts=`
Expected: 5 FAIL with `AttributeError: module 'smd_under_test_align' has no attribute 'cmd_align'` (or `_align_live_state`).  (`test_dry_run_cannot_move_anything` fails the same way until `cmd_align` exists; its real work is the Step 5 mutation.)

- [ ] **Step 3: Implement** in `bin/smd`, beside `cmd_manifest_restore`:

```python
def _align_live_state(base: str):
    """(live HEADs, dirty flags) for every component checkout under ``base``."""
    from sigmond.doctor import component_checkouts, git_state
    from sigmond.provenance import component_versions
    checkouts = component_checkouts(Path(base))
    live = component_versions(checkouts)
    dirty = {Path(p).name: bool(git_state(p).get('dirty')) for p in checkouts}
    return live, dirty


def cmd_align(args) -> int:
    """`smd align` — how far this station sits from the latest blessed release.

    Dry run only (Plan 1 of docs/superpowers/specs/2026-09-24-smd-align-design.md):
    it reads local state and makes small HTTPS GETs, and changes nothing.
    Exit 0 aligned, 1 alignment available, 2 target could not be established.
    """
    from sigmond import align
    import socket
    base = getattr(args, 'base', None) or '/opt/git/sigmond'
    try:
        rel = align.fetch_release(getattr(args, 'release', None))
    except align.LookupError_ as exc:
        print(f'smd align: cannot establish the target — {exc}')
        return 2
    live, dirty = _align_live_state(base)
    plan = align.plan_align(rel, live, dirty)
    print(f'smd align — {socket.gethostname()} against blessed {rel.tag} '
          f'(dry run: nothing changes)')
    recorded = align.recorded_release()
    print(f'  station recorded release: {recorded or "unknown"}')
    print('  components:')
    catalog = {}
    if not getattr(args, 'no_cost', False):
        try:
            from sigmond.catalog import load_catalog
            catalog = load_catalog()
        except (FileNotFoundError, OSError):
            catalog = {}
    for it in plan:
        if it.status == 'current':
            line = f'{(it.live or "")[:8]:<8}              current'
        elif it.status == 'move':
            line = f'{it.live[:8]} -> {it.target[:8]}   move'
            entry = catalog.get(it.component)
            slug = align.github_slug(getattr(entry, 'repo', '')) if entry else None
            if slug:
                d = align.commit_distance(slug, it.live, it.target)
                line += ('   ' + (f"{d['ahead']} commits, {d['files']} files"
                                  if 'error' not in d else 'distance unknown'))
        else:
            line = f'{it.status}'
        if it.note:
            line += f'   {it.note}'
        print(f'    {it.component:<16} {line}')
    files = align.image_file_drift(rel.tag)
    stale = [f for f in files if f['status'] in ('differs', 'absent')]
    print('  image-carried files:')
    for f in files:
        print(f"    {f['path']:<40} {f['note'] or f['status']}")
    if recorded and recorded != rel.tag:
        print(f'  host: recorded {recorded}, blessed {rel.tag} — run pm-align on the '
              f'Proxmox host (Plan 3)')
    counts = {s: sum(1 for i in plan if i.status == s)
              for s in ('move', 'refuse', 'missing', 'stray')}
    print(f"  summary: {counts['move']} to move, {counts['refuse']} refused, "
          f"{counts['missing']} missing, {counts['stray']} stray, "
          f"{len(stale)} file{'s' if len(stale) != 1 else ''} to refresh")
    print('  apply: not built yet (Plan 2)')
    pending = counts['move'] or counts['refuse'] or counts['missing'] or stale
    return 1 if pending else 0
```

Register the verb beside `update` (bin/smd ~20091):

```python
    p = sub.add_parser('align', help='how far this station sits from the latest blessed '
                                     'appliance release (dry run; changes nothing)')
    p.add_argument('--release', help='compare against this blessed release tag '
                                     '(default: the latest)')
    p.add_argument('--base', help='component checkout root (default /opt/git/sigmond)')
    p.add_argument('--no-cost', action='store_true',
                   help='skip the per-component commit-distance lookups')
```

and dispatch it wherever `update` dispatches to `cmd_update` (follow that exact pattern). Check whether `recorded != rel.tag` fits `recorded_release`'s return (e.g. `v3.36` vs `v3.53`); if the version file on a station carries more than the tag, compare only its first token (already what `recorded_release` returns).

- [ ] **Step 4: Run to verify they pass** — Expected: 5 passed. Then the full suite once: `.venv/bin/python -m pytest -q --override-ini addopts=` — record the count before and after.

- [ ] **Step 5: Mutation checks** — (a) temporarily make `cmd_align` return 0 unconditionally at the end: `test_moves_exit_1_and_flag_radiod` and `test_differing_image_file_exits_1` must FAIL. (b) temporarily add `import subprocess` to `align.py`: `test_dry_run_cannot_move_anything` must FAIL. Restore both.

- [ ] **Step 6: Commit**

```bash
git add bin/smd tests/test_align_cli.py
git commit -m "align: smd align — the dry run (target, plan, commit distance, image files, host level)"
```

---

### Task 5: dry-run it on B4 and ND

Controller task, read-only, no subagent. The dry run changes nothing, but it reaches GitHub from the station, so note it on the claude-bus.

- [ ] **Step 1: Run the new code on each station without touching its checkout.**  The stations' own sigmond lacks `align`, and this plan pushes nothing, so ship the devbox's copy to a scratch directory and run it from there (core smd is stdlib-only, so no venv is needed):

```bash
cd ~/hamsci/repos/sigmond && tar czf /tmp/align-try.tgz bin/smd lib/sigmond etc/catalog.toml
for h in b4 ac0g-nd; do
  B64=$(base64 -w0 /tmp/align-try.tgz)
  ~/hamsci/ops/bin/fleet-ssh $h "d=\$(mktemp -d); echo $B64 | base64 -d | tar xzf - -C \$d; \
    SIGMOND_NO_VENV_REEXEC=1 PYTHONPATH=\$d/lib python3 \$d/bin/smd align; echo rc=\$?; rm -rf \$d" \
    | tee /tmp/align-$h.txt
done
```

If the tarball proves too large for one command line, send it with `scp` through the same reach instead; the run stays identical.  Record both outputs.
- [ ] **Step 2:** Compare the dry run against what we know: B4 should show sigmond and the appliance files near-current; ND should show roughly 39 sigmond commits, hf-timestd current (5c8196d), ka9q-radio `deb7bdd -> 401992c` with the radiod flag, and "host: recorded v3.36". Any mismatch is a finding for Plan 2.
- [ ] **Step 3:** Post both outputs to the bus, and bring them to Michael as the input for Plan 2.

## Self-review notes

- Spec coverage: §3 step 1 → Task 1; step 3 (plan, cost) → Tasks 2–4, with the byte-cost departure stated above; §4's "host behind" line → Task 3 (`recorded_release`) and Task 4; §6 unit tests → every task. Steps 2 and 4–8, `pm-align`, the nested alignment and the board column belong to Plans 2–4.
- Type consistency: `Release`, `Item`, `plan_align`, `commit_distance`, `image_file_drift`, `recorded_release`, `github_slug`, `LookupError_`, `RELEASES_API`, `RAW_BASE`, `IMAGE_FILES`, `RADIOD` — each defined once, used with the same signature in later tasks and the CLI tests.

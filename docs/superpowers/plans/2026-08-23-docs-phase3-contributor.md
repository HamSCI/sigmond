# Documentation Program — Phase 3 (contributor guide + upkeep) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A developer new to the suite can, from `CONTRIBUTING.md` + `sigmond/docs/contributor/`, set up a dev environment, find which module implements any `smd` verb, understand how a change reaches a station (tag → `smd update` / golden image), author a conformant client, and keep the docs true — with that last part enforced by CI (`docs-check`) and a PR checklist in every repo.

**Architecture:** Six contributor pages under `docs/contributor/` (README, orchestration ★ with the CI-checked CLI table, appliance-boundary ★, dev-setup ★, client-authoring, docs-conventions already ★), meteor-scatter's six client docs truthed to MSK144 reality, CONTRIBUTING §14 "Docs travel with behavior", `.github/PULL_REQUEST_TEMPLATE.md` in nine repos, and a reusable GitHub Actions workflow `docs-check` in sigmond (called from every repo) that runs the stdlib link checker plus a new `Verified against` freshness check (warn-only) and, in sigmond, the CLI-table test. Verified by a fresh-context contributor walk-through.

**Tech Stack:** Markdown; Python 3.11 stdlib (`scripts/docs-freshness.py`, `tests/test_docs_cli_table.py`); GitHub Actions (`workflow_call`); pytest.

**Spec:** `sigmond/docs/superpowers/specs/2026-08-23-documentation-program-design.md` — §5 (contributor pages + archive policy), §7 (upkeep: CONTRIBUTING §14, PR template, docs-check CI), §10 Phase 3 "done when", §11 out of scope.

## Global Constraints

- **Docs + docs tooling + CI/PR-template files only.** Allowed non-markdown: `scripts/docs-freshness.py`, `tests/test_docs_cli_table.py`, `tests/test_docs_freshness.py`, `.github/workflows/docs-check.yml` (sigmond, reusable) and a 12-line caller `.github/workflows/docs-check.yml` in each other repo, `.github/PULL_REQUEST_TEMPLATE.md` in nine repos. No product code (`bin/smd`, `lib/`, clients' `src/`) changes. Software gaps → `docs/contributor/docs-gap-ledger.md` (rows 1–51 exist; next 52).
- **Repos:** `/home/mjh/hamsci/repos/<repo>/`, all on `main`, commit directly, **no push** (owner pushes; CI is observed after the owner's push — see Task 7 Step 6). Trailer, verbatim, on every commit:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012XQRNXmBj87SxR5H5UxZqt
  ```
- **Header block** (4 `>` lines under the H1; `**Audience:** contributor`; `**Verified against:** sigmond <sha> on <date> — code`; `**Canonical for:**`) per `docs/contributor/docs-conventions.md` §3; ★ once per topic in `docs/INDEX.md` §3; forward references to pages not yet written as plain text `x.md *(being written)*` (the checker must stay green at every commit); Task 5 converts, Task 9 asserts none remain.
- **Checks before every commit:** `cd /home/mjh/hamsci/repos/sigmond && python3 scripts/docs-linkcheck.py docs README.md CONTRIBUTING.md CLAUDE.md` → exit 0; `.venv/bin/pytest tests/test_docs_links.py -q` (and the new doc tests once they exist) → pass; `.venv/bin/pytest -q | tail -1` before the final commit of each task.
- **Live reads** (read-only) via `/tmp/claude-1000/-home-mjh-hamsci/c34744cd-f838-4226-981e-840c532862e2/scratchpad/fleet-ro.sh b4|dasi002 '<cmd>'` only if a page needs a live fact; never mutate.
- **hf-timestd**: out of scope for restructuring (spec §11) — but Task 7 MAY fix its five pre-existing broken relative links (docs-only, one commit) so the reusable workflow is not red there on day one.
- **The CLI table contract** (Task 1 ↔ Task 7): `docs/contributor/orchestration.md` contains one markdown table whose first column cells are backticked verb strings — top-level verbs as `` `status` ``, admin subverbs as `` `admin diag` ``; the test parses `bin/smd --help` and `bin/smd admin --help` `{a,b,…}` groups (run with `PYTHONPATH=lib python3 bin/smd …`, `SIGMOND_NO_VENV_REEXEC=1`) and asserts set equality both ways (top-level verbs; admin subverbs). Deprecated top-level verbs that `--help` still lists must appear in the table too (mark them "deprecated → replacement").

---

## File map

**Create (sigmond):** `docs/contributor/orchestration.md` ★, `appliance-boundary.md` ★, `dev-setup.md` ★, `client-authoring.md`, `README.md` (replaces stub); `scripts/docs-freshness.py`; `tests/test_docs_cli_table.py`, `tests/test_docs_freshness.py`; `.github/workflows/docs-check.yml`; `.github/PULL_REQUEST_TEMPLATE.md`.
**Modify (sigmond):** `CONTRIBUTING.md` (+§14), `README.md` (§Development → pointer), `docs/contributor/docs-conventions.md` (§8/§9 updates), `docs/INDEX.md`, `docs/README.md` (contributor door), `docs/contributor/docs-gap-ledger.md`.
**Other repos:** meteor-scatter (`docs/{ARCHITECTURE,CONFIG,INSTALL,OPERATIONS,SIGMOND-CONTRACT}.md` rewritten, `README.md` truthed, `docs/INDEX.md` warning removed); `.github/PULL_REQUEST_TEMPLATE.md` + `.github/workflows/docs-check.yml` in sigmond-appliance, wspr-recorder, psk-recorder, meteor-scatter, mag-recorder, gpsdo-monitor, hs-uploader, hamsci-dsp, hf-timestd (+ hf-timestd's five link fixes).

---

### Task 1: `docs/contributor/orchestration.md` ★ + the CLI-table test

**Files:** Create `docs/contributor/orchestration.md`, `tests/test_docs_cli_table.py`; modify `docs/INDEX.md` §3.
**Interfaces:** Produces the CLI table contract (Global Constraints) consumed by Task 7's CI.

Sources: `CLAUDE.md` (Architecture layers 1–13; CPU pinning; sink selection; catalog layering; fleet upgrade pattern), `README.md` §Architecture/§Command reference (l.216-269)/§How it works (l.435-456), `bin/smd --help` + `bin/smd admin --help` (verb groups; the "verbs (grouped by who uses them — see docs/CLI-V2-SPEC.md)" text), `docs/CLI-V2-SPEC.md`, `lib/sigmond/` module list (`ls lib/sigmond`), `docs/MULTI-INSTANCE-ARCHITECTURE.md`, `docs/RADIOD-IDENTIFICATION.md`, `CONTRIBUTING.md` §3 (update orientations) §10 (heartbeat/board), `docs/PSWS-HEARTBEAT-SPEC.md`, `etc/catalog.toml`, `/etc/sigmond/*` paths (`lib/sigmond/paths.py`). For verb → implementation: `grep -n "^def cmd_" bin/smd` (73 functions) and the dispatch (`grep -n "'<verb>'" bin/smd`); name the function and the `lib/sigmond/<module>.py` it leans on.

- [ ] **Step 1: Write the failing test** `tests/test_docs_cli_table.py`:
  ```python
  """docs/contributor/orchestration.md's CLI table must match `smd --help`.

  Parses the `{a,b,c}` verb group from `smd --help` and `smd admin --help`
  and asserts the table lists every verb (and nothing that is not a verb).
  Runs bin/smd from the checkout (PYTHONPATH=lib, no venv re-exec).
  """
  import os, re, subprocess, sys
  from pathlib import Path

  REPO = Path(__file__).resolve().parents[1]
  PAGE = REPO / "docs" / "contributor" / "orchestration.md"

  def _help(*args):
      env = dict(os.environ, PYTHONPATH=str(REPO / "lib"), SIGMOND_NO_VENV_REEXEC="1")
      out = subprocess.run([sys.executable, str(REPO / "bin" / "smd"), *args, "--help"],
                           capture_output=True, text=True, env=env, timeout=60).stdout
      m = re.search(r"\{([a-z0-9,\-]+)\}", out)
      assert m, f"no {{verb}} group in `smd {' '.join(args)} --help` output:\n{out[:400]}"
      return set(m.group(1).split(","))

  def _table_cells():
      """First-column backticked cells of the CLI table (rows like `| `status` | …`)."""
      text = PAGE.read_text()
      cells = re.findall(r"^\|\s*`([a-z][a-z0-9\- ]*)`\s*\|", text, flags=re.M)
      return set(cells)

  def test_page_exists():
      assert PAGE.exists()

  def test_top_level_verbs_match():
      verbs = _help()
      cells = {c for c in _table_cells() if " " not in c}
      assert verbs == cells, f"missing from table: {verbs - cells}; not a verb: {cells - verbs}"

  def test_admin_subverbs_match():
      subs = {f"admin {s}" for s in _help("admin")}
      cells = {c for c in _table_cells() if c.startswith("admin ")}
      assert subs == cells, f"missing from table: {subs - cells}; not a subverb: {cells - subs}"
  ```
- [ ] **Step 2:** `.venv/bin/pytest tests/test_docs_cli_table.py -v` → FAIL (page missing). Also confirm the `_help()` parse works: `PYTHONPATH=lib SIGMOND_NO_VENV_REEXEC=1 python3 bin/smd --help | head -3` shows the `{…}` group.
- [ ] **Step 3: Write the page** (≤ 300 lines):
  ```
  # How sigmond works — orchestration in one page
  (header; Canonical for: sigmond's orchestration model and the smd verb→module map)
  ## The shape (one paragraph + the architecture.png link)
  ## Layers (the 13 from CLAUDE.md, one line each, each naming its module)
  ## Production paths (/opt/git/sigmond/<name>, /usr/local/bin/smd symlink, /etc/sigmond/{topology,coordination,site-profile,catalog}.toml + coordination.env, /var/lib/sigmond/{sink.db,upload-wake.sock,lifecycle.lock}, /var/log/sigmond, /etc/<client>/, /var/lib/<client>/)
  ## How a client is discovered and enabled (deploy.toml → discovery layer → catalog overlay → topology enabled flag; install implies enable; core vs discretionary profiles)
  ## The verb map (THE TABLE — every top-level verb and every `admin` subverb; columns: verb | who/when | what it does | implementation (cmd_* + lib module) | mutates?)
  ## Updates (the two orientations, canary, `smd update` plan/apply, `smd fleet update`; version provenance — link appliance-boundary.md *(being written)*)
  ## Heartbeat and the board (5-min envelope, what's in it, where the admin sees it)
  ## Where to read next (CLIENT-CONTRACT ★, MULTI-INSTANCE, RADIOD-IDENTIFICATION, PRODUCER-THREAT-MODEL, PACKET-LOSS, CLI-V2-SPEC, dev-setup.md *(being written)*)
  ```
  Build the table FROM `--help` output (paste the parsed lists into the page as you go), one row per verb/subverb; implementation column from `bin/smd` (`cmd_<x>`) and the lib module it calls.
- [ ] **Step 4:** `.venv/bin/pytest tests/test_docs_cli_table.py -v` → 3 passed; link checker; INDEX §3 row (★); full suite `tail -1`.
- [ ] **Step 5:** commit `docs(contributor): orchestration page + CLI-table test`.

---

### Task 2: `docs/contributor/appliance-boundary.md` ★

**Files:** Create the page; modify `docs/INDEX.md`.
Sources: `/home/mjh/hamsci/repos/sigmond-appliance/{README.md,INSTALL.md,docs/RELEASE.md (the four rungs: Built/Tested/Blessed/Rolled + three rules), build-golden-vm.sh, complete-profile.sh, provision.sh, provision-components.sh, build-usb-v3.sh, firstboot-v3.sh, bless-release.sh, test-nested-v3.sh, test-update-v3.sh}` (read the headers/usage of each script), `scripts/proxmox/sigmond-wizard.sh` (what the wizard asks/sets), `lib/sigmond/capture_prep.py` + `smd admin capture-prep/personalize/readiness/manifest --help`, `CONTRIBUTING.md` §3 §5 §6 (pins, releases), `docs/operator/day-2.md` §Updates (image file is install-time only; `smd version`), ops memory leads (re-verify): `reference_smd_version_provenance.md`, `project_v334_image.md`.
- [ ] **Step 1: Write** — sections: The three layers (what the golden VM bakes: repos at pins, venvs, wisdom, units; what `firstboot`/`install.sh` does on the station: personalize, render configs, enable; what the wizard sets: designator, grid, RAC, keys); Version provenance (`/etc/sigmond-appliance/version` = image lineage; `smd version` = live SHAs; the blessed manifest `smd admin manifest`); How a change reaches a station (commit → release tag/pin → `smd update` pull on the station (canary first) — OR → next golden build → new image; which changes need which path: a client bug fix vs a new native build vs a wizard change); The bless ladder (link RELEASE.md's four rungs; `bless-release.sh`; the "three rules that have cost real time"); Testing an image (nested rig `test-nested-v3.sh`, `test-update-v3.sh`; build host B3 — say what is in the docs, nothing from memory); Where the appliance and sigmond disagree today (INSTALL.md vs sigmond docs — the ones Phase 1 fixed + any left; ledger rows).
- [ ] **Step 2:** checks; INDEX row ★; commit `docs(contributor): appliance ↔ sigmond boundary`.

---

### Task 3: `docs/contributor/dev-setup.md` ★ (+ README §Development → pointer)

**Files:** Create the page; modify `README.md` §Development (l.457-510 → 6-line pointer to the page keeping the `uv sync` one-liner), `docs/INDEX.md`.
Sources: `README.md` §Development, `scripts/dev-setup.sh`, `pyproject.toml` (extras, testpaths), `tests/conftest.py` (banner), `.github/workflows/test.yml` (what CI runs), `CONTRIBUTING.md` §1 §4 §7 §11 (graphify), `CLAUDE.md` §Developer commands + §Tests, `docs/native-binaries.md`, `sigmond-appliance/README.md` (nested rig) — link, don't restate.
- [ ] **Step 1: Write** — Prereqs; Clone layout (sibling checkouts under one dir; `[tool.uv.sources]` editable siblings); Dev venv (`uv sync --extra tui --extra dev` / `scripts/dev-setup.sh`); Running `smd` from the tree (`PYTHONPATH=lib ./bin/smd …`, `SIGMOND_NO_VENV_REEXEC=1`); Tests (`.venv/bin/pytest`; what CI runs; the docs tests: `test_docs_links`, `test_docs_cli_table`, `test_docs_freshness` *(being written)*); Docs checks (`scripts/docs-linkcheck.py`, `scripts/docs-freshness.py` *(being written)*); graphify (CONTRIBUTING §11 — link); Building native pieces (`docs/native-binaries.md` — link); The nested appliance rig and the build host (link appliance-boundary + sigmond-appliance README); Shell/tooling traps (CONTRIBUTING §7 — link).
- [ ] **Step 2:** README pointer; checks; INDEX row ★; commit `docs(contributor): dev setup; README §Development → pointer`.

---

### Task 4: meteor-scatter docs truthing + `docs/contributor/client-authoring.md`

**Files (meteor-scatter repo):** rewrite `docs/ARCHITECTURE.md`, `docs/CONFIG.md`, `docs/INSTALL.md`, `docs/OPERATIONS.md`, `docs/SIGMOND-CONTRACT.md`; truth `README.md` (it still says FT4/FT8); remove the Phase-0 ⚠ lines + INDEX warning row; `docs/INDEX.md` Verified bump. **Files (sigmond):** create `docs/contributor/client-authoring.md`; `docs/INDEX.md`.
Sources (meteor): `meteor-scatter/CLAUDE.md` (what it is: MSK144 via `jt9 --msk144`, sink `msk144.spots`, deposit mode, callhash, naming/rename, service user `meteorscat`, paths), `src/meteor_scatter/{cli,contract,config,configurator,core/*}.py`, `config/meteor-scatter-config.toml.template` ([station] [paths] [[radiod]] [radiod.msk144] keys), `systemd/meteor-scatter@.service`, `deploy.toml`, `docs/REQUIREMENTS.md` (accurate), `tests/`; for the shape copy psk-recorder's six docs (the true template for this client family) and the sigmond `CLIENT-CONTRACT.md` sections each SIGMOND-CONTRACT.md maps.
- [ ] **Step 1 (meteor):** rewrite each file to describe meteor-scatter: ARCHITECTURE (ReceiverManager → per-radiod streams → 15 s MSK144 slots (`MSK144_TR_PERIOD_SEC` etc. from the code) → wav → `jt9 --msk144 -Y` → ch_tailer + callhash → sink `msk144.spots` (mode="msk144") → hs-uploader deposit; no in-process PSKReporter upload unless `METEOR_SCATTER_DELIVERY_MODE=direct`), CONFIG (every key in the template with meaning/default — read `config.py`), INSTALL (standalone: `install.sh`/uv; under sigmond: `smd install meteor-scatter`; paths; user `meteorscat`), OPERATIONS (units `meteor-scatter@<reporter>.service`, logs `/var/log/meteor-scatter/*-msk144.log`, `smd watch meteor`, spool `/msk144`, health signs, restart cost = bounces radiod (cite sigmond troubleshooting table)), SIGMOND-CONTRACT (section-by-section map to CLIENT-CONTRACT v0.8 using `contract.py` as truth). Header blocks; `Verified against: meteor-scatter <sha> on <date> — code`. Remove the five ⚠ stale lines and the INDEX ⚠ row; README: replace FT4/FT8 text. Checker: `python3 /home/mjh/hamsci/repos/sigmond/scripts/docs-linkcheck.py /home/mjh/hamsci/repos/meteor-scatter/docs /home/mjh/hamsci/repos/meteor-scatter/README.md` → 0. Commit in meteor-scatter.
- [ ] **Step 2 (sigmond):** `client-authoring.md` — pointer + rule page: the path (scientist/becoming-a-client.md for the bridge; ADD-A-CLIENT mechanics; CLIENT-CONTRACT norm; REQUIREMENTS-TEMPLATE; the six-file docs skeleton ARCHITECTURE/CONFIG/INSTALL/OPERATIONS/REQUIREMENTS/SIGMOND-CONTRACT; `docs/scientist/skeleton/`); the rule "a client's `docs/` must be true for THAT client" with meteor-scatter's history as the example; how to register a new client's PR template + docs-check (Task 6/7 *(being written)*); `AFFINITY_UNITS` in `lib/sigmond/cpu.py` for decoders (CLAUDE.md). Checks; INDEX row; commit.

---

### Task 5: `docs/contributor/README.md` (replace stub), CONTRIBUTING §14, conventions §8/§9, front door, link conversions

**Files:** `docs/contributor/README.md`, `CONTRIBUTING.md` (+§14 after §13), `docs/contributor/docs-conventions.md` (§8 now points at §14 as in force; §9 lists the new checks *(being written)* as plain text until Task 7), `docs/README.md` (contributor door: drop "(Phase 3 — until then …)"), `docs/INDEX.md`; convert `*(being written)*` refs created by Tasks 1–4 EXCEPT those naming Task-7 artefacts (`docs-freshness`, `docs-check`, `test_docs_freshness`).
- [ ] **Step 1:** README: reading order with time estimates (CONTRIBUTING → orchestration → architecture diagram → CLIENT-CONTRACT → one client's six-file docs → appliance-boundary → dev-setup → client-authoring → docs-conventions); where the ledger is; how to run the docs checks; "what not to do in a deploy tree" (CONTRIBUTING §8 link). ≤ 80 lines.
- [ ] **Step 2:** CONTRIBUTING §14 "Docs travel with behavior": any PR touching a CLI surface, config key, systemd unit, file path, wizard prompt, or observable behavior touches the canonical page (★ in `docs/INDEX.md`) or states "no doc impact"; bump the page's `Verified against:`; the PR template checkboxes; the docs checks that CI runs (`docs-check`); link docs-conventions §2–§4.
- [ ] **Step 3:** front door; INDEX §3 (README ★ at top; all six contributor pages); conversions; checks; commit `docs(contributor): guide front page; CONTRIBUTING §14 docs travel with behavior`.

---

### Task 6: PR templates in nine repos (batched)

**Files:** `.github/PULL_REQUEST_TEMPLATE.md` in sigmond, sigmond-appliance, wspr-recorder, psk-recorder, meteor-scatter, mag-recorder, gpsdo-monitor, hs-uploader, hamsci-dsp, hf-timestd (ten incl. hf-timestd — a client; hamsci-dsp is a library but gets the same template for consistency).
- [ ] **Step 1:** identical template (≤ 25 lines):
  ```markdown
  ## What changed / why
  <!-- observed → changed → verified (CONTRIBUTING §12) -->

  ## Reach
  - [ ] touches a pin, a systemd unit list, or an install script (say so — these reach every field unit)

  ## Docs travel with behavior (sigmond CONTRIBUTING §14)
  - [ ] canonical page(s) updated: <!-- path(s) --> and their `Verified against:` bumped
  - [ ] **or** no doc impact (CLI surface, config keys, units, paths, wizard prompts, observable behavior unchanged)
  - [ ] `docs-check` is green (link check; `Verified against` freshness)
  ```
  (Link §14 by GitHub URL `https://github.com/HamSCI/sigmond/blob/main/CONTRIBUTING.md#14-docs-travel-with-behavior` — verify the slug after Task 5.)
- [ ] **Step 2:** one commit per repo `chore: PR template — docs travel with behavior checklist`; trailers.

---

### Task 7: `docs-check` CI (reusable) + `docs-freshness.py` + caller workflows (+ hf-timestd link fixes)

**Files (sigmond):** `scripts/docs-freshness.py`, `tests/test_docs_freshness.py`, `.github/workflows/docs-check.yml`; modify `docs/contributor/docs-conventions.md` §9 (the commands), `docs/contributor/dev-setup.md` (link), `CONTRIBUTING.md` §14 (name the workflow). **Files (other nine repos):** `.github/workflows/docs-check.yml` caller. **hf-timestd:** fix its five broken relative links (`docs/design/TIMING_AUTHORITY_ARCHITECTURE.md:15,646,916,949` → `/timing-validation`; `README.md:240` → `docs/SERVICES.md`) — retarget to the right existing file or plain text.

- [ ] **Step 1: Write the failing test** `tests/test_docs_freshness.py`:
  ```python
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
  ```
- [ ] **Step 2:** run → FAIL (script missing).
- [ ] **Step 3: Write `scripts/docs-freshness.py`** (stdlib): `HEADER_RE = re.compile(r"\*\*Verified against:\*\*\s+(?P<repo>[\w\-]+)\s+(?P<sha>[0-9a-f]{7,40})")`; `stale_pages(repo_root, paths) -> list[Stale]` where `Stale = namedtuple("Stale","path named_sha last_content_sha")`: for each `*.md` (skip dirs `.venv venv node_modules graphify-out .git superpowers archive`), read the header; skip if no match or `n/a`; find the last CONTENT commit: `git log --format=%h -- <file>` newest→oldest; for each commit `c`, `git show c -- file` and take the diff hunk lines starting with `+`/`-` (not `+++`/`---`); drop lines containing `Verified against:`; if any remain → `c` is the last content edit → break; stale iff `named_sha != last_content_sha` and `git merge-base --is-ancestor named_sha last_content_sha` returns 0 (named is a proper ancestor); unknown named sha (not in repo) → report as stale with reason "unknown sha". CLI: `docs-freshness.py [--strict] PATH...` prints `file: Verified against <sha> but last content edit <sha2>` lines; exit 1 only with `--strict` and ≥1 stale; otherwise exit 0 (warn-only — spec §7: "staleness is visible, not enforced"). Docstring documents this.
- [ ] **Step 4:** run tests → 4 pass; run on the real tree and report the list (expected: few or none after the Phase-2 sweep; any stale page → bump in this task and note it).
- [ ] **Step 5: The reusable workflow** `.github/workflows/docs-check.yml`:
  ```yaml
  # docs-check — link check + Verified-against freshness (warn) for any HamSCI repo.
  # Reusable: other repos call it with `uses: HamSCI/sigmond/.github/workflows/docs-check.yml@main`.
  # In sigmond itself it also runs the CLI-table and freshness pytest.
  name: docs-check
  on:
    workflow_call:
      inputs:
        paths: { type: string, required: false, default: "docs README.md" }
    push: { branches: [main] }
    pull_request: { branches: [main] }
  jobs:
    docs:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v6
          with: { fetch-depth: 0 }                       # freshness needs history
        - uses: actions/checkout@v6                      # the checker lives in sigmond
          with: { repository: HamSCI/sigmond, path: .sigmond-tools, fetch-depth: 1 }
        - uses: actions/setup-python@v6
          with: { python-version: "3.11" }
        - name: Link check (relative links + anchors; external links skipped)
          run: python3 .sigmond-tools/scripts/docs-linkcheck.py ${{ inputs.paths || 'docs README.md' }}
        - name: Verified-against freshness (warn-only)
          run: python3 .sigmond-tools/scripts/docs-freshness.py ${{ inputs.paths || 'docs README.md' }} || true
        - name: sigmond-only doc tests
          if: github.repository == 'HamSCI/sigmond'
          run: |
            pip install pytest
            PYTHONPATH=lib python -m pytest tests/test_docs_links.py tests/test_docs_cli_table.py tests/test_docs_freshness.py -q
  ```
  Note: in a caller repo `.sigmond-tools` is the second checkout; in sigmond itself the first checkout already has the scripts — the `.sigmond-tools` checkout is harmless. Caller workflow (each other repo), `.github/workflows/docs-check.yml`:
  ```yaml
  name: docs-check
  on: { push: { branches: [main] }, pull_request: { branches: [main] } }
  jobs:
    docs:
      uses: HamSCI/sigmond/.github/workflows/docs-check.yml@main
      with: { paths: "docs README.md" }
  ```
  (hf-timestd: `paths: "docs README.md INSTALLATION.md"`; sigmond-appliance: `"docs README.md INSTALL.md"`.) Validate locally: `python3 -c "import yaml,sys;[yaml.safe_load(open(p)) for p in sys.argv[1:]]" <all workflow files>` (install pyyaml into .venv if absent) and run the same three commands the workflow runs in each repo from the devbox (`python3 /home/mjh/hamsci/repos/sigmond/scripts/docs-linkcheck.py docs README.md` from the repo root — must exit 0 in all ten repos; fix hf-timestd's five links first).
- [ ] **Step 6 (post-push observation, owner-gated):** after the owner pushes, `gh run list -R HamSCI/sigmond --workflow docs-check -L 3` and one client repo; if red, fix forward in a follow-up commit. Record in the ledger that this step awaits the push.
- [ ] **Step 7:** docs-conventions §9 + dev-setup + CONTRIBUTING §14 name the workflow and the two scripts; convert their `*(being written)*` refs; checks; commits (sigmond; hf-timestd link fixes; nine caller repos — batch commits `ci: docs-check caller workflow`).

---

### Task 8: Contributor walk-through + fixes

- [ ] **Step 1: Dispatch a fresh-context subagent** with ONLY: "You are a developer new to HamSCI with Python and Linux skills. Your only docs: /home/mjh/hamsci/repos/sigmond/CONTRIBUTING.md and /home/mjh/hamsci/repos/sigmond/docs/contributor/ (follow links; HamSCI/<repo> GitHub links → /home/mjh/hamsci/repos/<repo>). Do not read code except where a page tells you to. Tasks: (1) set up the dev venv per dev-setup.md in a COPY of the sigmond checkout (`cp -r /home/mjh/hamsci/repos/sigmond /tmp/.../sigmond-copy`; do not touch the real checkout) and run the test suite; (2) answer from the docs: which module/function implements `smd doctor`? `smd component update`? what does `smd update` do on a station and who decides? (3) describe how a one-line fix in wspr-recorder reaches AC0G/B4 (both paths) and how a wizard change reaches a station; (4) following client-authoring.md, list the seven things a new client must ship and run the skeleton's verbs; (5) run the docs checks the docs tell you to run and interpret the output; (6) list every guess/failed command/undefined term/contradiction; rate BLOCKER/CONFUSING/NIT. Write `/tmp/claude-1000/-home-mjh-hamsci/c34744cd-f838-4226-981e-840c532862e2/scratchpad/contributor-walkthrough.md`."
- [ ] **Step 2:** triage → doc fixes / ledger rows / by-design sentences; apply; checks; commit `docs(contributor): walk-through fixes`.

---

### Task 9: Phase 3 close-out
- [ ] `docs/INDEX.md` §3 lists all six contributor pages (README ★, orchestration ★, appliance-boundary ★, dev-setup ★, client-authoring, docs-conventions ★, docs-gap-ledger) once each; `docs/README.md` contributor door final (no "Phase 3"); `grep -rn '(being written)' docs` → nothing; `scripts/docs-freshness.py docs README.md CONTRIBUTING.md` → list (bump what it names); `.venv/bin/pytest -q` green; checker exit 0; `graphify update /home/mjh/hamsci/repos`; ledger note "Phase 3 done <date>; rows 1–N ready (Phase 4)"; unpushed status across the ten repos; commit `docs: phase 3 close-out`; do not push.

## Self-review notes
- Spec §5 pages → T1 (orchestration), T2 (appliance-boundary), T3 (dev-setup), T4 (client-authoring + meteor truthing), T5 (README), conventions already exist (T5 updates §8/§9); §7 → T5 (§14), T6 (PR templates), T7 (docs-check CI + the CLI-table test from T1); §10 Phase 3 "done when: CI green in every repo; smd --help ⇔ CLI table" → T7 Step 6 is owner-gated (push) and recorded as such; §11 → hf-timestd touched only for five link fixes (ruled).
- Deviation from spec §7 recorded: the CI uses our stdlib `docs-linkcheck.py` instead of `lychee` (no binary to install; same coverage for relative links/anchors; external links already skipped) and adds the freshness check the Phase-2 final reviewer recommended; freshness is warn-only per spec ("visible, not enforced").
- No placeholders: test code and workflow YAML are given in full; the freshness algorithm is specified precisely (content edit = any diff line other than the Verified line).

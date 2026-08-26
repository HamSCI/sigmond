# Docs conformance — making the documentation surface a contract obligation

**Status:** approved design, 2026-08-26
**Audience:** contributors
**Owner:** mjh (AC0G)
**Why this exists:** `hamsci-physics` and `ka9q-python` carry no `docs-check`
CI, no `docs/INDEX.md`, and (for hamsci-physics) no `docs/REQUIREMENTS.md` or
`REQUIREMENTS-INDEX.md` row. They were not skipped deliberately — the
2026-08-23 documentation program established the standard as *convention plus
CI in whichever repos happened to get it*, and `CLIENT-CONTRACT.md` v0.8
declares no documentation obligation at all across its 2686 lines. Nothing
names the requirement, so nothing notices its absence. This spec closes that
by putting the docs surface in the contract and giving it four checks that
catch different failures.

## 1. Measured starting state

Taken 2026-08-26 across the 12 checkouts in `/home/mjh/hamsci/repos`.

| Repo | `docs-check.yml` | `docs/INDEX.md` | `docs/REQUIREMENTS.md` | index row |
|---|---|---|---|---|
| sigmond, hf-timestd, hamsci-dsp, hs-uploader, gpsdo-monitor, mag-recorder, meteor-scatter, psk-recorder, wspr-recorder | ✅ | ✅ | ✅ | ✅ |
| sigmond-appliance | ✅ | ✅ | ❌ | ❌ (not a bound component today — see §7) |
| **ka9q-python** | ❌ | ❌ | ✅ | ✅ |
| **hamsci-physics** | ❌ | ❌ | ❌ | ❌ |

`Verified against:` header census — **54 of 204 live pages carry one**
(archive/ and superpowers/ excluded, matching the checkers):

| Page class | Carries header |
|---|---|
| `docs/INDEX.md` | 9 of the 10 that exist (hf-timestd's lacks it) |
| `docs/REQUIREMENTS.md` | **0 of 11** |
| root `README.md` | **0 of 12** |
| all other live pages | 45 of 171 |

Two consequences drive the whole design:

- **A self-check cannot detect its own absence.** Both target repos pass
  `docs-linkcheck.py` (0 broken) and `docs-freshness.py` (0 stale) today. That
  is a vacuous pass: freshness skips any page with no header, and neither repo
  has one. Dropping in `docs-check.yml` alone buys a green badge that checks
  almost nothing. Absence has to be detected from a roster, not from the repo.
- **A per-page header MUST is unlandable.** It would red all 12 repos and
  commit us to a 150-page wave. It is also not what the documentation program
  asked for: `docs-conventions.md` scopes the header block to "every new or
  touched page", which is exactly the 54/204 we observe. The convention is
  working as specified; the mistake would be to retro-apply it as a gate.

## 2. Contract §20 — Documentation surface (v0.9)

§20 does **not** restate the standard. `docs/contributor/docs-conventions.md`
is already normative for header blocks, ★-canonical marking, and archive
policy; §20 references it, the same move the contract already makes with
`REQUIREMENTS-TEMPLATE.md`. Restating it in two places would guarantee drift
between them.

**Scope note (20.0).** §20 binds every component listed in
`REQUIREMENTS-INDEX.md` — clients, libraries (`ka9q-python`, `hamsci-dsp`,
`hs-uploader`, `callhash`) and infra (`gpsdo-monitor`, `igmp-querier`,
`sigmond-rac`) alike. It is the one section reaching beyond contract-conformant
clients, because a docs surface is a property of the repo, not of the running
sigmond↔client seam. Vendored upstreams (`ka9q-radio`, `ka9q-web`, `onion`,
`ft8_lib`) stay out of scope, matching the existing note in
`REQUIREMENTS-INDEX.md`.

**The rules.** MUST-level rules are drawn from what the fleet already does, so
the check can land failing rather than warning:

- **20.1 (MUST)** `docs/INDEX.md` exists and carries a `Verified against:`
  header, per `docs-conventions.md` §2.
- **20.2 (MUST)** `docs/REQUIREMENTS.md` exists, follows
  `REQUIREMENTS-TEMPLATE.md`, and has a row in `REQUIREMENTS-INDEX.md` naming
  its prefix, kind and maturity.
- **20.3 (MUST)** `.github/workflows/docs-check.yml` exists and calls
  `HamSCI/sigmond/.github/workflows/docs-check.yml@main`.
- **20.4 (SHOULD)** every other live page carries a `Verified against:`
  header. Enforcement stays warn-only via `docs-freshness.py`, unchanged.

The MUST/SHOULD split is the severity the subject matter deserves: an *absent*
canonical page is a fact, a *stale* header is a judgement call.

**Version handling.** Contract goes to 0.9. Migration states that
`contract_version` in `deploy.toml` tracks the **runtime seam only** — no
client re-declares 0.9 on account of §20, and sigmond's version-skew warning
ignores it. Without that carve-out a docs-only section forces a `deploy.toml`
edit and a catalog bump on every client in the fleet for no runtime change.

## 3. One implementation, four call sites

`lib/sigmond/docs_conformance.py` holds the rules. Everything else is a thin
caller, so the CI checker and the fleet checker cannot drift apart.

| Check | Where | Catches | Blind to |
|---|---|---|---|
| **A** `scripts/docs-conformance.py`, failing step in the shared `docs-check.yml` | client repo CI | 20.1/20.2 violations in a repo that runs the workflow | a repo that never added the workflow |
| **B-lite** `tests/test_docs_requirements_index.py` | sigmond CI | catalog entry with a `contract` value and no index row | repos absent from the catalog |
| **B-full** workflow-presence check via the GitHub API | sigmond CI | 20.3 — a missing `docs-check.yml`, the one thing A structurally cannot see | anything while the API is unreachable |
| **C** `smd doctor --docs` | live host | 20.1/20.2/20.3 across `/opt/git/sigmond` checkouts, no network | components not installed on that host |

Each catches at least one failure the others cannot. B-lite is ~20 lines and
would have caught `hamsci-physics` on the day it was extracted.

**A** — stdlib only, matching its sibling checkers' shape (argparse, path
args, `path: reason` lines, exit 1 on failure). Skips the same directories as
`docs-linkcheck.py`: `.venv`, `venv`, `node_modules`, `graphify-out`, `.git`,
`__pycache__`, `.pytest_cache`, `superpowers`, `archive`.

**B-lite** — parses `etc/catalog.toml` and `docs/REQUIREMENTS-INDEX.md`;
asserts every entry with a non-empty `contract` has a row. `ka9q-radio` is
excluded by its own `contract = ""`.

**B-full** — GETs `/repos/{owner}/{repo}/contents/.github/workflows/docs-check.yml`
per catalog repo, authenticated with the default `GITHUB_TOKEN`. That token
reads public repos across orgs, which covers the cross-org
`mijahauan/hamsci-physics`, so no PAT is needed. **Skips rather than fails** on
network error or rate limit — a GitHub outage must not red an unrelated build.
Non-HamSCI upstreams are skipped by URL.

**C** — new `--docs` flag on `cmd_doctor`, walking the existing
`doctor.py::component_checkouts('/opt/git/sigmond')`. Warn-level per
component, consistent with doctor's role as an operator diagnostic rather
than a gate.

## 4. Ordering — the one real hazard

`docs-check.yml` is consumed by ten repos on `@main`. The instant a failing
step merges into sigmond, it runs in all of them. So the work is ordered
green-first:

1. Fix `ka9q-python` (INDEX.md + workflow) and `hamsci-physics` (INDEX.md,
   REQUIREMENTS.md, index row, workflow); add the missing header to
   `hf-timestd/docs/INDEX.md`.
2. Run the new checker across all 12 checkouts and confirm it is green.
3. Only then wire step A into the shared workflow.
4. Land B-lite, B-full and C, which are sigmond-local and block nobody.

Landing A before step 2 turns ten repos red simultaneously.

## 5. Repo work

**ka9q-python** — `docs/INDEX.md` (12 doc pages plus README to map, `docs/audit`
and `docs/superpowers` present; the latter is checker-exempt);
`.github/workflows/docs-check.yml`. `REQUIREMENTS.md` and its index row already
exist.

**hamsci-physics** — `docs/INDEX.md` (4 pages plus README);
`docs/REQUIREMENTS.md` from the template, prefix `PHY`, kind `client`,
maturity `Active` (~370 lines, in line with its peers, derived from
`deploy.toml` plus `inventory --json` per the index's §8 convention); a
`REQUIREMENTS-INDEX.md` row; `.github/workflows/docs-check.yml`. Note
`docs/DRF_UPLOAD_SYSTEM.md` is a 5-line stub — either fill it or make it an
explicit pointer file per `docs-conventions.md` §2.

## 6. Testing

- Unit tests for `docs_conformance` on `tmp_path` fixtures: missing INDEX,
  INDEX without header, missing REQUIREMENTS, missing workflow, `archive/`
  and `superpowers/` exempt, and a fully conformant repo.
- B-lite and B-full as described, B-full network-skipping.
- The step-2 gate: checker green across all 12 checkouts before A merges.
- TDD throughout, per the repo workflow.

## 7. Open items carried into implementation

- **Five-plus bound components have no local checkout.** `superdarn-sounder`,
  `codar-sounder`, `hfdl-recorder`, `hf-tec`, `callhash`, `igmp-querier` and
  `sigmond-rac` hold `REQUIREMENTS-INDEX.md` rows but are not in `repos/`. §20
  binds them and B-full will report on them as soon as it runs. Expect that
  wave to surface further gaps; it is out of scope for this pass, which fixes
  what is checked out here.
- **`sigmond-appliance` is unbound.** It has `docs-check.yml` and
  `docs/INDEX.md` but no `REQUIREMENTS.md` and no index row, so §20 does not
  reach it. Decide during implementation: give it a row and a requirements
  doc, or have §20 state why appliance/image repos are exempt. Silence is the
  one option that recreates the bug this spec exists to fix.
- **`hamsci-physics` lives at `mijahauan/hamsci-physics`**, the only fleet
  client outside the org since the 2026-07-01 migration. `catalog.toml`
  already marks it "staging: transfers to HamSCI/". Nothing here blocks on the
  move — the reusable workflow resolves cross-org because both repos are
  public — but B-full's URL handling must not assume a `HamSCI/` owner.

## 8. Out of scope

- Retro-applying `Verified against:` headers to the 150 pages that lack one.
- Any change to `docs-linkcheck.py` or `docs-freshness.py` behavior.
- Content rewrites of existing pages beyond the stub noted in §5.
- The org transfer of `hamsci-physics`.

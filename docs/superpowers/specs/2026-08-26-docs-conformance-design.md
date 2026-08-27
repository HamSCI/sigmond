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

Taken 2026-08-26/27 across all 19 bound components — the 12 checkouts in
`/home/mjh/hamsci/repos` plus the 7 surveyed through the GitHub API. All are
public and the maintainer holds ADMIN on each, so no repo is access-blocked.

| Repo(s) | `docs-check.yml` | `docs/INDEX.md` | `docs/REQUIREMENTS.md` | index row |
|---|---|---|---|---|
| sigmond, hf-timestd, hamsci-dsp, hs-uploader, gpsdo-monitor, mag-recorder, meteor-scatter, psk-recorder, wspr-recorder | ✅ | ✅ | ✅ | ✅ |
| sigmond-appliance | ✅ | ✅ | ❌ | ❌ |
| ka9q-python | ❌ | ❌ | ✅ | ✅ |
| hamsci-physics | ❌ | ❌ | ❌ | ❌ |
| superdarn-sounder, codar-sounder, hfdl-recorder, hf-tec, callhash, igmp-querier, sigmond-rac | ❌ (0/7) | ❌ (0/7) | ✅ (7/7) | ✅ |

The bottom row is the shape of the original mistake, seen whole: the 2026-08-23
program wrote a `REQUIREMENTS.md` into all seven — which is what earned them
their index rows — but gave none of them an `INDEX.md` or the CI workflow.
Nothing declared those two artifacts as obligations, so their absence was
invisible from the index that lists the repos as documented.

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
`REQUIREMENTS-INDEX.md` — 19 after this pass adds `hamsci-physics` and
`sigmond-appliance` to the 17 already there. Clients, libraries (`ka9q-python`,
`hamsci-dsp`, `hs-uploader`, `callhash`), infra (`gpsdo-monitor`,
`igmp-querier`, `sigmond-rac`) and the appliance/image repo alike. It is the
one section reaching beyond contract-conformant clients, because a docs
surface is a property of the repo, not of the running sigmond↔client seam.
Membership of the index *is* the binding test, which keeps one list
authoritative instead of two.

A repo whose only doc page is `REQUIREMENTS.md` still owes an `INDEX.md`
(20.1). It will be a one-row table, and §20 says so explicitly: the stub is
correct, not an oversight awaiting cleanup. An exemption for repos judged "too
small to bother" is the exact mechanism that produced the gap this section
closes. Vendored upstreams (`ka9q-radio`, `ka9q-web`, `onion`,
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

`docs-check.yml` is consumed on `@main`, by ten repos today and nineteen once
this pass lands. The instant a failing step merges into sigmond it runs in all
of them, with no staging and no per-repo pin to hide behind. So the work is
ordered green-first:

1. Do all of §5's repo work: 2 `REQUIREMENTS.md`, 9 `INDEX.md`, 9 workflow
   files, 2 index rows, 1 header fix.
2. Run the new checker across all 19 components and confirm it is green.
   Adding the workflow to a repo in step 1 makes it a consumer immediately —
   but of the *current* workflow, which cannot fail on §20, so step 1 is safe
   to land incrementally.
3. Only then wire step A into the shared workflow. This is the irreversible
   moment; everything before it is additive.
4. Land B-lite, B-full and C, which are sigmond-local and block nobody.

Landing A before step 2 reds nineteen repos at once.

## 5. Repo work

19 bound components; 10 need changes. Ordered by effort, not by repo.

**New `docs/REQUIREMENTS.md` (2)** — the bulk of the effort, ~370 lines each in
line with peers, from `REQUIREMENTS-TEMPLATE.md`, §8 derived from `deploy.toml`
plus `inventory --json` per the index convention:

- `hamsci-physics` — prefix `PHY`, kind `client`, maturity `Active`.
- `sigmond-appliance` — prefix `APP`, kind and maturity to be set when the doc
  is written; it is an appliance/image repo, not a client, and the template's
  §8 I/O sections will need adapting rather than deriving.

**New `docs/INDEX.md` (9)** — ka9q-python (12 pages plus README, the only
substantial mapping job), hamsci-physics (4 pages plus README), and the seven
API-surveyed repos. Note the seven are small: superdarn-sounder 3 pages,
hf-tec 3, codar-sounder 2, hfdl-recorder 2, and callhash, igmp-querier and
sigmond-rac **1 page each — `REQUIREMENTS.md` itself**.

A single-page repo's INDEX is a one-row table pointing at its only sibling.
Write it anyway, and have §20 state explicitly that this is a stub *by design*:
the alternative is an exemption resting on someone's judgement about which
repos are "too small to bother", which is precisely the mechanism that let
these repos slip in the first place. A future tidy-up must not be able to read
the stub as an oversight.

**New `.github/workflows/docs-check.yml` (9)** — the same 5-line reusable call
as the existing ten, in ka9q-python, hamsci-physics and the seven.

**Edits (3)** — `REQUIREMENTS-INDEX.md` rows for `hamsci-physics` and
`sigmond-appliance`; the missing `Verified against:` header on
`hf-timestd/docs/INDEX.md`.

**Content note** — `hamsci-physics/docs/DRF_UPLOAD_SYSTEM.md` is a 5-line stub.
Either fill it or make it an explicit pointer file per `docs-conventions.md` §2.

**Working location** — the seven surveyed repos have no checkout in `repos/`.
Clone them to the session scratchpad, edit, push. `CLAUDE.md` warns against
re-rooting the graphify extraction on a changed `repos/`, and these are
one-touch doc edits rather than ongoing development, so they do not earn a
permanent checkout.

## 6. Testing

- Unit tests for `docs_conformance` on `tmp_path` fixtures: missing INDEX,
  INDEX without header, missing REQUIREMENTS, missing workflow, `archive/`
  and `superpowers/` exempt, and a fully conformant repo.
- B-lite and B-full as described, B-full network-skipping.
- The step-2 gate: checker green across all 12 checkouts before A merges.
- TDD throughout, per the repo workflow.

## 7. Open items carried into implementation

- **`sigmond-appliance` joins the bound set** (decided 2026-08-27). It gains a
  `REQUIREMENTS.md` and an index row rather than an exemption, bringing the
  bound set to 19. Writing requirements for an appliance/image repo against a
  template built for clients is the one genuinely novel authoring task here.
- **`hamsci-physics` org transfer is in hand** — Nathaniel is moving it to
  `HamSCI/`. Nothing in this spec blocks on it: the reusable workflow already
  resolves cross-org because both repos are public, and GitHub redirects the
  old URL after a move. B-full must still not assume a `HamSCI/` owner, and
  `catalog.toml`'s "staging: transfers to HamSCI/" comment should be cleared
  once the move lands.
- **Scale changes the ordering risk, not its shape.** Nine repos gain the
  workflow in this pass. Each becomes a live consumer of the shared
  `docs-check.yml` on `@main` the moment it merges, so §4's green-first
  sequence now governs nineteen repos rather than ten.

## 8. Out of scope

- Retro-applying `Verified against:` headers to the 150 pages that lack one.
- Any change to `docs-linkcheck.py` or `docs-freshness.py` behavior.
- Content rewrites of existing pages beyond the stub noted in §5.
- The org transfer of `hamsci-physics`.

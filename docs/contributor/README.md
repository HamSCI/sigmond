# Contributor guide

> **Audience:** contributor
> **Status:** current
> **Verified against:** sigmond 5d082b9 on 2026-08-23 — docs
> **Canonical for:** the contributor's table of contents

You can read the source. This page is the order to read it in, not a
restatement of it — every page below links the code path instead of
re-explaining it, so nothing here can drift silently out of sync with
what actually runs.

## Reading order

| # | Read | Time | For |
|---|---|---|---|
| 1 | [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) | 15 min | Working agreements: where work happens, the two update orientations, pins, deploy-tree hygiene, PR expectations, graphify. Read this first — everything else assumes it. |
| 2 | [`orchestration.md`](orchestration.md) ★ | 20 min | How sigmond works: the 13 architecture layers, the FHS paths, and the CI-checked `smd` verb→module map. |
| 3 | [`../architecture.png`](../architecture.png) | 2 min | The whole-suite picture — radiod, the clients, the shared sink, uploaders — in one diagram. |
| 4 | [`../CLIENT-CONTRACT.md`](../CLIENT-CONTRACT.md) ★ | 45–60 min (or skim the section headers first — it is a reference, not a linear read) | The sigmond↔component seam: what every client must do and why. The norm every other client doc defers to. |
| 5 | one client's six-file docs — [psk-recorder](https://github.com/HamSCI/psk-recorder/blob/main/docs/INDEX.md) (the reference implementation) or [meteor-scatter](https://github.com/HamSCI/meteor-scatter/blob/main/docs/INDEX.md) (a descendant, now truthed to its own MSK144 reality) | ~25–30 min | What a conformant client's `docs/` looks like once ARCHITECTURE/CONFIG/INSTALL/OPERATIONS/REQUIREMENTS/SIGMOND-CONTRACT actually describe the client shipping them. |
| 6 | [`appliance-boundary.md`](appliance-boundary.md) ★ | 20 min | The appliance ↔ sigmond boundary: what the golden image bakes, what first boot does, what the wizard sets, the three version-provenance files, and — the question every change eventually asks — does this reach a station by `smd update` or does it need a new image. |
| 7 | [`dev-setup.md`](dev-setup.md) ★ | 10 min | Clone layout, the dev venv, running `smd` from a checkout, the test runner, and the docs checks (below). |
| 8 | [`client-authoring.md`](client-authoring.md) | 10 min | The route through the client-authoring documents, the two registrations that fail silently (`AFFINITY_UNITS`, `[client_features]`), and the rule that a client's own `docs/` must be true for *that* client. |
| 9 | [`docs-conventions.md`](docs-conventions.md) ★ | 10 min | How this whole tree is organised and kept true: the header block, the ★-canonical rule, pointer files, archive policy, and the "docs travel with behavior" rule below. |

~2.5–3 hours end to end (CLIENT-CONTRACT is the long pole — skim its headers
first if you're short on time); each step assumes the ones before it, so read
in order the first time through.

## The ledger

[`docs-gap-ledger.md`](docs-gap-ledger.md) is where a real software or
documentation gap goes when you find one *while writing a doc* — a command
that doesn't exist yet, two lists that disagree, a policy nothing enforces.
Append a row instead of fixing the code in the same change; a docs change
stays a docs change. See [`docs-conventions.md`](docs-conventions.md) §10 for
the row shape and when a filed issue lets the page graduate from "gap" to
"tracked."

## Running the docs checks

```bash
python3 scripts/docs-linkcheck.py docs README.md CONTRIBUTING.md CLAUDE.md
python3 scripts/docs-freshness.py docs README.md CONTRIBUTING.md CLAUDE.md
.venv/bin/pytest tests/test_docs_links.py tests/test_docs_cli_table.py tests/test_docs_freshness.py -q
```

The first is the stdlib-only relative-link and `#anchor` checker. The
second, `docs-freshness`, warns on a page whose `Verified against` sha
predates the last commit that actually changed its content (warn-only:
exits 0 unless run with `--strict`). The pytest run repeats both as tests
and additionally asserts `orchestration.md`'s CLI table matches
`bin/smd --help` / `bin/smd admin --help` exactly, both directions. All
three run in CI via `.github/workflows/docs-check.yml` on every push and
PR. See [`dev-setup.md`](dev-setup.md) §Docs checks for the full list and
what each one verifies.

## Deploy trees are not workspaces

Before you ssh into anything that isn't your own dev checkout: a station's
component checkouts are simultaneously a git working tree, an install
target, and a systemd unit's runtime source, owned by their service users —
not a place to develop. [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §8,
"Deploy trees are not workspaces," is the rule and the reasons behind it.

## Where rulings and history live

Design decisions for this documentation program itself — what got built,
why, in what order — live in `docs/superpowers/specs/` (the design) and
`docs/superpowers/plans/` (the task-by-task execution record). They are not
narrative docs and are skipped by the link checker; read them when you want
to know *why* a page is shaped the way it is, not *what* it says.

# Documentation conventions

> **Audience:** contributor
> **Status:** current
> **Verified against:** sigmond 2cd11c4 on 2026-08-24 — docs
> **Canonical for:** how docs are organised and kept true across the HamSCI/DASI2 repos

## 1. Where things live

`sigmond/docs/README.md` is the front door: it splits readers into three
paths — "I host a station," "I want to record a signal," "I work on the
code" — and sends each to its own subtree. `sigmond/docs/INDEX.md` lists
every page in this repo's `docs/` by audience so nothing is orphaned. The
narrative subtrees are `operator/`, `scientist/`, `contributor/`, and
`hardware/` (shared by scientist and operator); `archive/` holds history
(§5). Every HamSCI repo keeps a `docs/INDEX.md` on the same pattern
(hf-timestd's is the model). Material that belongs to another repo — a client's install
mechanics, a library's API — is linked from here, never copied or moved
into this tree.

## 2. One canonical page per topic

Every topic has exactly one ★-marked page in an `INDEX.md`. When two pages
disagree, the ★ page wins — that is the whole point of marking one. If a
second page still says something useful (a worked example, a narrower
audience), it stays, but it is not where the fight gets settled. A page
that becomes fully redundant with the canonical one turns into a pointer
file (§4) rather than being deleted, so links from other repos, code
comments, and old commit messages keep resolving.

## 3. The header block

Every page this program creates or substantially touches carries this
block immediately under the H1:

```
> **Audience:** operator | scientist | contributor | all
> **Status:** draft | current | shipped | historical | pointer
> **Verified against:** <repo> <commit-or-tag> on <date> — <how: live dasi002 / live b4 / code / docs / not re-verified>
> **Canonical for:** <topic>        (or  **See instead:** [<path>](<path>)  for pointer pages,
>                                    or  **Role:** <what this page is for>  for a page that is
>                                    neither — it owns no topic and points at no single one)
```

`Audience` routes the reader. `Status` says how much to trust the page as
current: `draft` is being written, `current` is actively maintained,
`shipped` is a design note whose design landed and is still the best
explanation, `historical` lives in `archive/` and may be wrong today,
`pointer` means "look elsewhere." A `Status` value may carry one
parenthetical qualifier narrowing or explaining it — e.g. `shipped (stages
0–3)`, `draft (plan, not executed)`, `current (working file)` — rather than
inventing a new status word. `Verified against` records **how** the
page was last checked — against a live host, against the code, against
the surrounding docs tree itself (`docs`, for a page whose claims are
about doc organisation rather than code behavior), or "not re-verified"
if nobody has confirmed it recently — so a reader can judge trust without
an automated staleness check. The `<commit-or-tag>` slot should be a git
commit sha (short form preferred, matching the rest of this repo's
convention) when you want `scripts/docs-freshness.py` to check the page
automatically — it matches `[0-9a-f]{7,40}` and does not resolve tags, so
a tag name there is invisible to the checker (the page is silently
skipped, not flagged) even though it's fine as a human-readable qualifier.
`Canonical for` names the one topic this page owns; pointer pages replace
it with `See instead:`. A third kind of page is neither: it is listed
un-starred in an `INDEX.md` (§2) because it does not own a single topic
and there is no one canonical page to point at instead — a pointer+rule
page that names the real canonical page elsewhere, or a working file like
the docs-gap ledger. Such a page replaces `Canonical for` with `Role:`,
stating in one line what the page is for instead of what it owns.

## 4. Pointer files

A pointer file is deliberately thin: an H1, the header block with
`Status: pointer` and a `See instead:` line naming the canonical page, and
one sentence of context (why this file used to exist, or what changed).
Nothing else. Pointer files exist so that a URL from another repo, a code
comment, or an ops-memory note that names an old path keeps resolving
instead of 404ing — the content moved, the address didn't have to.

## 5. Archive policy

A page moves to `docs/archive/` when it is a dated investigation report, a
session log, a plan marked complete or superseded, or a design note whose
design has shipped and which is no longer the best explanation of the
current system. A page **stays** live — even if it describes something
finished — when it documents shipped architecture that is still the best
explanation available; those carry `Status: shipped` rather than moving
(examples in this repo: MULTI-INSTANCE-ARCHITECTURE, RADIOD-IDENTIFICATION,
PRODUCER-THREAT-MODEL, PACKET-LOSS-DIAGNOSTICS). Moves always use
`git mv`, never delete-and-recreate, so `git log --follow` keeps the
history. If the moved file is referenced from another repo, from a code
comment, or from an ops-memory note, a pointer file (§4) is left behind at
the old path. Every `archive/` directory, in every repo, has its own
`README.md` explaining that the contents are historical and the canonical
page wins on contradiction — see [`../archive/README.md`](../archive/README.md)
in this repo for the wording.

## 6. Naming

Narrative guides use `lowercase-kebab.md` (e.g. `day-2.md`,
`docs-conventions.md`). Specs and contracts that other code or other repos
depend on use `SCREAMING-KEBAB.md` (e.g. `CLIENT-CONTRACT.md`,
`REQUIREMENTS.md`). Dated artifacts — investigation reports, session logs,
one-off plans — use `<TOPIC>-YYYY-MM-DD.md`. Each client repo's own
documentation follows a six-file skeleton: `ARCHITECTURE.md`,
`CONFIG.md`, `INSTALL.md`, `OPERATIONS.md`, `REQUIREMENTS.md`, and
`SIGMOND-CONTRACT.md`, so a contributor who has read one client's docs
already knows where to look in the next one.

## 7. Writing for each audience

Operator pages assume a person who can burn a USB stick and paste
commands over ssh, nothing more: plain human words, no unexplained
jargon, and every shell command tagged `[host]` or `[VM]` so it is
unambiguous where it runs. Every non-obvious step gets the why in one
sentence before the how, and any term that isn't self-evident links the
`glossary.md`. Scientist pages assume working Python and ka9q-python
familiarity; they do not restate what the ka9q-python docs already cover
(`GETTING_STARTED.md`, `RECIPES.md`) — they link to it and focus on what's
specific to sigmond: the client contract, the sink, timing tiers.
Contributor pages assume the reader can read the source; instead of
re-explaining behavior in prose, they link the code path (module, file,
function) that implements it, so the doc can't drift silently out of sync
with what the code actually does.

## 8. Docs travel with behavior

A PR that changes a CLI surface, a config key, a unit name, a file path, a
wizard prompt, or any other observable behavior must touch the canonical
page for that behavior, or say explicitly "no doc impact" in the PR
description. This keeps the ★-canonical pages from drifting the moment
code moves out from under them, and it's cheaper to write one sentence at
review time than to rediscover the gap later while writing an unrelated
guide. The rule itself, the `Verified against:` bump it requires, and the
one-command habit that checks it live in
[`CONTRIBUTING.md` §14, "Docs travel with behavior"](../../CONTRIBUTING.md#14-docs-travel-with-behavior)
— the PR template's checklist and the `docs-check` CI workflow now back
it with a checked step, not just a convention.

## 9. Checking links and freshness

From the sigmond repo root:

```bash
python3 scripts/docs-linkcheck.py docs README.md CONTRIBUTING.md CLAUDE.md
python3 scripts/docs-freshness.py docs README.md CONTRIBUTING.md CLAUDE.md
.venv/bin/pytest tests/test_docs_links.py tests/test_docs_cli_table.py tests/test_docs_freshness.py
```

`test_docs_cli_table.py` is the second check: it parses `bin/smd --help`
and `bin/smd admin --help` and fails if `contributor/orchestration.md`'s
verb table is missing a verb, or names something that isn't one, in either
direction.

From any other repo, sigmond's checkers can be pointed at that repo's own
docs:

```bash
python3 ../sigmond/scripts/docs-linkcheck.py docs README.md
python3 ../sigmond/scripts/docs-freshness.py docs README.md
```

`docs-linkcheck.py` is stdlib-only and exits non-zero on the first broken
relative link or missing anchor, so it's safe to run in CI or before a
commit. `docs/superpowers/` (specs/plans) is skipped by the checker: those
are working documents that legitimately forward-reference pages not yet
written by later tasks in the same program. Any `archive/` directory is
skipped too, as a link *source*: archive pages are frozen history
(`Status: historical`, §3) and are not expected to keep their outgoing
links current. A live (non-archive) page linking *into* an `archive/`
page is unaffected — that's an ordinary outgoing link and is still
checked, since the archived page itself still has to exist at the path
named.

`docs-freshness.py` warns when a page's `Verified against:` sha predates
the last commit that actually changed its content (bumping the sha alone
doesn't count) — warn-only by design (§7 of the design spec: "staleness is
visible, not enforced"), so it exits 0 unless run with `--strict`. It
always prints a one-line summary (`docs-freshness: N stale page(s)`), on a
clean run too, so no output never has to be read as "did nothing."

The `docs-check` GitHub Actions workflow
(`.github/workflows/docs-check.yml`, reusable as
`HamSCI/sigmond/.github/workflows/docs-check.yml@main`) runs both checkers
plus, in sigmond itself, the three doc pytest files above on every push
and PR to `main` — though the workflow's own script steps run against its
`paths:` input, which defaults to `docs README.md` (narrower than the
four-file surface `CONTRIBUTING.md`/`CLAUDE.md` add); it's the pytest
trio, hardcoded to the fuller surface, that closes that gap in sigmond's
own CI.

## 10. Software gaps found while writing

Writing a doc often surfaces a real gap in the software — a command that
doesn't exist yet, a missing consolidated report, a policy nothing
enforces. Append a row to [`docs-gap-ledger.md`](docs-gap-ledger.md)
instead of fixing the code in the same change; a docs change stays a docs
change. Once a gap is filed as an issue in its owning repo, the page that
surfaced it says "today: X — tracked in `<repo>#N`" so the reader knows
the limitation is known and tracked, not merely undocumented.

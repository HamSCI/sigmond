# Authoring a sigmond client

> **Audience:** contributor
> **Status:** current
> **Verified against:** sigmond 5d082b9 on 2026-08-23 — code
> **Canonical for:** the route through the client-authoring documents, and the rule that a client's own `docs/` must be true

Writing a new client is well covered — by four documents, a runnable
scaffold and two living examples. What was *not* written down is the
order to read them in, and the obligation that outlives the merge: **a
client's own `docs/` must describe that client.** This page is those two
things. It restates none of the mechanics.

## The path

Read in this order. Each one assumes the previous.

| # | Read | For |
|---|---|---|
| 1 | [../scientist/becoming-a-client.md](../scientist/becoming-a-client.md) | The bridge. Whether you should build a client at all (Tier 0 — a capture on a disk — is often the right answer), what Tier 1 buys you, and the scientist's route through the two contributor documents below. Start here even as a contributor: it is the only page that argues the *decision*. |
| 2 | [../ADD-A-CLIENT.md](../ADD-A-CLIENT.md) | The mechanics. The seven things you must ship — repo layout, `deploy.toml`, templated unit, contract subcommands, config template + render step, `[client_features]` TUI registration, catalog entry — plus the `config/help.toml` three-tier audit and the drop-in verification steps. |
| 3 | [../CLIENT-CONTRACT.md](../CLIENT-CONTRACT.md) ★ | The norm. §1–§19: what each surface must do and why. ADD-A-CLIENT tells you which file to touch; the contract tells you what "correct" means, and it wins every disagreement. |
| 4 | [../REQUIREMENTS-TEMPLATE.md](../REQUIREMENTS-TEMPLATE.md) | The requirements register your client fills in as `docs/REQUIREMENTS.md`, in the same shape as every other component so the whole suite stays comparable. Interface requirements are *referenced*, not restated — the contract already holds them. |
| — | [../scientist/skeleton/README.md](../scientist/skeleton/README.md) | A copyable, MIT-licensed, stdlib-only minimal client (`deploy.toml`, unit, `cli.py`, `config/help.toml`, `pyproject.toml`). Run it on your laptop before you go near a station. |
| — | [../EVENT-CLIENT-PLAYBOOK.md](../EVENT-CLIENT-PLAYBOOK.md) ★ | Read *first* instead of (2) if this is a short-notice one-off — eclipse, meteor shower, eruption. It covers the capture-first, channel-envelope and load-budget decisions the checklist assumes you already made. |

Two shortcuts worth knowing. `smd install <name>` and `smd tui` work as
soon as items (1)–(6) of the ADD-A-CLIENT checklist exist, because
sigmond's catalog **discovery** layer synthesizes an entry from any
`/opt/git/sigmond/<name>/deploy.toml` — the catalog entry in
`etc/catalog.toml` (item 7) is what makes *other* hosts able to pull you
in. And "copy psk-recorder" is still the fastest correct start; the
skeleton exists for when 40 files of production client is too much to
read.

## Register your client with the substrate, not just the catalog

Two registrations are easy to forget because nothing fails loudly
without them.

**Decoders MUST join `AFFINITY_UNITS`.** `lib/sigmond/cpu.py` maps each
service template to a CPU group (`'psk-recorder@.service': 'other'`,
…). `smd admin diag cpu-affinity --apply` writes the standard
`smd-cpu-affinity.conf` drop-in only for templates in that map. A
decoder that is missing from it runs **unconfined — including on
radiod's hyperthread pair** — where its decode bursts pollute radiod's
L1/L2/L3 and the symptom is RX888 USB packet drops, not anything that
looks like your client's fault. It has been caught twice by
observation rather than by any check — wspr-recorder (`223427c`,
2026-05-30) and then meteor-scatter, hs-uploader and two watchdogs, all
found running on B4-100's radiod HT pair (`6a9fe3f`, 2026-07-20). Both
fixes were one line each; the cost was the drop investigation that
preceded them. Add your template when you add the client, and note that
nothing asserts the map is complete — a missing entry is silent until a
station shows packet loss.

**`[client_features]` is how you appear in the operator's tools.** A
`watch` verb, a `verifier` target and a `receiver_channels` parser are
declared in your own `deploy.toml` and loaded by
`lib/sigmond/client_features.py` — no sigmond-side edit. Skipping them
does not break anything; it just means `smd watch <you>` does not exist
and the TUI has nothing to show.

## The six-file docs skeleton

Every client in the suite ships the same six documents. Same names, same
jobs — so a reader who has read one client can navigate any other.

| File | Audience | What it owns |
|---|---|---|
| `docs/ARCHITECTURE.md` | contributor | Internals: the pipeline, module by module, and the design decisions behind it |
| `docs/CONFIG.md` | operator/contributor | Every config key and environment variable, with its default and the code that reads it |
| `docs/INSTALL.md` | operator/contributor | Prerequisites, what `install.sh` does step by step, the path layout, multi-instance, uninstall |
| `docs/OPERATIONS.md` | operator/contributor | Day-to-day: service control, logs, health signs, failure modes, and what a restart costs |
| `docs/REQUIREMENTS.md` | contributor | The formal register from REQUIREMENTS-TEMPLATE.md — `<PREFIX>-*` IDs, gaps, traceability |
| `docs/SIGMOND-CONTRACT.md` | contributor | Section-by-section conformance map to CLIENT-CONTRACT.md, **including the sections you do not implement** |

Plus `docs/INDEX.md` — the one-table map of those six, carrying the ★
markers. Every page gets the header block from
[docs-conventions.md §3](docs-conventions.md).

Two living examples, deliberately different:

* **[psk-recorder](https://github.com/HamSCI/psk-recorder/blob/main/docs/INDEX.md)**
  — the contract's greenfield reference implementation and the source of
  the skeleton's shape.
* **[meteor-scatter](https://github.com/HamSCI/meteor-scatter/blob/main/docs/INDEX.md)**
  — a client *descended* from that reference, whose docs page for the
  contract is worth reading precisely because it is honest about the
  sections it does not implement (§13 has no control socket) and about
  the drift its lineage left behind.

## THE RULE: a client's `docs/` must be true for *that* client

Copying psk-recorder is the right way to start a client. It is also how
a client acquires six documents that describe **psk-recorder**.

The skeleton is a starting point, not a deliverable. Before your client
merges, every one of the six files must describe *your* client:

- the modes you actually decode, and the decoder you actually fork;
- the sink target and per-row tags you actually write;
- the config keys your `config.py` actually reads, with the defaults it
  actually applies;
- the contract sections you actually implement — **and an honest entry
  for the ones you do not**, because a conformance page that lists only
  successes is a page nobody can use to find the gap;
- the environment variables that exist in your source, not the ones the
  parent's had.

Name-substitution is not truthing. Search-and-replacing `psk-recorder` →
`your-client` produces documents that are *plausibly* about your client
and wrong in every detail that matters.

### The cautionary example

meteor-scatter was scaffolded from psk-recorder and its `docs/` were
carried over with the names swapped. For months the repo shipped five
documents that told a reader, in meteor-scatter's own voice, that the
client decoded FT8 and FT4 with `decode_ft8` and uploaded through a
`pskreporter-sender` subprocess. It decodes MSK144 with
`jt9 --msk144 -Y` and has no `decode_ft8` and no sender subprocess
anywhere on its runtime path. The README claimed 15 s / 7.5 s FT slots;
the T/R period is 30 s by default. `docs/REQUIREMENTS.md` was the only
accurate document, and it called its own repo "the highest doc-debt in
the suite."

Nothing broke. That is the point — nothing *breaks*. What happens
instead is that every reader after you pays: the operator who greps for
`decode_ft8` on a host that has never had it, the contributor who wires
a feature against a sink table (`msk144.spots`) that does not exist, the
reviewer who cannot tell an intentional gap from an undocumented one.

The documentation program's Phase 0 flagged the five files with a
stale-copy banner rather than deleting them, because a page that says "I
am lying" is more useful than a page that is silently wrong. Phase 3
(2026-08-23) rewrote all five against the code. Truthing them surfaced a
list of facts that were written down nowhere: that `meteor-scatter
status` is a stub which reports "not running" whatever the daemon is
doing, that `env apply` accepts no keys at all (so the config wizard's
Delivery menu item always fails), and that the sink target is the shared
`psk.spots` and not a namespace of its own. **None of those were found
by reading the code. They were found by trying to write a true sentence
about it.**

That is the real argument for this rule: truthing a doc is a review
pass. The cheapest time to run it is while you still remember what you
wrote.

And the rule bites the person applying it too. The first draft of *this
page* asserted that meteor-scatter was missing from `AFFINITY_UNITS` —
written from a memory of a warning about that class of bug rather than
from `cpu.py`, where the entry has sat since `6a9fe3f`. Review caught it
before the docs shipped. A claim about code is only as good as the
last time someone opened the file.

## What else a new client ships

Two upkeep files, one per repo:

- `.github/PULL_REQUEST_TEMPLATE.md` — the docs-travel-with-behavior
  checklist a reviewer sees on every PR (sigmond's copy:
  [`.github/PULL_REQUEST_TEMPLATE.md`](../../.github/PULL_REQUEST_TEMPLATE.md)).
- `.github/workflows/docs-check.yml` — a ~12-line caller of sigmond's
  reusable `docs-check` workflow, which runs the stdlib link checker and
  the (warn-only) `Verified against` freshness check on every push and PR:

  ```yaml
  name: docs-check
  on: { push: { branches: [main] }, pull_request: { branches: [main] } }
  jobs:
    docs:
      uses: HamSCI/sigmond/.github/workflows/docs-check.yml@main
      with: { paths: "docs README.md" }
  ```

Both files copy into a new client repo largely as-is; adjust the PR
template's checklist wording only if the repo has no `docs/` tree yet, and
adjust `paths:` if the repo's doc surface includes more than `docs/` and
`README.md` (e.g. hf-timestd also lists `INSTALLATION.md`).

Run the link checker by hand before you commit docs, in addition to
whatever CI runs on push:

```bash
python3 /opt/git/sigmond/sigmond/scripts/docs-linkcheck.py \
    <your-repo>/docs <your-repo>/README.md
```

Zero broken links is the bar. See
[docs-conventions.md](docs-conventions.md) for the header block, the ★
rule and the audience split those checks assume.

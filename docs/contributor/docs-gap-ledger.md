# Docs-gap ledger

> **Audience:** contributor
> **Status:** current (working file)
> **Verified against:** sigmond f3b0c6e on 2026-11-18 — docs (the row→issue mapping stays as of 6adfc75; the repo list checked against the local checkouts, no network call)
> **Role:** working file — findings ledger, not a canonical topic page; each row becomes a `docs-gap` issue in the owning repo at the end of each phase

**All 68 rows of the 2026-08 documentation program (Phases 0–3) were filed on 2026-08-24: 65 issues across eight repos, one comment adding row 61's evidence to the already-open [sigmond#31](https://github.com/HamSCI/sigmond/issues/31), and three rows already resolved (28 merged into 31's issue, 59 fixed during the program, 65 closed when `docs-check` was observed green post-push).** The full row→issue mapping lives in this file's git history (commit `6adfc75`) and in `ops/docs/docs-program-20260823/docs-gap-ledger-final-filed.md`.

## Where the issues live

Every filed issue carries the `docs-gap` label; bug-class rows also carry `bug`.

| Repo | Issues | Query |
|------|--------|-------|
| sigmond | 37 | <https://github.com/HamSCI/sigmond/issues?q=label%3Adocs-gap> |
| hf-timestd | 9 | <https://github.com/HamSCI/hf-timestd/issues?q=label%3Adocs-gap> |
| sigmond-appliance | 6 | <https://github.com/HamSCI/sigmond-appliance/issues?q=label%3Adocs-gap> |
| ka9q-python | 5 | <https://github.com/HamSCI/ka9q-python/issues?q=label%3Adocs-gap> |
| meteor-scatter | 3 | <https://github.com/HamSCI/meteor-scatter/issues?q=label%3Adocs-gap> |
| mag-recorder | 2 | <https://github.com/HamSCI/mag-recorder/issues?q=label%3Adocs-gap> |
| hs-uploader | 1 | <https://github.com/HamSCI/hs-uploader/issues?q=label%3Adocs-gap> |
| Costas-array (mijahauan) | 2 | <https://github.com/mijahauan/Costas-array/issues?q=label%3Adocs-gap> |

Counts date from 2026-08-24 and have moved since; follow the query links for the live list. Seven of the eight repos have a checkout beside `sigmond/`; Costas-array sits outside the suite's `repos/` tree. The sigmond-appliance repo is slated for a rename to sigmond-proxmox, and its query link will need the new name then.

## Open rows

New gaps go here per [CONTRIBUTING §14](../../CONTRIBUTING.md#14-docs-travel-with-behavior) and are filed as issues at the next batch.

| # | Repo | Gap (what the doc wanted to say) | What is true today | Page that needs it | Issue |
|---|------|----------------------------------|--------------------|--------------------|-------|
| 69 | sigmond | `lib/sigmond/docs_conformance.py` and `scripts/docs-conformance.py` cite "CLIENT-CONTRACT §20" for the documentation surface a client must carry | `CLIENT-CONTRACT.md` ends at §19; no §20 exists, and `smd doctor` has no `--docs` flag | [`dev-setup.md`](dev-setup.md) §Docs checks | not yet filed |
| 70 | sigmond | The `lib/sigmond/commands/archive.py` module docstring says the archiver "ships in phase 2" and the drive "stands ready and empty" | hf-timestd's `timestd-archive.timer` ships and `smd archive add` links and enables it | [`orchestration.md`](orchestration.md) verb map | not yet filed |
| 71 | sigmond | `docs/INDEX.md` describes `orchestration.md` as holding "the 13 architecture layers" | `CLAUDE.md` lists 14 (storage ledger); the page now says 14 | `docs/INDEX.md` | not yet filed |

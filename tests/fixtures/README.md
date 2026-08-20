# Contract fixtures

Canonical JSON output captured from real HamSCI clients that implement
`docs/CLIENT-CONTRACT.md`.  Used by `tests/test_contract_adapter.py` as
a frozen reference so `ContractAdapter` keeps parsing real client
output correctly across refactors.

## Files

- `hf-timestd-inventory.json` — `hf-timestd inventory --json` output
  captured on bee3 at hf-timestd commit `96beda9` (v7.0.0, contract
  v0.2). First full v0.2 reference implementation.
- `hf-timestd-validate.json` — `hf-timestd validate --json` output
  from the same host/commit.
- `gap-hourly.tsv` — the `/var/log/gap-hourly.tsv` format written by
  `sigmond.gap_hourly` (header + a clean row, a row with gaps, and the
  honest `NA` row an empty window produces).  This is the cross-repo
  format contract between the sampler and
  `sigmond.heartbeat.parse_gap_row`; `tests/test_heartbeat.py` asserts
  the header still equals `gap_hourly.HEADER` and cross-checks a row
  produced by `build_row()`/`append_row()` itself, so a column change
  on either side fails loudly instead of silently reading `NA` as 0.

## Refreshing

When a client ships a new contract-affecting version, re-capture:

```
ssh bee3 'hf-timestd inventory --json' > tests/fixtures/hf-timestd-inventory.json
ssh bee3 'hf-timestd validate  --json' > tests/fixtures/hf-timestd-validate.json
```

Commit the new fixtures alongside any adapter changes needed to parse
them.  If a field is removed that the adapter previously consumed, the
adapter must keep tolerating its absence for one contract release
(see CLIENT-CONTRACT.md "Migration and versioning").

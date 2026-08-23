# Docs-gap ledger

> **Audience:** contributor
> **Status:** current (working file)
> **Verified against:** n/a
> **Canonical for:** software gaps discovered while documenting; each row becomes a `docs-gap` issue in the owning repo at the end of each phase

| # | Repo | Gap (what the doc wanted to say) | What is true today | Page that needs it | Issue |
|---|------|----------------------------------|--------------------|--------------------|-------|
| 1 | sigmond | one command that reports attached hardware (RX888 serial, GPSDO model, mag sensor) | scattered: `smd admin environment`, `smd watch gpsdo`, mag logs | operator/day-2.md, hardware/shopping-list.md | — |
| 2 | sigmond-appliance | the RAC page's root@ ssh command is wrong (PermitRootLogin no) | operators must ssh as hamsci@ via the PM | operator/remote-access.md | — |
| 3 | hs-uploader | PSWS transport for a new client's products | only hf-timestd/GRAPE + mag ship to PSWS; GRAPE uploader bypasses [uploads] policy | scientist/becoming-a-client.md (Phase 2) | — |
| 4 | sigmond | client scaffold command (`smd component add` takes a repo, not a template) | ADD-A-CLIENT says "copy psk-recorder" | scientist/becoming-a-client.md (Phase 2) | — |
| 5 | sigmond | which GPSDO output is patched into which jack (RX888 reference, TS-1 reference) | nothing on the station records the physical cabling; `gpsdo-monitor` reports OUT1/OUT2 frequencies (b4: 10 MHz / 27 MHz) but not where they go | hardware/shopping-list.md | — |

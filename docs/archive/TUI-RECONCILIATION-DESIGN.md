# TUI reconciliation — design

Decided with Michael (AC0G) 2026-08-11. This spec scopes a dedicated
session (or two) to bring the TUI back into correspondence with smd.

## Problem

Neither operator has used the TUI much; it has fallen behind smd. The
drift is measurable:

- `docs/TUI-FUNCTION-INVENTORY.md` is a snapshot of commit `01d6bb7`
  (2026-05-24). Since then ~200 commits touched the smd command
  surface; ~70 touched the TUI.
- Whole verb families have no TUI counterpart: `psws` (enroll / verify
  / register-radiod), `secrets`, top-level `timing`, `readiness`,
  `capture-prep`, `location`, `personalize`, `bundle`, `motd`,
  `render`, `backup`.
- The TUI test suite skips wherever textual isn't installed — i.e. on
  every dev box — so it only ever ran on CI, which was red from
  ≥2026-06-24 until dc48e80 (2026-08-11) with nobody noticing. The
  default-landing behavior changed underneath its own test (c1d47e1).

## Decisions (settled 2026-08-11)

| Question | Decision |
|---|---|
| What is the TUI? | **Curated operator console** — screens for the workflows the personas actually perform; no 1:1 smd verb parity. Rare/expert verbs stay CLI-only and TUI help names them. |
| Boundary with web UIs | **TUI = control, web = depth.** TUI owns mutation and being-at-the-station work (bring-up, lifecycle, config, enrollment) plus a shallow health overview. Rich time-series/science visualization is station-web's job (future, per the hf-timestd split plan). ka9q-web keeps per-host receiver detail. |
| Must-cover areas | Timing status + authority on the landing screen; PSWS enrollment + uploads; Greenfield ↔ bringup/readiness parity. (Debug-bundle build-out explicitly deferred.) |
| Out-of-scope screens | **Prune aggressively.** Delete screens with no persona workflow; recoverable by revert. |

## Personas

1. **On-site control operator** — brings a station up, completes PSWS
   enrollment, checks health at a glance, performs routine maintenance
   (restart a client, enable uploads, edit a config).
2. **Developer / administrator** — debugs problems over SSH, arranges
   updates. Served by the kept watch/validate/diag screens and by the
   CLI; no new build-out this round.

## Phases

### Phase 0 — ground truth audit

Regenerate `TUI-FUNCTION-INVENTORY.md` from current HEAD: the real
verb table out of `bin/smd`, the current screen list (37 modules under
`lib/sigmond/tui/screens/`), the tree/binding map. Rule every screen
**keep / fix / kill** against the persona workflows. Kill-default
candidates: sources, kiwisdr, fft_wisdom, placeholder, and the
components/topology overlap with the current install model.

### Phase 1 — stop the silent drift

- Declare textual a dev/test extra so the TUI suite stops skipping on
  dev boxes (the June–August blindness must not recur).
- Add a parity-lint test: every kept screen maps to an smd verb that
  still exists, so an smd rename breaks CI, not the operator.

### Phase 2 — prune

Delete killed screens + tree entries + their tests in one commit.

### Phase 3 — build-outs

1. **Timing on the landing screen.** Overview grows the T-level /
   accuracy verdict, reusing `lib/sigmond/commands/timing_show.py`
   (the `smd timing` implementation) — no new plumbing. Reconcile the
   existing timing_authority / annotation_quality screens with it.
2. **PSWS + uploads screen.** The enrollment arc (enroll → key →
   verify → register-radiod) plus per-client upload toggles
   (`smd config upload`) and delivery verdicts.
3. **Greenfield ↔ bringup parity.** Guided bring-up matches what the
   wizard/image do now: `bringup dasi2`, the readiness gate, and the
   equipment-detection panel including TS-1.

### Phase 4 — validate and ship

- Hermetic tests for every new/changed screen: mock the host, never
  read it (the dc48e80 lesson — help.toml, topology, disk_usage were
  all read live).
- Operator walkthrough on the real B4 VM as acceptance.
- Regenerated inventory doc committed as the new IA record.
- Ships in the v3.31+ image.

## Sizing and dependencies

One long session or two normal ones (Phases 0–2, then 3–4). Not gated
on the T6/hf-timestd-split track. Station-web's eventual existence
does not block anything here — the TUI-side boundary decision above
already accounts for it.

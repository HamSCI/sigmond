# Requirements Specification — Template & Method

**Purpose.** This is the standard template every SigMonD-suite requirements
document fills — `sigmond` itself and each component. The goal is the
*thorough enumeration of requirements we would ideally have written before
the first line of code*, reconstructed now from what exists. It is therefore
**retroactive but greenfield-grade**: it states requirements as if from
scratch, while honestly recording where each one came from.

> One template, filled the same way by every doc, is the whole point — it
> makes the ~17 documents comparable, aggregatable, and checkable against
> each other and against the [client contract](CLIENT-CONTRACT.md).

---

## The method (read once)

### Two requirement *kinds* — keep them in separate documents

- **Interface / integration requirements** (the seam between sigmond and a
  component) live **once**, in [CLIENT-CONTRACT.md](CLIENT-CONTRACT.md) and the
  sigmond coordination docs. A component requirements doc **references** the
  contract for its integration surface; it does not restate it.
- **Domain / functional requirements** (what a component does as a standalone
  instrument, or what sigmond does as the overseer) live **per document**.

### Three sources, reconciled — this is what "retroactive" means

Every requirement is built by reconciling:
1. **Documented** intent — READMEs, design docs, stated objectives.
2. **Implemented** reality — the actual code: CLI surface, config schema,
   systemd units, `deploy.toml`, and especially the machine-declared I/O in
   `inventory --json`.
3. **Contract** obligations — what conformance demands.

Where these three disagree, that **gap** is the highest-value output (see §8).

### Provenance & status tags — mandatory on every requirement

Tag each requirement with **where it came from** and **where it stands**:

| Provenance | Meaning |
|---|---|
| `[DOC]` | Was already documented (README / design doc / objective). |
| `[CODE]` | Implicit in the implementation; reverse-engineered here, never written down before. |
| `[NEW]` | Newly articulated by this review — a real requirement nobody had captured. |

| Status | Meaning |
|---|---|
| ✅ | Implemented and verified. |
| 🟡 | Partially implemented / implemented but unverified. |
| ⬜ | Specified, not yet built. |

The mix tells the story: a doc that is mostly `[CODE]✅` is mature-but-was-undocumented;
lots of `[NEW]⬜` means real scope was never captured.

### Requirement IDs — for traceability

`<PREFIX>-<TYPE>-<NNN>`, e.g. `SIG-F-001`, `HFT-Q-003`.
- PREFIX: `SIG` (sigmond) or the component's short code (e.g. `HFT` hf-timestd, `WSP` wspr-recorder).
- TYPE: `F` functional · `Q` quality/non-functional · `I` interface · `C` constraint · `D` data.
IDs are permanent. They are how a requirement links to a Project #18 issue, a
test, and (for shared items) a PSWS #6 charette item.

### Where each doc lives

- The **template** and **sigmond** requirements: in this repo (`sigmond/docs/`).
- Each **component's** requirements: **in that component's own repo** (matches
  hybrid ownership — "each client is authoritative"). Sigmond keeps a one-line
  **index** (sibling to the catalog) pointing at each, mirroring
  [PSWS-MAPPING.md](PSWS-MAPPING.md): distributed truth, centralized index.

### Relationship to Project #18

Requirements docs are the **durable "what & why"**; Project
[#18](https://github.com/orgs/HamSCI/projects/18) is the **live "what's being
done."** A `⬜`/`🟡` requirement should map to a #18 issue; a closed #18 issue
should flip a requirement to `✅`. Never duplicate the backlog into the spec —
link by ID.

---

## The template (copy everything below into the new doc)

```
# <Name> — Requirements Specification

**Status:** v0.1 baseline (retroactive). **Owner:** <name/call>.
**Last reconciled against code:** <commit/date>.
**Prefix:** <SIG|HFT|…>.

## 1. Context & problem statement
*Why this exists. The problem it solves, who needs it, what prompted it.
For a component: the science/operational need. 2–4 paragraphs, no fluff.*

## 2. Goals & objectives
*The measurable outcomes this must achieve. Bullet list. Each should be
something you could later say "yes/no, we met it."*

## 3. Non-goals / out of scope
*Explicit boundaries. What this deliberately does NOT do, and which
neighbor owns it instead (sigmond? another component? PSWS server?).
This section prevents the scope-creep that unwritten requirements invite.*

## 4. Stakeholders & actors
*Who/what interacts with this: operators, other components, radiod, the
sink, upstream services, sigmond. Name them; they drive the interfaces.*

## 5. Assumptions & constraints
*Environment and design constraints taken as given (e.g. stdlib-only core,
headless-first, Proxmox-guest CPU model, FHS paths, Python 3.11). Tag each
[DOC]/[CODE]/[NEW].*

## 6. Functional requirements
*The heart. Numbered, testable, independently verifiable. Each line:*
`<ID> [PROV] <STATUS> Requirement statement (one testable claim).`
*Group by capability. Prefer "The system SHALL …". Split anything with
"and" into two requirements.*

## 7. Quality / non-functional requirements
*Performance, reliability, atomicity/idempotency, security, operability,
portability, observability. Same ID/tag format. These are the ones most
often left implicit — be exhaustive.*

## 8. External interfaces (inputs, outputs, contracts)
*8.1 Inputs — config files, RF channels, deps, env. Where possible
DERIVE from deploy.toml + `inventory --json` so it can't drift.*
*8.2 Outputs — products, formats, sink writes (target_db/table), upload
targets, logs, status/inventory. Also derive what you can.*
*8.3 Contracts/APIs — contract conformance level (which version, which
optional sections), control sockets, the sigmond seam. REFERENCE
CLIENT-CONTRACT.md; do not restate it.*

## 9. Data requirements
*Schemas, retention, volume (mb_per_day), provenance/timing labels.*

## 10. Dependencies & development sequence
*What must exist first (uses/requires from the catalog). The intended
phase/milestone ordering — what was/will be built in what order, and why.
This is the "sequence of development" captured as intent, not git log.*

## 11. Acceptance criteria & verification
*How each requirement group is proven: which test, which `validate --json`
rule, which harmonization rule, which manual check. Link requirement IDs
to verification.*

## 12. Risks & open questions
*Known gaps, drift between doc and code found during reconciliation,
decisions still owed. Each open question gets an owner or a #18 issue.*

## 13. Traceability
*Table: Requirement ID → #18 issue → test/verification → (if shared)
PSWS #6 item. The spine that keeps spec, backlog, and code in sync.*
```

---

## Worked conventions (so every author does it identically)

- **"SHALL" = requirement; "SHOULD" = strong preference; "MAY" = option.** No
  requirement uses "will/can/handles" — those hide whether it's mandatory.
- **One testable claim per ID.** If you can't write a pass/fail check for it,
  it's an objective (§2), not a requirement (§6/§7).
- **Derive I/O, don't transcribe it.** The contract already makes components
  self-describe inputs/outputs; pull §8 from `deploy.toml` + `inventory --json`
  output so the doc stays honest against the code.
- **Tag honestly.** `[CODE]` is not a confession — most of a mature
  component's requirements *will* be `[CODE]✅`, and that's exactly the
  retroactive picture we want to see.

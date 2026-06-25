# Sigmond (Overseer) — Requirements Specification

**Status:** v0.1 baseline (retroactive). **Owner:** Michael Hauan (AC0G), Rob Robinett (AI6VN).
**Last reconciled against code:** `db7216c` (2026-06-25).
**Prefix:** `SIG`.

> This is the *frame* of the suite-wide requirements effort: it specifies what
> **only the overseer** does. The sigmond↔component seam is specified once in
> [CLIENT-CONTRACT.md](CLIENT-CONTRACT.md) (the interface requirements) and is
> referenced, not restated, here. Per-component domain requirements live in
> each component repo. Method, tags, and IDs: see
> [REQUIREMENTS-TEMPLATE.md](REQUIREMENTS-TEMPLATE.md).

## 1. Context & problem statement

A DASI2 station runs a family of independent SDR science clients (WSPR, FT8/FT4,
HF time-standard, magnetometer, CODAR, HFDL, beacon-TEC, meteor-scatter) that
share one ka9q-radio receiver, one GPS-disciplined clock, one set of CPU cores,
one local datastore, and a handful of upstream upload endpoints. Each client is
developed and owned independently, yet they must coexist on a single host
without colliding over those shared resources, and an operator (often remote,
often non-expert) must be able to install, configure, run, observe, and repair
the whole stack.

Sigmond exists to be that coordination layer — the **overseer** that owns
everything *between* the clients (installation, lifecycle, resource arbitration,
shared datastore, timing-authority distribution, diagnostics) while delegating
everything *inside* a client to that client. The defining design tension: do
this **without** absorbing the clients (they must remain standalone) and
**without** a client being able to silently break a neighbor.

## 2. Goals & objectives

- A new station goes from bare host to producing/uploading science with a
  bounded, repeatable procedure (ideally one guided flow).
- No client can degrade another's data quality through a shared resource (CPU,
  multicast, disk, clock) without sigmond detecting and surfacing it.
- Any client can be added, removed, enabled, disabled, or upgraded without
  destabilizing the others.
- The whole stack is operable headless (SSH/CI/scripted) and observable at a
  glance (`smd status`).
- Sigmond never becomes a hard dependency of a client's core function — a
  client still runs if sigmond is absent.

## 3. Non-goals / out of scope

- **Client-internal behavior** — decoding, DSP, science output. Owned by each
  component; sigmond delegates to the client's own `install.sh`, config, and CLI.
- **Post-upload / aggregated concerns** — database, API, visualization,
  cross-station science. Owned by the PSWS server (see
  [PSWS-INTERFACE-BOUNDARY.md](PSWS-INTERFACE-BOUNDARY.md)).
- **Cross-operator fleet orchestration** — each operator runs their own sigmond;
  coordination is via PSWS/HamSCI endpoints, not host-to-host (see CLAUDE.md).
- **Proxmox host-level policy authorship** beyond CPU/freq/passthrough bootstrap.

## 4. Stakeholders & actors

Station operator (interactive + headless) · each managed component (via the
contract) · `radiod`/ka9q-radio · the shared SQLite sink · `hs-uploader` and
upstream endpoints (wsprnet, wsprdaemon, PSKReporter, PSWS) · the Proxmox host ·
the timing authority (`hf-timestd`) · the PSWS charette (#6) as the source of
network-level requirements.

## 5. Assumptions & constraints

- `SIG-C-001` `[DOC]` ✅ The core `smd` SHALL be Python-3.11 **stdlib-only**, so
  it runs from `/usr/local/bin/smd` with no venv; Textual is a lazy import for
  the TUI subcommand only.
- `SIG-C-002` `[DOC]` ✅ Every command SHALL work **headless** (no TTY) for SSH,
  CI, and scripted installs; the TUI is strictly additive.
- `SIG-C-003` `[DOC]` ✅ Paths SHALL be FHS-compliant: config `/etc/sigmond/`,
  state `/var/lib/sigmond/`, logs `/var/log/sigmond/`, binary
  `/usr/local/bin/smd`.
- `SIG-C-004` `[DOC]` ✅ Sigmond SHALL NOT import `wdlib`; it MAY read wsprdaemon
  config but stays a separate tool.
- `SIG-C-005` `[CODE]` ✅ CPU frequency/governor control SHALL be assumed absent
  in the guest (Proxmox-guest model); any freq work happens host-side.
- `SIG-C-006` `[DOC]` ✅ Components SHALL be installed at `/opt/git/sigmond/<name>`
  where `<name>` is the canonical catalog name == directory name.

## 6. Functional requirements

### 6.1 Catalog — "what could be installed?"
- `SIG-F-001` `[DOC]` ✅ Sigmond SHALL maintain a catalog of known components
  answering installability, merged from three layers (discovery from each
  `deploy.toml`, repo-default `etc/catalog.toml`, operator override
  `/etc/sigmond/catalog.toml`).
- `SIG-F-002` `[CODE]` ✅ The merge SHALL be **sparse per-field overlay** (higher
  layers override only the keys they specify), so new repo entries propagate on
  `git pull` with zero per-host sync.
- `SIG-F-003` `[CODE]` ✅ `[deprecated.<name>]` entries SHALL be excluded from
  discovery so a stale on-disk `deploy.toml` cannot revive a retired client.
- `SIG-F-004` `[CODE]` ✅ `smd config catalog-prune` SHALL trim an operator
  override to only diverging fields (with a `.bak`), and run at install end.

### 6.2 Installer — clone + delegate
- `SIG-F-010` `[DOC]` ✅ For a catalog client, sigmond SHALL clone its repo to
  `/opt/git/sigmond/<name>` and invoke the client's **own** `install.sh`; it
  SHALL NOT duplicate client install logic.
- `SIG-F-011` `[CODE]` ✅ C/native projects without an `install_script` (radiod,
  ka9q-web) SHALL be built in-tree by sigmond's dedicated builders.
- `SIG-F-012` `[CODE]` 🟡 `smd install` (no args) SHALL walk catalog+topology;
  current behavior diverges from docs (Project #18 issue #4).

### 6.3 Topology — "what is enabled here?"
- `SIG-F-020` `[DOC]` ✅ Per-host enablement SHALL live in
  `/etc/sigmond/topology.toml`; install SHALL imply enable; the forward path
  SHALL be download→install→configure→start with no mandatory `enable` step.
- `SIG-F-021` `[DOC]` ✅ `smd disable <name>` SHALL take a component offline
  **reversibly** (stop units + clear flag); `smd start <name>` SHALL restore it.
- `SIG-F-022` `[CODE]` ✅ Legacy topology names SHALL resolve via
  `topology_alias` with a deprecation warning.

### 6.4 Lifecycle — operate the running services
- `SIG-F-030` `[DOC]` ✅ Sigmond SHALL resolve systemd units from each client's
  `deploy.toml`, expand templated units, and discover instances.
- `SIG-F-031` `[DOC]` ✅ Start ordering SHALL place radiod first, then clients in
  declaration order; stop SHALL reverse it.
- `SIG-F-032` `[DOC]` ✅ Mutating verbs SHALL acquire a flock lifecycle lock
  (`/var/lib/sigmond/lifecycle.lock`); read-only verbs SHALL be lock-free.
- `SIG-F-033` `[CODE]` ✅ Lifecycle scope SHALL be narrowable by positional
  `<component> [<instance>]`.
- `SIG-F-034` `[NEW]` ⬜ Sigmond SHALL warn when a client declares cross-client
  `After=`/`Requires=` that its ordering model doesn't represent (start-ordering
  validation; #18 Core).

### 6.5 Multi-instance
- `SIG-F-040` `[DOC]` ✅ Sigmond SHALL support multiple reporter instances of one
  client (per-reporter-ID units, per-instance env/config, `reporter_id` row tag).

### 6.6 Harmonization — cross-client safety (the overseer's core value)
- `SIG-F-050` `[DOC]` ✅ Sigmond SHALL run read-only cross-client validation
  (`smd admin validate`) covering at least: CPU-core isolation, radiod
  resolution, frequency coverage vs. sample rate, timing-chain integrity, disk
  budget, channel count, GPSDO governor, kernel rcvbuf, ka9q-python compat,
  data-path upstream, hardware presence (19 rules at baseline).
- `SIG-F-051` `[CODE]` ✅ A new decoder client absent from the CPU `AFFINITY_UNITS`
  map SHALL be detectable rather than silently running on radiod's cores
  (regression guard; #18 Coordination — currently a known gap, 🟡).

### 6.7 Shared sink & upload coordination
- `SIG-F-060` `[DOC]` ✅ Sigmond SHALL provide one local SQLite sink
  (`/var/lib/sigmond/sink.db`) with a `pending_uploads` queue carrying a
  per-row `schema_version`.
- `SIG-F-061` `[DOC]` ✅ Producer-side writing SHALL be **standalone-safe**: a
  no-op writer when the sink is unwritable, so a client never hard-depends on
  the sink.
- `SIG-F-062` `[DOC]` ✅ Cross-process upload signalling SHALL use a **stateless
  edge-trigger** socket; completeness SHALL be re-derived from the durable sink,
  never counted in memory (a lost/dup/reordered ping cannot desync).
- `SIG-F-063` `[CODE]` ✅ A TTL janitor (`smd admin storage trim`) SHALL enforce
  per-target retention with a floor; an upload audit (`smd admin verifier`)
  SHALL report delivery cohorts.

### 6.8 Timing-authority distribution
- `SIG-F-070` `[DOC]` ✅ Sigmond SHALL distribute the timing authority's RTP↔UTC
  offset + tier (contract §18) so subscribers can label data; absence SHALL fall
  back to RTP-default mode (no hard dependency on `hf-timestd`).

### 6.9 CPU arbitration
- `SIG-F-080` `[DOC]` ✅ Sigmond SHALL pin each local radiod to a hyperthread
  sibling pair and confine decoder clients to worker cores via per-template
  affinity drop-ins.
- `SIG-F-081` `[DOC]` ✅ On Proxmox, sigmond SHALL bootstrap the host hookscript
  (sibling-pair discovery, 1:1 vCPU→pCPU pinning, per-core freq caps) as the
  single source of truth; it SHALL NOT add a duplicate systemd freq service.

### 6.10 Status, logging, diagnostics
- `SIG-F-090` `[DOC]` ✅ `smd status` SHALL show systemd state plus each client's
  `inventory --json` enrichment (version, channels, frequencies, modes, issues).
- `SIG-F-091` `[DOC]` ✅ Sigmond SHALL aggregate logs via each client's
  `log_paths` and control runtime log level via `coordination.env` + SIGHUP.
- `SIG-F-092` `[DOC]` ✅ `smd admin diag` SHALL check network reachability,
  pinned-dep drift, per-client self-validation, and service health; `smd watch
  ka9q` SHALL flag upstream ka9q-radio changes that would break RTP delivery.

### 6.11 Environment discovery & TUI
- `SIG-F-100` `[DOC]` ✅ Sigmond SHALL discover network peers (KIWISDRs, GPSDOs,
  NTP) via mDNS/IGMP/HTTP probing.
- `SIG-F-101` `[DOC]` ✅ A Textual TUI (`smd tui`) SHALL expose install, topology,
  logs, validate, CPU, environment, GPSDO, lifecycle, apply, and version screens.
- `SIG-F-102` `[NEW]` ⬜ The TUI SHALL provide per-client config screens with
  live probing (#18 Core) and a CLI-free greenfield bring-up path (#18 Install #16).

### 6.12 Greenfield install / replicability
- `SIG-F-110` `[DOC]` 🟡 Sigmond SHALL bring a bare Proxmox guest to a running
  station with a bounded procedure; the one-command path is gated on #18 Install
  blockers (#7/#14/#16/#17).
- `SIG-F-111` `[CODE]` ✅ Install SHALL be resumable across host reboots via
  persisted state + a resume oneshot.
- `SIG-F-112` `[CODE]` 🟡 A golden-image model (`smd admin personalize`, `smd
  admin secrets`) SHALL support clone-and-personalize; PSWS account/key
  registration remains out-of-band.

## 7. Quality / non-functional requirements

- `SIG-Q-001` `[DOC]` ✅ Mutating operations SHALL be **atomic** under concurrent
  invocation (flock) and **idempotent** (re-running a partial install resumes).
- `SIG-Q-002` `[CODE]` ✅ Upload coordination SHALL be **crash-safe**: restart
  re-derives state from the sink; no in-flight state is lost.
- `SIG-Q-003` `[DOC]` ✅ Contract handling SHALL be **forward/backward compatible**
  across minor contract versions (warn, don't fail, on minor mismatch).
- `SIG-Q-004` `[DOC]` ✅ `inventory`/`validate` subprocesses SHALL be treated as
  untrusted I/O: stdout must be pure JSON; malformed output degrades gracefully.
- `SIG-Q-005` `[CODE]` 🟡 The shared sink SHALL tolerate N concurrent writers at
  cycle boundaries without unacceptable WAL contention (N=4 bench owed; #18).
- `SIG-Q-006` `[DOC]` ✅ Fleet upgrade SHALL propagate by `git pull` alone
  (editable installs); no per-venv reinstall.
- `SIG-Q-007` `[NEW]` ⬜ Every harmonization failure mode SHALL emit an
  operator-actionable message naming the conflicting components.

## 8. External interfaces

### 8.1 Inputs
`etc/catalog.toml` (+ operator `/etc/sigmond/catalog.toml`) · `/etc/sigmond/topology.toml`
· coordination config (`coordination.toml`/`coordination.env`) · each component's
`deploy.toml` and `<client> inventory|validate --json` · per-client `/etc/<client>/` config
· Proxmox host facts (sibling pairs, VMID).

### 8.2 Outputs
Expanded systemd units + drop-ins · the shared sink (`/var/lib/sigmond/sink.db`)
· aggregated status/inventory · logs (`/var/log/sigmond/`) · uploads via
`hs-uploader` to wsprnet / wsprdaemon / PSKReporter / PSWS · the Proxmox
hookscript.

### 8.3 Contracts / APIs
The **sigmond↔component interface requirements** are specified in
[CLIENT-CONTRACT.md](CLIENT-CONTRACT.md) (v0.8) — `inventory`/`validate --json`,
`deploy.toml`, systemd conventions, control sockets, §16 data-path, §17
data-sinks, §18 timing authority. **This document does not restate them.** The
**sigmond↔PSWS interface** is specified in
[PSWS-INTERFACE-BOUNDARY.md](PSWS-INTERFACE-BOUNDARY.md).

## 9. Data requirements

`pending_uploads(target_db, target_table, schema_version, payload_json, queued_at)`
as the producer↔uploader contract · per-target retention policy with a floor ·
timing labels (tier/offset) carried with data per §18 · audit tables for upload
delivery.

## 10. Dependencies & development sequence

Layered build order (each depends on the prior): **Catalog → Installer →
Topology → Lifecycle → Logging → Status/diag enrichment → Contract adapter →
Harmonization → Lifecycle lock → Start ordering → Catalog-walk install → TUI →
Environment discovery → ka9q-watch → Multi-instance (Phase 8)**. Install
orchestration phases A–C are shipped; Phase D (environment-aware, auto-skip
absent-hardware clients) is owed and depends on the contract's `hardware_present`
(§3). This ordering is the *intended* sequence, recovered as requirement, not a
git-log narration.

## 11. Acceptance criteria & verification

- Functional/quality requirements → the pytest suite (`tests/`, 56 files) and
  the 19 harmonization rules (`smd admin validate`).
- Contract conformance of managed clients → `<client> validate --json` surfaced
  through `smd status`/`smd admin diag`.
- Replicable install → a clean Proxmox guest reaching `smd status` all-green
  (the #18 Install epic's exit criterion).
- Each requirement group SHOULD name its verifying test in §13 as coverage grows.

## 12. Risks & open questions

- One-command greenfield is gated on #18 Install blockers — until then `SIG-F-110`
  stays 🟡.
- Multi-writer sink contention (`SIG-Q-005`) unproven at N=4.
- The `rac`/`wd-rac` naming split is resolved at the component axis (now
  `sigmond-rac`) but `wd-rac` service-axis references remain by design — note,
  don't "fix".
- Many `[CODE]` requirements here were never previously written down; this
  baseline is their first capture and SHOULD be reviewed by both owners.

## 13. Traceability

| Requirement | #18 issue | Verification | PSWS #6 |
|---|---|---|---|
| SIG-F-034 (start-ordering validation) | Core: start-ordering | (none yet) | — |
| SIG-F-051 (AFFINITY guard) | Coordination: AFFINITY_UNITS guard | (test owed) | — |
| SIG-F-102 (TUI greenfield) | Install #16 | manual greenfield | — |
| SIG-F-110 (one-command install) | Install: greenfield epic | clean-guest run | #6:25 (registration) |
| SIG-Q-005 (sink N=4) | Coordination: WAL bench | bench owed | — |
| SIG-F-070 (timing distribution) | PSWS: timing-tiering | §18 tests | #6:50 |

*Table grows as requirements link to tests and issues; IDs are the permanent spine.*

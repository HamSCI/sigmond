# Documentation program — three audiences, one front door

**Status:** approved design, 2026-08-23
**Audience:** contributors (this is a spec for doc work, not a user doc)
**Owner:** mjh (AC0G); handoff target is "anyone in the HamSCI org"
**Why this exists:** the suite's ~150 markdown files are developer- and
design-note-flavoured. Three audiences need to proceed *without the
original author*: station hosts, scientists standing up an event listener,
and contributors. This spec fixes what gets written, where it lives, how it
stays true, and in what order.

## 1. Decisions already taken

| Question | Decision |
|---|---|
| Where docs live / how published | Hub in `sigmond/docs/` + per-repo `docs/`; GitHub-rendered markdown; **no** site generator, **no** new repo |
| Audience order | Operator (C) → Scientist (B) → Contributor (A); each phase finishes before the next starts |
| Code changes | **None in this effort.** Gaps become `docs-gap` issues in the owning repo; docs say "today: X — tracked in repo#N" |
| Upkeep | CONTRIBUTING rule + PR-template checklist + a `docs-check` GitHub Action (link check + `smd --help` vs CLI table) |
| Operator install path | Appliance USB image **only**. Bare-Debian `install.sh` is documented for contributors/scientists; the operator guide says "not using the image ⇒ you are a contributor" |

## 2. Structure

```
sigmond/
  README.md                 ← gains a 3-line "Who are you?" block at the top → docs/README.md
  CONTRIBUTING.md           ← stays canonical for how-we-work; gains §14 "Docs travel with behavior"
  docs/
    README.md               ← FRONT DOOR: "I host a station" / "I want to record a signal" / "I work on the code"
    INDEX.md                ← every page in sigmond/docs by audience, ★ = canonical
    operator/               ← audience C narrative (new)
    scientist/              ← audience B narrative (new)
    contributor/            ← audience A narrative (new)
    hardware/               ← shared by B and C (new)
    archive/                ← dated notes / session logs / superseded specs (moved with `git mv`)
    <existing specs>        ← CLIENT-CONTRACT.md, REQUIREMENTS*.md, PSWS-*.md, networking.md … unchanged location
```

Fleet-wide rules (written down in `docs/contributor/docs-conventions.md`):

- **One ★-canonical page per topic.** When two pages disagree the canonical one
  wins (hf-timestd `docs/INDEX.md` convention, adopted everywhere). A duplicate
  becomes a 2-line pointer file; nothing is deleted, so old links don't 404.
- **Header block** on every new or touched page:
  `Audience:` · `Status:` (draft / current / shipped / historical) ·
  `Verified against: <repo> <tag-or-commit> on <date>` · `Canonical for: <topic>`
  (or `See instead: <path>` for pointers).
- **`docs/INDEX.md` in every repo** that lacks one (sigmond, sigmond-appliance,
  wspr-recorder, psk-recorder, meteor-scatter, mag-recorder, gpsdo-monitor,
  hs-uploader, hamsci-dsp). hf-timestd's existing INDEX stays as-is.
- **Cross-repo docs are linked, never moved.** Moves happen only *within* a repo
  into its `docs/archive/`.
- **Naming:** `SCREAMING-KEBAB.md` for specs/contracts, `lowercase-kebab.md` for
  narrative guides. Dated artifacts keep `<TOPIC>-YYYY-MM-DD.md`.

## 3. Operator guide — `sigmond/docs/operator/`

Audience: an amateur radio operator who can burn a USB stick and paste commands
over ssh. No Linux beyond that. Goal: stand up and keep a station running with
nobody on call.

| Page | Role | Content |
|---|---|---|
| `README.md` | TOC + 10-minute version | what you're signing up for: power, internet, antenna, approximate hardware cost, what it uploads where, the weekly 5-minute check |
| `hardware.md` | pointer | → `docs/hardware/shopping-list.md` |
| `install.md` | pointer | → `sigmond-appliance/INSTALL.md` (stays canonical; versioned with the image). Fix its `.img.xz`/Etcher text to match QUICKSTART (raw `.img`, `dd`/Pi-Imager). QUICKSTART.txt gains the hub URL |
| `registration.md` | ★ | PSWS station ID, wsprnet, pskreporter, wsprdaemon: what the wizard did vs what you do on each portal; how to confirm spots arrive. Consolidates `hs-uploader/docs/PER-SITE-SETUP.md` (keeps per-transport mechanics, points here for the narrative), `hf-timestd/docs/PSWS_SETUP_GUIDE.md`, `sigmond/docs/PROVISIONING-INPUTS.md` |
| `day-2.md` | ★ | what healthy looks like (`smd status`, ka9q-web, Proxmox GUI, gmag dashboard); the weekly check; `smd doctor`; how updates work and who decides (station-inward pull vs fleet push, in plain words); reboots / power loss; what the heartbeat tells the fleet |
| `remote-access.md` | ★ | RAC: what it is, what it exposes (host ssh + Proxmox GUI via gw2), enable/disable, who can reach you, the *correct* ssh path (the RAC page's `root@` command is wrong — `PermitRootLogin no`) |
| `troubleshooting.md` | ★ | symptom-first tree: no spots / no uploads / RX888 not found / GPS not locked / mag flat / disk filling / console keyboard dead (normal) / VM won't start; replug vs restart vs reboot vs reinstall; **how to ask for help** (what to paste) |
| `do-not-touch.md` | ★ | never `apt upgrade` the VM; never `smd apply` / `component install ka9q-radio` (reverts the RX888 fork pin); never hand-edit `/etc/radio/radiod@*.conf`; never `sudo smd`; don't touch CPU pinning/grub; never `install.sh` hf-timestd (ipcrm's chrony SHM); relocate only via `sigmond-setup --reconfigure`. Each: why + what happens if you do |
| `glossary.md` | | radiod, SSRC, RTP, GPSDO, PPS, TS-1, PSWS, RAC, VM vs PM, grid square, reporter ID, heartbeat, canary |

Sources: appliance INSTALL.md §11, CONTRIBUTING §3/§13, README §Monitoring/§Debugging,
networking.md, `smd --help`, live `smd status`/`doctor` on b4 and dasi002, and the
ops memory notes (lbe1421, reboot pitfalls, upload paths, update honesty, RAC registrar).

## 4. Scientist guide — `sigmond/docs/scientist/`

Audience: competent Python, knows their signal, has ≤ 1 week. Goal: from "listen on
14.110 MHz from Friday" to a recorder running under sigmond with data leaving the
station.

**Two explicit tiers:**
- **Tier 0 — capture-only.** A standalone script on ka9q-python, own files, own
  upload, no sigmond contract. This is what the Costas listener was.
- **Tier 1 — conformant client.** The `ADD-A-CLIENT.md` path; sigmond installs,
  supervises, and ships it.
The README says which to pick and when to upgrade.

| Page | Role | Content |
|---|---|---|
| `README.md` | path in 5 steps + tiers | time estimates; required reading #1 = `EVENT-CLIENT-PLAYBOOK.md` (stays canonical for design judgment) |
| `station-capabilities.md` | ★ | the envelope in one place: frequency span; sample-rate/bandwidth menu; simultaneous-channel budget and how to measure the load you add (playbook A/B numbers); timestamp accuracy by tier (METROLOGY §4.5 T-tiers; GPSDO holdover 1.44 µs/h; LBE-1421 PPS over USB = liveness only); storage per channel-hour; AGC posture (off + fixed gain; RX888 front-end AGC is real); zero-fill loss semantics (`gap_count` is the honest field) |
| `capture-quickstart.md` | ★ Tier 0 | ~30 lines of ka9q-python: explicit envelope, IQ + GPS_TIME anchors, user systemd unit with watchdog; pre-flight checklist; validate against WWV first. Links GETTING_STARTED/RECIPES for depth |
| `costas-14110-worked-example.md` | ★ | the eclipse listener as narrative: decisions, envelope used, verification (20/21 slots, 14.1 dB), what we'd change |
| `becoming-a-client.md` | ★ Tier 1 bridge | into ADD-A-CLIENT + CLIENT-CONTRACT; **minimal skeleton** at `docs/scientist/skeleton/` (`deploy.toml`, `<name>@.service`, four contract subcommands stubbed, `config/help.toml`) instead of "copy psk-recorder"; sink hand-off via `sigmond.hamsci_sink.Writer`; what hs-uploader ships and doesn't (PSWS transport for new clients → issue) |
| `data-and-timing.md` | ★ | where data lands, naming; RTP↔UTC in scientist terms (RTP is the ruler, GPS_TIME is host clock, T6 is the authority, tier labels on your data); the non-atomic anchor-pair caveat |

## 5. Contributor guide — `sigmond/docs/contributor/`

| Page | Role | Content |
|---|---|---|
| `README.md` | reading order | CONTRIBUTING → orchestration → architecture diagram → CLIENT-CONTRACT → one client's 6-file skeleton → appliance pipeline; with time estimates |
| `orchestration.md` | ★ | how sigmond works in one page: layers (installer / lifecycle / catalog / sink / timing-authority / TUI / fleet); **`smd` subcommand → module table** (the CI-checked table); production paths; unit naming; client discovery (catalog + `deploy.toml`); update orientations; heartbeat/board. Consolidates CLAUDE.md + README + MULTI-INSTANCE + RADIOD-IDENTIFICATION by *linking* |
| `appliance-boundary.md` | ★ | golden VM bakes vs `install.sh` at first boot vs wizard; version provenance (`/etc/sigmond-appliance/version` is install-time only); how a change reaches a station (tag → `smd update`, or → next golden build). Links `sigmond-appliance/docs/RELEASE.md` |
| `dev-setup.md` | ★ | dev venv (today README:457), running tests, graphify, nested-test rig, B3 as build host |
| `client-authoring.md` | pointer+rule | ADD-A-CLIENT + CLIENT-CONTRACT + scientist skeleton + per-client 6-file docs convention + REQUIREMENTS-TEMPLATE; rule: a client's `docs/` must be true for *that* client (**meteor-scatter's docs are truthed in this effort** — it is a verbatim stale psk-recorder copy) |
| `docs-conventions.md` | ★ | the §2 rules |
| `docs-gap-ledger.md` | working file | running list of software gaps found while writing; becomes the issue batch |

**Archive policy.** In each repo: dated investigation reports, session logs, plans
marked complete/superseded, and design notes whose design has shipped *and* which
are no longer the best explanation → `git mv` to `<repo>/docs/archive/` with a
one-paragraph `docs/archive/README.md` ("historical; may contradict current docs;
canonical pages win"). Candidate list = the audit's §3 (~30 files across sigmond,
hf-timestd, mag-recorder, wspr/psk, gpsdo-monitor, ka9q-python). Every candidate
is reviewed individually; anything still referenced by a live page or an ops memory
note leaves a pointer behind. Design notes that describe **shipped** architecture
and remain the best explanation stay live with `Status: shipped`
(MULTI-INSTANCE-ARCHITECTURE, RADIOD-IDENTIFICATION, PRODUCER-THREAT-MODEL,
PACKET-LOSS-DIAGNOSTICS).

Known contradictions fixed in Phase 0: burn instructions (`.img.xz` vs raw);
wspr-recorder CLAUDE.md contract v0.7 vs v0.8; the duplicate
`T6-BLOCK-SLIP-ROOT-CAUSE-2026-08-10.md` (sigmond's 27-line copy becomes a pointer
to hf-timestd's); triplicated hardware list (README / install-quickstart /
installation-guide → pointer to `hardware/shopping-list.md`).

## 6. Hardware — `sigmond/docs/hardware/`

| Page | Audience | Content |
|---|---|---|
| `shopping-list.md` | ★ operator | parts with model numbers: mini-PC floor (cores/RAM/NVMe), RX888 Mk II, Leo Bodnar LBE-1421, TS-1 injector, RM3100 + Pololu USB-I²C, antenna guidance (DXE reference), cabling (10 MHz + PPS → RX888; USB topology that matters), ≥16 GB stick; required vs optional per science product (no GPSDO ⇒ which products degrade); approximate cost; "what B4 actually runs" as the known-good build |
| `character.md` | ★ scientist/contributor | how the hardware behaves, each claim with its evidence link: RX888 dynamic range + front-end AGC (real, hunts 12.75–22.55 dB; off for science); 20 ms block deadline + zero-fill on USB starvation (gaps are radiod block-drops; `gap_count` honest, byte counts lie); USB sample loss ⇒ RTP↔GPS steps + re-anchor watchdog; VBUS reset needs power-off; GPSDO roles (10 MHz → RX888 clock; PPS → TS-1 → T6), holdover 1.44 µs/h, LBE-1421 PPS over USB = liveness only; TS-1 injection and what T6 gives; RM3100 NACK failure (frozen constant; replug ⇒ restart mag-recorder); Proxmox/CPU pinning consequences (radiod on its own core; why the host keyboard dies) |

Both cross-link `gpsdo-monitor/README.md` (LBE matrix), `mag-recorder/docs/PROVENANCE.md`,
`ka9q-radio/docs/SDR/rx888.md`, `hf-timestd/docs/METROLOGY.md` — no duplication.

## 7. Upkeep

- **CONTRIBUTING.md §14 "Docs travel with behavior":** any PR touching a CLI surface,
  config key, systemd unit, file path, wizard prompt, or observable behavior must touch
  the canonical page or state "no doc impact". References `docs-conventions.md`.
- **`.github/PULL_REQUEST_TEMPLATE.md`** in sigmond, sigmond-appliance, and each client
  repo: ☐ docs updated ☐ no doc impact ☐ `Verified against:` bumped on touched pages.
- **`docs-check` GitHub Action** — one reusable workflow in sigmond
  (`.github/workflows/docs-check.yml`, `workflow_call`), called from each repo:
  (a) `lychee` over `**/*.md`: internal + cross-repo GitHub links must resolve;
  external links warn only. (b) sigmond only: a pytest that runs `smd --help` and each
  subcommand's `--help` and asserts the set of subcommands equals the set in
  `docs/contributor/orchestration.md`'s CLI table (both directions).
  Proven on a personal-fork branch before it lands on `main` as a normal commit
  (develop-on-main stands; no long-lived branch).
- Staleness is **visible, not enforced**: the `Verified against:` line lets a reader
  judge trust. No automated expiry.

## 8. Issue filing

Every software gap found while writing → issue in the owning repo, label `docs-gap`,
body = what the doc wanted to say / what is true today / the page that links it. The
doc says "today: X — tracked in repo#N". Candidates already known: no single `smd`
command reporting attached hardware; no client scaffold command; PSWS transport for
new clients; RAC page's wrong `root@` command; GRAPE uploader bypassing `[uploads]`
policy. Issues are filed **in a batch at the end of each phase** from
`docs-gap-ledger.md`, after the owner has seen the list.

## 9. Verification

1. **Claim-level:** every command, path, flag, number in an operator/scientist page is
   checked against a live host (b4 / dasi002 via the read-only fleet fan-out,
   `smd --help`, real files) or code at a named commit — never against memory notes
   alone. The `Verified against:` footer records which.
2. **Walk-through:** a fresh-context subagent given only `docs/operator/` + ssh to
   dasi002 performs the day-2 check, the troubleshooting tree for a seeded symptom, and
   the registration confirmation, reporting every guess. Likewise a fresh agent given
   only `docs/scientist/` must produce a running Tier-0 capture on dasi002 (plumbing
   testbed; no antenna — verify pipeline, not signal). Guesses become doc fixes.
3. **Structural:** `docs-check` run locally before each commit.

Not done in this effort: no production service restarts; no appliance burn/boot on B4.
The install path stays verified by the last real burn (v3.34) + the nested rig, and
the footer says so.

## 10. Phasing

Each phase ends with reviewable commits, the gap ledger, and the walk-through report.
Phases 1–3 each get their own implementation plan; stopping after any phase leaves a
coherent result.

| Phase | Scope | Done when |
|---|---|---|
| 0 — scaffold | front door, four dirs, `docs-conventions.md`, per-repo `INDEX.md` stubs, archive moves (file-by-file), the §5 contradictions fixed | every repo has an INDEX; no dated note sits in a user-facing docs root; links green |
| 1 — operator | §3 pages + `hardware/shopping-list.md` + INSTALL.md/QUICKSTART link-up + walk-through | a ham with the stick and the hub URL needs nobody |
| 2 — scientist | §4 pages + `hardware/character.md` + skeleton + Costas example + walk-through on dasi002 | a fresh agent gets a Tier-0 capture running from the docs alone |
| 3 — contributor | §5 pages, meteor-scatter docs truthed, CONTRIBUTING §14, PR templates, `docs-check` CI proven and rolled | CI green in every repo; `smd --help` ⇔ CLI table |
| 4 — close | issues filed from the ledger, `graphify update`, final link sweep, "state of the docs" note in the front door | ledger empty; front door lists open issues |

## 11. Out of scope

Code changes of any kind (see §1); a rendered docs site; a new repo; restructuring
hf-timestd's docs (its INDEX already follows the convention); rewriting
CLIENT-CONTRACT.md; ops-repo (private) docs beyond linking.

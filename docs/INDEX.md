# sigmond documentation index

> **Audience:** all
> **Status:** current
> **Verified against:** sigmond 4aec0c2 on 2026-08-23 — code
> **Canonical for:** the map of sigmond/docs

Reading-order map. ★ = canonical — when two docs disagree, the ★ one wins.
Front door for each audience: [docs/README.md](README.md).

## 0. Front door

| Doc | What it gives you |
|---|---|
| [README.md](README.md) | The front door — pick your audience (operator / scientist / contributor) and it sends you to the right subtree |

## 1. Operator (host a station)

| Doc | What it gives you |
|---|---|
| [operator/README.md](operator/README.md) ★ | The operator's table of contents: what hosting a station involves, the ordered path from parts to weekly check, the two-machines-in-one-box (`[host]` vs `[VM]`) rule, and what to send when asking for help |
| [operator/glossary.md](operator/glossary.md) ★ | Plain-English definition of every term the operator pages use, alphabetical |
| [operator/registration.md](operator/registration.md) ★ | Getting a station's uploads accepted: which of the four networks need registration (only PSWS does), the PSWS account→site→instrument→key walkthrough, how to confirm each product arrived and how long to wait |
| [operator/day-2.md](operator/day-2.md) ★ | Day-2 operation: the four web windows and what "good" looks like in each, the weekly four-step check with an annotated real `smd status` (which ✗ and ⚠ are normal and why), the disk numbers that cost you data, how updates are decided and run, power loss and reboots, the heartbeat, and passwords |
| [operator/remote-access.md](operator/remote-access.md) ★ | Remote access (RAC): what the outbound tunnel is, that it runs on the Proxmox host and not the VM, the four channels it exposes and who can reach them, turning it on/off/checking from the host, why the dashboard's `root@` ssh command cannot work, and what to do when the install says FAILED |
| [operator/troubleshooting.md](operator/troubleshooting.md) ★ | Symptom-first troubleshooting: the 2-minute triage, fourteen symptoms each with likely causes / what to check / what to do / when to stop and ask, the replug-restart-reboot-reinstall ladder, exactly what to send when asking for help, and what not to touch while chasing a fault |
| [operator/do-not-touch.md](operator/do-not-touch.md) ★ | The guard rails: the seventeen things an operator must not do on a station — `sudo smd`, `apt`/`pip`/`git pull`, a client's own `install.sh`, `smd install ka9q-radio`, `smd apply`, `smd config <client> edit`, hand-edits to `/etc/radio` or `/etc/sigmond`, CPU/VM changes, moving without `--reconfigure` — each with the cited reason and what to do if you already did it, plus what you may do freely |
| [operator/hardware.md](operator/hardware.md) (pointer) | Redirects to `hardware/shopping-list.md` — the parts list is shared with the scientist path |
| [operator/install.md](operator/install.md) (pointer) | Redirects to sigmond-appliance's `INSTALL.md`, plus what to expect from the ~45-minute install |

## 2. Scientist (record a signal)

| Doc | What it gives you |
|---|---|
| [scientist/README.md](scientist/README.md) ★ | The scientist's table of contents: the five-page reading order with time estimates, the Tier 0 vs Tier 1 table, what to get from the fleet admin before you touch a station, what the station will not do for you, and links to the worked example and the hardware page |
| [EVENT-CLIENT-PLAYBOOK.md](EVENT-CLIENT-PLAYBOOK.md) ★ | Decision playbook for standing up a receiving client on short notice (eclipse, meteor shower, unannounced experiment) |
| [ADD-A-CLIENT.md](ADD-A-CLIENT.md) | Checklist for writing a new contract-conformant client repo so it appears in `smd list`/the TUI |
| [STATION-NETWORK-CAPABILITIES.md](STATION-NETWORK-CAPABILITIES.md) | What a coordinated mesh of DASI2 stations delivers scientifically — spatial + modal diversity, resilience to losing any one transmitter |
| [SCINTILLATION-MONITORING.md](SCINTILLATION-MONITORING.md) | Design note + implementation plan for adding S4/σ_φ/Doppler ionospheric-scintillation observables |
| [scientist/station-capabilities.md](scientist/station-capabilities.md) ★ | The DASI2 station capability envelope for a new client: the frequency/preset/sample-rate/encoding menu radiod will actually serve, how many channels you may add and how to measure the load, the T6–T1 timing tiers and where the tier is recorded, storage per channel-hour with the measured tie-point, the front-end AGC caveat, what "a gap" means when radiod zero-fills, and what the station cannot do |
| [scientist/capture-quickstart.md](scientist/capture-quickstart.md) ★ | The Tier-0 "capture first" recipe: what to settle before you touch a station, `event-recorder` with a job TOML as the fast path, and a complete one-file ka9q-python recorder — proven live on DASI002 — that sets a mandatory `lifetime`, re-polls the encoding `ensure_channel` returns (it can be stale) and then measures the wire format anyway, and writes a sidecar pinning sample 0 to UTC; the optional add-on that records what that timestamp is *worth* (tier, σ, judge age); plus the WWV known-signal check, running it unattended with a watchdog, and where not to write on a production station |
| [scientist/costas-14110-worked-example.md](scientist/costas-14110-worked-example.md) ★ | The 2026-08-12 eclipse Costas listener end to end, as the worked example a next scientist copies: the one-day ask, why the envelope was 14.110 MHz I/Q ±5 kHz at 12 kHz float32, the WWV known-signal check and the wire-format probe that replaced it, the external stall watchdog and what it caught, a timeline reconstructed from the station's own logs, the 22.23 h / 7.68 GB / 26-segment archive, the "no detection" verdict of 08-12 overturned on 08-13 by testing the permutation instead of the energy, the 24 null hours that make it a result, what the capture cannot show (no eclipse effect, no confirmed TX schedule or locations), and the eight things worth doing differently |
| [scientist/becoming-a-client.md](scientist/becoming-a-client.md) ★ | Tier 1 — turning a capture into a sigmond client: when graduating is worth it (and the eclipse listener that rightly stayed Tier 0), the seven things you must ship with a scientist's gloss on each, how `smd component add` + `smd install` put a repo on a station and why the fleet admin runs them, the permanent `[[radiod.fragment]]` vs dynamic-channel-with-`lifetime` decision, writing rows to the shared sink with `Writer.from_env` (including the hardened-unit trap that silently drops every row), declaring an hs-uploader pipeline and the transports that actually exist — with the PSWS dead end for a new client's product stated plainly — reporting your timing tier honestly under §18, `reporter_id` under §19, and the four checks that say you are done |
| [scientist/skeleton/README.md](scientist/skeleton/README.md) | The copyable minimal client scaffold — six MIT files (`deploy.toml`, templated unit, `cli.py` with the four contract verbs, `pyproject.toml`, `config/help.toml`, this README) that run on a laptop with stdlib Python and install editable into a venv, with the real output of every verb, what is stubbed, and the one required file (`install.sh`) it cannot guess |
| [scientist/data-and-timing.md](scientist/data-and-timing.md) ★ | Where a station's data lands and what its timestamps are worth: the three sink shapes and which one is yours, the live layout on b4 (hf-timestd's five-minute raw-buffer pairs and their products, SigMF event captures, the one `pending_uploads` table every client writes to) with the naming conventions worth copying, the clock story in five sentences — including the loop in which radiod's `GPS_TIME` is a host clock that chrony disciplines from hf-timestd's own feed — the tiers in one line each, how to stamp your own capture with `rtp_to_utc()` and the five fields to record per segment, RTP-default vs authority-corrected mode under §18, hf-timestd's sidecar as the reference implementation with its arithmetic reproduced, and six silent ways a well-formed archive turns out to be wrong |

## 3. Contributor (work on the code)

| Doc | What it gives you |
|---|---|
| [../CONTRIBUTING.md](../CONTRIBUTING.md) ★ | Working agreements: where work happens, host updates, pins, deploy-tree hygiene, PR expectations, graphify maintenance |
| [contributor/orchestration.md](contributor/orchestration.md) ★ | How sigmond works in one page: the 13 architecture layers and their modules, production paths, catalog/topology discovery, and the CI-checked `smd` verb→module map |
| [contributor/appliance-boundary.md](contributor/appliance-boundary.md) ★ | The appliance ↔ sigmond boundary: what the golden VM bakes vs what first boot does vs what the wizard sets, version provenance (image lineage vs `smd version` vs the blessed manifest), and which changes reach a station by `smd update` and which need a new image |
| [contributor/dev-setup.md](contributor/dev-setup.md) ★ | Build and test the suite: prereqs, sibling-checkout clone layout, the dev venv (`uv sync` + the `dev-setup.sh` pip fallback), running `smd` from the tree, the canonical test runner and what CI runs (with the unittest-discovery trap), the docs checks, and pointers to graphify, native binaries, and the appliance rig |
| [contributor/client-authoring.md](contributor/client-authoring.md) | The route through the client-authoring documents (becoming-a-client → ADD-A-CLIENT → CLIENT-CONTRACT → REQUIREMENTS-TEMPLATE → the skeleton), the two registrations that fail silently (`AFFINITY_UNITS`, `[client_features]`), the six-file per-client docs skeleton, and the rule that a client's own `docs/` must be true for that client — with meteor-scatter's copied-and-stale docs as the cautionary example |
| [contributor/docs-conventions.md](contributor/docs-conventions.md) ★ | How the docs tree is organised and kept true: header block, ★-canonical rule, audience split |
| [contributor/docs-gap-ledger.md](contributor/docs-gap-ledger.md) | Running ledger of software gaps discovered while documenting, feeding per-repo issues |
| [contributor/README.md](contributor/README.md) (stub — Phase 3) | Placeholder — real contributor guide not yet written; points to docs-conventions/CLIENT-CONTRACT/ADD-A-CLIENT/CONTRIBUTING until then |
| [CLIENT-CONTRACT.md](CLIENT-CONTRACT.md) ★ | The sigmond↔component interface contract — the authoritative statement of the integration surface every client implements |
| [REQUIREMENTS.md](REQUIREMENTS.md) | Sigmond-overseer-only requirements baseline (retroactive v0.1); the suite-wide frame, not the component seam |
| [REQUIREMENTS-INDEX.md](REQUIREMENTS-INDEX.md) | Front door to the whole suite's requirements baseline, one row per component repo |
| [REQUIREMENTS-TEMPLATE.md](REQUIREMENTS-TEMPLATE.md) | Standard template + method every SigMonD-suite requirements document fills |
| [CLI-V2-SPEC.md](CLI-V2-SPEC.md) | Verb-shape, cluster-boundary, and alias-keep spec for the next major `bin/smd` CLI rework |
| [MULTI-INSTANCE-ARCHITECTURE.md](MULTI-INSTANCE-ARCHITECTURE.md) ★ | Per-reporter-instance shape all recorder clients and the sigmond substrate must converge on |
| [RADIOD-IDENTIFICATION.md](RADIOD-IDENTIFICATION.md) ★ | Canonical radiod multicast-naming model every sigmond-suite client relies on |
| [networking.md](networking.md) ★ | Diagnoses the IGMP-querier silent-failure mode that kills multi-host multicast |
| [native-binaries.md](native-binaries.md) | Convention for how a client ships/builds native (C/C++/Fortran) deps it depends on (dumphfdl, mag-usb, wsprd/jt9, PHaRLAP) |
| [greenfield-runbook.md](greenfield-runbook.md) ★ | End-to-end bare-host-to-recording-station operational walkthrough: order of operations + host tuning |
| [install-quickstart.md](install-quickstart.md) | Short operator-focused guide to getting `smd` running via `install.sh` |
| [installation-guide.md](installation-guide.md) | Fuller installation guide: Proxmox passthrough, networking, host capacity |
| [install-redesign.md](install-redesign.md) | Station patterns, hardware-aware install, install-implies-enable vocabulary, 3-step IA |
| [install-orchestration-design.md](install-orchestration-design.md) | TUI-driven, contract-ordered bring-up design (phases A–C, shipped) |
| [HOST-CAPACITY-PLANNING.md](HOST-CAPACITY-PLANNING.md) | Open design discussion on workload tiers and matching client cost to host topology |
| [CAPACITY-MEASUREMENT-PLAN.md](CAPACITY-MEASUREMENT-PLAN.md) | Executable measurement plan that answers HOST-CAPACITY-PLANNING's open questions with numbers |
| [PRODUCER-THREAT-MODEL.md](PRODUCER-THREAT-MODEL.md) ★ | What threatens radiod's core data production, what currently defends it, and what's still exposed |
| [PACKET-LOSS-DIAGNOSTICS.md](PACKET-LOSS-DIAGNOSTICS.md) ★ | Six-layer diagnostic loop for tracking RTP sequence gaps from kernel UDP buffer to USB starvation |
| [timing-chain-architecture.md](timing-chain-architecture.md) | GPSDO → gpsd → chrony → hf-timestd timing-stack failure analysis and the idempotent-recovery design |
| [PSWS-MAPPING.md](PSWS-MAPPING.md) | Durable traceability map between the HamSCI PSWS Design Charette board and the sigmond implementation |
| [PSWS-INTERFACE-BOUNDARY.md](PSWS-INTERFACE-BOUNDARY.md) | Defines exactly where the sigmond⇄PSWS line is for board items shared between the two efforts |
| [PSWS-HEARTBEAT-SPEC.md](PSWS-HEARTBEAT-SPEC.md) | Proposed station→PSWS heartbeat interface spec (health/availability/provenance) — plan, not implemented |
| [PROVISIONING-INPUTS.md](PROVISIONING-INPUTS.md) | Every unique-per-installation input, credential, and manual action needed to stand up a host, both deployment models |
| [proxmox/CLAUDE.md](proxmox/CLAUDE.md) | Index for the Proxmox host-setup docs — what to read before touching VM/VFIO/CPU-pin config |
| [proxmox/wsprdaemon-proxmox-bios-checklist.md](proxmox/wsprdaemon-proxmox-bios-checklist.md) | Standalone BIOS checklist meant to be carried to the Proxmox host physically |
| [proxmox/wsprdaemon-proxmox-cpu-clock-tuning.md](proxmox/wsprdaemon-proxmox-cpu-clock-tuning.md) | CPU isolation, hyperthread-pair exposure, and clock-accuracy tuning for the Proxmox VM |
| [proxmox/wsprdaemon-proxmox-vm-setup.md](proxmox/wsprdaemon-proxmox-vm-setup.md) | Setting up the Proxmox VM + RX-888 USB passthrough for bare-metal SDR performance |
| [superpowers/specs/](superpowers/specs/) | Design specs written for planning documentation-program-style work |
| [superpowers/plans/](superpowers/plans/) | Implementation plans (task-by-task) for documentation-program-style work |
| [METEOR-SCATTER-DESIGN.md](METEOR-SCATTER-DESIGN.md) (pointer) | Redirects to `archive/METEOR-SCATTER-DESIGN.md`; kept because `meteor-scatter/docs/REQUIREMENTS.md` cites its §3/§7 |
| [PHASE-D-SERVER-MERGE-ENDPOINT.md](PHASE-D-SERVER-MERGE-ENDPOINT.md) (pointer) | Redirects to `archive/PHASE-D-SERVER-MERGE-ENDPOINT.md`; kept because `psk-recorder/docs/REQUIREMENTS.md` links here by URL |
| [TUI-FUNCTION-INVENTORY.md](TUI-FUNCTION-INVENTORY.md) (pointer) | Redirects to `archive/TUI-FUNCTION-INVENTORY.md`; kept because it's named in `lib/sigmond/tui/widgets/component_tree.py` and a test |
| [T6-BLOCK-SLIP-ROOT-CAUSE-2026-08-10.md](T6-BLOCK-SLIP-ROOT-CAUSE-2026-08-10.md) (pointer) | Redirects to the canonical hf-timestd analysis; keeps only the local record of the B4 layer-1 mitigations deployed 2026-08-10 |

## 4. Hardware

| Doc | What it gives you |
|---|---|
| [hardware/shopping-list.md](hardware/shopping-list.md) ★ | The station parts list: required vs optional with model numbers, what each optional part buys you, cabling, approximate cost, and the known-good build AC0G/B4 runs |
| [hardware/character.md](hardware/character.md) ★ | How the station hardware *behaves*: the RX888's one 16-bit converter and its live front-end AGC, the 20 ms deadline and zero-fill, USB sample loss stepping the timing anchor, the FX3 latch, what the GPSDO provably disciplines, the TS-1 and T6, the magnetometer's frozen-constant failure, and the timing-chain caveats (anchor pair, encoding grant, shared-frequency stations) |

## 5. Archive

| Doc | What it gives you |
|---|---|
| [archive/README.md](archive/README.md) | Archive policy note (the files sit beside it) — dated investigation reports, session logs, and superseded/shipped design notes kept for provenance only; nothing here is maintained |

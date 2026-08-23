# sigmond documentation index

> **Audience:** all
> **Status:** current
> **Verified against:** sigmond 78d9a8b on 2026-08-23 — code
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
| [EVENT-CLIENT-PLAYBOOK.md](EVENT-CLIENT-PLAYBOOK.md) ★ | Decision playbook for standing up a receiving client on short notice (eclipse, meteor shower, unannounced experiment) |
| [ADD-A-CLIENT.md](ADD-A-CLIENT.md) | Checklist for writing a new contract-conformant client repo so it appears in `smd list`/the TUI |
| [STATION-NETWORK-CAPABILITIES.md](STATION-NETWORK-CAPABILITIES.md) | What a coordinated mesh of DASI2 stations delivers scientifically — spatial + modal diversity, resilience to losing any one transmitter |
| [SCINTILLATION-MONITORING.md](SCINTILLATION-MONITORING.md) | Design note + implementation plan for adding S4/σ_φ/Doppler ionospheric-scintillation observables |
| [scientist/README.md](scientist/README.md) (stub — Phase 2) | Placeholder — real scientist guide not yet written; points to EVENT-CLIENT-PLAYBOOK.md and ka9q-python's Getting Started guide until then |

## 3. Contributor (work on the code)

| Doc | What it gives you |
|---|---|
| [../CONTRIBUTING.md](../CONTRIBUTING.md) ★ | Working agreements: where work happens, host updates, pins, deploy-tree hygiene, PR expectations, graphify maintenance |
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

## 5. Archive

| Doc | What it gives you |
|---|---|
| [archive/README.md](archive/README.md) | Index of 6 dated investigation reports, session logs, and superseded/shipped design notes kept for provenance only — nothing here is maintained |

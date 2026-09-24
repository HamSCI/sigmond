# Keeping an installed station at the blessed image — `smd align`

> **Audience:** contributor, fleet administrator
> **Status:** design — approved in conversation 2026-09-24, awaiting review of this text
> **Spans:** sigmond (`smd align`, the fleet board's level column), sigmond-appliance (`pm-align`, the nested test's alignment check, the published release manifest)
> **Related:** station identity across a reflash (`sigmond-appliance/docs/superpowers/specs/2026-09-24-station-identity-design.md`)

## 1. The problem

A station gets its software from a USB stick once.  After that nothing brings it forward to a newer image as a whole.

AC0G-ND shows the gap.  It came up on v3.36 and still records that release in `/etc/sigmond-appliance/version`, while v3.53 stands blessed.  Its components have wandered to whatever someone last pulled; its image-carried helpers, units and fixups still date from v3.36; its Proxmox host carries v3.36's wizard and never got the host heartbeat that v3.53 sets up.  A script written against today's `smd` failed on ND's older one.  "What level is ND at?" has no single answer.

A reinstall answers it, at the price of a site visit or a 5 GB download, a wiped identity and wiped site settings.  AC0G-B4's reflash on 2026-09-23 cost a day of repair for exactly those reasons.  For the stations bound for McMurdo, where the link carries bandwidth caps and daily quotas, a whole image does not travel at all.

Most of the machinery already exists.  `smd update` moves components forward and logs what it did; `smd admin manifest restore` moves them to a manifest's pins, fail-closed; `fleet_update()` knows canary-first ordering; the heartbeat already reports manifest drift to the board.  What nothing does: learn the latest blessed release, move a station to *it* rather than to each component's `origin/main`, refresh what the image itself carries, and say plainly whether the result works.

## 2. Decisions

Decided with mjh, 2026-09-24:

- **The target is the latest blessed release** — its manifest's pins and its image-carried files.  Not `origin/main`: a station should run a combination the nested test has seen.
- **Update in place is the normal path.**  Replacing the VM stays the fallback for a VM too damaged to repair.  (The host keeps no template today — B4's holds only VM 100 — so a replacement means the full image download.  Keeping a compressed template on the host at install would make replacement a local clone; noted, not in scope.)
- **The Proxmox host updates on demand.**  The VM reports when the host falls behind; a separate, explicit host step brings it forward.
- **Online first.**  McMurdo reaches GitHub, slowly.  The offline delta bundle waits until a site needs it.
- **Bandwidth is a budget.**  The dry run states the cost before anything moves.

## 3. `smd align` on the VM

Dry-run by default.  `smd align --apply` acts.

1. **Find the target.**  Fetch the latest blessed release's manifest from its GitHub Release (the v3.53 Release publishes `…manifest.txt`, 1,265 bytes, readable without credentials).  `--release vX.YY` names an earlier blessed release instead, which is how a rollback runs.
2. **Align sigmond first, then run as the target.**  Move the sigmond checkout to its pin and re-execute `smd align` under the new code.  Every later step then runs as the target release's code, whatever release the station started from.
3. **Plan and cost.**  For each other component: current commit, pinned commit, the bytes a fetch would bring, and the packages its `install.sh` would download.  Then the image-carried files that differ from the release's, a byte total, and whether the host sits behind.  The dry run prints this and stops.
4. **Apply, one component at a time.**  Fetch, check out the pin, run `install.sh` as the checkout's owner (never as root inside a checkout root does not own), write the `.pin` and exclude it from git.  A dirty or hand-modified checkout refuses to move.  Stop at the first failure and name it.
5. **Refresh the image-carried files** from the sigmond-appliance tree at the release tag: helper scripts, units, fixups.
6. **Re-run what bring-up runs:** `sigmond-site-timing`, `smd config render`, the uploader manifest, `smd doctor --fix`.
7. **Record.**  Write the release's `manifest.txt`, stamp the version file ("aligned to v3.53 at …", beside the install-time lineage), and append each step to `/var/lib/sigmond/update-history.jsonl`.
8. **Verify by production.**  Measurements arriving, uploads clean, a fresh heartbeat delivered.  Components that moved while measurements stopped is a failure, and `align` reports it as one.

**radiod.**  When ka9q-radio's pin moves, `align` rebuilds it and restarts in the known-safe order: build, restart radiod, then restart every consumer — a recorder left running across a radiod restart reports healthy and produces nothing.  The dry run says so in plain words: `RESTARTS radiod — recording gap of about N s`.

**Capped links.**  `--max-bytes N` refuses a plan that exceeds the budget; `--limit-rate R` throttles the fetches.  Each component forms its own step, so a site can spread an alignment across several days' quotas.

**Resuming.**  Every step checks before it acts, so re-running `smd align --apply` after a failure picks up where it stopped.

## 4. `pm-align` on the Proxmox host

The host carries no `smd` — only `python3` and a shell — so its step stays a standalone script, as the identity module and the host heartbeat emitter do.

- **The VM reports; it never acts on the host.**  `smd align` compares the host's recorded release with the blessed one and says: "host on v3.36; v3.53 available — run `pm-align` on the host".
- **`pm-align`, dry-run by default:**
  1. fetch the release's appliance tree at its tag — scripts only, a few hundred KB;
  2. show what would change in `/root/sigmond-appliance`: wizard, host heartbeat emitter, RAC client config, host-tuning scripts, network helpers;
  3. `--apply`: replace those files, then re-run the host's idempotent setup steps whose inputs changed — `pm-heartbeat-setup`, and the RAC registration once station identity Plan 3 lands.
- **It never touches Proxmox itself** (apt owns that), **the host's network, or its CPU / IRQ / cache tuning** without an explicit flag.  Those take a station off the air.
- The host heartbeat reports the host's release, so the board can show a host behind too.

## 5. Who runs it

- **A DASI site user:** `smd align`, read the plan, then `smd align --apply`, on their own station.
- **A fleet administrator:** `ops/bin/fleet-ssh <station> 'smd align --apply'` — one named station at a time.  The fleet's own fan-out stays read-only by construction (`fleet.READ_ONLY_COMMANDS`); the mutation remains a per-host act.
- **The fleet board** gains a level column: each station's aligned release against the latest blessed, e.g. `v3.36 → v3.53 available`, for the VM and, once its heartbeat reports it, the host.

## 6. Testing

Each test counts only after someone has watched it fail.

1. **Unit tests** for the planner, the cost accounting, a pin move, each refusal (dirty tree, budget exceeded, unknown release), and resume after a failure.
2. **The nested test gains a real alignment:** install v3.52, run `smd align --apply` to v3.53, then assert what a fresh v3.53 install must pass — metrology running and producing, the heartbeat timer enabled and a heartbeat delivered, zero doctor findings.
3. **AC0G-ND, the first live alignment,** v3.36 → v3.53:
   1. capture ND's identity bundle (`ops/bin/site-identity capture ac0g-nd`);
   2. announce on the claude-bus;
   3. run the dry run and read its plan and costs;
   4. apply;
   5. verify by production — T3 measurements, fusion, WSPR / PSK / GRAPE uploads, the heartbeat on the board.
   Then `pm-align` on ND's host, which brings its host heartbeat.

## 7. Order of work

1. `smd align` on the VM, with the nested test's alignment check beside it.
2. `pm-align` on the host.
3. The board's level column.
4. ND.

## 8. Out of scope, and why

- **The offline delta bundle** (`smd align --from <bundle>`): the devbox builds a bundle of exactly what one station lacks — git deltas and Python wheels, measured to the byte — for a site whose quota cannot take even the online path.  First site that needs it gets it.
- **OS packages.**  Debian and Proxmox updates stay with apt and their own delta mechanics.
- **Local-clone replacement.**  Keeping the golden template on the host at install would let a damaged VM be replaced without a download.  Worth a line in the install design; not this one.

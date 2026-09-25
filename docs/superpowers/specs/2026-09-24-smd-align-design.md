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
4. **Apply, one component at a time.**  Fetch, check out the pin, run `install.sh` as root (it installs system units), followed by an ownership repair of the egg-info it builds inside the checkouts, write the `.pin` and exclude it from git.  A dirty or hand-modified checkout refuses to move.  Stop at the first failure and name it.
5. **Refresh the image-carried files** from the sigmond-appliance tree at the release tag: helper scripts, units, fixups.
6. **Re-run what bring-up runs:** `sigmond-site-timing`, `smd config render`, the uploader manifest, `smd doctor --fix`.
7. **Record.**  Write the release's `manifest.txt`, stamp the version file ("aligned to v3.53 at …", beside the install-time lineage), and append each step to `/var/lib/sigmond/update-history.jsonl`.
8. **Verify by production.**  Measurements arriving, uploads clean, a fresh heartbeat delivered.  Components that moved while measurements stopped is a failure, and `align` reports it as one.

**radiod.**  When ka9q-radio's pin moves, `align` rebuilds it and restarts in the known-safe order: build, restart radiod, then restart every consumer — a recorder left running across a radiod restart reports healthy and produces nothing.  The dry run says so in plain words: `RESTARTS radiod — recording gap of about N s`.

**Capped links.**  `--max-bytes N` refuses a plan that exceeds the budget; `--limit-rate R` throttles the fetches.  Each component forms its own step, so a site can spread an alignment across several days' quotas.

**Resuming.**  Every step checks before it acts, so re-running `smd align --apply` after a failure picks up where it stopped.

### Decisions for `--apply` (mjh, 2026-09-24, after Plan 1's final review)

- **A component AHEAD of its pin stays where it sits.**  A hotfix deployed by fast-forward before the next image gets blessed survives an alignment.  `align` reports it (`ahead by N — left; --allow-rollback moves it back`), and only `--allow-rollback` moves it backward.  A component that has DIVERGED (ahead and behind) always refuses.
- **A component the release names but the station lacks gets installed**, at its pin, as bring-up would have.  The release says the station carries it; aligning means carrying it.
- **Dirt confined to `uv.lock` gets reset, then the move proceeds.**  Every component's `install.sh` regenerates that file, so the committed copy loses nothing.  `align` logs exactly what it discarded.  Any other uncommitted change still refuses.
- **Before any checkout,** `align` verifies that the release tag points at the manifest's `appliance_commit`, fetches from origin by ref (never by SHA), resolves each pin to a full commit (refusing an ambiguous abbreviation), requires that commit to sit in origin's history, and checks the checkout's `origin` against the catalog's repo.
- **`smd update` must not undo an alignment:** a component at its `.pin` stays held unless the operator passes `--unpin`.
- **Three records stay separate:** the install-time version file (never rewritten), a new aligned-release record, and the host's level (Plan 3).

### Decisions for Plan 2b (mjh, 2026-09-24, after B4's first live alignment)

- **`--apply` restarts by default what the move made stale.** A unit is stale when it started before its own checkout's HEAD last moved, or before an editable sibling it consumes (ka9q-python, hamsci-dsp, …) moved. Staleness is read from the station itself — the unit's start time against git's reflog — so it needs no state file, and a later run finds restarts an earlier run left undone. `--no-restart` opts out. The dry run names every restart it would do.
- **radiod rebuilds and restarts only when ka9q-radio moved.** Then: build, restart radiod, wait until it is ready, then restart every radiod consumer. The dry run says `RESTARTS radiod — recording gap of about N s`. radiod counts as stale only when two things both happened after it started: its binary (`/usr/local/sbin/radiod`) changed, and its ka9q-radio checkout moved. A rebuild of the same source by `smd update` or `smd install --force` changes the binary alone, so it restarts nothing. A build that an earlier run left un-restarted still gets caught, because its checkout moved too. Once radiod runs, any radiod consumer that started before radiod did counts as stale too, so a later run finishes consumer restarts that an earlier one left undone. (Final review, 2026-09-24.)
- **Bring-up re-runs and the image-carried file refresh happen after the moves and before the restarts**, so restarted services start on current configuration. A failure there stops the run before any restart. `sigmond-site-timing` re-runs only when this run refreshed its file, that is, when its own code changed. The script does more than read: it sets the timestd profile to `full`, runs `enable --now` on every metrology channel (starting any an operator had stopped), sets `lb1421_enabled` and restarts core-recorder, and restarts chrony. Otherwise that wiring stays bring-up's, and align reports the script as not re-run. When it does run, align reads back what it logged to the journal during this run and shows those lines, since the script writes nowhere else. (Final review, 2026-09-24.)
- **Verification comes in two speeds.** `--apply` checks at once: every restarted unit is active and stays active for 120 s, hf-timestd's authority is fresh (≤ 60 s old) when hf-timestd runs, and one heartbeat is emitted. The gap and upload verdicts run on an hourly sampler, so `smd align --verify`, run later, reads them against the alignment time.
- **install.sh keeps running as root** (every consumer's script refuses otherwise). The ownership repair after it covers each checkout's own `venv/` as well as its `*.egg-info` — B4's first alignment left two in-checkout venvs root-owned.

`--apply` ships in two plans: **2a** moves the components (steps 2 and 4, recording, the `smd update` hold); **2b** makes the moved code live (radiod rebuild and consumer restarts, service restarts, image-carried files, bring-up re-runs, production verification).

## 4. `pm-align` on the Proxmox host

The host carries no `smd` — only `python3` and a shell — so its step stays a standalone script, as the identity module and the host heartbeat emitter do.

- **The VM reports; it never acts on the host.**  `smd align` compares the host's recorded release with the blessed one and says: "host on v3.36; v3.53 available — run `pm-align` on the host".
- **`pm-align`, dry-run by default:**
  1. fetch the release's appliance tree at its tag — scripts only, a few hundred KB;
  2. show what would change in `/root/sigmond-appliance`: wizard, host heartbeat emitter, RAC client config, host-tuning scripts, network helpers;
  3. `--apply`: replace those files, then re-run the host's idempotent setup steps whose inputs changed — `pm-heartbeat-setup`, and the RAC registration once station identity Plan 3 lands.
- **It never touches Proxmox itself** (apt owns that), **the host's network, or its CPU / IRQ / cache tuning** without an explicit flag.  Those take a station off the air.
- The host heartbeat reports the host's release, so the board can show a host behind too.

### Amended 2026-09-25 (pm-align plan)

1. **Source of each host file.** The release ships no host-file list. pm-align derives code files from the release's own sources: quoted heredocs (`cat > /abs/path <<'DELIM'`) in `firstboot-v3.sh` and `sigmond-wizard.sh` are literal, identical on every host, and extractable; unquoted heredocs substitute site values and are config, not code. The allowlist below is the refresh set; anything else stays as it is.
2. **Refresh set** (path — source — mode):
   - `/usr/local/lib/sigmond-net.sh` — firstboot heredoc `NETLIBEOF` — 0644
   - `/usr/local/sbin/sigmond-issue` — firstboot `ISSEOF` — 0755
   - `/etc/systemd/system/sigmond-issue.service` — firstboot `ISVCEOF` — 0644
   - `/etc/systemd/system/sigmond-issue.timer` — firstboot `ITEOF` — 0644
   - `/usr/local/sbin/sigmond-setup` — the wizard, VMID-rendered as build-usb-v3.sh:262-263 does — 0755
   - `/usr/local/bin/sigmond-vm` — wizard heredoc `VMEOF`, VMID-rendered — 0755
   - `/usr/local/lib/sigmond/vm-port-relay.py` — wizard heredoc `RLEOF` — 0755
   - the 8 relay units `sigmond-vm-{ssh,web,station,gmag}-relay.socket` / `@.service` — rendered from the wizard's own `SOCKEOF`/`SVCEOF` templates with the host's VMID — 0644
   - `/usr/local/sbin/pm-align` — itself, from `wizard_commit` when that commit carries it — 0755
3. **Topology is reported, never changed.** A host whose `/etc/network/interfaces` lacks `vmbr1` has its VM on the LAN (pre-v3.4x). pm-align says so and that v3.53 installs put the VM behind the host; moving it is a reinstall-class change.
4. **Two tunnel channels are added, never re-registered.** The RAC number comes from `/etc/sigmond-appliance/rac-number`, cross-checked against the `vm-ssh` proxy's remotePort − 35800; disagreement refuses the step. Only the missing `-vm-station` / `-vm-gmag` proxies are appended; existing text stays byte-for-byte.
5. **Heartbeat is opt-in on a host that never had it.** An existing `/etc/pm-heartbeat/config.toml` is re-run with its own station/dest (the setup script is idempotent). An absent one is set up only with `--heartbeat`, as the wizard asks before it.
6. **The host's level is recorded, and the VM learns it.** `/etc/sigmond-appliance/host-aligned.json` on the host, copied into the VM at the same path via the guest agent; `smd align` prints it. The install-time `version` file is never rewritten.
7. **Out of scope:** the fleet board's host column (spec §7.3); pm-heartbeat reporting the release (its wire contract forbids a manifest block — a contract change of its own).

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

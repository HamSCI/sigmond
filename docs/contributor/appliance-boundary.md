# The appliance ↔ sigmond boundary

> **Audience:** contributor
> **Status:** current
> **Verified against:** sigmond 2cd11c4 on 2026-08-24 — code (sigmond-appliance scripts, `bin/smd:3566-3567`, `fleet.py`, capture_prep, wizard)
> **Canonical for:** the appliance ↔ sigmond boundary — what the image bakes, what first boot does, how a change reaches a station

Your change is committed. This page answers the next question: **how does it
get onto a station, and does it need a new image?**

Two repositories meet here. `HamSCI/sigmond` is the software — `smd`, the
catalog, the client integration. `HamSCI/sigmond-appliance` is a *build
pipeline* that turns whatever sigmond is at build time into a USB stick that
bare-metal-installs Proxmox VE 9.1 unattended, imports a decoder VM, and runs a
first-boot console wizard for the per-site facts (source:
`sigmond-appliance/README.md:1-13`). The appliance repo contains no product
code; it clones and drives sigmond.

The single most useful fact about the seam: **the golden VM clones current
`HamSCI/sigmond` `main` at build time — there is no tag or pin of sigmond in
the appliance repo** (source: `sigmond-appliance/provision.sh:10-15`;
`build-golden-vm.sh:3` "provision.sh clones CURRENT HamSCI main at build
time"). The image's *version* comes from a git tag on the **appliance**
checkout (source: `sigmond-appliance/build-usb-v3.sh:19-23`). What sigmond
commits went in is not decided by the appliance repo at all — it is *recorded*,
afterwards, in the component pin manifest (§2).

For how sigmond itself is put together — the layers, the verb→module map, the
update orientations — read [`orchestration.md`](orchestration.md) ★ first. This
page assumes it.

## 1. The three layers

Everything on a running station was set in exactly one of three places, at
three different times. Knowing which one owns a given file tells you whether
your change ships by `git pull` or by a new image.

| What | Which layer sets it | Source |
|---|---|---|
| Component source at `/opt/git/sigmond/<name>` | **Baked** — `provision.sh` clones sigmond from `main` and runs its `install.sh`; `provision-components.sh` then runs `smd install --yes` for the whole dasi2 set | `provision.sh:10-15`; `provision-components.sh:50-51` |
| Which components are enabled (`/etc/sigmond/topology.toml`) | **Baked** — hardcoded by the provisioner, not read from a profile argument | `provision-components.sh:8-48` (radiod, ka9q-web, igmp-querier, gpsdo-monitor, hf-timestd, wspr-recorder, psk-recorder, mag-recorder, meteor-scatter, gmag-webui) |
| Per-consumer venvs and their editable sibling links | **Baked** — created by each client's `install.sh` during `smd install` | `provision-components.sh:50-51`; [`../../CLAUDE.md`](../../CLAUDE.md) §Fleet upgrade pattern |
| The radiod binary | **Baked** — built natively in-tree (no `install_script`); its commit is owned by ka9q-python's pin, never by the catalog | `etc/catalog.toml:23-40` |
| Generic (non-cloud) kernel | **Baked** — the cloud kernel has no USB stack, and the decoder VM needs RX888 passthrough; the driver purges it and asserts `xhci` modules exist | `build-golden-vm.sh:62-78` |
| FFTW wisdom (`/etc/fftw/wisdomf`) and radiod's threaded channel plans (`/var/lib/ka9q-radio/wisdom-fftw-3.3.10-sse2-avx-threaded`) | **Baked** — deliberately *after* `capture-prep`, which scrubs wisdom as per-CPU | `provision-components.sh:82-124` |
| The `hamsci` operator account, its password and key | **Baked** | `provision-components.sh:58-69` |
| Identity: `machine-id`, SSH host keys, site profile, secrets, station keys, accumulated data | **Baked *absent*** — `smd admin capture-prep` strips them, and `smd admin readiness --gate capture` is the arbiter that the strip worked | `lib/sigmond/capture_prep.py:1-14`; `provision-components.sh:71-81`; `build-golden-vm.sh:87-92` (a not-ready gate aborts the build) |
| `/etc/sigmond-appliance/version` | **First boot** — written once from the `@@VERSION@@` the build substituted | `firstboot-v3.sh:21`, `:31-32` |
| `vmbr0` converted to DHCP | **First boot** — PVE always fossilizes a static config from whatever the installer ended up with | `firstboot-v3.sh:44-48` |
| The decoder VM itself (VMID 100): imported, sized to the host, disk grown | **First boot** — a udev-armed importer on the *running* host; no USB-present boot is ever required | `firstboot-v3.sh:79-80`, `:185-239`; `README.md:37-38` |
| `/usr/local/sbin/sigmond-setup` (the wizard, on the Proxmox host) | **First boot** — copied off the stick by the importer | `firstboot-v3.sh:117` |
| `/etc/sigmond-appliance/manifest.txt` | **First boot** — installed by the importer from the payload copy, after a row-count sanity check | `firstboot-v3.sh:145-156` |
| Host RAC payload (inert until configured) | **First boot** | `firstboot-v3.sh:242-244` |
| Reporter ID, callsign | **Wizard** — required; drives all upload paths | `sigmond-wizard.sh:336-345`, `:8` |
| Grid square | **Wizard** — a live GPSDO fix is *taken*, not asked; typed entry is provisional and `sigmond-location-check` re-asserts the GPSDO position later | `sigmond-wizard.sh:347-392` |
| Antenna description | **Wizard**, optional | `sigmond-wizard.sh:394-396` |
| Remote access (RAC): on/off, station class, DASI unit number → deterministic channel numbers | **Wizard** | `sigmond-wizard.sh:398-458` |
| PSWS station ID + instrument IDs (the *key* is registered later over SSH) | **Wizard** | `sigmond-wizard.sh:460-486` |
| Station designator → decoder VM name, and `designator-PM` for the Proxmox host | **Wizard** | `sigmond-wizard.sh:488-504` |
| `/etc/sigmond/site-profile.toml` in the guest, then identity + rendered configs | **Wizard**, executed *inside* the VM through the guest agent: writes the profile, then `smd admin personalize --reset-identity --yes`, then `smd config render` | `sigmond-wizard.sh:651-674` |
| A radiod instance (minted at all) | **Wizard**, indirectly — nothing else in the clone flow mints one; a 2-minute sentinel runs `smd bringup dasi2 --non-interactive` once an SDR is on the bus and no instance exists | `sigmond-wizard.sh:686-696`, `:734-744` |

Two structural notes about that table:

**`capture-prep` is the inverse of `personalize`.** The build strips identity
so the template is clone-safe; the wizard puts a *new* identity back per site
(source: `lib/sigmond/capture_prep.py:1-2`, "the inverse of `smd admin
personalize`"). Anything you add that is per-site must be strippable by
`capture_prep`'s plan, or it leaks into every image built after you add it.
`smd admin readiness --gate capture` is what catches that: it requires every
profile component installed, venvs proven importable, units in place, radiod
built, and *no per-site identity or secrets baked in* (source: `smd admin
readiness --help`).

**The heartbeat is deliberately not configured in the image.** The appliance's
answer file carries a comment block saying so and pointing at
`/etc/sigmond/site-profile.toml` in the guest as the real place — nothing in
the appliance repo touches `coordination.toml` or station identity (source:
`sigmond-appliance/answer.toml.template:22-46`).

## 2. Version provenance — three files, three different claims

| Artifact | What it actually says | Where it comes from |
|---|---|---|
| `/etc/sigmond-appliance/version` | The image **lineage** — which image laid this host down. Written once and never updated | `firstboot-v3.sh:32`; copied into the VM by the wizard (`lib/sigmond/provenance.py:34-37`) |
| `smd version` | The **live** commit of every component, computed from the checkouts on every call, plus the append-only update history | `lib/sigmond/provenance.py:1-25`, `:37-38` |
| `/etc/sigmond-appliance/manifest.txt` | The **blessed baseline** — the component SHAs that rode into the image | Generated from `smd version` inside the golden template; §The bless ladder |

`/etc/sigmond-appliance/version` on its own is a trap, and a documented one:
DASI002 asserted `v3.20` while running radiod `cd44bbdd`, ka9q-python 3.24.0
and hf-timestd `55e8797`, all installed in place that morning — "the string was
true when written and false by lunchtime, with no way to tell from the box"
(source: `lib/sigmond/provenance.py:3-8`). That is why `smd version` reports
the image string labelled as lineage, separately from live components (source:
`lib/sigmond/provenance.py:40-41`, `:46-57`), and why
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §3 tells you not to read the
file on its own. The operator-facing statement of the same rule is
[`../operator/day-2.md`](../operator/day-2.md) §Updates.

**The manifest is generated, never hand-written.** `build-golden-vm.sh`
captures `smd version` from *inside* the golden template into
`manifest-raw.txt` — the only point in the pipeline where components are
installed and still reachable over ssh — and gates on a row count, not just the
header string, because a truncated capture would still contain the header and
would ship a manifest that lies about having zero pins (source:
`build-golden-vm.sh:105-130`). `build-usb-v3.sh` re-checks the same floor as the
hard ship/no-ship gate (source: `build-usb-v3.sh:260-266`, `:367-391`) and
writes **two** copies: the Release-attached one with `image_sha256`, and a
payload copy without it — the field is the hash of the finished `.img`, which
does not exist until the payload is already sealed inside it (source:
`build-usb-v3.sh:319-322`; `docs/RELEASE.md:122-134`).

**Live hosts are never reimaged**, so they adopt a manifest in place instead:

```bash
smd admin manifest adopt <manifest.txt>                     # dry-run plan
smd admin manifest adopt <manifest.txt> --allow-superset --apply
```

The verb is fail-closed — it refuses unless every component matches exactly, or
(with `--allow-superset`) the live commit is an ancestry-verified superset of
the manifest SHA, and it prints the whole diff rather than the first mismatch
(source: `lib/sigmond/manifest_adopt.py:1-11`, `:25-37`; `smd admin manifest
adopt --help`). `smd admin manifest restore` is the other direction: it moves
diverging checkouts *back* onto the manifest SHAs, refusing a dirty tree rather
than stashing it, and leaving strays alone (source: `smd admin manifest restore
--help`). Blessed means **contained baseline, not frozen equality** — see
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §5 for why a develop-on-main
fleet needs that rule to have a meaningful baseline at all.

## 3. How a change reaches a station

Two paths exist and only two: **pull** (`smd update` on the station, one host
at a time) or **the next image**. Find your change in the left column.

| Your change | Path | Why |
|---|---|---|
| Python or docs in sigmond or any client | `smd update --apply` on the station | The plan's `pull` step is a `git pull --ff-only` as the checkout's owner; editable sibling installs mean every consumer venv sees the new source with no further action (`lib/sigmond/update.py:14-19`; [`../../CLAUDE.md`](../../CLAUDE.md) §Fleet upgrade pattern) |
| A new or bumped Python dependency (`pyproject.toml` / `uv.lock`) | Same — the plan's `install` step runs the client's `install.sh` | `install.sh` is `uv sync`, which honours `[tool.uv.sources]` and repairs a venv holding a private *copy* of a sibling; `deploy.sh` does **not** re-resolve siblings and will leave it stale forever (`lib/sigmond/update.py:25-29`; `bin/smd:3540-3553`) |
| A new or changed systemd unit in a client | Same — units ride that client's `install.sh` | Verify the *unit*, not the checkout ([`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §9) |
| A new catalog entry, or a repo-wide catalog field | `git pull` of sigmond alone | Sparse per-field overlay means a new repo-side entry propagates without any per-host sync ([`../../CLAUDE.md`](../../CLAUDE.md) §Catalog layering) |
| A ka9q-radio pin bump | `sync_types.py --apply`, then `smd update` — but the **rebuild is not automated** | `smd update` *plans* a `rebuild-radiod` step and prints the exact command instead of running it: it is long and it bounces acquisition, so it deserves an operator watching. It also tells you to `rm config_paths.h main.o` first, or the new binary reports the old commit (`bin/smd:3388-3391`, `:3570`; `lib/sigmond/update.py:30-32`, `:179-188`) |
| A **new core client in the shipped image** | Next golden build | The image's `topology.toml` is hardcoded in `provision-components.sh:8-48`, not derived from a catalog profile |
| A wizard change (`scripts/proxmox/sigmond-wizard.sh`) | **Next image** | `build-usb-v3.sh` copies the wizard out of a sigmond checkout at build time and refuses to build if it is uncommitted; firstboot then stages *that copy* as `/usr/local/sbin/sigmond-setup`. Pulling sigmond on a live station does not update the installed wizard (`build-usb-v3.sh:111-130`; `firstboot-v3.sh:117`) |
| `firstboot-v3.sh`, `answer.toml.template`, payload packaging | **Next image**, by definition | They execute only during install (`answer.toml.template:18-20`) |
| Baked wisdom, the kernel, the `hamsci` account, anything `capture-prep` must learn to strip | **Next golden build** | `provision-components.sh:58-124`; `lib/sigmond/capture_prep.py` |

`smd update` is a **planner with a deliberate boundary**: it executes the
`pull` and `install` steps (`bin/smd:3527-3553`) but prints the `rebuild-radiod`,
`wisdom` and `restart` steps as commands for a human to run — "restart not
automated here — bounce radiod, wait for it to settle, then the recorders"
(source: `bin/smd:3566-3567`). So `smd update --apply` exiting 0 means the new
source is on disk; it does not mean any process is running it. Every action in
the plan carries its own `verify`, because the failures this planner was written
from were uniformly of the form "looked fine, wasn't done" (source:
`lib/sigmond/update.py:39-42`). The two pin files for ka9q-radio are written by
`sync_types.py --apply`, never by hand — editing one and not the other looks
like a fix and changes nothing (source:
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §5).

Whichever path applies, the **order** is not yours to choose informally.
Mutation is one host at a time, **canary first** (`canary = true` in the fleet
inventory; B4 today), verify, then the rest — driven by `smd fleet update`,
which multiplexes the same station-inward procedure host by host. The
`status|doctor|roster|pubkeys` fan-out can only ask questions, enforced by a
test-checked whitelist (source:
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §3). That wall between the two
orientations is the point, not a missing feature.

Two things a "successful" update still will not have done for you: a
long-running service holds its start-time bytecode until restarted, and a
checkout at the right commit proves nothing about the venv the process imports
from. Both are in [`orchestration.md`](orchestration.md) §Updates and
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §9.

## 4. The bless ladder

An image climbs four rungs — **built → tested → blessed → rolled** — and each
has a concrete evidence requirement, precisely so "verified" can mean one thing
again (source:
[`sigmond-appliance/docs/RELEASE.md`](https://github.com/HamSCI/sigmond-appliance/blob/main/docs/RELEASE.md#the-four-rungs)).
Read that page for the full statement; the summary a contributor needs is
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §6.

`bless-release.sh <version> [--apply]` is the executable gate. It refuses to
cut a GitHub Release unless **seven** checks pass, reported individually:

0. the version is a real release version (no `-dev`, no `+`)
1. the tag exists and points at a commit reachable from `origin/main`
2. the working tree is clean
3. the image and its `.sha256` exist and the checksum verifies
4. the manifest exists and contains a components block
5. test evidence records a PASS for **this exact image**
6. no Release already exists for this tag

(source: `sigmond-appliance/bless-release.sh:10-23`.) It is dry-run by default,
matching `smd update`'s contract on this fleet, and `--apply` still reads the
confirmation from `/dev/tty` explicitly, so a pipe or an automated caller
cannot satisfy it (source: `bless-release.sh:21-23`, `:632-633`). On publish,
only the manifest and `.sha256` are attached — the image is ~4.7 GB against
GitHub's 2 GiB asset cap and stays on the artifact store (source:
`docs/RELEASE.md:63-68`).

Gate 5 is the one worth understanding, because it is the one a human cannot do
by eye. `test-nested-v3.sh` appends to `test-v3.log` forever; a `PASS` string
existing *somewhere* in that log is not evidence. The gate bounds the block
that starts with `USB image under test: <this exact filename>` and requires
`PHASE D PASS` inside *the latest* such block (source:
`bless-release.sh:196-244`; `docs/RELEASE.md:35-41`).

Under the heading **"Three rules that have cost real time"**, `RELEASE.md`
states four (see [ledger row 57](docs-gap-ledger.md)):

1. the manifest is generated, never hand-written;
2. the manifest also rides the payload, minus `image_sha256`;
3. verify the **venv**, not the checkout — a fix was "verified" live on B4 for
   a full day while the venv it ran from had not moved;
4. verify `/proc/PID/exe`, not the file you installed — a radiod swap was
   silently a no-op because a stale drop-in launched a different executable.

(source: `docs/RELEASE.md:109-153`.) Rules 3 and 4 are the ones that bite a
contributor confirming their own change on a station.

## 5. Testing an image

Two nested qemu/OVMF rigs, both documented as running on the build host —
"run on the build host, e.g. B3" (source: `sigmond-appliance/README.md:15`),
and each script's own header says "runs on B3" (source: `test-nested-v3.sh:2`;
`test-update-v3.sh:2`). Neither needs real hardware, with one stated exception:
there is no IOMMU in the nest, so PCI passthrough is real-hardware scope
(source: `test-nested-v3.sh:6-9`).

**Install — `test-nested-v3.sh [A|B|C|D]`.** Four phases: UEFI-boot the USB
image and auto-install PVE to an empty NVMe; boot NVMe only and confirm
firstboot ran with no media and the importer is armed; boot NVMe **plus** USB
so udev fires the import, the host gets tuned, and the nested host reboots into
VM 100 autostart; then drive the wizard non-interactively and verify identity,
PSWS, the generic kernel and the `hamsci` login inside the decoder VM (source:
`test-nested-v3.sh:1-14`). Since v3.33 Phase D also asserts the fleet-awareness
payload rides the image *and is correct for an unconfigured host* — `smd admin
heartbeat emit --dry-run` must exit **2**, because exit 0 would mean a station
config leaked into the golden template (source: `docs/RELEASE.md:43-49`). PCI
topology must stay identical across boots or the NIC renames and PVE's bridge
config breaks (source: `README.md:39-41`; `test-nested-v3.sh:12-14`).

**Update — `test-update-v3.sh [--image <release.img>] [--target <git-ref>]
[--resume]`.** The install rig proves an install; nothing proved an *update*
until this one, and both defects it exists for were silent — a client three
commits behind that every process reported as fine, and a `runuser`/PATH crash
in the `qm guest exec` update channel that only ever appeared on a live
station. It boots the previous blessed image, rolls it forward to `--target`
(default `origin/main`) through the **real production root channel** — `qm
guest exec` from the nested Proxmox host, never a direct ssh for a mutation —
then rolls back. Phase E is the scripted station-inward procedure of
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §3 (pre-state, `smd update
--apply`, idempotence, SHA-level assertion against `--target`, no new doctor
findings, heartbeat contract survives); Phase F adopts the release manifest
with `--allow-superset`; Phase G runs `smd admin manifest restore` back to the
blessed SHAs and then a **strict** adopt — the round-trip proof (source:
`test-update-v3.sh:1-29`, `:76-98`). It appends to `test-update-v3.log`; the
sibling owns `test-v3.log` (source: `test-update-v3.sh:26-29`).

That script's header also carries a **contract-strings block**: every product
string and exit code it asserts on, with the sigmond file that produces it, so
a `grep` for any of them finds both producer and consumer and a rename shows up
in review instead of forty minutes into a nested run (source:
`test-update-v3.sh:44-70`). If you rename a `smd` output string, that block is
where it bites — check it.

## 6. Known disagreements

Recorded in [`docs-gap-ledger.md`](docs-gap-ledger.md) rather than fixed here,
because they are software/doc defects in another repo:

- **Row 56** — `sigmond-appliance/README.md` §Pipeline still describes the v2
  scripts and a step `build-golden-vm.sh` does not run.
- **Row 57** — `RELEASE.md`'s "Three rules that have cost real time" lists
  four.
- **Row 58** — `complete-profile.sh` and `finish-build.sh` are v2-era orphans
  that no v3 script invokes.

## Where to read next

- [`orchestration.md`](orchestration.md) ★ — how sigmond itself is built, and
  the verb→module map.
- [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §3 (orientations), §5
  (pins), §6 (cutting a release), §9 (verify the thing that runs).
- [`../operator/day-2.md`](../operator/day-2.md) §Updates — the same boundary
  stated for the person who owns the station.
- [`../operator/install.md`](../operator/install.md) → `sigmond-appliance/INSTALL.md`
  — what the operator actually experiences at first boot.
- [`sigmond-appliance/docs/RELEASE.md`](https://github.com/HamSCI/sigmond-appliance/blob/main/docs/RELEASE.md)
  — the four rungs in full.
- [`dev-setup.md`](dev-setup.md) ★ — getting a working checkout before any of
  this matters.
- [`client-authoring.md`](client-authoring.md) — adding a component the image
  will then carry.

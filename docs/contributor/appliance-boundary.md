# The appliance ↔ sigmond boundary

> **Audience:** contributor
> **Status:** current
> **Verified against:** sigmond f3b0c6e on 2026-11-18 — code (sigmond-appliance scripts at 99869d3, `update.py`, `align.py`, `fleet.py`, `capture_prep.py`, `smd` help output)
> **Canonical for:** the appliance ↔ sigmond boundary — what the Proxmox image bakes, what first boot does, how a change reaches a station

Your change is committed. This page answers the next question: **how does it
get onto a station, and does it need a new Proxmox image?**

A release now carries the name "sigmond vN" (for example sigmond v3.93). It
names the blessed list of component versions. Two install paths deliver it:
the Proxmox USB image, and a bare-metal install from the repos. `smd align`
brings a running station to the same list. This page covers the Proxmox
image, the one path that bakes anything in.

Two repositories meet here. `HamSCI/sigmond` is the software — `smd`, the
catalog, the client integration. `HamSCI/sigmond-appliance` (slated for a
rename to sigmond-proxmox) is a *build pipeline*. It turns whatever sigmond
stands at build time into a USB stick that bare-metal-installs Proxmox VE
unattended, imports a decoder VM, and runs a first-boot console wizard for the
per-site facts (source: `sigmond-appliance/README.md`). The appliance repo
contains no product code; it clones and drives sigmond.

The single most useful fact about the seam: **the golden VM clones current
`HamSCI/sigmond` `main` at build time — the appliance repo holds no tag or pin
of sigmond** (source: `sigmond-appliance/provision.sh`;
`build-golden-vm.sh` "provision.sh clones CURRENT HamSCI main at build
time"). The release's *version* comes from a git tag on the **appliance**
checkout (source: `sigmond-appliance/build-usb-v3.sh`). What sigmond
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
| Component source at `/opt/git/sigmond/<name>` | **Baked** — `provision.sh` clones sigmond from `main` and runs its `install.sh`; `provision-components.sh` then runs `smd install --yes` for the whole dasi2 set | `provision.sh`; `provision-components.sh` |
| Which components are enabled (`/etc/sigmond/topology.toml`) | **Baked** — hardcoded by the provisioner, not read from a profile argument. The list covers radiod, ka9q-web, igmp-querier, gpsdo-monitor, hf-timestd, wspr-recorder, psk-recorder, mag-recorder, meteor-scatter, gmag-webui, hamsci-physics, station-web, hfdl-recorder and codar-sounder. A guard in the same script compares it with the catalog's `dasi2` profile and stops the build when they differ | `provision-components.sh` |
| Per-consumer venvs and their editable sibling links | **Baked** — created by each client's `install.sh` during `smd install` | `provision-components.sh`; [`../../CLAUDE.md`](../../CLAUDE.md) §Fleet upgrade pattern |
| The radiod binary | **Baked** — built natively in-tree (no `install_script`); its commit is owned by ka9q-python's pin, never by the catalog | `etc/catalog.toml` |
| Generic (non-cloud) kernel | **Baked** — the cloud kernel has no USB stack, and the decoder VM needs RX888 passthrough; the driver purges it and asserts `xhci` modules exist | `build-golden-vm.sh` |
| FFTW wisdom (`/etc/fftw/wisdomf`) and radiod's threaded channel plans (`/var/lib/ka9q-radio/wisdom-fftw-3.3.10-sse2-avx-threaded`) | **Baked** — deliberately *after* `capture-prep`, which scrubs wisdom as per-CPU | `provision-components.sh` |
| The `hamsci` operator account, its password and key | **Baked** | `provision-components.sh` |
| Identity: `machine-id`, SSH host keys, site profile, secrets, station keys, accumulated data | **Baked *absent*** — `smd admin capture-prep` strips them, and `smd admin readiness --gate capture` is the arbiter that the strip worked | `lib/sigmond/capture_prep.py`; `provision-components.sh`; `build-golden-vm.sh` (a not-ready gate aborts the build) |
| `/etc/sigmond-appliance/version` | **First boot** — written once from the `@@VERSION@@` the build substituted | `firstboot-v3.sh` |
| `vmbr0` converted to DHCP | **First boot** — PVE always fossilizes a static config from whatever the installer ended up with | `firstboot-v3.sh` |
| The decoder VM itself (VMID 100): imported, sized to the host, disk grown | **First boot** — a udev-armed importer on the *running* host; no USB-present boot is ever required | `firstboot-v3.sh`; `README.md` |
| `/usr/local/sbin/sigmond-setup` (the wizard, on the Proxmox host) | **First boot** — copied off the stick by the importer | `firstboot-v3.sh` |
| `/etc/sigmond-appliance/manifest.txt` | **First boot** — installed by the importer from the payload copy, after a row-count sanity check | `firstboot-v3.sh` |
| Host RAC payload (inert until configured) | **First boot** | `firstboot-v3.sh` |
| Reporter ID, callsign | **Wizard** — required; drives all upload paths | `sigmond-wizard.sh` |
| Grid square | **Wizard** — a live GPSDO fix is *taken*, not asked; typed entry is provisional and `sigmond-location-check` re-asserts the GPSDO position later | `sigmond-wizard.sh` |
| Antenna description | **Wizard**, optional | `sigmond-wizard.sh` |
| Remote access (RAC): on/off, station class, DASI unit number → deterministic channel numbers | **Wizard** | `sigmond-wizard.sh` |
| PSWS station ID + instrument IDs (the *key* is registered later over SSH) | **Wizard** | `sigmond-wizard.sh` |
| Station designator → decoder VM name, and `designator-PM` for the Proxmox host | **Wizard** | `sigmond-wizard.sh` |
| `/etc/sigmond/site-profile.toml` in the guest, then identity + rendered configs | **Wizard**, executed *inside* the VM through the guest agent: writes the profile, then `smd admin personalize --reset-identity --yes`, then `smd config render` | `sigmond-wizard.sh` |
| A radiod instance (minted at all) | **Wizard**, indirectly — nothing else in the clone flow mints one; a 2-minute sentinel runs `smd bringup dasi2 --non-interactive` once an SDR is on the bus and no instance exists | `sigmond-wizard.sh` |

Two structural notes about that table:

**`capture-prep` is the inverse of `personalize`.** The build strips identity
so the template is clone-safe; the wizard puts a *new* identity back per site
(source: `lib/sigmond/capture_prep.py`, "the inverse of `smd admin
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
`sigmond-appliance/answer.toml.template`).

## 2. Version provenance — three files, three different claims

| Artifact | What it actually says | Where it comes from |
|---|---|---|
| `/etc/sigmond-appliance/version` | The image **lineage** — which image laid this host down. Written once and never updated | `firstboot-v3.sh`; copied into the VM by the wizard (`lib/sigmond/provenance.py`) |
| `smd version` | The **live** commit of every component, computed from the checkouts on every call, plus the append-only update history | `lib/sigmond/provenance.py` |
| `/etc/sigmond-appliance/manifest.txt` | The **blessed baseline** — the component SHAs that rode into the image | Generated from `smd version` inside the golden template; §The bless ladder |

`/etc/sigmond-appliance/version` on its own is a trap, and a documented one:
DASI002 asserted `v3.20` while running radiod `cd44bbdd`, ka9q-python 3.24.0
and hf-timestd `55e8797`, all installed in place that morning — "the string was
true when written and false by lunchtime, with no way to tell from the box"
(source: `lib/sigmond/provenance.py`). That is why `smd version` reports
the image string labelled as lineage, separately from live components (source:
`lib/sigmond/provenance.py`), and why
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §3 tells you not to read the
file on its own. The operator-facing statement of the same rule is
[`../operator/day-2.md`](../operator/day-2.md) §Updates.

**The manifest is generated, never hand-written.** `build-golden-vm.sh`
captures `smd version` from *inside* the golden template into
`manifest-raw.txt` — the only point in the pipeline where components are
installed and still reachable over ssh — and gates on a row count, not just the
header string, because a truncated capture would still contain the header and
would ship a manifest that lies about having zero pins (source:
`build-golden-vm.sh`). `build-usb-v3.sh` re-checks the same floor as the
hard ship/no-ship gate (source: `build-usb-v3.sh`) and
writes **two** copies: the Release-attached one with `image_sha256`, and a
payload copy without it — the field is the hash of the finished `.img`, which
does not exist until the payload is already sealed inside it (source:
`build-usb-v3.sh`; `docs/RELEASE.md`).

**Live hosts are never reimaged**, so they adopt a manifest in place. (`smd align`
moves a station to the latest release; the verb below checks a host against one
manifest.)

```bash
smd admin manifest adopt <manifest.txt>                     # dry-run plan
smd admin manifest adopt <manifest.txt> --allow-superset --apply
```

The verb is fail-closed — it refuses unless every component matches exactly, or
(with `--allow-superset`) the live commit is an ancestry-verified superset of
the manifest SHA, and it prints the whole diff rather than the first mismatch
(source: `lib/sigmond/manifest_adopt.py`; `smd admin manifest
adopt --help`). `smd admin manifest restore` is the other direction: it moves
diverging checkouts *back* onto the manifest SHAs, refusing a dirty tree rather
than stashing it, and leaving strays alone (source: `smd admin manifest restore
--help`). After the checkouts it runs each moved component's `install.sh`, as
`smd update` does, so the units stand as the older release installs them. It
also disables and removes a unit file that only the newer commit shipped. Restore
never restarts radiod, and the operator restarts radiod and the recorders by
hand. A component's install.sh may restart its own services, exactly as under
`smd update`: gpsdo-monitor's restarts `gpsdo-monitor.service` every time;
sigmond's restarts chrony (through `sigmond-time-sync`) when the chrony config
changes, and `systemd-journald` when the journal cap file changes;
mag-recorder's restarts mosquitto when it writes the broker config. Blessed means **contained baseline, not frozen equality** — see
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §5 for why a develop-on-main
fleet needs that rule to have a meaningful baseline at all.

## 3. How a change reaches a station

A change reaches a station by one of three routes: **pull** (`smd update` on
the station, one host at a time), **align** (`smd align --apply` moves the
station to the latest blessed release), or **the next Proxmox image**. Find your
change in the left column.

A station that came from an image, or that ran `smd align`, holds every
component at its blessed pin. `smd update` refuses to pull a pinned component.
`smd update --unpin` releases the hold; `smd update --latest` moves every
component to the branch tip and pins the result. `smd align` (a dry run), then
`smd align --apply`, brings a station back to the blessed release instead.

| Your change | Path | Why |
|---|---|---|
| Python or docs in sigmond or any client | `smd update --apply` on the station | The plan's `pull` step is a `git pull --ff-only` as the checkout's owner; editable sibling installs mean every consumer venv sees the new source with no further action (`lib/sigmond/update.py`; [`../../CLAUDE.md`](../../CLAUDE.md) §Fleet upgrade pattern) |
| A new or bumped Python dependency (`pyproject.toml` / `uv.lock`) | Same — the plan's `install` step runs the client's `install.sh` | `install.sh` is `uv sync`, which honours `[tool.uv.sources]` and repairs a venv holding a private *copy* of a sibling; `deploy.sh` does **not** re-resolve siblings and will leave it stale forever (`lib/sigmond/update.py`) |
| A new or changed systemd unit in a client | Same — units ride that client's `install.sh` | Verify the *unit*, not the checkout ([`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §9) |
| A new catalog entry, or a repo-wide catalog field | `git pull` of sigmond alone | Sparse per-field overlay means a new repo-side entry propagates without any per-host sync ([`../../CLAUDE.md`](../../CLAUDE.md) §Catalog layering) |
| A ka9q-radio pin bump | `sync_types.py --apply`, then `smd update` — but the **rebuild is not automated** | `smd update` *plans* a `rebuild-radiod` step and prints the exact command instead of running it: it is long and it bounces acquisition, so it deserves an operator watching. It also tells you to `rm config_paths.h main.o` first, or the new binary reports the old commit (`lib/sigmond/update.py`) |
| A **new core client in the shipped image** | Next golden build | The image's `topology.toml` is hardcoded in `provision-components.sh`, not derived from a catalog profile |
| A wizard change (`scripts/proxmox/sigmond-wizard.sh`) | **Next image** | `build-usb-v3.sh` copies the wizard out of a sigmond checkout at build time and refuses to build if it is uncommitted; firstboot then stages *that copy* as `/usr/local/sbin/sigmond-setup`. Pulling sigmond on a live station does not update the installed wizard (`build-usb-v3.sh`; `firstboot-v3.sh`) |
| `firstboot-v3.sh`, `answer.toml.template`, payload packaging | **Next image**, by definition | They execute only during install (`answer.toml.template`) |
| Baked wisdom, the kernel, the `hamsci` account, anything `capture-prep` must learn to strip | **Next golden build** | `provision-components.sh`; `lib/sigmond/capture_prep.py` |

`smd update` is a **planner with a deliberate boundary**: it executes the
`pull` and `install` steps (`cmd_update` in `bin/smd`) but prints the `rebuild-radiod`,
`wisdom` and `restart` steps as commands for a human to run — "restart not
automated here — bounce radiod, wait for it to settle, then the recorders"
(source: `bin/smd` (the "restart not automated" line)). So `smd update --apply` exiting 0 means the new
source is on disk; it does not mean any process is running it. Every action in
the plan carries its own `verify`, because the failures this planner was written
from were uniformly of the form "looked fine, wasn't done" (source:
`lib/sigmond/update.py`). The two pin files for ka9q-radio are written by
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

A release climbs four rungs — **built → tested → blessed → rolled** — and each
has a concrete evidence requirement, precisely so "verified" can mean one thing
again (source:
[`sigmond-appliance/docs/RELEASE.md`](https://github.com/HamSCI/sigmond-appliance/blob/main/docs/RELEASE.md#the-four-rungs)).
Read that page for the full statement; the summary a contributor needs is
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) §6.

`bless-release.sh <version> [--apply]` is the executable gate. It refuses to
cut a GitHub Release unless **nine** checks (numbered 0 to 8) pass, reported
individually:

0. the version is a real release version (no `-dev`, no `+`)
1. the tag exists and points at a commit reachable from `origin/main`
2. the working tree is clean
3. the image and its `.sha256` exist and the checksum verifies
4. the manifest exists and contains a components block
5. test evidence records a PASS for **this exact image**
6. no Release already exists for this tag
7. the install instructions name this release: `INSTALL.md` on `origin/main`
   reads `Status: current` and `Verified against: sigmond-appliance <version>`,
   and `docs/install-page.html` names this image's file
8. `CHANGELOG.md` on `origin/main` holds exactly one
   `## sigmond-appliance <version>` section, not marked `(draft)`, with text
   under it

(source: `sigmond-appliance/bless-release.sh`.) It is dry-run by default,
matching `smd update`'s contract on this fleet, and `--apply` still reads the
confirmation from `/dev/tty` explicitly, so a pipe or an automated caller
cannot satisfy it (source: `bless-release.sh`). On publish, the release notes
open with the changelog section, and only the manifest and `.sha256` attach to
the Release. The image exceeds GitHub's 2 GiB asset cap and stays on the
artifact store (source:
`docs/RELEASE.md`).

Gate 5 is the one worth understanding, because it is the one a human cannot do
by eye. `test-nested-v3.sh` appends to `test-v3.log` forever; a `PASS` string
existing *somewhere* in that log is not evidence. The gate bounds the block
that starts with `USB image under test: <this exact filename>` and requires
`PHASE D PASS` inside *the latest* such block (source:
`bless-release.sh`; `docs/RELEASE.md`).

Under the heading **"Three rules that have cost real time"**, `RELEASE.md`
states four (see §6):

1. the manifest is generated, never hand-written;
2. the manifest also rides the payload, minus `image_sha256`;
3. verify the **venv**, not the checkout — a fix was "verified" live on B4 for
   a full day while the venv it ran from had not moved;
4. verify `/proc/PID/exe`, not the file you installed — a radiod swap was
   silently a no-op because a stale drop-in launched a different executable.

(source: `docs/RELEASE.md`.) Rules 3 and 4 are the ones that bite a
contributor confirming their own change on a station.

## 5. Testing an image

Two nested qemu/OVMF rigs, both documented as running on the build host —
"run on the build host, e.g. B3" (source: `sigmond-appliance/README.md`),
and each script's own header says "runs on B3" (source: `test-nested-v3.sh`;
`test-update-v3.sh`). Neither needs real hardware, with one stated exception:
there is no IOMMU in the nest, so PCI passthrough is real-hardware scope
(source: `test-nested-v3.sh`).

**Install — `test-nested-v3.sh [A|B|C|D]`.** Four phases: UEFI-boot the USB
image and auto-install PVE to an empty NVMe; boot NVMe only and confirm
firstboot ran with no media and the importer is armed; boot NVMe **plus** USB
so udev fires the import, the host gets tuned, and the nested host reboots into
VM 100 autostart; then drive the wizard non-interactively and verify identity,
PSWS, the generic kernel and the `hamsci` login inside the decoder VM (source:
`test-nested-v3.sh`). Since v3.33 Phase D also asserts the fleet-awareness
payload rides the image *and is correct for an unconfigured host* — `smd admin
heartbeat emit --dry-run` must exit **2**, because exit 0 would mean a station
config leaked into the golden template (source: `docs/RELEASE.md`). PCI
topology must stay identical across boots or the NIC renames and PVE's bridge
config breaks (source: `README.md`; `test-nested-v3.sh`).

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
findings, heartbeat contract survives); Phase E-bis checks what `smd align` does once the code has moved (the
uploader migrate, the sink carry-over, the heartbeat entry); Phase E-ter checks remote access on a
station already in the field; Phase F adopts the release manifest
with `--allow-superset`; Phase G runs `smd admin manifest restore` back to the
blessed SHAs and then a **strict** adopt — the round-trip proof (source:
`test-update-v3.sh`). It appends to `test-update-v3.log`; the
sibling owns `test-v3.log` (source: `test-update-v3.sh`).

That script's header also carries a **contract-strings block**: every product
string and exit code it asserts on, with the sigmond file that produces it, so
a `grep` for any of them finds both producer and consumer and a rename shows up
in review instead of forty minutes into a nested run (source:
`test-update-v3.sh`). If you rename a `smd` output string, that block is
where it bites — check it.

## 6. Known disagreements

Three defects in the appliance repo stay open as `docs-gap` issues
([query](https://github.com/HamSCI/sigmond-appliance/issues?q=label%3Adocs-gap)).
This page records them and fixes none:

- `sigmond-appliance/README.md` §Pipeline still describes the v2 scripts
  (`build-usb-v2.sh`, `test-nested-v2.sh`) and a `complete-profile.sh` step
  that `build-golden-vm.sh` does not run.
- `RELEASE.md`'s section "Three rules that have cost real time" lists four
  rules.
- `complete-profile.sh` and `finish-build.sh` stay in the repo as v2-era
  orphans that no v3 script invokes.

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

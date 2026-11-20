# Release home: sigmond holds the version

20 November 2026.  Michael Hauan and Claude.  Status: design.  Decisions by Michael (2026-11-18) and Rob's
approval of the move (given to Michael 2026-11-19, relayed 2026-11-20: "proceed with all").

## Intent

A version names the whole system: **sigmond vN** is a blessed list of component commits, delivered three ways:
the Proxmox image, a bare-metal install, and `smd align` on a running station.  Today the version lives in
sigmond-appliance, the image builder's repo.  Its tag, CHANGELOG, RELEASE.md, bless script and the list (the
image's `.manifest.txt`, captured after the build) all sit there.  Bare-metal and align users never touch that
repo, and people confuse "sigmond" with "sigmond-appliance".

Michael's decisions (ledger `sigmond-release-home`, 2026-11-18):

- sigmond holds the version tag, the blessed list as its own file **that the image build reads**, bless,
  RELEASE.md, CHANGELOG, and pointers to both install paths.
- sigmond-appliance is renamed **sigmond-proxmox**: the image, host first-boot and networking, the test rig.
- Prose: "sigmond" names the system; "the sigmond manager" or `smd` names the component.
- Each release runs a **bare-metal install test** on the rig: a fresh Debian 13 VM, `docs/install-bare-metal.md`
  followed verbatim, `smd bringup dasi2 --non-interactive` at the blessed list, no RX888 (hardware components
  dormant), healthy otherwise.  Bless requires it, beside the Proxmox image test, the update test, and the B4
  and SIGMA canaries.
- v3.93 keeps its tag in sigmond-appliance.  v3.94 is the first version tagged in sigmond.

## Goals

1. One file in sigmond, `release/components.toml`, lists the component commits a version ships.  Written and
   committed before the build; the tag `vN` on sigmond marks it.
2. The image build installs exactly that list, and fails if the captured image differs from it.
3. `smd align --release vN` (v3.94 and later) reads the list from sigmond's tag; `--manifest PATH` still serves a
   candidate before the bless.
4. Stations running a v3.93 or older smd still find v3.94 (see Transition).
5. Bless lives in sigmond (`release/bless-release.sh`), and its gates include the bare-metal install test.
6. GitHub repo renamed; every URL in both repos, the rig scripts and the install pages follows.

## Non-goals

- Renaming on-station paths.  `/etc/sigmond-appliance/{version,manifest.txt,aligned.json,host-aligned.json,
  rac-mode,golden-image}` stay as they are: every station and image carries them, and a rename is a migration
  with no user benefit.  New code keeps writing them.
- Changing what a blessed image contains, or the image build's mechanics beyond reading the list.
- Image hosting (rig -> rclone -> Drive) stays as it is.

## Anti-goals

- A v3.93 station whose `smd align` stops seeing new versions.
- Two lists that can disagree: a list in sigmond and a separately captured manifest that bless accepts when
  they differ.
- A bless that passes without the bare-metal test.

## Approaches considered (Claude's call; Michael's decision fixed the direction)

- **A. Move files only.**  CHANGELOG/RELEASE.md/bless into sigmond; the list stays a post-build capture
  attached to a GitHub Release.  Cheap, but breaks Michael's "a file in sigmond that the image build reads".
- **B. The list leads (chosen).**  `release/components.toml` is written first; the build installs it; the
  capture verifies it; the GitHub Release on sigmond attaches the manifest generated from it, in today's
  format, so align's parser stays.
- **C. The list leads, no GitHub Release.**  align reads `release/components.toml` at the tag over raw URLs.
  Neat, but every v3.93 smd reads Releases, and the Drive install page links Releases.  Rejected for now.

## The design

### The list

`release/components.toml` in sigmond:

```toml
version = "v3.94"
[components]
hf-timestd = "67ca2b10..."   # full 40-character commits
ka9q-radio = "..."
# one row per component the manifest carries today, sigmond itself excluded:
# the tag on sigmond names sigmond's own commit.
```

`smd admin release-list --write` fills it from the current heads of each component's main (what
`SIGMOND_TRACK_MAIN=1` builds today) for the release manager to review and commit; `--check` compares a
station's or a capture's commits with it.  Native pins (`_UBERSDR_COMMIT`, `_ONION_COMMIT`, `_WSJTX_COMMIT`,
radiod) stay where Task 9 of v3.94 put them, in sigmond's code: the list repeats them, and a test keeps the
two equal.

### The build

`provision-components.sh` (sigmond-proxmox) installs from the list at the sigmond tag under build instead of
`SIGMOND_TRACK_MAIN=1`.  The existing capture (`smd version` -> `manifest-raw.txt`, build records, decoder
provenance) still runs; `build-usb-v3.sh` refuses to ship when the capture differs from the list.  The image's
version comes from the list's `version`, no longer from a tag on sigmond-proxmox.

### Bless

`release/bless-release.sh` moves into sigmond with its tests and keeps its gates, now reading the tag and the
clean tree of sigmond.  Two gates change and one is added:

- gate 4 (manifest) also requires manifest == `release/components.toml` at the tag;
- gate 6 checks for an existing Release on HamSCI/sigmond;
- a new gate requires a passing bare-metal install test record for this version, beside the image test.

It creates the GitHub Release on **HamSCI/sigmond** with the manifest and `.sha256` attached.

### Transition (Claude's call)

v3.93 and older smd read releases from `HamSCI/sigmond-appliance` (GitHub redirects the renamed repo's API and
pages).  So while any station runs such an smd, bless also publishes the same Release, same manifest, on
**HamSCI/sigmond-proxmox**.  A v3.93 station's first `smd align --apply` moves it to v3.94's smd, which reads
HamSCI/sigmond from then on.  v3.94's smd reads HamSCI/sigmond first and falls back to sigmond-proxmox only
for tags it does not find there (v3.93 and older), so `smd align --release v3.93` keeps working.  The mirror
ends when the fleet board shows no station on a pre-v3.94 smd; bless's `--mirror` flag goes then.

Image-carried host files (`RAW_BASE`, `smd admin host-files`) still come from the proxmox repo: they belong to
the image.  Their URLs change to sigmond-proxmox.

### The bare-metal install test

A rig script, `test-bare-metal.sh` (sigmond-proxmox, beside the nested tests), boots a fresh Debian 13 cloud
VM on the rig, runs `docs/install-bare-metal.md`'s commands extracted from the doc itself (so the doc cannot
drift from the test), at the sigmond tag under test, then the bring-up command that version ships (today `smd bringup dasi2
--non-interactive`; from v3.95 the sensed bring-up, which Rob agreed 2026-11-20), and passes
when every topology-enabled component without hardware reads dormant and the rest healthy (`smd check`
exit 0 or only dormant lines).  It writes a record bless reads.

### The rename

After the code lands, Michael renames the GitHub repo (repo admin; GitHub keeps redirects).  Then every
`HamSCI/sigmond-appliance` URL in sigmond, sigmond-proxmox, ops (`fleet-ssh`, rig sync), the install page and
INSTALL.md changes, and the rig's checkout path follows (`sync-rig.sh`, the build scripts' `REPO` defaults).
Image file names change from `sigmond-appliance-<v>-<stamp>` to `sigmond-<v>-<stamp>`.

## Calls from the builder check (Claude's, 2026-11-20)

- **The image repo keeps a tag.**  sigmond-proxmox still gets `vN`, at the commit the image built from, and the
  manifest keeps its `appliance_tag`, `appliance_commit` and `wizard_commit` rows.  v3.93's `verify_release`
  checks that tag against `appliance_commit`, gate 5b reads firstboot-v3.sh at it, and pm-align and
  `image_file_drift` fetch host files at it.  `components.toml` lists sigmond-proxmox's commit as a row like any
  other component.
- **The sigmond tag is cut at the bless, not before the build.**  Until the bless a candidate is named by its
  manifest (`smd align --manifest`, as the canaries use today) and by the sigmond commit the build installed.  A
  canary that finds a sigmond bug means a new candidate build at the fixed commit; no tag moves and no version
  number is spent.  Bless tags sigmond at the manifest's `sigmond` row and sigmond-proxmox at
  `appliance_commit`, and refuses when either tag already exists elsewhere.  RELEASE.md's ladder says so.
- **Native pins have one owner each.**  `release-list --write` copies `_UBERSDR_COMMIT`, `_ONION_COMMIT` and
  `_WSJTX_COMMIT` from bin/smd at the sigmond commit and never rewrites them.  The ka9q-radio row comes from
  ka9q-python's `ka9q_radio_compat` file at the listed ka9q-python commit, its one owner today.  Every other
  row takes the head of that component's main.  A test keeps list and owners equal.
- **pm-align follows.**  `scripts/proxmox/pm-align.py` (it runs on the Proxmox host) gets the same switch to
  HamSCI/sigmond with the same fallback.  The mirror ends only when the board shows no station VM on a
  pre-v3.94 smd and no host on a pre-v3.94 pm-align.
- **Install pages.**  sigmond's README and `docs/install.md` point to both paths.  INSTALL.md and
  install-page.html (the Proxmox image path) stay in sigmond-proxmox.  Gates 7 and 8 match "sigmond vN"; gate 7
  also requires `docs/install-bare-metal.md` to read "Verified against: sigmond vN".  Bless reads origin/main of
  both repos, as gate 7 does today for one.
- **Pushing during the ladder.**  The golden VM clones from GitHub, so `components.toml` and the commits it
  names go out before each candidate build, file lists on the bus first.  Tags go out only at the bless.

## Constraints

- smd stays stdlib-only and prints ASCII; smd refuses sudo.
- Precise positions play no part.
- Commits stay local until Michael says push; the bus gets file lists first.
- Rob owns much of the appliance code: his approval covers the move; the bus hears each step.

## Left to the builder

- The exact component set in `components.toml` (mirror today's manifest rows).
- How `smd install` takes the list (a `--release-list PATH` or reading the tag's file).
- The bare-metal test's VM template on the rig and its record format.

## The bare-metal pass rule (Michael, 2026-11-20: "Waiting is a pass")

With no RX888 the radiod sits dormant, and its consumers cannot run.  The test passes when every component
installs at the list's commits, and each radiod consumer reads "waits on dormant radiod" in `smd check`, with
no other fault.  `smd check` gains that reading: a component whose only missing input is a dormant radiod is
waiting, not failing.  The test proves the install path; B4 and SIGMA prove reception.

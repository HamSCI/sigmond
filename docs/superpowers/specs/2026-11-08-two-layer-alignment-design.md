# One release, two modalities: a shared layer and a platform layer

Status: draft for Michael and Rob, 2026-11-08.  Target: the release after v3.86.
Builds on `2026-09-24-smd-align-design.md` (smd align, pm-align) and
`2026-11-04-bare-metal-install-design.md` (repo installs on a ham's own machine).

## 1. Why

Sigmond now installs two ways.  The USB image lays down a Proxmox host with a decoder VM; a repo
install puts sigmond on a machine the operator already runs (sigma, B1/B2).  Both modalities share
almost everything sigmond contains and manages.  They differ in what each needs underneath.

Michael, 2026-11-08: the differences between Proxmox and bare metal will stay far more stable than
the ongoing development inside sigmond that both share.  So align the shared part the same way
everywhere, and let each modality carry its own slower-moving layer.

Today the tools blur that line.  A dry run of `smd align` on sigma (bare metal, 11-08) shows three
symptoms:

- It ends with "host: unknown (install-time) — never aligned; v3.85 available: run pm-align on the
  host".  sigma has no Proxmox host.  `_align_host_line` assumes one on every machine.
- It lists `/usr/local/sbin/sigmond-site-timing` and `sigmond-location-check` as "image-carried
  files, not installed".  Both modalities need those scripts; only the image delivers them.
- Blessing (`bless-release.sh`, gate 5) asks for test evidence on the image only.  Nothing shows
  that a bare-metal station aligns to a release and runs.

## 2. The two layers

**The shared layer** holds every component the release manifest pins: sigmond itself, ka9q-radio,
hf-timestd, the recorders, decoders (ubersdr_wsprd, wsjtx), station-web, ka9q-python and the
rest; the files sigmond installs from those checkouts; configs rendered from site-profile.toml;
bring-up.  It moves weekly or faster.  One manifest pins it, identical for both modalities.

**The platform layer** holds what one modality needs and the other does not.  It moves rarely.

| Proxmox | Bare metal |
|---|---|
| host scripts in `/usr/local/sbin` (pm-align, pm-heartbeat, pm-storage, the wizard) | the isolcpus drop-in for radiod's cores |
| CPU-pin hookscript, radiod fence, IRQ affinity, resctrl | radiod placement on performance cores |
| netwatch, netfix, the host's NAT and vmbr1 | the operator's own network, untouched |
| RAC on the host, VM pairing for `smd remote` | sigmond-rac as the local entry point |
| vfio passthrough of the RX888's controller | the RX888 on the machine's own USB |
| the image's Debian package set | the operator's Debian, the operator's apt |

FFTW wisdom straddles the line: the plans belong to the CPU, so each modality keeps its own, but the
code that judges and plans them (`sigmond.wisdom`) belongs to the shared layer.

## 3. Decisions proposed

1. **Each release records one version per platform layer**, `proxmox` and `bare_metal`, small
   integers.  They travel in a separate release asset, `platform.txt`, never inside the manifest:
   `align.fetch_release` parses the manifest strictly (`align.py:87-125`), and a new block there
   would make every v3.85 station refuse the whole release.  An old smd ignores the extra asset.
   - Each layer's version lives in its own repo beside its files: `PLATFORM_VERSION` in
     `sigmond-appliance/` for Proxmox, in `sigmond/lib/sigmond/platform/` for bare metal.
   - A test in each repo hashes that layer's files and fails when they change without a bump, so
     the number cannot drift from what it describes.
   - The machine records what it holds: pm-align's `host-aligned.json` gains a `platform` field;
     bare metal gets `/var/lib/sigmond/platform-aligned.json`, written by the bare-metal adapter.
   - Most releases carry the same platform numbers as the one before.
2. **`smd align` does the shared layer the same way on every machine, then asks a platform
   adapter.**  The shared step stays the code that runs today.  The adapter answers one question:
   is this machine's platform layer at the release's platform version, and if not, what moves it?
   - Proxmox adapter: reads pm-align's record as `_align_host_line` does now; its remedy stays
     "run pm-align on the host", since the VM never acts on the host.
   - Bare-metal adapter: checks radiod placement, the wisdom verdict and the isolcpus drop-in.
     `--apply` repairs placement and wisdom.  It never edits GRUB unless the operator opted in, as
     the bare-metal spec requires (F16, `2026-11-04-bare-metal-install-design.md`); without the
     opt-in it prints the change and counts the layer current, "isolcpus declined by the
     operator".
   - The adapter is chosen by `provenance.is_appliance_install()` alone: the image's markers mean
     Proxmox, their absence means bare metal.  A repo install inside an operator's own KVM guest
     therefore runs the bare-metal adapter, which fits: sigmond manages no host there.  An
     appliance VM that has lost its marker but still holds `host-aligned.json` gets a warning
     naming both facts, never a silent switch of adapter.
3. **Files go to the layer where they run, not the layer that happened to deliver them.**
   `sigmond-site-timing` and `sigmond-location-check` live in sigmond-appliance, and
   `align.image_file_drift` already fetches them from the release's `appliance_commit`
   (`align.py:251`).  Both modalities need them, so the shared align step installs and refreshes
   them on every machine from that same commit.  They stay in sigmond-appliance; only their
   label changes, from "image-carried" to "shared files from the appliance repo".  Every release
   has an `appliance_commit`, so a repo install has a meaningful one too.  (Builder's call: moving
   the scripts into sigmond would break the appliance's build and bless scripts for no gain.)
4. **Blessing tests the shared layer in both modalities.**  The ladder gains one rung after the B4
   hardware test: align a bare-metal test machine to the candidate with
   `smd align --release <tag> --apply`, then require what `_align_live_passed` requires today
   (the three `_ALIGN_LIVE_CHECKS`, `complete` true, `failed` empty).
   - Evidence: a small script on the rig, `test-bare-metal.sh <tag> <host>`, runs the align over
     ssh and writes a PASS or FAIL line keyed on the tag and the manifest's sha256.  Gate 5 still
     wants only the image's PASS; the bare-metal lines go to `bare-metal-evidence.txt` on the
     Release, which a bare-metal align requires (§8).
   - A platform layer whose version moved needs its own full test: an image build for Proxmox; for
     bare metal, a fresh Debian 13 machine (a throwaway VM on the rig serves, since it carries no
     image marker) taken through the repo install from nothing.  One that did not move needs none.
5. **The image delivers a release; it no longer defines one.**  A release names a shared-layer pin
   set and two platform versions.  The image carries the Proxmox platform layer plus that pin set;
   a repo install reaches the same pin set from the repos.  The bless gate keeps its rule that
   blessed means tested; it just tests in two places.

## 4. What stays outside both layers

The operator's Debian on bare metal.  Two bare-metal stations on one release may still differ in
kernel, compiler and libraries.  Align cannot see that today.  A cheap first step: `smd align`
prints the kernel version and the versions of the few packages sigmond builds against (gcc,
cmake, libfftw3, python3) in its report.  Report only: no warning, no change to the exit code,
no expected values in the release.  Pinning an
operator's packages stays out of scope; that machine belongs to its owner.

## 5. Guarding the boundary

The danger lies in Proxmox assumptions leaking into the shared layer, as the pm-align advice did.
One test enforces the boundary: run the shared align path against both a Proxmox-guest fixture and
a bare-metal fixture with each adapter stubbed, and require identical shared-layer plans.  Any
shared-layer code that branches on modality fails that test unless it goes through the adapter.

## 5a. What a station runs comes from its operator, not from a role

Modality does not exhaust the differences.  Stations vary in which parts of sigmond they operate,
and operators decide that after installation.  DASI2 stations share one deployment pattern today,
yet even one of them may add a client, or add a machine on its LAN with a radiod to listen to.
AC0G-B1 and AC0G-B2 show the general case.  They are two separate stations (Michael, 11-08), each
aligned on its own: B1 runs two radiods and nothing else; B2 runs no radiod, and its clients bind
to B1's radiods across the LAN.  Nobody declared those roles.  They follow from what each
operator configured, enabled and bound, and one station listening to another's radiod is just a
binding.

Michael, 2026-11-08: the difference between B1 and B2 should not be pre-defined; it should result
from how an operator deploys them.  Aligning a station means making sure every client has the
latest software to support what gets configured, enabled and run there.

A dry run of `smd align` on each (11-08) shows where today's align falls short of that:

- **B1** would move 17 installed components it does not run.  That suits the rule below: kept
  current, any of them can be enabled tomorrow.  But it lists the decoders (ubersdr, wsjtx,
  ft8_lib) as "missing", though nothing enabled on B1 decodes.
- **B2** holds a ka9q-radio checkout 167 commits ahead, and align's line for it warns that a
  rollback "REBUILDS and RESTARTS radiod" on a host where no radiod is enabled.
- **Neither host sees the other.**  B2's clients depend on B1's radiods; aligning B2 alone could put
  its ka9q-python on a release whose contract B1's radiod does not meet.  Today's B2→B1A
  channel-creation failure (11-08) shows how such a pair breaks in ways neither host reports.

Proposed, with no roles anywhere in the code:

1. **Align keeps every installed component at the release.**  What sits on the host may be
   enabled later, so all of it moves.  A component never installed reads "not installed", never
   "missing" — unless something enabled needs it, as the recorders need the decoders; then it
   reads missing, and `--apply` installs it.
2. **What runs decides restarts and checks.**  Restarts, live checks and anything that rebuilds or
   restarts radiod apply to what the host has enabled.  A ka9q-radio checkout on a host with no
   enabled radiod moves like any other source and restarts nothing.
3. **Stations meet only through radiod's multicast; align checks that contract, not the other
   station.**  B2 depends on B1 only as radiod lets any listener depend on it: B1's radiods
   multicast status and RTP, and B2's clients, through ka9q-python, read them and send commands
   back.  So for each radiod an enabled client binds to (`smd admin sources bind`), align checks
   the contract itself, from the multicast:
   - the status stream decodes with no unknown or shifted tags against the ka9q-radio commit this
     ka9q-python was validated for (`ka9q/types.py`; the drift classes `smd watch ka9q` already
     uses);
   - a short-lived probe channel can be created and verified, as the 11-08 pilot probe did.  That
     probe would have caught today's B2→B1A channel-creation failure.
   The radiod need not belong to any sigmond station.  An operator may run one installed from
   apt on a plain Linux box (Michael, 11-08); if it behaves as radiod should, a sigmond station
   may use it as a data source like any other, and this contract check is the only gate it
   passes.  Its ka9q-radio version may sit older or newer than the one ka9q-python was validated
   for; the drift classes decide whether that matters (pass, warn, or fail on a stream-critical
   field), not the version itself.  The probe channel carries a short lifetime, so it leaves
   nothing behind on a radiod someone else runs.
   radiod's status carries no version, so align cannot read the other station's release from the
   multicast.  When the radiod's host happens to run sigmond on the same LAN, align may show its
   release for information.  It never refuses because of another station: each station aligns
   on its own, and a contract failure reads as a loud warning naming the radiod and what broke.

4. **The platform layer follows what runs, too.**  The bare-metal adapter checks radiod placement,
   the isolcpus opt-in and wisdom only where a local radiod is enabled; on a host with none it
   checks client CPU affinity and multicast reception (the IGMP querier).

## 6. Order of work

1. v3.86 ships on today's mechanism, plus two small things: the sigma align-and-check rung run by
   hand, and `_align_host_line` saying nothing about pm-align off Proxmox.
2. The shared align step installs the two appliance scripts on every machine (decision 3).
3. The adapter interface and the bare-metal adapter (decision 2), with the boundary test (§5).
4. Alignment shaped by what is installed, enabled and bound (§5a).
5. `platform.txt`, the ladder rung in `bless-release.sh` (decisions 1, 4).

## 7. Open questions

- Does a platform version need its own tag, or does the release tag carry it?  Proposed: the
  release carries it; a platform change rides the next release like any other change.
- Where do B1 and B2 sit?  Answered in §5a: two stations, both on the bare-metal platform layer;
  what each operator enabled and bound decides what restarts, and B2's binding to B1's radiods
  is checked through the multicast contract (§5a.3).
- Does a DASI2 golden-image clone count as Proxmox?  Yes: the clone keeps the image's marker and
  runs on a Proxmox host the same scripts set up, so both uses of `is_appliance_install()` (the
  adapter choice, and whether sigmond owns the kernel command line) hold for it.

## 8. For Michael and Rob

Decided by Michael, 2026-11-08:

- **sigma carries the bare-metal rung.**  `test-bare-metal.sh <tag> sigma` aligns it to the
  candidate and records PASS or FAIL.
- **Two stations bound across the LAN join the ladder: B1 and B2 verify it.**  After sigma, align
  B1, then B2, each as its own station, and require the same live checks on both plus a clean
  multicast-contract check (§5a.3) from B2 against both of B1's radiods.  This rung touches no image; Rob's 10-01 ruling kept
  B1 and B2 out of image work, so Rob should know they now serve as release infrastructure.

- **Blessing goes per modality.**  The image's PASS blesses a release for Proxmox stations, as
  now, and they may align at once.  A bare-metal station's `smd align` refuses a release until
  bare-metal evidence exists for it: sigma's PASS and the B1/B2 PASS.  That evidence may arrive
  after the release goes out, as an extra asset on the same Release (`bare-metal-evidence.txt`,
  keyed on the tag and the manifest's sha256), since gate 6 forbids a second Release for a tag.
  So the Proxmox fleet never waits on sigma, a bare-metal station never takes an untested
  release, and "blessed" stays honest in both modalities.

Builder's calls made in this draft, for checking: the scripts stay in sigmond-appliance (decision
3); platform versions travel in `platform.txt`, not the manifest (decision 1); the bare-metal
adapter never edits GRUB without opt-in (decision 2); the adapter keys on
`is_appliance_install()` alone (decision 2); the OS report changes no exit code (§4).

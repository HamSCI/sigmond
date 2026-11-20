# Release process

> **Audience:** release manager (Michael or Rob) and the agents working beside them
> **Status:** current from v3.94; v3.94 runs this ladder first
> **Verified against:** the hermetic tests in `release/test-bless-*.sh`; no release has yet blessed through it

A sigmond release names one commit of sigmond and one commit of every component sigmond installs.
sigmond holds the version.  `release/components.toml` lists the components' commits, the tag `vN`
on HamSCI/sigmond names sigmond's own commit, and `release/bless-release.sh` cuts that tag once the
evidence stands.  The image repo, HamSCI/sigmond-proxmox (named sigmond-appliance before the
rename), builds the Proxmox USB image from the list and keeps a `vN` tag of its own at the commit
the image came from.

"Verified" used to mean different things depending on who said it and when.  A fix once shipped
"verified" while the venv it ran from had gone stale.  A radiod binary swap passed as "verified"
against a file the running process never loaded.  Each rung below asks for evidence in a fixed
shape, tied to the exact artefact, so the word carries one meaning again.

## The ladder

Run the steps in this order.  Each step on B4 or the rig goes on the claude-bus first.

1. **Write the list.**  On the devbox, in a clean sigmond checkout on main:

   ```bash
   smd admin release-list --write --version vN
   ```

   The command reads the head of each component's main and copies the native pins from their
   owners: `_ONION_COMMIT`, `_WSJTX_COMMIT` and `_UBERSDR_COMMIT` from `bin/smd`, ka9q-radio from
   ka9q-python's `ka9q_radio_compat`, ft8_lib from psk-recorder's `FT8LIB_PIN`.  It writes
   `release/components.toml` and commits nothing.

2. **Review, commit, push.**  Read every row against what the canaries run today.  Commit the
   list.  Post the file list on the bus, then push.  The golden VM clones from GitHub, so the list
   and every commit it names must reach GitHub before the build.

3. **Build a candidate** at that sigmond commit: on the rig, `build-golden-vm.sh`, then
   `build-usb-v3.sh`.  The build installs the list's commits and refuses to ship when its capture
   differs from the list.  It writes `sigmond-vN-<YYYYMMDD>-release.img` with its `.sha256` and
   `.manifest.txt`, and puts them in each publish target's `pending/` folder.

4. **Nested image test.**  `test-nested-v3.sh` on the new image.  Gate 5 reads its log.

5. **Update test.**  `test-update-v3.sh` carries a station on the previous release forward to the
   candidate and back.

6. **Bare-metal test.**  Push the line `git checkout vN` into step 2 of
   `docs/install-bare-metal.md` first.  Then, on the rig:

   ```bash
   ./test-bare-metal.sh vN
   ```

   The test boots a fresh Debian VM, runs the doc's own commands at the candidate's sigmond commit,
   then `smd check`.  It writes a record to `$RIG_DIR/bare-metal/`, and gate 10 reads it.  With no
   RX888 on the VM, radiod stays dormant, and each radiod consumer reads "waits on dormant radiod":
   the test counts that as a pass (Michael, 2026-11-20).  The test proves the install path; B4 and
   SIGMA prove reception.

7. **Canaries: AC0G-B4 and SIGMA,** for about a day.  B4 runs Proxmox (`smd align` in the VM,
   `pm-align` on its host); SIGMA runs bare metal.  Each aligns to the candidate's manifest, copied
   from the rig.  No Release exists yet:

   ```bash
   smd align --manifest <candidate>.manifest.txt            # dry run
   smd align --manifest <candidate>.manifest.txt --apply
   pm-align --manifest <candidate>.manifest.txt --release-tag vN --apply   # B4's host only
   ```

   Every line those tools print names the target `CANDIDATE vN (not blessed)`.  Watch what a
   release can break: services stable, no OOM kills, uploads arriving at wd30, timing healthy, an
   align free of surprises.  A third canary joins only when B4 or SIGMA shows a problem.

8. **A sigmond bug found on the way means a new candidate.**  Fix it on main, write the list again
   (step 1), and build again from the fixed commit.  No tag exists yet, so none moves, and the
   version number stays unspent.

9. **Write the release lines,** commit them, post the file list on the bus, push:
   - the image repo's `INSTALL.md`: `**Status:** current`, and `**Verified against:**` naming
     `sigmond vN`, the image commit and the hardware run;
   - the image repo's `docs/install-page.html`: the new image's file name, and every step the
     release changed;
   - sigmond's `docs/install-bare-metal.md`: `**Verified against:** sigmond vN` (its
     `git checkout vN` line went in at step 6);
   - sigmond's `CHANGELOG.md`: rename the draft heading `## sigmond vN (draft)` to
     `## sigmond vN — blessed <date>`, and finish its **How it was proven**.

10. **Bless.**  On the rig, dry run first, then apply:

    ```bash
    $HOME/appliance/repos/sigmond/release/bless-release.sh vN --mirror
    $HOME/appliance/repos/sigmond/release/bless-release.sh vN --mirror --apply
    ```

    Then watch one station on the previous release find vN with `smd align`.  For the first
    release after the image repo's rename, make that a v3.93 station whose GitHub API answers
    (`curl -s https://api.github.com/rate_limit` shows `remaining` above 0): its API path follows
    GitHub's redirect and finds vN.  A rate-limited v3.93 station refuses until the API resets
    (next section).

## The mirror, and when it ends

smd and pm-align from v3.93 and older read Releases from the image repo alone.  So while any of
them remains, the bless publishes the same Release, same notes, same manifest, on the image repo
too: that copy goes out with `--mirror`.  A v3.93 station's first `smd align --apply` moves it to
the new smd, which reads HamSCI/sigmond first.

The bless refuses until it hears `--mirror` or `--no-mirror` (gate 6b).  Choose `--mirror` until
the fleet board shows no station VM on a pre-v3.94 smd and no Proxmox host on a pre-v3.94 pm-align.
After that, `--no-mirror`, and a later release removes the flag.

The image repo's rename (sigmond-appliance to sigmond-proxmox) leaves the mirror working on either
side.  GitHub redirects the old name to the new, never the new to the old, so the bless looks the
repo up first: the GitHub repo its checkout's origin names, then HamSCI/sigmond-proxmox, then
HamSCI/sigmond-appliance.  `GH_MIRROR_SLUG` overrides the search.

One gap stays open after the rename.  A v3.93 smd or pm-align asks the GitHub API for the newest
Release on HamSCI/sigmond-appliance.  The API follows GitHub's redirect to sigmond-proxmox, so
the station finds the new Release.  When the API refuses it for its rate limit, the old code falls
back to the repo's web page.  That page now carries the new repo name, which the old code does
not expect, so the station refuses to align.  It recovers by itself once the API's hourly limit
resets; nothing on the station needs fixing.  After the first release past the rename, check one
v3.93 station on the API path (step 10).

## What the bless checks

`release/bless-release.sh vN (--mirror | --no-mirror) [--apply]` reports every gate on its own line
and refuses unless all pass.  Without `--apply` it changes nothing.  The script's header comment
holds the reasoning behind each gate; this list says what each one asks.

| Gate | Asks |
|---|---|
| 0 | `vN` reads as a release version, not a `--dev` build stamp |
| 1 | the manifest's sigmond commit and its `appliance_commit` each sit on their repo's `origin/main`, and no tag `vN` names another commit in either repo, locally or on origin |
| 2 | both checkouts clean |
| 3 | exactly one image `sigmond-vN-*-release.img` (or the older `sigmond-appliance-` stem), its `.sha256` verifies |
| 4 | the manifest matches `release/components.toml` at the sigmond commit, row for row, and the list's `version` reads `vN` |
| 5 | the newest nested-test run of this exact image reached `PHASE D PASS` |
| 5b | the image carries the `firstboot-v3.sh` of `appliance_commit` |
| 6 | no Release `vN` on HamSCI/sigmond, nor, with `--mirror`, on the image repo |
| 6b | `--mirror` or `--no-mirror` given |
| 7 | the image repo's `INSTALL.md` (Status `current`, Verified against `sigmond vN`) and install page (the image's file name), and sigmond's `docs/install-bare-metal.md` (Verified against `sigmond vN`, checks out `vN`), all at `origin/main` |
| 8 | sigmond's `CHANGELOG.md` at `origin/main` holds one `## sigmond vN` heading, not `(draft)`, with text under it |
| 9 | the image's onion, wsjtx and ubersdr_wsprd checkouts, build records and decoder provenance equal `bin/smd`'s constants at the sigmond commit |
| 10 | the newest bare-metal record for `vN` at the sigmond commit reads `result: PASS`, and the doc's commands have not changed since it ran |

Gates 7, 8 and 10 read `origin/main` through `git show`.  The release lines land after the tests,
in commits later than the build's, so the build's commits hold the previous release's words, and a
working tree may differ from what the team pushed.  Gate 1 fetches both origins first.  A version
matches only as a whole token: `v3.7` does not satisfy `v3.70`, and `v3.70` does not satisfy
`v3.701`, `v3.70.1` or `v3.70-rc1`.

Gate 10 compares the record's `doc_commands_sha256` with the digest `sigmond.bare_metal_doc` takes
today of `docs/install-bare-metal.md`.  A prose edit leaves the digest alone.  A changed command, or
a new one, asks for a new run of the test.

## What `--apply` does

After every gate passes, the bless builds the release notes, checks them for leaks, prints them,
and asks the operator to type `publish vN`.  The notes open with the version's `CHANGELOG.md`
section under "What's new", then a record (both commits, the image, its checksum, both tests), then
sigmond's commits since the previous release tag.

The leak check reads the whole notes and the published changelog file.  An IPv4 address or a
`user@host` stops the bless outright.  A path, a domain or a hostname-shaped word gets flagged, and
then the operator must type the longer phrase that counts the flagged lines.  The confirmation
reads `/dev/tty`, so a script cannot give it.

Then, in this order:

1. **Tags.**  The bless cuts an annotated tag `vN` at the sigmond commit in sigmond and at
   `appliance_commit` in the image repo, each only where absent, pushes both, and reads origin back.
   A tag already on origin at the right commit (a re-run after a publish that stopped part way)
   stays as it stands.  Nothing forces or moves a tag.
2. **Releases.**  `gh release create vN --verify-tag` on HamSCI/sigmond, with the manifest and
   `.sha256` attached.  With `--mirror`, the same command, notes and files on the image repo.  The
   image itself (several GB) stays off GitHub, beyond its 2 GiB asset cap.  A failed mirror leaves
   the HamSCI/sigmond Release standing, prints the command to finish by hand, and ends the bless at
   exit 1.
3. **Downloads.**  The bless moves the image out of `pending/` into each publish target's download
   folder, then puts beside it `<stem>.INSTALL.md`, `<stem>.INSTALL.html` and `<stem>.CHANGELOG.md`
   (the bytes gates 7 and 8 checked) and refreshes `sigmond-flash-latest.sh`.  The targets come from
   `publish-targets.conf` beside the image repo's `publish-lib.sh`.  A target that cannot be reached
   costs a WARNING and a by-hand recipe, not the bless: the Release has already gone out.

Only the bless puts an image in a download folder.  Untested builds wait in `pending/`.  On
2026-09-02 a v3.36 image that the nested test went on to stop sat in the download folder for 37
minutes, under the very file name a blessed release would carry, before a rebuild overwrote it.
Anyone who fetched the image and its `.sha256` together in that window holds a pair that verifies
clean: the checksum confirms an honest download of the wrong build.

Republish the shared web page "Installing a Sigmond Station" from `docs/install-page.html` after the
bless.

## Rolled

A release reaches machines two ways.

- **A new machine** gets the blessed image written to a stick, or follows
  `docs/install-bare-metal.md` on a Debian machine it already runs.
- **A live machine never gets reimaged.**  `smd align --apply` moves it to the blessed manifest, and
  `pm-align --apply` moves a Proxmox host.  `smd fleet status` and the fleet board show who stands
  where.

## Rules that have cost real time

**The manifest comes from a capture, never from a hand.**  `build-golden-vm.sh` captures
`smd version` inside the golden VM; `build-usb-v3.sh` adds the version, the commits and the checksum.
A hand-typed row drifts the moment someone forgets a component bump, and nothing downstream would
notice.  Since the release home, the build also refuses a capture that differs from the list.

**Check the venv, not the checkout.**  A component can move in its git checkout while the running
process still imports a stale venv.  On B4 a fix passed as "verified" live for a full day while its
venv had not moved.  Read the venv's installed package, or its site-packages, directly.

**Check `/proc/PID/exe`, not the file you installed.**  A binary swap can land the right file at the
right path while the process keeps the old inode open, or while a drop-in points `ExecStart`
elsewhere.  A stale `10-patched.conf` once turned a radiod swap into a silent no-op.
`readlink /proc/<pid>/exe` shows what runs.

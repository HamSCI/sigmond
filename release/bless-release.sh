#!/bin/bash
# bless-release.sh — the executable release gate for sigmond.
#
# "Verified" has meant different things at different times on this fleet: a
# fix once shipped "verified" while the venv it lived in was stale; a binary
# swap was "verified" against a file that was not the one running. This
# script is what makes rung 3 of the release ladder (built -> tested ->
# BLESSED -> rolled) refuse to happen without evidence.
#
# The release lives in sigmond (release home, 2026-11-20).  The version tag vN
# on HamSCI/sigmond names sigmond's own commit; release/components.toml at that
# commit names every other component's.  The image repo, sigmond-proxmox, keeps
# a vN tag of its own at the commit the image built from (the manifest's
# appliance_commit): v3.93 smd verifies it, gate 5b and pm-align read files at
# it.  Neither tag exists before the bless.  A candidate goes by its manifest
# and its sigmond commit until then, and the bless cuts both tags.
#
# It refuses to cut a GitHub Release unless ALL of the following hold,
# reported individually so an operator can see exactly which one stopped it:
#
#   0. the version is a real release version (no -dev, no +)
#   1. the manifest's sigmond commit and its appliance_commit each sit on
#      their repo's origin/main, and no tag <version> already names another
#      commit in either repo (local or on origin)
#   2. both working trees are clean
#   3. the image and its .sha256 exist and the checksum verifies
#   4. the manifest contains a components block, matches
#      release/components.toml at the sigmond commit, and that list's version
#      is <version>
#   5. test evidence exists and records a PASS for THIS exact image
#   5b. the image carries the firstboot-v3.sh of appliance_commit
#   6. no Release already exists for <version> on HamSCI/sigmond, nor, with
#      --mirror, on the image repo; 6b. --mirror or --no-mirror was given
#   7. the install instructions name this release: the image repo's
#      origin/main INSTALL.md reads Status: current and Verified against:
#      sigmond <version>, its docs/install-page.html names this image's file
#      name, and sigmond's origin/main docs/install-bare-metal.md reads
#      Verified against: sigmond <version> and checks out <version>
#   8. the changelog names this release: sigmond's origin/main CHANGELOG.md
#      holds exactly one "## sigmond <version>" heading, not marked (draft),
#      with text under it
#   9. the image's native sources come from the release's pins: the
#      manifest's onion, wsjtx and ubersdr_wsprd checkouts, the commits the
#      image's builds record (native_built_* lines) and the decoders'
#      .provenance commits (native_provenance_* lines) equal the constants
#      in bin/smd at the manifest's sigmond commit
#  10. the newest bare-metal install record for <version> at that sigmond
#      commit reads result: PASS, and it ran the commands sigmond's
#      origin/main docs/install-bare-metal.md holds today
#
# Dry-run by default, matching smd update's contract on this fleet.
# --apply is required to create anything, and even then only after every
# gate above has passed and the operator typed the confirmation.  It then
# tags both repos (annotated, each only where absent), pushes both tags,
# creates the Release on HamSCI/sigmond and, with --mirror, the same Release
# on the image repo, where v3.93 and older smd look for it.  The release
# notes open with the version's changelog section, under "What's new", and
# the bless also publishes three files beside the image at every publish
# target: <image stem>.INSTALL.md, <image stem>.INSTALL.html and
# <image stem>.CHANGELOG.md.
#
# Usage: bless-release.sh <version> (--mirror | --no-mirror) [--apply]
#
#   --mirror     also publish the Release on the image repo.  Required while
#                any station VM runs a pre-v3.94 smd or any Proxmox host a
#                pre-v3.94 pm-align: those read Releases from the image repo
#                alone.  release/RELEASE.md says when the mirror ends.
#   --no-mirror  publish on HamSCI/sigmond alone.
#
# Env overrides (the verification suite points them at scratch fixtures):
#   SIGMOND_REPO    sigmond checkout (default: the checkout holding this script)
#   PROXMOX_REPO    image repo checkout (default: APPLIANCE_REPO if set, else
#                    $HOME/appliance/repos/sigmond-proxmox when that is a git
#                    checkout, else $HOME/appliance/repos/sigmond-appliance,
#                    the checkout's name before the rename; the same order
#                    build-usb-v3.sh follows)
#   RIG_DIR         build/test staging dir: images, .sha256, .manifest.txt,
#                    test-v3.log, bare-metal/ (default: $HOME/appliance/v3)
#   GH_REPO_SLUG    the release repo (default: HamSCI/sigmond)
#   GH_MIRROR_SLUG  the mirror (default: the GitHub repo PROXMOX_REPO's origin
#                    names, else HamSCI/sigmond-proxmox; a slug GitHub does
#                    not know falls back to HamSCI/sigmond-appliance, the
#                    image repo's name before the rename)
#   PUBLISH_LIB     publish-lib.sh; publish-targets.conf sits beside it
#                    (default: $PROXMOX_REPO/publish-lib.sh)
set -u
# systemd-run sets no HOME, and set -u would stop at the first use below.
# Take the account's home from the password database instead.
: "${HOME:=$(getent passwd "$(id -un)" | cut -d: -f6)}"

say(){ echo "[bless $(date '+%T')] $*"; }

_HERE="$(cd "$(dirname "$0")" && pwd)"
SIGMOND_REPO="${SIGMOND_REPO:-$(cd "$_HERE/.." && pwd)}"
# A rig's checkout takes the new name only when someone renames it, so the
# bless looks for either name, as build-usb-v3.sh does.
if [ -n "${PROXMOX_REPO:-}" ]; then
    PX_REPO="$PROXMOX_REPO"
elif [ -n "${APPLIANCE_REPO:-}" ]; then
    PX_REPO="$APPLIANCE_REPO"
elif git -C "$HOME/appliance/repos/sigmond-proxmox" rev-parse --git-dir >/dev/null 2>&1; then
    PX_REPO="$HOME/appliance/repos/sigmond-proxmox"
else
    PX_REPO="$HOME/appliance/repos/sigmond-appliance"
fi
RIG_DIR="${RIG_DIR:-$HOME/appliance/v3}"
GH_REPO="${GH_REPO_SLUG:-HamSCI/sigmond}"
PUBLISH_LIB="${PUBLISH_LIB:-$PX_REPO/publish-lib.sh}"
TESTLOG="$RIG_DIR/test-v3.log"
BM_DIR="$RIG_DIR/bare-metal"

# _py <module> <args...> — run a sigmond module from SIGMOND_REPO's lib.
_py(){ local m="$1"; shift; PYTHONPATH="$SIGMOND_REPO/lib" python3 -m "$m" "$@"; }

APPLY=0
MIRROR=""
VERSION=""
for a in "$@"; do
    case "$a" in
        --apply) APPLY=1 ;;
        --mirror|--no-mirror)
            m="${a#--}"
            [ -z "$MIRROR" ] || [ "$MIRROR" = "$m" ] || { echo "choose one of --mirror and --no-mirror" >&2; exit 2; }
            MIRROR="$m"
            ;;
        -*) echo "unrecognized argument: $a" >&2; exit 2 ;;
        *)
            [ -z "$VERSION" ] || { echo "unexpected extra argument: $a" >&2; exit 2; }
            VERSION="$a"
            ;;
    esac
done
[ -n "$VERSION" ] || { echo "usage: bless-release.sh <version> (--mirror | --no-mirror) [--apply]" >&2; exit 2; }

GATE_NAMES=()
GATE_RESULTS=()
GATE_DETAIL=()
FAILED=0
gate_pass(){ GATE_NAMES+=("$1"); GATE_RESULTS+=("PASS"); GATE_DETAIL+=("$2"); }
gate_fail(){ GATE_NAMES+=("$1"); GATE_RESULTS+=("FAIL"); GATE_DETAIL+=("$2"); FAILED=1; }
# WARN does not block.  It exists so "this cannot be checked" never renders
# as "this was checked and was fine" -- the distinction gate 5b needs for
# artefacts built before the manifest carried a firstboot hash.
gate_warn(){ GATE_NAMES+=("$1"); GATE_RESULTS+=("WARN"); GATE_DETAIL+=("$2"); }

# ---- Gate 0: the version is a real release version -------------------------
# build-usb-v3.sh --dev stamps v0.0-dev+<sha>, which PASSES the build's own
# v[0-9]* format check (the glob only demands "v" + a digit). This gate is
# the only thing standing between a development image and a blessed release.
case "$VERSION" in
    *-dev*|*+*)
        gate_fail "0 (real release version)" \
            "'$VERSION' contains -dev or + -- this is a --dev build stamp (build-usb-v3.sh's v0.0-dev+<sha>), which passes the build's own v[0-9]* format check but must never be blessed"
        ;;
    v[0-9]*)
        gate_pass "0 (real release version)" "$VERSION"
        ;;
    *)
        gate_fail "0 (real release version)" \
            "'$VERSION' does not look like a release version (expected v<N>...)"
        ;;
esac

# ---- The image and its manifest, resolved once -------------------------------
# Gate 1 needs the two commits the manifest names, and gate 3 checks the image
# itself, so the file names come first.  The image stem is sigmond-<version>-
# <date>-release; images built before the rename carry sigmond-appliance-.
# Both count, and two matches refuse at gate 3.
IMG_MATCHES=()
if [ -d "$RIG_DIR" ]; then
    for f in "$RIG_DIR"/sigmond-"${VERSION}"-*-release.img "$RIG_DIR"/sigmond-appliance-"${VERSION}"-*-release.img; do
        [ -e "$f" ] && IMG_MATCHES+=("$f")
    done
fi
_M0=""
[ ${#IMG_MATCHES[@]} -eq 1 ] && _M0="${IMG_MATCHES[0]%.img}.manifest.txt"
_g9_row(){ # _g9_row <manifest> <name>: the name's commit in the components block
    awk -v n="$2" '/^components/{c=1; next} c && /^[^ ]/{c=0}
                   c && $1==n {print $2; exit}' "$1"
}
_g9_pre(){ # _g9_pre <manifest> <key>: a preamble value
    awk -F': *' -v k="$2" '/^components/{exit} $1==k {print $2; exit}' "$1"
}
SIG_ROW=""; PX_ROW=""
if [ -n "$_M0" ] && [ -f "$_M0" ]; then
    SIG_ROW="$(_g9_row "$_M0" sigmond)"
    PX_ROW="$(_g9_pre "$_M0" appliance_commit)"
fi

# ---- Gate 1: the commits sit on origin/main, and no tag names another --------
#
# The tags are cut HERE, at the bless, not before the build (release home,
# 2026-11-20).  A canary that finds a sigmond bug means a new candidate at the
# fixed commit; no tag ever moves.  So this gate asks two things of each repo:
# the manifest's commit is on origin/main, and tag <version> either is absent
# (--apply cuts it) or already names that very commit (a re-run after a
# publish that stopped part way).  A tag anywhere else, in the checkout or on
# origin, refuses: some other commit already carries the version's name.
SIG_COMMIT=""; PX_COMMIT=""
G1_BAD=(); G1_OK=()
# _g1_repo <label> <checkout> <manifest commit> <var to set>
_g1_repo(){
    local label="$1" repo="$2" want="$3" var="$4" full err lt rt
    if ! git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
        G1_BAD+=("$label: not a git checkout: $repo"); return
    fi
    err="$(git -C "$repo" fetch origin --quiet 2>&1)" \
        || say "WARN: git fetch origin failed in $repo ($err) -- using cached origin/main"
    if [ -z "$want" ]; then
        G1_BAD+=("$label: the manifest names no commit (see gates 3 and 4)"); return
    fi
    full="$(git -C "$repo" rev-parse -q --verify "${want}^{commit}" 2>/dev/null)"
    if [ -z "$full" ]; then
        G1_BAD+=("$label: commit $want is not in $repo"); return
    fi
    printf -v "$var" '%s' "$full"
    if ! git -C "$repo" merge-base --is-ancestor "$full" origin/main 2>/dev/null; then
        G1_BAD+=("$label: $full is not reachable from origin/main in $repo"); return
    fi
    lt="$(git -C "$repo" rev-parse -q --verify "refs/tags/${VERSION}^{commit}" 2>/dev/null)"
    if ! rt="$(git -C "$repo" ls-remote --tags origin "refs/tags/${VERSION}" "refs/tags/${VERSION}^{}" 2>&1)"; then
        G1_BAD+=("$label: could not read origin's tags in $repo: $(printf '%s' "$rt" | tr '\n' ' ')"); return
    fi
    # A peeled line (^{}) names the commit of an annotated tag; it sorts last.
    rt="$(printf '%s\n' "$rt" | awk 'NF{print $1}' | tail -1)"
    if { [ -n "$lt" ] && [ "$lt" != "$full" ]; } || { [ -n "$rt" ] && [ "$rt" != "$full" ]; }; then
        G1_BAD+=("$label: tag $VERSION already exists elsewhere in $repo (local ${lt:-none}, origin ${rt:-none}; the manifest names $full)")
    elif [ -n "$lt" ] || [ -n "$rt" ]; then
        G1_OK+=("$label $VERSION -> ${full:0:12} (tag already there)")
    else
        G1_OK+=("$label ${full:0:12} on origin/main, tag $VERSION cut at --apply")
    fi
}
_g1_repo sigmond "$SIGMOND_REPO" "$SIG_ROW" SIG_COMMIT
_g1_repo "image repo" "$PX_REPO" "$PX_ROW" PX_COMMIT
G1_NAME="1 (commits on origin/main, tags free)"
if [ ${#G1_BAD[@]} -eq 0 ]; then
    gate_pass "$G1_NAME" "${G1_OK[0]}; ${G1_OK[1]}"
else
    _j=""; for _b in "${G1_BAD[@]}"; do _j="${_j:+$_j; }$_b"; done
    gate_fail "$G1_NAME" "$_j"
fi

# ---- Gate 2: both working trees clean ----------------------------------------
G2_BAD=()
for _r in "$SIGMOND_REPO" "$PX_REPO"; do
    if ! git -C "$_r" rev-parse --git-dir >/dev/null 2>&1; then
        G2_BAD+=("not a git checkout: $_r"); continue
    fi
    DIRTY="$(git -C "$_r" status --porcelain 2>&1)"
    RC=$?
    if [ $RC -ne 0 ]; then
        G2_BAD+=("git status failed in $_r: $DIRTY")
    elif [ -n "$DIRTY" ]; then
        G2_BAD+=("$_r has uncommitted changes: $(printf '%s' "$DIRTY" | tr '\n' ';')")
    fi
done
if [ ${#G2_BAD[@]} -eq 0 ]; then
    gate_pass "2 (clean working trees)" "$SIGMOND_REPO and $PX_REPO clean"
else
    _j=""; for _b in "${G2_BAD[@]}"; do _j="${_j:+$_j; }$_b"; done
    gate_fail "2 (clean working trees)" "$_j"
fi

# ---- Gate 3: image + .sha256 exist, checksum verifies ---------------------
IMG=""
IMGBASE=""
SHAFILE=""
if [ ${#IMG_MATCHES[@]} -eq 0 ]; then
    gate_fail "3 (image + sha256 verified)" \
        "no image matching sigmond-${VERSION}-*-release.img in $RIG_DIR"
elif [ ${#IMG_MATCHES[@]} -gt 1 ]; then
    gate_fail "3 (image + sha256 verified)" \
        "multiple images match sigmond-${VERSION}-*-release.img in $RIG_DIR: $(printf '%s ' "${IMG_MATCHES[@]##*/}")"
else
    IMG="${IMG_MATCHES[0]}"
    IMGBASE="$(basename "$IMG")"
    SHAFILE="${IMG%.img}.sha256"
    if [ ! -f "$SHAFILE" ]; then
        gate_fail "3 (image + sha256 verified)" "no .sha256 for $IMGBASE (expected $(basename "$SHAFILE"))"
    else
        SHA_OUT="$(cd "$RIG_DIR" && sha256sum -c "$(basename "$SHAFILE")" 2>&1)"
        SHA_RC=$?
        if [ $SHA_RC -eq 0 ]; then
            gate_pass "3 (image + sha256 verified)" "$IMGBASE checksum OK"
        else
            gate_fail "3 (image + sha256 verified)" "checksum verification failed for $IMGBASE: $SHA_OUT"
            IMG=""; IMGBASE=""  # do not let a bad-checksum image feed gates 4/5
        fi
    fi
fi

# Everything the gates read or write goes into one temp directory, removed on
# exit.  Gate 7 and 8 put the bytes they check there under the names --apply
# publishes, so what gets published is what was checked.
INSTR_DIR="$(mktemp -d)"
trap '[ -n "${INSTR_DIR:-}" ] && rm -rf "$INSTR_DIR"' EXIT

# ---- Gate 4: the manifest matches the release list -------------------------
#
# release/components.toml at the manifest's sigmond commit names the commit of
# every component the release ships, sigmond-proxmox among them (the manifest's
# appliance_commit).  The image must carry exactly those: sigmond.release_list
# owns that comparison, for this gate as for the build and smd.  Its version
# must be this one, so a list written for another release cannot bless this.
G4_NAME="4 (manifest matches the release list)"
MANIFEST=""
if [ -n "$IMG" ]; then
    MANIFEST="${IMG%.img}.manifest.txt"
    MANIFESTBASE="$(basename "$MANIFEST")"
    LIST="$INSTR_DIR/components.toml"
    if [ ! -f "$MANIFEST" ]; then
        gate_fail "$G4_NAME" "manifest missing: $MANIFESTBASE"
        MANIFEST=""
    elif ! grep -qi '^components' "$MANIFEST"; then
        gate_fail "$G4_NAME" "$MANIFESTBASE has no components block"
        MANIFEST=""
    elif [ -z "$SIG_COMMIT" ]; then
        gate_fail "$G4_NAME" "cannot read the release list -- no sigmond commit resolved (see gate 1)"
    elif ! git -C "$SIGMOND_REPO" show "$SIG_COMMIT:release/components.toml" > "$LIST" 2>/dev/null; then
        gate_fail "$G4_NAME" "no release/components.toml at sigmond ${SIG_COMMIT:0:12} -- write it with 'smd admin release-list --write --version $VERSION', review, commit, push, and build from that commit"
    else
        _lv="$(_py sigmond.release_list version --list "$LIST" 2>&1)"; _lrc=$?
        _lc="$(_py sigmond.release_list check --list "$LIST" --manifest "$MANIFEST" 2>&1)"; _crc=$?
        _lc="$(printf '%s' "$_lc" | tr '\n' ';')"
        if [ $_lrc -ne 0 ]; then
            gate_fail "$G4_NAME" "release/components.toml at sigmond ${SIG_COMMIT:0:12} does not parse: $_lv"
        elif [ "$_lv" != "$VERSION" ]; then
            gate_fail "$G4_NAME" "release/components.toml at sigmond ${SIG_COMMIT:0:12} is for $_lv, not $VERSION"
        elif [ $_crc -ne 0 ]; then
            gate_fail "$G4_NAME" "$MANIFESTBASE differs from release/components.toml at sigmond ${SIG_COMMIT:0:12}: $_lc"
        else
            NLINES=$(grep -c '^    [A-Za-z]' "$MANIFEST")
            gate_pass "$G4_NAME" "$MANIFESTBASE ($NLINES component lines) matches release/components.toml ($VERSION) at sigmond ${SIG_COMMIT:0:12}"
        fi
    fi
else
    gate_fail "$G4_NAME" "cannot check -- no image resolved (see gate 3)"
fi

# ---- Gate 5: test evidence records a PASS for THIS exact image ------------
# test-nested-v3.sh appends to $TESTLOG across many builds: each invocation
# starts with "USB image under test: <filename>" and (on a full run) ends
# with "PHASE D PASS -- NESTED TEST COMPLETE". The log is append-only and
# currently holds many runs of many images, some of which failed and were
# re-run. A bare `grep PASS` over the whole log would happily match a PASS
# that belongs to a completely different image.
#
# Approach: find the LAST line naming this exact bare filename (an exact
# string match against the text after "USB image under test: ", not a
# substring match -- this also means a run logged under an accidentally
# path-prefixed name, e.g. "/srv/build/v3/sigmond-appliance-...img", does
# NOT count as evidence for the bare-named image gate 3 resolved). Bound
# that run from its marker line to the next "USB image under test:" line
# (or EOF), and require the PHASE D marker inside that bounded block.
# Using the LAST such block (not "any" block) means a stale PASS from an
# earlier attempt of the same image does not paper over a later regression.
#
# This is airtight against the "different image" trap the task called out,
# and against stale/earlier passes of the same image. It is NOT airtight
# against: (a) a hand-edited log, (b) two blocks for the same image whose
# timestamps collide across a log that was concatenated out of order, or
# (c) a marker line with extra trailing text after the filename (treated as
# a non-match -- a false negative, never a false positive). See the report
# for the full discussion.
if [ -n "$IMG" ]; then
    if [ ! -f "$TESTLOG" ]; then
        gate_fail "5 (test evidence: PHASE D PASS)" "no test log at $TESTLOG"
    else
        # `|| [ -n "$line" ]` is needed on both loops below: plain `while
        # read -r line; do ... done` silently DROPS a final line that has
        # no trailing newline (read returns nonzero at EOF even though it
        # already populated $line, and a bare while-read treats that
        # nonzero as loop-exit before the body runs). A killed or
        # partially-flushed test-nested-v3.sh run is exactly the case
        # where the log's last line could be missing its newline -- and
        # that last line could be the PHASE D PASS marker itself. Same
        # reasoning applies to TOTAL below: `wc -l` counts newlines, not
        # lines, so it undercounts by one for the same reason; `awk
        # 'END{print NR}'` counts records instead and doesn't.
        LASTLINE=0
        LN=0
        while IFS= read -r line || [ -n "$line" ]; do
            LN=$((LN+1))
            case "$line" in
                *"USB image under test: "*)
                    marker="${line#*USB image under test: }"
                    [ "$marker" = "$IMGBASE" ] && LASTLINE=$LN
                    ;;
            esac
        done < "$TESTLOG"
        if [ "$LASTLINE" -eq 0 ]; then
            gate_fail "5 (test evidence: PHASE D PASS)" "no test run found for $IMGBASE in $TESTLOG"
        else
            TOTAL=$(awk 'END{print NR}' "$TESTLOG")
            NEXTLINE=$TOTAL
            LN=0
            while IFS= read -r line || [ -n "$line" ]; do
                LN=$((LN+1))
                if [ "$LN" -gt "$LASTLINE" ]; then
                    case "$line" in
                        *"USB image under test: "*) NEXTLINE=$((LN-1)); break ;;
                    esac
                fi
            done < "$TESTLOG"
            BLOCK="$(sed -n "${LASTLINE},${NEXTLINE}p" "$TESTLOG")"
            if printf '%s\n' "$BLOCK" | grep -qF 'PHASE D PASS'; then
                gate_pass "5 (test evidence: PHASE D PASS)" "$IMGBASE: log lines $LASTLINE-$NEXTLINE"
            else
                gate_fail "5 (test evidence: PHASE D PASS)" \
                    "$IMGBASE: latest test run (log lines $LASTLINE-$NEXTLINE) did not reach PHASE D PASS"
            fi
        fi
    fi
else
    gate_fail "5 (test evidence: PHASE D PASS)" "cannot check -- no image resolved (see gate 3)"
fi

# ---- Gate 5b: the IMAGE actually contains appliance_commit's firstboot -----
#
# ⛔ Every other gate reads the REPO -- the tag, the clean tree, the manifest.
# None of them had ever looked inside the artefact.  v3.44 passed all seven
# while shipping a firstboot-v3.sh three days older than its own tag, because
# build-usb-v3.sh read that file from the rig staging dir instead of the
# checkout.  Both appliance commits in the tag (c0b10f2 pristine snapshot,
# 9038623 panel fix) were simply absent from the image, and "ALL GATES PASS"
# said nothing about it.
#
# So: take what the image repo's firstboot-v3.sh holds at appliance_commit
# (the commit --apply tags), and require it in the bytes we are about to
# publish.  Before the release home this read the image repo's tag, which no
# longer exists before the bless.
G5B_NAME="5b (image carries appliance_commit's firstboot)"
if [ -n "${IMG:-}" ] && [ -f "$IMG" ]; then
    # EXACT check, not a sample.  The first version of this gate sampled
    # function definitions and heredoc starts -- lines that do not change
    # between releases -- so it passed on the very image that motivated it.
    # A gate that cannot fail is worse than no gate: it reads as assurance.
    #
    # The build records the sha256 of the rendered firstboot in the
    # manifest.  Recompute it from appliance_commit and compare.
    MANI="${IMG%.img}.manifest.txt"
    WANT_FB="$(awk -F': *' '/^firstboot_sha256:/{print $2; exit}' "$MANI" 2>/dev/null)"
    if [ -z "$WANT_FB" ]; then
        # Built before this field existed.  Say so plainly rather than
        # inventing confidence: an old artefact cannot be verified this way.
        gate_warn "$G5B_NAME" \
            "$(basename "$MANI") predates firstboot_sha256 -- provenance NOT verifiable for this build"
    elif [ -z "$PX_COMMIT" ]; then
        gate_fail "$G5B_NAME" "cannot check -- no appliance_commit resolved (see gate 1)"
    else
        TAGFB="$INSTR_DIR/firstboot.at-commit"; RENDERED="$INSTR_DIR/firstboot.rendered"
        if git -C "$PX_REPO" show "$PX_COMMIT:firstboot-v3.sh" > "$TAGFB" 2>/dev/null; then
            sed -e "s|@@VERSION@@|${VERSION}|g" "$TAGFB" > "$RENDERED"
            GOT_FB="$(sha256sum "$RENDERED" | cut -d' ' -f1)"
            if [ "$GOT_FB" = "$WANT_FB" ]; then
                gate_pass "$G5B_NAME" \
                    "firstboot-v3.sh matches ${PX_COMMIT:0:12} (${GOT_FB:0:16}…)"
            else
                gate_fail "$G5B_NAME" \
                    "the image was built from a DIFFERENT firstboot than ${PX_COMMIT:0:12}: image ${WANT_FB:0:16}… vs commit ${GOT_FB:0:16}…"
            fi
        else
            gate_fail "$G5B_NAME" \
                "could not read ${PX_COMMIT:0:12}:firstboot-v3.sh from $PX_REPO"
        fi
    fi
else
    gate_fail "$G5B_NAME" "cannot check -- no image resolved (see gate 3)"
fi

# ---- Gate 6: no Release already exists for this version ----------------------
#
# HamSCI/sigmond holds the Release.  With --mirror the image repo holds a copy,
# for the v3.93 and older smd and pm-align that read Releases there alone.
#
# The mirror must work on both sides of the image repo's rename
# (sigmond-appliance -> sigmond-proxmox).  GitHub redirects the old name to the
# new, never the new to the old, and `gh release view` says "release not found"
# for a repo that does not exist as for a missing release.  So the mirror is
# found first with `gh repo view`, which answers with the repo's present name:
# GH_MIRROR_SLUG when set; else the GitHub repo PROXMOX_REPO's origin names,
# then HamSCI/sigmond-proxmox, then HamSCI/sigmond-appliance, the first GitHub
# knows.  Only "not found" moves to the next; any other error refuses.
G6_NAME="6 (no existing Release)"
G6_BAD=(); G6_OK=()
_g6_release(){ # _g6_release <slug>: one Release check, into G6_BAD / G6_OK
    local out
    out="$(gh release view "$VERSION" --repo "$1" 2>&1)"
    if [ $? -eq 0 ]; then
        G6_BAD+=("a Release already exists for $VERSION on $1")
    elif printf '%s' "$out" | grep -qi 'release not found'; then
        G6_OK+=("no Release for $VERSION on $1")
    else
        G6_BAD+=("could not verify $1 -- gh release view failed: $(printf '%s' "$out" | tr '\n' ' ')")
    fi
}
MIRROR_SLUG=""
_mirror_candidates(){
    if [ -n "${GH_MIRROR_SLUG:-}" ]; then echo "$GH_MIRROR_SLUG"; return; fi
    local url
    url="$(git -C "$PX_REPO" config --get remote.origin.url 2>/dev/null)"
    if [[ "$url" =~ github\.com[:/]([^/]+)/([^/]+)$ ]]; then
        echo "${BASH_REMATCH[1]}/${BASH_REMATCH[2]%.git}"
    fi
    echo HamSCI/sigmond-proxmox
    echo HamSCI/sigmond-appliance
}
_g6_release "$GH_REPO"
if [ "$MIRROR" = mirror ]; then
    _tried=()
    while read -r _c; do
        [ -n "$_c" ] || continue
        case " ${_tried[*]-} " in *" $_c "*) continue ;; esac
        _tried+=("$_c")
        _out="$(gh repo view "$_c" --json nameWithOwner -q .nameWithOwner 2>&1)"
        if [ $? -eq 0 ] && [ -n "$_out" ]; then
            MIRROR_SLUG="$(printf '%s' "$_out" | tail -1)"; break
        fi
        if ! printf '%s' "$_out" | grep -qiE 'could not resolve to a repository|HTTP 404|not found'; then
            G6_BAD+=("could not look up the mirror $_c -- gh repo view failed: $(printf '%s' "$_out" | tr '\n' ' ')")
            _tried=(); break
        fi
    done < <(_mirror_candidates)
    if [ -n "$MIRROR_SLUG" ]; then
        _g6_release "$MIRROR_SLUG"
    elif [ ${#_tried[@]} -gt 0 ]; then
        G6_BAD+=("no mirror repo: GitHub knows none of ${_tried[*]}")
    fi
fi
if [ ${#G6_BAD[@]} -eq 0 ]; then
    _j=""; for _b in "${G6_OK[@]}"; do _j="${_j:+$_j; }$_b"; done
    gate_pass "$G6_NAME" "$_j"
else
    _j=""; for _b in "${G6_BAD[@]}"; do _j="${_j:+$_j; }$_b"; done
    gate_fail "$G6_NAME" "$_j"
fi
case "$MIRROR" in
    mirror)    gate_pass "6b (mirror chosen)" "--mirror: the Release also goes to ${MIRROR_SLUG:-the image repo}" ;;
    no-mirror) gate_pass "6b (mirror chosen)" "--no-mirror: HamSCI/sigmond alone" ;;
    *)         gate_fail "6b (mirror chosen)" \
                   "neither --mirror nor --no-mirror given -- choose --mirror while any station or host runs a pre-v3.94 smd or pm-align (release/RELEASE.md)" ;;
esac

# ---- Gate 7: the install instructions name THIS release ---------------------
#
# ⛔ Michael's standing policy since 2026-10-08: every build gets updated
# install instructions, and the bless carries them.  The rule had stood for one
# day as a paragraph in docs/RELEASE.md, and nothing enforced it.  Every other
# gate reads the image or the tag.  None read the words an operator follows to
# install the image, which lived in INSTALL.md and on a hand-published page.
#
# Two install paths, two documents.  The image path (INSTALL.md and the
# install page) lives in the image repo; the bare-metal path
# (docs/install-bare-metal.md) lives in sigmond.  Both must name this release.
#
# WHERE the gate reads.  The documents get their "Verified against" lines
# AFTER the hardware tests, in commits later than the commits the image built
# from.  So at bless time this release's instructions live at origin/main of
# each repo: not at the build's commits (which hold the previous release's
# words) and not in the working trees (which may differ from what the team
# pushed).  Gate 1 fetches origin in both repos first and warns when a fetch
# fails.  This gate reads through `git show origin/main:<path>`.
#
# The bytes it reads go into the temp directory under the names --apply
# publishes, so the files published are the files checked here -- no second
# read, nothing a late fetch could change between the check and the upload.
#
# PASS needs all five:
#   - INSTALL.md has a **Status:** line that reads exactly "current"
#   - INSTALL.md has a **Verified against:** line that holds
#     "sigmond <VERSION>" as a whole token: v3.7 does not satisfy v3.70,
#     v3.70 does not satisfy v3.701, v3.70.1 or v3.70-rc1
#   - docs/install-page.html holds this image's exact file name
#   - sigmond's docs/install-bare-metal.md has a **Verified against:** line
#     that holds "sigmond <VERSION>" the same way
#   - and its install commands check out <VERSION> (`git checkout <VERSION>`),
#     read by sigmond.bare_metal_doc, the reader the bare-metal test uses
INSTR_STEM=""
if [ -n "${IMGBASE:-}" ]; then INSTR_STEM="${IMGBASE%.img}"; fi
INSTR_MD="$INSTR_DIR/${INSTR_STEM:-unresolved}.INSTALL.md"
INSTR_HTML="$INSTR_DIR/${INSTR_STEM:-unresolved}.INSTALL.html"
BM_DOC="$INSTR_DIR/install-bare-metal.origin-main.md"

# _md_field <file> <label> — the text after the first "**<label>:**" line, with
# edge blanks stripped.  [:space:] covers the CR of a CRLF file.  Quote marks
# ("> ") before the label do not matter.  Prints nothing and fails when the
# line is absent.
_md_field(){
    local line
    line="$(grep -m1 -E "^[[:space:]>]*\*\*$2:\*\*" "$1")"
    [ -n "$line" ] || return 1
    line="${line#*"**$2:**"}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    printf '%s\n' "$line"
}

# _tok_in <text> <needle> — succeeds when needle occurs in text as a whole
# token.  Fixed-string, so the dots in a version stay dots.  The character
# before the needle must not belong to a word; the character after must not
# extend it (a letter, digit, "_" or "-", or a "." that a digit or letter
# follows).  A comma, a bracket, a space, or a full stop that ends the line
# all count as the end of the token.
_tok_in(){
    local text="$1" needle="$2" i=0 n=${#2} len=${#1} before after after2
    while [ $((i+n)) -le "$len" ]; do
        if [ "${text:i:n}" = "$needle" ]; then
            before=""; [ "$i" -gt 0 ] && before="${text:i-1:1}"
            after="${text:i+n:1}"; after2="${text:i+n+1:1}"
            case "$before" in [A-Za-z0-9_-]) i=$((i+1)); continue ;; esac
            case "$after" in [A-Za-z0-9_-]) i=$((i+1)); continue ;; esac
            if [ "$after" = "." ]; then
                case "$after2" in [A-Za-z0-9]) i=$((i+1)); continue ;; esac
            fi
            return 0
        fi
        i=$((i+1))
    done
    return 1
}

G7_NAME="7 (install instructions name this release)"
G7_BAD=()
_have_inst=0; _have_page=0
git -C "$PX_REPO" show "origin/main:INSTALL.md" > "$INSTR_MD" 2>/dev/null && _have_inst=1
git -C "$PX_REPO" show "origin/main:docs/install-page.html" > "$INSTR_HTML" 2>/dev/null && _have_page=1
# The flasher (Michael, 2026-11-03).  sigmond-flash-latest.sh sits in each download
# folder under ONE fixed name, so the link test sites hold never changes, and the
# bless re-uploads it every time so that copy never goes stale.  Not a gate: a
# release ships without it, with a NOTE.
FLASHER=""
FLASHER_NOTE=""
#
# The flasher runs as root on the machines of the people who download it, so it
# comes from the image commit this release blesses (appliance_commit), not from
# whatever origin/main holds at bless time: a commit pushed to main after the
# tests would otherwise reach every test site unreviewed.  The dry run and the
# confirmation print its sha256 and that commit.
FLASHER_SHA=""
FLASHER_AT="${PX_COMMIT:0:12}"
if [ -z "$PX_COMMIT" ]; then
    FLASHER_NOTE="NOTE: no appliance_commit resolved (see gate 1) -- the flasher in the download folders was not refreshed"
elif git -C "$PX_REPO" show "$PX_COMMIT:sigmond-flash-latest.sh" > "$INSTR_DIR/sigmond-flash-latest.sh" 2>/dev/null; then
    if bash -n "$INSTR_DIR/sigmond-flash-latest.sh" 2>/dev/null; then
        FLASHER="$INSTR_DIR/sigmond-flash-latest.sh"
        FLASHER_SHA="$(sha256sum "$FLASHER" | cut -d' ' -f1)"
        _fv="$(sed -n 's/^FLASH_VERSION=\([0-9.]*\).*/\1/p' "$FLASHER" | head -1)"
    else
        FLASHER_NOTE="WARNING: sigmond-flash-latest.sh at image commit $FLASHER_AT fails bash -n -- not publishing it; each folder keeps its previous copy"
    fi
else
    FLASHER_NOTE="NOTE: no sigmond-flash-latest.sh at image commit $FLASHER_AT -- the flasher in the download folders was not refreshed"
fi
# flasher_vs_published <dest> — why the flasher must NOT go to <dest>, or
# nothing when it may.  Copies people already hold replace themselves only
# when the published FLASH_VERSION is higher than theirs, so a changed flasher
# published under the same (or a lower) number would never reach them, and
# the folder would hold two different scripts claiming one version.  The same
# bytes (a re-run) or no copy there yet are fine.
_flash_ver() { sed -n 's/^FLASH_VERSION=\([0-9.]*\).*/\1/p' "$1" | head -1; }
flasher_vs_published() {
    local old new_v old_v
    old="$(mktemp)" || return 0
    if ! pub_cat "$1" sigmond-flash-latest.sh > "$old" 2>/dev/null || [ ! -s "$old" ] \
       || cmp -s "$old" "$FLASHER"; then
        rm -f "$old"; return 0
    fi
    new_v="$(_flash_ver "$FLASHER")"; old_v="$(_flash_ver "$old")"; rm -f "$old"
    if [ -n "$new_v" ] && [ "$new_v" != "$old_v" ] \
       && [ "$(printf '%s\n%s\n' "$old_v" "$new_v" | sort -V | tail -1)" = "$new_v" ]; then
        return 0
    fi
    echo "sigmond-flash-latest.sh at image commit $FLASHER_AT differs from the copy already published but its FLASH_VERSION (${new_v:-none}) is not above that copy's (${old_v:-none}) -- not publishing it here, since copies people hold would never update to it; bump FLASH_VERSION, build a new candidate, and bless that"
}

if [ $_have_inst -eq 0 ]; then
    G7_BAD+=("INSTALL.md is not readable at origin/main")
else
    if ! _st="$(_md_field "$INSTR_MD" "Status")"; then
        G7_BAD+=("INSTALL.md has no **Status:** line")
    elif [ "$_st" != "current" ]; then
        G7_BAD+=("INSTALL.md Status is not 'current' (it reads '$_st')")
    fi
    if ! _va="$(_md_field "$INSTR_MD" "Verified against")"; then
        G7_BAD+=("INSTALL.md has no **Verified against:** line")
    elif ! _tok_in "$_va" "sigmond $VERSION"; then
        G7_BAD+=("INSTALL.md Verified against does not name 'sigmond $VERSION'")
    fi
fi
if [ $_have_page -eq 0 ]; then
    G7_BAD+=("docs/install-page.html is not readable at origin/main")
elif [ -z "${IMGBASE:-}" ]; then
    G7_BAD+=("no image to check the page against (see gate 3)")
elif ! grep -qF -- "$IMGBASE" "$INSTR_HTML"; then
    G7_BAD+=("docs/install-page.html does not name '$IMGBASE'")
fi
if ! git -C "$SIGMOND_REPO" show "origin/main:docs/install-bare-metal.md" > "$BM_DOC" 2>/dev/null; then
    G7_BAD+=("docs/install-bare-metal.md is not readable at sigmond origin/main")
else
    if ! _va="$(_md_field "$BM_DOC" "Verified against")"; then
        G7_BAD+=("docs/install-bare-metal.md has no **Verified against:** line")
    elif ! _tok_in "$_va" "sigmond $VERSION"; then
        G7_BAD+=("docs/install-bare-metal.md Verified against does not name 'sigmond $VERSION'")
    fi
    _co="$(_py sigmond.bare_metal_doc checkout-version --doc "$BM_DOC" 2>&1)"
    if [ $? -ne 0 ]; then
        G7_BAD+=("docs/install-bare-metal.md does not read as the bare-metal test reads it: $(printf '%s' "$_co" | tr '\n' ' ')")
    elif [ "$_co" != "$VERSION" ]; then
        G7_BAD+=("docs/install-bare-metal.md checks out '${_co:-nothing}', not $VERSION")
    fi
fi

if [ ${#G7_BAD[@]} -eq 0 ]; then
    gate_pass "$G7_NAME" \
        "INSTALL.md is current and verified against sigmond $VERSION; docs/install-page.html names $IMGBASE (image repo origin/main $(git -C "$PX_REPO" rev-parse --short origin/main 2>/dev/null)); docs/install-bare-metal.md verified against and checks out $VERSION (sigmond origin/main $(git -C "$SIGMOND_REPO" rev-parse --short origin/main 2>/dev/null))"
else
    _g7_join=""
    for _b in "${G7_BAD[@]}"; do _g7_join="${_g7_join:+$_g7_join; }$_b"; done
    gate_fail "$G7_NAME" \
        "$_g7_join -- update INSTALL.md and docs/install-page.html in the image repo, and docs/install-bare-metal.md in sigmond, for this release, commit and push them, then bless (release/RELEASE.md)"
fi

# ---- Gate 8: the changelog names THIS release --------------------------------
#
# ⛔ Michael, 2026-11-18: every release carries a changelog, and the bless
# refuses without one.  Until then the only account of a release was the
# generated `git log --oneline` in its notes: commit subjects, written for the
# next developer, not for the operator deciding whether to install.
#
# CHANGELOG.md at sigmond's root holds one section per version, newest first,
# each under a heading of the form
#     ## sigmond v3.94 — blessed 2026-11-25
# The words after the version are free.  A heading that holds "(draft)" marks
# a section still being written.  Sections from before the release home read
# "## sigmond-appliance vN"; they stay as history, and no gate reads them.
#
# WHERE the gate reads: sigmond's origin/main, through `git show`, for gate
# 7's reason.  The section gets written after the hardware tests, in a commit
# later than the one the image installed, so that commit's tree lacks it and
# the working tree may differ from what the team pushed.
#
# PASS needs all four:
#   - CHANGELOG.md is readable at origin/main
#   - exactly one "## sigmond <VERSION>" heading, the version a whole
#     token: the heading continues with a space or ends there, so v3.7 does not
#     match v3.70, and v3.70 does not match v3.701 or v3.70.1
#   - that heading does not hold "(draft)", in any letter case
#   - the section (up to the next "## " line) holds at least one non-blank line
#
# The section's bytes go into the temp directory under the names --apply uses:
# the body for the release notes, and <stem>.CHANGELOG.md for the publish
# targets.  So what gets published is what this gate checked, as in gate 7.
G8_NAME="8 (changelog names this release)"
CL_URL="https://github.com/$GH_REPO/blob/main/CHANGELOG.md"
CL_FULL="$INSTR_DIR/CHANGELOG.origin-main.md"
CL_BODY="$INSTR_DIR/changelog-section.body"
CL_PUB="$INSTR_DIR/${INSTR_STEM:-unresolved}.CHANGELOG.md"
G8_BAD=""
G8_DO=""
if ! git -C "$SIGMOND_REPO" show "origin/main:CHANGELOG.md" > "$CL_FULL" 2>/dev/null; then
    G8_BAD="CHANGELOG.md is not readable at origin/main"
    G8_DO="create CHANGELOG.md with a '## sigmond $VERSION' section"
else
    _cl_n=0; _cl_head=""; _cl_in=0; _cl_body=()
    while IFS= read -r _l || [ -n "$_l" ]; do
        _lc="${_l%$'\r'}"
        case "$_lc" in
            "## "*)
                _cl_in=0
                _rest="${_lc#"## sigmond "}"
                if [ "$_rest" != "$_lc" ]; then
                    case "$_rest" in
                        "$VERSION"|"$VERSION "*)
                            _cl_n=$((_cl_n+1))
                            if [ "$_cl_n" -eq 1 ]; then _cl_head="$_l"; _cl_in=1; fi
                            ;;
                    esac
                fi
                continue
                ;;
        esac
        [ "$_cl_in" -eq 1 ] && _cl_body+=("$_l")
    done < "$CL_FULL"
    # Trim blank lines from both ends of the body; keep the bytes in between.
    _first=-1; _last=-1
    for _i in "${!_cl_body[@]}"; do
        case "${_cl_body[$_i]}" in *[![:space:]]*)
            [ "$_first" -lt 0 ] && _first=$_i
            _last=$_i ;;
        esac
    done
    if [ "$_cl_n" -eq 0 ]; then
        G8_BAD="CHANGELOG.md has no '## sigmond $VERSION' heading"
        G8_DO="write the $VERSION section at the top of CHANGELOG.md"
    elif [ "$_cl_n" -gt 1 ]; then
        G8_BAD="CHANGELOG.md has $_cl_n headings for sigmond $VERSION"
        G8_DO="merge them into one section"
    elif [[ "${_cl_head,,}" == *"(draft)"* ]]; then
        G8_BAD="the $VERSION heading is marked draft ('${_cl_head%$'\r'}')"
        G8_DO="finish the section and drop '(draft)' from its heading"
    elif [ "$_first" -lt 0 ]; then
        G8_BAD="the $VERSION section has no text under its heading"
        G8_DO="write what changed in $VERSION under its heading"
    else
        printf '%s\n' "${_cl_body[@]:_first:_last-_first+1}" > "$CL_BODY"
        {   printf '%s\n' "$_cl_head"
            printf '%s\n' "${_cl_body[@]:0:_last+1}"
            printf '\nFull changelog: %s\n' "$CL_URL"
        } > "$CL_PUB"
    fi
fi
if [ -z "$G8_BAD" ]; then
    gate_pass "$G8_NAME" \
        "CHANGELOG.md has a $VERSION section ($((_last-_first+1)) line(s)) (sigmond origin/main $(git -C "$SIGMOND_REPO" rev-parse --short origin/main 2>/dev/null))"
else
    gate_fail "$G8_NAME" \
        "$G8_BAD -- $G8_DO, commit and push CHANGELOG.md in sigmond, then bless (release/RELEASE.md)"
fi

# ---- Gate 9: the image's native sources come from the release's pins ------
#
# Michael, 2026-11-20: "Any pinning or version specifications should reside
# in the base repo and not vary per station."  The image, a bare-metal
# install and `smd align` must all land one set of pins, and the base repo
# holds it: bin/smd's _ONION_COMMIT, _WSJTX_COMMIT and _UBERSDR_COMMIT at
# the sigmond commit the image carries.  Until v3.94 an image built under
# SIGMOND_TRACK_MAIN=1 at newest main and shipped those commits in
# /etc/sigmond/component-pins.toml, a per-station file that won over the
# constants.  sigmond no longer reads that file, so an image whose natives
# sit anywhere else would ship binaries that the first `smd align` rebuilds.
#
# So a candidate built at newest main gets blessed only once its commits
# live in sigmond's constants (and the image carries that sigmond).
#
# WHAT the gate reads: the manifest (gate 4).  Its "sigmond" row names the
# sigmond commit; `git show <it>:bin/smd` in $SIGMOND_REPO gives the
# constants.  Each must match:
#   - the onion, wsjtx and ubersdr_wsprd checkout rows (all three present);
#   - native_built_<name>: preamble lines, the commits the image's builds
#     recorded in /var/lib/sigmond/build-manifest/*.toml;
#   - native_provenance_<wsjtx|ubersdr_wsprd>: the commit each decoder's
#     .provenance file names.
# build-golden-vm.sh captures those lines and writes native_built_capture:
# ok when it found all five, failed otherwise; build-usb-v3.sh writes failed
# when the template carries no capture at all.  failed FAILs.  ok demands
# all five lines.  No marker means a manifest from before v3.94: the gate
# checks the checkouts and any lines present, FAILs on one or two of the
# three native_built_ lines, and WARNs when there are none.
# ka9q-radio is not checked here: its pin belongs to ka9q-python, and
# build-golden-vm.sh already refuses a template whose ka9q-radio sits off
# ka9q-python's compat commit.
G9_NAME="9 (native pins come from the release)"
G9_BAD=()
_g9_same(){ # _g9_same <full> <short-or-full>: one is a prefix of the other
    [ -n "$1" ] && [ -n "$2" ] && { case "$1" in "$2"*) return 0 ;; esac
                                    case "$2" in "$1"*) return 0 ;; esac; }
    return 1
}
if [ -z "${MANIFEST:-}" ]; then
    gate_fail "$G9_NAME" "cannot check -- no manifest (see gate 4)"
else
    _g9_sig="$(_g9_row "$MANIFEST" sigmond)"
    if [ -z "$_g9_sig" ]; then
        gate_fail "$G9_NAME" "$(basename "$MANIFEST") has no sigmond row -- cannot tell which pins the image carries"
    elif [ ! -d "$SIGMOND_REPO" ]; then
        gate_fail "$G9_NAME" "no sigmond checkout at $SIGMOND_REPO (set SIGMOND_REPO)"
    else
        if ! git -C "$SIGMOND_REPO" cat-file -e "${_g9_sig}^{commit}" 2>/dev/null; then
            git -C "$SIGMOND_REPO" fetch --quiet origin 2>/dev/null \
                || say "WARNING: gate 9 could not fetch origin in $SIGMOND_REPO"
        fi
        _g9_smd="$(git -C "$SIGMOND_REPO" show "${SIG_COMMIT:-$_g9_sig}:bin/smd" 2>/dev/null)"
        if [ -z "$_g9_smd" ]; then
            gate_fail "$G9_NAME" "cannot read bin/smd at sigmond $_g9_sig in $SIGMOND_REPO"
        else
            _g9_mark="$(_g9_pre "$MANIFEST" native_built_capture)"
            case "$_g9_mark" in
                ""|ok) ;;
                failed) G9_BAD+=("the build-record capture failed (native_built_capture: failed) -- the built binaries cannot be checked") ;;
                *) G9_BAD+=("unknown native_built_capture value '$_g9_mark'") ;;
            esac
            _g9_nbuilt=0; _g9_nprov=0
            for _pair in onion:_ONION_COMMIT wsjtx:_WSJTX_COMMIT ubersdr_wsprd:_UBERSDR_COMMIT; do
                _n="${_pair%%:*}"; _c="${_pair#*:}"
                _want="$(printf '%s\n' "$_g9_smd" \
                    | sed -nE "s/^${_c} *= *['\"]([0-9a-f]{40})['\"].*/\1/p" | head -1)"
                if [ -z "$_want" ]; then
                    G9_BAD+=("sigmond $_g9_sig names no $_c"); continue
                fi
                _got="$(_g9_row "$MANIFEST" "$_n")"
                if [ -z "$_got" ]; then
                    G9_BAD+=("no $_n checkout row")
                elif ! _g9_same "$_want" "$_got"; then
                    G9_BAD+=("$_n checkout ${_got:0:9}, pin ${_want:0:9}")
                fi
                _built="$(_g9_pre "$MANIFEST" "native_built_$_n")"
                if [ -n "$_built" ]; then
                    _g9_nbuilt=$((_g9_nbuilt+1))
                    _g9_same "$_want" "$_built" \
                        || G9_BAD+=("$_n built from ${_built:0:9}, pin ${_want:0:9}")
                elif [ "$_g9_mark" = ok ]; then
                    G9_BAD+=("no native_built_$_n line")
                fi
                # onion is a library: no decoder .provenance of its own.
                [ "$_n" = onion ] && continue
                _prov="$(_g9_pre "$MANIFEST" "native_provenance_$_n")"
                if [ -n "$_prov" ]; then
                    _g9_nprov=$((_g9_nprov+1))
                    _g9_same "$_want" "$_prov" \
                        || G9_BAD+=("$_n decoder provenance ${_prov:0:9}, pin ${_want:0:9}")
                elif [ "$_g9_mark" = ok ]; then
                    G9_BAD+=("no native_provenance_$_n line")
                fi
            done
            if [ "$_g9_mark" != ok ] && [ "$_g9_nbuilt" -gt 0 ] && [ "$_g9_nbuilt" -lt 3 ]; then
                G9_BAD+=("only $_g9_nbuilt of 3 native_built_ lines")
            fi
            if [ "${#G9_BAD[@]}" -gt 0 ]; then
                _g9_join=""
                for _b in "${G9_BAD[@]}"; do _g9_join="${_g9_join:+$_g9_join; }$_b"; done
                gate_fail "$G9_NAME" \
                    "$_g9_join -- write the commits into bin/smd's constants in sigmond, rebuild the image from that sigmond, then bless"
            elif [ -z "$_g9_mark" ] && [ "$_g9_nbuilt" -eq 0 ]; then
                gate_warn "$G9_NAME" \
                    "checkouts match sigmond $_g9_sig's pins, but $(basename "$MANIFEST") predates native_built_ lines -- the built binaries are NOT verified"
            else
                gate_pass "$G9_NAME" \
                    "onion, wsjtx, ubersdr_wsprd checkouts, $_g9_nbuilt build record(s) and $_g9_nprov decoder provenance(s) match sigmond $_g9_sig's pins"
            fi
        fi
    fi
fi

# ---- Gate 10: the bare-metal install test passed at this sigmond commit ----
#
# The image test proves the Proxmox path.  test-bare-metal.sh (image repo)
# proves the other: it boots a fresh Debian VM on the rig, runs the commands
# of docs/install-bare-metal.md at the sigmond commit under test, then smd
# check, and writes one record per run to $RIG_DIR/bare-metal/:
#     <version>-<YYYYmmddTHHMMSSZ>.record, "key: value" lines
# (bare_metal_record, version, sigmond_commit, doc_commands_sha256,
# release_list_check, smd_check_exit, radiod_consumers, result, finished_utc).
#
# PASS needs the NEWEST record for this version AND this sigmond commit (by
# the stamp in its name) to read result: PASS.  A later FAIL for the same
# commit outranks an earlier PASS, as the newest test log block does at gate
# 5.  A record for another commit counts for nothing here.
#
# And the doc must not have changed since: the record's doc_commands_sha256
# must equal the digest sigmond.bare_metal_doc takes today of origin/main's
# docs/install-bare-metal.md, the document the bless publishes (gate 7).  A
# doc edited after the test (a changed command, a new step) asks for a new
# test run; a prose edit leaves the digest alone.
G10_NAME="10 (bare-metal install test PASS)"
_rec(){ awk -F': *' -v k="$2" '$1==k {sub(/^[^:]*: */,""); print; exit}' "$1"; }
_g10_rec=""
if [ -z "$SIG_COMMIT" ]; then
    gate_fail "$G10_NAME" "cannot check -- no sigmond commit resolved (see gate 1)"
elif [ ! -d "$BM_DIR" ]; then
    gate_fail "$G10_NAME" "no bare-metal records: $BM_DIR does not exist -- run test-bare-metal.sh $VERSION on the rig"
else
    while IFS= read -r _f; do
        [ "$(_rec "$_f" bare_metal_record)" = 1 ] || continue
        [ "$(_rec "$_f" version)" = "$VERSION" ] || continue
        [ "$(_rec "$_f" sigmond_commit)" = "$SIG_COMMIT" ] || continue
        _g10_rec="$_f"
    done < <(find "$BM_DIR" -maxdepth 1 -type f -name "${VERSION}-*.record" 2>/dev/null | LC_ALL=C sort)
    if [ -z "$_g10_rec" ]; then
        gate_fail "$G10_NAME" "no bare-metal record for $VERSION at sigmond ${SIG_COMMIT:0:12} in $BM_DIR -- run test-bare-metal.sh $VERSION on the rig"
    else
        _g10_b="$(basename "$_g10_rec")"
        _g10_res="$(_rec "$_g10_rec" result)"
        _g10_dg="$(_rec "$_g10_rec" doc_commands_sha256)"
        _g10_now=""
        if [ -s "${BM_DOC:-}" ]; then
            _g10_now="$(_py sigmond.bare_metal_doc digest --doc "$BM_DOC" 2>/dev/null)"
        fi
        if [ "$_g10_res" != PASS ]; then
            gate_fail "$G10_NAME" "the newest record for sigmond ${SIG_COMMIT:0:12}, $_g10_b, reads result: ${_g10_res:-nothing} (release_list_check: $(_rec "$_g10_rec" release_list_check), smd_check_exit: $(_rec "$_g10_rec" smd_check_exit), radiod_consumers: $(_rec "$_g10_rec" radiod_consumers))"
        elif [ -z "$_g10_now" ]; then
            gate_fail "$G10_NAME" "cannot take the digest of docs/install-bare-metal.md at sigmond origin/main (see gate 7)"
        elif [ "$_g10_dg" != "$_g10_now" ]; then
            gate_fail "$G10_NAME" "$_g10_b ran other commands than docs/install-bare-metal.md at sigmond origin/main holds now (record ${_g10_dg:0:12}, doc ${_g10_now:0:12}) -- run test-bare-metal.sh $VERSION again"
        else
            gate_pass "$G10_NAME" "$_g10_b: result PASS at sigmond ${SIG_COMMIT:0:12}, doc commands unchanged (${_g10_now:0:12})"
        fi
    fi
fi

# ---- report -----------------------------------------------------------------
say "=== bless-release.sh $VERSION ($([ $APPLY -eq 1 ] && echo apply || echo dry-run)) ==="
i=0
while [ $i -lt ${#GATE_NAMES[@]} ]; do
    printf '  [gate %-44s %-4s %s\n' "${GATE_NAMES[$i]}]" "${GATE_RESULTS[$i]}" "${GATE_DETAIL[$i]}"
    i=$((i+1))
done

if [ $FAILED -ne 0 ]; then
    say "BLOCKED: one or more gates failed -- refusing to bless $VERSION"
    exit 1
fi
_nwarn=0
for _r in "${GATE_RESULTS[@]}"; do [ "$_r" = "WARN" ] && _nwarn=$((_nwarn+1)); done
if [ "$_nwarn" -gt 0 ]; then
    say "GATES PASS with $_nwarn WARNING(S) -- $VERSION can be blessed, but read the WARN row(s) above: something could not be verified, which is not the same as verified"
else
    say "ALL GATES PASS -- $VERSION is ready to bless"
fi

_tag_plan(){ # _tag_plan <label> <repo> <commit>: what --apply does with that repo's tag
    if [ -n "$(git -C "$2" rev-parse -q --verify "refs/tags/${VERSION}^{commit}" 2>/dev/null)" ]; then
        echo "$1: tag $VERSION already at ${3:0:12}"
    else
        echo "$1: cut annotated tag $VERSION at ${3:0:12} and push it to origin"
    fi
}
if [ $APPLY -eq 0 ]; then
    say "DRY RUN -- would tag: $(_tag_plan sigmond "$SIGMOND_REPO" "$SIG_COMMIT"); $(_tag_plan 'image repo' "$PX_REPO" "$PX_COMMIT")"
    say "DRY RUN -- would create Release $VERSION on $GH_REPO${MIRROR_SLUG:+ and on $MIRROR_SLUG (--mirror)} attaching:"
    say "  $(basename "$MANIFEST")"
    say "  $(basename "$SHAFILE")"
    say "DRY RUN -- would also publish $(basename "$INSTR_MD"), $(basename "$INSTR_HTML") and $(basename "$CL_PUB") beside the image at every publish target (from origin/main)"
    say "DRY RUN -- the release notes would open with CHANGELOG.md's $VERSION section, under \"What's new\""
    if [ -n "$FLASHER" ]; then say "DRY RUN -- and sigmond-flash-latest.sh from image commit $FLASHER_AT (sha256 $FLASHER_SHA), under its fixed name"; else say "DRY RUN -- ${FLASHER_NOTE:-no flasher}"; fi
    say "Re-run with --apply to publish. Creating a public Release is outward-facing and requires explicit human authorization."
    exit 0
fi

# ---- --apply: build notes, self-check, confirm, tag, publish
# HamSCI/sigmond and the image repo are PUBLIC. Release notes reference the
# image by filename and sha256 only -- never a host, path, IP, or username.

# The publish step sources publish-lib.sh from the image repo.  Find out now,
# before anything is tagged, rather than after the Release exists.
if [ ! -r "$PUBLISH_LIB" ]; then
    say "FATAL: no publish library at $PUBLISH_LIB (set PUBLISH_LIB or PROXMOX_REPO) -- refusing before anything is tagged or published"
    exit 1
fi

# The commits since the previous release: sigmond's own history, from the
# newest release tag below this version that origin/main holds.  The tag for
# this version does not exist yet, so the range ends at the sigmond commit.
PREV_TAG="$( { git -C "$SIGMOND_REPO" tag --merged origin/main 2>/dev/null; echo "$VERSION"; } \
    | grep -E '^v[0-9]' | grep -vE -- '-dev|\+' | sort -uV \
    | awk -v cur="$VERSION" '$0==cur{print prev; exit} {prev=$0}')"
if [ -n "$PREV_TAG" ]; then
    CHANGELOG="$(git -C "$SIGMOND_REPO" log --oneline "${PREV_TAG}..${SIG_COMMIT}")"
else
    CHANGELOG="(no earlier release tag in sigmond)"
fi

# $PREV_TAG is an operator-chosen git tag NAME -- a PRIOR release's tag,
# not this one, so gate 0 never validated it and never will (gate 0 only
# ever looks at $VERSION, the version being blessed right now). It is used
# above to compute the git log range and nowhere else: it must never reach
# $NOTES itself. A reviewer proved why the hard way, against the real
# hard-abort scan: tag a poisoned prior release (e.g.
# `v9.0.0-root@10.0.0.5`), merge it to origin/main, then tag a completely
# clean NEXT version -- the clean release's own notes would still contain
# that old tag's name verbatim in the "### Changelog since <tag>" header,
# and the hard-abort tier is RIGHT to refuse publishing it: the text
# really would appear in a public Release if allowed through. That is a
# true positive, not a false one, and excluding this line from the scan
# (rather than fixing what gets published) would silence a real leak.
#
# The actual fix is to stop publishing the tag NAME at all -- the header
# only exists to tell a reader what range the changelog covers, and a
# short commit SHA does that job without dragging an arbitrary
# operator-chosen string into published output. $PREV_TAG keeps doing its
# job internally (the git log range above already used it); only the
# SAFE, script-computed SHA derived from it goes into $NOTES.
PREV_TAG_COMMIT=""
if [ -n "$PREV_TAG" ]; then
    PREV_TAG_COMMIT="$(git -C "$SIGMOND_REPO" rev-parse --short "${PREV_TAG}^{commit}" 2>/dev/null)"
fi

# The notes open with the changelog section gate 8 checked (CL_BODY), under
# "What's new": the words written for the operator come first, the generated
# record after.  WN_FIRST..WN_LAST are the section's line numbers in $NOTES, so
# the leak check below can name CHANGELOG.md as the place to fix a hit there.
NOTES="$INSTR_DIR/release-notes.md"
{
    echo "## sigmond $VERSION"
    echo
    echo "### What's new"
    echo
} > "$NOTES"
WN_FIRST=$(( $(wc -l < "$NOTES") + 1 ))
cat "$CL_BODY" >> "$NOTES"
WN_LAST=$(wc -l < "$NOTES")
{
    echo
    echo "Full changelog: $CL_URL"
    echo
    echo "### Release record"
    echo
    echo "- Version: $VERSION"
    echo "- sigmond commit: $SIG_COMMIT"
    echo "- Image repo commit: $PX_COMMIT"
    echo "- Image: $IMGBASE"
    echo "- SHA-256: $(cut -d' ' -f1 < "$SHAFILE")"
    echo "- Test verdict: PASS (PHASE D PASS -- NESTED TEST COMPLETE, test-nested-v3.sh)"
    echo "- Bare-metal test: PASS ($(basename "$_g10_rec"))"
    echo
    if [ -n "$PREV_TAG_COMMIT" ]; then
        echo "### sigmond commits since $PREV_TAG_COMMIT"
    else
        echo "### sigmond commits"
    fi
    echo '```'
    echo "$CHANGELOG"
    echo '```'
} >> "$NOTES"

# Leakage self-check -- SHAPES, not an enumerated list of known hostnames.
# A denylist of known names (the version before this one) only ever catches
# names someone already thought to list; a reviewer found this the hard way
# with a hypothetical commit message naming a runner this list had never
# heard of, and the real test-v3.log on this rig already contains
# /srv/build/v3/... paths that a denylist would also have missed.
#
# Scope is DIFFERENT PER TIER -- this took two attempts to get right, and
# the failure mode of getting it wrong is instructive enough to leave both
# attempts documented here rather than just the current answer.
#
# Attempt 1 scanned the whole $NOTES file with every shape. A reviewer
# found this self-defeating: "- Image: sigmond-appliance-v3.32-...img"
# matches the hyphenated-hostname shape as "appliance-v3" (gate 0 forces
# every version to start v[0-9]*, so this fired structurally on 100% of
# releases), and the hardcoded "...test-nested-v3.sh" verdict line matched
# the same shape as "nested-v3" regardless of version. NFLAGGED was never
# 0 in real usage, so the simple confirm phrase was unreachable and every
# release paid the deliberate-confirmation cost meant for real risk.
#
# Attempt 2 (over-corrected) scoped EVERYTHING, including the hard-abort
# checks, to the changelog body only -- on the reasoning that everything
# else in $NOTES is script-generated from gate-validated values and "can't
# carry a leak the script did not put there itself." That reasoning is
# right for the FLAGGED-tier shapes (paths, domains, hostname-shaped
# tokens -- these are exactly what matched the boilerplate above) but
# wrong for $VERSION specifically: it's an operator-supplied git tag name,
# and gate 0's positive check (v[0-9]*) barely constrains what follows the
# leading digit. A reviewer proved this against the committed script by
# creating the real tag `v1-root@192.168.1.14-leak` -- git accepts it
# (only "@{" is special) -- which is echoed verbatim into the title, the
# "- Version:" line, and (via $IMGBASE) the "- Image:" line. Scoping the
# hard-abort checks to the changelog meant NONE of those lines were ever
# scanned, so a poisoned tag sailed straight through to the simple confirm
# phrase with a real IP and a real user@host string sitting in the
# published notes.
#
# So: HARD-ABORT shapes (IPv4, user@host) scan the FULL $NOTES -- they
# never collided with the boilerplate in the first place (verified
# directly below), so there was never a reason to narrow them, and
# $VERSION is exactly the kind of operator-supplied text they exist to
# catch. FLAGGED shapes stay scoped to the changelog body -- that's where
# the real boilerplate collision was, and where narrowing was actually
# needed.
#
# I considered other things interpolated into $NOTES and either brought
# them into scope or deliberately did not:
#   - $PREV_TAG itself does NOT reach $NOTES at all (see the block above
#     this one) -- only its resolved commit SHA does, precisely so this
#     scan never has to make a judgment call about whether an old tag
#     name is safe to publish. An earlier version of this comment argued
#     the tag name was fine to leave unscanned because "it already leaked
#     the moment it was pushed" -- a reviewer correctly rejected that: a
#     prior leak does not make republishing the same string in a NEW
#     public Release harmless, and a reviewer proved the practical cost by
#     tagging a poisoned prior release, merging it to origin/main, then
#     tagging a completely clean next version -- whose own notes still
#     carried the old tag's name in the header, correctly hard-aborting a
#     release that had nothing wrong with it. Removing the tag name from
#     published output fixes that at the source instead of arguing about
#     whether to scan it.
#   - the attached manifest/sha256 files themselves are NOT interpolated
#     into the notes TEXT at all (gh attaches them as separate binary/text
#     assets), so they're out of scope for a check about what's IN the
#     notes. Worth flagging anyway: a manifest captured off a long-lived,
#     drifted install (not a fresh golden template) can carry a free-text
#     "updates since install" section with operator-written entries -- see
#     the Task 3 report's own sample. In practice this is narrower than it
#     sounds -- Task 3's capture point runs immediately after
#     provisioning, before any post-install drift can occur, so a fresh
#     golden-template build structurally cannot produce that section, and
#     a leakage grep against a realistic manifest sample came back clean.
#     But UNLIKE the changelog, the manifest's contents are never PRINTED
#     to the operator before the confirmation prompt below -- only the
#     notes text is. So if this residual risk were ever hit (a manifest
#     captured some other way, or the capture point's guarantee breaking
#     later), there is no human backstop for it at all, not even the
#     deliberate-confirmation one. That is a real gap, left as a gap
#     rather than silently assumed covered.
#
# Two tiers, split by how often the shape is legitimately part of ordinary
# release-note prose on this fleet vs. how bad it is to miss:
#
#   HARD ABORT (no recourse, ever): an IPv4 literal, a user@host reference.
#   Both are near-unambiguous evidence of a specific network identity or
#   credential context -- release notes never legitimately need either, so
#   there is nothing for a human to "wave through."
#
#   FLAGGED (human must acknowledge, deliberately): everything shape-ier
#   than that -- absolute paths (Unix and Windows), a dotted name under a
#   real-looking TLD, and two hostname-shaped-token heuristics. A reviewer
#   measured the false-positive cost of treating these as hard aborts: the
#   Unix-path shape alone fires on a meaningful fraction of ordinary commit
#   messages on this fleet (a date like 8/16, a ratio like 20/21, a version
#   like 3.24.0/stable, a GitHub issue URL) because this fleet's commit
#   messages are heavy on exactly that kind of text. A hard abort with no
#   recourse but rewriting git history trains whoever hits it first to
#   comment the check out -- a guard that gets removed protects nothing.
#   So these inform the human instead of replacing them: they're printed
#   with what matched and why, and acknowledging them requires a more
#   deliberate confirmation than a clean run (below), not just a bigger
#   red warning the operator learns to scroll past.
#
# The changelog is raw `git log --oneline` text, so every line starts with
# a short hex commit hash -- and a hex hash (e.g. "aca5772") itself matches
# the letters-then-digits hostname shape below on nearly every line,
# drowning any real signal. Now that the scan is scoped to a file
# containing ONLY changelog lines (see CHANGELOG_FILE below), that strip
# can be applied to the whole file safely and simply -- there is no longer
# any non-changelog content in the same file it could bleed into by
# accident (the fence-toggling awk state machine from the previous version
# existed only to guard against that, and is no longer needed once the
# scope itself excludes non-changelog text).
#
# Known gaps, left as gaps deliberately rather than papered over -- human
# review (below) is load-bearing for these, not this regex:
#   - a hostname that is pure letters with no digit (or a real word used
#     as a hostname) matches no shape here at all
#   - IPv6 literals are not matched (IPv4 is)
#   - a credential or identity string with no @ and no path separator
#     (e.g. a bare API key or token) matches nothing here
#   - a description of internal topology in plain English ("the box next
#     to the antenna switch") matches nothing here and never will by shape
# The CHANGELOG.md section joins the git log in this file: it is hand-written
# prose, as commit messages are, so the flagged shapes apply to it the same way.
# It goes in as published: the bytes of <stem>.CHANGELOG.md, HEADING INCLUDED,
# less the last two lines (the blank and the "Full changelog:" link the script
# itself writes).  The notes carry only the body, so the heading reaches no
# scan through $NOTES; a review of 3789ffb caught a heading such as
# "## sigmond-appliance v3.93 — tested at 10.0.0.5" going out unscanned.
# The hard-abort tier below scans the whole of $CL_PUB for the same reason.
CHANGELOG_FILE="$INSTR_DIR/changelog-scan.txt"
printf '%s\n' "$CHANGELOG" > "$CHANGELOG_FILE"
sed '$d' "$CL_PUB" | sed '$d' >> "$CHANGELOG_FILE"

# Collected per DISTINCT LINE, same reasoning as the flagged tier below:
# a line matching both shapes (e.g. "root@192.168.1.14" is simultaneously
# an IPv4 address AND a user@host reference) should report once, not
# twice, with every reason attached.
declare -A HARD_LINE_LABELS=()
note_hard(){ # note_hard <label> <newline-separated "N:..." grep -n output>
    local label="$1" hits="$2" ln
    [ -n "$hits" ] || return 0
    while IFS= read -r ln; do
        [ -n "$ln" ] || continue
        if [ -n "${HARD_LINE_LABELS[$ln]:-}" ]; then
            HARD_LINE_LABELS[$ln]="${HARD_LINE_LABELS[$ln]}, $label"
        else
            HARD_LINE_LABELS[$ln]="$label"
        fi
    done <<< "$(printf '%s\n' "$hits" | cut -d: -f1)"
}

# ---- hard-abort tier: scans the FULL $NOTES, not just the changelog ----
# $VERSION is operator-supplied (the bless's first argument, and the name
# both tags will carry).  Gate 4 now holds it to the list's version, which
# must read v<N>.<N>; gate 0 alone barely constrains it -- it only rejects "-dev" and "+"; its
# positive check is the case pattern v[0-9]*, which is "v" + one digit +
# ANYTHING. git tags happily accept an "@" and dotted-quad-looking runs
# too (only "@{" is special to git). $VERSION is echoed verbatim into the
# title, the "- Version:" line, and (via $IMGBASE) the "- Image:" line --
# none of which the changelog-only scope above ever looks at. A reviewer
# proved this against this exact script by creating the real tag
# `v1-root@192.168.1.14-leak`: it passed gates 0-5, the changelog-scoped
# leak check reported clean, and the notes -- containing a real IP AND a
# real user@host string, verbatim, twice -- reached the simple confirm
# phrase with zero friction.
#
# So the two tiers are scoped differently, on purpose, because they have
# different collision profiles. IPv4 and user@host NEVER collided with the
# boilerplate (verified directly: neither shape matches any of the
# version/commit/image/sha256/verdict lines for a normal release) -- the
# boilerplate collision that justified narrowing the FLAGGED tier (below)
# never applied to these two. Restoring full-$NOTES coverage here costs
# nothing and closes the tag-name hole outright, with no override
# possible -- exactly like every other hard-abort case.
note_hard "an IPv4 address" \
    "$(grep -nE '[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}' "$NOTES" || true)"
note_hard "a user@host reference" \
    "$(grep -nE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+' "$NOTES" || true)"

# The same two shapes over the exact bytes of <stem>.CHANGELOG.md, which the
# publish targets receive: its heading line appears nowhere in $NOTES.  Body
# lines get reported here and above alike, since both files would carry them.
declare -A PUB_HARD_LABELS=()
_pub_hard(){ # _pub_hard <label> <grep -n output>
    local ln
    [ -n "$2" ] || return 0
    while IFS= read -r ln; do
        [ -n "$ln" ] || continue
        PUB_HARD_LABELS[$ln]="${PUB_HARD_LABELS[$ln]:+${PUB_HARD_LABELS[$ln]}, }$1"
    done <<< "$(printf '%s\n' "$2" | cut -d: -f1)"
}
_pub_hard "an IPv4 address" \
    "$(grep -nE '[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}' "$CL_PUB" || true)"
_pub_hard "a user@host reference" \
    "$(grep -nE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+' "$CL_PUB" || true)"

if [ ${#HARD_LINE_LABELS[@]} -gt 0 ] || [ ${#PUB_HARD_LABELS[@]} -gt 0 ]; then
    say "FATAL: the release notes or $(basename "$CL_PUB") contain a hard-stop leak shape -- refusing to publish. No confirmation can override this. Line-by-line remedy:"
    for ln in $(printf '%s\n' "${!PUB_HARD_LABELS[@]}" | sort -n); do
        printf '  %s line %s: %s\n    matched: %s\n    remedy: %s\n' "$(basename "$CL_PUB")" "$ln" "$(sed -n "${ln}p" "$CL_PUB")" "${PUB_HARD_LABELS[$ln]}" \
            "this is CHANGELOG.md's $VERSION section on origin/main (its heading or its text) -- take the address or user@host out of it, commit and push CHANGELOG.md, then re-run"
    done
    for ln in $(printf '%s\n' "${!HARD_LINE_LABELS[@]}" | sort -n); do
        LINE_TEXT="$(sed -n "${ln}p" "$NOTES")"
        # The remedy depends on WHERE the match is, not just what matched.
        # A hit in a script-generated field means the version argument
        # itself is poisoned (the only operator-supplied text that reaches these
        # specific lines is $VERSION, echoed at the title/Version/Image
        # lines); a hit anywhere else is changelog content, where "fix
        # the tag" does nothing -- the offending text lives in a past
        # commit message already in history.
        if [ "$ln" -ge "$WN_FIRST" ] && [ "$ln" -le "$WN_LAST" ]; then
            LINE_TEXT="## changelog section ##"   # pick the CHANGELOG.md remedy below
        fi
        case "$LINE_TEXT" in
            "## changelog section ##")
                REMEDY="this is CHANGELOG.md's $VERSION section on origin/main, which these notes quote under \"What's new\" -- take the address or user@host out of that section, commit and push CHANGELOG.md, then re-run" ;;
            "## sigmond "*|"- Version: "*|"- Image: "*)
                REMEDY="the version argument ($VERSION) is poisoned -- nothing was tagged; bless again under a clean version name" ;;
            "- sigmond commit: "*|"- Image repo commit: "*|"- SHA-256: "*|"- Test verdict:"*|"- Bare-metal test:"*|"Full changelog: "*)
                REMEDY="this is a script-generated field that should be pure hex/literal text and should never match this shape -- something is wrong upstream of this script; do not trust anything else this run produced until that's understood" ;;
            *)
                REMEDY="this is changelog content -- a past commit message already in history. Re-tagging does not fix it; the commit needs to be excluded from what ships (adjust the release range) or the leak needs to be handled out of band (e.g. rewriting that commit's message and force-pushing, which has its own costs)" ;;
        esac
        printf '  line %s: %s\n    matched: %s\n    remedy: %s\n' "$ln" "$(sed -n "${ln}p" "$NOTES")" "${HARD_LINE_LABELS[$ln]}" "$REMEDY"
    done
    rm -f "$CHANGELOG_FILE"
    exit 1
fi

# ---- flagged tier ----
# Collected per DISTINCT LINE, not per occurrence: a line matching two
# shape categories (e.g. a path that also looks like a hostname) is
# recorded once, with both reasons attached, and counted once. A reviewer
# caught this the hard way in the previous version: counting occurrences
# instead of lines meant the printed tally and the number the confirmation
# phrase required could differ from what an operator would count by eye,
# which makes an honest, careful operator fail the confirmation on the
# first try -- exactly the muscle-memory-proof design fighting itself.
declare -A LINE_LABELS=()
note_flag(){ # note_flag <label> <newline-separated "N:..." grep -n output>
    local label="$1" hits="$2" ln
    [ -n "$hits" ] || return 0
    while IFS= read -r ln; do
        [ -n "$ln" ] || continue
        if [ -n "${LINE_LABELS[$ln]:-}" ]; then
            LINE_LABELS[$ln]="${LINE_LABELS[$ln]}, $label"
        else
            LINE_LABELS[$ln]="$label"
        fi
    done <<< "$(printf '%s\n' "$hits" | cut -d: -f1)"
}

note_flag "an absolute Unix path" \
    "$(grep -nE '/[A-Za-z0-9_.-]{2,}' "$CHANGELOG_FILE" || true)"
note_flag "a Windows-style path (drive letter)" \
    "$(grep -nE '\b[A-Za-z]:\\' "$CHANGELOG_FILE" || true)"
note_flag "a domain name (name.tld shape)" \
    "$(grep -nE '\b[A-Za-z0-9-]{1,63}\.(com|org|net|io|edu|gov|mil|local|lan|internal|dev|app|co)\b' "$CHANGELOG_FILE" || true)"
SCRUBBED_FOR_HOSTNAME_CHECK="$(sed -E 's/^[0-9a-f]{7,40}[[:space:]]+//' "$CHANGELOG_FILE")"
note_flag "a short hostname-shaped token (letters+digits)" \
    "$(printf '%s\n' "$SCRUBBED_FOR_HOSTNAME_CHECK" | grep -nE '\b[A-Za-z]{2,}[0-9]{1,4}\b' || true)"
note_flag "a hyphenated hostname-shaped token (e.g. b4-prox)" \
    "$(printf '%s\n' "$SCRUBBED_FOR_HOSTNAME_CHECK" | grep -nE '\b[A-Za-z]{1,6}[0-9]{1,3}-[A-Za-z0-9]{1,20}\b|\b[A-Za-z0-9]{1,20}-[A-Za-z]{1,6}[0-9]{1,3}\b' || true)"

NFLAGGED=${#LINE_LABELS[@]}
if [ "$NFLAGGED" -gt 0 ]; then
    say "$NFLAGGED distinct changelog line(s) flagged -- shape-based, may be false positives, but must be read before publishing:"
    for ln in $(printf '%s\n' "${!LINE_LABELS[@]}" | sort -n); do
        printf '  line %s: %s\n    matched: %s\n' "$ln" "$(sed -n "${ln}p" "$CHANGELOG_FILE")" "${LINE_LABELS[$ln]}"
    done
else
    say "leakage self-check clean (commits and CHANGELOG.md section: no hard-stop or flagged shapes matched)"
fi
rm -f "$CHANGELOG_FILE"

# Human confirmation -- the only backstop that covers what the shapes above
# cannot (see "Known gaps" in the comment above). Read from /dev/tty
# explicitly so a non-interactive invocation (cron, CI, a script piping
# input) cannot be mistaken for consent -- it fails closed instead.
#
# A flagged run requires a MORE deliberate confirmation than a clean run --
# the exact count of DISTINCT flagged lines (matching what was just
# printed) must appear in the typed phrase, which is only possible if the
# operator actually looked at the count just printed, rather than
# reflexively retyping a memorized phrase.
say "----- RELEASE NOTES ($VERSION) -- read before confirming -----"
cat "$NOTES"
say "----- END RELEASE NOTES -----"
if [ -n "$FLASHER" ]; then
    say "the download folders also get sigmond-flash-latest.sh from image commit $FLASHER_AT, sha256 $FLASHER_SHA"
else
    say "${FLASHER_NOTE:-no flasher}"
fi
if [ "$NFLAGGED" -eq 0 ]; then
    PROMPT="Type exactly 'publish $VERSION' to confirm tagging and publication to $GH_REPO${MIRROR_SLUG:+ and $MIRROR_SLUG} (anything else aborts): "
    REQUIRED="publish $VERSION"
else
    PROMPT="$NFLAGGED changelog line(s) were flagged above. Type exactly 'I reviewed $NFLAGGED flagged lines, publish $VERSION' to confirm anyway (anything else aborts): "
    REQUIRED="I reviewed $NFLAGGED flagged lines, publish $VERSION"
fi
if ! CONFIRM="$(read -r -p "$PROMPT" REPLY < /dev/tty && echo "$REPLY")"; then
    say "FATAL: could not read a confirmation from a terminal (/dev/tty) -- refusing to publish non-interactively"
    exit 1
fi
if [ "$CONFIRM" != "$REQUIRED" ]; then
    say "confirmation not given -- aborting, nothing tagged or published"
    exit 1
fi

# ---- the tags: cut at the bless, pushed before any Release -------------------
#
# Gate 1 found each tag absent, or already at the manifest's commit.  Cut the
# absent ones as annotated tags, push them, and read origin back: `gh release
# create --verify-tag` then refuses rather than let GitHub invent a tag at the
# default branch's tip.  A tag already on origin at the right commit (a re-run
# after a publish that stopped part way) is fetched, never re-pushed: a fresh
# annotated tag object would differ from the one origin holds.  Nothing here
# forces or moves a tag.
_cut_tag(){ # _cut_tag <label> <repo> <commit> <message>
    local label="$1" repo="$2" c="$3" msg="$4" lt rt
    rt="$(git -C "$repo" ls-remote --tags origin "refs/tags/${VERSION}" "refs/tags/${VERSION}^{}" 2>/dev/null \
        | awk 'NF{print $1}' | tail -1)"
    lt="$(git -C "$repo" rev-parse -q --verify "refs/tags/${VERSION}^{commit}" 2>/dev/null)"
    if [ -n "$rt" ] && [ "$rt" != "$c" ]; then
        say "FATAL: $label: tag $VERSION on origin names $rt, not $c -- it appeared after gate 1; reconcile by hand"; return 1
    fi
    if [ -n "$lt" ] && [ "$lt" != "$c" ]; then
        say "FATAL: $label: local tag $VERSION names $lt, not $c -- it appeared after gate 1; reconcile by hand"; return 1
    fi
    if [ -n "$rt" ]; then
        [ -n "$lt" ] || git -C "$repo" fetch -q origin "refs/tags/${VERSION}:refs/tags/${VERSION}" \
            || { say "FATAL: $label: could not fetch tag $VERSION from origin"; return 1; }
        say "$label: tag $VERSION already on origin at ${c:0:12}"
        return 0
    fi
    if [ -z "$lt" ]; then
        git -C "$repo" tag -a "$VERSION" -m "$msg" "$c" \
            || { say "FATAL: $label: could not tag $c as $VERSION in $repo"; return 1; }
    fi
    git -C "$repo" push -q origin "refs/tags/${VERSION}" \
        || { say "FATAL: $label: could not push tag $VERSION to origin from $repo"; return 1; }
    rt="$(git -C "$repo" ls-remote --tags origin "refs/tags/${VERSION}" "refs/tags/${VERSION}^{}" 2>/dev/null \
        | awk 'NF{print $1}' | tail -1)"
    if [ "$rt" != "$c" ]; then
        say "FATAL: $label: after the push, origin's tag $VERSION reads '${rt:-nothing}', not $c"; return 1
    fi
    say "$label: tag $VERSION cut at ${c:0:12} and pushed"
}
_cut_tag sigmond "$SIGMOND_REPO" "$SIG_COMMIT" "sigmond $VERSION" || exit 1
_cut_tag "image repo" "$PX_REPO" "$PX_COMMIT" "sigmond $VERSION: the image repo at the commit the image built from" \
    || { say "the sigmond tag stands on origin; no Release was created.  Fix the image repo's tag and re-run: gate 1 accepts the tag already there."; exit 1; }
say "tags pushed: $VERSION in sigmond at ${SIG_COMMIT:0:12}, in the image repo at ${PX_COMMIT:0:12}"

say "publishing Release $VERSION on $GH_REPO"
gh release create "$VERSION" --repo "$GH_REPO" \
    --title "sigmond $VERSION" --notes-file "$NOTES" \
    --target "$SIG_COMMIT" --verify-tag \
    "$MANIFEST" "$SHAFILE"
RC=$?
# A Release that failed leaves work for the operator, and the EXIT trap removes
# the temp directory, notes included.  _keep_notes copies the notes beside the
# manifest on the rig and prints that path, for the by-hand command.
KEPT_NOTES="${MANIFEST%.manifest.txt}.release-notes.md"
_keep_notes(){
    if cp "$NOTES" "$KEPT_NOTES" 2>/dev/null; then echo "$KEPT_NOTES"
    else echo "<the notes printed above; could not keep a copy at $KEPT_NOTES>"; fi
}
if [ $RC -ne 0 ]; then
    _kn="$(_keep_notes)"
    say "FATAL: gh release create on $GH_REPO failed (exit $RC).  Tags $VERSION stand on origin in both repos; no Release exists on $GH_REPO and nothing was published."
    say "  Re-run the bless (gate 1 accepts tags already at their commits), or by hand:"
    say "  gh release create $VERSION --repo $GH_REPO --title 'sigmond $VERSION' --notes-file $_kn --target $SIG_COMMIT --verify-tag $MANIFEST $SHAFILE"
    exit 1
fi
MIRROR_RC=0
if [ -n "$MIRROR_SLUG" ]; then
    # The same Release on the image repo, for v3.93 and older smd and
    # pm-align.  Its tag names the image repo's commit.  A failure here leaves
    # the authoritative Release standing: say so, give the command, go on to
    # publish the image, and exit 1 at the end.
    say "publishing the mirror Release $VERSION on $MIRROR_SLUG"
    gh release create "$VERSION" --repo "$MIRROR_SLUG" \
        --title "sigmond $VERSION" --notes-file "$NOTES" \
        --target "$PX_COMMIT" --verify-tag \
        "$MANIFEST" "$SHAFILE"
    MIRROR_RC=$?
    if [ $MIRROR_RC -ne 0 ]; then
        _kn="$(_keep_notes)"
        say "WARNING: the mirror Release on $MIRROR_SLUG failed -- pre-v3.94 stations cannot see $VERSION until it exists.  By hand:"
        say "  gh release create $VERSION --repo $MIRROR_SLUG --title 'sigmond $VERSION' --notes-file $_kn --target $PX_COMMIT --verify-tag $MANIFEST $SHAFILE"
    fi
fi

# ── promote the image out of gdrive:sigmond-images/pending/ ─────────────────
#
# build-usb-v3.sh uploads every build to gdrive:sigmond-images/pending/ the
# moment it exists (the wd30 in the 2026-09-02 record below was the transport
# at the time; the pending/-vs-download safety it teaches is unchanged), so
# an operator can download and burn in parallel with the nested test.  Nothing
# used to gate that copy: it landed directly in the download directory, where
# nothing told a built-but-untested image apart from a blessed one.  On
# 2026-09-02 a v3.36 build that the nested test went on to stop at 18:45Z sat
# in that directory from 18:24Z until the rebuild overwrote it at 19:01Z --
# same filename, same .sha256 name, so the overwrite also erased the record of
# which build a given download held.
#
# Promotion happens HERE and only here, after every gate above has passed, so
# "in the Drive download folder" means "blessed" and nothing weaker.
#
# A Drive that is unreachable does NOT fail the bless: the GitHub Release is the
# authoritative artifact and it is already published by this point.  Say so
# loudly and leave the operator a one-line command instead of unwinding a
# Release over a download convenience.
if [ $RC -eq 0 ]; then
    IMGBASE="$(basename "${SHAFILE%.sha256}.img")"
    # Destinations come from publish-targets.conf, never from a literal here.
    # Before this file existed the destination was hardcoded to one person's
    # Drive folder while anyone wanting a copy elsewhere moved it by hand, so
    # "where did this build go?" depended on who ran what.  One declaration,
    # every target, named in the log.
    . "$PUBLISH_LIB"
    if ! pub_load "$(dirname "$PUBLISH_LIB")/publish-targets.conf"; then
        say "WARNING: no publish targets — the Release is published and"
        say "         authoritative, but no download folder was updated."
    else
        say "promoting $IMGBASE out of pending/ to $((${#PUB_DESTS[@]})) target(s):"
        pub_describe | while read -r l; do say "$l"; done
        _allok=1
        _SRCDIR="$(dirname "$SHAFILE")"
        for _i in "${!PUB_DESTS[@]}"; do
            _lab="${PUB_LABELS[$_i]}"; _d="${PUB_DESTS[$_i]%/}"
            _ok=1
            if [ "${PUB_STAGES[$_i]}" = "blessed" ]; then
                # Nothing of this build is in that folder yet -- upload it now,
                # from the rig's own copy, so the folder only ever holds
                # blessed images.
                say "  [$_lab] blessed-only target — uploading $IMGBASE now"
                for _f in "$IMGBASE" "${IMGBASE%.img}.sha256" "$(basename "$MANIFEST")"; do
                    [ -f "$_SRCDIR/$_f" ] || { say "  [$_lab] MISSING locally: $_f"; _ok=0; continue; }
                    pub_put "$_d" "" "$_SRCDIR/$_f" 2>/dev/null || _ok=0
                done
                # A blessed-only target must not carry a pending/ at all --
                # its whole promise is that every file in the folder is
                # blessed.  One can exist from before the target was switched
                # to blessed (mjh's did, holding an untested v3.52).  Remove
                # it ONLY after this build's blessed copy has landed, so the
                # folder is never left without an image.
                if [ "$_ok" = 1 ] && pub_has_pending "$_d"; then
                    if pub_purge_pending "$_d"; then
                        say "  [$_lab] removed stale pending/ — a blessed-only folder holds no unblessed builds"
                    else
                        say "  [$_lab] NOTE: could not remove $_d/pending — remove it by hand"
                    fi
                fi
            else
            pub_promote "$_d" "$IMGBASE" "${IMGBASE%.img}.sha256" "$(basename "$MANIFEST")" 2>/dev/null || _ok=0
            fi
            if [ "$_ok" = 1 ]; then
                _link="$(pub_link "$_d" "$IMGBASE")"
                say "  [$_lab] promoted: $_d/$IMGBASE"
                [ -n "$_link" ] && say "  [$_lab] link: $_link"
                # The install instructions go beside the image, in the blessed
                # directory -- never into pending/, and never before the image
                # itself landed.  The files are the ones gate 7 checked
                # (INSTR_MD and INSTR_HTML), already named <stem>.INSTALL.*.
                # A failed upload costs a WARNING and the by-hand recipe, like
                # a failed promotion: the Release exists and stays authoritative.
                if pub_put "$_d" "" "$INSTR_MD" "$INSTR_HTML" 2>/dev/null; then
                    say "  [$_lab] instructions: $(basename "$INSTR_MD"), $(basename "$INSTR_HTML")"
                else
                    _allok=0
                    say "  [$_lab] WARNING: could not publish the install instructions to $_d"
                    say "  [$_lab]   by hand, in an image-repo checkout with origin/main fetched: git show origin/main:INSTALL.md > '$(basename "$INSTR_MD")'; git show origin/main:docs/install-page.html > '$(basename "$INSTR_HTML")'"
                    say "  [$_lab]   then copy both files beside $IMGBASE in $_d"
                fi
                # The changelog section gate 8 checked, with a link to the
                # whole file.  Same place, same order, same failure rule as
                # the install instructions.
                if pub_put "$_d" "" "$CL_PUB" 2>/dev/null; then
                    say "  [$_lab] changelog: $(basename "$CL_PUB")"
                else
                    _allok=0
                    say "  [$_lab] WARNING: could not publish the changelog to $_d"
                    say "  [$_lab]   by hand, in a sigmond checkout with origin/main fetched: copy the '## sigmond $VERSION' section of 'git show origin/main:CHANGELOG.md', heading included, into '$(basename "$CL_PUB")', and end it with the line 'Full changelog: $CL_URL'"
                    say "  [$_lab]   then copy that file beside $IMGBASE in $_d"
                fi
                # The flasher keeps its one name in every folder: rclone copyto onto
                # the same name updates the Drive file in place, so its id and link
                # stay.  Only after the image landed, like the instructions.
                if [ -n "$FLASHER" ]; then
                    _fwhy="$(flasher_vs_published "$_d")"
                    if [ -n "$_fwhy" ]; then
                        _allok=0
                        say "  [$_lab] WARNING: $_fwhy"
                    elif pub_put "$_d" "" "$FLASHER" 2>/dev/null; then
                        say "  [$_lab] flasher: sigmond-flash-latest.sh${_fv:+ $_fv}"
                    else
                        _allok=0
                        say "  [$_lab] WARNING: could not publish sigmond-flash-latest.sh to $_d"
                        say "  [$_lab]   by hand, in an image-repo checkout: git show $PX_COMMIT:sigmond-flash-latest.sh > sigmond-flash-latest.sh (sha256 $FLASHER_SHA); rclone copyto sigmond-flash-latest.sh '$_d/sigmond-flash-latest.sh'"
                    fi
                fi
                # Prune what this bless supersedes, only AFTER the promote
                # landed, so a target is never left without the image.  Newer
                # candidates stay; see pub_superseded.
                if [ "${PUB_STAGES[$_i]}" != "blessed" ]; then
                    mapfile -t _old < <(pub_superseded "$IMGBASE" $(pub_list_pending "$_d"))
                    if [ "${#_old[@]}" -gt 0 ]; then
                        if pub_delete_pending "$_d" "${_old[@]}" 2>/dev/null; then
                            say "  [$_lab] pruned ${#_old[@]} superseded file(s) from pending/: ${_old[*]}"
                        else
                            say "  [$_lab] NOTE: could not prune pending/ — remove superseded builds by hand"
                        fi
                    fi
                fi
            else
                _allok=0
                say "  [$_lab] WARNING: could not promote to $_d"
                say "  [$_lab]   by hand: rclone move '$_d/pending/' '$_d/' --include '${IMGBASE%.img}.*'"
            fi
        done
        # A Drive that is unreachable does NOT fail the bless: the GitHub
        # Release is the authoritative artifact and is already published by
        # this point.  Say so loudly rather than unwind a Release over a
        # download convenience -- but name every target that missed, so a
        # partial publish can never read as a complete one.
        [ -n "$FLASHER_NOTE" ] && say "$FLASHER_NOTE"
        [ "$_allok" = 1 ] || say "NOTE: some targets above did not receive $IMGBASE, its install instructions or its changelog (see the WARNING lines) — the GitHub Release is authoritative."
    fi
fi
# A missing mirror Release leaves the bless unfinished for pre-v3.94 stations.
[ $RC -eq 0 ] && [ "${MIRROR_RC:-0}" -ne 0 ] && RC=1
exit $RC

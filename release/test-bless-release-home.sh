#!/bin/bash
# test-bless-release-home.sh -- the gates the release home added to
# bless-release.sh: the tags it cuts (gate 1), the release list (gate 4), the
# Releases on HamSCI/sigmond and the mirror (gates 6 and 6b), the bare-metal
# record (gate 10), and what --apply tags and creates.
#
# Hermetic.  Each scene builds a scratch origin and checkout for sigmond and
# for the image repo, a scratch rig directory, and stub gh, rclone, ssh and
# scp.  The scratch sigmond commits a symlink to this checkout's lib/, so the
# bless runs the real sigmond.release_list and sigmond.bare_metal_doc.  No
# network, no GitHub; the tags go to the scratch origins.
#
#   usage: [SHOW="g1_absent apply"] ./test-bless-release-home.sh [path/to/bless-release.sh]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
BLESS="${1:-$HERE/bless-release.sh}"
[ -f "$BLESS" ] || { echo "FATAL: no bless-release.sh at $BLESS"; exit 1; }
BLESS="$(cd "$(dirname "$BLESS")" && pwd)/$(basename "$BLESS")"
REAL_LIB="$(cd "$HERE/../lib" && pwd)"
[ -f "$REAL_LIB/sigmond/release_list.py" ] || { echo "FATAL: no sigmond lib at $REAL_LIB"; exit 1; }
command -v script >/dev/null || { echo "FATAL: script(1) missing -- --apply needs a pty for its confirmation"; exit 1; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_TERMINAL_PROMPT=0
export GIT_AUTHOR_NAME=scratch GIT_AUTHOR_EMAIL=scratch@example.invalid
export GIT_COMMITTER_NAME=scratch GIT_COMMITTER_EMAIL=scratch@example.invalid

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
chk() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "want: $3 | got: $2"; fi; }
has() { case "$2" in *"$3"*) ok "$1" ;; *) bad "$1" "missing: $3 | in: ${2:0:400}" ;; esac; }
lacks() { case "$2" in *"$3"*) bad "$1" "found: $3 | in: ${2:0:400}" ;; *) ok "$1" ;; esac; }

# ── stubs ───────────────────────────────────────────────────────────────────
# gh: STUB_GH_REPOS lists the repos that exist (repo view answers for them);
# STUB_GH_RELEASES lists "<slug>:<tag>" pairs that already have a Release.
mkdir -p "$T/bin"
cat > "$T/bin/gh" <<'EOF'
#!/bin/bash
echo "gh $*" >> "$STUB_CALLS"
repo=""; a=("$@")
for ((i=0; i<${#a[@]}; i++)); do [ "${a[$i]}" = "--repo" ] && repo="${a[$((i+1))]}"; done
case "${1:-} ${2:-}" in
    "repo view")
        case " ${STUB_GH_REPOS:-HamSCI/sigmond HamSCI/sigmond-proxmox} " in
            *" $3 "*) echo "$3"; exit 0 ;;
        esac
        echo "GraphQL: Could not resolve to a Repository with the name '$3'. (repository)" >&2; exit 1 ;;
    "release view")
        case " ${STUB_GH_RELEASES:-} " in *" $repo:$3 "*) echo "title: $3"; exit 0 ;; esac
        echo "release not found" >&2; exit 1 ;;
    "release create") exit "${STUB_GH_CREATE_RC:-0}" ;;
esac
exit 0
EOF
for c in rclone ssh scp; do
    printf '#!/bin/bash\necho "%s $*" >> "$STUB_CALLS"\nexit 0\n' "$c" > "$T/bin/$c"
done
chmod +x "$T"/bin/*

# ── fixtures ────────────────────────────────────────────────────────────────
h40() { printf "$1%.0s" {1..40}; }
A40="$(h40 a)"; B40="$(h40 b)"; C40="$(h40 c)"
# The rows the scratch image carries besides sigmond and the three natives.
OTHER_ROWS="callhash:$(h40 1) hf-timestd:$(h40 2) hs-uploader:$(h40 3) ka9q-python:$(h40 4) ka9q-radio:$(h40 5) psk-recorder:$(h40 6) wspr-recorder:$(h40 7)"

bm_doc() { # bm_doc <verified-against text> <checkout version> [bring-up extra]
    cat <<EOF
# Installing a station on your own Linux machine

> **Audience:** operator
> **Status:** current
> **Verified against:** $1

## 2. Install sigmond

\`\`\`bash
git clone https://github.com/HamSCI/sigmond /opt/git/sigmond/sigmond
cd /opt/git/sigmond/sigmond
git checkout $2
./install.sh
\`\`\`

## 3. Bring up the station

\`\`\`bash
smd bringup dasi2 --callsign AC0G --grid EM38ww ${3:-}\\
                  --release-list /opt/git/sigmond/sigmond/release/components.toml
\`\`\`
EOF
}
inst() { printf '# Guide\n\n> **Status:** current\n> **Verified against:** %s\n' "$1"; }
page() { printf '<p>Download <span>%s</span>.</p>\n' "$1"; }
cl_doc() { printf '# changelog\n\n## sigmond %s\n\n- The release lives in sigmond.\n' "$1"; }

sc_reset() {
    SC_VERSION=v3.70; SC_DATE=20261008
    SC_LIST_VERSION=""          # "" = SC_VERSION
    SC_LIST_ROW=""              # "name=sha": one row the list holds instead of the image's
    SC_SIG_TAG=""               # remote | local | here: where a v3.70 tag sits in sigmond
    SC_PX_TAG=""                # the same for the image repo
    SC_RECORDS=DEFAULT          # bare-metal records; DEFAULT = one PASS for this commit
    SC_DOC_DRIFT=0              # 1 = a doc command changes after the record
    SC_PX_ORIGIN_URL=""         # the image repo checkout's origin as written in its config
    SC_STEM=new                 # new | old (sigmond-appliance-) | both
}

# mkscene <name> -- $T/<name>/{px.git,px-seed,px,sig.git,sig-seed,sig,rig,pub,tmp}
mkscene() {
    local S="$T/$1" v="$SC_VERSION"
    local stem="sigmond-$v-$SC_DATE-release"
    [ "$SC_STEM" = old ] && stem="sigmond-appliance-$v-$SC_DATE-release"
    mkdir -p "$S/rig/bare-metal" "$S/pub" "$S/tmp"
    # The image repo: the commit the image built from, then the instructions.
    git init -q -b main --bare "$S/px.git"
    git clone -q "$S/px.git" "$S/px-seed" 2>/dev/null
    (   cd "$S/px-seed" || exit 1
        printf '#!/bin/bash\necho firstboot @@VERSION@@\n' > firstboot-v3.sh
        mkdir -p docs; inst "sigmond v3.69" > INSTALL.md; page old.img > docs/install-page.html
        git add -A && git commit -q -m "image repo at the build" && git push -q origin main 2>/dev/null
    )
    PX="$(git -C "$S/px-seed" rev-parse HEAD)"
    git clone -q "$S/px.git" "$S/px" 2>/dev/null
    (   cd "$S/px-seed" || exit 1
        inst "sigmond $v (hardware 2026-10-08)" > INSTALL.md; page "$stem.img" > docs/install-page.html
        git add -A && git commit -q -m "instructions" && git push -q origin main 2>/dev/null
    )
    if [ -n "$SC_PX_ORIGIN_URL" ]; then
        git -C "$S/px" config remote.origin.url "$SC_PX_ORIGIN_URL"
        git -C "$S/px" config "url.$S/px.git.insteadOf" "$SC_PX_ORIGIN_URL"
    fi
    # sigmond: the commit the image installed (the list, the pins, lib/), then
    # the release docs on origin/main.
    git init -q -b main --bare "$S/sig.git"
    git clone -q "$S/sig.git" "$S/sig-seed" 2>/dev/null
    (   cd "$S/sig-seed" || exit 1
        ln -s "$REAL_LIB" lib
        mkdir -p bin release docs
        printf "_ONION_COMMIT = '%s'\n_WSJTX_COMMIT = '%s'\n_UBERSDR_COMMIT = '%s'\n" "$A40" "$B40" "$C40" > bin/smd
        {   printf 'version = "%s"\n\n[components]\n' "${SC_LIST_VERSION:-$v}"
            local p n s
            for p in onion:$A40 wsjtx:$B40 ubersdr_wsprd:$C40 sigmond-proxmox:$PX $OTHER_ROWS; do
                n="${p%%:*}"; s="${p#*:}"
                [ "${SC_LIST_ROW%%=*}" = "$n" ] && s="${SC_LIST_ROW#*=}"
                printf '%s = "%s"\n' "$n" "$s"
            done
        } > release/components.toml
        cl_doc v3.69 > CHANGELOG.md
        bm_doc "sigmond v3.69" v3.69 > docs/install-bare-metal.md
        git add -A && git commit -q -m "sigmond at the build" && git push -q origin main 2>/dev/null
    )
    SIG="$(git -C "$S/sig-seed" rev-parse HEAD)"
    git clone -q "$S/sig.git" "$S/sig" 2>/dev/null
    (   cd "$S/sig-seed" || exit 1
        cl_doc "$v — blessed 2026-10-08" > CHANGELOG.md
        bm_doc "sigmond $v on 2026-10-08" "$v" > docs/install-bare-metal.md
        git add -A && git commit -q -m "release docs" && git push -q origin main 2>/dev/null
    )
    DIGEST="$(PYTHONPATH="$REAL_LIB" python3 -m sigmond.bare_metal_doc digest --doc "$S/sig-seed/docs/install-bare-metal.md")"
    if [ "$SC_DOC_DRIFT" = 1 ]; then
        (   cd "$S/sig-seed" || exit 1
            bm_doc "sigmond $v on 2026-10-08" "$v" "--with-optional " > docs/install-bare-metal.md
            git add -A && git commit -q -m "doc: a command changed after the test" && git push -q origin main 2>/dev/null
        )
    fi
    # Tags that already stand.
    local other_sig other_px
    other_sig="$(git -C "$S/sig-seed" rev-parse HEAD)"; other_px="$(git -C "$S/px-seed" rev-parse HEAD)"
    case "$SC_SIG_TAG" in
        remote) git -C "$S/sig-seed" tag -a "$v" -m x "$other_sig" && git -C "$S/sig-seed" push -q origin "refs/tags/$v" 2>/dev/null ;;
        local)  git -C "$S/sig" fetch -q origin && git -C "$S/sig" tag "$v" origin/main ;;
        here)   git -C "$S/sig-seed" tag -a "$v" -m x "$SIG" && git -C "$S/sig-seed" push -q origin "refs/tags/$v" 2>/dev/null ;;
    esac
    case "$SC_PX_TAG" in
        remote) git -C "$S/px-seed" tag -a "$v" -m x "$other_px" && git -C "$S/px-seed" push -q origin "refs/tags/$v" 2>/dev/null ;;
        local)  git -C "$S/px" fetch -q origin && git -C "$S/px" tag "$v" origin/main ;;
        here)   git -C "$S/px-seed" tag -a "$v" -m x "$PX" && git -C "$S/px-seed" push -q origin "refs/tags/$v" 2>/dev/null ;;
    esac
    # The rig: image, checksum, manifest, nested-test log.
    printf 'image bytes %s\n' "$stem" > "$S/rig/$stem.img"
    (cd "$S/rig" && sha256sum "$stem.img" > "$stem.sha256")
    local fb; fb="$(sed "s|@@VERSION@@|$v|g" "$S/px-seed/firstboot-v3.sh" | sha256sum | cut -d' ' -f1)"
    {   echo "image_version: $v"
        echo "appliance_commit: $PX"
        echo "appliance_tag: $v"
        echo "firstboot_sha256: $fb"
        echo "native_built_capture: ok"
        echo "native_built_onion: $A40"; echo "native_built_wsjtx: $B40"; echo "native_built_ubersdr_wsprd: $C40"
        echo "native_provenance_wsjtx: $B40"; echo "native_provenance_ubersdr_wsprd: $C40"
        echo
        echo "components (live):"
        echo "    onion            ${A40:0:7}"
        echo "    sigmond          ${SIG:0:9}"
        echo "    ubersdr_wsprd    ${C40:0:7}"
        echo "    wsjtx            ${B40:0:9}"
        local p r
        for p in $OTHER_ROWS; do r="${p#*:}"; printf '    %-16s %s\n' "${p%%:*}" "${r:0:9}"; done
    } > "$S/rig/$stem.manifest.txt"
    printf 'USB image under test: %s.img\nPHASE D PASS -- NESTED TEST COMPLETE\n' "$stem" > "$S/rig/test-v3.log"
    if [ "$SC_STEM" = both ]; then
        cp "$S/rig/$stem.img" "$S/rig/sigmond-appliance-$v-$SC_DATE-release.img"
    fi
    # Bare-metal records: "<stamp> <PASS|FAIL> <this|other> <good|bad>" each.
    local recs="$SC_RECORDS" r
    [ "$recs" = DEFAULT ] && recs="20261008T120000Z PASS this good"
    while read -r stamp res who dg; do
        [ -n "$stamp" ] || continue
        local c="$SIG" d="$DIGEST"
        [ "$who" = other ] && c="$(h40 9)"
        [ "$dg" = bad ] && d="$(printf 'f%.0s' {1..64})"
        {   echo "bare_metal_record: 1"
            echo "version: $v"
            echo "sigmond_commit: $c"
            echo "doc_commands_sha256: $d"
            echo "release_list_check: $res"
            echo "smd_check_exit: 0"
            echo "radiod_consumers: $res"
            echo "result: $res"
            echo "finished_utc: ${stamp:0:4}-${stamp:4:2}-${stamp:6:2}T12:00:00Z"
        } > "$S/rig/bare-metal/$v-$stamp.record"
    done <<<"$recs"
    # A publish library with no targets: these scenes test the tags and the
    # Releases; test-bless-instructions.sh covers the publish.
    printf 'pub_load(){ return 1; }\n' > "$S/pub/publish-lib.sh"
    STEM="$stem"
}

# run_bless <scene> <apply 0|1> <args...> -- sets OUT and RC.
run_bless() {
    local name="$1" S="$T/$1" apply="$2"; shift 2
    : > "$S/calls"
    local envs=(SIGMOND_REPO="$S/sig" PROXMOX_REPO="$S/px" RIG_DIR="$S/rig" PUBLISH_LIB="$S/pub/publish-lib.sh"
                STUB_CALLS="$S/calls" TMPDIR="$S/tmp" PATH="$T/bin:$PATH"
                STUB_GH_REPOS="${STUB_GH_REPOS:-HamSCI/sigmond HamSCI/sigmond-proxmox}"
                STUB_GH_RELEASES="${STUB_GH_RELEASES:-}")
    if [ "$apply" = 1 ]; then
        OUT="$(cd "$S" && printf 'publish %s\n' "$SC_VERSION" | env "${envs[@]}" \
            script -qefc "bash '$BLESS' $SC_VERSION $* --apply" /dev/null 2>&1)"
    else
        OUT="$(cd "$S" && env "${envs[@]}" bash "$BLESS" "$SC_VERSION" "$@" 2>&1)"
    fi
    RC=$?
    OUT="$(printf '%s' "$OUT" | tr -d '\r')"
    case " ${SHOW:-} " in *" $name "*) printf '%s\n' "$OUT" | sed 's/^/      | /' ;; esac
}
row() { grep -F "[gate $1 (" <<<"$OUT" || true; }

# ═══════════════════════════════════════════════════════════════════════════
echo "a candidate that passes every gate"
sc_reset; mkscene base; run_bless base 0 --mirror
chk   "the bless exits 0"           "$RC" "0"
has   "all gates pass"              "$OUT" "ALL GATES PASS"
lacks "no unbound variable"         "$OUT" "unbound variable"

echo "gate 3: both image stems"
sc_reset; SC_STEM=old; mkscene g3_old; run_bless g3_old 0 --mirror
has   "g3 a sigmond-appliance- image still counts" "$(row 3)" " PASS "
chk   "g3 old stem: the bless exits 0" "$RC" "0"
sc_reset; SC_STEM=both; mkscene g3_both; run_bless g3_both 0 --mirror
has   "g3 both stems for one version: FAIL" "$(row 3)" "multiple images match"

echo "gate 1: tags"
sc_reset; mkscene g1_absent; run_bless g1_absent 0 --mirror
has   "g1 absent: PASS"             "$(row 1)" " PASS "
has   "g1 absent: cut at --apply"   "$(row 1)" "cut at --apply"
has   "g1 names the sigmond commit" "$(row 1)" "${SIG:0:9}"
has   "g1 names the image commit"   "$(row 1)" "${PX:0:9}"
sc_reset; SC_SIG_TAG=remote; mkscene g1_elsewhere; run_bless g1_elsewhere 0 --mirror
has   "g1 elsewhere (sigmond, on origin): FAIL" "$(row 1)" " FAIL "
has   "g1 elsewhere: says so"       "$(row 1)" "tag v3.70 already exists elsewhere in"
has   "g1 elsewhere: names sigmond" "$(row 1)" "$T/g1_elsewhere/sig"
chk   "g1 elsewhere: exits 1"       "$RC" "1"
sc_reset; SC_PX_TAG=local; mkscene g1_pxlocal; run_bless g1_pxlocal 0 --mirror
has   "g1 elsewhere (image repo, local only): FAIL" "$(row 1)" " FAIL "
has   "g1 elsewhere: names the image repo" "$(row 1)" "already exists elsewhere in $T/g1_pxlocal/px"
sc_reset; SC_SIG_TAG=here; SC_PX_TAG=here; mkscene g1_here; run_bless g1_here 0 --mirror
has   "g1 tags already at the commits: PASS" "$(row 1)" " PASS "
lacks "g1 tags already at the commits: not 'cut'" "$(row 1)" "cut at --apply"

echo "gate 4: the release list"
sc_reset; mkscene g4_ok; run_bless g4_ok 0 --mirror
has   "g4 matching list: PASS"      "$(row 4)" " PASS "
sc_reset; SC_LIST_ROW="hs-uploader=$(h40 e)"; mkscene g4_row; run_bless g4_row 0 --mirror
has   "g4 row differs: FAIL"        "$(row 4)" " FAIL "
has   "g4 row differs: names it"    "$(row 4)" "hs-uploader"
chk   "g4 row differs: exits 1"     "$RC" "1"
sc_reset; SC_LIST_VERSION=v3.95; mkscene g4_version; run_bless g4_version 0 --mirror
has   "g4 version: FAIL"            "$(row 4)" " FAIL "
has   "g4 version: names both"      "$(row 4)" "v3.95"

echo "gate 6: Releases"
sc_reset; mkscene g6_sig
STUB_GH_RELEASES="HamSCI/sigmond:v3.70" run_bless g6_sig 0 --mirror
has   "g6 sigmond release exists: FAIL" "$(row 6)" " FAIL "
has   "g6: names HamSCI/sigmond"    "$(row 6)" "HamSCI/sigmond"
sc_reset; mkscene g6_mirror
STUB_GH_RELEASES="HamSCI/sigmond-proxmox:v3.70" run_bless g6_mirror 0 --mirror
has   "g6 mirror release exists with --mirror: FAIL" "$(row 6)" " FAIL "
has   "g6: names the mirror"        "$(row 6)" "HamSCI/sigmond-proxmox"
STUB_GH_RELEASES="HamSCI/sigmond-proxmox:v3.70" run_bless g6_mirror 0 --no-mirror
has   "g6 mirror release exists with --no-mirror: PASS" "$(row 6)" " PASS "
lacks "g6 --no-mirror: the mirror is not asked" "$(cat "$T/g6_mirror/calls")" "sigmond-proxmox"
sc_reset; mkscene g6b; run_bless g6b 0
has   "g6b no choice: FAIL"         "$(row 6b)" " FAIL "
has   "g6b: says when to choose --mirror" "$(row 6b)" "choose --mirror while any station or host runs a pre-v3.94 smd or pm-align"
chk   "g6b: exits 1"                "$RC" "1"
sc_reset; mkscene g6_rename
STUB_GH_REPOS="HamSCI/sigmond HamSCI/sigmond-appliance" run_bless g6_rename 0 --mirror
has   "g6 before the rename: PASS"  "$(row 6)" " PASS "
has   "g6 before the rename: the mirror is the old name" "$(row 6)" "HamSCI/sigmond-appliance"
sc_reset; SC_PX_ORIGIN_URL="https://github.com/HamSCI/sigmond-appliance.git"; mkscene g6_origin
STUB_GH_REPOS="HamSCI/sigmond HamSCI/sigmond-appliance" run_bless g6_origin 0 --mirror
has   "g6 mirror from the checkout's origin: PASS" "$(row 6)" " PASS "
has   "g6 mirror from the checkout's origin: named" "$(row 6)" "HamSCI/sigmond-appliance"
sc_reset; mkscene g6_nomirror
STUB_GH_REPOS="HamSCI/sigmond" run_bless g6_nomirror 0 --mirror
has   "g6 no mirror repo under either name: FAIL" "$(row 6)" " FAIL "

echo "gate 10: the bare-metal record"
sc_reset; SC_RECORDS=""; mkscene g10_none; run_bless g10_none 0 --mirror
has   "g10 none: FAIL"              "$(row 10)" " FAIL "
chk   "g10 none: exits 1"           "$RC" "1"
sc_reset; SC_RECORDS="20261008T100000Z PASS this good
20261008T110000Z FAIL this good"; mkscene g10_later; run_bless g10_later 0 --mirror
has   "g10 later fail: FAIL"        "$(row 10)" " FAIL "
has   "g10 later fail: names the record" "$(row 10)" "v3.70-20261008T110000Z.record"
sc_reset; SC_RECORDS="20261008T100000Z PASS other good"; mkscene g10_other; run_bless g10_other 0 --mirror
has   "g10 other commit: FAIL"      "$(row 10)" " FAIL "
sc_reset; SC_RECORDS="20261008T100000Z PASS this good
20261008T110000Z FAIL other good"; mkscene g10_otherlater; run_bless g10_otherlater 0 --mirror
has   "g10 a newer FAIL for another commit does not matter: PASS" "$(row 10)" " PASS "
sc_reset; SC_DOC_DRIFT=1; mkscene g10_drift; run_bless g10_drift 0 --mirror
has   "g10 digest drift: FAIL"      "$(row 10)" " FAIL "
has   "g10 digest drift: says the doc changed" "$(row 10)" "docs/install-bare-metal.md"
sc_reset; mkscene g10_pass; run_bless g10_pass 0 --mirror
has   "g10 pass: PASS"              "$(row 10)" " PASS "
has   "g10 pass: names the record"  "$(row 10)" "v3.70-20261008T120000Z.record"

echo "--apply --mirror: tags cut at the commits, Releases on both repos"
sc_reset; mkscene apply; run_bless apply 1 --mirror
chk   "the bless exits 0"           "$RC" "0"
chk   "sigmond origin holds v3.70 at the sigmond commit" \
      "$(git -C "$T/apply/sig.git" rev-parse -q --verify 'refs/tags/v3.70^{commit}')" "$SIG"
chk   "image-repo origin holds v3.70 at appliance_commit" \
      "$(git -C "$T/apply/px.git" rev-parse -q --verify 'refs/tags/v3.70^{commit}')" "$PX"
chk   "the sigmond tag is annotated" "$(git -C "$T/apply/sig.git" cat-file -t refs/tags/v3.70)" "tag"
chk   "the image-repo tag is annotated" "$(git -C "$T/apply/px.git" cat-file -t refs/tags/v3.70)" "tag"
CR="$(grep 'release create' "$T/apply/calls")"
has   "release on HamSCI/sigmond"   "$(grep -- '--repo HamSCI/sigmond ' <<<"$CR")" "release create v3.70 --repo HamSCI/sigmond"
has   "  with the manifest"         "$(grep -- '--repo HamSCI/sigmond ' <<<"$CR")" "$STEM.manifest.txt"
has   "  with the .sha256"          "$(grep -- '--repo HamSCI/sigmond ' <<<"$CR")" "$STEM.sha256"
has   "  verifying the tag"         "$(grep -- '--repo HamSCI/sigmond ' <<<"$CR")" "--verify-tag"
has   "release on the mirror"       "$(grep -- '--repo HamSCI/sigmond-proxmox' <<<"$CR")" "release create v3.70 --repo HamSCI/sigmond-proxmox"
has   "  with the manifest"         "$(grep -- '--repo HamSCI/sigmond-proxmox' <<<"$CR")" "$STEM.manifest.txt"
has   "  with the .sha256"          "$(grep -- '--repo HamSCI/sigmond-proxmox' <<<"$CR")" "$STEM.sha256"
n_tag="$(grep -n "tags pushed" <<<"$OUT" | head -1 | cut -d: -f1)"
n_rel="$(grep -n "publishing Release" <<<"$OUT" | head -1 | cut -d: -f1)"
chk   "tags go out before the Release" "$([ -n "$n_tag" ] && [ -n "$n_rel" ] && [ "$n_tag" -lt "$n_rel" ] && echo before || echo "tag=$n_tag rel=$n_rel")" "before"

echo "--apply --no-mirror: one Release"
sc_reset; mkscene apply_nm; run_bless apply_nm 1 --no-mirror
chk   "the bless exits 0"           "$RC" "0"
chk   "one release create"          "$(grep -c 'release create' "$T/apply_nm/calls")" "1"
lacks "none on the mirror"          "$(grep 'release create' "$T/apply_nm/calls")" "sigmond-proxmox"

echo "--apply before the rename: the mirror Release goes to the old name"
sc_reset; mkscene apply_old
STUB_GH_REPOS="HamSCI/sigmond HamSCI/sigmond-appliance" run_bless apply_old 1 --mirror
chk   "the bless exits 0"           "$RC" "0"
has   "release on HamSCI/sigmond-appliance" "$(grep 'release create' "$T/apply_old/calls")" "--repo HamSCI/sigmond-appliance"

echo "--apply with the tags already at the commits: no new tag, Releases created"
sc_reset; SC_SIG_TAG=here; SC_PX_TAG=here; mkscene apply_re; run_bless apply_re 1 --mirror
chk   "the bless exits 0"           "$RC" "0"
chk   "two release creates"         "$(grep -c 'release create' "$T/apply_re/calls")" "2"

echo "--apply blocked by a gate: no tag, no Release"
sc_reset; SC_RECORDS=""; mkscene apply_blocked; run_bless apply_blocked 1 --mirror
chk   "the bless exits 1"           "$RC" "1"
chk   "no sigmond tag"              "$(git -C "$T/apply_blocked/sig.git" tag)" ""
chk   "no image-repo tag"           "$(git -C "$T/apply_blocked/px.git" tag)" ""
chk   "no Release"                  "$(grep -c 'release create' "$T/apply_blocked/calls")" "0"

echo "usage"
sc_reset; mkscene usage; run_bless usage 0 --mirror --no-mirror
chk   "--mirror with --no-mirror is a usage error" "$RC" "2"

echo
printf '%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

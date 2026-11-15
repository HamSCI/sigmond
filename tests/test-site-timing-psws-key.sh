#!/bin/bash
# test-site-timing-psws-key.sh — sigmond-site-timing step 6 fans the portal
# key out to keys/id_ed25519 and to timestd's ~/.ssh/id_rsa_psws.  hsupload
# can write the key directory and timestd owns ~/.ssh, so root must follow no
# link on either side (security review, 2026-11-15): a symlinked or foreign-
# owned source, or a symlinked destination, is refused and nothing is copied.
# The step runs verbatim against stubbed id/runuser/install/chown/usermod/getent.
# No root, no network; seconds.
set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
awk '/^# ── 6\. hs-uploader key ownership/{f=1} f' "$HERE/../bin/sigmond-site-timing" \
    | sed -e "s#/etc/hs-uploader/keys#$T/keys#g" -e "s#/home/timestd#$T/home#g" \
    | sed -e "s#owners = {0}#owners = {0, $(id -u)}#" \
    | grep -v '^exit 0$' > "$T/step.sh"
grep -q 'id_rsa_psws' "$T/step.sh" || { echo "FAIL: step 6 not found in sigmond-site-timing"; exit 1; }
mkdir -p "$T/bin"
printf '#!/bin/bash\nexit 0\n' > "$T/bin/id"
for c in install chown usermod getent; do printf '#!/bin/bash\necho "%s $*" >> %s/calls.log\n' "$c" "$T" > "$T/bin/$c"; done
# runuser -u timestd -- CMD...: run CMD as this user, and log that it was used
printf '#!/bin/bash\necho "runuser $2" >> %s/calls.log\nshift 3\nexec "$@"\n' "$T" > "$T/bin/runuser"
chmod +x "$T/bin/"*

KEY='-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
-----END OPENSSH PRIVATE KEY-----'
reset() {
    rm -rf "$T/keys" "$T/home" "$T/outside" "$T/calls.log" "$T/say.log"
    mkdir -p "$T/keys" "$T/home" "$T/outside"
    printf '%s\n' "$KEY" > "$T/keys/id_ed25519_host"
    printf 'ssh-ed25519 AAAAC3Nz test@b4\n' > "$T/keys/id_ed25519_host.pub"
}
run() { PATH="$T/bin:$PATH" bash -c "say(){ echo \"\$*\" >> $T/say.log; }; source $T/step.sh"; }
check() {  # check <name> <condition...>
    local name=$1; shift
    if "$@"; then echo "  ok   $name"; else echo "  FAIL $name"; cat "$T/say.log" 2>/dev/null | sed 's/^/       /'; fail=1; fi
}

reset; run
check "fresh: key written, mode 600, dir 700" \
    bash -c "cmp -s $T/keys/id_ed25519_host $T/home/.ssh/id_rsa_psws && [ \$(stat -c %a $T/home/.ssh/id_rsa_psws) = 600 ] && [ \$(stat -c %a $T/home/.ssh) = 700 ]"
check "fresh: written through runuser as timestd, never root install" \
    bash -c "grep -q 'runuser timestd' $T/calls.log && ! grep -q 'install.*id_rsa_psws' $T/calls.log"

reset; ln -s "$T/outside" "$T/home/.ssh"; run
check "symlinked .ssh: refused, nothing written outside" \
    bash -c "[ -z \"\$(ls -A $T/outside)\" ] && grep -q 'is a symlink - refused' $T/say.log"

reset; mkdir -p "$T/home/.ssh"; ln -s "$T/outside/stolen" "$T/home/.ssh/id_rsa_psws"; run
check "dangling symlinked target: refused, nothing written outside" \
    bash -c "[ ! -e $T/outside/stolen ] && grep -q 'id_rsa_psws is a symlink - refused' $T/say.log"

reset; mkdir -p "$T/home/.ssh"; printf 'x\n' > "$T/outside/f"; ln -s "$T/outside/f" "$T/home/.ssh/id_rsa_psws"; run
check "symlinked target to a file: refused, file untouched" \
    bash -c "[ \"\$(cat $T/outside/f)\" = x ] && grep -q 'refused' $T/say.log"

reset; mkdir -p "$T/home/.ssh"; printf 'MINE\n' > "$T/home/.ssh/id_rsa_psws"; run
check "existing key: left as it is" bash -c "[ \"\$(cat $T/home/.ssh/id_rsa_psws)\" = MINE ]"

# --- the source side: /etc/hs-uploader/keys is hsupload-writable ---
reset; run
check "fresh: hs-uploader copy written, 600, pub 644" \
    bash -c "cmp -s $T/keys/id_ed25519_host $T/keys/id_ed25519 && [ \$(stat -c %a $T/keys/id_ed25519) = 600 ] && [ \$(stat -c %a $T/keys/id_ed25519.pub) = 644 ] && grep -q 'runuser hsupload' $T/calls.log"

reset; printf 'SECRET\n' > "$T/outside/secret"; rm "$T/keys/id_ed25519_host"; ln -s "$T/outside/secret" "$T/keys/id_ed25519_host"; run
check "symlinked source: refused, nothing copied anywhere" \
    bash -c "[ ! -e $T/keys/id_ed25519 ] && [ ! -e $T/home/.ssh/id_rsa_psws ] && grep -q 'id_ed25519_host is a symlink - refused' $T/say.log"

reset; ln -s "$T/outside/planted" "$T/keys/id_ed25519"; run
check "symlinked hs-uploader destination: refused, nothing written outside" \
    bash -c "[ ! -e $T/outside/planted ] && grep -q 'keys/id_ed25519 is a symlink - refused' $T/say.log"

reset; ln "$T/keys/id_ed25519_host" "$T/outside/hardlink"; run
check "hard-linked source: refused, nothing copied" \
    bash -c "[ ! -e $T/keys/id_ed25519 ] && [ ! -e $T/home/.ssh/id_rsa_psws ] && grep -q 'hard links - refused' $T/say.log"

reset; printf 'root:x:0:0::/root:/bin/bash\n' > "$T/keys/id_ed25519_host"; run
check "source that is not an OpenSSH private key: refused, nothing copied" \
    bash -c "[ ! -e $T/keys/id_ed25519 ] && [ ! -e $T/home/.ssh/id_rsa_psws ] && grep -q 'not an OpenSSH private key - refused' $T/say.log"

# A source owned by neither root nor hsupload (here: by the test user, with
# only root allowed) is refused.
reset; sed "s#owners = {0, $(id -u)}#owners = {0}#" "$T/step.sh" > "$T/step-strict.sh"
PATH="$T/bin:$PATH" bash -c "say(){ echo \"\$*\" >> $T/say.log; }; source $T/step-strict.sh"
check "source owned by another user: refused, nothing copied" \
    bash -c "[ ! -e $T/keys/id_ed25519 ] && [ ! -e $T/home/.ssh/id_rsa_psws ] && grep -q 'not root or hsupload - refused' $T/say.log"

[ $fail = 0 ] && echo "PASS: site-timing step 6 (PSWS key)" || { echo "FAIL: site-timing step 6"; exit 1; }

#!/usr/bin/env bash
# Publish the station SSH keys that may drop heartbeats on the server.
#
# Run FROM THE DEVBOX, as yourself, with NO root:
#     ./authorize-stations.sh <pubkey-dir> [roster.json] [user@host]
#
# <pubkey-dir> holds harvested public keys named <station>.pub, where
# <station> is the station's roster name — the same string that appears
# in its heartbeat envelopes and in the drop filenames.
#
# The authorized_keys file is REGENERATED WHOLE, never appended to: an
# append-only key file is how a decommissioned host keeps its access for
# years.  Whatever is on the roster today is what can write tomorrow.
#
# A .pub whose station is not on the roster is a HARD ERROR, not a
# warning and not a silent skip.  Authorizing an undeclared host would
# put a station into the drop that the board never renders a row for —
# it would upload happily and be invisible, the exact shape of the
# defects this feature exists to surface.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEYDIR=${1:-}
ROSTER=${2:-}
DEST=${3:-${FLEETBOARD_DEST:-wsprdaemon@wd30}}
KEYS_FILE=${KEYS_FILE:-/etc/ssh/authorized_keys.d/hamsci-hb}

say() { printf '  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die() { printf '\nauthorize-stations.sh: %s\n' "$*" >&2; exit 1; }

[[ -n "$KEYDIR" ]] || die "usage: $0 <pubkey-dir> [roster.json] [user@host]"
[[ -d "$KEYDIR" ]] || die "not a directory: $KEYDIR"

if [[ -z "$ROSTER" ]]; then
    ROSTER="$HERE/roster.json"
    if [[ ! -f "$ROSTER" ]]; then
        WORK=$(mktemp -d)
        trap 'rm -rf "$WORK"' EXIT
        ROSTER="$WORK/roster.json"
        REPO="$(cd "$HERE/../.." && pwd)"
        export SIGMOND_FLEET=${SIGMOND_FLEET:-$HOME/hamsci/ops/fleet.toml}
        PYTHONPATH="$REPO/lib" "$REPO/bin/smd" fleet roster --json \
            --profile "${PROFILE:-dasi2}" > "$ROSTER" \
            || die "smd fleet roster failed and no roster.json was given"
    fi
fi

step "Roster: $ROSTER"
python3 "$HERE/roster_check.py" --check "$ROSTER" \
    || die "refusing to authorize anyone against an unusable roster"
mapfile -t STATIONS < <(python3 "$HERE/roster_check.py" --names "$ROSTER")
say "${#STATIONS[@]} station(s): ${STATIONS[*]}"

on_roster() {
    local candidate=$1 name
    for name in "${STATIONS[@]}"; do
        [[ "$name" == "$candidate" ]] && return 0
    done
    return 1
}

step "Reading public keys from $KEYDIR"
shopt -s nullglob
PUBS=("$KEYDIR"/*.pub)
shopt -u nullglob
[[ ${#PUBS[@]} -gt 0 ]] || die "no *.pub files in $KEYDIR"

declare -A KEY_OF=()
STRANGERS=()
for pub in "${PUBS[@]}"; do
    station=$(basename "$pub" .pub)
    if ! on_roster "$station"; then
        STRANGERS+=("$station ($pub)")
        continue
    fi
    # Field 1+2 only (type + base64): the .pub's trailing comment is
    # usually user@hostname, and site topology must not travel to a
    # server-side file that other people read.
    keytype=$(awk 'NF>=2 {print $1; exit}' "$pub")
    keydata=$(awk 'NF>=2 {print $2; exit}' "$pub")
    [[ -n "$keytype" && -n "$keydata" ]] \
        || die "$pub does not look like an OpenSSH public key"
    case "$keytype" in
        ssh-ed25519|ssh-rsa|ecdsa-sha2-*|sk-ssh-ed25519@*|sk-ecdsa-sha2-*) ;;
        *) die "$pub: unrecognised key type '$keytype'" ;;
    esac
    KEY_OF["$station"]="$keytype $keydata"
    say "$station: $keytype (${#keydata} chars)"
done

if [[ ${#STRANGERS[@]} -gt 0 ]]; then
    printf '\n' >&2
    for stranger in "${STRANGERS[@]}"; do
        echo "  NOT ON THE ROSTER: $stranger" >&2
    done
    die "${#STRANGERS[@]} key(s) belong to stations that are not on the roster.
Add them to the fleet inventory (ops/fleet.toml) first, or remove the
.pub files. A key is never authorized silently."
fi

step "Building the authorized-keys content"
CONTENT=""
MISSING=()
for station in "${STATIONS[@]}"; do
    if [[ -n "${KEY_OF[$station]:-}" ]]; then
        # 'restrict' turns off every forwarding/pty/agent feature; the
        # sshd Match block's ForceCommand internal-sftp does the rest.
        CONTENT+="restrict ${KEY_OF[$station]} # station=$station"$'\n'
    else
        MISSING+=("$station")
    fi
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    say "no key yet for: ${MISSING[*]}"
    say "(they will keep failing to upload until a key is harvested)"
fi
[[ -n "$CONTENT" ]] \
    || die "result is empty — refusing to publish a key file that authorizes
nobody (that would silence the whole fleet while reporting success)"
lines=$(printf '%s' "$CONTENT" | grep -c '^restrict ' || true)
say "$lines authorized station key(s)"

step "Publishing to $DEST:$KEYS_FILE"
header="# Managed by sigmond server/heartbeat/authorize-stations.sh."$'\n'
header+="# Regenerated whole from the fleet roster — do not hand-edit."$'\n'
if printf '%s%s' "$header" "$CONTENT" \
        | ssh "$DEST" "sudo -n tee $KEYS_FILE >/dev/null \
&& sudo -n chown root:root $KEYS_FILE && sudo -n chmod 644 $KEYS_FILE"; then
    say "published $lines key(s)"
else
    cat <<EOF

  ** NOTHING WAS PUBLISHED. ** Passwordless sudo was unavailable on $DEST.
  Run this on $DEST and paste the content below into $KEYS_FILE:

      sudo install -o root -g root -m 644 /dev/null $KEYS_FILE
      sudo nano $KEYS_FILE

  --- begin $KEYS_FILE ---
$header$CONTENT  --- end ---

EOF
    exit 1
fi

step "Done"
say "Verify from a station: sftp hamsci-hb@<server> then 'cd incoming'"

#!/usr/bin/env bash
# One-time setup of the heartbeat drop + fleet board on the CENTRAL SERVER.
#
# Run ON wd30, WITH sudo:   sudo ./setup-wd30.sh
#
# Idempotent by construction: every step checks before it acts and says
# what it did, so re-running after a partial failure is the normal repair
# and never a second, divergent configuration.
#
# What this builds, and why each piece is shaped the way it is:
#
#   * a nologin user (hamsci-hb) that stations authenticate as.  It owns
#     no shell and no login: the only thing it can do is drop files.
#   * a chroot whose ROOT is owned by root and NOT writable by that user
#     (an sshd hard requirement, and the reason incoming/ is a subdir).
#   * authorized_keys OUTSIDE the chroot, so the account that is confined
#     by the chroot can never edit the list of keys that confines it.
#   * a separate state dir owned by the operator account, because the
#     ingest and board services must never run as root over content that
#     arrived from the network.
set -euo pipefail

DROP_ROOT=${DROP_ROOT:-/srv/hamsci-hb}
INCOMING="$DROP_ROOT/incoming"
QUARANTINE="$DROP_ROOT/quarantine"
HB_USER=${HB_USER:-hamsci-hb}
OPERATOR=${OPERATOR:-wsprdaemon}
STATE_DIR=${STATE_DIR:-/var/lib/hamsci-fleetboard}
APP_DIR=${APP_DIR:-/opt/hamsci-fleetboard}
KEYS_DIR=/etc/ssh/authorized_keys.d
KEYS_FILE="$KEYS_DIR/$HB_USER"
SSHD_SNIPPET=/etc/ssh/sshd_config.d/hamsci-hb.conf
UNIT_SRC=${UNIT_SRC:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/units"}
UNIT_DIR=/etc/systemd/system

say() { printf '  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "setup-wd30.sh must run as root (use sudo)" >&2
    exit 1
fi

step "SFTP account: $HB_USER"
if id "$HB_USER" >/dev/null 2>&1; then
    say "user $HB_USER already exists — left alone"
else
    useradd --system --shell /usr/sbin/nologin --home-dir "$DROP_ROOT" \
            --no-create-home "$HB_USER"
    say "created system user $HB_USER (nologin, no home of its own)"
fi

step "Drop directories under $DROP_ROOT"
# The chroot root MUST be root-owned and non-group/other-writable or sshd
# refuses the session with a message that names the directory but not the
# rule; the writable part has to be a subdirectory.
install -d -o root -g root -m 755 "$DROP_ROOT"
say "chroot root $DROP_ROOT root:root 755"
install -d -o "$HB_USER" -g "$HB_USER" -m 750 "$INCOMING"
say "drop $INCOMING $HB_USER:$HB_USER 750"
install -d -o "$HB_USER" -g "$HB_USER" -m 750 "$QUARANTINE"
say "quarantine $QUARANTINE $HB_USER:$HB_USER 750"

# The ingest service runs as the operator and must be able to read the
# drop, move rejects into quarantine and unlink what it ingested.  Both
# units hardcode User=$OPERATOR, so a missing account is a broken install,
# not a warning to scroll past.
if ! id "$OPERATOR" >/dev/null 2>&1; then
    echo "operator account '$OPERATOR' does not exist on this host." >&2
    echo "Create it, or re-run with OPERATOR=<account> and edit the two" >&2
    echo "units' User= lines to match." >&2
    exit 1
fi
usermod -a -G "$HB_USER" "$OPERATOR"
chmod 770 "$INCOMING" "$QUARANTINE"
say "added $OPERATOR to group $HB_USER; drop dirs group-writable (770)"

step "Authorized keys OUTSIDE the chroot"
install -d -o root -g root -m 755 "$KEYS_DIR"
if [[ -e "$KEYS_FILE" ]]; then
    say "$KEYS_FILE already exists — left alone (authorize-stations.sh owns it)"
else
    install -o root -g root -m 644 /dev/null "$KEYS_FILE"
    say "created empty $KEYS_FILE root:root 644 (no station authorized yet)"
fi

step "sshd snippet $SSHD_SNIPPET"
install -d -o root -g root -m 755 /etc/ssh/sshd_config.d
snippet=$(cat <<EOF
# Managed by sigmond server/heartbeat/setup-wd30.sh — do not hand-edit.
# Station heartbeat drop: SFTP only, chrooted, no shell, no forwarding.
Match User $HB_USER
    ForceCommand internal-sftp
    ChrootDirectory $DROP_ROOT
    DisableForwarding yes
    PermitTTY no
    AuthorizedKeysFile $KEYS_FILE
EOF
)
if [[ -f "$SSHD_SNIPPET" ]] && [[ "$(cat "$SSHD_SNIPPET")" == "$snippet" ]]; then
    say "already up to date — sshd not touched"
else
    printf '%s\n' "$snippet" > "$SSHD_SNIPPET.new"
    chown root:root "$SSHD_SNIPPET.new"
    chmod 644 "$SSHD_SNIPPET.new"
    mv "$SSHD_SNIPPET.new" "$SSHD_SNIPPET"
    say "wrote $SSHD_SNIPPET"
    # Validate BEFORE reload: a rejected config on reload leaves the running
    # sshd up but the next restart locks everyone out of the server.
    if sshd -t; then
        say "sshd -t passed"
        systemctl reload ssh 2>/dev/null || systemctl reload sshd
        say "reloaded sshd"
    else
        echo "sshd -t FAILED — snippet left in place but sshd NOT reloaded;" >&2
        echo "fix $SSHD_SNIPPET before the next sshd restart" >&2
        exit 1
    fi
fi

step "Fleetboard state + application directories"
install -d -o "$OPERATOR" -g "$OPERATOR" -m 755 "$STATE_DIR"
say "$STATE_DIR owned by $OPERATOR (holds heartbeats.db)"
install -d -o "$OPERATOR" -g "$OPERATOR" -m 755 "$APP_DIR"
say "$APP_DIR owned by $OPERATOR (deploy-wd30.sh rsyncs into it)"

step "systemd units"
for unit in hamsci-hb-ingest.service hamsci-hb-ingest.timer \
            hamsci-fleetboard.service; do
    if [[ ! -f "$UNIT_SRC/$unit" ]]; then
        echo "missing unit source $UNIT_SRC/$unit" >&2
        exit 1
    fi
    install -o root -g root -m 644 "$UNIT_SRC/$unit" "$UNIT_DIR/$unit"
    say "installed $UNIT_DIR/$unit"
done
systemctl daemon-reload
say "daemon-reload done"

systemctl enable --now hamsci-hb-ingest.timer
say "enabled hamsci-hb-ingest.timer"
# The board is enabled but only STARTED once code is deployed; starting it
# now would crash-loop on a missing roster.json and bury the real error.
systemctl enable hamsci-fleetboard.service
say "enabled hamsci-fleetboard.service (start it after the first deploy)"

step "Done"
say "Next, from the devbox:"
say "  ./authorize-stations.sh <pubkey-dir>   # publish station keys"
say "  ./deploy-wd30.sh                       # ship code + roster, restart"
say "Check the bind address in $UNIT_DIR/hamsci-fleetboard.service first."

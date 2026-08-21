#!/usr/bin/env bash
# Provision the pm-heartbeat emitter ON a Proxmox host (a PM), as root.
#
# Run ON the PM, WITH sudo:
#   sudo ./pm-heartbeat-setup.sh --station b4-pm --vmid 100 --cohort dasi2
#
# Idempotent by construction: every step checks before it acts and says
# what it did, so re-running after a partial failure is the normal repair
# and never a second, divergent configuration (see
# server/heartbeat/setup-wd30.sh for the same idiom on the server side).
#
# A PM runs no sigmond, no venv, no pip — this script only ever touches
# /etc/pm-heartbeat/, /usr/local/sbin/pm-heartbeat.py and two systemd
# units. It never echoes the PRIVATE half of the ed25519 keypair it
# generates; only the PUBLIC key is printed, for the operator to hand to
# server/heartbeat/authorize-stations.sh.
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CONFIG_DIR=/etc/pm-heartbeat
CONFIG_FILE="$CONFIG_DIR/config.toml"
KEY_PATH="$CONFIG_DIR/id_ed25519"
INSTALL_DIR=/usr/local/sbin
UNIT_DIR=/etc/systemd/system

STATION=""
VMID=""
COHORT=""
DEST_HOST=""
DEST_PORT=""
EXPECT_CAT=false
PRINT_CONFIG=false

say() { printf '  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

usage() {
    cat <<'EOF'
Usage: pm-heartbeat-setup.sh --station NAME --vmid N [options]

Required:
  --station NAME          roster station name for this PM (e.g. b4-pm)
  --vmid N                 VMID of the sigmond guest this PM hosts

Optional:
  --cohort dasi2|public     destination preset (see below); with neither
                            this nor --dest-host, setup refuses to run
  --dest-host HOST          overrides the cohort preset's host
  --dest-port PORT          overrides the cohort preset's port
  --expect-cat               require /sys/fs/resctrl/radiod to exist
                            (doctor block reports cat-groups-missing when
                            it does not); default off
  --print-config             print the config.toml this run WOULD write
                            and exit 0 — no side effects, no root needed
  -h, --help                  show this help

Cohort presets — subsets of the fleet report to different servers,
mirroring the RAC split:
  dasi2    -> wd30.wsprdaemon.org:38222
              The DASI2-grant fleet server today; will move to the
              vpn.hamsci.org infrastructure once that hosts the
              fleetboard.
  public   -> wsprdaemon.org:38222
              Placeholder default: self-funded / independent sites run
              their OWN fleetboard and should pass --dest-host
              explicitly. This preset is a documented default, not a
              live service.

Explicit --dest-host/--dest-port always override the preset.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --station) STATION="${2:-}"; shift 2 ;;
        --vmid) VMID="${2:-}"; shift 2 ;;
        --cohort) COHORT="${2:-}"; shift 2 ;;
        --dest-host) DEST_HOST="${2:-}"; shift 2 ;;
        --dest-port) DEST_PORT="${2:-}"; shift 2 ;;
        --expect-cat) EXPECT_CAT=true; shift ;;
        --print-config) PRINT_CONFIG=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

case "$COHORT" in
    dasi2)
        : "${DEST_HOST:=wd30.wsprdaemon.org}"
        : "${DEST_PORT:=38222}"
        ;;
    public)
        : "${DEST_HOST:=wsprdaemon.org}"
        : "${DEST_PORT:=38222}"
        ;;
    "") ;;  # no preset — DEST_HOST must have come from --dest-host
    *)
        echo "unknown --cohort '$COHORT' (expected dasi2 or public)" >&2
        exit 1
        ;;
esac
DEST_PORT="${DEST_PORT:-22}"

if [[ -z "$STATION" ]]; then
    echo "pm-heartbeat-setup.sh: --station is required" >&2
    exit 2
fi
if [[ -z "$VMID" ]]; then
    echo "pm-heartbeat-setup.sh: --vmid is required" >&2
    exit 2
fi
if [[ -z "$DEST_HOST" ]]; then
    echo "pm-heartbeat-setup.sh: no destination — pass --cohort dasi2|public" \
         "or --dest-host explicitly" >&2
    exit 2
fi

render_config() {
    cat <<EOF
station = "$STATION"
vmid = $VMID
dest_host = "$DEST_HOST"
dest_port = $DEST_PORT
sftp_user = "hamsci-hb"
remote_path = "incoming"
interval_sec = 300
key_path = "$KEY_PATH"
expect_cat = $EXPECT_CAT
EOF
}

# --print-config is a pure dry-run: no root, no writes, no side effects
# at all — everything above this point is argument parsing only.
if [[ "$PRINT_CONFIG" == true ]]; then
    render_config
    exit 0
fi

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "pm-heartbeat-setup.sh must run as root (use sudo)" >&2
    exit 1
fi

step "Config directory $CONFIG_DIR"
install -d -o root -g root -m 755 "$CONFIG_DIR"
say "$CONFIG_DIR root:root 755"

step "SSH keypair"
if [[ -f "$KEY_PATH" ]]; then
    say "$KEY_PATH already exists — left alone"
else
    ssh-keygen -t ed25519 -N "" -C "pm-heartbeat@$STATION" -f "$KEY_PATH" -q
    chmod 600 "$KEY_PATH"
    chmod 644 "$KEY_PATH.pub"
    say "generated $KEY_PATH"
    # The private key NEVER gets echoed anywhere in this script — only
    # the public half is printed, at the end, for the operator to carry
    # to the fleetboard devbox and authorize via
    # server/heartbeat/authorize-stations.sh under this station's roster
    # name.
fi

step "config.toml"
if [[ -f "$CONFIG_FILE" ]]; then
    backup="$CONFIG_FILE.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    cp "$CONFIG_FILE" "$backup"
    say "backed up existing $CONFIG_FILE -> $backup"
fi
render_config > "$CONFIG_FILE.new"
chown root:root "$CONFIG_FILE.new"
chmod 644 "$CONFIG_FILE.new"
mv "$CONFIG_FILE.new" "$CONFIG_FILE"
say "wrote $CONFIG_FILE (dest $DEST_HOST:$DEST_PORT, expect_cat=$EXPECT_CAT)"

step "pm-heartbeat.py"
install -o root -g root -m 755 "$SELF_DIR/pm-heartbeat.py" \
    "$INSTALL_DIR/pm-heartbeat.py"
say "installed $INSTALL_DIR/pm-heartbeat.py"

step "systemd units"
for unit in pm-heartbeat.service pm-heartbeat.timer; do
    if [[ ! -f "$SELF_DIR/$unit" ]]; then
        echo "missing unit source $SELF_DIR/$unit" >&2
        exit 1
    fi
    install -o root -g root -m 644 "$SELF_DIR/$unit" "$UNIT_DIR/$unit"
    say "installed $UNIT_DIR/$unit"
done
systemctl daemon-reload
say "daemon-reload done"

# Config is guaranteed complete by this point — station/vmid/dest-host
# were all validated above before anything was written — so enabling the
# timer here is safe; a config missing a required key never reaches this
# line.
step "Enabling pm-heartbeat.timer"
systemctl enable --now pm-heartbeat.timer
say "enabled + started (first tick within one interval + up to 45s jitter)"

step "Done"
say "Pubkey to authorize on the fleetboard server (roster name '$STATION'):"
echo
cat "$KEY_PATH.pub"
echo
say "Next steps:"
say "  1. Copy the pubkey above to the fleetboard devbox."
say "  2. Run server/heartbeat/authorize-stations.sh there under name '$STATION'."
say "  3. Watch $CONFIG_DIR and /var/lib/pm-heartbeat/spool/ drain within"
say "     one interval of authorization landing."

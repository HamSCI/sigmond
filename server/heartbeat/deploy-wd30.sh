#!/usr/bin/env bash
# Ship the fleetboard code + a FRESH roster to the central server.
#
# Run FROM THE DEVBOX, as yourself, with NO root:
#     ./deploy-wd30.sh [user@host]
#
# Repeatable by design — this is the only supported way to change what
# runs on the server.  Nothing here edits a file on wd30 in place; the
# repo is the source of truth and this script is the transport.
#
# The roster is REGENERATED on every deploy rather than shipped from a
# checked-in file.  A stale roster is the one failure this whole feature
# cannot survive: the board derives absence by comparing arrivals against
# the roster, so a station missing from it is simply never asked about —
# it disappears silently instead of going red, which is precisely the
# defect class the fleet-awareness work exists to remove.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DEST=${1:-${FLEETBOARD_DEST:-wsprdaemon@wd30}}
APP_DIR=${APP_DIR:-/opt/hamsci-fleetboard}
PROFILE=${PROFILE:-dasi2}
SIGMOND_FLEET=${SIGMOND_FLEET:-$HOME/hamsci/ops/fleet.toml}
export SIGMOND_FLEET

say() { printf '  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die() { printf '\ndeploy-wd30.sh: %s\n' "$*" >&2; exit 1; }

step "Preflight"
[[ -r "$SIGMOND_FLEET" ]] || die "fleet inventory not readable: $SIGMOND_FLEET
Set SIGMOND_FLEET to your ops/fleet.toml."
say "fleet inventory: $SIGMOND_FLEET"
say "destination:     $DEST:$APP_DIR"
say "profile:         $PROFILE"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
ROSTER="$WORK/roster.json"

step "Generating roster.json fresh from the inventory"
if ! PYTHONPATH="$REPO/lib" "$REPO/bin/smd" fleet roster --json \
        --profile "$PROFILE" > "$ROSTER"; then
    die "smd fleet roster failed — refusing to deploy a board with no roster"
fi
# Fails CLOSED on [] / non-list / unparseable — see roster_check.py.
if ! /usr/bin/env python3 "$HERE/roster_check.py" --check "$ROSTER"; then
    die "roster is empty or malformed — refusing to deploy.
An empty roster renders a board that watches nothing and reports success."
fi
say "$(/usr/bin/env python3 "$HERE/roster_check.py" --names "$ROSTER" \
        | tr '\n' ' ')"

step "Rsyncing to $DEST:$APP_DIR"
# heartbeat_schema.py is the ONE file shared verbatim with the stations;
# it lands flat beside the server code and is imported as a sibling.
rsync -av --checksum \
    "$HERE/ingest.py" \
    "$HERE/fleetboard.py" \
    "$HERE/roster_check.py" \
    "$REPO/lib/sigmond/heartbeat_schema.py" \
    "$ROSTER" \
    "$DEST:$APP_DIR/" \
    || die "rsync failed — is $APP_DIR present and owned by you? (run setup-wd30.sh on the server first)"

step "Restarting services"
# sudo -n only: an interactive password prompt over a scripted ssh hangs
# the deploy with no output.  If it is not available we say exactly what
# to run by hand rather than pretending the deploy finished.
restart_cmd="sudo -n systemctl restart hamsci-fleetboard.service && \
sudo -n systemctl restart hamsci-hb-ingest.timer && \
sudo -n systemctl start hamsci-hb-ingest.service"
if ssh "$DEST" "$restart_cmd"; then
    say "hamsci-fleetboard restarted; ingest timer restarted and run once"
    ssh "$DEST" "systemctl is-active hamsci-fleetboard.service \
hamsci-hb-ingest.timer" || true
else
    cat <<EOF

  ** CODE IS DEPLOYED BUT THE SERVICES WERE NOT RESTARTED. **
  Passwordless sudo was unavailable on $DEST. Run these there:

      sudo systemctl restart hamsci-fleetboard.service
      sudo systemctl restart hamsci-hb-ingest.timer
      sudo systemctl start   hamsci-hb-ingest.service
      systemctl status hamsci-fleetboard.service

EOF
    exit 1
fi

step "Done"
say "Board: check the --bind address in hamsci-fleetboard.service on $DEST"

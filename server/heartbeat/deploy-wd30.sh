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
ROSTER_DASI2="$WORK/roster-dasi2.json"
ROSTER_PM="$WORK/roster-dasi2-pm.json"
ROSTER="$WORK/roster.json"
PM_PROFILE=${PM_PROFILE:-${PROFILE}-pm}

step "Generating roster.json fresh from the inventory ($PROFILE + $PM_PROFILE)"
if ! PYTHONPATH="$REPO/lib" "$REPO/bin/smd" fleet roster --json \
        --profile "$PROFILE" > "$ROSTER_DASI2"; then
    die "smd fleet roster --profile $PROFILE failed — refusing to deploy a board with no roster"
fi
if ! PYTHONPATH="$REPO/lib" "$REPO/bin/smd" fleet roster --json \
        --profile "$PM_PROFILE" > "$ROSTER_PM"; then
    die "smd fleet roster --profile $PM_PROFILE failed — refusing to deploy a board with no roster"
fi
# roster.json = union of both halves.  $PROFILE must be non-empty;
# $PM_PROFILE may legitimately be empty (no PM hosts declared yet) — see
# roster_check.merge_rosters's docstring. Fails LOUDLY and says which
# half was the problem.
if ! /usr/bin/env python3 -c "
import json, sys
sys.path.insert(0, '$HERE')
import roster_check

with open('$ROSTER_DASI2') as f:
    dasi2 = json.load(f)
with open('$ROSTER_PM') as f:
    pm = json.load(f)

try:
    merged = roster_check.merge_rosters(dasi2, pm)
except ValueError as exc:
    sys.stderr.write(f'roster merge: {exc}\n')
    sys.exit(1)

with open('$ROSTER', 'w') as f:
    json.dump(merged, f, indent=2)
"; then
    die "roster merge failed — refusing to deploy a board with a bad roster."
fi
# Fails CLOSED on [] / non-list / unparseable — see roster_check.py.
if ! /usr/bin/env python3 "$HERE/roster_check.py" --check "$ROSTER"; then
    die "merged roster is empty or malformed — refusing to deploy.
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

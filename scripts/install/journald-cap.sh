#!/bin/bash
# journald-cap.sh: give the journal an explicit size cap (v3.73 row 18).
#
# The appliance image bakes the same cap (sigmond-appliance
# provision-components.sh).  A station that updates instead of reflashing
# never ran that, so sigmond's install.sh runs this.  On AC0G-B4
# timestd-fusion once wrote 124,000 lines an hour; the journal reached 3.9G
# and dropped 10-15 13:33Z..10-16 23:41Z.
#
# Idempotent: it compares settings, not comments, so the image's own copy
# (same settings, with a comment header) stays as it is.  It restarts
# systemd-journald only when it wrote the file.  Run as root.
# Overrides, for tests: JOURNALD_CONF_DIR, SYSTEMCTL.
set -u
dir="${JOURNALD_CONF_DIR:-/etc/systemd/journald.conf.d}"
conf="$dir/50-sigmond-journal-size.conf"
want=$'[Journal]\nSystemMaxUse=4G'

have=""
if [[ -f "$conf" ]]; then
    have="$(grep -vE '^[[:space:]]*([#;]|$)' "$conf")"
fi
if [[ "$have" == "$want" ]]; then
    echo "journald cap already in place: SystemMaxUse=4G"
    exit 0
fi

mkdir -p "$dir" \
    && printf '%s\n' "$want" > "$conf.tmp" \
    && chmod 0644 "$conf.tmp" \
    && mv -f "$conf.tmp" "$conf" \
    || { echo "cannot write $conf" >&2; rm -f "$conf.tmp"; exit 1; }
if ! "${SYSTEMCTL:-systemctl}" restart systemd-journald; then
    echo "wrote $conf, but systemd-journald did not restart" >&2
    exit 1
fi
echo "journald cap written: SystemMaxUse=4G (systemd-journald restarted)"

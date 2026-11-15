#!/bin/bash
# site-timing-link.sh: point /usr/local/sbin/sigmond-site-timing at this
# checkout's bin/sigmond-site-timing.
#
# sigmond-site-timing lived in sigmond-appliance until 2026-11-15.  The
# wizard base64-copied it into the VM at install, and nothing refreshed it
# after that, so the 11-14 fixes (T4 wait, SUDO_*, T6 from `ts1 trust`)
# reached no running station.  With a link, every pull of sigmond carries
# the current script.
#
# A plain file at the destination is a copy from an older install.  The
# first time, this script keeps it as <dest>.pre-sigmond-link; after that it
# replaces any plain file without a second backup.  A link already pointing
# here is left alone.  Prints one line saying what it did.  Run as root.
#
# Callers: install.sh, `smd component update sigmond`, `smd align --apply`,
# and the Proxmox wizard (in the VM).
# Overrides, for tests: SITE_TIMING_SRC, SITE_TIMING_DEST.
set -u
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)"
src="${SITE_TIMING_SRC:-$here/bin/sigmond-site-timing}"
dest="${SITE_TIMING_DEST:-/usr/local/sbin/sigmond-site-timing}"
backup="$dest.pre-sigmond-link"

if [ ! -f "$src" ]; then
    echo "site-timing-link: $src is missing - nothing linked" >&2
    exit 1
fi
[ -x "$src" ] || chmod 0755 "$src" || exit 1

if [ -L "$dest" ]; then
    if [ "$(readlink "$dest")" = "$src" ]; then
        echo "site-timing-link: current ($dest -> $src)"
        exit 0
    fi
    rm -f "$dest" || exit 1
    did="relinked"
elif [ -e "$dest" ]; then
    if [ -e "$backup" ] || [ -L "$backup" ]; then
        rm -f "$dest" || exit 1
        did="replaced a plain file (earlier backup kept at $backup)"
    else
        mv "$dest" "$backup" || exit 1
        did="replaced a plain file (kept as $backup)"
    fi
else
    did="linked"
fi
mkdir -p "$(dirname "$dest")" || exit 1
ln -s "$src" "$dest" || exit 1
echo "site-timing-link: $did: $dest -> $src"

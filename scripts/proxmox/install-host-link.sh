#!/bin/bash
# install-host-link.sh — put sigmond-link on a Proxmox HOST, so the host's own
# remote-access tunnels (sigmond-rac-host@*) pause during scheduled satellite
# gaps.  The host fetches its own schedule: host RAC exists for when the VM
# is down, so it must not depend on the VM.
#
# Run from a sigmond checkout, as root on the host, or push it there:
#   tar -C <sigmond> -cf - bin/sigmond-link systemd/sigmond-link*.service \
#       systemd/sigmond-link*.timer scripts/proxmox/install-host-link.sh \
#     | ssh root@HOST 'mkdir -p /tmp/sl && tar -C /tmp/sl -xf - && bash /tmp/sl/scripts/proxmox/install-host-link.sh'
#
# Inert until /etc/sigmond/link.toml says enabled = true.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/../.." && pwd)"

install -m 0755 "$SRC/bin/sigmond-link" /usr/local/sbin/sigmond-link
for u in sigmond-link.service sigmond-link.timer sigmond-link-rac.service sigmond-link-rac.timer; do
    install -m 0644 "$SRC/systemd/$u" "/etc/systemd/system/$u"
done
command -v pdftotext >/dev/null 2>&1 \
    || DEBIAN_FRONTEND=noninteractive apt-get install -y -q poppler-utils >/dev/null 2>&1 \
    || echo "WARNING: poppler-utils not installed: sigmond-link cannot read the USAP schedule" >&2
mkdir -p /etc/sigmond
if [[ ! -f /etc/sigmond/link.toml ]]; then
    cat > /etc/sigmond/link.toml <<'EOF'
# sigmond-link on this Proxmox host (the VM reads its own topology.toml).
# See `sigmond-link --help` and HamSCI/sigmond tasks/plan-constrained-link-delivery.md.
[link_schedule]
enabled = false          # true at a site whose link follows a published schedule
# Host jobs that only reach the internet; skipped during a gap.
gated_units = ["apt-daily.service", "apt-daily-upgrade.service"]
EOF
fi
systemctl daemon-reload
systemctl enable --now sigmond-link.timer sigmond-link-rac.timer
/usr/local/sbin/sigmond-link apply || true
echo "sigmond-link installed on $(hostname); $(grep -E '^enabled' /etc/sigmond/link.toml)"

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
# SIGMOND_ROOT: a prefix for the tests (tests/test_install_host_link.py); empty on a host.
R="${SIGMOND_ROOT:-}"
# VMID: the decoder VM this host follows (firstboot and pm-align pass it).
VMID="${VMID:-}"

mkdir -p "$R/usr/local/sbin" "$R/etc/systemd/system" "$R/etc/sigmond"
install -m 0755 "$SRC/bin/sigmond-link" "$R/usr/local/sbin/sigmond-link"
for u in sigmond-link.service sigmond-link.timer sigmond-link-rac.service sigmond-link-rac.timer; do
    install -m 0644 "$SRC/systemd/$u" "$R/etc/systemd/system/$u"
done
command -v pdftotext >/dev/null 2>&1 \
    || DEBIAN_FRONTEND=noninteractive apt-get install -y -q poppler-utils >/dev/null 2>&1 \
    || echo "WARNING: poppler-utils not installed: sigmond-link cannot read the USAP schedule (probe mode still works)" >&2
if [[ ! -f "$R/etc/sigmond/link.toml" ]]; then
    {
        echo "# sigmond-link on this Proxmox host.  See \`sigmond-link --help\` and"
        echo "# HamSCI/sigmond tasks/plan-constrained-link-delivery.md."
        if [[ -n "$VMID" ]]; then
            echo "# follow_vm: this host takes VM $VMID's [link_schedule] (its /etc/sigmond/topology.toml)"
            echo "# through the guest agent, so turning the guard on in the VM covers the host too."
            echo "# Only the host-only keys below (gated_units, rac_units, probe_targets) stay the host's."
            echo "follow_vm = $VMID"
        fi
        echo "[link_schedule]"
        [[ -z "$VMID" ]] && echo "enabled = false          # true at a site whose link follows a published schedule"
        echo "# Host jobs that only reach the internet; skipped during a gap."
        echo 'gated_units = ["apt-daily.service", "apt-daily-upgrade.service"]'
    } > "$R/etc/sigmond/link.toml"
fi
systemctl daemon-reload
systemctl enable --now sigmond-link.timer sigmond-link-rac.timer
[[ -z "$R" ]] && { /usr/local/sbin/sigmond-link apply || true; }
echo "sigmond-link installed on $(hostname)$([[ -n "$VMID" ]] && echo ", following VM $VMID")"

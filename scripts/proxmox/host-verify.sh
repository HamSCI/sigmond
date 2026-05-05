#!/usr/bin/env bash
# scripts/proxmox/host-verify.sh
#
# Runs *on the Proxmox host* after the post-config reboot. Confirms that
# vfio-pci is bound to the USB controllers and the host kernel is happy.
#
# Args (env or positional):
#   USB_VID_DID — vendor:device id of the passed-through USB controllers
#
# Exit 0 on success; non-zero with a diagnostic on failure.

set -euo pipefail

USB_VID_DID="${USB_VID_DID:-${1:-}}"
[[ -n "$USB_VID_DID" ]] || { echo "ERROR: USB_VID_DID not set"; exit 2; }

emit() { printf '%s=%q\n' "$1" "$2"; }
log()  { printf '# %s\n' "$*" >&2; }

mapfile -t USB_ADDRS < <(lspci -nn | grep "\[${USB_VID_DID}\]" | awk '{print $1}')
[[ ${#USB_ADDRS[@]} -gt 0 ]] || { emit VERIFY_RESULT "no_devices_match"; exit 1; }

# Each USB controller must show vfio-pci as the driver in use.
all_ok=1
for addr in "${USB_ADDRS[@]}"; do
    drv="$(lspci -nnk -s "$addr" | awk -F': ' '/Kernel driver in use/{print $2; exit}' | tr -d ' ')"
    log "device $addr → driver $drv"
    [[ "$drv" == "vfio-pci" ]] || all_ok=0
    emit "DRIVER_${addr//[:.]/_}" "$drv"
done

# Quick sanity check: no AER errors since boot.
aer_errors="$(journalctl -k --since='-30min' 2>/dev/null | grep -ci 'AER\|aer_event' || true)"
emit AER_ERRORS "$aer_errors"

if [[ $all_ok -eq 1 ]]; then
    emit VERIFY_RESULT "ok"
    exit 0
else
    emit VERIFY_RESULT "wrong_driver"
    exit 1
fi

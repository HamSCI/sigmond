#!/usr/bin/env bash
# scripts/proxmox/radiod-count.sh — how many local radiods to lay the VM out
# for, when the operator gave no count.  Sourced by host-setup.sh (phase 1,
# on the host) and bootstrap.sh (phase 2, in the guest); not run directly.
#
# After phase 1 binds the USB controllers to vfio and reboots, the host's
# lsusb sees no RX888, so the host bus alone would shrink the layout to one
# radiod.  The count phase 1 saved in /etc/sigmond/host-layout.env, and in
# the guest the cards passed through to it, are counted too.

# choose_radiod_count LAYOUT HOST_VISIBLE [GUEST_VISIBLE]
# Prints "N|source": the largest of the three counts (empty or non-numeric
# ones skipped, the first source kept on a tie), never below 1.
choose_radiod_count() {
    local best=0 src="" n label i
    local -a counts=("${1:-}" "${2:-}" "${3:-}")
    local -a labels=("host-layout.env (phase 1)" "the host USB bus" "the guest USB bus")
    for i in 0 1 2; do
        n="${counts[$i]}"; label="${labels[$i]}"
        [[ "$n" =~ ^[0-9]+$ ]] || continue
        if (( 10#$n > best )); then
            best=$((10#$n)); src="$label"
        fi
    done
    if (( best < 1 )); then
        best=1; src="the one-card default"
    fi
    printf '%s|%s\n' "$best" "$src"
}

# rx888_visible: RX888s on THIS machine's bus (04b4:00f1 with firmware,
# 04b4:00f3 without); 0 allowed, 0 when lsusb is missing.
rx888_visible() {
    local n=0 c id
    for id in 04b4:00f1 04b4:00f3; do
        c=$(lsusb -d "$id" 2>/dev/null | grep -c . || true)
        n=$((n + ${c:-0}))
    done
    printf '%s' "$n"
}

# choose_host_reserve_pairs ENV SAVED ROLE
# Physical cores kept for the Proxmox host alone (compute_host_cpu_layout's
# reserve_host_pairs).  Prints "N|source": HOST_RESERVE_PAIRS from the
# environment when set; else the value phase 1 saved in host-layout.env (so
# a re-run keeps the layout, as the radiod count does); else 0 when the host
# records the radiod-only station role (/etc/sigmond-appliance/station-role:
# the host shares the VM's worker pair, leaving room for one more radiod);
# else 1.  A non-numeric or negative HOST_RESERVE_PAIRS is refused (return 1).
choose_host_reserve_pairs() {
    local env="${1:-}" saved="${2:-}" role="${3:-}"
    if [[ -n "$env" ]]; then
        [[ "$env" =~ ^[0-9]+$ ]] || return 1
        printf '%s|HOST_RESERVE_PAIRS\n' "$((10#$env))"
    elif [[ "$saved" =~ ^[0-9]+$ ]]; then
        printf '%s|host-layout.env (phase 1)\n' "$((10#$saved))"
    elif [[ "$role" == radiod-only ]]; then
        printf '0|the radiod-only station role\n'
    else
        printf '1|the default\n'
    fi
}

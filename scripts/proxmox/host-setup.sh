#!/usr/bin/env bash
# host-setup.sh — PHASE 1 of the two-phase DASI2 install: prepare the
# Proxmox HOST standalone, before any VM exists.
#
#   Phase 1 (this script, ON the host):  host RAC (remote reachability)
#       + CPU/passthrough base tuning (IOMMU, vfio, isolcpus, freq
#       utilities) + one reboot.  The host is remotely supportable from
#       this point on.
#   Phase 2 (inside the VM):  instantiate the golden VM
#       (golden-image.sh clone), boot, personalize + site-profile, then
#       run bootstrap.sh — which binds THIS host's passthrough + CPU
#       pinning to the VM (hookscript, qm set) and skips the host
#       reboot phase 1 already did.
#
# Run as root on a fresh Proxmox host, from a sigmond checkout:
#   git clone https://github.com/HamSCI/sigmond
#   sudo bash sigmond/scripts/proxmox/host-setup.sh [--radiod-count N]
#                                                   [--skip-rac] [--skip-tuning]
#
# Idempotent; safe to re-run.  BIOS must already be configured per
# docs/proxmox/wsprdaemon-proxmox-bios-checklist.md.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SIGMOND_REPO=$(cd "$SCRIPT_DIR/../.." && pwd)

RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; BOLD='\033[1m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERR ]${NC} $*" >&2; }
die()  { err "$*"; exit 1; }
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }

RADIOD_COUNT="${LOCAL_RADIOD_COUNT:-1}"
DO_RAC=1
DO_TUNING=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --radiod-count) RADIOD_COUNT="$2"; shift 2 ;;
        --skip-rac)     DO_RAC=0; shift ;;
        --skip-tuning)  DO_TUNING=0; shift ;;
        -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown arg: $1" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "run as root on the Proxmox host"
command -v qm >/dev/null || die "qm not found — this must run on a Proxmox host"
command -v python3 >/dev/null || die "python3 required"

echo -e "${BOLD}Sigmond DASI2 host setup — phase 1 (host standalone)${NC}"
echo

# ─── 1. host RAC — remote reachability first ─────────────────────────────────
if [[ "$DO_RAC" == 1 ]]; then
    rac_dir="$(cd "$SIGMOND_REPO/.." && pwd)/sigmond-rac"
    if [[ ! -x "$rac_dir/install-host.sh" ]]; then
        info "sigmond-rac checkout not found beside sigmond — cloning…"
        if git clone --depth 1 https://github.com/HamSCI/sigmond-rac \
                "$rac_dir" 2>/dev/null; then
            ok "cloned $rac_dir"
        else
            warn "could not clone sigmond-rac — host RAC skipped."
            warn "  install later: clone HamSCI/sigmond-rac + run install-host.sh"
            rac_dir=""
        fi
    fi
    if [[ -n "${rac_dir:-}" && -x "$rac_dir/install-host.sh" ]]; then
        info "installing host RAC (frpc)…"
        bash "$rac_dir/install-host.sh"
        ok "host RAC installed (inert until /etc/sigmond/frpc-host.toml is filled)"
    fi
else
    info "host RAC skipped (--skip-rac)"
fi

# ─── 2. console keys must not be able to stop the station ────────────────────
# ⛔ A STATION MUST NOT BE SHUT DOWN BY SOMEONE PLUGGING IN A KEYBOARD.
# systemd ships HandlePowerKey=poweroff.  On an appliance a power-key press
# is almost never a decision: it is an operator connecting a monitor and a
# USB keyboard to find out why the station is misbehaving, and catching the
# button on the way past.  Measured on AI6VN 2026-10-28 — logind logged
# "Power key pressed short" at 19:48:17, a clean poweroff followed one second
# later, and the station was off the air until somebody walked over to it.
# On a box at McMurdo that is a field visit to undo a cable insertion.
#
# Suspend and hibernate go with it, and for a worse reason: a suspended
# station is as dead as a powered-off one and does not even look off.
#
# ⛔ THIS BELONGS ON THE HOST ONLY — NEVER INSIDE THE VM.  Proxmox stops a
# guest by sending it an ACPI power-key event (`qm shutdown`, and pve-guests
# at host shutdown).  A VM that ignored its power key would sit out the full
# shutdown timeout — 180 s by default — and then be killed, so every host
# reboot would turn into an unclean guest shutdown.  In the guest the power
# key is the mechanism by which it is stopped CORRECTLY; only on the host is
# it a hazard.  The two look like the same setting and are opposites.
#
# This does not lock anyone out: a firmware long-press (hold ~4 s) still cuts
# power below anything systemd sees, so the hardware escape hatch remains.
LOGIND_DROPIN=/etc/systemd/logind.conf.d/10-sigmond-console-keys.conf
read -r -d '' LOGIND_BODY <<'LOGIND_EOF' || true
# Written by sigmond host-setup.sh — do not edit by hand.
# Why: scripts/proxmox/host-setup.sh, section 2.
# HOST ONLY.  The decoder VM keeps systemd's defaults so that `qm shutdown`
# can stop it cleanly.
[Login]
HandlePowerKey=ignore
HandlePowerKeyLongPress=ignore
HandleSuspendKey=ignore
HandleSuspendKeyLongPress=ignore
HandleHibernateKey=ignore
HandleHibernateKeyLongPress=ignore
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
LOGIND_EOF

mkdir -p "$(dirname "$LOGIND_DROPIN")"
if [[ -f "$LOGIND_DROPIN" ]] && [[ "$(cat "$LOGIND_DROPIN")" == "$LOGIND_BODY" ]]; then
    ok "console keys already inert ($LOGIND_DROPIN)"
else
    printf '%s\n' "$LOGIND_BODY" > "$LOGIND_DROPIN"
    chmod 0644 "$LOGIND_DROPIN"
    ok "console power/suspend keys disabled ($LOGIND_DROPIN)"
    # reload, never restart: restarting logind drops live sessions, and this
    # script is routinely run over the very ssh session that would be dropped.
    if systemctl reload systemd-logind 2>/dev/null; then
        ok "systemd-logind reloaded — in effect now"
    else
        warn "could not reload systemd-logind; the setting applies at next boot"
    fi
fi

# ─── 3. CPU/passthrough base tuning ───────────────────────────────────────────
REBOOT_REQUIRED=0
if [[ "$DO_TUNING" == 1 ]]; then
    info "discovering host topology (no VM yet)…"
    declare -A KV
    while IFS='=' read -r k v; do
        [[ "$k" =~ ^[A-Z_]+$ ]] && KV[$k]=$(eval "printf '%s' $v")
    done < <(bash "$SCRIPT_DIR/host-discover.sh" --no-vm)

    [[ "${KV[DISCOVERY_RESULT]:-}" == "ok" ]] || die "host discovery failed"
    [[ "${KV[IOMMU_OK]:-0}" == "1" ]] || warn \
        "IOMMU groups not isolated yet (normal before the first reboot with IOMMU flags)"

    info "USB=${KV[USB_VID_DID]} cpus=${KV[HOST_CPU_COUNT]} HT=${KV[HT_PATTERN]} pairs='${KV[HT_PAIRS]}' local_radiods=${RADIOD_COUNT}"

    layout_vars="$(PYTHONPATH="$SIGMOND_REPO/lib" python3 -c '
import sys
from sigmond.cpu import parse_ht_pairs, compute_host_cpu_layout, layout_shell_vars
pairs = parse_ht_pairs(sys.argv[1])
lay = compute_host_cpu_layout(pairs, local_radiod_count=int(sys.argv[2]))
print(layout_shell_vars(lay))
' "${KV[HT_PAIRS]}" "$RADIOD_COUNT")" \
        || die "CPU layout computation failed — non-SMT/asymmetric host; configure manually per docs/proxmox/wsprdaemon-proxmox-cpu-clock-tuning.md"
    eval "$layout_vars"

    info "applying host base tuning (grub IOMMU/isolcpus, vfio, initramfs)…"
    apply_out="$(VMID='' USB_VID_DID="${KV[USB_VID_DID]}" \
        CPU_VENDOR="${KV[CPU_VENDOR]}" ISOLCPUS_RANGE="$ISOLCPUS_RANGE" \
        bash "$SCRIPT_DIR/host-apply.sh")"
    echo "$apply_out" | sed 's/^/    /'
    if grep -q '^REBOOT_REQUIRED=1' <<<"$apply_out"; then
        REBOOT_REQUIRED=1
    fi

    # Persist the layout so phase 2 (guest bootstrap) and the operator can
    # see what phase 1 decided.
    mkdir -p /etc/sigmond
    {
        echo "# written by sigmond host-setup.sh $(date -Iseconds)"
        echo "LOCAL_RADIOD_COUNT=$RADIOD_COUNT"
        echo "$layout_vars"
    } > /etc/sigmond/host-layout.env
    ok "layout saved to /etc/sigmond/host-layout.env"
else
    info "CPU/passthrough tuning skipped (--skip-tuning)"
fi

# ─── 4. summary ───────────────────────────────────────────────────────────────
echo
echo -e "${BOLD}${GREEN}Phase 1 (host) complete.${NC}"
echo "  Next:"
echo "    • fill /etc/sigmond/frpc-host.toml (WD-admin assignment) to activate host RAC"
if [[ "$REBOOT_REQUIRED" == 1 ]]; then
    echo -e "    • ${BOLD}reboot this host now${NC} to activate IOMMU/isolcpus/vfio"
fi
echo "    • phase 2: instantiate the DASI2 VM —"
echo "        scripts/proxmox/golden-image.sh clone <template-id> <site-name>"
echo "      boot it, personalize + site-profile, then run bootstrap.sh inside"
echo "      it (binds passthrough + CPU pinning to the VM)."
echo

if [[ "$REBOOT_REQUIRED" == 1 ]]; then
    read -r -p "Reboot now? [y/N] " a
    [[ "${a,,}" == y* ]] && reboot
fi
exit 0

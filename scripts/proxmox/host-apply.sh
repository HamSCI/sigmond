#!/usr/bin/env bash
# scripts/proxmox/host-apply.sh
#
# Runs *on the Proxmox host* (scp'd to /tmp by bootstrap.sh). Applies all
# host-side configuration needed for PCIe USB-controller passthrough and
# CPU isolation, idempotently.
#
# Required env vars (passed via ssh):
#   USB_VID_DID, CPU_VENDOR, ISOLCPUS_RANGE   — always
#   VMID, VM_VCPU_COUNT, VM_CORES, VM_THREADS, RADIOD_CPUS, WORKER_CPUS,
#   VCPU_TO_PCPU                              — only for VM binding
#
# Two-phase install model: with VMID EMPTY/unset, applies only the
# VM-independent host base (grub IOMMU/isolcpus flags, vfio modules,
# initramfs) — Phase 1, run by host-setup.sh BEFORE any VM exists.
# With VMID set, additionally renders the cpu-pin hookscript and binds
# the VM (qm set) — Phase 2, run by the guest bootstrap.
#
# Reads the cpu-pin template from /tmp/cpu-pin-VMID.sh.template (VM mode).
#
# Idempotent: re-runs cleanly. Backup of mutated upstream files written
# to /root/proxmox-passthrough-backup/.

set -euo pipefail

# ─── inputs ───────────────────────────────────────────────────────────────────
VMID="${VMID:-}"
: "${USB_VID_DID:?USB_VID_DID required}"
: "${CPU_VENDOR:?CPU_VENDOR required}"
: "${ISOLCPUS_RANGE:?ISOLCPUS_RANGE required}"
: "${RADIOD_FREQ_KHZ:=3200000}"
: "${WORKER_FREQ_KHZ:=1400000}"
# L3 CAT way-fraction for radiod's exclusive slice.  The generic default is
# 0.62 (10/16 ways on this generation).  AC0G-B4's measured ZERO-GAP baseline
# (gap_hourly docstring) and the KX4AZ reference both use 13/3 on a 16 MB
# part — pass RADIOD_L3_FRACTION=0.8125 for that split.
: "${RADIOD_L3_FRACTION:=0.62}"
if [[ -n "$VMID" ]]; then
    : "${VM_VCPU_COUNT:?VM_VCPU_COUNT required (VM mode)}"
    : "${VM_CORES:?VM_CORES required (VM mode)}"
    : "${VM_THREADS:?VM_THREADS required (VM mode)}"
    : "${RADIOD_CPUS:?RADIOD_CPUS required (VM mode)}"
    : "${WORKER_CPUS:?WORKER_CPUS required (VM mode)}"
    : "${VCPU_TO_PCPU:?VCPU_TO_PCPU required (VM mode)}"
    CONF="/etc/pve/qemu-server/${VMID}.conf"
    [[ -f "$CONF" ]] || { echo "ERROR: VM config $CONF does not exist" >&2; exit 1; }
fi

BACKUP_DIR="/root/proxmox-passthrough-backup"
TEMPLATE="/tmp/cpu-pin-VMID.sh.template"
SNIPPET="/var/lib/vz/snippets/cpu-pin-${VMID}.sh"

log() { printf '[host-apply] %s\n' "$*"; }
backup_once() {
    local src="$1"
    local dst="${BACKUP_DIR}/$(basename "$src").original"
    [[ -e "$dst" ]] && return 0
    [[ -e "$src" ]] || return 0
    mkdir -p "$BACKUP_DIR"
    cp -p "$src" "$dst"
    log "backup: $src → $dst"
}

# ─── /etc/default/grub.d/sigmond.cfg ──────────────────────────────────────────
backup_once /etc/default/grub
mkdir -p /etc/default/grub.d

if [[ "$CPU_VENDOR" == "AuthenticAMD" ]]; then
    IOMMU_FLAGS="amd_iommu=on iommu=pt"
elif [[ "$CPU_VENDOR" == "GenuineIntel" ]]; then
    IOMMU_FLAGS="intel_iommu=on iommu=pt"
else
    IOMMU_FLAGS="iommu=pt"
fi

cat > /etc/default/grub.d/sigmond.cfg <<EOF
# /etc/default/grub.d/sigmond.cfg — managed by sigmond. Do not edit by hand.
# Re-run \`bash sigmond/install.sh\` (or the Proxmox bootstrap) to update.
GRUB_CMDLINE_LINUX_DEFAULT="\${GRUB_CMDLINE_LINUX_DEFAULT} ${IOMMU_FLAGS} isolcpus=${ISOLCPUS_RANGE} nohz_full=${ISOLCPUS_RANGE} rcu_nocbs=${ISOLCPUS_RANGE} tsc=reliable processor.max_cstate=1"
EOF
log "wrote /etc/default/grub.d/sigmond.cfg"

# ─── /etc/modules ─────────────────────────────────────────────────────────────
backup_once /etc/modules
for mod in vfio vfio_iommu_type1 vfio_pci; do
    if ! grep -qE "^${mod}\$" /etc/modules; then
        echo "$mod" >> /etc/modules
        log "added $mod to /etc/modules"
    fi
done

# ─── /etc/modprobe.d/vfio.conf ────────────────────────────────────────────────
backup_once /etc/modprobe.d/vfio.conf
cat > /etc/modprobe.d/vfio.conf <<EOF
# /etc/modprobe.d/vfio.conf — managed by sigmond. Do not edit by hand.
# Bind USB controllers ($USB_VID_DID) to vfio-pci at boot.
# softdep ensures vfio-pci binds *before* xhci_pci tries to claim them —
# critical on AMD APUs where live detach causes a host reboot.
options vfio-pci ids=${USB_VID_DID} disable_vga=1
softdep xhci_pci pre: vfio-pci
EOF
log "wrote /etc/modprobe.d/vfio.conf"

# ─── rebuild grub + initramfs ─────────────────────────────────────────────────
log "running update-grub…"
update-grub >/dev/null
log "running update-initramfs -u -k all (this can take a minute)…"
update-initramfs -u -k all >/dev/null

# ─── VM-independent base done — report whether a reboot is still needed ──────
# The guest bootstrap / host-setup use this to skip a redundant reboot when
# the base config (isolcpus cmdline + vfio-pci binding) is already active.
REBOOT_REQUIRED=1
if grep -qw "isolcpus=${ISOLCPUS_RANGE}" /proc/cmdline; then
    first_addr="$(lspci -nn | grep "\[${USB_VID_DID}\]" | awk '{print $1; exit}')"
    if [[ -n "$first_addr" ]] && \
       [[ "$(basename "$(readlink -f /sys/bus/pci/devices/0000:${first_addr}/driver 2>/dev/null || true)")" == "vfio-pci" ]]; then
        REBOOT_REQUIRED=0
    fi
fi
echo "REBOOT_REQUIRED=${REBOOT_REQUIRED}"

if [[ -z "$VMID" ]]; then
    echo "host-apply: base complete (no VM bound — phase 1)"
    exit 0
fi

# ─── cpu-pin hookscript ───────────────────────────────────────────────────────
[[ -f "$TEMPLATE" ]] || { echo "ERROR: $TEMPLATE not found (scp it before running this)" >&2; exit 1; }

mkdir -p /var/lib/vz/snippets

# Render the hookscript by parameter substitution. The template uses sentinel
# placeholders: @@VMID@@, @@RADIOD_CPUS@@, @@WORKER_CPUS@@, @@VCPU_TO_PCPU@@,
# @@RADIOD_FREQ_KHZ@@, @@WORKER_FREQ_KHZ@@.
sed \
    -e "s|@@VMID@@|${VMID}|g" \
    -e "s|@@RADIOD_CPUS@@|${RADIOD_CPUS}|g" \
    -e "s|@@WORKER_CPUS@@|${WORKER_CPUS}|g" \
    -e "s|@@VCPU_TO_PCPU@@|${VCPU_TO_PCPU}|g" \
    -e "s|@@RADIOD_FREQ_KHZ@@|${RADIOD_FREQ_KHZ}|g" \
    -e "s|@@WORKER_FREQ_KHZ@@|${WORKER_FREQ_KHZ}|g" \
    "$TEMPLATE" > "$SNIPPET"
chmod +x "$SNIPPET"
log "wrote $SNIPPET"

# ─── qm config ────────────────────────────────────────────────────────────────
backup_once "$CONF"

# Discover existing USB device passthrough lines to remove.
mapfile -t USB_LINES < <(grep -E '^usb[0-9]+:' "$CONF" | sed -E 's/:.*//' || true)
if [[ ${#USB_LINES[@]} -gt 0 ]]; then
    log "removing existing USB device passthrough lines: ${USB_LINES[*]}"
    qm set "$VMID" --delete "$(IFS=,; echo "${USB_LINES[*]}")"
fi

# Reset existing hostpci lines so we can re-apply cleanly.
mapfile -t PCI_LINES < <(grep -E '^hostpci[0-9]+:' "$CONF" | sed -E 's/:.*//' || true)
if [[ ${#PCI_LINES[@]} -gt 0 ]]; then
    log "clearing existing hostpci lines: ${PCI_LINES[*]}"
    qm set "$VMID" --delete "$(IFS=,; echo "${PCI_LINES[*]}")"
fi

# Find USB controller PCI addresses (host-side, sysfs).
mapfile -t USB_ADDRS < <(lspci -nn | grep "\[${USB_VID_DID}\]" | awk '{print $1}')
[[ ${#USB_ADDRS[@]} -gt 0 ]] || { echo "ERROR: no PCI devices match $USB_VID_DID" >&2; exit 1; }

i=0
for addr in "${USB_ADDRS[@]}"; do
    qm set "$VMID" "-hostpci${i}" "0000:${addr},pcie=1"
    log "set hostpci${i} = 0000:${addr}"
    i=$((i+1))
done

qm set "$VMID" --machine q35
qm set "$VMID" --cpu host
qm set "$VMID" --boot order=scsi0
qm set "$VMID" --onboot 1
qm set "$VMID" --hookscript "local:snippets/cpu-pin-${VMID}.sh"
qm set "$VMID" --affinity "$ISOLCPUS_RANGE"
qm set "$VMID" --cores "$VM_CORES" --sockets 1
# -cpu flags beyond topoext (tuning doc Part 12, each one measured):
#   host-cache-info=on — without it QEMU advertises a FICTIONAL cache topology
#     (16 MB L3 claimed on an 8 MB host); FFTW plans codelets for the fake
#     cache and the FFT thread pegs from constant DRAM evictions (12a).
#   +kvm_pv_eoi,+kvm_pv_unhalt — Proxmox adds these from `cpu: host`, but a
#     hand-rolled -cpu in args: silently REPLACES Proxmox's list, dropping
#     them; fewer VM exits on interrupt EOI and spinlock waits (12b).
#   -svm — no nested virt in an SDR guest; exposing it costs overhead (12d).
qm set "$VMID" --args "-smp ${VM_VCPU_COUNT},sockets=1,cores=${VM_CORES},threads=${VM_THREADS},maxcpus=${VM_VCPU_COUNT} -cpu host,host-cache-info=on,topoext=on,+kvm_pv_eoi,+kvm_pv_unhalt,-svm"
log "qm set complete"

# ─── radiod VM fence + KSM hold ───────────────────────────────────────────────
# qemu's NON-vCPU threads (io_uring iou-wrk, the main loop) inherit the VM-wide
# affinity and land on the very CPU pair radiod's threads are isolated onto.
# io_uring spawns those threads at RUNTIME, so a one-shot fence decays — hence a
# service that re-applies every 30 s.  It also holds KSM off: with a single VM
# KSM merges ~76 pages out of billions scanned, and its TLB shootdowns hit
# exactly the CPUs being protected.
# Measured on AC0G-B4: the pair is worth ~3x on sample loss (30 s/day/channel
# -> ~8 s/day/channel).  It was hand-applied there on 2026-08-14 and was NOT in
# the image — so every DASI unit has been running without it.
# Target = the CPUs OUTSIDE the VM's affinity, derived rather than hardcoded.
FENCE_HI="${ISOLCPUS_RANGE##*-}"
FENCE_LO=$((FENCE_HI + 1))
HOST_CPUS="$(nproc)"
if [ "$FENCE_LO" -le $((HOST_CPUS - 1)) ]; then
    FENCE_CPUS="${FENCE_LO}-$((HOST_CPUS - 1))"
    install -m 755 "$(dirname "$0")/radiod-vm-fence.sh" \
        /usr/local/sbin/radiod-vm-fence.sh
    cat > /etc/systemd/system/radiod-vm-fence.service <<FENCE
[Unit]
Description=Keep qemu non-vCPU threads off radiod's isolated CPUs, hold KSM off
After=qemu.slice

[Service]
Type=simple
ExecStart=/usr/local/sbin/radiod-vm-fence.sh ${VMID} ${FENCE_CPUS} 30
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
FENCE
    systemctl daemon-reload
    systemctl enable --now radiod-vm-fence.service >/dev/null 2>&1 \
        && log "radiod-vm-fence armed (VM ${VMID} non-vCPU threads -> ${FENCE_CPUS}, KSM held off)" \
        || log "WARNING: radiod-vm-fence failed to start"
    # ksmtuned would turn KSM back on behind the fence's back.
    systemctl disable --now ksmtuned >/dev/null 2>&1 || true
else
    log "radiod-vm-fence SKIPPED: VM affinity ${ISOLCPUS_RANGE} leaves no spare CPU on a ${HOST_CPUS}-CPU host"
fi

# ─── host IRQ herding + L3 CAT partition ─────────────────────────────────────
# Two more things isolcpus does NOT do (both observed on AI6VN-PM, 2026-08-26):
#   1. Interrupts: the vfio-msix vectors for the passed-through USB controllers
#      kept a wide mask and fired on isolated vCPU pCPUs — every RX888 URB
#      completion stole time from a pinned vCPU thread.  Herd every movable
#      host IRQ onto the housekeeping CPUs (same set the fence protects).
#   2. Cache: the L3 is shared; decoder walls on the worker pCPUs evict
#      radiod's FFT working set.  resctrl CLOS follows the physical cpu, so an
#      exclusive L3 slice for radiod's pCPUs protects the guest's radiod with
#      no guest-side support.
# Both are boot-volatile, hence oneshot units.
if [ -n "${FENCE_CPUS:-}" ]; then
    read -r -a _v2p <<< "${VCPU_TO_PCPU}"
    _radiod_pcpus=()
    for _g in ${RADIOD_CPUS}; do _radiod_pcpus+=("${_v2p[$_g]:-$_g}"); done
    RADIOD_PCPUS="$(printf '%s\n' "${_radiod_pcpus[@]}" | sort -n | paste -sd, -)"
    _others=()
    for (( _c=0; _c<HOST_CPUS; _c++ )); do
        case ",${RADIOD_PCPUS}," in *",${_c},"*) ;; *) _others+=("$_c");; esac
    done
    OTHER_PCPUS="$(printf '%s\n' "${_others[@]}" | paste -sd, -)"

    for _s in sigmond-host-irq-affinity.sh sigmond-host-resctrl.sh; do
        install -m 755 "$(dirname "$0")/${_s}" "/usr/local/sbin/${_s}"
    done

    cat > /etc/systemd/system/sigmond-host-irq-affinity.service <<UNIT
[Unit]
Description=Pin host IRQs off VM ${VMID} isolated pCPUs
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/sigmond-host-irq-affinity.sh ${FENCE_CPUS}

[Install]
WantedBy=multi-user.target
UNIT

    cat > /etc/systemd/system/sigmond-host-resctrl.service <<UNIT
[Unit]
Description=L3 CAT partition for guest radiod pCPUs (VM ${VMID})
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/sigmond-host-resctrl.sh ${RADIOD_PCPUS} ${OTHER_PCPUS} ${RADIOD_L3_FRACTION}

[Install]
WantedBy=multi-user.target
UNIT

    systemctl daemon-reload
    systemctl enable --now sigmond-host-irq-affinity.service >/dev/null 2>&1 \
        && log "host IRQs herded -> cpus ${FENCE_CPUS} (persistent)" \
        || log "WARNING: sigmond-host-irq-affinity failed to start"
    systemctl enable --now sigmond-host-resctrl.service >/dev/null 2>&1 \
        && log "L3 CAT: radiod pCPUs ${RADIOD_PCPUS} get an exclusive slice (persistent; no-op without cat_l3)" \
        || log "WARNING: sigmond-host-resctrl failed to start"
else
    log "host IRQ/L3 tuning SKIPPED: no housekeeping CPUs outside VM affinity"
fi

# Snapshot the working configuration alongside the originals.
cp -p "$CONF" "${BACKUP_DIR}/${VMID}.conf.applied"
date -Iseconds > "${BACKUP_DIR}/applied-on.txt"

echo "host-apply: complete"

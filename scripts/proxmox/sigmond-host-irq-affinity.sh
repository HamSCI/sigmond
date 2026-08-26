#!/bin/bash
# scripts/proxmox/sigmond-host-irq-affinity.sh <HOUSEKEEPING_CPUS>
#
# Runs ON THE PROXMOX HOST.  Pin every movable host IRQ, and the default
# mask newly registered IRQs inherit, onto the housekeeping CPUs (the ones
# OUTSIDE the VM's affinity — same derivation as radiod-vm-fence).
#
# isolcpus= removes the VM's pCPUs from the host scheduler but does NOT
# move interrupts: on AI6VN-PM the vfio-msix vectors for the passed-through
# RX888 USB controllers sat with a wide mask and fired on isolated vCPU
# pCPUs, stealing time from pinned vCPU threads on every URB completion.
# This is the tuning doc's Part 10 "recommended hygiene", made persistent
# (it was previously a manual loop that died with every reboot).
#
# Kernel-managed per-queue IRQs (NVMe/NIC) refuse the write and are left
# alone — they only fire for I/O submitted from their own CPU, which after
# isolation is essentially only host I/O.  irqbalance must stay absent or
# disabled, or it will migrate everything back.
#
# Not reboot-persistent by itself: host-apply.sh installs a oneshot unit.
set -u
HOUSE_CPUS="${1:?usage: sigmond-host-irq-affinity.sh <housekeeping-cpus e.g. 10-11>}"

# cpu list -> hex mask ("10-11" -> c00).  Good to 62 cpus; past that the
# /proc mask format needs comma-grouped words, so leave the default alone.
mask=0 toobig=0
for part in $(echo "$HOUSE_CPUS" | tr ',' ' '); do
    lo=${part%%-*}; hi=${part##*-}
    for (( c=lo; c<=hi; c++ )); do
        [ "$c" -ge 63 ] && { toobig=1; break; }
        mask=$(( mask | (1 << c) ))
    done
done
if [ "$toobig" = "0" ] && [ "$mask" != "0" ]; then
    hexmask=$(printf '%x' "$mask")
    echo "$hexmask" > /proc/irq/default_smp_affinity 2>/dev/null \
        && echo "sigmond-host-irq: default_smp_affinity -> $hexmask (cpus $HOUSE_CPUS)"
fi

moved=0 kept=0
for d in /proc/irq/[0-9]*; do
    [ -f "$d/smp_affinity_list" ] || continue
    if echo "$HOUSE_CPUS" > "$d/smp_affinity_list" 2>/dev/null; then
        moved=$((moved+1))
    else
        kept=$((kept+1))    # kernel-managed or immovable (IRQ0); left alone
    fi
done
echo "sigmond-host-irq: $moved IRQs -> cpus $HOUSE_CPUS, $kept kernel-managed/immovable left alone"

# Report where the vfio vectors (the passed-through USB path) actually fire.
grep vfio /proc/interrupts 2>/dev/null | awk -F: '{gsub(/ /,"",$1); print $1}' | while read -r irq; do
    echo "sigmond-host-irq: vfio IRQ $irq effective=$(cat /proc/irq/$irq/effective_affinity_list 2>/dev/null)"
done
exit 0

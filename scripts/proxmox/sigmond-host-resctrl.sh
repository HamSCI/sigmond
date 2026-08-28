#!/bin/bash
# scripts/proxmox/sigmond-host-resctrl.sh <RADIOD_PCPUS> <OTHER_PCPUS> [fraction]
#
# Runs ON THE PROXMOX HOST.  L3 CAT (resctrl) partition: give the pCPUs
# hosting the guest's radiod vCPUs an EXCLUSIVE slice of the L3, and
# everything else (worker vCPU pCPUs + the host itself) the remainder.
#
# CPU pinning alone does not protect radiod's cache: the L3 is shared and
# the decoder walls evict its FFT working set every cycle.  resctrl CLOS
# follows the PHYSICAL cpu, so partitioning on the host protects the
# guest's radiod with no guest-side support at all.
#
# The way split is computed from the LIVE cache geometry (ways from
# resctrl's cbm_mask), radiod share = round(ways * fraction), default
# fraction 0.62 (~5/8).  On the 5560U (8 MB, 16 ways) that yields the
# measured-good 10 ways = 5 MB exclusive; see wsprdaemon's
# wd-cpu-tuning.md "What a tuned host still shares" for why the partition
# helps and what it cannot do (DRAM bandwidth is still shared).
#
# No-op (exit 0 with a message) on CPUs without CAT.  Not reboot-persistent
# by itself: host-apply.sh installs a oneshot unit.
set -u
RADIOD_PCPUS="${1:?usage: sigmond-host-resctrl.sh <radiod-pcpus> <other-pcpus> [fraction]}"
OTHER_PCPUS="${2:?usage: sigmond-host-resctrl.sh <radiod-pcpus> <other-pcpus> [fraction]}"
RADIOD_L3_FRACTION="${3:-0.62}"
R=/sys/fs/resctrl

grep -qm1 cat_l3 /proc/cpuinfo || { echo "sigmond-host-resctrl: no L3 CAT on this CPU; nothing to do"; exit 0; }
mountpoint -q "$R" || mount -t resctrl resctrl "$R" 2>/dev/null \
    || { echo "sigmond-host-resctrl: cannot mount resctrl; CAT unavailable"; exit 0; }

cbm=$(cat "$R/info/L3/cbm_mask" 2>/dev/null)
[ -n "$cbm" ] || { echo "sigmond-host-resctrl: no L3 cbm_mask; nothing to do"; exit 0; }
ways=0; full=$((16#$cbm))
v=$full; while [ "$v" -gt 0 ]; do ways=$((ways + (v & 1))); v=$((v >> 1)); done
rw=$(awk -v w="$ways" -v f="$RADIOD_L3_FRACTION" 'BEGIN{printf "%d", int(w*f+0.5)}')
[ "$rw" -lt 1 ] && rw=1
[ "$rw" -ge "$ways" ] && rw=$((ways - 1))
radiod_mask=$(printf '%x' $(( (1 << rw) - 1 )))
other_mask=$(printf '%x' $(( full ^ ((1 << rw) - 1) )))

mkdir -p "$R/radiod" "$R/others"
# masks BEFORE cpus, to avoid a transient window where a group holds the full mask
echo "L3:0=$other_mask"  > "$R/schemata"
echo "L3:0=$radiod_mask" > "$R/radiod/schemata"
echo "L3:0=$other_mask"  > "$R/others/schemata"
echo "$OTHER_PCPUS"  > "$R/others/cpus_list"
echo "$RADIOD_PCPUS" > "$R/radiod/cpus_list"

echo "sigmond-host-resctrl: radiod pCPUs $(cat $R/radiod/cpus_list) -> $rw/$ways ways (mask $radiod_mask)"
echo "sigmond-host-resctrl: others pCPUs $(cat $R/others/cpus_list) -> $((ways - rw))/$ways ways (mask $other_mask)"
exit 0

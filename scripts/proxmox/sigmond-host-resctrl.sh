#!/bin/bash
# scripts/proxmox/sigmond-host-resctrl.sh <RADIOD_PCPUS> <OTHER_PCPUS> [fraction] [DECODER_PCPUS DECODER_MB_GBPS]
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
# Optional decoder DRAM-bandwidth cap (AMD MBA), arguments 4 and 5: the
# guest's worker (decoder) pCPUs go in a third group, `decoders`, with the
# same L3 ways as `others` and `MB:0=<GB/s * 8>`.  AMD MBA counts in 1/8
# GB/s, 2048 = unthrottled (Documentation/arch/x86/resctrl.rst, "Memory
# bandwidth Allocation" for AMD).  Measured on KFS-OMNI-SW-PM (5560U),
# 2026-11-06, resctrl mbm_total_bytes per 2 s: the radiod pair is steady at
# 7.8-8.7 GB/s; the decoders 1.3-2 GB/s quiet and up to ~7 GB/s in the
# even-minute WSPR burst (30-40 decoder processes).  MB:0=64 (8 GB/s) cut
# OMNI's fft-thread burst plateau from 76-88 % (peaks 92-100 %) to 69-72 %
# with decode time unchanged (~12 s); MB:0=32 (4 GB/s) doubled the burst
# length.  The host's housekeeping pCPUs (QEMU I/O, herded IRQs) stay in
# `others`, uncapped.  Applied only when /proc/cpuinfo has `mba` and
# info/MB has AMD's scale (bandwidth_gran 1, default above 100).  Intel MBA
# is a percentage of a delay throttle, not GB/s; it is skipped with a
# message, and the decoder pCPUs stay in `others`.
#
# ⚠ DO NOT RESTART sigmond-host-resctrl.service ON A RUNNING STATION.
# Measured 2026-11-06 00:23:16Z on two hosts: restarting it made both
# guests' clocks lag ~2 s over 5 s.  Every radiod logged "RTP<->GPS offset
# STEPPED" (cumulative -1.8..-2.2 s) and a WSPR cycle was lost on every
# reporter.  Creating or changing a separate group's MB: or cpus_list later
# caused no step.  host-apply.sh therefore leaves an already-active unit
# running and says the new partition applies at the next boot.
#
# No-op (exit 0 with a message) on CPUs without CAT.  Not reboot-persistent
# by itself: host-apply.sh installs a oneshot unit.
#
# SIGMOND_RESCTRL_ROOT and SIGMOND_CPUINFO are for tests only; with the
# root overridden the script does not try to mount it.
set -u
USAGE="usage: sigmond-host-resctrl.sh <radiod-pcpus> <other-pcpus> [fraction] [decoder-pcpus decoder-mb-gbps]"
RADIOD_PCPUS="${1:?$USAGE}"
OTHER_PCPUS="${2:?$USAGE}"
RADIOD_L3_FRACTION="${3:-0.62}"
DECODER_PCPUS="${4:-}"
DECODER_MB_GBPS="${5:-}"
R="${SIGMOND_RESCTRL_ROOT:-/sys/fs/resctrl}"
CPUINFO="${SIGMOND_CPUINFO:-/proc/cpuinfo}"

grep -qm1 cat_l3 "$CPUINFO" || { echo "sigmond-host-resctrl: no L3 CAT on this CPU; nothing to do"; exit 0; }
if [ -z "${SIGMOND_RESCTRL_ROOT:-}" ]; then
    mountpoint -q "$R" || mount -t resctrl resctrl "$R" 2>/dev/null \
        || { echo "sigmond-host-resctrl: cannot mount resctrl; CAT unavailable"; exit 0; }
fi

cbm=$(cat "$R/info/L3/cbm_mask" 2>/dev/null)
[ -n "$cbm" ] || { echo "sigmond-host-resctrl: no L3 cbm_mask; nothing to do"; exit 0; }
ways=0; full=$((16#$cbm))
v=$full; while [ "$v" -gt 0 ]; do ways=$((ways + (v & 1))); v=$((v >> 1)); done
rw=$(awk -v w="$ways" -v f="$RADIOD_L3_FRACTION" 'BEGIN{printf "%d", int(w*f+0.5)}')
[ "$rw" -lt 1 ] && rw=1
[ "$rw" -ge "$ways" ] && rw=$((ways - 1))
radiod_mask=$(printf '%x' $(( (1 << rw) - 1 )))
other_mask=$(printf '%x' $(( full ^ ((1 << rw) - 1) )))

# The AMD MB value for the decoder cap, or "" (with the reason printed)
# when the cap is not asked for or cannot be applied here.
decoder_mb=""
if [ -n "$DECODER_PCPUS" ] && [ -n "$DECODER_MB_GBPS" ]; then
    gran=$(cat "$R/info/MB/bandwidth_gran" 2>/dev/null)
    mb_default=$(sed -n 's/^[[:space:]]*MB:0=\([0-9]*\).*/\1/p' "$R/schemata" 2>/dev/null | head -n1)
    if ! grep -qwm1 mba "$CPUINFO"; then
        echo "sigmond-host-resctrl: no MBA on this CPU; decoder bandwidth left uncapped"
    elif [ -z "$gran" ] || [ -z "$mb_default" ]; then
        echo "sigmond-host-resctrl: resctrl shows no MB resource; decoder bandwidth left uncapped"
    elif grep -qs 'resctrl.*mba_MBps' /proc/mounts && [ -z "${SIGMOND_RESCTRL_ROOT:-}" ]; then
        echo "sigmond-host-resctrl: resctrl mounted with mba_MBps; decoder bandwidth left uncapped"
    elif [ "$gran" != 1 ] || [ "$mb_default" -le 100 ]; then
        echo "sigmond-host-resctrl: MB is a percentage here (Intel MBA, gran $gran, max $mb_default), not AMD's 1/8 GB/s; decoder bandwidth left uncapped"
    else
        decoder_mb=$(awk -v g="$DECODER_MB_GBPS" 'BEGIN{printf "%d", g*8+0.5}')
        [ "$decoder_mb" -lt 1 ] && decoder_mb=1
        [ "$decoder_mb" -gt "$mb_default" ] && decoder_mb=$mb_default
    fi
fi

# cpus_list ($1) without the cpus in $2; both kernel list syntax (0-3,8).
expand() { tr ',' '\n' <<<"$1" | awk -F- 'NF{e=(NF>1?$2:$1); for(i=$1;i<=e;i++) print i}'; }
without() { comm -23 <(expand "$1" | sort) <(expand "$2" | sort) | sort -n | paste -sd, -; }

mkdir -p "$R/radiod" "$R/others"
# masks BEFORE cpus, to avoid a transient window where a group holds the full mask
echo "L3:0=$other_mask"  > "$R/schemata"
echo "L3:0=$radiod_mask" > "$R/radiod/schemata"
echo "L3:0=$other_mask"  > "$R/others/schemata"
if [ -n "$decoder_mb" ]; then
    mkdir -p "$R/decoders"
    echo "L3:0=$other_mask" > "$R/decoders/schemata"
    echo "MB:0=$decoder_mb" >> "$R/decoders/schemata"
    echo "$(without "$OTHER_PCPUS" "$DECODER_PCPUS")" > "$R/others/cpus_list"
    echo "$DECODER_PCPUS" > "$R/decoders/cpus_list"
else
    echo "$OTHER_PCPUS"  > "$R/others/cpus_list"
    # A decoders group from an earlier run: its cpus just moved to `others`.
    [ -d "$R/decoders" ] && rmdir "$R/decoders" 2>/dev/null
fi
echo "$RADIOD_PCPUS" > "$R/radiod/cpus_list"

echo "sigmond-host-resctrl: radiod pCPUs $(cat $R/radiod/cpus_list) -> $rw/$ways ways (mask $radiod_mask)"
echo "sigmond-host-resctrl: others pCPUs $(cat $R/others/cpus_list) -> $((ways - rw))/$ways ways (mask $other_mask)"
[ -n "$decoder_mb" ] && echo "sigmond-host-resctrl: decoders pCPUs $(cat $R/decoders/cpus_list) -> others' ways, MB:0=$decoder_mb ($DECODER_MB_GBPS GB/s)"
exit 0

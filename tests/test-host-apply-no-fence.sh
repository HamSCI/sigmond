#!/bin/bash
# test-host-apply-no-fence.sh — a Proxmox host with no host-only core pair.
#
# A radiod-only Beelink (5560U, 6 cores) lays out three radiods with
# HOST_RESERVE_PAIRS=0: the VM gets every core and the host shares the VM's
# worker pair.  There is then no fence pair above the VM's affinity, and
# host-apply used to SKIP IRQ herding and the radiod-vm-fence, leaving host
# IRQs and QEMU's non-vCPU threads free to land on radiod's cores.  With no
# fence they must be herded onto the VM's WORKER pCPUs instead.
#
# host-apply's helpers are extracted from scripts/proxmox/host-apply.sh and
# run against stubbed systemctl, temp unit/sbin dirs and a stub radiod-vm-fence
# loop; nothing touches the real host.  Pass a different host-apply.sh as $1
# to run this against a mutant.
set -u
cd "$(dirname "$(readlink -f "$0")")/.."
HA="${1:-scripts/proxmox/host-apply.sh}"
[ -f "$HA" ] || { echo "FATAL: no host-apply.sh at $HA"; exit 1; }
PX="$(dirname "$HA")"
pass=0; fail=0
ok(){  printf '  PASS %s\n' "$1"; pass=$((pass+1)); }
bad(){ printf '  FAIL %s\n     %s\n' "$1" "$2"; fail=$((fail+1)); }
TMP=$(mktemp -d); trap 'kill "${SLEEPER:-}" 2>/dev/null; rm -rf "$TMP"' EXIT

python3 - "$HA" "$TMP/funcs.sh" <<'PYX'
import re, sys, pathlib
s = pathlib.Path(sys.argv[1]).read_text()
out = []
for name in ("cpus_to_range", "fence_cpus", "herd_cpus", "install_host_herding"):
    m = re.search(r"^%s\(\) \{\n.*?^\}\n" % name, s, re.S | re.M)
    if not m:
        raise SystemExit("could not extract %s() from host-apply.sh" % name)
    out.append(m.group(0))
pathlib.Path(sys.argv[2]).write_text("\n".join(out))
PYX
[ -s "$TMP/funcs.sh" ] || { echo "FAIL: host-apply.sh has no fence/herd helpers to test"; echo; echo "passed: 0   failed: 1"; exit 1; }

mkdir -p "$TMP/bin" "$TMP/units" "$TMP/sbin" "$TMP/src"
printf '#!/bin/bash\necho "systemctl $*" >> "$T/calls"\n' > "$TMP/bin/systemctl"
chmod +x "$TMP/bin/"*
for s in radiod-vm-fence.sh sigmond-host-irq-affinity.sh sigmond-host-resctrl.sh; do
    printf '#!/bin/bash\n: %s\n' "$s" > "$TMP/src/$s"
done
run(){ ( export T="$TMP" PATH="$TMP/bin:$PATH" SIGMOND_HOST_UNITS="$TMP/units" \
                SIGMOND_HOST_SBIN="$TMP/sbin" SIGMOND_HOST_SRC="$TMP/src"
         log(){ printf '[host-apply] %s\n' "$*"; }
         . "$TMP/funcs.sh"; "$@" ); }

echo "── cpus_to_range / fence_cpus ─────────────────────────────────────"
[ "$(run cpus_to_range "0 6 1 7 2 8 3 9 4 10")" = "0-4,6-10" ] && ok "split 5560U VM set -> 0-4,6-10 (what ISOLCPUS_RANGE was)" || bad "range" "got '$(run cpus_to_range "0 6 1 7 2 8 3 9 4 10")'"
[ "$(run cpus_to_range "0 1 2 3 4 5 6 7 8 9 10 11 12 13")" = "0-13" ] && ok "sequential 16-cpu VM set -> 0-13" || bad "range" "got '$(run cpus_to_range "$(seq -s' ' 0 13)")'"
[ "$(run cpus_to_range "0 2 3 4 7")" = "0,2-4,7" ] && ok "singletons and runs" || bad "range" "got '$(run cpus_to_range "0 2 3 4 7")'"
[ "$(run fence_cpus 0-13 16)" = "14-15" ] && ok "reserve 1, 16 cpus: fence 14-15 (unchanged)" || bad "fence" "got '$(run fence_cpus 0-13 16)'"
[ "$(run fence_cpus 0-4,6-10 12)" = "11-11" ] && ok "split 5560U reserve 1: fence 11-11 (unchanged)" || bad "fence" "got '$(run fence_cpus 0-4,6-10 12)'"
[ -z "$(run fence_cpus 0-11 12)" ] && ok "VM affinity covering every host CPU: no fence" || bad "fence" "got '$(run fence_cpus 0-11 12)'"
[ "$(run herd_cpus 14-15 "0 1 2 3")" = "14-15" ] && ok "a fence pair is still the herd target" || bad "herd" "got '$(run herd_cpus 14-15 "0 1 2 3")'"
[ "$(run herd_cpus "" "0 6 1 7 2 8")" = "0-2,6-8" ] && ok "no fence: herd onto the worker pCPUs" || bad "herd" "got '$(run herd_cpus "" "0 6 1 7 2 8")'"

echo "── no fence: IRQ unit + vm-fence written with the worker CPUs ─────"
# HOST_RESERVE_PAIRS=0 on the 5560U, three radiods: the VM's affinity covers
# every host CPU, so fence_cpus is empty and the worker pCPUs take the herd.
FENCE="$(run fence_cpus "$(run cpus_to_range "0 6 1 7 2 8 3 9 4 10 5 11")" 12)"
HERD="$(run herd_cpus "$FENCE" "0 6 1 7 2 8")"
out="$(run install_host_herding 100 "$HERD" "3,4,5,9,10,11" "3,4,5,9,10,11 0,1,2,6,7,8 0.62" "the VM's worker pCPUs")"
irq="$TMP/units/sigmond-host-irq-affinity.service"
grep -q "^ExecStart=$TMP/sbin/sigmond-host-irq-affinity.sh 0-2,6-8\$" "$irq" 2>/dev/null \
    && ok "IRQ unit herds host IRQs onto the worker CPUs 0-2,6-8" \
    || bad "IRQ unit missing or wrong target" "$(cat "$irq" 2>&1) // $out"
fen="$TMP/units/radiod-vm-fence.service"
grep -q "^ExecStart=$TMP/sbin/radiod-vm-fence.sh 100 0-2,6-8 30\$" "$fen" 2>/dev/null \
    && ok "radiod-vm-fence pins QEMU's non-vCPU threads onto 0-2,6-8" \
    || bad "vm-fence unit missing or wrong target" "$(cat "$fen" 2>&1) // $out"
grep -q "sigmond-host-resctrl.sh 3,4,5,9,10,11 0,1,2,6,7,8 " "$TMP/units/sigmond-host-resctrl.service" 2>/dev/null \
    && ok "L3 CAT unit still written" || bad "resctrl unit" "$(cat "$TMP/units/sigmond-host-resctrl.service" 2>&1)"
grep -q "enable --now sigmond-host-irq-affinity.service" "$TMP/calls" && grep -q "enable --now radiod-vm-fence.service" "$TMP/calls" \
    && ok "both units enabled" || bad "units not enabled" "$(cat "$TMP/calls" 2>&1)"
grep -q "SKIPPED" <<<"$out" && bad "still says SKIPPED" "$out" || ok "nothing SKIPPED"
[ -x "$TMP/sbin/radiod-vm-fence.sh" ] && [ -x "$TMP/sbin/sigmond-host-irq-affinity.sh" ] \
    && ok "helper scripts installed" || bad "helpers not installed" "$(ls "$TMP/sbin")"

echo "── with a fence pair the targets are unchanged ────────────────────"
rm -f "$TMP/units/"* "$TMP/calls"
run install_host_herding 100 14-15 "12,13" "12,13 0,1,2,3,4,5,6,7,8,9,10,11,14,15 0.62" "the host's own pair" >/dev/null
grep -q "sigmond-host-irq-affinity.sh 14-15\$" "$TMP/units/sigmond-host-irq-affinity.service" 2>/dev/null \
    && grep -q "radiod-vm-fence.sh 100 14-15 30\$" "$TMP/units/radiod-vm-fence.service" 2>/dev/null \
    && ok "fence 14-15 as before" || bad "fence targets changed" "$(cat "$TMP/units/"* 2>&1)"

echo "── host-apply's main body uses them ───────────────────────────────"
code=$(grep -vE '^[[:space:]]*#' "$HA")
grep -q 'install_host_herding "\$VMID"' <<<"$code" && ok "main body calls install_host_herding" || bad "install_host_herding not called" ""
grep -q 'qm set "\$VMID" --affinity "\$VM_AFFINITY"' <<<"$code" \
    && ok "qm affinity is the VM's whole set, not ISOLCPUS_RANGE" \
    || bad "qm --affinity still ISOLCPUS_RANGE" "with reserve 0, isolcpus is radiod's pairs only"

echo "── radiod-vm-fence: a target that holds cpu0 is not re-fenced forever ──"
# The fence used to re-taskset any thread that could still reach cpu0/cpu1.
# With the worker pCPUs as target (they always hold cpu0) it would re-fence
# every thread every interval.  It must touch only threads with a CPU outside
# the target.
sleep 30 & SLEEPER=$!
cat > "$TMP/bin/qm" <<EOF
#!/bin/bash
echo "VMID NAME STATUS MEM BOOTDISK PID"
echo "100 vm running 1 1 $SLEEPER"
EOF
cat > "$TMP/bin/taskset" <<'EOF'
#!/bin/bash
if [ "$1" = -p ]; then echo "pid $2's current affinity mask: $MASK"; else echo "taskset $*" >> "$T/fence-calls"; fi
EOF
chmod +x "$TMP/bin/"*
fence(){ rm -f "$TMP/fence-calls"; MASK="$1" T="$TMP" PATH="$TMP/bin:$PATH" timeout 2 bash "$PX/radiod-vm-fence.sh" 100 0-2,6-8 5 >/dev/null 2>&1; }
fence 1c7     # 0-2,6-8 exactly
[ ! -s "$TMP/fence-calls" ] && ok "a thread already on the target is left alone" || bad "re-fenced a thread already on target" "$(cat "$TMP/fence-calls")"
fence fff     # every cpu: reaches radiod's
grep -q "taskset -pc 0-2,6-8 " "$TMP/fence-calls" 2>/dev/null && ok "a thread that reaches radiod's CPUs is fenced" || bad "not fenced" "$(cat "$TMP/fence-calls" 2>&1)"

echo "── choose_host_reserve_pairs: env, then host-layout.env, then the role ──"
RC="$PX/radiod-count.sh"
pick(){ ( . "$RC"; choose_host_reserve_pairs "$@" ); }
[ "$(pick 0 1 full)" = "0|HOST_RESERVE_PAIRS" ] && ok "HOST_RESERVE_PAIRS wins" || bad "env" "got '$(pick 0 1 full)'"
[ "$(pick '' 0 full)" = "0|host-layout.env (phase 1)" ] && ok "a saved value is kept on re-run" || bad "saved" "got '$(pick '' 0 full)'"
[ "$(pick '' '' radiod-only)" = "0|the radiod-only station role" ] && ok "radiod-only role gives 0" || bad "role" "got '$(pick '' '' radiod-only)'"
[ "$(pick '' '' full)" = "1|the default" ] && ok "full role gives 1" || bad "full" "got '$(pick '' '' full)'"
[ "$(pick '' '' '')" = "1|the default" ] && ok "no role gives 1" || bad "none" "got '$(pick '' '' '')'"
pick -1 '' '' >/dev/null 2>&1 && bad "a negative HOST_RESERVE_PAIRS was accepted" "" || ok "a negative HOST_RESERVE_PAIRS is refused"
pick 'x' '' '' >/dev/null 2>&1 && bad "a non-numeric HOST_RESERVE_PAIRS was accepted" "" || ok "a non-numeric HOST_RESERVE_PAIRS is refused"

echo "── host-discover reports the saved value and the role ─────────────"
HD="$PX/host-discover.sh"
printf '#!/bin/bash\necho "04:00.3 USB controller [0c03]: Advanced Micro Devices, Inc. [AMD] Renoir/Cezanne USB 3.1 [1022:1639]"\n' > "$TMP/bin/lspci"; printf '#!/bin/bash\necho 12\n' > "$TMP/bin/nproc"; printf '#!/bin/bash\nexit 0\n' > "$TMP/bin/lsusb"
chmod +x "$TMP/bin/"*
printf 'LOCAL_RADIOD_COUNT=3\nHOST_RESERVE_PAIRS=0\n' > "$TMP/layout.env"
echo radiod-only > "$TMP/role"
SIGMOND_HOST_LAYOUT_ENV="$TMP/layout.env" SIGMOND_STATION_ROLE_FILE="$TMP/role" PATH="$TMP/bin:$PATH" bash "$HD" --no-vm > "$TMP/out" 2>/dev/null
val(){ ( eval "$(grep "^$1=" "$TMP/out")"; printf '%s' "${!1:-}" ); }
[ "$(val HOST_LAYOUT_RESERVE_PAIRS)" = "0" ] && ok "HOST_LAYOUT_RESERVE_PAIRS=0 read from host-layout.env" || bad "saved reserve" "got '$(val HOST_LAYOUT_RESERVE_PAIRS)'"
[ "$(val STATION_ROLE)" = "radiod-only" ] && ok "STATION_ROLE=radiod-only read from the host's record" || bad "role" "got '$(val STATION_ROLE)'"
printf 'HOST_RESERVE_PAIRS=$(touch %s/pwned)\n' "$TMP" > "$TMP/layout.env"; echo '$(touch pwned2)' > "$TMP/role"
SIGMOND_HOST_LAYOUT_ENV="$TMP/layout.env" SIGMOND_STATION_ROLE_FILE="$TMP/role" PATH="$TMP/bin:$PATH" bash "$HD" --no-vm > "$TMP/out" 2>/dev/null
[ -z "$(val HOST_LAYOUT_RESERVE_PAIRS)" ] && [ -z "$(val STATION_ROLE)" ] && [ ! -e "$TMP/pwned" ] \
    && ok "junk in either file is ignored, never run" || bad "junk" "$(cat "$TMP/out")"

echo "── host-setup and bootstrap hand the choice to the layout ─────────"
for f in host-setup.sh bootstrap.sh; do
    grep -q 'reserve_host_pairs=int(sys.argv\[3\])' "$PX/$f" && grep -q 'choose_host_reserve_pairs' "$PX/$f" \
        && ok "$f passes the chosen reserve to compute_host_cpu_layout" || bad "$f ignores HOST_RESERVE_PAIRS" ""
done
grep -q 'HOST_RESERVE_PAIRS=' "$PX/host-setup.sh" && ok "host-setup persists HOST_RESERVE_PAIRS" || bad "not persisted" ""

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ]

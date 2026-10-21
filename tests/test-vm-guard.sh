#!/bin/bash
# test-vm-guard.sh — sigmond-vm-guard, the boot-time stand-in for onboot=1.
#
# AC1SN (DASI-017, Ryzen 7 7735U), 2026-10-15: host-apply set onboot=1 in the
# same breath as handing the VM its USB controllers.  The host locked up
# seconds after every VM start, and onboot turned that into a crash-reboot
# loop that only GRUB surgery at the console broke.  host-apply now sets
# onboot=0 and installs sigmond-vm-guard, which starts the VM itself, refuses
# after two starts that never reached GUARD_SECS of uptime, and hands
# autostart back to Proxmox once the VM has stayed up.
#
# The guard is extracted verbatim from scripts/proxmox/host-apply.sh and run
# against stubbed qm / systemctl / logger.  Pass a different host-apply.sh as
# $1 to run this against a mutant.
set -u
cd "$(dirname "$(readlink -f "$0")")/.."
HA="${1:-scripts/proxmox/host-apply.sh}"
[ -f "$HA" ] || { echo "FATAL: no host-apply.sh at $HA"; exit 1; }
pass=0; fail=0
ok(){  printf '  ✓ %s\n' "$1"; pass=$((pass+1)); }
bad(){ printf '  ✗ %s\n     %s\n' "$1" "$2"; fail=$((fail+1)); }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

python3 - "$HA" "$TMP/guard" <<'PYX'
import re, sys, pathlib
s = pathlib.Path(sys.argv[1]).read_text()
m = re.search(r"cat > \"\$bin\" <<'GUARDEOF'\n(.*?)\nGUARDEOF\n", s, re.S)
if not m: raise SystemExit("could not extract sigmond-vm-guard")
pathlib.Path(sys.argv[2]).write_text(m.group(1) + "\n")
PYX
[ -s "$TMP/guard" ] || { echo "FATAL: extraction failed"; exit 1; }
chmod +x "$TMP/guard"

echo "── host-apply no longer sets onboot=1 at bind time ───────────────"
# host-apply's own code: comments out, and the guard script's text out (the
# guard itself sets onboot=1 -- that is its job, once the VM has proven itself).
code=$(sed "/<<'GUARDEOF'/,/^GUARDEOF\$/d" "$HA" | grep -vE '^[[:space:]]*#')
grep -qE 'qm set "\$VMID" --onboot 1' <<<"$code" \
    && bad "host-apply still sets onboot=1 when it binds the USB controllers" \
           "a host that dies on VM start crash-loops (AC1SN 2026-10-15)" \
    || ok "host-apply does not set onboot=1 at bind time"
grep -qE 'qm set "\$VMID" --onboot 0' <<<"$code" && grep -q 'install_vm_guard "\$VMID"' <<<"$code" \
    && ok "host-apply sets onboot=0 and installs the guard" \
    || bad "host-apply does not hand autostart to the guard" "onboot 0 + install_vm_guard expected"

# Stubs: qm reads its answers from files; every call is logged.
mkdir -p "$TMP/bin"
cat > "$TMP/bin/qm" <<'EOF'
#!/bin/bash
echo "qm $*" >> "$T/calls"
case "$1" in
  config) cat "$T/config" ;;
  status) cat "$T/status" ;;
  set)    [ "$3" = --onboot ] && printf 'onboot: %s\n' "$4" > "$T/config" ;;
  start)  : ;;
esac
EOF
printf '#!/bin/bash\necho "systemctl $*" >> "$T/calls"\n' > "$TMP/bin/systemctl"
printf '#!/bin/bash\n:\n' > "$TMP/bin/logger"
chmod +x "$TMP/bin/"*

run(){ # run <mode> <config-line> <status-text> <state-or-empty>
    rm -f "$TMP/calls"; : > "$TMP/calls"
    printf '%s\n' "$2" > "$TMP/config"
    printf '%b' "$3" > "$TMP/status"
    if [ -n "$4" ]; then echo "$4" > "$TMP/state"; else rm -f "$TMP/state"; fi
    T="$TMP" SIGMOND_GUARD_STATE="$TMP/state" PATH="$TMP/bin:$PATH" \
        "$TMP/guard" "$1" 100 > "$TMP/out" 2>&1
}
started(){ grep -q '^qm start 100' "$TMP/calls"; }
armed(){ grep -q '^qm set 100 --onboot 1' "$TMP/calls"; }
retired(){ grep -q 'disable --now sigmond-vm-guard.timer' "$TMP/calls"; }
STOPPED='status: stopped\n'
UP_LONG='status: running\nuptime: 700\n'
UP_SHORT='status: running\nuptime: 90\n'

echo "── boot: first and second start are made ──────────────────────────"
run boot 'onboot: 0' "$STOPPED" ""
started && [ "$(cat "$TMP/state")" = 1 ] && ok "first boot starts the VM and counts it (1)" \
    || bad "first boot did not start-and-count" "$(cat "$TMP/out")"
run boot 'onboot: 0' "$STOPPED" 1
started && [ "$(cat "$TMP/state")" = 2 ] && ok "second boot starts it again (2)" \
    || bad "second boot did not start-and-count" "$(cat "$TMP/out")"

echo "── boot: after two starts that never stayed up, it refuses ────────"
run boot 'onboot: 0' "$STOPPED" 2
! started && [ "$(cat "$TMP/state")" = 2 ] && grep -q 'NOT starting' "$TMP/out" \
    && ok "third boot does NOT start the VM and says why" \
    || bad "third boot started the VM anyway" "this is the crash loop"

echo "── check: a VM that stays up earns onboot=1 and the guard retires ─"
run check 'onboot: 0' "$UP_LONG" 1
armed && [ ! -e "$TMP/state" ] && retired && ok "uptime >= GUARD_SECS: onboot=1, count cleared, timer retired" \
    || bad "a proven VM was not given autostart" "$(cat "$TMP/calls")"
run check 'onboot: 0' "$UP_SHORT" 1
! armed && [ "$(cat "$TMP/state")" = 1 ] && ok "uptime < GUARD_SECS: nothing changes yet" \
    || bad "autostart granted before the VM proved itself" "$(cat "$TMP/calls")"

echo "── check never starts a VM; an armed host is left alone ───────────"
run check 'onboot: 0' "$STOPPED" 1
! started && ok "check mode does not start a stopped VM" || bad "check mode started the VM" "only boot mode may"
run boot 'onboot: 1' "$STOPPED" 2
! started && [ ! -e "$TMP/state" ] && retired && ok "onboot already 1: guard clears its count and retires" \
    || bad "guard interfered with a host that already autostarts" "$(cat "$TMP/calls")"
grep -q 'sigmond-vm-guard.service' "$TMP/calls" && ! grep -q 'disable --now sigmond-vm-guard.service' "$TMP/calls" \
    && ok "retire disables the boot service without stopping it (it may be running it)" \
    || bad "retire stops the boot service from inside itself" "use disable, not disable --now, for the .service"

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ]

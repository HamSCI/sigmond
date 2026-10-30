#!/bin/bash
# test-proxmox-wizard-radiod-count.sh — the Proxmox host counts the RX888s on
# its USB bus (lsusb 04b4:00f1 = firmware-loaded, 04b4:00f3 = bare/unloaded)
# and host-discover emits RX888_COUNT so host-setup and bootstrap can lay the
# VM out for that many radiods.  One card, no card, or no lsusb at all must
# still give 1, never 0.
#
# Runs the real scripts/proxmox/host-discover.sh --no-vm with lsusb, lspci and
# nproc stubbed.  Needs no root and never touches the real host.
set -u
cd "$(dirname "$(readlink -f "$0")")/.."
HD="${1:-scripts/proxmox/host-discover.sh}"
[ -f "$HD" ] || { echo "FATAL: no host-discover.sh at $HD"; exit 1; }
pass=0; fail=0
ok(){  printf '  PASS %s\n' "$1"; pass=$((pass+1)); }
bad(){ printf '  FAIL %s\n     %s\n' "$1" "$2"; fail=$((fail+1)); }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
printf '#!/bin/bash\ncat "$LSPCI_FIXTURE"\n' > "$TMP/bin/lspci"
printf '#!/bin/bash\necho 16\n' > "$TMP/bin/nproc"
chmod +x "$TMP/bin/"*
cat > "$TMP/pci" <<'EOF'
04:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Barcelo [1002:15e7] (rev c1)
04:00.3 USB controller [0c03]: Advanced Micro Devices, Inc. [AMD] Renoir/Cezanne USB 3.1 [1022:1639]
EOF

LAYOUT="$TMP/host-layout.env"
discover(){ SIGMOND_HOST_LAYOUT_ENV="$LAYOUT" LSPCI_FIXTURE="$TMP/pci" PATH="$TMP/bin:$PATH" bash "$HD" --no-vm 2>"$TMP/err" > "$TMP/out"; }
val(){ ( eval "$(grep "^$1=" "$TMP/out")"; printf '%s' "${!1:-}" ); }

echo "── two firmware-loaded + one bare RX888 -> 3 ──────────────────────"
cat > "$TMP/bin/lsusb" <<'EOF'
#!/bin/bash
[ "$1" = "-d" ] || exit 0
case "$2" in
  04b4:00f1) echo "Bus 001 Device 004: ID 04b4:00f1 Cypress"; echo "Bus 002 Device 005: ID 04b4:00f1 Cypress";;
  04b4:00f3) echo "Bus 003 Device 006: ID 04b4:00f3 Cypress";;
esac
EOF
chmod +x "$TMP/bin/lsusb"
discover
[ "$(val RX888_COUNT)" = "3" ] && ok "RX888_COUNT=3" || bad "expected 3, got '$(val RX888_COUNT)'" "$(cat "$TMP/err")"
[ "$(val RX888_VISIBLE)" = "3" ] && ok "RX888_VISIBLE=3" || bad "expected visible 3, got '$(val RX888_VISIBLE)'" "$(cat "$TMP/err")"
[ -z "$(val HOST_LAYOUT_RADIOD_COUNT)" ] && ok "no host-layout.env gives an empty HOST_LAYOUT_RADIOD_COUNT" || bad "expected empty, got '$(val HOST_LAYOUT_RADIOD_COUNT)'" "$(cat "$TMP/out")"

echo "── lsusb prints nothing -> 1 ──────────────────────────────────────"
printf '#!/bin/bash\nexit 0\n' > "$TMP/bin/lsusb"
discover
[ "$(val RX888_COUNT)" = "1" ] && ok "no cards seen gives 1" || bad "expected 1, got '$(val RX888_COUNT)'" "$(cat "$TMP/err")"
[ "$(val RX888_VISIBLE)" = "0" ] && ok "no cards seen gives RX888_VISIBLE=0" || bad "expected visible 0, got '$(val RX888_VISIBLE)'" "$(cat "$TMP/err")"

echo "── lsusb missing (exit 127) -> 1 ──────────────────────────────────"
printf '#!/bin/bash\nexit 127\n' > "$TMP/bin/lsusb"
discover
[ "$(val RX888_COUNT)" = "1" ] && ok "missing lsusb gives 1" || bad "expected 1, got '$(val RX888_COUNT)'" "$(cat "$TMP/err")"
[ "$(val RX888_VISIBLE)" = "0" ] && ok "missing lsusb gives RX888_VISIBLE=0" || bad "expected visible 0, got '$(val RX888_VISIBLE)'" "$(cat "$TMP/err")"

echo "── after the phase-1 reboot: no card on the host bus, host-layout.env says 2 ──"
printf '# written by sigmond host-setup.sh\nLOCAL_RADIOD_COUNT=2\nVM_CORES=6\n' > "$LAYOUT"
discover
[ "$(val HOST_LAYOUT_RADIOD_COUNT)" = "2" ] && ok "HOST_LAYOUT_RADIOD_COUNT=2 read from host-layout.env" || bad "expected 2, got '$(val HOST_LAYOUT_RADIOD_COUNT)'" "$(cat "$TMP/out")"
printf 'LOCAL_RADIOD_COUNT=$(touch %s/pwned)\n' "$TMP" > "$LAYOUT"
discover
[ -z "$(val HOST_LAYOUT_RADIOD_COUNT)" ] && [ ! -e "$TMP/pwned" ] && ok "a non-numeric host-layout.env value is ignored, never run" || bad "expected empty and no side effect" "$(cat "$TMP/out")"
rm -f "$LAYOUT"

echo "── choose_radiod_count: the largest of host-layout.env, host bus, guest bus ──"
RC="$(dirname "$HD")/radiod-count.sh"
pick(){ ( . "$RC"; choose_radiod_count "$@" ); }
[ "$(pick 2 0 '')" = "2|host-layout.env (phase 1)" ] && ok "host-layout.env wins when the host bus is empty" || bad "layout 2, visible 0" "got '$(pick 2 0 '')'"
[ "$(pick '' 3 '')" = "3|the host USB bus" ] && ok "host bus count used with no layout" || bad "visible 3" "got '$(pick '' 3 '')'"
[ "$(pick 1 0 2)" = "2|the guest USB bus" ] && ok "guest bus wins when it sees more" || bad "guest 2" "got '$(pick 1 0 2)'"
[ "$(pick '' 0 0)" = "1|the one-card default" ] && ok "nothing seen anywhere gives 1" || bad "nothing" "got '$(pick '' 0 0)'"
[ "$(pick 2 2 '')" = "2|host-layout.env (phase 1)" ] && ok "a tie keeps the first source" || bad "tie" "got '$(pick 2 2 '')'"

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ]

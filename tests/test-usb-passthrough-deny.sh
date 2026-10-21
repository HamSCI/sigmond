#!/bin/bash
# test-usb-passthrough-deny.sh — host-discover never hands the VM a USB
# controller that is known to hang the host, and still hands it every other one.
#
# AC1SN (DASI-017, Ryzen 7 7735U "Rembrandt"), 2026-10-15/16: the take-every-
# controller rule passed 1022:161d/161e (functions of the Radeon's PCI device;
# the host's amdgpu hard-locked) and 1022:161f (internal Bluetooth only; the
# host froze).  The station ran once the VM got only 15d6/15d7.
#
# The other direction matters as much: on the 5825U/5800H fleet (B4, ND) the
# 1022:1639 controllers ALSO sit on the GPU's PCI device and pass through fine,
# so they must still be taken.
#
# Runs the real scripts/proxmox/host-discover.sh --no-vm with lspci and nproc
# stubbed.  Pass a different host-discover.sh as $1 to run this against a mutant.
set -u
cd "$(dirname "$(readlink -f "$0")")/.."
HD="${1:-scripts/proxmox/host-discover.sh}"
[ -f "$HD" ] || { echo "FATAL: no host-discover.sh at $HD"; exit 1; }
pass=0; fail=0
ok(){  printf '  ✓ %s\n' "$1"; pass=$((pass+1)); }
bad(){ printf '  ✗ %s\n     %s\n' "$1" "$2"; fail=$((fail+1)); }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
printf '#!/bin/bash\ncat "$LSPCI_FIXTURE"\n' > "$TMP/bin/lspci"
printf '#!/bin/bash\necho 16\n' > "$TMP/bin/nproc"
chmod +x "$TMP/bin/"*

# lspci -nn as captured on each host (trimmed to the lines that matter).
cat > "$TMP/rembrandt" <<'EOF'
04:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Rembrandt [Radeon 680M] [1002:1681] (rev 08)
04:00.3 USB controller [0c03]: Advanced Micro Devices, Inc. [AMD] Rembrandt USB4 XHCI controller #3 [1022:161d]
04:00.4 USB controller [0c03]: Advanced Micro Devices, Inc. [AMD] Rembrandt USB4 XHCI controller #4 [1022:161e]
04:00.7 Signal processing controller [1180]: Advanced Micro Devices, Inc. [AMD] Sensor Fusion Hub [1022:15e4]
05:00.0 USB controller [0c03]: Advanced Micro Devices, Inc. [AMD] Rembrandt USB4 XHCI controller #8 [1022:161f]
05:00.3 USB controller [0c03]: Advanced Micro Devices, Inc. [AMD] Rembrandt USB4 XHCI controller #5 [1022:15d6]
05:00.4 USB controller [0c03]: Advanced Micro Devices, Inc. [AMD] Rembrandt USB4 XHCI controller #6 [1022:15d7]
EOF
cat > "$TMP/cezanne" <<'EOF'
04:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Barcelo [1002:15e7] (rev c1)
04:00.3 USB controller [0c03]: Advanced Micro Devices, Inc. [AMD] Renoir/Cezanne USB 3.1 [1022:1639]
04:00.4 USB controller [0c03]: Advanced Micro Devices, Inc. [AMD] Renoir/Cezanne USB 3.1 [1022:1639]
EOF

discover(){ LSPCI_FIXTURE="$TMP/$1" PATH="$TMP/bin:$PATH" bash "$HD" --no-vm 2>"$TMP/err" > "$TMP/out"; }
val(){ ( eval "$(grep "^$1=" "$TMP/out")"; printf '%s' "${!1}" ); }

echo "── Rembrandt (7735U, AC1SN): only the single-jack controllers ─────"
discover rembrandt
ids=$(val USB_VID_DID)
[ "$ids" = "1022:15d6,1022:15d7" ] && ok "passes exactly 1022:15d6,1022:15d7" \
    || bad "Rembrandt passthrough set is wrong: '$ids'" "$(cat "$TMP/err")"
for id in 1022:161d 1022:161e 1022:161f; do
    case ",$ids," in *",$id,"*) bad "$id would be passed through" "this is what hung AC1SN";;
                     *) ok "$id is not passed through";; esac
    grep -q "$id" <<<"$(val USB_SKIPPED)" && ok "$id is reported in USB_SKIPPED" \
        || bad "$id dropped silently" "the operator must be told which controllers stay on the host"
done

echo "── Cezanne/Barcelo (5825U, B4): unchanged, still passed ───────────"
discover cezanne
ids=$(val USB_VID_DID)
[ "$ids" = "1022:1639" ] && ok "passes 1022:1639 (shares PCI device 04:00 with the GPU, and that is fine there)" \
    || bad "the proven fleet's controller is no longer passed: '$ids'" "$(cat "$TMP/err")"
[ "$(val USB_ADDRS_FOR_ID)" = "04:00.3 04:00.4" ] && ok "both 1639 functions are bound" \
    || bad "1639 addresses wrong: '$(val USB_ADDRS_FOR_ID)'" ""

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ]

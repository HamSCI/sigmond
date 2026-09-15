#!/bin/bash
# The preflight's device reporting, extracted verbatim from
# scripts/proxmox/sigmond-wizard.sh.  What matters is that the three states
# stay distinct: absent, present-and-reachable, present-but-on-a-controller
# the VM will never get.  Conflating the last two is the bug that made a
# working RX888 read as "NOT DETECTED".
set -u

# --- verbatim from the wizard, with the two lookups stubbed per case ---
_dev_line() {
    local ok="$1" label="$2" id="$3" note="$4" ctrl
    if [ "$ok" != 1 ]; then
        printf '  ✗ %-22s — NOT DETECTED (%s)\n' "$label" "$note"
        return
    fi
    if [ "$PREFLIGHT_SRC" != "host" ]; then
        printf '  ✓ %s\n' "$label"
        return
    fi
    ctrl=$(_usb_ctrl "$id")
    if [ -z "$ctrl" ]; then
        printf '  ✓ %s\n' "$label"
        return
    fi
    _ctrl_passed "$ctrl"; local rc=$?
    if [ "$rc" = 0 ]; then
        printf '  ✓ %-22s (USB controller %s → passed to the VM)\n' "$label" "${ctrl#0000:}"
    elif [ "$rc" = 2 ]; then
        printf '  ✓ %-22s (USB controller %s)\n' "$label" "${ctrl#0000:}"
    else
        printf '  ! %-22s on USB controller %s, which is NOT passed to the VM\n' "$label" "${ctrl#0000:}"
        printf '      the VM will not see it. Move it to a port on a passed-through\n'
        printf '      controller (usually the REAR USB ports), then: sigmond-setup --reconfigure\n'
        PREFLIGHT_WRONG_CTRL=1
    fi
}

fail=0
ck() { # ck <desc> <expected-marker> <actual-first-line>
    case "$3" in
        "$2"*) printf '  ok   %s\n' "$1" ;;
        *)     printf '  FAIL %s\n       got: %s\n' "$1" "$3"; fail=1 ;;
    esac
}

echo "host-side reporting:"
PREFLIGHT_SRC=host; PREFLIGHT_WRONG_CTRL=0

_usb_ctrl() { echo ""; }; _ctrl_passed() { return 1; }
ck "absent device says NOT DETECTED" "  ✗" "$(_dev_line 0 'RX888 SDR' '04b4:00f1' 'no HF reception')"

_usb_ctrl() { echo "0000:05:00.4"; }; _ctrl_passed() { return 0; }
ck "present on a passed controller" "  ✓ RX888 SDR              (USB controller 05:00.4 → passed" \
   "$(_dev_line 1 'RX888 SDR' '04b4:00f1' 'x')"

_usb_ctrl() { echo "0000:05:00.3"; }; _ctrl_passed() { return 1; }
# Redirect rather than $(...): the wizard calls _dev_line in the CURRENT
# shell, and a command substitution would fork a subshell where the summary
# flag could not survive — testing the wrong thing.
tmp=$(mktemp)
_dev_line 1 'RX888 SDR' '04b4:00f1' 'x' > "$tmp"
out=$(cat "$tmp"); rm -f "$tmp"
ck "present on a NON-passed controller warns" "  ! RX888 SDR" "$(echo "$out" | head -1)"
case "$out" in *"REAR USB ports"*) printf '  ok   warning says what to do\n';; *) printf '  FAIL warning lacks remedy\n'; fail=1;; esac
[ "$PREFLIGHT_WRONG_CTRL" = 1 ] && printf '  ok   sets the summary flag\n' || { printf '  FAIL summary flag unset\n'; fail=1; }

_usb_ctrl() { echo "0000:05:00.4"; }; _ctrl_passed() { return 2; }
ck "unknown layout does not claim passthrough" "  ✓ RX888 SDR              (USB controller 05:00.4)" \
   "$(_dev_line 1 'RX888 SDR' '04b4:00f1' 'x')"

echo "VM-side reporting (controllers already handed over):"
PREFLIGHT_SRC=vm
_usb_ctrl() { echo "SHOULD-NOT-BE-CALLED"; }; _ctrl_passed() { return 1; }
ck "seeing it from the VM is proof enough" "  ✓ RX888 SDR" "$(_dev_line 1 'RX888 SDR' '04b4:00f1' 'x')"
ck "absent still reported" "  ✗" "$(_dev_line 0 'GPSDO' '1dd2:2444' 'timing falls back to NTP')"

echo
[ "$fail" -eq 0 ] && echo "PREFLIGHT REPORTING OK" || echo "PREFLIGHT REPORTING FAILED"
exit $fail

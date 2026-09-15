#!/bin/bash
# The derivation that decides which VPN gateway a station lands on.
# A false positive sends an ordinary station to HamSCI with a colliding RAC;
# a false negative is what put W3USR_9/W3USR_10 on gw2 as RAC 506/507.
set -u
say() { :; }
RAC_PROFILE=dasi
build_rac_tiers() { :; }

derive() {   # derive <designator> <reporter> <profile> -> prints resulting DASI_NUM
    DES="$1"; REPORTER="$2"; RAC_PROFILE="$3"; DASI_NUM=""
    [ "$RAC_PROFILE" = "dasi" ] || { echo ""; return; }
    local _n
    for _cand in "${DES:-}" "${REPORTER:-}"; do
        [ -n "$_cand" ] || continue
        _n=$(printf '%s' "$_cand" | tr 'a-z' 'A-Z' | sed -nE \
             -e 's/^DASI2[-_ ]([0-9]{1,3})$/\1/p' \
             -e 's/^DASI[-_ ]([0-9]{1,3})$/\1/p' \
             -e 's/^DASI([0-9]{3})$/\1/p')
        [ -n "$_n" ] && _n=$((10#$_n))
        if [ -n "$_n" ] && [ "$_n" -ge 1 ] 2>/dev/null && [ "$_n" -le 99 ]; then
            echo "$_n"; return
        fi
    done
    echo ""
}

fail=0
ck() { r=$(derive "$1" "$2" "${4:-dasi}"); if [ "$r" = "$3" ]; then printf '  ok   %-14s -> %-4s\n' "$1" "${r:-(none)}"; else printf '  FAIL %-14s -> %-4s (wanted %s)\n' "$1" "${r:-(none)}" "${3:-(none)}"; fail=1; fi; }

echo "should derive:"
ck "DASI-003"   ""  "3"
ck "DASI003"    ""  "3"
ck "DASI2-01"   ""  "1"
ck "DASI-099"   ""  "99"
ck "dasi-007"   ""  "7"
ck "DASI 42"    ""  "42"
ck "DASI2_15"   ""  "15"

echo "must NOT derive (ordinary stations):"
ck ""           "W3USR_9"  ""
ck "W3USR-10"   ""         ""
ck "AC0G-B4"    ""         ""
ck "AI6VN"      ""         ""
ck "DASI"       ""         ""
ck "DASI2"      ""         ""
ck "DASI-000"   ""         ""
ck "DASI-100"   ""         ""
ck "MYDASI-3"   ""         ""
ck "DASI-3X"    ""         ""
ck "DASI42"     ""         ""
ck "DASI-008"   ""         "8"
ck "DASI008"    ""         "8"

echo "operator said not-DASI: never override"
ck "DASI-003"   ""         ""   standard

echo
[ "$fail" -eq 0 ] && echo "DASI DERIVATION OK" || echo "DASI DERIVATION FAILED"
exit $fail

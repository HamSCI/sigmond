#!/bin/bash
# test-site-timing-t5.sh -- step 4.8 enables T5 for an LBE-1421 and restarts the
# recorder AND the authority manager (the authority builds its T5 probe from
# lb1421_enabled at start).  Stubbed systemctl; no root.
set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
awk '/^# ── 4\.8 precision timing tiers/{f=1} /^# ── 4\.9 T6 from a TS-1/{f=0} f' \
    "$HERE/../bin/sigmond-site-timing" | sed -e "s#/run/gpsdo/#$T/gpsdo/#g" > "$T/step.sh"
grep -q 'TCHANGED' "$T/step.sh" || { echo "FAIL: step 4.8 not found"; exit 1; }
mkdir -p "$T/bin" "$T/gpsdo"
printf '#!/bin/bash\necho "$*" >> %s/systemctl.log\nexit 0\n' "$T" > "$T/bin/systemctl"; chmod +x "$T/bin/systemctl"
printf '{"device":{"model":"LBE-1421"}}' > "$T/gpsdo/a.json"
printf '[timing]\nlb1421_enabled = false\n' > "$T/cfg.toml"
PATH="$T/bin:$PATH" CFG="$T/cfg.toml" bash -c "say(){ echo \"\$*\" >> $T/say.log; }; source $T/step.sh"
grep -q 'lb1421_enabled = true' "$T/cfg.toml" || { echo "  FAIL: T5 not enabled"; fail=1; }
if grep -qx 'try-restart timestd-core-recorder timestd-authority-manager' "$T/systemctl.log" 2>/dev/null; then
    echo "  ok   T5 enable restarts recorder and authority manager"
else echo "  FAIL T5 restart: $(cat "$T/systemctl.log" 2>/dev/null)"; fail=1; fi
grep -q 'timestd-authority-manager' "$T/say.log" 2>/dev/null || { echo "  FAIL: say text omits the authority"; fail=1; }
exit $fail

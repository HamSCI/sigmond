#!/bin/bash
# test-install-frpc-guard.sh — install.sh never touches a box another frpc
# already serves.
#
# install.sh provisions sigmond-rac (frpc + wd-rac.service).  The first real
# radiod-only target, KFS-NW's Beelink, runs wsprdaemon and is reachable ONLY
# through wsprdaemon's own RAC tunnel (wd-rac-client: frpc processes,
# wd-remote-access@<gw>.service, /etc/wd-remote-access).  Replacing that frpc
# would strand the box.  So install.sh skips the whole RAC step, saying why,
# when an frpc it did not start is running, a foreign frpc/RAC unit is active,
# or frp config it did not write exists.  sigmond-rac's own pieces
# (/etc/sigmond/frpc*.toml, wd-rac.service running /etc/sigmond/frpc.toml,
# sigmond-rac-*.service) do not count.
#
# The RAC block is extracted from install.sh and run with stubbed pgrep and
# systemctl, a temp /etc root (SIGMOND_ETC_ROOT) and a fake sigmond-rac
# installer (SIGMOND_RAC_INSTALL).  Pass a different install.sh as $1 to run
# this against a mutant.
set -u
cd "$(dirname "$(readlink -f "$0")")/.."
IS="${1:-install.sh}"
[ -f "$IS" ] || { echo "FATAL: no install.sh at $IS"; exit 1; }
pass=0; fail=0
ok(){  printf '  PASS %s\n' "$1"; pass=$((pass+1)); }
bad(){ printf '  FAIL %s\n     %s\n' "$1" "$2"; fail=$((fail+1)); }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

python3 - "$IS" "$TMP/rac.sh" <<'PYX'
import re, sys, pathlib
s = pathlib.Path(sys.argv[1]).read_text()
f = re.search(r"^_foreign_frpc\(\) \{\n.*?^\}\n", s, re.S | re.M)
b = re.search(r"^# ─── RAC — admin remote-access.*?(?=^# ─── done)", s, re.S | re.M)
if not f or not b:
    raise SystemExit("could not extract _foreign_frpc() and the RAC block")
pathlib.Path(sys.argv[2]).write_text(f.group(0) + "\n" + b.group(0))
PYX
[ -s "$TMP/rac.sh" ] || { echo "FAIL: install.sh has no _foreign_frpc guard to test"; echo; echo "passed: 0   failed: 1"; exit 1; }

mkdir -p "$TMP/bin"
# pgrep -a -x frpc: prints $T/procs (pid + command line per line)
cat > "$TMP/bin/pgrep" <<'EOF'
#!/bin/bash
[ -s "$T/procs" ] || exit 1
cat "$T/procs"
EOF
# systemctl list-units: prints $T/units; systemctl cat <unit>: $T/unitfile.<unit>
cat > "$TMP/bin/systemctl" <<'EOF'
#!/bin/bash
case "$1" in
  list-units) cat "$T/units" 2>/dev/null ;;
  cat)        cat "$T/unitfile.$2" 2>/dev/null || exit 1 ;;
esac
EOF
cat > "$TMP/fake-rac-install.sh" <<'EOF'
#!/bin/bash
echo ran >> "$T/rac-ran"
mkdir -p "$SIGMOND_ETC_ROOT/etc/frp"; echo "sigmond wrote this" > "$SIGMOND_ETC_ROOT/etc/frp/frpc.toml"
EOF
chmod +x "$TMP/bin/"* "$TMP/fake-rac-install.sh"

reset(){ rm -rf "$TMP/root" "$TMP/procs" "$TMP/units" "$TMP/unitfile."* "$TMP/rac-ran"; mkdir -p "$TMP/root/etc/sigmond"; }
run(){ ( export T="$TMP" PATH="$TMP/bin:$PATH" SIGMOND_ETC_ROOT="$TMP/root" SIGMOND_RAC_INSTALL="$TMP/fake-rac-install.sh"
         SUDO=""; info(){ echo "[sigmond] $*"; }; ok(){ echo "[  ok  ] $*"; }; warn(){ echo "[ warn ] $*"; }
         . "$TMP/rac.sh" ) > "$TMP/out" 2>&1; }
skipped(){ # $1 = name
    if [ ! -e "$TMP/rac-ran" ] && grep -qi "skip" "$TMP/out"; then ok "$1"; else bad "$1" "$(cat "$TMP/out")"; fi; }

echo "── the brief's case: frpc on PATH + frpc.service running ──────────"
reset
mkdir -p "$TMP/root/etc/frp"; echo "wsprdaemon's" > "$TMP/root/etc/frp/frpc.toml"
before=$(cd "$TMP/root/etc/frp" && ls -la --time-style=+%s . && md5sum *)
echo "4242 /usr/bin/frpc -c /etc/frp/frpc.toml" > "$TMP/procs"
echo "frpc.service loaded active running FRP client" > "$TMP/units"
run
skipped "RAC provisioning skipped"
after=$(cd "$TMP/root/etc/frp" && ls -la --time-style=+%s . && md5sum *)
[ "$before" = "$after" ] && ok "/etc/frp untouched" || bad "/etc/frp changed" "$after"
grep -q "frpc" "$TMP/out" && ok "the info line says why" || bad "no reason given" "$(cat "$TMP/out")"

echo "── each trigger alone ─────────────────────────────────────────────"
reset; echo "917 /home/wsprdaemon/bin/frpc -c frpc.ini" > "$TMP/procs"; run
skipped "a running frpc process alone"
reset; echo "wd-remote-access@gw1.service loaded active running WD RAC" > "$TMP/units"; run
skipped "wsprdaemon's wd-remote-access@gw1.service alone"
reset; echo "wd-rac-client.service loaded active running x" > "$TMP/units"; run
skipped "an active wd-rac* unit alone"
reset; echo "myrac.service loaded active running x" > "$TMP/units"; run
skipped "an active *rac* unit alone"
reset; printf 'frpc.service loaded active running x\n' > "$TMP/units"; run
skipped "an active *frpc* unit alone"
reset; echo "wd-rac.service loaded active running x" > "$TMP/units"
printf '[Service]\nExecStart=/opt/wd/frpc -c /opt/wd/frpc.toml\n' > "$TMP/unitfile.wd-rac.service"; run
skipped "a wd-rac.service that is not sigmond-rac's"
reset; mkdir -p "$TMP/root/etc/frp"; run
skipped "/etc/frp alone"
reset; touch "$TMP/root/etc/frpc.ini"; run
skipped "/etc/frpc.ini alone"
reset; mkdir -p "$TMP/root/etc/wd-remote-access"; run
skipped "/etc/wd-remote-access alone"

echo "── sigmond-rac's own pieces do not block a re-install ─────────────"
reset
echo "555 /usr/local/sbin/frpc -c /etc/sigmond/frpc.toml" > "$TMP/procs"
printf '%s\n' "wd-rac.service loaded active running WsprDaemon Remote Access Channel (frpc tunnel)" \
              "sigmond-rac-host.service loaded active running x" "sigmond-rac-register.service loaded active exited x" \
              "ssh.service loaded active running x" > "$TMP/units"
printf '[Service]\nExecStart=/usr/local/sbin/frpc -c /etc/sigmond/frpc.toml\n' > "$TMP/unitfile.wd-rac.service"
touch "$TMP/root/etc/sigmond/frpc.toml" "$TMP/root/etc/sigmond/frpc.toml.template"
run
[ -e "$TMP/rac-ran" ] && ok "sigmond-rac's own frpc/units/config: RAC installed" || bad "blocked by its own files" "$(cat "$TMP/out")"

echo "── a clean box gets RAC ───────────────────────────────────────────"
reset; echo "ssh.service loaded active running x" > "$TMP/units"; run
[ -e "$TMP/rac-ran" ] && ok "nothing foreign: RAC installed" || bad "clean box skipped" "$(cat "$TMP/out")"

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ]

#!/bin/bash
# test-install-frpc-guard.sh — install.sh never touches a box another frpc
# already serves.
#
# install.sh provisions sigmond-rac (frpc + wd-rac.service).  The first real
# radiod-only target, KFS-NW's Beelink, runs wsprdaemon and is reachable ONLY
# through wsprdaemon's own RAC tunnel (wd-rac-client: frpc processes,
# wd-remote-access@<gw>.service, /etc/wd-remote-access).  Replacing that frpc
# would strand the box.  So install.sh skips the whole RAC step, saying why,
# when an frpc it did not start is running, a foreign frpc/RAC unit is
# active, activating or failed, a wd-rac.service on disk is not sigmond-rac's,
# /usr/local/sbin/frpc stands with none of sigmond-rac's files, or frp config
# it did not write exists; and always under SIGMOND_SKIP_RAC=1.  sigmond-rac's own pieces
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
f = re.search(r"^_sigmond_rac_footprint\(\) \{\n.*?^\}\n\n_foreign_frpc\(\) \{\n.*?^\}\n", s, re.S | re.M)
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
  list-units) echo "$*" > "$T/listargs"; cat "$T/units" 2>/dev/null ;;
  cat)        cat "$T/unitfile.$2" 2>/dev/null || exit 1 ;;
esac
EOF
cat > "$TMP/fake-rac-install.sh" <<'EOF'
#!/bin/bash
echo ran >> "$T/rac-ran"
mkdir -p "$SIGMOND_ETC_ROOT/etc/frp"; echo "sigmond wrote this" > "$SIGMOND_ETC_ROOT/etc/frp/frpc.toml"
EOF
chmod +x "$TMP/bin/"* "$TMP/fake-rac-install.sh"

reset(){ rm -rf "$TMP/root" "$TMP/procs" "$TMP/units" "$TMP/unitfile."* "$TMP/rac-ran" "$TMP/listargs"; mkdir -p "$TMP/root/etc/sigmond"; }
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

echo "── fix round: wd-rac.service on disk, a bare frpc, unit states ───"
reset; mkdir -p "$TMP/root/etc/systemd/system"
printf '[Service]\nExecStart=/opt/wd/frpc -c /opt/wd/frpc.toml\n' > "$TMP/root/etc/systemd/system/wd-rac.service"
run
skipped "an INACTIVE /etc/systemd/system/wd-rac.service that is not sigmond-rac's"
grep -q "wd-rac.service exists" "$TMP/out" && ok "  ...and the line names it" || bad "no wd-rac reason" "$(cat "$TMP/out")"
reset; mkdir -p "$TMP/root/etc/systemd/system"
printf '[Service]\n# was: ExecStart=/etc/sigmond/frpc\nExecStart=/opt/wd/frpc -c x\n' > "$TMP/root/etc/systemd/system/wd-rac.service"
run
skipped "a wd-rac.service naming /etc/sigmond/frpc only in a comment"
reset; mkdir -p "$TMP/root/etc/systemd/system"
printf '[Service]\nExecStart=/usr/local/sbin/frpc -c /etc/sigmond/frpc.toml\n' > "$TMP/root/etc/systemd/system/wd-rac.service"
touch "$TMP/root/etc/sigmond/frpc.toml"
run
[ -e "$TMP/rac-ran" ] && ok "sigmond-rac's own wd-rac.service on disk: RAC installed" || bad "blocked by its own unit file" "$(cat "$TMP/out")"
reset; mkdir -p "$TMP/root/usr/local/sbin"; touch "$TMP/root/usr/local/sbin/frpc"; run
skipped "/usr/local/sbin/frpc with no /etc/sigmond/frpc*.toml"
reset; mkdir -p "$TMP/root/usr/local/sbin"; touch "$TMP/root/usr/local/sbin/frpc" "$TMP/root/etc/sigmond/frpc.toml"; run
[ -e "$TMP/rac-ran" ] && ok "/usr/local/sbin/frpc beside /etc/sigmond/frpc.toml: RAC installed" || bad "blocked by its own frpc binary" "$(cat "$TMP/out")"
reset; echo "wd-remote-access@gw1.service loaded failed failed WD RAC" > "$TMP/units"; run
skipped "a FAILED wd-remote-access@gw1.service"
grep -q "is failed" "$TMP/out" && ok "  ...and the line says failed" || bad "state not named" "$(cat "$TMP/out")"
reset; echo "frpc.service loaded activating auto-restart x" > "$TMP/units"; run
skipped "an ACTIVATING frpc.service"
reset; run
grep -q -- "--state=active,activating,failed" "$TMP/listargs" && ok "units listed in active, activating and failed states" \
    || bad "list-units states" "$(cat "$TMP/listargs" 2>/dev/null)"
reset; echo "ssh.service loaded active running x" > "$TMP/units"
( export SIGMOND_SKIP_RAC=1; run )
if [ ! -e "$TMP/rac-ran" ] && grep -q "SIGMOND_SKIP_RAC=1" "$TMP/out"; then ok "SIGMOND_SKIP_RAC=1 on a clean box: skipped, with an info line"
else bad "SIGMOND_SKIP_RAC=1 ignored" "$(cat "$TMP/out")"; fi
reset; echo "ssh.service loaded active running x" > "$TMP/units"
( export SIGMOND_SKIP_RAC=0; run )
[ -e "$TMP/rac-ran" ] && ok "SIGMOND_SKIP_RAC=0: RAC installed" || bad "SIGMOND_SKIP_RAC=0 skipped" "$(cat "$TMP/out")"

echo "── sigmond-rac's own UNCONFIGURED install (no frpc.toml yet) ──────"
# What sigmond-rac/install.sh leaves before the operator activates RAC:
# frpc, the template, the CA, the station key, an inert wd-rac.service.
reset; mkdir -p "$TMP/root/usr/local/sbin" "$TMP/root/etc/systemd/system"
touch "$TMP/root/usr/local/sbin/frpc" "$TMP/root/etc/sigmond/frpc.toml.template" \
      "$TMP/root/etc/sigmond/frps-ca.crt" "$TMP/root/etc/sigmond/frpc_id_rsa"
printf '[Service]\nExecStart=/usr/local/sbin/frpc -c /etc/sigmond/frpc.toml\n' > "$TMP/root/etc/systemd/system/wd-rac.service"
echo "ssh.service loaded active running x" > "$TMP/units"; run
[ -e "$TMP/rac-ran" ] && ok "unconfigured sigmond-rac install: RAC re-run, not skipped" || bad "own unconfigured install read as foreign" "$(cat "$TMP/out")"
for f in frpc.toml.template frps-ca.crt frpc_id_rsa; do
    reset; mkdir -p "$TMP/root/usr/local/sbin"; touch "$TMP/root/usr/local/sbin/frpc" "$TMP/root/etc/sigmond/$f"; run
    [ -e "$TMP/rac-ran" ] && ok "/usr/local/sbin/frpc beside /etc/sigmond/$f alone: ours" || bad "$f not counted as sigmond-rac's" "$(cat "$TMP/out")"
done
reset; mkdir -p "$TMP/root/usr/local/sbin" "$TMP/root/etc/systemd/system"; touch "$TMP/root/usr/local/sbin/frpc"
printf '[Service]\nExecStart=/usr/local/sbin/frpc -c /etc/sigmond/frpc.toml\n' > "$TMP/root/etc/systemd/system/wd-rac.service"; run
[ -e "$TMP/rac-ran" ] && ok "/usr/local/sbin/frpc beside sigmond-rac's wd-rac.service alone: ours" || bad "own unit not counted" "$(cat "$TMP/out")"

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

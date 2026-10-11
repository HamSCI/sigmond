#!/bin/bash
# sigmond-wizard — first-boot site-identity wizard for the Sigmond appliance.
# Runs on the Proxmox HOST console (tty1 via sigmond-wizard.service, or
# rerun any time as `sigmond-setup`).  Prompts for the few per-site facts,
# pushes them into the decoder VM via the qemu guest agent, and
# activates the host-side RAC tunnel.
#
# Prompts: reporter ID (required)   e.g. AC0G/B4 — drives ALL upload paths
#          grid square (required)   e.g. EM38ww
#          antenna     (optional)   free text
#          station class — DASI or not, and the DASI unit number.  It picks
#              the gateway ladder for remote access.
#
# Remote access (RAC) carries no question.  Every install registers with the
#          gateway and brings the tunnel up (tasks/plan-remote-access.md).
#          The registrar ($RAC_SERVER, configurable via SIGMOND_RAC_SERVER/
#          _REGISTRAR) assigns a free RAC number, and ONE host-side frpc
#          carries six channels: VM ssh (35800+n), VM ka9q-web (45800+n),
#          station-web (48800+n), magnetometer (49800+n), host ssh (50800+n)
#          and the Proxmox UI (55800+n).  The vm-* channels go through local
#          relays, so the host stays reachable even when the VM is down.
#          The operator holds the switch, in the decoder VM: smd remote off,
#          smd remote on, smd remote status.
#
# Subcommands: --reconfigure   rerun the wizard (RAC number is sticky)
#              --rac-off       turn the remote-access tunnel off (config kept)
#              --rac-on        turn it on again
#                              (both run /usr/local/sbin/sigmond-rac-switch,
#                              the one switch; the decoder VM reaches the
#                              same script with `smd remote`)
#              --rac-register  register with the gateway and write the tunnel
#                              configuration; asks nothing, starts nothing
#              --rac-retry     what sigmond-rac-register.timer runs: register a
#                              station that could not at install, then bring
#                              the tunnel up if its operator still wants it up
#              --pair-vm       pair the decoder VM with this host, so that
#                              `smd remote on|off|status` works there; also
#                              re-asserts a pairing that stands.  It records
#                              its outcome in /etc/sigmond-appliance/rac-pairing,
#                              which the console panel reads
#              --rac-upgrade   climb to a more secure gateway tier if one
#                              has since come up (see the RAC ladder below)
set -u

# ── which VM are we configuring? ────────────────────────────────────────────
# ⛔ NEVER fall straight back to a hardcoded id.  firstboot's own units carry
# `Environment=SIGMOND_VMID=<n>`, so the wizard gets the right VM when systemd
# starts it — but an operator running `sigmond-setup --reconfigure` from a root
# shell has no such environment.  The old default of 120 then made the wizard
# wait five minutes for a VM that does not exist, printing nothing, and read as
# a hang (rob, AI6VN-PM 2026-09-29, where the VM is 100).
#
# So DISCOVER it, most authoritative source first, falling back to the v3
# fleet convention only when nothing on the host can answer.
resolve_vmid(){
    [ -n "${SIGMOND_VMID:-}" ] && { printf '%s\n' "$SIGMOND_VMID"; return; }
    local v
    # what firstboot recorded for itself — the same number its units use
    v=$(sed -n 's/^Environment=SIGMOND_VMID=\([0-9][0-9]*\).*/\1/p' \
        "${SIGMOND_IMPORT_UNIT:-/etc/systemd/system/sigmond-import.service}" \
        2>/dev/null | head -1)
    [ -n "$v" ] && { printf '%s\n' "$v"; return; }
    # exactly one VM on this host is unambiguous; more than one is not, so
    # do not guess — fall through and let the wait loop report it.
    v=$(qm list 2>/dev/null | awk 'NR>1 && $1 ~ /^[0-9]+$/ {print $1}')
    if [ "$(printf '%s\n' "$v" | grep -c .)" = 1 ] && [ -n "$v" ]; then
        printf '%s\n' "$v"; return
    fi
    # ⛔ 100, not 120.  The decoder VM is VMID 100 by v3 fleet convention; the
    # wizard's historical default was 120, and build-usb-v3.sh sed-rewrote
    # `SIGMOND_VMID:-120` to `:-100` on its way onto the stick to correct it.
    # Refactoring this into a resolver removed that literal, so the sed
    # silently stopped matching and the build warned "wizard still mentions
    # 120 somewhere".  Carrying the right number here is better than relying
    # on a rewrite that a refactor can quietly disarm.
    printf '100\n'
}
VMID="$(resolve_vmid)"
# ── RAC endpoint ladder ────────────────────────────────────────────────
# Two policies, split on whether this is a DASI station (rob 2026-08-09):
#
#   dasi      Prefer the HamSCI gateway, and rather than finish with no
#             remote access at all, walk all the way down to an unsecured
#             tunnel.  These boxes sit in a physically secure location, and
#             a station nobody can reach is a station nobody can support.
#   standard  Ordinary WsprDaemon-group installs never touch vpn.hamsci.org
#             and are secure-only — no unsecured fallback, ever.
#
# I flagged that silently downgrading to an unsecured transport is a genuine
# weakening; rob scoped it to DASI deliberately rather than dropping it, and
# every tier below announces itself on the console and in $LOG.
#
# Why a ladder at all: vpn.hamsci.org's secure stack has never answered, so
# EVERY greenfield install since 2026-07-30 finished with RAC dead and nobody
# noticed until someone needed remote support (found on B4, 2026-08-06).
#
# What secure/unsecured actually MEANS (mjh 2026-08-09, correcting the
# v3.25/v3.26 guess): it is the frps TUNNEL port —
#   secure    = TLS frps on 35736 + frpc [transport.tls] with the pinned CA
#   unsecured = legacy plain frps on 35735 + TLS off
# The REGISTRAR (account-creation API) is a separate, tier-independent thing:
# plain http on 35737, the only registrar either gateway serves.  v3.25/26
# implemented "secure" as an https registrar on 35737 — nothing serves that,
# so every install quietly fell through to an unsecured rung.
#
# Tier fields:  label|server|registrar|tls|frps_port
#
# DASI stations get a REGISTRAR-LESS rung (rob 2026-08-09): both gateways
# keep their registration pages WireGuard-only by policy (exposing them
# publicly is a hole rob won't open — gw2's :35737 is firewalled to known
# sites), so a greenfield DASI box cannot self-register with the HamSCI
# gateway.  Instead DASI-N maps DETERMINISTICALLY to RAC 220+N (DASI-001 ->
# RAC #221) and to the standard port bands, and "is it free?" is answered
# by ATTEMPTING the tunnel — frps refuses a taken name/port — never by
# probing.  Port bands measured live on gw2 (RAC #151, 2026-08-09):
RAC_BASE_VMSSH=35800; RAC_BASE_VMWEB=45800
RAC_BASE_HSSH=50800;  RAC_BASE_HUI=55800
# The decoder VM serves THREE web UIs, not one.  ka9q-web (8081) had the only
# channel, so station-web and the magnetometer dashboard were unreachable
# over RAC from anywhere -- rob, 2026-09-20, looking at the HamSCI dashboard:
# "there's additional webpages available ... make sure that page reflects all
# the services being offered."
# Bands are chosen clear of gw2's existing reservations: it already uses
# 46800/47800 for a 2nd/3rd RX888's ka9q-web and 40800 for GRAPE charts, so
# reusing those would mislabel these as ka9q-web instances.  Both sit inside
# 35800-59999, which is what makes them shareable rather than mesh-only.
RAC_BASE_VMSTN=48800; RAC_BASE_VMGMAG=49800
# Profile + DASI number stick across --reconfigure via /etc/sigmond-appliance.
RAC_PROFILE="${SIGMOND_RAC_PROFILE:-$(cat /etc/sigmond-appliance/rac-profile 2>/dev/null || echo dasi)}"
DASI_NUM="${SIGMOND_DASI_NUMBER:-$(cat /etc/sigmond-appliance/dasi-number 2>/dev/null)}"
HAMSCI_TOKEN="${SIGMOND_RAC_HAMSCI_TOKEN:-}"
RAC_REG_PORT="${SIGMOND_RAC_REG_PORT:-35737}"
RAC_PORT_SECURE="${SIGMOND_RAC_PORT_SECURE:-35736}"
RAC_PORT_UNSEC="${SIGMOND_RAC_PORT_UNSECURED:-35735}"
_hs="vpn.hamsci.org"
_wd="gw2.wsprdaemon.org"
build_rac_tiers() {
    case "$RAC_PROFILE" in
        standard)
            RAC_TIERS="wsprdaemon (secure)|$_wd|http://$_wd:$RAC_REG_PORT/register|on|$RAC_PORT_SECURE"
            ;;
        *)
            if [ -n "$DASI_NUM" ]; then
                # vpn.hamsci.org runs frps-secure on 35736 with account-less
                # TOFU auth (verified end-to-end 2026-08-09): no registrar,
                # no Linux accounts — the station's pubkey rides the login as
                # metadata and is trusted-on-first-use, keyed by DASI id.
                # DIRECT = deterministic RAC/ports claimed by ATTEMPT.
                # tls=opp: encrypted against the self-signed cert without
                # pinning; graduates to tls=on when the VPN gets a fleet-CA
                # cert (one tier-table field).  No token needed (pubkey is
                # the gate; the server's auth.token is empty).
                # Rung 2 (unsecured VPN) is the interim path that works TODAY:
                # vpn's legacy frps on 35735 is open (empty token, no plugin),
                # reachable through the firewall now, so a DASI box gets a
                # HamSCI tunnel before 35736 is opened.  Same DIRECT
                # deterministic RAC/ports + pubkey metadata; tls=off means the
                # pubkey rides but isn't verified there — the shared-open port
                # is the admission, a working tunnel the point (proven from
                # the AI6VN box 2026-08-09).  --rac-upgrade climbs to the TOFU
                # rung once 35736 opens.
                RAC_TIERS="HamSCI secure (TOFU)|$_hs|DIRECT|opp|$RAC_PORT_SECURE
HamSCI unsecured (direct)|$_hs|DIRECT|off|$RAC_PORT_UNSEC
wsprdaemon (secure)|$_wd|http://$_wd:$RAC_REG_PORT/register|on|$RAC_PORT_SECURE
wsprdaemon (UNSECURED)|$_wd|http://$_wd:$RAC_REG_PORT/register|off|$RAC_PORT_UNSEC"
            else
                # NO HamSCI rung here, and that is deliberate.  Until
                # 2026-09-15 this branch led with two rungs pointing at
                # http://vpn.hamsci.org:35737/register.  Nothing has ever
                # served that URL: the HamSCI gateway runs frps (35735),
                # frps-secure (35736), the TOFU login plugin and a dashboard
                # — there is no registrar on it at all (verified on the box,
                # 10.3.2.1, 2026-09-15).  So both rungs always failed and the
                # ladder walked silently on to gw2, which auto-assigns from
                # its 500 block.  That is precisely how two Scranton installs
                # became RAC 506 (W3USR_10) and 507 (W3USR_9) on the
                # WSPRDAEMON gateway when they were meant to be on HamSCI.
                #
                # A station reaches HamSCI by the REGISTRAR-LESS DIRECT rungs
                # above, which need a DASI number to derive RAC 220+N.  With
                # no number there is no way to choose a HamSCI RAC, so gw2 is
                # the only honest option — but say so out loud rather than
                # discovering it four failed rungs later.
                RAC_TIERS="wsprdaemon (secure)|$_wd|http://$_wd:$RAC_REG_PORT/register|on|$RAC_PORT_SECURE
wsprdaemon (UNSECURED)|$_wd|http://$_wd:$RAC_REG_PORT/register|off|$RAC_PORT_UNSEC"
            fi
            ;;
    esac
    # An explicitly pinned endpoint is an instruction, not a preference: it
    # replaces the ladder outright rather than becoming its first rung.
    if [ -n "${SIGMOND_RAC_SERVER:-}" ] || [ -n "${SIGMOND_RAC_REGISTRAR:-}" ]; then
        _ps="${SIGMOND_RAC_SERVER:-$_wd}"
        RAC_TIERS="pinned|$_ps|${SIGMOND_RAC_REGISTRAR:-http://$_ps:$RAC_REG_PORT/register}|${SIGMOND_RAC_TLS:-on}|${SIGMOND_RAC_FRPS_PORT:-$RAC_PORT_SECURE}"
    fi
}
# A DASI station that skipped the number prompt still BELONGS on HamSCI, and
# its name says so.  The number was asked for before the station designator
# was even collected (ask_rac runs before ask_names), so "Enter to skip" was
# an easy and silent way onto the wrong gateway.  Derive it from the name
# instead and rebuild the ladder; ask_names calls this, so a re-edit on the
# review screen re-derives too.  Anchored, like lib/sigmond/station_identity.py,
# so an ordinary callsign site (W3USR_9) never matches.
derive_dasi_num() {
    [ "$RAC_PROFILE" = "dasi" ] || return 0      # operator said not-DASI; respect it
    [ -z "$DASI_NUM" ] || return 0               # explicit number always wins
    local _n
    for _cand in "${DES:-}" "${REPORTER:-}"; do
        [ -n "$_cand" ] || continue
        # Three accepted shapes, each needing a separator or the full
        # three-digit form.  "DASI2" alone is the PROGRAM name, not unit 2,
        # and must never derive — it would claim RAC 222 and collide with the
        # real DASI-002 in the roster.  Erring toward no-derivation is safe:
        # the operator is still asked for the number.
        _n=$(printf '%s' "$_cand" | tr 'a-z' 'A-Z' | sed -nE \
             -e 's/^DASI2[-_ ]([0-9]{1,3})$/\1/p' \
             -e 's/^DASI[-_ ]([0-9]{1,3})$/\1/p' \
             -e 's/^DASI([0-9]{3})$/\1/p')
        # 10# or bash reads a leading zero as octal and 008 is fatal, not 8.
        [ -n "$_n" ] && _n=$((10#$_n))
        if [ -n "$_n" ] && [ "$_n" -ge 1 ] 2>/dev/null && [ "$_n" -le 99 ]; then
            DASI_NUM="$_n"
            say "station name '$_cand' identifies DASI-$(printf '%03d' "$_n") — using the HamSCI gateway (RAC $((220 + _n)))"
            build_rac_tiers
            return 0
        fi
    done
    return 0
}

# A pinned gateway persists.  A registration that runs alone (the switch
# script, the retry timer) carries no environment from the wizard run that
# pinned it, so it reads the pin from this file.  The image rig writes one,
# so that a nested install never registers with a real gateway.
RAC_PIN_FILE=/etc/sigmond-appliance/rac-endpoint.env
rac_load_pin(){
    local k v
    [ -r "$RAC_PIN_FILE" ] || return 0
    while IFS='=' read -r k v; do
        case "$k" in
            SIGMOND_RAC_SERVER|SIGMOND_RAC_REGISTRAR|SIGMOND_RAC_TLS|SIGMOND_RAC_FRPS_PORT)
                # the environment of this run wins over the file
                [ -n "${!k:-}" ] || printf -v "$k" '%s' "$v" ;;
        esac
    done < "$RAC_PIN_FILE"
}
rac_save_pin(){
    local k
    [ -n "${SIGMOND_RAC_SERVER:-}" ] || [ -n "${SIGMOND_RAC_REGISTRAR:-}" ] || return 0
    for k in SIGMOND_RAC_SERVER SIGMOND_RAC_REGISTRAR SIGMOND_RAC_TLS SIGMOND_RAC_FRPS_PORT; do
        if [ -n "${!k:-}" ]; then printf '%s=%s\n' "$k" "${!k}"; fi
    done > "$RAC_PIN_FILE.new" && mv -f "$RAC_PIN_FILE.new" "$RAC_PIN_FILE"
}
rac_load_pin
build_rac_tiers
RAC_SERVER="$(echo "$RAC_TIERS" | head -1 | cut -d'|' -f2)"
RAC_TLS="on"
RAC_TIER_LABEL=""
MARK_DIR=/etc/sigmond-appliance
CONF_MARK="$MARK_DIR/.configured"
LOG=/var/log/sigmond-wizard.log
say(){ echo "[wizard] $*" | tee -a "$LOG"; }

mkdir -p "$MARK_DIR"

# ── registration, as a step that runs alone ────────────────────────────────
# tasks/plan-remote-access.md section 3.  Before v3.71 these lines sat inside
# the interactive flow and read its shell variables, so only a person
# answering the wizard could register a station.  They now read what a wizard
# run recorded under $MARK_DIR, and `sigmond-setup --rac-register` runs them
# with no question and no decoder VM.
#
# Nothing here starts or stops the tunnel.
RAC_FRPC=/usr/local/sbin/frpc
RAC_CA=/etc/sigmond/frps-ca.crt
RAC_TOML=/etc/sigmond/frpc-host.toml
RAC_HOSTKEY=/root/.ssh/id_ed25519
RAC_LOCK=/run/sigmond-rac-register.lock
RAC_RELAY_UNIT=/etc/systemd/system/sigmond-vm-ssh-relay.socket
RAC_FAIL=""

rac_site_name(){
    # The name the gateway files this station under: the reporter id in upper
    # case, with / as _.  A running wizard knows $REPORTER.  A run without it
    # reads the name the last registration recorded, then the reporter id the
    # last wizard run left in the configured mark.
    local r="${REPORTER:-}"
    [ -n "$r" ] || r=$(cat "$MARK_DIR/rac-site" 2>/dev/null)
    [ -n "$r" ] || r=$(awk 'NR==1{print $1}' "$CONF_MARK" 2>/dev/null)
    printf '%s' "$r" | tr '[:lower:]' '[:upper:]' | tr '/' '_' | tr -cd 'A-Z0-9_-'
}

rac_payload_ready(){
    # frpc, the pinned CA and the host unit come from the sigmond-rac payload.
    if [ ! -x "$RAC_FRPC" ] && [ -x /root/sigmond-appliance/sigmond-rac/install-host.sh ]; then
        (cd /root/sigmond-appliance/sigmond-rac && ./install-host.sh) >>"$LOG" 2>&1
    fi
    [ -x "$RAC_FRPC" ] && [ -f "$RAC_CA" ]
}

rac_ladder(){
    # Walk $RAC_TIERS, best first, and stop at the first tier that registers
    # this station.  Sets REG (empty when none did), RAC_TRIED, and for the
    # tier that answered RAC_SERVER, RAC_REGISTRAR, RAC_TLS, RAC_PORT and
    # RAC_TIER_LABEL.  Reads SITE, DASI_NUM, HAMSCI_TOKEN and $RAC_HOSTKEY.pub.
    local _lbl _cand _cand_reg _tls _fport
    local _racn _pvs _pvw _phs _phu _pst _pgm _duser _tf _dtls _spec _n _lp _rp
    local _tpid _drun _derr _api _i
    REG=""
    RAC_TRIED=""
    while IFS='|' read -r _lbl _cand _cand_reg _tls _fport; do
        [ -n "$_cand" ] || continue
        RAC_TRIED="${RAC_TRIED:+$RAC_TRIED, }$_lbl"
        say "trying $_lbl — $_cand:$_fport (registrar $_cand_reg)"
        case "$_tls" in
            off) say "  NOTE: this tier is UNSECURED (no TLS, legacy frps)." ;;
            opp) say "  NOTE: encrypted (TLS) but the server is unauthenticated" ;;
        esac
        # The tier IS its tunnel port: if no frps listens there, the tier is
        # down no matter what the (shared) registrar would say — probe it
        # first so a dead secure stack can't register and then fail late.
        if ! timeout 5 bash -c "exec 3<>/dev/tcp/$_cand/$_fport" 2>/dev/null; then
            say "  $_lbl did not answer (no frps on $_cand:$_fport)"
            continue
        fi
        if [ "$_cand_reg" = "DIRECT" ]; then
            # Registrar-less DASI rung: deterministic RAC + ports, verified
            # by ATTEMPT — a throwaway frpc claims all 4 proxies and the
            # frps itself refuses a taken name/port.  The live tunnel (if
            # any) is untouched: separate config, separate admin port.
            _racn=$((220 + DASI_NUM))
            _pvs=$((RAC_BASE_VMSSH+_racn)); _pvw=$((RAC_BASE_VMWEB+_racn))
            _phs=$((RAC_BASE_HSSH+_racn)); _phu=$((RAC_BASE_HUI+_racn))
            _pst=$((RAC_BASE_VMSTN+_racn)); _pgm=$((RAC_BASE_VMGMAG+_racn))
            _duser="DASI-$(printf '%03d' "$DASI_NUM")"
            say "  deterministic mapping: $_duser -> RAC #$_racn (attempting, not probing)"
            _tf=$(mktemp /tmp/frpc-dasi-XXXXXX.toml)
            _dtls=true; [ "$_tls" = "off" ] && _dtls=false
            {
                printf 'serverAddr = "%s"\nserverPort = %s\nuser = "%s"\nloginFailExit = true\n' "$_cand" "$_fport" "$_duser"
                # the pubkey rides EVERY login as metadata — today the server
                # ignores it; a future Login plugin on the VPN turns it into
                # per-unit admission (TOFU) with no Linux accounts (rob).
                printf '[metadatas]\npubkey = "%s"\nsite = "%s"\ndasi = "%s"\n' "$(cat "$RAC_HOSTKEY.pub")" "$SITE" "$_duser"
                # TOFU: the pubkey (above) is the credential; the server's
                # auth.token is empty, so the client sends an empty token too.
                printf '[auth]\nmethod = "token"\ntoken = ""\n'
                printf '[transport.tls]\nenable = %s\n' "$_dtls"
                printf '[webServer]\naddr = "127.0.0.1"\nport = 7502\n'
                for _spec in "vm-ssh:12222:$_pvs" "vm-web:12223:$_pvw" "host-ssh:22:$_phs" "host-ui:8006:$_phu" \
                             "vm-station:12224:$_pst" "vm-gmag:12225:$_pgm"; do
                    IFS=: read -r _n _lp _rp <<<"$_spec"
                    printf '[[proxies]]\nname = "%s-%s"\ntype = "tcp"\nlocalIP = "127.0.0.1"\nlocalPort = %s\nremotePort = %s\n' "$SITE" "$_n" "$_lp" "$_rp"
                done
            } > "$_tf"
            "$RAC_FRPC" -c "$_tf" >>"$LOG" 2>&1 &
            _tpid=$!
            _drun=0; _derr=""
            for _i in 1 2 3 4 5 6 7 8; do
                sleep 3
                kill -0 "$_tpid" 2>/dev/null || break
                _api=$(curl -s http://127.0.0.1:7502/api/status 2>/dev/null)
                _drun=$(echo "$_api" | grep -o '"status":"running"' | wc -l)
                _derr=$(echo "$_api" | grep -o '"err":"[^"]\{1,\}"' | head -3)
                [ "$_drun" -ge 4 ] && break
            done
            kill "$_tpid" 2>/dev/null; wait "$_tpid" 2>/dev/null; rm -f "$_tf"
            if [ "$_drun" -ge 4 ]; then
                say "  RAC #$_racn is free on $_cand — claiming it"
                REG="RACN=$_racn RUSER=$_duser RTOKEN=$HAMSCI_TOKEN P_VMSSH=$_pvs P_VMWEB=$_pvw P_HSSH=$_phs P_HUI=$_phu SRV=$_cand SPORT=$_fport"
            elif echo "$_derr" | grep -qi "port already"; then
                say "  ** RAC #$_racn ports are TAKEN on $_cand — is another unit already"
                say "  ** using DASI number $DASI_NUM?  Choose a different number via"
                say "  ** sigmond-setup --reconfigure, or let the ladder continue below."
            else
                say "  HamSCI direct attempt failed (${_derr:-login refused — check the token}); see $LOG"
            fi
        else
            REG=$(python3 - "$SITE" "$(cat "$RAC_HOSTKEY.pub")" "$_cand_reg" "$RAC_CA" <<'PYEOF' 2>>"$LOG"
import json, re, ssl, sys, urllib.error, urllib.request
site, pub, registrar, cafile = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
# The reply carries this station's token.  Over https, accept it only from a
# registrar that proves itself against the CA the image pins.  Both gateways
# serve their registrar over plain http today (see the ladder comment above),
# so an http address still goes out in the clear.
ctx = ssl.create_default_context(cafile=cafile) if registrar.startswith("https://") else None
req = urllib.request.Request(
    registrar,
    data=json.dumps({"site": site, "pubkey": pub}).encode(),
    headers={"Content-Type": "application/json"})
try:
    r = json.load(urllib.request.urlopen(req, timeout=30, context=ctx))
except urllib.error.HTTPError as e:
    try: msg = json.load(e)["error"]
    except Exception: msg = str(e)
    sys.exit("gateway refused registration: %s" % msg)
except Exception as e:
    sys.exit("cannot reach the gateway registrar: %s" % e)
ok = (re.fullmatch(r"[0-9a-f]{16}", str(r.get("user", "")))
      and re.fullmatch(r"[0-9a-zA-Z]{8,64}", str(r.get("token", "")))
      and re.fullmatch(r"[a-z0-9.-]+", str(r.get("server_addr", "")))
      and isinstance(r.get("rac"), int)
      and all(isinstance(r.get("ports", {}).get(k), int)
              for k in ("vm_ssh", "vm_web", "host_ssh", "host_ui")))
if not ok:
    sys.exit("gateway returned a malformed registration")
print("RACN=%d RUSER=%s RTOKEN=%s P_VMSSH=%d P_VMWEB=%d P_HSSH=%d P_HUI=%d SRV=%s SPORT=%d" % (
    r["rac"], r["user"], r["token"], r["ports"]["vm_ssh"], r["ports"]["vm_web"],
    r["ports"]["host_ssh"], r["ports"]["host_ui"],
    r["server_addr"], int(r["server_port"])))
PYEOF
)
        fi
        if [ -n "$REG" ]; then
            RAC_SERVER="$_cand"
            RAC_REGISTRAR="$_cand_reg"
            RAC_TLS="$_tls"
            RAC_PORT="$_fport"
            RAC_TIER_LABEL="$_lbl"
            break
        fi
        say "  $_lbl frps answered on :$_fport but its registrar did not"
    done <<TIEREOF
$RAC_TIERS
TIEREOF
}

rac_write_config(){
    # Turn one registration ($REG, from rac_ladder) into this host's tunnel
    # configuration and its marks.  It leaves RACN, SRV and the four gateway
    # ports set for the caller's summary line.
    local RAC_TLS_BLOCK
    RACN=""; P_VMSTN=""; P_VMGMAG=""
    eval "$REG"
    [ -n "$RACN" ] || { RAC_FAIL="the registration carried no RAC number"; return 1; }
    # The two newest channels are DERIVED, never supplied.  The deterministic
    # path computes them; the registrar path cannot, because the gateway's
    # JSON only carries the four original ports.  Every band is base+rac by
    # construction, so deriving is the same rule, applied here.
    P_VMSTN=$((RAC_BASE_VMSTN+RACN))
    P_VMGMAG=$((RAC_BASE_VMGMAG+RACN))
    # transport follows the tier that actually answered: a secure tier pins
    # the fleet CA, opportunistic (opp) encrypts without pinning (server cert
    # is self-signed — vpn.hamsci.org today), and an unsecured one has no TLS.
    case "$RAC_TLS" in
        off) RAC_TLS_BLOCK="[transport.tls]
enable = false" ;;
        opp) RAC_TLS_BLOCK="[transport.tls]
enable = true" ;;
        *)   RAC_TLS_BLOCK="[transport.tls]
enable = true
trustedCaFile = \"$RAC_CA\"" ;;
    esac
    say "writing $RAC_TOML (6 channels, one tunnel)"
    # Write beside the live file under a closed umask, then rename: the file
    # holds the station's token, and frpc must never read half of it.
    if ! ( umask 077; cat > "$RAC_TOML.new" <<TOMLEOF
# Written by sigmond-setup $(date -u +%F) — RAC #$RACN, site $SITE.
# ONE frpc on the Proxmox host carries every channel; the vm-* channels
# ride the local relays (12222-12225), so this file never needs the VM's
# address.
# serverPort comes from the TIER that answered (35736 TLS / 35735 legacy),
# not from the registrar's reply — the registrar only knows the legacy stack.
serverAddr = "$SRV"
serverPort = ${RAC_PORT:-$SPORT}
user = "$RUSER"

# The station's pubkey rides every login as metadata (any tier): servers
# ignore it today; a Login plugin can enforce per-unit admission (TOFU)
# from it later — no per-client accounts needed (rob 2026-08-09).
[metadatas]
pubkey = "$(cat "$RAC_HOSTKEY.pub")"
site = "$SITE"
dasi = "${DASI_NUM:+DASI-$(printf '%03d' "$DASI_NUM")}"

[auth]
method = "token"
token = "$RTOKEN"

$RAC_TLS_BLOCK

[webServer]
addr = "127.0.0.1"
port = 7500

[[proxies]]
name = "$SITE-vm-ssh"
type = "tcp"
localIP = "127.0.0.1"
localPort = 12222
remotePort = $P_VMSSH

[[proxies]]
name = "$SITE-vm-web"
type = "tcp"
localIP = "127.0.0.1"
localPort = 12223
remotePort = $P_VMWEB

[[proxies]]
name = "$SITE-vm-station"
type = "tcp"
localIP = "127.0.0.1"
localPort = 12224
remotePort = $P_VMSTN

[[proxies]]
name = "$SITE-vm-gmag"
type = "tcp"
localIP = "127.0.0.1"
localPort = 12225
remotePort = $P_VMGMAG

[[proxies]]
name = "$SITE-host-ssh"
type = "tcp"
localIP = "127.0.0.1"
localPort = 22
remotePort = $P_HSSH

[[proxies]]
name = "$SITE-host-ui"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8006
remotePort = $P_HUI
TOMLEOF
    ) || ! mv -f "$RAC_TOML.new" "$RAC_TOML"; then
        rm -f "$RAC_TOML.new"
        RAC_FAIL="could not write $RAC_TOML"
        return 1
    fi
    # The marks come last: a station counts as registered once the tunnel
    # configuration stands, and never before.
    printf '%s\n' "$RAC_TIER_LABEL" > "$MARK_DIR/rac-tier"
    printf '%s\n' "$RAC_REGISTRAR" > "$MARK_DIR/rac-registrar"
    printf '%s\n' "$RACN" > "$MARK_DIR/rac-number"
    printf '%s\n' "$SITE" > "$MARK_DIR/rac-site"
}

rac_register_locked(){
    local mode="$1"
    if [ "$mode" = ensure ] && [ -s "$RAC_TOML" ]; then
        return 0
    fi
    SITE=$(rac_site_name)
    if [ -z "$SITE" ]; then
        RAC_FAIL="this host records no station name yet; run: sigmond-setup --reconfigure"
        return 1
    fi
    if ! rac_payload_ready; then
        RAC_FAIL="sigmond-rac payload missing (frpc/CA); run install-host.sh, then sigmond-setup --reconfigure"
        return 1
    fi
    if [ ! -f "$RAC_RELAY_UNIT" ]; then
        RAC_FAIL="the VM relays are not installed on this host; run: pm-align --apply (or sigmond-setup --reconfigure)"
        return 1
    fi
    [ -f "$RAC_HOSTKEY" ] || ssh-keygen -q -t ed25519 -N "" -f "$RAC_HOSTKEY"
    say "registering with the gateway: this sends the station's name ($SITE) and this host's public key"
    rac_ladder
    if [ -z "$REG" ]; then
        RAC_FAIL="no gateway tier answered (tried: $RAC_TRIED)"
        return 1
    fi
    rac_write_config || return 1
    # A tunnel that runs takes up the new configuration.  One that stands
    # stopped stays stopped.
    systemctl try-restart sigmond-rac-host.service >>"$LOG" 2>&1
    return 0
}

rac_register(){
    # rac_register ensure|fresh
    #   ensure  register only a host that holds no tunnel configuration yet
    #   fresh   register again even so.  A wizard run does, because the
    #           reporter id may have changed.  A failed fresh run leaves the
    #           old configuration as it stands.
    # Returns 0 once $RAC_TOML holds a registration.  Returns 1 otherwise, with
    # the reason in $RAC_FAIL.  One registration at a time: the lock covers a
    # wizard run, the switch script and the retry timer.
    local rc
    RAC_FAIL=""
    exec 8>"$RAC_LOCK"
    if ! flock -w 300 8; then
        exec 8>&-
        RAC_FAIL="another registration is still running"
        return 1
    fi
    # `8>&-` on the call keeps the lock with this shell and out of every
    # program the registration starts (the trial frpc, python3, systemctl):
    # bash holds the lock in a copy no child inherits, so a child left behind
    # holds no lock once this shell has gone.
    rac_register_locked "${1:-ensure}" 8>&-; rc=$?
    exec 8>&-
    return $rc
}

# ── reaching the decoder VM ─────────────────────────────────────────────────
# These three stood further down, among the wizard's questions.  They sit
# here so that `sigmond-setup --pair-vm` can use them: that subcommand runs
# before the wizard waits for anything or asks anything.
# The VM's address on the host-only /30 (PM .1, VM .2).  Needed because the
# repair below must NOT go through the guest agent — that is the thing that is
# broken.  ssh over the private link is the independent path.
mgmt_vm_ip() {
    [ -n "${SIGMOND_MGMT_VM:-}" ] && { printf '%s\n' "${SIGMOND_MGMT_VM}"; return; }
    local a
    a=$(ip -4 -o addr show 2>/dev/null \
        | awk '$4 ~ /\/30$/ {split($4,p,"/"); print p[1]; exit}')
    if [ -n "$a" ]; then
        awk -v ip="$a" 'BEGIN{n=split(ip,o,"."); printf "%s.%s.%s.%d\n",o[1],o[2],o[3],o[4]+1}'
        return
    fi
    printf '10.99.0.2\n'
}

# ── what crosses to the guest agent ─────────────────────────────────────────
# ⛔ NO BYTE ABOVE 0x7F MAY CROSS TO THE GUEST AGENT.  Proxmox (PVE 9.1.1,
# qemu-server 9.0.30) writes a request that holds non-ASCII characters SHORT:
# as many bytes as the request has characters, so the closing bytes never
# arrive.  About one time in six the agent's parser then waits for them for
# good: it keeps its PID, reads every later request and answers none
# (tasks/plan-guest-agent-requests.md).  An operator's own answer can hold
# such a character -- a name with an accent, a password -- so gexec and
# guest_out send every command base64-encoded: the request holds ASCII
# whatever the command holds.  tests/test_wizard_guest_senders.py fails on
# any byte above 0x7F in a request.
#
# The guest decodes the word and runs it in the login shell it stands in, as
# `bash -lc "<command>"` did.  The command arrives as an ARGUMENT, never on
# the shell's standard input: a command that reads its input would swallow
# whatever followed it there.
qga_b64(){ printf '%s' "$1" | base64 -w0; }
QGA_RUN='eval "$(printf %s "$1" | base64 -d)"'

# qga_reset <vmid> : one 0xFF byte and a sync request down the agent's socket.
# The byte resets the agent's parser; the QGA protocol defines it and Proxmox
# never sends it.  Returns 0 when the agent answered the sync.  The same bytes
# stand in the console panel (sigmond-appliance firstboot-v3.sh), pm-align.py
# and pm-heartbeat.py; tests/test_qga_reset.py holds them equal.
QGA_DIR="${SIGMOND_QGA_DIR:-/var/run/qemu-server}"
QGA_NOTE="${SIGMOND_QGA_NOTE:-/run/sigmond/qga-reset}"
qga_reset(){
    timeout 10 python3 -c 'import socket,sys,time
s=socket.socket(socket.AF_UNIX); s.settimeout(4)
end=time.monotonic()+4; got=b""
try:
    s.connect(sys.argv[2]+"/"+sys.argv[1]+".qga")
    s.sendall(b"\xff"+b"{\"execute\":\"guest-sync-delimited\",\"arguments\":{\"id\":4242}}\n")
    while b"4242" not in got:
        left=end-time.monotonic()
        if left<=0: sys.exit(1)
        s.settimeout(left); d=s.recv(4096)
        if not d: sys.exit(1)
        got+=d
    sys.exit(0)
except Exception:
    sys.exit(1)' "$1" "$QGA_DIR" >/dev/null 2>&1 </dev/null
}
# One line, replaced each time: when the last reset brought the agent back,
# and who sent it.  pm-heartbeat.py reports from it.
qga_note_reset(){
    mkdir -p "$(dirname "$QGA_NOTE")" 2>/dev/null
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" 2>/dev/null >"$QGA_NOTE"
}

gexec(){ # gexec <timeout-s> <command...>  → runs in guest, echoes exitcode
    # A guest agent that dies mid-wizard used to poison every LATER step:
    # each one returned "QEMU guest agent is not running", each printed its
    # own WARN, and the wizard walked on to declare success.  That is how
    # v3.39/v3.40 shipped a VM with no password and no ssh policy — the two
    # steps that happen to sit right after the one that killed the agent
    # (2026-09-16).  A dead agent is now a transient to wait out, not a
    # silent skip: wait for it to answer again and retry the step ONCE.
    # A command that merely exits non-zero is NOT retried — that is a real
    # answer from a live agent.
    local t="$1"; shift
    local out rc try i b
    b=$(qga_b64 "$*")
    for try in 1 2; do
        out=$(qm guest exec "$VMID" --timeout "$t" -- bash -lc "$QGA_RUN" bash "$b" 2>&1 </dev/null)
        rc=$(echo "$out" | grep -o '"exitcode" *: *[0-9-]*' | grep -o '[0-9-]*$' | head -1)
        echo "$out" >> "$LOG"
        [ "${rc:-1}" = "0" ] && return 0
        case "$out" in
            *"guest agent is not running"*|*"got timeout"*|*"QGA"*)
                [ "$try" = 2 ] && return 1
                say "guest agent stopped answering — waiting for it before retrying"
                # One reset before the wait: an agent that stands deaf hears
                # again at once, and nothing inside the VM restarts.
                if ! timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 </dev/null; then
                    qga_reset "$VMID" && qga_note_reset wizard
                fi
                for i in $(seq 1 24); do
                    timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 </dev/null && break
                    sleep 5
                done
                ;;
            *) return 1 ;;
        esac
    done
    return 1
}

# gexec_unit <timeout-s> <name> <command...> : gexec, but the command runs in
# a transient unit of its own, sigmond-wizard-<name>, outside the guest
# agent's cgroup.  sigmond's drop-in caps the agent at MemoryMax=128M, and the
# cap covers every process a request starts: one smd needs about 55 MB, two
# pass it.  So every smd the wizard runs goes through here.  `systemd-run
# --wait` hands back the command's own exit, and --pipe passes its output
# through to the request.  The command travels base64-encoded a second time,
# so no quote inside it can break the line.  A retry after a dead agent meets
# the unit's name still in use and fails, rather than start a second smd.
QGA_SDRUN="${SIGMOND_QGA_SDRUN:-systemd-run}"
gexec_unit(){
    local t="$1" name="$2"; shift 2
    gexec "$t" "$QGA_SDRUN --quiet --wait --pipe --collect --unit=sigmond-wizard-$name /bin/bash -lc '$QGA_RUN' bash $(qga_b64 "$*")"
}

# Run a command in the guest and hand back its STDOUT.  gexec returns only an
# exit code; preflight_devices already needed the output and open-coded this.
guest_out() {
    local t="$1"; shift
    qm guest exec "$VMID" --timeout "$t" -- bash -lc "$QGA_RUN" bash "$(qga_b64 "$*")" 2>/dev/null </dev/null \
      | python3 -c 'import json,sys
try:  print(json.load(sys.stdin).get("out-data",""))
except Exception: pass' 2>/dev/null || true
}

# ── the one switch, and the host files that go with it ─────────────────────
# tasks/plan-remote-access.md section 1.  /usr/local/sbin/sigmond-rac-switch
# holds the only code on the station that turns the tunnel on or off.  The
# wizard carries it, so that every host with this wizard can install it:
# firstboot stages the wizard on a new station, and pm-align brings it to a
# station already in the field.
RAC_SWITCH=/usr/local/sbin/sigmond-rac-switch
RAC_STATE_FILE=/run/sigmond/rac-state
rac_install_host_files(){
    # Unlink before writing: a copy that runs at this moment keeps its file.
    # The working copy waits under a second name, a hard link to the same file,
    # until the new one proves whole (rac_switch_settle, below).
    ln -f /usr/local/sbin/sigmond-rac-switch /usr/local/sbin/sigmond-rac-switch.old 2>/dev/null
    rm -f /usr/local/sbin/sigmond-rac-switch
    cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'
#!/bin/bash
# sigmond-rac-switch - the one switch for this station's remote-access tunnel.
#
#   sigmond-rac-switch on       record the wish; register first if this station
#                               never did; bring the relays and the tunnel up;
#                               wait for the channels; print the state
#   sigmond-rac-switch off      record the wish; stop and disable the tunnel and
#                               the relays.  The registration stays, so a later
#                               `on` needs nothing from the gateway.  It stops
#                               and disables them even when it can write
#                               nothing: neither the wish, nor its lock, nor
#                               its state file
#   sigmond-rac-switch status   print the state and change nothing
#
# It takes exactly one of those three words and nothing after it.  The decoder
# VM reaches this script through one ssh key whose forced command runs it
# under sudo with no argument at all; the word then arrives in
# SSH_ORIGINAL_COMMAND.  Nothing else on the station turns the tunnel on or
# off: `sigmond-setup --rac-on` and `--rac-off` run this script too.
#
# The first line it prints reads exactly one of
#     Remote access: on
#     Remote access: off
#     Remote access: wanted on, not up
# and `smd remote` in the VM reads the state from that line.
#
# Exit codes: 0 done (for status: on, or off)   2 refused, nothing changed
#             3 wanted on, not up               4 off, yet the tunnel still runs
#             1 the switch could not start its work, and changed nothing
#               (for off: the tunnel stands stopped and disabled, and the wish
#               could not be recorded; its answer says so)
#
# Written to the host by sigmond-wizard.sh (rac_install_host_files).
# Design: sigmond tasks/plan-remote-access.md.
set -u
LC_ALL=C; export LC_ALL

# -- the word ----------------------------------------------------------------
if [ "$#" -eq 0 ]; then
    # The pairing's way in: the forced command passes no argument.  In this
    # mode the script takes one thing from its caller, the word, and nothing
    # else: no stdin, and no variable whose name begins SIGMOND_.  That covers
    # the overrides this script reads and the ones a program it starts reads
    # (sigmond-setup, which registers, takes a gateway and a port that way).
    exec </dev/null
    for _v in "${!SIGMOND_@}"; do unset "$_v"; done
    unset _v
    WORD="${SSH_ORIGINAL_COMMAND-}"
elif [ "$#" -eq 1 ]; then
    WORD="$1"
else
    WORD=""
fi
case "$WORD" in
    on|off|status) ;;
    *)  # Never print what was asked: it came from another machine.
        echo "sigmond-rac-switch: refused. It takes exactly one word: on, off or status." >&2
        exit 2 ;;
esac

# Root on this host, and the tests, may point these elsewhere.
MARK_DIR="${SIGMOND_RAC_MARK_DIR:-/etc/sigmond-appliance}"
TOML="${SIGMOND_RAC_TOML:-/etc/sigmond/frpc-host.toml}"
RUN_DIR="${SIGMOND_RAC_RUN_DIR:-/run/sigmond}"
SETUP="${SIGMOND_RAC_SETUP:-/usr/local/sbin/sigmond-setup}"
API="${SIGMOND_RAC_API:-http://127.0.0.1:7500/api/status}"
WAIT_S="${SIGMOND_RAC_WAIT_S:-60}"
WAIT_STEP="${SIGMOND_RAC_WAIT_STEP:-5}"
LOCK_WAIT_S="${SIGMOND_RAC_LOCK_WAIT_S:-120}"
UNIT=sigmond-rac-host.service
SOCKETS="sigmond-vm-ssh-relay.socket sigmond-vm-web-relay.socket sigmond-vm-station-relay.socket sigmond-vm-gmag-relay.socket"
RETRY_TIMER=sigmond-rac-register.timer
PAIR_USER=sigmond-remote

# Who asks: the decoder VM (through the pairing account), or root on this
# host.  A wizard run and the retry timer name themselves.
if [ "${SUDO_USER:-}" = "$PAIR_USER" ]; then
    BY=vm
else
    case "${SIGMOND_RAC_BY:-}" in install|timer) BY="$SIGMOND_RAC_BY" ;; *) BY=host ;; esac
fi

# -- reading what stands -----------------------------------------------------
registered(){ [ -s "$TOML" ]; }
tunnel_running(){
    # `activating` is the unit inside its restart delay: the service keeps
    # trying, so "does not run" would come and go between two tries.
    case "$(systemctl is-active "$UNIT" 2>/dev/null)" in active|activating) return 0 ;; esac
    return 1
}
declared(){ local n; n=$(grep -c '^name *=' "$TOML" 2>/dev/null); echo "${n:-0}"; }
channels_up(){
    # grep -o | wc -l, not grep -c: the API answers with one line of JSON.
    local n
    n=$(curl -s --max-time 3 "$API" 2>/dev/null | grep -o '"status":"running"' | wc -l | tr -d ' ')
    echo "${n:-0}"
}
wish_on_file(){
    local w
    w=$(head -1 "$MARK_DIR/rac-desired" 2>/dev/null)
    case "$w" in on|off) echo "$w" ;; esac
}
wish(){
    local w
    w=$(wish_on_file)
    if [ -n "$w" ]; then echo "$w"; return; fi
    # No wish on file: a station installed before v3.71.  Read the wish from
    # what stands.  Registered and enabled means on; anything else means off,
    # so a station whose owner declined at install stays as it stands.
    if registered && systemctl is-enabled --quiet "$UNIT" 2>/dev/null; then echo on; else echo off; fi
}
set_wish(){
    # Record who and when only when the wish changes.
    local new="$1"
    [ "$(wish_on_file)" = "$new" ] && return 0
    mkdir -p "$MARK_DIR" || return 1
    printf '%s\n' "$new" > "$MARK_DIR/rac-desired.new" \
        && mv -f "$MARK_DIR/rac-desired.new" "$MARK_DIR/rac-desired" \
        || { rm -f "$MARK_DIR/rac-desired.new" 2>/dev/null; return 1; }
    printf '%s %s %s\n' "$new" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$BY" > "$MARK_DIR/rac-changed"
    logger -t sigmond-rac-switch "remote access wish set to $new by $BY" 2>/dev/null
    return 0
}
read_state(){
    W=$(wish)
    if [ -n "$(wish_on_file)" ]; then WSRC=file; else WSRC=units; fi
    if registered; then REGD=yes; else REGD=no; fi
    DECL=$(declared)
    UP=0; RUNS=stopped
    if tunnel_running; then RUNS=running; UP=$(channels_up); fi
    if [ "$W" = off ]; then STATE=off
    elif [ "$RUNS" = running ] && [ "$UP" -ge 1 ]; then STATE=on
    else STATE=down
    fi
    RACN=$(head -1 "$MARK_DIR/rac-number" 2>/dev/null)
    TIER=$(head -1 "$MARK_DIR/rac-tier" 2>/dev/null)
    GW=$(awk -F'"' '/^serverAddr/{print $2; exit}' "$TOML" 2>/dev/null)
    CHANGED_AT=""; CHANGED_BY=""
    if [ -r "$MARK_DIR/rac-changed" ]; then
        read -r _ CHANGED_AT CHANGED_BY < "$MARK_DIR/rac-changed"
    fi
}
write_state(){
    # One line per fact, key=value, for the console panel and the host
    # heartbeat.  They read this file; they never run it.  `status` takes no
    # lock, and the panel, the heartbeat and an operator can each run one at
    # the same moment, so every run writes a file of its own name and renames
    # it into place.  Returns 1 when the file could not be written.
    local tmp="$RUN_DIR/rac-state.new.$$"
    mkdir -p "$RUN_DIR" 2>/dev/null
    if {
        printf 'state=%s\n' "$STATE"
        printf 'wish=%s\n' "$W"
        printf 'wish_source=%s\n' "$WSRC"
        printf 'registered=%s\n' "$REGD"
        printf 'tunnel=%s\n' "$RUNS"
        printf 'channels_up=%s\n' "$UP"
        printf 'channels_declared=%s\n' "$DECL"
        printf 'rac=%s\n' "$RACN"
        printf 'gateway=%s\n' "$GW"
        printf 'tier=%s\n' "$TIER"
        printf 'changed_at=%s\n' "$CHANGED_AT"
        printf 'changed_by=%s\n' "$CHANGED_BY"
        printf 'checked_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } 2>/dev/null > "$tmp" \
        && chmod 644 "$tmp" 2>/dev/null \
        && mv -f "$tmp" "$RUN_DIR/rac-state" 2>/dev/null; then
        return 0
    fi
    rm -f "$tmp" 2>/dev/null
    return 1
}
who_when(){
    [ -n "$CHANGED_AT" ] || return 0
    local who
    case "$CHANGED_BY" in
        vm)      who="from the decoder VM" ;;
        install) who="by the installer" ;;
        timer)   who="by the host's retry timer" ;;
        *)       who="on the Proxmox host" ;;
    esac
    echo "  Set to $W at $CHANGED_AT (UTC) $who."
}
print_state(){
    local where="RAC ${RACN:-?} on ${GW:-the gateway}${TIER:+, $TIER}"
    case "$STATE" in
        on)
            echo "Remote access: on"
            echo "  The gateway accepted $UP of $DECL channels ($where)."
            who_when
            echo "  Turn it off:  smd remote off   (in the decoder VM)" ;;
        off)
            echo "Remote access: off"
            if [ "$REGD" = yes ]; then
                echo "  The operator holds it off. The registration stays ($where),"
                echo "  so turning it on needs nothing from the gateway."
            elif [ "$WSRC" = units ]; then
                echo "  This station never registered for remote access."
            else
                echo "  The operator holds it off. The station has not registered yet."
            fi
            [ "$RUNS" = running ] && echo "  NOTE: the tunnel service runs all the same. Someone started it outside this switch; 'off' again stops it."
            who_when
            echo "  Turn it on:   smd remote on    (in the decoder VM)" ;;
        *)
            echo "Remote access: wanted on, not up"
            if [ "$REGD" = no ]; then
                echo "  The station has not registered yet: no gateway has answered."
                echo "  This host tries again every 10 minutes and brings the tunnel up when one does."
            elif [ "$RUNS" = running ]; then
                echo "  The tunnel service runs, and the gateway has accepted none of its $DECL channels ($where)."
                echo "  It keeps trying every 30 seconds. Look at:  journalctl -u $UNIT -n 50   (on the Proxmox host)"
            else
                echo "  The tunnel service does not run ($where)."
                echo "  Look at:  journalctl -u $UNIT -n 50   (on the Proxmox host)"
            fi
            who_when
            echo "  Turn it off:  smd remote off   (in the decoder VM)" ;;
    esac
}
wait_for_channels(){
    # Every channel the configuration declares, never a fixed count.
    local want waited=0
    want=$(declared)
    [ "$want" -gt 0 ] || want=1
    while :; do
        [ "$(channels_up)" -ge "$want" ] && return 0
        [ "$waited" -ge "$WAIT_S" ] && return 1
        sleep "$WAIT_STEP"
        waited=$((waited + WAIT_STEP))
    done
}
repaint_panel(){ systemctl start --no-block sigmond-issue.service >/dev/null 2>&1; return 0; }

# -- status ------------------------------------------------------------------
if [ "$WORD" = status ]; then
    read_state
    write_state
    print_state
    [ "$STATE" = down ] && exit 3
    exit 0
fi

# -- on, off -----------------------------------------------------------------
# The caller may sit at the far end of the very tunnel this script stops, and
# its terminal then goes away mid-run.  Finish regardless.
trap '' HUP PIPE
mkdir -p "$RUN_DIR" 2>/dev/null
LOCKED=yes
if ! { exec 9>"$RUN_DIR/rac-switch.lock"; } 2>/dev/null; then
    # No lock file can be opened: a full disk, or one mounted read-only.  `on`
    # stops here.  `off` goes on without the lock, because an owner must be
    # able to close the station whatever the disk does.  (A lock that another
    # change HOLDS is a different thing: `off` waits for that one, below.)
    LOCKED=no
    if [ "$WORD" != off ]; then
        echo "sigmond-rac-switch: cannot write under $RUN_DIR; nothing changed." >&2
        exit 1
    fi
fi
if [ "$LOCKED" = yes ] && ! flock -w "$LOCK_WAIT_S" 9; then
    echo "sigmond-rac-switch: another change is still running; nothing changed." >&2
    exit 1
fi

switch_off(){
    # Stop and disable first, whatever else fails.  Then say what stands, and
    # what could not be put on file.
    local recorded=yes wrote=yes did
    trap '' INT TERM
    set_wish off 2>/dev/null || recorded=no
    # The relays first and the tunnel last: stopping the tunnel may end the
    # caller's own session.
    # shellcheck disable=SC2086
    systemctl disable --now $SOCKETS >/dev/null 2>&1
    systemctl disable --now "$UNIT" >/dev/null 2>&1
    # With no `off` on file, nothing tells a later run of the retry timer that
    # the operator said off.  So the timer goes too.
    [ "$recorded" = yes ] || systemctl disable --now "$RETRY_TIMER" >/dev/null 2>&1
    repaint_panel
    read_state
    write_state || wrote=no
    case "$STATE" in
        on|off|down) print_state 2>/dev/null ;;
        *)  # No state to print.  Say the one thing this run did.
            if [ "$RUNS" = running ]; then
                echo "sigmond-rac-switch: off asked systemd to stop and disable the tunnel, and the tunnel service still runs."
            else
                echo "sigmond-rac-switch: the tunnel and its relays are stopped and disabled."
            fi ;;
    esac
    # These lines go to stdout, below the state: `smd remote` in the VM prints
    # the switch's stdout, and its operator must read them.
    if [ "$recorded" = no ]; then
        # Say only what holds.  On a root filesystem mounted read-only, systemd
        # stops a unit and cannot remove the link that enables it.
        if [ "$RUNS" = running ]; then
            did="off asked systemd to stop and disable the tunnel, its relays and the retry timer, and the tunnel service STILL RUNS."
        elif systemctl is-enabled --quiet "$UNIT" 2>/dev/null; then
            did="off stopped the tunnel and its relays, and the tunnel service STAYS ENABLED: systemd could not disable it, so it starts again at the next boot."
        else
            did="off stopped and disabled the tunnel, its relays and the retry timer."
        fi
        echo "sigmond-rac-switch: $did"
        echo "sigmond-rac-switch: it could not record the wish 'off' under $MARK_DIR (is the disk full, or read-only?)."
        echo "sigmond-rac-switch: this host therefore still reads the wish as it stood before. Make room on the disk, then turn remote access off once more."
    fi
    [ "$wrote" = yes ] || echo "sigmond-rac-switch: could not write under $RUN_DIR, so this run left no state file; the console panel and the host heartbeat read that file."
    [ "$RUNS" = running ] && return 4
    [ "$recorded" = yes ] || return 1
    return 0
}

switch_on(){
    # A timer run and a wizard run read the wish before they ask for `on`: the
    # timer a moment before, a wizard that registers afresh some minutes
    # before.  An operator who said off in between has the last word.  With
    # `off` on file, a request from either of the two changes nothing and
    # answers as `status` does.  An operator's own `on` acts.
    if [ "$(wish_on_file)" = off ]; then
        case "$BY" in
            timer|install)
                read_state
                write_state
                print_state
                [ "$STATE" = down ] && return 3
                return 0 ;;
        esac
    fi
    set_wish on || { echo "sigmond-rac-switch: could not record the wish; nothing changed." >&2; return 1; }
    if ! registered; then
        echo "This station has not registered for remote access yet. Registering now."
        "$SETUP" --rac-register
    fi
    if registered; then
        systemctl disable --now "$RETRY_TIMER" >/dev/null 2>&1
        # shellcheck disable=SC2086
        systemctl enable --now $SOCKETS >/dev/null 2>&1
        systemctl enable --now "$UNIT" >/dev/null 2>&1
        echo "Waiting up to ${WAIT_S}s for the gateway to accept the channels."
        wait_for_channels
    else
        # No gateway answered.  The wish stands, and the timer keeps trying.
        systemctl enable --now "$RETRY_TIMER" >/dev/null 2>&1
    fi
    repaint_panel
    read_state
    write_state
    print_state
    [ "$STATE" = down ] && return 3
    return 0
}

# The lock stays with this shell and reaches no program it starts: `9>&-` on
# the call closes descriptor 9 for everything the function runs, while bash
# keeps the lock itself in a copy that no child inherits.  A child left behind
# (a registration cut short, a systemctl that hangs) then holds no lock after
# this shell has gone.
if [ "$WORD" = off ]; then
    switch_off 9>&-
    exit $?
fi
switch_on 9>&-
RC=$?
[ "$RC" = 0 ] || exit "$RC"
exit 0
RACSWEOF
    rac_switch_settle $? || return 1
    chmod 755 /usr/local/sbin/sigmond-rac-switch || return 1
    bash -n /usr/local/sbin/sigmond-rac-switch || return 1
    # The retry timer.  The switch arms it when a station wants remote access
    # and no gateway answers, and the run that registers disarms it.
    # LogLevelMax and SyslogLevel keep systemd's own "Starting", "Started" and
    # "Deactivated" lines out of the journal: at one try in ten minutes they
    # would bury the one line the script writes when its reason changes.
    cat > /etc/systemd/system/sigmond-rac-register.service <<'RACREGSVCEOF'
[Unit]
Description=Sigmond remote access: register with the gateway, then bring the tunnel up if wanted
[Service]
Type=oneshot
TimeoutStartSec=900
LogLevelMax=notice
SyslogLevel=notice
ExecStart=/usr/local/sbin/sigmond-setup --rac-retry
RACREGSVCEOF
    [ $? -eq 0 ] || return 1
    cat > /etc/systemd/system/sigmond-rac-register.timer <<'RACREGTMREOF'
[Unit]
Description=Sigmond remote access: retry the gateway registration until it succeeds
[Timer]
OnActiveSec=10min
OnUnitInactiveSec=10min
RandomizedDelaySec=60
[Install]
WantedBy=timers.target
RACREGTMREOF
    [ $? -eq 0 ] || return 1
    # The pairing's re-assertion: after every boot, four times a day, and
    # whenever this host's sshd key changes (an identity restore does that).
    cat > /etc/systemd/system/sigmond-rac-pair.service <<'RACPAIRSVCEOF'
[Unit]
Description=Sigmond remote access: re-assert the pairing between this host and its decoder VM
After=pve-guests.service
[Service]
Type=oneshot
TimeoutStartSec=600
ExecStart=/usr/local/sbin/sigmond-setup --pair-vm
RACPAIRSVCEOF
    [ $? -eq 0 ] || return 1
    cat > /etc/systemd/system/sigmond-rac-pair.timer <<'RACPAIRTMREOF'
[Unit]
Description=Sigmond remote access: re-assert the VM pairing after boot and four times a day
[Timer]
OnBootSec=4min
OnUnitInactiveSec=6h
[Install]
WantedBy=timers.target
RACPAIRTMREOF
    [ $? -eq 0 ] || return 1
    cat > /etc/systemd/system/sigmond-rac-pair.path <<'RACPAIRPATHEOF'
[Unit]
Description=Sigmond remote access: re-assert the VM pairing when this host's sshd key changes
[Path]
PathChanged=/etc/ssh/ssh_host_ed25519_key.pub
Unit=sigmond-rac-pair.service
[Install]
WantedBy=paths.target
RACPAIRPATHEOF
    [ $? -eq 0 ] || return 1
    # A reload that fails costs nothing: `systemctl enable` reloads by itself.
    # The switch stands, so say so and return 0.  Plain echo: `say` belongs to
    # the wizard, and rac_retry replaces it.
    systemctl daemon-reload || echo "sigmond-wizard: systemctl daemon-reload failed; the units are written, and enabling the timer reloads systemd by itself." >&2
    return 0
}

# rac_install_host_files hands this the status of its write.  The new switch
# stands when that status reads 0, the file takes mode 755, it parses, and its
# last line reads `exit 0`: a copy cut short parses too, and only the last line
# shows the cut.  Otherwise the working copy comes back, or, on a first
# install, the broken file goes.  Either way this returns 1.
rac_switch_settle(){
    if [ "$1" = 0 ] && chmod 755 "$RAC_SWITCH" \
            && [ "$(tail -n 1 "$RAC_SWITCH")" = "exit 0" ] && bash -n "$RAC_SWITCH"; then
        rm -f "$RAC_SWITCH.old"
        return 0
    fi
    if [ -e "$RAC_SWITCH.old" ]; then mv -f "$RAC_SWITCH.old" "$RAC_SWITCH"; else rm -f "$RAC_SWITCH"; fi
    return 1
}

RAC_RETRY_NOTE=/run/sigmond/rac-retry.reason
rac_retry(){
    # What sigmond-rac-register.timer runs.  Register a station that could
    # not at install.  Then bring the tunnel up, but only if its operator
    # still wants it up.  A try that fails ends quietly: the timer tries
    # again, and the journal gets one line per reason, never one per try.
    local last want
    say(){ :; }
    LOG=/dev/null
    # No wish on file: nothing on this station has asked for remote access (a
    # station from before v3.71 whose owner declined reads so).  Nothing
    # registers it, by this timer or by hand.  The timer has nothing left to
    # try, so it goes too: one line now, and none every ten minutes.
    want=$(head -1 "$MARK_DIR/rac-desired" 2>/dev/null)
    case "$want" in
        on|off) ;;
        *)
            systemctl disable --now sigmond-rac-register.timer >/dev/null 2>&1
            echo "remote access: this station has recorded no wish for remote access, so nothing registers it. To turn it on, in the decoder VM: smd remote on"
            return 0 ;;
    esac
    if ! rac_register ensure; then
        last=$(cat "$RAC_RETRY_NOTE" 2>/dev/null)
        if [ "$RAC_FAIL" != "$last" ]; then
            mkdir -p "$(dirname "$RAC_RETRY_NOTE")" 2>/dev/null
            printf '%s\n' "$RAC_FAIL" > "$RAC_RETRY_NOTE" 2>/dev/null
            echo "remote access: not registered yet: $RAC_FAIL. This host tries again every 10 minutes; this line returns only when the reason changes."
        fi
        return 0
    fi
    rm -f "$RAC_RETRY_NOTE"
    echo "remote access: registered as RAC #$(cat "$MARK_DIR/rac-number" 2>/dev/null)"
    if [ "$(head -1 "$MARK_DIR/rac-desired" 2>/dev/null)" = on ]; then
        # The switch disarms the timer once it holds a registration.  Until it
        # has run, the timer stays armed: a switch that fails (its lock is
        # busy) leaves the next try something to do.
        SIGMOND_RAC_BY=timer "$RAC_SWITCH" on
        return 0
    fi
    # The operator holds the tunnel off and the station holds a registration:
    # nothing is left to retry.
    systemctl disable --now sigmond-rac-register.timer >/dev/null 2>&1
    echo "remote access: the operator holds it off, so the tunnel stays down"
    return 0
}

# ── the pairing: the decoder VM asks, this host alone acts ─────────────────
# tasks/plan-remote-access.md section 2.  The operator knows the VM, and this
# host has no keyboard once the install ends.  So the VM holds one ssh key,
# and that key buys one thing: sigmond-rac-switch, with one of its three
# words.  No shell, no forwarding, no other command.
#
# The VM can never create this pairing: the host's half lives in files that
# only root on this host writes.  And the host re-asserts both halves, at
# every boot and whenever its own sshd key changes.  On AC0G-B4 an identity
# restore replaced a host key eight minutes after the wizard had authorized
# the old one, and the fleet's login stayed broken for a week.
RAC_PAIR_USER=sigmond-remote
RAC_PAIR_HOME=/var/lib/sigmond-remote
RAC_PAIR_SUDOERS=/etc/sudoers.d/sigmond-remote
RAC_PAIR_HOSTPUB=/etc/ssh/ssh_host_ed25519_key.pub
RAC_PAIR_LOCK=/run/sigmond/rac-pair.lock
RAC_PAIR_FAIL=""
RAC_PAIR_WARN=""

rac_pair_key_ok(){
    # One ed25519 public key, as "type base64", and not a byte more.  What
    # comes back through the guest agent is data from another machine.  The
    # match covers the whole string, so a second line cannot ride along: in
    # an authorized_keys file a second line would stand as a key of its own,
    # with no restriction on it.
    local LC_ALL=C
    [[ "$1" =~ ^ssh-ed25519\ [A-Za-z0-9+/]{60,100}={0,2}$ ]]
}

rac_have_sudo(){ command -v sudo >/dev/null 2>&1 && command -v visudo >/dev/null 2>&1; }

rac_pair_sudoers(){
    cat <<SUDOEOF
# Written by sigmond-setup --pair-vm (sigmond tasks/plan-remote-access.md).
# The decoder VM holds one ssh key.  That key reaches this account, and this
# account may run one script as root, with no argument.  The script reads the
# requested word (on, off or status) from SSH_ORIGINAL_COMMAND.
Defaults:$RAC_PAIR_USER env_reset
Defaults:$RAC_PAIR_USER env_keep = "SSH_ORIGINAL_COMMAND"
Defaults:$RAC_PAIR_USER !requiretty
$RAC_PAIR_USER ALL=(root) NOPASSWD: $RAC_SWITCH ""
SUDOEOF
}

rac_pair_authorized_line(){
    # rac_pair_authorized_line <VM address> <VM public key>
    rac_pair_key_ok "$2" || return 1
    printf 'restrict,from="%s",command="sudo -n %s" %s sigmond-rac-switch\n' "$1" "$RAC_SWITCH" "$2"
}

rac_pair_guest_script(){
    # rac_pair_guest_script <host address> <host sshd public key>
    # The script the guest runs.  It travels base64-encoded, and it holds
    # ASCII only: Proxmox sends a guest-agent request with a non-ASCII
    # character short, and the agent can then go deaf.
    #
    # The guest's bash reads this script from a pipe (echo ... | base64 -d |
    # bash), so any command inside that reads stdin takes the rest of the
    # script with it.  Bash reads a whole function before it runs any of it,
    # and main runs with stdin from /dev/null.  `set -e` and `umask 077` stand
    # first inside main: both bind the whole shell either way.
    cat <<GUESTEOF
main(){
    set -e
    umask 077
    D=/etc/sigmond
    M=/etc/update-motd.d
    K=\$D/rac-switch.key
    install -d -m 755 "\$D"
    if [ ! -s "\$K" ] || [ ! -s "\$K.pub" ]; then
        rm -f "\$K" "\$K.pub"
        ssh-keygen -q -t ed25519 -N '' -C sigmond-rac-switch -f "\$K"
    fi
    chown root:root "\$K" "\$K.pub"
    chmod 600 "\$K"
    chmod 644 "\$K.pub"
    printf '%s %s\n' '$1' '$2' > "\$D/rac-switch.known_hosts.new"
    chown root:root "\$D/rac-switch.known_hosts.new"
    chmod 644 "\$D/rac-switch.known_hosts.new"
    mv -f "\$D/rac-switch.known_hosts.new" "\$D/rac-switch.known_hosts"
    if [ -d "\$M" ]; then
        printf '%s\n' '#!/bin/sh' \\
            '# One line on the state of remote access.  Written from the Proxmox host' \\
            '# by sigmond-setup --pair-vm.  Prints nothing where smd lacks the verb.' \\
            'command -v smd >/dev/null 2>&1 && smd remote motd 2>/dev/null' \\
            'exit 0' > "\$M/72-sigmond-remote"
        chmod 755 "\$M/72-sigmond-remote"
    fi
}
main </dev/null
GUESTEOF
}

rac_pair_verify_script(){
    # rac_pair_verify_script <host address>
    # One request from the VM, through sshd and sudo, to the switch.  It also
    # leaves the answer where the VM's login banner reads it.
    # Same shape as the guest script, for the same reason: this one runs ssh,
    # and a real ssh client reads stdin to the end unless it gets -n.  Both
    # protections stand, the function and the -n.
    # The state comes from the first line that begins "Remote access: ", so a
    # warning that ssh prints ahead of the switch's answer costs nothing.  The
    # whole output stays for the failure print.
    cat <<VERIFYEOF
main(){
    D=/etc/sigmond
    C=/var/lib/sigmond/remote-access.json
    out=\$(ssh -n -T -F /dev/null -i "\$D/rac-switch.key" -o BatchMode=yes -o IdentitiesOnly=yes \\
              -o StrictHostKeyChecking=yes -o UserKnownHostsFile="\$D/rac-switch.known_hosts" \\
              -o GlobalKnownHostsFile=/dev/null -o ConnectTimeout=8 -o LogLevel=ERROR \\
              $RAC_PAIR_USER@$1 status 2>&1)
    rc=\$?
    case "\$rc" in 0|3) ;; *) printf '%s\n' "\$out" | tail -3; exit 1 ;; esac
    answer=\$(printf '%s\n' "\$out" | sed -n '/^Remote access: /{p;q;}')
    case "\$answer" in
        "Remote access: on") s=on ;;
        "Remote access: off") s=off ;;
        "Remote access: wanted on, not up") s=down ;;
        *) printf '%s\n' "\$out" | head -3; exit 1 ;;
    esac
    mkdir -p "\$(dirname "\$C")"
    # Off, and the tunnel service runs all the same: the switch says so in its
    # NOTE line, and the login banner must carry it.
    if [ "\$s" = off ] && printf '%s\n' "\$out" | grep -q '^  NOTE: the tunnel service runs all the same\.'; then
        printf '{"state": "%s", "checked_at": "%s", "tunnel_runs": true}\n' "\$s" "\$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "\$C"
    else
        printf '{"state": "%s", "checked_at": "%s"}\n' "\$s" "\$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "\$C"
    fi
    chmod 644 "\$C"
    exit 0
}
main </dev/null
VERIFYEOF
}

rac_pair_vm(){
    # Pair the decoder VM with this host, or re-assert a pairing that stands.
    # Safe at any time: it writes both halves again from what holds now.
    #   this host's half   the account, its sudoers lines, the VM's public key
    #   the VM's half      its key pair (made in the VM; the private half
    #                      never leaves it), and this host's sshd key as the
    #                      only host that key talks to
    # Returns 0 once a request from the VM has reached the switch script.
    # Returns 2 when another pairing runs at this moment, and does nothing:
    # the timer carries OnBootSec, so arming it long after boot starts the
    # service at once, beside the wizard's own pairing.
    # Otherwise it returns 1 with the reason in RAC_PAIR_FAIL.  RAC_PAIR_WARN
    # holds a warning when the re-assertion units could not start, or when the
    # outcome could not be put on file.
    # Every run that did the work records its outcome (rac_pair_record).  The
    # run that found another one at work records nothing: that one does.
    local rc word=not-paired
    RAC_PAIR_FAIL=""
    RAC_PAIR_WARN=""
    mkdir -p "$(dirname "$RAC_PAIR_LOCK")" 2>/dev/null
    if ! { exec 7>"$RAC_PAIR_LOCK"; } 2>/dev/null; then
        RAC_PAIR_FAIL="cannot open the pairing lock ($RAC_PAIR_LOCK)"
        rc=1
    elif ! flock -n 7; then
        exec 7>&-
        RAC_PAIR_FAIL="another pairing is running"
        return 2
    else
        # `7>&-` on the call keeps the lock with this shell and out of every
        # program the pairing starts (qm, apt-get, systemctl): bash holds the
        # lock in a copy no child inherits.  A child left behind then holds no
        # lock after this shell has gone, and the next pairing can run.
        rac_pair_locked 7>&-; rc=$?
    fi
    [ "$rc" = 0 ] && word=paired
    if ! rac_pair_record "$word" "$RAC_PAIR_FAIL"; then
        RAC_PAIR_WARN="${RAC_PAIR_WARN:+$RAC_PAIR_WARN; }could not record the pairing's outcome in $MARK_DIR/rac-pairing; the console panel reads that file to choose the instruction it prints"
    fi
    exec 7>&-
    return $rc
}

rac_pair_record(){
    # rac_pair_record paired | not-paired <reason>
    # One line in $MARK_DIR/rac-pairing, the outcome of the last pairing:
    #     paired <UTC>          or          not-paired <UTC> <reason>
    # The console panel reads the first word: it names the decoder VM's
    # command (smd remote ...) only where a pairing stands, and the host's own
    # switch everywhere else.  pm-align --status prints the line.  The reason
    # stays on one line of printable ASCII, 200 characters at most.
    local f="$MARK_DIR/rac-pairing" line why
    line="$1 $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [ "$1" != paired ]; then
        why=$(printf '%s' "${2:-}" | LC_ALL=C tr '\n\r\t' '   ' | LC_ALL=C tr -cd '\040-\176' | cut -c1-200)
        line="$line $why"
    fi
    if { mkdir -p "$MARK_DIR" && printf '%s\n' "$line" > "$f.new" && chmod 644 "$f.new" \
         && mv -f "$f.new" "$f"; } 2>/dev/null; then
        return 0
    fi
    rm -f "$f.new" 2>/dev/null
    return 1
}

rac_pair_locked(){
    local pm vm hostkey vmkey line tmp i
    # The units first.  A pairing that fails here must try again by itself:
    # a host with no keyboard cannot wait for an operator.  A station whose
    # guest agent stood deaf at install pairs at the next boot, or in six
    # hours, without anyone.
    if ! rac_install_host_files; then
        RAC_PAIR_FAIL="could not install the remote-access files on this host (the switch or its units)"
        return 1
    fi
    # The private link is vmbr1's /30: this host holds its first address and
    # the decoder VM the next one.  Read that bridge first.  A host can carry
    # another /30 (a WireGuard link, a point-to-point uplink) and `ip` may list
    # it first; mgmt_vm_ip reads "the first /30 on this host" and would then
    # name a machine that is not the VM.  So the VM's address follows from the
    # bridge's here, unless SIGMOND_MGMT_VM names it.  Only a host whose vmbr1
    # carries no /30 falls back to the first /30 it has, and to mgmt_vm_ip.
    pm=$(ip -4 -o addr show dev vmbr1 2>/dev/null | awk '$4 ~ /\/30$/ {split($4,p,"/"); print p[1]; exit}')
    if [ -n "$pm" ] && [ -z "${SIGMOND_MGMT_VM:-}" ]; then
        vm=$(awk -v ip="$pm" 'BEGIN{n=split(ip,o,"."); printf "%s.%s.%s.%d\n",o[1],o[2],o[3],o[4]+1}')
    else
        vm=$(mgmt_vm_ip)
        [ -n "$pm" ] || pm=$(ip -4 -o addr show 2>/dev/null | awk '$4 ~ /\/30$/ {split($4,p,"/"); print p[1]; exit}')
    fi
    if [ -z "$pm" ]; then
        RAC_PAIR_FAIL="this host has no private link to its decoder VM (no /30 address)"
        return 1
    fi
    hostkey=$(awk '{print $1" "$2; exit}' "$RAC_PAIR_HOSTPUB" 2>/dev/null)
    if ! rac_pair_key_ok "$hostkey"; then
        RAC_PAIR_FAIL="cannot read this host's sshd key ($RAC_PAIR_HOSTPUB)"
        return 1
    fi
    # Armed before every step that can fail by chance (the guest agent, sudo, the
    # account, the guest script, the proof), and after the two checks above, which
    # fail by structure: a host with no /30 link or no sshd key can never pair, and
    # would run a unit that fails at every boot and every six hours.
    if ! systemctl enable --now sigmond-rac-pair.timer sigmond-rac-pair.path >/dev/null 2>&1; then
        RAC_PAIR_WARN="the pairing's re-assertion units did not start (systemctl enable --now failed)"
    fi
    # Each ping under a timeout: on a channel gone stale `qm agent ping` can
    # hang, and this run would then sit on the pairing's lock.
    for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
        timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 </dev/null && break
        # One reset after the first silent ping: an agent that stands deaf
        # hears again at once, and this run need not fail over it.
        if [ "$i" = 1 ] && qga_reset "$VMID"; then
            qga_note_reset pairing
            continue
        fi
        if [ "$i" = 12 ]; then
            RAC_PAIR_FAIL="the guest agent in VM $VMID does not answer"
            return 1
        fi
        sleep 5
    done
    # Stock Proxmox carries no sudo.  A v3.71 install brings it on the stick;
    # a station installed earlier fetches it here, once.
    if ! rac_have_sudo; then
        say "this host has no sudo; installing it (the pairing account needs its one sudoers line)"
        timeout 120 apt-get -qq update >>"$LOG" 2>&1
        DEBIAN_FRONTEND=noninteractive timeout 300 apt-get -y -qq install sudo >>"$LOG" 2>&1
        if ! rac_have_sudo; then
            RAC_PAIR_FAIL="this host has no sudo and could not install it; run: apt-get install sudo, then: sigmond-setup --pair-vm"
            return 1
        fi
    fi

    # This host's half, part one: an account that can do one thing.
    if ! id "$RAC_PAIR_USER" >/dev/null 2>&1 \
       && ! useradd --system --home-dir "$RAC_PAIR_HOME" --shell /bin/sh "$RAC_PAIR_USER"; then
        RAC_PAIR_FAIL="could not create the account $RAC_PAIR_USER"
        return 1
    fi
    # No password that could ever match, and a shell sshd can run the forced
    # command with.  Root owns the home and its .ssh: an account that owns the
    # file with its key could change its own key.
    if ! { usermod -p '*' -s /bin/sh "$RAC_PAIR_USER" >/dev/null 2>&1 \
           && install -d -m 755 "$RAC_PAIR_HOME" "$RAC_PAIR_HOME/.ssh" \
           && chown root:root "$RAC_PAIR_HOME" "$RAC_PAIR_HOME/.ssh"; }; then
        RAC_PAIR_FAIL="could not prepare $RAC_PAIR_HOME for the account (root must own it)"
        return 1
    fi
    # sudo skips a file whose name holds a dot, so the candidate cannot take
    # effect before visudo has passed it.
    tmp="$RAC_PAIR_SUDOERS.new"
    if ! { rac_pair_sudoers > "$tmp" && chmod 440 "$tmp" \
           && visudo -c -q -f "$tmp" >/dev/null 2>&1 && mv -f "$tmp" "$RAC_PAIR_SUDOERS"; }; then
        rm -f "$tmp"
        RAC_PAIR_FAIL="could not install the sudoers lines ($RAC_PAIR_SUDOERS)"
        return 1
    fi

    # The VM's half.
    if ! gexec 60 "echo $(rac_pair_guest_script "$pm" "$hostkey" | base64 -w0) | base64 -d | bash"; then
        RAC_PAIR_FAIL="could not write the pairing into VM $VMID (see $LOG)"
        return 1
    fi
    # The line that starts with the key type: a guest profile script may print
    # a line of its own ahead of the key.
    vmkey=$(guest_out 20 "cat /etc/sigmond/rac-switch.key.pub" | awk '$1=="ssh-ed25519"{print $1" "$2; exit}')
    if ! line=$(rac_pair_authorized_line "$vm" "$vmkey"); then
        RAC_PAIR_FAIL="VM $VMID returned no usable public key"
        return 1
    fi

    # This host's half, part two: that one key, in a file the account cannot
    # write.
    tmp="$RAC_PAIR_HOME/.ssh/authorized_keys.new"
    if ! { printf '%s\n' "$line" > "$tmp" && chown root:root "$tmp" && chmod 644 "$tmp" \
           && mv -f "$tmp" "$RAC_PAIR_HOME/.ssh/authorized_keys"; }; then
        rm -f "$tmp"
        RAC_PAIR_FAIL="could not write $RAC_PAIR_HOME/.ssh/authorized_keys"
        return 1
    fi

    # Prove it.
    if ! gexec 60 "echo $(rac_pair_verify_script "$pm" | base64 -w0) | base64 -d | bash"; then
        RAC_PAIR_FAIL="both halves stand on disk, but a request from the VM did not reach the switch (see $LOG)"
        return 1
    fi
    return 0
}

# ── RAC on/off switches (no wizard rerun needed) ────────────────────────────
case "${1:-}" in
    --rac-register)
        # Registration alone.  No question, no decoder VM, and no start:
        # this branch exits before the wizard waits for anything.
        if rac_register ensure; then
            say "registered: RAC #$(cat "$MARK_DIR/rac-number" 2>/dev/null) via $(cat "$MARK_DIR/rac-tier" 2>/dev/null)"
            exit 0
        fi
        say "not registered: $RAC_FAIL"
        exit 1;;
    --rac-retry)
        rac_retry
        exit 0;;
    --pair-vm)
        # Pair the decoder VM with this host, or re-assert the pairing.  A
        # station installed before v3.71 runs this once, by hand or through
        # pm-align.  sigmond-rac-pair runs it after every boot and whenever
        # this host's sshd key changes.
        rac_pair_vm; _prc=$?
        [ -n "$RAC_PAIR_WARN" ] && say "WARN: $RAC_PAIR_WARN"
        if [ "$_prc" = 0 ]; then
            say "paired: the decoder VM can run smd remote on, smd remote off and smd remote status"
            exit 0
        fi
        if [ "$_prc" = 2 ]; then
            # The pairing that holds the lock does the work.
            say "another pairing is running; nothing done"
            exit 0
        fi
        say "NOT paired: $RAC_PAIR_FAIL"
        exit 1;;
    --rac-off|--rac-on)
        # One switch.  These two spellings from before v3.71 run the script
        # that `smd remote` reaches from the decoder VM.  The host files are
        # written afresh first.  Where that fails (a full disk) and a switch
        # stands at its path, that switch runs all the same: an owner must be
        # able to close the station, as `--rac-off` could before v3.71.
        if ! rac_install_host_files; then
            if [ ! -x "$RAC_SWITCH" ]; then
                say "could not install the remote-access files on this host (the switch or its units)"
                exit 1
            fi
            say "WARN: could not refresh the remote-access files on this host (the switch or its units); running the switch that stands"
        fi
        exec "$RAC_SWITCH" "${1#--rac-}";;
    --rac-upgrade)
        # rob 2026-08-09: a station that had to settle for a lower tier should
        # be able to climb once a better gateway comes up, without a rebuild.
        # This probes the ladder top-down and only acts if something strictly
        # better than the current tier answers.
        CUR=$(cat "$MARK_DIR/rac-tier" 2>/dev/null || echo "(none)")
        echo "current RAC tier: $CUR"
        echo "probing the ladder, best first:"
        BEST=""; BEST_LBL=""
        while IFS='|' read -r _l _c _r _t _p; do
            [ -n "$_c" ] || continue
            # a tier is up iff its frps TUNNEL port answers (35736 TLS /
            # 35735 legacy); the registrar is shared and proves nothing
            if timeout 5 bash -c "exec 3<>/dev/tcp/$_c/$_p" 2>/dev/null; then
                echo "  $_l — ANSWERS ($_c:$_p)"
                [ -z "$BEST" ] && { BEST="$_r"; BEST_LBL="$_l"; }
            else
                echo "  $_l — no answer ($_c:$_p)"
            fi
        done <<TIEREOF
$RAC_TIERS
TIEREOF
        if [ -z "$BEST" ]; then
            echo "no gateway tier is reachable right now — nothing to do."
            exit 1
        fi
        if [ "$BEST_LBL" = "$CUR" ]; then
            echo "already on the best tier that answers ($CUR) — nothing to do."
            exit 0
        fi
        echo ""
        echo "a better tier is available: $BEST_LBL  (currently $CUR)"
        echo "re-registering re-runs the setup questions; the RAC number is sticky,"
        echo "so the station keeps its assigned channels."
        rd -r -p "upgrade now? [y/N] " _u
        case "${_u:-N}" in
            [Yy]*) exec "$0" --reconfigure ;;
            *) echo "left on $CUR; run this again any time." ; exit 0 ;;
        esac
        ;;
esac

if [ -e "$CONF_MARK" ] && [ "${1:-}" != "--reconfigure" ]; then
    say "already configured ($(cat "$CONF_MARK")). Run 'sigmond-setup --reconfigure' to redo."
    exit 0
fi

# Ctrl-C is the observed trigger for the stale-channel fault below, so leave
# the operator knowing both that nothing was applied and that the next run may
# have to clear the channel (it now does that itself).
trap 'echo; say "interrupted — nothing was applied. Rerun: sigmond-setup --reconfigure"; exit 130' INT

# ── wait for decoder VM + guest agent ───────────────────────────────────────
say "waiting for decoder VM $VMID and its guest agent..."
# </dev/null on every qm call: qm inherits our stdin and can slurp piped
# answers — one stolen line desynced every later prompt (nested test,
# 2026-08-11: nine grid rejections = exactly one line short).
# Say something while waiting.  This loop can run five minutes, and printing
# nothing for five minutes is indistinguishable from a hang — which is exactly
# how it was reported (rob, 2026-09-29).  A wait that names what it is waiting
# for, and counts, is a wait; a silent one is a bug report.
if ! qm status "$VMID" >/dev/null 2>&1 </dev/null; then
    say "  VM $VMID does not exist on this host. Known VMs:"
    qm list 2>/dev/null </dev/null | sed 's/^/    /'
    say "  If the decoder VM has a different id, rerun as:"
    say "      SIGMOND_VMID=<id> sigmond-setup --reconfigure"
    exit 1
fi
# >>> agent_wait (tests/test_wizard_guest_senders.py loads exactly this block)
_vmip=$(mgmt_vm_ip)
_healed=0
for i in $(seq 1 60); do
    timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 </dev/null && break
    [ "$i" = 1 ] && qm start "$VMID" >/dev/null 2>&1 </dev/null
    # ONE RESET FIRST.  At the second silent ping the VM has had its start, so
    # the agent may stand deaf: it reads every request and answers none
    # (tasks/plan-guest-agent-requests.md).  One 0xFF byte resets its parser.
    # Nothing inside the VM restarts, and the private link plays no part.
    if [ "$i" = 2 ] && qga_reset "$VMID"; then
        qga_note_reset wizard
        say "  the guest agent answered after one parser reset"
        continue
    fi
    # ⚡ SELF-HEAL A STALE CHANNEL, the second resort.  Observed repeatedly on AI6VN 2026-09-29:
    # the agent is `active` inside the guest with NRestarts=0, having served
    # requests moments earlier, while the host insists "QEMU guest agent is
    # not running".  It is the host<->guest channel that has desynced, not the
    # agent, and restarting the agent inside the VM clears it every time.
    # Empirically the trigger is interrupting the wizard (Ctrl-C) — rob hit it
    # three times in a row that way.
    #
    # Waiting five minutes and then telling the operator to go and do this by
    # hand is not a fix when we can reach the VM ourselves over the host-only
    # link, which does not involve the agent at all.  Try ONCE, after 30s, so
    # a VM that is merely still booting is given a fair chance first.
    if [ "$i" = 6 ] && [ "$_healed" = 0 ]; then
        _healed=1
        say "  no answer after 30s — the channel may be stale rather than the"
        say "  VM down. Restarting qemu-guest-agent inside the VM via $_vmip"
        if ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no \
               -o UserKnownHostsFile=/dev/null -o ConnectTimeout=6 \
               "sigmond@${_vmip}" 'sudo -n systemctl restart qemu-guest-agent' \
               >/dev/null 2>&1; then
            say "  restarted — waiting for it to answer"
        else
            say "  could not reach the VM over ssh; continuing to wait"
        fi
    fi
    [ $((i % 3)) = 0 ] && say "  still waiting for the guest agent ($((i * 5))s of 300s)..."
    sleep 5
done
if ! timeout 10 qm agent "$VMID" ping >/dev/null 2>&1 </dev/null; then
    say "ERROR: guest agent in VM $VMID not answering after 300s."
    say "  The VM can be up and reachable over ssh while the agent channel is"
    say "  stale — the service inside the guest shows active, but the host still"
    say "  says 'QEMU guest agent is not running' (seen on AI6VN 2026-09-29)."
    say "  Try, inside the VM:   sudo systemctl restart qemu-guest-agent"
    say "  Then rerun: sigmond-setup --reconfigure"
    say "  If the VM was never imported, plug in the Sigmond USB to trigger it."
    exit 1
fi
# <<< agent_wait

# rd — read that ABORTS on EOF instead of letting a validation loop spin
# forever when piped stdin runs dry (nested-test hang, 2026-08-11).  On a
# live tty read only fails on EOF (Ctrl-D), where aborting is also right.
rd(){ read "$@" || { echo; say "stdin closed (EOF) — aborting wizard; nothing applied. Rerun: sigmond-setup"; exit 1; }; }


# uploads_toml — the [uploads] block a FRESH install writes: the site sink
# switch starts `off` (stored as the legacy discard) until the operator checks
# the station and runs `smd sink upload` (tasks/plan-sink-control.md §10.3
# item 2).  A configured host prints nothing; `smd config profile-install`
# then keeps whatever block the station already carries.
uploads_toml(){
    [ -e "$CONF_MARK" ] && return 0
    printf '\n[uploads]\nenabled = false\nmode    = "discard"\nreason  = "new station: the site sink switch stays off until someone runs smd sink upload"\n'
}

# ── prompts ─────────────────────────────────────────────────────────────────
# Each question is a function so the final review screen can re-run any
# single one: the operator sees everything they typed and picks a number
# to fix a mistake BEFORE anything is applied (rob mistyped his reporter
# ID on the first Kamrui install, 2026-07-27 — the old flow's only exits
# were apply-it-wrong or abort-and-reinstall).
echo ""
echo "──────────────────────────────────────────────────────"
echo "  Sigmond station setup — a few questions and you're on the air"
echo "  (you'll get a review screen to fix any answer before it's applied)"
echo "──────────────────────────────────────────────────────"

# ── external-device pre-flight ─────────────────────────────────────────────
# Runs BEFORE the questions, and must ask WHICHEVER SIDE currently owns the
# USB controllers.  On a first install that is the Proxmox host: passthrough
# is configured by firstboot after this wizard and needs a reboot.  After
# that reboot the controllers belong to vfio-pci and the host has no USB at
# all, so `sigmond-setup --reconfigure` asking host lsusb reported every
# device as NOT DETECTED even with everything plugged in and working.
#
# A device can also be present on the host and still never reach the VM, if
# it sits on a USB controller that was not passed through — routinely the
# case for a front USB-C port, which is often a separate xHCI from the rear
# ports.  "Not detected" and "on the wrong controller" need different
# actions, so the two are distinguished below rather than both reported as
# absent.
#
# This NEVER blocks.  A station with nothing plugged in must still reach the
# end of the wizard, because the single most valuable outcome of a failed
# install is a working RAC tunnel: it lets a remote admin connect and finish
# the job.  So we report what is missing, plainly, and carry on.
HAVE_GPSDO=0; HAVE_RX888=0; HAVE_TS1=0; HAVE_MAG=0; GPSDO_MODEL=""
RX888_ID=""; GPSDO_ID=""; MAG_ID=""; PREFLIGHT_SRC=host; PREFLIGHT_WRONG_CTRL=0

# PCI address of the USB controller a given VID:PID sits behind, or "" if the
# device is not on this machine's bus.  /sys/bus/usb/devices is flat and
# hub-transparent — a device at 4-1.4 behind a hub at 4-1 is its own entry —
# so hub depth is irrelevant here; the controller is the last PCI address in
# the resolved sysfs path.
_usb_ctrl() {
    local id="$1" d v pr
    for d in /sys/bus/usb/devices/*; do
        [ -r "$d/idVendor" ] || continue
        v=$(cat "$d/idVendor" 2>/dev/null); pr=$(cat "$d/idProduct" 2>/dev/null)
        if [ "$v:$pr" = "$id" ]; then
            readlink -f "$d" 2>/dev/null \
              | grep -oE '0000:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]' | tail -1
            return 0
        fi
    done
    return 0
}

# Is that controller in the set firstboot will hand to the VM?  Unknown
# (no layout yet) is reported as unknown, never as a pass.
_ctrl_passed() {
    local addr="${1#0000:}" ids
    [ -n "$addr" ] || return 2
    ids=$(sed -n 's/^USB_VID_DID=//p' /etc/sigmond-appliance/layout.env 2>/dev/null | tr -d "'\"")
    [ -n "$ids" ] || return 2
    lspci -nn -s "$addr" 2>/dev/null | grep -qE "\[(${ids//,/|})\]"
}

# One line per device: found, and if found, whether the VM will actually see
# it.  This is the sentence that distinguishes "nothing plugged in" from
# "plugged into a port whose controller stays on the host".
_dev_line() {
    local ok="$1" label="$2" id="$3" note="$4" ctrl
    if [ "$ok" != 1 ]; then
        printf '  ✗ %-22s — NOT DETECTED (%s)\n' "$label" "$note"
        return
    fi
    if [ "$PREFLIGHT_SRC" != "host" ]; then
        printf '  ✓ %s\n' "$label"          # asked the VM: seeing it IS the proof
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

preflight_devices() {
    local usb
    PREFLIGHT_SRC=host
    PREFLIGHT_WRONG_CTRL=0
    # Controllers already handed over?  Then the host has no USB and the VM
    # is the only place worth asking.
    if grep -qE '^hostpci[0-9]+:' "/etc/pve/qemu-server/${VMID}.conf" 2>/dev/null; then
        PREFLIGHT_SRC=vm
        usb=$(qm guest exec "$VMID" --timeout 20 -- /usr/bin/lsusb 2>/dev/null </dev/null \
              | python3 -c 'import json,sys
try:  print(json.load(sys.stdin).get("out-data",""))
except Exception: pass' 2>/dev/null || true)
    else
        usb=$(lsusb 2>/dev/null || true)
    fi
    # Keep the id that actually matched, not just yes/no: the controller
    # lookup below needs it to answer "and can the VM see it?".
    # RX888 — the PID set the bring-up branch below matches on too.
    RX888_ID=$(echo "$usb" | grep -oiE '04b4:00(f[013]|bc)|f4b3:0100' | head -1 | tr 'A-Z' 'a-z')
    [ -n "$RX888_ID" ] && HAVE_RX888=1
    # Leo Bodnar GPSDOs, per gpsdo-monitor/models/registry.py:
    #   LBE-1420 0x2443 · LBE-1421 0x2444 · LBE-1423 0x226f · LBE-Mini 0x2211
    if   echo "$usb" | grep -qiE '1dd2:2211'; then HAVE_GPSDO=1; GPSDO_MODEL="LBE-Mini"; GPSDO_ID="1dd2:2211"
    elif echo "$usb" | grep -qiE '1dd2:2444'; then HAVE_GPSDO=1; GPSDO_MODEL="LBE-1421"; GPSDO_ID="1dd2:2444"
    elif echo "$usb" | grep -qiE '1dd2:2443'; then HAVE_GPSDO=1; GPSDO_MODEL="LBE-1420"; GPSDO_ID="1dd2:2443"
    elif echo "$usb" | grep -qiE '1dd2:226f'; then HAVE_GPSDO=1; GPSDO_MODEL="LBE-1423"; GPSDO_ID="1dd2:226f"
    fi
    # TS-1 TimeSync: the USB interface enumerates as an Adafruit SAMD21
    # module, so the VID/PID alone is not proof — confirmed by asking the
    # CLI, which answers with a "TimeSync vN.N, Board ID #..." banner.
    echo "$usb" | grep -qiE '239a:801e' && HAVE_TS1=1
    # RM3100 magnetometer via the Pololu isolated USB-I2C adapter.
    MAG_ID=$(echo "$usb" | grep -oiE '1ffb:250[23]' | head -1 | tr 'A-Z' 'a-z')
    [ -n "$MAG_ID" ] && HAVE_MAG=1

    echo ""
    if [ "$PREFLIGHT_SRC" = vm ]; then
        echo "  ── Attached equipment (as seen by the decoder VM) ───"
    else
        echo "  ── Attached equipment (as seen by the Proxmox host) ─"
    fi
    _dev_line "$HAVE_RX888" "RX888 SDR"           "$RX888_ID"  "no HF reception until fitted"
    _dev_line "$HAVE_GPSDO" "GPSDO ($GPSDO_MODEL)" "$GPSDO_ID" "timing falls back to NTP"
    # ⚡ PRESENT IS NOT THE SAME AS LOCKED, and this list is where an operator
    # looks for device state -- a bare "✓ GPSDO (LBE-Mini)" reads as "all
    # good" for a unit that is flashing red with no satellites.  rob, looking
    # for it here and not finding it (2026-09-29): "where in the dialog will
    # it print out the gpsdo has no sats? I don't see that in the attached
    # equipment list."  The grid step says it, but that is several questions
    # later and only in passing.  Say it where it is looked for.
    [ "$HAVE_GPSDO" = 1 ] && gpsdo_preflight_note
    _dev_line "$HAVE_TS1"   "TS-1 TimeSync"       "239a:801e"  "no ns-class timing"
    _dev_line "$HAVE_MAG"   "RM3100 magnetometer" "$MAG_ID"    "no magnetometer data"
    if [ "${PREFLIGHT_WRONG_CTRL:-0}" = 1 ]; then
        echo ""
        echo "  One or more devices are on a USB controller that is NOT handed to"
        echo "  the decoder VM.  They work on this host but the station cannot use"
        echo "  them.  A hub does not cause this — a hub is transparent — but the"
        echo "  PORT the hub is plugged into decides which controller it is on."
    fi

    if [ "$HAVE_RX888" = 0 ] || [ "$HAVE_GPSDO" = 0 ] || [ "$HAVE_TS1" = 0 ] || [ "$HAVE_MAG" = 0 ]; then
        echo ""
        echo "  Missing equipment does not stop this install.  The station comes"
        echo "  up with whatever you have fitted and stays dormant for the rest."
        echo "  Attach the missing equipment whenever it arrives, then:"
        echo ""
        echo "      smd status    names what the station can now adopt"
        echo "      smd adopt     asks first, then starts it"
        echo ""
        echo "  Nothing starts on its own.  Setup continues."
    fi
    echo "──────────────────────────────────────────────────────"
}

# ── GPSDO → Maidenhead ─────────────────────────────────────────────────────
# gpsdo-monitor computes this properly (nmea.py maidenhead(), and its own
# comment says the position exists for "the station's grid square at install
# time") — but it lives in the decoder VM, which cannot see the GPSDO until
# passthrough happens after this wizard.  So read NMEA straight off the
# device here.  This value is only the PROMPT DEFAULT; the authoritative
# position is re-asserted from the GPSDO after bring-up by
# sigmond-location-check ("location authority first (GPSDO definitive)").

# ── is the GPSDO actually locked? ───────────────────────────────────────────
# ⚡ The wizard is the ONLY place this surfaces during an install, and it was
# not being said: the pre-flight listed the GPSDO as present and the grid step
# explained it could not be read, but nothing reported whether it had a fix
# (rob, 2026-09-29, whose LBE-Mini was present and flashing red).
#
# The LBE-Mini speaks UBX over HID, which this shell cannot parse — but
# gpsdo-monitor can, and it runs in the decoder VM where the GPSDO is passed
# through.  So ask it.  This also means an LBE-Mini CAN supply a grid after
# all, by way of the monitor, which the NMEA path could never do.
#
# Sets GPSDO_FIX / GPSDO_SATS / GPSDO_GRID_UBX; returns 1 if nothing answered.
gpsdo_lock_report() {
    GPSDO_FIX=""; GPSDO_SATS=""; GPSDO_GRID_UBX=""
    # If the GPSDO was seen on the HOST, passthrough has not happened yet and
    # the VM cannot see the device at all — asking it would report a spurious
    # "no GPSDO" rather than the truth.
    [ "${PREFLIGHT_SRC:-host}" = "vm" ] || return 1
    local json
    json=$(guest_out 35 'for b in /usr/local/bin/gpsdo-monitor \
                             /opt/gpsdo-monitor/venv/bin/gpsdo-monitor; do
                           [ -x "$b" ] && exec "$b" status 2>/dev/null
                         done')
    [ -n "$json" ] || return 1
    local parsed
    parsed=$(printf '%s' "$json" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if isinstance(d, list):
    d = d[0] if d else {}
h = (d or {}).get("health") or {}
fix = h.get("gps_fix")
sats = h.get("sats_used")
grid = h.get("grid")
if h.get("gps_locked") and not fix:
    fix = "lock"
print("%s|%s|%s" % (fix or "", "" if sats is None else sats, grid or ""))
' 2>/dev/null) || return 1
    [ -n "$parsed" ] || return 1
    GPSDO_FIX="${parsed%%|*}"
    GPSDO_SATS=$(echo "$parsed" | cut -d'|' -f2)
    GPSDO_GRID_UBX=$(echo "$parsed" | cut -d'|' -f3)
    return 0
}

# The equipment list's lock annotation.  Kept a function, not inlined in
# preflight_devices, so it can be tested without standing up the whole
# pre-flight (which needs a live VM, USB controller lookups and a guest agent).
gpsdo_preflight_note() {
    gpsdo_lock_report || return 0        # unknown: say nothing here, the grid
                                         # step explains it properly
    case "$GPSDO_FIX" in
        ""|"no_fix"|"none")
            printf '      ⚠ but NO GPS LOCK — %s satellites in use. Needs an antenna with\n' "${GPSDO_SATS:-0}"
            printf '        sky view; until it locks its output is undisciplined.\n' ;;
        *)
            printf '      GPS lock %s, %s satellites\n' "$GPSDO_FIX" "${GPSDO_SATS:-?}" ;;
    esac
}

# One line describing lock state, for whichever branch needs it.
gpsdo_say_lock() {
    case "$GPSDO_FIX" in
        ""|"no_fix"|"none")
            echo "  ⚠ NO GPS LOCK: the GPSDO is powered and running but is not"
            echo "    tracking satellites (${GPSDO_SATS:-0} in use). It needs an antenna with a"
            echo "    view of the sky; a flashing red LED means exactly this."
            echo "    Its output keeps running undisciplined until it locks, so"
            echo "    the station will capture against a free-running clock." ;;
        *)
            echo "  GPS lock: $GPSDO_FIX (${GPSDO_SATS:-?} satellites)" ;;
    esac
}

gpsdo_grid() {
    [ "$HAVE_GPSDO" = 1 ] || return 1
    # Find the GPSDO's OWN serial node.  Never walk /dev/ttyACM* blindly:
    # on a DASI2 box ttyACM0 is usually the TS-1 TimeSync CLI, and opening
    # a CDC port asserts DTR -- that is how the TS-1's console was wedged
    # during testing on 2026-08-08.  gpsdo-monitor solves this the same way
    # (nmea.py: "Return /dev/ttyACM* nodes whose owning USB device matches").
    local port=""
    for cand in /dev/serial/by-id/*Leo_Bodnar*; do
        [ -e "$cand" ] || continue
        port=$(readlink -f "$cand"); break
    done
    # LBE-Mini (0x2211) is HID-only -- UBX on interrupt-IN, no serial node
    # at all (verified on a v3.22 install: bInterfaceClass 3, 1 endpoint).
    # Nothing to parse here; the VM sets the grid later from the real
    # gpsdo-monitor, which speaks UBX properly.
    [ -n "$port" ] || return 1
    # -hupcl and clocal suppress the DTR toggle on open.
    stty -F "$port" 9600 raw -echo clocal -hupcl 2>/dev/null || true
    local sentence
    sentence=$(timeout 6 grep -m1 -E '^\$G[PNLA]RMC,' < "$port" 2>/dev/null || true)
    [ -n "$sentence" ] || return 1
    # Field layout per gpsdo-monitor/nmea.py:
    #   $xxRMC,utc,status,lat,N/S,lon,E/W,sog,cog,date,magvar,...
    echo "$sentence" | awk -F, '
        $3 != "A" { exit 1 }
        {
          lat = int($4/100) + ($4 - int($4/100)*100)/60; if ($5 == "S") lat = -lat
          lon = int($6/100) + ($6 - int($6/100)*100)/60; if ($7 == "W") lon = -lon
          if (lat == 0 && lon == 0) exit 1
          L = lon + 180; A = lat + 90
          f1 = int(L/20); f2 = int(A/10)
          s1 = int((L - f1*20)/2); s2 = int(A - f2*10)
          u1 = int((L - f1*20 - s1*2) * 12); u2 = int((A - f2*10 - s2) * 24)
          printf "%c%c%d%d%c%c\n", 65+f1, 65+f2, s1, s2, 97+u1, 97+u2
        }'
}

ask_reporter() {
    REPORTER=""
    # Name the destination, not just the field.  This value is what appears on
    # wsprnet.org as the reporter, and it has to be one wsprnet will accept --
    # which is the constraint the operator is actually working under, and not
    # something they can infer from "Reporter ID" (rob, 2026-09-29).
    echo ""
    echo "  Your wsprnet.org reporter ID. This is the name your spots are"
    echo "  filed under on wsprnet.org, so it must be one wsprnet accepts."
    while [ -z "$REPORTER" ]; do
        rd -r -p "wsprnet.org reporter ID — callsign, optionally /suffix (e.g. AC0G/B4 — required): " REPORTER
        REPORTER=$(echo "$REPORTER" | tr '[:lower:]' '[:upper:]' | tr -d ' ')
        if [ -z "$REPORTER" ]; then echo "  ✗ required — please enter your callsign"; continue; fi
        echo "$REPORTER" | grep -qE '^[A-Z0-9]{3,}(/[A-Z0-9]+)?$' || { echo "  ✗ that doesn't look like a callsign"; REPORTER=""; continue; }
        # A WARNING, never a rejection.  Neither sigmond nor either uploader
        # enforces a length, and wsprnet's exact cap is not documented here --
        # so refusing a long id on a guessed limit could lock out a legitimate
        # compound callsign.  Flag it and let the operator decide.
        if [ "${#REPORTER}" -gt 10 ]; then
            echo "  ⚠ $REPORTER is ${#REPORTER} characters. wsprnet.org limits the reporter"
            echo "    field and may refuse it — check it is accepted there before"
            echo "    relying on uploads. (Nothing here enforces a length.)"
        fi
    done
    CALLSIGN="${REPORTER%%/*}"
}

ask_grid() {
    # Offer the GPSDO's own position as the default so the operator confirms
    # rather than types.  Falls back to a bare prompt when there is no GPSDO,
    # no fix yet, or the device is not on a port we can read.
    local GRID_DEFAULT=""
    if [ "$HAVE_GPSDO" = 1 ]; then
        # ⛔ "not readable here" conflated two very different things, and on the
        # commonest DASI GPSDO it was actively misleading.  The LBE-Mini
        # (1dd2:2211) is HID-only -- UBX on interrupt-IN, NO serial node at all
        # -- so this wizard can never read a grid from it, with a perfect sky
        # view or none.  Printing "not readable" invites the operator to go
        # hunting for a fault that does not exist (rob, 2026-09-29, whose
        # LBE-Mini was attached, powered and flashing red for want of antenna
        # signal).  Say which of the two situations this actually is.
        if [ "${GPSDO_ID:-}" = "1dd2:2211" ]; then
            echo "GPSDO present (LBE-Mini). It reports over USB HID (UBX), not"
            echo "  NMEA, so this wizard cannot read it directly — that is"
            echo "  normal and is not a fault. Asking gpsdo-monitor in the"
            printf "  decoder VM, which speaks UBX... "
            if gpsdo_lock_report; then
                echo "answered."
                gpsdo_say_lock
                GRID_DEFAULT="$GPSDO_GRID_UBX"
                [ -n "$GRID_DEFAULT" ] && echo "  position from the GPSDO: $GRID_DEFAULT"
            else
                echo "no answer."
                # UNKNOWN is not the same as NO LOCK, and must never be printed
                # as one.  Two quite different things produce it, and the
                # operator can act on both.
                echo "  ⚠ LOCK STATE UNKNOWN — this is not the same as 'no lock'."
                echo "    Either gpsdo-monitor is not running in the VM yet (it is"
                echo "    installed during bring-up), or the guest agent channel"
                echo "    has gone stale — seen on AI6VN twice on 2026-09-29, with"
                echo "    the agent 'active' inside the guest while the host said"
                echo "    'QEMU guest agent is not running'. Clear that with, in"
                echo "    the VM:  sudo systemctl restart qemu-guest-agent"
                echo "    Then:  smd gpsdo status"
            fi
        else
            printf "Reading position from the GPSDO (%s)... " "$GPSDO_MODEL"
            GRID_DEFAULT=$(gpsdo_grid || true)
            if [ -n "$GRID_DEFAULT" ]; then
                echo "$GRID_DEFAULT"
            else
                echo "no position yet"
                # NMEA gave us nothing.  Say WHY, using the monitor's own view
                # rather than guessing between "no fix" and "cannot read".
                if gpsdo_lock_report; then
                    gpsdo_say_lock
                    GRID_DEFAULT="$GPSDO_GRID_UBX"
                    [ -n "$GRID_DEFAULT" ] && echo "  position from the GPSDO: $GRID_DEFAULT"
                else
                    echo "  The GPSDO is attached but is not holding a GPS fix. It"
                    echo "  needs an antenna with a view of the sky; a flashing red"
                    echo "  LED on a Leo Bodnar unit means exactly that — powered,"
                    echo "  no satellite lock."
                fi
            fi
        fi
        if [ -z "$GRID_DEFAULT" ]; then
            # Not a problem either way: sigmond-location-check runs before
            # bring-up and treats a live GPSDO position as DEFINITIVE,
            # rewriting site-profile grid/lat/lon and propagating to
            # hf-timestd, the metrology envs and mag-recorder via
            # sigmond-site-timing.  Whatever is entered now is provisional.
            echo "  Your GPSDO will set the grid automatically once it locks —"
            echo "  the value below is only used until then."
            echo "  To see what it is reporting:  smd gpsdo status"
        fi
    fi
    # A live GPSDO fix IS the answer — don't ask (mjh 2026-08-11).  The
    # review screen can still override (it re-runs us with "ask"), and
    # sigmond-location-check re-asserts the GPSDO position as definitive
    # after bring-up anyway.
    if [ -n "$GRID_DEFAULT" ] && [ "${1:-}" != "ask" ]; then
        GRID="$GRID_DEFAULT"
        echo "Grid square: $GRID  (from the GPSDO fix; re-edit at the review screen to override)"
        return
    fi
    GRID=""
    while [ -z "$GRID" ]; do
        if [ -n "$GRID_DEFAULT" ]; then
            rd -r -p "Grid square (Maidenhead) [$GRID_DEFAULT]: " GRID
            GRID="${GRID:-$GRID_DEFAULT}"
        else
            rd -r -p "Grid square — 4/6-char Maidenhead (e.g. EM38ww — required): " GRID
        fi
        GRID=$(echo "$GRID" | tr -d ' ')
        if [ -z "$GRID" ] && [ -z "$GRID_DEFAULT" ]; then
            echo "  ✗ required — please enter your grid square"; continue
        fi
        echo "$GRID" | grep -qE '^[A-Ra-r]{2}[0-9]{2}([A-Xa-x]{2})?$' || { echo "  ✗ 4 or 6 character Maidenhead locator, please"; GRID=""; }
    done
}

ask_antenna() {
    rd -r -p "Antenna description (optional, Enter to skip): " ANTENNA
    # The profile pastes this inside a TOML "…" string: a quote, a backslash or
    # a raw control byte (an arrow key) there aborts first boot.
    ANTENNA=$(printf '%s' "$ANTENNA" | tr -d '"\\[:cntrl:]')
}

# ── uplink gate ─────────────────────────────────────────────────────────────
# ⛔ AN INSTALL WITH NO INTERNET CANNOT SUCCEED, AND IT FAILS LATE.
# Bring-up clones from github.com (install.sh and bin/smd name it ten times)
# and fetches uv from astral.sh.  With no egress the wizard still "finishes":
# every question is answered, the VM is created, and the failure surfaces
# minutes later inside bring-up as a clone error nobody is standing by to see.
# rob, 2026-10-11: a working uplink is "a condition for proceeding through the
# wizard ... if that isn't possible the installation should abort".
#
# ⛔ THREE STAGES, EACH NAMING ITS OWN FAILURE.  "No internet" is useless to an
# operator at a console.  A link with no address, a lease whose resolver never
# answers, and a captive portal are three different repairs, and the whole
# point of stopping here is to tell them which one they have.
#
# ⛔ NEVER FORCE AN ADDRESS FAMILY.  curl resolves through getaddrinfo and uses
# whichever family works, so an IPv6-only NAT64 site passes on its own terms.
# Forcing -4 would reject a perfectly good station: this fleet runs IPv6-only
# stations behind NAT64/CLAT, and they are not a hypothetical.
UPLINK_HOST="${SIGMOND_NET_PROBE_HOST:-github.com}"

# Echoes "ok", or "<stage>: <reason>", and returns non-zero on failure.
# No prompting and no side effects, so the retry loop and the tests can both
# call it as often as they like.
uplink_probe() {
    local out
    if [ -z "$(ip -o addr show scope global 2>/dev/null | grep -v ' lo ' | head -1)" ]; then
        echo "link: no interface has a global address"; return 1
    fi
    # ⚡ AN ADDRESS IS NOT A ROUTE.  vmbr1 is the host-only /30 to the decoder
    # VM and always carries a global address, so an address check alone passes
    # on a station with no uplink at all.  The default route is what separates
    # "has an address" from "can leave the building".
    if [ -z "$(ip -4 route show default 2>/dev/null)$(ip -6 route show default 2>/dev/null)" ]; then
        echo "link: an address, but no default route"; return 1
    fi
    if ! timeout 5 getent ahosts "$UPLINK_HOST" >/dev/null 2>&1; then
        echo "DNS: cannot resolve $UPLINK_HOST (the link is up; the resolver is not answering)"; return 1
    fi
    if [ -n "${SIGMOND_NET_PROBE_CMD:-}" ]; then
        out=$(eval "$SIGMOND_NET_PROBE_CMD" 2>&1) || { echo "egress: ${out:-probe command failed}"; return 1; }
        echo ok; return 0
    fi
    # ⚡ EXIT CODE, NOT STATUS CODE.  Any HTTP answer proves the path is open;
    # github may reply 200 or 301 and both mean egress works.  curl's stderr
    # rides along in the message so the operator sees TLS vs timeout vs refused
    # rather than a bare "failed".
    out=$(curl -sS --max-time 8 -o /dev/null --head "https://$UPLINK_HOST" 2>&1) \
        || { echo "egress: cannot reach https://$UPLINK_HOST — ${out:-timed out}"; return 1; }
    echo ok
}

require_uplink() {
    local st
    while :; do
        st=$(uplink_probe)
        if [ "$st" = ok ]; then
            say "uplink OK — $UPLINK_HOST is reachable"
            return 0
        fi

        echo ""
        echo "  ── No working Internet connection ───────────────────"
        echo "  $st"
        echo ""
        echo "  The install must reach $UPLINK_HOST (and astral.sh) to fetch the"
        echo "  station software.  Without that, bring-up fails minutes from now,"
        echo "  after the VM has already been created."
        echo ""

        # ⛔ NEVER PROMPT ON PIPED STDIN.  bash suppresses `read -p` prompts on a
        # non-tty, so a prompt here would silently eat the answer meant for the
        # NEXT question and desync every prompt after it — the 2026-08-11
        # nested-test hunt.  An unattended run gets one probe and an abort.
        if [ ! -t 0 ]; then
            say "no uplink, and no operator to fix it (unattended) — aborting; nothing was applied"
            exit 1
        fi

        echo "  Plug in an Ethernet cable, or set up Wi-Fi, then re-check."
        rd -r -p "  [r] re-check · [w] set up Wi-Fi · [q] abort install: " _uq
        case "${_uq:-r}" in
            [Ww]*) ask_wifi ;;
            [Qq]*) say "aborted: no Internet connection — nothing was applied. Rerun: sigmond-setup"; exit 1 ;;
            *)     ;;   # r (or anything else): probe again
        esac
    done
}

# ── Wi-Fi ───────────────────────────────────────────────────────────────────
# ⚡ On a DASI station Wi-Fi is PREFERRED over wired, and the reason is
# physical, not convenience (rob, 2026-09-28): "the wired interface introduces
# potential for radio interference into the SDR."  1000BASE-T runs at a 125 MHz
# symbol rate; its harmonics and the PHY's switching noise land in HF, and
# unshielded twisted pair entering the shack carries that in as common-mode
# current on what is electrically an antenna.  2.4/5 GHz is nowhere near the
# receiver's passband.  Removing the Ethernet cable removes a conducted noise
# path into the instrument, and the station is a measuring instrument first.
#
# Every DASI box has a radio, so this is offered at EVERY install, not only
# when there is no cable.  It stays declinable: a station already on Ethernet
# is working, and we do not disturb a working install without being asked.
# Both injectable so the step can be exercised without a radio and without
# writing to /usr/local/sbin (same convention as sigmond-wifi's SIGMOND_WIFI_DEV).
WIFI_TOOL="${SIGMOND_WIFI_TOOL:-/usr/local/sbin/sigmond-wifi}"
WIFI_SYSFS="${SIGMOND_WIFI_SYSFS:-/sys/class/net}"

wifi_radio_dev(){
    [ -n "${SIGMOND_WIFI_DEV:-}" ] && { printf '%s\n' "$SIGMOND_WIFI_DEV"; return; }
    local d
    for d in "$WIFI_SYSFS"/*/wireless; do
        [ -e "$d" ] || continue
        basename "$(dirname "$d")"; return
    done
}

ask_wifi() {
    WIFI_SUMMARY=""
    local dev; dev=$(wifi_radio_dev)
    if [ -z "$dev" ]; then
        WIFI_SUMMARY="no Wi-Fi radio on this host"
        return
    fi
    if [ ! -x "$WIFI_TOOL" ]; then
        # The tool rides on the stick and firstboot installs it; if it is
        # absent the offline-debs payload did not apply, which is worth saying
        # out loud rather than silently offering nothing.
        WIFI_SUMMARY="radio $dev found, but sigmond-wifi is missing (offline-debs did not apply)"
        return
    fi
    # ⛔ NEVER prompt when stdin is not a tty.  A piped answer file has a fixed
    # number of lines in a fixed order; a new prompt would eat the answer meant
    # for the NEXT question and desync every prompt after it — invisibly, since
    # bash suppresses `read -p` prompts on non-tty stdin.  That is exactly the
    # 2026-08-11 nested-test failure recorded at the "Press Enter to begin"
    # guard below.  An unattended install keeps whatever link brought it up.
    if [ ! -t 0 ]; then
        WIFI_SUMMARY="radio $dev found — skipped (unattended run)"
        return
    fi

    echo ""
    echo "  ── Wi-Fi ───────────────────────────────────────────"
    echo "  Wi-Fi radio found: $dev"
    echo "  On a DASI station Wi-Fi is usually the BETTER link. An Ethernet"
    echo "  cable entering the shack is a conducted noise path into the HF"
    echo "  receiver; 2.4/5 GHz is nowhere near its passband. Going cable-free"
    echo "  lowers your noise floor."
    if [ -n "$(ip -o addr show scope global 2>/dev/null | grep -v " $dev " | head -1)" ]; then
        echo "  (This host already has a working link, so this is optional —"
        echo "   joining Wi-Fi will not disturb it.)"
    else
        echo "  ⚠ No other network is up. Wi-Fi may be the only way to reach"
        echo "    this station after install."
    fi
    rd -r -p "Set up Wi-Fi now? [y/N] " _wq
    case "${_wq:-N}" in
        [Yy]*) ;;
        *) WIFI_SUMMARY="declined (radio $dev available; run 'sigmond-wifi scan' any time)"; return ;;
    esac

    local _scan _n _pick _ssid _pass _line _out _rc
    while :; do
        echo ""
        # ⛔ Match a NETWORK ROW, not merely a line containing "dBm".  The tool's
        # own closing advice says "below -70 dBm the link is marginal", so a
        # bare `grep ' dBm '` numbered that sentence as a selectable network:
        #
        #   13)  Portola 5A   5GHz   -79 dBm  WPA3   ⚠ weak ...
        #   14)  ⚠ below -70 dBm the link is marginal. On a DASI station that is
        #
        # Picking 14 would have handed a fragment of prose to `join` as an SSID
        # (rob, 2026-09-29).  A real row always carries a band token, so require
        # one: band, then the signal, then the literal dBm.
        _scan=$("$WIFI_TOOL" scan 2>/dev/null \
                | grep -E '(2\.4GHz|5GHz)[[:space:]]+-?[0-9]+ dBm')
        if [ -z "$_scan" ]; then
            echo "  No networks found. The AP may be hidden, or out of range."
        else
            _n=0
            echo "$_scan" | while IFS= read -r _line; do
                _n=$((_n+1)); printf '  %2d)%s\n' "$_n" "$_line"
            done
            # ⚠ the loop above runs in a subshell, so recount here for the prompt
            _n=$(printf '%s\n' "$_scan" | wc -l)
            echo ""
            echo "  ⚠ below -70 dBm the link is marginal. On a DASI station that"
            echo "    is still often the right trade — weigh it against the noise"
            echo "    a cable puts into the receiver."
        fi
        echo ""
        rd -r -p "Pick a number, type an SSID (hidden networks), r = rescan, Enter = skip: " _pick
        case "$_pick" in
            "")  WIFI_SUMMARY="skipped at the network list (radio $dev available)"; return ;;
            [Rr]) continue ;;
            ''|*[!0-9]*) _ssid="$_pick" ;;                       # not a number → an SSID
            *)   _ssid=$(printf '%s\n' "$_scan" | sed -n "${_pick}p" \
                         | sed 's/^ *//; s/  .*//')
                 if [ -z "$_ssid" ]; then echo "  ✗ no entry $_pick"; continue; fi ;;
        esac
        [ -n "$_ssid" ] || { echo "  ✗ please enter an SSID"; continue; }

        # An inner loop, so a mistyped passphrase costs a retype and not a
        # rescan and a re-pick.  rob, 2026-09-29, after a rejected passphrase:
        # "it should go back and prompt and repeat, or quit ... the user should
        # have repeated opportunities to select the SSID and enter a password."
        while :; do
            # -s: the passphrase must not be echoed to a console that, during
            # an install, is routinely being watched by whoever is in the room.
            echo ""
            rd -rs -p "Passphrase for \"$_ssid\" (Enter if open): " _pass
            echo ""
            # Say how long this takes.  Association, DHCP/SLAAC and -- on an
            # IPv6-only AP -- standing up the resolver and CLAT add up to tens
            # of seconds of silence, which rob read as a possible hang
            # (2026-09-29): "I wasn't sure whether it was hanging or not."
            echo "  joining $_ssid ... (this can take 10-30 s: association,"
            echo "  then an address, then a resolver if it is an IPv6-only AP)"
            # ⛔ CAPTURE THE STATUS — never test the pipeline.  `cmd | sed`
            # reports SED's exit status, which is 0 no matter what the join
            # did, so EVERY failed join was recorded as a success and the
            # wizard walked straight on.  rob hit this on the first real use:
            # "it says failed ... and then it went on. That's not what I
            # wanted."  The join itself was reporting the failure correctly;
            # the wizard was throwing the answer away.
            _out=$(printf '%s' "$_pass" | "$WIFI_TOOL" join "$_ssid" 2>&1)
            _rc=$?
            _pass=""
            printf '%s\n' "$_out" | sed 's/^/    /'
            if [ "$_rc" = 0 ]; then
                WIFI_SUMMARY="joined \"$_ssid\" on $dev"
                return
            fi
            echo "  ✗ could not join \"$_ssid\"."
            case "$_out" in
                *WRONG_KEY*|*4-Way*|*4-way*|*handshake*|*auth*)
                    echo "    The access point rejected that passphrase." ;;
            esac
            while :; do
                rd -r -p "  [p] re-enter passphrase · [s] select another network · [q] skip Wi-Fi: " _wq
                case "${_wq:-p}" in
                    [Pp]*) break ;;        # same SSID, ask for the passphrase again
                    [Ss]*) continue 3 ;;   # back out to the scan + picker
                    [Qq]*) WIFI_SUMMARY="not joined — \"$_ssid\" refused the passphrase (radio $dev available)"
                           return ;;
                    *) echo "  ✗ please answer p, s, or q" ;;
                esac
            done
        done
    done
}

ask_rac() {
    echo ""
    # Station class decides the gateway ladder (rob 2026-08-09): DASI = the
    # HamSCI-managed install shape, prefers vpn.hamsci.org and may walk down
    # to an unsecured tunnel; everything else is an ordinary WsprDaemon-group
    # station, gw2 secure ONLY.  Default follows the image profile so later
    # image builds can flip it (SIGMOND_RAC_PROFILE=standard -> default No).
    if [ "$RAC_PROFILE" = "standard" ]; then _dp="y/N"; else _dp="Y/n"; fi
    rd -r -p "Is this a DASI (HamSCI) station? [$_dp] " _dq
    _ddef="Y"; [ "$RAC_PROFILE" = "standard" ] && _ddef="N"
    case "${_dq:-$_ddef}" in
        [Nn]*) RAC_PROFILE="standard" ;;
        *)     RAC_PROFILE="dasi" ;;
    esac
    if [ "$RAC_PROFILE" = "dasi" ]; then
        while :; do
            rd -r -p "DASI unit number (1-99, e.g. 3 for DASI-003${DASI_NUM:+; current $DASI_NUM}; Enter to skip): " _dn
            _dn="${_dn:-$DASI_NUM}"
            [ -z "$_dn" ] && break
            echo "$_dn" | grep -qE '^[1-9][0-9]?$' && { DASI_NUM="$_dn"; break; }
            echo "  ✗ DASI numbers are 1-99 (DASI-001 … DASI-099)"
        done
        if [ -n "$DASI_NUM" ]; then
            _rn=$((220 + DASI_NUM))
            echo "  DASI-$(printf '%03d' "$DASI_NUM") maps deterministically to RAC #$_rn:"
            echo "    VM ssh :$((RAC_BASE_VMSSH+_rn)) · ka9q-web :$((RAC_BASE_VMWEB+_rn)) · host ssh :$((RAC_BASE_HSSH+_rn)) · host UI :$((RAC_BASE_HUI+_rn))"
            echo "  Secure HamSCI access (vpn.hamsci.org:35736, TLS + trust-on-first-use)"
            echo "  needs no token or account — this station's key is filed on first connect."
        else
            echo "  No DASI number yet. If the station designator names a DASI unit"
            echo "  (DASI-007, DASI2-07) the number is taken from it at the naming step."
            echo "  Otherwise this station registers with gw2.wsprdaemon.org, which"
            echo "  auto-assigns a RAC in its 500 block — NOT a HamSCI number."
        fi
    fi
    build_rac_tiers
    echo ""
    echo "Remote access comes up by itself; this wizard asks nothing about it."
    echo "It gives your fleet admin the only effective way to support this"
    echo "station: a tunnel the station dials out, with nothing opened on your"
    echo "router."
    echo "  Registering sends two things to the gateway: this station's name"
    echo "  and this host's public key."
    echo "  You hold the switch. In the decoder VM, at any time:"
    echo "      smd remote off      turn it off (it stays off across reboots)"
    echo "      smd remote on       turn it on again"
    echo "      smd remote status   show the state"
    echo ""
    if [ "$RAC_PROFILE" = "standard" ]; then
        echo "  Gateway: $RAC_SERVER, secure only."
    else
        echo "  Gateways are tried in order, most secure first:"
        echo "$RAC_TIERS" | while IFS='|' read -r _l _c _r _t _p; do
            [ -n "$_c" ] && echo "    · $_l  ($_c:$_p)"
        done
        echo "  The tier that answers is reported at the end of the install."
    fi
    # When equipment is missing, the tunnel is the most valuable thing the
    # install can still achieve: it lets someone remote help finish the build.
    if [ "$HAVE_RX888" = 0 ] || [ "$HAVE_GPSDO" = 0 ] || [ "$HAVE_TS1" = 0 ] || [ "$HAVE_MAG" = 0 ]; then
        echo ""
        echo "  ** Some equipment was not detected (see above).  Remote access lets"
        echo "     the fleet admin connect and help finish the install once the"
        echo "     missing parts are fitted. **"
    fi
}

ask_psws() {
    echo ""
    echo "HamSCI PSWS (space-weather / GRAPE uploads) — optional. If this station"
    echo "has a PSWS account (https://pswsnetwork.eng.ua.edu/), enter its IDs now."
    echo "The upload KEY is registered later from any SSH session — the VM's login"
    echo "banner walks you through it (no copy-paste needed on this console)."
    PSWS_ID=""
    while :; do
        rd -r -p "PSWS station ID (e.g. S000123, Enter to skip): " PSWS_ID
        PSWS_ID=$(echo "$PSWS_ID" | tr '[:lower:]' '[:upper:]' | tr -d ' ')
        [ -z "$PSWS_ID" ] && break
        echo "$PSWS_ID" | grep -qE '^S[0-9]{6}$' && break
        echo "  ✗ PSWS station IDs look like S000123 (S + 6 digits)"
    done
    # ONE station id per site.  The wizard used to ask for a second one just
    # for the magnetometer; no station ever answered it differently, and two
    # station identities per site made every "which station is this?" question
    # ambiguous.  Instruments differ by instrument id, not by station.
    PSWS_GRAPE=""; PSWS_MAG=""
    if [ -n "$PSWS_ID" ]; then
        rd -r -p "  GRAPE instrument ID from the portal (e.g. 172, Enter if none): " PSWS_GRAPE
        PSWS_GRAPE=$(echo "$PSWS_GRAPE" | tr -d ' ')
        rd -r -p "  magnetometer instrument number from the portal (e.g. 84, Enter if none): " PSWS_MAG
        PSWS_MAG=$(echo "$PSWS_MAG" | tr -d ' ')
    fi
}

# ── fleet heartbeat ────────────────────────────────────────────────────────
# The heartbeat existed and shipped OFF: nothing in this wizard ever asked
# about it, so every greenfield install ended silent, and the one station
# reporting (AC0G-B4) had been hand-edited to a LAN address that would stop
# working the moment the station moved.  Ask, default ON, and default to the
# PUBLIC name so the answer survives the station leaving the bench.
#
# Re-pointing later takes no reflash: edit [heartbeat].host in
# /etc/sigmond/site-profile.toml on the decoder VM and run `smd config render`.
# That matters -- wd30 is a temporary home for the fleetboard.
HB_DEFAULT_HOST="wd30.wsprdaemon.org"
# ⛔ The PORT is not optional and its code default is WRONG for this host.
# site_profile.py defaults [heartbeat].port to 22, but wd30 answers only on
# 38222 (the operator's router-forward); measured 2026-09-23, :22 refuses and
# :38222 accepts.  Writing the host without a port would give every new
# station a heartbeat that silently never delivers -- which is how the
# feature came to be neglected in the first place.
HB_DEFAULT_PORT="38222"
ask_heartbeat() {
    echo
    echo "  A status heartbeat lets the fleet dashboard show this station is"
    echo "  alive and healthy. It sends a small report every 5 minutes, outbound"
    echo "  only — nothing listens on this machine for it."
    rd -r -p "Send status heartbeats? [Y/n] " _hb
    case "${_hb:-Y}" in
        [Nn]*) HB_ENABLED=0; HB_HOST=""; return ;;
    esac
    HB_ENABLED=1
    rd -r -p "  heartbeat collector host [$HB_DEFAULT_HOST]: " HB_HOST
    HB_HOST="${HB_HOST:-$HB_DEFAULT_HOST}"
    rd -r -p "  heartbeat collector port [$HB_DEFAULT_PORT]: " HB_PORT
    HB_PORT=$(echo "${HB_PORT:-$HB_DEFAULT_PORT}" | tr -d ' ')
    case "$HB_PORT" in ''|*[!0-9]*) HB_PORT="$HB_DEFAULT_PORT" ;; esac
}

ask_names() {
    # Station naming convention (rob 2026-07-29): the decoder VM carries the
    # station designator, the Proxmox host carries designator-PM. Default
    # derives from the reporter ID (AC0G/B4 → AC0G-B4), but multi-station
    # sites or generic deployments can override (DASI2-01 / DASI2-01-PM).
    local DES_DEFAULT
    DES_DEFAULT=$(echo "$REPORTER" | tr '/' '-')
    echo ""
    echo "Station names: the decoder VM takes the station designator and the"
    echo "Proxmox host takes designator-PM (e.g. AC0G-B4 + AC0G-B4-PM, or"
    echo "DASI2-01 + DASI2-01-PM for numbered deployments)."
    rd -r -p "Station designator [$DES_DEFAULT]: " DES
    DES=$(echo "${DES:-$DES_DEFAULT}" | tr -cd 'A-Za-z0-9-')
    [ -z "$DES" ] && DES="$DES_DEFAULT"
    VMNAME="$DES"
    PMNAME="$DES-PM"
    derive_dasi_num
}

# ── take the console cleanly ────────────────────────────────────────────
# sigmond-wizard.service has Conflicts=getty@tty1, but systemd stops getty
# ASYNCHRONOUSLY: getty routinely gets "login:" onto the screen just before
# it dies.  v3.25 printed blank lines first, but blank lines can't win a
# race — "login:" can land AFTER they've flushed, putting the banner back
# on the login line (mjh, v3.25 test 2026-08-09).  So wait for getty@tty1
# to actually be gone (bounded) before emitting the first byte; the leading
# blank line then lands below whatever getty last wrote.
for _i in $(seq 1 50); do
    _gst=$(systemctl show -p ActiveState --value getty@tty1.service 2>/dev/null)
    case "$_gst" in active|activating|deactivating) sleep 0.2 ;; *) break ;; esac
done
sleep 1
printf '\n\n'
echo "════════════════════════════════════════════════════════════════"
echo "  Sigmond appliance — first-boot station setup"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "  This asks a short series of questions about the station, then"
echo "  configures the Proxmox host and the decoder VM.  Nothing is"
echo "  applied until you confirm at the review screen."
echo ""
echo "  Prompt conventions:  [bracketed] values are defaults — press Enter"
echo "  to accept them.  'e.g.' values are examples only — those answers"
echo "  are required (or say 'Enter to skip' when optional)."
echo ""
# Console-only pacing gate: on a tty it lets the operator take in the
# banner before questions start.  On piped stdin it must NOT run — it
# silently ate the first answer, desyncing every prompt after it (the
# 2026-08-11 nested-test hunt; the theft is invisible because bash
# suppresses read -p prompts on non-tty stdin).
if [ -t 0 ]; then rd -r -p "  Press Enter to begin: " _; fi
echo ""

preflight_devices
ask_reporter
ask_grid
ask_antenna
# Before ask_rac on purpose: remote access is only meaningful over a link that
# works, and on a cable-free DASI box this is the step that creates one.
ask_wifi
# After ask_wifi on purpose: on a cable-free DASI box that step is what creates
# the link, so judging connectivity before it would abort installs that were
# about to be fine.
require_uplink
ask_rac
ask_psws
ask_heartbeat
ask_names

# ── review: everything on one screen, any entry editable ───────────────────
while :; do
    echo ""
    echo "  ── Review — nothing is applied yet ──────────────────"
    echo "  1) Reporter:  $REPORTER"
    echo "  2) Grid:      $GRID"
    echo "  3) Antenna:   ${ANTENNA:-(none)}"
    _rgw="$(echo "$RAC_TIERS" | head -1 | cut -d'|' -f2)"
    if [ -n "$DASI_NUM" ] && [ "$RAC_PROFILE" != "standard" ]; then
        _rwhich="DASI-$(printf '%03d' "$DASI_NUM") → RAC $((220 + DASI_NUM))"
    else
        _rwhich="number auto-assigned by the gateway"
    fi
    echo "  4) Remote:    via ${_rgw} ($_rwhich)"
    echo "                (comes up by itself; switch it in the decoder VM: smd remote off / on)"
    if [ -n "$PSWS_ID" ]; then
        echo "  5) PSWS:      station $PSWS_ID${PSWS_GRAPE:+  grape=$PSWS_GRAPE}${PSWS_MAG:+  mag=$PSWS_MAG}  (key registered after install)"
    else
        echo "  5) PSWS:      (skipped)"
    fi
    if [ "${HB_ENABLED:-0}" = 1 ]; then
        echo "  6) Heartbeat: enabled → $HB_HOST:$HB_PORT  (change later: site-profile.toml + smd config render)"
    else
        echo "  6) Heartbeat: disabled"
    fi
    echo "  7) Names:     VM $VMNAME · Proxmox host $PMNAME"
    echo "  8) Wi-Fi:     ${WIFI_SUMMARY:-(not offered)}"
    # Every other answer is inert until Apply; a Wi-Fi join is a live link the
    # moment it succeeds.  Say so rather than let the header above imply
    # otherwise — an operator who aborts here still has the association.
    case "$WIFI_SUMMARY" in
        joined*) echo "                (already live — a link, not a setting applied later)";;
    esac
    echo "  ─────────────────────────────────────────────────────"
    rd -r -p "Apply? [Y = apply / 1-8 = re-edit that entry / n = abort] " OK
    case "${OK:-Y}" in
        1) ask_reporter;;
        2) ask_grid ask;;
        3) ask_antenna;;
        4) ask_rac;;
        5) ask_psws;;
        6) ask_heartbeat;;
        7) ask_names;;
        8) ask_wifi;;
        [Nn]*) say "aborted by operator — nothing was applied. Rerun any time: sigmond-setup"; exit 1;;
        [Yy]*|"") break;;
        *) echo "  ✗ Y, n, or an entry number 1-8";;
    esac
done

# ⛔ RE-CHECK THE UPLINK AT APPLY.  Review entry 8 re-runs ask_wifi, and joining
# a different AP can take down the very link the earlier gate passed on — so
# the last thing checked before anything is applied must be the link itself.
# Cheap (one DNS lookup and one HEAD) and it reuses the same retry loop.
require_uplink

# ── station names: VM = designator, Proxmox host = designator-PM ────────────
# Applied FIRST: everything after this uses qm, and a Proxmox single-node
# rename moves the node's config dir — do it before touching the VM, verify
# qm still works, and revert if it doesn't. pmxcfs keys node data by
# hostname: restart pve-cluster, then adopt the VM configs into the new
# node dir.
OLDHOST=$(hostname)
if [ "$PMNAME" != "$OLDHOST" ]; then
    say "renaming Proxmox host: $OLDHOST → $PMNAME"
    hostnamectl set-hostname "$PMNAME" 2>/dev/null || hostname "$PMNAME"
    sed -i "s/\b$OLDHOST\b/$PMNAME/g" /etc/hosts 2>/dev/null
    [ -f /etc/postfix/main.cf ] && sed -i "s/\b$OLDHOST\b/$PMNAME/g" /etc/postfix/main.cf 2>/dev/null
    systemctl restart pve-cluster 2>/dev/null
    # wait for pmxcfs to mount, then create the new node dir OURSELVES:
    # pmxcfs materializes nodes/<name> lazily — sometimes minutes after the
    # restart (observed 2026-07-29: a 54s wait wasn't enough, which made the
    # guard revert two good renames). mkdir inside the fuse fs is legal and
    # deterministic.
    for i in $(seq 1 15); do [ -d /etc/pve/nodes ] && break; sleep 2; done
    mkdir -p "/etc/pve/nodes/$PMNAME/qemu-server" 2>/dev/null
    [ -d "/etc/pve/nodes/$OLDHOST/qemu-server" ] && \
        mv "/etc/pve/nodes/$OLDHOST/qemu-server/"*.conf "/etc/pve/nodes/$PMNAME/qemu-server/" 2>/dev/null
    systemctl restart pveproxy pvedaemon 2>/dev/null
    # pmxcfs can take a while to settle after the restart — a single-shot
    # qm check 3s in reverted a PERFECTLY GOOD rename (nested run
    # 2026-07-29); give it up to 45s before declaring the rename broken
    QOK=0
    for i in $(seq 1 15); do
        qm status "$VMID" >/dev/null 2>&1 && { QOK=1; break; }
        sleep 3
    done
    if [ "$QOK" = 1 ]; then
        # confs adopted + qm healthy: retire the old node dir (a leftover
        # empty node lingers in the GUI forever otherwise)
        rm -rf "/etc/pve/nodes/$OLDHOST" 2>/dev/null
    else
        say "WARN: node rename broke qm — reverting to $OLDHOST"
        hostnamectl set-hostname "$OLDHOST" 2>/dev/null || hostname "$OLDHOST"
        sed -i "s/\b$PMNAME\b/$OLDHOST/g" /etc/hosts 2>/dev/null
        systemctl restart pve-cluster 2>/dev/null; sleep 5
        [ -d "/etc/pve/nodes/$PMNAME/qemu-server" ] && \
            mv "/etc/pve/nodes/$PMNAME/qemu-server/"*.conf "/etc/pve/nodes/$OLDHOST/qemu-server/" 2>/dev/null
        rm -rf "/etc/pve/nodes/$PMNAME" 2>/dev/null
        systemctl restart pveproxy pvedaemon 2>/dev/null; sleep 3
        PMNAME="$OLDHOST"
    fi
fi
qm set "$VMID" --name "$VMNAME" >/dev/null 2>&1 && say "decoder VM named $VMNAME"

# ── push identity into the decoder VM ───────────────────────────────────────
say "writing site-profile.toml into VM $VMID"
# PSWS: ids now, key later — `smd config render` in the VM generates the
# station key, arms the login banner that shows the pubkey to register at
# the portal, and `smd psws verify` (run over ssh, where copy-paste works)
# proves the handshake.
PSWS_TOML=""
if [ -n "$PSWS_ID" ]; then
    PSWS_TOML="
[psws]
enabled    = true
station_id = \"$PSWS_ID\"

[psws.instruments]"
    [ -n "$PSWS_GRAPE" ] && PSWS_TOML="$PSWS_TOML
\"hf-timestd\"   = \"$PSWS_GRAPE\""
    [ -n "$PSWS_MAG" ] && PSWS_TOML="$PSWS_TOML
\"mag-recorder\" = \"$PSWS_MAG\""
    PSWS_TOML="$PSWS_TOML
"
fi
HB_TOML=""
if [ "${HB_ENABLED:-0}" = 1 ] && [ -n "${HB_HOST:-}" ]; then
    HB_TOML="
[heartbeat]
enabled  = true
host     = \"$HB_HOST\"
port     = $HB_PORT
"
fi
UPLOADS_TOML=$(uploads_toml)
PROFILE=$(cat <<PEOF
# Written by the Sigmond appliance first-boot wizard $(date -u +%Y-%m-%dT%H:%MZ)
[station]
callsign    = "$CALLSIGN"
grid_square = "$GRID"
description = "$ANTENNA"
$PSWS_TOML
[reporters]
reporter_id = "$REPORTER"
$HB_TOML
$UPLOADS_TOML

[host]
hostname = "$VMNAME"
PEOF
)
B64=$(echo "$PROFILE" | base64 -w0)
gexec 30 "echo $B64 | base64 -d > /etc/sigmond/site-profile.toml.new" \
    || { say "ERROR: could not write site-profile.toml in guest"; exit 1; }
# profile-install keeps a site sink switch the station already set; a plain
# overwrite would reset it on every --reconfigure (§10.3 item 2).  Ask first
# whether this VM's smd knows the verb, so that a refusal of the new file
# (invalid TOML, say, from a double quote in an answer) never falls through
# to installing it whole.
# An older smd has no profile-install.  Installing the profile whole is safe
# only when the guest affirmatively answers that the live profile has no
# [uploads] header (a missing live file passes too: nothing to lose).  gexec
# returns 0 only when the agent answered with exit 0, so a block, a dead agent
# and a transient failure all refuse, and the live profile stays as it is.
if gexec_unit 30 profile-probe "smd config profile-install --help >/dev/null 2>&1"; then
    gexec_unit 30 profile-install "smd config profile-install /etc/sigmond/site-profile.toml.new" \
        || { say "ERROR: smd config profile-install refused the new site-profile.toml — most often a double quote or backslash in an answer; see $LOG"; exit 1; }
elif gexec 30 "! grep -qsE '^[[:space:]]*\\[uploads\\]' /etc/sigmond/site-profile.toml"; then
    say "WARN: this VM's sigmond predates 'smd config profile-install', so the wizard"
    say "      installs the profile whole, as earlier wizards did.  The live profile sets"
    say "      no [uploads] block, so none is lost.  Check 'smd upload status'; change it"
    say "      with 'smd upload on', 'smd upload hold' or 'smd upload discard'."
    gexec 30 "mv -f /etc/sigmond/site-profile.toml.new /etc/sigmond/site-profile.toml" \
        || { say "ERROR: could not install site-profile.toml in guest"; exit 1; }
else
    gexec 30 "rm -f /etc/sigmond/site-profile.toml.new" || true
    say "ERROR: this VM's sigmond predates 'smd config profile-install', and its site-profile.toml"
    say "       sets the site sink switch ([uploads]).  Installing the new profile whole would reset it."
    say "       Ask your fleet admin to update this VM's sigmond, then run the wizard again."
    say "       The live site profile is unchanged."
    exit 1
fi

say "personalizing VM (new machine-id, SSH host keys, hostname)..."
gexec_unit 600 personalize "smd admin personalize --reset-identity --yes" \
    || { say "ERROR: personalize failed — see $LOG"; exit 1; }
# ⛔ Pin the VM's NEW host key on this host, read over the guest-agent channel
# (authoritative; no network scan to trust).  Nothing pinned it before, so
# every PM -> VM ssh (fleet-ssh's nested hop, pm-align, site-restore) failed
# BatchMode with "Host key verification failed" -- AC0G-ND, v3.67,
# 2026-10-05, fixed there by hand.  Both names the PM uses: the /30 address
# and the vm-ssh relay.  Replaces any stale pin from an earlier install.
VMKEY=$(qm guest exec "$VMID" --timeout 15 -- cat /etc/ssh/ssh_host_ed25519_key.pub 2>/dev/null </dev/null \
        | grep -o '"out-data" *: *"[^"]*"' | sed 's/.*: *"//;s/\\n"$//;s/"$//' | awk '{print $1" "$2}')
if [[ "$VMKEY" == ssh-ed25519\ * ]]; then
    install -d -m 700 /root/.ssh; touch /root/.ssh/known_hosts; chmod 600 /root/.ssh/known_hosts
    for h in 10.99.0.2 '[127.0.0.1]:12222'; do
        ssh-keygen -R "$h" -f /root/.ssh/known_hosts >/dev/null 2>&1
        echo "$h $VMKEY" >> /root/.ssh/known_hosts
    done
    say "pinned the decoder VM's host key on this host"
else
    say "WARN: could not read the VM's host key — PM -> VM ssh will refuse until pinned"
fi
say "rendering site config in VM..."
gexec_unit 600 render "smd config render" \
    || say "WARN: smd config render reported issues (continuing; rerun inside VM)"

# sigmond-heartbeat.timer ships installed everywhere but never auto-enabled
# by design — answering "yes" above only wrote [heartbeat] into the site
# profile, which is how v3.52 shipped a station that never reported
# (sigmond#94).  Arm (or, on a rerun that turned it off, disarm) the timer
# to match what was just written.
if [ "${HB_ENABLED:-0}" = 1 ]; then
    gexec 30 "systemctl enable --now sigmond-heartbeat.timer" \
        && say "fleet heartbeat timer enabled" \
        || say "WARN: could not enable the fleet heartbeat timer — run in the VM: systemctl enable --now sigmond-heartbeat.timer"
else
    gexec 30 "systemctl disable --now sigmond-heartbeat.timer 2>/dev/null; true"
fi

# The PM (this Proxmox host, not the VM) ships its own heartbeat emitter —
# firstboot unpacks the whole sigmond tree to /root/sigmond-appliance/sigmond/,
# pm-heartbeat-setup.sh included — but nothing ever ran it, so every PM row
# on the fleet board stayed silent after a fresh install (sigmond-appliance#9).
# The station name is the reporter id plus "-pm"; the inventory maps that to
# its fleet name (heartbeat_station, sigmond#95).
PM_HEARTBEAT_SETUP=/root/sigmond-appliance/sigmond/scripts/proxmox/pm-heartbeat-setup.sh
if [ "${HB_ENABLED:-0}" = 1 ]; then
    if [ -x "$PM_HEARTBEAT_SETUP" ]; then
        if "$PM_HEARTBEAT_SETUP" --station "${REPORTER}-pm" --vmid "$VMID" \
                --dest-host "$HB_HOST" --dest-port "$HB_PORT" >>"$LOG" 2>&1; then
            say "host heartbeat enabled — authorize its public key on the fleetboard server:"
            say "$(cat /etc/pm-heartbeat/id_ed25519.pub 2>/dev/null)"
        else
            say "WARN: host heartbeat setup failed — rerun by hand: $PM_HEARTBEAT_SETUP --station ${REPORTER}-pm --vmid $VMID --dest-host $HB_HOST --dest-port $HB_PORT"
        fi
    else
        say "WARN: host heartbeat setup script missing at $PM_HEARTBEAT_SETUP — rerun by hand once present: pm-heartbeat-setup.sh --station ${REPORTER}-pm --vmid $VMID --dest-host $HB_HOST --dest-port $HB_PORT"
    fi
else
    systemctl disable --now pm-heartbeat.timer 2>/dev/null; true
fi

# mag-recorder identity: render pushes the SITE PSWS station id, but the
# magnetometer often lives under its OWN portal station, and the config's
# callsign/grid stay template placeholders (field gap, AC0G-B4 2026-07-30).
# Fill them here.
if [ -n "${PSWS_MAG:-}" ]; then
    MAGST="$PSWS_ID"
    gexec 30 "C=/etc/mag-recorder/mag-recorder-config.toml; [ -f \$C ] && { sed -i -e \"s|^psws_station_id  = .*|psws_station_id  = \\\"$MAGST\\\"|\" -e \"s|^callsign         = \\\"<YOUR_CALL>\\\"|callsign         = \\\"$CALLSIGN\\\"|\" -e \"s|^grid_square      = \\\"<YOUR_GRID>\\\"|grid_square      = \\\"$GRID\\\"|\" \$C; systemctl try-restart mag-recorder 2>/dev/null; }; true" \
        || say "WARN: could not fill mag-recorder identity"
fi

# ── FFT wisdom seed + location authority ───────────────────────────────────
# TWO structural gaps found on B4 + rob's Kamrui (2026-07-27/28, root-caused
# in smd @9b015f2): (a) NOTHING in the wizard/clone flow ever mints a radiod
# instance — `config render` only renders config for existing instances, and
# minting lives solely in `smd config init radiod`, reached via `smd bringup`;
# (b) nothing reacts to an RX888 that appears (or gets replugged out of an
# FX3 wedge) after setup. Net effect: every appliance deploy ended with
# radiod never started.
#
# The sentinel closed both by polling, and closed (b) by STARTING the stack
# unasked — see the retirement note below. Each gap now has its own answer.
# The wizard closes (a) itself: it calls `smd bringup` where it can see the
# RX888, and the operator standing at the console supplies the consent. An
# operator closes (b) with `smd adopt`, whenever the hardware turns up. So
# the appliance keeps a periodic job here only for the one thing that
# genuinely repeats — re-asserting the GPSDO position.
# Also: `smd apply` refuses to START radiod until /etc/fftw/wisdomf exists,
# and the in-VM planner grinds >1 h on first boot — seed wisdom from the
# build fleet first (fftw silently ignores entries foreign to the CPU).
# appliance version visible inside the VM too (rob 2026-07-30) — same
# path as on the host; placed after personalize so identity reset can't
# touch it
gexec 15 "mkdir -p /etc/sigmond-appliance && echo '$(cat /etc/sigmond-appliance/version 2>/dev/null || echo unknown)' > /etc/sigmond-appliance/version" \
    || say "WARN: could not stamp appliance version into the VM"
# Component pin manifest: the importer installs the host copy, but `smd doctor`
# and the heartbeat run IN THE VM and read the same path there -- without this
# copy every imaged decoder VM reports "manifest: unassessed" and the drift
# check the manifest exists for never runs (AI6VN v3.37, 2026-09-05).
if [ -s /etc/sigmond-appliance/manifest.txt ]; then
    gexec 30 "echo $(base64 -w0 /etc/sigmond-appliance/manifest.txt) | base64 -d > /etc/sigmond-appliance/manifest.txt" \
        || say "WARN: could not copy the component manifest into the VM — drift check unavailable there"
fi

if [ -f /root/sigmond-appliance/wisdomf-seed ]; then
    say "seeding FFT wisdom into the VM (radiod start is gated on it)"
    gexec 30 "[ -s /etc/fftw/wisdomf ] || { mkdir -p /etc/fftw; echo $(base64 -w0 /root/sigmond-appliance/wisdomf-seed) | base64 -d > /etc/fftw/wisdomf; }" \
        || say "WARN: could not seed FFT wisdom — first radiod start waits on the planner"
fi
# sigmond-site-timing: everything the timing chain needs from the local
# site, discovered and wired automatically after bringup (rob 2026-07-29:
# NO manual-only fixes — whatever the live station needed by hand must be
# in the greenfield flow). The script rides the stick as its own payload
# file (staged by the importer); the sentinel runs it after each
# successful bringup, and it is safe to rerun any time.
say "installing the site-timing auto-wiring helper in the VM"
if [ -f /root/sigmond-appliance/sigmond-site-timing ]; then
    gexec 60 "echo $(base64 -w0 /root/sigmond-appliance/sigmond-site-timing) | base64 -d > /usr/local/sbin/sigmond-site-timing && chmod 755 /usr/local/sbin/sigmond-site-timing" \
        || say "WARN: could not install sigmond-site-timing"
else
    say "WARN: sigmond-site-timing not staged — timing chain will need manual wiring"
fi
# ⛔ Tell the VM which network the SITE is on.  site-timing discovers a LAN
# stratum-1 (T4) and a GNSS VTEC stream (:9000) by scanning the VM's own
# kernel route -- but since v3.50 the VM sits on the host-only 10.99.0.0/30
# behind this host, so it scanned four addresses holding nothing but us.
# AC0G-ND (v3.67, 2026-10-05) came up with NEITHER its LAN stratum-1 nor its
# VTEC box although both answered on 192.168.8.0/24.  Only this host sees
# the site LAN; write it where site-timing (and every rerun of it) can read it.
# The uplink is whatever device carries the IPv4 default route -- vmbr0 on a
# wired station, the wlan on a Wi-Fi-only one (sigmond-wifi never bridges it).
# Skip linkdown routes and the installer's 192.168.100.x fallback, which a
# wired PM can still hold beside its real lease, and our own host-only /30.
SITE_LAN=""
_updev=$(ip -4 route show default 2>/dev/null \
         | awk '!/linkdown/ && !/via 192\.168\.100\./{for(i=1;i<NF;i++) if($i=="dev"){print $(i+1); exit}}')
for _d in $_updev vmbr0; do
    SITE_LAN=$(ip -4 route show dev "$_d" proto kernel scope link 2>/dev/null \
               | awk '!/linkdown/ && $1 !~ /^192\.168\.100\./ && $1 !~ /^10\.99\.0\./ {print $1; exit}')
    [ -n "$SITE_LAN" ] && break
done
if [ -n "$SITE_LAN" ]; then
    gexec 15 "mkdir -p /etc/sigmond && echo '$SITE_LAN' > /etc/sigmond/site-lan" \
        && say "site LAN for discovery: $SITE_LAN" \
        || say "WARN: could not record the site LAN in the VM — T4/VTEC discovery will not find LAN servers"
else
    # a re-run at a new site must not leave the old site's subnet behind
    gexec 15 "rm -f /etc/sigmond/site-lan" || true
    say "WARN: no usable IPv4 site LAN — T4/VTEC discovery will not find LAN servers"
fi
# location authority: GPSDO position is definitive over operator entry
# (rob 2026-08-04) — ticked by the sentinel; staged from the stick
if [ -f /root/sigmond-appliance/sigmond-location-check ]; then
    gexec 60 "echo $(base64 -w0 /root/sigmond-appliance/sigmond-location-check) | base64 -d > /usr/local/sbin/sigmond-location-check && chmod 755 /usr/local/sbin/sigmond-location-check" \
        || say "WARN: could not install sigmond-location-check"
fi
say "installing the location-authority timer in the VM"
# ⛔ RETIRED 2026-09-02 — this used to be `sigmond-sdr-sentinel`, which ALSO ran
# `smd bringup dasi2 --non-interactive` whenever it saw an RX888 and no radiod
# conf.  That made hardware appearing an INSTRUCTION: a station brought its whole
# stack up, unattended and unasked, within two minutes of a device being plugged
# in.  `smd adopt` is now the only verb that starts anything, and it asks first.
#
# What survives here is the one job that genuinely is periodic — re-asserting the
# GPSDO position as the location authority.  sigmond-location-check calls
# sigmond-site-timing itself when the position moves, so a relocated station still
# re-wires its timing chain.  Hardware that appears is reported by `smd status`
# as adoptable and waits to be adopted.
LOCTICK_B64=$(base64 -w0 <<'LOCEOF'
#!/bin/bash
# sigmond-location-tick — periodic location authority (GPSDO definitive).
# Starts NOTHING.  Hardware that appears shows up in `smd status` as adoptable
# and waits for `smd adopt`.
exec 9>/run/sigmond-location-tick.lock; flock -n 9 || exit 0
[ -x /usr/local/sbin/sigmond-location-check ] || exit 0
exec /usr/local/sbin/sigmond-location-check
LOCEOF
)
LOCINST=$(cat <<INSTEOF
echo $LOCTICK_B64 | base64 -d > /usr/local/sbin/sigmond-location-tick
chmod 755 /usr/local/sbin/sigmond-location-tick
cat > /etc/systemd/system/sigmond-location-check.service <<'UEOF'
[Unit]
Description=Sigmond location authority: re-assert the GPSDO position
[Service]
Type=oneshot
TimeoutStartSec=300
ExecStart=/usr/local/sbin/sigmond-location-tick
UEOF
cat > /etc/systemd/system/sigmond-location-check.timer <<'TEOF'
[Unit]
Description=Periodic location authority (the GPSDO position is definitive)
[Timer]
OnBootSec=75
OnUnitActiveSec=120
[Install]
WantedBy=timers.target
TEOF
systemctl daemon-reload
# Retire the predecessor wherever this wizard runs against an existing station.
systemctl disable --now sigmond-sdr-sentinel.timer 2>/dev/null || true
rm -f /etc/systemd/system/sigmond-sdr-sentinel.timer \\
      /etc/systemd/system/sigmond-sdr-sentinel.service \\
      /usr/local/sbin/sigmond-sdr-sentinel
systemctl daemon-reload
systemctl enable --now sigmond-location-check.timer
INSTEOF
)
gexec 60 "echo $(echo "$LOCINST" | base64 -w0) | base64 -d > /tmp/sig-locchk-install.sh && bash /tmp/sig-locchk-install.sh && rm -f /tmp/sig-locchk-install.sh" \
    || say "WARN: could not install the location-authority timer"

# ── relocation / reconfigure propagation (rob 2026-08-04) ──────────────────
# When bringup has already run (radiod conf exists — i.e. this is a
# reconfigure, e.g. a station staged at one site being re-gridded at its
# deployment site), rerun the site wiring so identity flows into
# hf-timestd/[station], the metrology channel envs, and mag-recorder, and
# bounce the recorders so uploads carry the new grid immediately.
if gexec 15 "ls /etc/radio/radiod@*.conf >/dev/null 2>&1"; then
    say "existing station detected — re-applying site wiring (relocation-safe)"
    gexec 300 "[ -x /usr/local/sbin/sigmond-site-timing ] && /usr/local/sbin/sigmond-site-timing; true" \
        || say "WARN: site wiring rerun failed — check journal -t sigmond-site-timing in the VM"
    gexec 60 "systemctl try-restart 'wspr-recorder@*' 'psk-recorder@*' 'meteor-scatter@*' 2>/dev/null; true"
fi

# ── site-keys restore (optional, rob 2026-07-29) ────────────────────────────
# A returning station's registered keys ride the stick: after burning, the
# stick's small FAT (EFI) volume accepts `site-keys.tar.gz`, created on the
# old station with:
#   tar czf site-keys.tar.gz -C / etc/hs-uploader/keys home/timestd/.ssh
# The importer staged it; restore AFTER config render (which generates a
# fresh key when absent) so the registered key wins, then fix ownership.
KEYS_RESTORED=""
if [ -f /root/sigmond-appliance/site-keys.tar.gz ]; then
    say "restoring site keys from the install stick"
    if gexec 60 "echo $(base64 -w0 /root/sigmond-appliance/site-keys.tar.gz) | base64 -d | tar xzf - -C / etc/hs-uploader/keys home/timestd/.ssh 2>/dev/null; chown -R hsupload:sigmond /etc/hs-uploader/keys 2>/dev/null || chown -R root:sigmond /etc/hs-uploader/keys 2>/dev/null; chmod 600 /etc/hs-uploader/keys/id_ed25519* 2>/dev/null; chmod 644 /etc/hs-uploader/keys/*.pub 2>/dev/null; chown -R timestd:timestd /home/timestd/.ssh 2>/dev/null; chmod 700 /home/timestd/.ssh 2>/dev/null; ls /etc/hs-uploader/keys/id_ed25519_host"; then
        KEYS_RESTORED=1
        say "site keys restored (hs-uploader + timestd)"
    else
        say "WARN: site-keys restore failed — see $LOG"
    fi
fi

RADIOD_STATE="unknown"
# On a FIRST install the VM owns no USB controllers yet — firstboot binds them
# after this wizard and reboots — so asking the guest for an RX888 is a test
# that cannot pass, and every first install fell into the FX3-latch branch
# below and told the operator the SDR was "NOT VISIBLE TO THE DECODER VM".
# That message is true and useful AFTER passthrough; before it, it is just
# the install order.  Check whether the handover has happened at all first.
VM_HAS_USB=0
grep -qE '^hostpci[0-9]+:' "/etc/pve/qemu-server/${VMID}.conf" 2>/dev/null && VM_HAS_USB=1
if [ "$VM_HAS_USB" = 0 ] && [ "$HAVE_RX888" = 1 ]; then
    say "RX888 present on the host; its USB controller is handed to the VM at the reboot that follows"
    RADIOD_STATE="SDR bring-up runs after the reboot that completes passthrough"
elif [ "$VM_HAS_USB" = 0 ]; then
    RADIOD_STATE="no RX888 seen; nothing to bring up"
elif gexec 15 "lsusb | grep -qiE '04b4:00(f[013]|bc)|f4b3:0100'"; then
    say "RX888 detected — starting SDR bring-up now (takes a few minutes)..."
    # The operator is running this wizard with the radio already plugged in, so
    # bringing it up is what they asked for.  This is the ONE place bring-up
    # still happens without a separate `smd adopt` — the consent is the wizard
    # itself.  Hardware attached AFTER the install is reported by `smd status`
    # and waits to be adopted.
    #
    # ⛔ START THE FIRST-RUN UNIT.  Do not launch a second bring-up here.
    # This used to be `systemd-run --unit=sigmond-wizard-bringup smd bringup`,
    # a transient unit with its own name — so on a fresh install the wizard's
    # bring-up and sigmond-firstrun-bringup.service could run AT THE SAME TIME,
    # with nothing to stop them.  Two `smd bringup` runs contend for the
    # lifecycle lock (/var/lib/sigmond/lifecycle.lock, contract v0.5 §5.5), and
    # the loser does not wait — it fails the step.  On AI6VN (v3.50, 2026-09-21)
    # that cost TEN consecutive `smd install` steps, every one of them reporting
    # "lifecycle lock held by a background smd operation", and the station came
    # up without ka9q-web.  It read as nine broken components; it was one race.
    #
    # Driving the same unit both ways makes systemd the mutual exclusion:
    # `systemctl start` on a unit that is already running JOINS the existing
    # job instead of starting a second copy.  One implementation, one unit, one
    # log (/var/log/sigmond/firstrun-bringup.log rather than a transient
    # journal nobody thinks to read), and one marker.
    #
    # The marker is cleared first because it is what makes the boot-time run
    # fire only once.  The wizard IS fresh consent — an operator re-running it
    # with hardware attached means "bring this up" even if a previous bring-up
    # already completed — so re-arm the unit rather than silently no-op.  If
    # the unit happens to be running right now this is harmless: the marker is
    # written when it finishes.
    #
    # The fallback covers a wizard newer than its decoder template: a VM built
    # before the first-run unit existed has nothing to start, and without this
    # the station would come up with NOTHING running and no error — the exact
    # silent-dead-install class this whole path exists to prevent.  There is no
    # race in that case, because the unit that would have raced does not exist.
    if gexec 15 "systemctl cat sigmond-firstrun-bringup.service >/dev/null 2>&1"; then
        gexec 30 "rm -f /var/lib/sigmond/.firstrun-bringup-done; \
            systemctl start --no-block sigmond-firstrun-bringup.service" || true
    else
        say "  (older template: no first-run unit — running bring-up directly)"
        gexec 30 "systemd-run --unit=sigmond-wizard-bringup --collect \
            smd bringup dasi2 --non-interactive" || true
    fi
    RADIOD_STATE="bringup launched — still settling; check later with: sigmond-vm smd admin validate"
    # ⛔ Say something WHILE waiting.  This printed one line and then nothing
    # for five minutes, which is indistinguishable from a hang -- rob, at the
    # console on 2026-09-21: "the wizard is hung at detected starting SDR".
    # It was not hung; it was doing exactly this loop.  A bounded wait that
    # looks like a crash is a bug in the reporting, not the waiting.
    say "  waiting up to 5 min for radiod to come up (bring-up continues in the"
    say "  background either way; the wizard will move on when the time is up)"
    for i in $(seq 1 30); do
        if gexec 15 "systemctl list-units --state=active 'radiod@*' --no-legend 2>/dev/null | grep -q radiod@"; then
            RADIOD_STATE="radiod ACTIVE ✓ (decoding starts within ~2 min cycles)"
            say "  radiod is active after $(( i * 10 ))s"
            break
        fi
        [ $(( i % 6 )) -eq 0 ] && say "  ... still waiting ($(( i / 6 )) of 5 min) — this is normal on a cold box"
        sleep 10
    done
    case "$RADIOD_STATE" in
        radiod\ ACTIVE*) : ;;
        *) say "  radiod did not report active within 5 min — continuing; bring-up"
           say "  is still running in the background. Follow it with:"
           say "    sigmond-vm tail -f /var/log/sigmond/firstrun-bringup.log" ;;
    esac
elif [ "$HAVE_RX888" = 1 ]; then
    # The pre-flight saw an RX888 on this machine, but the VM cannot.  This is
    # the first-install USB handoff, and it is expected exactly once:
    #
    #   1. the host boots and enumerates the RX888 in its FX3 boot ROM
    #      (04b4:00f3, USB 2.0) -- the host has no rx888_boot, so it sits
    #      there claimed but unloaded;
    #   2. firstboot binds the USB controllers to the VM and reboots;
    #   3. the FX3 is handed across mid-state and never re-enumerates in the
    #      guest -- no `add` uevent, so 70-rx888-boot.rules never fires
    #      rx888_boot.service, so no firmware, so no RX888.
    #
    # On EVERY later boot the controllers are already assigned at boot and the
    # host never touches the device, so the guest enumerates it cleanly.  That
    # is why this is an install-only problem (rob, 2026-08-09).
    #
    # A warm reboot does NOT clear it: the FX3 stays latched as long as VBUS is
    # maintained.  Only removing power -- or physically replugging the RX888 --
    # resets it.  Say so plainly, because "reboot" is what an operator will try
    # first and it will not work.
    say ""
    say "  ── RX888 not visible to the decoder VM ──────────────"
    say "  An RX888 was detected on this machine, but the VM cannot see it."
    say "  This is EXPECTED on a first install: the SDR was handed across to"
    say "  the VM mid-state and needs its power removed to reset."
    say ""
    say "  ==> The installer POWERS THIS MACHINE OFF at the end of setup;"
    say "      that power-off is exactly the reset the SDR needs (a reboot"
    say "      would NOT be — the FX3 stays latched while USB power is held)."
    say ""
    say "  After you power back on the VM enumerates the SDR cleanly, and"
    say "  this will not recur on later boots.  Then run 'smd adopt' to start"
    say "  radiod: the station starts no hardware it was not asked to start."
    say "  If the SDR STILL fails to appear then, unplug and replug the"
    say "  RX888's USB cable (some boards keep USB power even in soft-off)."
    say "──────────────────────────────────────────────────────"
    RADIOD_STATE="RX888 present on the host but NOT yet visible to the VM —
            expected on a first install; the POWER-OFF at the end of setup
            resets it.  After the next power-on, run 'smd adopt' to start
            radiod"
    # The wizard's transcript does not survive the power-off that follows, so
    # the operator never sees the paragraph above (rob 2026-08-09: "after the
    # reboot I did not see any mention of the need to re-plug or power cycle").
    # Leave a marker: the console access panel repeats the instruction on every
    # boot, and clears it by itself once the SDR turns up.
    : > "$MARK_DIR/.rx888-needs-powercycle"
else
    RADIOD_STATE="NO RX888 on the VM's USB bus — plug it in (or re-seat its USB
            cable if it was already in: a wedged FX3 needs a physical replug),
            then run 'smd adopt' to start radiod.  Nothing starts on its own"
fi
say "SDR/radiod: $RADIOD_STATE"

# ── host RAC (optional) ─────────────────────────────────────────────────────
# ── operator access to the decoder VM ──────────────────────────────
# The operator account is the 'sigmond' user — remote root ssh login is
# DISABLED (root stays usable on the console / qm terminal for recovery).
# sigmond gets the SAME password as this host's root (hash copy — one
# password for the whole appliance), this host's SSH key, and sudo.
say "setting up VM accounts (ONE password — every account tracks this host's root)"
gexec 30 "id sigmond >/dev/null 2>&1 || useradd -m -s /bin/bash sigmond; usermod -s /bin/bash sigmond; getent group sudo >/dev/null && usermod -aG sudo sigmond || true" \
    || say "WARN: could not ensure sigmond operator user in VM"
# ...and sudo WITHOUT a password prompt, like the hamsci account beside it
# (rob 2026-09-15: "there's no reason for it to require").  Membership of the
# sudo group alone still prompts, and the operator account exists to run smd,
# systemctl and journalctl all day; on a key-only account reached over ssh
# the prompt buys nothing.  Written with visudo -c so a malformed file can
# never lock the account out of sudo entirely.
gexec 20 "printf '%s\\n' 'sigmond ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/.sigmond.new && chmod 440 /etc/sudoers.d/.sigmond.new && visudo -c -q -f /etc/sudoers.d/.sigmond.new && mv /etc/sudoers.d/.sigmond.new /etc/sudoers.d/sigmond || rm -f /etc/sudoers.d/.sigmond.new" \
    || say "WARN: could not grant sigmond passwordless sudo in VM"
# ...and the shell conveniences install.sh seeds for operator accounts.
# install.sh only seeds sigmond "if it already exists", and at image-build
# time it does NOT: this wizard creates the account, long after the image
# was built.  So a freshly installed station had tmux mouse support for
# hamsci and not for sigmond, which is the account the panel tells operators
# to use (rob 2026-09-15: "it wasn't there after installation").
# Seed-if-absent, so an operator who set their own config keeps it.
# ASCII ONLY, and resolve the account's REAL home.  This line as first
# written carried an em dash — the only non-ASCII byte in any gexec — and
# wrote to a hardcoded /home/sigmond that need not exist (the template
# already has a sigmond service account, so the useradd -m above is
# skipped).  Live effect, v3.39 and v3.40: the guest-exec HUNG, timed out,
# and took the qemu guest agent down with it, after which EVERY later step
# failed "QEMU guest agent is not running" — silently skipping the password
# hash copy and the ssh policy, so the VM shipped with no password login at
# all (found by the nested test 2026-09-16, twice, deterministic).
gexec 20 "H=\$(getent passwd sigmond | cut -d: -f6); [ -n \"\$H\" ] || H=/home/sigmond; mkdir -p \"\$H\" && { test -e \"\$H/.tmux.conf\" || { printf '%s\\n' '# added by sigmond-setup - tmux mouse support' 'set -g mouse on' > \"\$H/.tmux.conf\" && chown sigmond:sigmond \"\$H/.tmux.conf\"; }; }" \
    || say "WARN: could not seed sigmond's .tmux.conf in VM"
# operator accounts must read fleet state: smd status parses
# group-readable client configs (hamsci hit Errno 13 on
# timestd-config.toml, 2026-07-30) — grant the service groups.
# gpsdo: operator CLI probes (gpsdo-monitor status / smd gpsdo) open
# /dev/hidraw* directly (hamsci lacked it on b4, 2026-08-13 — bench
# probes needed sudo)
gexec 30 "for u in hamsci sigmond; do for g in sigmond timestd pskrec wsprrec radio gpsdo; do getent group \$g >/dev/null && usermod -aG \$g \$u; done; done; true" \
    || say "WARN: could not add operator accounts to service groups"
HASH=$(getent shadow root | cut -d: -f2)
if [ -n "$HASH" ] && [ "$HASH" != "*" ] && [ "$HASH" != "!" ]; then
    # Transport the hash as base64: yescrypt hashes are full of $ runs
    # ($y$j9T$...) which the double-quoted gexec string used to expand to
    # nothing — every fresh v3.30 install shipped VM accounts with a
    # mangled hash and no working password (found on B4, 2026-08-11).
    HB64=$(printf '%s' "$HASH" | base64 -w0)
    gexec 30 "H=\$(echo $HB64 | base64 -d); for u in sigmond root hamsci; do usermod -p \"\$H\" \$u; done" \
        || say "WARN: could not set VM account passwords"
fi
[ -f /root/.ssh/id_ed25519 ] || ssh-keygen -q -t ed25519 -N "" -f /root/.ssh/id_ed25519
PUB=$(cat /root/.ssh/id_ed25519.pub)
# Install the key in sigmond's REAL home — the template's service account
# may not live in /home/sigmond (hardcoding that put the key where sshd
# never looks: observed 'Permission denied (publickey)' 2026-07-03).
# Ownership/modes matter too: sshd StrictModes rejects root-owned homes.
gexec 30 "H=\$(getent passwd sigmond | cut -d: -f6); case \"\$H\" in ''|/|/nonexistent) usermod -d /home/sigmond sigmond; H=/home/sigmond;; esac; G=\$(id -gn sigmond); mkdir -p \"\$H\"; chown sigmond:\"\$G\" \"\$H\"; install -d -m 700 -o sigmond -g \"\$G\" \"\$H/.ssh\"; grep -qF '$PUB' \"\$H/.ssh/authorized_keys\" 2>/dev/null || echo '$PUB' >> \"\$H/.ssh/authorized_keys\"; chown sigmond:\"\$G\" \"\$H/.ssh/authorized_keys\"; chmod 600 \"\$H/.ssh/authorized_keys\"" \
    || say "WARN: could not install host ssh key for sigmond"
# ssh policy: password login ON, remote root OFF. Cloud-init images ship
# PasswordAuthentication no in 50-cloud-init.conf; OpenSSH takes the FIRST
# value it sees and reads sshd_config.d alphabetically — 10- beats 50-.
# AuthorizedKeysFile: sigmond's home IS the source tree (/opt/git/sigmond,
# group-writable+setgid), which StrictModes rightly distrusts — so keys
# also live in root-owned /etc/ssh/authorized_keys.d/%u (observed
# 2026-07-03: key refused from ~/.ssh, password fine).
# StrictModes no: operators add THEIR keys with plain ssh-copy-id, which
# only knows ~/.ssh/authorized_keys — under the group-writable home,
# StrictModes silently ignored those keys (observed: ssr/ssh-copy-id
# looped forever re-adding a key sshd refused to trust). The 'offending'
# group is sigmond's own private group, so this is safe here.
gexec 30 "install -d -m 755 /etc/ssh/authorized_keys.d && printf '%s\n' '$PUB' > /etc/ssh/authorized_keys.d/sigmond && chmod 644 /etc/ssh/authorized_keys.d/sigmond && install -d /etc/ssh/sshd_config.d && printf 'PasswordAuthentication yes\nPermitRootLogin no\nStrictModes no\nAuthorizedKeysFile .ssh/authorized_keys /etc/ssh/authorized_keys.d/%%u\n' > /etc/ssh/sshd_config.d/10-sigmond-operator.conf && rm -f /etc/ssh/sshd_config.d/50-sigmond-no-root.conf && { systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || systemctl restart ssh 2>/dev/null || systemctl restart sshd; }" \
    || say "WARN: could not set VM ssh policy (password on / remote root off)"
gexec 30 "systemctl enable --now serial-getty@ttyS0.service" || true
# operator comfort (rob 2026-07-30): tmux mouse mode for every login user
# (btop + tmux binaries are baked into the template since v3.16)
gexec 30 "for h in /root /home/hamsci /home/sigmond /etc/skel; do [ -d \$h ] && { echo 'set -g mouse on' > \$h/.tmux.conf; o=\$(stat -c %U \$h 2>/dev/null); [ \"\$o\" != root ] && chown \$o: \$h/.tmux.conf; }; done; true" \
    || say "WARN: could not install .tmux.conf in the VM"
# operator shell helpers: rob's tm() tmux picker + ll/lrt aliases,
# system-wide via /etc/profile.d (bash-guarded; staged from the stick)
if [ -f /root/sigmond-appliance/sigmond-operator.sh ]; then
    gexec 30 "echo $(base64 -w0 /root/sigmond-appliance/sigmond-operator.sh) | base64 -d > /etc/profile.d/sigmond-operator.sh && chmod 644 /etc/profile.d/sigmond-operator.sh; grep -q sigmond-operator /etc/bash.bashrc 2>/dev/null || echo '[ -f /etc/profile.d/sigmond-operator.sh ] && . /etc/profile.d/sigmond-operator.sh' >> /etc/bash.bashrc" \
        || say "WARN: could not install operator shell helpers in the VM"
fi
# Catch-all DHCP: the template's build-time NIC name never matches the
# deployed VM's (observed: no IP on real hardware) — match en* instead.
say "ensuring decoder VM networking (DHCP on any ethernet NIC)"
gexec 90 "mkdir -p /etc/systemd/network && { echo '[Match]'; echo 'Name=en*'; echo; echo '[Network]'; echo 'DHCP=yes'; } > /etc/systemd/network/99-dhcp-en.network && systemctl enable --now systemd-networkd && networkctl reload && for l in /sys/class/net/en*; do ip link set \$(basename \$l) up 2>/dev/null; done; sleep 8" \
    || say "WARN: could not configure VM networking"
# DHCP can take a while after networkctl reload — poll up to ~90 s for a
# lease instead of a single grab (one-shot came up empty on real hardware).
say "waiting for the decoder VM to get an IP address (DHCP)..."
VMIP=""
for i in $(seq 1 18); do
    VMIP=$(qm guest exec "$VMID" --timeout 15 -- bash -lc "ip -4 -br addr show scope global" 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    [ -n "$VMIP" ] && break
    sleep 5
done
[ -z "$VMIP" ] && say "WARN: VM has no IPv4 address yet — check cabling/DHCP, then: qm guest exec $VMID -- ip -4 -br addr"

# ── sigmond-vm: shell into the VM without knowing its (DHCP) address ────────
# The operator shouldn't have to hunt for an IP that DHCP can change —
# this helper asks the guest agent for the CURRENT address every time.
say "installing the sigmond-vm helper (ssh to the VM, IP auto-discovered)"
cat > /usr/local/bin/sigmond-vm <<'VMEOF'
#!/bin/bash
# sigmond-vm — shell into the Sigmond decoder VM as 'sigmond'; the VM's
# current IP is discovered live via the qemu guest agent (DHCP-proof).
# Installed by sigmond-setup.
#   sigmond-vm             interactive shell
#   sigmond-vm <cmd...>    run a command in the VM
#   sigmond-vm --ip        just print the VM's current IPv4
# Same discovery the wizard uses: a hardcoded 120 sends this at a VM that does
# not exist on a host whose decoder VM is 100.
VMID="${SIGMOND_VMID:-}"
if [ -z "$VMID" ]; then
    VMID=$(sed -n 's/^Environment=SIGMOND_VMID=\([0-9][0-9]*\).*/\1/p' \
           /etc/systemd/system/sigmond-import.service 2>/dev/null | head -1)
fi
if [ -z "$VMID" ]; then
    _v=$(qm list 2>/dev/null | awk 'NR>1 && $1 ~ /^[0-9]+$/ {print $1}')
    [ "$(printf '%s\n' "$_v" | grep -c .)" = 1 ] && VMID="$_v"
fi
VMID="${VMID:-100}"
if ! qm status "$VMID" >/dev/null 2>&1; then
    echo "sigmond-vm: VM $VMID does not exist. Known VMs:" >&2
    qm list 2>/dev/null >&2
    echo "sigmond-vm: if it has another id: SIGMOND_VMID=<id> sigmond-vm" >&2
    exit 1
fi
if ! qm status "$VMID" | grep -q running; then
    echo "sigmond-vm: VM $VMID is not running — try: qm start $VMID" >&2; exit 1
fi
IP=""
for i in 1 2 3; do
    IP=$(qm agent "$VMID" network-get-interfaces 2>/dev/null \
         | grep -oE '"ip-address" *: *"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+"' \
         | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | grep -v '^127\.' | head -1)
    [ -n "$IP" ] && break
    sleep 2
done
if [ -z "$IP" ]; then
    # Agent-free fallback (AI6VN 2026-09-09): the bridge's ARP table knows
    # the VM's address even when qemu-guest-agent is wedged.
    _mac=$(qm config "$VMID" 2>/dev/null | grep -oE "(virtio|e1000|vmxnet3|rtl8139)=[0-9A-Fa-f:]{17}" | head -1 | cut -d= -f2 | tr A-Z a-z)
    [ -n "$_mac" ] && IP=$(ip -4 neigh show 2>/dev/null | awk -v m="$_mac" 'tolower($5)==m && $1 !~ /^169\.254\./ {print $1; exit}')
    [ -n "$IP" ] && echo "sigmond-vm: guest agent not answering — using ARP address $IP" >&2
fi
if [ -z "$IP" ]; then
    echo "sigmond-vm: the guest agent reports no IPv4 yet and ARP has no entry — check the VM's network" >&2; exit 1
fi
if [ "${1:-}" = "--ip" ]; then echo "$IP"; exit 0; fi
# dedicated known_hosts: sigmond-setup clears it when it regenerates the
# VM's host keys, so operators never see a MITM warning for their own VM
exec ssh -o StrictHostKeyChecking=accept-new \
         -o UserKnownHostsFile=/etc/sigmond-appliance/vm-known_hosts \
         sigmond@"$IP" "$@"
VMEOF
chmod +x /usr/local/bin/sigmond-vm

# ── PROVE the operator login works (don't just claim it) ───────────────────
# Key login, host→VM, via the same helper path the operator will use.
# Fresh known_hosts each run: personalize regenerates the VM's host keys,
# so a cached entry from a previous run always clashes — clear + re-seed.
rm -f "$MARK_DIR/vm-known_hosts"
SSH_STATE="not verified — VM had no IP during setup"
if [ -n "$VMIP" ]; then
    if ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new \
           -o UserKnownHostsFile="$MARK_DIR/vm-known_hosts" sigmond@"$VMIP" true 2>>"$LOG"; then
        SSH_STATE="verified from this host ✓"
    else
        SSH_STATE="FAILED — key login from this host was refused; see $LOG"
        say "WARN: ssh sigmond@$VMIP verification FAILED"
        qm guest exec "$VMID" --timeout 15 -- bash -lc \
            "sshd -T 2>/dev/null | grep -Ei '^(passwordauthentication|permitrootlogin|pubkeyauthentication)'; getent passwd sigmond; ls -la \$(getent passwd sigmond | cut -d: -f6)/.ssh/ 2>&1" >>"$LOG" 2>&1
    fi
fi

RAC_STATE=""
# The relays that carry the VM's channels.  A function, so that the lines
# below keep their bytes: pm-align reads these very templates out of the
# release's wizard to refresh a station's relay units.
rac_install_relays(){
            # DHCP-proof relays: frpc needs a fixed local target, but the
            # VM's address can change — so the vm-* channels point at local
            # sockets whose per-connection handler asks the guest agent for
            # the VM's CURRENT IP (same trick as sigmond-vm).
            say "installing the VM port relays (ssh, ka9q-web, station-web, gmag)"
            install -d /usr/local/lib/sigmond
            cat > /usr/local/lib/sigmond/vm-port-relay.py <<'RLEOF'
#!/usr/bin/env python3
# vm-port-relay.py <vm-port> — inetd-style relay for ONE accepted connection
# (systemd socket with Accept=yes): stdin/stdout is the client socket.
#
# ⛔ THE GUEST AGENT IS NOT ON THE HOT PATH.  It used to be: this script asked
# `qm agent <vmid> network-get-interfaces` on EVERY accepted connection, "so
# the relay keeps working when DHCP moves the VM".  Since v3.44 DHCP cannot
# move the VM -- it sits on a host-only /30 at a fixed address, and the PM is
# the other end of that /30.
#
# Meanwhile the cost of asking is not zero, it is the agent's TIMEOUT.  On
# AI6VN (v3.53, 2026-09-24) the guest agent was running inside the VM with its
# virtio port present, but the PM's qemu process had no chardev for it, so
# every `qm agent` call spent ~3.8 s before failing.  Per connection.  Every
# page load, asset and ssh session paid it:
#
#     PM -> VM direct  (10.99.0.2:8081)   0.0006 s
#     PM -> this relay (127.0.0.1:12223)  4.5590 s
#
# The services were perfect and the station looked broken.  rob: "AI6VN is
# not present -web and the ssh is slow but functions."
#
# So: try the management peer FIRST -- one cheap TCP connect to an address we
# already know -- and only consult the agent if that fails.  The agent stays
# as a fallback for pre-v3.44 topologies where the VM really can move.
import os, re, select, socket, struct, subprocess, sys

port = int(sys.argv[1])
vmid = os.environ.get("SIGMOND_VMID", "120")
ips = []

def _mgmt_peer():
    """The other end of the host-only /30 the decoder VM lives on.

    Derived, not hardcoded: read the PM's own address on a /30 and flip the
    host bit.  10.99.0.1 -> 10.99.0.2.  Returns None if this PM has no /30,
    which is exactly the pre-v3.44 case that still needs the agent.
    """
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"],
                             capture_output=True, text=True, timeout=5).stdout
        for m in re.finditer(r'inet (\d+\.\d+\.\d+\.\d+)/30\b', out):
            a = struct.unpack("!I", socket.inet_aton(m.group(1)))[0]
            # /30: .0 network, .1 and .2 usable, .3 broadcast.  We are one of
            # the two usable ones; the VM is the other.
            peer = (a & 0xFFFFFFFC) | (3 - (a & 3))
            if (a & 3) in (1, 2):
                return socket.inet_ntoa(struct.pack("!I", peer))
    except Exception:
        pass
    return None

_peer = _mgmt_peer()
if _peer:
    try:
        _probe = socket.create_connection((_peer, port), timeout=2)
        _probe.close()
        ips = [_peer]
    except Exception:
        ips = []      # fixed address did not answer — fall through to the agent

if not ips:
    try:
        out = subprocess.run(["qm", "agent", vmid, "network-get-interfaces"],
                             capture_output=True, text=True, timeout=10).stdout
        ips = [ip for ip in re.findall(r'"ip-address"\s*:\s*"(\d+\.\d+\.\d+\.\d+)"', out)
               if not ip.startswith("127.")]
    except Exception:
        pass
if not ips:
    # Agent-free fallback (AI6VN 2026-09-09): a wedged qemu-guest-agent must
    # not take the RAC channels down with it.  The host's bridge learned the
    # VM's address from ARP; look up the VM's NIC MAC in the neighbour table.
    try:
        cfg = subprocess.run(["qm", "config", vmid], capture_output=True, text=True, timeout=10).stdout
        mac = re.search(r'(?:virtio|e1000|vmxnet3|rtl8139)=([0-9A-Fa-f:]{17})', cfg)
        neigh = subprocess.run(["ip", "-4", "neigh", "show"], capture_output=True, text=True, timeout=10).stdout
        if mac:
            m = mac.group(1).lower()
            ips = [l.split()[0] for l in neigh.splitlines()
                   if "lladdr" in l and l.split()[l.split().index("lladdr") + 1].lower() == m
                   and not l.startswith("169.254.")]
    except Exception:
        pass
if not ips:
    sys.exit(1)
# Prefer an address on a network THIS HOST is directly attached to.  Taking
# ips[0] blindly assumes every address the guest reports is reachable from
# here, and that is exactly the assumption that fails when the site puts the
# PM and its own VM in different VLANs: the VM answers with a campus lease
# the PM cannot open a socket to, every vm-* channel dies, and the dashboard
# link looks broken while the tunnel is perfectly healthy (DASI-019 Scranton,
# 2026-09-19 -- ICMP hairpinned out and back at 8.9 ms while TCP 22 was
# refused).  A host-only management NIC fixes the reachability; this makes
# the relay actually pick it.
def _local_nets():
    out = subprocess.run(["ip", "-4", "-o", "addr", "show"],
                         capture_output=True, text=True, timeout=10).stdout
    nets = []
    for m in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", out):
        addr, plen = m.group(1), int(m.group(2))
        if addr.startswith("127."):
            continue
        a = struct.unpack("!I", socket.inet_aton(addr))[0]
        mask = (0xFFFFFFFF << (32 - plen)) & 0xFFFFFFFF
        nets.append((a & mask, mask))
    return nets
try:
    nets = _local_nets()
    def _is_local(ip):
        v = struct.unpack("!I", socket.inet_aton(ip))[0]
        return any((v & m) == n for n, m in nets)
    ips.sort(key=lambda ip: 0 if _is_local(ip) else 1)
except Exception:
    pass
vm = None
for cand in ips:                       # try every address, not just the first
    try:
        vm = socket.create_connection((cand, port), timeout=10)
        break
    except OSError:
        continue
if vm is None:
    sys.exit(1)
vm.settimeout(None)
client_open, vm_open = True, True
try:
    while client_open or vm_open:
        watch = ([0] if client_open else []) + ([vm] if vm_open else [])
        r, _, _ = select.select(watch, [], [], 900)
        if not r:
            break  # idle 15 min
        if 0 in r:
            d = os.read(0, 65536)
            if d:
                vm.sendall(d)
            else:
                client_open = False
                try: vm.shutdown(socket.SHUT_WR)
                except OSError: pass
                if not vm_open: break
        if vm in r:
            try: d = vm.recv(65536)
            except OSError: d = b""
            if d:
                os.write(1, d)
            else:
                vm_open = False
                if not client_open: break
except (BrokenPipeError, ConnectionResetError):
    pass
finally:
    vm.close()
RLEOF
            chmod 755 /usr/local/lib/sigmond/vm-port-relay.py
            for spec in "ssh:12222:22" "web:12223:8081" "station:12224:8000" "gmag:12225:8082"; do
                IFS=: read -r RNAME RLPORT RVPORT <<<"$spec"
                cat > "/etc/systemd/system/sigmond-vm-$RNAME-relay.socket" <<SOCKEOF
[Unit]
Description=Relay 127.0.0.1:$RLPORT → decoder VM :$RVPORT (IP via guest agent)
[Socket]
ListenStream=127.0.0.1:$RLPORT
Accept=yes
[Install]
WantedBy=sockets.target
SOCKEOF
                cat > "/etc/systemd/system/sigmond-vm-$RNAME-relay@.service" <<SVCEOF
[Unit]
Description=decoder-VM $RNAME relay (%i)
CollectMode=inactive-or-failed
[Service]
Type=simple
Environment=SIGMOND_VMID=$VMID
StandardInput=socket
StandardOutput=socket
StandardError=journal
ExecStart=/usr/local/lib/sigmond/vm-port-relay.py $RVPORT
SVCEOF
            done
            systemctl daemon-reload
}
rac_record_inputs(){
    # What a registration that runs alone must know.  Written before the
    # switch runs, because the switch registers in a process of its own.
    printf '%s\n' "$RAC_PROFILE" > "$MARK_DIR/rac-profile"
    [ -n "$DASI_NUM" ] && printf '%s\n' "$DASI_NUM" > "$MARK_DIR/dasi-number"
    rac_site_name > "$MARK_DIR/rac-site"
    rac_save_pin
}
rac_activate(){
    # Remote access at the end of a wizard run (tasks/plan-remote-access.md).
    # A first install turns it on, for every station class, and asks nothing.
    # A later run leaves the switch where its operator put it.  Sets
    # RAC_STATE for the summary.  It never fails the wizard: a station with
    # no gateway in reach still finishes its install, and the host keeps
    # trying.
    local want out rc line asked=no
    # The wish comes first, before anything that can fail.  A wish on file
    # decides.  With none on file, a first install wants it on, and the wizard
    # records that itself, in the format the switch writes: a run that dies
    # below must not leave a station that the next run reads as one that
    # declined.  A configured station with no wish on file predates v3.71; the
    # switch reads the wish from what stands, once the switch exists.
    want=$(head -1 "$MARK_DIR/rac-desired" 2>/dev/null)
    case "$want" in on|off) ;; *) want="" ;; esac
    if [ -z "$want" ] && [ ! -e "$CONF_MARK" ]; then
        want=on
        mkdir -p "$MARK_DIR" \
            && printf '%s\n' on > "$MARK_DIR/rac-desired.new" \
            && mv -f "$MARK_DIR/rac-desired.new" "$MARK_DIR/rac-desired" \
            && printf '%s %s %s\n' on "$(date -u +%Y-%m-%dT%H:%M:%SZ)" install > "$MARK_DIR/rac-changed" \
            || say "WARN: could not record the remote-access wish in $MARK_DIR; carrying on with it on"
    fi
    rac_install_relays
    install -m 755 /root/sigmond-appliance/sigmond/scripts/proxmox/pm-align.py /usr/local/sbin/pm-align 2>/dev/null \
        || say "WARN: pm-align not installed (sigmond tree lacks it)"
    rac_record_inputs
    if ! rac_install_host_files; then
        RAC_STATE="FAILED: could not install the remote-access files on this host (the switch or its units)"
        [ -n "$want" ] && RAC_STATE="$RAC_STATE
          The wish stands on file (remote access $want)."
        RAC_STATE="$RAC_STATE
          To try again, run on this host: sigmond-setup --reconfigure"
        say "WARN: $RAC_STATE"
        return 0
    fi
    if [ -z "$want" ]; then
        # A silent switch is no decision.  Clear the old answer first, so that
        # only this run's answer can stand.
        rm -f "$RAC_STATE_FILE"
        "$RAC_SWITCH" status >/dev/null 2>&1
        asked=yes
        want=$(sed -n 's/^wish=//p' "$RAC_STATE_FILE" 2>/dev/null | head -1)
        case "$want" in
            on|off) ;;
            *)
                RAC_STATE="FAILED: could not read the remote-access switch's state on this host; look at it with: $RAC_SWITCH status"
                say "WARN: $RAC_STATE"
                return 0 ;;
        esac
    fi
    if [ "$want" != on ]; then
        RAC_STATE="off, as its operator left it. Turn it on from the decoder VM: smd remote on"
        say "remote access stays off, as its operator left it"
        # The wish reads off, and the tunnel service can run all the same:
        # someone started it outside the switch.  The summary must not read a
        # plain "off" over an open door.  `status` changes nothing; the old
        # state file goes first, so that only this run's answer can stand.
        if [ "$asked" = no ]; then
            rm -f "$RAC_STATE_FILE"
            "$RAC_SWITCH" status >/dev/null 2>&1
        fi
        if [ "$(sed -n 's/^tunnel=//p' "$RAC_STATE_FILE" 2>/dev/null | head -1)" = running ]; then
            RAC_STATE="off, as its operator left it, BUT THE TUNNEL SERVICE STILL RUNS:
          this station can still be reached. Close it from the decoder VM: smd remote off"
            say "WARN: remote access reads off, but the tunnel service still runs; close it from the decoder VM: smd remote off"
        fi
        return 0
    fi
    say "remote access: bringing it up (turn it off later from the decoder VM: smd remote off)"
    # A station that registered before registers again: its reporter id may
    # have changed.  A station that never did registers inside the switch.
    if [ -s "$RAC_TOML" ] && ! rac_register fresh; then
        say "WARN: could not register afresh ($RAC_FAIL); the earlier registration stays"
    fi
    # The switch's lines reach the console and the log as they come.  A
    # registration and the wait for the channels can take minutes, and a
    # console that prints nothing for minutes reads as a hang.  The summary
    # still comes from the same lines.  (`wait` on the process substitution
    # hands back the switch's exit status.)
    out=""
    while IFS= read -r line || [ -n "$line" ]; do
        printf '  %s\n' "$line" | tee -a "$LOG"
        out="$out$line
"
    done < <(SIGMOND_RAC_BY=install "$RAC_SWITCH" on 2>&1)
    wait "$!"; rc=$?
    RAC_STATE=$(printf '%s' "$out" | sed -n '/^Remote access: /,$p' \
                | sed -e '1s/^Remote access: //' -e '2,$s/^/          /')
    [ -n "$RAC_STATE" ] || RAC_STATE="FAILED: $RAC_SWITCH gave no answer (exit $rc); try: sigmond-setup --rac-on"
    case "$(head -1 "$MARK_DIR/rac-tier" 2>/dev/null)" in
        *UNSECURED*|*unsecured*)
            RAC_STATE="$RAC_STATE
              ** this tunnel is UNSECURED (no TLS): the secure gateways did
                 not answer.  Once one is available run: sigmond-setup --rac-upgrade **" ;;
    esac
    return 0
}
rac_activate

# The switch works from the decoder VM only once the two machines pair.
PAIR_STATE="paired: in the decoder VM, smd remote on / off / status"
rac_pair_vm; _prc=$?
if [ "$_prc" = 2 ]; then
    # Another pairing holds the lock (the pairing's own timer started one) and
    # does the work.  That is no failure, and nothing is left for the reader
    # to run.
    PAIR_STATE="a pairing runs already on this host, and it does the work.
            To see when it has finished, in the decoder VM: smd remote status"
    say "remote-access switch: $PAIR_STATE"
elif [ "$_prc" != 0 ]; then
    PAIR_STATE="NOT PAIRED ($RAC_PAIR_FAIL).
            Until it pairs, smd remote cannot reach this host. Later, on this host: sigmond-setup --pair-vm"
    say "WARN: remote-access switch: $PAIR_STATE"
fi
if [ -n "$RAC_PAIR_WARN" ]; then
    PAIR_STATE="$PAIR_STATE
            WARN: $RAC_PAIR_WARN"
    say "WARN: remote-access switch: $RAC_PAIR_WARN"
fi

PSWS_STATE="skipped — add later: sigmond-setup --reconfigure"
if [ -n "$PSWS_ID" ] && [ -n "$KEYS_RESTORED" ]; then
    if gexec_unit 90 psws-verify "smd psws verify"; then
        PSWS_STATE="station $PSWS_ID — registered key RESTORED from the stick, verify PASSED ✓"
    else
        PSWS_STATE="station $PSWS_ID — key restored from the stick; verify pending
            (portal may still need the key; check with: smd psws verify)"
    fi
elif [ -n "$PSWS_ID" ]; then
    PSWS_STATE="station $PSWS_ID — IDs recorded, UPLOAD KEY NOT YET REGISTERED.
            Finish from any computer: log into the VM (sigmond-vm) — the
            login banner shows the key to paste into the PSWS portal,
            then run:  smd psws verify"
fi

echo "$REPORTER $GRID $(date -u +%F)" > "$CONF_MARK"
# Station class + DASI number stick across --reconfigure / --rac-upgrade.
printf '%s\n' "$RAC_PROFILE" > "$MARK_DIR/rac-profile"
[ -n "$DASI_NUM" ] && printf '%s\n' "$DASI_NUM" > "$MARK_DIR/dasi-number"

# The wizard unit must NOT stay enabled once configured: its
# Conflicts=getty@tty1 stop job fires even when the Condition check fails
# (conditions don't remove Conflicts from the boot transaction — same bug
# class as the dasi-install tty takeover, 2026-07-26), which left every
# post-wizard boot with a BLANK console: no login prompt, no access panel
# (observed on B4 v3.1 and rob's Kamrui, 2026-07-27/28).
# disable ONLY — do NOT start getty here: Conflicts is symmetric, so a
# getty start at this point SIGTERMs the wizard itself mid-run (v3.6 field
# run 2026-07-29: summary never written/shown). The unit's ExecStopPost
# already hands the console to getty when the wizard exits.
systemctl disable sigmond-wizard.service 2>/dev/null

# ── final summary ───────────────────────────────────────────────────────────
# Print it, save it (/root/sigmond-setup-summary.txt), pin it above the tty1
# login prompt (/etc/issue) and after ssh login (/etc/motd), and HOLD the
# console until the operator acknowledges — getty resets the tty the moment
# we exit, which used to erase everything (observed 2026-07-03).
HOSTIP=$(ip -4 -o addr show vmbr0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
# ⚠ NOT `hostname -I | awk '{print $1}'`: that lists EVERY address, and since
# the decoder VM moved behind a host-only bridge this host also holds
# 10.99.0.1.  Ordering is not guaranteed, so the panel/summary could announce
# the management /30 -- an address reachable only from the VM -- as the
# station's address (rob, 2026-09-21: "it was using 10.99.0 ... I think you
# need to exclude 10.99").  Ask vmbr0 directly, and fall back excluding it.
[ -n "$HOSTIP" ] || HOSTIP=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -vE '^(10\.99\.0\.|127\.)' | head -1)
SINK_NOTE=""
[ -n "${UPLOADS_TOML:-}" ] && SINK_NOTE=" Site sink switch: off — the station records and decodes but sends
            no data; only its heartbeat goes out.  Check reception and
            identity, then run in the VM: smd sink upload"
SUMMARY=$(cat <<SEOF
──────────────────────────────────────────────────────
 Sigmond station configured: $REPORTER @ $GRID
 Host (Proxmox):
   login:   root — password set at install (image default:
            hamsci-sigmond — CHANGE IT: run 'passwd')
   ssh:     ssh root@${HOSTIP:-<host-ip>}
   web GUI: https://${HOSTIP:-<host-ip>}:8006
 Decoder VM $VMID:
   IP:      ${VMIP:-none yet — check: qm guest exec $VMID -- ip -4 -br addr}
   login:   sigmond or hamsci — SAME password as this host's root
            (ONE password per appliance; remote root ssh is disabled)
   shell:   sigmond-vm   ← run this on the host: finds the VM's
            current IP and logs you in as sigmond ($SSH_STATE)
   ssh:     ssh sigmond@${VMIP:-<vm-ip>}   (from other machines;
            sigmond-vm --ip prints the current address)
   console: qm terminal $VMID  (Ctrl+O exits; sigmond or root)
 SDR/radiod: $RADIOD_STATE
 RAC:       $RAC_STATE
 Switch:    $PAIR_STATE
 PSWS:      $PSWS_STATE
$SINK_NOTE
 Rerun wizard:  sigmond-setup --reconfigure
 This summary is saved in /root/sigmond-setup-summary.txt
──────────────────────────────────────────────────────
SEOF
)
echo "$SUMMARY" | tee -a "$LOG"
echo "$SUMMARY" > /root/sigmond-setup-summary.txt
# The persistent, boot-surviving login panel is sigmond-issue's job
# (pvebanner rewrites /etc/issue every boot and DHCP addresses go stale —
# it regenerates with live IPs).  Fall back to the old one-shot append on
# hosts that don't have it yet.
if [ -x /usr/local/sbin/sigmond-issue ]; then
    SIGMOND_VMID="$VMID" /usr/local/sbin/sigmond-issue || true
else
    for f in /etc/issue /etc/motd; do
        sed -i '/^─* Sigmond setup ─*$/,/^─* end Sigmond setup ─*$/d' "$f" 2>/dev/null
        { echo "────── Sigmond setup ──────"
          echo "$SUMMARY"
          echo "────── end Sigmond setup ──────"; } >> "$f"
    done
fi
echo ""
rd -r -p "Press Enter to finish (this summary stays on the login screen)... " _ 2>/dev/null || true
exit 0

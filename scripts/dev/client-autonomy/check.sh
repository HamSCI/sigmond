#!/usr/bin/env bash
# check.sh [--keep] <client> [<client> ...]
#
# Install each HamSCI client into a clean Debian 13 container with systemd
# and no sigmond, then report what works.  See README.md.
#
# Exit 0 when the run reached the end, whatever the clients did.  Non-zero
# only when the harness itself failed (no podman, image build failed,
# container would not start).
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOS="${REPOS:-$HOME/hamsci/repos}"
REPORT="${REPORT:-$HOME/hamsci/tasks/2026-10-29-client-autonomy-report.md}"
IMAGE="hamsci-autonomy:trixie"
SETTLE="${SETTLE:-30}"           # seconds between starting units and judging them
INSTALL_TIMEOUT="${INSTALL_TIMEOUT:-1800}"
INSTANCE="autonomy"
# Optional: UV_PYTHON_INSTALL_DIR=/opt/uv/python makes the installers put uv's
# interpreter where service users can read it (see README, "second pass").
PYDIR="${UV_PYTHON_INSTALL_DIR:-}"
LOGDIR="${REPORT%.md}-logs"

KEEP=0
CLIENTS=()
for a in "$@"; do
    case "$a" in
        --keep) KEEP=1 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) CLIENTS+=("$a") ;;
    esac
done
[[ ${#CLIENTS[@]} -gt 0 ]] || { echo "usage: $0 [--keep] <client> ..." >&2; exit 2; }
command -v podman >/dev/null || { echo "check.sh: podman not found" >&2; exit 3; }
command -v systemd-run >/dev/null || { echo "check.sh: systemd-run not found" >&2; exit 3; }

# Rootless podman cannot start systemd as PID 1 from a plain login shell:
# the shell's cgroup belongs to root, so crun cannot create the container's
# cgroup inside it ("Permission denied", or exit 255 with no output).  The
# user manager's own cgroup (user@UID.service) is delegated to us, so run
# every podman call as a transient user scope that lives there.  The
# "no systemd user session" warnings that podman prints are noise; drop them.
# --cgroup-manager=cgroupfs: with the default (systemd) manager, `podman build`
# RUN steps fail with "sd-bus call: Interactive authentication required".
pd() {
    systemd-run --user --scope --quiet podman --cgroup-manager=cgroupfs "$@" 2> >(grep -v 'level=warning' >&2)
}

# --- per-client facts the installers do not let us discover -----------------
# Prints: <installer relative to checkout>|<installer args>|<venv dir>|<note>
spec() {
    case "$1" in
        wspr-recorder)    echo "install.sh||/opt/git/sigmond/wspr-recorder/venv|" ;;
        psk-recorder)     echo "scripts/install.sh|--yes|/opt/git/sigmond/psk-recorder/venv|" ;;
        meteor-scatter)   echo "scripts/install.sh|--yes|/opt/git/sigmond/meteor-scatter/venv|" ;;
        mag-recorder)     echo "install.sh||/opt/git/sigmond/mag-recorder/venv|" ;;
        hf-timestd)       echo "scripts/install.sh|--yes|/opt/git/sigmond/hf-timestd/venv|" ;;
        codar-sounder)    echo "scripts/install.sh|--yes|/opt/git/sigmond/codar-sounder/venv|dormant by design" ;;
        hf-tec)           echo "scripts/install.sh||/opt/hf-tec/venv|dormant by design" ;;
        superdarn-sounder) echo "scripts/install.sh||/opt/git/sigmond/superdarn-sounder/venv|" ;;
        hamsci-physics)   echo "DEPLOY_BUILD||/opt/git/sigmond/hamsci-physics/venv|no installer: harness ran deploy.toml [build].steps after apt-installing pkg-config + libsystemd-dev (undeclared build deps)" ;;
        gpsdo-monitor)    echo "install.sh||/opt/gpsdo-monitor/venv|" ;;
        hs-uploader)      echo "install.sh||/opt/hs-uploader/venv|" ;;
        *) echo "" ;;
    esac
}

# Siblings named under [tool.uv.sources] (path = "../<name>").
siblings() {
    python3 - "$REPOS/$1/pyproject.toml" <<'PY'
import sys, tomllib, os
try:
    d = tomllib.load(open(sys.argv[1], "rb"))
except FileNotFoundError:
    sys.exit(0)
for v in d.get("tool", {}).get("uv", {}).get("sources", {}).values():
    if isinstance(v, dict) and "path" in v:
        print(os.path.basename(v["path"].rstrip("/")))
PY
}

# Units the client names: deploy.toml [systemd] units, templated_units
# (instance "autonomy") and optional_units.  hs-uploader has no deploy.toml.
unit_list() {
    if [[ "$1" == hs-uploader ]]; then echo "hs-uploader.service"; return; fi
    python3 - "$REPOS/$1/deploy.toml" "$INSTANCE" <<'PY'
import sys, tomllib
d = tomllib.load(open(sys.argv[1], "rb")).get("systemd", {})
inst = sys.argv[2]
# a template (name@.service) needs an instance wherever it is listed
for u in d.get("units", []) + d.get("optional_units", []) + d.get("templated_units", []):
    print(u.replace("@.", "@" + inst + "."))
PY
}

# --- image ------------------------------------------------------------------
if ! pd image exists "$IMAGE"; then
    echo "check.sh: building $IMAGE" >&2
    pd build -q -t "$IMAGE" -f "$HERE/Containerfile" "$HERE" >/dev/null \
        || { echo "check.sh: image build failed" >&2; exit 4; }
fi

mkdir -p "$(dirname "$REPORT")" "$LOGDIR"
if [[ ! -s "$REPORT" ]]; then
    {
        echo "| client | install | sigmond in venv | writer | units | notes |"
        echo "|---|---|---|---|---|---|"
    } >"$REPORT"
fi

CT=""
cleanup() { [[ -n "$CT" && $KEEP -eq 0 ]] && pd rm -f -t 0 "$CT" >/dev/null 2>&1; CT=""; }
trap cleanup EXIT

cx() { pd exec "$CT" "$@"; }   # run in the container

# probe_as <user|"">: run the writer probe in the client's venv as that user.
probe_as() {
    local who="$1" runner="bash -c"
    [[ -n "$who" ]] && runner="runuser -u $who -- bash -c"
    pd exec -i "$CT" $runner "cd / && $venv/bin/python -" 2>&1 <<'PY' | tr '\n' ';' | sed 's/;$//; s/|/\\|/g'
try:
    import hs_uploader
except Exception as e:
    print("hs_uploader import: NO (%s: %s)" % (type(e).__name__, e)); raise SystemExit
print("hs_uploader import: yes")
try:
    from hs_uploader.sink import Writer, sink_path
    w = Writer.from_env(table="probe", mode="probe")
    noop = w.is_noop          # a property in hs-uploader; the brief wrote a call
    noop = noop() if callable(noop) else noop
    print("sink_path=%s noop=%s" % (sink_path(), noop))
except Exception as e:
    print("writer probe FAILED (%s: %s)" % (type(e).__name__, e))
PY
}

check_one() {
    local c="$1" s inst args venv note
    s="$(spec "$c")"
    [[ -n "$s" && -d "$REPOS/$c" ]] || { echo "check.sh: unknown client $c" >&2; return 1; }
    IFS='|' read -r inst args venv note <<<"$s"

    CT="autonomy-$c-$$"
    # --cap-add=SYS_ADMIN: hardened units (ProtectSystem, PrivateTmp, ...) make
    # systemd keep CAP_SYS_ADMIN across the User= switch; without it they die
    # at step USER (217) -- a container limit, not a client fault.
    # 2>&1: a kept container must not hold the caller's stderr pipe open.
    pd run -d --name "$CT" --systemd=always --cap-add=SYS_ADMIN "$IMAGE" >/dev/null 2>&1 || return 1
    local i
    for i in $(seq 1 30); do
        [[ "$(cx systemctl is-system-running 2>/dev/null)" =~ ^(running|degraded)$ ]] && break
        sleep 1
    done
    cx systemctl is-system-running >/dev/null 2>&1 || true

    # copy the LOCAL checkouts (they hold unpushed commits): the client and its
    # siblings, never sigmond.  No .git, no venvs.
    local n
    for n in "$c" $(siblings "$c"); do
        [[ "$n" == sigmond ]] && continue
        [[ -d "$REPOS/$n" ]] || { echo "check.sh: missing sibling $REPOS/$n" >&2; continue; }
        cx mkdir -p /opt/git/sigmond
        tar -C "$REPOS" --exclude=.git --exclude=.venv --exclude=venv \
            --exclude=__pycache__ --exclude=node_modules --exclude='*.egg-info' \
            -cf - "$n" | pd exec -i "$CT" tar -C /opt/git/sigmond -xf -
    done

    # run the installer
    local rc tail="$LOGDIR/$c.install-tail.txt"
    if [[ "$inst" == DEPLOY_BUILD ]]; then
        # No installer: run the deploy.toml [build].steps, with the uv-managed
        # CPython 3.11 they name provisioned first (sigmond would have done it; in
        # pass 1, with no UV_PYTHON_INSTALL_DIR, uv installs it under /root and the
        # /opt/uv/python path the steps name stays absent, as shipped), and
        # the apt build deps of systemd-python (nothing declares them).
        cx bash -c 'apt-get install -y --no-install-recommends pkg-config libsystemd-dev
                    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
                    '"${PYDIR:+export UV_PYTHON_INSTALL_DIR=$PYDIR; install -d -m 0755 $PYDIR; }"'/usr/local/bin/uv python install 3.11' \
            >"$LOGDIR/$c.install.log" 2>&1
        python3 - "$REPOS/$c/deploy.toml" >"$LOGDIR/$c.steps.sh" <<'PY'
import sys, tomllib
print("set -e")
for s in tomllib.load(open(sys.argv[1], "rb"))["build"]["steps"]:
    print(s)
PY
        pd exec -i "$CT" bash -c "cd /opt/git/sigmond/$c && timeout $INSTALL_TIMEOUT bash -x /dev/stdin" \
            <"$LOGDIR/$c.steps.sh" >>"$LOGDIR/$c.install.log" 2>&1
        rc=$?
    else
        local pre=""
        [[ -n "$PYDIR" ]] && pre="export UV_PYTHON_INSTALL_DIR=$PYDIR; install -d -m 0755 $PYDIR; "
        cx bash -c "${pre}cd /opt/git/sigmond/$c && timeout $INSTALL_TIMEOUT ./$inst $args </dev/null >/root/install.log 2>&1"
        rc=$?
        if [[ "$c" == hf-timestd ]]; then
            # --yes without a config defers the wizard and stops.  Answer the
            # wizard through its documented env bag, then run the installer again.
            cx bash -c "${pre}cd /opt/git/sigmond/$c && STATION_CALL=N0CALL STATION_GRID=EM38 \
                SIGMOND_RADIOD_STATUS=radiod.local STATION_RX888_ADC_HZ=64800000 \
                ./scripts/setup-station.sh --config /etc/hf-timestd/timestd-config.toml --non-interactive \
                </dev/null >>/root/install.log 2>&1; \
                timeout $INSTALL_TIMEOUT ./$inst $args </dev/null >>/root/install.log 2>&1"
            rc=$?
        fi
        cx cat /root/install.log >"$LOGDIR/$c.install.log" 2>&1
    fi
    tail -n 30 "$LOGDIR/$c.install.log" >"$tail"

    # probes inside the client's venv, from /.  Once as root, once as the user
    # that runs the service (the first User= in the client's unit files).
    local sig="n/a (no venv)" writer="n/a (no venv)" pyt="" svc="" slib1
    slib1="$(cx bash -c 'test ! -e /var/lib/sigmond && echo absent || echo PRESENT')"
    svc="$(cx bash -c "grep -hs '^User=' /opt/git/sigmond/$c/systemd/*.service /opt/git/sigmond/$c/deploy/*.service | head -1 | cut -d= -f2")"
    if cx test -x "$venv/bin/python" || cx test -L "$venv/bin/python"; then
        pyt="$(cx readlink -f "$venv/bin/python" 2>/dev/null)"
        cx bash -c "cd / && $venv/bin/python -c 'import sigmond' >/dev/null 2>&1" \
            && sig="yes (UNEXPECTED)" || sig="no"
        local root_w svc_w
        root_w="$(probe_as "")"
        if [[ -z "$svc" ]]; then svc_w="no service user (units run as root)"
        elif ! cx id "$svc" >/dev/null 2>&1; then svc_w="service user $svc not created by install"
        else svc_w="$(probe_as "$svc")"; fi
        writer="root: $root_w<br>${svc:+$svc: }$svc_w"
    fi

    # units: enable --now each, wait, judge
    local units ucell="" u note2="" ok
    units="$(unit_list "$c")"
    for u in $units; do
        cx systemctl enable --now "$u" >"$LOGDIR/$c.enable-$u.txt" 2>&1
    done
    sleep "$SETTLE"
    for u in $units; do
        local act load res st sub
        act="$(cx systemctl is-active "$u" 2>&1 | head -1)"
        load="$(cx systemctl show -p LoadState --value "$u" 2>&1)"
        res="$(cx systemctl show -p Result --value "$u" 2>&1)"
        st="$(cx systemctl show -p ExecMainStatus --value "$u" 2>&1)"
        if [[ "$act" == activating && "$st" == 0 ]]; then
            # in auto-restart systemd clears ExecMainStatus; the journal keeps it
            st="$(cx journalctl -u "$u" --no-pager -n 60 2>/dev/null | grep -o 'status=[0-9]*/' | tail -1 | tr -dc 0-9)"
        fi
        if [[ "$load" != not-found && "$act" != active ]]; then
            cx journalctl -u "$u" --no-pager -n 20 >"$LOGDIR/$c.journal-$u.txt" 2>&1
            cx systemctl show -p ConditionResult,ExecMainCode,ActiveState,SubState "$u" >>"$LOGDIR/$c.journal-$u.txt" 2>&1
        fi
        if [[ "$load" == not-found ]]; then
            ucell+="$u: not installed<br>"
        elif [[ "$act" == active ]]; then
            ucell+="$u: active<br>"
        else
            ucell+="$u: $act (Result=$res, status=$st)<br>"
            [[ "$res" == exit-code && "$st" == 226 ]] && note2+=" $u 226 = missing path (namespace)."
            [[ "$st" == 203 ]] && note2+=" $u 203 = cannot exec the binary (see journal)."
            [[ "$st" == 217 ]] && note2+=" $u 217 = cannot switch to User= (container limit?)."
        fi
    done
    ucell="${ucell%<br>}"
    [[ -n "$ucell" ]] || ucell="(none named)"

    local slib2; slib2="$(cx bash -c 'test ! -e /var/lib/sigmond && echo absent || echo PRESENT')"
    note="${note}${note:+. }/var/lib/sigmond: $slib1 after install, $slib2 after units${pyt:+. venv python -> $pyt}"
    [[ -n "$PYDIR" ]] && note="${note}${note:+. }UV_PYTHON_INSTALL_DIR=$PYDIR"
    local row="| $c | $rc | $sig | $writer | $ucell | ${note}${note:+.}${note2} |"
    echo "$row"
    echo "$row" >>"$REPORT"
    cleanup
}

for c in "${CLIENTS[@]}"; do
    check_one "$c" || { echo "check.sh: harness failure on $c" >&2; cleanup; exit 5; }
done
exit 0

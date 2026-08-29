#!/usr/bin/env bash
# install.sh — Bootstrap Sigmond (Dr. SigMonD) on any Linux host.
#
# Recommended install:
#
#   sudo mkdir -p /opt/git/sigmond
#   sudo chown $USER /opt/git/sigmond
#   git clone https://github.com/HamSCI/sigmond /opt/git/sigmond/sigmond
#   cd /opt/git/sigmond/sigmond
#   ./install.sh
#
# Sigmond installs at /opt/git/sigmond/sigmond/, peer to its managed
# components (hf-timestd, ka9q-python, etc., all of
# which live at /opt/git/sigmond/<name>/).  This script will refuse to
# run from any other location.
#
# What this script does:
#   1. Validates the canonical install path
#   2. Creates the `sigmond` system user + group (owns /opt/git/sigmond)
#   3. Verifies sudo access
#   4. Installs git and Python 3.11+ if missing
#   5. Creates FHS directories (/etc/sigmond, /var/lib/sigmond, etc.)
#   6. Sets ownership of /opt/git/sigmond to sigmond:sigmond + setgid on
#      directories so future writes inherit the group
#   7. Adds the invoking user to the sigmond group (so they can edit
#      /opt/git/sigmond/* as themselves)
#   8. Writes a default /etc/sigmond/topology.toml (all components off)
#   9. Reads catalog from repo etc/catalog.toml (sparse overlay; any
#      /etc/sigmond/catalog.toml holds host-specific overrides only)
#  10. Builds /opt/git/sigmond/sigmond/venv with sigmond[tui] (Textual + Rich)
#  11. Symlinks bin/smd into /usr/local/bin/smd (on every user's PATH)
#
# After this script completes, run:
#   smd install               — CLI: install all catalog components
#   smd install wspr-recorder — CLI: install one component
#   smd tui                   — TUI: browse and install components
#
# Note: the sigmond group membership applies to sessions started AFTER
# install.sh.  Open a new shell (or `newgrp sigmond`) before editing
# files in /opt/git/sigmond as yourself.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANONICAL_REPO="/opt/git/sigmond/sigmond"
SMD_BIN="$REPO_DIR/bin/smd"
VENV_DIR="/opt/git/sigmond/sigmond/venv"
# /usr/local/bin (not /usr/local/sbin) so smd is on every user's PATH out of
# the box.  smd self-elevates per-operation via sudo (see _run sudo=True in
# bin/smd), so non-root users get read-only verbs for free and a sudo prompt
# only when a verb actually mutates state.
INSTALL_SMD="/usr/local/bin/smd"
LEGACY_INSTALL_SMD="/usr/local/sbin/smd"

# ─── terminal helpers ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info() { echo -e "${CYAN}[sigmond]${NC} $*"; }
ok()   { echo -e "${GREEN}[  ok  ]${NC} $*"; }
warn() { echo -e "${YELLOW}[ warn ]${NC} $*"; }
die()  { echo -e "${RED}[error ]${NC} $*" >&2; exit 1; }

# ─── sudo / passwordless-sudo bootstrap ──────────────────────────────────────
# install.sh and the installed `smd` CLI run many commands under sudo.  We
# set up passwordless sudo once so neither prompts mid-install or
# mid-operation, and so the script can run unattended (incl. from Claude
# Code and other non-TTY contexts) on subsequent invocations.
#
# Three cases:
#   1. Running as root              → no sudo needed; SUDO="".
#   2. Passwordless sudo already on → SUDO="sudo"; continue.
#   3. Need to bootstrap it         → check sudo group membership; if the
#      invoking user is in the group, prompt for password once and write a
#      sudoers drop-in.  Otherwise tell them how to gain sudo first and exit.
#
# To revert later: sudo rm /etc/sudoers.d/sigmond-nopasswd

INVOKER="${SUDO_USER:-${USER:-$(id -un)}}"
SUDOERS_DROPIN="/etc/sudoers.d/sigmond-nopasswd"

if [[ $EUID -eq 0 ]]; then
    SUDO=""
    ok "running as root — sudo not required"
    # Invoked via `sudo ./install.sh`: the invoking user still needs the
    # NOPASSWD drop-in for every later `smd` operation.  We are already
    # root, so write it now — no prompt needed.
    if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" \
          && ! -f "$SUDOERS_DROPIN" ]]; then
        _tmp="$(mktemp /etc/sudoers.d/.sigmond-nopasswd.XXXXXX)" \
            || die "couldn't create temp sudoers file"
        printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$SUDO_USER" > "$_tmp"
        chmod 440 "$_tmp"
        if visudo -c -f "$_tmp" >/dev/null 2>&1; then
            mv "$_tmp" "$SUDOERS_DROPIN"
            ok "passwordless sudo configured for '$SUDO_USER' at $SUDOERS_DROPIN"
        else
            rm -f "$_tmp"
            warn "sudoers validation failed — drop-in not installed"
        fi
    fi
# `-k` makes this probe ignore cached sudo credentials: a bare `sudo -n true`
# also succeeds for ~15 min after any earlier sudo command (e.g. the
# runbook's own `sudo mkdir/chown/git clone` steps), which used to skip the
# drop-in and leave every later `smd` operation prompting for a password.
elif sudo -k -n true 2>/dev/null; then
    SUDO="sudo"
    ok "passwordless sudo already active for '$INVOKER'"
else
    info "Passwordless sudo is not configured for '$INVOKER'."

    # Check sudo-granting group membership: sudo (Debian/Ubuntu),
    # wheel (RHEL/Fedora/Arch), admin (some derivatives).
    if ! id -nG "$INVOKER" 2>/dev/null | tr ' ' '\n' | grep -Eqx 'sudo|wheel|admin'; then
        cat >&2 <<EOF

${RED}[error]${NC} User '$INVOKER' is not in the sudo (or wheel) group.

Sigmond needs sudo to install system packages, create users, write to
/etc and /opt, and manage systemd services.

  ${BOLD}To fix:${NC}
    1. Log in as root (or ask your sysadmin) and run:
         ${CYAN}usermod -aG sudo $INVOKER${NC}
    2. Log out of every session for '$INVOKER' and log back in
       (group membership only applies to new sessions).
    3. Re-run this installer:
         ${CYAN}cd $REPO_DIR && ./install.sh${NC}

EOF
        exit 1
    fi

    # In sudo group: need a TTY to type the password once.
    if [[ ! -e /dev/tty ]]; then
        die "no TTY available — run install.sh from an interactive terminal
       (real SSH session or local console) so sudo can prompt once.
       After this one-time setup, future runs won't need a TTY."
    fi

    info "Will create $SUDOERS_DROPIN granting passwordless sudo to '$INVOKER'."
    info "You'll be prompted for your password once."
    printf "%b[?]%b Continue? [Y/n]: " "$YELLOW" "$NC" >/dev/tty
    read -r _resp </dev/tty || _resp="n"
    if [[ "$_resp" =~ ^[Nn] ]]; then
        die "aborted — re-run install.sh when ready."
    fi

    # Acquire credentials (one prompt), then write/validate/install the drop-in.
    sudo -v || die "sudo authentication failed"
    _tmp="$(sudo mktemp /etc/sudoers.d/.sigmond-nopasswd.XXXXXX)" \
        || die "couldn't create temp sudoers file"
    printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$INVOKER" | sudo tee "$_tmp" >/dev/null
    sudo chmod 440 "$_tmp"
    if ! sudo visudo -c -f "$_tmp" >/dev/null 2>&1; then
        sudo rm -f "$_tmp"
        die "sudoers validation failed — drop-in not installed"
    fi
    sudo mv "$_tmp" "$SUDOERS_DROPIN"

    if ! sudo -k -n true 2>/dev/null; then
        die "drop-in installed but passwordless sudo still inactive —
       check: sudo cat $SUDOERS_DROPIN  &&  sudo -nl"
    fi

    ok "passwordless sudo configured at $SUDOERS_DROPIN"
    SUDO="sudo"
fi
unset _resp _tmp

# ─── Proxmox VM detection (auto-skip when not applicable) ────────────────────
# Running in a KVM guest with no prior install state? Offer to run the
# Proxmox host passthrough bootstrap first. Bare-metal hosts (virt=none)
# and resume runs (state file present, or env var set, or no TTY) skip
# this entirely — existing flow is untouched.
_virt="$(systemd-detect-virt 2>/dev/null || echo none)"
_state_file="/etc/sigmond/install-state.env"
# -e /dev/tty is true even with no controlling terminal (the node always
# exists; opening it then fails with ENXIO — e.g. ssh without -t).  Test
# openability, not existence.
_tty_ok=0
{ : </dev/tty; } 2>/dev/null && _tty_ok=1
if [[ "$_virt" == "kvm" \
      && -z "${SIGMOND_SKIP_PROXMOX_PROMPT:-}" \
      && ! -f "$_state_file" \
      && "$_tty_ok" == 1 \
      && -x "$REPO_DIR/scripts/proxmox/bootstrap.sh" ]]; then
    info "Detected KVM guest. Sigmond can configure the Proxmox host's PCIe USB"
    info "passthrough, CPU isolation, and vfio binding — required for full"
    info "bare-metal SDR performance with RX-888 or similar."
    printf '%b[?]%b Run Proxmox passthrough setup first? [y/N]: ' "$YELLOW" "$NC" >/dev/tty
    read -r _resp </dev/tty || _resp=""
    if [[ "$_resp" =~ ^[Yy] ]]; then
        info "handing off to scripts/proxmox/bootstrap.sh…"
        exec bash "$REPO_DIR/scripts/proxmox/bootstrap.sh"
    fi
    info "skipping Proxmox setup — proceeding with bare-metal install."
fi
unset _virt _state_file _resp _tty_ok

echo -e "${BOLD}"
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  Dr. SigMonD — HamSCI SDR suite manager     │"
echo "  │  'Zo... ven did your signals first propagate?│"
echo "  └─────────────────────────────────────────────┘"
echo -e "${NC}"

# ─── canonical-path enforcement (auto-relocate if needed) ───────────────────
# Sigmond's source-of-truth lives at /opt/git/sigmond/sigmond/, peer to the
# components it manages.  If invoked from anywhere else, relocate the clone
# in place and re-exec from the canonical path — sudo was acquired above so
# this should not re-prompt.
if [[ "$REPO_DIR" != "$CANONICAL_REPO" ]]; then
    info "Repo is at $REPO_DIR"
    info "Canonical location is $CANONICAL_REPO — relocating before install."
    if [[ -d "$CANONICAL_REPO" && -n "$(ls -A "$CANONICAL_REPO" 2>/dev/null)" ]]; then
        die "$CANONICAL_REPO already exists and is non-empty.
       Inspect it and remove (or rename) it, then re-run install.sh:
         sudo ls -la $CANONICAL_REPO"
    fi
    $SUDO mkdir -p "$(dirname "$CANONICAL_REPO")"
    [[ -d "$CANONICAL_REPO" ]] && $SUDO rmdir "$CANONICAL_REPO"
    $SUDO mv "$REPO_DIR" "$CANONICAL_REPO"
    ok "Relocated → $CANONICAL_REPO; re-execing install.sh"
    exec "$CANONICAL_REPO/install.sh" "$@"
fi

# ─── ensure ka9q-python is at the canonical sibling location ────────────────
# pyproject.toml declares  ka9q-python = { path = "../ka9q-python" }, which
# resolves to /opt/git/sigmond/ka9q-python.  If it's not there, relocate from
# common alternate locations or clone from upstream so the venv install can
# resolve the path-based dependency.
KA9Q_CANONICAL="/opt/git/sigmond/ka9q-python"
KA9Q_REPO_URL="https://github.com/HamSCI/ka9q-python"

if [[ ! -f "$KA9Q_CANONICAL/pyproject.toml" ]]; then
    info "ka9q-python not at $KA9Q_CANONICAL — searching common locations"
    _ka9q_src=""
    for _candidate in \
        "/home/$INVOKER/ka9q-python" \
        "/home/$INVOKER/git/ka9q-python" \
        "/opt/git/ka9q-python"; do
        if [[ -f "$_candidate/pyproject.toml" ]]; then
            _ka9q_src="$_candidate"
            break
        fi
    done

    if [[ -n "$_ka9q_src" ]]; then
        info "Found at $_ka9q_src — relocating to $KA9Q_CANONICAL"
        if [[ -d "$KA9Q_CANONICAL" && -n "$(ls -A "$KA9Q_CANONICAL" 2>/dev/null)" ]]; then
            die "$KA9Q_CANONICAL exists and is non-empty — inspect and remove first."
        fi
        [[ -d "$KA9Q_CANONICAL" ]] && $SUDO rmdir "$KA9Q_CANONICAL"
        $SUDO mv "$_ka9q_src" "$KA9Q_CANONICAL"
        ok "ka9q-python relocated to $KA9Q_CANONICAL"
    else
        info "ka9q-python not found locally — cloning from $KA9Q_REPO_URL"
        $SUDO git clone "$KA9Q_REPO_URL" "$KA9Q_CANONICAL" \
            || die "failed to clone ka9q-python"
        ok "ka9q-python cloned to $KA9Q_CANONICAL"
    fi
    unset _ka9q_src _candidate
fi

# ─── ensure sibling Python libraries are at their canonical locations ───────
# callhash (wspr-recorder + psk-recorder) and hs-uploader (mag-recorder) are
# path-based editable siblings declared in those clients' pyproject.toml.
# Sigmond clones them on demand at client-install time, but front-loading the
# pure-python substrate here makes the later client installs robust — a
# missing callhash sibling was a documented greenfield uv-sync failure.
# Best-effort: a failure is non-fatal since the client install pulls it anyway.
for _lib in callhash hs-uploader; do
    _lib_dir="/opt/git/sigmond/$_lib"
    if [[ ! -f "$_lib_dir/pyproject.toml" && ! -d "$_lib_dir/.git" ]]; then
        info "Cloning $_lib substrate → $_lib_dir"
        if $SUDO git clone "https://github.com/HamSCI/$_lib" "$_lib_dir"; then
            ok "$_lib cloned"
        else
            info "$_lib clone skipped (will be pulled at client install time)"
        fi
    fi
done
unset _lib _lib_dir

# ─── sigmond user + group ────────────────────────────────────────────────────
# A single non-human user `sigmond` owns /opt/git/sigmond/*.  Humans (Rob,
# Michael, anyone collaborating) become members of the `sigmond` group and
# edit as themselves, with setgid keeping group ownership consistent.
if ! getent passwd sigmond >/dev/null 2>&1; then
    info "Creating system user/group: sigmond"
    $SUDO useradd --system --user-group --home-dir /opt/git/sigmond \
                  --shell /usr/sbin/nologin sigmond
fi
ok "sigmond user/group ready: $(getent passwd sigmond | cut -d: -f1,3,4,7)"

# Add the invoking user to the sigmond group so they can edit
# /opt/git/sigmond/* as themselves.  $SUDO_USER is set when running via
# sudo; falls back to $USER for direct-as-root invocations.
INVOKER="${SUDO_USER:-${USER:-}}"
if [[ -n "$INVOKER" && "$INVOKER" != "root" ]]; then
    if ! id -nG "$INVOKER" 2>/dev/null | tr ' ' '\n' | grep -qx sigmond; then
        info "Adding $INVOKER to sigmond group"
        $SUDO usermod -aG sigmond "$INVOKER"
        warn "$INVOKER must log out and back in (or 'newgrp sigmond') for group membership to take effect"
    else
        ok "$INVOKER is already in the sigmond group"
    fi
fi

# ─── /opt/git/sigmond/ ownership + setgid ───────────────────────────────────
# Make /opt/git/sigmond/* a group-shared tree:  files are sigmond:sigmond,
# group has read+write, directories have setgid so newly-created files
# inherit the sigmond group automatically.
info "Setting /opt/git/sigmond ownership: sigmond:sigmond + setgid"
$SUDO chown -R sigmond:sigmond /opt/git/sigmond
$SUDO chmod -R g+rwX /opt/git/sigmond
$SUDO find /opt/git/sigmond -type d -exec chmod g+s {} +
ok "/opt/git/sigmond ownership and permissions set"

# ─── git safe.directory for /opt/git/sigmond/* ──────────────────────────────
# When sigmond's UID doesn't match the human's UID (the common case — sigmond
# is a system user, humans are uid 1000+), git refuses to operate with a
# "dubious ownership" error.  System-wide safe.directory entries scoped to
# /opt/git/sigmond/* let any user in the sigmond group use git there without
# per-user config.  We enumerate (rather than use `*`) so the trust scope is
# bounded.
info "Adding system-wide git safe.directory entries for /opt/git/sigmond/*"
for _repo_dir in /opt/git/sigmond/*/; do
    _repo_dir="${_repo_dir%/}"  # strip trailing slash
    if ! $SUDO git config --system --get-all safe.directory 2>/dev/null \
            | grep -Fxq "$_repo_dir"; then
        $SUDO git config --system --add safe.directory "$_repo_dir"
    fi
done
ok "git safe.directory entries set"

# ─── package manager detection ────────────────────────────────────────────────
_PKG_MGR=""
if   command -v apt-get &>/dev/null; then _PKG_MGR="apt"
elif command -v dnf     &>/dev/null; then _PKG_MGR="dnf"
elif command -v yum     &>/dev/null; then _PKG_MGR="yum"
elif command -v pacman  &>/dev/null; then _PKG_MGR="pacman"
fi

_pkg_install() {
    case "$_PKG_MGR" in
        apt)    $SUDO apt-get install -y "$@" ;;
        dnf)    $SUDO dnf install -y "$@" ;;
        yum)    $SUDO yum install -y "$@" ;;
        pacman) $SUDO pacman -S --noconfirm "$@" ;;
        *)      die "Cannot install $* — no known package manager found.  Install manually and re-run." ;;
    esac
}

# ─── git ──────────────────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
    info "Installing git…"
    _pkg_install git
fi
ok "git: $(git --version)"

# ─── core operator tools (curl, tmux, btop, vim) ─────────────────────────────
# Minimal server images (Ubuntu 24.04 among them) ship without curl and with
# only vim-tiny.  curl is load-bearing for sigmond (RAC key registration,
# ad-hoc fetches in docs and component installs); tmux, btop, and full vim
# are the fleet-standard session manager, process monitor, and editor every
# operator/support session assumes.  Install any that are missing; a distro
# without one of them (e.g. btop on older releases) degrades to a warning,
# not a failed install.
for _tool in curl tmux btop vim; do
    if command -v "$_tool" &>/dev/null; then
        ok "$_tool: present"
        continue
    fi
    info "Installing $_tool…"
    if _pkg_install "$_tool"; then
        ok "$_tool: installed"
    else
        warn "$_tool could not be installed via $_PKG_MGR — install it manually"
    fi
done
unset _tool

# ─── usbutils (lsusb) ────────────────────────────────────────────────────────
# lsusb is load-bearing for SDR discovery: the bringup preflight and the
# `smd sdr` inventory probe the USB bus via lsusb.  Without it a fresh host
# reports "no RX888/SDR on the USB bus" and bringup hard-aborts even with an
# RX-888 attached (a cold FX3 always enumerates in DFU mode until radiod
# loads firmware, so discovery must work before anything is built).
if command -v lsusb &>/dev/null; then
    ok "lsusb: present"
else
    info "Installing usbutils (lsusb)…"
    if _pkg_install usbutils; then
        ok "usbutils: installed"
    else
        warn "usbutils could not be installed via $_PKG_MGR — SDR discovery will report an empty USB bus until lsusb is available"
    fi
fi

# ─── uhubctl (USB per-port power control) ────────────────────────────────────
# The RX-888 recurrently disappears from the USB bus, and nothing short of a
# power cycle of the SDR itself brings it back — not a radiod restart, not a
# rebind, not a host reboot in every case.  Until now that meant a human at the
# site pulling a cable.  uhubctl drives per-port power switching on any hub
# whose descriptor advertises `ppps`, so a station with the SDR behind such a
# hub can cut and restore VBUS to that port alone and recover unattended.
#
# ⚠ It installs to /usr/sbin/uhubctl, which is NOT on a non-root PATH.  Scripts
# must call the absolute path or run as root; `command -v uhubctl` as an
# ordinary user returns nothing even when the package is present.
#
# Absence degrades to a warning rather than a failed install: a station whose
# SDR sits on a root port, or behind a hub without per-port switching, has
# nothing for uhubctl to drive and loses only the unattended recovery.
if command -v uhubctl &>/dev/null || [ -x /usr/sbin/uhubctl ]; then
    ok "uhubctl: present"
else
    info "Installing uhubctl (USB per-port power control)…"
    if _pkg_install uhubctl; then
        ok "uhubctl: installed"
    else
        warn "uhubctl could not be installed via $_PKG_MGR — USB port power cycling unavailable; an SDR that drops off the bus will need a manual power cycle"
    fi
fi

# ─── health-check prerequisites (sqlite3, lsof, bc) ──────────────────────────
# These are shelled out to by watchdogs and maintenance scripts.  Each one is
# absent from a minimal Debian image and was present on our older hosts only by
# accretion, so a fresh appliance loses the check silently — none of these
# failures announce themselves:
#
#   sqlite3  hf-timestd's pipeline-watchdog measures data freshness with it.
#            Missing, every query fails and the watchdog cannot tell "no rows"
#            from "no sqlite3", reads both as stale, and restarts healthy
#            services every 5 minutes.  Observed on B4 2026-08-07.
#   lsof     guards deletion in cleanup-orphaned-data.sh ("ensure no processes
#            have files open") and the HDF5 in-use check.  Missing, the count
#            comes back 0 while files ARE open, so the guard fails OPEN.
#   bc       shell-side arithmetic in the timestd helper scripts; missing, the
#            substitution yields an empty string rather than an error.
#
# Installing them here is only half the fix: a check whose tool is absent must
# fail loudly rather than fall through to a default.  See
# hf-timestd fix/watchdog-failsafe-sqlite for that half.
for _tool in sqlite3 lsof bc; do
    if command -v "$_tool" &>/dev/null; then
        ok "$_tool: present"
        continue
    fi
    info "Installing $_tool…"
    if _pkg_install "$_tool"; then
        ok "$_tool: installed"
    else
        warn "$_tool could not be installed via $_PKG_MGR — health checks that use it will be degraded; install it manually"
    fi
done
unset _tool

# ─── operator terminal conveniences (tmux mouse, top P column) ───────────────
# Support sessions run inside tmux; mouse mode (scroll, pane select, resize)
# is the fleet convention.  And the fleet debugging habit is `top -H` to watch
# radiod's fft / proc_rx888 threads — seed a toprc that shows the P column
# (Last Used Cpu) right of %CPU, so a thread on the wrong core is visible at
# a glance (the whole CPU-affinity subsystem exists to put them on the right
# ones).  Both are seeded for the INVOKING user *and* the sigmond operator
# account: the installer often runs as root (qm guest exec, firstboot), and
# keying off the invoker alone left the operator without either (AI6VN,
# 2026-08-26).  Append/seed-if-absent only — an operator who changed their
# own config stays in charge of it.
_conv_users="$INVOKER"
id sigmond >/dev/null 2>&1 && [[ "$INVOKER" != sigmond ]] && _conv_users="$_conv_users sigmond"
for _conv_user in $_conv_users; do
    _conv_home="$(getent passwd "$_conv_user" | cut -d: -f6)"
    [[ -n "$_conv_home" && -d "$_conv_home" ]] || continue

    _tmux_conf="$_conv_home/.tmux.conf"
    if ! grep -Eq '^[[:space:]]*set(-option)?[[:space:]]+(-g[[:space:]]+)?mouse[[:space:]]' \
            "$_tmux_conf" 2>/dev/null; then
        {
            echo '# added by sigmond install.sh — tmux mouse support'
            echo 'set -g mouse on'
        } >> "$_tmux_conf"
        # When running as root (sudo ./install.sh) a freshly created file
        # must still belong to the operator.
        [[ $EUID -eq 0 ]] && chown "$_conv_user": "$_tmux_conf" || true
        ok "tmux: enabled mouse support in $_tmux_conf"
    else
        ok "tmux: mouse setting already present in $_tmux_conf"
    fi

    # toprc: seed only when the user has none — top rewrites this file on 'W'
    # and a hand-tuned layout must never be clobbered.  etc/toprc was written
    # by procps-ng 4.x top itself on Debian 13 (fields screen → W), the only
    # portable way to produce one — the fieldscur encoding is version-specific.
    _toprc="$_conv_home/.config/procps/toprc"
    if [[ ! -f "$_toprc" && -f "$REPO_DIR/etc/toprc" ]]; then
        install -D -m 0644 "$REPO_DIR/etc/toprc" "$_toprc"
        [[ $EUID -eq 0 ]] && chown -R "$_conv_user": "$_conv_home/.config/procps" || true
        ok "top: seeded $_toprc (P column right of %CPU)"
    fi
    unset _tmux_conf _toprc
done
unset _conv_user _conv_users _conv_home

# ─── avahi-browse (mDNS discovery) ───────────────────────────────────────────
# sigmond's discovery/mdns.py and ka9q-python's discover_radiod_services
# both shell out to avahi-browse to enumerate radiod instances on the LAN
# (service type `_ka9q-ctl._udp`).  When avahi-browse is missing both probes
# silently return zero hits, which then mis-informs `smd install`'s
# pre-flight check (lib/sigmond/preflight.py) into reporting "no radiod
# on LAN" on a host where several are advertising.  Install the utility
# so discovery works out of the box.
if ! command -v avahi-browse &>/dev/null; then
    info "Installing avahi-browse (for mDNS radiod discovery)…"
    case "$_PKG_MGR" in
        apt)      _pkg_install avahi-utils ;;
        dnf|yum)  _pkg_install avahi-tools ;;
        pacman)   _pkg_install avahi ;;
        *)        warn "no known package providing avahi-browse for this package manager — mDNS discovery will be unavailable" ;;
    esac
fi
if command -v avahi-browse &>/dev/null; then
    ok "avahi-browse: $(avahi-browse --version 2>&1 | head -1)"
fi

# ─── Python 3.11+ ─────────────────────────────────────────────────────────────
PYTHON3=""
for _py in python3.13 python3.12 python3.11 python3; do
    if command -v "$_py" &>/dev/null; then
        if "$_py" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
            PYTHON3="$_py"
            break
        fi
    fi
done

if [[ -z "$PYTHON3" ]]; then
    info "Python 3.11+ not found — installing…"
    case "$_PKG_MGR" in
        apt)
            $SUDO apt-get update -qq
            _pkg_install python3.11 python3.11-venv
            PYTHON3="python3.11"
            ;;
        dnf)
            _pkg_install python3.11
            PYTHON3="python3.11"
            ;;
        *)
            die "Python 3.11+ is required.  Install it and re-run this script."
            ;;
    esac
fi

# Ensure the venv module + ensurepip are present (Debian/Ubuntu split these
# into a per-minor-version sub-package).  `python3 -m venv --help` succeeds
# without ensurepip, so check for ensurepip directly — that's what venv
# creation actually needs.  Sigmond itself uses uv (which doesn't need
# ensurepip), but sibling clients invoke `python3 -m venv` directly and
# fail with a confusing error if the package is missing.
if ! "$PYTHON3" -c 'import ensurepip' &>/dev/null; then
    _pyver=$("$PYTHON3" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    info "Installing python${_pyver}-venv (ensurepip module missing)…"
    case "$_PKG_MGR" in
        apt) _pkg_install "python${_pyver}-venv" ;;
        dnf) _pkg_install python3-venv ;;
        *)   die "python venv module missing — install it for your Python version." ;;
    esac
fi
ok "Python: $($PYTHON3 --version)"

# ─── FHS system directories ───────────────────────────────────────────────────
# /var/lib/sigmond holds the SQLite local sink (sink.db) plus the environment
# cache, lifecycle locks, and net-diag JSON.  Producers run as non-root users
# (pskrec, hf-timestd, etc.) but share the `sigmond` group; mode 2775
# root:sigmond + setgid lets every group member write while keeping the dir
# world-readable so operators can `sqlite3 sink.db` read-only without joining
# the group.  Matches lib/sigmond/storage_migrate.py SINK_DIR_MODE/SINK_GROUP
# — without this match, the producer-side writer falls back to silent noop
# and `smd admin storage migrate-to-sqlite` is the only thing that re-applies the
# perms (it shouldn't be load-bearing for a greenfield install).
#
# /var/log/sigmond stays group-only (2770 sigmond:sigmond) — no need to
# expose logs to non-members.
info "Creating system directories…"
$SUDO install -d -m 755                        /etc/sigmond
$SUDO install -d -m 2775 -o root    -g sigmond /var/lib/sigmond
$SUDO install -d -m 2770 -o sigmond -g sigmond /var/log/sigmond

# Pre-create sink.db so the first producer to flush doesn't end up owning
# it.  Without this, default umask (0022) means the first race-winner's
# UID owns the file mode 0644, and other sigmond-group producers hit
# "attempt to write a readonly database" until a human chmods.  Observed
# on bee1 2026-05-12 — see lib/sigmond/storage_migrate.py:500-512.
if [[ ! -f /var/lib/sigmond/sink.db ]]; then
    $SUDO install -m 0664 -o root -g sigmond /dev/null /var/lib/sigmond/sink.db
    ok "sink.db pre-created at /var/lib/sigmond/sink.db"
fi
# Post-consolidation (2026-05-26) the prod venv lives at
# /opt/git/sigmond/sigmond/venv (inside the repo), so the legacy
# /opt/sigmond/ tree is no longer needed.  Greenfield installs
# never create it; existing hosts can `rm -rf /opt/sigmond` after
# moving the venv into the repo.
ok "System directories ready"

# ─── catalog.toml ─────────────────────────────────────────────────────────────
# Catalog now ships as a sparse-overlay layer: the in-repo etc/catalog.toml
# is read directly by sigmond.catalog.load_catalog() and any
# /etc/sigmond/catalog.toml carries only host-specific overrides.  We
# defer prune-to-minimal until after the venv + smd binary are in place
# (see the "catalog prune" block near the end of this script).  No
# unconditional copy happens here — on a fresh install we'd just be
# writing a file we'd immediately prune to nothing, and on an upgrade
# we'd silently clobber operator overrides.

# ─── fallback lifecycle shims ────────────────────────────────────────────────
# Non-contract upstream components (ka9q-radio, ka9q-web, …) don't carry
# their own deploy.toml.  Sigmond's lib/sigmond/lifecycle.py looks for
# fallback shims at /etc/sigmond/clients/<name>.deploy.toml; ship the
# repo's etc/clients/ directory there so `smd start <component>` can
# discover the systemd units.
if [[ -d "$REPO_DIR/etc/clients" ]]; then
    info "Installing fallback lifecycle shims → /etc/sigmond/clients/"
    $SUDO mkdir -p /etc/sigmond/clients
    $SUDO cp "$REPO_DIR/etc/clients/"*.deploy.toml /etc/sigmond/clients/ 2>/dev/null || true
    ok "fallback shims installed: $(ls /etc/sigmond/clients/ 2>/dev/null | tr '\n' ' ')"
fi

# ─── default topology.toml ────────────────────────────────────────────────────
if [[ ! -f /etc/sigmond/topology.toml ]]; then
    info "Writing default topology → /etc/sigmond/topology.toml"
    $SUDO tee /etc/sigmond/topology.toml >/dev/null <<'TOML'
# /etc/sigmond/topology.toml — which components are enabled on this host.
#
# All components start disabled.  Use  smd tui  (Install screen)
# or  smd install <name>  to enable and install them.

[component.ka9q-radio]
enabled = false
managed = true

[component.hf-timestd]
enabled = false

[component.psk-recorder]
enabled = false

[component.wspr-recorder]
enabled = false
TOML
    ok "topology.toml installed (all components off by default)"
else
    ok "topology.toml already present — not overwritten"
fi

# ─── uv (fast package manager) ───────────────────────────────────────────────
UV=""
if command -v uv &>/dev/null; then
    UV="$(command -v uv)"
    ok "uv $(uv --version) found"
else
    info "Installing uv…"
    # Official uv installer: single static binary, no pip required.
    # UV_INSTALL_DIR=/usr/local/bin puts it system-wide; --no-modify-path
    # skips shell-profile edits since we know the directory is already in PATH.
    _uv_installer=$(mktemp /tmp/uv-install-XXXXXX.sh)
    _downloaded=false
    if command -v curl &>/dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh -o "$_uv_installer" 2>/dev/null \
            && _downloaded=true
    elif command -v wget &>/dev/null; then
        wget -qO "$_uv_installer" https://astral.sh/uv/install.sh 2>/dev/null \
            && _downloaded=true
    fi

    if $_downloaded; then
        # sudo sh -c "VAR=val sh script" avoids sudoers env_reset stripping our vars.
        # UV_NO_MODIFY_PATH=1 skips writing to ~/.bashrc; /usr/local/bin is already in PATH.
        $SUDO sh -c "UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh '$_uv_installer'"
    fi
    rm -f "$_uv_installer"

    if command -v uv &>/dev/null; then
        UV="$(command -v uv)"
        ok "uv installed: $(uv --version)"
    else
        warn "uv install failed — falling back to pip (slower first install)"
    fi
fi

# uv-managed Python install dir.  See scripts/install/ensure_uv.sh
# for the full rationale: uv's default ~/.local/share/uv/python/
# resolves to /root/.local/... under sudo, which non-root service
# users can't traverse.  /opt/uv/python is shared + world-readable.
# Sourced helpers (consumer install.sh files) export the same value;
# the wrapper-via-`env` calls below propagate it past sudoers'
# env_reset.
UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/opt/uv/python}"
$SUDO install -d -m 0755 "$UV_PYTHON_INSTALL_DIR" 2>/dev/null || true

# Helpers that use uv when available, plain pip/venv otherwise.
_venv_create() {
    local target="$1"
    if [[ -n "$UV" ]]; then
        $SUDO env "UV_PYTHON_INSTALL_DIR=$UV_PYTHON_INSTALL_DIR" \
            "$UV" venv --python "$PYTHON3" --clear "$target"
    else
        $SUDO "$PYTHON3" -m venv --clear "$target"
        $SUDO "$target/bin/pip" install --quiet --upgrade pip
    fi
}
_pip_install() {
    local target="$1"; shift
    if [[ -n "$UV" ]]; then
        $SUDO env "UV_PYTHON_INSTALL_DIR=$UV_PYTHON_INSTALL_DIR" \
            "$UV" pip install --quiet --python "$target/bin/python" "$@"
    else
        $SUDO "$target/bin/pip" install --quiet "$@"
    fi
}

# ─── sigmond TUI venv ─────────────────────────────────────────────────────────
info "Building sigmond venv at $VENV_DIR (beside the live one; swapped only after it imports)..."
# sigmond#47: build into $VENV_DIR.new, verify `import sigmond, ka9q`, then
# swap — a failed build (ENOSPC, offline) leaves the live venv untouched
# instead of wiped.  Helper: scripts/venv-atomic.sh.
# shellcheck source=scripts/venv-atomic.sh
source "$REPO_DIR/scripts/venv-atomic.sh"
SUDO="$SUDO" UV="$UV" PYTHON3="$PYTHON3" UV_PYTHON_INSTALL_DIR="$UV_PYTHON_INSTALL_DIR" \
    venv_atomic_install "$VENV_DIR" "sigmond ka9q" -e "$REPO_DIR[tui]" \
    || die "sigmond venv build failed — the previous venv (if any) is still live at $VENV_DIR"
ok "Venv ready at $VENV_DIR"

# ka9q-python was placed at /opt/git/sigmond/ka9q-python near the top of this
# script; uv resolves it via [tool.uv.sources] in pyproject.toml during the
# sigmond[tui] install above, so no separate editable install is needed here.

# ─── sigmond systemd units + helper scripts ─────────────────────────────────
# Ship the unit files that smd's scheduled verbs depend on (storage-trim
# janitors, decode-health collector, gap-hourly block-drop sampler).
# Without these, PSK retention silently fails (sink.db grows unbounded),
# decode trend collection never runs, and the fleet's only honest
# radiod-loss signal stops at whichever single host still has it hand-run.
#
# Per-target trim units are shipped for backwards compatibility; the unified
# sigmond-storage-trim-all.timer covers every retention policy in one pass
# and is the only one enabled here.  Decode-health is installed but not
# enabled — operators turn it on once psk-recorder is producing log lines
# worth scraping.  gap-hourly IS enabled by default (see below) — it's
# gated by ConditionPathExists so it's inert without hf-timestd.
info "Installing sigmond systemd units → /etc/systemd/system/"
for _unit in "$REPO_DIR"/systemd/sigmond-*.service "$REPO_DIR"/systemd/sigmond-*.timer; do
    [[ -f "$_unit" ]] || continue
    $SUDO install -m 0644 "$_unit" /etc/systemd/system/
done
unset _unit
ok "sigmond systemd units installed"

# tmpfiles.d entries.  sigmond has exactly one today: the heartbeat spool
# needs group hsupload + setgid (2775) so hs-uploader, running as its own
# user, can unlink acked ticks that `smd admin heartbeat emit` (root) wrote.
# A plain `install -d` here (like the /var/lib/sigmond dir below) would fail
# once and stay wrong if hs-uploader's own install.sh -- which creates the
# hsupload user/group -- hasn't run yet; systemd-tmpfiles re-applies this
# every boot, so the ownership self-heals in whichever order the two
# installs actually happen.
if [[ -f "$REPO_DIR/systemd/sigmond-heartbeat.conf" ]]; then
    info "Installing sigmond-heartbeat.conf → /etc/tmpfiles.d/"
    $SUDO install -m 0644 "$REPO_DIR/systemd/sigmond-heartbeat.conf" /etc/tmpfiles.d/
    $SUDO systemd-tmpfiles --create /etc/tmpfiles.d/sigmond-heartbeat.conf 2>/dev/null \
        || warn "sigmond-heartbeat.conf: could not create /var/lib/sigmond/heartbeat yet (hsupload group not present?) — self-heals next boot or once hs-uploader is installed"
    ok "sigmond-heartbeat.conf installed"
fi

# Helper script invoked by sigmond-decode-health-collect.service.  Symlinked
# from the repo (same pattern as bin/smd) so a `git pull` updates the script
# without re-running install.sh.
info "Installing sigmond-decode-health-collect → /usr/local/sbin/"
$SUDO chmod a+x "$REPO_DIR/scripts/sigmond-decode-health-collect.py"
$SUDO ln -sf "$REPO_DIR/scripts/sigmond-decode-health-collect.py" \
        /usr/local/sbin/sigmond-decode-health-collect
ok "sigmond-decode-health-collect symlink installed"

# Helper script invoked by sigmond-gap-hourly.service.  Symlinked from the
# repo (same pattern as sigmond-decode-health-collect above) so a `git pull`
# updates the sampler without re-running install.sh.  This promotes B4's
# uncommitted /usr/local/sbin/gap-hourly.sh -- the only durable honest gap
# (radiod block-drop) record in the fleet -- into sigmond proper.
info "Installing sigmond-gap-hourly → /usr/local/sbin/"
$SUDO chmod a+x "$REPO_DIR/scripts/sigmond-gap-hourly"
$SUDO ln -sf "$REPO_DIR/scripts/sigmond-gap-hourly" \
        /usr/local/sbin/sigmond-gap-hourly
ok "sigmond-gap-hourly symlink installed"

# Timing-chain SHM pre-create (docs/timing-chain-architecture.md, step 2): give
# the chrony/gpsd/hf-timestd refclock SHM segments a stable owner+perm before any
# of them start, so a chrony/gpsd/fusion restart can never flip ownership and
# lock a producer out (the cascade that put the GPS reference on internet NTP).
info "Installing sigmond-shm-precreate → /usr/local/sbin/"
$SUDO ln -sf "$REPO_DIR/scripts/sigmond-shm-precreate" /usr/local/sbin/sigmond-shm-precreate
ok "sigmond-shm-precreate symlink installed"

# xhci IRQ pinning (CPU-affinity subsystem's missing piece): hard-IRQ
# service obeys neither nice nor unit CPUAffinity, so decoder walls can
# starve the RX888 USB path even with perfect unit fences.  No-op on
# hosts without a CPU-affinity-managed radiod.
info "Installing sigmond-rx888-irq-affinity → /usr/local/sbin/"
$SUDO ln -sf "$REPO_DIR/scripts/sigmond-rx888-irq-affinity" /usr/local/sbin/sigmond-rx888-irq-affinity
ok "sigmond-rx888-irq-affinity symlink installed"

# The companion sweep: everything the xhci pin deliberately leaves alone.
# Movable non-xhci IRQs, the default mask new IRQs inherit, and the unbound
# workqueue pool all default to EVERY cpu, radiod's included — herd them
# onto the non-radiod set.  No-op on hosts without a managed radiod.
info "Installing sigmond-guest-irq-affinity → /usr/local/sbin/"
$SUDO ln -sf "$REPO_DIR/scripts/sigmond-guest-irq-affinity" /usr/local/sbin/sigmond-guest-irq-affinity
ok "sigmond-guest-irq-affinity symlink installed"

# Park fft / proc_rx888 on fixed siblings inside radiod's pair (runs from the
# smd drop-in's ExecStartPost on every radiod start; no unit of its own).
info "Installing sigmond-radiod-pin-threads → /usr/local/sbin/"
$SUDO ln -sf "$REPO_DIR/scripts/sigmond-radiod-pin-threads" /usr/local/sbin/sigmond-radiod-pin-threads
ok "sigmond-radiod-pin-threads symlink installed"

# qemu-guest-agent self-heal drop-in (VM guests only — the unit exists
# only where the agent package is installed).  qemu-ga buffers a whole
# `qm guest exec` output in memory; a large exec balloons it to GB and
# a client timeout then leaves the agent wedged — alive, channel dead,
# invisible to Restart=always (nothing died).  MemoryMax converts that
# wedge into an OOM-kill + clean restart.  See the drop-in's header and
# scripts/proxmox/pm-heartbeat.py::_check_guest_exec (the alarm side).
if systemctl list-unit-files qemu-guest-agent.service >/dev/null 2>&1 \
        && systemctl list-unit-files qemu-guest-agent.service 2>/dev/null | grep -q qemu-guest-agent; then
    info "Installing qemu-guest-agent self-heal drop-in"
    $SUDO install -d -m 0755 /etc/systemd/system/qemu-guest-agent.service.d
    $SUDO install -m 0644 "$REPO_DIR/systemd/qemu-guest-agent-selfheal.conf" \
        /etc/systemd/system/qemu-guest-agent.service.d/50-sigmond-selfheal.conf
    ok "qemu-guest-agent drop-in installed (MemoryMax self-heal)"
fi

$SUDO systemctl daemon-reload
# Enable just the unified trim timer.  ConditionPathExists=/var/lib/sigmond/sink.db
# in the service unit keeps it inactive until a producer writes — and even
# with sink.db pre-created, `smd admin storage trim --all --yes` is a no-op on an
# empty db.  Safe to enable on greenfield.
# `enable --now` STARTS the timer immediately (not just at next boot).
# Combined with the unit's OnActiveSec=10min, this guarantees a first
# trim fire ~10 min after install — without it, a host installed but
# never rebooted accumulates stale sink.db rows forever (the timer's
# OnBootSec/OnUnitActiveSec have no anchor until reboot or a manual
# service run).  Observed on B4-100 2026-05-30.
$SUDO systemctl enable --now sigmond-storage-trim-all.timer
# Enable the gap-hourly block-drop sampler timer.  ConditionPathExists=
# /var/lib/timestd/raw_buffer in the service unit keeps it inert on hosts
# without hf-timestd, so it's safe to always enable (same reasoning as the
# trim timer above re: greenfield-safe).
$SUDO systemctl enable --now sigmond-gap-hourly.timer 2>/dev/null || true
# sigmond-heartbeat.timer is INSTALLED above (it matched the sigmond-*.timer
# glob) but deliberately NOT enabled here — this is the one asymmetry with
# every other timer in this block.  Unlike gap-hourly/storage-trim, `smd
# admin heartbeat emit` has no ConditionPathExists to stay inert on an
# unconfigured host: it exits 2 every run until [heartbeat] is set in
# coordination.toml, so enabling it here would tick a failed-unit exit-2
# error every 5 minutes on every fresh install.  Enabling it is a per-host
# Phase-6 step, run once an operator has configured [heartbeat]:
#   sudo systemctl enable --now sigmond-heartbeat.timer
# Enable the timing SHM pre-create oneshot (idempotent; creates NTP0-3 at boot
# before gpsd/chrony/hf-timestd).  Only meaningful on a host running radiod +
# a local GPS, but harmless otherwise.
$SUDO systemctl enable sigmond-shm-precreate.service 2>/dev/null || true
# xhci IRQ pin at boot (no-op without a CPU-affinity-managed radiod).
$SUDO systemctl enable --now sigmond-rx888-irq-affinity.service 2>/dev/null || true
# Herd the remaining IRQs + unbound workqueues off radiod's cpus (same condition).
$SUDO systemctl enable --now sigmond-guest-irq-affinity.service 2>/dev/null || true
# Timing-chain reconciler (docs/timing-chain-architecture.md, step 3): the single
# owner of GPSDO/gpsd/chrony/hf-timestd recovery, replacing the hf-timestd watchdogs.
# Service is ConditionFileIsExecutable=/usr/sbin/gpsd, so harmless where no local GPS.
$SUDO systemctl enable --now sigmond-timing-reconcile.timer 2>/dev/null || true
ok "sigmond-storage-trim-all.timer enabled + started (15-min cadence)"

# ─── smd symlink ──────────────────────────────────────────────────────────────
info "Installing smd → $INSTALL_SMD"
$SUDO chmod a+x "$SMD_BIN"
$SUDO ln -sf "$SMD_BIN" "$INSTALL_SMD"
ok "smd installed at $INSTALL_SMD"

# Older installs put smd in /usr/local/sbin (root-only PATH on Debian).  Clean
# that up so we don't leave two symlinks pointing at the same target — and so
# `which smd` returns the canonical bin/ location.
if [[ -L "$LEGACY_INSTALL_SMD" ]]; then
    info "Removing legacy symlink $LEGACY_INSTALL_SMD"
    $SUDO rm -f "$LEGACY_INSTALL_SMD"
fi

# ─── operator shell aliases ───────────────────────────────────────────────────
# Ensure the invoking user's ~/.bash_aliases sources sigmond's curated alias
# file (ll / lrt / cds / tm).  Sourcing — not copying — keeps the repo file the
# single source of truth, so `git pull` updates the aliases with no per-host
# re-sync.  Idempotent: guarded by a marker block, so re-running install.sh is a
# no-op once present.
_ensure_operator_aliases() {
    local user="$1"
    [[ -z "$user" || "$user" == "root" ]] && return 0
    local home
    home="$(getent passwd "$user" | cut -d: -f6)"
    [[ -z "$home" || ! -d "$home" ]] && return 0
    local rc="$home/.bash_aliases"
    if [[ -f "$rc" ]] && grep -q '>>> sigmond aliases >>>' "$rc"; then
        ok "operator aliases already wired in $rc"
        return 0
    fi
    info "Wiring sigmond aliases (ll/lrt/cds/tm) into $rc…"
    {
        echo '# >>> sigmond aliases >>>'
        echo '# Curated sigmond shell aliases/functions (ll, lrt, cds, tm).'
        echo "# Source of truth: $CANONICAL_REPO/etc/aliases.sh — edits there propagate on \`git pull\`."
        echo "[ -r $CANONICAL_REPO/etc/aliases.sh ] && . $CANONICAL_REPO/etc/aliases.sh"
        echo '# <<< sigmond aliases <<<'
    } | $SUDO tee -a "$rc" >/dev/null
    $SUDO chown "$user:$(id -gn "$user" 2>/dev/null || echo "$user")" "$rc"
    ok "operator aliases wired (new shells pick them up; source ~/.bashrc for this one)"
}
_ensure_operator_aliases "$INVOKER"

# ─── catalog prune ────────────────────────────────────────────────────────────
# Trim /etc/sigmond/catalog.toml so it carries only entries that diverge
# from the in-repo catalog.  On a fresh install (file doesn't exist) this
# is a no-op.  On an upgrade where the operator file is a stale full copy,
# every duplicate block is dropped and the file may end up being removed
# entirely — sigmond's sparse-overlay reads the repo file directly when
# no operator file is present.  Non-fatal: a failed prune leaves the
# operator file as-is.
if [[ -f /etc/sigmond/catalog.toml ]]; then
    info "Pruning /etc/sigmond/catalog.toml against repo catalog…"
    if $SUDO env SIGMOND_ALLOW_SUDO=1 "$INSTALL_SMD" config catalog-prune; then
        ok "catalog pruned"
    else
        warn "catalog prune failed (non-fatal — operator file left intact)"
    fi
fi

# ─── proactive catalog repo clone ─────────────────────────────────────────────
# Download is universal; install is selective.  Clone every catalog
# entry's repo under /opt/git/sigmond/<name>/ so the operator can
# enable/disable components freely from the TUI later without waiting
# on a network round-trip every time they change their mind.  Clones
# are shallow (--depth 1) — full history is a `git fetch --unshallow`
# away when needed.  Non-fatal: any failure here just defers the
# clone to `smd install <name>`.
info "Pre-cloning catalog repos under /opt/git/sigmond/ (fast switch-on later)…"
to_clone=$(/usr/bin/env python3 - <<'PY' 2>/dev/null
import sys
from pathlib import Path
sys.path.insert(0, '/opt/git/sigmond/sigmond/lib')
try:
    from sigmond.catalog import load_catalog
    for n, e in load_catalog().items():
        if e.repo and not Path('/opt/git/sigmond') .joinpath(n).exists():
            print(f"{n}\t{e.repo}")
except Exception as exc:
    sys.stderr.write(f"catalog read failed: {exc}\n")
PY
)
if [[ -n "$to_clone" ]]; then
    while IFS=$'\t' read -r name url; do
        [[ -z "$name" || -z "$url" ]] && continue
        # Use $SUDO: on a greenfield first-run the invoking user's freshly
        # granted `sigmond` group membership is not yet active in this
        # session, so an unprivileged clone into the setgid, sigmond-owned
        # /opt/git/sigmond fails with EACCES.  Clone as root (mirrors the
        # ka9q-python clone above); the $SUDO chown below restores ownership.
        if $SUDO git clone --quiet --depth 1 "$url" "/opt/git/sigmond/$name" 2>/dev/null; then
            ok "  cloned $name"
        else
            warn "  $name: git clone $url failed (non-fatal)"
        fi
    done <<< "$to_clone"
    # Make sure the sigmond group owns + can write the new trees, same
    # pattern as the initial setgid setup.
    $SUDO chown -R sigmond:sigmond /opt/git/sigmond
    $SUDO find /opt/git/sigmond -maxdepth 1 -type d -exec chmod g+s {} \;
else
    ok "  every catalog repo already cloned"
fi

# ─── git safe.directory for cloned repos ──────────────────────────────────────
# Client builds run `uv sync` as root, which builds sibling path-deps
# (callhash, ka9q-python, hs-uploader, …) whose setuptools-scm version
# detection shells out to git.  Those repos are owned by `sigmond`, so a
# root-run git aborts with "detected dubious ownership" and the build fails.
# Trust every cloned repo system-wide (mirrors installer.py's clone_repo).
info "Marking /opt/git/sigmond repos as git safe.directory (system-wide)…"
for _repo in /opt/git/sigmond/*/; do
    _repo="${_repo%/}"
    [[ -d "$_repo/.git" ]] || continue
    $SUDO git config --system --get-all safe.directory 2>/dev/null | grep -qxF "$_repo" \
        || $SUDO git config --system --add safe.directory "$_repo"
done

# ─── RAC — admin remote-access infra on every sigmond host ────────────────────
# Provisions frpc + wd-rac.service (enabled, but INERT until configured with the
# gw2 assignment from the WsprDaemon admin).  Belongs on every install so a
# NAT'd station is reachable for support even before the station clients come up.
[[ -f /etc/sigmond/coordination.env ]] && source /etc/sigmond/coordination.env
if [[ -x /opt/git/sigmond/sigmond-rac/install.sh ]]; then
    info "Installing RAC (remote access channel)…"
    if $SUDO env STATION_CALL="${STATION_CALL:-}" SIGMOND_INSTANCE="${SIGMOND_INSTANCE:-}"             bash /opt/git/sigmond/sigmond-rac/install.sh; then
        ok "  RAC installed (inert until configured with the gw2 assignment)"
    else
        warn "  RAC install failed (non-fatal)"
    fi
else
    info "RAC not cloned (catalog repo unreachable) — skipping; add it later with 'smd install rac'"
fi

# ─── done ─────────────────────────────────────────────────────────────────────
echo
echo -e "${BOLD}${GREEN}╔═══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║   Sigmond is installed!  Next: open the TUI.          ║${NC}"
echo -e "${BOLD}${GREEN}╚═══════════════════════════════════════════════════════╝${NC}"
echo
echo -e "  ${BOLD}Open the configuration workflow:${NC}"
echo -e "    smd tui"
echo
echo -e "  Walk the Installation section top to bottom — that's the"
echo -e "  greenfield workflow:"
echo -e "    1. ${BOLD}Topology${NC}        — pick which catalog components to deploy"
echo -e "                            on this host.  The Detected column"
echo -e "                            says yes/no/— per row to inform you."
echo -e "    2. ${BOLD}Software versions${NC} — check what's installed and at which commit."
echo -e "    3. ${BOLD}Install${NC}         — build + install the enabled components."
echo -e "    4. ${BOLD}SDR inventory${NC}   — verify hardware enumeration before"
echo -e "                            radiod runs against it."
echo -e "    5. ${BOLD}Configuration${NC}   — create per-reporter instances for each"
echo -e "                            client (reporter ID, source, …)."
echo -e "    6. ${BOLD}CPU affinity${NC}    — pin radiod to dedicated cores (only if"
echo -e "                            this host is running radiod)."
echo -e "    7. ${BOLD}CPU frequency${NC}   — same — cap non-radiod cores to save power."
echo
echo -e "  ${BOLD}CLI shortcuts${NC} (power users):"
echo -e "    smd install                  install everything topology-enabled"
echo -e "    smd install <component>      install one"
echo -e "    smd list --catalog           browse the catalog"
echo

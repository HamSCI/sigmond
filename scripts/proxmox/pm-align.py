#!/usr/bin/env python3
"""pm-align — bring this Proxmox host forward to the latest blessed release, in place.

Run ON the Proxmox host (a PM), as root for --apply.  A PM carries no
sigmond, no git and no venv, so this is one stdlib file.  Dry run by
default: it says what differs from the release and changes nothing.

What it never touches, whatever the flags: the host's network (interfaces,
vmbr1, NAT, netfix/setnet), CPU/IRQ/cache tuning (host-apply, the fence,
irq-affinity, resctrl, grub, vfio), Proxmox packages, the hostname, the
VM's configuration, and the host's ssh key.  Those take a station off the
air; they stay with a reinstall or an explicit, separate act.

Design: sigmond docs/superpowers/specs/2026-09-24-smd-align-design.md §4.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

RELEASES_API = "https://api.github.com/repos/HamSCI/sigmond-appliance/releases"
RAW = "https://raw.githubusercontent.com/HamSCI"
PROXMOX_FILES = ("pm-heartbeat.py", "pm-heartbeat.service", "pm-heartbeat.timer",
                 "pm-heartbeat-setup.sh", "pm-align.py")
RECORD = "/etc/sigmond-appliance/host-aligned.json"
BACKUP_BASE = "/var/lib/pm-align/backup"
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_TIMEOUT = 30
_geteuid = os.geteuid

FRPC = "/usr/local/sbin/frpc"
FRPC_TOML = "/etc/sigmond/frpc-host.toml"
RAC_UNIT = "sigmond-rac-host.service"
RAC_NUMBER_FILE = "/etc/sigmond-appliance/rac-number"
FRPC_API = "http://127.0.0.1:7500/api/status"
TUNNEL_RESULT = "/var/lib/pm-align/tunnel-result.json"
BANDS = {"vm-ssh": 35800, "vm-web": 45800, "host-ssh": 50800, "host-ui": 55800,
         "vm-station": 48800, "vm-gmag": 49800}
LOCAL = {"vm-station": 12224, "vm-gmag": 12225}

HB_CONFIG = "/etc/pm-heartbeat/config.toml"
HB_DEFAULT = ("wd30.wsprdaemon.org", "38222")      # sigmond-wizard.sh:680, 687
HB_FILES = ("pm-heartbeat.py", "pm-heartbeat.service", "pm-heartbeat.timer",
            "pm-heartbeat-setup.sh")
HB_REQUIRED_KEYS = ("station", "vmid", "dest_host")   # pm-heartbeat.py's own _REQUIRED_KEYS


class PmAlignError(Exception):
    """The target cannot be established or a source fails verification."""


@dataclass
class Release:
    tag: str
    appliance_commit: str
    wizard_commit: str
    firstboot_sha256: str
    manifest_text: str


@dataclass
class Sources:
    firstboot: str
    wizard: str
    proxmox: dict = field(default_factory=dict)   # name -> bytes; absent names absent


def _get(url: str, urlopen: Callable) -> bytes:
    """GET ``url``.  On failure, raise PmAlignError with ``.status`` set to
    the HTTP status code when the failure was an HTTPError (e.g. 404), else
    None -- so a caller can distinguish "this commit doesn't carry the
    file" from a transient or server failure that must not be swallowed."""
    req = urllib.request.Request(url, headers={"User-Agent": "pm-align"})
    try:
        with urlopen(req, timeout=_TIMEOUT) as r:
            return r.read()
    except PmAlignError:
        raise
    except urllib.error.HTTPError as exc:
        err = PmAlignError(f"GET {url}: {exc}")
        err.status = exc.code
        raise err from exc
    except Exception as exc:
        err = PmAlignError(f"GET {url}: {exc}")
        err.status = None
        raise err from exc


def _preamble(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([a-z_0-9]+):\s*(\S+)\s*$", line)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2)
    return out


def fetch_release(tag: Optional[str] = None, *, urlopen: Callable = urllib.request.urlopen) -> Release:
    """The blessed release ``tag`` (default: the latest) and its manifest preamble."""
    url = f"{RELEASES_API}/tags/{tag}" if tag else f"{RELEASES_API}/latest"
    meta = json.loads(_get(url, urlopen))
    if meta.get("draft") or meta.get("prerelease"):
        raise PmAlignError(f"release {meta.get('tag_name')} is a draft or prerelease")
    tag_name = meta.get("tag_name") or ""
    asset = next((a for a in meta.get("assets", [])
                  if a.get("name", "").endswith(".manifest.txt")), None)
    if asset is None:
        raise PmAlignError(f"release {tag_name} publishes no manifest asset")
    text = _get(asset["browser_download_url"], urlopen).decode("utf-8", "replace")
    pre = _preamble(text)
    if pre.get("image_version") != tag_name:
        raise PmAlignError(f"release {tag_name}: manifest says image_version "
                           f"{pre.get('image_version')!r}")
    for key in ("appliance_commit", "wizard_commit"):
        if not _FULL_SHA.match(pre.get(key, "")):
            raise PmAlignError(f"release {tag_name}: {key} {pre.get(key)!r} is not a full commit")
    if not re.match(r"^[0-9a-f]{64}$", pre.get("firstboot_sha256", "")):
        raise PmAlignError(f"release {tag_name}: firstboot_sha256 missing or malformed")
    return Release(tag_name, pre["appliance_commit"], pre["wizard_commit"],
                   pre["firstboot_sha256"], text)


def render_wizard(text: str, vmid: int) -> str:
    """The build's VMID rendering (sigmond-appliance build-usb-v3.sh:262-263),
    with this host's VMID rather than a fixed 100."""
    return (text.replace("SIGMOND_VMID:-120", f"SIGMOND_VMID:-{vmid}")
                .replace('"SIGMOND_VMID", "120"', f'"SIGMOND_VMID", "{vmid}"'))


def fetch_sources(rel: Release, *, vmid: int, urlopen: Callable = urllib.request.urlopen) -> Sources:
    """firstboot-v3.sh at appliance_commit, rendered with the tag and checked
    against firstboot_sha256; the wizard and PROXMOX_FILES at wizard_commit.
    A 404 fetching pm-align.py means that commit predates the tool and is
    simply absent; any other failure, on pm-align.py or any other name,
    raises."""
    raw_fb = _get(f"{RAW}/sigmond-appliance/{rel.appliance_commit}/firstboot-v3.sh",
                  urlopen).decode("utf-8")
    firstboot = raw_fb.replace("@@VERSION@@", rel.tag)
    got = hashlib.sha256(firstboot.encode("utf-8")).hexdigest()
    if got != rel.firstboot_sha256:
        raise PmAlignError(f"firstboot-v3.sh at {rel.appliance_commit[:8]} hashes {got[:12]}…, "
                           f"manifest firstboot_sha256 {rel.firstboot_sha256[:12]}… — refused")
    base = f"{RAW}/sigmond/{rel.wizard_commit}/scripts/proxmox"
    wizard = render_wizard(_get(f"{base}/sigmond-wizard.sh", urlopen).decode("utf-8"), vmid)
    proxmox = {}
    for name in PROXMOX_FILES:
        try:
            proxmox[name] = _get(f"{base}/{name}", urlopen)
        except PmAlignError as exc:
            if name == "pm-align.py" and getattr(exc, "status", None) == 404:
                continue
            raise
    return Sources(firstboot, wizard, proxmox)


_HEREDOC_START = re.compile(r"""^\s*cat\s+>\s*"?(/[^"\s]+)"?\s+<<\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\2\s*$""")


def extract_heredocs(text: str, *, quoted_only: bool = True) -> dict:
    """{absolute path: body} for every top-level ``cat > PATH <<'DELIM'``.

    A quoted delimiter makes the body literal — the same bytes on every host —
    which is what makes it code.  An unquoted one substitutes site values, so
    it is skipped unless ``quoted_only`` is False (relay_units reads the
    wizard's unquoted templates that way).  A start line inside another
    heredoc's body is part of that body, never a heredoc of its own.  The
    closing delimiter must stand alone at column 0, as bash requires."""
    out = {}
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        m = _HEREDOC_START.match(lines[i].rstrip("\n"))
        if not m:
            i += 1
            continue
        path, quote, delim = m.group(1), m.group(2), m.group(3)
        j = i + 1
        while j < len(lines) and lines[j].rstrip("\n") != delim:
            j += 1
        if j >= len(lines):
            raise PmAlignError(f"heredoc for {path} (<<{delim}) never closes")
        if quote or not quoted_only:
            out[path] = "".join(lines[i + 1:j])
        i = j + 1
    return out


@dataclass
class HostFile:
    path: str
    content: bytes
    mode: int
    source: str


@dataclass
class Change:
    file: HostFile
    state: str          # current | changed | missing


FIRSTBOOT_FILES = {                          # path -> mode
    "/usr/local/lib/sigmond-net.sh": 0o644,
    "/usr/local/sbin/sigmond-issue": 0o755,
    "/etc/systemd/system/sigmond-issue.service": 0o644,
    "/etc/systemd/system/sigmond-issue.timer": 0o644,
}
WIZARD_HEREDOCS = {
    "/usr/local/bin/sigmond-vm": 0o755,
    "/usr/local/lib/sigmond/vm-port-relay.py": 0o755,
}
RELAYS = (("ssh", 12222, 22), ("web", 12223, 8081),
          ("station", 12224, 8000), ("gmag", 12225, 8082))
NOT_TOUCHED = (
    "network: /etc/network/*, vmbr1 + NAT + sysctl, sigmond-netfix / sigmond-setnet "
    "and their unit (sigmond-netfix.service)",
    "tuning: grub, vfio, kernel modules, initramfs, radiod-vm-fence, host IRQ affinity, "
    "resctrl (CAT), and the VM's CPU-pinning hookscript (/var/lib/vz/snippets/*)",
    "Proxmox itself and every apt package",
    "the hostname, /etc/pve, and the VM's own configuration (qm set, of any kind)",
    "the host's ssh key (/root/.ssh/id_ed25519) — it is the RAC identity",
)


def relay_units(wizard: str, vmid: int) -> list:
    """The 8 relay units, rendered from the wizard's own SOCKEOF/SVCEOF
    templates — so a unit the wizard changes, pm-align changes the same way."""
    tmpl = extract_heredocs(wizard, quoted_only=False)
    sock = tmpl.get("/etc/systemd/system/sigmond-vm-$RNAME-relay.socket")
    svc = tmpl.get("/etc/systemd/system/sigmond-vm-$RNAME-relay@.service")
    if sock is None or svc is None:
        raise PmAlignError("the release's wizard carries no relay unit templates")
    out = []
    for name, lport, vport in RELAYS:
        def render(t):
            return (t.replace("$RNAME", name).replace("$RLPORT", str(lport))
                     .replace("$RVPORT", str(vport)).replace("$VMID", str(vmid)))
        out.append(HostFile(f"/etc/systemd/system/sigmond-vm-{name}-relay.socket",
                            render(sock).encode(), 0o644, "wizard relay template"))
        out.append(HostFile(f"/etc/systemd/system/sigmond-vm-{name}-relay@.service",
                            render(svc).encode(), 0o644, "wizard relay template"))
    return out


def desired_files(src: Sources, vmid: int) -> list:
    """The refresh set (plan Decision 2): what the release says these host
    files hold.  Nothing outside it is ever written."""
    fb = extract_heredocs(src.firstboot)
    wz = extract_heredocs(src.wizard)
    out = []
    for path, mode in FIRSTBOOT_FILES.items():
        if path not in fb:
            raise PmAlignError(f"the release's firstboot no longer writes {path}")
        out.append(HostFile(path, fb[path].encode(), mode, "firstboot-v3.sh"))
    out.append(HostFile("/usr/local/sbin/sigmond-setup", src.wizard.encode(), 0o755,
                        "sigmond-wizard.sh"))
    for path, mode in WIZARD_HEREDOCS.items():
        if path not in wz:
            raise PmAlignError(f"the release's wizard no longer writes {path}")
        out.append(HostFile(path, wz[path].encode(), mode, "sigmond-wizard.sh"))
    out.extend(relay_units(src.wizard, vmid))
    if "pm-align.py" in src.proxmox:
        out.append(HostFile("/usr/local/sbin/pm-align", src.proxmox["pm-align.py"], 0o755,
                            "scripts/proxmox/pm-align.py"))
    return out


def _under(root: Path, path: str) -> Path:
    return Path(root) / path.lstrip("/")


def plan_files(desired: list, root: Path) -> list:
    out = []
    for f in desired:
        p = _under(root, f.path)
        try:
            state = "current" if p.read_bytes() == f.content else "changed"
        except FileNotFoundError:
            state = "missing"
        out.append(Change(f, state))
    return out


def host_vmid(root: Path) -> int:
    """The decoder VM's id, from the existing ssh relay unit (every host since
    v3.3x has it); 100, the v3 convention, when it cannot be read."""
    p = _under(root, "/etc/systemd/system/sigmond-vm-ssh-relay@.service")
    try:
        m = re.search(r"^Environment=SIGMOND_VMID=(\d+)\s*$", p.read_text(), re.M)
        if m:
            return int(m.group(1))
    except OSError:
        pass
    return 100


def topology_note(root: Path) -> Optional[str]:
    try:
        text = _under(root, "/etc/network/interfaces").read_text()
    except OSError:
        return None
    if re.search(r"^\s*(auto|iface)\s+vmbr1\b", text, re.M):
        return None
    return ("the decoder VM sits on the LAN (pre-v3.4x topology); current installs put it "
            "behind the host on vmbr1 — pm-align does not move it (a reinstall-class change)")


def apply_files(changes: list, root: Path, backup_dir: Path) -> list:
    """Write every changed or missing file: the old bytes to ``backup_dir``
    first (same relative path), then the new ones via a temp file and
    os.replace, so a reader never sees half a file."""
    written = []
    for ch in changes:
        if ch.state == "current":
            continue
        dest = _under(root, ch.file.path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            bk = _under(backup_dir, ch.file.path)
            bk.parent.mkdir(parents=True, exist_ok=True)
            bk.write_bytes(dest.read_bytes())
        tmp = dest.with_name(dest.name + ".pm-align-new")
        tmp.write_bytes(ch.file.content)
        os.chmod(tmp, ch.file.mode)
        os.replace(tmp, dest)
        written.append(ch.file.path)
    return written


def enable_new_relays(changes: list, *, run) -> list:
    """daemon-reload once any unit changed; then enable --now each relay
    socket that did not exist before.  A socket that already existed keeps
    its state — --rac-off may have stopped it on purpose."""
    units = [c for c in changes if c.state != "current"
             and c.file.path.startswith("/etc/systemd/system/")]
    if not units:
        return []
    run(["systemctl", "daemon-reload"], capture_output=True, text=True, timeout=60)
    enabled = []
    for c in units:
        name = Path(c.file.path).name
        if c.state == "missing" and name.startswith("sigmond-vm-") and name.endswith("-relay.socket"):
            r = run(["systemctl", "enable", "--now", name], capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                enabled.append(name)
            else:
                _say(f"  WARN: systemctl enable --now {name} failed: {(r.stderr or '').strip()}")
    return enabled


def write_record(root: Path, rel: Release, written: list, now: str) -> Path:
    p = _under(root, RECORD)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"release": rel.tag, "appliance_commit": rel.appliance_commit,
           "wizard_commit": rel.wizard_commit, "at": now, "files_written": written}
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, p)
    return p


def declared_proxies(toml_text: str) -> list:
    return re.findall(r'^name\s*=\s*"([^"]+)"\s*$', toml_text, re.M)


def _remote_ports(toml_text: str) -> dict:
    """{proxy name: remotePort} by reading each [[proxies]] block in order."""
    out, name = {}, None
    for line in toml_text.splitlines():
        m = re.match(r'^name\s*=\s*"([^"]+)"', line)
        if m:
            name = m.group(1)
        m = re.match(r'^remotePort\s*=\s*(\d+)', line)
        if m and name:
            out[name] = int(m.group(1))
            name = None
    return out


def tunnel_plan(toml_text: str, rac_marker: Optional[str]):
    """(new text, [names added]).  The RAC number is the marker's, and must
    agree with the vm-ssh proxy's remotePort − 35800; without a marker the
    port decides.  Only a missing vm-station / vm-gmag block is appended."""
    ports = _remote_ports(toml_text)
    ssh = next(((n, p) for n, p in ports.items() if n.endswith("-vm-ssh")), None)
    if ssh is None:
        raise PmAlignError(f"{FRPC_TOML} declares no -vm-ssh proxy — not a sigmond host tunnel")
    site = ssh[0][: -len("-vm-ssh")]
    from_port = ssh[1] - BANDS["vm-ssh"]
    rac = from_port
    if rac_marker and rac_marker.strip():
        rac = int(rac_marker.strip())
        if rac != from_port:
            raise PmAlignError(f"RAC number {rac} (rac-number) disagrees with the vm-ssh port "
                               f"{ssh[1]} (RAC {from_port}) — refused")
    names = set(declared_proxies(toml_text))
    added, blocks = [], []
    for ch in ("vm-station", "vm-gmag"):
        name = f"{site}-{ch}"
        if name in names:
            continue
        added.append(name)
        blocks.append(f'\n[[proxies]]\nname = "{name}"\ntype = "tcp"\nlocalIP = "127.0.0.1"\n'
                      f'localPort = {LOCAL[ch]}\nremotePort = {BANDS[ch] + rac}\n')
    if not added:
        return toml_text, []
    text = toml_text if toml_text.endswith("\n") else toml_text + "\n"
    return text + "".join(blocks), added


def proxies_running(api_json: str, user: str, names) -> bool:
    try:
        doc = json.loads(api_json or "{}")
    except ValueError:
        return False
    status = {p.get("name"): p.get("status") for p in doc.get("tcp", [])}
    return all(status.get(f"{user}.{n}") == "running" for n in names)


def _fetch_status() -> str:
    try:
        with urllib.request.urlopen(FRPC_API, timeout=5) as r:
            return r.read().decode()
    except Exception:
        # Any transient failure (connection, a truncated/invalid HTTP
        # response, undecodable bytes) reads as "not running yet", never
        # as an exception a caller must handle.
        return ""


def _wait_running(user, names, *, fetch_status, sleep, wait_s) -> bool:
    deadline = wait_s
    while True:
        if proxies_running(fetch_status(), user, names):
            return True
        if deadline <= 0:
            return False
        sleep(5)
        deadline -= 5


def _read_rac_marker(root: Path) -> Optional[str]:
    try:
        return _under(root, RAC_NUMBER_FILE).read_text()
    except OSError:
        return None


def _rollback(path: Path, backup: Path, old_names: list, user: str, run, fetch_status,
             sleep, wait_s: int, reason: str) -> dict:
    """Restore the previous config and restart, tolerating a second failure
    at each of the three steps so it can never mask ``reason`` (the first
    failure) and a second exception can never skip the restore that follows
    it.  Called only once the live file has already been swapped to the new
    (unconfirmed) config, so every path here ends in either "rolled-back"
    (old tunnel back and running) or "failed" (needs hands)."""
    try:
        os.replace(backup, path)
    except BaseException as exc:
        return {"outcome": "failed",
                "detail": f"{reason}; restoring the previous config raised {exc!r} — needs hands"}
    try:
        run(["systemctl", "restart", RAC_UNIT], capture_output=True, text=True, timeout=60)
    except BaseException as exc:
        return {"outcome": "failed",
                "detail": f"{reason}; previous config restored but the restart raised {exc!r} — "
                          "needs hands"}
    try:
        up = _wait_running(user, old_names, fetch_status=fetch_status, sleep=sleep, wait_s=wait_s)
    except BaseException as exc:
        return {"outcome": "failed",
                "detail": f"{reason}; previous config restored but checking it raised {exc!r} — "
                          "needs hands"}
    if up:
        return {"outcome": "rolled-back", "detail": f"{reason}; previous tunnel restored and running"}
    return {"outcome": "failed",
            "detail": f"{reason}; previous tunnel restored but NOT all running — needs hands"}


def tunnel_apply(root: Path, *, run, fetch_status=_fetch_status, sleep=time.sleep,
                 wait_s: int = 90) -> dict:
    """Add the missing channels to the host tunnel, or leave it exactly as it was.

    In order: (a) refuse to touch a tunnel the operator switched off
    (``sigmond-setup --rac-off`` disables the unit but keeps the config);
    (b) refuse unless every currently-declared proxy is already running —
    installing on top of an already-broken tunnel would misattribute the
    break; (c) plan, verify with frpc, then install, restart, and require
    EVERY declared proxy "running" within ``wait_s`` — guaranteed rollback:
    from the install onward, any exception (the rename itself, a raised
    restart, a raised status fetch, a decode failure) is treated exactly
    like "didn't come up" and drives the same restore-and-recheck path, so
    the live file is never left on an unconfirmed config and no exception
    ever escapes this function once the backup has been written."""
    path = _under(root, FRPC_TOML)
    old = path.read_text()

    # (a) active check
    r = run(["systemctl", "is-active", RAC_UNIT], capture_output=True, text=True, timeout=30)
    if (getattr(r, "stdout", "") or "").strip() != "active":
        return {"outcome": "skipped", "detail": "tunnel is off — not changed"}

    m = re.search(r'^user\s*=\s*"([^"]*)"', old, re.M)
    if not m:
        return {"outcome": "failed", "detail": f"{FRPC_TOML} declares no user — cannot verify proxies"}
    user = m.group(1)
    old_names = declared_proxies(old)

    # (b) baseline check
    if not proxies_running(fetch_status(), user, old_names):
        return {"outcome": "failed", "detail": "tunnel not fully up before the change — refused"}

    # (c) plan / verify / install / restart / wait, with guaranteed rollback
    new, added = tunnel_plan(old, _read_rac_marker(root))
    if not added:
        return {"outcome": "current", "detail": "every channel already declared"}
    cand = path.with_name(path.name + ".pm-align-new")
    cand.write_text(new)
    os.chmod(cand, 0o600)
    rv = run([FRPC, "verify", "-c", str(cand)], capture_output=True, text=True, timeout=30)
    if rv.returncode != 0:
        cand.unlink()
        return {"outcome": "failed", "detail": f"frpc verify refused: {(rv.stderr or rv.stdout).strip()}"}
    backup = path.with_name(path.name + ".pm-align-prev")
    backup.write_text(old)
    os.chmod(backup, 0o600)
    try:
        os.replace(cand, path)
        run(["systemctl", "restart", RAC_UNIT], capture_output=True, text=True, timeout=60)
        if _wait_running(user, declared_proxies(new), fetch_status=fetch_status, sleep=sleep,
                         wait_s=wait_s):
            return {"outcome": "applied", "detail": f"added {', '.join(added)}; every channel running"}
        reason = f"{', '.join(added)} did not come up"
    except BaseException as exc:
        reason = f"installing {', '.join(added)} raised {exc!r}"
        # If the rename itself is what failed, cand is still sitting there
        # (os.replace is atomic: it either completes or leaves both files
        # exactly as before) — clean up the litter. Restoring the backup
        # below is harmless either way: when the install never happened,
        # path already holds `old`, and backup holds those same bytes.
        try:
            if cand.exists():
                cand.unlink()
        except OSError:
            pass
    return _rollback(path, backup, old_names, user, run, fetch_status, sleep, wait_s, reason)


def write_tunnel_result(root: Path, out: dict, now: str) -> Path:
    p = _under(root, TUNNEL_RESULT)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(out)
    doc["at"] = now
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, p)
    return p


def heartbeat_args(root: Path, *, opt_in: bool, vmid: int, dest: Optional[str]):
    """Arguments for the release's pm-heartbeat-setup.sh, or None to skip.

    Configured before: re-run with its own station and destination (the
    setup script is idempotent and keeps its key) -- ``opt_in``/``dest``
    are then irrelevant, they only steer the never-configured path.  Never
    configured: only with --heartbeat, as the wizard asks first; the
    station is the configured reporter plus "-pm" (sigmond-wizard.sh:941)."""
    cfg = _under(root, HB_CONFIG)
    if cfg.exists():
        try:
            doc = tomllib.loads(cfg.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise PmAlignError(f"{HB_CONFIG} is not valid TOML: {exc}")
        missing = [k for k in HB_REQUIRED_KEYS if doc.get(k) in (None, "")]
        if missing:
            raise PmAlignError(f"{HB_CONFIG} is missing required key(s): {', '.join(missing)}")
        return ["--station", str(doc["station"]), "--vmid", str(doc["vmid"]),
                "--dest-host", str(doc["dest_host"]), "--dest-port", str(doc.get("dest_port", 22))]
    if not opt_in:
        return None
    try:
        reporter = _under(root, "/etc/sigmond-appliance/.configured").read_text().split()[0]
    except (OSError, IndexError):
        raise PmAlignError("--heartbeat: no configured reporter in "
                           "/etc/sigmond-appliance/.configured — run sigmond-setup first")
    host, port = (dest.rsplit(":", 1) if dest else HB_DEFAULT)
    return ["--station", f"{reporter}-pm", "--vmid", str(vmid),
            "--dest-host", host, "--dest-port", str(port)]


def heartbeat_step(root: Path, src: Sources, argv_tail: list, *, run, tmpdir: Path) -> str:
    """Stage the release's heartbeat files in ``tmpdir`` (the setup script
    installs from its own directory) and run its setup.  ``tmpdir`` is the
    caller's to create and remove; this function only writes into it."""
    tmpdir.mkdir(parents=True, exist_ok=True)
    for name in HB_FILES:
        if name not in src.proxmox:
            return f"skipped: the release carries no {name}"
        p = tmpdir / name
        p.write_bytes(src.proxmox[name])
        os.chmod(p, 0o755 if name.endswith((".sh", ".py")) else 0o644)
    existed = _under(root, HB_CONFIG).exists()
    r = run([str(tmpdir / "pm-heartbeat-setup.sh"), *argv_tail],
            capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:]
        return f"failed: {tail[0] if tail else 'exit ' + str(r.returncode)}"
    return "re-run" if existed else "set up"


def _say(msg=""):
    print(msg, flush=True)


def _launch_tunnel_step(root: Path, run) -> int:
    """Launch ``--tunnel-step`` detached via systemd-run so it (and the frpc
    restart it may do) survives this ssh session dropping.  Runs from the
    just-installed /usr/local/sbin/pm-align when the file step wrote one,
    else this process's own script.  Returns the rc to propagate from
    --apply: 0 if launched, 1 if systemd-run itself failed."""
    installed = _under(root, "/usr/local/sbin/pm-align")
    script = installed if installed.exists() else Path(__file__).resolve()
    r = run(["systemd-run", "--unit", "pm-align-tunnel", "--collect", "--quiet",
             sys.executable, str(script), "--tunnel-step"],
            capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        _say(f"  tunnel: systemd-run failed: {(r.stderr or r.stdout or '').strip()}")
        return 1
    return 0


def _apply_tunnel(root: Path, run) -> int:
    """The tunnel step of --apply: skip if there's no host tunnel, refuse if
    the plan disagrees with the RAC number, else launch it detached."""
    tpath = _under(root, FRPC_TOML)
    if not tpath.exists():
        _say("  tunnel: no host tunnel configured — skipped")
        return 0
    try:
        _, added = tunnel_plan(tpath.read_text(), _read_rac_marker(root))
    except PmAlignError as exc:
        _say(f"  tunnel: REFUSED — {exc}")
        return 0
    if not added:
        _say("  tunnel: every channel declared")
        return 0
    rc = _launch_tunnel_step(root, run)
    if rc == 0:
        _say("  tunnel: adding " + ", ".join(added) + " — running detached (pm-align-tunnel).")
        _say("  This ssh session may drop while the tunnel restarts. Reconnect in ~2 minutes,")
        _say("  then: pm-align --status")
    return rc


def _apply_heartbeat(root: Path, src: Sources, *, run, vmid: int, opt_in: bool,
                     dest: Optional[str]) -> int:
    """The heartbeat step of --apply: skip when never configured and not
    opted in, else stage the release's own setup script into a scratch
    dir (removed after, success or failure) and run it.  A "failed: ..."
    result is a WARN and makes --apply's exit code 1; "skipped: ..." (the
    release carries no heartbeat files) is informational only -- neither
    stops the tunnel step that follows."""
    try:
        hb = heartbeat_args(root, opt_in=opt_in, vmid=vmid, dest=dest)
    except PmAlignError as exc:
        _say(f"  heartbeat: REFUSED — {exc}")
        return 1
    if hb is None:
        _say("  heartbeat: not set up — add --heartbeat to set it up")
        return 0
    tmpdir = Path(tempfile.mkdtemp(prefix="pm-align-hb-"))
    try:
        out = heartbeat_step(root, src, hb, run=run, tmpdir=tmpdir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if out.startswith("failed"):
        _say(f"  heartbeat: WARN — {out}")
        return 1
    _say(f"  heartbeat: {out}")
    if out == "set up":
        pub = _under(root, "/etc/pm-heartbeat/id_ed25519.pub")
        try:
            _say(f"  heartbeat pubkey: {pub.read_text().strip()}")
        except OSError:
            pass
        _say("  authorize it on the fleetboard server (server/heartbeat/authorize-stations.sh)")
    return 0


def _do_apply(changes: list, rel: Release, root: Path, run, now, *, src: Sources, vmid: int,
             heartbeat_opt_in: bool, heartbeat_dest: Optional[str]) -> int:
    """The --apply steps, in order: files, relays, record, heartbeat, then
    the tunnel launch LAST (it may drop the operator's ssh session)."""
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    backup_dir = _under(root, f"{BACKUP_BASE}/{stamp}")
    written = apply_files(changes, root, backup_dir)
    enable_new_relays(changes, run=run)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    record = write_record(root, rel, written, now_iso)
    for path in written:
        _say(f"  wrote {path}")
    _say(f"  backup: {backup_dir}")
    _say(f"  record: {record}")
    hb_rc = _apply_heartbeat(root, src, run=run, vmid=vmid, opt_in=heartbeat_opt_in,
                             dest=heartbeat_dest)
    tunnel_rc = _apply_tunnel(root, run)
    return max(hb_rc, tunnel_rc)


def _dry_run_tunnel_note(root: Path) -> tuple:
    """(message, pending) for the dry-run tunnel status line."""
    tpath = _under(root, FRPC_TOML)
    if not tpath.exists():
        return "tunnel: no host tunnel configured", False
    try:
        _, added = tunnel_plan(tpath.read_text(), _read_rac_marker(root))
    except PmAlignError as exc:
        return f"tunnel: REFUSED — {exc}", True
    if added:
        return f"tunnel: would add {', '.join(added)}", True
    return "tunnel: every channel declared", False


def _dry_run_heartbeat_note(root: Path) -> str:
    """The dry-run heartbeat status line.  Purely a read of whether the
    host has ever been configured -- opt_in/dest only matter to --apply."""
    if _under(root, HB_CONFIG).exists():
        return "heartbeat: configured (re-run on --apply)"
    return "heartbeat: not set up — add --heartbeat to set it up"


def _print_status(root: Path) -> None:
    tpath = _under(root, TUNNEL_RESULT)
    _say(f"tunnel result ({tpath}):")
    _say(tpath.read_text().rstrip("\n") if tpath.exists() else "  none yet")
    rpath = _under(root, RECORD)
    _say(f"alignment record ({rpath}):")
    _say(rpath.read_text().rstrip("\n") if rpath.exists() else "  none yet")


def main(argv=None, *, root: Path = Path("/"), urlopen: Callable = urllib.request.urlopen,
         run=None, now=None) -> int:
    """Exit 0 aligned (or --apply/--tunnel-step succeeded), 1 alignment
    available (or --tunnel-step did not fully come up), 2 target could not
    be established, or --apply refused (not root)."""
    run = run or subprocess.run
    now = now or datetime.now(timezone.utc)
    ap = argparse.ArgumentParser(prog="pm-align", description=__doc__.splitlines()[0])
    ap.add_argument("--release", help="a blessed tag (default: the latest)")
    ap.add_argument("--apply", action="store_true", help="write the refresh set, as root")
    ap.add_argument("--tunnel-step", action="store_true", dest="tunnel_step",
                    help=argparse.SUPPRESS)
    ap.add_argument("--status", action="store_true",
                    help="print the last alignment and tunnel-step results")
    ap.add_argument("--heartbeat", action="store_true",
                    help="set up the host heartbeat if this host has never had one")
    ap.add_argument("--heartbeat-dest", dest="heartbeat_dest", metavar="HOST:PORT",
                    help="override the heartbeat destination (only used the first time "
                         "the heartbeat is set up; an existing config keeps its own)")
    args = ap.parse_args(argv)

    if args.heartbeat_dest is not None:
        host, sep, port = args.heartbeat_dest.rpartition(":")
        if not sep or not port.isdigit():
            _say(f"pm-align: --heartbeat-dest must be HOST:PORT, got {args.heartbeat_dest!r}")
            return 2

    if args.status:
        _print_status(root)
        return 0

    if args.tunnel_step:
        try:
            out = tunnel_apply(root, run=run)
        except BaseException as exc:
            # Belt and braces: tunnel_apply's own guaranteed-rollback path
            # should already catch everything it can, but a result file
            # must exist however this ends, so nothing gets read as "still
            # running" when it silently died instead.
            out = {"outcome": "failed", "detail": f"tunnel_apply raised {exc!r} — needs hands"}
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        write_tunnel_result(root, out, now_iso)
        _say(json.dumps(out))
        return 0 if out["outcome"] in ("applied", "current", "skipped") else 1

    vmid = host_vmid(root)
    try:
        rel = fetch_release(args.release, urlopen=urlopen)
        src = fetch_sources(rel, vmid=vmid, urlopen=urlopen)
        changes = plan_files(desired_files(src, vmid), root)
    except PmAlignError as exc:
        _say(f"pm-align: cannot establish the target — {exc}")
        return 2
    _say(f"pm-align: this host against {rel.tag} (VM {vmid})")
    for ch in changes:
        _say(f"  {ch.state:8} {ch.file.path}   ({ch.file.source})")
    note = topology_note(root)
    if note:
        _say(f"  topology: {note}")
    _say("  not touched, by design:")
    for line in NOT_TOUCHED:
        _say(f"    - {line}")
    pending = [c for c in changes if c.state != "current"]
    if args.apply:
        if _geteuid() != 0:
            _say("pm-align --apply: run as root")
            return 2
        return _do_apply(changes, rel, root, run, now, src=src, vmid=vmid,
                         heartbeat_opt_in=args.heartbeat, heartbeat_dest=args.heartbeat_dest)
    tunnel_msg, tunnel_pending = _dry_run_tunnel_note(root)
    _say(f"  {tunnel_msg}")
    _say(f"  {_dry_run_heartbeat_note(root)}")
    _say(f"  summary: {len(pending)} file(s) to refresh; re-run with --apply as root")
    return 1 if (pending or tunnel_pending) else 0


if __name__ == "__main__":
    sys.exit(main())

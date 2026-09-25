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
import subprocess
import sys
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


def _say(msg=""):
    print(msg, flush=True)


def _do_apply(changes: list, rel: Release, root: Path, run, now) -> int:
    """The --apply steps, in order.  Later tasks (tunnel, heartbeat, VM
    record push) append further steps here after write_record."""
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
    return 0


def main(argv=None, *, root: Path = Path("/"), urlopen: Callable = urllib.request.urlopen,
         run=None, now=None) -> int:
    """Exit 0 aligned (or --apply succeeded), 1 alignment available,
    2 target could not be established, or --apply refused (not root)."""
    run = run or subprocess.run
    now = now or datetime.now(timezone.utc)
    ap = argparse.ArgumentParser(prog="pm-align", description=__doc__.splitlines()[0])
    ap.add_argument("--release", help="a blessed tag (default: the latest)")
    ap.add_argument("--apply", action="store_true", help="write the refresh set, as root")
    args = ap.parse_args(argv)
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
        return _do_apply(changes, rel, root, run, now)
    _say(f"  summary: {len(pending)} file(s) to refresh; re-run with --apply as root")
    return 1 if pending else 0


if __name__ == "__main__":
    sys.exit(main())

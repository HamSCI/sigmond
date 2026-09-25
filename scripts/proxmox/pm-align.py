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

import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

RELEASES_API = "https://api.github.com/repos/HamSCI/sigmond-appliance/releases"
RAW = "https://raw.githubusercontent.com/HamSCI"
PROXMOX_FILES = ("pm-heartbeat.py", "pm-heartbeat.service", "pm-heartbeat.timer",
                 "pm-heartbeat-setup.sh", "pm-align.py")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_TIMEOUT = 30


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

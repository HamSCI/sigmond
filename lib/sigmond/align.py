"""smd align — how far a station sits from the latest blessed appliance release.

Design: docs/superpowers/specs/2026-09-24-smd-align-design.md.  This module
plans; it never changes a station.  Every network read goes through an
injected ``urlopen`` (the lib/sigmond/discovery/http_* idiom), so tests
never reach the network.  Standard library only, like the rest of core smd.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from sigmond.doctor import _parse_manifest_components, _sha_equal

RELEASES_API = "https://api.github.com/repos/HamSCI/sigmond-appliance/releases"
_MAX_BODY = 1 << 20          # no reply we read is anywhere near 1 MiB


class LookupError_(RuntimeError):
    """The target could not be established; align must not guess one."""


def _default_urlopen(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": "sigmond-align",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _get(url: str, urlopen, timeout: float) -> bytes:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.read(_MAX_BODY)
    except Exception as exc:  # noqa: BLE001 — every failure means "no answer"
        raise LookupError_(f"could not reach {url}: {exc}") from exc


@dataclass(frozen=True)
class Release:
    tag: str
    manifest_text: str
    appliance_commit: Optional[str]
    components: dict


def _preamble(text: str, key: str) -> Optional[str]:
    for line in text.splitlines():
        if line.startswith(key + ":"):
            return line.split(":", 1)[1].strip() or None
    return None


def fetch_release(tag: Optional[str] = None, urlopen: Optional[Callable] = None,
                  timeout: float = 20.0) -> Release:
    """The blessed release ``tag`` (default: the latest) and its manifest."""
    urlopen = urlopen or _default_urlopen
    url = f"{RELEASES_API}/tags/{tag}" if tag else f"{RELEASES_API}/latest"
    try:
        meta = json.loads(_get(url, urlopen, timeout))
    except ValueError as exc:
        raise LookupError_(f"{url} did not return JSON: {exc}") from exc
    asset = next((a for a in meta.get("assets", [])
                  if a.get("name", "").endswith(".manifest.txt")), None)
    if asset is None:
        raise LookupError_(f"release {meta.get('tag_name')} publishes no manifest asset")
    text = _get(asset["browser_download_url"], urlopen, timeout).decode("utf-8", "replace")
    components = _parse_manifest_components(text)
    if components is None:
        raise LookupError_(f"release {meta.get('tag_name')}: manifest is missing or truncated")
    return Release(tag=meta.get("tag_name") or (tag or "?"), manifest_text=text,
                   appliance_commit=_preamble(text, "appliance_commit"),
                   components=components)


RADIOD = "ka9q-radio"
_DIRTY_NOTE = "uncommitted changes — commit, stash or discard first"


@dataclass(frozen=True)
class Item:
    component: str
    status: str            # current | move | refuse | missing | stray
    live: Optional[str]
    target: Optional[str]
    note: str = ""


def plan_align(release: Release, live: dict, dirty: dict) -> list:
    """What aligning to ``release`` would do, component by component. Pure."""
    items = []
    for name, target in release.components.items():
        if name not in live:
            items.append(Item(name, "missing", None, target, "no checkout on this station"))
            continue
        head = live[name]
        if head is None:
            items.append(Item(name, "refuse", None, target, "HEAD unreadable"))
        elif _sha_equal(head, target):
            items.append(Item(name, "current", head, target))
        elif dirty.get(name):
            items.append(Item(name, "refuse", head, target, _DIRTY_NOTE))
        else:
            note = "RESTARTS radiod on --apply" if name == RADIOD else ""
            items.append(Item(name, "move", head, target, note))
    strays = [Item(n, "stray", h, None, "not in the release manifest; left alone")
              for n, h in live.items() if n not in release.components]
    items.sort(key=lambda i: (i.component != "sigmond", i.component))
    return items + sorted(strays, key=lambda i: i.component)


RAW_BASE = "https://raw.githubusercontent.com/HamSCI/sigmond-appliance"
IMAGE_FILES = (
    ("/usr/local/sbin/sigmond-site-timing", "sigmond-site-timing"),
    ("/usr/local/sbin/sigmond-location-check", "sigmond-location-check"),
)


def github_slug(repo_url: str) -> Optional[str]:
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", repo_url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else None


def commit_distance(slug: str, base: str, head: str, urlopen: Optional[Callable] = None,
                    timeout: float = 20.0) -> dict:
    url = f"https://api.github.com/repos/{slug}/compare/{base}...{head}"
    try:
        doc = json.loads(_get(url, urlopen or _default_urlopen, timeout))
    except (LookupError_, ValueError) as exc:
        return {"error": str(exc)}
    return {"ahead": int(doc.get("ahead_by", 0)), "behind": int(doc.get("behind_by", 0)),
            "files": len(doc.get("files") or [])}


def _read_local(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def image_file_drift(tag: str, read_local: Optional[Callable] = None,
                     urlopen: Optional[Callable] = None, timeout: float = 20.0) -> list:
    read_local = read_local or _read_local
    out = []
    for vm_path, repo_path in IMAGE_FILES:
        try:
            want = _get(f"{RAW_BASE}/{tag}/{repo_path}", urlopen or _default_urlopen, timeout)
        except LookupError_ as exc:
            out.append({"path": vm_path, "status": "unknown", "note": str(exc)})
            continue
        have = read_local(vm_path)
        if have is None:
            out.append({"path": vm_path, "status": "absent", "note": "not installed"})
        elif hashlib.sha256(have).digest() == hashlib.sha256(want).digest():
            out.append({"path": vm_path, "status": "current", "note": ""})
        else:
            out.append({"path": vm_path, "status": "differs", "note": f"differs from {tag}"})
    return out


def recorded_release(path: str = "/etc/sigmond-appliance/version") -> Optional[str]:
    text = _read_local(path)
    if not text:
        return None
    parts = text.decode("utf-8", "replace").split()
    return parts[0] if parts else None

"""smd align — how far a station sits from the latest blessed appliance release.

Design: docs/superpowers/specs/2026-09-24-smd-align-design.md.  This module
plans; it never changes a station.  Every network read goes through an
injected ``urlopen`` (the lib/sigmond/discovery/http_* idiom), so tests
never reach the network.  Standard library only, like the rest of core smd.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from sigmond.doctor import _parse_manifest_components

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

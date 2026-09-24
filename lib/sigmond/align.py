"""smd align — how far a station sits from the latest blessed appliance release.

Design: docs/superpowers/specs/2026-09-24-smd-align-design.md.  This module
plans; it never changes a station.  Every network read goes through an
injected ``urlopen`` (the lib/sigmond/discovery/http_* idiom), so tests
never reach the network.  Standard library only, like the rest of core smd.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
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


def _rate_limit_reset(headers) -> str:
    reset = headers.get("X-RateLimit-Reset") if headers else None
    if not reset:
        return ""
    try:
        dt = datetime.fromtimestamp(int(reset), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return ""
    return f" (resets {dt.strftime('%H:%MZ')})"


def _get(url: str, urlopen, timeout: float) -> bytes:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.read(_MAX_BODY)
    except urllib.error.HTTPError as exc:
        headers = exc.headers or {}
        try:
            body = exc.read(_MAX_BODY)
        except Exception:  # noqa: BLE001 — reading the error body is best-effort
            body = b""
        body_text = body.decode("utf-8", "replace") if body else ""
        remaining = headers.get("X-RateLimit-Remaining") if headers else None
        if exc.code in (403, 429) and (remaining == "0" or "rate limit" in body_text.lower()):
            raise LookupError_(
                f"GitHub API rate limit reached{_rate_limit_reset(headers)}") from exc
        if exc.code == 404 and "/releases/tags/" in url:
            raise LookupError_(f"no such release: {url.rsplit('/', 1)[-1]}") from exc
        raise LookupError_(f"HTTP {exc.code} from {url}") from exc
    except (urllib.error.URLError, OSError, http.client.HTTPException, TimeoutError) as exc:
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


# The trust boundary: everything above this point is our own code; a
# manifest's component/sha rows and its self-declared tag come from a
# release asset on GitHub — reachable by anyone who can push a release
# to the appliance repo. A malformed row must refuse the WHOLE manifest
# rather than let a partially-bad table drive a later `--apply`.
COMPONENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+(\.\d+)?$")


def fetch_release(tag: Optional[str] = None, urlopen: Optional[Callable] = None,
                  timeout: float = 20.0) -> Release:
    """The blessed release ``tag`` (default: the latest) and its manifest."""
    if tag is not None and not RELEASE_TAG_RE.match(tag):
        raise LookupError_(f"not a release tag: {tag}")
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
    tag_name = meta.get("tag_name") or (tag or "?")
    for name, sha in components.items():
        if not COMPONENT_NAME_RE.match(name) or not SHA_RE.match(sha):
            raise LookupError_(
                f"release {tag_name}: manifest entry {name!r} {sha!r} is malformed")
    preamble_tag = _preamble(text, "appliance_tag") or _preamble(text, "image_version")
    if preamble_tag is not None and preamble_tag != tag_name:
        raise LookupError_(
            f"release {tag_name}: manifest preamble tag {preamble_tag!r} does not "
            f"match the release tag {tag_name!r}")
    return Release(tag=tag_name, manifest_text=text,
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


def plan_align(release: Release, live: dict, dirty: dict, errors: Optional[dict] = None) -> list:
    """What aligning to ``release`` would do, component by component. Pure."""
    errors = errors or {}
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
        elif errors.get(name):
            items.append(Item(name, "refuse", head, target, f"state unreadable: {errors[name]}"))
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
    # per_page=1: the compare response otherwise carries every commit and
    # its full patch — hundreds of KB for a large move, on links this is
    # meant to be cheap on. GitHub does not paginate `files` by per_page
    # on this endpoint, so it may be absent or capped (~300); when the
    # key is missing we report "unknown", never a false zero.
    url = f"https://api.github.com/repos/{slug}/compare/{base}...{head}?per_page=1"
    try:
        doc = json.loads(_get(url, urlopen or _default_urlopen, timeout))
    except (LookupError_, ValueError) as exc:
        return {"error": str(exc)}
    files = len(doc["files"]) if "files" in doc and doc["files"] is not None else None
    return {"ahead": int(doc.get("ahead_by", 0)), "behind": int(doc.get("behind_by", 0)),
            "files": files}


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

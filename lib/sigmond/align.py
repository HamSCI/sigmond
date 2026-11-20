"""smd align — how far a station sits from the latest blessed sigmond release.

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

# Releases live on HamSCI/sigmond from v3.94 on (release-home design,
# 2026-11-20).  Older ones live only on the image repo, so a release
# HamSCI/sigmond does not have is looked for there -- on a 404 alone, never
# on a timeout or a 5xx, which could aim a station at an older latest.
RELEASE_SLUG = "HamSCI/sigmond"
# The image repo.  HamSCI/sigmond-appliance is its name before the rename;
# it goes when the mirror ends (no station left on a pre-v3.94 smd).
PROXMOX_SLUGS = ("HamSCI/sigmond-proxmox", "HamSCI/sigmond-appliance")


def releases_api(slug: str) -> str:
    return f"https://api.github.com/repos/{slug}/releases"


def releases_web(slug: str) -> str:
    """github.com's own release pages: no API rate limit (60 calls/h per
    address, which a shared site address can spend before align runs --
    N8UR, 2026-11-15)."""
    return f"https://github.com/{slug}/releases"


def manifest_href_re(slug: str) -> "re.Pattern":
    return re.compile(r'href="(/' + re.escape(slug)
                      + r'/releases/download/([^"/]+)/[^"/]+\.manifest\.txt)"')


RELEASES_API = releases_api(RELEASE_SLUG)
RELEASES_WEB = releases_web(RELEASE_SLUG)
_MAX_BODY = 1 << 20          # no reply we read is anywhere near 1 MiB


class LookupError_(RuntimeError):
    """The target could not be established; align must not guess one."""


class RateLimited(LookupError_):
    """The GitHub API refused for its rate limit."""


class NotFound(LookupError_):
    """The server answered 404: the thing asked for is not there.  Every
    other failure (network, timeout, 5xx) says nothing about absence."""


class ReleaseNotFound(NotFound):
    """The repo holds no such release (a 404 on a /releases/ URL, or the
    release pages show none).  The one failure fetch_release falls back on."""


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
            raise RateLimited(
                f"GitHub API rate limit reached{_rate_limit_reset(headers)}") from exc
        if exc.code == 404 and "/releases/tags/" in url:
            raise ReleaseNotFound(f"no such release: {url.rsplit('/', 1)[-1]}") from exc
        if exc.code == 404 and "/releases/" in url:
            raise ReleaseNotFound(f"HTTP 404 from {url}") from exc
        if exc.code == 404:
            raise NotFound(f"HTTP 404 from {url}") from exc
        raise LookupError_(f"HTTP {exc.code} from {url}") from exc
    except (urllib.error.URLError, OSError, http.client.HTTPException, TimeoutError) as exc:
        raise LookupError_(f"could not reach {url}: {exc}") from exc


@dataclass(frozen=True)
class Release:
    tag: str
    manifest_text: str
    appliance_commit: Optional[str]
    components: dict
    draft: bool = False
    prerelease: bool = False
    # A release candidate read from a local manifest file (`smd align
    # --manifest`): no GitHub Release stands behind it yet.  Canaries align
    # to it before the bless; nothing records it as blessed.
    candidate: bool = False
    manifest_path: Optional[str] = None
    # The repo whose Release this is: RELEASE_SLUG, or one of PROXMOX_SLUGS
    # for a release older than v3.94.  verify_release checks sigmond's own
    # tag only for the first.
    source: str = "HamSCI/sigmond-proxmox"


def target_label(rel: Release) -> str:
    """How every output line names the target: `blessed v3.92`, or
    `CANDIDATE v3.92 (not blessed)` for a release candidate."""
    if rel.candidate:
        return f"CANDIDATE {rel.tag} (not blessed)"
    return f"blessed {rel.tag}"


def _preamble(text: str, key: str) -> Optional[str]:
    for line in text.splitlines():
        if line.startswith(key + ":"):
            return line.split(":", 1)[1].strip() or None
    return None


# The trust boundary: everything above this point is our own code; a
# manifest's component/sha rows and its self-declared tag come from a
# release asset on GitHub — reachable by anyone who can push a release
# to HamSCI/sigmond or the image repo. A malformed row must refuse the WHOLE manifest
# rather than let a partially-bad table drive a later `--apply`.
COMPONENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+(\.\d+)?$")


def fetch_release(tag: Optional[str] = None, urlopen: Optional[Callable] = None,
                  timeout: float = 20.0) -> Release:
    """The blessed release ``tag`` (default: the latest) and its manifest.

    Reads HamSCI/sigmond first.  Only when it answers "no such release"
    (ReleaseNotFound) does it try each of PROXMOX_SLUGS in turn; any other
    failure refuses at once.  Within each repo it asks the GitHub API
    first, and when the API refuses for its rate limit, reads the same
    release from github.com's pages instead.  The fallback covers finding
    the release, not reading its manifest: a release that names a
    manifest it cannot serve refuses."""
    if tag is not None and not RELEASE_TAG_RE.match(tag):
        raise LookupError_(f"not a release tag: {tag}")
    urlopen = urlopen or _default_urlopen
    for slug in (RELEASE_SLUG, *PROXMOX_SLUGS):
        try:
            tag_name, manifest_url = _release_in(slug, tag, urlopen, timeout)
            break
        except ReleaseNotFound:
            continue
    else:
        raise ReleaseNotFound(f"no such release: {tag or 'latest'} -- none on "
                              f"{', '.join((RELEASE_SLUG, *PROXMOX_SLUGS))}")
    try:
        text = _get(manifest_url, urlopen, timeout).decode("utf-8", "replace")
    except NotFound as exc:
        # The release exists and names this asset: a missing asset is a
        # broken release, not an absent one.
        raise LookupError_(f"release {tag_name} on {slug}: manifest asset missing ({exc})") from exc
    components = _parse_manifest_components(text)
    if components is None:
        raise LookupError_(f"release {tag_name}: manifest is missing or truncated")
    for name, sha in components.items():
        if not COMPONENT_NAME_RE.match(name) or not SHA_RE.match(sha):
            raise LookupError_(
                f"release {tag_name}: manifest entry {name!r} {sha!r} is malformed")
    preamble_tag = _preamble(text, "appliance_tag") or _preamble(text, "image_version")
    if preamble_tag is not None and preamble_tag != tag_name:
        raise LookupError_(
            f"release {tag_name}: manifest preamble tag {preamble_tag!r} does not "
            f"match the release tag {tag_name!r}")
    if slug == RELEASE_SLUG and "sigmond" not in components:
        # The sigmond tag names that row's commit; verify_release checks it.
        raise LookupError_(f"release {tag_name} on {slug}: manifest has no sigmond row")
    return Release(tag=tag_name, manifest_text=text,
                   appliance_commit=_preamble(text, "appliance_commit"),
                   components=components, source=slug)


def _release_in(slug: str, tag: Optional[str], urlopen, timeout: float) -> tuple:
    """(tag, manifest URL) of ``tag`` (or the latest) on ``slug``; raises
    ReleaseNotFound when that repo has none."""
    try:
        return _release_from_api(slug, tag, urlopen, timeout)
    except RateLimited as exc:
        return _release_from_web(slug, tag, urlopen, timeout, str(exc))


FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def load_candidate(path: str) -> Release:
    """A release candidate from the local manifest file at ``path`` -- the
    file the rig writes beside the image (``sigmond-<tag>-<date>-release.
    manifest.txt``, ``sigmond-appliance-...`` before v3.94), before any
    GitHub Release exists.

    The same checks as a fetched release, minus the Release itself: the
    components block parses and every row is well formed, the preamble
    names a release tag (appliance_tag, else image_version), and the
    preamble's appliance_commit is a full commit (image files are read at
    it).  Any failure refuses the whole file."""
    try:
        with open(path, "rb") as f:
            raw = f.read(_MAX_BODY + 1)
    except OSError as exc:
        raise LookupError_(f"cannot read candidate manifest {path}: {exc}") from exc
    if len(raw) > _MAX_BODY:
        raise LookupError_(f"candidate manifest {path} is larger than {_MAX_BODY} bytes")
    text = raw.decode("utf-8", "replace")
    tag = _preamble(text, "appliance_tag") or _preamble(text, "image_version")
    if tag is None:
        raise LookupError_(f"candidate manifest {path} names no appliance_tag or image_version")
    if not RELEASE_TAG_RE.match(tag):
        raise LookupError_(f"candidate manifest {path}: not a release tag: {tag!r}")
    image_version = _preamble(text, "image_version")
    if image_version is not None and image_version != tag:
        raise LookupError_(f"CANDIDATE {tag} (not blessed): image_version {image_version!r} does not "
                           f"match appliance_tag {tag!r}")
    components = _parse_manifest_components(text)
    if not components:
        raise LookupError_(f"CANDIDATE {tag} (not blessed): manifest has no usable components block "
                           f"(missing or truncated)")
    for name, sha in components.items():
        if not COMPONENT_NAME_RE.match(name) or not SHA_RE.match(sha):
            raise LookupError_(
                f"CANDIDATE {tag} (not blessed): manifest entry {name!r} {sha!r} is malformed")
    commit = _preamble(text, "appliance_commit")
    if commit is None or not FULL_SHA_RE.match(commit):
        raise LookupError_(f"CANDIDATE {tag} (not blessed): appliance_commit {commit!r} is not a full commit")
    return Release(tag=tag, manifest_text=text, appliance_commit=commit,
                   components=components, candidate=True, manifest_path=str(path))


def _release_from_api(slug: str, tag: Optional[str], urlopen, timeout: float) -> tuple:
    """(tag, manifest URL) from the GitHub API's release record on ``slug``.
    The API answers 404 for a missing tag, for /latest on a repo with no
    release, and for a repo that does not exist: all ReleaseNotFound."""
    api = releases_api(slug)
    url = f"{api}/tags/{tag}" if tag else f"{api}/latest"
    try:
        meta = json.loads(_get(url, urlopen, timeout))
    except ValueError as exc:
        raise LookupError_(f"{url} did not return JSON: {exc}") from exc
    if tag is None and not RELEASE_TAG_RE.match(str(meta.get("tag_name") or "")):
        # /latest names its own tag; it goes into the re-exec argv and the
        # records, so it gets the same check an operator-supplied tag does.
        raise LookupError_(f"not a release tag: {meta.get('tag_name')!r}")
    if meta.get("draft") or meta.get("prerelease"):
        raise LookupError_(f"release {meta.get('tag_name')} is a draft or prerelease — not blessed")
    asset = next((a for a in meta.get("assets", [])
                  if a.get("name", "").endswith(".manifest.txt")), None)
    if asset is None:
        raise LookupError_(f"release {meta.get('tag_name')} publishes no manifest asset")
    return meta.get("tag_name") or (tag or "?"), asset["browser_download_url"]


def _final_url(url: str, urlopen, timeout: float) -> str:
    """Where ``url`` lands after its redirects."""
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ReleaseNotFound(f"HTTP 404 from {url}") from exc
        raise LookupError_(f"HTTP {exc.code} from {url}") from exc
    except (urllib.error.URLError, OSError, http.client.HTTPException, TimeoutError) as exc:
        raise LookupError_(f"could not reach {url}: {exc}") from exc


def _release_from_web(slug: str, tag: Optional[str], urlopen, timeout: float,
                      why: str) -> tuple:
    """(tag, manifest URL) from ``slug``'s github.com pages, for when the API
    is rate-limited.

    /releases/latest redirects to the newest release that is neither draft
    nor prerelease, so it proves the latest blessed.  On a repo with no
    release it lands on /releases itself: ReleaseNotFound, as is a 404.
    Any other landing refuses.  Nothing on these pages proves a named older
    tag is no prerelease, so a named tag must be the latest; any other
    waits for the API, unless its asset page answers 404, which proves the
    repo has no such release."""
    web = releases_web(slug)
    landed = _final_url(f"{web}/latest", urlopen, timeout)
    if landed.rstrip("/") == web:
        raise ReleaseNotFound(f"{why}; {slug} has no release ({web}/latest led to {landed!r})")
    prefix = f"{web}/tag/"
    latest = landed[len(prefix):] if landed.startswith(prefix) else ""
    if not RELEASE_TAG_RE.match(latest):
        raise LookupError_(f"{why}; {web}/latest led to {landed!r}, not a release")
    if tag is not None and tag != latest:
        try:
            _get(f"{web}/expanded_assets/{tag}", urlopen, timeout)
        except ReleaseNotFound as exc:
            raise ReleaseNotFound(f"{why}; {slug} has no release {tag}") from exc
        except LookupError_:
            pass        # absence unproven: refuse as below, never fall back
        raise LookupError_(f"{why}; without the API only the latest release ({latest}) can "
                           f"be read, not {tag} — try again after the reset")
    try:
        page = _get(f"{web}/expanded_assets/{latest}", urlopen, timeout)
    except NotFound as exc:
        # /latest just named this release; its asset page cannot prove it absent.
        raise LookupError_(f"{why}; release {latest}'s asset list: {exc}") from exc
    links = sorted({href for href, t in manifest_href_re(slug).findall(
                        page.decode("utf-8", "replace"))
                    if t == latest})
    if len(links) != 1:
        raise LookupError_(f"{why}; release {latest}'s asset list names "
                           f"{len(links)} manifests, not one")
    return latest, "https://github.com" + links[0]


RADIOD = "ka9q-radio"
_DIRTY_NOTE = "uncommitted changes — commit, stash or discard first"
_UVLOCK_NOTE = "uv.lock will be reset"

# Shared libraries move before the components that import them, so no
# consumer is ever checked out against a library older than its pin.
LIBRARIES_FIRST = ("ka9q-python", "hamsci-dsp", "callhash", "hs-uploader")


def _plan_order(name: str) -> tuple:
    """sigmond, then LIBRARIES_FIRST in its own order, then the rest A-Z."""
    if name == "sigmond":
        return (0, 0, name)
    if name in LIBRARIES_FIRST:
        return (1, LIBRARIES_FIRST.index(name), name)
    return (2, 0, name)


def _dirt(value) -> tuple:
    """(refuses, uvlock_only) from a dirty-map value: a bool (the older
    form) or the list of dirty files. A list that is exactly uv.lock,
    once .pin is dropped, is the one dirt --apply resets rather than
    refuses."""
    if isinstance(value, (list, tuple)):
        files = [f for f in value if f != ".pin"]
        if files == ["uv.lock"]:
            return False, True
        return bool(files), False
    return bool(value), False


@dataclass(frozen=True)
class Item:
    component: str
    status: str            # current | move | refuse | missing | stray | local
    live: Optional[str]
    target: Optional[str]
    note: str = ""


# Never installed by --install-missing alone: it opens the station to the
# HamSCI remote-access path, and on a station reached only through another
# operator's tunnels (KJ6MKI via WD's gw1/gw2) that is a change in who can
# get in.  Enabled in topology, it installs like anything else.
NEVER_IMPLICIT = ("sigmond-rac",)


def missing_decision(name: str, *, enabled, install_missing: bool) -> tuple:
    """(install, reason) for a release component with no checkout here.

    `smd align --apply` used to install every such component: on KJ6MKI
    (bare metal, 2026-11-11) it put down codar-sounder, gmag-webui,
    gpsdo-monitor, hamsci-physics and hf-tec, and would have gone on to
    sigmond-rac, while the dry run only said "missing".  Now a missing
    component installs only when topology enables it, or when the operator
    asks with --install-missing (sigmond-rac excepted)."""
    if name in set(enabled or ()):
        return True, "would install (enabled in topology, not on this station)"
    if name in NEVER_IMPLICIT:
        return False, ("would skip: not on this station — never installed "
                       "implicitly (enable it in topology to install)")
    if install_missing:
        return True, "would install (not on this station; --install-missing)"
    return False, "would skip: not on this station (use --install-missing)"


def local_note(local) -> str:
    """The note a locally managed item carries: `locally managed (<by>) —
    left as is`."""
    label = local.label() if hasattr(local, "label") else "locally managed"
    return f"{label} — left as is"


def plan_align(release: Release, live: dict, dirty: dict, errors: Optional[dict] = None,
               local=None) -> list:
    """What aligning to ``release`` would do, component by component. Pure.

    ``dirty`` maps a component to its dirty files (a list) or, in the
    older form, a bool. Only dirt beyond a lone uv.lock refuses.

    ``local`` holds the components the site manages by hand (site-profile
    ``[components] locally_managed``, a ``site_profile.LocallyManaged``).
    Each one is a ``local`` item whatever its HEAD: never moved, installed,
    pinned or rebuilt, and nothing restarts because of it."""
    errors = errors or {}
    local = local or ()
    items = []
    for name, target in release.components.items():
        if name in local:
            items.append(Item(name, "local", live.get(name), target, local_note(local)))
            continue
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
        elif _dirt(dirty.get(name))[0]:
            items.append(Item(name, "refuse", head, target, _DIRTY_NOTE))
        else:
            notes = []
            if _dirt(dirty.get(name))[1]:
                notes.append(_UVLOCK_NOTE)
            items.append(Item(name, "move", head, target, "; ".join(notes)))
    strays = [Item(n, "local", h, None, local_note(local)) if n in local
              else Item(n, "stray", h, None, "not in the release manifest; left alone")
              for n, h in live.items() if n not in release.components]
    items.sort(key=lambda i: _plan_order(i.component))
    return items + sorted(strays, key=lambda i: i.component)


def native_rebuild_note(component: str, reason: str) -> str:
    """The dry run's tail for a `current` native component whose binary
    trails its checkout (``reason`` from smd's ``_align_native_stale``).
    ASCII only.  A radiod rebuild names its restart and the recording gap,
    as a moved ka9q-radio does."""
    note = f"-- {reason}; --apply REBUILDS"
    if component == RADIOD:
        from sigmond.align_live import RADIOD_GAP_ESTIMATE_S
        note += (f" and RESTARTS radiod -- recording gap of about "
                 f"{RADIOD_GAP_ESTIMATE_S} s")
    return note


AHEAD_NOTE = "ahead of the pin — left; --allow-rollback moves it back"
DIVERGED_NOTE = "diverged from the pin — resolve by hand"


def classify(items: list, is_ancestor: Callable) -> list:
    """Turn each 'move' into forward / ahead / diverged. Pure; ancestry is injected."""
    out = []
    for it in items:
        if it.status != "move":
            out.append(it)
            continue
        fwd = is_ancestor(it.component, it.live, it.target)
        back = is_ancestor(it.component, it.target, it.live)
        if fwd is None or back is None:
            out.append(Item(it.component, "refuse", it.live, it.target, "ancestry unknown — run a fetch first"))
        elif fwd:
            out.append(Item(it.component, "forward", it.live, it.target, it.note))
        elif back:
            out.append(Item(it.component, "ahead", it.live, it.target, AHEAD_NOTE))
        else:
            out.append(Item(it.component, "diverged", it.live, it.target, DIVERGED_NOTE))
    return out


# Image-carried host files come from the image repo, not HamSCI/sigmond:
# they belong to the image.  The second base is the repo's name before the
# rename, tried on a 404 alone; it goes with the mirror.
RAW_BASES = tuple(f"https://raw.githubusercontent.com/{s}" for s in PROXMOX_SLUGS)
RAW_BASE = RAW_BASES[0]
# sigmond-site-timing left this list on 2026-11-15: it lives in sigmond now,
# and /usr/local/sbin links to the checkout (align_apply.link_site_timing).
IMAGE_FILES = (
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


def _get_first(urls: tuple, urlopen, timeout: float) -> bytes:
    """The body of the first of ``urls`` that is there.  Moves on after a
    404 alone; any other failure raises at once."""
    for url in urls[:-1]:
        try:
            return _get(url, urlopen, timeout)
        except NotFound:
            continue
    return _get(urls[-1], urlopen, timeout)


def image_file_drift(ref: str, read_local: Optional[Callable] = None,
                     urlopen: Optional[Callable] = None, timeout: float = 20.0) -> list:
    """Each image-carried file against the image repo's (sigmond-proxmox's)
    copy at ``ref``, from each of RAW_BASES in turn on a 404 alone.

    The dry run passes the release tag (all it has before verification);
    ``--apply`` passes the release's appliance_commit, already verified to
    be what the tag points at, so the compare and the refresh read the
    very bytes the verification covered (final review / I7)."""
    read_local = read_local or _read_local
    out = []
    for vm_path, repo_path in IMAGE_FILES:
        try:
            want = _get_first(tuple(f"{b}/{ref}/{repo_path}" for b in RAW_BASES),
                              urlopen or _default_urlopen, timeout)
        except LookupError_ as exc:
            out.append({"path": vm_path, "status": "unknown", "note": str(exc)})
            continue
        have = read_local(vm_path)
        if have is None:
            out.append({"path": vm_path, "status": "absent", "note": "not installed"})
        elif hashlib.sha256(have).digest() == hashlib.sha256(want).digest():
            out.append({"path": vm_path, "status": "current", "note": ""})
        else:
            out.append({"path": vm_path, "status": "differs", "note": f"differs from {ref}"})
    return out


def recorded_release(path: str = "/etc/sigmond-appliance/version") -> Optional[str]:
    text = _read_local(path)
    if not text:
        return None
    parts = text.decode("utf-8", "replace").split()
    return parts[0] if parts else None

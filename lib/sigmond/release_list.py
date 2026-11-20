"""sigmond.release_list -- the one owner of release/components.toml.

The list names the commit of every component a sigmond release ships.  smd
verbs, the rig's shell scripts (through ``python3 -m sigmond.release_list``)
and bless all read and compare it here.  Standard library only, like the
rest of core smd.
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass
from typing import Callable, Optional

from sigmond.align import COMPONENT_NAME_RE, RELEASE_TAG_RE, _preamble
from sigmond.doctor import _parse_manifest_components, _sha_equal

ROWS = (
    "callhash", "codar-sounder", "ft8_lib", "gmag-webui", "gpsdo-monitor",
    "hamsci-dsp", "hamsci-physics", "hf-tec", "hf-timestd", "hfdl-recorder",
    "hs-uploader", "igmp-querier", "ka9q-python", "ka9q-radio", "ka9q-web",
    "mag-recorder", "meteor-scatter", "onion", "psk-recorder",
    "sigmond-proxmox", "sigmond-rac", "station-web", "superdarn-sounder",
    "ubersdr_wsprd", "wsjtx", "wspr-recorder",
)
PROXMOX_ROW = "sigmond-proxmox"
NATIVE_CONSTANTS = {"onion": "_ONION_COMMIT", "wsjtx": "_WSJTX_COMMIT",
                    "ubersdr_wsprd": "_UBERSDR_COMMIT"}

HEADER = (
    "# sigmond release list -- written by `smd admin release-list --write`, reviewed, committed.\n"
    "# The tag vN on HamSCI/sigmond names sigmond's own commit; sigmond has no row here.\n"
)

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class ReleaseListError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseList:
    version: str
    components: dict


@dataclass(frozen=True)
class Mismatch:
    component: str
    want: Optional[str]     # None: the list has no such row
    have: Optional[str]     # None: no such checkout or manifest row
    note: str


def parse_list(text: str) -> ReleaseList:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ReleaseListError(f"release list is not valid TOML: {e}")
    version = data.get("version")
    if not isinstance(version, str) or not RELEASE_TAG_RE.match(version):
        raise ReleaseListError(f"version {version!r} is not a release tag like v3.94")
    comps = data.get("components")
    if not isinstance(comps, dict):
        raise ReleaseListError("release list has no [components] table")
    for name, value in comps.items():
        if name == "sigmond":
            raise ReleaseListError("sigmond has no row: the tag names sigmond's own commit")
        if not COMPONENT_NAME_RE.match(name):
            raise ReleaseListError(f"row name {name!r} is not a component name")
        if not isinstance(value, str) or not _FULL_SHA.match(value):
            raise ReleaseListError(f"row {name}: {value!r} is not a full 40-hex lowercase commit")
    if PROXMOX_ROW not in comps:
        raise ReleaseListError(f"row {PROXMOX_ROW} is missing")
    return ReleaseList(version=version, components=dict(comps))


def load_list(path) -> ReleaseList:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise ReleaseListError(f"cannot read release list {path}: {e}")
    return parse_list(text)


def format_list(rl: ReleaseList) -> str:
    out = [HEADER, f'version = "{rl.version}"\n', "\n[components]\n"]
    for name in sorted(rl.components):
        out.append(f'{name} = "{rl.components[name]}"\n')
    return "".join(out)


def native_constants(smd_text: str) -> dict:
    out = {}
    for name, const in NATIVE_CONSTANTS.items():
        m = re.search(r"^%s\s*=\s*'([0-9a-f]{40})'" % const, smd_text, re.M)
        if not m:
            raise ReleaseListError(f"{name}: no full {const} in bin/smd")
        out[name] = m.group(1)
    return out


def ka9q_radio_from_compat(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if not _FULL_SHA.match(s):
            raise ReleaseListError(f"ka9q_radio_compat: {s!r} is not a full commit")
        return s
    raise ReleaseListError("ka9q_radio_compat holds no commit")


def ft8lib_from_install_sh(text: str) -> str:
    m = re.search(r'^FT8LIB_PIN="([0-9a-f]{40})"', text, re.M)
    if not m:
        raise ReleaseListError("install.sh has no full FT8LIB_PIN")
    return m.group(1)


def compare_live(rl: ReleaseList, live: dict, *, require_all: bool = False) -> list:
    out = []
    skip = ("sigmond", PROXMOX_ROW)
    for name in sorted(rl.components):
        if name in skip:
            continue
        want = rl.components[name]
        have = live.get(name)
        if have is None:
            if require_all:
                out.append(Mismatch(name, want, None, "listed but not checked out"))
        elif not _sha_equal(want, have):
            out.append(Mismatch(name, want, have, "checkout differs from the list"))
    for name in sorted(live):
        if name != "sigmond" and name not in rl.components:
            out.append(Mismatch(name, None, live[name], "checked out but not on the list"))
    return out


def compare_manifest(rl: ReleaseList, manifest_text: str) -> list:
    rows = _parse_manifest_components(manifest_text)
    if rows is None:
        raise ReleaseListError("manifest has no readable components (live) block")
    rows = {n: s for n, s in rows.items() if n != "sigmond"}
    pre = _preamble(manifest_text, "appliance_commit")
    if pre:
        rows[PROXMOX_ROW] = pre
    out = []
    for name in sorted(rl.components):
        want = rl.components[name]
        have = rows.get(name)
        if have is None:
            out.append(Mismatch(name, want, None, "listed but not in the manifest"))
        elif not _sha_equal(want, have):
            out.append(Mismatch(name, want, have, "manifest differs from the list"))
    for name in sorted(rows):
        if name not in rl.components:
            out.append(Mismatch(name, None, rows[name], "in the manifest but not on the list"))
    return out


EXTRA_URLS = {
    "ka9q-python": "https://github.com/HamSCI/ka9q-python",
    "sigmond-proxmox": "https://github.com/HamSCI/sigmond-proxmox",
}


def repo_urls(catalog: dict) -> dict:
    """Clone URL of every row that has a head of its own.  The owner-derived
    rows (NATIVE_CONSTANTS, ka9q-radio, ft8_lib) have no entry."""
    out = {}
    derived = set(NATIVE_CONSTANTS) | {"ka9q-radio", "ft8_lib"}
    for name in ROWS:
        if name in derived:
            continue
        if name in EXTRA_URLS:
            out[name] = EXTRA_URLS[name]
        elif name in catalog and getattr(catalog[name], "repo", None):
            out[name] = catalog[name].repo
    return out


def compute_list(version: str, *, urls: dict, smd_text: str,
                 remote_head: Callable[[str], str],
                 fetch_file: Callable[[str, str, str], str]) -> ReleaseList:
    """Build the list: heads from the remote, native pins from their owners."""
    if not RELEASE_TAG_RE.match(version or ""):
        raise ReleaseListError(f"version {version!r} is not a release tag like v3.94")
    comps = {}
    for name, url in urls.items():
        sha = remote_head(url)
        if not sha:
            raise ReleaseListError(f"{name}: no head from {url}")
        comps[name] = sha
    comps.update(native_constants(smd_text))
    for name in ("ka9q-python", "psk-recorder"):
        if name not in comps:
            raise ReleaseListError(f"{name}: no head, so its native pin cannot be read")
    comps["ka9q-radio"] = ka9q_radio_from_compat(
        fetch_file(urls["ka9q-python"], comps["ka9q-python"], "ka9q_radio_compat"))
    comps["ft8_lib"] = ft8lib_from_install_sh(
        fetch_file(urls["psk-recorder"], comps["psk-recorder"], "scripts/install.sh"))
    missing = sorted(set(ROWS) - set(comps))
    if missing:
        raise ReleaseListError("no source for row(s): " + ", ".join(missing))
    return ReleaseList(version=version, components={n: comps[n] for n in ROWS})


def _read_list(arg: str) -> ReleaseList:
    if arg == "-":
        return parse_list(sys.stdin.read())
    return load_list(arg)


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m sigmond.release_list")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("version", help="print the list's version")
    v.add_argument("--list", required=True, metavar="FILE|-")
    c = sub.add_parser("check", help="compare the list with a manifest")
    c.add_argument("--list", required=True, metavar="FILE|-")
    c.add_argument("--manifest", required=True, metavar="FILE")
    args = ap.parse_args(argv)
    try:
        rl = _read_list(args.list)
        if args.cmd == "version":
            print(rl.version)
            return 0
        try:
            with open(args.manifest, encoding="utf-8") as f:
                mtext = f.read()
        except (OSError, UnicodeDecodeError) as e:
            raise ReleaseListError(f"cannot read manifest {args.manifest}: {e}")
        bad = compare_manifest(rl, mtext)
    except ReleaseListError as e:
        print(f"release_list: {e}".encode("ascii", "replace").decode(), file=sys.stderr)
        return 2
    for m in bad:
        print(f"{m.component}: list={m.want or '-'} manifest={m.have or '-'} ({m.note})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

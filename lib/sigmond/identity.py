"""Station identity — the files a station minted that others have learned to trust.

Host keys, the uploader key, the RAC credential.  When a reflash mints new ones,
every party that trusted the old ones stops trusting the station: operators'
known_hosts, the wd30 heartbeat account, PSWS, gw2.  This module holds the ONE
list of those files.  Capture reads it now; the installer's restore will read it
in Plan 2, so the two can never drift apart.

Standard library only, Python 3.11+.  ops/bin/site-identity pipes this file's
source to `python3 -` on stations that may carry no sigmond at all — the
Proxmox host has none.  It only reads.

Bundle format (schema 1)
------------------------
A plane's tar (what `export()` writes) holds files only, no directory
members, so a restore must create the parent directories itself, with the
right owner and mode: `/home/timestd/.ssh` 0700 timestd, and
`/etc/hs-uploader/keys`.  Tar member names are root-relative with no leading
slash (`etc/ssh/ssh_host_...`); the matching manifest entry's `path` field is
the same name WITH a leading slash.

Each member carries both the owner/group NAMES and the station-local numeric
ids tar always stores.  A restore sets ownership by NAME from the manifest,
and fails loudly when a name does not resolve on the new station, rather
than falling back to the numeric id, which names a different account there.
For the same reason a restore must never extract with tarfile's `data`
filter (Python 3.12's new default `filter=` behaviour) — it strips
uid/gid/uname/gname from every member.

`identity/manifest.json` is always the tar's last member.  On the pm plane a
further member, `identity/rac-credential.json`, holds the RAC credential's
three fields flattened: `user`, `auth.method`, `auth.token`.

The devbox's outer bundle — what `capture` in ops/bin/site-identity encrypts
— wraps both planes' tars unchanged, as `pm.tar` and `vm.tar`.

Design: sigmond-appliance/docs/superpowers/specs/2026-09-24-station-identity-design.md
"""
from __future__ import annotations

import argparse
import base64
import glob
import grp
import hashlib
import io
import json
import os
import pwd
import stat
import sys
import tarfile
import tomllib

SCHEMA = 1
PLANES = ("vm", "pm")

# Root-relative glob patterns, per plane.  known_hosts and authorized_keys stay
# out: they record whom the station trusts, not who the station is.
FILE_PATTERNS: dict[str, tuple[str, ...]] = {
    "vm": (
        "etc/ssh/ssh_host_*",
        "etc/hs-uploader/keys/id_*",
        "home/timestd/.ssh/id_*",
    ),
    "pm": (
        "etc/ssh/ssh_host_*",
        # Rides every RAC login as [metadatas] pubkey, and the VM's sigmond
        # account trusts it for the PM -> VM hop.
        "root/.ssh/id_ed25519",
        "root/.ssh/id_ed25519.pub",
        # The pm-heartbeat emitter's SFTP key, authorized on the fleetboard
        # server (sigmond-appliance#9).
        "etc/pm-heartbeat/id_ed25519",
        "etc/pm-heartbeat/id_ed25519.pub",
    ),
}

RAC_CONFIG = "etc/sigmond/frpc-host.toml"            # pm only
RAC_FIELDS = (("user",), ("auth", "method"), ("auth", "token"))
RAC_MEMBER = "identity/rac-credential.json"
MANIFEST_MEMBER = "identity/manifest.json"


def _check_plane(plane: str) -> None:
    if plane not in PLANES:
        raise ValueError(f"unknown plane {plane!r}; expected one of {PLANES}")


def matched_files(plane: str, root: str) -> list[str]:
    """Regular files (never symlinks) matching the plane's patterns, root-relative."""
    _check_plane(plane)
    found = set()
    for pattern in FILE_PATTERNS[plane]:
        for path in glob.glob(os.path.join(root, pattern)):
            if os.path.isfile(path) and not os.path.islink(path):
                found.add(os.path.relpath(path, root))
    return sorted(found)


def ssh_fingerprint(pub_text: str) -> str | None:
    """The SHA256:... fingerprint `ssh-keygen -lf` prints, from a .pub line."""
    parts = pub_text.split()
    if len(parts) < 2:
        return None
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError:
        return None
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode()
    return "SHA256:" + digest.rstrip("=")


def _name(lookup, ident: int) -> str:
    try:
        return lookup(ident)[0]
    except KeyError:
        return str(ident)


def _read_entry(root: str, rel: str) -> tuple[dict, bytes]:
    """The manifest entry AND the file's bytes, from one read.

    export() needs both: the entry for the manifest, the bytes for the tar
    member.  Returning them together from one read keeps the two from ever
    describing different content.
    """
    path = os.path.join(root, rel)
    st = os.stat(path)
    with open(path, "rb") as f:
        data = f.read()
    entry = {
        "path": "/" + rel,
        "owner": _name(pwd.getpwuid, st.st_uid),
        "group": _name(grp.getgrgid, st.st_gid),
        "mode": format(stat.S_IMODE(st.st_mode), "04o"),
        # A digest of a private key reveals nothing, and lets check() see a
        # changed private half even where no .pub sits beside it.
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if rel.endswith(".pub"):
        fp = ssh_fingerprint(data.decode("utf-8", "replace"))
        if fp:
            entry["ssh_fingerprint"] = fp
    return entry, data


def _file_entry(root: str, rel: str) -> dict:
    entry, _ = _read_entry(root, rel)
    return entry


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def rac_credential(root: str) -> dict | None:
    """The credential lines of frpc-host.toml, keyed 'user', 'auth.method', 'auth.token'.

    Only these.  The proxy list gets regenerated from the station's layout, and
    a restored list could contradict a changed layout.
    """
    path = os.path.join(root, RAC_CONFIG)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    cred = {}
    for keys in RAC_FIELDS:
        node = doc
        for key in keys:
            node = node.get(key) if isinstance(node, dict) else None
        if node is not None:
            cred[".".join(keys)] = node
    return cred or None


def manifest(plane: str, root: str = "/") -> dict:
    """Describe the plane's identity without carrying any secret value."""
    _check_plane(plane)
    m = {
        "schema": SCHEMA,
        "plane": plane,
        "files": [_file_entry(root, rel) for rel in matched_files(plane, root)],
    }
    if plane == "pm":
        cred = rac_credential(root)
        m["rac_credential"] = None if cred is None else {
            "fields": sorted(cred),
            "sha256": hashlib.sha256(_canonical(cred)).hexdigest(),
        }
    return m


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.uname = info.gname = "root"
    tar.addfile(info, io.BytesIO(data))


def export(plane: str, out, root: str = "/") -> dict:
    """Write the plane's identity as an uncompressed tar stream; return its manifest.

    Members keep their owner, group and mode, so a restore can put them back
    exactly.  The manifest goes last, as identity/manifest.json.

    Each file is read once: the same bytes back both the manifest's sha256
    and the tar member, so the two can never disagree about what got
    exported.  Same for the RAC credential on the pm plane.
    """
    _check_plane(plane)
    files = []
    with tarfile.open(fileobj=out, mode="w|") as tar:
        for rel in matched_files(plane, root):
            entry, data = _read_entry(root, rel)
            files.append(entry)
            path = os.path.join(root, rel)
            tar.addfile(tar.gettarinfo(path, arcname=rel), io.BytesIO(data))
        m = {"schema": SCHEMA, "plane": plane, "files": files}
        if plane == "pm":
            cred = rac_credential(root)
            m["rac_credential"] = None if cred is None else {
                "fields": sorted(cred),
                "sha256": hashlib.sha256(_canonical(cred)).hexdigest(),
            }
            if cred is not None:
                _add_bytes(tar, RAC_MEMBER, _canonical(cred), 0o600)
        _add_bytes(tar, MANIFEST_MEMBER,
                   json.dumps(m, indent=2, sort_keys=True).encode(), 0o644)
    return m


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="identity", description="Read a station plane's identity files.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    for verb, text in (("export", "write the identity tar to stdout"),
                       ("fingerprints", "print the manifest (no secrets) as JSON")):
        p = verbs.add_parser(verb, help=text)
        p.add_argument("--plane", required=True, choices=PLANES)
        p.add_argument("--root", default="/")
    args = parser.parse_args(argv)
    if args.verb == "fingerprints":
        json.dump(manifest(args.plane, args.root), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    export(args.plane, sys.stdout.buffer, args.root)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

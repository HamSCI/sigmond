"""Upload-credential readiness, reported per upload PATH.

A recorder writes its spots / observations to the local SQLite sink (or
Digital RF buffer) and runs perfectly well *regardless* of upload
credentials — only the UPLOAD step needs them.  So a missing credential is
not a fault in the recorder; it's "upload information missing" on a
specific upload path.  This module is the single source of truth for which
upload paths need credentials, whether those credentials are present, and
exactly what's missing — so `smd component` and `smd admin validate` can
surface it without mislabeling a green, happily-recording daemon.

Credential requirements by path (see hs-uploader / recorder code):
  * wsprnet.org      (wspr-recorder)  — anonymous POST, NO credentials.
  * wsprdaemon.org   (wspr-recorder)  — SFTP via the shared hs-uploader
                                        ed25519 key (/etc/hs-uploader/keys).
  * PSKReporter      (psk-recorder)   — open TCP, NO credentials.
  * PSWS             (hf-timestd)     — SFTP; needs station id + instrument
                                        id + an SFTP private key.
  * PSWS             (mag-recorder)   — SFTP; needs a PSWS station id
                                        (+ instrument id, defaulted RM3100).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover  (py<3.11)
    tomllib = None  # type: ignore

# The uploader manifest (uploader_manifest.py) writes ssh_key_file =
# .../id_ed25519_host into pipelines.toml; the legacy shared name is
# .../id_ed25519.  Check the CONFIGURED path when the manifest exists,
# else accept either name — a station whose key is at the _host path must
# not be reported as "missing hs-uploader SSH key .../id_ed25519".
HS_UPLOADER_MANIFEST    = Path("/etc/hs-uploader/pipelines.toml")
HS_UPLOADER_KEY         = Path("/etc/hs-uploader/keys/id_ed25519")
HS_UPLOADER_KEY_HOST    = Path("/etc/hs-uploader/keys/id_ed25519_host")
HF_TIMESTD_CONFIG  = Path("/etc/hf-timestd/timestd-config.toml")
MAG_CONFIG         = Path("/etc/mag-recorder/mag-recorder-config.toml")
WSPR_ETC           = Path("/etc/wspr-recorder")
PSK_ETC            = Path("/etc/psk-recorder")

# Config placeholders the install templates leave behind look like
# "<YOUR_STATION_ID>" / "<YOUR_PSWS_STATION_ID>".
_PLACEHOLDER_PREFIX = "<YOUR"


# Per-recorder remediation for a missing PSWS / SFTP upload identity — the
# concrete command(s) the operator runs to enter the values.  Surfaced
# verbatim next to "what's missing" so the message is self-documenting.
_FIX = {
    "wspr-recorder":
        "the hs-uploader key self-generates on first upload; "
        "force it with `smd admin secrets`",
    "hf-timestd":
        "smd config edit hf-timestd  (set [station] id + instrument_id),  then  "
        "sudo bash /opt/git/sigmond/hf-timestd/scripts/setup-psws-keys.sh  "
        "(prints the public key to register at https://pswsnetwork.eng.ua.edu/)",
    "mag-recorder":
        "smd config edit mag-recorder  (set [station] psws_station_id; "
        "instrument_id defaults RM3100)",
}


@dataclass
class UploadPath:
    """Credential readiness for one (recorder → destination) upload path."""
    path: str           # destination label, e.g. "wsprdaemon.org"
    recorder: str       # the component that produces the data
    needs_creds: bool   # does this path require any credentials/identity?
    ready: bool         # are the required credentials/identity present?
    missing: str        # what's missing (empty when ready or no creds needed)
    fix: str = ""       # how to provide the missing creds (concrete command)


def _is_placeholder(v: object) -> bool:
    """True for an empty value or an unedited "<YOUR_…>" template default."""
    s = "" if v is None else str(v).strip()
    return (not s) or s.startswith(_PLACEHOLDER_PREFIX)


# Credentials/configs are service-user-owned (e.g. timestd-config.toml is
# 0640 timestd; the hs-uploader key sits under a 0700 dir).  `smd component`
# runs as the operator, so direct reads hit EACCES — fall back to passwordless
# `sudo -n`.  When already root (e.g. `smd admin validate`), the direct read
# succeeds and sudo is never invoked.
def _sudo_prefix() -> list[str]:
    return [] if os.geteuid() == 0 else ["sudo", "-n"]


def _read_text(p: Path) -> str | None:
    try:
        return p.read_text()
    except PermissionError:
        try:
            r = subprocess.run([*_sudo_prefix(), "cat", str(p)],
                               capture_output=True, text=True, check=False)
            return r.stdout if r.returncode == 0 else None
        except FileNotFoundError:
            return None
    except OSError:
        return None


def _exists(p: Path) -> bool:
    try:
        return p.exists()
    except PermissionError:
        try:
            r = subprocess.run([*_sudo_prefix(), "test", "-e", str(p)],
                               capture_output=True, check=False)
            return r.returncode == 0
        except FileNotFoundError:
            return False
    except OSError:
        return False


def _load_toml(p: Path) -> dict:
    if tomllib is None:
        return {}
    text = _read_text(p)
    if text is None:
        return {}
    try:
        return tomllib.loads(text)
    except ValueError:
        return {}


def _hs_uploader_key_status() -> tuple[bool, Path]:
    """(ready, checked_path) for the hs-uploader SFTP key.

    The manifest's ``[identity] ssh_key_file`` is authoritative when
    pipelines.toml exists; without it, EITHER the ``_host``-suffixed key
    (what the manifest generator writes) or the legacy shared name counts
    as present."""
    configured = ((_load_toml(HS_UPLOADER_MANIFEST).get("identity", {}) or {})
                  .get("ssh_key_file"))
    if configured:
        p = Path(str(configured))
        return _exists(p), p
    for p in (HS_UPLOADER_KEY_HOST, HS_UPLOADER_KEY):
        if _exists(p):
            return True, p
    return False, HS_UPLOADER_KEY_HOST


def upload_paths_status() -> list[UploadPath]:
    """Per-path upload-credential readiness for the recorders installed on
    this host.  Only reports a path when its owning recorder is installed
    (config dir / config file present), so a host without a component shows
    no spurious "missing credential" lines."""
    out: list[UploadPath] = []

    # wspr-recorder → wsprnet.org (no creds) + wsprdaemon.org (hs-uploader key)
    if WSPR_ETC.is_dir():
        out.append(UploadPath("wsprnet.org", "wspr-recorder",
                              needs_creds=False, ready=True, missing=""))
        ready, key_path = _hs_uploader_key_status()
        out.append(UploadPath(
            "wsprdaemon.org", "wspr-recorder", needs_creds=True, ready=ready,
            missing="" if ready else f"hs-uploader SSH key {key_path}",
            fix="" if ready else _FIX["wspr-recorder"]))

    # psk-recorder → PSKReporter (no creds)
    if PSK_ETC.is_dir():
        out.append(UploadPath("PSKReporter", "psk-recorder",
                              needs_creds=False, ready=True, missing=""))

    # hf-timestd → PSWS (station id + instrument id + SFTP key)
    if HF_TIMESTD_CONFIG.exists():
        d = _load_toml(HF_TIMESTD_CONFIG)
        st = d.get("station", {}) or {}
        miss: list[str] = []
        if _is_placeholder(st.get("id")):
            miss.append("station id")
        if _is_placeholder(st.get("instrument_id")):
            miss.append("instrument id")
        key = ((d.get("uploader", {}) or {}).get("sftp", {}) or {}).get("ssh_key")
        if key and not _exists(Path(str(key))):
            miss.append(f"SFTP key {key}")
        out.append(UploadPath("PSWS (hf-timestd)", "hf-timestd",
                              needs_creds=True, ready=not miss,
                              missing=", ".join(miss),
                              fix="" if not miss else _FIX["hf-timestd"]))

    # mag-recorder → PSWS (psws_station_id + instrument id)
    if MAG_CONFIG.exists():
        d = _load_toml(MAG_CONFIG)
        st = d.get("station", {}) or {}
        miss = []
        if _is_placeholder(st.get("psws_station_id")):
            miss.append("PSWS station id")
        if _is_placeholder(st.get("instrument_id")):
            miss.append("instrument id")
        out.append(UploadPath("PSWS (mag-recorder)", "mag-recorder",
                              needs_creds=True, ready=not miss,
                              missing=", ".join(miss),
                              fix="" if not miss else _FIX["mag-recorder"]))

    return out


def missing_upload_paths() -> list[UploadPath]:
    """Just the paths that need credentials and don't have them."""
    return [p for p in upload_paths_status() if p.needs_creds and not p.ready]

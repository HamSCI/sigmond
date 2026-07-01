"""``smd admin uploader manifest`` — generate the single-host uploader manifest.

Renders ``/etc/hs-uploader/pipelines.toml`` from every enabled client's
``deploy.toml`` ``[[hs_uploader.pipeline]]`` declarations, with per-site
identity substituted (see :mod:`sigmond.uploader_manifest`).

Modes (default is the read-only check):

* ``--check`` / (no flag) — render and diff against the installed manifest;
  exit non-zero on drift.  Read-only, no root.
* ``--write`` — write the manifest (root); back up any existing file to ``.bak``.
* ``--enable`` — write, then ensure ``hs-uploader.service`` is installed +
  enabled + running (restart it when the manifest actually changed).
"""
from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from .. import uploader_manifest as um

SERVICE = "hs-uploader.service"
_UNIT_SRC = Path("/opt/git/sigmond/hs-uploader/systemd/hs-uploader.service")
_UNIT_DST = Path("/etc/systemd/system/hs-uploader.service")
_INSTALL_SH = Path("/opt/git/sigmond/hs-uploader/install.sh")
_VENV = Path("/opt/hs-uploader/venv")


def _err(msg: str) -> None:
    print(f"smd: {msg}", file=sys.stderr)


def _strip_secrets(obj):
    """Drop keys that are deliberately omitted from the generated manifest
    (ftp_password is code-defaulted) so the semantic diff doesn't flag them."""
    if isinstance(obj, dict):
        return {k: _strip_secrets(v) for k, v in obj.items()
                if k != "ftp_password"}
    if isinstance(obj, list):
        return [_strip_secrets(x) for x in obj]
    return obj


def _semantic(text: str):
    """Parsed manifest normalized for a *functional* comparison: secrets
    stripped and the pipeline array sorted by name (order is irrelevant — each
    pipeline is independent, keyed by its derived source_id/dest_id).  Used to
    decide whether a regenerate is a real change (restart) vs comment/order
    churn (no restart)."""
    try:
        data = _strip_secrets(tomllib.loads(text))
    except tomllib.TOMLDecodeError:
        return None
    if isinstance(data.get("pipeline"), list):
        data["pipeline"] = sorted(
            data["pipeline"], key=lambda p: str(p.get("name", "")))
    return data


def _run(cmd: list) -> int:
    return subprocess.run(cmd, check=False).returncode


def _service_active() -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", SERVICE],
                          check=False).returncode == 0


def _ensure_daemon_installed() -> bool:
    """Make sure the unit + hsupload user + venv exist.  Runs the sibling
    install.sh (idempotent) when the venv or user is missing.  Returns True
    when the daemon is ready to enable."""
    have_user = subprocess.run(["getent", "passwd", "hsupload"],
                               capture_output=True, check=False).returncode == 0
    if (not have_user or not _VENV.exists()) and _INSTALL_SH.exists():
        print(f"uploader: bootstrapping hs-uploader daemon via {_INSTALL_SH}")
        _run(["bash", str(_INSTALL_SH)])
    if not _UNIT_DST.exists() and _UNIT_SRC.exists():
        shutil.copy2(_UNIT_SRC, _UNIT_DST)
        _run(["systemctl", "daemon-reload"])
    if not _UNIT_DST.exists():
        _err(f"{SERVICE} unit not found and no source at {_UNIT_SRC} — "
             "install hs-uploader first")
        return False
    return True


def cmd_uploader_manifest(args) -> int:
    write = bool(getattr(args, "write", False) or getattr(args, "enable", False))
    enable = bool(getattr(args, "enable", False))

    try:
        text = um.generate()
    except Exception as exc:  # pragma: no cover - defensive
        _err(f"uploader manifest generation failed: {exc}")
        return 1

    path = um.MANIFEST_PATH
    installed = path.read_text() if path.exists() else ""
    changed = _semantic(text) != _semantic(installed)

    if not write:
        # read-only check
        if not installed:
            print(f"uploader: no manifest at {path} — would create "
                  f"{text.count('[[pipeline]]')} pipeline(s)")
            return 1
        if not changed:
            print(f"uploader: {path} is up to date "
                  f"({text.count('[[pipeline]]')} pipeline(s))")
            return 0
        print(f"uploader: {path} DRIFT — `--write` would change it:\n")
        diff = difflib.unified_diff(
            installed.splitlines(), text.splitlines(),
            fromfile=f"{path} (installed)", tofile="generated", lineterm="")
        for line in diff:
            print(line)
        return 1

    # write path — needs root
    if os.geteuid() != 0:
        _err("writing the manifest requires root (sudo smd admin uploader "
             "manifest --write)")
        return 1

    path.parent.mkdir(parents=True, exist_ok=True)
    n = text.count("[[pipeline]]")
    if installed == text:
        print(f"uploader: {path} already current ({n} pipeline(s))")
    else:
        # Back up on any byte change (even a semantic no-op — comment/order
        # churn), so the previous manifest is always recoverable.
        if installed:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        path.write_text(text)
        if changed:
            print(f"uploader: wrote {path} ({n} pipeline(s))")
        else:
            print(f"uploader: refreshed {path} "
                  f"({n} pipeline(s); no functional change)")

    if not enable:
        return 0

    if not _ensure_daemon_installed():
        return 1
    was_active = _service_active()
    _run(["systemctl", "enable", "--now", SERVICE])
    if was_active and changed:
        print(f"uploader: manifest changed — restarting {SERVICE}")
        _run(["systemctl", "restart", SERVICE])
    return 0

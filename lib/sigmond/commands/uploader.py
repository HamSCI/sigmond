"""``smd admin uploader manifest`` — generate the single-host uploader manifest.

Renders ``/etc/hs-uploader/pipelines.toml`` from every enabled client's
``deploy.toml`` ``[[hs_uploader.pipeline]]`` declarations, with per-site
identity substituted (see :mod:`sigmond.uploader_manifest`).

Modes (default is the read-only check):

* ``--check`` / (no flag) — render and diff against the installed manifest;
  exit non-zero on drift.  Read-only, no root.
* ``--write`` — write the manifest (root); back up any existing file to
  ``.bak``; then run ``hs-uploader migrate`` as ``hsupload``.
* ``--enable`` — install the daemon first when it is missing (the render
  probes its venv), write, migrate, then ensure ``hs-uploader.service`` is
  enabled + running (restart it when the manifest actually changed).

``hs-uploader migrate`` brings ``watermarks.db`` to the schema the
installed daemon expects.  It runs after the write and before anything
starts or restarts the daemon, because the daemon never migrates while it
starts (tasks/plan-sink-control.md D10).  A failed migrate leaves the
daemon as it stands and exits 1.
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
# Longer than hs-uploader migrate's own 30 s wait for another writer's lock,
# with room for the interpreter to start on a busy station.
_MIGRATE_TIMEOUT_S = 120


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


def _run_migrate() -> int:
    """Run the daemon's own ``hs-uploader migrate`` as ``hsupload``.

    The daemon never migrates while it starts (tasks/plan-sink-control.md
    D10), so this runs after the manifest write and before any start or
    restart.  It runs as ``hsupload``, the daemon's account, in the unit's
    working directory.  So no file SQLite creates beside ``watermarks.db``,
    such as its rollback journal, ever belongs to root (bee1, 2026-05-12:
    "attempt to write a readonly database").

    Returns 0 when migrate finished or found nothing to do.  Also returns 0,
    with a line saying so, when no daemon venv exists or the installed
    hs-uploader predates ``migrate`` (argparse exits 2 with ``invalid
    choice: 'migrate'``).  Returns 1 on any other failure, after saying
    why."""
    exe = _VENV / "bin" / "hs-uploader"
    if not exe.exists():
        print(f"uploader: no {exe}; send-record migrate skipped")
        return 0
    runuser = shutil.which("runuser") or "/usr/sbin/runuser"
    cmd = [runuser, "-u", "hsupload", "--", str(exe), "migrate"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, cwd=str(_VENV.parent),
                           timeout=_MIGRATE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        _err(f"hs-uploader migrate did not finish within "
             f"{_MIGRATE_TIMEOUT_S} s")
        return 1
    except OSError as exc:
        _err(f"hs-uploader migrate could not run: {exc}")
        return 1
    stderr = (r.stderr or "").strip()
    if r.returncode == 2 and "invalid choice: 'migrate'" in stderr:
        print("uploader: this hs-uploader predates `migrate`; send-record "
              "migrate skipped")
        return 0
    for line in (r.stdout or "").splitlines():
        if line.strip():
            print(f"uploader: {line}")
    if r.returncode != 0:
        _err(f"hs-uploader migrate failed (exit {r.returncode}): "
             f"{stderr or 'no message'}")
        return 1
    return 0


def cmd_uploader_manifest(args) -> int:
    write = bool(getattr(args, "write", False) or getattr(args, "enable", False))
    enable = bool(getattr(args, "enable", False))

    # Install the daemon BEFORE rendering.  um.generate() asks the daemon's
    # own venv whether it can discard.  A fresh host has no venv yet, so
    # the probe answers no.  The site sink switch `off` then renders as the
    # legacy hold, and a backlog builds that a later `smd sink upload`
    # would ship (tasks/plan-sink-control.md §10.3).  Only root installs;
    # a non-root call reaches the refusal below.  The enable branch reuses
    # this result, so install.sh runs once.  An install error still lets
    # the write go ahead, as it did before.
    daemon_ready = None
    if enable and os.geteuid() == 0:
        try:
            daemon_ready = _ensure_daemon_installed()
        except (OSError, subprocess.SubprocessError) as exc:
            _err(f"hs-uploader daemon install failed: {exc}")
            daemon_ready = False

    try:
        text = um.generate()
    except Exception as exc:  # pragma: no cover - defensive
        _err(f"uploader manifest generation failed: {exc}")
        return 1

    # sigmond#53: say, by name, what the uploads policy is holding back.
    try:
        suppressed = um.suppressed_pipelines()
    except Exception:  # pragma: no cover - defensive
        suppressed = []
    if suppressed:
        try:
            from ..coordination import load_coordination
            mode = um.effective_mode(load_coordination())
        except Exception:  # pragma: no cover - defensive
            mode = "hold"
        if mode == "discard":
            print("uploader: SITE SINK OFF — these pipelines ack without "
                  "sending: " + ", ".join(suppressed))
        else:
            print("uploader: SITE SINK HOLD (LEGACY) ([uploads] enabled = false) "
                  "— not rendered: " + ", ".join(suppressed))

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

    # A failed install still leaves the manifest written above; the call
    # then reports failure here, and never migrates.
    if enable:
        if daemon_ready is None:
            daemon_ready = _ensure_daemon_installed()
        if not daemon_ready:
            return 1

    # D10: migrate the send-record store after the write and before anything
    # starts or restarts the daemon.  `--write` alone migrates too: smd align
    # runs it, then restarts hs-uploader itself only when this exits 0.
    if _run_migrate() != 0:
        _err(f"hs-uploader migrate failed; {SERVICE} left as it stands, "
             "neither started nor restarted.  Fix the cause above, then run "
             "this command again.")
        return 1

    if not enable:
        return 0

    was_active = _service_active()
    _run(["systemctl", "enable", "--now", SERVICE])
    if was_active and changed:
        print(f"uploader: manifest changed — restarting {SERVICE}")
        _run(["systemctl", "restart", SERVICE])
    return 0

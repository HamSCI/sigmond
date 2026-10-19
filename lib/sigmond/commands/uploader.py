"""``smd admin uploader manifest`` — generate the single-host uploader manifest.

Renders ``/etc/hs-uploader/pipelines.toml`` from every enabled client's
``deploy.toml`` ``[[hs_uploader.pipeline]]`` declarations, with per-site
identity substituted (see :mod:`sigmond.uploader_manifest`).

Modes (default is the read-only check):

* ``--check`` / (no flag) — render and diff against the installed manifest;
  exit non-zero on drift.  Read-only, no root.
* ``--write`` — land the manifest (root) by the steps below, backing up
  any previous file to ``.bak``.
* ``--enable`` — install the daemon first when it is missing (the render
  probes its venv), take the same steps, then make sure
  ``hs-uploader.service`` is enabled and running.

``--write`` and ``--enable`` alike follow tasks/spec-key-split-v373.md
§5.2:

1. ask the daemon's interpreter which store version it reads (only when
   the render holds client pipelines; a hold renders without asking);
2. render to ``pipelines.toml.new``;
3. ask ``hs-uploader migrate --check`` whether a migration waits;
4. stop here when the manifest has not changed and nothing waits;
5. note whether the daemon runs, then stop it;
6. run ``hs-uploader migrate`` as ``hsupload``; on failure drop the side
   file, return the daemon to the state it was found in, and exit 1, so
   the old manifest and the old keys still match;
7. move the side file into place;
8. start the daemon if it ran before, or if ``--enable`` asked for it.

The daemon never migrates while it starts (tasks/plan-sink-control.md
D10), and it reads its manifest once, when it starts.  On a manifest that
did not change, ``--enable`` still restarts an active daemon that started
before the manifest file's last write: an older sigmond may have written
the file and restarted nothing.
"""
from __future__ import annotations

import difflib
import os
import shutil
import signal
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Optional

from .. import uploader_manifest as um

SERVICE = "hs-uploader.service"
_UNIT_SRC = Path("/opt/git/sigmond/hs-uploader/systemd/hs-uploader.service")
_UNIT_DST = Path("/etc/systemd/system/hs-uploader.service")
_INSTALL_SH = Path("/opt/git/sigmond/hs-uploader/install.sh")
_VENV = Path("/opt/hs-uploader/venv")
# hs-uploader's own default for its store's directory (watermark/sqlite.py
# default_path); the unit may override it with HS_UPLOADER_STATE_DIR.
_DEFAULT_STATE_DIR = "/var/lib/hs-uploader"
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


def _service_started_at() -> "float | None":
    """When the daemon's current run began, in Unix seconds, or None when
    systemd does not say.  ``--timestamp=unix`` prints ``@<seconds>``."""
    try:
        r = subprocess.run(
            ["systemctl", "show", "-p", "ActiveEnterTimestamp",
             "--timestamp=unix", "--value", SERVICE],
            capture_output=True, text=True, errors="replace",
            stdin=subprocess.DEVNULL, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return float((r.stdout or "").strip().lstrip("@"))
    except ValueError:
        return None


def _started_before_manifest(path: Path) -> bool:
    """True when the running daemon started before the manifest file's last
    write, so it still follows an older manifest.  Also True when either
    time cannot be read: the operator's setting (``smd sink off``) must take
    effect, and one extra restart costs little."""
    started = _service_started_at()
    if started is None:
        return True
    try:
        return started < path.stat().st_mtime
    except OSError:
        return True


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




def _daemon_store_path() -> Path:
    """The daemon's own ``watermarks.db``: ``HS_UPLOADER_STATE_DIR`` from
    the unit's effective environment when it sets one, else the default
    ``/var/lib/hs-uploader`` (hs-uploader's ``default_path``).  migrate
    takes it as ``--db`` so it migrates the store the daemon opens (map
    J17), not one its own environment would pick."""
    from ..inprocess_senders import _systemctl_show
    try:
        env = _systemctl_show(SERVICE)
    except OSError:
        env = {}
    base = (env.get("HS_UPLOADER_STATE_DIR") or "").strip()
    return Path(base or _DEFAULT_STATE_DIR) / "watermarks.db"


def _run_in_group(cmd: list, *, cwd: str, timeout: float
                  ) -> subprocess.CompletedProcess:
    """Run ``cmd`` in a session of its own and wait up to ``timeout``.

    On a timeout it kills the whole process group, then raises
    ``subprocess.TimeoutExpired``.  ``runuser`` forks to close its PAM
    session, so killing only the process it started would leave the
    real ``hs-uploader migrate`` running (map J17)."""
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, errors="replace", cwd=cwd,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _check_pending(stdout: str) -> bool:
    """Whether ``migrate --check`` reports work for a real migrate: a
    pending migration, a retry it would delete or move, or a crash's
    journal that only a writing open recovers.  A retry that "stays"
    needs nothing."""
    for line in stdout.splitlines():
        text = line.strip()
        if (text.startswith("pending ") or ": would delete" in text
                or ": would move " in text or "journal to recover" in text):
            return True
    return False


def _run_migrate(*, db: Path, sink: Optional[Path],
                 keep_retry_for: Optional[str], check: bool = False) -> int:
    """Run the daemon's own ``hs-uploader migrate`` as ``hsupload``.

    It runs as ``hsupload``, the daemon's account, in the unit's working
    directory.  So no file SQLite creates beside ``watermarks.db``, such as
    its rollback journal, ever belongs to root (bee1, 2026-05-12: "attempt
    to write a readonly database"), and a missing store gets created
    stamped current.  ``--sink`` and ``--keep-retry-for`` go only when
    given: an hs-uploader older than the split knows neither.

    ``check=False`` returns 0 when migrate finished or found nothing to do,
    1 on any failure, after saying why.  ``check=True`` runs ``migrate
    --check``, which writes nothing, and returns 0 when nothing waits, 3
    when a migration is pending (``_check_pending``), 1 when the check
    failed.  Either mode returns 0, with a line saying so, when no daemon
    venv exists or the installed hs-uploader predates ``migrate`` (argparse
    exits 2 with ``invalid choice: 'migrate'``)."""
    exe = _VENV / "bin" / "hs-uploader"
    if not exe.exists():
        if not check:
            print(f"uploader: no {exe}; send-record migrate skipped")
        return 0
    runuser = shutil.which("runuser") or "/usr/sbin/runuser"
    cmd = [runuser, "-u", "hsupload", "--", str(exe), "migrate",
           "--db", str(db)]
    if sink is not None:
        cmd += ["--sink", str(sink)]
    if keep_retry_for is not None:
        cmd += ["--keep-retry-for", keep_retry_for]
    if check:
        cmd.append("--check")
    what = "hs-uploader migrate --check" if check else "hs-uploader migrate"
    try:
        r = _run_in_group(cmd, cwd=str(_VENV.parent),
                          timeout=_MIGRATE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _err(f"{what} did not finish within {_MIGRATE_TIMEOUT_S} s; "
             "killed its process group")
        return 1
    except OSError as exc:
        _err(f"{what} could not run: {exc}")
        return 1
    stderr = (r.stderr or "").strip()
    if r.returncode == 2 and "invalid choice: 'migrate'" in stderr:
        if not check:
            print("uploader: this hs-uploader predates `migrate`; "
                  "send-record migrate skipped")
        return 0
    stdout = r.stdout or ""
    pending = check and r.returncode == 0 and _check_pending(stdout)
    if not check or pending or r.returncode != 0:
        for line in stdout.splitlines():
            if line.strip():
                print(f"uploader: {line}")
    if r.returncode != 0:
        _err(f"{what} failed (exit {r.returncode}): "
             f"{stderr or 'no message'}")
        return 1
    if check:
        return 3 if pending else 0
    return 0


def _effective_mode() -> str:
    """The mode the render will take (``upload``, ``discard`` or
    ``hold``), from coordination.toml and the daemon's discard support.
    ``hold`` when coordination cannot be read: generate() then fails on
    its own read, and no probe runs first."""
    try:
        from ..coordination import load_coordination
        return um.effective_mode(load_coordination())
    except Exception:  # pragma: no cover - defensive
        return "hold"


def _keep_retry_for(text: str) -> Optional[str]:
    """The pipeline that inherits a ``psk-pskreporter`` retry migrate cannot
    delete (spec §4.4 item 4): psk-recorder's when the rendered manifest
    holds it, else meteor-scatter's, else None.  hs-uploader refuses any
    other name."""
    try:
        names = {str(p.get("name")) for p in
                 tomllib.loads(text).get("pipeline") or []
                 if isinstance(p, dict)}
    except tomllib.TOMLDecodeError:
        return None
    for producer in ("psk-recorder", "meteor-scatter"):
        name = um.PSK_PRODUCERS[producer][0]
        if name in names:
            return name
    return None


def _start(enable: bool) -> None:
    """Start the daemon, clearing a failed state first: a daemon whose
    every pipeline was refused exits 1, and enough of those trip systemd's
    start limit (ruling 5).  ``enable`` also enables it at boot.  A start
    that fails only warns: a manifest with no pipeline makes the daemon
    exit "nothing to do", on a station that is fine."""
    _run(["systemctl", "reset-failed", SERVICE])
    cmd = (["systemctl", "enable", "--now", SERVICE] if enable
           else ["systemctl", "start", SERVICE])
    if _run(cmd) != 0:
        _err(f"`{' '.join(cmd)}` failed; see `journalctl -u {SERVICE}`")


def _land(side: Path, path: Path, installed: str, text: str) -> None:
    """Move the side file into place, keeping the previous manifest as
    ``.bak`` on any byte change, so it is always recoverable."""
    if installed and installed != text:
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    os.replace(side, path)


def cmd_uploader_manifest(args) -> int:
    write = bool(getattr(args, "write", False) or getattr(args, "enable", False))
    enable = bool(getattr(args, "enable", False))

    # Install the daemon BEFORE rendering.  The render asks the daemon's
    # own venv what it supports.  A fresh host has no venv yet, so the
    # probe would fail, or answer no.  The site sink switch `off` would
    # then render as the legacy hold, and a backlog builds that a later
    # `smd sink upload` would ship (tasks/plan-sink-control.md §10.3).
    # Only root installs; a non-root call reaches the refusal below.  The
    # enable branch reuses this result, so install.sh runs once.
    daemon_ready = None
    if enable and os.geteuid() == 0:
        try:
            daemon_ready = _ensure_daemon_installed()
        except (OSError, subprocess.SubprocessError) as exc:
            _err(f"hs-uploader daemon install failed: {exc}")
            daemon_ready = False

    # Step 1: ask the daemon's interpreter, once, which store version it
    # reads (spec §5.1), and only when the render needs the answer: a
    # hold renders the heartbeat alone (ruling 9).
    mode = _effective_mode()
    store_version = None
    try:
        if mode in ("upload", "discard"):
            store_version = um.hs_uploader_store_version()
            if store_version is None:
                raise um.ProbeFailed(
                    f"cannot ask {um.HS_UPLOADER_PYTHON} for `hs-uploader "
                    "capabilities` (failed or timed out)")
        text = um.generate(store_version=store_version)
    except um.ProbeFailed as exc:
        _err(f"uploader manifest: {exc}; the installed manifest and "
             f"{SERVICE} stay as they are")
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        _err(f"uploader manifest generation failed: {exc}")
        return 1

    # sigmond#53: say, by name, what the uploads policy is holding back.
    # A hold took no probe; it names the shared pipeline.
    try:
        suppressed = um.suppressed_pipelines(
            store_version=store_version if store_version is not None else 0)
    except Exception:  # pragma: no cover - defensive
        suppressed = []
    if suppressed:
        if mode == "discard":
            print("uploader: SITE SINK OFF — these pipelines ack without "
                  "sending: " + ", ".join(suppressed))
        else:
            print("uploader: SITE SINK HOLD (LEGACY) ([uploads] enabled = false) "
                  "— not rendered: " + ", ".join(suppressed))

    path = um.MANIFEST_PATH
    installed = path.read_text() if path.exists() else ""
    changed = _semantic(text) != _semantic(installed)
    n = text.count("[[pipeline]]")

    if not write:
        # read-only check
        if not installed:
            print(f"uploader: no manifest at {path} — would create "
                  f"{n} pipeline(s)")
            return 1
        if not changed:
            print(f"uploader: {path} is up to date ({n} pipeline(s))")
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

    # Step 2: render to the side file.  The installed manifest stays in
    # force until migrate has moved the keys it names.
    path.parent.mkdir(parents=True, exist_ok=True)
    side = path.with_name(path.name + ".new")
    side.write_text(text)

    # --sink only for an hs-uploader that knows the split; a hold took no
    # probe, so it cannot tell.  --keep-retry-for only names a pipeline the
    # new manifest holds.
    split = (store_version or 0) >= um.SPLIT_STORE_VERSION
    migrate_kw = dict(db=_daemon_store_path(),
                      sink=Path(um.SINK_PATH) if split else None,
                      keep_retry_for=_keep_retry_for(text) if split else None)

    # Step 3: is a migration pending?
    pending = _run_migrate(check=True, **migrate_kw)
    if pending not in (0, 3):
        side.unlink(missing_ok=True)
        _err(f"hs-uploader migrate --check failed; {path} and {SERVICE} "
             "left as they stand")
        return 1

    # Step 4: nothing changed and nothing waits: stop nothing.
    if not changed and pending == 0:
        stale = _service_active() and _started_before_manifest(path)
        if installed == text:
            side.unlink(missing_ok=True)
            print(f"uploader: {path} already current ({n} pipeline(s))")
        else:
            _land(side, path, installed, text)
            print(f"uploader: refreshed {path} "
                  f"({n} pipeline(s); no functional change)")
        if not enable:
            return 0
        if daemon_ready is None:
            daemon_ready = _ensure_daemon_installed()
        if not daemon_ready:
            return 1
        _start(enable=True)
        # The daemon reads its manifest once, at start.  One that started
        # before the file's last write still follows an older manifest.
        if stale:
            print(f"uploader: {SERVICE} started before the manifest on disk "
                  f"was written — restarting {SERVICE}")
            _run(["systemctl", "restart", SERVICE])
        return 0

    # Step 5: note whether the daemon runs, then stop it.  Stop even an
    # inactive unit: that cancels a restart systemd has queued, which could
    # otherwise start the daemon in the middle of the migration.
    was_active = _service_active()
    if _run(["systemctl", "stop", SERVICE]) != 0 and was_active:
        side.unlink(missing_ok=True)
        _err(f"could not stop {SERVICE}; nothing migrated, {path} left as "
             "it stands")
        return 1

    # Step 6: migrate, the daemon stopped.  On failure the old manifest
    # and the old keys still match: drop the side file and put the daemon
    # back as found.
    if _run_migrate(**migrate_kw) != 0:
        side.unlink(missing_ok=True)
        if was_active:
            _start(enable=False)
        _err(f"hs-uploader migrate failed; {path} left as it stands and "
             f"{SERVICE} {'running' if was_active else 'stopped'} as found.  "
             "Run this command again once migrate passes.")
        return 1

    # Step 7: land the manifest, keeping a .bak.
    if installed == text:
        side.unlink(missing_ok=True)
        print(f"uploader: {path} already current ({n} pipeline(s))")
    else:
        _land(side, path, installed, text)
        if changed:
            print(f"uploader: wrote {path} ({n} pipeline(s))")
        else:
            print(f"uploader: refreshed {path} "
                  f"({n} pipeline(s); no functional change)")

    # Step 8: start the daemon if it ran before, or if --enable asked.  A
    # failed install leaves the manifest landed and starts nothing.
    if enable:
        if daemon_ready is None:
            daemon_ready = _ensure_daemon_installed()
        if not daemon_ready:
            if was_active:
                _start(enable=False)
            return 1
        _start(enable=True)
    elif was_active:
        _start(enable=False)
    return 0

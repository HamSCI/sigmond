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

Before step 1, under the same install lock, the write step runs the part 2
steps (tasks/spec-sink-2b-part2.md §6.1, §6.4, §7.2, §7.4):

a. ask ``hs-uploader capabilities --features``, once;
b. ``gate`` and no carry-over marker: the carry-over (sink_carryover);
   a failed carry-over renders part 1's form and the step exits 1;
c. ``gate`` and the marker: repair the ``[uploads]`` mirror from
   ``site.toml`` (sink_mirror; a broken ``site.toml`` leaves everything
   alone and exits 1), give a late client its file, and run the lever
   step, which costs nothing once the senders stand lowered;
d. a feature set without ``gate`` (not a failed probe) and the marker:
   move the marker aside (plan Ruling 10);
e. render the form the probe and the marker call for (generate_forms).

Step 7 lands ``pipelines.d/sigmond.toml`` too: in the gate form before the
legacy manifest, in part 1's form removed after it.  When the legacy
manifest stays the same, step 4 lands or removes it with no restart (plan
Ruling 14).  The read-only step previews b-d and writes nothing (Ruling 13).

The daemon never migrates while it starts (tasks/plan-sink-control.md
D10), and it reads its manifest once, when it starts.  On a manifest that
did not change, ``--enable`` still restarts an active daemon that started
before the manifest file's last write: an older sigmond may have written
the file and restarted nothing.
"""
from __future__ import annotations

import contextlib
import difflib
import io
import os
import shutil
import signal
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Optional

from .. import install_lock, safe_write
from .. import sink_carryover as co
from .. import sink_levers, sink_mirror, sink_state
from .. import uploader_manifest as um

SERVICE = "hs-uploader.service"
_UNIT_SRC = Path("/opt/git/sigmond/hs-uploader/systemd/hs-uploader.service")
_UNIT_DST = Path("/etc/systemd/system/hs-uploader.service")
_INSTALL_SH = Path("/opt/git/sigmond/hs-uploader/install.sh")
_VENV = Path("/opt/hs-uploader/venv")
# hs-uploader's own default for its store's directory (watermark/sqlite.py
# default_path); the unit may override it with HS_UPLOADER_STATE_DIR.
_DEFAULT_STATE_DIR = "/var/lib/hs-uploader"
# `hs-uploader migrate --check` exits this when a migrate would write
# (hs-uploader 72a7888 on; an older one always exits 0).
_CHECK_PENDING = 3
# Longer than hs-uploader migrate's own 30 s wait for another writer's lock,
# with room for the interpreter to start on a busy station.
_MIGRATE_TIMEOUT_S = 120


def _err(msg: str) -> None:
    """Every line this command prints to stderr reads ``smd: uploader: ``
    (stdout lines read ``uploader: ``), so a parser of the step's output
    matches one prefix per stream."""
    print(f"smd: uploader: {msg}", file=sys.stderr)


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
    except OSError as exc:
        guess = Path(_DEFAULT_STATE_DIR) / "watermarks.db"
        _err(f"could not read {SERVICE}'s environment ({exc}); assuming "
             f"its store sits at {guess}.  A store missing there counts "
             "as a pending migration.")
        return guess
    base = (env.get("HS_UPLOADER_STATE_DIR") or "").strip()
    return Path(base or _DEFAULT_STATE_DIR) / "watermarks.db"


# The daemon's own environment file (hs-uploader.service EnvironmentFile=-).
_UPLOADER_ENV = Path("/etc/hs-uploader/uploader.env")


def _unit_environment() -> dict:
    """hs-uploader.service's effective environment; raises OSError."""
    from ..inprocess_senders import _systemctl_show
    return _systemctl_show(SERVICE)


def _env_file_value(path: Path, key: str) -> Optional[str]:
    """``key``'s last value in a systemd environment file, or None."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    value = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        k, sep, v = line.partition("=")
        if sep and k.strip() == key:
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            value = v
    return value


def _state_dir_override() -> Optional[str]:
    """Task 27 M-3': one line naming the override when hs-uploader's unit
    environment or ``uploader.env`` sets ``HS_UPLOADER_STATE_DIR`` to
    anything but the default; None otherwise.

    sigmond reads and writes the carry-over marker at sink_state's fixed
    MARKER_PATH, while hs-uploader looks for it under its state directory.
    Until sigmond derives the marker the same way (v3.79), the two would
    read different markers, so a caller treats the probe as offering no
    ``gate``: part 1's write and part 1's render."""
    found = []
    try:
        env = _unit_environment()
    except OSError:
        env = {}
    found.append((f"{SERVICE}'s environment",
                  env.get("HS_UPLOADER_STATE_DIR")))
    found.append((str(_UPLOADER_ENV),
                  _env_file_value(_UPLOADER_ENV, "HS_UPLOADER_STATE_DIR")))
    for source, value in found:
        value = (value or "").strip()
        if value and os.path.normpath(value) != _DEFAULT_STATE_DIR:
            return (f"{source} sets HS_UPLOADER_STATE_DIR={value}, but "
                    f"sigmond reads the carry-over marker at "
                    f"{sink_state.MARKER_PATH} only; it treats hs-uploader "
                    "as offering no `gate` (part 1's write and render) "
                    "until it derives the marker from that directory "
                    "(v3.79)")
    return None


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


def _store_missing(stdout: str) -> bool:
    """``migrate --check`` found no store at the path it was given.  The
    path is the daemon's store, or a guess at it, so a missing file may
    mean a wrong guess rather than a fresh host: run migrate, which
    creates and stamps a missing store as ``hsupload`` (review #4)."""
    return any(line.startswith("watermarks.db: not found at ")
               for line in stdout.splitlines())


def _check_pending(rc: int, stdout: str,
                   store_version: Optional[int] = None) -> bool:
    """Whether ``migrate --check`` reports work for a real migrate.

    An hs-uploader that reads store version 2 or more says so in its exit
    code: 3 when a migrate would write, 0 when not.  An older one always
    exits 0, so its wording decides: a pending migration, a retry it would
    delete or move, or a crash's journal that only a writing open
    recovers.  A retry that "stays" needs nothing.  A store missing at the
    given path counts as pending either way."""
    if _store_missing(stdout):
        return True
    if store_version is not None and store_version >= um.SPLIT_STORE_VERSION:
        return rc == _CHECK_PENDING
    if rc == _CHECK_PENDING:
        return True
    for line in stdout.splitlines():
        text = line.strip()
        if (text.startswith("pending ") or ": would delete" in text
                or ": would move " in text or "journal to recover" in text):
            return True
    return False


def _run_migrate(*, db: Path, sink: Optional[Path],
                 keep_retry_for: Optional[str], check: bool = False,
                 store_version: Optional[int] = None) -> int:
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
    failed.  ``store_version`` (the probe's answer) says whether the exit
    code can be trusted (:func:`_check_pending`).  Either mode returns 0, with a line saying so, when no daemon
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
    ok = r.returncode == 0 or (check and r.returncode == _CHECK_PENDING)
    pending = check and ok and _check_pending(r.returncode, stdout,
                                              store_version)
    if not check or pending or not ok:
        for line in stdout.splitlines():
            if line.strip():
                print(f"uploader: {line}")
    if not ok:
        _err(f"{what} failed (exit {r.returncode}): "
             f"{stderr or 'no message'}")
        return 1
    if check:
        return 3 if pending else 0
    return 0


def _render_inputs(path: Optional[Path] = None, *, gate: bool = False):
    """``(coord, mode, supports_discard)`` for the render: coordination.toml
    (at `path`, the default when None),
    the mode the render will take (``upload``, ``discard`` or ``hold``),
    and, only when the policy asks for discard, whether the daemon's venv
    can discard.  The command hands all three to generate(), so the venv
    is asked once (review #3).  ``(None, "hold", None)`` when coordination
    cannot be read: generate() then fails on its own read, and no probe
    runs first.  The gate form renders no ``discard``, so with ``gate``
    the venv is never asked (spec §7.3)."""
    try:
        from ..coordination import load_coordination
        coord = load_coordination() if path is None else load_coordination(path)
    except Exception:  # pragma: no cover - defensive
        return None, "hold", None
    if gate:
        return coord, coord.uploads.mode, None
    supports = (um.hs_uploader_supports_discard()
                if coord.uploads.mode == "discard" else None)
    return coord, um.effective_mode(coord, supports_discard=supports), supports


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


# The systemctl the carry-over and the lever step call for the recorders
# (never for hs-uploader.service, which goes through _run).  Tests put a
# fake here.
_lever_systemctl = subprocess.run


def _euid() -> int:
    return os.geteuid()


def _step_roots() -> co.CarryRoots:
    """Where the part 2 steps read and write: the production paths, with
    the legacy manifest wherever this module renders it."""
    return co.CarryRoots(manifest=um.MANIFEST_PATH)


def _say(line: str) -> None:
    print(f"uploader: {line}")


def _warn(line: str) -> None:
    _err(line)


def _print_result(lines) -> None:
    """Lines from the carry-over or the lever step: a failure to stderr,
    everything else to stdout, each with the step's prefix."""
    for line in lines:
        if "FAILED" in line or "still armed" in line:
            _warn(line)
        else:
            _say(line)


_STATUS_CAUSE = "sigmond: cannot read hs-uploader sink status: "


@contextlib.contextmanager
def _relayed_stderr(lines: list):
    """Collect what a helper prints to stderr (sink_state's one-line
    cause, which reads ``sigmond: ...``) into `lines`, so this step can
    print it under its own prefix."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):
            yield
    finally:
        lines.extend(ln.strip() for ln in buf.getvalue().splitlines()
                     if ln.strip())


def _cause(lines: list) -> str:
    """The relayed lines as one cause, sink_state's own prefix dropped."""
    return "; ".join(ln[len(_STATUS_CAUSE):] if ln.startswith(_STATUS_CAUSE)
                     else ln for ln in lines)


def _apply_relayed(plan, what: str):
    """``co.apply`` with sink_state's stderr line relayed under the
    step's prefix (the read-back reads ``sink status``)."""
    relayed: list = []
    with _relayed_stderr(relayed):
        res = co.apply(plan, systemctl=_lever_systemctl)
    for ln in relayed:
        _warn(f"{what}: {_cause([ln])}")
    return res


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
    if enable and _euid() == 0:
        try:
            daemon_ready = _ensure_daemon_installed()
        except (OSError, subprocess.SubprocessError) as exc:
            _err(f"hs-uploader daemon install failed: {exc}")
            daemon_ready = False

    # Plan Ruling 7: the write step holds hs-uploader's install lock from
    # the probe through the landing, so two manifest steps never interleave.
    # The daemon install above runs outside it: once hs-uploader's own
    # installer takes this lock (v3.79), holding it here would make that
    # installer wait out its bound.  The read-only step writes nothing and
    # takes no lock; a non-root write is refused below, before any write.
    # Nothing under the lock calls back into this function (ruling C14-3:
    # flock would deadlock on itself).
    if not (write and _euid() == 0):
        return _manifest_step(write=write, enable=enable,
                              daemon_ready=daemon_ready)
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(install_lock.held())
        except install_lock.InstallLockError as exc:
            _err(f"manifest step: {exc}")
            return 1
        return _manifest_step(write=write, enable=enable,
                              daemon_ready=daemon_ready)


# ---- the part 2 steps before the render (spec §6, §7.2; Rulings 9, 10) -------

def _carry_over(roots: co.CarryRoots) -> bool:
    """Spec §6.4: plan, print, apply.  True when the marker got written."""
    try:
        plan = co.plan(roots)
    except Exception as exc:
        _warn(f"carry-over: cannot read the station ({type(exc).__name__}: "
              f"{exc}); no switch file written, no marker; rendering part "
              "1's form")
        return False
    for line in plan.lines():
        _say(line)
    try:
        res = _apply_relayed(plan, "carry-over: read-back")
    except Exception as exc:
        _warn(f"carry-over: FAILED ({type(exc).__name__}: {exc}); no marker "
              "written; rendering part 1's form")
        return False
    _print_result(res.lines())
    if not res.ok:
        _warn("carry-over: rendering part 1's form until a carry-over "
              "finishes; this step exits 1")
    return res.ok


def _mirror_inputs(roots: co.CarryRoots):
    return sink_mirror.read_inputs(roots.sinks, roots.coordination,
                                   roots.etc / "sigmond" / "site-profile.toml")


def _mirror_repair(roots: co.CarryRoots) -> Optional[int]:
    """Ruling 9.  0 on success or nothing to do, 1 on a failed write, and
    None for a broken site.toml: the caller then leaves everything alone."""
    try:
        mp = sink_mirror.plan_mirror(*_mirror_inputs(roots))
    except Exception as exc:
        _warn(f"mirror repair: cannot read [uploads] ({type(exc).__name__}: "
              f"{exc}); nothing repaired")
        return 1
    if mp.action == "broken":
        for line in mp.lines():
            _warn(line)
        return None
    if mp.action == "none":
        return 0

    def runner(args, stdin=None):
        return um.run_hs_uploader(args, stdin=stdin, python=roots.python)

    failures = sink_mirror.apply_mirror(mp, runner=runner, sinks=roots.sinks)
    if failures:
        for path, why in failures:
            _warn(f"mirror repair: FAILED {path}: {why}")
        return 1
    for line in mp.lines():
        _say(line)
    return 0


def _lever_step(roots: co.CarryRoots) -> int:
    """The lever step with the marker standing (spec §8; idempotent): every
    client hs-uploader reads below ``upload`` loses its in-process senders.
    A sender already lowered and restarted since costs nothing and prints
    nothing."""
    relayed: list = []
    with _relayed_stderr(relayed):
        state = sink_state.read_state(
            python=roots.python, manifest=str(roots.manifest),
            sinks=str(roots.sinks), marker=str(roots.marker),
            store=None if roots.store is None else str(roots.store))
    if state is None:
        # One line, naming the cause (ruling C19-1: fail loud, exit 1).
        _warn("lever step: cannot read hs-uploader sink status ("
              f"{_cause(relayed) or 'no reason given'}), so no in-process "
              "sender was checked; this step exits 1")
        return 1
    for ln in relayed:
        _warn(f"lever step: {ln}")
    below = [c for c in sink_state.below_upload(state) if c != co.SITE]
    if not below:
        return 0
    try:
        from ..topology import load_topology
        lp = sink_levers.plan_levers(
            below, load_topology(roots.topology),
            roots=sink_levers.Roots(etc=roots.etc, writer_py=roots.writer_py),
            systemctl=_lever_systemctl)
        if not (lp.env_levers or lp.restart_only or lp.mag is not None
                or lp.wspr_uploader or lp.problems):
            return 0
        try:
            res = sink_levers.apply_levers(lp, systemctl=_lever_systemctl)
        except sink_levers.RadiodMoved as exc:
            if exc.result is not None:
                _print_result(exc.result.lines())
            _warn(f"lever step: FAILED: {exc}")
            return 1
        _print_result(res.lines())
        verify = sink_levers.verify_levers(lp, systemctl=_lever_systemctl)
    except Exception as exc:
        _warn(f"lever step: FAILED ({type(exc).__name__}: {exc})")
        return 1
    for v in verify:
        _warn(f"lever: still armed: {v}")
    return 1 if (res.failures or verify) else 0


def _marker_standing(roots: co.CarryRoots) -> int:
    """``gate`` and the marker: mirror repair, late clients, lever step."""
    rc = _mirror_repair(roots)
    if rc is None:
        return 1                    # a broken site.toml: touch nothing else
    try:
        late = co.late_clients(roots)
    except Exception as exc:
        _warn(f"late clients: cannot read the station ({type(exc).__name__}: "
              f"{exc}); none checked")
        return 1
    if late.clients:
        # apply() levers the union of these and what hs-uploader reads
        # below upload, so the lever step below would repeat it.
        for line in late.lines():
            _say(line)
        try:
            res = _apply_relayed(late, "late clients: read-back")
        except Exception as exc:
            _warn(f"late clients: FAILED ({type(exc).__name__}: {exc})")
            return 1
        _print_result(res.lines())
        return rc or (0 if res.ok else 1)
    return _lever_step(roots) or rc


def _move_aside(roots: co.CarryRoots) -> int:
    """Ruling 10: hs-uploader offers no ``gate`` while the marker stands."""
    try:
        new = sink_state.move_marker_aside(roots.marker)
    except OSError as exc:
        _warn(f"the installed hs-uploader offers no `gate`, but the "
              f"carry-over marker {roots.marker} could not be moved aside "
              f"({exc.strerror or exc}); this step exits 1")
        return 1
    if new is not None:
        _say(f"the installed hs-uploader offers no `gate`: moved the "
             f"carry-over marker aside to {new}; the carry-over runs again "
             "when an hs-uploader with the gate returns")
    return 0


def _sink_steps(roots: co.CarryRoots, features, marker: bool):
    """Everything the write step does before the render (spec §6.1, §7.4),
    under the install lock: ``(rc, marker standing now)``."""
    gate = features is not None and um.GATE_FEATURE in features
    if gate and not marker:
        ok = _carry_over(roots)
        return (0 if ok else 1), sink_state.marker_stands(roots.marker)
    if gate:
        return _marker_standing(roots), True
    if marker and features is not None:
        return _move_aside(roots), sink_state.marker_stands(roots.marker)
    # A failed probe (None) cannot tell: nothing moves (spec §7.4).
    return 0, marker


def _preview_sink_steps(roots: co.CarryRoots, features, marker: bool):
    """Ruling 13: what ``--write`` would do before its render, written
    nowhere.  ``(something would change, the marker the render assumes)``."""
    gate = features is not None and um.GATE_FEATURE in features
    if gate and not marker:
        try:
            plan = co.plan(roots)
        except Exception as exc:
            _warn(f"carry-over: cannot read the station ({type(exc).__name__}"
                  f": {exc})")
            return True, True
        _say("--write would carry over first, then render the gate form:")
        for line in plan.lines():
            _say(line)
        return True, True
    if gate:
        pending = False
        try:
            mp = sink_mirror.plan_mirror(*_mirror_inputs(roots))
            late = co.late_clients(roots)
        except Exception as exc:
            _warn(f"cannot read the switches ({type(exc).__name__}: {exc})")
            return True, True
        if mp.action == "broken":
            for line in mp.lines():
                _warn(f"--write would leave [uploads] and the site switch "
                      f"alone and exit 1: {line}")
            return True, True
        for line in mp.lines():
            _say(f"--write would repair the mirror: {line}")
            pending = True
        for line in late.lines() if late.clients else ():
            _say(f"--write would: {line}")
            pending = True
        return pending, True
    if marker and features is not None:
        _say(f"--write would move the carry-over marker {roots.marker} aside "
             "(the installed hs-uploader offers no `gate`)")
        return True, False
    return False, marker


def _land_sigmond(text: Optional[str]) -> bool:
    """Land or remove ``pipelines.d/sigmond.toml`` beside the manifest and
    say so when it changed.  Raises OSError (a refused symlink, say)."""
    d = um.MANIFEST_PATH.parent / "pipelines.d"
    changed = um.land_sigmond_file(text, d)
    if changed:
        _say(f"{'wrote' if text is not None else 'removed'} "
             f"{d / um.SIGMOND_FILE}")
    return changed


def _sigmond_preflight() -> Optional[str]:
    """Why ``pipelines.d/`` or ``pipelines.d/sigmond.toml`` cannot be
    landed or removed, by lstat, before the daemon stops; None when they
    can.  A symlink at either, or a directory that is no directory, or a
    file that is no regular file, refuses here, so nothing stops for a
    landing that :func:`uploader_manifest.land_sigmond_file` would refuse.
    A link planted between this check and the landing still refuses
    there, and the step puts the daemon back."""
    d = um.MANIFEST_PATH.parent / "pipelines.d"
    for p, want, kind in ((d, stat.S_ISDIR, "a directory"),
                          (d / um.SIGMOND_FILE, stat.S_ISREG, "a regular file")):
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            return None
        except OSError as exc:
            return f"cannot lstat {p} ({exc.strerror or exc})"
        if stat.S_ISLNK(st.st_mode):
            return f"{p} is a symlink; refusing to work through it"
        if not want(st.st_mode):
            return f"{p} is not {kind}"
    return None


def _nothing_behind_a_linked_pipelines_d() -> bool:
    """Part 1's form only removes ``pipelines.d/sigmond.toml``: True when
    ``pipelines.d`` is a symlink and no sigmond.toml stands behind it, so
    there is nothing to remove and the legacy manifest may land (Task 19).
    Reads only: an lstat of the link, and of the file through it."""
    d = um.MANIFEST_PATH.parent / "pipelines.d"
    try:
        if not stat.S_ISLNK(os.lstat(d).st_mode):
            return False
    except OSError:
        return False
    return not os.path.lexists(d / um.SIGMOND_FILE)


def _sigmond_drift(text: Optional[str]) -> bool:
    """Read-only: whether ``pipelines.d/sigmond.toml`` would change."""
    path = um.MANIFEST_PATH.parent / "pipelines.d" / um.SIGMOND_FILE
    try:
        have = safe_write.read_nofollow(path)
    except FileNotFoundError:
        have = None
    except OSError:
        return True
    return have != text


# ---- the step ----------------------------------------------------------------

def _manifest_step(*, write: bool, enable: bool, daemon_ready) -> int:
    """The manifest step proper, after the daemon install; under the
    install lock when it writes."""
    roots = _step_roots()
    # Spec §7.4: ask the installed hs-uploader, once and first, which
    # features it offers; the step holds the answer from here on.  A
    # failed probe (None) warns once and renders part 1's form.
    try:
        features = um.hs_uploader_features(python=roots.python)
    except Exception:  # pragma: no cover - defensive
        features = None
    if features is None:
        _err("cannot ask hs-uploader for `capabilities "
             "--features` (failed or timed out); rendering without it")
    else:
        print("uploader: hs-uploader features: "
              + (" ".join(sorted(features)) or "none"))
    if features is not None and um.GATE_FEATURE in features:
        why = _state_dir_override()
        if why:
            _err(why)
            features = features - {um.GATE_FEATURE}

    # Spec §6.1, §7.2, §7.4: the carry-over, or mirror repair, late clients
    # and the lever step, or the move-aside, all before the render.  The
    # read-only step previews them and writes nothing (Ruling 13); a
    # non-root write runs none of them and is refused below.
    marker = sink_state.marker_stands(roots.marker)
    pre_rc, sink_pending = 0, False
    if not write:
        sink_pending, marker = _preview_sink_steps(roots, features, marker)
    elif _euid() == 0:
        pre_rc, marker = _sink_steps(roots, features, marker)
    gate = um.gate_form(features, marker)

    # Step 1: ask the daemon's interpreter, once, which store version it
    # reads (spec §5.1), and only when the render needs the answer: a
    # hold renders the heartbeat alone (ruling 9); the gate form renders
    # every client pipeline whatever [uploads] says.
    coord, mode, supports_discard = _render_inputs(roots.coordination,
                                                   gate=gate)
    store_version = None
    try:
        if gate or mode in ("upload", "discard"):
            store_version = um.hs_uploader_store_version(python=roots.python)
            if store_version is None:
                raise um.ProbeFailed(
                    f"cannot ask {roots.python} for `hs-uploader "
                    "capabilities` (failed or timed out)")
        form = um.generate_forms(coord=coord, features=features,
                                 marker_stands=marker,
                                 supports_discard=supports_discard,
                                 store_version=store_version)
        text = form.legacy_text
    except um.ProbeFailed as exc:
        _err(f"manifest step: {exc}; the installed manifest and "
             f"{SERVICE} stay as they are")
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        _err(f"manifest generation failed: {exc}")
        return 1

    # sigmond#53: say, by name, what the uploads policy is holding back.
    # A hold took no probe; it names the shared pipeline.  The gate form
    # holds nothing back by render: the switches decide.
    suppressed = []
    if not gate:
        try:
            suppressed = um.suppressed_pipelines(
                coord=coord,
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
        rc = 1 if sink_pending else 0
        if _sigmond_drift(form.sigmond_file):
            what = ("write" if form.sigmond_file is not None else "remove")
            print(f"uploader: `--write` would {what} "
                  f"{path.parent / 'pipelines.d' / um.SIGMOND_FILE}")
            rc = 1
        if not installed:
            print(f"uploader: no manifest at {path} — would create "
                  f"{n} pipeline(s)")
            return 1
        if not changed:
            print(f"uploader: {path} is up to date ({n} pipeline(s))")
            return rc
        print(f"uploader: {path} DRIFT — `--write` would change it:\n")
        diff = difflib.unified_diff(
            installed.splitlines(), text.splitlines(),
            fromfile=f"{path} (installed)", tofile="generated", lineterm="")
        for line in diff:
            print(line)
        return 1

    # write path — needs root
    if _euid() != 0:
        _err("writing the manifest requires root (sudo smd admin uploader "
             "manifest --write)")
        return 1

    rc = _land_step(path=path, installed=installed, text=text,
                    sigmond_file=form.sigmond_file, gate=gate,
                    changed=changed, n=n, enable=enable,
                    daemon_ready=daemon_ready, store_version=store_version)
    return rc or pre_rc


def _land_step(*, path: Path, installed: str, text: str,
               sigmond_file: Optional[str], gate: bool, changed: bool,
               n: int, enable: bool, daemon_ready,
               store_version: Optional[int]) -> int:
    """Steps 2-8 (spec-key-split §5.2), with ``pipelines.d/sigmond.toml``
    landed beside the manifest (spec §3.6): in the gate form before it, in
    part 1's form removed after it."""
    # Before anything stops: pipelines.d/sigmond.toml must be landable.
    # Part 1's form refuses a symlinked pipelines.d only when a sigmond.toml
    # stands behind it to remove.
    if not gate and _nothing_behind_a_linked_pipelines_d():
        refused = None
    else:
        refused = _sigmond_preflight()
    if refused is not None:
        _warn(f"{refused}; {path}, pipelines.d/ and {SERVICE} left as they "
              f"stand.  Remove it, then run this command again")
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
    pending = _run_migrate(check=True, store_version=store_version,
                           **migrate_kw)
    if pending not in (0, 3):
        side.unlink(missing_ok=True)
        _err(f"hs-uploader migrate --check failed; {path} and {SERVICE} "
             "left as they stand")
        return 1

    # Step 4: nothing changed and nothing waits: stop nothing.  Ruling 14:
    # sigmond.toml still lands or goes here, with no restart; part 1 and
    # part 2 daemons alike rebuild from pipelines.d/ within one pump.
    if not changed and pending == 0:
        rc = 0
        stale = _service_active() and _started_before_manifest(path)
        if gate:
            try:
                _land_sigmond(sigmond_file)
            except OSError as exc:
                side.unlink(missing_ok=True)
                _warn(f"could not land pipelines.d/{um.SIGMOND_FILE} "
                      f"({exc.strerror or exc}); {path} left as it stands")
                return 1
        if installed == text:
            side.unlink(missing_ok=True)
            print(f"uploader: {path} already current ({n} pipeline(s))")
        else:
            _land(side, path, installed, text)
            print(f"uploader: refreshed {path} "
                  f"({n} pipeline(s); no functional change)")
        if not gate:
            rc = _remove_sigmond()
        if not enable:
            return rc
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
        return rc

    # Steps 5-8 stop the daemon.  Whatever goes wrong before step 8 starts
    # it again, an exception included, must put it back as found and exit
    # non-zero (review #2): a stopped uploader under a green `smd apply`
    # ships nothing until someone notices.
    was_active = _service_active()
    try:
        return _stop_migrate_land_start(
            side=side, path=path, installed=installed, text=text,
            changed=changed, n=n, enable=enable, daemon_ready=daemon_ready,
            was_active=was_active, migrate_kw=migrate_kw,
            sigmond_file=sigmond_file, gate=gate)
    except Exception as exc:
        side.unlink(missing_ok=True)
        _err(f"manifest step failed after stopping {SERVICE}: "
             f"{type(exc).__name__}: {exc}")
        if was_active:
            try:
                _start(enable=False)
            except Exception as exc2:  # pragma: no cover - defensive
                _err(f"could not start {SERVICE} again: {exc2}")
        return 1


def _remove_sigmond() -> int:
    """Part 1's form: no ``pipelines.d/sigmond.toml`` (spec §3.6).  The
    legacy manifest, holding the heartbeat, has landed already."""
    if _nothing_behind_a_linked_pipelines_d():
        return 0                # nothing to remove, and never through a link
    try:
        _land_sigmond(None)
    except OSError as exc:
        _warn(f"could not remove pipelines.d/{um.SIGMOND_FILE} "
              f"({exc.strerror or exc}); the legacy heartbeat may stand "
              "aside for it")
        return 1
    return 0


def _stop_migrate_land_start(*, side: Path, path: Path, installed: str,
                             text: str, changed: bool, n: int, enable: bool,
                             daemon_ready, was_active: bool,
                             migrate_kw: dict, sigmond_file: Optional[str] = None,
                             gate: bool = False) -> int:
    """Spec §5.2 steps 5-8, the daemon's state as found in ``was_active``."""
    # Step 5: stop the daemon.  Stop even an inactive unit: that cancels a
    # restart systemd has queued, which could otherwise start the daemon in
    # the middle of the migration.
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

    # Step 7: land the manifest, keeping a .bak.  The gate form lands
    # sigmond.toml first, so the heartbeat never drops out between the two;
    # a failure there fails the step (the caller puts the daemon back).
    if gate:
        _land_sigmond(sigmond_file)
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
    rc = 0 if gate else _remove_sigmond()

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
    return rc

"""Snapshots of smd's read-only JSON, for station-web to read.

station-web runs unprivileged and runs no subprocess, so it cannot ask smd
anything itself.  Instead a root timer (sigmond-snapshot.timer, every
5 minutes) runs `smd admin snapshot write`, which runs each command in
SNAPSHOT_COMMANDS and writes its output, wrapped in an envelope, to
/run/sigmond/snapshot/<name>.json.  The envelope records the command, when
it ran, its exit code, and either the parsed JSON or why there is none, so
a reader can tell a failed or missing snapshot from a passing one.

The files are 0640 root:sigmond-status; station-web's user joins that
group.  station-web asks for a fresh check snapshot by dropping an empty
file into /run/sigmond/snapshot-request/; sigmond-snapshot-request.path
then runs `smd admin snapshot write --request`, which answers at most once
per 30 s.

SNAPSHOT_COMMANDS names every command station-web relies on.
tests/test_snapshot.py checks each one against the live smd parser, so a
renamed verb fails CI here instead of going quietly empty on the web page.
"""
import datetime
import grp
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

SNAPSHOT_COMMANDS: Dict[str, List[str]] = {
    "check": ["check", "--json"],
    "components": ["component", "list", "--json", "--no-fetch"],
    "catalog": ["component", "list", "--catalog", "--json"],
    "instances": ["admin", "instance", "list", "--json"],
    "validate": ["admin", "validate", "--json"],
    "viewers": ["admin", "viewers", "--json"],
    "manifest": ["admin", "manifest", "status", "--json"],
    # Exits 1 when the spool holds no tick; that envelope then carries
    # data null and the error, which the page reads as "heartbeat not
    # running here".
    "heartbeat": ["admin", "heartbeat", "show", "--json"],
}

OUT_DIR = Path("/run/sigmond/snapshot")
REQUEST_DIR = Path("/run/sigmond/snapshot-request")
READ_GROUP = "sigmond-status"
SMD = "/usr/local/bin/smd"
TIMEOUT_S = 60
MIN_REQUEST_INTERVAL_S = 30.0
LAST_REQUEST = ".last-request"
_ERROR_MAX = 2000
# smd's _err always colours its mark: "  \x1b[31m✗\x1b[0m  message".
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _read_gid() -> Optional[int]:
    """sigmond-status's gid when running as root and the group exists, else
    None (a non-root writer could not chown anyway; tests run that way)."""
    if os.geteuid() != 0:
        return None
    try:
        return grp.getgrnam(READ_GROUP).gr_gid
    except KeyError:
        return None


def _write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` as 0640 (root:sigmond-status where it can),
    through a temp file in the same directory and os.replace, so a reader
    sees the old file or the new one, never half of either."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.",
                               suffix=".tmp")
    try:
        os.fchmod(fd, 0o640)
        gid = _read_gid()
        if gid is not None:
            os.fchown(fd, 0, gid)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _plain(stderr: str) -> str:
    """stderr as plain text: no colour codes, no leading ✗ marks."""
    lines = (_ANSI.sub("", line).strip().lstrip("\u2717").strip()
             for line in stderr.splitlines())
    return "\n".join(line for line in lines if line)


def _iso_utc(t: float) -> str:
    return datetime.datetime.fromtimestamp(
        t, datetime.timezone.utc).isoformat(timespec="seconds")


def _run_one(name: str, args: List[str], run: Callable, now: Callable,
             smd: str) -> dict:
    envelope = {"name": name, "command": "smd " + " ".join(args),
                "ran_at": _iso_utc(now()), "exit": -1, "data": None,
                "error": None}
    try:
        proc = run([smd, *args], stdin=subprocess.DEVNULL,
                   capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        envelope["error"] = "timed out"
        return envelope
    except (OSError, subprocess.SubprocessError) as exc:
        envelope["error"] = f"could not run: {exc}"
        return envelope
    envelope["exit"] = proc.returncode
    try:
        # A nonzero exit with valid JSON keeps its data: `smd check`
        # exits 1 or 2 by design and still reports every line.
        envelope["data"] = json.loads(proc.stdout or "")
    except ValueError:
        stderr = _plain(proc.stderr or "")
        if proc.returncode != 0 and stderr:
            envelope["error"] = stderr[-_ERROR_MAX:]
        else:
            envelope["error"] = "output was not JSON"
    return envelope


def write_snapshots(names: Iterable[str], out_dir: Path,
                    run: Optional[Callable] = None,
                    now: Optional[Callable] = None,
                    smd: str = SMD) -> Dict[str, dict]:
    """Run each named command and write `<out_dir>/<name>.json`.

    Each name runs on its own: one that fails, times out or prints
    something other than JSON gets an envelope saying so, and the rest
    are still written.  An unknown name raises KeyError before anything
    runs.  Returns the envelopes by name.
    """
    # Resolved at call time, so a test's patch of subprocess.run applies.
    run = run or subprocess.run
    now = now or time.time
    jobs = [(name, SNAPSHOT_COMMANDS[name]) for name in names]
    out_dir = Path(out_dir)
    written: Dict[str, dict] = {}
    for name, args in jobs:
        envelope = _run_one(name, args, run, now, smd)
        _write_atomic(out_dir / f"{name}.json", json.dumps(envelope) + "\n")
        written[name] = envelope
    return written


def take_requests(req_dir: Path, last_run: float, now: float,
                  min_interval_s: float = MIN_REQUEST_INTERVAL_S) -> bool:
    """Delete every request file in `req_dir`; return whether a refresh
    should run.

    It should when at least one request was waiting and the last requested
    refresh ran `min_interval_s` or more ago.  The files go either way:
    left in place, they would set sigmond-snapshot-request.path off again
    the moment its service finished.
    """
    try:
        entries = list(Path(req_dir).iterdir())
    except FileNotFoundError:
        return False
    asked = False
    for entry in entries:
        if entry.is_dir() and not entry.is_symlink():
            continue
        try:
            entry.unlink()
        except FileNotFoundError:
            pass
        asked = True
    return asked and now - last_run >= min_interval_s


def _last_request(out_dir: Path) -> float:
    try:
        return float((out_dir / LAST_REQUEST).read_text().strip())
    except (OSError, ValueError):
        return 0.0


def write_requested(req_dir: Path, out_dir: Path,
                    run: Optional[Callable] = None,
                    now: Optional[Callable] = None,
                    smd: str = SMD) -> bool:
    """Answer station-web's refresh requests: consume them, and write the
    check snapshot if take_requests allows.  The time of the last answered
    request lives in `<out_dir>/.last-request`.  Returns whether it wrote."""
    now = now or time.time
    out_dir = Path(out_dir)
    t = now()
    if not take_requests(req_dir, _last_request(out_dir), t):
        return False
    # Recorded before the run, so a check that fails still holds the limit.
    _write_atomic(out_dir / LAST_REQUEST, f"{t}\n")
    write_snapshots(["check"], out_dir, run=run, now=now, smd=smd)
    return True


def cmd_write(only: Optional[str] = None, request: bool = False) -> int:
    """`smd admin snapshot write [--only NAME | --request]`.

    Exits 0 once the envelopes are written, whatever the commands returned:
    each envelope records its own command's failure.
    """
    if request:
        if write_requested(REQUEST_DIR, OUT_DIR):
            print("check: refreshed on request")
        else:
            print("no refresh: no request waiting, or one ran under "
                  f"{MIN_REQUEST_INTERVAL_S:.0f} s ago")
        return 0
    names = [only] if only else list(SNAPSHOT_COMMANDS)
    for name, env in write_snapshots(names, OUT_DIR).items():
        line = f"{name}: exit {env['exit']}"
        if env["error"]:
            line += f" ({env['error'].splitlines()[-1]})"
        print(line)
    return 0

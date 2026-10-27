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
import shutil
import subprocess
import sys
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
        # errors="replace": a stray non-UTF-8 byte must not cost the
        # snapshot; json.loads below judges what it got.
        proc = run([smd, *args], stdin=subprocess.DEVNULL,
                   capture_output=True, text=True, errors="replace",
                   timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        envelope["error"] = "timed out"
        return envelope
    except (OSError, subprocess.SubprocessError) as exc:
        envelope["error"] = f"could not run: {exc}"
        return envelope
    except Exception as exc:
        # Anything else still becomes an envelope, never a missing file.
        envelope["error"] = f"{type(exc).__name__}: {exc}"
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
    something other than JSON gets an envelope saying so, and one whose
    file cannot be written is reported on stderr and left out of the
    result; the rest are still written.  An unknown name raises KeyError
    before anything runs.  Returns the envelopes written, by name.
    """
    # Resolved at call time, so a test's patch of subprocess.run applies.
    run = run or subprocess.run
    now = now or time.time
    jobs = [(name, SNAPSHOT_COMMANDS[name]) for name in names]
    out_dir = Path(out_dir)
    written: Dict[str, dict] = {}
    for name, args in jobs:
        envelope = _run_one(name, args, run, now, smd)
        try:
            _write_atomic(out_dir / f"{name}.json",
                          json.dumps(envelope) + "\n")
        except Exception as exc:
            print(f"{name}: could not write the snapshot: {exc}",
                  file=sys.stderr)
            continue
        written[name] = envelope
    return written


def _consume_requests(req_dir: Path) -> int:
    """Remove every entry in `req_dir` and return how many there were.

    Every entry counts as a request and every entry goes, whatever its
    kind: sigmond-snapshot-request.path's PathExistsGlob matches
    directories and symlinks too, so one left behind would set the path
    unit off again the moment its service finished, until systemd failed
    it.  Symlinks are unlinked, never followed.
    """
    try:
        entries = list(Path(req_dir).iterdir())
    except FileNotFoundError:
        return 0
    for entry in entries:
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
            continue
        try:
            entry.unlink()
        except FileNotFoundError:
            pass
    return len(entries)


def take_requests(req_dir: Path, last_run: float, now: float,
                  min_interval_s: float = MIN_REQUEST_INTERVAL_S) -> bool:
    """Remove every request in `req_dir`; return whether a refresh may run
    now: at least one request waited, and the last requested refresh ran
    `min_interval_s` or more ago."""
    asked = _consume_requests(req_dir) > 0
    return asked and now - last_run >= min_interval_s


def _last_request(out_dir: Path) -> float:
    try:
        return float((out_dir / LAST_REQUEST).read_text().strip())
    except (OSError, ValueError):
        return 0.0


def write_requested(req_dir: Path, out_dir: Path,
                    run: Optional[Callable] = None,
                    now: Optional[Callable] = None,
                    sleep: Optional[Callable] = None,
                    smd: str = SMD,
                    min_interval_s: float = MIN_REQUEST_INTERVAL_S
                    ) -> Optional[Dict[str, dict]]:
    """Answer station-web's refresh requests with one check snapshot.

    It consumes every waiting request first, so a burst collapses into one
    refresh.  No request is ever dropped: when the last requested refresh
    ran under `min_interval_s` ago, it sleeps out the remainder and then
    refreshes.  A request that arrives during the sleep stays in place and
    sets the path unit off again afterwards.  The time of the last answered
    request lives in `<out_dir>/.last-request`.

    Returns None when no request waited, else write_snapshots' result
    (empty if the check snapshot could not be written).
    """
    now = now or time.time
    sleep = sleep or time.sleep
    out_dir = Path(out_dir)
    if _consume_requests(req_dir) == 0:
        return None
    last = _last_request(out_dir)
    # Clamped: a stamp from the future (the clock stepped back) must not
    # make this oneshot sleep for hours while "Check now" waits.
    wait = min(max(min_interval_s - (now() - last), 0.0), min_interval_s)
    if wait > 0:
        sleep(wait)
    try:
        return write_snapshots(["check"], out_dir, run=run, now=now, smd=smd)
    finally:
        # Recorded after the run, at the clock's time, so a future stamp
        # heals itself; in `finally`, so a run that fails still holds the
        # limit.
        _write_atomic(out_dir / LAST_REQUEST, f"{now()}\n")


def _print_written(names: List[str], written: Dict[str, dict]) -> int:
    for name in names:
        env = written.get(name)
        if env is None:
            print(f"{name}: NOT WRITTEN")
            continue
        line = f"{name}: exit {env['exit']}"
        if env["error"]:
            line += f" ({env['error'].splitlines()[-1]})"
        print(line)
    return 0 if all(n in written for n in names) else 1


def cmd_write(only: Optional[str] = None, request: bool = False) -> int:
    """`smd admin snapshot write [--only NAME | --request]`.

    Exits 0 once every envelope is written, whatever the commands
    returned (each envelope records its own command's failure), and 1
    when any snapshot file could not be written.
    """
    if request:
        written = write_requested(REQUEST_DIR, OUT_DIR)
        if written is None:
            print("no refresh: no request waiting")
            return 0
        return _print_written(["check"], written)
    names = [only] if only else list(SNAPSHOT_COMMANDS)
    return _print_written(names, write_snapshots(names, OUT_DIR))

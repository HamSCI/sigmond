"""Read the sink switches through ``hs-uploader sink status --json``.

Spec §7.5 and Ruling 12: hs-uploader holds the one implementation of the
read rules (stricter of site and client, a fault reads ``off``), so sigmond
asks it rather than reading ``sinks/`` itself.  The call goes through the
daemon's own interpreter with :func:`uploader_manifest.run_hs_uploader`
(``python -I -m hs_uploader.cli`` from ``/``; ruling C14-2).

:func:`read_state` returns ``None`` whenever it cannot trust what came
back, and prints one line to stderr that says why:

- the call failed to run, or timed out;
- it exited non-zero.  Exit 2 means the manifest stood unreadable; the
  JSON it still prints then lists no pipeline at all, so "nothing held"
  would be a false reading.  The line quotes the JSON's first error;
- the output was not JSON, lacked a field, or carried a ``schema`` other
  than 1 (V14).

The caller then falls back to ``[uploads]`` and says so; the carry-over
aborts before the marker (ruling X4).

A state that did come back may still carry ``errors`` (the store stood
unreadable, so the daemon's recorded refusals do not show).  Such a state
reads as :attr:`SinkState.partial`; callers show its errors, and never
read an empty ``refused_files`` there as "nothing refused".

Each client's ``effective`` state does not depend on the carry-over
marker (ruling X5); ``enforced`` and ``held_pipelines`` do.  So
:func:`below_upload` reads ``effective`` alone.
"""
from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import safe_write, uploader_manifest

SCHEMA = 1
UPLOAD = "upload"

# The carry-over marker (spec §1, ruling X15: one helper set on the sigmond
# side).  Same path as hs-uploader's ``sinkswitch.marker_path()`` under its
# default state directory.
MARKER_NAME = "carry-over-v1.done"
STATE_DIR = Path("/var/lib/hs-uploader")
MARKER_PATH = STATE_DIR / MARKER_NAME
SINKS_DIR = Path("/etc/hs-uploader/sinks")


def marker_stands(path: Path = MARKER_PATH) -> bool:
    """True when anything stands at ``path``, a dangling link included, as
    hs-uploader's ``sinkswitch.marker_stands`` reads it (Ruling 4).  Only
    "no such file" reads absent; any other error (EACCES on the directory)
    reads as standing, so nothing carries over twice on a guess."""
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


UPDATE_NOTICE = "the sink switches take effect at the next `smd align`"


def update_notice(*, marker: Path = MARKER_PATH,
                  python: Path = uploader_manifest.HS_UPLOADER_PYTHON
                  ) -> Optional[str]:
    """The line `smd update` prints (spec §6.1): the carry-over runs in the
    manifest step alone, which `smd update` never reaches.  So while the
    marker stands absent and the installed hs-uploader offers ``gate``,
    the switches wait for the next `smd align`.  None otherwise; a failed
    features probe reads as no ``gate``.  Reads only."""
    if marker_stands(marker):
        return None
    features = uploader_manifest.hs_uploader_features(python=python)
    if features is None or uploader_manifest.GATE_FEATURE not in features:
        return None
    return UPDATE_NOTICE


ROLLED_BACK = ".rolled-back-"
_ASIDE_TRIES = 100


def _move_noreplace(dfd: int, src: str, dst: str) -> bool:
    """Give `src` the name `dst` inside the directory `dfd`, never replacing
    anything at `dst`; False when `dst` is taken.

    A hard link (``linkat`` without AT_SYMLINK_FOLLOW, so a symlink links
    as itself), then the old name unlinked: the link fails with EEXIST
    rather than replace.  An object that cannot be hard-linked (a
    directory) is renamed after a check that `dst` stands empty."""
    try:
        os.link(src, dst, src_dir_fd=dfd, dst_dir_fd=dfd,
                follow_symlinks=False)
    except FileExistsError:
        return False
    except OSError as e:
        if e.errno not in (errno.EPERM, errno.EMLINK, errno.EOPNOTSUPP,
                           errno.EXDEV):
            raise
        try:
            os.stat(dst, dir_fd=dfd, follow_symlinks=False)
            return False
        except FileNotFoundError:
            pass
        os.rename(src, dst, src_dir_fd=dfd, dst_dir_fd=dfd)
        return True
    os.unlink(src, dir_fd=dfd)
    return True


def move_marker_aside(path: Path = MARKER_PATH, *,
                      now: Optional[datetime] = None) -> Optional[Path]:
    """Move the carry-over marker to ``<name>.rolled-back-<UTC>`` (spec
    §6.7; plan Ruling 10), so the carry-over runs again once the gate
    returns.  Returns the new path, or ``None`` when nothing stood there.

    The rename runs inside a descriptor on the marker's directory, opened
    without following a link there; the marker itself moves as whatever it
    is (a symlink moves as a link, its target untouched).  A rolled-back
    name already taken (a second move within one second) is never
    replaced: ``.1``, ``.2`` ... follow.  The directory is fsynced.
    Raises OSError when the directory cannot be opened or the move fails.
    Task 22's restore reuses this helper."""
    path = Path(path)
    when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    base = f"{path.name}{ROLLED_BACK}{when.strftime('%Y%m%dT%H%M%SZ')}"
    dfd = safe_write._open_parent(path.parent)
    try:
        try:
            os.stat(path.name, dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        for i in range(_ASIDE_TRIES):
            name = base if i == 0 else f"{base}.{i}"
            try:
                moved = _move_noreplace(dfd, path.name, name)
            except FileNotFoundError:
                return None             # gone meanwhile: nothing stands
            if moved:
                os.fsync(dfd)
                return path.parent / name
        raise FileExistsError(errno.EEXIST, f"{path.parent / base}: "
                              f"{_ASIDE_TRIES} rolled-back names taken")
    finally:
        os.close(dfd)


@dataclass(frozen=True)
class SiteState:
    state: str              # upload, fill, off, missing or error
    error: str
    set_at: str
    set_by: str
    reason: str


@dataclass(frozen=True)
class ClientState:
    file_state: str         # what the client's own file says
    effective: str          # the stricter of site and client; a fault: off
    held_by: Optional[str]  # site, client, fault, or None while it uploads
    error: str
    pipelines: tuple = ()


@dataclass(frozen=True)
class HeldPipeline:
    pipeline: str
    client: Optional[str]
    held_by: str            # site, client or fault
    reason: str


@dataclass(frozen=True)
class RefusedFile:
    file: Optional[str]     # None: refused for the store's schema
    pipeline: Optional[str]
    client: Optional[str]
    reason: str


@dataclass(frozen=True)
class SinkState:
    enforced: bool
    site: SiteState
    clients: dict
    held_pipelines: tuple = ()
    refused_files: tuple = ()
    no_client_pipelines: tuple = ()
    builder_pipelines: tuple = ()
    errors: tuple = ()
    marker: str = ""
    sinks_dir: str = ""

    @property
    def partial(self) -> bool:
        """hs-uploader could not read everything; ``errors`` says what."""
        return bool(self.errors)


def below_upload(state: SinkState) -> list:
    """The clients whose effective state reads below ``upload``, sorted.

    Reads ``effective``, never ``file_state``: under site ``off`` a client
    whose own file says ``upload`` sends nothing.  A state this sigmond
    does not know also reads below ``upload``."""
    return sorted(n for n, c in state.clients.items()
                  if c.effective != UPLOAD)


def _say(msg: str) -> None:
    print(f"sigmond: cannot read hs-uploader sink status: {msg}",
          file=sys.stderr)


def _str(v) -> str:
    if not isinstance(v, str):
        raise TypeError(f"expected a string, got {type(v).__name__}")
    return v


def _opt(v) -> Optional[str]:
    return None if v is None else _str(v)


def _names(v) -> tuple:
    if not isinstance(v, list):
        raise TypeError(f"expected a list, got {type(v).__name__}")
    return tuple(_str(x) for x in v)


def _build(d: dict) -> SinkState:
    if not isinstance(d["enforced"], bool):
        raise TypeError("enforced is not a boolean")
    s = d["site"]
    site = SiteState(state=_str(s["state"]), error=_str(s["error"]),
                     set_at=_str(s["set_at"]), set_by=_str(s["set_by"]),
                     reason=_str(s["reason"]))
    clients = {}
    for c in d["clients"]:
        clients[_str(c["client"])] = ClientState(
            file_state=_str(c["file_state"]), effective=_str(c["effective"]),
            held_by=_opt(c["held_by"]), error=_str(c["error"]),
            pipelines=_names(c["pipelines"]))
    held = tuple(HeldPipeline(pipeline=_str(h["pipeline"]),
                              client=_opt(h["client"]),
                              held_by=_str(h["held_by"]),
                              reason=_str(h["reason"]))
                 for h in d["held_pipelines"])
    refused = tuple(RefusedFile(file=_opt(r["file"]),
                                pipeline=_opt(r["pipeline"]),
                                client=_opt(r["client"]),
                                reason=_str(r["reason"]))
                    for r in d["refused_files"])
    return SinkState(
        enforced=d["enforced"], site=site, clients=clients,
        held_pipelines=held, refused_files=refused,
        no_client_pipelines=_names(d["no_client_pipelines"]),
        builder_pipelines=_names(d["builder_pipelines"]),
        errors=_names(d["errors"]),
        marker=_str(d["marker"]), sinks_dir=_str(d["sinks_dir"]))


def parse_status(text: str) -> Optional[SinkState]:
    """Parse ``sink status --json`` output.  ``None``, with one line on
    stderr, for anything but a complete schema-1 document."""
    try:
        d = json.loads(text)
    except ValueError:
        _say("the output is not JSON")
        return None
    if not isinstance(d, dict):
        _say("the output is not a JSON object")
        return None
    schema = d.get("schema")
    if schema != SCHEMA or isinstance(schema, bool):
        _say(f"it reports schema {schema!r}; this sigmond reads schema "
             f"{SCHEMA} only")
        return None
    try:
        return _build(d)
    except (KeyError, TypeError, AttributeError) as exc:
        _say(f"a field is missing or malformed ({type(exc).__name__}: "
             f"{exc})")
        return None


def _first_error(stdout: str, stderr: str) -> str:
    try:
        errs = json.loads(stdout).get("errors")
        if isinstance(errs, list) and errs and isinstance(errs[0], str):
            return errs[0]
    except (ValueError, AttributeError):
        pass
    lines = [ln for ln in (stderr or "").splitlines() if ln.strip()]
    return lines[-1].strip() if lines else "no message"


def read_state(*, python: Path = uploader_manifest.HS_UPLOADER_PYTHON,
               manifest: Optional[str] = None, sinks: Optional[str] = None,
               marker: Optional[str] = None, store: Optional[str] = None,
               timeout: float = 20) -> Optional[SinkState]:
    """Ask the hs-uploader the service runs what the switches say.

    With no paths, hs-uploader reads its production defaults (the legacy
    manifest, ``sinks/`` beside it, the marker and store under
    ``$HS_UPLOADER_STATE_DIR``).  Returns ``None`` when the call fails,
    times out, exits non-zero, or returns anything but schema 1; one line
    on stderr then says why."""
    args = ["--state", str(store)] if store is not None else []
    args += ["sink", "status", "--json"]
    for flag, val in (("--manifest", manifest), ("--sinks", sinks),
                      ("--marker", marker)):
        if val is not None:
            args += [flag, str(val)]
    try:
        r = uploader_manifest.run_hs_uploader(args, python=python,
                                              timeout=timeout)
    except subprocess.TimeoutExpired:
        _say(f"the call timed out after {timeout:g} s")
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        _say(f"the call failed ({type(exc).__name__}: {exc})")
        return None
    if r.returncode != 0:
        _say(f"exit {r.returncode}: "
             f"{_first_error(r.stdout or '', r.stderr or '')}")
        return None
    return parse_status(r.stdout or "")

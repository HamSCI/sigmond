"""Pure-Python helpers for the in-TUI Textual config wizard.

Companion to ``format.py``: this module has NO Textual imports — every
function here is subprocess glue, a filesystem reader, or a plain string
lookup.  It exists for the same reason ``format.py`` does: the screen
module that consumes these helpers (``screens/textual_wizard.py``)
imports ``textual`` at module scope, which makes the *screen* module
unimportable without the ``[tui]`` extra.  The logic itself needs no
Textual widget or container type, so it lives here where it can be
unit-tested (and imported by anything else, e.g. a future non-Textual
wizard) on a bare interpreter.

``screens/textual_wizard.py`` imports every one of these rather than
keeping its own copy — this is the single source of truth for:

- ``load_config_via_show`` / ``load_help_toml`` / ``help_label`` /
  ``help_entry`` / ``_help_toml_candidates`` — the ``config show`` +
  ``help.toml`` sidecar glue described in that module's docstring.
- ``instance_tag_from_path`` — per-instance config path parsing
  (``TextualConfigWizardScreen._instance_tag_from_path`` is a thin
  ``staticmethod`` alias onto this function).
- ``run_with_stdin`` — piping a JSON payload to ``config apply --json -``
  over stdin, with a sudo fast-path/fallback.  Historically lived in
  ``tui/mutation.py`` (which re-exports it for backward compatibility),
  but it never actually touched Textual: the ``app`` parameter is only
  ever used for its duck-typed ``.suspend()`` context manager.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ModuleNotFoundError:  # py<3.11
    import tomli as tomllib  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Subprocess helpers (kept module-level so tests can monkeypatch them).
# ---------------------------------------------------------------------------

def load_config_via_show(
    client_bin: str,
    config_path: Optional[str] = None,
) -> tuple[Optional[dict], str]:
    """Run ``<client_bin> config show --json --defaults`` and parse JSON.

    When ``config_path`` is given, ``--config <path>`` is appended so
    the client reads the per-instance file at
    ``/etc/<client>/<reporter-id>.toml`` instead of its legacy shared
    config (MULTI-INSTANCE-ARCHITECTURE.md §4).  When ``config_path`` is
    None the client picks its own default.

    Returns ``(data, error)``.  ``data`` is the parsed dict on success,
    None on failure; ``error`` is a human-readable string for display.
    """
    argv = [client_bin, 'config', 'show', '--json', '--defaults']
    if config_path:
        argv.extend(['--config', config_path])
    try:
        proc = subprocess.run(
            argv, capture_output=True, check=False, text=True,
        )
    except OSError as exc:
        return None, f"failed to exec {client_bin}: {exc}"
    if proc.returncode != 0:
        return None, (
            f"`{client_bin} config show` exited {proc.returncode}: "
            f"{proc.stderr.strip() or '(no stderr)'}"
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return None, f"`{client_bin} config show` stdout was not JSON: {exc}"
    if not isinstance(data, dict):
        return None, (
            f"`{client_bin} config show` returned "
            f"{type(data).__name__}, expected object"
        )
    return data, ""


# ---------------------------------------------------------------------------
# help.toml — per-client operator-help sidecar.  Same file the whiptail
# wizard reads; we share the schema-of-truth across both renderers.
# ---------------------------------------------------------------------------

def _help_toml_candidates(client_name: str) -> list[Path]:
    """Where to look for ``<client>/config/help.toml``.

    Sigmond's editable-install path first, then the packaged-install
    fallback.  Both psk-recorder and mag-recorder live at the first
    path on production hosts; the second covers operators who installed
    from a package.
    """
    return [
        Path(f"/opt/git/sigmond/{client_name}/config/help.toml"),
        Path(f"/usr/local/share/{client_name}/help.toml"),
    ]


def load_help_toml(client_name: str) -> dict:
    """Read the client's ``config/help.toml`` if present.

    Returns a dict shaped like::

        {"station": {"callsign": {"title": "Amateur callsign", "help": "...",
                                  "example": "AC0G", "validator_hint": "...",
                                  "required": True},
                     "grid_square": {...}, ...},
         "radiod":  {"id":            {...},
                     "radiod_status": {...}}, ...}

    Returns ``{}`` when the file is absent or unreadable — the renderer
    just falls back to bare key names.  Errors are swallowed because
    operator help is a UX nicety, not a contract dependency.
    """
    for path in _help_toml_candidates(client_name):
        if not path.is_file():
            continue
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def help_label(help_data: dict, section: str, key: str) -> str:
    """Look up the operator-facing label for one (section, key).

    Returns the help.toml ``title`` when present, the bare key name
    otherwise.  Used so the form's left-column label is human-readable
    on clients with a help sidecar and still legible on clients without.
    """
    block = help_data.get(section, {})
    if isinstance(block, dict):
        entry = block.get(key, {})
        if isinstance(entry, dict):
            title = entry.get("title")
            if isinstance(title, str) and title.strip():
                return title
    return key


def help_entry(help_data: dict, section: str, key: str) -> dict:
    """Look up the full per-key help block for one (section, key).

    Returns the help.toml subtable as a dict (with keys ``title``,
    ``help``, ``example``, ``validator_hint``, ``required``) when the
    client ships a help sidecar entry; an empty dict otherwise.
    Used so the wizard can surface placeholder text, required-field
    markers, validator hints, and focus-driven help bodies — every
    piece of operator guidance the help.toml authors put in is
    consumed by the form.
    """
    block = help_data.get(section, {})
    if isinstance(block, dict):
        entry = block.get(key, {})
        if isinstance(entry, dict):
            return entry
    return {}


# ---------------------------------------------------------------------------
# Per-instance config path parsing.
# ---------------------------------------------------------------------------

def instance_tag_from_path(config_path: str) -> str:
    """Best-effort extraction of the reporter-id from a per-instance
    config path.  ``/etc/psk-recorder/AC0G-B1.toml`` → ``AC0G-B1``.
    Returns ``""`` when the path doesn't look reporter-keyed.

    ``TextualConfigWizardScreen._instance_tag_from_path`` is a
    ``staticmethod`` alias onto this function — kept here so it's
    reachable without importing Textual.
    """
    stem = Path(config_path).stem
    # Strip well-known legacy suffixes so e.g. the legacy
    # ``psk-recorder-config`` doesn't get treated as a reporter id.
    if stem in {"config", "psk-recorder-config",
                "wspr-recorder-config", "hfdl-recorder-config",
                "codar-sounder-config"} or stem.endswith("-config"):
        return ""
    return stem


# ---------------------------------------------------------------------------
# run_with_stdin — pipe a JSON payload to a CLI's ``config apply`` over
# stdin, with a sudo fast-path/fallback.  Moved here from ``mutation.py``
# (which re-exports it for backward compatibility): despite living
# alongside Textual's ConfirmModal/suspend_and_run_sudo there, this
# function never touches Textual itself — ``app`` is only ever used for
# its duck-typed ``.suspend()`` context manager on the slow path.
# ---------------------------------------------------------------------------

def run_with_stdin(
    app: Any,
    cmd: list,
    stdin_bytes: bytes,
    sudo: bool = False,
) -> subprocess.CompletedProcess:
    """Run ``cmd`` with ``stdin_bytes`` piped to stdin, capturing stdout/stderr.

    Used by in-TUI editors that drive a CLI's ``config apply --json -``
    contract.  Unlike ``suspend_and_run_sudo``, this does NOT suspend
    the app on the happy path — the child runs silently with captured
    streams and the TUI never blanks.

    When ``sudo=True`` the call tries ``sudo -n`` first (works if the
    operator has NOPASSWD configured for the command).  If that's
    rejected with the well-known "a password is required" exit, the
    function falls back to suspending the app and running ``sudo``
    interactively so the operator can type their password in the
    real terminal; on resume, stderr is no longer captured (the
    operator already saw it) and ``result.stderr`` is set to "" for
    parity with the captured path.

    ``app`` is duck-typed: any object with a ``suspend()`` context
    manager works (in production, a Textual ``App``; tests pass a
    ``Mock``).

    Returns the :class:`subprocess.CompletedProcess` with bytes for
    stdout/stderr (decoded to str for the suspended fallback so callers
    don't have to special-case the two paths).
    """
    if not sudo:
        return subprocess.run(
            cmd, input=stdin_bytes,
            capture_output=True, check=False,
        )

    # Fast path: try sudo -n (no password prompt).
    fast = subprocess.run(
        ['sudo', '-n', *cmd], input=stdin_bytes,
        capture_output=True, check=False,
    )
    # sudo prints "a password is required" (or similar) to stderr and
    # exits 1 when -n can't run without a prompt.  Distinguish that
    # from a genuine command failure by looking at stderr.
    needs_password = (
        fast.returncode != 0
        and (b'password is required' in fast.stderr
             or b'a terminal is required' in fast.stderr
             or b'sudo: a password' in fast.stderr)
    )
    if not needs_password:
        return fast

    # Slow path: suspend so the operator can type a password.  The
    # child inherits the parent's TTY for stdin (so sudo can prompt),
    # but we still pipe our JSON payload via a tempfile so the child's
    # actual stdin reader sees our bytes after sudo finishes authing.
    #
    # Implementation: write stdin to a NamedTemporaryFile, then run
    # `sudo <cmd> < tmpfile`.  Tempfile is removed in finally.
    tmp = tempfile.NamedTemporaryFile(delete=False)
    try:
        tmp.write(stdin_bytes)
        tmp.flush()
        tmp.close()
        with app.suspend():
            with open(tmp.name, 'rb') as fh:
                result = subprocess.run(
                    ['sudo', *cmd], stdin=fh, check=False,
                )
        # Suspended path didn't capture stdout/stderr; surface empty
        # bytes so callers can treat the return value uniformly.
        return subprocess.CompletedProcess(
            args=result.args, returncode=result.returncode,
            stdout=b'', stderr=b'',
        )
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

"""`smd ka9q-watch` — flag upstream ka9q-radio changes that could break clients.

Thin wrapper around ka9q-python's ``scripts/check_upstream_drift.py``:
locates the ka9q-python and ka9q-radio source trees, runs the checker
with ``--json``, and renders the result with sigmond's UI conventions.

Severity model (from the underlying script):

  pass  — no upstream commits, or upstream advanced but no header changed
  warn  — header changed but no stream-critical field affected
  fail  — a stream-critical TLV/enum value shifted (RTP delivery at risk)

Exit code is the script's: 0 on pass/warn, 1 on fail, 2 on setup error.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from ..ui import err, heading, info, ok, warn


# Standard sigmond install location for client/library checkouts.
SIGMOND_GIT_ROOT = Path("/opt/git/sigmond")

# Dev-mode fallbacks — looked at only when the standard path is absent.
DEV_LOCATIONS = [
    Path("/home/wsprdaemon"),
    Path.home(),
]

_SEVERITY_GLYPH = {
    "pass": "\033[32m✓\033[0m",
    "warn": "\033[33m⚠\033[0m",
    "fail": "\033[31m✗\033[0m",
}


def _resolve_path(name: str, override: Optional[str], env_var: str) -> Optional[Path]:
    """Resolve a checkout path with this priority:
       1. explicit --flag override
       2. environment variable
       3. /opt/git/sigmond/<name>
       4. dev fallbacks (~, /home/wsprdaemon)
    """
    if override:
        p = Path(override).expanduser().resolve()
        return p if p.exists() else None

    env_val = os.environ.get(env_var)
    if env_val:
        p = Path(env_val).expanduser().resolve()
        return p if p.exists() else None

    standard = SIGMOND_GIT_ROOT / name
    if standard.exists():
        return standard.resolve()

    for base in DEV_LOCATIONS:
        candidate = base / name
        if candidate.exists():
            return candidate.resolve()

    return None


def _render_human(report: dict) -> None:
    sev = report.get("severity", "fail")
    glyph = _SEVERITY_GLYPH.get(sev, "?")
    summary = report.get("summary") or report.get("error") or "(no summary)"

    heading('ka9q-watch')
    print(f"  {glyph}  {summary}")

    pin  = report.get("pin")
    up   = report.get("upstream_sha")
    ref  = report.get("upstream_ref")
    if pin:
        print(f"     pin:      {pin[:12]}")
    if up:
        print(f"     upstream: {up[:12]}  ({ref or '?'})")

    commits = report.get("commits") or []
    if commits:
        print(f"     commits:  {len(commits)} ahead")
        for c in commits[-10:]:
            mark = "H" if c.get("touches_headers") else " "
            print(f"       [{mark}] {c['sha'][:12]}  {c['subject']}")
        if len(commits) > 10:
            print(f"       … {len(commits) - 10} earlier commit(s) elided")

    for d in report.get("header_deltas") or []:
        sym = _SEVERITY_GLYPH.get(d.get("severity", "warn"), "?")
        print(f"     {sym}  {d['header']} ({d['enum']}):")
        for c in d.get("changes", []):
            csym = _SEVERITY_GLYPH.get(c.get("severity", "warn"), "?")
            kind = c.get("kind")
            name = c.get("name", "?")
            if kind == "added":
                detail = f"+{name} = {c.get('head')}"
            elif kind == "removed":
                detail = f"-{name}  (was {c.get('pin')})"
            elif kind == "value_changed":
                detail = f"~{name}: {c.get('pin')} → {c.get('head')}"
            else:
                detail = f"?{name}"
            print(f"         {csym}  {detail}  — {c.get('reason', '')}")

    if sev == "fail":
        print()
        info("Do NOT advance the ka9q-radio pin until ka9q-python is updated")
        info("to handle the changed fields, or downstream RTP clients will break.")


def cmd_ka9q_watch(args) -> int:
    py_root = _resolve_path("ka9q-python", getattr(args, "ka9q_python", None),
                            "KA9Q_PYTHON_PATH")
    if py_root is None:
        err("ka9q-python checkout not found")
        info("Tried: --ka9q-python, $KA9Q_PYTHON_PATH, "
             "/opt/git/sigmond/ka9q-python, ~/ka9q-python")
        return 2

    radio_root = _resolve_path("ka9q-radio", getattr(args, "ka9q_radio", None),
                               "KA9Q_RADIO_PATH")
    if radio_root is None:
        err("ka9q-radio checkout not found")
        info("Tried: --ka9q-radio, $KA9Q_RADIO_PATH, "
             "/opt/git/sigmond/ka9q-radio, ~/ka9q-radio")
        return 2

    script = py_root / "scripts" / "check_upstream_drift.py"
    if not script.exists():
        err(f"drift checker missing: {script}")
        info("ka9q-python may be older than the watcher; pull latest.")
        return 2

    python_bin = shutil.which("python3") or sys.executable
    cmd = [python_bin, str(script),
           "--ka9q-radio", str(radio_root),
           "--remote", getattr(args, "remote", None) or "origin",
           "--branch", getattr(args, "branch", None) or "main",
           "--json"]
    if getattr(args, "no_fetch", False):
        cmd.append("--no-fetch")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        err(f"could not invoke {script}: {exc}")
        return 2

    # Parse JSON if present; otherwise surface stderr.
    report: Optional[dict] = None
    if proc.stdout.strip():
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass

    if getattr(args, "json", False):
        if report is not None:
            json.dump(report, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        return proc.returncode

    if report is None:
        err("drift checker emitted no JSON")
        if proc.stderr:
            info(proc.stderr.strip())
        return proc.returncode or 2

    _render_human(report)
    return proc.returncode

"""Calls into clients' `storage` verbs (CLIENT-CONTRACT §17.7), and the stop lever.

Binary resolution belongs to the caller: the contract adapter's
`find_binary()` locates a client's binary, so callers pass the result in.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Mapping, Optional

from .. import lifecycle

HOLDINGS_TIMEOUT_S = 10
TRIM_TIMEOUT_S = 120

NEVER_STOP = frozenset({"radiod", "ka9q-radio", "ka9q-web", "sigmond"})


@dataclass(frozen=True)
class ClientAnswer:
    ok: bool
    held_bytes: Optional[int] = None
    bytes_by_class: Mapping[str, int] = field(default_factory=dict)
    refused: bool = False
    reason: str = ""
    unsupported: bool = False
    bytes_written_per_day: Optional[int] = None    # `holdings` only, optional


def _fail(reason: str, unsupported: bool = False) -> ClientAnswer:
    return ClientAnswer(False, None, {}, False, reason, unsupported)


def _invoke(argv: list, timeout: int, run) -> ClientAnswer:
    try:
        proc = run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _fail(f"timed out after {timeout}s")
    except OSError as exc:
        return _fail(f"could not run: {exc}")
    if proc.returncode == 2:
        return _fail("verb not supported (older client)", unsupported=True)
    if proc.returncode != 0:
        return _fail(f"exit {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return _fail("output is not JSON")
    if not isinstance(data, dict):
        return _fail("output is not a JSON object")
    by_class = data.get("bytes_by_class")
    if not isinstance(by_class, dict) or not all(
            isinstance(v, int) and not isinstance(v, bool) for v in by_class.values()):
        return _fail("bytes_by_class missing or malformed")
    held = data.get("held_bytes")
    if held is None:
        held = sum(by_class.values())      # `holdings` carries no held_bytes
    if not isinstance(held, int) or isinstance(held, bool):
        return _fail("held_bytes malformed")
    written = data.get("bytes_written_per_day")
    if not isinstance(written, int) or isinstance(written, bool) or written < 0:
        written = None                     # optional; a malformed rate drops alone
    return ClientAnswer(True, held, dict(by_class), bool(data.get("refused", False)),
                        str(data.get("reason", "")), False, written)


def call_holdings(binary: str, run=subprocess.run) -> ClientAnswer:
    return _invoke([binary, "storage", "holdings", "--json"], HOLDINGS_TIMEOUT_S, run)


def call_trim(binary: str, fit_bytes: int, run=subprocess.run) -> ClientAnswer:
    return _invoke([binary, "storage", "trim", "--fit-bytes", str(fit_bytes), "--json"],
                   TRIM_TIMEOUT_S, run)


def call_final(binary: str, fit_bytes: int, policy: Optional[str],
               run=subprocess.run) -> ClientAnswer:
    argv = [binary, "storage", "final", "--fit-bytes", str(fit_bytes)]
    if policy:
        argv += ["--policy", policy]
    return _invoke(argv + ["--json"], TRIM_TIMEOUT_S, run)


@dataclass(frozen=True)
class ExpireAnswer:
    ok: bool
    deleted: int = 0
    complete: bool = False
    refused: bool = False
    reason: str = ""
    unsupported: bool = False


def call_expire(binary: str, target: str, days: int, max_seconds: int,
                run=subprocess.run) -> ExpireAnswer:
    """Ask a client to delete what lies past `days` in one target.  One call is
    one client process, one target, one database: the kit's privilege drop
    holds for the life of that process."""
    argv = [binary, "storage", "expire", "--target", target, "--days", str(int(days)),
            "--max-seconds", str(int(max_seconds)), "--json"]
    timeout = int(max_seconds) + 60
    try:
        proc = run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ExpireAnswer(False, reason=f"timed out after {timeout}s")
    except OSError as exc:
        return ExpireAnswer(False, reason=f"could not run: {exc}")
    if proc.returncode == 2:
        return ExpireAnswer(False, reason="verb not supported (older client)", unsupported=True)
    if proc.returncode != 0:
        return ExpireAnswer(False, reason=f"exit {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return ExpireAnswer(False, reason="output is not JSON")
    if not isinstance(data, dict):
        return ExpireAnswer(False, reason="output is not a JSON object")
    deleted = data.get("deleted", 0)
    if not isinstance(deleted, int) or isinstance(deleted, bool) or deleted < 0:
        return ExpireAnswer(False, reason="deleted malformed")
    return ExpireAnswer(True, deleted, data.get("complete") is True,
                        data.get("refused") is True, str(data.get("reason", "")))


def _default_resolve(component: str):
    return lifecycle.resolve_units([component], [component])


def stop_client(component: str, resolve=_default_resolve,
                run=subprocess.run) -> tuple[bool, str]:
    """`systemctl stop` each service and timer unit of a client.

    Never disables a unit and never deletes anything; targets are skipped.
    """
    if component in NEVER_STOP:
        return False, f"{component} is never stopped by the storage ledger"
    try:
        refs = list(resolve(component))
    except (ValueError, OSError) as exc:
        return False, f"cannot resolve units of {component}: {exc}"
    stopped = []
    for ref in refs:
        if ref.kind not in ("service", "timer"):
            continue
        try:
            proc = run(["systemctl", "stop", ref.unit], capture_output=True,
                       text=True, timeout=60)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, (f"systemctl stop {ref.unit} failed: {exc}"
                           + (f"; already stopped: {', '.join(stopped)}" if stopped else ""))
        if proc.returncode != 0:
            return False, (f"systemctl stop {ref.unit} failed (exit {proc.returncode})"
                           + (f"; already stopped: {', '.join(stopped)}" if stopped else ""))
        stopped.append(ref.unit)
    if not stopped:
        return False, "no units to stop"
    return True, "stopped " + ", ".join(stopped)


@dataclass(frozen=True)
class CompactAnswer:
    ok: bool
    refused: bool = False
    reason: str = ""
    unsupported: bool = False
    holders: tuple = ()          # (pid, unit | None) pairs; `--holders` only
    data: Mapping = field(default_factory=dict)    # the client's whole answer


def _compact(binary: str, flag: str, target: str, timeout: int, run) -> CompactAnswer:
    argv = [binary, "storage", "compact", flag, "--target", target, "--json"]
    try:
        proc = run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return CompactAnswer(False, reason=f"timed out after {timeout}s")
    except OSError as exc:
        return CompactAnswer(False, reason=f"could not run: {exc}")
    if proc.returncode == 2:
        return CompactAnswer(False, reason="verb not supported (older client)",
                             unsupported=True)
    if proc.returncode != 0:
        return CompactAnswer(False, reason=f"exit {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return CompactAnswer(False, reason="output is not JSON")
    if not isinstance(data, dict):
        return CompactAnswer(False, reason="output is not a JSON object")
    refused = data.get("refused") is True
    reason = str(data.get("reason", ""))
    holders = ()
    if flag == "--holders" and not refused:
        raw = data.get("holders")
        if not isinstance(raw, list):
            return CompactAnswer(False, reason="holders missing or malformed", data=data)
        pairs = []
        for h in raw:
            pid = h.get("pid") if isinstance(h, dict) else None
            unit = h.get("unit") if isinstance(h, dict) else None
            if not isinstance(pid, int) or isinstance(pid, bool) \
                    or not (unit is None or isinstance(unit, str)):
                return CompactAnswer(False, reason="holders malformed", data=data)
            pairs.append((pid, unit or None))
        holders = tuple(pairs)
    return CompactAnswer(True, refused, reason, False, holders, data)


COMPACT_TIMEOUT_S = 3600


def call_compact_prepare(binary: str, target: str, run=subprocess.run) -> CompactAnswer:
    return _compact(binary, "--prepare", target, COMPACT_TIMEOUT_S, run)


def call_compact_holders(binary: str, target: str, run=subprocess.run) -> CompactAnswer:
    return _compact(binary, "--holders", target, HOLDINGS_TIMEOUT_S * 3, run)


def call_compact_swap(binary: str, target: str, run=subprocess.run) -> CompactAnswer:
    return _compact(binary, "--swap", target, COMPACT_TIMEOUT_S, run)

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
    return ClientAnswer(True, held, dict(by_class), bool(data.get("refused", False)),
                        str(data.get("reason", "")), False)


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


def _default_resolve(component: str):
    return lifecycle.resolve_units([component], [component])


def stop_client(component: str, resolve=_default_resolve,
                run=subprocess.run) -> tuple[bool, str]:
    """`systemctl stop` each service and timer unit of a client.

    Never disables a unit and never deletes anything; targets are skipped.
    """
    if component in NEVER_STOP:
        return False, f"{component} is never stopped by the storage ledger"
    stopped = []
    for ref in resolve(component):
        if ref.kind not in ("service", "timer"):
            continue
        proc = run(["systemctl", "stop", ref.unit], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return False, f"systemctl stop {ref.unit} failed (exit {proc.returncode})"
        stopped.append(ref.unit)
    return True, "stopped " + ", ".join(stopped) if stopped else "no units to stop"

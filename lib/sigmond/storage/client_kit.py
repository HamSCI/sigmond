"""Client kit for the contract v0.9 storage verbs (CLIENT-CONTRACT.md 17.7).

A client writes one function that lists its files with their classes.
This module does the rest: it sums holdings, picks what to trim, and
wires `storage holdings|trim|final` onto the client's argparse.

Clients load sigmond lazily with a fallback, so this module imports only
the standard library and `sigmond.storage.model.CLASSES`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from sigmond.storage.model import CLASSES

DAY = 86400.0
# Trim order: sent first, then derived, then raw.  Pinned never goes.
_TRIM_ORDER = tuple(c for c in ("sent", "derived", "raw") if c in CLASSES)


@dataclass(frozen=True)
class Item:
    path: str
    bytes: int
    cls: str
    mtime: float


def holdings(items: Sequence[Item]) -> dict:
    out: dict = {}
    for it in items:
        out[it.cls] = out.get(it.cls, 0) + it.bytes
    return out


def written_per_day(items: Sequence[Item], now: float) -> int:
    """Bytes of items modified within the last 24 h.

    A ring buffer at steady state still writes, so this counts what the
    client wrote, not what it holds.
    """
    return sum(it.bytes for it in items if now - DAY < it.mtime <= now + DAY)


def choose_trim(items: Sequence[Item], fit_bytes: int, floor_bytes: int,
                *, final: bool = False, classes: Optional[Sequence[str]] = None
                ) -> list:
    remaining = sum(it.bytes for it in items)
    chosen: list = []
    order = [c for c in _TRIM_ORDER if classes is None or c in classes]
    for cls in order:
        for it in sorted((i for i in items if i.cls == cls), key=lambda i: i.mtime):
            if remaining <= fit_bytes:
                return chosen
            if not final and remaining - it.bytes < floor_bytes:
                return chosen
            chosen.append(it)
            remaining -= it.bytes
    return chosen


def _answer(items: Sequence[Item], refused: bool = False, reason: str = "") -> dict:
    return {"held_bytes": sum(i.bytes for i in items),
            "bytes_by_class": holdings(items),
            "refused": refused, "reason": reason}


def _delete_all(chosen, delete, items):
    """Delete chosen items; return (items still held, error notes)."""
    gone, errors = set(), []
    for it in chosen:
        try:
            delete(it)
            gone.add(it.path)
        except Exception as exc:  # a failed delete leaves the item held
            errors.append(f"{it.path}: {exc}")
    return [i for i in items if i.path not in gone], errors


def _emit(obj: dict) -> int:
    print(json.dumps(obj))
    return 0


def add_storage_verbs(subparsers, *, list_items: Callable[[], list],
                      delete: Callable[[Item], None], floor_bytes: int,
                      final_policy: str,
                      apply_policy: Optional[Callable[[str, int], dict]] = None
                      ) -> None:
    storage = subparsers.add_parser("storage", help="storage capacity verbs (contract 17.7)")
    verbs = storage.add_subparsers(dest="storage_cmd")

    def do_holdings(args) -> int:
        items = list_items()
        return _emit({"bytes_by_class": holdings(items), "estimated": False,
                      "bytes_written_per_day": written_per_day(items, time.time())})

    def do_trim(args) -> int:
        items = list_items()
        if args.fit_bytes < floor_bytes:
            note = (f"fit {args.fit_bytes} B lies below the floor "
                    f"{floor_bytes} B; keeping the floor")
            refused = True
        else:
            note, refused = "", False
        chosen = choose_trim(items, args.fit_bytes, floor_bytes)
        left, errors = _delete_all(chosen, delete, items)
        reason = "; ".join(filter(None, [note] + (["could not delete " + ", ".join(errors)] if errors else [])))
        return _emit(_answer(left, refused, reason))

    def do_final(args) -> int:
        policy = args.policy or final_policy
        if apply_policy is not None:
            return _emit(apply_policy(policy, args.fit_bytes))
        items = list_items()
        kind, _, cls = policy.partition(":")
        if kind != "give-up-oldest" or cls not in CLASSES or cls == "pinned":
            return _emit(_answer(items, True, f"policy {policy} is not handled by this client"))
        chosen = choose_trim(items, args.fit_bytes, floor_bytes, final=True, classes=[cls])
        left, errors = _delete_all(chosen, delete, items)
        reason = ("could not delete " + ", ".join(errors)) if errors else ""
        return _emit(_answer(left, False, reason))

    p = verbs.add_parser("holdings")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=do_holdings)
    p = verbs.add_parser("trim")
    p.add_argument("--fit-bytes", type=int, required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=do_trim)
    p = verbs.add_parser("final")
    p.add_argument("--fit-bytes", type=int, required=True)
    p.add_argument("--policy", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=do_final)

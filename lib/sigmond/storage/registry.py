"""Build storage registrations from client inventory.

A client is managed when it declares contract v0.9 or later and at least
one file sink names data classes.  Every other path a client reports
(old `disk_writes`, sinks without classes) registers as unmanaged, with
no floor and no policy.

Each registration also carries the client's declared write rate on that
store (`mb_per_day`, summed over its sinks there) and its retention cap
(`max_days`, the smallest `retention_days` above 0), for the days division.
"""

import re
from typing import Mapping, Optional, Sequence

from ..clients.base import ClientView
from .config import POLICY_RE
from .model import CLASSES, Registration, Store

CONTRACT_STORAGE_VERSION = (0, 9)


def _parts(path: str) -> list:
    return [p for p in path.split("/") if p]


def store_for_path(path: str, stores: Sequence[Store]) -> Optional[Store]:
    """The store with the longest mount that contains `path`, by component."""
    want = _parts(path)
    best, best_len = None, -1
    for s in stores:
        m = _parts(s.mount)
        if want[:len(m)] == m and len(m) > best_len:
            best, best_len = s, len(m)
    return best


def _version(text) -> tuple:
    try:
        return tuple(int(x) for x in str(text).split(".")[:2])
    except ValueError:
        return (0, 0)


def _rate(v) -> float:
    """A declared non-negative number, or 0."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0:
        return 0.0
    return float(v)


def registrations_from_views(views: Mapping[str, ClientView],
                             stores: Sequence[Store]) -> list:
    out = []
    for name in sorted(views):
        view = views[name]
        v09 = _version(view.contract_version) >= CONTRACT_STORAGE_VERSION
        merged: dict = {}  # store name -> [paths, floor, policy, managed, mb/day, max_days]
        sinks = []  # (path, floor, policy, managed, mb_per_day, retention_days)
        for iv in view.instances:
            for s in iv.data_sinks:
                if s.kind != "file" or not s.path:
                    continue
                classes = tuple(c for c in s.classes if c in CLASSES)
                managed = v09 and bool(classes)
                policy = s.final_policy
                if policy is not None and not re.match(POLICY_RE, policy):
                    view.issues.append(
                        f"{name}: sink {s.path}: final_policy {policy!r} "
                        f"is not a known policy; ignored")
                    policy = None
                sinks.append((s.path, s.floor_bytes if managed else 0,
                              policy if managed else None, managed,
                              s.mb_per_day, s.retention_days))
            for dw in iv.disk_writes:
                if dw.path:
                    sinks.append((dw.path, 0, None, False, dw.mb_per_day, dw.retention_days))
        for path, floor, policy, managed, mbpd, keep in sinks:
            store = store_for_path(path, stores)
            if store is None:
                view.issues.append(
                    f"{name}: {path} lies on no known store; not registered")
                continue
            e = merged.setdefault(store.name, [[], 0, None, False, 0.0, None])
            if path not in e[0]:
                e[0].append(path)
            e[1] += floor
            e[2] = e[2] or policy
            e[3] = e[3] or managed
            e[4] += _rate(mbpd)
            k = _rate(keep)
            if k > 0:
                e[5] = k if e[5] is None else min(e[5], k)
        for store_name in sorted(merged):
            paths, floor, policy, managed, mbpd, max_days = merged[store_name]
            out.append(Registration(name, store_name, tuple(paths), floor,
                                    policy, managed, mbpd, max_days))
    return out

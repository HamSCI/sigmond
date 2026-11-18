"""Drift report model: Item, Report and the collector runner.

A drift report lists each way a station differs from its sigmond release,
in six kinds.  Each item is a ``drift`` or a ``fault`` with a one-line fix.
Collectors (one per kind) return lists of Item; ``run_collectors`` runs
them and turns a collector that raises into one fault item.
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional, Sequence

KINDS = ("source", "build", "unit", "override", "stray", "host")
SEVERITIES = ("drift", "fault")


@dataclass(frozen=True)
class Item:
    kind: str
    severity: str
    subject: str
    detail: str
    fix: str

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown drift kind: {self.kind!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown drift severity: {self.severity!r}")

    @property
    def key(self) -> str:
        """The exception key an operator accepts: ``kind:subject``."""
        return f"{self.kind}:{self.subject}"


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Report:
    station: str
    release: Optional[str]
    baseline: str  # "blessed" | "candidate" | "none"
    generated_at: str = field(default_factory=now_utc)
    items: list = field(default_factory=list)
    accepted: list = field(default_factory=list)
    reminders: list = field(default_factory=list)

    def counts(self) -> dict:
        out: dict = {}
        for it in self.items:
            by_sev = out.setdefault(it.kind, {})
            by_sev[it.severity] = by_sev.get(it.severity, 0) + 1
        return out

    def faults(self) -> list:
        return [i for i in self.items if i.severity == "fault"]

    def to_json(self) -> dict:
        return {
            "station": self.station,
            "release": self.release,
            "baseline": self.baseline,
            "generated_at": self.generated_at,
            "counts": self.counts(),
            "items": [dict(asdict(i), key=i.key) for i in self.items],
            "accepted": list(self.accepted),
            "reminders": list(self.reminders),
        }


def run_collectors(
        collectors: Sequence[tuple]) -> list:
    """Run (kind, callable) pairs; a raising callable yields one fault."""
    items: list = []
    for kind, fn in collectors:
        try:
            items.extend(fn())
        except Exception as exc:  # noqa: BLE001 - one bad collector must not hide the rest
            items.append(Item(
                kind=kind, severity="fault", subject="check",
                detail=f"could not check {kind}: {exc}",
                fix="see the journal; rerun smd align"))
    return items

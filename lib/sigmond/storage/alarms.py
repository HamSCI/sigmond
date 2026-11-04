"""The alarm book: one journal line per alarm episode, not one per tick.

An episode starts with a `raise`, repeats as a `remind` every REMIND_S while
it lasts, and ends with a `clear`.  A change of severity under the same key
starts a new episode with a fresh `raise`.  The book persists as plain JSON.
"""

from dataclasses import dataclass
from typing import Mapping

from .ladder import Severity

REMIND_S = 6 * 3600


@dataclass(frozen=True)
class Emission:
    key: str
    kind: str  # "raise" | "remind" | "clear"
    severity: Severity
    message: str


def journal_line(e: Emission) -> str:
    prio = 6 if e.kind == "clear" else int(e.severity)
    return f"<{prio}>storage {e.kind}: {e.message}"


class AlarmBook:
    def __init__(self) -> None:
        # key -> {"severity": int, "last_emit": float, "message": str}
        self._active: dict[str, dict] = {}

    def update(self, active: Mapping[str, tuple[Severity, str]],
               now: float) -> list[Emission]:
        out: list[Emission] = []
        for key in sorted(active):
            sev, msg = active[key]
            sev = Severity(sev)
            prev = self._active.get(key)
            if prev is None or prev["severity"] != int(sev):
                out.append(Emission(key, "raise", sev, msg))
                self._active[key] = {"severity": int(sev), "last_emit": now, "message": msg}
            elif now - prev["last_emit"] >= REMIND_S:
                out.append(Emission(key, "remind", sev, msg))
                prev["last_emit"] = now
                prev["message"] = msg
            else:
                prev["message"] = msg
        for key in sorted(set(self._active) - set(active)):
            prev = self._active.pop(key)
            out.append(Emission(key, "clear", Severity(prev["severity"]), prev["message"]))
        return out

    def to_dict(self) -> dict:
        return {"active": {k: dict(v) for k, v in self._active.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "AlarmBook":
        book = cls()
        try:
            items = d.get("active", {}).items()
            for key, v in items:
                try:
                    sev = Severity(int(v["severity"]))
                    book._active[str(key)] = {
                        "severity": int(sev),
                        "last_emit": float(v["last_emit"]),
                        "message": str(v.get("message", "")),
                    }
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
        except (AttributeError, TypeError):
            return cls()
        return book

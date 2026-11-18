"""Drift items for the source and host kinds.

These wrap what ``smd align`` already computes (its plan, its image-file
compare, the host record pm-align pushes in).  Nothing is recomputed here.
"""

from __future__ import annotations

from typing import Optional

from sigmond.drift.model import Item

_APPLY = "smd align --apply"

# status -> (severity, fix)
_MAP = {
    "forward": ("drift", _APPLY),
    "ahead": ("drift", "smd align --apply --allow-rollback (or accept it)"),
    "diverged": ("fault", "resolve the checkout by hand, then smd align --apply"),
    "refuse": ("fault", _APPLY),
    "missing": ("fault", "smd align --apply --install-missing"),
    "stray": ("drift", "remove it or add it to the release"),
    "local": ("drift", "none; managed locally"),
}


def _short(commit: Optional[str]) -> str:
    return commit[:7] if commit else "none"


def baseline_of(aligned_record: Optional[dict]) -> tuple:
    """(release, "blessed"|"candidate"|"none") from aligned.json's record."""
    if not isinstance(aligned_record, dict):
        return None, "none"
    rel = aligned_record.get("release")
    if not rel:
        return None, "none"
    return rel, ("candidate" if aligned_record.get("candidate") else "blessed")


def source_items(plan: list, *, baseline: str) -> list:
    """One Item per align.Item that is not current.

    ``baseline`` is the second value of ``baseline_of``; with "none" the
    report opens with an item saying no release record exists."""
    out: list = []
    if baseline == "none":
        out.append(Item("source", "drift", "baseline",
                        "no blessed or candidate record", _APPLY))
    for it in plan:
        if it.status == "current":
            continue
        entry = _MAP.get(it.status)
        if entry is None:
            continue
        severity, fix = entry
        detail = f"{_short(it.live)} -> {_short(it.target)}"
        if it.note:
            detail += f"; {it.note}"
        out.append(Item("source", severity, it.component, detail, fix))
    return out


def host_items(host_record: Optional[dict], file_drift: list, *,
               vm_release: Optional[str] = None,
               vm_candidate: bool = False) -> list:
    """Items for the Proxmox host as the VM sees it.

    ``file_drift`` is align.image_file_drift's list of dicts.  An "unknown"
    file (the network lookup failed) yields nothing."""
    if host_record is None:
        return []
    out: list = []
    for f in file_drift:
        status = f.get("status")
        if status in ("differs", "absent"):
            out.append(Item("host", "drift",
                            f"host file {f['path']} {status}",
                            f.get("note") or status, "pm-align --apply"))
    rel = host_record.get("release") or "unknown"
    host_cand = bool(host_record.get("candidate"))
    if vm_release and (rel != vm_release or host_cand != vm_candidate):
        def kind(c):
            return "candidate" if c else "blessed"
        out.append(Item("host", "drift", "host release",
                        f"host on {kind(host_cand)} {rel}, "
                        f"VM on {kind(vm_candidate)} {vm_release}",
                        "pm-align --apply"))
    return out

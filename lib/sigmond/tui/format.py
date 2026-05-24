"""Pure-Python formatting helpers + readers for TUI screens.

This module has NO Textual imports — every function here is a plain
string formatter, value mapper, or filesystem reader.  That keeps the
helpers unit-testable in environments where Textual is not installed
(e.g. CI without GUI deps), which most of the screen modules cannot
support because Textual is imported at module top level.

Add helpers here when they:

- Are pure functions of their inputs (or near-pure: file readers
  with deterministic error modes are fine).
- Are referenced by ``screens/*.py`` for rendering.
- Don't need any Textual widget or container types.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# --- §18 / authority.json reader and formatters --------------------------


# Where hf-timestd publishes its per-cycle snapshot per
# ARCHITECTURE-FIRST-PRINCIPLES.md §3 / METROLOGY.md §4.5.  The path is
# fixed by hf-timestd; sigmond reads it as a downstream consumer.
AUTHORITY_JSON_PATH = Path("/run/hf-timestd/authority.json")


@dataclass
class AuthoritySnapshot:
    """In-memory mirror of /run/hf-timestd/authority.json.

    Only the fields the TUI consumes are surfaced as named attributes;
    the raw dict is preserved under ``raw`` so future fields can be
    accessed without a schema bump in this module.

    None on a field means hf-timestd didn't populate it this cycle
    (e.g. ``governor_radiod`` is absent when no provider is wired,
    ``last_transition_utc`` is None until the first transition).
    """
    schema:            str                                  # e.g. "v1"
    utc_published:     Optional[datetime]                   # parsed from ISO8601
    a_level:           str                                  # "A0" / "A1"
    t_level_active:    Optional[str]                        # "T6" / "T5" / ... / None
    t_level_available: List[str] = field(default_factory=list)
    t_level_witnesses: List[str] = field(default_factory=list)
    rtp_to_utc_offset_ns: Optional[int] = None
    sigma_ns:          Optional[int] = None
    stations_contributing: List[str] = field(default_factory=list)
    last_transition_utc: Optional[datetime] = None          # parsed from ISO8601
    disagreement_flags: List[str] = field(default_factory=list)
    governor_radiod:   Optional[str] = None
    bootstrap:         Optional[dict] = None                # opaque to the TUI
    raw:               Optional[dict] = None                # full original dict


# Errors surfaced by read_authority_snapshot — kept as sentinel strings
# so callers can branch on them cleanly without importing exception
# classes.  Each value also doubles as an operator-friendly message
# when no further context is needed.
ERR_NOT_FOUND  = "not_found"
ERR_UNREADABLE = "unreadable"
ERR_MALFORMED  = "malformed"


def _parse_iso8601_utc(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO8601-with-Z timestamp (as hf-timestd emits via
    ``_iso_z``) into a timezone-aware UTC datetime.  Returns None on
    any parse failure — callers treat this as 'field absent'.

    Accepts both the trailing-Z form (``...Z``) that
    ``authority_manager._iso_z`` produces and the equivalent
    ``+00:00`` form for forward-compatibility with any future
    producer.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        # Defensive: caller already parsed.
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        # datetime.fromisoformat understands +00:00 natively in 3.11+
        # but not the trailing Z; normalise.
        s = value.rstrip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def read_authority_snapshot(
    path: Path = AUTHORITY_JSON_PATH,
) -> tuple[Optional[AuthoritySnapshot], Optional[str]]:
    """Read and parse hf-timestd's authority.json.

    Returns ``(snapshot, error)`` — exactly one is non-None.  The
    error is a sentinel string (``ERR_NOT_FOUND``, ``ERR_UNREADABLE``,
    ``ERR_MALFORMED``) the caller can branch on.

    Failure modes are explicit because the snapshot's absence /
    staleness is itself the operationally interesting signal: an
    operator looking at this screen wants to know "is hf-timestd
    publishing?" first, then the contents second.
    """
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None, ERR_NOT_FOUND
    except (OSError, PermissionError):
        return None, ERR_UNREADABLE

    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return None, ERR_MALFORMED

    if not isinstance(raw, dict):
        return None, ERR_MALFORMED

    snap = AuthoritySnapshot(
        schema=str(raw.get("schema", "")),
        utc_published=_parse_iso8601_utc(raw.get("utc_published")),
        a_level=str(raw.get("a_level", "")),
        t_level_active=raw.get("t_level_active"),
        t_level_available=list(raw.get("t_level_available") or []),
        t_level_witnesses=list(raw.get("t_level_witnesses") or []),
        rtp_to_utc_offset_ns=(
            int(raw["rtp_to_utc_offset_ns"])
            if raw.get("rtp_to_utc_offset_ns") is not None else None
        ),
        sigma_ns=(
            int(raw["sigma_ns"])
            if raw.get("sigma_ns") is not None else None
        ),
        stations_contributing=list(raw.get("stations_contributing") or []),
        last_transition_utc=_parse_iso8601_utc(raw.get("last_transition_utc")),
        disagreement_flags=list(raw.get("disagreement_flags") or []),
        governor_radiod=raw.get("governor_radiod") or None,
        bootstrap=raw.get("bootstrap"),
        raw=raw,
    )
    return snap, None


def snapshot_age_seconds(
    snap: AuthoritySnapshot,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Compute seconds elapsed since the snapshot's ``utc_published``.

    Returns None when ``utc_published`` is absent (treat as unknown /
    don't show an age line).  ``now`` defaults to wall-clock UTC; tests
    inject a fixed time.
    """
    if snap.utc_published is None:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    return (now - snap.utc_published).total_seconds()


def format_offset_ns(ns: Optional[int]) -> str:
    """Render a signed nanosecond offset auto-scaled to ns / µs / ms / s.

    Mirrors ``timing.py``'s ``format_offset`` but takes an integer-ns
    input matching authority.json's ``rtp_to_utc_offset_ns`` /
    ``sigma_ns`` field types.
    """
    if ns is None:
        return "?"
    sign = "+" if ns >= 0 else "-"
    n = abs(ns)
    if n < 1_000:
        return f"{sign}{n} ns"
    if n < 1_000_000:
        return f"{sign}{n / 1_000:.2f} µs"
    if n < 1_000_000_000:
        return f"{sign}{n / 1_000_000:.2f} ms"
    return f"{sign}{n / 1_000_000_000:.3f} s"


def format_sigma_ns(ns: Optional[int]) -> str:
    """Like format_offset_ns but unsigned (σ is always positive).
    Returns '?' when sigma is unknown."""
    if ns is None:
        return "?"
    n = abs(ns)
    if n < 1_000:
        return f"{n} ns"
    if n < 1_000_000:
        return f"{n / 1_000:.2f} µs"
    if n < 1_000_000_000:
        return f"{n / 1_000_000:.2f} ms"
    return f"{n / 1_000_000_000:.3f} s"


def format_age_seconds(seconds: Optional[float]) -> str:
    """Format a duration in seconds as ``Ns`` / ``Nm Ss`` / ``Nh Mm``,
    picking the most natural granularity.  Returns '?' when None.
    Negative values are treated as 0 (clock skew can produce them)."""
    if seconds is None:
        return "?"
    if seconds < 0:
        seconds = 0.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds - m * 60)
        return f"{m}m {s}s"
    h = int(seconds // 3600)
    m = int((seconds - h * 3600) // 60)
    return f"{h}h {m}m"


# Snapshot age above which we colour the header red and add a warning
# line.  authority_manager ticks at ~30 s by default; 60 s of silence
# means the manager has missed two cycles.
AUTHORITY_STALE_THRESHOLD_S = 60.0


def _tier_colour(tier: Optional[str]) -> str:
    """Tier-quality colour mirroring format_timing_line: T5/T6 ns-class
    hard-wired paths are green; T4 USB/LAN is yellow; T0-T3 are red
    (usable for sample-labelling, not for hard-deadline gating)."""
    if tier in ("T5", "T6"):
        return "green"
    if tier == "T4":
        return "yellow"
    return "red"


def render_authority_body(
    snap: Optional[AuthoritySnapshot],
    error: Optional[str],
    age_s: Optional[float],
) -> str:
    """Render the Authority screen's main body as a markup string.

    Pure function — kept in format.py rather than alongside the
    Textual screen so it can be unit-tested without Textual installed.

    Branches:

    - error set → operator-facing diagnostic explaining the absence /
      unreadability of the file.
    - snap.t_level_active is None → authority manager is publishing
      but no tier is currently selected (bootstrap pending, or
      complete loss of all probes).
    - normal path → header (active tier, A-level, offset, σ), age
      with staleness colour, transition history, witnesses /
      disagreements, governor radiod, contributing stations.
    """
    if error == ERR_NOT_FOUND:
        return (
            f"[red]No authority snapshot found at {AUTHORITY_JSON_PATH}[/]\n\n"
            f"The authority manager publishes this file every ~30 s.\n"
            f"Likely causes:\n"
            f"  • hf-timestd is not running\n"
            f"    [dim]sudo systemctl status timestd-authority-manager[/]\n"
            f"  • hf-timestd is starting up (first tick has not completed)\n"
            f"  • Permissions issue — the file should be world-readable\n"
            f"    (per AuthorityManager._write_state).\n\n"
            f"[dim]Operators relying on §18 timing authority cannot gate on\n"
            f"tier/σ/age until this file is present and recent.[/]"
        )
    if error == ERR_UNREADABLE:
        return (
            f"[red]{AUTHORITY_JSON_PATH} exists but is not readable[/]\n\n"
            f"Check permissions: AuthorityManager writes the file as\n"
            f"world-readable (mode 0644) precisely so consumers can\n"
            f"read it without elevation."
        )
    if error == ERR_MALFORMED:
        return (
            f"[red]{AUTHORITY_JSON_PATH} is unparseable[/]\n\n"
            f"The file exists but is not valid JSON.  Likely an\n"
            f"in-progress atomic write that lost a race, or a partial\n"
            f"write from a crashed authority manager.  Wait one\n"
            f"poll cycle; if it persists, restart\n"
            f"timestd-authority-manager."
        )
    if snap is None:
        return "[red]internal error: no snapshot and no error[/]"

    lines: list = []

    # Header line — active tier + A-level, offset, σ.
    tier = snap.t_level_active or "—"
    tcol = _tier_colour(snap.t_level_active)
    offset_str = format_offset_ns(snap.rtp_to_utc_offset_ns)
    sigma_str  = format_sigma_ns(snap.sigma_ns)
    a_level    = snap.a_level or "?"
    lines.append(
        f"Active: [bold {tcol}]{tier}[/] + [cyan]{a_level}[/]"
        f"   rtp_to_utc = [bold]{offset_str}[/] ± {sigma_str}"
    )

    # Age + publication timestamp.
    age_str = format_age_seconds(age_s)
    if age_s is not None and age_s > AUTHORITY_STALE_THRESHOLD_S:
        age_colour = "red"
        stale_note = (
            f"   [red]⚠ stale[/]"
            f" — authority manager may have stalled "
            f"(threshold {AUTHORITY_STALE_THRESHOLD_S:.0f} s)"
        )
    else:
        age_colour = "green" if age_s is not None and age_s < 30 else "yellow"
        stale_note = ""
    pub = snap.utc_published.isoformat() if snap.utc_published else "?"
    lines.append(
        f"Published: {pub}"
        f"   ([{age_colour}]{age_str} ago[/]){stale_note}"
    )

    # Governor radiod.
    if snap.governor_radiod:
        lines.append(
            f"Governor radiod: [cyan]{snap.governor_radiod}[/]"
            f"   [dim](rtp_to_utc_offset_ns is relative to this radiod's RTP timebase)[/]"
        )
    else:
        lines.append(
            "Governor radiod: [dim](not declared — single-radiod station,"
            " or governor_radiod_provider not wired)[/]"
        )

    # Available + witnesses.
    avail = ", ".join(snap.t_level_available) or "[dim](none)[/]"
    lines.append(f"Available tiers: {avail}")
    if snap.t_level_witnesses:
        wits = ", ".join(snap.t_level_witnesses)
        lines.append(f"Witnesses: [yellow]{wits}[/]")
    else:
        lines.append("Witnesses: [dim](none active)[/]")

    # Disagreement flags.
    if snap.disagreement_flags:
        flags = ", ".join(snap.disagreement_flags)
        lines.append(f"Disagreements: [red]⚠ {flags}[/]")
    else:
        lines.append("Disagreements: [green]none[/]")

    # Last transition.
    if snap.last_transition_utc:
        lines.append(
            f"Last transition: {snap.last_transition_utc.isoformat()}"
        )

    # Stations contributing.
    if snap.stations_contributing:
        stations = ", ".join(snap.stations_contributing)
        lines.append(f"Stations contributing: [dim]{stations}[/]")

    # Bootstrap (only while actively gating).
    if snap.bootstrap and not snap.bootstrap.get("complete", True):
        reason = snap.bootstrap.get("reason", "unknown")
        delta = snap.bootstrap.get("delta_sec")
        delta_str = f" Δ={delta:+.3f} s" if isinstance(delta, (int, float)) else ""
        lines.append(
            f"[yellow]Bootstrap pending:[/] {reason}{delta_str}"
            f"   [dim](probes resume when bootstrap completes)[/]"
        )

    return "\n".join(lines)


def format_timing_line(inst) -> Optional[str]:
    """Render a CLIENT-CONTRACT v0.7 §18 timing-state line for one
    ``InstanceView``, or ``None`` if the instance is in the boring
    default case (no §18 role, nothing worth surfacing).

    The Overview screen calls this once per instance to produce a
    sub-line under each client entry.  Returning ``None`` lets the
    common-case rendering stay compact: only instances with an
    interesting §18 role contribute a "timing: …" line.

    Cases (mutually exclusive, in priority order):

    1. ``provides_timing_calibration=True`` — the instance is itself
       a §18 timing-authority producer.  Visually distinctive (green)
       because there's typically one per station and operators want
       to confirm it's there.
    2. ``timing_authority_applied`` is a populated dict — the instance
       is actively subscribing.  Show ``tier / σ / age (source)`` so
       an operator can read the budget at a glance.  Colour by tier
       quality: green for T5+, yellow for T4, red for ≤T3.
    3. ``uses_timing_calibration=True`` but ``timing_authority_applied``
       is None — the client is capable of subscribing but is currently
       in default mode (either no authority is reachable or it's been
       gated off).  Yellow, slightly verbose so the operator knows
       why nothing is happening.
    4. All other cases — return ``None`` (no line emitted).
    """
    if getattr(inst, 'provides_timing_calibration', False):
        return "[green]provides authority[/]"

    applied = getattr(inst, 'timing_authority_applied', None)
    if isinstance(applied, dict):
        tier   = applied.get('tier') or '?'
        source = applied.get('source') or '?'
        sigma  = applied.get('sigma_ns')
        age    = applied.get('snapshot_age_s')

        # σ in ns; auto-scale to the most natural unit, matching the
        # convention in timing.py's format_offset.
        if isinstance(sigma, (int, float)):
            if sigma < 1_000:
                sigma_str = f"σ={sigma:g} ns"
            elif sigma < 1_000_000:
                sigma_str = f"σ={sigma / 1_000:.2g} µs"
            else:
                sigma_str = f"σ={sigma / 1_000_000:.2g} ms"
        else:
            sigma_str = "σ=?"

        if isinstance(age, (int, float)):
            age_str = f"age={age:.1f}s" if age < 60 else f"age={age / 60:.1f}m"
        else:
            age_str = "age=?"

        # Tier-quality colour per ARCHITECTURE-FIRST-PRINCIPLES.md §2
        # (post-2026-05-24 rerank): T5 / T6 are ns-class hard-wired
        # paths (green); T4 is µs-to-ms LAN/USB (yellow); T0–T3 are
        # ms-class or worse (red), inadequate for hard-deadline
        # gating but still useful for sample-labelling clients.
        if tier in ('T5', 'T6'):
            colour = 'green'
        elif tier == 'T4':
            colour = 'yellow'
        else:
            colour = 'red'
        return f"[{colour}]{tier}[/] {sigma_str} {age_str}  source={source}"

    if getattr(inst, 'uses_timing_calibration', False):
        return ("[yellow]subscriber-capable, currently default mode[/] "
                "(no §18 authority applied)")

    return None

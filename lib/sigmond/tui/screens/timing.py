"""Timing screen — live chrony-source comparison with HPPS as reference.

The default ``chronyc sources`` view shows every offset relative to the
*system clock*, which forces mental subtraction to compare two sources.
On bee1 the natural reference is HPPS (the T6 path: TS-1 HF-injected
BPSK-PPS via the RX-888 ADC, sample-precise from the IQ stream;
ns-class once the §8 chain delay is calibrated — historically ±1 ns
σ=1 ns).  This screen pivots so HPPS = 0 and every other source
shows Δ-from-HPPS, with a 60-sample Unicode sparkline so transient
events (e.g. Costas-loop excursions, chrony slews) are visible at a
glance instead of having to grep journals.

Header shows ``chronyc tracking`` output framed for the question that
actually matters when reasoning about timestamp confidence: where does
chrony think the kernel clock currently sits relative to UTC, and what
is the conservative bound (root dispersion) on that estimate?

NOTE: this screen reflects chrony's *facade* view of timing
(per ARCHITECTURE-FIRST-PRINCIPLES.md §5: chrony is a downstream
consumer, not the architectural design center).  An operator who
needs to gate a hard-deadline capture on §18 authority budget
should consult the per-client `timing_authority_applied` field on
the Overview screen — that reads from authority.json directly and
reports tier + σ + snapshot-age without the chrony-shape
intermediation.

Refresh: ``set_interval(1.0)`` — light enough that the running cost is
negligible, fast enough to track the ~13-s Costas excursions visible in
the underlying calibrator.
"""

from __future__ import annotations

import subprocess
from collections import deque
from typing import Dict, List, Optional

from textual.containers import Vertical
from textual.widgets import DataTable, Static

from ..format import (
    HISTORY,
    SPARKS,
    SourceRow,
    TrackingRow,
    format_age,
    format_offset,
    format_reach,
    parse_sources,
    parse_tracking,
    sparkline,
)

# Refresh cadence.  Lightweight enough that the running cost is
# negligible, fast enough to track the ~13-s Costas excursions visible
# in the underlying calibrator.  History depth (HISTORY) lives in
# ..format alongside sparkline(), whose default width it sets.
POLL_SEC = 1.0


def _run_chronyc(args: List[str]) -> Optional[str]:
    """Run chronyc in CSV mode.  Returns stdout on success, None on
    any failure (chrony down, command not found, timeout)."""
    try:
        proc = subprocess.run(
            ['chronyc', '-c'] + args,
            capture_output=True, text=True, timeout=2.0,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _row_color(name: str, delta_sec: float) -> str:
    """Colour-code a row's name by how close it is to HPPS.  HPPS
    itself is bold (it's the reference); everything else gets graded
    green/yellow/red against thresholds chosen for stratum-1 NTP
    quality vs. publicly-routed ms-jitter sources."""
    if name == 'HPPS':
        return f"[bold]{name}[/]"
    abs_s = abs(delta_sec)
    if abs_s < 10e-6:           # <10 µs — chrony-quality
        return f"[green]{name}[/]"
    if abs_s < 1e-3:            # <1 ms — usable LAN
        return f"[yellow]{name}[/]"
    return f"[red]{name}[/]"     # ≥1 ms — public-internet-grade


class TimingScreen(Vertical):
    """Live chrony source comparison with HPPS as the reference."""

    DEFAULT_CSS = """
    TimingScreen { padding: 1; }
    TimingScreen .section-title {
        text-style: bold;
        margin-top: 1;
        margin-bottom: 0;
    }
    TimingScreen #timing-utc {
        margin-top: 1;
        margin-bottom: 1;
    }
    TimingScreen #timing-status {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Per-source ring of (Δ-from-HPPS in seconds) values.
        self._history: Dict[str, deque] = {}

    def compose(self):
        yield Static("Timing — chrony sources (HPPS reference)",
                     classes="section-title")
        yield Static("", id="timing-utc")

        table = DataTable(id="timing-table", zebra_stripes=True,
                          cursor_type="row")
        table.add_columns(
            "Source", "Δ from HPPS", "Reach", "Age",
            "σ (sample)", f"trace ({HISTORY}s)",
        )
        yield table

        yield Static("", id="timing-status")

    def on_mount(self) -> None:
        self._refresh()
        # Live update every POLL_SEC.  set_interval runs on the
        # Textual event loop so chronyc subprocess calls block the UI
        # very briefly (~10-30 ms).  If that ever becomes visible,
        # move the chronyc invocation into a worker thread the way
        # cpu_freq.py does.
        self.set_interval(POLL_SEC, self._refresh)

    def _refresh(self) -> None:
        sources_csv = _run_chronyc(['sources'])
        tracking_csv = _run_chronyc(['tracking'])
        status = self.query_one("#timing-status", Static)
        utc = self.query_one("#timing-utc", Static)
        table = self.query_one("#timing-table", DataTable)

        if sources_csv is None:
            status.update(
                "[red]chronyc unavailable — chrony not running, "
                "or chronyc not in PATH[/]"
            )
            return

        sources = parse_sources(sources_csv)
        tracking = parse_tracking(tracking_csv or "")

        hpps = next((s for s in sources if s.name == 'HPPS'), None)
        if not hpps:
            status.update(
                "[yellow]No HPPS source in chronyc output — "
                "is the TS-1 BPSK refclock (T6 path) configured "
                "and its SHM segment fed to chrony?[/]"
            )
            return

        # Update history per source.
        for s in sources:
            delta = s.last_offset_sec - hpps.last_offset_sec
            hist = self._history.setdefault(
                s.name, deque(maxlen=HISTORY)
            )
            hist.append(delta)

        # Header summary: kernel-vs-UTC.
        if tracking:
            ref = tracking.ref_id_name
            utc.update(
                f"Kernel clock vs UTC: "
                f"[bold]{format_offset(tracking.last_offset_sec)}[/] "
                f"(RMS {format_offset(tracking.rms_offset_sec)}, "
                f"root dispersion ±{format_offset(tracking.root_dispersion_sec)}) "
                f"— ref [cyan]{ref}[/]  leap: {tracking.leap_status}"
            )
        else:
            utc.update("[yellow]chronyc tracking unavailable[/]")

        # Body table.
        table.clear()
        for s in sources:
            delta = s.last_offset_sec - hpps.last_offset_sec
            delta_str = "[bold]ref[/]" if s.name == 'HPPS' else format_offset(delta)
            spark = sparkline(list(self._history.get(s.name, [])))
            table.add_row(
                _row_color(s.name, delta),
                delta_str,
                format_reach(s.reach),
                format_age(s.last_rx_sec),
                format_offset(s.sample_error_sec),
                spark,
            )

        status.update(
            f"[dim]{len(sources)} source"
            f"{'s' if len(sources) != 1 else ''} — "
            f"refresh {POLL_SEC:.0f}s, history {HISTORY}s[/]"
        )

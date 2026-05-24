"""Annotation Quality screen — per-consumer science verdict.

Companion to the Authority screen.  Where Authority shows the
substrate state in metrology terms (active tier, σ, witnesses,
disagreement flags), this screen answers the operator-facing
question "is each science consumer being given a usable RTP→UTC
label right now?" by attaching the global authority tier+σ to
each running consumer instance and applying a verdict threshold.

The reading order is therefore:

  - Annotation Quality (this screen) — "is my science data being
    annotated honestly right now?"  Operator-friendly, per-stream.
  - Authority — "what does hf-timestd say the timing budget is?"
    Substrate-friendly, single global state.
  - Timing — "what does chrony do with that information?"
    Facade view, downstream consumer.

Data sources, polled every second:

  - ``/run/hf-timestd/authority.json`` — global authority tier + σ.
  - ``/var/lib/timestd/status/core-recorder-status.json`` — the
    drift-monitor block that explains *why* the verdict is what it
    is (lms_ns, breach state, recapture history).
  - ``systemctl list-units`` for known science-recorder patterns —
    the set of consumers attaching the σ.

When hf-timestd is not publishing (authority.json absent), the
screen explains that rather than silently rendering nothing — the
absence of authority IS the operationally interesting signal.
"""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Static

from ..format import (
    enumerate_timing_consumer_units,
    read_authority_snapshot,
    read_core_recorder_status,
    render_annotation_quality_body,
    snapshot_age_seconds,
)


POLL_SEC = 1.0


class AnnotationQualityScreen(Vertical):
    """Per-consumer science-verdict view of the host's RTP→UTC authority."""

    DEFAULT_CSS = """
    AnnotationQualityScreen { padding: 1; }
    AnnotationQualityScreen .section-title {
        text-style: bold;
        margin-top: 1;
        margin-bottom: 0;
    }
    AnnotationQualityScreen #annotation-body {
        margin-top: 1;
        margin-bottom: 1;
    }
    AnnotationQualityScreen #annotation-footer {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def compose(self):
        yield Static(
            "Annotation Quality — per-consumer science verdict",
            classes="section-title",
        )
        yield Static("", id="annotation-body")
        yield Static("", id="annotation-footer")

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(POLL_SEC, self._refresh)

    def _refresh(self) -> None:
        auth_snap, auth_err = read_authority_snapshot()
        auth_age = (
            snapshot_age_seconds(auth_snap) if auth_snap is not None else None
        )
        recorder_status, recorder_err = read_core_recorder_status()
        consumer_units = enumerate_timing_consumer_units()

        body = self.query_one("#annotation-body", Static)
        footer = self.query_one("#annotation-footer", Static)
        body.update(render_annotation_quality_body(
            auth_snap, auth_err, auth_age,
            recorder_status, recorder_err,
            consumer_units,
        ))
        footer.update(
            f"[dim]refresh {POLL_SEC:.0f}s — "
            f"authority ticks every ~30 s — "
            f"substrate view: Monitoring / Authority — "
            f"chrony view: Monitoring / Timing[/]"
        )

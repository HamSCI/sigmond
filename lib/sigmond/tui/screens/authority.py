"""Authority screen — substrate view of hf-timestd's authority.json.

Counterpart to the Timing screen.  Where Timing shows the chrony
*facade* view (one downstream consumer's selection algorithm),
Authority shows the *substrate* view: the per-cycle (A, T) annotation
hf-timestd publishes for any consumer that needs to gate on
CLIENT-CONTRACT v0.7 §18 timing budget.

Per ARCHITECTURE-FIRST-PRINCIPLES.md §5:

    Chrony is a **downstream consumer** of the offset stream — useful
    for keeping the host clock disciplined, not the architectural
    design center.

The natural reading order is therefore: this screen first (what
hf-timestd thinks the timing budget is), Timing screen second (what
chrony does with that information).

Data source: ``/run/hf-timestd/authority.json``, rewritten atomically
each ~30 s by ``authority_manager.AuthorityManager._write_state``.
We poll every second so the UI stays current; the read cost is
trivial (one small JSON file).

When the file is absent (hf-timestd not running, or its first tick
hasn't completed), the screen surfaces that explicitly rather than
silently rendering empty.  Stale snapshots (older than the
``STALE_THRESHOLD_S`` heuristic) get a prominent warning — a stuck
authority manager is one of the most operationally important things
this screen needs to catch.
"""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Static

from ..format import (
    read_authority_snapshot,
    render_authority_body,
    snapshot_age_seconds,
)


POLL_SEC = 1.0


class AuthorityScreen(Vertical):
    """Live view of hf-timestd's published §18 authority state."""

    DEFAULT_CSS = """
    AuthorityScreen { padding: 1; }
    AuthorityScreen .section-title {
        text-style: bold;
        margin-top: 1;
        margin-bottom: 0;
    }
    AuthorityScreen #authority-body {
        margin-top: 1;
        margin-bottom: 1;
    }
    AuthorityScreen #authority-footer {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def compose(self):
        yield Static(
            "Authority — substrate view "
            "(hf-timestd /run/hf-timestd/authority.json)",
            classes="section-title",
        )
        yield Static("", id="authority-body")
        yield Static("", id="authority-footer")

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(POLL_SEC, self._refresh)

    def _refresh(self) -> None:
        snap, error = read_authority_snapshot()
        age = snapshot_age_seconds(snap) if snap is not None else None
        body = self.query_one("#authority-body", Static)
        footer = self.query_one("#authority-footer", Static)
        body.update(render_authority_body(snap, error, age))
        footer.update(
            f"[dim]refresh {POLL_SEC:.0f}s — "
            f"hf-timestd ticks every ~30 s — "
            f"chrony view: Observe / Timing screen[/]"
        )

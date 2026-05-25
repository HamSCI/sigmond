"""Combined Timing & Authority screen.

Composes the existing AuthorityScreen (substrate view of authority.json:
active tier, σ, witnesses) on top and TimingScreen (chrony-facade view:
sources vs HPPS, root dispersion) below, with a thin separator.

The two were always meant to be read together — authority.py's
docstring explicitly says "The natural reading order is therefore:
this screen first ..., Timing screen second."  Operators monitoring
"is timing healthy?" want both at once: what hf-timestd thinks the
budget is, and what chrony is doing about it.

Each child screen keeps its own data fetching and refresh interval;
this wrapper just mounts them and inserts a heading + rule.
"""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Rule, Static

from .authority import AuthorityScreen
from .timing import TimingScreen


class TimingAuthorityScreen(Vertical):
    """Authority (substrate) + Timing (chrony facade) on one screen."""

    DEFAULT_CSS = """
    TimingAuthorityScreen {
        padding: 0;
    }
    TimingAuthorityScreen .ta-divider {
        margin-top: 1;
        margin-bottom: 1;
        text-style: bold;
        color: $text-muted;
    }
    """

    def compose(self):
        yield AuthorityScreen()
        yield Static("─── Timing (chrony facade) ───",
                     classes="ta-divider")
        yield Rule()
        yield TimingScreen()

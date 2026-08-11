"""Tests for sigmond.tui.format — pure-Python formatting helpers — plus
the Overview screen's timing section (Task 4 of the TUI reconciliation
plan: the landing screen carries the shallow T-level verdict so an
operator never has to dig into Authority / Annotation Quality just to
learn "is timing OK right now?").

The ``FormatTimingLine*`` and ``RenderOverviewTimingBody*`` classes
below exercise pure functions in ``sigmond.tui.format`` and have no
Textual dependency, so they run in any environment with sigmond
installed (unlike test_tui_timing which imports a screen module that
pulls in Textual at top level).  The ``OverviewTiming*`` classes at
the bottom DO need Textual (they gather/mount the real screen) and are
skipped when it isn't installed, matching the pattern in
test_tui_navigation.py / test_tui_logs.py."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond.tui.format import (
    AuthoritySnapshot,
    ERR_NOT_FOUND,
    format_timing_line,
    render_overview_timing_body,
)

try:
    import textual  # noqa: F401
    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False


def _inst(**fields):
    """Build a SimpleNamespace mocking the InstanceView fields the
    formatter reads.  Defaults to the boring case (all flags false,
    timing_authority_applied None)."""
    defaults = dict(
        provides_timing_calibration=False,
        uses_timing_calibration=False,
        timing_authority_applied=None,
    )
    defaults.update(fields)
    return SimpleNamespace(**defaults)


class FormatTimingLineProducerTests(unittest.TestCase):
    """Case 1 — instance is itself the §18 producer."""

    def test_producer_returns_distinctive_green_marker(self):
        line = format_timing_line(_inst(provides_timing_calibration=True))
        self.assertIsNotNone(line)
        self.assertIn("provides authority", line)
        self.assertIn("[green]", line)

    def test_producer_takes_precedence_over_applied(self):
        """If a client both provides AND has applied a peer authority
        (hypothetical future stratum-cascading hf-timestd), the
        producer label wins — that's the more interesting station-
        wide role to surface."""
        line = format_timing_line(_inst(
            provides_timing_calibration=True,
            timing_authority_applied={'tier': 'T5', 'source': 'peer'},
        ))
        self.assertIn("provides authority", line)


class FormatTimingLineAppliedTests(unittest.TestCase):
    """Case 2 — instance is actively subscribing to a §18 authority."""

    def test_basic_subscriber_t5_green(self):
        line = format_timing_line(_inst(
            timing_authority_applied={
                'source': 'hf-timestd@bee3',
                'tier': 'T5',
                'sigma_ns': 1200,
                'snapshot_age_s': 4.2,
            },
        ))
        self.assertIsNotNone(line)
        self.assertIn("T5", line)
        self.assertIn("[green]", line)
        self.assertIn("source=hf-timestd@bee3", line)
        self.assertIn("age=4.2s", line)

    def test_t6_also_green(self):
        """T5 and T6 are both ns-class hard-wired paths per the
        revised ARCHITECTURE-FIRST-PRINCIPLES.md §2 — both render
        green."""
        line = format_timing_line(_inst(
            timing_authority_applied={
                'source': 'hf-timestd@bee3',
                'tier': 'T6',
                'sigma_ns': 1,
                'snapshot_age_s': 0.5,
            },
        ))
        self.assertIn("[green]", line)
        self.assertIn("T6", line)

    def test_t4_yellow(self):
        """T4 is LAN-stratum-1 µs-to-ms class per the revised table —
        usable but not hard-deadline-grade."""
        line = format_timing_line(_inst(
            timing_authority_applied={
                'source': 'lan-ntp@bee2',
                'tier': 'T4',
                'sigma_ns': 500_000,   # 500 µs
                'snapshot_age_s': 30.0,
            },
        ))
        self.assertIn("[yellow]", line)
        self.assertIn("T4", line)

    def test_t3_or_lower_red(self):
        for tier in ('T3', 'T2', 'T1', 'T0'):
            line = format_timing_line(_inst(
                timing_authority_applied={
                    'source': 'fallback', 'tier': tier,
                    'sigma_ns': 5_000_000, 'snapshot_age_s': 60.0,
                },
            ))
            self.assertIn("[red]", line, f"tier {tier} should render red")
            self.assertIn(tier, line)

    def test_sigma_auto_scales_ns(self):
        line = format_timing_line(_inst(
            timing_authority_applied={
                'source': 'src', 'tier': 'T6',
                'sigma_ns': 500, 'snapshot_age_s': 1.0,
            },
        ))
        self.assertIn("σ=500 ns", line)

    def test_sigma_auto_scales_us(self):
        line = format_timing_line(_inst(
            timing_authority_applied={
                'source': 'src', 'tier': 'T5',
                'sigma_ns': 1200, 'snapshot_age_s': 1.0,
            },
        ))
        self.assertIn("σ=1.2 µs", line)

    def test_sigma_auto_scales_ms(self):
        line = format_timing_line(_inst(
            timing_authority_applied={
                'source': 'src', 'tier': 'T3',
                'sigma_ns': 3_500_000, 'snapshot_age_s': 1.0,
            },
        ))
        self.assertIn("σ=3.5 ms", line)

    def test_age_auto_scales_minutes(self):
        """Snapshot ages above 60 s render as minutes — important for
        spotting "this snapshot is dangerously stale" cases without
        squinting at a four-digit second count."""
        line = format_timing_line(_inst(
            timing_authority_applied={
                'source': 'src', 'tier': 'T5',
                'sigma_ns': 1000, 'snapshot_age_s': 180.0,
            },
        ))
        self.assertIn("age=3.0m", line)

    def test_missing_optional_fields_render_question_marks(self):
        """Defensive: a producer that hasn't fully populated the
        snapshot shouldn't crash the renderer — show '?' so the gap
        is operator-visible."""
        line = format_timing_line(_inst(
            timing_authority_applied={
                'source': 'partial', 'tier': 'T5',
                # sigma_ns + snapshot_age_s absent
            },
        ))
        self.assertIn("σ=?", line)
        self.assertIn("age=?", line)

    def test_unknown_tier_renders_red(self):
        """A tier string we don't recognise (future T7, malformed) is
        treated as low-quality (red) — the safe default; never crash
        on unfamiliar tier names."""
        line = format_timing_line(_inst(
            timing_authority_applied={
                'source': 'src', 'tier': 'T7',
                'sigma_ns': 1, 'snapshot_age_s': 1.0,
            },
        ))
        self.assertIn("[red]", line)
        self.assertIn("T7", line)


class FormatTimingLineCapableTests(unittest.TestCase):
    """Case 3 — instance is subscriber-capable but currently in default
    mode (authority unreachable, gated off, or never connected)."""

    def test_capable_but_default_mode(self):
        line = format_timing_line(_inst(
            uses_timing_calibration=True,
            timing_authority_applied=None,
        ))
        self.assertIsNotNone(line)
        self.assertIn("[yellow]", line)
        self.assertIn("subscriber-capable", line)
        self.assertIn("default mode", line)

    def test_applied_takes_precedence_over_capable(self):
        """If the client is capable AND currently applying, show the
        applied snapshot — capability becomes implicit."""
        line = format_timing_line(_inst(
            uses_timing_calibration=True,
            timing_authority_applied={
                'source': 'src', 'tier': 'T5',
                'sigma_ns': 1000, 'snapshot_age_s': 1.0,
            },
        ))
        self.assertIn("T5", line)
        self.assertNotIn("subscriber-capable", line)


class FormatTimingLineBoringTests(unittest.TestCase):
    """Case 4 — instance has no §18 role; emit no line at all to keep
    the Overview screen scannable."""

    def test_all_false_returns_none(self):
        self.assertIsNone(format_timing_line(_inst()))

    def test_explicit_none_for_applied_with_no_capability(self):
        self.assertIsNone(format_timing_line(_inst(
            uses_timing_calibration=False,
            timing_authority_applied=None,
        )))

    def test_non_dict_applied_value_treated_as_absent(self):
        """A garbage applied value (string, list) shouldn't be rendered
        as if it were a populated dict. Falls back to capability check."""
        for bad in ('hf-timestd', ['a'], 42, True):
            self.assertIsNone(format_timing_line(_inst(
                timing_authority_applied=bad,
            )), f"non-dict applied={bad!r} should yield None")


def _snap(**overrides) -> AuthoritySnapshot:
    """A T3-active snapshot resembling smd timing's own docstring
    example (tier T3, available T3/T4, witness T4) — the everyday
    "usable but not hard-deadline-grade" case."""
    base = dict(
        schema="v1",
        utc_published=None,
        a_level="A1",
        t_level_active="T3",
        t_level_available=["T3", "T4"],
        t_level_witnesses=["T4"],
        rtp_to_utc_offset_ns=92_012,
        sigma_ns=3_260_000,
        governor_radiod="AC0G-B4-status.local",
    )
    base.update(overrides)
    return AuthoritySnapshot(**base)


class RenderOverviewTimingBodyTests(unittest.TestCase):
    """Pure-function tests for the Overview landing screen's shallow
    timing verdict — no Textual required.  ``render_overview_timing_body``
    is the single source both the screen and these tests exercise, so
    a passing test here is a guarantee about exactly what the operator
    sees, not just that something rendered."""

    def test_healthy_t3_renders_tier_offset_sigma_governor_witnesses(self):
        body = render_overview_timing_body(_snap(), None, 4.2, None)
        self.assertIn("T3", body)
        self.assertIn("+92.01", body)          # offset, µs-scaled
        self.assertIn("3.26 ms", body)         # sigma, ms-scaled
        self.assertIn("AC0G-B4-status.local", body)
        self.assertIn("T4", body)              # available + witness

    def test_t6_tier_renders_green(self):
        body = render_overview_timing_body(
            _snap(t_level_active="T6"), None, 1.0, None,
        )
        self.assertIn("[bold green]T6[/]", body)

    def test_t4_tier_renders_yellow(self):
        body = render_overview_timing_body(
            _snap(t_level_active="T4"), None, 1.0, None,
        )
        self.assertIn("[bold yellow]T4[/]", body)

    def test_t3_tier_renders_red(self):
        """T3 is usable for sample-labelling but not hard-deadline
        gating — same tier-quality colouring as the Authority screen
        and format_timing_line, not a separate scheme."""
        body = render_overview_timing_body(_snap(), None, 1.0, None)
        self.assertIn("[bold red]T3[/]", body)

    def test_no_snapshot_gives_explanatory_line_not_a_fake_tier(self):
        """A host with no authority.json (not an hf-timestd host, or
        the service is down) is not an error state — mirror smd
        timing's own wording rather than editorialising, and never
        show a bogus 'Tier:' line."""
        body = render_overview_timing_body(None, ERR_NOT_FOUND, None, None)
        self.assertIn("no authority snapshot at", body)
        self.assertIn(ERR_NOT_FOUND, body)
        self.assertIn(
            "not an hf-timestd host, service down, or file stale-deleted",
            body,
        )
        self.assertNotIn("Tier:", body)
        for bogus_tier in ("T0", "T1", "T2", "T3", "T4", "T5", "T6"):
            self.assertNotIn(bogus_tier, body)

    def test_stale_snapshot_renders_red_stale_warning(self):
        """Past AUTHORITY_STALE_THRESHOLD_S (60s) — the authority
        manager may have stalled; same threshold/colouring as the
        Authority screen so the two can't disagree about staleness."""
        body = render_overview_timing_body(_snap(), None, 120.0, None)
        self.assertIn("⚠ stale", body)
        self.assertIn("[red]", body)

    def test_fresh_snapshot_has_no_stale_warning(self):
        body = render_overview_timing_body(_snap(), None, 4.2, None)
        self.assertNotIn("⚠ stale", body)
        self.assertIn("[green]", body)         # age under 30s -> green

    def test_chrony_present_renders_reference_stratum_offset(self):
        chrony = {
            "reference": "FUSE", "stratum": 1,
            "system_offset_s": 1.8e-6, "rms_offset_s": 7.2e-5,
            "root_dispersion_s": 1e-4, "leap_status": "Normal",
        }
        body = render_overview_timing_body(_snap(), None, 4.2, chrony)
        self.assertIn("FUSE", body)
        self.assertIn("stratum 1", body)
        self.assertIn("µs", body)

    def test_chrony_absent_renders_unavailable_not_blank(self):
        body = render_overview_timing_body(_snap(), None, 4.2, None)
        self.assertIn("chrony: unavailable", body)

    def test_chrony_line_present_even_without_authority_snapshot(self):
        """Authority and chrony are independent facts — a host that's
        not an hf-timestd host can still have a healthy chrony."""
        chrony = {
            "reference": "GPS", "stratum": 1,
            "system_offset_s": 5e-7, "rms_offset_s": 1e-6,
            "root_dispersion_s": 1e-5, "leap_status": "Normal",
        }
        body = render_overview_timing_body(None, ERR_NOT_FOUND, None, chrony)
        self.assertIn("GPS", body)
        self.assertIn("no authority snapshot at", body)

    def test_missing_governor_omits_governor_line(self):
        body = render_overview_timing_body(
            _snap(governor_radiod=None), None, 1.0, None,
        )
        self.assertNotIn("Governor:", body)

    def test_empty_available_and_witnesses_render_dash(self):
        body = render_overview_timing_body(
            _snap(t_level_available=[], t_level_witnesses=[]), None, 1.0, None,
        )
        self.assertIn("available: —", body)
        self.assertIn("witnesses: —", body)


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class GatherTimingTests(unittest.TestCase):
    """``_gather_timing`` is the Overview screen's only host-touching
    entry point for the timing section — it calls exactly
    ``read_authority_snapshot`` and ``chrony_tracking`` (both in
    sigmond.tui.format).  Mocking those two call sites (per the
    project's mock.patch.object-on-the-call-site pattern, dc48e80)
    keeps this hermetic without needing to fake out topology/catalog/
    systemctl the rest of _gather_overview touches."""

    def test_healthy_t3_host(self):
        from unittest import mock
        import sigmond.tui.screens.overview as overview

        snap = _snap()
        with mock.patch.object(
                overview, "read_authority_snapshot",
                return_value=(snap, None)), \
             mock.patch.object(
                overview, "snapshot_age_seconds", return_value=4.2), \
             mock.patch.object(
                overview, "chrony_tracking",
                return_value={"reference": "FUSE", "stratum": 1,
                              "system_offset_s": 1e-6, "rms_offset_s": 1e-6,
                              "root_dispersion_s": 1e-5, "leap_status": "Normal"}):
            got_snap, got_err, got_age, got_chrony = overview._gather_timing()
        self.assertIs(got_snap, snap)
        self.assertIsNone(got_err)
        self.assertEqual(got_age, 4.2)
        self.assertEqual(got_chrony["reference"], "FUSE")

    def test_no_authority_host(self):
        from unittest import mock
        import sigmond.tui.screens.overview as overview

        with mock.patch.object(
                overview, "read_authority_snapshot",
                return_value=(None, ERR_NOT_FOUND)), \
             mock.patch.object(
                overview, "chrony_tracking", return_value={}):
            got_snap, got_err, got_age, got_chrony = overview._gather_timing()
        self.assertIsNone(got_snap)
        self.assertEqual(got_err, ERR_NOT_FOUND)
        self.assertIsNone(got_age)
        self.assertEqual(got_chrony, {})

    def test_stale_snapshot_age_flows_through(self):
        from unittest import mock
        import sigmond.tui.screens.overview as overview

        snap = _snap()
        with mock.patch.object(
                overview, "read_authority_snapshot",
                return_value=(snap, None)), \
             mock.patch.object(
                overview, "snapshot_age_seconds", return_value=180.0), \
             mock.patch.object(
                overview, "chrony_tracking", return_value={}):
            _, _, got_age, _ = overview._gather_timing()
        self.assertEqual(got_age, 180.0)


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class OverviewTimingSectionMountTests(unittest.IsolatedAsyncioTestCase):
    """Mounts the real OverviewScreen and asserts on the *rendered
    content* of the #ov-timing widget — not merely that the screen
    exists.  ``_refresh`` (which spawns the real background worker
    and touches the real host) is patched to a no-op so the widget
    tree is built without any host I/O; ``_render_timing`` is then
    called directly against a hand-built ``_OverviewData``, exactly
    as ``_gather_overview`` would populate it."""

    async def _render(self, data) -> str:
        from unittest import mock
        from textual.app import App
        from textual.widgets import Static
        from sigmond.tui.screens.overview import OverviewScreen

        class _Harness(App):
            def compose(self):
                yield OverviewScreen()

        app = _Harness()
        with mock.patch.object(OverviewScreen, "_refresh", lambda self: None):
            async with app.run_test() as pilot:
                screen = app.query_one(OverviewScreen)
                screen._render_timing(data)
                await pilot.pause()
                widget = screen.query_one("#ov-timing", Static)
                return str(widget.render())

    async def test_healthy_t3_host_renders_tier_and_offset(self):
        from sigmond.tui.screens.overview import _OverviewData

        data = _OverviewData(
            timing_snapshot=_snap(), timing_error=None, timing_age_s=4.2,
            chrony={"reference": "FUSE", "stratum": 1,
                    "system_offset_s": 1.8e-6, "rms_offset_s": 7.2e-5,
                    "root_dispersion_s": 1e-4, "leap_status": "Normal"},
        )
        content = await self._render(data)
        self.assertIn("T3", content)
        self.assertIn("92.01", content)
        self.assertIn("FUSE", content)

    async def test_no_authority_host_renders_explanation_not_traceback(self):
        from sigmond.tui.screens.overview import _OverviewData

        data = _OverviewData(
            timing_snapshot=None, timing_error=ERR_NOT_FOUND,
            timing_age_s=None, chrony={},
        )
        content = await self._render(data)
        self.assertIn("no authority snapshot at", content)
        self.assertIn("not an hf-timestd host", content)

    async def test_stale_snapshot_renders_stale_warning(self):
        from sigmond.tui.screens.overview import _OverviewData

        data = _OverviewData(
            timing_snapshot=_snap(), timing_error=None, timing_age_s=180.0,
            chrony=None,
        )
        content = await self._render(data)
        self.assertIn("stale", content)


if __name__ == '__main__':
    unittest.main()

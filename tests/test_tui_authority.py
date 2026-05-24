"""Tests for the Authority screen's pure-Python helpers.

Reader (``read_authority_snapshot``), age computation
(``snapshot_age_seconds``), and the numeric formatters all live in
``sigmond.tui.format`` (no Textual dependency).  The screen-side
``render_authority_body`` function in ``screens/authority.py`` is
imported lazily inside the test so a textual-less env skips its
tests cleanly while still running the format tests.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond.tui.format import (
    AuthoritySnapshot,
    ERR_MALFORMED,
    ERR_NOT_FOUND,
    ERR_UNREADABLE,
    format_age_seconds,
    format_offset_ns,
    format_sigma_ns,
    read_authority_snapshot,
    render_authority_body,
    snapshot_age_seconds,
)


# Captured shape mirroring the live bee1 output post-2026-05-23
# (T6 active via TS-1 BPSK, A1 from RX-888 GPSDO discipline).
SAMPLE_SNAPSHOT = {
    "schema": "v1",
    "utc_published": "2026-05-24T18:30:21.123456Z",
    "a_level": "A1",
    "t_level_active": "T6",
    "t_level_available": ["T6", "T5", "T4"],
    "t_level_witnesses": ["T5"],
    "rtp_to_utc_offset_ns": 7,
    "sigma_ns": 1,
    "stations_contributing": [],
    "last_transition_utc": "2026-05-24T16:13:44Z",
    "disagreement_flags": [],
    "governor_radiod": "bee3-rx888",
}


class ReadAuthoritySnapshotTests(unittest.TestCase):
    """Reader handles every failure mode the operator might encounter."""

    def test_normal_file_parses_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "authority.json"
            p.write_text(json.dumps(SAMPLE_SNAPSHOT))
            snap, err = read_authority_snapshot(p)
        self.assertIsNone(err)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.schema, "v1")
        self.assertEqual(snap.a_level, "A1")
        self.assertEqual(snap.t_level_active, "T6")
        self.assertEqual(snap.t_level_available, ["T6", "T5", "T4"])
        self.assertEqual(snap.t_level_witnesses, ["T5"])
        self.assertEqual(snap.rtp_to_utc_offset_ns, 7)
        self.assertEqual(snap.sigma_ns, 1)
        self.assertEqual(snap.governor_radiod, "bee3-rx888")
        # ISO timestamp must round-trip to a tz-aware UTC datetime.
        self.assertIsNotNone(snap.utc_published)
        self.assertEqual(snap.utc_published.tzinfo, timezone.utc)
        self.assertEqual(snap.utc_published.year, 2026)

    def test_raw_field_preserved(self):
        """The ``raw`` dict lets future fields be read without bumping
        the AuthoritySnapshot dataclass."""
        snap_dict = dict(SAMPLE_SNAPSHOT)
        snap_dict["future_field"] = "added in v2"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "authority.json"
            p.write_text(json.dumps(snap_dict))
            snap, _ = read_authority_snapshot(p)
        self.assertEqual(snap.raw["future_field"], "added in v2")

    def test_missing_file_returns_not_found(self):
        snap, err = read_authority_snapshot(Path("/nonexistent/authority.json"))
        self.assertIsNone(snap)
        self.assertEqual(err, ERR_NOT_FOUND)

    def test_malformed_json_returns_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "authority.json"
            p.write_text("{not valid json")
            snap, err = read_authority_snapshot(p)
        self.assertIsNone(snap)
        self.assertEqual(err, ERR_MALFORMED)

    def test_non_dict_root_returns_malformed(self):
        """Defensive: a JSON file with a list / string at the top
        level isn't contract-conformant — surface as malformed
        rather than crashing on .get()."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "authority.json"
            p.write_text("[1, 2, 3]")
            snap, err = read_authority_snapshot(p)
        self.assertIsNone(snap)
        self.assertEqual(err, ERR_MALFORMED)

    def test_missing_optional_fields_default_to_none_or_empty(self):
        """Authority.json's optional fields (governor_radiod, last_
        transition_utc, bootstrap) are absent in fresh deployments —
        the reader must not crash."""
        minimal = {
            "schema": "v1",
            "utc_published": "2026-05-24T18:30:21Z",
            "a_level": "A1",
            "t_level_active": "T3",
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "authority.json"
            p.write_text(json.dumps(minimal))
            snap, _ = read_authority_snapshot(p)
        self.assertEqual(snap.t_level_active, "T3")
        self.assertIsNone(snap.governor_radiod)
        self.assertIsNone(snap.last_transition_utc)
        self.assertIsNone(snap.bootstrap)
        self.assertEqual(snap.t_level_available, [])
        self.assertEqual(snap.t_level_witnesses, [])
        self.assertEqual(snap.disagreement_flags, [])

    def test_iso_with_z_suffix_parses(self):
        """authority_manager._iso_z emits trailing-Z; the reader
        must handle that without help."""
        snap = AuthoritySnapshot(
            schema="v1",
            utc_published=None,
            a_level="A1",
            t_level_active="T6",
        )
        # Exercise the parser via the full read path so the
        # normalisation logic is covered end-to-end.
        snap_dict = dict(SAMPLE_SNAPSHOT)
        snap_dict["utc_published"] = "2026-05-24T18:30:21Z"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "authority.json"
            p.write_text(json.dumps(snap_dict))
            parsed, _ = read_authority_snapshot(p)
        self.assertIsNotNone(parsed.utc_published)
        self.assertEqual(parsed.utc_published.tzinfo, timezone.utc)


class SnapshotAgeTests(unittest.TestCase):
    """Age computation drives the staleness colour and warning."""

    def test_fresh_snapshot_returns_small_positive_age(self):
        snap = AuthoritySnapshot(
            schema="v1",
            utc_published=datetime(2026, 5, 24, 18, 30, 0, tzinfo=timezone.utc),
            a_level="A1",
            t_level_active="T6",
        )
        now = datetime(2026, 5, 24, 18, 30, 4, tzinfo=timezone.utc)
        self.assertAlmostEqual(snapshot_age_seconds(snap, now=now), 4.0, places=3)

    def test_stale_snapshot_returns_large_age(self):
        snap = AuthoritySnapshot(
            schema="v1",
            utc_published=datetime(2026, 5, 24, 18, 0, 0, tzinfo=timezone.utc),
            a_level="A1",
            t_level_active="T6",
        )
        now = datetime(2026, 5, 24, 18, 35, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(snapshot_age_seconds(snap, now=now), 2100.0, places=1)

    def test_no_utc_published_returns_none(self):
        """When the reader couldn't parse utc_published, age is
        meaningless — the screen should treat this as 'unknown'."""
        snap = AuthoritySnapshot(
            schema="v1",
            utc_published=None,
            a_level="A1",
            t_level_active="T6",
        )
        self.assertIsNone(snapshot_age_seconds(snap))


class FormatOffsetNsTests(unittest.TestCase):
    """Auto-scaling matches the ns / µs / ms / s breakpoints from
    timing.py's format_offset (different signature, same scheme)."""

    def test_ns_range(self):
        self.assertEqual(format_offset_ns(0), "+0 ns")
        self.assertEqual(format_offset_ns(7), "+7 ns")
        self.assertEqual(format_offset_ns(-7), "-7 ns")
        self.assertEqual(format_offset_ns(999), "+999 ns")

    def test_us_range(self):
        self.assertEqual(format_offset_ns(1_000), "+1.00 µs")
        self.assertEqual(format_offset_ns(1_500), "+1.50 µs")
        self.assertEqual(format_offset_ns(-50_000), "-50.00 µs")

    def test_ms_range(self):
        self.assertEqual(format_offset_ns(1_000_000), "+1.00 ms")
        self.assertEqual(format_offset_ns(3_500_000), "+3.50 ms")

    def test_s_range(self):
        self.assertEqual(format_offset_ns(1_500_000_000), "+1.500 s")

    def test_none_renders_question_mark(self):
        self.assertEqual(format_offset_ns(None), "?")


class FormatSigmaNsTests(unittest.TestCase):
    """σ is always non-negative; the formatter omits the sign."""

    def test_ns_us_ms_s_progression(self):
        self.assertEqual(format_sigma_ns(1), "1 ns")
        self.assertEqual(format_sigma_ns(1_200), "1.20 µs")
        self.assertEqual(format_sigma_ns(3_500_000), "3.50 ms")

    def test_none_renders_question_mark(self):
        self.assertEqual(format_sigma_ns(None), "?")


class FormatAgeSecondsTests(unittest.TestCase):
    """Operator-readable durations: ``Ns`` / ``Nm Ss`` / ``Nh Mm``."""

    def test_under_minute_uses_seconds(self):
        self.assertEqual(format_age_seconds(4.2), "4.2s")
        self.assertEqual(format_age_seconds(0), "0.0s")
        self.assertEqual(format_age_seconds(59.9), "59.9s")

    def test_minute_to_hour_uses_minutes(self):
        self.assertEqual(format_age_seconds(60.0), "1m 0s")
        self.assertEqual(format_age_seconds(125.0), "2m 5s")

    def test_over_hour_uses_hours(self):
        self.assertEqual(format_age_seconds(3600.0), "1h 0m")
        self.assertEqual(format_age_seconds(8160.0), "2h 16m")

    def test_negative_clamped_to_zero(self):
        """A snapshot whose utc_published is *ahead* of wall clock
        (rare, but possible under clock skew during a chrony slew)
        gets clamped to 0 rather than printing a negative duration."""
        self.assertEqual(format_age_seconds(-5.0), "0.0s")

    def test_none_renders_question_mark(self):
        self.assertEqual(format_age_seconds(None), "?")


class RenderAuthorityBodyTests(unittest.TestCase):
    """Tests for the pure-Python render function — lives in
    ``tui/format.py`` so it runs in any environment, no Textual
    required.  The screen module in ``screens/authority.py`` just
    wraps this function in a Textual widget."""

    def setUp(self):
        self.render = render_authority_body

    def _snap(self, **overrides) -> AuthoritySnapshot:
        base = dict(
            schema="v1",
            utc_published=datetime(2026, 5, 24, 18, 30, tzinfo=timezone.utc),
            a_level="A1",
            t_level_active="T6",
            t_level_available=["T6", "T5", "T4"],
            t_level_witnesses=["T5"],
            rtp_to_utc_offset_ns=7,
            sigma_ns=1,
            governor_radiod="bee3-rx888",
        )
        base.update(overrides)
        return AuthoritySnapshot(**base)

    def test_not_found_renders_diagnostic(self):
        body = self.render(None, ERR_NOT_FOUND, None)
        self.assertIn("No authority snapshot found", body)
        self.assertIn("hf-timestd is not running", body)
        self.assertIn("[red]", body)

    def test_unreadable_renders_permissions_hint(self):
        body = self.render(None, ERR_UNREADABLE, None)
        self.assertIn("not readable", body)
        self.assertIn("world-readable", body)

    def test_malformed_renders_race_hint(self):
        body = self.render(None, ERR_MALFORMED, None)
        self.assertIn("unparseable", body)
        self.assertIn("atomic write", body)

    def test_normal_snapshot_renders_header(self):
        body = self.render(self._snap(), None, 4.2)
        self.assertIn("T6", body)
        self.assertIn("A1", body)
        self.assertIn("+7 ns", body)         # rtp_to_utc offset
        self.assertIn("1 ns", body)          # σ
        self.assertIn("bee3-rx888", body)    # governor radiod

    def test_fresh_snapshot_renders_age_green(self):
        body = self.render(self._snap(), None, 4.2)
        # Age "4.2s ago" inside a green span — fresh.
        self.assertIn("4.2s ago", body)
        self.assertIn("[green]", body)
        self.assertNotIn("⚠ stale", body)

    def test_stale_snapshot_renders_red_warning(self):
        """Past STALE_THRESHOLD_S → age coloured red + warning line."""
        body = self.render(self._snap(), None, 120.0)
        self.assertIn("[red]", body)
        self.assertIn("⚠ stale", body)
        self.assertIn("stalled", body)

    def test_disagreement_flags_render_red(self):
        body = self.render(
            self._snap(disagreement_flags=["t6_vs_t5"]), None, 1.0,
        )
        self.assertIn("Disagreements:", body)
        self.assertIn("t6_vs_t5", body)
        self.assertIn("[red]⚠", body)

    def test_no_disagreement_renders_green(self):
        body = self.render(self._snap(), None, 1.0)
        self.assertIn("Disagreements: [green]none[/]", body)

    def test_t3_active_uses_red_tier_colour(self):
        body = self.render(
            self._snap(t_level_active="T3"), None, 1.0,
        )
        # T3 falls through to red in _tier_colour.  We don't pin the
        # exact tag string, just that the header line contains both
        # T3 and the red marker.
        self.assertIn("T3", body)
        self.assertIn("[bold red]", body)

    def test_bootstrap_pending_surfaces(self):
        body = self.render(
            self._snap(bootstrap={"complete": False,
                                  "reason": "wallclock_skew",
                                  "delta_sec": 5.2}),
            None, 1.0,
        )
        self.assertIn("Bootstrap pending", body)
        self.assertIn("wallclock_skew", body)

    def test_bootstrap_complete_not_surfaced(self):
        """Once bootstrap is complete it's no longer interesting; the
        line is suppressed to keep the screen scannable."""
        body = self.render(
            self._snap(bootstrap={"complete": True, "reason": "ok"}),
            None, 1.0,
        )
        self.assertNotIn("Bootstrap pending", body)


if __name__ == '__main__':
    unittest.main()

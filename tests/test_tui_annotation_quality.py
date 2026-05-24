"""Tests for the Annotation Quality screen's pure helpers.

Reader (``read_core_recorder_status``), unit enumeration
(``enumerate_timing_consumer_units``), verdict thresholds
(``_annotation_verdict``), and the body renderer
(``render_annotation_quality_body``) all live in
``sigmond.tui.format`` and have no Textual dependency.
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
    ANNOTATION_VERDICT_GREEN_NS,
    ANNOTATION_VERDICT_YELLOW_NS,
    AuthoritySnapshot,
    CoreRecorderStatus,
    ERR_MALFORMED,
    ERR_NOT_FOUND,
    TimingConsumerUnit,
    _annotation_verdict,
    enumerate_timing_consumer_units,
    read_core_recorder_status,
    render_annotation_quality_body,
)


# Mirrors the real /var/lib/timestd/status/core-recorder-status.json
# shape (subset of fields the screen consumes).
SAMPLE_RECORDER_STATUS = {
    "timestamp": "2026-05-24T21:00:00.123456Z",
    "l6_pps": {
        "enabled": True,
        "locked": True,
        "pps_consecutive": 50,
        "chain_delay_ns": 174_147_000,
        "chain_delay_ns_std_ns": 950.0,
        "local_minus_source_ns": -280_000_000,
        "drift_monitor": {
            "sustained_breach": True,
            "anchor_discontinuity": False,
            "anchor_residual_samples": 223,
            "breach_duration_sec": 528.8,
            "recapture_count": 34,
            "last_recapture_age_sec": 540.0,
            "last_recapture_reason": "sustained_breach",
        },
    },
}


class ReadCoreRecorderStatusTests(unittest.TestCase):

    def test_normal_file_parses_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "core-recorder-status.json"
            p.write_text(json.dumps(SAMPLE_RECORDER_STATUS))
            status, err = read_core_recorder_status(p)
        self.assertIsNone(err)
        self.assertIsNotNone(status)
        self.assertEqual(status.local_minus_source_ns, -280_000_000)
        self.assertEqual(status.chain_delay_ns, 174_147_000)
        self.assertEqual(status.pps_consecutive, 50)
        self.assertTrue(status.locked)
        self.assertTrue(status.sustained_breach)
        self.assertFalse(status.anchor_discontinuity)
        self.assertAlmostEqual(status.breach_duration_sec, 528.8)
        self.assertEqual(status.recapture_count, 34)
        self.assertEqual(status.last_recapture_reason, "sustained_breach")
        self.assertEqual(status.utc_published.year, 2026)
        self.assertEqual(status.utc_published.tzinfo, timezone.utc)

    def test_missing_file_returns_not_found(self):
        status, err = read_core_recorder_status(
            Path("/nonexistent/core-recorder-status.json")
        )
        self.assertIsNone(status)
        self.assertEqual(err, ERR_NOT_FOUND)

    def test_malformed_json_returns_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "core-recorder-status.json"
            p.write_text("{not json")
            status, err = read_core_recorder_status(p)
        self.assertIsNone(status)
        self.assertEqual(err, ERR_MALFORMED)

    def test_missing_l6_pps_block_yields_empty_fields(self):
        """Pre-T6 deployments may not have an l6_pps block; the reader
        must not crash, just return None for every l6-derived field."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "core-recorder-status.json"
            p.write_text(json.dumps({"timestamp": "2026-05-24T21:00:00Z"}))
            status, err = read_core_recorder_status(p)
        self.assertIsNone(err)
        self.assertIsNone(status.local_minus_source_ns)
        self.assertIsNone(status.sustained_breach)
        self.assertIsNone(status.recapture_count)

    def test_unparseable_numeric_fields_become_none(self):
        """Defensive: a producer that emits a string in a numeric slot
        shouldn't crash the screen."""
        bad = {
            "timestamp": "2026-05-24T21:00:00Z",
            "l6_pps": {"local_minus_source_ns": "not-a-number"},
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "core-recorder-status.json"
            p.write_text(json.dumps(bad))
            status, err = read_core_recorder_status(p)
        self.assertIsNone(err)
        self.assertIsNone(status.local_minus_source_ns)


class AnnotationVerdictTests(unittest.TestCase):

    def test_green_below_100us(self):
        label, colour = _annotation_verdict(50_000)
        self.assertEqual(label, "GREEN")
        self.assertEqual(colour, "green")

    def test_yellow_between_100us_and_10ms(self):
        label, colour = _annotation_verdict(1_500_000)
        self.assertEqual(label, "YELLOW")
        self.assertEqual(colour, "yellow")

    def test_red_at_or_above_10ms(self):
        label, colour = _annotation_verdict(15_000_000)
        self.assertEqual(label, "RED")
        self.assertEqual(colour, "red")

    def test_none_renders_as_unknown(self):
        label, colour = _annotation_verdict(None)
        self.assertEqual(label, "?")
        self.assertEqual(colour, "dim")

    def test_boundary_green_yellow_is_yellow(self):
        # σ = 100 µs exactly — at the boundary, yellow not green.
        label, _ = _annotation_verdict(ANNOTATION_VERDICT_GREEN_NS)
        self.assertEqual(label, "YELLOW")

    def test_boundary_yellow_red_is_red(self):
        label, _ = _annotation_verdict(ANNOTATION_VERDICT_YELLOW_NS)
        self.assertEqual(label, "RED")

    def test_negative_sigma_uses_abs(self):
        """σ should never be negative, but if a producer ever emits
        one, the absolute value is what matters for the verdict."""
        label, _ = _annotation_verdict(-50_000)
        self.assertEqual(label, "GREEN")


class EnumerateTimingConsumerUnitsTests(unittest.TestCase):
    """The systemctl wrapper is fed a fake runner so the test is
    hermetic and doesn't depend on the host's actual unit state."""

    def _make_runner(self, per_pattern_output):
        """per_pattern_output: dict mapping pattern (the last arg of
        the systemctl call) to (returncode, stdout, stderr).
        """
        class _FakeResult:
            def __init__(self, returncode, stdout, stderr):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        def runner(cmd, **kwargs):
            pattern = cmd[-1]
            rc, out, err = per_pattern_output.get(pattern, (1, "", ""))
            return _FakeResult(rc, out, err)
        return runner

    def test_extracts_template_instance_label(self):
        runner = self._make_runner({
            "timestd-metrology@*.service": (
                0,
                "timestd-metrology@WWV_20000.service loaded active running\n"
                "timestd-metrology@CHU_3330.service loaded active running\n",
                "",
            ),
        })
        units = enumerate_timing_consumer_units(runner=runner)
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0].client, "timestd-metrology")
        self.assertEqual(units[0].instance, "WWV_20000")
        self.assertEqual(units[0].unit,
                         "timestd-metrology@WWV_20000.service")
        self.assertEqual(units[1].instance, "CHU_3330")

    def test_non_templated_unit_has_empty_instance(self):
        runner = self._make_runner({
            "mag-recorder.service": (
                0,
                "mag-recorder.service loaded active running\n",
                "",
            ),
        })
        units = enumerate_timing_consumer_units(runner=runner)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].client, "mag-recorder")
        self.assertEqual(units[0].instance, "")

    def test_no_matches_returns_empty_list(self):
        runner = self._make_runner({})  # every pattern returns rc=1
        units = enumerate_timing_consumer_units(runner=runner)
        self.assertEqual(units, [])

    def test_systemctl_missing_returns_empty_list(self):
        """FileNotFoundError on systemctl (containers without it) must
        not crash the screen — return empty list and let the renderer
        say '(no running timing consumers)'."""
        def runner(cmd, **kwargs):
            raise FileNotFoundError("no systemctl")
        units = enumerate_timing_consumer_units(runner=runner)
        self.assertEqual(units, [])


class RenderAnnotationQualityBodyTests(unittest.TestCase):
    """End-to-end rendering: the function takes pre-parsed inputs and
    produces a string with markup the screen can hand to a Textual
    Static.  These tests validate content, not pixel layout."""

    def _full_inputs(self, sigma_ns=262_000_000, breach=True):
        auth = AuthoritySnapshot(
            schema="v1",
            utc_published=datetime(2026, 5, 24, 21, 0, 0, tzinfo=timezone.utc),
            a_level="A1",
            t_level_active="T6",
            t_level_available=["T6", "T4", "T3"],
            t_level_witnesses=["T4"],
            rtp_to_utc_offset_ns=-sigma_ns,
            sigma_ns=sigma_ns,
            disagreement_flags=["chrony-rejected-HPPS:state=x"],
            governor_radiod="bee1-status.local",
        )
        recorder = CoreRecorderStatus(
            utc_published=datetime(2026, 5, 24, 21, 0, 0, tzinfo=timezone.utc),
            local_minus_source_ns=-sigma_ns if breach else 1000,
            chain_delay_ns=174_147_000,
            chain_delay_ns_std_ns=950.0,
            pps_consecutive=50,
            locked=True,
            sustained_breach=breach,
            anchor_discontinuity=False,
            breach_duration_sec=528.8 if breach else None,
            recapture_count=34,
            last_recapture_reason="sustained_breach" if breach else None,
            last_recapture_age_sec=540.0,
        )
        units = [
            TimingConsumerUnit("timestd-metrology", "WWV_20000",
                               "timestd-metrology@WWV_20000.service"),
            TimingConsumerUnit("timestd-metrology", "CHU_3330",
                               "timestd-metrology@CHU_3330.service"),
            TimingConsumerUnit("codar-sounder", "ac0g-bee1-rx888",
                               "codar-sounder@ac0g-bee1-rx888.service"),
            TimingConsumerUnit("mag-recorder", "",
                               "mag-recorder.service"),
        ]
        return auth, recorder, units

    def test_normal_rendering_includes_all_sections(self):
        auth, recorder, units = self._full_inputs()
        out = render_annotation_quality_body(
            auth, None, 5.0, recorder, None, units,
        )
        self.assertIn("Active:", out)
        self.assertIn("T6", out)
        self.assertIn("262.00 ms", out)        # σ scaled
        self.assertIn("RED", out)              # verdict at σ=262 ms
        self.assertIn("Per-consumer", out)
        self.assertIn("WWV_20000", out)        # per-stream row label
        self.assertIn("CHU_3330", out)
        self.assertIn("ac0g-bee1-rx888", out)
        self.assertIn("(default)", out)        # mag-recorder non-templated
        self.assertIn("timestd-metrology", out)
        self.assertIn("codar-sounder", out)
        self.assertIn("mag-recorder", out)
        self.assertIn("Substrate detail", out)
        self.assertIn("local_minus_source", out)
        self.assertIn("sustained_breach = [red]yes[/]", out)
        self.assertIn("disagreement flags", out)

    def test_green_verdict_when_sigma_tight(self):
        auth, recorder, units = self._full_inputs(sigma_ns=50_000, breach=False)
        out = render_annotation_quality_body(
            auth, None, 5.0, recorder, None, units,
        )
        self.assertIn("GREEN", out)
        self.assertNotIn("RED", out)

    def test_no_consumers_renders_helpful_message(self):
        auth, recorder, _ = self._full_inputs()
        out = render_annotation_quality_body(
            auth, None, 5.0, recorder, None, [],
        )
        self.assertIn("no running timing consumers", out)

    def test_authority_absent_short_circuits_with_explanation(self):
        out = render_annotation_quality_body(
            None, ERR_NOT_FOUND, None, None, None, [],
        )
        self.assertIn("hf-timestd authority unavailable", out)
        self.assertIn("timestd-fusion", out)
        # Should not crash trying to render the rest.
        self.assertNotIn("Per-consumer", out)

    def test_recorder_status_absent_degrades_gracefully(self):
        """Authority is good; substrate detail isn't.  Per-stream view
        still works; substrate panel shows the absence rather than
        crashing."""
        auth, _, units = self._full_inputs()
        out = render_annotation_quality_body(
            auth, None, 5.0, None, ERR_NOT_FOUND, units,
        )
        self.assertIn("Per-consumer", out)
        self.assertIn("WWV_20000", out)
        self.assertIn("core-recorder status unavailable", out)


if __name__ == '__main__':
    unittest.main()

"""Tests for the Apply screen's dry-run output classifier.

Lives in ``sigmond.apply_pending`` (not in tui/screens/apply.py) so the
classifier is importable without pulling in the Textual runtime —
makes it usable from this unit test environment, which doesn't have
textual installed.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond.apply_pending import classify_line, strip_ansi


class StripAnsiTests(unittest.TestCase):
    def test_drops_sgr_codes(self):
        self.assertEqual(strip_ansi("\x1b[32m✓\x1b[0m hello"), "✓ hello")

    def test_drops_multiple_codes(self):
        self.assertEqual(
            strip_ansi("\x1b[1m\x1b[31mfoo\x1b[0m bar"),
            "foo bar",
        )

    def test_passthrough_when_no_ansi(self):
        self.assertEqual(strip_ansi("plain text"), "plain text")


class ClassifyLineTests(unittest.TestCase):
    """Mirrors the line shapes bin/smd's apply reconciler actually
    emits.  When a new step is added to cmd_apply that uses different
    phrasing, add a row here so the Pending pane keeps classifying
    correctly."""

    def test_unchanged_line(self):
        line = ("\x1b[32m✓\x1b[0m  radiod fragment: unchanged: "
                "/etc/radio/radiod@bee1-status.local.conf.d/40-codar-sounder.conf")
        self.assertEqual(classify_line(line), 'unchanged')

    def test_dry_run_marker(self):
        line = ("\x1b[36m  (dry-run) would create "
                "/etc/systemd/system/foo.service\x1b[0m")
        self.assertEqual(classify_line(line), 'pending')

    def test_would_phrasing_without_dry_run_marker(self):
        # `systemctl is-enabled` is one of the steps that uses "would
        # enable" rather than "(dry-run) would …" — classifier should
        # catch both phrasings.
        line = "\x1b[32m✓\x1b[0m  systemd enable: would enable foo.service"
        self.assertEqual(classify_line(line), 'pending')

    def test_warning_glyph(self):
        line = ("\x1b[33m⚠\x1b[0m  firmware reconcile failed: "
                "no DFU device matched")
        self.assertEqual(classify_line(line), 'warning')

    def test_warning_word(self):
        line = "  warning: dialout group not present on this host"
        self.assertEqual(classify_line(line), 'warning')

    def test_section_heading(self):
        self.assertEqual(classify_line("\x1b[1m━━━ apply ━━━\x1b[0m"), 'info')

    def test_plain_status_line(self):
        line = ("     network: IGMP diagnosis running in background "
                "→ /var/log/sigmond/net-diag.log")
        self.assertEqual(classify_line(line), 'info')

    def test_empty_line(self):
        self.assertEqual(classify_line(""), 'info')

    def test_whitespace_only(self):
        self.assertEqual(classify_line("    \t   "), 'info')


class ClassifyPrecedenceTests(unittest.TestCase):
    """When a single line could match multiple buckets, the rule
    order in classify_line decides.  These tests pin that order so
    a future refactor doesn't quietly invert it."""

    def test_dry_run_beats_unchanged(self):
        # A composite line shouldn't happen in practice, but the
        # ordering matters: "(dry-run) would replace unchanged X"
        # is a pending change, not a no-op.
        line = "(dry-run) would replace previously-unchanged X"
        self.assertEqual(classify_line(line), 'pending')

    def test_would_beats_warning_word(self):
        # If both 'would' and 'warning' are present, prefer pending
        # — operator cares more about an actionable change than a
        # passing mention of a warning category.
        line = "✓ would silence warning about cpu governor"
        self.assertEqual(classify_line(line), 'pending')


if __name__ == '__main__':
    unittest.main()

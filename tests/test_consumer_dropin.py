"""The consumer drop-in must survive a long radiod absence.

AC0G-B4, 2026-08-30: radiod went away at 03:09Z.  meteor-scatter could
not start without it, and systemd's default start limit -- 5 attempts in
10 seconds -- was gone in about five:

    Scheduled restart job, restart counter is at 21
    Start request repeated too quickly
    Failed with result 'protocol'

It then sat `failed` for twenty-two hours.  Restart=always does not
rescue a unit that has burned its start budget.

This is the same defect already fixed on radiod itself, where
Restart=always with the default StartLimitBurst burned five restarts in
under a second while the SDR was absent and left radiod in `failed`.  The
fix there was StartLimitIntervalSec=0 plus a real RestartSec; it was
never applied to the consumers, which face exactly the same absence.
"""
from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DROPIN = REPO / "systemd" / "sigmond-radiod-consumer.conf.in"


class ConsumerDropIn(unittest.TestCase):
    def setUp(self):
        self.text = DROPIN.read_text()

    def test_the_start_limit_is_disabled(self):
        """A consumer must retry patiently for as long as radiod is away,
        not burn a ten-second budget and give up for a day."""
        self.assertIn("StartLimitIntervalSec=0", self.text)

    def test_retries_are_paced(self):
        """Without a real RestartSec, patient retry becomes a hot loop."""
        self.assertIn("RestartSec=", self.text)

    def test_it_still_waits_for_radiod_to_serve(self):
        self.assertIn("sigmond-radiod-ready", self.text)
        self.assertIn("After=@RADIOD_UNIT@", self.text)

    def test_partof_stays_out(self):
        """Removed deliberately 2026-08-31: the recorders self-heal, and
        PartOf= took them down without a path back up."""
        self.assertNotIn("\nPartOf=", self.text)

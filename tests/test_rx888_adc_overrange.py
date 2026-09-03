"""A clipping A/D must not go unreported.

⛔ AC0G-ND, 2026-09-03.  The RX888 front end ran +18.4 dB of VGA gain and the
A/D clipped 220-450 times a SECOND.  Clipping splatters broadband, so it
destroyed WSPR and FT8 while leaving WWV — 150+ dB SNR — perfect, and every
health check on the station read green all day.

radiod publishes the evidence in every status message and no consumer read it.
One field separates the two stations:

    [108] Samples since A/D overrange     b4: 126,084,448,256  (973 s)
                                          ND:         176,160,768  (1.4 s)

Lowering the AGC's target band to -31 dBFS took the gain to 10.0 dB, cut the
clipping by an order of magnitude, left N0 essentially unchanged
(-131.8 -> -131.5 dB/Hz), and FT8 on 14.074 went from zero decodable messages
to seventeen.

Why the AGC could not do this by itself: `agc_rx888` steers on
`frontend->if_power`, the wideband AVERAGE, toward the midpoint of
[agc-low-threshold, agc-high-threshold] (rx888.c:660-668).  ND read a
comfortable -22.3 dBFS average while its peaks sat on the rail.  An
average-power loop cannot see peak clipping, so the operator needs the counter.

Every reader is injected, so these run with no radiod, no RX888 and no root.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_adcover_under_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_adcover_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

SAMPRATE = 129_600_000.0

# Live readings, 2026-09-03 12:43Z.
ND = {'ssrc': '793918428', 'samprate': SAMPRATE,
      'since_samples': 176_160_768.0, 'overranges': 9_682_983.0,
      'gain': '+18.4', 'if_pwr': '-22.9'}
B4 = {'ssrc': '724334084', 'samprate': SAMPRATE,
      'since_samples': 126_084_448_256.0, 'overranges': 99_553.0,
      'gain': '+11.9', 'if_pwr': '-24.5'}


class DoctorSeesAClippingFrontEndTest(unittest.TestCase):

    def test_a_currently_clipping_ad_is_reported(self):
        f = smd._rx888_adc_overrange_findings(read_frontend=lambda: ND)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].kind, "rx888-adc-overrange")
        self.assertIn("1.4 s ago", f[0].detail)
        self.assertIn("+18.4", f[0].detail)
        # The finding must name the mechanism and the remedy, not just the fact.
        self.assertIn("splatters broadband", f[0].detail)
        self.assertIn("agc-low-threshold", f[0].detail)
        self.assertFalse(f[0].fixable,
                         "the right gain is the operator's call, not doctor's")

    def test_a_station_quiet_for_minutes_is_silent(self):
        # b4 has clipped historically and does not now. A cumulative count on
        # its own says nothing; only recency does.
        self.assertEqual(
            smd._rx888_adc_overrange_findings(read_frontend=lambda: B4), [])

    def test_a_front_end_that_never_clipped_is_silent(self):
        fresh = dict(ND, overranges=0.0, since_samples=0.0)
        self.assertEqual(
            smd._rx888_adc_overrange_findings(read_frontend=lambda: fresh), [])

    def test_no_radiod_is_not_a_finding(self):
        # Absence of evidence must never be reported as a fault.
        for reader in (lambda: None, lambda: {}, lambda: {'samprate': 0}):
            with self.subTest(reader=reader):
                self.assertEqual(
                    smd._rx888_adc_overrange_findings(read_frontend=reader), [])

    def test_the_quiet_window_is_the_only_discriminator(self):
        # One overrange 59 s ago still counts; 61 s ago does not.
        near = dict(ND, since_samples=59.0 * SAMPRATE, overranges=1.0)
        far = dict(ND, since_samples=61.0 * SAMPRATE, overranges=1.0)
        self.assertEqual(
            len(smd._rx888_adc_overrange_findings(read_frontend=lambda: near)), 1)
        self.assertEqual(
            smd._rx888_adc_overrange_findings(read_frontend=lambda: far), [])

    def test_the_threshold_is_adjustable(self):
        self.assertEqual(
            smd._rx888_adc_overrange_findings(read_frontend=lambda: B4,
                                              quiet_sec=2000.0)[0].kind,
            "rx888-adc-overrange")


if __name__ == "__main__":
    unittest.main()

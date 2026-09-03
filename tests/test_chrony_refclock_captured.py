"""A local refclock must not capture the system clock unnoticed.

⛔ AC0G-ND, 2026-09-03.  The RX888 sampled ~350 ppm fast because its GPSDO's
27 MHz never reached the ADC.  hf-timestd's FUSE derives from those samples and
inherited the error; `trust` on the FUSE refclock then told chrony to believe it
over the network.  chrony obeyed, drove the clock 374 ppm fast, and marked all
three honest NTP servers falsetickers:

    #* FUSE             -174us
    ^x 144.202.66.214  -12.3s
    ^x 72.87.88.202    -12.2s
    ^x 172.234.37.140  -12.0s

The station sat TWELVE SECONDS off UTC while `chronyc tracking` reported
stratum 1 and a 380 us system-time error, and hf-timestd published T3 at -87 us.
radiod's GPS_TIME IS the host clock, so every RTP label drifted with it and no
decoder could align.  hf-timestd cannot catch this alone: it locks WWV's
ONE-SECOND tick, so it measures modulo one second and a multi-second error reads
as zero.

Frequency alone cannot discriminate — b4 legitimately runs 81 ppm slow
correcting its VM's crystal and is healthy.  What separates the stations is
DISAGREEMENT: independent network sources agreeing with each other and not with
the selected local refclock.  These tests hold that distinction, and in
particular hold that a healthy b4 stays silent.

`chronyc -c` output is injected, so these need no chrony and no root.
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
        "smd_chrony_under_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_chrony_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

# chronyc -c tracking: ref_id_hex, ref_id_name, stratum, ref_time, system_time,
# last_offset, rms_offset, frequency, residual_freq, skew, root_delay,
# root_dispersion, update_interval, leap_status
ND_TRACKING = ("46555345,FUSE,1,1788448224.0,0.000380781,-0.000418171,"
               "0.000298615,374.155,-0.695,11.880,0.001,0.001207114,32.0,Normal")
B4_TRACKING = ("46555345,FUSE,1,1788448224.0,0.000005794,-0.000005,"
               "0.000012,-81.075,0.010,0.103,0.001,0.001,32.0,Normal")

# chronyc -c sources: mode, state, name, stratum, poll, reach, last_rx,
# last_adjusted, last_measured, sample_error
ND_SOURCES = "\n".join([
    "#,*,FUSE,0,4,125,5,-0.000174,-0.000576,0.0015",
    "^,x,144.202.66.214,4,10,377,48,-12.3,-12.3,0.048",
    "^,x,72.87.88.202,2,9,375,407,-12.2,-12.2,0.135",
    "^,x,172.234.37.140,2,10,377,633,-12.0,-12.0,0.087",
])

B4_SOURCES = "\n".join([
    "#,*,FUSE,0,4,52,39,-0.000028,-0.000027,0.0006",
    "^,-,5.161.94.12,3,10,377,499,0.000596,0.000597,0.023",
    "^,-,199.85.8.46,2,10,377,165,0.000756,0.000754,0.010",
    "^,-,208.70.148.101,2,10,377,528,-0.001976,-0.001975,0.044",
    "^,-,50.205.57.38,1,10,377,134,-0.001442,-0.001440,0.017",
])


class ChronyCaptureTest(unittest.TestCase):

    def test_a_captured_clock_is_reported(self):
        f = smd._chrony_captured_findings(
            read_chrony=lambda: (ND_TRACKING, ND_SOURCES))
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].kind, "chrony-refclock-captured")
        d = f[0].detail
        self.assertIn("3 independent network time sources", d)
        self.assertIn("-12.200 s", d)          # the median, not one outlier
        self.assertIn("FUSE", d)
        self.assertIn("374.2 ppm", d)
        # The finding must explain why every other check reads healthy.
        self.assertIn("modulo one second", d)
        self.assertIn("GPS_TIME IS the host clock", d)
        self.assertIn("trust", d)
        self.assertFalse(f[0].fixable,
                         "stepping the clock disturbs every recorder")

    def test_a_healthy_b4_stays_silent(self):
        # b4 runs 81 ppm of genuine correction with its sources agreeing to
        # ~2 ms. Frequency magnitude alone must never trigger this.
        self.assertEqual(
            smd._chrony_captured_findings(
                read_chrony=lambda: (B4_TRACKING, B4_SOURCES)),
            [])

    def test_sources_that_disagree_with_each_other_prove_nothing(self):
        # Two servers that disagree wildly are a network problem, not a
        # captured clock. They must agree before their verdict counts.
        scattered = "\n".join([
            "#,*,FUSE,0,4,125,5,-0.000174,-0.000576,0.0015",
            "^,x,a,2,10,377,48,-12.3,-12.3,0.048",
            "^,x,b,2,10,377,48,4.1,4.1,0.048",
        ])
        self.assertEqual(
            smd._chrony_captured_findings(
                read_chrony=lambda: (ND_TRACKING, scattered)), [])

    def test_one_lone_network_source_is_not_enough(self):
        lone = "\n".join([
            "#,*,FUSE,0,4,125,5,-0.000174,-0.000576,0.0015",
            "^,x,144.202.66.214,4,10,377,48,-12.3,-12.3,0.048",
        ])
        self.assertEqual(
            smd._chrony_captured_findings(
                read_chrony=lambda: (ND_TRACKING, lone)), [])

    def test_unreachable_sources_are_ignored(self):
        # reach 0 means chrony has no measurement; absence of evidence must
        # not be reported as a fault.
        unreached = "\n".join([
            "#,*,FUSE,0,4,125,5,-0.000174,-0.000576,0.0015",
            "^,?,a,0,6,0,0,0.0,0.0,0.0",
            "^,?,b,0,6,0,0,0.0,0.0,0.0",
        ])
        self.assertEqual(
            smd._chrony_captured_findings(
                read_chrony=lambda: (ND_TRACKING, unreached)), [])

    def test_no_chrony_is_not_a_finding(self):
        for reader in (lambda: None, lambda: ('', ''), lambda: ('', 'garbage')):
            with self.subTest(reader=reader):
                self.assertEqual(
                    smd._chrony_captured_findings(read_chrony=reader), [])

    def test_a_host_without_chrony_is_silent(self):
        """The DEFAULT reader, on a box with no chronyc at all.

        It used to raise FileNotFoundError, which turned the whole family
        'unassessed' on every host without chrony — including this devbox.
        Absence of chrony is not a fault and not an unknown; it is nothing
        to check.
        """
        self.assertEqual(smd._chrony_captured_findings(), [])

    def test_the_disagreement_threshold_is_adjustable(self):
        # A sub-second disagreement is normal; the default must not fire on it.
        small = "\n".join([
            "#,*,FUSE,0,4,125,5,-0.000174,-0.000576,0.0015",
            "^,x,a,2,10,377,48,-0.30,-0.30,0.048",
            "^,x,b,2,10,377,48,-0.31,-0.31,0.048",
        ])
        self.assertEqual(
            smd._chrony_captured_findings(
                read_chrony=lambda: (ND_TRACKING, small)), [])
        self.assertEqual(
            len(smd._chrony_captured_findings(
                read_chrony=lambda: (ND_TRACKING, small),
                disagree_sec=0.1)), 1)


if __name__ == "__main__":
    unittest.main()

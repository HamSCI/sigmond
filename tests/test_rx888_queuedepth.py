"""An RX888 station must not run on radiod's default USB ring depth.

⛔ AC0G-ND, 2026-09-03.  radiod's built-in ring holds 16 URBs of 524288 B —
about 2.02 ms of 129.6 Msps each, so ~32 ms of tolerance before the ring
empties.  A KVM guest misses that deadline often enough to matter.  ND stalled
every ~13 minutes and lost a full day of WSPR and FT8 decodes; b4, which has
carried `queuedepth = 64` (~130 ms) by hand since the v3.31 soak, did not.

The device reports nothing when the ring runs dry, so the signature reads::

    RX888 RTP<->GPS offset STEPPED +1.000 s over 1.0 s
    (~129611155 samples missing; USB xfer failures +0; cumulative +102.831 s)
    RX888 measured sample rate: 0.0 Hz vs nominal 129600000.000000 Hz
    No rx888 data for 5 seconds, quitting

radiod then hangs in libusb teardown until sigmond-radiod-watchdog restarts it,
and every recorder loses its RTP anchor across that restart — which is how a
USB ring depth presents as "the station decodes nothing while timing looks
perfect".

The single-variable proof: b4's and ND's radiod configs differed in exactly
this one functional line.  b4 carried it by hand and the generator did not, so
the reference station and every greenfield station disagreed silently — the
same shape as the rx888-irq override.  These tests hold both halves: the
generator emits the setting, and the doctor finds the stations built before it
did.

Everything here reads injected text, so it needs no /etc/radio, no USB bus and
no root.
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

from sigmond.commands import radiod_config  # noqa: E402


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_queuedepth_under_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_queuedepth_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

_ND_BEFORE = """\
[global]
hardware  = rx888
status    = AC0G-ND-status.local

[rx888]
device      = "rx888"
description = "AC0G/ND RX-888 Mk2"
samprate    = 129600000
gainmode    = high
"""

_B4 = """\
[global]
hardware  = rx888

[rx888]
device      = "rx888"
# USB URB ring: 64 x ~2 ms = ~130 ms stall tolerance (v3.31 soak; default 16)
queuedepth  = 64
samprate    = 129600000
"""


class GeneratorEmitsQueuedepthTest(unittest.TestCase):
    """The generator, not the operator, must carry the soak-proven value."""

    def test_all_rx888_labels_emit_64(self):
        for label in ("RX888", "RX-888", "RX-888 Mk2"):
            with self.subTest(frontend=label):
                self.assertIn(
                    "queuedepth  = 64",
                    radiod_config._profile_for(label)["defaults"])

    def test_other_front_ends_do_not_get_an_rx888_knob(self):
        for label in ("Airspy", "Airspy HF+", "SDRplay", "MysteryRadio"):
            with self.subTest(frontend=label):
                self.assertNotIn(
                    "queuedepth",
                    radiod_config._profile_for(label)["defaults"])


class DoctorSeesQueuedepthDriftTest(unittest.TestCase):
    """Stations installed before the generator emitted it still run the cliff."""

    def test_missing_queuedepth_is_reported(self):
        f = smd._rx888_queuedepth_findings(
            read_configs=lambda: {"/etc/radio/radiod@AC0G-ND.conf": _ND_BEFORE})
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].kind, "rx888-queuedepth-default")
        self.assertIn("radiod@AC0G-ND.conf", f[0].detail)
        # The finding must name the mechanism and the remedy, not just the fact.
        self.assertIn("32 ms", f[0].detail)
        self.assertIn("USB xfer failures +0", f[0].detail)
        self.assertIn("queuedepth  = 64", f[0].detail)
        self.assertFalse(f[0].fixable, "doctor must not restart a receiver")

    def test_a_station_carrying_the_setting_is_silent(self):
        self.assertEqual(
            smd._rx888_queuedepth_findings(
                read_configs=lambda: {"/etc/radio/radiod@AC0G-B4.conf": _B4}),
            [])

    def test_non_rx888_front_end_is_silent(self):
        # queuedepth names an RX888 driver knob; an Airspy station is not drifting.
        airspy = _ND_BEFORE.replace("[rx888]", "[airspy]").replace(
            "hardware  = rx888", "hardware  = airspy")
        self.assertEqual(
            smd._rx888_queuedepth_findings(
                read_configs=lambda: {"/etc/radio/radiod@lab.conf": airspy}),
            [])

    def test_a_commented_out_setting_does_not_count(self):
        # The operator who left it commented is still running the default.
        commented = _ND_BEFORE.replace(
            "gainmode    = high", "gainmode    = high\n# queuedepth  = 64")
        f = smd._rx888_queuedepth_findings(
            read_configs=lambda: {"/etc/radio/radiod@AC0G-ND.conf": commented})
        self.assertEqual(len(f), 1)

    def test_no_configs_is_not_a_finding(self):
        # Absence of evidence must not be reported as a fault.
        self.assertEqual(
            smd._rx888_queuedepth_findings(read_configs=lambda: {}), [])

    def test_every_rx888_instance_is_reported_separately(self):
        f = smd._rx888_queuedepth_findings(read_configs=lambda: {
            "/etc/radio/radiod@one.conf": _ND_BEFORE,
            "/etc/radio/radiod@two.conf": _ND_BEFORE,
            "/etc/radio/radiod@three.conf": _B4,
        })
        self.assertEqual(len(f), 2)
        self.assertEqual({"one", "two"},
                         {p.detail.split("radiod@")[1].split(".conf")[0]
                          for p in f})


if __name__ == "__main__":
    unittest.main()

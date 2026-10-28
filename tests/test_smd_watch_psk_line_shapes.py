"""`smd watch psk` must read both psk-recorder spot-line shapes (decode_ft8
and jt9), and `smd watch wspr` must not relay the wsprnet transport's
async-API diagnostics.  Regression for KJ6MKI 2026-10-28: under
decoder_kind="jt9" every FT8/FT4 spot rendered as 160m with dt=-0.65."""
import importlib.machinery
import importlib.util
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_watch_psk_line_shapes", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

DECODE_FT8_LINE = "2026/10/28 02:02:45  30 +0.43 7,048,250.1 ~ N2RSI K4XL +08"
JT9_FT8_LINE = "261028 151200 18100000 0 -1 0.4 1273 ~ N1MRG HA7TM R-06 FT8"
JT9_FT4_LINE = "261028 111507 7047500 0 13 0.5 1074 ~ KC5JEC W8ATE R+00 FT4"


class PskSpotFieldsTests(unittest.TestCase):

    def test_decode_ft8_shape(self):
        t, hz, dt, is_jt9 = smd._psk_spot_fields(DECODE_FT8_LINE)
        self.assertEqual(t, "02:02:45")
        self.assertAlmostEqual(hz, 7_048_250.1)
        self.assertAlmostEqual(dt, 0.43)
        self.assertFalse(is_jt9)

    def test_jt9_shape_ft8(self):
        t, hz, dt, is_jt9 = smd._psk_spot_fields(JT9_FT8_LINE)
        self.assertEqual(t, "15:12:00")
        self.assertAlmostEqual(hz, 18_101_273.0)   # dial + audio offset
        self.assertAlmostEqual(dt, 0.4)
        self.assertTrue(is_jt9)

    def test_jt9_shape_ft4(self):
        t, hz, dt, is_jt9 = smd._psk_spot_fields(JT9_FT4_LINE)
        self.assertEqual(t, "11:15:07")
        self.assertAlmostEqual(hz, 7_048_574.0)
        self.assertTrue(is_jt9)

    def test_garbage_is_none(self):
        self.assertIsNone(smd._psk_spot_fields("#TIMINGFAULT anchor walked"))
        self.assertIsNone(smd._psk_spot_fields(""))
        self.assertIsNone(smd._psk_spot_fields("261028 151200 notanumber 0 -1 0.4 1273 ~ X"))


class WsprWatchHelpersTests(unittest.TestCase):

    def test_async_diagnostics_dropped(self):
        raw = ("53/53 added [async status=done, 1000ms, "
               "nonce 8e0a548c90d04f619a05a53d08cd33d5]")
        self.assertEqual(smd._wsprnet_post_server_summary(raw), "53/53 added")

    def test_rejected_count_kept(self):
        raw = "61/62 added, 1 rejected [async status=done, 1000ms, nonce ee51]"
        self.assertEqual(smd._wsprnet_post_server_summary(raw),
                         "61/62 added, 1 rejected")

    def test_legacy_tail_untouched(self):
        self.assertEqual(smd._wsprnet_post_server_summary("49/49 added"),
                         "49/49 added")

    def test_band_labels(self):
        self.assertEqual(smd._wspr_band_label("20"), "20m")
        self.assertEqual(smd._wspr_band_label("2200"), "2200m")
        self.assertEqual(smd._wspr_band_label("60eu"), "60eu")
        self.assertEqual(smd._wspr_band_label("80eu"), "80eu")


if __name__ == "__main__":
    unittest.main()


class WsprPostCleanTests(unittest.TestCase):
    """Default `smd watch wspr` hides POST rows that say 'all added'."""

    def test_all_added_is_clean(self):
        self.assertTrue(smd._wsprnet_post_is_clean("59/59 added"))

    def test_short_count_is_not_clean(self):
        self.assertFalse(smd._wsprnet_post_is_clean("61/62 added, 1 rejected"))
        self.assertFalse(smd._wsprnet_post_is_clean("0/62 added"))

    def test_unexpected_text_is_not_clean(self):
        self.assertFalse(smd._wsprnet_post_is_clean("HTTP 503"))
        self.assertFalse(smd._wsprnet_post_is_clean(""))

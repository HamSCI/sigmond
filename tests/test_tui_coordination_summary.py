"""The Configuration screen's one-line coordination summary.

It once read `coord.station` as a dict; the loader returns a dataclass, so
`smd tui` crashed before drawing on any station with a `[station]` block
(AC0G-B4, 2026-10-14).
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

try:
    from sigmond.tui.screens import configuration
except ImportError:          # textual absent: nothing to test
    configuration = None

from sigmond.coordination import load_coordination

B4_TOML = """
[host]
call = "AC0G"
grid = "EM38ww"

[station]
psws_id = "S000170"
reporter_id = "AC0G=B4"

[timing_authority]
source = "hf-timestd"

[radiod."AC0G-B4-status.local"]
host = "localhost"
"""


@unittest.skipIf(configuration is None, "textual not installed")
class CoordinationSummaryTests(unittest.TestCase):

    def summary(self, toml_text):
        with TemporaryDirectory() as d:
            p = Path(d) / "coordination.toml"
            p.write_text(toml_text)
            coord = load_coordination(p)
        with mock.patch("sigmond.coordination.load_coordination",
                        return_value=coord):
            return configuration._coordination_summary()

    def test_a_station_block_does_not_crash_and_shows_call_and_grid(self):
        text = self.summary(B4_TOML)
        self.assertIn("AC0G", text)
        self.assertIn("EM38ww", text)

    def test_the_reporter_id_shows_in_display_form(self):
        text = self.summary(B4_TOML)
        self.assertIn("AC0G/B4", text)
        self.assertNotIn("AC0G=B4", text)

    def test_the_timing_authority_comes_from_its_real_field(self):
        self.assertIn("hf-timestd", self.summary(B4_TOML))

    def test_an_empty_coordination_still_renders(self):
        text = self.summary("")
        self.assertIsInstance(text, str)
        self.assertTrue(text)


if __name__ == "__main__":
    unittest.main()

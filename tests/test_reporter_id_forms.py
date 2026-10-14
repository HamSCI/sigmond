"""The three spellings of a reporter id, and the helpers that convert them
(tasks/spec-reporter-id-v372.md §2-§3).

    display   AC0G/B4      what a person types and reads
    storage   AC0G=B4      file names, directory names, config keys
    systemd   AC0G\\x3dB4  unit names, *.wants links, %i

tests/data/reporter_id_vectors.json holds the cases every copy of these
rules must agree on.  psk-recorder, meteor-scatter and wspr-recorder each
carry a byte-identical copy; tests/test_reporter_id_drift.py compares them.
"""
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond.instance import (  # noqa: E402
    InvalidReporterId,
    display_reporter_id,
    display_unit_name,
    legacy_instance_hint,
    parse_user_reporter_id,
    systemd_escape,
    systemd_instance,
    unescape_systemd_instance,
    unit_name,
)

VECTORS = json.loads(
    (REPO / "tests" / "data" / "reporter_id_vectors.json").read_text())


class VectorTableTests(unittest.TestCase):
    """instance.py against the shared table."""

    def test_reporter_ids_convert_to_every_form(self):
        for v in VECTORS["reporter_ids"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(parse_user_reporter_id(v["input"]), v["storage"])
                self.assertEqual(systemd_instance(v["storage"]), v["systemd"])
                self.assertEqual(unit_name("psk-recorder", v["storage"]),
                                 f"psk-recorder@{v['systemd']}.service")
                self.assertEqual(unescape_systemd_instance(v["systemd"]), v["storage"])
                self.assertEqual(display_reporter_id(v["storage"]), v["display"])
                self.assertEqual(display_reporter_id(v["systemd"]), v["display"])

    def test_unit_names_escape_only_what_systemd_must(self):
        for v in VECTORS["escape_only"]:
            with self.subTest(name=v["name"]):
                self.assertEqual(systemd_escape(v["name"]), v["systemd"])

    def test_invalid_text_is_refused(self):
        for text in VECTORS["invalid"]:
            with self.subTest(text=text):
                with self.assertRaises(InvalidReporterId):
                    parse_user_reporter_id(text)

    def test_display_never_shows_an_escape(self):
        for v in VECTORS["display_only"]:
            with self.subTest(input=v["input"]):
                self.assertEqual(display_reporter_id(v["input"]), v["display"])


class HelperTests(unittest.TestCase):

    def test_hyphens_stay_hyphens_toward_systemd(self):
        # systemd-escape(1) would write W1ABC\x2d5: a unit nobody enabled.
        self.assertEqual(systemd_instance("W1ABC-5"), "W1ABC-5")
        self.assertEqual(systemd_instance("KP4MD-RPI-4"), "KP4MD-RPI-4")
        self.assertEqual(unit_name("wspr-recorder", "W1ABC-5"),
                         "wspr-recorder@W1ABC-5.service")

    def test_systemd_instance_takes_only_the_storage_form(self):
        for text in ("AC0G/B4", "AC0G\\x3dB4", "ac0g=b4", "my-rx888"):
            with self.subTest(text=text), self.assertRaises(InvalidReporterId):
                systemd_instance(text)

    def test_escape_undoes_exactly_one_unescape(self):
        self.assertEqual(systemd_escape("AC0G=B4"), "AC0G\\x3dB4")
        # A double-escaped orphan keeps its own name through a round trip,
        # so it can never be mistaken for the live unit.
        orphan = "AC0G\\x5cx3dB4"
        self.assertEqual(systemd_escape(unescape_systemd_instance(orphan)), orphan)

    def test_unit_name_takes_a_suffix(self):
        self.assertEqual(unit_name("wspr-recorder", "AC0G=B4", suffix=".timer"),
                         "wspr-recorder@AC0G\\x3dB4.timer")

    def test_display_unit_name(self):
        cases = {
            "psk-recorder@AC0G\\x3dB4.service": "psk-recorder@AC0G/B4.service",
            "psk-recorder@AC0G=B4.service": "psk-recorder@AC0G/B4.service",
            "psk-recorder@AC0G\\x3dB4": "psk-recorder@AC0G/B4",
            "wspr-recorder@W1ABC-5.service": "wspr-recorder@W1ABC-5.service",
            "radiod@ac0g-b4.service": "radiod@ac0g-b4.service",
            "codar-sounder@bee1-status.local.service":
                "codar-sounder@bee1-status.local.service",
            "wspr-recorder@*": "wspr-recorder@*",
            "hf-timestd.service": "hf-timestd.service",
        }
        for unit, want in cases.items():
            with self.subTest(unit=unit):
                self.assertEqual(display_unit_name(unit), want)

    def test_lower_and_mixed_case_reach_the_upper_case_instance(self):
        # Review Focus 1: reporter ids are upper case only.  No guess at a
        # legacy name: that is a fallback the watch commands make (Task 5).
        for text in ("w1abc-5", "W1abc-5", "w1abc\\x2d5"):
            with self.subTest(text=text):
                self.assertEqual(parse_user_reporter_id(text), "W1ABC-5")
        self.assertEqual(unit_name("psk-recorder", parse_user_reporter_id("ac0g/b4")),
                         "psk-recorder@AC0G\\x3dB4.service")

    def test_legacy_instance_hint(self):
        self.assertEqual(legacy_instance_hint("my-rx888"),
                         "legacy instance name my-rx888; run: smd admin instance migrate")


if __name__ == "__main__":
    unittest.main()

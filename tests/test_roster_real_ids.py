"""The roster carries REAL PSWS ids, and one instrument per recorder.

⛔ Two things this file pins, both of which were wrong before 2026-09-02.

**One instrument was never enough.** A DASI2 site has a GRAPE/HF instrument AND
a magnetometer instrument under one station id — Michael's roster gives both
(DASI002 = station S000422, RX888 370, mag 371).  `StationIdentity` modelled a
single `psws_instrument`, which silently had no room for the second.  Worse, it
invented a narrower shape than the one `sigmond.site_profile` had already
settled on: a per-recorder `psws_instruments` map keyed by client name, with
`instrument_for(recorder)` to read it.  The roster now speaks that same
vocabulary, so there is one model of PSWS identity in this codebase rather than
two that disagree.

**The placeholders are gone.**  Until the real ids landed, this file shipped
`S0002NN` values in the same namespace as real ones, guarded only by a
`[_meta] placeholder = true` sentinel.  Both the placeholders and the sentinel
had to go in the same commit — a stale sentinel warns forever, and a stale
placeholder reaches real config.
"""
import unittest

from sigmond.station_identity import (
    DEFAULT_ROSTER,
    StationIdentity,
    UnrosteredDasiName,
    identify,
    load_roster,
)


class ShippedRoster(unittest.TestCase):
    def setUp(self):
        self.roster = load_roster(DEFAULT_ROSTER)

    def test_the_real_stations_are_present_with_their_real_ids(self):
        """Michael's table, 2026-09-02.  Station id, then the two instruments."""
        expected = {
            "DASI002": ("S000422", "370", "371"),
            "DASI003": ("S000468", "380", "381"),
            "DASI004": ("S000469", "382", "383"),
            "DASI005": ("S000470", "384", "385"),
        }
        for host, (station, hf, mag) in expected.items():
            with self.subTest(host=host):
                entry = self.roster[host]
                self.assertEqual(entry["psws_station"], station)
                self.assertEqual(entry["psws_instruments"]["hf-timestd"], hf)
                self.assertEqual(entry["psws_instruments"]["mag-recorder"], mag)

    def test_no_placeholder_ids_survive(self):
        """The placeholders sat in the same S0002NN namespace as real ids and
        nothing but a sentinel could tell them apart."""
        for host, entry in self.roster.items():
            with self.subTest(host=host):
                self.assertNotRegex(entry["psws_station"], r"^S0002\d\d$")

    def test_the_placeholder_sentinel_is_gone(self):
        """A sentinel left behind after the real ids land warns forever, and a
        warning that is always wrong is one operators learn to ignore."""
        text = DEFAULT_ROSTER.read_text()
        self.assertNotIn("_meta", text)
        self.assertNotIn("placeholder", text.lower())

    def test_only_the_stations_michael_defined_are_rostered(self):
        """DASI001 and DASI006+ are deliberately ABSENT.  A machine carrying a
        DASI name we have no ids for must be refused, not handed a guess — that
        refusal is the whole reason the roster is a closed list."""
        self.assertEqual(set(self.roster),
                         {"DASI002", "DASI003", "DASI004", "DASI005"})


class IdentityFromRoster(unittest.TestCase):
    def test_a_rostered_host_carries_both_instruments(self):
        ident = identify("DASI002")
        self.assertTrue(ident.dasi2_site)
        self.assertEqual(ident.psws_station, "S000422")
        self.assertEqual(ident.instrument_for("hf-timestd"), "370")
        self.assertEqual(ident.instrument_for("mag-recorder"), "371")

    def test_an_unknown_recorder_has_no_instrument(self):
        """Absent, not invented: a station with no magnetometer instrument must
        report nothing rather than fall back to the GRAPE id and upload
        magnetometer data under the HF instrument."""
        self.assertEqual(identify("DASI002").instrument_for("psk-recorder"), "")

    def test_an_ordinary_station_has_no_instruments(self):
        ident = identify("fargo-1")
        self.assertFalse(ident.dasi2_site)
        self.assertEqual(ident.instrument_for("hf-timestd"), "")

    def test_a_dasi_name_we_have_no_ids_for_is_still_refused(self):
        with self.assertRaises(UnrosteredDasiName):
            identify("DASI001")
        with self.assertRaises(UnrosteredDasiName):
            identify("DASI017")


class InstrumentAccessor(unittest.TestCase):
    def test_it_matches_site_profile_vocabulary(self):
        """`instrument_for(recorder)` is the name `sigmond.site_profile`
        already uses; the roster must not invent a second spelling."""
        from sigmond.site_profile import SiteProfile
        self.assertTrue(hasattr(SiteProfile, "instrument_for"))
        self.assertTrue(hasattr(StationIdentity, "instrument_for"))


if __name__ == "__main__":
    unittest.main()

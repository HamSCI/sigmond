"""Tests for sigmond.site_profile + the PSWS push planner (Phase 2:
one-file identity for the golden-image model)."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sigmond.site_profile import TEMPLATE, load_site_profile
from sigmond.commands.config import plan_psws_updates, _patch_heartbeat_block
from sigmond.coordination import Heartbeat, load_coordination


def _write_profile(td: str, body: str) -> Path:
    p = Path(td) / "site-profile.toml"
    p.write_text(body)
    return p


FULL_PROFILE = """\
schema_version = 1

[station]
callsign    = "ac0g"
grid_square = "EM38ww"
latitude    = 38.93
longitude   = -92.33

[psws]
enabled       = true
station_id    = "S000418"
instrument_id = "367"

[psws.instruments]
"hf-timestd"   = "367"
"mag-recorder" = "RM3100"

[reporters]
reporter_id      = "ac0g/s"
wsprnet_call     = ""
pskreporter_call = ""
"""


class TestLoadSiteProfile(unittest.TestCase):

    def test_missing_file_returns_none(self):
        with TemporaryDirectory() as td:
            self.assertIsNone(load_site_profile(Path(td) / "nope.toml"))

    def test_template_parses_with_placeholders_cleaned(self):
        with TemporaryDirectory() as td:
            sp = load_site_profile(_write_profile(td, TEMPLATE))
        self.assertEqual(sp.call, "")          # <YOUR_CALL> cleaned
        self.assertEqual(sp.grid, "")
        self.assertFalse(sp.psws_enabled)
        self.assertEqual(sp.psws_instruments, {})
        self.assertEqual(sp.reporter_id, "")

    def test_full_profile_fields(self):
        with TemporaryDirectory() as td:
            sp = load_site_profile(_write_profile(td, FULL_PROFILE))
        self.assertEqual(sp.call, "AC0G")      # upcased
        self.assertEqual(sp.reporter_id, "AC0G/S")
        self.assertEqual(sp.effective_reporter_id, "AC0G/S")
        self.assertEqual(sp.psws_station_id, "S000418")
        self.assertEqual(sp.instrument_for("hf-timestd"), "367")
        self.assertEqual(sp.instrument_for("mag-recorder"), "RM3100")

    def test_reporter_id_defaults_to_callsign(self):
        with TemporaryDirectory() as td:
            sp = load_site_profile(_write_profile(td, """\
[station]
callsign = "AC0G"
"""))
        self.assertEqual(sp.effective_reporter_id, "AC0G")

    def test_legacy_single_instrument_id_is_hf_timestd(self):
        with TemporaryDirectory() as td:
            sp = load_site_profile(_write_profile(td, """\
[psws]
enabled       = true
station_id    = "S000418"
instrument_id = "172"
"""))
        self.assertEqual(sp.instrument_for("hf-timestd"), "172")
        # No map, no legacy claim for mag — stays empty (its own config
        # template default applies).
        self.assertEqual(sp.instrument_for("mag-recorder"), "")

    def test_instruments_map_wins_over_legacy(self):
        with TemporaryDirectory() as td:
            sp = load_site_profile(_write_profile(td, """\
[psws]
enabled       = true
station_id    = "S000418"
instrument_id = "172"

[psws.instruments]
"hf-timestd" = "367"
"""))
        self.assertEqual(sp.instrument_for("hf-timestd"), "367")

    def test_template_heartbeat_is_undeclared(self):
        """The TEMPLATE's [heartbeat] block is fully commented out (no
        live [heartbeat] table at all) — an operator must uncomment it to
        opt in, so a freshly-scaffolded profile declares nothing."""
        with TemporaryDirectory() as td:
            sp = load_site_profile(_write_profile(td, TEMPLATE))
        self.assertFalse(sp.heartbeat_declared)

    def test_heartbeat_block_parses(self):
        with TemporaryDirectory() as td:
            sp = load_site_profile(_write_profile(td, """\
[station]
callsign = "AC0G"

[heartbeat]
enabled      = true
station      = "AC0G-B4"
host         = "wd30.wsprdaemon.org"
port         = 38222
sftp_user    = "hamsci-hb"
remote_path  = "incoming"
interval_sec = 300
"""))
        self.assertTrue(sp.heartbeat_declared)
        self.assertTrue(sp.heartbeat_enabled)
        self.assertEqual(sp.heartbeat_station, "AC0G-B4")
        self.assertEqual(sp.heartbeat_host, "wd30.wsprdaemon.org")
        self.assertEqual(sp.heartbeat_port, 38222)
        self.assertEqual(sp.heartbeat_sftp_user, "hamsci-hb")
        self.assertEqual(sp.heartbeat_remote_path, "incoming")
        self.assertEqual(sp.heartbeat_interval_sec, 300)

    def test_heartbeat_block_absent_is_not_declared(self):
        with TemporaryDirectory() as td:
            sp = load_site_profile(_write_profile(td, """\
[station]
callsign = "AC0G"
"""))
        self.assertFalse(sp.heartbeat_declared)
        self.assertFalse(sp.heartbeat_enabled)
        self.assertEqual(sp.heartbeat_port, 22)
        self.assertEqual(sp.heartbeat_sftp_user, "hamsci-hb")
        self.assertEqual(sp.heartbeat_remote_path, "incoming")
        self.assertEqual(sp.heartbeat_interval_sec, 300)

    def test_heartbeat_station_defaults_to_effective_reporter_id_when_blank(self):
        with TemporaryDirectory() as td:
            sp = load_site_profile(_write_profile(td, """\
[station]
callsign = "AC0G"

[reporters]
reporter_id = "AC0G/B4"

[heartbeat]
enabled = true
"""))
        self.assertEqual(sp.heartbeat_station, "")   # raw field IS blank
        self.assertEqual(sp.effective_heartbeat_station, "AC0G/B4")


class TestPatchHeartbeatBlock(unittest.TestCase):
    """`_patch_heartbeat_block` — the coordination.toml [heartbeat] writer
    `smd config render` calls when site-profile.toml declares one. Mirrors
    _patch_station_block: rewrite the block in place, preserve everything
    else verbatim, copy the fixed keys through field-for-field."""

    def _tmp(self, text=""):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "coordination.toml"
        if text:
            p.write_text(text)
        return p

    def test_writes_all_fields_on_empty_file(self):
        p = self._tmp()
        hb = Heartbeat(enabled=True, station="AC0G-B4",
                       host="wd30.wsprdaemon.org", port=38222,
                       sftp_user="hamsci-hb", remote_path="incoming",
                       interval_sec=300)
        _patch_heartbeat_block(p, hb)
        coord = load_coordination(p)
        self.assertTrue(coord.heartbeat.enabled)
        self.assertEqual(coord.heartbeat.station, "AC0G-B4")
        self.assertEqual(coord.heartbeat.host, "wd30.wsprdaemon.org")
        self.assertEqual(coord.heartbeat.port, 38222)
        self.assertEqual(coord.heartbeat.interval_sec, 300)

    def test_preserves_other_blocks(self):
        p = self._tmp('[host]\ncall = "AC0G"\ngrid = "EM38ww"\n')
        _patch_heartbeat_block(p, Heartbeat(enabled=True, station="AC0G-B4"))
        coord = load_coordination(p)
        self.assertEqual(coord.host.call, "AC0G")
        self.assertTrue(coord.heartbeat.enabled)

    def test_replaces_existing_heartbeat_block_wholesale(self):
        p = self._tmp('[heartbeat]\nenabled = false\nstation = "OLD"\n')
        _patch_heartbeat_block(p, Heartbeat(enabled=True, station="NEW"))
        coord = load_coordination(p)
        self.assertTrue(coord.heartbeat.enabled)
        self.assertEqual(coord.heartbeat.station, "NEW")


class _FakeState:
    def __init__(self, station="", instrument=""):
        self.station = station
        self.instrument = instrument
        self.config_exists = True


class TestPlanPswsUpdates(unittest.TestCase):

    def _profile(self, body=FULL_PROFILE):
        with TemporaryDirectory() as td:
            return load_site_profile(_write_profile(td, body))

    def test_unset_recorder_gets_both_fields(self):
        sp = self._profile()
        updates = plan_psws_updates(sp, "hf-timestd", _FakeState())
        self.assertEqual(updates, [
            ("station", "id", "S000418"),
            ("station", "instrument_id", "367"),
        ])

    def test_mag_uses_its_own_section_keys(self):
        sp = self._profile()
        updates = plan_psws_updates(sp, "mag-recorder", _FakeState())
        self.assertEqual(updates, [
            ("station", "psws_station_id", "S000418"),
            ("station", "instrument_id", "RM3100"),
        ])

    def test_current_recorder_yields_no_updates(self):
        sp = self._profile()
        st = _FakeState(station="S000418", instrument="367")
        self.assertEqual(plan_psws_updates(sp, "hf-timestd", st), [])

    def test_empty_profile_value_never_clobbers(self):
        sp = self._profile("""\
[psws]
enabled    = true
station_id = "S000418"
""")
        # mag has no instrument id anywhere in this profile; a
        # hand-configured value must survive.
        st = _FakeState(station="S000418", instrument="RM3100-custom")
        self.assertEqual(plan_psws_updates(sp, "mag-recorder", st), [])

    def test_changed_id_is_updated(self):
        sp = self._profile()
        st = _FakeState(station="S000001", instrument="367")
        updates = plan_psws_updates(sp, "hf-timestd", st)
        self.assertEqual(updates, [("station", "id", "S000418")])


if __name__ == "__main__":
    unittest.main()

def test_psws_station_override_per_recorder(tmp_path):
    """[psws.stations] gives a recorder its own portal station (AC0G mag:
    S000082/84 vs site S000170)."""
    p = tmp_path / "site-profile.toml"
    p.write_text(
        '[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n'
        '[psws]\nenabled = true\nstation_id = "S000170"\n'
        '[psws.instruments]\n"mag-recorder" = "84"\n'
        '[psws.stations]\n"mag-recorder" = "S000082"\n'
    )
    sp = load_site_profile(p)
    assert sp.station_for("mag-recorder") == "S000082"
    assert sp.station_for("hf-timestd") == "S000170"
    assert sp.instrument_for("mag-recorder") == "84"

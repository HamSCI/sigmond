"""One definition of the STATION_* identity bag, read by both consumers.

⛔ Why this check exists.  AC0G-ND, 2026-09-04.  The station is enrolled with
PSWS (S000111, instrument 115) and `/etc/sigmond/coordination.env` carried every
field, yet `smd config init hamsci-physics` wrote empty callsign and empty PSWS
ids and reported "no PSWS station id — science runs locally, GRAPE upload is
skipped."

Two independent reconstructions of the same bag had drifted apart:

  * `coordination.render_env()` writes coordination.env and emits the full set —
    STATION_CALL, STATION_CALLSIGN (deliberately, "so every client auto-seeds
    the callsign regardless of which name it expects"), STATION_GRID, LAT, LON,
    plus the station block: REPORTER_ID, PSWS_STATION_ID, PSWS_INSTRUMENT_ID,
    WSPRNET_CALL, PSKREPORTER_CALL.

  * `commands.client_config._build_env_bag()` rebuilds the bag for
    `smd config init|edit` straight from `coord.host` and emitted only CALL,
    GRID, LAT, LON.  It never read `coord.station` at all.

So the identity a client's own wizard saw depended on which code path reached
it, and the path that configures GRAPE saw the smaller one.

`station_identity_env()` is now the single definition.  These tests hold that
both consumers use it, so a field added to one cannot go missing from the other.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import coordination as coordmod  # noqa: E402


def _coord():
    """A coordination object shaped like AC0G-ND's."""
    c = coordmod.Coordination()
    c.host.call = "AC0G"
    c.host.grid = "EN16ov"
    c.host.lat = 46.9071215
    c.host.lon = -96.7926047
    c.station.reporter_id = "AC0G/ND"
    c.station.psws_id = "S000111"
    c.station.instrument_id = "115"
    c.station.wsprnet_call = "AC0G"
    c.station.pskreporter_call = "AC0G"
    return c


class TheIdentityBagIsDefinedOnce(unittest.TestCase):

    def test_it_carries_the_psws_ids(self):
        """The ND regression: GRAPE could never learn where to upload."""
        env = coordmod.station_identity_env(_coord())
        self.assertEqual(env["STATION_PSWS_STATION_ID"], "S000111")
        self.assertEqual(env["STATION_PSWS_INSTRUMENT_ID"], "115")

    def test_it_carries_both_callsign_spellings(self):
        env = coordmod.station_identity_env(_coord())
        self.assertEqual(env["STATION_CALL"], "AC0G")
        self.assertEqual(env["STATION_CALLSIGN"], "AC0G")

    def test_absent_fields_are_omitted_not_blanked(self):
        """An empty value would overwrite a client's existing config with ''."""
        c = coordmod.Coordination()
        c.host.call = "AC0G"
        env = coordmod.station_identity_env(c)
        self.assertEqual(env["STATION_CALL"], "AC0G")
        for absent in ("STATION_GRID", "STATION_PSWS_STATION_ID",
                       "STATION_PSWS_INSTRUMENT_ID", "STATION_REPORTER_ID"):
            self.assertNotIn(absent, env)

    def test_render_env_publishes_every_name_the_helper_defines(self):
        """coordination.env is one consumer."""
        coord = _coord()
        env = coordmod.station_identity_env(coord)
        text = coordmod.render_env(coord, passthrough_lookup=lambda _t: [])
        emitted = {l.split("=", 1)[0] for l in text.splitlines()
                   if l.startswith("STATION_")}
        self.assertEqual(set(env) - emitted, set(),
                         "render_env dropped a name the identity bag defines")

    def test_config_init_publishes_every_name_the_helper_defines(self):
        """`smd config init` is the other consumer — the one that regressed."""
        from sigmond.commands import client_config
        coord = _coord()
        env = coordmod.station_identity_env(coord)
        import unittest.mock as mock
        with mock.patch.object(client_config, "load_coordination",
                               return_value=coord):
            bag = client_config._build_env_bag(client="hamsci-physics")
        missing = {k: v for k, v in env.items() if bag.get(k) != v}
        self.assertEqual(missing, {},
                         "_build_env_bag dropped identity the client needs — "
                         "this is exactly how ND's GRAPE upload stayed unset")

    def test_config_init_still_carries_the_radiod_runtime_vars(self):
        from sigmond.commands import client_config
        import unittest.mock as mock
        with mock.patch.object(client_config, "load_coordination",
                               return_value=_coord()):
            bag = client_config._build_env_bag(client="hamsci-physics")
        self.assertIn("SIGMOND_RADIOD_COUNT", bag)


if __name__ == "__main__":
    unittest.main()

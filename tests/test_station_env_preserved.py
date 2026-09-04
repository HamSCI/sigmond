"""Every STATION_* var sigmond publishes must survive the sudo boundary.

⛔ Why this check exists.  AC0G-ND, 2026-09-04.  The station is enrolled with
PSWS — `smd psws status` reports station S000111, hf-timestd instrument 115,
key verified — and `/etc/sigmond/coordination.env` carries all of it:

    STATION_CALL=AC0G
    STATION_CALLSIGN=AC0G
    STATION_GRID=EN16ov
    STATION_PSWS_STATION_ID=S000111
    STATION_PSWS_INSTRUMENT_ID=115

`smd config init hamsci-physics` nevertheless wrote:

    callsign         = ""
    grid_square      = "EN16ov"
    psws_station_id  = ""
    instrument_id    = ""
    note: no PSWS station id — science runs locally, GRAPE upload is skipped.

One field of four landed.  That single success is the tell: `setup-station.sh`
reads `STATION_GRID`, `STATION_CALLSIGN`, `STATION_PSWS_STATION_ID` and
`STATION_PSWS_INSTRUMENT_ID`, and only the first appeared on the hand-written
`sudo --preserve-env` allowlist here.  sudo's env_reset stripped the other
three between coordination.env and the client's own wizard.

So a fully enrolled station computed GRAPE and could never upload it, and
nothing reported a fault — the note about skipping the upload reads like a
choice rather than a stripped variable.

`coordination.py` already emits `STATION_CALLSIGN` beside `STATION_CALL`
specifically "so every client auto-seeds the callsign regardless of which name
it expects".  That intent died at this allowlist.

## The fix, and why it is not another list

Replacing one hand-maintained list of names with a longer hand-maintained list
would reproduce the same drift the next time coordination.py learns a field —
the shape that also gave us the topology/profile drift and the stale rig
scripts.  The allowlist now derives from the contract bag itself: every
STATION_* key that coordination.env actually defines is preserved, plus the
SIGMOND_* runtime vars.

That keeps the original safety property the comment argued for.  Blanket
`--preserve-env` would forward the invoking operator's whole shell; this
forwards only names sigmond itself published in a root-owned file.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import installer  # noqa: E402

COORD = """\
# sigmond coordination bag
STATION_CALL=AC0G
STATION_CALLSIGN=AC0G
STATION_GRID=EN16ov
STATION_LAT=46.9071215
STATION_LON=-96.7926047
STATION_REPORTER_ID=AC0G/ND
STATION_PSWS_STATION_ID=S000111
STATION_PSWS_INSTRUMENT_ID=115
STATION_WSPRNET_CALL=AC0G
"""


class PreserveList(unittest.TestCase):
    """What sudo is told to keep."""

    def setUp(self):
        self._tmp = Path(__import__("tempfile").mkdtemp())
        self.coord = self._tmp / "coordination.env"
        self.coord.write_text(COORD)

    def tearDown(self):
        __import__("shutil").rmtree(self._tmp, ignore_errors=True)

    def _preserve(self):
        """Return the --preserve-env names run_install_script would pass."""
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw.get("env") or {}
            class R:  # noqa: E306
                returncode = 0
            return R()

        entry = type("E", (), {"name": "a-client", "install_script": str(self._tmp / "install.sh")})()
        script = self._tmp / "install.sh"
        script.write_text("#!/bin/sh\nexit 0\n")

        with mock.patch.object(installer, "find_install_script", return_value=script), \
             mock.patch.object(installer, "COORDINATION_ENV", self.coord), \
             mock.patch.object(installer.subprocess, "run", fake_run):
            installer.run_install_script(entry, self._tmp)

        flag = [a for a in captured["cmd"] if a.startswith("--preserve-env=")]
        self.assertEqual(len(flag), 1, captured["cmd"])
        return set(flag[0].split("=", 1)[1].split(",")), captured["env"]

    def test_the_psws_ids_survive(self):
        """The ND regression: GRAPE could never be told where to upload."""
        names, _ = self._preserve()
        self.assertIn("STATION_PSWS_STATION_ID", names)
        self.assertIn("STATION_PSWS_INSTRUMENT_ID", names)

    def test_station_callsign_survives_alongside_station_call(self):
        """coordination.py emits both on purpose; both must cross sudo."""
        names, _ = self._preserve()
        self.assertIn("STATION_CALLSIGN", names)
        self.assertIn("STATION_CALL", names)

    def test_every_station_var_in_the_bag_is_preserved(self):
        """The rule, not a list: whatever coordination.env defines, sudo keeps."""
        names, _ = self._preserve()
        from_file = {l.split("=", 1)[0] for l in COORD.splitlines()
                     if l.startswith("STATION_")}
        self.assertEqual(from_file - names, set(),
                         "a STATION_* var sigmond published was dropped at sudo")

    def test_the_sigmond_runtime_vars_are_still_preserved(self):
        names, _ = self._preserve()
        for k in ("SIGMOND_INSTANCE", "SIGMOND_RADIOD_COUNT",
                  "SIGMOND_RADIOD_INDEX", "SIGMOND_RADIOD_STATUS",
                  "SIGMOND_TIME_SOURCE", "SIGMOND_GNSS_VTEC"):
            self.assertIn(k, names)

    def test_unrelated_shell_state_is_not_forwarded(self):
        """⛔ The safety property the original comment argued for.

        Blanket --preserve-env would hand the operator's whole shell to a root
        install script.  Only names sigmond itself published may cross.
        """
        with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "nope",
                                          "MY_TOKEN": "nope"}, clear=False):
            names, _ = self._preserve()
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", names)
        self.assertNotIn("MY_TOKEN", names)

    def test_a_missing_coordination_env_still_preserves_the_runtime_vars(self):
        """A host mid-bringup has no bag yet; the install must still run."""
        self.coord.unlink()
        names, _ = self._preserve()
        self.assertIn("SIGMOND_RADIOD_COUNT", names)
        self.assertNotIn("", names)


if __name__ == "__main__":
    unittest.main()

"""hfdl-recorder's own deploy.toml declares its sigmondb pipelines, and they
render (sigmondb plan, Task 7).  Reads the sibling checkout; skips without it."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sigmond import uploader_manifest as um
from sigmond.coordination import Coordination, Host, Radiod
from test_uploader_manifest import _Topo

DEPLOY = Path(__file__).resolve().parents[2] / "hfdl-recorder" / "deploy.toml"


@unittest.skipUnless(DEPLOY.is_file(), f"no hfdl-recorder checkout at {DEPLOY}")
class HfdlSigmondbPipelinesRender(unittest.TestCase):
    def setUp(self):
        um._WARNED_MISSING_FEATURES.clear()

    def _collect(self, features):
        coord = Coordination(
            host=Host(call="AC0G/B4", grid="EM38ww"),
            radiods={"sigma-status.local": Radiod(id="sigma-status.local")})
        with mock.patch.object(um, "find_deploy_toml", return_value=DEPLOY):
            return um.collect_pipelines(_Topo(["hfdl-recorder"]), coord,
                                        store_version=2, receivers=[],
                                        features=features)

    def test_both_tables_render_with_the_feature(self):
        by = {p["name"]: p for p in self._collect(frozenset({"sigmondb-tar"}))}
        for table in ("arrival", "capture"):
            p = by[f"hfdl-sigmondb-{table}"]
            self.assertEqual(p["source"]["producer"], "hfdl-recorder")
            self.assertEqual(p["source"]["table"], table)
            self.assertEqual(p["transport"]["tar_tag"], f"chirp-{table}")
            self.assertEqual(p["transport"]["primary_table_name"],
                             f"sigmondb.{table}")
            self.assertEqual(p["transport"]["receiver"], "AC0G_B4")
            self.assertNotIn("requires_features", p)

    def test_an_older_hs_uploader_gets_neither(self):
        names = {p["name"] for p in self._collect(frozenset({"gate"}))}
        self.assertFalse({"hfdl-sigmondb-arrival", "hfdl-sigmondb-capture"} & names)


if __name__ == "__main__":
    unittest.main()

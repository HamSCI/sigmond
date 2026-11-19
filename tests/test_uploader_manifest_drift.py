"""pipelines.d/sigmond.toml gains the daily drift pipeline (Task 9)."""
import sys
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(HERE))

from sigmond import uploader_manifest as um   # noqa: E402
import render_forms_driver as drv              # noqa: E402

GATE = frozenset({"gate", "sigmondb-tar"})


def _pipelines(text):
    return {p["name"]: p for p in tomllib.loads(text)["pipeline"]}


class DriftPipelineRenders(unittest.TestCase):
    def test_sigmond_pipeline_renders_drift_pipeline_when_feature_present(self):
        coord = drv.coordination("upload")
        by = _pipelines(um.sigmond_pipeline_file(coord, GATE))
        self.assertIn("heartbeat", by)
        p = by["sigmond-sigmondb-drift"]
        self.assertEqual(p["client"], "sigmond")
        self.assertIs(p["governed"], False)
        self.assertEqual(p["source"]["type"], "sqlite")
        self.assertEqual(p["source"]["database"], "sigmondb")
        self.assertEqual(p["source"]["table"], "station_drift")
        self.assertEqual(p["source"]["producer"], "sigmond")
        self.assertEqual(p["source"]["accepted_schema_versions"], [1])
        t = p["transport"]
        self.assertEqual(t["type"], "wsprdaemon_tar")
        self.assertEqual(t["primary_table_name"], "sigmondb.station_drift")
        self.assertEqual(t["tar_tag"], "chirp-drift")
        self.assertEqual(t["servers"],
                         ["gw1.wsprdaemon.org", "gw2.wsprdaemon.org"])
        self.assertEqual(p["retry"], {"base": 2.0, "cap_sec": 900.0})
        self.assertNotIn("requires_features", p)

    def test_drift_pipeline_absent_without_sigmondb_tar(self):
        coord = drv.coordination("upload")
        for feats in (None, frozenset(), frozenset({"gate"})):
            by = _pipelines(um.sigmond_pipeline_file(coord, feats))
            self.assertEqual(set(by), {"heartbeat"})

    def test_drift_alone_when_heartbeat_off(self):
        coord = drv.coordination("upload", heartbeat=False)
        by = _pipelines(um.sigmond_pipeline_file(coord, GATE))
        self.assertEqual(set(by), {"sigmond-sigmondb-drift"})
        self.assertIsNone(um.sigmond_pipeline_file(coord, frozenset()))


if __name__ == "__main__":
    unittest.main()

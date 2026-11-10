"""A client that runs but is not enabled in topology renders no pipelines.
Say so once, by name, so the gap does not hide (hfdl-recorder, 2026-11)."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sigmond import uploader_manifest as um
from sigmond.coordination import Coordination, Host, Radiod
from test_uploader_manifest import _Topo

DEPLOY_TOML = """
[[hs_uploader.pipeline]]
name = "demo-a"
[hs_uploader.pipeline.source]
type = "sqlite"
[[hs_uploader.pipeline]]
name = "demo-b"
[hs_uploader.pipeline.source]
type = "sqlite"
"""


class HiddenPipelineWarning(unittest.TestCase):
    def setUp(self):
        um._WARNED_MISSING_FEATURES.clear()
        d = tempfile.mkdtemp()
        self.deploy = Path(d) / "deploy.toml"
        self.deploy.write_text(DEPLOY_TOML)
        self.coord = Coordination(
            host=Host(call="AC0G/B4", grid="EM38ww"),
            radiods={"x.local": Radiod(id="x.local")})

    def _collect(self, enabled, running):
        cat = {"demo": SimpleNamespace(name="demo", topology_alias=None)}
        asked = []

        def is_running(name):
            asked.append(name)
            return running

        def find(name):
            return self.deploy if name == "demo" else None

        with mock.patch.object(um, "find_deploy_toml", side_effect=find), \
                mock.patch.object(um, "load_catalog", return_value=cat), \
                self.assertLogs(um.logger, level="DEBUG") as cm:
            out = um.collect_pipelines(_Topo(enabled), self.coord,
                                       store_version=2, receivers=[],
                                       check_features=False,
                                       is_running=is_running)
            um.logger.debug("sentinel")
        warns = [r.getMessage() for r in cm.records
                 if r.levelname == "WARNING" and "not enabled in topology" in r.getMessage()]
        return out, warns

    def test_running_not_enabled_warns_once(self):
        out, warns = self._collect([], True)
        self.assertEqual(out, [])
        self.assertEqual(len(warns), 1)
        self.assertIn("demo runs on this host", warns[0])
        self.assertIn("2 upload pipeline(s)", warns[0])
        self.assertIn("smd enable demo", warns[0])

    def test_not_running_is_silent(self):
        _, warns = self._collect([], False)
        self.assertEqual(warns, [])

    def test_enabled_renders_and_is_silent(self):
        out, warns = self._collect(["demo"], True)
        self.assertEqual(warns, [])
        self.assertEqual({p["name"] for p in out}, {"demo-a", "demo-b"})


if __name__ == "__main__":
    unittest.main()

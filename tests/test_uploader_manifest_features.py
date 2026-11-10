"""requires_features: sigmond renders a pipeline only where hs-uploader can
carry it (sigmondb plan, Task 5)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sigmond import uploader_manifest as um
from test_uploader_manifest import _Topo, _coord

BLOCK = """
[[hs_uploader.pipeline]]
name = "hfdl-sigmondb-arrival"
requires_features = ["sigmondb-tar"]
[hs_uploader.pipeline.source]
type = "sqlite"
[hs_uploader.pipeline.transport]
type = "wsprdaemon_tar"
"""

PLAIN = """
[[hs_uploader.pipeline]]
name = "plain"
[hs_uploader.pipeline.source]
type = "sqlite"
[hs_uploader.pipeline.transport]
type = "wsprdaemon_tar"
"""


def _deploy(body):
    f = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
    f.write(body)
    f.close()
    return Path(f.name)


def _collect(body, features):
    with mock.patch.object(um, "find_deploy_toml", return_value=_deploy(body)):
        return um.collect_pipelines(_Topo(["hfdl-recorder"]), _coord(),
                                    store_version=2, receivers=[],
                                    features=features)


class RequiresFeaturesTests(unittest.TestCase):
    def setUp(self):
        um._WARNED_MISSING_FEATURES.clear()

    def test_old_hs_uploader_never_gets_the_sigmondb_pipeline(self):
        for feats in (None, frozenset(), frozenset({"gate", "pipelines.d"})):
            self.assertEqual(_collect(BLOCK, feats), [])

    def test_feature_present_renders_and_strips_the_key(self):
        (p,) = _collect(BLOCK, frozenset({"sigmondb-tar", "gate"}))
        self.assertEqual(p["name"], "hfdl-sigmondb-arrival")
        self.assertNotIn("requires_features", p)

    def test_pipeline_without_requirement_renders_as_before(self):
        for feats in (None, frozenset()):
            (p,) = _collect(PLAIN, feats)
            self.assertEqual(p["name"], "plain")

    def test_one_warning_names_the_missing_words_and_does_not_repeat(self):
        with self.assertLogs(um.logger, level="WARNING") as cm:
            _collect(BLOCK, frozenset({"gate"}))
            _collect(BLOCK, frozenset({"gate"}))
        warns = [r for r in cm.records if "sigmondb-tar" in r.getMessage()]
        self.assertEqual(len(warns), 1)

    def test_generate_forms_threads_its_features(self):
        feats = frozenset({"gate", "sigmondb-tar"})
        with mock.patch.object(um, "find_deploy_toml",
                               return_value=_deploy(BLOCK)), \
             mock.patch.object(um, "host_key_file", return_value="/k"), \
             mock.patch.object(um, "_load_merge_receivers", return_value=[]):
            res = um.generate_forms(_Topo(["hfdl-recorder"]), _coord(),
                                    features=feats, marker_stands=True,
                                    store_version=2)
            old = um.generate_forms(_Topo(["hfdl-recorder"]), _coord(),
                                    features=frozenset({"gate"}),
                                    marker_stands=True, store_version=2)
        self.assertIn("hfdl-sigmondb-arrival", res.legacy_text)
        self.assertNotIn("hfdl-sigmondb-arrival", old.legacy_text)
        self.assertNotIn("requires_features", res.legacy_text)

    def _block(self, value):
        return BLOCK.replace('["sigmondb-tar"]', value)

    def test_a_bare_string_is_one_word(self):
        feats = frozenset({"sigmondb-tar"})
        self.assertEqual(len(_collect(self._block('"sigmondb-tar"'), feats)), 1)
        self.assertEqual(_collect(self._block('"sigmondb-tar"'), frozenset()), [])

    def test_a_non_list_requirement_fails_loud(self):
        for bad in ("3", "true", "{ a = 1 }"):
            with self.assertRaises(um.MergeError):
                _collect(self._block(bad), frozenset({"sigmondb-tar"}))

    def _suppressed(self, features_probe=None):
        coord = _coord()
        coord.uploads.mode = "discard"
        with mock.patch.object(um, "find_deploy_toml",
                               return_value=_deploy(BLOCK)), \
             mock.patch.object(um, "_load_merge_receivers", return_value=[]):
            return um.suppressed_pipelines(_Topo(["hfdl-recorder"]), coord,
                                           store_version=2)

    def test_suppressed_pipelines_lists_it_and_stays_silent(self):
        with self.assertNoLogs(um.logger, level="WARNING"):
            self.assertEqual(self._suppressed(), ["hfdl-sigmondb-arrival"])
        # and the dedup entry is untouched: a real render still warns once
        with self.assertLogs(um.logger, level="WARNING") as cm:
            _collect(BLOCK, frozenset({"gate"}))
            _collect(BLOCK, frozenset({"gate"}))
        self.assertEqual(
            len([r for r in cm.records if "sigmondb-tar" in r.getMessage()]), 1)


if __name__ == "__main__":
    unittest.main()

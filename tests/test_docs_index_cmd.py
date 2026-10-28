"""`smd admin docs index --json` and the `docs` snapshot.

A fixture tree under a temp base stands in for /opt/git/sigmond; the
cache path is redirected into the temp dir.  Nothing here elevates.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import snapshot  # noqa: E402


def _load_smd():
    loader = importlib.machinery.SourceFileLoader(
        "smd_docs_index_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_docs_index_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _entry(name):
    return SimpleNamespace(name=name, repo=f"https://example.invalid/{name}.git")


class DocsIndexCmdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.base = root / "base"
        for name, has_docs in (("alpha", True), ("beta", True), ("nodocs", False),
                               ("notcloned", False)):
            if name == "notcloned":
                continue
            d = self.base / name
            d.mkdir(parents=True)
            if has_docs:
                (d / "docs").mkdir()
                (d / "docs" / "INDEX.md").write_text("# Index\n\n- [Guide](guide.md)\n")
                (d / "docs" / "guide.md").write_text("# Guide\n\n> **Audience:** operators\n")
        self.cache = root / "var" / "docs-index-cache.json"
        self.smd = _load_smd()
        self.catalog = {n: _entry(n) for n in ("alpha", "beta", "nodocs", "notcloned")}
        self.patches = [
            mock.patch.object(self.smd, "load_catalog", return_value=self.catalog),
            mock.patch.object(self.smd, "DOCS_INDEX_CACHE", self.cache),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def _run(self, euid=1000):
        buf = io.StringIO()
        args = SimpleNamespace(json=True, base=str(self.base))
        with mock.patch.object(os, "geteuid", return_value=euid), \
                contextlib.redirect_stdout(buf):
            rc = self.smd.cmd_admin_docs_index(args)
        return rc, buf.getvalue()

    def test_json_names_every_component_that_has_docs(self):
        rc, out = self._run()
        self.assertEqual(rc, 0)
        index = json.loads(out)
        names = [r["name"] for r in index["roots"]]
        self.assertEqual(sorted(names), ["alpha", "beta"])
        alpha = next(r for r in index["roots"] if r["name"] == "alpha")
        self.assertIn("guide.md", [p["name"] for p in alpha["documents"]])

    def test_non_root_writes_no_cache(self):
        self._run(euid=1000)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.cache.parent.exists())

    def test_root_writes_the_cache_atomically_mode_0644(self):
        self._run(euid=0)
        self.assertTrue(self.cache.is_file())
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o644)
        self.assertIsInstance(json.loads(self.cache.read_text()), dict)
        self.assertEqual([p.name for p in self.cache.parent.iterdir()],
                         [self.cache.name])

    def test_root_ignores_an_unparseable_cache(self):
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text("{not json")
        rc, out = self._run(euid=0)
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out)["roots"]), 2)

    def test_root_feeds_the_cache_to_build_index(self):
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text(json.dumps({"k": {"head": "x", "commits": {}}}))
        seen = {}

        def fake(components, now, cache):
            seen["cache"] = cache
            return {"roots": []}, {"new": 1}
        with mock.patch("sigmond.docs_index.build_index", fake):
            self._run(euid=0)
        self.assertEqual(seen["cache"], {"k": {"head": "x", "commits": {}}})
        self.assertEqual(json.loads(self.cache.read_text()), {"new": 1})

    def test_non_root_passes_an_empty_cache(self):
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text(json.dumps({"k": 1}))
        seen = {}

        def fake(components, now, cache):
            seen["cache"] = cache
            return {"roots": []}, {"new": 1}
        with mock.patch("sigmond.docs_index.build_index", fake):
            self._run(euid=1000)
        self.assertEqual(seen["cache"], {})
        self.assertEqual(json.loads(self.cache.read_text()), {"k": 1})


class SnapshotAndParserTest(unittest.TestCase):
    def test_docs_is_a_snapshot(self):
        self.assertEqual(snapshot.SNAPSHOT_COMMANDS["docs"],
                         ["admin", "docs", "index", "--json"])

    def test_the_fallback_names_include_docs(self):
        self.assertIn("docs", _load_smd()._SNAPSHOT_NAMES_FALLBACK)


if __name__ == "__main__":
    unittest.main()

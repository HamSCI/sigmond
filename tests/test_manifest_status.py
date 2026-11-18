"""`smd admin manifest status --json` and the `fetched_at` row field.

Real temp git checkouts stand in for /opt/git/sigmond; nothing here
touches /etc or a station.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import heartbeat                       # noqa: E402
from sigmond.manifest_status import manifest_status  # noqa: E402

NAMES = [f"comp{i:02d}" for i in range(10)]
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=ENV).stdout.strip()


def _make_repo(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "f").write_text(path.name)
    _git(path, "add", "f")
    _git(path, "commit", "-q", "-m", "c")
    return _git(path, "rev-parse", "HEAD")


def _load_smd():
    loader = importlib.machinery.SourceFileLoader(
        "smd_manifest_status_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_manifest_status_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class ManifestStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.base = root / "base"
        self.shas = {n: _make_repo(self.base / n) for n in NAMES}
        self.manifest = root / "manifest.txt"
        self.version = root / "version"
        self.version.write_text("v3.99 2026-10-27\n")
        self.drift_json = root / "drift.json"
        self.paths = heartbeat.HeartbeatPaths(
            base=str(self.base), manifest_path=str(self.manifest),
            drift_json=str(self.drift_json))

    def _write_manifest(self, shas):
        rows = "".join(f"    {n} {s[:12]}\n" for n, s in shas.items())
        self.manifest.write_text("sigmond image\n\ncomponents (live):\n" + rows)

    def _status(self):
        return manifest_status(self.paths, version_file=str(self.version))

    def test_all_match(self):
        self._write_manifest(self.shas)
        st = self._status()
        self.assertTrue(st["present"])
        self.assertEqual(st["release"], "v3.99")
        self.assertEqual(st["source"], str(self.manifest))
        self.assertEqual(st["components"], {n: "match" for n in NAMES})

    def test_moved(self):
        shas = dict(self.shas)
        shas["comp03"] = "0" * 40
        self._write_manifest(shas)
        comps = self._status()["components"]
        self.assertEqual(comps["comp03"], "moved")
        self.assertEqual(comps["comp04"], "match")

    def test_manifest_only_and_live_only(self):
        shas = dict(self.shas)
        del shas["comp05"]
        shas["ghost"] = "1" * 40
        self._write_manifest(shas)
        comps = self._status()["components"]
        self.assertEqual(comps["comp05"], "live_only")
        self.assertEqual(comps["ghost"], "manifest_only")

    def test_missing_manifest(self):
        st = self._status()
        self.assertFalse(st["present"])
        self.assertEqual(st["components"], {})

    def test_untrusted_manifest(self):
        self.manifest.write_text("components (live):\n    a 1234567\n")
        st = self._status()
        self.assertFalse(st["present"])
        self.assertEqual(st["components"], {})

    def test_missing_version_stamp(self):
        self._write_manifest(self.shas)
        self.version.unlink()
        self.assertIsNone(self._status()["release"])

    def test_json_is_ascii_and_round_trips(self):
        self._write_manifest(self.shas)
        text = json.dumps(self._status())
        self.assertTrue(text.isascii())
        self.assertEqual(json.loads(text)["components"]["comp00"], "match")


class DriftReportSourceTest(ManifestStatusTest):
    """With a fresh drift.json the manifest block comes from its source
    items; the keys every consumer reads stay the same."""

    def _write_drift(self, items, accepted=(), age_s=3600, baseline="blessed"):
        from datetime import datetime, timedelta, timezone
        at = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        self.drift_json.write_text(json.dumps({
            "station": "S", "release": "v3.90", "baseline": baseline,
            "generated_at": at, "counts": {}, "items": list(items),
            "accepted": list(accepted), "reminders": []}))

    @staticmethod
    def _src(comp, detail, fix, sev="drift"):
        return {"kind": "source", "severity": sev, "subject": comp, "detail": detail,
                "fix": fix, "key": f"source:{comp}"}

    def test_manifest_status_keys_unchanged(self):
        self._write_manifest(self.shas)
        before = self._status()
        block_before = heartbeat._read_manifest(self.paths)
        self._write_drift([
            self._src("comp03", "aaaaaaa -> bbbbbbb", "smd align --apply"),
            self._src("comp04", "ccccccc -> ddddddd",
                      "smd align --apply --allow-rollback (or accept it)"),
            self._src("ghost", "none -> eeeeeee",
                      "smd align --apply --install-missing", sev="fault"),
            self._src("baseline", "x", "smd align --apply"),
            {"kind": "unit", "severity": "fault", "subject": "x.timer",
             "detail": "d", "fix": "smd align --apply", "key": "unit:x.timer"}],
            accepted=[{"key": "source:comp05", "kind": "test",
                       "fix": "remove it or add it to the release"}])
        st = self._status()
        block = heartbeat._read_manifest(self.paths)
        self.assertEqual(set(st), set(before))
        self.assertEqual(set(block), set(block_before))
        self.assertTrue(st["present"])
        self.assertIn("blessed v3.90", st["source"])
        comps = st["components"]
        self.assertEqual(comps["comp03"], "moved")
        self.assertEqual(comps["comp04"], "superset")
        self.assertEqual(comps["comp05"], "live_only")
        self.assertEqual(comps["ghost"], "manifest_only")
        self.assertEqual(comps["comp00"], "match")
        row = next(d for d in block["drift"] if d["component"] == "comp03")
        self.assertEqual(set(row), {"component", "status", "manifest", "live"})
        self.assertEqual((row["live"], row["manifest"]), ("aaaaaaa", "bbbbbbb"))
        # the heartbeat mapper reads it the same way
        mapped = heartbeat._map_manifest(block)
        self.assertEqual(mapped["data"]["drift"], block["drift"])

    def test_stale_or_baseline_none_report_is_ignored(self):
        self._write_manifest(self.shas)
        self._write_drift([self._src("comp03", "aaaaaaa -> bbbbbbb",
                                     "smd align --apply")], age_s=37 * 3600)
        self.assertEqual(self._status()["components"]["comp03"], "match")
        self._write_drift([self._src("comp03", "aaaaaaa -> bbbbbbb",
                                     "smd align --apply")], baseline="none")
        self.assertEqual(self._status()["source"], str(self.manifest))


class FetchedAtTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.smd = _load_smd()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "r"
        _make_repo(self.repo)

    def test_none_when_never_fetched(self):
        self.assertIsNone(self.smd._fetched_at(self.repo))

    def test_iso_utc_from_fetch_head_mtime(self):
        fh = self.repo / ".git" / "FETCH_HEAD"
        fh.write_text("")
        os.utime(fh, (1_700_000_000, 1_700_000_000))
        got = self.smd._fetched_at(self.repo)
        self.assertEqual(got, "2023-11-14T22:13:20Z")
        self.assertRegex(got, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


class BehindCountTest(unittest.TestCase):
    """A rev-list that cannot count gives no count, never a bare zero."""

    @classmethod
    def setUpClass(cls):
        cls.smd = _load_smd()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "r"
        _make_repo(self.repo)

    def test_counts_against_a_real_ref(self):
        _git(self.repo, "branch", "-q", "base")
        (self.repo / "g").write_text("x")
        _git(self.repo, "add", "g")
        _git(self.repo, "commit", "-q", "-m", "d")
        self.assertEqual(self.smd._git_behind_ahead(self.repo, "base"), (0, 1))
        self.assertEqual(
            self.smd._git_behind_ahead(self.repo, "HEAD")[0], 0)

    def test_a_failed_rev_list_is_none(self):
        self.assertEqual(
            self.smd._git_behind_ahead(self.repo, "origin/main"), (None, None))

    def test_no_ref_is_none(self):
        self.assertEqual(self.smd._git_behind_ahead(self.repo, ""), (None, None))

    def test_the_row_carries_null_and_an_honest_verdict(self):
        from unittest import mock
        entry = type("E", (), {"name": "comp", "kind": "client", "repo": "x"})()
        topo = type("T", (), {"components": {}})()
        args = type("A", (), {"components": "comp", "topology": None})()
        with mock.patch.object(self.smd, "load_catalog",
                               return_value={"comp": entry}), \
             mock.patch("sigmond.topology.load_topology", return_value=topo), \
             mock.patch.object(self.smd, "_resolve_repo_dir",
                               return_value=self.repo), \
             mock.patch.object(self.smd, "_git_default_ref",
                               return_value="origin/main"):
            rows = self.smd._component_statuses(args, fetch=False)
        row = next(r for r in rows if r["name"] == "comp")
        self.assertIsNone(row["behind"])
        self.assertFalse(row["behind_exact"])
        self.assertEqual(row["verdict"], "behind unknown")
        self.assertIsNone(row["fetched_at"])
        self.assertIn('"behind": null', json.dumps(row))


class CliTest(unittest.TestCase):
    def test_status_json_prints_the_dict(self):
        import contextlib
        import io
        from unittest import mock
        smd = _load_smd()
        fake = {"present": False, "release": None, "source": "x",
                "components": {}}
        buf = io.StringIO()
        with mock.patch("sigmond.manifest_status.manifest_status",
                        return_value=fake), contextlib.redirect_stdout(buf):
            rc = smd.cmd_manifest_status(
                type("A", (), {"json": True})())
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue()), fake)


if __name__ == "__main__":
    unittest.main()

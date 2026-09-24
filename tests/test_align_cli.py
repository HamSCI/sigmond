"""CLI tests for `smd align` — every probe patched; no network, no git."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import types
import unittest
from pathlib import Path
from unittest import mock

from sigmond import align

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_align", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_align", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()
REL = align.Release(tag="v3.53", manifest_text="", appliance_commit=None,
                    components={"sigmond": "daba1f6", "hf-timestd": "5c8196d",
                                "ka9q-radio": "401992c"})


def run(live, dirty=None, files=None, recorded="v3.36", release=REL, no_cost=True,
       origins=None, **ns):
    args = argparse.Namespace(release=None, base="/opt/git/sigmond", no_cost=no_cost, **ns)
    patches = [
        mock.patch.object(smd, "_align_live_state",
                          return_value=(live, dirty or {}, origins or {})),
        mock.patch("sigmond.align.image_file_drift", return_value=files or [
            {"path": "/usr/local/sbin/sigmond-site-timing", "status": "current", "note": ""}]),
        mock.patch("sigmond.align.recorded_release", return_value=recorded),
    ]
    if isinstance(release, Exception):
        patches.append(mock.patch("sigmond.align.fetch_release", side_effect=release))
    else:
        patches.append(mock.patch("sigmond.align.fetch_release", return_value=release))
    out = io.StringIO()
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        st.enter_context(contextlib.redirect_stdout(out))
        rc = smd.cmd_align(args)
    return rc, out.getvalue()


class AlignCliTests(unittest.TestCase):
    def test_aligned_station_exits_0(self):
        rc, out = run({"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      recorded="v3.53")
        self.assertEqual(rc, 0)
        self.assertIn("against blessed v3.53", out)
        self.assertIn("nothing changes", out)

    def test_moves_exit_1_and_flag_radiod(self):
        rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "deb7bdd"})
        self.assertEqual(rc, 1)
        self.assertIn("459bee6 -> daba1f6", out)
        self.assertIn("RESTARTS radiod", out)
        self.assertIn("run pm-align", out)

    def test_differing_image_file_exits_1(self):
        rc, out = run({"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      files=[{"path": "/usr/local/sbin/sigmond-site-timing",
                              "status": "differs", "note": "differs from v3.53"}],
                      recorded="v3.53")
        self.assertEqual(rc, 1)
        self.assertIn("1 file to refresh", out)

    def test_lookup_failure_exits_2(self):
        rc, out = run({}, release=align.LookupError_("could not reach api.github.com"))
        self.assertEqual(rc, 2)
        self.assertIn("could not reach", out)

    def test_dry_run_cannot_move_anything(self):
        # Structural guard: the dry run has no way to change a station. align.py
        # never imports subprocess, and cmd_align names no git mutation. Adding
        # either on purpose (Plan 2) must come with its own tests.
        import inspect
        src = inspect.getsource(align)
        self.assertNotIn("import subprocess", src)
        self.assertNotIn("subprocess.", src)
        body = inspect.getsource(smd.cmd_align)
        for word in ("checkout", "'fetch'", '"fetch"', "install.sh", "systemctl", "_run_git"):
            self.assertNotIn(word, body)

    # --- Fix round 1 -----------------------------------------------------

    def test_unverified_image_file_blocks_exit_0(self):
        """An 'unknown' image-file status (probe unreachable) must not let
        the run silently claim alignment — exit 1, and name the file(s)."""
        rc, out = run({"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      files=[{"path": "/usr/local/sbin/sigmond-site-timing",
                              "status": "unknown", "note": "could not reach raw.githubusercontent.com"},
                             {"path": "/usr/local/sbin/sigmond-location-check",
                              "status": "unknown", "note": "could not reach raw.githubusercontent.com"}],
                      recorded="v3.53")
        self.assertEqual(rc, 1)
        self.assertIn("could not be checked", out)
        self.assertIn("/usr/local/sbin/sigmond-site-timing", out)
        self.assertIn("/usr/local/sbin/sigmond-location-check", out)

    def test_no_cost_false_shows_commit_distance(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond")}
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance",
                        return_value={"ahead": 39, "behind": 0, "files": 51}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False)
        self.assertIn("39 commits, 51 files", out)

    def test_no_cost_false_catalog_unreadable_no_crash(self):
        with mock.patch("sigmond.catalog.load_catalog", side_effect=ValueError("bad toml")):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False)
        self.assertEqual(rc, 1)
        self.assertIn("catalog unreadable", out)

    def test_no_cost_false_commit_distance_error_shows_unknown(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond")}
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance", return_value={"error": "no route"}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False)
        self.assertIn("distance unknown", out)

    def test_origin_fallback_when_catalog_has_no_repo(self):
        """sigmond has no catalog entry/repo; _align_live_state's origin-URL
        fallback should still let a commit distance be computed."""
        with mock.patch("sigmond.catalog.load_catalog", return_value={}), \
             mock.patch("sigmond.align.commit_distance",
                        return_value={"ahead": 39, "behind": 0, "files": 51}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False,
                          origins={"sigmond": "https://github.com/HamSCI/sigmond"})
        self.assertIn("39 commits, 51 files", out)

    def test_commit_distance_singular_grammar(self):
        catalog = {"sigmond": types.SimpleNamespace(repo="https://github.com/HamSCI/sigmond")}
        with mock.patch("sigmond.catalog.load_catalog", return_value=catalog), \
             mock.patch("sigmond.align.commit_distance",
                        return_value={"ahead": 1, "behind": 0, "files": 1}):
            rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                          no_cost=False)
        self.assertIn("1 commit, 1 file", out)
        self.assertNotIn("1 commits", out)
        self.assertNotIn("1 files", out)

    def test_refuse_row_shows_shas(self):
        rc, out = run({"sigmond": "459bee6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"},
                      dirty={"sigmond": True})
        self.assertIn("459bee6 -> daba1f6   refuse", out)


if __name__ == "__main__":
    unittest.main()

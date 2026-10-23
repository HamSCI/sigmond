"""``sink_state``: sigmond reads the switches through
``hs-uploader sink status --json`` (spec §7.5; Ruling 12; V14).

The fixtures under ``tests/fixtures/sink_status/`` come from the branch
hs-uploader, run by ``capture.sh`` on trees it builds (global constraint 4).
``ContractTests`` re-runs that script against the real checkout beside
sigmond and compares key for key, then runs ``read_state`` itself on the
same trees.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond import sink_state as ss
from sigmond import uploader_manifest as um

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sink_status"
CAPTURE = FIXTURES / "capture.sh"
REAL_PY = (Path(__file__).resolve().parent.parent.parent
           / "hs-uploader" / ".venv" / "bin" / "python")
CASES = sorted(p.stem for p in FIXTURES.glob("*.json"))


def _text(case: str) -> str:
    return (FIXTURES / f"{case}.json").read_text()


def _rc(case: str) -> int:
    return int((FIXTURES / f"{case}.rc").read_text())


def _parse(case: str):
    err = StringIO()
    with redirect_stderr(err):
        st = ss.parse_status(_text(case))
    return st, err.getvalue()


def _done(stdout: str, rc: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(["python"], rc, stdout, stderr)


class FixtureSetTests(unittest.TestCase):

    def test_every_brief_case_was_captured(self):
        self.assertEqual(set(CASES), {
            "all-upload", "site-off", "psk-fill", "psk-fill-site-off",
            "broken-site", "no-sinks", "marker-absent", "store-unreadable",
            "refused", "manifest-unreadable"})
        for c in CASES:
            self.assertEqual(_rc(c), 2 if c == "manifest-unreadable" else 0, c)


class ParseTests(unittest.TestCase):

    def test_all_upload(self):
        st, err = _parse("all-upload")
        self.assertEqual(err, "")
        self.assertTrue(st.enforced)
        self.assertEqual(st.site.state, "upload")
        self.assertEqual(st.site.set_at, "2026-10-01T00:00:00Z")
        self.assertEqual(sorted(st.clients),
                         ["mag-recorder", "psk-recorder", "wspr-recorder"])
        self.assertEqual(st.clients["psk-recorder"], ss.ClientState(
            file_state="upload", effective="upload", held_by=None, error="",
            pipelines=("psk-recorder-pskreporter",)))
        self.assertEqual(st.held_pipelines, ())
        self.assertEqual(st.refused_files, ())
        self.assertEqual(st.no_client_pipelines, ())
        self.assertEqual(st.builder_pipelines, ())
        self.assertEqual(st.errors, ())
        self.assertEqual(ss.below_upload(st), [])

    def test_site_off_holds_every_client(self):
        st, _ = _parse("site-off")
        self.assertEqual(st.site.state, "off")
        for name, c in st.clients.items():
            self.assertEqual((c.file_state, c.effective, c.held_by),
                             ("upload", "off", "site"), name)
        self.assertEqual(ss.below_upload(st),
                         ["mag-recorder", "psk-recorder", "wspr-recorder"])
        self.assertEqual(
            [(h.pipeline, h.client, h.held_by) for h in st.held_pipelines],
            [("mag-recorder-psws", "mag-recorder", "site"),
             ("psk-recorder-pskreporter", "psk-recorder", "site"),
             ("wspr-recorder-wsprnet", "wspr-recorder", "site")])
        self.assertEqual(st.held_pipelines[0].reason, "sink off (site)")

    def test_psk_fill(self):
        st, _ = _parse("psk-fill")
        self.assertEqual(st.clients["psk-recorder"].effective, "fill")
        self.assertEqual(st.clients["psk-recorder"].held_by, "client")
        self.assertEqual(ss.below_upload(st), ["psk-recorder"])
        self.assertEqual([h.pipeline for h in st.held_pipelines],
                         ["psk-recorder-pskreporter"])

    def test_psk_fill_under_site_off_reads_effective_not_file(self):
        st, _ = _parse("psk-fill-site-off")
        psk = st.clients["psk-recorder"]
        self.assertEqual((psk.file_state, psk.effective, psk.held_by),
                         ("fill", "off", "site"))
        self.assertEqual(st.clients["wspr-recorder"].file_state, "upload")
        self.assertEqual(ss.below_upload(st),
                         ["mag-recorder", "psk-recorder", "wspr-recorder"])

    def test_broken_site_reads_as_a_fault_holding_everything(self):
        st, _ = _parse("broken-site")
        self.assertEqual(st.site.state, "error")
        self.assertIn("site.toml: parse error", st.site.error)
        for c in st.clients.values():
            self.assertEqual((c.effective, c.held_by), ("off", "fault"))
            self.assertIn("parse error", c.error)
        self.assertEqual(len(ss.below_upload(st)), 3)
        self.assertEqual({h.held_by for h in st.held_pipelines}, {"fault"})

    def test_no_sinks_dir(self):
        st, _ = _parse("no-sinks")
        self.assertEqual(st.site.state, "missing")
        for c in st.clients.values():
            self.assertEqual((c.file_state, c.effective, c.held_by),
                             ("missing", "off", "fault"))
        self.assertEqual(len(ss.below_upload(st)), 3)

    def test_marker_absent_still_reads_effective_states(self):
        """Ruling X5: effective and held_by do not depend on the marker;
        only enforced and held_pipelines do."""
        st, _ = _parse("marker-absent")
        self.assertFalse(st.enforced)
        self.assertEqual(st.held_pipelines, ())
        self.assertEqual(ss.below_upload(st),
                         ["mag-recorder", "psk-recorder", "wspr-recorder"])

    def test_store_unreadable_keeps_its_error(self):
        """A non-empty errors list is not "nothing held": the state keeps
        it, and says the read was partial."""
        st, _ = _parse("store-unreadable")
        self.assertEqual(len(st.errors), 1)
        self.assertIn("cannot read the store", st.errors[0])
        self.assertTrue(st.partial)
        self.assertFalse(_parse("all-upload")[0].partial)
        self.assertEqual(st.refused_files, ())
        self.assertEqual(ss.below_upload(st), ["psk-recorder"])

    def test_refused_files_both_kinds(self):
        st, _ = _parse("refused")
        self.assertEqual(st.refused_files, (
            ss.RefusedFile(file=None, pipeline="store-held",
                           client="wspr-recorder",
                           reason="store schema too old"),
            ss.RefusedFile(file="meteor-scatter.toml", pipeline=None,
                           client="meteor-scatter",
                           reason=st.refused_files[1].reason)))
        self.assertIn("start_at", st.refused_files[1].reason)
        self.assertEqual(st.clients["meteor-scatter"].pipelines, ())

    def test_manifest_unreadable_output_still_parses(self):
        """parse_status reads the text; read_state decides on the exit
        code (see ReadStateTests)."""
        st, _ = _parse("manifest-unreadable")
        self.assertTrue(st.partial)
        self.assertIn("cannot read", st.errors[0])


class SchemaTests(unittest.TestCase):

    def test_schema_2_reads_as_none_with_one_line_naming_it(self):
        d = json.loads(_text("all-upload"))
        d["schema"] = 2
        err = StringIO()
        with redirect_stderr(err):
            self.assertIsNone(ss.parse_status(json.dumps(d)))
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("schema 2", lines[0])

    def test_missing_schema_reads_as_none(self):
        d = json.loads(_text("all-upload"))
        del d["schema"]
        with redirect_stderr(StringIO()):
            self.assertIsNone(ss.parse_status(json.dumps(d)))

    def test_not_json_reads_as_none(self):
        err = StringIO()
        with redirect_stderr(err):
            self.assertIsNone(ss.parse_status("usage: hs-uploader ...\n"))
        self.assertEqual(len(err.getvalue().splitlines()), 1)

    def test_a_missing_field_reads_as_none(self):
        for key in ("clients", "site", "errors", "held_pipelines"):
            d = json.loads(_text("site-off"))
            del d[key]
            with redirect_stderr(StringIO()):
                self.assertIsNone(ss.parse_status(json.dumps(d)), key)
        d = json.loads(_text("site-off"))
        del d["clients"][0]["effective"]
        with redirect_stderr(StringIO()):
            self.assertIsNone(ss.parse_status(json.dumps(d)))

    def test_an_unknown_effective_state_reads_below_upload(self):
        d = json.loads(_text("all-upload"))
        d["clients"][0]["effective"] = "paused"
        st = ss.parse_status(json.dumps(d))
        self.assertEqual(ss.below_upload(st), ["mag-recorder"])


class ReadStateTests(unittest.TestCase):

    def _read(self, result=None, exc=None, **kw):
        err = StringIO()
        with mock.patch.object(um, "run_hs_uploader",
                               side_effect=exc,
                               return_value=result) as run, \
                redirect_stderr(err):
            st = ss.read_state(**kw)
        return st, run, err.getvalue()

    def test_reads_through_the_runner(self):
        st, run, err = self._read(_done(_text("psk-fill")))
        self.assertEqual(ss.below_upload(st), ["psk-recorder"])
        self.assertEqual(err, "")
        args, kw = run.call_args
        self.assertEqual(args[0], ["sink", "status", "--json"])
        self.assertEqual(kw["python"], um.HS_UPLOADER_PYTHON)

    def test_passes_the_tree_it_is_given(self):
        _, run, _ = self._read(_done(_text("psk-fill")),
                               manifest="/m.toml", sinks="/s", marker="/k",
                               store="/w.db", python=Path("/py"), timeout=3)
        args, kw = run.call_args
        self.assertEqual(args[0], ["--state", "/w.db", "sink", "status",
                                   "--json", "--manifest", "/m.toml",
                                   "--sinks", "/s", "--marker", "/k"])
        self.assertEqual((kw["python"], kw["timeout"]), (Path("/py"), 3))

    def test_a_timeout_reads_as_none(self):
        st, _, err = self._read(
            exc=subprocess.TimeoutExpired(["python"], 20))
        self.assertIsNone(st)
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn("the call timed out after 20 s", err)

    def test_a_real_timeout_reads_as_none(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        py = Path(td.name) / "python"
        py.write_text("#!/bin/sh\nexec sleep 30\n")
        py.chmod(py.stat().st_mode | stat.S_IXUSR)
        err = StringIO()
        with redirect_stderr(err):
            self.assertIsNone(ss.read_state(python=py, timeout=0.5))
        self.assertIn("the call timed out after 0.5 s", err.getvalue())

    def test_a_missing_interpreter_reads_as_none(self):
        st, _, err = self._read(exc=FileNotFoundError("no python"))
        self.assertIsNone(st)
        self.assertEqual(len(err.splitlines()), 1)

    def test_exit_2_reads_as_none_and_names_the_error(self):
        """The manifest unreadable: status exits 2, its JSON lists no
        pipelines, so nothing held would be a false reading (Ruling 12:
        a failed call reads as None)."""
        st, _, err = self._read(_done(_text("manifest-unreadable"), rc=2))
        self.assertIsNone(st)
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn("exit 2", err)
        self.assertIn("cannot read @TREE@/etc/pipelines.toml", err)

    def test_a_failure_without_json_names_stderr(self):
        st, _, err = self._read(_done("", rc=1, stderr="Traceback\nBoom: x\n"))
        self.assertIsNone(st)
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn("Boom: x", err)

    def test_an_older_hs_uploader_without_sink_reads_as_none(self):
        st, _, err = self._read(_done(
            "", rc=2, stderr="hs-uploader: error: argument cmd: invalid "
                             "choice: 'sink'\n"))
        self.assertIsNone(st)
        self.assertIn("invalid choice", err)

    def test_store_unreadable_exit_0_keeps_the_errors(self):
        st, _, _ = self._read(_done(_text("store-unreadable")))
        self.assertTrue(st.partial)
        self.assertEqual(len(st.errors), 1)


class ContractTests(unittest.TestCase):
    """The branch hs-uploader, through its own venv, on the trees
    capture.sh builds: the committed fixtures match it key for key, and
    read_state on the same tree reads what the fixture parses to."""

    @classmethod
    def setUpClass(cls):
        if not REAL_PY.exists():
            raise unittest.SkipTest(f"no hs-uploader checkout at {REAL_PY}")
        cls.td = tempfile.TemporaryDirectory()
        cls.out = Path(cls.td.name) / "out"
        cls.work = Path(cls.td.name) / "work"
        cls.work.mkdir()
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env.update(HS_UPLOADER_PY=str(REAL_PY), CAPTURE_WORK=str(cls.work))
        subprocess.run(["bash", str(CAPTURE), str(cls.out)], env=env,
                       check=True, capture_output=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_same_cases(self):
        self.assertEqual(sorted(p.stem for p in self.out.glob("*.json")),
                         CASES)

    def test_fixtures_match_the_real_output_key_for_key(self):
        for c in CASES:
            with self.subTest(case=c):
                self.assertEqual(
                    json.loads((self.out / f"{c}.json").read_text()),
                    json.loads(_text(c)))
                self.assertEqual((self.out / f"{c}.rc").read_text(),
                                 (FIXTURES / f"{c}.rc").read_text())

    def test_read_state_on_the_real_trees(self):
        for c in CASES:
            tree = self.work / c
            with self.subTest(case=c), mock.patch.dict(os.environ), \
                    redirect_stderr(StringIO()) as err:
                os.environ.pop("PYTHONPATH", None)
                got = ss.read_state(
                    python=REAL_PY,
                    manifest=str(tree / "etc" / "pipelines.toml"),
                    marker=str(tree / "state" / "carry-over-v1.done"),
                    store=str(tree / "state" / "watermarks.db"))
                if c == "manifest-unreadable":
                    self.assertIsNone(got)
                    self.assertIn("exit 2", err.getvalue())
                    continue
                want = ss.parse_status(_text(c).replace("@TREE@", str(tree)))
                self.assertIsNotNone(want)
                self.assertEqual(got, want)


if __name__ == "__main__":
    unittest.main()

"""The two render forms of the uploader manifest, and pipelines.d/sigmond.toml
(spec sink-2b part 2 §3.6, §7.4; tests 14 and 15; plan Task 16; V2, V3, V12).

With the feature word ``gate`` and the carry-over marker, sigmond renders
every client pipeline as it is, with no ``discard``, no hold and no banner,
and moves the heartbeat into ``pipelines.d/sigmond.toml``.  Otherwise it
renders exactly part 1's form.  Part 1's form is pinned by rendering the
same inputs with sigmond be91e22 (the branch base) in its own interpreter
(``render_forms_driver.py``).  The part 1 hs-uploader loader (29a0806)
reads the new file in its own interpreter too.  Both old trees come from
``git archive`` into a temporary directory (plan ruling X13); the tests
that need them skip when git or the commit is missing.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(HERE))

from sigmond import uploader_manifest as um   # noqa: E402
import render_forms_driver as drv              # noqa: E402

PART1_SIGMOND = "be91e22"
PART1_HSU = "29a0806"
HSU_REPO = REPO.parent / "hs-uploader"

MODES = ("upload", "hold", "discard")
GATE = frozenset({"gate", "starting-record"})
NO_GATE_SETS = {
    "other-words": frozenset({"starting-record"}),
    "empty": frozenset(),
    "none": None,
}


def _archive(repo: Path, commit: str, subdir: str, dest: Path) -> bool:
    """Extract `subdir` of `commit` into `dest`; False when git or the
    commit is missing."""
    if shutil.which("git") is None or not (repo / ".git").exists():
        return False
    if subprocess.run(["git", "-C", str(repo), "cat-file", "-e",
                       f"{commit}^{{commit}}"],
                      capture_output=True).returncode != 0:
        return False
    tar = subprocess.run(["git", "-C", str(repo), "archive", commit, subdir],
                         capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)
    return True


# --------------------------------------------------------------------------
# part 1's render, from sigmond be91e22 in its own interpreter
# --------------------------------------------------------------------------

# Every input the pin compares: [key, [uploads] mode, render keywords].
PART1_CASES = [
    [f"{m}", m, {}] for m in MODES
] + [
    ["discard-unsupported", "discard", {"supports_discard": False}],
    ["upload-no-heartbeat", "upload", {"heartbeat": False}],
    ["upload-psk-inprocess", "upload", {"senders": [list(drv.PSK)]}],
]

_part1 = {}


def part1_renders(test: unittest.TestCase) -> dict:
    """``{case key: text}`` from be91e22's uploader_manifest; skips the
    test when the commit cannot be archived."""
    if "texts" in _part1:
        return _part1["texts"]
    if "skip" in _part1:
        test.skipTest(_part1["skip"])
    tmp = Path(tempfile.mkdtemp(prefix="part1-sigmond-"))
    try:
        if not _archive(REPO, PART1_SIGMOND, "lib", tmp):
            _part1["skip"] = f"git or sigmond {PART1_SIGMOND} unavailable"
            test.skipTest(_part1["skip"])
        # be91e22 holds no part 2 code: none of the modules part 2 added.
        for added in ("safe_write.py", "install_lock.py", "sink_state.py"):
            assert not (tmp / "lib/sigmond" / added).exists(), added
        assert "generate_forms" not in (
            tmp / "lib/sigmond/uploader_manifest.py").read_text()
        r = subprocess.run(
            [sys.executable, "-I", str(HERE / "render_forms_driver.py"),
             str(tmp / "lib"), str(tmp / "work")],
            input=json.dumps(PART1_CASES), capture_output=True, text=True,
            cwd=str(tmp), timeout=120)
        if r.returncode != 0:
            raise AssertionError(f"part 1 render failed: {r.stderr}")
        _part1["texts"] = json.loads(r.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _part1["texts"]


class _Render(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def forms(self, mode, features, marker, **kw):
        return drv.render(um, self.tmp / "in", mode,
                          forms=dict(features=features, marker_stands=marker),
                          **kw)


# --------------------------------------------------------------------------
# test 15: without the gate form, part 1's bytes
# --------------------------------------------------------------------------


class PartOneFormTests(_Render):
    """Test 15 (V2, V3): no ``gate``, the empty set, a failed probe, or
    ``gate`` without the marker: part 1's render, byte for byte."""

    CASES = [  # (key, mode, keywords)
        ("upload", "upload", {}),
        ("hold", "hold", {}),
        ("discard", "discard", {}),
        ("discard-unsupported", "discard", {"supports_discard": False}),
    ]

    def _check(self, features, marker):
        part1 = part1_renders(self)
        for key, mode, kw in self.CASES:
            with self.subTest(case=key):
                legacy, sfile = self.forms(mode, features, marker, **kw)
                self.assertEqual(legacy, part1[key])
                self.assertIsNone(sfile)
                # The heartbeat stays in the legacy manifest.
                names = [p["name"] for p in
                         tomllib.loads(legacy).get("pipeline", [])]
                self.assertIn("heartbeat", names)

    def test_no_gate_word(self):
        for marker in (True, False):
            with self.subTest(marker=marker):
                self._check(NO_GATE_SETS["other-words"], marker)

    def test_empty_set(self):
        for marker in (True, False):
            with self.subTest(marker=marker):
                self._check(NO_GATE_SETS["empty"], marker)

    def test_failed_probe(self):
        # Mutation "trust the daemon whenever the probe fails" fails here.
        for marker in (True, False):
            with self.subTest(marker=marker):
                self._check(None, marker)

    def test_gate_without_marker(self):
        """A failed carry-over leaves the marker absent: part 1's form."""
        self._check(GATE, False)

    def test_generate_keeps_returning_part1_text(self):
        """``generate()`` keeps its part 1 signature and bytes (ruling X13):
        the manifest step calls it unchanged until Task 19."""
        part1 = part1_renders(self)
        for key, mode, kw in self.CASES:
            with self.subTest(case=key):
                self.assertEqual(drv.render(um, self.tmp / "in", mode, **kw),
                                 part1[key])

    def test_removes_sigmond_file(self):
        """Without the gate form the result names no file, and landing that
        None removes a sigmond.toml an earlier gate render left (spec §3.6)."""
        d = self.tmp / "etc" / "pipelines.d"
        d.mkdir(parents=True)
        (d / "sigmond.toml").write_text("[[pipeline]]\nname = \"x\"\n")
        _, sfile = self.forms("upload", frozenset(), True)
        self.assertTrue(um.land_sigmond_file(sfile, d))
        self.assertFalse((d / "sigmond.toml").exists())
        # Positive control: the gate render names a file, which lands.
        _, sfile = self.forms("upload", GATE, True)
        self.assertTrue(um.land_sigmond_file(sfile, d))
        self.assertTrue((d / "sigmond.toml").exists())


# --------------------------------------------------------------------------
# test 14: the gate form
# --------------------------------------------------------------------------


class GateFormTests(_Render):
    """Test 14: with ``gate`` and the marker, the legacy bytes do not depend
    on ``[uploads]``; no ``discard``; the heartbeat only in sigmond.toml."""

    def _all_modes(self, **kw):
        return {m: self.forms(m, GATE, True, **kw) for m in MODES}

    def test_legacy_bytes_identical_for_every_uploads_mode(self):
        got = self._all_modes()
        got["discard-unsupported"] = self.forms("discard", GATE, True,
                                                supports_discard=False)
        texts = {k: v[0] for k, v in got.items()}
        self.assertEqual(len(set(texts.values())), 1, texts.keys())
        files = {v[1] for v in got.values()}
        self.assertEqual(len(files), 1)

    def test_no_discard_no_hold_no_banner(self):
        for mode, (legacy, sfile) in self._all_modes().items():
            with self.subTest(mode=mode):
                self.assertNotIn("discard", legacy)
                self.assertNotIn("discard", sfile)
                self.assertNotIn("SITE SINK", legacy)
                parsed = tomllib.loads(legacy)
                names = [p["name"] for p in parsed["pipeline"]]
                # Every client pipeline renders, even under hold (no hold).
                self.assertEqual(names, ["wspr-wsprnet",
                                         "psk-recorder-pskreporter",
                                         "meteor-scatter-pskreporter"])
                for p in parsed["pipeline"]:
                    self.assertIn(p["client"], drv.BODIES)
                    self.assertNotIn("discard", p)

    def test_client_pipelines_match_part1_upload_form(self):
        """Apart from the header comments and the heartbeat, the gate form
        holds what part 1 renders under ``upload``."""
        part1 = tomllib.loads(part1_renders(self)["upload-no-heartbeat"])
        for mode, (legacy, _) in self._all_modes().items():
            with self.subTest(mode=mode):
                self.assertEqual(tomllib.loads(legacy), part1)

    def test_heartbeat_only_in_sigmond_file(self):
        for mode, (legacy, sfile) in self._all_modes().items():
            with self.subTest(mode=mode):
                names = [p["name"] for p in tomllib.loads(legacy)["pipeline"]]
                self.assertNotIn("heartbeat", names)
                self.assertNotIn("heartbeat_sftp", legacy)
                parsed = tomllib.loads(sfile)
                self.assertEqual(set(parsed), {"pipeline"})
                self.assertEqual(len(parsed["pipeline"]), 1)
                hb = parsed["pipeline"][0]
                self.assertEqual(hb["name"], "heartbeat")
                self.assertEqual(hb["client"], "sigmond")
                self.assertIs(hb["governed"], False)
                self.assertNotIn("start_at", hb["source"])
                self.assertEqual(hb["transport"]["type"], "heartbeat_sftp")
        # Positive control: the part 1 form carries it in the legacy text.
        legacy, sfile = self.forms("upload", None, True)
        self.assertIn('name = "heartbeat"', legacy)
        self.assertIsNone(sfile)

    def test_heartbeat_disabled_means_no_file(self):
        legacy, sfile = self.forms("upload", GATE, True, heartbeat=False)
        self.assertIsNone(sfile)
        self.assertNotIn("heartbeat_sftp", legacy)
        self.assertNotIn("heartbeat", [p["name"] for p in
                                       tomllib.loads(legacy)["pipeline"]])

    def test_sigmond_pipeline_file_alone(self):
        text = um.sigmond_pipeline_file(drv.coordination("upload"))
        hb = tomllib.loads(text)["pipeline"][0]
        self.assertIs(hb["governed"], False)
        self.assertIsNone(um.sigmond_pipeline_file(
            drv.coordination("upload", heartbeat=False)))

    def test_gate_form_asks_no_discard_probe(self):
        """``effective_mode`` serves only the old form: the gate render never
        asks whether the venv can discard (the driver's probes raise)."""
        legacy, _ = self.forms("discard", GATE, True, supports_discard=None)
        self.assertIn("meteor-scatter-pskreporter", legacy)

    def test_failed_store_probe_raises_in_gate_form(self):
        with mock.patch.object(um, "hs_uploader_store_version",
                               return_value=None):
            with self.assertRaises(um.ProbeFailed):
                um.generate_forms(drv._Topo(drv.BODIES),
                                  drv.coordination("upload"),
                                  features=GATE, marker_stands=True)


class D13Tests(_Render):
    """D13 stays: a producer's daemon pskreporter pipeline stays out while
    its in-process sender runs, in both forms."""

    def test_gate_form_drops_inprocess_producer(self):
        legacy, _ = self.forms("upload", GATE, True, senders=[drv.PSK])
        self.assertNotIn('name = "psk-recorder-pskreporter"', legacy)
        # Positive control: the other producer stays, and without the
        # sender psk-recorder's pipeline renders.
        self.assertIn('name = "meteor-scatter-pskreporter"', legacy)
        legacy, _ = self.forms("upload", GATE, True)
        self.assertIn('name = "psk-recorder-pskreporter"', legacy)

    def test_part1_form_drops_inprocess_producer_as_before(self):
        part1 = part1_renders(self)["upload-psk-inprocess"]
        legacy, _ = self.forms("upload", None, True, senders=[drv.PSK])
        self.assertEqual(legacy, part1)
        self.assertNotIn('name = "psk-recorder-pskreporter"', legacy)


# --------------------------------------------------------------------------
# V12: the part 1 hs-uploader loader reads sigmond.toml
# --------------------------------------------------------------------------

_LOADER = r"""
import json, sys
src, manifest = sys.argv[1], sys.argv[2]
sys.path.insert(0, src)
import hs_uploader
from pathlib import Path
assert str(Path(hs_uploader.__file__).resolve()).startswith(
    str(Path(src).resolve())), hs_uploader.__file__
from hs_uploader.pipelines_d import load_layout
lay = load_layout(manifest)
out = {
    "entries": [[e.get("name"), e.get("client"), origin,
                 e.get("governed", True)] for e, origin in lay.entries],
    "refused": [[r.file, r.reason] for r in lay.refused],
}
if len(sys.argv) > 3:      # also build, as the part 1 daemon does at start
    from hs_uploader.daemon import build_layout
    from hs_uploader.watermark import SqliteWatermarkStore
    built, refused = build_layout(lay, watermark=SqliteWatermarkStore(sys.argv[3]))
    out["built"] = [[p.name, type(p.transport).__name__] for p in built]
    out["build_refused"] = [[r.file, r.reason] for r in refused]
json.dump(out, sys.stdout)
"""


class PartOneLoaderTests(_Render):
    """V12: hs-uploader 29a0806 (part 1) accepts sigmond.toml, and its
    precedence silences the legacy heartbeat (``client = "sigmond"``)."""

    def _load(self, legacy: str, sfile, build: bool = False):
        src = self.tmp / "hsu"
        src.mkdir()
        if not _archive(HSU_REPO, PART1_HSU, "src", src):
            self.skipTest(f"git or hs-uploader {PART1_HSU} unavailable")
        py = HSU_REPO / ".venv" / "bin" / "python"
        if not py.exists():
            self.skipTest("no ../hs-uploader/.venv")
        etc = self.tmp / "etc"
        (etc / "pipelines.d").mkdir(parents=True)
        (etc / "pipelines.toml").write_text(legacy)
        if sfile is not None:
            um.land_sigmond_file(sfile, etc / "pipelines.d")
        argv = [str(py), "-I", "-c", _LOADER, str(src / "src"),
                str(etc / "pipelines.toml")]
        if build:
            argv.append(str(self.tmp / "watermark.db"))
        r = subprocess.run(argv,
                           capture_output=True, text=True, cwd=str(self.tmp),
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_part1_loader_accepts_the_file(self):
        legacy, sfile = self.forms("upload", GATE, True)
        got = self._load(legacy, sfile)
        self.assertEqual(got["refused"], [])
        hb = [e for e in got["entries"] if e[0] == "heartbeat"]
        self.assertEqual(hb, [["heartbeat", "sigmond",
                               "pipelines.d/sigmond.toml", False]])

    def test_precedence_silences_the_legacy_heartbeat(self):
        """A part 1 daemon that meets the file beside a legacy manifest that
        still holds the heartbeat runs one heartbeat, the file's."""
        legacy_part1, _ = self.forms("upload", None, True)
        self.assertIn('client = "sigmond"', legacy_part1)
        _, sfile = self.forms("upload", GATE, True)
        got = self._load(legacy_part1, sfile)
        hb = [e for e in got["entries"] if e[0] == "heartbeat"]
        self.assertEqual(hb, [["heartbeat", "sigmond",
                               "pipelines.d/sigmond.toml", False]])
        self.assertEqual(got["refused"], [])

    def test_part1_daemon_builds_the_heartbeat_from_the_file(self):
        """The part 1 daemon builds the file's entry into a heartbeat_sftp
        pipeline, its key coming from the legacy manifest's [identity]."""
        _, sfile = self.forms("upload", GATE, True)
        legacy = ('[identity]\ncall = "AC0G"\nssh_key_file = "/k"\n\n'
                  '[daemon]\npump_interval_sec = 30\n')
        got = self._load(legacy, sfile, build=True)
        self.assertEqual(got["build_refused"], [])
        self.assertEqual(got["built"], [["heartbeat", "HeartbeatSftp"]])

    def test_control_without_the_file_the_legacy_heartbeat_runs(self):
        legacy_part1, _ = self.forms("upload", None, True)
        got = self._load(legacy_part1, None)
        hb = [e for e in got["entries"] if e[0] == "heartbeat"]
        self.assertEqual(hb, [["heartbeat", "sigmond", "legacy", True]])


# --------------------------------------------------------------------------
# land_sigmond_file
# --------------------------------------------------------------------------


class LandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.etc = self.tmp / "etc-hs-uploader"
        self.etc.mkdir()
        self.d = self.etc / "pipelines.d"
        self.f = self.d / "sigmond.toml"
        old = os.umask(0o077)
        self.addCleanup(os.umask, old)
        self.text = um.sigmond_pipeline_file(drv.coordination("upload"))

    def test_creates_the_directory_0755_and_the_file_0644(self):
        self.assertTrue(um.land_sigmond_file(self.text, self.d))
        self.assertEqual(stat.S_IMODE(os.lstat(self.d).st_mode), 0o755)
        self.assertTrue(stat.S_ISDIR(os.lstat(self.d).st_mode))
        self.assertEqual(stat.S_IMODE(os.lstat(self.f).st_mode), 0o644)
        self.assertEqual(self.f.read_text(), self.text)
        self.assertEqual([p.name for p in self.d.iterdir()], ["sigmond.toml"])

    def test_existing_directory_keeps_its_mode(self):
        self.d.mkdir()
        os.chmod(self.d, 0o750)
        um.land_sigmond_file(self.text, self.d)
        self.assertEqual(stat.S_IMODE(os.lstat(self.d).st_mode), 0o750)

    def test_reports_change(self):
        self.assertTrue(um.land_sigmond_file(self.text, self.d))
        self.assertFalse(um.land_sigmond_file(self.text, self.d))
        self.assertTrue(um.land_sigmond_file(self.text + "# x\n", self.d))
        os.chmod(self.f, 0o600)
        self.assertTrue(um.land_sigmond_file(self.text + "# x\n", self.d))
        self.assertEqual(stat.S_IMODE(os.lstat(self.f).st_mode), 0o644)

    def test_writes_atomically_by_rename(self):
        """A rewrite replaces the file (a new inode), never edits it in
        place, and leaves no temporary file behind."""
        um.land_sigmond_file(self.text, self.d)
        ino = os.lstat(self.f).st_ino
        keep = self.d / "hold-old-inode"
        os.link(self.f, keep)       # pin the old inode so it cannot recycle
        um.land_sigmond_file(self.text + "# changed\n", self.d)
        self.assertNotEqual(os.lstat(self.f).st_ino, ino)
        self.assertEqual(keep.read_text(), self.text)   # the old one untouched
        keep.unlink()
        self.assertEqual(sorted(p.name for p in self.d.iterdir()),
                         ["sigmond.toml"])

    def test_writes_through_write_owned(self):
        with mock.patch.object(um.safe_write, "write_owned",
                               wraps=um.safe_write.write_owned) as w:
            um.land_sigmond_file(self.text, self.d)
        w.assert_called_once()
        self.assertEqual(Path(w.call_args.args[0]), self.f)
        self.assertEqual(w.call_args.kwargs.get("mode"), 0o644)

    def test_none_removes(self):
        um.land_sigmond_file(self.text, self.d)
        self.assertTrue(um.land_sigmond_file(None, self.d))
        self.assertFalse(self.f.exists())
        self.assertTrue(self.d.is_dir())
        self.assertFalse(um.land_sigmond_file(None, self.d))

    def test_none_with_no_directory_creates_nothing(self):
        self.assertFalse(um.land_sigmond_file(None, self.d))
        self.assertFalse(self.d.exists())

    def test_symlink_at_target_refuses(self):
        self.d.mkdir()
        victim = self.tmp / "victim"
        victim.write_text("secret\n")
        os.symlink(victim, self.f)
        with self.assertRaises(OSError):
            um.land_sigmond_file(self.text, self.d)
        self.assertEqual(victim.read_text(), "secret\n")
        self.assertTrue(self.f.is_symlink())
        # Positive control: with the link gone, the same call writes.
        self.f.unlink()
        self.assertTrue(um.land_sigmond_file(self.text, self.d))
        self.assertFalse(self.f.is_symlink())

    def test_symlink_at_directory_refuses(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        os.symlink(elsewhere, self.d)
        for text in (self.text, None):
            with self.subTest(text=text is not None):
                with self.assertRaises(OSError):
                    um.land_sigmond_file(text, self.d)
        self.assertEqual(list(elsewhere.iterdir()), [])
        (elsewhere / "sigmond.toml").write_text("kept\n")
        with self.assertRaises(OSError):
            um.land_sigmond_file(None, self.d)
        self.assertEqual((elsewhere / "sigmond.toml").read_text(), "kept\n")

    def test_as_root_gives_the_new_directory_to_root_sigmond(self):
        """As root, a new pipelines.d goes to root:sigmond by fchown on its
        descriptor, never by path."""
        calls = []
        real_fchown = os.fchown

        def fchown(fd, uid, gid):
            calls.append((stat.S_ISDIR(os.fstat(fd).st_mode), uid, gid))

        grp_entry = mock.Mock(gr_gid=4242)
        with mock.patch.object(um.os, "geteuid", return_value=0), \
             mock.patch.object(um.os, "fchown", side_effect=fchown), \
             mock.patch.object(um.os, "chown",
                               side_effect=AssertionError("chown by path")), \
             mock.patch.object(um.grp, "getgrnam", return_value=grp_entry):
            um.land_sigmond_file(self.text, self.d)
        self.assertIs(os.fchown, real_fchown)
        self.assertIn((True, 0, 4242), calls)


if __name__ == "__main__":
    unittest.main()

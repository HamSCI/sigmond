"""The paths sigmond shares with hs-uploader (Task 27 M-1, Task 22 M-4).

sigmond writes the switch files, reads and moves the carry-over marker, and
lands ``pipelines.d/sigmond.toml``; hs-uploader reads all three.  Each side
names the paths itself, so a drift on either side leaves sigmond writing
where the daemon never looks.  ``HsUploaderPathTests`` asks the branch
hs-uploader beside this checkout for its own values, in a subprocess, under
its default state directory, and compares.  ``RestoreFallbackTests`` pins the
production paths a restore below part 2 hands its mover and its lander.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "tests"))

from sigmond import sink_state as ss
from sigmond import uploader_manifest as um

from test_sink_state import real_python

PROBE = r"""
import json
from hs_uploader import daemon, pipelines_d, sinkswitch
manifest = daemon._DEFAULT_MANIFEST
print(json.dumps({
    "manifest": manifest,
    "sinks": str(sinkswitch.DEFAULT_SINKS_DIR),
    "sinks_for_manifest": str(pipelines_d.sinks_dir_for(manifest)),
    "marker": str(sinkswitch.marker_path()),
    "pipelines_d": str(pipelines_d.pipelines_dir_for(manifest)),
}))
"""


class HsUploaderPathTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        py = real_python()
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "HS_UPLOADER_STATE_DIR")}
        r = subprocess.run([str(py), "-P", "-c", PROBE], env=env,
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError(f"hs-uploader path probe failed: {r.stderr}")
        cls.hsu = json.loads(r.stdout)

    def test_sinks_dir(self):
        self.assertEqual(ss.SINKS_DIR, Path(self.hsu["sinks"]))
        self.assertEqual(ss.SINKS_DIR, Path(self.hsu["sinks_for_manifest"]))

    def test_marker_path(self):
        self.assertEqual(ss.MARKER_PATH, Path(self.hsu["marker"]))

    def test_pipelines_d(self):
        self.assertEqual(um.PIPELINES_D, Path(self.hsu["pipelines_d"]))

    def test_manifest_path(self):
        self.assertEqual(um.MANIFEST_PATH, Path(self.hsu["manifest"]))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    name = "smd_under_test_sink_paths"
    loader = importlib.machinery.SourceFileLoader(name, str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class RestoreFallbackTests(unittest.TestCase):
    """With both test globals None, a restore below part 2 hands the mover
    sink_state's MARKER_PATH and the lander uploader_manifest's PIPELINES_D
    (which HsUploaderPathTests pins against hs-uploader).  Both are mocked:
    nothing here touches this host's /var/lib or /etc."""

    def test_production_paths_reach_the_mover_and_the_lander(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            stub = base / "sigmond" / "bin" / "smd"
            stub.parent.mkdir(parents=True)
            stub.write_text("")              # no sink_carryover.py: below part 2
            with mock.patch.object(smd, "_RESTORE_MARKER_PATH", None), \
                    mock.patch.object(smd, "_RESTORE_PIPELINES_D", None), \
                    mock.patch.object(ss, "move_marker_aside",
                                      return_value=None) as mover, \
                    mock.patch.object(um, "land_sigmond_file",
                                      return_value=False) as lander:
                rc = smd._restore_below_part2(base, smd_path=stub)
        self.assertEqual(rc, 0)
        mover.assert_called_once()
        lander.assert_called_once()
        self.assertEqual(mover.call_args.args[0],
                         Path("/var/lib/hs-uploader/carry-over-v1.done"))
        self.assertEqual(mover.call_args.args[0], ss.MARKER_PATH)
        self.assertEqual(lander.call_args.args,
                         (None, Path("/etc/hs-uploader/pipelines.d")))
        self.assertEqual(lander.call_args.args[1], um.PIPELINES_D)


if __name__ == "__main__":
    unittest.main()

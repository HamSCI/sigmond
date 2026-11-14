"""smd watch hfdl / codar: choose the instance that holds data, and count
only real transmitter directories."""
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_watch_pick", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class _Tree(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        # No per-instance config: the data dir is named for the instance.
        p = mock.patch.object(smd, "_radiod_status_from_per_instance_config",
                              lambda client, inst: None)
        p.start()
        self.addCleanup(p.stop)

    def hfdl(self, name, mtime=None, band="HFDL8.json"):
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        if band:
            f = d / band
            f.write_text("{}\n")
            if mtime:
                os.utime(f, (mtime, mtime))


class HfdlPickTests(_Tree):
    def pick(self, instances):
        return smd._pick_instance_with_data(
            "hfdl-recorder", instances, "HFDL*.json", root=self.root)

    def test_stray_default_with_empty_dir_loses(self):
        self.hfdl("default", band=None)
        self.hfdl("N8UR-status.local", 1000)
        chosen, others = self.pick(["default", "N8UR-status.local"])
        self.assertEqual(chosen, "N8UR-status.local")
        self.assertEqual(others, [])

    def test_default_ignored_even_with_data_when_another_qualifies(self):
        self.hfdl("default", 5000)
        self.hfdl("A", 1000)
        self.assertEqual(self.pick(["default", "A"])[0], "A")

    def test_most_recent_wins_and_others_named(self):
        self.hfdl("A", 1000)
        self.hfdl("B", 2000)
        self.hfdl("C", 500)
        chosen, others = self.pick(["A", "B", "C"])
        self.assertEqual(chosen, "B")
        self.assertEqual(sorted(others), ["A", "C"])

    def test_default_alone_with_data_is_used(self):
        self.hfdl("default", 1000)
        self.assertEqual(self.pick(["default"])[0], "default")

    def test_nobody_has_data_prefers_non_default_first_active(self):
        self.hfdl("default", band=None)
        self.hfdl("A", band=None)
        self.assertEqual(self.pick(["default", "A"]), ("A", []))

    def test_per_instance_config_status_names_the_data_dir(self):
        self.hfdl("N8UR-status.local", 1000)
        with mock.patch.object(smd, "_radiod_status_from_per_instance_config",
                               lambda c, i: "N8UR-status.local"):
            self.assertEqual(self.pick(["AC0G-B4"])[0], "AC0G-B4")
            self.hfdl("default", band=None)
            self.assertEqual(self.pick(["default", "AC0G-B4"])[0], "AC0G-B4")


class CodarStationTests(unittest.TestCase):
    def test_phase_and_dot_dirs_are_not_stations(self):
        with tempfile.TemporaryDirectory() as t:
            for n in ("phase", ".cache", "BLR", "CDR", "NCC", "SEA"):
                (Path(t) / n).mkdir()
            (Path(t) / "stray-file").write_text("x")
            self.assertEqual(smd._codar_station_dirs(Path(t)),
                             ["BLR", "CDR", "NCC", "SEA"])


class CodarPickTests(_Tree):
    def test_prefers_instance_with_jsonl(self):
        a = self.root / "default" / "BLR" / "2026" / "11"
        a.mkdir(parents=True)
        b = self.root / "R1" / "BLR" / "2026" / "11"
        b.mkdir(parents=True)
        (b / "14.jsonl").write_text("{}\n")
        self.assertEqual(
            smd._pick_instance_with_data(
                "codar-sounder", ["default", "R1"], "*.jsonl",
                root=self.root, recursive=True), ("R1", []))

    def test_phase_series_files_do_not_count_as_station_data(self):
        p = self.root / "R1" / "phase" / "g"
        p.mkdir(parents=True)
        (p / "x.jsonl").write_text("{}\n")
        s = self.root / "R2" / "BLR" / "2026"
        s.mkdir(parents=True)
        (s / "14.jsonl").write_text("{}\n")
        self.assertEqual(
            smd._pick_instance_with_data(
                "codar-sounder", ["R1", "R2"], "*.jsonl",
                root=self.root, recursive=True)[0], "R2")


if __name__ == "__main__":
    unittest.main()

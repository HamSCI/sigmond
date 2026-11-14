"""Bare metal with one shared L3 carries an L3 CAT partition for radiod
(sigmond-l3-cat.service), installed for the next boot and never applied to a
running station (VK5ARG, Ryzen 7 PRO 5850U, 2026-11-14)."""
import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_l3_cat", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_l3_cat", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

ONE_L3 = [SimpleNamespace(level=3, cache_type="Unified", cpus=frozenset(range(16)))]
TWO_L3 = [SimpleNamespace(level=3, cache_type="Unified", cpus=frozenset(range(8))),
          SimpleNamespace(level=3, cache_type="Unified", cpus=frozenset(range(8, 16)))]


class Topology(unittest.TestCase):
    def _load(self, text):
        from sigmond.topology import load_topology
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(text)
        return load_topology(Path(f.name))

    def test_enabled_by_default(self):
        t = self._load("")
        self.assertTrue(t.l3_cat["enabled"])
        self.assertEqual(t.l3_cat["radiod_fraction"], 0.62)

    def test_operator_opt_out(self):
        self.assertFalse(self._load("[l3_cat]\nenabled = false\n").l3_cat["enabled"])

    def test_fraction_outside_zero_one_is_ignored(self):
        self.assertEqual(self._load("[l3_cat]\nradiod_fraction = 1.5\n")
                         .l3_cat["radiod_fraction"], 0.62)
        self.assertEqual(self._load("[l3_cat]\nradiod_fraction = 0.8\n")
                         .l3_cat["radiod_fraction"], 0.8)


class Wanted(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.cpuinfo = self.root / "cpuinfo"
        self.cpuinfo.write_text("processor\t: 0\nflags\t\t: fpu sse2 rdt_a cat_l3 mba\n")
        p = mock.patch.object(smd, "_PVE_DIR", self.root / "pve")
        p.start(); self.addCleanup(p.stop)
        self.virt = "none"
        self.wd_resctrl = "not-found"

    def run_(self, argv, **kw):
        if argv[:1] == ["systemd-detect-virt"]:
            return subprocess.CompletedProcess(argv, 0 if self.virt != "none" else 1,
                                               self.virt + "\n", "")
        if argv[:2] == ["systemctl", "is-enabled"]:
            return subprocess.CompletedProcess(argv, 0, self.wd_resctrl + "\n", "")
        raise AssertionError(f"unexpected command {argv}")

    def wanted(self, topo=None, islands=ONE_L3):
        return smd._l3_cat_wanted(topo or {"l3_cat": {}}, cpuinfo=self.cpuinfo,
                                  l3_islands=islands, run=self.run_)

    def test_bare_metal_one_l3_wants_it(self):
        self.assertEqual(self.wanted(), (True, ""))

    def test_opt_out_wins(self):
        self.assertFalse(self.wanted({"l3_cat": {"enabled": False}})[0])

    def test_vm_guest_does_not(self):
        self.virt = "kvm"
        wanted, why = self.wanted()
        self.assertFalse(wanted)
        self.assertIn("kvm guest", why)

    def test_proxmox_host_does_not(self):
        (self.root / "pve").mkdir()
        self.assertFalse(self.wanted()[0])

    def test_cpu_without_cat_does_not(self):
        self.cpuinfo.write_text("flags\t\t: fpu sse2\n")
        self.assertIn("no L3 CAT", self.wanted()[1])

    def test_split_l3_does_not(self):
        wanted, why = self.wanted(islands=TWO_L3)
        self.assertFalse(wanted)
        self.assertIn("2 L3 domains", why)

    def test_wsprdaemon_resctrl_still_enabled_does_not(self):
        self.wd_resctrl = "enabled"
        self.assertIn("wd-resctrl", self.wanted()[1])


class Unit(unittest.TestCase):
    def test_runs_the_host_script_before_the_network(self):
        text = smd._l3_cat_unit_text("14-15", "0-13", 0.62)
        self.assertIn("ExecStart=/usr/local/sbin/sigmond-host-resctrl.sh 14-15 0-13 0.62\n", text)
        self.assertIn("Before=network-pre.target", text)
        self.assertIn("DefaultDependencies=no", text)
        self.assertIn("RemainAfterExit=yes", text)

    def test_script_source_is_the_proxmox_one(self):
        self.assertTrue(smd._L3_CAT_SCRIPT_SRC.is_file())
        self.assertEqual(smd._L3_CAT_SCRIPT_SRC.name, "sigmond-host-resctrl.sh")


class Apply(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.unit = self.root / "sigmond-l3-cat.service"
        self.script = self.root / "sigmond-host-resctrl.sh"
        self.calls = []
        self.enabled = False
        for name, val in (("_L3_CAT_UNIT_PATH", self.unit),
                          ("_L3_CAT_SCRIPT", self.script),
                          ("_RESCTRL", self.root / "resctrl")):
            p = mock.patch.object(smd, name, val)
            p.start(); self.addCleanup(p.stop)
        for name, rv in (("_l3_cat_wanted", (True, "")),
                         ("_l3_cat_cpus", ("14-15", "0-13")),
                         ("_need_root", False)):
            p = mock.patch.object(smd, name, return_value=rv)
            p.start(); self.addCleanup(p.stop)
        for name in ("_info", "_ok", "_warn", "_heading"):
            p = mock.patch.object(smd, name)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(smd, "_run", side_effect=self.run_)
        p.start(); self.addCleanup(p.stop)

    def run_(self, argv, **kw):
        self.calls.append(argv)
        if argv[:2] == ["systemctl", "is-enabled"]:
            return subprocess.CompletedProcess(argv, 0, "enabled\n" if self.enabled
                                               else "disabled\n", "")
        if argv[:2] == ["systemctl", "enable"]:
            self.enabled = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    def apply(self, topo=None):
        ns = SimpleNamespace(apply=True, topology=None,
                             _topology_override=topo or {"l3_cat": {"radiod_fraction": 0.62}})
        return smd.cmd_diag_l3_cat(ns)

    def test_installs_script_and_unit_and_enables_it(self):
        self.assertEqual(self.apply(), 0)
        self.assertIn("14-15 0-13 0.62", self.unit.read_text())
        self.assertEqual(self.script.read_bytes(), smd._L3_CAT_SCRIPT_SRC.read_bytes())
        self.assertTrue(os.access(self.script, os.X_OK))
        self.assertIn(["systemctl", "enable", "sigmond-l3-cat.service"], self.calls)

    def test_never_starts_or_restarts_it(self):
        self.apply()
        self.apply()
        for argv in self.calls:
            self.assertNotIn("start", argv)
            self.assertNotIn("restart", argv)

    def test_second_apply_changes_nothing(self):
        self.apply()
        n = len(self.calls)
        mtime = self.unit.stat().st_mtime_ns
        self.apply()
        self.assertEqual(self.unit.stat().st_mtime_ns, mtime)
        self.assertNotIn(["systemctl", "daemon-reload"], self.calls[n:])

    def test_ensure_is_a_noop_once_current(self):
        topo = {"l3_cat": {"radiod_fraction": 0.62}}
        self.assertTrue(smd._ensure_l3_cat(topo))
        self.assertFalse(smd._ensure_l3_cat(topo))

    def test_ensure_rewrites_when_radiod_moves(self):
        topo = {"l3_cat": {}}
        smd._ensure_l3_cat(topo)
        with mock.patch.object(smd, "_l3_cat_cpus", return_value=("2-3", "0-1,4-15")):
            self.assertTrue(smd._ensure_l3_cat(topo))
        self.assertIn("2-3 0-1,4-15", self.unit.read_text())

    def test_no_local_radiod_installs_nothing(self):
        with mock.patch.object(smd, "_l3_cat_cpus", return_value=("", "")):
            self.assertFalse(smd._ensure_l3_cat({"l3_cat": {}}))
        self.assertFalse(self.unit.exists())


class Active(unittest.TestCase):
    def test_reads_the_running_partition(self):
        r = Path(tempfile.mkdtemp())
        (r / "radiod").mkdir()
        (r / "radiod" / "cpus_list").write_text("14-15\n")
        (r / "radiod" / "schemata").write_text("    L3:0=3ff\n    MB:0=2048\n")
        self.assertEqual(smd._l3_cat_active(r), "radiod 14-15 L3:0=3ff")

    def test_none_without_resctrl(self):
        self.assertEqual(smd._l3_cat_active(Path(tempfile.mkdtemp())), "")


if __name__ == "__main__":
    unittest.main()

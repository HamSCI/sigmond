"""Bare metal applies the [cpu_freq] clock policy by default; diag cpu-freq
reports amd-pstate's real floor and reads back a settled value (rob and the
KJ6MKI measurements, 2026-11-12)."""
import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_cpu_freq_default", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_cpu_freq_default", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class Topology(unittest.TestCase):
    def _load(self, text):
        from sigmond.topology import load_topology
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(text)
        return load_topology(Path(f.name))

    def test_enabled_by_default(self):
        self.assertTrue(self._load("").cpu_freq["enabled"])
        self.assertEqual(self._load("").cpu_freq["radiod_max_mhz"], 3200)

    def test_operator_opt_out(self):
        self.assertFalse(self._load("[cpu_freq]\nenabled = false\n").cpu_freq["enabled"])


class Wanted(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.cpu = self.root / "cpu"
        p1 = mock.patch.object(smd, "_CPU_SYSFS", self.cpu)
        p2 = mock.patch.object(smd, "_PVE_DIR", self.root / "pve")
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        self.virt = "none"
        real_run = smd._run

        def run(argv, **kw):
            if argv[:1] == ["systemd-detect-virt"]:
                return mock.Mock(returncode=0 if self.virt != "none" else 1,
                                 stdout=self.virt + "\n", stderr="")
            return real_run(argv, **kw)
        p3 = mock.patch.object(smd, "_run", side_effect=run)
        p3.start(); self.addCleanup(p3.stop)

    def test_guest_with_cpufreq_exposed_does_not(self):
        (self.cpu / "cpu0" / "cpufreq").mkdir(parents=True)
        self.virt = "kvm"
        wanted, why = smd._cpu_freq_policy_wanted({"cpu_freq": {}})
        self.assertFalse(wanted)
        self.assertIn("kvm guest", why)

    def test_bare_metal_wants_it(self):
        (self.cpu / "cpu0" / "cpufreq").mkdir(parents=True)
        self.assertEqual(smd._cpu_freq_policy_wanted({"cpu_freq": {}})[0], True)

    def test_guest_without_cpufreq_does_not(self):
        wanted, why = smd._cpu_freq_policy_wanted({"cpu_freq": {}})
        self.assertFalse(wanted)
        self.assertIn("VM guest", why)

    def test_proxmox_host_does_not(self):
        (self.cpu / "cpu0" / "cpufreq").mkdir(parents=True)
        (self.root / "pve").mkdir()
        self.assertFalse(smd._cpu_freq_policy_wanted({"cpu_freq": {}})[0])

    def test_opt_out_wins(self):
        (self.cpu / "cpu0" / "cpufreq").mkdir(parents=True)
        self.assertFalse(smd._cpu_freq_policy_wanted({"cpu_freq": {"enabled": False}})[0])

    def test_ensure_applies_once(self):
        (self.cpu / "cpu0" / "cpufreq").mkdir(parents=True)
        state = {"enabled": False}
        def run(argv, **kw):
            if argv[:1] == ["systemd-detect-virt"]:
                return subprocess.CompletedProcess(argv, 1, "none\n", "")
            out = "enabled\n" if state["enabled"] else "disabled\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        def apply(ns):
            assert ns.apply and ns._topology_override == {"cpu_freq": {}}
            state["enabled"] = True
            return 0
        with mock.patch.object(smd, "_run", side_effect=run), \
             mock.patch.object(smd, "cmd_diag_cpu_freq", side_effect=apply) as a, \
             mock.patch.object(smd, "_info"):
            self.assertTrue(smd._ensure_cpu_freq_policy({"cpu_freq": {}}))
            self.assertFalse(smd._ensure_cpu_freq_policy({"cpu_freq": {}}))   # idempotent
        self.assertEqual(a.call_count, 1)


class Readback(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()) / "cpu9" / "cpufreq"
        self.d.mkdir(parents=True)

    def test_nominal_floor_on_amd_pstate(self):
        (self.d / "scaling_driver").write_text("amd-pstate-epp\n")
        (self.d / "amd_pstate_nominal_freq").write_text("2420000\n")
        self.assertEqual(smd._cpufreq_nominal_khz(self.d), 2420000)

    def test_nominal_from_acpi_cppc(self):
        (self.d / "scaling_driver").write_text("amd-pstate\n")
        (self.d.parent / "acpi_cppc").mkdir()
        (self.d.parent / "acpi_cppc" / "nominal_freq").write_text("2400\n")
        self.assertEqual(smd._cpufreq_nominal_khz(self.d), 2400000)

    def test_no_floor_on_other_drivers(self):
        (self.d / "scaling_driver").write_text("acpi-cpufreq\n")
        (self.d / "amd_pstate_nominal_freq").write_text("2420000\n")
        self.assertEqual(smd._cpufreq_nominal_khz(self.d), 0)

    def test_settled_readback_waits_for_the_driver(self):
        f = self.d / "scaling_max_freq"
        reads = iter(["4062000", "4062000", "1400000"])
        orig = Path.read_text
        def rt(self_, *a, **k):
            return next(reads) if self_ == f else orig(self_, *a, **k)
        with mock.patch.object(Path, "read_text", rt), mock.patch.object(smd.time, "sleep"):
            self.assertEqual(smd._read_settled_khz(f, 1400000), 1400000)

    def test_governor_change_precedes_the_cap_write(self):
        src = (REPO / "bin" / "smd").read_text()
        i = src.index("def cmd_diag_cpu_freq(")
        body = src[i:src.index("\ndef ", i + 10)]
        self.assertLess(body.index("if not is_fast and cur_gov == 'performance':"),
                        body.index("(freq_dir / 'scaling_max_freq').write_text(str(applied_khz))"))


class Wiring(unittest.TestCase):
    def test_apply_and_align_call_it(self):
        src = (REPO / "bin" / "smd").read_text()
        i = src.index("def cmd_apply(")
        self.assertIn("_ensure_cpu_freq_policy(topology)", src[i:src.index("\ndef ", i + 10)])
        j = src.index("def _align_bringup(")
        self.assertIn("'cpu-freq',", src[j:src.index("\ndef ", j + 10)])



class RadiodCoresBeforeTheirDropIns(unittest.TestCase):
    """`smd apply` runs the clock policy before it reserves radiod's cores:
    with no radiod CPUAffinity yet, every CPU got the other-CPU cap and
    smd-cpu-freq.service kept later applies from fixing it (VK5ARG,
    2026-11-14).  The affinity plan is the fallback."""

    def _plan(self, radiod):
        return mock.Mock(radiod=radiod)

    def test_live_cpu_affinity_wins(self):
        with mock.patch.object(smd, "_get_radiod_cpus_by_unit",
                               return_value={"radiod@a.service": {14, 15}}), \
             mock.patch("sigmond.cpu.compute_affinity_plan") as plan:
            got, src = smd._radiod_cpus_for_clock_policy({"cpu_affinity": {}})
        self.assertEqual(got, {"radiod@a.service": {14, 15}})
        self.assertEqual(src, "CPUAffinity")
        plan.assert_not_called()

    def test_falls_back_to_the_affinity_plan(self):
        with mock.patch.object(smd, "_get_radiod_cpus_by_unit", return_value={}), \
             mock.patch("sigmond.cpu.compute_affinity_plan",
                        return_value=self._plan({"radiod@a.service": {14, 15}})) as plan:
            got, src = smd._radiod_cpus_for_clock_policy({"cpu_affinity": {"x": 1}})
        self.assertEqual(got, {"radiod@a.service": {14, 15}})
        self.assertIn("affinity plan", src)
        plan.assert_called_once_with({"x": 1})

    def test_no_radiod_anywhere_is_empty(self):
        with mock.patch.object(smd, "_get_radiod_cpus_by_unit", return_value={}), \
             mock.patch("sigmond.cpu.compute_affinity_plan",
                        return_value=self._plan({})):
            self.assertEqual(smd._radiod_cpus_for_clock_policy({}), ({}, ""))

    def test_a_failing_plan_is_empty_not_a_crash(self):
        with mock.patch.object(smd, "_get_radiod_cpus_by_unit", return_value={}), \
             mock.patch("sigmond.cpu.compute_affinity_plan", side_effect=OSError("no sysfs")):
            self.assertEqual(smd._radiod_cpus_for_clock_policy({}), ({}, ""))

    def test_planned_cores_become_the_fast_set(self):
        cores = [{2 * i, 2 * i + 1} for i in range(8)]
        with mock.patch.object(smd, "_get_physical_cores", return_value=cores):
            clock = smd._clock_plan({"cpu_freq": {}}, {"radiod@a.service": {14, 15}})
        self.assertEqual(set(clock.fast_cpus), {14, 15})

    def test_cmd_uses_the_fallback(self):
        src = (REPO / "bin" / "smd").read_text()
        i = src.index("def cmd_diag_cpu_freq(")
        body = src[i:src.index("\ndef ", i + 10)]
        self.assertIn("_radiod_cpus_for_clock_policy(topology)", body)
        self.assertNotIn("radiod_by_unit = _get_radiod_cpus_by_unit()", body)


if __name__ == "__main__":
    unittest.main()

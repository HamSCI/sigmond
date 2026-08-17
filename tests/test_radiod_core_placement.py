"""bin/smd's radiod drop-in writer must use the shared placement rule.

`_ensure_radiod_affinity_drop_ins` is the writer used by `smd apply` and
`smd start` (the `admin diag cpu-affinity --apply` path goes through
`compute_affinity_plan` instead).  It used to assign `cores[i]` directly —
so it handed radiod physical core 0 and ignored `[cpu_affinity].radiod_cpus`
entirely, meaning a single `smd apply` would silently revert a host that had
been moved off core 0.  The kernel refuses to tick-isolate CPU 0, so that
placement is never correct.  See sigmond.cpu.assign_radiod_cores.
"""
import importlib.machinery
import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_placement", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_placement", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

EIGHT_CORES = [{0, 1}, {2, 3}, {4, 5}, {6, 7},
               {8, 9}, {10, 11}, {12, 13}, {14, 15}]


class _UnitRef:
    def __init__(self, unit, instance=None):
        self.unit = unit
        self.instance = instance


class RadiodDropInPlacementTests(unittest.TestCase):
    def _written(self, units, ca=None, cores=None, isolated=()):
        """Run the writer with the filesystem and systemd stubbed out;
        return {unit: cpu_set} that it tried to write."""
        calls = {}

        def fake_write(unit, cpus, label):
            calls[unit] = set(cpus)
            return 'unchanged'          # no restart bookkeeping

        with mock.patch('sigmond.cpu.get_physical_cores',
                        return_value=cores or EIGHT_CORES), \
             mock.patch('sigmond.cpu.get_isolated_cpus',
                        return_value=set(isolated)), \
             mock.patch.object(smd, '_write_affinity_drop_in', fake_write), \
             mock.patch.object(smd, '_run'), \
             mock.patch.object(Path, 'glob', return_value=[]):
            smd._ensure_radiod_affinity_drop_ins(units, ca)
        return calls

    def test_does_not_write_core_zero(self):
        got = self._written([_UnitRef('radiod@rx.service', 'rx')])
        self.assertNotIn(0, got['radiod@rx.service'])

    def test_writes_the_highest_eligible_core(self):
        got = self._written([_UnitRef('radiod@rx.service', 'rx')])
        self.assertEqual(got['radiod@rx.service'], {14, 15})

    def test_honors_the_topology_override(self):
        got = self._written([_UnitRef('radiod@rx.service', 'rx')],
                            ca={'radiod_cpus': '12-13'})
        self.assertEqual(got['radiod@rx.service'], {12, 13})

    def test_respects_kernel_isolation(self):
        got = self._written([_UnitRef('radiod@rx.service', 'rx')],
                            isolated=range(8))
        self.assertEqual(got['radiod@rx.service'], {6, 7})

    def test_two_instances_get_distinct_top_cores(self):
        got = self._written([_UnitRef('radiod@a.service', 'a'),
                             _UnitRef('radiod@b.service', 'b')])
        self.assertEqual(got['radiod@a.service'], {12, 13})
        self.assertEqual(got['radiod@b.service'], {14, 15})


HOOKSCRIPT_SEQUENTIAL = """\
VMID=100
RADIOD_CPUS=(12 13)
RADIOD_FREQ_KHZ=3200000
WORKER_CPUS=(0 1 2 3 4 5 6 7 8 9 10 11)
WORKER_FREQ_KHZ=1400000
VCPU_TO_PCPU=(0 1 2 3 4 5 6 7 8 9 10 11 12 13)
"""

# Split-HT host: guest vCPU 0 -> host pCPU 0, but radiod is on {6,14}.
HOOKSCRIPT_SPLIT = """\
VMID=100
RADIOD_CPUS=(6 14)
WORKER_CPUS=(0 8 1 9 2 10 3 11 4 12 5 13)
VCPU_TO_PCPU=(0 8 1 9 2 10 3 11 4 12 5 13 6 14)
"""

HOOKSCRIPT_NO_MAP = """\
VMID=100
RADIOD_CPUS=(12 13)
WORKER_CPUS=(0 1 2 3 4 5 6 7 8 9 10 11)
"""


class DiscoverPcpuForVcpu0Tests(unittest.TestCase):
    """`_discover_pcpu_for_vcpu0` feeds `_wisdom_boost_cpu0`, which rewrites
    that pCPU's scaling_max_freq.  It used to read RADIOD_CPUS[0], which only
    equalled vCPU 0's pCPU while radiod sat on the FIRST VM pair.  With radiod
    on the top pair that returns radiod's own core, so the wisdom run would
    disturb the one CPU whose min==max pin must not move.  VCPU_TO_PCPU[0] is
    the actual answer and the hookscript already carries it.
    """

    def _discover(self, hookscript):
        import subprocess as _sp
        fake = _sp.CompletedProcess(args=[], returncode=0, stdout=hookscript)
        with mock.patch.object(smd, '_ssh_proxmox_host', return_value=fake):
            return smd._discover_pcpu_for_vcpu0({'VMID': '100'})

    def test_returns_vcpu0_pcpu_not_radiods_core(self):
        self.assertEqual(self._discover(HOOKSCRIPT_SEQUENTIAL), 0)

    def test_split_host_maps_vcpu0_correctly(self):
        self.assertEqual(self._discover(HOOKSCRIPT_SPLIT), 0)

    def test_missing_map_degrades_to_none(self):
        # Better to report "couldn't read it" than to boost the wrong core.
        self.assertIsNone(self._discover(HOOKSCRIPT_NO_MAP))


if __name__ == '__main__':
    unittest.main()

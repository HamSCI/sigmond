"""Kernel isolation only where sigmond owns the machine (spec 4.1, F16).

A ham's own machine boots the way its owner set it up.  On a repo install,
`smd apply` prints the isolcpus/nohz_full change it would make and leaves GRUB
alone, unless topology.toml says `[cpu_affinity] write_kernel_isolation = true`.
The image keeps writing its drop-in.  AC0G-B1 is a repo install whose isolation
sigmond wrote earlier; that drop-in counts as the operator's opt-in and stays
current, never deleted (controller ruling, 2026-11-05).
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sigmond import provenance
from sigmond.cpu import render_guest_isolation

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_isol", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_isol", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _Harness(unittest.TestCase):
    def setUp(self):
        self.smd = _load_smd()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'sigmond-cpu-isolation.cfg'
        self.calls = []

    def run_ensure(self, appliance, topo=None, want=frozenset({6, 7})):
        def fake_run(cmd, *a, **kw):
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, '', '')
        with mock.patch('sigmond.cpu.GUEST_ISOLATION_PATH', self.path), \
             mock.patch('sigmond.cpu.is_hypervisor_host', return_value=False), \
             mock.patch('sigmond.cpu.compute_affinity_plan', return_value=object()), \
             mock.patch('sigmond.cpu.recommended_isolcpus', return_value=set(want)), \
             mock.patch('sigmond.cpu.guest_isolation_reboot_pending', return_value=False), \
             mock.patch('sigmond.provenance.is_appliance_install', create=True,
                        return_value=appliance), \
             mock.patch('subprocess.run', side_effect=fake_run):
            return self.smd._ensure_guest_cpu_isolation(topo)

    def grub_runs(self):
        return [c for c in self.calls if c and c[0] in ('update-grub', 'grub-mkconfig')]


class RepoInstall(_Harness):

    def test_a_repo_install_prints_isolation_and_leaves_grub_alone(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            changed = self.run_ensure(appliance=False)
        out = buf.getvalue()
        self.assertFalse(changed)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.grub_runs(), [])
        self.assertIn('isolcpus=6-7', out)
        self.assertIn('nohz_full=6-7', out)
        self.assertIn('write_kernel_isolation', out)

    def test_the_topology_opt_in_writes_on_a_repo_install(self):
        changed = self.run_ensure(appliance=False,
                                  topo={'write_kernel_isolation': True})
        self.assertTrue(changed)
        self.assertEqual(self.path.read_text(), render_guest_isolation({6, 7}))
        self.assertEqual(len(self.grub_runs()), 1)

    def test_an_existing_drop_in_on_a_repo_install_is_kept_current(self):
        self.path.write_text(render_guest_isolation({15}))
        self.assertIn('isolcpus=15', self.path.read_text())
        changed = self.run_ensure(appliance=False)
        self.assertTrue(changed)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.read_text(), render_guest_isolation({6, 7}))
        self.assertEqual(len(self.grub_runs()), 1)


class ImageInstall(_Harness):

    def test_an_image_install_still_writes_the_drop_in(self):
        changed = self.run_ensure(appliance=True)
        self.assertTrue(changed)
        self.assertEqual(self.path.read_text(), render_guest_isolation({6, 7}))
        self.assertEqual(len(self.grub_runs()), 1)


class ApplianceSignal(unittest.TestCase):

    def test_is_appliance_install_reads_the_image_version_file(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(provenance.is_appliance_install(root))
            d = Path(root) / 'etc' / 'sigmond-appliance'
            d.mkdir(parents=True)
            (d / 'version').write_text('v3.85\n')
            self.assertTrue(provenance.is_appliance_install(root))


if __name__ == '__main__':
    unittest.main()

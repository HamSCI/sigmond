"""radiod's FFTW-version wisdom slot (KA9Q's convention), FFTW version
checks on wisdom and bundles, and hands-off where an operator owns radiod
(VK5ARG / KFS, 2026-11-14)."""
import ctypes
import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sigmond import wisdom
from sigmond import wisdom_slot as ws

REPO = Path(__file__).resolve().parent.parent
V8 = 'fftw-3.3.8-sse2-avx'
V11 = 'fftw-3.3.11-sse2-avx-avx2-avx2_128'


def _wisdom(path: Path, ver='3.3.8', body='(fftwf_codelet_n1fv_12_sse2 0 #x1 #x1 #x0 #x1 #x2 #x3 #x4)'):
    path.write_text(f'(fftw-{ver} fftwf_wisdom #x706526c0 #x2f8b6c85 #x8cd1bb1a #x7c96e03d\n  {body}\n)\n')
    return path


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_wisdom_slot", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_wisdom_slot", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Versions(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_header_version(self):
        self.assertEqual(ws.header_version(_wisdom(self.d / 'w', '3.3.10')), '3.3.10')
        (self.d / 'x').write_text('not wisdom')
        self.assertEqual(ws.header_version(self.d / 'x'), '')
        self.assertEqual(ws.header_version(self.d / 'absent'), '')

    def test_numeric_version(self):
        self.assertEqual(ws.numeric_version(V8), '3.3.8')
        self.assertEqual(ws.numeric_version(V11), '3.3.11')
        self.assertEqual(ws.numeric_version(''), '')

    def test_compatible(self):
        w = _wisdom(self.d / 'w', '3.3.10')
        self.assertFalse(ws.compatible(w, V8))
        self.assertTrue(ws.compatible(w, 'fftw-3.3.10-sse2-avx'))
        self.assertTrue(ws.compatible(w, ''))                 # radiod unknown
        self.assertTrue(ws.compatible(self.d / 'absent', V8))  # nothing to judge

    def test_ldd_parse(self):
        out = ('\tlinux-vdso.so.1 (0x00007ffd)\n'
               '\tlibfftw3f_threads.so.3 => /lib/x86_64-linux-gnu/libfftw3f_threads.so.3 (0x1)\n'
               '\tlibfftw3f.so.3 => /usr/local/lib/libfftw3f.so.3 (0x2)\n')
        run = lambda *a, **k: subprocess.CompletedProcess(a, 0, out, '')
        self.assertEqual(ws.radiod_libfftw3f(Path('/x'), run=run), '/usr/local/lib/libfftw3f.so.3')

    def test_library_version_reads_the_fftwf_version_array(self):
        # fftwf_version is a char ARRAY in the library: hand in_dll real
        # memory holding the string itself, as the symbol would be.
        buf = ctypes.create_string_buffer(V11.encode())
        with mock.patch.object(ctypes.c_char, 'in_dll',
                               return_value=ctypes.c_char.from_buffer(buf)):
            self.assertEqual(ws.library_version('/lib/libfftw3f.so.3', loader=lambda p: object()), V11)
        self.assertEqual(ws.library_version(''), '')

    def test_real_libfftw3f_when_present(self):
        import ctypes.util
        lib = ctypes.util.find_library('fftw3f')
        if not lib:
            self.skipTest('no libfftw3f on this host')
        self.assertRegex(ws.library_version(lib), r'^fftw-3\.\d+\.\d+')

    def test_library_version_unloadable_is_empty(self):
        def boom(p):
            raise OSError('no such lib')
        self.assertEqual(ws.library_version('/nope.so', loader=boom), '')


class Slot(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.state = self.d / 'ka9q'
        self.state.mkdir()
        self.src = _wisdom(self.d / 'wisdomf')
        self.side = self.d / 'wisdomf.cpu'
        self.side.write_text('model=AMD Ryzen 7 PRO 5850U with Radeon Graphics\n'
                             'planned_profiles=cob15,rof3240000\n')

    def test_name_is_exactly_radiods(self):
        self.assertEqual(ws.slot_path(V8, True, self.state).name,
                         'wisdom-fftw-3.3.8-sse2-avx-threaded')
        self.assertEqual(ws.slot_path(V8, False, self.state).name, 'wisdom-fftw-3.3.8-sse2-avx')

    def test_publish_into_an_empty_slot(self):
        slot, action, moved = ws.publish(self.src, self.side, V8, statedir=self.state,
                                         extra={'radiod_cpus': '14,15'})
        self.assertEqual(action, 'published')
        self.assertIsNone(moved)
        self.assertEqual(slot.read_bytes(), self.src.read_bytes())
        side = wisdom.read_sidecar(ws.sidecar_of(slot))
        self.assertEqual(side['made_by'], 'sigmond')
        self.assertEqual(side['fftw_version'], V8)
        self.assertEqual(side['threaded'], 'yes')
        self.assertEqual(side['radiod_cpus'], '14,15')
        self.assertEqual(side['model'], 'AMD Ryzen 7 PRO 5850U with Radeon Graphics')
        self.assertEqual(ws.slot_owner(slot), 'sigmond')

    def test_republishing_the_same_bytes_is_current(self):
        ws.publish(self.src, self.side, V8, statedir=self.state)
        self.assertEqual(ws.publish(self.src, self.side, V8, statedir=self.state)[1], 'current')

    def test_a_foreign_slot_is_kept_aside_not_deleted(self):
        slot = ws.slot_path(V8, True, self.state)
        _wisdom(slot, body='(phils_plan)')
        self.assertEqual(ws.slot_owner(slot), 'foreign')
        _, action, moved = ws.publish(self.src, self.side, V8, statedir=self.state, now=lambda: 1234)
        self.assertEqual(action, 'published')
        self.assertEqual(moved.name, 'wisdom-fftw-3.3.8-sse2-avx-threaded.foreign-1234')
        self.assertIn('(phils_plan)', moved.read_text())
        self.assertEqual(slot.read_bytes(), self.src.read_bytes())

    def test_another_versions_slot_is_untouched(self):
        other = ws.slot_path(V11, True, self.state)
        _wisdom(other, '3.3.11', body='(phils_plan)')
        ws.publish(self.src, self.side, V8, statedir=self.state)
        self.assertIn('(phils_plan)', other.read_text())

    def test_wrong_version_is_not_published(self):
        _, action, _ = ws.publish(self.src, self.side, V11, statedir=self.state)
        self.assertTrue(action.startswith('skipped'))
        self.assertEqual(list(self.state.iterdir()), [])

    def test_unknown_radiod_version_is_not_published(self):
        self.assertTrue(ws.publish(self.src, self.side, '', statedir=self.state)[1].startswith('skipped'))

    def test_conditions(self):
        r = self.d / 'resctrl' / 'radiod'
        r.mkdir(parents=True)
        (r / 'schemata').write_text('    L3:0=03ff\n    MB:0=2048\n')
        f = self.d / 'cpu' / 'cpu14' / 'cpufreq'
        f.mkdir(parents=True)
        (f / 'scaling_max_freq').write_text('3200000\n')
        cl = self.d / 'cmdline'
        cl.write_text('BOOT_IMAGE=/vmlinuz isolcpus=14-15 nohz_full=14-15\n')
        c = ws.conditions({15, 14}, resctrl=self.d / 'resctrl', sysfs=self.d / 'cpu', cmdline=cl)
        self.assertEqual(c, {'radiod_cpus': '14,15', 'radiod_max_khz': '3200000',
                             'radiod_cat_l3': 'L3:0=03ff', 'isolcpus': '14-15'})


class Bundles(unittest.TestCase):
    def test_a_bundle_from_another_fftw_is_not_offered(self):
        d = Path(tempfile.mkdtemp())
        _wisdom(d / 'wisdomf-amd-x', '3.3.10')
        (d / 'wisdomf-amd-x.cpu').write_text('model=AMD X\n')
        self.assertEqual(wisdom.find_bundle([d], 'AMD X', 'fftw-3.3.10-sse2'), d / 'wisdomf-amd-x')
        self.assertIsNone(wisdom.find_bundle([d], 'AMD X', V8))
        self.assertEqual(wisdom.find_bundle([d], 'AMD X'), d / 'wisdomf-amd-x')   # version unknown


class SmdHooks(unittest.TestCase):
    smd = _load_smd()

    def test_operator_owned_radiod_is_left_alone(self):
        with mock.patch.object(self.smd, '_wisdom_hands_off', return_value=True):
            self.assertEqual(self.smd._wisdom_verdict(), 'current')
            self.assertFalse(self.smd._wisdom_install_cpu_bundle())
            with mock.patch('sigmond.wisdom_slot.publish') as pub:
                self.smd._wisdom_publish_slot({14, 15})
            pub.assert_not_called()

    def test_wisdom_from_another_fftw_is_fresh(self):
        d = Path(tempfile.mkdtemp())
        wf = _wisdom(d / 'wisdomf', '3.3.10')
        with mock.patch.object(self.smd, '_wisdom_hands_off', return_value=False), \
             mock.patch.object(self.smd, '_radiod_fftw_version', return_value=V8):
            self.assertEqual(self.smd._wisdom_verdict(wf), 'fresh')

    def test_plan_auto_does_nothing_where_an_operator_owns_radiod(self):
        args = mock.Mock(auto=True, fresh=False)
        with mock.patch.object(self.smd, '_wisdom_hands_off', return_value=True), \
             mock.patch.object(self.smd, '_info'), \
             mock.patch.object(self.smd.subprocess, 'run') as run:
            self.assertEqual(self.smd.cmd_wisdom_plan(args), 0)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

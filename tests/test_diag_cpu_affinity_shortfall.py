"""`smd admin diag cpu-affinity --apply` must refuse a radiod shortfall before
it writes anything (spec 4.1: no efficiency core, bring-up stops with the
reason).  The refusal is the same one `smd apply` gives."""
import importlib.machinery
import importlib.util
import io
import contextlib
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
_HYBRID_CORES = [{0, 1}, {2, 3}, {4, 5}, {6, 7},
                 {8}, {9}, {10}, {11}, {12}, {13}, {14}, {15}]


def _load_smd():
    path = str(REPO / 'bin' / 'smd')
    loader = importlib.machinery.SourceFileLoader('smd_under_test', path)
    spec = importlib.util.spec_from_loader('smd_under_test', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class DiagApplyShortfallTests(unittest.TestCase):

    def test_no_performance_core_fits_writes_no_drop_in(self):
        smd = _load_smd()
        report = mock.Mock(units=[])
        args = Namespace(apply=True, topology=None, threads=False,
                         json=False, components=None)
        with mock.patch.object(smd, '_need_root', return_value=False), \
             mock.patch.object(smd, '_load_topology', return_value={}), \
             mock.patch('sigmond.cpu.build_affinity_report', return_value=report), \
             mock.patch('sigmond.cpu.get_physical_cores', return_value=_HYBRID_CORES), \
             mock.patch('sigmond.cpu.get_performance_cpus', return_value={1, 3}), \
             mock.patch('sigmond.cpu.get_isolated_cpus', return_value=set()), \
             mock.patch('sigmond.cpu.get_radiod_instances',
                        return_value=['radiod@x.service']), \
             mock.patch('sigmond.cpu.get_radiod_cpus_by_unit', return_value={}), \
             mock.patch.object(smd, '_write_affinity_drop_in') as write, \
             mock.patch.object(smd, '_disable_hftimestd_affinity_watcher') as disable, \
             mock.patch.object(smd, '_run') as run, \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            rc = smd.cmd_diag_cpu_affinity(args)
        self.assertEqual(rc, 1)
        write.assert_not_called()
        disable.assert_not_called()
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

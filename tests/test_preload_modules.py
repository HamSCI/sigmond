"""Commands that move sigmond's own checkout load every sigmond module they
need before the move (v3.73 row 4, fix round 3).

B3 rig, 2026-10-20: `smd admin manifest restore --apply` checked sigmond out
at v3.72 and then imported sigmond.restore_units, which v3.72's tree lacks:
ModuleNotFoundError.  `smd update` and `smd align --apply` move sigmond the
same way.  bin/smd keeps one list per command (_PRELOAD_MODULES) and imports
it first.  This test reads the function-level imports along each command's
code path and fails when one names a module its list lacks.
"""
import importlib
import importlib.machinery
import importlib.util
import os
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SMD = (REPO / 'bin' / 'smd').read_text()

# The functions each command runs from its first checkout or pull onward.
PATHS = {
    'manifest_restore': ['cmd_manifest_restore', '_restore_plan_units',
                         '_restore_apply_units', '_restore_unit_instances',
                         '_restore_rerender_uploader', '_align_run_install',
                         '_align_repair_ownership', '_write_update_log'],
    'update': ['cmd_update', '_record_update_step', '_write_update_log',
               '_align_run_install'],
    'align': ['_align_apply', '_align_make_live', '_align_install_and_repair',
              '_align_run_install', '_align_repair_ownership', '_align_build',
              '_align_install_missing', '_align_restart_uploader',
              '_align_staleness'],
}


def _body(fn):
    m = re.search(rf'^def {re.escape(fn)}\(.*?(?=^def |\Z)', SMD, re.S | re.M)
    assert m, f'{fn} not found in bin/smd'
    return m.group(0)


def _lazy_modules(body):
    mods = set()
    for line in body.splitlines():
        s = line.strip()
        if not line.startswith((' ', '\t')):
            continue
        m = re.match(r'from (sigmond(?:\.[\w.]+)?) import (.+)', s)
        if m:
            if m.group(1) == 'sigmond':
                names = m.group(2).strip('()').split(',')
                mods.update(f'sigmond.{n.split(" as ")[0].strip()}' for n in names if n.strip())
            else:
                mods.add(m.group(1))
            continue
        m = re.match(r'import (sigmond\.[\w.]+)', s)
        if m:
            mods.add(m.group(1))
    return mods


def _load_smd():
    os.environ.setdefault('SIGMOND_NO_VENV_REEXEC', '1')
    loader = importlib.machinery.SourceFileLoader('smd_preload_test', str(REPO / 'bin' / 'smd'))
    spec = importlib.util.spec_from_loader('smd_preload_test', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class PreloadTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.smd = _load_smd()

    def test_each_list_covers_its_command_s_lazy_imports(self):
        for cmd, fns in PATHS.items():
            need = set().union(*(_lazy_modules(_body(f)) for f in fns))
            have = set(self.smd._PRELOAD_MODULES[cmd])
            self.assertEqual(sorted(need - have), [], cmd)

    def test_each_listed_module_imports(self):
        for cmd, mods in self.smd._PRELOAD_MODULES.items():
            for mod in mods:
                importlib.import_module(mod)

    def test_each_command_preloads_before_it_moves_anything(self):
        for fn, cmd, move in (('cmd_manifest_restore', 'manifest_restore', "'checkout', '--detach'"),
                              ('cmd_update', 'update', "'pull', '--ff-only'"),
                              ('_align_apply', 'align', 'apply_plan(')):
            body = _body(fn)
            pre = body.find(f"_preload_sigmond_modules('{cmd}')")
            self.assertGreater(pre, 0, f'{fn} never preloads')
            mv = body.find(move)
            self.assertGreater(mv, pre, f'{fn} moves before it preloads')


if __name__ == '__main__':
    unittest.main()

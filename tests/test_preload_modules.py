"""Commands that move sigmond's own checkout load every sigmond module they
need before the move (v3.73 row 4, fix rounds 3 and 4).

B3 rig, 2026-10-20: `smd admin manifest restore --apply` checked sigmond out
at v3.72 and then imported sigmond.restore_units, which v3.72's tree lacks:
ModuleNotFoundError.  `smd update` and `smd align --apply` move sigmond the
same way.  bin/smd keeps one list per command (_PRELOAD_MODULES) and imports
it first.

This test builds the call graph of bin/smd from its AST, starting at each
command's entry, and collects every function-level `sigmond.*` import it
can reach.  It then follows the lazy imports inside those modules too
(sigmond.catalog imports sigmond.discover inside a function).  Each list
must cover the result.  The closure counts every reachable import, not only
those after the move: an import that runs earlier costs nothing to preload.
"""
import ast
import importlib
import importlib.machinery
import importlib.util
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / 'lib'
TREE = ast.parse((REPO / 'bin' / 'smd').read_text())
FUNCS = {n.name: n for n in TREE.body if isinstance(n, ast.FunctionDef)}
ENTRIES = {'manifest_restore': 'cmd_manifest_restore',
           'update': 'cmd_update',
           'align': '_align_apply'}


def _sigmond_imports(node, package=None):
    """Function-level sigmond.* imports below ``node``.  ``package`` resolves
    relative imports inside a sigmond module."""
    out = set()
    for fn in ast.walk(node):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for n in ast.walk(fn):
            if isinstance(n, ast.ImportFrom):
                mod = n.module or ''
                if n.level and package:
                    parts = package.split('.')
                    base = '.'.join(parts[:len(parts) - n.level + 1])
                    mod = f'{base}.{mod}' if mod else base
                if mod == 'sigmond':
                    out |= {f'sigmond.{a.name}' for a in n.names}
                elif mod.startswith('sigmond.'):
                    out.add(mod)
            elif isinstance(n, ast.Import):
                out |= {a.name for a in n.names if a.name.startswith('sigmond.')}
    return out


def _module_file(mod):
    rel = Path(*mod.split('.'))
    for p in (LIB / rel.with_suffix('.py'), LIB / rel / '__init__.py'):
        if p.is_file():
            return p
    return None


def needed(command):
    seen, stack = set(), [ENTRIES[command]]
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        stack += [n.id for n in ast.walk(FUNCS[f])
                  if isinstance(n, ast.Name) and n.id in FUNCS]
    mods = set()
    for f in seen:
        mods |= _sigmond_imports(FUNCS[f])
    # the lazy imports inside those modules, transitively
    todo = list(mods)
    while todo:
        mod = todo.pop()
        path = _module_file(mod)
        if path is None:
            continue
        pkg = mod if path.name == '__init__.py' else mod.rsplit('.', 1)[0]
        for m in _sigmond_imports(ast.parse(path.read_text()), package=pkg):
            if m not in mods and _module_file(m) is not None:
                mods.add(m)
                todo.append(m)
    return {m for m in mods if _module_file(m) is not None}


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

    def test_each_list_covers_what_its_command_can_reach(self):
        for cmd in ENTRIES:
            missing = sorted(needed(cmd) - set(self.smd._PRELOAD_MODULES[cmd]))
            self.assertEqual(missing, [], cmd)

    def test_the_closure_reaches_heartbeat_through_align_fast_checks(self):
        self.assertIn('sigmond.heartbeat', needed('align'))
        self.assertIn('sigmond.restore_units', needed('manifest_restore'))

    def test_each_listed_module_imports(self):
        for mods in self.smd._PRELOAD_MODULES.values():
            for mod in mods:
                importlib.import_module(mod)

    def test_each_command_preloads_before_it_moves_anything(self):
        src = (REPO / 'bin' / 'smd').read_text()
        for cmd, fn, move in (('manifest_restore', 'cmd_manifest_restore', "'checkout', '--detach'"),
                              ('update', 'cmd_update', "'pull', '--ff-only'"),
                              ('align', '_align_apply', 'apply_plan(')):
            body = ast.get_source_segment(src, FUNCS[fn])
            pre = body.find(f"_preload_sigmond_modules('{cmd}')")
            self.assertGreater(pre, 0, f'{fn} never preloads')
            self.assertGreater(body.find(move), pre, f'{fn} moves before it preloads')


if __name__ == '__main__':
    unittest.main()

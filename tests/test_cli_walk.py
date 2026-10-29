"""cli_walk: a stdlib-only walker that prints an argparse tree as JSON."""

from __future__ import annotations

import ast
import argparse
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_LIB = _REPO / 'lib'
_WALKER = _LIB / 'sigmond' / 'cli_walk.py'
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

_FLAG = {'ran': False}


def _tree_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='prog')
    sub = p.add_subparsers(dest='cmd')
    a = sub.add_parser('a', help='the a verb')
    asub = a.add_subparsers(dest='sub')
    asub.add_parser('x', help='x help')
    asub.add_parser('y')
    sub.add_parser('b', help='the b verb', description='long b')
    return p


def _args_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='prog', description='root desc')
    p.add_argument('mode', choices=['on', 'off'], help='which mode')
    p.add_argument('--opt', default=5, metavar='N', type=int, help='an option')
    p.add_argument('--verbose', action='store_true', help='chatty')
    p.add_argument('--must', required=True, help='needed')
    p.add_argument('--hidden', help=argparse.SUPPRESS)
    p.add_argument('--sup', default=argparse.SUPPRESS, help='suppressed default')
    p.add_argument('files', nargs='*', help='some files')
    return p


def _main_that_parses():
    p = argparse.ArgumentParser(prog='m')
    p.add_argument('--x')
    p.parse_args()
    _FLAG['ran'] = True


def _main_that_raises():
    raise OSError('device gone')


class WalkParserTests(unittest.TestCase):
    def test_walks_nested_subcommands_in_order(self):
        from sigmond.cli_walk import walk_parser
        cmds = walk_parser(_tree_parser(), 'prog')
        self.assertEqual([c['path'] for c in cmds],
                         ['prog', 'prog a', 'prog a x', 'prog a y', 'prog b'])
        by = {c['path']: c for c in cmds}
        self.assertEqual(by['prog a']['help'], 'the a verb')
        self.assertEqual(by['prog b']['description'], 'long b')
        self.assertEqual(by['prog a y']['help'], '')

    def test_records_arguments(self):
        from sigmond.cli_walk import walk_parser
        cmd = walk_parser(_args_parser(), 'prog')[0]
        self.assertEqual(cmd['description'], 'root desc')
        args = {a['name']: a for a in cmd['arguments']}
        self.assertNotIn('help', args)
        self.assertNotIn('hidden', args)
        mode = args['mode']
        self.assertEqual(mode['kind'], 'positional')
        self.assertEqual(mode['choices'], ['on', 'off'])
        self.assertTrue(mode['required'])
        opt = args['opt']
        self.assertEqual(opt['kind'], 'option')
        self.assertEqual(opt['flags'], ['--opt'])
        self.assertEqual(opt['default'], '5')
        self.assertEqual(opt['metavar'], 'N')
        self.assertFalse(opt['required'])
        self.assertIsNone(opt['choices'])
        self.assertIsNone(opt['nargs'])
        flag = args['verbose']
        self.assertEqual(flag['kind'], 'flag')
        self.assertEqual(flag['default'], 'False')
        self.assertTrue(args['must']['required'])
        self.assertIsNone(args['sup']['default'])
        self.assertEqual(args['files']['nargs'], '*')
        self.assertIsInstance(args['files']['nargs'], str)
        # the whole thing must serialise
        json.dumps(cmd)


class CaptureParserTests(unittest.TestCase):
    def test_capture_parser_stops_before_running(self):
        from sigmond.cli_walk import capture_parser
        _FLAG['ran'] = False
        orig = (argparse.ArgumentParser.parse_args,
                argparse.ArgumentParser.parse_known_args)
        argv = list(sys.argv)
        p = capture_parser(_main_that_parses)
        self.assertIsInstance(p, argparse.ArgumentParser)
        self.assertFalse(_FLAG['ran'])
        self.assertEqual((argparse.ArgumentParser.parse_args,
                          argparse.ArgumentParser.parse_known_args), orig)
        self.assertEqual(sys.argv, argv)

    def test_capture_parser_restores_when_func_raises(self):
        from sigmond.cli_walk import capture_parser
        orig = (argparse.ArgumentParser.parse_args,
                argparse.ArgumentParser.parse_known_args)
        argv = list(sys.argv)
        with self.assertRaises(OSError):
            capture_parser(_main_that_raises)
        self.assertEqual((argparse.ArgumentParser.parse_args,
                          argparse.ArgumentParser.parse_known_args), orig)
        self.assertEqual(sys.argv, argv)


class SubprocessTests(unittest.TestCase):
    def _run(self, *args, pythonpath=None):
        # -I ignores PYTHONPATH, so fixtures run without it (the real
        # run is isolated; a client's own venv carries its package).
        isolated = [] if pythonpath else ['-I']
        env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        if pythonpath:
            env['PYTHONPATH'] = pythonpath
        r = subprocess.run([sys.executable, *isolated, str(_WALKER), *args],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _fixture_dir(self, body):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        Path(d.name, 'fixtures_cli.py').write_text(body)
        return d.name

    def test_non_argparse_program_reports_error(self):
        d = self._fixture_dir('def no_parser():\n    return 0\n')
        out = self._run('--entry', 'fixtures_cli:no_parser', '--program', 'p',
                        pythonpath=d)
        self.assertEqual(out['schema'], 1)
        self.assertEqual(out['commands'], [])
        self.assertIn('no argparse parser reached', out['error'])

    def test_side_effect_before_parse_is_bounded(self):
        d = self._fixture_dir(
            'def boom():\n    raise OSError("device gone")\n')
        out = self._run('--entry', 'fixtures_cli:boom', '--program', 'p',
                        pythonpath=d)
        self.assertEqual(out['commands'], [])
        self.assertIn('OSError', out['error'])
        self.assertIn('device gone', out['error'])

    def test_entry_that_parses_walks(self):
        d = self._fixture_dir(
            'import argparse\n'
            'def main():\n'
            '    p = argparse.ArgumentParser()\n'
            '    p.add_argument("--q")\n'
            '    p.parse_args()\n'
            '    raise SystemExit("ran")\n')
        out = self._run('--entry', 'fixtures_cli:main', '--program', 'p',
                        pythonpath=d)
        self.assertIsNone(out['error'])
        self.assertEqual(out['entry'], 'fixtures_cli:main')
        self.assertEqual(out['commands'][0]['path'], 'p')
        self.assertEqual(out['commands'][0]['arguments'][0]['flags'], ['--q'])

    def test_missing_script_reports_error(self):
        out = self._run('--script', 'no-such-script-xyz')
        self.assertEqual(out['commands'], [])
        self.assertTrue(out['error'])

    def test_script_lookup_uses_console_scripts(self):
        want = [e for e in importlib.metadata.entry_points(
            group='console_scripts') if e.name == 'pytest']
        self.assertTrue(want)
        out = self._run('--script', 'pytest')
        self.assertIsNone(out['error'])
        self.assertEqual(out['entry'], want[0].value)
        self.assertEqual(out['commands'][0]['path'], 'pytest')
        self.assertEqual(out['version'],
                         importlib.metadata.version(want[0].dist.name))


class PurityTests(unittest.TestCase):
    def test_walker_imports_nothing_from_sigmond(self):
        tree = ast.parse(_WALKER.read_text())
        mods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, 'relative import')
                mods.append(node.module)
        self.assertTrue(mods)
        for m in mods:
            self.assertIn(m.split('.')[0], sys.stdlib_module_names, m)


if __name__ == '__main__':
    unittest.main()

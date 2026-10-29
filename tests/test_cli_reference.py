"""cli_reference: build and show the per-program CLI reference files."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_LIB = Path(__file__).resolve().parent.parent / 'lib'
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from sigmond import cli_reference  # noqa: E402


def _walker_json(name, version='1.0'):
    return json.dumps({
        'schema': 1, 'program': name, 'version': version,
        'entry': 'x:main', 'commands': [
            {'path': name, 'help': '', 'description': '', 'args': []}],
        'error': None})


class _Proc:
    def __init__(self, stdout='', returncode=0, stderr=''):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class _Fake:
    """A fake subprocess.run that records its commands."""

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.reply is not None:
            return self.reply(cmd, kw)
        name = cmd[cmd.index('--program') + 1]
        return _Proc(_walker_json(name))


def _make_base(root: Path) -> Path:
    base = root / 'base'
    for comp, vdir in (('alpha', 'venv'), ('beta', '.venv'), ('gamma', 'build')):
        (base / comp / vdir / 'bin').mkdir(parents=True)
        (base / comp / vdir / 'bin' / 'python').write_text('')
    (base / 'alpha' / 'deploy.toml').write_text(
        '[[install.steps]]\nkind = "link"\n'
        f'src = "{base}/alpha/venv/bin/alpha-cli"\ndst = "/usr/local/bin/alpha"\n')
    (base / 'beta' / 'deploy.toml').write_text(
        '[[install.steps]]\nkind = "link"\n'
        f'src = "{base}/beta/.venv/bin/beta"\ndst = "/usr/local/bin/beta"\n'
        '[[install.steps]]\nkind = "link"\nsrc = "systemd/beta@.service"\n'
        'dst = "/etc/systemd/system/beta@.service"\n')
    (base / 'gamma' / 'deploy.toml').write_text(
        '[[install.steps]]\nkind = "link"\n'
        f'src = "{base}/gamma/build/bin/gamma"\ndst = "/usr/local/bin/gamma"\n')
    return base


CATALOG = {'alpha': None, 'beta': None, 'gamma': None}


def _smd_parser():
    import argparse
    p = argparse.ArgumentParser(prog='smd')
    sub = p.add_subparsers(dest='c')
    admin = sub.add_parser('admin')
    cr = admin.add_subparsers(dest='a').add_parser('cli-reference')
    cr.add_subparsers(dest='v').add_parser('build', help='build it')
    return p


class CliReferenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.base = _make_base(self.root)
        self.out = self.root / 'out'

    def _build(self, run=None, **kw):
        return cli_reference.build(
            self.out, run=run or _Fake(), base=self.base, catalog=CATALOG,
            smd_parser=_smd_parser, **kw)

    def test_discovers_programs_from_deploy_links(self):
        progs = cli_reference.discover_programs(self.base, CATALOG)
        self.assertEqual(
            [(p.name, p.component) for p in progs],
            [('alpha', 'alpha'), ('beta', 'beta'), ('smd', 'sigmond')])
        self.assertEqual(progs[0].script, 'alpha-cli')
        self.assertEqual(progs[0].python, self.base / 'alpha/venv/bin/python')
        self.assertIsNone(progs[2].python)

    def test_build_writes_one_file_per_program(self):
        results = self._build()
        self.assertEqual([r['program'] for r in results], ['alpha', 'beta', 'smd'])
        self.assertTrue(all(r['ok'] and r['error'] is None for r in results))
        for name in ('alpha', 'beta', 'smd'):
            path = self.out / f'{name}.json'
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            data = json.loads(path.read_text())
            self.assertEqual(data['program'], name)
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o755)
        self.assertEqual(json.loads((self.out / 'alpha.json').read_text())
                         ['component'], 'alpha')

    def test_timeout_is_recorded_not_raised(self):
        def reply(cmd, kw):
            raise subprocess.TimeoutExpired(cmd, 30)
        results = self._build(run=_Fake(reply))
        data = json.loads((self.out / 'alpha.json').read_text())
        self.assertIn('timed out', data['error'])
        self.assertEqual(data['commands'], [])
        self.assertFalse(results[0]['ok'])
        self.assertTrue(results[-1]['ok'])  # smd still walked

    def test_non_json_output_is_recorded(self):
        results = self._build(run=_Fake(lambda c, k: _Proc('oops', 1, 'boom')))
        data = json.loads((self.out / 'beta.json').read_text())
        self.assertIn('no JSON', data['error'])
        self.assertFalse(results[1]['ok'])

    def test_build_removes_files_of_vanished_programs(self):
        self.out.mkdir()
        (self.out / 'ghost.json').write_text('{}')
        self._build()
        self.assertFalse((self.out / 'ghost.json').exists())
        self.assertTrue((self.out / 'alpha.json').exists())

    def test_runs_as_nobody_when_root(self):
        fake = _Fake()
        with mock.patch('os.geteuid', return_value=0):
            self._build(run=fake)
        cmd, kw = fake.calls[0]
        self.assertEqual(cmd[:4], ['runuser', '-u', 'nobody', '--'])
        self.assertEqual(kw['cwd'], '/')
        self.assertEqual(kw['timeout'], 30)
        self.assertEqual(set(kw['env']) - {'LANG'}, {'PATH', 'PYTHONDONTWRITEBYTECODE'})
        fake = _Fake()
        with mock.patch('os.geteuid', return_value=1000):
            self._build(run=fake)
        self.assertNotEqual(fake.calls[0][0][0], 'runuser')
        self.assertIn('-I', fake.calls[0][0])

    def test_show_marks_stale_versions(self):
        self._build()
        with mock.patch.object(cli_reference, 'installed_version',
                               side_effect=lambda py, script, **k: '1.1' if script == 'alpha-cli' else '1.0'):
            shown = cli_reference.show(self.out)
        by = {p['program']: p for p in shown['programs']}
        self.assertEqual(shown['schema'], 1)
        self.assertTrue(by['alpha']['stale'])
        self.assertFalse(by['beta']['stale'])
        self.assertIsInstance(by['alpha']['built_at'], str)
        self.assertEqual(by['alpha']['component'], 'alpha')
        self.assertNotIn('python', by['alpha'])

    def test_show_runs_no_walk(self):
        self._build()
        fake = _Fake(lambda c, k: _Proc('1.0\n'))
        cli_reference.show(self.out, run=fake)
        for cmd, _ in fake.calls:
            self.assertNotIn('--script', cmd)

    def test_smd_walks_itself_in_process(self):
        fake = _Fake()
        self._build(run=fake)
        data = json.loads((self.out / 'smd.json').read_text())
        paths = [c['path'] for c in data['commands']]
        self.assertIn('smd admin cli-reference build', paths)
        self.assertEqual(len(fake.calls), 2)  # alpha, beta; never smd

    def test_real_smd_tree_has_the_verbs(self):
        data = cli_reference._walk_smd(None)
        self.assertIsNone(data['error'])
        paths = [c['path'] for c in data['commands']]
        self.assertIn('smd admin cli-reference show', paths)

    def test_cli_reference_is_a_snapshot_command(self):
        from sigmond.snapshot import SNAPSHOT_COMMANDS
        self.assertEqual(SNAPSHOT_COMMANDS['cli-reference'],
                         ['admin', 'cli-reference', 'show', '--json'])


if __name__ == '__main__':
    unittest.main()

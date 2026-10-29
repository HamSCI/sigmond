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
        'entry': f'{name}:main', 'commands': [
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

    def _add_twins(self):
        """component `delta` links two scripts, `delta` and `d`, to one entry."""
        d = self.base / 'delta'
        (d / 'venv' / 'bin').mkdir(parents=True)
        (d / 'venv' / 'bin' / 'python').write_text('')
        (d / 'deploy.toml').write_text(
            '[[install.steps]]\nkind = "link"\n'
            f'src = "{d}/venv/bin/d"\ndst = "/usr/local/bin/d"\n'
            '[[install.steps]]\nkind = "link"\n'
            f'src = "{d}/venv/bin/delta"\ndst = "/usr/local/bin/delta"\n')
        return dict(CATALOG, delta=None)

    def _twin_fake(self):
        def reply(cmd, kw):
            name = cmd[cmd.index('--program') + 1]
            rec = json.loads(_walker_json(name))
            if name in ('d', 'dd', 'delta'):
                rec['entry'] = 'delta.cli:main'
            return _Proc(json.dumps(rec))
        return _Fake(reply)

    def test_programs_sharing_an_entry_point_collapse_into_one_record(self):
        catalog = self._add_twins()
        results = cli_reference.build(
            self.out, run=self._twin_fake(), base=self.base, catalog=catalog,
            smd_parser=_smd_parser)
        self.assertEqual([r['program'] for r in results],
                         ['alpha', 'beta', 'delta', 'smd'])
        data = json.loads((self.out / 'delta.json').read_text())
        self.assertEqual(data['aliases'], ['d'])
        self.assertFalse((self.out / 'd.json').exists())
        self.assertEqual(json.loads((self.out / 'alpha.json').read_text())
                         ['aliases'], [])

    def test_collapse_keeps_the_first_name_when_none_matches_the_component(self):
        catalog = self._add_twins()
        (self.base / 'delta' / 'deploy.toml').write_text(
            (self.base / 'delta' / 'deploy.toml').read_text().replace(
                '/usr/local/bin/delta', '/usr/local/bin/dd'))
        cli_reference.build(self.out, run=self._twin_fake(), base=self.base,
                            catalog=catalog, smd_parser=_smd_parser)
        data = json.loads((self.out / 'd.json').read_text())
        self.assertEqual(data['aliases'], ['dd'])
        self.assertFalse((self.out / 'dd.json').exists())

    def test_a_stale_alias_file_is_removed(self):
        catalog = self._add_twins()
        self.out.mkdir()
        (self.out / 'd.json').write_text(json.dumps(
            {'schema': 1, 'program': 'd', 'commands': []}))
        cli_reference.build(self.out, run=self._twin_fake(), base=self.base,
                            catalog=catalog, smd_parser=_smd_parser)
        self.assertFalse((self.out / 'd.json').exists())

    def test_failed_walks_do_not_collapse(self):
        catalog = self._add_twins()
        results = cli_reference.build(
            self.out, run=_Fake(lambda c, k: _Proc('oops', 1)), base=self.base,
            catalog=catalog, smd_parser=_smd_parser)
        self.assertIn('d', [r['program'] for r in results])

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
        (self.out / 'ghost.json').write_text(json.dumps(
            {'schema': 1, 'program': 'ghost', 'commands': []}))
        self._build()
        self.assertFalse((self.out / 'ghost.json').exists())
        self.assertTrue((self.out / 'alpha.json').exists())

    def test_runs_as_nobody_when_root(self):
        fake = _Fake()
        with mock.patch('os.geteuid', return_value=0):
            self._build(run=fake)
        cmd, kw = fake.calls[0]
        self.assertTrue(cmd[0].endswith('/runuser'))
        self.assertTrue(os.path.isabs(cmd[0]))
        self.assertEqual(cmd[1:4], ['-u', 'nobody', '--'])
        self.assertEqual(kw['cwd'], '/')
        self.assertEqual(kw['timeout'], 30)
        self.assertEqual(set(kw['env']) - {'LANG'}, {'PATH', 'PYTHONDONTWRITEBYTECODE'})
        fake = _Fake()
        with mock.patch('os.geteuid', return_value=1000):
            self._build(run=fake)
        self.assertFalse(fake.calls[0][0][0].endswith('runuser'))
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

    def test_build_leaves_foreign_files_and_existing_dir_mode(self):
        self.out.mkdir()
        os.chmod(self.out, 0o700)
        (self.out / 'other.json').write_text('{"hello": 1}')
        (self.out / 'notes.txt').write_text('keep')
        (self.out / 'broken.json').write_text('not json')
        (self.out / 'ghost.json').write_text(json.dumps(
            {'schema': 1, 'program': 'ghost', 'commands': []}))
        self._build()
        self.assertTrue((self.out / 'other.json').exists())
        self.assertTrue((self.out / 'notes.txt').exists())
        self.assertTrue((self.out / 'broken.json').exists())
        self.assertFalse((self.out / 'ghost.json').exists())
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o700)

    def _show_one(self, record, installed):
        self.out.mkdir(exist_ok=True)
        (self.out / 'p.json').write_text(json.dumps(record))
        with mock.patch.object(cli_reference, 'installed_version',
                               return_value=installed):
            return cli_reference.show(self.out)['programs'][0]['stale']

    def test_stale_only_for_a_good_record_with_a_different_version(self):
        good = {'schema': 1, 'program': 'p', 'version': '1.0', 'error': None,
                'commands': [], 'python': '/x/python'}
        self.assertTrue(self._show_one(good, '1.1'))
        self.assertFalse(self._show_one(good, None))
        self.assertFalse(self._show_one(good, '1.0'))
        bad = dict(good, version=None, error='boom')
        self.assertFalse(self._show_one(bad, '1.1'))

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

    def test_smd_version_is_the_checkout_head(self):
        sha = subprocess.run(
            ['git', '-C', str(Path(cli_reference.__file__).resolve().parents[2]),
             'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(cli_reference.smd_version(), sha)
        self.assertEqual(cli_reference.installed_version(None, 'smd'), sha)
        self._build()
        data = json.loads((self.out / 'smd.json').read_text())
        self.assertEqual(data['version'], sha)

    def test_smd_version_falls_back_to_the_package_version(self):
        from sigmond import __version__
        with mock.patch.object(cli_reference, 'SMD_CHECKOUT', Path('/nonexistent')):
            self.assertEqual(cli_reference.smd_version(), __version__)

    def test_real_smd_tree_has_the_verbs(self):
        data = cli_reference._walk_smd(None)
        self.assertIsNone(data['error'])
        paths = [c['path'] for c in data['commands']]
        self.assertIn('smd admin cli-reference show', paths)

    def test_cli_reference_is_a_snapshot_command(self):
        from sigmond.snapshot import SNAPSHOT_COMMANDS
        self.assertEqual(SNAPSHOT_COMMANDS['cli-reference'],
                         ['admin', 'cli-reference', 'show', '--json'])


class BoundedRunTests(unittest.TestCase):
    """No process a walk starts may outlive its timeout (final review F1)."""

    def test_timeout_kills_the_whole_process_group(self):
        import time
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as d:
            pidfile = Path(d) / 'grandchild.pid'
            # The shell forks a sleeper (the "program") and waits on it,
            # as runuser forks the real program.
            script = f'sleep 8 & echo $! > {pidfile}; wait'
            t0 = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                cli_reference.run_bounded(['sh', '-c', script], timeout=1.5,
                                          capture_output=True, text=True)
            self.assertLess(time.monotonic() - t0, 5)  # no wait on the sleeper
            pid = int(pidfile.read_text())
            for _ in range(20):
                if not Path(f'/proc/{pid}').exists():
                    break
                time.sleep(0.1)
            alive = Path(f'/proc/{pid}').exists()
            if alive:
                os.kill(pid, 9)
            self.assertFalse(alive, 'grandchild survived the timeout')

    def test_returns_completed_output(self):
        proc = cli_reference.run_bounded(['echo', 'hi'], timeout=5,
                                         capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stdout), (0, 'hi\n'))

    def test_version_probe_timeout_is_short(self):
        fake = _Fake(lambda c, k: _Proc('1.0\n'))
        cli_reference.installed_version(Path('/x/python'), 's', run=fake)
        self.assertEqual(fake.calls[0][1]['timeout'], 5)


if __name__ == '__main__':
    unittest.main()

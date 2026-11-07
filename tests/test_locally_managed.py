"""site-profile `[components] locally_managed`: checkouts a site keeps by hand.

At KFS Phil, the ka9q-radio author, changes the ka9q-radio checkout himself
(Rob, 2026-11-07).  sigmond must stop checking, pulling, aligning, pinning
and rebuilding it there, while every other site keeps today's behaviour.
So the list is off by default, and per site.

These tests cover the profile reading and each pure planner and chokepoint
that would otherwise touch such a checkout.  The CLI paths have their own
tests beside the existing ones: test_update_executor.py, test_align_cli.py,
test_smd_doctor_glue.py and test_harmonize.py.
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

import sigmond.site_profile as sp
from sigmond import align, align_apply, installer, update_build
from sigmond.catalog import CatalogEntry
from sigmond.manifest_restore import plan_restore
from sigmond.update import plan_update

REPO = Path(__file__).resolve().parent.parent

KFS = ('[components]\n'
       'locally_managed = ["ka9q-radio"]\n'
       'locally_managed_by = "Phil"\n')


def _profile(text: str) -> Path:
    sp.SITE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    sp.SITE_PROFILE_PATH.write_text(text)
    return sp.SITE_PROFILE_PATH


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_locally_managed", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_locally_managed", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class ProfileTests(unittest.TestCase):

    def test_absent_key_means_nothing_is_locally_managed(self):
        _profile('[station]\ncallsign = "AI6VN"\n')
        self.assertEqual(sp.load_site_profile().locally_managed, [])
        local = sp.locally_managed_components(known={'ka9q-radio'})
        self.assertFalse(local)
        self.assertNotIn('ka9q-radio', local)

    def test_no_profile_means_nothing_is_locally_managed(self):
        self.assertFalse(sp.SITE_PROFILE_PATH.exists())
        self.assertFalse(sp.locally_managed_components(known={'ka9q-radio'}))

    def test_the_list_and_its_manager_are_read(self):
        _profile(KFS)
        prof = sp.load_site_profile()
        self.assertEqual(prof.locally_managed, ['ka9q-radio'])
        self.assertEqual(prof.locally_managed_by, 'Phil')
        local = sp.locally_managed_components(known={'ka9q-radio'})
        self.assertIn('ka9q-radio', local)
        self.assertNotIn('hf-timestd', local)

    def test_the_note_names_the_manager_and_the_commit(self):
        _profile(KFS)
        local = sp.locally_managed_components(known={'ka9q-radio'})
        self.assertEqual(local.note('ka9q-radio', '401992cd1234'),
                         'ka9q-radio: locally managed (Phil) — left as is at 401992cd')

    def test_the_manager_is_optional(self):
        _profile('[components]\nlocally_managed = ["ka9q-radio"]\n')
        local = sp.locally_managed_components(known={'ka9q-radio'})
        self.assertEqual(local.note('ka9q-radio', 'abc1234'),
                         'ka9q-radio: locally managed — left as is at abc1234')

    def test_an_unknown_name_is_a_warning_not_an_error(self):
        _profile('[components]\nlocally_managed = ["ka9q-radio", "ka9q-raido"]\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            local = sp.locally_managed_components(known={'ka9q-radio'})
        self.assertIn('ka9q-radio', local)
        self.assertIn("'ka9q-raido'", err.getvalue())
        self.assertIn('WARNING', err.getvalue())

    def test_a_value_that_is_not_a_list_is_ignored_with_a_warning(self):
        _profile('[components]\nlocally_managed = 3\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(sp.load_site_profile().locally_managed, [])
        self.assertIn('must be a list', err.getvalue())

    def test_the_template_documents_the_block_and_still_parses(self):
        import tomllib
        self.assertIn('locally_managed', sp.TEMPLATE)
        self.assertIn('locally_managed_by', sp.TEMPLATE)
        self.assertNotIn('components', tomllib.loads(sp.TEMPLATE))


class WizardRewriteTests(unittest.TestCase):
    """The wizard rewrites site-profile whole; the list must survive it."""

    def test_the_components_block_is_carried_across_a_rewrite(self):
        import tomllib
        old = '[station]\ncallsign = "K6KFS"\n\n' + KFS
        new = '[station]\ncallsign = "K6KFS"\ngrid_square = "CM87"\n'
        out = tomllib.loads(sp.carry_operator_blocks(new, old))
        self.assertEqual(out['components']['locally_managed'], ['ka9q-radio'])
        self.assertEqual(out['station']['grid_square'], 'CM87')

    def test_profile_install_keeps_the_components_block(self):
        from sigmond.commands import config as cfg
        import argparse
        live = _profile('[station]\ncallsign = "K6KFS"\n\n' + KFS)
        new = live.with_name('site-profile.toml.new')
        new.write_text('[station]\ncallsign = "K6KFS"\n')
        with mock.patch.object(cfg, 'SITE_PROFILE_PATH', live), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = cfg.cmd_config_profile_install(
                argparse.Namespace(path=str(new)), chown=lambda *a: None)
        self.assertEqual(rc, 0)
        self.assertIn('locally_managed = ["ka9q-radio"]', live.read_text())


class PlanUpdateTests(unittest.TestCase):
    STATE = {
        'repos': {'ka9q-radio': {'behind': 4, 'dirty': [], 'owner': 'sigmond'},
                  'hf-timestd': {'behind': 1, 'dirty': [], 'owner': 'timestd'}},
        'venv_skew': [],
        'radiod': {'running': 'aaaaaaa', 'pin': 'bbbbbbb', 'contains_pin': False},
        'wisdom_misses': ['cob100'],
    }

    def test_a_locally_managed_ka9q_radio_is_neither_pulled_nor_rebuilt(self):
        # Without the list, the same state pulls and rebuilds it.
        kinds = {(a.kind, a.target) for a in plan_update(dict(self.STATE))}
        self.assertIn(('pull', 'ka9q-radio'), kinds)
        self.assertIn(('rebuild-radiod', 'ka9q-radio'), kinds)

        plan = plan_update(dict(self.STATE, locally_managed=['ka9q-radio']))
        kinds = [(s.kind, s.target) for s in plan]
        self.assertNotIn(('pull', 'ka9q-radio'), kinds)
        self.assertNotIn(('install', 'ka9q-radio'), kinds)
        self.assertNotIn(('rebuild-radiod', 'ka9q-radio'), kinds)
        # The other components update as before ...
        self.assertIn(('pull', 'hf-timestd'), kinds)
        # ... and missing wisdom is still reported: FFTW plans, not the checkout.
        self.assertIn(('wisdom', 'ka9q-radio'), kinds)

    def test_a_locally_managed_component_is_never_held_or_refused(self):
        state = {'repos': {'ka9q-radio': {'behind': 0, 'dirty': ['src/main.c'],
                                          'incoming': None, 'pinned': 'abc',
                                          'at_pin': True}},
                 'radiod': {'running': None, 'pin': 'bbbbbbb'}}
        self.assertNotEqual(plan_update(state), [])
        self.assertEqual(plan_update(dict(state, locally_managed=['ka9q-radio'])), [])


REL = align.Release(tag='v3.90', manifest_text='', appliance_commit='f' * 40,
                    components={'sigmond': 'daba1f6', 'ka9q-radio': '401992c',
                                'hf-timestd': '5c8196d'})


def _no_git(*a, **k):
    raise AssertionError(f'git ran on a locally managed checkout: {a}')


class AlignTests(unittest.TestCase):

    def _local(self):
        _profile(KFS)
        return sp.locally_managed_components(known={'ka9q-radio'})

    def test_a_locally_managed_checkout_is_a_local_item_at_any_head(self):
        # Without the list, it moves.
        items = align.plan_align(REL, {'sigmond': 'daba1f6', 'ka9q-radio': 'deb7bdd',
                                       'hf-timestd': '5c8196d'}, {})
        self.assertEqual({i.component: i.status for i in items}['ka9q-radio'], 'move')
        local = self._local()
        for head, dirt in (('deb7bdd', []), ('401992c', []), ('deb7bdd', ['src/x.c'])):
            items = align.plan_align(REL, {'sigmond': 'daba1f6', 'ka9q-radio': head,
                                           'hf-timestd': 'aaaaaaa'},
                                     {'ka9q-radio': dirt}, local=local)
            by = {i.component: i for i in items}
            self.assertEqual(by['ka9q-radio'].status, 'local')
            self.assertEqual(by['ka9q-radio'].note, 'locally managed (Phil) — left as is')
            self.assertEqual(by['hf-timestd'].status, 'move')

    def test_a_missing_locally_managed_checkout_is_not_installed(self):
        items = align.plan_align(REL, {'sigmond': 'daba1f6', 'hf-timestd': '5c8196d'},
                                 {}, local=self._local())
        self.assertEqual({i.component: i.status for i in items}['ka9q-radio'], 'local')

    def test_apply_leaves_a_local_item_untouched(self):
        items = align.plan_align(REL, {'sigmond': 'daba1f6', 'ka9q-radio': 'deb7bdd',
                                       'hf-timestd': '5c8196d'}, {}, local=self._local())
        local_items = [i for i in align.classify(items, _no_git)
                       if i.component == 'ka9q-radio']
        with tempfile.TemporaryDirectory() as td:
            ctx = align_apply.Ctx(base=Path(td), run=_no_git, as_owner=_no_git,
                                  owner_of=_no_git, catalog={}, origins={},
                                  build=_no_git, deploy_build=_no_git,
                                  install_missing=_no_git, say=lambda m: None)
            (step,) = align_apply.apply_plan(REL, local_items, ctx)
            self.assertFalse((Path(td) / 'ka9q-radio' / '.pin').exists())
        self.assertEqual(step.outcome, 'left')
        self.assertIn('locally managed (Phil)', step.detail)
        self.assertIn('deb7bdd', step.detail)


class RestoreTests(unittest.TestCase):
    MANIFEST = ('components (live):\n'
                + ''.join(f'    {n:<16} {s}\n' for n, s in (
                    ('sigmond', 'daba1f6'), ('ka9q-radio', '401992c'),
                    ('hf-timestd', '5c8196d'), ('wspr-recorder', '1111111'),
                    ('psk-recorder', '2222222')))
                # padding above doctor.MIN_COMPONENT_ROWS
                + ''.join(f'    filler-{i:02d}       ffff{i:03d}\n' for i in range(8)))

    LIVE = dict({'sigmond': 'daba1f6', 'ka9q-radio': 'deb7bdd', 'hf-timestd': '5c8196d',
                 'wspr-recorder': '1111111', 'psk-recorder': '2222222'},
                **{f'filler-{i:02d}': f'ffff{i:03d}' for i in range(8)})

    def test_a_locally_managed_checkout_is_left_and_refuses_nothing(self):
        # Without the list, it is checked out.
        plan = plan_restore(self.MANIFEST, self.LIVE, lambda n, s: True)
        self.assertIn(('ka9q-radio', 'checkout', 'deb7bdd', '401992c'), plan.actions)
        plan = plan_restore(self.MANIFEST, self.LIVE, lambda n, s: False,
                            local={'ka9q-radio'})
        self.assertTrue(plan.ok, plan.refusals)
        self.assertIn(('ka9q-radio', 'local', 'deb7bdd', '401992c'), plan.actions)
        self.assertFalse([a for a in plan.actions if a[1] == 'checkout'])


def _entry(name):
    return CatalogEntry(name=name, kind='server', description='', repo='https://example.invalid/x.git')


class InstallerTests(unittest.TestCase):

    def test_clone_repo_leaves_an_existing_locally_managed_checkout_alone(self):
        _profile(KFS)
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'ka9q-radio').mkdir()
            calls = []

            def fake_git(repo_dir, *args):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, 'deb7bdd\n', '')
            with mock.patch.object(installer, '_git', side_effect=fake_git), \
                    mock.patch.object(installer.subprocess, 'run', side_effect=_no_git), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                got = installer.clone_repo(_entry('ka9q-radio'), base=Path(td),
                                           pull_if_exists=True, ref='401992c')
        self.assertEqual(got, Path(td) / 'ka9q-radio')
        self.assertEqual(calls, [('rev-parse', '--short', 'HEAD')])
        self.assertIn('locally managed (Phil) — left as is at deb7bdd', out.getvalue())

    def test_clone_repo_never_clones_a_missing_locally_managed_checkout(self):
        _profile(KFS)
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(installer.subprocess, 'run', side_effect=_no_git):
            with self.assertRaises(RuntimeError) as cm:
                installer.clone_repo(_entry('ka9q-radio'), base=Path(td))
        self.assertIn('does not clone it', str(cm.exception))

    def test_install_client_runs_nothing_for_a_locally_managed_component(self):
        _profile('[components]\nlocally_managed = ["hf-timestd"]\n')
        with mock.patch.object(installer, 'clone_repo', side_effect=_no_git), \
                mock.patch.object(installer, 'run_install_script', side_effect=_no_git), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            ok = installer.install_client(_entry('hf-timestd'), yes=True)
        self.assertTrue(ok)
        self.assertIn('hf-timestd: locally managed', out.getvalue())

    def test_update_build_builds_nothing_for_a_locally_managed_component(self):
        _profile('[components]\nlocally_managed = ["station-web"]\n')
        with mock.patch.object(installer, 'run_deploy_build_steps', side_effect=_no_git):
            ok, msgs = update_build.run(Path('/opt/git/sigmond/station-web'), runner=_no_git)
        self.assertTrue(ok)
        self.assertIn('not built', msgs[0])


class SmdChokepointTests(unittest.TestCase):

    def test_build_ka9q_radio_builds_nothing_when_locally_managed(self):
        _profile(KFS)
        with mock.patch.object(smd, '_run', side_effect=_no_git), \
                mock.patch.object(smd, '_checkout_short_head', return_value='deb7bdd'), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(smd._build_ka9q_radio(Path('/nonexistent/ka9q-radio'), force=True))
        self.assertIn('ka9q-radio: locally managed (Phil) — left as is at deb7bdd', out.getvalue())

    def test_rebuild_component_builds_nothing_when_locally_managed(self):
        _profile('[components]\nlocally_managed = ["ka9q-web"]\n')
        with mock.patch.object(smd, '_build_ka9q_web_with_onion', side_effect=_no_git), \
                mock.patch.object(smd, '_checkout_short_head', return_value=None), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(smd._rebuild_component(
                'ka9q-web', _entry('ka9q-web'), Path('/nonexistent'), {}))


if __name__ == '__main__':
    unittest.main()

"""[decoders] operator_override in coordination.toml (WWV-2, 2026-11-16).

WWV-2's operator chose the ubersdr decoder build by hand although its WSJT-X
parity check had failed.  `smd update` / `smd align` would have pointed the
station back at wsjtx without asking.  With the override set, sigmond keeps
the chosen build, says so, and never switches on its own.  Without it,
nothing changes: an unverified ubersdr build is still refused.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond import decoder_choice
from sigmond.commands import check


def _smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    spec = importlib.util.spec_from_loader("smd_mod_override", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = "bin/smd"
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


_SMD = _smd()

OVERRIDE = '[decoders]\noperator_override = true\n'


class Station(unittest.TestCase):
    """A temp coordination.toml, decoder dir and bin dir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.coord = self.tmp / 'coordination.toml'
        self.dec = self.tmp / 'decoders'
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()

    def build(self, impl, parity='verified'):
        d = self.dec / impl
        d.mkdir(parents=True, exist_ok=True)
        for b in ('wsprd', 'jt9'):
            (d / b).write_text('x')
        if parity is not None:
            (d / '.provenance').write_text(
                f'{impl}-decoders abc123 parity={parity}\n')

    def link(self, impl):
        for b in ('wsprd', 'jt9'):
            (self.bin / b).symlink_to(self.dec / impl / b)


class Reading(Station):

    def test_absent_means_no_override(self):
        self.assertFalse(decoder_choice.operator_override(self.coord))

    def test_true_sets_it(self):
        self.coord.write_text(OVERRIDE)
        self.assertTrue(decoder_choice.operator_override(self.coord))

    def test_only_a_real_true_counts(self):
        self.coord.write_text('[decoders]\noperator_override = "yes"\n')
        self.assertFalse(decoder_choice.operator_override(self.coord))

    def test_parity_word_is_read_from_provenance(self):
        self.build('ubersdr', parity='FAILED')
        self.assertEqual(decoder_choice.parity('ubersdr', self.dec), 'FAILED')

    def test_note_names_the_build_and_its_failed_parity(self):
        self.coord.write_text(OVERRIDE)
        self.build('ubersdr', parity='FAILED')
        self.link('ubersdr')
        note = decoder_choice.override_note(path=self.coord, bindir=self.bin,
                                            decdir=self.dec)
        self.assertIn('ubersdr', note)
        self.assertIn('parity FAILED', note)
        self.assertIn('operator override', note)

    def test_no_note_without_the_override(self):
        self.build('ubersdr', parity='FAILED')
        self.link('ubersdr')
        self.assertIsNone(decoder_choice.override_note(
            path=self.coord, bindir=self.bin, decdir=self.dec))


class Selecting(Station):
    """_select_decoders with and without the override."""

    def setUp(self):
        super().setUp()
        self.calls = []

        def fake_run(cmd, **kw):
            self.calls.append([str(c) for c in cmd])
            return types.SimpleNamespace(returncode=0, stdout='', stderr='')

        for p in (mock.patch.object(_SMD, '_DECODER_DIR', self.dec),
                  mock.patch.object(_SMD, '_run', fake_run),
                  mock.patch.object(_SMD, '_adopt_legacy_decoders', lambda: None),
                  mock.patch.object(_SMD, '_ok'), mock.patch.object(_SMD, '_err'),
                  mock.patch.object(_SMD, '_info')):
            p.start()
            self.addCleanup(p.stop)

    def links(self):
        return [c for c in self.calls if c and c[0] == 'ln']

    def test_default_still_refuses_a_failed_parity_build(self):
        self.build('ubersdr', parity='FAILED')
        self.assertFalse(_SMD._select_decoders('ubersdr'))
        self.assertEqual(self.links(), [])

    def test_override_selects_it_and_says_so(self):
        self.build('ubersdr', parity='FAILED')
        with mock.patch.object(_SMD, '_warn') as warn:
            self.assertTrue(_SMD._select_decoders('ubersdr', waive_parity=True))
        self.assertEqual(len(self.links()), 2)
        said = ' '.join(str(c) for c in warn.call_args_list)
        self.assertIn('FAILED', said)
        self.assertIn('operator_override', said)

    def test_override_never_selects_a_build_that_is_not_there(self):
        self.assertFalse(_SMD._select_decoders('ubersdr', waive_parity=True))
        self.assertEqual(self.links(), [])


class Preference(Station):

    def pref(self, text, live=''):
        self.coord.write_text(text)
        with mock.patch.object(decoder_choice, 'COORDINATION_TOML', self.coord), \
             mock.patch.object(_SMD, '_COORDINATION_TOML', self.coord), \
             mock.patch.object(_SMD, '_decoder_selected', lambda: live), \
             mock.patch.object(_SMD, '_warn'):
            return _SMD._decoder_preference()

    def test_override_without_implementation_keeps_the_live_build(self):
        self.assertEqual(self.pref(OVERRIDE, live='wsjtx'), 'wsjtx')

    def test_override_with_implementation_takes_the_named_build(self):
        self.assertEqual(self.pref(OVERRIDE + 'implementation = "ubersdr"\n',
                                   live='wsjtx'), 'ubersdr')

    def test_without_override_the_default_is_unchanged(self):
        self.assertEqual(self.pref('', live='wsjtx'), 'ubersdr')


class Building(unittest.TestCase):
    """_build_decoders: what update/align/install run."""

    def run_build(self, *, override, ubersdr_builds, ubersdr_on_disk=True):
        picked = []
        with mock.patch.object(_SMD, '_build_wsjtx_decoders', lambda: True), \
             mock.patch.object(_SMD, '_build_ubersdr_decoders',
                               lambda **k: ubersdr_builds), \
             mock.patch.object(_SMD, '_adopt_legacy_decoders', lambda: None), \
             mock.patch.object(_SMD, '_decoder_preference', lambda: 'ubersdr'), \
             mock.patch.object(_SMD, '_decoder_override', lambda: override), \
             mock.patch.object(_SMD, '_decoder_built',
                               lambda impl: ubersdr_on_disk), \
             mock.patch.object(_SMD, '_select_decoders',
                               lambda i, **k: picked.append((i, k)) or True), \
             mock.patch.object(_SMD, '_warn') as warn, \
             mock.patch.object(_SMD, '_info') as info, \
             mock.patch.object(_SMD, '_ok'), mock.patch.object(_SMD, '_err'):
            ok = _SMD._build_decoders()
        said = ' '.join(str(c) for c in warn.call_args_list + info.call_args_list)
        return ok, picked, said

    def test_default_falls_back_to_wsjtx_as_before(self):
        ok, picked, _ = self.run_build(override=False, ubersdr_builds=False)
        self.assertEqual([p[0] for p in picked], ['wsjtx'])

    def test_override_keeps_the_chosen_build_when_its_rebuild_fails(self):
        ok, picked, said = self.run_build(override=True, ubersdr_builds=False)
        self.assertTrue(ok)
        self.assertEqual(picked, [('ubersdr', {'waive_parity': True})])
        self.assertIn('operator override', said)

    def test_override_with_nothing_built_falls_back_loudly(self):
        ok, picked, said = self.run_build(override=True, ubersdr_builds=False,
                                          ubersdr_on_disk=False)
        self.assertEqual([p[0] for p in picked], ['wsjtx'])
        self.assertIn('operator override', said)


class Doctor(Station):

    def test_held_build_is_no_finding(self):
        self.build('ubersdr', parity='FAILED')
        self.link('ubersdr')
        self.assertEqual(_SMD._decoder_findings(bindir=self.bin, decdir=self.dec,
                                                want='ubersdr'), [])


class Check(unittest.TestCase):
    """`smd check`'s WSPR line carries the hold, whatever else it says."""

    CYCLE = ('cycle UTC 11:50 rx=B4 \u2192 30 spots in wspr.spots '
             '(4 bands, write 2 ms) bands=[40:10 30:7 20:13]')
    NOTE = ('decoders: ubersdr held by operator override, parity FAILED; '
            'sigmond will not switch it')

    def test_wspr_line_names_the_hold(self):
        from tests.check_fakes import make_env
        env = make_env(journal=lambda u, m: [self.CYCLE],
                       decoder_note=lambda: self.NOTE)
        line = check.wspr_line(env)
        self.assertEqual(line.mark, check.OK)
        self.assertIn('30 spots', line.text)
        self.assertIn('held by operator override, parity FAILED', line.text)

    def test_wspr_line_unchanged_without_it(self):
        from tests.check_fakes import make_env
        env = make_env(journal=lambda u, m: [self.CYCLE],
                       decoder_note=lambda: None)
        self.assertEqual(check.wspr_line(env).text,
                         '30 spots in the last 10 min, on 3 bands')


if __name__ == '__main__':
    unittest.main()

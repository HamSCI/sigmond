"""Tests for `smd admin manifest adopt` — the fail-closed verb that lets
an already-running host (b4, dasi002, ...) adopt a blessed manifest in
place, refusing unless live component SHAs already match it exactly.

``plan_adopt`` (sigmond.manifest_adopt) is pure — no filesystem, no
process state — and is tested directly here. The CLI wrapper in
bin/smd is exercised the way tests/test_smd_doctor_glue.py exercises
`smd doctor`: load bin/smd via SourceFileLoader and call the cmd_*
function with a fabricated argparse Namespace, so the test never
shells out and never risks the real `_need_root` sudo re-exec (only
the dry-run path is exercised here, which never reaches `_need_root`).
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import types
import unittest
from pathlib import Path
from unittest import mock

from sigmond.doctor import MIN_COMPONENT_ROWS
from sigmond.manifest_adopt import AdoptPlan, plan_adopt

REPO = Path(__file__).resolve().parent.parent


def _filler_rows(n, start=0):
    """`n` synthetic component-row lines padding a manifest above
    MIN_COMPONENT_ROWS, mirroring tests/test_doctor.py's helper of the
    same name so a manifest under test can carry exactly the rows the
    assertions care about plus enough filler to clear the row floor."""
    return ''.join(f"    filler-{i:02d}       ffff{i:03d}\n"
                   for i in range(start, start + n))


def _filler_live(n, start=0):
    return {f'filler-{i:02d}': f'ffff{i:03d}' for i in range(start, start + n)}


# ── plan_adopt (pure) ───────────────────────────────────────────────

class PlanAdoptTests(unittest.TestCase):

    def test_exact_match_is_ok(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        live = {'hf-timestd': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        plan = plan_adopt(manifest_text, live)
        self.assertIsInstance(plan, AdoptPlan)
        self.assertTrue(plan.ok)
        self.assertEqual(plan.refusals, [])
        self.assertEqual(len(plan.matches), MIN_COMPONENT_ROWS)

    def test_one_sha_off_refuses_only_that_component(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            "    wspr-recorder    bbbbbbb\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 2)
        )
        live = {'hf-timestd': 'ccccccc', 'wspr-recorder': 'bbbbbbb',
                **_filler_live(MIN_COMPONENT_ROWS - 2)}
        plan = plan_adopt(manifest_text, live)
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.refusals), 1)
        self.assertIn('hf-timestd', plan.refusals[0])
        self.assertNotIn('wspr-recorder', ' '.join(plan.refusals))
        # the matching components still show up as matches, not refusals
        self.assertTrue(any('wspr-recorder' in m for m in plan.matches))

    def test_extra_live_component_refuses(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        live = {'hf-timestd': 'aaaaaaa', 'meteor-scatter': 'ddddddd',
                **_filler_live(MIN_COMPONENT_ROWS - 1)}
        plan = plan_adopt(manifest_text, live)
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.refusals), 1)
        self.assertIn('meteor-scatter', plan.refusals[0])

    def test_missing_live_component_refuses(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            "    wspr-recorder    bbbbbbb\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 2)
        )
        live = {'hf-timestd': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 2)}
        plan = plan_adopt(manifest_text, live)
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.refusals), 1)
        self.assertIn('wspr-recorder', plan.refusals[0])

    def test_malformed_manifest_text_refuses_with_components_block_reason(self):
        plan = plan_adopt("not a manifest at all\n", {'hf-timestd': 'aaaaaaa'})
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.refusals), 1)
        self.assertIn('components block', plan.refusals[0])
        self.assertEqual(plan.matches, [])

    def test_empty_manifest_text_refuses_with_components_block_reason(self):
        plan = plan_adopt("", {'hf-timestd': 'aaaaaaa'})
        self.assertFalse(plan.ok)
        self.assertIn('components block', plan.refusals[0])

    def test_thin_components_block_below_row_floor_refuses(self):
        # header present but only 2 rows -- below MIN_COMPONENT_ROWS, so
        # _parse_manifest_components returns None (untrustworthy), same
        # as a missing header.
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            "    wspr-recorder    bbbbbbb\n"
        )
        plan = plan_adopt(manifest_text, {'hf-timestd': 'aaaaaaa'})
        self.assertFalse(plan.ok)
        self.assertIn('components block', plan.refusals[0])

    def test_mixed_length_sha_prefixes_that_agree_is_ok(self):
        # git rev-parse --short abbreviation length varies by repo size;
        # a 7-char manifest SHA and a 9-char live SHA that share the
        # shorter prefix are the SAME commit (mirrors
        # test_doctor.test_manifest_drift_tolerates_differing_abbreviation_length).
        manifest_text = (
            "components (live):\n"
            "    ka9q-radio       abc1234\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        live = {'ka9q-radio': 'abc1234567',
                **_filler_live(MIN_COMPONENT_ROWS - 1)}
        plan = plan_adopt(manifest_text, live)
        self.assertTrue(plan.ok)
        self.assertEqual(plan.refusals, [])

    def test_empty_live_components_refuses(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        plan = plan_adopt(manifest_text, {})
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.refusals), MIN_COMPONENT_ROWS)
        self.assertEqual(plan.matches, [])

    def test_refusal_lists_every_divergent_component_not_just_the_first(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            "    wspr-recorder    bbbbbbb\n"
            "    psk-recorder     ccccccc\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 3)
        )
        live = {'hf-timestd': 'zzzzzzz', 'wspr-recorder': 'yyyyyyy',
                'psk-recorder': 'ccccccc',
                **_filler_live(MIN_COMPONENT_ROWS - 3)}
        plan = plan_adopt(manifest_text, live)
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.refusals), 2)
        joined = ' '.join(plan.refusals)
        self.assertIn('hf-timestd', joined)
        self.assertIn('wspr-recorder', joined)


# ── CLI wrapper in bin/smd ───────────────────────────────────────────

def _load_smd():
    # bin/smd re-execs into the production venv unless told not to;
    # suppress that so importing the script just defines its functions.
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_manifest_adopt", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_manifest_adopt", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class ManifestAdoptCliDryRunTests(unittest.TestCase):
    """Dry-run (the default, no --apply) must never write
    MANIFEST_PATH, whether the plan is ok or refused."""

    def setUp(self):
        import tempfile
        self.tdir = Path(tempfile.mkdtemp())
        self.base = self.tdir / 'base'
        self.base.mkdir()
        self._orig_manifest_path = smd.MANIFEST_PATH
        smd.MANIFEST_PATH = self.tdir / 'manifest.txt'  # deliberately absent

    def tearDown(self):
        smd.MANIFEST_PATH = self._orig_manifest_path

    def _run(self, manifest_path, live=None):
        args = types.SimpleNamespace(
            path=str(manifest_path), apply=False, base=str(self.base))
        buf = io.StringIO()
        live = {} if live is None else live
        with mock.patch('sigmond.provenance.component_versions',
                         return_value=live):
            with contextlib.redirect_stdout(buf):
                rc = smd.cmd_manifest_adopt(args)
        return rc, buf.getvalue()

    def test_dry_run_never_writes_on_a_refused_plan(self):
        manifest = self.tdir / 'candidate.txt'
        manifest.write_text(
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        rc, out = self._run(manifest, live={})  # empty live -> refused
        self.assertEqual(rc, 1)
        self.assertFalse(smd.MANIFEST_PATH.exists())

    def test_dry_run_never_writes_on_an_ok_plan(self):
        manifest = self.tdir / 'candidate.txt'
        manifest.write_text(
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        live = {'hf-timestd': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        rc, out = self._run(manifest, live=live)
        self.assertEqual(rc, 0)
        self.assertFalse(smd.MANIFEST_PATH.exists())


if __name__ == '__main__':
    unittest.main()


class SupersetTests(unittest.TestCase):
    """--allow-superset: the sanctioned contains-pin case.

    plan_adopt trusts superset_components (it stays pure); the ancestry
    proof lives in doctor.sha_contained, tested below against real git
    history built in a tmp dir.
    """

    MANIFEST = ("components (live):\n" + _filler_rows(MIN_COMPONENT_ROWS))

    def test_mismatch_in_superset_set_is_ok_and_labelled(self):
        from sigmond.manifest_adopt import plan_adopt
        live = _filler_live(MIN_COMPONENT_ROWS)
        name = sorted(live)[0]
        live[name] = 'feedface1'
        plan = plan_adopt(self.MANIFEST, live,
                          superset_components=frozenset({name}))
        self.assertTrue(plan.ok, plan.refusals)
        labelled = [m for m in plan.matches if m.startswith(f'{name}: superset')]
        self.assertEqual(len(labelled), 1, plan.matches)
        self.assertIn('contained in live feedface1', labelled[0])
        # A superset entry is never disguised as an exact match.
        self.assertNotIn(f'{name}: feedface1', plan.matches)

    def test_mismatch_not_in_set_still_refuses(self):
        from sigmond.manifest_adopt import plan_adopt
        live = _filler_live(MIN_COMPONENT_ROWS)
        names = sorted(live)
        live[names[0]] = 'feedface1'
        live[names[1]] = 'cafecafe1'
        plan = plan_adopt(self.MANIFEST, live,
                          superset_components=frozenset({names[0]}))
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.refusals), 1)
        self.assertIn(names[1], plan.refusals[0])

    def test_component_missing_live_refuses_even_if_in_set(self):
        from sigmond.manifest_adopt import plan_adopt
        live = _filler_live(MIN_COMPONENT_ROWS)
        name = sorted(live)[0]
        del live[name]
        plan = plan_adopt(self.MANIFEST, live,
                          superset_components=frozenset({name}))
        self.assertFalse(plan.ok)


class ShaContainedTests(unittest.TestCase):
    """doctor.sha_contained: fail-closed ancestry proof in a real repo."""

    def _mkrepo(self, root):
        import subprocess as sp
        def git(*a):
            r = sp.run(['git', '-C', str(root), *a],
                       capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.strip()
        sp.run(['git', 'init', '-q', str(root)], check=True)
        git('config', 'user.email', 't@t'); git('config', 'user.name', 't')
        (root / 'f').write_text('a'); git('add', 'f')
        git('commit', '-qm', 'base')
        base = git('rev-parse', '--short', 'HEAD')
        git('checkout', '-qb', 'fork')
        (root / 'g').write_text('b'); git('add', 'g')
        git('commit', '-qm', 'fork work')
        fork = git('rev-parse', '--short', 'HEAD')
        return base, fork

    def test_ancestor_is_contained(self):
        import tempfile
        from pathlib import Path
        from sigmond.doctor import sha_contained
        with tempfile.TemporaryDirectory() as d:
            comp = Path(d) / 'ka9q-radio'
            comp.mkdir()
            base, fork = self._mkrepo(comp)
            self.assertTrue(sha_contained('ka9q-radio', base, fork, base=d))
            # Reversed: live older than manifest is NOT containment.
            self.assertFalse(sha_contained('ka9q-radio', fork, base, base=d))

    def test_unknown_sha_and_missing_repo_fail_closed(self):
        import tempfile
        from pathlib import Path
        from sigmond.doctor import sha_contained
        with tempfile.TemporaryDirectory() as d:
            comp = Path(d) / 'ka9q-radio'
            comp.mkdir()
            base, fork = self._mkrepo(comp)
            self.assertFalse(
                sha_contained('ka9q-radio', 'deadbeef9', fork, base=d))
            self.assertFalse(
                sha_contained('nonexistent', base, fork, base=d))


class DriftAncestryTests(unittest.TestCase):
    """manifest_drift_text: ancestry hook turns 'moved' into 'superset';
    default (no hook) stays strict."""

    def test_hook_marks_superset_and_default_stays_moved(self):
        from sigmond.doctor import manifest_drift_text
        live = _filler_live(MIN_COMPONENT_ROWS)
        name = sorted(live)[0]
        live[name] = 'feedface1'
        MANIFEST = ("components (live):\n" + _filler_rows(MIN_COMPONENT_ROWS))
        strict = manifest_drift_text(live, MANIFEST)
        self.assertEqual([d['status'] for d in strict], ['moved'])
        hooked = manifest_drift_text(
            live, MANIFEST,
            ancestry=lambda n, m, l: n == name)
        self.assertEqual([d['status'] for d in hooked], ['superset'])
        # Hook that vouches for nothing: unchanged.
        refused = manifest_drift_text(
            live, MANIFEST, ancestry=lambda n, m, l: False)
        self.assertEqual([d['status'] for d in refused], ['moved'])

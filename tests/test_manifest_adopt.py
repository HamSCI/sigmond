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

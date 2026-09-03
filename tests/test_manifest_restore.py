"""Tests for `smd admin manifest restore` — the remote-rollback half of
the blessed-manifest mechanism (`manifest_adopt`'s mirror).

``plan_restore`` (sigmond.manifest_restore) is pure — no filesystem, no
process state — and is tested directly here, mirroring
tests/test_manifest_adopt.py's structure and fixtures. The CLI wrapper
in bin/smd is exercised the way test_manifest_adopt.py exercises `smd
admin manifest adopt`: load bin/smd via SourceFileLoader and call the
cmd_* function with a fabricated argparse Namespace, so the test never
shells out for real — `subprocess.run` is patched with a fake that
records argv and answers each git subcommand from a small in-test
script.
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
from sigmond.manifest_restore import RestorePlan, plan_restore

REPO = Path(__file__).resolve().parent.parent


def _filler_rows(n, start=0):
    """Mirrors tests/test_manifest_adopt.py's helper of the same name:
    `n` synthetic component-row lines padding a manifest above
    MIN_COMPONENT_ROWS."""
    return ''.join(f"    filler-{i:02d}       ffff{i:03d}\n"
                   for i in range(start, start + n))


def _filler_live(n, start=0):
    return {f'filler-{i:02d}': f'ffff{i:03d}' for i in range(start, start + n)}


def _always_resolvable(name, sha):
    return True


def _never_resolvable(name, sha):
    return False


# ── plan_restore (pure) ──────────────────────────────────────────────

class PlanRestoreTests(unittest.TestCase):

    def test_exact_match_is_ok_all_keep(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        live = {'hf-timestd': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        plan = plan_restore(manifest_text, live, _never_resolvable)
        self.assertIsInstance(plan, RestorePlan)
        self.assertTrue(plan.ok, plan.refusals)
        self.assertEqual(plan.refusals, [])
        self.assertEqual(len(plan.actions), MIN_COMPONENT_ROWS)
        self.assertTrue(all(a == 'keep' for _, a, _, _ in plan.actions))
        self.assertEqual(plan.strays, [])

    def test_one_behind_and_resolvable_is_checkout_action(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        live = {'hf-timestd': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        plan = plan_restore(manifest_text, live, _always_resolvable)
        self.assertTrue(plan.ok, plan.refusals)
        by_name = {n: (a, f, t) for n, a, f, t in plan.actions}
        self.assertEqual(by_name['hf-timestd'], ('checkout', 'ccccccc', 'aaaaaaa'))
        # everything else stayed a keep
        self.assertEqual(
            sum(1 for a, _, _ in by_name.values() if a == 'keep'),
            MIN_COMPONENT_ROWS - 1)

    def test_one_behind_and_unresolvable_refuses_naming_it(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            "    wspr-recorder    bbbbbbb\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 2)
        )
        live = {'hf-timestd': 'ccccccc', 'wspr-recorder': 'bbbbbbb',
                **_filler_live(MIN_COMPONENT_ROWS - 2)}
        plan = plan_restore(manifest_text, live, _never_resolvable)
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.refusals), 1)
        self.assertIn('hf-timestd', plan.refusals[0])
        self.assertIn('aaaaaaa', plan.refusals[0])
        self.assertIn('not resolvable', plan.refusals[0])
        self.assertNotIn('wspr-recorder', ' '.join(plan.refusals))
        # the matching component still shows up as a keep action
        self.assertTrue(any(n == 'wspr-recorder' and a == 'keep'
                            for n, a, _, _ in plan.actions))

    def test_missing_checkout_refuses(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            "    wspr-recorder    bbbbbbb\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 2)
        )
        live = {'hf-timestd': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 2)}
        plan = plan_restore(manifest_text, live, _always_resolvable)
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.refusals), 1)
        self.assertIn('wspr-recorder', plan.refusals[0])
        self.assertIn('no checkout to restore', plan.refusals[0])

    def test_stray_live_component_is_never_a_refusal_and_never_an_action(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        live = {'hf-timestd': 'aaaaaaa', 'meteor-scatter': 'ddddddd',
                **_filler_live(MIN_COMPONENT_ROWS - 1)}
        plan = plan_restore(manifest_text, live, _never_resolvable)
        self.assertTrue(plan.ok, plan.refusals)
        self.assertEqual(len(plan.refusals), 0)
        self.assertTrue(any('meteor-scatter' in s for s in plan.strays))
        self.assertNotIn('meteor-scatter', [n for n, _, _, _ in plan.actions])

    def test_malformed_manifest_text_refuses_with_components_block_reason(self):
        plan = plan_restore("not a manifest at all\n", {'hf-timestd': 'aaaaaaa'},
                            _always_resolvable)
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.refusals), 1)
        self.assertIn('components block', plan.refusals[0])
        self.assertEqual(plan.actions, [])
        self.assertEqual(plan.strays, [])

    def test_empty_manifest_text_refuses_with_components_block_reason(self):
        plan = plan_restore("", {'hf-timestd': 'aaaaaaa'}, _always_resolvable)
        self.assertFalse(plan.ok)
        self.assertIn('components block', plan.refusals[0])

    def test_thin_components_block_below_row_floor_refuses(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            "    wspr-recorder    bbbbbbb\n"
        )
        plan = plan_restore(manifest_text, {'hf-timestd': 'aaaaaaa'},
                            _always_resolvable)
        self.assertFalse(plan.ok)
        self.assertIn('components block', plan.refusals[0])

    def test_mixed_length_sha_prefixes_that_agree_is_a_keep(self):
        manifest_text = (
            "components (live):\n"
            "    ka9q-radio       abc1234\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        live = {'ka9q-radio': 'abc1234567',
                **_filler_live(MIN_COMPONENT_ROWS - 1)}
        plan = plan_restore(manifest_text, live, _never_resolvable)
        self.assertTrue(plan.ok, plan.refusals)
        by_name = {n: a for n, a, _, _ in plan.actions}
        self.assertEqual(by_name['ka9q-radio'], 'keep')

    def test_refusals_list_every_divergent_component_not_just_the_first(self):
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
        plan = plan_restore(manifest_text, live, _never_resolvable)
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.refusals), 2)
        joined = ' '.join(plan.refusals)
        self.assertIn('hf-timestd', joined)
        self.assertIn('wspr-recorder', joined)

    def test_empty_live_components_refuses_every_manifest_entry(self):
        manifest_text = (
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        plan = plan_restore(manifest_text, {}, _always_resolvable)
        self.assertFalse(plan.ok)
        self.assertEqual(len(plan.refusals), MIN_COMPONENT_ROWS)
        self.assertEqual(plan.actions, [])


# ── CLI wrapper in bin/smd ───────────────────────────────────────────

def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_manifest_restore", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_manifest_restore", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class _FakeGit:
    """Records every argv smd shells out with and answers each git
    subcommand from a small script the test configures, so no test
    here needs a real git repository.

    ``dirty`` maps component name -> list of `git status --porcelain`
    lines (default: clean). ``unresolvable`` is a set of component
    names for which `git rev-parse --verify --quiet <sha>^{commit}`
    should fail (default: every SHA resolves). ``changed_files`` maps
    component name -> list of paths `git diff --name-only <from> <to>`
    should report changed (default: none, so install.sh is skipped).
    """

    def __init__(self, dirty=None, unresolvable=None, changed_files=None):
        self.calls = []
        self.dirty = dirty or {}
        self.unresolvable = unresolvable or set()
        self.changed_files = changed_files or {}

    def _component_of(self, cmd):
        # component checkouts live at .../<base>/<name>; find the -C arg.
        if '-C' in cmd:
            return Path(cmd[cmd.index('-C') + 1]).name
        return None

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        out = types.SimpleNamespace(returncode=0, stdout='', stderr='')
        name = self._component_of(cmd)
        if 'fetch' in cmd:
            return out
        if 'status' in cmd and '--porcelain' in cmd:
            lines = self.dirty.get(name, [])
            out.stdout = ''.join(f' M {f}\n' for f in lines)
            return out
        if 'rev-parse' in cmd and '--verify' in cmd:
            out.returncode = 1 if name in self.unresolvable else 0
            return out
        if 'checkout' in cmd and '--detach' in cmd:
            return out
        if 'diff' in cmd and '--name-only' in cmd:
            out.stdout = '\n'.join(self.changed_files.get(name, []))
            return out
        if cmd and cmd[0] == 'bash':
            return out
        return out


class ManifestRestoreCliDryRunTests(unittest.TestCase):
    """Dry-run (the default, no --apply) must never issue a `checkout`,
    whether the plan is ok or refused."""

    def setUp(self):
        import tempfile
        self.tdir = Path(tempfile.mkdtemp())
        self.base = self.tdir / 'base'
        self.base.mkdir()

    def _mkcheckout(self, name):
        d = self.base / name
        (d / '.git').mkdir(parents=True)
        return d

    def _manifest(self, extra_rows=0):
        p = self.tdir / 'candidate.txt'
        p.write_text(
            "components (live):\n"
            "    hf-timestd       aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        return p

    def _run(self, manifest_path, live, apply=False, no_fetch=False, fake=None):
        for name in live:
            self._mkcheckout(name)
        args = types.SimpleNamespace(
            path=str(manifest_path), apply=apply, no_fetch=no_fetch,
            base=str(self.base))
        fake = fake or _FakeGit()
        buf = io.StringIO()
        with mock.patch('sigmond.provenance.component_versions',
                         return_value=live), \
             mock.patch('subprocess.run', side_effect=fake):
            with contextlib.redirect_stdout(buf):
                rc = smd.cmd_manifest_restore(args)
        return rc, buf.getvalue(), fake

    def test_dry_run_never_issues_checkout_on_a_refused_plan(self):
        manifest = self._manifest()
        rc, out, fake = self._run(manifest, live={})  # missing checkouts -> refused
        self.assertEqual(rc, 1)
        self.assertFalse(any('checkout' in c and '--detach' in c
                             for c in fake.calls))

    def test_dry_run_never_issues_checkout_on_an_ok_plan(self):
        manifest = self._manifest()
        live = {'hf-timestd': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        rc, out, fake = self._run(manifest, live=live)
        self.assertEqual(rc, 0)
        self.assertFalse(any('checkout' in c and '--detach' in c
                             for c in fake.calls))

    def test_dry_run_fetches_as_the_checkout_owner(self):
        manifest = self._manifest()
        # hf-timestd is behind -> needs fetching to test resolvability.
        live = {'hf-timestd': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        rc, out, fake = self._run(manifest, live=live)
        fetch_calls = [c for c in fake.calls if 'fetch' in c]
        self.assertEqual(len(fetch_calls), 1)
        fc = fetch_calls[0]
        # Assert the INTENT — runs as the checkout owner — not the literal
        # argv[0].  `_runuser` resolves the binary to /usr/sbin/runuser (not on
        # an operator's PATH) and prepends `sudo -n` when not root, so argv[0]
        # is legitimately either.  Pinning the bare name here is what let the
        # FileNotFoundError on AC0G-ND 2026-09-03 pass a green suite.
        self.assertTrue(fc[0].endswith('runuser') or fc[0] == 'sudo', fc[:3])
        i = next(n for n, a in enumerate(fc) if a.endswith('runuser'))
        self.assertEqual(fc[i + 1], '-u')
        self.assertIn('--', fc)
        self.assertIn('git', fc)
        self.assertIn('--all', fc)
        self.assertIn('--quiet', fc)

    def test_no_fetch_flag_skips_fetch_entirely(self):
        manifest = self._manifest()
        live = {'hf-timestd': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        rc, out, fake = self._run(manifest, live=live, no_fetch=True)
        self.assertFalse(any('fetch' in c for c in fake.calls))

    def test_matching_components_are_never_fetched(self):
        manifest = self._manifest()
        live = {'hf-timestd': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        rc, out, fake = self._run(manifest, live=live)
        self.assertFalse(any('fetch' in c for c in fake.calls))


class ManifestRestoreDirtyTreeTests(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tdir = Path(tempfile.mkdtemp())
        self.base = self.tdir / 'base'
        self.base.mkdir()

    def _mkcheckout(self, name):
        d = self.base / name
        (d / '.git').mkdir(parents=True)
        return d

    def _manifest(self):
        p = self.tdir / 'candidate.txt'
        p.write_text(
            "components (live):\n"
            "    wspr-recorder    aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        return p

    def _run(self, manifest_path, live, fake):
        for name in live:
            self._mkcheckout(name)
        args = types.SimpleNamespace(
            path=str(manifest_path), apply=False, no_fetch=False,
            base=str(self.base))
        buf = io.StringIO()
        with mock.patch('sigmond.provenance.component_versions',
                         return_value=live), \
             mock.patch('subprocess.run', side_effect=fake):
            with contextlib.redirect_stdout(buf):
                rc = smd.cmd_manifest_restore(args)
        return rc, buf.getvalue()

    def test_dirty_tree_refuses_and_never_checks_out(self):
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit(dirty={'wspr-recorder': ['config.py']})
        rc, out = self._run(self._manifest(), live, fake)
        self.assertEqual(rc, 1)
        self.assertFalse(any('checkout' in c and '--detach' in c
                             for c in fake.calls))
        self.assertIn('wspr-recorder', out + '')

    def test_dirty_uv_lock_gets_the_specific_hint(self):
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit(dirty={'wspr-recorder': ['uv.lock']})
        buf = io.StringIO()
        errbuf = io.StringIO()
        for name in live:
            (self.base / name / '.git').mkdir(parents=True)
        args = types.SimpleNamespace(
            path=str(self._manifest()), apply=False, no_fetch=False,
            base=str(self.base))
        with mock.patch('sigmond.provenance.component_versions',
                         return_value=live), \
             mock.patch('subprocess.run', side_effect=fake):
            with contextlib.redirect_stdout(buf), \
                 contextlib.redirect_stderr(errbuf):
                smd.cmd_manifest_restore(args)
        combined = buf.getvalue() + errbuf.getvalue()
        self.assertIn('uv.lock', combined)
        self.assertIn('install.sh', combined)
        self.assertIn('git checkout -- uv.lock', combined)


class ManifestRestoreApplyTests(unittest.TestCase):
    """--apply path, with `_need_root` neutralised (elevation already
    happened / not needed) so the test never risks a real sudo re-exec."""

    def setUp(self):
        import tempfile
        self.tdir = Path(tempfile.mkdtemp())
        self.base = self.tdir / 'base'
        self.base.mkdir()

    def _mkcheckout(self, name):
        d = self.base / name
        (d / '.git').mkdir(parents=True)
        (d / 'scripts').mkdir()
        (d / 'scripts' / 'install.sh').write_text('#!/bin/bash\n')
        return d

    def _manifest(self, sha='aaaaaaa'):
        p = self.tdir / 'candidate.txt'
        p.write_text(
            "components (live):\n"
            f"    wspr-recorder    {sha}\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1)
        )
        return p

    def _run(self, manifest_path, live, fake, after_live=None):
        for name in live:
            self._mkcheckout(name)
        args = types.SimpleNamespace(
            path=str(manifest_path), apply=True, no_fetch=False,
            base=str(self.base))
        buf = io.StringIO()
        errbuf = io.StringIO()
        versions = mock.Mock(side_effect=[live, after_live if after_live is not None else live])
        with mock.patch.object(smd, '_need_root', return_value=False), \
             mock.patch('sigmond.provenance.component_versions',
                         side_effect=versions), \
             mock.patch('subprocess.run', side_effect=fake):
            with contextlib.redirect_stdout(buf), \
                 contextlib.redirect_stderr(errbuf):
                rc = smd.cmd_manifest_restore(args)
        # Refusals/errors print via _err (stderr); combine so a test can
        # assert on message text without caring which stream carried it.
        return rc, buf.getvalue() + errbuf.getvalue(), fake

    def test_install_sh_runs_when_pyproject_changed_between_shas(self):
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'wspr-recorder': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit(changed_files={'wspr-recorder': ['pyproject.toml']})
        rc, out, fake = self._run(self._manifest(), live, fake, after_live=after)
        self.assertTrue(any(c and c[0] == 'bash' for c in fake.calls))
        self.assertTrue(any('checkout' in c and '--detach' in c
                            for c in fake.calls))

    def test_install_sh_skipped_when_nothing_relevant_changed(self):
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'wspr-recorder': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit(changed_files={'wspr-recorder': ['README.md']})
        rc, out, fake = self._run(self._manifest(), live, fake, after_live=after)
        self.assertFalse(any(c and c[0] == 'bash' for c in fake.calls))
        self.assertTrue(any('checkout' in c and '--detach' in c
                            for c in fake.calls))

    def test_checkout_uses_detach_and_runs_as_owner(self):
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'wspr-recorder': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit()
        rc, out, fake = self._run(self._manifest(), live, fake, after_live=after)
        checkout_calls = [c for c in fake.calls
                          if 'checkout' in c and '--detach' in c]
        self.assertEqual(len(checkout_calls), 1)
        cc = checkout_calls[0]
        # Intent, not argv[0] — see the fetch test above.
        self.assertTrue(cc[0].endswith('runuser') or cc[0] == 'sudo', cc[:3])
        i = next(n for n, a in enumerate(cc) if a.endswith('runuser'))
        self.assertEqual(cc[i + 1], '-u')
        self.assertIn('aaaaaaa', cc)

    def test_post_apply_verification_ok_reports_success(self):
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'wspr-recorder': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit()
        rc, out, fake = self._run(self._manifest(), live, fake, after_live=after)
        self.assertEqual(rc, 0)
        self.assertIn('re-plan verifies all-keep', out)
        self.assertIn('0 stray component(s) untouched', out)

    def test_post_apply_verification_passes_with_a_stray_live_component(self):
        # The finding this test guards against: an earlier version used
        # plan_adopt STRICT for post-apply verification, which refuses
        # on ANY live component absent from the manifest -- so a
        # perfectly successful restore on a host carrying a sanctioned
        # extra (e.g. meteor-scatter, not in this manifest) reported
        # verification FAILURE. plan_restore's own re-plan must pass:
        # strays are informational, never a refusal.
        live = {'wspr-recorder': 'ccccccc', 'meteor-scatter': 'deadbee',
                **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'wspr-recorder': 'aaaaaaa', 'meteor-scatter': 'deadbee',
                **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit()
        rc, out, fake = self._run(self._manifest(), live, fake, after_live=after)
        self.assertEqual(rc, 0, out)
        self.assertIn('re-plan verifies all-keep', out)
        self.assertIn('1 stray component(s) untouched', out)
        # The stray is never named in any git argv -- it's genuinely
        # never touched, not just excluded from the final message.
        self.assertFalse(any('meteor-scatter' in str(c) for c in fake.calls))

    def test_post_apply_verification_failure_is_loud_and_exits_1(self):
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        # Simulate the checkout not actually landing (still 'ccccccc' after)
        # -- a manifest component still diverging after --apply.
        after = live
        fake = _FakeGit()
        rc, out, fake = self._run(self._manifest(), live, fake, after_live=after)
        self.assertEqual(rc, 1)
        self.assertIn('wspr-recorder', out)
        self.assertIn('not resolvable', out)
        self.assertNotIn('re-plan verifies all-keep', out)


if __name__ == '__main__':
    unittest.main()

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
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from sigmond.doctor import MIN_COMPONENT_ROWS
from sigmond.manifest_restore import RestorePlan, lock_rewrites, plan_restore

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


class LockRewritesTests(unittest.TestCase):
    """v3.82 row 6: which tracked changes a restore's install.sh made that
    the restore discards — lock files only, and only new ones."""

    def test_lock_rewrites_picks_new_lock_files_only(self):
        self.assertEqual(
            lock_rewrites({'a.py'}, {'a.py', 'uv.lock', 'sub/uv.lock', 'b.py'}),
            ['sub/uv.lock', 'uv.lock'])

    def test_lock_rewrites_ignores_a_lock_file_dirty_before(self):
        self.assertEqual(lock_rewrites({'uv.lock'}, {'uv.lock'}), [])


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
# A restore that reads its sigmond tree as below part 2 moves the marker and
# removes sigmond.toml.  Paths that never exist keep every test here off this
# host's /var/lib/hs-uploader and /etc/hs-uploader; a test that wants the step
# points these at its own scratch tree.
smd._RESTORE_MARKER_PATH = Path('/nonexistent/sigmond-test/state/carry-over-v1.done')
smd._RESTORE_PIPELINES_D = Path('/nonexistent/sigmond-test/etc/pipelines.d')


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
    ``rerender`` answers the restored smd's `admin uploader manifest
    --write --enable`: a (returncode, stdout, stderr) tuple, or an
    exception to raise (default: success).  ``rerender_kwargs`` keeps the
    keyword arguments that call received, and ``rerender_environ`` the
    process environment at that moment.
    """

    def __init__(self, dirty=None, unresolvable=None, changed_files=None,
                 rerender=(0, 'uploader: /etc/hs-uploader/pipelines.toml '
                              'already current (3 pipeline(s))\n', ''),
                 removed_files=None, blobs=None, install_rc=None,
                 diff_rc=None, listing='', disable_rc=None,
                 dirty_after_checkout=None, dirty_after_install=None,
                 discard_rc=None, status_rc=None):
        self.calls = []
        # status_rc: component -> exit codes of its successive `status
        # --porcelain` calls (the dirty-tree check, then before and after
        # its install.sh); a missing entry means 0.  A failed call prints
        # nothing on stdout.
        self.status_rc = status_rc or {}
        self.status_calls = {}
        self.dirty = dirty or {}
        # dirty_after_checkout / dirty_after_install: component -> paths
        # `status --porcelain` adds once that component's checkout has
        # landed / its install.sh has run.  discard_rc: path -> exit code
        # of `git checkout -- <path>` (default 0).
        self.dirty_after_checkout = dirty_after_checkout or {}
        self.dirty_after_install = dirty_after_install or {}
        self.discard_rc = discard_rc or {}
        self.checked_out, self.installed = set(), set()
        self.unresolvable = unresolvable or set()
        self.changed_files = changed_files or {}
        # removed_files: component -> paths `git diff --name-only
        # --diff-filter=D <newer> <older>` lists (in the newer commit only).
        # blobs: (component, path) -> bytes `git show <sha>:<path>` prints.
        # install_rc: component -> exit code of its install.sh (default 0).
        self.removed_files = removed_files or {}
        self.blobs = blobs or {}
        self.install_rc = install_rc or {}
        # diff_rc: component -> exit code of `git diff --diff-filter=D`.
        # listing: what `systemctl list-units` prints.
        # install_kwargs: the keyword arguments each install.sh call got.
        self.diff_rc = diff_rc or {}
        self.listing = listing
        self.install_kwargs = []
        # disable_rc: unit -> exit code of `systemctl disable` for it.
        self.disable_rc = disable_rc or {}
        self.rerender = rerender
        self.rerender_kwargs = None
        self.rerender_environ = None

    def _component_of(self, cmd):
        # component checkouts live at .../<base>/<name>; find the -C arg.
        if '-C' in cmd:
            return Path(cmd[cmd.index('-C') + 1]).name
        return None

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        out = types.SimpleNamespace(returncode=0, stdout='', stderr='')
        if 'uploader' in cmd and 'manifest' in cmd:
            self.rerender_kwargs = kwargs
            self.rerender_environ = dict(os.environ)
            if isinstance(self.rerender, BaseException):
                raise self.rerender
            out.returncode, out.stdout, out.stderr = self.rerender
            return out
        name = self._component_of(cmd)
        if 'fetch' in cmd:
            return out
        if 'status' in cmd and '--porcelain' in cmd:
            n = self.status_calls[name] = self.status_calls.get(name, 0) + 1
            rcs = self.status_rc.get(name, [])
            if n <= len(rcs) and rcs[n - 1]:
                out.returncode = rcs[n - 1]
                out.stderr = 'fatal: detected dubious ownership'
                return out
            lines = list(self.dirty.get(name, []))
            if name in self.checked_out:
                lines += self.dirty_after_checkout.get(name, [])
            if name in self.installed:
                lines += self.dirty_after_install.get(name, [])
            out.stdout = ''.join(f' M {f}\n' for f in dict.fromkeys(lines))
            return out
        if 'rev-parse' in cmd and '--verify' in cmd:
            out.returncode = 1 if name in self.unresolvable else 0
            return out
        if 'checkout' in cmd and '--detach' in cmd:
            self.checked_out.add(name)
            return out
        if 'checkout' in cmd and cmd[-2] == '--':
            out.returncode = self.discard_rc.get(cmd[-1], 0)
            if out.returncode:
                out.stderr = f"error: unable to unlink '{cmd[-1]}'"
            return out
        if 'diff' in cmd and '--diff-filter=D' in cmd:
            out.returncode = self.diff_rc.get(name, 0)
            out.stdout = '\n'.join(self.removed_files.get(name, []))
            return out
        if 'systemctl' in cmd and 'list-units' in cmd:
            out.stdout = self.listing
            return out
        if 'systemctl' in cmd and 'disable' in cmd:
            out.returncode = self.disable_rc.get(cmd[-1], 0)
            if out.returncode:
                out.stderr = f'Failed to disable unit {cmd[-1]}'
            return out
        if 'diff' in cmd and '--name-only' in cmd:
            out.stdout = '\n'.join(self.changed_files.get(name, []))
            return out
        if 'show' in cmd and name is not None:
            path = cmd[-1].split(':', 1)[1]
            blob = self.blobs.get((name, path))
            if blob is None:
                out.returncode, out.stdout = 128, b''
            else:
                out.stdout = blob
            return out
        sh_args = [a for a in cmd if str(a).endswith('install.sh')]
        if 'bash' in cmd and sh_args:
            self.install_kwargs.append(kwargs)
            sh = Path(sh_args[0])
            comp = (sh.parent.parent if sh.parent.name == 'scripts'
                    else sh.parent).name
            out.returncode = self.install_rc.get(comp, 0)
            self.installed.add(comp)
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

    def test_dry_run_never_rerenders_the_uploader_manifest(self):
        # D11's re-render belongs to --apply alone, even when the plan
        # would move a checkout.
        manifest = self._manifest()
        live = {'hf-timestd': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        rc, out, fake = self._run(manifest, live=live)
        self.assertEqual(rc, 0)
        self.assertFalse(any('uploader' in c for c in fake.calls))


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

    def test_an_unreadable_tree_refuses_and_never_checks_out(self):
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit(status_rc={'wspr-recorder': [128]})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc, out = self._run(self._manifest(), live, fake)
        out += err.getvalue()
        self.assertEqual(rc, 1, out)
        self.assertIn('wspr-recorder: cannot read the working tree', out)
        self.assertFalse(any('checkout' in c and '--detach' in c
                             for c in fake.calls))

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


# Captured at import, before any test patches `subprocess.run`.
_REAL_RUN = subprocess.run


class _RealStatusGit(_FakeGit):
    """_FakeGit, except `git status` runs for real against the checkout.

    The dirty-tree check's whole meaning lives in which flags it hands
    git — a fake that answers `status` from a script cannot tell
    tracked from untracked, so these tests ask git itself."""

    def __call__(self, cmd, **kwargs):
        if 'status' in cmd and '--porcelain' in cmd:
            self.calls.append(list(cmd))
            return _REAL_RUN(cmd, **kwargs)
        return super().__call__(cmd, **kwargs)


class ManifestRestoreUntrackedIsNotDirtTests(unittest.TestCase):
    """Untracked files are not dirt — the definition align_apply.dirty_files
    and doctor.git_state already use.

    The v3.69 rig test (PHASE G) refused to restore ka9q-web over
    `config_paths.h`, an untracked file its Makefile generates. Restore
    must refuse a modified TRACKED file and let an untracked one be: a
    checkout that would collide with one fails loudly as a git error."""

    def setUp(self):
        import tempfile
        self.tdir = Path(tempfile.mkdtemp())
        self.base = self.tdir / 'base'
        self.base.mkdir()
        self.repo = self.base / 'ka9q-web'
        self.repo.mkdir()
        for args in (('init', '-q', '-b', 'main'),
                     ('config', 'user.name', 'T'),
                     ('config', 'user.email', 't@example.com')):
            self._git(*args)
        (self.repo / 'Makefile').write_text('all:\n')
        self._git('add', 'Makefile')
        self._git('commit', '-q', '-m', 'A')
        # The build output that tripped PHASE G: untracked, never committed.
        (self.repo / 'config_paths.h').write_text('#define X 1\n')
        for name in _filler_live(MIN_COMPONENT_ROWS - 1):
            (self.base / name / '.git').mkdir(parents=True)

    def _git(self, *args):
        _REAL_RUN(['git', '-C', str(self.repo), *args], check=True,
                  capture_output=True, text=True)

    def _run(self):
        manifest = self.tdir / 'blessed.txt'
        manifest.write_text(
            "components (live):\n"
            "    ka9q-web         aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 1))
        # Live sits elsewhere, so the plan wants to check ka9q-web out.
        live = {'ka9q-web': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        args = types.SimpleNamespace(path=str(manifest), apply=False,
                                     no_fetch=True, base=str(self.base))
        fake = _RealStatusGit()
        buf, errbuf = io.StringIO(), io.StringIO()
        with mock.patch('sigmond.provenance.component_versions',
                         return_value=live), \
             mock.patch('subprocess.run', side_effect=fake):
            with contextlib.redirect_stdout(buf), \
                 contextlib.redirect_stderr(errbuf):
                rc = smd.cmd_manifest_restore(args)
        self.assertTrue(any('status' in c for c in fake.calls),
                        'the dirty-tree check never ran')
        return rc, buf.getvalue() + errbuf.getvalue()

    def test_untracked_build_output_alone_does_not_refuse(self):
        rc, out = self._run()
        self.assertNotIn('dirty working tree', out)
        self.assertNotIn('config_paths.h', out)
        self.assertEqual(rc, 0, out)
        self.assertIn('ka9q-web: checkout', out)

    def test_modified_tracked_file_still_refuses_naming_only_it(self):
        (self.repo / 'Makefile').write_text('all:\n\techo local fix\n')
        rc, out = self._run()
        self.assertEqual(rc, 1, out)
        self.assertIn('ka9q-web: dirty working tree (Makefile)', out)
        self.assertIn('never stashed or discarded', out)
        # The untracked neighbour is not named as dirt.
        self.assertNotIn('config_paths.h', out)
        # NEVER stash or discard: the local fix survives the refusal.
        self.assertIn('echo local fix', (self.repo / 'Makefile').read_text())
        self.assertTrue((self.repo / 'config_paths.h').exists())


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
        text = '#!/bin/bash\n'
        if getattr(self, '_yes_installer', False):
            text += 'for arg in "$@"; do case "$arg" in --yes) ;; esac; done\n'
        if getattr(self, '_rejecting_installer', False):
            text += ('for arg in "$@"; do\n  case "$arg" in\n'
                     '    *) echo "unknown arg: $arg" >&2; exit 2 ;;\n  esac\ndone\n')
        (d / 'scripts' / 'install.sh').write_text(text)
        return d

    def test_an_older_installer_without_yes_runs_with_no_arguments(self):
        self._rejecting_installer = True
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'wspr-recorder': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit()
        rc, out, fake = self._run(self._manifest(), live, fake, after_live=after)
        self.assertEqual(rc, 0, out)
        inst = self._installs(fake)
        self.assertEqual(len(inst), 1)
        self.assertNotIn('--yes', inst[0])
        self.assertIs(fake.install_kwargs[0].get('stdin'), subprocess.DEVNULL)

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
        # Intent: install.sh RUNS.  It needs root and smd refuses sudo, so the
        # invocation is `sudo -n bash <sh>` unless we are already root — pinning
        # argv[0] == 'bash' is the same over-specificity that hid the missing
        # escalation on B4 (2026-09-03).
        self.assertTrue(any('bash' in (c or []) for c in fake.calls))
        self.assertTrue(any('checkout' in c and '--detach' in c
                            for c in fake.calls))

    @staticmethod
    def _installs(fake):
        return [c for c in fake.calls
                if 'bash' in c and any(str(a).endswith('install.sh') for a in c)]

    def test_install_sh_runs_even_when_only_code_changed(self):
        # v3.73 row 4: `smd update` runs install.sh after every pull, so a
        # forward roll rewrites the units.  A restore that skipped it left
        # v3.72's consumers unit behind on a rolled-back v3.71 guest.
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'wspr-recorder': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit(changed_files={'wspr-recorder': ['systemd/x.service']})
        rc, out, fake = self._run(self._manifest(), live, fake, after_live=after)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self._installs(fake)), 1)
        self.assertIn('wspr-recorder: install.sh exit 0', out)

    def _two_component_manifest(self):
        p = self.tdir / 'candidate2.txt'
        p.write_text(
            "components (live):\n"
            "    psk-recorder     bbbbbbb\n"
            "    wspr-recorder    aaaaaaa\n"
            + _filler_rows(MIN_COMPONENT_ROWS - 2))
        return p

    def test_every_checkout_lands_before_any_install_runs(self):
        # An install.sh resolves its siblings by path ([tool.uv.sources]),
        # so each one must see every checkout already moved, as `smd
        # update` pulls everything before it installs anything.
        fill = _filler_live(MIN_COMPONENT_ROWS - 2)
        live = {'psk-recorder': 'ddddddd', 'wspr-recorder': 'ccccccc', **fill}
        after = {'psk-recorder': 'bbbbbbb', 'wspr-recorder': 'aaaaaaa', **fill}
        fake = _FakeGit()
        rc, out, fake = self._run(self._two_component_manifest(), live, fake,
                                  after_live=after)
        self.assertEqual(rc, 0, out)
        idx = [i for i, c in enumerate(fake.calls)
               if 'checkout' in c and '--detach' in c]
        inst = [i for i, c in enumerate(fake.calls)
                if 'bash' in c and any(str(a).endswith('install.sh') for a in c)]
        self.assertEqual((len(idx), len(inst)), (2, 2))
        self.assertLess(max(idx), min(inst))

    def test_ka9q_radio_runs_no_install_and_names_the_rebuild(self):
        # radiod is a compiled binary: a checkout move changes nothing
        # running, and a rebuild stays a deliberate step by hand.
        p = self.tdir / 'candidate3.txt'
        p.write_text("components (live):\n"
                     "    ka9q-radio       aaaaaaa\n"
                     + _filler_rows(MIN_COMPONENT_ROWS - 1))
        live = {'ka9q-radio': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'ka9q-radio': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit(changed_files={'ka9q-radio': ['pyproject.toml']})
        rc, out, fake = self._run(p, live, fake, after_live=after)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._installs(fake), [])
        self.assertIn('ka9q-radio: no install.sh run', out)
        self.assertIn('rebuild', out)

    def test_a_failed_install_sh_exits_1_and_names_it(self):
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'wspr-recorder': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit(install_rc={'wspr-recorder': 3})
        rc, out, fake = self._run(self._manifest(), live, fake, after_live=after)
        self.assertEqual(rc, 1, out)
        self.assertIn('wspr-recorder: install.sh exit 3', out)
        self.assertIn('install.sh failed', out)

    def test_restore_never_starts_stops_or_restarts_a_service(self):
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'wspr-recorder': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit()
        rc, out, fake = self._run(self._manifest(), live, fake, after_live=after)
        for c in fake.calls:
            if 'systemctl' in c:
                self.assertFalse({'start', 'stop', 'restart', 'try-restart',
                                  'reload-or-restart'} & set(c), c)

    # -- units the newer release added, which the older one never ships --

    SIG_MANIFEST_ROW = "    sigmond          aaaaaaa\n"

    def _sigmond_run(self, fake, installed):
        p = self.tdir / 'candidate-sig.txt'
        p.write_text("components (live):\n" + self.SIG_MANIFEST_ROW
                     + _filler_rows(MIN_COMPONENT_ROWS - 1))
        live = {'sigmond': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'sigmond': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        sysd = self.tdir / 'etc-systemd'
        sysd.mkdir()
        for rel, data in installed.items():
            (sysd / rel).parent.mkdir(parents=True, exist_ok=True)
            (sysd / rel).write_bytes(data)
        with mock.patch.object(smd, '_RESTORE_SYSTEMD_DIR', sysd):
            rc, out, fake = self._run(p, live, fake, after_live=after)
        return rc, out, fake, sysd

    def test_a_timer_only_the_newer_release_shipped_is_disabled_and_removed(self):
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/sigmond-new.timer',
                                       'systemd/sigmond-new.service']},
            blobs={('sigmond', 'systemd/sigmond-new.timer'): b'T',
                   ('sigmond', 'systemd/sigmond-new.service'): b'S'})
        rc, out, fake, sysd = self._sigmond_run(
            fake, {'sigmond-new.timer': b'T', 'sigmond-new.service': b'S'})
        self.assertEqual(rc, 0, out)
        self.assertFalse((sysd / 'sigmond-new.timer').exists())
        self.assertFalse((sysd / 'sigmond-new.service').exists())
        sc = [c for c in fake.calls if 'systemctl' in c]
        self.assertTrue(any('disable' in c and '--now' in c
                            and 'sigmond-new.timer' in c for c in sc), sc)
        # A service is disabled, never stopped: something may Require it.
        svc = [c for c in sc if 'sigmond-new.service' in c]
        self.assertTrue(svc and all('disable' in c and '--now' not in c
                                    for c in svc), svc)
        self.assertTrue(any('daemon-reload' in c for c in sc), sc)
        self.assertIn('retired sigmond-new.timer', out)

    def test_a_radiod_drop_in_only_the_newer_release_shipped_is_removed(self):
        rel = 'radiod@.service.d/45-sigmond-consumers.conf'
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/radiod-consumers.conf']},
            blobs={('sigmond', 'systemd/radiod-consumers.conf'): b'D'})
        rc, out, fake, sysd = self._sigmond_run(fake, {rel: b'D'})
        self.assertEqual(rc, 0, out)
        self.assertFalse((sysd / rel).exists())
        sc = [c for c in fake.calls if 'systemctl' in c]
        # A drop-in needs a daemon-reload and nothing else: radiod picks
        # the change up at its next start, never from this restore.
        self.assertFalse(any('disable' in c for c in sc), sc)
        self.assertTrue(any('daemon-reload' in c for c in sc), sc)

    def test_an_edited_unit_stays_and_the_restore_names_it(self):
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/sigmond-new.timer']},
            blobs={('sigmond', 'systemd/sigmond-new.timer'): b'T'})
        rc, out, fake, sysd = self._sigmond_run(
            fake, {'sigmond-new.timer': b'edited'})
        self.assertEqual(rc, 0, out)
        self.assertTrue((sysd / 'sigmond-new.timer').exists())
        self.assertIn('sigmond-new.timer', out)
        self.assertIn('left in place', out)
        self.assertFalse(any('systemctl' in c and 'disable' in c
                             for c in fake.calls))

    def test_no_unit_left_behind_means_no_daemon_reload(self):
        fake = _FakeGit()
        rc, out, fake, sysd = self._sigmond_run(fake, {})
        self.assertEqual(rc, 0, out)
        self.assertFalse(any('systemctl' in c and 'daemon-reload' in c
                             for c in fake.calls))

    # -- fix round 1 --------------------------------------------------

    def test_restore_imports_no_sigmond_module_after_the_first_checkout(self):
        # B3 rig 2026-10-20: restore checked sigmond out at v3.72, whose tree
        # has no sigmond/restore_units.py, and then imported it lazily.
        import importlib.abc
        blocked = []

        class Block(importlib.abc.MetaPathFinder):
            active = False

            def find_spec(self, fullname, path=None, target=None):
                if self.active and (fullname == 'sigmond'
                                    or fullname.startswith('sigmond.')):
                    blocked.append(fullname)
                    raise ImportError(f'{fullname}: sigmond moved under restore')
                return None

        blocker = Block()
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/sigmond-new.timer']},
            blobs={('sigmond', 'systemd/sigmond-new.timer'): b'T'})
        inner = fake.__call__

        def call(cmd, **kw):
            if 'checkout' in cmd and '--detach' in cmd:
                blocker.active = True
            return inner(cmd, **kw)
        fake.__call__ = call
        saved = sys.modules.pop('sigmond.restore_units', None)
        sys.meta_path.insert(0, blocker)
        try:
            p = self.tdir / 'candidate-sig.txt'
            p.write_text("components (live):\n" + self.SIG_MANIFEST_ROW
                         + _filler_rows(MIN_COMPONENT_ROWS - 1))
            live = {'sigmond': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
            after = {'sigmond': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
            sysd = self.tdir / 'etc-systemd'
            sysd.mkdir()
            (sysd / 'sigmond-new.timer').write_bytes(b'T')
            with mock.patch.object(smd, '_RESTORE_SYSTEMD_DIR', sysd):
                rc, out, _ = self._run(p, live, call, after_live=after)
        finally:
            sys.meta_path.remove(blocker)
            if saved is not None:
                sys.modules['sigmond.restore_units'] = saved
        self.assertTrue(blocker.active, 'the checkout never ran')
        self.assertEqual(blocked, [])
        self.assertEqual(rc, 0, out)
        self.assertFalse((sysd / 'sigmond-new.timer').exists())

    def test_install_runs_like_align_runs_it(self):
        # --yes and a closed stdin, as align and bring-up run it; the full
        # output kept in a log; ownership repaired once after the installs.
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'wspr-recorder': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        fake = _FakeGit()
        logs, repairs = [], []
        self._yes_installer = True
        with mock.patch.object(smd, '_write_update_log',
                               side_effect=lambda t, r: logs.append(t)), \
             mock.patch.object(smd, '_align_repair_ownership',
                               side_effect=lambda base: repairs.append(base)):
            rc, out, fake = self._run(self._manifest(), live, fake, after_live=after)
        self.assertEqual(rc, 0, out)
        inst = self._installs(fake)
        self.assertEqual(len(inst), 1)
        self.assertIn('--yes', inst[0])
        self.assertIs(fake.install_kwargs[0].get('stdin'), subprocess.DEVNULL)
        self.assertEqual(logs, ['wspr-recorder'])
        self.assertEqual(len(repairs), 1)

    def test_a_failing_unit_diff_exits_1(self):
        fake = _FakeGit(diff_rc={'sigmond': 128})
        rc, out, fake, sysd = self._sigmond_run(fake, {})
        self.assertEqual(rc, 1, out)
        self.assertIn('cannot list the units', out)

    def test_a_retired_unit_that_cannot_be_removed_exits_1(self):
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/sigmond-new.timer']},
            blobs={('sigmond', 'systemd/sigmond-new.timer'): b'T'})
        real_unlink = Path.unlink

        def unlink(self_, *a, **k):
            if self_.name == 'sigmond-new.timer':
                raise PermissionError('read-only')
            return real_unlink(self_, *a, **k)
        with mock.patch.object(Path, 'unlink', unlink):
            rc, out, fake, sysd = self._sigmond_run(fake, {'sigmond-new.timer': b'T'})
        self.assertEqual(rc, 1, out)
        self.assertIn('cannot remove', out)

    def test_a_retired_template_timer_takes_its_instances_down(self):
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/sigmond-x@.timer']},
            blobs={('sigmond', 'systemd/sigmond-x@.timer'): b'T'},
            listing='sigmond-x@b.timer loaded active waiting x\n')
        rc, out, fake, sysd = self._sigmond_run(
            fake, {'sigmond-x@.timer': b'T',
                   'timers.target.wants/sigmond-x@a.timer': b''})
        self.assertEqual(rc, 0, out)
        sc = [c for c in fake.calls if 'systemctl' in c and 'disable' in c]
        for inst in ('sigmond-x@a.timer', 'sigmond-x@b.timer'):
            self.assertTrue(any(inst in c and '--now' in c for c in sc), (inst, sc))
            self.assertIn(inst, out)
        self.assertFalse((sysd / 'sigmond-x@.timer').exists())

    def test_a_retired_template_service_disables_instances_without_stopping(self):
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/sigmond-y@.service']},
            blobs={('sigmond', 'systemd/sigmond-y@.service'): b'S'},
            listing='sigmond-y@c.service loaded active running y\n')
        rc, out, fake, sysd = self._sigmond_run(fake, {'sigmond-y@.service': b'S'})
        self.assertEqual(rc, 0, out)
        inst = [c for c in fake.calls if 'systemctl' in c and 'sigmond-y@c.service' in c]
        self.assertTrue(inst and all('disable' in c and '--now' not in c for c in inst), inst)

    def test_a_retired_socket_is_stopped(self):
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/sigmond-new.socket']},
            blobs={('sigmond', 'systemd/sigmond-new.socket'): b'K'})
        rc, out, fake, sysd = self._sigmond_run(fake, {'sigmond-new.socket': b'K'})
        self.assertEqual(rc, 0, out)
        self.assertTrue(any('systemctl' in c and 'disable' in c and '--now' in c
                            and 'sigmond-new.socket' in c for c in fake.calls))

    def test_an_operator_drop_in_dir_left_behind_is_named(self):
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/sigmond-new.service']},
            blobs={('sigmond', 'systemd/sigmond-new.service'): b'S'})
        rc, out, fake, sysd = self._sigmond_run(
            fake, {'sigmond-new.service': b'S',
                   'sigmond-new.service.d/override.conf': b'[Service]\n'})
        self.assertEqual(rc, 0, out)
        self.assertTrue((sysd / 'sigmond-new.service.d' / 'override.conf').exists())
        self.assertIn('sigmond-new.service.d', out)

    def test_a_drop_in_of_another_component_is_retired_too(self):
        p = self.tdir / 'candidate-ht.txt'
        p.write_text("components (live):\n    hf-timestd       aaaaaaa\n"
                     + _filler_rows(MIN_COMPONENT_ROWS - 1))
        live = {'hf-timestd': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'hf-timestd': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        sysd = self.tdir / 'etc-systemd'
        (sysd / 'timestd-fusion.service.d').mkdir(parents=True)
        dropin = sysd / 'timestd-fusion.service.d' / 'timestd-x.conf'
        dropin.write_bytes(b'D')
        fake = _FakeGit(removed_files={'hf-timestd': ['systemd/timestd-x.conf']},
                        blobs={('hf-timestd', 'systemd/timestd-x.conf'): b'D'})
        with mock.patch.object(smd, '_RESTORE_SYSTEMD_DIR', sysd):
            rc, out, fake = self._run(p, live, fake, after_live=after)
        self.assertEqual(rc, 0, out)
        self.assertFalse(dropin.exists())

    # -- fix round 2 --------------------------------------------------

    def test_a_failed_disable_names_the_unit_and_exits_1(self):
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/sigmond-new.timer']},
            blobs={('sigmond', 'systemd/sigmond-new.timer'): b'T'},
            disable_rc={'sigmond-new.timer': 1})
        rc, out, fake, sysd = self._sigmond_run(fake, {'sigmond-new.timer': b'T'})
        self.assertEqual(rc, 1, out)
        self.assertIn('sigmond-new.timer', out)
        self.assertIn('disable', out)
        self.assertNotIn('retired sigmond-new.timer', out)

    def test_a_failed_instance_disable_exits_1(self):
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/sigmond-x@.timer']},
            blobs={('sigmond', 'systemd/sigmond-x@.timer'): b'T'},
            listing='sigmond-x@b.timer loaded active waiting x\n',
            disable_rc={'sigmond-x@b.timer': 1})
        rc, out, fake, sysd = self._sigmond_run(fake, {'sigmond-x@.timer': b'T'})
        self.assertEqual(rc, 1, out)
        self.assertIn('sigmond-x@b.timer', out)

    def test_an_instance_drop_in_dir_left_behind_is_named(self):
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/sigmond-x@.timer']},
            blobs={('sigmond', 'systemd/sigmond-x@.timer'): b'T'},
            listing='sigmond-x@a.timer loaded active waiting x\n')
        rc, out, fake, sysd = self._sigmond_run(
            fake, {'sigmond-x@.timer': b'T',
                   'sigmond-x@a.timer.d/override.conf': b'[Timer]\n'})
        self.assertEqual(rc, 0, out)
        self.assertTrue((sysd / 'sigmond-x@a.timer.d' / 'override.conf').exists())
        self.assertIn('sigmond-x@a.timer.d', out)

    def test_the_newer_units_are_read_before_install_sh_runs(self):
        # The comparison reads the NEWER commit's bytes from git, so it
        # holds whichever order the steps take; it must still diff from the
        # sha the checkout left (newer) to the one it reached (older).
        fake = _FakeGit(
            removed_files={'sigmond': ['systemd/sigmond-new.timer']},
            blobs={('sigmond', 'systemd/sigmond-new.timer'): b'T'})
        rc, out, fake, sysd = self._sigmond_run(fake, {'sigmond-new.timer': b'T'})
        diffs = [c for c in fake.calls if '--diff-filter=D' in c]
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0][-2:], ['ccccccc', 'aaaaaaa'])
        shows = [c for c in fake.calls if 'show' in c and '-C' in c]
        self.assertTrue(shows and shows[0][-1] == 'ccccccc:systemd/sigmond-new.timer',
                        shows)

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

    # -- D11: re-render pipelines.toml with the restored sigmond --------

    LIVE = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
    AFTER = {'wspr-recorder': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}

    @staticmethod
    def _rerenders(fake):
        return [c for c in fake.calls if 'uploader' in c and 'manifest' in c]

    def test_apply_rerenders_with_the_restored_smd_after_the_checkouts(self):
        restored = self.base / 'sigmond' / 'bin' / 'smd'
        restored.parent.mkdir(parents=True)
        restored.write_text('#!/usr/bin/env python3\n')
        fake = _FakeGit(rerender=(0, 'uploader: manifest changed — restarting '
                                     'hs-uploader.service\n', ''))
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 0, out)
        rerenders = self._rerenders(fake)
        self.assertEqual(len(rerenders), 1)
        self.assertEqual(rerenders[0], [sys.executable, str(restored), 'admin',
                                        'uploader', 'manifest', '--write',
                                        '--enable'])
        checkout = next(i for i, c in enumerate(fake.calls)
                        if 'checkout' in c and '--detach' in c)
        self.assertGreater(fake.calls.index(rerenders[0]), checkout)
        self.assertEqual(smd._RESTORE_RERENDER_TIMEOUT_S, 600)
        self.assertEqual(fake.rerender_kwargs['timeout'],
                         smd._RESTORE_RERENDER_TIMEOUT_S)
        self.assertIn('restored to manifest — 1 component(s) moved', out)
        self.assertIn('| uploader: manifest changed — restarting '
                      'hs-uploader.service', out)
        # Task 9's rig greps the words after the mark; the line names the smd
        # that rendered, and it follows the restore line and the relay.
        success = ('\x1b[32m✓\x1b[0m  pipelines.toml re-rendered by the '
                   f'restored sigmond ({restored})')
        self.assertIn(success, out)
        self.assertLess(out.index('restored to manifest'),
                        out.index('| uploader: manifest changed'))
        self.assertLess(out.index('| uploader: manifest changed'),
                        out.index(success))

    def test_without_a_sigmond_checkout_the_running_smd_rerenders(self):
        fake = _FakeGit()
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._rerenders(fake)[0][1], str(smd._SCRIPT))
        self.assertIn(f're-rendered by the restored sigmond ({smd._SCRIPT})', out)

    def test_apply_rerenders_even_when_nothing_moved(self):
        # A re-run after a failed re-render repairs the manifest.
        fake = _FakeGit()
        rc, out, fake = self._run(self._manifest(), self.AFTER, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 0, out)
        self.assertFalse(any('checkout' in c for c in fake.calls))
        self.assertEqual(len(self._rerenders(fake)), 1)

    def test_a_failed_rerender_exits_1_and_names_the_fix(self):
        fake = _FakeGit(rerender=(1, '', 'smd: hs-uploader migrate failed\n'))
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 1)
        self.assertIn('restored to manifest', out)   # the checkouts did move
        self.assertIn('| smd: hs-uploader migrate failed', out)
        self.assertIn('re-render by the restored sigmond FAILED (exit 1)', out)
        self.assertIn('smd admin uploader manifest --write --enable', out)

    def test_a_hung_rerender_exits_1(self):
        fake = _FakeGit(rerender=subprocess.TimeoutExpired(['smd'], 600))
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 1)
        self.assertIn('timed out after 600 s', out)
        self.assertIn('FAILED (exit -1)', out)

    def test_a_restore_that_fails_verification_never_rerenders(self):
        fake = _FakeGit()
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.LIVE)
        self.assertEqual(rc, 1)
        self.assertEqual(self._rerenders(fake), [])
        self.assertIn('pipelines.toml not re-rendered', out)

    def test_the_child_runs_as_a_captured_text_child_under_a_timeout(self):
        # How the child runs matters as much as what it prints: a missing
        # timeout hangs the restore, a missing capture loses the relay, and
        # an inherited stdin could block it on a terminal.
        rc, out, fake = self._run(self._manifest(), self.LIVE, _FakeGit(),
                                  after_live=self.AFTER)
        self.assertEqual(rc, 0, out)
        kw = fake.rerender_kwargs
        self.assertTrue(kw['capture_output'])
        self.assertTrue(kw['text'])
        self.assertEqual(kw['errors'], 'replace')   # a stray byte cannot raise
        self.assertEqual(kw['stdin'], subprocess.DEVNULL)
        self.assertEqual(kw['timeout'], 600)

    def test_the_child_inherits_the_environment_unchanged(self):
        # Decision 6: `_need_root` sets SIGMOND_ALLOW_SUDO before this process
        # re-executes under sudo, and the child must see it.  A PYTHONPATH
        # naming the running (newer) checkout would make the restored smd
        # import the newer lib it exists to replace.
        with mock.patch.dict(os.environ, {'SIGMOND_ALLOW_SUDO': '1'}):
            os.environ.pop('PYTHONPATH', None)
            rc, out, fake = self._run(self._manifest(), self.LIVE, _FakeGit(),
                                      after_live=self.AFTER)
        self.assertEqual(rc, 0, out)
        self.assertNotIn('env', fake.rerender_kwargs)
        self.assertEqual(fake.rerender_environ.get('SIGMOND_ALLOW_SUDO'), '1')
        self.assertNotIn('PYTHONPATH', fake.rerender_environ)

    def test_a_child_that_cannot_start_exits_1_with_exit_minus_1(self):
        fake = _FakeGit(rerender=FileNotFoundError(2, 'No such file or '
                                                      'directory'))
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 1)
        self.assertIn('restored to manifest', out)
        self.assertIn('could not run', out)
        self.assertIn('FAILED (exit -1)', out)

    def _hung_after_printing(self, partial):
        fake = _FakeGit(rerender=subprocess.TimeoutExpired(
            ['smd'], 600, output=partial, stderr=b'warn \xff\n'))
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 1)
        self.assertIn('| uploader: installing hs-uploader', out)
        self.assertIn('| warn \ufffd', out)
        self.assertLess(out.index('| uploader: installing'),
                        out.index('| warn'))
        self.assertLess(out.index('| warn'), out.index('| timed out after 600 s'))
        self.assertIn('FAILED (exit -1)', out)

    def test_a_hung_rerender_relays_what_the_child_already_printed(self):
        # subprocess keeps the output as bytes (None when the child printed
        # nothing); that output says where the child hung.
        self._hung_after_printing(b'uploader: installing hs-uploader\n')

    def test_a_hung_rerender_relays_partial_output_given_as_text(self):
        self._hung_after_printing('uploader: installing hs-uploader\n')

    def test_the_relay_is_stdout_then_stderr_without_blank_lines(self):
        # Task 9's rig reads these lines.  The unterminated last stdout line
        # must not fuse with the first stderr line.
        fake = _FakeGit(rerender=(0, 'out-1\n\nout-2', 'err-1\n\n'))
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.AFTER)
        self.assertEqual(rc, 0, out)
        lines = [ln.strip() for ln in out.splitlines()
                 if ln.strip().startswith('|')]
        self.assertEqual(lines, ['| out-1', '| out-2', '| err-1'])

    def _refused_apply_never_rerenders(self, fake):
        rc, out, fake = self._run(self._manifest(), self.LIVE, fake,
                                  after_live=self.LIVE)
        self.assertEqual(rc, 1)
        self.assertIn('REFUSED', out)
        self.assertFalse(any('checkout' in c and '--detach' in c
                             for c in fake.calls))
        self.assertEqual(self._rerenders(fake), [])

    def test_a_refused_plan_with_apply_never_rerenders(self):
        self._refused_apply_never_rerenders(
            _FakeGit(unresolvable={'wspr-recorder'}))

    def test_a_dirty_tree_refusal_with_apply_never_rerenders(self):
        self._refused_apply_never_rerenders(
            _FakeGit(dirty={'wspr-recorder': ['lib/x.py']}))


    # -- v3.82 row 6: lock files an older install.sh rewrote --

    @staticmethod
    def _discards(fake):
        return [c for c in fake.calls
                if 'checkout' in c and len(c) > 1 and c[-2] == '--']

    def _lock_run(self, fake):
        live = {'wspr-recorder': 'ccccccc', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        after = {'wspr-recorder': 'aaaaaaa', **_filler_live(MIN_COMPONENT_ROWS - 1)}
        return self._run(self._manifest(), live, fake, after_live=after)

    def test_a_lock_file_the_installer_rewrote_is_discarded_and_named(self):
        # Rolling v3.81 back to v3.78 ran superdarn-sounder's old install.sh,
        # whose unfrozen `uv sync` rewrote uv.lock and left the tree dirty.
        fake = _FakeGit(dirty_after_install={'wspr-recorder': ['uv.lock']})
        rc, out, fake = self._lock_run(fake)
        self.assertEqual(rc, 0, out)
        self.assertTrue(any(c[-3:] == ['checkout', '--', 'uv.lock']
                            for c in fake.calls), fake.calls)
        self.assertIn('wspr-recorder: discarded uv.lock', out)

    def test_the_discard_follows_the_install(self):
        fake = _FakeGit(dirty_after_install={'wspr-recorder': ['uv.lock']})
        rc, out, fake = self._lock_run(fake)
        inst = [i for i, c in enumerate(fake.calls)
                if 'bash' in c and any(str(a).endswith('install.sh') for a in c)]
        disc = [i for i, c in enumerate(fake.calls) if c in self._discards(fake)]
        self.assertEqual((len(inst), len(disc)), (1, 1))
        self.assertLess(inst[0], disc[0])

    def test_a_lock_file_dirty_before_install_is_kept(self):
        # Review Focus 2: a change that stood before the install.sh ran (here
        # it appears once the checkout lands, as a sibling's install could
        # make it) is not the installer's to discard.
        fake = _FakeGit(dirty_after_checkout={'wspr-recorder': ['uv.lock']},
                        dirty_after_install={'wspr-recorder': ['uv.lock']})
        rc, out, fake = self._lock_run(fake)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._discards(fake), [])
        self.assertNotIn('discarded', out)

    def test_a_non_lock_file_the_installer_changed_is_left_alone(self):
        fake = _FakeGit(dirty_after_install={'wspr-recorder': ['src/x.py']})
        rc, out, fake = self._lock_run(fake)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._discards(fake), [])

    def test_a_failed_install_still_discards_its_lock_rewrite(self):
        fake = _FakeGit(dirty_after_install={'wspr-recorder': ['uv.lock']},
                        install_rc={'wspr-recorder': 3})
        rc, out, fake = self._lock_run(fake)
        self.assertEqual(rc, 1, out)
        self.assertEqual(len(self._discards(fake)), 1)

    def test_a_failed_discard_warns_with_the_command(self):
        fake = _FakeGit(dirty_after_install={'wspr-recorder': ['uv.lock']},
                        discard_rc={'uv.lock': 1})
        rc, out, fake = self._lock_run(fake)
        self.assertEqual(rc, 1, out)
        d = self.base / 'wspr-recorder'
        self.assertIn(f'git -C {d} checkout -- uv.lock', out)
        self.assertIn('unable to unlink', out)
        self.assertIn('wspr-recorder', out.split('put back for')[1])

    def test_a_failed_status_before_install_discards_nothing(self):
        # Review Focus 2: an unreadable `before` must not read as "clean",
        # or a uv.lock changed before the install would count as a rewrite.
        fake = _FakeGit(dirty_after_checkout={'wspr-recorder': ['uv.lock']},
                        dirty_after_install={'wspr-recorder': ['uv.lock']},
                        status_rc={'wspr-recorder': [0, 128]})
        rc, out, fake = self._lock_run(fake)
        self.assertEqual(rc, 1, out)
        self.assertEqual(self._discards(fake), [])
        self.assertIn('wspr-recorder: cannot read the working tree', out)
        self.assertIn('wspr-recorder', out.split('put back for')[1])

    def test_a_failed_status_after_install_discards_nothing(self):
        fake = _FakeGit(dirty_after_install={'wspr-recorder': ['uv.lock']},
                        status_rc={'wspr-recorder': [0, 0, 128]})
        rc, out, fake = self._lock_run(fake)
        self.assertEqual(rc, 1, out)
        self.assertEqual(self._discards(fake), [])
        self.assertIn('wspr-recorder: cannot read the working tree', out)

    def test_a_failed_discard_is_not_reported_as_a_failed_install(self):
        fake = _FakeGit(dirty_after_install={'wspr-recorder': ['uv.lock']},
                        discard_rc={'uv.lock': 1})
        rc, out, fake = self._lock_run(fake)
        self.assertEqual(rc, 1, out)
        self.assertNotIn('install.sh failed', out)
        self.assertIn('wspr-recorder', out.split('put back for')[1])

    def test_the_discard_runs_as_the_checkout_owner(self):
        fake = _FakeGit(dirty_after_install={'wspr-recorder': ['uv.lock']})
        rc, out, fake = self._lock_run(fake)
        (dc,) = self._discards(fake)
        i = next(n for n, a in enumerate(dc) if a.endswith('runuser'))
        owner = (self.base / 'wspr-recorder').owner()
        self.assertEqual(dc[i + 1:i + 4], ['-u', owner, '--'])
        self.assertEqual(dc[i + 4:], ['git', '-C', str(self.base / 'wspr-recorder'),
                                      'checkout', '--', 'uv.lock'])


class RestoreRerenderRealChildTests(unittest.TestCase):
    """`_restore_rerender_uploader` against a REAL child process: a stub
    `<base>/sigmond/bin/smd` that only prints, run by `sys.executable` the way
    the restore runs the restored smd.  The fakes above cannot show that the
    child is a separate process whose bytes survive the pipe.  Nothing here
    touches a station or runs sigmond."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        (self.base / 'sigmond' / 'bin').mkdir(parents=True)
        # This stub tree lacks sink_carryover.py, so the restore reads it as
        # below part 2: keep its marker move and sigmond.toml removal off
        # this host's /var/lib and /etc.
        for name, rel in (('_RESTORE_MARKER_PATH', 'state/carry-over-v1.done'),
                          ('_RESTORE_PIPELINES_D', 'etc/pipelines.d')):
            p = mock.patch.object(smd, name, self.base / rel)
            p.start()
            self.addCleanup(p.stop)

    def _call(self, stub):
        (self.base / 'sigmond' / 'bin' / 'smd').write_text(stub)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = smd._restore_rerender_uploader(self.base)
        return rc, buf.getvalue()

    def test_stdout_then_stderr_and_a_failed_exit(self):
        rc, out = self._call(
            "import sys\n"
            "print('out-1'); print(); print('out-2')\n"
            "print('err-1', file=sys.stderr)\n"
            "sys.exit(3)\n")
        self.assertEqual(rc, 1)
        lines = [ln.strip() for ln in out.splitlines()
                 if ln.strip().startswith('|')]
        self.assertEqual(lines, ['| out-1', '| out-2', '| err-1'])
        self.assertIn('FAILED (exit 3)', out)

    def test_the_child_gets_the_subcommand_and_the_inherited_environment(self):
        stub = ("import os, sys\n"
                "print('argv', sys.argv[1:])\n"
                "print('allow', os.environ.get('SIGMOND_ALLOW_SUDO'))\n"
                "print('pythonpath', os.environ.get('PYTHONPATH'))\n")
        with mock.patch.dict(os.environ, {'SIGMOND_ALLOW_SUDO': '1'}):
            os.environ.pop('PYTHONPATH', None)
            rc, out = self._call(stub)
        self.assertEqual(rc, 0, out)
        self.assertIn("| argv ['admin', 'uploader', 'manifest', '--write', "
                      "'--enable']", out)
        self.assertIn('| allow 1', out)
        self.assertIn('| pythonpath None', out)
        self.assertIn('re-rendered by the restored sigmond', out)

    def test_output_that_is_not_utf8_does_not_raise(self):
        rc, out = self._call("import sys\n"
                             "sys.stdout.buffer.write(b'ok \\xff bad\\n')\n")
        self.assertEqual(rc, 0, out)
        self.assertIn('| ok \ufffd bad', out)

    def test_a_hung_child_is_killed_and_its_output_relayed(self):
        stub = ("import time\n"
                "print('uploader: installing hs-uploader', flush=True)\n"
                "time.sleep(60)\n")
        with mock.patch.object(smd, '_RESTORE_RERENDER_TIMEOUT_S', 2):
            rc, out = self._call(stub)
        self.assertEqual(rc, 1)
        self.assertIn('| uploader: installing hs-uploader', out)
        self.assertLess(out.index('| uploader: installing'),
                        out.index('| timed out after 2 s'))
        self.assertIn('FAILED (exit -1)', out)


if __name__ == '__main__':
    unittest.main()


class RetireUnitsRealGitTests(unittest.TestCase):
    """_restore_retire_units against a real repository."""

    def git(self, *a):
        _REAL_RUN(['git', '-C', str(self.repo), *a], check=True,
                  capture_output=True, text=True)

    def test_a_renamed_unit_counts_as_retired(self):
        tdir = Path(tempfile.mkdtemp())
        self.repo = tdir / 'sigmond'
        (self.repo / 'systemd').mkdir(parents=True)
        self.git('init', '-q')
        self.git('config', 'user.email', 't@t')
        self.git('config', 'user.name', 't')
        unit = b'[Unit]\nDescription=a timer whose body stays the same\n' * 4
        (self.repo / 'systemd' / 'sigmond-old.timer').write_bytes(unit)
        self.git('add', '-A'); self.git('commit', '-qm', 'older')
        older = _REAL_RUN(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'],
                          capture_output=True, text=True).stdout.strip()
        self.git('mv', 'systemd/sigmond-old.timer', 'systemd/sigmond-new.timer')
        self.git('commit', '-qm', 'newer')
        newer = _REAL_RUN(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'],
                          capture_output=True, text=True).stdout.strip()
        sysd = tdir / 'etc-systemd'
        sysd.mkdir()
        (sysd / 'sigmond-new.timer').write_bytes(unit)
        calls = []

        def run(cmd, **kw):
            if cmd and cmd[0] == 'git':
                return _REAL_RUN(cmd, **kw)
            calls.append(list(cmd))
            return types.SimpleNamespace(returncode=0, stdout='', stderr='')
        buf = io.StringIO()
        with mock.patch.object(smd, '_RESTORE_SYSTEMD_DIR', sysd), \
             mock.patch('subprocess.run', side_effect=run), \
             contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            smd._restore_retire_units(self.repo, 'sigmond', newer, older)
        self.assertFalse((sysd / 'sigmond-new.timer').exists(), buf.getvalue())


# ── restore below part 2: the marker aside, sigmond.toml gone ────────
# (spec §6.7, §10 "Rollback of both" / "Rollback of sigmond alone", test 22)

import re
import shutil
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sink_carryover import Runner, Station, needs_real  # noqa: E402

MARKER = 'carry-over-v1.done'
ASIDE_RE = re.compile(re.escape(MARKER) + r'\.rolled-back-(\d{8}T\d{6}Z)$')
SWITCHES = {
    'site.toml': b'state = "fill"\n[[history]]\nto = "fill"\n',
    'psk-recorder.toml': b'state = "off"\n[[history]]\nto = "off"\n',
    'wspr-recorder.toml': b'state = "upload"\n',
}


class _HsTree:
    """A scratch stand-in for /var/lib/hs-uploader and /etc/hs-uploader,
    and a restore base whose sigmond checkout carries part 2 or not."""

    def __init__(self, test, *, part2, restored_smd=True):
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = self.tmp / 'var-lib-hs-uploader'
        self.state.mkdir()
        self.marker = self.state / MARKER
        self.marker.write_bytes(b'marker\n')
        self.etc = self.tmp / 'etc-hs-uploader'
        self.pipelines_d = self.etc / 'pipelines.d'
        self.pipelines_d.mkdir(parents=True)
        self.sigmond_toml = self.pipelines_d / 'sigmond.toml'
        self.sigmond_toml.write_text('[[pipeline]]\nname = "heartbeat"\n')
        self.sinks = self.etc / 'sinks'
        self.sinks.mkdir()
        for name, data in SWITCHES.items():
            (self.sinks / name).write_bytes(data)
        self.base = self.tmp / 'base'
        self.sigmond = self.base / 'sigmond'
        (self.sigmond / 'lib' / 'sigmond').mkdir(parents=True)
        if restored_smd:
            (self.sigmond / 'bin').mkdir()
            (self.sigmond / 'bin' / 'smd').write_text('print("restored")\n')
        if part2:
            self.carryover().write_text('# part 2\n')

    def carryover(self):
        return self.sigmond / 'lib' / 'sigmond' / 'sink_carryover.py'

    def asides(self):
        return sorted(p.name for p in self.state.iterdir()
                      if p.name.startswith(MARKER + '.'))

    def switches(self):
        return {p.name: p.read_bytes() for p in sorted(self.sinks.iterdir())}

    def call(self, now=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = smd._restore_below_part2(self.base, marker=self.marker,
                                          pipelines_d=self.pipelines_d,
                                          now=now)
        return rc, buf.getvalue()


class RestoreBelowPart2Tests(unittest.TestCase):

    def test_below_part2_moves_the_marker_aside_and_removes_sigmond_toml(self):
        t = _HsTree(self, part2=False)
        before = datetime.now(timezone.utc).replace(microsecond=0)
        rc, out = t.call()
        after = datetime.now(timezone.utc)
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.lexists(t.marker), out)
        asides = t.asides()
        self.assertEqual(len(asides), 1, asides)
        when = datetime.strptime(ASIDE_RE.match(asides[0]).group(1),
                                 '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        self.assertLessEqual(before, when)
        self.assertLessEqual(when, after)
        self.assertEqual((t.state / asides[0]).read_bytes(), b'marker\n')
        self.assertFalse(os.path.lexists(t.sigmond_toml), out)
        self.assertEqual(t.switches(), SWITCHES)
        self.assertIn(asides[0], out)
        self.assertIn('sigmond.toml', out)

    def test_a_part2_tree_leaves_both(self):
        t = _HsTree(self, part2=True)
        rc, out = t.call()
        self.assertEqual(rc, 0, out)
        self.assertEqual(t.marker.read_bytes(), b'marker\n')
        self.assertEqual(t.asides(), [])
        self.assertTrue(t.sigmond_toml.is_file())
        self.assertEqual(t.switches(), SWITCHES)
        self.assertEqual(out, '')

    def test_no_restored_smd_means_the_running_part2_smd_renders(self):
        # _restore_rerender_uploader falls back to the running smd.
        t = _HsTree(self, part2=False, restored_smd=False)
        rc, out = t.call()
        self.assertEqual(rc, 0, out)
        self.assertTrue(t.marker.is_file())
        self.assertTrue(t.sigmond_toml.is_file())

    def test_a_symlink_at_sink_carryover_does_not_count_as_part2(self):
        t = _HsTree(self, part2=False)
        real = t.tmp / 'elsewhere.py'
        real.write_text('# part 2\n')
        t.carryover().symlink_to(real)
        rc, out = t.call()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(t.asides()), 1)
        self.assertFalse(os.path.lexists(t.sigmond_toml))

    def test_a_symlinked_lib_sigmond_does_not_count_as_part2(self):
        t = _HsTree(self, part2=False)
        other = t.tmp / 'other-lib'
        other.mkdir()
        (other / 'sink_carryover.py').write_text('# part 2\n')
        shutil.rmtree(t.sigmond / 'lib' / 'sigmond')
        (t.sigmond / 'lib' / 'sigmond').symlink_to(other)
        rc, out = t.call()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(t.asides()), 1)

    def test_a_symlink_at_the_rolled_back_name_does_not_redirect_the_move(self):
        t = _HsTree(self, part2=False)
        now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
        planted = t.state / (MARKER + '.rolled-back-20261023T120000Z')
        victim = t.tmp / 'victim'
        victim.write_bytes(b'victim\n')
        planted.symlink_to(victim)
        rc, out = t.call(now=now)
        self.assertEqual(rc, 0, out)
        self.assertTrue(planted.is_symlink())
        self.assertEqual(os.readlink(planted), str(victim))
        self.assertEqual(victim.read_bytes(), b'victim\n')
        moved = t.state / (MARKER + '.rolled-back-20261023T120000Z.1')
        self.assertFalse(moved.is_symlink())
        self.assertEqual(moved.read_bytes(), b'marker\n')
        self.assertFalse(os.path.lexists(t.marker))

    def test_no_marker_and_no_state_directory_change_nothing(self):
        t = _HsTree(self, part2=False)
        shutil.rmtree(t.state)
        rc, out = t.call()
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.lexists(t.sigmond_toml))
        # positive control: the same harness sees a move when one happens
        t2 = _HsTree(self, part2=False)
        t2.call()
        self.assertEqual(len(t2.asides()), 1)

    def test_no_hs_uploader_etc_changes_nothing(self):
        t = _HsTree(self, part2=False)
        shutil.rmtree(t.etc)
        rc, out = t.call()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(t.asides()), 1)

    def test_a_symlinked_state_directory_refuses_and_exits_1(self):
        t = _HsTree(self, part2=False)
        real = t.tmp / 'real-state'
        t.state.rename(real)
        t.state.symlink_to(real)
        rc, out = t.call()
        self.assertEqual(rc, 1, out)
        self.assertTrue((real / MARKER).is_file())
        self.assertEqual(sorted(p.name for p in real.iterdir()), [MARKER])
        self.assertIn('could not be moved aside', out)
        # sigmond.toml still goes: the two steps stand apart
        self.assertFalse(os.path.lexists(t.sigmond_toml))

    def test_a_symlinked_pipelines_d_refuses_and_exits_1(self):
        t = _HsTree(self, part2=False)
        real = t.etc / 'real-pipelines.d'
        t.pipelines_d.rename(real)
        t.pipelines_d.symlink_to(real)
        rc, out = t.call()
        self.assertEqual(rc, 1, out)
        self.assertTrue((real / 'sigmond.toml').is_file())
        self.assertIn('could not be removed', out)
        self.assertEqual(len(t.asides()), 1)


class RestoreProbeTheRenderingTreeTests(unittest.TestCase):
    """M-1: the probe judges the tree whose smd renders, so a restored
    bin/smd that links into another tree is judged by that tree."""

    def test_smd_linking_into_an_old_tree_counts_as_below_part2(self):
        t = _HsTree(self, part2=True)       # the checkout looks like part 2
        old = t.tmp / 'old-sigmond'
        (old / 'bin').mkdir(parents=True)
        (old / 'lib' / 'sigmond').mkdir(parents=True)
        (old / 'bin' / 'smd').write_text('print("v3.76")\n')
        smd_link = t.sigmond / 'bin' / 'smd'
        smd_link.unlink()
        smd_link.symlink_to(old / 'bin' / 'smd')
        rc, out = t.call()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(t.asides()), 1)
        self.assertFalse(os.path.lexists(t.sigmond_toml))

    def test_smd_linking_into_a_part2_tree_leaves_both(self):
        t = _HsTree(self, part2=False)      # the checkout looks old
        new = t.tmp / 'new-sigmond'
        (new / 'bin').mkdir(parents=True)
        (new / 'lib' / 'sigmond').mkdir(parents=True)
        (new / 'lib' / 'sigmond' / 'sink_carryover.py').write_text('#\n')
        (new / 'bin' / 'smd').write_text('print("part 2")\n')
        smd_link = t.sigmond / 'bin' / 'smd'
        smd_link.unlink()
        smd_link.symlink_to(new / 'bin' / 'smd')
        rc, out = t.call()
        self.assertEqual(rc, 0, out)
        self.assertEqual(t.asides(), [])
        self.assertTrue(t.sigmond_toml.is_file())

    def test_the_failure_line_names_the_marker_and_its_target(self):
        t = _HsTree(self, part2=False)
        real = t.tmp / 'real-state'
        t.state.rename(real)
        t.state.symlink_to(real)
        rc, out = t.call()
        self.assertEqual(rc, 1, out)
        self.assertIn(str(t.marker), out)
        self.assertIn(f'{t.marker}.rolled-back-<UTC>', out)


class RestoreUnverifiedStillMovesTheMarkerTests(unittest.TestCase):
    """I-1: a restore that moved sigmond below part 2 but fails
    re-verification still moves the marker aside and removes sigmond.toml;
    it still never re-renders (D11)."""

    setUp = ManifestRestoreApplyTests.setUp
    _mkcheckout = ManifestRestoreApplyTests._mkcheckout
    _run = ManifestRestoreApplyTests._run

    def _go(self, after_sha):
        t = _HsTree(self, part2=False)
        restored = self.base / 'sigmond' / 'bin' / 'smd'
        restored.parent.mkdir(parents=True)
        restored.write_text('#!/usr/bin/env python3\n')
        p = self.tdir / 'candidate-sig.txt'
        p.write_text("components (live):\n    sigmond          aaaaaaa\n"
                     + _filler_rows(MIN_COMPONENT_ROWS - 1))
        fill = _filler_live(MIN_COMPONENT_ROWS - 1)
        sysd = self.tdir / 'etc-systemd'
        sysd.mkdir()
        with mock.patch.object(smd, '_RESTORE_SYSTEMD_DIR', sysd), \
             mock.patch.object(smd, '_RESTORE_MARKER_PATH', t.marker), \
             mock.patch.object(smd, '_RESTORE_PIPELINES_D', t.pipelines_d):
            rc, out, fake = self._run(
                p, {'sigmond': 'ccccccc', **fill}, _FakeGit(),
                after_live={'sigmond': after_sha, **fill})
        rerenders = [c for c in fake.calls
                     if 'uploader' in c and 'manifest' in c]
        return t, rc, out, rerenders

    def test_an_unverified_restore_moves_the_marker_but_never_renders(self):
        t, rc, out, rerenders = self._go('bbbbbbb')
        self.assertEqual(rc, 1, out)
        self.assertIn('STILL finds a divergence', out)
        self.assertEqual(rerenders, [])
        self.assertEqual(len(t.asides()), 1, out)
        self.assertFalse(os.path.lexists(t.sigmond_toml))
        self.assertEqual(t.switches(), SWITCHES)

    def test_positive_control_a_verified_restore_moves_and_renders(self):
        t, rc, out, rerenders = self._go('aaaaaaa')
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(rerenders), 1)
        self.assertEqual(len(t.asides()), 1, out)


class RestoreBelowPart2BeforeRenderTests(unittest.TestCase):
    """Through `_restore_rerender_uploader`: the move and the removal land
    before the restored smd runs, and a failed move still re-renders but
    exits 1."""

    def _rerender(self, t, child):
        (t.sigmond / 'bin' / 'smd').write_text(child)
        buf = io.StringIO()
        with mock.patch.object(smd, '_RESTORE_MARKER_PATH', t.marker), \
             mock.patch.object(smd, '_RESTORE_PIPELINES_D', t.pipelines_d), \
             contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = smd._restore_rerender_uploader(t.base)
        return rc, buf.getvalue()

    def _probe(self, t):
        return ("import os\n"
                f"print('marker', os.path.lexists({str(t.marker)!r}))\n"
                f"print('toml', os.path.lexists({str(t.sigmond_toml)!r}))\n")

    def test_the_child_renders_after_the_move(self):
        t = _HsTree(self, part2=False)
        rc, out = self._rerender(t, self._probe(t))
        self.assertEqual(rc, 0, out)
        self.assertIn('| marker False', out)
        self.assertIn('| toml False', out)
        self.assertEqual(len(t.asides()), 1)

    def test_a_part2_child_finds_both(self):
        t = _HsTree(self, part2=True)
        rc, out = self._rerender(t, self._probe(t))
        self.assertEqual(rc, 0, out)
        self.assertIn('| marker True', out)
        self.assertIn('| toml True', out)

    def test_a_failed_move_still_renders_and_exits_1(self):
        t = _HsTree(self, part2=False)
        real = t.tmp / 'real-state'
        t.state.rename(real)
        t.state.symlink_to(real)
        rc, out = self._rerender(t, self._probe(t))
        self.assertEqual(rc, 1, out)
        self.assertIn('| marker True', out)
        self.assertIn('re-rendered by the restored sigmond', out)


@needs_real
class CarryOverRestoreCarryOverTests(unittest.TestCase):
    """CARRY-OVER GATE: carry-over, restore below part 2, carry-over again
    (V5 then V8).  The real sink_carryover and the branch hs-uploader, with
    a fake systemctl; the restore step through its function."""

    def test_the_second_carry_over_writes_the_marker_again_and_keeps_files(self):
        st = Station(self, 'b4')
        _, res, _ = st.run()
        self.assertTrue(res.ok, res.lines())
        self.assertTrue(st.roots.marker.exists())
        first = st.snapshot()
        clients = [n for n in first if n != 'site.toml']
        self.assertTrue(clients, first)
        pipelines_d = st.tmp / 'etc' / 'hs-uploader' / 'pipelines.d'
        pipelines_d.mkdir(parents=True, exist_ok=True)
        (pipelines_d / 'sigmond.toml').write_text('# gate form\n')
        base = st.tmp / 'base'
        (base / 'sigmond' / 'bin').mkdir(parents=True)
        (base / 'sigmond' / 'bin' / 'smd').write_text('# v3.77\n')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = smd._restore_below_part2(base, marker=st.roots.marker,
                                          pipelines_d=pipelines_d)
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertFalse(os.path.lexists(st.roots.marker))
        self.assertFalse(os.path.lexists(pipelines_d / 'sigmond.toml'))
        self.assertEqual(st.snapshot(), first)
        # The restored sigmond keeps [uploads] current meanwhile (V8).
        coord = st.tmp / 'etc/sigmond/coordination.toml'
        text = coord.read_text()
        self.assertIn('enabled = true\nmode = "upload"', text)
        coord.write_text(text.replace('enabled = true\nmode = "upload"',
                                      'enabled = false\nmode = "hold"'))
        p, res2, r2 = st.run()
        self.assertTrue(res2.ok, res2.lines())
        self.assertTrue(st.roots.marker.is_file())
        self.assertEqual(r2.sets(), ['site'])
        second = st.snapshot()
        self.assertEqual({n: second[n] for n in clients},
                         {n: first[n] for n in clients})
        self.assertEqual(sorted(second), sorted(first))
        site = st.switch('site')
        self.assertEqual(site['state'], 'fill')
        self.assertEqual([h['to'] for h in site['history']],
                         ['upload', 'fill'])
        self.assertEqual(site['history'][1]['reason'],
                         'carried over from [uploads] mode = hold')
        for name in clients:
            self.assertIn(b'history', first[name], name)
        aside = [q.name for q in st.roots.marker.parent.iterdir()
                 if q.name.startswith(MARKER + '.rolled-back-')]
        self.assertEqual(len(aside), 1, aside)


# ── the restore run and the next manifest step, captured (Task 24) ───
# sigmond-appliance's update rig reads, at G-bis, what a restore to a sigmond
# below part 2 prints and leaves, and at G-ter what the next manifest step
# prints and leaves (global constraint 4: its parsers read real output).
# restore_rerun_capture runs the whole chain here once: the V1 manifest step
# (Task 19's scenario), a verified `smd admin manifest restore --apply` to a
# sigmond below part 2 (the real cmd_manifest_restore and the real
# _restore_below_part2; git, the restored child and the units faked as
# ManifestRestoreApplyTests fakes them), then the next manifest step (V8).
# tests/fixtures/capture_restore_part2.py writes what it returns to
# tests/fixtures/restore_part2/.

RESTORE_CAPTURE = Path(__file__).resolve().parent / 'fixtures' / 'restore_part2'
_CAPTURE_UTC = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
_CAPTURE_ASIDE = re.compile(r'(\.rolled-back-)\d{8}T\d{6}Z')


def restore_rerun_capture(case) -> dict:
    """{relative file name: text} for tests/fixtures/restore_part2/.

    restore.txt          the restore's whole output, in the order printed
    rerun.txt            the next manifest step's whole output
    coordination.toml    the station's [uploads], as the steps read it
    sinks-v1/<n>         every switch file after the first carry-over
    sinks-restored/<n>   ... after the restore
    sinks-rerun/<n>      ... after the next manifest step
    marker-v1.txt, marker-rerun.txt   the marker each carry-over wrote

    The scratch root reads @ROOT@, each wall-clock time 2026-10-23T12:00:00Z
    and the rolled-back stamp 20261023T120000Z, so a re-run leaves `git
    diff` empty."""
    from test_uploader_manifest_part2 import Part2Station, normalise
    st = Part2Station(case)

    def norm(text):
        text = normalise(text, st)
        text = _CAPTURE_UTC.sub('2026-10-23T12:00:00Z', text)
        return _CAPTURE_ASIDE.sub(r'\g<1>20261023T120000Z', text)

    def sinks(tag):
        return {f'{tag}/{p.name}': norm(p.read_text())
                for p in sorted(st.roots.sinks.iterdir())
                if not p.name.startswith('.')}

    out = {}
    first = st.step()
    case.assertEqual(first.rc, 0, first.both)
    out.update(sinks('sinks-v1'))
    out['marker-v1.txt'] = norm(st.roots.marker.read_text())

    fill = _filler_live(MIN_COMPONENT_ROWS - 1)
    base = st.tmp / 'opt-git-sigmond'
    for name in ('sigmond', *fill):
        d = base / name
        (d / '.git').mkdir(parents=True)
        (d / 'scripts').mkdir()
        (d / 'scripts' / 'install.sh').write_text('#!/bin/bash\n')
    # The restored sigmond: its own smd, and no sink_carryover.py.
    (base / 'sigmond' / 'bin').mkdir()
    (base / 'sigmond' / 'bin' / 'smd').write_text('#!/usr/bin/env python3\n')
    manifest = st.tmp / 'blessed.manifest.txt'
    manifest.write_text('components (live):\n    sigmond          aaaaaaa\n'
                        + _filler_rows(MIN_COMPONENT_ROWS - 1))
    sysd = st.tmp / 'etc-systemd'
    sysd.mkdir()
    args = types.SimpleNamespace(path=str(manifest), apply=True, no_fetch=True,
                                 base=str(base))
    versions = mock.Mock(side_effect=[{'sigmond': 'ccccccc', **fill},
                                      {'sigmond': 'aaaaaaa', **fill}])
    both = io.StringIO()
    with mock.patch.object(smd, '_need_root', return_value=False), \
         mock.patch('sigmond.provenance.component_versions',
                    side_effect=versions), \
         mock.patch('subprocess.run', side_effect=_FakeGit()), \
         mock.patch.object(smd, '_RESTORE_SYSTEMD_DIR', sysd), \
         mock.patch.object(smd, '_RESTORE_MARKER_PATH', st.roots.marker), \
         mock.patch.object(smd, '_RESTORE_PIPELINES_D', st.pipelines_d), \
         contextlib.redirect_stdout(both), contextlib.redirect_stderr(both):
        rc = smd.cmd_manifest_restore(args)
    case.assertEqual(rc, 0, both.getvalue())
    out['restore.txt'] = norm(both.getvalue())
    out.update(sinks('sinks-restored'))

    second = st.step()
    case.assertEqual(second.rc, 0, second.both)
    out['rerun.txt'] = norm(second.both)
    out.update(sinks('sinks-rerun'))
    out['marker-rerun.txt'] = norm(st.roots.marker.read_text())
    out['coordination.toml'] = norm(
        (st.tmp / 'etc' / 'sigmond' / 'coordination.toml').read_text())
    return out


@needs_real
class RestoreCaptureTests(unittest.TestCase):
    """The files Task 24's static test reads stay what this chain prints
    and leaves.  Regenerate with tests/fixtures/capture_restore_part2.py."""

    def test_the_captured_files_match(self):
        got = restore_rerun_capture(self)
        have = {str(p.relative_to(RESTORE_CAPTURE)): p.read_text()
                for p in sorted(RESTORE_CAPTURE.rglob('*')) if p.is_file()}
        self.assertEqual(sorted(got), sorted(have))
        for name in got:
            self.assertEqual(got[name], have[name], name)

    def test_the_chain_is_the_one_task_24_asserts(self):
        got = restore_rerun_capture(self)
        restore = got['restore.txt']
        self.assertIn('restored to manifest', restore)
        self.assertIn('sigmond: checked out', restore)
        self.assertIn('moved the carry-over marker aside to @ROOT@/state/'
                      'carry-over-v1.done.rolled-back-20261023T120000Z;',
                      restore)
        self.assertIn('removed @ROOT@/etc/hs-uploader/pipelines.d/sigmond.toml',
                      restore)
        self.assertIn('re-rendered by the restored sigmond', restore)
        rerun = got['rerun.txt']
        self.assertIn('uploader: carry-over: marker written\n', rerun)
        self.assertLess(rerun.index('carry-over: marker written'),
                        rerun.index('uploader: wrote @ROOT@/etc/hs-uploader/'
                                    'pipelines.d/sigmond.toml'))
        clients = [n.split('/', 1)[1] for n in got
                   if n.startswith('sinks-v1/') and n != 'sinks-v1/site.toml']
        self.assertTrue(clients)
        for n in clients:
            self.assertEqual(got[f'sinks-rerun/{n}'], got[f'sinks-v1/{n}'], n)
            self.assertEqual(got[f'sinks-restored/{n}'], got[f'sinks-v1/{n}'], n)

"""install, update --apply and align --apply refresh the CLI reference.

Each operation's internals are patched to succeed (or fail); `cli_reference.build`
is patched, so nothing here walks a program, writes /var/lib/sigmond or elevates.
The refresh runs once after success, never in a dry run or after a failure, and
never changes the operation's exit code.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'lib'))

from sigmond import align_apply, cli_reference  # noqa: E402
from sigmond.catalog import CatalogEntry  # noqa: E402
from tests import test_align_cli as _tac  # noqa: E402


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_cliref_wiring", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_cliref_wiring", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

_OK = [{'program': 'a', 'ok': True, 'error': None},
       {'program': 'b', 'ok': False, 'error': 'boom'},
       {'program': 'c', 'ok': True, 'error': None}]


def _build(raises=False):
    if raises:
        return mock.patch.object(cli_reference, 'build', side_effect=OSError('disk full'))
    return mock.patch.object(cli_reference, 'build', return_value=list(_OK))


class HelperTests(unittest.TestCase):
    def test_prints_one_line_with_counts_and_passes_the_smd_parser(self):
        out = io.StringIO()
        with _build() as b, contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            smd._refresh_cli_reference()
        b.assert_called_once()
        self.assertIs(b.call_args.kwargs.get('smd_parser'), smd._build_parser)
        self.assertIn('cli reference: 3 programs (1 failed)', out.getvalue())

    def test_an_exception_becomes_one_warning(self):
        out = io.StringIO()
        with _build(raises=True), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            smd._refresh_cli_reference()
        lines = [l for l in out.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn('disk full', lines[0])


# --- install ---------------------------------------------------------------

def _install(*, dry_run=False, install_ok=True, raises=False):
    entry = CatalogEntry(name='fake', kind='client', description='t',
                         repo='https://github.com/test/fake',
                         install_script='/opt/git/sigmond/fake/install.sh')
    targets = smd._InstallTargets()
    targets.catalog_queue.append(entry)
    args = argparse.Namespace(client=None, components='fake', profile=None,
                              client_dir=None, dry_run=dry_run, yes=True, force=False,
                              no_enable=False, topology=None, no_pull=True)
    out = io.StringIO()
    with contextlib.ExitStack() as st:
        for name, kw in (
                ('_need_root', dict(return_value=False)),
                ('_netdiag_auto_refresh', {}),
                ('_load_topology', dict(return_value={"smd_bin": Path("/nonexistent/smd")})),
                ('load_catalog', dict(return_value=[entry])),
                ('_classify_install_targets', dict(return_value=targets)),
                ('_ensure_sigmond_dirs', {}), ('_install_smd_itself', {}),
                ('_install_timing_watchdog', {}), ('_install_radiod_watchdog', {}),
                ('_install_loopback_multicast_route', {}),
                ('_host_has_local_radiod', dict(return_value=False)),
                ('_ensure_sigmond_venv', dict(return_value=True)),
                ('_post_install_fixups', {}),
                ('_do_install_client', dict(return_value=install_ok))):
            st.enter_context(mock.patch.object(smd, name, **kw))
        b = st.enter_context(_build(raises))
        st.enter_context(contextlib.redirect_stdout(out))
        st.enter_context(contextlib.redirect_stderr(out))
        rc = smd._install_full_suite(args)
    return rc, out.getvalue(), b


class InstallTests(unittest.TestCase):
    def test_success_refreshes_once(self):
        rc, out, b = _install()
        self.assertEqual(rc, 0)
        b.assert_called_once()
        self.assertIn('cli reference: 3 programs (1 failed)', out)

    def test_exception_keeps_exit_code_and_warns_once(self):
        rc, out, b = _install(raises=True)
        self.assertEqual(rc, 0)
        self.assertEqual(out.count('cli reference'), 1)

    def test_dry_run_never_refreshes(self):
        rc, out, b = _install(dry_run=True)
        self.assertEqual(rc, 0)
        b.assert_not_called()

    def test_failed_install_never_refreshes(self):
        rc, out, b = _install(install_ok=False)
        self.assertEqual(rc, 1)
        b.assert_not_called()


# --- update ----------------------------------------------------------------

def _update(*, apply, raises=False):
    from sigmond.update import Action
    args = argparse.Namespace(base='/nonexistent/base', apply=apply,
                              no_fetch=True, unpin=False)
    out = io.StringIO()
    with contextlib.ExitStack() as st:
        st.enter_context(mock.patch('sigmond.doctor.component_checkouts', return_value=[]))
        st.enter_context(mock.patch.object(smd, '_venv_sibling_skew', return_value=([], [])))
        st.enter_context(mock.patch.object(smd, '_refresh_upstreams'))
        st.enter_context(mock.patch.object(smd, '_say_sink_notice'))
        st.enter_context(mock.patch('sigmond.wisdom.plans_from_fft_log', return_value=[]))
        st.enter_context(mock.patch('sigmond.update.plan_update',
                                    return_value=[Action('restart', 'radiod')]))
        b = st.enter_context(_build(raises))
        st.enter_context(contextlib.redirect_stdout(out))
        st.enter_context(contextlib.redirect_stderr(out))
        rc = smd.cmd_update(args)
    return rc, out.getvalue(), b


class UpdateTests(unittest.TestCase):
    def test_apply_refreshes_once(self):
        rc, out, b = _update(apply=True)
        self.assertEqual(rc, 0)
        b.assert_called_once()

    def test_exception_keeps_exit_code_and_warns_once(self):
        rc, out, b = _update(apply=True, raises=True)
        self.assertEqual(rc, 0)
        self.assertEqual(out.count('cli reference'), 1)

    def test_dry_run_never_refreshes(self):
        rc, out, b = _update(apply=False)
        b.assert_not_called()


# --- align -----------------------------------------------------------------

class AlignTests(unittest.TestCase):
    _run = _tac.AlignMakeLiveApplyTests._run

    def _go(self, restart_outcome='restarted', raises=False, no_restart=False):
        with _build(raises) as b:
            rc, out, m = self._run(
                restart_steps=[align_apply.Step('hf-timestd', restart_outcome)],
                restarted_units=['hf-a.service'], no_restart=no_restart)
        return rc, out, b

    def test_success_refreshes_once_before_the_summary(self):
        rc, out, b = self._go()
        self.assertEqual(rc, 0)
        b.assert_called_once()
        self.assertIn('cli reference: 3 programs', out)
        self.assertLess(out.index('cli reference'), out.index('summary:'))

    def test_exception_keeps_exit_code_and_warns_once(self):
        rc, out, b = self._go(raises=True)
        self.assertEqual(rc, 0)
        self.assertEqual(out.count('cli reference'), 1)

    def test_failed_align_never_refreshes(self):
        rc, out, b = self._go(restart_outcome='failed')
        self.assertEqual(rc, 1)
        b.assert_not_called()

    def test_dry_run_never_refreshes(self):
        with _build() as b, mock.patch('sigmond.align.fetch_release',
                                       side_effect=RuntimeError('stop')):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(Exception):
                    smd.cmd_align(argparse.Namespace(
                        apply=False, base='/nonexistent', release=None))
        b.assert_not_called()


if __name__ == '__main__':
    unittest.main()

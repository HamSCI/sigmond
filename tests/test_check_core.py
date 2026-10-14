"""`smd check`: lines, verdict, exit code, rendering, the exception guard and
the smd registration (tasks/spec-smd-check-v372.md §2, §3, §6, §8)."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from sigmond.commands import check
from sigmond.commands.check import ABSENT, FAIL, OK, WAIT, Line

from tests.check_fakes import make_env

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_check", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_check", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _run(args, lines):
    checks = [(ln.label, (lambda env, ln=ln: ln)) for ln in lines]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = check.cmd_check(args, env=make_env(), checks=checks)
    return rc, out.getvalue()


class VerdictTests(unittest.TestCase):
    def test_worst_mark_wins(self):
        self.assertEqual(check.verdict([Line(OK, 'a', ''), Line(ABSENT, 'b', '')]), 'PASS')
        self.assertEqual(check.verdict([Line(OK, 'a', ''), Line(WAIT, 'b', '')]), 'WAIT')
        self.assertEqual(check.verdict([Line(WAIT, 'a', ''), Line(FAIL, 'b', '')]), 'FAIL')

    def test_absent_lines_count_for_nothing(self):
        self.assertEqual(check.verdict([Line(ABSENT, 'a', '')]), 'PASS')
        self.assertEqual(check.summary([Line(ABSENT, 'a', '')]), 'PASS')

    def test_summary_counts_failing_and_waiting(self):
        lines = [Line(FAIL, 'a', ''), Line(WAIT, 'b', ''), Line(WAIT, 'c', ''), Line(OK, 'd', '')]
        self.assertEqual(check.summary(lines), 'FAIL  (1 failing, 2 waiting)')
        self.assertEqual(check.summary([Line(WAIT, 'b', '')]), 'WAIT  (1 waiting)')

    def test_exit_codes_follow_the_verdict(self):
        self.assertEqual(check.EXIT_CODES, {'PASS': 0, 'WAIT': 2, 'FAIL': 1})
        self.assertEqual(_run(types.SimpleNamespace(), [Line(OK, 'a', 'x')])[0], 0)
        self.assertEqual(_run(types.SimpleNamespace(), [Line(WAIT, 'a', 'x')])[0], 2)
        self.assertEqual(_run(types.SimpleNamespace(), [Line(FAIL, 'a', 'x')])[0], 1)


class RenderTests(unittest.TestCase):
    def test_columns_line_up_as_michael_drew_them(self):
        self.assertEqual(check.render_line(Line(OK, 'PSK', '214 FT8 and 31 FT4 spots')),
                         ' ok    PSK            214 FT8 and 31 FT4 spots')
        self.assertEqual(check.render_line(Line(ABSENT, 'meteor scatter', 'not on this station')),
                         ' --    meteor scatter not on this station')
        self.assertEqual(check.render_line(Line(OK, 'magnetometer page', 'IPv4 http://x:8082')),
                         ' ok    magnetometer page  IPv4 http://x:8082')

    def test_a_fix_moves_to_its_own_line_under_the_text(self):
        line = Line(FAIL, 'magnetometer', 'frozen: 0.00 C, no change in 60 s.  '
                                          'Fix: sudo systemctl restart mag-recorder')
        self.assertEqual(check.render_line(line).split('\n'), [
            ' FAIL  magnetometer   frozen: 0.00 C, no change in 60 s.',
            '                      Fix: sudo systemctl restart mag-recorder'])

    def test_text_output_ends_with_the_verdict(self):
        rc, out = _run(types.SimpleNamespace(json=False, no_verdict=False),
                       [Line(OK, 'PSK', 'fine'), Line(WAIT, 'GRAPE', 'soon')])
        self.assertEqual(out.splitlines(), [' ok    PSK            fine',
                                            ' wait  GRAPE          soon',
                                            'WAIT  (1 waiting)'])

    def test_no_verdict_drops_only_the_verdict_line(self):
        rc, out = _run(types.SimpleNamespace(json=False, no_verdict=True),
                       [Line(FAIL, 'PSK', 'broken')])
        self.assertEqual(out.splitlines(), [' FAIL  PSK            broken'])
        self.assertEqual(rc, 1)

    def test_json_carries_lines_verdict_and_exit(self):
        rc, out = _run(types.SimpleNamespace(json=True), [Line(WAIT, 'GRAPE', 'soon')])
        doc = json.loads(out)
        self.assertEqual(doc, {'lines': [{'mark': 'wait', 'label': 'GRAPE', 'text': 'soon'}],
                               'verdict': 'WAIT', 'exit': 2})


class AsciiTests(unittest.TestCase):
    """The host's console font (Lat15) shows nothing past ASCII (Michael,
    2026-10-14), so nothing smd check prints may leave it."""

    def test_every_kind_of_line_and_the_verdict_print_as_ascii(self):
        lines = [Line(OK, 'PSK', '214 FT8 and 31 FT4 spots in the last 15 min'),
                 Line(WAIT, 'uploads', 'off, new station: check the lines above, then run: smd sink upload'),
                 Line(FAIL, 'magnetometer', 'frozen: 0.00 C, no change in 60 s.  '
                                            'Fix: sudo systemctl restart mag-recorder'),
                 Line(ABSENT, 'meteor scatter', 'not on this station'),
                 Line(OK, 'magnetometer page', 'http://127.0.0.1:8082 answers'),
                 Line(FAIL, 'GRAPE', 'the check itself failed: OSError: caf\u00e9 \u2014 \u00b0')]
        rc, out = _run(types.SimpleNamespace(json=False, no_verdict=False), lines)
        bad = sorted({c for c in out if ord(c) >= 0x80})
        self.assertEqual(bad, [], out)
        self.assertIn('caf? ? ?', out)


class GuardTests(unittest.TestCase):
    def test_an_exception_becomes_a_failing_line_with_its_text(self):
        def boom(env):
            raise RuntimeError('disk on fire')
        lines = check.run_checks(make_env(), [('PSK', boom), ('WSPR', lambda env: Line(OK, 'WSPR', 'ok'))])
        self.assertEqual(lines[0], Line(FAIL, 'PSK', 'the check itself failed: RuntimeError: disk on fire'))
        self.assertEqual(lines[1].mark, OK)

    def test_unreadable_names_the_path_and_the_user(self):
        def refused(env):
            raise check.Unreadable('/var/log/psk-recorder')
        line = check.run_checks(make_env(), [('PSK', refused)])[0]
        self.assertEqual(line.mark, FAIL)
        self.assertRegex(line.text, r'^cannot read /var/log/psk-recorder as \S+$')

    def test_a_check_returning_none_prints_no_line(self):
        self.assertEqual(check.run_checks(make_env(), [('x', lambda env: None)]), [])


class SmdRegistrationTests(unittest.TestCase):
    """`smd check` is registered, read-only, and never reaches the timing judge."""

    @classmethod
    def setUpClass(cls):
        cls.smd = _load_smd()

    def _main(self, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['smd', *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = self.smd.main()
        return rc, out.getvalue()

    def test_check_never_asks_for_root(self):
        with mock.patch.object(self.smd, '_need_root', side_effect=AssertionError('elevated')), \
                mock.patch.object(check, 'default_env', return_value=make_env()), \
                mock.patch.object(check, 'CHECKS', [('PSK', lambda env: Line(OK, 'PSK', 'fine'))]):
            rc, out = self._main('check', '--no-verdict')
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), [' ok    PSK            fine'])

    def test_check_never_calls_the_timing_judge(self):
        poisoned = types.ModuleType('sigmond.timing_judge')

        def _raise(name):
            raise AssertionError(f'smd check touched the timing judge ({name})')
        poisoned.__getattr__ = _raise
        with mock.patch.dict(sys.modules, {'sigmond.timing_judge': poisoned}), \
                mock.patch.object(check, 'default_env', return_value=make_env()):
            rc, out = self._main('check', '--json')
        doc = json.loads(out)
        self.assertIn(rc, (0, 1, 2))
        self.assertEqual(doc['exit'], rc)
        self.assertFalse(any('timing judge' in ln['text'] for ln in doc['lines']), doc)


if __name__ == '__main__':
    unittest.main()

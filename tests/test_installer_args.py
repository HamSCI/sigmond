"""sigmond.installer_args against real installers (v3.73 row 4, fix round 4).

The rule: pass --yes to every installer unless its own argument parser
would reject it (an argument case with an exiting catch-all branch and no
--yes branch).  The fixtures in tests/fixtures/installers are copies of
real scripts; their README names each origin.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sigmond.installer_args import pass_yes, rejects_yes

FIX = Path(__file__).resolve().parent / 'fixtures' / 'installers'


def _text(name):
    return (FIX / name).read_text()


class RealInstallerTests(unittest.TestCase):

    def test_hf_timestd_unquoted_case_accepts_yes(self):
        # `case $1 in` with `--yes|-y)`: align installing a missing
        # hf-timestd must pass --yes, or setup-station.sh meets closed stdin.
        t = _text('hf-timestd-install.sh')
        self.assertFalse(rejects_yes(t))
        self.assertTrue(pass_yes(t))

    def test_hf_tec_613408c_rejects_yes(self):
        t = _text('hf-tec-613408c-install.sh')
        self.assertTrue(rejects_yes(t))
        self.assertFalse(pass_yes(t))

    def test_hf_tec_current_accepts_yes(self):
        self.assertTrue(pass_yes(_text('hf-tec-a3a0f51-install.sh')))


class ParserFormsTests(unittest.TestCase):

    REJECT = '    --x) X=1 ;;\n    *) echo "unknown: {v}" >&2; exit 2 ;;\n'

    def test_every_loop_form_is_read(self):
        for head in ('case "$arg" in', 'case $arg in', 'case "$1" in',
                     'case $1 in', 'case "${1}" in', 'case ${1} in',
                     'case "$opt" in'):
            t = f'for arg in "$@"; do\n  {head}\n{self.REJECT}  esac\ndone\n'
            self.assertTrue(rejects_yes(t), head)
            self.assertFalse(pass_yes(t), head)

    def test_an_installer_with_no_parser_gets_yes(self):
        self.assertTrue(pass_yes('#!/bin/sh\necho hi\n'))

    def test_a_parser_that_ignores_unknown_args_gets_yes(self):
        self.assertTrue(pass_yes('for a in "$@"; do\n case "$a" in\n  --pull) P=1 ;;\n esac\ndone\n'))


class AlignRunInstallWithRealScriptsTests(unittest.TestCase):

    def _argv(self, fixture):
        import tests.test_align_cli as tac   # loads bin/smd once
        smd = tac.smd
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / 'scripts').mkdir()
            (repo / 'scripts' / 'install.sh').write_text(_text(fixture))
            with mock.patch('subprocess.run',
                            return_value=subprocess.CompletedProcess([], 0, '', '')) as m:
                smd._align_run_install(repo)
        return m.call_args.args[0]

    def test_hf_timestd_gets_yes(self):
        self.assertIn('--yes', self._argv('hf-timestd-install.sh'))

    def test_hf_tec_613408c_gets_no_yes(self):
        self.assertNotIn('--yes', self._argv('hf-tec-613408c-install.sh'))


if __name__ == '__main__':
    unittest.main()

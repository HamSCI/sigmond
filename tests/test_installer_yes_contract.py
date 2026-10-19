"""Every component installer tolerates --yes (v3.73 row 4, fix round 2).

smd align, `smd admin manifest restore --apply` and bring-up run each
component's install.sh as `install.sh --yes` with stdin closed
(bin/smd _align_run_install).  An installer that rejects an argument it
does not know fails that run.  hf-tec's did, with exit 2, until hf-tec
e54fb17.  The contract also sits in etc/catalog.toml's header.

The check reads the scripts; it never runs them.  An installer breaks the
contract when its argument `case` has a catch-all `*)` branch that exits
or dies and no `--yes` branch.
"""
import re
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPOS = REPO.parent

_ARG_CASE = re.compile(r'case\s+"\$(?:arg|1|\{1\}|opt)"\s+in(.*?)\besac\b', re.S)
_CATCH_ALL_EXITS = re.compile(r'^\s*\*\)[^\n]*\b(exit|die)\b', re.M)


def rejects_yes(text: str) -> bool:
    for m in _ARG_CASE.finditer(text):
        body = m.group(1)
        if _CATCH_ALL_EXITS.search(body) and '--yes' not in body:
            return True
    return False


def _installers():
    """(component, path) for each catalog install_script whose checkout
    sits beside this one, plus sigmond's own."""
    cat = tomllib.loads((REPO / 'etc' / 'catalog.toml').read_text())
    out = [('sigmond', REPO / 'install.sh')]
    for name, entry in (cat.get('client') or {}).items():
        script = entry.get('install_script') or ''
        prefix = f'/opt/git/sigmond/{name}/'
        if not script.startswith(prefix):
            continue
        path = REPOS / name / script[len(prefix):]
        if path.is_file():
            out.append((name, path))
    return out


class InstallerYesContractTests(unittest.TestCase):

    def test_the_old_hf_tec_parser_is_caught(self):
        old = ('for arg in "$@"; do\n    case "$arg" in\n'
               '        --build-only) BUILD_ONLY=1 ;;\n'
               '        *) echo "unknown arg: $arg" >&2; exit 2 ;;\n'
               '    esac\ndone\n')
        self.assertTrue(rejects_yes(old))

    def test_a_parser_with_yes_passes(self):
        ok = ('while [[ $# -gt 0 ]]; do\n  case "$1" in\n'
              '    --yes|-y) AUTO_YES=true; shift ;;\n'
              '    *) log_error "Unknown option: $1"; exit 1 ;;\n  esac\ndone\n')
        self.assertFalse(rejects_yes(ok))

    def test_a_parser_that_ignores_unknown_args_passes(self):
        ok = 'for arg in "$@"; do\n  case "$arg" in\n    --pull) P=1 ;;\n  esac\ndone\n'
        self.assertFalse(rejects_yes(ok))

    def test_every_installer_beside_this_checkout_tolerates_yes(self):
        found = _installers()
        self.assertGreaterEqual(len(found), 1)
        bad = [f'{n} ({p})' for n, p in found if rejects_yes(p.read_text())]
        self.assertEqual(bad, [], 'these installers exit on --yes')


if __name__ == '__main__':
    unittest.main()

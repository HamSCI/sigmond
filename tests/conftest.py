"""Shared test path bootstrap.

Tests import `sigmond` without requiring the package to be installed.
This mirrors what bin/smd does at runtime.
"""

import os
import sys
from pathlib import Path

# bin/smd re-execs into <repo>/venv/bin/python at module scope (install.sh's
# layout, distinct from this checkout's dev .venv/).  Eighteen test files load
# bin/smd with exec(); without this guard the FIRST of them replaces the
# running pytest process via os.execv(), which does not look like a failure --
# it looks like the suite ending early.  Each of those files sets this itself;
# setting it here once makes it true for any file added later, and setdefault
# leaves a caller's own override alone.
os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")

_LIB = Path(__file__).resolve().parent.parent / 'lib'
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


def _tui_skip_banner():
    """Print a loud stderr warning when textual is absent.

    Each tests/test_tui_*.py file skips its own classes via
    @unittest.skipUnless(_HAS_TEXTUAL, ...) — that mechanism stays as
    the enforcement point. This banner exists because that per-class
    skip is otherwise silent: `pip install -e '.[dev]'` (no `tui`
    extra) produces a suite that runs and reports green while quietly
    skipping every TUI test. That gap is exactly what let the TUI
    suite go unexercised in CI from June to August 2026 with nobody
    noticing. Run scripts/dev-setup.sh (installs `.[tui,dev]`) to get
    textual and stop the skips.
    """
    try:
        import textual  # noqa: F401
    except ImportError:
        pass
    else:
        return
    tui_test_count = len(list((Path(__file__).resolve().parent).glob('test_tui_*.py')))
    print(
        '\n' + '=' * 70 + '\n'
        'WARNING: textual is not installed in this interpreter.\n'
        f'  {tui_test_count} tests/test_tui_*.py file(s) will SKIP ENTIRELY,\n'
        '  not fail -- the run will still look green.\n'
        "  Run scripts/dev-setup.sh to install the '.[tui,dev]' extras.\n"
        + '=' * 70 + '\n',
        file=sys.stderr,
    )


_tui_skip_banner()

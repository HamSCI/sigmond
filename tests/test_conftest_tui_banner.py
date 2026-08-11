"""Tests for the loud textual-missing banner in tests/conftest.py.

The per-class ``@unittest.skipUnless(_HAS_TEXTUAL, ...)`` guards in each
tests/test_tui_*.py file are silent by design (that's what skips look
like). That silence let the whole TUI suite go unexercised in CI from
June to August 2026 with a green-looking run, because
`pip install -e '.[dev]'` (no `tui` extra) never installs textual.
conftest.py now prints a loud stderr banner in that situation. These
tests fake the ImportError to exercise the banner without needing a
second interpreter that actually lacks textual.
"""

import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

_CONFTEST_PATH = Path(__file__).resolve().parent / 'conftest.py'

try:
    import textual  # noqa: F401
    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False


def _load_conftest_module():
    """Load tests/conftest.py as a standalone module.

    Uses importlib rather than a plain `import conftest` so this test
    doesn't depend on how pytest happens to have conftest.py on
    sys.path/in sys.modules already.
    """
    spec = importlib.util.spec_from_file_location(
        '_conftest_under_test_for_banner', _CONFTEST_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TuiSkipBannerTests(unittest.TestCase):
    def test_banner_fires_when_textual_import_fails(self):
        module = _load_conftest_module()
        # Force `import textual` to raise ImportError, scoped to this
        # call: setting the sys.modules entry to None makes the import
        # statement fail without touching whether textual is actually
        # installed in this interpreter.
        buf = io.StringIO()
        with mock.patch.dict(sys.modules, {'textual': None}), \
                mock.patch.object(sys, 'stderr', buf):
            module._tui_skip_banner()
        output = buf.getvalue()
        self.assertIn('WARNING', output)
        self.assertIn('textual', output.lower())
        self.assertIn('dev-setup.sh', output)

        # Shape, not just substrings: adjacent string literals
        # concatenate before `*` binds, so `'\n' '=' * 70` silently
        # becomes `'\n=' * 70` (seventy one-char lines) instead of one
        # 70-char rule. Assert the actual rendered lines to catch that
        # class of bug, which the substring checks above would miss.
        rule = '=' * 70
        lines = output.splitlines()
        rule_lines = [line for line in lines if line == rule]
        self.assertEqual(
            len(rule_lines), 2,
            f'expected exactly two {len(rule)}-char rule lines, got: {lines!r}',
        )
        for line in lines:
            self.assertNotEqual(
                line, '=', 'found a lone "=" line -- separator got split one char per line',
            )

    @unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
    def test_no_banner_when_textual_importable(self):
        module = _load_conftest_module()
        buf = io.StringIO()
        with mock.patch.object(sys, 'stderr', buf):
            module._tui_skip_banner()
        self.assertEqual(buf.getvalue(), '')


if __name__ == '__main__':
    unittest.main()

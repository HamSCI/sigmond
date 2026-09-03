"""A question nobody can answer must not decide the exit code.

`smd bringup dasi2` finished on AC0G-ND 2026-09-02 with every service
started — radiod, six metrology channels, wspr, psk, meteor-scatter — and
then ended with a traceback and exit 1:

    configure PSWS for hf-timestd now? [Y/n]
    OSError: [Errno 5] Input/output error

The block that asked declares its own contract in a comment three lines
above: "NON-BLOCKING: the station is already up; PSWS upload is optional and
completable anytime."  It guarded with `sys.stdin.isatty()` and caught
`EOFError`, which covers a pipe and a closed stdin — and misses the case that
actually happened.  A DETACHED tmux pane IS a tty: isatty() returns True, and
the read then fails EIO because no client is attached to type into it.

So the guard has to be about whether stdin can be READ, not whether it looks
like a terminal.
"""

import importlib.machinery
import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_prompt_under_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_prompt_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class _FakeTTY(io.StringIO):
    """Looks like a terminal.  Reading it raises whatever we were given."""

    def __init__(self, raises=None):
        super().__init__()
        self._raises = raises

    def isatty(self):
        return True

    def readline(self, *a, **kw):
        if self._raises is not None:
            raise self._raises
        return "\n"


class OptionalPromptTest(unittest.TestCase):

    def _with_stdin(self, stream, answer_source=None):
        real = sys.stdin
        sys.stdin = stream
        try:
            return smd._ask_optional_yes_no('ask? [Y/n] ')
        finally:
            sys.stdin = real

    def test_unreadable_tty_returns_none_rather_than_raising(self):
        # THE live case: detached tmux pane.  isatty() True, read raises EIO.
        stream = _FakeTTY(raises=OSError(5, 'Input/output error'))
        self.assertIsNone(self._with_stdin(stream),
                          'an unreadable tty must yield "no answer", not an '
                          'exception that aborts the caller')

    def test_closed_stdin_returns_none(self):
        stream = _FakeTTY(raises=EOFError())
        self.assertIsNone(self._with_stdin(stream))

    def test_interrupt_returns_none(self):
        stream = _FakeTTY(raises=KeyboardInterrupt())
        self.assertIsNone(self._with_stdin(stream))

    def test_non_tty_is_never_asked(self):
        stream = io.StringIO('y\n')       # isatty() False
        self.assertIsNone(self._with_stdin(stream))

    def test_bare_enter_takes_the_default(self):
        # Guards the guard: the fix must not turn a real operator's Enter into
        # "no answer" — the prompt reads [Y/n] and Enter has always meant yes.
        stream = _FakeTTY()               # readline returns "\n"
        self.assertIs(self._with_stdin(stream), True)

    def test_explicit_no_is_respected(self):
        class _No(_FakeTTY):
            def readline(self, *a, **kw):
                return "n\n"
        self.assertIs(self._with_stdin(_No()), False)

    def test_explicit_yes_is_respected(self):
        class _Yes(_FakeTTY):
            def readline(self, *a, **kw):
                return "yes\n"
        self.assertIs(self._with_stdin(_Yes()), True)


if __name__ == '__main__':
    unittest.main()

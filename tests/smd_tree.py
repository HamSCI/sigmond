"""The live `smd` verb tree, walked from bin/smd's argparse parser.

Shared by tests that check a declared verb list against what `smd`
really accepts: the TUI parity lint (test_tui_parity.py) and the
snapshot command list (test_snapshot.py).  Never runs a subcommand.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import sys
from pathlib import Path

_SMD_BIN = Path(__file__).resolve().parent.parent / 'bin' / 'smd'


class _Caught(Exception):
    """Carries the fully-built ArgumentParser out of bin/smd's main()."""

    def __init__(self, parser):
        self.parser = parser


def live_verb_parsers() -> dict:
    """Import bin/smd and walk its argparse tree to get every live
    command path, as space-separated strings without the leading
    "smd" (e.g. "admin verifier report"), mapped to that verb's own
    ArgumentParser (so a caller can check its flags too).

    Never calls a subcommand: argparse.ArgumentParser.parse_args is
    patched, module-wide, to raise _Caught(self) instead of parsing --
    main() is called (with sys.argv forced to just ['smd'] and stdout
    silenced) purely to reach the point where the parser is fully
    constructed and parse_args() would normally run.
    """
    orig_parse_args = argparse.ArgumentParser.parse_args

    def _patched(self, *args, **kwargs):
        raise _Caught(self)

    argparse.ArgumentParser.parse_args = _patched
    old_argv = sys.argv
    # bin/smd re-execs into <repo>/venv/bin/python (install.sh's layout) at
    # module scope once __file__ is resolvable -- which SourceFileLoader
    # always makes true.  Unset, this would replace the running pytest
    # process with os.execv() mid-suite on an installed host.
    os.environ.setdefault('SIGMOND_NO_VENV_REEXEC', '1')
    try:
        loader = importlib.machinery.SourceFileLoader('smd_argv_probe', str(_SMD_BIN))
        spec = importlib.util.spec_from_loader('smd_argv_probe', loader)
        module = importlib.util.module_from_spec(spec)
        sys.argv = ['smd']
        root = None
        with contextlib.redirect_stdout(io.StringIO()):
            loader.exec_module(module)
            try:
                module.main()
            except _Caught as caught:
                root = caught.parser
    finally:
        sys.argv = old_argv
        argparse.ArgumentParser.parse_args = orig_parse_args

    if root is None:
        raise RuntimeError(
            'bin/smd main() never reached parse_args() -- cannot derive '
            'the live verb tree; something in main() changed shape.'
        )

    paths: dict = {}

    def walk(parser, prefix):
        for action in parser._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            # Group choices by underlying subparser identity so an
            # aliased verb (e.g. `list` aliasing `component list`)
            # contributes one canonical path, keyed on first-seen name.
            order = []
            groups: dict = {}
            for name, subparser in action.choices.items():
                key = id(subparser)
                if key not in groups:
                    groups[key] = (subparser, [])
                    order.append(key)
                groups[key][1].append(name)
            for key in order:
                subparser, names = groups[key]
                full = prefix + [names[0]]
                paths[' '.join(full)] = subparser
                walk(subparser, full)

    walk(root, [])
    return paths




def live_verb_paths() -> set:
    """Every live command path (see live_verb_parsers), as a set."""
    return set(live_verb_parsers())

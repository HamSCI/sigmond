#!/usr/bin/env python3
"""Print a program's argparse command tree as JSON.

Standalone and stdlib only.  sigmond runs this file BY PATH inside each
client's own venv (``<venv>/bin/python -I cli_walk.py --script NAME``),
where sigmond is not installed, so nothing here imports sigmond.

The walker never runs a command.  It calls the program's entry point with
``argparse.ArgumentParser.parse_args`` and ``parse_known_args`` patched to
stop at the first call and hand back the parser.  A program that does work
before it parses, or never parses, ends in ``error`` -- the process still
exits 0 and prints valid JSON.

Usage:
    cli_walk.py --entry MODULE:FUNC --program NAME [--dist DIST]
    cli_walk.py --script NAME
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import sys
from typing import Any, Callable

SCHEMA = 1
NO_PARSER = "no argparse parser reached"


class _Captured(BaseException):
    """Carries the parser out of the program's main().

    BaseException, so a program's own ``except Exception`` cannot swallow it.
    """

    def __init__(self, parser: argparse.ArgumentParser):
        super().__init__("parser captured")
        self.parser = parser


def capture_parser(func: Callable[[], Any]) -> argparse.ArgumentParser:
    """Call ``func`` and return the first ArgumentParser it tries to parse with.

    ``parse_args`` and ``parse_known_args`` are patched for the call and
    restored in a ``finally``, as is ``sys.argv``.  Exceptions from ``func``
    (including SystemExit) propagate; a return without parsing raises
    RuntimeError.
    """
    cls = argparse.ArgumentParser
    orig_parse_args = cls.parse_args
    orig_parse_known = cls.parse_known_args

    def _stop(self, *args, **kwargs):
        raise _Captured(self)

    old_argv = sys.argv
    cls.parse_args = _stop
    cls.parse_known_args = _stop
    sys.argv = [_program_name(old_argv), "--help"]
    try:
        func()
    except _Captured as caught:
        return caught.parser
    finally:
        cls.parse_args = orig_parse_args
        cls.parse_known_args = orig_parse_known
        sys.argv = old_argv
    raise RuntimeError(NO_PARSER)


def _program_name(argv: list) -> str:
    return argv[0] if argv else "prog"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _help_text(parser: argparse.ArgumentParser, action: argparse.Action) -> str:
    """The action's help with %(default)s style fields filled in."""
    text = action.help
    if not text or text is argparse.SUPPRESS:
        return ""
    params = dict(vars(action), prog=parser.prog)
    for key in list(params):
        if params[key] is argparse.SUPPRESS:
            del params[key]
    if params.get("choices") is not None:
        params["choices"] = ", ".join(str(c) for c in params["choices"])
    try:
        return text % params
    except (KeyError, ValueError, TypeError):
        return text


def _metavar(action: argparse.Action) -> str | None:
    mv = action.metavar
    if mv is None:
        return None
    if isinstance(mv, tuple):
        return " ".join(str(m) for m in mv)
    return str(mv)


def _arg(parser: argparse.ArgumentParser, action: argparse.Action) -> dict:
    if not action.option_strings:
        kind = "positional"
    elif action.nargs == 0:
        kind = "flag"
    else:
        kind = "option"
    default = action.default
    name = action.dest
    if name is argparse.SUPPRESS or not name:
        name = _metavar(action) or ""
    return {
        "name": name,
        "flags": list(action.option_strings),
        "kind": kind,
        "help": _help_text(parser, action),
        "choices": (None if action.choices is None
                    else [str(c) for c in action.choices]),
        "default": (None if default is None or default is argparse.SUPPRESS
                    else str(default)),
        "required": bool(action.required),
        "nargs": None if action.nargs is None else str(action.nargs),
        "metavar": _metavar(action),
    }


def _visible(action: argparse.Action) -> bool:
    if action.help is argparse.SUPPRESS:
        return False
    if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
        return False
    return True


def walk_parser(parser: argparse.ArgumentParser, program: str) -> list[dict]:
    """Every command in the tree, root first, depth first, in parser order."""
    out: list[dict] = []
    _walk(parser, program, "", out)
    return out


def _walk(parser, path: str, help_text: str, out: list) -> None:
    out.append({
        "path": path,
        "help": help_text,
        "description": _text(parser.description),
        "arguments": [_arg(parser, a) for a in parser._actions if _visible(a)],
    })
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        helps = {ca.dest: _text(ca.help)
                 for ca in getattr(action, "_choices_actions", [])
                 if ca.help is not argparse.SUPPRESS}
        seen: set[int] = set()
        for name, sub in action.choices.items():
            if id(sub) in seen:  # an alias of one already walked
                continue
            seen.add(id(sub))
            _walk(sub, f"{path} {name}", helps.get(name, ""), out)


def _load_entry(entry: str) -> Callable[[], Any]:
    module_name, _, attr = entry.partition(":")
    if not module_name or not attr:
        raise ValueError(f"entry must be MODULE:FUNC, got {entry!r}")
    obj: Any = importlib.import_module(module_name)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _version(dist: str | None) -> str | None:
    if not dist:
        return None
    try:
        return importlib.metadata.version(dist)
    except Exception:
        return None


def _lookup_script(name: str) -> tuple[str, str | None]:
    """(entry, distribution name or None) for a console script."""
    for ep in importlib.metadata.entry_points(group="console_scripts"):
        if ep.name == name:
            dist = getattr(getattr(ep, "dist", None), "name", None)
            return ep.value, dist
    raise LookupError(f"no console script named {name!r}")


def build(entry: str | None, program: str, dist: str | None) -> dict:
    result: dict = {
        "schema": SCHEMA, "program": program, "version": _version(dist),
        "entry": entry, "commands": [], "error": None,
    }
    try:
        func = _load_entry(entry)
        parser = capture_parser(func)
        result["commands"] = walk_parser(parser, program)
    except SystemExit as exc:
        result["error"] = f"{NO_PARSER} (SystemExit: {exc.code})"
    except BaseException as exc:  # a walk must always end in JSON
        if isinstance(exc, RuntimeError) and str(exc) == NO_PARSER:
            result["error"] = NO_PARSER
        else:
            result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main(argv: list[str] | None = None) -> int:
    """The CLI path.  Never returns: ends in os._exit(0).

    The JSON goes to a saved copy of the real stdout.  For the whole walk,
    fd 1 points at stderr, so Python-level, C-level and child-process
    output from the program (import time included) cannot reach it.  The
    hard exit keeps a client's non-daemon thread from writing after the
    JSON or holding the process open.
    """
    json_fd = os.dup(1)
    sys.stdout.flush()
    os.dup2(2, 1)
    try:
        text = json.dumps(_run(argv), indent=2) + "\n"
    except SystemExit as exc:  # argparse usage error: report it, exit 2
        _hard_exit(exc.code if isinstance(exc.code, int) else 2)
    data = text.encode()
    while data:
        data = data[os.write(json_fd, data):]
    _hard_exit(0)


def _hard_exit(code: int):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)


def _run(argv: list[str] | None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--entry", help="MODULE:FUNC of the program's main")
    ap.add_argument("--program", help="name to show as the command root")
    ap.add_argument("--dist", help="distribution whose version to record")
    ap.add_argument("--script", help="console script to look up")
    ns = ap.parse_args(argv)

    entry, program, dist = ns.entry, ns.program, ns.dist
    error = None
    if ns.script:
        program = program or ns.script
        try:
            entry, found_dist = _lookup_script(ns.script)
            dist = dist or found_dist
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    elif not entry or not program:
        ap.error("give --script NAME, or --entry and --program")

    if error is not None:
        result = {"schema": SCHEMA, "program": program, "version": _version(dist),
                  "entry": None, "commands": [], "error": error}
    else:
        result = build(entry, program, dist)
    return result


if __name__ == "__main__":
    main()

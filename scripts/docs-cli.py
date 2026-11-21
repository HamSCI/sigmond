#!/usr/bin/env python3
"""Render the full `smd` command reference from the parser bin/smd builds.

    python3 scripts/docs-cli.py            # print the page

It walks the parser with the same code that builds station-web's Commands
page on a station (`smd admin cli-reference build`: lib/sigmond/cli_reference.py
and cli_walk.py).  scripts/docs-site.py calls it at every site build, so the
public site and a station both render from the code itself, and no committed
copy exists to drift.  It runs no command.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

HEADER = """# smd command reference

> **Audience:** operator, contributor
> **Status:** current (generated)
> **Verified against:** sigmond — code (rendered from `bin/smd`'s parser by `scripts/docs-cli.py` at each site build)
> **Canonical for:** every `smd` command and option

Every `smd` command, with its options, as `smd <command> --help` prints them.
The same walk builds the Commands page in station-web on each station
(`smd admin cli-reference build`), which adds the station's other programs
and [example output](smd.md).  For what to run day to day, start at
[COMMANDS.md](../COMMANDS.md); `smd` refuses `sudo` and elevates itself when a
command needs root.

"""


def _walk():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    sys.path.insert(0, str(REPO / "lib"))
    sys.argv = ["smd"]
    from sigmond import cli_reference, cli_walk
    return cli_walk.walk_parser(cli_reference._smd_parser(), "smd")


def _cell(text) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|")


def _arg_row(a: dict) -> str:
    if a.get("flags"):
        name = ", ".join(f"`{f}`" for f in a["flags"])
        if a.get("metavar") and a.get("kind") != "flag":
            name += f" `{a['metavar']}`"
    else:
        name = f"`{a.get('metavar') or a.get('name')}`"
    extra = []
    if a.get("choices"):
        extra.append("one of " + ", ".join(f"`{c}`" for c in a["choices"]))
    if a.get("default") not in (None, False, "", [], "==SUPPRESS=="):
        extra.append(f"default `{a['default']}`")
    if a.get("required") and a.get("nargs") not in ("*", "?"):
        extra.append("required")
    help_text = _cell(a.get("help"))
    if extra:
        help_text = (help_text + " (" + "; ".join(extra) + ")").strip()
    return f"| {name} | {help_text} |"


def render() -> str:
    rows = _walk()
    out = [HEADER]
    tops = [r for r in rows if len(r["path"].split()) == 2]
    out.append("Top-level commands: " + ", ".join(
        f"[`{r['path'].split()[1]}`](#{_anchor(r['path'])})" for r in tops) + ".\n")
    for r in rows:
        depth = len(r["path"].split())
        if depth == 1:
            continue
        level = "##" if depth == 2 else "###" if depth == 3 else "####"
        out.append(f"{level} {r['path']}\n")
        text = r.get("description") or r.get("help")
        if text:
            out.append(_cell(text) + "\n")
        args = [a for a in r.get("arguments", []) if a.get("name") != "help"]
        if args:
            out.append("| Argument | What it does |")
            out.append("|---|---|")
            out.extend(_arg_row(a) for a in args)
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def _anchor(path: str) -> str:
    return "-".join(path.lower().split())


def main(argv):
    sys.stdout.write(render())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

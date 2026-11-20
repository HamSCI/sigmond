"""The bare-metal install doc as the rig's test script.

docs/install-bare-metal.md holds the commands an operator types.  The rig
runs those same commands on a fresh machine, so the doc is the test: this
module pulls the commands out of the doc, rewrites them for a throwaway
identity, and judges the `smd check --json` that follows.

Stdlib only.  The rig calls it through main().
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from typing import Optional

from sigmond.commands.check import RADIOD_DORMANT

TEST_IDENTITY = {"--callsign": "N0CALL", "--grid": "EM38ww", "--reporter": "N0CALL/BM"}
RADIOD_CONSUMERS = ('PSK', 'WSPR', 'meteor scatter', 'GRAPE')

_CHECKOUT = re.compile(r'^git checkout (v\d+\.\d+(\.\d+)?)$')
_FENCE_OPEN = re.compile(r'^```bash\s*$')


class DocError(ValueError):
    """The doc no longer has the shape the test script needs."""


@dataclass(frozen=True)
class DocSteps:
    install: tuple
    bringup: str
    checkout_version: Optional[str]


def _section(md: str, prefix: str) -> list:
    """The lines under the heading that starts `prefix`, up to the next `## `."""
    out, inside = [], False
    for line in md.splitlines():
        if line.startswith('## '):
            if inside:
                break
            inside = line.startswith(prefix)
            continue
        if inside:
            out.append(line)
    if not inside:
        raise DocError(f'the doc has no section {prefix.strip()}')
    return out


def _bash_blocks(lines: list) -> list:
    blocks, cur = [], None
    for line in lines:
        if cur is None:
            if _FENCE_OPEN.match(line):
                cur = []
        elif line.startswith('```'):
            blocks.append(cur)
            cur = None
        else:
            cur.append(line)
    return blocks


def _join_continuations(block: list) -> list:
    out, acc = [], ''
    for raw in block:
        line = raw.strip()
        if line.endswith('\\'):
            acc += line[:-1].strip() + ' '
            continue
        line = acc + line
        acc = ''
        if line and not line.startswith('#'):
            out.append(line)
    if acc.strip():
        out.append(acc.strip())
    return out


def extract(md: str) -> DocSteps:
    install = []
    for block in _bash_blocks(_section(md, '## 2.')):
        install.extend(_join_continuations(block))
    blocks = _bash_blocks(_section(md, '## 3.'))
    if not blocks:
        raise DocError('section 3 has no bash block')
    cmds = _join_continuations(blocks[0])
    bringup = ' '.join(cmds)
    if not bringup.startswith('smd bringup dasi2'):
        raise DocError('the bring-up command does not start "smd bringup dasi2"')
    if '--release-list' not in bringup.split():
        raise DocError('the bring-up command lacks --release-list')
    found = [m.group(1) for m in map(_CHECKOUT.match, install) if m]
    if len(found) > 1:
        raise DocError('the doc checks out more than one release')
    return DocSteps(tuple(install), bringup, found[0] if found else None)


def commands_digest(steps: DocSteps) -> str:
    text = '\n'.join(steps.install + (steps.bringup,)) + '\n'
    return hashlib.sha256(text.encode()).hexdigest()


def _rewrite_bringup(line: str) -> str:
    toks, out, i = line.split(), [], 0
    while i < len(toks):
        t = toks[i]
        opt, eq, _val = t.partition('=')
        if opt.startswith('--psws-'):
            i += 1 if eq else 2
            continue
        if t in TEST_IDENTITY and i + 1 < len(toks):
            out += [t, TEST_IDENTITY[t]]
            i += 2
            continue
        if eq and opt in TEST_IDENTITY:
            out.append(f'{opt}={TEST_IDENTITY[opt]}')
            i += 1
            continue
        out.append(t)
        i += 1
    out.append('--non-interactive')
    return ' '.join(out)


def test_script(steps: DocSteps, *, sigmond_commit: str):
    """(install part, bringup part) as shell text."""
    if steps.checkout_version is None:
        raise DocError('the doc checks out no release')
    head = 'set -euo pipefail\n'
    install = [f'git checkout {sigmond_commit}' if _CHECKOUT.match(c) else c
               for c in steps.install]
    return (head + '\n'.join(install) + '\n',
            head + _rewrite_bringup(steps.bringup) + '\n')


def judge_check(doc: dict):
    """(passed, reasons) over `smd check --json`.  Michael's rule: every radiod
    consumer waits on the dormant radiod, and nothing fails."""
    lines = doc.get('lines', [])
    why = []
    for ln in lines:
        if ln.get('mark') == 'FAIL':
            why.append(f"{ln.get('label')}: FAIL: {ln.get('text')}")
    by_label = {ln.get('label'): ln for ln in lines}
    for label in RADIOD_CONSUMERS:
        ln = by_label.get(label)
        if ln is None:
            why.append(f'{label}: no such line in the check output')
        elif ln.get('mark') != 'wait' or RADIOD_DORMANT not in ln.get('text', ''):
            why.append(f"{label}: expected wait with '{RADIOD_DORMANT}', "
                       f"got {ln.get('mark')}: {ln.get('text')}")
    why = [w.encode('ascii', 'replace').decode().replace('\n', ' ') for w in why]
    return (not why, why)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='bare_metal_doc')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('script')
    s.add_argument('--doc', required=True)
    s.add_argument('--commit', required=True)
    s.add_argument('--part', choices=('install', 'bringup'), required=True)
    for name in ('digest', 'checkout-version'):
        sub.add_parser(name).add_argument('--doc', required=True)
    sub.add_parser('judge').add_argument('--check-json', required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'judge':
            with open(a.check_json) as f:
                ok, why = judge_check(json.load(f))
            for w in why:
                print(w)
            return 0 if ok else 1
        with open(a.doc) as f:
            steps = extract(f.read())
        if a.cmd == 'digest':
            print(commands_digest(steps))
        elif a.cmd == 'checkout-version':
            print(steps.checkout_version or '')
        else:
            inst, br = test_script(steps, sigmond_commit=a.commit)
            sys.stdout.write(inst if a.part == 'install' else br)
    except DocError as e:
        print(f'bare_metal_doc: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

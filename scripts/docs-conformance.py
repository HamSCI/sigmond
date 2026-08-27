#!/usr/bin/env python3
"""docs-conformance — verify a repo carries its contract §20 documentation surface.

Usage:  docs-conformance.py [REPO_ROOT ...]     (default: cwd)

Exit 0 when every bound rule passes; exit 1 and print
`path: [rule] reason` per violation otherwise.  Stdlib only.

Rules live in `lib/sigmond/docs_conformance.py`; this is only a CLI over them,
so `smd doctor --docs` and CI cannot disagree about what §20 means.

CI runs this from a sigmond checkout at `.sigmond-tools`, by path and with no
PYTHONPATH set, so the script bootstraps its own `lib/` onto sys.path.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sigmond.docs_conformance import check_repo  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="*", type=Path, default=[Path(".")],
                    help="repo checkout roots (default: cwd)")
    a = ap.parse_args(argv)

    findings = []
    for root in (a.roots or [Path(".")]):
        findings.extend(check_repo(root))

    for f in findings:
        print(f"{f.path}: [{f.rule}] {f.reason}")
    print(f"docs-conformance: {len(findings)} finding(s)", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

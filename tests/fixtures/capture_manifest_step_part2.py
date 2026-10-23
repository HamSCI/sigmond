#!/usr/bin/env python3
"""Regenerate tests/fixtures/manifest_step_part2.txt: the full output of
the manifest step's V1 run (both part 2: carry-over, marker, gate render,
pipelines.d/sigmond.toml, one daemon restart), stdout and stderr in the
order printed.

    .venv/bin/python tests/fixtures/capture_manifest_step_part2.py [OUT]

It runs the same scenario as test_uploader_manifest_part2.V1Tests, on a
scratch copy of tests/fixtures/carryover/legacy, with the branch
hs-uploader beside this checkout (../hs-uploader/.venv) answering every
hs-uploader call, and the daemon's and the recorders' systemctl faked.
The scratch root reads @ROOT@, so a re-run leaves `git diff` empty.
Task 24 feeds the file to the rig's static test (global constraint 4).
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))
sys.path.insert(0, str(HERE.parent))

from test_sink_carryover import REAL_PY  # noqa: E402
from test_uploader_manifest_part2 import FIXTURE, v1_transcript  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURE
    if not REAL_PY.exists():
        print(f"capture: no hs-uploader interpreter at {REAL_PY}",
              file=sys.stderr)
        return 1
    case = unittest.TestCase()
    try:
        text = v1_transcript(case)
    finally:
        case.doCleanups()
    out.write_text(text)
    print(f"capture: wrote {out} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Regenerate tests/fixtures/sink_fill_part2.txt: the full output of
`smd sink fill --reason rig` on a station the carry-over has run on (both
part 2, the marker standing), stdout and stderr in the order printed.

    .venv/bin/python tests/fixtures/capture_sink_fill_part2.py [OUT]

It runs the same scenario as test_sink_site_form.CaptureTests, on a scratch
copy of tests/fixtures/carryover/legacy (W1AW: psk-recorder direct,
wspr-recorder with its shim on), with the branch hs-uploader beside this
checkout (../hs-uploader/.venv) answering every hs-uploader call, and the
daemon's and the recorders' systemctl faked.  The scratch root reads
@ROOT@, so a re-run leaves `git diff` empty.  Task 23 feeds the file to the
rig's static test (global constraint 4, ruling X6).
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))
sys.path.insert(0, str(HERE.parent))

from test_sink_carryover import REAL_PY  # noqa: E402
from test_sink_site_form import FILL_FIXTURE, fill_transcript  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else FILL_FIXTURE
    if not REAL_PY.exists():
        print(f"capture: no hs-uploader interpreter at {REAL_PY}",
              file=sys.stderr)
        return 1
    case = unittest.TestCase()
    try:
        text = fill_transcript(case)
    finally:
        case.doCleanups()
    out.write_text(text)
    print(f"capture: wrote {out} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

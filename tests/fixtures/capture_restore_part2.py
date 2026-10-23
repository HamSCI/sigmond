#!/usr/bin/env python3
"""Regenerate tests/fixtures/restore_part2/: a restore to a sigmond below
part 2 and the manifest step after it (V5 then V8), as printed and as left
on disk.

    .venv/bin/python tests/fixtures/capture_restore_part2.py [OUTDIR]

It runs test_manifest_restore.restore_rerun_capture: the V1 manifest step
on a scratch copy of tests/fixtures/carryover/legacy, then a verified
`smd admin manifest restore --apply` whose restored sigmond carries no
sink_carryover.py (the real cmd_manifest_restore and _restore_below_part2;
git, the restored child and the units faked), then the next manifest step.
The branch hs-uploader beside this checkout (../hs-uploader/.venv) answers
every hs-uploader call.  The scratch root reads @ROOT@ and every wall-clock
time is fixed, so a re-run leaves `git diff` empty.  Task 24 feeds these
files to sigmond-appliance's update-rig static test (global constraint 4).
"""
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))
sys.path.insert(0, str(HERE.parent))

from test_sink_carryover import REAL_PY  # noqa: E402
from test_manifest_restore import (RESTORE_CAPTURE,  # noqa: E402
                                   restore_rerun_capture)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else RESTORE_CAPTURE
    if not REAL_PY.exists():
        print(f"capture: no hs-uploader interpreter at {REAL_PY}",
              file=sys.stderr)
        return 1
    case = unittest.TestCase()
    try:
        files = restore_rerun_capture(case)
    finally:
        case.doCleanups()
    if out.exists():
        shutil.rmtree(out)
    for name, text in files.items():
        p = out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    print(f"capture: wrote {len(files)} files under {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

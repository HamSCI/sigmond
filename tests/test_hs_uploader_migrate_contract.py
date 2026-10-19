"""Contract: sigmond's manifest step reads `hs-uploader migrate --check`
right (tasks/spec-key-split-v373.md §5.2 step 3; Task 10 review #1c).

Runs the sibling ../hs-uploader checkout's real `migrate --check`, through
sigmond's own `_run_migrate(check=True)`, against stores that hs-uploader
itself builds.  A stand-in `runuser` drops `-u hsupload --`; nothing else is
faked.  Skips when no sibling checkout with a .venv sits beside this one.
"""

import contextlib
import io
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from sigmond.commands import uploader as up_cmd

_REPO = Path(__file__).resolve().parent.parent
_HS = _REPO.parent / "hs-uploader"
_HS_PY = _HS / ".venv" / "bin" / "python"

KEEP = "psk-recorder-pskreporter"

# Builds a store with hs-uploader's own code: argv[1] the kind, argv[2] the
# path.  "current" holds the shared pskreporter record and both producer
# keys at version 2 (the store stamps its version on creation), as a
# migrate leaves it.  "born-shared" holds the shared record alone at version
# 2: a store the daemon created and a shared pipeline used, whose missing
# producer keys every migrate fills (hs-uploader final review I1).  "v1"
# holds the shared record at version 1, and "retry" a current store with a
# retry queued under psk-pskreporter.
_BUILD = textwrap.dedent("""
    import sqlite3, sys
    from hs_uploader.watermark.sqlite import SqliteWatermarkStore
    kind, path = sys.argv[1], sys.argv[2]
    key = ("sqlite:psk.spots", "pskreporter-tcp:report.pskreporter.info:4739",
           "psk.spots")
    s = SqliteWatermarkStore(path)
    s._write_cursor(*key, cursor=b"5", last_ack="2026-10-18T12:00:00+00:00")
    if kind in ("current", "retry"):
        for producer in ("psk-recorder", "meteor-scatter"):
            s._write_cursor(key[0] + "@" + producer, *key[1:], cursor=b"5",
                            last_ack="2026-10-18T12:00:00+00:00")
    if kind == "retry":
        s.enqueue_deliverable(
            pipeline="psk-pskreporter", payload_blob=b"IPFIX-8",
            enqueued_at="2026-10-18T12:01:00+00:00",
            next_attempt_at="2026-10-18T12:06:00+00:00",
            cursor_after=b"8", commit_token=b"8",
            source_id=key[0], dest_id=key[1], table=key[2])
    s.close()
    if kind == "v1":
        c = sqlite3.connect(path)
        c.execute("PRAGMA user_version = 1")
        c.commit()
        c.close()
""")


@unittest.skipUnless(_HS_PY.exists(), "no sibling ../hs-uploader/.venv")
class MigrateCheckContractTests(unittest.TestCase):

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        venv = self.root / "venv"
        exe = venv / "bin" / "hs-uploader"
        exe.parent.mkdir(parents=True)
        exe.write_text(f'#!/bin/sh\nexec "{_HS_PY}" -m hs_uploader.cli "$@"\n')
        exe.chmod(0o755)
        runuser = self.root / "runuser"
        runuser.write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
        runuser.chmod(0o755)
        self.venv, self.runuser = venv, runuser
        caps = subprocess.run([str(_HS_PY), "-m", "hs_uploader.cli",
                               "capabilities"], capture_output=True, text=True)
        self.store_version = int(caps.stdout.strip())

    def _store(self, kind):
        db = self.root / kind / "watermarks.db"
        db.parent.mkdir()
        subprocess.run([str(_HS_PY), "-c", _BUILD, kind, str(db)], check=True)
        return db

    def _pending(self, db, store_version):
        with mock.patch.object(up_cmd, "_VENV", self.venv), \
             mock.patch.object(up_cmd.shutil, "which",
                               return_value=str(self.runuser)), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            rc = up_cmd._run_migrate(db=db, sink=None, keep_retry_for=KEEP,
                                     check=True, store_version=store_version)
        self.assertIn(rc, (0, 3))
        return rc == 3

    def test_check_pending_by_exit_code_and_by_wording(self):
        self.assertGreaterEqual(self.store_version, 2)
        stores = {kind: self._store(kind)
                  for kind in ("v1", "retry", "current", "born-shared")}
        # Exit code (the probe's answer) and the wording fallback agree.
        for sv in (self.store_version, None):
            with self.subTest(store_version=sv):
                self.assertTrue(self._pending(stores["v1"], sv))
                self.assertTrue(self._pending(stores["retry"], sv))
                self.assertFalse(self._pending(stores["current"], sv))
                self.assertTrue(self._pending(stores["born-shared"], sv))

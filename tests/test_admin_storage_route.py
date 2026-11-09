"""`smd admin storage trim` must reach the trim janitor, not the ledger.

v3.86 added the ledger's `smd storage`; the admin dispatch rewrites
`admin storage` to `storage`, so the ledger branch caught `trim` and
answered "unknown verb 'trim'".  sigmond-storage-trim-all.service runs
`smd admin storage trim --all --yes` every 15 minutes and failed on B4.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _smd(*argv):
    return subprocess.run([sys.executable, str(REPO / "bin" / "smd"), *argv],
                          capture_output=True, text=True, timeout=60)


def test_admin_storage_trim_reaches_the_janitor(tmp_path):
    r = _smd("admin", "storage", "trim", "--all", "--db", str(tmp_path / "none.db"))
    assert "unknown verb" not in r.stderr
    assert r.returncode == 0, r.stderr


def test_ledger_storage_status_still_answers(tmp_path):
    r = _smd("storage", "status", "--help")
    assert r.returncode == 0
    assert "unknown verb" not in r.stderr

"""docs/COMPONENTS.md's table must match what scripts/docs-components.py
generates from the catalog and the release list (ledger docs-organization,
step 2): the suite's top-level view cannot drift from what sigmond installs.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "docs-components.py"


def test_components_table_is_current():
    r = subprocess.run([sys.executable, str(SCRIPT), "--check"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_release_row_appears():
    sys.path.insert(0, str(REPO / "lib"))
    from sigmond import release_list
    page = (REPO / "docs" / "COMPONENTS.md").read_text()
    missing = [n for n in release_list.ROWS if f"| **{n}** |" not in page]
    assert not missing, f"release rows missing from COMPONENTS.md: {missing}"

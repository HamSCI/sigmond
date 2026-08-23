"""docs/contributor/orchestration.md's CLI table must match `smd --help`.

Parses the `{a,b,c}` verb group from `smd --help` and `smd admin --help`
and asserts the table lists every verb (and nothing that is not a verb).
Runs bin/smd from the checkout (PYTHONPATH=lib, no venv re-exec).
"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "docs" / "contributor" / "orchestration.md"


def _help(*args):
    env = dict(os.environ, PYTHONPATH=str(REPO / "lib"), SIGMOND_NO_VENV_REEXEC="1")
    out = subprocess.run([sys.executable, str(REPO / "bin" / "smd"), *args, "--help"],
                         capture_output=True, text=True, env=env, timeout=60).stdout
    m = re.search(r"\{([a-z0-9,\-]+)\}", out)
    assert m, f"no {{verb}} group in `smd {' '.join(args)} --help` output:\n{out[:400]}"
    return set(m.group(1).split(","))


def _table_cells():
    """First-column backticked cells of the CLI table (rows like `| `status` | …`)."""
    text = PAGE.read_text()
    cells = re.findall(r"^\|\s*`([a-z][a-z0-9\- ]*)`\s*\|", text, flags=re.M)
    return set(cells)


def test_page_exists():
    assert PAGE.exists()


def test_top_level_verbs_match():
    verbs = _help()
    cells = {c for c in _table_cells() if " " not in c}
    assert verbs == cells, f"missing from table: {verbs - cells}; not a verb: {cells - verbs}"


def test_admin_subverbs_match():
    subs = {f"admin {s}" for s in _help("admin")}
    cells = {c for c in _table_cells() if c.startswith("admin ")}
    assert subs == cells, f"missing from table: {subs - cells}; not a subverb: {cells - subs}"

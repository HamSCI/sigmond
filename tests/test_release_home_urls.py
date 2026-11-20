"""The image repo is HamSCI/sigmond-proxmox.  No live page or code names the
old slug, except the pre-rename fallback lists."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREES = ["README.md", "CONTRIBUTING.md", "CLAUDE.md", "docs", "lib", "bin", "scripts"]
SKIP_DIRS = {"superpowers", "archive", "__pycache__", "sigmond.egg-info"}
# The legacy slug stays in the fallback lists, and nowhere else.
LEGACY_OK = {"lib/sigmond/align.py", "scripts/proxmox/pm-align.py"}
OLD = re.compile(r"HamSCI/sigmond-appliance")
PROSE = re.compile(r"sigmond-appliance's copy")


def _files():
    for t in TREES:
        p = ROOT / t
        if p.is_file():
            yield p
        else:
            for f in p.rglob("*"):
                rel = f.relative_to(ROOT).parts
                if f.is_file() and not SKIP_DIRS & set(rel) and "egg-info" not in str(f):
                    yield f


def test_no_live_reference_to_old_repo():
    bad = []
    for f in _files():
        rel = str(f.relative_to(ROOT))
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if OLD.search(line) and rel not in LEGACY_OK:
                bad.append(f"{rel}:{n}")
            if PROSE.search(line):
                bad.append(f"{rel}:{n} prose")
    assert not bad, bad

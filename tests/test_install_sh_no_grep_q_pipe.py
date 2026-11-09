"""install.sh runs under `set -euo pipefail`.  A `cmd | grep -q` pipeline then
fails at random: grep -q exits at its first match, the writer takes SIGPIPE,
and pipefail turns the whole test false.  That silently skipped the
qemu-guest-agent drop-in in the v3.86 golden build (2026-11-09).  Capture the
output first and grep a here-string instead."""
import re
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"


def test_install_sh_has_no_grep_q_after_a_pipe():
    bad = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(INSTALL_SH.read_text().splitlines(), 1)
        if not line.lstrip().startswith("#")
        and re.search(r"\|\s*grep\s+-[A-Za-z]*q", line)
    ]
    assert not bad, "pipefail + grep -q races (SIGPIPE):\n" + "\n".join(bad)
